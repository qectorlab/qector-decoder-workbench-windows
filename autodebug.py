"""autodebug.py: self-diagnosing, multi-fallback decode backend for QECTOR.

A resilient layer over :mod:`backend`.  Three capabilities, all fully wired to
the real ``qector_decoder_v3`` API: no placeholders, no mocks:

1. **Resilient single decode** (:func:`resilient_single_decode`)  -  samples one
   seeded error/syndrome, then tries the requested decoder followed by an
   ordered fallback chain, *verifying* every correction against the GF(2)
   syndrome equation ``H·c == s`` and moving on when a decoder errors or returns
   an invalid correction.  Returns the first valid result plus a full trace of
   every attempt.

2. **Resilient batch decode** (:func:`resilient_batch_decode`)  -  tries the
   requested hardware backend then falls back ``cuda → opencl → cpu``, recording
   why each backend was skipped or failed.

3. **Self-diagnostics** (:func:`run_self_diagnostics`, :func:`probe_decoders`)  - 
   a comprehensive environment/decoder/hardware self-test that also surfaces the
   native diagnostics of v0.6.6's ``AutoDecoder``.

Design contract: **nothing here raises for control flow.**  Every public
function returns a structured, JSON-serialisable object (dataclasses expose
``.to_dict()``); failures are reported, never thrown, so the GUI, the MCP server
and tests stay robust.
"""

from __future__ import annotations

import platform
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import numpy as np

try:  # keep this module importable even if the compiled backend is broken,
    import backend as be  # so run_self_diagnostics() can report the failure.
    _BACKEND_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - only hit on a broken install
    be = None  # type: ignore[assignment]
    _BACKEND_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


# Ordered fallback chain used when the caller does not supply one.  union_find
# is the most general/robust matching decoder, so it leads; bp_osd (LDPC) and
# the auxiliary decoders round out the list.  lookup_table is intentionally last
# because it is only practical for small codes.  The v0.6.9 kinds
# (hybrid_cascade / belief_matching / gnn_belief_matching) sit with the other
# hybrid decoders; each is attempted only after the earlier entries failed and
# a failure (e.g. gnn_belief_matching on a pre-0.6.9 wheel, or a graphlike-only
# decoder on a qLDPC code) is recorded and skipped like any other.
DEFAULT_SINGLE_CHAIN = [
    "union_find", "fast_union_find", "blossom", "sparse_blossom",
    "auto", "hybrid", "hybrid_cascade", "belief_matching", "gnn_belief_matching",
    "two_stage", "ambiguity_cluster", "colour_code",
    "bp_osd", "predecoded", "auto_router", "lookup_table",
]
DEFAULT_BATCH_CHAIN = ["cuda", "opencl", "cpu"]


def _safe_logger():
    try:
        from logger import get_logger
        return get_logger()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Structured records
# ---------------------------------------------------------------------------

@dataclass
class AttemptRecord:
    """One decode attempt in a resilient run."""
    method: str
    ok: bool
    syndrome_valid: Optional[bool]
    hamming_weight: Optional[int]
    latency_ms: Optional[float]
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResilientDecodeResult:
    family: str
    distance: int
    error_rate: float
    seed: int
    requested_decoder: str
    used_decoder: Optional[str]
    success: bool
    fallback_used: bool
    correction: Optional[list]
    hamming_weight: Optional[int]
    syndrome_valid: Optional[bool]
    logical_failure: Optional[bool]
    message: str
    attempts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CheckResult:
    """One self-diagnostics check.  status ∈ {"pass", "warn", "fail"}."""
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DiagnosticsReport:
    overall_status: str            # "pass" | "degraded" | "fail"
    timestamp: float
    platform: str
    python: str
    workbench_version: str
    backend_version: Optional[str]
    summary: dict
    checks: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["checks"] = [c if isinstance(c, dict) else c.to_dict() for c in self.checks]
        return d


# ---------------------------------------------------------------------------
# Fallback-chain helpers
# ---------------------------------------------------------------------------

def _ordered_chain(requested: str, chain: Optional[list], default: list, valid: list) -> list:
    """Return ``requested`` first, then the fallback order, de-duplicated and
    filtered to valid names."""
    base = list(chain) if chain else list(default)
    ordered: list = []
    for name in [requested] + base:
        if name in valid and name not in ordered:
            ordered.append(name)
    # Guarantee the requested decoder leads even if it is not in ``valid`` yet;
    # the attempt will surface a clear error rather than being silently dropped.
    if requested not in ordered:
        ordered.insert(0, requested)
    return ordered


def _try_single(code, kind: str, syndrome, verify: bool) -> tuple[AttemptRecord, Optional[np.ndarray]]:
    """Try one decoder against a fixed syndrome; never raises."""
    t0 = time.perf_counter()
    try:
        decoder = be.make_decoder(code, kind)
        correction = np.asarray(decoder.decode(syndrome))
        latency_ms = (time.perf_counter() - t0) * 1e3
        valid = be.verify_correction(code, syndrome, correction) if verify else None
        rec = AttemptRecord(
            method=kind, ok=True, syndrome_valid=valid,
            hamming_weight=int(np.sum(correction)), latency_ms=round(latency_ms, 4),
            error=None,
        )
        return rec, correction
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1e3
        return AttemptRecord(
            method=kind, ok=False, syndrome_valid=None, hamming_weight=None,
            latency_ms=round(latency_ms, 4), error=f"{type(exc).__name__}: {exc}",
        ), None


# ---------------------------------------------------------------------------
# Resilient single decode
# ---------------------------------------------------------------------------

def resilient_single_decode(
    family: str,
    distance: int,
    error_rate: float = 0.05,
    decoder: str = "union_find",
    seed: int = 42,
    fallback_chain: Optional[list] = None,
    verify: bool = True,
) -> ResilientDecodeResult:
    """Decode one seeded syndrome, falling back through decoders on failure.

    Tries ``decoder`` first, then the fallback chain, accepting the first
    correction that reproduces the syndrome (when ``verify``).  Records every
    attempt.  Never raises.
    """
    log = _safe_logger()
    requested = str(decoder)
    base = ResilientDecodeResult(
        family=str(family), distance=int(distance) if _is_int(distance) else -1,
        error_rate=float(error_rate), seed=int(seed) if _is_int(seed) else 0,
        requested_decoder=requested, used_decoder=None, success=False,
        fallback_used=False, correction=None, hamming_weight=None,
        syndrome_valid=None, logical_failure=None, message="", attempts=[],
    )
    if be is None:
        base.message = f"backend unavailable: {_BACKEND_IMPORT_ERROR}"
        return base

    try:
        code = be.build_code(str(family), int(distance))
    except Exception as exc:
        base.message = f"code build failed: {type(exc).__name__}: {exc}"
        return base

    try:
        error, syndrome = be.sample_error_and_syndrome(code, float(error_rate), int(seed))
    except Exception as exc:
        base.message = f"error sampling failed: {type(exc).__name__}: {exc}"
        return base

    logicals = be.logicals_matrix(code)
    chain = _ordered_chain(requested, fallback_chain, DEFAULT_SINGLE_CHAIN, list(be.DECODER_KINDS))

    for kind in chain:
        rec, correction = _try_single(code, kind, syndrome, verify)
        base.attempts.append(rec.to_dict())
        usable = rec.ok and (rec.syndrome_valid is not False)  # None (verify off) counts as usable
        if usable and correction is not None:
            lf = be.logical_failure(logicals, error, correction) if logicals is not None else None
            base.success = True
            base.used_decoder = kind
            base.fallback_used = (kind != requested)
            base.correction = np.asarray(correction, dtype=np.int64).tolist()
            base.hamming_weight = int(np.sum(correction))
            base.syndrome_valid = rec.syndrome_valid
            base.logical_failure = lf
            base.message = (
                f"decoded with requested decoder {requested!r}"
                if not base.fallback_used
                else f"requested decoder {requested!r} unusable; recovered via fallback {kind!r}"
            )
            if log is not None and base.fallback_used:
                log.warning(
                    f"autodebug: {requested!r} failed on {family} d={distance}; "
                    f"fell back to {kind!r} after {len(base.attempts)} attempt(s)"
                )
            return base

    tried = ", ".join(a["method"] for a in base.attempts)
    base.message = f"all decoders failed to produce a valid correction (tried: {tried})"
    if log is not None:
        log.error(f"autodebug: {base.message} for {family} d={distance}")
    return base


# ---------------------------------------------------------------------------
# Resilient batch decode
# ---------------------------------------------------------------------------

def resilient_batch_decode(
    family: str,
    distance: int,
    backend: str = "cuda",
    n_samples: int = 100,
    error_rate: float = 0.05,
    seed: int = 1,
    fallback_chain: Optional[list] = None,
) -> dict:
    """Batch-decode, falling back ``cuda → opencl → cpu`` on unavailability.

    Unlike :func:`backend.run_batch_decode` (which fails loudly with no
    fallback), this path deliberately walks a hardware chain and reports every
    step so the caller sees exactly which backend served the request.
    """
    log = _safe_logger()
    result: dict[str, Any] = {
        "family": str(family), "distance": distance, "requested_backend": str(backend),
        "backend_used": None, "success": False, "fallback_used": False,
        "attempts": [], "message": "",
    }
    if be is None:
        result["message"] = f"backend unavailable: {_BACKEND_IMPORT_ERROR}"
        return result
    try:
        code = be.build_code(str(family), int(distance))
    except Exception as exc:
        result["message"] = f"code build failed: {type(exc).__name__}: {exc}"
        return result

    chain = _ordered_chain(str(backend), fallback_chain, DEFAULT_BATCH_CHAIN, ["cpu", "cuda", "opencl"])
    for b in chain:
        t0 = time.perf_counter()
        try:
            res = be.run_batch_decode(
                code, backend=b, n_samples=int(n_samples),
                error_rate=float(error_rate), seed=int(seed),
            )
            result["attempts"].append({
                "backend": b, "ok": True,
                "success_rate": res["success_rate"],
                "batch_seconds": res["batch_seconds"], "error": None,
            })
            result.update({
                "backend_used": b, "success": True,
                "fallback_used": (b != str(backend)),
                "success_rate": res["success_rate"],
                "logical_error_rate": res["logical_error_rate"],
                "mean_hamming_weight": res["mean_hamming_weight"],
                "batch_seconds": res["batch_seconds"],
                "n_samples": res["n_samples"],
                "message": (f"batch-decoded on {b!r}" if b == str(backend)
                            else f"{backend!r} unavailable; recovered on {b!r}"),
            })
            if log is not None and result["fallback_used"]:
                log.warning(f"autodebug: batch backend {backend!r} unavailable; used {b!r}")
            return result
        except Exception as exc:
            result["attempts"].append({
                "backend": b, "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "batch_seconds": round((time.perf_counter() - t0) * 1e3 / 1e3, 6),
            })
    tried = ", ".join(a["backend"] for a in result["attempts"])
    result["message"] = f"all batch backends failed (tried: {tried})"
    if log is not None:
        log.error(f"autodebug: {result['message']} for {family} d={distance}")
    return result


# ---------------------------------------------------------------------------
# Probe: which decoders work for a given code
# ---------------------------------------------------------------------------

def probe_decoders(
    family: str,
    distance: int,
    error_rate: float = 0.05,
    seed: int = 42,
    verify: bool = True,
) -> dict:
    """Try every wired decoder against one seeded syndrome and report results."""
    out: dict[str, Any] = {
        "family": str(family), "distance": distance, "error_rate": float(error_rate),
        "seed": seed, "results": [], "working": [], "failing": [], "message": "",
    }
    if be is None:
        out["message"] = f"backend unavailable: {_BACKEND_IMPORT_ERROR}"
        return out
    try:
        code = be.build_code(str(family), int(distance))
        error, syndrome = be.sample_error_and_syndrome(code, float(error_rate), int(seed))
    except Exception as exc:
        out["message"] = f"setup failed: {type(exc).__name__}: {exc}"
        return out

    for kind in be.DECODER_KINDS:
        rec, _ = _try_single(code, kind, syndrome, verify)
        out["results"].append(rec.to_dict())
        usable = rec.ok and (rec.syndrome_valid is not False)
        (out["working"] if usable else out["failing"]).append(kind)
    out["message"] = f"{len(out['working'])}/{len(be.DECODER_KINDS)} decoders produced a valid correction"
    return out


# ---------------------------------------------------------------------------
# Comprehensive self-diagnostics
# ---------------------------------------------------------------------------

def run_self_diagnostics(probe_family: str = "repetition", probe_distance: int = 3) -> DiagnosticsReport:
    """Run a full environment/decoder/hardware self-test.

    Critical failures (backend import, dependency import, no working decoder)
    yield ``overall_status == "fail"``; missing GPU acceleration downgrades to
    ``"degraded"``; everything green is ``"pass"``.
    """
    from version import WORKBENCH_VERSION, MIN_BACKEND_VERSION

    checks: list[CheckResult] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append(CheckResult(name=name, status=status, detail=detail))

    # 1. Compiled backend import + version
    backend_version: Optional[str] = None
    if be is None:
        add("backend import", "fail", f"import backend failed: {_BACKEND_IMPORT_ERROR}")
    else:
        backend_version = getattr(be, "PACKAGE_VERSION", None)
        add("backend import", "pass", f"qector_decoder_v3 {backend_version} imported")
        if backend_version and _version_tuple(backend_version) < _version_tuple(MIN_BACKEND_VERSION):
            add("backend version", "fail",
                f"{backend_version} < minimum supported {MIN_BACKEND_VERSION}")
        else:
            add("backend version", "pass",
                f"{backend_version} >= minimum {MIN_BACKEND_VERSION}")

    # 2. Scientific dependencies.  numpy and matplotlib are imported directly by
    #    the workbench and are required; scipy is a *declared but optional*
    #    dependency  -  neither the app nor the qector_decoder_v3 backend imports
    #    it, so a frozen bundle legitimately omits it.  In frozen builds we skip
    #    the scipy check entirely so diagnostics report HEALTHY; in source/dev
    #    builds a missing scipy is still a warning (degraded), never a failure.
    _critical_deps = {"numpy", "matplotlib"}
    _frozen = bool(getattr(sys, "frozen", False))
    for mod in ("numpy", "scipy", "matplotlib"):
        if mod == "scipy" and _frozen:
            continue
        try:
            m = __import__(mod)
            add(f"dependency {mod}", "pass", f"{mod} {getattr(m, '__version__', '?')}")
        except Exception as exc:
            if mod in _critical_deps:
                add(f"dependency {mod}", "fail", f"import {mod} failed: {exc}")
            else:
                add(f"dependency {mod}", "warn",
                    f"optional dependency {mod} not present (not required at runtime): {exc}")

    # 3. Hardware acceleration
    gpu_available = False
    if be is not None:
        try:
            from hardware_routing import detect_hardware
            hw = detect_hardware()
            gpu_available = bool(hw.cuda_rust or hw.opencl)
            if gpu_available:
                add("hardware acceleration", "pass",
                    f"cuda={hw.cuda_rust} ({hw.gpu}) opencl={hw.opencl} ({hw.opencl_device})")
            else:
                add("hardware acceleration", "warn",
                    "no CUDA/OpenCL device detected; CPU decode only (batch GPU backends unavailable)")
        except Exception as exc:
            add("hardware acceleration", "warn", f"hardware probe failed: {exc}")

    # 4. Code families
    if be is not None:
        built, failed = [], []
        for fam in be.CODE_FAMILIES:
            try:
                be.build_code(fam, 3)
                built.append(fam)
            except Exception as exc:
                failed.append(f"{fam} ({exc})")
        if failed:
            add("code families", "fail", f"built {built}; FAILED {failed}")
        else:
            add("code families", "pass", f"all {len(built)} families build at d=3: {', '.join(built)}")

    # 5. Every decoder against a real syndrome
    working: list[str] = []
    if be is not None:
        probe = probe_decoders(probe_family, probe_distance)
        working = probe.get("working", [])
        failing = probe.get("failing", [])
        if not working:
            add("decoders", "fail", f"no decoder produced a valid correction on {probe_family} d={probe_distance}")
        elif failing:
            add("decoders", "warn",
                f"{len(working)}/{len(be.DECODER_KINDS)} usable on {probe_family} d={probe_distance}; "
                f"unusable: {', '.join(failing)}")
        else:
            add("decoders", "pass",
                f"all {len(working)} decoders usable on {probe_family} d={probe_distance}: {', '.join(working)}")

    # 6. Native AutoDecoder diagnostics (v0.6.6)
    if be is not None:
        try:
            import importlib
            qd = importlib.import_module("qector_decoder_v3")
            code = be.build_code(probe_family, probe_distance)
            ad = qd.AutoDecoder(code.check_to_qubits)
            diag = ad.diagnostics()
            msg = (f"available_backends={diag.get('available_backends')} "
                   f"prefer={diag.get('config', {}).get('prefer')}")
            if hasattr(ad, "_diag"):
                health = getattr(ad._diag, "backend_health", {})
                active = getattr(ad._diag, "active_backend", None)
                msg += f" active_backend={active} backend_health={health}"
            add("auto-decoder diagnostics", "pass", msg)
        except Exception as exc:
            add("auto-decoder diagnostics", "warn", f"AutoDecoder diagnostics unavailable: {exc}")

    # 6b. Newly wired v1.0.0 feature surface (rich diagnostics, native streaming,
    #     policy routing, ecosystem compat, enterprise hooks). Never fatal: a hiccup
    #     here degrades to a warning so a healthy install is never marked "fail" for an
    #     auxiliary feature.
    if be is not None:
        feature_errors: list[str] = []
        try:
            code = be.build_code(probe_family, probe_distance)
            be.run_diagnostic_decode(code, 0.05, "blossom", seed=1)
        except Exception as exc:
            feature_errors.append(f"diagnostic_decode: {exc}")
        try:
            rec = be.native_recommend(probe_family, probe_distance)
            if rec.get("status") != "ok":
                feature_errors.append(f"native_recommend: {rec.get('message')}")
        except Exception as exc:
            feature_errors.append(f"native_recommend: {exc}")
        try:
            be.run_native_streaming(be.build_code(probe_family, probe_distance),
                                    n_rounds=4, window_size=2, seed=1)
        except Exception as exc:
            feature_errors.append(f"native_streaming: {exc}")
        try:
            be.compat_report()
        except Exception as exc:
            feature_errors.append(f"compat_report: {exc}")
        try:
            be.clear_decoder_cache()
        except Exception as exc:
            feature_errors.append(f"clear_decoder_cache: {exc}")
        try:
            be.run_doctor_checks()
        except Exception as exc:
            feature_errors.append(f"run_doctor_checks: {exc}")
            
        if feature_errors:
            add("v1.0.0 feature wiring", "warn",
                "some auxiliary features unavailable: " + "; ".join(feature_errors))
        else:
            add("v1.0.0 feature wiring", "pass",
                "diagnostic decode, native streaming, policy routing, compat report and v1.0.0 hooks all OK")

    # 7. Writable data directory
    try:
        from utils import get_data_dir
        d = get_data_dir()
        probe_file = d / ".qector_write_test"
        probe_file.write_text("ok", encoding="utf-8")
        probe_file.unlink()
        add("data directory", "pass", f"writable: {d}")
    except Exception as exc:
        add("data directory", "warn", f"data dir not writable: {exc}")

    # Aggregate
    n_fail = sum(1 for c in checks if c.status == "fail")
    n_warn = sum(1 for c in checks if c.status == "warn")
    n_pass = sum(1 for c in checks if c.status == "pass")
    if n_fail:
        overall = "fail"
    elif n_warn:
        overall = "degraded"
    else:
        overall = "pass"

    report = DiagnosticsReport(
        overall_status=overall,
        timestamp=time.time(),
        platform=platform.platform(),
        python=sys.version.split()[0],
        workbench_version=WORKBENCH_VERSION,
        backend_version=backend_version,
        summary={"pass": n_pass, "warn": n_warn, "fail": n_fail,
                 "total": len(checks), "working_decoders": working},
        checks=[c.to_dict() for c in checks],
    )
    log = _safe_logger()
    if log is not None:
        log.info(f"self-diagnostics: {overall} ({n_pass} pass / {n_warn} warn / {n_fail} fail)")
    return report


self_diagnostics = run_self_diagnostics


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------

def _is_int(v: Any) -> bool:
    try:
        int(v)
        return True
    except Exception:
        return False


def _version_tuple(v: str) -> tuple:
    nums: list[int] = []
    for seg in str(v).split("."):
        s = ""
        for ch in seg:
            if ch.isdigit():
                s += ch
            else:
                break
        if s == "":
            break
        nums.append(int(s))
    return tuple(nums) if nums else (0,)
