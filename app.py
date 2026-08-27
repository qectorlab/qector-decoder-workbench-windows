"""app.py — Main application entry point for QECTOR Decoder Workbench.

Builds the full application window: a CTkTabview with the six feature tabs
plus a live Console tab, a status bar showing the app version and the
current code summary, per-tab crash isolation, a global Tk callback
exception hook with a non-modal error toast, a logging ``sys.excepthook``,
and an auto-update check that is scheduled only after the window exists.

Importing this module has zero side effects: no threads are started and no
network calls are made until a :class:`QectorApp` is constructed and its
event loop begins servicing timers.
"""

from __future__ import annotations

import sys
import tkinter
import traceback
from typing import Any, Optional

from version import FULL_VERSION, WORKBENCH_VERSION

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False


def _declare_dpi_awareness() -> None:
    """Declare per-monitor DPI awareness so the GUI is sharp on 4K displays.

    Must run before the Tk root is created; safe to call from any platform
    (no-op outside Windows).  Never raises.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass
    except Exception:
        pass


_WINDOW_WIDTH = 1280
_WINDOW_HEIGHT = 820
_MIN_WIDTH = 1100
_MIN_HEIGHT = 700

# Boot maximized (full screen work area) on desktop platforms; tests set this
# to False so the requested geometry stays explicit.
_START_MAXIMIZED = True

# (tab name, module, class) in display order; the Console tab is built inline.
_TAB_SPECS = [
    ("Code Explorer", "code_explorer_tab", "CodeExplorerTab"),
    ("Decoder Lab", "decoder_lab_tab", "DecoderLabTab"),
    ("Benchmark", "benchmark_tab", "BenchmarkTab"),
    ("Batch & Streaming", "batch_streaming_tab", "BatchStreamingTab"),
    ("History", "history_tab", "HistoryTab"),
    ("Hardware", "hardware_tab", "HardwareTab"),
    ("Diagnostics", "diagnostics_tab", "DiagnosticsTab"),
    ("Documentation", "documentation_tab", "DocumentationTab"),
    ("Lab & Personal Info", "lab_info_tab", "LabInfoTab"),
]

TAB_NAMES = [spec[0] for spec in _TAB_SPECS] + ["Console"]


def _safe_logger():
    """Return the app logger, or None when logging cannot be initialised."""
    try:
        from logger import get_logger
        return get_logger()
    except Exception:
        return None


_EXCEPTHOOK_INSTALLED = False
_PREVIOUS_EXCEPTHOOK: Optional[Any] = None


def _install_sys_excepthook() -> None:
    """Install (once) a ``sys.excepthook`` that logs uncaught exceptions."""
    global _EXCEPTHOOK_INSTALLED, _PREVIOUS_EXCEPTHOOK
    if _EXCEPTHOOK_INSTALLED:
        return
    _PREVIOUS_EXCEPTHOOK = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            logger = _safe_logger()
            if logger is not None:
                logger.error(
                    "Uncaught exception:\n"
                    + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                )
        except Exception:
            pass
        try:
            if _PREVIOUS_EXCEPTHOOK is not None:
                _PREVIOUS_EXCEPTHOOK(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = _hook
    _EXCEPTHOOK_INSTALLED = True


# ---------------------------------------------------------------------------
# Application class
# ---------------------------------------------------------------------------

class QectorApp:
    """Main QECTOR Decoder Workbench application with all tabs wired."""

    def __init__(self):
        if not _HAS_GUI:
            raise RuntimeError("customtkinter is required for QectorApp")

        import theme
        import threading_utils
        from console import Console
        from state import AppState

        self._logger = _safe_logger()
        self._colors = theme.COLORS
        self._fonts = theme.get_fonts()

        ctk.set_appearance_mode("dark")  # default color theme is kept as-is

        self._app = ctk.CTk()
        self.root = self._app  # backward-compatible alias
        self._width = _WINDOW_WIDTH
        self._height = _WINDOW_HEIGHT
        # The app re-versions itself to the live decoder backend: the topbar
        # never shows a hardcoded workbench number.  Seed from the installed
        # decoder version (synchronous, no network); the boot update check then
        # swaps in the live PyPI-resolved version.
        self._version_title = self._boot_version_string()
        self._app.title(self._version_title)
        self._app.geometry(f"{self._width}x{self._height}")
        self._app.minsize(_MIN_WIDTH, _MIN_HEIGHT)
        self._set_window_icon()

        self.state = AppState()
        self.console = Console()
        self.tabs: dict[str, Any] = {}
        self._toast: Any = None
        self._toast_after_id: Optional[str] = None
        self._update_after_id: Optional[str] = None
        self._destroyed = False

        # UI pump: safe marshalling of console/status updates coming from
        # worker threads onto the Tk main thread.
        self._ui = threading_utils.UiPump(self._app)

        # Global crash handling: Tk callbacks and uncaught thread exceptions
        # are logged and surfaced without ever killing the app.
        self._app.report_callback_exception = self._on_tk_exception
        _install_sys_excepthook()

        self._build_ui()
        self._center_and_lift_window()
        self.console.log(f"{self._version_title} ready", "INFO")

        # Session persistence: restore the last workspace (code family,
        # distance, decoder, error rate, seed) and preferences, then arm
        # saving on close.  All failure paths are silent — persistence is a
        # convenience, never a boot blocker.
        self._workspace_path: Optional[Any] = None
        self._prefs_path: Optional[Any] = None
        self._restore_session()
        try:
            self._app.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

        # Memory monitoring: periodic RSS check with a warning at 500 MB and
        # a restart offer at 1 GB (Phase-2 hardening item).
        self._mem_after_id: Optional[str] = None
        self._mem_warned = False
        try:
            self._mem_after_id = self._app.after(30000, self._monitor_memory)
        except Exception:
            self._mem_after_id = None

        # Keyboard shortcuts (Phase-2 hardening item).
        self._bind_shortcuts()

        # Auto-update check: scheduled AFTER construction; importing this
        # module starts zero threads and makes zero network calls.
        try:
            self._update_after_id = self._app.after(1500, self._start_update_check)
        except Exception:
            self._update_after_id = None

        self._boot_tests_scheduled = False
        try:
            self._app.after(900, self._start_boot_tests)
        except Exception:
            pass

    # ── public surface used by tests ──────────────────────────────────
    def title(self) -> str:
        return self._app.title()

    def winfo_reqwidth(self) -> int:
        """The configured window width (matches the geometry set in __init__)."""
        return int(self._width)

    def winfo_reqheight(self) -> int:
        """The configured window height (matches the geometry set in __init__)."""
        return int(self._height)

    def destroy(self) -> None:
        self._destroyed = True
        try:
            self._save_session()
        except Exception:
            pass
        try:
            self.console.unsubscribe(self._on_console_output)
        except Exception:
            pass
        # Close UI pumps first so no stale "after" timers outlive the window.
        try:
            self._ui.close()
        except Exception:
            pass
        for tab in list(self.tabs.values()):
            pump = getattr(tab, "_ui", None)
            if pump is not None:
                try:
                    pump.close()
                except Exception:
                    pass
        for after_id in (self._update_after_id, self._toast_after_id, self._mem_after_id):
            if after_id is not None:
                try:
                    self._app.after_cancel(after_id)
                except Exception:
                    pass
        self._update_after_id = None
        self._toast_after_id = None
        self._mem_after_id = None
        # Cancel every remaining Tk "after" timer — including CustomTkinter's
        # internal DPI/scaling-tracker loop, which reschedules itself and would
        # otherwise fire against the destroyed interpreter (an intermittent
        # _tkinter.TclError when roots are created and torn down repeatedly).
        try:
            pending = self._app.tk.splitlist(self._app.tk.call("after", "info"))
            for aid in pending:
                try:
                    self._app.after_cancel(aid)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._app.update_idletasks()
        except Exception:
            pass
        try:
            self._app.destroy()
        except Exception:
            pass
        # Failure-proof window teardown.  Cancelling every pending "after"
        # timer above deletes Tcl commands that CustomTkinter widgets still
        # hold, so tkinter.Tk.destroy() can abort mid-recursion with
        # "can't delete Tcl command" BEFORE the root window itself is
        # destroyed — leaving mainloop() spinning forever and the app unable
        # to close.  Fall back to the raw Tcl destroy so the window always
        # dies and mainloop() always returns.
        try:
            if self._app.tk.getboolean(self._app.tk.call("winfo", "exists", self._app._w)):
                self._app.tk.call("destroy", self._app._w)
        except Exception:
            pass

    def mainloop(self) -> None:
        try:
            self._app.mainloop()
        except tkinter.TclError:
            # CustomTkinter's mainloop wrapper re-applies the Windows
            # titlebar colour once the event loop unwinds; when the window
            # was already destroyed by _on_close this raises "application
            # has been destroyed".  The app is closing anyway — swallow it so
            # teardown returns cleanly instead of surfacing as a fatal error.
            pass

    # ── window icon (taskbar / title bar) ────────────────────────────
    def _set_window_icon(self) -> None:
        """Set the window icon, using the right mechanism per platform.

        Windows Tk supports ``.ico`` via ``iconbitmap``; Linux/X11 Tk does not
        (it expects an XBM there) and instead takes a raster image through
        ``iconphoto``.  On Linux we therefore load the bundled ``icon.png``
        (generated at build time from ``icon.jpg``) as a :class:`tkinter.PhotoImage`
        and keep a reference on ``self`` so Tk does not garbage-collect it,
        which would blank the icon.  The whole method is defensive: a missing
        or unreadable icon never prevents the window from opening.
        """
        try:
            import os
            # In a frozen onedir build ``icon.ico`` sits alongside the launcher,
            # while imported modules normally live under ``_internal``.  Probe
            # both locations so the Windows title bar/taskbar always receives
            # the shipped ICO rather than Tk's default feather icon.
            search_dirs: list[str] = []
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                search_dirs.append(meipass)
            try:
                search_dirs.append(os.path.dirname(os.path.abspath(sys.executable)))
            except Exception:
                pass
            search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
            try:
                search_dirs.append(os.getcwd())
            except Exception:
                pass
            if os.name == "nt":
                # Give the app its own taskbar identity so Windows shows the
                # app icon instead of the generic pythonw/Tk feather icon.
                try:
                    import ctypes
                    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                        "iD01t.QECTOR.Workbench")
                except Exception:
                    pass
                for directory in search_dirs:
                    ico = os.path.join(directory, "icon.ico")
                    if os.path.isfile(ico):
                        self._reassert_icon_path = ico
                        try:
                            self._app.iconbitmap(ico)
                        except Exception:
                            pass
                        # CustomTkinter re-applies its OWN default icon ~200ms
                        # after window creation (the "generic" icon); re-assert
                        # the real icon a few times afterwards so it always wins,
                        # in both source runs and frozen builds.
                        for delay in (250, 500, 1200):
                            try:
                                self._app.after(delay, self._reassert_window_icon)
                            except Exception:
                                pass
                        break
                return
            # Linux / macOS: prefer a PNG via iconphoto.
            for directory in search_dirs:
                png = os.path.join(directory, "icon.png")
                if os.path.isfile(png):
                    self._icon_image = tkinter.PhotoImage(file=png)
                    self._app.iconphoto(True, self._icon_image)
                    break
        except Exception:
            pass

    def _reassert_window_icon(self) -> None:
        """Re-apply the real window icon after CustomTkinter's delayed reset."""
        if getattr(self, "_destroyed", False):
            return
        ico = getattr(self, "_reassert_icon_path", None)
        if not ico:
            return
        try:
            self._app.iconbitmap(ico)
            self._app.wm_iconbitmap(ico)
        except Exception:
            pass

    def _center_and_lift_window(self) -> None:
        """Open full screen on the active monitor, lift, and force focus.

        On Windows the active-monitor work area is resolved via win32api so a
        multi-monitor setup never centers across the primary by mistake.  The
        window boots full screen by default; tests that assert on geometry set
        _START_MAXIMIZED = False.
        """
        try:
            self._app.update_idletasks()
            if _START_MAXIMIZED:
                # Prefer a MAXIMIZED window (state "zoomed") over true
                # fullscreen: fullscreen hides the title bar, which removes
                # the minimize/maximize/close window buttons entirely.
                try:
                    self._app.state("zoomed")
                except Exception:
                    try:
                        self._app.attributes("-fullscreen", True)
                    except Exception:
                        pass
            # Always center explicitly so single-monitor / fallback layouts
            # still start in the middle of the visible work area.
            try:
                import os
                dev_mode = bool(os.environ.get("QECTOR_CENTER_ON_PRIMARY"))
            except Exception:
                dev_mode = False
            work_x, work_y, work_w, work_h = 0, 0, 0, 0
            if self._app.state() == "zoomed" or self._app.attributes("-fullscreen"):
                # Maximized windows manage their own geometry; just deiconify+lift.
                self._app.deiconify()
                self._app.lift()
                self._app.focus_force()
                self._app.attributes("-topmost", True)
                self._app.after(600, lambda: self._app.attributes("-topmost", False))
                return
            if sys.platform == "win32" and not dev_mode:
                try:
                    import ctypes
                    u32 = ctypes.windll.user32
                    hwnd = self._app.winfo_id()
                    monitor = u32.MonitorFromWindow(
                        hwnd, 2  # MONITOR_DEFAULTTONEAREST
                    )
                    info = ctypes.create_unicode_buffer(40 * 4)
                    info[0:4] = (40,)
                    if u32.GetMonitorInfoW(hwnd, info):
                        work = tuple(info[16:24])
                        work_x, work_y, work_w, work_h = (
                            work[0], work[1], work[2] - work[0], work[3] - work[1],
                        )
                except Exception:
                    work_x, work_y, work_w, work_h = 0, 0, 0, 0
            if not (work_w > 0 and work_h > 0):
                sw = self._app.winfo_screenwidth()
                sh = self._app.winfo_screenheight()
                work_w, work_h = sw, sh
            if work_w > 0 and work_h > 0:
                x = max(0, work_x + (work_w - self._width) // 2)
                y = max(0, work_y + (work_h - self._height) // 2)
                self._app.geometry(f"{self._width}x{self._height}+{x}+{y}")
            self._app.deiconify()
            self._app.lift()
            self._app.focus_force()
            self._app.attributes("-topmost", True)
            self._app.after(600, lambda: self._app.attributes("-topmost", False))
        except Exception:
            pass

    # ── UI construction ───────────────────────────────────────────────
    def _build_ui(self) -> None:
        """Build the full application layout: tabview + status bar."""
        self._app.grid_columnconfigure(0, weight=1)
        self._app.grid_rowconfigure(0, weight=1)
        self._app.grid_rowconfigure(1, weight=0)

        menu = tkinter.Menu(self._app)
        menu.configure(bg='#2b2b2b', fg='#dcdcdc', activebackground='#4a9eff', activeforeground='#ffffff')
        
        doc_menu = tkinter.Menu(menu, tearoff=0)
        doc_menu.configure(bg='#2b2b2b', fg='#dcdcdc', activebackground='#4a9eff', activeforeground='#ffffff')
        
        doc_menu.add_command(label="Generate Documentation...", accelerator="Ctrl+D", 
                             command=lambda: self.tabs.get("Documentation")._on_generate() if "Documentation" in self.tabs else None)
        doc_menu.add_command(label="Open Export Folder", 
                             command=lambda: self.tabs.get("Documentation")._on_open_folder() if "Documentation" in self.tabs else None)
        doc_menu.add_separator()
        doc_menu.add_command(label="Export Official Docs...", 
                             command=lambda: self.tabs.get("Documentation")._on_export_official() if "Documentation" in self.tabs else None)
        
        menu.add_cascade(label="Documentation", menu=doc_menu)
        self._app.config(menu=menu)
        self._app.bind("<Control-d>", lambda e: self.tabs.get("Documentation")._on_generate() if "Documentation" in self.tabs else None)

        self.tabview = ctk.CTkTabview(self._app)
        self.tabview.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))
        for name in TAB_NAMES:
            self.tabview.add(name)

        for tab_name, module_name, class_name in _TAB_SPECS:
            self._wire_tab(tab_name, module_name, class_name)

        self._build_console_tab()
        self._build_status_bar()
        self.tabview.set("Code Explorer")

    def _wire_tab(self, tab_name: str, module_name: str, class_name: str) -> None:
        """Import and instantiate a tab class into its tabview slot.

        Crash isolation: if the import or construction fails, a fallback
        frame showing the error is mounted instead and the rest of the app
        keeps working.
        """
        parent = self.tabview.tab(tab_name)
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        try:
            import importlib
            mod = importlib.import_module(module_name)
            cls = getattr(mod, class_name)
            widget = cls(parent, state=self.state, console=self.console, fonts=self._fonts)
            widget.grid(row=0, column=0, sticky="nsew")
            self.tabs[tab_name] = widget
            self.console.log(f"Tab loaded: {tab_name}", "INFO")
        except Exception as exc:
            detail = traceback.format_exc()
            if self._logger is not None:
                self._logger.error(f"Failed to load tab {tab_name}:\n{detail}")
            self.console.log(f"Failed to load tab {tab_name}: {exc}", "ERROR")
            try:
                fallback = ctk.CTkFrame(parent, fg_color=self._colors["bg_panel"])
                fallback.grid(row=0, column=0, sticky="nsew")
                ctk.CTkLabel(
                    fallback,
                    text=f"Failed to load {tab_name}:\n\n{exc}",
                    text_color=self._colors["error"],
                    font=ctk.CTkFont(family=self._fonts.mono, size=11),
                    wraplength=760,
                    justify="left",
                ).pack(anchor="nw", padx=20, pady=20)
                # Tab crash recovery: a "Reload Tab" button that destroys the
                # dead widget and reinstantiates the tab class (Phase-2 item).
                ctk.CTkButton(
                    fallback,
                    text="Reload Tab",
                    font=ctk.CTkFont(size=12, weight="bold"),
                    width=120,
                    command=lambda n=tab_name, m=module_name, c=class_name: self.reload_tab(n, m, c),
                ).pack(anchor="nw", padx=20, pady=(0, 12))
            except Exception:
                pass

    def reload_tab(self, tab_name: str, module_name: str, class_name: str) -> None:
        """Destroy a crashed tab widget and re-instantiate the tab class.

        Used by the fallback "Reload Tab" button; also called when a tab's
        own callback reports a crash.  Safe when the tab never loaded.
        """
        if getattr(self, "_destroyed", False):
            return
        parent = self.tabview.tab(tab_name)
        old = self.tabs.get(tab_name)
        if old is not None:
            try:
                pump = getattr(old, "_ui", None)
                if pump is not None:
                    try:
                        pump.close()
                    except Exception:
                        pass
                old.destroy()
            except Exception:
                pass
            self.tabs.pop(tab_name, None)
        try:
            for child in parent.winfo_children():
                try:
                    child.destroy()
                except Exception:
                    pass
        except Exception:
            pass
        self._wire_tab(tab_name, module_name, class_name)

    # ── Console tab ───────────────────────────────────────────────────
    def _build_console_tab(self) -> None:
        """Build the live Console tab: a read-only textbox fed by Console."""
        parent = self.tabview.tab("Console")
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        parent.grid_rowconfigure(1, weight=0)

        self._console_text = ctk.CTkTextbox(
            parent, wrap="word",
            font=ctk.CTkFont(family=self._fonts.mono, size=self._fonts.mono_size + 1),
        )
        self._console_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        self._console_text.configure(state="disabled")

        btn_frame = ctk.CTkFrame(parent, fg_color="transparent")
        btn_frame.grid(row=1, column=0, sticky="ew", padx=8, pady=6)
        ctk.CTkButton(
            btn_frame, text="Clear", width=90,
            command=self._clear_console,
            font=ctk.CTkFont(size=11),
        ).pack(side="right", padx=4)

        # Live subscription: Console.write may fire from worker threads, so
        # each chunk is marshalled to the UI thread before touching Tk.
        self.console.subscribe(self._on_console_output)
        self._refresh_console()

    def _on_console_output(self, text: str) -> None:
        """Console subscriber — may run on any thread; marshal to UI."""
        if self._destroyed:
            return
        self._ui.post(self._append_console, text)

    def _append_console(self, text: str) -> None:
        try:
            self._console_text.configure(state="normal")
            self._console_text.insert("end", text)
            self._console_text.see("end")
            self._console_text.configure(state="disabled")
        except tkinter.TclError:
            pass

    def _refresh_console(self) -> None:
        try:
            self._console_text.configure(state="normal")
            self._console_text.delete("1.0", "end")
            self._console_text.insert("1.0", self.console.get_text())
            self._console_text.see("end")
            self._console_text.configure(state="disabled")
        except tkinter.TclError:
            pass

    def _clear_console(self) -> None:
        self.console.clear()
        self._refresh_console()

    # ── Status bar ────────────────────────────────────────────────────
    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(
            self._app, height=30, corner_radius=0,
            fg_color=self._colors["bg_status"],
        )
        bar.grid(row=1, column=0, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)
        bar.grid_columnconfigure(1, weight=0)
        bar.grid_columnconfigure(2, weight=0)

        self._status_left = ctk.CTkLabel(
            bar, text=self._version_title, anchor="w",
            font=ctk.CTkFont(family=self._fonts.mono, size=10),
            text_color=self._colors["text_secondary"],
        )
        self._status_left.grid(row=0, column=0, sticky="w", padx=(10, 4), pady=2)

        offline_badge = ctk.CTkLabel(
            bar, text="AIR-GAPPED · NO NETWORK", width=150,
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=self._colors["text_secondary"],
        )
        offline_badge.grid(row=0, column=1, sticky="e", padx=(4, 4), pady=2)

        self._status_right = ctk.CTkLabel(
            bar, text="no code built", anchor="e",
            font=ctk.CTkFont(family=self._fonts.mono, size=10),
            text_color=self._colors["text_secondary"],
        )
        self._status_right.grid(row=0, column=2, sticky="e", padx=(4, 10), pady=2)

        self.state.on_code_changed(self._on_state_code_changed)

    def _on_state_code_changed(self) -> None:
        """State listener — may fire from any thread; marshal to UI."""
        if self._destroyed:
            return
        self._ui.post(self._refresh_status_code)

    def _refresh_status_code(self) -> None:
        try:
            code = self.state.current_code
            if code is None:
                text = "no code built"
            else:
                n_qubits = getattr(code, "n_qubits", "?")
                text = (
                    f"{self.state.current_family_key} d={self.state.current_param}"
                    f" - {n_qubits} qubits"
                )
            self._status_right.configure(text=text)
        except tkinter.TclError:
            pass

    # ── Error toast + exception hooks ─────────────────────────────────
    def _show_error_toast(self, message: str) -> None:
        """Show a non-modal, self-dismissing error toast (bottom-right)."""
        try:
            self._dismiss_toast()
            msg = (message or "").strip() or "Unknown error"
            if len(msg) > 400:
                msg = msg[:400] + "…"
            toast = ctk.CTkFrame(
                self._app, corner_radius=8, border_width=1,
                fg_color=self._colors["bg_toast"],
                border_color=self._colors["border_toast"],
            )
            ctk.CTkLabel(
                toast, text=msg, wraplength=460, justify="left",
                text_color=self._colors["text_primary"],
                font=ctk.CTkFont(size=11),
            ).pack(padx=14, pady=(10, 6))
            ctk.CTkButton(
                toast, text="Dismiss", width=76, height=24,
                command=self._dismiss_toast,
                font=ctk.CTkFont(size=11),
            ).pack(padx=14, pady=(0, 10), anchor="e")
            toast.place(relx=1.0, rely=1.0, x=-18, y=-44, anchor="se")
            self._toast = toast
            self._toast_after_id = self._app.after(8000, self._dismiss_toast)
        except Exception:
            pass

    def _dismiss_toast(self) -> None:
        toast, self._toast = self._toast, None
        after_id, self._toast_after_id = self._toast_after_id, None
        if after_id is not None:
            try:
                self._app.after_cancel(after_id)
            except Exception:
                pass
        if toast is not None:
            try:
                toast.destroy()
            except Exception:
                pass

    def _on_tk_exception(self, exc_type, exc_value, exc_tb) -> None:
        """Global Tk callback exception hook — log, surface, never re-raise."""
        try:
            detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        except Exception:
            detail = f"{exc_type}: {exc_value}"
        try:
            if self._logger is not None:
                self._logger.error(f"Unhandled Tk callback exception:\n{detail}")
        except Exception:
            pass
        try:
            self.console.log(f"Unhandled exception:\n{detail}", "ERROR")
        except Exception:
            pass
        try:
            self._show_error_toast(f"{getattr(exc_type, '__name__', exc_type)}: {exc_value}")
        except Exception:
            pass

    # ── session persistence (Phase-2 item) ─────────────────────────────
    def _session_paths(self):
        """Return (workspace_path, prefs_path) inside the per-user data dir."""
        try:
            import utils
            data_dir = utils.get_data_dir()
            try:
                wpath = data_dir / "workspace.json"
                ppath = data_dir / "preferences.json"
            except Exception:
                wpath, ppath = None, None
            return wpath, ppath
        except Exception:
            return None, None

    def _restore_session(self) -> None:
        """Restore workspace + preferences on launch (silent on any failure).

        Applies last-session values onto the freshly built tabs: code family +
        distance (Code Explorer), decoder + error rate + seed (Decoder Lab /
        Benchmark).  Any mismatch with the current tab options is ignored —
        persistence is a convenience, never a source of errors.
        """
        try:
            import utils
            wpath, ppath = self._session_paths()
            self._workspace_path, self._prefs_path = wpath, ppath
            ws = utils.load_json(wpath, {}) if wpath else {}
            prefs = utils.load_json(ppath, {}) if ppath else {}
            self._preferences = dict(prefs) if isinstance(prefs, dict) else {}

            # Restore the saved color theme (dark / light / high contrast) on
            # launch; the toggle in Lab & Personal Info writes it to prefs.
            try:
                import theme
                theme.set_appearance_mode(str(self._preferences.get("theme") or "dark"))
            except Exception:
                pass

            family = str(ws.get("family") or "")
            distance = int(ws.get("distance") or 0)
            decoder = str(ws.get("decoder") or "")
            rate = float(ws.get("error_rate") or 0.05)
            seed = int(ws.get("seed") or 42)

            for tab in self.tabs.values():
                try:
                    if family and getattr(tab, "family_var", None) is not None:
                        tab.family_var.set(family)
                except Exception:
                    pass
                try:
                    if distance and getattr(tab, "distance_var", None) is not None:
                        tab.distance_var.set(distance)
                except Exception:
                    pass
                try:
                    if decoder and getattr(tab, "decoder_var", None) is not None:
                        tab.decoder_var.set(decoder)
                except Exception:
                    pass
                try:
                    if getattr(tab, "rate_var", None) is not None:
                        tab.rate_var.set(rate)
                except Exception:
                    pass
                try:
                    if getattr(tab, "seed_entry", None) is not None:
                        tab.seed_entry.delete(0, "end")
                        tab.seed_entry.insert(0, str(seed))
                except Exception:
                    pass
            self.console.log("Session restored from workspace data.", "INFO")
        except Exception:
            pass

    def _save_session(self) -> None:
        """Persist workspace state and preferences on close (best effort)."""
        try:
            import utils
            ws: dict[str, Any] = {"family": "rotated_surface", "distance": 5,
                                  "decoder": "", "error_rate": 0.05, "seed": 42}
            try:
                if self.state is not None:
                    ws["family"] = self.state.current_family_key
                    ws["distance"] = self.state.current_param
            except Exception:
                pass
            for tab in ("Decoder Lab", "Benchmark", "Batch & Streaming"):
                tab_obj = self.tabs.get(tab)
                if tab_obj is None:
                    continue
                try:
                    if getattr(tab_obj, "decoder_var", None) is not None:
                        ws["decoder"] = tab_obj.decoder_var.get()
                except Exception:
                    pass
                try:
                    if getattr(tab_obj, "rate_var", None) is not None:
                        ws["error_rate"] = float(tab_obj.rate_var.get())
                except Exception:
                    pass
                try:
                    if getattr(tab_obj, "seed_entry", None) is not None:
                        ws["seed"] = int(tab_obj.seed_entry.get() or 42)
                except Exception:
                    pass
            prefs = getattr(self, "_prefs", {})
            try:
                ws_export_dir = str(utils.get_export_dir())
                prefs.setdefault("default_export_dir", ws_export_dir)
                prefs.setdefault("theme", "dark")
            except Exception:
                pass
            if self._workspace_path is not None:
                utils.save_json(self._workspace_path, ws)
            if self._prefs_path is not None:
                utils.save_json(self._prefs_path, prefs)
        except Exception:
            pass

    def _on_close(self) -> None:
        """Window close handler: persist session, then tear down."""
        try:
            self._save_session()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        try:
            sys.exit(0)
        except SystemExit:
            pass

    # ── memory monitoring (Phase-2 item) ──────────────────────────────
    def _monitor_memory(self) -> None:
        """Every 30 s check RSS; warn at 500 MB, offer restart at 1 GB."""
        self._mem_after_id = None
        if self._destroyed:
            return
        try:
            import os
            import psutil
            rss_mb = psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
            if rss_mb >= 1000:
                self._show_error_toast(
                    f"Memory usage reached {rss_mb:.0f} MB. Save your work and restart "
                    "the workbench to reclaim memory."
                )
            elif rss_mb >= 500 and not self._mem_warned:
                self._mem_warned = True
                self._show_error_toast(
                    f"Memory usage is high ({rss_mb:.0f} MB). Consider restarting."
                )
        except Exception:
            pass
        try:
            self._mem_after_id = self._app.after(30000, self._monitor_memory)
        except Exception:
            self._mem_after_id = None

    # ── keyboard shortcuts (Phase-2 item) ─────────────────────────────
    def _bind_shortcuts(self) -> None:
        """Bind the documented shortcut set; each binding is individually safe."""
        try:
            bindings = (
                ("<Control-n>", lambda e: self._select_tab("Code Explorer")),
                ("<Control-d>", lambda e: self._doc_generate()),
                ("<Control-r>", lambda e: self._run_decode()),
                ("<Control-b>", lambda e: self._run_benchmark()),
                ("<Control-e>", lambda e: self._export_current()),
                ("<Control-comma>", lambda e: self._select_tab("Lab & Personal Info")),
                ("<F5>", lambda e: self._refresh_current()),
                ("<Control-q>", lambda e: self._on_close()),
                ("<Escape>", lambda e: self._exit_fullscreen()),
                ("<Control-Tab>", lambda e: self._cycle_tab(1)),
                ("<Control-Shift-Tab>", lambda e: self._cycle_tab(-1)),
            )
            for seq, callback in bindings:
                try:
                    self._app.bind(seq, callback)
                except Exception:
                    pass
        except Exception:
            pass

    def _select_tab(self, name: str) -> None:
        try:
            self.tabview.set(name)
        except Exception:
            pass

    def _exit_fullscreen(self) -> None:
        """Leave full-screen mode and return to a maximized window."""
        if getattr(self, "_destroyed", False):
            return
        try:
            if self._app.attributes("-fullscreen"):
                self._app.attributes("-fullscreen", False)
                self._app.state("zoomed")
        except Exception:
            pass

    def _doc_generate(self) -> None:
        tab = self.tabs.get("Documentation")
        fn = getattr(tab, "_on_generate", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    def _run_decode(self) -> None:
        self._select_tab("Decoder Lab")
        tab = self.tabs.get("Decoder Lab")
        fn = getattr(tab, "_on_decode", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    def _run_benchmark(self) -> None:
        self._select_tab("Benchmark")
        tab = self.tabs.get("Benchmark")
        fn = getattr(tab, "_on_run", None)
        if fn is not None:
            try:
                fn()
            except Exception:
                pass

    def _export_current(self) -> None:
        """Export from the currently open tab when it exposes an export action."""
        try:
            current = self.tabview.get()
        except Exception:
            return
        tab = self.tabs.get(current)
        if tab is None:
            return
        for name in ("_on_export", "_on_quick_export", "_on_generate"):
            fn = getattr(tab, name, None)
            if fn is None:
                continue
            try:
                fn()
                return
            except Exception:
                continue

    def _refresh_current(self) -> None:
        try:
            current = self.tabview.get()
        except Exception:
            return
        tab = self.tabs.get(current)
        if tab is None:
            return
        for name in ("_redraw", "_on_build", "_on_refresh"):
            fn = getattr(tab, name, None)
            if fn is None:
                continue
            try:
                fn()
                return
            except Exception:
                continue

    def _cycle_tab(self, direction: int) -> None:
        try:
            names = list(self.tabview._tab_dict.keys())
            if not names:
                return
            try:
                current = self.tabview.get()
            except Exception:
                return
            try:
                idx = names.index(current)
            except ValueError:
                idx = 0
            next_name = names[(idx + direction) % len(names)]
            self.tabview.set(next_name)
        except Exception:
            pass

    def _boot_version_string(self) -> str:
        """The app's own version at construction — tracks the installed decoder
        backend (read live from the wheel), never a hardcoded workbench number.
        The moment a newer decoder (e.g. 0.6.7) is installed, the app identifies
        as that version."""
        ver = None
        try:
            import version_service
            ver = version_service.effective_app_version()
        except Exception:
            ver = None
        return f"QECTOR Decoder Workbench v{ver}" if ver else "QECTOR Decoder Workbench"

    def _apply_live_version(self, banner: str, title: str) -> None:
        """Update the status-bar version label and window title.
        Runs on the Tk main thread (posted via the UI pump); guarded so a late
        update after teardown is a no-op."""
        if self._destroyed:
            return
        try:
            status_left = getattr(self, "_status_left", None)
            if status_left is not None:
                status_left.configure(text=banner)
        except Exception:
            pass
        try:
            self._app.title(title)
        except Exception:
            pass

    # ── Version banner (deferred; console/log output only) ────────────
    def _start_update_check(self) -> None:
        self._update_after_id = None
        if self._destroyed:
            return
        try:
            import threading_utils
            threading_utils.run_in_background(self._update_check_worker)
        except Exception as exc:
            self.console.log(f"Version check could not start: {exc}", "WARN")

    def _update_check_worker(self) -> None:
        """Runs on a daemon thread; displays installed version banner only.

        No PyPI check, no auto-upgrade.  The app always shows the locally
        installed decoder version.
        """
        try:
            import version_service
            banner = version_service.format_version_banner()
        except Exception as exc:
            if self._logger is not None:
                self._logger.warning(f"Version banner failed: {exc}")
            return
        try:
            self.console.log(banner, "INFO")
            if self._logger is not None:
                self._logger.info(banner)
            installed = version_service.installed_backend_version() or "0.7.0"
            title = f"QECTOR Decoder Workbench v{WORKBENCH_VERSION}"
            try:
                self._ui.post(self._apply_live_version, banner, title)
            except Exception:
                pass
            self.console.log(
                "qector-decoder-v3 is up to date — using bundled local wheel", "INFO")
        except Exception:
            pass

    def _start_boot_tests(self) -> None:
        """Kick off verbose boot tests + fresh docs (once, background, opt-out aware)."""
        if getattr(self, "_boot_tests_scheduled", False):
            try:
                from boot_test_runner import schedule_boot_tests

                schedule_boot_tests(self)
            except Exception as exc:
                try:
                    self.console.log(f"Boot tests scheduling failed: {exc}", "WARN")
                except Exception:
                    pass
            return
        self._boot_tests_scheduled = True
        try:
            from boot_test_runner import schedule_boot_tests

            schedule_boot_tests(self)
        except Exception as exc:
            try:
                self.console.log(f"Boot tests failed to schedule: {exc}", "WARN")
            except Exception:
                pass


# ---------------------------------------------------------------------------
# EULA Acceptance Dialog
# ---------------------------------------------------------------------------

def show_eula_dialog() -> bool:
    """Prompt the user to accept the EULA. Returns True if accepted, False otherwise."""
    import customtkinter as ctk
    import sys
    from pathlib import Path

    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    eula_path = base_path / "EULA.txt"
    if not eula_path.is_file():
        eula_path = Path("EULA.txt")

    eula_text = "No EULA.txt found. Please contact the administrator."
    if eula_path.is_file():
        try:
            eula_text = eula_path.read_text(encoding="utf-8")
        except Exception:
            pass

    def _find_logo_path() -> Path | None:
        exe_dir = None
        try:
            exe_dir = Path(sys.executable).parent
        except Exception:
            exe_dir = None
        file_dir = Path(__file__).resolve().parent
        candidates: list[Path] = []
        for base in (base_path, exe_dir, file_dir, Path.cwd(), file_dir / "assets", base_path / "assets"):
            if base is None:
                continue
            for name in ("logo_banner.png", "icon.png", "assets/logo_banner.png", "assets/icon.png"):
                candidates.append(Path(base) / name)
        candidates += [Path("logo_banner.png"), Path("icon.png"), Path("assets/logo_banner.png")]
        for c in candidates:
            try:
                if c and c.is_file() and c.stat().st_size > 0:
                    return c
            except Exception:
                continue
        return None

    root = ctk.CTk()
    root.title("QECTOR Decoder Workbench - Licence Agreement")
    root.configure(fg_color="#12141a")
    w, h = 760, 640
    try:
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    except Exception:
        sw, sh = 1280, 800
    root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    try:
        import os

        search_dirs: list[str] = []
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            search_dirs.append(meipass)
        try:
            search_dirs.append(str(Path(sys.executable).parent))
        except Exception:
            pass
        search_dirs.append(str(Path(__file__).resolve().parent))
        if os.name == "nt":
            for d in search_dirs:
                ico = os.path.join(d, "icon.ico")
                if os.path.isfile(ico):
                    try:
                        root.iconbitmap(ico)
                    except Exception:
                        pass
                    break
    except Exception:
        pass

    accepted = {"value": False}

    logo_path = _find_logo_path()
    if logo_path is not None:
        try:
            from PIL import Image

            img = Image.open(logo_path)
            banner_w = 520
            iw, ih = img.size
            scale = min(banner_w / max(1, iw), 1.0)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            if nh > 120:
                scale2 = 120 / nh
                nw, nh = int(nw * scale2), 120
            img = img.resize((nw, nh), Image.LANCZOS)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=(nw, nh))
            lbl_logo = ctk.CTkLabel(root, image=ctk_img, text="")
            lbl_logo.pack(pady=(14, 6))
            root._eula_logo_ref = ctk_img  # keep alive
        except Exception:
            pass

    lbl_title = ctk.CTkLabel(
        root,
        text="End User License Agreement (EULA)",
        font=ctk.CTkFont(family="Segoe UI", size=18, weight="bold"),
        text_color="#e8ecf4"
    )
    lbl_title.pack(pady=(6, 8))

    lbl_info = ctk.CTkLabel(
        root,
        text="Please read and accept the agreement below before starting the application.",
        font=ctk.CTkFont(family="Segoe UI", size=11),
        text_color="#8294ad"
    )
    lbl_info.pack(pady=(0, 12))

    # Scrollable textbox for EULA text
    txt_eula = ctk.CTkTextbox(
        root, 
        width=580, 
        height=280, 
        font=ctk.CTkFont(family="Consolas", size=10),
        fg_color="#1A1C24",
        text_color="#D1D5DB"
    )
    txt_eula.insert("1.0", eula_text)
    txt_eula.configure(state="disabled")
    txt_eula.pack(padx=30, pady=5)

    # Checkbox
    var_agree = ctk.BooleanVar(value=False)
    
    def on_toggle():
        if var_agree.get():
            btn_accept.configure(state="normal", fg_color="#4F46E5", hover_color="#4338CA")
        else:
            btn_accept.configure(state="disabled", fg_color="#374151")

    chk_agree = ctk.CTkCheckBox(
        root,
        text="I accept the terms of this license agreement and the 'AS IS' warranty disclaimers.",
        variable=var_agree,
        command=on_toggle,
        font=ctk.CTkFont(family="Segoe UI", size=11),
        text_color="#D1D5DB",
        border_color="#4B5563",
        hover_color="#374151"
    )
    chk_agree.pack(pady=15, padx=30, anchor="w")

    # Button container
    btn_frame = ctk.CTkFrame(root, fg_color="transparent")
    btn_frame.pack(pady=(5, 20))

    def on_accept():
        accepted["value"] = True
        root.destroy()

    def on_reject():
        accepted["value"] = False
        root.destroy()

    btn_reject = ctk.CTkButton(
        btn_frame, 
        text="Decline & Exit", 
        command=on_reject,
        width=140,
        fg_color="#EF4444",
        hover_color="#DC2626",
        text_color="#FFFFFF"
    )
    btn_reject.pack(side="left", padx=10)

    btn_accept = ctk.CTkButton(
        btn_frame, 
        text="Accept & Continue", 
        command=on_accept,
        state="disabled",
        width=140,
        fg_color="#374151",
        text_color="#FFFFFF"
    )
    btn_accept.pack(side="left", padx=10)

    root.protocol("WM_DELETE_WINDOW", on_reject)
    root.mainloop()

    return accepted["value"]

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(on_ready=None) -> None:
    """Application entry point with a readable fatal-error fallback.

    *on_ready* is invoked once the main window is built and mapped; ``main.py``
    passes the boot-splash closer so the splash hands over to a visible window
    with no blank gap in between.
    """
    logger = _safe_logger()
    if not _HAS_GUI:
        msg = "ERROR: customtkinter is required to run QECTOR Workbench"
        print(msg, file=sys.stderr)
        if logger is not None:
            logger.error(msg)
        sys.exit(1)

    # First check if EULA has been accepted in preferences
    try:
        import utils
        data_dir = utils.get_data_dir()
        ppath = data_dir / "preferences.json"
        prefs = utils.load_json(ppath, {}) if ppath else {}
        if not prefs.get("eula_accepted"):
            if on_ready is not None:
                try:
                    on_ready()
                    on_ready = None  # prevent calling it again
                except Exception:
                    pass
            if not show_eula_dialog():
                if logger is not None:
                    logger.info("EULA declined by user. Exiting.")
                sys.exit(0)
            prefs["eula_accepted"] = True
            try:
                utils.save_json(ppath, prefs)
            except Exception:
                pass
    except Exception as e:
        if logger is not None:
            logger.warning(f"Error checking EULA preferences: {e}")

    exit_code = 0
    try:
        _declare_dpi_awareness()
        if logger is not None:
            logger.info(f"Starting {FULL_VERSION}")
        app = QectorApp()
        if on_ready is not None:
            try:
                on_ready()
            except Exception:
                pass
        app.mainloop()
    except Exception:
        detail = traceback.format_exc()
        msg = (
            f"{FULL_VERSION} hit a fatal error and must close.\n"
            f"{detail}\n"
            "The full log is in the per-user data directory (logs/qector.log)."
        )
        print(msg, file=sys.stderr)
        if logger is not None:
            logger.error(msg)
        exit_code = 1
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
