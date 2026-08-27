"""state.py  -  Application state management for QECTOR Workbench."""

from __future__ import annotations

import traceback
from typing import Any, Callable

import backend as be


class AppState:
    """Holds the current application state: selected code family, parameter, and code object."""

    def __init__(self):
        self.current_family_key: str = "rotated_surface"
        self.current_param: int = 5
        self.current_code: Any = None
        self._code_changed_callbacks: list[Callable[[], None]] = []

    def on_code_changed(self, callback: Callable[[], None]) -> None:
        self._code_changed_callbacks.append(callback)

    def set_code(self, code: Any, family: str, param: int) -> None:
        self.current_code = code
        self.current_family_key = family
        self.current_param = param
        for cb in list(self._code_changed_callbacks):
            try:
                cb()
            except Exception:
                # A broken listener must not break the others, but the
                # failure is logged rather than silently swallowed.
                try:
                    from logger import get_logger

                    get_logger().error(
                        "AppState code-changed listener raised:\n" + traceback.format_exc()
                    )
                except Exception:
                    pass

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.current_family_key, "param": self.current_param}

    def restore(self, snapshot: dict[str, Any]) -> None:
        try:
            self.current_family_key = snapshot["family"]
            self.current_param = snapshot["param"]
            self.current_code = be.build_code(self.current_family_key, self.current_param)
        except Exception:
            self.current_code = None
