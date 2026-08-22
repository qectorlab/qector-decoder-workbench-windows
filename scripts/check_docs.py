"""Check public documentation against live values.

Public docs drift silently. Before this existed, `AGENT.md` advertised
"WORKBENCH_VERSION = 3.5.1", "backend 0.6.9", "47 tools", "13 decoders" and
"9 code families" while the code served 0.5.3 / 0.7.0 / 56 / 16 / 10, and the
README's download table listed four artifacts that were never built.

Run it before tagging a release:

    python scripts/check_docs.py            # report, exit 1 on any finding
    python scripts/check_docs.py --quiet    # only the summary line

What it checks, against the live registry and backend rather than a copy:

* MCP tool, decoder and code-family counts asserted in prose;
* workbench and backend version strings;
* the old contact address;
* download tables that name an artifact absent from release_assets/.

Historical statements are not findings. A line inside a section whose heading
names an older release, or containing a past-tense marker such as "was",
"previously" or "deleted in", is allowed to carry old numbers: a changelog that
cannot describe the past is useless.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: Docs that make factual claims to the public.
TRACKED = [
    "README.md", "README_LINUX.md", "AGENT.md", "PACKAGING.md",
    "PROJECT_STATUS.md", "RELEASE_REPORT.md",
    "docs/architecture.md", "docs/README.txt", "manuals/README.txt",
]

#: Changelog-shaped documents: only their newest section is checked.
CHANGELOGS = ["CHANGELOG.md", "UPGRADE_NOTES.md"]

#: A line carrying one of these is describing history, not the present.
HISTORY_MARKERS = re.compile(
    r"\b(was|were|previously|used to|no longer|removed in|deleted in|dropped in"
    r"|earlier|before|until|had\b|claimed|prior to|as of workbench|first shipped"
    r"|superseded|legacy|shipped with|also shipped|that read|edition of"
    r"|unchanged from|left at|title bumped|now\b.*\binstead)\b",
    re.IGNORECASE,
)
#: Section headings that scope their content to an older release.
OLD_SECTION = re.compile(r"^#+\s.*\b(v?\d+\.\d+\.\d+)\s*(?:→|->|to)\s*(v?\d+\.\d+\.\d+)"
                         r"|^#+\s*(?:Release|What Changed).*\bv?\d+\.\d+\.\d+",
                         re.IGNORECASE)


def live_facts() -> dict:
    import version
    import backend as be
    from mcp_server import get_mcp_server

    server = get_mcp_server()
    node, table = server, None
    for _ in range(4):
        candidate = getattr(node, "tools", None)
        if isinstance(candidate, dict):
            table = candidate
            break
        if candidate is None:
            break
        node = candidate
    return {
        "workbench": version.WORKBENCH_VERSION,
        "backend": be.PACKAGE_VERSION,
        "mcp_tools": len(table) if table else -1,
        "decoders": len(be.DECODER_KINDS),
        "families": len(be.CODE_FAMILIES),
        "contact": version.CONTACT_EMAIL,
    }


def scan(path: Path, facts: dict, newest_section_only: bool) -> list[str]:
    """Return findings for one document."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return [f"unreadable: {exc}"]

    lines = text.split("\n")
    findings: list[str] = []
    in_old_section = False
    seen_first_section = False

    checks = (
        (re.compile(r"\*{0,2}(\d+)\*{0,2}[\s-]+MCP tools?\b", re.I), facts["mcp_tools"], "MCP tools"),
        # Only an explicit assignment. The looser "\D{0,10}" form read
        # "56 MCP tools, 16 decoders" as "MCP tools = 16", flagging a correct line.
        (re.compile(r"\bMCP[_ ]?[Tt]ools?\s*[:=]\s*\*{0,2}(\d+)"), facts["mcp_tools"], "MCP tools"),
        # (?<![.\d]) so a version fragment cannot be read as a count: "v0.7.0
        # decoder wheel" was reported as "says 0 decoders".
        (re.compile(r"(?<![.\d])\*{0,2}(\d+)\*{0,2}\s+decoders?\b", re.I),
         facts["decoders"], "decoders"),
        (re.compile(r"\*{0,2}(\d+)\*{0,2}\s+code famil", re.I), facts["families"], "code families"),
    )
    stale_versions = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")

    for n, line in enumerate(lines, 1):
        # Only level-2 headings change section scope. Letting a "###" subheading
        # reset it made every subsection of an old release look current, which
        # is how this checker first reported 13 findings inside a changelog's
        # historical sections.
        if line.startswith("## "):
            if OLD_SECTION.match(line):
                in_old_section = True
            elif newest_section_only and seen_first_section:
                in_old_section = True
            else:
                in_old_section = False
            seen_first_section = True
            continue
        if line.startswith("#"):
            continue
        if in_old_section or HISTORY_MARKERS.search(line):
            continue

        for rx, expected, label in checks:
            for m in rx.finditer(line):
                got = int(m.group(1))
                if got != expected:
                    findings.append(f"L{n}: says {got} {label}, live is {expected}: {m.group(0).strip()!r}")

        for m in stale_versions.finditer(line):
            v = m.group(1)
            # Python runtime versions (e.g. 3.12.0, 3.12.3) are not workbench/backend versions
            if "python" in line.lower():
                continue
            if v not in (facts["workbench"], facts["backend"]) and v.count(".") == 2:
                if v.split(".")[0] in ("0", "3") and v not in ("1.2.0", "2.0.0", "4.0.0"):
                    findings.append(f"L{n}: version {v} is neither workbench "
                                    f"{facts['workbench']} nor backend {facts['backend']}: "
                                    f"{line.strip()[:70]!r}")

        if "contact@qector.store" in line:
            findings.append(f"L{n}: old contact address; use {facts['contact']}")

    return findings


def check_download_claims(facts: dict) -> list[str]:
    """Every artifact named in the README download table must exist."""
    readme = REPO / "README.md"
    if not readme.exists():
        return []
    text = readme.read_text(encoding="utf-8", errors="replace")
    section = re.search(r"##\s*[^\n]*Downloads(.*?)(?:\n---|\Z)", text, re.S)
    if not section:
        return []
    release = REPO / "release_assets"
    present = {p.name for p in release.iterdir()} if release.is_dir() else set()
    findings = []
    # Only the first column names a shipped artifact; later cells describe
    # what is *inside* a bundle and must not be required to exist on their own.
    for row in section.group(1).splitlines():
        cells = [c.strip() for c in row.strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        m = re.search(r"`([A-Za-z0-9_.\-]+\.(?:zip|exe|deb|dmg|AppImage))`", cells[0])
        if not m:
            continue
        name = m.group(1)
        if name not in present:
            findings.append(f"README download table names {name}, "
                            f"which is not in release_assets/")
    return findings


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true", help="only print the summary line")
    args = ap.parse_args(argv)

    facts = live_facts()
    if not args.quiet:
        print("Live facts:")
        for k, v in facts.items():
            print(f"  {k:11s} {v}")
        print()

    total = 0
    for name in TRACKED + CHANGELOGS:
        path = REPO / name
        if not path.exists():
            continue
        findings = scan(path, facts, newest_section_only=name in CHANGELOGS)
        if findings:
            total += len(findings)
            if not args.quiet:
                print(f"{name} ({len(findings)}):")
                for f in findings:
                    print(f"  {f}")
                print()

    download = check_download_claims(facts)
    if download:
        total += len(download)
        if not args.quiet:
            print(f"README downloads ({len(download)}):")
            for f in download:
                print(f"  {f}")
            print()

    if total:
        print(f"check_docs: {total} finding(s). Docs disagree with the code.")
        return 1
    print("check_docs: public docs agree with the live code.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
