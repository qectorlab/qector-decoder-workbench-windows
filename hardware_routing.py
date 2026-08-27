"""
hardware_routing.py - local replacement for qector_decoder_v3.routing.

The installed qector_decoder_v3 wheel (v0.5.9) no longer ships a
'routing' submodule - backend.py was written against an older package
build (v0.5.8) that had it. Rather than depend on a private package
internal that has since been removed, this module reimplements the two
pieces of surface area the app actually needs (HardwareProfile,
Recommendation, detect_hardware(), recommend()) directly on top of the
current, real, public API: qector_decoder_v3.cuda_is_available() /
opencl_is_available().

No fabricated benchmark numbers are used here. The recommendation logic
is a plain, documented heuristic derived from the decoder docstrings
shipped in the installed package (v0.6.6): Blossom = weight-optimal exact
MWPM (reaches PyMatching's LER but is not faster); SparseBlossom =
region-growing near-optimal matching, NOT exact; UnionFind/FastUnionFind =
fast approximate paths with higher LER than exact MWPM (regenerate LER on
the target workload, no fixed ratio is quoted); batch decode should prefer
CUDA > OpenCL > CPU when available. Note: the standard qector_decoder_v3
wheel ships a CUDA path but no OpenCL kernels, so opencl_is_available() is
False unless the package was built from source with the opencl feature.
"""

from __future__ import annotations

import ctypes
import importlib
import os
import sys
from dataclasses import dataclass
from typing import Any, Optional

qec = importlib.import_module("qector_decoder_v3")

_CL_PLATFORM_NAME = 0x0902
_CL_DEVICE_TYPE_ALL = 0xFFFFFFFF


@dataclass(frozen=True)
class HardwareProfile:
    """Detected hardware/backends relevant to decode routing."""

    cuda_rust: bool
    gpu: Optional[str] = None
    opencl: bool = False
    opencl_device: Optional[str] = None
    # Host-side OpenCL, probed independently of the decoder build.  Without these
    # a machine with a functional OpenCL device and a decoder compiled
    # without OpenCL kernels both reported a bare "Unavailable", which reads like
    # a bug in the app when it is not one.
    opencl_host_devices: int = 0
    opencl_host_platform: Optional[str] = None
    opencl_reason: str = ""


def _load_opencl() -> Any:
    """Load the host OpenCL ICD, or return None when there is none."""
    if sys.platform == "win32":
        names, loader = ("OpenCL.dll",), ctypes.WinDLL
    elif sys.platform == "darwin":
        names = ("/System/Library/Frameworks/OpenCL.framework/OpenCL",)
        loader = ctypes.CDLL
    else:
        names, loader = ("libOpenCL.so.1", "libOpenCL.so"), ctypes.CDLL
    for name in names:
        try:
            return loader(name)
        except Exception:
            continue
    return None


def opencl_host() -> tuple[int, Optional[str]]:
    """Return (device count, first platform name) exposed by the *host* OpenCL.

    Deliberately independent of ``qec.opencl_is_available()``: that reports
    whether the decoder was *built* with OpenCL kernels, which is a different
    question from whether this machine can run OpenCL at all.
    """
    lib = _load_opencl()
    if lib is None:
        return 0, None
    try:
        count = ctypes.c_uint(0)
        if lib.clGetPlatformIDs(0, None, ctypes.byref(count)) != 0 or count.value == 0:
            return 0, None
        ids = (ctypes.c_void_p * count.value)()
        lib.clGetPlatformIDs(count.value, ids, None)
        total, first = 0, None
        for pid in ids:
            name: Optional[str] = None
            try:
                buf = ctypes.create_string_buffer(256)
                lib.clGetPlatformInfo(ctypes.c_void_p(pid), _CL_PLATFORM_NAME, 256, buf, None)
                name = buf.value.decode("utf-8", "replace").strip() or None
            except Exception:
                name = None
            try:
                found = ctypes.c_uint(0)
                if lib.clGetDeviceIDs(ctypes.c_void_p(pid), _CL_DEVICE_TYPE_ALL, 0,
                                      None, ctypes.byref(found)) == 0 and found.value:
                    total += found.value
                    if first is None:
                        first = name
            except Exception:
                continue
        return total, first
    except Exception:
        return 0, None


def opencl_reason(decoder_opencl: bool, host_devices: int,
                  host_platform: Optional[str]) -> str:
    """Explain the OpenCL state in terms a user can act on."""
    if decoder_opencl:
        return "available"
    if host_devices > 0:
        return (
            f"this qector-decoder-v3 build ships no OpenCL kernels; the host does "
            f"expose {host_devices} OpenCL device(s) via "
            f"{host_platform or 'an unknown platform'}. Rebuild qector-decoder-v3 "
            f"with its 'opencl' Cargo feature to enable this backend; CUDA and CPU "
            f"are unaffected."
        )
    return "no OpenCL runtime or device found on this machine"


@dataclass(frozen=True)
class Recommendation:
    """A decoder recommendation for a given code / priority."""

    decoder: str
    reason: str
    family: Optional[str]
    priority: str
    batch_size: int
    hardware: str
    gpu_batched_bp: bool


def detect_hardware() -> HardwareProfile:
    """Detect real GPU/CUDA/OpenCL availability via the installed package.

    Honors ``QECTOR_DISABLE_OPENCL=1``: when set, every OpenCL probe is
    skipped and the profile reports OpenCL unavailable with a reason that
    names the env var.  The docs advertise this escape hatch for systems
    whose OpenCL driver crashes or hangs on the ctypes platform probe;
    without this check the documented switch did nothing and the Hardware
    tab could freeze on such a machine.
    """
    cuda = bool(qec.cuda_is_available())

    disable_opencl = os.environ.get("QECTOR_DISABLE_OPENCL", "").strip() == "1"
    if disable_opencl:
        opencl = False
        opencl_name = None
        host_devices, host_platform = 0, None
        reason = "OpenCL probing skipped (QECTOR_DISABLE_OPENCL=1)"
    else:
        opencl = bool(qec.opencl_is_available())
        opencl_name = None
        if opencl:
            opencl_name = _safe_device_name("opencl")
        host_devices, host_platform = opencl_host()
        reason = opencl_reason(opencl, host_devices, host_platform)

    gpu_name: Optional[str] = None
    if cuda:
        gpu_name = _safe_device_name("cuda")

    return HardwareProfile(
        cuda_rust=cuda,
        gpu=gpu_name,
        opencl=opencl,
        opencl_device=opencl_name,
        opencl_host_devices=host_devices,
        opencl_host_platform=host_platform,
        opencl_reason=reason,
    )


def _safe_device_name(kind: str) -> Optional[str]:
    """Best-effort device name probe; a tiny 1-qubit decoder is enough to
    read .device_name without doing any real decode work."""
    try:
        dec: Any
        if kind == "cuda":
            dec = qec.CUDABatchDecoder([[0]], 1)
        else:
            dec = qec.OpenCLBatchDecoder([[0]], 1)
        return str(dec.device_name)
    except Exception:
        return None


# Decoders in accuracy order (best LER first) per the installed package's
# own docstrings (Blossom = exact MWPM; SparseBlossom = near-optimal, not
# exact; UnionFind family trades accuracy for raw speed).
_ACCURACY_ORDER = ["blossom", "sparse_blossom", "bp_osd", "fast_union_find", "union_find"]
_SPEED_ORDER = ["fast_union_find", "union_find", "cpu_batch", "sparse_blossom", "blossom"]


def recommend(
    code_family: Optional[str],
    distance: Optional[int],
    n_qubits: Optional[int],
    priority: str,
) -> Recommendation:
    """Heuristic decoder recommendation (deterministic, no model call).

    Mirrors the removed qector_decoder_v3.routing.recommend: priority is
    one of "balanced", "speed", "accuracy".
    """
    priority = (priority or "balanced").strip().lower()
    if priority not in ("balanced", "speed", "accuracy"):
        raise ValueError(f"unknown priority {priority!r}; choose from balanced/speed/accuracy")

    hw = detect_hardware()
    large_n = bool(n_qubits and n_qubits >= 200)
    high_distance = bool(distance and distance >= 9)

    # qLDPC codes (bicycle / bivariate_bicycle) are non-graphlike: the
    # union-find and AutoDecoder paths fail to construct on their high-weight
    # checks, so recommending them would be a broken recommendation.  BP-OSD is
    # the decoder designed for LDPC / qLDPC codes and always applies here.
    try:
        from backend import QLDPC_FAMILIES as _qldpc
    except Exception:
        _qldpc = {"bicycle", "bivariate_bicycle"}

    if code_family in _qldpc:
        decoder = "bp_osd"
        reason = (
            f"qLDPC code ({code_family}): BP-OSD is the appropriate decoder, "
            "matching / union-find decoders do not apply to high-weight qLDPC "
            "checks. blossom also works on small instances; regenerate LER on your target."
        )
    elif priority == "accuracy":
        decoder = "blossom"
        reason = "Exact MWPM (Blossom) minimizes logical error rate; sparse_blossom scales better for large codes."
        if large_n:
            decoder = "sparse_blossom"
            reason = "Large qubit count: sparse_blossom (near-optimal, not exact) scales better than Blossom; use Blossom if exact MWPM is required."
    elif priority == "speed":
        decoder = "fast_union_find"
        reason = "FastUnionFind gives the lowest per-decode latency; accept a higher LER than exact MWPM (regenerate the ratio on your target)."
        if hw.cuda_rust and large_n:
            decoder = "cpu_batch"
            reason = "CUDA available with a large batch: use a batch backend (cuda/cpu_batch) for max throughput."
    else:  # balanced
        if high_distance or large_n:
            decoder = "sparse_blossom"
            reason = "Balanced priority on a large/high-distance code: sparse_blossom trades a little speed for MWPM accuracy at scale."
        else:
            decoder = "union_find"
            reason = "Balanced priority on a small/medium code: union_find is a solid speed/accuracy middle ground."

    # QECTOR_ENABLE_OPENCL_AUTO gates whether the recommender treats OpenCL as
    # an auto-routing target.  Detection still reports OpenCL availability
    # (Hardware tab, self-diagnostics); this only controls whether recommend()
    # *suggests* it.  The docs advertise this as an opt-in so a user who built
    # the decoder with OpenCL kernels is not silently routed to a slower
    # backend they did not ask for.
    _opencl_auto = os.environ.get("QECTOR_ENABLE_OPENCL_AUTO", "").strip() == "1"
    opencl_routable = bool(hw.opencl and _opencl_auto)

    batch_size = 1024 if (hw.cuda_rust or opencl_routable) else 256
    hardware_label = "cuda" if hw.cuda_rust else (
        "opencl" if opencl_routable else "cpu")

    return Recommendation(
        decoder=decoder,
        reason=reason,
        family=code_family,
        priority=priority,
        batch_size=batch_size,
        hardware=hardware_label,
        gpu_batched_bp=bool(hw.cuda_rust and priority != "speed"),
    )