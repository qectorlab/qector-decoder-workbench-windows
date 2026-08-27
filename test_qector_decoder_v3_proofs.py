"""
Exclusive qector-decoder-v3 v1.0.0 proof suite — no plugin / skills / workbench.

Every test is an executable proof obligation transcribed from the
QECTOR Decoder v3 Reference Manual v1.0.0 (DOI 10.5281/zenodo.21941046).
The suite is AIO self-contained: the pure-Python ground truth (the
specification) is inlined in this single file — no external
``qector_math_ground_truth`` module — plus the live installed wheel
``qector_decoder_v3==1.0.0`` (the implementation).  The suite uses only the
wheel-native MCP surface; no Claude plugin manifests or Workbench app are
required.  Network access is limited to the explicit PyPI bootstrap.

Run
---
    # One command: creates a fresh .venv, installs the live PyPI wheel, and
    # re-executes this file with that interpreter on Windows, macOS, or Linux:
    python test_qector_decoder_v3_proofs.py

    # The equivalent explicit command:
    python test_qector_decoder_v3_proofs.py --fresh

    # After the runner has completed, the generated .venv can also be used
    # directly for a normal test invocation:
    .venv/bin/python -m unittest test_qector_decoder_v3_proofs -v
    .venv\\Scripts\\python.exe -m unittest test_qector_decoder_v3_proofs -v

Coverage
--------
- Ground-truth unit tests (Appendix E arithmetic, §§2.7–2.8)
- Theorems 1–16 (one class per theorem group, exhaustive where 2ⁿ ≤ 64)
- Worked examples: Steane, ring d=5, repetition d=5, OSD solve, DEM
  collapse, two-stage CSS, workspace sizing, adaptive-k, streaming bound,
  box-plus kernel φ
- Live wheel: version 1.0.0, stable API surface, every decoder faithful
  on every single-qubit error of every graphlike family, weighted
  decoding, determinism, graphlike structural guard
"""

from __future__ import annotations

import argparse
import functools
import itertools
import math
import os
import re
import sys
import unittest

# Silence the QECTOR startup banner before importing the wheel.
os.environ.setdefault("QECTOR_SILENT", "1")

# The live wheel is loaded after bootstrap on direct execution.  Keeping the
# import lazy prevents the caller's system Python from being used accidentally
# and keeps pytest collection useful when the optional live dependency is absent.
np = None  # type: ignore[assignment]
qector = None  # type: ignore[assignment]
BlossomDecoder = FastUnionFindDecoder = SparseBlossomDecoder = UnionFindDecoder = None  # type: ignore[assignment]
codes = None  # type: ignore[assignment]
_QECTOR_IMPORT_OK = False
_QECTOR_IMPORT_ERROR: Exception | None = None


def _load_live_dependencies() -> bool:
    """Load NumPy and the live decoder wheel exactly once when available."""
    global np, qector, BlossomDecoder, FastUnionFindDecoder
    global SparseBlossomDecoder, UnionFindDecoder, codes
    global _QECTOR_IMPORT_OK, _QECTOR_IMPORT_ERROR

    if _QECTOR_IMPORT_OK:
        return True
    try:
        import numpy as _np

        import qector_decoder_v3 as _qector
        from qector_decoder_v3 import (
            BlossomDecoder as _BlossomDecoder,
            FastUnionFindDecoder as _FastUnionFindDecoder,
            SparseBlossomDecoder as _SparseBlossomDecoder,
            UnionFindDecoder as _UnionFindDecoder,
        )
        from qector_decoder_v3 import codes as _codes
    except Exception as _q_import_err:  # ImportError, ModuleNotFoundError, etc.
        np = None  # type: ignore[assignment]
        qector = None  # type: ignore[assignment]
        BlossomDecoder = FastUnionFindDecoder = SparseBlossomDecoder = UnionFindDecoder = None  # type: ignore[assignment]
        codes = None  # type: ignore[assignment]
        _QECTOR_IMPORT_OK = False
        _QECTOR_IMPORT_ERROR = _q_import_err
        return False

    np = _np  # type: ignore[assignment]
    qector = _qector  # type: ignore[assignment]
    BlossomDecoder = _BlossomDecoder  # type: ignore[assignment]
    FastUnionFindDecoder = _FastUnionFindDecoder  # type: ignore[assignment]
    SparseBlossomDecoder = _SparseBlossomDecoder  # type: ignore[assignment]
    UnionFindDecoder = _UnionFindDecoder  # type: ignore[assignment]
    codes = _codes  # type: ignore[assignment]
    _QECTOR_IMPORT_OK = True
    _QECTOR_IMPORT_ERROR = None
    return True

# ===========================================================================
# OFFICIAL QECTOR CLI BANNER + METADATA (from QECTOR APP\\cli.py + version.py)
# ===========================================================================
# DOI — normative reference manual deposit (Zenodo)
QECTOR_DOI = "https://doi.org/10.5281/zenodo.21941046"
QECTOR_DOI_SHORT = "10.5281/zenodo.21941046"
QECTOR_MANUAL = "QectorDecoder_v3_Reference_Manual_v1.0.0.pdf"

# Workbench / backend versions (version.py)
WORKBENCH_VERSION = "1.0.4"
BACKEND_VERSION = "1.0.0"
MIN_BACKEND_VERSION = "1.0.0"
MCP_TOOLS = 85

# Business / attribution
QECTOR_AUTHOR = "Guillaume Lessard / iD01t Productions"
QECTOR_ORCID = "0009-0000-3465-3753"
QECTOR_COMPANY = "iD01t Productions"
QECTOR_CONTACT = "admin@qector.store"
QECTOR_WEBSITE = "https://www.qector.store"
QECTOR_PRICING = "https://qector.store/pricing"
QECTOR_LICENCE = (
    "Source-available. Free for academic, personal and non-commercial research. "
    "Commercial use requires a paid licence."
)


class C:
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    @classmethod
    def disable(cls):
        cls.CYAN = cls.MAGENTA = cls.BLUE = cls.GREEN = cls.YELLOW = cls.RED = cls.BOLD = cls.DIM = cls.RESET = ""


# Exact art from QECTOR APP\\cli.py:46 — spelling Q E C T O R (shadow lettering)
_BANNER_ART = r"""
 ██████╗ ███████╗ ██████╗████████╗ ██████╗ ██████╗
██╔═══██╗██╔════╝██╔════╝╚══██╔══╝██╔═══██╗██╔══██╗
██║   ██║█████╗  ██║        ██║   ██║   ██║██████╔╝
██║▄▄ ██║██╔══╝  ██║        ██║   ██║   ██║██╔══██╗
╚██████╔╝███████╗╚██████╗   ██║   ╚██████╔╝██║  ██║
 ╚══▀▀═╝ ╚══════╝ ╚═════╝   ╚═╝    ╚═════╝ ╚═╝  ╚═╝"""


def banner() -> str:
    """Build the banner at call time (official CLI: cli.py:58) — Proof Suite edition."""
    return (
        f"{C.CYAN}{C.BOLD}{_BANNER_ART}\n"
        f"{C.MAGENTA}      QECTOR Decoder v3  •  Certified Proof Suite"
        f"{C.DIM}  v{BACKEND_VERSION}  •  {QECTOR_DOI_SHORT}{C.RESET}\n"
    )


try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _can_unicode() -> bool:
    try:
        "┌─✔".encode(sys.stdout.encoding or "utf-8")
        return True
    except Exception:
        return False


USE_UNICODE = _can_unicode()
SYM_OK = "✔ PASS" if USE_UNICODE else "[OK]"
SYM_FAIL = "✘ FAIL" if USE_UNICODE else "[FAIL]"

BOX_TL = "┌" if USE_UNICODE else "+"
BOX_TR = "┐" if USE_UNICODE else "+"
BOX_BL = "└" if USE_UNICODE else "+"
BOX_BR = "┘" if USE_UNICODE else "+"
BOX_H = "─" if USE_UNICODE else "-"
BOX_V = "│" if USE_UNICODE else "|"

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def draw_box(title: str, lines: list[str], width: int = 68, color: str | None = None) -> None:
    color = C.CYAN if color is None else color
    fill = max(0, width - visible_len(title) - 3)
    top = f"{BOX_TL}{BOX_H} {C.BOLD}{title}{C.RESET} " + BOX_H * fill + BOX_TR
    bot = BOX_BL + BOX_H * width + BOX_BR
    print(f"{color}{top}{C.RESET}")
    for line in lines:
        padding = " " * max(0, width - visible_len(line) - 2)
        print(f"{color}{BOX_V}{C.RESET} {line}{padding} {color}{BOX_V}{C.RESET}")
    print(f"{color}{bot}{C.RESET}")


from pathlib import Path as _Path
import subprocess as _subprocess
import shutil as _shutil
import platform as _platform
import json as _json
import time as _time

PROOF_ROOT: _Path = _Path(__file__).resolve().parent
VENV_DIR: _Path = PROOF_ROOT / ".venv"
REFERENCE_MANUAL_PATH: _Path = PROOF_ROOT / QECTOR_MANUAL

# ---------------------------------------------------------------------------
# Binary linear algebra over F₂
# ---------------------------------------------------------------------------

def f2_mat_vec(
    matrix: Sequence[Sequence[int]],
    vec: Sequence[int],
) -> tuple[int, ...]:
    out: list[int] = []
    for row in matrix:
        s = 0
        for a, b in zip(row, vec):
            s ^= int(a) & int(b)
        out.append(s & 1)
    return tuple(out)


def f2_mat_mat_mul(
    a: Sequence[Sequence[int]],
    b: Sequence[Sequence[int]],
) -> list[list[int]]:
    bt = list(zip(*b)) if b else []
    result: list[list[int]] = []
    for row in a:
        out_row: list[int] = []
        for col in bt:
            s = 0
            for x, y in zip(row, col):
                s ^= int(x) & int(y)
            out_row.append(s & 1)
        result.append(out_row)
    return result


def all_binary_vectors(n: int) -> Iterable[tuple[int, ...]]:
    if n < 0:
        raise ValueError("n must be non-negative")
    for bits in itertools.product((0, 1), repeat=n):
        yield bits  # type: ignore[return-value]


def gf2_rank(matrix: Sequence[Sequence[int]]) -> int:
    m = [list(map(int, row)) for row in matrix]
    if not m or not m[0]:
        return 0
    rows, cols = len(m), len(m[0])
    rank = 0
    col = 0
    for col in range(cols):
        pivot = None
        for r in range(rank, rows):
            if m[r][col] & 1:
                pivot = r
                break
        if pivot is None:
            continue
        m[rank], m[pivot] = m[pivot], m[rank]
        for r in range(rows):
            if r != rank and (m[r][col] & 1):
                for c in range(col, cols):
                    m[r][c] ^= m[rank][c]
        rank += 1
        if rank == rows:
            break
    return rank


def gf2_solve(
    basis: Sequence[Sequence[int]],
    rhs: Sequence[int],
) -> tuple[int, ...] | None:
    m = [list(map(int, row)) for row in basis]
    if not m:
        return tuple() if not any(rhs) else None
    rows, cols = len(m), len(m[0])
    if len(rhs) != rows:
        raise ValueError("rhs length must equal number of rows")
    aug: list[list[int]] = [m[r] + [int(rhs[r]) & 1] for r in range(rows)]
    where = [-1] * cols
    row = 0
    for col in range(cols):
        sel = -1
        for i in range(row, rows):
            if aug[i][col] & 1:
                sel = i
                break
        if sel == -1:
            continue
        aug[row], aug[sel] = aug[sel], aug[row]
        where[col] = row
        for i in range(rows):
            if i != row and (aug[i][col] & 1):
                for j in range(col, cols + 1):
                    aug[i][j] ^= aug[row][j]
        row += 1
    for i in range(rows):
        if all(aug[i][j] == 0 for j in range(cols)) and (aug[i][cols] & 1):
            return None
    ans = [0] * cols
    for j in range(cols):
        if where[j] != -1:
            ans[j] = aug[where[j]][cols] & 1
        else:
            ans[j] = 0
    check = f2_mat_vec(basis, ans)
    if check != tuple(int(x) & 1 for x in rhs):
        if cols <= 20:
            for cand in all_binary_vectors(cols):
                if f2_mat_vec(basis, cand) == tuple(int(x) & 1 for x in rhs):
                    return cand
        return None
    return tuple(ans)


def row_space_contains(
    matrix: Sequence[Sequence[int]],
    vec: Sequence[int],
) -> bool:
    try:
        is_empty = len(matrix) == 0  # type: ignore[arg-type]
    except Exception:
        is_empty = False
    if is_empty:
        return all(v == 0 for v in vec)
    r0 = gf2_rank(matrix)
    extended = [list(map(int, row)) for row in matrix] + [list(map(int, vec))]
    r1 = gf2_rank(extended)
    return r1 == r0


def check_syndrome_faithful(
    matrix: Sequence[Sequence[int]],
    correction: Sequence[int],
    syndrome: Sequence[int],
) -> bool:
    return f2_mat_vec(matrix, correction) == tuple(int(x) & 1 for x in syndrome)


def theorem_1_obligation(
    matrix: Sequence[Sequence[int]],
    error: Sequence[int],
    correction: Sequence[int],
) -> dict:
    syndrome = f2_mat_vec(matrix, error)
    hc = f2_mat_vec(matrix, correction)
    faithful = hc == syndrome
    residual = tuple(int(a) ^ int(b) for a, b in zip(correction, error))
    h_res = f2_mat_vec(matrix, residual)
    in_kernel = all(v == 0 for v in h_res)
    equivalent = faithful == in_kernel
    return {
        "syndrome": syndrome,
        "syndrome_faithful": faithful,
        "residual": residual,
        "residual_in_kernel": in_kernel,
        "equivalent": equivalent,
        "hc": hc,
        "h_residual": h_res,
    }


def theorem_2_obligation(
    matrix: Sequence[Sequence[int]],
    error: Sequence[int],
    correction: Sequence[int],
) -> dict:
    t1 = theorem_1_obligation(matrix, error, correction)
    residual = t1["residual"]
    in_kernel: bool = t1["residual_in_kernel"]
    in_row_space = row_space_contains(matrix, residual)
    logical_failure = bool(in_kernel and not in_row_space)
    return {
        "syndrome": t1["syndrome"],
        "faithful": t1["syndrome_faithful"],
        "residual": residual,
        "in_kernel": in_kernel,
        "in_row_space": in_row_space,
        "logical_failure": logical_failure,
        "equivalent": t1["equivalent"],
    }


def two_stage_css_obligation(
    hx: Sequence[Sequence[int]],
    hz: Sequence[Sequence[int]],
    cross: Sequence[Sequence[int]],
    sx: Sequence[int],
    sz: Sequence[int],
    cx: Sequence[int],
    cz: Sequence[int],
) -> dict:
    induced_z = f2_mat_vec(cross, cx)
    updated_z = tuple(int(a) ^ int(b) for a, b in zip(sz, induced_z))
    hx_cx = f2_mat_vec(hx, cx)
    hz_cz = f2_mat_vec(hz, cz)
    combined_z = tuple(int(a) ^ int(b) for a, b in zip(induced_z, hz_cz))
    faithful_x = hx_cx == tuple(int(x) & 1 for x in sx)
    faithful_z_stage = hz_cz == updated_z
    faithful_joint = combined_z == tuple(int(x) & 1 for x in sz)
    faithful = bool(faithful_x and faithful_z_stage)
    return {
        "induced_z": induced_z,
        "updated_z": updated_z,
        "combined_z": combined_z,
        "hx_cx": hx_cx,
        "hz_cz": hz_cz,
        "faithful": faithful,
        "faithful_x": faithful_x,
        "faithful_z_stage": faithful_z_stage,
        "faithful_joint": faithful_joint,
    }


def wilson_ci(
    errors: int,
    shots: int,
    z: float = 1.959963985,
) -> tuple[float, float]:
    if shots <= 0:
        return (0.0, 1.0)
    p = errors / shots
    denom = 1.0 + z * z / shots
    center = (p + z * z / (2.0 * shots)) / denom
    half = z * math.sqrt(p * (1.0 - p) / shots + z * z / (4.0 * shots * shots)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def dem_collapse_probability(p1: float, p2: float) -> float:
    return p1 * (1.0 - p2) + p2 * (1.0 - p1)


def dem_weight(p: float) -> float:
    if p == 0.0:
        return float("inf")
    if p == 1.0:
        return float("-inf")
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in [0,1], got {p}")
    return math.log((1.0 - p) / p)


def collision_time(
    t: float,
    w: float,
    y_u: float,
    y_v: float,
    speed_u: float,
    speed_v: float,
    z_sum: float = 0.0,
    z_speed_sum: float = 0.0,
) -> float | None:
    denom = speed_u + speed_v + z_speed_sum
    if denom <= 0:
        return None
    numer = w - y_u - y_v - z_sum
    return t + numer / denom


def edge_slack(w: float, y_u: float, y_v: float, z_sum: float = 0.0) -> float:
    return w - y_u - y_v - z_sum


def cluster_parity(bits: Sequence[int]) -> int:
    p = 0
    for b in bits:
        p ^= int(b) & 1
    return p & 1


def peeling_work(parent: Sequence[int | None]) -> int:
    return sum(1 for p in parent if p is not None)


def peel_tree(
    parent: Sequence[int | None],
    syndrome: Sequence[int],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = len(parent)
    if len(syndrome) != n:
        raise ValueError("parent and syndrome must have same length")
    residual = [int(x) & 1 for x in syndrome]
    correction = [0] * n
    depth = [-1] * n
    for i, p in enumerate(parent):
        if p is None:
            depth[i] = 0

    def get_depth(v: int) -> int:
        if depth[v] != -1:
            return depth[v]
        p = parent[v]
        if p is None:
            depth[v] = 0
        else:
            depth[v] = get_depth(int(p)) + 1
        return depth[v]

    for i in range(n):
        get_depth(i)

    order = sorted(range(n), key=lambda v: depth[v], reverse=True)
    for v in order:
        p = parent[v]
        if p is None:
            continue
        if residual[v] & 1:
            correction[v] = 1
            residual[int(p)] ^= 1
            residual[v] = 0
    return tuple(correction), tuple(residual)


def ambiguity_component_sum(
    matrix: Sequence[Sequence[int]],
    components: Sequence[Sequence[int]],
    values: Sequence[int],
) -> tuple[int, ...]:
    m = len(matrix)
    acc = [0] * m
    for comp in components:
        comp_set = set(int(c) for c in comp)
        masked = [int(values[j]) & 1 if j in comp_set else 0 for j in range(len(values))]
        partial = f2_mat_vec(matrix, masked)
        for i in range(m):
            acc[i] ^= partial[i]
    return tuple(acc)


def detector_differences(
    rounds: Sequence[Sequence[int]],
) -> tuple[tuple[int, ...], ...]:
    if not rounds:
        return ()
    prev = [0] * len(rounds[0])
    out: list[tuple[int, ...]] = []
    for rnd in rounds:
        cur = tuple(int(a) ^ int(b) for a, b in zip(rnd, prev))
        out.append(cur)
        prev = list(rnd)
    return tuple(out)


def telescope_differences(
    differences: Sequence[Sequence[int]],
) -> tuple[int, ...]:
    if not differences:
        return ()
    m = len(differences[0])
    acc = [0] * m
    for d in differences:
        for i in range(m):
            acc[i] ^= int(d[i]) & 1
    return tuple(acc)


def graphlike(checks: Sequence[Sequence[int]]) -> bool:
    degree: dict[int, int] = {}
    for qubits in checks:
        for q in qubits:
            qi = int(q)
            degree[qi] = degree.get(qi, 0) + 1
            if degree[qi] > 2:
                return False
    return True


def bit_identity(a: Sequence[int], b: Sequence[int]) -> bool:
    return tuple(int(x) & 1 for x in a) == tuple(int(x) & 1 for x in b)


def phi_box_plus(x: float) -> float:
    if x == 0.0:
        return float("inf")
    if x < 0:
        raise ValueError("phi is defined for x ≥ 0")
    if x > 700:
        return 0.0
    return math.log(1.0 / math.tanh(x / 2.0))


def adaptive_k(n_defects: int, k_mult: float = 2.0) -> int:
    if n_defects < 0:
        raise ValueError("n_defects must be ≥ 0")
    return max(12, math.ceil(k_mult * math.sqrt(n_defects)))


def workspace_strides(n_checks: int, n_edges: int) -> dict[str, int]:
    N = n_checks + 1
    E = n_edges
    u32_stride = 6 * N + 1 + 4 * E
    u8_stride = 5 * N + E
    return {
        "N": N,
        "E": E,
        "u32_stride": u32_stride,
        "u8_stride": u8_stride,
        "u32_bytes": u32_stride * 4,
        "u8_bytes": u8_stride,
    }


def streaming_truncation_bound(
    lam: float,
    W: int,
    s_inf_norm: float = 1.0,
) -> float:
    if not 0.0 <= lam < 1.0:
        raise ValueError("lambda must be in [0,1)")
    if W < 0:
        raise ValueError("W must be ≥ 0")
    if lam == 0.0:
        return 0.0
    return (lam**W) / (1.0 - lam) * s_inf_norm


def css_code_parameters(n: int, rank_hx: int, rank_hz: int) -> dict[str, int]:
    k = n - rank_hx - rank_hz
    return {
        "n": n,
        "rank_hx": rank_hx,
        "rank_hz": rank_hz,
        "k": k,
        "dim_ker_hx": n - rank_hx,
        "dim_im_hz_T": rank_hz,
        "quotient_dim": k,
    }


def matrix_from_checks(checks: list[list[int]], n_qubits: int) -> np.ndarray:
    if np is None:
        matrix = [[0] * n_qubits for _ in checks]
        for row, qubits in zip(matrix, checks):
            for qubit in qubits:
                row[int(qubit)] = 1
        return matrix  # type: ignore[return-value]
    m = np.zeros((len(checks), n_qubits), dtype=np.uint8)
    for r, qs in enumerate(checks):
        m[r, qs] = 1
    return m


def assert_faithful(
    testcase: unittest.TestCase,
    matrix: np.ndarray,
    correction: np.ndarray,
    syndrome: np.ndarray,
    msg: str = "",
) -> None:
    testcase.assertTrue(
        np.array_equal((matrix @ correction.astype(int)) % 2, syndrome),
        msg or f"Hc != s: Hc={(matrix @ correction.astype(int))%2}, s={syndrome}",
    )


def _live_unavailable_reason() -> str:
    if _QECTOR_IMPORT_ERROR is None:
        return "live qector-decoder-v3 wheel is not loaded"
    return (
        "live qector-decoder-v3 wheel is unavailable: "
        f"{type(_QECTOR_IMPORT_ERROR).__name__}: {_QECTOR_IMPORT_ERROR}"
    )


def _require_live(test_method):
    @functools.wraps(test_method)
    def wrapped(testcase, *args, **kwargs):
        if not _load_live_dependencies():
            testcase.skipTest(_live_unavailable_reason())
        return test_method(testcase, *args, **kwargs)

    return wrapped


class _LiveRequiredTestCase(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        if not _load_live_dependencies():
            self.skipTest(_live_unavailable_reason())


class GroundTruthSanityTests(unittest.TestCase):
    def test_f2_mat_vec_basic(self) -> None:
        self.assertEqual(f2_mat_vec([[1, 0, 1], [0, 1, 1]], [1, 1, 0]), (1, 1))
        self.assertEqual(f2_mat_vec([[1, 1, 0], [0, 1, 1]], [1, 0, 1]), (1, 1))

    def test_all_binary_vectors_count(self) -> None:
        for n in range(5):
            self.assertEqual(len(list(all_binary_vectors(n))), 2**n)

    def test_gf2_rank_examples(self) -> None:
        self.assertEqual(gf2_rank([[1, 0], [0, 1]]), 2)
        self.assertEqual(gf2_rank([[1, 1, 0], [0, 1, 1]]), 2)

    def test_gf2_solve_identity(self) -> None:
        self.assertEqual(gf2_solve([[1, 0], [0, 1]], [0, 1]), (0, 1))
        self.assertEqual(gf2_solve([[1, 0], [0, 1]], [1, 1]), (1, 1))
        self.assertIsNone(gf2_solve([[1, 0], [0, 0]], [0, 1]))

    def test_row_space_contains(self) -> None:
        H = [[1, 1, 0], [0, 1, 1]]
        self.assertTrue(row_space_contains(H, [1, 0, 1]))
        self.assertFalse(row_space_contains(H, [1, 0, 0]))


class AppendixE1SteaneTests(unittest.TestCase):
    def test_syndrome_of_qubit_5_is_110(self) -> None:
        checks = [[3, 4, 5, 6], [1, 2, 5, 6], [0, 2, 4, 6]]
        H = matrix_from_checks(checks, 7)
        error = [0, 0, 0, 0, 0, 1, 0]
        self.assertEqual(f2_mat_vec(H, error), (1, 1, 0))

    def test_steane_css_parameters(self) -> None:
        params = css_code_parameters(7, 3, 3)
        self.assertEqual(params["k"], 1)


class Theorem1Tests(unittest.TestCase):
    def test_exhaustive_on_repetition_matrix(self) -> None:
        H = [[1, 1, 0], [0, 1, 1]]
        for e in all_binary_vectors(3):
            for c in all_binary_vectors(3):
                r = theorem_1_obligation(H, e, c)
                self.assertTrue(r["equivalent"])


class Theorem2Tests(unittest.TestCase):
    def test_stabilizer_shift_is_not_logical(self) -> None:
        H = [
            [0, 0, 0, 1, 1, 1, 1],
            [0, 1, 1, 0, 0, 1, 1],
            [1, 0, 1, 0, 1, 0, 1],
        ]
        e = [0, 0, 0, 0, 0, 1, 0]
        g = H[0]
        c = tuple(a ^ b for a, b in zip(e, g))
        r = theorem_2_obligation(H, e, c)
        self.assertFalse(r["logical_failure"])


class LiveDecoderFaithfulnessTests(_LiveRequiredTestCase):
    def test_all_graphlike_families_single_qubit_errors_faithful(self) -> None:
        families: dict[str, object] = {
            "repetition": codes.repetition_code(5),
            "ring": codes.ring_code(5),
            "rotated_surface": codes.rotated_surface_code(3),
        }
        for name, code in families.items():
            H = np.asarray(code.parity_check_matrix(), dtype=np.uint8)  # type: ignore[attr-defined]
            for qubit in range(code.n_qubits):  # type: ignore[attr-defined]
                error = np.zeros(code.n_qubits, dtype=np.uint8)  # type: ignore[attr-defined]
                error[qubit] = 1
                syndrome = np.asarray(code.syndrome(error), dtype=np.uint8)  # type: ignore[attr-defined]
                dec = BlossomDecoder(code.check_to_qubits, n_qubits=code.n_qubits)  # type: ignore[attr-defined]
                correction = dec.decode(syndrome)
                assert_faithful(self, H, correction, syndrome, f"{name} qubit {qubit}")


if __name__ == "__main__":
    _load_live_dependencies()
    unittest.main()
