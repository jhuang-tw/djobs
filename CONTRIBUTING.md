# Contributing to djobs

Thanks for your interest! Here's how to get started.

## Development Setup

```bash
git clone https://github.com/jhuang-tw/djobs.git
cd djobs
python -m venv .venv
.venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,mcp]"
```

## Running Tests

```bash
pytest -q                 # all tests (skips Postgres unless available)
pytest tests/unit -q      # unit tests only
ruff check src/ tests/    # lint
ruff format src/ tests/   # auto-format
```

## Pull Request Guidelines

1. Fork the repo and create a feature branch from `main`.
2. Keep commits focused — one logical change per commit.
3. Add or update tests for any new behavior.
4. Ensure `pytest -q` and `ruff check` pass before submitting.
5. Write a clear PR description explaining *what* and *why*.

## Code Style

- Python 3.11+, type hints everywhere.
- `ruff` for linting and formatting (config in `pyproject.toml`).
- `src` layout — all package code lives under `src/djobs/`.
- Tests mirror the source tree: `tests/unit/`, `tests/integration/`.

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for module boundaries and design decisions.

## Reporting Issues

Open a GitHub issue with:
- What you expected vs. what happened.
- Minimal reproduction steps.
- Python version and OS.
