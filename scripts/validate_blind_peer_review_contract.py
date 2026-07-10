from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    _aggregate_blind_peer_reviews,
    _blind_peer_review_exit_blocked_reasons,
    _blind_peer_review_public_text,
)


def _mask_contract() -> dict[str, object]:
    text = (
        "GPT/OpenAI citou gpt-5.5; Claude Opus e Anthropic divergiram; "
        "Gemini gemini-flash-latest e DeepSeek deepseek-v4-pro concordaram; "
        "Perplexity sonar-pro trouxe outra fonte. Versões 5.5, 4.8 e V4 Pro apareceram. "
        "Brasil x Marrocos tem odds 1.85, Elo 1850, xG 1.4 e Google Trends como fonte contextual."
    )
    public = _blind_peer_review_public_text(
        text,
        agent_slots=["GPT 5.5", "Opus 4.8", "Perplexity Pro", "DeepSeek V4 Pro", "Gemini Pro"],
        mask_terms=[
            "openai",
            "gpt-5.5",
            "claude",
            "opus",
            "anthropic",
            "gemini-flash-latest",
            "deepseek-v4-pro",
            "sonar-pro",
        ],
    ).lower()
    forbidden = ["gpt", "openai", "claude", "opus", "anthropic", "gemini", "deepseek", "perplexity", "sonar"]
    version_tokens = ["5.5", "4.8", "v4"]
    preserved = ["brasil", "marrocos", "1.85", "1850", "xg 1.4", "google trends"]
    return {
        "model_tokens_masked": not any(token in public for token in forbidden),
        "version_tokens_masked": not any(token in public for token in version_tokens),
        "football_context_preserved": all(token in public for token in preserved),
        "public_text": public,
    }


def _leakage_contract() -> dict[str, object]:
    positions = [
        {"position_id": "position_1", "_agent": "GPT 5.5"},
        {"position_id": "position_2", "_agent": "Perplexity Pro"},
        {"position_id": "position_3", "_agent": "Gemini Pro"},
    ]
    payloads = {
        "GPT 5.5": {
            "scores": [
                {"position_id": "position_1", "score": 1.0, "accepted": True},
                {"position_id": "position_2", "score": 0.72, "accepted": True},
                {"position_id": "position_3", "score": 0.72, "accepted": True},
            ]
        },
        "Perplexity Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.72, "accepted": True},
                {"position_id": "position_2", "score": 1.0, "accepted": True},
                {"position_id": "position_3", "score": 0.72, "accepted": True},
            ]
        },
        "Gemini Pro": {
            "scores": [
                {"position_id": "position_1", "score": 0.72, "accepted": True},
                {"position_id": "position_2", "score": 0.72, "accepted": True},
                {"position_id": "position_3", "score": 1.0, "accepted": True},
            ]
        },
    }
    reviews = [
        AgentOpinion(
            agent=agent,
            title_pct=10.0,
            summary="Revisão cega.",
            answer=json.dumps(payload),
            raw_text=json.dumps(payload),
            source_urls=["https://example.com/blind-review"],
        )
        for agent, payload in payloads.items()
    ]
    metadata = _aggregate_blind_peer_reviews(
        reviews,
        positions=positions,
        config={
            "blind_peer_review_enabled": True,
            "blind_peer_review_shadow_only": False,
            "blind_peer_review_acceptance_threshold": 0.72,
            "blind_peer_review_max_self_preference_leakage": 0.20,
        },
    )
    leakage = metadata["self_preference_leakage"]
    return {
        "value": leakage["value"],
        "threshold": leakage["threshold"],
        "exceeds_threshold": leakage["exceeds_threshold"],
        "metadata": metadata,
    }


def _exit_gate_contract(leakage_metadata: dict[str, object]) -> dict[str, object]:
    config = {
        "blind_peer_review_enabled": True,
        "blind_peer_review_shadow_only": False,
        "blind_peer_review_max_self_preference_leakage": 0.20,
    }
    missing_acceptance = _blind_peer_review_exit_blocked_reasons(
        {"blind_peer_review": {"blind_acceptance_count": 0, "errors": []}},
        config=config,
        room_quorum=2,
    )
    leakage_review = dict(leakage_metadata["metadata"])
    leakage_review["blind_acceptance_count"] = 2
    leakage_blocked = _blind_peer_review_exit_blocked_reasons(
        {"blind_peer_review": leakage_review},
        config=config,
        room_quorum=2,
    )
    return {
        "blocks_on_acceptance_missing": "blind_acceptance_missing" in missing_acceptance,
        "blocks_on_leakage": "self_preference_leakage_high" in leakage_blocked,
        "missing_acceptance_reasons": missing_acceptance,
        "leakage_reasons": leakage_blocked,
    }


def main() -> int:
    mask = _mask_contract()
    leakage = _leakage_contract()
    exit_gate = _exit_gate_contract(leakage)
    ok = (
        bool(mask["model_tokens_masked"])
        and bool(mask["version_tokens_masked"])
        and bool(mask["football_context_preserved"])
        and bool(leakage["exceeds_threshold"])
        and bool(exit_gate["blocks_on_acceptance_missing"])
        and bool(exit_gate["blocks_on_leakage"])
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "mask_contract": mask,
                "leakage_contract": leakage,
                "exit_gate_contract": exit_gate,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
