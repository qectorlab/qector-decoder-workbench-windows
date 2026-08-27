# -*- mode: python ; coding: utf-8 -*-
# ==========================================================================
# QectorWorkbench-onefile.spec  —  PRODUCTION (bundled offline decoder wheel)
# ==========================================================================
# The decoder (qector-decoder-v3) ships as a bundled .whl data file.  On first
# launch the app's decoder_provisioner.py purges any outdated managed decoder
# site (< MIN_BACKEND_VERSION), extracts the bundled wheel into the ABI-scoped
# managed user site, and activates it — fully offline, no PyPI access needed.
# ==========================================================================
from PyInstaller.utils.hooks import (
    collect_submodules, collect_data_files, collect_dynamic_libs,
)

import sys
import os
SPEC = os.path.abspath(SPECPATH)
def P(rel: str) -> str:
    return os.path.join(SPEC, rel)
sys.path.insert(0, SPEC)
import version as _qector_version  # noqa: E402

app_version = _qector_version.WORKBENCH_VERSION
backend_version = _qector_version.BACKEND_VERSION

hiddenimports = [
    # ---------- QECTOR app modules (keep in sync with APP_MODULES) ----------
    'app', 'backend', 'state', 'theme', 'utils', 'logger', 'console', 'version',
    'version_service', 'decoder_provisioner', 'doc_generator',
    'threading_utils', 'results_tracker', 'hardware_routing',
    'mcp_server', 'mcp_resources', 'dialogs', 'autodebug', 'cli',
    'code_explorer_tab', 'decoder_lab_tab', 'benchmark_tab',
    'batch_streaming_tab', 'hardware_tab', 'diagnostics_tab', 'documentation_tab',
    'lab_info_tab', 'history_tab', 'compliance', 'entra_auth',
    'generate_manuals', 'api_reference', 'docs_exporter',
    'boot_test_runner', 'self_autodebug_backend', 'certification',
    # ---------- Runtime deps of the decoder ----------
    'cffi', '_cffi_backend',
    # ---------- In-app official docs export ----------
    'reportlab', 'reportlab.platypus', 'reportlab.lib', 'reportlab.lib.pagesizes',
    'reportlab.lib.units', 'reportlab.lib.colors', 'reportlab.lib.styles',
    'reportlab.lib.enums', 'reportlab.platypus.tableofcontents',
    'matplotlib.backends.backend_svg', 'matplotlib.backends.backend_pdf',
] + collect_submodules('customtkinter') + collect_submodules('cryptography')

_raw_datas = [
    (P('icon.jpg'), '.'), (P('icon.ico'), '.'), (P('icon.png'), '.'), (P('logo_banner.png'), '.'),
    (P('EULA.txt'), '.'), (P('README.md'), '.'),
    ('wheels/*', 'wheels'),
    (P('assets/logo_banner.png'), 'assets'), (P('assets/icon.png'), 'assets'), (P('assets/icon.ico'), 'assets'), (P('assets/splash.png'), 'assets'),
]
datas = [(s, d) for s, d in _raw_datas if os.path.exists(s) or '*' in s] + collect_data_files('customtkinter')

binaries = collect_dynamic_libs('cryptography') + collect_dynamic_libs('cffi')

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # The decoder package is NOT importable from the bundle — it ships as wheels/*.whl (offline).
        'qector_decoder_v3',
        # --- DEV/INTERNAL: never bundled (0 dev files policy) ---
        'pytest', '_pytest', 'pluggy', 'tests', 'test', 'todo_lab', 'todo_all3',
        # Heavy ML / notebook frameworks: unused at runtime.
        'torch', 'tensorflow', 'jax', 'pandas', 'notebook',
        'matplotlib.tests', 'matplotlib.testing',
        'scipy.tests', 'scipy.testing',
        # GPU acceleration (optional; users who want GPU run from source).
        'cupy', 'cupy_backends', 'cupyx', 'fastrlock',
        # Interactive tooling pulled in transitively.
        'IPython', 'jedi', 'notebook', 'nbconvert', 'nbformat', 'jupyter_client',
        'ipykernel', 'tornado', 'jinja2', 'pygments', 'yaml',
        'Cython', 'cython', 'pyximport',
    ],
    noarchive=False,
    optimize=0,
)

# Scrub any accidentally collected qector_decoder_v3 files (except the .whl)
a.pure = [p for p in a.pure if not p[0].startswith('qector_decoder_v3')]
a.binaries = [b for b in a.binaries if not b[0].startswith('qector_decoder_v3\\') and not b[0].startswith('qector_decoder_v3/')]
a.datas = [d for d in a.datas if not d[0].startswith('qector_decoder_v3\\') and not d[0].startswith('qector_decoder_v3/') and not (d[0].startswith('qector_decoder_v3') and not d[0].endswith('.whl'))]

# --- PRODUCTION PURITY: 0 dev/internal files/docs/scripts bundled ---
_DEV_DIRS = {'tests', '.venv', 'venv', '__pycache__', '.git', '.pytest_cache', '.mypy_cache', '.ruff_cache', 'htmlcov', 'build', 'dist', 'scripts', 'docs', '.github', 'winzip', 'linuxzip'}
_DEV_FILES = {'.coverage', 'coverage.xml'}
def _is_dev(path: str) -> bool:
    low = path.replace('\\', '/').lower()
    parts = low.split('/')
    if any(p in _DEV_DIRS for p in parts):
        return True
    base = parts[-1] if parts else ''
    if base in _DEV_FILES or base.startswith('todo_') or base.startswith('test_'):
        if base in ('boot_test_runner.py', 'self_autodebug_backend.py'):
            return False
        return True
    return False
a.pure = [p for p in a.pure if not _is_dev(p[0])]
a.datas = [d for d in a.datas if not _is_dev(d[0]) or d[0].lower().endswith('.whl') or 'customtkinter' in d[0].lower() or 'cryptography' in d[0].lower()]
a.binaries = [b for b in a.binaries if not _is_dev(b[0])]

pyz = PYZ(a.pure)

# Boot splash: the bootloader paints this before Python even starts, so the
# onefile unpack + cold Rust/PyO3 decoder import are never an invisible wait.
# main.py writes progress into it via pyi_splash and closes it once the real
# window is mapped.
splash = Splash(
    P('assets/splash.png'),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(40, 205),
    text_size=9,
    text_color='#8294ad',
    text_default='Starting QECTOR Decoder Workbench...',
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.dependencies,
    a.binaries,
    a.datas,
    [],
    name='QectorWorkbench-Portable',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=P('icon.ico'),
)

