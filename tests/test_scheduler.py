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
