from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE_GATE = ROOT / ".github" / "workflows" / "release-gate.yml"


def _workflow_job(source: str, job_name: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [\w-]+:|\Z)",
        source,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing workflow job: {job_name}"
    return match.group(0)


def test_native_matrix_runs_results_provider_apply_suite_on_each_platform() -> None:
    native_install = _workflow_job(
        RELEASE_GATE.read_text(encoding="utf-8"), "native-clean-install"
    )

    assert "os: [ubuntu-latest, macos-latest]" in native_install
    assert re.search(
        r"^      - name: Platform-specific results provider apply\n"
        r"        run: python -m pytest -q "
        r"tests/tournament_forecaster/test_results_provider\.py$",
        native_install,
        flags=re.MULTILINE,
    ), "native matrix must run the results provider apply suite on macOS and Linux"
