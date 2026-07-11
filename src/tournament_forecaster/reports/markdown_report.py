"""Human-readable Markdown forecasts."""

from __future__ import annotations

from ..domain import Forecast


def _display_team(forecast: Forecast, team_id: str) -> str:
    return forecast.team_display_names.get(team_id, team_id)


def _percent(probability: float) -> str:
    return f"{probability:.1%}"


def render_markdown_report(forecast: Forecast) -> str:
    """Render a complete competition-neutral Markdown report."""

    tournament_name = forecast.tournament_display_name or forecast.tournament_id
    focus_name = _display_team(forecast, forecast.focus_team_id)
    lines = [
        f"# {tournament_name} forecast",
        "",
        f"**Focus team:** {focus_name}",
        f"**Run:** `{forecast.run_id}`",
        f"**Generated:** {forecast.generated_at}",
        "",
        "## Stage probabilities",
        "",
        "| Stage | Probability | Confidence interval |",
        "| --- | ---: | ---: |",
    ]
    for stage_id, probability in forecast.stage_probabilities.items():
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
        lines.extend(f"- {warning}" for warning in forecast.warnings)
    return "\n".join(lines) + "\n"
