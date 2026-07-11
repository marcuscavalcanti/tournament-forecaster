"""Access to files packaged with :mod:`tournament_forecaster`."""

from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
import shutil
from typing import Iterator

from .config import load_tournament
from .domain import Tournament


@contextmanager
def resource_path(*parts: str) -> Iterator[Path]:
    """Yield a filesystem path for a package resource, including wheel installs."""

    resource = files("tournament_forecaster").joinpath(*parts)
    with as_file(resource) as path:
        yield path


def _resource_name(name: str) -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("resource name must be non-empty text")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("resource name must not contain a path separator")
    return name


def load_bundled_preset(name: str) -> Tournament:
    """Load a validated tournament preset shipped in the installed package."""

    preset_name = _resource_name(name)
    with resource_path("data", "presets", preset_name, "tournament.json") as path:
        if not path.is_file():
            raise ValueError(f"unknown bundled preset: {preset_name}")
        return load_tournament(path)


def list_bundled_presets() -> tuple[str, ...]:
    """Return the names of complete tournament presets shipped in the package."""

    root = files("tournament_forecaster").joinpath("data", "presets")
    return tuple(
        sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir() and child.joinpath("tournament.json").is_file()
        )
    )


def list_bundled_templates() -> tuple[str, ...]:
    """Return the names of complete scaffold templates shipped in the package."""

    root = files("tournament_forecaster").joinpath("data", "templates")
    return tuple(
        sorted(
            child.name
            for child in root.iterdir()
            if child.is_dir()
            and child.joinpath("tournament.json").is_file()
            and child.joinpath("README.md").is_file()
        )
    )


def copy_template(name: str, destination: Path) -> Path:
    """Copy a complete packaged template directory and return its JSON config path."""

    template_name = _resource_name(name)
    source = files("tournament_forecaster").joinpath("data", "templates", template_name)
    if not source.is_dir():
        raise ValueError(f"unknown bundled template: {template_name}")
    with as_file(source) as source_path:
        shutil.copytree(source_path, destination)
    return destination / "tournament.json"
