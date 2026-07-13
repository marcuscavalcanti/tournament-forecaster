# Code Quality Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the highest-value security and maintainability risks without changing tournament probabilities or report contracts.

**Architecture:** Keep the deterministic public engine dependency-free at runtime. Harden the deprecated bridge boundary where secrets leave the process, measure public-core branch coverage in CI, and split tournament-wide semantic validation into explicit internal contexts while preserving `tournament_forecaster.domain.validate_tournament`.

**Tech Stack:** Python 3.11+, pytest, pytest-cov, Ruff, Mypy, uv.

## Global Constraints

- Preserve every existing public import and output schema.
- Use TDD for changed behavior and characterization tests for refactors.
- Do not change simulation formulas, ratings, seeds, or stage semantics.
- HTTP remains available only for loopback development endpoints; remote provider endpoints require HTTPS.
- Bridge subprocesses receive only operational variables and explicitly required provider credentials.

---

### Task 1: Harden legacy provider boundaries

**Files:**
- Modify: `worldcup_brazil/agents.py`
- Test: `tests/test_agents_fallbacks.py`

- [x] Add failing tests proving remote HTTP endpoints are rejected, loopback HTTP remains usable, and unrelated secrets do not reach bridge subprocesses.
- [x] Run the focused tests and confirm they fail for the missing policy.
- [x] Implement an HTTPS-or-loopback endpoint validator and a bridge environment allowlist.
- [x] Run the focused tests and the complete legacy agent test module.

### Task 2: Make public-core coverage measurable

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.github/workflows/ci.yml`
- Modify: `Makefile`
- Test: `tests/test_public_repository_contract.py`

- [x] Add a failing repository-contract test for a branch-coverage CI step.
- [x] Measure the current public-core branch coverage without choosing a threshold in advance.
- [x] Add `pytest-cov` and a conservative non-regressing threshold below the measured baseline.
- [x] Run the repository contract and coverage target.

### Task 3: Separate tournament-wide semantic validation

**Files:**
- Modify: `src/tournament_forecaster/domain.py`
- Test: `tests/tournament_forecaster/test_domain_and_config.py`

- [x] Record the domain/config test baseline.
- [x] Split tournament graph, completed-fact, and knockout consistency validation into focused private helpers.
- [x] Keep `domain.validate_tournament` as the stable public orchestration facade.
- [x] Split orchestration into structure, completed-facts, resolved-state, and locked-pair helpers.
- [x] Run focused tests, strict Mypy, Ruff, and the full validation target.

### Task 4: Final verification

- [x] Run `make validate`.
- [x] Run public-core strict Mypy and the new coverage target.
- [x] Review the final diff for simulation or report changes; reject any such drift.
