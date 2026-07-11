"""Human-readable Markdown forecasts."""

from __future__ import annotations

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

    if forecast.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {_markdown_text(warning)}" for warning in forecast.warnings)
    return "\n".join(lines) + "\n"
