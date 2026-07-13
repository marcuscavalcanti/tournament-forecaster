from __future__ import annotations

import json
import os
import re
import runpy
import shlex
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from datetime import date
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tournament_forecaster.config import load_tournament_document
from tournament_forecaster.resources import resource_path

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
    Path("CONTRIBUTING.md"),
    Path("Makefile"),
    Path("README.md"),
    Path("SECURITY.md"),
    Path("docs/ARCHITECTURE.md"),
    Path("docs/CONFIGURATION.md"),
    Path("docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md"),
    Path("docs/PROVIDERS.md"),
    Path("docs/assets/architecture"),
    Path("docs/knockout-stage-output-contract.md"),
    Path("docs/superpowers/plans/2026-07-10-tournament-forecaster-productization.md"),
    Path(
        "docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"
    ),
    Path("pyproject.toml"),
    Path("scripts/check_english_surface.py"),
    Path("tests/test_agents_fallbacks.py"),
    Path("tests/test_clean_wheel.py"),
    Path("tests/test_clean_source_install.py"),
    Path("tests/test_public_repository_contract.py"),
    Path("tests/test_readme_diagrams.py"),
    Path("tests/tournament_forecaster/test_package_resources.py"),
    Path("uv.lock"),
)

SUPPORTED_NATIVE_CLASSIFIERS = {
    "Operating System :: MacOS",
    "Operating System :: POSIX :: Linux",
}
DOCUMENTED_TOURNAMENT_FIELDS = {
    "schema_version",
    "tournament.id",
    "tournament.display_name",
    "tournament.season",
    "focus_team_id",
    "teams",
    "stages",
    "ratings",
    "completed_matches",
}
LEGACY_ALIAS_REMOVAL_VERSION = (0, 2, 0)
LEGACY_ALIAS_REMOVAL_DATE = date(2026, 10, 1)
LEGACY_POLICY_PATHS = (
    Path("README.md"),
    Path("SECURITY.md"),
    Path("docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md"),
    Path("docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"),
    Path("docs/superpowers/plans/2026-07-10-tournament-forecaster-productization.md"),
)
MYPY_STEP_NAME = "Package-wide strict Mypy for public package"
MYPY_COMMAND = (
    "mypy",
    "--no-incremental",
    "--exclude",
    "^worldcup_brazil/",
    "src/tournament_forecaster",
    "scripts/check_english_surface.py",
    "docs/assets/architecture/generate.py",
)
MYPY_DOCUMENTED_COMMAND = (
    "uv run --locked --extra dev mypy --no-incremental --exclude "
    "'^worldcup_brazil/' src/tournament_forecaster scripts/check_english_surface.py "
    "docs/assets/architecture/generate.py"
)
MYPY_STALE_DEBT_PHRASES = (
    "targeted strict mypy",
    "not yet green",
    "fix that debt directly",
    "no new suppressions",
)
STRICT_RUFF_STEP_NAME = "Strict Ruff for green release and provider contract targets"
STRICT_RUFF_TARGETS = (
    "src/tournament_forecaster/providers/security.py",
    "scripts/check_english_surface.py",
    "tests/test_clean_wheel.py",
    "tests/test_clean_source_install.py",
    "tests/test_public_repository_contract.py",
    "tests/test_readme_diagrams.py",
    "tests/tournament_forecaster/test_results_provider.py",
    "tests/tournament_forecaster/test_odds_provider.py",
    "docs/assets/architecture/generate.py",
)


def _tracked_files() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(Path(item.decode()) for item in completed.stdout.split(b"\0") if item)


def _workflow_job(workflow: str, job_name: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [a-z0-9-]+:\n|\Z)",
        workflow,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert match is not None, f"missing workflow job: {job_name}"
    return match.group("body")


def _workflow_step_command(workflow: str, step_name: str) -> list[str]:
    match = re.search(
        rf"^      - name: {re.escape(step_name)}\n"
        r"        run: >-\n"
        r"(?P<body>(?: {10}.*\n)+)",
        workflow,
        flags=re.MULTILINE,
    )
    assert match is not None, f"missing workflow step: {step_name}"
    command = " ".join(line.strip() for line in match.group("body").splitlines())
    return shlex.split(command)


def _version_triplet(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(?:v)?(\d+)\.(\d+)\.(\d+)", version)
    assert match is not None, f"unsupported project version: {version}"
    return tuple(int(part) for part in match.groups())


def _legacy_alias_removal_allowed(version: str, as_of: date) -> bool:
    return (
        _version_triplet(version) >= LEGACY_ALIAS_REMOVAL_VERSION
        and as_of >= LEGACY_ALIAS_REMOVAL_DATE
    )


def _assert_legacy_migration_contract(
    migration: str,
    policy_documents: tuple[str, ...],
    example_config: dict[str, object],
) -> None:
    normalized = migration.casefold()
    for phrase in (
        "no `.env` file or shell profile is loaded implicitly",
        "`--env-file`",
        "`--shell-env-file`",
        "`--bridges`",
        "`--no-bridges`",
        '"bridges_enabled": true',
        "`worldcup_enable_bridges=1`",
        "argument array, not a shell string",
        "`legacy_env_file`",
        "`legacy_shell_env_file`",
        "`legacy_bridges`",
    ):
        assert phrase in normalized

    json_blocks = re.findall(r"```json\n(.*?)```", migration, flags=re.DOTALL)
    examples = [json.loads(block) for block in json_blocks]
    bridge_examples = [item for item in examples if item.get("bridges_enabled") is True]
    assert len(bridge_examples) == 1
    agents = bridge_examples[0].get("agents")
    assert isinstance(agents, list) and agents
    browser_command = agents[0].get("browser_command")
    assert isinstance(browser_command, list) and all(
        isinstance(argument, str) and argument for argument in browser_command
    )

    assert example_config.get("bridges_enabled", False) is False
    for document in policy_documents:
        policy = document.casefold()
        assert "v0.1.x" in policy
        assert "v0.2.0" in policy
        assert "2026-10-01" in policy
        assert "one release cycle" not in policy
        assert "one-release-cycle" not in policy
    assert "both conditions" in normalized
    assert "neither threshold alone permits removal" in normalized


def _assert_quality_gate_contract(
    workflow: str,
    makefile: str,
    contributing: str,
) -> None:
    assert (
        "$(PYTHON) -m compileall -q src/tournament_forecaster worldcup_brazil scripts"
        in makefile
    )
    assert _workflow_step_command(workflow, MYPY_STEP_NAME) == list(MYPY_COMMAND)
    assert MYPY_DOCUMENTED_COMMAND in contributing
    normalized_contributing = contributing.casefold()
    for phrase in MYPY_STALE_DEBT_PHRASES:
        assert phrase not in normalized_contributing
    assert _workflow_step_command(workflow, STRICT_RUFF_STEP_NAME) == [
        "ruff",
        "check",
        *STRICT_RUFF_TARGETS,
        "--select",
        "E,F,I,UP,B,SIM",
    ]

def _legacy_make_dry_run(target: str, *assignments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    for key in (
        "LEGACY_ENV_FILE",
        "LEGACY_SHELL_ENV_FILE",
        "LEGACY_BRIDGES",
        "WORLDCUP_ENABLE_BRIDGES",
    ):
        environment.pop(key, None)
    return subprocess.run(
        [
            "make",
            "--dry-run",
            target,
            "RESULTS_INPUT=/private/tmp/results.json",
            "MARKET_ODDS_INPUT=/private/tmp/odds.json",
            *assignments,
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )


def _tracked_package_files() -> set[str]:
    return {
        f"/{path.as_posix()}"
        for path in _tracked_files()
        if path.as_posix().startswith(("src/tournament_forecaster/", "worldcup_brazil/"))
    }


def _assert_source_install_contract(
    readme: str,
    design: str,
    plan: str,
    product_flow: str,
) -> None:
    required = (
        "first source install requires package-index/network access for build dependencies",
        "hatchling is not vendored",
        "after installation, `simulate`, `init`, and `validate` run offline",
    )
    for phrase in required:
        assert phrase in readme.casefold()
    assert "source installation requires package-index/network access" in design.casefold()
    assert "source install requires package-index/network access" in plan.casefold()
    assert "after installation" in product_flow.casefold()
    for stale_claim in (
        "it requires only python 3.11 or newer and the cloned repository",
        "clean clone reaches valid json, markdown, and svg outputs through the "
        "documented four-line flow in less than five minutes without keys, network",
        "clone the repository and generate valid json, markdown, and svg forecasts "
        "through the documented four-line quick start without credentials, network access",
    ):
        assert stale_claim not in design.casefold()


def _assert_platform_contract(
    readme: str,
    security: str,
    providers: str,
    design: str,
    classifiers: set[str],
    release_gate: str,
) -> None:
    assert "`v0.1.0` supports macos and linux natively" in readme.casefold()
    assert "on windows, use wsl2" in readme.casefold()
    assert "native windows is not supported" in readme.casefold()
    for document in (security, providers):
        normalized = document.casefold()
        assert "posix" in normalized
        assert "wsl2" in normalized
        assert "native windows" in normalized
    assert "`v0.1.0` supports macos and linux natively and windows through wsl2" in (
        design.casefold()
    )
    assert classifiers >= SUPPORTED_NATIVE_CLASSIFIERS
    assert "Operating System :: OS Independent" not in classifiers
    assert not any("Windows" in classifier for classifier in classifiers)
    assert "ubuntu-latest" in release_gate
    assert "macos-latest" in release_gate
    assert "windows-latest" not in release_gate


def _assert_temporal_import_contract(
    providers: str,
    adding_provider: str,
    results_schema: dict[str, object],
    example_builder: str,
) -> None:
    normalized_providers = providers.casefold()
    normalized_adding = adding_provider.casefold()
    assert "generic preview/apply layer validates only normalized final facts" in (
        normalized_providers
    )
    assert "does not infer a schedule" in normalized_providers
    assert "authoritative kickoff" in normalized_providers
    assert "scripts/build_world_cup_2026_example.py" in providers
    assert "before preview" in normalized_adding
    assert "authoritative kickoff" in normalized_adding
    assert "generic apply rejects future" not in normalized_providers
    assert "apply rejects future" not in normalized_providers

    properties = results_schema["properties"]
    assert isinstance(properties, dict)
    results = properties["results"]
    assert isinstance(results, dict)
    items = results["items"]
    assert isinstance(items, dict)
    row_properties = items["properties"]
    assert isinstance(row_properties, dict)
    assert not {"kickoff_at", "observed_at", "result_at"} & set(row_properties)
    assert "retrieved_at must be after kickoff_at" in example_builder


def _assert_output_path_contract(readme: str, configuration: str, security: str) -> None:
    for document in (readme, configuration, security):
        normalized = document.casefold()
        assert "ancestor symlink or junction" in normalized
        assert "canonical path" in normalized
        assert "/tmp" in document
        assert "/private/tmp" in document


def _documented_core_fields(configuration: str) -> set[str]:
    section = configuration.split("## Core Fields\n", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^- `([^`]+)`", section, flags=re.MULTILINE))


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


def test_source_install_and_runtime_offline_contract_is_truthful() -> None:
    _assert_source_install_contract(
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (
            ROOT
            / "docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"
        ).read_text(encoding="utf-8"),
        (
            ROOT
            / "docs/superpowers/plans/2026-07-10-tournament-forecaster-productization.md"
        ).read_text(encoding="utf-8"),
        (ROOT / "docs/PRODUCT_FLOW.md").read_text(encoding="utf-8"),
    )


def test_source_install_contract_rejects_offline_clone_claim_mutations() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    design = (
        ROOT
        / "docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"
    ).read_text(encoding="utf-8")
    plan = (
        ROOT / "docs/superpowers/plans/2026-07-10-tournament-forecaster-productization.md"
    ).read_text(encoding="utf-8")
    product_flow = (ROOT / "docs/PRODUCT_FLOW.md").read_text(encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_source_install_contract(
            readme.replace(
                "first source install requires package-index/network access",
                "first source install is offline",
            ),
            design,
            plan,
            product_flow,
        )


def test_v0_1_platform_support_is_posix_native_and_windows_via_wsl2() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    _assert_platform_contract(
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "SECURITY.md").read_text(encoding="utf-8"),
        (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8"),
        (
            ROOT
            / "docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"
        ).read_text(encoding="utf-8"),
        set(metadata["project"]["classifiers"]),
        (ROOT / ".github/workflows/release-gate.yml").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("mutation", ["metadata", "readme", "release-gate"])
def test_platform_contract_rejects_native_windows_mutations(mutation: str) -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    providers = (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8")
    design = (
        ROOT
        / "docs/superpowers/specs/2026-07-10-open-source-tournament-forecaster-design.md"
    ).read_text(encoding="utf-8")
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    classifiers = set(metadata["project"]["classifiers"])
    release_gate = (ROOT / ".github/workflows/release-gate.yml").read_text(
        encoding="utf-8"
    )
    if mutation == "metadata":
        classifiers.add("Operating System :: OS Independent")
    elif mutation == "readme":
        readme = readme.replace("native Windows is not supported", "native Windows is supported")
    else:
        release_gate += "\n# windows-latest\n"

    with pytest.raises(AssertionError):
        _assert_platform_contract(
            readme,
            security,
            providers,
            design,
            classifiers,
            release_gate,
        )


def test_generic_import_contract_does_not_claim_schedule_aware_rejection() -> None:
    results_schema = json.loads(
        (
            ROOT / "src/tournament_forecaster/schemas/results.import.schema.json"
        ).read_text(encoding="utf-8")
    )
    _assert_temporal_import_contract(
        (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8"),
        (ROOT / "docs/ADDING_A_PROVIDER.md").read_text(encoding="utf-8"),
        results_schema,
        (ROOT / "scripts/build_world_cup_2026_example.py").read_text(
            encoding="utf-8"
        ),
    )


def test_temporal_import_contract_rejects_generic_apply_claim_mutation() -> None:
    providers = (ROOT / "docs/PROVIDERS.md").read_text(encoding="utf-8")
    adding_provider = (ROOT / "docs/ADDING_A_PROVIDER.md").read_text(encoding="utf-8")
    results_schema = json.loads(
        (
            ROOT / "src/tournament_forecaster/schemas/results.import.schema.json"
        ).read_text(encoding="utf-8")
    )
    example_builder = (ROOT / "scripts/build_world_cup_2026_example.py").read_text(
        encoding="utf-8"
    )

    with pytest.raises(AssertionError):
        _assert_temporal_import_contract(
            providers + "\nGeneric apply rejects future results.\n",
            adding_provider,
            results_schema,
            example_builder,
        )


def test_documented_tournament_fields_and_json_example_match_schema_and_runtime() -> None:
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    migration = (ROOT / "docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md").read_text(
        encoding="utf-8"
    )
    assert _documented_core_fields(configuration) == DOCUMENTED_TOURNAMENT_FIELDS
    assert "`tournament.id`" in migration
    assert "root `focus_team_id`" in migration
    assert "tournament_id" not in migration
    assert "default_focus_team_id" not in migration

    json_blocks = re.findall(r"```json\n(.*?)```", configuration, flags=re.DOTALL)
    assert len(json_blocks) == 1
    example = json.loads(json_blocks[0])
    with resource_path("schemas", "tournament.schema.json") as schema_path:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(example)
    tournament = load_tournament_document(example)
    assert tournament.id == example["tournament"]["id"]
    assert tournament.focus_team_id == example["focus_team_id"]

    for field in DOCUMENTED_TOURNAMENT_FIELDS:
        node = schema
        for segment in field.split("."):
            properties = node.get("properties")
            assert isinstance(properties, dict), f"{field} has no schema object at {segment}"
            assert segment in properties, f"documented field {field} is absent from schema"
            node = properties[segment]


def test_configuration_contract_rejects_stale_field_mutation() -> None:
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    mutated = configuration.replace("`focus_team_id`", "`default_focus_team_id`", 1)

    assert _documented_core_fields(mutated) != DOCUMENTED_TOURNAMENT_FIELDS


def test_legacy_migration_uses_explicit_safe_opt_ins_and_retains_aliases() -> None:
    migration = (ROOT / "docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md").read_text(
        encoding="utf-8"
    )
    policy_documents = tuple(
        (ROOT / path).read_text(encoding="utf-8") for path in LEGACY_POLICY_PATHS
    )
    example_config = json.loads(
        (ROOT / "config/worldcup_brazil.example.json").read_text(encoding="utf-8")
    )
    _assert_legacy_migration_contract(migration, policy_documents, example_config)

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    assert not _legacy_alias_removal_allowed(version, date.max)
    assert metadata["project"]["scripts"]["worldcup-brazil-report"] == (
        "worldcup_brazil.cli:main"
    )
    assert (ROOT / "worldcup_brazil/__init__.py").is_file()


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("No `.env` file or shell profile is loaded implicitly", "Load `.env` automatically"),
        ("`--bridges`", "implicit bridges"),
        ("v0.2.0", "v0.1.9"),
        ("2026-10-01", "2026-09-30"),
        ("argument array, not a shell string", "shell command string"),
    ],
)
def test_legacy_migration_contract_rejects_policy_mutations(old: str, new: str) -> None:
    migration_path = Path("docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md")
    migration = (ROOT / migration_path).read_text(encoding="utf-8")
    assert old in migration
    mutated_migration = migration.replace(old, new)
    policy_documents = tuple(
        mutated_migration
        if path == migration_path
        else (ROOT / path).read_text(encoding="utf-8")
        for path in LEGACY_POLICY_PATHS
    )
    example_config = json.loads(
        (ROOT / "config/worldcup_brazil.example.json").read_text(encoding="utf-8")
    )

    with pytest.raises(AssertionError):
        _assert_legacy_migration_contract(
            mutated_migration,
            policy_documents,
            example_config,
        )


def test_legacy_migration_contract_rejects_enabled_bridge_default_mutation() -> None:
    migration = (ROOT / "docs/MIGRATION_FROM_WORLDCUP_BRAZIL.md").read_text(
        encoding="utf-8"
    )
    policy_documents = tuple(
        (ROOT / path).read_text(encoding="utf-8") for path in LEGACY_POLICY_PATHS
    )
    example_config = json.loads(
        (ROOT / "config/worldcup_brazil.example.json").read_text(encoding="utf-8")
    )
    example_config["bridges_enabled"] = True

    with pytest.raises(AssertionError):
        _assert_legacy_migration_contract(migration, policy_documents, example_config)


@pytest.mark.parametrize(
    ("version", "as_of"),
    [
        ("0.1.9", date(2026, 10, 1)),
        ("0.2.0", date(2026, 9, 30)),
    ],
)
def test_legacy_alias_removal_rejects_single_threshold_mutations(
    version: str,
    as_of: date,
) -> None:
    assert not _legacy_alias_removal_allowed(version, as_of)
    assert _legacy_alias_removal_allowed("0.2.0", date(2026, 10, 1))


def test_legacy_make_targets_require_intentional_environment_and_bridge_opt_ins() -> None:
    default = _legacy_make_dry_run("daily")
    assert default.returncode == 0, default.stdout + default.stderr
    assert "--env-file" not in default.stdout
    assert "--shell-env-file" not in default.stdout
    assert "--bridges" not in default.stdout
    assert "--no-bridges" not in default.stdout

    daily = _legacy_make_dry_run(
        "daily",
        "LEGACY_ENV_FILE=/private/tmp/legacy.env",
        "LEGACY_BRIDGES=1",
    )
    assert daily.returncode == 0, daily.stdout + daily.stderr
    assert daily.stdout.count('--env-file "/private/tmp/legacy.env"') == 2
    assert "--bridges" in daily.stdout

    force = _legacy_make_dry_run(
        "force",
        "LEGACY_SHELL_ENV_FILE=/private/tmp/legacy-profile.env",
        "LEGACY_BRIDGES=0",
    )
    assert force.returncode == 0, force.stdout + force.stderr
    assert force.stdout.count(
        '--shell-env-file "/private/tmp/legacy-profile.env"'
    ) == 2
    assert "--no-bridges" in force.stdout
    assert "--force" in force.stdout

    doctor = _legacy_make_dry_run(
        "doctor",
        "LEGACY_ENV_FILE=/private/tmp/legacy.env",
        "LEGACY_BRIDGES=1",
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert '--env-file "/private/tmp/legacy.env"' in doctor.stdout
    assert "--bridges" in doctor.stdout

    invalid = _legacy_make_dry_run("daily", "LEGACY_BRIDGES=yes")
    assert invalid.returncode != 0
    assert "LEGACY_BRIDGES must be empty, 0, or 1" in invalid.stderr


def test_output_directory_symlink_policy_and_canonical_remedy_are_public() -> None:
    _assert_output_path_contract(
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8"),
        (ROOT / "SECURITY.md").read_text(encoding="utf-8"),
    )


def test_output_path_contract_rejects_noncanonical_remedy_mutation() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_output_path_contract(
            readme.replace("/private/tmp", "/tmp"),
            configuration,
            security,
        )


def test_readme_states_the_real_example_and_backtest_boundaries() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").casefold()
    for phrase in (
        "retrieved_at",
        "2026-07-13t16:35:34z",
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
        "normalized from",
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
        "test_clean_source_install.py",
        "test_readme_diagrams.py",
        "backtest",
        "--disable-socket",
        "full tracked test baseline",
        "pristine clone make validate",
        "generate.py --check-render",
    ):
        assert check in ci
    assert ci.count("worldcup_brazil") == 1
    assert "--exclude '^worldcup_brazil/'" in ci
    assert "tournament-forecast backtest" not in ci
    assert "tournament_forecast_offline" not in ci
    assert "./.github/workflows/ci.yml" in gate
    assert "full-history-secret-scan" in gate
    assert "ubuntu-latest" in gate
    assert "macos-latest" in gate
    assert "windows-latest" not in gate
    assert "online source-install onboarding" in gate
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


def test_ci_quality_gates_compile_and_type_check_the_entire_public_package() -> None:
    _assert_quality_gate_contract(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"),
        (ROOT / "Makefile").read_text(encoding="utf-8"),
        (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"),
    )


@pytest.mark.parametrize("mutation", ["compile", "mypy", "mypy-docs", "strict-ruff"])
def test_ci_quality_gate_contract_rejects_scope_mutations(mutation: str) -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    if mutation == "compile":
        makefile = makefile.replace("src/tournament_forecaster ", "", 1)
    elif mutation == "mypy":
        step, following_steps = ci.split(f"      - name: {MYPY_STEP_NAME}\n", maxsplit=1)
        ci = step + f"      - name: {MYPY_STEP_NAME}\n" + following_steps.replace(
            "          docs/assets/architecture/generate.py\n", "", 1
        )
    elif mutation == "mypy-docs":
        contributing = contributing.replace(
            MYPY_DOCUMENTED_COMMAND,
            "mypy src/tournament_forecaster",
            1,
        )
    elif mutation == "strict-ruff":
        ci = ci.replace(
            "          tests/tournament_forecaster/test_odds_provider.py\n",
            "",
            1,
        )

    with pytest.raises(AssertionError):
        _assert_quality_gate_contract(ci, makefile, contributing)


def test_ci_mypy_gate_types_the_entire_public_package() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert _workflow_step_command(ci, MYPY_STEP_NAME) == list(MYPY_COMMAND)

    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    mypy_config = metadata.get("tool", {}).get("mypy")
    assert isinstance(mypy_config, dict), "missing [tool.mypy] configuration"
    assert mypy_config.get("strict") is True, "[tool.mypy] strict must be true"
    assert mypy_config.get("follow_imports", "normal") == "normal", (
        "[tool.mypy] follow_imports must be absent or normal"
    )
    assert mypy_config.get("exclude") == ["^worldcup_brazil/"], (
        "[tool.mypy] exclude must retain only the legacy worldcup_brazil package"
    )


@pytest.mark.parametrize(
    ("mypy_settings", "expected_message"),
    [
        ("strict = false\n", r"\[tool\.mypy\] strict must be true"),
        (
            'strict = true\nfollow_imports = "skip"\n',
            r"\[tool\.mypy\] follow_imports must be absent or normal",
        ),
        (
            'strict = true\nfollow_imports = "silent"\n',
            r"\[tool\.mypy\] follow_imports must be absent or normal",
        ),
        (
            'strict = true\nfollow_imports = "error"\n',
            r"\[tool\.mypy\] follow_imports must be absent or normal",
        ),
        (
            'strict = true\nexclude = ["^worldcup_brazil/", '
            '"^src/tournament_forecaster/compatibility/"]\n',
            r"\[tool\.mypy\] exclude must retain only the legacy worldcup_brazil package",
        ),
    ],
)
def test_ci_mypy_gate_rejects_unsafe_configuration_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mypy_settings: str,
    expected_message: str,
) -> None:
    workflow_source = ROOT / ".github/workflows/ci.yml"
    workflow_copy = tmp_path / ".github/workflows/ci.yml"
    workflow_copy.parent.mkdir(parents=True)
    shutil.copyfile(workflow_source, workflow_copy)
    (tmp_path / "pyproject.toml").write_text(
        f"[tool.mypy]\n{mypy_settings}",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "ROOT", tmp_path)

    with pytest.raises(AssertionError, match=expected_message):
        test_ci_mypy_gate_types_the_entire_public_package()


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

    gitleaks = (ROOT / ".github/workflows/gitleaks.yml").read_text(encoding="utf-8")
    assert (
        "gitleaks/gitleaks-action@f586c14365d4643c6aa59d472ae6e984bf47bb34 # v2.3.8"
        in gitleaks
    )


def test_release_secret_gate_scans_full_history_for_an_empty_tag_payload(
    tmp_path: Path,
) -> None:
    gate = (ROOT / ".github/workflows/release-gate.yml").read_text(encoding="utf-8")
    job = _workflow_job(gate, "full-history-secret-scan")
    expected_checksum = "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb"

    assert "fetch-depth: 0" in job
    assert "gitleaks_8.30.1_linux_x64.tar.gz" in job
    assert expected_checksum in job
    assert "sha256sum --check" in job
    assert "github.event" not in job.casefold()
    assert "gitleaks/gitleaks-action" not in job

    scan_match = re.search(
        r"^\s+run:\s+(gitleaks git \. --redact --log-opts=--all)\s*$",
        job,
        flags=re.MULTILINE,
    )
    assert scan_match is not None

    event = {
        "created": True,
        "ref": "refs/tags/v0.1.0",
        "commits": [],
    }
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    assert json.loads(event_path.read_text(encoding="utf-8"))["commits"] == []

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "arguments.txt"
    fake_gitleaks = fake_bin / "gitleaks"
    fake_gitleaks.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_gitleaks.chmod(0o755)
    environment = os.environ.copy()
    environment["CAPTURE"] = str(capture)
    environment["GITHUB_EVENT_PATH"] = str(event_path)
    environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
    completed = subprocess.run(
        shlex.split(scan_match.group(1)),
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "git",
        ".",
        "--redact",
        "--log-opts=--all",
    ]


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


def test_package_build_targets_use_exact_tracked_file_allowlists() -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel = metadata["tool"]["hatch"]["build"]["targets"]["wheel"]
    sdist = metadata["tool"]["hatch"]["build"]["targets"]["sdist"]
    tracked_packages = _tracked_package_files()
    wheel_includes = set(wheel["include"])
    sdist_includes = set(sdist["include"])
    excludes = set(sdist["exclude"])

    assert "packages" not in wheel
    assert wheel["sources"] == ["src"]
    assert wheel_includes == tracked_packages
    assert {
        path
        for path in sdist_includes
        if path.startswith(("/src/tournament_forecaster/", "/worldcup_brazil/"))
    } == tracked_packages
    assert "/src/tournament_forecaster" not in sdist_includes
    assert "/worldcup_brazil" not in sdist_includes
    for required in (
        "/README.md",
        "/LICENSE",
        "/NOTICE.md",
        "/docs/PROVIDERS.md",
        "/examples/world-cup-2026-live/tournament.json",
    ):
        assert required in sdist_includes
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


def test_contaminated_package_trees_cannot_enter_sdist_or_wheel(
    tmp_path: Path,
) -> None:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    sdist_includes = set(
        metadata["tool"]["hatch"]["build"]["targets"]["sdist"]["include"]
    )
    tracked_packages = _tracked_package_files()
    token = f"{os.getpid()}-{tmp_path.name}"
    generated_root = ROOT / "src/tournament_forecaster" / f"strict-outputs-{token}"
    generic_marker_root = (
        ROOT / "src/tournament_forecaster" / f"adversarial-export-{token}"
    )
    legacy_marker_root = ROOT / "worldcup_brazil" / f"adversarial-export-{token}"
    arbitrary_modules = (
        ROOT / "src/tournament_forecaster" / f"adversarial_module_{os.getpid()}.py",
        ROOT / "worldcup_brazil" / f"adversarial_module_{os.getpid()}.py",
    )
    marker = "TASK6_PACKAGE_TREE_MARKER_MUST_NOT_SHIP"
    macos_path = "/Users/" + "marcus/private-source"
    windows_path = "C:\\Users\\" + "marcus\\private-source"
    dist = tmp_path / "dist"

    for path in (generated_root, generic_marker_root, legacy_marker_root, *arbitrary_modules):
        assert not path.exists()
    try:
        simulated = subprocess.run(
            [
                sys.executable,
                "-m",
                "tournament_forecaster",
                "simulate",
                "--config",
                "presets/synthetic-cup/tournament.json",
                "--iterations",
                "8",
                "--output-dir",
                str(generated_root),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert simulated.returncode == 0, simulated.stdout + simulated.stderr
        assert generated_root.is_dir()

        generic_marker_root.mkdir()
        legacy_marker_root.mkdir()
        (generic_marker_root / "forecast.json").write_text(
            f"{marker}\n{macos_path}\n",
            encoding="utf-8",
        )
        (legacy_marker_root / "report.md").write_text(
            f"{marker}\n{windows_path}\n",
            encoding="utf-8",
        )
        for module in arbitrary_modules:
            module.write_text(f'# {marker}\n', encoding="utf-8")

        for artifact_type in ("--sdist", "--wheel"):
            built = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    artifact_type,
                    "--no-isolation",
                    "--outdir",
                    str(dist),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )
            assert built.returncode == 0, built.stdout + built.stderr
    finally:
        for directory in (generated_root, generic_marker_root, legacy_marker_root):
            shutil.rmtree(directory, ignore_errors=True)
        for module in arbitrary_modules:
            module.unlink(missing_ok=True)

    sdist_archives = list(dist.glob("*.tar.gz"))
    wheel_archives = list(dist.glob("*.whl"))
    assert len(sdist_archives) == 1
    assert len(wheel_archives) == 1

    with tarfile.open(sdist_archives[0], mode="r:gz") as archive:
        files = [member for member in archive.getmembers() if member.isfile()]
        sdist_names = [member.name.split("/", 1)[1] for member in files]
        allowlisted_paths = tuple(item.removeprefix("/") for item in sdist_includes)
        unexpected = [
            name
            for name in sdist_names
            if name != "PKG-INFO"
            and not any(
                name == allowed or name.startswith(f"{allowed.rstrip('/')}/")
                for allowed in allowlisted_paths
            )
        ]
        assert not unexpected, f"sdist members outside the allowlist: {unexpected}"
        sdist_text = b"\n".join(
            stream.read()
            for member in files
            if member.size <= 2_000_000
            for stream in [archive.extractfile(member)]
            if stream is not None
        ).decode("utf-8", errors="ignore")

    with zipfile.ZipFile(wheel_archives[0]) as archive:
        wheel_names = [name for name in archive.namelist() if not name.endswith("/")]
        wheel_text = b"\n".join(
            archive.read(name)
            for name in wheel_names
            if archive.getinfo(name).file_size <= 2_000_000
        ).decode("utf-8", errors="ignore")

    expected_sdist_packages = {path.removeprefix("/") for path in tracked_packages}
    actual_sdist_packages = {
        name
        for name in sdist_names
        if name.startswith(("src/tournament_forecaster/", "worldcup_brazil/"))
    }
    assert actual_sdist_packages == expected_sdist_packages
    expected_wheel_packages = {
        path.removeprefix("/src/")
        if path.startswith("/src/")
        else path.removeprefix("/")
        for path in tracked_packages
    }
    actual_wheel_packages = {
        name
        for name in wheel_names
        if name.startswith(("tournament_forecaster/", "worldcup_brazil/"))
    }
    assert actual_wheel_packages == expected_wheel_packages
    for archive_names, archive_text in (
        (sdist_names, sdist_text),
        (wheel_names, wheel_text),
    ):
        assert not any(token in name for name in archive_names)
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
