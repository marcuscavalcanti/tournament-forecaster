"""Preview-first normalization of local JSON and CSV result files."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import os
import secrets
import stat
import unicodedata
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from ..config import load_tournament_document
from ..domain import CompletedMatch, Score, Tournament
from ..errors import TournamentValidationError
from ..group_fixtures import generate_group_fixture_specs
from .security import sanitize_metadata, serializable_value

_fcntl = None
with suppress(ImportError):  # pragma: no cover - unsupported platforms fail closed.
    _fcntl = importlib.import_module("fcntl")

_JSON_ROOT_FIELDS = frozenset({"schema_version", "provider", "retrieved_at", "results"})
_RESULT_FIELDS = frozenset(
    {
        "status",
        "match_id",
        "stage_id",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "leg",
        "winner_team",
        "source_id",
        "metadata",
    }
)
_CSV_PROVENANCE_FIELDS = frozenset({"provider", "retrieved_at"})
_CSV_REQUIRED_FIELDS = frozenset(
    {
        "status",
        "stage_id",
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "provider",
        "retrieved_at",
    }
)
_CSV_SUPPORTED_FIELDS = _RESULT_FIELDS | _CSV_PROVENANCE_FIELDS


@dataclass(frozen=True, slots=True)
class LocalFileIdentity:
    path: Path
    device: int
    inode: int
    size: int
    mtime_ns: int
    digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "device": self.device,
            "inode": self.inode,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class LocalDirectoryIdentity:
    path: Path
    device: int
    inode: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "device": self.device,
            "inode": self.inode,
        }


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    provider: str
    retrieved_at: str
    source: str


@dataclass(frozen=True, slots=True)
class ResultFact:
    match_id: str
    stage_id: str
    home_team_id: str
    away_team_id: str
    home_score: int
    away_score: int
    leg: int = 1
    winner_team_id: str | None = None
    source_id: str | None = None
    provenance: SourceProvenance | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def score(self) -> Score:
        return Score(home=self.home_score, away=self.away_score)

    @property
    def key(self) -> tuple[str, int]:
        return self.match_id, self.leg

    def completed_match(self) -> CompletedMatch:
        metadata = dict(self.metadata)
        if self.provenance is not None:
            metadata["import_provenance"] = {
                "provider": self.provenance.provider,
                "retrieved_at": self.provenance.retrieved_at,
                "source": self.provenance.source,
                **({"source_id": self.source_id} if self.source_id else {}),
            }
        return CompletedMatch(
            match_id=self.match_id,
            stage_id=self.stage_id,
            home_team_id=self.home_team_id,
            away_team_id=self.away_team_id,
            score=self.score,
            leg=self.leg,
            winner_team_id=self.winner_team_id,
            metadata=metadata,
        )

    def to_document(self) -> dict[str, object]:
        match = self.completed_match()
        value: dict[str, object] = {
            "match_id": match.match_id,
            "stage_id": match.stage_id,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "score": {"home": match.score.home, "away": match.score.away},
            "leg": match.leg,
            "metadata": serializable_value(match.metadata),
        }
        if match.winner_team_id is not None:
            value["winner_team_id"] = match.winner_team_id
        return value


@dataclass(frozen=True, slots=True)
class ResultConflict:
    existing: ResultFact
    incoming: ResultFact
    reason: str


@dataclass(frozen=True, slots=True)
class UnmatchedResult:
    row_number: int
    reason: str
    row: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ImportPreview:
    config_path: Path
    source_path: Path
    source_format: str
    config_digest: str
    config_identity: LocalFileIdentity
    config_parent_identity: LocalDirectoryIdentity
    source_identity: LocalFileIdentity
    source_provenance: SourceProvenance
    additions: tuple[ResultFact, ...] = ()
    idempotent: tuple[ResultFact, ...] = ()
    conflicts: tuple[ResultConflict, ...] = ()
    unmatched: tuple[UnmatchedResult, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def has_blockers(self) -> bool:
        return bool(self.conflicts or self.unmatched)

    def to_dict(self) -> dict[str, object]:
        provenance = {
            "provider": self.source_provenance.provider,
            "retrieved_at": self.source_provenance.retrieved_at,
            "source": self.source_provenance.source,
        }
        return {
            "config": self.config_identity.to_dict(),
            "config_parent": self.config_parent_identity.to_dict(),
            "source": self.source_identity.to_dict(),
            "source_format": self.source_format,
            "source_provenance": provenance,
            "additions": [fact.to_document() for fact in self.additions],
            "idempotent": [fact.to_document() for fact in self.idempotent],
            "conflicts": [
                {
                    "existing": conflict.existing.to_document(),
                    "incoming": conflict.incoming.to_document(),
                    "reason": conflict.reason,
                }
                for conflict in self.conflicts
            ],
            "unmatched": [
                {
                    "row_number": issue.row_number,
                    "reason": issue.reason,
                    "row": serializable_value(issue.row),
                }
                for issue in self.unmatched
            ],
            "warnings": list(self.warnings),
        }


def _error(message: str) -> TournamentValidationError:
    return TournamentValidationError(message)


def _stat_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
    )


def _resolved_regular_path(path: Path, label: str) -> tuple[Path, os.stat_result]:
    supplied = Path(path)
    try:
        canonical_parent = supplied.parent.resolve(strict=True)
        canonical = canonical_parent / supplied.name
        final_stat = canonical.stat(follow_symlinks=False)
    except TournamentValidationError:
        raise
    except OSError as error:
        raise _error(f"{label} file could not be accessed: {supplied}") from error
    if stat.S_ISLNK(final_stat.st_mode):
        raise _error(f"{label} path must not end in a symlink: {canonical}")
    if not stat.S_ISREG(final_stat.st_mode):
        raise _error(f"{label} must be a regular file: {canonical}")
    return canonical, final_stat


def _require_race_resistant_primitives() -> None:
    supports_dir_fd: Collection[object] = getattr(os, "supports_dir_fd", ())
    supports_follow_symlinks: Collection[object] = getattr(
        os,
        "supports_follow_symlinks",
        (),
    )
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in supports_dir_fd
        or os.stat not in supports_dir_fd
        or os.stat not in supports_follow_symlinks
        or os.unlink not in supports_dir_fd
        or os.rename not in supports_dir_fd
        or _fcntl is None
    ):
        raise _error(
            "race-resistant results apply is unavailable on this platform"
        )


def _directory_identity(
    path: Path,
    directory_stat: os.stat_result,
) -> LocalDirectoryIdentity:
    return LocalDirectoryIdentity(
        path=path,
        device=directory_stat.st_dev,
        inode=directory_stat.st_ino,
    )


def _verify_parent_identity(
    descriptor: int,
    expected: LocalDirectoryIdentity,
) -> None:
    try:
        descriptor_stat = os.fstat(descriptor)
        path_stat = os.stat(expected.path, follow_symlinks=False)
    except OSError as error:
        raise _error("tournament config parent changed since preview") from error
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or not stat.S_ISDIR(path_stat.st_mode)
        or (descriptor_stat.st_dev, descriptor_stat.st_ino)
        != (expected.device, expected.inode)
        or (path_stat.st_dev, path_stat.st_ino)
        != (expected.device, expected.inode)
    ):
        raise _error("tournament config parent changed since preview")


def _open_parent_directory(
    path: Path,
    *,
    expected: LocalDirectoryIdentity | None = None,
) -> tuple[int, LocalDirectoryIdentity]:
    _require_race_resistant_primitives()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _error("tournament config parent could not be opened safely") from error
    try:
        descriptor_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(descriptor_stat.st_mode):
            raise _error("tournament config parent must be a directory")
        identity = _directory_identity(path, descriptor_stat)
        _verify_parent_identity(descriptor, expected or identity)
        if expected is not None and identity != expected:
            raise _error("tournament config parent changed since preview")
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_config_writer_lock(parent_descriptor: int, target_name: str) -> int:
    if _fcntl is None:
        raise _error("race-resistant results apply is unavailable on this platform")
    lock_name = f".{target_name}.lock"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            lock_name,
            flags,
            0o600,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        raise _error("tournament config writer lock could not be opened safely") from error
    try:
        path_stat = os.stat(
            lock_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        lock_stat = os.fstat(descriptor)
        if not stat.S_ISREG(lock_stat.st_mode):
            raise _error("tournament config writer lock must be a regular file")
        if _stat_signature(lock_stat) != _stat_signature(path_stat):
            raise _error("tournament config writer lock changed while opening")
        _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        current_stat = os.stat(
            lock_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if _stat_signature(os.fstat(descriptor)) != _stat_signature(current_stat):
            raise _error("tournament config writer lock changed while acquiring")
        return descriptor
    except BlockingIOError as error:
        os.close(descriptor)
        raise _error("tournament config is locked by another project writer") from error
    except BaseException:
        os.close(descriptor)
        raise


def _release_config_writer_lock(descriptor: int) -> None:
    try:
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _identity_from_content(
    canonical_path: Path,
    file_stat: os.stat_result,
    content: bytes,
) -> LocalFileIdentity:
    return LocalFileIdentity(
        path=canonical_path,
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        size=file_stat.st_size,
        mtime_ns=file_stat.st_mtime_ns,
        digest=_digest(content),
    )


def _identity_matches_stat(
    identity: LocalFileIdentity,
    file_stat: os.stat_result,
) -> bool:
    return stat.S_ISREG(file_stat.st_mode) and (
        identity.device,
        identity.inode,
        identity.size,
        identity.mtime_ns,
    ) == (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )


def _read_open_descriptor(
    descriptor: int,
    canonical_path: Path,
    label: str,
) -> tuple[LocalFileIdentity, bytes]:
    try:
        before = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        content = b"".join(chunks)
        after = os.fstat(descriptor)
    except OSError as error:
        raise _error(f"{label} file could not be read safely: {canonical_path}") from error
    if (
        _stat_signature(before) != _stat_signature(after)
        or len(content) != after.st_size
    ):
        raise _error(f"{label} identity changed while reading")
    return _identity_from_content(canonical_path, after, content), content


def _open_file_at(
    parent_descriptor: int,
    filename: str,
    canonical_path: Path,
    label: str,
) -> tuple[int, LocalFileIdentity, bytes]:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = -1
    try:
        path_stat = os.stat(
            filename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(path_stat.st_mode):
            raise _error(f"{label} path must not end in a symlink: {canonical_path}")
        if not stat.S_ISREG(path_stat.st_mode):
            raise _error(f"{label} must be a regular file: {canonical_path}")
        descriptor = os.open(filename, flags, dir_fd=parent_descriptor)
        opened_stat = os.fstat(descriptor)
        if _stat_signature(opened_stat) != _stat_signature(path_stat):
            raise _error(f"{label} identity changed while opening")
        identity, content = _read_open_descriptor(
            descriptor,
            canonical_path,
            label,
        )
        current_stat = os.stat(
            filename,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        descriptor_stat = os.fstat(descriptor)
        if _stat_signature(descriptor_stat) != _stat_signature(current_stat):
            raise _error(f"{label} identity changed while reading")
        if _stat_signature(descriptor_stat) != _stat_signature(opened_stat):
            raise _error(f"{label} identity changed while reading")
        return descriptor, identity, content
    except TournamentValidationError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise _error(f"{label} file could not be read safely: {canonical_path}") from error


def _read_file_at(
    parent_descriptor: int,
    filename: str,
    canonical_path: Path,
    label: str,
) -> tuple[LocalFileIdentity, bytes]:
    descriptor, identity, content = _open_file_at(
        parent_descriptor,
        filename,
        canonical_path,
        label,
    )
    try:
        return identity, content
    finally:
        os.close(descriptor)


def _read_local_file(path: Path, label: str) -> tuple[LocalFileIdentity, bytes]:
    canonical, path_stat = _resolved_regular_path(path, label)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(canonical, flags)
        opened_stat = os.fstat(descriptor)
        if _stat_signature(opened_stat) != _stat_signature(path_stat):
            raise _error(f"{label} identity changed while opening")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            content = handle.read()
            read_stat = os.fstat(handle.fileno())
        current_path, current_stat = _resolved_regular_path(canonical, label)
    except TournamentValidationError:
        raise
    except OSError as error:
        raise _error(f"{label} file could not be read: {canonical}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if current_path != canonical or _stat_signature(read_stat) != _stat_signature(current_stat):
        raise _error(f"{label} identity changed while reading")
    return (
        LocalFileIdentity(
            path=canonical,
            device=read_stat.st_dev,
            inode=read_stat.st_ino,
            size=read_stat.st_size,
            mtime_ns=read_stat.st_mtime_ns,
            digest=_digest(content),
        ),
        content,
    )


def _read_preview_target(
    path: Path,
) -> tuple[LocalDirectoryIdentity, LocalFileIdentity, bytes]:
    canonical, _ = _resolved_regular_path(path, "tournament config")
    parent_descriptor, parent_identity = _open_parent_directory(canonical.parent)
    try:
        identity, content = _read_file_at(
            parent_descriptor,
            canonical.name,
            canonical,
            "tournament config",
        )
        _verify_parent_identity(parent_descriptor, parent_identity)
        return parent_identity, identity, content
    finally:
        os.close(parent_descriptor)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise _error(f"{label} must be an object with string keys")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _error(f"{label} must be non-empty text")
    return value.strip()


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError as error:
            raise _error(f"{label} must be an integer") from error
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _error(f"{label} must be an integer greater than or equal to {minimum}")
    return value


def _timestamp(value: object, label: str) -> str:
    text = _text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _error(f"{label} must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise _error(f"{label} must include a timezone")
    return parsed.isoformat()


def _normalized_name(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(character for character in decomposed if character.isalnum())


def _alias_index(tournament: Tournament) -> dict[str, str | None]:
    index: dict[str, str | None] = {}
    for team in tournament.teams:
        for value in (team.id, team.display_name, *team.aliases):
            normalized = _normalized_name(value)
            if normalized not in index:
                index[normalized] = team.id
            elif index[normalized] != team.id:
                index[normalized] = None
    return index


def _resolve_team(
    value: object,
    aliases: Mapping[str, str | None],
) -> tuple[str | None, str | None]:
    display = _text(value, "result team")
    normalized = _normalized_name(display)
    if normalized not in aliases:
        return None, f"unmatched team: {display}"
    team_id = aliases[normalized]
    if team_id is None:
        return None, f"ambiguous team alias: {display}"
    return team_id, None


def _configured_fixtures(tournament: Tournament) -> tuple[
    dict[tuple[str, str, str, int], tuple[str, ...]],
    dict[tuple[str, str, int], tuple[str, str] | None],
]:
    by_identity: dict[tuple[str, str, str, int], list[str]] = {}
    configured: dict[tuple[str, str, int], tuple[str, str] | None] = {}
    for stage in tournament.stages:
        stage_id = str(stage["id"])
        stage_type = stage["type"]
        if stage_type == "round_robin_groups":
            for group_fixture in generate_group_fixture_specs(stage):
                pair = (group_fixture.home_team_id, group_fixture.away_team_id)
                by_identity.setdefault(
                    (stage_id, *pair, group_fixture.leg),
                    [],
                ).append(group_fixture.match_id)
                configured[(stage_id, group_fixture.match_id, group_fixture.leg)] = pair
        elif stage_type == "league_table":
            fixtures = stage.get("fixtures", ())
            assert isinstance(fixtures, Sequence)
            for raw_fixture in fixtures:
                league_fixture = _mapping(raw_fixture, "league fixture")
                match_id = str(league_fixture["match_id"])
                pair = (
                    str(league_fixture["home_team_id"]),
                    str(league_fixture["away_team_id"]),
                )
                by_identity.setdefault((stage_id, *pair, 1), []).append(match_id)
                configured[(stage_id, match_id, 1)] = pair
        else:
            pairing = _mapping(stage["pairing"], "knockout pairing")
            ties = pairing.get("ties", ())
            legs = stage["legs"]
            assert isinstance(legs, int) and not isinstance(legs, bool)
            assert isinstance(ties, Sequence)
            for raw_tie in ties:
                tie = _mapping(raw_tie, "knockout tie")
                for leg in range(1, legs + 1):
                    configured[(stage_id, str(tie["id"]), leg)] = None
    return (
        {key: tuple(value) for key, value in by_identity.items()},
        configured,
    )


def _existing_fact(match: CompletedMatch) -> ResultFact:
    metadata = sanitize_metadata(match.metadata, label="completed match metadata")
    assert isinstance(metadata, Mapping)
    return ResultFact(
        match_id=match.match_id,
        stage_id=match.stage_id,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_score=match.score.home,
        away_score=match.score.away,
        leg=match.leg,
        winner_team_id=match.winner_team_id,
        metadata=metadata,
    )


def _same_fact(first: ResultFact, second: ResultFact) -> bool:
    return (
        first.match_id,
        first.stage_id,
        first.home_team_id,
        first.away_team_id,
        first.home_score,
        first.away_score,
        first.leg,
        first.winner_team_id,
    ) == (
        second.match_id,
        second.stage_id,
        second.home_team_id,
        second.away_team_id,
        second.home_score,
        second.away_score,
        second.leg,
        second.winner_team_id,
    )


def _load_json_rows(
    content: bytes,
    source: Path,
) -> tuple[SourceProvenance, list[Mapping[str, object]]]:
    try:
        document = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = error.msg if isinstance(error, json.JSONDecodeError) else "invalid UTF-8"
        raise _error(f"invalid results JSON: {message}") from error
    except ValueError as error:
        raise _error(f"invalid results JSON: {error}") from error
    root = _mapping(document, "results import")
    unknown = sorted(set(root) - _JSON_ROOT_FIELDS)
    if unknown:
        raise _error(f"results import contains unknown properties: {', '.join(unknown)}")
    if root.get("schema_version") != 1:
        raise _error("results import schema version must be 1")
    raw_results = root.get("results")
    if not isinstance(raw_results, list):
        raise _error("results must be an array")
    provenance = SourceProvenance(
        provider=_text(root.get("provider"), "results provider"),
        retrieved_at=_timestamp(root.get("retrieved_at"), "results retrieved_at"),
        source=str(source),
    )
    return provenance, [_mapping(row, f"results[{index}]") for index, row in enumerate(raw_results)]


def _load_csv_rows(
    content: bytes,
    source: Path,
) -> tuple[SourceProvenance, list[dict[str, object]]]:
    try:
        text = content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        headers = reader.fieldnames
        if not headers:
            raise _error("results CSV must contain a header row")
        duplicates = sorted(
            name for name, count in Counter(headers).items() if count > 1
        )
        if duplicates:
            raise _error(
                f"results CSV header contains duplicates: {', '.join(duplicates)}"
            )
        unknown = sorted(set(headers) - _CSV_SUPPORTED_FIELDS)
        if unknown:
            raise _error(
                f"results CSV header contains unsupported columns: {', '.join(unknown)}"
            )
        missing = sorted(_CSV_REQUIRED_FIELDS - set(headers))
        if missing:
            raise _error(
                f"results CSV header is missing required columns: {', '.join(missing)}"
            )
        rows = []
        for line_number, raw_row in enumerate(reader, start=2):
            if None in raw_row:
                raise _error(f"results CSV row {line_number} contains surplus columns")
            if any(value is None for value in raw_row.values()):
                raise _error(f"results CSV row {line_number} has missing columns")
            row: dict[str, object] = {
                str(key): value for key, value in raw_row.items()
            }
            raw_metadata = row.get("metadata")
            if raw_metadata in {None, ""}:
                row["metadata"] = {}
            else:
                try:
                    row["metadata"] = _mapping(
                        json.loads(str(raw_metadata)),
                        f"CSV row {line_number} metadata",
                    )
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise _error(
                        f"results CSV row {line_number} metadata must be valid JSON"
                    ) from error
            rows.append(row)
    except UnicodeDecodeError as error:
        raise _error("invalid results CSV: input must be UTF-8") from error
    except csv.Error as error:
        raise _error(f"invalid results CSV: {error}") from error
    if not rows:
        raise _error("results CSV must contain at least one row")
    provider = _text(rows[0].pop("provider", None), "results provider")
    retrieved_at = _timestamp(rows[0].pop("retrieved_at", None), "results retrieved_at")
    for index, row in enumerate(rows[1:], start=2):
        row_provider = _text(row.pop("provider", None), f"CSV row {index} provider")
        row_retrieved = _timestamp(row.pop("retrieved_at", None), f"CSV row {index} retrieved_at")
        if row_provider != provider or row_retrieved != retrieved_at:
            raise _error("all CSV rows must use the same provider and retrieved_at")
    return SourceProvenance(provider, retrieved_at, str(source)), rows


def _validate_row_fields(row: Mapping[str, object], row_number: int) -> None:
    unknown = sorted(
        key
        for key, value in row.items()
        if key not in _RESULT_FIELDS and value is not None and value != ""
    )
    if unknown:
        raise _error(f"result row {row_number} contains unknown properties: {', '.join(unknown)}")
    if _text(row.get("status"), f"result row {row_number} status").casefold() != "final":
        raise _error(f"result row {row_number} status must be final")


def _resolve_row(
    row: Mapping[str, object],
    *,
    row_number: int,
    tournament: Tournament,
    provenance: SourceProvenance,
    aliases: Mapping[str, str | None],
    fixtures_by_identity: Mapping[tuple[str, str, str, int], tuple[str, ...]],
    configured_fixtures: Mapping[tuple[str, str, int], tuple[str, str] | None],
) -> tuple[ResultFact | None, UnmatchedResult | None]:
    raw_metadata = row.get("metadata", {})
    if raw_metadata is None or raw_metadata == "":
        raw_metadata = {}
    metadata = sanitize_metadata(
        _mapping(raw_metadata, f"result row {row_number} metadata"),
        label=f"result row {row_number} metadata",
    )
    assert isinstance(metadata, Mapping)
    sanitized_row = dict(row)
    sanitized_row["metadata"] = metadata
    row = MappingProxyType(sanitized_row)
    _validate_row_fields(row, row_number)
    stage_id = _text(row.get("stage_id"), f"result row {row_number} stage_id")
    if stage_id not in {str(stage["id"]) for stage in tournament.stages}:
        raise _error(f"result row {row_number} references an unknown stage")
    home_team_id, home_error = _resolve_team(row.get("home_team"), aliases)
    away_team_id, away_error = _resolve_team(row.get("away_team"), aliases)
    if home_error or away_error:
        reason = "; ".join(error for error in (home_error, away_error) if error)
        return None, UnmatchedResult(row_number, reason, MappingProxyType(dict(row)))
    assert home_team_id is not None and away_team_id is not None
    if home_team_id == away_team_id:
        raise _error(f"result row {row_number} teams must be distinct")
    leg = _integer(row.get("leg", 1), f"result row {row_number} leg", minimum=1)
    pair = frozenset((home_team_id, away_team_id))
    raw_match_id = row.get("match_id")
    if raw_match_id not in {None, ""}:
        match_id = _text(raw_match_id, f"result row {row_number} match_id")
        configured_pair = configured_fixtures.get((stage_id, match_id, leg), "missing")
        if configured_pair == "missing":
            raise _error(f"result row {row_number} match_id is not configured for its stage")
        if configured_pair is not None and configured_pair != (
            home_team_id,
            away_team_id,
        ):
            raise _error(
                f"result row {row_number} home-away order contradicts the configured match_id"
            )
    else:
        candidates = fixtures_by_identity.get(
            (stage_id, home_team_id, away_team_id, leg),
            (),
        )
        if len(candidates) != 1:
            reversed_candidates = fixtures_by_identity.get(
                (stage_id, away_team_id, home_team_id, leg),
                (),
            )
            if reversed_candidates:
                raise _error(
                    f"result row {row_number} home-away order contradicts the configured fixture"
                )
            reason = "no unique configured fixture matches stage, teams, and leg"
            return None, UnmatchedResult(row_number, reason, MappingProxyType(dict(row)))
        match_id = candidates[0]
    home_score = _integer(row.get("home_score"), f"result row {row_number} home_score")
    away_score = _integer(row.get("away_score"), f"result row {row_number} away_score")
    winner_team_id: str | None = None
    if row.get("winner_team") not in {None, ""}:
        winner_team_id, winner_error = _resolve_team(row.get("winner_team"), aliases)
        if winner_error:
            return None, UnmatchedResult(row_number, winner_error, MappingProxyType(dict(row)))
        if winner_team_id not in pair:
            raise _error(f"result row {row_number} winner must be one of its teams")
        stage = next(stage for stage in tournament.stages if stage["id"] == stage_id)
        if stage["type"] != "knockout":
            if home_score == away_score:
                raise _error(
                    f"result row {row_number} draw cannot declare a winner"
                )
            score_winner = (
                home_team_id if home_score > away_score else away_team_id
            )
            if winner_team_id != score_winner:
                raise _error(f"result row {row_number} winner contradicts score")
    source_id = None
    if row.get("source_id") not in {None, ""}:
        source_id = _text(row["source_id"], f"result row {row_number} source_id")
    return ResultFact(
        match_id=match_id,
        stage_id=stage_id,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_score=home_score,
        away_score=away_score,
        leg=leg,
        winner_team_id=winner_team_id,
        source_id=source_id,
        provenance=provenance,
        metadata=metadata,
    ), None


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _encode_json_document(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            serializable_value(value),
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_temp_at(
    parent_descriptor: int,
    target_name: str,
    content: bytes,
) -> str:
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    for _ in range(128):
        temporary_name = f".{target_name}.{secrets.token_hex(12)}.tmp"
        descriptor = -1
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            return temporary_name
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            raise
    raise _error("could not allocate a secure temporary results file")


def _remove_temp_at(parent_descriptor: int, temporary_name: str | None) -> None:
    if temporary_name is None:
        return
    with suppress(FileNotFoundError):
        os.unlink(temporary_name, dir_fd=parent_descriptor)


def _restore_target_at(
    parent_descriptor: int,
    target_name: str,
    canonical_path: Path,
    content: bytes,
) -> None:
    temporary_name: str | None = None
    try:
        temporary_name = _write_temp_at(parent_descriptor, target_name, content)
        os.replace(
            temporary_name,
            target_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        temporary_name = None
        os.fsync(parent_descriptor)
        _, restored_bytes = _read_file_at(
            parent_descriptor,
            target_name,
            canonical_path,
            "restored tournament config",
        )
        if restored_bytes != content:
            raise _error("tournament config rollback could not be verified")
    except TournamentValidationError:
        raise
    except (OSError, TypeError, NotImplementedError) as error:
        raise _error("tournament config rollback failed closed") from error
    finally:
        _remove_temp_at(parent_descriptor, temporary_name)


def _load_tournament_bytes(content: bytes) -> Tournament:
    try:
        document = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = error.msg if isinstance(error, json.JSONDecodeError) else "invalid UTF-8"
        raise _error(f"invalid tournament JSON: {message}") from error
    return load_tournament_document(_mapping(document, "tournament document"))


def _candidate_document(
    config_bytes: bytes,
    additions: Sequence[ResultFact],
    conflicts: Sequence[ResultConflict],
    *,
    replace_conflicts: bool,
) -> dict[str, object]:
    try:
        document = json.loads(config_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        message = error.msg if isinstance(error, json.JSONDecodeError) else "invalid UTF-8"
        raise _error(f"invalid tournament JSON: {message}") from error
    root = dict(_mapping(document, "tournament document"))
    raw_completed = root.get("completed_matches")
    if not isinstance(raw_completed, list):
        raise _error("tournament completed_matches must be an array")
    completed = [dict(_mapping(item, "completed match")) for item in raw_completed]
    by_key = {
        (
            str(item.get("match_id")),
            _integer(item.get("leg", 1), "completed match leg", minimum=1),
        ): index
        for index, item in enumerate(completed)
    }
    completed.extend(fact.to_document() for fact in additions)
    if replace_conflicts:
        for conflict in conflicts:
            completed[by_key[conflict.incoming.key]] = conflict.incoming.to_document()
    root["completed_matches"] = completed
    load_tournament_document(root)
    return root


def preview_results(config: Path, source: Path, *, format: str) -> ImportPreview:
    """Classify a local results file without mutating the tournament config."""

    normalized_format = format.casefold()
    if normalized_format not in {"json", "csv"}:
        raise _error("results format must be json or csv")
    config_parent_identity, config_identity, config_bytes = _read_preview_target(config)
    tournament = _load_tournament_bytes(config_bytes)
    source_identity, source_bytes = _read_local_file(source, "results source")
    provenance, rows = (
        _load_json_rows(source_bytes, source_identity.path)
        if normalized_format == "json"
        else _load_csv_rows(source_bytes, source_identity.path)
    )
    aliases = _alias_index(tournament)
    fixtures_by_identity, configured_fixtures = _configured_fixtures(tournament)
    existing = {
        (match.match_id, match.leg): _existing_fact(match)
        for match in tournament.completed_matches
    }
    additions: list[ResultFact] = []
    idempotent: list[ResultFact] = []
    conflicts: list[ResultConflict] = []
    unmatched: list[UnmatchedResult] = []
    source_keys: set[tuple[str, int]] = set()
    for row_number, row in enumerate(rows, start=1):
        fact, issue = _resolve_row(
            row,
            row_number=row_number,
            tournament=tournament,
            provenance=provenance,
            aliases=aliases,
            fixtures_by_identity=fixtures_by_identity,
            configured_fixtures=configured_fixtures,
        )
        if issue is not None:
            unmatched.append(issue)
            continue
        assert fact is not None
        if fact.key in source_keys:
            raise _error(f"duplicate result fact for {fact.match_id} leg {fact.leg}")
        source_keys.add(fact.key)
        previous = existing.get(fact.key)
        if previous is None:
            additions.append(fact)
        elif _same_fact(previous, fact):
            idempotent.append(fact)
        else:
            conflicts.append(
                ResultConflict(
                    previous,
                    fact,
                    "incoming result differs from immutable completed fact",
                )
            )

    _candidate_document(
        config_bytes,
        additions,
        conflicts,
        replace_conflicts=bool(conflicts),
    )
    return ImportPreview(
        config_path=config_identity.path,
        source_path=source_identity.path,
        source_format=normalized_format,
        config_digest=_digest(config_bytes),
        config_identity=config_identity,
        config_parent_identity=config_parent_identity,
        source_identity=source_identity,
        source_provenance=provenance,
        additions=tuple(additions),
        idempotent=tuple(idempotent),
        conflicts=tuple(conflicts),
        unmatched=tuple(unmatched),
    )


def apply_results(
    config: Path,
    preview: ImportPreview,
    *,
    replace_conflicts: bool = False,
) -> None:
    """Atomically apply a still-current preview after complete domain validation."""

    _require_race_resistant_primitives()
    canonical, _ = _resolved_regular_path(config, "tournament config")
    if canonical != preview.config_path:
        raise _error("preview belongs to a different tournament config path")
    if canonical.parent != preview.config_parent_identity.path:
        raise _error("preview belongs to a different tournament config parent")

    parent_descriptor, _ = _open_parent_directory(
        preview.config_parent_identity.path,
        expected=preview.config_parent_identity,
    )
    temporary_name: str | None = None
    lock_descriptor = -1
    commit_guard_descriptor = -1
    try:
        lock_descriptor = _acquire_config_writer_lock(
            parent_descriptor,
            canonical.name,
        )
        _verify_parent_identity(parent_descriptor, preview.config_parent_identity)
        current_identity, current_bytes = _read_file_at(
            parent_descriptor,
            canonical.name,
            canonical,
            "tournament config",
        )
        if (
            current_identity != preview.config_identity
            or current_identity.digest != preview.config_digest
        ):
            raise _error("tournament config identity or content changed since preview")
        if preview.unmatched:
            detail = "conflict and unmatched rows" if preview.conflicts else "unmatched rows"
            raise _error(f"cannot apply preview with {detail}")
        if preview.conflicts and not replace_conflicts:
            raise _error("cannot apply preview with conflicts without explicit replacement")
        if not preview.additions and not (replace_conflicts and preview.conflicts):
            return

        root = _candidate_document(
            current_bytes,
            preview.additions,
            preview.conflicts,
            replace_conflicts=replace_conflicts,
        )
        replacement_bytes = _encode_json_document(root)
        try:
            temporary_name = _write_temp_at(
                parent_descriptor,
                canonical.name,
                replacement_bytes,
            )
        except OSError as error:
            raise _error("could not prepare a race-resistant results update") from error

        _verify_parent_identity(parent_descriptor, preview.config_parent_identity)
        commit_guard_descriptor, commit_identity, commit_bytes = _open_file_at(
            parent_descriptor,
            canonical.name,
            canonical,
            "tournament config",
        )
        if (
            commit_identity != preview.config_identity
            or commit_identity.digest != preview.config_digest
        ):
            raise _error("tournament config content changed before commit")
        try:
            os.replace(
                temporary_name,
                canonical.name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        except (OSError, TypeError, NotImplementedError) as error:
            raise _error("race-resistant atomic replace is unavailable") from error
        temporary_name = None
        try:
            os.fsync(parent_descriptor)
        except OSError as error:
            raise _error("tournament config parent could not be fsynced") from error

        boundary_identity, boundary_bytes = _read_open_descriptor(
            commit_guard_descriptor,
            canonical,
            "pre-commit tournament config",
        )
        boundary_changed = (
            boundary_identity != commit_identity
            or boundary_bytes != commit_bytes
        )
        try:
            _verify_parent_identity(parent_descriptor, preview.config_parent_identity)
        except TournamentValidationError as parent_error:
            rollback_bytes = boundary_bytes if boundary_changed else commit_bytes
            try:
                _restore_target_at(
                    parent_descriptor,
                    canonical.name,
                    canonical,
                    rollback_bytes,
                )
            except TournamentValidationError as rollback_error:
                raise _error(
                    "tournament config parent changed at the commit boundary and "
                    "the detached update could not be rolled back"
                ) from rollback_error
            raise _error(
                "tournament config parent changed at the commit boundary; "
                "the detached update was rolled back"
            ) from parent_error

        if boundary_changed:
            try:
                _restore_target_at(
                    parent_descriptor,
                    canonical.name,
                    canonical,
                    boundary_bytes,
                )
            except TournamentValidationError as rollback_error:
                raise _error(
                    "concurrent destination edit detected at the commit boundary and "
                    "could not be restored"
                ) from rollback_error
            raise _error(
                "concurrent destination edit detected at the commit boundary; "
                "the edit was restored"
            )

        committed_identity, committed_bytes = _read_file_at(
            parent_descriptor,
            canonical.name,
            canonical,
            "committed tournament config",
        )
        if committed_bytes != replacement_bytes:
            raise _error("tournament config changed at the commit boundary")
        _verify_parent_identity(parent_descriptor, preview.config_parent_identity)
        committed_path, committed_stat = _resolved_regular_path(
            canonical,
            "committed tournament config",
        )
        if (
            committed_path != canonical
            or not _identity_matches_stat(committed_identity, committed_stat)
        ):
            raise _error("tournament config target changed after commit")
        _verify_parent_identity(parent_descriptor, preview.config_parent_identity)
    finally:
        try:
            _remove_temp_at(parent_descriptor, temporary_name)
        finally:
            try:
                if commit_guard_descriptor >= 0:
                    os.close(commit_guard_descriptor)
            finally:
                try:
                    if lock_descriptor >= 0:
                        _release_config_writer_lock(lock_descriptor)
                finally:
                    os.close(parent_descriptor)
