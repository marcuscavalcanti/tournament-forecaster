from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_force_refreshes_fifa_results_before_running_agents() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    force_block = makefile.split("\nforce:", 1)[1].split("\nwatch:", 1)[0]
    assert "update-results" in force_block
    assert "APPLY=1" in force_block
    assert force_block.index("update-results") < force_block.index("$(RUN_DAILY) --force")
