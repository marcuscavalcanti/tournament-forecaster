# Migration From World Cup Brazil

The `worldcup_brazil` package and `worldcup-brazil-report` command are deprecated compatibility. Both aliases are retained throughout all `v0.1.x` releases; new integrations must use `tournament_forecaster` and `tournament-forecast`.

Removal is permitted only when both conditions are true: the package version is `v0.2.0` or later and the calendar date is `2026-10-01` or later. Neither threshold alone permits removal.

## Migration Map

| Legacy concern | Generic replacement |
| --- | --- |
| Brazil-specific configuration | Versioned tournament JSON with nested `tournament.id` and root `focus_team_id` |
| Daily report entry point | `tournament-forecast simulate` |
| Brazil-only bracket assumptions | Explicit stage and pairing contracts |
| Mutable dated output files | Immutable generation plus stable `current` alias |
| Provider calls inside orchestration | Separate acquisition and local preview/apply |
| LinkedIn post rendering | Generic JSON, Markdown, and SVG artifacts |
| Model council | First-class optional multi-LLM council, configured separately and bounded by the fixed 55/45 engine/council policy |

## Explicit Legacy Opt-Ins

No `.env` file or shell profile is loaded implicitly. Pass a trusted dotenv file with `--env-file`; pass a trusted shell-style export file with `--shell-env-file`. These options parse assignments without executing the file as shell code.

Local executable and browser bridges also remain off unless explicitly enabled. Use `--bridges` to enable configured bridges for one invocation, or `--no-bridges` to disable them even when configuration or the inherited environment enables them:

```bash
worldcup-brazil-report --env-file /path/to/operator.env --bridges
worldcup-brazil-report --shell-env-file /path/to/operator-profile.env --no-bridges
```

For persistent operator-owned configuration, use the `bridges_enabled` config field. Every `browser_command` must be an argument array, not a shell string; the runner executes the array directly without a shell:

```json
{
  "bridges_enabled": true,
  "agents": [
    {
      "slot": "GPT 5.5",
      "browser_command": ["codex", "exec", "{prompt}"]
    }
  ]
}
```

Bridge subprocesses receive a least-privilege environment: basic process, locale, proxy, and certificate variables; the known CLI credential; and the `env_api_key` declared for that agent. Unrelated provider, cloud, GitHub, and database credentials are not inherited. Remote custom API endpoints must use HTTPS. Plain HTTP is reserved for `localhost` and loopback IP addresses used during local development.

When `bridges_enabled` is omitted from config, the inherited-environment alternative is `WORLDCUP_ENABLE_BRIDGES=1`. It does not load a profile; an explicit CLI bridge flag still has highest precedence:

```bash
WORLDCUP_ENABLE_BRIDGES=1 worldcup-brazil-report
```

The legacy Make targets expose the same choices intentionally through `LEGACY_ENV_FILE`, `LEGACY_SHELL_ENV_FILE`, and `LEGACY_BRIDGES`. `daily` and `force` pass an explicit environment file to both the market-odds refresh and report process; `doctor` receives the environment-file and bridge choices. An empty variable adds no flag; `LEGACY_BRIDGES` accepts only `0` or `1` when set:

```bash
make daily LEGACY_ENV_FILE=/path/to/operator.env
make force LEGACY_SHELL_ENV_FILE=/path/to/operator-profile.env LEGACY_BRIDGES=1
make daily LEGACY_BRIDGES=0
```

## Steps

1. Create a generic tournament configuration from a template.
2. Move team identities, ratings, fixtures, stages, and completed results into the versioned schema.
3. Set the root `focus_team_id` or pass `--focus-team` at runtime.
4. Validate and compare deterministic stage probabilities against the legacy seeded fixture.
5. Update automation to consume `forecast.json`, `report.md`, or `bracket.svg` under the stable `current` alias.
6. Remove legacy command, model-key, and post-template dependencies only after the `v0.2.0` and `2026-10-01` gates are both satisfied.

Historical Portuguese posts, output fixtures, and migration-only test data remain compatibility evidence; they are not part of the English public product surface.
