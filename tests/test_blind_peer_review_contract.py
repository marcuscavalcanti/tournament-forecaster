import json
import subprocess
import sys


def test_validate_blind_peer_review_contract_cli_passes() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/validate_blind_peer_review_contract.py"],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["mask_contract"]["version_tokens_masked"] is True
    assert payload["leakage_contract"]["exceeds_threshold"] is True
    assert payload["exit_gate_contract"]["blocks_on_acceptance_missing"] is True
    assert payload["exit_gate_contract"]["blocks_on_leakage"] is True
