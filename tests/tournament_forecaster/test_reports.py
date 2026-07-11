from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tournament_forecaster.domain import Forecast, MatchupProbability
from tournament_forecaster.errors import TournamentValidationError


def _forecast() -> Forecast:
    return Forecast(
        run_id="run-report-0001",
        generated_at="2026-07-11T12:00:00+00:00",
        tournament_id="synthetic-cup",
        focus_team_id="north-city",
        stage_probabilities={
            "group-stage": 1.0,
            "semi-finals": 0.72,
            "final": 0.41,
        },
        matchup_probabilities=(
            MatchupProbability("semi-finals", "river-town", 0.36),
            MatchupProbability("final", "east-city", 0.21),
        ),
        championship_probability=0.27,
        confidence_intervals={
            "group-stage": (1.0, 1.0),
            "semi-finals": (0.68, 0.76),
            "final": (0.37, 0.45),
            "championship_probability": (0.23, 0.31),
        },
        input_provenance=({"kind": "preset", "name": "Synthetic Cup"},),
        warnings=(),
        tournament_display_name="Synthetic <Cup> & Friends",
        team_display_names={
            "north-city": "North <City> & Co",
            "river-town": "River Town",
            "east-city": "East City",
        },
        simulation={"seed": 17, "iterations": 250, "confidence_level": 0.95},
    )


def test_report_bundle_is_complete_versioned_and_self_contained(tmp_path: Path) -> None:
    from tournament_forecaster.reports import write_report_bundle

    paths = write_report_bundle(_forecast(), tmp_path)

    assert paths.json == tmp_path / "forecast.json"
    assert paths.markdown == tmp_path / "report.md"
    assert paths.svg == tmp_path / "bracket.svg"
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)

    document = json.loads(paths.json.read_text(encoding="utf-8"))
    assert document["schema_version"] == Forecast.SCHEMA_VERSION
    assert document["run_id"] == "run-report-0001"
    assert document["simulation"] == {
        "confidence_level": 0.95,
        "iterations": 250,
        "seed": 17,
    }

    markdown = paths.markdown.read_text(encoding="utf-8")
    assert "# Synthetic <Cup> & Friends forecast" in markdown
    assert "North <City> & Co" in markdown
    assert "27.0%" in markdown
    assert "/Users/" not in markdown

    svg_text = paths.svg.read_text(encoding="utf-8")
    root = ElementTree.fromstring(svg_text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["viewBox"] == "0 0 960 540"
    assert "North &lt;City&gt; &amp; Co" in svg_text
    assert "<script" not in svg_text.lower()
    assert "href=" not in svg_text.lower()
    assert "/Users/" not in svg_text


def test_report_bundle_rolls_back_when_a_commit_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports as reports

    old = {
        "forecast.json": "old-json\n",
        "report.md": "old-markdown\n",
        "bracket.svg": "old-svg\n",
    }
    for name, content in old.items():
        (tmp_path / name).write_text(content, encoding="utf-8")

    real_replace = reports._replace_staged_file
    calls = 0

    def fail_on_second(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(reports, "_replace_staged_file", fail_on_second)

    with pytest.raises(OSError, match="injected replacement failure"):
        reports.write_report_bundle(_forecast(), tmp_path)

    assert {
        name: (tmp_path / name).read_text(encoding="utf-8")
        for name in old
    } == old
    assert not list(tmp_path.glob(".*.tmp"))
    assert not list(tmp_path.glob(".*.backup"))


def test_forecast_loader_rejects_non_finite_and_unsupported_documents(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports.json_report import load_forecast

    valid = _forecast().to_dict()

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(
        json.dumps({**valid, "schema_version": Forecast.SCHEMA_VERSION + 1}),
        encoding="utf-8",
    )
    with pytest.raises(TournamentValidationError, match="unsupported forecast schema version"):
        load_forecast(unsupported)

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text(
        json.dumps(valid).replace('"championship_probability": 0.27', '"championship_probability": NaN'),
        encoding="utf-8",
    )
    with pytest.raises(TournamentValidationError, match="must be finite"):
        load_forecast(non_finite)
