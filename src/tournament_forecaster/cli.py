"""English, offline-first command-line interface for Tournament Forecaster."""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from .atomic_io import atomic_write_text
from .config import load_tournament
from .domain import Forecast, SimulationOptions, Tournament
from .errors import TournamentValidationError
from .reports import write_rendered_reports, write_report_bundle
from .reports.json_report import load_forecast
from .providers.odds import preview_odds
from .providers.results import apply_results, preview_results
from .resources import (
    copy_template,
    list_bundled_presets,
    list_bundled_templates,
    load_bundled_preset,
    resource_path,
)
from .simulation import simulate_tournament


DEFAULT_SEED = 0
DEFAULT_ITERATIONS = 10_000


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be greater than or equal to 0")
    return parsed


def _positive_integer(value: str) -> int:
    parsed = _non_negative_integer(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be greater than or equal to 1")
    return parsed


def _artifact_directory(root: Path, forecast: Forecast) -> Path:
    if root.exists() and not root.is_dir():
        raise ValueError("output path conflicts with an existing file")
    return root / forecast.tournament_id / forecast.focus_team_id


def _print_probability_summary(forecast: Forecast) -> None:
    focus_name = forecast.team_display_names.get(
        forecast.focus_team_id,
        forecast.focus_team_id,
    )
    print(f"Forecast for {focus_name}")
    for stage_id in forecast.stage_order:
        probability = forecast.stage_probabilities[stage_id]
        print(f"  {stage_id}: {probability:.1%}")
    print(f"  championship: {forecast.championship_probability:.1%}")


def _print_artifacts(paths: Sequence[Path], current: Path) -> None:
    print("Artifacts (immutable generation):")
    for path in paths:
        print(f"  {path}")
    print("Current alias:")
    print(f"  {current}")


def _print_next_commands() -> None:
    print("Next commands:")
    print("  tournament-forecast presets list")
    print("  tournament-forecast init my-tournament --template group-knockout")
    print("  tournament-forecast validate --config my-tournament/tournament.json")
    print(
        "  tournament-forecast simulate --config my-tournament/tournament.json "
        "--focus-team bravo-town"
    )


def _simulate(
    tournament: Tournament,
    *,
    focus_team_id: str | None,
    seed: int,
    iterations: int,
) -> Forecast:
    return simulate_tournament(
        tournament,
        focus_team_id=focus_team_id,
        options=SimulationOptions(seed=seed, iterations=iterations),
    )


def _run_quickstart(arguments: argparse.Namespace) -> int:
    tournament = load_bundled_preset("synthetic-cup")
    forecast = _simulate(
        tournament,
        focus_team_id=None,
        seed=arguments.seed,
        iterations=arguments.iterations,
    )
    paths = write_report_bundle(
        forecast,
        _artifact_directory(arguments.output_dir, forecast),
    )
    _print_probability_summary(forecast)
    _print_artifacts(tuple(paths), paths.current)
    _print_next_commands()
    return 0


def _run_init(arguments: argparse.Namespace) -> int:
    destination: Path = arguments.directory
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"destination already exists: {destination}")
    config_path = copy_template(arguments.template, destination)
    print(f"Created tournament template: {config_path}")
    print(f"  tournament-forecast validate --config {config_path}")
    print(f"  tournament-forecast simulate --config {config_path}")
    return 0


def _run_validate(arguments: argparse.Namespace) -> int:
    tournament = load_tournament(arguments.config)
    print(
        f"Valid tournament: {tournament.display_name} "
        f"({len(tournament.teams)} teams, {len(tournament.stages)} stages)"
    )
    return 0


def _run_simulate(arguments: argparse.Namespace) -> int:
    tournament = load_tournament(arguments.config)
    forecast = _simulate(
        tournament,
        focus_team_id=arguments.focus_team,
        seed=arguments.seed,
        iterations=arguments.iterations,
    )
    paths = write_report_bundle(
        forecast,
        _artifact_directory(arguments.output_dir, forecast),
    )
    _print_probability_summary(forecast)
    _print_artifacts(tuple(paths), paths.current)
    return 0


def _run_report(arguments: argparse.Namespace) -> int:
    forecast = load_forecast(arguments.forecast)
    paths = write_rendered_reports(
        forecast,
        _artifact_directory(arguments.output_dir, forecast),
    )
    _print_artifacts(tuple(paths), paths.current)
    return 0


def _run_doctor(_arguments: argparse.Namespace) -> int:
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 or newer is required")
    print(f"Python 3.11+: OK ({sys.version_info.major}.{sys.version_info.minor})")

    presets = list_bundled_presets()
    templates = list_bundled_templates()
    if "synthetic-cup" not in presets or not templates:
        raise ValueError("required package resources are unavailable")
    with resource_path("schemas", "forecast.schema.json") as schema:
        if not schema.is_file() or schema.stat().st_size == 0:
            raise ValueError("forecast schema resource is unavailable")
    print(
        f"Package resources: OK ({len(presets)} presets, {len(templates)} templates)"
    )

    with tempfile.TemporaryDirectory(
        prefix=".tournament-forecast-doctor-",
        dir=Path.cwd(),
    ) as temporary_name:
        probe = Path(temporary_name) / "write-probe.txt"
        atomic_write_text(probe, "ok\n")
        if probe.read_text(encoding="utf-8") != "ok\n":
            raise ValueError("output write verification failed")
    print("Writable output: OK")
    print("Optional providers: not required for offline commands")
    return 0


def _run_presets_list(_arguments: argparse.Namespace) -> int:
    for name in list_bundled_presets():
        print(name)
    return 0


def _run_update_results(arguments: argparse.Namespace) -> int:
    source_format = arguments.format
    if source_format == "auto":
        source_format = arguments.source.suffix.casefold().removeprefix(".")
    preview = preview_results(arguments.config, arguments.source, format=source_format)
    print("Results import preview")
    print(f"  additions: {len(preview.additions)}")
    print(f"  idempotent: {len(preview.idempotent)}")
    print(f"  conflicts: {len(preview.conflicts)}")
    print(f"  unmatched: {len(preview.unmatched)}")
    for conflict in preview.conflicts:
        existing = conflict.existing
        incoming = conflict.incoming
        print(f"  conflict {incoming.match_id} leg {incoming.leg}:")
        print(
            f"    existing: {existing.home_team_id} "
            f"{existing.home_score}-{existing.away_score} {existing.away_team_id}; "
            f"winner: {existing.winner_team_id or 'none'}"
        )
        print(
            f"    incoming: {incoming.home_team_id} "
            f"{incoming.home_score}-{incoming.away_score} {incoming.away_team_id}; "
            f"winner: {incoming.winner_team_id or 'none'}"
        )
        print(f"    reason: {conflict.reason}")
    for issue in preview.unmatched:
        print(f"  unmatched row {issue.row_number}: {issue.reason}")
    if not arguments.apply:
        print("Preview only; pass --apply to mutate the tournament config.")
        return 0
    apply_results(
        arguments.config,
        preview,
        replace_conflicts=arguments.replace_conflicts,
    )
    print(
        f"Applied results: {len(preview.additions)} addition(s), "
        f"{len(preview.conflicts) if arguments.replace_conflicts else 0} replacement(s)."
    )
    return 0


def _run_update_odds(arguments: argparse.Namespace) -> int:
    preview = preview_odds(arguments.source)
    print("Odds preview (provenance only; deterministic probabilities are unchanged)")
    print(f"  provider: {preview.provenance.provider}")
    print(f"  retrieved_at: {preview.provenance.retrieved_at}")
    print(f"  records: {len(preview.records)}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tournament-forecast",
        description="Validate, simulate, and report tournament forecasts offline.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    quickstart = commands.add_parser(
        "quickstart",
        help="generate a complete offline synthetic forecast",
    )
    quickstart.add_argument("--output-dir", type=Path, default=Path("outputs"))
    quickstart.add_argument("--seed", type=_non_negative_integer, default=DEFAULT_SEED)
    quickstart.add_argument(
        "--iterations",
        type=_positive_integer,
        default=DEFAULT_ITERATIONS,
    )
    quickstart.set_defaults(handler=_run_quickstart)

    initialize = commands.add_parser(
        "init",
        help="copy a complete tournament template",
    )
    initialize.add_argument("directory", type=Path)
    initialize.add_argument("--template", required=True)
    initialize.set_defaults(handler=_run_init)

    validate = commands.add_parser(
        "validate",
        help="validate a tournament configuration without simulation",
    )
    validate.add_argument("--config", type=Path, required=True)
    validate.set_defaults(handler=_run_validate)

    simulate = commands.add_parser(
        "simulate",
        help="simulate a validated tournament offline",
    )
    simulate.add_argument("--config", type=Path, required=True)
    simulate.add_argument("--focus-team")
    simulate.add_argument("--seed", type=_non_negative_integer, default=DEFAULT_SEED)
    simulate.add_argument(
        "--iterations",
        type=_positive_integer,
        default=DEFAULT_ITERATIONS,
    )
    simulate.add_argument("--output-dir", type=Path, default=Path("outputs"))
    simulate.set_defaults(handler=_run_simulate)

    report = commands.add_parser(
        "report",
        help="render Markdown and SVG from a forecast JSON artifact",
    )
    report.add_argument("--forecast", type=Path, required=True)
    report.add_argument("--output-dir", type=Path, default=Path("outputs"))
    report.set_defaults(handler=_run_report)

    doctor = commands.add_parser(
        "doctor",
        help="check the local offline runtime and package resources",
    )
    doctor.set_defaults(handler=_run_doctor)

    presets = commands.add_parser("presets", help="inspect bundled tournament presets")
    preset_commands = presets.add_subparsers(dest="presets_command", required=True)
    preset_list = preset_commands.add_parser("list", help="list bundled preset names")
    preset_list.set_defaults(handler=_run_presets_list)

    update_results = commands.add_parser(
        "update-results",
        help="preview or apply a local JSON/CSV results import",
    )
    update_results.add_argument("--config", type=Path, required=True)
    update_results.add_argument("--source", type=Path, required=True)
    update_results.add_argument("--format", choices=("auto", "json", "csv"), default="auto")
    update_results.add_argument("--apply", action="store_true")
    update_results.add_argument(
        "--replace-conflicts",
        action="store_true",
        help="explicitly replace conflict-visible completed facts during apply",
    )
    update_results.set_defaults(handler=_run_update_results)

    update_odds = commands.add_parser(
        "update-odds",
        help="validate and inspect local odds provenance without mutating core state",
    )
    update_odds.add_argument("--source", type=Path, required=True)
    update_odds.set_defaults(handler=_run_update_odds)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Tournament Forecaster CLI and return a process exit code."""

    parser = _build_parser()
    arguments = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = arguments.handler
    try:
        return handler(arguments)
    except (TournamentValidationError, OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
