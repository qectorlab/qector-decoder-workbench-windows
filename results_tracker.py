"""results_tracker.py  -  Benchmark and decode result tracking."""

from __future__ import annotations

from typing import Any


class ResultsTracker:
    """Tracks benchmark and decode results in memory."""

    def __init__(self):
        self._results: list[dict[str, Any]] = []

    def add(self, result: dict[str, Any]) -> None:
        self._results.append(result)

    def get_all(self) -> list[dict[str, Any]]:
        return list(self._results)

    def clear(self) -> None:
        self._results.clear()
