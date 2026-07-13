# Configuration

Tournament Forecaster reads versioned JSON and fails before simulation when a structural or semantic contract is invalid.

## Start From A Template

```bash
tournament-forecast presets list
tournament-forecast init my-tournament --template group-knockout
tournament-forecast validate --config my-tournament/tournament.json
```

Packaged templates cover group-to-knockout, league-to-knockout, and group-to-two-leg-knockout structures. Root presets mirror packaged resources and are parity-tested.

## Core Fields

- `schema_version` selects the public tournament schema.
- `tournament_id` and every team, stage, fixture, tie, and match ID must be stable.
- `display_name` is presentation text; references always use IDs.
- `default_focus_team_id` selects the report focus unless the CLI overrides it.
- `ratings` are frozen numeric inputs whose provenance belongs in metadata or a data-sources document.
- `stages` define ordered round-robin group, league-table, and knockout contracts.
- `completed_matches` is an append-oriented ledger of locked facts.

Pairing modes are `fixed`, `seeded_draw`, and `open_draw`. Knockout stages declare one or two legs and their home/away policy. Group and league stages declare qualification rules explicitly; the engine does not infer competition rules from a name.

## Local And Secret Settings

Commit reusable public configuration only. Keep credentials in environment variables and machine-specific values in ignored `*.local.json` files. `.env.example` names optional variables but contains no values. The generic simulation, validation, reporting, schema, preset, and backtest paths require no provider key.

Configuration is trusted input. Review local files and templates before running them, especially when an operator has separately enabled a local command bridge.

## Validation And Reproducibility

```bash
tournament-forecast validate --config my-tournament/tournament.json
tournament-forecast simulate --config my-tournament/tournament.json --seed 2026 --iterations 10000
```

Record source citations, retrieval timestamps, rating methodology, seed, and iteration count. Never replace an observed completed match with a simulated value. See [Data policy](DATA_POLICY.md) for repository rules.
