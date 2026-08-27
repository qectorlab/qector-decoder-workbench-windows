"""dialogs.py  -  Dialog windows for QECTOR Workbench."""

from __future__ import annotations

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False


class QectorDialog:
    """Base dialog class for QECTOR Workbench."""

    def __init__(self, title: str = "QECTOR"):
        self.title = title


if _HAS_GUI:
    class ErrorDialog(ctk.CTkToplevel):
        def __init__(self, master, message: str, **kwargs):
            super().__init__(master, **kwargs)
            self.title("Error")
            self.label = ctk.CTkLabel(self, text=message, wraplength=400)
            self.label.pack(pady=20, padx=20)
            self.btn = ctk.CTkButton(self, text="OK", command=self.destroy)
            self.btn.pack(pady=10)
else:
    class ErrorDialog:
        def __init__(self, master=None, message: str = "", **kwargs):
            pass
