from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_events(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def pick_run(events: list[dict], run_id: str | None) -> tuple[str, list[dict]]:
    by_run: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_run[str(event.get("run_id", ""))].append(event)
    if run_id:
        if run_id not in by_run:
            raise SystemExit(f"run_id {run_id} não encontrado em {len(by_run)} runs")
        return run_id, by_run[run_id]
    complete = [
        (rid, run)
        for rid, run in by_run.items()
        if any(e.get("step") == "render_post" and e.get("status") == "finish" for e in run)
    ]
    pool = complete or list(by_run.items())
    rid, run = sorted(pool, key=lambda kv: kv[1][-1].get("timestamp", ""))[-1]
    return rid, run


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def stage_durations(run: list[dict]) -> list[tuple[str, float]]:
    starts: dict[str, str] = {}
    ends: dict[str, str] = {}
    for event in run:
        step, status = str(event.get("step", "")), str(event.get("status", ""))
        ts = str(event.get("timestamp", ""))
        if not step or not ts:
            continue
        if status == "start" and step not in starts:
            starts[step] = ts
        if status in ("finish", "fail"):
            ends[step] = ts
    rows: list[tuple[str, float]] = []
    for step, started in starts.items():
        if step in ends:
            rows.append((step, (_parse_ts(ends[step]) - _parse_ts(started)).total_seconds()))
    return sorted(rows, key=lambda row: -row[1])


def round_latencies(run: list[dict]) -> dict[str, list[float]]:
    question_ts: list[datetime] = []
    response_ts_by_question: list[list[datetime]] = []
    for event in run:
        if event.get("step") != "model_room":
            continue
        status = event.get("status")
        ts = _parse_ts(str(event.get("timestamp")))
        if status == "question":
            question_ts.append(ts)
            response_ts_by_question.append([])
        elif status == "response" and response_ts_by_question:
            response_ts_by_question[-1].append(ts)
    rounds: list[float] = []
    question_phase: list[float] = []
    response_phase: list[float] = []
    for index, qts in enumerate(question_ts):
        responses = response_ts_by_question[index]
        if responses:
            response_phase.append((max(responses) - qts).total_seconds())
        if index > 0:
            rounds.append((qts - question_ts[index - 1]).total_seconds())
            previous_responses = response_ts_by_question[index - 1]
            if previous_responses:
                question_phase.append((qts - max(previous_responses)).total_seconds())
    return {"round": rounds, "question_phase": question_phase, "response_phase": response_phase}


def control_events(run: list[dict]) -> dict[str, int]:
    counters: dict[str, int] = defaultdict(int)
    for event in run:
        if event.get("step") != "model_room":
            continue
        status = str(event.get("status", ""))
        if status in ("circuit_breaker", "breaker_skipped", "early_exit", "invalidation", "repair", "coverage_missing"):
            counters[status] += 1
    return dict(counters)


def main() -> int:
    parser = argparse.ArgumentParser(description="Breakdown de tempo por etapa/rodada a partir do watchdog JSONL.")
    parser.add_argument("--watchdog-log", default="data/watchdog.jsonl")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()

    path = Path(args.watchdog_log)
    if not path.exists():
        raise SystemExit(f"watchdog log não encontrado: {path}")
    events = load_events(path)
    if not events:
        raise SystemExit("watchdog log vazio")
    run_id, run = pick_run(events, args.run_id)

    first, last = _parse_ts(str(run[0]["timestamp"])), _parse_ts(str(run[-1]["timestamp"]))
    print(f"run: {run_id}")
    print(f"TOTAL: {(last - first).total_seconds():.0f}s ({run[0]['timestamp']} -> {run[-1]['timestamp']})")
    print()
    print("etapa | duração")
    for step, duration in stage_durations(run):
        print(f"{step:>28} | {duration:7.0f}s")

    latencies = round_latencies(run)
    if latencies["round"]:
        print()
        print("sala principal | p50 | p95 | max | n")
        for label, values in latencies.items():
            if not values:
                continue
            print(
                f"{label:>14} | {quantile(values, 0.5):4.0f}s | {quantile(values, 0.95):4.0f}s "
                f"| {max(values):4.0f}s | {len(values)}"
            )
    controls = control_events(run)
    if controls:
        print()
        print("eventos de controle: " + ", ".join(f"{key}={value}" for key, value in sorted(controls.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
