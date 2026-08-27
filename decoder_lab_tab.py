"""decoder_lab_tab.py  -  Decoder Lab tab for QECTOR Workbench.

Interactive decoder testing: select decoder (info updates live), set error
rate and seed, run a single decode in a background thread, and inspect the
error, syndrome, correction, syndrome validity, and logical failure.
"""

from __future__ import annotations

import inspect
import tkinter
import traceback
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
import utils

_MAX_SEED = 2**31 - 1
_MAX_STATS_SAMPLES = 100_000
_MAX_TRAIN_SAMPLES = 50_000
_MAX_TRAIN_EPOCHS = 1_000

# Display-label -> backend-value maps for the contextual decoder options panel.
_BP_METHODS = {"Exact": "exact", "Min-Sum": "min_sum", "Relay": "relay"}
_OSD_ORDERS = ("0", "1", "2")
_ESCALATIONS = {"Blossom": "blossom", "BP-OSD": "bposd"}
# Decoder kinds whose decode path is driven by learned / BP-derived weights.
_NEURAL_KINDS = ("gnn_belief_matching", "belief_matching")


def _supports_decoder_options() -> bool:
    """True when backend.run_single_decode accepts a ``decoder_options`` kwarg.

    The backend evolves independently of the workbench; probing the signature
    keeps the GUI honest on builds that predate per-decoder options.
    """
    try:
        return "decoder_options" in inspect.signature(be.run_single_decode).parameters
    except Exception:
        return False


if _HAS_GUI:

    class DecoderLabTab(ctk.CTkFrame):
        """Interactive decoder laboratory panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._run_seq = 0
            self._last_decode_result = None
            self._imported_syndrome = None
            self._imported_syndrome_path = None
            self._history: list[dict] = []

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(0, weight=1)

            scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
            scroll.grid(row=0, column=0, sticky="nsew")
            scroll.grid_columnconfigure(0, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)
            bold = ctk.CTkFont(size=12, weight="bold")

            ctk.CTkLabel(
                scroll, text="Decoder Laboratory",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(16, 4))
            ctk.CTkLabel(
                scroll, text="Test decoders interactively on the current code.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
            ).pack(anchor="w", padx=16, pady=(0, 12))

            # Decoder selector  -  info text updates when the choice changes
            row0 = ctk.CTkFrame(scroll, fg_color="transparent")
            row0.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(row0, text="Decoder:", font=bold).pack(side="left")
            self.decoder_var = ctk.StringVar(value="union_find")
            self.decoder_menu = ctk.CTkOptionMenu(
                row0, values=list(be.DECODER_KINDS),
                variable=self.decoder_var, width=180,
                command=self._on_decoder_change,
            )
            self.decoder_menu.pack(side="left", padx=(12, 0))
            # When the chosen decoder cannot handle the current code (e.g. a
            # union-find decoder on a qLDPC code), auto-recover with a working
            # decoder and report the fallback instead of just erroring.
            self.resilient_var = ctk.BooleanVar(value=True)
            self.resilient_check = ctk.CTkCheckBox(
                row0, text="Resilient fallback", variable=self.resilient_var,
                font=ctk.CTkFont(size=11),
            )
            self.resilient_check.pack(side="left", padx=(16, 0))

            # Contextual per-decoder options panel  -  the visible contents swap
            # with the selected decoder kind (BP-OSD tuning, hybrid-cascade
            # escalation + batch stats, neural-path info + predecoder
            # training).  Empty  -  and effectively invisible  -  for decoders
            # that expose no options.
            self.options_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            self.options_frame.pack(fill="x", padx=16, pady=0)
            self._build_options_panels()
            self._stats_seq = 0

            # Error rate
            row1 = ctk.CTkFrame(scroll, fg_color="transparent")
            row1.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(row1, text="Error Rate:", font=bold).pack(side="left")
            self.rate_var = ctk.DoubleVar(value=0.05)
            self.rate_slider = ctk.CTkSlider(
                row1, from_=0.01, to=0.5, number_of_steps=49,
                variable=self.rate_var, command=self._update_rate_label,
                width=250,
            )
            self.rate_slider.pack(side="left", padx=(12, 8))
            self.rate_label = ctk.CTkLabel(row1, text="0.05", font=ctk.CTkFont(size=12))
            self.rate_label.pack(side="left")

            # Seed (plain entry  -  validated as text, never crashes)
            row2 = ctk.CTkFrame(scroll, fg_color="transparent")
            row2.pack(fill="x", padx=16, pady=4)
            ctk.CTkLabel(row2, text="Seed:", font=bold).pack(side="left")
            self.seed_entry = ctk.CTkEntry(row2, width=100)
            self.seed_entry.insert(0, "42")
            self.seed_entry.pack(side="left", padx=(12, 0))

            # Decode button
            btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
            btn_row.pack(fill="x", padx=16, pady=8)
            self.decode_btn = ctk.CTkButton(
                btn_row, text="Run Decode", command=self._on_decode,
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self.decode_btn.pack(side="left")
            self.clear_cache_btn = ctk.CTkButton(
                btn_row, text="Clear Decoder Cache", command=self._on_clear_cache,
                font=ctk.CTkFont(size=11), width=140, fg_color="#3A3D4E",
            )
            self.clear_cache_btn.pack(side="left", padx=(12, 0))
            self.import_syn_btn = ctk.CTkButton(
                btn_row, text="⬆ Import Syndrome", command=self._on_import_syndrome,
                font=ctk.CTkFont(size=11), width=140, fg_color="#2E5A4B",
            )
            self.import_syn_btn.pack(side="left", padx=(12, 0))
            self.compare_btn = ctk.CTkButton(
                btn_row, text="⚔ Compare Decoders", command=self._on_compare,
                font=ctk.CTkFont(size=11), width=150, fg_color="#5A4B2E",
            )
            self.compare_btn.pack(side="left", padx=(12, 0))

            # Doc gen and Export buttons
            doc_row = ctk.CTkFrame(scroll, fg_color="transparent")
            doc_row.pack(fill="x", padx=16, pady=(0, 8))
            self.gen_doc_btn = ctk.CTkButton(
                doc_row, text="📄 Generate Doc", command=self._on_generate_doc,
                font=ctk.CTkFont(size=12, weight="bold")
            )
            self.gen_doc_btn.pack(side="left")

            self.export_var = ctk.StringVar(value="Quick Export")
            self.export_menu = ctk.CTkOptionMenu(
                doc_row, values=["Export .md", "Export .html", "Export .txt"],
                command=self._on_quick_export,
                variable=self.export_var,
                font=ctk.CTkFont(size=11), width=120, 
                fg_color=theme.COLORS_DARK.get("bg_widget", "#3A3D4E"),
                button_color=theme.COLORS_DARK.get("bg_widget", "#3A3D4E")
            )
            self.export_menu.pack(side="left", padx=(12, 0))


            # Decoder info
            self.info_text = ctk.CTkTextbox(scroll, height=60, wrap="word", font=mono)
            self.info_text.pack(fill="x", padx=16, pady=4)
            self.info_text.configure(state="disabled")

            # Results display
            self.result_text = ctk.CTkTextbox(scroll, height=230, wrap="word", font=mono)
            self.result_text.pack(fill="both", expand=True, padx=16, pady=(4, 16))
            self.result_text.insert("1.0", "Results will appear here.")
            self.result_text.configure(state="disabled")

            # Experiment history
            ctk.CTkLabel(
                scroll, text="Experiment History",
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(8, 2))
            self.history_text = ctk.CTkTextbox(scroll, height=100, wrap="word", font=mono)
            self.history_text.pack(fill="x", padx=16, pady=(0, 8))
            self.history_text.insert("1.0", "No experiments yet.")
            self.history_text.configure(state="disabled")

            self._update_decoder_info()
            self._refresh_options_panel(self.decoder_var.get())

        def _on_clear_cache(self) -> None:
            ok = be.clear_decoder_cache()
            msg = "Decoder cache cleared successfully." if ok else "Decoder cache clear skipped."
            self._log(msg, "INFO" if ok else "WARN")
            self._set_text(self.result_text, msg)

        def _on_compare(self) -> None:
            """Run the current syndrome against ALL decoders and show side-by-side results."""
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first.")
                return
            rate = self.rate_var.get()
            seed_text = self.seed_entry.get().strip()
            valid, msg = utils.validate_int(seed_text, min_val=0, max_val=_MAX_SEED)
            if not valid:
                self._set_text(self.result_text, f"Invalid seed: {msg}")
                return
            seed = int(seed_text)

            self._set_text(self.result_text, "Running comparison across all decoders...")
            self._log("Starting decoder comparison", "INFO")

            def _worker():
                results = []
                for kind in be.DECODER_KINDS:
                    try:
                        out = be.run_single_decode(code, rate, kind, seed)
                        results.append({
                            "kind": kind,
                            "hamming_weight": out["hamming_weight"],
                            "syndrome_valid": out["syndrome_valid"],
                            "logical_failure": out.get("logical_failure"),
                            "error": None,
                        })
                    except Exception as e:
                        results.append({
                            "kind": kind,
                            "error": str(e),
                        })
                self._ui.post(self._render_compare, results)

            import threading
            threading.Thread(target=_worker, daemon=True).start()

        def _render_compare(self, results: list) -> None:
            """Render comparison results side-by-side."""
            header = f"{'Decoder':<25} {'HW':>5} {'Syn':>5} {'Logical':>10}  Status\n"
            header += "─" * 65 + "\n"
            lines = [header]
            for r in results:
                if r.get("error"):
                    lines.append(f"{r['kind']:<25} {' - ':>5} {' - ':>5} {' - ':>10}  ✗ {r['error'][:40]}\n")
                else:
                    lf = r["logical_failure"]
                    lf_s = "N/A" if lf is None else ("FAIL" if lf else "ok")
                    syn_s = "✓" if r["syndrome_valid"] else "✗"
                    lines.append(f"{r['kind']:<25} {r['hamming_weight']:>5} {syn_s:>5} {lf_s:>10}  ✓\n")
            self._set_text(self.result_text, "".join(lines))
            self._log(f"Comparison complete: {len(results)} decoders tested", "SUCCESS")
            try:
                from history_tab import record_event
                kinds = [r["kind"] for r in results]
                record_event("compare", {
                    "decoders": kinds,
                    "rate": self.rate_var.get() if hasattr(self, "rate_var") else 0,
                    "seed": int(self.seed_entry.get() or 0) if hasattr(self, "seed_entry") else 0,
                })
            except Exception:
                pass

        def _on_import_syndrome(self) -> None:
            """Import a syndrome from a CSV/JSON/text file and decode it."""
            import tkinter.filedialog as fd
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first.")
                self._log("No active code for syndrome import", "WARN")
                return
            path = fd.askopenfilename(
                title="Import Syndrome",
                filetypes=[("Text data", "*.txt *.csv *.json"), ("All Files", "*.*")],
            )
            if not path:
                return
            self._read_imported_syndrome(code, path)

        def _read_imported_syndrome(self, code, path: str) -> None:
            import json as _json
            from pathlib import Path as _Path
            raw = None
            try:
                text = _Path(path).read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                self._set_text(self.result_text, f"Failed to read '{path}': {e}")
                self._log(f"Failed to read '{path}': {e}", "ERROR")
                return
            try:
                if path.lower().endswith(".json"):
                    data = _json.loads(text)
                    syn = self._extract_syndrome(data)
                else:
                    syn = self._extract_syndrome(text)
                if syn is None:
                    raise ValueError("no 0/1 syndrome sequence found")
            except Exception as exc:
                self._set_text(self.result_text, f"Failed to parse syndrome data: {exc}\nExpected a CSV row or JSON array of 0/1 integers.")
                self._log(f"Failed to parse syndrome data from '{path}': {exc}", "ERROR")
                return
            try:
                expected = code.n_checks if hasattr(code, "n_checks") else None
                if expected is not None and len(syn) != expected:
                    raise ValueError(
                        f"syndrome has {len(syn)} entries but the active code has {expected} checks"
                    )
            except Exception as exc:
                self._set_text(self.result_text, f"Syndrome length mismatch: {exc}")
                self._log(f"Syndrome import length mismatch: {exc}", "ERROR")
                return
            self._imported_syndrome = syn
            self._imported_syndrome_path = path
            self.result_text.configure(state="normal")
            self.result_text.delete("1.0", "end")
            self.result_text.insert("1.0", f"Imported syndrome from {path}\n\n{syn[:64]}{' ...' if len(syn) > 64 else ''}\n\nLength: {len(syn)} check(s). Ready to decode  -  press Run Decode (imported syndrome will be used).")
            self.result_text.configure(state="disabled")
            self._log(f"Imported syndrome: {len(syn)} entries from {path}", "SUCCESS")

        def _extract_syndrome(self, data) -> Optional[list]:
            """Pull a flat 0/1 integer sequence out of JSON/text byte arrays."""
            if isinstance(data, str):
                parsed = []
                import re as _re
                for chunk in _re.split(r"[,\s]+", data.strip().strip("][()")):
                    if not chunk:
                        continue
                    try:
                        parsed.append(int(chunk))
                    except ValueError:
                        parsed.clear()
                        break
                return parsed or None
            if isinstance(data, list):
                if not data:
                    return None
                try:
                    arr = [int(v) for v in data]
                except (TypeError, ValueError):
                    return None
                if not all(v in (0, 1) for v in arr):
                    return None
                return arr
            if isinstance(data, dict):
                for key in ("syndrome", "data", "values"):
                    if key in data:
                        found = self._extract_syndrome(data[key])
                        if found is not None:
                            return found
            return None

        def _on_generate_doc(self) -> None:
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first to generate documentation.")
                self._log("No active code for documentation generation", "WARN")
                return

            self._set_text(self.result_text, "Generating documentation... please wait.")
            try:
                self.gen_doc_btn.configure(state="disabled")
            except tkinter.TclError:
                pass
            threading_utils.run_in_background(self._generate_doc_worker, args=(code,))

        def _generate_doc_worker(self, code) -> None:
            try:
                from doc_generator import ProfessionalDocGenerator
                generator = ProfessionalDocGenerator()
                results = generator.generate_all(code, formats=["markdown", "html", "pdf"])
                ok = [f for f, (good, _) in results.items() if good]
                bad = [f for f, (good, _) in results.items() if not good]
                paths = [p for good, p in results.values() if good]
                outdir = str(paths[0].parent) if paths else str(generator.output_dir)
                if not ok:
                    raise RuntimeError(f"no format could be written ({', '.join(bad)})")
                message = f"Documentation generated ({', '.join(ok)}) in:\n{outdir}"
                if bad:
                    message += f"\n\nFailed: {', '.join(bad)} (see Console for details)"
                self._ui.post(self._on_generate_doc_done, True, message)
                self._log(f"Documentation generated: {', '.join(ok)} -> {outdir}", "SUCCESS")
            except Exception as e:
                self._log(f"Documentation generation failed: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_generate_doc_done, False, f"Documentation generation failed: {e}")

        def _on_generate_doc_done(self, success: bool, message: str) -> None:
            try:
                self._set_text(self.result_text, message)
                self.gen_doc_btn.configure(state="normal")
            except tkinter.TclError:
                pass

        def _on_quick_export(self, format_choice: str) -> None:
            import tkinter.filedialog as fd
            import datetime
            
            self.export_var.set("Quick Export")
            
            content = ""
            try:
                content = self.result_text.get("1.0", "end-1c")
            except tkinter.TclError:
                pass
                
            # Only a real decode result is exportable. The result pane also
            # carries placeholders and doc-generation notices; writing one of
            # those out as a "Decode Report" would be a fabricated record.
            if not content or self._last_decode_result is None or any(
                marker in content for marker in (
                    "Results will appear here",
                    "Generating documentation",
                    "Documentation generated",
                    "Documentation generation failed",
                    "No active code",
                    "Decoder cache",
                )
            ):
                self._log("Nothing to export: run a decode first.", "WARN")
                self._set_text(self.result_text, "Nothing to export: run a decode first.")
                return

            ext = ".txt"
            if ".md" in format_choice.lower():
                ext = ".md"
            elif ".html" in format_choice.lower():
                ext = ".html"
                
            filetypes = [(f"{ext[1:].upper()} File", f"*{ext}"), ("All Files", "*.*")]
            
            try:
                filepath = fd.asksaveasfilename(
                    title="Export Decode Result",
                    defaultextension=ext,
                    filetypes=filetypes
                )
                if not filepath:
                    return
                ok_path, filepath = utils.sanitize_export_path(filepath)
                if not ok_path:
                    self._log("Export rejected: directory traversal is not allowed.", "ERROR")
                    self._set_text(self.result_text, "Export rejected: directory traversal is not allowed.")
                    return

                output = f"Decode Report\n=============\n\nTimestamp: {datetime.datetime.now().isoformat()}\n\n"
                
                if self._last_decode_result:
                    payload = self._last_decode_result
                    output += f"Decoder: {payload.get('kind', 'N/A')}\n"
                    output += f"Error Rate: {payload.get('rate', 'N/A')}\n"
                    output += f"Seed: {payload.get('seed', 'N/A')}\n\n"
                    
                if ext == ".html":
                    output = f"<html><body><pre>{output}{content}</pre></body></html>"
                else:
                    output += content
                    
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(output)
                    
                self._log(f"Exported decode result to {filepath}", "SUCCESS")
            except Exception as e:
                self._log(f"Export failed: {e}", "ERROR")


        # ── contextual options panel ─────────────────────────────────
        def _build_options_panels(self) -> None:
            """Create the (initially hidden) per-decoder option sub-panels."""
            bold = ctk.CTkFont(size=12, weight="bold")
            small = ctk.CTkFont(size=11)

            # BP-OSD: belief-propagation method + OSD order + damping + OSD lambda.
            self._bposd_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(self._bposd_panel, text="BP method:", font=bold).pack(side="left")
            self.bp_method_var = ctk.StringVar(value="Exact")
            ctk.CTkOptionMenu(
                self._bposd_panel, values=list(_BP_METHODS.keys()),
                variable=self.bp_method_var, width=110,
            ).pack(side="left", padx=(12, 16))
            ctk.CTkLabel(self._bposd_panel, text="OSD order:", font=bold).pack(side="left")
            self.osd_order_var = ctk.StringVar(value="0")
            ctk.CTkOptionMenu(
                self._bposd_panel, values=list(_OSD_ORDERS),
                variable=self.osd_order_var, width=70,
            ).pack(side="left", padx=(12, 16))
            ctk.CTkLabel(self._bposd_panel, text="Damping:", font=bold).pack(side="left")
            self.damping_var = ctk.DoubleVar(value=0.5)
            self.damping_slider = ctk.CTkSlider(
                self._bposd_panel, from_=0.0, to=1.0, number_of_steps=20,
                variable=self.damping_var, width=100,
            )
            self.damping_slider.pack(side="left", padx=(8, 4))
            self.damping_label = ctk.CTkLabel(self._bposd_panel, text="0.50", font=small)
            self.damping_label.pack(side="left", padx=(0, 12))
            self.damping_var.trace_add("write", lambda *_: self._update_damping_label())
            ctk.CTkLabel(self._bposd_panel, text="OSD λ:", font=bold).pack(side="left")
            self.osd_lambda_var = ctk.StringVar(value="0")
            self.osd_lambda_entry = ctk.CTkEntry(self._bposd_panel, width=50)
            self.osd_lambda_entry.insert(0, "0")
            self.osd_lambda_entry.pack(side="left", padx=(8, 0))

            # Hybrid cascade: escalation decoder + batch cascade statistics.
            self._cascade_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(self._cascade_panel, text="Escalation:", font=bold).pack(side="left")
            self.escalation_var = ctk.StringVar(value="Blossom")
            ctk.CTkOptionMenu(
                self._cascade_panel, values=list(_ESCALATIONS.keys()),
                variable=self.escalation_var, width=110,
            ).pack(side="left", padx=(12, 16))
            ctk.CTkLabel(self._cascade_panel, text="Stats samples:", font=bold).pack(side="left")
            self.cascade_samples_entry = ctk.CTkEntry(self._cascade_panel, width=70)
            self.cascade_samples_entry.insert(0, "200")
            self.cascade_samples_entry.pack(side="left", padx=(12, 16))
            self.cascade_stats_btn = ctk.CTkButton(
                self._cascade_panel, text="Cascade Stats",
                command=self._on_cascade_stats, font=ctk.CTkFont(size=11), width=110,
            )
            self.cascade_stats_btn.pack(side="left")

            # Neural paths: info note + neural predecoder training (research).
            self._neural_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(
                self._neural_panel,
                text=("This path uses GNN / belief-propagation-derived weights "
                      "with a faithfulness fallback."),
                font=small, text_color=theme.c("text_secondary"),
                wraplength=900, justify="left",
            ).pack(fill="x", pady=(2, 4))
            train_row = ctk.CTkFrame(self._neural_panel, fg_color="transparent")
            train_row.pack(fill="x", pady=(0, 2))
            ctk.CTkLabel(
                train_row, text="Train neural predecoder (research):", font=bold,
            ).pack(side="left")
            ctk.CTkLabel(train_row, text="Samples:", font=small).pack(side="left", padx=(12, 0))
            self.train_samples_entry = ctk.CTkEntry(train_row, width=70)
            self.train_samples_entry.insert(0, "500")
            self.train_samples_entry.pack(side="left", padx=(6, 12))
            ctk.CTkLabel(train_row, text="Epochs:", font=small).pack(side="left")
            self.train_epochs_entry = ctk.CTkEntry(train_row, width=50)
            self.train_epochs_entry.insert(0, "3")
            self.train_epochs_entry.pack(side="left", padx=(6, 12))
            self.train_btn = ctk.CTkButton(
                train_row, text="Train Predecoder",
                command=self._on_neural_train, font=ctk.CTkFont(size=11), width=120,
            )
            self.train_btn.pack(side="left")

            # Two-stage: decoupled X/Z decoders
            self._twostage_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(self._twostage_panel, text="X-Decoder:", font=bold).pack(side="left")
            self.twostage_x_var = ctk.StringVar(value="Blossom")
            ctk.CTkOptionMenu(
                self._twostage_panel, values=["Blossom", "Union_Find", "BP_OSD"],
                variable=self.twostage_x_var, width=100,
            ).pack(side="left", padx=(8, 16))
            ctk.CTkLabel(self._twostage_panel, text="Z-Decoder:", font=bold).pack(side="left")
            self.twostage_z_var = ctk.StringVar(value="Blossom")
            ctk.CTkOptionMenu(
                self._twostage_panel, values=["Blossom", "Union_Find", "BP_OSD"],
                variable=self.twostage_z_var, width=100,
            ).pack(side="left", padx=(8, 0))

            # Ambiguity cluster: threshold + max cluster size
            self._ambiguity_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(self._ambiguity_panel, text="Ambig Threshold:", font=bold).pack(side="left")
            self.ambig_threshold_entry = ctk.CTkEntry(self._ambiguity_panel, width=60)
            self.ambig_threshold_entry.insert(0, "0.5")
            self.ambig_threshold_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(self._ambiguity_panel, text="Max Cluster Size:", font=bold).pack(side="left")
            self.ambig_clustersize_entry = ctk.CTkEntry(self._ambiguity_panel, width=60)
            self.ambig_clustersize_entry.insert(0, "12")
            self.ambig_clustersize_entry.pack(side="left", padx=(8, 0))

            # Colour code: max iterations + OSD order + Method
            self._colourcode_panel = ctk.CTkFrame(self.options_frame, fg_color="transparent")
            ctk.CTkLabel(self._colourcode_panel, text="Method:", font=bold).pack(side="left")
            self.cc_method_var = ctk.StringVar(value="bposd")
            ctk.CTkOptionMenu(
                self._colourcode_panel, values=["bposd", "cluster_bposd"],
                variable=self.cc_method_var, width=130,
            ).pack(side="left", padx=(8, 16))
            ctk.CTkLabel(self._colourcode_panel, text="Max Iter:", font=bold).pack(side="left")
            self.cc_maxiter_entry = ctk.CTkEntry(self._colourcode_panel, width=50)
            self.cc_maxiter_entry.insert(0, "30")
            self.cc_maxiter_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(self._colourcode_panel, text="OSD Order:", font=bold).pack(side="left")
            self.cc_osdorder_var = ctk.StringVar(value="0")
            ctk.CTkOptionMenu(
                self._colourcode_panel, values=list(_OSD_ORDERS),
                variable=self.cc_osdorder_var, width=70,
            ).pack(side="left", padx=(8, 0))

        def _refresh_options_panel(self, kind: str) -> None:
            """Show the option sub-panel matching the selected decoder kind."""
            for panel in (self._bposd_panel, self._cascade_panel, self._neural_panel,
                          self._twostage_panel, self._ambiguity_panel, self._colourcode_panel):
                try:
                    panel.pack_forget()
                except Exception:
                    pass
            target = None
            if kind == "bp_osd":
                target = self._bposd_panel
            elif kind == "hybrid_cascade":
                target = self._cascade_panel
            elif kind in _NEURAL_KINDS:
                target = self._neural_panel
            elif kind == "two_stage":
                target = self._twostage_panel
            elif kind == "ambiguity_cluster":
                target = self._ambiguity_panel
            elif kind == "colour_code":
                target = self._colourcode_panel
            if target is not None:
                try:
                    target.pack(fill="x", pady=4)
                except tkinter.TclError:
                    pass

        def _update_damping_label(self, *_args) -> None:
            try:
                self.damping_label.configure(text=f"{self.damping_var.get():.2f}")
            except Exception:
                pass

        def _collect_decoder_options(self, kind: str, rate: float) -> dict[str, Any]:
            """Assemble the ``decoder_options`` dict for kinds that expose options."""
            try:
                if kind == "bp_osd":
                    osd_lambda_raw = self.osd_lambda_entry.get().strip()
                    try:
                        osd_lambda = int(osd_lambda_raw) if osd_lambda_raw else 0
                    except ValueError:
                        osd_lambda = 0
                    return {
                        "bp_method": _BP_METHODS.get(self.bp_method_var.get(), "exact"),
                        "osd_order": int(self.osd_order_var.get() or 0),
                        "damping": float(self.damping_var.get()),
                        "osd_lambda": osd_lambda,
                    }
                if kind == "hybrid_cascade":
                    return {
                        "escalation": _ESCALATIONS.get(self.escalation_var.get(), "blossom"),
                        "error_rate": float(rate),
                    }
                if kind == "two_stage":
                    return {
                        "x_decoder": self.twostage_x_var.get().lower(),
                        "z_decoder": self.twostage_z_var.get().lower(),
                    }
                if kind == "ambiguity_cluster":
                    return {
                        "error_rate": float(rate),
                        "ambig_threshold": float(self.ambig_threshold_entry.get() or 0.5),
                        "max_cluster_size": int(self.ambig_clustersize_entry.get() or 12),
                    }
                if kind == "colour_code":
                    return {
                        "method": self.cc_method_var.get(),
                        "max_iter": int(self.cc_maxiter_entry.get() or 30),
                        "osd_order": int(self.cc_osdorder_var.get() or 0),
                    }
            except Exception:
                pass
            return {}

        # ── helpers ────────────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        def _update_rate_label(self, *_args) -> None:
            try:
                self.rate_label.configure(text=f"{self.rate_var.get():.2f}")
            except (tkinter.TclError, ValueError):
                pass

        def _on_decoder_change(self, _choice: str = "") -> None:
            self._update_decoder_info()
            try:
                self._refresh_options_panel(self.decoder_var.get())
            except Exception:
                pass

        def _update_decoder_info(self) -> None:
            try:
                kind = self.decoder_var.get()
                info = be.get_decoder_info(kind)
                self._set_text(self.info_text, f"{info['name']}: {info['description']}")
            except Exception:
                pass

        # ── decode action ──────────────────────────────────────────────
        def _on_decode(self) -> None:
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first.")
                self._log("No active code for decode", "WARN")
                return

            kind = self.decoder_var.get()
            try:
                rate = float(self.rate_var.get())
            except (tkinter.TclError, ValueError):
                self._set_text(self.result_text, "Invalid error rate  -  use the slider to pick a value.")
                return
            seed_text = self.seed_entry.get().strip()
            valid, msg = utils.validate_int(seed_text, min_val=0, max_val=_MAX_SEED)
            if not valid:
                self._set_text(self.result_text, f"Invalid seed: {msg}\nEnter an integer between 0 and {_MAX_SEED}.")
                return
            seed = int(seed_text)

            # Snapshot code identity on the UI thread for the resilient path.
            family = getattr(self.state, "current_family_key", None) if self.state else None
            distance = getattr(self.state, "current_param", None) if self.state else None
            try:
                resilient = bool(self.resilient_var.get())
            except Exception:
                resilient = True
            options = self._collect_decoder_options(kind, rate)

            self._run_seq += 1
            seq = self._run_seq
            try:
                self.decode_btn.configure(state="disabled")
            except tkinter.TclError:
                return
            threading_utils.run_in_background(
                self._decode_worker,
                args=(seq, code, family, distance, rate, kind, seed, resilient, options),
            )

        def _decode_worker(self, seq: int, code, family, distance, rate: float,
                            kind: str, seed: int, resilient: bool,
                            options: Optional[dict] = None) -> None:
            try:
                options = options or {}
                imported = getattr(self, "_imported_syndrome", None)
                if imported is not None:
                    out = be.decode_syndrome(code, imported, kind, decoder_options=options) if options and _supports_decoder_options() else be.decode_syndrome(code, imported, kind)
                    source = f"imported syndrome ({len(imported)} checks)"
                else:
                    source = f"sampled error (p={rate}, seed={seed})"
                    options_applied = bool(options)
                    if options and _supports_decoder_options():
                        out = be.run_single_decode(code, rate, kind, seed, decoder_options=options)
                    else:
                        if options:
                            options_applied = False
                        out = be.run_single_decode(code, rate, kind, seed)
                result = out["result"]
                if imported is not None:
                    payload = {
                        "kind": kind,
                        "rate": rate,
                        "seed": seed,
                        "options": options,
                        "options_applied": bool(options),
                        "source": source,
                        "hamming_weight": result.hamming_weight,
                        "syndrome_valid": result.syndrome_valid,
                        "logical_failure": None,
                        "error_str": "",
                        "syndrome_str": np.array2string(np.asarray(out["syndrome"])[:24], max_line_width=68),
                        "correction_str": np.array2string(np.asarray(result.correction)[:24], max_line_width=68),
                    }
                else:
                    payload = {
                        "kind": kind,
                        "rate": rate,
                        "seed": seed,
                        "options": options,
                        "options_applied": options_applied,
                        "source": source,
                        "hamming_weight": result.hamming_weight,
                        "syndrome_valid": result.syndrome_valid,
                        "logical_failure": result.logical_failure,
                        "error_str": np.array2string(np.asarray(out["error"])[:24], max_line_width=68),
                        "syndrome_str": np.array2string(np.asarray(out["syndrome"])[:24], max_line_width=68),
                        "correction_str": np.array2string(np.asarray(result.correction)[:24], max_line_width=68),
                    }
                self._ui.post(self._on_decode_done, seq, payload)
            except be.QectorError as e:
                # The requested decoder cannot handle this code.  When resilient
                # fallback is enabled, recover with a compatible decoder and
                # report exactly what happened rather than just failing.
                if resilient and family and distance and imported is None:
                    try:
                        import autodebug
                        res = autodebug.resilient_single_decode(
                            str(family), int(distance), error_rate=rate,
                            decoder=kind, seed=seed).to_dict()
                        if res.get("success"):
                            self._ui.post(self._on_decode_fallback, seq, res)
                            return
                    except Exception:
                        pass
                self._log(f"Decode failed: {e}", "ERROR")
                self._ui.post(self._on_decode_failed, seq, f"Decode error: {e}")
            except Exception as e:
                self._log(f"Unexpected decode error: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_decode_failed, seq, f"Unexpected decode error: {e}")

        def _on_decode_done(self, seq: int, p: dict[str, Any]) -> None:
            if seq != self._run_seq:
                return
            self._last_decode_result = p
            try:
                lf = p["logical_failure"]
                lf_str = "N/A (code exposes no logicals matrix)" if lf is None else ("YES" if lf else "no")
                opt_line = ""
                if p.get("options"):
                    if p.get("options_applied"):
                        opts = ", ".join(f"{k}={v}" for k, v in p["options"].items())
                        opt_line = f"Options:         {opts}\n"
                    else:
                        opt_line = (
                            "Options:         NOT APPLIED  -  this backend build does not\n"
                            "                 accept decoder_options; decoder defaults used.\n"
                        )
                text = (
                    f"Decoder:         {p['kind']}\n"
                    f"Source:          {p.get('source', 'sampled error')}\n"
                    f"Error rate:      {p['rate']:.2f}\n"
                    f"Seed:            {p['seed']}\n"
                    f"{opt_line}"
                    f"Hamming weight:  {p['hamming_weight']}\n"
                    f"Syndrome valid:  {'yes' if p['syndrome_valid'] else 'NO'}\n"
                    f"Logical failure: {lf_str}\n\n"
                )
                if p.get("source", "").startswith("imported"):
                    text += (
                        f"Syndrome (first 24):   {p['syndrome_str']}\n"
                        f"Correction (first 24): {p['correction_str']}\n"
                    )
                else:
                    text += (
                        f"Error (first 24):      {p['error_str']}\n"
                        f"Syndrome (first 24):   {p['syndrome_str']}\n"
                        f"Correction (first 24): {p['correction_str']}\n"
                    )
                self._set_text(self.result_text, text)
                self._log(
                    f"Decode {p['kind']}: hw={p['hamming_weight']} "
                    f"syndrome_valid={p['syndrome_valid']} logical_failure={lf}",
                    "SUCCESS",
                )
                self._log_history(p)
                try:
                    from history_tab import record_event
                    record_event("decode", {
                        "decoder": p.get("kind", "?"),
                        "rate": p.get("rate", 0),
                        "seed": p.get("seed", 0),
                        "hw": p.get("hamming_weight", 0),
                        "syn_valid": p.get("syndrome_valid", False),
                    })
                except Exception:
                    pass
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _log_history(self, p: dict) -> None:
            """Append a decode result to the experiment history panel."""
            import datetime
            entry = {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "decoder": p.get("kind", "?"),
                "rate": p.get("rate", 0),
                "seed": p.get("seed", 0),
                "hw": p.get("hamming_weight", 0),
                "syn_valid": p.get("syndrome_valid", False),
                "logical_failure": p.get("logical_failure"),
            }
            self._history.append(entry)
            # Render full history
            lines = []
            for i, h in enumerate(reversed(self._history), 1):
                lf = h["logical_failure"]
                lf_s = "N/A" if lf is None else ("FAIL" if lf else "ok")
                lines.append(
                    f"#{len(self._history) - i + 1}  {h['time']}  "
                    f"{h['decoder']:20s}  p={h['rate']:.2f}  seed={h['seed']}  "
                    f"hw={h['hw']}  syn={'✓' if h['syn_valid'] else '✗'}  "
                    f"logical={lf_s}"
                )
            self._set_text(self.history_text, "\n".join(lines))

        def _on_decode_fallback(self, seq: int, res: dict) -> None:
            """Render a resilient-fallback decode (requested decoder unusable)."""
            if seq != self._run_seq:
                return
            try:
                used = res.get("used_decoder")
                lf = res.get("logical_failure")
                lf_str = "N/A (code exposes no logicals matrix)" if lf is None else ("YES" if lf else "no")
                lines = []
                for a in res.get("attempts", []):
                    if a.get("ok") and a.get("syndrome_valid") is not False:
                        lines.append(f"  - {a.get('method')}: ok (valid={a.get('syndrome_valid')})")
                    elif a.get("ok"):
                        lines.append(f"  - {a.get('method')}: invalid correction")
                    else:
                        lines.append(f"  - {a.get('method')}: unusable ({a.get('error')})")
                text = (
                    f"Requested decoder: {res.get('requested_decoder')}  "
                    f"(cannot decode this code)\n"
                    f"Recovered with:    {used}   [resilient fallback]\n"
                    f"Hamming weight:    {res.get('hamming_weight')}\n"
                    f"Syndrome valid:    {'yes' if res.get('syndrome_valid') else 'NO'}\n"
                    f"Logical failure:   {lf_str}\n\n"
                    f"{res.get('message', '')}\n\n"
                    f"Attempt trace:\n" + "\n".join(lines) + "\n"
                )
                self._set_text(self.result_text, text)
                self._log(
                    f"Resilient fallback: {res.get('requested_decoder')} -> {used}", "WARN"
                )
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _on_decode_failed(self, seq: int, message: str) -> None:
            if seq != self._run_seq:
                return
            try:
                self._set_text(self.result_text, message)
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        # ── hybrid-cascade batch statistics ──────────────────────────
        def _on_cascade_stats(self) -> None:
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first.")
                self._log("No active code for cascade stats", "WARN")
                return
            n_text = self.cascade_samples_entry.get().strip()
            valid, msg = utils.validate_int(n_text, min_val=1, max_val=_MAX_STATS_SAMPLES)
            if not valid:
                self._set_text(
                    self.result_text,
                    f"Invalid stats sample count: {msg}\nEnter an integer between 1 and {_MAX_STATS_SAMPLES}.",
                )
                return
            n = int(n_text)
            seed_text = self.seed_entry.get().strip()
            valid, msg = utils.validate_int(seed_text, min_val=0, max_val=_MAX_SEED)
            if not valid:
                self._set_text(self.result_text, f"Invalid seed: {msg}\nEnter an integer between 0 and {_MAX_SEED}.")
                return
            seed = int(seed_text)
            try:
                rate = float(self.rate_var.get())
            except (tkinter.TclError, ValueError):
                self._set_text(self.result_text, "Invalid error rate  -  use the slider to pick a value.")
                return
            escalation = _ESCALATIONS.get(self.escalation_var.get(), "blossom")

            self._stats_seq += 1
            seq = self._stats_seq
            self._set_stats_buttons(False)
            self._set_text(self.result_text, f"Running hybrid-cascade stats ({n} samples, escalation={escalation}) ...")
            threading_utils.run_in_background(
                self._cascade_stats_worker, args=(seq, code, n, rate, seed, escalation)
            )

        def _cascade_stats_worker(self, seq: int, code, n: int, rate: float,
                                  seed: int, escalation: str) -> None:
            fn = getattr(be, "run_hybrid_cascade_stats", None)
            if fn is None:
                self._ui.post(
                    self._on_stats_failed, seq,
                    "run_hybrid_cascade_stats is not available in this backend build yet.",
                )
                return
            try:
                try:
                    stats = fn(code, n, rate, seed, escalation=escalation)
                except TypeError:
                    # Backend build without the escalation kwarg  -  run with its default.
                    stats = fn(code, n, rate, seed)
                payload = dict(stats) if isinstance(stats, dict) else {"result": str(stats)}
                self._ui.post(self._on_stats_done, seq, "hybrid_cascade batch statistics", payload)
            except Exception as e:
                self._log(f"Cascade stats failed: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_stats_failed, seq, f"Cascade stats failed: {e}")

        # ── neural predecoder training (research feature) ────────────
        def _on_neural_train(self) -> None:
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_text(self.result_text, "No active code. Build a code in Code Explorer first.")
                self._log("No active code for predecoder training", "WARN")
                return
            n_text = self.train_samples_entry.get().strip()
            valid, msg = utils.validate_int(n_text, min_val=1, max_val=_MAX_TRAIN_SAMPLES)
            if not valid:
                self._set_text(
                    self.result_text,
                    f"Invalid training sample count: {msg}\nEnter an integer between 1 and {_MAX_TRAIN_SAMPLES}.",
                )
                return
            n = int(n_text)
            ep_text = self.train_epochs_entry.get().strip()
            valid, msg = utils.validate_int(ep_text, min_val=1, max_val=_MAX_TRAIN_EPOCHS)
            if not valid:
                self._set_text(
                    self.result_text,
                    f"Invalid epoch count: {msg}\nEnter an integer between 1 and {_MAX_TRAIN_EPOCHS}.",
                )
                return
            epochs = int(ep_text)
            seed_text = self.seed_entry.get().strip()
            valid, msg = utils.validate_int(seed_text, min_val=0, max_val=_MAX_SEED)
            if not valid:
                self._set_text(self.result_text, f"Invalid seed: {msg}\nEnter an integer between 0 and {_MAX_SEED}.")
                return
            seed = int(seed_text)
            try:
                rate = float(self.rate_var.get())
            except (tkinter.TclError, ValueError):
                self._set_text(self.result_text, "Invalid error rate  -  use the slider to pick a value.")
                return

            self._stats_seq += 1
            seq = self._stats_seq
            self._set_stats_buttons(False)
            self._set_text(self.result_text, f"Training neural predecoder ({n} samples, {epochs} epochs) ...")
            threading_utils.run_in_background(
                self._neural_train_worker, args=(seq, code, n, epochs, rate, seed)
            )

        def _neural_train_worker(self, seq: int, code, n: int, epochs: int,
                                 rate: float, seed: int) -> None:
            fn = getattr(be, "run_neural_predecoder_training", None)
            if fn is None:
                self._ui.post(
                    self._on_stats_failed, seq,
                    "run_neural_predecoder_training is not available in this backend build yet.",
                )
                return
            try:
                result = fn(code, n, epochs, rate, seed)
                payload = dict(result) if isinstance(result, dict) else {"result": str(result)}
                self._ui.post(self._on_stats_done, seq, "neural predecoder training", payload)
            except Exception as e:
                self._log(f"Predecoder training failed: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_stats_failed, seq, f"Predecoder training failed: {e}")

        # ── shared stats/training completion ─────────────────────────
        def _on_stats_done(self, seq: int, title: str, stats: dict) -> None:
            if seq != self._stats_seq:
                return
            try:
                lines = [f"{title}", "-" * 34]
                for key, value in stats.items():
                    if isinstance(value, float):
                        value = f"{value:.4f}"
                    lines.append(f"{key}: {value}")
                self._set_text(self.result_text, "\n".join(lines))
                self._log(f"{title}: {stats}", "SUCCESS")
            except tkinter.TclError:
                pass
            finally:
                self._reenable_stats(seq)

        def _on_stats_failed(self, seq: int, message: str) -> None:
            if seq != self._stats_seq:
                return
            try:
                self._set_text(self.result_text, message)
            except tkinter.TclError:
                pass
            finally:
                self._reenable_stats(seq)

        def _set_stats_buttons(self, enabled: bool) -> None:
            state = "normal" if enabled else "disabled"
            for btn in (self.cascade_stats_btn, self.train_btn):
                try:
                    btn.configure(state=state)
                except tkinter.TclError:
                    pass

        def _reenable_stats(self, seq: int) -> None:
            if seq != self._stats_seq:
                return
            self._set_stats_buttons(True)

        def _reenable(self, seq: int) -> None:
            if seq != self._run_seq:
                return
            try:
                self.decode_btn.configure(state="normal")
            except tkinter.TclError:
                pass

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
    class DecoderLabTab:
        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            pass
