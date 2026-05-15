# Contributing to hunt

Thank you for your interest in contributing. hunt is a small, focused framework — contributions that keep it simple and well-tested are most welcome.

---

## Ways to contribute

- **Bug reports** — open an issue with a minimal reproduction case
- **Bug fixes** — open a PR against `main` with a test that fails before your fix and passes after
- **Documentation** — fixes, clarifications, and new examples in the docs site (hunt-docs)
- **Feature proposals** — open an issue first to discuss before writing code; small focused features are preferred over large ones

---

## Development setup

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/)

```bash
git clone git@github.com:hunt-core/hunt.git
cd hunt
uv venv && uv pip install -e ".[dev]"
```

Run the test suite:

```bash
pytest
```

---

## Code style

hunt uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting (configured in `pyproject.toml`).

```bash
uv tool run ruff check src/        # lint
uv tool run ruff format src/       # format
uv tool run ruff check src/ --fix  # auto-fix lint issues
```

Rules enforced: `E`, `W`, `F`, `I` (import order), `UP` (pyupgrade), `B` (bugbear), `RUF`. Line length 120.

CI will fail if either check reports errors, so run both before opening a PR.

Additional conventions:

- Type annotations on all public functions and methods
- No comments unless the *why* is genuinely non-obvious
- No docstrings on simple functions — the name and types should speak for themselves

---

## Tests

Every bug fix must include a regression test. New features must include tests covering the happy path and at least one edge case. Tests live in `tests/` and use pytest.

```bash
pytest                  # run all tests
pytest tests/test_orm.py  # run a specific file
pytest -k "test_create"   # run matching tests
```

---

## Pull requests

1. Fork the repository and create a branch from `main`
2. Make your changes — keep commits small and focused
3. Ensure `ruff check src/`, `ruff format --check src/`, and `pytest` all pass
4. Open a PR against `main` with a clear description of the change and why

PR titles should follow the pattern:

```
fix: <short description>
feat: <short description>
docs: <short description>
refactor: <short description>
```

---

## Versioning

hunt increments the patch version (third number) for every change. You do not need to bump the version in your PR — maintainers handle that on merge.

---

## Reporting security issues

Do **not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

---

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
