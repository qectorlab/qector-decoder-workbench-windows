"""generate_manuals.py  -  build the QECTOR Decoder Workbench public documentation.

Every document is derived from the LIVE application (the MCP tool registry, the
backend decoder and code-family tables, and the version module) so the content
can never drift from the software or invent tools that do not exist.

Deliverables (written to the output directory, default ``manuals``):

  1. QECTOR_User_Manual_Windows.pdf   professional, formatted, linked contents
  2. QECTOR_User_Manual_Linux.pdf
  3. QECTOR_User_Manual_macOS.pdf
  4. QECTOR_Quick_Start_Guide.pdf      one-look install-and-run, all platforms
  5. QECTOR_MCP_Integration_Guide.pdf  connect agents/clients to the MCP server
  6. QECTOR_LLM_Manual.json            machine manual for LLM agents
  7. README.txt                        index of the documentation set

House style: no em dashes or en dashes in prose (a sanitiser enforces this);
ASCII hyphens survive only inside identifiers such as qector-decoder-v3 and the
--mcp flag.

Usage:
    python generate_manuals.py [--outdir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import version


# --- dash-free sanitiser -------------------------------------------------------
def san(text: str) -> str:
    if text is None:
        return ""
    res = (str(text)
           .replace("  -  ", ", ")
           .replace(" - ", ", ")
           .replace("  -  ", " to ")
           .replace(" - ", " to ")
           .replace("−", "-")
           .replace(" ", " "))
    while "  " in res:
        res = res.replace("  ", " ")
    return res.replace(" ,", ",")


def esc(text: str) -> str:
    s = san(text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# Archival DOIs for the published record set.  Kept here so regenerating the
# documentation cannot silently drop them: they previously existed only in a
# hand-edited README.txt in the distributed set and no generator knew about them.
ZENODO_DOIS = [
    ("User Manual & Licensing", "https://doi.org/10.5281/zenodo.21363016"),
    ("Architecture Whitepaper", "https://doi.org/10.5281/zenodo.21320543"),
]


# --- live application facts ----------------------------------------------------
def gather_facts() -> dict:
    import version
    import backend as be
    from mcp_server import get_mcp_server, PROTOCOL_VERSION, SERVER_NAME

    reg = get_mcp_server().tools.tools
    tools = []
    for name in sorted(reg):
        spec = reg[name]
        params = []
        example_args = {}
        for pname, pspec in spec.get("parameters", {}).items():
            required = "default" not in pspec
            entry = {
                "name": pname,
                "type": pspec.get("type", "any"),
                "required": required,
                "description": san(pspec.get("description", "")),
            }
            if not required:
                entry["default"] = pspec.get("default")
                example_args[pname] = pspec.get("default")
            else:
                t = pspec.get("type", "string")
                example_args[pname] = {"string": "REPLACE_ME", "integer": 0,
                                       "number": 0.0, "boolean": False,
                                       "array": [], "object": {}}.get(t, "REPLACE_ME")
            if "items" in pspec:
                entry["items"] = pspec["items"]
            params.append(entry)
        tools.append({
            "name": name,
            "description": san(spec.get("description", "")),
            "parameters": params,
            "example_call": {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": example_args},
            },
        })

    decoders = [{"kind": k, "description": san(be.get_decoder_info(k)["description"])}
                for k in be.DECODER_KINDS]
    qldpc = set(getattr(be, "QLDPC_FAMILIES", set()))
    families = []
    for key in be.CODE_FAMILIES:
        info = be.get_code_family_info(key)
        families.append({"key": key, "label": san(info["label"]),
                         "class": "qLDPC (non graphlike)" if key in qldpc else "graphlike"})

    return {
        "product": "QECTOR Decoder Workbench",
        "app_version": version.WORKBENCH_VERSION,
        "backend_package": "qector-decoder-v3",
        "backend_version": version.BACKEND_VERSION,
        "min_backend_version": version.MIN_BACKEND_VERSION,
        "mcp_tools_count": len(tools),
        "protocol_version": PROTOCOL_VERSION,
        "server_name": SERVER_NAME,
        "author": version.AUTHOR,
        "author_orcid": version.AUTHOR_ORCID,
        "project_url": version.PROJECT_URL,
        # Business / licensing facts, single-sourced from version.py so the manuals,
        # the CLI and the in-app Developer and Licensing panel cannot drift apart.
        "company": version.COMPANY,
        "maintainer": version.MAINTAINER,
        "contact_email": version.CONTACT_EMAIL,
        "pricing_url": version.PRICING_URL,
        "licence_summary": version.LICENCE_SUMMARY,
        "licence_evaluation": version.LICENCE_EVALUATION,
        "tools": tools,
        "decoders": decoders,
        "families": families,
        "qldpc_keys": sorted(qldpc),
    }


# --- decoder selection guidance (authored, stable across builds) ---------------
DECODER_GUIDE = {
    "union_find": "Fast graphlike matching for surface, repetition and ring codes. Not for qLDPC.",
    "fast_union_find": "SIMD-accelerated union-find; highest throughput on graphlike codes. Not for qLDPC.",
    "blossom": "Exact minimum-weight perfect matching. Best accuracy on graphlike codes; slower.",
    "sparse_blossom": "Region-growing matcher; strong accuracy with better scaling than exact blossom.",
    "bp_osd": "Belief propagation with ordered-statistics decoding. The go-to decoder for qLDPC codes.",
    "auto": "Policy decoder: inspects the code and dispatches a suitable concrete decoder automatically.",
    "hybrid": "Neural pre-decoder feeding sparse blossom; adaptive edge weights for enriched decoding.",
    "lookup_table": "Exact table for small codes only (refused above 20 checks). Constant-time lookups.",
    "predecoded": "A pre-decoding stage in front of a matcher to reduce work on likely error patterns.",
    "auto_router": "Routes graphlike codes to matching and qLDPC codes to BP-OSD; safe default everywhere.",
}

FAMILY_GUIDE = {
    "repetition": "1D chain that protects against bit-flip errors. The simplest teaching code.",
    "ring": "Periodic 1D code (repetition on a ring). Good for streaming and cyclic examples.",
    "rotated_surface": "Compact rotated surface code; the standard workhorse for 2D QEC studies.",
    "unrotated_surface": "Classic surface code layout with boundary stabilisers.",
    "toric": "Surface code on a torus (periodic boundaries); two logical qubits.",
    "heavy_hex": "IBM heavy-hexagon layout used by superconducting hardware.",
    "bicycle": "A qLDPC code that every applicable decoder can handle.",
    "bivariate_bicycle": "The IBM bivariate-bicycle family, e.g. the [[72,12,6]] gross code.",
    "hypergraph_product": "Constructs a CSS code from two classical codes; graphlike here.",
}


# --- platform prose (bundled, self-contained; dash free) -----------------------
PLATFORM = {
    "Windows": {
        "run_cmd": "QectorWorkbench-Portable.exe",
        "mcp_cmd": "QectorWorkbench-Portable.exe --mcp",
        "data_dir": "%LOCALAPPDATA%\\QectorWorkbench",
        "prereq": [
            "Windows 10 or Windows 11, 64-bit.",
            "About 300 MB of free disk space.",
            "No internet connection, Python, or pip required: the decoder wheel is "
            "bundled inside the application and activates automatically on first "
            "launch, entirely offline.",
        ],
        "install": [
            ("Option A: single-file portable (recommended)", [
                "Download QectorWorkbench-Portable.exe.",
                "Double-click it. Nothing to install; it runs from anywhere, "
                "including a USB stick. On first launch it activates the bundled "
                "decoder into a per-user site.",
            ]),
            ("Option B: Windows installer", [
                "Download and run QectorWorkbenchSetup.exe.",
                "Follow the wizard. It adds a Start-menu entry and a desktop icon. "
                "On first launch it activates the bundled decoder.",
            ]),
        ],
    },
    "Linux": {
        "run_cmd": "qector-workbench",
        "mcp_cmd": "qector-workbench --mcp",
        "data_dir": "$XDG_DATA_HOME/QectorWorkbench or ~/.local/share/QectorWorkbench",
        "prereq": [
            "A 64-bit x86_64 Linux desktop built on glibc 2.31 or newer: Ubuntu "
            "20.04+, Debian 11+, Linux Mint 20+, antiX 21+, MX 21+, Fedora 33+, "
            "openSUSE Leap 15.3+, recent Arch, and anything newer.",
            "About 150 MB of free disk space.",
            "The decoder wheel is bundled; it activates automatically on first "
            "launch, entirely offline (no internet, pip, or extra Python needed).",
        ],
        "install": [
            ("Ubuntu, Debian, Mint", [
                f"sudo dpkg -i ./qector-workbench_{version.WORKBENCH_VERSION}_amd64.deb",
                "sudo apt-get -f install    (only if dpkg reports missing dependencies)",
                "A QECTOR icon appears in the Science menu.",
                "Remove it later with:  sudo apt remove qector-workbench",
            ]),
            ("antiX, MX and other Debian-family distributions", [
                f"sudo dpkg -i ./qector-workbench_{version.WORKBENCH_VERSION}_amd64.deb",
                "sudo apt-get -f install    (only if dpkg reports missing dependencies)",
            ]),
        ],
    },
    "macOS": {
        "run_cmd": "QECTOR Workbench.app",
        "mcp_cmd": "/Applications/QectorWorkbench.app/Contents/MacOS/QectorWorkbench --mcp",
        "data_dir": "~/Library/Application Support/QectorWorkbench",
        "prereq": [
            "macOS 11 (Big Sur) or newer, Apple Silicon or Intel.",
            "About 300 MB of free disk space.",
            "The decoder wheel is bundled; it activates automatically on first "
            "launch, entirely offline (no internet, pip, or extra Python needed).",
        ],
        "install": [
            ("Install from the disk image", [
                "Open QectorWorkbench.dmg and drag QECTOR Workbench to Applications.",
                "First launch: right-click the app and choose Open, then confirm, "
                "so Gatekeeper allows the unsigned build to run.",
            ]),
        ],
    },
}

TABS = [
    ("Code Explorer",
     "Pick one of the nine code families, set the distance, and inspect the code: "
     "qubit and check counts, code distance, and a readable Tanner graph. This is "
     "where you build the code that the other tabs operate on."),
    ("Decoder Lab",
     "Run a single seeded decode with any of the ten decoders. The panel reports the "
     "correction weight, whether the correction reproduces the observed syndrome "
     "(syndrome_valid), and whether a logical operator was flipped. If a chosen "
     "decoder cannot handle the selected code, a resilient fallback recovers "
     "automatically and records the full attempt trace."),
    ("Benchmark",
     "Sweep a decoder over many seeded shots and read throughput (decodes per "
     "second), latency percentiles, and the logical error rate for that workload."),
    ("Batch and Streaming",
     "Decode a batch of syndromes on the CPU, CUDA or OpenCL backend, and run a "
     "sliding-window streaming session with per-round commit telemetry."),
    ("Hardware",
     "Detect CUDA and OpenCL availability, show system information, and read a "
     "decoder recommendation that never suggests a decoder the selected code "
     "cannot use."),
    ("Diagnostics",
     "Run a full self-test of the environment, probe which decoders work for a "
     "given code, and exercise the resilient decode path with a complete trace."),
    ("Documentation",
     "Export a provenance-stamped record of the current code in six formats: "
     "Markdown, JSON, HTML, LaTeX, PDF and SVG."),
    ("Console",
     "A live, severity-coloured log of every operation, kept in one place for "
     "review and troubleshooting."),
]

GLOSSARY = [
    ("Stabiliser code", "A quantum error-correcting code defined by a set of commuting "
                        "parity checks (stabilisers) whose measurement reveals errors without "
                        "disturbing the encoded information."),
    ("Syndrome", "The pattern of parity-check outcomes. A decoder maps a syndrome to a "
                 "correction that explains it."),
    ("Decoder", "An algorithm that turns a syndrome into a correction. QECTOR ships ten."),
    ("Code distance", "The size of the smallest error that can cause an undetected logical "
                      "failure. Larger distance means stronger protection."),
    ("Graphlike code", "A code whose errors can be represented as edges in a graph, so "
                       "matching decoders (union-find, blossom) apply directly."),
    ("qLDPC code", "A quantum low-density parity-check code (here bicycle and bivariate "
                   "bicycle). Not graphlike; decode with BP-OSD or the routed decoders."),
    ("Logical error rate", "The simulated probability that a decode leaves an undetected "
                          "logical error. A simulation output, not a fixed benchmark."),
    ("MCP", "Model Context Protocol. A JSON-RPC interface that lets automation and "
            "language-model agents drive the workbench headlessly."),
]

RULES = [
    "Call only tools whose exact name is listed in this manual. Never invent a tool "
    "name, a parameter name, a decoder kind, or a code family. Unknown names return an error.",
    "Use only the decoder kinds in the decoders list and the code families in the "
    "families list. Passing any other value returns a handled error, not a result.",
    "Report tool errors exactly as returned. When a tool result carries isError true, "
    "relay the message text and do not fabricate or guess a successful outcome.",
    "Do not claim a decode succeeded unless the returned object has syndrome_valid true. "
    "Every accepted correction satisfies the parity-check equation H times c equals s "
    "modulo 2; do not assert any property that is not present in the returned object.",
    "Treat logical error rate, throughput and latency as simulation outputs that depend "
    "on the seed, the hardware, the driver and the workload. Never present them as fixed "
    "or universal benchmarks. State that they must be regenerated for the target setting.",
    "The qLDPC families bicycle and bivariate_bicycle are not graphlike. Decode them with "
    "bp_osd, blossom, sparse_blossom, hybrid, predecoded, auto or auto_router. The "
    "union_find, fast_union_find and lookup_table decoders do not apply to them.",
    "Version numbers are resolved live at runtime. Do not state a version without first "
    "reading it from the version_info or get_system_info tool.",
    "The decoder wheel is bundled inside the application. On first launch the app extracts "
    "and activates the bundled qector-decoder-v3 wheel into an ABI-scoped managed site, "
    "entirely offline. Any outdated managed decoder from an older release is purged "
    "automatically before activation. Do not claim the app downloads anything at runtime.",
    "OpenCL is reported unavailable by the published qector-decoder-v3 wheel because that wheel "
    "is built without its OpenCL feature, not because the machine lacks a GPU. No environment "
    "variable can turn it on. Do not tell a user to fix drivers or set a flag to enable it; CUDA "
    "and CPU are unaffected.",
    "When a large array in a result is summarised or truncated, do not extrapolate or invent "
    "the omitted values. Work only from what the result actually contains.",
    "The destructive tools (for example those that clear results, delete a resource or reset "
    "configuration) require confirm set to true. Do not call them unless the user explicitly "
    "asked for that action.",
    "If a tool call fails or times out, say so plainly. Do not present a placeholder or an "
    "assumed value as though it came from the server.",
]


# ===============================================================================
# reportlab shared toolkit
# ===============================================================================
def _rl():
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, BaseDocTemplate, PageTemplate, Paragraph, Spacer, Table,
        TableStyle, Image as RLImage, PageBreak, ListFlowable, ListItem,
        Preformatted, HRFlowable, Frame,
    )
    from reportlab.platypus.tableofcontents import TableOfContents
    return dict(locals())


def _make_styles(R):
    colors = R["colors"]; ParagraphStyle = R["ParagraphStyle"]
    TA_CENTER = R["TA_CENTER"]; TA_LEFT = R["TA_LEFT"]
    ss = R["getSampleStyleSheet"]()
    NAVY = colors.HexColor("#14315c")
    BLUE = colors.HexColor("#2f6fb0")
    LIGHT = colors.HexColor("#eef3fb")
    GREY = colors.HexColor("#555555")
    INK = colors.HexColor("#20242b")
    return NAVY, BLUE, LIGHT, GREY, {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName="Helvetica-Bold",
                                fontSize=32, textColor=NAVY, leading=36, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("st", parent=ss["Normal"], fontSize=13, textColor=GREY,
                                   alignment=TA_CENTER, leading=18),
        "kicker": ParagraphStyle("k", parent=ss["Normal"], fontSize=11, textColor=BLUE,
                                 alignment=TA_CENTER, leading=15, fontName="Helvetica-Bold"),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                             fontSize=17, textColor=NAVY, spaceBefore=8, spaceAfter=6, leading=21),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                             fontSize=12.5, textColor=BLUE, spaceBefore=10, spaceAfter=3, leading=16),
        "body": ParagraphStyle("b", parent=ss["Normal"], fontSize=10.3, leading=15.2,
                               alignment=TA_LEFT, spaceAfter=6, textColor=INK),
        "bullet": ParagraphStyle("bu", parent=ss["Normal"], fontSize=10.3, leading=14.8, textColor=INK),
        "code": ParagraphStyle("c", parent=ss["Code"], fontName="Courier", fontSize=8.8,
                              leading=12, textColor=colors.HexColor("#0d223a"),
                              backColor=LIGHT, borderPadding=6, spaceBefore=2, spaceAfter=8),
        "cell": ParagraphStyle("ce", parent=ss["Normal"], fontSize=8.7, leading=11.6, textColor=INK),
        "cellb": ParagraphStyle("ceb", parent=ss["Normal"], fontSize=8.7, leading=11.6,
                               fontName="Helvetica-Bold", textColor=NAVY),
        "note": ParagraphStyle("n", parent=ss["Normal"], fontSize=9.6, leading=13.6,
                              textColor=colors.HexColor("#20242b"), backColor=colors.HexColor("#e9f6ee"),
                              borderColor=colors.HexColor("#2e7d46"), borderWidth=0.6,
                              borderPadding=7, spaceBefore=3, spaceAfter=9),
        "toc1": ParagraphStyle("toc1", parent=ss["Normal"], fontSize=10.5, leading=17,
                               fontName="Helvetica-Bold", textColor=NAVY),
        "toc2": ParagraphStyle("toc2", parent=ss["Normal"], fontSize=9.6, leading=14,
                               leftIndent=16, textColor=INK),
    }


class _Book:
    """Small builder wrapping reportlab flowables with a linked Table of Contents."""

    def __init__(self, facts, footer_text):
        self.facts = facts
        self.footer_text = footer_text
        self.R = _rl()
        self.NAVY, self.BLUE, self.LIGHT, self.GREY, self.S = _make_styles(self.R)
        self.story = []
        self._n = 0

    # flowable helpers -------------------------------------------------------
    def P(self, txt):
        return self.R["Paragraph"](txt if "<" in txt and "&lt;" not in txt else esc(txt), self.S["body"])

    def spacer(self, h):
        return self.R["Spacer"](1, h)

    def H1(self, txt, number=True):
        if number:
            self._n += 1
            txt = f"{self._n}. {txt}"
        para = self.R["Paragraph"](esc(txt), self.S["h1"])
        para._toc = (0, san(txt))
        return [self.spacer(6), para,
                self.R["HRFlowable"](width="100%", thickness=1.1, color=self.BLUE, spaceAfter=6)]

    def H2(self, txt):
        para = self.R["Paragraph"](esc(txt), self.S["h2"])
        para._toc = (1, san(txt))
        return para

    def bullets(self, items):
        LI = self.R["ListItem"]; PG = self.R["Paragraph"]
        return self.R["ListFlowable"](
            [LI(PG(esc(it), self.S["bullet"]), leftIndent=10, value="•") for it in items],
            bulletType="bullet", start="•", leftIndent=14, spaceAfter=6)

    def code(self, txt):
        return self.R["Preformatted"](san(txt), self.S["code"])

    def note(self, txt):
        return self.R["Paragraph"]("<b>Note.</b> " + esc(txt), self.S["note"])

    def table(self, header, rows, widths):
        colors = self.R["colors"]
        PG = self.R["Paragraph"]
        data = [[PG(esc(h), self.S["cellb"]) for h in header]]
        for r in rows:
            data.append([PG(esc(str(c)), self.S["cell"]) for c in r])
        t = self.R["Table"](data, colWidths=widths, repeatRows=1)
        t.setStyle(self.R["TableStyle"]([
            ("BACKGROUND", (0, 0), (-1, 0), self.NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.LIGHT]),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c5d4e6")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return t

    def pagebreak(self):
        return self.R["PageBreak"]()

    def cover(self, subtitle, edition=None):
        f = self.facts
        inch = self.R["inch"]
        s = [self.spacer(1.6 * inch),
             self.R["Paragraph"](esc(f["product"]), self.S["title"]),
             self.spacer(12),
             self.R["Paragraph"](subtitle, self.S["kicker"])]
        if edition:
            s += [self.spacer(3), self.R["Paragraph"](edition, self.S["subtitle"])]
        s += [self.spacer(22),
              self.R["HRFlowable"](width="55%", thickness=1.2, color=self.BLUE),
              self.spacer(18),
              self.R["Paragraph"](f"Application version {f['app_version']}", self.S["subtitle"]),
              self.R["Paragraph"](f"Decoder backend {f['backend_package']} {f['backend_version']} "
                                  f"(bundled wheel, activated offline on first launch; minimum "
                                  f"supported {f['min_backend_version']})",
                                  self.S["subtitle"]),
              self.R["Paragraph"](f"{f['mcp_tools_count']}-tool MCP server, {len(f['decoders'])} "
                                  f"decoders, {len(f['families'])} code families", self.S["subtitle"]),
              self.spacer(30),
              self.R["Paragraph"](esc(f"{f['author']}. {f['project_url']}"), self.S["subtitle"]),
              self.R["Paragraph"](datetime.now(timezone.utc).strftime("Generated %Y-%m-%d"),
                                  self.S["subtitle"]),
              self.pagebreak()]
        return s

    def toc(self, title="Contents"):
        TableOfContents = self.R["TableOfContents"]
        toc = TableOfContents()
        toc.levelStyles = [self.S["toc1"], self.S["toc2"]]
        return [self.R["Paragraph"](title, self.S["h1"]),
                self.R["HRFlowable"](width="100%", thickness=1.1, color=self.BLUE, spaceAfter=8),
                toc, self.pagebreak()]

    def build(self, out_path):
        R = self.R; inch = R["inch"]; colors = R["colors"]
        facts = self.facts; footer_text = self.footer_text
        GREY = self.GREY

        def decorate(canvas, doc):
            canvas.saveState()
            canvas.setStrokeColor(colors.HexColor("#c5d4e6"))
            canvas.setLineWidth(0.5)
            canvas.line(0.75 * inch, 0.7 * inch, 7.75 * inch, 0.7 * inch)
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(GREY)
            canvas.drawString(0.75 * inch, 0.55 * inch, san(footer_text))
            canvas.drawRightString(7.75 * inch, 0.55 * inch, f"Page {doc.page}")
            canvas.restoreState()

        frame = R["Frame"](0.75 * inch, 0.85 * inch, 7.0 * inch, 9.3 * inch, id="main")

        class Doc(R["BaseDocTemplate"]):
            def afterFlowable(self, flowable):
                toc = getattr(flowable, "_toc", None)
                if toc is not None:
                    level, text = toc
                    key = f"h{level}-{id(flowable)}"
                    self.canv.bookmarkPage(key)
                    self.notify("TOCEntry", (level, text, self.page, key))

        doc = Doc(str(out_path), pagesize=R["letter"],
                  topMargin=0.8 * inch, bottomMargin=0.9 * inch,
                  leftMargin=0.75 * inch, rightMargin=0.75 * inch,
                  title=san(footer_text), author=san(facts["author"]))
        doc.addPageTemplates([R["PageTemplate"](id="body", frames=[frame], onPage=decorate)])
        doc.multiBuild(self.story)


# ===============================================================================
# User manual (per platform)
# ===============================================================================
def build_user_manual(platform: str, facts: dict, out_path: Path) -> None:
    p = PLATFORM[platform]
    b = _Book(facts, f"{facts['product']} User Manual, {platform} Edition")
    inch = b.R["inch"]
    st = b.story

    st += b.cover("USER MANUAL", f"{platform} Edition")
    st += b.toc()

    # 1 Introduction
    st += b.H1("Introduction")
    st += [b.P(f"{facts['product']} is a professional desktop suite for building quantum "
               "error-correcting codes and analysing their decoders. It combines a graphical "
               "workbench of seven feature tabs and a live console, a resilient decoding backend, "
               "a multi-format documentation generator, and a headless server that exposes "
               f"{facts['mcp_tools_count']} tools over the Model Context Protocol (MCP) for use by "
               "automation and language-model agents.")]
    st += [b.P(f"The suite is powered by {facts['backend_package']}, a compiled Rust decoding "
               f"engine with a Python API. It offers {len(facts['decoders'])} single-shot decoders "
               f"across {len(facts['families'])} code families, batch and streaming decode, hardware "
               "detection, and fully reproducible seeded runs.")]
    st += [b.note("This edition is fully self-contained: the decoder backend wheel is bundled "
                  "and activates offline on first launch. Reported "
                  "logical error rate, throughput and latency are simulation outputs that depend on "
                  "the seed, the hardware and the workload; regenerate them for your own setting "
                  "before quoting.")]

    # 2 Requirements
    st += b.H1("System requirements")
    st += [b.bullets(p["prereq"])]

    # 3 Installation
    st += b.H1("Installation")
    for title, steps in p["install"]:
        st += [b.H2(title), b.bullets(steps)]

    # 4 First launch
    st += b.H1("First launch")
    st += [b.P("The application starts immediately. The bundled decoder backend wheel is extracted "
               "and activated on first launch  -  no internet access is needed at any point. Any "
               "outdated managed decoder left by an older release is purged automatically first. "
               "The window title and the status bar show the workbench and decoder versions."),
           b.P(f"Start the graphical application with {p['run_cmd']}. To run the headless MCP server, "
               "which needs no display, use:"),
           b.code(p["mcp_cmd"]),
           b.note("To move to a newer decoder, install a newer release of the application; each "
                  "release bundles its matching decoder wheel.")]

    # 5 The interface
    st += b.H1("The graphical interface")
    st += [b.P("The workbench presents the following tabs. A typical session builds a code in the "
               "Code Explorer, then decodes, benchmarks or streams it in the other tabs.")]
    st += [b.table(["Tab", "What it does"], [(n, d) for n, d in TABS],
                   [1.5 * inch, 5.5 * inch])]

    # 6 Working with codes
    st += [b.pagebreak()]
    st += b.H1("Working with codes")
    st += [b.P("Open the Code Explorer, choose a family, set the distance (minimum 3), and build. "
               f"The {len(facts['families'])} families are:")]
    st += [b.table(["Family key", "Label", "Class", "Notes"],
                   [(f["key"], f["label"], f["class"], FAMILY_GUIDE.get(f["key"], ""))
                    for f in facts["families"]],
                   [1.35 * inch, 1.35 * inch, 1.25 * inch, 3.05 * inch])]
    st += [b.note("The two qLDPC families (bicycle, bivariate_bicycle) are not graphlike. Decode "
                  "them with bp_osd, sparse_blossom, blossom, hybrid, predecoded, auto or auto_router. "
                  "The resilient decode path selects a working decoder automatically if you pick one "
                  "that does not apply.")]

    # 7 Decoders
    st += b.H1("Decoders")
    st += [b.P("These single-shot decoders are available by exact name in the Decoder Lab, "
               "Benchmark and Diagnostics tabs and in the MCP server.")]
    st += [b.table(["Decoder", "When to use it"],
                   [(d["kind"], DECODER_GUIDE.get(d["kind"], d["description"])) for d in facts["decoders"]],
                   [1.5 * inch, 5.5 * inch])]

    # 8 Running decodes, benchmarking, streaming
    st += b.H1("Decoding, benchmarking and streaming")
    st += [b.H2("Decoder Lab"),
           b.P("Select a decoder, set a physical error rate and a seed, and run a single decode. "
               "The result reports the correction weight, syndrome_valid (whether the correction "
               "reproduces the syndrome), and logical_failure (whether a logical operator flipped). "
               "A seed makes the run exactly reproducible."),
           b.H2("Benchmark"),
           b.P("Sweep the chosen decoder over many seeded shots to read throughput (decodes per "
               "second), latency percentiles, and the logical error rate for that workload."),
           b.H2("Batch and Streaming"),
           b.P("Decode a batch of syndromes on the CPU, CUDA or OpenCL backend, or run a "
               "sliding-window streaming session with per-round commit telemetry. The CPU backend "
               "is the default and works everywhere; GPU backends are used only when present and "
               "healthy.")]

    # 9 Hardware and diagnostics
    st += b.H1("Hardware, diagnostics and export")
    st += [b.H2("Hardware"),
           b.P("Detects CUDA and OpenCL availability and system information, and gives a decoder "
               "recommendation that never proposes a decoder the selected code cannot use."),
           b.H2("Diagnostics"),
           b.P("Runs a full environment self-test, probes which decoders work for a given code, and "
               "exercises the resilient decode path with a complete attempt trace. Use it first when "
               "anything looks wrong."),
           b.H2("Documentation export"),
           b.P("The Documentation tab writes a provenance-stamped record of the current code in six "
               "formats (Markdown, JSON, HTML, LaTeX, PDF, SVG) into your data directory. For a very "
               "large code the SVG export falls back to a minimal valid file rather than failing.")]

    # 10 MCP
    st += [b.pagebreak()]
    st += b.H1("The MCP server")
    st += [b.P(f"The application ships a server that speaks newline-delimited JSON-RPC 2.0 over "
               f"standard input and output, protocol version {facts['protocol_version']}. Start it "
               f"with the --mcp flag. A client sends an initialize request, then an initialized "
               f"notification, then calls tools/list to enumerate the {facts['mcp_tools_count']} tools "
               "or tools/call to invoke one."),
           b.code(p["mcp_cmd"]),
           b.P("A complete developer walkthrough is in the QECTOR MCP Integration Guide, and a "
               "machine-readable description of every tool is in QECTOR_LLM_Manual.json.")]

    # 11 Environment variables
    st += b.H1("Environment variables")
    st += [b.table(["Variable", "Effect"],
                   [("QECTOR_DATA_DIR", "Relocate all per-user data (logs, exports, settings) to a "
                                        "chosen directory."),
                    ("QECTOR_PYTHON", "Legacy override: path to a compatible CPython used only for "
                                      "source-run installs. The shipped app never needs it  -  the "
                                      "decoder comes from the bundled wheel, offline."),
                    ("QECTOR_DISABLE_OPENCL", "Set to 1 to skip OpenCL probing on systems with "
                                              "unstable GPU drivers. It cannot enable OpenCL; the "
                                              "published wheel is built without that backend."),
                    ("QECTOR_SILENT", "Set to 1 to suppress the backend startup notice.")],
                   [1.9 * inch, 5.1 * inch])]
    st += [b.P("The per-user data directory for this edition is " + p["data_dir"] + ".")]

    # 12 Troubleshooting
    st += b.H1("Troubleshooting")
    st += [b.table(["Symptom", "Resolution"],
                   [("The app does not appear after launch.",
                     "A splash screen appears within about a second and stays up until the main "
                     "window is ready. The first launch is the slow one: it extracts the bundled "
                     "decoder wheel, then loads a compiled extension. If nothing appears at all, read "
                     "logs/boot.log in the per-user data directory; it records every provisioning "
                     "step and the exact import error."),
                    ("A decoder is refused for a qLDPC code.",
                     "Choose bp_osd, blossom, sparse_blossom, hybrid, predecoded, auto or auto_router, "
                     "or let the resilient path pick a working decoder."),
                    ("CUDA reports unavailable.",
                     "Expected without a supported NVIDIA GPU and a healthy driver. The CPU backend "
                     "decodes fully."),
                    ("OpenCL always reports unavailable, even with a working OpenCL GPU.",
                     "Expected. The published qector-decoder-v3 wheel is built without its OpenCL "
                     "feature, so the backend does not exist in that build; this is not a driver or "
                     "configuration fault and no environment variable enables it. The Hardware tab "
                     "and 'qector hardware' show how many OpenCL devices the host itself exposes, so "
                     "you can tell the two situations apart. Use CUDA or CPU."),
                    ("Benchmarks differ between machines.",
                     "Throughput, latency and logical error rate are simulation outputs; they depend "
                     "on hardware, seed and workload. Regenerate them for your setting.")],
                   [2.5 * inch, 4.5 * inch])]

    # 13 Licensing and support
    st += b.H1("Licensing and support")
    st += [b.P("The software is source-available. Academic, personal and non-commercial research "
               "use is free. Commercial use requires a paid licence, and a 60-day commercial "
               "evaluation is available and creditable against a licence. The qector-decoder-v3 "
               "backend is licensed separately. Consult the EULA shipped with the application for "
               "full terms."),
            b.P(f"Licensing reference: {facts['pricing_url']}. The application does not open "
               "websites, email clients, or other external links; use the shipped EULA and an "
               "approved offline transfer process for licensing administration."),
           b.P(f"Sales and licensing enquiries: {facts['contact_email']}."),
           b.P(f"Project home and support: {facts['project_url']}. Attribution: {facts['author']} "
               f"(ORCID {facts['author_orcid']}).")]

    # 14 Glossary
    st += b.H1("Glossary")
    st += [b.table(["Term", "Meaning"], [(t, d) for t, d in GLOSSARY], [1.6 * inch, 5.4 * inch])]

    # Appendix A: full tool list
    st += [b.pagebreak()]
    st += b.H1("Appendix A. Full MCP tool list", number=False)
    st += [b.P(f"All {facts['mcp_tools_count']} tools registered by the server, alphabetical.")]
    st += [b.table(["Tool", "Description"],
                   [(t["name"], t["description"]) for t in facts["tools"]],
                   [1.85 * inch, 5.15 * inch])]

    b.build(out_path)


# ===============================================================================
# Quick Start (cross platform)
# ===============================================================================
def build_quick_start(facts: dict, out_path: Path) -> None:
    b = _Book(facts, f"{facts['product']} Quick Start Guide")
    inch = b.R["inch"]
    st = b.story
    st += b.cover("QUICK START GUIDE")

    st += b.H1("Install and run in one minute")
    st += [b.P("Every edition ships with the decoder wheel bundled inside. On first launch the "
               "application activates qector-decoder-v3 from the bundled wheel automatically  -  "
               "no internet connection, Python, or pip needed at any point.")]

    st += [b.H2("Windows"),
           b.bullets(["Double-click QectorWorkbench-Portable.exe. That is all; it is a single file. "
                      "First launch activates the bundled decoder backend offline.",
                      "Or run the installer QectorWorkbenchSetup.exe for a Start-menu and desktop icon."]),
           b.H2("Linux"),
           b.bullets([f"sudo dpkg -i ./qector-workbench_{version.WORKBENCH_VERSION}_amd64.deb",
                      "sudo apt-get -f install    (only if dpkg reports missing dependencies)",
                      "Launch from the Science menu or run: qector-workbench"]),
           b.H2("macOS"),
           b.bullets(["Open QectorWorkbench.dmg and drag the app to Applications.",
                      "Right-click the app the first time and choose Open. "
                      "First launch activates the bundled decoder backend offline."])]

    st += b.H1("Your first decode")
    st += [b.bullets([
        "Open the Code Explorer tab. Choose rotated_surface, set distance 3, and click Build.",
        "Open the Decoder Lab tab. Choose the decoder auto_router, set a physical error rate "
        "(for example 0.05) and a seed, and run.",
        "Read the result: syndrome_valid should be true, with a correction weight and the "
        "logical outcome. Change the seed to see a reproducible new shot.",
        "Try the Benchmark tab to sweep many shots, or Batch and Streaming for bulk decode.",
    ])]

    st += b.H1("Use it from an AI agent (MCP)")
    st += [b.P("The same application is a headless MCP server. Start it with the --mcp flag and "
               "connect any MCP client:"),
           b.code("Windows :  QectorWorkbench-Portable.exe --mcp\n"
                  "Linux   :  qector-workbench --mcp\n"
                  "macOS   :  /Applications/QectorWorkbench.app/Contents/MacOS/QectorWorkbench --mcp"),
           b.P("See the QECTOR MCP Integration Guide for client configuration and tool examples.")]

    st += b.H1("Good to know")
    st += [b.bullets([
        f"{len(facts['decoders'])} decoders and {len(facts['families'])} code families are built in.",
        "auto_router is a safe default: it routes each code to a suitable decoder automatically.",
        "The qLDPC families (bicycle, bivariate_bicycle) need bp_osd or a routed decoder, not "
        "union-find.",
        "All benchmark numbers are simulation outputs; regenerate them on your hardware.",
    ])]
    b.build(out_path)


# ===============================================================================
# MCP Integration Guide (developer / agent)
# ===============================================================================
def build_mcp_guide(facts: dict, out_path: Path) -> None:
    b = _Book(facts, f"{facts['product']} MCP Integration Guide")
    inch = b.R["inch"]
    st = b.story
    st += b.cover("MCP INTEGRATION GUIDE")
    st += b.toc()

    st += b.H1("Overview")
    st += [b.P(f"{facts['product']} embeds an MCP (Model Context Protocol) server that exposes "
               f"{facts['mcp_tools_count']} tools for building codes, decoding, benchmarking, "
               "streaming, documentation export and system introspection. Any MCP-capable client, "
               "including AI assistants and custom agents, can drive the workbench headlessly.")]
    st += [b.table(["Property", "Value"],
                   [("Server name", facts["server_name"]),
                    ("Transport", "newline-delimited JSON-RPC 2.0 over stdin/stdout (stdio)"),
                    ("Protocol version", facts["protocol_version"]),
                    ("Tool count", str(facts["mcp_tools_count"])),
                    ("Launch flag", "--mcp")],
                   [1.7 * inch, 5.3 * inch])]

    st += b.H1("Launching the server")
    st += [b.code("Windows portable :  QectorWorkbench-Portable.exe --mcp\n"
                  "Linux (.deb)     :  qector-workbench --mcp\n"
                  "macOS app        :  /Applications/QectorWorkbench.app/Contents/MacOS/QectorWorkbench --mcp\n"
                  "From source      :  python mcp_server.py")]

    st += b.H1("Client configuration")
    st += [b.P("Most MCP clients accept a small JSON entry describing the command and arguments. "
               "The examples below register QECTOR as a stdio server. Use the absolute path to the "
               "executable on the target machine.")]
    st += [b.H2("Windows client configuration"),
           b.code('{\n'
                  '  "mcpServers": {\n'
                  '    "qector": {\n'
                  '      "command": "C:\\\\Apps\\\\QectorWorkbench-Portable.exe",\n'
                  '      "args": ["--mcp"]\n'
                  '    }\n'
                  '  }\n'
                  '}'),
           b.H2("Linux or macOS client configuration"),
           b.code('{\n'
                  '  "mcpServers": {\n'
                  '    "qector": {\n'
                  '      "command": "qector-workbench",\n'
                  '      "args": ["--mcp"]\n'
                  '    }\n'
                  '  }\n'
                  '}')]

    st += [b.pagebreak()]
    st += b.H1("The handshake")
    st += [b.P("A session opens with an initialize request, an initialized notification, and then "
               "tool discovery. One JSON object per line."),
           b.code('{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
                  '{"protocolVersion":"' + facts["protocol_version"] + '","capabilities":{},'
                  '"clientInfo":{"name":"client","version":"1.0"}}}\n'
                  '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
                  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')]

    st += b.H1("Calling a tool")
    st += [b.P("Invoke a tool with tools/call, passing its exact name and an arguments object. "
               "A minimal build-and-decode flow:"),
           b.code('{"jsonrpc":"2.0","id":3,"method":"tools/call","params":'
                  '{"name":"build_code","arguments":{"family":"rotated_surface","distance":3}}}\n'
                  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":'
                  '{"name":"run_decode","arguments":{"decoder":"auto_router","error_rate":0.05,"seed":1}}}'),
           b.note("Tool and argument names must match exactly. Use only the decoder kinds and code "
                  "families listed in this documentation set; any other value returns a handled error.")]

    st += b.H1("Interpreting results and errors")
    st += [b.table(["Field or case", "Meaning"],
                   [("syndrome_valid", "True only when the correction reproduces the syndrome "
                                       "(H times c equals s mod 2). Success requires this."),
                    ("logical_failure", "True, false or null. Null when a code exposes no logicals "
                                        "matrix (the qLDPC families)."),
                    ("logical_error_rate", "A simulation estimate for the seed, workload and hardware; "
                                           "not a universal benchmark."),
                    ("isError true", "A tool-level error. Relay the message text; do not treat it as "
                                     "success."),
                    ("array summaries", "Large arrays may be summarised (shape, dtype, preview). Do not "
                                        "infer omitted values.")],
                   [1.85 * inch, 5.15 * inch])]

    st += b.H1("Rules for agents")
    st += [b.P("These rules keep an automated caller correct and prevent hallucination. They are "
               "also encoded, per tool, in QECTOR_LLM_Manual.json.")]
    st += [b.bullets(RULES)]

    st += [b.pagebreak()]
    st += b.H1("Tool reference")
    st += [b.P(f"All {facts['mcp_tools_count']} tools, alphabetical. Each tool's full parameter "
               "schema and an example call are in QECTOR_LLM_Manual.json.")]
    st += [b.table(["Tool", "Description"],
                   [(t["name"], t["description"]) for t in facts["tools"]],
                   [1.85 * inch, 5.15 * inch])]
    b.build(out_path)


# ===============================================================================
# LLM JSON manual
# ===============================================================================
def build_llm_json(facts: dict, out_path: Path) -> None:
    qldpc = facts["qldpc_keys"]
    manual = {
        "manual_type": "operational manual for LLM and agent use",
        "schema_version": "2.0",
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "product": facts["product"],
        "application_version": facts["app_version"],
        "decoder_backend": {
            "package": facts["backend_package"],
            "version": facts["backend_version"],
            "minimum_version": facts["min_backend_version"],
            "bundled": True,
            "provisioning": ("Activated automatically from the wheel bundled inside the "
                             "application on first launch; fully offline on every platform. "
                             "Outdated managed decoders from older releases are purged "
                             "automatically before activation."),
        },
        "purpose": ("Use this file to operate the QECTOR MCP server correctly. It lists every tool, "
                    "its parameters, and an example call, and defines strict rules that prevent "
                    "hallucination. Treat the tools, decoders and code families here as the complete "
                    "and only valid set."),
        "hard_rules_no_hallucination": RULES,
        "mcp_server": {
            "server_name": facts["server_name"],
            "transport": "newline delimited JSON-RPC 2.0 over standard input and output",
            "protocol_version": facts["protocol_version"],
            "launch_commands": {
                "windows_portable": "QectorWorkbench-Portable.exe --mcp",
                "linux_deb": "qector-workbench --mcp",
                "macos_app": "/Applications/QectorWorkbench.app/Contents/MacOS/QectorWorkbench --mcp",
                "from_source": "python mcp_server.py",
            },
            "handshake_sequence": [
                {"send": {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": facts["protocol_version"],
                                     "capabilities": {},
                                     "clientInfo": {"name": "client", "version": "1.0"}}}},
                {"send": {"jsonrpc": "2.0", "method": "notifications/initialized"}},
                {"send": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}},
                {"then": "call tools with tools/call using params.name and params.arguments"},
            ],
            "call_shape": {"jsonrpc": "2.0", "id": "<int>", "method": "tools/call",
                           "params": {"name": "<tool name from tools list>",
                                      "arguments": "<object of parameters>"}},
            "error_semantics": {
                "tool_level_error": ("A successful JSON-RPC response whose result has isError set to "
                                     "true and a text message. Relay the message; do not treat it as a "
                                     "successful result."),
                "protocol_error": ("A JSON-RPC error object with a numeric code, for example method not "
                                   "found or invalid params. Report it verbatim."),
                "array_summaries": ("Large arrays in a result may be summarised as an object with "
                                    "shape, dtype, a short preview, and summary set to true. Do not "
                                    "infer the omitted values."),
            },
            "tool_count": facts["mcp_tools_count"],
            "tools": facts["tools"],
        },
        "decoders": facts["decoders"],
        "decoder_names": [d["kind"] for d in facts["decoders"]],
        "code_families": facts["families"],
        "code_family_keys": [f["key"] for f in facts["families"]],
        "result_interpretation": {
            "syndrome_valid": "Boolean. True only when the correction reproduces the observed syndrome "
                              "under H times c equals s modulo 2. Do not claim success when it is false.",
            "logical_failure": "Boolean or null. Null when the code exposes no usable logicals matrix, "
                               "for example the qLDPC families. Do not invent a value when it is null.",
            "logical_error_rate": "Float or null. A simulation estimate for the specific seed, workload "
                                  "and hardware. Not a universal benchmark.",
            "throughput_and_latency": "Simulation timings for the run. Regenerate before quoting.",
        },
        "valid_parameter_notes": {
            "distance_minimum": 3,
            "qldpc_families": qldpc,
            "qldpc_incompatible_decoders": ["union_find", "fast_union_find", "lookup_table"],
            "qldpc_recommended_decoders": ["bp_osd", "sparse_blossom", "blossom", "hybrid",
                                           "predecoded", "auto", "auto_router"],
            "lookup_table_limit": "Refused above 20 checks because the table size grows as 2 to the "
                                  "power of the number of checks.",
        },
        "environment_variables": {
            "QECTOR_DATA_DIR": "Relocate all per user data.",
            "QECTOR_PYTHON": "Legacy source-run override: path to a compatible CPython. The shipped "
                             "app never uses it (decoder comes from the bundled wheel, offline).",
            "QECTOR_DISABLE_OPENCL": "Set to 1 to skip OpenCL probing on unstable GPU drivers. It "
                                     "cannot enable OpenCL.",
            "QECTOR_SILENT": "Set to 1 to suppress the backend startup notice.",
        },
        "hardware_backends": {
            "cpu": "Always available.",
            "cuda": "Available when an NVIDIA GPU and a healthy driver are present.",
            "opencl": ("Reported unavailable by the published wheel: it is built without the OpenCL "
                       "feature, so the backend does not exist in that build. This is independent of "
                       "the host, which may well expose OpenCL devices, and no environment variable "
                       "enables it. Do not attribute it to drivers or configuration."),
        },
        "licensing": {
            "model": facts["licence_summary"],
            "evaluation": facts["licence_evaluation"],
            "pricing_url": facts["pricing_url"],
            "contact_email": facts["contact_email"],
            "company": facts["company"],
            "maintainer": facts["maintainer"],
            "in_app": ("Documentation tab, Developer and Licensing section, with local licensing "
                       "information and no external-link buttons."),
        },
        "boot_behaviour": {
            "splash": "A splash screen appears within about a second and closes when the main window "
                      "is mapped.",
            "first_launch": "Extracts and activates the bundled decoder wheel, then loads a "
                            "compiled extension; this is the slow launch.",
            "later_launches": "Activates the already-installed managed site and works offline.",
            "diagnostics": "logs/boot.log in the per-user data directory records every provisioning "
                           "step and the exact import error; logs/boot_stdio.log captures output that "
                           "a windowed build has nowhere else to send.",
        },
    }
    out_path.write_text(json.dumps(manual, indent=2, ensure_ascii=True), encoding="utf-8")


# ===============================================================================
def build_readme(facts: dict, out_path: Path) -> None:
    lines = [
        f"{facts['product']} - Public Documentation Set",
        "=" * 52,
        "",
        f"Application version : {facts['app_version']}",
        f"Decoder backend     : {facts['backend_package']} {facts['backend_version']} (bundled wheel, offline activation on first launch)",
        f"MCP tools           : {facts['mcp_tools_count']}",
        f"Decoders            : {len(facts['decoders'])}",
        f"Code families       : {len(facts['families'])}",
        datetime.now(timezone.utc).strftime("Generated           : %Y-%m-%d %H:%M UTC"),
        "",
        "Archival Zenodo DOIs:",
    ]
    lines += [f"  {label:24}: {doi}" for label, doi in ZENODO_DOIS]
    lines += [
        "",
        "Contents of this documentation set:",
        "",
        "  QECTOR_User_Manual_Windows.pdf   Full user manual, Windows edition",
        "  QECTOR_User_Manual_Linux.pdf     Full user manual, Linux edition",
        "  QECTOR_User_Manual_macOS.pdf     Full user manual, macOS edition",
        "  QECTOR_Quick_Start_Guide.pdf     One-minute install and first decode (all platforms)",
        "  QECTOR_MCP_Integration_Guide.pdf Connect AI agents and clients to the MCP server",
        "  QECTOR_API_Reference.md          Complete API reference (backend, MCP tools, schemas)",
        "  QECTOR_API_Reference.pdf         Printable API reference with figures",
        "  QECTOR_LLM_Manual.json           Machine-readable tool manual for LLM agents",
        "  README.txt                       This index file",
        "",
        "The application bundles the decoder wheel inside. On first launch it activates",
        "qector-decoder-v3 from the bundled wheel automatically  -  no internet connection,",
        "Python, or pip is needed on any platform, and any outdated managed decoder left by",
        "an older release is purged automatically before activation.",
        "",
        "A splash screen appears within about a second of launch and closes when the main window",
        "is ready. The first launch is the slow one. If the app ever fails to start, logs/boot.log",
        "in the per-user data directory records every provisioning step and the exact import error.",
        "",
        "Note on OpenCL: the published qector-decoder-v3 wheel is built without its OpenCL feature,",
        "so the OpenCL backend reports unavailable even on machines that expose OpenCL devices.",
        "That is a property of the build, not a driver fault, and no environment variable enables",
        "it. CUDA and CPU are unaffected.",
        "",
        "Licensing:",
        f"  {facts['licence_summary']}",
        f"  {facts['licence_evaluation']}",
        f"  Buy a licence : {facts['pricing_url']}",
        f"  Sales contact : {facts['contact_email']}",
        "  In the app    : Documentation tab > Developer and Licensing > offline local licensing",
        "",
        f"Project: {facts['project_url']}",
        f"Attribution: {facts['author']}",
        f"ORCID: {facts['author_orcid']}",
        "",
    ]
    out_path.write_text("\n".join(san(l) for l in lines), encoding="utf-8")


def build_zip(outdir: Path, zip_name: str = "manuals.zip") -> Path:
    """Bundle the whole documentation set, so the zip can never lag the files.

    The distributed manuals.zip previously had no generator at all and silently
    kept shipping an older README and API reference than the loose files beside it.
    """
    import zipfile

    target = outdir / zip_name
    files = sorted(
        p for p in outdir.rglob("*")
        if p.is_file() and p.name != zip_name and p.suffix.lower() != ".pyc"
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, path.relative_to(outdir).as_posix())
    return target


def build_public_docs(outdir: Path) -> dict[str, Path]:
    """Generate the complete public documentation set into ``outdir``.

    Returns ``{artifact_name: path}`` for every file produced.  Used by the CLI
    ``main()`` and by the in-app "Export Official Docs" button so both always
    ship byte-identical documents.  Individual build failures raise (the caller
    decides how to surface them); every artifact is written by its own builder
    so a failure in one never corrupts the others.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    facts = gather_facts()
    produced: dict[str, Path] = {}

    for platform in ("Windows", "Linux", "macOS"):
        path = outdir / f"QECTOR_User_Manual_{platform}.pdf"
        build_user_manual(platform, facts, path)
        produced[f"QECTOR_User_Manual_{platform}.pdf"] = path

    qs = outdir / "QECTOR_Quick_Start_Guide.pdf"
    build_quick_start(facts, qs)
    produced["QECTOR_Quick_Start_Guide.pdf"] = qs

    mcp = outdir / "QECTOR_MCP_Integration_Guide.pdf"
    build_mcp_guide(facts, mcp)
    produced["QECTOR_MCP_Integration_Guide.pdf"] = mcp

    js = outdir / "QECTOR_LLM_Manual.json"
    build_llm_json(facts, js)
    produced["QECTOR_LLM_Manual.json"] = js

    rd = outdir / "README.txt"
    build_readme(facts, rd)
    produced["README.txt"] = rd

    # Sync package-only reference files if available
    pkg_ref_src = Path(_HERE) / "docs" / "QECTOR_Decoder_v3_Extended_Reference_package_only.md"
    if pkg_ref_src.exists():
        for name in ("QECTOR Decoder v3 - Reference (package only).md",
                     "QECTOR Decoder v3 - Extended Reference (package only).md"):
            target = outdir / name
            target.write_text(pkg_ref_src.read_text(encoding="utf-8"), encoding="utf-8")
            produced[name] = target

    # Retire documents this generator no longer emits. Renaming an artifact used
    # to leave the old file behind, and the release bundler mirrors the manuals
    # directory wholesale, so two superseded copies of the package reference
    # (one still carrying an em dash) shipped inside a public zip alongside the
    # current one.
    #
    # pkg_ref_src is protected: it is the *input* for the two reference copies
    # and normalises to the same key as its own output, so an unprotected prune
    # deletes the source and the documents can never be regenerated.
    _prune_superseded(outdir, set(produced), protected={pkg_ref_src.name})

    # Bundle last, so the zip always matches the files just written.
    z = build_zip(outdir)
    produced[z.name] = z
    return produced


def _name_key(name: str) -> str:
    """Normalise a filename so renamings of the same document collide.

    Em dash, en dash, underscore and space all fold to a single separator, so
    ``QECTOR Decoder v3  -  Extended Reference (package only).md`` and
    ``QECTOR_Decoder_v3_Extended_Reference_package_only.md`` both reduce to the
    same key as the current ``QECTOR Decoder v3 - Extended Reference
    (package only).md``.
    """
    stem = Path(name).stem.lower()
    for ch in (" - ", " - ", "_", "-", "(", ")", "."):
        stem = stem.replace(ch, " ")
    return " ".join(stem.split()) + Path(name).suffix.lower()


def _prune_superseded(outdir: Path, produced_names: set[str],
                      protected: set[str] | None = None) -> list[str]:
    """Delete older *renamings* of documents this run just produced.

    Deliberately narrow. An earlier version matched whole filename categories
    and deleted `QECTOR_API_Reference.*`, which belongs to `api_reference.py`,
    plus any document whose optional source happened to be missing on that run.
    A file is removed only when its normalised name matches a file this run
    actually wrote and the exact name differs: that is precisely the
    "same document, superseded spelling" case and nothing else.

    ``protected`` names are never removed. Source files that live beside their
    own outputs belong here, or pruning eats the input.
    """
    removed: list[str] = []
    protected = protected or set()
    produced_keys = {_name_key(n): n for n in produced_names}
    try:
        entries = list(outdir.iterdir())
    except Exception:
        return removed
    for path in entries:
        if not path.is_file() or path.name in produced_names or path.name in protected:
            continue
        current = produced_keys.get(_name_key(path.name))
        if current is None:
            continue
        try:
            path.unlink()
            removed.append(f"{path.name} (superseded by {current})")
        except Exception:
            pass
    for entry in removed:
        print(f"  [pruned superseded] {entry}")
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the QECTOR public documentation set.")
    ap.add_argument("--outdir", default="manuals", help="Output directory (default: manuals)")
    ap.add_argument("--also", action="append", default=[], metavar="DIR",
                    help="Additional directory to mirror the set into (repeatable). "
                         "Set QECTOR_MANUALS_MIRROR for a persistent default.")
    args = ap.parse_args(argv)

    mirrors = list(args.also)
    env_mirror = os.environ.get("QECTOR_MANUALS_MIRROR", "").strip()
    if env_mirror and env_mirror not in mirrors:
        mirrors.append(env_mirror)

    outdirs = [Path(args.outdir)] + [Path(m) for m in mirrors]
    results = []
    for outdir in outdirs:
        produced = build_public_docs(outdir)
        if outdir == outdirs[0]:
            results.extend(produced.values())

    where = ", ".join(str(d) for d in outdirs)
    print(f"Generated (synced to {where}):")
    for r in results:
        print(f"  {r.name:34s} {r.stat().st_size:>8} bytes")
    facts = gather_facts()
    print(f"Facts: {facts['mcp_tools_count']} MCP tools, {len(facts['decoders'])} decoders, "
          f"{len(facts['families'])} code families, backend {facts['backend_version']}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
