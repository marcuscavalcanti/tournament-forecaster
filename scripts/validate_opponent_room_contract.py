from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    _opponent_debriefing_config,
    _opponent_debriefing_degraded_decision,
    _protagonist_question_prompt,
    load_config,
)


def _valid_opinion(agent: str) -> AgentOpinion:
    return AgentOpinion(
        agent=agent,
        title_pct=7.1,
        summary="Top-2 por fase validado com fonte viva.",
        answer="Japao e Holanda seguem como top-2 do slot oficial.",
        source_urls=[f"https://example.com/{agent.lower().split()[0]}"],
        scenario_probabilities={"Oitavas: Japao": 36.0},
        match_probabilities={"Oitavas: Japao": 71.0},
    )


def _prompt_contract() -> dict[str, object]:
    config = load_config(ROOT / "config/worldcup_brazil.example.json")
    opponent_config = _opponent_debriefing_config(config)
    prompt = _protagonist_question_prompt(
        config=opponent_config,
        protagonist="GPT 5.5",
        previous_turn=None,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    ).lower()
    main_prompt = _protagonist_question_prompt(
        config=config,
        protagonist="GPT 5.5",
        previous_turn=None,
        generated_at=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
    ).lower()
    return {
        "opponent_prompt_decisive": "top-2 por fase" in prompt,
        "mc_is_challengeable_baseline": "baseline auditável e desafiável" in prompt and "premissa forte" not in prompt,
        "main_room_not_contaminated": "top-2 por fase" not in main_prompt,
    }


def _degraded_gate_contract() -> dict[str, object]:
    base_config = {
        "opponent_debriefing_degraded_consensus_enabled": True,
        "opponent_debriefing_degraded_shadow_only": False,
        "meeting_min_participants": 3,
    }
    complete_transcript = [
        {
            "round": 3,
            "coverage": {"complete": True},
            "responses": [],
        }
    ]
    incomplete_transcript = [
        {
            "round": 3,
            "coverage": {"complete": False},
            "responses": [],
        }
    ]
    opinions = [_valid_opinion("GPT 5.5"), _valid_opinion("Perplexity Pro"), _valid_opinion("DeepSeek V4 Pro")]
    usable = _opponent_debriefing_degraded_decision(
        config=base_config,
        exit_status="degraded_last_valid",
        transcript=complete_transcript,
        final_opinions=opinions,
    )
    incomplete = _opponent_debriefing_degraded_decision(
        config=base_config,
        exit_status="degraded_last_valid",
        transcript=incomplete_transcript,
        final_opinions=opinions,
    )
    shadow = _opponent_debriefing_degraded_decision(
        config={**base_config, "opponent_debriefing_degraded_shadow_only": True},
        exit_status="degraded_last_valid",
        transcript=complete_transcript,
        final_opinions=opinions,
    )
    wrong_exit = _opponent_debriefing_degraded_decision(
        config=base_config,
        exit_status="max_rounds_no_consensus",
        transcript=complete_transcript,
        final_opinions=opinions,
    )
    return {
        "usable_when_complete_and_live": bool(usable["usable"]),
        "blocks_when_coverage_incomplete": not bool(incomplete["usable"])
        and "coverage_incomplete" in incomplete["reasons"],
        "shadow_measures_without_rewriting": bool(shadow["would_be_usable"]) and not bool(shadow["usable"]),
        "blocks_wrong_exit_status": not bool(wrong_exit["usable"])
        and "exit_status_max_rounds_no_consensus" in wrong_exit["reasons"],
        "usable_decision": usable,
        "incomplete_decision": incomplete,
        "shadow_decision": shadow,
        "wrong_exit_decision": wrong_exit,
    }


def main() -> int:
    prompt_contract = _prompt_contract()
    degraded_contract = _degraded_gate_contract()
    ok = all(
        bool(value)
        for value in (
            prompt_contract["opponent_prompt_decisive"],
            prompt_contract["mc_is_challengeable_baseline"],
            prompt_contract["main_room_not_contaminated"],
            degraded_contract["usable_when_complete_and_live"],
            degraded_contract["blocks_when_coverage_incomplete"],
            degraded_contract["shadow_measures_without_rewriting"],
            degraded_contract["blocks_wrong_exit_status"],
        )
    )
    print(
        json.dumps(
            {
                "ok": ok,
                "prompt_contract": prompt_contract,
                "degraded_gate_contract": degraded_contract,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
