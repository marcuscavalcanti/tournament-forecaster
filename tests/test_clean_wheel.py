from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).parents[1]
PROVIDER_VARIABLES = {
    "ANTHROPIC_API_KEY",
    "CODEX_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "THE_ODDS_API_KEY",
}


def _clean_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in PROVIDER_VARIABLES
        and not key.endswith("_API_KEY")
        and key != "PYTHONPATH"
    }
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def test_clean_wheel_quickstart_is_offline_and_init_validate_work_outside_repo(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1

    venv = tmp_path / "venv"
    created = subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = venv / ("Scripts/tournament-forecast.exe" if os.name == "nt" else "bin/tournament-forecast")
    installed = subprocess.run(
        ["uv", "pip", "install", "--python", str(python), "--no-deps", str(wheels[0])],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr

    outside = tmp_path / "outside-repository"
    outside.mkdir()
    audit_probe = """
import runpy
import sys

def deny_network(event, args):
    if event in {"socket.connect", "socket.bind"}:
        raise RuntimeError(f"network denied by audit hook: {event}")

sys.addaudithook(deny_network)
sys.argv = [sys.argv[1], "quickstart", "--iterations", "40", "--seed", "11"]
runpy.run_path(sys.argv[0], run_name="__main__")
"""
    quickstart = subprocess.run(
        [str(python), "-I", "-c", audit_probe, str(script)],
        cwd=outside,
        env=_clean_environment(),
        capture_output=True,
        text=True,
    )
    assert quickstart.returncode == 0, quickstart.stderr
    assert "tournament-forecast presets list" in quickstart.stdout

    artifacts = outside / "outputs" / "synthetic-cup" / "north-city"
    paths = [artifacts / "forecast.json", artifacts / "report.md", artifacts / "bracket.svg"]
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
    forecast = json.loads(paths[0].read_text(encoding="utf-8"))
    assert forecast["schema_version"] == 2
    assert forecast["tournament_id"] == "synthetic-cup"
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert str(REPOSITORY_ROOT) not in content
        assert str(Path.home()) not in content

    destination = outside / "configured-cup"
    initialized = subprocess.run(
        [str(script), "init", str(destination), "--template", "group-knockout"],
        cwd=outside,
        env=_clean_environment(),
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stderr
    validated = subprocess.run(
        [str(script), "validate", "--config", str(destination / "tournament.json")],
        cwd=outside,
        env=_clean_environment(),
        capture_output=True,
        text=True,
    )
    assert validated.returncode == 0, validated.stderr
    assert "Valid tournament:" in validated.stdout
