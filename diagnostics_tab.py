"""diagnostics_tab.py  -  Self-Diagnostics & Auto-Debug tab for QECTOR Workbench.

Surfaces the :mod:`autodebug` layer in the GUI:

* **Run Self-Diagnostics**  -  full environment/decoder/hardware self-test.
* **Probe Decoders**  -  which decoders produce a valid correction for the
  current (or a default) code.
* **Resilient Decode**  -  one decode with automatic multi-decoder fallback and a
  full attempt trace.

Every probe runs on a background worker and marshals back through a UI pump, so
the buttons never block the event loop.  The text formatters are module-level
and side-effect free, so they can be unit-tested headlessly.
"""

from __future__ import annotations

import tkinter
import html as _html
import traceback
from pathlib import Path

try:
    import customtkinter as ctk
    _HAS_GUI = True
except Exception:
    _HAS_GUI = False

import theme
import threading_utils
import backend as be
import utils


_STATUS_TAG = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]",
               "degraded": "DEGRADED", "ok": "[ OK ]"}


# ---------------------------------------------------------------------------
# Text formatters (pure; safe to call off the UI thread and in tests)
# ---------------------------------------------------------------------------

def format_diagnostics(report: dict) -> str:
    """Render a run_self_diagnostics() report dict as aligned text."""
    overall = str(report.get("overall_status", "?")).upper()
    summary = report.get("summary", {}) or {}
    lines = [
        f"QECTOR Self-Diagnostics  -  OVERALL: {overall}",
        f"Backend: qector_decoder_v3 {report.get('backend_version')}"
        f"  |  Python {report.get('python')}  |  {report.get('platform')}",
        f"Summary: {summary.get('pass', 0)} pass / {summary.get('warn', 0)} warn / "
        f"{summary.get('fail', 0)} fail",
        "",
    ]
    for c in report.get("checks", []):
        tag = _STATUS_TAG.get(str(c.get("status")), "[ ?? ]")
        lines.append(f"{tag} {c.get('name')}  -  {c.get('detail')}")
    working = summary.get("working_decoders")
    if working:
        lines += ["", f"Working decoders: {', '.join(working)}"]
    return "\n".join(lines)


def format_probe(probe: dict) -> str:
    """Render a probe_decoders() result dict."""
    lines = [
        f"Decoder probe  -  {probe.get('family')} d={probe.get('distance')}"
        f"  (error_rate={probe.get('error_rate')}, seed={probe.get('seed')})",
        probe.get("message", ""),
        "",
    ]
    for r in probe.get("results", []):
        ok = r.get("ok")
        valid = r.get("syndrome_valid")
        usable = ok and (valid is not False)
        tag = "[ OK ]" if usable else "[FAIL]"
        if ok:
            lines.append(
                f"{tag} {str(r.get('method')):14s} valid={valid}  "
                f"weight={r.get('hamming_weight')}  {r.get('latency_ms')} ms"
            )
        else:
            lines.append(f"{tag} {str(r.get('method')):14s} error={r.get('error')}")
    return "\n".join(lines)


def format_resilient(res: dict) -> str:
    """Render a resilient_single_decode() result dict."""
    head = "SUCCESS" if res.get("success") else "FAILURE"
    used = res.get("used_decoder")
    fb = res.get("fallback_used")
    lines = [
        f"Resilient decode  -  {res.get('family')} d={res.get('distance')}, "
        f"requested={res.get('requested_decoder')!r}",
        f"{head}"
        + (f" via {used!r}" + (" (fallback)" if fb else " (no fallback)") if used else ""),
        res.get("message", ""),
    ]
    if res.get("success"):
        lines.append(
            f"correction weight={res.get('hamming_weight')}, "
            f"syndrome_valid={res.get('syndrome_valid')}, "
            f"logical_failure={res.get('logical_failure')}"
        )
    lines.append("")
    lines.append("Attempts:")
    for i, a in enumerate(res.get("attempts", []), 1):
        if a.get("ok"):
            lines.append(
                f"  {i}. {str(a.get('method')):14s} ok=True  valid={a.get('syndrome_valid')}  "
                f"weight={a.get('hamming_weight')}  {a.get('latency_ms')} ms"
            )
        else:
            lines.append(f"  {i}. {str(a.get('method')):14s} ok=False error={a.get('error')}")
    return "\n".join(lines)


if _HAS_GUI:

    class DiagnosticsTab(ctk.CTkFrame):
        """Self-diagnostics and resilient auto-debug panel."""

        def __init__(self, master, state=None, console=None, fonts=None, **kwargs):
            super().__init__(master, fg_color="transparent", **kwargs)
            self.state = state
            self.console = console
            self.fonts = fonts if fonts is not None else theme.get_fonts()

            self._ui = threading_utils.UiPump(self)
            self._seq = 0

            self.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(0, weight=1)

            scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
            scroll.grid(row=0, column=0, sticky="nsew")
            scroll.grid_columnconfigure(0, weight=1)

            mono = ctk.CTkFont(family=self.fonts.mono, size=self.fonts.mono_size + 1)

            ctk.CTkLabel(
                scroll, text="Self-Diagnostics & Auto-Debug",
                font=ctk.CTkFont(size=18, weight="bold"),
            ).pack(anchor="w", padx=16, pady=(16, 4))
            ctk.CTkLabel(
                scroll,
                text="Environment/decoder/hardware self-test, per-decoder probe, and "
                     "resilient decode with automatic fallback.",
                font=ctk.CTkFont(size=11), text_color=theme.c("text_secondary"),
                wraplength=900, justify="left",
            ).pack(anchor="w", padx=16, pady=(0, 12))

            btns = ctk.CTkFrame(scroll, fg_color="transparent")
            btns.pack(anchor="w", padx=16, pady=(0, 8), fill="x")
            self.diag_btn = ctk.CTkButton(
                btns, text="Run Self-Diagnostics", command=self._on_diagnostics,
                font=ctk.CTkFont(size=12, weight="bold"), width=180,
            )
            self.diag_btn.pack(side="left", padx=(0, 8))
            self.probe_btn = ctk.CTkButton(
                btns, text="Probe Decoders", command=self._on_probe,
                font=ctk.CTkFont(size=12), width=150,
            )
            self.probe_btn.pack(side="left", padx=8)
            self.resilient_btn = ctk.CTkButton(
                btns, text="Resilient Decode", command=self._on_resilient,
                font=ctk.CTkFont(size=12), width=160,
            )
            self.resilient_btn.pack(side="left", padx=8)
            # Decoder requested by Resilient Decode. The other two actions do
            # not use it: the probe tries every decoder, and the self-test
            # picks its own. Kept on the button row so the association with
            # Resilient Decode is visible.
            self.decoder_var = ctk.StringVar(value="union_find")
            self.decoder_combo = ctk.CTkComboBox(
                btns, values=[str(k) for k in be.DECODER_KINDS],
                variable=self.decoder_var, width=165, font=ctk.CTkFont(size=11),
            )
            self.decoder_combo.pack(side="left", padx=8)
            self.doctor_btn = ctk.CTkButton(
                btns, text="Doctor Health", command=self._on_doctor,
                font=ctk.CTkFont(size=12), width=140, fg_color="#2D5A46",
            )
            self.doctor_btn.pack(side="left", padx=8)
            self.reset_btn = ctk.CTkButton(
                btns, text="Reset Health", command=self._on_reset,
                font=ctk.CTkFont(size=12), width=120, fg_color="#8B0000", hover_color="#A52A2A"
            )
            self.reset_btn.pack(side="left", padx=8)
            self.export_btn = ctk.CTkButton(
                btns, text="📋 Export Report", command=self._on_export_report,
                font=ctk.CTkFont(size=12, weight="bold"), width=140,
                fg_color=theme.c("accent_dim"), hover_color=theme.c("accent"),
            )
            self.export_btn.pack(side="left", padx=8)

            self.status_label = ctk.CTkLabel(
                scroll, text="Ready.", font=ctk.CTkFont(size=11),
                text_color=theme.c("text_secondary"),
            )
            self.status_label.pack(anchor="w", padx=16, pady=(0, 6))

            self.result_text = ctk.CTkTextbox(scroll, height=460, wrap="word", font=mono)
            self.result_text.pack(fill="both", expand=True, padx=16, pady=(2, 16))
            self.result_text.insert("1.0", "Click 'Run Self-Diagnostics' to check the installation.")
            self.result_text.configure(state="disabled")

            # Defer the initial self-diagnostics so construction never blocks.
            try:
                self.after(200, self._on_diagnostics)
            except tkinter.TclError:
                pass

        # ── helpers ────────────────────────────────────────────────────
        def _log(self, msg: str, level: str = "INFO") -> None:
            if self.console:
                try:
                    self.console.log(msg, level)
                except Exception:
                    pass

        def _snapshot(self) -> tuple[str, int, str]:
            """Read current code state on the UI thread (family, distance, decoder)."""
            family = getattr(self.state, "current_family_key", None) if self.state else None
            distance = getattr(self.state, "current_param", None) if self.state else None
            try:
                decoder = self.decoder_var.get() or "union_find"
            except Exception:
                decoder = "union_find"
            return (family or "repetition"), int(distance) if distance else 3, decoder

        def _busy(self, on: bool, status: str = "") -> None:
            for b in (self.diag_btn, self.probe_btn, self.resilient_btn, self.doctor_btn, self.reset_btn):
                try:
                    b.configure(state="disabled" if on else "normal")
                except tkinter.TclError:
                    pass
            if status:
                try:
                    self.status_label.configure(text=status)
                except tkinter.TclError:
                    pass

        # ── actions ────────────────────────────────────────────────────
        def _on_diagnostics(self) -> None:
            self._seq += 1
            seq = self._seq
            self._busy(True, "Running self-diagnostics …")
            threading_utils.run_in_background(self._diag_worker, args=(seq,))

        def _diag_worker(self, seq: int) -> None:
            try:
                import autodebug
                report = autodebug.run_self_diagnostics().to_dict()
                text = format_diagnostics(report)
                overall = report.get("overall_status")
                self._ui.post(self._done, seq, text, f"Self-diagnostics: {overall}")
            except Exception as e:
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._done, seq, f"Self-diagnostics failed: {e}", "Error")

        def _on_probe(self) -> None:
            self._seq += 1
            seq = self._seq
            family, distance, _ = self._snapshot()
            self._busy(True, f"Probing decoders on {family} d={distance} …")
            threading_utils.run_in_background(self._probe_worker, args=(seq, family, distance))

        def _probe_worker(self, seq: int, family: str, distance: int) -> None:
            try:
                import autodebug
                probe = autodebug.probe_decoders(family, distance)
                self._ui.post(self._done, seq, format_probe(probe),
                              f"Probe: {probe.get('message', '')}")
            except Exception as e:
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._done, seq, f"Probe failed: {e}", "Error")

        def _on_resilient(self) -> None:
            self._seq += 1
            seq = self._seq
            family, distance, decoder = self._snapshot()
            self._busy(True, f"Resilient decode on {family} d={distance} …")
            threading_utils.run_in_background(
                self._resilient_worker, args=(seq, family, distance, decoder))

        def _resilient_worker(self, seq: int, family: str, distance: int, decoder: str) -> None:
            try:
                import autodebug
                res = autodebug.resilient_single_decode(family, distance, decoder=decoder).to_dict()
                status = "recovered via fallback" if res.get("fallback_used") else (
                    "ok" if res.get("success") else "failed")
                self._ui.post(self._done, seq, format_resilient(res), f"Resilient decode: {status}")
            except Exception as e:
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._done, seq, f"Resilient decode failed: {e}", "Error")
        def _on_doctor(self) -> None:
            self._seq += 1
            seq = self._seq
            self._busy(True, "Running qd.doctor health checks …")
            threading_utils.run_in_background(self._doctor_worker, args=(seq,))

        def _doctor_worker(self, seq: int) -> None:
            try:
                res = be.run_doctor_checks()
                import json
                text = json.dumps(res, indent=2)
                self._ui.post(self._done, seq, text, "Doctor health check complete")
            except Exception as e:
                self._log(traceback.format_exc(), "ERROR")
                self._ui.post(self._done, seq, f"Doctor check failed: {e}", "Error")

        def _on_reset(self) -> None:
            import backend as be
            if hasattr(be.qd, "reset_backend_health"):
                be.qd.reset_backend_health()
                self._log("reset_backend_health() called", "SUCCESS")
                self.status_label.configure(text="Backend health reset.")
            else:
                self.status_label.configure(text="reset_backend_health not available.")

        def _done(self, seq: int, text: str, status: str) -> None:
            if seq != self._seq:
                return
            # A finished run must give the buttons back: without this the
            # deferred boot-time self-test disabled all three permanently.
            self._busy(False)
            try:
                self.result_text.configure(state="normal")
                self.result_text.delete("1.0", "end")
                self.result_text.insert("1.0", text)
                self.result_text.configure(state="disabled")
                self.status_label.configure(text=status)
            except tkinter.TclError:
                pass
        def _on_export_report(self) -> None:
            """Export self-diagnostics or resilient report to file."""
            try:
                import tkinter.filedialog as fd
                filename = fd.asksaveasfilename(
                    title="Export Diagnostics Report",
                    defaultextension=".html",
                    filetypes=[("HTML Report", "*.html"), ("Markdown Report", "*.md"), ("Text Document", "*.txt")],
                )
                if not filename:
                    return
                content = self.result_text.get("1.0", "end").strip()
                safe = _html.escape(content)
                path = Path(filename)
                if path.suffix.lower() == ".html":
                    html_doc = f"""<!DOCTYPE html>
<html>
<head><title>QECTOR Diagnostics Report</title>
<style>
body {{ font-family: monospace; padding: 30px; background: #fff; color: #111; line-height: 1.5; }}
pre {{ background: #f4f4f4; padding: 20px; border-radius: 6px; border: 1px solid #ccc; }}
</style>
</head>
<body>
<h1>QECTOR Self-Diagnostics Report</h1>
<pre>{safe}</pre>
</body>
</html>"""
                    path.write_text(html_doc, encoding="utf-8")
                else:
                    path.write_text(f"# QECTOR Self-Diagnostics Report\n\n```text\n{content}\n```", encoding="utf-8")
                ok_sum, digest = utils.sha256_sidecar(path)
                if ok_sum:
                    self._log(f"Exported Diagnostics Report -> {path} (sha256 {digest[:16]}…, {path.name}.sha256)", "SUCCESS")
                else:
                    self._log(f"Exported Diagnostics Report -> {path} (checksum sidecar FAILED: {digest})", "ERROR")
            except Exception as exc:
                self._log(f"Failed to export diagnostics report: {exc}", "ERROR")

else:

    class DiagnosticsTab:
        """No-GUI fallback used when customtkinter is unavailable."""

        def __init__(self, master=None, state=None, console=None, fonts=None, **kwargs):
            self.state = state
            self.console = console
