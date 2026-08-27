"""benchmark_tab.py  -  Benchmark tab for QECTOR Workbench.

Configurable benchmarks (family, distance, decoder, error rate, samples,
seed) run in a background thread; results render latency statistics and a
two-panel chart (latency stats + session throughput comparison), with JSON
export to the per-user export directory.
"""

from __future__ import annotations

import json
import tkinter
import html as _html
import traceback
from typing import Any

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
_MAX_SAMPLES = 200_000


def _fmt_ler(value: Any) -> str:
    """Display string for ``logical_error_rate``.

    Codes without a logicals matrix report ``None``; an export must say so in
    text rather than die inside ``None.__format__``.
    """
    return "N/A (no logicals matrix)" if value is None else f"{value:.4f}"


if _HAS_GUI:

    class BenchmarkTab(ctk.CTkFrame):
        """Professional benchmark runner panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._run_seq = 0
            self._results: list[dict[str, Any]] = []

            self.grid_columnconfigure(0, weight=0)
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(1, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)
            bold = ctk.CTkFont(size=12, weight="bold")

            # ── Controls (row 0, spans both columns) ──────────────────
            controls = ctk.CTkFrame(self, fg_color="transparent")
            controls.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))

            ctk.CTkLabel(
                controls, text="Benchmark Suite",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", pady=(0, 2))
            ctk.CTkLabel(
                controls, text="Measure decoder throughput and latency across codes.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
            ).pack(anchor="w", pady=(0, 8))

            row0 = ctk.CTkFrame(controls, fg_color="transparent")
            row0.pack(fill="x", pady=2)
            ctk.CTkLabel(row0, text="Code:", font=bold).pack(side="left")
            self.family_var = ctk.StringVar(value="rotated_surface")
            ctk.CTkOptionMenu(
                row0, values=list(be.CODE_FAMILIES.keys()),
                variable=self.family_var, width=170,
            ).pack(side="left", padx=(10, 20))
            ctk.CTkLabel(row0, text="Distance:", font=bold).pack(side="left")
            self.distance_var = ctk.IntVar(value=5)
            ctk.CTkSlider(
                row0, from_=3, to=21, number_of_steps=18,
                variable=self.distance_var, command=self._on_distance_change,
                width=140,
            ).pack(side="left", padx=(10, 6))
            self.dist_label = ctk.CTkLabel(row0, text="5", width=22, font=ctk.CTkFont(size=12))
            self.dist_label.pack(side="left", padx=(0, 20))
            ctk.CTkLabel(row0, text="Decoder:", font=bold).pack(side="left")
            self.decoder_var = ctk.StringVar(value="union_find")
            ctk.CTkOptionMenu(
                row0, values=list(be.DECODER_KINDS),
                variable=self.decoder_var, width=170,
            ).pack(side="left", padx=(10, 0))

            row1 = ctk.CTkFrame(controls, fg_color="transparent")
            row1.pack(fill="x", pady=2)
            ctk.CTkLabel(row1, text="Mode:", font=bold).pack(side="left")
            self.mode_var = ctk.StringVar(value="Standard (Throughput)")
            ctk.CTkOptionMenu(
                row1, values=["Standard (Throughput)", "LER Benchmark (Wilson CI)"],
                variable=self.mode_var, width=170,
            ).pack(side="left", padx=(10, 0))

            row2 = ctk.CTkFrame(controls, fg_color="transparent")
            row2.pack(fill="x", pady=2)
            ctk.CTkLabel(row2, text="Samples:", font=bold).pack(side="left")
            self.samples_entry = ctk.CTkEntry(row2, width=80)
            self.samples_entry.insert(0, "200")
            self.samples_entry.pack(side="left", padx=(10, 20))
            ctk.CTkLabel(row2, text="Error Rate:", font=bold).pack(side="left")
            self.rate_entry = ctk.CTkEntry(row2, width=80)
            self.rate_entry.insert(0, "0.05")
            self.rate_entry.pack(side="left", padx=(10, 20))
            ctk.CTkLabel(row2, text="Seed:", font=bold).pack(side="left")
            self.seed_entry = ctk.CTkEntry(row2, width=80)
            self.seed_entry.insert(0, "42")
            self.seed_entry.pack(side="left", padx=(10, 20))

            self.run_btn = ctk.CTkButton(
                row2, text="Run Benchmark", command=self._on_run,
                font=ctk.CTkFont(size=12, weight="bold"), width=130,
            )
            self.run_btn.pack(side="left", padx=(0, 8))
            self.export_btn = ctk.CTkButton(
                row2, text="📊 Export Report", command=self._on_export,
                font=ctk.CTkFont(size=11), width=100,
            )
            self.export_btn.pack(side="left")
            self.fig_export_btn = ctk.CTkButton(
                row2, text="🖼 Export Figure", command=self._on_export_figure,
                font=ctk.CTkFont(size=11), width=100,
            )
            self.fig_export_btn.pack(side="left", padx=(8, 0))

            # ── Left column: results text ─────────────────────────────
            left = ctk.CTkFrame(self, fg_color="transparent", width=350)
            left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(4, 12))
            left.grid_propagate(False)
            left.grid_columnconfigure(0, weight=1)
            left.grid_rowconfigure(0, weight=1)
            self.result_text = ctk.CTkTextbox(left, wrap="word", font=mono)
            self.result_text.grid(row=0, column=0, sticky="nsew")
            self.result_text.insert("1.0", "Run a benchmark to see results.")
            self.result_text.configure(state="disabled")

            # ── Right column: matplotlib canvas ───────────────────────
            right = ctk.CTkFrame(self, fg_color=theme.c("bg_panel"))
            right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(4, 12))
            right.grid_columnconfigure(0, weight=1)
            right.grid_rowconfigure(0, weight=1)

            theme.configure_matplotlib()
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

            self._figure = Figure(figsize=(7.2, 4.4), dpi=140)
            theme.style_dark_figure(self._figure)
            self._mpl_canvas = FigureCanvasTkAgg(self._figure, master=right)
            self._mpl_canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
            # Progressive-render guard: a benchmark with hundreds of result
            # rows can take tens of ms to draw; the canvas redraw is deferred
            # so the UI never visibly blocks.
            self._pending_redraw: Optional[str] = None
            self._draw_placeholder("Run a benchmark to see latency and throughput charts.")

        # ── helpers ────────────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        def _on_distance_change(self, value=None) -> None:
            try:
                d = int(round(float(value))) if value is not None else int(self.distance_var.get())
                self.dist_label.configure(text=str(d))
            except (TypeError, ValueError, tkinter.TclError):
                pass

        # ── run action ─────────────────────────────────────────────────
        def _on_run(self) -> None:
            family = self.family_var.get()
            kind = self.decoder_var.get()
            try:
                d = int(self.distance_var.get())
            except tkinter.TclError:
                self._set_result_text("Invalid distance  -  use the slider to pick 3-21.")
                return

            samples_text = self.samples_entry.get().strip()
            valid, msg = utils.validate_int(samples_text, min_val=1, max_val=_MAX_SAMPLES)
            if not valid:
                self._set_result_text(f"Invalid sample count: {msg}\nEnter an integer between 1 and {_MAX_SAMPLES}.")
                return
            n = int(samples_text)

            seed_text = self.seed_entry.get().strip()
            valid, msg = utils.validate_int(seed_text, min_val=0, max_val=_MAX_SEED)
            if not valid:
                self._set_result_text(f"Invalid seed: {msg}\nEnter an integer between 0 and {_MAX_SEED}.")
                return
            seed = int(seed_text)

            rate_text = self.rate_entry.get().strip()
            try:
                rate = float(rate_text)
            except ValueError:
                self._set_result_text(f"Invalid error rate: {rate_text!r}\nEnter a number between 0 and 1 (e.g. 0.05).")
                return
            if not (0.0 < rate < 1.0):
                self._set_result_text(f"Error rate {rate} out of range  -  it must be strictly between 0 and 1.")
                return

            self._run_seq += 1
            seq = self._run_seq
            try:
                self.run_btn.configure(state="disabled")
            except tkinter.TclError:
                return
            mode = self.mode_var.get()
            self._set_result_text(f"Running {family} d={d} / {kind}  -  {n} samples ...")
            threading_utils.run_in_background(
                self._run_worker, args=(seq, family, d, kind, n, rate, seed, mode)
            )

        def _run_worker(self, seq: int, family: str, d: int, kind: str,
                        n: int, rate: float, seed: int, mode: str = "Standard (Throughput)") -> None:
            try:
                code = be.build_code(family, d)
                if mode == "LER Benchmark (Wilson CI)":
                    result = be.run_ler_benchmark(
                        code, n_samples=n, seed=seed, decoder_kind=kind, error_rate=rate
                    )
                else:
                    result = be.run_benchmark(
                        code, n_samples=n, seed=seed, decoder_kind=kind, error_rate=rate
                    )
                result["code_family"] = family
                result["distance"] = d
                result["label"] = f"{family} d={d}\n{kind}"
                self._ui.post(self._on_run_done, seq, result)
            except be.QectorError as e:
                self._log(f"Benchmark failed: {e}", "ERROR")
                self._ui.post(self._on_run_failed, seq, f"Benchmark error: {e}")
            except Exception as e:
                self._log(f"Unexpected benchmark error: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_run_failed, seq, f"Unexpected benchmark error: {e}")

        def _on_run_done(self, seq: int, result: dict[str, Any]) -> None:
            if seq != self._run_seq:
                return
            self._results.append(result)
            try:
                self._show_result(result)
                self._draw_charts(result)
                try:
                    from history_tab import record_event
                    record_event("benchmark", {
                        "family": result.get("code_family", "?"),
                        "distance": result.get("distance", "?"),
                        "decoder": result.get("method", "?"),
                        "throughput": f"{result['throughput_decodes_per_s']:.0f}",
                    })
                except Exception:
                    pass
                self._log(
                    f"Benchmark {result['code_family']} d={result['distance']} {result['method']}: "
                    f"{result['throughput_decodes_per_s']:.0f} dec/s, "
                    f"p99={result['latency_p99_us']:.1f} us",
                    "SUCCESS",
                )
            except tkinter.TclError:
                pass
            except Exception as e:
                self._log(f"Benchmark chart rendering failed: {e}", "ERROR")
            finally:
                self._reenable(seq)

        def _on_run_failed(self, seq: int, message: str) -> None:
            if seq != self._run_seq:
                return
            try:
                self._set_result_text(message)
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _reenable(self, seq: int) -> None:
            if seq != self._run_seq:
                return
            try:
                self.run_btn.configure(state="normal")
            except tkinter.TclError:
                pass

        # ── rendering ──────────────────────────────────────────────────
        def _show_result(self, result: dict) -> None:
            ler = result.get("logical_error_rate")
            if ler is not None:
                ler_str = f"{ler:.4f}"
                lo = result.get("ler_ci95_low")
                hi = result.get("ler_ci95_high")
                if lo is not None and hi is not None:
                    ler_str += f"  (95% CI [{lo:.4f}, {hi:.4f}])"
            else:
                ler_str = "N/A (no logicals matrix)"
            text = (
                f"Code:               {result.get('code_family', 'N/A')} d={result.get('distance', 'N/A')}\n"
                f"Decoder:            {result.get('method', 'N/A')}\n"
                f"Backend:            {result.get('backend', 'N/A')}\n"
                f"Trials:             {result['n_trials']}\n"
                f"Error rate (p):     {result['p']}\n"
                f"Seed:               {result.get('seed', 'N/A')}\n"
                f"\n"
                f"Throughput:         {result['throughput_decodes_per_s']:.0f} decodes/s\n"
                f"Total decode time:  {result['decode_seconds'] * 1000:.2f} ms\n"
                f"LATENCY (us):\n"
                f"  mean:             {result['latency_mean_us']:.1f}\n"
                f"  p50:              {result['latency_p50_us']:.1f}\n"
                f"  p99:              {result['latency_p99_us']:.1f}\n"
                f"  min:              {result['latency_min_us']:.1f}\n"
                f"  max:              {result['latency_max_us']:.1f}\n"
                f"\n"
                f"Syndrome match:     {result['syndrome_match_rate'] * 100:.1f}%\n"
                f"Unfaithful:         {result.get('unfaithful_count', 'N/A')} "
                f"({result.get('unfaithful_rate', 0) * 100:.1f}%)\n"
                f"Logical error rate: {ler_str}\n"
                f"\nSession runs:       {len(self._results)}\n"
            )
            self._set_result_text(text)

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

        def _draw_charts(self, latest: dict[str, Any]) -> None:
            """Two-panel chart: latency stats (left) + session comparison (right).

            run_benchmark exposes aggregate latency statistics (not raw
            per-sample timings), so the left panel is a labeled bar chart of
            mean/p50/p99/min/max with horizontal marker lines at p50/p99.
            """
            self._figure.clear()
            show_ler = any(
                r.get("logical_error_rate") is not None for r in self._results
            )
            ax1 = self._figure.add_subplot(121)
            ax2 = self._figure.add_subplot(122)

            # Panel 1: latency statistics for the latest run
            stats = [
                ("mean", latest["latency_mean_us"]),
                ("p50", latest["latency_p50_us"]),
                ("p99", latest["latency_p99_us"]),
                ("min", latest["latency_min_us"]),
                ("max", latest["latency_max_us"]),
            ]
            labels = [s[0] for s in stats]
            values = [s[1] for s in stats]
            theme.style_dark_axes(
                ax1,
                title=f"Latency  -  {latest['method']}",
                ylabel="microseconds",
            )
            xs1 = np.arange(len(labels))
            bars = ax1.bar(
                xs1, values, color=theme.mc("bar"), width=0.62,
                linewidth=0, zorder=3,
            )
            ax1.set_xticks(xs1)
            ax1.set_xticklabels(labels)
            for bar, value in zip(bars, values):
                ax1.text(
                    bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{value:.0f}", ha="center", va="bottom",
                    color=theme.mc("text_secondary"), fontsize=8,
                )
            ax1.axhline(
                latest["latency_p50_us"], color=theme.mc("marker_p50"),
                linewidth=1.4, linestyle="--", alpha=0.9, label="p50",
                solid_capstyle="round", zorder=4,
            )
            ax1.axhline(
                latest["latency_p99_us"], color=theme.mc("marker_p99"),
                linewidth=1.4, linestyle="--", alpha=0.9, label="p99",
                solid_capstyle="round", zorder=4,
            )
            legend = ax1.legend(loc="upper left", fontsize=8)
            theme.style_dark_legend(legend)

            # Panel 2: throughput across session runs, optionally with the
            # logical error rate on a twin axis when the code exposes logicals.
            theme.style_dark_axes(
                ax2, title="Session comparison", ylabel="decodes/s",
            )
            xs = np.arange(len(self._results))
            throughputs = [r["throughput_decodes_per_s"] for r in self._results]
            colors = [
                theme.mc("bar") if i == len(self._results) - 1 else theme.mc("bar_dim")
                for i in range(len(self._results))
            ]
            ax2.bar(xs, throughputs, color=colors, width=0.62, linewidth=0, zorder=3)
            ax2.set_xticks(xs)
            ax2.set_xticklabels(
                [r.get("label", "?") for r in self._results],
                fontsize=7, color=theme.mc("text_secondary"),
                rotation=25 if len(self._results) > 3 else 0,
            )
            if show_ler:
                ler_vals = [r.get("logical_error_rate") for r in self._results]
                ler_vals = [v if v is not None else 0.0 for v in ler_vals]
                ax2b = ax2.twinx()
                ax2b.plot(
                    xs, ler_vals, marker="o", linewidth=1.6,
                    color=theme.mc("bar_hot"), markersize=4,
                    linestyle="-", label="LER", zorder=5,
                )
                ax2b.set_ylabel("logical error rate", color=theme.mc("bar_hot"))
                ax2b.tick_params(axis="y", labelcolor=theme.mc("bar_hot"), labelsize=8)
                ax2b.set_ylim(0, 1)
            self._mpl_canvas.draw_idle()

        # ── export ─────────────────────────────────────────────────────
        def _on_export_figure(self) -> None:
            """Export the current chart at publication quality (300 DPI)."""
            if not self._results:
                self._set_result_text("No results to export  -  run a benchmark first.")
                return
            try:
                import tkinter.filedialog as fd
                filename = fd.asksaveasfilename(
                    title="Export Benchmark Figure",
                    defaultextension=".png",
                    filetypes=[
                        ("PNG (300 DPI)", "*.png"),
                        ("SVG", "*.svg"),
                        ("PDF", "*.pdf"),
                    ],
                )
                if not filename:
                    return
                ok_path, path = utils.sanitize_export_path(filename)
                if not ok_path:
                    self._set_result_text("Figure export path rejected: directory traversal is not allowed.")
                    return
                try:
                    self._mpl_canvas.figure.savefig(
                        path, dpi=300, facecolor=theme.mc("fig_bg"),
                        bbox_inches="tight",
                    )
                except Exception as e:
                    self._set_result_text(f"Figure export failed: {e}")
                    return
                ok_sum, digest = utils.sha256_sidecar(path)
                if ok_sum:
                    self._set_result_text(
                        f"Figure exported to:\n{path.resolve()}\n"
                        f"SHA-256: {digest}  ({path.name}.sha256)"
                    )
                    self._log(f"Benchmark figure exported to {path} (sha256 {digest[:16]}…)", "SUCCESS")
                else:
                    self._log(f"Benchmark figure exported to {path} (checksum sidecar FAILED: {digest})", "ERROR")
            except Exception as e:
                self._set_result_text(f"Figure export failed: {e}")
                self._log(f"Figure export failed: {e}", "ERROR")

        def _on_export(self) -> None:
            if not self._results:
                self._set_result_text("No results to export  -  run a benchmark first.")
                return
            try:
                import tkinter.filedialog as fd
                filename = fd.asksaveasfilename(
                    title="Export Benchmark Report",
                    defaultextension=".html",
                    filetypes=[("HTML Report", "*.html"), ("Markdown Report", "*.md"), ("JSON Data", "*.json")],
                )
                if not filename:
                    return
                ok_path, path = utils.sanitize_export_path(filename)
                if not ok_path:
                    self._set_result_text("Export path rejected: directory traversal is not allowed.")
                    self._log("Export path rejected: directory traversal is not allowed.", "ERROR")
                    return
                if path.suffix.lower() == ".json":
                    data = json.dumps(self._results, indent=2, default=str)
                    path.write_text(data, encoding="utf-8")
                elif path.suffix.lower() == ".html":
                    rows = "".join(
                        f"<tr><td>{_html.escape(str(r.get('method')))}</td>"
                        f"<td>{r.get('throughput_decodes_per_s', 0):.0f}</td>"
                        f"<td>{r.get('latency_p50_us', 0):.2f} us</td>"
                        f"<td>{r.get('latency_p99_us', 0):.2f} us</td>"
                        f"<td>{_html.escape(_fmt_ler(r.get('logical_error_rate')))}</td></tr>"
                        for r in self._results
                    )
                    html_doc = f"""<!DOCTYPE html>
<html>
<head><title>Benchmark Performance Report</title>
<style>
body {{ font-family: sans-serif; padding: 30px; background: #fff; color: #111; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
th, td {{ border: 1px solid #ccc; padding: 8px 12px; text-align: left; }}
th {{ background: #f0f0f0; }}
</style>
</head>
<body>
<h1>QECTOR Decoder Benchmark Report</h1>
<table>
<thead><tr><th>Decoder</th><th>Throughput (dec/s)</th><th>p50 Latency</th><th>p99 Latency</th><th>Logical Failure Fraction</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>"""
                    path.write_text(html_doc, encoding="utf-8")
                else:
                    lines = [
                        "# QECTOR Decoder Benchmark Report",
                        "",
                        "| Decoder | Throughput (dec/s) | p50 Latency | p99 Latency | Logical Failure Fraction |",
                        "|:---|:---|:---|:---|:---|",
                    ]
                    for r in self._results:
                        lines.append(
                            f"| `{r.get('method')}` | `{r.get('throughput_decodes_per_s', 0):.0f}` | "
                            f"`{r.get('latency_p50_us', 0):.2f} us` | `{r.get('latency_p99_us', 0):.2f} us` | "
                            f"`{_fmt_ler(r.get('logical_error_rate'))}` |"
                        )
                    path.write_text("\n".join(lines), encoding="utf-8")

                ok_sum, digest = utils.sha256_sidecar(path)
                full = str(path.resolve())
                if ok_sum:
                    self._set_result_text(
                        f"Exported {len(self._results)} result(s) to:\n{full}\n"
                        f"SHA-256: {digest}  ({path.name}.sha256)"
                    )
                    self._log(f"Benchmark exported to {full} (sha256 {digest[:16]}…)", "SUCCESS")
                else:
                    self._set_result_text(
                        f"Exported {len(self._results)} result(s) to:\n{full}\n"
                        f"(checksum sidecar FAILED: {digest})"
                    )
                    self._log(f"Benchmark exported to {full} (checksum sidecar FAILED: {digest})", "ERROR")
            except Exception as e:
                self._set_result_text(f"Export failed: {e}")
                self._log(f"Export failed: {e}", "ERROR")

        def _set_result_text(self, text: str) -> None:
            try:
                self.result_text.configure(state="normal")
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", text)
                self.result_text.configure(state="disabled")
            except tkinter.TclError:
                pass

else:
    class BenchmarkTab:
        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            pass
