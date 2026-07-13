from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
QUICKSTART = "\n".join(
    (
        "git clone https://github.com/marcuscavalcanti/worldcup2026.git",
        "cd worldcup2026",
        "python3 -m venv .venv && . .venv/bin/activate && python -m pip install .",
        "tournament-forecast simulate --config "
        "examples/world-cup-2026-live/tournament.json "
        "--iterations 10000 --output-dir outputs",
    )
)
REQUIRED_FILES = {
    "LICENSE",
    "NOTICE.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    ".env.example",
    ".github/workflows/ci.yml",
    ".github/workflows/gitleaks.yml",
    ".github/workflows/release.yml",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/pull_request_template.md",
    ".github/dependabot.yml",
    "docs/CONFIGURATION.md",
    "docs/PROVIDERS.md",
    "docs/ADDING_A_COMPETITION.md",
    "docs/ADDING_A_PROVIDER.md",
    "docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md",
    "docs/DATA_POLICY.md",
    "scripts/check_english_surface.py",
}


def _tracked_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(Path(item.decode()) for item in completed.stdout.split(b"\0") if item)


def test_required_public_repository_files_exist() -> None:
    missing = sorted(path for path in REQUIRED_FILES if not (ROOT / path).is_file())
    assert not missing, f"missing public repository files: {missing}"


def test_readme_leads_with_exact_working_four_line_quickstart() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert readme.splitlines()[0] == "# Tournament Forecaster"

    shell_blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", readme, flags=re.DOTALL)
    assert shell_blocks
    assert shell_blocks[0].strip() == QUICKSTART
    assert len(shell_blocks[0].strip().splitlines()) == 4
    assert readme.index(QUICKSTART) < readme.index("Architecture")
    assert "tournament-forecast quickstart" in readme
    assert (
        "tournament-forecast backtest --input examples/world-cup-2026-live/backtest.json"
    ) in readme
    assert "outputs/fifa-world-cup-2026-live/france/forecast.json" in readme
    assert "outputs/fifa-world-cup-2026-live/france/report.md" in readme
    assert "outputs/fifa-world-cup-2026-live/france/bracket.svg" in readme


def test_readme_states_the_real_example_and_backtest_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    for phrase in (
        "retrieved_at",
        "2026-07-13t12:21:03z",
        "100 completed facts",
        "72 group",
        "16 r32",
        "8 r16",
        "4 qf",
        "0 sf",
        "0 final",
        "france",
        "france-spain",
        "england-argentina",
        "frozen ratings",
        "normalized snapshot",
        "rps",
        "multiclass brier",
        "log loss",
        "top-pick accuracy",
        "sample size",
        "one tournament",
        "project-authored pre-tournament rating seed",
        "deterministic rating/poisson core",
        "not the optional multi-model council",
        "not historical linkedin posts",
        "not proof of universal calibration",
        "0.146838",
        "0.498738",
        "0.832030",
        "0.625000",
        "72",
    ):
        assert phrase in readme


def test_public_repo_rejects_tracked_runtime_and_private_material() -> None:
    tracked = _tracked_files()
    forbidden_parts = {
        ".env",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".superpowers",
        "__pycache__",
        "attachments",
        "outputs",
        "raw_provider_payloads",
        "provider_raw",
    }
    forbidden_exact = {
        Path("config/worldcup_brazil.json"),
        Path("data/run_state.json"),
        Path("data/watchdog.jsonl"),
    }
    violations = [
        path
        for path in tracked
        if path in forbidden_exact
        or any(part.casefold() in forbidden_parts for part in path.parts)
        or path.name.endswith((".pyc", ".pyo"))
    ]
    assert not violations, f"tracked private/runtime files: {violations}"

    personal_paths: list[Path] = []
    public_roots = (
        "src/tournament_forecaster/",
        "docs/",
        "examples/",
        "presets/",
        ".github/",
    )
    for path in tracked:
        path_text = path.as_posix()
        if path_text not in {
            "README.md",
            "NOTICE.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
        } and not path_text.startswith(public_roots):
            continue
        candidate = ROOT / path
        if not candidate.is_file() or candidate.suffix.casefold() in {".png", ".jpg", ".jpeg"}:
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "/Users/" in content or "C:\\Users\\" in content:
            personal_paths.append(path)
    assert not personal_paths, f"personal absolute paths in tracked files: {personal_paths}"


def test_public_governance_and_provider_contracts_are_explicit() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8").casefold()
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8").casefold()
    providers = (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8").casefold()

    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Marcus Cavalcanti" in license_text
    for entity in (
        "fifa",
        "uefa",
        "conmebol",
        "opta",
        "model vendors",
        "bookmakers",
        "data providers",
    ):
        assert entity in notice
    assert "no protected logos" in notice
    assert "private vulnerability reporting" in security
    for boundary in (
        "trusted configuration",
        "local command",
        "symlink",
        "provider key",
        "data provenance",
    ):
        assert boundary in security
    for provider_requirement in (
        "competition `17`",
        "season `285023`",
        "browser",
        "network",
        "calendar",
        "quarter-final",
        "quarter-finals",
        "the_odds_api_key",
        "preview",
        "--apply",
        "rotation",
        "revocation",
        "external contracts",
        "may change",
    ):
        assert provider_requirement in providers


def test_package_metadata_is_publication_ready_without_runtime_dependencies() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert project["requires-python"] == ">=3.11"
    assert project["dependencies"] == []
    assert "dev" in project["optional-dependencies"]
    assert set(project["urls"]) >= {"Homepage", "Documentation", "Issues", "Source"}
    classifiers = set(project["classifiers"])
    assert "License :: OSI Approved :: MIT License" in classifiers
    for version in ("3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers


def test_workflows_are_offline_scoped_and_do_not_publish() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8").casefold()
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").casefold()
    assert '["3.11", "3.12", "3.13"]' in ci
    for check in (
        "ruff",
        "mypy",
        "check_english_surface.py",
        "test_public_repository_contract.py",
        "test_format_contracts.py",
        "test_clean_wheel.py",
        "backtest",
    ):
        assert check in ci
    assert "worldcup_brazil" not in ci
    assert "build" in release
    assert "twine check" in release
    assert "pypi" not in release
    assert "publish" not in release


def test_english_public_surface_scanner_passes() -> None:
    environment = os.environ.copy()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "scripts/check_english_surface.py"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_no_protected_provider_logo_assets_are_bundled() -> None:
    protected_names = {"fifa", "uefa", "conmebol", "opta"}
    logo_assets = [
        path
        for path in _tracked_files()
        if path.suffix.casefold() in {".svg", ".png", ".jpg", ".jpeg", ".webp"}
        and any(name in path.name.casefold() for name in protected_names)
    ]
    assert not logo_assets
