# Contributing to QECTOR Decoder Workbench

Thank you for your interest in contributing to the QECTOR Decoder Workbench. This document explains the development workflow, code standards, and review process.

---

## Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/qectorlab/qector-decoder-workbench.git
   cd qector-decoder-workbench
   ```

2. **Create a virtual environment** (Python 3.11+)
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Linux / macOS
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt   # test and lint tooling
   ```

4. **Install the decoder backend**
   ```bash
   pip install wheels/qector_decoder_v3-1.0.0-cp311-cp311-win_amd64.whl
   ```

5. **Run the test suite** to confirm everything works
   ```bash
   pytest tests/ -v
   ```

---

## Code Style

| Tool | Purpose | Config |
|------|---------|--------|
| **ruff** | Linting and import sorting | `pyproject.toml [tool.ruff]` |
| **black** | Code formatting (line length 120) | `pyproject.toml [tool.black]` |
| **mypy** | Static type checking | `pyproject.toml [tool.mypy]` |

Run all checks before committing:
```bash
ruff check .
black --check .
mypy --strict backend.py utils.py cli.py
```

---

## Test Requirements

- All new code must include tests in `tests/`.
- Minimum coverage: **85%** on new modules.
- Tests must pass on Python 3.11 and 3.12 on Windows, Linux, and macOS.
- Never mock the decoder backend in integration tests; use real decode calls.
- Mark slow tests with `@pytest.mark.slow` and GUI-dependent tests with `@pytest.mark.gui`.

Run the full suite:
```bash
pytest tests/ -v --timeout=120
```

---

## Pull Request Process

1. **Create a feature branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following the code style above.

3. **Write or update tests** covering your changes.

4. **Run the full test suite** and verify all tests pass.

5. **Sync the three OS trees** (root, `Linux/`, `Mac/`):
   ```bash
   python scripts/sync_trees.py
   ```

6. **Open a Pull Request** with:
   - A clear title describing the change
   - A description explaining *why* the change is needed
   - References to any related issues
   - Test evidence (e.g. "426 passed, 4 skipped, 0 failed")

7. **Address review feedback** promptly. PRs require at least one approval.

---

## Code Review Expectations

- Every PR is reviewed for correctness, test coverage, and style.
- Reviewers will check that:
  - New public functions have docstrings
  - Error paths are tested
  - No hardcoded paths or credentials are introduced
  - Changes are synced across root, `Linux/`, and `Mac/` trees
- Reviewers should respond within 48 hours.

---

## Reporting Bugs

Use GitHub Issues with the **Bug** template. Include:
- Steps to reproduce
- Expected vs actual behaviour
- OS, Python version, decoder version (`python cli.py version`)
- Full traceback if available

---

## Feature Requests

Use GitHub Issues with the **Feature Request** template. Describe:
- The scientific use case
- The expected user workflow
- Any reference implementations or papers

---

## Contact

For questions about contributing, email **admin@qector.store**.
