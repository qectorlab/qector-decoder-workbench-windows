"""shortcuts.py  -  single source of truth for QECTOR Workbench keyboard shortcuts.

``app.py`` binds every entry in :data:`SHORTCUTS`; the Keyboard Shortcuts
dialog (F1) and the compact hint shown in Lab & Personal Info both render
from the same list, so the three surfaces can never drift out of sync with
each other again.
"""

from __future__ import annotations

#: (accelerator, description) in the order they should be listed/bound.
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+N", "Go to Code Explorer"),
    ("Ctrl+R", "Run decode (Decoder Lab)"),
    ("Ctrl+B", "Run benchmark (Benchmark)"),
    ("Ctrl+D", "Generate documentation"),
    ("Ctrl+E", "Export from the current tab"),
    ("Ctrl+H", "Go to History"),
    ("Ctrl+,", "Go to Lab & Personal Info"),
    ("F5", "Refresh the current tab"),
    ("F1", "Show this shortcut list"),
    ("Ctrl+Tab", "Next tab"),
    ("Ctrl+Shift+Tab", "Previous tab"),
    ("Ctrl+Q", "Quit"),
)


def hint_text(max_items: int = 6) -> str:
    """A compact one-block hint for inline display (e.g. Lab & Personal Info)."""
    lines = [f"\u2022 {accel}: {desc}" for accel, desc in SHORTCUTS[:max_items]]
    return "Keyboard Shortcuts (F1 for the full list):\n" + "\n".join(lines)


def dialog_lines() -> list[str]:
    """One aligned line per shortcut, for the full Keyboard Shortcuts dialog."""
    width = max(len(accel) for accel, _ in SHORTCUTS)
    return [f"{accel:<{width}}   {desc}" for accel, desc in SHORTCUTS]
