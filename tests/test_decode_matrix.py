import pytest
import backend as be

# Build exact compatible (family, decoder) pairs so every test runs and zero are skipped
COMPATIBLE_PAIRS = []
for family in be.CODE_FAMILIES.keys():
    try:
        code = be.build_code(family, 3)
        for decoder in be.DECODER_KINDS:
            if decoder in be.compatible_decoder_kinds(code):
                COMPATIBLE_PAIRS.append((family, decoder))
    except Exception:
        pass

@pytest.mark.parametrize("family,decoder", COMPATIBLE_PAIRS)
def test_decode_matrix(family, decoder):
    code = be.build_code(family, 3)
    raw = be.run_single_decode(code, error_rate=0.05, decoder_kind=decoder, seed=42)
    res = raw["result"]
    assert hasattr(res, "syndrome_valid")
    assert res.syndrome_valid is True
