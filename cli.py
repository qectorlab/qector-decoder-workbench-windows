"""cli.py: command line interface for QECTOR Decoder Workbench.

Supports interactive and automated terminal workflows across Windows, Linux and
macOS. Wires the backend features: 16 decoders, 10 code families,
self-diagnostics, hardware routing, benchmarks, document generation and MCP
server launch.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import sys
from typing import Any, Optional

# Enable VT100 colors on Windows
if sys.platform == "win32":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass  # best-effort ANSI enablement; colors degrade gracefully

# ANSI Color Tokens
class C:
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.CYAN = cls.MAGENTA = cls.BLUE = cls.GREEN = cls.YELLOW = cls.RED = cls.BOLD = cls.DIM = cls.RESET = ""


from version import WORKBENCH_VERSION, BACKEND_VERSION, MCP_TOOLS  # noqa: E402


# ANSI Shadow lettering spelling QECTOR.
# The previous art read Q-E-U-T-R-O: the third glyph was a U, and the last two
# were transposed. Read it letter by letter before changing it.
_BANNER_ART = """
 ██████╗ ███████╗ ██████╗████████╗ ██████╗ ██████╗
██╔═══██╗██╔════╝██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗
██║   ██║█████╗  ██║        ██║   ██║   ██║██████╔╝
██║▄▄ ██║██╔══╝  ██║        ██║   ██║   ██║██╔══██╗
╚██████╔╝███████╗╚██████╗   ██║   ╚██████╔╝██║  ██║
 ╚══▀▀═╝ ╚══════╝ ╚═════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝"""


def banner() -> str:
    """Build the banner at call time.

    Deliberately a function, not a module constant: as an f-string evaluated at
    import the colour codes were baked in before ``--no-color`` could be parsed,
    so the flag left the banner full of escape sequences.
    """
    return (
        f"{C.CYAN}{C.BOLD}{_BANNER_ART}\n"
        f"{C.MAGENTA}      Quantum Error Correction Decoder Workbench "
        f"{C.DIM}v{WORKBENCH_VERSION} / backend v{BACKEND_VERSION}{C.RESET}\n"
    )

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _can_unicode() -> bool:
    try:
        "┌─✔".encode(sys.stdout.encoding or "utf-8")
        return True
    except Exception:
        return False

USE_UNICODE = _can_unicode()

SYM_OK = "✔ PASS" if USE_UNICODE else "[OK]"
SYM_FAIL = "✘ FAIL" if USE_UNICODE else "[FAIL]"
SYM_WARN = "▲ WARN" if USE_UNICODE else "[WARN]"

BOX_TL = "┌" if USE_UNICODE else "+"
BOX_TR = "┐" if USE_UNICODE else "+"
BOX_BL = "└" if USE_UNICODE else "+"
BOX_BR = "┘" if USE_UNICODE else "+"
BOX_H = "─" if USE_UNICODE else "-"
BOX_V = "│" if USE_UNICODE else "|"


def print_json(data: Any) -> None:
    """Output clean, formatted JSON to stdout."""
    print(json.dumps(data, indent=2, default=str))


#: Matches any ANSI SGR sequence, so printable width can be measured whatever
#: colours a caller embedded. Replacing each known colour by name missed any
#: sequence not in that list and mis-measured the line.
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(text: str) -> int:
    """Printable width of ``text``, ignoring ANSI colour sequences."""
    return len(_ANSI_RE.sub("", text))


def draw_box(title: str, lines: list[str], width: int = 68, color: str = C.CYAN) -> None:
    """Draw a titled box whose three edges are the same width.

    The top border used ``width - len(title) - 4`` fill characters, one short of
    what the body and bottom occupy, so the right-hand edge never lined up.
    Body and bottom are both ``width + 2`` glyphs wide; the top must match.
    """
    fill = max(0, width - visible_len(title) - 3)
    top = f"{BOX_TL}{BOX_H} {C.BOLD}{title}{C.RESET} " + BOX_H * fill + BOX_TR
    bot = BOX_BL + BOX_H * width + BOX_BR
    print(f"{color}{top}{C.RESET}")
    for line in lines:
        padding = " " * max(0, width - visible_len(line) - 2)
        print(f"{color}{BOX_V}{C.RESET} {line}{padding} {color}{BOX_V}{C.RESET}")
    print(f"{color}{bot}{C.RESET}")


def cmd_decode(args: argparse.Namespace) -> int:
    import backend as be

    try:
        code = be.build_code(args.family, args.distance)
        raw = be.run_single_decode(
            code, error_rate=args.error_rate, decoder_kind=args.decoder, seed=args.seed
        )
        dec_obj = raw.get("result") if isinstance(raw, dict) else raw
        res = dec_obj.to_dict() if hasattr(dec_obj, "to_dict") else (dec_obj if isinstance(dec_obj, dict) else {})
        res["family"] = args.family
        res["distance"] = args.distance
        res["decoder"] = args.decoder
        res["n_qubits"] = getattr(code, "n_qubits", None)
        res["n_checks"] = getattr(code, "n_checks", None)
        res["error_rate"] = args.error_rate
        syndrome = raw.get("syndrome") if isinstance(raw, dict) else None
        err = raw.get("error") if isinstance(raw, dict) else None
        res["error_weight"] = int(sum(err)) if err is not None else None
        res["syndrome_weight"] = int(sum(syndrome)) if syndrome is not None else None

        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            valid_str = f"{C.GREEN}✔ VALID{C.RESET}" if res.get("syndrome_valid") else f"{C.RED}✘ INVALID{C.RESET}"
            lines = [
                f"{C.BOLD}Decoder:{C.RESET}           {res.get('decoder')}",
                f"{C.BOLD}Code Family:{C.RESET}       {res.get('family')} (d={res.get('distance')})",
                f"{C.BOLD}Qubits / Checks:{C.RESET}   {res.get('n_qubits')} qubits | {res.get('n_checks')} check operators",
                f"{C.BOLD}Error Rate (p):{C.RESET}    {res.get('error_rate')}",
                f"{C.BOLD}Error Weight:{C.RESET}      {res.get('error_weight')}",
                f"{C.BOLD}Syndrome Weight:{C.RESET}   {res.get('syndrome_weight')}",
                f"{C.BOLD}Correction Weight:{C.RESET} {res.get('hamming_weight')}",
                f"{C.BOLD}Syndrome Valid:{C.RESET}    {valid_str}",
            ]
            if res.get("logical_failure") is not None:
                fail_str = f"{C.RED}TRUE{C.RESET}" if res.get("logical_failure") else f"{C.GREEN}FALSE{C.RESET}"
                lines.append(f"{C.BOLD}Logical Failure:{C.RESET}   {fail_str}")
            draw_box(f"DECODE RESULT: {args.family.upper()}", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during decode: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_benchmark(args: argparse.Namespace) -> int:
    import backend as be

    try:
        code = be.build_code(args.family, args.distance)
        res = be.run_benchmark(
            code=code,
            n_samples=args.samples,
            seed=args.seed,
            decoder_kind=args.decoder,
            error_rate=args.error_rate,
        )
        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            throughput = res.get('throughput_decodes_per_s', 0)
            ler_val = res.get('logical_error_rate')
            ler_str = f"{ler_val:.5f}" if ler_val is not None else "N/A"
            lines = [
                f"{C.BOLD}Code Family:{C.RESET}       {res.get('code_family') or args.family} (d={res.get('distance') or args.distance})",
                f"{C.BOLD}Decoder Kind:{C.RESET}      {res.get('method')}",
                f"{C.BOLD}Sample Trials:{C.RESET}     {res.get('n_trials')}",
                f"{C.BOLD}Error Rate (p):{C.RESET}    {res.get('p')}",
                f"{C.BOLD}Logical Error Rate:{C.RESET} {C.YELLOW}{C.BOLD}{ler_str}{C.RESET}",
                f"{C.BOLD}Throughput:{C.RESET}        {C.GREEN}{C.BOLD}{throughput:,.1f}{C.RESET} decodes/sec",
                f"{C.BOLD}Execution Time:{C.RESET}    {res.get('decode_seconds', 0):.4f} seconds",
                f"{C.BOLD}Backend Used:{C.RESET}      {res.get('backend')}",
            ]
            draw_box(f"BENCHMARK PERFORMANCE: {args.decoder.upper()}", lines, color=C.MAGENTA)

        # --verify: assert LER is within expected bounds for the reference seed.
        if getattr(args, "verify", False):
            ler_val = res.get('logical_error_rate')
            p = res.get('p', args.error_rate)
            ok, msg = _verify_ler(args.decoder, args.family, args.distance, p, ler_val)
            if ok:
                print(f"{C.GREEN}{SYM_OK}{C.RESET} LER verification passed: {msg}")
                return 0
            else:
                print(f"{C.RED}{SYM_FAIL}{C.RESET} LER verification FAILED: {msg}", file=sys.stderr)
                return 1
        return 0
    except Exception as e:
        print(f"{C.RED}Error during benchmark: {e}{C.RESET}", file=sys.stderr)
        return 1


# Known reference LER ranges for --verify mode.
# Format: (family, distance, decoder, (p_low, p_ref, p_high)) — (family, distance, decoder)
# keys are sorted tuples; missing entries fall back to a generic sanity check.
_LER_REFERENCE: dict[tuple, tuple[float, float, float]] = {
    # repetition code, d=5, union_find at p=0.05 with seed=42 — known stable range
    ("repetition", 5, "union_find"): (0.00, 0.04, 0.12),
    # rotated_surface, d=3, union_find at p=0.05 — shallow code reference
    ("rotated_surface", 3, "union_find"): (0.00, 0.06, 0.20),
    # rotated_surface, d=5, union_find at p=0.05 — medium code reference
    ("rotated_surface", 5, "union_find"): (0.00, 0.03, 0.15),
}


def _verify_ler(
    decoder: str, family: str, distance: int, p: float, ler: Optional[float]
) -> tuple[bool, str]:
    """Check that ``ler`` falls within an expected range for the reference seed.

    Returns ``(ok, message)``.
    """
    if ler is None:
        return False, "no LER returned by benchmark"
    if ler < 0:
        return False, f"LER {ler} is negative (decoder bug)"
    if ler > 1:
        return False, f"LER {ler} exceeds 1.0 (decoder bug)"
    # Soft upper bound: LER cannot exceed physical error rate for any code.
    if ler > p * 1.05:
        return False, f"LER {ler:.5f} exceeds physical rate {p} by more than 5% (implausible)"
    key = (family, distance, decoder)
    if key in _LER_REFERENCE:
        lo, ref, hi = _LER_REFERENCE[key]
        if lo <= ler <= hi:
            return True, f"LER {ler:.5f} within reference range [{lo:.5f}, {hi:.5f}] for {decoder}/{family}/d{distance}"
        else:
            return False, f"LER {ler:.5f} outside reference range [{lo:.5f}, {hi:.5f}] for {decoder}/{family}/d{distance}"
    # Generic sanity: LER must be between 0 and physical rate for reasonable configs.
    if ler <= p:
        return True, f"LER {ler:.5f} within physical rate {p} (no reference range for {decoder}/{family}/d{distance})"
    return False, f"LER {ler:.5f} exceeds physical rate {p} with no reference range"


def cmd_probe(args: argparse.Namespace) -> int:
    import autodebug

    try:
        report = autodebug.probe_decoders(
            family=args.family, distance=args.distance, error_rate=args.error_rate, seed=args.seed
        )
        if args.json:
            print_json(report)
        else:
            if not args.no_banner:
                print(banner())
            lines = []
            for r in report.get("results", []):
                status_icon = f"{C.GREEN}✔ PASS{C.RESET}" if r.get("ok") else f"{C.RED}✘ FAIL{C.RESET}"
                valid_icon = f"{C.GREEN}valid{C.RESET}" if r.get("syndrome_valid") else f"{C.RED}invalid{C.RESET}"
                method = f"{C.BOLD}{r.get('method'):20s}{C.RESET}"
                lines.append(f"  [{status_icon}] {method} | Hc==s: {valid_icon:12s} | weight: {r.get('hamming_weight')}")
            draw_box(f"DECODER COMPATIBILITY PROBE: {args.family.upper()} (d={args.distance})", lines, color=C.BLUE)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during probe: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_diagnostics(args: argparse.Namespace) -> int:
    import autodebug

    try:
        rep = autodebug.run_self_diagnostics().to_dict()
        if args.json:
            print_json(rep)
        else:
            if not args.no_banner:
                print(banner())
            overall = rep.get('overall_status', 'unknown').upper()
            status_color = C.GREEN if overall == "PASS" else (C.YELLOW if overall == "DEGRADED" else C.RED)
            lines = [
                f"{C.BOLD}Overall Status:{C.RESET}   {status_color}{C.BOLD}{overall}{C.RESET}",
                f"{C.BOLD}Host Platform:{C.RESET}    {rep.get('platform')}",
                f"{C.BOLD}Python Version:{C.RESET}   {rep.get('python')}",
                f"{C.BOLD}Workbench App:{C.RESET}    {rep.get('workbench_version')}",
                f"{C.BOLD}Decoder Backend:{C.RESET}  {rep.get('backend_version')}",
                "",
                f"{C.BOLD}Subsystem Diagnostics:{C.RESET}",
            ]
            checks_raw = rep.get("checks", [])
            checks_list = checks_raw.values() if isinstance(checks_raw, dict) else checks_raw
            for item in checks_list:
                name = item.get("name", "") if isinstance(item, dict) else str(item)
                st = (item.get("status", "unknown") if isinstance(item, dict) else "").upper()
                detail = (item.get("detail") or item.get("message", "")) if isinstance(item, dict) else ""
                c_st = f"{C.GREEN}✔ PASS{C.RESET}" if st == "PASS" else (f"{C.YELLOW}▲ WARN{C.RESET}" if st == "WARN" else f"{C.RED}✘ FAIL{C.RESET}")
                lines.append(f"  [{c_st}] {C.BOLD}{name:24s}{C.RESET}: {detail}")
            draw_box("SYSTEM DIAGNOSTICS & ACCELERATION REPORT", lines, color=C.GREEN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during diagnostics: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_hardware(args: argparse.Namespace) -> int:
    import hardware_routing as hw

    import os
    import textwrap

    try:
        # detect_hardware() returns a HardwareProfile dataclass, not a mapping.
        info = hw.detect_hardware()
        payload = {
            "cuda": info.cuda_rust,
            "gpu": info.gpu,
            "opencl": info.opencl,
            "opencl_device": info.opencl_device,
            "opencl_host_devices": info.opencl_host_devices,
            "opencl_host_platform": info.opencl_host_platform,
            "opencl_reason": info.opencl_reason,
            "cpu_count": os.cpu_count(),
        }
        if args.json:
            print_json(payload)
        else:
            if not args.no_banner:
                print(banner())
            cuda_st = f"{C.GREEN}Available{C.RESET}" if info.cuda_rust else f"{C.DIM}Unavailable{C.RESET}"
            opencl_st = f"{C.GREEN}Available{C.RESET}" if info.opencl else f"{C.DIM}Unavailable{C.RESET}"
            lines = [
                f"{C.BOLD}CUDA Acceleration:{C.RESET} {cuda_st}",
                f"{C.BOLD}GPU Hardware:{C.RESET}      {info.gpu or 'N/A'}",
                f"{C.BOLD}OpenCL Engine:{C.RESET}     {opencl_st}",
                f"{C.BOLD}OpenCL Device:{C.RESET}     {info.opencl_device or 'N/A'}",
                f"{C.BOLD}Host OpenCL:{C.RESET}       {info.opencl_host_devices} device(s)"
                f"{f' via {info.opencl_host_platform}' if info.opencl_host_platform else ''}",
                f"{C.BOLD}CPU Threads:{C.RESET}       {os.cpu_count()} cores",
            ]
            # Say *why* OpenCL is off, so an unavailable backend never reads as a bug.
            if not info.opencl and info.opencl_reason:
                lines.append("")
                lines.append(f"{C.BOLD}Why OpenCL is unavailable:{C.RESET}")
                for chunk in textwrap.wrap(info.opencl_reason, 62):
                    lines.append(f"  {C.DIM}{chunk}{C.RESET}")
            draw_box("HARDWARE ACCELERATION PROFILES", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error detecting hardware: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_list_codes(args: argparse.Namespace) -> int:
    import backend as be

    try:
        codes = be.list_available_codes()
        if args.json:
            print_json(codes)
        else:
            if not args.no_banner:
                print(banner())
            lines = []
            for fam in codes.get("wired_families", []):
                info = be.get_code_family_info(fam)
                key = f"{C.CYAN}{C.BOLD}{fam:20s}{C.RESET}"
                lines.append(f"  - {key} : {info.get('label', fam)}")
            draw_box(f"SUPPORTED QUANTUM CODE FAMILIES ({len(codes.get('wired_families', []))})", lines, color=C.BLUE)
        return 0
    except Exception as e:
        print(f"{C.RED}Error listing codes: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_list_decoders(args: argparse.Namespace) -> int:
    import backend as be

    try:
        decs = [be.get_decoder_info(k) for k in be.DECODER_KINDS]
        if args.json:
            print_json({"decoders": decs, "count": len(decs)})
        else:
            if not args.no_banner:
                print(banner())
            lines = []
            for d in decs:
                name = f"{C.MAGENTA}{C.BOLD}{d.get('name'):22s}{C.RESET}"
                lines.append(f"  - {name} : {d.get('description')}")
            draw_box(f"SUPPORTED QUANTUM DECODERS ({len(decs)})", lines, color=C.MAGENTA)
        return 0
    except Exception as e:
        print(f"{C.RED}Error listing decoders: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_docgen(args: argparse.Namespace) -> int:
    import backend as be
    import doc_generator as dg

    try:
        code = be.build_code(args.family, args.param)
        gen = dg.ProfessionalDocGenerator()
        formats = args.formats.split(",") if args.formats else ["md", "html", "pdf", "json"]
        paths_map = gen.generate_all(code, formats=formats)
        res = {
            "family": args.family,
            "param": args.param,
            "formats": {fmt: str(p) for fmt, (ok, p) in paths_map.items() if ok},
            "failed_formats": [fmt for fmt, (ok, _) in paths_map.items() if not ok],
        }
        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            lines = [f"{C.BOLD}Generated Formats for {args.family} (d={args.param}):{C.RESET}"]
            for fmt, path in res.get("formats", {}).items():
                lines.append(f"  [{C.GREEN}{fmt.upper():8s}{C.RESET}] {path}")
            if res.get("failed_formats"):
                lines.append(f"  [{C.RED}FAILED  {C.RESET}] {', '.join(res['failed_formats'])}")
            draw_box("DOCUMENT GENERATION COMPLETE", lines, color=C.GREEN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error generating documentation: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_version(args: argparse.Namespace) -> int:
    import version_service as vs

    try:
        report = vs.get_version_report()
        if args.json:
            print_json(report)
        else:
            if not args.no_banner:
                print(banner())
            app_info = report.get("app", {})
            be_info = report.get("backend", {})
            app_ver = app_info.get("local", "N/A")
            be_ver = be_info.get("installed") or be_info.get("local", "N/A")
            any_update = bool(app_info.get("update_available") or be_info.get("update_available"))
            lines = [
                f"{C.BOLD}Workbench App:{C.RESET}    {app_ver} (local bundle)",
                f"{C.BOLD}Decoder Backend:{C.RESET}  {be_ver} (local bundle)",
                f"{C.BOLD}MCP Toolset:{C.RESET}      {MCP_TOOLS} tools (protocol 2024-11-05)",
                f"{C.BOLD}Update Status:{C.RESET}    {C.GREEN}Offline bundle{C.RESET}",
            ]
            draw_box("QECTOR SYSTEM VERSION STATUS", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error querying version: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_update(args: argparse.Namespace) -> int:
    import decoder_provisioner

    try:
        if not args.no_banner and not args.json:
            print(banner())
        res = decoder_provisioner.ensure(allow_upgrade=True)
        if args.json:
            print_json(res)
        else:
            status = f"{C.GREEN}✔ SUCCESS{C.RESET}" if res.get("ok") else f"{C.RED}✘ FAILED{C.RESET}"
            lines = [
                f"{C.BOLD}Update Action:{C.RESET}    {res.get('action')}",
                f"{C.BOLD}Status:{C.RESET}           {status}",
                f"{C.BOLD}Version:{C.RESET}          {res.get('version') or 'N/A'}",
                f"{C.BOLD}Site Path:{C.RESET}        {res.get('path') or 'N/A'}",
                f"{C.BOLD}Message:{C.RESET}          {res.get('message')}",
            ]
            draw_box("LIVE DECODER UPDATE REPORT", lines, color=C.GREEN if res.get("ok") else C.RED)
        return 0 if res.get("ok") else 1
    except Exception as e:
        print(f"{C.RED}Error updating decoder: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_selftest(args: argparse.Namespace) -> int:
    import decoder_provisioner

    try:
        report = decoder_provisioner.self_check()
        if args.json:
            print_json(report)
        else:
            if not args.no_banner:
                print(banner())
            ok = report.get("import_ok", False)
            status_str = f"{C.GREEN}✔ VERIFIED{C.RESET}" if ok else f"{C.RED}✘ UNVERIFIED{C.RESET}"
            lines = [
                f"{C.BOLD}Import Verification:{C.RESET} {status_str}",
                f"{C.BOLD}Active Version:{C.RESET}      {report.get('active_version') or 'N/A'}",
                f"{C.BOLD}Active Site:{C.RESET}         {report.get('active_site') or 'N/A'}",
                f"{C.BOLD}Baseline Version:{C.RESET}    {report.get('baseline_version') or 'N/A'}",
                f"{C.BOLD}ABI Tag:{C.RESET}             {report.get('abi_tag')}",
                f"{C.BOLD}Interpreter:{C.RESET}         {report.get('interpreter')}",
            ]
            draw_box("DECODER PROVISIONER SELF-TEST", lines, color=C.CYAN if ok else C.RED)
        return 0 if ok else 1
    except Exception as e:
        print(f"{C.RED}Error during self-test: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_compliance(args: argparse.Namespace) -> int:
    import compliance

    try:
        # Enforce before attesting: an offline/frozen run must show an ACTIVE
        # guard, not an advisory.  Source runs without QECTOR_AIRGAP/QECTOR_OFFLINE
        # report the honest inactive state (dev mode can reach the network).
        if compliance.airgap_mode():
            compliance.install_egress_guard()
        report = compliance.compliance_report()
        if args.json:
            print_json(report)
        else:
            if not args.no_banner:
                print(banner())
            print(compliance.format_compliance_report(report))
        return 0 if report.get("compliant") else 1
    except Exception as e:
        print(f"{C.RED}Error running compliance check: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_entra(args: argparse.Namespace) -> int:
    try:
        import entra_auth
        action = getattr(args, "action", "status")
        
        if action == "configure":
            cid = getattr(args, "client_id", None)
            ten = getattr(args, "tenant", None)
            grp = getattr(args, "group_id", None)
            scp = getattr(args, "scopes", None)
            cloud = getattr(args, "cloud", "public")
            res = entra_auth.configure(cid, ten, grp, scp, cloud)
            if args.json:
                import json
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"Entra ID configured. Config saved to: {res.get('path')}")
                else:
                    print(f"Configuration failed: {res.get('reason')}", file=sys.stderr)
                    return 1
                    
        elif action == "login":
            flow = getattr(args, "flow", "browser")
            print(f"Starting Entra ID sign-in ({flow} flow)...")
            res = entra_auth.login(flow=flow)
            if args.json:
                import json
                print(json.dumps(res, indent=2))
            else:
                if res.get("ok"):
                    print(f"\\nSigned in successfully as: {res.get('account')}")
                    if res.get("overage"):
                        print("Note: Group overage detected (>200 groups)")
                else:
                    print(f"\\nSign-in failed: {res.get('reason')}", file=sys.stderr)
                    return 1
                    
        elif action == "logout":
            res = entra_auth.logout()
            print("Session cleared.")
            
        elif action == "status":
            if args.json:
                import json
                print(json.dumps(entra_auth.posture(), indent=2))
            else:
                print(entra_auth._format_status())
                
        elif action == "export-voucher":
            path = getattr(args, "file", "voucher.bin")
            res = entra_auth.export_voucher(path)
            if res.get("ok"):
                print(f"Voucher exported to {path}")
            else:
                print(f"Export failed: {res.get('reason')}", file=sys.stderr)
                return 1
                
        elif action == "import-voucher":
            path = getattr(args, "file", "voucher.bin")
            res = entra_auth.import_voucher(path)
            if res.get("ok"):
                print("Voucher imported successfully")
            else:
                print(f"Import failed: {res.get('reason')}", file=sys.stderr)
                return 1
                
        else:
            print(f"Unknown action: {action}", file=sys.stderr)
            return 1
            
        return 0
    except Exception as e:
        print(f"Error executing Entra ID command: {e}", file=sys.stderr)
        return 1



def cmd_compare(args: argparse.Namespace) -> int:
    import backend as be
    try:
        decoders_str = getattr(args, "decoders", "blossom,bp_osd,union_find")
        decoders = [d.strip() for d in decoders_str.split(",") if d.strip()]
        code = be.build_code(args.family, args.distance)
        results = []
        for decoder in decoders:
            try:
                res = be.run_benchmark(
                    code=code,
                    n_samples=args.n_samples,
                    seed=args.seed,
                    decoder_kind=decoder,
                    error_rate=args.error_rate,
                )
                results.append(res)
            except Exception as ex:
                if not getattr(args, "quiet", False):
                    print(f"Skipping decoder {decoder}: {ex}", file=sys.stderr)
        
        if args.json:
            print_json(results)
        else:
            if not args.no_banner:
                print(banner())
            lines = []
            header = f"{'Decoder':20s} | {'Throughput (d/s)':18s} | {'Mean Latency (us)':18s} | {'LER':10s}"
            lines.append(header)
            lines.append("-" * len(header))
            for r in results:
                ler_val = r.get("logical_error_rate")
                ler_str = f"{ler_val:.5f}" if ler_val is not None else "N/A"
                lines.append(
                    f"{r.get('method'):20s} | {r.get('throughput_decodes_per_s', 0):18,.1f} | {r.get('latency_mean_us', 0):18.2f} | {ler_str:10s}"
                )
            draw_box(f"DECODER COMPARISON: {args.family.upper()} (d={args.distance})", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during comparison: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_batch(args: argparse.Namespace) -> int:
    import backend as be
    import numpy as np
    try:
        code = be.build_code(args.family, args.distance)
        res = be.run_batch_decode(
            code=code,
            backend=args.backend,
            n_samples=args.samples,
            error_rate=args.error_rate,
            seed=args.seed,
        )
        if args.json:
            out = {k: v for k, v in res.items() if k not in ("corrections", "syndromes")}
            if getattr(args, "verbose", False):
                out["corrections"] = res["corrections"].tolist()
                out["syndromes"] = res["syndromes"].tolist()
            print_json(out)
        else:
            if not args.no_banner:
                print(banner())
            ler_val = res.get("logical_error_rate")
            ler_str = f"{ler_val:.5f}" if ler_val is not None else "N/A"
            lines = [
                f"{C.BOLD}Backend Used:{C.RESET}      {res.get('backend_used')}",
                f"{C.BOLD}Total Samples:{C.RESET}     {res.get('n_samples')}",
                f"{C.BOLD}Physical Error Rate:{C.RESET} {args.error_rate}",
                f"{C.BOLD}Success Rate:{C.RESET}      {res.get('success_rate'):.5f}",
                f"{C.BOLD}Logical Error Rate:{C.RESET} {ler_str}",
                f"{C.BOLD}Mean Hamming Weight:{C.RESET} {res.get('mean_hamming_weight'):.2f}",
                f"{C.BOLD}Batch Duration:{C.RESET}     {res.get('batch_seconds'):.4f} seconds",
            ]
            if getattr(args, "verbose", False):
                lines.append("")
                lines.append(f"{C.BOLD}Corrections Hamming Weights:{C.RESET}")
                lines.append(f"  {np.sum(res['corrections'], axis=1).tolist()[:20]}...")
            draw_box(f"BATCH DECODE: {args.family.upper()} (d={args.distance})", lines, color=C.GREEN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during batch decode: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_stream(args: argparse.Namespace) -> int:
    import backend as be
    try:
        code = be.build_code(args.family, args.distance)
        res = be.run_streaming_session(
            code=code,
            window_size=args.window,
            n_rounds=args.n_rounds,
            error_rate=args.error_rate,
            seed=args.seed,
            decoder_kind=args.decoder,
        )
        if args.json:
            out = {k: v for k, v in res.items() if k != "committed_corrections"}
            if getattr(args, "verbose", False):
                out["committed_corrections"] = [c.tolist() for c in res["committed_corrections"]]
            print_json(out)
        else:
            if not args.no_banner:
                print(banner())
            ler_val = res.get("logical_error_rate")
            ler_str = f"{ler_val:.5f}" if ler_val is not None else "N/A"
            lines = [
                f"{C.BOLD}Decoder Kind:{C.RESET}      {args.decoder}",
                f"{C.BOLD}Window Size:{C.RESET}       {res.get('window_size')}",
                f"{C.BOLD}Total Rounds:{C.RESET}      {res.get('rounds')}",
                f"{C.BOLD}Physical Error Rate:{C.RESET} {args.error_rate}",
                f"{C.BOLD}Logical Error Rate:{C.RESET} {ler_str}",
                f"{C.BOLD}Session Duration:{C.RESET}    {res.get('session_seconds'):.4f} seconds",
            ]
            draw_box(f"STREAM DECODE: {args.family.upper()} (d={args.distance})", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during streaming: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_train(args: argparse.Namespace) -> int:
    import backend as be
    try:
        code = be.build_code(args.family, args.distance)
        res = be.run_neural_predecoder_training(
            code=code,
            n_samples=args.samples,
            n_epochs=args.epochs,
            error_rate=args.error_rate,
            seed=args.seed,
        )
        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            ler_val = res.get("logical_error_rate")
            ler_str = f"{ler_val:.5f}" if ler_val is not None else "N/A"
            lines = [
                f"{C.BOLD}Train Samples:{C.RESET}     {res.get('n_samples')}",
                f"{C.BOLD}Train Epochs:{C.RESET}      {res.get('n_epochs')}",
                f"{C.BOLD}Exact Match Rate:{C.RESET}  {res.get('exact_match_rate'):.5f}",
                f"{C.BOLD}Bit Accuracy:{C.RESET}      {res.get('bit_accuracy'):.5f}",
                f"{C.BOLD}Syndrome Validity:{C.RESET}  {res.get('syndrome_validity_rate'):.5f}",
                f"{C.BOLD}Logical Error Rate:{C.RESET} {ler_str}",
                f"{C.BOLD}Training Time:{C.RESET}      {res.get('train_seconds'):.4f} seconds",
            ]
            draw_box(f"NEURAL PREDECODER TRAINING: {args.family.upper()} (d={args.distance})", lines, color=C.MAGENTA)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during neural training: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    import backend as be
    from utils import sanitize_export_path
    try:
        ok, safe_path = sanitize_export_path(args.output)
        if not ok:
            print(f"{C.RED}Error: invalid output path or path traversal attempt outside export folder: {args.output}{C.RESET}", file=sys.stderr)
            return 1
        
        family = args.family or "rotated_surface"
        decoder = args.decoder or "blossom"
        
        res = be.export_session(
            code_family=family,
            distance=5,
            decoder_name=decoder,
            error_rate=0.05,
            seed=42,
            output_path=str(safe_path),
        )
        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            lines = [
                f"{C.BOLD}Status:{C.RESET}   {C.GREEN}Exported successfully{C.RESET}",
                f"{C.BOLD}File Path:{C.RESET} {res.get('path')}",
            ]
            draw_box("SESSION EXPORT COMPLETE", lines, color=C.GREEN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during export: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_import(args: argparse.Namespace) -> int:
    import backend as be
    try:
        syndrome = be.import_syndrome(args.file)
        code = be.build_code(args.family, args.distance)
        
        if len(syndrome) != code.n_checks:
            print(f"{C.RED}Error: syndrome length {len(syndrome)} does not match code checks {code.n_checks}{C.RESET}", file=sys.stderr)
            return 1
            
        res = be.decode_syndrome(
            code=code,
            syndrome=syndrome,
            decoder_kind=args.decoder,
        )
        dec_obj = res.get("result")
        res_dict = dec_obj.to_dict()
        res_dict["decoder"] = args.decoder
        res_dict["family"] = args.family
        res_dict["distance"] = args.distance
        res_dict["syndrome_weight"] = int(sum(syndrome))
        
        if args.json:
            print_json(res_dict)
        else:
            if not args.no_banner:
                print(banner())
            valid_str = f"{C.GREEN}✔ VALID{C.RESET}" if res_dict.get("syndrome_valid") else f"{C.RED}✘ INVALID{C.RESET}"
            lines = [
                f"{C.BOLD}Imported File:{C.RESET}      {args.file}",
                f"{C.BOLD}Decoder:{C.RESET}            {args.decoder}",
                f"{C.BOLD}Code Family:{C.RESET}        {args.family} (d={args.distance})",
                f"{C.BOLD}Syndrome Weight:{C.RESET}    {res_dict.get('syndrome_weight')}",
                f"{C.BOLD}Correction Weight:{C.RESET}  {res_dict.get('hamming_weight')}",
                f"{C.BOLD}Syndrome Valid:{C.RESET}     {valid_str}",
            ]
            draw_box("IMPORTED SYNDROME DECODE RESULT", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error during import decode: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_matrix(args: argparse.Namespace) -> int:
    import backend as be
    try:
        matrix = be.get_compatibility_matrix()
        if args.format == "json" or args.json:
            print_json(matrix)
        elif args.format == "csv":
            import csv
            writer = csv.writer(sys.stdout)
            writer.writerow(["Family", "Compatible Decoders"])
            for fam, decs in matrix.items():
                writer.writerow([fam, ",".join(decs)])
        else:
            if not args.no_banner:
                print(banner())
            all_decs = be.DECODER_KINDS
            lines = []
            header = f"{'Code Family':22s} | " + " ".join(f"{d[:3].upper():4s}" for d in all_decs)
            lines.append(header)
            lines.append("-" * len(header))
            
            for fam, decs in matrix.items():
                row = f"{fam:22s} | "
                row_parts = []
                for d in all_decs:
                    icon = " ✔  " if d in decs else " ✘  "
                    row_parts.append(icon)
                lines.append(row + "".join(row_parts))
            
            lines.append("")
            lines.append("Decoder Legend:")
            for d in all_decs:
                lines.append(f"  {d[:3].upper()}: {d}")
                
            draw_box("DECODER / CODE COMPATIBILITY MATRIX", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"{C.RED}Error building matrix: {e}{C.RESET}", file=sys.stderr)
        return 1


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        host = getattr(args, "host", "127.0.0.1")
        port = getattr(args, "port", 8000)
        allow_remote = bool(getattr(args, "allow_remote", False))

        from compliance import airgap_mode
        if airgap_mode():
            if host not in ("127.0.0.1", "::1", "localhost"):
                print(f"Error: Air-gap mode enforces loopback-only binding. Host {host!r} is refused.", file=sys.stderr)
                return 1

        # The REST API has no authentication layer. Refuse non-loopback binds
        # unless the operator explicitly opts in with --allow-remote.
        if host not in ("127.0.0.1", "::1", "localhost") and not allow_remote:
            print(
                f"Error: refusing to bind the unauthenticated REST API to {host!r}.\n"
                "The server has no auth layer; remote binding exposes full decoder control.\n"
                "If you understand the risk and have network-level controls, pass --allow-remote.",
                file=sys.stderr,
            )
            return 1
        if allow_remote and host not in ("127.0.0.1", "::1", "localhost"):
            print(f"WARNING: serving UNAUTHENTICATED API on {host}:{port} — anyone who can reach this port has full access.",
                  file=sys.stderr)

        import qector_decoder_v3.rest_api as api
        if hasattr(api, "run"):
            api.run(host=host, port=port)
        elif hasattr(api, "main"):
            api.main()
        else:
            import uvicorn
            uvicorn.run(api.app, host=host, port=port)
        return 0
    except Exception as e:
        print(f"Error starting REST API server: {e}", file=sys.stderr)
        return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    import backend as be
    try:
        res = be.run_doctor_checks()
        if args.json:
            print_json(res)
        else:
            if not args.no_banner:
                print(banner())
            checks = res.get("checks", [])
            lines = []
            for c in checks:
                name = c.get("check") or c.get("name") or "Check"
                status = c.get("status") or "PASS"
                detail = c.get("detail") or c.get("message") or ""
                remedy = c.get("remedy") or ""
                
                status_color = C.GREEN if status.upper() == "PASS" else (C.YELLOW if status.upper() == "WARN" else C.RED)
                status_icon = f"✔ {status}" if status.upper() == "PASS" else (f"▲ {status}" if status.upper() == "WARN" else f"✘ {status}")
                
                lines.append(f"[{status_color}{status_icon}{C.RESET}] {C.BOLD}{name}{C.RESET}: {detail}")
                if remedy:
                    lines.append(f"  {C.DIM}Remedy: {remedy}{C.RESET}")
            draw_box("QECTOR DOCTOR 15-CHECK ENVIRONMENT DIAGNOSTIC", lines, color=C.CYAN)
        return 0
    except Exception as e:
        print(f"Error running doctor: {e}", file=sys.stderr)
        return 1


def _require_eula_or_exit() -> bool:
    try:
        from utils import get_data_dir, load_json
        if not bool(load_json(get_data_dir() / "preferences.json", {}).get("eula_accepted")):
            print("EULA not yet accepted — please launch the GUI and accept the Licence Agreement first. Test deferred until customer accepts.", file=sys.stderr)
            return False
    except Exception:
        pass
    return True

def cmd_test(args: argparse.Namespace) -> int:
    """Run full verbose Windows test suite (bundled 1.0.0 wheel, air-gapped, no network).

    Gated: requires EULA acceptance. Verbose (-v) streams every test node
    (pytest -v or internal fallback) + fresh docs + wheel SHA256 proof + session
    SHA256 + fresh certification, exactly the boot path but synchronously in the
    CLI so `QectorWorkbench.exe --cli test --verbose` works after customer accepts.
    """
    if not _require_eula_or_exit():
        return 2
    verbose = bool(getattr(args, "verbose", False) or getattr(args, "quiet", False) is False)
    def _print(msg: str) -> None:
        print(msg, flush=True)
    _print("QECTOR CLI test — verbose Windows suite (bundled 1.0.0, air-gapped)")
    _print(f"  verbose={'on' if verbose else 'off'}  backend 1.0.0  wheels/SHA256SUMS.txt  session SHA256")
    try:
        import self_autodebug_backend as sab
        res = sab.run_autodebug_cycle(on_log=_print if verbose else None)
        ok = bool(res.get("ok"))
        sess = (res.get("session") or {}).get("sha256", "")[:16]
        bt = (res.get("boot_tests") or {}).get("outcome", "?")
        bh = (res.get("backend_health") or {}).get("method", "?")
        if getattr(args, "json", False):
            import json
            print(json.dumps(res, indent=2, default=str))
        else:
            _print(f"Result: backend={bh} boot_tests={bt} session={sess}… ok={ok}")
            cnt = (res.get("boot_tests") or {}).get("counts", {})
            if cnt:
                _print(f"Counts: {cnt}")
        return 0 if ok else 1
    except Exception as exc:
        import traceback
        print(f"CLI test failed: {type(exc).__name__}: {exc}\n{traceback.format_exc()}", file=sys.stderr)
        return 1


def cmd_completions(args: argparse.Namespace) -> int:
    shell = args.shell
    if shell == "bash":
        print("""_qector_completion() {
    local cur prev opts
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    opts="decode benchmark probe diagnostics hardware list-codes list-decoders docgen version update selftest compare batch stream train export import matrix serve doctor decode_mmap completions --json --no-color --no-banner --output --verbose --quiet --config"

    if [[ ${COMP_CWORD} -eq 1 ]] ; then
        COMPREPLY=( $(compgen -W "${opts}" -- ${cur}) )
        return 0
    fi
}
complete -F _qector_completion qector""")
    elif shell == "zsh":
        print("""#compdef qector
_qector() {
    local line
    _arguments -C \\
        '1: :(decode benchmark probe diagnostics hardware list-codes list-decoders docgen version update selftest compare batch stream train export import matrix serve doctor decode_mmap completions)' \\
        '*:: :->args'
}""")
    elif shell == "powershell":
        print("""Register-ArgumentCompleter -CommandName qector -ScriptBlock {
    param($commandName, $parameterName, $wordToComplete, $commandAst, $fakeBoundParameters)
    $choices = @('decode', 'benchmark', 'probe', 'diagnostics', 'hardware', 'list-codes', 'list-decoders', 'docgen', 'version', 'update', 'selftest', 'compare', 'batch', 'stream', 'train', 'export', 'import', 'matrix', 'serve', 'doctor', 'compliance', 'decode_mmap', 'completions')
    $choices | Where-Object { $_ -like "$wordToComplete*" } | ForEach-Object { [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_) }
}""")
    else:
        print(f"Unsupported shell: {shell}", file=sys.stderr)
        return 1
    return 0


def cmd_decode_mmap(args: argparse.Namespace) -> int:
    import backend as be
    from qector_decoder_v3 import decode_mmap
    code = be.build_code(args.family, args.distance)
    print(f"Decoding out-of-core from {args.input} to {args.output}...")
    try:
        decode_mmap(
            args.input, args.output, code.check_to_qubits, int(code.n_qubits),
            decoder_type=args.decoder, batch_size=args.batch_size,
            n_shots=args.shots, verbose=not getattr(args, "quiet", False)
        )
        print("Done.")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

def _add_common_flags(p: argparse.ArgumentParser) -> None:
    """Add the global flags to *p* (used for the top-level parser AND every
    subparser, so ``qector --json doctor`` and ``qector doctor --json`` both
    work). Defaults are SUPPRESS so an explicit pre-subcommand flag is not
    clobbered by the subparser's default; main() fills them in afterwards.
    Flags the subparser already defines itself are skipped."""
    have: set[str] = set()
    for act in p._actions:
        have.update(act.option_strings)
    def _add(*opts: str, **kw) -> None:
        if not any(o in have for o in opts):
            p.add_argument(*opts, **kw)
    _add("--json", action="store_true", default=argparse.SUPPRESS,
         help="Output raw results in JSON format")
    _add("--no-color", action="store_true", default=argparse.SUPPRESS,
         help="Disable ANSI colors")
    _add("--no-banner", action="store_true", default=argparse.SUPPRESS,
         help="Suppress ASCII header banner")
    _add("--output", "-o", default=argparse.SUPPRESS,
         help="Redirect output to a file")
    _add("--verbose", "-v", action="store_true", default=argparse.SUPPRESS,
         help="Enable verbose logging")
    _add("--quiet", "-q", action="store_true", default=argparse.SUPPRESS,
         help="Enable quiet mode (only errors)")


# Fallbacks applied in main() for flags that were never provided anywhere.
_COMMON_FLAG_DEFAULTS = {"json": False, "no_color": False, "no_banner": False,
                         "output": None, "verbose": False, "quiet": False}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qector",
        description="QECTOR Decoder Workbench, unified command line interface",
    )
    # Global flags (also accepted after the subcommand — see _add_common_flags)
    _add_common_flags(parser)
    parser.add_argument("--config", "-c", help="Path to config file loading JSON parameters")
    parser.add_argument("--version", "-V", action="store_true", help="Show version information and exit")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # decode
    p_decode = subparsers.add_parser("decode", help="Run a single quantum error correction decode")
    p_decode.add_argument("--family", "-f", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_decode.add_argument("--distance", "-d", type=int, default=5, help="Code distance / parameter (default: 5)")
    p_decode.add_argument("--decoder", "-m", default="blossom", help="Decoder kind (default: blossom)")
    p_decode.add_argument("--error-rate", "-p", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_decode.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    p_decode.add_argument("--dry-run", action="store_true", help="Validate parameters without running")
    p_decode.set_defaults(func=cmd_decode)

    # benchmark
    p_bench = subparsers.add_parser("benchmark", help="Run throughput benchmark for a decoder")
    p_bench.add_argument("--family", "-f", default="repetition", help="Code family (default: repetition)")
    p_bench.add_argument("--distance", "-d", type=int, default=5, help="Code distance (default: 5)")
    p_bench.add_argument("--decoder", "-m", default="blossom", help="Decoder kind (default: blossom)")
    p_bench.add_argument("--samples", "-n", type=int, default=40, help="Number of benchmark trials (default: 40)")
    p_bench.add_argument("--error-rate", "-p", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_bench.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    p_bench.add_argument("--dry-run", action="store_true", help="Validate parameters without running")
    p_bench.add_argument(
        "--verify", "-V", action="store_true",
        help="Verify against reference LER for the given decoder/family/distance/rate "
             "using seed 42. Exits 0 on match, 1 on mismatch.",
    )
    p_bench.set_defaults(func=cmd_benchmark)

    # probe
    p_probe = subparsers.add_parser("probe", help="Probe all decoders on a code family")
    p_probe.add_argument("--family", "-f", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_probe.add_argument("--distance", "-d", type=int, default=3, help="Code distance (default: 3)")
    p_probe.add_argument("--error-rate", "-p", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_probe.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    p_probe.set_defaults(func=cmd_probe)

    # diagnostics
    p_diag = subparsers.add_parser("diagnostics", help="Run system self-diagnostics report")
    p_diag.set_defaults(func=cmd_diagnostics)

    # hardware
    p_hw = subparsers.add_parser("hardware", help="Show hardware detection and acceleration info")
    p_hw.set_defaults(func=cmd_hardware)

    # list-codes
    p_lc = subparsers.add_parser("list-codes", help="List all supported quantum code families")
    p_lc.set_defaults(func=cmd_list_codes)

    # list-decoders
    p_ld = subparsers.add_parser("list-decoders", help="List all supported decoders")
    p_ld.set_defaults(func=cmd_list_decoders)

    # docgen
    p_doc = subparsers.add_parser("docgen", help="Generate code family documentation")
    p_doc.add_argument("--family", "-f", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_doc.add_argument("--param", "-p", type=int, default=5, help="Distance or size parameter (default: 5)")
    p_doc.add_argument("--formats", default="md,html,pdf,json", help="Comma-separated formats (md,html,pdf,json,latex,svg)")
    p_doc.set_defaults(func=cmd_docgen)

    # version
    p_version = subparsers.add_parser("version", help="Show workbench and backend versions and update status")
    p_version.set_defaults(func=cmd_version)

    # New subcommands from finaldev.md tasks 5.1-5.7
    # compare
    p_comp = subparsers.add_parser("compare", help="Compare multiple decoders on the same code and return comparison table")
    p_comp.add_argument("--family", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_comp.add_argument("--distance", type=int, default=5, help="Code distance (default: 5)")
    p_comp.add_argument("--error-rate", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_comp.add_argument("--n-samples", type=int, default=50, help="Number of samples per decoder (default: 50)")
    p_comp.add_argument("--seed", type=int, default=42, help="Determinism seed (default: 42)")
    p_comp.add_argument("--decoders", default="blossom,bp_osd,union_find", help="Comma-separated decoders to compare")
    p_comp.set_defaults(func=cmd_compare)

    # batch
    p_batch = subparsers.add_parser("batch", help="Batch decode multiple syndromes")
    p_batch.add_argument("--family", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_batch.add_argument("--distance", type=int, default=5, help="Code distance (default: 5)")
    p_batch.add_argument("--backend", default="cpu", choices=["cpu", "cuda", "auto"], help="Backend target (default: cpu)")
    p_batch.add_argument("--samples", type=int, default=100, help="Number of samples (default: 100)")
    p_batch.add_argument("--error-rate", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_batch.add_argument("--seed", type=int, default=42, help="Determinism seed (default: 42)")
    p_batch.set_defaults(func=cmd_batch)

    # stream
    p_stream = subparsers.add_parser("stream", help="Streaming decode workflow")
    p_stream.add_argument("--family", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_stream.add_argument("--distance", type=int, default=5, help="Code distance (default: 5)")
    p_stream.add_argument("--window", type=int, default=5, help="Sliding window size (default: 5)")
    p_stream.add_argument("--n-rounds", type=int, default=8, help="Number of rounds (default: 8)")
    p_stream.add_argument("--error-rate", type=float, default=0.03, help="Physical error rate (default: 0.03)")
    p_stream.add_argument("--seed", type=int, default=5, help="Determinism seed (default: 5)")
    p_stream.add_argument("--decoder", default="union_find", help="Decoder family (default: union_find)")
    p_stream.add_argument("--dry-run", action="store_true", help="Validate parameters without running")
    p_stream.set_defaults(func=cmd_stream)

    # train
    p_train = subparsers.add_parser("train", help="Train neural predecoder")
    p_train.add_argument("--family", default="repetition", help="Code family (default: repetition)")
    p_train.add_argument("--distance", type=int, default=3, help="Code distance (default: 3)")
    p_train.add_argument("--samples", type=int, default=200, help="Number of training samples (default: 200)")
    p_train.add_argument("--epochs", type=int, default=5, help="Number of training epochs (default: 5)")
    p_train.add_argument("--error-rate", type=float, default=0.05, help="Physical error rate (default: 0.05)")
    p_train.add_argument("--seed", type=int, default=8, help="Determinism seed (default: 8)")
    p_train.set_defaults(func=cmd_train)

    # export
    p_export = subparsers.add_parser("export", help="Export a complete decode session")
    p_export.add_argument("--format", default="zip", choices=["zip", "json", "yaml"], help="Export format (default: zip)")
    p_export.add_argument("--output", default="session.zip", help="Output file path (default: session.zip)")
    p_export.add_argument("--decoder", help="Specific decoder for export")
    p_export.add_argument("--family", help="Specific code family for export")
    p_export.set_defaults(func=cmd_export)

    # import
    p_import = subparsers.add_parser("import", help="Import external syndrome data")
    p_import.add_argument("--file", required=True, help="Input file path (CSV, JSON, .npy)")
    p_import.add_argument("--decoder", default="blossom", help="Decoder family (default: blossom)")
    p_import.add_argument("--family", default="rotated_surface", help="Code family (default: rotated_surface)")
    p_import.add_argument("--distance", type=int, default=5, help="Code distance (default: 5)")
    p_import.add_argument("--seed", type=int, default=42, help="Determinism seed (default: 42)")
    p_import.set_defaults(func=cmd_import)

    # matrix
    p_matrix = subparsers.add_parser("matrix", help="Return the full decoder/code compatibility matrix")
    p_matrix.add_argument("--format", default="table", choices=["table", "json", "csv"], help="Output format (default: table)")
    p_matrix.set_defaults(func=cmd_matrix)

    # serve
    p_serve = subparsers.add_parser("serve", help="Launch local REST API service")
    p_serve.add_argument("--host", default="127.0.0.1", help="Host interface to bind to (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port to bind to (default: 8000)")
    p_serve.add_argument("--allow-remote", action="store_true",
                         help="Explicitly allow binding to a non-loopback host (the API has no auth layer)")
    p_serve.set_defaults(func=cmd_serve)

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Run 15-check environment diagnostic")
    p_doctor.set_defaults(func=cmd_doctor)

    # compliance
    p_compliance = subparsers.add_parser("compliance", help="Run zero-egress / offline compliance attestation")
    p_compliance.set_defaults(func=cmd_compliance)

    # entra
    p_entra = subparsers.add_parser("entra", help="Optional Microsoft Entra ID SSO readiness (off by default)")
    p_entra.add_argument("action", nargs="?", default="status",
                         choices=["status", "configure", "login", "logout", "export-voucher", "import-voucher"],
                         help="Action (default: status)")
    p_entra.add_argument("--client-id", help="Entra ID application (client) ID")
    p_entra.add_argument("--tenant", help="Entra ID tenant ID or verified domain")
    p_entra.add_argument("--group-id", help="Entra ID group that gates Enterprise entitlement")
    p_entra.add_argument("--scopes", nargs="*", default=None, help="OAuth scopes (default: User.Read)")
    p_entra.add_argument("--cloud", default="public", help="Target cloud environment")
    p_entra.add_argument("--flow", default="browser", choices=["browser", "broker", "device"], help="Auth flow to use (default: browser)")
    p_entra.add_argument("--file", default="voucher.bin", help="Path for voucher import/export")
    p_entra.set_defaults(func=cmd_entra)

    # decode_mmap
    p_mmap = subparsers.add_parser("decode_mmap", help="Out-of-core memmap decoding")
    p_mmap.add_argument("--family", default="rotated_surface", help="Code family")
    p_mmap.add_argument("--distance", type=int, default=5, help="Code distance")
    p_mmap.add_argument("--input", required=True, help="Input syndrome .npy file")
    p_mmap.add_argument("--output", required=True, help="Output correction .npy file")
    p_mmap.add_argument("--decoder", default="cpu_batch", help="Decoder type")
    p_mmap.add_argument("--batch-size", type=int, default=65536, help="Batch size")
    p_mmap.add_argument("--shots", type=int, default=None, help="Total shots (optional)")
    p_mmap.set_defaults(func=cmd_decode_mmap)

    # test (verbose Windows)
    p_test = subparsers.add_parser("test", help="Run full verbose test suite (bundled 1.0.0, air-gapped, Windows)")
    p_test.add_argument("--verbose", "-v", action="store_true", default=False, help="Stream verbose per-test output")
    p_test.set_defaults(func=cmd_test)

    # completions
    p_comp_shell = subparsers.add_parser("completions", help="Generate shell completions")
    p_comp_shell.add_argument("--shell", default="bash", choices=["bash", "zsh", "powershell"], help="Shell type (default: bash)")
    p_comp_shell.set_defaults(func=cmd_completions)

    # Accept the global flags after ANY subcommand too (qector doctor --json).
    for _sub in subparsers.choices.values():
        _add_common_flags(_sub)

    return parser


def main(args_list: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(args_list)
    # Fill in global flags that were never provided (SUPPRESS defaults).
    for _attr, _default in _COMMON_FLAG_DEFAULTS.items():
        if not hasattr(args, _attr):
            setattr(args, _attr, _default)
    
    # Load config file if specified
    if args.config:
        try:
            with open(args.config, "r", encoding="utf-8") as f:
                config_data = json.load(f)
            for k, v in config_data.items():
                if hasattr(args, k):
                    setattr(args, k, v)
        except Exception as e:
            print(f"Error loading config file {args.config}: {e}", file=sys.stderr)
            return 1
            
    if args.no_color or getattr(args, "quiet", False):
        C.disable()
        
    # Redirect stdout if --output is specified
    stdout_redir = None
    if args.output:
        from utils import sanitize_export_path
        ok, safe_path = sanitize_export_path(args.output)
        if not ok:
            print(f"Error: Invalid output path (resolves outside export directory): {args.output}", file=sys.stderr)
            return 1
        try:
            stdout_redir = open(safe_path, "w", encoding="utf-8")
            sys.stdout = stdout_redir
        except Exception as e:
            print(f"Error opening output file {safe_path}: {e}", file=sys.stderr)
            return 1
            
    try:
        if getattr(args, "version", False):
            return cmd_version(args)
        if not hasattr(args, "func"):
            if not getattr(args, "quiet", False):
                print(banner())
                parser.print_help()
            return 0
            
        # Dry-run validation
        if getattr(args, "dry_run", False) and args.command in ("decode", "benchmark", "stream"):
            import backend as be
            try:
                dist = getattr(args, "distance", getattr(args, "param", 5))
                ok, msg = be.validate_parameter(args.family, dist)
                if not ok:
                    print(f"Validation FAILED: {msg}", file=sys.stderr)
                    return 1
                if not getattr(args, "quiet", False):
                    print(f"Validation PASSED: family={args.family}, parameters are valid (dry-run)")
                return 0
            except Exception as e:
                print(f"Validation FAILED: {e}", file=sys.stderr)
                return 1
                
        return args.func(args)
    finally:
        if stdout_redir is not None:
            stdout_redir.close()
            sys.stdout = sys.__stdout__


if __name__ == "__main__":
    sys.exit(main())

