"""Provision the QECTOR decoder outside packaged application bundles.

The workbench itself is packaged without ``qector-decoder-v3``.  At launch this
module activates a per-user managed site, discovers an ABI-compatible Python
interpreter when necessary, and installs a wheel there.  A versioned install
directory and atomic active-pointer update ensure a failed upgrade never
damages a previously working decoder.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Optional

PACKAGE = "qector-decoder-v3"
MODULE = "qector_decoder_v3"
_PIP_TIMEOUT = 300
_LOCK_TIMEOUT = 20.0
_THREAD_LOCK = threading.Lock()
_MIN_FREE_BYTES = 100 * 1024 * 1024  # 100 MB floor before any install (devv1 §2.2)
_MAX_RETRIES = 3
_BASE_RETRY_DELAY = 2.0
# The distributed product is permanently offline.  The legacy environment flag
# remains accepted by the diagnostics, but it cannot enable a network fallback.
_OFFLINE = True


def _provision_timeout() -> int:
    """pip/install timeout in seconds; overridable with QECTOR_PROVISION_TIMEOUT."""
    raw = os.environ.get("QECTOR_PROVISION_TIMEOUT")
    if raw:
        try:
            return max(int(raw), 10)
        except (TypeError, ValueError):
            pass
    return _PIP_TIMEOUT


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 checksum of a file."""
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception:
        return ""


def _fetch_pypi_metadata(version: Optional[str] = None) -> Optional[dict]:
    """Fetch wheel metadata from PyPI JSON API with retry logic.

    Zero-egress guarantee: in offline mode or in a frozen release this never
    contacts PyPI  -  the bundled wheel is the only accepted decoder source."""
    if _OFFLINE or is_frozen():
        return None
    import urllib.request
    import urllib.error
    
    url = f"https://pypi.org/pypi/{PACKAGE}/json"
    if version:
        url = f"https://pypi.org/pypi/{PACKAGE}/{version}/json"
    
    for attempt in range(_MAX_RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QECTOR-Provisioner/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            _diag(f"PyPI fetch attempt {attempt+1} failed: HTTP {e.code}")
        except Exception as e:
            _diag(f"PyPI fetch attempt {attempt+1} failed: {type(e).__name__}: {e}")
        
        if attempt < _MAX_RETRIES - 1:
            delay = _BASE_RETRY_DELAY * (2 ** attempt)
            _diag(f"Retrying PyPI fetch in {delay}s...")
            time.sleep(delay)
    
    return None


def _find_wheel_url_and_checksum(metadata: dict, version: str) -> tuple[Optional[str], Optional[str]]:
    """Find the appropriate wheel URL and SHA-256 checksum from PyPI metadata."""
    releases = metadata.get("releases", {}).get(version, [])
    if not releases:
        return None, None
    
    # Prefer manylinux for Linux, win_amd64 for Windows
    platform_pref = "manylinux" if platform.system() != "Windows" else "win_amd64"
    
    for file_info in releases:
        filename = file_info.get("filename", "")
        if filename.endswith(".whl") and platform_pref in filename:
            url = file_info.get("url")
            sha256 = file_info.get("digests", {}).get("sha256")
            return url, sha256
    
    # Fallback to any wheel
    for file_info in releases:
        filename = file_info.get("filename", "")
        if filename.endswith(".whl"):
            url = file_info.get("url")
            sha256 = file_info.get("digests", {}).get("sha256")
            return url, sha256
    
    return None, None


def _download_with_checksum(url: str, dest: Path, expected_sha256: Optional[str]) -> bool:
    """Download a file and verify its SHA-256 checksum.

    Zero-egress guarantee: in offline mode or in a frozen release this never
    contacts the network."""
    if _OFFLINE or is_frozen():
        _diag("download blocked: offline mode or frozen release")
        return False
    import urllib.request
    
    try:
        _diag(f"Downloading {url} to {dest}")
        urllib.request.urlretrieve(url, dest)
        
        if expected_sha256:
            actual = _sha256_file(dest)
            if actual.lower() != expected_sha256.lower():
                _diag(f"Checksum mismatch: expected {expected_sha256}, got {actual}")
                dest.unlink(missing_ok=True)
                return False
            _diag(f"Checksum verified: {actual}")
        return True
    except Exception as e:
        _diag(f"Download failed: {type(e).__name__}: {e}")
        dest.unlink(missing_ok=True)
        return False


def _disk_free_bytes(path: Path) -> Optional[int]:
    """Free bytes on the volume hosting ``path``, or None if unprobeable."""
    try:
        return shutil.disk_usage(str(path)).free
    except Exception:
        return None


def _check_disk_space(path: Path) -> Optional[str]:
    """Return an error message if ``path``'s volume has < 100 MB free, else None."""
    free = _disk_free_bytes(path)
    if free is None:
        return None  # cannot probe here; let the installer surface the real error
    if free < _MIN_FREE_BYTES:
        return (f"insufficient free disk space "
                f"({free / (1024 * 1024):.0f} MB < 100 MB) on {path} "
                f"for decoder installation")
    return None


def _diag(message: str) -> None:
    """Append a boot line to ``logs/boot.log``; never raise, never block boot.

    A frozen windowed build has no stderr, so a failed boot used to be a dialog
    with no way to find out *why*.  Every provisioning step records here instead.
    """
    try:
        try:
            from utils import get_data_dir
            base = Path(get_data_dir())
        except Exception:
            base = Path.home() / ".qector_workbench"
        log_dir = base / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_dir / "boot.log", "a", encoding="utf-8", errors="replace") as handle:
            handle.write(f"[{stamp}] pid={os.getpid()} frozen={is_frozen()} {message}\n")
    except Exception:
        pass


def _version_key(value: Optional[str]) -> tuple[int, ...]:
    """Conservative numeric comparison for normal release versions."""
    if not value:
        return (0,)
    parts: list[int] = []
    for segment in str(value).split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or (0,)


def _minimum_version() -> str:
    try:
        from version import MIN_BACKEND_VERSION
        return MIN_BACKEND_VERSION
    except Exception:
        return "0.6.2"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def abi_tag() -> str:
    """A short tag unique to this interpreter's binary ABI.

    Compiled decoder wheels (maturin ``cpXY-cpXY-<platform>``) only load in a
    matching interpreter, so the managed site is partitioned by this tag: a
    frozen Python 3.11 app and a Python 3.12 source run keep entirely separate
    installs and never clobber each other's extension module.
    """
    base = getattr(sys.implementation, "cache_tag", None) or (
        f"{platform.python_implementation().lower()}-"
        f"{sys.version_info.major}-{sys.version_info.minor}"
    )
    arch = _machine(platform.machine()) or "unknown"
    return f"{base}-{arch}"


def managed_root() -> Path:
    """Return the app-owned, user-writable, ABI-scoped decoder storage dir."""
    try:
        from utils import get_data_dir
        base = Path(get_data_dir())
    except Exception:
        base = Path.home() / ".qector_workbench"
    root = base / "decoder_site" / abi_tag()
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / "versions").mkdir(exist_ok=True)
        return root
    except OSError:
        # Locked-down profiles occasionally deny the normal OS data directory.
        # ``get_data_dir`` already falls back to cwd, but retry here so the
        # provisioner itself is safe when an existing directory turns unwritable.
        fallback = Path.cwd() / ".qector_decoder_site" / abi_tag()
        fallback.mkdir(parents=True, exist_ok=True)
        (fallback / "versions").mkdir(exist_ok=True)
        return fallback


def _pointer_path() -> Path:
    return managed_root() / "active.json"


def _version_dir(version: str) -> Path:
    safe = "".join(char for char in version if char.isalnum() or char in ".-+_")
    return managed_root() / "versions" / safe


def active_site() -> Optional[Path]:
    """Read the active managed-site pointer; malformed pointers are ignored."""
    try:
        payload = json.loads(_pointer_path().read_text(encoding="utf-8"))
        version = str(payload.get("version", ""))
        candidate = _version_dir(version)
        if version and candidate.is_dir():
            return candidate
    except Exception:
        pass
    return None


def activate_site() -> Optional[Path]:
    """Place the active managed site first on ``sys.path`` (idempotently)."""
    site = active_site()
    if site is None:
        return None
    value = str(site)
    while value in sys.path:
        sys.path.remove(value)
    sys.path.insert(0, value)
    importlib.invalidate_caches()
    return site


def _normalised_name(name: str) -> str:
    return name.lower().replace("_", "-").replace(".", "-")


def _version_in(path: Path) -> Optional[str]:
    try:
        for distribution in importlib.metadata.distributions(path=[str(path)]):
            if _normalised_name(distribution.metadata.get("Name", "")) == PACKAGE:
                return distribution.version
    except Exception:
        pass
    return None


def scan_version() -> Optional[str]:
    """Return the active managed version, otherwise a system-installed version."""
    active = active_site()
    if active is not None:
        version = _version_in(active)
        if version:
            return version
    try:
        return importlib.metadata.version(PACKAGE)
    except Exception:
        pass
    try:
        module = sys.modules.get(MODULE) or importlib.import_module(MODULE)
        return getattr(module, "__version__", None)
    except Exception:
        return None


_LAST_IMPORT_ERROR = ""


def _import_failure_detail() -> str:
    """Why the last :func:`import_ok` failed, for the boot diagnostics log.

    Reads the reason recorded by ``import_ok`` rather than retrying the import:
    a second attempt could actually succeed and load the decoder from somewhere
    we were about to override, changing boot behaviour just by logging it.
    """
    return f"{_LAST_IMPORT_ERROR or 'no error recorded'} | sys.path[:4]={sys.path[:4]}"


def import_ok() -> bool:
    """True iff the decoder *actually imports* in this interpreter  -  i.e. its
    compiled extension loads.  A metadata-only presence check is not enough: a
    wheel built for another Python ABI leaves valid dist-info but an unloadable
    ``.pyd``/``.so``.  This is the authoritative "is a usable decoder present?"
    test used by the boot gate."""
    global _LAST_IMPORT_ERROR
    try:
        importlib.invalidate_caches()
        module = sys.modules.get(MODULE)
        if module is None:
            module = importlib.import_module(MODULE)
        if getattr(module, "__version__", None) is not None:
            _LAST_IMPORT_ERROR = ""
            return True
        _LAST_IMPORT_ERROR = f"{MODULE} imported from {getattr(module, '__file__', '?')} but exposes no __version__"
        return False
    except BaseException as exc:
        # BaseException: a broken compiled extension can raise SystemError or
        # even abort-level errors that are not Exception subclasses.
        _LAST_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
        return False


def _scrubbed_env() -> dict:
    """Environment for child processes with workbench secrets removed.

    The decrypted license key and MCP token live in this process's env
    (main.py); child processes (pip, probes) must not inherit them  -  they are
    readable via /proc/<pid>/environ / WMI while the child runs.
    """
    env = dict(os.environ)
    for _k in ("QECTOR_LICENSE_KEY", "QECTOR_MCP_TOKEN"):
        env.pop(_k, None)
    return env


def _run(command: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout, env=_scrubbed_env())
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def _machine(value: str) -> str:
    value = value.lower().replace("_", "").replace("-", "")
    return {"amd64": "x8664", "x64": "x8664", "aarch64": "arm64"}.get(value, value)


def _identity() -> dict[str, object]:
    return {
        "implementation": platform.python_implementation(),
        "major": sys.version_info.major,
        "minor": sys.version_info.minor,
        "cache_tag": getattr(sys.implementation, "cache_tag", ""),
        "machine": _machine(platform.machine()),
        "bits": struct.calcsize("P") * 8,
        "soabi": sysconfig.get_config_var("SOABI") or "",
    }





_IDENTITY_PROBE = (
    "import json,platform,struct,sys,sysconfig;"
    "print(json.dumps({'implementation':platform.python_implementation(),"
    "'major':sys.version_info.major,'minor':sys.version_info.minor,"
    "'cache_tag':getattr(sys.implementation,'cache_tag',''),"
    "'machine':platform.machine(),'bits':struct.calcsize('P')*8,"
    "'soabi':sysconfig.get_config_var('SOABI') or ''}))"
)


def _candidate_identity(argv: list[str]) -> Optional[dict[str, object]]:
    rc, out, _ = _run(argv + ["-c", _IDENTITY_PROBE], 15)
    if rc:
        return None
    try:
        identity = json.loads(out)
        identity["machine"] = _machine(str(identity.get("machine", "")))
        return identity
    except Exception:
        return None


def _compatible(candidate: Optional[dict[str, object]]) -> bool:
    if not candidate:
        return False
    running = _identity()
    keys = ("implementation", "major", "minor", "cache_tag", "machine", "bits")
    return all(candidate.get(key) == running.get(key) for key in keys)


def _has_pip(argv: list[str]) -> bool:
    return _run(argv + ["-m", "pip", "--version"], 20)[0] == 0


def _candidate_pythons() -> list[list[str]]:
    candidates: list[list[str]] = []
    override = os.environ.get("QECTOR_PYTHON", "").strip()
    if override:
        candidates.append([override])
    wanted = f"{sys.version_info.major}.{sys.version_info.minor}"
    if os.name == "nt" and shutil.which("py"):
        candidates.append(["py", f"-{wanted}"])
    for name in (f"python{wanted}", "python3", "python"):
        executable = shutil.which(name)
        if executable:
            candidates.append([executable])
    seen: set[tuple[str, ...]] = set()
    return [item for item in candidates if not (tuple(item) in seen or seen.add(tuple(item)))]


def resolve_pip_argv() -> tuple[Optional[list[str]], str]:
    """Find a pip interpreter that can install extension modules for this app.

    Source runs always use their interpreter.  Frozen PyInstaller applications
    cannot execute ``--pip`` themselves, so they require a compatible system
    CPython; this is checked before an installation attempt.
    """
    if not is_frozen():
        return [sys.executable, "-m", "pip"], "source interpreter"
    for candidate in _candidate_pythons():
        if _compatible(_candidate_identity(candidate)) and _has_pip(candidate):
            return candidate + ["-m", "pip"], f"system CPython ({' '.join(candidate)})"
    return None, "no ABI-compatible system Python with pip found"


def _install_lock() -> tuple[Optional[int], Optional[Path]]:
    lock = managed_root() / "install.lock"
    # Auto-evict stale lock files (> 30s old) left by killed processes
    if lock.exists():
        try:
            if time.time() - lock.stat().st_mtime > 30:
                lock.unlink(missing_ok=True)
        except Exception:
            pass
    deadline = time.monotonic() + _LOCK_TIMEOUT
    while time.monotonic() < deadline:
        try:
            return os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY), lock
        except FileExistsError:
            try:
                if lock.exists() and time.time() - lock.stat().st_mtime > 30:
                    lock.unlink(missing_ok=True)
                    continue
            except Exception:
                pass
            time.sleep(0.2)
        except OSError:
            return None, None
    return None, None


def _release_lock(handle: Optional[int], path: Optional[Path]) -> None:
    try:
        if handle is not None:
            os.close(handle)
        if path is not None:
            path.unlink(missing_ok=True)
    except Exception:
        pass


def _latest_pypi() -> Optional[str]:
    """Fetch latest version from PyPI with retry logic (offline-safe)."""
    if _OFFLINE or is_frozen():
        return None
    metadata = _fetch_pypi_metadata()
    if not metadata:
        return None
    info = metadata.get("info", {})
    version = info.get("version")
    if version:
        _diag(f"Latest PyPI version: {version}")
    return version


def _install_from_pypi_with_checksum(version: str, timeout: int, on_log: Optional[Callable[[str], None]]) -> tuple[bool, str, Optional[str]]:
    """Install a specific version from PyPI with checksum verification.

    Zero-egress guarantee: in offline mode or in a frozen release this never
    contacts PyPI."""
    if _OFFLINE or is_frozen():
        return False, "offline mode is active; bundled wheel only", None
    metadata = _fetch_pypi_metadata(version)
    if not metadata:
        return False, f"PyPI metadata not found for version {version}", None
    
    url, expected_sha256 = _find_wheel_url_and_checksum(metadata, version)
    if not url:
        return False, f"No compatible wheel found on PyPI for version {version}", None
    
    # Download to staging area
    root = managed_root()
    disk_err = _check_disk_space(root)
    if disk_err:
        return False, disk_err, None
    
    staging = Path(tempfile.mkdtemp(prefix="decoder-download-", dir=str(root)))
    wheel_path = staging / f"{PACKAGE}-{version}.whl"
    
    if on_log:
        try:
            on_log(f"Downloading {PACKAGE} {version} from PyPI...")
        except Exception:
            pass
    
    if not _download_with_checksum(url, wheel_path, expected_sha256):
        shutil.rmtree(staging, ignore_errors=True)
        return False, "Failed to download or verify wheel checksum", None
    
    # Now install the downloaded wheel
    argv, strategy = resolve_pip_argv()
    if argv is None:
        shutil.rmtree(staging, ignore_errors=True)
        return False, "no ABI-compatible system Python with pip found", None
    
    command = argv + [
        "install", "--isolated", "--upgrade", "--no-deps",
        "--no-input", "--disable-pip-version-check", "--no-cache-dir",
        "--target", str(staging / "install"), str(wheel_path),
    ]
    
    if on_log:
        try:
            on_log(f"Installing downloaded wheel {wheel_path.name}...")
        except Exception:
            pass
    
    rc, out, err = _run(command, timeout)
    if rc:
        shutil.rmtree(staging, ignore_errors=True)
        lines = (err or out).strip().splitlines()
        return False, "; ".join(lines[-4:]) or f"pip exited {rc}", None
    
    installed_version = _version_in(staging / "install")
    if not installed_version or not (staging / "install" / MODULE).exists():
        shutil.rmtree(staging, ignore_errors=True)
        return False, "pip completed but no valid decoder wheel was installed", None
    
    destination = _version_dir(installed_version)
    try:
        if destination.exists():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            (staging / "install").replace(destination)
        
        ok_import, detail = _verify_import(destination)
        if not ok_import:
            if active_site() == destination:
                _diag(f"keeping installed decoder since it's active: {destination}")
            else:
                shutil.rmtree(destination, ignore_errors=True)
            return False, (f"{PACKAGE} {installed_version} installed but its wheel does not import "
                           f"in this runtime (broken release): {detail}"), installed_version
        
        pointer = _pointer_path()
        temporary = pointer.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_text(json.dumps({"version": installed_version}), encoding="utf-8")
        os.replace(temporary, pointer)
        activate_site()
        
        shutil.rmtree(staging, ignore_errors=True)
        return True, f"installed {PACKAGE} {installed_version} from PyPI with checksum verification", installed_version
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"could not activate installed decoder: {exc}", None


def _verify_import(path: Path) -> tuple[bool, str]:
    """Confirm the decoder at *path* actually imports in the target runtime.

    The in-process fast path is only trustworthy when the module is not already
    loaded, and only when the module that gets imported really resolves inside
    *path*.  ``import_module`` returns whatever is already in ``sys.modules``
    regardless of *path*, so on the upgrade path it would happily "verify" a
    broken candidate against the copy already in memory and then flip the active
    pointer onto it - the exact way a bad release bricks the next boot.
    """
    sp = str(path)
    if MODULE not in sys.modules:
        added = sp not in sys.path
        if added:
            sys.path.insert(0, sp)
        try:
            import importlib
            importlib.invalidate_caches()
            mod = importlib.import_module(MODULE)
            ver = (getattr(mod, "__version__", "") or "").strip()
            origin = os.path.abspath(getattr(mod, "__file__", "") or "")
            if ver and origin.startswith(os.path.abspath(sp) + os.sep):
                return True, ver
        except Exception:
            pass
        # Inconclusive: drop the half-imported module and the candidate path so a
        # stale entry cannot shadow later imports, then probe a clean runtime.
        for name in [n for n in sys.modules if n == MODULE or n.startswith(MODULE + ".")]:
            sys.modules.pop(name, None)
        if added:
            try:
                sys.path.remove(sp)
            except ValueError:
                pass

    if is_frozen():
        argv = [sys.executable, "--decoder-selftest", str(path)]
    else:
        probe = ("import sys; sys.path.insert(0, %r); "
                 "import qector_decoder_v3 as q; "
                 "sys.stdout.write('OK ' + (getattr(q, '__version__', '') or ''))" % str(path))
        argv = [sys.executable, "-c", probe]
    rc, out, err = _run(argv, 120)
    if rc == 0 and (out or "").strip().startswith("OK"):
        return True, ((out or "").strip()[3:].strip() or "ok")
    detail = (err or out or "").strip().splitlines()
    reason = "; ".join(detail[-4:]) if detail else f"import probe exited {rc}"
    _diag(f"_verify_import FAILED path={path} rc={rc} argv0={argv[0]} reason={reason}")
    return False, reason


def _install(specification: str, timeout: int, on_log: Optional[Callable[[str], None]]) -> tuple[bool, str, Optional[str]]:
    argv, strategy = resolve_pip_argv()
    if argv is None:
        return False, strategy, None
    root = managed_root()
    disk_err = _check_disk_space(root)
    if disk_err:
        return False, disk_err, None
    staging = Path(tempfile.mkdtemp(prefix="decoder-", dir=str(root)))
    command = argv + [
        "install", "--isolated", "--upgrade", "--only-binary=:all:", "--no-deps",
        "--no-input", "--disable-pip-version-check", "--no-cache-dir", "--index-url",
        "https://pypi.org/simple", "--target", str(staging), specification,
    ]
    if on_log:
        try:
            on_log(f"Installing {specification} via {strategy}.")
        except Exception:
            pass
    rc, out, err = _run(command, timeout)
    if rc:
        shutil.rmtree(staging, ignore_errors=True)
        lines = (err or out).strip().splitlines()
        return False, "; ".join(lines[-4:]) or f"pip exited {rc}", None
    version = _version_in(staging)
    if not version or not (staging / MODULE).exists():
        shutil.rmtree(staging, ignore_errors=True)
        return False, "pip completed but no valid decoder wheel was installed", None
    destination = _version_dir(version)
    try:
        if destination.exists():
            shutil.rmtree(staging, ignore_errors=True)
        else:
            staging.replace(destination)
        # Verify the freshly installed wheel actually imports in the TARGET
        # runtime BEFORE flipping the active pointer, so a broken upstream release
        # cannot wedge the app or replace a previously working version.  On
        # failure the pointer is left untouched (the previous good version stays
        # active) and the broken install is removed so a later, fixed release
        # reinstalls cleanly.  The version is still returned so the caller can
        # step the install down to the next-lower release.
        ok_import, detail = _verify_import(destination)
        if not ok_import:
            if active_site() == destination:
                _diag(f"keeping installed decoder since it's active: {destination}")
            else:
                shutil.rmtree(destination, ignore_errors=True)
            return False, (f"{PACKAGE} {version} installed but its wheel does not import "
                           f"in this runtime (broken release): {detail}"), version
        pointer = _pointer_path()
        temporary = pointer.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_text(json.dumps({"version": version}), encoding="utf-8")
        os.replace(temporary, pointer)
        activate_site()
        return True, f"installed {PACKAGE} {version} using {strategy}", version
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return False, f"could not activate installed decoder: {exc}", None


_MAX_INSTALL_ATTEMPTS = 4


def _blocklist_path() -> Path:
    return managed_root() / "blocked.json"


def _load_blocklist() -> set[str]:
    """Versions already proven to NOT import in this runtime.

    Persisted per ABI-scoped managed root so a known-bad release is never
    re-downloaded and re-probed on every launch; it is simply skipped and the
    provisioner drops to the next-lower version.
    """
    try:
        data = json.loads(_blocklist_path().read_text(encoding="utf-8"))
        return {str(v) for v in data}
    except Exception:
        return set()


def _add_blocklist(version: Optional[str]) -> None:
    if not version:
        return
    try:
        blocked = _load_blocklist()
        blocked.add(str(version))
        path = _blocklist_path()
        temporary = path.with_suffix(f".tmp-{uuid.uuid4().hex}")
        temporary.write_text(json.dumps(sorted(blocked)), encoding="utf-8")
        os.replace(temporary, path)
    except Exception:
        pass


def _install_best(latest: Optional[str], timeout: int,
                  on_log: Optional[Callable[[str], None]]) -> tuple[bool, str, Optional[str]]:
    """Install the newest release that actually imports in the target runtime.

    Starts at ``latest`` (or an unpinned newest) and, whenever a wheel installs
    but fails to import here, records it and steps down to ``PACKAGE<version`` so
    pip picks the next-lower release.  This makes a broken "latest" self-heal to
    the last good version instead of bricking the app  -  the exact failure mode
    where a decoder release adds an import-time dependency the frozen bundle does
    not carry.  Bounded to a handful of attempts so a pathological index can
    never spin.
    """
    blocked = _load_blocklist()
    spec_version = latest
    last_message = "no installable decoder release found"
    for _ in range(_MAX_INSTALL_ATTEMPTS):
        if spec_version and spec_version in blocked:
            # Known-bad exact version: let pip choose the next-lower release.
            spec = f"{PACKAGE}<{spec_version}"
        elif spec_version:
            spec = f"{PACKAGE}=={spec_version}"
        else:
            spec = PACKAGE
        ok, message, version = _install(spec, timeout, on_log)
        last_message = message
        if ok:
            return True, message, version
        if version is None:
            # pip could not install anything for this spec (no wheel / no lower
            # release exists)  -  stepping further down is futile.
            return False, message, None
        # A wheel installed but does not import in this runtime: remember it and
        # try the next-lower release on the following iteration.
        _add_blocklist(version)
        blocked.add(version)
        if on_log:
            try:
                on_log(f"{PACKAGE} {version} does not import in this runtime; trying an older release.")
            except Exception:
                pass
        spec_version = version
    return False, last_message, None


def ensure(prefer_latest: bool = True, timeout: Optional[int] = None,
           on_log: Optional[Callable[[str], None]] = None,
           target_version: Optional[str] = None) -> dict:
    """Ensure an importable decoder, preferring the bundled wheel (offline).

    The returned object is JSON serialisable and this function never raises.
    """
    if timeout is None:
        timeout = _provision_timeout()
    with _THREAD_LOCK:
        activate_site()
        installed = scan_version()
        minimum = _minimum_version()
        latest = target_version or (_latest_pypi() if prefer_latest and not _OFFLINE else None)
        result = {
            "module": MODULE, "installed_before": installed, "installed": installed,
            "minimum": minimum, "latest": latest, "managed_dir": str(managed_root()),
            "frozen": is_frozen(), "action": "none", "ok": False, "message": "",
        }
        needs_install = (installed is None or _version_key(installed) < _version_key(minimum)
                         or not import_ok())
        needs_upgrade = bool(latest and installed and _version_key(latest) > _version_key(installed))
        if not needs_install and not needs_upgrade:
            result.update(ok=True, message=f"{MODULE} {installed} is ready")
            return result
        lock_handle, lock_path = _install_lock()
        if lock_handle is None:
            result.update(ok=not needs_install, action="deferred", message="another QECTOR instance is installing the decoder")
            return result
        try:
            # Re-scan under the inter-process lock
            activate_site()
            installed = scan_version()
            needs_install = (installed is None or _version_key(installed) < _version_key(minimum)
                             or not import_ok())
            if not needs_install:
                result.update(installed=installed, ok=True, message=f"{MODULE} {installed} is ready")
                return result
        finally:
            _release_lock(lock_handle, lock_path)

    # Local wheel extraction ONLY (no live PyPI downloads)
    local_wheels = find_local_wheels()
    for whl in local_wheels:
        ok_whl, msg_whl, ver_whl = _extract_wheel_direct(whl)
        if ok_whl and import_ok():
            activate_site()
            sys.modules.pop(MODULE, None)
            importlib.invalidate_caches()
            result.update(
                ok=True,
                action="bundled_wheel",
                installed=ver_whl,
                message=f"activated local wheel {whl.name} (version {ver_whl})",
            )
            return result

    # A frozen release is deliberately offline: the bundled wheel is the only
    # accepted decoder source. A missing or incompatible wheel is a hard error,
    # not a reason to contact PyPI.
    if _OFFLINE or is_frozen():
        result.update(
            ok=False,
            action="failed",
            installed=installed,
            message=f"local bundled wheel required ({minimum}); offline mode is active",
        )
        return result

    # Live install from PyPI with checksum verification and retry for source
    # installs only.
    if latest:
        if on_log:
            try:
                on_log(f"Installing {PACKAGE} {latest} from PyPI with checksum verification...")
            except Exception:
                pass
        
        for attempt in range(_MAX_RETRIES):
            ok, msg, ver = _install_from_pypi_with_checksum(latest, timeout, on_log)
            if ok:
                result.update(
                    ok=True,
                    action="pypi_install",
                    installed=ver,
                    message=msg,
                )
                return result
            
            if attempt < _MAX_RETRIES - 1:
                delay = _BASE_RETRY_DELAY * (2 ** attempt)
                if on_log:
                    try:
                        on_log(f"Install attempt {attempt+1} failed: {msg}. Retrying in {delay}s...")
                    except Exception:
                        pass
                _diag(f"PyPI install attempt {attempt+1} failed: {msg}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                result.update(
                    ok=False,
                    action="failed",
                    installed=installed,
                    message=f"Failed after {_MAX_RETRIES} attempts: {msg}",
                )
                return result
    
    usable = installed is not None and not needs_install
    result.update(
        ok=usable,
        action="none" if usable else "failed",
        installed=installed,
        message=f"local wheel required ({minimum}); PyPI auto-install failed or disabled.",
    )
    return result


# Pinned to the verified working set (see requirements.txt). Pins prevent
# silent upgrades to untested/breaking versions during auto-install.
REQUIRED_DEPENDENCIES = ["numpy", "scipy", "matplotlib", "Pillow", "customtkinter", "psutil"]
_DEPENDENCY_PINS = {
    "numpy": "numpy==2.2.6",          # must stay <2.3 (decoder Rust bindings)
    "scipy": "scipy==1.18.0",
    "matplotlib": "matplotlib==3.11.1",
    "Pillow": "Pillow==12.3.0",
    "customtkinter": "customtkinter==6.0.0",
    "psutil": "psutil==7.2.2",
}


def ensure_dependencies(on_log: Optional[Callable[[str], None]] = None) -> dict:
    """Check and automatically install any missing core dependencies via pip."""
    missing = []
    for pkg in REQUIRED_DEPENDENCIES:
        try:
            mod_name = "PIL" if pkg.lower() == "pillow" else pkg
            importlib.import_module(mod_name)
        except Exception:
            missing.append(pkg)
    if not missing:
        return {"ok": True, "missing": [], "message": "all dependencies satisfied"}

    argv, strategy = resolve_pip_argv()
    if argv is None:
        return {"ok": False, "missing": missing, "message": f"missing dependencies {missing} but {strategy}"}

    if on_log:
        try:
            on_log(f"Installing missing dependencies: {', '.join(missing)} via {strategy}")
        except Exception:
            pass

    command = argv + ["install"] + [_DEPENDENCY_PINS.get(pkg, pkg) for pkg in missing]
    rc, out, err = _run(command, _provision_timeout())
    if rc == 0:
        return {"ok": True, "installed": missing, "message": f"installed missing dependencies {missing}"}
    return {"ok": False, "missing": missing, "message": f"pip failed to install {missing}: {err or out}"}


def _imported_version() -> Optional[str]:
    """__version__ of whatever ``qector_decoder_v3`` currently imports, or None."""
    try:
        module = sys.modules.get(MODULE) or importlib.import_module(MODULE)
        return getattr(module, "__version__", None)
    except Exception:
        return None


def find_local_wheels() -> list[Path]:
    """Find local or bundled .whl files for qector_decoder_v3."""
    search_dirs: list[Path] = []
    if is_frozen():
        if hasattr(sys, "_MEIPASS"):
            search_dirs.append(Path(getattr(sys, "_MEIPASS")))
            search_dirs.append(Path(getattr(sys, "_MEIPASS")) / "wheels")
        exe_dir = Path(sys.executable).parent
        search_dirs.append(exe_dir)
        search_dirs.append(exe_dir / "wheels")
    else:
        root = Path(__file__).parent
        search_dirs.append(root)
        search_dirs.append(root / "wheels")

    candidates: list[Path] = []
    seen: set[Path] = set()
    for d in search_dirs:
        try:
            if d.is_dir():
                for p in d.glob("qector_decoder_v3*.whl"):
                    resolved = p.resolve()
                    if resolved not in seen:
                        seen.add(resolved)
                        candidates.append(p)
        except Exception:
            pass
    return candidates


def _extract_wheel_direct(wheel_path: Path) -> tuple[bool, str, Optional[str]]:
    """Unpack a .whl file directly into the managed site without requiring pip."""
    try:
        with zipfile.ZipFile(wheel_path, "r") as zip_ref:
            version = None
            for name in zip_ref.namelist():
                if name.endswith(".dist-info/METADATA"):
                    for line in zip_ref.read(name).decode("utf-8", errors="replace").splitlines():
                        if line.startswith("Version:"):
                            version = line.split(":", 1)[1].strip()
                            break
            if not version:
                version = "1.0.0"

            destination = _version_dir(version)
            disk_err = _check_disk_space(destination.parent)
            if disk_err:
                return False, disk_err, version
            destination.mkdir(parents=True, exist_ok=True)
            # Zip-slip guard: reject any member that would write outside the
            # destination (absolute paths or '..' traversal) before extracting.
            dest_root = destination.resolve()
            for member in zip_ref.namelist():
                target = (dest_root / member).resolve()
                if target != dest_root and dest_root not in target.parents:
                    return False, f"unsafe path in wheel archive: {member!r}", version
            zip_ref.extractall(destination)

            ok_import, detail = _verify_import(destination)
            if not ok_import:
                _diag(f"direct wheel extract import verification failed: {detail}")
                return False, detail, version

            pointer = _pointer_path()
            temporary = pointer.with_suffix(f".tmp-{uuid.uuid4().hex}")
            temporary.write_text(json.dumps({"version": version}), encoding="utf-8")
            os.replace(temporary, pointer)
            activate_site()
            return True, f"activated bundled wheel {wheel_path.name} (version {version})", version
    except Exception as exc:
        _diag(f"direct wheel extract failed: {exc}")
        return False, str(exc), None


def purge_outdated_managed_sites(minimum_ver: Optional[str] = None) -> list[str]:
    """Delete any on-disk managed decoder site versions older than minimum_ver.

    Also removes active.json pointer if it references an outdated version.
    This guarantees that upgrading from an older pre-release (e.g. 0.6.9 -> 0.7.0)
    completely purges old cached sites before unzipping/activating the bundled wheel.
    """
    min_v = minimum_ver or _minimum_version()
    purged = []
    try:
        root = managed_root()
        versions_dir = root / "versions"
        if versions_dir.exists():
            for vdir in versions_dir.iterdir():
                if vdir.is_dir():
                    ver_name = vdir.name
                    if _version_key(ver_name) < _version_key(min_v):
                        _diag(f"purging outdated managed decoder version: {ver_name} at {vdir}")
                        try:
                            shutil.rmtree(vdir, ignore_errors=True)
                            purged.append(ver_name)
                        except Exception as e:
                            _diag(f"failed to delete outdated version dir {vdir}: {e}")
        pointer = _pointer_path()
        if pointer.exists():
            try:
                data = json.loads(pointer.read_text(encoding="utf-8"))
                ptr_ver = data.get("version")
                if ptr_ver and _version_key(ptr_ver) < _version_key(min_v):
                    _diag(f"removing outdated active.json pointer ({ptr_ver})")
                    pointer.unlink(missing_ok=True)
            except Exception:
                pointer.unlink(missing_ok=True)
    except Exception as exc:
        _diag(f"purge_outdated_managed_sites encountered error: {exc}")
    return purged


def bootstrap(on_log: Optional[Callable[[str], None]] = None) -> dict:
    """Blocking pre-import gate used by ``main.py`` before backend imports.

    Resolution order:
      1. Already importable (environment / previous managed install) with version >= 1.0.0
      2. Activate managed site → try import again if version >= 1.0.0
      3. Purge outdated managed site directories (< 1.0.0) from disk
      4. Extract local/bundled .whl wheel directly into managed site (1.0.0)
    """
    _diag(f"bootstrap start abi={abi_tag()} exe={sys.executable}")
    minimum = _minimum_version()

    # 1. Already importable in the current environment?
    if import_ok():
        version = _imported_version()
        if version and _version_key(version) >= _version_key(minimum):
            _diag(f"step1 ambient import OK version={version}")
            return {"ok": True, "action": "ready", "installed": version,
                    "message": f"{MODULE} {version} is ready"}
        _diag(f"step1 ambient import outdated: version={version} < {minimum}")

    # 2. Activate managed site and re-check
    try:
        site = activate_site()
        _diag(f"step2 activate_site -> {site}")
    except Exception as exc:
        _diag(f"step2 activate_site raised {type(exc).__name__}: {exc}")
    if import_ok():
        version = scan_version() or _imported_version()
        if version and _version_key(version) >= _version_key(minimum):
            _diag(f"step2 managed import OK version={version}")
            return {"ok": True, "action": "managed", "installed": version,
                    "message": f"{MODULE} {version} is ready (managed site)"}
        _diag(f"step2 managed import outdated: version={version} < {minimum}")
    else:
        _diag(f"step2 managed import failed: {_import_failure_detail()}")

    # 2.2 Purge any outdated managed versions (< 0.7.0) from system disk
    purged = purge_outdated_managed_sites(minimum)
    if purged:
        _diag(f"purged outdated managed decoder versions: {purged}")
        sys.modules.pop(MODULE, None)
        importlib.invalidate_caches()

    # 2.5. Try local / bundled wheel extraction
    local_wheels = find_local_wheels()
    for whl in local_wheels:
        _diag(f"step2.5 trying local wheel: {whl}")
        ok, msg, ver = _extract_wheel_direct(whl)
        if ok and import_ok():
            version = scan_version() or _imported_version()
            if version and _version_key(version) >= _version_key(minimum):
                _diag(f"step2.5 local wheel extract OK version={ver}")
                return {"ok": True, "action": "bundled_wheel", "installed": ver,
                        "message": f"{MODULE} {ver} is ready (from {whl.name})"}
        _diag(f"step2.5 local wheel extract failed: {msg}")

    if _OFFLINE or is_frozen():
        result = {
            "ok": False,
            "action": "failed",
            "installed": _imported_version(),
            "minimum": minimum,
            "message": f"bundled {PACKAGE} {minimum} wheel is missing or incompatible; offline mode is active",
        }
        _diag(f"offline bootstrap failed: {result['message']}")
        return result

    # 3. Live install from PyPI for explicitly non-frozen source installs.
    if on_log:
        try:
            on_log(f"Downloading {PACKAGE} from PyPI...")
        except Exception:
            pass
    result = ensure(prefer_latest=True, target_version=minimum, on_log=on_log)
    _diag(f"ensure -> ok={result.get('ok')} action={result.get('action')} "
          f"msg={result.get('message')}")
    return result


def ensure_async(prefer_latest: bool = True, callback: Optional[Callable[[dict], None]] = None,
                 on_log: Optional[Callable[[str], None]] = None,
                 target_version: Optional[str] = None) -> threading.Thread:
    """Run :func:`ensure` on a daemon thread; upgrade takes effect next launch."""
    def worker() -> None:
        result = ensure(prefer_latest, on_log=on_log, target_version=target_version)
        if callback:
            try:
                callback(result)
            except Exception:
                pass
    thread = threading.Thread(target=worker, name="qector-decoder-provision", daemon=True)
    thread.start()
    return thread


def self_check() -> dict:
    argv, strategy = resolve_pip_argv()
    return {
        "frozen": is_frozen(), "managed_dir": str(managed_root()),
        "abi_tag": abi_tag(),
        "active_site": str(active_site() or ""), "scanned_version": scan_version(),
        "import_ok": import_ok(),
        "minimum": _minimum_version(), "pip_strategy": strategy,
        "pip_available": argv is not None, "interpreter_identity": _identity(),
        "blocked_versions": sorted(_load_blocklist()),
    }


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(bootstrap(), indent=2))