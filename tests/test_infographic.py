import json
from pathlib import Path
from types import SimpleNamespace

from worldcup_brazil.infographic import (
    _match_label,
    collect_recent_infographic_bundles,
    render_html_to_png_with_chrome,
    render_simulation_review_infographic_html,
    render_svg_to_png_with_chrome,
    render_simulation_review_infographic_svg,
)


def _bundle(stamp: str, *, title: float, final: float, messages: int, valid: int, invalid: int):
    return SimpleNamespace(
        generated_at_iso=f"{stamp}T12:00:00+00:00",
        stage_probabilities={"quartas": 40.0, "semifinal": 20.0, "final": final, "titulo": title},
        model_participation={
            "total_messages": messages,
            "valid_messages": valid,
            "invalid_responses": invalid,
            "total_rounds": 6,
            "by_model": {
                "Opus 4.8": {"valid_responses": 4, "invalid_responses": 0, "messages": 6},
                "GPT 5.5": {"valid_responses": 3, "invalid_responses": 1, "messages": 5},
            },
        },
        model_influence_pct={"Opus 4.8": 27.0, "GPT 5.5": 23.0},
        model_token_costs={"total": {"total_tokens": 100_000, "cost_usd": 2.5, "calls": 10, "fallback_calls": 1}},
        group_matches=[
            SimpleNamespace(opponent="Marrocos", brazil_pct=59.0, draw_pct=24.0, opponent_pct=17.0),
            SimpleNamespace(opponent="Haiti", brazil_pct=92.0, draw_pct=8.0, opponent_pct=0.0),
            SimpleNamespace(opponent="Escócia", brazil_pct=73.0, draw_pct=19.0, opponent_pct=8.0),
        ],
        knockout_matches=[
            SimpleNamespace(
                phase="16 avos",
                opponent="Japão",
                scenario_pct=100.0,
                brazil_pct=69.8,
                most_likely=True,
                match_date="2026-06-29",
            )
        ],
        metadata={
            "market_title_challenge": {"status": "within_threshold"},
            "parallel_opponent_debriefing": {"exit_status": "consensus", "usable_for_main_room": True},
            "group_state": {
                "completed_results": [
                    {"score": "Brasil 1-1 Marrocos"},
                    {"score": "Brasil 3-0 Haiti"},
                    {"score": "Escócia 0-3 Brasil"},
                ]
            },
        },
        warnings=[],
        meeting_transcript=[],
    )


def test_render_simulation_review_infographic_svg_summarizes_runs_and_model_metrics() -> None:
    svg = render_simulation_review_infographic_svg(
        [
            _bundle("2026-06-15", title=4.1, final=9.6, messages=24, valid=24, invalid=0),
            _bundle("2026-06-18", title=5.1, final=10.7, messages=24, valid=22, invalid=2),
            _bundle("2026-06-24", title=3.5, final=7.8, messages=31, valid=25, invalid=6),
            _bundle("2026-06-28", title=7.8, final=16.2, messages=31, valid=27, invalid=4),
        ]
    )

    assert svg.startswith("<svg")
    assert "CopaComAchismo" in svg
    assert "Review das Simulações" in svg
    assert "15/06" in svg
    assert "28/06" in svg
    assert "7,8%" in svg
    assert "110" in svg
    assert "Opus 4.8" in svg
    assert "27,0%" in svg
    assert "Japão 100%" in svg


def test_render_simulation_review_infographic_html_prioritizes_model_ranking_and_run_leaders() -> None:
    html = render_simulation_review_infographic_html(
        [
            _bundle("2026-06-15", title=4.1, final=9.6, messages=24, valid=24, invalid=0),
            _bundle("2026-06-18", title=5.1, final=10.7, messages=24, valid=22, invalid=2),
            _bundle("2026-06-24", title=3.5, final=7.8, messages=31, valid=25, invalid=6),
            _bundle("2026-06-28", title=7.8, final=16.2, messages=31, valid=27, invalid=4),
        ]
    )

    assert html.startswith("<!doctype html>")
    assert "Ranking geral dos modelos" in html
    assert "Mais influente por run" in html
    assert "Opus 4.8" in html
    assert "GPT 5.5" in html
    assert "Mensagens válidas" in html
    assert "Tokens usados" in html
    assert "Índice de acerto por jogo" in html
    assert "Brasil 1-1 Marrocos" in html
    assert "24%" in html
    assert "2/3 direção" in html
    assert "Japão 100%" in html
    assert "Sem dados empilhados" not in html
    assert html.count('<div class="run-date">') == 4
    assert ".run-grid { display: grid; grid-template-columns: repeat(5, 1fr);" in html


def test_infographic_uses_next_future_knockout_after_round_of_32_is_completed() -> None:
    previous = _bundle("2026-06-28", title=7.8, final=16.2, messages=31, valid=27, invalid=4)
    previous.group_matches = [
        SimpleNamespace(opponent="Marrocos", match_date="13/jun"),
        SimpleNamespace(opponent="Haiti", match_date="19/jun"),
        SimpleNamespace(opponent="Escócia", match_date="24/jun"),
    ]
    previous.knockout_matches = [
        SimpleNamespace(phase="16 avos", opponent="Japão", scenario_pct=100.0, brazil_pct=69.8, opponent_pct=30.2, most_likely=True, match_date="2026-06-29"),
        SimpleNamespace(phase="Oitavas", opponent="Noruega", scenario_pct=57.7, brazil_pct=75.2, most_likely=True, match_date="2026-07-05"),
    ]
    bundle = _bundle("2026-07-04", title=11.7, final=23.5, messages=28, valid=24, invalid=4)
    bundle.group_matches = [
        SimpleNamespace(opponent="Marrocos", match_date="13/jun"),
        SimpleNamespace(opponent="Haiti", match_date="19/jun"),
        SimpleNamespace(opponent="Escócia", match_date="24/jun"),
    ]
    bundle.knockout_matches = [
        SimpleNamespace(phase="16 avos", opponent="Japão", scenario_pct=100.0, brazil_pct=100.0, opponent_pct=0.0, most_likely=True, match_date="2026-06-29"),
        SimpleNamespace(phase="Oitavas", opponent="Noruega", scenario_pct=100.0, brazil_pct=74.1, most_likely=True, match_date="2026-07-05"),
        SimpleNamespace(phase="Quartas", opponent="Inglaterra", scenario_pct=71.9, brazil_pct=50.3, most_likely=True, match_date="2026-07-11"),
    ]
    bundle.metadata["monte_carlo"] = {
        "completed_knockout_matches": {
            "matches": [
                {"phase": "16 avos", "date": "2026-06-29", "score": "Brasil 2-1 Japão", "winner": "Brasil"}
            ]
        }
    }

    html = render_simulation_review_infographic_html([previous, bundle])
    svg = render_simulation_review_infographic_svg([previous, bundle])

    assert "Brasil x Noruega" in html
    assert "Noruega 100%; Brasil 74,1%" in html
    assert html.count("Brasil x Japão") == 2
    assert "Japão 100%" not in html
    assert "Brasil 2-1 Japão" in html
    assert "Brasil 2-1 Japão</td><td>Vitória</td><td>69,8%</td>" in html
    assert "Noruega 100%; Brasil 74,1%" in svg


def test_collect_recent_infographic_bundles_keeps_one_latest_bundle_per_brazil_round(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()

    def write_bundle(day: str, *, title: float = 1.0) -> Path:
        path = output_dir / f"linkedin_brazil_{day}.json"
        path.write_text(
            json.dumps(
                {
                    "bundle": {
                        "generated_at_iso": f"{day}T12:00:00+00:00",
                        "stage_probabilities": {"titulo": title},
                        "group_matches": [
                            {"opponent": "Marrocos", "match_date": "13/jun"},
                            {"opponent": "Haiti", "match_date": "19/jun"},
                            {"opponent": "Escócia", "match_date": "24/jun"},
                        ],
                        "knockout_matches": [
                            {
                                "phase": "16 avos",
                                "opponent": "Japão",
                                "scenario_pct": 100.0,
                                "brazil_pct": 69.8,
                                "most_likely": True,
                                "match_date": "2026-06-29",
                            },
                            {
                                "phase": "Oitavas",
                                "opponent": "Noruega",
                                "scenario_pct": 100.0,
                                "brazil_pct": 74.1,
                                "most_likely": True,
                                "match_date": "2026-07-05",
                            },
                        ],
                        "metadata": {
                            "completed_knockout_matches": {
                                "matches": [
                                    {
                                        "phase": "16 avos",
                                        "date": "2026-06-29",
                                        "score": "Brasil 2-1 Japão",
                                        "winner": "Brasil",
                                    }
                                ]
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        return path

    for day in ["2026-06-11", "2026-06-13", "2026-06-18", "2026-06-24", "2026-06-27"]:
        write_bundle(day)
    write_bundle("2026-06-28", title=7.8)
    current_path = write_bundle("2026-07-04", title=11.7)

    bundles = collect_recent_infographic_bundles(output_dir, current_path, limit=5)

    assert [bundle.generated_at_iso[:10] for bundle in bundles] == [
        "2026-06-13",
        "2026-06-18",
        "2026-06-24",
        "2026-06-28",
        "2026-07-04",
    ]
    assert [_match_label(bundle) for bundle in bundles] == [
        "Brasil x Marrocos",
        "Brasil x Haiti",
        "Brasil x Escócia",
        "Brasil x Japão",
        "Brasil x Noruega",
    ]


def test_collect_recent_infographic_bundles_falls_back_to_recent_files_without_round_metadata(tmp_path: Path) -> None:
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    for day in ["2026-06-15", "2026-06-18", "2026-06-24", "2026-06-27"]:
        (output_dir / f"linkedin_brazil_{day}.json").write_text(
            json.dumps({"bundle": {"generated_at_iso": f"{day}T12:00:00+00:00", "stage_probabilities": {"titulo": 1}}}),
            encoding="utf-8",
        )
    current_path = output_dir / "linkedin_brazil_2026-06-28.json"
    current_path.write_text(
        json.dumps({"bundle": {"generated_at_iso": "2026-06-28T12:00:00+00:00", "stage_probabilities": {"titulo": 2}}}),
        encoding="utf-8",
    )

    bundles = collect_recent_infographic_bundles(output_dir, current_path, limit=4)

    assert [bundle.generated_at_iso[:10] for bundle in bundles] == [
        "2026-06-18",
        "2026-06-24",
        "2026-06-27",
        "2026-06-28",
    ]


def test_render_svg_to_png_with_chrome_is_best_effort_when_chrome_missing(tmp_path: Path) -> None:
    svg_path = tmp_path / "chart.svg"
    png_path = tmp_path / "chart.png"
    svg_path.write_text("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='10'></svg>", encoding="utf-8")

    ok = render_svg_to_png_with_chrome(svg_path, png_path, chrome_path=str(tmp_path / "missing-chrome"))

    assert ok is False
    assert not png_path.exists()


def test_render_html_to_png_with_chrome_is_best_effort_when_chrome_missing(tmp_path: Path) -> None:
    html_path = tmp_path / "chart.html"
    png_path = tmp_path / "chart.png"
    html_path.write_text("<!doctype html><html><body>ok</body></html>", encoding="utf-8")

    ok = render_html_to_png_with_chrome(html_path, png_path, chrome_path=str(tmp_path / "missing-chrome"))

    assert ok is False
    assert not png_path.exists()
