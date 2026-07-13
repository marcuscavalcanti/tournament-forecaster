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
- `tournament.id` is the stable tournament ID.
- `tournament.display_name` is presentation text; references always use IDs.
- `tournament.season` is optional presentation metadata for the competition edition.
- `focus_team_id` is a root field that selects the report focus unless the CLI overrides it.
- `teams` contains stable IDs, display names, and optional aliases.
- `ratings` are frozen numeric inputs whose provenance belongs in metadata or a data-sources document.
- `stages` define ordered round-robin group, league-table, and knockout contracts.
- `completed_matches` is an append-oriented ledger of locked facts.

Pairing modes are `fixed`, `seeded_draw`, and `open_draw`. Knockout stages declare one or two legs and their home/away policy. Group and league stages declare qualification rules explicitly; the engine does not infer competition rules from a name.

## Minimal Valid Configuration

```json
{
  "schema_version": 2,
  "tournament": {"id": "group-knockout-template", "display_name": "Group Knockout Template", "season": "2026"},
  "focus_team_id": "alpha-club",
  "teams": [
    {"id": "alpha-club", "display_name": "Alpha Club"}, {"id": "bravo-town", "display_name": "Bravo Town"},
    {"id": "charlie-fc", "display_name": "Charlie FC"}, {"id": "delta-united", "display_name": "Delta United"},
    {"id": "echo-athletic", "display_name": "Echo Athletic"}, {"id": "foxtrot-rovers", "display_name": "Foxtrot Rovers"},
    {"id": "golf-city", "display_name": "Golf City"}, {"id": "hotel-club", "display_name": "Hotel Club"}
  ],
  "stages": [
    {
      "id": "group-stage", "type": "round_robin_groups",
      "groups": {"A": ["alpha-club", "bravo-town", "charlie-fc", "delta-united"], "B": ["echo-athletic", "foxtrot-rovers", "golf-city", "hotel-club"]},
      "rounds_per_pair": 1,
      "points": {"win": 3, "draw": 1, "loss": 0},
      "tiebreakers": ["points", "goal_difference", "goals_for", "wins", "rating", "team_id"],
      "qualification": {"direct_per_group": 2, "best_additional": 0, "additional_rank": 3}
    },
    {
      "id": "semi-finals", "type": "knockout",
      "pairing": {"mode": "fixed", "ties": [
        {"id": "semi-final-1", "entrants": [{"type": "group_rank", "stage_id": "group-stage", "group": "A", "rank": 1}, {"type": "group_rank", "stage_id": "group-stage", "group": "B", "rank": 2}]},
        {"id": "semi-final-2", "entrants": [{"type": "group_rank", "stage_id": "group-stage", "group": "B", "rank": 1}, {"type": "group_rank", "stage_id": "group-stage", "group": "A", "rank": 2}]}
      ]},
      "legs": 1, "home_away_order": "listed_team_first_leg_home", "aggregate_tiebreak": "extra_time_then_penalties", "away_goals_rule": false
    },
    {
      "id": "final", "type": "knockout",
      "pairing": {"mode": "fixed", "ties": [{"id": "final-1", "entrants": [{"type": "match_winner", "match_id": "semi-final-1"}, {"type": "match_winner", "match_id": "semi-final-2"}]}]},
      "legs": 1, "home_away_order": "listed_team_first_leg_home", "aggregate_tiebreak": "extra_time_then_penalties", "away_goals_rule": false, "terminal": "championship"
    }
  ],
  "ratings": {"alpha-club": 1600, "bravo-town": 1560, "charlie-fc": 1520, "delta-united": 1480, "echo-athletic": 1590, "foxtrot-rovers": 1550, "golf-city": 1510, "hotel-club": 1470},
  "completed_matches": []
}
```

## Local And Secret Settings

Tournament rules and council policy use separate JSON files. This keeps a reusable tournament offline and prevents model-provider choices from becoming tournament truth.

Start from `examples/council.example.json`. Its public contract is:

- `enabled` is the saved default; `--council` and `--no-council` override it for one run.
- `engine_weight` must be `0.55` and `council_weight` must be `0.45`; this fixed public policy prevents configuration drift from contradicting the audit.
- `rounds` defaults to `2`: independent opinions first, then an anonymized peer review.
- `minimum_valid_agents` is the quorum required before the consensus can affect the forecast.
- `timeout_seconds` and `max_attempts` bound each provider call.
- Each agent declares a stable `id`, presentation `display_name`, `provider`, current `model`, `api_key_env`, and `enabled` state.
- `reasoning_effort` supports `none`, `minimal`, `low`, `medium`, `high`, and `xhigh` where the provider supports it.
- `thinking_budget_tokens` exposes providers that use an explicit thinking budget instead of an effort label.
- `max_output_tokens` and `temperature` are per-agent controls.
- An `openai-compatible` agent must declare an HTTPS `endpoint`; standard OpenAI, Anthropic, and Gemini endpoints are supplied by the adapter.

Validate policy, models, effort, endpoints, and quorum without making a network call:

```bash
tournament-forecast council validate --config council.local.json
```

Council configuration contains environment variable names, never credential values. A missing key becomes a classified agent failure; if valid agents fall below quorum, the forecast uses the deterministic baseline and records the fallback.

Commit reusable public configuration only. Keep credentials in environment variables and machine-specific values in ignored `*.local.json` files. `.env.example` names optional variables but contains no values. The generic simulation, validation, reporting, schema, preset, and backtest paths require no provider key. Council calls require only the keys used by enabled agents.

Configuration is trusted input. Review local files and templates before running them. The generic CLI does not implement a local command bridge or accept executable commands from configuration.

## Output Paths

Report publication rejects an output directory whose lexical path contains an ancestor symlink or junction. Pass the canonical path as `--output-dir`. On macOS, use `/private/tmp/tournament-forecaster-outputs` instead of the `/tmp/tournament-forecaster-outputs` alias.

## Validation And Reproducibility

```bash
tournament-forecast validate --config my-tournament/tournament.json
tournament-forecast simulate --config my-tournament/tournament.json --seed 2026 --iterations 10000
```

Record source citations, retrieval timestamps, rating methodology, seed, and iteration count. Never replace an observed completed match with a simulated value. See [Data policy](DATA_POLICY.md) for repository rules.
