# Tournament Forecaster Open-Source Design

**Status:** Approved direction, pending written-spec review  
**Date:** 2026-07-10  
**Working name:** Tournament Forecaster  
**Python package:** `tournament_forecaster`  
**Command-line interface:** `tournament-forecast`  
**License:** MIT for source code

## 1. Product Goal

Tournament Forecaster is a reusable, configuration-driven engine that estimates how likely a selected team is to reach every stage of a tournament and win the title.

The public product must support three representative competition structures before the repository is advertised as globally reusable:

1. FIFA World Cup style: round-robin groups followed by a fixed single-match knockout bracket.
2. UEFA Champions League style: a single league table, qualification bands, seeded or constrained pairings, and knockout rounds.
3. CONMEBOL Libertadores style: round-robin groups followed by two-leg knockout ties and a single-match final.

The existing Brazil World Cup 2026 workflow remains operational throughout the migration. It becomes an English preset and reference integration, not the core domain model.

The repository must not become public until the public-release gates in section 15 pass.

## 2. Design Decisions

### 2.1 Generic core, optional intelligence, optional publishing

The system is divided into three product layers:

- **Deterministic tournament engine:** configuration validation, standings, qualification, bracket progression, probability modeling, Monte Carlo simulation, completed-result locking, and focus-team forecasts.
- **Optional intelligence layer:** multi-agent source research, debate, contextual rating adjustments, market challenges, audit trails, and model-provider adapters.
- **Optional publishing layer:** generic Markdown and JSON reports plus competition-specific or user-specific templates such as the current LinkedIn series.

The deterministic engine must run offline without API keys, browser bridges, shell configuration, or model-provider packages.

### 2.2 Stable identifiers, localizable display names

Every competition, team, stage, match, and rule uses a stable ASCII identifier. Human-facing names are data:

```json
{
  "id": "brazil",
  "display_name": "Brazil",
  "aliases": ["Brasil", "BRA"]
}
```

Simulation logic never branches on localized display text such as `Brasil`, `Oitavas`, or `Quartas`.

### 2.3 Focus team is a first-class domain concept

The generic schema uses `focus_team_id`, `focus_team_probability`, and `focus_team_confidence_interval`. The current `brazil_*` fields remain readable and writable only through a versioned compatibility adapter.

One accessor resolves the focus team. No literal team name is allowed in generic engine logic.

### 2.4 Accuracy means explicit, testable contracts

Accuracy preservation has two different meanings:

- The seeded deterministic World Cup simulation must remain bit-identical during extraction from `worldcup_brazil` into the generic engine, given the same normalized input and random seed.
- English LLM prompts are not expected to produce byte-identical output to Portuguese prompts. Their acceptance contract is invariant-based: valid bracket references, source coverage, bounded numeric shifts, stable Monte Carlo ownership of published probabilities, and no regression in resolved-prediction calibration.

The current Portuguese path remains available only as a temporary migration oracle. The public package, documentation, prompts, messages, comments, examples, and default artifacts are English.

## 3. Architecture

The target source layout is:

```text
src/tournament_forecaster/
  __init__.py
  cli.py
  config.py
  domain.py
  errors.py
  standings.py
  qualification.py
  pairing.py
  simulation.py
  probabilities.py
  results.py
  schemas/
    tournament.schema.json
    forecast.schema.json
  stages/
    base.py
    group_stage.py
    league_stage.py
    knockout_stage.py
  council/
    agents.py
    meeting.py
    consensus.py
    prompts.py
    sources.py
  reports/
    json_report.py
    markdown_report.py
    audit_report.py
  providers/
    results.py
    odds.py
  compatibility/
    worldcup_brazil_config.py
    worldcup_brazil_artifacts.py

worldcup_brazil/
  __init__.py
  cli.py

presets/
  world-cup-2026/
  champions-league/
  libertadores/

examples/
  synthetic-cup/
```

The `worldcup_brazil` package becomes a deprecated shim. It delegates to the generic package and contains no independent simulation logic.

The existing 9,613-line `pipeline.py` is not split as a preliminary cleanup. Behavior is extracted behind tested interfaces in small increments. Unrelated refactoring is excluded from the migration.

Two canonical diagrams accompany this design:

- [`docs/PRODUCT_FLOW.md`](../../PRODUCT_FLOW.md) explains the user journey from zero-key quickstart through recurring forecast updates.
- [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) explains technical components, trust boundaries, ownership, failure behavior, and one complete run.

The README includes condensed versions and links to these detailed diagrams. The diagrams describe the target architecture until migration is complete; they must not be presented as current implementation status before the corresponding release gates pass.

## 4. Tournament Schema

Every tournament document declares `schema_version`, metadata, teams, stages, transitions, ratings, completed results, and the focus team.

```json
{
  "schema_version": 2,
  "tournament": {
    "id": "synthetic-cup",
    "display_name": "Synthetic Cup",
    "season": "2026"
  },
  "focus_team_id": "north-city",
  "teams": [],
  "stages": [],
  "ratings": {},
  "completed_matches": []
}
```

### 4.1 Round-robin group stage

```json
{
  "id": "group-stage",
  "type": "round_robin_groups",
  "groups": {"A": ["north-city", "south-city", "east-city", "west-city"]},
  "rounds_per_pair": 1,
  "points": {"win": 3, "draw": 1, "loss": 0},
  "tiebreakers": ["points", "goal_difference", "goals_for", "wins", "rating"],
  "qualification": {"direct_per_group": 2, "best_additional": 0}
}
```

Group sizes, number of groups, repeated fixtures, points, tiebreakers, and best-ranked additional qualifiers are configurable. Tiebreakers are ordered data, not hardcoded labels.

### 4.2 League phase

```json
{
  "id": "league-phase",
  "type": "league_table",
  "fixtures": [],
  "points": {"win": 3, "draw": 1, "loss": 0},
  "tiebreakers": ["points", "goal_difference", "goals_for", "wins"],
  "qualification_bands": [
    {"ranks": [1, 8], "destination": "round-of-16"},
    {"ranks": [9, 24], "destination": "knockout-playoff"},
    {"ranks": [25, 36], "destination": "eliminated"}
  ]
}
```

League-stage pairings consume rank ranges and explicit seeding rules. They do not reuse the World Cup slot parser.

### 4.3 Knockout stage

```json
{
  "id": "quarter-finals",
  "type": "knockout",
  "pairing": {"mode": "fixed", "ties": []},
  "legs": 2,
  "home_away_order": "seeded_team_second_leg_home",
  "aggregate_tiebreak": "extra_time_then_penalties",
  "away_goals_rule": false
}
```

Supported pairing modes are `fixed`, `seeded_draw`, and `open_draw`. A knockout stage declares one or two legs. The final may use one leg even when preceding rounds use two. Aggregate scoring, extra time, penalties, home-away ordering, and away-goals behavior are explicit rules.

### 4.4 Stage transitions

Qualification produces typed entrants rather than string parsing. Entrants may be a group rank, league rank, best-ranked additional qualifier, match winner, match loser, or draw seed.

Stage transitions are validated as a directed acyclic graph. Every entrant source must resolve, every team slot must be reachable, and every terminal path must end in elimination or championship.

## 5. Simulation Semantics

1. Completed results are immutable facts and are never resimulated.
2. Partially completed stages use real standings plus simulations of remaining fixtures.
3. Match probabilities are derived from ratings and configured context adjustments using the existing deterministic formula unless a later calibrated model replaces it.
4. A one-leg knockout draw is resolved by configured extra-time and penalty probabilities.
5. A two-leg tie simulates both legs, aggregates scores, and applies the declared aggregate tiebreak.
6. The same seeded random stream produces reproducible results for the same normalized configuration.
7. The engine simulates the entire competition, not only the focus-team path, so opponent probabilities remain conditional on all other results.
8. Stage reach is defined as the probability of being an entrant in that stage. For a locked, already-reached stage it is exactly 100 percent.
9. The probability of reaching the next stage equals the probability of winning the currently locked tie when no other path exists.

## 6. Output Contract

The new JSON artifact is versioned and competition-neutral:

```json
{
  "schema_version": 2,
  "run_id": "...",
  "generated_at": "...",
  "tournament_id": "synthetic-cup",
  "focus_team_id": "north-city",
  "stage_probabilities": {},
  "matchup_probabilities": [],
  "championship_probability": 0.0,
  "confidence_intervals": {},
  "input_provenance": [],
  "warnings": [],
  "council": null
}
```

New artifacts use:

```text
forecast_<tournament-id>_<focus-team-id>_<date>.json
forecast_<tournament-id>_<focus-team-id>_<date>.md
audit_<tournament-id>_<focus-team-id>_<date>.md
```

The compatibility reader accepts current `linkedin_brazil_*.json` artifacts. During the deprecation window, the legacy CLI may emit the old field names and filenames from the generic result. The generic core never imports the legacy package.

## 7. Multi-Agent Council

The council is optional. It may research injuries, availability, odds, ratings, performance, scheduling, travel, and tactical context, but it cannot freely replace deterministic tournament structure or invent the published title probability.

The English prompt pack uses competition-neutral vocabulary and injects:

- tournament name and format;
- focus team;
- completed matches and current standings;
- legal future opponents from the stage engine;
- source policy;
- numeric baseline and allowed adjustment bounds.

Provider adapters remain configurable. Core simulations work with `council.enabled=false` and no environment variables.

The current Portuguese council is exercised in shadow comparisons until the English contracts pass. It is removed from the public surface before release; only legacy artifact fixtures may contain Portuguese text.

## 8. Security and Trust Model

Public defaults are safe and non-executing:

- `~/.zshrc` is not read automatically.
- CLI and browser bridges are disabled unless explicitly enabled.
- `--no-bridges` is available as a hard-off switch.
- Configuration files that declare commands are documented as trusted code.
- Commands are represented as argument arrays and never passed through a shell.
- Query-string secrets are redacted before logging or artifact persistence.
- `.env`, local configs, runtime state, transcripts, outputs, and credentials remain ignored.
- `.env.example` contains names only, never values.
- A full-history Gitleaks scan runs before visibility changes and in CI for future commits.
- `SECURITY.md` documents supported versions, private reporting, the config trust boundary, and provider credential handling.

The basic history scan found no known secret formats or tracked runtime artifacts. This does not replace the dedicated entropy and history scan required by the release gate.

### 8.1 Provider onboarding and credential acquisition

The repository includes `docs/PROVIDERS.md` and `.env.example`. Provider documentation distinguishes three different concepts that must not be conflated:

1. **Authentication keys:** secrets used to authorize paid or quota-controlled APIs.
2. **Competition identifiers:** non-secret IDs such as a competition, season, or stage identifier used to select the correct feed.
3. **Local bridges:** explicitly enabled CLI programs that may use their own login instead of an API key.

The quick-start simulation requires none of them. Missing optional credentials disable only the corresponding provider or council slot.

The provider guide contains a table with the environment variable, official account page, billing requirement, validation command, and failure behavior for every bundled integration:

| Provider | Purpose | Credential source | Environment variable |
| --- | --- | --- | --- |
| FIFA calendar feed | Completed group and knockout results | No authentication key in the currently used public endpoint | None |
| The Odds API | Structured outright and match odds | [The Odds API access and documentation](https://the-odds-api.com/liveapi/guides/v4/) | `THE_ODDS_API_KEY` |
| OpenAI | Optional council model | [OpenAI API keys](https://platform.openai.com/api-keys) | `OPENAI_API_KEY` |
| Anthropic | Optional council model | [Anthropic Console API keys](https://console.anthropic.com/settings/keys) | `ANTHROPIC_API_KEY` |
| Perplexity | Optional web-grounded council model | [Perplexity API Portal](https://console.perplexity.ai/) | `PERPLEXITY_API_KEY` |
| DeepSeek | Optional council model | [DeepSeek API keys](https://platform.deepseek.com/api_keys) | `DEEPSEEK_API_KEY` |
| Google Gemini | Optional council model | [Google AI Studio API keys](https://aistudio.google.com/apikey) | `GEMINI_API_KEY` |

Each entry explains how to create the account or project, enable billing when required, create the least-privileged key available, store it in `.env`, verify it with `tournament-forecast doctor`, rotate it, and revoke it. The guide links to current official documentation instead of freezing provider-specific quotas or pricing in the repository.

`.env.example` contains only placeholders:

```dotenv
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
PERPLEXITY_API_KEY=
DEEPSEEK_API_KEY=
GEMINI_API_KEY=
THE_ODDS_API_KEY=
```

The documentation explicitly prohibits placing credentials in tournament JSON, command-line arguments, committed files, source URLs, screenshots, or issue reports.

### 8.2 FIFA results discovery and synchronization

The bundled FIFA adapter uses the public calendar endpoint currently exercised by the Brazil workflow:

```text
https://api.fifa.com/api/v3/calendar/matches
```

The endpoint was verified on 2026-07-10 to respond without an authentication key. It is treated as an optional, undocumented external feed whose shape or availability may change; it is not presented as a guaranteed public API.

The provider guide explains how to discover the non-secret selectors for another FIFA competition:

1. Open the competition in the FIFA Data Centre.
2. In browser developer tools, filter Network requests for `api/v3/calendar/matches`.
3. Record the `idCompetition` and `idSeason` query parameters plus the returned `StageName`, team codes, and match IDs.
4. Save one response as a local fixture and run the provider in dry-run mode.
5. Confirm that every external team code maps to exactly one configured team and every stage maps to exactly one tournament stage.
6. Apply only final results after reviewing additions, replacements, unmatched fixtures, and conflicts.

The generic provider configuration is explicit:

```json
{
  "results_provider": {
    "type": "fifa_calendar",
    "base_url": "https://api.fifa.com/api/v3/calendar/matches",
    "competition_id": "17",
    "season_id": "285023",
    "language": "en",
    "stage_map": {
      "First Stage": "group-stage",
      "Round of 16": "round-of-16",
      "Quarter-finals": "quarter-finals",
      "Semi-finals": "semi-finals",
      "Final": "final"
    }
  }
}
```

Competition and season IDs are preset data, never hardcoded in the generic provider. The adapter ingests both group and knockout results. A normalized completed match records provider, external match ID, stage ID, teams, regular score, penalty score when present, winner, status, match date, source URL, and retrieval timestamp.

The synchronization command is preview-first:

```bash
tournament-forecast update-results \
  --config presets/world-cup-2026/tournament.json \
  --provider fifa-calendar \
  --dry-run

tournament-forecast update-results \
  --config presets/world-cup-2026/tournament.json \
  --provider fifa-calendar \
  --apply
```

An external result is accepted only when it matches a configured fixture or a uniquely resolvable knockout tie. Unmatched teams, ambiguous aliases, non-final matches, impossible stages, and conflicting scores fail before the ledger is written. Writes are atomic. Group results update standings; knockout results lock winners, penalty outcomes, and future bracket slots.

JSON and CSV import remain first-class fallbacks for competitions without a supported live provider or when the FIFA endpoint is unavailable. Provider failure never fabricates a result. A tournament chooses between `required`, `cached_with_ttl`, and `best_effort` freshness policies.

The public CLI also exposes:

```text
tournament-forecast providers list
tournament-forecast providers doctor
tournament-forecast providers inspect --provider fifa-calendar
```

`providers inspect` prints resolved non-secret settings, discovered competition and stage metadata, last successful fetch, cache age, and redacted request URLs. It never prints credential values.

## 9. Licensing, Data, and Trademarks

Source code is MIT licensed. Example data is governed separately:

- The default quick-start example is synthetic and fully redistributable.
- Competition presets include `DATA_SOURCES.md` with source, retrieval date, terms, and redistribution status.
- Data that cannot be redistributed is fetched by the user or represented by a documented schema without bundled values.
- `NOTICE.md` states that the project is unofficial and not affiliated with FIFA, UEFA, CONMEBOL, Opta, model providers, bookmakers, or data vendors.
- Logos and protected competition artwork are not distributed without explicit compatible licenses.

## 10. Packaging and Installation

The project uses the `src/` layout and a standards-compliant build backend. Core dependencies remain empty because the deterministic engine uses the Python standard library. Optional extras are explicit:

- `visual`: Pillow and rendering helpers.
- `agents`: provider SDKs only when a provider cannot use the standard HTTP adapter.
- `dev`: pytest, coverage, Ruff, mypy, build, and Gitleaks integration helpers.

Supported Python versions are 3.11 through 3.13.

Primary onboarding:

```bash
git clone https://github.com/marcuscavalcanti/tournament-forecaster.git
cd tournament-forecaster
python -m pip install -e .
tournament-forecast quickstart
```

The existing `worldcup-brazil-report` console command remains as a deprecated alias for one release cycle.

### 10.1 Clone-to-first-output contract

`tournament-forecast quickstart` is the mandatory first-run path. It requires only Python 3.11 or newer and the cloned repository. It does not require API keys, network access, a copied configuration file, `make`, `uv`, browser automation, provider CLIs, or user input.

The command performs this complete flow:

1. Load the bundled synthetic tournament.
2. Validate its teams, fixtures, stage graph, ratings, and focus team.
3. Run a deterministic simulation with a documented seed and 10,000 iterations.
4. Print a compact stage-probability summary in the terminal.
5. Atomically create:

```text
outputs/synthetic-cup/north-city/forecast.json
outputs/synthetic-cup/north-city/report.md
outputs/synthetic-cup/north-city/bracket.svg
```

6. Print the exact next commands for changing the focus team, copying a competition template, enabling live results, and enabling the optional council.

Repeated runs with the same seed produce byte-identical JSON probability fields. Existing unrelated files are never deleted. The command accepts `--output-dir`, `--seed`, and `--iterations` for explicit overrides.

For contributors, `make quickstart` delegates to the same CLI behavior. The README does not require Make because it must work on Windows as well as macOS and Linux.

After the first output, a user can scaffold a competition without writing Python:

```bash
tournament-forecast init my-tournament --template group-knockout
tournament-forecast validate --config my-tournament/tournament.json
tournament-forecast simulate --config my-tournament/tournament.json --focus-team my-team
```

`init` supports `group-knockout`, `league-knockout`, and `group-two-leg-knockout` templates. Generated files contain explanatory English comments in an adjacent README because strict JSON itself cannot contain comments.

## 11. Command-Line Interface

The generic CLI exposes:

```text
tournament-forecast quickstart
tournament-forecast init
tournament-forecast validate
tournament-forecast simulate
tournament-forecast report
tournament-forecast doctor
tournament-forecast update-results
tournament-forecast update-odds
tournament-forecast providers list
tournament-forecast providers doctor
tournament-forecast providers inspect
```

`validate` is offline and checks schema, stage graph, entrants, team references, result consistency, and deterministic prerequisites before any external call.

`simulate` is offline by default. Provider updates and the council require explicit flags or preset settings.

`quickstart` and `init` are covered by wheel-install tests, not only editable-checkout tests, so packaging errors cannot leave the documented onboarding falsely green.

## 12. Error Handling and Resilience

- Invalid tournament structure fails before model preflight or paid API calls.
- Missing past results fail loudly when freshness is required.
- External data sources distinguish expected unavailability from internal programming errors.
- Quota and billing failures are surfaced with provider-specific actions.
- Impossible bracket references remain terminal structural errors.
- Council degradation never changes locked results or tournament topology.
- Every run has a stable `run_id`, atomic output writes, and an observable watchdog.
- Compatibility conversion reports every translated or dropped legacy field.

## 13. Testing Strategy

### 13.1 Baseline before extraction

Before renaming fields or packages, commit deterministic goldens for:

- the seeded World Cup stage funnel;
- focus-team matchup probabilities;
- completed-result locking;
- bracket opponent distributions;
- current JSON artifact serialization;
- current Markdown and audit rendering;
- a stored, offline debate artifact.

### 13.2 Generic engine tests

The suite covers:

- standings and every configured tiebreaker;
- best-ranked additional qualifiers;
- league qualification bands;
- fixed, seeded, and open draws;
- one-leg and two-leg ties;
- aggregate ties, extra time, and penalties;
- stage-graph validation;
- partially completed competitions;
- focus-team invariance under renaming and localization;
- deterministic replay under the same seed;
- probability coherence and monotonic stage reach;
- generic-to-legacy and legacy-to-generic schema conversion.

### 13.3 Preset contracts

Three offline acceptance presets prove product scope:

- World Cup: groups, additional qualifiers where configured, fixed one-leg bracket.
- Champions League: league table, rank bands, playoff, seeded pairing, two-leg knockout where configured.
- Libertadores: groups, two-leg ties, single-match final.

### 13.4 CI

GitHub Actions runs on Python 3.11, 3.12, and 3.13 without API keys or network-dependent tests. Required checks include tests, compile, lint, type checking, schema validation, package build, English-surface scan, secret scan, and preset contracts.

A dedicated onboarding job builds a wheel, creates a clean virtual environment, installs only that wheel, clears all supported credential variables, blocks network access, runs `tournament-forecast quickstart`, and validates the three generated artifacts. Linux runs on every pull request; macOS and Windows run before release.

Network integration tests are opt-in and never required for pull requests from forks.

## 14. Documentation and Governance

The public repository includes:

- `README.md`: value proposition, clone-to-first-output commands at the top, architecture summary, three competition examples, output example, limitations, and links.
- `docs/PRODUCT_FLOW.md`: product journey, simple and advanced paths, outputs, and the result-to-rerun feedback loop.
- `docs/CONFIGURATION.md`: complete schema reference.
- `docs/ARCHITECTURE.md`: component diagram, execution sequence, stage engine, trust boundaries, council ownership limits, and compatibility model.
- `docs/ADDING_A_COMPETITION.md`: authoring and validating a preset.
- `docs/PROVIDERS.md`: obtaining credentials, discovering competition identifiers, configuring result and odds providers, and running safe dry-runs.
- `docs/ADDING_A_PROVIDER.md`: provider protocol, normalization, redaction, fixtures, and contract tests.
- `docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md`: aliases and deprecation dates.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `NOTICE.md`, issue templates, and pull-request template.
- Repository topics and description aligned with tournament simulation, Monte Carlo, football, bracket forecasting, and multi-agent analysis.

All public documentation, identifiers, comments, error messages, prompts, tests, and default examples are English. Localized team or competition display names may appear only as explicit data fields such as `display_name` and `aliases`. Free-form Portuguese text is otherwise allowed only inside explicitly named legacy artifact fixtures used to test migration.

## 15. Public-Release Gates

The repository may become public only when all of these conditions hold:

1. `main` contains the complete intended history and is the default branch; the stale `master` divergence is resolved.
2. MIT `LICENSE`, `NOTICE.md`, `SECURITY.md`, `CONTRIBUTING.md`, and `CODE_OF_CONDUCT.md` are present.
3. Gitleaks scans the entire reachable history with zero unresolved findings.
4. No runtime outputs, private transcripts, credentials, local configuration, or personal filesystem paths are tracked.
5. Personal LinkedIn content and hashtags live only in an optional example or user config.
6. A clean clone reaches valid JSON, Markdown, and SVG outputs through the documented four-line flow in less than five minutes without keys, network, configuration edits, Make, or uv.
7. CI passes across Python 3.11 through 3.13.
8. World Cup, Champions League, and Libertadores preset contracts pass offline.
9. Seeded World Cup Monte Carlo goldens remain bit-identical through the generic extraction.
10. English council shadow tests satisfy structural and numeric invariants before replacing the Portuguese migration oracle.
11. Every bundled dataset has explicit provenance and redistribution status.
12. Provider documentation covers credential acquisition, secure storage, dry-run validation, rotation, and revocation.
13. FIFA result synchronization passes offline contract fixtures for both group and knockout matches, including penalties and conflict rejection.
14. README claims match implemented formats; no roadmap feature is presented as available.
15. Product and technical Mermaid diagrams render on GitHub, match the implemented CLI and schemas, and clearly label any remaining target-state behavior.

## 16. Migration Sequence

### Milestone 0: Safety baseline

Add CI, MIT and governance documents, full-history scanning, deterministic goldens, safe shell and bridge defaults, provider onboarding documentation, and branch normalization. Keep repository private.

### Milestone 1: Focus-team extraction

Add the focus-team accessor, remove literal `Brasil` behavior, introduce schema aliases, and prove bit-identical World Cup Monte Carlo behavior. Keep current package and CLI operational.

### Milestone 2: Generic package and fixed bracket

Create `tournament_forecaster`, move group standings and fixed one-leg knockout simulation behind generic interfaces, extract the FIFA calendar adapter behind the generic results-provider protocol, add `quickstart`, `init`, and the synthetic example, and retain `worldcup_brazil` as a shim.

### Milestone 3: Stage engine completion

Add league tables, qualification bands, seeded or open draws, two-leg ties, aggregate rules, and competition-specific tiebreakers. Add Champions League and Libertadores acceptance presets.

### Milestone 4: English intelligence and reports

Move provider adapters, council contracts, reports, and publishing templates behind optional generic interfaces. Shadow-test the English council and move the current LinkedIn series into an example preset.

### Milestone 5: Public release

Build and install the package in a clean environment, run all release gates, reconcile `main`, set GitHub metadata, publish the repository, and create the first tagged release. PyPI publication is a separate explicit action after the GitHub release is validated.

## 17. Explicit Non-Goals

- A web application is not required for the first public release.
- The engine does not promise exact score prediction.
- The engine does not automatically scrape every competition.
- Market odds challenge the model but do not silently force calibration.
- LLM consensus never replaces structural tournament rules.
- The migration does not begin by splitting `pipeline.py` for aesthetic reasons.
- PyPI publication and GitHub visibility changes are not performed without an explicit final release command.

## 18. Success Criteria

A new user can clone the repository and generate valid JSON, Markdown, and SVG forecasts through the documented four-line quick start without credentials, network access, configuration edits, or prior knowledge of the project. The same user can then select a preset or scaffold a schema-valid tournament, choose any focus team, simulate the complete competition, and receive coherent stage and championship probabilities without editing Python code.

The existing Brazil workflow continues producing valid forecasts during migration. At public release, the repository contains no Brazil-specific behavior in the generic core, no Portuguese public surface, and no claim of competition support that lacks a passing offline contract.
