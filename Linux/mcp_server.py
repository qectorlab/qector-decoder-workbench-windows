"""mcp_server.py — MCP server for QECTOR Decoder Workbench.

Two layers in one module:

1. An in-process tool registry exposing exactly 85 tools wired to the real
   backend (``get_mcp_server()``, ``call_mcp_tool()``, ``MCPError``).  Every
   tool result is passed through a JSON sanitizer so it always survives
   ``json.dumps``.
2. A real MCP stdio transport: newline-delimited JSON-RPC 2.0 over
   stdin/stdout implementing ``initialize``, ``notifications/initialized``,
   ``ping``, ``tools/list`` and ``tools/call`` (protocol version 2024-11-05).
   Run it with ``python mcp_server.py``.  All logging goes to stderr; stdout
   carries only JSON-RPC messages.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import platform
import re
import sys
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False

import backend as be
import utils
from mcp_resources import get_resource_manager
from version import WORKBENCH_VERSION

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "qector-workbench"
_SERVER_START_TIME = time.time()

# Access the decoder module through backend
qd = be.qd

_ARRAY_SUMMARY_THRESHOLD = 200
_ARRAY_PREVIEW_LEN = 20


class MCPError(Exception):
    """Raised when an MCP tool call encounters a handled error."""



# ---------------------------------------------------------------------------

# JSON sanitizer
# ---------------------------------------------------------------------------

def _json_safe(obj: Any) -> Any:
    """Recursively convert *obj* into something ``json.dumps`` accepts.

    numpy scalars become Python numbers; small arrays become nested lists;
    arrays with more than _ARRAY_SUMMARY_THRESHOLD elements are summarized as
    {"shape", "dtype", "preview" (first _ARRAY_PREVIEW_LEN flat values),
    "summary": True}.  Anything unrecognized falls back to ``str(obj)``.
    """
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, np.ndarray):
        if obj.size > _ARRAY_SUMMARY_THRESHOLD:
            flat = obj.reshape(-1)[:_ARRAY_PREVIEW_LEN]
            return {
                "shape": [int(s) for s in obj.shape],
                "dtype": str(obj.dtype),
                "preview": [_json_safe(v) for v in flat.tolist()],
                "summary": True,
            }
        return [_json_safe(v) for v in obj.tolist()] if obj.dtype == object else obj.tolist()
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset, deque)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    if isinstance(obj, Path):
        return str(obj)
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _json_safe(dataclasses.asdict(obj))
    return str(obj)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class _ToolRegistry:
    """Registry of MCP tool definitions and their handlers."""

    def __init__(self):
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, name: str, description: str, parameters: dict, handler: Callable[..., Any]) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }
        self._handlers[name] = handler

    @property
    def tools(self) -> dict[str, dict]:
        return self._tools

    def execute(self, name: str, params: Optional[dict[str, Any]], timeout: float = 60.0) -> Any:
        handler = self._handlers.get(name)
        if handler is None:
            raise MCPError(f"unknown tool: {name!r}")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise MCPError(f"tool parameters must be an object, got {type(params).__name__}")
        # Per-tool timeout to prevent hung requests from blocking the server.
        # A persistent executor is reused across calls (one ThreadPoolExecutor
        # per call leaks threads). On timeout we do NOT wait for the hung
        # handler: the future is left running in the background (a running
        # Python thread cannot be force-killed) and the caller gets an error.
        import concurrent.futures
        executor = _get_tool_executor()
        future = executor.submit(handler, **params)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()  # best-effort: only works if not yet started
            _log(f"tool {name!r} timed out after {timeout}s; handler left running in background")
            raise MCPError(f"tool {name!r} timed out after {timeout}s") from None
        except MCPError:
            raise
        except TypeError as e:
            # Unknown / missing keyword arguments end up here.
            raise MCPError(f"invalid parameters for {name}: {e}") from e
        return _json_safe(result)


_server_instance: Optional["MCPServer"] = None
_server_lock = threading.Lock()
_registry: Optional[_ToolRegistry] = None

# Persistent executor for tool handlers (see _ToolRegistry.execute).
_tool_executor: Optional[Any] = None
_tool_executor_lock = threading.Lock()


def _get_tool_executor():
    """Return the shared tool-handler executor, creating it on first use."""
    global _tool_executor
    with _tool_executor_lock:
        if _tool_executor is None:
            import concurrent.futures
            _tool_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="qector-mcp-tool"
            )
        return _tool_executor


def _require_registry() -> "_ToolRegistry":
    """Return the tool registry, building it on first use. Never None."""
    global _registry
    if _registry is None:
        _build_registry()
    assert _registry is not None  # nosec B101 - invariant established by _build_registry
    return _registry


def _get_default_config() -> dict[str, Any]:
    return {
        "theme_mode": "dark",
        "log_level": "INFO",
        "max_results": 100,
        "auto_update": True,
    }


_config: dict[str, Any] = {}
_clients: dict[str, dict[str, Any]] = {}
# Stored benchmark results keyed by result_id (insertion-ordered).
_results: dict[str, dict[str, Any]] = {}
# Guards _config, _clients and _results: tool handlers run on arbitrary
# executor threads, so all mutations of these dicts must hold this lock.
_state_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Parameter validation helpers
# ---------------------------------------------------------------------------

def _require_str(name: str, value: Any) -> str:
    if value is None:
        raise MCPError(f"parameter {name!r} must be a string, got None")
    value = str(value).strip()
    if not value:
        raise MCPError(f"parameter {name!r} must be a non-empty string")
    return value


def _require_int(name: str, value: Any) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError
        return int(value)
    except (TypeError, ValueError):
        raise MCPError(f"parameter {name!r} must be an integer, got {value!r}") from None


def _require_float(name: str, value: Any) -> float:
    try:
        if isinstance(value, bool):
            raise TypeError
        return float(value)
    except (TypeError, ValueError):
        raise MCPError(f"parameter {name!r} must be a number, got {value!r}") from None


def _require_decoder(decoder_name: Any) -> str:
    if decoder_name not in be.DECODER_KINDS:
        raise MCPError(
            f"unknown decoder kind {decoder_name!r}; valid kinds: {', '.join(be.DECODER_KINDS)}"
        )
    return decoder_name


def _build_code(family: str, distance: int):
    try:
        return be.build_code(family, distance)
    except be.QectorError as e:
        raise MCPError(str(e)) from e


# ---------------------------------------------------------------------------
# Decoder catalogue metadata (tool-level enrichment over backend.get_decoder_info)
# ---------------------------------------------------------------------------

# Per-kind traits: ``symbol`` is the qector_decoder_v3 class the backend factory
# needs (probed for honest availability reporting), ``graphlike_only`` flags
# decoders requiring a hyperedge-free (graphlike) code, and ``compatibility`` is
# a human-readable note.  The live per-code probe (compatible_decoders tool)
# remains the authoritative compatibility answer.
_DECODER_TRAITS: dict[str, dict[str, Any]] = {
    "union_find": {"symbol": "UnionFindDecoder", "graphlike_only": True,
                   "compatibility": "Graphlike codes only — rejects hyperedge (weight>2) "
                                    "checks, so qLDPC families are unsupported."},
    "fast_union_find": {"symbol": "FastUnionFindDecoder", "graphlike_only": True,
                        "compatibility": "Graphlike codes only — rejects hyperedge checks, "
                                         "so qLDPC families are unsupported."},
    "blossom": {"symbol": "BlossomDecoder", "graphlike_only": False,
                "compatibility": "Exact MWPM; designed for graphlike codes but constructs broadly."},
    "sparse_blossom": {"symbol": "SparseBlossomDecoder", "graphlike_only": False,
                       "compatibility": "Region-growing near-MWPM; designed for graphlike codes."},
    "bp_osd": {"symbol": "BPOSDDecoder", "graphlike_only": False,
               "compatibility": "LDPC / qLDPC capable — the reference decoder for "
                                "non-graphlike codes."},
    "auto": {"symbol": "AutoDecoder", "graphlike_only": False,
             "compatibility": "Self-selecting backend; matching-based, so qLDPC codes can "
                              "fail at decode time."},
    "hybrid": {"symbol": "HybridDecoder", "graphlike_only": False,
               "compatibility": "GNN + SparseBlossom hybrid; graphlike-leaning."},
    "lookup_table": {"symbol": "LookupTableDecoder", "graphlike_only": False,
                     "compatibility": "Exact for small codes; refused above the backend's "
                                      "2**n_checks table-size guard."},
    "predecoded": {"symbol": "PredecodedDecoder", "graphlike_only": True,
                   "compatibility": "Local-matching predecoder + matching residual decoder; "
                                    "graphlike codes."},
    "auto_router": {"symbol": "AutoRouter", "graphlike_only": False,
                    "compatibility": "Policy router — dispatches the best concrete decoder per "
                                     "code; universally applicable."},
    "hybrid_cascade": {"symbol": "HybridCascadeDecoder", "graphlike_only": False,
                       "compatibility": "Union-Find pre-filter + Blossom/BP-OSD escalation; "
                                        "graphlike-oriented, escalation covers hard cases."},
    "belief_matching": {"symbol": "BeliefMatching", "graphlike_only": False,
                        "compatibility": "BP posteriors + exact MWPM with a plain-MWPM "
                                         "faithfulness fallback; broad applicability."},
    "gnn_belief_matching": {"symbol": "GNNBeliefMatcher", "graphlike_only": True,
                            "compatibility": "GNN-guided weighted MWPM with a "
                                             "syndrome-faithfulness guard; graphlike codes."},
}

# Fallback descriptions for decoder kinds a stale backend.get_decoder_info might
# not know yet (defensive while backend.py evolves in parallel).
_DECODER_DESCRIPTIONS_FALLBACK: dict[str, str] = {
    "hybrid_cascade": "Hybrid Cascade — Union-Find pre-filter + Blossom/BP-OSD escalation "
                      "with live prefilter/escalation statistics.",
    "gnn_belief_matching": "GNN Belief Matching — GNN-predicted per-qubit weights guide a "
                           "weighted matching decode with a syndrome-faithfulness guard "
                           "(research).",
    "belief_matching": "Belief Matching — sum-product BP posteriors reweight an exact "
                       "Blossom matching step (Higgott et al. 2023), with a plain-MWPM "
                       "faithfulness fallback.",
}


def _decoder_symbol_available(kind: str) -> bool:
    """Honest package-level availability probe for a decoder kind.

    Checks the qector_decoder_v3 symbol the backend factory needs.  Kinds
    without a known symbol report True (nothing to probe); a missing symbol
    means the installed wheel predates the decoder and calls fail with a clear
    error instead of a crash.
    """
    symbol = _DECODER_TRAITS.get(kind, {}).get("symbol")
    if not symbol:
        return True
    qd = getattr(be, "qd", None)
    if qd is None:
        return True
    if getattr(qd, symbol, None) is not None:
        return True
    if symbol == "GNNBeliefMatcher":
        # Some wheels also expose it via the belief_matching submodule.
        bm_mod = getattr(qd, "belief_matching", None)
        return bool(bm_mod is not None and getattr(bm_mod, symbol, None) is not None)
    return False


def _decoder_catalog_entry(kind: str) -> dict[str, Any]:
    """One rich decoder-catalogue entry shared by list_decoders/get_decoder_info."""
    try:
        description = be.get_decoder_info(kind).get("description") or "Unknown decoder"
    except Exception:
        description = "Unknown decoder"
    if description == "Unknown decoder" and kind in _DECODER_DESCRIPTIONS_FALLBACK:
        description = _DECODER_DESCRIPTIONS_FALLBACK[kind]
    traits = _DECODER_TRAITS.get(kind, {})
    available = _decoder_symbol_available(kind)
    entry: dict[str, Any] = {
        "name": kind,
        "description": description,
        "available": available,
        "graphlike_only": bool(traits.get("graphlike_only", False)),
    }
    if traits.get("compatibility"):
        entry["compatibility"] = traits["compatibility"]
    if not available:
        entry["notes"] = (
            "decoder symbol not present in the installed qector_decoder_v3 build "
            f"({be.PACKAGE_VERSION}); the wheel predates this decoder — calls report a "
            "clear unavailable status instead of crashing"
        )
    return entry


# ---------------------------------------------------------------------------
# decoder_options contract (backend.make_decoder/run_single_decode)
# ---------------------------------------------------------------------------

_DECODER_OPTION_KEYS = (
    "bp_method", "osd_order", "error_rate", "escalation", "max_accept_weight",
    "gnn_hidden_size", "gnn_n_layers",
    "x_decoder", "z_decoder", "ambig_threshold", "max_cluster_size", "max_iter",
)


def _validate_decoder_options(raw: Any) -> Optional[dict]:
    """Validate a ``decoder_options`` object against the backend contract.

    Accepted keys: ``bp_method`` ("exact"|"min_sum"), ``osd_order`` (0|1|2),
    ``error_rate`` (float), ``escalation`` ("blossom"|"bposd"),
    ``max_accept_weight`` (int), plus hybrid GNN overrides
    ``gnn_hidden_size``/``gnn_n_layers`` and v0.7.0 options ``x_decoder``/
    ``z_decoder``/``ambig_threshold``/``max_cluster_size``/``max_iter``.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise MCPError("parameter 'decoder_options' must be an object")
    unknown = sorted(str(k) for k in raw if k not in _DECODER_OPTION_KEYS)
    if unknown:
        raise MCPError(
            f"unknown decoder_options key(s): {', '.join(unknown)}; "
            f"accepted: {', '.join(_DECODER_OPTION_KEYS)}"
        )
    opts: dict[str, Any] = {}
    if raw.get("bp_method") is not None:
        bp = str(raw["bp_method"])
        if bp not in ("exact", "min_sum"):
            raise MCPError(f"decoder_options.bp_method must be 'exact' or 'min_sum', got {bp!r}")
        opts["bp_method"] = bp
    if raw.get("osd_order") is not None:
        osd = _require_int("decoder_options.osd_order", raw["osd_order"])
        if osd not in (0, 1, 2):
            raise MCPError(f"decoder_options.osd_order must be 0, 1 or 2, got {osd}")
        opts["osd_order"] = osd
    if raw.get("error_rate") is not None:
        opts["error_rate"] = _require_float("decoder_options.error_rate", raw["error_rate"])
    if raw.get("escalation") is not None:
        esc = str(raw["escalation"])
        if esc not in ("blossom", "bposd"):
            raise MCPError(f"decoder_options.escalation must be 'blossom' or 'bposd', got {esc!r}")
        opts["escalation"] = esc
    if raw.get("max_accept_weight") is not None:
        opts["max_accept_weight"] = _require_int(
            "decoder_options.max_accept_weight", raw["max_accept_weight"])
    if raw.get("gnn_hidden_size") is not None:
        opts["gnn_hidden_size"] = _require_int(
            "decoder_options.gnn_hidden_size", raw["gnn_hidden_size"])
    if raw.get("gnn_n_layers") is not None:
        opts["gnn_n_layers"] = _require_int(
            "decoder_options.gnn_n_layers", raw["gnn_n_layers"])
    if raw.get("x_decoder") is not None:
        opts["x_decoder"] = str(raw["x_decoder"])
    if raw.get("z_decoder") is not None:
        opts["z_decoder"] = str(raw["z_decoder"])
    if raw.get("ambig_threshold") is not None:
        opts["ambig_threshold"] = _require_float(
            "decoder_options.ambig_threshold", raw["ambig_threshold"])
    if raw.get("max_cluster_size") is not None:
        opts["max_cluster_size"] = _require_int(
            "decoder_options.max_cluster_size", raw["max_cluster_size"])
    if raw.get("max_iter") is not None:
        opts["max_iter"] = _require_int(
            "decoder_options.max_iter", raw["max_iter"])
    return opts


def _make_decoder_with_options(code, kind: str, options: Optional[dict]):
    """backend.make_decoder with decoder_options when the backend supports it.

    Returns ``(decoder, options_applied)``.  A TypeError from the call with
    options (backend pre-dating the ``decoder_options`` parameter) retries
    without options and reports them as not applied.
    """
    if options:
        try:
            return be.make_decoder(code, kind, decoder_options=options), True
        except TypeError:
            pass
    return be.make_decoder(code, kind), not options


def _run_single_decode_with_options(code, error_rate: float, kind: str, seed: int,
                                    options: Optional[dict]):
    """backend.run_single_decode with decoder_options when supported.

    Same ``(result, options_applied)`` contract as
    :func:`_make_decoder_with_options`.
    """
    if options:
        try:
            return (
                be.run_single_decode(code, error_rate, kind, seed, decoder_options=options),
                True,
            )
        except TypeError:
            pass
    return be.run_single_decode(code, error_rate, kind, seed), not options


def _validate_syndrome(raw: Any, n_checks: int) -> np.ndarray:
    """Validate an explicit 0/1 syndrome array against the code's check count."""
    if not isinstance(raw, (list, tuple)):
        raise MCPError("parameter 'syndrome' must be an array of 0/1 integers")
    bits: list[int] = []
    for v in raw:
        iv = _require_int("syndrome element", v)
        if iv not in (0, 1):
            raise MCPError(f"syndrome elements must be 0 or 1, got {iv}")
        bits.append(iv)
    if len(bits) != n_checks:
        raise MCPError(f"syndrome length {len(bits)} != n_checks {n_checks} for this code")
    return np.asarray(bits, dtype=np.uint8)


def _logical_failure_or_none(code, error, correction) -> Optional[bool]:
    """Logical-failure test; None when no sampled error or no usable logicals."""
    if error is None:
        return None
    try:
        logicals = be.logicals_matrix(code)
        if logicals is None:
            return None
        return bool(be.logical_failure(logicals, error, correction))
    except Exception:
        return None


def _gpu_availability() -> dict[str, bool]:
    """Honest compute-backend availability via the package's own probes.

    cpu is always available; cuda/opencl report exactly what
    ``qector_decoder_v3.cuda_is_available()`` / ``opencl_is_available()``
    return (False on any probe error) — no silent fallback, no faking.
    """
    qd = getattr(be, "qd", None)
    out = {"cpu": True, "cuda": False, "opencl": False}
    for name in ("cuda", "opencl"):
        probe = getattr(qd, f"{name}_is_available", None) if qd is not None else None
        if probe is None:
            continue
        try:
            out[name] = bool(probe())
        except Exception:
            out[name] = False
    return out


# ---------------------------------------------------------------------------
# Tool handlers (alphabetical)
# ---------------------------------------------------------------------------

def _handle_analyze_code_family(family_name: str = "rotated_surface", distance: int = 5) -> dict:
    distance = _require_int("distance", distance)
    try:
        info = be.get_code_family_info(family_name)
        code = be.build_code(family_name, distance)
        summary = be.code_summary(code)
        return {"family": info, "example": summary, "distance": distance, "status": "ok"}
    except be.QectorError as e:
        raise MCPError(str(e)) from e


def _handle_batch_decode(family: str = "rotated_surface", distance: int = 5,
                         backend: str = "cpu", n_samples: int = 100,
                         error_rate: float = 0.05, seed: int = 1) -> dict:
    distance = _require_int("distance", distance)
    n_samples = _require_int("n_samples", n_samples)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        result = be.run_batch_decode(code, str(backend), n_samples, error_rate, seed)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    result["error_rate"] = error_rate
    result["seed"] = seed
    return result


def _handle_batch_decode_gpu(family: str = "rotated_surface", distance: int = 3,
                             backend: str = "cuda", n_samples: int = 32,
                             error_rate: float = 0.05, seed: int = 1) -> dict:
    distance = _require_int("distance", distance)
    n_samples = _require_int("n_samples", n_samples)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    backend = str(backend)
    if backend not in ("cpu", "cuda", "opencl"):
        raise MCPError(f"unknown backend {backend!r}; valid backends: cpu, cuda, opencl")
    availability = _gpu_availability()
    base: dict[str, Any] = {
        "family": family, "distance": distance, "backend": backend,
        "availability": availability, "gpu_accelerated": backend != "cpu",
        "n_samples": n_samples, "error_rate": error_rate, "seed": seed,
    }
    if not availability.get(backend, False):
        return dict(base, status="unavailable",
                    reason=f"{backend} backend is not available on this machine "
                           "(qector_decoder_v3 availability probe returned False); no "
                           "results were computed or faked — pick a reported-available backend")
    code = _build_code(family, distance)
    try:
        result = be.run_batch_decode(code, backend, n_samples, error_rate, seed)
    except be.QectorError as e:
        return dict(base, status="error", reason=str(e))
    result.update(base)
    result["status"] = "ok"
    return result


def _handle_belief_match_decode(family: str = "rotated_surface", distance: int = 5,
                                error_rate: float = 0.05, seed: int = 42) -> dict:
    """Convenience seeded decode pinned to the ``belief_matching`` kind.

    BP posteriors reweight an exact Blossom matching step with a plain-MWPM
    faithfulness fallback (see ``_DECODER_TRAITS``).  Same result contract as
    :func:`_handle_decode_with_options`.
    """
    return _handle_decode_with_options(
        family=family, distance=distance, decoder_name="belief_matching",
        error_rate=error_rate, seed=seed)


def _handle_benchmark_decoder(decoder_name: str = "union_find", code_family: str = "rotated_surface",
                              distance: int = 5, error_rate: float = 0.05,
                              n_samples: int = 100, seed: int = 42) -> dict:
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    error_rate = _require_float("error_rate", error_rate)
    n_samples = _require_int("n_samples", n_samples)
    seed = _require_int("seed", seed)
    code = _build_code(code_family, distance)
    try:
        result = be.run_benchmark(
            code,
            n_samples=n_samples,
            seed=seed,
            decoder_kind=decoder_name,
            error_rate=error_rate,
        )
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["code_family"] = code_family
    result["distance"] = distance
    return result


def _handle_clear_results(confirm: bool = False) -> dict:
    if not confirm:
        raise MCPError("confirmation required: set confirm=True to clear all results")
    with _state_lock:
        n = len(_results)
        _results.clear()
    return {"cleared": True, "removed": n, "status": "ok"}


def _handle_compare_benchmarks(benchmarks: list) -> dict:
    if not isinstance(benchmarks, (list, tuple)):
        raise MCPError("parameter 'benchmarks' must be a list of result ids")
    comparison: list[dict] = []
    missing: list[str] = []
    for rid in benchmarks:
        rid = str(rid)
        with _state_lock:
            entry = _results.get(rid)
        if entry is None:
            missing.append(rid)
            continue
        comparison.append({
            "result_id": rid,
            "method": entry.get("method"),
            "code_family": entry.get("code_family"),
            "distance": entry.get("distance"),
            "p": entry.get("p"),
            "n_trials": entry.get("n_trials"),
            "throughput_decodes_per_s": entry.get("throughput_decodes_per_s"),
            "latency_p99_us": entry.get("latency_p99_us"),
            "logical_error_rate": entry.get("logical_error_rate"),
        })
    summary: dict[str, Any] = {}
    if comparison:
        by_throughput = max(comparison, key=lambda c: c["throughput_decodes_per_s"] or 0.0)
        summary["highest_throughput"] = by_throughput["result_id"]
        with_ler = [c for c in comparison if c["logical_error_rate"] is not None]
        if with_ler:
            summary["lowest_logical_error_rate"] = min(
                with_ler, key=lambda c: c["logical_error_rate"]
            )["result_id"]
    return {"comparison": comparison, "missing": missing, "count": len(comparison),
            "summary": summary}


def _handle_compatible_decoders(family: str = "rotated_surface", distance: int = 3) -> dict:
    distance = _require_int("distance", distance)
    code = _build_code(family, distance)
    try:
        kinds = be.compatible_decoder_kinds(code)
        infos = be.get_compatible_decoders(code)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    return {
        "family": family,
        "distance": distance,
        "compatible_kinds": list(kinds),
        "decoders": infos,
        "incompatible_kinds": [k for k in be.DECODER_KINDS if k not in kinds],
        "count": len(kinds),
        "total_kinds": len(be.DECODER_KINDS),
        "note": "live probe: construction plus a verified decode per kind; never raises",
        "status": "ok",
    }


def _handle_decode_single(family: str = "rotated_surface", distance: int = 5,
                          decoder_name: str = "union_find", error_rate: float = 0.05,
                          seed: int = 42) -> dict:
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        out = be.run_single_decode(code, error_rate, decoder_name, seed)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    res = out["result"]
    error = np.asarray(out["error"])
    syndrome = np.asarray(out["syndrome"])
    return {
        "family": family,
        "distance": distance,
        "decoder": decoder_name,
        "error_rate": error_rate,
        "seed": seed,
        "n_qubits": int(code.n_qubits),
        "n_checks": int(code.n_checks),
        "error_weight": int(error.sum()),
        "syndrome_weight": int(syndrome.sum()),
        "hamming_weight": res.hamming_weight,
        "syndrome_valid": res.syndrome_valid,
        "logical_failure": res.logical_failure,
        "correction": np.asarray(res.correction, dtype=np.uint8),
    }


def _handle_decode_syndrome(family: str = "rotated_surface", distance: int = 5,
                            decoder_name: str = "union_find", syndrome: Any = None,
                            decoder_options: Optional[dict] = None) -> dict:
    """Decode an explicit 0/1 syndrome with a chosen decoder.

    ``syndrome_valid`` is the GF(2) re-check of the returned correction against
    the supplied syndrome.  No reference error exists for an externally supplied
    syndrome, so ``logical_failure`` is honestly ``None`` (unknowable) — never a
    fabricated boolean.
    """
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    options = _validate_decoder_options(decoder_options)
    code = _build_code(family, distance)
    if syndrome is None:
        raise MCPError(
            "parameter 'syndrome' is required: an array of 0/1 integers whose "
            f"length equals the code's n_checks ({int(code.n_checks)} for "
            f"{family} d={distance})"
        )
    bits = _validate_syndrome(syndrome, int(code.n_checks))
    try:
        decoder, options_applied = _make_decoder_with_options(code, decoder_name, options)
        correction = np.asarray(decoder.decode(bits), dtype=np.uint8).reshape(-1)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    except Exception as e:
        raise MCPError(f"decode_syndrome failed: {e}") from e
    return {
        "family": family,
        "distance": distance,
        "decoder": decoder_name,
        "n_qubits": int(code.n_qubits),
        "n_checks": int(code.n_checks),
        "syndrome_weight": int(bits.sum()),
        "hamming_weight": int(correction.sum()),
        "syndrome_valid": bool(be.verify_correction(code, bits, correction)),
        "logical_failure": _logical_failure_or_none(code, None, correction),
        "correction": correction,
        "decoder_options": options,
        "options_applied": options_applied,
    }


def _handle_decode_with_options(family: str = "rotated_surface", distance: int = 5,
                                decoder_name: str = "bp_osd", error_rate: float = 0.05,
                                seed: int = 42, decoder_options: Optional[dict] = None) -> dict:
    """Seeded decode with validated per-decoder construction options.

    Exposes the per-decoder option contract (bp_osd ``bp_method``/``osd_order``,
    hybrid_cascade ``escalation``, GNN architecture overrides) over MCP.
    ``options_applied`` reports honestly whether the backend accepted the
    options (a pre-options backend retries without them and reports False).
    """
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    options = _validate_decoder_options(decoder_options)
    code = _build_code(family, distance)
    try:
        out, options_applied = _run_single_decode_with_options(
            code, error_rate, decoder_name, seed, options)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    res = out["result"]
    error = np.asarray(out["error"])
    syndrome = np.asarray(out["syndrome"])
    return {
        "family": family,
        "distance": distance,
        "decoder": decoder_name,
        "error_rate": error_rate,
        "seed": seed,
        "n_qubits": int(code.n_qubits),
        "n_checks": int(code.n_checks),
        "error_weight": int(error.sum()),
        "syndrome_weight": int(syndrome.sum()),
        "hamming_weight": res.hamming_weight,
        "syndrome_valid": res.syndrome_valid,
        "logical_failure": res.logical_failure,
        "correction": np.asarray(res.correction, dtype=np.uint8),
        "decoder_options": options,
        "options_applied": options_applied,
    }


def _handle_delete_resource(resource_id: str, confirm: bool = False) -> dict:
    if not confirm:
        raise MCPError("confirmation required: set confirm=True to delete resource")
    rm = get_resource_manager()
    ok = rm.delete_resource(resource_id)
    if not ok:
        raise MCPError(f"resource not found: {resource_id!r}")
    return {"deleted": True, "resource_id": resource_id}


def _handle_export_benchmark(benchmark_id: str, format: str = "json") -> dict:
    valid_formats = {"json", "csv", "markdown", "html"}
    if format not in valid_formats:
        raise MCPError(f"unsupported export format: {format!r} (supported: {', '.join(valid_formats)})")
    benchmark_id = str(benchmark_id)
    with _state_lock:
        entry = _results.get(benchmark_id)
    if entry is None:
        raise MCPError(f"unknown benchmark id {benchmark_id!r}")
    
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", benchmark_id) or "benchmark"
    ext = "md" if format == "markdown" else format
    path = utils.get_export_dir() / f"benchmark_{safe_id}.{ext}"
    
    try:
        if format == "json":
            path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        elif format == "csv":
            import csv
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Key", "Value"])
                for k, v in entry.items():
                    writer.writerow([k, json.dumps(v)])
        elif format == "markdown":
            lines = [f"# Benchmark: {benchmark_id}", "", "| Key | Value |", "|---|---|"]
            for k, v in entry.items():
                lines.append(f"| {k} | {json.dumps(v)} |")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif format == "html":
            lines = [f"<html><head><title>Benchmark {benchmark_id}</title></head><body>",
                     f"<h1>Benchmark: {benchmark_id}</h1>", "<table border='1'><tr><th>Key</th><th>Value</th></tr>"]
            for k, v in entry.items():
                import html
                lines.append(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(json.dumps(v))}</td></tr>")
            lines.append("</table></body></html>")
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            
        size = int(path.stat().st_size)
    except OSError as e:
        raise MCPError(f"export failed: {e}") from e
    return {"benchmark_id": benchmark_id, "format": format, "path": str(path.resolve()), "size": size}



def _handle_generate_documentation(family_key: str = "ring", param: int = 6,
                                   formats: Optional[list[str]] = None) -> dict:
    if formats is None:
        formats = ["json"]
    try:
        pass
    except Exception as e:
        raise MCPError(f"documentation generator unavailable: {e}") from e
    try:
        code = be.build_code(family_key, _require_int("param", param))
        import doc_generator
        def dummy_benchmark(self, code, n_trials=0, error_rate=0.0):
            return []
        doc_generator.ProfessionalDocGenerator._benchmark_decoders = dummy_benchmark
        gen = doc_generator.ProfessionalDocGenerator()
        paths_map = gen.generate_all(code, formats=list(formats))
        return {
            "family": family_key,
            "param": param,
            "formats": {fmt: str(p) for fmt, (ok, p) in paths_map.items() if ok},
            "failed_formats": [fmt for fmt, (ok, _) in paths_map.items() if not ok],
            "status": "ok",
        }
    except be.QectorError as e:
        raise MCPError(str(e)) from e


def _handle_get_code_properties(family_name: str = "ring", distance: int = 5) -> dict:
    distance = _require_int("distance", distance)
    try:
        code = be.build_code(family_name, distance)
        summary = be.code_summary(code)
        info = be.get_code_family_info(family_name)
        return {"properties": summary, "info": info, "status": "ok"}
    except be.QectorError as e:
        raise MCPError(str(e)) from e


def _handle_get_config() -> dict:
    with _state_lock:
        return dict(_get_default_config(), **_config)


def _handle_get_decoder_info(decoder_name: str = "bp_osd") -> dict:
    _require_decoder(decoder_name)
    info = be.get_decoder_info(decoder_name)
    return {"name": info["name"], "description": info["description"], "available": True}


def _handle_get_hardware_info() -> dict:
    try:
        from hardware_routing import detect_hardware
        hw = detect_hardware()
        return {"cuda": hw.cuda_rust, "gpu": hw.gpu, "opencl": hw.opencl,
                "opencl_device": hw.opencl_device}
    except Exception:
        return {"cuda": False, "gpu": None, "opencl": False, "opencl_device": None,
                "_fallback": True}


def _handle_get_resource(resource_id: str) -> dict:
    rm = get_resource_manager()
    r = rm.get_resource(resource_id)
    if r is None:
        raise MCPError(f"resource not found: {resource_id!r}")
    return r


def _handle_get_resources() -> dict:
    rm = get_resource_manager()
    resources = rm.list_resources()
    return {"resources": resources, "count": len(resources)}


def _handle_get_results(limit: int = 10) -> dict:
    limit = _require_int("limit", limit)
    if limit < 1:
        raise MCPError("parameter 'limit' must be >= 1")
    with _state_lock:
        stored = list(_results.values())
    return {"results": stored[-limit:], "total": len(stored), "limit": limit}


def _handle_get_statistics() -> dict:
    with _state_lock:
        return {"total_results": len(_results), "total_clients": len(_clients),
                "config_keys": len(_config)}


def _handle_get_system_info() -> dict:
    info = {
        "platform": platform.platform(),
        "python": sys.version,
        "hostname": platform.node(),
        "workbench_version": WORKBENCH_VERSION,
        "backend_version": be.PACKAGE_VERSION,
    }
    if _HAS_PSUTIL:
        try:
            info["cpu_percent"] = psutil.cpu_percent(interval=0.1)
            info["memory_percent"] = psutil.virtual_memory().percent
        except Exception:
            pass
    return info


def _handle_gnn_belief_match_decode(family: str = "rotated_surface", distance: int = 5,
                                    error_rate: float = 0.05, seed: int = 42,
                                    gnn_hidden_size: Optional[int] = None,
                                    gnn_n_layers: Optional[int] = None) -> dict:
    """Convenience seeded decode pinned to the ``gnn_belief_matching`` kind.

    GNN-predicted per-qubit weights guide a weighted matching with a built-in
    faithfulness fallback that keeps corrections syndrome-valid.  Optional
    ``gnn_hidden_size``/``gnn_n_layers`` overrides are threaded through the
    validated decoder-options contract.
    """
    options: dict[str, Any] = {}
    if gnn_hidden_size is not None:
        options["gnn_hidden_size"] = _require_int("gnn_hidden_size", gnn_hidden_size)
    if gnn_n_layers is not None:
        options["gnn_n_layers"] = _require_int("gnn_n_layers", gnn_n_layers)
    return _handle_decode_with_options(
        family=family, distance=distance, decoder_name="gnn_belief_matching",
        error_rate=error_rate, seed=seed, decoder_options=options or None)


def _handle_hybrid_cascade_stats(family: str = "rotated_surface", distance: int = 3,
                                 n_samples: int = 64, error_rate: float = 0.05,
                                 seed: int = 1, escalation: Optional[str] = None) -> dict:
    """Live cascade statistics from a seeded hybrid_cascade batch decode.

    Wraps ``backend.run_hybrid_cascade_stats``: Union-Find pre-filter hits,
    Blossom/BP-OSD escalations, hit rate, wall-clock throughput, the
    syndrome-match rate, and the logical error rate (flagged by
    ``logical_error_rate_kind`` when no usable logicals exist).
    """
    distance = _require_int("distance", distance)
    n_samples = _require_int("n_samples", n_samples)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    if escalation is not None:
        escalation = str(escalation)
        if escalation not in ("blossom", "bposd"):
            raise MCPError(f"escalation must be 'blossom' or 'bposd', got {escalation!r}")
    code = _build_code(family, distance)
    try:
        result = be.run_hybrid_cascade_stats(
            code, n_samples=n_samples, error_rate=error_rate, seed=seed,
            escalation=escalation)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    return result


def _handle_list_clients() -> dict:
    with _state_lock:
        clients = [{"id": cid, **data} for cid, data in _clients.items()]
        count = len(_clients)
    return {"clients": clients, "count": count}


def _handle_list_code_families() -> dict:
    families = [be.get_code_family_info(key) for key in be.CODE_FAMILIES]
    return {"families": families, "count": len(families)}


def _handle_list_decoders() -> dict:
    return {"decoders": [be.get_decoder_info(d) for d in be.DECODER_KINDS],
            "count": len(be.DECODER_KINDS)}


def _handle_list_tools() -> dict:
    reg = _require_registry()
    return {"tools": list(reg.tools.values()), "count": len(reg.tools)}


def _handle_mcp_status() -> dict:
    return {"status": "running", "uptime": time.monotonic(),
            "version": WORKBENCH_VERSION, "backend": be.PACKAGE_VERSION,
            "protocol_version": PROTOCOL_VERSION, "tools": len(_require_registry().tools)}


def _handle_neural_predecoder_train(family: str = "repetition", distance: int = 3,
                                    n_samples: int = 200, n_epochs: int = 5,
                                    error_rate: float = 0.05, seed: int = 1) -> dict:
    """Train + evaluate the NeuralPredecoder research/lab MLP.

    Wraps ``backend.run_neural_predecoder_training``: trains on ``n_samples``
    seeded (syndrome, error) pairs and evaluates on a disjoint held-out seed
    stream, reporting exact-match rate, per-bit accuracy, syndrome-validity
    rate, and the logical error rate when the code exposes usable logicals.
    The neural pre-decoder is deliberately not a wired decoder kind.
    """
    distance = _require_int("distance", distance)
    n_samples = _require_int("n_samples", n_samples)
    n_epochs = _require_int("n_epochs", n_epochs)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        result = be.run_neural_predecoder_training(
            code, n_samples=n_samples, n_epochs=n_epochs,
            error_rate=error_rate, seed=seed)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    return result


def _handle_recommend_decoder(family: str = "rotated_surface", distance: int = 5,
                              n_qubits: Optional[int] = None,
                              priority: str = "balanced") -> dict:
    try:
        from hardware_routing import recommend
    except Exception as e:
        raise MCPError(f"hardware routing unavailable: {e}") from e
    distance_v = None if distance is None else _require_int("distance", distance)
    n_qubits_v = None if n_qubits is None else _require_int("n_qubits", n_qubits)
    try:
        rec = recommend(family, distance_v, n_qubits_v, str(priority))
    except ValueError as e:
        raise MCPError(str(e)) from e
    except Exception as e:
        raise MCPError(f"recommendation failed: {e}") from e
    return dataclasses.asdict(rec)


def _handle_register_client(client_id: str, access_level: str = "USER") -> dict:
    with _state_lock:
        _clients[client_id] = {"access_level": access_level, "registered_at": time.time()}
    return {"client_id": client_id, "access_level": access_level, "status": "registered"}


def _handle_reset_config(confirm: bool = False) -> dict:
    if not confirm:
        raise MCPError("confirmation required: set confirm=True to reset config")
    with _state_lock:
        _config.clear()
    return {"reset": True, "config": _get_default_config()}


def _handle_run_benchmark(code_family: str = "rotated_surface", distance: int = 5,
                          decoder_name: str = "union_find", n_samples: int = 100,
                          seed: int = 42, error_rate: float = 0.05) -> dict:
    result = _handle_benchmark_decoder(
        decoder_name=decoder_name,
        code_family=code_family,
        distance=distance,
        error_rate=error_rate,
        n_samples=n_samples,
        seed=seed,
    )
    result_id = uuid.uuid4().hex
    entry = _json_safe(dict(result, result_id=result_id))
    with _state_lock:
        _results[result_id] = entry
    return entry


def _handle_set_config(config: dict) -> dict:
    if not isinstance(config, dict):
        raise MCPError("parameter 'config' must be an object mapping keys to values")
    with _state_lock:
        _config.update(config)
        merged = dict(_get_default_config(), **_config)
    return {"updated": sorted(str(k) for k in config), "config": merged}


def _handle_stream_decode(family: str = "rotated_surface", distance: int = 5,
                          window_size: int = 5, n_rounds: int = 10,
                          error_rate: float = 0.03, seed: int = 1,
                          decoder_name: str = "union_find") -> dict:
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    window_size = _require_int("window_size", window_size)
    n_rounds = _require_int("n_rounds", n_rounds)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        result = be.run_streaming_session(
            code,
            window_size=window_size,
            n_rounds=n_rounds,
            error_rate=error_rate,
            seed=seed,
            decoder_kind=decoder_name,
        )
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    result["decoder"] = decoder_name
    result["error_rate"] = error_rate
    result["seed"] = seed
    return result


def _handle_self_diagnostics() -> dict:
    import autodebug
    return autodebug.run_self_diagnostics().to_dict()


def _handle_version_info(refresh: bool = False) -> dict:
    """App + backend version report resolved locally (offline, no PyPI)."""
    import version_service
    return version_service.get_version_report(refresh=bool(refresh))


def _handle_check_updates(refresh: bool = False) -> dict:
    """Combined app + backend version report (local wheel only, no PyPI)."""
    import version_service
    return version_service.get_version_report(refresh=bool(refresh))


def _handle_diagnostic_decode(family: str = "rotated_surface", distance: int = 5,
                              decoder_name: str = "blossom", error_rate: float = 0.05,
                              seed: int = 42) -> dict:
    _require_decoder(decoder_name)
    distance = _require_int("distance", distance)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        result = be.run_diagnostic_decode(code, error_rate, decoder_name, seed)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    return result


def _handle_native_recommend(family: str = "rotated_surface", distance: int = 5,
                             n_qubits: Optional[int] = None, priority: str = "balanced",
                             batch_size: int = 1) -> dict:
    distance_v = None if distance is None else _require_int("distance", distance)
    n_qubits_v = None if n_qubits is None else _require_int("n_qubits", n_qubits)
    batch_size = _require_int("batch_size", batch_size)
    return be.native_recommend(family, distance_v, n_qubits_v, str(priority), batch_size)


def _handle_native_streaming(family: str = "rotated_surface", distance: int = 5,
                             n_rounds: int = 8, error_rate: float = 0.03,
                             seed: int = 1, window_size: int = 4) -> dict:
    distance = _require_int("distance", distance)
    n_rounds = _require_int("n_rounds", n_rounds)
    window_size = _require_int("window_size", window_size)
    error_rate = _require_float("error_rate", error_rate)
    seed = _require_int("seed", seed)
    code = _build_code(family, distance)
    try:
        result = be.run_native_streaming(code, n_rounds=n_rounds, error_rate=error_rate,
                                         seed=seed, window_size=window_size)
    except be.QectorError as e:
        raise MCPError(str(e)) from e
    result["family"] = family
    result["distance"] = distance
    return result


def _handle_list_codes() -> dict:
    """Workbench code families plus the backend's native code catalogue."""
    return be.list_available_codes()


def _handle_compat_report() -> dict:
    """Ecosystem-integration availability (stim/sinter/pymatching/qiskit/ldpc)."""
    return be.compat_report()


def _handle_probe_decoders(family: str = "rotated_surface", distance: int = 5,
                           error_rate: float = 0.05, seed: int = 42) -> dict:
    import autodebug
    return autodebug.probe_decoders(
        family, _require_int("distance", distance),
        _require_float("error_rate", error_rate), _require_int("seed", seed))


def _handle_resilient_decode(family: str = "rotated_surface", distance: int = 5,
                             decoder_name: str = "union_find", error_rate: float = 0.05,
                             seed: int = 42) -> dict:
    _require_decoder(decoder_name)
    import autodebug
    return autodebug.resilient_single_decode(
        family, _require_int("distance", distance),
        error_rate=_require_float("error_rate", error_rate),
        decoder=decoder_name, seed=_require_int("seed", seed)).to_dict()


def _handle_sparse_blossom_radix_neighbors(
    family: str = "rotated_surface",
    distance: int = 5,
    defects: Optional[list[int]] = None,
    k: int = 8,
) -> dict:
    code = _build_code(family, _require_int("distance", distance))
    def_list = defects if defects is not None else [0, 1]
    neighbors = be.sparse_blossom_radix_neighbors(code, def_list, k=_require_int("k", k))
    return {
        "family": family,
        "distance": distance,
        "defects": def_list,
        "k": k,
        "candidate_edges": _json_safe(neighbors),
    }


def _handle_clear_decoder_cache() -> dict:
    ok = be.clear_decoder_cache()
    return {"status": "cleared" if ok else "failed", "ok": ok}


def _handle_flush_usage(customer_id: Optional[str] = None, api_key: Optional[str] = None) -> dict:
    return be.flush_usage(customer_id=customer_id, api_key=api_key)


def _handle_doctor_diagnostics() -> dict:
    return be.run_doctor_checks()


def _handle_verify_license_token(token: str) -> dict:
    return be.verify_license_token(token)


def _handle_set_license_key_file(path: str) -> dict:
    ok = be.set_license_key_file(path)
    return {"path": path, "ok": ok}


def _handle_two_stage_decode(
    family: str = "rotated_surface",
    distance: int = 5,
    x_decoder: str = "blossom",
    z_decoder: str = "blossom",
    syndrome: Optional[list[int]] = None,
    seed: int = 42,
) -> dict:
    code = _build_code(family, _require_int("distance", distance))
    if syndrome is None:
        _err, syn = be.sample_error_and_syndrome(code, 0.05, seed=_require_int("seed", seed))
    else:
        syn = np.asarray(syndrome, dtype=np.uint8)
    dec = be.make_decoder(code, "two_stage", {"x_decoder": str(x_decoder), "z_decoder": str(z_decoder)})
    corr = dec.decode(syn)
    valid = be.verify_correction(code, syn, corr)
    return {
        "family": family,
        "distance": distance,
        "syndrome_valid": valid,
        "correction": _json_safe(corr),
    }


def _handle_ambiguity_cluster_decode(
    family: str = "rotated_surface",
    distance: int = 5,
    error_rate: float = 0.05,
    ambig_threshold: float = 0.5,
    max_cluster_size: int = 12,
    syndrome: Optional[list[int]] = None,
    seed: int = 42,
) -> dict:
    code = _build_code(family, _require_int("distance", distance))
    if syndrome is None:
        _err, syn = be.sample_error_and_syndrome(code, _require_float("error_rate", error_rate), seed=_require_int("seed", seed))
    else:
        syn = np.asarray(syndrome, dtype=np.uint8)
    opts = {
        "error_rate": _require_float("error_rate", error_rate),
        "ambig_threshold": _require_float("ambig_threshold", ambig_threshold),
        "max_cluster_size": _require_int("max_cluster_size", max_cluster_size),
    }
    dec = be.make_decoder(code, "ambiguity_cluster", opts)
    corr = dec.decode(syn)
    valid = be.verify_correction(code, syn, corr)
    return {
        "family": family,
        "distance": distance,
        "syndrome_valid": valid,
        "correction": _json_safe(corr),
    }


def _handle_colour_code_decode(
    distance: int = 3,
    max_iter: int = 30,
    osd_order: int = 0,
    syndrome: Optional[list[int]] = None,
    seed: int = 42,
) -> dict:
    code = be.build_code("color_code", _require_int("distance", distance))
    if syndrome is None:
        _err, syn = be.sample_error_and_syndrome(code, 0.05, seed=_require_int("seed", seed))
    else:
        syn = np.asarray(syndrome, dtype=np.uint8)
    opts = {"max_iter": _require_int("max_iter", max_iter), "osd_order": _require_int("osd_order", osd_order)}
    dec = be.make_decoder(code, "colour_code", opts)
    corr = dec.decode(syn)
    valid = be.verify_correction(code, syn, corr)
    return {
        "family": "color_code",
        "distance": distance,
        "syndrome_valid": valid,
        "correction": _json_safe(corr),
    }


# ---------------------------------------------------------------------------
# v1.0.0 tool handlers
# ---------------------------------------------------------------------------

def _handle_parallel_batch_decode(
    family: str = "rotated_surface",
    distance: int = 5,
    decoder_name: str = "blossom",
    n_samples: int = 100,
    error_rate: float = 0.05,
    seed: int = 42,
    n_workers: int = 4,
) -> dict:
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    result = be.run_parallel_batch_decode(
        code, n_samples=n_samples, error_rate=error_rate,
        seed=seed, decoder_type=decoder_name, n_workers=n_workers
    )
    return {
        "family": family, "distance": distance, "decoder": decoder_name,
        "n_samples": n_samples, "n_workers": n_workers,
        "logical_error_rate": result.get("logical_error_rate", 0.0),
        "throughput": result.get("throughput", 0.0),
    }


def _handle_mcp_health() -> dict:
    import os
    import time
    import psutil
    process = psutil.Process(os.getpid())
    mem_info = process.memory_info()
    return {
        "server": SERVER_NAME,
        "version": WORKBENCH_VERSION,
        "backend_version": be.PACKAGE_VERSION,
        "uptime_seconds": time.time() - _SERVER_START_TIME,
        "memory_rss_mb": round(mem_info.rss / (1024 * 1024), 1),
        "tool_count": len(_require_registry().tools),
        "decoder_import_ok": be.PACKAGE_VERSION is not None,
        "pid": os.getpid(),
    }


def _handle_compare_all_decoders(
    family: str = "rotated_surface",
    distance: int = 5,
    error_rate: float = 0.05,
    n_samples: int = 50,
    seed: int = 42,
) -> dict:
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    compatible = be.compatible_decoder_kinds(code)
    results = []
    for decoder_name in compatible:
        try:
            bench = be.run_benchmark(decoder_name, code, n_samples=n_samples,
                                     error_rate=error_rate, seed=seed)
            results.append({
                "decoder": decoder_name,
                "logical_error_rate": bench.get("logical_error_rate"),
                "throughput_decodes_per_s": bench.get("throughput_decodes_per_s"),
                "p50_latency_us": bench.get("p50_latency_us"),
                "p99_latency_us": bench.get("p99_latency_us"),
            })
        except Exception as e:
            results.append({"decoder": decoder_name, "error": str(e)})
    return {
        "family": family, "distance": distance, "error_rate": error_rate,
        "n_samples": n_samples, "results": results,
    }


def _handle_compatibility_matrix() -> dict:
    matrix = {}
    for family_name in be.CODE_FAMILIES:
        try:
            code = be.build_code(family_name, 5)
            compatible = be.compatible_decoder_kinds(code)
            matrix[family_name] = {k: (k in compatible) for k in be.DECODER_KINDS}
        except Exception:
            matrix[family_name] = {k: False for k in be.DECODER_KINDS}
    return {
        "decoders": be.DECODER_KINDS,
        "code_families": list(be.CODE_FAMILIES.keys()),
        "matrix": matrix,
    }


def _handle_decode_mmap(family: str = "rotated_surface", distance: int = 5,
                        syndrome_path: str = "syndromes.npy", output_path: str = "corrections.npy",
                        decoder_name: str = "cpu_batch", batch_size: int = 65536,
                        n_shots: Optional[int] = None) -> dict:
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        qd.decode_mmap(
            syndrome_path, output_path, code.check_to_qubits, int(code.n_qubits),
            decoder_type=decoder_name, batch_size=batch_size, n_shots=n_shots, verbose=False
        )
    except Exception as e:
        raise MCPError(str(e)) from e
    return {"status": "success", "output_path": output_path}


def _handle_decoder_benchmark_suite(
    n_samples: int = 100,
    seed: int = 42,
) -> dict:
    code = be.build_code("rotated_surface", 5)
    results = []
    for decoder_name in be.DECODER_KINDS:
        try:
            compatible = be.compatible_decoder_kinds(code)
            if decoder_name not in compatible:
                continue
            bench = be.run_benchmark(decoder_name, code, n_samples=n_samples,
                                     error_rate=0.05, seed=seed)
            results.append({
                "decoder": decoder_name,
                "logical_error_rate": bench.get("logical_error_rate"),
                "throughput_decodes_per_s": bench.get("throughput_decodes_per_s"),
            })
        except Exception:
            pass
    results.sort(key=lambda x: x.get("logical_error_rate", 1.0))
    return {
        "family": "rotated_surface", "distance": 5, "error_rate": 0.05,
        "n_samples": n_samples, "ranked_results": results,
    }


def _handle_get_backend_health() -> dict:
    auto_cls = getattr(qd, "AutoDecoder", None)
    if auto_cls is None:
        return {"error": "AutoDecoder not available"}
    try:
        code = be.build_code("rotated_surface", 3)
        dec = be.make_decoder(code, "auto")
        diag = getattr(dec, "_diag", None)
        if diag is None:
            return {"status": "ok", "message": "No diagnostics available"}
        return {
            "backend_health": getattr(diag, "backend_health", {}),
            "active_backend": getattr(diag, "active_backend", "unknown"),
        }
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# v1.0.0 extra tool handlers — 3.11–3.14
# ---------------------------------------------------------------------------

def _handle_export_session(
    output_path: Optional[str] = None,
    family: str = "rotated_surface",
    distance: int = 5,
    decoder_name: str = "blossom",
    error_rate: float = 0.05,
    seed: int = 42,
) -> dict:
    """Export the complete current session to a ZIP archive."""
    try:
        result = be.export_session(
            code_family=_require_str("family", family),
            distance=_require_int("distance", distance),
            decoder_name=_require_str("decoder_name", decoder_name),
            error_rate=_require_float("error_rate", error_rate),
            seed=_require_int("seed", seed),
            output_path=_require_str("output_path", output_path or "session_export.zip"),
        )
        if isinstance(result, dict):
            return result
        return {"path": str(result), "ok": True}
    except Exception as e:
        raise MCPError(f"export_session failed: {e}") from e


def _handle_import_syndrome(
    file_path: str = "",
    family: str = "rotated_surface",
    distance: int = 5,
    decoder_name: str = "blossom",
) -> dict:
    """Load external syndrome data (CSV/JSON/.npy) and decode it."""
    if not file_path:
        raise _InvalidParams("file_path is required")
    try:
        syndrome = be.import_syndrome(file_path)
        code = be.build_code(family, _require_int("distance", distance))
        decoder = be.make_decoder(code, decoder_name)
        correction = decoder.decode(syndrome)
        valid = be.verify_correction(code, syndrome, correction)
        return {
            "family": family, "distance": distance, "decoder": decoder_name,
            "file": file_path, "syndrome_length": int(len(syndrome)),
            "syndrome_valid": bool(valid), "correction": _json_safe(correction),
        }
    except MCPError:
        raise
    except Exception as e:
        raise MCPError(f"import_syndrome failed: {e}") from e


def _handle_analyze_logicals(
    family: str = "rotated_surface",
    distance: int = 5,
) -> dict:
    """Analyze logical operators, weight distribution, and code distance."""
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    summary = be.code_summary(code)
    result: dict = {
        "family": family,
        "distance": distance,
        "n_qubits": summary.get("n_qubits"),
        "n_checks": summary.get("n_checks"),
    }
    # Try native logical operator analysis
    for attr in ("logical_xs", "logical_zs", "n_logicals", "logical_weight"):
        val = getattr(code, attr, None)
        if val is not None:
            try:
                result[attr] = _json_safe(val() if callable(val) else val)
            except Exception:
                pass
    try:
        analyze_fn = getattr(be, "analyze_logicals", None)
        if analyze_fn is not None:
            logicals = analyze_fn(code)
            result.update({k: _json_safe(v) for k, v in logicals.items()})
    except Exception:
        pass
    return result


def _handle_analyze_error_patterns(
    family: str = "rotated_surface",
    distance: int = 5,
    error_rate: float = 0.05,
    n_samples: int = 100,
    seed: int = 42,
) -> dict:
    """Analyze error patterns: weight distribution, cluster size, correlated errors."""
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    import numpy as np
    rng = np.random.default_rng(seed)
    weights = []
    for _ in range(n_samples):
        error = code.random_error(error_rate, rng=rng)
        w = int(np.sum(np.asarray(error, dtype=np.uint8)))
        weights.append(w)
    weights_arr = np.array(weights, dtype=np.int32)
    result = {
        "family": family, "distance": distance, "error_rate": error_rate,
        "n_samples": n_samples,
        "mean_weight": float(np.mean(weights_arr)),
        "max_weight": int(np.max(weights_arr)),
        "min_weight": int(np.min(weights_arr)),
        "std_weight": float(np.std(weights_arr)),
        "weight_histogram": {str(int(k)): int(v)
                             for k, v in zip(*np.unique(weights_arr, return_counts=True))},
    }
    try:
        analyze_fn = getattr(be, "analyze_error_patterns", None)
        if analyze_fn is not None:
            extra = analyze_fn(code, error_rate=error_rate, n_samples=n_samples, seed=seed)
            result.update({k: _json_safe(v) for k, v in extra.items()})
    except Exception:
        pass
    return result


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class MCPServer:
    """MCP Server providing all QECTOR tools."""

    def __init__(self):
        self.tools = _registry

    def describe(self) -> dict:
        return {"name": SERVER_NAME, "version": WORKBENCH_VERSION,
                "backend_version": be.PACKAGE_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "tools": len(_require_registry().tools)}


def get_mcp_server() -> MCPServer:
    """Get or create the singleton MCP server instance."""
    global _server_instance
    if _server_instance is None:
        with _server_lock:
            if _server_instance is None:
                _build_registry()
                _server_instance = MCPServer()
    return _server_instance


async def call_mcp_tool(name: str, arguments: dict) -> dict:
    """Call an MCP tool by name with the given arguments.

    This is the public API used by test_mcp_all.py and other callers.
    """
    server = get_mcp_server()
    return server.tools.execute(name, arguments)


def _build_registry() -> None:
    global _registry
    _registry = _ToolRegistry()
    _decoder_desc = "One of: " + ", ".join(be.DECODER_KINDS)
    _family_desc = "One of: " + ", ".join(be.CODE_FAMILIES)
    _options_desc = ("Optional per-decoder construction options: bp_method (exact|min_sum|relay), "
                     "osd_order (0|1|2), osd_lambda, damping, error_rate, "
                     "escalation (blossom|bposd), max_accept_weight, "
                     "gnn_hidden_size, gnn_n_layers")
    # Publish to module scope so :func:`_register_v1_tools` (defined below
    # the v1.0.0 handler block) can use the same human-readable descriptions.
    global _DECODER_DESC, _FAMILY_DESC, _OPTIONS_DESC
    _DECODER_DESC = _decoder_desc
    _FAMILY_DESC = _family_desc
    _OPTIONS_DESC = _options_desc

    _registry.register(
        "analyze_code_family", "Analyze a code family with an example code instance",
        {"family_name": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5}},
        _handle_analyze_code_family)
    _registry.register(
        "batch_decode", "Batch-decode sampled syndromes on cpu/cuda/opencl via backend.run_batch_decode",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "backend": {"type": "string", "default": "cpu",
                     "description": "One of: cpu, cuda, opencl (no silent fallback)"},
         "n_samples": {"type": "integer", "default": 100},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 1}},
        _handle_batch_decode)
    _registry.register(
        "batch_decode_gpu", "Batch-decode on an explicit compute backend (cpu/cuda/opencl) with "
                            "honest availability reporting — unavailable GPU backends return "
                            "status='unavailable' with a reason, never fake results",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3},
         "backend": {"type": "string", "default": "cuda",
                     "description": "One of: cpu, cuda, opencl"},
         "n_samples": {"type": "integer", "default": 32},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 1}},
        _handle_batch_decode_gpu)
    _registry.register(
        "belief_match_decode", "Convenience seeded decode pinned to the belief_matching "
                               "kind (BP posteriors + exact MWPM with faithfulness fallback)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_belief_match_decode)
    _registry.register(
        "benchmark_decoder", "Benchmark a decoder on a code family via backend.run_benchmark "
                             "(latency percentiles, throughput, logical error rate)",
        {"decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC},
         "code_family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "n_samples": {"type": "integer", "default": 100},
         "seed": {"type": "integer", "default": 42}},
        _handle_benchmark_decoder)
    _registry.register(
        "clear_results", "Clear all stored benchmark results",
        {"confirm": {"type": "boolean", "default": False}},
        _handle_clear_results)
    _registry.register(
        "compare_benchmarks", "Compare stored benchmark results side by side "
                              "(throughput, p99 latency, logical error rate)",
        {"benchmarks": {"type": "array", "items": {"type": "string"},
                        "description": "result_id values returned by the run_benchmark tool"}},
        _handle_compare_benchmarks)
    _registry.register(
        "compatible_decoders", "Live probe: which decoder kinds construct and produce a "
                               "syndrome-verified correction on this code",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3}},
        _handle_compatible_decoders)
    _registry.register(
        "decode_single", "Run one seeded decode and report correction weight, syndrome validity "
                         "and logical failure",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_decode_single)
    _registry.register(
        "decode_syndrome", "Decode an explicit 0/1 syndrome (length n_checks) with a chosen "
                           "decoder; syndrome_valid is the GF(2) re-check, logical_failure is "
                           "null (no reference error exists, so it is unknowable)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC},
         "syndrome": {"type": "array", "items": {"type": "integer"},
                      "description": "0/1 syndrome bits, length must equal the code's n_checks"},
         "decoder_options": {"type": ["object", "null"], "default": None,
                             "description": _OPTIONS_DESC}},
        _handle_decode_syndrome)
    _registry.register(
        "decode_with_options", "Seeded decode with validated per-decoder construction options "
                               "(bp_osd bp_method/osd_order, hybrid_cascade escalation, GNN "
                               "architecture); reports options_applied honestly",
        {"family": {"type": "string", "default": "repetition", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3},
         "decoder_name": {"type": "string", "default": "bp_osd", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42},
         "decoder_options": {"type": ["object", "null"], "default": None,
                             "description": _OPTIONS_DESC}},
        _handle_decode_with_options)
    _registry.register(
        "delete_resource", "Delete a resource by ID",
        {"resource_id": {"type": "string"},
         "confirm": {"type": "boolean", "default": False}},
        _handle_delete_resource)
    _registry.register(
        "export_benchmark", "Export a stored benchmark result (by result_id) to the export directory",
        {"benchmark_id": {"type": "string",
                          "description": "result_id returned by the run_benchmark tool"},
         "format": {"type": "string", "default": "json"}},
        _handle_export_benchmark)
    _registry.register(
        "generate_documentation", "Generate code documentation files",
        {"family_key": {"type": "string", "default": "ring", "description": _FAMILY_DESC},
         "param": {"type": "integer", "default": 6},
         "formats": {"type": "array", "default": ["json"],
                     "description": "Any of: json, markdown, html, latex, pdf"}},
        _handle_generate_documentation)
    _registry.register(
        "get_code_properties", "Get properties of a code family",
        {"family_name": {"type": "string", "default": "ring", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5}},
        _handle_get_code_properties)
    _registry.register(
        "get_config", "Get current server configuration", {},
        _handle_get_config)
    _registry.register(
        "get_decoder_info", "Get information about a decoder",
        {"decoder_name": {"type": "string", "default": "bp_osd", "description": _DECODER_DESC}},
        _handle_get_decoder_info)
    _registry.register(
        "get_hardware_info", "Get hardware/backend availability", {},
        _handle_get_hardware_info)
    _registry.register(
        "get_resource", "Get a specific resource by ID",
        {"resource_id": {"type": "string"}},
        _handle_get_resource)
    _registry.register(
        "get_resources", "List all resources", {},
        _handle_get_resources)
    _registry.register(
        "get_results", "Get stored benchmark results (most recent first-in order)",
        {"limit": {"type": "integer", "default": 10}},
        _handle_get_results)
    _registry.register(
        "get_statistics", "Get server statistics", {},
        _handle_get_statistics)
    _registry.register(
        "get_system_info", "Get system information", {},
        _handle_get_system_info)
    _registry.register(
        "gnn_belief_match_decode", "Convenience seeded decode pinned to the "
                                   "gnn_belief_matching kind with optional GNN architecture "
                                   "overrides (gnn_hidden_size, gnn_n_layers)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42},
         "gnn_hidden_size": {"type": ["integer", "null"], "default": None},
         "gnn_n_layers": {"type": ["integer", "null"], "default": None}},
        _handle_gnn_belief_match_decode)
    _registry.register(
        "hybrid_cascade_stats", "Batch-decode through the hybrid_cascade decoder and expose its "
                                "live cascade statistics (prefilter_hits, escalations, hit rate, "
                                "throughput, syndrome-match rate, logical error rate)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3},
         "n_samples": {"type": "integer", "default": 64},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 1},
         "escalation": {"type": ["string", "null"], "default": None,
                        "description": "One of: blossom, bposd (default: backend's blossom)"}},
        _handle_hybrid_cascade_stats)
    _registry.register(
        "list_clients", "List registered clients", {},
        _handle_list_clients)
    _registry.register(
        "list_code_families", "List available code families", {},
        _handle_list_code_families)
    _registry.register(
        "list_decoders", "List available decoders", {},
        _handle_list_decoders)
    _registry.register(
        "list_tools", "List all available MCP tools", {},
        _handle_list_tools)
    _registry.register(
        "mcp_status", "Get MCP server status", {},
        _handle_mcp_status)
    _registry.register(
        "neural_predecoder_train", "Train the NeuralPredecoder research/lab MLP on seeded "
                                   "(syndrome, error) pairs and evaluate on a disjoint held-out "
                                   "stream (exact-match, bit accuracy, syndrome validity, LER)",
        {"family": {"type": "string", "default": "repetition", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3},
         "n_samples": {"type": "integer", "default": 200},
         "n_epochs": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 1}},
        _handle_neural_predecoder_train)
    _registry.register(
        "recommend_decoder", "Recommend a decoder for a code/priority using detected hardware",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "n_qubits": {"type": ["integer", "null"], "default": None},
         "priority": {"type": "string", "default": "balanced",
                      "description": "One of: balanced, speed, accuracy"}},
        _handle_recommend_decoder)
    _registry.register(
        "register_client", "Register a client",
        {"client_id": {"type": "string"},
         "access_level": {"type": "string", "default": "USER"}},
        _handle_register_client)
    _registry.register(
        "reset_config", "Reset configuration to defaults",
        {"confirm": {"type": "boolean", "default": False}},
        _handle_reset_config)
    _registry.register(
        "run_benchmark", "Run a benchmark and store the result under a generated result_id",
        {"code_family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC},
         "n_samples": {"type": "integer", "default": 100},
         "seed": {"type": "integer", "default": 42},
         "error_rate": {"type": "number", "default": 0.05}},
        _handle_run_benchmark)
    _registry.register(
        "set_config", "Merge key/value pairs into the server configuration",
        {"config": {"type": "object",
                    "description": "Key/value pairs merged into the current configuration"}},
        _handle_set_config)
    _registry.register(
        "stream_decode", "Run a sliding-window streaming decode session via "
                         "backend.run_streaming_session",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "window_size": {"type": "integer", "default": 5},
         "n_rounds": {"type": "integer", "default": 10},
         "error_rate": {"type": "number", "default": 0.03},
         "seed": {"type": "integer", "default": 1},
         "decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC}},
        _handle_stream_decode)
    _registry.register(
        "probe_decoders", "Probe which decoders produce a valid (syndrome-verified) correction "
                          "for a code — a self-test across every wired decoder",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_probe_decoders)
    _registry.register(
        "resilient_decode", "Single decode with automatic multi-decoder fallback and a full "
                            "attempt trace (autodebug.resilient_single_decode)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "union_find", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_resilient_decode)
    _registry.register(
        "self_diagnostics", "Run a full environment/decoder/hardware self-diagnostics report "
                            "(autodebug.run_self_diagnostics)",
        {},
        _handle_self_diagnostics)
    _registry.register(
        "version_info", "App + decoder-backend version report (workbench baseline + installed "
                        "backend, resolved locally — no network)",
        {"refresh": {"type": "boolean", "default": False,
                     "description": "Accepted for compatibility; resolution is always local"}},
        _handle_version_info)
    _registry.register(
        "check_updates", "Report whether the installed decoder backend matches the bundled "
                         "release baseline (offline — no update service)",
        {"refresh": {"type": "boolean", "default": False,
                     "description": "Accepted for compatibility; resolution is always local"}},
        _handle_check_updates)
    _registry.register(
        "diagnostic_decode", "Rich single decode via the backend's native decode_with_diagnostics "
                             "(matched weight, backend used, internal fallback, timing, logicals)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_diagnostic_decode)
    _registry.register(
        "native_recommend", "Backend-native decoder recommendation (qector_decoder_v3.recommend) "
                            "with the mapped workbench decoder_kind",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "n_qubits": {"type": ["integer", "null"], "default": None},
         "priority": {"type": "string", "default": "balanced",
                      "description": "One of: balanced, speed, accuracy"},
         "batch_size": {"type": "integer", "default": 1}},
        _handle_native_recommend)
    _registry.register(
        "native_streaming", "Native hardware-accelerated sliding-window streaming decode "
                            "(qector_decoder_v3.sliding_window_decode) with per-round validity + telemetry",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "n_rounds": {"type": "integer", "default": 8},
         "error_rate": {"type": "number", "default": 0.03},
         "seed": {"type": "integer", "default": 1},
         "window_size": {"type": "integer", "default": 4}},
        _handle_native_streaming)
    _registry.register(
        "list_codes", "List workbench code families plus the backend's native code catalogue",
        {},
        _handle_list_codes)
    _registry.register(
        "compat_report", "Report ecosystem-integration availability "
                         "(stim/sinter/pymatching/qiskit/ldpc) and research components",
        {},
        _handle_compat_report)
    _registry.register(
        "sparse_blossom_radix_neighbors", "Discover k-nearest candidate edges via SparseBlossom RadixHeap",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "defects": {"type": ["array", "null"], "default": None, "items": {"type": "integer"}},
         "k": {"type": "integer", "default": 8}},
        _handle_sparse_blossom_radix_neighbors)
    _registry.register(
        "clear_decoder_cache", "Clear the backend's native decoder cache",
        {},
        _handle_clear_decoder_cache)
    _registry.register(
        "flush_usage", "Flush usage metrics to Stripe metered billing API",
        {"customer_id": {"type": ["string", "null"], "default": None},
         "api_key": {"type": ["string", "null"], "default": None}},
        _handle_flush_usage)
    _registry.register(
        "doctor_diagnostics", "Run system health and environment diagnostic checks via qd.doctor",
        {},
        _handle_doctor_diagnostics)
    _registry.register(
        "verify_license_token", "Verify an Ed25519 signed license token string",
        {"token": {"type": "string"}},
        _handle_verify_license_token)
    _registry.register(
        "set_license_key_file", "Set license key file path for offline verification",
        {"path": {"type": "string"}},
        _handle_set_license_key_file)
    _registry.register(
        "two_stage_decode", "Decode using TwoStageDecoder (decoupled X/Z sector decoders)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "x_decoder": {"type": "string", "default": "blossom"},
         "z_decoder": {"type": "string", "default": "blossom"},
         "syndrome": {"type": ["array", "null"], "default": None, "items": {"type": "integer"}},
         "seed": {"type": "integer", "default": 42}},
        _handle_two_stage_decode)
    _registry.register(
        "ambiguity_cluster_decode", "Decode using AmbiguityClusterDecoder for high noise or non-graphlike codes",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "ambig_threshold": {"type": "number", "default": 0.5},
         "max_cluster_size": {"type": "integer", "default": 12},
         "syndrome": {"type": ["array", "null"], "default": None, "items": {"type": "integer"}},
         "seed": {"type": "integer", "default": 42}},
        _handle_ambiguity_cluster_decode)
    _registry.register(
        "colour_code_decode", "Decode color code using BP-OSD over undecomposed detector error model",
        {"distance": {"type": "integer", "default": 3},
         "max_iter": {"type": "integer", "default": 30},
         "osd_order": {"type": "integer", "default": 0},
         "syndrome": {"type": ["array", "null"], "default": None, "items": {"type": "integer"}},
         "seed": {"type": "integer", "default": 42}},
        _handle_colour_code_decode)
    # v1.0.0 additions
    _registry.register(
        "parallel_batch_decode", "Parallel batch decode using multiple processes via backend.run_parallel_batch_decode",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "n_samples": {"type": "integer", "default": 100},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42},
         "n_workers": {"type": "integer", "default": 4}},
        _handle_parallel_batch_decode)
    _registry.register(
        "mcp_health", "Server health check: uptime, memory, decoder status, tool count",
        {},
        _handle_mcp_health)
    _registry.register(
        "compare_all_decoders", "Run all compatible decoders on the same code and return comparison table",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "n_samples": {"type": "integer", "default": 50},
         "seed": {"type": "integer", "default": 42}},
        _handle_compare_all_decoders)
    _registry.register(
        "compatibility_matrix", "Return the full 16x10 decoder/code compatibility matrix",
        {},
        _handle_compatibility_matrix)
    _registry.register(
        "decoder_benchmark_suite", "Run standard benchmark (rotated_surface d=5, p=0.05) across all decoders",
        {"n_samples": {"type": "integer", "default": 100},
         "seed": {"type": "integer", "default": 42}},
        _handle_decoder_benchmark_suite)
    _registry.register(
        "get_backend_health", "7-tier backend health status from AutoDecoder diagnostics",
        {},
        _handle_get_backend_health)
    _registry.register(
        "export_session",
        "Export the current session (code + decode + benchmark + diagnostics) as a ZIP archive",
        {"output_path": {"type": ["string", "null"], "default": None,
                         "description": "Optional file path for the ZIP; auto-named if omitted"},
         "family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_export_session)
    _registry.register(
        "import_syndrome",
        "Load external syndrome data (CSV, JSON, or .npy) and decode it",
        {"file_path": {"type": "string", "description": "Path to syndrome file"},
         "family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC}},
        _handle_import_syndrome)
    _registry.register(
        "analyze_logicals",
        "Expose logical operator matrix, logical weight distribution, and code distance estimation",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5}},
        _handle_analyze_logicals)
    _registry.register(
        "analyze_error_patterns",
        "Analyze error patterns: weight distribution, cluster size, correlated errors",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "error_rate": {"type": "number", "default": 0.05},
         "n_samples": {"type": "integer", "default": 100},
         "seed": {"type": "integer", "default": 42}},
        _handle_analyze_error_patterns)

    # v1.0.0 backend-integration tools (DEM, Stim, threshold, LER, etc.).
    _register_v1_tools()

    # v1.0.1 compliance attestation (zero-egress posture for infosec review).
    _registry.register(
        "compliance_attestation", "Zero-egress / offline compliance attestation for infosec review: "
                                  "AST scan for network and telemetry imports, runtime EgressGuard "
                                  "state, offline license tier, local-only data residency, and "
                                  "optional Entra ID readiness. No network calls.",
        {},
        _handle_compliance_attestation)


def _handle_build_dem(family: str = "rotated_surface", distance: int = 5,
                      noise_model: str = "circuit", p: float = 0.05,
                      bias: float = 0.5) -> dict:
    """Build a Detector Error Model from a code and noise model."""
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        dem = be.build_dem_from_code(code, noise_model=noise_model,
                                     p=_require_float("p", p),
                                     bias=_require_float("bias", bias))
    except Exception as e:
        raise MCPError(f"Failed to build DEM with noise_model={noise_model!r}: {e}")
    return {"status": "ok", "family": family, "distance": distance,
            "noise_model": noise_model, "p": p, "dem": _json_safe(dem)}


def _handle_decode_dem(family: str = "rotated_surface", distance: int = 5,
                       decoder_kind: str = "bp_osd",
                       decoder_options: Optional[dict] = None) -> dict:
    """Decode using a Detector Error Model."""
    _require_decoder(decoder_kind)
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    options = _validate_decoder_options(decoder_options)
    try:
        return be.decode_dem(code, decoder_kind=decoder_kind, decoder_options=options)
    except Exception as e:
        raise MCPError(f"Failed to decode DEM: {e}")


def _handle_import_stim(file_path: str, family: str = "rotated_surface",
                        distance: int = 5,
                        decoder_name: str = "blossom") -> dict:
    """Import a Stim circuit and optionally decode it."""
    if not file_path:
        raise MCPError("file_path is required")
    try:
        circuit = be.import_stim_circuit(_require_str("file_path", file_path))
    except Exception as e:
        raise MCPError(f"Failed to import Stim circuit: {e}")
    out = {"status": "ok", "circuit": _json_safe(circuit)}
    # Try a one-shot decode so the user sees syndrome-valid output immediately.
    try:
        code = be.build_code(_require_str("family", family), _require_int("distance", distance))
        syndrome = circuit.detector_error_model().detector_errors  # best-effort
        if syndrome is not None:
            decoder = be._make_decoder(decoder_name, code)
            out["decode"] = _json_safe(decoder.decode(syndrome))
    except Exception:
        pass
    return out


def _handle_build_code_from_matrix(H_matrix: list, family: str = "custom",
                                   distance: int = 3) -> dict:
    """Build a code from a user-provided parity-check matrix."""
    import numpy as np
    H = np.asarray(H_matrix, dtype=np.uint8)
    if H.ndim != 2:
        raise MCPError("H_matrix must be 2D")
    try:
        code = be.build_code_from_matrix(H)
    except Exception as e:
        raise MCPError(f"Failed to build code from matrix: {e}")
    return {"family": family, "distance": distance,
            "summary": _json_safe(be.code_summary(code))}


def _handle_estimate_threshold(family: str = "rotated_surface", distance: int = 5,
                               decoder_kind: str = "blossom",
                               p_min: float = 0.01, p_max: float = 0.2,
                               n_samples: int = 100) -> dict:
    """Estimate the error threshold using binary search on error rate."""
    _require_decoder(decoder_kind)
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        return be.estimate_threshold(code, decoder_kind=decoder_kind,
                                     p_range=(_require_float("p_min", p_min),
                                              _require_float("p_max", p_max)),
                                     n_samples=_require_int("n_samples", n_samples))
    except Exception as e:
        raise MCPError(f"Threshold estimation failed: {e}")


def _handle_finite_size_scaling(family: str = "rotated_surface",
                                decoder_kind: str = "blossom",
                                distances: Optional[list] = None,
                                 p_vals: Optional[list] = None,
                                 n_samples: int = 100) -> dict:
    """Finite-size scaling analysis (LER vs distance at fixed p)."""
    _require_decoder(decoder_kind)
    try:
        return be.finite_size_scaling(_require_str("family", family),
                                      decoder_kind=decoder_kind,
                                      distances=distances or [3, 5, 7, 9, 11],
                                      p_vals=p_vals or [0.01, 0.03, 0.05, 0.07, 0.1],
                                      n_samples=_require_int("n_samples", n_samples))
    except Exception as e:
        raise MCPError(f"Finite-size scaling failed: {e}")


def _handle_run_ler_benchmark(family: str = "rotated_surface", distance: int = 5,
                              decoder_name: str = "blossom",
                              n_samples: int = 1000, error_rate: float = 0.05,
                              seed: int = 42) -> dict:
    """Run LER benchmark with Wilson confidence intervals."""
    _require_decoder(decoder_name)
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        return be.run_ler_benchmark(code, n_samples=_require_int("n_samples", n_samples),
                                    error_rate=_require_float("error_rate", error_rate),
                                    decoder_kind=decoder_name, seed=_require_int("seed", seed))
    except MCPError:
        raise
    except Exception as e:
        raise MCPError(f"LER benchmark failed: {e}")


def _handle_generate_parity_check(family: str = "rotated_surface", distance: int = 5) -> dict:
    """Generate a parity-check matrix for a code family."""
    try:
        H = be.generate_parity_check_matrix(_require_str("family", family),
                                            _require_int("distance", distance))
    except Exception as e:
        raise MCPError(f"Failed to generate parity-check matrix: {e}")
    return {"family": family, "distance": distance,
            "parity_check_matrix": _json_safe(H)}


def _handle_get_license_info() -> dict:
    """Get license info (tier, key status, expiry) from the decoder."""
    try:
        return be.get_license_info()
    except Exception as e:
        raise MCPError(f"Failed to get license info: {e}")


def _handle_generate_reproducibility_package(family: str = "rotated_surface",
                                            distance: int = 5,
                                            decoder_name: str = "blossom",
                                            error_rate: float = 0.05,
                                            seed: int = 42,
                                            output_path: str = "reproducibility_package.zip") -> dict:
    """Generate a complete reproducibility package (ZIP)."""
    _require_decoder(decoder_name)
    try:
        return be.generate_reproducibility_package(
            _require_str("family", family), _require_int("distance", distance),
            decoder_name, _require_float("error_rate", error_rate),
            _require_int("seed", seed), _require_str("output_path", output_path),
        )
    except Exception as e:
        raise MCPError(f"Failed to generate reproducibility package: {e}")


def _handle_export_figure(family: str = "rotated_surface", distance: int = 5,
                          output_path: str = "tanner_graph.png",
                          format: str = "png", dpi: int = 300) -> dict:
    """Export a publication-ready figure of the Tanner graph."""
    if format not in ("png", "pdf", "svg", "pgf"):
        raise MCPError("format must be one of: png, pdf, svg, pgf")
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        return be.export_figure(code, _require_str("family", family),
                                _require_int("distance", distance),
                                _require_str("output_path", output_path),
                                format=format, dpi=_require_int("dpi", dpi))
    except Exception as e:
        raise MCPError(f"Failed to export figure: {e}")


def _handle_get_server_env() -> dict:
    """Return the effective QECTOR environment variables (tuning vars).

    Secret-bearing variables (license key, MCP token) are NEVER returned —
    they are reported as "set"/"unset" only, so an MCP client cannot exfiltrate
    credentials through this tool.
    """
    import os
    keys = (
        "QECTOR_BLOSSOM_K_MULT", "QECTOR_BLOSSOM_INTRA_PAR",
        "QECTOR_BLOSSOM_INTRA_THREADS", "QECTOR_CUDA_DEVICE_ID",
        "QECTOR_OPENCL_DEVICE_ALLOW", "QECTOR_SILENT",
        "QECTOR_ENFORCE", "QECTOR_LICENSE_FILE",
        "QECTOR_PROVISION_TIMEOUT",
    )
    secret_keys = ("QECTOR_LICENSE_KEY", "QECTOR_MCP_TOKEN")
    result = {k: os.environ.get(k) for k in keys if os.environ.get(k) is not None}
    for k in secret_keys:
        if os.environ.get(k):
            result[k] = "<set:redacted>"
    return result


def _handle_compliance_attestation() -> dict:
    """Zero-egress / offline compliance attestation for infosec review.

    Returns the live posture: AST scan of the shipped surface for network and
    telemetry imports, runtime EgressGuard state, offline Ed25519 license tier,
    local-only data residency, and optional Entra ID readiness.  This tool
    performs no network calls; it is safe on air-gapped machines.
    """
    try:
        import compliance
    except Exception as exc:
        raise MCPError(f"compliance module unavailable: {exc}")
    try:
        report = compliance.compliance_report()
        report["blocking_network_call"] = False
        return report
    except Exception as exc:
        raise MCPError(f"attestation failed: {exc}")


def _handle_decode_hyperedge(family: str = "bicycle", distance: int = 3,
                             decoder_name: str = "bp_osd",
                             error_rate: float = 0.05, seed: int = 42) -> dict:
    """Hyperedge / qLDPC decoding via bp_osd (or any LDPC-capable decoder)."""
    _require_decoder(decoder_name)
    code = be.build_code(_require_str("family", family), _require_int("distance", distance))
    try:
        out = be.run_single_decode(code, _require_float("error_rate", error_rate),
                                   decoder_name, _require_int("seed", seed))
    except Exception as e:
        raise MCPError(f"Hyperedge decode failed: {e}")
    res = out["result"]
    return {
        "family": family, "distance": distance, "decoder": decoder_name,
        "hamming_weight": res.hamming_weight,
        "syndrome_valid": res.syndrome_valid,
        "logical_failure": res.logical_failure,
        "correction": _json_safe(res.correction),
    }


# ---------------------------------------------------------------------------
# Wire the v1.0.0 backend-integration tools into the registry.
# ---------------------------------------------------------------------------
def _register_v1_tools() -> None:
    """Register the v1.0.0 backend-integration tools.

    Called from :func:`_build_registry` *after* the existing tool list so the
    upstream ``qector_decoder_v3`` MCP surface (DEM, Stim, threshold,
    finite-size scaling, LER benchmark, parity-check matrix export, license
    info, reproducibility package, figure export, server env, hyperedge
    decode) is exposed in addition to the workbench-native tools.
    """
    _registry.register(
        "build_dem", "Build a Detector Error Model (DEM) from a code and noise model",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "noise_model": {"type": "string", "default": "depolarizing",
                         "description": "One of: depolarizing, biased, correlated, circuit"},
         "p": {"type": "number", "default": 0.05},
         "bias": {"type": "number", "default": 0.5}},
        _handle_build_dem)
    _registry.register(
        "decode_dem", "Decode using a Detector Error Model (DEM-native decoding)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_kind": {"type": "string", "default": "bp_osd", "description": _DECODER_DESC},
         "decoder_options": {"type": ["object", "null"], "default": None,
                            "description": _OPTIONS_DESC}},
        _handle_decode_dem)
    _registry.register(
        "import_stim", "Import a Stim circuit from file and convert to DEM",
        {"file_path": {"type": "string", "description": "Path to Stim circuit file"},
         "family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC}},
        _handle_import_stim)
    _registry.register(
        "build_code_from_matrix", "Build a code from a user-provided parity check matrix",
        {"H_matrix": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}},
                      "description": "Binary parity check matrix (n_checks x n_qubits)"},
         "family": {"type": "string", "default": "custom",
                    "description": "Family name for the custom code"},
         "distance": {"type": "integer", "default": 3}},
        _handle_build_code_from_matrix)
    _registry.register(
        "estimate_threshold", "Estimate the error threshold using binary search on error rate",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_kind": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "p_min": {"type": "number", "default": 0.01},
         "p_max": {"type": "number", "default": 0.2},
         "n_samples": {"type": "integer", "default": 100}},
        _handle_estimate_threshold)
    _registry.register(
        "finite_size_scaling", "Perform finite-size scaling analysis (LER vs distance at fixed p)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "decoder_kind": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "distances": {"type": "array", "items": {"type": "integer"},
                       "default": [3, 5, 7, 9, 11],
                       "description": "Code distances to test"},
         "p_vals": {"type": "array", "items": {"type": "number"},
                    "default": [0.01, 0.03, 0.05, 0.07, 0.1],
                    "description": "Error rates to test"},
         "n_samples": {"type": "integer", "default": 100}},
        _handle_finite_size_scaling)
    _registry.register(
        "run_ler_benchmark", "Run LER benchmark with Wilson confidence intervals",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "n_samples": {"type": "integer", "default": 1000},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_run_ler_benchmark)
    _registry.register(
        "generate_parity_check", "Generate a parity check matrix for a code family",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5}},
        _handle_generate_parity_check)
    _registry.register(
        "get_license_info", "Get license info from the decoder (tier, key_status, expiry)",
        {}, _handle_get_license_info)
    _registry.register(
        "generate_reproducibility_package",
        "Generate a complete reproducibility package (ZIP)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "decoder_name": {"type": "string", "default": "blossom", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42},
         "output_path": {"type": "string",
                         "default": "reproducibility_package.zip"}},
        _handle_generate_reproducibility_package)
    _registry.register(
        "export_figure", "Export a publication-ready figure of the Tanner graph",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "output_path": {"type": "string", "default": "tanner_graph.png"},
         "format": {"type": "string", "default": "png",
                    "description": "One of: png, pdf, svg, pgf"},
         "dpi": {"type": "integer", "default": 300}},
        _handle_export_figure)
    _registry.register(
        "get_server_env", "Get effective QECTOR environment variables (tuning vars)",
        {}, _handle_get_server_env)
    _registry.register(
        "decode_hyperedge",
        "Hyperedge / qLDPC decoding via bp_osd or other LDPC-capable decoders",
        {"family": {"type": "string", "default": "bicycle", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 3},
         "decoder_name": {"type": "string", "default": "bp_osd", "description": _DECODER_DESC},
         "error_rate": {"type": "number", "default": 0.05},
         "seed": {"type": "integer", "default": 42}},
        _handle_decode_hyperedge)
    _registry.register(
        "decode_syndrome_blossom",
        "Convenience tool: exact Blossom (MWPM) syndrome decode",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "syndrome": {"type": "array", "items": {"type": "integer"}, "description": "Binary syndrome vector"}},
        lambda **kwargs: _handle_decode_syndrome(decoder_name="blossom", **kwargs))
    _registry.register(
        "decode_syndrome_cascade",
        "Convenience tool: Hybrid cascading syndrome decode (UF pre-filter escalating to Blossom)",
        {"family": {"type": "string", "default": "rotated_surface", "description": _FAMILY_DESC},
         "distance": {"type": "integer", "default": 5},
         "syndrome": {"type": "array", "items": {"type": "integer"}, "description": "Binary syndrome vector"}},
        lambda **kwargs: _handle_decode_syndrome(decoder_name="hybrid_cascade", **kwargs))
    _registry.register(
        "decode_mmap", "Out-of-core batch decoding via memory-mapped arrays",
        {"family": {"type": "string", "default": "rotated_surface"},
         "distance": {"type": "integer", "default": 5},
         "syndrome_path": {"type": "string"},
         "output_path": {"type": "string"},
         "decoder_name": {"type": "string", "default": "cpu_batch"},
         "batch_size": {"type": "integer", "default": 65536},
         "n_shots": {"type": "integer"}},
        _handle_decode_mmap)
    
    def _mcp_get_entra_posture() -> dict[str, Any]:
        """Return the Microsoft Entra ID posture (enabled, unconfigured, or authenticated)."""
        try:
            import entra_auth
            return entra_auth.posture()
        except ImportError:
            return {"status": "disabled", "reason": "entra_auth module not found"}

    def _mcp_get_identity_info() -> dict[str, Any]:
        """Return identity info if signed into Entra ID, else None."""
        try:
            import entra_auth
            p = entra_auth.posture()
            if p.get("status") == "authenticated":
                return p
            return {"status": "unauthenticated"}
        except ImportError:
            return {"status": "disabled"}

    _registry.register(
        "get_entra_posture", "Return the Microsoft Entra ID posture (enabled, unconfigured, or authenticated).",
        {}, _mcp_get_entra_posture)
    _registry.register(
        "get_identity_info", "Return identity info if signed into Entra ID, else None.",
        {}, _mcp_get_identity_info)


# ---------------------------------------------------------------------------
# MCP stdio transport (newline-delimited JSON-RPC 2.0)
# ---------------------------------------------------------------------------

class _MethodNotFound(Exception):
    """Requested JSON-RPC method is not implemented."""


class _InvalidParams(Exception):
    """JSON-RPC params are structurally invalid."""


def _log(msg: str) -> None:
    """Log to stderr only — stdout is reserved for JSON-RPC messages."""
    try:
        print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)
    except Exception:
        pass


def _error_response(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _tool_input_schema(parameters: dict) -> dict:
    """Build a JSON Schema object from a registry parameter table."""
    properties: dict[str, dict] = {}
    required: list[str] = []
    for pname, spec in parameters.items():
        prop: dict[str, Any] = {}
        if "type" in spec:
            prop["type"] = spec["type"]
        if "description" in spec:
            prop["description"] = spec["description"]
        if "items" in spec:
            prop["items"] = spec["items"]
        if "default" in spec:
            prop["default"] = _json_safe(spec["default"])
        else:
            required.append(pname)
        properties[pname] = prop
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _list_tools_payload() -> dict:
    server = get_mcp_server()
    return {
        "tools": [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": _tool_input_schema(t["parameters"]),
            }
            for t in server.tools.tools.values()
        ]
    }


_BUSY_LOCK = asyncio.Lock()


async def _handle_tools_call(params: Any) -> dict:
    if not isinstance(params, dict):
        raise _InvalidParams("params must be an object with 'name' and 'arguments'")
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _InvalidParams("params.name (tool name) is required")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise _InvalidParams("params.arguments must be an object")
    if _BUSY_LOCK.locked():
        return {"content": [{"type": "text", "text": "server is busy processing another tool"}], "isError": True}
    
    try:
        async with _BUSY_LOCK:
            result = await asyncio.wait_for(call_mcp_tool(name, arguments), timeout=60.0)
    except asyncio.TimeoutError:
        return {"content": [{"type": "text", "text": f"tool {name!r} timed out after 60s"}], "isError": True}
    except MCPError as e:
        # Tool-level error: JSON-RPC success with isError=True per MCP spec.
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    try:
        text = json.dumps(result)
        if len(text) > 1024 * 1024:
            _log(f"result for {name} exceeded 1MB limit; truncating")
            return {"content": [{"type": "text", "text": "error: result exceeded 1MB limit and was truncated."}], "isError": True}
    except (TypeError, ValueError) as e:
        _log(f"result serialization failed for {name}: {e}")
        return {"content": [{"type": "text", "text": f"result serialization failed: {e}"}],
                "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": False}


# Set to True once a client has successfully completed `initialize` with a
# valid token (or when no token is configured). tools/list and tools/call are
# rejected before that point when QECTOR_MCP_TOKEN is set.
_session_authenticated = False


def _auth_required() -> bool:
    return bool(os.environ.get("QECTOR_MCP_TOKEN"))


async def _dispatch_method(method: Any, params: Any) -> Any:
    global _session_authenticated
    if method == "initialize":
        # CSRF token validation (constant-time comparison)
        expected_token = os.environ.get("QECTOR_MCP_TOKEN")
        if expected_token:
            import hmac as _hmac
            client_token = params.get("token") if isinstance(params, dict) else None
            if not isinstance(client_token, str) or not _hmac.compare_digest(
                client_token.encode("utf-8"), expected_token.encode("utf-8")
            ):
                _log("initialize rejected: invalid token")
                raise _InvalidParams("invalid or missing connection token")
        _session_authenticated = True
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": WORKBENCH_VERSION},
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {}
    if _auth_required() and not _session_authenticated:
        # Never serve tool traffic before a successful authenticated initialize.
        _log(f"rejected {method!r}: session not authenticated")
        raise _InvalidParams("session not authenticated: call initialize with a valid token first")
    if method == "tools/list":
        return _list_tools_payload()
    if method == "tools/call":
        return await _handle_tools_call(params)
    raise _MethodNotFound(str(method))


async def _dispatch_line(line: str) -> Optional[dict]:
    """Handle one JSON-RPC line; return the response dict or None (notification)."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as e:
        return _error_response(None, -32700, f"parse error: {e}")
    if not isinstance(msg, dict):
        return _error_response(None, -32600, "invalid request: expected a JSON object")
    is_notification = "id" not in msg
    msg_id = msg.get("id")
    method = msg.get("method")
    try:
        result = await _dispatch_method(method, msg.get("params"))
    except _MethodNotFound:
        if is_notification:
            _log(f"ignoring unknown notification {method!r}")
            return None
        return _error_response(msg_id, -32601, f"method not found: {method!r}")
    except _InvalidParams as e:
        if is_notification:
            return None
        return _error_response(msg_id, -32602, f"invalid params: {e}")
    except Exception as e:
        _log(f"handler for {method!r} crashed:\n{traceback.format_exc()}")
        if is_notification:
            return None
        return _error_response(msg_id, -32603, f"internal error: {e}")
    if is_notification:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _write_message(stdout, message: dict) -> None:
    try:
        text = json.dumps(message)
    except (TypeError, ValueError) as e:
        _log(f"response serialization failed: {e}")
        text = json.dumps(_error_response(message.get("id"), -32603,
                                          "internal error: unserializable response"))
    try:
        stdout.write(text + "\n")
        stdout.flush()
    except Exception as e:
        _log(f"stdout write failed: {e}")


MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 10 MB limit as per v0.7.0 spec


async def serve_stdio() -> int:
    """Serve MCP over newline-delimited JSON-RPC 2.0 on stdin/stdout.

    The loop never dies from a handler exception; it exits cleanly on EOF.
    """
    server = get_mcp_server()
    loop = asyncio.get_running_loop()
    stdin = sys.stdin
    stdout = sys.stdout
    _log(f"stdio server ready: {len(server.tools.tools)} tools, protocol {PROTOCOL_VERSION}, "
         f"workbench {WORKBENCH_VERSION}, backend {be.PACKAGE_VERSION}")
    # A SINGLE long-lived reader task keeps exactly one executor thread busy.
    # (The old code submitted a fresh blocking _shutdown_requested.wait() per
    # request, permanently consuming a worker each time; once the pool was
    # exhausted, stdin.readline could never run and the server silently hung —
    # visible as a client timeout after ~8 requests.)
    read_task = asyncio.ensure_future(asyncio.to_thread(stdin.readline))
    while True:
        if _shutdown_requested.is_set():
            _log("shutdown requested; exiting stdio loop")
            read_task.cancel()
            return 0
        done, _pending = await asyncio.wait({read_task}, timeout=0.25)
        if not done:
            continue  # still waiting for input; poll shutdown again
        try:
            line = read_task.result()
        except Exception as e:
            _log(f"stdin read failed: {e}")
            return 1
        if line == "":
            _shutdown_requested.set()
            _log("stdin closed; shutting down")
            return 0
        read_task = asyncio.ensure_future(asyncio.to_thread(stdin.readline))
        if len(line) > MAX_CONTENT_LENGTH:
            _log(f"rejected frame exceeding 10 MB limit ({len(line)} bytes)")
            _write_message(stdout, _error_response(None, -32600, "invalid request: frame length exceeds 10 MB limit"))
            continue
        line = line.strip()
        if not line:
            continue
        try:
            response = await _dispatch_line(line)
        except Exception as e:  # absolute last resort — keep serving
            _log(f"dispatch crashed:\n{traceback.format_exc()}")
            response = _error_response(None, -32603, f"internal error: {e}")
        if response is not None:
            _write_message(stdout, response)


def _reopen_frozen_streams() -> None:
    """Windows-specific helper to restore stdin/stdout/stderr pipes in frozen GUI builds."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    import ctypes
    import msvcrt
    import io

    STD_INPUT_HANDLE = -10
    STD_OUTPUT_HANDLE = -11
    STD_ERROR_HANDLE = -12

    kernel32 = ctypes.windll.kernel32

    for std_id, mode, attr in [
        (STD_INPUT_HANDLE, "r", "stdin"),
        (STD_OUTPUT_HANDLE, "w", "stdout"),
        (STD_ERROR_HANDLE, "w", "stderr"),
    ]:
        h = kernel32.GetStdHandle(std_id)
        if h and h != -1:
            try:
                fd = msvcrt.open_osfhandle(h, 0 if mode == "r" else 1)
                stream = io.TextIOWrapper(open(fd, mode + "b", buffering=0), encoding="utf-8")
                setattr(sys, attr, stream)
            except Exception:
                pass


_shutdown_requested = threading.Event()


def main() -> int:
    """Entry point for `python mcp_server.py` — run the stdio MCP server."""
    _reopen_frozen_streams()
    try:
        sys.stdout.reconfigure(encoding="utf-8", newline="\n")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception:
        pass
    _install_signal_handlers()
    try:
        return asyncio.run(serve_stdio())
    except KeyboardInterrupt:
        _log("interrupted; shutting down")
        return 0


def _install_signal_handlers() -> None:
    """Install SIGINT/SIGTERM handlers that request a graceful drain.

    In-flight tool calls finish (or hit their per-tool timeout) before the
    stdio loop exits, instead of dropping requests on the floor.
    """
    try:
        import signal as _signalmod

        def _request_shutdown(_signum, _frame):
            if not _shutdown_requested.is_set():
                _log(f"signal {_signum} received; draining in-flight requests")
            _shutdown_requested.set()

        for _sig in (_signalmod.SIGINT, _signalmod.SIGTERM):
            try:
                _signalmod.signal(_sig, _request_shutdown)
            except (ValueError, OSError):
                pass  # signal not available on this platform (e.g. SIGTERM on Windows)
    except Exception as e:  # pragma: no cover - defensive only
        _log(f"failed to install signal handlers: {e}")


if __name__ == "__main__":
    sys.exit(main())
