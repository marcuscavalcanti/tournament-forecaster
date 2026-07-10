"""Access to files packaged with :mod:`tournament_forecaster`."""

from __future__ import annotations

from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path
from typing import Iterator


@contextmanager
def resource_path(*parts: str) -> Iterator[Path]:
    """Yield a filesystem path for a package resource, including wheel installs."""

    resource = files("tournament_forecaster").joinpath(*parts)
    with as_file(resource) as path:
        yield path
