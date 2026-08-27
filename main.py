"""main.py  -  QECTOR Decoder Workbench entry point for the PyInstaller build.

PyInstaller executes this module as ``__main__`` inside the frozen app, so the
``__main__`` guard below still launches the GUI in the packaged EXE while
keeping ``import main`` side-effect free for tests and tooling.
"""
import multiprocessing
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_CLI_COMMANDS = [
    "decode", "benchmark", "probe", "diagnostics", "hardware",
    "list-codes", "list-decoders", "docgen", "version",
    # New CLI subcommands from finaldev.md tasks 5.1-5.7
    "compare", "batch", "stream", "train", "export", "import", "matrix",
    "serve", "doctor", "completions",
    # v1.0.1: zero-egress attestation + optional Entra ID SSO
    "compliance", "entra",
]


def _selftest_write(text: str) -> None:
    """Report selftest output without ever raising.

    Writing is best effort by design.  A redirected or half-open handle can
    fail on write *or* on flush (Windows raises ``OSError: [Errno 22] Invalid
    argument`` for some redirections), and losing the text must never change
    the verdict: the exit code is what ``decoder_provisioner`` reads.
    """
    # Tried in order, and only as far as needed: each fallback is attempted
    # solely because the previous one failed.
    for stream in (sys.stdout, sys.stderr):
        if stream is None or getattr(stream, "name", "") in ("<none>", "<null>"):
            continue
        try:
            stream.write(text)
        except Exception:
            continue
        try:
            stream.flush()
        except Exception:
            pass  # written is written; an unflushable handle is not a failure
        return

    # Raw fd 1, written directly. Deliberately not wrapped in open(1, "w"):
    # closing such a wrapper closes the process's real stdout, and not closing
    # it leaks the handle.
    try:
        os.write(1, text.encode("utf-8", "replace"))
        return
    except Exception:
        pass

    # Windows console of last resort, when the app is windowed and fd 1 is dead.
    try:
        handle = open("CONOUT$", "w", encoding="utf-8", buffering=1)
    except Exception:
        return
    try:
        handle.write(text)
        handle.flush()
    except Exception:
        pass
    finally:
        try:
            handle.close()
        except Exception:
            pass


def _decoder_selftest() -> int:
    """Import the candidate decoder at ``argv`` and report the result.

    The exit code is the contract: 0 means the decoder imported in this
    runtime.  Reporting is deliberately separated from the verdict, because a
    stream that cannot be written to must never turn a healthy decoder into a
    failed verification (which would make the provisioner reject a good wheel
    and reinstall on every launch).
    """
    import importlib
    import traceback
    import decoder_provisioner

    try:
        idx = sys.argv.index("--decoder-selftest")
        path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    except ValueError:
        path = ""
    if path:
        sys.path.insert(0, path)
    else:
        try:
            decoder_provisioner.activate_site()
        except Exception:
            pass  # the import below is the real test

    try:
        importlib.invalidate_caches()
        module = importlib.import_module("qector_decoder_v3")
        version_str = "OK " + (getattr(module, "__version__", "") or "")
    except BaseException:
        # BaseException: a broken compiled extension can raise SystemError or
        # worse, and that is still just a failed import, not a crash of ours.
        try:
            detail = traceback.format_exc()
        except Exception:
            detail = "decoder import failed (traceback unavailable)"
        _selftest_write(detail)
        return 1

    _selftest_write(version_str)
    return 0


def _bootstrap_decoder(on_log=None) -> dict:
    """Provision the external decoder before anything imports ``backend``."""
    try:
        import decoder_provisioner
        return decoder_provisioner.bootstrap(on_log=on_log)
    except Exception as exc:
        return {"ok": False, "message": f"decoder bootstrap failed: {exc}"}


# ---------------------------------------------------------------------------
# Boot feedback
# ---------------------------------------------------------------------------
# Cold-start cost is real and unavoidable: the onefile bootloader unpacks ~60 MB
# and the qector_decoder_v3 Rust/PyO3 extension takes several seconds to import
# on a cold file cache.  Without visible feedback the desktop stays empty and
# the app looks like it never started.  Frozen builds use PyInstaller's native
# splash (painted before Python starts, and safe to keep open alongside the
# CustomTkinter root); running from source falls back to a plain Tk splash.


class _Splash:
    """Boot progress indicator that is always safe to call, even if unusable.

    Auto-closes after ``_TIMEOUT_S`` seconds even if the real window never
    appears (e.g. the decoder import hangs), so the desktop never shows a
    permanently frozen splash.
    """

    _TIMEOUT_S = 30.0

    def __init__(self, enabled: bool = True) -> None:
        self._pyi = None
        self._root = None
        self._label = None
        self._message = "Starting..."
        self._tick = 0
        self._closed = False
        self._started = time.monotonic()
        if not enabled:
            return
        try:
            import pyi_splash  # provided by the PyInstaller bootloader
            if pyi_splash.is_alive():
                self._pyi = pyi_splash
                return
        except Exception:
            self._pyi = None
        self._build_tk_splash()

    def _build_tk_splash(self) -> None:
        """Fallback splash for source runs (no bootloader splash available)."""
        try:
            import tkinter as tk
            root = tk.Tk()
            root.title("QECTOR Decoder Workbench")
            bg, fg, dim = "#12141a", "#e8ecf4", "#8294ad"
            root.configure(bg=bg)
            w, h = 520, 260
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
            root.resizable(False, False)
            ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.ico")
            if os.path.isfile(ico):
                try:
                    root.iconbitmap(ico)
                except Exception:
                    pass
            tk.Label(root, text="QECTOR", bg=bg, fg=fg,
                     font=("Segoe UI", 34, "bold")).pack(pady=(70, 0))
            tk.Label(root, text="Decoder Workbench", bg=bg, fg=dim,
                     font=("Segoe UI", 12)).pack(pady=(2, 26))
            self._label = tk.Label(root, text=self._message, bg=bg, fg=dim,
                                   font=("Segoe UI", 9), wraplength=w - 80)
            self._label.pack()
            root.attributes("-topmost", True)
            root.update()
            self._root = root
        except Exception:
            self._root = None
            self._label = None

    @property
    def native(self) -> bool:
        """True when the bootloader splash is in use.

        Only the native splash may stay open while the CustomTkinter root is
        built; the Tk fallback must be destroyed first, because two live Tk
        interpreters in one process is a known source of flakiness.
        """
        return self._pyi is not None

    def set(self, message: str) -> None:
        """Record a status line.  Safe to call from a worker thread."""
        self._message = str(message or "").strip() or self._message

    def pump(self) -> None:
        """Repaint from the main thread so the splash never looks frozen.

        Also enforces the auto-close timeout: once ``_TIMEOUT_S`` seconds
        elapse the splash closes itself so a hung boot cannot leave the
        splash pinned to the desktop forever.
        """
        if self._closed:
            return
        if time.monotonic() - self._started > self._TIMEOUT_S:
            self.close()
            return
        self._tick += 1
        text = self._message + "." * (self._tick % 4)
        if self._pyi is not None:
            try:
                self._pyi.update_text(text)
            except Exception:
                self._pyi = None
            return
        if self._root is not None:
            try:
                if self._label is not None:
                    self._label.configure(text=text)
                self._root.update()
            except Exception:
                self._root = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pyi is not None:
            try:
                self._pyi.close()
            except Exception:
                pass
            self._pyi = None
        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._label = None


def _bootstrap_with_splash(splash: "_Splash") -> dict:
    """Provision the decoder on a worker thread while the splash keeps painting."""
    import threading

    result: dict = {}
    done = threading.Event()

    def worker() -> None:
        try:
            result.update(_bootstrap_decoder(on_log=splash.set))
        except Exception as exc:  # never let a boot thread kill the app
            result.update({"ok": False, "message": f"decoder bootstrap failed: {exc}"})
        finally:
            done.set()

    threading.Thread(target=worker, name="qector-boot", daemon=True).start()
    while not done.wait(0.12):
        splash.pump()
    return result or {"ok": False, "message": "decoder bootstrap returned nothing"}


def _report_bootstrap_failure(status: dict) -> None:
    message = (
        "QECTOR could not start because qector-decoder-v3 is unavailable.\n\n"
        f"{status.get('message', 'Unknown provisioning error')}\n\n"
        "Install a matching CPython with pip, then restart QECTOR. "
        "Set QECTOR_PYTHON to that interpreter if it is not on PATH."
    )
    print(message, file=sys.stderr)
    if "--mcp" not in sys.argv:
        try:
            from tkinter import messagebox
            messagebox.showerror("QECTOR Decoder unavailable", message)
        except Exception:
            pass


def _attach_console_if_needed() -> None:
    """Attach to parent console on Windows when running CLI/MCP/selftest in a GUI build."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        if any(arg in sys.argv for arg in ("--cli", "--mcp", "--decoder-selftest")):
            try:
                import ctypes
                ctypes.windll.kernel32.AttachConsole(-1)
            except Exception:
                pass
            if sys.stdout is None or getattr(sys.stdout, "name", "") in ("", "<none>", "<null>"):
                try:
                    sys.stdout = open(1, "w", encoding="utf-8", buffering=1)
                except Exception:
                    try:
                        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                    except Exception:
                        pass
            if sys.stderr is None or getattr(sys.stderr, "name", "") in ("", "<none>", "<null>"):
                try:
                    sys.stderr = open(2, "w", encoding="utf-8", buffering=1)
                except Exception:
                    try:
                        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
                    except Exception:
                        pass


class _LogStream:
    """Minimal write-only stream standing in for a missing stdout/stderr.

    ``name`` is reported as ``"<null>"`` on purpose: the console-attach helpers
    treat that as "not a real console" and re-open the true file descriptor when
    one exists, so installing this never hides a usable pipe.
    """

    name = "<null>"
    encoding = "utf-8"
    errors = "replace"

    def __init__(self, path=None) -> None:
        self._path = path

    def write(self, text) -> int:
        text = "" if text is None else str(text)
        if self._path:
            try:
                with open(self._path, "a", encoding="utf-8", errors="replace") as handle:
                    handle.write(text)
            except Exception:
                self._path = None
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def fileno(self):
        raise OSError("stream has no file descriptor")

    def writable(self) -> bool:
        return True


def _ensure_std_streams() -> None:
    """Guarantee ``sys.stdout``/``sys.stderr`` are writable objects, never None.

    A frozen windowed build (``console=False``) leaves both set to ``None``.  Any
    module that prints while being imported then dies with
    ``AttributeError: 'NoneType' object has no attribute 'write'`` - which is
    exactly how the ``qector_decoder_v3`` licence banner broke the GUI boot while
    ``--cli`` / ``--mcp`` / ``--decoder-selftest`` (which attach a console first)
    kept working.  The failure surfaced as a bogus "ABI mismatch" dialog followed
    by a pointless reinstall on every launch.
    """
    target = None
    try:
        from utils import get_data_dir
        log_dir = os.path.join(get_data_dir(), "logs")
        os.makedirs(log_dir, exist_ok=True)
        target = os.path.join(log_dir, "boot_stdio.log")
    except Exception:
        target = None

    for name in ("stdout", "stderr", "__stdout__", "__stderr__"):
        stream = getattr(sys, name, None)
        usable = False
        if stream is not None:
            try:
                stream.write("")
                stream.flush()
                usable = True
            except Exception:
                usable = False
        if not usable:
            try:
                setattr(sys, name, _LogStream(target))
            except Exception:
                pass


def launch() -> int:
    """Start GUI or MCP after validating the externally managed decoder."""
    _attach_console_if_needed()
    # Must run before ANY third-party import: see _ensure_std_streams.
    _ensure_std_streams()

    # Air-gap enforcement is mandatory for every runtime.  Loopback stays
    # allowed for local services; all external DNS/connect attempts are blocked
    # and logged.  Never raises: a broken guard must not brick the boot.
    try:
        import compliance
        compliance.install_egress_guard()
            
        # Also enforce monotonic time to prevent offline clock rollback attacks
        import utils
        utils.enforce_monotonic_clock()
    except Exception:
        pass

    # Load and decrypt stored license key if present (with auto-migration)
    try:
        from pathlib import Path
        import utils
        qdir = Path.home() / ".qector"
        lpath = qdir / "license.key"
        if lpath.is_file():
            content = lpath.read_text(encoding="utf-8").strip()
            if content:
                try:
                    decrypted = utils.decrypt_license_key(content)
                    # NOTE: the decoder reads the key from the environment at
                    # runtime. Child processes spawned by the provisioner are
                    # given a scrubbed env (see decoder_provisioner._scrubbed_env)
                    # so the key does not leak into pip/probe subprocesses.
                    os.environ["QECTOR_LICENSE_KEY"] = decrypted
                except Exception:
                    # Legacy plaintext key: migrate and encrypt
                    os.environ["QECTOR_LICENSE_KEY"] = content
                    try:
                        encrypted = utils.encrypt_license_key(content)
                        lpath.write_text(encrypted + "\n", encoding="utf-8")
                        os.chmod(lpath, 0o600)
                    except Exception:
                        pass
    except Exception:
        pass

    # Fast path: the provisioner re-invokes us in this mode to verify a candidate
    # decoder in the real runtime. Handle it before anything else (no GUI, no
    # provisioning, no multiprocessing bootstrap) so verification is cheap and
    # cannot recurse into another provisioning pass.
    if "--decoder-selftest" in sys.argv:
        return _decoder_selftest()
    multiprocessing.freeze_support()

    headless = (
        "--mcp" in sys.argv
        or "--cli" in sys.argv
        or "--version" in sys.argv
        or "-V" in sys.argv
        or (len(sys.argv) > 1 and sys.argv[1] in _CLI_COMMANDS)
    )

    if headless:
        status = _bootstrap_decoder()
        if not status.get("ok"):
            _report_bootstrap_failure(status)
            return 1
        if "--mcp" in sys.argv:
            from mcp_server import main as mcp_main
            return int(mcp_main() or 0)
        import cli
        cli_args = [a for a in sys.argv[1:] if a != "--cli"]
        return int(cli.main(cli_args) or 0)

    # GUI path: show the splash first, then provision, so the app is visible
    # from the first second instead of after the cold decoder import.
    splash = _Splash()
    try:
        status = _bootstrap_with_splash(splash)
        if not status.get("ok"):
            splash.close()
            _report_bootstrap_failure(status)
            return 1
        splash.set("Opening workbench")
        splash.pump()
        from app import main as app_main
        if splash.native:
            # Bootloader splash stays up until the real window is on screen.
            app_main(on_ready=splash.close)
        else:
            splash.close()
            app_main()
        return 0
    finally:
        splash.close()


if __name__ == "__main__":
    raise SystemExit(launch())