"""
tests/test_app_gui.py — GUI smoke tests for QECTOR Workbench.

Live-widget tests are conditionally skipped when Tk/Tcl is not usable on this
host; import-only and pure-python state checks still run regardless.

Window hygiene
--------------
Every Tk root we create is ``withdraw()``-n *immediately* so the test run
does not pop real windows on the host desktop.  A session-scoped
``hidden_root`` fixture gives a single shared, invisible CTk root to all
tests that need a Tk parent; tests that need a full ``QectorApp`` build one
and withdraw it before any geometry call.  Net effect: zero visible windows
during ``pytest tests/``.
"""

from __future__ import annotations

from typing import Any, Generator, List

import pytest


@pytest.fixture(autouse=True)
def _reset_default_root():
    """Clear tkinter's cached default root when it points at a dead interpreter.

    A destroyed Tk root keeps ``tkinter._default_root`` alive until the object is
    garbage-collected; the next ``CTkFont``/widget then calls ``font create`` on
    a dead interpreter ("application has been destroyed").  This fixture unlinks
    only roots that no longer exist, so repeated QectorApp bootstraps in one
    pytest process stay deterministic.
    """
    yield
    import tkinter
    root = tkinter._default_root
    if root is not None:
        try:
            root.winfo_exists()
        except Exception:
            tkinter._default_root = None


# ---------------------------------------------------------------------------
# Guard: only run GUI tests when Tk works on this host.
#
# The probe creates a Tk root and withdraws it before destroy so the
# availability check never flashes a window on the host.
# ---------------------------------------------------------------------------
def _tk_works() -> bool:
    try:
        import tkinter as tk
        root = tk.Tk()
        try:
            root.withdraw()
        except Exception:
            pass
        root.destroy()
        return True
    except Exception:
        return False


_HAS_TK = _tk_works()


def _gui_deps_installed() -> bool:
    if not _HAS_TK:
        return False
    try:
        import customtkinter  # noqa: F401
        import matplotlib  # noqa: F401
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _gui_deps_installed(), reason="GUI deps/tk not available"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _all_children(widget) -> List[Any]:
    """Recursively collect widget children."""
    out = [widget]
    try:
        for child in widget.winfo_children():
            out.extend(_all_children(child))
    except Exception:
        pass
    return out


def _make_hidden_qector_app():
    """Instantiate ``QectorApp`` and withdraw the root window immediately.

    A full ``QectorApp`` builds dozens of widgets and a default geometry; on
    Windows that briefly creates a visible toplevel. Withdrawing the inner
    Tk root *before* the constructor returns is awkward (the call itself
    may compute geometry that briefly shows the window), so we instead
    patch ``customtkinter.CTk.__init__`` to call ``withdraw()`` right after
    the parent constructor finishes — that path is stable across CustomTkinter
    5.x and is the same one ``QectorApp`` uses.
    """
    import customtkinter as ctk
    import app as _app

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    original_init = ctk.CTk.__init__

    def init_then_hide(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        try:
            self.withdraw()
        except Exception:
            pass

    ctk.CTk.__init__ = init_then_hide
    try:
        return _app.QectorApp()
    finally:
        ctk.CTk.__init__ = original_init


# ---------------------------------------------------------------------------
# Import-only / pure-python smoke tests (always run)
# ---------------------------------------------------------------------------

def test_tab_modules_import_cleanly():
    """Top-level tab modules must import without errors."""
    modules = [
        "code_explorer_tab",
        "decoder_lab_tab",
        "benchmark_tab",
        "batch_streaming_tab",
        "hardware_tab",
        "documentation_tab",
        "app",
    ]
    failed = []
    for mod in modules:
        try:
            __import__(mod)
        except Exception as exc:
            failed.append(f"{mod}: {exc}")
    assert not failed, f"tab/app module import errors: {failed}"


def test_state_round_trip_via_backend_builder():
    """AppState serializes family/param and rebuilds the code from backend."""
    from state import AppState

    state = AppState()
    state.current_family_key = "rotated_surface"
    state.current_param = 5

    snapshot = state.to_dict()
    assert snapshot["family"] == "rotated_surface"
    assert snapshot["param"] == 5

    restored = AppState()
    restored.restore(snapshot)
    assert restored.current_family_key == "rotated_surface"
    assert restored.current_param == 5
    assert restored.current_code is not None
    assert restored.current_code.n_qubits > 0


def test_version_values_exposed():
    from version import WORKBENCH_VERSION, DOC_GENERATOR_VERSION

    assert isinstance(WORKBENCH_VERSION, str)
    assert len(WORKBENCH_VERSION) > 0
    assert isinstance(DOC_GENERATOR_VERSION, str)
    assert len(DOC_GENERATOR_VERSION) > 0


# ---------------------------------------------------------------------------
# Live-GUI tests (only when Tk/Tcl is actually present)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def hidden_root() -> Generator[Any, None, None]:
    """One shared, invisible CTk root for the whole test session.

    Session-scoped so the suite does not create a fresh Tk interpreter per
    test.  Withdrawn before yield so no window flashes on the host.
    """
    import customtkinter as ctk

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")

    root = ctk.CTk()
    try:
        root.withdraw()
    except Exception:
        pass
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:
            pass


def test_app_instantiates_without_mainloop():
    """QectorApp can be instantiated without entering mainloop."""
    if not _HAS_TK:
        pytest.skip("Tk/Tcl is not usable on this host")
    try:
        app = _make_hidden_qector_app()
    except Exception as e:
        if "TclError" in type(e).__name__ or "tk" in str(e).lower():
            pytest.skip(f"Tk/Tcl environment unavailable: {e}")
        raise
    assert app is not None
    assert app.title()
    try:
        app.destroy()
    except Exception:
        pass


def test_app_requested_size_is_large():
    """App requested size should be large enough for the QEC dashboard."""
    if not _HAS_TK:
        pytest.skip("Tk/Tcl is not usable on this host")
    try:
        app = _make_hidden_qector_app()
    except Exception as e:
        if "TclError" in type(e).__name__ or "tk" in str(e).lower():
            pytest.skip(f"Tk/Tcl environment unavailable: {e}")
        raise
    try:
        req_w = max(app.winfo_reqwidth(), 0)
        req_h = max(app.winfo_reqheight(), 0)
    finally:
        try:
            app.destroy()
        except Exception:
            pass
    assert req_w >= 900, f"requested width too small: {req_w}"
    assert req_h >= 700, f"requested height too small: {req_h}"


def test_app_exposes_console():
    if not _HAS_TK:
        pytest.skip("Tk/Tcl is not usable on this host")
    app = _make_hidden_qector_app()
    try:
        assert hasattr(app, "console"), "QectorApp must expose a .console attribute"
    finally:
        try:
            app.destroy()
        except Exception:
            pass


def test_app_window_control_buttons():
    """Verify window size control handlers and toggle actions exist and execute cleanly."""
    if not _HAS_TK:
        pytest.skip("Tk/Tcl is not usable on this host")
    app = _make_hidden_qector_app()
    try:
        assert hasattr(app, "_toggle_maximize"), "QectorApp must have _toggle_maximize"
        assert hasattr(app, "_toggle_theme"), "QectorApp must have _toggle_theme"
        assert hasattr(app, "_show_eula_viewer"), "QectorApp must have _show_eula_viewer"
        
        # Invoke maximize toggle
        app._toggle_maximize()
        app._toggle_theme()
    finally:
        try:
            app.destroy()
        except Exception:
            pass

