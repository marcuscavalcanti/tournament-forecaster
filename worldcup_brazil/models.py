from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from worldcup_brazil.probabilities import MatchEstimate


@dataclass
class ReportBundle:
    generated_at_iso: str
    group_matches: list[MatchEstimate]
    knockout_matches: list[MatchEstimate]
    stage_probabilities: dict[str, float]
    final_rationale: str
    sources: list[str]
    agent_summaries: dict[str, str]
    warnings: list[str]
    custom_hashtag: str
    group_name: str = "GRUPO A"
    group_summary: str = ""
    stage_confidence_intervals: dict[str, tuple[float, float]] = field(default_factory=dict)
    debate_transcript: list[str] = field(default_factory=list)
    meeting_transcript: list[dict[str, Any]] = field(default_factory=list)
    source_plan_by_model: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    model_influence_pct: dict[str, float] = field(default_factory=dict)
    model_participation: dict[str, Any] = field(default_factory=dict)
    model_token_costs: dict[str, Any] = field(default_factory=dict)
    agent_effort_profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    model_predictions_no_opta: dict[str, dict[str, Any]] = field(default_factory=dict)
    model_self_identification: dict[str, dict[str, str]] = field(default_factory=dict)
    opta_benchmark: dict[str, Any] = field(default_factory=dict)
    model_vs_opta: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
