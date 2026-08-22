"""
tests/test_compliance.py — Zero-egress attestation and EgressGuard enforcement.

The v1.0.1 release line is sold to enterprises as air-gapped and Entra ID
ready; these tests pin the two claims the auditors actually check:

1. The shipped Python surface contains no unguarded network or telemetry
   imports (the AST scan of compliance.scan_python_surface stays clean).
2. When air-gap mode is active, the EgressGuard genuinely blocks external
   DNS resolution and connections, allows loopback (local REST API keeps
   working), logs every attempt, and can be cleanly removed.

The portable Windows exe and the .deb auto-install the guard at launch
(main.launch calls compliance.install_egress_guard when airgap_mode()).
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

import compliance
import entra_auth

ROOT = Path(__file__).resolve().parents[1]

TRUTHY = {"1", "true", "yes", "on"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    """Never let a stray QECTOR_AIRGAP/QECTOR_OFFLINE leak between tests."""
    for key in ("QECTOR_AIRGAP", "QECTOR_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    # the compliance report includes the Entra posture read from disk; isolate
    # it so a real ~/.qector/entra.json cannot flake the attestation test
    monkeypatch.setattr(entra_auth, "_data_dir", lambda: tmp_path)
    yield
    compliance.remove_egress_guard()
    for key in ("QECTOR_AIRGAP", "QECTOR_OFFLINE"):
        monkeypatch.delenv(key, raising=False)


def test_surface_scan_is_clean():
    """No hard network imports, no telemetry imports, no env-key violations."""
    scan = compliance.scan_python_surface(ROOT)
    assert scan["files_scanned"] > 5
    assert scan["clean"] is True, scan["hard_network_imports"]
    assert scan["hard_network_imports"] == []
    assert scan["telemetry_imports"] == []
    assert scan["env_key_violations"] == []
    # decoder_provisioner's function-local urllib uses must stay guarded
    assert any("decoder_provisioner" in g for g in scan["guarded_network_imports"])


def test_airgap_mode_is_mandatory(monkeypatch):
    assert compliance.airgap_mode() is True
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    assert compliance.airgap_mode() is True
    monkeypatch.delenv("QECTOR_AIRGAP", raising=False)
    monkeypatch.setenv("QECTOR_OFFLINE", "yes")
    assert compliance.airgap_mode() is True


def test_guard_installs_without_an_opt_in_flag(monkeypatch):
    status = compliance.install_egress_guard()
    assert status["active"] is True
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    status = compliance.install_egress_guard()
    assert status["active"] is True
    assert status["mode"] == "airgap"


def test_guard_blocks_external_dns(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    with pytest.raises(compliance.EgressBlockedError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(compliance.EgressBlockedError):
        socket.gethostbyname("example.com")
    status = compliance.egress_guard_status()
    assert status["blocked_attempts"] >= 2


def test_guard_blocks_external_connect(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_OFFLINE", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    sock = socket.socket()
    with pytest.raises(compliance.EgressBlockedError):
        sock.connect(("192.0.2.1", 443))
    sock.close()


def test_guard_allows_loopback(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    # bind a loopback listener (what `qector serve` does) and connect to it
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=2)
    client.close()
    listener.close()
    # DNS for explicit loopback names still resolves
    assert socket.getaddrinfo("localhost", 80)


def test_guard_logs_attempts(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    log = tmp_path / "egress.log"
    compliance.install_egress_guard(log_path=log)
    with pytest.raises(compliance.EgressBlockedError):
        socket.create_connection(("203.0.113.7", 443), timeout=1)
    assert log.is_file()
    content = log.read_text(encoding="utf-8")
    assert "dns/getaddrinfo" in content


def test_guard_remove_restores_socket(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    assert compliance.egress_guard_status()["active"] is True
    compliance.remove_egress_guard()
    assert compliance.egress_guard_status()["active"] is False
    # original socket class restored: creating a socket must not raise GuardedSocket wiring errors
    sock = socket.socket()
    sock.close()


def test_urlopen_guarded(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    import urllib.request
    with pytest.raises(compliance.EgressBlockedError):
        urllib.request.urlopen("http://example.com", timeout=1)


def test_compliance_report_shape(monkeypatch, tmp_path):
    monkeypatch.setenv("QECTOR_AIRGAP", "1")
    compliance.install_egress_guard(log_path=tmp_path / "egress.log")
    report = compliance.compliance_report()
    assert report["compliant"] is True
    assert report["attestation"]["egress_guard"]["active"] is True
    assert report["attestation"]["runtime"] in ("source", "frozen")
    assert report["version"]["workbench"] == "1.0.2"
    assert report["version"]["mcp_tools"] >= 83
    assert report["entra"]["status"] == "disabled"
    assert report["license"]["blocking_network_call"] is False


def test_installer_and_build_manifest_include_new_modules():
    """The frozen bundle and .deb must ship compliance + entra_auth."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"compliance"' in pyproject
    assert '"entra_auth"' in pyproject
    build_prod = (ROOT / "build_production.py").read_text(encoding="utf-8")
    assert '"compliance.py"' in build_prod
    assert '"entra_auth.py"' in build_prod
    for spec in ("QectorWorkbench.spec", "QectorWorkbench-onefile.spec"):
        text = (ROOT / spec).read_text(encoding="utf-8")
        assert "'compliance'" in text
        assert "'entra_auth'" in text
