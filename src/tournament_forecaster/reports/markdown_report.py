"""Human-readable Markdown forecasts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape
import re

from ..domain import Forecast


_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?i)file://[^\s<>()\[\]{}|]+"),
    re.compile(r"(?i)(?<!\w)/var/home/[^/\s]+(?:/[^\s<>()\[\]{}|]*)?"),
    re.compile(r"(?i)(?<!\w)(?:/Users|/home)/[^/\s]+(?:/[^\s<>()\[\]{}|]*)?"),
    re.compile(r"(?i)(?<!\w)/root(?:/[^\s<>()\[\]{}|]*)?"),
    re.compile(
        r"(?i)(?<!\w)[A-Z]:[\\/]+Users[\\/]+[^\\/\s]+"
        r"(?:[\\/][^\s<>()\[\]{}|]*)*"
    ),
    re.compile(r"(?<!\w)~[\\/][^\s<>()\[\]{}|]+"),
)
_MARKDOWN_METACHARACTERS = frozenset("\\`*_{}[]()#+-.!|>")


def _markdown_text(value: str) -> str:
    normalized = " ".join(value.replace("\r\n", "\n").replace("\r", "\n").split())
    for pattern in _LOCAL_PATH_PATTERNS:
        normalized = pattern.sub("[redacted local path]", normalized)
    encoded = escape(normalized, quote=False)
    return "".join(
        f"\\{character}" if character in _MARKDOWN_METACHARACTERS else character
        for character in encoded
    )


def _display_team(forecast: Forecast, team_id: str) -> str:
    return _markdown_text(forecast.team_display_names.get(team_id, team_id))


def _percent(probability: float) -> str:
    return f"{probability:.1%}"


def _council_lines(forecast: Forecast) -> list[str]:
    council = forecast.council
    if not isinstance(council, Mapping):
        return []
    status = str(council.get("status", "unknown"))
    lines = ["", "## Multi-LLM Council Debrief", "", f"**Status:** {_markdown_text(status)}"]
    engine_weight = council.get("engine_weight")
    council_weight = council.get("council_weight")
    if isinstance(engine_weight, (int, float)) and isinstance(
        council_weight, (int, float)
    ):
        lines.append(
            f"**Blend policy:** {float(engine_weight):.0%} deterministic engine / "
            f"{float(council_weight):.0%} council consensus"
        )
    reason = council.get("reason")
    if isinstance(reason, str) and reason.strip():
        lines.append(f"**Decision detail:** {_markdown_text(reason)}")

    participants = council.get("participants")
    if (
        isinstance(participants, Sequence)
        and not isinstance(participants, (str, bytes, bytearray))
        and participants
    ):
        lines.extend(
            [
                "",
                "### Configured participants",
                "",
                "| Model slot | Provider / model | Effort |",
                "| --- | --- | --- |",
            ]
        )
        for participant in participants:
            if not isinstance(participant, Mapping):
                continue
            name = _markdown_text(str(participant.get("display_name", "unknown")))
            provider = _markdown_text(str(participant.get("provider", "unknown")))
            model = _markdown_text(str(participant.get("model", "unknown")))
            effort_value = participant.get("reasoning_effort")
            if isinstance(effort_value, str):
                effort = f"reasoning effort: {_markdown_text(effort_value)}"
            elif isinstance(participant.get("thinking_budget_tokens"), int):
                effort = (
                    "thinking budget: "
                    + str(participant["thinking_budget_tokens"])
                    + " tokens"
                )
            else:
                effort = "provider default"
            lines.append(f"| {name} | {provider} / {model} | {effort} |")

    consensus = council.get("consensus")
    if isinstance(consensus, Mapping):
        lines.extend(["", "### Consensus", ""])
        summary = consensus.get("summary")
        if isinstance(summary, str):
            lines.append(_markdown_text(summary))
        championship = consensus.get("championship_probability")
        if isinstance(championship, (int, float)):
            lines.append(
                f"Council-only championship position: **{_percent(float(championship))}**."
            )
        factors = consensus.get("key_factors")
        if isinstance(factors, Sequence) and not isinstance(
            factors, (str, bytes, bytearray)
        ):
            lines.extend(
                f"- {_markdown_text(str(factor))}"
                for factor in factors
                if isinstance(factor, str)
            )

    rounds = council.get("rounds")
    if isinstance(rounds, Sequence) and not isinstance(
        rounds, (str, bytes, bytearray)
    ):
        for round_value in rounds:
            if not isinstance(round_value, Mapping):
                continue
            lines.extend(
                [
                    "",
                    f"### Debate round {round_value.get('round', '?')}",
                    "",
                ]
            )
            opinions = round_value.get("opinions")
            if isinstance(opinions, Sequence) and not isinstance(
                opinions, (str, bytes, bytearray)
            ):
                for opinion in opinions:
                    if not isinstance(opinion, Mapping):
                        continue
                    label = _markdown_text(str(opinion.get("agent_id", "participant")))
                    summary = _markdown_text(str(opinion.get("summary", "No summary")))
                    title = opinion.get("championship_probability")
                    title_text = (
                        _percent(float(title))
                        if isinstance(title, (int, float))
                        else "not supplied"
                    )
                    lines.append(f"- **{label}:** title {title_text}. {summary}")
            failures = round_value.get("failures")
            if isinstance(failures, Sequence) and not isinstance(
                failures, (str, bytes, bytearray)
            ):
                for failure in failures:
                    if not isinstance(failure, Mapping):
                        continue
                    label = _markdown_text(str(failure.get("agent_id", "participant")))
                    category = _markdown_text(str(failure.get("category", "failure")))
                    detail = _markdown_text(str(failure.get("detail", "no detail")))
                    lines.append(f"- **{label}:** {category}. {detail}")

    lines.extend(
        [
            "",
            "Matchup probabilities remain engine-only because the council cannot "
            "rewrite legal opponents or bracket topology.",
            "Published confidence intervals carry engine sampling uncertainty only; "
            "council consensus is treated as fixed for those intervals.",
        ]
    )
    return lines


def render_markdown_report(forecast: Forecast) -> str:
    """Render a complete competition-neutral Markdown report."""

    tournament_name = _markdown_text(
        forecast.tournament_display_name or forecast.tournament_id
    )
    focus_name = _display_team(forecast, forecast.focus_team_id)
    lines = [
        f"# {tournament_name} forecast",
        "",
        f"**Focus team:** {focus_name}",
        f"**Run:** `{forecast.run_id}`",
        f"**Generated:** {_markdown_text(forecast.generated_at)}",
        "",
        "## Stage probabilities",
        "",
        "| Stage | Probability | Confidence interval |",
        "| --- | ---: | ---: |",
    ]
    for stage_id in forecast.stage_order:
        probability = forecast.stage_probabilities[stage_id]
        interval = forecast.confidence_intervals.get(stage_id)
        interval_text = (
            f"{_percent(interval[0])} to {_percent(interval[1])}"
            if interval is not None
            else "Not available"
        )
        lines.append(f"| {stage_id} | {_percent(probability)} | {interval_text} |")

    championship_interval = forecast.confidence_intervals.get(
        "championship_probability"
    )
    lines.extend(
        [
            "",
            "## Championship outlook",
            "",
            f"{focus_name} has a **{_percent(forecast.championship_probability)}** "
            "estimated championship probability.",
        ]
    )
    if championship_interval is not None:
        lines.append(
            "The confidence interval is "
            f"{_percent(championship_interval[0])} to "
            f"{_percent(championship_interval[1])}."
        )

    lines.extend(["", "## Matchup probabilities", ""])
    if forecast.matchup_probabilities:
        lines.extend(["| Stage | Opponent | Probability |", "| --- | --- | ---: |"])
        for matchup in forecast.matchup_probabilities:
            lines.append(
                f"| {matchup.stage_id} | "
                f"{_display_team(forecast, matchup.opponent_team_id)} | "
                f"{_percent(matchup.probability)} |"
            )
    else:
        lines.append("No focus-team matchup probabilities were produced.")

    lines.extend(_council_lines(forecast))

    if forecast.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {_markdown_text(warning)}" for warning in forecast.warnings)
    return "\n".join(lines) + "\n"
