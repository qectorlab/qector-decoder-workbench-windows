QECTOR Decoder Workbench - Public Documentation Set
====================================================

Application version : 1.0.2
Decoder backend : qector-decoder-v3 1.0.0 (bundled wheel, offline activation on first launch)
MCP tools : 85
Decoders : 17
Code families : 10
Generated : 2026-08-21 12:30 UTC

Archival Zenodo DOIs:
 User Manual & Licensing : https://doi.org/10.5281/zenodo.21363016
 Performance Benchmarks : https://doi.org/10.5281/zenodo.21339300
 Architecture Whitepaper : https://doi.org/10.5281/zenodo.21320543

Contents of this documentation set:

 QECTOR_User_Manual_Windows.pdf Full user manual, Windows edition
 QECTOR_User_Manual_Linux.pdf Full user manual, Linux edition
 QECTOR_User_Manual_macOS.pdf Full user manual, macOS edition
 QECTOR_Quick_Start_Guide.pdf One-minute install and first decode (all platforms)
 QECTOR_MCP_Integration_Guide.pdf Connect AI agents and clients to the MCP server
 QECTOR_API_Reference.md Complete API reference (backend, MCP tools, schemas)
 QECTOR_API_Reference.pdf Printable API reference with figures
 QECTOR_LLM_Manual.json Machine-readable tool manual for LLM agents
 README.txt This index file

The application bundles the decoder wheel inside. On first launch it activates
qector-decoder-v3 from the bundled wheel automatically, no internet connection,
Python, or pip is needed on any platform, and any outdated managed decoder left by
an older release is purged automatically before activation.

A splash screen appears within about a second of launch and closes when the main window
is ready. The first launch is the slow one. If the app ever fails to start, logs/boot.log
in the per-user data directory records every provisioning step and the exact import error.

Note on OpenCL: the published qector-decoder-v3 wheel is built without its OpenCL feature,
so the OpenCL backend reports unavailable even on machines that expose OpenCL devices.
That is a property of the build, not a driver fault, and no environment variable enables
it. CUDA and CPU are unaffected.

Licensing:
 Source-available. Free for academic, personal and non-commercial research.
 In the app : Documentation tab > Developer and Licensing > offline local licensing

Project: https://www.qector.store
Attribution: Guillaume Lessard / iD01t Productions
ORCID: 0009-0000-3465-3753
