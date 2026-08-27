"""documentation_tab.py: in-app Documentation tab.

Publication-grade documentation viewer and multi-format exporter (Markdown,
JSON, HTML, LaTeX, PDF, SVG, plus the Zenodo deposit sidecars) with
provenance, code analysis and decoder benchmarking.  Generation runs in a
background thread so the GUI stays responsive; every failure path is surfaced
in the preview pane and console instead of raising.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False

import backend as be
from doc_generator import ProfessionalDocGenerator
from theme import COLORS, get_fonts
from threading_utils import run_in_background

#: Where the "recent export folders" list is persisted between sessions.
_RECENT_PATH = Path.home() / ".qector" / "recent_export_dirs.json"
#: How many folders to remember.
_RECENT_LIMIT = 8


def _load_recent_dirs() -> list[str]:
    """Load the recent export folders, newest first.  Never raises."""
    try:
        if _RECENT_PATH.exists():
            data = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(d) for d in data if isinstance(d, str)][:_RECENT_LIMIT]
    except Exception:
        pass
    return []


def _save_recent_dirs(dirs: list[str]) -> None:
    """Persist the recent export folders.  Never raises."""
    try:
        _RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _RECENT_PATH.write_text(
            json.dumps(dirs[:_RECENT_LIMIT], indent=2), encoding="utf-8"
        )
    except Exception:
        pass


if _HAS_GUI:

    class DocumentationTab(ctk.CTkFrame):
        """Professional documentation viewer and exporter."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else get_fonts()
            self.generator = ProfessionalDocGenerator()
            self._generating = False

            # Preview state: zoom level, search hits, and the folders this
            # session has exported into (most recent first).
            self._preview_font_size = 11
            self._search_hits: list[int] = []
            self._search_index = -1
            self._recent_dirs: list[str] = _load_recent_dirs()
            #: set by "Generate and Open Folder" so the folder opens only once
            #: generation has actually finished writing.
            self._open_folder_when_done = False

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(0, weight=1)

            self._build_ui()

        # ------------------------------------------------------------------
        # UI construction
        # ------------------------------------------------------------------
        def _build_ui(self) -> None:
            scroll = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_panel"], corner_radius=10)
            scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)

            ctk.CTkLabel(
                scroll, text="Documentation Studio",
                font=ctk.CTkFont(family=self.fonts.heading, size=18, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(anchor="w", padx=18, pady=(18, 4))

            ctk.CTkLabel(
                scroll,
                text="Publication-grade scientific export studio with lab metadata, provenance, and decoder benchmarking.",
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
                text_color=COLORS["text_secondary"],
            ).pack(anchor="w", padx=18, pady=(0, 14))

            # Prominent Action Bar (Top)
            self._build_top_action_bar(scroll)
            # Format & Template selector
            self._build_format_section(scroll)
            # Official release-docs export
            self._build_official_export_section(scroll)
            # Preview with Search & Toolbar
            self._build_preview_section(scroll)
            # Developer and licensing details (offline-only; no external actions)
            self._build_licence_section(scroll)

        def _build_top_action_bar(self, scroll) -> None:
            bar = ctk.CTkFrame(scroll, fg_color=COLORS["bg_panel_alt"], corner_radius=10)
            bar.pack(fill="x", padx=18, pady=(0, 14))

            self.generate_btn = ctk.CTkButton(
                bar, text="📄 Generate Documentation", command=self._on_generate,
                fg_color=COLORS["accent"], hover_color=COLORS["accent_dim"], text_color="#ffffff",
                font=ctk.CTkFont(size=13, weight="bold"), height=36, width=200,
            )
            self.generate_btn.pack(side="left", padx=14, pady=12)

            self.gen_export_btn = ctk.CTkButton(
                bar, text="⚡ Generate & Open Folder", command=self._on_generate_and_open,
                fg_color=COLORS["accent_dim"], hover_color=COLORS["accent"], text_color="#ffffff",
                font=ctk.CTkFont(size=12, weight="bold"), height=36, width=190,
            )
            self.gen_export_btn.pack(side="left", padx=(0, 14), pady=12)

            self.status_progress_label = ctk.CTkLabel(
                bar, text="Ready", font=ctk.CTkFont(size=11), text_color=COLORS["text_secondary"]
            )
            self.status_progress_label.pack(side="right", padx=14, pady=12)

            self.recent_var = ctk.StringVar(value="Recent folders")
            self.recent_menu = ctk.CTkOptionMenu(
                bar, values=self._recent_menu_values(), variable=self.recent_var,
                command=self._on_pick_recent, width=150,
                font=ctk.CTkFont(size=11),
                fg_color=COLORS["bg_widget"], button_color=COLORS["bg_widget"],
            )
            self.recent_menu.pack(side="right", padx=(0, 6), pady=12)

        # ------------------------------------------------------------------
        # Progress reporting
        # ------------------------------------------------------------------
        def _set_progress(self, text: str, tone: str = "text_secondary") -> None:
            """Update the tab's progress readout and the app status bar."""
            try:
                self.status_progress_label.configure(
                    text=text, text_color=COLORS.get(tone, COLORS["text_secondary"])
                )
            except Exception:
                pass
            # Mirror into the app status bar when the host app exposes one, so
            # progress is visible from any tab.
            try:
                app = self.winfo_toplevel()
                setter = getattr(app, "set_status", None) or getattr(app, "_set_status", None)
                if callable(setter):
                    setter(text)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Recent export folders
        # ------------------------------------------------------------------
        def _recent_menu_values(self) -> list[str]:
            return self._recent_dirs[:] if self._recent_dirs else ["(no recent folders)"]

        def _remember_dir(self, path: str) -> None:
            """Record ``path`` as the most recent export folder."""
            if not path:
                return
            path = str(path)
            self._recent_dirs = [path] + [d for d in self._recent_dirs if d != path]
            self._recent_dirs = self._recent_dirs[:_RECENT_LIMIT]
            _save_recent_dirs(self._recent_dirs)
            try:
                self.recent_menu.configure(values=self._recent_menu_values())
                self.recent_var.set("Recent folders")
            except Exception:
                pass

        def _on_pick_recent(self, choice: str) -> None:
            """Open a previously used export folder."""
            self.recent_var.set("Recent folders")
            if not choice or choice.startswith("("):
                return
            self._open_path(choice)

        def _on_generate_and_open(self) -> None:
            """Generate, then open the folder once the files actually exist.

            Generation runs on a worker thread, so opening the folder inline
            would show an empty (or stale) directory.  The request is recorded
            and honoured by :meth:`_on_generate_done`.
            """
            self._open_folder_when_done = True
            self._on_generate()
            if not self._generating:
                # Generation was refused (no active code, no format selected) or
                # failed to start; do not open a folder for work that never ran.
                self._open_folder_when_done = False

        def _build_official_export_section(self, scroll) -> None:
            """Robust export of the official public documentation set.

            Generates the exact documents shipped with every release (user
            manuals, quick start, MCP guide, LLM manual, README, manuals.zip
            and the API reference) into the per-user export directory.  Runs
            in a background thread; every artifact is reported in the preview
            pane and the console, and no failure can take the GUI down.
            """
            section = ctk.CTkFrame(scroll, fg_color=COLORS["bg_panel_alt"], corner_radius=10)
            section.pack(fill="x", padx=18, pady=(0, 14))
            section.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                section, text="Official Docs Export",
                font=ctk.CTkFont(family=self.fonts.heading, size=12, weight="bold"),
                text_color=COLORS["accent"],
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(14, 2))

            ctk.CTkLabel(
                section,
                text=(
                    "Regenerates the full public documentation set shipped with each "
                    "release - user manuals (Windows/Linux/macOS), quick-start, MCP "
                    "integration guide, LLM manual, README, manuals.zip and the complete "
                    "API reference - directly from the live application, fully offline."
                ),
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
                text_color=COLORS["text_secondary"], wraplength=640, justify="left",
            ).grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))

            import utils

            try:
                from docs_exporter import default_docs_dir
                self._official_dir = str(default_docs_dir().resolve())
            except Exception:
                try:
                    self._official_dir = str(utils.get_export_dir().resolve())
                except Exception:
                    self._official_dir = ""
            self._official_dir_var = ctk.StringVar(value=self._official_dir)

            ctk.CTkLabel(
                section, text="Export to:",
                font=ctk.CTkFont(family=self.fonts.ui, size=11, weight="bold"),
                text_color=COLORS["text_secondary"],
            ).grid(row=2, column=0, sticky="w", padx=(14, 8), pady=1)

            self._official_entry = ctk.CTkEntry(
                section, textvariable=self._official_dir_var,
                font=ctk.CTkFont(family=self.fonts.mono, size=11),
                fg_color=COLORS["bg_widget"], border_color=COLORS["accent_dim"],
            )
            self._official_entry.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=1)

            buttons = ctk.CTkFrame(section, fg_color="transparent")
            buttons.grid(row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 14))

            self.official_btn = ctk.CTkButton(
                buttons, text="Export Official Docs", command=self._on_export_official,
                fg_color=COLORS["accent_dim"], hover_color=COLORS["accent"],
                text_color=COLORS["text_primary"],
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self.official_btn.pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                buttons, text="Browse...", width=110,
                command=self._on_browse_official_dir,
                fg_color=COLORS["bg_widget"], hover_color=COLORS["accent_dim"],
                text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=8)

            ctk.CTkButton(
                buttons, text="Open Folder", width=110,
                command=self._on_open_official_dir,
                fg_color=COLORS["bg_widget"], hover_color=COLORS["accent_dim"],
                text_color=COLORS["text_secondary"], font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=8)

        def _on_browse_official_dir(self) -> None:
            """Pick an export folder with a native dialog (fallback: no-op)."""
            try:
                import tkinter.filedialog as fd
                chosen = fd.askdirectory(
                    title="Choose export folder for the official docs",
                    initialdir=self._official_dir_var.get() or None,
                )
                if chosen:
                    self._official_dir_var.set(chosen)
            except Exception as exc:
                self._log(f"Folder picker unavailable: {exc}", "WARN")

        def _on_open_official_dir(self) -> None:
            """Open the configured export folder in the platform file manager."""
            target = self._official_dir_var.get() or self._official_dir
            try:
                os.makedirs(target, exist_ok=True)
                if hasattr(os, "startfile"):
                    os.startfile(target)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    subprocess.Popen(["xdg-open", target])
                self._log(f"Official docs folder: {target}", "INFO")
            except FileNotFoundError:
                self._log(f"Official docs folder: {target}", "INFO")
            except Exception as exc:
                self._log(f"Could not open official docs folder: {exc}", "ERROR")

        def _on_export_official(self) -> None:
            if getattr(self, "_exporting_official", False):
                return
            target = (self._official_dir_var.get() or self._official_dir or "").strip()
            if not target:
                self._log("Choose an export folder first.", "WARN")
                self._set_preview("Choose an export folder first.")
                return
            self._exporting_official = True
            self._set_official_export_enabled(False)
            self._set_preview(f"Exporting official docs to:\n{target}\n\nGenerating...")
            self._log(f"Official docs export started -> {target}", "INFO")
            try:
                run_in_background(self._official_export_worker, args=(target,))
            except Exception as exc:
                self._exporting_official = False
                self._set_official_export_enabled(True)
                self._set_preview(f"Export failed to start: {exc}")
                self._log(f"Official docs export failed to start: {exc}", "ERROR")

        def _set_official_export_enabled(self, enabled: bool) -> None:
            try:
                self.official_btn.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

        def _official_export_worker(self, target: str) -> None:
            """Background thread: run the official export, marshal results."""
            try:
                from docs_exporter import export_public_docs

                report = export_public_docs(
                    target,
                    on_log=lambda msg: self._marshal(lambda m=msg: self._log(m, "INFO")),
                )
            except Exception as exc:
                self._marshal(lambda exc=exc: self._on_official_export_error(exc))
                return
            self._marshal(lambda report=report: self._on_official_export_done(report))

        def _on_official_export_error(self, exc: Exception) -> None:
            try:
                self._set_preview(f"Official docs export failed: {exc}")
                self._log(f"Official docs export failed: {exc}", "ERROR")
            finally:
                self._exporting_official = False
                self._set_official_export_enabled(True)

        def _on_official_export_done(self, report: dict) -> None:
            try:
                summary = report.get("_summary", {})
                lines = ["Official docs export result:", ""]
                for name, info in report.items():
                    if name == "_summary":
                        continue
                    if info.get("ok"):
                        lines.append(f"  [OK]   {name}")
                    else:
                        lines.append(f"  [FAIL] {name}: {info.get('error')}")
                lines.append("")
                lines.append(
                    f"Total: {summary.get('artifacts_ok', 0)}/{summary.get('artifacts_total', 0)} "
                    f"artifacts in {summary.get('elapsed_s', '?')}s"
                )
                if not summary.get("ok"):
                    lines.append(f"\nErrors:\n{summary.get('error')}")
                self._set_preview("\n".join(lines))
                for name, info in report.items():
                    if name == "_summary":
                        continue
                    if info.get("ok"):
                        self._log(f"Generated {name}: {info.get('path')}", "SUCCESS")
                    else:
                        self._log(f"Failed to generate {name}: {info.get('error')}", "ERROR")
                if summary.get("path"):
                    self._log(f"Official docs folder: {summary.get('path')}", "INFO")
            except Exception as exc:
                self._set_preview(f"Export finished but the preview failed: {exc}")
                self._log(f"Official docs preview failed: {exc}", "ERROR")
            finally:
                self._exporting_official = False
                self._set_official_export_enabled(True)

        def _build_licence_section(self, scroll) -> None:
            """Developer and licensing details for the offline product."""
            import version as ver

            info = ver.business_info()

            section = ctk.CTkFrame(scroll, fg_color=COLORS["bg_panel_alt"], corner_radius=10)
            section.pack(fill="x", padx=18, pady=(0, 18))
            section.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                section, text="Developer & Licensing",
                font=ctk.CTkFont(family=self.fonts.heading, size=13, weight="bold"),
                text_color=COLORS["text_primary"],
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 2))

            ctk.CTkLabel(
                section, text=info["licence"], wraplength=620, justify="left",
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
                text_color=COLORS["text_secondary"],
            ).grid(row=1, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 2))

            ctk.CTkLabel(
                section, text=info["evaluation"], wraplength=620, justify="left",
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
                text_color=COLORS["text_secondary"],
            ).grid(row=2, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))

            rows = [
                ("Product", info["product"]),
                ("Backend", info["backend"]),
                ("Company", info["company"]),
                ("Maintainer", f"{info['maintainer']}  (ORCID {info['orcid']})"),
                ("Licensing", "Offline local licence verification"),
            ]
            for index, (label, value) in enumerate(rows, start=3):
                ctk.CTkLabel(
                    section, text=f"{label}:", anchor="w",
                    font=ctk.CTkFont(family=self.fonts.ui, size=11, weight="bold"),
                    text_color=COLORS["text_secondary"],
                ).grid(row=index, column=0, sticky="w", padx=(14, 8), pady=1)
                ctk.CTkLabel(
                    section, text=value, anchor="w", justify="left", wraplength=520,
                    font=ctk.CTkFont(family=self.fonts.mono, size=11),
                    text_color=COLORS["text_primary"],
                ).grid(row=index, column=1, sticky="w", padx=(0, 14), pady=1)

            buttons = ctk.CTkFrame(section, fg_color="transparent")
            buttons.grid(row=3 + len(rows), column=0, columnspan=2, sticky="w",
                         padx=14, pady=(12, 14))

            ctk.CTkLabel(
                buttons, text="Offline licensing only - no external links",
                text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=(0, 8))

        def _build_format_section(self, scroll) -> None:
            section = ctk.CTkFrame(scroll, fg_color=COLORS["bg_panel_alt"], corner_radius=10)
            section.pack(fill="x", padx=18, pady=(0, 14))

            ctk.CTkLabel(
                section, text="Export Formats",
                font=ctk.CTkFont(family=self.fonts.heading, size=12, weight="bold"),
                text_color=COLORS["accent"],
            ).pack(anchor="w", padx=14, pady=(14, 8))

            row1 = ctk.CTkFrame(section, fg_color="transparent")
            row1.pack(fill="x", padx=14, pady=2)
            row2 = ctk.CTkFrame(section, fg_color="transparent")
            row2.pack(fill="x", padx=14, pady=(2, 14))

            self.fmt_md = ctk.BooleanVar(value=True)
            self.fmt_pdf = ctk.BooleanVar(value=False)
            self.fmt_html = ctk.BooleanVar(value=True)
            self.fmt_json = ctk.BooleanVar(value=True)
            self.fmt_latex = ctk.BooleanVar(value=False)
            self.fmt_svg = ctk.BooleanVar(value=False)

            for row, checks in [
                (row1, [("Markdown", self.fmt_md), ("HTML", self.fmt_html), ("LaTeX", self.fmt_latex)]),
                (row2, [("JSON", self.fmt_json), ("PDF", self.fmt_pdf), ("SVG", self.fmt_svg)]),
            ]:
                for label, var in checks:
                    ctk.CTkCheckBox(
                        row, text=label, variable=var,
                        fg_color=COLORS["accent_dim"], hover_color=COLORS["accent"],
                    ).pack(side="left", padx=(0, 10))

            # Info
            info = ctk.CTkFrame(scroll, fg_color=COLORS["bg_panel_alt"], corner_radius=10)
            info.pack(fill="x", padx=18, pady=(0, 14))
            ctk.CTkLabel(
                info, text="Certified Provenance",
                font=ctk.CTkFont(family=self.fonts.heading, size=12, weight="bold"),
                text_color=COLORS["accent"],
            ).pack(anchor="w", padx=14, pady=(14, 6))
            ctk.CTkLabel(
                info,
                text=(
                    "All exports embed provenance metadata: doc generator version, "
                    "UTC timestamp, and the QECTOR CERTIFIED watermark for traceability."
                ),
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=640, justify="left",
            ).pack(anchor="w", padx=14, pady=(0, 14))

        def _build_preview_section(self, scroll) -> None:
            preview_label = ctk.CTkLabel(
                scroll, text="Documentation Preview",
                font=ctk.CTkFont(family=self.fonts.heading, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
            )
            preview_label.pack(anchor="w", padx=18, pady=(0, 6))

            # ── Toolbar: search + zoom ──────────────────────────────────
            toolbar = ctk.CTkFrame(scroll, fg_color="transparent")
            toolbar.pack(fill="x", padx=18, pady=(0, 4))

            self.search_var = ctk.StringVar()
            self.search_entry = ctk.CTkEntry(
                toolbar, textvariable=self.search_var, width=220,
                placeholder_text="Search preview (Enter for next)",
                font=ctk.CTkFont(family=self.fonts.ui, size=11),
            )
            self.search_entry.pack(side="left")
            self.search_entry.bind("<Return>", lambda _e: self._on_search_next())
            self.search_entry.bind("<Shift-Return>", lambda _e: self._on_search_next(back=True))
            self.search_var.trace_add("write", lambda *_a: self._on_search_changed())

            ctk.CTkButton(
                toolbar, text="Find", width=54, command=self._on_search_next,
                font=ctk.CTkFont(size=11), fg_color=COLORS["bg_widget"],
                hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
            ).pack(side="left", padx=(6, 2))

            self.search_status = ctk.CTkLabel(
                toolbar, text="", font=ctk.CTkFont(size=10),
                text_color=COLORS["text_secondary"],
            )
            self.search_status.pack(side="left", padx=(6, 0))

            ctk.CTkButton(
                toolbar, text="Copy", width=58, command=self._on_copy_preview,
                font=ctk.CTkFont(size=11), fg_color=COLORS["bg_widget"],
                hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=(6, 0))

            ctk.CTkButton(
                toolbar, text="Print Preview (PDF)", width=120, command=self._on_pdf_preview,
                font=ctk.CTkFont(size=11), fg_color=COLORS["bg_widget"],
                hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=(6, 0))

            ctk.CTkButton(
                toolbar, text="A+", width=38, command=lambda: self._zoom(+1),
                font=ctk.CTkFont(size=11), fg_color=COLORS["bg_widget"],
                hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=2)
            ctk.CTkButton(
                toolbar, text="A-", width=38, command=lambda: self._zoom(-1),
                font=ctk.CTkFont(size=11), fg_color=COLORS["bg_widget"],
                hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
            ).pack(side="right", padx=2)
            self.zoom_label = ctk.CTkLabel(
                toolbar, text=f"{self._preview_font_size}pt", font=ctk.CTkFont(size=10),
                text_color=COLORS["text_secondary"], width=34,
            )
            self.zoom_label.pack(side="right", padx=(0, 2))

            self.preview = ctk.CTkTextbox(
                scroll, fg_color=COLORS["bg_panel_alt"], text_color=COLORS["text_primary"],
                font=ctk.CTkFont(family=self.fonts.mono, size=self._preview_font_size),
                height=260, wrap="word",
            )
            self.preview.pack(fill="x", padx=18, pady=(0, 14))
            self._configure_preview_tags()
            self.preview.configure(state="disabled")

        # ------------------------------------------------------------------
        # Preview: highlighting, search, zoom
        # ------------------------------------------------------------------
        def _configure_preview_tags(self) -> None:
            """Register the syntax tags used by :meth:`_highlight_preview`."""
            specs = {
                "hl_heading": {"foreground": COLORS["accent"]},
                "hl_path": {"foreground": COLORS["success"]},
                "hl_key": {"foreground": COLORS["text_secondary"]},
                "hl_error": {"foreground": COLORS["error"]},
                "hl_number": {"foreground": COLORS["warning"]},
                "search_hit": {"background": COLORS["accent_dim"],
                               "foreground": COLORS["text_primary"]},
                "search_current": {"background": COLORS["accent"], "foreground": "#ffffff"},
            }
            for name, cfg in specs.items():
                try:
                    self.preview.tag_config(name, **cfg)
                except Exception:
                    pass

        #: (tag, regex) applied in order to the preview text.
        _HIGHLIGHT_RULES = (
            ("hl_error", re.compile(r"^.*(?:failed|failure|error|could not).*$",
                                    re.IGNORECASE | re.MULTILINE)),
            ("hl_heading", re.compile(r"^[A-Z][A-Za-z &/]+:$", re.MULTILINE)),
            ("hl_path", re.compile(r"(?:[A-Za-z]:\\|/)[^\s\"'<>|]+")),
            ("hl_key", re.compile(r"^\s*[A-Za-z][A-Za-z0-9 _&/()-]*(?=:\s)", re.MULTILINE)),
            ("hl_number", re.compile(r"\b\d+(?:\.\d+)?\b")),
        )

        def _highlight_preview(self) -> None:
            """Apply syntax highlighting to the current preview text."""
            try:
                text = self.preview.get("1.0", "end-1c")
                for tag, _rx in self._HIGHLIGHT_RULES:
                    self.preview.tag_remove(tag, "1.0", "end")
                for tag, rx in self._HIGHLIGHT_RULES:
                    for match in rx.finditer(text):
                        if match.start() == match.end():
                            continue
                        self.preview.tag_add(
                            tag, f"1.0+{match.start()}c", f"1.0+{match.end()}c"
                        )
            except Exception:
                pass  # highlighting is cosmetic; never let it break the preview

        def _on_search_changed(self) -> None:
            """Re-mark all hits as the query changes, without moving the view."""
            self._search_hits = []
            self._search_index = -1
            try:
                self.preview.tag_remove("search_hit", "1.0", "end")
                self.preview.tag_remove("search_current", "1.0", "end")
            except Exception:
                return
            needle = self.search_var.get()
            if len(needle) < 2:
                self._set_search_status("")
                return
            try:
                text = self.preview.get("1.0", "end-1c").lower()
            except Exception:
                return
            start = 0
            low = needle.lower()
            while True:
                pos = text.find(low, start)
                if pos < 0:
                    break
                self._search_hits.append(pos)
                try:
                    self.preview.tag_add(
                        "search_hit", f"1.0+{pos}c", f"1.0+{pos + len(needle)}c"
                    )
                except Exception:
                    pass
                start = pos + max(len(low), 1)
            self._set_search_status(
                f"{len(self._search_hits)} match(es)" if self._search_hits else "no match"
            )

        def _on_search_next(self, back: bool = False) -> None:
            """Jump to the next (or previous) hit and scroll it into view."""
            if not self._search_hits:
                self._on_search_changed()
            if not self._search_hits:
                return
            step = -1 if back else 1
            self._search_index = (self._search_index + step) % len(self._search_hits)
            pos = self._search_hits[self._search_index]
            needle_len = max(len(self.search_var.get()), 1)
            try:
                self.preview.tag_remove("search_current", "1.0", "end")
                self.preview.tag_add(
                    "search_current", f"1.0+{pos}c", f"1.0+{pos + needle_len}c"
                )
                self.preview.see(f"1.0+{pos}c")
            except Exception:
                return
            self._set_search_status(
                f"{self._search_index + 1} of {len(self._search_hits)}"
            )

        def _set_search_status(self, text: str) -> None:
            try:
                self.search_status.configure(text=text)
            except Exception:
                pass

        def _zoom(self, direction: int) -> None:
            """Step the preview font size within a readable range."""
            size = self._preview_font_size + (1 if direction > 0 else -1)
            self._preview_font_size = max(8, min(size, 22))
            try:
                self.preview.configure(
                    font=ctk.CTkFont(family=self.fonts.mono, size=self._preview_font_size)
                )
                self.zoom_label.configure(text=f"{self._preview_font_size}pt")
            except Exception:
                pass

        def _on_copy_preview(self) -> None:
            """Copy the preview to the clipboard via this app's own Tk root."""
            try:
                text = self.preview.get("1.0", "end-1c")
            except Exception:
                return
            if not text.strip():
                self._log("Nothing to copy: the preview is empty.", "WARN")
                return
            from docs_exporter import copy_to_clipboard
            ok, message = copy_to_clipboard(text, widget=self)
            self._log(message, "SUCCESS" if ok else "ERROR")

        def _on_pdf_preview(self) -> None:
            """Generate a publication-grade PDF report and open it in the system viewer."""
            code = self.state.current_code if self.state else None
            if code is None:
                self._log("No active code. Build a code in Code Explorer first.", "WARN")
                return

            self._set_progress("Generating PDF report...")
            self._log("Generating PDF report for print preview", "INFO")

            def _worker():
                try:
                    import tempfile
                    from lab_info_tab import load_lab_info
                    
                    pdf_dir = Path(tempfile.gettempdir())
                    pdf_path = pdf_dir / f"qector_report_{code.name}.pdf"
                    
                    recs = []
                    self.generator.set_output_dir(pdf_dir)
                    meta = load_lab_info()
                    self.generator.set_meta(meta)
                    self.generator.generate(code, formats=["pdf"], recs=recs)
                    
                    import os
                    if sys.platform == "win32":
                        os.startfile(pdf_path)
                    elif sys.platform == "darwin":
                        subprocess.run(["open", str(pdf_path)])
                    else:
                        subprocess.run(["xdg-open", str(pdf_path)])
                    
                    self.status_progress_label.configure(text="PDF generated and opened successfully.", text_color=COLORS["success"])
                except Exception as exc:
                    self.status_progress_label.configure(text=f"PDF generation failed: {exc}", text_color=COLORS["error"])
                    self._log(f"PDF generation failed: {exc}", "ERROR")

            run_in_background(_worker)

        def _build_actions(self, scroll) -> None:
            actions = ctk.CTkFrame(scroll, fg_color="transparent")
            actions.pack(fill="x", padx=18, pady=(0, 18))

            self.generate_btn = ctk.CTkButton(
                actions, text="Generate Documentation", command=self._on_generate,
                fg_color=COLORS["accent_dim"], hover_color=COLORS["accent"], text_color=COLORS["text_primary"],
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self.generate_btn.pack(side="left", padx=(0, 8))

            ctk.CTkButton(
                actions, text="Open Export Folder", command=self._on_open_folder,
                fg_color=COLORS["bg_widget"], hover_color=COLORS["accent_dim"], text_color=COLORS["text_secondary"],
                font=ctk.CTkFont(size=11),
            ).pack(side="left", padx=8)

        # ------------------------------------------------------------------
        # Helpers (console/preview access never raises)
        # ------------------------------------------------------------------
        def _log(self, message: str, level: str = "INFO") -> None:
            if self.console is not None:
                try:
                    self.console.log(message, level)
                except Exception:
                    pass

        def _set_preview(self, text: str) -> None:
            try:
                self.preview.configure(state="normal")
                self.preview.delete("1.0", "end")
                self.preview.insert("1.0", text)
                self.preview.configure(state="disabled")
            except Exception:
                return
            # Re-apply highlighting and re-mark any live search against the new
            # text: stale tags would point at offsets that no longer exist.
            self._highlight_preview()
            try:
                if self.search_var.get():
                    self._on_search_changed()
            except Exception:
                pass

        def _set_generate_enabled(self, enabled: bool) -> None:
            try:
                self.generate_btn.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

        def _marshal(self, fn) -> None:
            """Schedule ``fn`` on the Tk main thread; never raises."""
            try:
                self.after(0, fn)
            except Exception:
                pass

        # ------------------------------------------------------------------
        # Generation (background thread; results marshaled via .after)
        # ------------------------------------------------------------------
        def _on_generate(self) -> None:
            if self._generating:
                return
            code = getattr(self.state, "current_code", None) if self.state else None
            if code is None:
                self._log("Build a code first, then generate documentation.", "WARN")
                self._set_preview("No active code. Build a code in Code Explorer first. Tip: Switch to the Code Explorer tab to build a code, then come back here.")
                return

            formats = []
            for var, fmt in [
                (self.fmt_md, "markdown"), (self.fmt_html, "html"),
                (self.fmt_json, "json"), (self.fmt_latex, "latex"),
                (self.fmt_pdf, "pdf"), (self.fmt_svg, "svg"),
            ]:
                try:
                    selected = bool(var.get())
                except Exception:
                    selected = False
                if selected:
                    formats.append(fmt)

            if not formats:
                self._log("Select at least one export format.", "WARN")
                self._set_preview("Select at least one export format.")
                return

            self._generating = True
            self._set_generate_enabled(False)
            self._set_progress(f"Generating {len(formats)} format(s)...", "warning")
            self._set_preview(f"Generating documentation ({', '.join(formats)})...")
            self._log(f"Documentation generation started: {', '.join(formats)}", "INFO")
            try:
                run_in_background(self._generate_worker, args=(code, formats))
            except Exception as exc:
                # Thread could not even start: restore the UI immediately.
                self._generating = False
                self._open_folder_when_done = False
                self._set_generate_enabled(True)
                self._set_progress("Generation failed to start", "error")
                self._set_preview(f"Generation failed to start: {exc}")
                self._log(f"Documentation generation failed to start: {exc}", "ERROR")

        def _generate_worker(self, code, formats: list) -> None:
            """Background thread: run the generator, marshal results via .after."""
            try:
                results = self.generator.generate_all(code, formats=formats)
            except Exception as exc:
                self._marshal(lambda exc=exc: self._on_generate_error(exc))
                return
            self._marshal(lambda: self._on_generate_done(code, results))

        def _on_generate_error(self, exc: Exception) -> None:
            try:
                self._set_progress("Generation failed", "error")
                self._set_preview(f"Generation failed: {exc}")
                self._log(f"Documentation generation failed: {exc}", "ERROR")
            finally:
                self._generating = False
                self._open_folder_when_done = False
                self._set_generate_enabled(True)

        def _on_generate_done(self, code, results: dict) -> None:
            outdir = ""
            try:
                exported = [f"{fmt.upper()}: {path}\n" for fmt, (ok, path) in results.items() if ok and path]
                failed = [f"{fmt}: failed\n" for fmt, (ok, _) in results.items() if not ok]
                ok_paths = [path for ok, path in results.values() if ok and path]
                if ok_paths:
                    outdir = str(ok_paths[0].parent)
                else:
                    outdir = str(self.generator.output_dir)
                try:
                    summary = be.code_summary(code)
                except Exception:
                    summary = {}
                preview_text = (
                    f"Code: {getattr(code, 'name', summary.get('name', 'N/A'))}\n"
                    f"Qubits: {summary.get('n_qubits', '?')} | Checks: {summary.get('n_checks', '?')}\n"
                    f"Output folder: {outdir}\n\n"
                    "Exported files:\n" + ("".join(exported) if exported else "None\n") +
                    ("\nFailures:\n" + "".join(failed) if failed else "")
                )
                self._set_preview(preview_text)
                n_ok = len(exported)
                n_bad = len(failed)
                if n_bad:
                    self._set_progress(f"Done: {n_ok} written, {n_bad} failed", "warning")
                else:
                    self._set_progress(f"Done: {n_ok} file(s) written", "success")
                if ok_paths:
                    self._remember_dir(outdir)
                for fmt, (ok, path) in results.items():
                    if ok and path:
                        self._log(f"Generated {fmt}: {path}", "SUCCESS")
                    else:
                        self._log(f"Failed to generate {fmt}", "ERROR")
            except Exception as exc:
                self._set_progress("Generated, preview failed", "warning")
                self._set_preview(f"Generation finished but the preview failed: {exc}")
                self._log(f"Documentation preview failed: {exc}", "ERROR")
            finally:
                self._generating = False
                self._set_generate_enabled(True)
                # Honour a pending "Generate and Open Folder" only now that the
                # files are on disk.
                if self._open_folder_when_done:
                    self._open_folder_when_done = False
                    self._open_path(outdir or str(self.generator.output_dir))

        # ------------------------------------------------------------------
        # Export folder
        # ------------------------------------------------------------------
        def _open_path(self, target: str) -> None:
            """Open ``target`` in the platform file manager.  Never raises."""
            try:
                target = str(Path(target).resolve())
                os.makedirs(target, exist_ok=True)
                if hasattr(os, "startfile"):
                    # Windows
                    os.startfile(target)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    # Linux / other POSIX desktops: hand off to the file manager.
                    subprocess.Popen(["xdg-open", target])
                self._log(f"Export folder: {target}", "INFO")
            except FileNotFoundError:
                # xdg-open / open not installed (e.g. headless host): still
                # surface the path so the user can open it manually.
                self._log(f"Export folder: {target}", "INFO")
            except Exception as exc:
                self._log(f"Could not open export folder: {exc}", "ERROR")

        def _on_open_folder(self) -> None:
            self._open_path(str(self.generator.output_dir))

else:

    class DocumentationTab:
        """No-GUI fallback used when customtkinter is unavailable."""

        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            self.state = state
            self.console = console
            self.fonts = fonts
