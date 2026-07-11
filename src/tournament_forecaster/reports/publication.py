"""Owned immutable generations and atomic public report pointers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from xml.etree import ElementTree

from ..atomic_io import atomic_write_text
from ..domain import Forecast
from ..errors import TournamentValidationError
from .bracket_svg import render_bracket_svg
from .json_report import forecast_from_document, load_forecast, render_json_report
from .markdown_report import render_markdown_report


_ARTIFACT_NAMES = ("forecast.json", "report.md", "bracket.svg")
_CONTROL_NAME = ".tournament-forecast"
_OWNER_NAME = "owner.json"
_OWNER = "tournament-forecaster"
_LAYOUT_VERSION = 1
_GENERATION_NAME = re.compile(
    r"v[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{16}\Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Stable public paths produced by a complete forecast generation."""

    json: Path
    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.json, self.markdown, self.svg))


@dataclass(frozen=True, slots=True)
class RenderedReportPaths:
    """Stable public rendered paths backed by a complete forecast generation."""

    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.markdown, self.svg))


@dataclass(frozen=True, slots=True)
class _Layout:
    destination: Path
    parent: Path
    control: Path
    focus: Path
    generations: Path
    metadata: Path
    staging: Path
    pointers: Path


@dataclass(frozen=True, slots=True)
class _StagingOrphan:
    directory: Path | None
    marker: Path
    generation: str


@dataclass(frozen=True, slots=True)
class _Recovery:
    staging: tuple[_StagingOrphan, ...] = ()
    pointers: tuple[Path, ...] = ()


def _json_text(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _control_owner() -> dict[str, object]:
    return {"layout_version": _LAYOUT_VERSION, "owner": _OWNER}


def _focus_owner(name: str) -> dict[str, object]:
    return {
        "focus": name,
        "layout_version": _LAYOUT_VERSION,
        "owner": _OWNER,
    }


def _generation_metadata(generation: str, digest: str) -> dict[str, object]:
    return {
        "digest": digest,
        "generation": generation,
        "layout_version": _LAYOUT_VERSION,
        "owner": _OWNER,
        "state": "complete",
    }


def _staging_metadata(generation: str, digest: str) -> dict[str, object]:
    return {
        "digest": digest,
        "generation": generation,
        "layout_version": _LAYOUT_VERSION,
        "owner": _OWNER,
        "state": "staging",
    }


def _layout(destination: Path) -> _Layout:
    parent = destination.parent
    control = parent / _CONTROL_NAME
    focus = control / destination.name
    return _Layout(
        destination=destination,
        parent=parent,
        control=control,
        focus=focus,
        generations=focus / "generations",
        metadata=focus / "metadata",
        staging=focus / "staging",
        pointers=focus / "pointers",
    )


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _read_owned_json(path: Path, expected: dict[str, object], message: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{message}: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{message}: {path}") from error
    if document != expected:
        raise ValueError(f"{message}: {path}")


def _read_metadata(path: Path, *, state: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"stale or unowned report state: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"stale or unowned report state: {path}") from error
    generation = document.get("generation") if isinstance(document, dict) else None
    digest = document.get("digest") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or document.get("owner") != _OWNER
        or document.get("layout_version") != _LAYOUT_VERSION
        or document.get("state") != state
        or not isinstance(generation, str)
        or _GENERATION_NAME.fullmatch(generation) is None
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
    ):
        raise ValueError(f"stale or unowned report state: {path}")
    return document


def _validate_parent(layout: _Layout) -> None:
    current = layout.parent
    while not _lexists(current):
        parent = current.parent
        if parent == current:
            break
        current = parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError(
            f"report parent conflicts with a file or symlink not owned by Tournament Forecaster: {current}"
        )


def _artifact_digest(files: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in _ARTIFACT_NAMES:
        digest.update(name.encode("ascii"))
        digest.update(b"\0")
        digest.update(files[name].encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _directory_digest(directory: Path) -> str:
    return _artifact_digest(
        {
            name: (directory / name).read_text(encoding="utf-8")
            for name in _ARTIFACT_NAMES
        }
    )


def _validate_rendered_files(files: dict[str, str], forecast: Forecast) -> None:
    if set(files) != set(_ARTIFACT_NAMES) or any(not files[name] for name in _ARTIFACT_NAMES):
        raise TournamentValidationError(
            "report generation must contain three non-empty artifacts"
        )
    try:
        document = json.loads(files["forecast.json"])
        loaded = forecast_from_document(document)
    except (json.JSONDecodeError, TournamentValidationError, TypeError) as error:
        raise TournamentValidationError(
            "forecast.json must contain the validated forecast generation"
        ) from error
    if loaded.to_dict() != forecast.to_dict():
        raise TournamentValidationError(
            "forecast.json must contain the validated forecast generation"
        )
    try:
        ElementTree.fromstring(files["bracket.svg"])
    except (ElementTree.ParseError, ValueError) as error:
        raise TournamentValidationError("bracket.svg must be valid XML") from error


def _validate_generation(
    directory: Path,
    metadata_path: Path,
    *,
    expected_digest: str | None = None,
) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"stale or unowned report state: {directory}")
    entries = {path.name for path in directory.iterdir()}
    if entries != set(_ARTIFACT_NAMES):
        raise ValueError(f"stale or unowned report state: {directory}")
    for name in _ARTIFACT_NAMES:
        artifact = directory / name
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0:
            raise ValueError(f"stale or unowned report state: {artifact}")
    metadata = _read_metadata(metadata_path, state="complete")
    digest = _directory_digest(directory)
    if (
        metadata["generation"] != directory.name
        or metadata["digest"] != digest
        or (expected_digest is not None and digest != expected_digest)
    ):
        raise ValueError(f"stale or unowned report state: {directory}")
    try:
        load_forecast(directory / "forecast.json")
        ElementTree.parse(directory / "bracket.svg")
    except (OSError, ElementTree.ParseError, TournamentValidationError) as error:
        raise ValueError(f"stale or unowned report state: {directory}") from error


def _scan_staging(layout: _Layout) -> tuple[_StagingOrphan, ...]:
    directories: dict[str, Path] = {}
    markers: dict[str, Path] = {}
    for path in layout.staging.iterdir():
        if path.is_symlink():
            raise ValueError(f"stale or unowned report state: {path}")
        if path.is_dir():
            directories[path.name] = path
        elif path.is_file() and path.suffix == ".json":
            markers[path.stem] = path
        else:
            raise ValueError(f"stale or unowned report state: {path}")
    if set(directories) - set(markers):
        name = sorted(set(directories) - set(markers))[0]
        raise ValueError(f"stale or unowned report state: {directories[name]}")

    orphans: list[_StagingOrphan] = []
    for token, marker in markers.items():
        document = _read_metadata(marker, state="staging")
        directory = directories.get(token)
        if directory is not None:
            entries = {path.name for path in directory.iterdir()}
            if not entries <= set(_ARTIFACT_NAMES):
                raise ValueError(f"stale or unowned report state: {directory}")
            if any(path.is_symlink() or not path.is_file() for path in directory.iterdir()):
                raise ValueError(f"stale or unowned report state: {directory}")
        orphans.append(
            _StagingOrphan(
                directory=directory,
                marker=marker,
                generation=str(document["generation"]),
            )
        )
    return tuple(orphans)


def _scan_pointers(layout: _Layout, known_generations: set[str]) -> tuple[Path, ...]:
    pointers: list[Path] = []
    expected_parent = Path(_CONTROL_NAME) / layout.destination.name / "generations"
    for path in layout.pointers.iterdir():
        if not path.is_symlink():
            raise ValueError(f"stale or unowned report state: {path}")
        target = os.readlink(path)
        target_path = Path(target)
        if (
            target_path.is_absolute()
            or ".." in target_path.parts
            or target_path.parent != expected_parent
            or target_path.name not in known_generations
        ):
            raise ValueError(f"stale or unowned report state: {path}")
        pointers.append(path)
    return tuple(pointers)


def _validate_public_pointer(layout: _Layout, known_generations: set[str]) -> None:
    if not _lexists(layout.destination):
        return
    if not layout.destination.is_symlink():
        raise ValueError(
            f"report destination is not owned by Tournament Forecaster: {layout.destination}"
        )
    target = os.readlink(layout.destination)
    expected_prefix = f"{_CONTROL_NAME}/{layout.destination.name}/generations/"
    target_path = Path(target)
    if (
        target_path.is_absolute()
        or ".." in target_path.parts
        or not target.startswith(expected_prefix)
        or target_path.name not in known_generations
    ):
        raise ValueError(
            f"report destination is not owned by Tournament Forecaster: {layout.destination}"
        )
    expected = layout.generations / target_path.name
    try:
        resolved = layout.destination.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"stale or unowned report state: {layout.destination}") from error
    if resolved != expected.resolve(strict=True):
        raise ValueError(
            f"report destination is not owned by Tournament Forecaster: {layout.destination}"
        )


def _preflight(layout: _Layout) -> _Recovery:
    _validate_parent(layout)
    control_exists = _lexists(layout.control)
    if not control_exists:
        if _lexists(layout.destination):
            raise ValueError(
                f"report destination is not owned by Tournament Forecaster: {layout.destination}"
            )
        return _Recovery()
    if layout.control.is_symlink() or not layout.control.is_dir():
        raise ValueError(f"stale or unowned report state: {layout.control}")
    _read_owned_json(
        layout.control / _OWNER_NAME,
        _control_owner(),
        "stale or unowned report state",
    )
    for entry in layout.control.iterdir():
        if entry.name == _OWNER_NAME:
            continue
        if entry.is_symlink() or not entry.is_dir():
            raise ValueError(f"stale or unowned report state: {entry}")
    if not _lexists(layout.focus):
        if _lexists(layout.destination):
            raise ValueError(
                f"report destination is not owned by Tournament Forecaster: {layout.destination}"
            )
        return _Recovery()
    if layout.focus.is_symlink() or not layout.focus.is_dir():
        raise ValueError(f"stale or unowned report state: {layout.focus}")
    _read_owned_json(
        layout.focus / _OWNER_NAME,
        _focus_owner(layout.destination.name),
        "stale or unowned report state",
    )
    expected_entries = {_OWNER_NAME, "generations", "metadata", "staging", "pointers"}
    if {entry.name for entry in layout.focus.iterdir()} != expected_entries:
        raise ValueError(f"stale or unowned report state: {layout.focus}")
    for directory in (layout.generations, layout.metadata, layout.staging, layout.pointers):
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError(f"stale or unowned report state: {directory}")

    staging = _scan_staging(layout)
    staged_generations = {orphan.generation for orphan in staging}
    generation_names: set[str] = set()
    for generation in layout.generations.iterdir():
        if generation.is_symlink() or not generation.is_dir():
            raise ValueError(f"stale or unowned report state: {generation}")
        generation_names.add(generation.name)
        _validate_generation(
            generation,
            layout.metadata / f"{generation.name}.json",
        )
    metadata_names: set[str] = set()
    for metadata in layout.metadata.iterdir():
        if metadata.is_symlink() or not metadata.is_file() or metadata.suffix != ".json":
            raise ValueError(f"stale or unowned report state: {metadata}")
        document = _read_metadata(metadata, state="complete")
        if document["generation"] != metadata.stem:
            raise ValueError(f"stale or unowned report state: {metadata}")
        metadata_names.add(metadata.stem)
    if metadata_names - generation_names - staged_generations:
        stale = sorted(metadata_names - generation_names - staged_generations)[0]
        raise ValueError(f"stale or unowned report state: {layout.metadata / (stale + '.json')}")
    if generation_names - metadata_names:
        stale = sorted(generation_names - metadata_names)[0]
        raise ValueError(f"stale or unowned report state: {layout.generations / stale}")
    pointers = _scan_pointers(layout, generation_names)
    _validate_public_pointer(layout, generation_names)
    return _Recovery(staging=staging, pointers=pointers)


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ValueError(
            "atomic report publication requires directory fsync support"
        ) from error
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _initialize_layout(layout: _Layout) -> None:
    layout.parent.mkdir(parents=True, exist_ok=True)
    if not _lexists(layout.control):
        layout.control.mkdir()
        atomic_write_text(layout.control / _OWNER_NAME, _json_text(_control_owner()))
        _fsync_directory(layout.control)
        _fsync_directory(layout.parent)
    if not _lexists(layout.focus):
        layout.focus.mkdir()
        atomic_write_text(
            layout.focus / _OWNER_NAME,
            _json_text(_focus_owner(layout.destination.name)),
        )
        for directory in (
            layout.generations,
            layout.metadata,
            layout.staging,
            layout.pointers,
        ):
            directory.mkdir()
        _fsync_directory(layout.focus)
        _fsync_directory(layout.control)


def _recover(layout: _Layout, recovery: _Recovery) -> None:
    for orphan in recovery.staging:
        if orphan.directory is not None:
            shutil.rmtree(orphan.directory)
        orphan.marker.unlink(missing_ok=True)
        generation = layout.generations / orphan.generation
        if not generation.exists():
            (layout.metadata / f"{orphan.generation}.json").unlink(missing_ok=True)
    for pointer in recovery.pointers:
        pointer.unlink(missing_ok=True)
    if recovery.staging:
        _fsync_directory(layout.staging)
        _fsync_directory(layout.metadata)
    if recovery.pointers:
        _fsync_directory(layout.pointers)


def _generation_name(forecast: Forecast, digest: str) -> str:
    return f"v{forecast.SCHEMA_VERSION}-{forecast.run_id}-{digest[:16]}"


def _promote_staged_generation(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _swap_public_pointer(source: Path, destination: Path) -> None:
    os.replace(source, destination)


def _stage_generation(
    layout: _Layout,
    forecast: Forecast,
    files: dict[str, str],
    generation_name: str,
    digest: str,
) -> Path:
    generation = layout.generations / generation_name
    metadata = layout.metadata / f"{generation_name}.json"
    if _lexists(generation) or _lexists(metadata):
        if not (_lexists(generation) and _lexists(metadata)):
            raise ValueError(f"stale or unowned report state: {generation}")
        _validate_generation(generation, metadata, expected_digest=digest)
        _fsync_directory(generation)
        return generation

    token = uuid.uuid4().hex
    staging = layout.staging / token
    marker = layout.staging / f"{token}.json"
    staging.mkdir()
    atomic_write_text(marker, _json_text(_staging_metadata(generation_name, digest)))
    _fsync_directory(layout.staging)
    for name in _ARTIFACT_NAMES:
        atomic_write_text(staging / name, files[name])
    _fsync_directory(staging)
    _validate_generation_files(staging, forecast, files, digest)
    atomic_write_text(metadata, _json_text(_generation_metadata(generation_name, digest)))
    _fsync_directory(layout.metadata)
    _promote_staged_generation(staging, generation)
    _fsync_directory(layout.generations)
    marker.unlink()
    _fsync_directory(layout.staging)
    _validate_generation(generation, metadata, expected_digest=digest)
    return generation


def _validate_generation_files(
    directory: Path,
    forecast: Forecast,
    files: dict[str, str],
    digest: str,
) -> None:
    entries = {path.name for path in directory.iterdir()}
    if entries != set(_ARTIFACT_NAMES):
        raise TournamentValidationError(
            "report generation must contain exactly three artifacts"
        )
    for name in _ARTIFACT_NAMES:
        artifact = directory / name
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0:
            raise TournamentValidationError(
                "report generation must contain three non-empty regular files"
            )
        if artifact.read_text(encoding="utf-8") != files[name]:
            raise TournamentValidationError("staged report content changed before publication")
    if _directory_digest(directory) != digest:
        raise TournamentValidationError("staged report digest changed before publication")
    loaded = load_forecast(directory / "forecast.json")
    if loaded.to_dict() != forecast.to_dict():
        raise TournamentValidationError("staged forecast changed before publication")
    try:
        ElementTree.parse(directory / "bracket.svg")
    except (ElementTree.ParseError, ValueError) as error:
        raise TournamentValidationError("bracket.svg must be valid XML") from error


def _publish_pointer(layout: _Layout, generation: Path) -> None:
    relative_target = (
        Path(_CONTROL_NAME)
        / layout.destination.name
        / "generations"
        / generation.name
    ).as_posix()
    temporary_pointer = layout.pointers / f"pointer-{uuid.uuid4().hex}"
    try:
        os.symlink(relative_target, temporary_pointer, target_is_directory=True)
        _fsync_directory(layout.pointers)
        known_generations = {
            path.name
            for path in layout.generations.iterdir()
            if path.is_dir() and not path.is_symlink()
        }
        _validate_public_pointer(layout, known_generations)
        _swap_public_pointer(temporary_pointer, layout.destination)
        _fsync_directory(layout.parent)
    except (NotImplementedError, TypeError) as error:
        raise ValueError(
            "atomic report publication is unavailable on this platform"
        ) from error
    finally:
        if temporary_pointer.is_symlink():
            temporary_pointer.unlink()


def _publish_generation(forecast: Forecast, destination: Path) -> None:
    layout = _layout(destination)
    recovery = _preflight(layout)
    files = {
        "forecast.json": render_json_report(forecast),
        "report.md": render_markdown_report(forecast),
        "bracket.svg": render_bracket_svg(forecast),
    }
    _validate_rendered_files(files, forecast)
    digest = _artifact_digest(files)
    generation_name = _generation_name(forecast, digest)

    _initialize_layout(layout)
    recovery = _preflight(layout) if not recovery.staging and not recovery.pointers else recovery
    _recover(layout, recovery)
    clean_recovery = _preflight(layout)
    if clean_recovery.staging or clean_recovery.pointers:
        raise ValueError(f"stale or unowned report state: {layout.focus}")
    generation = _stage_generation(
        layout,
        forecast,
        files,
        generation_name,
        digest,
    )
    _publish_pointer(layout, generation)


def write_report_bundle(forecast: Forecast, destination: Path) -> ReportPaths:
    """Publish one immutable generation through an atomic public pointer swap."""

    _publish_generation(forecast, destination)
    return ReportPaths(
        json=destination / "forecast.json",
        markdown=destination / "report.md",
        svg=destination / "bracket.svg",
    )


def write_rendered_reports(
    forecast: Forecast,
    destination: Path,
) -> RenderedReportPaths:
    """Publish a complete generation while returning the rendered public paths."""

    _publish_generation(forecast, destination)
    return RenderedReportPaths(
        markdown=destination / "report.md",
        svg=destination / "bracket.svg",
    )
