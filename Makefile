SHELL := /bin/sh

PYTHON ?= uv run --locked python
PYTEST ?= uv run --locked --extra dev python -m pytest
RUFF ?= uv run --locked --extra dev ruff
COVERAGE_MIN ?= 83

.PHONY: help quickstart validate coverage complexity diagrams

help:
	@printf "Main commands:\n"
	@printf "  make quickstart  generates a complete synthetic offline forecast\n"
	@printf "  make validate    runs the generic test, quality, and documentation checks\n"
	@printf "  make coverage    measures public-core branch coverage (minimum: $(COVERAGE_MIN)%%)\n"
	@printf "  make complexity  enforces the public-core complexity ceiling\n"
	@printf "  make diagrams    verifies the committed architecture assets\n"

quickstart:
	$(PYTHON) -m tournament_forecaster quickstart

coverage:
	TOURNAMENT_FORECASTER_INNER_MAKE_VALIDATE=1 $(PYTEST) -q \
		--ignore=tests/test_clean_wheel.py \
		--cov=tournament_forecaster --cov-branch --cov-report=term-missing \
		--cov-fail-under=$(COVERAGE_MIN)

complexity:
	$(RUFF) check src/tournament_forecaster --select C901

diagrams:
	$(PYTHON) docs/assets/architecture/generate.py --check

validate:
	$(PYTEST) -q
	$(MAKE) complexity
	$(PYTHON) -m compileall -q src/tournament_forecaster scripts
	$(MAKE) diagrams
