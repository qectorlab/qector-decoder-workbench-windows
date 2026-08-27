"""boot_test_runner.py — Verbose automatic tests + fresh result docs on every boot.

Runs the full pytest suite with ``-v`` (verbose) in a background daemon thread
on every GUI boot, streams verbose output to the live Console + log file, and
writes machine-readable results + freshly generated docs for lab review.

Artifacts (per-user data dir via ``utils.get_data_dir()``):
  logs/boot_tests_verbose.log   — full verbose pytest stdout/stderr (text, -v)
  logs/boot_test_results.json   — machine-readable summary + per-test mapping
  exports/boot_diagnostics/     — freshly generated HTML + Markdown report

Opt-out:
  Set environment ``QECTOR_SKIP_BOOT_TESTS=1`` or launch with ``--no-boot-tests``
  to skip the automatic run (useful for headless CI that drives its own runner).

This module has zero import side effects; ``schedule_boot_tests(app)`` must be
called explicitly from the UI thread (``app.QectorApp._start_boot_tests``).
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Optional


def _is_eula_accepted() -> bool:
    try:
        from utils import get_data_dir, load_json
        p = get_data_dir() / "preferences.json"
        return bool(load_json(p, {}).get("eula_accepted"))
    except Exception:
        return False

def _should_skip() -> tuple[bool, str]:
    if not _is_eula_accepted():
        return True, "EULA not yet accepted — tests deferred until customer accepts"
    if os.environ.get("QECTOR_SKIP_BOOT_TESTS", "").strip() in ("1", "true", "yes", "on"):
        return True, "QECTOR_SKIP_BOOT_TESTS=1"
    for arg in sys.argv[1:]:
        if arg == "--no-boot-tests":
            return True, "--no-boot-tests flag"
    return False, ""


def _resolve_paths() -> dict[str, Path]:
    try:
        from utils import get_data_dir

        data = get_data_dir()
    except Exception:
        data = Path.cwd()
    logs = data / "logs"
    exports = data / "exports" / "boot_diagnostics"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        exports.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    verbose_log = logs / "boot_tests_verbose.log"
    json_path = logs / "boot_test_results.json"
    return {"data": data, "logs": logs, "exports": exports, "verbose_log": verbose_log, "json_path": json_path}


def _has_pytest_available() -> bool:
    try:
        import pytest  # noqa: F401

        return True
    except Exception:
        return False


def _run_internal_verbose_fallback(on_log: Optional[Callable[[str], None]] = None) -> tuple[int, str]:
    """Fully offline bundled fallback when pytest is not available (air-gapped frozen).

    Runs a curated verbose smoke suite (no network, no pytest) and formats
    output to mimic ``pytest -v`` so docs/logs stay uniform.  Uses only
    bundled deps (backend, autodebug, utils).
    """
    lines: list[str] = []
    passed = failed = 0

    def check(name: str, fn: Callable[[], None]) -> None:
        nonlocal passed, failed
        try:
            fn()
            lines.append(f"tests/internal::{name} PASSED")
            passed += 1
            if on_log:
                try:
                    on_log(f"[boot-tests][fallback] {name} PASSED")
                except Exception:
                    pass
        except Exception as exc:
            lines.append(f"tests/internal::{name} FAILED — {type(exc).__name__}: {exc}")
            failed += 1
            if on_log:
                try:
                    on_log(f"[boot-tests][fallback] {name} FAILED: {exc}")
                except Exception:
                    pass

    def _check_backend_import():
        import backend as be

        assert be is not None

    def _check_decoder_probe():
        import backend as be

        code = be.build_code("repetition", 3)
        res = be.run_single_decode(code, 0.05, decoder_kind="union_find", seed=42)
        r = res.get("result")
        assert r is not None and getattr(r, "syndrome_valid", False) is True

    def _check_wheel_sha256():
        from utils import sha256_of

        root = Path(__file__).resolve().parent
        wheels = list((root / "wheels").glob("qector_decoder_v3-1.0.0*.whl"))
        assert wheels
        for w in wheels:
            h = sha256_of(w)
            assert len(h) == 64

    def _check_diagnostics():
        import autodebug

        rep = autodebug.run_self_diagnostics()
        assert rep.overall_status in ("pass", "degraded")

    check("test_backend_import", _check_backend_import)
    check("test_decoder_probe", _check_decoder_probe)
    check("test_wheel_sha256", _check_wheel_sha256)
    check("test_diagnostics", _check_diagnostics)

    rc = 0 if failed == 0 else 1
    header = "internal verbose fallback (air-gapped, pytest not bundled) — fully offline"
    summary_line = f"{passed} passed, {failed} failed in 0.00s"
    combined = header + "\n" + "\n".join(lines) + "\n" + summary_line + "\n"
    return rc, combined


def _run_pytest_verbose(
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Run ``pytest -v`` with full fallback chain (air-gapped, bundled).

    F1: ``python -m pytest -v`` when pytest is available (preferred, verbose).
    F2: Internal bundled fallback (no pytest, no network) — always succeeds.
    Never raises.
    """
    started = time.monotonic()
    started_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    def log(msg: str) -> None:
        if on_log is not None:
            try:
                on_log(msg)
            except Exception:
                pass

    root = Path(__file__).resolve().parent
    # F1 — try real pytest when available
    use_pytest = _has_pytest_available()
    if use_pytest:
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "-v",
            "--tb=short",
            "-p",
            "no:warnings",
            "--override-ini=addopts=",
            "--override-ini=testpaths=tests",
        ]
        log(f"[boot-tests] F1 $ {' '.join(cmd)}  (cwd={root})")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            rc = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            combined = stdout + ("\n" + stderr if stderr else "")
            # If pytest was not actually runnable (e.g. missing plugin), fall through.
            if rc == 127 or "No module named pytest" in combined:
                raise RuntimeError("pytest not runnable, falling back")
        except subprocess.TimeoutExpired as exc:
            rc, combined = 124, f"pytest timed out after 600s: {exc}\n{(exc.stdout or '')}\n{(exc.stderr or '')}"
            cmd = cmd  # keep for summary
        except Exception as exc:
            # F2 fallback
            log(f"[boot-tests] F1 pytest failed: {exc} — using F2 internal bundled fallback")
            rc, combined = _run_internal_verbose_fallback(on_log=on_log)
            cmd = [sys.executable, "-m", "internal_fallback", "-v"]
    else:
        log("[boot-tests] pytest not available (air-gapped frozen) — using F2 internal bundled fallback")
        rc, combined = _run_internal_verbose_fallback(on_log=on_log)
        cmd = [sys.executable, "-m", "internal_fallback", "-v"]

    elapsed = round(time.monotonic() - started, 2)

    # Minimal parse of verbose lines for per-test mapping.
    passed = failed = skipped = errors = 0
    per_test: list[dict[str, str]] = []
    for line in combined.splitlines():
        # Verbose format: "tests/test_foo.py::test_bar PASSED [ 12%]"
        if "::" in line and any(k in line for k in (" PASSED", " FAILED", " SKIPPED", " ERROR", " XPASS", " XFAIL")):
            parts = line.strip().split()
            # e.g. ["tests/test_foo.py::test_bar", "PASSED", "[", "12%]"]
            name = parts[0]
            status = parts[1] if len(parts) > 1 else "UNKNOWN"
            per_test.append({"nodeid": name, "outcome": status, "line": line.strip()})
            if status == "PASSED":
                passed += 1
            elif status == "FAILED":
                failed += 1
            elif status == "SKIPPED":
                skipped += 1
            elif status == "ERROR":
                errors += 1
        elif " passed" in line or " failed" in line:
            # summary line fallback when verbose parse misses (e.g. -q override ignored)
            pass

    # Summary fallback: count from pytest's final summary if per-line parse yielded zero.
    if not per_test and combined:
        import re

        m = re.search(r"(\d+) passed", combined)
        if m:
            passed = int(m.group(1))
        m = re.search(r"(\d+) failed", combined)
        if m:
            failed = int(m.group(1))
        m = re.search(r"(\d+) skipped", combined)
        if m:
            skipped = int(m.group(1))
        m = re.search(r"(\d+) error", combined)
        if m:
            errors = int(m.group(1))

    total = passed + failed + skipped + errors
    # If still zero, treat the whole run as one synthetic case.
    if total == 0 and rc != 0:
        total, failed = 1, 1

    outcome = "passed" if rc == 0 else ("timeout" if rc == 124 else "failed")

    summary: dict[str, Any] = {
        "schema": "qector.boot_test_results.v1",
        "outcome": outcome,
        "returncode": rc,
        "started_at": started_iso,
        "elapsed_s": elapsed,
        "counts": {"total": total, "passed": passed, "failed": failed, "skipped": skipped, "errors": errors},
        "per_test": per_test,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cwd": str(root),
        "command": cmd,
        "verbose_log": None,
        "json_path": None,
    }
    summary["_raw_verbose"] = combined
    return summary


def _write_artifacts(summary: dict[str, Any]) -> dict[str, Path]:
    """Persist verbose log + JSON summary to the per-user data dir."""
    paths = _resolve_paths()
    verbose_log: Path = paths["verbose_log"]
    json_path: Path = paths["json_path"]
    combined: str = summary.pop("_raw_verbose", "")

    header = (
        f"# QECTOR boot tests — verbose\n"
        f"# started: {summary.get('started_at')}  outcome={summary.get('outcome')}  rc={summary.get('returncode')}  elapsed={summary.get('elapsed_s')}s\n"
        f"# python: {summary.get('python')}  exe: {summary.get('executable')}\n"
        f"# command: {' '.join(summary.get('command') or [])}\n"
        f"# {'='*60}\n"
    )
    try:
        verbose_log.write_text(header + combined, encoding="utf-8", errors="replace")
    except Exception:
        try:
            verbose_log.parent.mkdir(parents=True, exist_ok=True)
            verbose_log.write_text(header + combined, encoding="utf-8", errors="replace")
        except Exception:
            pass

    summary["verbose_log"] = str(verbose_log)
    summary["json_path"] = str(json_path)
    try:
        json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    except Exception:
        try:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception:
            pass
    return paths


def _regenerate_docs(
    summary: dict[str, Any],
    exports_dir: Path,
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Generate fresh result data docs in ``exports/boot_diagnostics/``.

    Best-effort.  Uses ``doc_generator`` when a code is available to produce a
    small provenance doc, and always writes a standalone Markdown+HTML summary
    from the boot test results so docs are fresh even on a failing run.
    Never raises.
    """
    def log(msg: str) -> None:
        if on_log is not None:
            try:
                on_log(msg)
            except Exception:
                pass

    exports_dir.mkdir(parents=True, exist_ok=True)
    started = summary.get("started_at")
    outcome = summary.get("outcome")
    counts = summary.get("counts", {})
    per_test = summary.get("per_test", [])

    # 1) Always-fresh Markdown summary from this run's result data.
    md_path = exports_dir / "BOOT_TEST_REPORT.md"
    html_path = exports_dir / "BOOT_TEST_REPORT.html"
    try:
        safe_counts = json.dumps(counts) if isinstance(counts, dict) else str(counts)
        lines = [
            "# QECTOR Boot Test Report",
            "",
            f"- **Started:** {started}",
            f"- **Outcome:** {outcome}  (rc={summary.get('returncode')})  elapsed {summary.get('elapsed_s')}s",
            f"- **Python:** {summary.get('python')} — `{summary.get('executable')}`",
            f"- **Counts:** {safe_counts}",
            f"- **Verbose log:** `{summary.get('verbose_log')}`",
            "",
            "## Per-test results (verbose, -v)",
            "",
            "| # | Test | Outcome |",
            "|---|------|---------|",
        ]
        for idx, t in enumerate(per_test, 1):
            node = t.get("nodeid", "?").replace("|", "\\|")
            out = t.get("outcome", "?")
            lines.append(f"| {idx} | `{node}` | {out} |")
        if not per_test:
            lines.append("| — | *(no per-test lines captured — see verbose log)* | — |")
        lines += ["", f"_Generated at {datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')}Z_", ""]
        md_path.write_text("\n".join(lines), encoding="utf-8")
        log(f"[boot-tests] wrote {md_path}")
    except Exception as exc:
        log(f"[boot-tests] failed to write {md_path}: {exc}")

    # 2) HTML companion (escaped, self-contained).
    try:
        import html as _html

        rows = ""
        for t in per_test:
            rows += f"<tr><td><code>{_html.escape(t.get('nodeid',''))}</code></td><td>{_html.escape(t.get('outcome',''))}</td></tr>\n"
        if not rows:
            rows = '<tr><td colspan="2"><em>no per-test lines — see verbose log</em></td></tr>'
        html_doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>QECTOR Boot Test Report</title>
<style>body{{font-family: ui-monospace, monospace; max-width: 980px; margin: 32px auto; padding: 0 16px; color: #111}}
table{{border-collapse: collapse; width: 100%}} th,td{{border: 1px solid #ccc; padding: 6px 8px; text-align: left}} th{{background:#f4f4f4}}
pre{{background:#f6f8fa; padding: 12px; overflow:auto}}</style></head><body>
<h1>QECTOR Boot Test Report</h1>
<p>Started { _html.escape(str(started)) } — outcome <b>{ _html.escape(str(outcome)) }</b> rc={summary.get('returncode')} elapsed {summary.get('elapsed_s')}s</p>
<p>Python { _html.escape(str(summary.get('python')))} — <code>{ _html.escape(str(summary.get('executable')))}</code></p>
<p>Counts: <code>{ _html.escape(json.dumps(counts))}</code></p>
<p>Verbose log: <code>{ _html.escape(str(summary.get('verbose_log')))}</code></p>
<table><thead><tr><th>Test</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
        html_path.write_text(html_doc, encoding="utf-8")
        log(f"[boot-tests] wrote {html_path}")
    except Exception as exc:
        log(f"[boot-tests] failed to write {html_path}: {exc}")

    # 3) Also refresh the full official docs set in the background-like call
    # so result-data docs are never stale.  Best-effort, no hard failure.
    try:
        from docs_exporter import export_public_docs

        log("[boot-tests] regenerating official docs (fresh)…")
        export_public_docs(outdir=exports_dir / "official_docs", on_log=on_log)
        log("[boot-tests] official docs regenerated")
    except Exception as exc:
        log(f"[boot-tests] official docs regen skipped: {type(exc).__name__}: {exc}")

    # 4) SHA sidecars
    try:
        from utils import write_sha256_manifest

        write_sha256_manifest(exports_dir, [md_path, html_path])
    except Exception:
        pass

    return {"md": md_path, "html": html_path, "dir": exports_dir}


def run_boot_tests_and_refresh_docs(
    on_log: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Synchronous entry point: run verbose tests + persist + regen docs. Never raises.

    Returns the JSON summary dict (also written to ``logs/boot_test_results.json``).
    """
    skip, why = _should_skip()
    if skip:
        msg = f"[boot-tests] skipped ({why})"
        if on_log is not None:
            try:
                on_log(msg)
            except Exception:
                pass
        return {"schema": "qector.boot_test_results.v1", "outcome": "skipped", "reason": why, "returncode": 0}

    summary = _run_pytest_verbose(on_log=on_log)
    paths = _write_artifacts(summary)
    _regenerate_docs(summary, paths["exports"], on_log=on_log)

    outcome = summary.get("outcome")
    counts = summary.get("counts", {})
    if on_log is not None:
        try:
            on_log(
                f"[boot-tests] done: {outcome} — "
                f"{counts.get('passed', 0)} passed / {counts.get('failed', 0)} failed / "
                f"{counts.get('skipped', 0)} skipped in {summary.get('elapsed_s')}s "
                f"(verbose log: {paths['verbose_log']})"
            )
        except Exception:
            pass

    # Also mirror into the app logger.
    try:
        from logger import get_logger

        lg = get_logger()
        lg.info(
            f"Boot tests ({outcome}): {counts.get('passed',0)} passed / "
            f"{counts.get('failed',0)} failed in {summary.get('elapsed_s')}s — "
            f"{paths['verbose_log']}"
        )
    except Exception:
        pass

    return summary


def schedule_boot_tests(app: Any) -> None:
    """Schedule the verbose boot test run on the Tk main thread (fire-and-forget).

    Call once from ``QectorApp`` after the window is built.  Safe to call
    multiple times — the second call is a no-op.  Never raises.
    """
    if getattr(app, "_boot_tests_scheduled", False):
        return
    app._boot_tests_scheduled = True

    def _console_log(msg: str) -> None:
        # Use UiPump when available so worker threads never touch Tk directly.
        pump = getattr(app, "_ui", None)
        con = getattr(app, "console", None)
        if pump is not None:
            try:
                level = "WARN" if "failed" in msg.lower() or "error" in msg.lower() else "INFO"
                pump.post(con.log, msg, level)  # type: ignore[union-attr]
                return
            except Exception:
                pass
        if con is not None:
            try:
                con.log(msg, "INFO")
            except Exception:
                pass

    def worker() -> None:
        try:
            run_boot_tests_and_refresh_docs(on_log=_console_log)
        except Exception:
            try:
                _console_log(f"[boot-tests] unexpected error:\n{traceback.format_exc()}")
            except Exception:
                pass

    try:
        import threading_utils

        # Small delay so the splash/window is already visible before tests start.
        def delayed() -> None:
            threading_utils.run_in_background(worker)

        app._app.after(900, delayed)
    except Exception:
        # Fallback: run directly in background.
        try:
            import threading

            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass

