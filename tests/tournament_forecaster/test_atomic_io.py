from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


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


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), float("-inf"), float("1e999")],
    ids=["nan", "positive-infinity", "negative-infinity", "overflow"],
)
def test_atomic_write_json_rejects_non_finite_numbers_without_touching_target(
    tmp_path: Path,
    value: float,
) -> None:
    _require_package()
    from tournament_forecaster.atomic_io import atomic_write_json

    target = tmp_path / "forecast.json"
    target.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON compliant"):
        atomic_write_json(target, {"championship_probability": value})

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".forecast.json.*.tmp")) == []


@pytest.mark.parametrize("failure_point", ["write", "fsync", "replace"])
def test_atomic_write_failure_preserves_original_and_cleans_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    _require_package()
    import tournament_forecaster.atomic_io as atomic_io

    target = tmp_path / "forecast.txt"
    target.write_text("original", encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError(f"{failure_point} failed")

    if failure_point == "write":
        real_fdopen = atomic_io.os.fdopen

        class WriteFailingHandle:
            def __init__(self, handle: object) -> None:
                self.handle = handle

            def __enter__(self) -> "WriteFailingHandle":
                self.handle.__enter__()  # type: ignore[attr-defined]
                return self

            def __exit__(self, *args: object) -> object:
                return self.handle.__exit__(*args)  # type: ignore[attr-defined]

            def write(self, text: str) -> None:
                fail(text)

        def failing_fdopen(*args: object, **kwargs: object) -> WriteFailingHandle:
            return WriteFailingHandle(real_fdopen(*args, **kwargs))  # type: ignore[arg-type]

        monkeypatch.setattr(atomic_io.os, "fdopen", failing_fdopen)
    elif failure_point == "fsync":
        monkeypatch.setattr(atomic_io.os, "fsync", fail)
    else:
        monkeypatch.setattr(atomic_io.os, "replace", fail)

    with pytest.raises(OSError, match=f"{failure_point} failed"):
        atomic_io.atomic_write_text(target, "replacement")

    assert target.read_text(encoding="utf-8") == "original"
    assert list(tmp_path.glob(".forecast.txt.*.tmp")) == []
