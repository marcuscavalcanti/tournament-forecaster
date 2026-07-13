# First-Class Multi-LLM Council Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the multi-LLM debriefing as a first-class optional capability with a configurable 55/45 engine-to-council blend.

**Architecture:** Keep the deterministic simulator unchanged and place the council behind a separate configuration and provider boundary. The CLI first produces a deterministic baseline, then optionally runs a two-pass structured debate, validates and aggregates opinions, blends valid consensus, and publishes one auditable forecast with safe fallback.

**Tech Stack:** Python 3.11 standard library, JSON Schema 2020-12, pytest, existing Forecast/report contracts.

## Global Constraints

- Offline quickstart and simulation without `--council-config` must remain byte-for-contract compatible.
- Enabled default weights are exactly 0.55 engine and 0.45 council.
- No provider secret or raw response body may enter committed files or forecast artifacts.
- Completed results, topology, legal opponents, and matchup probabilities remain engine-owned.
- Council failure degrades to the deterministic forecast; invalid configuration fails before network access.

---

### Task 1: Council configuration and schema

**Files:**
- Create: `src/tournament_forecaster/council/__init__.py`
- Create: `src/tournament_forecaster/council/config.py`
- Create: `src/tournament_forecaster/schemas/council.schema.json`
- Create: `tests/tournament_forecaster/test_council_config.py`

**Interfaces:**
- Produces: `CouncilConfig`, `CouncilAgentConfig`, and `load_council_config(path: Path) -> CouncilConfig`.

- [ ] Write failing tests for 55/45 defaults, provider-specific fields, effort controls, duplicate IDs, missing endpoints, unsafe key names, invalid weight sums, and non-finite values.
- [ ] Run `uv run --locked --extra dev pytest tests/tournament_forecaster/test_council_config.py -q` and confirm failure.
- [ ] Implement immutable validated configuration values and JSON loading.
- [ ] Add the packaged JSON Schema with the same closed-property contract.
- [ ] Run the focused tests and confirm they pass.
- [ ] Commit as `feat: add council configuration contract`.

### Task 2: Provider adapters and structured opinions

**Files:**
- Create: `src/tournament_forecaster/council/models.py`
- Create: `src/tournament_forecaster/council/providers.py`
- Create: `tests/tournament_forecaster/test_council_providers.py`

**Interfaces:**
- Consumes: `CouncilAgentConfig`.
- Produces: `CouncilOpinion`, `AgentCallResult`, `call_agent(...)`, and provider-specific JSON request/extraction helpers.

- [ ] Write failing tests for OpenAI, Anthropic, OpenAI-compatible, and Gemini request payloads and response extraction.
- [ ] Add tests proving environment-only secret lookup, sanitized HTTP errors, explicit 429/quota details, and Gemini prepayment-credit guidance.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement direct HTTPS adapters with injectable transport and bounded response details.
- [ ] Implement tolerant fenced-JSON extraction followed by strict opinion validation.
- [ ] Run focused tests and commit as `feat: add portable council provider adapters`.

### Task 3: Two-pass debate, quorum, consensus, and blending

**Files:**
- Create: `src/tournament_forecaster/council/prompts.py`
- Create: `src/tournament_forecaster/council/runner.py`
- Create: `src/tournament_forecaster/council/blend.py`
- Create: `tests/tournament_forecaster/test_council_runner.py`
- Create: `tests/tournament_forecaster/test_council_blend.py`

**Interfaces:**
- Consumes: `Forecast`, `Tournament`, `CouncilConfig`, and an injectable agent caller.
- Produces: `run_council(...) -> CouncilRun` and `apply_council(...) -> Forecast`.

- [ ] Write failing tests for independent first-round prompts, anonymized second-round positions, median consensus, minimum quorum, invalid funnels, locked 0/1 stages, provider failures, and fallback.
- [ ] Write failing tests for exact 55/45 blending, conditional intervals, engine-only matchups, changed run IDs, and provenance without secrets.
- [ ] Run the focused tests and confirm failure.
- [ ] Implement the two-pass runner with parallel per-round calls and deterministic ordering of stored results.
- [ ] Implement median aggregation and validated blending.
- [ ] Run focused tests and commit as `feat: run auditable multi-llm debriefings`.

### Task 4: CLI and report integration

**Files:**
- Modify: `src/tournament_forecaster/cli.py`
- Modify: `src/tournament_forecaster/reports/markdown_report.py`
- Modify: `src/tournament_forecaster/schemas/forecast.schema.json`
- Modify: `src/tournament_forecaster/reports/json_report.py`
- Modify: `tests/test_tournament_forecast_cli.py`
- Modify: `tests/tournament_forecaster/test_reports.py`

**Interfaces:**
- Consumes: council config, runner, and blend interfaces from Tasks 1-3.
- Produces: `simulate --council-config ... --council|--no-council` and `council validate --config ...`.

- [ ] Write failing CLI tests for offline default, enable, disable override, missing config, invalid config before network, applied status, and fallback status.
- [ ] Write failing report tests for the Council Debrief section and matchup/uncertainty labels.
- [ ] Run focused tests and confirm failure.
- [ ] Wire baseline simulation to optional council execution and print council status.
- [ ] Render structured council metadata safely in Markdown.
- [ ] Tighten the forecast schema's council object while preserving old `null` artifacts.
- [ ] Run focused tests and commit as `feat: expose council through generic cli`.

### Task 5: Public examples, README, security, and architecture

**Files:**
- Create: `examples/council.example.json`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/CONFIGURATION.md`
- Modify: `docs/PROVIDERS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/PRODUCT_FLOW.md`
- Modify: `SECURITY.md`
- Modify: `docs/assets/architecture/product-flow.svg`
- Modify: `docs/assets/architecture/technical-architecture.svg`
- Modify: `docs/assets/architecture/product-flow.png`
- Modify: `docs/assets/architecture/technical-architecture.png`
- Modify: `docs/assets/architecture/manifest.json`
- Modify: `pyproject.toml`
- Modify: `tests/test_public_repository_contract.py`
- Modify: `tests/test_announcement_diagrams.py`

**Interfaces:**
- Produces: copy-paste offline and council quickstarts, packaged council resources, and diagrams that show the council as a first-class optional lane.

- [ ] Write failing public-contract tests for README commands, 55/45 policy, model/effort fields, environment keys, and packaged example/schema files.
- [ ] Update the public documentation and example without embedding secret values.
- [ ] Update custom SVG sources, render matching PNGs with `sips`, and refresh manifest digests.
- [ ] Run public-contract and architecture tests.
- [ ] Commit as `docs: restore the llm council as a core capability`.

### Task 6: Adversarial verification and release

**Files:**
- Verify only, except for defects found by the checks.

**Interfaces:**
- Produces: a reviewed branch, merged `main`, and published GitHub state.

- [ ] Run all focused council tests.
- [ ] Run `make validate`, `make coverage`, clean-wheel tests, and `git diff --check`.
- [ ] Build sdist/wheel and run `twine check`.
- [ ] Review the diff for credentials, endpoint leakage, raw responses, and accidental legacy coupling.
- [ ] Push `codex/first-class-llm-council`, open a pull request, verify checks, and merge with head-SHA protection.
- [ ] Pull/verify `main`, confirm the GitHub default branch contains the council README and example, and report the deployed commit.
