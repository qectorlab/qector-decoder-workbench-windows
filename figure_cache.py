"""figure_cache.py  -  LRU figure cache for QECTOR Workbench GUI tabs.

Matplotlib figures re-render on every tab switch or parameter change; for
expensive visualisations (Tanner graphs of large codes, benchmark charts
over hundreds of results) that is wasteful.  This module provides a small
LRU cache keyed on ``(view, *params)`` returning a pickled figure state that
callers can restore via ``pickle.loads``  -  figures themselves are not
picklable, but their drawing state is, so a cached figure restores exactly
what was last drawn without re-running the layout algorithm.

The cache is a convenience layer: every function here is a best-effort
wrapper and never raises.  Headless/import-time safety is guaranteed  -  the
cache never imports matplotlib itself.
"""

from __future__ import annotations

import collections
import pickle
import threading
from typing import Any, Callable, Optional

_MAX_ENTRIES = 12
_lock = threading.Lock()
_cache: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()


def _key(view: str, params: Any) -> str:
    try:
        return view + "|" + repr(params)
    except Exception:
        return view + "|"


def make_key(view: str, *params: Any) -> str:
    """Build a stable cache key from a view name and hashable params."""
    return _key(view, tuple(_freeze(p) for p in params))


def _freeze(value: Any) -> Any:
    try:
        hash(value)
        return value
    except Exception:
        try:
            return tuple(value)
        except Exception:
            return str(value)


def get(view: str, params: Any) -> Optional[bytes]:
    """Return cached figure state bytes for (view, params), or None."""
    key = _key(view, params)
    try:
        with _lock:
            if key not in _cache:
                return None
            _cache.move_to_end(key)
            return _cache[key]
    except Exception:
        return None


def put(view: str, params: Any, state: bytes) -> None:
    """Store figure state bytes under (view, params), evicting LRU entries."""
    key = _key(view, params)
    try:
        with _lock:
            _cache[key] = state
            _cache.move_to_end(key)
            while len(_cache) > _MAX_ENTRIES:
                _cache.popitem(last=False)
    except Exception:
        pass


def dumps_state(state: Any) -> bytes:
    """Pickle an object (e.g. a Figure) into cacheable bytes."""
    try:
        return pickle.dumps(state, protocol=4)
    except Exception:
        return b""


def loads_state(blob: bytes) -> Any:
    """Unpickle cached figure state; returns None on any failure."""
    if not blob:
        return None
    try:
        return pickle.loads(blob)
    except Exception:
        return None


def cached(view: str, params: Any, factory: Callable[[], Any]) -> Any:
    """Return a restored figure state from cache or build it via *factory*.

    ``factory`` must return a picklable figure state (e.g. a Figure).  The
    result is cached and also returned to the caller.
    """
    blob = get(view, params)
    if blob is not None:
        state = loads_state(blob)
        if state is not None:
            return state
    state = factory()
    put(view, params, dumps_state(state))
    return state


def clear() -> None:
    """Drop every cached entry (used by tests and "Clear Cache" actions)."""
    try:
        with _lock:
            _cache.clear()
    except Exception:
        pass


def size() -> int:
    try:
        with _lock:
            return len(_cache)
    except Exception:
        return 0
