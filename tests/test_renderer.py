from worldcup_brazil.models import ReportBundle
from worldcup_brazil.probabilities import MatchEstimate
from worldcup_brazil.renderer import render_audit_report, render_decision_flow_svg, render_linkedin_post


def test_render_linkedin_post_keeps_required_sections_and_custom_hashtag() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Marrocos",
                phase="Fase de grupos",
                brazil_pct=64.2,
                opponent_pct=35.8,
                draw_pct=21.0,
                match_date="13/jun",
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Ratings e mercado deixam o Brasil favorito, com risco concentrado em transição defensiva.",
                venue="Nova Jersey",
            )
        ],
        knockout_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Uruguai",
                phase="Oitavas",
                brazil_pct=58.0,
                opponent_pct=42.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cruzamento provável por simulação de chave; diferença estreita nos ratings.",
                most_likely=True,
                venue="Los Angeles Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="Colômbia",
                phase="Oitavas",
                brazil_pct=61.0,
                opponent_pct=39.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Segundo cenário por massa de probabilidade de grupo.",
                most_likely=False,
                venue="MetLife Stadium",
            ),
        ],
        stage_probabilities={
            "quartas": 72.4,
            "semifinal": 45.1,
            "final": 24.8,
            "titulo": 12.2,
        },
        group_name="GRUPO C",
        group_summary="Brasil em 1º: ~66% (faixa 61% modelo conservador-72% mercado). Avança: ~97%. Único rival real: Marrocos (lidera ~29%).",
        stage_confidence_intervals={
            "quartas": (67.0, 77.8),
            "semifinal": (39.6, 50.4),
            "final": (20.5, 29.2),
            "titulo": (9.8, 14.7),
        },
        final_rationale="Quando cruzamos mercados, ratings e forma recente, o Brasil é candidato forte, não favorito absoluto.",
        sources=["FIFA rankings", "sportsbook consensus", "prediction market"],
        agent_summaries={"GPT": "consenso calibrado"},
        model_influence_pct={"GPT": 100.0},
        agent_effort_profiles={
            "GPT": {
                "provider": "openai",
                "model": "gpt-5.5",
                "effort_level": "reasoning_effort=high + resposta rápida",
                "controls": "reasoning_effort=high; max_output_tokens=6000; temperature=0.15",
                "latency_guard": "JSON objetivo; sem texto fora do formato",
            }
        },
        model_predictions_no_opta={
            "GPT": {
                "title_pct": 12.2,
                "summary": "Brasil candidato, mas não favorito absoluto.",
                "used_fallback": False,
            }
        },
        model_self_identification={
            "GPT": {
                "name": "ChatGPT",
                "version": "GPT-5.5 Thinking",
                "source": "declarado pelo próprio modelo",
            }
        },
        opta_benchmark={
            "available": True,
            "title_pct": 9.5,
            "source": "Benchmark legado",
            "notes": "Benchmark externo, fora do cálculo do meu modelo.",
        },
        model_vs_opta={
            "available": True,
            "title_delta_pct": 2.7,
            "leader": "Meu modelo",
            "rationale": "Meu modelo ficou mais otimista por ponderar mercado, Elo e contexto atual.",
        },
        source_plan_by_model={
            "GPT": {
                "source_urls": ["https://odds.example.com"],
                "source_queries": ["Brazil World Cup 2026 odds"],
            }
        },
        model_participation={
            "total_messages": 2,
            "total_questions": 1,
            "total_responses": 1,
            "total_rounds": 1,
            "protagonist_counts": {"GPT": 1},
            "rounds": [{"round": 1, "protagonist": "GPT", "protagonist_count": 1, "participants": ["GPT"]}],
            "consensus_questions_by_phase": [
                {
                    "phase": "Fase de grupos",
                    "round": 1,
                    "protagonist": "GPT",
                    "question": "Qual a leitura para Marrocos, Haiti e Escócia?",
                },
                {
                    "phase": "Oitavas",
                    "round": 1,
                    "protagonist": "GPT",
                    "question": "Qual fonte está distorcendo o consenso?",
                },
            ],
            "last_consensus_round": 1,
            "last_consensus_protagonist": "GPT",
            "last_consensus_question": "Qual fonte está distorcendo o consenso?",
            "last_consensus_participants": ["GPT"],
            "by_model": {
                "GPT": {"messages": 2, "questions": 1, "responses": 1},
            },
        },
        model_token_costs={
            "pricing_basis": "estimativa local para teste",
            "usd_to_brl": 5.4,
            "total": {
                "calls": 2,
                "fallback_calls": 0,
                "prompt_tokens": 1000,
                "completion_tokens": 400,
                "total_tokens": 1400,
                "cost_usd": 0.042,
                "cost_brl": 0.2268,
            },
            "by_model": {
                "GPT": {
                    "calls": 2,
                    "fallback_calls": 0,
                    "prompt_tokens": 1000,
                    "completion_tokens": 400,
                    "total_tokens": 1400,
                    "cost_usd": 0.042,
                    "cost_brl": 0.2268,
                }
            },
        },
        meeting_transcript=[
            {
                "round": 1,
                "protagonist": "GPT",
                "question": "Qual fonte está distorcendo o consenso?",
                "responses": [
                    {
                        "agent": "GPT",
                        "answer": "Sportsbooks e ratings convergem melhor que um modelo isolado.",
                        "title_pct": 12.2,
                        "support_score": 0.9,
                    }
                ],
                "next_protagonist": "GPT",
                "consensus_title_pct": 12.2,
                "consensus_spread_pct": 0.0,
            }
        ],
        debate_transcript=[
            "Rodada 1 - GPT: mercado ainda não precifica Brasil como favorito absoluto.",
            "Rodada 2 - Opus: ajuste para lesões reduz cauda otimista.",
            "Consenso: título em 12.2% com dispersão moderada.",
        ],
        warnings=[],
        custom_hashtag="#CopaComAchismo",
        metadata={
            "uncertainty": {
                "confidence_level": 0.99,
                "minimum_declared_coverage": 0.95,
                "rating_uncertainty_enabled": True,
                "model_dispersion_method": "logit_student_t",
            }
        },
    )

    post = render_linkedin_post(bundle)

    assert "Fase de grupos (todos os jogos):" in post
    assert "Como ler este post:" in post
    assert "IC = intervalo de confiança" in post
    assert "nível declarado: 99%" in post
    assert "mínimo operacional: 95%" in post
    assert "A Copa real é um único funil" in post
    assert "condicional à simulação da chave" in post
    assert "largura aumenta" in post
    assert "Gráfico do racional:" in post
    assert "GRUPO C — probabilidade de vitória do Brasil por jogo:" in post
    assert "• 13/jun vs Marrocos (Nova Jersey): 64.2% V | 21.0% E | 14.8% D" in post
    assert "→ Brasil em 1º: ~66%" in post
    assert "Fase de mata-mata (todos os jogos):" in post
    assert "Conclusão final:" in post
    assert "Intervalo de confiança" in post
    assert "Palpites por modelo:" in post
    assert "Meu modelo sem Opta vs Opta:" not in post
    assert "Opta" not in post
    assert "Sala de decisão dos modelos (debriefing):" in post
    assert "Fontes escolhidas por modelo:" in post
    assert "Chat da sala:" in post
    assert "Rodada 1 | Protagonista: GPT" in post
    assert "[Rodada 1] GPT (protagonista): Qual fonte está distorcendo o consenso?" in post
    assert "[Rodada 1] GPT: Sportsbooks e ratings convergem melhor que um modelo isolado." in post
    assert "Status: aceita" in post
    assert "Próximo protagonista: GPT" in post
    assert "Influência dos modelos na decisão:" in post
    assert "GPT: 100.0%" in post
    assert "Participação na sala:" in post
    assert "Total de rodadas: 1." in post
    assert "Protagonismo por modelo: GPT 1x." in post
    assert "Protagonismo por rodada: R1 GPT (1x no run)." in post
    assert "Perguntas que fecharam consenso por fase:" in post
    assert "- Fase de grupos: rodada 1, GPT. Pergunta: Qual a leitura para Marrocos, Haiti e Escócia?" in post
    assert "- Oitavas: rodada 1, GPT. Pergunta: Qual fonte está distorcendo o consenso?" in post
    assert "Última pergunta que virou consenso: rodada 1, GPT." in post
    assert "Participantes da rodada de consenso: GPT." in post
    assert "Total de mensagens trocadas: 2 (1 perguntas de protagonista + 1 respostas dos modelos)." in post
    assert "GPT: 2 mensagens (1 perguntas, 1 respostas)." in post
    assert "Custo estimado da rodada:" in post
    assert "Consolidado: 1.400 tokens aprox. | US$ 0.0420 | R$ 0.23" in post
    assert "GPT: 2 chamada(s); 1.400 tokens aprox.; US$ 0.0420 / R$ 0.23" in post
    assert "Esforço usado por modelo:" in post
    assert "Identidade declarada pelos modelos:" in post
    assert "GPT: nome ChatGPT | versão GPT-5.5 Thinking" in post
    assert "GPT: reasoning_effort=high + resposta rápida" in post
    assert "reasoning_effort=high" in post
    assert "Fontes usadas:" in post
    assert "Comentário sugerido para postar nos comentários:" in post
    comment = post.split("Comentário sugerido para postar nos comentários:", 1)[1]
    comment_bullets = [line for line in comment.splitlines() if line.startswith("- ")]
    assert len(comment_bullets) == 6
    assert any("fontes próprias" in line for line in comment_bullets)
    assert any("protagonista" in line for line in comment_bullets)
    assert any("Concordar" in line for line in comment_bullets)
    assert any("Discordar" in line for line in comment_bullets)
    assert any("Fallback" in line for line in comment_bullets)
    assert any("Quanti e quali" in line for line in comment_bullets)
    assert "#CopaComAchismo" in post
    assert "#BrasilCopa2026Radar" not in post
    assert len(post.splitlines()) <= 2500


def test_render_reports_handle_meeting_response_without_own_title_number() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-18T04:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 30.0, "semifinal": 15.0, "final": 8.0, "titulo": 5.0},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
        meeting_transcript=[
            {
                "round": 1,
                "protagonist": "GPT 5.5",
                "question": "Concordam?",
                "responses": [
                    {
                        "agent": "Opus 4.8",
                        "answer": "Concordo sem número próprio.",
                        "title_pct": None,
                        "title_pct_source": "missing",
                        "support_score": 1.0,
                        "accepted": True,
                        "used_fallback": False,
                        "removed_from_main": False,
                    }
                ],
                "next_protagonist": "GPT 5.5",
                "consensus_title_pct": 5.0,
                "consensus_spread_pct": 0.0,
            }
        ],
    )

    post = render_linkedin_post(bundle)
    audit = render_audit_report(bundle)

    assert "título: sem número próprio" in post
    assert "Título: sem número próprio" in audit


def test_render_group_block_uses_completed_result_ledger() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-15T12:00:00+00:00",
        group_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Marrocos",
                phase="Fase de grupos",
                brazil_pct=59.0,
                opponent_pct=17.0,
                draw_pct=24.0,
                match_date="13/jun",
                statistical_weight=0.5,
                qualitative_weight=0.5,
                rationale="Pré-jogo obsoleto se o placar já existe.",
                venue="Nova Jersey",
            )
        ],
        knockout_matches=[],
        stage_probabilities={"quartas": 40.0, "semifinal": 20.0, "final": 10.0, "titulo": 4.0},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
        group_name="GRUPO C",
        group_summary="Brasil em 1º: ~77.1%.",
        metadata={
            "group_state": {
                "completed_results": [
                    {"score": "Brasil 1-1 Marrocos"},
                ]
            }
        },
    )

    post = render_linkedin_post(bundle)

    assert "resultado Brasil 1-1 Marrocos" in post
    assert "59.0% V | 24.0% E | 17.0% D" not in post


def test_render_linkedin_post_includes_monte_carlo_path_summary() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 71.2, "semifinal": 42.8, "final": 22.1, "titulo": 10.9},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
        metadata={
            "monte_carlo": {
                "enabled": True,
                "iterations": 40000,
                "seed": 26062026,
                "rating_coverage_pct": 18.8,
                "confidence_level": 0.99,
                "rating_uncertainty": {
                    "enabled": True,
                    "outer_samples": 200,
                    "inner_iterations": 200,
                    "configured_rating_sigma": 50.0,
                    "prior_rating_sigma": 150.0,
                },
                "stage_uncertainty_intervals": {
                    "titulo": (2.0, 22.0),
                },
                "path_gate": {
                    "reliable": False,
                    "mode": "weak_prior",
                    "min_iterations": 10000,
                    "min_rating_coverage_pct": 65.0,
                },
                "team_context": {
                    "applied_signal_count": 4,
                    "teams_with_context_count": 3,
                    "source_families": [
                        "bets_prediction_markets",
                        "injuries_cuts_news",
                        "specialist_press_recent_friendlies",
                    ],
                },
                "relevant_group_states": {
                    "F": {
                        "phases": ["16 avos"],
                        "current_table": [
                            {"team": "Suécia", "points": 3, "goal_difference": 4},
                            {"team": "Holanda", "points": 1, "goal_difference": 0},
                        ],
                        "completed_results": [
                            {"score": "Holanda 2-2 Japão"},
                            {"score": "Suécia 5-1 Tunísia"},
                        ],
                    }
                },
                "stage_probabilities": {
                    "quartas": 71.2,
                    "semifinal": 42.8,
                    "final": 22.1,
                    "titulo": 10.9,
                },
                "phases": {
                    "16 avos": {
                        "top_opponents": [
                            {"opponent": "Japão", "scenario_pct": 38.4},
                            {"opponent": "Suécia", "scenario_pct": 31.7},
                        ]
                    }
                },
            },
            "numeric_chairman": {
                "stage_probability_blend": {
                    "enabled": True,
                    "monte_carlo_weight": 0.6,
                    "model_weight": 0.4,
                    "label": "monte_carlo_model_blend_60_40",
                }
            },
        },
        )

    post = render_linkedin_post(bundle)

    assert "Simulação Monte Carlo:" in post
    assert "- Rodadas: 40.000; seed: 26062026; cobertura de rating explícito: 18.8%." in post
    assert "- Nível de IC Monte Carlo: 99%; incerteza de rating: 200 cenários x 200 torneios" in post
    assert "sigma rating explícito 50.0 Elo; sigma prior 150.0 Elo" in post
    assert "ponto central = média posterior sobre cenários de rating" in post
    assert "variância total desconta ruído interno" in post
    assert "- Banda epistêmica MC: título 2.0%-22.0%." in post
    assert "- Gate do caminho: prior fraco de caminho; mínimo 10.000 simulações e 65.0% de cobertura." in post
    assert "Placares do caminho já incorporados: Grupo F (16 avos; líder Suécia): Holanda 2-2 Japão; Suécia 5-1 Tunísia." in post
    assert "Contexto por seleção: 4 sinais em 3 seleções" in post
    assert "bets_prediction_markets" in post
    assert "injuries_cuts_news" in post
    assert "funil final combina 60% Monte Carlo e 40% consenso dos modelos" in post
    assert "mercado pode desafiar, mas não reprecifica sozinho" in post
    assert "- Funil MC de base: quartas 71.2% | semi 42.8% | final 22.1% | título simulado 10.9%." in post
    assert "- 16 avos, adversários por simulação: Japão 38.4%, Suécia 31.7%." in post


def test_render_linkedin_post_stays_publishable_and_audit_keeps_full_meeting_chat_text() -> None:
    long_answer = "começo " + ("detalhe " * 180) + "FIM-DA-FALA-COMPLETA"
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        meeting_transcript=[
            {
                "round": 1,
                "protagonist": "GPT",
                "question": "Pergunta longa " + ("x " * 150) + "FIM-DA-PERGUNTA-COMPLETA",
                "responses": [
                    {
                        "agent": "GPT",
                        "answer": long_answer,
                        "title_pct": 12.2,
                        "support_score": 0.9,
                    }
                ],
                "next_protagonist": "GPT",
                "consensus_title_pct": 12.2,
                "consensus_spread_pct": 0.0,
            }
        ],
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)
    audit = render_audit_report(bundle)

    assert "FIM-DA-PERGUNTA-COMPLETA" not in post
    assert "FIM-DA-FALA-COMPLETA" not in post
    assert len(post.splitlines()) < 180
    assert "FIM-DA-PERGUNTA-COMPLETA" in audit
    assert "FIM-DA-FALA-COMPLETA" in audit


def test_render_audit_report_formats_chat_turns_with_human_readable_leadership_details() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        meeting_transcript=[
            {
                "round": 1,
                "protagonist": "GPT 5.5",
                "question": "Concordam ou discordam?",
                "responses": [
                    {
                        "agent": "Gemini Pro",
                        "answer": "Concordo e proponho testar cartões.",
                        "title_pct": 12.0,
                        "support_score": 0.93,
                        "accepted": True,
                        "leadership_bid": True,
                        "proposed_next_question": "Cartões mudam o risco nas quartas?",
                        "leadership_rationale": "Fonte disciplinar nova e pergunta auditável.",
                    }
                ],
                "next_protagonist": "Gemini Pro",
                "consensus_title_pct": 12.0,
                "consensus_spread_pct": 0.0,
            }
        ],
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    audit = render_audit_report(bundle)

    assert "Chat completo:" in audit
    assert "Rodada 1 | Protagonista: GPT 5.5" in audit
    assert "Pergunta do protagonista: Concordam ou discordam?" in audit
    assert "Status: aceita" in audit
    assert "Mensagem: Concordo e proponho testar cartões." in audit
    assert "Proposta de próxima pergunta: Cartões mudam o risco nas quartas?" in audit
    assert "Racional de protagonismo: Fonte disciplinar nova e pergunta auditável." in audit


def test_render_linkedin_post_includes_transfermarkt_market_value_momentum_highlights() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        metadata={
            "market_value_momentum": {
                "available": True,
                "source": "Transfermarkt",
                "rule": (
                    "Peso combina delta nominal em euros e percentual, com teto para o percentual; "
                    "um ganho 50M->55M pesa mais que 10M->13M."
                ),
                "teams": {
                    "Brasil": {
                        "players_tracked": 2,
                        "positive_players": 2,
                        "nominal_delta_eur": 8_000_000,
                        "weighted_delta_eur": 9_900_000,
                        "top_players": [
                            {
                                "player": "Brasil A",
                                "display": "Brasil A 50.0M->55.0M",
                                "delta_eur": 5_000_000,
                                "pct_delta": 10.0,
                            },
                            {
                                "player": "Brasil B",
                                "display": "Brasil B 10.0M->13.0M",
                                "delta_eur": 3_000_000,
                                "pct_delta": 30.0,
                            },
                        ],
                    },
                    "Marrocos": {
                        "players_tracked": 1,
                        "positive_players": 0,
                        "nominal_delta_eur": -2_000_000,
                        "weighted_delta_eur": -2_200_000,
                        "top_players": [
                            {
                                "player": "Marrocos A",
                                "display": "Marrocos A 20.0M->18.0M",
                                "delta_eur": -2_000_000,
                                "pct_delta": -10.0,
                            }
                        ],
                    },
                },
            }
        },
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)

    assert "Destaques de valorização (Transfermarkt):" in post
    assert "delta nominal em euros pesa mais que percentual isolado" in post
    assert "50M->55M pesa mais que 10M->13M" in post
    assert "Brasil: 2/2 jogadores em alta" in post
    assert "delta nominal €8.0M" in post
    assert "score ponderado €9.9M" in post
    assert "Brasil A 50.0M->55.0M" in post
    assert "Marrocos: 0/1 jogadores em alta" in post


def test_render_meeting_invalidation_without_leaking_invalid_opponent() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        meeting_transcript=[
            {
                "round": 1,
                "protagonist": "DeepSeek V4 Pro",
                "question": (
                    "A fala do protagonista foi invalidada pela sala por citar adversário fora do grupo configurado. "
                    "Modelos da sala: ignorem essa fala e voltem aos jogos configurados."
                ),
                "invalidated_protagonist_question": {
                    "agent": "DeepSeek V4 Pro",
                    "reason": "adversário fora do grupo configurado ou país não definido no JSON de cenários",
                    "action": "fala excluída da influência; modelos pares seguem o debate",
                },
                "responses": [
                    {
                        "agent": "GPT 5.5",
                        "answer": "Ignoro a fala inválida e volto a Marrocos, Haiti e Escócia com odds e Elo.",
                        "title_pct": 8.3,
                        "support_score": 0.86,
                        "accepted": True,
                        "used_fallback": False,
                    }
                ],
                "next_protagonist": "GPT 5.5",
                "consensus_title_pct": 8.3,
                "consensus_spread_pct": 0.0,
            }
        ],
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)
    audit = render_audit_report(bundle)

    assert "Fala invalidada" in post
    assert "fala excluída da influência" in audit
    assert "Sérvia" not in post
    assert "Sérvia" not in audit


def test_render_decision_flow_svg_creates_clean_visual_asset() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[],
        stage_probabilities={"quartas": 60.0, "semifinal": 38.0, "final": 21.0, "titulo": 10.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        model_vs_opta={},
        opta_benchmark={},
        model_influence_pct={"Perplexity Pro": 44.0, "GPT 5.5": 22.0, "Opus 4.8": 18.0},
        metadata={"meeting_rounds": 6},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    svg = render_decision_flow_svg(bundle)

    assert svg.startswith("<svg")
    assert "Fluxo de decisão" in svg
    assert "10.2%" in svg
    assert "Opta" not in svg
    assert "6 rodadas" in svg
    assert "discordância" in svg
    assert "eventos recentes" in svg
    assert "nome/versão" in svg


def test_render_linkedin_post_groups_knockout_scenarios_by_phase() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Uruguai",
                phase="16 avos",
                brazil_pct=58.0,
                opponent_pct=42.0,
                brazil_ci_low=52.0,
                brazil_ci_high=64.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cenário mais provável do primeiro mata-mata.",
                match_date="29/jun",
                most_likely=True,
                scenario_pct=46.0,
                venue="Los Angeles Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="Colômbia",
                phase="16 avos",
                brazil_pct=61.0,
                opponent_pct=39.0,
                brazil_ci_low=55.0,
                brazil_ci_high=67.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Segundo cenário do primeiro mata-mata.",
                match_date="29/jun",
                most_likely=False,
                scenario_pct=24.0,
                venue="MetLife Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="Uruguai",
                phase="Oitavas",
                brazil_pct=58.0,
                opponent_pct=42.0,
                brazil_ci_low=52.0,
                brazil_ci_high=64.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cenário mais provável por chave.",
                match_date="30/jun",
                most_likely=True,
                venue="Los Angeles Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="Espanha",
                phase="Quartas",
                brazil_pct=49.0,
                opponent_pct=51.0,
                brazil_ci_low=43.0,
                brazil_ci_high=55.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cenário de quartas por simulação.",
                match_date="4/jul",
                most_likely=True,
                venue="MetLife Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="França",
                phase="Semifinal",
                brazil_pct=45.0,
                opponent_pct=55.0,
                brazil_ci_low=39.0,
                brazil_ci_high=51.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cenário de semifinal por simulação.",
                most_likely=True,
                venue="AT&T Stadium",
            ),
            MatchEstimate(
                brazil="Brasil",
                opponent="Argentina",
                phase="Final",
                brazil_pct=50.0,
                opponent_pct=50.0,
                brazil_ci_low=44.0,
                brazil_ci_high=56.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Cenário de final por simulação.",
                most_likely=True,
                venue="MetLife Stadium",
            ),
        ],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)

    assert "1. Primeiro vem o funil jogo a jogo: grupo, 16 avos, oitavas, quartas, semi e final." in post
    assert post.index("16 avos:") < post.index("Oitavas:")
    assert "16 AVOS — caminhos mais prováveis:" in post
    assert "• Adversário mais provável (chance do confronto: 46.0%): 29/jun vs Uruguai (Los Angeles Stadium): 58.0% Brasil passa | 42.0% Uruguai passa" in post
    assert "• Adversário menos provável (chance do confronto: 24.0%): 29/jun vs Colômbia (MetLife Stadium): 61.0% Brasil passa | 39.0% Colômbia passa" in post
    assert "Oitavas:" in post
    assert "OITAVAS — caminhos mais prováveis:" in post
    assert "• Adversário mais provável: 30/jun vs Uruguai (Los Angeles Stadium): 58.0% Brasil passa | 42.0% Uruguai passa" in post
    assert "Quartas:" in post
    assert "QUARTAS — caminhos mais prováveis:" in post
    assert "Semifinal:" in post
    assert "Final:" in post
    assert "Intervalo de confiança: 52.0%-64.0% Brasil" in post


def test_render_linkedin_post_labels_second_knockout_path_as_less_likely() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_matches=[],
        knockout_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Colômbia",
                phase="Oitavas",
                brazil_pct=61.0,
                opponent_pct=39.0,
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Segundo caminho.",
                most_likely=False,
                venue="MetLife Stadium",
            )
        ],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)

    assert "• Adversário menos provável: vs Colômbia (MetLife Stadium):" in post


def test_render_group_line_omits_zero_loss_bucket() -> None:
    bundle = ReportBundle(
        generated_at_iso="2026-06-14T12:00:00+00:00",
        group_name="GRUPO C",
        group_matches=[
            MatchEstimate(
                brazil="Brasil",
                opponent="Haiti",
                phase="Fase de grupos",
                brazil_pct=92.0,
                opponent_pct=8.0,
                draw_pct=8.0,
                match_date="19/jun",
                statistical_weight=0.7,
                qualitative_weight=0.3,
                rationale="Favoritismo amplo.",
                venue="Filadélfia",
            )
        ],
        knockout_matches=[],
        stage_probabilities={"quartas": 72.4, "semifinal": 45.1, "final": 24.8, "titulo": 12.2},
        final_rationale="Racional.",
        sources=[],
        agent_summaries={},
        warnings=[],
        custom_hashtag="#CopaComAchismo",
    )

    post = render_linkedin_post(bundle)

    assert "• 19/jun vs Haiti (Filadélfia): 92.0% V | 8.0% E" in post
    assert "0.0% D" not in post
