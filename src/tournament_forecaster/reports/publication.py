"""Owned immutable generations and atomic public report pointers."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator
from xml.etree import ElementTree

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
_CONTROL_INIT_PREFIX = f"{_CONTROL_NAME}.init-"
_FOCUS_INIT_INFIX = ".focus-init-"
_STAGE_INIT_PREFIX = ".stage-init-"
_STAGE_OWNER_NAME = ".stage-owner.json"
_GENERATION_NAME = re.compile(
    r"v[0-9]+-[a-z0-9]+(?:-[a-z0-9]+)*-[0-9a-f]{16}\Z"
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_ATOMIC_TEMP_NAME = re.compile(
    r"\.(?P<target>.+)\.(?P<token>[0-9a-f]{32})\.tmp\Z"
)
_UUID_TOKEN = re.compile(r"[0-9a-f]{32}\Z")
_STAGING_MARKER_TARGET = re.compile(r"[0-9a-f]{32}\.json\Z")
_USE_DIR_FD = os.name != "nt" and hasattr(os, "O_NOFOLLOW") and all(
    function in os.supports_dir_fd
    for function in (os.open, os.mkdir, os.stat, os.rename, os.symlink, os.unlink)
)
_RMTREE_DIR_FD_SAFE = bool(
    getattr(shutil.rmtree, "avoids_symlink_attacks", False)
)


@dataclass(frozen=True, slots=True)
class ReportPaths:
    """Stable public paths produced by a complete forecast generation."""

    generation: Path
    current: Path
    json: Path
    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.json, self.markdown, self.svg))

    @property
    def current_json(self) -> Path:
        return self.current / "forecast.json"

    @property
    def current_markdown(self) -> Path:
        return self.current / "report.md"

    @property
    def current_svg(self) -> Path:
        return self.current / "bracket.svg"


@dataclass(frozen=True, slots=True)
class RenderedReportPaths:
    """Stable public rendered paths backed by a complete forecast generation."""

    generation: Path
    current: Path
    markdown: Path
    svg: Path

    def __iter__(self) -> Iterator[Path]:
        return iter((self.markdown, self.svg))

    @property
    def current_json(self) -> Path:
        return self.current / "forecast.json"

    @property
    def current_markdown(self) -> Path:
        return self.current / "report.md"

    @property
    def current_svg(self) -> Path:
        return self.current / "bracket.svg"


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
    marker: Path | None
    generation: str


@dataclass(frozen=True, slots=True)
class _InitCandidate:
    path: Path
    resumable: bool


@dataclass(frozen=True, slots=True)
class _Recovery:
    staging: tuple[_StagingOrphan, ...] = ()
    pointers: tuple[Path, ...] = ()
    temporary_files: tuple[Path, ...] = ()
    orphan_directories: tuple[Path, ...] = ()


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
    if ".." in destination.parts:
        raise ValueError("report destination must not contain '..' path components")
    if not destination.name or destination.name in {".", _CONTROL_NAME}:
        raise ValueError("report destination must name a focus directory")
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


def _after_filesystem_mutation(_operation: str, _path: Path) -> None:
    """Test seam invoked after each logical filesystem mutation."""


def _record_mutation(operation: str, path: Path) -> None:
    _after_filesystem_mutation(operation, path)


def _absolute_lexical(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_symlink_or_junction(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def _ancestor_error(path: Path) -> ValueError:
    return ValueError(f"report path contains an ancestor symlink or junction: {path}")


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory_chain(path: Path, *, create: bool) -> int | None:
    absolute = _absolute_lexical(path)
    anchor = Path(absolute.anchor)
    descriptor = os.open(anchor, _directory_flags())
    current = anchor
    try:
        for component in absolute.parts[1:]:
            candidate = current / component
            try:
                info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    os.close(descriptor)
                    return None
                os.mkdir(component, dir_fd=descriptor)
                _record_mutation("mkdir-parent", candidate)
                info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
            if _is_symlink_or_junction(info):
                raise _ancestor_error(candidate)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError(
                    f"report parent conflicts with an existing file: {candidate}"
                )
            try:
                next_descriptor = os.open(component, _directory_flags(), dir_fd=descriptor)
            except OSError as error:
                raise ValueError(
                    f"report path changed during access or contains an ancestor "
                    f"symlink or junction: {candidate}"
                ) from error
            opened = os.fstat(next_descriptor)
            if not stat.S_ISDIR(opened.st_mode):
                os.close(next_descriptor)
                raise ValueError(
                    f"report parent conflicts with an existing file: {candidate}"
                )
            os.close(descriptor)
            descriptor = next_descriptor
            current = candidate
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _walk_directory_chain(path: Path, *, create: bool) -> None:
    absolute = _absolute_lexical(path)
    if _USE_DIR_FD:
        descriptor = _open_directory_chain(absolute, create=create)
        if descriptor is not None:
            os.close(descriptor)
        return

    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if not create:
                return
            current.mkdir()
            _record_mutation("mkdir-parent", current)
            info = os.lstat(current)
        if _is_symlink_or_junction(info):
            raise _ancestor_error(current)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"report parent conflicts with an existing file: {current}")


@contextmanager
def _opened_directory(path: Path) -> Iterator[int | None]:
    if not _USE_DIR_FD:
        _walk_directory_chain(path, create=False)
        yield None
        return
    descriptor = _open_directory_chain(path, create=False)
    if descriptor is None:
        raise ValueError(f"report parent no longer exists: {path}")
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"report parent conflicts with an existing file: {path}")
        yield descriptor
    finally:
        os.close(descriptor)


def _atomic_write_owned_text(path: Path, text: str) -> None:
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    if not _USE_DIR_FD:
        temporary_path = path.parent / temporary_name
        descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as fallback_handle:
            _record_mutation("write-temp", temporary_path)
            fallback_handle.write(text)
            fallback_handle.flush()
            os.fsync(fallback_handle.fileno())
        os.replace(temporary_path, path)
        _record_mutation("write", path)
        return
    with _opened_directory(path.parent) as directory_descriptor:
        assert directory_descriptor is not None
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=directory_descriptor,
        )
        handle: IO[str] | None = None
        try:
            handle = os.fdopen(descriptor, "w", encoding="utf-8")
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            _record_mutation("write", path)
            os.fsync(directory_descriptor)
        finally:
            if handle is not None:
                handle.close()
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass


def _mkdir_child(parent: Path, name: str) -> Path:
    child = parent / name
    if _USE_DIR_FD:
        with _opened_directory(parent) as descriptor:
            assert descriptor is not None
            os.mkdir(name, dir_fd=descriptor)
    else:
        child.mkdir()
    _record_mutation("mkdir", child)
    return child


def _rename_owned(source: Path, destination: Path) -> None:
    if _USE_DIR_FD:
        with _opened_directory(source.parent) as source_descriptor:
            with _opened_directory(destination.parent) as destination_descriptor:
                assert source_descriptor is not None and destination_descriptor is not None
                os.rename(
                    source.name,
                    destination.name,
                    src_dir_fd=source_descriptor,
                    dst_dir_fd=destination_descriptor,
                )
    else:
        os.rename(source, destination)
    _record_mutation("rename", destination)


def _replace_owned(source: Path, destination: Path) -> None:
    if _USE_DIR_FD:
        with _opened_directory(source.parent) as source_descriptor:
            with _opened_directory(destination.parent) as destination_descriptor:
                assert source_descriptor is not None and destination_descriptor is not None
                os.replace(
                    source.name,
                    destination.name,
                    src_dir_fd=source_descriptor,
                    dst_dir_fd=destination_descriptor,
                )
    else:
        os.replace(source, destination)


def _unlink_owned(path: Path) -> None:
    if not _lexists(path):
        return
    if _USE_DIR_FD:
        with _opened_directory(path.parent) as descriptor:
            assert descriptor is not None
            os.unlink(path.name, dir_fd=descriptor)
    else:
        path.unlink()
    _record_mutation("unlink", path)


def _symlink_owned(target: str, path: Path) -> None:
    if _USE_DIR_FD:
        with _opened_directory(path.parent) as descriptor:
            assert descriptor is not None
            os.symlink(
                target,
                path.name,
                target_is_directory=True,
                dir_fd=descriptor,
            )
    else:
        os.symlink(target, path, target_is_directory=True)
    _record_mutation("symlink", path)


def _remove_owned_tree(path: Path) -> None:
    if not _lexists(path):
        return
    if _USE_DIR_FD:
        if not _RMTREE_DIR_FD_SAFE:
            raise ValueError(
                "race-resistant report recovery is unavailable on this platform"
            )
        with _opened_directory(path.parent) as descriptor:
            assert descriptor is not None
            info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if _is_symlink_or_junction(info) or not stat.S_ISDIR(info.st_mode):
                raise ValueError(f"stale or unowned report state: {path}")
            shutil.rmtree(path.name, dir_fd=descriptor)
    else:
        info = os.lstat(path)
        if _is_symlink_or_junction(info) or not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"stale or unowned report state: {path}")
        shutil.rmtree(path)
    _record_mutation("rmtree", path)


def _atomic_temp_target(path: Path) -> str | None:
    match = _ATOMIC_TEMP_NAME.fullmatch(path.name)
    if match is None:
        return None
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    if _is_symlink_or_junction(info) or not stat.S_ISREG(info.st_mode):
        return None
    return match.group("target")


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
    _walk_directory_chain(layout.parent, create=False)


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


def _scan_staging(
    layout: _Layout,
) -> tuple[tuple[_StagingOrphan, ...], tuple[Path, ...], tuple[Path, ...]]:
    directories: dict[str, Path] = {}
    init_directories: set[str] = set()
    markers: dict[str, Path] = {}
    temporary_files: list[Path] = []
    for path in layout.staging.iterdir():
        info = os.lstat(path)
        if _is_symlink_or_junction(info):
            raise ValueError(f"stale or unowned report state: {path}")
        if stat.S_ISDIR(info.st_mode):
            if path.name.startswith(_STAGE_INIT_PREFIX):
                token = path.name.removeprefix(_STAGE_INIT_PREFIX)
                if _UUID_TOKEN.fullmatch(token) is None:
                    raise ValueError(f"stale or unowned report state: {path}")
                init_directories.add(path.name)
            elif _UUID_TOKEN.fullmatch(path.name) is None:
                raise ValueError(f"stale or unowned report state: {path}")
            directories[path.name] = path
        elif stat.S_ISREG(info.st_mode) and path.suffix == ".json":
            if _UUID_TOKEN.fullmatch(path.stem) is None:
                raise ValueError(f"stale or unowned report state: {path}")
            markers[path.stem] = path
        elif (
            stat.S_ISREG(info.st_mode)
            and (target := _atomic_temp_target(path)) is not None
            and _STAGING_MARKER_TARGET.fullmatch(target) is not None
        ):
            temporary_files.append(path)
        else:
            raise ValueError(f"stale or unowned report state: {path}")

    orphans: list[_StagingOrphan] = []
    init_orphans: list[Path] = []
    handled_markers: set[str] = set()
    for name, directory in directories.items():
        internal_marker = directory / _STAGE_OWNER_NAME
        sidecar = markers.get(name)
        if name in init_directories and not _lexists(internal_marker):
            entries = list(directory.iterdir())
            if not entries or (
                len(entries) == 1
                and _atomic_temp_target(entries[0]) == _STAGE_OWNER_NAME
            ):
                init_orphans.append(directory)
                continue
            raise ValueError(f"stale or unowned report state: {directory}")
        internal_document = (
            _read_metadata(internal_marker, state="staging")
            if _lexists(internal_marker)
            else None
        )
        sidecar_document = (
            _read_metadata(sidecar, state="staging")
            if sidecar is not None
            else None
        )
        if internal_document is None and sidecar_document is None:
            raise ValueError(f"stale or unowned report state: {directory}")
        if (
            internal_document is not None
            and sidecar_document is not None
            and internal_document != sidecar_document
        ):
            raise ValueError(f"stale or unowned report state: {directory}")
        document = internal_document or sidecar_document
        assert document is not None
        allowed_entries = {*_ARTIFACT_NAMES, _STAGE_OWNER_NAME}
        for entry in directory.iterdir():
            entry_info = os.lstat(entry)
            if _is_symlink_or_junction(entry_info) or not stat.S_ISREG(
                entry_info.st_mode
            ):
                raise ValueError(f"stale or unowned report state: {directory}")
            if entry.name not in allowed_entries:
                target = _atomic_temp_target(entry)
                if target not in allowed_entries:
                    raise ValueError(f"stale or unowned report state: {directory}")
        if sidecar is not None:
            handled_markers.add(name)
        orphans.append(
            _StagingOrphan(
                directory=directory,
                marker=sidecar,
                generation=str(document["generation"]),
            )
        )

    for token, marker in markers.items():
        if token in handled_markers:
            continue
        document = _read_metadata(marker, state="staging")
        orphans.append(
            _StagingOrphan(
                directory=None,
                marker=marker,
                generation=str(document["generation"]),
            )
        )
    return tuple(orphans), tuple(temporary_files), tuple(init_orphans)


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
    control_candidates = _scan_control_init_candidates(layout)
    init_orphans = [candidate.path for candidate in control_candidates]
    control_exists = _lexists(layout.control)
    if not control_exists:
        if _lexists(layout.destination):
            raise ValueError(
                f"report destination is not owned by Tournament Forecaster: {layout.destination}"
            )
        return _Recovery(orphan_directories=tuple(init_orphans))
    if layout.control.is_symlink() or not layout.control.is_dir():
        raise ValueError(f"stale or unowned report state: {layout.control}")
    _read_owned_json(
        layout.control / _OWNER_NAME,
        _control_owner(),
        "stale or unowned report state",
    )
    focus_candidates = _scan_focus_init_candidates(layout)
    init_orphans.extend(candidate.path for candidate in focus_candidates)
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
        return _Recovery(orphan_directories=tuple(init_orphans))
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

    staging, staging_temporary_files, staging_init_orphans = _scan_staging(layout)
    init_orphans.extend(staging_init_orphans)
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
    metadata_temporary_files: list[Path] = []
    for metadata in layout.metadata.iterdir():
        temporary_target = _atomic_temp_target(metadata)
        if temporary_target is not None:
            target_path = Path(temporary_target)
            if (
                target_path.suffix != ".json"
                or _GENERATION_NAME.fullmatch(target_path.stem) is None
            ):
                raise ValueError(f"stale or unowned report state: {metadata}")
            metadata_temporary_files.append(metadata)
            continue
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
    return _Recovery(
        staging=staging,
        pointers=pointers,
        temporary_files=(
            *staging_temporary_files,
            *metadata_temporary_files,
        ),
        orphan_directories=tuple(init_orphans),
    )


def _fsync_directory(path: Path) -> None:
    try:
        with _opened_directory(path) as descriptor:
            if descriptor is not None:
                os.fsync(descriptor)
                return
        fallback_descriptor = os.open(path, _directory_flags())
        try:
            os.fsync(fallback_descriptor)
        finally:
            os.close(fallback_descriptor)
    except OSError as error:
        raise ValueError(
            "atomic report publication requires directory fsync support"
        ) from error


def _owned_json_matches(path: Path, expected: dict[str, object]) -> bool:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    if _is_symlink_or_junction(info) or not stat.S_ISREG(info.st_mode):
        return False
    try:
        return bool(json.loads(path.read_text(encoding="utf-8")) == expected)
    except (OSError, json.JSONDecodeError):
        return False


def _classify_init_candidate(
    entry: Path,
    *,
    parent: Path,
    prefix: str,
    expected_owner: dict[str, object],
    expected_directories: tuple[str, ...],
) -> _InitCandidate:
    token = entry.name.removeprefix(prefix)
    if entry.parent != parent or _UUID_TOKEN.fullmatch(token) is None:
        raise ValueError(f"stale or unowned report state: {entry}")
    try:
        info = os.lstat(entry)
    except FileNotFoundError as error:
        raise ValueError(f"stale or unowned report state: {entry}") from error
    if _is_symlink_or_junction(info) or not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"stale or unowned report state: {entry}")

    children = sorted(entry.iterdir(), key=lambda path: path.name)
    if not children:
        return _InitCandidate(entry, resumable=False)
    for child in children:
        child_info = os.lstat(child)
        if _is_symlink_or_junction(child_info):
            raise ValueError(f"stale or unowned report state: {child}")
    if len(children) == 1 and _atomic_temp_target(children[0]) == _OWNER_NAME:
        return _InitCandidate(entry, resumable=False)

    allowed = {_OWNER_NAME, *expected_directories}
    if {child.name for child in children} - allowed:
        raise ValueError(f"stale or unowned report state: {entry}")
    owner = entry / _OWNER_NAME
    if not _owned_json_matches(owner, expected_owner):
        raise ValueError(f"stale or unowned report state: {owner}")
    for name in expected_directories:
        child = entry / name
        if not _lexists(child):
            continue
        child_info = os.lstat(child)
        if (
            _is_symlink_or_junction(child_info)
            or not stat.S_ISDIR(child_info.st_mode)
            or any(child.iterdir())
        ):
            raise ValueError(f"stale or unowned report state: {child}")
    return _InitCandidate(entry, resumable=True)


def _scan_init_candidates(
    parent: Path,
    *,
    prefix: str,
    expected_owner: dict[str, object],
    expected_directories: tuple[str, ...] = (),
) -> tuple[_InitCandidate, ...]:
    if not _lexists(parent):
        return ()
    candidates: list[_InitCandidate] = []
    for entry in sorted(parent.iterdir(), key=lambda path: path.name):
        if not entry.name.startswith(prefix):
            continue
        candidates.append(
            _classify_init_candidate(
                entry,
                parent=parent,
                prefix=prefix,
                expected_owner=expected_owner,
                expected_directories=expected_directories,
            )
        )
    return tuple(candidates)


def _scan_control_init_candidates(layout: _Layout) -> tuple[_InitCandidate, ...]:
    return _scan_init_candidates(
        layout.parent,
        prefix=_CONTROL_INIT_PREFIX,
        expected_owner=_control_owner(),
    )


def _scan_focus_init_candidates(layout: _Layout) -> tuple[_InitCandidate, ...]:
    return _scan_init_candidates(
        layout.control,
        prefix=_focus_init_prefix(layout),
        expected_owner=_focus_owner(layout.destination.name),
        expected_directories=("generations", "metadata", "staging", "pointers"),
    )


def _select_init_candidate(
    candidates: tuple[_InitCandidate, ...],
    parent: Path,
) -> Path | None:
    selected = next((candidate for candidate in candidates if candidate.resumable), None)
    removed = False
    for candidate in candidates:
        if candidate != selected:
            _remove_owned_tree(candidate.path)
            removed = True
    if removed:
        _fsync_directory(parent)
    return selected.path if selected is not None else None


def _control_init_candidate(layout: _Layout) -> Path | None:
    return _select_init_candidate(
        _scan_control_init_candidates(layout),
        layout.parent,
    )


def _install_control(layout: _Layout) -> None:
    candidate = _control_init_candidate(layout)
    if candidate is None:
        candidate = _mkdir_child(
            layout.parent,
            f"{_CONTROL_INIT_PREFIX}{uuid.uuid4().hex}",
        )
        _atomic_write_owned_text(candidate / _OWNER_NAME, _json_text(_control_owner()))
        _fsync_directory(candidate)
    _walk_directory_chain(layout.parent, create=False)
    if _lexists(layout.control):
        raise ValueError(f"stale or unowned report state: {layout.control}")
    _rename_owned(candidate, layout.control)
    _fsync_directory(layout.parent)


def _focus_init_prefix(layout: _Layout) -> str:
    return f".{layout.destination.name}{_FOCUS_INIT_INFIX}"


def _focus_init_candidate(layout: _Layout) -> Path | None:
    return _select_init_candidate(
        _scan_focus_init_candidates(layout),
        layout.control,
    )


def _complete_focus_candidate(layout: _Layout, candidate: Path) -> None:
    expected_directories = ("generations", "metadata", "staging", "pointers")
    allowed = {_OWNER_NAME, *expected_directories}
    unknown = {entry.name for entry in candidate.iterdir()} - allowed
    if unknown:
        raise ValueError(f"stale or unowned report state: {candidate}")
    for name in expected_directories:
        child = candidate / name
        if not _lexists(child):
            _mkdir_child(candidate, name)
        elif child.is_symlink() or not child.is_dir():
            raise ValueError(f"stale or unowned report state: {child}")
        _fsync_directory(child)
    _fsync_directory(candidate)


def _install_focus(layout: _Layout) -> None:
    candidate = _focus_init_candidate(layout)
    if candidate is None:
        candidate = _mkdir_child(
            layout.control,
            f"{_focus_init_prefix(layout)}{uuid.uuid4().hex}",
        )
        _atomic_write_owned_text(
            candidate / _OWNER_NAME,
            _json_text(_focus_owner(layout.destination.name)),
        )
    _complete_focus_candidate(layout, candidate)
    _walk_directory_chain(layout.control, create=False)
    if _lexists(layout.focus):
        raise ValueError(f"stale or unowned report state: {layout.focus}")
    _rename_owned(candidate, layout.focus)
    _fsync_directory(layout.control)


def _initialize_layout(layout: _Layout) -> None:
    _walk_directory_chain(layout.parent, create=True)
    if not _lexists(layout.control):
        _install_control(layout)
    elif layout.control.is_symlink() or not layout.control.is_dir():
        raise ValueError(f"stale or unowned report state: {layout.control}")
    _walk_directory_chain(layout.control, create=False)
    if not _lexists(layout.focus):
        _install_focus(layout)
    elif layout.focus.is_symlink() or not layout.focus.is_dir():
        raise ValueError(f"stale or unowned report state: {layout.focus}")


def _recover(layout: _Layout, recovery: _Recovery) -> None:
    for temporary_file in recovery.temporary_files:
        _unlink_owned(temporary_file)
    orphan_parents: set[Path] = set()
    for orphan_directory in recovery.orphan_directories:
        orphan_parents.add(orphan_directory.parent)
        _remove_owned_tree(orphan_directory)
    for orphan in recovery.staging:
        generation = layout.generations / orphan.generation
        if not generation.exists():
            _unlink_owned(layout.metadata / f"{orphan.generation}.json")
        if orphan.directory is not None:
            _remove_owned_tree(orphan.directory)
        if orphan.marker is not None:
            _unlink_owned(orphan.marker)
    for pointer in recovery.pointers:
        _unlink_owned(pointer)
    if recovery.staging or recovery.temporary_files:
        _fsync_directory(layout.staging)
        _fsync_directory(layout.metadata)
    if recovery.pointers:
        _fsync_directory(layout.pointers)
    for parent in sorted(orphan_parents, key=os.fspath):
        if _lexists(parent):
            _fsync_directory(parent)


def _generation_name(forecast: Forecast, digest: str) -> str:
    return f"v{forecast.SCHEMA_VERSION}-{forecast.run_id}-{digest[:16]}"


def _promote_staged_generation(source: Path, destination: Path) -> None:
    _replace_owned(source, destination)
    _record_mutation("promote-generation", destination)


def _swap_public_pointer(source: Path, destination: Path) -> None:
    _replace_owned(source, destination)
    _record_mutation("swap-pointer", destination)


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
    staging_temporary = _mkdir_child(
        layout.staging,
        f"{_STAGE_INIT_PREFIX}{token}",
    )
    marker = layout.staging / f"{token}.json"
    staging_document = _staging_metadata(generation_name, digest)
    _atomic_write_owned_text(
        staging_temporary / _STAGE_OWNER_NAME,
        _json_text(staging_document),
    )
    for name in _ARTIFACT_NAMES:
        _atomic_write_owned_text(staging_temporary / name, files[name])
    _fsync_directory(staging_temporary)
    _validate_generation_files(
        staging_temporary,
        forecast,
        files,
        digest,
        allow_owner=True,
    )
    _rename_owned(staging_temporary, staging)
    _fsync_directory(layout.staging)
    _atomic_write_owned_text(marker, _json_text(staging_document))
    _atomic_write_owned_text(
        metadata,
        _json_text(_generation_metadata(generation_name, digest)),
    )
    _fsync_directory(layout.metadata)
    _unlink_owned(staging / _STAGE_OWNER_NAME)
    _fsync_directory(staging)
    _validate_generation_files(staging, forecast, files, digest)
    _promote_staged_generation(staging, generation)
    _fsync_directory(layout.generations)
    _unlink_owned(marker)
    _fsync_directory(layout.staging)
    _validate_generation(generation, metadata, expected_digest=digest)
    return generation


def _validate_generation_files(
    directory: Path,
    forecast: Forecast,
    files: dict[str, str],
    digest: str,
    *,
    allow_owner: bool = False,
) -> None:
    entries = {path.name for path in directory.iterdir()}
    expected_entries = set(_ARTIFACT_NAMES)
    if allow_owner:
        expected_entries.add(_STAGE_OWNER_NAME)
    if entries != expected_entries:
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
        _symlink_owned(relative_target, temporary_pointer)
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
            _unlink_owned(temporary_pointer)


def _publish_generation(forecast: Forecast, destination: Path) -> Path:
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
    recovery = (
        _preflight(layout)
        if not recovery.staging
        and not recovery.pointers
        and not recovery.temporary_files
        and not recovery.orphan_directories
        else recovery
    )
    _recover(layout, recovery)
    clean_recovery = _preflight(layout)
    if (
        clean_recovery.staging
        or clean_recovery.pointers
        or clean_recovery.temporary_files
        or clean_recovery.orphan_directories
    ):
        raise ValueError(f"stale or unowned report state: {layout.focus}")
    generation = _stage_generation(
        layout,
        forecast,
        files,
        generation_name,
        digest,
    )
    _publish_pointer(layout, generation)
    return generation.resolve(strict=True)


def write_report_bundle(forecast: Forecast, destination: Path) -> ReportPaths:
    """Publish one immutable generation through an atomic public pointer swap."""

    generation = _publish_generation(forecast, destination)
    return ReportPaths(
        generation=generation,
        current=destination,
        json=generation / "forecast.json",
        markdown=generation / "report.md",
        svg=generation / "bracket.svg",
    )


def write_rendered_reports(
    forecast: Forecast,
    destination: Path,
) -> RenderedReportPaths:
    """Publish a complete generation while returning the rendered public paths."""

    generation = _publish_generation(forecast, destination)
    return RenderedReportPaths(
        generation=generation,
        current=destination,
        markdown=generation / "report.md",
        svg=generation / "bracket.svg",
    )
