from __future__ import annotations

from collections.abc import Mapping, Sequence

from tournament_forecaster.compatibility.worldcup_brazil import (
    CompatibilityConversion,
    generic_to_legacy,
    legacy_to_generic,
)


def _leaf_inventory(value: object, path: str = "") -> dict[str, object]:
    if isinstance(value, Mapping):
        if not value:
            return {path: value}
        leaves: dict[str, object] = {}
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            leaves.update(_leaf_inventory(item, child))
        return leaves
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return {path: value}
        leaves = {}
        for index, item in enumerate(value):
            leaves.update(_leaf_inventory(item, f"{path}[{index}]"))
        return leaves
    return {path: value}


def _assert_leaf_complete_ledger(
    source: Mapping[str, object],
    conversion: CompatibilityConversion,
) -> None:
    source_leaves = _leaf_inventory(source)
    mapped_sources = set(conversion.report.mapped)
    dropped_sources = set(conversion.report.dropped)
    assert mapped_sources.isdisjoint(dropped_sources)
    assert mapped_sources | dropped_sources == set(source_leaves)

    target_leaves = _leaf_inventory(conversion.document)
    mapped_targets = set(conversion.report.mapped.values())
    defaulted_targets = set(conversion.report.defaulted)
    assert len(mapped_targets) == len(conversion.report.mapped)
    assert mapped_targets.isdisjoint(defaulted_targets)
    assert mapped_targets | defaulted_targets == set(target_leaves)
    for path, value in conversion.report.defaulted.items():
        assert target_leaves[path] == value


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
    assert "evidence[0].name" in conversion.report.dropped
    _assert_leaf_complete_ledger(legacy, conversion)


def test_absent_legacy_warnings_are_defaulted_and_never_mapped() -> None:
    legacy = {
        "bundle": {
            "generated_at_iso": "2026-07-11T12:00:00+00:00",
            "stage_probabilities": {"titulo": 11.7},
        }
    }

    conversion = legacy_to_generic(legacy)

    assert conversion.document["warnings"] == []
    assert conversion.report.defaulted["warnings"] == []
    assert not any(path.startswith("bundle.warnings") for path in conversion.report.mapped)
    _assert_leaf_complete_ledger(legacy, conversion)


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
    _assert_leaf_complete_ledger(generic, conversion)


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
