# Migration From World Cup Brazil

The `worldcup_brazil` package and `worldcup-brazil-report` command are deprecated compatibility. They remain for one release cycle; new integrations must use `tournament_forecaster` and `tournament-forecast`.

## Migration Map

| Legacy concern | Generic replacement |
| --- | --- |
| Brazil-specific configuration | Versioned tournament JSON plus `default_focus_team_id` |
| Daily report entry point | `tournament-forecast simulate` |
| Brazil-only bracket assumptions | Explicit stage and pairing contracts |
| Mutable dated output files | Immutable generation plus stable `current` alias |
| Provider calls inside orchestration | Separate acquisition and local preview/apply |
| LinkedIn post rendering | Generic JSON, Markdown, and SVG artifacts |
| Model council | Optional future extension; not part of the deterministic CLI forecast |

## Steps

1. Create a generic tournament configuration from a template.
2. Move team identities, ratings, fixtures, stages, and completed results into the versioned schema.
3. Set the desired default focus team or pass `--focus-team` at runtime.
4. Validate and compare deterministic stage probabilities against the legacy seeded fixture.
5. Update automation to consume `forecast.json`, `report.md`, or `bracket.svg` under the stable `current` alias.
6. Remove legacy command, model-key, and post-template dependencies after one successful release cycle.

Historical Portuguese posts, output fixtures, and migration-only test data remain compatibility evidence; they are not part of the English public product surface.
