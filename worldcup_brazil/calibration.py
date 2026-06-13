from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from worldcup_brazil.atomic_io import atomic_write_text, quarantine_corrupt


CALIBRATION_RECORD_VERSION = 1


def _probability_pct(record: dict[str, Any]) -> float:
    for key in ("predicted_pct", "probability_pct", "forecast_pct"):
        if key not in record:
            continue
        value = float(record[key])
        if 0.0 <= value <= 1.0:
            value *= 100.0
        return max(0.0, min(100.0, value))
    raise ValueError(f"calibration record missing predicted_pct/probability_pct: {record!r}")


def _outcome(record: dict[str, Any]) -> int:
    value = record.get("outcome", record.get("result"))
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "win", "won", "yes", "sim"}:
            return 1
        if normalized in {"0", "false", "loss", "lost", "no", "nao", "não"}:
            return 0
    numeric = int(value)
    if numeric not in {0, 1}:
        raise ValueError(f"calibration outcome must be binary: {record!r}")
    return numeric


def evaluate_calibration(
    records: list[dict[str, Any]],
    *,
    bins: int = 10,
    target_ece: float = 0.05,
) -> dict[str, Any]:
    if not records:
        return {
            "total_predictions": 0,
            "brier_score": 0.0,
            "log_loss": 0.0,
            "expected_calibration_error": 0.0,
            "recommended_width_multiplier": 1.0,
            "bins": [],
        }
    bins = max(2, int(bins))
    target_ece = max(0.001, float(target_ece))

    parsed: list[tuple[float, int]] = []
    for record in records:
        parsed.append((_probability_pct(record) / 100.0, _outcome(record)))

    n = len(parsed)
    brier = sum((probability - outcome) ** 2 for probability, outcome in parsed) / n
    epsilon = 1e-12
    log_loss = -sum(
        outcome * math.log(max(epsilon, probability))
        + (1 - outcome) * math.log(max(epsilon, 1.0 - probability))
        for probability, outcome in parsed
    ) / n

    buckets = [
        {
            "index": index,
            "count": 0,
            "probability_sum": 0.0,
            "outcome_sum": 0.0,
        }
        for index in range(bins)
    ]
    for probability, outcome in parsed:
        index = min(bins - 1, max(0, int(probability * bins)))
        buckets[index]["count"] += 1
        buckets[index]["probability_sum"] += probability
        buckets[index]["outcome_sum"] += outcome

    ece = 0.0
    rendered_bins: list[dict[str, Any]] = []
    for bucket in buckets:
        count = int(bucket["count"])
        if count <= 0:
            rendered_bins.append(
                {
                    "index": bucket["index"],
                    "count": 0,
                    "mean_predicted_pct": 0.0,
                    "observed_rate_pct": 0.0,
                    "absolute_gap_pct": 0.0,
                }
            )
            continue
        mean_probability = float(bucket["probability_sum"]) / count
        observed_rate = float(bucket["outcome_sum"]) / count
        gap = abs(mean_probability - observed_rate)
        ece += (count / n) * gap
        rendered_bins.append(
            {
                "index": bucket["index"],
                "count": count,
                "mean_predicted_pct": round(mean_probability * 100.0, 1),
                "observed_rate_pct": round(observed_rate * 100.0, 1),
                "absolute_gap_pct": round(gap * 100.0, 1),
            }
        )

    recommended_multiplier = max(1.0, 1.0 + max(0.0, ece - target_ece) / target_ece)
    return {
        "total_predictions": n,
        "brier_score": round(brier, 4),
        "log_loss": round(log_loss, 4),
        "expected_calibration_error": round(ece, 4),
        "target_ece": round(target_ece, 4),
        "recommended_width_multiplier": round(recommended_multiplier, 3),
        "bins": rendered_bins,
    }


def _record_id(*parts: Any) -> str:
    clean = []
    for part in parts:
        text = str(part or "").strip().lower()
        text = "".join(char if char.isalnum() else "-" for char in text)
        text = "-".join(token for token in text.split("-") if token)
        clean.append(text or "unknown")
    return ":".join(clean)


def _pending_record(
    *,
    run_id: str,
    generated_at_iso: str,
    target_type: str,
    target: str,
    predicted_pct: float,
    artifact_path: str,
    phase: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": CALIBRATION_RECORD_VERSION,
        "id": _record_id(run_id, target_type, target),
        "run_id": run_id,
        "generated_at_iso": generated_at_iso,
        "target_type": target_type,
        "target": target,
        "phase": phase,
        "predicted_pct": round(max(0.0, min(100.0, float(predicted_pct))), 1),
        "outcome": None,
        "resolved": False,
        "artifact_path": artifact_path,
        "metadata": metadata or {},
    }


def prediction_records_from_bundle(
    bundle: Any,
    *,
    run_id: str,
    artifact_path: str = "",
) -> list[dict[str, Any]]:
    generated_at_iso = str(getattr(bundle, "generated_at_iso", "") or "")
    bundle_metadata = getattr(bundle, "metadata", {}) or {}
    monte_carlo_meta = bundle_metadata.get("monte_carlo") or {}
    funnel_source = "monte_carlo_funnel" if monte_carlo_meta.get("enabled") else "consensus_heuristic"
    records: list[dict[str, Any]] = []
    for stage, probability in (getattr(bundle, "stage_probabilities", {}) or {}).items():
        target_type = "title" if stage == "titulo" else "stage_reach"
        stage_metadata: dict[str, Any] = {"probability_source": funnel_source}
        if stage == "titulo":
            stage_metadata["room_consensus_title_pct"] = bundle_metadata.get("agent_title_consensus_pct")
        records.append(
            _pending_record(
                run_id=run_id,
                generated_at_iso=generated_at_iso,
                target_type=target_type,
                target=str(stage),
                phase=str(stage),
                predicted_pct=float(probability),
                artifact_path=artifact_path,
                metadata=stage_metadata,
            )
        )
    for match in [
        *(getattr(bundle, "group_matches", []) or []),
        *(getattr(bundle, "knockout_matches", []) or []),
    ]:
        phase = str(getattr(match, "phase", "") or "")
        target = f"{phase}: {getattr(match, 'brazil', 'Brasil')} x {getattr(match, 'opponent', '')}"
        records.append(
            _pending_record(
                run_id=run_id,
                generated_at_iso=generated_at_iso,
                target_type="match",
                target=target,
                phase=phase,
                predicted_pct=float(getattr(match, "brazil_pct", 0.0) or 0.0),
                artifact_path=artifact_path,
                metadata={
                    "draw_pct": getattr(match, "draw_pct", None),
                    "scenario_pct": getattr(match, "scenario_pct", None),
                    "most_likely": getattr(match, "most_likely", None),
                },
            )
        )
    return records


def _load_prediction_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        # Torn/corrupt write: cada run completaria o debate de US$6,43 e só então
        # estouraria. Isola o log ruim e segue como log vazio em vez de propagar.
        quarantine_corrupt(path)
        return []
    if not isinstance(payload, list):
        raise ValueError("calibration prediction log must be a JSON list")
    return [record for record in payload if isinstance(record, dict)]


def append_prediction_log(path: Path | str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = Path(path)
    existing = _load_prediction_log(path)
    by_id = {str(record.get("id")): record for record in existing if record.get("id")}
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        by_id[record_id] = record
    merged = list(by_id.values())
    atomic_write_text(path, json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True))
    return merged


def resolved_calibration_records(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    resolved: list[dict[str, Any]] = []
    pending_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("outcome", record.get("result")) is None:
            pending_count += 1
            continue
        resolved.append(record)
    return resolved, pending_count


def load_resolved_calibration_records(path: Path | str) -> tuple[list[dict[str, Any]], int, bool, int]:
    path = Path(path)
    input_exists = path.exists()
    records = _load_prediction_log(path) if input_exists else []
    resolved, pending_count = resolved_calibration_records(records)
    return resolved, pending_count, input_exists, len(records)
