import json
from pathlib import Path

from worldcup_brazil import cli


class _FakeBundle:
    generated_at_iso = "2026-06-13T12:00:00+00:00"
    group_matches: list = []
    knockout_matches: list = []
    stage_probabilities: dict = {}
    stage_confidence_intervals: dict = {}
    sources: list = []
    warnings: list = []
    debate_transcript: list = []
    meeting_transcript: list = []
    source_plan_by_model: dict = {}
    model_influence_pct = {"GPT 5.5": 40.0}
    model_participation: dict = {}
    model_token_costs = {"total_usd": 6.43}
    agent_effort_profiles: dict = {}
    model_predictions_no_opta: dict = {}
    opta_benchmark: dict = {}
    model_vs_opta: dict = {}
    metadata: dict = {}
    group_name = ""
    group_summary = ""


class _FakeArtifacts:
    post = "# post"
    bundle = _FakeBundle()
    raw_evidence: list = []


def _base_argv(tmp_path: Path) -> list[str]:
    config = tmp_path / "config.json"
    config.write_text('{"baseline_title_pct": 8.0, "agents": []}', encoding="utf-8")
    return [
        "--config",
        str(config),
        "--state",
        str(tmp_path / "state.json"),
        "--source-memory",
        str(tmp_path / "source_memory.json"),
        "--output-dir",
        str(tmp_path / "outputs"),
        "--watchdog-log",
        str(tmp_path / "watchdog.jsonl"),
        "--calibration-log",
        str(tmp_path / "calibration.json"),
        "--lock-file",
        str(tmp_path / ".run.lock"),
        "--force",
        "--no-watchdog",
        "--no-model-preflight",
    ]


def test_bundle_json_is_written_even_when_a_render_raises(tmp_path, monkeypatch, capsys) -> None:
    """Bug histórico (ITEM 7): o JSON com meeting_transcript, model_influence_pct
    e model_token_costs (~US$6,43 de debate) era escrito DEPOIS dos renders
    (audit/svg). Se um render estourasse, todo o registro em disco era perdido.

    No código antigo o json_path.write_text vinha após render_audit_report e
    render_decision_flow_svg, então com render_audit_report quebrando o
    linkedin_brazil_<stamp>.json NUNCA seria criado e este teste falharia no
    assert de existência. O fix move o bloco JSON para ANTES dos renders."""
    monkeypatch.setattr(cli, "build_report_bundle_sync", lambda **kwargs: _FakeArtifacts())

    def boom_audit(_bundle):
        raise RuntimeError("render de auditoria explodiu apos 1h de debate")

    monkeypatch.setattr(cli, "render_audit_report", boom_audit)

    try:
        cli.main(_base_argv(tmp_path))
    except RuntimeError as exc:
        assert "render de auditoria" in str(exc)
    else:
        raise AssertionError("esperava o RuntimeError do render propagar")

    json_path = tmp_path / "outputs" / "linkedin_brazil_2026-06-13.json"
    assert json_path.exists(), "bundle JSON deve sobreviver a falha de render"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # O JSON é o único registro em disco do custo do debate; deve sobreviver
    # mesmo com o render de auditoria estourando depois.
    assert payload["bundle"]["model_token_costs"] == {"total_usd": 6.43}
    assert payload["bundle"]["model_influence_pct"] == {"GPT 5.5": 40.0}


def test_acquire_run_lock_second_acquisition_returns_none(tmp_path) -> None:
    """Bug histórico (ITEM 6): não existia lock; 3 runs no mesmo dia dobravam o
    gasto e rasgavam o read-modify-write da calibração. O fix adquire um flock
    exclusivo não-bloqueante. Com o lock já seguro por um fd, a 2a aquisição
    no mesmo arquivo deve retornar None (no código antigo a função nem existia)."""
    import worldcup_brazil.cli as cli_mod

    if cli_mod.fcntl is None:  # pragma: no cover - plataforma sem fcntl
        import pytest

        pytest.skip("fcntl indisponível nesta plataforma")

    lock_path = tmp_path / ".run.lock"
    first = cli_mod._acquire_run_lock(lock_path)
    assert first is not None
    try:
        second = cli_mod._acquire_run_lock(lock_path)
        assert second is None, "2a aquisicao do lock deve falhar (run concorrente)"
    finally:
        import os

        os.close(first)


def test_calibration_append_failure_does_not_block_mark_success(tmp_path, monkeypatch, capsys) -> None:
    """Bug histórico (ITEM 5): se append_prediction_log estourar (log corrompido,
    disco cheio), o run de US$6,43 não podia ser marcado como sucesso — então
    state.mark_success nunca rodava e o run inteiro re-disparava no próximo
    agendamento. O fix envolve a calibração em try/except que só avisa.

    No código antigo append_prediction_log estava fora de qualquer try/except no
    bloco, então uma exceção propagaria e state.mark_success NÃO seria chamado:
    state.last_success_at() ficaria None e este teste falharia."""
    monkeypatch.setattr(cli, "build_report_bundle_sync", lambda **kwargs: _FakeArtifacts())

    def boom_append(path, records):
        raise RuntimeError("log de calibracao corrompido")

    monkeypatch.setattr(cli, "append_prediction_log", boom_append)

    rc = cli.main(_base_argv(tmp_path))
    captured = capsys.readouterr()

    assert rc == 0
    state = cli.RunState(tmp_path / "state.json")
    assert state.last_success_at() is not None, "mark_success deve rodar apesar da falha de calibracao"
    assert "calibração não registrada" in captured.err
