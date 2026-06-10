from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class RunState:
    path: Path

    def last_success_at(self) -> datetime | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        value = payload.get("last_success_at")
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def mark_success(self, when: datetime) -> None:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"last_success_at": when.astimezone(timezone.utc).isoformat()}
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def should_run(state: RunState, *, now: datetime, interval: timedelta = timedelta(days=3)) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    last_success = state.last_success_at()
    if last_success is None:
        return True
    return now.astimezone(timezone.utc) - last_success.astimezone(timezone.utc) >= interval
