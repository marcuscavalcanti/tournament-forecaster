import json
from pathlib import Path

from worldcup_brazil.agents import _local_claude_cli_command
from worldcup_brazil.bracket import hydrate_canonical_configs
from worldcup_brazil.consensus import AgentOpinion
from worldcup_brazil.pipeline import (
    _has_unsupported_meeting_vote,
    _impossible_bracket_opponent_detail,
    _phase_allowed_opponents_union,
    _specs_after_preflight_exclusion,
)


def _hydrated_config() -> dict:
    config = json.loads(Path("config/worldcup_brazil.example.json").read_text(encoding="utf-8"))
    hydrate_canonical_configs(config, base_dir=Path("config"))
    return config


def test_path_enumeration_sentence_is_not_flagged_as_impossible_opponent() -> None:
    """Regressão dos runs de 10/jun/2026: enumeração legítima do caminho inteiro era
    flagrada porque o segmentador atribuía a frase a todas as fases citadas (35
    remoções falsas; sala de adversários 0/49)."""
    config = _hydrated_config()
    text = (
        "O cenário-base mantém o Brasil forte contra Marrocos, Haiti e Escócia no grupo; "
        "o custo do caminho é Japão/Holanda/Suécia nos 16 avos, depois Equador/Noruega/Senegal "
        "nas oitavas, e um salto para Inglaterra, Argentina, Portugal, França e Espanha nas "
        "quartas e semifinais."
    )
    assert _impossible_bracket_opponent_detail(text, config) is None


def test_genuinely_wrong_phase_claim_is_still_flagged() -> None:
    config = _hydrated_config()
    detail = _impossible_bracket_opponent_detail(
        "Nos 16 avos o Brasil deve enfrentar a Argentina em jogo duríssimo.",
        config,
    )
    assert detail is not None
    assert "argentina" in detail["invalid_opponents"]
    assert detail["phase"] == "16 avos"


def test_group_opponents_near_phase_keywords_are_never_flagged() -> None:
    config = _hydrated_config()
    text = "Vencer Marrocos e a Escócia é o que leva o Brasil aos 16 avos com tranquilidade."
    assert _impossible_bracket_opponent_detail(text, config) is None


def test_phase_allowed_union_includes_alternative_path_candidates() -> None:
    """Multi-caminho: candidato alcançável só se o Brasil passar em outra posição do
    grupo não pode ser 'impossível' (o Monte Carlo simula esses caminhos)."""
    config = _hydrated_config()
    union = {entry["phase"]: entry["allowed_opponents"] for entry in _phase_allowed_opponents_union(config)}
    single = {
        entry["phase"]: entry["allowed_opponents"]
        for entry in __import__("worldcup_brazil.bracket", fromlist=["brazil_bracket_path"]).brazil_bracket_path(config)
    }
    assert any(len(union[phase]) > len(single.get(phase, [])) for phase in union)


def test_informed_agreement_without_own_sources_is_a_valid_vote() -> None:
    """Regressão de 10/jun/2026: aceite explícito da tese do protagonista era removido
    por 'concordância sem hipótese auditável' (20+ remoções) e a sala morria estéril.
    A exigência de número+fonte vale para a tese e para discordância — não para o eco."""
    opinion = AgentOpinion(
        agent="GPT 5.5",
        title_pct=8.0,
        summary="Aceite informado.",
        answer=(
            "Concordo integralmente com o racional do protagonista: a premissa está bem "
            "fundamentada nas fontes apresentadas e não tenho evidência nova para mover o número."
        ),
        agrees_with_protagonist=True,
    )
    combined = f"{opinion.summary} {opinion.answer}"
    assert _has_unsupported_meeting_vote(opinion, combined=combined, config={}) is False


def test_disagreement_without_evidence_is_still_removed() -> None:
    opinion = AgentOpinion(
        agent="GPT 5.5",
        title_pct=9.0,
        summary="Discordância vazia.",
        answer="Discordo do racional do protagonista porque sim, sem número e sem fonte alguma.",
        agrees_with_protagonist=False,
    )
    combined = f"{opinion.summary} {opinion.answer}"
    assert _has_unsupported_meeting_vote(opinion, combined=combined, config={}) is True


def test_agreement_injecting_probability_maps_without_sources_is_removed() -> None:
    opinion = AgentOpinion(
        agent="GPT 5.5",
        title_pct=8.0,
        summary="Eco com dados novos sem fonte.",
        answer=(
            "Concordo integralmente com o racional do protagonista e a premissa proposta, "
            "e aproveito para ajustar os números dos confrontos conforme minha leitura."
        ),
        agrees_with_protagonist=True,
        match_probabilities={"16 avos: Japão": 70.0},
    )
    combined = f"{opinion.summary} {opinion.answer}"
    assert _has_unsupported_meeting_vote(opinion, combined=combined, config={}) is True


def test_claude_cli_command_grants_web_search_tools(monkeypatch) -> None:
    """Regressão de 10/jun/2026: Opus removido em 100% dos runs por 'Web search blocked —
    permission not granted'; o bridge CLI não concedia ferramentas de busca."""
    monkeypatch.delenv("CLAUDE_CLI_ALLOWED_TOOLS", raising=False)
    monkeypatch.setattr("worldcup_brazil.agents.shutil.which", lambda name: "/usr/local/bin/claude")
    command = _local_claude_cli_command()
    assert "--allowedTools=WebSearch,WebFetch" in command
    assert "--allowedTools" not in command


def test_preflight_failed_slots_are_excluded_from_run() -> None:
    class Spec:
        def __init__(self, slot):
            self.slot = slot

    specs = [Spec("Opus 4.8"), Spec("Gemini Pro"), Spec("GPT 5.5")]
    config = {"_preflight_failed_slots": ["Gemini Pro"], "exclude_slots_failing_preflight": True}
    remaining = _specs_after_preflight_exclusion(specs, config)
    assert [spec.slot for spec in remaining] == ["Opus 4.8", "GPT 5.5"]

    config_disabled = {"_preflight_failed_slots": ["Gemini Pro"], "exclude_slots_failing_preflight": False}
    assert len(_specs_after_preflight_exclusion(specs, config_disabled)) == 3


def test_round_name_compound_de_final_does_not_whitelist_everything() -> None:
    """Achado da revisão adversarial: '\\bfinal\\b' casava dentro de '16 avos de final' e
    'fase final', e a união da fase Final (47/48 seleções) anulava o validador para a
    fraseologia padrão do português."""
    config = _hydrated_config()
    detail = _impossible_bracket_opponent_detail(
        "O duelo dos 16 avos de final deve ser contra a Argentina, pelo que vejo nas odds.",
        config,
    )
    assert detail is not None
    assert "argentina" in detail["invalid_opponents"]

    detail = _impossible_bracket_opponent_detail(
        "Nos 16 avos da fase final, o confronto mais provável é contra a Argentina.",
        config,
    )
    assert detail is not None


def test_third_place_path_candidates_are_not_flagged() -> None:
    """Achado da revisão adversarial: caminhos de 3º lugar eram descartados por
    ambiguidade (3C cabe em vários jogos) e candidatos legítimos viravam 'impossíveis'."""
    config = _hydrated_config()
    text = "No caminho de 3º lugar, o Brasil enfrenta a Alemanha nos 16 avos."
    assert _impossible_bracket_opponent_detail(text, config) is None


def test_contextual_and_historical_mentions_are_not_bracket_claims() -> None:
    """Achado da revisão adversarial: menção contextual/histórica perto de marcador de
    fase derrubava a fala inteira — mesma classe de falso positivo que esterilizou
    os runs de 10/jun/2026."""
    config = _hydrated_config()
    assert (
        _impossible_bracket_opponent_detail(
            "Em 2022 a Croácia eliminou o Brasil nas oitavas nos pênaltis.",
            config,
        )
        is None
    )
    assert (
        _impossible_bracket_opponent_detail(
            "Depois das oitavas, o caminho do Brasil passa por Espanha e Argentina, segundo as odds.",
            config,
        )
        is None
    )


def test_agreement_with_junk_source_and_new_maps_is_still_removed() -> None:
    """Achado da revisão adversarial: string de fonte fabricada liberava o bypass do
    aceite informado e mapas novos moviam estimativas. Deferência agora é sem mapas
    e sem ajuste, ponto; alegação numérica cai no caminho estrito."""
    opinion = AgentOpinion(
        agent="GPT 5.5",
        title_pct=8.0,
        summary="Eco com fonte-lixo.",
        answer=(
            "Concordo integralmente com o racional do protagonista e a premissa central, "
            "registrando minha leitura dos confrontos conforme tabela em anexo."
        ),
        agrees_with_protagonist=True,
        source_urls=["nota interna do modelo"],
        match_probabilities={"Oitavas: Equador": 71.0},
    )
    combined = f"{opinion.summary} {opinion.answer}"
    assert _has_unsupported_meeting_vote(opinion, combined=combined, config={}) is True


def test_agreement_with_numeric_adjustment_and_no_source_is_removed() -> None:
    opinion = AgentOpinion(
        agent="GPT 5.5",
        title_pct=14.0,
        summary="Eco com ajuste sem fonte.",
        answer=(
            "Concordo com o racional e a premissa do protagonista, mas ajusto o título "
            "pela minha leitura pessoal do momento do elenco."
        ),
        adjustment="Subo o título para 14%.",
        agrees_with_protagonist=True,
    )
    combined = f"{opinion.summary} {opinion.answer} {opinion.adjustment}"
    assert _has_unsupported_meeting_vote(opinion, combined=combined, config={}) is True


def test_empty_allowed_tools_env_disables_claude_flag(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CLI_ALLOWED_TOOLS", "")
    monkeypatch.setattr("worldcup_brazil.agents.shutil.which", lambda name: "/usr/local/bin/claude")
    command = _local_claude_cli_command()
    assert not any(arg.startswith("--allowedTools") for arg in command)


def test_compliance_statement_about_opta_is_not_flagged() -> None:
    """Regressão do make doctor de 10/jun (pós-22463ed): o plano do GPT dizia
    'Opta fica explicitamente excluída do Modelo Principal' — declaração de
    conformidade — e foi removido por 'benchmark reservado'. Advérbio intercalado
    entre o verbo e o particípio agora é tolerado na detecção de negação."""
    from worldcup_brazil.pipeline import _has_opta_marker

    assert _has_opta_marker("Opta fica explicitamente excluída do Modelo Principal.") is False
    assert _has_opta_marker("A Opta está totalmente vedada nesta sala.") is False
    assert _has_opta_marker("usei o ranking da Opta como âncora do título") is True


def test_claude_cli_prompt_placeholder_survives_variadic_allowed_tools(monkeypatch) -> None:
    """Regressão do make doctor de 10/jun (pós-22463ed): '--allowedTools X {prompt}'
    em dois argumentos fazia a opção variádica engolir o {prompt} — claude CLI
    respondia 'Input must be provided either through stdin or as a prompt argument'.
    A flag deve ir em forma '--allowedTools=...' e o {prompt} deve ser o último arg."""
    monkeypatch.delenv("CLAUDE_CLI_ALLOWED_TOOLS", raising=False)
    monkeypatch.setattr("worldcup_brazil.agents.shutil.which", lambda name: "/usr/local/bin/claude")
    command = _local_claude_cli_command()
    assert command[-1] == "{prompt}"
    assert "--allowedTools" not in command
    assert any(arg.startswith("--allowedTools=") for arg in command)
