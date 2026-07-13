from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
PROVIDER_VARIABLES = {
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "PERPLEXITY_API_KEY",
    "THE_ODDS_API_KEY",
}


def _clean_environment(home: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in PROVIDER_VARIABLES
        and not key.endswith("_API_KEY")
        and key != "PYTHONPATH"
    }
    environment["HOME"] = str(home)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run_audited(
    python: Path,
    probe: Path,
    script: Path,
    outside: Path,
    environment: dict[str, str],
    *arguments: str,
    module: bool = False,
) -> subprocess.CompletedProcess[str]:
    mode = "module" if module else ("entrypoint" if os.name == "nt" else "script")
    target = [] if mode != "script" else [str(script)]
    return subprocess.run(
        [str(python), "-I", str(probe), mode, *target, *arguments],
        cwd=outside,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_clean_wheel_all_cli_paths_are_offline_and_process_free_outside_repo(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(dist),
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stderr
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1
    assert REPOSITORY_ROOT.resolve() not in wheels[0].resolve().parents

    venv = tmp_path / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", str(venv)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert created.returncode == 0, created.stderr
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = venv / (
        "Scripts/tournament-forecast.exe"
        if os.name == "nt"
        else "bin/tournament-forecast"
    )
    installed = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            str(wheels[0]),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    assert script.is_file()

    outside = tmp_path / "outside-repository"
    outside.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()
    for name in (".zshrc", ".bashrc", ".bash_profile", ".profile"):
        (fake_home / name).write_text("raise if opened\n", encoding="utf-8")
    environment = _clean_environment(fake_home)
    assert "PYTHONPATH" not in environment
    assert not any(key.endswith("_API_KEY") for key in environment)

    audit_probe = tmp_path / "audit_probe.py"
    audit_probe.write_text(
        """
from importlib.metadata import entry_points
import os
import runpy
import socket
import sys

SHELL_PROFILES = {
    ".zshrc",
    ".bashrc",
    ".bash_profile",
    ".profile",
    "config.fish",
}

mode, *arguments = sys.argv[1:]
precreated_socket = None
if mode in {"selftest-connect", "selftest-bind", "selftest-sendto"}:
    socket_type = socket.SOCK_DGRAM if mode == "selftest-sendto" else socket.SOCK_STREAM
    precreated_socket = socket.socket(socket.AF_INET, socket_type)

def deny_side_effects(event, args):
    if event.startswith("socket."):
        raise RuntimeError(f"network denied by audit hook: {event}")
    if (
        event == "subprocess.Popen"
        or event == "os.system"
        or event.startswith("os.spawn")
        or event == "os.posix_spawn"
    ):
        raise RuntimeError(f"process denied by audit hook: {event}")
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        candidate = os.fsdecode(args[0]).replace("\\\\", "/")
        if candidate.rsplit("/", 1)[-1] in SHELL_PROFILES:
            raise RuntimeError(f"shell profile denied by audit hook: {candidate}")

sys.addaudithook(deny_side_effects)
if mode == "selftest-create":
    socket.socket()
elif mode == "selftest-dns":
    socket.getaddrinfo("localhost", 80)
elif mode == "selftest-connect":
    precreated_socket.connect(("127.0.0.1", 9))
elif mode == "selftest-bind":
    precreated_socket.bind(("127.0.0.1", 0))
elif mode == "selftest-sendto":
    precreated_socket.sendto(b"probe", ("127.0.0.1", 9))
elif mode == "selftest-subprocess":
    import subprocess
    subprocess.run([sys.executable, "-c", "pass"], check=True)
elif mode == "selftest-system":
    os.system("true")
elif mode == "selftest-profile":
    open(arguments[0], encoding="utf-8").read()
elif mode == "module":
    sys.argv = ["tournament_forecaster", *arguments]
    runpy.run_module("tournament_forecaster", run_name="__main__")
elif mode == "script":
    script, *arguments = arguments
    sys.argv = [script, *arguments]
    runpy.run_path(script, run_name="__main__")
else:
    command = next(
        item for item in entry_points(group="console_scripts")
        if item.name == "tournament-forecast"
    )
    sys.argv = ["tournament-forecast", *arguments]
    raise SystemExit(command.load()())
""".lstrip(),
        encoding="utf-8",
    )

    policy_selftests = {
        "selftest-create": "network denied by audit hook: socket.__new__",
        "selftest-dns": "network denied by audit hook: socket.getaddrinfo",
        "selftest-connect": "network denied by audit hook: socket.connect",
        "selftest-bind": "network denied by audit hook: socket.bind",
        "selftest-sendto": "network denied by audit hook: socket.sendto",
        "selftest-subprocess": "process denied by audit hook: subprocess.Popen",
        "selftest-system": "process denied by audit hook: os.system",
        "selftest-profile": "shell profile denied by audit hook",
    }
    for mode, denial in policy_selftests.items():
        arguments = [str(fake_home / ".profile")] if mode == "selftest-profile" else []
        denied = subprocess.run(
            [str(python), "-I", str(audit_probe), mode, *arguments],
            cwd=outside,
            env=environment,
            capture_output=True,
            text=True,
        )
        assert denied.returncode != 0
        assert denial in denied.stderr

    shutil.copytree(
        REPOSITORY_ROOT / "examples/world-cup-2026-live",
        outside / "examples/world-cup-2026-live",
    )
    readme_simulation = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "simulate",
        "--config",
        "examples/world-cup-2026-live/tournament.json",
        "--iterations",
        "10000",
        "--output-dir",
        "outputs",
    )
    assert readme_simulation.returncode == 0, readme_simulation.stderr

    stable_alias = outside / "outputs/fifa-world-cup-2026-live/france"
    readme_artifacts = (
        stable_alias / "forecast.json",
        stable_alias / "report.md",
        stable_alias / "bracket.svg",
    )
    assert stable_alias.is_symlink()
    assert all(path.is_file() and path.stat().st_size > 0 for path in readme_artifacts)

    readme_backtest = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "backtest",
        "--input",
        "examples/world-cup-2026-live/backtest.json",
    )
    assert readme_backtest.returncode == 0, readme_backtest.stderr
    backtest_report = json.loads(readme_backtest.stdout)
    assert backtest_report["ok"] is True
    assert backtest_report["model_version"] == "poisson-elo-v1"
    assert backtest_report["sample_size"] == 72

    quickstart = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "quickstart",
        "--iterations",
        "40",
        "--seed",
        "11",
    )
    assert quickstart.returncode == 0, quickstart.stderr
    assert "tournament-forecast presets list" in quickstart.stdout

    artifacts = outside / "outputs" / "synthetic-cup" / "north-city"
    paths = [artifacts / "forecast.json", artifacts / "report.md", artifacts / "bracket.svg"]
    assert artifacts.is_symlink()
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)
    forecast = json.loads(paths[0].read_text(encoding="utf-8"))
    assert forecast["schema_version"] == 2
    assert forecast["tournament_id"] == "synthetic-cup"
    assert forecast["stage_order"] == ["group-stage", "semi-finals", "final"]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert str(REPOSITORY_ROOT) not in content
        assert str(Path.home()) not in content

    destination = outside / "configured-cup"
    initialized = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "init",
        str(destination),
        "--template",
        "group-knockout",
    )
    assert initialized.returncode == 0, initialized.stderr
    config = destination / "tournament.json"

    validated = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "validate",
        "--config",
        str(config),
    )
    assert validated.returncode == 0, validated.stderr

    simulation_root = outside / "simulated"
    simulated = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "simulate",
        "--config",
        str(config),
        "--focus-team",
        "bravo-town",
        "--iterations",
        "30",
        "--output-dir",
        str(simulation_root),
    )
    assert simulated.returncode == 0, simulated.stderr
    simulated_forecast = (
        simulation_root
        / "group-knockout-template"
        / "bravo-town"
        / "forecast.json"
    )

    rendered = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "report",
        "--forecast",
        str(simulated_forecast),
        "--output-dir",
        str(outside / "rendered"),
    )
    assert rendered.returncode == 0, rendered.stderr

    doctor = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "doctor",
    )
    assert doctor.returncode == 0, doctor.stderr

    presets = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "presets",
        "list",
    )
    assert presets.returncode == 0, presets.stderr
    assert "synthetic-cup" in presets.stdout

    module_entry = _run_audited(
        python,
        audit_probe,
        script,
        outside,
        environment,
        "presets",
        "list",
        module=True,
    )
    assert module_entry.returncode == 0, module_entry.stderr
    assert module_entry.stdout == presets.stdout
