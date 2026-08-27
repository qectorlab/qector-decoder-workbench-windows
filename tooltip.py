"""tooltip.py  -  lightweight hover tooltips for QECTOR Workbench widgets.

CustomTkinter has no built-in tooltip and Tk exposes no real accessibility
tree, so a hover tooltip is the pragmatic stand-in for a screen-reader label
on icon-only or emoji-prefixed controls (Generate Doc, Import Syndrome,
Compare Decoders, ...). ``attach`` is the only entry point most callers need;
it is a no-op on any failure so a tooltip can never take a tab down.
"""

from __future__ import annotations

class _Tooltip:
    """One borderless popup window that follows a single widget's hover state."""

    _DELAY_MS = 500

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self._after_id = None
        self._win = None
        try:
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
            widget.bind("<ButtonPress>", self._on_leave, add="+")
            widget.bind("<Destroy>", self._on_leave, add="+")
        except Exception:
            pass

    def _on_enter(self, _event=None) -> None:
        self._cancel()
        try:
            self._after_id = self.widget.after(self._DELAY_MS, self._show)
        except Exception:
            self._after_id = None

    def _on_leave(self, _event=None) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self) -> None:
        if self._win is not None:
            return
        try:
            import tkinter
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
            win = tkinter.Toplevel(self.widget)
            win.wm_overrideredirect(True)
            try:
                win.wm_attributes("-topmost", True)
            except Exception:
                pass
            win.wm_geometry(f"+{x}+{y}")
            label = tkinter.Label(
                win, text=self.text, justify="left",
                background="#1f1f1f", foreground="#dcdcdc",
                relief="solid", borderwidth=1,
                font=("Segoe UI", 9), padx=6, pady=3,
            )
            label.pack()
            self._win = win
        except Exception:
            self._win = None

    def _hide(self) -> None:
        win, self._win = self._win, None
        if win is not None:
            try:
                win.destroy()
            except Exception:
                pass


def attach(widget, text: str) -> None:
    """Attach a hover tooltip showing *text* to *widget*. Never raises."""
    if not text:
        return
    try:
        _Tooltip(widget, text)
    except Exception:
        pass
