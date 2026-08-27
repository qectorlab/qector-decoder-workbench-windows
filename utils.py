"""utils.py  -  Shared utility functions for QECTOR Workbench."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# ── Input length limits ────────────────────────────────────────────────
MAX_PATH_LENGTH = 1024
MAX_NAME_LENGTH = 256
MAX_FIELD_LENGTH = 4096


def validate_string_length(value: str, max_len: int = MAX_FIELD_LENGTH,
                           label: str = "input") -> tuple[bool, str]:
    """Validate that a string does not exceed the maximum length.

    Returns ``(True, "")`` on success or ``(False, reason)`` on failure.
    Never raises.
    """
    if not isinstance(value, str):
        return False, f"{label} must be a string"
    if len(value) > max_len:
        return False, f"{label} exceeds maximum length ({len(value)} > {max_len})"
    if "\x00" in value:
        return False, f"{label} contains null bytes"
    return True, ""


def validate_path_length(path: str) -> tuple[bool, str]:
    """Validate a file path does not exceed MAX_PATH_LENGTH."""
    return validate_string_length(path, MAX_PATH_LENGTH, "path")


def validate_name_length(name: str) -> tuple[bool, str]:
    """Validate a name field does not exceed MAX_NAME_LENGTH."""
    return validate_string_length(name, MAX_NAME_LENGTH, "name")


def get_data_dir() -> Path:
    """Return the per-user writable data directory for the workbench.

    Resolution order: the QECTOR_DATA_DIR environment variable when set, else
    %LOCALAPPDATA%/QectorWorkbench on Windows, ~/Library/Application Support/
    QectorWorkbench on macOS, else the freedesktop location on Linux
    ($XDG_DATA_HOME/QectorWorkbench, falling back to
    ~/.local/share/QectorWorkbench, then legacy ~/.qector_workbench).  The
    directory is created on demand; any candidate that cannot be created is
    skipped and the fallback chain ends at the current working directory.
    Never raises.
    """
    import sys

    candidates: list[Path] = []
    try:
        override = os.environ.get("QECTOR_DATA_DIR", "").strip()
        if override:
            candidates.append(Path(override))
    except Exception:
        pass
    try:
        if os.name == "nt":
            local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
            if local_appdata:
                candidates.append(Path(local_appdata) / "QectorWorkbench")
            else:
                candidates.append(Path.home() / "AppData" / "Local" / "QectorWorkbench")
        elif sys.platform == "darwin":
            # macOS convention for per-user application data.
            candidates.append(Path.home() / "Library" / "Application Support" / "QectorWorkbench")
            candidates.append(Path.home() / ".qector_workbench")
        else:
            # Freedesktop XDG Base Directory spec, with a legacy fallback so
            # existing installs keep their data.
            xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
            if xdg_data_home:
                candidates.append(Path(xdg_data_home) / "QectorWorkbench")
            else:
                candidates.append(Path.home() / ".local" / "share" / "QectorWorkbench")
            candidates.append(Path.home() / ".qector_workbench")
    except Exception:
        pass
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_dir():
                return candidate
        except Exception:
            continue
    try:
        return Path.cwd()
    except Exception:
        return Path(".")


def get_export_dir() -> Path:
    """Return the per-user export directory (get_data_dir()/"exports").

    Created on demand; if creation fails the (existing) data directory itself
    is returned.  Never raises.
    """
    data_dir = get_data_dir()
    export_dir = data_dir / "exports"
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        return export_dir
    except Exception:
        return data_dir


def sanitize_export_path(path: Any, base_dir: Any = None) -> tuple[bool, Path]:
    """Validate an export path against directory-traversal attacks.

    Rejects empty names, ``..`` components, NUL bytes, and sensitive system
    locations. If *base_dir* is provided, forces the resolved path to be inside
    that directory. If *base_dir* is None, permits user-chosen absolute paths
    outside system roots, or resolves relative paths inside the per-user export directory.
    Returns ``(ok, resolved)``, never raises.
    """
    try:
        p = Path(path) if path is not None else Path("")
    except Exception:
        return False, Path("")
    if not p.name:
        return False, p
    raw = str(p)
    if "\x00" in raw:
        return False, p  # embedded NUL bytes are never valid in a filesystem path
    # Cross-platform traversal guard: on POSIX, backslash is an ordinary
    # filename character, so pathlib alone cannot catch "..\\..\\..." or a
    # Windows drive path ("C:\\...").  Normalise to POSIX separators and
    # validate components so Windows-style traversal is rejected on every OS.
    norm = raw.replace("\\", "/")
    norm_parts = norm.split("/")
    if ".." in norm_parts:
        return False, p
    if os.name != "nt" and len(norm_parts) and len(norm_parts[0]) >= 2 and norm_parts[0][1] == ":":
        return False, p  # drive-letter absolute path (C:/...) on a POSIX host
    p = Path(norm)
    try:
        if ".." in p.parts:
            return False, p
    except Exception:
        return False, p
    try:
        if base_dir is not None:
            allowed_root = Path(base_dir).resolve()
            if p.is_absolute():
                resolved = p.resolve()
            else:
                resolved = (allowed_root / p).resolve()
            try:
                resolved.relative_to(allowed_root)
            except ValueError:
                return False, p
        else:
            if p.is_absolute():
                resolved = p.resolve()
                # Protect OS system locations against overwrite
                raw_res = str(resolved).lower()
                if os.name == "nt":
                    sys_root = os.environ.get("SystemRoot", "C:\\Windows").lower()
                    sys32 = os.path.join(sys_root, "system32").lower()
                    if raw_res.startswith(sys32) or raw_res == sys_root:
                        return False, p
                else:
                    if any(raw_res.startswith(prefix) for prefix in ("/etc", "/sys", "/proc", "/boot", "/dev")):
                        return False, p
            else:
                allowed_root = get_export_dir().resolve()
                resolved = (allowed_root / p).resolve()
                try:
                    resolved.relative_to(allowed_root)
                except ValueError:
                    return False, p

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return True, resolved
    except Exception:
        return False, p


def load_json(path: Any, default: Any = None) -> Any:
    """Load a JSON file; return *default* when missing or malformed. Never raises."""
    import json as _json

    if default is None:
        default = {}
    try:
        return _json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Any, data: Any) -> bool:
    """Atomically write ``data`` as pretty JSON to ``path``; True on success."""
    import json as _json

    try:
        ok, msg = safe_write_file(path, _json.dumps(data, indent=2, default=str))
        return ok
    except Exception:
        return False


def validate_int(val: str, min_val: int = 0, max_val: int = 100) -> tuple[bool, str]:
    """Validate that a string represents an integer in [min_val, max_val]."""
    try:
        n = int(val)
        if n < min_val or n > max_val:
            return False, f"value {n} out of range [{min_val}, {max_val}]"
        return True, ""
    except (ValueError, TypeError):
        return False, f"invalid integer: {val!r}"


def format_number(num: float, precision: int = 2) -> str:
    """Format a number to a given precision."""
    return f"{num:.{precision}f}"


def safe_write_file(path: Any, content: str) -> tuple[bool, str]:
    """Atomically write content to a file, creating parent directories."""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(p)
        return True, ""
    except Exception as e:
        return False, str(e)


def sha256_of(path: Any) -> str:
    """Return the real SHA-256 hex digest of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_sidecar(path: Any) -> tuple[bool, str]:
    """Write ``<name>.sha256`` next to an exported file; return (ok, digest).

    The sidecar uses the coreutils layout (``<digest>  <name>``) so a lab can
    verify the artifact with ``sha256sum -c`` on Linux, or by eye against
    ``certutil -hashfile <name> SHA256`` on Windows.  Never raises: on failure
    the second element carries the reason, not a digest.
    """
    try:
        p = Path(path)
        digest = sha256_of(p)
        p.with_name(p.name + ".sha256").write_text(f"{digest}  {p.name}\n", encoding="utf-8")
        return True, digest
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def write_sha256_manifest(directory: Any, files: Iterable[Any],
                          manifest_name: str = "SHA256SUMS.txt") -> tuple[bool, str]:
    """Write a coreutils-format SHA-256 manifest for one export run.

    ``files`` is the artifact list of that run; only files that exist are
    listed.  Returns ``(True, manifest path)`` or ``(False, reason)``.
    Never raises.
    """
    try:
        directory = Path(directory)
        lines = [
            "# SHA-256 checksums: QECTOR Decoder Workbench export",
            f"# Generated: {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            f"# Verify:    sha256sum -c {manifest_name}",
        ]
        for f in files:
            p = Path(f)
            if p.is_file():
                lines.append(f"{sha256_of(p)}  {p.name}")
        manifest = directory / manifest_name
        manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True, str(manifest)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def get_machine_derived_key() -> bytes:
    """Derive a machine-bound Fernet key from stable host attributes.

    SECURITY NOTE: this is *obfuscation*, not strong encryption. The inputs
    (MAC address, hostname, platform, processor) are known to any process
    running on the same machine, so the key is derivable by a local attacker.
    It protects secrets only against casual offline inspection of files, NOT
    against on-machine attackers. Prefer OS-backed storage (DPAPI / keyring)
    wherever available.
    """
    import hashlib
    import base64
    import platform
    import uuid
    node_str = str(uuid.getnode())
    system_str = platform.system() + platform.node() + platform.processor()
    combined = (node_str + system_str).encode("utf-8")
    h = hashlib.sha256(combined).digest()
    return base64.urlsafe_b64encode(h)


def encrypt_license_key(key: str) -> str:
    from cryptography.fernet import Fernet
    f = Fernet(get_machine_derived_key())
    return f.encrypt(key.encode("utf-8")).decode("utf-8")


def enforce_monotonic_clock() -> bool:
    """Enforce monotonic sequence timestamp tracking to prevent clock rollback attacks.
    
    Returns True if clock is valid, False if a rollback > 24h is detected.
    """
    import time
    try:
        data_dir = get_data_dir()
        state_dir = data_dir / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        clock_file = state_dir / "clock_watermark.bin"
        
        current_time = time.time()
        
        if clock_file.is_file():
            try:
                raw = clock_file.read_text()
                if raw:
                    last_time = float(decrypt_license_key(raw))
                    if last_time - current_time > 86400:
                        import sys
                        print("SECURITY ALERT: Clock rollback attack detected: System clock is >24h behind last run watermark.", file=sys.stderr)
                        return False
            except Exception:
                pass
                
        try:
            clock_file.write_text(encrypt_license_key(str(current_time)))
        except Exception:
            pass
            
        return True
    except Exception:
        return True

def decrypt_license_key(encrypted_key: str) -> str:
    from cryptography.fernet import Fernet
    f = Fernet(get_machine_derived_key())
    return f.decrypt(encrypted_key.encode("utf-8")).decode("utf-8")
