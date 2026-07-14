# Tournament Forecaster

Tournament Forecaster is a configuration-driven hybrid engine for simulating tournament formats and publishing auditable forecasts. Its deterministic tournament engine is the source of truth, and a first-class optional multi-LLM council can research, challenge, and debrief the forecast before publication. The generic product, its schemas, and its CLI are the primary interface.

## Quickstart

```bash
git clone https://github.com/marcuscavalcanti/tournament-forecaster.git
cd tournament-forecaster
python3 -m venv .venv && . .venv/bin/activate && python -m pip install .
tournament-forecast simulate --config examples/world-cup-2026-live/tournament.json --iterations 10000 --output-dir outputs
```

The first source install requires package-index/network access for build dependencies; Hatchling is not vendored. After installation, `simulate`, `init`, and `validate` run offline without provider credentials or additional downloads.

## Ready-To-Run Examples

The repository includes two credential-free, reproducible tournament snapshots:

- **FIFA World Cup 2026, France:** group stage through the final, with France as the active focus team.
- **Copa Libertadores 2026, Palmeiras:** the confirmed round-of-16 field, fixed draw, and two-leg knockout rules.

### FIFA World Cup 2026: France

The checked-in World Cup 2026 example is normalized from the CC0 1.0 [OpenFootball World Cup 2026 snapshot](https://raw.githubusercontent.com/openfootball/worldcup.json/056c53ec82feb3fb68da63d1ce74ec59fc23e95d/2026/worldcup.json), pinned to upstream commit `056c53ec82feb3fb68da63d1ce74ec59fc23e95d`. Its `retrieved_at` value is `2026-07-13T16:35:34Z`, and the exact source SHA-256 is `b0aef8771d7fc3b6a5ec04cf7a9f9cd167c4e8b0be9152b3a35ae5629bb4e8d5`. The source contains 104 matches and 100 completed facts. Stage counts are 72 group, 16 R32, 8 R16, 4 QF, 0 SF, and 0 final. The remaining semi-finals are France-Spain and England-Argentina. Its active default focus team is France, and its frozen ratings are a project-authored, cited pre-tournament seed. It is reproducible snapshot data, not a live feed. OpenFootball-derived match facts retain CC0 status; the repository MIT license applies only to project-authored material and does not relicense those facts. The command writes:

- `outputs/fifa-world-cup-2026-live/france/forecast.json`
- `outputs/fifa-world-cup-2026-live/france/report.md`
- `outputs/fifa-world-cup-2026-live/france/bracket.svg`

Each run first creates an immutable generation directory. The CLI labels the stable focus path as the `Current alias`; `outputs/fifa-world-cup-2026-live/france` points to that complete generation, so readers never observe a partially written report.

Output publication fails closed when the lexical output path contains an ancestor symlink or junction. Use a canonical path instead. For example, macOS exposes `/tmp` as a symlink to `/private/tmp`, so use `--output-dir /private/tmp/tournament-forecaster-outputs` rather than a path below `/tmp`.

`v0.1.2` supports macOS and Linux natively. On Windows, use WSL2 and run the four POSIX quickstart commands inside the Linux distribution; native Windows is not supported in `v0.1.2`.

For a fully synthetic offline smoke test after installation:

```bash
tournament-forecast quickstart --iterations 10000 --output-dir outputs
```

### Copa Libertadores 2026: Palmeiras

The repository also includes a ready-to-run [Copa Libertadores 2026 round-of-16
snapshot](examples/copa-libertadores-2026-live/README.md) focused on Palmeiras. It
uses the official CONMEBOL field, seed order, fixed draw, and two-leg rules from a
frozen snapshot; ratings remain explicitly synthetic project inputs.

```bash
tournament-forecast validate --config examples/copa-libertadores-2026-live/tournament.json
tournament-forecast simulate --config examples/copa-libertadores-2026-live/tournament.json --iterations 10000 --output-dir outputs
```

The configuration models the official `A-H`, `B-G`, `C-F`, and `D-E` quarter-final
path. Better group-stage seeds host leg two through the semi-finals; aggregate ties
before the final go directly to penalties, while the one-leg final uses extra time
and then penalties. See the example's [source record](examples/copa-libertadores-2026-live/DATA_SOURCES.md) before refreshing the snapshot.

## Multi-LLM Council

The debriefing council is a core product capability but an optional runtime dependency. With no council configuration, every command keeps the zero-key offline behavior above. When enabled, the default policy is **55% deterministic engine / 45% council consensus**. The deterministic engine still owns completed facts, standings, legal opponents, bracket topology, and matchup probabilities.

Start from the credential-free example, choose current provider model IDs, and keep the local copy out of source control:

```bash
cp examples/council.example.json council.local.json
tournament-forecast council validate --config council.local.json
```

Set `enabled` to `true` in `council.local.json`, export only the environment variables named by its `api_key_env` fields, and run:

```bash
tournament-forecast simulate --config examples/world-cup-2026-live/tournament.json --iterations 10000 --output-dir outputs --council-config council.local.json --council
```

The default two-pass debrief keeps round one independent and gives valid reviewers anonymized peer positions in round two. Models have equal voting weight; the median valid opinion becomes the council consensus. Model, provider, effort, rounds, failures, and consensus are retained in `forecast.json` and `report.md`. If the council misses quorum or a provider fails, the run falls back to the deterministic baseline and records the reason instead of weakening tournament invariants.

Hard-disable all model calls without changing the saved configuration:

```bash
tournament-forecast simulate --config examples/world-cup-2026-live/tournament.json --iterations 10000 --output-dir outputs --council-config council.local.json --no-council
```

Supported adapters are OpenAI Responses, Anthropic Messages, Google Gemini, and HTTPS OpenAI-compatible chat endpoints. Model IDs and reasoning controls remain explicit because provider availability and capabilities change independently of this repository. See [Configuration](docs/CONFIGURATION.md), [Providers](docs/PROVIDERS.md), and [Security](SECURITY.md).

## Backtesting

Run the checked-in evaluation dataset with:

```bash
tournament-forecast backtest --input examples/world-cup-2026-live/backtest.json
```

The committed report has sample size 72 and records RPS `0.146838`, multiclass Brier `0.498738`, natural log loss `0.832030`, and top-pick accuracy `0.625000`. It scores the deterministic rating/Poisson core, not the optional multi-model council and not historical LinkedIn posts. The evidence is limited to one tournament and a project-authored pre-tournament rating seed; it is not proof of universal calibration.

## Supported Formats

Implemented contracts include:

- round-robin groups with direct and best additional qualifiers;
- league stages with explicit fixtures and qualification bands;
- fixed, seeded, and open draws;
- one-leg and two-leg knockout ties;
- completed-result locking so known matches are never resimulated;
- an optional, auditable multi-LLM debriefing council with a fixed 55/45 blend and deterministic fallback; and
- JSON forecast output with rendered Markdown and SVG artifacts.

The engine does not infer arbitrary tournament rules from prose, schedule matches, or fetch tournament results and odds implicitly. Data acquisition remains an explicit provider step. The optional council makes direct HTTPS model calls only when it is configured and enabled. Unsupported tie-break rules and competition-specific edge cases must be modeled before use, not silently approximated.

## Configuration

List packaged presets or create a project-owned configuration:

```bash
tournament-forecast presets list
tournament-forecast init my-tournament --template group-knockout
tournament-forecast validate --config my-tournament/tournament.json
tournament-forecast simulate --config my-tournament/tournament.json --focus-team team-id
```

See [Configuration](docs/CONFIGURATION.md), [Adding a competition](docs/ADDING_A_COMPETITION.md), and [Adding a provider](docs/ADDING_A_PROVIDER.md).

## Providers And Data

The CLI consumes validated local JSON or CSV files. Imports are preview-first, credentials remain outside committed configuration, and raw provider responses are not repository artifacts. See [Providers](docs/PROVIDERS.md) and [Data policy](docs/DATA_POLICY.md).

## Architecture

The committed diagrams describe the authoritative offline core, the first-class optional council, and the remaining extension boundaries. They are custom AWS-style SVG assets with matching PNG exports, not Mermaid diagrams.

- [Product flow SVG](docs/assets/architecture/product-flow.svg) ([PNG](docs/assets/architecture/product-flow.png))
- [Technical architecture SVG](docs/assets/architecture/technical-architecture.svg) ([PNG](docs/assets/architecture/technical-architecture.png))
- [Asset manifest and generation contract](docs/assets/architecture/README.md)
- [Architecture contract](docs/ARCHITECTURE.md)
- [Product flow](docs/PRODUCT_FLOW.md)

## Trust And Governance

Tournament configuration is trusted local code-like input. The generic CLI does not execute local command bridges; any future bridge requires a separate reviewed design. Security boundaries and private reporting are documented in [Security](SECURITY.md). Contributions follow [Contributing](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and the [MIT license](LICENSE).

This project is independent and carries no vendor or governing-body affiliation. See [NOTICE](NOTICE.md).
