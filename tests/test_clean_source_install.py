from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[1]
REMOTE_URL = "https://github.com/marcuscavalcanti/tournament-forecaster.git"


def test_clean_source_install_executes_literal_readme_quickstart(
    tmp_path: Path,
) -> None:
    if os.environ.get("TOURNAMENT_FORECASTER_INNER_MAKE_VALIDATE") == "1":
        pytest.skip("the outer clean source-install test owns this recursive boundary")
    assert os.name == "posix", "v0.1.0 source onboarding is native POSIX only"

    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    shell_blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", readme, flags=re.DOTALL)
    assert shell_blocks
    commands = shell_blocks[0].strip()
    assert len(commands.splitlines()) == 4

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PIP_NO_INDEX", "PIP_FIND_LINKS", "PIP_NO_BUILD_ISOLATION"}
        and key != "PYTHONPATH"
        and not key.endswith("_API_KEY")
    }
    environment.update(
        {
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": f"url.{REPOSITORY_ROOT.as_uri()}.insteadOf",
            "GIT_CONFIG_VALUE_0": REMOTE_URL,
            "GIT_CONFIG_KEY_1": "protocol.file.allow",
            "GIT_CONFIG_VALUE_1": "always",
            "HOME": str(fake_home),
            "PIP_CACHE_DIR": str(tmp_path / "empty-pip-cache"),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_CACHE_DIR": "1",
            "PYTHONNOUSERSITE": "1",
        }
    )

    completed = subprocess.run(
        ["/bin/sh", "-eu", "-c", commands],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=300,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    forecast_path = (
        tmp_path
        / "tournament-forecaster"
        / "outputs"
        / "fifa-world-cup-2026-live"
        / "france"
        / "forecast.json"
    )
    assert forecast_path.is_file()
    forecast = json.loads(forecast_path.read_text(encoding="utf-8"))
    assert forecast["tournament_id"] == "fifa-world-cup-2026-live"
    assert forecast["focus_team_id"] == "france"
