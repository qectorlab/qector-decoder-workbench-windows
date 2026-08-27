"""hardware_tab.py  -  Hardware tab for QECTOR Workbench.

Detects available decode backends (CUDA, OpenCL, CPU), system resources
(psutil) and decoder recommendations.  All probes run on a background
thread  -  the refresh button never blocks the UI.
"""

from __future__ import annotations

import os
import platform
import sys
import tkinter
import html as _html
import traceback
from pathlib import Path
from typing import Any, Optional

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False

import backend as be
import theme
import threading_utils
import utils


def _probe_hardware_text() -> str:
    """Backend availability probe (safe to call off the UI thread)."""
    try:
        from hardware_routing import detect_hardware
        hw = detect_hardware()
        return (
            f"CUDA:          {'available' if hw.cuda_rust else 'not available'}\n"
            f"GPU:           {hw.gpu or 'N/A'}\n"
            f"OpenCL:        {'available' if hw.opencl else 'not available'}\n"
            f"OpenCL device: {hw.opencl_device or 'N/A'}\n"
            f"OpenCL note:   {hw.opencl_reason}\n"
            f"CPU:           always available\n"
        )
    except Exception as e:
        return f"Hardware detection unavailable: {e}"


def _probe_recommendation_text(family: Optional[str], d: Optional[int], n_qubits: Optional[int]) -> str:
    """Decoder recommendation probe (safe to call off the UI thread)."""
    try:
        from hardware_routing import recommend
        rec = recommend(family, d, n_qubits, "balanced")
        return (
            f"Recommended:    {rec.decoder}\n"
            f"Priority:       {rec.priority}\n"
            f"Hardware:       {rec.hardware}\n"
            f"Batch size:     {rec.batch_size}\n"
            f"GPU batched BP: {rec.gpu_batched_bp}\n"
            f"Reason:         {rec.reason}\n"
        )
    except Exception:
        return "Build a code to get decoder recommendations."


def _probe_system_text() -> str:
    """System resource probe (safe to call off the UI thread)."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.15)
        mem = psutil.virtual_memory()
        cores = psutil.cpu_count(logical=True)
        ps_text = (
            f"CPU usage: {cpu:.0f}% ({cores} logical cores) | "
            f"RAM: {mem.percent:.0f}% of {mem.total / (1024 ** 3):.1f} GiB"
        )
    except Exception as e:
        ps_text = f"psutil probe failed: {e}"
    return (
        f"Platform:      {platform.platform()}\n"
        f"Python:        {sys.version.split()[0]}\n"
        f"Backend:       qector_decoder_v3 {be.PACKAGE_VERSION}\n"
        f"Resources:     {ps_text}\n"
    )


if _HAS_GUI:

    class HardwareTab(ctk.CTkFrame):
        """Hardware and system info panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._refresh_seq = 0

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(0, weight=1)

            scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
            scroll.grid(row=0, column=0, sticky="nsew")
            scroll.grid_columnconfigure(0, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)
            bold = ctk.CTkFont(size=12, weight="bold")

            ctk.CTkLabel(
                scroll, text="Hardware & System",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(16, 4))
            ctk.CTkLabel(
                scroll, text="Detected backends, system resources, and decoder recommendations.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
            ).pack(anchor="w", padx=16, pady=(0, 12))

            h_row = ctk.CTkFrame(scroll, fg_color="transparent")
            h_row.pack(anchor="w", padx=16, pady=(0, 8), fill="x")

            self.refresh_btn = ctk.CTkButton(
                h_row, text="Refresh Hardware Info", command=self._on_refresh,
                font=ctk.CTkFont(size=12, weight="bold"),
            )
            self.refresh_btn.pack(side="left", padx=(0, 8))

            self.export_hw_btn = ctk.CTkButton(
                h_row, text="📋 Export Report", command=self._on_export_report,
                font=ctk.CTkFont(size=12, weight="bold"), width=140,
                fg_color=theme.c("accent_dim"), hover_color=theme.c("accent"),
            )
            self.export_hw_btn.pack(side="left")

            ctk.CTkLabel(scroll, text="Backends", font=bold).pack(anchor="w", padx=16)
            self.hw_text = ctk.CTkTextbox(scroll, height=120, wrap="word", font=mono)
            self.hw_text.pack(fill="x", padx=16, pady=(2, 8))
            self.hw_text.insert("1.0", "Probing hardware ...")
            self.hw_text.configure(state="disabled")

            ctk.CTkLabel(scroll, text="Recommendation", font=bold).pack(anchor="w", padx=16)
            self.rec_text = ctk.CTkTextbox(scroll, height=120, wrap="word", font=mono)
            self.rec_text.pack(fill="x", padx=16, pady=(2, 8))
            self.rec_text.configure(state="disabled")

            ctk.CTkLabel(scroll, text="System", font=bold).pack(anchor="w", padx=16)
            self.sys_text = ctk.CTkTextbox(scroll, height=120, wrap="word", font=mono)
            self.sys_text.pack(fill="x", padx=16, pady=(2, 8))
            self.sys_text.configure(state="disabled")

            # ── Tuning environment variables (finaldev.md §16.5) ────────
            # The workbench exposes every QECTOR tuning env var here so users
            # can flip a switch in the GUI instead of editing their shell
            # profile. Each field writes through to os.environ on Apply;
            # the live process picks the new value up on the next call into
            # the affected module.
            ctk.CTkLabel(scroll, text="Tuning Environment Variables",
                         font=bold).pack(anchor="w", padx=16, pady=(8, 0))
            ctk.CTkLabel(
                scroll,
                text=("These mirror the QECTOR_* environment variables. "
                      "Settings take effect on Apply; a worker restart is "
                      "only required for QECTOR_LICENSE_KEY / QECTOR_LICENSE_FILE."),
                font=ctk.CTkFont(size=10),
                text_color=theme.c("text_secondary"),
                wraplength=720, justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 6))
            tuning_frame = ctk.CTkFrame(scroll, fg_color="transparent")
            tuning_frame.pack(anchor="w", padx=16, pady=(0, 8), fill="x")
            tuning_frame.grid_columnconfigure(1, weight=1)
            self._tuning_vars: dict[str, ctk.StringVar] = {}
            _TUNING_SPECS = [
                # (env var, label, kind, default, hint)
                ("QECTOR_BLOSSOM_K_MULT", "Blossom k multiplier",
                 "float", "2.0",
                 "Candidate-neighbour multiplier for sparse MWPM. Affects accuracy."),
                ("QECTOR_BLOSSOM_INTRA_PAR", "Force intra-decode parallelism",
                 "bool", "auto", "auto / on / off."),
                ("QECTOR_BLOSSOM_INTRA_THREADS", "Dedicated Rayon pool size",
                 "int", "", "Empty = use Rayon default."),
                ("QECTOR_CUDA_DEVICE_ID", "CUDA device id",
                 "int", "0", "0-based index of the CUDA device to use."),
                ("QECTOR_OPENCL_DEVICE_ALLOW", "OpenCL device name filter",
                 "str", "",
                 "Substring; empty allows every OpenCL device."),
                ("QECTOR_SILENT", "Suppress licensing notice",
                 "bool", "off", "Quiet the boot banner."),
                ("QECTOR_ENFORCE", "Hard license gate",
                 "bool", "off", "Refuse to start without a valid licence."),
                ("QECTOR_PROVISION_TIMEOUT", "Provision timeout (s)",
                 "int", "300", "Network timeout for decoder provisioning."),
            ]
            for row, (env_name, label, kind, default, hint) in enumerate(_TUNING_SPECS):
                ctk.CTkLabel(
                    tuning_frame, text=label,
                    font=ctk.CTkFont(size=11, weight="bold"),
                    text_color=theme.c("text_primary"),
                ).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=2)
                var = ctk.StringVar(value=os.environ.get(env_name, default) or default)
                self._tuning_vars[env_name] = (var, kind)
                if kind == "bool":
                    widget = ctk.CTkOptionMenu(
                        tuning_frame, values=["on", "off"],
                        variable=var, width=110,
                    )
                else:
                    widget = ctk.CTkEntry(
                        tuning_frame, textvariable=var, width=160,
                        placeholder_text=default,
                    )
                widget.grid(row=row, column=1, sticky="w", padx=(0, 12), pady=2)
                ctk.CTkLabel(
                    tuning_frame, text=hint,
                    font=ctk.CTkFont(size=10),
                    text_color=theme.c("text_secondary"),
                ).grid(row=row, column=2, sticky="w", padx=(12, 0), pady=2)
            # Apply / Reset buttons
            tuning_btn_row = ctk.CTkFrame(scroll, fg_color="transparent")
            tuning_btn_row.pack(anchor="w", padx=16, pady=(0, 8))
            ctk.CTkButton(
                tuning_btn_row, text="Apply Tuning", command=self._on_apply_tuning,
                font=ctk.CTkFont(size=11, weight="bold"),
            ).pack(side="left", padx=(0, 8))
            ctk.CTkButton(
                tuning_btn_row, text="Reset to Defaults", command=self._on_reset_tuning,
                font=ctk.CTkFont(size=11),
                fg_color=theme.c("bg_widget"),
            ).pack(side="left")
            self.tuning_status = ctk.CTkLabel(
                scroll, text="", font=ctk.CTkFont(size=10),
                text_color=theme.c("text_secondary"),
            )
            self.tuning_status.pack(anchor="w", padx=16, pady=(0, 8))

            ctk.CTkLabel(scroll, text="Capabilities", font=bold).pack(anchor="w", padx=16)
            theme.configure_matplotlib()
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            self._figure = Figure(figsize=(6.8, 2.6), dpi=140)
            theme.style_dark_figure(self._figure)
            self._mpl_canvas = FigureCanvasTkAgg(self._figure, master=scroll)
            self._mpl_canvas.get_tk_widget().pack(fill="x", padx=16, pady=(2, 16))
            self._pending_redraw = False
            self._draw_caps(None, None, None)

            # Initial refresh is deferred so construction never blocks on
            # CUDA/OpenCL probes; the probes themselves run on a worker.
            try:
                self.after(150, self._on_refresh)
            except tkinter.TclError:
                pass

        # ── capability chart ──────────────────────────────────────────
        def _defer_redraw(self) -> None:
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

        def _draw_caps(self, hw_text: Optional[str], sys_text: Optional[str],
                       rec_text: Optional[str]) -> None:
            labels = []
            values = []
            colors = []
            if hw_text is not None:
                for name, key in (("CUDA", "cuda"), ("OpenCL", "opencl")):
                    line = next((ln for ln in hw_text.splitlines() if ln.lower().startswith(key)), "")
                    ok = "available" in line.lower()
                    labels.append(name)
                    values.append(1 if ok else 0)
                    colors.append(theme.mc("bar_hot") if ok else theme.mc("bar_dim"))
                labels.append("CPU")
                values.append(1)
                colors.append(theme.mc("bar"))
            else:
                labels = ["CUDA", "OpenCL", "CPU"]
                values = [0, 0, 1]
                colors = [theme.mc("bar_dim"), theme.mc("bar_dim"), theme.mc("bar")]
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            theme.style_dark_axes(
                ax, title="Decode backend availability",
                ylabel="backend", grid=False,
            )
            ax.barh(labels, values, color=colors, height=0.55, zorder=3)
            ax.set_xlim(0, 1.15)
            ax.set_xticks([0, 1])
            ax.set_xticklabels(["unavailable", "available"], fontsize=8)
            for i, v in enumerate(values):
                ax.text(v + 0.03, i, "yes" if v else "no",
                        va="center", fontsize=9,
                        color=theme.mc("bar_hot") if v else theme.mc("text_secondary"))
            note = ""
            if rec_text:
                line = next((ln for ln in rec_text.splitlines() if ln.lower().startswith("recommended")), "")
                if line:
                    note = line.split(":", 1)[-1].strip()
            if note:
                ax.set_xlabel(f"recommended: {note}", fontsize=8,
                              color=theme.mc("text_secondary"))
            self._defer_redraw()

        # ── helpers ────────────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        # ── tuning env vars ────────────────────────────────────────────
        def _on_apply_tuning(self) -> None:
            """Apply every QECTOR_* env var the user typed, validate the
            value, and write through to ``os.environ`` so the next call
            into the affected module picks it up.

            Validation: ints and floats are parsed; bad input is reported
            and the env var is left unchanged (the GUI's stale value is
            still safer than a half-applied batch).
            """
            applied: list[str] = []
            errors: list[str] = []
            for env_name, (var, kind) in self._tuning_vars.items():
                raw = (var.get() or "").strip()
                if kind == "int":
                    if not raw:
                        os.environ.pop(env_name, None)
                        applied.append(f"{env_name}=<unset>")
                        continue
                    try:
                        int(raw)
                    except ValueError:
                        errors.append(f"{env_name}: not an integer ({raw!r})")
                        continue
                    os.environ[env_name] = raw
                    applied.append(f"{env_name}={raw}")
                elif kind == "float":
                    if not raw:
                        os.environ.pop(env_name, None)
                        applied.append(f"{env_name}=<unset>")
                        continue
                    try:
                        float(raw)
                    except ValueError:
                        errors.append(f"{env_name}: not a float ({raw!r})")
                        continue
                    os.environ[env_name] = raw
                    applied.append(f"{env_name}={raw}")
                elif kind == "bool":
                    v = raw.lower()
                    if v not in ("on", "off", "1", "0", "true", "false", "auto"):
                        errors.append(f"{env_name}: must be on/off/auto ({raw!r})")
                        continue
                    os.environ[env_name] = v
                    applied.append(f"{env_name}={v}")
                else:
                    os.environ[env_name] = raw
                    applied.append(f"{env_name}={raw!r}")
            if errors:
                self.tuning_status.configure(
                    text=("Apply FAILED: " + "; ".join(errors)),
                    text_color=theme.c("error"),
                )
            else:
                self.tuning_status.configure(
                    text=f"Applied {len(applied)} variable(s): "
                         + ", ".join(applied[:4])
                         + (" …" if len(applied) > 4 else ""),
                    text_color=theme.c("success"),
                )
            self._log(
                f"Tuning apply: {len(applied)} applied, {len(errors)} error(s)",
                "SUCCESS" if not errors else "ERROR",
            )

        def _on_reset_tuning(self) -> None:
            """Pop every QECTOR_* var the tab exposes from the process env."""
            for env_name in self._tuning_vars:
                os.environ.pop(env_name, None)
            self.tuning_status.configure(
                text="Cleared all QECTOR_* tuning variables from this process.",
                text_color=theme.c("text_secondary"),
            )
            self._log("Tuning reset to defaults", "INFO")

        # ── refresh action ─────────────────────────────────────────────
        def _on_refresh(self) -> None:
            self._refresh_seq += 1
            seq = self._refresh_seq
            try:
                self.refresh_btn.configure(state="disabled")
            except tkinter.TclError:
                return
            # Snapshot state on the UI thread; the worker only reads copies.
            code = self.state.current_code if self.state else None
            family = self.state.current_family_key if self.state else None
            d = self.state.current_param if self.state else None
            n_qubits = getattr(code, "n_qubits", None) if code is not None else None
            threading_utils.run_in_background(
                self._refresh_worker, args=(seq, family, d, n_qubits)
            )

        def _refresh_worker(self, seq: int, family: Optional[str], d: Optional[int],
                            n_qubits: Optional[int]) -> None:
            try:
                payload = {
                    "hw": _probe_hardware_text(),
                    "rec": _probe_recommendation_text(family, d, n_qubits),
                    "sys": _probe_system_text(),
                }
                self._ui.post(self._on_refresh_done, seq, payload)
            except Exception as e:
                self._log(f"Hardware refresh failed: {e}", "ERROR")
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._on_refresh_failed, seq, f"Hardware refresh failed: {e}")

        def _on_refresh_done(self, seq: int, payload: dict[str, Any]) -> None:
            if seq != self._refresh_seq:
                return
            try:
                self._set_text(self.hw_text, payload["hw"])
                self._set_text(self.rec_text, payload["rec"])
                self._set_text(self.sys_text, payload["sys"])
                self._draw_caps(payload["hw"], payload["sys"], payload["rec"])
                self._log("Hardware info refreshed", "INFO")
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _on_refresh_failed(self, seq: int, message: str) -> None:
            if seq != self._refresh_seq:
                return
            try:
                self._set_text(self.hw_text, message)
            except tkinter.TclError:
                pass
            finally:
                self._reenable(seq)

        def _reenable(self, seq: int) -> None:
            if seq != self._refresh_seq:
                return
            try:
                self.refresh_btn.configure(state="normal")
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

        def _on_export_report(self) -> None:
            """Export hardware capabilities report to file."""
            try:
                import tkinter.filedialog as fd
                filename = fd.asksaveasfilename(
                    title="Export Hardware Report",
                    defaultextension=".html",
                    filetypes=[("HTML Report", "*.html"), ("Markdown Report", "*.md"), ("Text Document", "*.txt")],
                )
                if not filename:
                    return
                ok_path, path = utils.sanitize_export_path(filename)
                if not ok_path:
                    self._log("Export path rejected: directory traversal is not allowed.", "ERROR")
                    return
                hw = self.hw_text.get("1.0", "end").strip()
                rec = self.rec_text.get("1.0", "end").strip()
                sys_info = self.sys_text.get("1.0", "end").strip()
                content = f"=== BACKENDS ===\n{hw}\n\n=== RECOMMENDATION ===\n{rec}\n\n=== SYSTEM ===\n{sys_info}"
                safe = _html.escape(content)

                path = Path(path)
                if path.suffix.lower() == ".html":
                    html_doc = f"""<!DOCTYPE html>
<html>
<head><title>QECTOR Hardware Capabilities Report</title>
<style>
body {{ font-family: monospace; padding: 30px; background: #fff; color: #111; line-height: 1.5; }}
pre {{ background: #f4f4f4; padding: 20px; border-radius: 6px; border: 1px solid #ccc; }}
</style>
</head>
<body>
<h1>QECTOR Hardware Capabilities Report</h1>
<pre>{safe}</pre>
</body>
</html>"""
                    path.write_text(html_doc, encoding="utf-8")
                else:
                    path.write_text(f"# QECTOR Hardware Capabilities Report\n\n```text\n{content}\n```", encoding="utf-8")
                ok_sum, digest = utils.sha256_sidecar(path)
                if ok_sum:
                    self._log(f"Exported Hardware Report -> {path} (sha256 {digest[:16]}…, {path.name}.sha256)", "SUCCESS")
                else:
                    self._log(f"Exported Hardware Report -> {path} (checksum sidecar FAILED: {digest})", "ERROR")
            except Exception as exc:
                self._log(f"Failed to export hardware report: {exc}", "ERROR")

else:
    class HardwareTab:
        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            pass
