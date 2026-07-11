# Task 5A Implementation and Test Report

## Scope

Implemented Task 5A in the productization worktree without modifying
`src/tournament_forecaster/providers/results.py` or
`src/tournament_forecaster/providers/security.py`. Concurrent productization,
provider, documentation, packaging, and architecture-asset changes were left intact
and excluded from the Task 5A commit.

## Implementation

### Exact 1X2 scoring and explicit home advantage

- Added `OutcomeProbabilities` and `predict_match_outcomes(...)` using the same
  Poisson/Elo goal-rate model as score simulation, with deterministic summation and
  final normalization.
- Removed the hidden `+65` rating-point home boost from generic score simulation.
- Generic group, league, one-leg knockout, and two-leg knockout stages now default to
  zero home advantage.
- A finite numeric `metadata.home_advantage_rating_points` explicitly boosts the
  actual home side. Two-leg ties apply it independently to each leg's home side.
- Added semantic validation for non-numeric, boolean, and non-finite stage values.

### Locked mid-tournament entrants

- Added typed entrant `{"type":"team","team_id":"..."}` to the domain and JSON
  schema.
- Added direct runtime resolution and configured-team reference validation.
- Team entrants create no artificial stage dependency, enabling a tournament to begin
  directly from a locked bracket.

### Honest backtest

- Added versioned `backtest.schema.json`, `BacktestReport`, canonical ratings SHA-256,
  strict document loading, evaluation, and CLI support:
  `tournament-forecast backtest --input PATH [--output PATH] [--min-resolved INT]`.
- Cases enforce unique immutable source IDs, finite frozen ratings, matching ratings
  hash, timezone-aware timestamps, and `captured_at < kickoff_at`.
- Metrics are deterministic with no random sampling:
  - RPS: ordered home/draw/away cumulative squared error divided by `K-1 = 2`.
  - Brier: unscaled sum of the three squared class errors.
  - Log loss: natural logarithm.
  - Top-pick accuracy: observed class must be the unique maximum; probabilities within
    `1e-15` are treated as tied.
  - Uniform baseline: `1/3` per class and expected top-pick accuracy `1/3`.
- Empty samples return `no_resolved`, `ok=false`, and null metrics. Samples below
  `min_resolved` return `insufficient`, `ok=false`, while retaining mathematically
  computable sample metrics.

### FIFA builder and live example

- Added a standard-library-only builder with mutually exclusive saved-fixture and
  explicit network-fetch modes.
- Both modes use one deterministic normalization gate. It rejects unknown teams,
  unsupported stages/result types, malformed scores, invalid winners, and conflicting
  duplicate FIFA IDs. Non-final rows remain pending bracket fixtures and are never
  promoted to completed facts.
- Accepted FIFA final result types are `{1, 2, 3}`. Type `3` covers completed extra
  time. Added the same root-cause fix to the legacy updater.
- Added singular `Quarter-final` mapping in both the builder and legacy updater.
- Group facts use generated generic fixture IDs and retain official FIFA source IDs,
  official home/away IDs, kickoff, URL, and result type in metadata.
- Knockout ties use official FIFA match IDs through the final. The third-place match is
  omitted because the generic contract has no loser entrant.
- Generated snapshot retrieval boundary: `2026-07-11T19:54:25Z`, competition `17`,
  season `285023`.
- Checked-in snapshot contains 48 teams and 98 completed facts:
  72 group, 16 Round of 32, 8 Round of 16, and 2 quarter-finals.
- Verified extra-time facts:
  - `400021525`: Belgium 3-2 Senegal, Belgium winner.
  - `400021521`: Argentina 3-2 Cabo Verde, Argentina winner.
- Verified quarter-finals:
  - `400021536`: France 2-0 Morocco.
  - `400021538`: Spain 2-1 Belgium.
- Norway-England and Argentina-Switzerland were non-final and remain unresolved bracket
  fixtures. France is the default focus and is locked into the semi-finals.

### Ratings and reproducible report

- Used the project-authored pre-tournament rating seed frozen at git commit
  `a7b6e694` at its exact git timestamp `2026-06-09T23:27:23-03:00`
  (`2026-06-10T02:27:23Z`).
- Canonical ratings SHA-256:
  `983a20748541db3612dd75fa2d5dde954d1b89de52a23c1b19f345a427bca259`.
- Documentation states that this seed is leakage-free for the 72 later group outcomes,
  but is not an official FIFA source or evidence of universal calibration.
- Checked-in 72-case neutral-site report:
  - RPS: `0.1468376571147013` (uniform `0.2314814814814817`).
  - Unscaled Brier: `0.49873843934489415` (uniform `0.6666666666666662`).
  - Natural log loss: `0.8320301393116665` (uniform `1.0986122886681084`).
  - Top-pick accuracy: `0.625` (uniform expected `0.3333333333333333`).

## Strict TDD Evidence

1. Exact outcome/home-advantage RED:
   - Missing `predict_match_outcomes` import.
   - Group expected `(1540, 1500)` but received `(1500, 1500)`.
   - Two-leg expected home-side boosts but received neutral ratings.
   - GREEN: `5 passed`.
2. Typed-team entrant RED:
   - Domain rejected `team` as unsupported and schema rejected the entrant.
   - GREEN: `3 passed`.
3. Backtest evaluator RED:
   - `tournament_forecaster.backtest` did not exist.
   - GREEN: `5 passed`, later `6 passed` after the tied-top-pick boundary fix.
4. Backtest schema/CLI RED:
   - Missing schema resource and invalid CLI command choice.
   - GREEN: `2 passed`.
5. Legacy FIFA edge RED:
   - Result type `3` yielded no final records.
   - Singular `Quarter-final` returned no phase.
   - GREEN: `2 passed`.
6. Builder RED:
   - Builder module absent.
   - Saved-fixture normalization GREEN: `2 passed`.
7. Live integration regressions were captured before fixes:
   - Final group draw incorrectly required a winner; GREEN `1 passed`.
   - Top-level `W101`/`W102` placeholders were unread; GREEN `1 passed`.
   - Official group orientation contradicted canonical generated fixtures; fixed by
     score transposition at the builder boundary and preserved official orientation in
     metadata.
8. Metric tie RED:
   - Equal-rating tied maximum incorrectly scored `1.0`; after epsilon-aware unique-pick
     handling, the complete backtest module was GREEN (`6 passed`).

## Verification

- Final ownership-isolated Task 5A/generic/legacy suite after the timestamp correction:
  `334 passed in 4.75s`. This includes all generic tests except the concurrently owned
  odds/results provider modules, plus examples, CLI, clean-wheel, and legacy updater.
- Saved-input determinism: two complete builds with the same fixture and retrieval
  timestamp were byte-identical across all six artifacts (`diff -rq`, exit 0).
- `python3 -m compileall` completed successfully.
- Owned tracked diff passed `git diff --check`.
- Real snapshot generated through explicit FIFA network mode with 98 facts and 72
  backtest cases; no raw API payload was written into the repository.
- Clean-wheel isolation:
  - Built wheel, created a fresh virtual environment, and installed with `--no-deps`.
  - Cleared source `PYTHONPATH` and forced HTTP/HTTPS/ALL proxy variables to a dead
    local endpoint.
  - France, 10,000 iterations, seed `20260711`: final `54.6%`, championship `31.1%`.
  - Spain override, unchanged config, 200 iterations: final `46.5%`, championship
    `27.5%`.
  - Installed-wheel backtest returned `ok=true`, sample size `72`, and the checked hash
    and metrics.
- Repository-wide suite: `977 passed`, one unrelated failure in
  `tests/test_readme_diagrams.py::test_readme_links_target_product_and_technical_diagrams`.
  Concurrent `README.md` lacks the phrase `target architecture diagrams`; Task 5A did
  not modify or revert that concurrent surface.

## Concerns

- No Task 5A functional blocker remains.
- The repository-wide README diagram assertion remains red outside Task 5A ownership.
- During final verification, the concurrent provider worker added tests that currently
  produce 11 failures in `test_odds_provider.py` and `test_results_provider.py` against
  its still-in-progress provider/security implementation. Task 5A did not modify those
  provider files or absorb that worker's test changes.
- The builder intentionally pins this checked example to the 2026-07-11 retrieval
  boundary. A later maintainer fetch after additional matches become final will fail the
  snapshot-count guard until the example boundary and expected facts are deliberately
  advanced.
