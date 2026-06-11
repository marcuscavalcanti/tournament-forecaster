from pathlib import Path


def test_announcement_diagram_generator_uses_current_brand_and_directional_rule() -> None:
    script = Path("scripts/generate_framework_diagram_pngs.py").read_text(encoding="utf-8")
    script_lower = script.lower()

    assert "#CopaComAchismo" in script
    assert "#copaComAchismo" not in script
    assert "menos achismo" not in script_lower
    assert "mais lastro" not in script_lower
    assert "#brasilcopa2026radar" not in script_lower
    assert "opta" not in script_lower
    assert "70% dados quantitativos" not in script_lower
    assert "30% contexto" not in script_lower
    assert "quanti e quali sem quota fixa" in script_lower
    assert "liderança roda por mérito" in script_lower
    assert script_lower.count("líder sem voto perde a palavra") >= 1
    assert "estável 2 rodadas encerra cedo" in script_lower
    assert "sala vazia 2 rodadas" in script_lower
    assert "eventos recentes" in script_lower
    assert "recent_event_harness" in script
    assert "nome/versão declarados" in script_lower
    assert "self_identification" in script
    assert "sala paralela" in script_lower
    assert "adversários prováveis" in script_lower
    assert "sala de debriefing funcional" in script_lower
    assert "mesa de debriefing" in script_lower
    assert "rodada de consenso" in script_lower
    assert "modelos na mesa" in script_lower
    assert "gate pré-render" in script_lower
    assert "prior baixo" in script_lower
    assert "gate de confiança" in script_lower
    assert "reentrada seletiva" in script_lower
    assert "entrada: 3 planos" in script_lower
    assert "floor(participantes ativos/2)+1" in script_lower
    assert "fallback sintético não vota" in script_lower
    assert "fallback auditável conta" not in script_lower
    assert "consensus_check_question" in script
    assert "pergunta de consenso" in script_lower
    assert "melhor" not in script_lower
    assert "pergunta mais útil" not in script_lower
