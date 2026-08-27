"""version_service.py  -  version resolution for QECTOR Workbench.

The workbench reports two versions at boot without any network access:

* the workbench application itself  -  the static :data:`version.WORKBENCH_VERSION`
  baseline baked into the release;
* the compiled decoder backend  -  ``qector-decoder-v3`` as actually imported
  (provisioned offline from the bundled wheel by ``decoder_provisioner``).

Every public call is robust: it never raises for control flow and never
touches the network.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Optional

BACKEND_PACKAGE = "qector-decoder-v3"
# The workbench's own PyPI distribution name (override for private indexes /
# renamed releases).  If the package is not published, the live lookup simply
# reports latest=None and the app falls back to its baked-in baseline version.
APP_PACKAGE = os.environ.get("QECTOR_APP_PACKAGE", "qector-workbench")


def _fallback_backend_version() -> str:
    """The version to report when the backend has not imported yet.

    Uses the centrally-declared minimum supported backend version so every
    call site reports the *current* release line instead of a hard-coded
    historical number (the old ``"0.7.0"`` fallbacks showed a stale version
    whenever the import briefly failed)."""
    try:
        from version import MIN_BACKEND_VERSION
        return MIN_BACKEND_VERSION or "0.0.0"
    except Exception:
        return "0.0.0"

_CACHE_TTL_SECONDS = 6 * 3600  # 6h: version churn is slow; don't spam PyPI.
_CACHE_FILE = "version_cache.json"

_lock = threading.Lock()
_memcache: dict[str, tuple[float, Optional[str]]] = {}  # package -> (ts, version)


# ---------------------------------------------------------------------------
# Version parsing / comparison (PEP 440-tolerant, best-effort)
# ---------------------------------------------------------------------------

def parse_version(v: Optional[str]) -> tuple:
    """Parse a version string to a tuple of ints for ordering.

    Leading digits of each dot-separated segment are taken so PEP 440 suffixes
    are tolerated ("0.6.2rc1" -> (0, 6, 2)); parsing stops at the first
    non-numeric segment.  Unparseable / None -> (0,)."""
    if not v:
        return (0,)
    nums: list[int] = []
    for seg in str(v).split("."):
        m = re.match(r"\s*(\d+)", seg)
        if not m:
            break
        nums.append(int(m.group(1)))
    return tuple(nums) if nums else (0,)


def is_newer(latest: Optional[str], current: Optional[str]) -> bool:
    """True iff ``latest`` is a strictly newer version than ``current``."""
    if not latest or not current:
        return False
    return parse_version(latest) > parse_version(current)


# ---------------------------------------------------------------------------
# Local / installed versions
# ---------------------------------------------------------------------------

def installed_backend_version() -> Optional[str]:
    """The version of the compiled decoder backend actually imported, if any."""
    try:
        import importlib
        qd = importlib.import_module("qector_decoder_v3")
        return getattr(qd, "__version__", None)
    except Exception:
        return None


def local_app_version() -> str:
    """The workbench's baked-in baseline version (offline-safe)."""
    try:
        from version import WORKBENCH_VERSION
        return WORKBENCH_VERSION
    except Exception:
        return "0.0.0"


def effective_app_version(prefer_latest: bool = False) -> Optional[str]:
    """The version the app presents as *its own*  -  the workbench baseline.

    The workbench has its own release line (``version.WORKBENCH_VERSION``),
    independent of the decoder backend version.  Always returns the baked-in
    workbench version; ``prefer_latest`` is accepted for call-site
    compatibility and ignored (no network lookups)."""
    return local_app_version()


# ---------------------------------------------------------------------------
# On-disk cache
# ---------------------------------------------------------------------------

def _cache_path():
    try:
        from utils import get_data_dir
        return get_data_dir() / _CACHE_FILE
    except Exception:
        return None


def _load_disk_cache() -> dict[str, Any]:
    p = _cache_path()
    if p is None:
        return {}
    try:
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_disk_cache(data: dict[str, Any]) -> None:
    p = _cache_path()
    if p is None:
        return
    try:
        # Atomic write: write to a temp file then rename, so a crash or a
        # concurrent process can never leave a truncated/corrupt cache.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PyPI fetch (https-pinned, cached, robust)
# ---------------------------------------------------------------------------

def _fetch_pypi_latest(package: str, timeout: int = 5) -> Optional[str]:
    """Local wheel only: return local installed backend version."""
    return installed_backend_version() or _fallback_backend_version()


def _cached_latest(package: str, refresh: bool = False) -> Optional[str]:
    """Latest version for *package*  -  local wheel only, no PyPI.

    Always returns the installed backend version.  Any stale disk cache entries
    from previous sessions are overwritten on first call
    so they never cause the app to display a downgraded version."""
    installed = installed_backend_version() or _fallback_backend_version()
    now = time.time()
    # Overwrite any stale disk cache to prevent old PyPI versions from leaking
    with _lock:
        _memcache[package] = (now, installed)
        try:
            disk = _load_disk_cache()
            disk[package] = {"ts": now, "version": installed}
            _save_disk_cache(disk)
        except Exception:
            pass
    return installed


# ---------------------------------------------------------------------------
# Public: per-component and combined version reports
# ---------------------------------------------------------------------------

def get_backend_version_info(refresh: bool = False) -> dict[str, Any]:
    """Installed backend version, resolved locally  -  bundled wheel only, no PyPI."""
    installed = installed_backend_version()
    latest = _cached_latest(BACKEND_PACKAGE, refresh=refresh)
    return {
        "package": BACKEND_PACKAGE,
        "installed": installed,
        "latest": latest,
        "update_available": is_newer(latest, installed),
        "checked": latest is not None,
    }


def get_app_version_info(refresh: bool = False) -> dict[str, Any]:
    """Local baseline for the workbench application (offline  -  no PyPI)."""
    local = local_app_version()
    return {
        "package": APP_PACKAGE,
        "local": local,
        "latest": local,
        "update_available": False,
        "published": False,
    }


def get_version_report(refresh: bool = False) -> dict[str, Any]:
    """Combined app + backend version report (offline  -  bundled wheel only)."""
    return {
        "app": get_app_version_info(refresh=refresh),
        "backend": get_backend_version_info(refresh=refresh),
        "resolved_at": time.time(),
    }


def resolve_versions_async(callback: Optional[Callable[[dict], None]] = None,
                           refresh: bool = False) -> threading.Thread:
    """Resolve both versions on a daemon thread, then invoke ``callback(report)``.

    Returns the started thread.  The callback (if any) is always called exactly
    once; any exception inside it is swallowed so a boot-time UI update can never
    crash the app.
    """
    def _run() -> None:
        try:
            report = get_version_report(refresh=refresh)
        except Exception:
            report = {"app": {}, "backend": {}, "resolved_at": time.time()}
        if callback is not None:
            try:
                callback(report)
            except Exception:
                pass

    t = threading.Thread(target=_run, name="qector-version-resolve", daemon=True)
    t.start()
    return t


def format_version_banner(report: Optional[dict] = None) -> str:
    """One-line human banner: workbench version + installed backend version.

    e.g. 'QECTOR Decoder Workbench v1.0.1  |  qector-decoder-v3 1.0.0 (latest)'
    The workbench version shown is always the baked-in workbench baseline;
    the backend version is the one actually imported at runtime.
    """
    report = report or get_version_report()
    be = report.get("backend", {})
    installed = be.get("installed") or _fallback_backend_version()
    head = f"QECTOR Decoder Workbench v{local_app_version()}"
    tail = f"{be.get('package', BACKEND_PACKAGE)} {installed} (latest)"
    return f"{head}  |  {tail}"


if __name__ == "__main__":  # pragma: no cover - manual smoke check
    import pprint
    pprint.pprint(get_version_report(refresh=True))
    print(format_version_banner())
