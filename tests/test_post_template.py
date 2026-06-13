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
