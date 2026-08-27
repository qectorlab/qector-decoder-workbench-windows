# QECTOR Desktop Packaging

QECTOR packages the Workbench UI, Python runtime, GUI libraries, scientific
dependencies, and an offline fallback copy of the `qector-decoder-v3` wheel.
The decoder itself is a separately versioned component.  Normally it is bundled
into the frozen app so the workbench runs with no external Python or network.
If the bundled package cannot be imported (corruption, ABI mismatch, or a user
wiping the managed site), the provisioner extracts the shipped wheel into a
per-user managed site as a self-healing fallback; network provisioning is the
last resort.

## Runtime decoder contract

At every launch QECTOR first tries the decoder bundled inside the app, then its
managed decoder site, then the normal Python installation.  If no supported
decoder is present, it extracts the bundled wheel (if one is included in the
build) or installs a wheel-only release from `https://pypi.org/simple` with
`--no-deps` into the managed site.  Releases live in versioned directories; an
atomic `active.json` pointer changes only after a successful installation and
import verification, so a failed upgrade leaves the previous decoder usable.

The managed site is **partitioned by interpreter ABI**  -  installs live under an
`<cache_tag>-<arch>` subdirectory (e.g. `cpython-311-x8664`)  -  so a frozen
Python 3.11 app and a Python 3.12 source run keep entirely separate decoders and
never load each other's incompatible compiled extension. Presence is judged by
**actually importing** the decoder (the native `.pyd`/`.so` must load), not by
dist metadata alone: a wheel built for a different Python ABI is treated as
absent and reinstalled with a compatible interpreter, and a freshly installed
wheel is verified to import before it is accepted.

Frozen apps are standalone: the decoder package and a matching wheel are bundled,
so no external Python, pip, or network is required on first launch.  A system
Python with pip is only needed if both the bundled package and the bundled wheel
are unavailable and the app must fall back to a PyPI install.  The app discovers
a compatible interpreter from `QECTOR_PYTHON`, `py -<major>.<minor>` on Windows,
or `python<major>.<minor>` / `python3` / `python` on PATH. Set `QECTOR_PYTHON` to
the explicit interpreter path if discovery fails. The launcher shows a clear error
rather than loading a possibly ABI-incompatible compiled wheel.

The default managed locations are
`%LOCALAPPDATA%\\QectorWorkbench\\decoder_site\\<abi_tag>` (Windows),
`~/Library/Application Support/QectorWorkbench/decoder_site/<abi_tag>` (macOS),
and `$XDG_DATA_HOME/QectorWorkbench/decoder_site/<abi_tag>` or
`~/.local/share/QectorWorkbench/decoder_site/<abi_tag>` (Linux), where
`<abi_tag>` is the running interpreter's ABI (e.g. `cpython-311-x8664`). Set
`QECTOR_DATA_DIR` to relocate all QECTOR user data and `QECTOR_PYTHON` to pin the
interpreter used for provisioning. At every launch the provisioner also purges
managed decoder versions older than the minimum supported release before
activating the bundled wheel, so upgrading from an older build needs no manual
cleanup. As of workbench v0.5.2 there are no background upgrade checks  -  the
bundled wheel is the single source of truth.

## Build artifacts

| Platform | Build command | Primary artifact |
| --- | --- | --- |
| Windows x64 | `python -m PyInstaller --clean -y QectorWorkbench.spec` | `dist/QectorWorkbench/` |
| Windows portable | `python -m PyInstaller --clean -y QectorWorkbench-onefile.spec` | `dist/QectorWorkbench-Portable.exe` |
| Linux x86_64 | `cd Linux && ./compile.sh --docker --test` | `Linux/dist/*.AppImage` |
| Linux .deb | `cd Linux && ./build_installers.sh --test` | `Linux/dist/*.deb` |
| macOS arm64 / x86_64 | `cd Mac && ./build_macos.sh --arch <arch> --test` | `Mac/dist/*.dmg` |

PyInstaller cannot cross-compile macOS applications. Linux AppImages and .deb
packages are built through Docker on this host; macOS packaging must run on a
Mac or macOS CI runner. The macOS build is ad-hoc signed only until a Developer
ID signing and notarization step is completed.

## Release verification

Before distribution, run the platform source test suite, launch the artifact
with `--mcp`, and verify that `qector_decoder_v3` is absent from the package
payload. Then generate SHA-256 checksums for every artifact and include this
document and the platform README in the release archive.
