"""batch_streaming_tab.py  -  Batch & Streaming tab for QECTOR Workbench.

Batch decode on cpu/cuda/opencl backends (backend errors surfaced verbatim,
no silent fallback) and real sliding-window streaming sessions with decoder
selection.  Both run in background threads; results include an embedded
matplotlib chart (hamming-weight histogram for batch runs, per-round
committed-weight chart for streaming sessions).
"""

from __future__ import annotations

import tkinter
import html as _html
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
_MAX_BATCH_SAMPLES = 100_000
_MAX_ROUNDS = 100_000
_MAX_WINDOW = 10_000
_BATCH_BACKENDS = ["cpu", "cuda", "cuda_bposd", "opencl", "cpu_parallel"]
# Decoder types the cpu_parallel process pool (backend.run_parallel_batch_decode)
# actually accepts  -  deliberately NOT derived from DECODER_KINDS: offering kinds
# the pool would reject would be a UI lie.
_POOL_DECODERS = ["union_find", "fast_union_find", "blossom", "sparse_blossom", "bp_osd"]


if _HAS_GUI:

    class BatchStreamingTab(ctk.CTkFrame):
        """Batch & streaming decode panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._batch_seq = 0
            self._stream_seq = 0
            self._batch_queue = []
            self._batch_running = False

            self.grid_columnconfigure(0, weight=0)
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(1, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)
            bold = ctk.CTkFont(size=12, weight="bold")

            # ── Controls (row 0, spans both columns) ──────────────────
            controls = ctk.CTkFrame(self, fg_color="transparent")
            controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))

            ctk.CTkLabel(
                controls, text="Batch & Streaming",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", pady=(0, 2))
            ctk.CTkLabel(
                controls,
                text="Batch decode multiple error samples and run sliding-window streaming sessions.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
            ).pack(anchor="w", pady=(0, 8))

            # Batch section
            ctk.CTkLabel(controls, text="Batch Decode", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(2, 2))
            brow = ctk.CTkFrame(controls, fg_color="transparent")
            brow.pack(fill="x", pady=2)
            ctk.CTkLabel(brow, text="Samples:", font=bold).pack(side="left")
            self.batch_samples_entry = ctk.CTkEntry(brow, width=70)
            self.batch_samples_entry.insert(0, "100")
            self.batch_samples_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(brow, text="Error Rate:", font=bold).pack(side="left")
            self.batch_rate_entry = ctk.CTkEntry(brow, width=70)
            self.batch_rate_entry.insert(0, "0.05")
            self.batch_rate_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(brow, text="Seed:", font=bold).pack(side="left")
            self.batch_seed_entry = ctk.CTkEntry(brow, width=70)
            self.batch_seed_entry.insert(0, "1")
            self.batch_seed_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(brow, text="Backend:", font=bold).pack(side="left")
            self.backend_var = ctk.StringVar(value="cpu")
            self.backend_menu = ctk.CTkOptionMenu(
                brow, values=list(_BATCH_BACKENDS),
                variable=self.backend_var, width=120,
                command=self._on_backend_change,
            )
            self.backend_menu.pack(side="left", padx=(8, 16))
            # Pool decoder selector  -  only meaningful for cpu_parallel, so it
            # stays hidden unless that backend is chosen.
            self.pool_decoder_label = ctk.CTkLabel(brow, text="Pool decoder:", font=bold)
            self.pool_decoder_var = ctk.StringVar(value="union_find")
            self.pool_decoder_menu = ctk.CTkOptionMenu(
                brow, values=list(_POOL_DECODERS),
                variable=self.pool_decoder_var, width=130,
            )
            
            self.gpu_options_frame = ctk.CTkFrame(brow, fg_color="transparent")
            self.weighted_gpu_var = ctk.BooleanVar(value=False)
            self.weighted_gpu_check = ctk.CTkCheckBox(
                self.gpu_options_frame, text="Weighted GPU", variable=self.weighted_gpu_var,
                font=ctk.CTkFont(size=11), width=100,
            )
            self.weighted_gpu_check.pack(side="left", padx=(8, 4))
            self.precision_var = ctk.StringVar(value="f32")
            self.precision_menu = ctk.CTkOptionMenu(
                self.gpu_options_frame, values=["f32", "f64"],
                variable=self.precision_var, width=60,
            )
            self.precision_menu.pack(side="left", padx=(0, 16))

            self.batch_btn = ctk.CTkButton(
                brow, text="Queue Batch Decode", command=self._on_batch,
                font=ctk.CTkFont(size=12, weight="bold"), width=150,
            )
            self.batch_btn.pack(side="left", padx=(0, 8))
            
            self.batch_progress = ctk.CTkProgressBar(brow, width=100)
            self.batch_progress.set(0.0)
            self.batch_progress.pack(side="left", padx=(0, 8))
            self.batch_progress_label = ctk.CTkLabel(brow, text="Idle", font=ctk.CTkFont(size=10), text_color=theme.c("text_secondary"))
            self.batch_progress_label.pack(side="left", padx=(0, 8))

            self.export_batch_doc_btn = ctk.CTkButton(
                brow, text="📊 Export Report", command=self._on_export_batch_report,
                font=ctk.CTkFont(size=12, weight="bold"), width=130,
                fg_color=theme.c("accent_dim"), hover_color=theme.c("accent"),
            )
            self.export_batch_doc_btn.pack(side="left")

            # Honest, live GPU availability note (probed in the background so
            # construction never blocks on CUDA/OpenCL detection).
            self.backend_status_label = ctk.CTkLabel(
                controls, text="Probing GPU backend availability ...",
                font=ctk.CTkFont(size=10), text_color=theme.c("text_secondary"),
            )
            self.backend_status_label.pack(anchor="w", pady=(0, 2))

            # Streaming section
            ctk.CTkLabel(controls, text="Streaming Session", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 2))
            srow = ctk.CTkFrame(controls, fg_color="transparent")
            srow.pack(fill="x", pady=2)
            ctk.CTkLabel(srow, text="Window:", font=bold).pack(side="left")
            self.stream_window_entry = ctk.CTkEntry(srow, width=60)
            self.stream_window_entry.insert(0, "5")
            self.stream_window_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(srow, text="Rounds:", font=bold).pack(side="left")
            self.stream_rounds_entry = ctk.CTkEntry(srow, width=60)
            self.stream_rounds_entry.insert(0, "20")
            self.stream_rounds_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(srow, text="Error Rate:", font=bold).pack(side="left")
            self.stream_rate_entry = ctk.CTkEntry(srow, width=60)
            self.stream_rate_entry.insert(0, "0.03")
            self.stream_rate_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(srow, text="Seed:", font=bold).pack(side="left")
            self.stream_seed_entry = ctk.CTkEntry(srow, width=60)
            self.stream_seed_entry.insert(0, "1")
            self.stream_seed_entry.pack(side="left", padx=(8, 16))
            ctk.CTkLabel(srow, text="Decoder:", font=bold).pack(side="left")
            self.stream_decoder_var = ctk.StringVar(value="union_find")
            ctk.CTkOptionMenu(
                srow, values=list(be.DECODER_KINDS),
                variable=self.stream_decoder_var, width=150,
            ).pack(side="left", padx=(8, 16))
            self.stream_btn = ctk.CTkButton(
                srow, text="Run Streaming Session", command=self._on_stream,
                font=ctk.CTkFont(size=12, weight="bold"), width=170,
            )
            self.stream_btn.pack(side="left")

            # ── Left column: results text ─────────────────────────────
            left = ctk.CTkFrame(self, fg_color="transparent", width=340)
            left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(4, 12))
            left.grid_propagate(False)
            left.grid_columnconfigure(0, weight=1)
            left.grid_rowconfigure(0, weight=1)
            self.result_text = ctk.CTkTextbox(left, wrap="word", font=mono)
            self.result_text.grid(row=0, column=0, sticky="nsew")
            self.result_text.insert("1.0", "Run batch or streaming decode to see results.")
            self.result_text.configure(state="disabled")

            # ── Right column: matplotlib canvas (one shared figure) ───
            right = ctk.CTkFrame(self, fg_color=theme.c("bg_panel"))
            right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(4, 12))
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(0, weight=1)

            theme.configure_matplotlib()
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

            self._figure = Figure(figsize=(6.8, 4.2), dpi=140)
            theme.style_dark_figure(self._figure)
            self._mpl_canvas = FigureCanvasTkAgg(self._figure, master=right)
            self._mpl_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            self._pending_redraw = False
            self._draw_placeholder("Run a batch decode or streaming session to see charts.")

            # Probe GPU availability off the UI thread; the label updates when done.
            threading_utils.run_in_background(self._backend_probe_worker)

        # ── backend availability probe ───────────────────────────────
        def _backend_probe_worker(self) -> None:
            cuda: Optional[bool] = None
            opencl: Optional[bool] = None
            try:
                import qector_decoder_v3 as qd
                cuda = bool(qd.cuda_is_available())
            except Exception:
                cuda = None
            try:
                import qector_decoder_v3 as qd
                opencl = bool(qd.opencl_is_available())
            except Exception:
                opencl = None
            self._ui.post(self._on_backend_probe, cuda, opencl)

        def _on_backend_probe(self, cuda: Optional[bool], opencl: Optional[bool]) -> None:
            def _fmt(flag: Optional[bool]) -> str:
                if flag is None:
                    return "unknown"
                return "available" if flag else "unavailable"
            try:
                self.backend_status_label.configure(
                    text=(
                        f"Backend availability  -  cpu: available | cpu_parallel: process pool | "
                        f"cuda: {_fmt(cuda)} | opencl: {_fmt(opencl)}.  "
                        "Unavailable backends report their error verbatim on run."
                    )
                )
            except tkinter.TclError:
                pass

        def _on_backend_change(self, choice: str = "") -> None:
            """Show the pool-decoder selector only when cpu_parallel is chosen."""
            try:
                if choice == "cpu_parallel":
                    self.pool_decoder_label.pack(side="left", before=self.batch_btn)
                    self.pool_decoder_menu.pack(side="left", padx=(8, 16), before=self.batch_btn)
                else:
                    self.pool_decoder_label.pack_forget()
                    self.pool_decoder_menu.pack_forget()
                
                if choice in ("cuda", "cuda_bposd", "opencl"):
                    self.gpu_options_frame.pack(side="left", before=self.batch_btn)
                else:
                    self.gpu_options_frame.pack_forget()
            except tkinter.TclError:
                pass

        # ── helpers ────────────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        def _current_code(self):
            code = self.state.current_code if self.state else None
            if code is None:
                self._set_result_text("No active code. Build a code in Code Explorer first.")
                self._log("No active code for batch/streaming", "WARN")
            return code

        def _read_int(self, entry, label: str, min_val: int, max_val: int) -> Optional[int]:
            text = entry.get().strip()
            valid, msg = utils.validate_int(text, min_val=min_val, max_val=max_val)
            if not valid:
                self._set_result_text(
                    f"Invalid {label}: {msg}\nEnter an integer between {min_val} and {max_val}."
                )
                return None
            return int(text)

        def _read_rate(self, entry, label: str) -> Optional[float]:
            text = entry.get().strip()
            try:
                rate = float(text)
            except ValueError:
                self._set_result_text(f"Invalid {label}: {text!r}\nEnter a number between 0 and 1 (e.g. 0.05).")
                return None
            if not (0.0 < rate < 1.0):
                self._set_result_text(f"{label} {rate} out of range  -  it must be strictly between 0 and 1.")
                return None
            return rate

        # ── batch action ───────────────────────────────────────────────
        def _on_batch(self) -> None:
            code = self._current_code()
            if code is None:
                return
            n = self._read_int(self.batch_samples_entry, "sample count", 1, _MAX_BATCH_SAMPLES)
            if n is None:
                return
            rate = self._read_rate(self.batch_rate_entry, "error rate")
            if rate is None:
                return
            seed = self._read_int(self.batch_seed_entry, "seed", 0, _MAX_SEED)
            if seed is None:
                return
            backend = self.backend_var.get()
            pool_decoder = self.pool_decoder_var.get()
            if pool_decoder not in _POOL_DECODERS:
                pool_decoder = _POOL_DECODERS[0]
            weighted_gpu = self.weighted_gpu_var.get()
            precision = self.precision_var.get()

            job = {
                "code": code,
                "n": n,
                "rate": rate,
                "seed": seed,
                "backend": backend,
                "pool_decoder": pool_decoder,
                "weighted_gpu": weighted_gpu,
                "precision": precision,
            }
            self._batch_queue.append(job)
            self._update_queue_ui()
            if not self._batch_running:
                self._run_next_batch()

        def _update_queue_ui(self):
            if self._batch_running:
                q_size = len(self._batch_queue)
                self.batch_progress_label.configure(text=f"Running (Queued: {q_size})")
            else:
                self.batch_progress_label.configure(text="Idle")
                self.batch_progress.set(0.0)

        def _run_next_batch(self):
            if not self._batch_queue:
                self._batch_running = False
                self._update_queue_ui()
                return
            
            self._batch_running = True
            job = self._batch_queue.pop(0)
            self._update_queue_ui()
            
            code = job["code"]
            n = job["n"]
            rate = job["rate"]
            seed = job["seed"]
            backend = job["backend"]
            pool_decoder = job["pool_decoder"]
            weighted_gpu = job.get("weighted_gpu", False)
            precision = job.get("precision", "f32")

            self._batch_seq += 1
            seq = self._batch_seq
            self.batch_progress.set(0.0)
            self._set_result_text(f"Running batch decode ({n} samples, backend={backend}) ...")
            threading_utils.run_in_background(
                self._batch_worker, args=(seq, code, backend, n, rate, seed, pool_decoder, weighted_gpu, precision)
            )

        def _batch_worker(self, seq: int, code, backend: str, n: int, rate: float,
                          seed: int, pool_decoder: str = "union_find",
                          weighted_gpu: bool = False, precision: str = "f32") -> None:
            try:
                if backend == "cpu_parallel":
                    # Multi-process DecoderPool: aggregate statistics only  - 
                    # no per-sample corrections are returned for a histogram.
                    out = be.run_parallel_batch_decode(
                        code, n_samples=n, error_rate=rate, seed=seed,
                        decoder_type=pool_decoder,
                    )
                    workers = out.get("n_workers")
                    payload = {
                        "n_samples": out["n_samples"],
                        "backend_used": (
                            f"cpu_parallel [{out.get('decoder_type', pool_decoder)}"
                            + (f", {workers} workers" if workers else "") + "]"
                        ),
                        "success_rate": out["success_rate"],
                        "logical_error_rate": out.get("logical_error_rate"),
                        "mean_hamming_weight": out["mean_hamming_weight"],
                        "batch_seconds": out["batch_seconds"],
                        "weights": None,
                    }
                    self._ui.post(self._on_batch_done, seq, payload)
                    return
                
                edge_weights = None
                if weighted_gpu:
                    try:
                        dem = be.build_dem(code, "depolarizing", rate)
                        if dem is not None and hasattr(dem, "edge_weights"):
                            edge_weights = dem.edge_weights
                        elif dem is not None and hasattr(dem, "get_edge_weights"):
                            edge_weights = dem.get_edge_weights()
                        elif dem is not None and hasattr(dem, "_m") and hasattr(dem._m, "edge_weights"):
                            edge_weights = dem._m.edge_weights
                    except Exception as e:
                        self._log(f"Failed to get edge_weights from DemModel: {e}", "WARN")
                
                out = be.run_batch_decode(code, backend, n, rate, seed, precision=precision, edge_weights=edge_weights)
                weights = np.sum(np.asarray(out["corrections"], dtype=np.int64), axis=1)
                payload = {
                    "n_samples": out["n_samples"],
                    "backend_used": out["backend_used"],
                    "success_rate": out["success_rate"],
                    "logical_error_rate": out["logical_error_rate"],
                    "mean_hamming_weight": out["mean_hamming_weight"],
                    "batch_seconds": out["batch_seconds"],
                    "weights": weights.tolist(),
                }
                self._ui.post(self._on_batch_done, seq, payload)
            except be.QectorError as e:
                # Backend errors (e.g. "cuda backend unavailable ...") are
                # surfaced verbatim  -  no silent fallback.
                self._log(f"Batch decode failed: {e}", "ERROR")
                self._ui.post(self._on_batch_failed, seq, str(e))
            except Exception as e:
                self._log(f"Unexpected batch error: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_batch_failed, seq, f"Unexpected batch error: {e}")

        def _on_batch_done(self, seq: int, p: dict[str, Any]) -> None:
            if seq != self._batch_seq:
                return
            try:
                ler = p["logical_error_rate"]
                if ler is not None:
                    ler_str = f"{ler:.4f}"
                elif p.get("weights") is None:
                    ler_str = "N/A (not reported by the process pool)"
                else:
                    ler_str = "N/A (no logicals matrix)"
                n = p["n_samples"]
                secs = p["batch_seconds"]
                text = (
                    f"Batch Decode Complete\n"
                    f"{'-' * 34}\n"
                    f"Samples:             {n}\n"
                    f"Backend used:        {p['backend_used']}\n"
                    f"Syndrome match rate: {p['success_rate'] * 100:.1f}%\n"
                    f"Logical error rate:  {ler_str}\n"
                    f"Mean Hamming weight: {p['mean_hamming_weight']:.2f}\n"
                    f"Batch time:          {secs * 1000:.2f} ms\n"
                    f"Throughput:          {n / max(secs, 1e-9):.0f} decodes/s\n"
                )
                self._set_result_text(text)
                self._draw_batch_histogram(p)
                try:
                    from history_tab import record_event
                    record_event("batch", {
                        "n": p["n_samples"],
                        "backend": p["backend_used"],
                        "success_rate": f"{p['success_rate'] * 100:.1f}%",
                    })
                except Exception:
                    pass
                self._log(
                    f"Batch {n} samples on {p['backend_used']}: "
                    f"{p['success_rate'] * 100:.0f}% syndrome match, LER={ler_str}",
                    "SUCCESS",
                )
            except tkinter.TclError:
                pass
            except Exception as e:
                self._log(f"Batch chart rendering failed: {e}", "ERROR")
            finally:
                self._run_next_batch()

        def _on_batch_failed(self, seq: int, message: str) -> None:
            if seq != self._batch_seq:
                return
            try:
                self._set_result_text(f"Batch decode failed:\n{message}")
            except tkinter.TclError:
                pass
            finally:
                self._run_next_batch()

        # ── streaming action ───────────────────────────────────────────
        def _on_stream(self) -> None:
            code = self._current_code()
            if code is None:
                return
            window = self._read_int(self.stream_window_entry, "window size", 1, _MAX_WINDOW)
            if window is None:
                return
            rounds = self._read_int(self.stream_rounds_entry, "round count", 0, _MAX_ROUNDS)
            if rounds is None:
                return
            rate = self._read_rate(self.stream_rate_entry, "error rate")
            if rate is None:
                return
            seed = self._read_int(self.stream_seed_entry, "seed", 0, _MAX_SEED)
            if seed is None:
                return
            kind = self.stream_decoder_var.get()

            self._stream_seq += 1
            seq = self._stream_seq
            try:
                self.stream_btn.configure(state="disabled")
            except tkinter.TclError:
                return
            self._set_result_text(f"Running streaming session ({rounds} rounds, window={window}, decoder={kind}) ...")
            threading_utils.run_in_background(
                self._stream_worker, args=(seq, code, window, rounds, rate, seed, kind)
            )

        def _stream_worker(self, seq: int, code, window: int, rounds: int,
                           rate: float, seed: int, kind: str) -> None:
            try:
                import time
                gen = be.run_streaming_session_yield(
                    code, window_size=window, n_rounds=rounds,
                    error_rate=rate, seed=seed, decoder_kind=kind,
                )
                weights = []
                last_ler = 0.0
                t0 = time.perf_counter()
                for res in gen:
                    if seq != self._stream_seq:
                        return
                    if res is not None:
                        weights.append(res["weight"])
                        last_ler = res["logical_error_rate"] or 0.0
                        payload = {
                            "committed_count": len(weights),
                            "rounds": rounds,
                            "window_size": window,
                            "session_seconds": time.perf_counter() - t0,
                            "logical_error_rate": last_ler,
                            "weights": list(weights),
                            "decoder": kind,
                        }
                        self._ui.post(self._on_stream_progress, seq, payload)
                    time.sleep(0.01)

                payload = {
                    "committed_count": len(weights),
                    "rounds": rounds,
                    "window_size": window,
                    "session_seconds": time.perf_counter() - t0,
                    "logical_error_rate": last_ler,
                    "weights": list(weights),
                    "decoder": kind,
                }
                self._ui.post(self._on_stream_done, seq, payload)
            except be.QectorError as e:
                self._log(f"Streaming session failed: {e}", "ERROR")
                self._ui.post(self._on_stream_failed, seq, str(e))
            except Exception as e:
                self._log(f"Unexpected streaming error: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_stream_failed, seq, f"Unexpected streaming error: {e}")

        def _on_stream_progress(self, seq: int, p: dict[str, Any]) -> None:
            if seq != self._stream_seq:
                return
            try:
                ler = p["logical_error_rate"]
                ler_str = f"{ler:.4f}" if ler is not None else "N/A"
                text = (
                    f"Streaming Session - Decoding Live...\n"
                    f"{'-' * 34}\n"
                    f"Decoder:            {p['decoder']}\n"
                    f"Progress:           {p['committed_count']} / {p['rounds']} rounds\n"
                    f"Window size:        {p['window_size']}\n"
                    f"Logical error rate: {ler_str}\n"
                )
                self._set_result_text(text)
                self._draw_stream_chart(p)
            except tkinter.TclError:
                pass

        def _on_stream_done(self, seq: int, p: dict[str, Any]) -> None:
            if seq != self._stream_seq:
                return
            try:
                ler = p["logical_error_rate"]
                ler_str = f"{ler:.4f}" if ler is not None else "N/A (no logicals matrix)"
                text = (
                    f"Streaming Session Complete\n"
                    f"{'-' * 34}\n"
                    f"Decoder:            {p['decoder']}\n"
                    f"Committed count:    {p['committed_count']}\n"
                    f"Rounds:             {p['rounds']}\n"
                    f"Window size:        {p['window_size']}\n"
                    f"Session time:       {p['session_seconds'] * 1000:.2f} ms\n"
                    f"Logical error rate: {ler_str}\n"
                )
                self._set_result_text(text)
                self._draw_stream_chart(p)
                try:
                    from history_tab import record_event
                    record_event("streaming", {
                        "rounds": p["rounds"],
                        "decoder": p["decoder"],
                        "window": p["window_size"],
                        "committed": p["committed_count"],
                    })
                except Exception:
                    pass
                self._log(
                    f"Streaming done ({p['decoder']}): {p['committed_count']} committed, LER={ler_str}",
                    "SUCCESS",
                )
            except tkinter.TclError:
                pass
            except Exception as e:
                self._log(f"Streaming chart rendering failed: {e}", "ERROR")
            finally:
                self._reenable_stream(seq)

        def _on_stream_failed(self, seq: int, message: str) -> None:
            if seq != self._stream_seq:
                return
            try:
                self._set_result_text(f"Streaming session failed:\n{message}")
            except tkinter.TclError:
                pass
            finally:
                self._reenable_stream(seq)

        def _reenable_stream(self, seq: int) -> None:
            if seq != self._stream_seq:
                return
            try:
                self.stream_btn.configure(state="normal")
            except tkinter.TclError:
                pass

        # ── drawing (UI thread only, one shared figure) ────────────────
        def _defer_redraw(self) -> None:
            """Coalesce consecutive redraws into a single canvas pass."""
            if self._pending_redraw:
                return
            self._pending_redraw = True
            try:
                self.after(40, self._flush_redraw)
            except tkinter.TclError:
                self._pending_redraw = False

        def _flush_redraw(self) -> None:
            self._pending_redraw = False
            try:
                self._mpl_canvas.draw_idle()
            except tkinter.TclError:
                pass

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
            self._defer_redraw()

        def _draw_batch_histogram(self, p: dict[str, Any]) -> None:
            weights = p.get("weights")
            if weights is None:
                self._draw_placeholder(
                    "cpu_parallel returns aggregate statistics only\n"
                    "(no per-sample corrections to histogram)."
                )
                return
            weights = np.asarray(weights, dtype=np.int64)
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            theme.style_dark_axes(
                ax,
                title=f"Correction Hamming weights  -  batch ({p['backend_used']}, n={p['n_samples']})",
                xlabel="hamming weight", ylabel="count",
            )
            if weights.size:
                low, high = int(weights.min()), int(weights.max())
                bins = np.arange(low, high + 2) - 0.5
                n, _, _ = ax.hist(
                    weights, bins=bins, color=theme.mc("bar"),
                    edgecolor=theme.mc("fig_bg"), linewidth=0.6,
                    zorder=3, antialiased=True,
                )
                mean = float(weights.mean())
                med = float(np.median(weights))
                ax.axvline(mean, color=theme.mc("bar_hot"), linestyle="--", linewidth=1.2, zorder=4)
                ax.axvline(med, color=theme.mc("accent"), linestyle=":", linewidth=1.2, zorder=4)
                from matplotlib.patches import Patch
                legend = ax.legend(
                    handles=[
                        Patch(color=theme.mc("bar_hot"), label=f"mean = {mean:.2f}"),
                        Patch(color=theme.mc("accent"), label=f"median = {med:.1f}"),
                    ],
                    loc="upper right", fontsize=8,
                )
                theme.style_dark_legend(legend)
                ax.annotate(
                    f"max = {high}  |  P(>0) = {(weights > 0).mean() * 100:.1f}%",
                    xy=(0.02, 0.98), xycoords="axes fraction",
                    ha="left", va="top", fontsize=8, color=theme.mc("text_secondary"),
                )
            self._defer_redraw()

        def _draw_stream_chart(self, p: dict[str, Any]) -> None:
            weights = list(p["weights"])
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            theme.style_dark_axes(
                ax,
                title=f"Committed correction weight per round  -  {p['decoder']} (window={p['window_size']})",
                xlabel="round", ylabel="hamming weight",
            )
            if weights:
                xs = np.arange(len(weights))
                colors = [
                    theme.mc("bar_hot") if w > 0 else theme.mc("bar_dim")
                    for w in weights
                ]
                ax.bar(xs, weights, color=colors, width=0.8, linewidth=0, zorder=3)
                cum = np.cumsum(weights)
                if len(weights) > 1:
                    ax.plot(xs, cum, color=theme.mc("accent"), linewidth=1.4,
                            zorder=5, label="cumulative weight")
                    ax.yaxis.set_label_position("right")
                    ax2 = ax.twinx()
                    ax2.set_ylabel("cumulative hamming weight", color=theme.mc("text_secondary"), fontsize=8)
                    ax2.tick_params(axis="y", labelcolor=theme.mc("text_secondary"), labelsize=8)
                    ax2.plot(xs, cum, color=theme.mc("accent"), linewidth=1.4, zorder=5)
                    ax2.spines["top"].set_visible(False)
                from matplotlib.patches import Patch
                legend = ax.legend(
                    handles=[
                        Patch(color=theme.mc("bar_hot"), label="nonzero weight"),
                        Patch(color=theme.mc("bar_dim"), label="zero weight"),
                        Patch(color=theme.mc("accent"), label="cumulative"),
                    ],
                    loc="upper right", fontsize=8,
                )
                theme.style_dark_legend(legend)
            self._defer_redraw()

        def _set_result_text(self, text: str) -> None:
            try:
                self.result_text.configure(state="normal")
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", text)
                self.result_text.configure(state="disabled")
            except tkinter.TclError:
                pass

        def _on_export_batch_report(self) -> None:
            """Export the latest batch or streaming results as a report."""
            try:
                import tkinter.filedialog as fd
                filename = fd.asksaveasfilename(
                    title="Export Batch / Streaming Report",
                    defaultextension=".html",
                    filetypes=[("HTML Report", "*.html"), ("Markdown Report", "*.md"), ("Text Document", "*.txt")],
                )
                if not filename:
                    return
                content = self.result_text.get("1.0", "end").strip()
                if not content or content.startswith("Run a batch"):
                    content = "No batch decode execution data recorded yet."
                safe = _html.escape(content)
                
                ok_path, path = utils.sanitize_export_path(filename)
                if not ok_path:
                    self._log("Export path rejected: directory traversal is not allowed.", "ERROR")
                    return
                if path.suffix.lower() == ".html":
                    html_doc = f"""<!DOCTYPE html>
<html>
<head><title>Batch & Streaming Decode Report</title>
<style>
body {{ font-family: monospace; padding: 30px; background: #fff; color: #111; line-height: 1.5; }}
pre {{ background: #f4f4f4; padding: 20px; border-radius: 6px; border: 1px solid #ccc; }}
h1 {{ font-family: sans-serif; font-size: 18pt; }}
</style>
</head>
<body>
<h1>Batch & Streaming Decode Report</h1>
<pre>{safe}</pre>
</body>
</html>"""
                    path.write_text(html_doc, encoding="utf-8")
                else:
                    path.write_text(f"# Batch & Streaming Decode Report\n\n```text\n{content}\n```", encoding="utf-8")
                ok_sum, digest = utils.sha256_sidecar(path)
                if ok_sum:
                    self._log(f"Exported Batch Report -> {path} (sha256 {digest[:16]}…, {path.name}.sha256)", "SUCCESS")
                else:
                    self._log(f"Exported Batch Report -> {path} (checksum sidecar FAILED: {digest})", "ERROR")
            except Exception as exc:
                self._log(f"Failed to export batch report: {exc}", "ERROR")

else:
    class BatchStreamingTab:
        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            pass
