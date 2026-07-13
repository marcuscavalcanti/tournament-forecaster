# Tournament Forecaster

Tournament Forecaster is an offline-first, configuration-driven engine for simulating tournament formats and publishing auditable forecasts. The generic product, its schemas, and its CLI are the primary interface.

## Quickstart

```bash
git clone https://github.com/marcuscavalcanti/worldcup2026.git
cd worldcup2026
python3 -m venv .venv && . .venv/bin/activate && python -m pip install .
tournament-forecast simulate --config examples/world-cup-2026-live/tournament.json --iterations 10000 --output-dir outputs
```

The first source install requires package-index/network access for build dependencies; Hatchling is not vendored. After installation, `simulate`, `init`, and `validate` run offline without provider credentials or additional downloads.

The checked-in World Cup 2026 example is a normalized snapshot of fixture and result facts from the official FIFA calendar endpoint. Its `retrieved_at` value is `2026-07-13T12:21:03Z`; it contains 100 completed facts. Stage counts are 72 group, 16 R32, 8 R16, 4 QF, 0 SF, and 0 final. The remaining semi-finals are France-Spain and England-Argentina. Its active default focus team is France, and its frozen ratings are a project-authored, cited pre-tournament seed. It is reproducible snapshot data, not a live feed. The command writes:

- `outputs/fifa-world-cup-2026-live/france/forecast.json`
- `outputs/fifa-world-cup-2026-live/france/report.md`
- `outputs/fifa-world-cup-2026-live/france/bracket.svg`

Each run first creates an immutable generation directory. The CLI labels the stable focus path as the `Current alias`; `outputs/fifa-world-cup-2026-live/france` points to that complete generation, so readers never observe a partially written report.

Output publication fails closed when the lexical output path contains an ancestor symlink or junction. Use a canonical path instead. For example, macOS exposes `/tmp` as a symlink to `/private/tmp`, so use `--output-dir /private/tmp/tournament-forecaster-outputs` rather than a path below `/tmp`.

`v0.1.0` supports macOS and Linux natively. On Windows, use WSL2 and run the four POSIX quickstart commands inside the Linux distribution; native Windows is not supported in `v0.1.0`.

For a fully synthetic offline smoke test after installation:

```bash
tournament-forecast quickstart --iterations 10000 --output-dir outputs
```

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
- completed-result locking so known matches are never resimulated; and
- JSON forecast output with rendered Markdown and SVG artifacts.

The engine does not yet infer arbitrary tournament rules from prose, schedule matches, fetch provider data over the network, or run the optional multi-model council through the generic CLI. Provider acquisition is an explicit external step. Unsupported tie-break rules and competition-specific edge cases must be modeled before use, not silently approximated.

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

The committed diagrams describe the authoritative offline core and clearly mark future extension boundaries. They are custom AWS-style SVG assets with matching PNG exports, not Mermaid diagrams.

- [Product flow SVG](docs/assets/architecture/product-flow.svg) ([PNG](docs/assets/architecture/product-flow.png))
- [Technical architecture SVG](docs/assets/architecture/technical-architecture.svg) ([PNG](docs/assets/architecture/technical-architecture.png))
- [Asset manifest and generation contract](docs/assets/architecture/README.md)
- [Architecture contract](docs/ARCHITECTURE.md)
- [Product flow](docs/PRODUCT_FLOW.md)

## Legacy Compatibility

The Brazil-focused `worldcup_brazil` workflow is deprecated compatibility, not the product entry point. Its package and command aliases remain throughout `v0.1.x` and may be removed only when the release is `v0.2.0` or later and the date is `2026-10-01` or later. See [Migration from World Cup Brazil](docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md) for explicit environment-file, bridge, and command-array opt-ins.

## Trust And Governance

Tournament configuration is trusted local code-like input. The generic CLI does not execute local command bridges; any future bridge requires a separate reviewed design. Security boundaries and private reporting are documented in [Security](SECURITY.md). Contributions follow [Contributing](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and the [MIT license](LICENSE).

This project is independent and carries no vendor or governing-body affiliation. See [NOTICE](NOTICE.md).
