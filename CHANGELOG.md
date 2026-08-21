# Changelog

## 1.0.1

- Windows, Linux, and macOS source trees share the same application runtime.
- Portable builds enforce mandatory zero-egress execution.
- External-link buttons and browser actions were removed from the application.
- Entra ID posture is fail-closed and reports disabled in air-gapped labs.
- MCP registry is synchronized at 85 tools.
- Decoder, DEM, matrix, memmap, import, CLI, and documentation workflows are
  covered by the release test suite.
- Benchmark measurements are no longer stored or shipped; benchmark runs are
  local to the target hardware.
- Release inputs use canonical `README.md`, `EULA.txt`, and platform assets.
