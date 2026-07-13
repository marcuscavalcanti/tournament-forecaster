# Contributing

## Development Setup

Use Python 3.11, 3.12, or 3.13.

```bash
git clone https://github.com/marcuscavalcanti/worldcup2026.git
cd worldcup2026
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

## Change Process

1. Open an issue for behavior or contract changes that need design agreement.
2. Keep the deterministic engine offline and preserve completed-result locking.
3. Add a failing test first, confirm the failure, then implement the smallest passing change.
4. Do not commit credentials, local configuration, raw provider responses, generated outputs, caches, attachments, personal paths, or protected logos.
5. Update schemas, examples, and documentation when a public contract changes.
6. Submit a focused pull request with test evidence and provenance for factual data.

## Local Checks

```bash
ruff check src/tournament_forecaster tests/tournament_forecaster tests/presets tests/examples tests/test_tournament_forecast_cli.py tests/test_clean_wheel.py tests/test_clean_source_install.py tests/test_public_repository_contract.py tests/test_readme_diagrams.py scripts/check_english_surface.py docs/assets/architecture/generate.py --exclude src/tournament_forecaster/compatibility --exclude tests/tournament_forecaster/test_legacy_compatibility.py --select E4,E7,E9,F
ruff check src/tournament_forecaster/providers/security.py scripts/check_english_surface.py tests/test_clean_wheel.py tests/test_clean_source_install.py tests/test_public_repository_contract.py tests/test_readme_diagrams.py tests/tournament_forecaster/test_results_provider.py tests/tournament_forecaster/test_odds_provider.py docs/assets/architecture/generate.py --select E,F,I,UP,B,SIM
uv run --locked --extra dev mypy --no-incremental --exclude '^worldcup_brazil/' src/tournament_forecaster scripts/check_english_surface.py docs/assets/architecture/generate.py
python scripts/check_english_surface.py
pytest -q --disable-socket tests/test_public_repository_contract.py tests/presets tests/examples tests/test_clean_wheel.py
python -m build
python -m twine check dist/*
```

Provider tests must use saved synthetic or normalized fixtures and must not make network calls. Runtime dependencies remain empty unless a separately reviewed product requirement proves one is necessary.

## Review Standard

Maintainers review correctness, reproducibility, security boundaries, data rights, compatibility, and test quality. A green workflow is required but does not replace review. By contributing, you agree that your contribution is licensed under the MIT License and that you have the right to submit it.
