from __future__ import annotations

from tournament_forecaster.compatibility.worldcup_brazil import (
    generic_to_legacy,
    legacy_to_generic,
)


def test_legacy_to_generic_reports_every_mapped_defaulted_and_dropped_field() -> None:
    legacy = {
        "bundle": {
            "generated_at_iso": "2026-07-11T12:00:00+00:00",
            "stage_probabilities": {"quartas": 72.1, "semifinal": 42.3, "final": 23.5, "titulo": 11.7},
            "stage_confidence_intervals": {"titulo": [8.1, 15.4]},
            "warnings": ["legacy warning"],
            "group_name": "Grupo C",
        },
        "evidence": [{"name": "fixture evidence"}],
    }

    conversion = legacy_to_generic(legacy)

    assert conversion.document["schema_version"] == 2
    assert conversion.document["tournament_id"] == "world-cup-2026"
    assert conversion.document["focus_team_id"] == "brazil"
    assert conversion.document["championship_probability"] == 0.117
    assert conversion.document["stage_probabilities"]["quarter-finals"] == 0.721
    assert conversion.report.mapped["bundle.stage_probabilities.titulo"] == "championship_probability"
    assert conversion.report.defaulted["tournament_id"] == "world-cup-2026"
    assert "bundle.group_name" in conversion.report.dropped
    assert "evidence" in conversion.report.dropped


def test_generic_to_legacy_reports_translation_without_importing_legacy_into_core() -> None:
    generic = {
        "schema_version": 2,
        "run_id": "run-1",
        "generated_at": "2026-07-11T12:00:00+00:00",
        "tournament_id": "world-cup-2026",
        "focus_team_id": "brazil",
        "stage_probabilities": {
            "quarter-finals": 0.721,
            "semi-finals": 0.423,
            "final": 0.235,
        },
        "stage_order": ["quarter-finals", "semi-finals", "final"],
        "matchup_probabilities": [],
        "championship_probability": 0.117,
        "confidence_intervals": {"championship": [0.081, 0.154]},
        "input_provenance": [],
        "warnings": ["generic warning"],
        "council": None,
    }

    conversion = generic_to_legacy(generic)

    bundle = conversion.document["bundle"]
    assert bundle["stage_probabilities"] == {
        "quartas": 72.1,
        "semifinal": 42.3,
        "final": 23.5,
        "titulo": 11.7,
    }
    assert bundle["stage_confidence_intervals"]["titulo"] == [8.1, 15.4]
    assert conversion.report.mapped["championship_probability"] == "bundle.stage_probabilities.titulo"
    assert "run_id" in conversion.report.dropped


def test_generic_package_never_imports_worldcup_brazil() -> None:
    from pathlib import Path
    import tournament_forecaster

    package_root = Path(tournament_forecaster.__file__).parent
    offenders = [
        path
        for path in package_root.rglob("*.py")
        if "worldcup_brazil" in path.read_text(encoding="utf-8")
        and "compatibility/" not in path.as_posix()
    ]

    assert offenders == []
