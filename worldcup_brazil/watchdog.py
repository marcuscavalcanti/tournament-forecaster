from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass
class RunWatchdog:
    path: Path | None
    run_id: str = field(default_factory=lambda: uuid4().hex)
    verbose: bool = True
    _started_at: dict[str, float] = field(default_factory=dict)

    def _write(self, event: dict[str, Any]) -> None:
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        if self.verbose:
            detail = f" - {event['detail']}" if event.get("detail") else ""
            print(f"[watchdog] {event['status']} {event['step']}{detail}", file=sys.stderr)

    def event(self, step: str, status: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        event = {
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "status": status,
            "detail": detail,
        }
        if extra:
            event["extra"] = extra

        if status == "start":
            self._started_at[step] = time.monotonic()
        elif step in self._started_at:
            event["elapsed_ms"] = round((time.monotonic() - self._started_at[step]) * 1000)

        self._write(event)

    def start(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "start", detail=detail, extra=extra)

    def finish(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "finish", detail=detail, extra=extra)

    def fail(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "fail", detail=detail, extra=extra)

    def chat(self, agent: str, message: str, *, round_name: str) -> None:
        self.event(
            "model_room",
            "chat",
            detail=message,
            extra={"agent": agent, "round": round_name},
        )

    def meeting_question(self, *, round_index: int, protagonist: str, question: str) -> None:
        self.event(
            "model_room",
            "question",
            detail=question,
            extra={"round": round_index, "protagonist": protagonist},
        )

    def meeting_response(self, *, round_index: int, agent: str, answer: str, support_score: float) -> None:
        self.event(
            "model_room",
            "response",
            detail=answer,
            extra={"round": round_index, "agent": agent, "support_score": support_score},
        )
