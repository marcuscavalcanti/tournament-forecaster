from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETIRED_PREFIXES = (
    "config/",
    "docs/decisions/",
    "src/tournament_forecaster/compatibility/",
)
RETIRED_PATHS = {
    "docs/knockout-stage-output-contract.md",
}
ALLOWED_ROOT_SCRIPTS = {
    "scripts/build_world_cup_2026_example.py",
}
REQUIRED_PUBLIC_FILES = {
    ".env.example",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".github/workflows/gitleaks.yml",
    ".github/workflows/release-gate.yml",
    ".github/workflows/release.yml",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE.md",
    "README.md",
    "SECURITY.md",
    "docs/ADDING_A_COMPETITION.md",
    "docs/ADDING_A_PROVIDER.md",
    "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md",
    "docs/DATA_POLICY.md",
    "docs/PRODUCT_FLOW.md",
    "docs/PROVIDERS.md",
    "examples/council.example.json",
}


def _tracked_paths() -> set[str]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return {
        item.decode("utf-8")
        for item in completed.stdout.split(b"\0")
        if item
    }


def test_public_repository_excludes_retired_non_generic_surfaces() -> None:
    tracked = _tracked_paths()
    retired = {
        path
        for path in tracked
        if path.startswith(RETIRED_PREFIXES) or path in RETIRED_PATHS
    }
    assert not retired
    assert tracked >= REQUIRED_PUBLIC_FILES

    scripts = {path for path in tracked if path.startswith("scripts/")}
    assert scripts == ALLOWED_ROOT_SCRIPTS


def test_public_package_exposes_only_the_generic_cli() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"] == {
        "tournament-forecast": "tournament_forecaster.cli:main"
    }

    packaged = "\n".join(
        metadata["tool"]["hatch"]["build"]["targets"]["wheel"]["include"]
    )
    assert "compatibility" not in packaged


def test_makefile_advertises_only_generic_public_commands() -> None:
    completed = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("Main commands:\n")
    for command in ("quickstart", "validate", "coverage", "complexity", "diagrams"):
        assert f"make {command}" in completed.stdout


def test_pristine_clone_make_validate_installs_declared_test_dependencies(
    tmp_path: Path,
) -> None:
    if os.environ.get("TOURNAMENT_FORECASTER_INNER_MAKE_VALIDATE") == "1":
        return

    clone = tmp_path / "pristine-clone"
    cloned = subprocess.run(
        ["git", "clone", "--local", "--no-hardlinks", "--quiet", str(ROOT), str(clone)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
    )
    assert cloned.returncode == 0, cloned.stderr

    patch = subprocess.run(
        ["git", "diff", "--binary", "HEAD"],
        cwd=ROOT,
        text=False,
        capture_output=True,
        check=True,
    ).stdout
    if patch:
        applied = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=clone,
            input=patch,
            capture_output=True,
        )
        assert applied.returncode == 0, applied.stderr.decode("utf-8")

    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["TOURNAMENT_FORECASTER_INNER_MAKE_VALIDATE"] = "1"
    for key in tuple(environment):
        if key.endswith("_API_KEY"):
            environment.pop(key)
    validated = subprocess.run(
        ["make", "validate"],
        cwd=clone,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr


def test_workflow_actions_are_pinned_and_release_scans_full_history() -> None:
    action_pattern = re.compile(
        r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?", re.MULTILINE
    )
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        content = workflow.read_text(encoding="utf-8")
        for action, version_comment in action_pattern.findall(content):
            if action.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (
                f"{workflow.name} has a mutable action reference: {action}"
            )
            assert re.fullmatch(r"v\d+(?:\.\d+){0,2}", version_comment), (
                f"{workflow.name} must retain a version comment for {action}"
            )

    release_gate = (ROOT / ".github/workflows/release-gate.yml").read_text(
        encoding="utf-8"
    )
    assert "fetch-depth: 0" in release_gate
    assert "gitleaks git . --redact --log-opts=--all" in release_gate


def test_readme_keeps_the_optional_council_and_offline_boundary_explicit() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = readme.casefold()

    for phrase in (
        "configuration-driven hybrid engine",
        "multi-llm council",
        "55% deterministic engine / 45% council consensus",
        "zero-key offline behavior",
        "deterministic baseline",
    ):
        assert phrase in normalized
    assert "vendor or governing-body affiliation" in normalized
