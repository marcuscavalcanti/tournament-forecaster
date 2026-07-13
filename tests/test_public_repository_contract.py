from __future__ import annotations

import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tarfile
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
    ".github/workflows/release-gate.yml",
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
    "docs/assets/architecture/README.md",
    "docs/assets/architecture/generate.py",
    "docs/assets/architecture/manifest.json",
    "scripts/check_english_surface.py",
}

TASK6_OVERLAY_PATHS = (
    Path(".env.example"),
    Path(".github/workflows"),
    Path("Makefile"),
    Path("README.md"),
    Path("SECURITY.md"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/CONFIGURATION.md"),
    Path("docs/PROVIDERS.md"),
    Path("docs/assets/architecture"),
    Path("docs/knockout-stage-output-contract.md"),
    Path("pyproject.toml"),
    Path("scripts/check_english_surface.py"),
    Path("tests/test_agents_fallbacks.py"),
    Path("tests/test_clean_wheel.py"),
    Path("tests/test_public_repository_contract.py"),
    Path("tests/test_readme_diagrams.py"),
    Path("uv.lock"),
)


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


def test_readme_live_simulation_and_backtest_are_socket_denied(
    tmp_path: Path,
    monkeypatch,
    capsys,
    socket_disabled,
) -> None:
    del socket_disabled
    from tournament_forecaster.cli import main

    source = ROOT / "examples/world-cup-2026-live"
    destination = tmp_path / "examples/world-cup-2026-live"
    shutil.copytree(source, destination)
    monkeypatch.chdir(tmp_path)

    simulated = main(
        [
            "simulate",
            "--config",
            "examples/world-cup-2026-live/tournament.json",
            "--iterations",
            "10000",
            "--output-dir",
            "outputs",
        ]
    )
    assert simulated == 0
    capsys.readouterr()

    stable_alias = tmp_path / "outputs/fifa-world-cup-2026-live/france"
    artifacts = (
        stable_alias / "forecast.json",
        stable_alias / "report.md",
        stable_alias / "bracket.svg",
    )
    assert stable_alias.is_symlink()
    assert all(path.is_file() and path.stat().st_size > 0 for path in artifacts)

    backtested = main(
        ["backtest", "--input", "examples/world-cup-2026-live/backtest.json"]
    )
    assert backtested == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["model_version"] == "poisson-elo-v1"
    assert report["sample_size"] == 72


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

    named_personal_paths = []
    for path in tracked:
        candidate = ROOT / path
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        named_macos_home = "/Users/" + "marcus"
        named_windows_home = "C:\\Users\\" + "marcus"
        if named_macos_home in content or named_windows_home in content:
            named_personal_paths.append(path)
    assert not named_personal_paths, (
        f"named personal absolute paths in tracked files: {named_personal_paths}"
    )


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
    gate = (ROOT / ".github/workflows/release-gate.yml").read_text(
        encoding="utf-8"
    ).casefold()
    gitleaks = (ROOT / ".github/workflows/gitleaks.yml").read_text(
        encoding="utf-8"
    ).casefold()
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8").casefold()
    assert "workflow_call" in ci
    assert "workflow_call" in gitleaks
    assert "workflow_call" in gate
    assert '["3.11", "3.12", "3.13"]' in ci
    for check in (
        "ruff",
        "mypy",
        "check_english_surface.py",
        "test_public_repository_contract.py",
        "test_format_contracts.py",
        "test_clean_wheel.py",
        "test_readme_diagrams.py",
        "backtest",
        "--disable-socket",
        "full tracked test baseline",
        "pristine clone make validate",
        "generate.py --check-render",
    ):
        assert check in ci
    assert "worldcup_brazil" not in ci
    assert "tournament-forecast backtest" not in ci
    assert "tournament_forecast_offline" not in ci
    assert "./.github/workflows/ci.yml" in gate
    assert "./.github/workflows/gitleaks.yml" in gate
    assert "ubuntu-latest" in gate
    assert "macos-latest" in gate
    assert "windows-latest" in gate
    assert "tests/test_clean_wheel.py" in gate
    assert "./.github/workflows/release-gate.yml" in release
    assert "github.ref_type" in release
    assert "github.ref_name" in release
    assert "tomllib" in release
    assert 'project["version"]' in release
    assert "v{version}" in release
    assert "needs: [version-contract, release-gate]" in release
    assert "build" in release
    assert "twine check" in release
    assert "pypi" not in release
    assert "publish" not in release


def test_workflow_actions_are_pinned_to_immutable_commits() -> None:
    action_pattern = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?", re.MULTILINE)
    for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
        content = workflow.read_text(encoding="utf-8")
        for action, version_comment in action_pattern.findall(content):
            if action.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), (
                f"{workflow.name} has a mutable action reference: {action}"
            )
            assert re.fullmatch(r"v\d+(?:\.\d+){0,2}", version_comment), (
                f"{workflow.name} must retain a Dependabot-readable version comment for {action}"
            )


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


def test_makefile_help_is_english_and_scanned() -> None:
    scanner = runpy.run_path(str(ROOT / "scripts/check_english_surface.py"))
    assert scanner["_is_public"](Path("Makefile"))

    completed = subprocess.run(
        ["make", "help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.startswith("Main commands:\n")
    assert "make quickstart generates a complete synthetic offline forecast" in completed.stdout


def test_pristine_clone_make_validate_installs_declared_test_dependencies(
    tmp_path: Path,
) -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "PYTEST ?= uv run --locked --extra dev python -m pytest" in makefile
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
    for relative in TASK6_OVERLAY_PATHS:
        source = ROOT / relative
        destination = clone / relative
        if source.is_dir():
            shutil.rmtree(destination, ignore_errors=True)
            shutil.copytree(source, destination)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    assert not (clone / ".venv").exists()

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


def test_sdist_is_an_explicit_public_allowlist_without_local_material(
    tmp_path: Path,
) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist = metadata["tool"]["hatch"]["build"]["targets"]["sdist"]
    includes = set(sdist["include"])
    excludes = set(sdist["exclude"])
    for required in (
        "/src/tournament_forecaster",
        "/worldcup_brazil",
        "/README.md",
        "/LICENSE",
        "/NOTICE.md",
        "/docs/PROVIDERS.md",
        "/examples/world-cup-2026-live/tournament.json",
    ):
        assert required in includes
    for forbidden in (
        "/.github",
        "/.superpowers",
        "/docs/superpowers",
        "/outputs",
        "/raw_provider_payloads",
        "/scripts",
        "/tests",
    ):
        assert forbidden in excludes

    probe_root = ROOT / f"task6-untracked-output-{os.getpid()}-{tmp_path.name}"
    marker = "TASK6_UNTRACKED_OUTPUT_MUST_NOT_SHIP"
    probe_root.mkdir()
    (probe_root / "forecast.json").write_text(marker, encoding="utf-8")
    dist = tmp_path / "dist"
    try:
        built = subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--sdist",
                "--no-isolation",
                "--outdir",
                str(dist),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)
    assert built.returncode == 0, built.stdout + built.stderr

    archives = list(dist.glob("*.tar.gz"))
    assert len(archives) == 1
    with tarfile.open(archives[0], mode="r:gz") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        relative_names = [member.name.split("/", 1)[1] for member in files]
        allowlisted_paths = tuple(item.removeprefix("/") for item in includes)
        unexpected = [
            name
            for name in relative_names
            if name != "PKG-INFO"
            and not any(
                name == allowed or name.startswith(f"{allowed.rstrip('/')}/")
                for allowed in allowlisted_paths
            )
        ]
        assert not unexpected, f"sdist members outside the allowlist: {unexpected}"
        assert not any(
            name.startswith(
                (
                    ".github/",
                    ".superpowers/",
                    "docs/superpowers/",
                    "outputs/",
                    "scripts/",
                    "tests/",
                )
            )
            for name in relative_names
        )
        assert not any(probe_root.name in name for name in relative_names)
        text_members = []
        for member in files:
            if member.size > 2_000_000:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                continue
            try:
                text_members.append(stream.read().decode("utf-8"))
            except UnicodeDecodeError:
                continue
    archive_text = "\n".join(text_members)
    assert marker not in archive_text
    assert "/Users/" not in archive_text
    assert "C:\\Users\\" not in archive_text


def test_internal_docs_are_unshipped_and_knockout_contract_is_public_english() -> None:
    scanner = runpy.run_path(str(ROOT / "scripts/check_english_surface.py"))
    assert scanner["_is_public"](Path("docs/knockout-stage-output-contract.md"))
    assert scanner["_is_internal"](Path("docs/superpowers/plans/internal.md"))
    assert not scanner["_is_public"](Path("docs/superpowers/plans/internal.md"))

    contract = (ROOT / "docs/knockout-stage-output-contract.md").read_text(
        encoding="utf-8"
    )
    assert contract.startswith("# Output Contract: Knockout Stage\n")


def test_generic_cli_does_not_advertise_unimplemented_bridge_controls() -> None:
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8").casefold()
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8").casefold()
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text(
        encoding="utf-8"
    ).casefold()
    providers = (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8").casefold()

    assert "bridge_command" not in env_example
    assert "generic cli does not implement local command bridges" in security
    assert "future bridge" in security
    for content in (readme, configuration, providers):
        assert "explicitly enables" not in content
        assert "separately enabled" not in content


def test_only_manifest_approved_architecture_assets_are_bundled() -> None:
    asset_directory = ROOT / "docs/assets/architecture"
    manifest = json.loads((asset_directory / "manifest.json").read_text(encoding="utf-8"))
    approved = {
        Path("docs/assets/architecture") / record[key]
        for record in manifest["assets"]
        for key in ("svg", "png")
    }
    tracked_images = {
        path
        for path in _tracked_files()
        if path.suffix.casefold() in {".svg", ".png", ".jpg", ".jpeg", ".webp"}
    }
    assert tracked_images == approved

    completed = subprocess.run(
        [sys.executable, str(asset_directory / "generate.py"), "--check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
