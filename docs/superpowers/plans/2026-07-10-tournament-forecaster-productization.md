# Tournament Forecaster Productization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the merged Brazil-specific repository into an installable, English-first, configuration-driven tournament forecaster whose offline quickstart generates valid JSON, Markdown, and SVG output from a clean wheel.

**Architecture:** Build a new `src/tournament_forecaster` package that does not import `worldcup_brazil`. The generic core owns schema validation, standings, qualification, typed entrant resolution, one- and two-leg knockout simulation, deterministic Monte Carlo, and reports. `worldcup_brazil` remains a deprecated compatibility integration for one release cycle; it is not the generic engine.

**Tech Stack:** Python 3.11-3.13, standard library runtime, Hatchling build backend, pytest, Ruff, mypy, JSON Schema documents, GitHub Actions.

## Global Constraints

- The four-line quickstart works from a wheel with no API keys, network, Make, uv, shell profile, browser bridge, or user input.
- Public package, CLI, default examples, reports, errors, docs, comments, and tests are English.
- Stable ASCII IDs own logic; localized names are display data only.
- Completed results are immutable and keyed by stable `match_id` plus `leg`.
- The deterministic engine simulates the complete tournament, not only the focus-team path.
- The same normalized input, focus team, seed, and iteration count produce identical probability fields.
- Odds and models may challenge bounded context but never rewrite locked results or tournament topology.
- No runtime dependency is required for the deterministic core.
- Canonical package resources live under `src/tournament_forecaster/data`; root `presets/` copies are parity-tested for repository users.
- Bundled competition contracts use synthetic teams and redistributable data. No FIFA, UEFA, CONMEBOL, bookmaker, or provider raw payload is committed.
- Existing `worldcup-brazil-report` behavior and the 534-test baseline remain green.

---

### Task 0: Baseline Repair and Isolation

**Files:**
- Modify: `tests/test_readme_diagrams.py`

**Interfaces:**
- Produces: a green baseline on branch `codex/productized-tournament-forecaster`

- [x] Update the stale README diagram assertions to check the committed SVG assets.
- [x] Verify the targeted test: `1 passed`.
- [x] Run `make validate`: `534 passed` plus compile and contract checks.
- [x] Commit as `340c325 test: align README diagram contract`.

### Task 1: Package Skeleton, Domain Model, and Configuration Contract

**Files:**
- Modify: `pyproject.toml`
- Create: `src/tournament_forecaster/__init__.py`
- Create: `src/tournament_forecaster/errors.py`
- Create: `src/tournament_forecaster/domain.py`
- Create: `src/tournament_forecaster/config.py`
- Create: `src/tournament_forecaster/resources.py`
- Create: `src/tournament_forecaster/atomic_io.py`
- Create: `src/tournament_forecaster/schemas/tournament.schema.json`
- Create: `src/tournament_forecaster/schemas/forecast.schema.json`
- Create: `tests/tournament_forecaster/test_domain_and_config.py`
- Create: `tests/tournament_forecaster/test_atomic_io.py`
- Create: `tests/tournament_forecaster/test_package_resources.py`

**Interfaces:**
- Produces:

```python
load_tournament(path: Path) -> Tournament
load_tournament_document(document: Mapping[str, object]) -> Tournament
validate_tournament(tournament: Tournament) -> None
atomic_write_text(path: Path, text: str) -> None
atomic_write_json(path: Path, value: Mapping[str, object]) -> None
resource_path(*parts: str) -> AbstractContextManager[Path]
```

- Domain types include `Team`, `Score`, `CompletedMatch`, `Tournament`, `SimulationOptions`, `MatchupProbability`, and `Forecast`.
- `Forecast.to_dict()` emits schema version 2, stable run ID, focus team, stage probabilities, matchup probabilities, championship probability, confidence intervals, provenance, warnings, and optional council metadata.

- [x] Write tests that fail because the package, typed config loader, stable-ID validation, duplicate-result rejection, and atomic writers do not exist.
- [x] Configure Hatchling to package both `src/tournament_forecaster` and the temporary `worldcup_brazil` compatibility package; retain both console script names.
- [x] Implement immutable domain types, semantic validation, schema resources, and standard-library atomic writes.
- [x] Verify Task 1 tests and the legacy baseline.
- [x] Commit `feat: add generic tournament domain and schema` plus the adversarial contract hardening commits.

### Task 2: Deterministic Stage Engine and Monte Carlo

**Files:**
- Create: `src/tournament_forecaster/probabilities.py`
- Create: `src/tournament_forecaster/standings.py`
- Create: `src/tournament_forecaster/qualification.py`
- Create: `src/tournament_forecaster/pairing.py`
- Create: `src/tournament_forecaster/simulation.py`
- Create: `src/tournament_forecaster/stages/__init__.py`
- Create: `src/tournament_forecaster/stages/group_stage.py`
- Create: `src/tournament_forecaster/stages/league_stage.py`
- Create: `src/tournament_forecaster/stages/knockout_stage.py`
- Create: `tests/tournament_forecaster/test_standings.py`
- Create: `tests/tournament_forecaster/test_pairing.py`
- Create: `tests/tournament_forecaster/test_group_stage.py`
- Create: `tests/tournament_forecaster/test_league_stage.py`
- Create: `tests/tournament_forecaster/test_knockout_stage.py`
- Create: `tests/tournament_forecaster/test_simulation.py`

**Interfaces:**

```python
simulate_tournament(
    tournament: Tournament,
    *,
    focus_team_id: str | None = None,
    options: SimulationOptions | None = None,
) -> Forecast
```

- Entrant sources are typed mappings: `group_rank`, `best_additional`, `league_rank`, and `match_winner`.
- Pairing modes are `fixed`, `seeded_draw`, and `open_draw`.
- Group and league standings support configurable points and ordered tiebreakers: points, goal difference, goals for, wins, rating, and stable team ID.
- One-leg ties resolve regulation draws with extra time and penalties. Two-leg ties aggregate both legs, apply configured tiebreaks, and support a one-leg final.
- Completed group, league, and knockout matches are locked by explicit match IDs. Partially completed two-leg ties simulate only missing legs.

- [x] Write failing tests for each stage type, completed-result locking, typed entrant resolution, pairing determinism, two-leg aggregate ties, focus-team stage reach, matchup distributions, monotonicity, and deterministic replay.
- [x] Implement rating-derived score simulation using one local `random.Random(seed)` and stable traversal order.
- [x] Implement complete-tournament iteration and Wilson confidence intervals without importing legacy modules.
- [x] Verify all generic engine tests and the legacy baseline.
- [x] Commit `feat: add generic tournament simulation engine` plus the adversarial fact, terminal, and locked-bracket fixes.

### Task 3: Packaged Examples and Format Contracts

**Files:**
- Create: `src/tournament_forecaster/data/presets/synthetic-cup/tournament.json`
- Create: `src/tournament_forecaster/data/presets/world-cup-style/tournament.json`
- Create: `src/tournament_forecaster/data/presets/champions-league-style/tournament.json`
- Create: `src/tournament_forecaster/data/presets/libertadores-style/tournament.json`
- Create: `src/tournament_forecaster/data/templates/group-knockout/tournament.json`
- Create: `src/tournament_forecaster/data/templates/group-knockout/README.md`
- Create: `src/tournament_forecaster/data/templates/league-knockout/tournament.json`
- Create: `src/tournament_forecaster/data/templates/league-knockout/README.md`
- Create: `src/tournament_forecaster/data/templates/group-two-leg-knockout/tournament.json`
- Create: `src/tournament_forecaster/data/templates/group-two-leg-knockout/README.md`
- Modify: `src/tournament_forecaster/resources.py`
- Create: `presets/synthetic-cup/tournament.json`
- Create: `presets/world-cup-style/tournament.json`
- Create: `presets/champions-league-style/tournament.json`
- Create: `presets/libertadores-style/tournament.json`
- Create: `presets/synthetic-cup/DATA_SOURCES.md`
- Create: `presets/world-cup-style/DATA_SOURCES.md`
- Create: `presets/champions-league-style/DATA_SOURCES.md`
- Create: `presets/libertadores-style/DATA_SOURCES.md`
- Create: `tests/presets/test_format_contracts.py`

**Interfaces:**
- `load_bundled_preset(name: str) -> Tournament`
- `copy_template(name: str, destination: Path) -> Path`

- [ ] Write failing parity and acceptance tests for four packaged presets and three templates.
- [ ] Add an eight-team synthetic quickstart cup, a groups-to-one-leg contract, a league-table-to-seeded-knockout contract, and a groups-to-two-leg-knockout contract with a one-leg final.
- [ ] Use only synthetic team names and document every file as redistributable project-authored test data.
- [ ] Verify every preset validates and produces coherent stage/title probabilities offline.
- [ ] Commit `feat: add packaged tournament format contracts`.

### Task 4: Reports, CLI, and Clone-to-First-Output

**Files:**
- Create: `src/tournament_forecaster/__main__.py`
- Create: `src/tournament_forecaster/cli.py`
- Create: `src/tournament_forecaster/reports/__init__.py`
- Create: `src/tournament_forecaster/reports/json_report.py`
- Create: `src/tournament_forecaster/reports/markdown_report.py`
- Create: `src/tournament_forecaster/reports/bracket_svg.py`
- Create: `tests/tournament_forecaster/test_reports.py`
- Create: `tests/test_tournament_forecast_cli.py`
- Create: `tests/test_clean_wheel.py`
- Modify: `Makefile`

**Interfaces:**

```text
tournament-forecast quickstart [--output-dir PATH] [--seed INT] [--iterations INT]
tournament-forecast init DIRECTORY --template NAME
tournament-forecast validate --config PATH
tournament-forecast simulate --config PATH [--focus-team ID] [--seed INT] [--iterations INT] [--output-dir PATH]
tournament-forecast report --forecast PATH [--output-dir PATH]
tournament-forecast doctor
tournament-forecast presets list
```

- `quickstart` atomically creates `outputs/synthetic-cup/north-city/forecast.json`, `report.md`, and `bracket.svg` and prints exact next commands.
- The SVG renderer uses only repository-independent XML and escapes all user display text.
- `init` refuses an existing destination and copies a complete config plus adjacent English README.
- CLI errors are concise English messages with exit code 2 and no traceback for user input errors.

- [ ] Write failing report, CLI, offline-network-block, and clean-wheel tests before implementation.
- [ ] Implement JSON, Markdown, and SVG rendering plus the six CLI surfaces.
- [ ] Build a wheel, install only the wheel into a clean venv, clear provider variables and `PYTHONPATH`, block network, and prove quickstart plus init/validate.
- [ ] Add `make quickstart` as a convenience without making it part of primary onboarding.
- [ ] Verify CLI tests, wheel test, generic tests, and legacy baseline.
- [ ] Commit `feat: add offline tournament forecast CLI`.

### Task 5: Provider Boundaries, Compatibility, and Safe Defaults

**Files:**
- Create: `src/tournament_forecaster/providers/__init__.py`
- Create: `src/tournament_forecaster/providers/results.py`
- Create: `src/tournament_forecaster/providers/odds.py`
- Create: `src/tournament_forecaster/compatibility/__init__.py`
- Create: `src/tournament_forecaster/compatibility/worldcup_brazil.py`
- Create: `src/tournament_forecaster/schemas/results.import.schema.json`
- Create: `src/tournament_forecaster/schemas/odds.import.schema.json`
- Create: `tests/tournament_forecaster/test_results_provider.py`
- Create: `tests/tournament_forecaster/test_odds_provider.py`
- Create: `tests/tournament_forecaster/test_legacy_compatibility.py`
- Modify: `src/tournament_forecaster/cli.py`
- Modify: `worldcup_brazil/agents.py`
- Modify: `worldcup_brazil/cli.py`
- Modify: `worldcup_brazil/sources.py`
- Modify: `scripts/run_agent_source_harness.py`
- Modify relevant legacy resilience tests.

**Interfaces:**

```python
preview_results(config: Path, source: Path, *, format: str) -> ImportPreview
apply_results(config: Path, preview: ImportPreview) -> None
preview_odds(source: Path) -> OddsPreview
redact_url(url: str) -> str
```

- JSON/CSV result imports are preview-first, idempotent, alias-aware, conflict-rejecting, and atomic on apply.
- Odds imports preserve provenance and never modify deterministic probabilities.
- FIFA remains an opt-in, undocumented adapter exercised only through synthetic offline fixtures; UEFA and CONMEBOL make no live-provider claim.
- Legacy local executable bridges default off, `--no-bridges` is absolute, and no path reads `~/.zshrc` implicitly.

- [ ] Write failing tests for import preview/apply/conflict, odds provenance, URL redaction, bridge opt-in, and no-shell-profile behavior.
- [ ] Implement provider protocols and compatibility translation without importing legacy code into the generic core.
- [ ] Verify focused security/provider tests and the full baseline.
- [ ] Commit `feat: add safe provider and legacy boundaries`.

### Task 6: English Public Surface, Governance, and CI

**Files:**
- Rewrite: `README.md`
- Create: `LICENSE`
- Create: `NOTICE.md`
- Create: `SECURITY.md`
- Create: `CONTRIBUTING.md`
- Create: `CODE_OF_CONDUCT.md`
- Create: `.env.example`
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/gitleaks.yml`
- Create: `.github/workflows/release.yml`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/pull_request_template.md`
- Create: `.github/dependabot.yml`
- Create: `docs/CONFIGURATION.md`
- Create: `docs/PROVIDERS.md`
- Create: `docs/ADDING_A_COMPETITION.md`
- Create: `docs/ADDING_A_PROVIDER.md`
- Create: `docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md`
- Create: `docs/DATA_POLICY.md`
- Create: `scripts/check_english_surface.py`
- Create: `tests/test_public_repository_contract.py`
- Modify: `.gitignore`
- Modify: `pyproject.toml`

- [ ] Write a failing public-repository contract test for required files, English default surfaces, four-line quickstart placement, no tracked runtime artifacts, and no personal filesystem paths.
- [ ] Rewrite README to lead with the working wheel quickstart, real outputs, supported format contracts, current limitations, architecture diagrams, and legacy migration link.
- [ ] Add MIT governance, security/trust boundaries, provider acquisition guidance, data policy, issue templates, and CI matrices.
- [ ] Scope the English checker to public package, public docs, governance, presets, and generic tests; explicitly exempt the deprecated legacy package and named migration fixtures.
- [ ] Verify governance contracts, workflow syntax, public scan, package metadata, and full tests.
- [ ] Commit `docs: publish the generic tournament forecaster surface`.

### Task 7: Final Integration and Adversarial Release Gate

**Files:**
- Modify only files required by review findings.

- [ ] Run `make validate` and all generic preset contracts.
- [ ] Run Ruff, mypy, package build, schema validation, and public English scan.
- [ ] Install the wheel in a new temporary venv and run quickstart with network denied and credentials cleared.
- [ ] Verify the three generated outputs are non-empty, coherent, and free of local paths or secrets.
- [ ] Run `gitleaks git --redact --log-opts="--all"` when the binary is available; otherwise record it as a visibility blocker rather than claiming release-ready.
- [ ] Dispatch a broad adversarial code review over the complete branch diff and fix every Critical or Important finding.
- [ ] Confirm the legacy World Cup seeded goldens and 534-test baseline still pass.
- [ ] Commit fixes as `fix: close productization release findings` when needed.
- [ ] Push `codex/productized-tournament-forecaster` and open a new implementation PR against `main`.
