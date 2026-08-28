"""doc_generator.py: publication-grade documentation generator for QECTOR codes.

Generates Markdown, JSON, HTML, LaTeX, PDF (ReportLab) and SVG documentation
for a QEC code object, plus the two deposit sidecars a Zenodo record needs
(``.zenodo.json`` and ``CITATION.cff``).  All outputs land in a per-user
writable export directory (``utils.get_export_dir()``) unless an explicit
``output_dir`` is supplied.  Decoder recommendations are measured on the code
actually being documented, and every code-derived string is escaped for the
target format (``html.escape`` for HTML, :func:`latex_escape` for LaTeX).

Two house rules apply to every generated artifact:

* **Deposit ready.** Each document carries the metadata a repository record
  needs: creators with ORCID, affiliation, licence, keywords, resource type,
  DOI (when reserved), a formatted citation and a data availability statement.
* **No typographic dashes.** Em dashes, en dashes and Unicode minus signs are
  purged from every rendered artifact by :func:`_nodash`, which runs on the
  final text of each format.  ASCII hyphens inside identifiers are preserved.
"""

from __future__ import annotations

import base64
import html
import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

import utils
from version import DOC_GENERATOR_VERSION

try:
    import backend as be
    _HAS_BACKEND = True
except Exception:
    _HAS_BACKEND = False

WATERMARK = "QECTOR CERTIFIED - PROVENANCE INTACT"

#: Matches embedded version references ("v0.6.6", "0.6.9", ">= 0.6.9", "v3.5.1")
#: so they can be scrubbed from backend-supplied free text.
_VERSION_REF_RE = re.compile(
    r"\s*\((?:>=?\s*)?v?\d+\.\d+\.\d+\)"  # parenthesised, e.g. "(v0.6.9)"
    r"|\s*(?:>=?\s*)?v?\d+\.\d+\.\d+"     # bare, e.g. "v0.6.6" / ">= 0.6.9"
)


def _deversion(value: Any) -> str:
    """Scrub version references from backend-supplied text.

    Generated documents must carry no app or backend version strings,
    including inside decoder descriptions or error messages fetched live
    from the backend.
    """
    return _VERSION_REF_RE.sub("", str(value)).strip()


#: Unicode dashes and their plain-ASCII replacements.  Ordered longest first so
#: the spaced forms win before the bare character is considered.
_DASH_REPLACEMENTS = (
    ("  -  ", ", "),      # spaced em dash  -> comma
    ("  -  ", " to "),    # spaced en dash  -> range word
    (" ‒ ", " to "),    # figure dash
    (" ― ", ", "),      # horizontal bar
    (" - ", ", "),        # bare em dash
    (" - ", " to "),      # bare en dash
    ("‒", " to "),
    ("―", ", "),
    ("−", "-"),         # Unicode minus -> ASCII hyphen-minus
    ("&mdash;", ", "),
    ("&ndash;", " to "),
    ("&minus;", "-"),
)

#: ASCII dash ligatures ("---", "--") used as punctuation in prose. They are
#: handled separately from the table above because a bare "--" is far more often
#: a command-line flag than an en dash: rewriting it unconditionally turned
#: `--mcp` into "to mcp" and `--code X --distance 5` into "to code X to distance
#: 5", i.e. documentation that hands the reader commands that do not work.
#: Only a ligature surrounded by whitespace is punctuation.
_LIGATURE_RE = (
    (re.compile(r"(?<=\s)---(?=\s)"), ", "),
    (re.compile(r"(?<=\s)--(?=\s)"), " to "),
)

#: Markdown constructs made of ASCII hyphens that must survive the dash purge:
#: thematic breaks, table rules and YAML document markers.
_DASH_EXEMPT_RE = re.compile(
    r"^(?:---|\.\.\.)\s*$"              # YAML front matter fences / doc end
    r"|^\s*\|?[\s:|-]{3,}\|?\s*$"       # Markdown table rule rows
    r"|^\s*-{3,}\s*$"                   # thematic break / setext rule
    r"|^\s*\\?(?:midrule|toprule|bottomrule|hline)\b",  # LaTeX booktabs rules
)


def _nodash(text: str) -> str:
    """Remove typographic dashes from rendered document text.

    Em and en dashes read as noise in archived PDFs and break plain-text and
    CSV round-trips, so every generated artifact ships without them.  Applied
    line by line: structural ASCII-hyphen runs (Markdown table rules, YAML
    fences, LaTeX booktabs rules) are exempt, and hyphens inside identifiers
    such as ``qector-decoder-v3`` or ``bp-osd`` are never touched.
    """
    out: list[str] = []
    for line in str(text).split("\n"):
        if _DASH_EXEMPT_RE.match(line):
            out.append(line)
            continue
        touched = False
        for needle, repl in _DASH_REPLACEMENTS:
            if needle in line:
                line = line.replace(needle, repl)
                touched = True
        for rx, repl in _LIGATURE_RE:
            line, n = rx.subn(repl, line)
            touched = touched or bool(n)
        if touched:
            # Tidy only what a substitution can leave behind, and only in the
            # body: leading indentation is structural in Markdown, HTML and
            # LaTeX and must survive untouched.
            indent = line[: len(line) - len(line.lstrip(" "))]
            body = line[len(indent):]
            body = re.sub(r"  +", " ", body)
            body = body.replace(" ,", ",").replace(",,", ",").replace(", ,", ",")
            line = indent + body
        out.append(line)
    return "\n".join(out)

#: Number of timed single-shot decodes per decoder for the recommendations table.
RECOMMENDATION_TRIALS = 25
#: Physical error rate used for the recommendation timing loop.
RECOMMENDATION_ERROR_RATE = 0.05
#: Base seed for the recommendation timing loop (seed = base + trial index).
RECOMMENDATION_SEED_BASE = 1000

_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def latex_escape(value: Any) -> str:
    """Escape LaTeX special characters (\\ & % $ # _ { } ~ ^) in ``value``."""
    return "".join(_LATEX_SPECIALS.get(ch, ch) for ch in str(value))


def _html_escape(value: Any) -> str:
    """HTML-escape the string form of ``value`` (including quotes)."""
    return html.escape(str(value), quote=True)


def _md_cell(value: Any) -> str:
    """Sanitise a value for use inside a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _mpl_text(value: Any) -> str:
    """Sanitise a string for matplotlib text (avoid accidental mathtext)."""
    return str(value).replace("$", r"\$")


def _safe_attr(obj, attr: str, default: Any = "") -> Any:
    return getattr(obj, attr, default)


def _code_metadata(code) -> dict[str, Any]:
    """Collect display metadata from a code object; bound methods are called."""
    md: dict[str, Any] = {}
    for k in ("n_qubits", "n_checks", "distance", "name", "description", "max_qubit_degree"):
        try:
            v = getattr(code, k, None)
            if callable(v):
                v = v()
        except Exception:
            v = None
        if v is not None:
            md[k] = v
    md.setdefault("n_qubits", 0)
    md.setdefault("n_checks", 0)
    return md


def _rate_str(nq: int, nc: int) -> str:
    try:
        return f"{1 - nc / max(int(nq), 1):.4f}" if nq else "N/A"
    except Exception:
        return "N/A"


def _parity_check_dense(code) -> Optional[np.ndarray]:
    """Return the parity-check matrix as a dense 2-D ndarray, or None.

    Handles both attribute-style and callable-style ``parity_check_matrix``
    (falling back to ``H``), and both dense and sparse (todense/toarray)
    representations.
    """
    mat = getattr(code, "parity_check_matrix", None)
    if mat is None:
        mat = getattr(code, "H", None)
    if mat is None:
        return None
    try:
        if callable(mat):
            mat = mat()
        if mat is None:
            return None
        if hasattr(mat, "toarray"):
            mat = mat.toarray()
        elif hasattr(mat, "todense"):
            mat = mat.todense()
        arr = np.asarray(mat)
        if arr.ndim != 2 or arr.size == 0:
            return None
        return arr
    except Exception:
        return None


#: Default deposit licence for generated reports (Zenodo "license" identifier).
PUBLICATION_LICENCE = "CC-BY-4.0"
PUBLICATION_LICENCE_NAME = "Creative Commons Attribution 4.0 International"
PUBLICATION_LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"

#: Subject keywords attached to every generated record.
PUBLICATION_KEYWORDS = (
    "quantum error correction",
    "stabilizer codes",
    "decoder benchmarking",
    "fault tolerance",
    "surface code",
    "qLDPC",
)


def _publication_metadata() -> dict[str, Any]:
    """Resolve the deposit metadata for a generated report.

    Reads the operator profile saved by the Lab and Personal Info tab and fills
    every field a Zenodo record requires.  Missing values fall back to neutral
    placeholders rather than invented identity: an unset author becomes an
    explicit "Unattributed" marker, never a fabricated researcher name, so a
    deposit is never made under a person who does not exist.
    """
    try:
        from lab_info_tab import load_lab_info
        info = load_lab_info()
    except Exception:
        info = {}

    def _clean(key: str, default: str = "") -> str:
        value = str(info.get(key, "") or "").strip()
        return value or default

    author = _clean("author")
    institution = _clean("institution")
    department = _clean("department")

    # Profile keywords extend the standing subject list rather than replacing
    # it, so a record never loses its discipline terms because someone added
    # one project-specific tag.
    keywords = list(PUBLICATION_KEYWORDS)
    for extra in _clean("keywords").split(","):
        extra = extra.strip()
        if extra and extra.lower() not in {k.lower() for k in keywords}:
            keywords.append(extra)

    affiliation = ", ".join(p for p in (department, institution) if p)
    return {
        "author": author or "Unattributed (set your profile in Lab and Personal Info)",
        "author_is_set": bool(author),
        "orcid": _clean("orcid"),
        "institution": institution,
        "department": department,
        "affiliation": affiliation or "Affiliation not set",
        "email": _clean("email"),
        "website": _clean("website"),
        "doi": _clean("doi"),
        "funding": _clean("funding"),
        "publisher": _clean("publisher", "Zenodo"),
        "keywords": keywords,
        "licence": PUBLICATION_LICENCE,
        "licence_name": PUBLICATION_LICENCE_NAME,
        "licence_url": PUBLICATION_LICENCE_URL,
        "watermark": _clean("watermark", WATERMARK),
        "resource_type": "publication-technicalnote",
    }


def _citation_string(meta: dict[str, Any], title: str, year: int) -> str:
    """Format a single-line citation for the generated report."""
    author = meta["author"] if meta["author_is_set"] else "Unattributed"
    publisher = meta.get("publisher") or "Zenodo"
    doi = meta.get("doi")
    tail = f" https://doi.org/{doi}" if doi else " DOI not yet reserved."
    return f"{author} ({year}). {title}. {publisher}.{tail}"


def _provenance_block() -> str:
    from version import BACKEND_VERSION

    backend_ver = BACKEND_VERSION
    try:
        if _HAS_BACKEND and hasattr(be, "PACKAGE_VERSION") and be.PACKAGE_VERSION:
            backend_ver = be.PACKAGE_VERSION
    except Exception:
        pass
    return (
        "Generated by QECTOR Documentation Studio\n"
        f"Decoder Engine: qector-decoder-v3 v{backend_ver}\n"
        f"Timestamp: {datetime.now(timezone.utc).isoformat()}\n"
        f"Watermark: {WATERMARK}\n"
    )


def _log_doc(message: str) -> None:
    """Log a doc-generation diagnostic (best effort; never raises)."""
    try:
        from logger import get_logger
        get_logger().warning(f"doc_generator: {message}")
    except Exception:
        pass


def _write_minimal_svg(path: Path, title: str, md: dict) -> None:
    """Write a valid, non-empty fallback SVG when the rendered Tanner graph
    cannot be produced, so SVG export never reports a hard failure."""
    nq = md.get("n_qubits", "?")
    nc = md.get("n_checks", "?")
    body = (
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600" '
        'viewBox="0 0 800 600">\n'
        '  <rect width="800" height="600" fill="#242424"/>\n'
        f'  <text x="400" y="280" fill="#dcdcdc" font-family="sans-serif" '
        f'font-size="20" text-anchor="middle">{_html_escape(title)}</text>\n'
        f'  <text x="400" y="322" fill="#a0a0a0" font-family="sans-serif" '
        f'font-size="14" text-anchor="middle">{_html_escape(nq)} qubits &#183; '
        f'{_html_escape(nc)} checks</text>\n'
        f'  <text x="400" y="352" fill="#6a8caf" font-family="sans-serif" '
        f'font-size="11" text-anchor="middle">{_html_escape(WATERMARK)}</text>\n'
        '</svg>\n'
    )
    try:
        path.write_text(body, encoding="utf-8")
    except Exception as exc:
        _log_doc(f"minimal SVG write failed: {type(exc).__name__}: {exc}")
        raise


def _stamp_watermark(fig) -> None:
    """Draw the provenance watermark as a small footer on a matplotlib figure.

    Applied to the SVG Tanner graph and to the graph embedded in the PDF, so
    the watermark travels with the rendered figure and not only in metadata.
    The PDF pages themselves are stamped by the ReportLab page callback in
    :meth:`ProfessionalDocGenerator._generate_pdf`.  Best effort: a stamping
    failure never aborts document generation.
    """
    try:
        fig.text(
            0.5, 0.015, _mpl_text(WATERMARK),
            ha="center", va="bottom", fontsize=7, color="#666666",
        )
    except Exception:
        pass


class ProfessionalDocGenerator:
    """Professional multi-format documentation generator for QEC codes."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.version = DOC_GENERATOR_VERSION
        if output_dir is None:
            self.output_dir = utils.get_export_dir()
        else:
            self.output_dir = Path(output_dir)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Per-format writes will report failure honestly if the
            # directory truly cannot be created.
            pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_all(self, code, formats: list[str]) -> dict[str, tuple[bool, Path]]:
        """Generate documentation in each requested format.

        Returns ``{fmt: (ok, path)}`` where ``ok`` is False (with an empty
        path) for unknown formats or per-format failures.  Never raises for a
        single failing format.  A ``<stem>.SHA256SUMS.txt`` manifest carrying
        the real SHA-256 digest of every produced artifact is written beside
        them, so an export can be verified without trusting the transport.
        """
        results: dict[str, tuple[bool, Path]] = {}
        md = _code_metadata(code)
        nq = md.get("n_qubits", 0)
        nc = md.get("n_checks", 0)
        recs = self._benchmark_decoders(code)
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        stem = f"code_doc_{nq}q_{nc}c"
        text_builders = {
            "markdown": (".md", self._generate_markdown),
            "md": (".md", self._generate_markdown),
            "json": (".json", self._generate_json),
            "html": (".html", self._generate_html),
            "latex": (".tex", self._generate_latex),
            "zenodo": (".zenodo.json", self._generate_zenodo),
            "citation": (".cff", self._generate_citation),
        }
        #: JSON payloads are machine-readable and must stay byte-exact; the dash
        #: purge is a typographic rule for rendered prose only.
        literal_formats = {"json", "zenodo"}
        for fmt in formats:
            try:
                if fmt in text_builders:
                    suffix, builder = text_builders[fmt]
                    # The two deposit sidecars describe the *record*, not one
                    # file, so a deposit carries exactly one of each under the
                    # names Zenodo and GitHub look for.
                    if fmt == "citation":
                        path = self.output_dir / "CITATION.cff"
                    elif fmt == "zenodo":
                        path = self.output_dir / ".zenodo.json"
                    else:
                        path = self.output_dir / (stem + suffix)
                    content = builder(code, md, nq, nc, recs)
                    if fmt not in literal_formats:
                        content = _nodash(content)
                    path.write_text(content, encoding="utf-8")
                elif fmt == "pdf":
                    path = self.output_dir / (stem + ".pdf")
                    self._generate_pdf(code, md, nq, nc, recs, path)
                elif fmt == "svg":
                    path = self.output_dir / (stem + ".svg")
                    self._generate_svg(code, md, path)
                else:
                    results[fmt] = (False, Path())
                    continue
                results[fmt] = (True, path.resolve())
            except Exception as exc:
                results[fmt] = (False, Path())
                _log_doc(f"format '{fmt}' failed: {type(exc).__name__}: {exc}")

        # Every data export carries real SHA-256 digests. The figures are
        # written by the builders as a side effect, so they join the format
        # outputs in the manifest: they are part of the same deposit.
        # Every data export carries real SHA-256 digests. The figures are
        # written by the builders as a side effect, so they join the format
        # outputs in the manifest: they are part of the same deposit.
        produced = [path for ok, path in results.values() if ok and path]
        produced += sorted(self.output_dir.glob(f"{stem}_fig*.png"))
        produced += sorted(self.output_dir.glob(f"{stem}_fig*.svg"))
        produced += sorted(self.output_dir.glob(f"{stem}_fig*.pdf"))
        if produced:
            ok_sum, msg = utils.write_sha256_manifest(
                self.output_dir, produced, manifest_name=f"{stem}.SHA256SUMS.txt"
            )
            if ok_sum:
                _log_doc(f"sha256 manifest ({len(produced)} artifacts): {msg}")
            else:
                _log_doc(f"sha256 manifest write failed: {msg}")
        return results

    # ------------------------------------------------------------------
    # Decoder recommendations: measured on THE PASSED CODE
    # ------------------------------------------------------------------
    def _benchmark_decoders(
        self,
        code,
        n_trials: int = RECOMMENDATION_TRIALS,
        error_rate: float = RECOMMENDATION_ERROR_RATE,
    ) -> list[dict[str, Any]]:
        """Time each decoder kind on the code being documented.

        For every kind in ``backend.DECODER_KINDS`` runs ``n_trials`` seeded
        single-shot decodes on ``code`` and reports mean latency (ms) plus the
        observed logical-failure fraction.  Per-decoder failures are recorded
        honestly as ``unavailable: <reason>``.
        """
        rows: list[dict[str, Any]] = []
        if not _HAS_BACKEND:
            return rows
        for kind in be.DECODER_KINDS:
            try:
                description = _deversion(be.get_decoder_info(kind).get("description", ""))
            except Exception:
                description = ""
            try:
                latencies_s: list[float] = []
                failures = 0
                observed = 0
                for i in range(n_trials):
                    t0 = time.perf_counter()
                    out = be.run_single_decode(
                        code, error_rate, kind, seed=RECOMMENDATION_SEED_BASE + i
                    )
                    latencies_s.append(time.perf_counter() - t0)
                    lf = out["result"].logical_failure
                    if lf is not None:
                        observed += 1
                        if lf:
                            failures += 1
                mean_ms = 1000.0 * sum(latencies_s) / len(latencies_s)
                rows.append(
                    {
                        "decoder": kind,
                        "description": description,
                        "status": "ok",
                        "n_trials": n_trials,
                        "error_rate": error_rate,
                        "mean_latency_ms": round(mean_ms, 4),
                        "logical_failure_fraction": (failures / observed) if observed else None,
                    }
                )
            except Exception as exc:
                rows.append(
                    {
                        "decoder": kind,
                        "description": description,
                        "status": _deversion(f"unavailable: {exc}"),
                        "n_trials": n_trials,
                        "error_rate": error_rate,
                        "mean_latency_ms": None,
                        "logical_failure_fraction": None,
                    }
                )
        return rows

    @staticmethod
    def _rec_display(row: dict[str, Any]) -> tuple[str, str, str, str]:
        """Return (decoder, latency, failure-fraction, note) display strings."""
        decoder = str(row.get("decoder", ""))
        if row.get("status") == "ok":
            # An "ok" row with no measurement must degrade to text rather than
            # crash the whole format inside None.__format__.
            mean_ms = row.get("mean_latency_ms")
            latency = "n/a" if mean_ms is None else f"{mean_ms:.3f} ms"
            lff = row.get("logical_failure_fraction")
            failure = "N/A (no logicals matrix)" if lff is None else f"{lff:.3f}"
            note = f"{row.get('n_trials')} trials @ p={row.get('error_rate')}"
        else:
            latency = "n/a"
            failure = "n/a"
            note = str(row.get("status", "unavailable"))
        return decoder, latency, failure, note

    # ------------------------------------------------------------------
    # Markdown
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Markdown: publication standard, Zenodo deposit ready
    # ------------------------------------------------------------------
    def _generate_markdown(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        meta = _publication_metadata()
        name = str(md.get("name", "N/A"))
        distance = str(md.get("distance", "N/A"))
        rate = _rate_str(nq, nc)
        now = datetime.now(timezone.utc)
        timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        title = f"Quantum Error Correction Code Report: {name}"
        citation = _citation_string(meta, title, now.year)
        keywords_csv = ", ".join(meta["keywords"])

        def _yaml(value: str) -> str:
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

        # YAML front matter: consumed directly by Pandoc, Quarto and the Zenodo
        # deposit sidecar, so the same source builds a repository record.
        lines = [
            "---",
            f"title: {_yaml(title)}",
            "authors:",
            f"  - name: {_yaml(meta['author'])}",
            f"    affiliation: {_yaml(meta['affiliation'])}",
        ]
        if meta["orcid"]:
            lines.append(f"    orcid: {_yaml(meta['orcid'])}")
        if meta["email"]:
            lines.append(f"    email: {_yaml(meta['email'])}")
        lines += [
            f"date: {_yaml(now.strftime('%Y-%m-%d'))}",
            f"license: {_yaml(meta['licence'])}",
            f"keywords: [{', '.join(_yaml(k) for k in meta['keywords'])}]",
            f"resource_type: {_yaml(meta['resource_type'])}",
        ]
        if meta["doi"]:
            lines.append(f"doi: {_yaml(meta['doi'])}")
        lines += [
            "---",
            "",
            f"# {title}",
            "",
            f"**Author**: {meta['author']}" + (f" (ORCID {meta['orcid']})" if meta["orcid"] else "") + "  ",
            f"**Affiliation**: {meta['affiliation']}  ",
        ]
        if meta["email"]:
            lines.append(f"**Contact**: {meta['email']}  ")
        lines += [
            f"**Date**: {timestamp_utc}  ",
            f"**Licence**: {meta['licence_name']} ({meta['licence']})  ",
            f"**Keywords**: {keywords_csv}  ",
            f"**Provenance**: {meta['watermark']}  ",
            "",
            "---",
            "",
            "## Table of Contents",
            "",
            "1. [Abstract](#1-abstract)",
            "2. [Code Parameters](#2-code-parameters)",
            "3. [Structural Analysis](#3-structural-analysis)",
            "4. [Decoder Benchmark](#4-decoder-benchmark)",
            "5. [Figures](#5-figures)",
            "6. [Methods](#6-methods)",
            "7. [Data Availability](#7-data-availability)",
            "8. [How to Cite](#8-how-to-cite)",
            "9. [Provenance and Build Environment](#9-provenance-and-build-environment)",
            "",
            "---",
            "",
            "## 1. Abstract",
            "",
            f"This technical note documents the quantum error-correcting code `{name}` "
            f"(distance *d* = {distance}, *n* = {nq} data qubits, *m* = {nc} check operators, "
            f"encoding rate *R* = {rate}). Decoder performance was measured over "
            f"{RECOMMENDATION_TRIALS} seeded, independent single-shot decoding trials per decoder "
            f"at physical error rate *p* = {RECOMMENDATION_ERROR_RATE}. Reported quantities are "
            "mean wall-clock decode latency and the observed logical failure fraction. All "
            "measurements were taken on the code instance described in this document, on the "
            "host recorded under Provenance.",
            "",
            "## 2. Code Parameters",
            "",
            "| Property | Value |",
            "|:---|:---|",
            f"| Code name | `{_md_cell(name)}` |",
            f"| Distance (*d*) | `{_md_cell(distance)}` |",
            f"| Data qubits (*n*) | `{_md_cell(nq)}` |",
            f"| Check operators (*m*) | `{_md_cell(nc)}` |",
            f"| Encoding rate *R* = (*n* - *m*)/*n* | `{_md_cell(rate)}` |",
            f"| Max qubit degree | `{_md_cell(md.get('max_qubit_degree', 'N/A'))}` |",
            "",
        ]
        description = str(md.get("description", "") or "").strip()
        if description:
            lines += [description, ""]

        lines += [
            "## 3. Structural Analysis",
            "",
            f"- Encoding rate (*n* - *m*)/*n*: `{_md_cell(rate)}`",
            f"- Parity-check matrix dimensions: `{_md_cell(nc)} x {_md_cell(nq)}`",
            f"- Stabilizer generators: `{_md_cell(nc)}`",
            "",
            "## 4. Decoder Benchmark",
            "",
            f"Measured on code `{_md_cell(name)}`: {RECOMMENDATION_TRIALS} seeded single-shot "
            f"decoding trials per decoder at physical noise rate *p* = {RECOMMENDATION_ERROR_RATE}. "
            "Decoders incompatible with this code family are reported as unavailable rather than "
            "silently omitted.",
            "",
            f"**Table 1.** Decoder benchmark for `{_md_cell(name)}` at *p* = {RECOMMENDATION_ERROR_RATE}.",
            "",
            "| Decoder | Mean latency | Logical failure fraction | Notes |",
            "|:---|:---|:---|:---|",
        ]
        if recs:
            for row in recs:
                decoder, latency, failure, note = self._rec_display(row)
                lines.append(
                    f"| `{_md_cell(decoder)}` | `{_md_cell(latency)}` | "
                    f"`{_md_cell(failure)}` | {_md_cell(note)} |"
                )
        else:
            lines.append("| *(backend unavailable)* | n/a | n/a | n/a |")

        # Figures are written as sibling PNGs and referenced relatively, so the
        # Markdown renders in any viewer and the images stay usable on their own.
        lines += ["", "## 5. Figures", ""]
        fig_stem = f"code_doc_{nq}q_{nc}c_fig"
        fig_entries = self._write_markdown_figures(code, md, recs, fig_stem)
        if fig_entries:
            for fig_number, fig_caption, fig_file in fig_entries:
                lines += [
                    f"![Figure {fig_number}]({fig_file})",
                    "",
                    f"**Figure {fig_number}.** {fig_caption}",
                    "",
                ]
        else:
            lines += [
                "No figures could be produced for this code: the backend reported no "
                "usable layout and no decoder measurements.",
                "",
            ]

        lines += [
            "",
            "## 6. Methods",
            "",
            f"Each decoder was constructed against the code instance above and run over "
            f"{RECOMMENDATION_TRIALS} independent trials. Trial *i* uses seed "
            f"`{RECOMMENDATION_SEED_BASE} + i`, so the whole benchmark is reproducible from the "
            "seed base recorded in the JSON sidecar. Each trial samples an independent "
            f"depolarizing error at *p* = {RECOMMENDATION_ERROR_RATE}, computes the syndrome, and "
            "times a single decode call. Latency is the arithmetic mean of the per-trial "
            "wall-clock times and excludes decoder construction. The logical failure fraction is "
            "the proportion of trials whose recovery did not return the state to the codespace. "
            "With this trial count the reported failure fraction is an estimate, not a converged "
            "logical error rate; treat it as a screening signal and re-run at higher trial counts "
            "before quoting it as a threshold result.",
            "",
            "## 7. Data Availability",
            "",
            "The JSON sidecar emitted alongside this document carries the full machine-readable "
            "record: code parameters, per-decoder measurements, evaluation settings and the seed "
            "base needed to regenerate every number above. The SVG Tanner graph and the "
            "parity-check dimensions describe the code instance completely, so the code can be "
            "rebuilt without this report.",
            "",
            "## 8. How to Cite",
            "",
            "```text",
            citation,
            "```",
            "",
            f"Licence: {meta['licence_name']} ({meta['licence']}), {meta['licence_url']}",
            "",
        ]
        if meta["funding"]:
            lines += [f"Funding: {meta['funding']}", ""]

        lines += [
            "## 9. Provenance and Build Environment",
            "",
            "```text",
            _provenance_block(),
            "```",
            "",
            "---",
            "",
            f"*Generated by QECTOR Decoder Workbench Doc Generator v{DOC_GENERATOR_VERSION} "
            f"at {timestamp_utc}.*",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # JSON: rich schema v1.2, machine-readable record
    # ------------------------------------------------------------------
    def _generate_json(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        meta = _publication_metadata()
        now = datetime.now(timezone.utc)
        name = str(md.get("name", "N/A"))
        title = f"Quantum Error Correction Code Report: {name}"

        doc = {
            "$schema": "https://qector.store/schemas/qec_code_doc_v1.2.json",
            "schema_version": "1.2",
            "generator": f"QECTOR Decoder Workbench Doc Generator v{DOC_GENERATOR_VERSION}",
            "generator_version": DOC_GENERATOR_VERSION,
            "generated_at": now.isoformat(),
            "watermark": meta["watermark"],
            "provenance_watermark": WATERMARK,
            "metadata": {
                "title": title,
                "author": meta["author"],
                "orcid": meta["orcid"],
                "institution": meta["institution"],
                "department": meta["department"],
                "affiliation": meta["affiliation"],
                "email": meta["email"],
                "website": meta["website"],
            },
            "publication": {
                "resource_type": meta["resource_type"],
                "license": meta["licence"],
                "license_name": meta["licence_name"],
                "license_url": meta["licence_url"],
                "keywords": meta["keywords"],
                "doi": meta["doi"] or None,
                "publisher": meta["publisher"],
                "funding": meta["funding"] or None,
                "citation": _citation_string(meta, title, now.year),
            },
            "code": {
                "name": md.get("name"),
                "distance": md.get("distance"),
                "n_qubits": nq,
                "n_checks": nc,
                "max_qubit_degree": md.get("max_qubit_degree"),
                "description": md.get("description"),
            },
            "analysis": {
                "parity_check_matrix_shape": [nc, nq],
                "rate": _rate_str(nq, nc),
            },
            "evaluation_settings": {
                "trials_per_decoder": RECOMMENDATION_TRIALS,
                "physical_error_rate": RECOMMENDATION_ERROR_RATE,
                "seed_base": RECOMMENDATION_SEED_BASE,
            },
            "recommendations": recs,
            "errors": [],
            "warnings": [],
        }
        return json.dumps(doc, indent=2, default=str)

    # ------------------------------------------------------------------
    # Zenodo deposit sidecars
    # ------------------------------------------------------------------
    def _generate_zenodo(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        """Emit ``.zenodo.json``: the metadata block Zenodo reads on upload."""
        meta = _publication_metadata()
        now = datetime.now(timezone.utc)
        name = str(md.get("name", "N/A"))
        title = f"Quantum Error Correction Code Report: {name}"
        rate = _rate_str(nq, nc)

        creator: dict[str, Any] = {"name": meta["author"]}
        if meta["affiliation"] and meta["affiliation"] != "Affiliation not set":
            creator["affiliation"] = meta["affiliation"]
        if meta["orcid"]:
            creator["orcid"] = meta["orcid"]

        record: dict[str, Any] = {
            "title": title,
            "upload_type": "publication",
            "publication_type": "technicalnote",
            "publication_date": now.strftime("%Y-%m-%d"),
            "description": (
                f"<p>Technical note documenting the quantum error-correcting code "
                f"<em>{_html_escape(name)}</em>: distance {_html_escape(md.get('distance', 'N/A'))}, "
                f"{nq} data qubits, {nc} check operators, encoding rate {rate}. "
                f"Includes a decoder benchmark over {RECOMMENDATION_TRIALS} seeded single-shot "
                f"trials per decoder at physical error rate {RECOMMENDATION_ERROR_RATE}, with "
                f"mean latency and observed logical failure fraction, plus a machine-readable "
                f"JSON sidecar carrying the seed base needed to reproduce every measurement.</p>"
            ),
            "creators": [creator],
            "keywords": meta["keywords"],
            "license": meta["licence"],
            "access_right": "open",
            "language": "eng",
            "version": str(DOC_GENERATOR_VERSION),
        }
        if meta["doi"]:
            record["doi"] = meta["doi"]
        if meta["funding"]:
            record["notes"] = f"Funding: {meta['funding']}"
        return json.dumps(record, indent=2, ensure_ascii=False)

    def _generate_citation(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        """Emit ``CITATION.cff`` (Citation File Format 1.2.0)."""
        meta = _publication_metadata()
        now = datetime.now(timezone.utc)
        name = str(md.get("name", "N/A"))
        title = f"Quantum Error Correction Code Report: {name}"

        def _q(value: Any) -> str:
            return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

        # CFF wants given/family names; a single free-text profile field cannot be
        # split reliably, so it goes in `name` as an entity. That is valid CFF and
        # is honest about what we actually know.
        lines = [
            "cff-version: 1.2.0",
            "message: " + _q("If you use this report, please cite it as below."),
            f"title: {_q(title)}",
            f"date-released: {_q(now.strftime('%Y-%m-%d'))}",
            "type: dataset",
            f"license: {_q(meta['licence'])}",
            f"version: {_q(DOC_GENERATOR_VERSION)}",
            "authors:",
            f"  - name: {_q(meta['author'])}",
        ]
        if meta["affiliation"] and meta["affiliation"] != "Affiliation not set":
            lines.append(f"    affiliation: {_q(meta['affiliation'])}")
        if meta["orcid"]:
            lines.append(f"    orcid: {_q('https://orcid.org/' + meta['orcid'])}")
        if meta["email"]:
            lines.append(f"    email: {_q(meta['email'])}")
        lines.append("keywords:")
        lines += [f"  - {_q(k)}" for k in meta["keywords"]]
        if meta["doi"]:
            lines += ["identifiers:", "  - type: doi", f"    value: {_q(meta['doi'])}"]
        return "\n".join(lines) + "\n"

    # ------------------------------------------------------------------
    # HTML: every interpolated value passes through html.escape
    # ------------------------------------------------------------------
    def _generate_html(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        """Generate a scientific lab-report quality HTML document.

        Follows IEEE/APS typographic conventions: serif body text, numbered
        sections, booktabs-style ruled tables (horizontal rules only, no
        vertical gridlines), monospace provenance, and clean black-on-white
        layout suitable for printing and archival.
        """
        meta = _publication_metadata()
        pub_now = datetime.now(timezone.utc)
        raw_name = str(md.get("name", "N/A"))
        pub_title = f"Quantum Error Correction Code Report: {raw_name}"

        author = _html_escape(meta["author"])
        orcid = _html_escape(meta["orcid"])
        institution = _html_escape(meta["institution"])
        department = _html_escape(meta["department"])
        affiliation = _html_escape(meta["affiliation"])
        email = _html_escape(meta["email"])
        watermark_str = _html_escape(meta["watermark"])
        licence_name = _html_escape(meta["licence_name"])
        licence_id = _html_escape(meta["licence"])
        licence_url = _html_escape(meta["licence_url"])
        keywords_csv = _html_escape(", ".join(meta["keywords"]))
        citation_html = _html_escape(_citation_string(meta, pub_title, pub_now.year))
        funding_html = _html_escape(meta["funding"])
        doi_html = _html_escape(meta["doi"])

        name = _html_escape(md.get("name", "N/A"))
        distance = _html_escape(md.get("distance", "N/A"))
        max_deg = _html_escape(md.get("max_qubit_degree", "N/A"))
        description = md.get("description", "")
        rate = _html_escape(_rate_str(nq, nc))
        timestamp_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        doc_version = _html_escape(DOC_GENERATOR_VERSION)

        # ── Recommendations table body ──
        if recs:
            rec_rows = "".join(
                "<tr><td>{}</td><td class=\"num\">{}</td>"
                "<td class=\"num\">{}</td><td>{}</td></tr>\n".format(
                    *(_html_escape(v) for v in self._rec_display(row))
                )
                for row in recs
            )
        else:
            rec_rows = ("<tr><td colspan=\"4\" class=\"empty\">"
                        "Backend unavailable, decoder benchmarking skipped."
                        "</td></tr>\n")

        # ── Description block (only if the code has one) ──
        desc_block = ""
        if description:
            desc_block = (
                f'<p class="desc">{_html_escape(description)}</p>\n'
            )

        provenance_html = _html_escape(_provenance_block()).replace("\n", "\n")

        # Figures are embedded as data URIs so the report stays a single
        # self-contained file: a report that loses its images when moved out of
        # its folder is not archivable.
        figure_html_parts: list[str] = []
        for fig_number, fig_caption, figure in self._report_figures(code, md, recs):
            try:
                buf = io.BytesIO()
                figure.savefig(buf, format="png",
                               dpi=self.FIGURE_STYLE["savefig.dpi"],
                               bbox_inches="tight", facecolor="white")
                figure.clear()
                encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception as exc:
                _log_doc(f"HTML figure {fig_number} failed: {type(exc).__name__}: {exc}")
                continue
            figure_html_parts.append(
                f'  <figure class="report-figure">\n'
                f'    <img src="data:image/png;base64,{encoded}" '
                f'alt="Figure {fig_number}">\n'
                f'    <figcaption><strong>Figure {fig_number}.</strong> '
                f'{_html_escape(fig_caption)}</figcaption>\n'
                f'  </figure>\n'
            )
        figures_html = "".join(figure_html_parts) or (
            '  <p class="empty">No figures could be produced for this code: the '
            'backend reported no usable layout and no decoder measurements.</p>\n'
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{_html_escape(pub_title)}</title>
<style>
/* ================================================================
   Scientific lab-report stylesheet.
   Typographic model: single-column, serif, 11pt body, modeled on
   IEEE Transactions / Physical Review / Nature formatting.
   ================================================================ */

/* Fonts are resolved from the reader's machine only. A generated report must
   render identically offline and must never phone home to a font CDN: an
   archived deposit that fetches a remote stylesheet leaks every reader's IP
   and breaks the moment that host changes. */

*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

body {{
  font-family: 'CMU Serif', 'Latin Modern Roman', 'Computer Modern',
               'Times New Roman', 'Times', Georgia, serif;
  font-size: 11pt;
  line-height: 1.55;
  color: #111;
  background: #fff;
  -webkit-font-smoothing: antialiased;
}}

.paper {{
  max-width: 680px;
  margin: 0 auto;
  padding: 48px 0 60px;
}}

/* ── Title block ── */
.title-block {{
  text-align: center;
  margin-bottom: 28px;
  padding-bottom: 18px;
  border-bottom: 0.8pt solid #111;
}}
.title-block h1 {{
  font-size: 17pt;
  font-weight: 700;
  letter-spacing: -0.3px;
  margin-bottom: 6px;
  color: #111;
}}
.title-block .affiliation {{
  font-size: 9pt;
  color: #444;
  margin-bottom: 4px;
}}
.title-block .date {{
  font-size: 9pt;
  color: #666;
  font-style: italic;
}}

/* ── Abstract ── */
.abstract {{
  margin: 20px 40px 28px;
  font-size: 9.5pt;
  line-height: 1.5;
  text-align: justify;
}}
.abstract strong {{
  font-variant: small-caps;
  font-weight: 700;
  letter-spacing: 0.5px;
}}

/* ── Section headings (numbered) ── */
h2 {{
  font-size: 12pt;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin: 30px 0 10px;
  color: #111;
  border: none;
  padding: 0;
}}
h3 {{
  font-size: 11pt;
  font-weight: 700;
  font-style: italic;
  margin: 18px 0 6px;
  color: #222;
}}

/* ── Body text ── */
p {{
  margin: 0 0 10px;
  text-align: justify;
  hyphens: auto;
}}
p.desc {{
  font-style: italic;
  color: #333;
  margin-bottom: 14px;
}}

/* ── Booktabs-style tables (horizontal rules only) ── */
table {{
  width: 100%;
  border-collapse: collapse;
  margin: 14px 0 18px;
  font-size: 9.5pt;
  line-height: 1.4;
}}
thead {{
  border-top: 1.6pt solid #111;
  border-bottom: 0.8pt solid #111;
}}
thead th {{
  font-weight: 700;
  padding: 6px 10px;
  text-align: left;
  vertical-align: bottom;
  white-space: nowrap;
  background: none;
  border: none;
  color: #111;
}}
tbody td {{
  padding: 5px 10px;
  border: none;
  border-bottom: 0.4pt solid #ddd;
  vertical-align: top;
}}
tbody tr:last-child td {{
  border-bottom: 1.6pt solid #111;
}}
td.num {{
  font-variant-numeric: tabular-nums;
  font-family: 'Source Code Pro', 'Consolas', 'Monaco', monospace;
  font-size: 9pt;
}}
td.empty {{
  font-style: italic;
  color: #666;
  text-align: center;
  padding: 12px 10px;
}}
caption {{
  font-size: 9pt;
  text-align: left;
  margin-bottom: 6px;
  color: #333;
  caption-side: top;
}}
caption strong {{
  font-variant: small-caps;
}}

/* ── Key-value properties (no-grid layout) ── */
.kv-table td:first-child {{
  font-weight: 600;
  white-space: nowrap;
  width: 170px;
  color: #222;
}}
.kv-table td:last-child {{
  font-family: 'Source Code Pro', 'Consolas', monospace;
  font-size: 9.5pt;
}}

/* ── Table of contents ── */
.toc {{
  margin: 0 auto 26px;
  padding: 12px 18px;
  border: 0.5pt solid #bbb;
  background: #fafafa;
  max-width: 460px;
}}
.toc-heading {{
  font-size: 10pt;
  font-variant: small-caps;
  letter-spacing: 0.6px;
  margin: 0 0 6px;
  padding: 0;
  border: none;
  text-align: center;
}}
.toc ol {{
  margin: 0;
  padding-left: 22px;
  font-size: 10pt;
}}
.toc li {{ margin: 2px 0; }}
.toc a {{ color: #14406b; text-decoration: none; }}
.toc a:hover {{ text-decoration: underline; }}

/* ── Citation and licence ── */
.orcid {{
  font-size: 8.5pt;
  font-variant: small-caps;
  letter-spacing: 0.4px;
  color: #555;
}}
.citation {{
  margin: 8px 0 6px;
  padding: 10px 14px;
  background: #f7f7f7;
  border-left: 2.5pt solid #14406b;
  font-size: 9.5pt;
}}
.licence {{
  font-size: 9pt;
  color: #444;
  margin-bottom: 6px;
}}
.licence a {{ color: #14406b; }}

/* ── Figures ── */
.report-figure {{
  margin: 20px 0 24px;
  text-align: center;
  page-break-inside: avoid;
}}
.report-figure img {{
  max-width: 100%;
  height: auto;
  border: 0.4pt solid #ddd;
}}
.report-figure figcaption {{
  font-size: 8.5pt;
  color: #333;
  margin-top: 6px;
  text-align: left;
  line-height: 1.45;
}}

/* ── Provenance ── */
.provenance {{
  margin-top: 28px;
  padding: 12px 14px;
  background: #f7f7f7;
  border: 0.5pt solid #ccc;
  font-family: 'Source Code Pro', 'Consolas', 'Courier New', monospace;
  font-size: 8pt;
  line-height: 1.55;
  color: #444;
  white-space: pre-wrap;
  word-break: break-word;
}}
.provenance-label {{
  font-family: 'CMU Serif', 'Times New Roman', Georgia, serif;
  font-size: 9pt;
  font-variant: small-caps;
  font-weight: 700;
  letter-spacing: 0.5px;
  color: #222;
  display: block;
  margin-bottom: 4px;
}}

/* ── Footer ── */
.doc-footer {{
  margin-top: 32px;
  padding-top: 10px;
  border-top: 0.4pt solid #999;
  font-size: 8pt;
  color: #888;
  text-align: center;
}}

/* ── Print styles ── */
@media print {{
  body {{ font-size: 10pt; }}
  .paper {{ max-width: none; padding: 0; }}
  .provenance {{ background: none; border: 0.5pt solid #999; }}
  .doc-footer {{ color: #666; }}
  @page {{ margin: 2.5cm; }}
}}
</style>
</head>
<body>
<article class="paper">

  <header class="title-block">
    <h1>{_html_escape(pub_title)}</h1>
    <p class="author"><strong>{author}</strong>{f' <span class="orcid">ORCID {orcid}</span>' if orcid else ''}</p>
    <p class="affiliation">{affiliation}{f'<br><em>{email}</em>' if email else ''}</p>
    <p class="watermark">{watermark_str}</p>
    <p class="date">Generated {timestamp_utc}</p>
  </header>

  <nav class="toc" aria-label="Table of contents">
    <h2 class="toc-heading">Contents</h2>
    <ol>
      <li><a href="#sec-abstract">Abstract</a></li>
      <li><a href="#sec-parameters">Code Parameters</a></li>
      <li><a href="#sec-analysis">Structural Analysis</a></li>
      <li><a href="#sec-benchmark">Decoder Benchmark</a></li>
      <li><a href="#sec-figures">Figures</a></li>
      <li><a href="#sec-methods">Methods</a></li>
      <li><a href="#sec-data">Data Availability</a></li>
      <li><a href="#sec-cite">How to Cite</a></li>
      <li><a href="#sec-provenance">Provenance and Build Environment</a></li>
    </ol>
  </nav>

  <div class="abstract" id="sec-abstract">
    <strong>Abstract.</strong>
    This technical note documents the quantum error-correcting code
    <em>{name}</em> (distance&nbsp;{distance},
    {_html_escape(nq)}&nbsp;data qubits, {_html_escape(nc)}&nbsp;stabilizer
    checks, encoding rate&nbsp;{rate}).
    Decoder performance was measured over {RECOMMENDATION_TRIALS}&nbsp;seeded
    independent single-shot decoding trials per decoder at physical error rate
    <em>p</em>&nbsp;=&nbsp;{RECOMMENDATION_ERROR_RATE}.
    Reported quantities are mean wall-clock decode latency and the observed
    logical failure fraction.
  </div>

  <h2 id="sec-parameters">1. Code Parameters</h2>
  {desc_block}
  <table class="kv-table">
    <thead><tr><th>Property</th><th>Value</th></tr></thead>
    <tbody>
      <tr><td>Code name</td><td>{name}</td></tr>
      <tr><td>Distance</td><td>{distance}</td></tr>
      <tr><td>Data qubits (<em>n</em>)</td><td>{_html_escape(nq)}</td></tr>
      <tr><td>Stabilizer checks (<em>m</em>)</td><td>{_html_escape(nc)}</td></tr>
      <tr><td>Encoding rate (<em>n</em>-<em>m</em>)/<em>n</em></td><td>{rate}</td></tr>
      <tr><td>Parity-check matrix <em>H</em></td><td>{_html_escape(nc)}&times;{_html_escape(nq)}</td></tr>
      <tr><td>Max qubit degree</td><td>{max_deg}</td></tr>
    </tbody>
  </table>

  <h2 id="sec-analysis">2. Structural Analysis</h2>
  <table class="kv-table">
    <tbody>
      <tr><td>Encoding rate</td><td>{rate}</td></tr>
      <tr><td>Parity-check dimensions</td><td>{_html_escape(nc)}&times;{_html_escape(nq)}</td></tr>
      <tr><td>Stabilizer generators</td><td>{_html_escape(nc)}</td></tr>
    </tbody>
  </table>

  <h2 id="sec-benchmark">3. Decoder Benchmark</h2>
  <p>
    Each decoder was benchmarked with {RECOMMENDATION_TRIALS}&nbsp;seeded
    single-shot decodes on the code above at physical error rate
    <em>p</em>&nbsp;=&nbsp;{RECOMMENDATION_ERROR_RATE}. Mean wall-clock
    latency and the observed logical failure fraction are reported.
    Decoders that are incompatible with this code family are listed as
    unavailable rather than silently omitted.
  </p>
  <table>
    <caption><strong>Table 1.</strong> Decoder benchmark results for {name} at <em>p</em>&nbsp;=&nbsp;{RECOMMENDATION_ERROR_RATE}.</caption>
    <thead>
      <tr>
        <th>Decoder</th>
        <th>Mean latency</th>
        <th>Logical failure fraction</th>
        <th>Notes</th>
      </tr>
    </thead>
    <tbody>
{rec_rows}    </tbody>
  </table>

  <h2 id="sec-figures">4. Figures</h2>
{figures_html}
  <h2 id="sec-methods">5. Methods</h2>
  <p>
    Each decoder was constructed against the code instance above and run over
    {RECOMMENDATION_TRIALS}&nbsp;independent trials. Trial <em>i</em> uses seed
    {RECOMMENDATION_SEED_BASE}&nbsp;+&nbsp;<em>i</em>, so the benchmark is
    reproducible from the seed base recorded in the JSON sidecar. Each trial
    samples an independent depolarizing error at
    <em>p</em>&nbsp;=&nbsp;{RECOMMENDATION_ERROR_RATE}, computes the syndrome
    and times a single decode call. Latency is the arithmetic mean of the
    per-trial wall-clock times and excludes decoder construction. The logical
    failure fraction is the proportion of trials whose recovery did not return
    the state to the codespace. At this trial count that fraction is a
    screening estimate, not a converged logical error rate.
  </p>

  <h2 id="sec-data">6. Data Availability</h2>
  <p>
    The JSON sidecar emitted alongside this document carries the full
    machine-readable record: code parameters, per-decoder measurements,
    evaluation settings and the seed base needed to regenerate every number
    above. The SVG Tanner graph and the parity-check dimensions describe the
    code instance completely, so the code can be rebuilt without this report.
  </p>

  <h2 id="sec-cite">7. How to Cite</h2>
  <p class="citation">{citation_html}</p>
  <p class="licence">
    Licence: <a href="{licence_url}">{licence_name} ({licence_id})</a>.
    {f'DOI: {doi_html}.' if doi_html else 'DOI not yet reserved.'}
    {f'Funding: {funding_html}.' if funding_html else ''}
    Keywords: {keywords_csv}.
  </p>

  <h2 id="sec-provenance">8. Provenance and Build Environment</h2>
  <div class="provenance">
    <span class="provenance-label">Build and Environment</span>
{provenance_html}
  </div>

  <footer class="doc-footer">
    QECTOR Decoder Workbench &middot; Doc Generator v{doc_version}
    &middot; {_html_escape(timestamp_utc)}
  </footer>

</article>
</body>
</html>"""

    # ------------------------------------------------------------------
    # LaTeX: every interpolated value passes through latex_escape
    # ------------------------------------------------------------------
    def _generate_latex(self, code, md: dict, nq: int, nc: int, recs: list[dict]) -> str:
        if recs:
            rec_rows = "".join(
                " & ".join(latex_escape(v) for v in self._rec_display(row)) + r" \\" + "\n"
                for row in recs
            )
        else:
            rec_rows = r"(backend unavailable) & n/a & n/a & n/a \\" + "\n"
        meta = _publication_metadata()
        now = datetime.now(timezone.utc)
        tex_title = f"Quantum Error Correction Code Report: {md.get('name', 'N/A')}"
        citation = _citation_string(meta, tex_title, now.year)
        author_tex = latex_escape(meta["author"])
        if meta["orcid"]:
            author_tex += r" \\ \small ORCID " + latex_escape(meta["orcid"])
        if meta["affiliation"]:
            author_tex += r" \\ \small " + latex_escape(meta["affiliation"])
        if meta["email"]:
            author_tex += r" \\ \small \texttt{" + latex_escape(meta["email"]) + "}"

        return (
            r"\documentclass{article}" + "\n"
            r"\usepackage[utf8]{inputenc}" + "\n"
            r"\usepackage{geometry,booktabs,longtable}" + "\n"
            r"\usepackage{fancyhdr}" + "\n"
            r"\geometry{margin=2.5cm}" + "\n"
            r"\pagestyle{fancy}" + "\n"
            rf"\fancyfoot[C]{{\footnotesize {latex_escape(meta['watermark'])}}}" + "\n"
            r"\begin{document}" + "\n"
            rf"\title{{\textbf{{{latex_escape(tex_title)}}}}}" + "\n"
            rf"\author{{{author_tex}}}" + "\n"
            r"\date{\today}" + "\n"
            r"\maketitle" + "\n"
            rf"\begin{{center}}\footnotesize Licence: {latex_escape(meta['licence_name'])} "
            rf"({latex_escape(meta['licence'])}). "
            rf"Keywords: {latex_escape(', '.join(meta['keywords']))}.\end{{center}}" + "\n"
            r"\section*{Code Summary}" + "\n"
            r"\begin{tabular}{ll}" + "\n"
            rf"Qubits: & {latex_escape(nq)} \\" + "\n"
            rf"Checks: & {latex_escape(nc)} \\" + "\n"
            rf"Distance: & {latex_escape(md.get('distance', 'N/A'))} \\" + "\n"
            rf"Name: & {latex_escape(md.get('name', 'N/A'))} \\" + "\n"
            rf"Max qubit degree: & {latex_escape(md.get('max_qubit_degree', 'N/A'))} \\" + "\n"
            r"\end{tabular}" + "\n"
            r"\section*{Code Analysis}" + "\n"
            r"\begin{tabular}{ll}" + "\n"
            rf"Rate: & {latex_escape(_rate_str(nq, nc))} \\" + "\n"
            rf"Parity Check Matrix: & ${int(nc)}\times{int(nq)}$ \\" + "\n"
            r"\end{tabular}" + "\n"
            r"\section*{Decoder Recommendations}" + "\n"
            rf"Measured on this code ({latex_escape(md.get('name', 'unnamed'))}): "
            rf"{RECOMMENDATION_TRIALS} seeded single-shot decodes per decoder at "
            rf"$p={RECOMMENDATION_ERROR_RATE}$." + "\n"
            r"\begin{longtable}{llll}" + "\n"
            r"Decoder & Mean latency & Logical failure fraction & Notes \\" + "\n"
            r"\midrule" + "\n"
            + rec_rows
            + r"\end{longtable}" + "\n"
            r"\section*{How to Cite}" + "\n"
            + latex_escape(citation) + "\n"
            r"\section*{Provenance}" + "\n"
            r"\begin{verbatim}" + "\n"
            + _provenance_block() + "\n"
            r"\end{verbatim}" + "\n"
            r"\end{document}"
        )

    # ------------------------------------------------------------------
    # matplotlib helpers (PDF + SVG)
    # ------------------------------------------------------------------
    @staticmethod
    def _mpl_figure_classes():
        """Import matplotlib without disturbing an already-selected backend.

        Only forces the Agg backend when pyplot has not been imported yet;
        rendering itself uses explicit Figure/FigureCanvasAgg objects, so no
        GUI backend is ever required.
        """
        import matplotlib

        if "matplotlib.pyplot" not in sys.modules:
            try:
                matplotlib.use("Agg")
            except Exception:
                pass
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        return Figure, FigureCanvasAgg

    # ------------------------------------------------------------------
    # Publication figure suite
    # ------------------------------------------------------------------
    #: House style for every figure that lands in a generated report. A report
    #: is read on paper and in a repository viewer, so figures are drawn light
    #: on white at print resolution regardless of the app's dark GUI theme.
    FIGURE_STYLE = {
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "axes.grid": True,
        "grid.alpha": 0.3,
        "grid.linestyle": "--",
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#333333",
        "text.color": "#111111",
        "axes.labelcolor": "#111111",
        "xtick.color": "#333333",
        "ytick.color": "#333333",
        "legend.framealpha": 0.9,
    }

    #: Consistent series colours across every figure in a report.
    FIG_COLORS = {
        "primary": "#1f6fb4",
        "secondary": "#2e8b57",
        "accent": "#d9822b",
        "warn": "#c0392b",
        "muted": "#7f8c8d",
        "qubit": "#4a9eff",
        "check": "#e07a5f",
    }

    def _new_figure(self, width: float, height: float):
        """Create a Figure with the report house style applied locally.

        The style is applied to this Figure only (via ``rc_context`` at draw
        time is not possible for detached Figures, so the axes are styled
        explicitly): the GUI sets global dark rcParams, and a document figure
        must not inherit them or it renders white-on-white when printed.
        """
        Figure, FigureCanvasAgg = self._mpl_figure_classes()
        fig = Figure(figsize=(width, height), dpi=self.FIGURE_STYLE["figure.dpi"])
        try:
            fig.set_layout_engine("none")
        except Exception:
            pass
        fig.patch.set_facecolor("white")
        FigureCanvasAgg(fig)
        return fig

    def _style_axes(self, ax, title: str = "", xlabel: str = "", ylabel: str = "") -> None:
        """Apply the report house style to a single axes."""
        style = self.FIGURE_STYLE
        ax.set_facecolor(style["axes.facecolor"])
        if title:
            ax.set_title(title, fontsize=style["axes.titlesize"], color=style["text.color"])
        if xlabel:
            ax.set_xlabel(xlabel, fontsize=style["axes.labelsize"], color=style["axes.labelcolor"])
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=style["axes.labelsize"], color=style["axes.labelcolor"])
        ax.grid(True, alpha=style["grid.alpha"], linestyle=style["grid.linestyle"], zorder=0)
        ax.set_axisbelow(True)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(style["axes.edgecolor"])
        ax.tick_params(colors=style["xtick.color"], labelsize=style["font.size"] - 0.5)

    @staticmethod
    def _label_bars(ax, bars, values, fmt: str = "{:.2f}") -> None:
        """Write each bar's value just above it.

        A reader should not have to measure a bar against an axis to recover the
        number: the figure and the table must agree without interpolation.

        The offset is expressed in points rather than data units, so labels sit
        the same distance above the bar on a linear and on a log axis. An
        additive data-space pad puts every small bar's label near the top of a
        log plot, which is how this first shipped.
        """
        for bar, value in zip(bars, values):
            if value is None or not np.isfinite(value):
                continue
            ax.annotate(
                fmt.format(value),
                xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
                xytext=(0, 3), textcoords="offset points",
                ha="center", va="bottom", fontsize=7.5, color="#111111",
                clip_on=False,
            )

    @staticmethod
    def _ok_rows(recs: list[dict]) -> list[dict]:
        """Benchmark rows that actually produced a measurement."""
        return [r for r in (recs or []) if r.get("status") == "ok"
                and r.get("mean_latency_ms") is not None]

    def _fig_latency(self, recs: list[dict], name: str):
        """Figure: mean decode latency per decoder, sorted fastest first."""
        rows = sorted(self._ok_rows(recs), key=lambda r: r["mean_latency_ms"])
        if not rows:
            return None
        labels = [str(r["decoder"]) for r in rows]
        values = [float(r["mean_latency_ms"]) for r in rows]

        fig = self._new_figure(7.4, max(3.2, 0.32 * len(rows) + 1.9))
        ax = fig.add_subplot(111)
        bars = ax.bar(labels, values, color=self.FIG_COLORS["primary"],
                      edgecolor="black", linewidth=0.6, zorder=3)
        self._label_bars(ax, bars, values, fmt="{:.3f}")

        fastest = values[0]
        ax.axhline(fastest, ls="--", lw=1.0, color=self.FIG_COLORS["secondary"],
                   zorder=4, label=f"fastest: {labels[0]} at {fastest:.3f} ms")
        # A spread over more than two orders of magnitude flattens every bar but
        # the slowest, so switch to log and say so on the axis.
        if fastest > 0 and max(values) / fastest > 100:
            ax.set_yscale("log")
            ylabel = "mean latency (ms, log scale)"
        else:
            ylabel = "mean latency (ms)"
        self._style_axes(ax, title=f"Mean decode latency by decoder on {name}",
                         ylabel=ylabel)
        ax.tick_params(axis="x", rotation=35)
        for lbl in ax.get_xticklabels():
            lbl.set_ha("right")
            lbl.set_rotation_mode("anchor")
        ax.legend(loc="upper left", fontsize=7.5)
        fig.tight_layout()
        return fig

    def _fig_failure(self, recs: list[dict], name: str):
        """Figure: observed logical failure fraction, against the physical rate."""
        rows = [r for r in self._ok_rows(recs)
                if r.get("logical_failure_fraction") is not None]
        if not rows:
            return None
        rows = sorted(rows, key=lambda r: float(r["logical_failure_fraction"]))
        labels = [str(r["decoder"]) for r in rows]
        values = [float(r["logical_failure_fraction"]) for r in rows]

        fig = self._new_figure(7.4, max(3.2, 0.32 * len(rows) + 1.9))
        ax = fig.add_subplot(111)
        colors = [self.FIG_COLORS["secondary"] if v <= RECOMMENDATION_ERROR_RATE
                  else self.FIG_COLORS["warn"] for v in values]
        bars = ax.bar(labels, values, color=colors, edgecolor="black",
                      linewidth=0.6, zorder=3)
        self._label_bars(ax, bars, values, fmt="{:.3f}")
        ax.axhline(RECOMMENDATION_ERROR_RATE, ls="--", lw=1.1,
                   color=self.FIG_COLORS["accent"], zorder=4,
                   label=f"physical error rate p = {RECOMMENDATION_ERROR_RATE}")
        self._style_axes(
            ax,
            title=f"Observed logical failure fraction on {name} "
                  f"({RECOMMENDATION_TRIALS} trials per decoder)",
            ylabel="logical failure fraction",
        )
        ax.tick_params(axis="x", rotation=35)
        for lbl in ax.get_xticklabels():
            lbl.set_ha("right")
            lbl.set_rotation_mode("anchor")
        ax.legend(loc="upper left", fontsize=7.5)
        # State the sampling limit on the figure itself, so a bar at zero is not
        # read as "never fails".
        ax.text(0.99, 0.02,
                f"resolution limit: 1/{RECOMMENDATION_TRIALS} = "
                f"{1.0 / RECOMMENDATION_TRIALS:.3f}",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=7, color=self.FIG_COLORS["muted"], style="italic")
        fig.tight_layout()
        return fig

    def _fig_tradeoff(self, recs: list[dict], name: str):
        """Figure: latency against logical failure, with the Pareto front marked."""
        rows = [r for r in self._ok_rows(recs)
                if r.get("logical_failure_fraction") is not None]
        if len(rows) < 2:
            return None
        xs = [float(r["mean_latency_ms"]) for r in rows]
        ys = [float(r["logical_failure_fraction"]) for r in rows]
        labels = [str(r["decoder"]) for r in rows]

        # Pareto front: nothing is both faster and more accurate.
        order = sorted(range(len(rows)), key=lambda i: (xs[i], ys[i]))
        front: list[int] = []
        best_y = float("inf")
        for i in order:
            if ys[i] < best_y - 1e-12:
                front.append(i)
                best_y = ys[i]
        front_set = set(front)

        fig = self._new_figure(7.0, 4.4)
        ax = fig.add_subplot(111)

        # Annotating every point produces an unreadable smear whenever many
        # decoders share a value (typically a whole cluster at zero observed
        # failures). Label only what a reader needs to identify: the Pareto
        # front, anything that actually failed, and the slowest decoder. The
        # unlabelled remainder is summarised in words below the cloud.
        slowest = max(range(len(xs)), key=lambda i: xs[i])
        to_label = set(front) | {i for i, y in enumerate(ys) if y > 0} | {slowest}

        for i, (x, y, lbl) in enumerate(zip(xs, ys, labels)):
            on_front = i in front_set
            ax.scatter(x, y, s=70 if on_front else 42,
                       color=self.FIG_COLORS["secondary"] if on_front
                       else self.FIG_COLORS["muted"],
                       edgecolors="black", linewidths=0.6,
                       zorder=4 if on_front else 3,
                       label="Pareto optimal" if on_front and i == front[0] else
                             ("dominated" if not on_front and i == order[-1] else None))
            if i in to_label:
                ax.annotate(lbl, xy=(x, y), xytext=(4, 5), textcoords="offset points",
                            fontsize=7, color="#111111", zorder=5)

        hidden = [i for i in range(len(xs)) if i not in to_label]
        if hidden:
            lo, hi = min(xs[i] for i in hidden), max(xs[i] for i in hidden)
            ax.text(0.5, -0.22,
                    f"{len(hidden)} further decoders recorded zero failures in "
                    f"{RECOMMENDATION_TRIALS} trials, spanning {lo:.3f} to {hi:.3f} ms; "
                    f"they are plotted but not labelled. See Table 1 for every value.",
                    transform=ax.transAxes, ha="center", va="top",
                    fontsize=7, color=self.FIG_COLORS["muted"], style="italic")
        if len(front) > 1:
            ax.plot([xs[i] for i in front], [ys[i] for i in front],
                    ls="--", lw=1.0, color=self.FIG_COLORS["secondary"], zorder=2)
        ax.axhline(RECOMMENDATION_ERROR_RATE, ls=":", lw=1.0,
                   color=self.FIG_COLORS["accent"],
                   label=f"physical rate p = {RECOMMENDATION_ERROR_RATE}")
        if max(xs) / max(min(xs), 1e-9) > 100:
            ax.set_xscale("log")
        self._style_axes(
            ax,
            title=f"Speed against accuracy on {name}: lower left is better",
            xlabel="mean latency (ms)",
            ylabel="logical failure fraction",
        )
        handles, lbls = ax.get_legend_handles_labels()
        if handles:
            ax.legend(loc="upper right", fontsize=7.5)
        fig.tight_layout()
        return fig

    def _fig_parity_pattern(self, code, md: dict):
        """Figure: sparsity pattern of the parity-check matrix."""
        H = _parity_check_dense(code)
        if H is None:
            return None
        rows, cols = H.shape

        # Square cells keep the matrix readable as a matrix. Only fall back to a
        # stretched aspect when a square rendering would be taller than a page.
        cell = 0.34 if max(rows, cols) <= 24 else 0.12
        width = min(7.4, max(3.4, cols * cell + 1.4))
        height = min(6.5, max(2.2, rows * cell + 1.6))
        square = max(rows, cols) <= 64

        fig = self._new_figure(width, height)
        ax = fig.add_subplot(111)
        ax.imshow(H, cmap="Greys", interpolation="nearest",
                  aspect="equal" if square else "auto", vmin=0, vmax=1)

        density = float(np.count_nonzero(H)) / float(rows * cols or 1)
        row_w = np.count_nonzero(H, axis=1)
        col_w = np.count_nonzero(H, axis=0)
        self._style_axes(
            ax,
            title=f"Parity-check matrix H ({rows} x {cols}), density {density:.3f}",
            xlabel="qubit index", ylabel="check index",
        )
        ax.grid(False)
        # Cell borders help only while individual cells are still resolvable.
        if max(rows, cols) <= 32:
            ax.set_xticks(np.arange(-0.5, cols, 1), minor=True)
            ax.set_yticks(np.arange(-0.5, rows, 1), minor=True)
            ax.grid(which="minor", color="#bbbbbb", linewidth=0.4)
            ax.tick_params(which="minor", length=0)
        # Placed below the axis: at the top it collides with the title.
        ax.text(0.5, -0.30,
                f"row weight {row_w.min()} to {row_w.max()};   "
                f"column weight {col_w.min()} to {col_w.max()}",
                transform=ax.transAxes, ha="center", va="top",
                fontsize=7.5, color=self.FIG_COLORS["muted"])
        fig.tight_layout()
        return fig

    def _report_figures(self, code, md: dict, recs: list[dict]) -> list[tuple[str, str, Any]]:
        """Build the numbered figure suite for a report.

        Returns ``[(number, caption, figure)]``.  A figure that cannot be built
        from the available data is skipped rather than emitted empty, and the
        numbering closes over the gap so captions stay contiguous.
        """
        name = str(md.get("name", "this code"))
        candidates = [
            ("Tanner graph of {n}: qubit nodes (circles) joined to the checks "
             "(squares) they participate in.",
             lambda: self._tanner_figure(code, md, f"Tanner graph: {name}")),
            ("Sparsity pattern of the parity-check matrix. Each dark cell marks "
             "a qubit that the corresponding stabilizer acts on.",
             lambda: self._fig_parity_pattern(code, md)),
            ("Mean decode latency per decoder, fastest first. Bars are labelled "
             "with the measured value; the dashed line marks the fastest decoder.",
             lambda: self._fig_latency(recs, name)),
            ("Observed logical failure fraction per decoder against the physical "
             "error rate. Bars above the dashed line did not improve on doing "
             "nothing at this noise level.",
             lambda: self._fig_failure(recs, name)),
            ("Speed against accuracy. Points on the dashed front are Pareto "
             "optimal: no other decoder measured here is both faster and more "
             "accurate.",
             lambda: self._fig_tradeoff(recs, name)),
        ]
        figures: list[tuple[str, str, Any]] = []
        for caption, builder in candidates:
            try:
                fig = builder()
            except Exception as exc:
                _log_doc(f"figure skipped ({type(exc).__name__}: {exc})")
                continue
            if fig is None:
                continue
            number = len(figures) + 1
            figures.append((str(number), caption.replace("{n}", name), fig))
        return figures

    def _write_markdown_figures(self, code, md: dict, recs: list[dict],
                                stem: str) -> list[tuple[str, str, str]]:
        """Render the figure suite as PNGs beside the Markdown file.

        Returns ``[(number, caption, filename)]`` for the figures that were
        written.  A failure to write one figure never aborts the document: the
        entry is simply absent and the caller renders the rest.
        """
        written: list[tuple[str, str, str]] = []
        for number, caption, fig in self._report_figures(code, md, recs):
            filename = f"{stem}{number}.png"
            try:
                fig.savefig(self.output_dir / filename, format="png",
                            dpi=self.FIGURE_STYLE["savefig.dpi"],
                            bbox_inches="tight", facecolor="white")
                # Publication-quality standalone exports alongside the PNG:
                # vector SVG and print-ready PDF are written independently so
                # each figure can be embedded or printed without the report.
                for vector_fmt, vector_ext in (("svg", "svg"), ("pdf", "pdf")):
                    vname = f"{stem}{number}.{vector_ext}"
                    try:
                        fig.savefig(self.output_dir / vname, format=vector_fmt,
                                    bbox_inches="tight", facecolor="white")
                    except Exception as vexc:
                        _log_doc(f"vector figure {number} ({vector_ext}) failed: "
                                 f"{type(vexc).__name__}: {vexc}")
                written.append((number, caption, filename))
            except Exception as exc:
                _log_doc(f"markdown figure {number} failed: {type(exc).__name__}: {exc}")
            finally:
                try:
                    fig.clear()
                except Exception:
                    pass
        return written

    def _tanner_layout(self, code, md: dict) -> tuple[list, list]:
        """Qubit/check coordinates from the backend layout engine."""
        if not _HAS_BACKEND:
            raise RuntimeError("backend unavailable: cannot compute Tanner graph layout")
        family = str(md.get("name", ""))
        try:
            distance = int(md.get("distance", 0))
        except Exception:
            distance = 0
        return be.get_tanner_graph_layout(code, family, distance)

    def _tanner_figure(self, code, md: dict, title: str):
        """Build a rendered Tanner-graph Figure for the given code."""
        Figure, FigureCanvasAgg = self._mpl_figure_classes()
        from matplotlib.collections import LineCollection

        q_coords, c_coords = self._tanner_layout(code, md)
        fig = Figure(figsize=(8.0, 6.0), dpi=150)
        # Render deterministically regardless of the app's global matplotlib
        # rcParams.  The GUI enables constrained_layout, which conflicts with the
        # explicit tight_layout() below and has raised on large, equal-aspect
        # figures in some matplotlib builds  -  the cause of SVG export failures on
        # big codes.  Force the layout engine off here so doc export is stable.
        try:
            fig.set_layout_engine("none")
        except Exception:
            try:
                fig.set_constrained_layout(False)
            except Exception:
                pass
        FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)

        H = _parity_check_dense(code)
        segments = []
        if H is not None:
            rows, cols = np.nonzero(H)
            for r, c in zip(rows.tolist(), cols.tolist()):
                if r < len(c_coords) and c < len(q_coords):
                    segments.append([c_coords[r], q_coords[c]])
        if segments:
            ax.add_collection(
                LineCollection(segments, colors="#9a9a9a", linewidths=0.7,
                               alpha=0.75, zorder=1)
            )
        # Scale marker size down as the node count grows so large codes stay
        # legible instead of collapsing into a solid blob.
        n_nodes = len(q_coords) + len(c_coords)
        size = float(np.clip(3200.0 / max(n_nodes, 1), 12.0, 90.0))
        if q_coords:
            ax.scatter(
                [p[0] for p in q_coords], [p[1] for p in q_coords],
                marker="o", s=size, c="#4a9eff", edgecolors="#1a3c6e", linewidths=0.5,
                label=f"qubits ({len(q_coords)})", zorder=2,
            )
        if c_coords:
            ax.scatter(
                [p[0] for p in c_coords], [p[1] for p in c_coords],
                marker="s", s=size, c="#e07a5f", edgecolors="#7a2e1d", linewidths=0.5,
                label=f"checks ({len(c_coords)})", zorder=3,
            )
        ax.set_title(_mpl_text(title), fontsize=11)
        ax.set_aspect("equal", adjustable="datalim")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.margins(0.06)
        if q_coords or c_coords:
            ax.legend(loc="upper right", fontsize=8)
        try:
            fig.tight_layout()
        except Exception:
            pass
        _stamp_watermark(fig)
        return fig

    # ------------------------------------------------------------------
    # PDF: IEEE / APS publication-grade ReportLab generation
    # ------------------------------------------------------------------
    def _generate_pdf(self, code, md: dict, nq: int, nc: int, recs: list[dict], path: Path) -> int:
        """Write a publication-grade PDF report to path using ReportLab; returns the page count."""
        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import inch
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
        except ImportError:
            raise RuntimeError("reportlab is required for PDF generation")

        import io
        meta = _publication_metadata()
        now = datetime.now(timezone.utc)

        author = meta["author"]
        institution = meta["institution"]
        department = meta["department"]
        affiliation = meta["affiliation"]
        email = meta["email"]
        watermark_str = meta["watermark"]

        name = md.get("name", "code")
        distance = md.get("distance", "N/A")
        rate = _rate_str(nq, nc)
        timestamp_utc = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        pdf_title = f"Quantum Error Correction Code Report: {name}"
        citation = _citation_string(meta, pdf_title, now.year)

        doc = SimpleDocTemplate(
            str(path),
            pagesize=letter,
            title=pdf_title,
            author=author,
            subject=watermark_str,
            keywords=", ".join(meta["keywords"]),
            creator="QECTOR Decoder Workbench",
            producer="QECTOR Decoder Workbench",
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch
        )

        styles = getSampleStyleSheet()

        title_style = ParagraphStyle(
            'SciTitle',
            parent=styles['Heading1'],
            fontName='Times-Bold',
            fontSize=18,
            leading=22,
            spaceAfter=4,
            textColor=colors.black,
            alignment=1
        )

        author_style = ParagraphStyle(
            'SciAuthor',
            parent=styles['Normal'],
            fontName='Times-Bold',
            fontSize=11,
            leading=14,
            spaceAfter=2,
            alignment=1
        )

        affil_style = ParagraphStyle(
            'SciAffil',
            parent=styles['Normal'],
            fontName='Times-Italic',
            fontSize=9.5,
            leading=12,
            spaceAfter=12,
            alignment=1,
            textColor=colors.HexColor('#333333')
        )

        heading_style = ParagraphStyle(
            'SciHeading',
            parent=styles['Heading2'],
            fontName='Times-Bold',
            fontSize=12,
            leading=15,
            spaceBefore=14,
            spaceAfter=6,
            textColor=colors.black
        )

        body_style = ParagraphStyle(
            'SciBody',
            parent=styles['Normal'],
            fontName='Times-Roman',
            fontSize=10,
            leading=14,
            spaceAfter=6,
            alignment=4 # Justified
        )

        mono_style = ParagraphStyle(
            'SciMono',
            parent=styles['Normal'],
            fontName='Courier',
            fontSize=8,
            leading=10,
            textColor=colors.HexColor('#222222')
        )

        cite_style = ParagraphStyle(
            'SciCite',
            parent=styles['Normal'],
            fontName='Times-Roman',
            fontSize=8.5,
            leading=11,
            spaceAfter=4,
            leftIndent=10,
            textColor=colors.HexColor('#333333')
        )

        elements = []

        def P(text: str, style) -> None:
            """Append a paragraph with typographic dashes purged."""
            elements.append(Paragraph(_nodash(text), style))

        # Document Header
        P(_nodash(pdf_title), title_style)
        orcid_suffix = f"  (ORCID {meta['orcid']})" if meta["orcid"] else ""
        P(html.escape(str(author)) + html.escape(orcid_suffix), author_style)
        affil_lines = html.escape(affiliation)
        if email:
            affil_lines += f"<br/><i>{html.escape(str(email))}</i>"
        P(affil_lines, affil_style)
        elements.append(Spacer(1, 8))

        # Abstract
        abstract_text = (
            f"<b>Abstract.</b> This technical note documents the characteristics and benchmark "
            f"performance of the quantum error-correcting code <i>{html.escape(str(name))}</i> "
            f"(distance <i>d</i> = {distance}, <i>n</i> = {nq} data qubits, <i>m</i> = {nc} check operators, "
            f"rate <i>R</i> = {rate}). Decoder performance was measured over {RECOMMENDATION_TRIALS} seeded, "
            f"independent single-shot trials per decoder at physical error rate "
            f"<i>p</i> = {RECOMMENDATION_ERROR_RATE}. Reported quantities are mean wall-clock decode "
            f"latency and the observed logical failure fraction."
        )
        P(abstract_text, body_style)
        elements.append(Spacer(1, 6))

        keywords_line = ", ".join(meta["keywords"])
        P(f"<b>Keywords:</b> {html.escape(keywords_line)}", cite_style)
        P(f"<b>Licence:</b> {html.escape(meta['licence_name'])} "
          f"({html.escape(meta['licence'])})", cite_style)
        elements.append(Spacer(1, 8))

        # Section I: Code Parameters
        P("I. CODE PARAMETERS", heading_style)

        summary_data = [
            ["Property", "Value"],
            ["Code Name", str(name)],
            ["Distance (d)", str(distance)],
            ["Data Qubits (n)", str(nq)],
            ["Check Operators (m)", str(nc)],
            ["Encoding Rate (n-m)/n", rate],
            ["Parity-Check Matrix H", f"{nc} x {nq}"],
            ["Max Qubit Degree", str(md.get("max_qubit_degree", "N/A"))],
        ]

        t_summary = Table(summary_data, colWidths=[2.5 * inch, 4.0 * inch])
        t_summary.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.black),
            ('LINEBELOW', (0, -1), (-1, -1), 1.2, colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Times-Roman'),
            ('FONTNAME', (1, 1), (1, -1), 'Courier'),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.black),
            ('PADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(t_summary)
        elements.append(Spacer(1, 14))

        # Section II: Decoder Performance
        P("II. DECODER PERFORMANCE", heading_style)
        P(f"Table I summarizes wall-clock decoding latency and observed logical failure fractions "
          f"across compatible decoders evaluated on <i>{html.escape(str(name))}</i> at physical noise "
          f"probability <i>p</i> = {RECOMMENDATION_ERROR_RATE}. Trial <i>i</i> uses seed "
          f"{RECOMMENDATION_SEED_BASE} + <i>i</i>, so every figure below is reproducible from the "
          f"JSON sidecar. At {RECOMMENDATION_TRIALS} trials the failure fraction is a screening "
          f"estimate, not a converged logical error rate.", body_style)

        rec_data = [["Decoder", "Mean Latency", "Logical Failure Fraction", "Notes"]]
        if recs:
            for row in recs:
                d_name, d_lat, d_fail, d_note = self._rec_display(row)
                rec_data.append([d_name, d_lat, d_fail, d_note])
        else:
            rec_data.append(["(backend unavailable)", "N/A", "N/A", "N/A"])

        t_rec = Table(rec_data, colWidths=[1.8 * inch, 1.3 * inch, 1.6 * inch, 1.8 * inch])
        t_rec.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1.2, colors.black),
            ('LINEBELOW', (0, 0), (-1, 0), 0.6, colors.black),
            ('LINEBELOW', (0, -1), (-1, -1), 1.2, colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 8.5),
            ('FONTNAME', (0, 1), (-1, -1), 'Times-Roman'),
            ('FONTNAME', (1, 1), (2, -1), 'Courier'),
            ('PADDING', (0, 0), (-1, -1), 4),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        elements.append(t_rec)
        elements.append(Spacer(1, 14))

        # Section III: Tanner Graph Structure
        P("III. FIGURES", heading_style)
        caption_style = ParagraphStyle(
            'SciCaption',
            parent=styles['Normal'],
            fontName='Times-Italic',
            fontSize=8.5,
            leading=11,
            spaceBefore=3,
            spaceAfter=12,
            alignment=1,
            textColor=colors.HexColor('#333333')
        )

        figures = self._report_figures(code, md, recs)
        if not figures:
            P("No figures could be produced for this code: the backend reported no "
              "usable layout and no decoder measurements.", body_style)
        for number, caption, fig in figures:
            img_data = io.BytesIO()
            try:
                fig.savefig(img_data, format="png",
                            dpi=self.FIGURE_STYLE["savefig.dpi"],
                            bbox_inches="tight", facecolor="white")
            except Exception as exc:
                _log_doc(f"figure {number} save failed: {type(exc).__name__}: {exc}")
                continue
            finally:
                try:
                    fig.clear()
                except Exception:
                    pass
            img_data.seek(0)
            img = RLImage(img_data)
            aspect = img.imageWidth / float(max(img.imageHeight, 1))
            target_w = 6.0 * inch
            img.drawWidth = target_w
            img.drawHeight = target_w / aspect
            elements.append(img)
            P(f"<b>Figure {number}.</b> {html.escape(caption)}", caption_style)

        # Section IV: Data availability and citation
        P("IV. DATA AVAILABILITY AND CITATION", heading_style)
        P("The JSON sidecar emitted alongside this report carries the full machine-readable "
          "record: code parameters, per-decoder measurements, evaluation settings and the seed "
          "base needed to regenerate every number above. Deposit sidecars "
          "(<font face='Courier'>.zenodo.json</font> and <font face='Courier'>CITATION.cff</font>) "
          "are written next to it.", body_style)
        P("<b>Cite as:</b>", body_style)
        P(html.escape(citation), cite_style)
        if meta["funding"]:
            P(f"<b>Funding:</b> {html.escape(meta['funding'])}", cite_style)
        if not meta["author_is_set"]:
            P("<b>Note:</b> no author profile is set. Open the Lab and Personal Info tab and save "
              "your profile before depositing this report, so the record carries a real creator "
              "and ORCID.", cite_style)
        elements.append(Spacer(1, 8))

        # Section V: Provenance & Environment
        P("V. PROVENANCE AND ENVIRONMENT", heading_style)
        provenance_text = html.escape(_provenance_block()).replace('\n', '<br/>')
        elements.append(Paragraph(_nodash(provenance_text), mono_style))

        def page_footer(canvas, doc):
            canvas.saveState()
            canvas.setFont('Times-Roman', 8.5)
            canvas.setFillColor(colors.HexColor('#444444'))
            canvas.drawString(0.75 * inch, 0.4 * inch, f"{watermark_str} | Generated {timestamp_utc}")
            canvas.drawRightString(letter[0] - 0.75 * inch, 0.4 * inch, f"Page {doc.page}")
            canvas.restoreState()

        doc.build(elements, onFirstPage=page_footer, onLaterPages=page_footer)
        return doc.page

    # ------------------------------------------------------------------
    # SVG: standalone Tanner graph with the document title embedded
    # ------------------------------------------------------------------
    def _generate_svg(self, code, md: dict, path: Path) -> None:
        name = md.get("name", "code")
        title = f"QECTOR Code Documentation: {name} Tanner graph"
        metadata = {
            "Title": title,
            "Creator": "QECTOR Decoder Workbench",
            "Description": WATERMARK,
        }
        # Robust, progressive fallbacks: SVG export must never fail.  Any
        # failure (metadata rejected, a matplotlib layout/backend quirk on large
        # codes, a rendering error) drops to the next, simpler strategy, and the
        # final strategy writes a valid minimal SVG with no matplotlib at all.
        fig = None
        try:
            fig = self._tanner_figure(code, md, title)
        except Exception as exc:
            _log_doc(f"SVG Tanner figure build failed ({type(exc).__name__}: {exc}); "
                     "writing minimal SVG")
        if fig is not None:
            for label, saver in (
                ("with metadata", lambda: fig.savefig(path, format="svg", metadata=metadata)),
                ("without metadata", lambda: fig.savefig(path, format="svg")),
            ):
                try:
                    saver()
                    fig.clear()
                    return
                except Exception as exc:
                    _log_doc(f"SVG save ({label}) failed: {type(exc).__name__}: {exc}")
            try:
                fig.clear()
            except Exception:
                pass
        # Last resort: always produces a valid, non-empty SVG file.
        _write_minimal_svg(path, title, md)
