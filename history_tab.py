"""history_tab.py  -  global experiment history for QECTOR Workbench.

Every decode, decoder comparison, benchmark, batch and streaming run records
one compact JSON event via :func:`record_event`, regardless of which tab
produced it. This tab is the read side: a scrollable, filterable,
newest-first list with a per-row "Use" (jump to the originating tab and
restore its inputs) and a whole-history JSON/CSV export. Recording is
best-effort and never raises; a lost history entry is a UX inconvenience,
never a reason to break the run that produced it.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False

import theme
import threading_utils

_MAX_FILE_LINES = 5000
_TRIM_TO = 3000
_KINDS = ("decode", "compare", "benchmark", "batch", "streaming")


def _history_path() -> Path:
    import utils
    return utils.get_data_dir() / "history.jsonl"


def record_event(kind: str, data: dict[str, Any]) -> None:
    """Append one history event. Best-effort: never raises, never checked."""
    if kind not in _KINDS:
        kind = "decode"
    event: dict[str, Any] = {"ts": time.time(), "kind": kind}
    for key, value in data.items():
        try:
            json.dumps(value)
        except Exception:
            value = str(value)
        event[key] = value
    try:
        path = _history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, default=str) + "\n")
        _maybe_trim(path)
    except Exception:
        pass


def _maybe_trim(path: Path) -> None:
    """Bound the history file: once it exceeds _MAX_FILE_LINES, rewrite it
    down to the newest _TRIM_TO lines. Best-effort; failures are silent."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_FILE_LINES:
            path.write_text("\n".join(lines[-_TRIM_TO:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def load_events(limit: int = 500) -> list[dict[str, Any]]:
    """Return the most recent *limit* events, newest first. Never raises."""
    try:
        path = _history_path()
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        events = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue
        events.reverse()
        return events
    except Exception:
        return []


def clear_events() -> bool:
    try:
        path = _history_path()
        if path.exists():
            path.unlink()
        return True
    except Exception:
        return False


if _HAS_GUI:

    _KIND_LABELS = {
        "decode": "Decode", "compare": "Compare", "benchmark": "Benchmark",
        "batch": "Batch", "streaming": "Streaming",
    }
    _KIND_ORIGIN_TAB = {
        "decode": "Decoder Lab", "compare": "Decoder Lab",
        "benchmark": "Benchmark", "batch": "Batch & Streaming",
        "streaming": "Batch & Streaming",
    }

    class HistoryTab(ctk.CTkFrame):
        """Experiment history: aggregated, persisted, browsable, reusable."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()
            self._ui = threading_utils.UiPump(self)
            self._events: list[dict[str, Any]] = []
            self._filter = "All"

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(2, weight=1)

            header = ctk.CTkFrame(self, fg_color="transparent")
            header.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
            self._heading = ctk.CTkLabel(
                header, text="History",
                font=ctk.CTkFont(size=18, weight="bold"),
            )
            self._heading.pack(anchor="w")
            ctk.CTkLabel(
                header,
                text="Every decode, comparison, benchmark, batch and streaming run  -  this session and past ones.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
            ).pack(anchor="w", pady=(0, 4))

            toolbar = ctk.CTkFrame(self, fg_color="transparent")
            toolbar.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 6))
            self.filter_seg = ctk.CTkSegmentedButton(
                toolbar,
                values=["All", "Decode", "Compare", "Benchmark", "Batch", "Streaming"],
                command=self._on_filter_change, font=ctk.CTkFont(size=11),
            )
            self.filter_seg.set("All")
            self.filter_seg.pack(side="left")
            ctk.CTkButton(
                toolbar, text="Refresh", width=90, command=self.refresh,
                font=ctk.CTkFont(size=11),
            ).pack(side="right", padx=(6, 0))
            ctk.CTkButton(
                toolbar, text="Export...", width=90, command=self._on_export,
                font=ctk.CTkFont(size=11),
            ).pack(side="right", padx=(6, 0))
            ctk.CTkButton(
                toolbar, text="Clear History", width=110, command=self._on_clear,
                font=ctk.CTkFont(size=11), fg_color=theme.c("error"),
                hover_color=theme.c("border_toast"),
            ).pack(side="right", padx=(6, 0))

            self.list_frame = ctk.CTkScrollableFrame(self, fg_color=theme.c("bg_panel"))
            self.list_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
            self.list_frame.grid_columnconfigure(0, weight=1)

            self.status_label = ctk.CTkLabel(
                self, text="", font=ctk.CTkFont(size=10),
                text_color=theme.c("text_secondary"),
            )
            self.status_label.grid(row=3, column=0, sticky="w", padx=12, pady=(0, 6))

            self.refresh()

        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        # ── loading / rendering ─────────────────────────────────────
        def refresh(self) -> None:
            threading_utils.run_in_background(self._load_worker)

        def _load_worker(self) -> None:
            events = load_events(limit=500)
            self._ui.post(self._on_loaded, events)

        def _on_loaded(self, events: list[dict[str, Any]]) -> None:
            self._events = events
            self._render()

        def _on_filter_change(self, choice: str) -> None:
            self._filter = choice
            self._render()

        def _render(self) -> None:
            try:
                for child in list(self.list_frame.winfo_children()):
                    child.destroy()
            except Exception:
                return
            wanted = self._filter.lower()
            rows = [e for e in self._events if wanted == "all" or e.get("kind") == wanted]
            if not rows:
                ctk.CTkLabel(
                    self.list_frame,
                    text="No history yet  -  run a decode, benchmark, or batch job.",
                    text_color=theme.c("text_secondary"), font=ctk.CTkFont(size=11),
                ).grid(row=0, column=0, sticky="w", padx=10, pady=10)
            for i, event in enumerate(rows):
                self._build_row(i, event)
            self.status_label.configure(
                text=f"{len(rows)} of {len(self._events)} event(s) shown."
            )

        def _build_row(self, row: int, event: dict[str, Any]) -> None:
            frame = ctk.CTkFrame(self.list_frame, fg_color=theme.c("bg_panel_alt"))
            frame.grid(row=row, column=0, sticky="ew", padx=4, pady=3)
            frame.grid_columnconfigure(1, weight=1)

            kind = str(event.get("kind", "?"))
            label = _KIND_LABELS.get(kind, kind)
            ts = event.get("ts")
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "?"

            ctk.CTkLabel(
                frame, text=label, width=90, anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=theme.c("accent"),
            ).grid(row=0, column=0, sticky="w", padx=(10, 4), pady=6)

            summary = self._summarize(kind, event)
            ctk.CTkLabel(
                frame, text=f"{when}   {summary}", anchor="w",
                font=ctk.CTkFont(family=self.fonts.mono, size=10),
                text_color=theme.c("text_primary"),
            ).grid(row=0, column=1, sticky="ew", padx=4, pady=6)

            ctk.CTkButton(
                frame, text="Use", width=56, height=24,
                command=lambda ev=event: self._on_use(ev),
                font=ctk.CTkFont(size=10),
            ).grid(row=0, column=2, sticky="e", padx=(4, 10), pady=6)

        @staticmethod
        def _summarize(kind: str, e: dict[str, Any]) -> str:
            if kind in ("decode", "compare"):
                bits = [str(e.get("decoder", e.get("decoders", "?")))]
                if "rate" in e:
                    bits.append(f"p={e['rate']}")
                if "seed" in e:
                    bits.append(f"seed={e['seed']}")
                if "hw" in e:
                    bits.append(f"hw={e['hw']}")
                if e.get("syn_valid") is not None:
                    bits.append("syn=ok" if e["syn_valid"] else "syn=BAD")
                return "  ".join(str(b) for b in bits)
            if kind == "benchmark":
                return (f"{e.get('family', '?')} d={e.get('distance', '?')}  "
                        f"{e.get('decoder', '?')}  {e.get('throughput', '?')} dec/s")
            if kind == "batch":
                return (f"n={e.get('n', '?')}  backend={e.get('backend', '?')}  "
                        f"success={e.get('success_rate', '?')}")
            if kind == "streaming":
                return (f"rounds={e.get('rounds', '?')}  decoder={e.get('decoder', '?')}  "
                        f"window={e.get('window', '?')}")
            return json.dumps({k: v for k, v in e.items() if k not in ("kind", "ts")})[:100]

        def _on_export(self) -> None:
            import tkinter.filedialog as fd
            import csv
            import utils

            if not self._events:
                self._log("No history events to export.", "WARN")
                return

            filetypes = [
                ("JSON File", "*.json"),
                ("CSV File", "*.csv"),
                ("All Files", "*.*")
            ]
            try:
                filepath = fd.asksaveasfilename(
                    title="Export History",
                    defaultextension=".json",
                    filetypes=filetypes
                )
                if not filepath:
                    return
                ok_path, filepath = utils.sanitize_export_path(filepath)
                if not ok_path:
                    self._log("Export rejected: directory traversal is not allowed.", "ERROR")
                    return

                path = Path(filepath)
                if path.suffix.lower() == ".csv":
                    with path.open("w", newline="", encoding="utf-8") as f:
                        keys = set()
                        for ev in self._events:
                            keys.update(ev.keys())
                        ordered_keys = ["ts", "kind"] + sorted(list(keys - {"ts", "kind"}))
                        writer = csv.DictWriter(f, fieldnames=ordered_keys)
                        writer.writeheader()
                        for ev in self._events:
                            writer.writerow(ev)
                else:
                    path.write_text(json.dumps(self._events, indent=2, default=str), encoding="utf-8")

                self._log(f"Exported history to {path.name}.", "INFO")
            except Exception as e:
                self._log(f"Export failed: {e}", "ERROR")

        def _on_clear(self) -> None:
            import tkinter.messagebox as mb
            try:
                if mb.askyesno("Clear History", "Are you sure you want to permanently clear all experiment history?"):
                    if clear_events():
                        self._log("History cleared.", "INFO")
                        self.refresh()
            except Exception as e:
                self._log(f"Failed to clear history: {e}", "ERROR")

        # ── "Use": jump to the originating tab and restore its inputs ──
        def _on_use(self, event: dict[str, Any]) -> None:
            kind = str(event.get("kind", ""))
            target_name = _KIND_ORIGIN_TAB.get(kind)
            if not target_name:
                self._log("No originating tab recorded for this event.", "WARN")
                return
            controller = self._controller()
            if controller is None:
                self._log("Could not reach the application to switch tabs.", "WARN")
                return
            try:
                controller.tabview.set(target_name)
            except Exception:
                pass
            target = controller.tabs.get(target_name)
            if target is None:
                return
            self._restore_fields(target, event)
            self._log(f"Restored {kind} parameters into {target_name}.", "INFO")

        def _controller(self):
            try:
                root = self.winfo_toplevel()
                return getattr(root, "qector_app", None)
            except Exception:
                return None

        @staticmethod
        def _restore_fields(tab, event: dict[str, Any]) -> None:
            """Best-effort restore, reusing the same attribute names app.py's
            own session-restore already relies on (family_var, distance_var,
            decoder_var, rate_var, seed_entry)."""
            try:
                if "family" in event and getattr(tab, "family_var", None) is not None:
                    tab.family_var.set(event["family"])
            except Exception:
                pass
            try:
                if "distance" in event and getattr(tab, "distance_var", None) is not None:
                    tab.distance_var.set(int(event["distance"]))
            except Exception:
                pass
            try:
                decoder = event.get("decoder")
                if decoder and getattr(tab, "decoder_var", None) is not None:
                    tab.decoder_var.set(decoder)
            except Exception:
                pass
            try:
                if "rate" in event and getattr(tab, "rate_var", None) is not None:
                    tab.rate_var.set(float(event["rate"]))
            except Exception:
                pass
            try:
                if "seed" in event and getattr(tab, "seed_entry", None) is not None:
                    tab.seed_entry.delete(0, "end")
                    tab.seed_entry.insert(0, str(event["seed"]))
            except Exception:
                pass
