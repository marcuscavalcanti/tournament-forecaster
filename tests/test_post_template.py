from datetime import date
from types import SimpleNamespace

import pytest

from worldcup_brazil.post_template import (
    MAX_POST_CHARS,
    apply_editor_append,
    render_template_post,
    validate_template_post,
)


def _match(phase, opponent, *, scenario, brazil, opp, venue, date_, most_likely):
    return SimpleNamespace(
        phase=phase, opponent=opponent, scenario_pct=scenario, brazil_pct=brazil,
        opponent_pct=opp, venue=venue, match_date=date_, most_likely=most_likely,
        draw_pct=None,
    )


def _bundle():
    group = [
        SimpleNamespace(opponent="Marrocos", brazil_pct=59.0, draw_pct=24.0, opponent_pct=17.0,
                        match_date="13/jun", venue="Nova Jersey", phase="Fase de grupos", most_likely=None),
        SimpleNamespace(opponent="Haiti", brazil_pct=92.0, draw_pct=8.0, opponent_pct=0.0,
                        match_date="19/jun", venue="Filadélfia", phase="Fase de grupos", most_likely=None),
        SimpleNamespace(opponent="Escócia", brazil_pct=73.0, draw_pct=19.0, opponent_pct=8.0,
                        match_date="24/jun", venue="Miami", phase="Fase de grupos", most_likely=None),
    ]
    knockout = []
    for phase, ml, alt in [
        ("16 avos", ("Japão", 33.6, 71.9, 28.1), ("Holanda", 29.3, 52.3, 47.7)),
        ("Oitavas", ("Equador", 16.1, 75.6, 24.4), ("Noruega", 14.8, 75.4, 24.6)),
        ("Quartas", ("Inglaterra", 31.3, 49.3, 50.7), ("Croácia", 9.5, 61.5, 38.5)),
        ("Semifinal", ("Argentina", 23.2, 46.3, 53.7), ("Portugal", 20.3, 49.0, 51.0)),
        ("Final", ("França", 17.9, 42.1, 57.9), ("Espanha", 14.0, 41.3, 58.7)),
    ]:
        knockout.append(_match(phase, ml[0], scenario=ml[1], brazil=ml[2], opp=ml[3],
                               venue="Estádio X", date_="2026-06-29", most_likely=True))
        knockout.append(_match(phase, alt[0], scenario=alt[1], brazil=alt[2], opp=alt[3],
                               venue="Estádio Y", date_="2026-06-29", most_likely=False))
    transcript = [
        {
            "round": 4,
            "responses": [
                {"agent": "GPT 5.5",
                 "answer": "Concordo com o líder, mas fui conferir antes. Rodrygo está fora do ano por lesão, então o sinal de '+3,2 por desempenho recente' não pode ficar no Modelo Principal.",
                 "disagreed": True, "removed_from_main": False, "used_fallback": False},
            ],
        },
        {
            "round": 6,
            "responses": [
                {"agent": "Opus 4.8",
                 "answer": "Discordo do protagonista. Cotações de gol assumem que o jogador joga: elas não medem se Neymar está apto a entrar em campo.",
                 "disagreed": True, "removed_from_main": False, "used_fallback": False},
            ],
        },
    ]
    return SimpleNamespace(
        generated_at_iso="2026-06-11T15:26:34+00:00",
        group_matches=group,
        knockout_matches=knockout,
        stage_probabilities={"quartas": 47.5, "semifinal": 29.0, "final": 16.3, "titulo": 8.6},
        group_summary="Brasil em 1º: ~66% (faixa 61%-72%).",
        metadata={"monte_carlo": {"stage_probabilities": {"16_avos": 99.2, "oitavas": 67.8}, "iterations": 40000}},
        meeting_transcript=transcript,
        model_participation={"total_messages": 31, "total_rounds": 8},
        model_influence_pct={"GPT 5.5": 29.9, "DeepSeek V4 Pro": 34.8, "Perplexity Pro": 0.5},
        model_token_costs={"total": {"cost_usd": 6.43}},
        sources=["https://example.com"] * 12,
    )


def test_template_post_fills_all_placeholders_within_limit() -> None:
    text = render_template_post(_bundle(), post_index=1, run_date=date(2026, 6, 11))

    validate_template_post(text, _bundle())
    assert len(text) <= MAX_POST_CHARS
    assert text.startswith("⚽ 13/jun · Brasil x Marrocos · 59/24/17 · Hexa: 8,6%")
    assert "\n\nPRIMEIRO PALPITE DA SÉRIE: Brasil x Marrocos\n" in text
    assert "A ESTREIA (sábado, Nova Jersey):" in text
    assert "BRASIL x MARROCOS — 59% vitória | 24% empate | 17% derrota" in text
    assert "➡️ 16 AVOS (29/jun) - Estádio X" in text
    assert "Mais provável: Japão (34% de chance desse cruzamento) → Brasil passa: 72% | Japão: 28%\n" in text
    assert "➡️ FINAL (29/jun) - Estádio X" in text
    assert "França (18% de chance desse cruzamento) → Brasil HEXA: 42% | França: 58%" in text
    assert "Alternativa: Espanha (14%) → Brasil HEXA: 41% | Espanha: 59%" in text
    assert "levanta a taça em 8,6% 🏆." in text
    assert "Propositalmente, o modelo da OPTA" in text
    assert "https://www.linkedin.com/posts/marcuscavalcanti_copacomachismo" in text
    assert chr(34) not in text.split("DOIS BASTIDORES")[1].split("⚠️")[0]
    assert "Alternativa: Holanda (29%) → Brasil: 52% | Holanda: 48%\n" in text
    assert "Holanda: 48% - " not in text
    assert "  " not in text.replace("\n", "|")
    assert "16 avos em 99%" in text
    assert "levanta a taça em 8,6%" in text
    assert "📊 NÚMEROS DA RODADA:" in text
    assert text.split("NÚMEROS DA RODADA:")[1].split("⚠️")[0].count("• ") >= 1
    assert "Rodada 6 — Opus 4.8 bateu de frente: Cotações de gol assumem que o jogador joga" in text
    assert "Rodada 4 — GPT 5.5 foi conferir antes: Rodrygo está fora do ano por lesão" in text
    assert "Modelo Principal" not in text.split("DOIS BASTIDORES")[1].split("⚠️")[0]
    assert "Próximo post: véspera/dia de Brasil x Haiti (19/jun), com o mapa recalculado." in text
    assert "Próximo post: véspera/dia de Brasil x Marrocos (13/jun)" not in text
    assert "#Hexa #WorldCup #CopaDoMundo" in text
    assert "Galera do bolão: 59 / 24 / 17." in text


def test_template_post_uses_monte_carlo_group_state_instead_of_static_summary() -> None:
    bundle = _bundle()
    bundle.group_summary = "Brasil em 1º: ~11% (valor estático obsoleto)."
    bundle.metadata["monte_carlo"]["group_state"] = {
        "brazil_first_pct": 42.4,
        "completed_results": [
            {"score": "Brasil 1-1 Marrocos"},
            {"score": "Escócia 1-0 Haiti"},
        ],
    }

    text = render_template_post(bundle, post_index=2, run_date=date(2026, 6, 15))

    assert "Brasil termina em 1º do grupo em 42%" in text
    assert "Brasil 1-1 Marrocos" in text
    assert "Escócia 1-0 Haiti" in text
    assert "11%" not in text


def test_template_post_discloses_market_title_challenge_without_repricing_title() -> None:
    bundle = _bundle()
    bundle.stage_probabilities["titulo"] = 4.5
    bundle.metadata["market_title_challenge"] = {
        "triggered": True,
        "model_title_pct": 4.5,
        "market_low_pct": 8.5,
        "market_high_pct": 11.0,
        "decision": "mantem_funil_60_40_mercado_como_desafio",
    }

    text = render_template_post(bundle, post_index=3, run_date=date(2026, 6, 16))

    assert "levanta a taça em 4,5%" in text
    assert "Mercado desafia o Hexa: funil 60/40 4,5%; mercado 8,5%-11%. Mantive funil." in text
    assert "levanta a taça em 8,5%" not in text


def test_backstage_section_omitted_when_beats_lack_substance() -> None:
    bundle = _bundle()
    bundle.meeting_transcript = [
        {
            "round": 2,
            "responses": [
                {"agent": "Gemini Pro", "answer": "Concordo com o protagonista, sem ressalvas relevantes.",
                 "disagreed": True, "removed_from_main": False, "used_fallback": False},
            ],
        }
    ]

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    validate_template_post(text, bundle)
    assert "DOIS BASTIDORES" not in text
    assert "1️⃣" not in text
    assert "📊 NÚMEROS DA RODADA:" in text
    assert text.split("NÚMEROS DA RODADA:")[1].split("⚠️")[0].count("• ") >= 3


def test_template_post_uses_next_unplayed_game_and_ordinal() -> None:
    text = render_template_post(_bundle(), post_index=2, run_date=date(2026, 6, 18))

    assert text.startswith("⚽ 19/jun · Brasil x Haiti · 92/8/0 · Hexa: 8,6%")
    assert "\n\nSEGUNDO PALPITE DA SÉRIE: Brasil x Haiti\n" in text
    assert "O PRÓXIMO JOGO (sexta-feira, Filadélfia):" in text
    assert "BRASIL x HAITI — 92% vitória | 8% empate" in text
    assert "Próximo post: véspera/dia de Brasil x Escócia (24/jun), com o mapa recalculado." in text
    assert "derrota" not in text.split("O CAMINHO")[0]


def test_template_post_derives_group_loss_from_win_and_draw_when_bundle_opponent_pct_is_stale() -> None:
    bundle = _bundle()
    haiti = bundle.group_matches[1]
    haiti.opponent_pct = 8.0

    text = render_template_post(bundle, post_index=2, run_date=date(2026, 6, 18))

    validate_template_post(text, bundle)
    assert text.startswith("⚽ 19/jun · Brasil x Haiti · 92/8/0 · Hexa: 8,6%")
    assert "BRASIL x HAITI — 92% vitória | 8% empate" in text
    assert "8% derrota" not in text.split("O CAMINHO")[0]
    assert "Galera do bolão: 92 / 8 / 0." in text


def test_template_post_discloses_active_models_and_opponent_room_fallback() -> None:
    bundle = _bundle()
    bundle.source_plan_by_model = {
        "Opus 4.8": {},
        "GPT 5.5": {},
        "DeepSeek V4 Pro": {},
        "Gemini Pro": {},
    }
    bundle.model_influence_pct = {
        "Opus 4.8": 0.5,
        "GPT 5.5": 0.5,
        "Perplexity Pro": 35.0,
        "DeepSeek V4 Pro": 34.0,
        "Gemini Pro": 30.0,
    }
    bundle.model_participation["last_consensus_participants"] = [
        "Opus 4.8",
        "GPT 5.5",
        "DeepSeek V4 Pro",
        "Gemini Pro",
    ]
    bundle.metadata["removed_agent_slots"] = ["Perplexity Pro"]
    bundle.metadata["removed_agent_reasons"] = {
        "Perplexity Pro": "fontes fora do escopo estatístico/qualitativo do futebol competitivo",
    }
    bundle.metadata["parallel_opponent_debriefing"] = {
        "enabled": True,
        "usable_for_main_room": False,
        "exit_status": "max_rounds_no_consensus",
    }

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    validate_template_post(text, bundle)
    assert "4 modelos ativos (Opus 4.8, GPT 5.5, DeepSeek V4 Pro e Gemini Pro)" in text
    assert "Perplexity Pro saiu no planejamento" in text
    assert "fontes fora do escopo competitivo" in text
    assert "os 5 modelos pesquisam" not in text
    assert "cruzamentos sem consenso lateral; usei Monte Carlo/bracket" in text


def test_template_post_discloses_shallow_opponent_room_phase_coverage() -> None:
    bundle = _bundle()
    bundle.metadata["parallel_opponent_debriefing"] = {
        "enabled": True,
        "usable_for_main_room": True,
        "exit_status": "consensus",
        "phase_coverage_sufficient": False,
        "phase_coverage": {
            "16_avos": 1,
            "oitavas": 0,
            "quartas": 0,
            "semifinal": 0,
            "final": 0,
        },
    }

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    validate_template_post(text, bundle)
    assert "sala lateral validou o mapa" in text
    assert "ranking de adversários por fase segue ancorado no Monte Carlo" in text


def test_round_stats_reports_valid_message_count_when_responses_were_removed() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 45,
        "total_rounds": 15,
        "valid_messages": 32,
        "invalid_responses": 13,
        "last_consensus_participants": ["DeepSeek V4 Pro", "Perplexity Pro", "Gemini Pro"],
    }
    bundle.model_influence_pct = {
        "DeepSeek V4 Pro": 34.9,
        "Perplexity Pro": 35.0,
        "Gemini Pro": 30.1,
    }

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    stats = text.split("📊 NÚMEROS DA RODADA:\n", 1)[1].split("\n\n⚠️", 1)[0]
    assert "32 válidas" in stats
    assert "13 removidas" in stats
    assert "Gemini teve menor influência" in stats
    assert "quase não moveu (30,1%)" not in stats


def test_post_says_models_ratified_mc_when_consensus_equals_mc() -> None:
    bundle = _bundle()
    bundle.stage_probabilities["titulo"] = 5.1
    bundle.metadata["agent_title_consensus_pct"] = 5.1
    bundle.metadata["monte_carlo"]["stage_probabilities"]["titulo"] = 5.1
    bundle.metadata["numeric_chairman"] = {
        "stage_probability_blend": {"monte_carlo_weight": 0.6, "model_weight": 0.4},
    }

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    assert "os modelos ratificaram o Monte Carlo" in text


def test_post_says_models_repriced_when_consensus_differs_from_mc() -> None:
    bundle = _bundle()
    bundle.stage_probabilities["titulo"] = 5.5
    bundle.metadata["agent_title_consensus_pct"] = 6.2
    bundle.metadata["monte_carlo"]["stage_probabilities"]["titulo"] = 5.1
    bundle.metadata["numeric_chairman"] = {
        "stage_probability_blend": {"monte_carlo_weight": 0.6, "model_weight": 0.4},
    }

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    assert "os modelos moveram o funil" in text


def test_round_stats_prioritize_discussion_profile_over_cost() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 24,
        "total_rounds": 6,
        "protagonist_counts": {"Opus 4.8": 3, "GPT 5.5": 1, "DeepSeek V4 Pro": 1, "Perplexity Pro": 1},
    }
    bundle.model_influence_pct = {
        "Opus 4.8": 29.9,
        "GPT 5.5": 34.8,
        "DeepSeek V4 Pro": 34.8,
        "Perplexity Pro": 0.5,
    }
    bundle.model_token_costs = {"total": {"cost_usd": 5.348506, "calls": 40, "total_tokens": 565761}}

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    stats = text.split("📊 NÚMEROS DA RODADA:\n", 1)[1].split("\n\n⚠️", 1)[0]
    first_bullet = stats.splitlines()[0]
    assert "24 mensagens" in first_bullet
    assert "6 rodadas" in first_bullet
    assert "GPT e DeepSeek" in first_bullet
    assert "Perplexity quase não moveu" in first_bullet
    assert "US$" not in first_bullet


def test_backstage_prefers_source_correction_and_protagonist_behavior() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 24,
        "total_rounds": 6,
        "protagonist_counts": {"Opus 4.8": 3, "GPT 5.5": 1, "DeepSeek V4 Pro": 1, "Perplexity Pro": 1},
        "last_consensus_protagonist": "Opus 4.8",
    }
    bundle.meeting_transcript = [
        {
            "round": 3,
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": (
                        "Discordo do protagonista por erro de fonte e por seleção de âncora. "
                        "Verificação fresca: FanDuel lista Brasil +900, não 4,3%; "
                        "Polymarket 72% é Grupo C, não título — não sustenta 4% de título."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": "A chance de título em 5,4% é uma convergência auditável entre o simulação configurado.",
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
            ],
        }
    ]

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    backstage = text.split("DOIS BASTIDORES DA REUNIÃO DE HOJE:\n\n", 1)[1].split("📊", 1)[0]
    assert "Polymarket 72% era Grupo C, não título" in backstage
    assert "Opus 4.8 virou protagonista 3 vezes" in backstage
    assert "convergência auditável" not in backstage


def test_backstage_prefers_tactical_probability_adjustments_over_meeting_choreography() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 24,
        "total_rounds": 6,
        "protagonist_counts": {"Opus 4.8": 3, "GPT 5.5": 2, "DeepSeek V4 Pro": 1},
        "last_consensus_protagonist": "Opus 4.8",
    }
    bundle.meeting_transcript = [
        {
            "round": 6,
            "responses": [
                {
                    "agent": "GPT 5.5",
                    "answer": (
                        "Concordo que 66.9% está superestimado no cenário Brasil x Japão. "
                        "O ajuste que proponho é reduzir para 63.5%. "
                        "A razão é simples: a ausência de Neymar já tira criação central e bola parada; "
                        "a lesão de Raphinha adiciona risco no corredor direito; o 3-0 contra o Haiti "
                        "teve placar confortável, mas relatos de jogo apontaram falta de coesão e criação lenta; "
                        "e o Japão mostrou resiliência real no 2-2 contra a Holanda. "
                        "Isso não transforma o Japão em favorito, mas comprime a margem brasileira em mata-mata."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": (
                        "Discordo do ajuste agressivo abaixo de 60%. O Brasil ainda tem superioridade de elenco, "
                        "mais caminhos individuais de gol e controle territorial esperado; minha leitura é que "
                        "Japão é adversário de upset plausível, não adversário parelho."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
            ],
        }
    ]

    text = render_template_post(bundle, post_index=2, run_date=date(2026, 6, 20))

    backstage = text.split("DOIS BASTIDORES DA REUNIÃO DE HOJE:\n\n", 1)[1].split("📊", 1)[0]
    assert "Brasil x Japão caiu de 66,9% para 63,5%" in backstage
    assert "Neymar" in backstage
    assert "Raphinha" in backstage
    assert "upset plausível" in backstage
    assert "virou protagonista" not in backstage


def test_backstage_describes_weighted_path_risk_without_turning_branch_probability_into_adjustment() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 24,
        "total_rounds": 6,
        "protagonist_counts": {"Opus 4.8": 3, "Gemini Pro": 2},
        "last_consensus_protagonist": "Opus 4.8",
    }
    bundle.meeting_transcript = [
        {
            "round": 3,
            "responses": [
                {
                    "agent": "Gemini Pro",
                    "answer": (
                        "Concordo com o cálculo ponderado das 16-avos: Japão 32,5% x 66,9%, "
                        "Holanda 31,7% x 46,6% e Suécia 28,8% x 70,0% resultam em 60,9%. "
                        "O risco de cauda é a Holanda: quase a mesma chance de cenário do Japão, "
                        "mas com Brasil abaixo de 50% no confronto. "
                        "A performance contra o Haiti (3-0) demonstra que a coesão ofensiva foi parcialmente "
                        "reajustada, invalidando o pânico por ausência de Neymar."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
                {
                    "agent": "Opus 4.8",
                    "answer": (
                        "CONCORDO com o protagonista. O headline Brasil 66,9% vs Japão é menos informativo "
                        "que essa média, e o ramo Holanda (46,6%, quase mesma probabilidade de cenário que "
                        "o Japão) é a cauda correta a monitorar."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": (
                        "Discordo de tratar Japão como adversário parelho. Abaixo de 60% seria agressivo demais: "
                        "Japão é upset plausível, mas o Brasil ainda tem vantagem de elenco e controle territorial."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
            ],
        }
    ]

    text = render_template_post(bundle, post_index=2, run_date=date(2026, 6, 20))

    backstage = text.split("DOIS BASTIDORES DA REUNIÃO DE HOJE:\n\n", 1)[1].split("📊", 1)[0]
    assert "risco ponderado de 60,9%" in backstage
    assert "Holanda a 46,6%" in backstage
    assert "Japão a 32,5%" not in backstage
    assert "de 60,9% para 46,6%" not in backstage
    assert "virou protagonista" not in backstage


def test_backstage_does_not_truncate_after_numeric_date_fragment() -> None:
    bundle = _bundle()
    bundle.model_participation = {
        "total_messages": 18,
        "total_rounds": 6,
        "protagonist_counts": {"DeepSeek V4 Pro": 3},
        "last_consensus_protagonist": "DeepSeek V4 Pro",
    }
    bundle.meeting_transcript = [
        {
            "round": 13,
            "responses": [
                {
                    "agent": "DeepSeek V4 Pro",
                    "answer": (
                        "Após buscar informações atualizadas sobre lesões no Brasil e na Costa do Marfim entre "
                        "13 e 16 de junho, não encontrei novo desfalque auditável que altere a probabilidade."
                    ),
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                }
            ],
        }
    ]

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    backstage = text.split("DOIS BASTIDORES DA REUNIÃO DE HOJE:\n\n", 1)[1].split("📊", 1)[0]
    assert "entre 13.\n" not in backstage
    assert "entre 13." not in backstage.split("2️⃣", 1)[0]
    assert "entre 13 e 16" in backstage


def test_template_post_includes_change_bullets_when_previous_bundle_is_available() -> None:
    previous = _bundle()
    current = _bundle()
    current.generated_at_iso = "2026-06-13T18:43:54+00:00"
    current.stage_probabilities = {"quartas": 40.3, "semifinal": 20.5, "final": 9.2, "titulo": 3.7}
    for match in current.knockout_matches:
        if match.phase == "Quartas" and match.most_likely:
            match.scenario_pct = 38.7
            match.brazil_pct = 37.0
            match.opponent_pct = 63.0
    current.meeting_transcript = [
        {
            "round": 3,
            "responses": [
                {
                    "agent": "Opus 4.8",
                    "answer": "Polymarket 72% é Grupo C, não título — não sustenta 4% de título.",
                    "disagreed": True,
                    "removed_from_main": False,
                    "used_fallback": False,
                },
            ],
        }
    ]

    text = render_template_post(current, post_index=2, run_date=date(2026, 6, 13), previous_bundle=previous)

    assert "O QUE MUDOU DESDE A ESTREIA (13/06):" in text
    assert "• Hexa 8,6%→3,7%; final 16%→9%." in text
    assert "• Quartas: Inglaterra 31%→39%; Brasil 49%→37%." in text
    assert "• Por quê: Polymarket era Grupo C; caminho recalculado." in text
    assert "Esse mapa muda a cada rodada" not in text


def test_template_post_change_header_uses_latest_brazil_match_already_played() -> None:
    previous = _bundle()
    current = _bundle()
    current.generated_at_iso = "2026-06-20T12:00:00+00:00"
    current.stage_probabilities = {"quartas": 42.0, "semifinal": 24.0, "final": 12.0, "titulo": 6.0}

    text = render_template_post(current, post_index=3, run_date=date(2026, 6, 20), previous_bundle=previous)

    assert "O QUE MUDOU DESDE BRASIL x HAITI (19/06):" in text
    assert "O QUE MUDOU DESDE A ESTREIA" not in text


def test_template_post_surfaces_team_context_warnings_in_run_note() -> None:
    bundle = _bundle()
    bundle.warnings = [
        "Ajuste contextual fora da faixa de revisão: Suécia teve +46.5 pontos de rating, acima do limiar 40.0; validar se há dupla contagem ou reação excessiva antes de publicar.",
        "Ajuste contextual pode estar subagrupado: Inglaterra teve sinal de resultado sem calendário concluído.",
    ]

    text = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    assert "⚠️ Nota do run:" in text
    assert "ajustes contextuais em revisão" in text
    assert "Suécia" in text
    assert "Inglaterra" in text


def test_validate_rejects_unresolved_placeholder_and_oversize() -> None:
    bundle = _bundle()
    good = render_template_post(bundle, post_index=1, run_date=date(2026, 6, 11))

    with pytest.raises(ValueError, match="placeholder"):
        validate_template_post(good + " {sobrou}", bundle)
    with pytest.raises(ValueError, match="caracteres"):
        validate_template_post(good + "x" * MAX_POST_CHARS, bundle)


def test_editor_can_only_append() -> None:
    base = "POST FIXO DO TEMPLATE\nLinha final.\n"

    appended = apply_editor_append(base, base.rstrip("\n") + "\n\n⚽ Bônus: estreia com cara de 2 a 0.")
    assert appended.endswith("2 a 0.")

    mutated = apply_editor_append(base, base.replace("FIXO", "MUDADO"))
    assert mutated == base

    oversized = apply_editor_append(base, base.rstrip("\n") + "\n" + "x" * MAX_POST_CHARS)
    assert oversized == base
