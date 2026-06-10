from pathlib import Path

from worldcup_brazil.source_memory import SourceMemory


def test_source_memory_starts_every_source_with_neutral_beta_prior(tmp_path: Path) -> None:
    memory = SourceMemory(tmp_path / "sources.json")

    assert memory.score("FIFA rankings") == 0.5
    assert memory.score("prediction market") == 0.5


def test_source_memory_rewards_more_accurate_sources(tmp_path: Path) -> None:
    memory = SourceMemory(tmp_path / "sources.json")
    memory.record_result("FIFA rankings", hit=True)
    memory.record_result("FIFA rankings", hit=True)
    memory.record_result("rumor account", hit=False)

    assert memory.score("FIFA rankings") > memory.score("rumor account")


def test_source_memory_persists_source_quality(tmp_path: Path) -> None:
    path = tmp_path / "sources.json"
    memory = SourceMemory(path)
    memory.record_result("sportsbook", hit=True)
    memory.save()

    reloaded = SourceMemory(path)

    assert reloaded.score("sportsbook") == memory.score("sportsbook")
