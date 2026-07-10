from __future__ import annotations

import json
import importlib.util
import tomllib
from pathlib import Path


def _require_package() -> None:
    assert importlib.util.find_spec("tournament_forecaster") is not None, (
        "the generic tournament_forecaster package does not exist yet"
    )


def test_schema_resources_are_available_through_the_installed_package() -> None:
    _require_package()
    from tournament_forecaster.resources import resource_path

    for filename in ("tournament.schema.json", "forecast.schema.json"):
        with resource_path("schemas", filename) as path:
            schema = json.loads(path.read_text(encoding="utf-8"))

        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["properties"]["schema_version"]["const"] == 2


def test_hatchling_packages_generic_and_legacy_surfaces() -> None:
    repository_root = Path(__file__).parents[2]
    pyproject = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["build-system"] == {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }
    assert pyproject["project"]["name"] == "tournament-forecaster"
    assert pyproject["project"]["scripts"] == {
        "tournament-forecast": "tournament_forecaster.cli:main",
        "worldcup-brazil-report": "worldcup_brazil.cli:main",
    }
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/tournament_forecaster",
        "worldcup_brazil",
    ]
