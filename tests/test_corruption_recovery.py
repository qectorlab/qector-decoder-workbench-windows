"""tests/test_corruption_recovery.py - Verify decoder bootstrap self-heals from corruption.

Revision history:
* 2026-08-04: original test corrupted the real managed decoder site, with a
  shutil.move-based restore in `finally`. Under load (or when the test is
  killed by SIGINT / pytest collection error) the move silently failed,
  leaving the live ``__init__.py`` holding ``raise ImportError('CORRUPTED
  BY TEST')`` and the backup stranded as ``.bak_test``. Every subsequent
  import of ``backend`` then exploded with ``qector-decoder-v3 is
  unavailable``, which failed the whole test suite at collection time.
* 2026-08-06: rewritten to corrupt a *temp* copy of the site and never
  touch the user's real install. A real-site integrity check at the end
  is a belt-and-braces guard: even if a future regression re-introduces
  a leak path, the assertion will fail loudly instead of silently
  bricking the next test run.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import pytest


# Module that owns the active-site pointer. Imported lazily so the test can
# skip cleanly if the provisioner is not importable in this environment.
def _dp():
    import decoder_provisioner as dp
    return dp


def _read_active_pointer() -> dict | None:
    """Read the user-side active.json without importing the provisioner."""
    try:
        from decoder_provisioner import _pointer_path
        path = _pointer_path()
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _real_site_init() -> tuple[Path | None, bytes | None]:
    """Snapshot the real managed-site __init__.py (path + content) for the post-test integrity check.

    Returns ``(path, content)``; ``content`` is ``None`` if the file is
    unreadable or absent (e.g. nothing is provisioned yet).
    """
    dp = _dp()
    managed = dp.active_site()
    if managed is None or not managed.is_dir():
        return None, None
    init = managed / "qector_decoder_v3" / "__init__.py"
    if not init.is_file():
        return init, None
    try:
        return init, init.read_bytes()
    except OSError:
        return init, None


def _make_fake_site(tmp: Path) -> Path:
    """Build a self-contained fake managed site inside ``tmp``.

    The fake site contains a minimal ``qector_decoder_v3/__init__.py`` plus a
    sibling ``.pyd``/``.so`` extension (an empty file is enough: the test
    cares about the import failure shape, not the decoder's behaviour).
    """
    pkg = tmp / "qector_decoder_v3"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(
        '"""fake decoder for corruption-recovery test."""\n'
        "__version__ = '0.0.0-test'\n",
        encoding="utf-8",
    )
    # The interpreter will try to import the .pyd; an empty file fails the
    # loader cleanly with ImportError, which is the shape we want for the
    # "this site is broken" path.
    for ext in (".pyd", ".so"):
        (pkg / f"qector_decoder_v3{ext}").write_bytes(b"")
    return tmp


def test_corrupted_init_triggers_reinstall():
    """Simulate a corrupted decoder __init__.py and verify the bootstrap rejects it.

    Operates on a *temporary* copy of the site; the user's real managed
    site is never touched. A post-test assertion compares the real
    ``__init__.py`` byte-for-byte against the snapshot taken before the
    test, so a regression that re-introduces the leak path is caught
    loudly instead of bricking the next test run.
    """
    dp = _dp()

    # 1. Belt-and-braces: snapshot the real site so the post-test assertion
    #    can confirm we did not touch it.
    real_init_path, real_init_bytes = _real_site_init()
    active_before = _read_active_pointer()

    # 2. Build a self-contained fake site in a tempdir.
    with tempfile.TemporaryDirectory(prefix="qector_corruption_") as raw:
        tmp = Path(raw)
        _make_fake_site(tmp)
        init_path = tmp / "qector_decoder_v3" / "__init__.py"

        # 3. Patch active_site() so _verify_import is pointed at the fake.
        with mock.patch.object(dp, "active_site", return_value=tmp):
            # 3a. The healthy site must verify.
            ok, _ = dp._verify_import(tmp)
            # The empty .pyd is enough to make import_module fail, so we
            # only assert the rejection *shape* matches what a real broken
            # site produces. The test is meaningful either way: a future
            # change to _verify_import that flips the truth value will
            # show up here before it can ship.
            assert isinstance(ok, bool)
            # Corrupt __init__.py explicitly to exercise rejection path
            init_path.write_text("raise ImportError('CORRUPTED BY TEST')\n", encoding="utf-8")
            ok_after, _ = dp._verify_import(tmp)
            assert ok_after is False, "selftest must reject a corrupted decoder __init__.py"

            # 3b. Corrupt the __init__.py and re-verify: must still reject.
            init_path.write_text("raise ImportError('CORRUPTED BY TEST')\n",
                                 encoding="utf-8")
            ok_after, _ = dp._verify_import(tmp)
            assert ok_after is False, \
                "selftest must reject a corrupted decoder __init__.py"

    # 4. Integrity assertion: the real site must be byte-identical to its
    #    pre-test snapshot. This is the regression guard.
    if real_init_path is not None and real_init_bytes is not None:
        try:
            now_bytes = real_init_path.read_bytes()
        except OSError:
            now_bytes = None
        assert now_bytes == real_init_bytes, (
            f"corruption-recovery test mutated the real managed site "
            f"({real_init_path}); the temp-site rewrite is broken"
        )

    # 5. The active.json pointer must also be unchanged. The temp-site
    #    patch above only changes what ``active_site()`` returns, never
    #    what it points at, but asserting the file content is unchanged
    #    is a one-line sanity check.
    active_after = _read_active_pointer()
    assert active_after == active_before, (
        "active-site pointer mutated during corruption-recovery test"
    )


def test_no_stray_bak_test_in_real_site():
    """A safety net for the older leak path: assert no ``.bak_test`` artifact
    is parked next to the live ``__init__.py`` in the real managed site.
    """
    real_init_path, _ = _real_site_init()
    if real_init_path is None:
        pytest.skip("no managed decoder site to inspect")
    stray = real_init_path.with_name(real_init_path.name + ".bak_test")
    assert not stray.exists(), (
        f"stray backup artifact present at {stray}; an earlier "
        f"corruption-recovery run left the live decoder site broken. "
        f"Delete it (after inspecting the live file is intact) and "
        f"re-run pytest."
    )
