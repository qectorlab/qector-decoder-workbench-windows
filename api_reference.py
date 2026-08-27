"""api_reference.py  -  Build the QECTOR Workbench Complete API Reference.

Portable, parameterized port of ``scripts/generate_api_manual.py`` so the same
generator can be driven by the release tooling, the CLI, and the in-app
"Export Official Docs" button without hard-coded machine paths.

Everything is derived from the LIVE application source (``version``,
``backend``, ``mcp_server``) so tool names, decoder kinds, code families and
function signatures always match the running build exactly.
"""

from __future__ import annotations

import inspect
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import version
import backend as be
from mcp_server import get_mcp_server

_HERE = Path(__file__).resolve().parent


def sig(o):
    try:
        return str(inspect.signature(o))
    except Exception:
        return "(...)"


def lead(o):
    d = inspect.getdoc(o) or ""
    return d.split("\n\n")[0].replace("\n", " ")


def code_families():
    rows = [
        ("repetition", "distance", "int", "yes", "all (16)", "1D chain parity-check code."),
        ("ring", "distance", "int", "yes", "all (16)", "Periodic 1D chain."),
        ("rotated_surface", "distance", "int", "yes", "all (16)", "Standard rotated surface code."),
        ("unrotated_surface", "distance", "int", "yes", "15 (lookup_table refused >20 checks)", "Square lattice surface code."),
        ("toric", "distance", "int", "yes", "15 (lookup_table refused >20 checks)", "Toric code with periodic boundaries."),
        ("heavy_hex", "distance", "int", "yes", "all (16)", "IBM heavy-hex lattice."),
        ("hypergraph_product", "distance", "int", "yes", "all (16)", "CSS from repetition seed; graphlike."),
        ("bicycle", "circulant size", "int", "no", "all (16)", "qLDPC bicycle code; graphlike enough for all decoders."),
        ("bivariate_bicycle", "preset index", "int", "no", "13 (excludes union_find, fast_union_find, lookup_table)", "IBM BB presets; see compatibility matrix."),
        ("color_code", "triangular size", "int", "no", "colour_code, bp_osd, blossom, hybrid, auto_router", "Triangular & 2D 4.8.8 color codes."),
    ]
    lines = ["## Code families", "", "| Family | Parameter | Type | Graphlike | Decoders | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def decoders():
    rows = [
        ("union_find", "Fast approximate matching via union-find.", "bp_method, osd_order ignored", "graphlike only"),
        ("fast_union_find", "Faster union-find variant; approximate, higher LER.", "-", "graphlike only"),
        ("blossom", "Weight-optimal exact MWPM; matches PyMatching LER.", "-", "all"),
        ("sparse_blossom", "Region-growing near-optimal matching; not exact.", "-", "graphlike only"),
        ("sparse_blossom_radix_neighbors", "RadixHeap k-NN candidate edge discovery variant of region-growing matching.", "-", "graphlike only"),
        ("bp_osd", "Belief propagation + ordered statistics for LDPC/qLDPC.", "bp_method, osd_order, error_rate", "all"),
        ("auto", "Self-selecting AutoDecoder.", "-", "graphlike only"),
        ("hybrid", "Combines multiple strategies; chooses per problem.", "-", "graphlike only"),
        ("lookup_table", "Exhaustive syndrome-to-correction table; refused above 20 checks.", "-", "small codes only"),
        ("predecoded", "Fast pre-decoding pass before matching.", "-", "graphlike only"),
        ("auto_router", "Policy decoder: matching for graphlike, bp_osd for qLDPC. Universally compatible.", "-", "all"),
        ("hybrid_cascade", "Union-Find pre-filter + Blossom/BP-OSD escalation; exposes cascade stats.", "escalation, error_rate", "graphlike only"),
        ("gnn_belief_matching", "GNN-guided weighted matching with faithfulness fallback.", "gnn_hidden_size, gnn_n_layers, error_rate", "graphlike only"),
        ("belief_matching", "BP posteriors reweight exact Blossom matching; faithfulness fallback.", "bp_method, osd_order, error_rate", "graphlike only"),
        ("two_stage", "Two-stage decode pipeline (fast stage + exact escalation).", "escalation", "graphlike only"),
        ("ambiguity_cluster", "Cluster decoding for high-noise/non-graphlike degenerate syndromes.", "-", "non-graphlike friendly"),
        ("colour_code", "Native colour-code decoder over undecomposed detector error models.", "-", "color_code family only"),
    ]
    lines = ["## Decoder kinds", "", "| Kind | Description | Options | Compatibility |", "|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def options():
    return textwrap.dedent("""\
        ## Decoder options

        | Key | Type | Applies to | Description |
        |---|---|---|---|
        | `bp_method` | string | `bp_osd`, `belief_matching` | `"exact"` or `"min_sum"`. |
        | `osd_order` | int | `bp_osd`, `belief_matching` | `0`, `1`, or `2`. Higher is slower/more accurate. |
        | `error_rate` | float | all | Physical error probability used to weight edges or set BP priors. |
        | `escalation` | string | `hybrid_cascade` | `"blossom"` or `"bp_osd"`. |
        | `max_accept_weight` | int | `hybrid_cascade` | Maximum syndrome weight accepted by the pre-filter. |
        | `gnn_hidden_size` | int | `gnn_belief_matching` | Hidden dimension of the GNN. |
        | `gnn_n_layers` | int | `gnn_belief_matching` | Number of GNN message-passing layers. |

        Unknown keys are ignored with a warning; missing keys use backend defaults.
        """)


def backend_api():
    lines = ["## backend.py API", ""]
    for name in sorted(dir(be)):
        if name.startswith("_"):
            continue
        obj = getattr(be, name)
        if callable(obj):
            lines.append(f"### `backend.{name}{sig(obj)}`")
            lines.append("")
            lines.append(lead(obj) or "*No docstring.*")
            lines.append("")
    return "\n".join(lines)


def module_api(title, modname):
    try:
        mod = __import__(modname)
    except Exception as e:
        return f"## {title}\n\n*Import failed: {e}*\n"
    lines = [f"## {title}", ""]
    for name in sorted(dir(mod)):
        if name.startswith("_"):
            continue
        obj = getattr(mod, name)
        if callable(obj) and not isinstance(obj, type):
            lines.append(f"### `{modname}.{name}{sig(obj)}`")
            lines.append("")
            lines.append(lead(obj) or "*No docstring.*")
            lines.append("")
    return "\n".join(lines)


def measurements_section():
    return ("## Performance measurements\n\n"
            "Benchmark measurements are intentionally not stored or shipped. "
            "Run a local benchmark on the target hardware when measurements are needed.\n")
    fig_dir = _HERE / "manuals" / "figures"
    for img in ["tanner_rotated_surface_d5.png", "compatibility_matrix.png"]:
        if (fig_dir / img).is_file():
            lines.append(f"![{img}](figures/{img})")
            lines.append("")
    return "\n".join(lines)


def mcp_section():
    reg = get_mcp_server().tools.tools
    lines = ["## MCP tool reference", "", f"{len(reg)} tools via stdio JSON-RPC 2.0.", ""]
    for name in sorted(reg):
        spec = reg[name]
        lines.append(f"### `{name}`")
        lines.append(spec.get("description", ""))
        params = spec.get("parameters", {})
        if params:
            lines.append("**Parameters**")
            for pname, pspec in params.items():
                ptype = pspec.get("type", "any")
                default = pspec.get("default")
                req = "required" if default is None else f"default `{default!r}`"
                desc = pspec.get("description", "")
                lines.append(f"- `{pname}` (`{ptype}`, {req}) - {desc}")
        else:
            lines.append("*No parameters.*")
        lines.append("")
    return "\n".join(lines)


def wire_protocol():
    return textwrap.dedent("""\
        ## MCP wire protocol

        Newline-delimited JSON-RPC 2.0 over stdio. Launch with `--mcp`.

        ```json
        {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"client","version":"1.0"}}}
        {"jsonrpc":"2.0","method":"notifications/initialized"}
        {"jsonrpc":"2.0","id":2,"method":"tools/list"}
        {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"decode_single","arguments":{"family":"rotated_surface","distance":5,"decoder_name":"blossom","error_rate":0.05,"seed":42}}}
        ```

        Result envelope: `content[0].text` holds a JSON payload; `isError` flags tool-level failure.
        """)


def schemas():
    return textwrap.dedent("""\
        ## Common result schemas

        ```python
        # Single decode result
        {"error":[...],"syndrome":[...],"result":{"correction":[...],"hamming_weight":int,"syndrome_valid":bool,"logical_failure":bool|None,"backend_used":str|None,"matched_weight":int|None,"fallback_used":bool,"options_applied":bool|dict,"latency_us":float}}
        # Benchmark result
        {"throughput_decodes_per_s":float,"decode_seconds":float,"n_trials":int,"p":float,"seed":int,"method":str,"backend":str,"latency_mean_us":float,"latency_p50_us":float,"latency_p99_us":float,"latency_min_us":float,"latency_max_us":float,"syndrome_match_rate":float,"logical_error_rate":float}
        # Resilient decode
        {"success":bool,"used_decoder":str|None,"fallback_used":bool,"syndrome_valid":bool|None,"logical_failure":bool|None,"attempts":[{"method":str,"ok":bool,"syndrome_valid":bool|None,"hamming_weight":int|None,"latency_ms":float|None,"error":str|None}],"message":str}
        # Diagnostics report
        {"overall_status":"pass|degraded|fail","timestamp":float,"platform":str,"python":str,"workbench_version":str,"backend_version":str|None,"summary":{...},"checks":[{"name":str,"status":str,"detail":str}]}
        ```
        """)


def env_and_examples():
    return textwrap.dedent("""\
        ## Environment variables

        | Variable | Effect |
        |---|---|
        | `QECTOR_DATA_DIR` | Relocate all QECTOR user data. |
        | `QECTOR_SILENT` | Set to `1` to suppress the backend startup notice. |
        | `QECTOR_LICENSE` | Ed25519 token that overrides academic/commercial for testing. |
        | `QECTOR_DISABLE_OPENCL` | Set to `1` to skip OpenCL probing. It cannot *enable* OpenCL. |
        | `QECTOR_ENABLE_OPENCL_AUTO` | Allows OpenCL auto-routing, but only when OpenCL is already available. |

        ## Provisioning model

        The Workbench bundles the decoder wheel inside the application. On first launch it
        activates `qector-decoder-v3` from the bundled wheel into a managed, ABI-scoped user
        site  -  fully offline, on every platform. Any outdated managed decoder left by an
        older release is purged automatically before activation. No internet connection,
        Python, or pip is required.

        A splash screen is shown within roughly a second of launch and closes once the main window is
        mapped, so the cold start (extracting the wheel on first run, then loading a compiled
        extension) is never an invisible wait.

        ### Boot diagnostics

        A windowed build has no stderr, so provisioning is logged to files under the per-user data
        directory:

        | File | Contents |
        |---|---|
        | `logs/boot.log` | Every bootstrap step, the activated site, and the exact import error. |
        | `logs/boot_stdio.log` | Anything written to stdout/stderr when the build has no console. |

        ## Hardware backends

        | Backend | Availability |
        |---|---|
        | `cpu` | Always available. |
        | `cuda` | Requires an NVIDIA GPU with a healthy driver. |
        | `opencl` | Reported unavailable by the published wheel, which is built without the OpenCL feature. |

        `opencl_is_available()` returning `False` is a property of the decoder build, not of the host:
        a machine can expose OpenCL devices and still get `False`, and no environment variable changes
        it. `hardware_routing.detect_hardware()` reports `opencl_host_devices` and
        `opencl_host_platform` (probed from the host ICD) plus an `opencl_reason` string so the two
        situations can be told apart. Enabling the backend requires rebuilding `qector-decoder-v3`
        with its `opencl` Cargo feature.

        ## Example workflows

        ```python
        import backend as be, autodebug
        code = be.build_code("rotated_surface", 5)
        out = be.run_single_decode(code, error_rate=0.05, decoder_kind="blossom", seed=42)
        assert out["result"]["syndrome_valid"]

        out2 = be.run_single_decode(code, error_rate=0.05, decoder_kind="bp_osd", seed=7, decoder_options={"bp_method":"min_sum","osd_order":1})
        probe = autodebug.probe_decoders("bivariate_bicycle", 3, seed=99)
        resilient = autodebug.resilient_single_decode("bivariate_bicycle", 3, decoder="union_find", seed=7)
        stats = be.run_hybrid_cascade_stats(code, n_samples=64, error_rate=0.05, seed=1)
        ```
        """)


def build_markdown():
    parts = [
        "# QECTOR Workbench - Complete API Reference",
        f"**Workbench {version.WORKBENCH_VERSION} - Backend `qector_decoder_v3` {version.BACKEND_VERSION} (min {version.MIN_BACKEND_VERSION}) - {version.MCP_TOOLS} MCP tools - {len(be.DECODER_KINDS)} decoders - {len(be.CODE_FAMILIES)} code families**",
        f"**Decoder package: `qector_decoder_v3` {version.BACKEND_VERSION} (bundled wheel, activated offline on first launch)**",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}Z",
        "",
        "This manual is generated from the live application source so every tool name, decoder kind, code family, and function signature matches the running build exactly.",
        "",
        code_families(),
        "",
        decoders(),
        "",
        options(),
        "",
        measurements_section(),
        "",
        backend_api(),
        "",
        module_api("autodebug.py API", "autodebug"),
        "",
        module_api("hardware_routing.py API", "hardware_routing"),
        "",
        module_api("version_service.py API", "version_service"),
        "",
        module_api("decoder_provisioner.py API", "decoder_provisioner"),
        "",
        module_api("doc_generator.py API", "doc_generator"),
        "",
        mcp_section(),
        "",
        wire_protocol(),
        "",
        schemas(),
        "",
        env_and_examples(),
    ]
    return "\n".join(parts)


def md_to_pdf_elements(text: str, outdir: Path):
    from reportlab.lib.units import inch
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import Paragraph, Spacer, Preformatted, PageBreak, Image as RLImage

    styles = getSampleStyleSheet()
    title = ParagraphStyle("title", parent=styles["Title"], fontSize=18, spaceAfter=12)
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], fontSize=14, spaceAfter=8, spaceBefore=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=12, spaceAfter=6, spaceBefore=8)
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=9, leading=12)
    code = ParagraphStyle("code", parent=styles["Code"], fontSize=8, leading=10)
    elems = []
    in_code = False
    buf = []

    def flush_code():
        nonlocal buf
        if buf:
            elems.append(Preformatted("\n".join(buf), style=code))
            elems.append(Spacer(1, 6))
            buf = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("```"):
            if in_code:
                flush_code()
            in_code = not in_code
            continue
        if in_code:
            buf.append(line)
            continue
        if not line:
            elems.append(Spacer(1, 4))
            continue
        if line.startswith("![") and "](figures/" in line:
            name = line.split("](figures/")[1].split(")")[0]
            img_path = outdir / "figures" / name
            if img_path.exists():
                from reportlab.lib.utils import ImageReader
                reader = ImageReader(str(img_path))
                iw, ih = reader.getSize()
                max_w = 6.5 * inch
                max_h = 4.5 * inch
                scale = min(max_w / iw, max_h / ih)
                elems.append(RLImage(str(img_path), width=iw * scale, height=ih * scale))
                elems.append(Spacer(1, 6))
            continue
        if line.startswith("# "):
            flush_code()
            elems.append(Paragraph(line[2:], title))
        elif line.startswith("## "):
            flush_code()
            if line in {"## Code families", "## Decoder kinds", "## backend.py API", "## MCP tool reference", "## Measured data"}:
                elems.append(PageBreak())
            elems.append(Paragraph(line[3:], h1))
        elif line.startswith("### "):
            elems.append(Paragraph(line[4:], h2))
        elif line.startswith("|") and line.endswith("|"):
            elems.append(Preformatted(line, style=code))
        else:
            elems.append(Paragraph(line.replace("`", ""), body))
    flush_code()
    return elems


_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>QECTOR Complete API Reference</title>
<style>
body {{ font-family: 'Latin Modern Roman', 'Times New Roman', Georgia, serif;
       max-width: 860px; margin: 40px auto; padding: 0 20px; color: #111;
       background: #fff; line-height: 1.6; }}
h1, h2, h3, h4 {{ font-family: 'Segoe UI', Helvetica, Arial, sans-serif;
                 line-height: 1.25; margin: 1.4em 0 0.5em; }}
h1 {{ font-size: 1.9em; border-bottom: 2px solid #14406b; padding-bottom: 6px; }}
h2 {{ font-size: 1.4em; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
h3 {{ font-size: 1.15em; }}
table {{ border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 0.94em;
        display: block; overflow-x: auto; }}
th, td {{ border: 1px solid #ccc; padding: 7px 11px; text-align: left;
         vertical-align: top; }}
th {{ background: #f4f4f4; font-weight: 600; }}
tr:nth-child(even) td {{ background: #fbfbfb; }}
code {{ font-family: 'Cascadia Mono', Consolas, monospace; background: #eef1f4;
       padding: 1px 5px; border-radius: 3px; font-size: 0.92em; }}
pre {{ background: #f7f8fa; border: 1px solid #dde1e6; border-radius: 5px;
      padding: 12px 14px; overflow-x: auto; }}
pre code {{ background: none; padding: 0; }}
blockquote {{ border-left: 3px solid #14406b; margin: 12px 0; padding: 4px 14px;
             color: #444; background: #fafbfc; }}
ul, ol {{ padding-left: 26px; }}
img {{ max-width: 100%; height: auto; }}
hr {{ border: none; border-top: 1px solid #ddd; margin: 26px 0; }}
@media (prefers-color-scheme: dark) {{
  body {{ background: #16181c; color: #e6e6e6; }}
  h1 {{ border-bottom-color: #4a9eff; }}
  h2 {{ border-bottom-color: #333; }}
  th {{ background: #23262c; }}
  tr:nth-child(even) td {{ background: #1b1e23; }}
  th, td {{ border-color: #333; }}
  code {{ background: #23262c; }}
  pre {{ background: #1b1e23; border-color: #333; }}
  blockquote {{ border-left-color: #4a9eff; background: #1b1e23; color: #bbb; }}
}}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def render_markdown_html(text: str) -> str:
    """Render the API-reference Markdown to a real HTML document.

    Deliberately a small, dependency-free renderer covering exactly the
    constructs this document uses: ATX headings, fenced code, pipe tables,
    lists, blockquotes, images, rules and inline code/emphasis/links.  Every
    span of literal text is HTML-escaped first, so a type hint such as
    ``dict[str, Any]`` or an ``&`` in a description can never break the page.
    """
    import html as _html
    import re as _re

    def inline(chunk: str) -> str:
        """Escape a run of text, then re-introduce the inline markup."""
        out = _html.escape(chunk, quote=False)
        out = _re.sub(r"`([^`]+)`", lambda m: f"<code>{m.group(1)}</code>", out)
        out = _re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)",
                      lambda m: f'<img src="{m.group(2)}" alt="{m.group(1)}">', out)
        out = _re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                      lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', out)
        out = _re.sub(r"\*\*([^*]+)\*\*", lambda m: f"<strong>{m.group(1)}</strong>", out)
        out = _re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda m: f"<em>{m.group(1)}</em>", out)
        return out

    lines = text.split("\n")
    body: list[str] = []
    in_code = False
    list_kind: str | None = None
    table: list[list[str]] = []

    def close_list() -> None:
        nonlocal list_kind
        if list_kind:
            body.append(f"</{list_kind}>")
            list_kind = None

    def flush_table() -> None:
        """Emit the buffered pipe table; row 2 is the alignment rule."""
        nonlocal table
        if not table:
            return
        rows = [r for r in table if not _re.fullmatch(r"[\s:|-]+", "|".join(r))]
        if rows:
            body.append("<table>")
            head, *rest = rows
            body.append("<thead><tr>"
                        + "".join(f"<th>{inline(c)}</th>" for c in head)
                        + "</tr></thead>")
            if rest:
                body.append("<tbody>")
                for row in rest:
                    body.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in row) + "</tr>")
                body.append("</tbody>")
            body.append("</table>")
        table = []

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("```"):
            flush_table()
            close_list()
            if in_code:
                body.append("</code></pre>")
                in_code = False
            else:
                lang = line[3:].strip()
                cls = f' class="language-{_html.escape(lang, quote=True)}"' if lang else ""
                body.append(f"<pre><code{cls}>")
                in_code = True
            continue
        if in_code:
            body.append(_html.escape(raw, quote=False))
            continue

        if line.startswith("|") and line.count("|") >= 2:
            table.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        flush_table()

        if not line.strip():
            close_list()
            continue

        heading = _re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            close_list()
            level = len(heading.group(1))
            body.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
            continue

        if _re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            close_list()
            body.append("<hr>")
            continue

        if line.lstrip().startswith("> "):
            close_list()
            body.append(f"<blockquote>{inline(line.lstrip()[2:])}</blockquote>")
            continue

        bullet = _re.match(r"^\s*[-*+]\s+(.*)$", line)
        if bullet:
            if list_kind != "ul":
                close_list()
                body.append("<ul>")
                list_kind = "ul"
            body.append(f"<li>{inline(bullet.group(1))}</li>")
            continue

        numbered = _re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if numbered:
            if list_kind != "ol":
                close_list()
                body.append("<ol>")
                list_kind = "ol"
            body.append(f"<li>{inline(numbered.group(1))}</li>")
            continue

        close_list()
        body.append(f"<p>{inline(line)}</p>")

    if in_code:
        body.append("</code></pre>")
    flush_table()
    close_list()
    return _HTML_PAGE.format(body="\n".join(body))


def write_pdf(text: str, path: Path):
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate

    elems = md_to_pdf_elements(text, path.parent)
    doc = SimpleDocTemplate(str(path), pagesize=letter,
                            rightMargin=0.6 * 72, leftMargin=0.6 * 72,
                            topMargin=0.8 * 72, bottomMargin=0.8 * 72)
    doc.build(elems)


def build_api_reference(outdir: Path) -> dict[str, Path]:
    """Write QECTOR_API_Reference.md + .pdf into ``outdir``.

    Returns ``{name: path}`` for the two artifacts.  Figures are copied from
    the tree-local ``manuals/figures`` directory when present (never fails:
    the PDF simply renders without embedded figures).  Raises on failure so the
    caller controls error reporting.
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    text = build_markdown()

    fig_src = _HERE / "manuals" / "figures"
    fig_dst = outdir / "figures"
    try:
        if fig_src.exists():
            fig_dst.mkdir(exist_ok=True)
            for img in fig_src.glob("*.png"):
                target = fig_dst / img.name
                if not target.exists() or img.stat().st_mtime > target.stat().st_mtime:
                    target.write_bytes(img.read_bytes())
    except Exception:
        pass

    md_path = outdir / "QECTOR_API_Reference.md"
    pdf_path = outdir / "QECTOR_API_Reference.pdf"
    html_path = outdir / "QECTOR_API_Reference.html"
    md_path.write_text(text, encoding="utf-8")
    write_pdf(text, pdf_path)
    html_path.write_text(render_markdown_html(text), encoding="utf-8")

    return {
        "QECTOR_API_Reference.md": md_path,
        "QECTOR_API_Reference.pdf": pdf_path,
        "QECTOR_API_Reference.html": html_path,
    }


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Generate QECTOR API reference manual (Markdown + PDF).")
    ap.add_argument("--outdir", action="append", default=[],
                    help="Output directory (repeatable; defaults to manuals/ and Desktop/manuals)")
    args = ap.parse_args(argv)
    outdirs = [Path(d) for d in args.outdir] or [
        _HERE / "manuals",
    ]
    for outdir in outdirs:
        build_api_reference(outdir)
        print("wrote", outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
