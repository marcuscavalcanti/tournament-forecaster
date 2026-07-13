"""Translate deprecated Brazil artifacts at an explicit compatibility boundary."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..errors import TournamentValidationError
from ..reports.json_report import forecast_from_document


_LEGACY_TO_GENERIC_STAGE = {
    "quartas": "quarter-finals",
    "semifinal": "semi-finals",
    "final": "final",
}
_GENERIC_TO_LEGACY_STAGE = {value: key for key, value in _LEGACY_TO_GENERIC_STAGE.items()}


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    mapped: Mapping[str, str]
    defaulted: Mapping[str, object]
    dropped: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "mapped": dict(self.mapped),
            "defaulted": dict(self.defaulted),
            "dropped": list(self.dropped),
        }


@dataclass(frozen=True, slots=True)
class CompatibilityConversion:
    document: dict[str, object]
    report: CompatibilityReport


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise TournamentValidationError(f"{label} must be an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise TournamentValidationError(f"{label} must be an array")
    return value


def _percentage(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TournamentValidationError(f"{label} must be numeric")
    probability = float(value) / 100.0
    if not 0.0 <= probability <= 1.0:
        raise TournamentValidationError(f"{label} must be between 0 and 100")
    return round(probability, 10)


def _legacy_interval(value: object, label: str) -> list[float]:
    bounds = _sequence(value, label)
    if len(bounds) != 2:
        raise TournamentValidationError(f"{label} must contain two bounds")
    return [_percentage(bounds[0], label), _percentage(bounds[1], label)]


def _leaf_paths(value: object, path: str) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        if not value:
            return (path,)
        return tuple(
            leaf
            for key, item in value.items()
            for leaf in _leaf_paths(item, f"{path}.{key}" if path else str(key))
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return (path,)
        return tuple(
            leaf
            for index, item in enumerate(value)
            for leaf in _leaf_paths(item, f"{path}[{index}]")
        )
    return (path,)


def legacy_to_generic(document: Mapping[str, object]) -> CompatibilityConversion:
    """Convert one legacy LinkedIn bundle into a validated v2 forecast document."""

    root = _mapping(document, "legacy artifact")
    bundle = _mapping(root.get("bundle"), "legacy artifact bundle")
    mapped: dict[str, str] = {}
    defaulted: dict[str, object] = {
        "schema_version": 2,
        "run_id": "legacy-worldcup-brazil",
        "tournament_id": "world-cup-2026",
        "focus_team_id": "brazil",
        "matchup_probabilities": [],
        "input_provenance": [],
        "council": None,
    }
    dropped: set[str] = set()

    generated_at = bundle.get("generated_at_iso")
    if not isinstance(generated_at, str) or not generated_at:
        raise TournamentValidationError("legacy bundle generated_at_iso must be non-empty text")
    mapped["bundle.generated_at_iso"] = "generated_at"

    legacy_stages = _mapping(bundle.get("stage_probabilities"), "legacy stage probabilities")
    generic_stages: dict[str, float] = {}
    championship = None
    for legacy_stage, value in legacy_stages.items():
        source_path = f"bundle.stage_probabilities.{legacy_stage}"
        if legacy_stage == "titulo":
            championship = _percentage(value, source_path)
            mapped[source_path] = "championship_probability"
        elif legacy_stage in _LEGACY_TO_GENERIC_STAGE:
            generic_stage = _LEGACY_TO_GENERIC_STAGE[legacy_stage]
            generic_stages[generic_stage] = _percentage(value, source_path)
            mapped[source_path] = f"stage_probabilities.{generic_stage}"
        else:
            dropped.update(_leaf_paths(value, source_path))
    if championship is None:
        raise TournamentValidationError("legacy stage probabilities must contain titulo")

    intervals: dict[str, list[float]] = {}
    intervals_present = "stage_confidence_intervals" in bundle
    raw_intervals = bundle.get("stage_confidence_intervals", {})
    for legacy_stage, value in _mapping(raw_intervals, "legacy confidence intervals").items():
        source_path = f"bundle.stage_confidence_intervals.{legacy_stage}"
        if legacy_stage == "titulo":
            target = "championship_probability"
        elif legacy_stage in _LEGACY_TO_GENERIC_STAGE:
            target = _LEGACY_TO_GENERIC_STAGE[legacy_stage]
        else:
            dropped.update(_leaf_paths(value, source_path))
            continue
        intervals[target] = _legacy_interval(value, source_path)
        mapped[f"{source_path}[0]"] = f"confidence_intervals.{target}[0]"
        mapped[f"{source_path}[1]"] = f"confidence_intervals.{target}[1]"
    if not intervals:
        if intervals_present and not raw_intervals:
            mapped["bundle.stage_confidence_intervals"] = "confidence_intervals"
        else:
            defaulted["confidence_intervals"] = {}

    warnings_present = "warnings" in bundle
    raw_warnings = bundle.get("warnings", [])
    warnings = list(_sequence(raw_warnings, "legacy warnings"))
    if not all(isinstance(warning, str) and warning for warning in warnings):
        raise TournamentValidationError("legacy warnings must contain non-empty text")
    if warnings:
        for index in range(len(warnings)):
            mapped[f"bundle.warnings[{index}]"] = f"warnings[{index}]"
    elif warnings_present:
        mapped["bundle.warnings"] = "warnings"
    else:
        defaulted["warnings"] = []

    consumed_bundle = {
        "generated_at_iso",
        "stage_probabilities",
        "stage_confidence_intervals",
        "warnings",
    }
    for key, value in bundle.items():
        if key not in consumed_bundle:
            dropped.update(_leaf_paths(value, f"bundle.{key}"))
    for key, value in root.items():
        if key != "bundle":
            dropped.update(_leaf_paths(value, key))

    if generic_stages:
        for index, stage_id in enumerate(generic_stages):
            defaulted[f"stage_order[{index}]"] = stage_id
    else:
        defaulted["stage_probabilities"] = {}
        defaulted["stage_order"] = []

    generic: dict[str, object] = {
        "schema_version": 2,
        "run_id": defaulted["run_id"],
        "generated_at": generated_at,
        "tournament_id": defaulted["tournament_id"],
        "focus_team_id": defaulted["focus_team_id"],
        "stage_probabilities": generic_stages,
        "stage_order": list(generic_stages),
        "matchup_probabilities": defaulted["matchup_probabilities"],
        "championship_probability": championship,
        "confidence_intervals": intervals,
        "input_provenance": defaulted["input_provenance"],
        "warnings": warnings,
        "council": defaulted["council"],
    }
    forecast_from_document(generic)
    return CompatibilityConversion(
        document=generic,
        report=CompatibilityReport(mapped, defaulted, tuple(sorted(dropped))),
    )


def generic_to_legacy(document: Mapping[str, object]) -> CompatibilityConversion:
    """Convert a validated v2 forecast document into the legacy bundle envelope."""

    root = _mapping(document, "generic forecast")
    forecast = forecast_from_document(root)
    mapped: dict[str, str] = {
        "generated_at": "bundle.generated_at_iso",
        "championship_probability": "bundle.stage_probabilities.titulo",
    }
    defaulted: dict[str, object] = {"evidence": []}
    dropped: set[str] = set()
    consumed = {
        "generated_at",
        "stage_probabilities",
        "championship_probability",
        "confidence_intervals",
        "warnings",
    }
    for key, value in root.items():
        if key not in consumed:
            dropped.update(_leaf_paths(value, key))

    legacy_stages: dict[str, float] = {}
    if not forecast.stage_probabilities:
        dropped.add("stage_probabilities")
    for stage_id, probability in forecast.stage_probabilities.items():
        source_path = f"stage_probabilities.{stage_id}"
        legacy_stage = _GENERIC_TO_LEGACY_STAGE.get(stage_id)
        if legacy_stage is None:
            dropped.update(_leaf_paths(probability, source_path))
            continue
        legacy_stages[legacy_stage] = round(probability * 100.0, 1)
        mapped[source_path] = f"bundle.stage_probabilities.{legacy_stage}"
    legacy_stages["titulo"] = round(forecast.championship_probability * 100.0, 1)

    legacy_intervals: dict[str, list[float]] = {}
    if not forecast.confidence_intervals:
        mapped["confidence_intervals"] = "bundle.stage_confidence_intervals"
    for interval_id, bounds in forecast.confidence_intervals.items():
        source_path = f"confidence_intervals.{interval_id}"
        if interval_id == "championship_probability":
            legacy_stage = "titulo"
        else:
            legacy_stage = _GENERIC_TO_LEGACY_STAGE.get(interval_id)
        if legacy_stage is None:
            dropped.update(_leaf_paths(bounds, source_path))
            continue
        legacy_intervals[legacy_stage] = [round(bounds[0] * 100.0, 1), round(bounds[1] * 100.0, 1)]
        mapped[f"{source_path}[0]"] = (
            f"bundle.stage_confidence_intervals.{legacy_stage}[0]"
        )
        mapped[f"{source_path}[1]"] = (
            f"bundle.stage_confidence_intervals.{legacy_stage}[1]"
        )
    if forecast.confidence_intervals and not legacy_intervals:
        defaulted["bundle.stage_confidence_intervals"] = {}

    if forecast.warnings:
        for index in range(len(forecast.warnings)):
            mapped[f"warnings[{index}]"] = f"bundle.warnings[{index}]"
    else:
        mapped["warnings"] = "bundle.warnings"

    legacy: dict[str, object] = {
        "bundle": {
            "generated_at_iso": forecast.generated_at,
            "stage_probabilities": legacy_stages,
            "stage_confidence_intervals": legacy_intervals,
            "warnings": list(forecast.warnings),
        },
        "evidence": [],
    }
    return CompatibilityConversion(
        document=legacy,
        report=CompatibilityReport(mapped, defaulted, tuple(sorted(dropped))),
    )
