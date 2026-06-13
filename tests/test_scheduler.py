from datetime import datetime, timedelta, timezone
from pathlib import Path

from worldcup_brazil.scheduler import RunState, should_run


def test_should_run_when_no_previous_successful_run(tmp_path: Path) -> None:
    state = RunState(path=tmp_path / "state.json")
    now = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)

    assert should_run(state, now=now, interval=timedelta(days=3))


def test_should_not_run_before_three_day_interval(tmp_path: Path) -> None:
    state = RunState(path=tmp_path / "state.json")
    state.mark_success(datetime(2026, 6, 12, 12, tzinfo=timezone.utc))
    now = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)

    assert not should_run(state, now=now, interval=timedelta(days=3))


def test_should_run_after_three_day_interval(tmp_path: Path) -> None:
    state = RunState(path=tmp_path / "state.json")
    state.mark_success(datetime(2026, 6, 11, 11, 59, tzinfo=timezone.utc))
    now = datetime(2026, 6, 14, 12, tzinfo=timezone.utc)

    assert should_run(state, now=now, interval=timedelta(days=3))


def test_corrupt_run_state_is_quarantined_and_treated_as_no_state(tmp_path: Path) -> None:
    """Bug histórico (ITEM 5): um torn write de run_state.json fazia
    last_success_at() estourar JSONDecodeError a cada run, criando uma falha
    auto-perpetuante — cada run completava o debate de US$6,43 e só então
    estourava na leitura do estado, diariamente, até reparo manual.

    No código antigo last_success_at fazia json.loads sem try/except, então este
    teste levantaria JSONDecodeError em vez de retornar None. O fix captura
    (JSONDecodeError, ValueError), renomeia o arquivo para sufixo .corrupt e
    retorna None (= "sem run anterior")."""
    state_path = tmp_path / "state.json"
    state_path.write_text('{"last_success_at": "2026-06-1', encoding="utf-8")  # torn JSON
    state = RunState(path=state_path)

    assert state.last_success_at() is None
    assert not state_path.exists()
    # sufixo único (.corrupt.<timestamp>) para não clobbar forense de incidente anterior
    assert list(tmp_path.glob("state.json.corrupt*"))
