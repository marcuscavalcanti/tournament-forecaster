"""Preview-first normalization of local JSON and CSV result files."""

from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from ..atomic_io import atomic_write_json
from ..config import load_tournament, load_tournament_document
from ..domain import CompletedMatch, Score, Tournament
from ..errors import TournamentValidationError
from ..group_fixtures import generate_group_fixture_specs


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
            "metadata": dict(match.metadata),
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
    source_provenance: SourceProvenance
    additions: tuple[ResultFact, ...] = ()
    idempotent: tuple[ResultFact, ...] = ()
    conflicts: tuple[ResultConflict, ...] = ()
    unmatched: tuple[UnmatchedResult, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def has_blockers(self) -> bool:
        return bool(self.conflicts or self.unmatched)


def _error(message: str) -> TournamentValidationError:
    return TournamentValidationError(message)


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


def _resolve_team(value: object, aliases: Mapping[str, str | None]) -> tuple[str | None, str | None]:
    display = _text(value, "result team")
    normalized = _normalized_name(display)
    if normalized not in aliases:
        return None, f"unmatched team: {display}"
    team_id = aliases[normalized]
    if team_id is None:
        return None, f"ambiguous team alias: {display}"
    return team_id, None


def _configured_fixtures(tournament: Tournament) -> tuple[
    dict[tuple[str, frozenset[str], int], tuple[str, ...]],
    dict[tuple[str, str], frozenset[str] | None],
]:
    by_identity: dict[tuple[str, frozenset[str], int], list[str]] = {}
    configured: dict[tuple[str, str], frozenset[str] | None] = {}
    for stage in tournament.stages:
        stage_id = str(stage["id"])
        stage_type = stage["type"]
        if stage_type == "round_robin_groups":
            for group_fixture in generate_group_fixture_specs(stage):
                pair = frozenset(
                    (group_fixture.home_team_id, group_fixture.away_team_id)
                )
                by_identity.setdefault(
                    (stage_id, pair, group_fixture.leg),
                    [],
                ).append(group_fixture.match_id)
                configured[(stage_id, group_fixture.match_id)] = pair
        elif stage_type == "league_table":
            fixtures = stage.get("fixtures", ())
            assert isinstance(fixtures, Sequence)
            for raw_fixture in fixtures:
                league_fixture = _mapping(raw_fixture, "league fixture")
                match_id = str(league_fixture["id"])
                pair = frozenset(
                    (
                        str(league_fixture["home_team_id"]),
                        str(league_fixture["away_team_id"]),
                    )
                )
                by_identity.setdefault((stage_id, pair, 1), []).append(match_id)
                configured[(stage_id, match_id)] = pair
        else:
            pairing = _mapping(stage["pairing"], "knockout pairing")
            ties = pairing.get("ties", ())
            assert isinstance(ties, Sequence)
            for raw_tie in ties:
                tie = _mapping(raw_tie, "knockout tie")
                configured[(stage_id, str(tie["id"]))] = None
    return (
        {key: tuple(value) for key, value in by_identity.items()},
        configured,
    )


def _existing_fact(match: CompletedMatch) -> ResultFact:
    return ResultFact(
        match_id=match.match_id,
        stage_id=match.stage_id,
        home_team_id=match.home_team_id,
        away_team_id=match.away_team_id,
        home_score=match.score.home,
        away_score=match.score.away,
        leg=match.leg,
        winner_team_id=match.winner_team_id,
        metadata=match.metadata,
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


def _load_json_rows(source: Path) -> tuple[SourceProvenance, list[Mapping[str, object]]]:
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise _error(f"invalid results JSON: {error.msg}") from error
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


def _load_csv_rows(source: Path) -> tuple[SourceProvenance, list[dict[str, object]]]:
    try:
        with source.open("r", encoding="utf-8", newline="") as handle:
            rows: list[dict[str, object]] = [
                {key: value for key, value in row.items()}
                for row in csv.DictReader(handle)
            ]
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
    fixtures_by_identity: Mapping[tuple[str, frozenset[str], int], tuple[str, ...]],
    configured_fixtures: Mapping[tuple[str, str], frozenset[str] | None],
) -> tuple[ResultFact | None, UnmatchedResult | None]:
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
        configured_pair = configured_fixtures.get((stage_id, match_id), "missing")
        if configured_pair == "missing":
            raise _error(f"result row {row_number} match_id is not configured for its stage")
        if configured_pair is not None and configured_pair != pair:
            raise _error(f"result row {row_number} teams contradict the configured match_id")
    else:
        candidates = fixtures_by_identity.get((stage_id, pair, leg), ())
        if len(candidates) != 1:
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
        score_winner = home_team_id if home_score > away_score else away_team_id if away_score > home_score else None
        if score_winner is not None and winner_team_id != score_winner:
            raise _error(f"result row {row_number} winner contradicts score")
    metadata_value = row.get("metadata", {})
    metadata = _mapping(metadata_value, f"result row {row_number} metadata")
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
        metadata=MappingProxyType(dict(metadata)),
    ), None


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _candidate_document(
    config_bytes: bytes,
    additions: Sequence[ResultFact],
    conflicts: Sequence[ResultConflict],
    *,
    replace_conflicts: bool,
) -> dict[str, object]:
    try:
        document = json.loads(config_bytes)
    except json.JSONDecodeError as error:
        raise _error(f"invalid tournament JSON: {error.msg}") from error
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
    config_bytes = config.read_bytes()
    tournament = load_tournament(config)
    provenance, rows = (
        _load_json_rows(source) if normalized_format == "json" else _load_csv_rows(source)
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
            conflicts.append(ResultConflict(previous, fact, "incoming result differs from immutable completed fact"))

    _candidate_document(
        config_bytes,
        additions,
        conflicts,
        replace_conflicts=bool(conflicts),
    )
    return ImportPreview(
        config_path=config,
        source_path=source,
        source_format=normalized_format,
        config_digest=_digest(config_bytes),
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

    if config != preview.config_path:
        raise _error("preview belongs to a different tournament config")
    current_bytes = config.read_bytes()
    if _digest(current_bytes) != preview.config_digest:
        raise _error("tournament config changed since preview")
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
    atomic_write_json(config, root)
