"""mcp_resources.py  -  Resource manager for MCP Server (tracking allocations)."""

from __future__ import annotations

import threading
from typing import Any, Optional


class _ResourceManager:
    """Thread-safe resource tracker for MCP allocations."""

    def __init__(self):
        self._lock = threading.Lock()
        self._resources: dict[str, dict[str, Any]] = {}

    def allocate_resource(self, resource_id: str, rtype: str, metadata: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if resource_id in self._resources:
                raise KeyError(f"resource {resource_id!r} already exists")
            entry = {"id": resource_id, "type": rtype, "metadata": metadata}
            self._resources[resource_id] = entry
            return entry

    def get_resource(self, resource_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._resources.get(resource_id)

    def list_resources(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._resources.values())

    def delete_resource(self, resource_id: str) -> bool:
        with self._lock:
            if resource_id in self._resources:
                del self._resources[resource_id]
                return True
            return False


_manager: Optional[_ResourceManager] = None
_manager_lock = threading.Lock()


def get_resource_manager() -> _ResourceManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = _ResourceManager()
    return _manager
