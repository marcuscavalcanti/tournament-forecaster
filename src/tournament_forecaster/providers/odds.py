"""Validated, provenance-only previews of local odds documents."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from ..errors import TournamentValidationError
from .security import redact_url, sanitize_metadata, serializable_value


_ROOT_FIELDS = frozenset({"schema_version", "provider", "retrieved_at", "odds"})
_ODDS_FIELDS = frozenset(
    {"market", "selection_id", "decimal_odds", "bookmaker", "source_url", "source_id", "metadata"}
)
@dataclass(frozen=True, slots=True)
class OddsProvenance:
    provider: str
    retrieved_at: str
    source: str


@dataclass(frozen=True, slots=True)
class OddsRecord:
    market: str
    selection_id: str
    decimal_odds: float
    bookmaker: str | None = None
    source_url: str | None = None
    source_id: str | None = None
    metadata: Mapping[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "market": self.market,
            "selection_id": self.selection_id,
            "decimal_odds": self.decimal_odds,
            **({"bookmaker": self.bookmaker} if self.bookmaker else {}),
            **({"source_url": self.source_url} if self.source_url else {}),
            **({"source_id": self.source_id} if self.source_id else {}),
            **({"metadata": serializable_value(self.metadata)} if self.metadata else {}),
        }


@dataclass(frozen=True, slots=True)
class OddsPreview:
    provenance: OddsProvenance
    records: tuple[OddsRecord, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "provenance": {
                "provider": self.provenance.provider,
                "retrieved_at": self.provenance.retrieved_at,
                "source": self.provenance.source,
            },
            "records": [record.to_dict() for record in self.records],
            "warnings": list(self.warnings),
        }


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


def _timestamp(value: object) -> str:
    text = _text(value, "odds retrieved_at")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise _error("odds retrieved_at must be an ISO 8601 timestamp") from error
    if parsed.tzinfo is None:
        raise _error("odds retrieved_at must include a timezone")
    return parsed.isoformat()


def _source_url(value: object, label: str) -> str:
    url = _text(value, label)
    try:
        parsed = urlsplit(url)
    except ValueError as error:
        raise _error(f"{label} must be a valid HTTP(S) URL") from error
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
        raise _error(f"{label} must be an HTTP(S) URL")
    return redact_url(url)


def preview_odds(source: Path) -> OddsPreview:
    """Validate one local odds file without accepting any core-state mutation fields."""

    try:
        source_text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise _error("odds source must be valid UTF-8") from error
    except OSError as error:
        raise _error(f"odds source could not be read: {source}") from error
    try:
        document = json.loads(source_text)
    except json.JSONDecodeError as error:
        raise _error(f"invalid odds JSON: {error.msg}") from error
    root = _mapping(document, "odds import")
    unknown = sorted(set(root) - _ROOT_FIELDS)
    if unknown:
        raise _error(f"odds import contains unknown properties: {', '.join(unknown)}")
    if root.get("schema_version") != 1:
        raise _error("odds import schema version must be 1")
    raw_records = root.get("odds")
    if not isinstance(raw_records, list):
        raise _error("odds must be an array")
    provenance = OddsProvenance(
        provider=_text(root.get("provider"), "odds provider"),
        retrieved_at=_timestamp(root.get("retrieved_at")),
        source=str(source),
    )
    records: list[OddsRecord] = []
    for index, raw_record in enumerate(raw_records):
        record = _mapping(raw_record, f"odds[{index}]")
        unknown = sorted(set(record) - _ODDS_FIELDS)
        if unknown:
            raise _error(f"odds[{index}] contains unknown properties: {', '.join(unknown)}")
        decimal_value = record.get("decimal_odds")
        if (
            isinstance(decimal_value, bool)
            or not isinstance(decimal_value, (int, float))
            or not math.isfinite(float(decimal_value))
            or float(decimal_value) <= 1.0
        ):
            raise _error(f"odds[{index}] decimal_odds must be finite and greater than 1")
        source_url = None
        if record.get("source_url") is not None:
            source_url = _source_url(record["source_url"], f"odds[{index}] source_url")
        metadata = record.get("metadata")
        if metadata is not None:
            metadata = sanitize_metadata(
                _mapping(metadata, f"odds[{index}] metadata"),
                label=f"odds[{index}] metadata",
            )
            assert isinstance(metadata, Mapping)
        records.append(
            OddsRecord(
                market=_text(record.get("market"), f"odds[{index}] market"),
                selection_id=_text(record.get("selection_id"), f"odds[{index}] selection_id"),
                decimal_odds=float(decimal_value),
                bookmaker=(
                    _text(record["bookmaker"], f"odds[{index}] bookmaker")
                    if record.get("bookmaker") is not None
                    else None
                ),
                source_url=source_url,
                source_id=(
                    _text(record["source_id"], f"odds[{index}] source_id")
                    if record.get("source_id") is not None
                    else None
                ),
                metadata=metadata,
            )
        )
    return OddsPreview(provenance=provenance, records=tuple(records))
