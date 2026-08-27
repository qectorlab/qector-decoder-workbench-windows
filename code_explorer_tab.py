"""code_explorer_tab.py  -  Code Explorer tab for QECTOR Workbench.

Code family browser with a debounced distance slider, background code
building, rich property/analysis panels, and an embedded matplotlib view
that toggles between a professional Tanner graph and the parity-check
matrix (dark-styled to match the app theme).
"""

from __future__ import annotations

import tkinter
import traceback
import html
from typing import Any, Optional

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False

import numpy as np

import backend as be
import theme
import threading_utils

_DEBOUNCE_MS = 200
_VIEW_TANNER = "Tanner graph"
_VIEW_MATRIX = "Parity-check matrix"
_VIEW_LATTICE = "2D Lattice"
_VIEW_RADAR = "Radar Chart"


def _dense_parity_matrix(code) -> Optional[np.ndarray]:
    """Return the code's parity-check matrix as a dense uint8 2-D array.

    ``parity_check_matrix`` may be an attribute or a bound method, dense or
    sparse; returns None when no usable matrix can be extracted.
    """
    matrix = getattr(code, "parity_check_matrix", None)
    if matrix is None:
        matrix = getattr(code, "H", None)
    try:
        if callable(matrix):
            matrix = matrix()
    except Exception:
        return None
    if matrix is None:
        return None
    try:
        if hasattr(matrix, "todense"):
            matrix = matrix.todense()
        arr = np.asarray(matrix)
    except Exception:
        return None
    if arr.ndim != 2 or arr.size == 0:
        return None
    return (arr != 0).astype(np.uint8)


if _HAS_GUI:

    class CodeExplorerTab(ctk.CTkFrame):
        """Full code exploration panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._build_seq = 0
            self._debounce_id: Optional[str] = None
            self._graph_data: Optional[dict[str, Any]] = None

            self.grid_columnconfigure(0, weight=0)
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(1, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)
            bold = ctk.CTkFont(size=12, weight="bold")

            # ── Controls (row 0, spans both columns) ──────────────────
            controls = ctk.CTkFrame(self, fg_color="transparent")
            controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))

            ctk.CTkLabel(
                controls, text="Code Explorer",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", pady=(0, 2))
            ctk.CTkLabel(
                controls,
                text="Build and inspect quantum error correction codes.",
                font=ctk.CTkFont(size=11),
                text_color=theme.c("text_secondary"),
            ).pack(anchor="w", pady=(0, 8))

            row = ctk.CTkFrame(controls, fg_color="transparent")
            row.pack(fill="x")
            ctk.CTkLabel(row, text="Code Family:", font=bold).pack(side="left")
            self.family_var = ctk.StringVar(value="rotated_surface")
            self.family_menu = ctk.CTkOptionMenu(
                row, values=list(be.CODE_FAMILIES.keys()),
                variable=self.family_var, command=self._on_family_change,
                width=190,
            )
            self.family_menu.pack(side="left", padx=(10, 24))

            ctk.CTkLabel(row, text="Distance:", font=bold).pack(side="left")
            self.distance_var = ctk.IntVar(value=5)
            self.distance_slider = ctk.CTkSlider(
                row, from_=3, to=63, number_of_steps=60,
                variable=self.distance_var, command=self._on_slider_change,
                width=240,
            )
            self.distance_slider.pack(side="left", padx=(10, 8))
            self.distance_label = ctk.CTkLabel(row, text="5", width=24, font=ctk.CTkFont(size=12))
            self.distance_label.pack(side="left", padx=(0, 24))

            self.build_btn = ctk.CTkButton(
                row, text="Build Code", command=self._on_build,
                font=ctk.CTkFont(size=12, weight="bold"), width=120,
            )
            self.build_btn.pack(side="left")

            self.status_label = ctk.CTkLabel(
                row, text="", font=ctk.CTkFont(size=11),
                text_color=theme.c("text_secondary"),
            )
            self.status_label.pack(side="left", padx=(14, 0))

            # ── Action buttons live on their own row so they are never
            # truncated when the window is too narrow to fit everything on
            # a single line.
            actions = ctk.CTkFrame(controls, fg_color="transparent")
            actions.pack(fill="x", pady=(8, 0))

            self.gen_doc_btn = ctk.CTkButton(
                actions, text="📄 Generate Doc", command=self._on_generate_doc,
                font=ctk.CTkFont(size=12, weight="bold"), width=130,
            )
            self.gen_doc_btn.pack(side="left")

            self.quick_export_btn = ctk.CTkButton(
                actions, text="Quick Export", command=self._on_quick_export,
                font=ctk.CTkFont(size=12), width=110,
                fg_color=theme.COLORS.get("bg_widget", "gray20"),
                hover_color=theme.COLORS.get("bg_panel", "gray30"),
            )
            self.quick_export_btn.pack(side="left", padx=(8, 0))

            self.import_dem_btn = ctk.CTkButton(
                actions, text="Import DEM", command=self._on_import_dem,
                font=ctk.CTkFont(size=12), width=90,
                fg_color=theme.COLORS.get("bg_widget", "gray20"),
                hover_color=theme.COLORS.get("bg_panel", "gray30"),
            )
            self.import_dem_btn.pack(side="left", padx=(8, 0))

            self.import_stim_btn = ctk.CTkButton(
                actions, text="Import Stim", command=self._on_import_stim,
                font=ctk.CTkFont(size=12), width=90,
                fg_color=theme.COLORS.get("bg_widget", "gray20"),
                hover_color=theme.COLORS.get("bg_panel", "gray30"),
            )
            self.import_stim_btn.pack(side="left", padx=(8, 0))

            # ── Left column: properties + analysis ────────────────────
            left = ctk.CTkFrame(self, fg_color="transparent", width=340)
            left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(4, 12))
            left.grid_propagate(False)
            left.grid_columnconfigure(0, weight=1)
            left.grid_rowconfigure(1, weight=3)
            left.grid_rowconfigure(3, weight=2)

            ctk.CTkLabel(left, text="Properties", font=bold).grid(row=0, column=0, sticky="w")
            self.props_text = ctk.CTkTextbox(left, wrap="word", font=mono)
            self.props_text.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
            self.props_text.insert("1.0", "Build a code to see its properties.")
            self.props_text.configure(state="disabled")

            ctk.CTkLabel(left, text="Analysis", font=bold).grid(row=2, column=0, sticky="w")
            self.analysis_text = ctk.CTkTextbox(left, wrap="word", font=mono)
            self.analysis_text.grid(row=3, column=0, sticky="nsew", pady=(2, 0))
            self.analysis_text.insert("1.0", "Analysis will appear here after building a code.")
            self.analysis_text.configure(state="disabled")

            # ── Right column: view toggle + matplotlib canvas ─────────
            right = ctk.CTkFrame(self, fg_color=theme.c("bg_panel"))
            right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(4, 12))
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(1, weight=1)

            self.view_toggle = ctk.CTkSegmentedButton(
                right, values=[_VIEW_TANNER, _VIEW_MATRIX, _VIEW_LATTICE, _VIEW_RADAR],
                command=self._on_view_change,
                font=ctk.CTkFont(size=11),
            )
            self.view_toggle.set(_VIEW_TANNER)
            self.view_toggle.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

            theme.configure_matplotlib()
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

            self._figure = Figure(figsize=(6.4, 4.8), dpi=140)
            theme.style_dark_figure(self._figure)
            self._mpl_canvas = FigureCanvasTkAgg(self._figure, master=right)
            self._mpl_canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
            self._draw_placeholder("Build a code to see its Tanner graph.")

        # ── logging helper ─────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        # ── event handlers ─────────────────────────────────────────────
        def _on_family_change(self, choice: str) -> None:
            self._log(f"Family changed to {choice}", "INFO")
            self._schedule_build()

        def _on_slider_change(self, value=None) -> None:
            """Debounce slider drags so we do not spam code builds."""
            try:
                d = int(round(float(value))) if value is not None else int(self.distance_var.get())
            except (TypeError, ValueError, tkinter.TclError):
                return
            try:
                self.distance_label.configure(text=str(d))
            except tkinter.TclError:
                return
            self._schedule_build()

        def _schedule_build(self) -> None:
            if self._debounce_id is not None:
                try:
                    self.after_cancel(self._debounce_id)
                except Exception:
                    pass
            try:
                self._debounce_id = self.after(_DEBOUNCE_MS, self._on_build)
            except tkinter.TclError:
                self._debounce_id = None

        def _on_build(self, *_args) -> None:
            """Validate inputs and build the selected code in the background."""
            self._debounce_id = None
            family = self.family_var.get()
            try:
                d = int(self.distance_var.get())
            except tkinter.TclError:
                self._set_text(self.props_text, "Invalid distance value  -  use the slider to pick 3-63.")
                return
            if family not in be.CODE_FAMILIES:
                self._set_text(self.props_text, f"Unknown code family: {family!r}")
                return

            self._build_seq += 1
            seq = self._build_seq
            try:
                self.build_btn.configure(state="disabled")
                self.status_label.configure(text=f"Building {family} d={d} ...")
            except tkinter.TclError:
                return
            threading_utils.run_in_background(self._build_worker, args=(seq, family, d))

        def _on_generate_doc(self, *_args) -> None:
            if not self.state or not getattr(self.state, "current_code", None):
                self._set_text(self.props_text, "Please build a code first before generating documentation.")
                return

            try:
                self.gen_doc_btn.configure(state="disabled")
                self._set_text(self.analysis_text, "Generating professional documentation...\nThis may take a moment.")
            except tkinter.TclError:
                pass

            code_obj = self.state.current_code
            threading_utils.run_in_background(self._gen_doc_worker, args=(code_obj,))

        def _gen_doc_worker(self, code_obj) -> None:
            try:
                from doc_generator import ProfessionalDocGenerator
                doc_gen = ProfessionalDocGenerator()
                formats = ["markdown", "html", "pdf"]
                results = doc_gen.generate_all(code_obj, formats)
                self._ui.post(self._on_gen_doc_done, results)
            except Exception as e:
                self._log(f"Doc generation failed: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_gen_doc_failed, f"Doc generation failed: {e}")

        def _on_gen_doc_done(self, results: dict) -> None:
            try:
                self.gen_doc_btn.configure(state="normal")
            except tkinter.TclError:
                pass

            lines = ["Documentation Generation Results:\n"]
            for fmt, (ok, path) in results.items():
                status = "SUCCESS" if ok else "FAILED"
                if ok:
                    self._log(f"Generated {fmt} doc at {path}", "SUCCESS")
                else:
                    self._log(f"Failed to generate {fmt} doc", "ERROR")
                lines.append(f"- {fmt.upper()}: {status} ({path if ok else 'N/A'})")

            self._set_text(self.analysis_text, "\n".join(lines))

        def _on_gen_doc_failed(self, message: str) -> None:
            try:
                self.gen_doc_btn.configure(state="normal")
                self._set_text(self.analysis_text, message)
            except tkinter.TclError:
                pass

        def _on_quick_export(self, *_args) -> None:
            if not self.state or not getattr(self.state, "current_code", None):
                self._set_text(self.props_text, "Please build a code first before exporting.")
                return

            try:
                from tkinter import filedialog
                import os

                family = self.family_var.get()
                d = self.distance_var.get()
                default_name = f"{family}_d{d}_doc"

                filepath = filedialog.asksaveasfilename(
                    initialfile=default_name,
                    defaultextension=".md",
                    filetypes=[
                        ("Markdown", "*.md"),
                        ("HTML", "*.html"),
                        ("JSON", "*.json")
                    ]
                )
                if not filepath:
                    return

                import utils
                ok_path, filepath = utils.sanitize_export_path(filepath)
                if not ok_path:
                    self._log("Quick export rejected: directory traversal is not allowed.", "ERROR")
                    return
                filepath = str(filepath)

                props = self.props_text.get("1.0", "end-1c")
                analysis = self.analysis_text.get("1.0", "end-1c")
                ext = os.path.splitext(filepath)[1].lower()

                if ext == ".html":
                    content = f"<html><body><h2>Properties</h2><pre>{html.escape(props)}</pre><h2>Analysis</h2><pre>{html.escape(analysis)}</pre></body></html>"
                elif ext == ".json":
                    import json
                    content = json.dumps({"properties": props, "analysis": analysis}, indent=2)
                else:
                    content = f"## Properties\n\n```text\n{props}\n```\n\n## Analysis\n\n```text\n{analysis}\n```\n"

                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)

                self._log(f"Quick export saved to {filepath}", "SUCCESS")
            except Exception as e:
                self._log(f"Quick export failed: {e}", "ERROR")

        def _on_import_dem(self) -> None:
            self._import_model("DEM Files", "*.dem", is_stim=False)

        def _on_import_stim(self) -> None:
            self._import_model("Stim Circuits", "*.stim", is_stim=True)

        def _import_model(self, file_desc: str, ext: str, is_stim: bool) -> None:
            """Import a .dem or .stim file via DemModel."""
            import tkinter.filedialog as fd
            path = fd.askopenfilename(
                title=f"Import {file_desc}",
                filetypes=[(file_desc, ext), ("All Files", "*.*")],
            )
            if not path:
                return
            self._set_text(self.props_text, f"Importing from {path} ...")
            self._log(f"Importing from {path}", "INFO")
            
            def _worker():
                try:
                    dem_mod = getattr(be.qd, "dem", None)
                    if dem_mod is None:
                        raise ValueError("DEM module not available")
                    DemModel = getattr(dem_mod, "DemModel", None)
                    if DemModel is None:
                        raise ValueError("DemModel not available")
                    
                    if is_stim:
                        stim_mod = getattr(be.qd, "stim_compat", None)
                        if stim_mod is None:
                            raise ValueError("stim_compat module not available")
                        from_stim = getattr(stim_mod, "from_stim_detector_error_model", None)
                        if from_stim is None:
                            raise ValueError("from_stim_detector_error_model not available")
                        model = DemModel.from_stim(path)
                    else:
                        model = DemModel.from_parities([], 0) # Mock fallback if no direct load 
                        # Assuming DemModel has a load/from_file method, but usually it's from_stim
                        # We will use from_stim for both if .dem is a Stim DEM file.
                        model = DemModel.from_stim(path)
                    
                    props = (
                        f"Imported {'Stim' if is_stim else 'DEM'}: {path}\n\n"
                        f"Checks: {model.n_checks if hasattr(model, 'n_checks') else 'N/A'}\n"
                        f"Edges: {model._m.n_edges if hasattr(model, '_m') else 'N/A'}\n"
                    )
                    
                    self._ui.post(self._on_import_done, True, props, model)
                except Exception as e:
                    self._ui.post(self._on_import_done, False, f"Import failed: {e}", None)

            import threading
            threading.Thread(target=_worker, daemon=True).start()

        def _on_import_done(self, success: bool, message: str, model: Any) -> None:
            self._set_text(self.props_text, message)
            if not success:
                self._log(message, "ERROR")
            elif self.state:
                class _DemCodeMock:
                    name = "Imported Model"
                    n_checks = getattr(model, "n_checks", 0)
                    n_qubits = getattr(model, "n_qubits", 0)
                self.state.current_code = _DemCodeMock()


        # ── worker (background thread) ─────────────────────────────────
        def _build_worker(self, seq: int, family: str, d: int) -> None:
            try:
                code = be.build_code(family, d)
                summary = be.code_summary(code)
                H = _dense_parity_matrix(code)
                if H is None:
                    try:
                        H = be.generate_parity_check_matrix(family, d)
                    except Exception as e:
                        self._log(f"Fallback generate_parity_check_matrix failed: {e}", "WARN")
                q_coords, c_coords = be.get_tanner_graph_layout(code, family, d)
                analysis = self._analysis_text(code, family, d)
                payload = {
                    "code": code, "family": family, "d": d,
                    "summary": summary, "H": H,
                    "q_coords": q_coords, "c_coords": c_coords,
                    "analysis": analysis,
                }
                self._ui.post(self._on_build_done, seq, payload)
            except be.QectorError as e:
                self._log(f"Build failed: {e}", "ERROR")
                self._ui.post(self._on_build_failed, seq, f"Build failed: {e}")
            except Exception as e:
                self._log(f"Unexpected build error: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_build_failed, seq, f"Unexpected build error: {e}")

        @staticmethod
        def _analysis_text(code, family: str, d: int) -> str:
            """Decoder recommendation for this code (safe to call off-thread)."""
            try:
                from hardware_routing import recommend
                rec = recommend(family, d, getattr(code, "n_qubits", None), "balanced")
                return (
                    f"Recommended decoder: {rec.decoder}\n"
                    f"Priority:            {rec.priority}\n"
                    f"Hardware:            {rec.hardware}\n"
                    f"Batch size:          {rec.batch_size}\n"
                    f"Reason:              {rec.reason}\n"
                )
            except Exception:
                return "Code analysis: compatible with all decoders."

        # ── completion (UI thread via UiPump) ──────────────────────────
        def _on_build_done(self, seq: int, payload: dict[str, Any]) -> None:
            if seq != self._build_seq:
                return  # a newer build superseded this one
            code, family, d = payload["code"], payload["family"], payload["d"]
            summary = payload["summary"]
            try:
                if self.state:
                    self.state.set_code(code, family, d)

                n_qubits = summary.get("n_qubits", "?")
                n_checks = summary.get("n_checks", "?")
                try:
                    rate = f"{(n_qubits - n_checks) / n_qubits:.4f}"
                except Exception:
                    rate = "?"
                lines = [
                    f"Name:             {summary.get('name', family)}",
                    f"Family:           {family}",
                    f"Distance:         {summary.get('distance', d)}",
                    f"Qubits:           {n_qubits}",
                    f"Checks:           {n_checks}",
                    f"Rate (n-m)/n:     {rate}",
                ]
                if "max_qubit_degree" in summary:
                    lines.append(f"Max qubit degree: {summary['max_qubit_degree']}")
                if summary.get("description"):
                    lines.append("")
                    lines.append(str(summary["description"]))
                self._set_text(self.props_text, "\n".join(lines))
                self._set_text(self.analysis_text, payload["analysis"])

                self._graph_data = {
                    "q_coords": payload["q_coords"],
                    "c_coords": payload["c_coords"],
                    "H": payload["H"],
                    "name": str(summary.get("name", f"{family} d={d}")),
                }
                self._redraw()
                self.status_label.configure(text=f"Built {family} d={d}")
                self._log(f"Built {family} d={d}: {n_qubits} qubits, {n_checks} checks", "SUCCESS")
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _on_build_failed(self, seq: int, message: str) -> None:
            if seq != self._build_seq:
                return
            try:
                self._set_text(self.props_text, message)
                self.status_label.configure(text="Build failed")
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _reenable(self, seq: int) -> None:
            if seq != self._build_seq:
                return
            try:
                self.build_btn.configure(state="normal")
            except tkinter.TclError:
                pass

        # ── drawing (UI thread only) ───────────────────────────────────
        def _on_view_change(self, _choice: str = "") -> None:
            self._redraw()

        def _draw_placeholder(self, message: str) -> None:
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            theme.style_dark_axes(ax, grid=False)
            ax.set_xticks([])
            ax.set_yticks([])
            ax.text(
                0.5, 0.5, message, ha="center", va="center",
                color=theme.mc("text_secondary"), fontsize=10,
                transform=ax.transAxes,
            )
            self._mpl_canvas.draw_idle()

        def _redraw(self) -> None:
            """Redraw the current view; figures are reused, never recreated."""
            data = self._graph_data
            if data is None:
                self._draw_placeholder("Build a code to see its Tanner graph.")
                return
            try:
                view = self.view_toggle.get()
            except tkinter.TclError:
                return
            try:
                if view == _VIEW_MATRIX:
                    self._draw_matrix(data)
                elif view == _VIEW_LATTICE:
                    self._draw_lattice(data)
                elif view == _VIEW_RADAR:
                    self._draw_radar(data)
                else:
                    self._draw_tanner(data)
            except Exception as e:
                self._log(f"Graph rendering failed: {e}", "ERROR")
                self._draw_placeholder(f"Graph rendering failed:\n{e}")

        def _draw_tanner(self, data: dict[str, Any]) -> None:
            from matplotlib.collections import LineCollection

            q_coords = data["q_coords"]
            c_coords = data["c_coords"]
            H = data["H"]
            name = data["name"]

            # Cancel any ongoing progressive rendering
            if hasattr(self, "_progressive_after_id") and self._progressive_after_id:
                try:
                    self.after_cancel(self._progressive_after_id)
                except Exception:
                    pass
                self._progressive_after_id = None

            cache_key = ("tanner", str(name), len(q_coords), len(c_coords))
            blob = None
            if len(q_coords) + len(c_coords) <= 400:
                try:
                    import figure_cache
                    blob = figure_cache.get(*cache_key)
                    if blob is not None:
                        restored = figure_cache.loads_state(blob)
                        if restored is not None:
                            self._figure.clear()
                            self._figure = restored
                            self._mpl_canvas.figure = self._figure
                            self._mpl_canvas.draw_idle()
                            return
                except Exception:
                    blob = None

            # Progressive rendering: for very large codes, first render the
            # node skeleton synchronously (fast), then schedule the full edge
            # pass on the idle loop so the UI never visibly freezes.
            is_large = len(q_coords) + len(c_coords) > 800

            self._figure.clear()
            self._ax = self._figure.add_subplot(111)
            theme.style_dark_axes(self._ax, title=f"Tanner graph  -  {name}", grid=False)
            self._ax.set_xticks([])
            self._ax.set_yticks([])

            n_nodes = len(q_coords) + len(c_coords)
            size = float(np.clip(2800.0 / max(n_nodes, 1), 16.0, 100.0))
            if q_coords:
                qx, qy = zip(*q_coords)
                self._ax.scatter(
                    qx, qy, s=size, marker="o",
                    c=theme.mc("qubit_node"),
                    edgecolors=theme.mc("fig_bg"), linewidths=0.8,
                    zorder=3, label="qubit",
                    antialiased=True,
                )
            if c_coords:
                cx, cy = zip(*c_coords)
                self._ax.scatter(
                    cx, cy, s=size, marker="s",
                    c=theme.mc("check_node"),
                    edgecolors=theme.mc("fig_bg"), linewidths=0.8,
                    zorder=3, label="check",
                    antialiased=True,
                )
            legend = self._ax.legend(loc="upper right", fontsize=8, scatterpoints=1)
            theme.style_dark_legend(legend)
            self._ax.set_aspect("equal", adjustable="datalim")
            self._ax.autoscale_view()
            self._ax.margins(0.08)
            self._mpl_canvas.draw_idle()

            if H is not None and len(q_coords) and len(c_coords):
                rows, cols = np.nonzero(H)
                valid_mask = (rows < len(c_coords)) & (cols < len(q_coords))
                rows = rows[valid_mask]
                cols = cols[valid_mask]
                
                self._edge_segments = [
                    (q_coords[c_idx], c_coords[r_idx])
                    for r_idx, c_idx in zip(rows, cols)
                ]
                self._edge_collection = LineCollection(
                    [], colors=theme.mc("edge"),
                    linewidths=1.0, alpha=0.80, zorder=1,
                    antialiaseds=True,
                )
                self._ax.add_collection(self._edge_collection)
                
                chunk_size = 200 if is_large else 2000
                self._progressive_draw_edges(0, chunk_size)

            # Cache the drawing for small/medium codes so switching views or
            # rebuilding a code is instant on the next visit.
            if not is_large and len(q_coords) + len(c_coords) <= 400:
                try:
                    import figure_cache
                    figure_cache.put(*cache_key, figure_cache.dumps_state(self._figure))
                except Exception:
                    pass

        def _progressive_draw_edges(self, start_idx: int, chunk_size: int) -> None:
            self._progressive_after_id = None
            if not hasattr(self, "_edge_segments") or not self._edge_segments:
                return
            end_idx = min(start_idx + chunk_size, len(self._edge_segments))
            current_segs = self._edge_segments[:end_idx]
            self._edge_collection.set_segments(current_segs)
            self._mpl_canvas.draw_idle()
            if end_idx < len(self._edge_segments):
                self._progressive_after_id = self.after(
                    15, self._progressive_draw_edges, end_idx, chunk_size
                )

        def _draw_lattice(self, data: dict[str, Any]) -> None:
            q_coords = data["q_coords"]
            c_coords = data["c_coords"]
            name = data["name"]

            # 2D Lattice is a geometric view; it is only meaningful for codes
            # whose backend supplies spatial coordinates. Guard against very
            # large codes: a full distance matrix on a d=17 surface code is
            # ~5k x ~5k and would freeze the Tk main thread, so we cap the
            # edge construction and degrade gracefully to a node-only plot.
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            theme.style_dark_axes(ax, title=f"2D Lattice representation  -  {name}", grid=False)

            if not q_coords:
                self._draw_placeholder("Lattice representation is only available for codes with coordinates.")
                return

            is_large = (len(q_coords) + len(c_coords)) > 5000

            try:
                qx = [float(p[0]) for p in q_coords]
                qy = [float(p[1]) for p in q_coords]
            except (TypeError, IndexError, ValueError):
                self._draw_placeholder(
                    "Lattice representation is only available for codes with 2-D coordinates."
                )
                return
            ax.scatter(qx, qy, color=theme.mc("qubit_node"), s=50, label="qubit", zorder=3)

            if c_coords and not is_large:
                try:
                    cx = [float(p[0]) for p in c_coords]
                    cy = [float(p[1]) for p in c_coords]
                except (TypeError, IndexError, ValueError):
                    cx = cy = None
                if cx is not None:
                    ax.scatter(cx, cy, color=theme.mc("check_node"), s=50, marker="s", label="check", zorder=3)

                    # Vectorised nearest-neighbour edge construction (numpy). The
                    # previous O(N*M) pure-Python loop over every qubit/check
                    # pair froze the UI on large codes (e.g. rotated_surface d>=13).
                    # This mirrors the safe pattern already used in doc_generator.
                    try:
                        from scipy.spatial import distance_matrix
                        q_arr = np.array(list(zip(qx, qy)), dtype=float)
                        c_arr = np.array(list(zip(cx, cy)), dtype=float)
                        dists = distance_matrix(q_arr, c_arr)
                        min_dist = float(dists.min())
                        threshold = min_dist * 1.5
                        near = dists <= threshold
                        q_idx, c_idx = np.nonzero(near)
                        if q_idx.size:
                            from matplotlib.collections import LineCollection
                            segments = [
                                ((qx[i], qy[i]), (cx[j], cy[j]))
                                for i, j in zip(q_idx.tolist(), c_idx.tolist())
                            ]
                            ax.add_collection(LineCollection(
                                segments, colors=theme.mc("edge"),
                                linewidths=1.0, alpha=0.6, zorder=1,
                            ))
                    except Exception as e:
                        self._log(f"Lattice edge computation skipped: {e}", "WARN")

            if is_large:
                ax.text(
                    0.5, 0.95,
                    f"Edge drawing disabled for large codes ({len(q_coords)} qubits). "
                    "Switch to Tanner graph for connectivity.",
                    ha="center", va="top",
                    color=theme.mc("text_secondary"), fontsize=9,
                    transform=ax.transAxes,
                )

            ax.set_aspect("equal", adjustable="datalim")
            ax.autoscale_view()
            self._mpl_canvas.draw_idle()

        def _draw_radar(self, data: dict[str, Any]) -> None:
            H = data["H"]
            self._figure.clear()
            
            # Compute metrics for the radar chart
            if H is None:
                self._draw_placeholder("No parity-check matrix available for this code.")
                return

            n_qubits = H.shape[1]
            n_checks = H.shape[0]
            rate = max(0.0, float(n_qubits - n_checks) / max(n_qubits, 1))
            sparsity = 1.0 - (float(np.count_nonzero(H)) / H.size)
            
            # Normalize to 0-1 scale for the radar chart
            # 1. Qubits count (log scale or normalized to max 200)
            norm_qubits = min(1.0, n_qubits / 200.0)
            # 2. Checks count
            norm_checks = min(1.0, n_checks / 200.0)
            # 3. Rate
            norm_rate = rate
            # 4. Sparsity (denser H -> lower sparsity)
            norm_sparsity = sparsity
            # 5. Distance (from distance_var, normalized to max 31)
            norm_dist = min(1.0, float(self.distance_var.get()) / 31.0)

            categories = ["Qubits", "Checks", "Code Rate", "Sparsity", "Distance"]
            values = [norm_qubits, norm_checks, norm_rate, norm_sparsity, norm_dist]
            
            # Close the radar loop
            import math
            N = len(categories)
            angles = [n / float(N) * 2 * math.pi for n in range(N)]
            values += values[:1]
            angles += angles[:1]

            ax = self._figure.add_subplot(111, polar=True)
            ax.set_facecolor(theme.mc("axes_bg"))
            
            # Draw categories
            ax.set_xticks(angles[:-1])
            ax.set_xticklabels(categories, color=theme.mc("text_primary"), fontsize=9)
            
            # Plot data
            ax.plot(angles, values, color=theme.mc("accent"), linewidth=2, linestyle="solid")
            ax.fill(angles, values, color=theme.mc("accent"), alpha=0.25)
            
            # Set grid colors
            ax.grid(color=theme.mc("grid"), linestyle="--", linewidth=0.5)
            ax.spines['polar'].set_color(theme.mc("grid"))
            ax.tick_params(colors=theme.mc("text_secondary"))
            
            ax.set_title(f"Code Metrics Radar  -  {data['name']}", color=theme.mc("text_primary"), pad=15)
            self._mpl_canvas.draw_idle()

        def _draw_matrix(self, data: dict[str, Any]) -> None:
            from matplotlib.colors import ListedColormap

            H = data["H"]
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            if H is None:
                theme.style_dark_axes(ax, grid=False)
                ax.set_xticks([])
                ax.set_yticks([])
                ax.text(
                    0.5, 0.5, "No parity-check matrix available for this code.",
                    ha="center", va="center", color=theme.mc("text_secondary"),
                    fontsize=10, transform=ax.transAxes,
                )
            else:
                theme.style_dark_axes(
                    ax, title=f"Parity-check matrix  -  {data['name']}",
                    xlabel="qubits", ylabel="checks", grid=False,
                )
                cmap = ListedColormap([theme.mc("axes_bg"), theme.mc("accent")])
                ax.imshow(H, cmap=cmap, aspect="auto", interpolation="nearest", vmin=0, vmax=1)
            self._mpl_canvas.draw_idle()

        # ── textbox helper ─────────────────────────────────────────────
        @staticmethod
        def _set_text(widget, text: str) -> None:
            try:
                widget.configure(state="normal")
                widget.delete("1.0", "end")
                widget.insert("1.0", text)
                widget.configure(state="disabled")
            except tkinter.TclError:
                pass

else:
    class CodeExplorerTab:
        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            pass
