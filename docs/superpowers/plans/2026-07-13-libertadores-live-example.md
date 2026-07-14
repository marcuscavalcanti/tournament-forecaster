# Copa Libertadores 2026 Live Example Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a reproducible Copa Libertadores 2026 round-of-16 snapshot for Palmeiras, while preserving the World Cup 2026 backtest contract.

**Architecture:** Add one explicit knockout home-leg rule: the team with the better pre-knockout seed hosts the second leg, regardless of which earlier tie it won. The public example begins at the officially drawn round of 16, avoiding a false claim to recompute CONMEBOL's completed group standings from an unsupported head-to-head rule. Factual bracket facts are local, cited, and frozen; ratings are project-authored synthetic inputs.

**Tech Stack:** Python 3.11+, JSON Schema 2020-12, pytest, packaged JSON resources, deterministic Monte Carlo CLI.

## Global Constraints

- Do not fetch network data during `validate`, `simulate`, or `backtest`.
- Use `aggregate_tiebreak: "penalties"` for Libertadores two-leg ties and `"extra_time_then_penalties"` only for its single-leg final.
- Use official CONMEBOL 2026 sources for the qualified field, seeds, draw, bracket, and schedule; commit only normalized facts, citations, and retrieval timestamps.
- Do not include CONMEBOL logos, raw provider payloads, or governing-body affiliation claims.
- The live example is a round-of-16 snapshot and must not claim to recalculate completed group standings.
- Keep public documentation in English.
- Preserve the World Cup 2026 backtest values: sample size 72, RPS 0.146838, Brier 0.498738, log loss 0.832030, top-pick accuracy 0.625000.

---

## File Map

- `src/tournament_forecaster/domain.py`: validate and normalize root `knockout_seeds` plus `better_seed_second_leg_home`.
- `src/tournament_forecaster/config.py`: parse `knockout_seeds` into `Tournament`.
- `src/tournament_forecaster/schemas/tournament.schema.json`: describe the new root property and home/away enum.
- `src/tournament_forecaster/simulation.py`: pass immutable seed ranks to each knockout stage.
- `src/tournament_forecaster/stages/knockout_stage.py`: choose two-leg home order from resolved seed ranks.
- `tests/tournament_forecaster/test_domain_and_config.py`: reject invalid dynamic-home seed contracts.
- `tests/tournament_forecaster/test_knockout_stage.py`: prove seed ordering controls the second leg, including future rounds.
- `src/tournament_forecaster/data/presets/libertadores-style/tournament.json`: correct pre-final tiebreaks to direct penalties.
- `tests/presets/test_format_contracts.py`: lock the generic preset rule.
- `examples/copa-libertadores-2026-live/`: factual snapshot, provenance, and one-command README.
- `tests/examples/test_copa_libertadores_2026.py`: snapshot topology and reproducibility.
- `README.md`, `pyproject.toml`, `tests/test_public_repository_contract.py`: expose and package the example.

## Official Snapshot Facts

The frozen field and seed ranks are: Flamengo 1, Independiente Rivadavia 2, Independiente del Valle 3, Universidad Catolica 4, Cerro Porteno 5, LDU Quito 6, Corinthians 7, Coquimbo Unido 8, Rosario Central 9, Mirassol 10, Palmeiras 11, Cruzeiro 12, Platense 13, Estudiantes 14, Deportes Tolima 15, and Fluminense 16.

The R16 labels are: A Estudiantes/Universidad Catolica; B Mirassol/LDU Quito; C Fluminense/Independiente Rivadavia; D Deportes Tolima/Independiente del Valle; E Cruzeiro/Flamengo; F Platense/Coquimbo Unido; G Palmeiras/Cerro Porteno; H Rosario Central/Corinthians.

The official downstream bracket is A-H, B-G, C-F, D-E in the quarter-finals; then QF1-QF4 and QF2-QF3 in the semi-finals. R16, quarter-finals, and semi-finals are two legs/direct penalties; the Montevideo final is one leg/extra time then penalties.

### Task 1: Dynamic Better-Seed Home-Leg Contract

**Files:**
- Modify: `src/tournament_forecaster/domain.py`
- Modify: `src/tournament_forecaster/config.py`
- Modify: `src/tournament_forecaster/schemas/tournament.schema.json`
- Modify: `src/tournament_forecaster/simulation.py`
- Modify: `src/tournament_forecaster/stages/knockout_stage.py`
- Test: `tests/tournament_forecaster/test_domain_and_config.py`
- Test: `tests/tournament_forecaster/test_knockout_stage.py`

**Interfaces:**
- Consumes root JSON `knockout_seeds: {"team-id": positive_unique_integer}`.
- Produces `Tournament.knockout_seeds: Mapping[str, int]`.
- Produces stage value `home_away_order: "better_seed_second_leg_home"`.
- Extends `simulate_knockout_stage(..., knockout_seeds: Mapping[str, int])`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_better_seed_second_leg_home_requires_complete_unique_known_seeds() -> None:
    document = _two_leg_tie_document()
    document["knockout_seeds"] = {"alpha": 1, "bravo": 1}
    document["stages"][1]["home_away_order"] = "better_seed_second_leg_home"

    with pytest.raises(TournamentValidationError, match="knockout seeds"):
        load_tournament_document(document)


def test_better_seed_second_leg_home_requires_every_possible_entrant_seed() -> None:
    document = _two_leg_tie_document()
    document["knockout_seeds"] = {"alpha": 1}
    document["stages"][1]["home_away_order"] = "better_seed_second_leg_home"

    with pytest.raises(TournamentValidationError, match="knockout seeds"):
        load_tournament_document(document)
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/tournament_forecaster/test_domain_and_config.py -k better_seed_second_leg_home -q`

Expected: failure because neither the root property nor the new home-order value exists.

- [ ] **Step 3: Write failing behavior tests**

```python
def test_better_seed_hosts_second_leg_when_pairing_order_is_not_seed_order() -> None:
    stage = _stage(legs=2)
    stage["home_away_order"] = "better_seed_second_leg_home"
    result = simulate_knockout_stage(
        stage,
        state=QualificationState(),
        ratings={"alpha": 1600.0, "bravo": 1600.0},
        knockout_seeds={"alpha": 11, "bravo": 3},
        completed_matches=(),
        rng=random.Random(7),
    )
    assert [(match.home_team_id, match.away_team_id) for match in result.matches] == [
        ("alpha", "bravo"),
        ("bravo", "alpha"),
    ]
```

Add this exact future-round regression alongside it:

```python
def test_better_seed_hosts_second_leg_after_match_winner_resolution() -> None:
    state = QualificationState(match_winners={"r16-a": "alpha", "r16-b": "bravo"})
    stage = _stage(legs=2)
    stage["pairing"]["ties"][0]["entrants"] = [
        {"type": "match_winner", "match_id": "r16-a"},
        {"type": "match_winner", "match_id": "r16-b"},
    ]
    stage["home_away_order"] = "better_seed_second_leg_home"
    result = simulate_knockout_stage(
        stage,
        state=state,
        ratings={"alpha": 1600.0, "bravo": 1600.0},
        knockout_seeds={"alpha": 14, "bravo": 2},
        completed_matches=(),
        rng=random.Random(7),
    )
    assert [(match.home_team_id, match.away_team_id) for match in result.matches] == [
        ("alpha", "bravo"),
        ("bravo", "alpha"),
    ]
```

- [ ] **Step 4: Verify RED**

Run: `pytest tests/tournament_forecaster/test_knockout_stage.py -k better_seed_hosts_second_leg -q`

Expected: failure because `simulate_knockout_stage` does not accept `knockout_seeds`.

- [ ] **Step 5: Implement the smallest contract**

1. Add `knockout_seeds` to the root schema and `Tournament`, defaulting to an immutable empty mapping.
2. Add `better_seed_second_leg_home` to the domain and schema enums.
3. Reject seed IDs not in `teams`, non-positive/non-integer seeds, duplicate numeric seeds, and dynamic-home stages whose possible entrants lack a seed.
4. Pass seed ranks from `simulate_tournament` to `simulate_knockout_stage`.
5. When the new rule is selected, compare resolved numeric seed ranks: lower number hosts leg two.
6. Use the same resolution in `_locked_pairs` so official completed legs cannot contradict the configured orientation.
7. Preserve the existing two home-order modes byte-for-byte in behavior.

- [ ] **Step 6: Verify GREEN**

Run: `pytest tests/tournament_forecaster/test_domain_and_config.py tests/tournament_forecaster/test_knockout_stage.py -q`

Expected: all focused tests pass, including legacy orientation and aggregate-tie tests.

- [ ] **Step 7: Commit**

```bash
git add src/tournament_forecaster/domain.py src/tournament_forecaster/config.py src/tournament_forecaster/simulation.py src/tournament_forecaster/stages/knockout_stage.py src/tournament_forecaster/schemas/tournament.schema.json tests/tournament_forecaster/test_domain_and_config.py tests/tournament_forecaster/test_knockout_stage.py
git commit -m "feat: support dynamic knockout seed home legs"
```

### Task 2: Correct The Generic Libertadores Preset

**Files:**
- Modify: `src/tournament_forecaster/data/presets/libertadores-style/tournament.json`
- Test: `tests/presets/test_format_contracts.py`

- [ ] **Step 1: Write the failing test**

```python
def test_libertadores_style_uses_direct_penalties_before_the_final() -> None:
    tournament = load_tournament(preset_path("libertadores-style"))
    stages = {str(stage["id"]): stage for stage in tournament.stages}
    assert stages["quarter-finals"]["aggregate_tiebreak"] == "penalties"
    assert stages["semi-finals"]["aggregate_tiebreak"] == "penalties"
    assert stages["final"]["aggregate_tiebreak"] == "extra_time_then_penalties"
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/presets/test_format_contracts.py -k libertadores_style_uses_direct_penalties -q`

Expected: failure because the two-leg stages declare extra time.

- [ ] **Step 3: Implement**

Change only the quarter-final and semi-final values to `"penalties"`; retain the final's `"extra_time_then_penalties"`.

- [ ] **Step 4: Verify GREEN and commit**

Run: `pytest tests/presets/test_format_contracts.py tests/tournament_forecaster/test_package_resources.py -q`

```bash
git add src/tournament_forecaster/data/presets/libertadores-style/tournament.json tests/presets/test_format_contracts.py
git commit -m "fix: model Libertadores aggregate penalties correctly"
```

### Task 3: Publish The Palmeiras R16 Snapshot

**Files:**
- Create: `examples/copa-libertadores-2026-live/tournament.json`
- Create: `examples/copa-libertadores-2026-live/DATA_SOURCES.md`
- Create: `examples/copa-libertadores-2026-live/README.md`
- Test: `tests/examples/test_copa_libertadores_2026.py`

**Interfaces:**
- Consumes Task 1's dynamic home-leg rule.
- Produces a credential-free, deterministic `validate` and `simulate` example with `palmeiras` as focus team.

- [ ] **Step 1: Write the failing topology test**

```python
def test_copa_libertadores_snapshot_has_official_field_draw_and_progression() -> None:
    tournament = load_tournament(EXAMPLE_CONFIG)
    assert tournament.focus_team_id == "palmeiras"
    assert tournament.knockout_seeds["palmeiras"] == 11
    stages = {str(stage["id"]): stage for stage in tournament.stages}
    assert _tie_entrants(stages["round-of-16"], "r16-g") == {"palmeiras", "cerro-porteno"}
    assert _winner_sources(stages["quarter-finals"], "quarter-final-2") == {"r16-b", "r16-g"}
    assert _winner_sources(stages["semi-finals"], "semi-final-1") == {"quarter-final-1", "quarter-final-4"}
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/examples/test_copa_libertadores_2026.py -q`

Expected: failure because the example does not exist.

- [ ] **Step 3: Build the frozen configuration**

Create a 16-team document with `focus_team_id: "palmeiras"`, the official seed map above, no completed knockout results, and project-authored ratings. Use fixed ties `r16-a` through `r16-h`, fixed quarter-finals A-H/B-G/C-F/D-E, semi-finals QF1-QF4/QF2-QF3, and `final-1`.

All pre-final stages use `legs: 2`, `home_away_order: "better_seed_second_leg_home"`, `aggregate_tiebreak: "penalties"`, and `away_goals_rule: false`. The final uses one leg, `extra_time_then_penalties`, and terminal `championship`.

- [ ] **Step 4: Add provenance and boundary docs**

`DATA_SOURCES.md` cites the official group/draw page, R16 schedule, and 2026 club manual. It states that R16 field/seeds/draw/bracket are normalized facts; ratings are synthetic project inputs; the snapshot begins after the group phase; no raw response/logo/affiliation is included; and the retrieval date is frozen.

`README.md` contains:

```bash
tournament-forecast validate --config examples/copa-libertadores-2026-live/tournament.json
tournament-forecast simulate --config examples/copa-libertadores-2026-live/tournament.json --iterations 10000 --output-dir outputs
```

- [ ] **Step 5: Add deterministic-output coverage**

```python
def test_copa_libertadores_snapshot_simulates_reproducibly() -> None:
    tournament = load_tournament(EXAMPLE_CONFIG)
    first = simulate_tournament(tournament, SimulationOptions(seed=20260811, iterations=500))
    second = simulate_tournament(tournament, SimulationOptions(seed=20260811, iterations=500))
    assert first.to_dict() == second.to_dict()
    assert first.focus_team_id == "palmeiras"
    assert 0.0 <= first.championship_probability <= 1.0
```

- [ ] **Step 6: Verify GREEN and commit**

Run:

```bash
pytest tests/examples/test_copa_libertadores_2026.py -q
tournament-forecast validate --config examples/copa-libertadores-2026-live/tournament.json
tournament-forecast simulate --config examples/copa-libertadores-2026-live/tournament.json --iterations 500 --output-dir /private/tmp/libertadores-example-output
```

```bash
git add examples/copa-libertadores-2026-live tests/examples/test_copa_libertadores_2026.py
git commit -m "feat: add Copa Libertadores 2026 live example"
```

### Task 4: Package It And Prove World Cup Backtest Stability

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml`
- Modify: `tests/test_public_repository_contract.py`
- Test: `tests/examples/test_openfootball_world_cup_2026.py`

- [ ] **Step 1: Write the failing public-example test**

```python
def test_public_repository_includes_copa_libertadores_live_example() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for target in ("wheel", "sdist"):
        includes = metadata["tool"]["hatch"]["build"]["targets"][target]["include"]
        for path in (
            "/examples/copa-libertadores-2026-live/tournament.json",
            "/examples/copa-libertadores-2026-live/README.md",
            "/examples/copa-libertadores-2026-live/DATA_SOURCES.md",
        ):
            assert path in includes
```

- [ ] **Step 2: Verify RED**

Run: `pytest tests/test_public_repository_contract.py -k copa_libertadores_live -q`

Expected: failure because the new example is not in either package include list.

- [ ] **Step 3: Integrate**

Add a `Copa Libertadores 2026` Quickstart subsection after the World Cup example, explain the R16 snapshot boundary, Palmeiras focus, source disclaimer, and link to the example provenance. Add the three example files to both relevant `pyproject.toml` distribution include lists. Do not add root scripts.

- [ ] **Step 4: Verify public contract and World Cup regression**

Run:

```bash
pytest tests/test_public_repository_contract.py tests/examples/test_openfootball_world_cup_2026.py -q
tournament-forecast backtest --input examples/world-cup-2026-live/backtest.json
```

Expected: all public checks pass and the World Cup backtest reports exactly the Global Constraints values.

- [ ] **Step 5: Commit**

```bash
git add README.md pyproject.toml tests/test_public_repository_contract.py
git commit -m "docs: document Copa Libertadores live example"
```

### Task 5: Whole-Branch Verification And Adversarial Review

- [ ] **Step 1: Run the authoritative suite**

Run: `pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Build and inspect the source distribution**

```bash
python -m build --sdist
tar -tzf dist/*.tar.gz | rg 'copa-libertadores-2026-live/(tournament.json|README.md|DATA_SOURCES.md)'
```

Expected: all three example files are packaged.

- [ ] **Step 3: Run streamed adversarial review**

Run:

```bash
claude -p --model claude-opus-4-8 --effort xhigh --permission-mode plan --tools '' --no-session-persistence --output-format stream-json --include-partial-messages --verbose
```

Give the reviewer the branch diff and these acceptance criteria: no extra time before the final; A-H/B-G/C-F/D-E; better seed hosts leg two; snapshot does not claim to recompute group standings; World Cup backtest remains unchanged; all public docs are English. Treat streamed partial output as liveness evidence. If time runs out, mark the review incomplete rather than discarding partial findings.

- [ ] **Step 4: Triage material reviewer findings before merge**

If the review reports a material defect, stop the merge workflow, write a narrowly scoped TDD task for that defect, and rerun the relevant focused tests plus the whole suite after its fix. If there is no material defect, record the reviewer verdict in the final delivery.

- [ ] **Step 5: Commit the plan**

```bash
git add docs/superpowers/plans/2026-07-13-libertadores-live-example.md
git commit -m "docs: add Libertadores live example plan"
```
