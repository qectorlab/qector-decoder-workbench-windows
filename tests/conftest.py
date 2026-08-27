"""
tests/conftest.py — Shared pytest fixtures for QECTOR tests.
"""

from __future__ import annotations

import sys
import pytest

# Ensure local imports resolve without installation
sys.path.insert(0, ".")

# Activate the externally managed decoder site (mirrors what main.py does at
# launch) before any test module imports backend, so the suite runs whether the
# decoder is installed in system site-packages or only in the per-user managed
# site provisioned for frozen builds.
#
# Bundled-wheel guarantee: the decoder under test MUST come from the app-owned
# managed site (decoder_site/<abi>) or the shipped wheels/ directory — never
# from an arbitrary system site-packages that could be a stale or tampered
# install.  Tests that need this provenance use the ``bundled_wheel`` fixture
# below which asserts the origin (see tests/test_bundled_wheel.py).
try:
    import decoder_provisioner as _decoder_provisioner
    _decoder_provisioner.activate_site()
    try:
        _decoder_provisioner.bootstrap()
    except Exception:
        pass
except Exception:
    pass


@pytest.fixture(scope="session")
def bundled_wheel():
    """Provenance guard: decoder must be the PYPI live 1.0.0 wheel, never below.

    Enforces EXACTLY 1.0.0 (not 0.6.x, not 0.9.x) from the bundled offline wheel
    or its managed extraction.  Verifies via module __version__, importlib.metadata,
    and wheel origin (managed_site or wheels/ dir).
    """
    import importlib
    import importlib.metadata as _meta
    import sys as _sys
    from pathlib import Path as _Path

    mod = _sys.modules.get("qector_decoder_v3") or importlib.import_module("qector_decoder_v3")
    f = _Path(getattr(mod, "__file__", "") or "").resolve()
    ver = getattr(mod, "__version__", None) or ""
    assert ver == "1.0.0", f"PYPI live bundled wheel must be exactly 1.0.0, got {ver!r} at {f} — below is rejected"
    try:
        meta_ver = _meta.version("qector-decoder-v3")
        assert meta_ver == "1.0.0", f"importlib.metadata must report 1.0.0 (PYPI live), got {meta_ver!r}"
    except importlib.metadata.PackageNotFoundError:
        pass
    # Strict numeric floor: must be >= 1.0.0, never below.
    def _vtup(v: str) -> tuple[int, ...]:
        out: list[int] = []
        for seg in str(v).split("."):
            d = "".join(c for c in seg if c.isdigit())
            if not d:
                break
            out.append(int(d))
        return tuple(out) or (0,)
    assert _vtup(ver) >= (1, 0, 0), f"version {ver!r} is below PYPI live 1.0.0 floor"
    assert _vtup(ver) == (1, 0, 0), f"version {ver!r} must be exactly 1.0.0, not below or above for this release"
    try:
        import decoder_provisioner as _dp

        managed = _dp.managed_root().resolve()
        wheels_dir = (_Path(__file__).resolve().parents[1] / "wheels").resolve()
        in_managed = managed in f.parents or f == managed
        in_wheels = wheels_dir in f.parents or f == wheels_dir
        # Also accept active_site() path directly.
        active = _dp.active_site()
        if active is not None:
            try:
                if _Path(active).resolve() in f.parents:
                    in_managed = True
            except Exception:
                pass
        # System site fallback is explicitly rejected here — the assertion
        # below forces test runs to use the bundled/managed wheel, not an
        # externally installed copy.
        assert in_managed or in_wheels, (
            f"decoder must be from bundled wheel/managed site, got {f} "
            f"(managed={managed}, wheels={wheels_dir})"
        )
        kind = "managed" if in_managed else "wheels"
    except AssertionError:
        raise
    except Exception:
        kind = "unknown"
    return {"path": str(f), "version": ver, "kind": kind}

@pytest.fixture(scope="session")
def app_imports():
    """Import all local GUI modules once per session to verify they load."""
    import app  # noqa: F401
    import backend as be  # noqa: F401
    import code_explorer_tab  # noqa: F401
    import decoder_lab_tab  # noqa: F401
    import benchmark_tab  # noqa: F401
    import batch_streaming_tab  # noqa: F401
    import hardware_tab  # noqa: F401
    import diagnostics_tab  # noqa: F401
    import autodebug  # noqa: F401
    import mcp_server  # noqa: F401
    import doc_generator  # noqa: F401
    import dialogs  # noqa: F401
    import documentation_tab  # noqa: F401
    import state  # noqa: F401
    import theme  # noqa: F401
    import console  # noqa: F401
    import threading_utils  # noqa: F401
    import utils  # noqa: F401
    import logger as qector_logger  # noqa: F401
    return True

@pytest.fixture(scope="session")
def backend(app_imports):
    import backend as be
    return be

@pytest.fixture(scope="session")
def code_families(backend):
    return backend.CODE_FAMILIES

@pytest.fixture(scope="session")
def decoder_kinds(backend):
    return backend.DECODER_KINDS

@pytest.fixture(scope="session")
def default_code(backend):
    return backend.build_code("rotated_surface", 5)

@pytest.fixture(scope="session")
def repetition_code(backend):
    return backend.build_code("repetition", 5)

@pytest.fixture(scope="session")
def ring_code(backend):
    return backend.build_code("ring", 6)

@pytest.fixture(scope="session")
def heavy_hex_code(backend):
    return backend.build_code("heavy_hex", 5)

@pytest.fixture(scope="session")
def toric_code(backend):
    return backend.build_code("toric", 4)
