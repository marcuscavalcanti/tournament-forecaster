from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[1]


def _run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(REPOSITORY_ROOT / "src"), str(REPOSITORY_ROOT)]
    )
    return subprocess.run(
        [sys.executable, "-m", "tournament_forecaster", *arguments],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )


def _assert_user_error(result: subprocess.CompletedProcess[str], message: str) -> None:
    assert result.returncode == 2
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


def test_quickstart_creates_exactly_three_artifacts_and_prints_next_commands(
    tmp_path: Path,
) -> None:
    result = _run_cli(tmp_path, "quickstart", "--iterations", "40", "--seed", "7")

    assert result.returncode == 0, result.stderr
    output = tmp_path / "outputs" / "synthetic-cup" / "north-city"
    files = sorted(path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file())
    assert files == ["bracket.svg", "forecast.json", "report.md"]
    assert all((output / name).stat().st_size > 0 for name in files)
    assert "Artifacts (immutable generation):" in result.stdout
    assert "Current alias:" in result.stdout
    assert "outputs/synthetic-cup/north-city" in result.stdout
    assert "tournament-forecast presets list" in result.stdout
    assert "tournament-forecast init my-tournament --template group-knockout" in result.stdout
    assert "tournament-forecast validate --config my-tournament/tournament.json" in result.stdout
    assert (
        "tournament-forecast simulate --config my-tournament/tournament.json "
        "--focus-team bravo-town" in result.stdout
    )
    assert (
        result.stdout.index("  group-stage:")
        < result.stdout.index("  semi-finals:")
        < result.stdout.index("  final:")
    )


def test_init_validate_simulate_report_doctor_and_presets_surfaces(tmp_path: Path) -> None:
    destination = tmp_path / "my-tournament"
    initialized = _run_cli(
        tmp_path,
        "init",
        str(destination),
        "--template",
        "group-knockout",
    )
    assert initialized.returncode == 0, initialized.stderr
    assert sorted(path.name for path in destination.iterdir()) == ["README.md", "tournament.json"]
    assert "tournament-forecast validate --config tournament.json" in (
        destination / "README.md"
    ).read_text(encoding="utf-8")

    validated = _run_cli(tmp_path, "validate", "--config", str(destination / "tournament.json"))
    assert validated.returncode == 0, validated.stderr
    assert "Valid tournament:" in validated.stdout

    simulated = _run_cli(
        tmp_path,
        "simulate",
        "--config",
        str(destination / "tournament.json"),
        "--focus-team",
        "alpha-club",
        "--iterations",
        "30",
        "--seed",
        "9",
        "--output-dir",
        str(tmp_path / "simulation-output"),
    )
    assert simulated.returncode == 0, simulated.stderr
    artifact_dir = tmp_path / "simulation-output" / "group-knockout-template" / "alpha-club"
    assert sorted(path.name for path in artifact_dir.iterdir()) == [
        "bracket.svg",
        "forecast.json",
        "report.md",
    ]

    rerendered = _run_cli(
        tmp_path,
        "report",
        "--forecast",
        str(artifact_dir / "forecast.json"),
        "--output-dir",
        str(tmp_path / "rendered"),
    )
    assert rerendered.returncode == 0, rerendered.stderr
    rendered_dir = tmp_path / "rendered" / "group-knockout-template" / "alpha-club"
    assert sorted(path.name for path in rendered_dir.iterdir()) == [
        "bracket.svg",
        "forecast.json",
        "report.md",
    ]

    doctor = _run_cli(tmp_path, "doctor")
    assert doctor.returncode == 0, doctor.stderr
    assert "Python 3.11+" in doctor.stdout
    assert "Package resources" in doctor.stdout
    assert "Writable output" in doctor.stdout
    assert "Optional providers" in doctor.stdout

    presets = _run_cli(tmp_path, "presets", "list")
    assert presets.returncode == 0, presets.stderr
    assert presets.stdout.splitlines() == [
        "champions-league-style",
        "libertadores-style",
        "synthetic-cup",
        "world-cup-style",
    ]


def test_init_refuses_even_an_empty_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()

    result = _run_cli(tmp_path, "init", str(destination), "--template", "group-knockout")

    _assert_user_error(result, "destination already exists")
    assert list(destination.iterdir()) == []


def test_backtest_writes_report_and_returns_nonzero_when_sample_is_insufficient(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.backtest import ratings_sha256

    ratings = {"alpha": 1600.0, "bravo": 1500.0}
    document = {
        "schema_version": 1,
        "model_version": "poisson-elo-v1",
        "home_advantage_rating_points": 0,
        "ratings": ratings,
        "ratings_sha256": ratings_sha256(ratings),
        "cases": [
            {
                "source_id": "official-1",
                "captured_at": "2026-06-09T12:00:00+00:00",
                "kickoff_at": "2026-06-11T12:00:00+00:00",
                "home_team_id": "alpha",
                "away_team_id": "bravo",
                "result": {"home": 1, "away": 0},
            }
        ],
    }
    source = tmp_path / "backtest.json"
    output = tmp_path / "backtest-report.json"
    source.write_text(json.dumps(document), encoding="utf-8")

    result = _run_cli(
        tmp_path,
        "backtest",
        "--input",
        str(source),
        "--output",
        str(output),
        "--min-resolved",
        "2",
    )

    assert result.returncode == 1, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "insufficient"
    assert report["ok"] is False
    assert json.loads(result.stdout) == report


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ("{", "invalid forecast JSON"),
        ('{"schema_version": 999}', "unsupported forecast schema version"),
        ('{"schema_version": 2, "championship_probability": NaN}', "must be finite"),
    ],
)
def test_report_rejects_bad_forecast_input_without_traceback(
    tmp_path: Path,
    document: str,
    message: str,
) -> None:
    forecast = tmp_path / "forecast.json"
    forecast.write_text(document, encoding="utf-8")

    result = _run_cli(tmp_path, "report", "--forecast", str(forecast))

    _assert_user_error(result, message)


def test_report_rejects_output_path_conflict_without_traceback(tmp_path: Path) -> None:
    result = _run_cli(tmp_path, "quickstart", "--iterations", "10")
    assert result.returncode == 0, result.stderr
    forecast = tmp_path / "outputs" / "synthetic-cup" / "north-city" / "forecast.json"

    conflict = tmp_path / "occupied"
    conflict.write_text("not a directory", encoding="utf-8")
    result = _run_cli(
        tmp_path,
        "report",
        "--forecast",
        str(forecast),
        "--output-dir",
        str(conflict),
    )

    _assert_user_error(result, "output path conflicts with an existing file")


def test_quickstart_rejects_a_symlinked_output_ancestor_without_mutation(
    tmp_path: Path,
) -> None:
    real_output = tmp_path / "real-output"
    (real_output / "synthetic-cup").mkdir(parents=True)
    output_alias = tmp_path / "output-alias"
    output_alias.symlink_to(real_output, target_is_directory=True)

    result = _run_cli(
        tmp_path,
        "quickstart",
        "--iterations",
        "10",
        "--output-dir",
        str(output_alias),
    )

    _assert_user_error(result, "ancestor symlink")
    assert list((real_output / "synthetic-cup").iterdir()) == []


def test_validate_does_not_simulate_or_open_a_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tournament_forecaster import cli
    from tournament_forecaster.resources import resource_path

    with resource_path("data", "presets", "synthetic-cup", "tournament.json") as config:
        config_path = Path(config)

        def forbidden(*_args: object, **_kwargs: object) -> object:
            raise AssertionError("validate attempted simulation or network access")

        monkeypatch.setattr(cli, "simulate_tournament", forbidden)
        monkeypatch.setattr("socket.socket.connect", forbidden)
        monkeypatch.setattr("socket.socket.bind", forbidden)

        assert cli.main(["validate", "--config", str(config_path)]) == 0
