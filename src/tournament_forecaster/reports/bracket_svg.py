"""Self-contained SVG rendering for a focus team's forecast path."""

from __future__ import annotations

from html import escape

from ..domain import Forecast


def _label(value: str, *, limit: int = 42) -> str:
    compact = " ".join(value.split())
    if len(compact) > limit:
        compact = f"{compact[: limit - 3]}..."
    return escape(compact, quote=True)


def render_bracket_svg(forecast: Forecast) -> str:
    """Render stable, dependency-free SVG forecast summary XML."""

    tournament_name = forecast.tournament_display_name or forecast.tournament_id
    focus_name = forecast.team_display_names.get(
        forecast.focus_team_id,
        forecast.focus_team_id,
    )
    stages = list(forecast.stage_probabilities.items())[:6]
    stage_rows: list[str] = []
    for index, (stage_id, probability) in enumerate(stages):
        y = 196 + index * 44
        width = round(520 * probability, 2)
        stage_rows.extend(
            [
                f'  <text class="stage" x="72" y="{y}">{_label(stage_id)}</text>',
                f'  <rect class="track" x="300" y="{y - 20}" width="520" height="24" rx="4"/>',
                f'  <rect class="bar" x="300" y="{y - 20}" width="{width}" height="24" rx="4"/>',
                f'  <text class="probability" x="836" y="{y}">{probability:.1%}</text>',
            ]
        )
    if len(forecast.stage_probabilities) > len(stages):
        stage_rows.append(
            f'  <text class="note" x="72" y="476">+{len(forecast.stage_probabilities) - len(stages)} more stages</text>'
        )

    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="540" viewBox="0 0 960 540" role="img" aria-labelledby="title desc">',
            f"  <title id=\"title\">{_label(tournament_name)} forecast</title>",
            f"  <desc id=\"desc\">Stage and championship probabilities for {_label(focus_name)}</desc>",
            "  <style>",
            "    .background { fill: #f7f8fa; }",
            "    .header { fill: #172b3a; }",
            "    .title { fill: #ffffff; font: 700 28px sans-serif; }",
            "    .subtitle { fill: #d9e2e8; font: 16px sans-serif; }",
            "    .stage { fill: #172b3a; font: 600 16px sans-serif; }",
            "    .track { fill: #d9e2e8; }",
            "    .bar { fill: #2e7d63; }",
            "    .probability { fill: #172b3a; font: 700 16px sans-serif; text-anchor: end; }",
            "    .championship { fill: #b45309; font: 700 18px sans-serif; }",
            "    .note { fill: #53636f; font: 14px sans-serif; }",
            "  </style>",
            '  <rect class="background" width="960" height="540"/>',
            '  <rect class="header" width="960" height="126"/>',
            f'  <text class="title" x="56" y="52">{_label(tournament_name)}</text>',
            f'  <text class="subtitle" x="56" y="86">Focus: {_label(focus_name)}</text>',
            f'  <text class="championship" x="56" y="148">Championship probability: {forecast.championship_probability:.1%}</text>',
            *stage_rows,
            f'  <text class="note" x="56" y="516">Run {_label(forecast.run_id)}</text>',
            "</svg>",
            "",
        ]
    )
