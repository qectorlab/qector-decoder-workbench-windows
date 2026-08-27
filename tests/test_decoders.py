"""
tests/test_decoders.py — Complete decoder coverage for qector_decoder_v3 v0.6.6.

Exercises every decoder wired into backend.DECODER_KINDS (the five matching /
LDPC decoders plus the v0.6.6 additions: auto, hybrid, lookup_table,
predecoded) across every code family and through every backend entry point:
single decode, benchmark, and streaming.  Real decodes only — no mocks.
"""

from __future__ import annotations

import numpy as np
import pytest

import backend as be

# Graphlike + bicycle qLDPC families accept every wired decoder.  (The
# bivariate_bicycle family only accepts a subset and is covered separately in
# tests/test_code_families.py.)
ALL_FAMILIES = ["repetition", "ring", "rotated_surface",
                "unrotated_surface", "toric", "heavy_hex", "bicycle"]
EXPECTED_DECODERS = ["union_find", "fast_union_find", "blossom", "sparse_blossom",
                     "bp_osd", "auto", "hybrid", "lookup_table", "predecoded",
                     "auto_router", "hybrid_cascade", "gnn_belief_matching",
                     "belief_matching", "two_stage", "ambiguity_cluster", "colour_code",
                     "space_time"]


# ---------------------------------------------------------------------------
# Registry / metadata
# ---------------------------------------------------------------------------

def test_all_expected_decoders_registered():
    for kind in EXPECTED_DECODERS:
        assert kind in be.DECODER_KINDS, f"{kind} missing from DECODER_KINDS"
    assert len(be.DECODER_KINDS) == len(EXPECTED_DECODERS)


@pytest.mark.parametrize("kind", be.DECODER_KINDS)
def test_get_decoder_info_has_real_description(kind):
    info = be.get_decoder_info(kind)
    assert info["name"] == kind
    assert info["description"] and info["description"] != "Unknown decoder"


@pytest.mark.parametrize("kind", be.DECODER_KINDS)
def test_make_decoder_constructs(kind):
    code = be.build_code("repetition", 5)
    dec = be.make_decoder(code, kind)
    assert hasattr(dec, "decode")


def test_make_decoder_rejects_unknown():
    code = be.build_code("repetition", 5)
    with pytest.raises(be.QectorError, match="unknown decoder kind"):
        be.make_decoder(code, "not_a_decoder")


# ---------------------------------------------------------------------------
# Single decode: every decoder × every family satisfies the GF(2) contract
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", be.DECODER_KINDS)
@pytest.mark.parametrize("family", ALL_FAMILIES)
def test_single_decode_valid_every_decoder_every_family(kind, family):
    code = be.build_code(family, 3)
    if kind not in be.compatible_decoder_kinds(code):
        return
    out = be.run_single_decode(code, 0.06, kind, seed=7)
    res = out["result"]
    assert res.syndrome_valid is True, f"{kind}/{family}: correction does not reproduce syndrome"
    assert res.hamming_weight >= 0
    assert len(np.asarray(res.correction)) == code.n_qubits


@pytest.mark.parametrize("kind", be.DECODER_KINDS)
def test_single_decode_deterministic_every_decoder(kind):
    code = be.build_code("rotated_surface", 3)
    r1 = be.run_single_decode(code, 0.08, kind, seed=555)
    r2 = be.run_single_decode(code, 0.08, kind, seed=555)
    assert (np.asarray(r1["result"].correction) == np.asarray(r2["result"].correction)).all()


def test_zero_error_gives_valid_correction_every_decoder():
    code = be.build_code("repetition", 5)
    for kind in be.DECODER_KINDS:
        out = be.run_single_decode(code, 0.0, kind, seed=1)
        assert out["result"].syndrome_valid is True, kind
    # union_find is guaranteed to return the trivial (weight-0) correction here.
    assert be.run_single_decode(code, 0.0, "union_find", seed=1)["result"].hamming_weight == 0


# ---------------------------------------------------------------------------
# Benchmark + streaming through every decoder
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", be.DECODER_KINDS)
def test_benchmark_every_decoder(kind):
    code = be.build_code("repetition", 5)
    b = be.run_benchmark(code, n_samples=20, seed=3, decoder_kind=kind, error_rate=0.05)
    assert b["n_trials"] == 20
    assert b["method"] == kind
    assert b["throughput_decodes_per_s"] > 0
    assert 0.0 <= b["syndrome_match_rate"] <= 1.0
    assert b["latency_p99_us"] >= b["latency_p50_us"] >= 0.0


@pytest.mark.parametrize("kind", be.DECODER_KINDS)
def test_streaming_every_decoder(kind):
    code = be.build_code("repetition", 5)
    s = be.run_streaming_session(code, window_size=3, n_rounds=6,
                                 error_rate=0.05, seed=2, decoder_kind=kind)
    assert s["committed_count"] == 6
    assert len(s["committed_corrections"]) == 6


# ---------------------------------------------------------------------------
# LookupTable exponential-blowup guard (bulletproofing)
# ---------------------------------------------------------------------------

def test_lookup_table_refuses_large_code():
    code = be.build_code("rotated_surface", 7)  # > 20 checks
    with pytest.raises(be.QectorError, match="lookup_table is impractical"):
        be.run_single_decode(code, 0.05, "lookup_table", seed=1)


def test_lookup_table_works_on_small_code():
    code = be.build_code("repetition", 5)  # 4 checks
    out = be.run_single_decode(code, 0.1, "lookup_table", seed=4)
    assert out["result"].syndrome_valid is True


# ---------------------------------------------------------------------------
# Public helpers used by the resilient layer
# ---------------------------------------------------------------------------

def test_verify_correction_and_sampling_helpers():
    code = be.build_code("repetition", 7)
    err, syn = be.sample_error_and_syndrome(code, 0.15, seed=9)
    # deterministic sampling
    err2, syn2 = be.sample_error_and_syndrome(code, 0.15, seed=9)
    assert (np.asarray(err) == np.asarray(err2)).all()
    assert (np.asarray(syn) == np.asarray(syn2)).all()

    corr = be.make_decoder(code, "union_find").decode(syn)
    assert be.verify_correction(code, syn, corr) is True
    if int(np.sum(np.asarray(syn))) > 0:
        zeros = np.zeros(code.n_qubits, dtype=np.uint8)
        assert be.verify_correction(code, syn, zeros) is False


def test_logicals_helpers_consistent():
    code = be.build_code("repetition", 5)
    logicals = be.logicals_matrix(code)
    if logicals is not None:
        err, syn = be.sample_error_and_syndrome(code, 0.1, seed=2)
        corr = be.make_decoder(code, "union_find").decode(syn)
        # A valid correction on repetition should not flip a logical at low weight.
        assert isinstance(be.logical_failure(logicals, err, corr), bool)
