"""console.py  -  Thread-safe console output buffer for QECTOR Workbench."""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable

MAX_LINES = 5000


class Console:
    """Thread-safe, bounded in-memory console buffer with subscriber callbacks.

    At most ``max_lines`` entries are retained; when the buffer is full the
    oldest entries are dropped first.  Subscribers registered with
    :meth:`subscribe` are invoked outside the lock with each newly written
    chunk of text; a failing subscriber never breaks the console or the other
    subscribers.  All methods are safe to call from any thread.
    """

    def __init__(self, max_lines: int = MAX_LINES):
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=max(1, int(max_lines)))
        self._callbacks: list[Callable[[str], None]] = []

    def subscribe(self, callback: Callable[[str], None]) -> None:
        """Register a callback invoked with each newly written text chunk."""
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]) -> None:
        """Remove a previously registered callback (no-op if absent)."""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass

    def write(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
            callbacks = list(self._callbacks)
        for cb in callbacks:
            try:
                cb(text)
            except Exception:
                pass

    def log(self, text: str, level: str = "INFO") -> None:
        self.write(f"[{level}] {text}\n")

    def get_text(self) -> str:
        with self._lock:
            return "".join(self._lines)

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
