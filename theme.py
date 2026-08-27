"""theme.py  -  Theme, palette and font definitions for QECTOR Workbench."""

from __future__ import annotations

import sys
from types import SimpleNamespace

# matplotlib is intentionally NOT imported at module top-level so that
# ``import theme`` stays cheap and headless-safe.  configure_matplotlib()
# lazy-imports it and is safe to call multiple times.

COLORS_DARK = {
    "bg_panel": "#2b2b2b",
    "bg_panel_alt": "#333333",
    "bg_widget": "#3a3a3a",
    "text_primary": "#dcdcdc",
    "text_secondary": "#a0a0a0",
    "accent": "#4a9eff",
    "accent_dim": "#3a7bd5",
    "bg_status": "#1f1f1f",
    "bg_toast": "#3a2a2a",
    "border_toast": "#ff5555",
    "error": "#ff5555",
    "warning": "#e5a53a",
    "success": "#4caf7d",
    "text_muted": "#8a8a8a",
    "fig_bg": "#242424",
    "axes_bg": "#2b2b2b",
    "grid": "#404040",
    "qubit_node": "#4a9eff",
    "check_node": "#ff9f43",
    "edge": "#6a6a6a",
    "bar": "#4a9eff",
    "bar_dim": "#3a5f8f",
    "bar_hot": "#ff9f43",
    "marker_p50": "#4caf7d",
    "marker_p99": "#ff9f43",
}

COLORS_LIGHT = {
    "bg_panel": "#f5f5f5",
    "bg_panel_alt": "#ebebeb",
    "bg_widget": "#ffffff",
    "text_primary": "#111111",
    "text_secondary": "#555555",
    "accent": "#1a73e8",
    "accent_dim": "#4285f4",
    "bg_status": "#e0e0e0",
    "bg_toast": "#ffebee",
    "border_toast": "#d32f2f",
    "error": "#d32f2f",
    "warning": "#f57c00",
    "success": "#388e3c",
    "text_muted": "#888888",
    "fig_bg": "#ffffff",
    "axes_bg": "#f5f5f5",
    "grid": "#e0e0e0",
    "qubit_node": "#1a73e8",
    "check_node": "#f57c00",
    "edge": "#999999",
    "bar": "#1a73e8",
    "bar_dim": "#90bcf9",
    "bar_hot": "#f57c00",
    "marker_p50": "#388e3c",
    "marker_p99": "#f57c00",
}

COLORS_HIGH_CONTRAST = {
    "bg_panel": "#000000",
    "bg_panel_alt": "#111111",
    "bg_widget": "#000000",
    "text_primary": "#ffffff",
    "text_secondary": "#ffff00",
    "accent": "#00ff00",
    "accent_dim": "#008800",
    "bg_status": "#000000",
    "bg_toast": "#000000",
    "border_toast": "#ffffff",
    "error": "#ff0000",
    "warning": "#ffff00",
    "success": "#00ff00",
    "text_muted": "#ffffff",
    "fig_bg": "#000000",
    "axes_bg": "#000000",
    "grid": "#ffffff",
    "qubit_node": "#00ff00",
    "check_node": "#ffff00",
    "edge": "#ffffff",
    "bar": "#00ff00",
    "bar_dim": "#004400",
    "bar_hot": "#ffff00",
    "marker_p50": "#00ff00",
    "marker_p99": "#ffff00",
}

# For backward compatibility where we absolutely need it
COLORS = COLORS_DARK

_current_mode = "Dark"

def set_appearance_mode(mode: str) -> None:
    """Switch both CustomTkinter and Matplotlib to the new mode."""
    global _current_mode
    _current_mode = mode
    try:
        import customtkinter as ctk
        if mode.lower() == "high contrast":
            ctk.set_appearance_mode("Dark")
        else:
            ctk.set_appearance_mode(mode)
    except Exception:
        pass
    configure_matplotlib()

def c(key: str) -> tuple[str, str]:
    """Return a (light, dark) CustomTkinter color tuple."""
    if _current_mode.lower() == "high contrast":
        return (COLORS_HIGH_CONTRAST[key], COLORS_HIGH_CONTRAST[key])
    return (COLORS_LIGHT[key], COLORS_DARK[key])

def mc(key: str) -> str:
    """Return a single hex string for Matplotlib based on the current mode."""
    if _current_mode.lower() == "high contrast":
        return COLORS_HIGH_CONTRAST[key]
    return COLORS_LIGHT[key] if _current_mode.lower() == "light" else COLORS_DARK[key]

Fonts = SimpleNamespace


def get_fonts() -> SimpleNamespace:
    """Return a namespace with platform-appropriate font definitions.

    Consolas / Segoe UI are Windows fonts; on Linux (and as a macOS fallback)
    Tk would silently substitute an unstyled default.  The DejaVu family ships
    on virtually every Linux distribution and is bundled into the AppImage, so
    the workbench renders identically whether run from source or frozen.  The
    returned namespace shape is identical across platforms, so the six tab
    modules that read ``fonts.mono`` / ``fonts.ui`` need no changes.
    """
    if sys.platform.startswith("win"):
        mono, ui = "Consolas", "Segoe UI"
    elif sys.platform == "darwin":
        mono, ui = "Menlo", "Helvetica Neue"
    else:  # Linux / other POSIX
        mono, ui = "DejaVu Sans Mono", "DejaVu Sans"
    return SimpleNamespace(
        mono=mono,
        ui=ui,
        heading=ui,
        mono_size=10,
        ui_size=10,
        heading_size=14,
    )


# ---------------------------------------------------------------------------
# Matplotlib global quality configuration
# ---------------------------------------------------------------------------

def configure_matplotlib() -> None:
    """Configure matplotlib defaults to match the QECTOR theme.

    This function safe-imports matplotlib and applies settings globally.
    It should be called once on startup (or when switching themes) from
    the Tk main thread.
    """
    try:
        import matplotlib
        import matplotlib.pyplot as plt
        # Switch to non-interactive backend to prevent GUI thread conflicts
        matplotlib.use("Agg")
    except Exception:
        return

    try:
        rc = plt.rcParams

        # --- Resolution / layout -----------------------------------------
        rc["figure.dpi"] = 140          # crisp on-screen (HiDPI-friendly)
        rc["savefig.dpi"] = 300         # publication-grade exports
        rc["figure.constrained_layout.use"] = True

        # --- Anti-aliasing -----------------------------------------------
        rc["lines.antialiased"] = True
        rc["patch.antialiased"] = True
        rc["text.antialiased"] = True

        # --- Path rendering ----------------------------------------------
        rc["path.simplify"] = True
        rc["path.simplify_threshold"] = 0.1   # low threshold → high accuracy
        rc["agg.path.chunksize"] = 10000

        # --- Line / marker quality ----------------------------------------
        rc["lines.linewidth"] = 1.8
        rc["lines.markersize"] = 6
        rc["lines.solid_capstyle"] = "round"

        # --- Typography ---------------------------------------------------
        rc["font.size"] = 10
        rc["font.family"] = "sans-serif"
        # Prefer DejaVu (ships on all platforms), fall back gracefully
        rc["font.sans-serif"] = [
            "DejaVu Sans", "Segoe UI", "Helvetica Neue", "Arial", "sans-serif"
        ]
        rc["axes.titlesize"] = 11
        rc["axes.labelsize"] = 10

        # --- Axes / ticks ------------------------------------------------
        rc["axes.linewidth"] = 0.8
        rc["text.color"] = mc("text_primary")
        rc["axes.labelcolor"] = mc("text_primary")
        rc["xtick.color"] = mc("text_primary")
        rc["ytick.color"] = mc("text_primary")
        rc["axes.edgecolor"] = mc("grid")
        rc["grid.color"] = mc("grid")
        rc["xtick.major.size"] = 4
        rc["xtick.minor.size"] = 2
        rc["xtick.major.width"] = 0.8
        rc["ytick.major.size"] = 4
        rc["ytick.minor.size"] = 2
        rc["ytick.major.width"] = 0.8

        # --- Background colours (based on current theme) -----------------
        rc["figure.facecolor"] = mc("fig_bg")
        rc["axes.facecolor"] = mc("axes_bg")
        rc["savefig.facecolor"] = mc("fig_bg")

        # --- Export quality ----------------------------------------------
        rc["svg.fonttype"] = "none"      # crisp SVG text (editable in Inkscape)
        rc["pdf.fonttype"] = 42          # TrueType embedding
        rc["ps.fonttype"] = 42

    except Exception:
        # matplotlib not installed or rcParams key changed  -  silently ignore
        pass


# ---------------------------------------------------------------------------
# Matplotlib dark styling helpers (figures are only ever touched on the Tk
# main thread; these helpers contain no matplotlib imports of their own so
# importing theme stays cheap).
# ---------------------------------------------------------------------------

def style_dark_figure(fig) -> None:
    """Apply the workbench dark palette to a matplotlib Figure."""
    try:
        fig.set_facecolor(mc("fig_bg"))
        # constrained_layout (set globally by configure_matplotlib) manages
        # padding automatically; only fall back to subplots_adjust when it is
        # not active so we don't trigger the "incompatible" UserWarning.
        try:
            if not fig.get_constrained_layout():
                fig.subplots_adjust(left=0.12, right=0.97, top=0.93, bottom=0.11)
        except AttributeError:
            fig.subplots_adjust(left=0.12, right=0.97, top=0.93, bottom=0.11)
    except Exception:
        pass


def style_dark_axes(
    ax,
    title: str = "",
    xlabel: str = "",
    ylabel: str = "",
    grid: bool = True,
) -> None:
    """Apply the workbench dark palette to a matplotlib Axes.

    Sets facecolor, spine/tick colors, optional title and axis labels, and a
    crisp antialiased grid drawn below the data.  Signature is unchanged from
    v1 so all call-sites continue to work as-is.
    """
    ax.set_facecolor(mc("axes_bg"))

    # Sharper, slightly lighter spines for contrast against the dark bg
    for spine in ax.spines.values():
        spine.set_color(mc("grid"))
        spine.set_linewidth(0.8)

    ax.tick_params(
        colors=mc("text_secondary"),
        labelsize=9,
        length=4,
        width=0.8,
    )
    # Tick labels inherit the same colour as the tick marks
    for label in ax.get_xticklabels() + ax.get_yticklabels():
        label.set_color(mc("text_secondary"))

    if title:
        ax.set_title(title, color=mc("text_primary"), pad=10, fontsize=11)
    if xlabel:
        ax.set_xlabel(xlabel, color=mc("text_secondary"), fontsize=10, labelpad=4)
    if ylabel:
        ax.set_ylabel(ylabel, color=mc("text_secondary"), fontsize=10, labelpad=4)

    if grid:
        # zorder=0 pushes grid behind plotted data (bars, lines, scatters)
        ax.grid(True, color=mc("grid"), linestyle="--", linewidth=0.6, alpha=0.3, zorder=0)
        ax.set_axisbelow(True)
    else:
        ax.grid(False)
        ax.set_axisbelow(True)


def style_dark_legend(legend) -> None:
    """Style a matplotlib legend to match the dark palette."""
    if legend is None:
        return
    try:
        frame = legend.get_frame()
        frame.set_facecolor(COLORS["bg_panel"])
        frame.set_edgecolor(COLORS["grid"])
        frame.set_linewidth(0.8)
        for text in legend.get_texts():
            text.set_color(COLORS["text_primary"])
    except Exception:
        pass
