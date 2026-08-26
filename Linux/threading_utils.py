"""threading_utils.py — Threading utilities for QECTOR Workbench."""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable, Optional


def run_in_background(target: Callable, args: tuple = (), daemon: bool = True) -> threading.Thread:
    """Run a function in a background thread."""
    t = threading.Thread(target=target, args=args, daemon=daemon)
    t.start()
    return t


class CancelToken:
    """Cooperative cancellation token for background tasks."""

    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()


class UiPump:
    """Marshal callables from worker threads onto the Tk main thread.

    Calling ``widget.after(0, ...)`` directly from a worker thread raises
    ``RuntimeError: main thread is not in main loop`` whenever the app is
    being driven by ``update()`` (tests, tooling) instead of ``mainloop()``.
    ``UiPump`` avoids that entirely: worker threads :meth:`post` callables
    into a thread-safe queue, and a widget-bound ``after`` chain — created on
    the UI thread in ``__init__`` — drains the queue every ``interval_ms``
    milliseconds.  The pump closes itself when the widget is destroyed.
    """

    def __init__(self, widget, interval_ms: int = 25):
        self._widget = widget
        self._interval = max(1, int(interval_ms))
        self._queue: "queue.SimpleQueue[Callable[[], None]]" = queue.SimpleQueue()
        # _closed is read/written from both worker threads (post) and the UI
        # thread (close/_schedule); the lock makes the check-then-act in post
        # atomic against close().
        self._lock = threading.Lock()
        self._closed = False
        self._after_id: Optional[str] = None
        try:
            widget.bind("<Destroy>", self._on_destroy, add="+")
        except Exception:
            pass
        self._schedule()

    # -- UI thread side ---------------------------------------------------
    def _schedule(self) -> None:
        if self._closed:
            return
        try:
            self._after_id = self._widget.after(self._interval, self._drain)
        except Exception:
            self._closed = True

    def _drain(self) -> None:
        try:
            while True:
                try:
                    fn = self._queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    fn()
                except Exception:
                    # A failing marshalled callback must never kill the pump.
                    try:
                        from logger import get_logger

                        get_logger().error(
                            "UI-marshalled callback raised:\n" + traceback.format_exc()
                        )
                    except Exception:
                        pass
        finally:
            self._schedule()

    def _on_destroy(self, event=None) -> None:
        # CTk widgets delegate bind() to an internal child canvas, and a
        # <Destroy> binding on a toplevel fires for every descendant, so the
        # event widget is matched by Tk path: the bound widget itself or one
        # of its internal children closes the pump; unrelated widgets do not.
        if event is not None:
            widget = getattr(event, "widget", None)
            if widget is not None:
                wpath = str(widget)
                mypath = str(self._widget)
                if mypath == ".":
                    if wpath != ".":
                        return
                elif wpath != mypath and not wpath.startswith(mypath + "."):
                    return
        self.close()

    # -- any thread -------------------------------------------------------
    def post(self, fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """Queue ``fn(*args, **kwargs)`` for execution on the UI thread.

        Returns False (never raises) when the pump is already closed because
        the widget was destroyed.
        """
        with self._lock:
            if self._closed:
                return False
            self._queue.put(lambda: fn(*args, **kwargs))
            return True

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            after_id, self._after_id = self._after_id, None
        if after_id is not None:
            try:
                self._widget.after_cancel(after_id)
            except Exception:
                pass
