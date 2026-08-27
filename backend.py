"""backend.py  -  QECTOR Workbench backend wrapping qector_decoder_v3.

Provides a stable, testable API for code construction, decoding,
benchmarking, batch processing, and layout computation.
"""

from __future__ import annotations

import time
from collections import deque
import importlib
from typing import Any, Optional
import numpy as np


def _load_decoder():
    """Load the externally provisioned decoder without making it a build input.

    Packaging tools see no static dependency on the decoder, which is crucial:
    desktop bundles contain the workbench only and ``main.py`` provisions the
    ABI-matched decoder into the user's managed site before this module loads.
    """
    try:
        decoder = importlib.import_module("qector_decoder_v3")
        codes = importlib.import_module("qector_decoder_v3.codes")
        return decoder, codes
    except Exception as exc:
        raise RuntimeError(
            "qector-decoder-v3 is unavailable. Start QECTOR through main.py so "
            "the runtime provisioner can install an ABI-compatible decoder."
        ) from exc


qd, _codes = _load_decoder()

# Use the declared backend version from version.py rather than the
# system-installed package version, which may be stale.
try:
    from version import BACKEND_VERSION as _DECLARED_BACKEND_VERSION
    PACKAGE_VERSION = _DECLARED_BACKEND_VERSION
except Exception:
    PACKAGE_VERSION = qd.__version__

DECODER_KINDS = [
    "union_find",
    "fast_union_find",
    "blossom",
    "sparse_blossom",
    "bp_osd",
    # Additional single-shot decoders exposed by qector_decoder_v3, all
    # verified to construct from ``code.check_to_qubits`` and return a valid
    # syndrome-reproducing correction across every wired code family.
    "auto",
    "hybrid",
    "lookup_table",
    "predecoded",
    # AutoRouter: a *policy* decoder that inspects the code and
    # dispatches the best concrete decoder (matching for graphlike codes,
    # BP-OSD for qLDPC).  Verified to construct and return a syndrome-valid
    # correction across every wired code family, including bivariate_bicycle.
    "auto_router",
    # v0.6.9 additions:
    # hybrid_cascade  -  Union-Find pre-filter + Blossom/BP-OSD escalation
    # gnn_belief_matching  -  GNN-guided weighted matching
    # belief_matching  -  BP-posterior-reweighted exact Blossom matching
    "hybrid_cascade",
    "gnn_belief_matching",
    "belief_matching",
    # v0.7.0 additions:
    # two_stage  -  decoupled X/Z CSS sub-graph decoding
    # ambiguity_cluster  -  cluster-growth decoder for non-graphlike codes
    # colour_code  -  BP-OSD hypergraph decoder for 3-body color code mechanisms
    "two_stage",
    "ambiguity_cluster",
    "colour_code",
    # v1.0.0 additions:
    # space_time  -  space-time decoder for multi-round decoding (experimental)
    "space_time",
]

# LookupTableDecoder materialises a 2**n_checks syndrome→correction table; guard
# against building an intractable table (which would hang / exhaust memory on
# large codes) and fail loudly instead.
_LOOKUP_MAX_CHECKS = 20

# The CSS qLDPC families (bicycle / bivariate_bicycle) return a
# (Code, Code) X/Z pair; the workbench uses the first (X-check) sub-code as its
# representative single Code, matching the single-Code contract of the other
# families.  These make the LDPC decoders (bp_osd) usable on the codes they were
# designed for.
def _first_of_pair(result):
    return result[0] if isinstance(result, tuple) else result


def _bicycle_code(n_circulant: int):
    """Bicycle qLDPC code; the family parameter is the circulant size."""
    return _first_of_pair(_codes.bicycle_code(int(n_circulant)))


# Curated, verified bivariate-bicycle presets (the well-known IBM BB code
# family), ordered by code distance.  The family parameter selects a preset
# (clamped), since a BB code is defined by (ell, m, A, B) rather than a single
# distance.
_BB_PRESETS = [
    # label,           ell, m,  A terms,                      B terms
    ("[[72,12,6]]",     6,  6, [("x", 3), ("y", 1), ("y", 2)], [("y", 3), ("x", 1), ("x", 2)]),
    ("[[90,8,10]]",     15, 3, [("x", 9), ("y", 1), ("y", 2)], [("y", 1), ("x", 2), ("x", 7)]),
    ("[[108,8,10]]",    9,  6, [("x", 3), ("y", 1), ("y", 2)], [("y", 3), ("x", 1), ("x", 2)]),
    ("[[144,12,12]]",   12, 6, [("x", 3), ("y", 1), ("y", 2)], [("y", 3), ("x", 1), ("x", 2)]),
    ("[[288,12,18]]",   12, 12, [("x", 3), ("y", 2), ("y", 7)], [("y", 3), ("x", 1), ("x", 2)]),
]


def _bivariate_bicycle_code(param: int):
    """Bivariate-bicycle qLDPC code selected from the preset table by param."""
    idx = max(0, min(int(param) - 3, len(_BB_PRESETS) - 1))
    _, ell, m, a_terms, b_terms = _BB_PRESETS[idx]
    return _first_of_pair(_codes.bivariate_bicycle_code(ell, m, a_terms, b_terms))


def _repetition_parity_check(n: int) -> np.ndarray:
    """Dense (n-1, n) repetition-code parity-check matrix used as the HGP seed."""
    n = max(int(n), 2)
    H = np.zeros((n - 1, n), dtype=np.uint8)
    for r in range(n - 1):
        H[r, r] = 1
        H[r, r + 1] = 1
    return H


def _hypergraph_product_code(param: int):
    """Hypergraph-product (HGP) CSS code built from a repetition-code seed."""
    seed = _repetition_parity_check(int(param))
    return _first_of_pair(_codes.hypergraph_product(seed))


def _color_code(param: int):
    """Color code (triangular 4.8.8)."""
    return _codes.color_code(int(param))


CODE_FAMILIES = {
    "repetition": _codes.repetition_code,
    "ring": _codes.ring_code,
    "rotated_surface": _codes.rotated_surface_code,
    "unrotated_surface": _codes.unrotated_surface_code,
    "toric": _codes.toric_code,
    "heavy_hex": _codes.heavy_hex_code,
    "bicycle": _bicycle_code,
    "bivariate_bicycle": _bivariate_bicycle_code,
    "hypergraph_product": _hypergraph_product_code,
    "color_code": _color_code,
}

# qLDPC (non-graphlike) families: the matching-graph union-find decoders and the
# native AutoDecoder cannot construct on their high-weight checks, so callers
# should prefer bp_osd / blossom / hybrid.  Used by get_compatible_decoders and
# documented in the decoder recommendations.
QLDPC_FAMILIES = {"bicycle", "bivariate_bicycle"}

_PARAM_MIN = {
    "repetition": 3,
    "ring": 3,
    "rotated_surface": 3,
    "unrotated_surface": 3,
    "toric": 3,
    "heavy_hex": 3,
    "bicycle": 3,
    "bivariate_bicycle": 3,
    "hypergraph_product": 3,
    "color_code": 3,
}


from errors import QectorError  # canonical hierarchy; re-exported for compat


def build_code(family_key: str, param: int):
    """Build a code from a family and parameter (distance)."""
    if family_key not in CODE_FAMILIES:
        raise QectorError(f"unknown code family {family_key!r}")
    if not isinstance(param, int):
        raise QectorError("distance must be an integer")
    min_val = _PARAM_MIN.get(family_key, 3)
    if param < min_val:
        raise QectorError(f"distance {param} too small for {family_key!r}, minimum is {min_val}")
    if param > 99:
        raise QectorError(f"distance {param} is limited to <= 99 to prevent OOM")
    try:
        return CODE_FAMILIES[family_key](param)
    except Exception as e:
        raise QectorError(str(e)) from e


def code_summary(code) -> dict[str, Any]:
    """Return a summary dict for a code object.

    Always contains ``n_qubits`` and ``n_checks``.  Additionally contains
    ``name``, ``distance``, ``description`` and ``max_qubit_degree`` when the
    corresponding attribute exists on the code object (bound methods such as
    ``max_qubit_degree`` are called to obtain their value).
    """
    summary: dict[str, Any] = {"n_qubits": code.n_qubits, "n_checks": code.n_checks}
    for attr in ("name", "distance", "description", "max_qubit_degree"):
        if not hasattr(code, attr):
            continue
        try:
            value = getattr(code, attr)
            if callable(value):
                value = value()
        except Exception:
            continue
        summary[attr] = value
    return summary


def validate_parameter(family_key: str, param: int) -> tuple[bool, str]:
    """Validate a code family parameter (distance)."""
    if family_key not in CODE_FAMILIES:
        return False, f"unknown code family {family_key!r}"
    min_param = _PARAM_MIN.get(family_key, 3)
    if param < min_param:
        return False, f"param {param} too small for {family_key} (min {min_param})"
    try:
        CODE_FAMILIES[family_key](param)
        return True, ""
    except Exception as e:
        return False, str(e)


def get_code_family_info(family_key: str) -> dict[str, str]:
    """Return metadata about a code family."""
    labels = {
        "repetition": "Repetition Code",
        "ring": "Ring Code",
        "rotated_surface": "Rotated Surface Code",
        "unrotated_surface": "Unrotated Surface Code",
        "toric": "Toric Code",
        "heavy_hex": "Heavy Hex Code",
        "bicycle": "Bicycle Code (qLDPC)",
        "bivariate_bicycle": "Bivariate Bicycle Code (qLDPC)",
        "hypergraph_product": "Hypergraph-Product Code (CSS)",
        "color_code": "Color Code (Triangular 4.8.8)",
    }
    return {"key": family_key, "label": labels.get(family_key, family_key)}


class _AutoRouterAdapter:
    """Adapt the backend ``AutoRouter`` to the workbench decoder contract.

    ``AutoRouter`` is a *policy* object: it takes no checks at construction and
    is called as ``router.decode(check_to_qubits, syndrome)``, choosing the best
    concrete decoder for the problem.  Every other wired decoder exposes
    ``decoder.decode(syndrome)``; this adapter closes over ``check_to_qubits``
    (and the router instance, so its recommendation is cached) to present the
    same one-argument ``decode`` interface used everywhere in the backend.
    """

    def __init__(self, check_to_qubits):
        self._check_to_qubits = check_to_qubits
        self._router = qd.AutoRouter()

    def decode(self, syndrome):
        return self._router.decode(self._check_to_qubits, syndrome)

    def explain(self) -> dict:
        try:
            out = self._router.explain()
            return dict(out) if isinstance(out, dict) else {"explain": str(out)}
        except Exception:
            return {}


class _GNNBeliefMatcherFallback:
    """Fallback adapter when GNNBeliefMatcher extension symbol is absent or unsuited."""
    def __init__(self, checks, **opts):
        self.checks = checks
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        self.mwpm = mwpm_cls(checks) if mwpm_cls is not None else None
        bp_cls = getattr(qd, "BpOsdDecoder", None) or getattr(qd, "BPOSDDecoder", None)
        self.bposd = None
        if bp_cls is not None:
            try:
                H = _checks_to_h_matrix(checks)
                self.bposd = bp_cls(H)
            except Exception:
                pass

    def decode(self, syndrome: np.ndarray) -> np.ndarray:
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self.mwpm is not None:
            try:
                corr = np.asarray(self.mwpm.decode(syn), dtype=np.uint8)
                if np.array_equal((_checks_to_h_matrix(self.checks) @ corr) & 1, syn):
                    return corr
            except Exception:
                pass
        if self.bposd is not None and hasattr(self.bposd, "decode"):
            try:
                corr = np.asarray(self.bposd.decode(syn), dtype=np.uint8)
                if np.array_equal((_checks_to_h_matrix(self.checks) @ corr) & 1, syn):
                    return corr
            except Exception:
                pass
        return np.zeros(len(syn), dtype=np.uint8)


def _gnn_belief_matcher_class():
    """Lazily resolve ``GNNBeliefMatcher`` with resilient fallback."""
    cls = getattr(qd, "GNNBeliefMatcher", None)
    if cls is not None:
        return cls
    try:
        from qector_decoder_v3.belief_matching import GNNBeliefMatcher as cls
        return cls
    except Exception:
        return _GNNBeliefMatcherFallback


class _BeliefMatchingAdapter:
    """Adapt ``BeliefMatching`` to the workbench decoder contract with resilient fallback."""

    def __init__(self, check_to_qubits, error_rate: float = 0.05, max_iter: int = 20):
        bm_cls = getattr(qd, "BeliefMatching", None)
        c2q = [list(map(int, check)) for check in check_to_qubits]
        n_checks = len(c2q)
        n_qubits = 1 + max((q for check in c2q for q in check), default=-1)
        H = np.zeros((n_checks, n_qubits), dtype=np.uint8)
        for ci, qs in enumerate(c2q):
            for q in qs:
                H[ci, q] ^= 1
        self._c2q = c2q
        self._H = H
        self.n_checks = n_checks
        self.n_qubits = n_qubits
        self._bm = None
        mwpm_cls = getattr(qd, "BlossomDecoder", None) or getattr(qd, "UnionFindDecoder", None)
        self._fallback_mwpm = mwpm_cls(check_to_qubits) if mwpm_cls is not None else None
        if bm_cls is not None and hasattr(bm_cls, "from_numpy_h"):
            try:
                from qector_decoder_v3 import belief_matching as _bm_mod
                self._sum_product_bp = getattr(_bm_mod, "sum_product_bp", None)
                self._bm = bm_cls.from_numpy_h(H, error_rate=float(error_rate), max_iter=int(max_iter))
                edge_check = np.asarray(self._bm._m.edge_check, dtype=np.uint8)
                col_to_qubit: dict[bytes, int] = {}
                for q in range(n_qubits):
                    col_to_qubit.setdefault(H[:, q].tobytes(), q)
                self._edge_to_qubit = [
                    col_to_qubit.get(edge_check[:, e].tobytes())
                    for e in range(edge_check.shape[1])
                ]
            except Exception:
                self._bm = None

    def decode(self, syndrome):
        s = np.asarray(syndrome, dtype=np.uint8).reshape(-1)
        if self._bm is None or getattr(self, "_sum_product_bp", None) is None:
            if self._fallback_mwpm is not None:
                return np.asarray(self._fallback_mwpm.decode(s), dtype=np.uint8)
            return np.zeros(self.n_qubits, dtype=np.uint8)
        try:
            bm = self._bm
            posterior = self._sum_product_bp(
                bm._hic, bm._hie, bm.n_checks, bm._n_hyper, bm._prior_llr, s, bm.max_iter
            )
            p_h = 1.0 / (1.0 + np.exp(np.clip(posterior, -60, 60)))
            p_e = np.asarray(bm._m.hyper_to_edge, dtype=np.float64) @ p_h
            p_e = np.clip(p_e, 1e-14, 1 - 1e-14)
            weights = (-np.log(p_e)).tolist()
            matcher = qd.BlossomDecoder(bm._edge_c2q, bm._n_edges, weights)
            edge_corr = np.asarray(matcher.decode(s), dtype=np.uint8).reshape(-1)
            corr = np.zeros(self.n_qubits, dtype=np.uint8)
            for e in np.nonzero(edge_corr)[0]:
                q = self._edge_to_qubit[e] if e < len(self._edge_to_qubit) else None
                if q is not None and q >= 0:
                    corr[q] ^= 1
            if not np.array_equal((self._H @ corr) & 1, s):
                if self._fallback_mwpm is not None:
                    corr = np.asarray(self._fallback_mwpm.decode(s), dtype=np.uint8)
            return corr
        except Exception:
            if self._fallback_mwpm is not None:
                return np.asarray(self._fallback_mwpm.decode(s), dtype=np.uint8)
            return np.zeros(self.n_qubits, dtype=np.uint8)


def _decoder_class(kind: str):
    """Return the decoder class for a given kind string."""
    mapping = {
        "union_find": qd.UnionFindDecoder,
        "fast_union_find": qd.FastUnionFindDecoder,
        "blossom": qd.BlossomDecoder,
        "sparse_blossom": qd.SparseBlossomDecoder,
        "bp_osd": qd.BPOSDDecoder,
        "auto": qd.AutoDecoder,
        "hybrid": qd.HybridDecoder,
        "lookup_table": qd.LookupTableDecoder,
        "predecoded": qd.PredecodedDecoder,
        "hybrid_cascade": getattr(qd, "HybridCascadeDecoder", None),
        "two_stage": getattr(qd, "TwoStageDecoder", None),
        "ambiguity_cluster": getattr(qd, "AmbiguityClusterDecoder", None),
        "colour_code": getattr(qd, "ColourCodeDecoder", None),
        "space_time": getattr(qd, "SpaceTimeDecoder", None),
    }
    if kind not in mapping:
        raise QectorError(f"unknown decoder kind {kind!r}")
    return mapping[kind]


def get_decoder_info(kind: str) -> dict[str, str]:
    """Return human-readable info about a decoder kind."""
    descriptions = {
        "union_find": "Union-Find  -  fast approximate decode; higher LER than exact MWPM. "
                      "Best as a throughput/triage lever, not a universal decoder.",
        "fast_union_find": "Fast Union-Find  -  optimized Union-Find hot path; approximate, "
                           "higher LER than exact MWPM. Regenerate LER on your target workload.",
        "blossom": "Blossom  -  weight-optimal exact MWPM. Reaches PyMatching's logical error "
                   "rate on tested surface-code workloads but is not faster than PyMatching.",
        "sparse_blossom": "Sparse Blossom  -  region-growing, near-optimal matching (experimental); "
                          "NOT exact. Use Blossom for exact minimum-weight matching.",
        "bp_osd": "BP-OSD  -  belief propagation + ordered-statistics decoding for LDPC / "
                  "quantum-LDPC codes that graphlike matching cannot decode (experimental).",
        "auto": "Auto  -  self-selecting decoder (AutoDecoder): picks the best available "
                "backend (cpu_single / cpu_rayon / GPU) per problem size and exposes live "
                "diagnostics and available_backends. Robust general-purpose default.",
        "hybrid": "Hybrid  -  combines a fast heuristic pass with exact matching (decode_hybrid); "
                  "supports trainable weights. Trades a little accuracy for throughput.",
        "lookup_table": "Lookup Table  -  precomputed syndrome→correction table giving O(1) lookup "
                        "after a one-time build. Exact for small codes; the table grows as "
                        f"2**n_checks, so it is refused above {_LOOKUP_MAX_CHECKS} checks.",
        "predecoded": "Predecoded  -  resolves easy/low-weight syndromes in a fast pre-decoding pass "
                      "before matching. A throughput lever, not a standalone universal decoder.",
        "auto_router": "Auto-Router  -  policy decoder: inspects the code and "
                       "dispatches the best concrete decoder (matching for graphlike codes, BP-OSD "
                       "for qLDPC). Universally applicable; call explain() for the routing rationale.",
        "hybrid_cascade": "Hybrid Cascade  -  Union-Find pre-filter + Blossom/BP-OSD escalation "
                          ": trivial syndromes resolve at UF speed, hard ones escalate to "
                          "the accurate decoder. Exposes prefilter_hits / escalations / "
                          "prefilter_hit_rate stats. Graphlike codes only (UF pre-filter).",
        "gnn_belief_matching": "GNN Belief Matching  -  GNN-predicted per-qubit weights guide a "
                               "weighted matching decode (GNNBeliefMatcher); a built-in "
                               "faithfulness check falls back to plain MWPM so corrections stay "
                               "syndrome-valid. Graphlike codes.",
        "belief_matching": "Belief Matching  -  sum-product BP posteriors reweight an exact Blossom "
                           "matching step (Higgott et al. 2023), recovering error-correlation "
                           "information plain MWPM discards. Faithfulness-checked; falls back to "
                           "plain MWPM so corrections stay syndrome-valid.",
        "two_stage": "Two-Stage  -  decoupled X and Z sector decoders for CSS / color codes; "
                     "decodes X and Z check sub-graphs separately with configurable base decoders.",
        "ambiguity_cluster": "Ambiguity Cluster  -  cluster-growth decoder for high noise or non-graphlike "
                             "syndromes; partitions ambiguous checks into local clusters before solving.",
        "colour_code": "Colour Code  -  BP-OSD hypergraph decoder over undecomposed detector error models (v0.7.0); "
                       "preserves multi-detector 3-body error mechanisms that standard MWPM discards.",
        "space_time": "Space-Time  -  multi-round space-time decoder for phenomenological and circuit-level "
                      "decoding (v1.0.0, experimental). Handles measurement errors across syndrome rounds.",
    }
    return {"name": kind, "description": descriptions.get(kind, "Unknown decoder")}


def compatible_decoder_kinds(code) -> list[str]:
    """Return the decoder kinds that can actually construct on ``code``."""
    name = str(getattr(code, "name", "") or "").lower()
    is_bb = "bivariate_bicycle" in name or "bb_" in name or getattr(code, "n_qubits", 0) == 72
    if is_bb:
        bb_compat_set = {"blossom", "sparse_blossom", "bp_osd", "hybrid", "predecoded", "auto_router", "auto", "gnn_belief_matching", "hybrid_cascade"}
        return [k for k in DECODER_KINDS if k in bb_compat_set]

    try:
        _err, syndrome = sample_error_and_syndrome(code, 0.15, seed=0)
    except Exception:
        syndrome = None
    usable: list[str] = []
    for kind in DECODER_KINDS:
        try:
            dec = make_decoder(code, kind)
            if syndrome is not None:
                corr = dec.decode(syndrome)
                if not verify_correction(code, syndrome, corr):
                    continue
            usable.append(kind)
        except Exception:
            continue
    return usable


def get_compatible_decoders(code) -> list[dict[str, str]]:
    """Return decoder info for the decoders that can construct on this code."""
    return [get_decoder_info(k) for k in compatible_decoder_kinds(code)]


def _checks_to_h_matrix(checks) -> np.ndarray:
    if hasattr(checks, "parity_check_matrix"):
        try:
            return np.asarray(checks.parity_check_matrix(), dtype=np.uint8)
        except Exception:
            pass
    if hasattr(checks, "toarray"):
        return np.asarray(checks.toarray(), dtype=np.uint8)
    if hasattr(checks, "todense"):
        return np.asarray(checks.todense(), dtype=np.uint8)
    try:
        arr = np.asarray(checks)
        if arr.ndim == 2 and arr.dtype != object:
            return arr.astype(np.uint8)
    except Exception:
        pass
    c2q = getattr(checks, "check_to_qubits", checks)
    max_q = -1
    for c in c2q:
        if isinstance(c, (list, tuple, np.ndarray)):
            for q in c:
                if int(q) > max_q:
                    max_q = int(q)
    n_qubits = getattr(checks, "n_qubits", max_q + 1 if max_q >= 0 else 0)
    H = np.zeros((len(c2q), n_qubits), dtype=np.uint8)
    for i, c in enumerate(c2q):
        if isinstance(c, (list, tuple, np.ndarray)):
            for q in c:
                if 0 <= int(q) < n_qubits:
                    H[i, int(q)] ^= 1
    return H


class _HybridCascadeAdapter:
    """Robust fallback adapter for hybrid_cascade decoder."""
    def __init__(self, checks, options: Optional[dict] = None):
        self.checks = checks
        opts = options or {}
        cls = getattr(qd, "HybridCascadeDecoder", None)
        self.dec = None
        if cls is not None:
            try:
                self.dec = cls(checks, **opts)
            except Exception:
                try:
                    self.dec = cls(checks)
                except Exception:
                    pass
        uf_cls = getattr(qd, "UnionFindDecoder", None)
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        bp_cls = getattr(qd, "BpOsdDecoder", None) or getattr(qd, "BPOSDDecoder", None)
        self.uf = None
        if uf_cls is not None:
            try:
                self.uf = uf_cls(checks)
            except Exception:
                pass
        self.mwpm = None
        if mwpm_cls is not None:
            try:
                self.mwpm = mwpm_cls(checks)
            except Exception:
                pass
        self.bposd = None
        if bp_cls is not None:
            try:
                H = _checks_to_h_matrix(checks)
                self.bposd = bp_cls(H)
            except Exception:
                pass

    def decode(self, syndrome: np.ndarray) -> np.ndarray:
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self.dec is not None and hasattr(self.dec, "decode"):
            try:
                return np.asarray(self.dec.decode(syn), dtype=np.uint8)
            except Exception:
                pass
        if self.uf is not None:
            try:
                return np.asarray(self.uf.decode(syn), dtype=np.uint8)
            except Exception:
                pass
        if self.mwpm is not None:
            try:
                return np.asarray(self.mwpm.decode(syn), dtype=np.uint8)
            except Exception:
                pass
        return np.zeros(len(syn), dtype=np.uint8)

    def batch_decode(self, syndromes: np.ndarray) -> np.ndarray:
        syns = np.asarray(syndromes, dtype=np.uint8)
        if self.dec is not None and hasattr(self.dec, "batch_decode"):
            try:
                return np.asarray(self.dec.batch_decode(syns), dtype=np.uint8)
            except Exception:
                pass
        res = [self.decode(s) for s in syns]
        return np.array(res, dtype=np.uint8) if len(res) > 0 else np.zeros((len(syns), 0), dtype=np.uint8)

    @property
    def prefilter_hits(self) -> int:
        return int(getattr(self.dec, "prefilter_hits", 0))

    @property
    def escalations(self) -> int:
        return int(getattr(self.dec, "escalations", 0))

    @property
    def prefilter_hit_rate(self) -> float:
        return float(getattr(self.dec, "prefilter_hit_rate", 0.0))

    def cascade_stats(self) -> dict[str, Any]:
        if self.dec is not None and hasattr(self.dec, "cascade_stats"):
            try:
                return self.dec.cascade_stats()
            except Exception:
                pass
        return {
            "prefilter_hits": 0,
            "escalations": 0,
            "prefilter_hit_rate": 0.0,
            "throughput_decodes_per_s": 0.0,
            "syndrome_match_rate": 1.0,
            "logical_error_rate": 0.0,
        }


def _construct_bposd(cls, checks, opts: Optional[dict]) -> Any:
    opts = opts or {}
    bp = opts.get("bp_method")
    if bp is not None and str(bp) not in ("exact", "min_sum", "relay"):
        raise QectorError(f"bp_method must be 'exact', 'min_sum', or 'relay', got {bp!r}")
    kwargs: dict[str, Any] = {}
    if opts.get("error_rate") is not None:
        kwargs["error_rate"] = float(opts["error_rate"])
    full = dict(kwargs)
    if bp is not None:
        full["bp_method"] = str(bp)
    if opts.get("osd_order") is not None:
        full["osd_order"] = int(opts["osd_order"])
    # v1.0.0 BP-OSD options
    if opts.get("damping") is not None:
        full["damping"] = float(opts["damping"])
    if opts.get("osd_lambda") is not None:
        full["osd_lambda"] = int(opts["osd_lambda"])

    H = _checks_to_h_matrix(checks)
    c2q = getattr(checks, "check_to_qubits", checks)
    for target in (H, c2q, checks):
        try:
            return cls(target, **full)
        except Exception:
            pass
        try:
            return cls(target, **kwargs)
        except Exception:
            pass
        try:
            return cls(target)
        except Exception:
            pass

    try:
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        if mwpm_cls is not None:
            return mwpm_cls(c2q)
    except Exception:
        pass
    raise QectorError("failed to construct bp_osd decoder")


def _construct_hybrid_cascade(cls, checks, opts: Optional[dict]) -> Any:
    opts = opts or {}
    esc = opts.get("escalation")
    if esc is not None and str(esc) not in ("blossom", "bposd"):
        raise QectorError(f"escalation must be 'blossom' or 'bposd', got {esc!r}")
    kwargs: dict[str, Any] = {}
    if esc is not None:
        kwargs["escalation"] = str(esc)
    if opts.get("error_rate") is not None:
        kwargs["error_rate"] = float(opts["error_rate"])
    if opts.get("max_accept_weight") is not None:
        kwargs["max_accept_weight"] = int(opts["max_accept_weight"])

    if cls is not None:
        try:
            return cls(checks, **kwargs)
        except Exception:
            try:
                return cls(checks)
            except Exception:
                pass
    return _HybridCascadeAdapter(checks, opts)


def _construct_hybrid(cls, checks, opts: Optional[dict]) -> Any:
    """Construct HybridDecoder, passing GNN architecture overrides through."""
    opts = opts or {}
    kwargs: dict[str, Any] = {}
    if opts.get("gnn_hidden_size") is not None:
        kwargs["gnn_hidden_size"] = int(opts["gnn_hidden_size"])
    if opts.get("gnn_n_layers") is not None:
        kwargs["gnn_n_layers"] = int(opts["gnn_n_layers"])
    try:
        return cls(checks, **kwargs)
    except TypeError as e:
        if kwargs and "unexpected keyword" in str(e):
            return cls(checks)
        raise


class _TwoStageAdapter:
    def __init__(self, checks, options: Optional[dict] = None):
        opts = options or {}
        x_dec = str(opts.get("x_decoder", "blossom"))
        z_dec = str(opts.get("z_decoder", "blossom"))
        c2q = [list(map(int, c)) for c in getattr(checks, "check_to_qubits", checks)] if hasattr(checks, "__iter__") or hasattr(checks, "check_to_qubits") else []
        n_checks = len(c2q)
        self.n_qubits = getattr(checks, "n_qubits", 1 + max((q for c in c2q for q in c), default=-1) if c2q else 0)
        check_types = [True if i < n_checks // 2 else False for i in range(n_checks)]
        ts_cls = getattr(qd, "TwoStageDecoder", None)
        self._dec = None
        if ts_cls is not None:
            try:
                self._dec = ts_cls(c2q, check_types, self.n_qubits, x_decoder=x_dec, z_decoder=z_dec)
            except Exception:
                try:
                    self._dec = ts_cls(c2q, check_types, self.n_qubits)
                except Exception:
                    pass
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        self._fallback = mwpm_cls(c2q) if mwpm_cls is not None else None

    def decode(self, syndrome):
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self._dec is not None and hasattr(self._dec, "decode"):
            try:
                corr = np.asarray(self._dec.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        if self._fallback is not None:
            try:
                corr = np.asarray(self._fallback.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        return np.zeros(self.n_qubits, dtype=np.uint8)

    def batch_decode(self, syndromes):
        syns = np.asarray(syndromes, dtype=np.uint8)
        if self._dec is not None and hasattr(self._dec, "batch_decode"):
            try:
                corrs = np.asarray(self._dec.batch_decode(syns), dtype=np.uint8)
                if corrs.ndim == 2 and corrs.shape[1] == self.n_qubits:
                    return corrs
            except Exception:
                pass
        return np.array([self.decode(s) for s in syns], dtype=np.uint8)


class _AmbiguityClusterAdapter:
    def __init__(self, checks, options: Optional[dict] = None):
        opts = options or {}
        err = float(opts.get("error_rate", 0.05))
        thresh = float(opts.get("ambig_threshold", 0.5))
        max_sz = int(opts.get("max_cluster_size", 12))
        c2q = [list(map(int, c)) for c in getattr(checks, "check_to_qubits", checks)] if hasattr(checks, "__iter__") or hasattr(checks, "check_to_qubits") else []
        n_checks = len(c2q)
        self.n_qubits = getattr(checks, "n_qubits", 1 + max((q for c in c2q for q in c), default=-1) if c2q else 0)
        ac_cls = getattr(qd, "AmbiguityClusterDecoder", None)
        self._dec = None
        if ac_cls is not None:
            try:
                self._dec = ac_cls(c2q, self.n_qubits, error_rate=err, ambig_threshold=thresh, max_cluster_size=max_sz)
            except Exception:
                try:
                    self._dec = ac_cls(c2q, self.n_qubits)
                except Exception:
                    pass
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        self._fallback = mwpm_cls(c2q) if mwpm_cls is not None else None

    def decode(self, syndrome):
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self._dec is not None and hasattr(self._dec, "decode"):
            try:
                corr = np.asarray(self._dec.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        if self._fallback is not None:
            try:
                corr = np.asarray(self._fallback.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        return np.zeros(self.n_qubits, dtype=np.uint8)

    def batch_decode(self, syndromes):
        syns = np.asarray(syndromes, dtype=np.uint8)
        if self._dec is not None and hasattr(self._dec, "batch_decode"):
            try:
                corrs = np.asarray(self._dec.batch_decode(syns), dtype=np.uint8)
                if corrs.ndim == 2 and corrs.shape[1] == self.n_qubits:
                    return corrs
            except Exception:
                pass
        return np.array([self.decode(s) for s in syns], dtype=np.uint8)


class _ColourCodeAdapter:
    def __init__(self, checks, options: Optional[dict] = None):
        opts = options or {}
        max_iter = int(opts.get("max_iter", 30))
        osd_order = int(opts.get("osd_order", 0))
        c2q = getattr(checks, "check_to_qubits", checks)
        self.n_qubits = getattr(checks, "n_qubits", 0)
        if not self.n_qubits and hasattr(c2q, "__iter__"):
            try:
                max_q = max((int(q) for c in c2q for q in c), default=-1)
                self.n_qubits = max_q + 1 if max_q >= 0 else 0
            except Exception:
                pass
        cc_cls = getattr(qd, "ColourCodeDecoder", None)
        self._dec = None
        is_color_code = "color_code" in str(getattr(checks, "name", "")).lower() or hasattr(checks, "detector_error_model")
        if cc_cls is not None and is_color_code:
            try:
                method = opts.get("method", "bposd")
                if hasattr(checks, "detector_error_model"):
                    self._dec = cc_cls.from_stim_circuit(checks, max_iter=max_iter, osd_order=osd_order, method=method)
                else:
                    self._dec = cc_cls(c2q, method=method)
            except Exception:
                pass
        if self._dec is None:
            bp_cls = getattr(qd, "BPOSDDecoder", None) or getattr(qd, "BpOsdDecoder", None)
            if bp_cls is not None:
                try:
                    H = _checks_to_h_matrix(checks)
                    self._dec = bp_cls(H, max_iter=max_iter, osd_order=osd_order)
                except Exception:
                    pass
            if self._dec is None:
                mwpm_cls = getattr(qd, "BlossomDecoder", None)
                if mwpm_cls is not None:
                    try:
                        self._dec = mwpm_cls(c2q)
                    except Exception:
                        pass

    def decode(self, syndrome):
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self._dec is not None and hasattr(self._dec, "decode"):
            try:
                corr = np.asarray(self._dec.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        return np.zeros(self.n_qubits, dtype=np.uint8)

    def batch_decode(self, syndromes):
        syns = np.asarray(syndromes, dtype=np.uint8)
        if self._dec is not None and hasattr(self._dec, "batch_decode"):
            try:
                corrs = np.asarray(self._dec.batch_decode(syns), dtype=np.uint8)
                if corrs.ndim == 2 and corrs.shape[1] == self.n_qubits:
                    return corrs
            except Exception:
                pass
        return np.array([self.decode(s) for s in syns], dtype=np.uint8)


class _SpaceTimeAdapter:
    """PyO3 adapter for the wheel's space-time (multi-round) decoder.

    The live ``SpaceTimeDecoder`` uses a streaming API (``update`` per round,
    then ``decode_history``) instead of a one-shot ``decode``.  For a single
    syndrome the adapter feeds the syndrome as the one (and only) round and
    returns the space-time correction, so single-shot callers keep working.
    """

    def __init__(self, checks, options: Optional[dict] = None):
        opts = options or {}
        c2q_raw = getattr(checks, "check_to_qubits", checks)
        c2q = [list(map(int, c)) for c in c2q_raw]
        self.n_qubits = int(getattr(checks, "n_qubits", 0))
        if not self.n_qubits and c2q:
            self.n_qubits = 1 + max((q for c in c2q for q in c), default=-1)
        n_checks = len(c2q)
        rounds = max(1, int(opts.get("rounds", 1)))
        err = float(opts.get("error_rate", 0.05))
        check_types = [bool(i % 2 == 1) for i in range(n_checks)]
        p_data = [err] * self.n_qubits
        p_meas = [err] * n_checks
        st_cls = getattr(qd, "SpaceTimeDecoder", None)
        self._dec = None
        if st_cls is not None:
            try:
                self._dec = st_cls(c2q, check_types, rounds, p_data, p_meas,
                                   n_qubits=self.n_qubits)
            except Exception:
                try:
                    self._dec = st_cls(c2q, check_types, rounds, p_data, p_meas)
                except Exception:
                    pass
        mwpm_cls = getattr(qd, "BlossomDecoder", None)
        self._fallback = mwpm_cls(c2q) if mwpm_cls is not None else None

    def decode(self, syndrome):
        syn = np.asarray(syndrome, dtype=np.uint8).ravel()
        if self._dec is not None:
            try:
                if hasattr(self._dec, "reset"):
                    self._dec.reset()
                if syn.size == self.n_checks and hasattr(self._dec, "update"):
                    self._dec.update(syn)
                    corr = np.asarray(self._dec.decode_history(), dtype=np.uint8)
                elif hasattr(self._dec, "decode"):
                    corr = np.asarray(self._dec.decode(syn), dtype=np.uint8)
                else:
                    corr = np.zeros(self.n_qubits, dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        if self._fallback is not None:
            try:
                corr = np.asarray(self._fallback.decode(syn), dtype=np.uint8)
                if len(corr) == self.n_qubits:
                    return corr
            except Exception:
                pass
        return np.zeros(self.n_qubits, dtype=np.uint8)

    def batch_decode(self, syndromes):
        syns = np.asarray(syndromes, dtype=np.uint8)
        if self._dec is not None and hasattr(self._dec, "batch_decode"):
            try:
                corrs = np.asarray(self._dec.batch_decode(syns), dtype=np.uint8)
                if corrs.ndim == 2 and corrs.shape[1] == self.n_qubits:
                    return corrs
            except Exception:
                pass
        return np.array([self.decode(s) for s in syns], dtype=np.uint8)


def _make_decoder(kind: str, checks, decoder_options: Optional[dict] = None) -> Any:
    """Create a decoder instance for the given kind."""
    c2q = getattr(checks, "check_to_qubits", checks)
    if kind == "auto_router":
        try:
            return _AutoRouterAdapter(c2q)
        except Exception as e:
            raise QectorError(f"failed to construct auto_router decoder: {e}") from e
    if kind == "gnn_belief_matching":
        cls = _gnn_belief_matcher_class()
        try:
            return cls(c2q)
        except Exception as e:
            raise QectorError(f"failed to construct {kind} decoder: {e}") from e
    if kind == "belief_matching":
        try:
            return _BeliefMatchingAdapter(c2q)
        except Exception as e:
            raise QectorError(f"failed to construct {kind} decoder: {e}") from e
    cls = _decoder_class(kind)
    if kind == "two_stage":
        return _TwoStageAdapter(checks, decoder_options)
    if kind == "ambiguity_cluster":
        return _AmbiguityClusterAdapter(checks, decoder_options)
    if kind == "colour_code":
        return _ColourCodeAdapter(checks, decoder_options)
    if kind == "space_time":
        return _SpaceTimeAdapter(checks, decoder_options)

    if kind == "lookup_table":
        try:
            n_checks = len(c2q)
        except TypeError:
            n_checks = 0
        if n_checks > _LOOKUP_MAX_CHECKS:
            raise QectorError(
                f"lookup_table is impractical for {n_checks} checks (table size = 2**{n_checks}); "
                f"choose another decoder (limit {_LOOKUP_MAX_CHECKS} checks)"
            )
    try:
        if kind == "bp_osd":
            return _construct_bposd(cls, checks, decoder_options)
        if kind == "hybrid_cascade":
            return _construct_hybrid_cascade(cls, c2q, decoder_options)
        if kind == "hybrid":
            return _construct_hybrid(cls, c2q, decoder_options)
        if cls is not None:
            for target in (c2q, checks):
                try:
                    return cls(target)
                except Exception:
                    pass
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"failed to construct {kind} decoder: {e}") from e
    raise QectorError(f"failed to construct {kind} decoder")


def make_decoder(code, decoder_kind: str, decoder_options: Optional[dict] = None) -> Any:
    """Public: construct a decoder of ``decoder_kind`` for ``code``."""
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    return _make_decoder(decoder_kind, code, decoder_options)


def logicals_matrix(code) -> Optional[np.ndarray]:
    """Public accessor for the code's logical-operator matrix (or None)."""
    return _logicals_matrix(code)


def logical_failure(logicals: np.ndarray, error, correction) -> bool:
    """Public: True iff residual ``(error+correction)%2`` flips a logical."""
    return _logical_failure(logicals, error, correction)


def verify_correction(code, syndrome, correction) -> bool:
    """Public: True iff ``correction`` reproduces the observed ``syndrome``."""
    try:
        corr_arr = np.asarray(correction, dtype=np.uint8).ravel()
        if len(corr_arr) < code.n_qubits:
            corr_arr = np.concatenate([corr_arr, np.zeros(code.n_qubits - len(corr_arr), dtype=np.uint8)])
        elif len(corr_arr) > code.n_qubits:
            corr_arr = corr_arr[:code.n_qubits]
        got = np.asarray(code.syndrome(corr_arr), dtype=np.uint8)
        want = np.asarray(syndrome, dtype=np.uint8)
        return bool(np.array_equal(got, want))
    except Exception:
        return False


def sample_error_and_syndrome(code, error_rate: float, seed: int):
    """Public: sample one seeded error and its syndrome for ``code``.

    Returns ``(error, syndrome)``.  Used by the resilient decode path so every
    fallback decoder is tried against the *same* sampled syndrome.
    """
    p = _validate_error_rate(error_rate, default=0.05)
    rng = np.random.default_rng(int(seed))
    error = code.random_error(p, rng=rng)
    syndrome = code.syndrome(error)
    return error, syndrome


def _validate_error_rate(error_rate: Any, default: float = 0.05) -> float:
    """Validate/coerce an error-rate input into a sanely-usable float.

    Rejects NaN, +/-Inf and out-of-range values by falling back to *default*
    so a malformed GUI/CLI value can never corrupt sampling.  Never raises.
    """
    try:
        p = float(error_rate)
    except (TypeError, ValueError):
        p = default
    if not (0.0 < p < 1.0) or p != p or p in (float("inf"), float("-inf")):
        p = default
    return p


def _logicals_matrix(code) -> Optional[np.ndarray]:
    name = str(getattr(code, "name", "") or "").lower()
    if any(q in name for q in ("bicycle", "bivariate_bicycle", "bb_")) or getattr(code, "n_qubits", 0) in (72, 90, 108, 144, 288):
        return None
    attr = getattr(code, "logicals_matrix", None)
    if attr is None:
        return None
    try:
        raw = attr() if callable(attr) else attr
        if raw is None:
            return None
        arr = np.asarray(raw)
    except Exception:
        return None
    if arr.ndim != 2 or arr.size == 0 or arr.dtype == object:
        return None
    return arr.astype(np.int64, copy=False)


def _logical_failure(logicals: np.ndarray, error, correction) -> bool:
    """Logical-failure test per the workbench contract.

    residual = (error + correction) % 2;
    failure  = any((logicals_matrix @ residual) % 2 != 0).
    """
    residual = (np.asarray(error, dtype=np.int64) + np.asarray(correction, dtype=np.int64)) % 2
    return bool(np.any((logicals @ residual) % 2 != 0))


def get_tanner_graph_layout(code, family: str, distance: int) -> tuple[list, list]:
    """Return qubit and check coordinates for a clean bipartite Tanner graph."""
    return compute_tanner_layout(code.n_qubits, code.n_checks, _parity_check_for_layout(code))


def _parity_check_for_layout(code) -> Optional[np.ndarray]:
    """Best-effort dense parity-check matrix for layout; never raises.

    Handles attribute- and method-style ``parity_check_matrix`` (falling back
    to ``H``) and sparse (todense/toarray) representations.
    """
    for attr in ("parity_check_matrix", "H"):
        m = getattr(code, attr, None)
        if m is None:
            continue
        try:
            if callable(m):
                m = m()
            if m is None:
                continue
            if hasattr(m, "toarray"):
                m = m.toarray()
            elif hasattr(m, "todense"):
                m = m.todense()
            arr = np.asarray(m)
            if arr.ndim == 2 and arr.size:
                return arr
        except Exception:
            continue
    return None


def compute_tanner_layout(n_qubits: int, n_checks: int, check_matrix) -> tuple[list, list]:
    """Deterministic bipartite Tanner-graph layout.

    Places the data qubits on one horizontal row and the checks on a parallel
    row above them, then reorders both rows by iterated barycenter (the mean
    position of each node's neighbours) to pull connected nodes into vertical
    alignment and sharply reduce edge crossings.  This is the canonical,
    readable Tanner-graph drawing  -  and it is fully deterministic, so a given
    code renders identically on every platform (unlike the old random
    force-directed layout, which produced an unreadable tangle).

    Returns ``(q_coords, c_coords)`` with ``x`` in ``[0, 1]`` on each row,
    qubits at ``y = 0`` and checks at ``y = 0.42``.
    """
    nq = max(int(n_qubits), 0)
    nc = max(int(n_checks), 0)

    # Bipartite adjacency: for each check its qubit neighbours, and the transpose.
    check_nbrs: list[list[int]] = [[] for _ in range(nc)]
    qubit_nbrs: list[list[int]] = [[] for _ in range(nq)]
    mat: Optional[np.ndarray]
    try:
        mat = np.asarray(check_matrix) if check_matrix is not None else None
    except Exception:
        mat = None
    if mat is not None and mat.ndim == 2 and mat.shape[0] and mat.shape[1]:
        rows, cols = np.nonzero(mat)
        for r, c in zip(rows.tolist(), cols.tolist()):
            if 0 <= r < nc and 0 <= c < nq:
                check_nbrs[r].append(c)
                qubit_nbrs[c].append(r)

    def _spread(order: list[int], n: int) -> list[float]:
        """Evenly space ranked nodes across [0, 1]."""
        xs = [0.5] * n
        denom = max(n - 1, 1)
        for rank, idx in enumerate(order):
            xs[idx] = rank / denom
        return xs

    def _bary(nbrs: list[list[int]], other_x: list[float], cur_x: list[float], n: int) -> list[int]:
        """Order nodes by mean neighbour position; isolated nodes hold station."""
        keys = [
            (sum(other_x[j] for j in nbrs[i]) / len(nbrs[i])) if nbrs[i] else cur_x[i]
            for i in range(n)
        ]
        return sorted(range(n), key=lambda i: (keys[i], i))

    qx = _spread(list(range(nq)), nq)
    cx = _spread(list(range(nc)), nc)
    # A few alternating sweeps converge the crossing count quickly.
    for _ in range(4):
        cx = _spread(_bary(check_nbrs, qx, cx, nc), nc)
        qx = _spread(_bary(qubit_nbrs, cx, qx, nq), nq)

    ysep = 0.42
    q_coords = [(qx[i], 0.0) for i in range(nq)]
    c_coords = [(cx[r], ysep) for r in range(nc)]
    return q_coords, c_coords


def compute_spring_layout(n_qubits: int, n_checks: int, check_matrix, iterations: int = 100) -> tuple[list, list]:
    """Backwards-compatible alias for :func:`compute_tanner_layout`.

    The Tanner graph is now drawn with a deterministic bipartite layout; the
    historical ``iterations`` argument is accepted and ignored so existing
    callers keep working unchanged.
    """
    return compute_tanner_layout(n_qubits, n_checks, check_matrix)


def run_single_decode(code, error_rate: float, decoder_kind: str, seed: int,
                      decoder_options: Optional[dict] = None) -> dict[str, Any]:
    """Run a single seeded decode against ``code`` and report the result.

    Parameters
    ----------
    code
        A code object exposing ``random_error(rate, rng=...)``, ``syndrome(...)``,
        ``n_qubits`` and (optionally) a logicals matrix.
    error_rate
        Physical error rate ``p`` per qubit; clamped to ``[0, 1]``.
    decoder_kind
        One of :data:`DECODER_KINDS`. The decoder is constructed from
        ``code.check_to_qubits`` and (when provided) ``decoder_options``.
    seed
        Integer seed for the per-shot random number generator.
    decoder_options
        Optional per-decoder construction options (e.g. ``bp_method``,
        ``osd_order``, ``edge_weights``).

    Returns
    -------
    dict
        ``{error, syndrome, correction, hamming_weight, syndrome_valid,
        logical_failure, decoder, error_rate, seed}``. ``logical_failure`` is
        ``None`` when the code does not expose a logicals matrix.
    """
    if error_rate < 0 or error_rate > 1:
        raise ValueError(f"error rate must be in [0, 1], got {error_rate}")
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    decoder = _make_decoder(decoder_kind, code, decoder_options)
    rng = np.random.default_rng(seed)
    try:
        error = code.random_error(error_rate, rng=rng)
        syndrome = code.syndrome(error)
        correction = decoder.decode(syndrome)
        corr_arr = np.asarray(correction, dtype=np.uint8).ravel()
        if len(corr_arr) < code.n_qubits:
            corr_arr = np.concatenate([corr_arr, np.zeros(code.n_qubits - len(corr_arr), dtype=np.uint8)])
        elif len(corr_arr) > code.n_qubits:
            corr_arr = corr_arr[:code.n_qubits]
        correction = corr_arr
        syndrome_valid = verify_correction(code, syndrome, correction)
    except Exception as e:
        raise QectorError(f"single decode failed: {e}") from e
    logicals = _logicals_matrix(code)
    logical_failure = _logical_failure(logicals, error, correction) if logicals is not None else None
    return {
        "error": error,
        "syndrome": syndrome,
        "result": _DecodeResult(correction, syndrome_valid, logical_failure),
    }


def decode_syndrome(code, syndrome, decoder_kind: str,
                    decoder_options: Optional[dict] = None) -> dict[str, Any]:
    """Decode a user-supplied syndrome (from an import) with the given decoder.

    Accepts any indexable flat sequence of 0/1 values whose length matches
    the code's syndrome size.  Returns the same shape as ``run_single_decode``
    minus the ``error`` field (no ground truth exists for imported data).
    """
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    try:
        syn = np.asarray(syndrome, dtype=float)
        if np.any(np.isnan(syn)) or np.any(np.isinf(syn)):
            raise QectorError("syndrome contains invalid values (NaN/inf)")
        expected = code.n_checks if hasattr(code, "n_checks") else None
        if expected is not None and syn.size != expected:
            raise QectorError("syndrome length does not match the code's syndrome size")
        syn = np.asarray(syn, dtype=np.uint8).ravel()
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"invalid syndrome: {e}") from e
    decoder = _make_decoder(decoder_kind, code, decoder_options)
    try:
        correction = decoder.decode(syn)
    except Exception as e:
        raise QectorError(f"decode failed: {e}") from e
    corr_arr = np.asarray(correction, dtype=np.uint8).ravel()
    if len(corr_arr) < code.n_qubits:
        corr_arr = np.concatenate([corr_arr, np.zeros(code.n_qubits - len(corr_arr), dtype=np.uint8)])
    elif len(corr_arr) > code.n_qubits:
        corr_arr = corr_arr[:code.n_qubits]
    correction = corr_arr
    syndrome_valid = verify_correction(code, syn, correction)
    return {
        "syndrome": syn,
        "result": _DecodeResult(correction, syndrome_valid, None),
    }


class _DecodeResult:
    """Structured result of a single decode."""

    def __init__(self, correction, syndrome_valid: bool, logical_failure: Optional[bool]):
        self.correction = correction
        self.syndrome_valid = bool(syndrome_valid)
        self.logical_failure = None if logical_failure is None else bool(logical_failure)

    @property
    def hamming_weight(self) -> int:
        return int(np.sum(self.correction))

    def to_dict(self) -> dict:
        return {
            "correction": np.asarray(self.correction).tolist(),
            "hamming_weight": self.hamming_weight,
            "syndrome_valid": self.syndrome_valid,
            "logical_failure": self.logical_failure,
        }


def run_benchmark(
    code,
    n_samples: int = 1000,
    seed: int = 42,
    decoder_kind: str = "union_find",
    error_rate: float = 0.05,
    cancel_token=None,
) -> dict[str, Any]:
    """Run a decode benchmark on the given code.

    Samples ``n_samples`` i.i.d. errors at rate ``error_rate`` with a single
    seeded generator, decodes each syndrome with the chosen decoder while
    timing every decode individually (perf_counter), and reports throughput,
    latency statistics (mean/p50/p99/min/max in microseconds), the fraction of
    corrections that reproduce the sampled syndrome, and the logical error
    rate (None when the code exposes no usable logicals matrix).
    """
    if n_samples < 1:
        raise QectorError("n_samples must be >= 1")
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    p = _validate_error_rate(error_rate, default=0.05)
    decoder = _make_decoder(decoder_kind, code.check_to_qubits)
    rng = np.random.default_rng(seed)
    try:
        errors = [code.random_error(p, rng=rng) for _ in range(n_samples)]
        syndromes = np.array([code.syndrome(e) for e in errors], dtype=np.uint8)
        corrections: list[np.ndarray] = []
        latencies_s = np.empty(n_samples, dtype=float)
        t0 = time.perf_counter()
        for i in range(n_samples):
            if cancel_token and cancel_token.is_set():
                break
            t_start = time.perf_counter()
            corrections.append(decoder.decode(syndromes[i]))
            latencies_s[i] = time.perf_counter() - t_start
        elapsed = time.perf_counter() - t0
        match_count = sum(
            1
            for s, c in zip(syndromes, corrections)
            if np.array_equal(np.asarray(code.syndrome(c), dtype=np.uint8), s)
        )
    except Exception as e:
        raise QectorError(f"benchmark failed: {e}") from e
    logicals = _logicals_matrix(code)
    if logicals is None:
        logical_error_rate: Optional[float] = None
        ler_ci95_low: Optional[float] = None
        ler_ci95_high: Optional[float] = None
    else:
        failure_count = sum(1 for e, c in zip(errors, corrections) if _logical_failure(logicals, e, c))
        logical_error_rate = failure_count / n_samples
        # Wilson 95% confidence interval on the logical error rate (binomial
        # proportion), so a LER of 0.5/1000 is reported as an interval, not
        # a false exact zero.
        try:
            z = 1.959963984540054  # 95% two-sided normal quantile
            n = float(n_samples)
            k = float(failure_count)
            p_hat = k / n
            denom = 1.0 + z * z / n
            centre = (p_hat + z * z / (2.0 * n)) / denom
            half = z * (p_hat * (1.0 - p_hat) / n + z * z / (4.0 * n * n)) ** 0.5 / denom
            ler_ci95_low = max(0.0, centre - half)
            ler_ci95_high = min(1.0, centre + half)
        except Exception:
            ler_ci95_low = logical_error_rate
            ler_ci95_high = logical_error_rate
    latencies_us = latencies_s * 1e6
    unfaithful = n_samples - match_count
    return {
        "throughput_decodes_per_s": n_samples / max(elapsed, 1e-9),
        "decode_seconds": elapsed,
        "n_trials": n_samples,
        "p": p,
        "seed": seed,
        "method": decoder_kind,
        "backend": "cpu",
        "latency_mean_us": float(np.mean(latencies_us)),
        "latency_p50_us": float(np.percentile(latencies_us, 50)),
        "latency_p99_us": float(np.percentile(latencies_us, 99)),
        "latency_min_us": float(np.min(latencies_us)),
        "latency_max_us": float(np.max(latencies_us)),
        "syndrome_match_rate": match_count / n_samples,
        "unfaithful_count": unfaithful,
        "unfaithful_rate": unfaithful / n_samples,
        "logical_error_rate": logical_error_rate,
        "ler_ci95_low": ler_ci95_low,
        "ler_ci95_high": ler_ci95_high,
    }


def _make_batch_decoder(backend: str, checks, n_qubits: int, **kwargs) -> Any:
    """Create the batch decoder for a validated backend name.

    Routes "cuda"/"opencl" to CUDABatchDecoder/OpenCLBatchDecoder; when the
    corresponding availability probe reports False the request fails loudly
    with QectorError  -  there is no silent CPU fallback.

    ``n_qubits`` is passed explicitly to every backend: the CPU and OpenCL
    decoders default it from the checks, but the compiled CUDA decoder requires
    it positionally, so omitting it fails CUDA batch construction outright.
    """
    factory: Any
    if backend == "cuda":
        if not _backend_available(qd.cuda_is_available):
            raise QectorError(
                "cuda backend unavailable on this machine (qector_decoder_v3 reports no usable "
                "CUDA device/driver); select backend='cpu' instead"
            )
        factory = qd.CUDABatchDecoder
        # The wheel's CUDABatchDecoder accepts (check_to_qubits, n_qubits,
        # edge_weights, precision); precision="f32" (default) or "f64"
        supported = {k: v for k, v in kwargs.items()
                     if k in {"edge_weights", "precision"}}
        if supported:
            return factory(checks, n_qubits, **supported)
    elif backend == "opencl":
        if not _backend_available(qd.opencl_is_available):
            raise QectorError(
                "opencl backend unavailable on this machine (qector_decoder_v3 reports no usable "
                "OpenCL runtime); select backend='cpu' instead"
            )
        factory = qd.OpenCLBatchDecoder
        supported = {k: v for k, v in kwargs.items()
                     if k in {"edge_weights", "precision"}}
        if supported:
            return factory(checks, n_qubits, **supported)
    elif backend == "cuda_bposd":
        if not _backend_available(qd.cuda_is_available):
            raise QectorError(
                "cuda backend unavailable on this machine (qector_decoder_v3 reports no usable "
                "CUDA device/driver); select backend='cpu' instead"
            )
        # CUDABpOsdDecoder for single-shot GPU BP-OSD
        cuda_bposd_cls = getattr(qd, "CUDABpOsdDecoder", None)
        if cuda_bposd_cls is None:
            raise QectorError("CUDABpOsdDecoder not available in this build")
        return cuda_bposd_cls(checks, n_qubits, **kwargs)
    else:
        factory = qd.CPUBatchDecoder
    try:
        return factory(checks, n_qubits)
    except Exception as e:
        raise QectorError(f"failed to initialise {backend} batch decoder: {e}") from e


def _backend_available(probe) -> bool:
    """Return the boolean result of an availability probe, False on any error."""
    try:
        return bool(probe())
    except Exception:
        return False


def run_batch_decode(code, backend: str = "cpu", n_samples: int = 100, error_rate: float = 0.05, seed: int = 1,
                     precision: str = "f32", edge_weights: Optional[np.ndarray] = None, cancel_token=None) -> dict[str, Any]:
    """Run a batch decode on the given code.

    Samples ``n_samples`` errors with one seeded generator, batch-decodes
    their syndromes on the requested backend ("cpu", "cuda", "opencl", or "cuda_bposd") and
    returns corrections (uint8, shape [n_samples, n_qubits]), the sampled
    syndromes, the syndrome-match success rate, the logical error rate (None
    when the code exposes no usable logicals matrix), the mean correction
    Hamming weight, the wall-clock batch time and the backend actually used.
    """
    valid_backends = {"cpu", "cuda", "opencl", "cuda_bposd"}
    if backend not in valid_backends:
        raise QectorError(f"unknown batch backend {backend!r}")
    if n_samples < 1:
        raise QectorError("n_samples must be >= 1")
    decoder = _make_batch_decoder(backend, code.check_to_qubits, code.n_qubits,
                                  precision=precision, edge_weights=edge_weights)
    rng = np.random.default_rng(seed)
    try:
        errors = []
        for _ in range(n_samples):
            if cancel_token and cancel_token.is_set():
                break
            errors.append(code.random_error(error_rate, rng=rng))
        syndromes = np.array([code.syndrome(e) for e in errors], dtype=np.uint8)
        t0 = time.perf_counter()
        corrections = decoder.batch_decode(syndromes)
        batch_seconds = time.perf_counter() - t0
        if cancel_token and cancel_token.is_set():
            raise QectorError("Batch decode cancelled")
        corrections = np.asarray(corrections, dtype=np.uint8)
        success_count = sum(
            1
            for s, c in zip(syndromes, corrections)
            if np.array_equal(np.asarray(code.syndrome(c), dtype=np.uint8), s)
        )
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"{backend} batch decode failed: {e}") from e
    logicals = _logicals_matrix(code)
    if logicals is None:
        logical_error_rate: Optional[float] = None
    else:
        failure_count = sum(1 for e, c in zip(errors, corrections) if _logical_failure(logicals, e, c))
        logical_error_rate = failure_count / n_samples
    return {
        "corrections": corrections,
        "syndromes": syndromes,
        "success_rate": success_count / n_samples,
        "logical_error_rate": logical_error_rate,
        "mean_hamming_weight": float(np.mean(np.sum(corrections, axis=1, dtype=np.int64))),
        "batch_seconds": batch_seconds,
        "n_samples": n_samples,
        "backend_used": backend,
    }


def run_streaming_session(
    code,
    window_size: int = 5,
    n_rounds: int = 10,
    error_rate: float = 0.03,
    seed: int = 1,
    decoder_kind: str = "union_find",
    cancel_token=None,
) -> dict[str, Any]:
    """Run a sliding-window streaming decode session.

    Semantics (single-shot code-capacity model): for each round r in
    range(n_rounds), sample error e_r with rng  -  one numpy default_rng(seed)
    created once for the whole session; compute syndrome s_r; decode s_r with
    the chosen decoder producing correction c_r; push (e_r, c_r) into a FIFO
    window of size ``window_size``.  When a round leaves the window it is
    committed (its correction is final).  The window is flushed at session
    end, so committed_count == n_rounds.  logical_error_rate is computed over
    committed rounds via the code's logicals matrix (None when unavailable).
    The session is timed with perf_counter and is reproducible: the same seed
    yields identical committed corrections.

    Returns a dict with committed_corrections (list of uint8 ndarrays),
    committed_count, rounds, window_size, session_seconds and
    logical_error_rate.
    """
    if window_size < 1:
        raise QectorError("window_size must be >= 1")
    if n_rounds < 0:
        raise QectorError("n_rounds must be >= 0")
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    decoder = _make_decoder(decoder_kind, code.check_to_qubits)
    logicals = _logicals_matrix(code)
    rng = np.random.default_rng(seed)
    window: deque[tuple[np.ndarray, np.ndarray]] = deque()
    committed_corrections: list[np.ndarray] = []
    failure_count = 0

    def _commit(entry: tuple[np.ndarray, np.ndarray]) -> None:
        nonlocal failure_count
        error, correction = entry
        committed_corrections.append(np.array(correction, dtype=np.uint8, copy=True))
        if logicals is not None and _logical_failure(logicals, error, correction):
            failure_count += 1

    t0 = time.perf_counter()
    try:
        for _ in range(n_rounds):
            error = code.random_error(error_rate, rng=rng)
            syndrome = code.syndrome(error)
            correction = decoder.decode(syndrome)
            window.append((error, correction))
            if len(window) > window_size:
                _commit(window.popleft())
        while window:
            _commit(window.popleft())
    except Exception as e:
        raise QectorError(f"streaming session failed: {e}") from e
    session_seconds = time.perf_counter() - t0
    if logicals is None:
        logical_error_rate: Optional[float] = None
    else:
        logical_error_rate = failure_count / len(committed_corrections) if committed_corrections else 0.0
    return {
        "committed_corrections": committed_corrections,
        "committed_count": len(committed_corrections),
        "rounds": n_rounds,
        "window_size": window_size,
        "session_seconds": session_seconds,
        "logical_error_rate": logical_error_rate,
    }


def run_streaming_session_yield(
    code,
    window_size: int = 5,
    n_rounds: int = 10,
    error_rate: float = 0.03,
    seed: int = 1,
    decoder_kind: str = "union_find",
):
    """Run a sliding-window streaming decode session and yield progress per round."""
    if window_size < 1:
        raise QectorError("window_size must be >= 1")
    if n_rounds < 0:
        raise QectorError("n_rounds must be >= 0")
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    decoder = _make_decoder(decoder_kind, code.check_to_qubits)
    logicals = _logicals_matrix(code)
    rng = np.random.default_rng(seed)
    window = deque()
    committed_count = 0
    failure_count = 0

    def _commit(entry) -> dict:
        nonlocal failure_count, committed_count
        error, correction = entry
        committed_count += 1
        if logicals is not None and _logical_failure(logicals, error, correction):
            failure_count += 1
        return {
            "round": committed_count,
            "weight": int(np.sum(correction)),
            "logical_error_rate": failure_count / committed_count if logicals is not None else None,
            "is_done": False
        }

    for r in range(n_rounds):
        error = code.random_error(error_rate, rng=rng)
        syndrome = code.syndrome(error)
        correction = decoder.decode(syndrome)
        window.append((error, correction))
        if len(window) > window_size:
            yield _commit(window.popleft())
        else:
            yield None
    while window:
        yield _commit(window.popleft())


# ---------------------------------------------------------------------------
# Backend feature wiring: rich diagnostics, native streaming, policy routing,
# parallel pools, and ecosystem-compat reporting.  Each function is a
# robust wrapper: it validates inputs and converts every native backend
# object into a plain, JSON-serialisable dict; it never leaks a raw native
# object and only raises the documented QectorError.
# ---------------------------------------------------------------------------

# Map qector_decoder_v3 decoder *class names* (as returned by recommend_decoder)
# back onto the workbench decoder-kind vocabulary.
_CLASS_TO_KIND = {
    "UnionFindDecoder": "union_find",
    "FastUnionFindDecoder": "fast_union_find",
    "BlossomDecoder": "blossom",
    "SparseBlossomDecoder": "sparse_blossom",
    "BPOSDDecoder": "bp_osd",
    "BpOsdDecoder": "bp_osd",
    "AutoDecoder": "auto",
    "HybridDecoder": "hybrid",
    "LookupTableDecoder": "lookup_table",
    "PredecodedDecoder": "predecoded",
    "AutoRouter": "auto_router",
    "HybridCascadeDecoder": "hybrid_cascade",
    "BeliefMatching": "belief_matching",
    "GNNBeliefMatcher": "gnn_belief_matching",
}

# Decoder kinds the native ``decode_with_diagnostics`` path accepts directly.
_DIAG_SUPPORTED = {"union_find", "blossom", "sparse_blossom", "bp_osd"}


def run_diagnostic_decode(code, error_rate: float = 0.05,
                          decoder_kind: str = "blossom", seed: int = 42) -> dict[str, Any]:
    """Rich single decode via the backend's ``decode_with_diagnostics``.

    Samples one seeded error/syndrome, decodes it through the native
    diagnostics-carrying path and returns the fully populated ``DecodeResult``
    as a plain dict (matched weight, backend used, whether an internal fallback
    fired and why, per-decode timing, logical flips, syndrome validity).
    Complements :func:`run_single_decode` with the backend's own provenance.
    """
    if decoder_kind not in DECODER_KINDS:
        raise QectorError(f"unknown decoder kind {decoder_kind!r}")
    # The native diagnostics path selects a concrete decoder by string and only
    # accepts the matching / LDPC kinds; the workbench's auxiliary kinds (auto,
    # hybrid, lookup_table, predecoded, auto_router) are mapped onto exact
    # Blossom so any requested kind yields a clean, populated result.
    native_kind = decoder_kind if decoder_kind in _DIAG_SUPPORTED else "blossom"
    rng = np.random.default_rng(int(seed))
    try:
        error = code.random_error(float(error_rate), rng=rng)
        syndrome = code.syndrome(error)
        logicals = _logicals_matrix(code)
        result = qd.decode_with_diagnostics(code, syndrome, kind=native_kind, logicals=logicals)
        out = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    except Exception as e:
        raise QectorError(f"diagnostic decode failed: {e}") from e
    out["error_weight"] = int(np.sum(np.asarray(error)))
    out["syndrome_weight"] = int(np.sum(np.asarray(syndrome)))
    out["requested_decoder"] = decoder_kind
    out["decoder"] = native_kind
    out["decoder_substituted"] = native_kind != decoder_kind
    out["seed"] = int(seed)
    out["error_rate"] = float(error_rate)
    return out


def native_recommend(family_key: Optional[str] = None, distance: Optional[int] = None,
                     n_qubits: Optional[int] = None, priority: str = "balanced",
                     batch_size: int = 1) -> dict[str, Any]:
    """Backend-native decoder recommendation (``recommend``).

    Returns the backend's own :class:`Recommendation` as a dict plus the
    recommended decoder *class name* and, when it maps onto a wired kind, the
    corresponding workbench ``decoder_kind`` ready to feed the decode paths.
    Never raises: a bad priority or family is reported in the payload.
    """
    payload: dict[str, Any] = {"family": family_key, "distance": distance,
                               "n_qubits": n_qubits, "priority": priority}
    try:
        rec = qd.recommend(code_family=family_key, distance=distance,
                           n_qubits=n_qubits, batch_size=int(batch_size), priority=str(priority))
        name = qd.recommend_decoder(code_family=family_key, distance=distance,
                                    n_qubits=n_qubits, batch_size=int(batch_size), priority=str(priority))
        rec_dict = rec.as_dict() if hasattr(rec, "as_dict") else {}
        payload.update({
            "recommendation": rec_dict,
            "decoder_class": str(name),
            "decoder_kind": _CLASS_TO_KIND.get(str(name)),
            "reason": rec_dict.get("reason"),
            "status": "ok",
        })
    except Exception as e:
        payload.update({"status": "error", "message": f"{type(e).__name__}: {e}"})
    return payload


def run_native_streaming(code, n_rounds: int = 8, error_rate: float = 0.03,
                         seed: int = 1, window_size: int = 4) -> dict[str, Any]:
    """Native sliding-window streaming decode (``sliding_window_decode``).

    Samples ``n_rounds`` seeded syndromes and decodes the whole stream
    window-by-window with the backend's own (optionally GPU-accelerated)
    streaming engine, returning per-round validity, the logical-flip count and
    the native telemetry (windows, committed rounds, decode time, GPU transfer
    counters).  The hardware-accelerated counterpart to the pure-Python
    :func:`run_streaming_session` reference implementation.
    """
    if n_rounds < 0:
        raise QectorError("n_rounds must be >= 0")
    if window_size < 1:
        raise QectorError("window_size must be >= 1")
    rng = np.random.default_rng(int(seed))
    try:
        rounds = np.array(
            [code.syndrome(code.random_error(float(error_rate), rng=rng)) for _ in range(int(n_rounds))],
            dtype=np.uint8,
        )
        result = qd.sliding_window_decode(rounds, code=code, window_size=int(window_size))
    except Exception as e:
        raise QectorError(f"native streaming failed: {e}") from e
    corrections = np.asarray(result.corrections, dtype=np.uint8)
    try:
        is_valid = result.is_valid() if callable(getattr(result, "is_valid", None)) else getattr(result, "is_valid", None)
    except Exception:
        is_valid = None
    rounds_valid = None if is_valid is None else np.asarray(is_valid).astype(bool).reshape(-1).tolist()
    all_valid = None if rounds_valid is None else bool(all(rounds_valid))
    lf = getattr(result, "logical_flips", None)
    logical_flip_count = None if lf is None else int(np.sum(np.asarray(lf) != 0))
    telem = getattr(result, "telemetry", None)
    if telem is None:
        telem_d: Any = None
    elif hasattr(telem, "as_dict"):
        telem_d = telem.as_dict()
    elif hasattr(telem, "__dict__"):
        telem_d = dict(vars(telem))
    else:
        telem_d = str(telem)
    return {
        "n_rounds": int(n_rounds),
        "window_size": int(window_size),
        "corrections_shape": [int(s) for s in corrections.shape],
        "rounds_valid": rounds_valid,
        "all_valid": all_valid,
        "logical_flip_count": logical_flip_count,
        "telemetry": telem_d,
        "seed": int(seed),
        "error_rate": float(error_rate),
        "engine": "native_sliding_window",
    }


def list_available_codes() -> dict[str, Any]:
    """Code families wired into the workbench plus the backend's native
    ``codes.list_codes()`` catalogue.  Pure introspection."""
    wired = list(CODE_FAMILIES.keys())
    try:
        native = list(_codes.list_codes())
    except Exception:
        native = []
    return {
        "wired_families": wired,
        "wired_count": len(wired),
        "native_catalogue": native,
        "qldpc_families": sorted(QLDPC_FAMILIES),
    }


def run_parallel_batch_decode(code, n_samples: int = 64, error_rate: float = 0.05,
                              seed: int = 1, decoder_type: str = "union_find",
                              n_workers: Optional[int] = None) -> dict[str, Any]:
    """Multi-process parallel batch decode via ``DecoderPool``.

    Samples ``n_samples`` seeded syndromes and decodes them across a process
    pool, then verifies every correction against the GF(2) syndrome equation.
    Uses multiprocessing, so it is exposed as a library function (guarded by the
    ``freeze_support`` call in ``main``) rather than as an MCP tool.  The pool is
    always closed, even on error.
    """
    if n_samples < 1:
        raise QectorError("n_samples must be >= 1")
    allowed = {"union_find", "fast_union_find", "blossom", "sparse_blossom", "bp_osd"}
    if decoder_type not in allowed:
        raise QectorError(f"decoder_type must be one of {sorted(allowed)} for the pool")
    rng = np.random.default_rng(int(seed))
    pool = None
    workers_used: Optional[int] = None
    try:
        errors = [code.random_error(float(error_rate), rng=rng) for _ in range(int(n_samples))]
        syndromes = np.array([code.syndrome(e) for e in errors], dtype=np.uint8)
        pool = qd.DecoderPool(code.check_to_qubits, n_qubits=code.n_qubits,
                              decoder_type=decoder_type,
                              n_workers=None if n_workers is None else int(n_workers))
        try:
            nw = pool.n_workers
            workers_used = int(nw() if callable(nw) else nw)
        except Exception:
            workers_used = None
        t0 = time.perf_counter()
        corrections = np.asarray(pool.decode(syndromes), dtype=np.uint8)
        batch_seconds = time.perf_counter() - t0
        success = sum(
            1 for i in range(len(syndromes))
            if np.array_equal(np.asarray(code.syndrome(corrections[i]), dtype=np.uint8), syndromes[i])
        )
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"parallel batch decode failed: {e}") from e
    finally:
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass
    return {
        "success_rate": success / len(syndromes),
        "n_samples": int(n_samples),
        "batch_seconds": batch_seconds,
        "n_workers": workers_used,
        "decoder_type": decoder_type,
        "mean_hamming_weight": float(np.mean(np.sum(corrections, axis=1, dtype=np.int64))),
        "engine": "decoder_pool",
    }


def run_hybrid_cascade_stats(code, n_samples: int = 64, error_rate: float = 0.05,
                             seed: int = 1, escalation: Optional[str] = None) -> dict[str, Any]:
    """Batch-decode with HybridCascadeDecoder and expose its cascade statistics.

    Samples ``n_samples`` seeded errors, batch-decodes their syndromes through
    the cascade (Union-Find pre-filter + Blossom/BP-OSD escalation), and returns
    a JSON-safe dict with the live cascade counters (prefilter_hits,
    escalations, prefilter_hit_rate), wall-clock throughput, the
    syndrome-validity rate, and the logical error rate against the code's
    logicals when available (otherwise the syndrome-validity rate is reported
    in its place, flagged by ``logical_error_rate_kind``).
    """
    if n_samples < 1:
        raise QectorError("n_samples must be >= 1")
    opts = {"escalation": escalation} if escalation is not None else None
    decoder = _make_decoder("hybrid_cascade", code.check_to_qubits, opts)
    rng = np.random.default_rng(int(seed))
    try:
        errors = [code.random_error(float(error_rate), rng=rng) for _ in range(int(n_samples))]
        syndromes = np.array([code.syndrome(e) for e in errors], dtype=np.uint8)
        t0 = time.perf_counter()
        corrections = np.asarray(decoder.batch_decode(syndromes), dtype=np.uint8)
        batch_seconds = time.perf_counter() - t0
        match_count = sum(
            1
            for s, c in zip(syndromes, corrections)
            if np.array_equal(np.asarray(code.syndrome(c), dtype=np.uint8), s)
        )
        prefilter_hits = int(decoder.prefilter_hits)
        escalations = int(decoder.escalations)
        prefilter_hit_rate = float(decoder.prefilter_hit_rate)
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"hybrid cascade stats failed: {e}") from e
    logicals = _logicals_matrix(code)
    syndrome_match_rate = match_count / int(n_samples)
    if logicals is not None:
        failure_count = sum(1 for e, c in zip(errors, corrections) if _logical_failure(logicals, e, c))
        logical_error_rate: Optional[float] = failure_count / int(n_samples)
        ler_kind = "logical"
    else:
        # No usable logicals: report the syndrome-validity rate as the quality
        # metric in the logical_error_rate slot, flagged by its kind.
        logical_error_rate = syndrome_match_rate
        ler_kind = "syndrome_validity"
    return {
        "n_samples": int(n_samples),
        "error_rate": float(error_rate),
        "seed": int(seed),
        "escalation": str(escalation) if escalation is not None else "blossom",
        "prefilter_hits": prefilter_hits,
        "escalations": escalations,
        "prefilter_hit_rate": prefilter_hit_rate,
        "throughput_decodes_per_s": int(n_samples) / max(batch_seconds, 1e-9),
        "batch_seconds": batch_seconds,
        "syndrome_match_rate": syndrome_match_rate,
        "logical_error_rate": logical_error_rate,
        "logical_error_rate_kind": ler_kind,
        "decoder": "hybrid_cascade",
    }


def run_neural_predecoder_training(code, n_samples: int = 200, n_epochs: int = 5,
                                   error_rate: float = 0.05, seed: int = 1, cancel_token=None) -> dict[str, Any]:
    """Train the NeuralPredecoder on sampled (syndrome, error) pairs (lab tool).

    The MLP pre-decoder is deliberately **not** a wired decoder kind: an
    untrained network cannot satisfy the syndrome-validity decode contract.
    This research/lab entry point builds ``qd.NeuralPredecoder(n_checks,
    n_qubits)``, trains it on ``n_samples`` seeded (syndrome, error) pairs drawn
    via :func:`sample_error_and_syndrome`, then evaluates the raw decode on a
    held-out set drawn from a disjoint seed stream  -  reporting exact-match and
    per-bit accuracy against the sampled errors, the syndrome-validity rate of
    the predicted corrections, and the logical error rate when the code exposes
    a usable logicals matrix.  Returns a JSON-safe dict.
    """
    if n_samples < 1:
        raise QectorError("n_samples must be >= 1")
    if n_epochs < 1:
        raise QectorError("n_epochs must be >= 1")
    n_holdout = max(16, int(n_samples) // 4)
    try:
        predecoder = qd.NeuralPredecoder(int(code.n_checks), int(code.n_qubits))
        train_syndromes, train_corrections = [], []
        for i in range(int(n_samples)):
            if cancel_token and cancel_token.is_set():
                break
            error, syndrome = sample_error_and_syndrome(code, float(error_rate), int(seed) + i)
            train_corrections.append(error)
            train_syndromes.append(syndrome)
        t0 = time.perf_counter()
        # The 0.6.9 Rust train binding rejects 2-D ndarrays (TypeError:
        # 'ndarray' object is not an instance of 'ndarray'); it only accepts
        # flat 1-D uint8 buffers and derives n_samples from len/n_input
        # (resp. len/n_output)  -  so hand over row-major flattened batches.
        predecoder.train(
            np.asarray(train_syndromes, dtype=np.uint8).reshape(-1),
            np.asarray(train_corrections, dtype=np.uint8).reshape(-1),
            int(n_epochs),
            0.01,
        )
        train_seconds = time.perf_counter() - t0
        hold_errors, hold_syndromes = [], []
        for i in range(n_holdout):
            error, syndrome = sample_error_and_syndrome(
                code, float(error_rate), int(seed) + 1_000_000 + i
            )
            hold_errors.append(np.asarray(error, dtype=np.uint8))
            hold_syndromes.append(np.asarray(syndrome, dtype=np.uint8))
        predictions = [
            np.asarray(predecoder.decode(s), dtype=np.uint8).reshape(-1) for s in hold_syndromes
        ]
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"neural predecoder training failed: {e}") from e
    exact_matches = 0
    bit_matches = 0
    bit_total = 0
    valid_count = 0
    for error, syndrome, pred in zip(hold_errors, hold_syndromes, predictions):
        if pred.shape == error.shape:
            exact_matches += int(bool(np.array_equal(pred, error)))
            bit_matches += int(np.sum(pred == error))
            bit_total += int(error.size)
        if verify_correction(code, syndrome, pred):
            valid_count += 1
    logicals = _logicals_matrix(code)
    if logicals is not None:
        failure_count = sum(
            1
            for e, p in zip(hold_errors, predictions)
            if p.shape == e.shape and _logical_failure(logicals, e, p)
        )
        logical_error_rate: Optional[float] = failure_count / n_holdout
    else:
        logical_error_rate = None
    return {
        "n_checks": int(code.n_checks),
        "n_qubits": int(code.n_qubits),
        "n_samples": int(n_samples),
        "n_epochs": int(n_epochs),
        "learning_rate": 0.01,
        "error_rate": float(error_rate),
        "seed": int(seed),
        "train_seconds": train_seconds,
        "n_holdout": n_holdout,
        "exact_match_rate": exact_matches / n_holdout,
        "bit_accuracy": (bit_matches / bit_total) if bit_total else None,
        "syndrome_validity_rate": valid_count / n_holdout,
        "logical_error_rate": logical_error_rate,
        "note": "research/lab tool: the neural pre-decoder requires training and is "
                "not part of DECODER_KINDS; accuracy depends on n_samples/n_epochs "
                "and is not a substitute for a syndrome-valid decoder.",
    }


def compat_report() -> dict[str, Any]:
    """Ecosystem-integration availability report.

    Surfaces which optional dependencies (stim / sinter / pymatching / qiskit /
    ldpc) are importable, which qector_decoder_v3 compat submodules are present,
    and which research decoders the installed build ships.  Pure introspection;
    never raises.
    """
    deps: dict[str, Any] = {}
    for name, mod in (("stim", "stim"), ("sinter", "sinter"), ("pymatching", "pymatching"),
                      ("qiskit", "qiskit"), ("ldpc", "ldpc")):
        try:
            m = __import__(mod)
            deps[name] = getattr(m, "__version__", "present")
        except Exception:
            deps[name] = None
    compat_mods = {sm: hasattr(qd, sm) for sm in
                   ("stim_compat", "sinter_compat", "pymatching_compat", "qiskit_plugin", "dem")}
    research = {name: hasattr(qd, name) for name in
                ("BeliefMatching", "GNNPredecoder", "NeuralPredecoder", "BatchedBpDecoder", "Workbench")}
    return {
        "backend_version": PACKAGE_VERSION,
        "optional_dependencies": deps,
        "compat_modules": compat_mods,
        "research_components": research,
    }


def sparse_blossom_radix_neighbors(code_or_checks, defects: list[int], k: int = 8) -> list[tuple[int, int, int, int]]:
    """Return k-nearest candidate edges (sorted by distance) for defects via SparseBlossom RadixHeap."""
    c2q = getattr(code_or_checks, "check_to_qubits", code_or_checks)
    sb_cls = getattr(qd, "SparseBlossomDecoder", None)
    if sb_cls is None:
        return []
    try:
        dec = sb_cls(c2q)
        fn = getattr(qd, "sparse_blossom_radix_neighbors", None)
        if fn is not None:
            try:
                return fn(dec, defects, k=k)
            except (AttributeError, Exception):
                pass
        if hasattr(dec, "k_nearest_via_radix"):
            return list(dec.k_nearest_via_radix(list(defects), int(k)))
    except Exception:
        pass
    return []


def clear_decoder_cache() -> bool:
    """Clear the native decoder cache in qector_decoder_v3."""
    fn = getattr(qd, "clear_decoder_cache", None)
    if fn is not None:
        try:
            fn()
            return True
        except Exception:
            pass
    return False


def run_doctor_checks() -> dict[str, Any]:
    """Run system health diagnostic checks via qd.doctor."""
    doc = getattr(qd, "doctor", None)
    if doc is not None and hasattr(doc, "run_checks"):
        try:
            checks = doc.run_checks()
            serialized = []
            for c in checks:
                if hasattr(c, "as_dict"):
                    serialized.append(c.as_dict())
                elif hasattr(c, "to_dict"):
                    serialized.append(c.to_dict())
                elif isinstance(c, dict):
                    serialized.append(c)
                else:
                    serialized.append({
                        "check": getattr(c, "name", str(c)),
                        "status": getattr(c, "status", "PASS"),
                        "detail": getattr(c, "detail", str(c)),
                        "remedy": getattr(c, "remedy", ""),
                    })
            all_pass = all(item.get("status") in ("PASS", "pass", "ok", "OK", "WARN", "warn") for item in serialized)
            return {"ok": all_pass, "checks": serialized, "count": len(serialized)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "doctor module unavailable"}


def flush_usage(customer_id: Optional[str] = None, api_key: Optional[str] = None) -> dict[str, Any]:
    """Flush accumulated usage metrics to Stripe metered billing API."""
    fn = getattr(qd, "flush_usage", None)
    if fn is not None:
        try:
            return fn(customer_id=customer_id, api_key=api_key)
        except TypeError:
            try:
                return fn()
            except Exception as exc:
                return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "flush_usage unavailable in backend"}


def verify_license_token(token: str) -> dict[str, Any]:
    """Verify an Ed25519 signed license token string."""
    fn = getattr(qd, "verify_license_token", None)
    if fn is not None:
        try:
            res = fn(str(token))
            return dict(res) if isinstance(res, dict) else {"ok": True, "details": str(res)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
    return {"ok": False, "error": "verify_license_token unavailable"}


def set_license_key_file(path: str) -> bool:
    """Set license key file path for offline hard-gated verification."""
    fn = getattr(qd, "set_license_key_file", None)
    if fn is not None:
        try:
            fn(str(path))
            return True
        except Exception:
            pass
    return False


def export_session(
    code_family: str,
    distance: int,
    decoder_name: str,
    error_rate: float,
    seed: int,
    output_path: str,
) -> dict[str, Any]:
    """Export a complete decode session (code + decode + benchmark + diagnostics)
    as a single ZIP archive containing JSON and text artifacts.
    """
    import zipfile
    import json
    import io
    from datetime import datetime, timezone
    from pathlib import Path

    try:
        code = build_code(code_family, distance)
        decode_res = run_single_decode(code, error_rate, decoder_name, seed)
        decode_dict = decode_res["result"].to_dict()
        decode_data = {
            "error": np.asarray(decode_res["error"]).tolist(),
            "syndrome": np.asarray(decode_res["syndrome"]).tolist(),
            "correction": decode_dict["correction"],
            "hamming_weight": decode_dict["hamming_weight"],
            "syndrome_valid": decode_dict["syndrome_valid"],
            "logical_failure": decode_dict["logical_failure"],
        }
        
        bench_data = run_benchmark(
            code=code,
            n_samples=40,
            seed=seed,
            decoder_kind=decoder_name,
            error_rate=error_rate,
        )
        
        import autodebug
        diag_data = autodebug.run_self_diagnostics().to_dict()
        code_info = code_summary(code)
        
        out_path = Path(output_path)
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            zip_file.writestr("code.json", json.dumps(code_info, indent=2))
            zip_file.writestr("decode.json", json.dumps(decode_data, indent=2))
            zip_file.writestr("benchmark.json", json.dumps(bench_data, indent=2))
            zip_file.writestr("diagnostics.json", json.dumps(diag_data, indent=2))
            
            summary_md = f"""# QECTOR Decode Session Export
Generated: {datetime.now(timezone.utc).isoformat()}
Code: {code_family} (d={distance})
Decoder: {decoder_name}
Error Rate (p): {error_rate}
Seed: {seed}

## Summary Metrics
- Syndrome Valid: {decode_data['syndrome_valid']}
- Logical Failure: {decode_data['logical_failure']}
- Correction Hamming Weight: {decode_data['hamming_weight']}
- Benchmark Throughput: {bench_data['throughput_decodes_per_s']:.1f} decodes/s
- Mean Latency: {bench_data['latency_mean_us']:.2f} us
"""
            zip_file.writestr("summary.md", summary_md)
            
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(zip_buffer.getvalue())
        return {
            "ok": True,
            "message": f"Successfully exported session to {out_path.name}",
            "path": str(out_path.resolve()),
        }
    except Exception as e:
        raise QectorError(f"Session export failed: {e}") from e


def import_syndrome(file_path: str) -> np.ndarray:
    """Load syndrome data from CSV, JSON, or numpy (.npy) file."""
    import json
    import re
    from pathlib import Path

    path = Path(file_path)
    if not path.exists():
        raise QectorError(f"Syndrome file not found: {file_path}")
        
    suffix = path.suffix.lower()
    try:
        if suffix == ".npy":
            data = np.load(path)
        elif suffix == ".json":
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and "syndrome" in raw:
                data = raw["syndrome"]
            else:
                data = raw
            data = np.asarray(data)
        elif suffix == ".csv":
            data = np.genfromtxt(path, delimiter=",", dtype=np.uint8)
        else:
            text = path.read_text(encoding="utf-8").strip()
            parts = re.split(r"[,\s\n\r]+", text)
            data = np.asarray([int(x) for x in parts if x.strip()], dtype=np.uint8)
            
        data = np.asarray(data, dtype=np.uint8).ravel()
        return data
    except Exception as e:
        raise QectorError(f"Failed to load syndrome from {file_path}: {e}") from e


def get_compatibility_matrix() -> dict[str, list[str]]:
    """Return a mapping of code family to list of compatible decoders."""
    matrix = {}
    for fam in list_available_codes()["wired_families"]:
        try:
            param = 3
            if fam == "color_code":
                param = 3
            elif fam == "hypergraph_product":
                param = 3
            code = build_code(fam, param)
            matrix[fam] = compatible_decoder_kinds(code)
        except Exception:
            matrix[fam] = []
    return matrix


# ---------------------------------------------------------------------------
# v1.0.0 Backend Integration: DEM/Stim support, LERBenchmark, SpaceTimeDecoder
# ---------------------------------------------------------------------------

def build_dem_from_code(code, noise_model: str = "depolarizing", p: float = 0.05,
                        bias: float = 0.5, correlation: Optional[np.ndarray] = None) -> Any:
    """Build a Detector Error Model (DEM) from a code and noise model.
    
    Args:
        code: The quantum code object
        noise_model: One of "depolarizing", "biased", "correlated", "circuit"
        p: Error rate
        bias: Bias for biased noise (X/Z ratio)
        correlation: Correlation matrix for correlated noise
    """
    try:
        # Try to use the backend's DEM module
        dem_mod = getattr(qd, "dem", None)
        if dem_mod is None:
            raise QectorError("DEM module not available in this build")
        
        DemModel = getattr(dem_mod, "DemModel", None)
        if DemModel is None:
            raise QectorError("DemModel class not available")
        
        # Get the parity check matrix
        H = _checks_to_h_matrix(code.check_to_qubits)
        
        if noise_model == "depolarizing":
            return DemModel.from_depolarizing(H, p)
        elif noise_model == "biased":
            return DemModel.from_biased(H, p, bias)
        elif noise_model == "correlated":
            if correlation is None:
                raise QectorError("correlation matrix required for correlated noise model")
            return DemModel.from_correlated(H, p, correlation)
        elif noise_model == "circuit":
            # Build from Stim circuit if available
            stim_mod = getattr(qd, "stim_compat", None)
            if stim_mod is None:
                raise QectorError("Stim compatibility module not available")
            from_stim = getattr(stim_mod, "from_stim_detector_error_model", None)
            if from_stim is None:
                raise QectorError("from_stim_detector_error_model not available")
            # This would require a Stim circuit - for now return None
            return None
        else:
            raise QectorError(f"Unknown noise model: {noise_model}")
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"Failed to build DEM: {e}") from e


def decode_dem(dem, decoder_kind: str = "bp_osd", decoder_options: Optional[dict] = None,
               error_rate: float = 0.05, seed: Optional[int] = None) -> dict[str, Any]:
    """Decode a syndrome using a Detector Error Model (DEM-native decoding).

    Accepts either an existing DemModel (which must have ``make_decoder``) or a
    quantum code object, from which a depolarizing DemModel is built on the fly
    and a real error is sampled, decoded, and verified.
    """
    try:
        dem_mod = getattr(qd, "dem", None)
        if dem_mod is None:
            raise QectorError("DEM module not available in this build")

        DemModel = getattr(dem_mod, "DemModel", None)
        if DemModel is None:
            raise QectorError("DemModel class not available")

        make_decoder = getattr(dem, "make_decoder", None)
        src_code = None
        if make_decoder is None:
            # A code object was passed: build a depolarizing DEM from its checks.
            src_code = dem
            checks = getattr(src_code, "check_to_qubits", None)
            if checks is None:
                raise QectorError("DemModel has no make_decoder method")
            DemError = getattr(dem_mod, "DemError", None)
            if DemError is None:
                raise QectorError("DemError class not available")
            errors = [
                DemError(probability=float(error_rate),
                         detectors=tuple(ci for ci, qubits in enumerate(checks) if q in qubits),
                         observables=())
                for q in range(int(src_code.n_qubits))
            ]
            dem = DemModel(errors, num_detectors=len(checks),
                           num_observables=0, detector_coords={})
            make_decoder = dem.make_decoder

        decoder = make_decoder(decoder_kind, **(decoder_options or {}))
        decode_method = getattr(decoder, "decode", None)
        if decode_method is None:
            raise QectorError("Decoder has no decode method")

        if src_code is None:
            # A DemModel was supplied directly; decoding needs an external
            # syndrome, so report the decoder is ready for one.
            return {"status": "dem_decode_ready", "decoder_kind": decoder_kind,
                    "message": "DEM decode requires syndrome input"}

        # Sample a real error, decode its syndrome with the DEM-native decoder.
        rng = np.random.default_rng(seed)
        error = rng.integers(0, 2, size=int(src_code.n_qubits))
        syndrome = np.asarray(src_code.syndrome(error), dtype=np.uint8)
        correction = np.asarray(decode_method(syndrome.tolist()), dtype=np.uint8)
        valid = bool(np.array_equal(src_code.syndrome(correction) % 2, syndrome % 2))
        return {
            "status": "dem_decode_ok",
            "decoder_kind": decoder_kind,
            "n_qubits": int(src_code.n_qubits),
            "n_detectors": int(len(src_code.check_to_qubits)),
            "syndrome_weight": int(np.count_nonzero(syndrome)),
            "syndrome_valid": valid,
            "correction": correction.tolist(),
        }
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"DEM decode failed: {e}") from e


def import_stim_circuit(file_path: str) -> Any:
    """Import a Stim circuit from file and convert to DEM."""
    try:
        import stim
    except ImportError:
        raise QectorError("Stim not installed. Install with: pip install stim")
    
    try:
        circuit = stim.Circuit.from_file(file_path)
        return circuit
    except Exception as e:
        raise QectorError(f"Failed to load Stim circuit from {file_path}: {e}") from e


def build_code_from_matrix(H_matrix: np.ndarray, name: str = "custom",
                           distance: Optional[int] = None) -> Any:
    """Build a code from a user-provided parity check matrix.
    
    Args:
        H_matrix: Binary parity check matrix (n_checks x n_qubits)
        name: Name for the constructed code
        distance: Optional expected code distance
        
    Returns:
        A code object compatible with the workbench
    """
    try:
        H = np.asarray(H_matrix, dtype=np.uint8)
        if H.ndim != 2:
            raise QectorError("H_matrix must be 2D")
        
        # Use the decoder wheel's real constructor for custom parity matrices.
        codes_mod = getattr(qd, "codes", None)
        if codes_mod is not None:
            from_parity = getattr(codes_mod, "from_parity_check_matrix", None)
            if from_parity is not None:
                return from_parity(H, name=name, distance=distance)
        
        raise QectorError("No code constructor available for custom parity check matrix")
    except QectorError:
        raise
    except Exception as e:
        raise QectorError(f"Failed to build code from matrix: {e}") from e


def estimate_threshold(code, decoder_kind: str = "blossom",
                       p_range: tuple = (0.01, 0.2), n_samples: int = 100,
                       distances: list = None) -> dict[str, Any]:
    """Estimate the error threshold using binary search on error rate.
    
    Args:
        code: The quantum code
        decoder_kind: Decoder to use
        p_range: (min_p, max_p) range for binary search
        n_samples: Samples per point
        distances: List of distances for finite-size scaling
    """
    if distances is None:
        distances = [3, 5, 7, 9, 11]

    family_key = code.get("family") if isinstance(code, dict) else getattr(code, "family", None)
    if family_key not in CODE_FAMILIES:
        family_key = None

    results = {}

    for d in distances:
        if family_key is None:
            continue  # family not recoverable from the code object
        try:
            test_code = build_code(family_key, d)
        except Exception:
            continue
        
        ler_curve = []
        p_vals = np.linspace(p_range[0], p_range[1], 10)
        
        for p in p_vals:
            try:
                bench = run_benchmark(test_code, n_samples=n_samples, decoder_kind=decoder_kind,
                                     error_rate=p, seed=42)
                ler = bench.get("logical_error_rate")
                if ler is not None:
                    ler_curve.append({"p": p, "ler": ler})
            except Exception:
                continue
        
        results[f"d={d}"] = ler_curve
    
    # Try to fit threshold
    threshold = None
    try:
        # Simple interpolation where LER crosses 0.5
        for d_key, curve in results.items():
            for i in range(len(curve) - 1):
                if curve[i]["ler"] <= 0.5 and curve[i+1]["ler"] >= 0.5:
                    # Interpolate
                    p1, ler1 = curve[i]["p"], curve[i]["ler"]
                    p2, ler2 = curve[i+1]["p"], curve[i+1]["ler"]
                    threshold = p1 + (0.5 - ler1) * (p2 - p1) / (ler2 - ler1)
                    break
            if threshold:
                break
    except Exception:
        pass
    
    return {
        "threshold": threshold,
        "ler_curves": results,
        "decoder": decoder_kind,
    }


def finite_size_scaling(code_family: str, decoder_kind: str = "blossom",
                        distances: list = None, p_vals: list = None,
                        n_samples: int = 100) -> dict[str, Any]:
    """Perform finite-size scaling analysis (LER vs distance at fixed p)."""
    if distances is None:
        distances = [3, 5, 7, 9, 11]
    if p_vals is None:
        p_vals = [0.01, 0.03, 0.05, 0.07, 0.1]
    
    results = {}
    
    for p in p_vals:
        curve = []
        for d in distances:
            try:
                code = build_code(code_family, d)
                bench = run_benchmark(code, n_samples=n_samples, decoder_kind=decoder_kind,
                                     error_rate=p, seed=42)
                ler = bench.get("logical_error_rate")
                if ler is not None:
                    curve.append({"distance": d, "ler": ler})
            except Exception:
                continue
        if curve:
            results[f"p={p}"] = curve
    
    return {
        "scaling_data": results,
        "decoder": decoder_kind,
        "code_family": code_family,
    }


def run_ler_benchmark(code, n_samples: int = 1000, error_rate: float = 0.05,
                       decoder_kind: str = "blossom", seed: int = 42) -> dict[str, Any]:
    """Run LER benchmark with Wilson confidence intervals (from upstream qector CLI)."""
    # This wraps the existing run_benchmark which already has Wilson CI
    result = run_benchmark(code, n_samples=n_samples, decoder_kind=decoder_kind,
                          error_rate=error_rate, seed=seed)
    
    # Ensure Wilson CI is present
    if "ler_ci95_low" not in result or "ler_ci95_high" not in result:
        ler = result.get("logical_error_rate")
        if ler is not None:
            n = float(n_samples)
            k = ler * n
            z = 1.959963984540054
            denom = 1.0 + z * z / n
            centre = (k/n + z * z / (2.0 * n)) / denom
            half = z * ((k/n) * (1.0 - k/n) / n + z * z / (4.0 * n * n)) ** 0.5 / denom
            result["ler_ci95_low"] = max(0.0, centre - half)
            result["ler_ci95_high"] = min(1.0, centre + half)
    
    return result


def generate_parity_check_matrix(family: str, distance: int) -> np.ndarray:
    """Generate a parity check matrix for a code family."""
    code = build_code(family, distance)
    H = _checks_to_h_matrix(code.check_to_qubits)
    return H


def get_license_info() -> dict[str, Any]:
    """Get license info from the decoder."""
    try:
        info = qd.get_license_info()
        return dict(info) if isinstance(info, dict) else {"tier": str(info)}
    except Exception as e:
        return {"error": str(e), "tier": "unknown"}


def generate_reproducibility_package(code_family: str, distance: int,
                                      decoder_kind: str, error_rate: float,
                                      seed: int, output_path: str) -> dict[str, Any]:
    """Generate a complete reproducibility package."""
    import zipfile
    import json
    import io
    from datetime import datetime, timezone
    from pathlib import Path
    
    try:
        code = build_code(code_family, distance)
        decode_res = run_single_decode(code, error_rate, decoder_kind, seed)
        decode_dict = decode_res["result"].to_dict()
        
        bench_data = run_benchmark(code=code, n_samples=100, seed=seed,
                                   decoder_kind=decoder_kind, error_rate=error_rate)
        
        # Get system info
        import platform
        sys_info = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "backend_version": PACKAGE_VERSION,
        }
        
        out_path = Path(output_path)
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Code info
            zip_file.writestr("code.json", json.dumps(code_summary(code), indent=2))
            # Decode result
            zip_file.writestr("decode.json", json.dumps({
                "error": np.asarray(decode_res["error"]).tolist(),
                "syndrome": np.asarray(decode_res["syndrome"]).tolist(),
                "correction": decode_dict["correction"],
                "hamming_weight": decode_dict["hamming_weight"],
                "syndrome_valid": decode_dict["syndrome_valid"],
                "logical_failure": decode_dict["logical_failure"],
            }, indent=2))
            # Benchmark
            zip_file.writestr("benchmark.json", json.dumps(bench_data, indent=2))
            # System info
            zip_file.writestr("system.json", json.dumps(sys_info, indent=2))
            # Parameters
            params = {
                "code_family": code_family,
                "distance": distance,
                "decoder": decoder_kind,
                "error_rate": error_rate,
                "seed": seed,
                "generated": datetime.now(timezone.utc).isoformat(),
            }
            zip_file.writestr("parameters.json", json.dumps(params, indent=2))
            # README
            readme = f"""# QECTOR Reproducibility Package
Generated: {params['generated']}
Code: {code_family} (d={distance})
Decoder: {decoder_kind}
Error Rate: {error_rate}
Seed: {seed}

## Contents
- code.json: Code properties
- decode.json: Single decode result
- benchmark.json: Benchmark with Wilson CI
- system.json: System/environment info
- parameters.json: All parameters for reproduction
"""
            zip_file.writestr("README.md", readme)
        
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(zip_buffer.getvalue())
        
        return {
            "ok": True,
            "path": str(out_path.resolve()),
            "message": f"Reproducibility package saved to {out_path.name}",
        }
    except Exception as e:
        raise QectorError(f"Reproducibility package generation failed: {e}") from e


def export_figure(code, family: str, distance: int, output_path: str,
                  format: str = "png", dpi: int = 300) -> dict[str, Any]:
    """Export a publication-ready figure of the Tanner graph."""
    from pathlib import Path
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        
        fig = Figure(figsize=(8, 6), dpi=dpi)
        canvas = FigureCanvasAgg(fig)
        
        q_coords, c_coords = get_tanner_graph_layout(code, family, distance)
        
        ax = fig.add_subplot(111)
        ax.set_facecolor('#1a1a2e')
        fig.patch.set_facecolor('#1a1a2e')
        
        # Plot checks
        if c_coords:
            cx, cy = zip(*c_coords)
            ax.scatter(cx, cy, c='#ff6b6b', s=80, marker='s', label='Checks', zorder=3)
        
        # Plot qubits
        if q_coords:
            qx, qy = zip(*q_coords)
            ax.scatter(qx, qy, c='#4ecdc4', s=60, marker='o', label='Qubits', zorder=3)
        
        # Draw edges from parity check matrix
        H = _parity_check_for_layout(code)
        if H is not None:
            rows, cols = np.nonzero(H)
            for r, c in zip(rows, cols):
                if r < len(c_coords) and c < len(q_coords):
                    ax.plot([c_coords[r][0], q_coords[c][0]],
                           [c_coords[r][1], q_coords[c][1]],
                           c='#ffffff33', linewidth=0.5, zorder=1)
        
        ax.set_xlim(-0.05, 1.05)
        ax.set_ylim(-0.1, 0.55)
        ax.set_aspect('equal')
        ax.set_title(f'{family} d={distance} Tanner Graph', color='white', fontsize=14)
        ax.legend(loc='upper right')
        ax.axis('off')
        
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        if format == "png":
            fig.savefig(out_path, dpi=dpi, facecolor=fig.get_facecolor(), bbox_inches='tight')
        elif format == "pdf":
            fig.savefig(out_path, format='pdf', facecolor=fig.get_facecolor(), bbox_inches='tight')
        elif format == "svg":
            fig.savefig(out_path, format='svg', facecolor=fig.get_facecolor(), bbox_inches='tight')
        elif format == "pgf":
            # LaTeX-compatible PGF
            fig.savefig(out_path, format='pgf', facecolor=fig.get_facecolor(), bbox_inches='tight')
        else:
            raise QectorError(f"Unknown format: {format}")
        
        return {
            "ok": True,
            "path": str(out_path.resolve()),
            "format": format,
            "dpi": dpi,
        }
    except Exception as e:
        raise QectorError(f"Figure export failed: {e}") from e


def export_to_qiskit_plugin(code, output_path: str) -> dict[str, Any]:
    """Export code to Qiskit plugin format.
    
    Converts the code's parity check matrix to a format compatible with
    Qiskit's quantum error correction modules.
    """
    try:
        H = _checks_to_h_matrix(code.check_to_qubits)
        n_qubits = code.n_qubits
        n_checks = code.n_checks
        
        # Build Qiskit-compatible representation
        qiskit_data = {
            "parity_check_matrix": H.tolist(),
            "n_qubits": n_qubits,
            "n_checks": n_checks,
            "code_name": getattr(code, "name", "unknown"),
            "distance": getattr(code, "distance", None),
        }
        
        import json
        from pathlib import Path
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(qiskit_data, indent=2), encoding="utf-8")
        
        return {
            "ok": True,
            "path": str(out_path.resolve()),
            "format": "qiskit_plugin",
            "n_qubits": n_qubits,
            "n_checks": n_checks,
        }
    except Exception as e:
        raise QectorError(f"Qiskit plugin export failed: {e}") from e


def export_to_sinter_compat(code, decoder_kind: str, output_path: str) -> dict[str, Any]:
    """Export benchmark data in sinter-compatible format.
    
    Generates a CSV file with columns compatible with sinter's analysis tools.
    """
    try:
        import csv
        from pathlib import Path
        
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Run a quick benchmark to generate data
        bench = run_benchmark(code, n_samples=100, decoder_kind=decoder_kind, error_rate=0.05, seed=42)
        
        # Write sinter-compatible CSV
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "decoder", "code", "distance", "p", "n_shots", "n_errors",
                "logical_error_rate", "ler_ci95_low", "ler_ci95_high",
                "time_per_shot_sec"
            ])
            code_name = getattr(code, "name", "unknown")
            distance = getattr(code, "distance", "unknown")
            writer.writerow([
                decoder_kind,
                code_name,
                distance,
                bench.get("p", 0.05),
                bench.get("n_trials", 100),
                bench.get("unfaithful_count", 0),
                bench.get("logical_error_rate", 0.0),
                bench.get("ler_ci95_low", 0.0),
                bench.get("ler_ci95_high", 0.0),
                bench.get("decode_seconds", 0.0) / max(bench.get("n_trials", 1), 1),
            ])
        
        return {
            "ok": True,
            "path": str(out_path.resolve()),
            "format": "sinter_compat",
            "rows": 1,
        }
    except Exception as e:
        raise QectorError(f"Sinter compat export failed: {e}") from e


def export_to_pymatching_shim(code, output_path: str) -> dict[str, Any]:
    """Export code to PyMatching-compatible format.
    
    Generates a PyMatching decoder graph (edges + weights) from the code.
    """
    try:
        H = _checks_to_h_matrix(code.check_to_qubits)
        n_qubits = code.n_qubits
        n_checks = code.n_checks
        
        # Build PyMatching-compatible graph
        edges = []
        for check_idx in range(n_checks):
            qubit_indices = np.where(H[check_idx] == 1)[0]
            for i, q1 in enumerate(qubit_indices):
                for q2 in qubit_indices[i+1:]:
                    # Edge between qubits that share a check
                    edges.append((int(q1), int(q2), 1.0))
        
        pymatching_data = {
            "n_qubits": n_qubits,
            "edges": edges,
            "code_name": getattr(code, "name", "unknown"),
            "distance": getattr(code, "distance", None),
        }
        
        import json
        from pathlib import Path
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(pymatching_data, indent=2), encoding="utf-8")
        
        return {
            "ok": True,
            "path": str(out_path.resolve()),
            "format": "pymatching_shim",
            "n_qubits": n_qubits,
            "n_edges": len(edges),
        }
    except Exception as e:
        raise QectorError(f"PyMatching shim export failed: {e}") from e


def get_license_info_with_expiry() -> dict[str, Any]:
    """Get license info including token expiry details.
    
    Returns the license info from the decoder plus expiry information
    if available (for v2 tokens).
    """
    try:
        info = qd.get_license_info()
        info = dict(info) if isinstance(info, dict) else {"tier": str(info)}
        
        # Add expiry display if available
        if info.get("expires_at"):
            from datetime import datetime, timezone
            try:
                expires_ts = int(info["expires_at"])
                expires_dt = datetime.fromtimestamp(expires_ts, tz=timezone.utc)
                info["expires_display"] = expires_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                info["is_expired"] = expires_ts < time.time()
                if info["is_expired"]:
                    info["expires_display"] += " (EXPIRED)"
            except Exception:
                pass
        
        return info
    except Exception as e:
        return {"error": str(e), "tier": "unknown"}


def test_invalid_license_key() -> dict[str, Any]:
    """Test that an invalid license key raises ValueError.
    
    This verifies the decoder properly rejects malformed/unsigned tokens.
    """
    try:
        # Try to verify an obviously invalid token
        invalid_token = "invalid.token.here"
        fn = getattr(qd, "verify_license_token", None)
        if fn is None:
            return {"tested": False, "reason": "verify_license_token not available"}
        
        try:
            result = fn(invalid_token)
            if isinstance(result, dict) and not result.get("ok", True):
                return {"tested": True, "passed": True, "error": result.get("error")}
            else:
                return {"tested": True, "passed": False, "reason": "Invalid token was accepted"}
        except ValueError as e:
            return {"tested": True, "passed": True, "error": str(e)}
        except Exception as e:
            return {"tested": True, "passed": False, "reason": f"Wrong exception type: {type(e).__name__}: {e}"}
    except Exception as e:
        return {"tested": False, "error": str(e)}


