from __future__ import annotations

import json
import importlib.util
from pathlib import Path


def _require_package() -> None:
    assert importlib.util.find_spec("tournament_forecaster") is not None, (
        "the generic tournament_forecaster package does not exist yet"
    )


def test_atomic_write_text_replaces_content_and_creates_parent_directories(tmp_path: Path) -> None:
    _require_package()
    from tournament_forecaster.atomic_io import atomic_write_text

    target = tmp_path / "nested" / "forecast.txt"
    target.parent.mkdir()
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new forecast")

    assert target.read_text(encoding="utf-8") == "new forecast"
    assert list(target.parent.glob(".forecast.txt.*.tmp")) == []


def test_atomic_write_json_serializes_a_mapping_without_temporary_artifacts(tmp_path: Path) -> None:
    _require_package()
    from tournament_forecaster.atomic_io import atomic_write_json

    target = tmp_path / "outputs" / "forecast.json"

    atomic_write_json(target, {"stage_probabilities": {"final": 0.25}, "run_id": "run-0001"})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "run_id": "run-0001",
        "stage_probabilities": {"final": 0.25},
    }
    assert list(target.parent.glob(".forecast.json.*.tmp")) == []
