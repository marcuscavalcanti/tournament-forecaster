from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class SourceRecord:
    hits: int = 0
    misses: int = 0

    @property
    def alpha(self) -> int:
        return self.hits + 1

    @property
    def beta(self) -> int:
        return self.misses + 1

    @property
    def score(self) -> float:
        return self.alpha / (self.alpha + self.beta)


class SourceMemory:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.records: dict[str, SourceRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        for name, record in payload.get("sources", {}).items():
            self.records[name] = SourceRecord(
                hits=int(record.get("hits", 0)),
                misses=int(record.get("misses", 0)),
            )

    def _record_for(self, source: str) -> SourceRecord:
        if source not in self.records:
            self.records[source] = SourceRecord()
        return self.records[source]

    def score(self, source: str) -> float:
        return self._record_for(source).score

    def record_result(self, source: str, *, hit: bool) -> None:
        record = self._record_for(source)
        if hit:
            record.hits += 1
        else:
            record.misses += 1

    def thompson_scores(self, sources: Iterable[str], *, seed: int | None = None) -> dict[str, float]:
        rng = random.Random(seed)
        scores = {}
        for source in sources:
            record = self._record_for(source)
            scores[source] = rng.betavariate(record.alpha, record.beta)
        return scores

    def ranked_sources(self, sources: Iterable[str], *, seed: int | None = None) -> list[str]:
        sampled = self.thompson_scores(sources, seed=seed)
        return sorted(sampled, key=sampled.get, reverse=True)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sources": {
                name: {"hits": record.hits, "misses": record.misses}
                for name, record in sorted(self.records.items())
            }
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
