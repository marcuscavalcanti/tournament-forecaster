from pathlib import Path

from worldcup_brazil.agents import default_agent_specs, load_agent_specs_from_config
from worldcup_brazil.consensus import AGENT_WEIGHTS, REQUIRED_AGENT_SLOTS
from worldcup_brazil.pipeline import DEFAULT_MODEL_PRICING_USD_PER_MILLION_TOKENS, load_config


def test_retired_agent_is_removed_from_runtime_contracts() -> None:
    retired = "Gr" + "ok"
    retired_slot = retired + " Latest Free"

    assert retired_slot not in REQUIRED_AGENT_SLOTS
    assert retired_slot not in AGENT_WEIGHTS
    assert retired_slot not in DEFAULT_MODEL_PRICING_USD_PER_MILLION_TOKENS
    assert retired_slot not in [spec.slot for spec in default_agent_specs()]
    assert retired_slot not in [
        spec.slot
        for spec in load_agent_specs_from_config(load_config(Path("config/worldcup_brazil.example.json")))
    ]


def test_retired_agent_has_no_source_references_in_project_files() -> None:
    retired_tokens = ["Gr" + "ok", "gr" + "ok", "X" + "AI", "x" + "ai"]
    allowed_paths = {
        Path("tests/test_retired_agent_removed.py"),
    }
    search_roots = [
        Path("README.md"),
        Path("config"),
        Path("scripts"),
        Path("worldcup_brazil"),
        Path("tests"),
    ]

    offenders: list[str] = []
    for root in search_roots:
        paths = [root] if root.is_file() else list(root.rglob("*"))
        for path in paths:
            if (
                path in allowed_paths
                or not path.is_file()
                or "__pycache__" in path.parts
                or path.suffix in {".pyc", ".png"}
            ):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in retired_tokens:
                if token in text:
                    offenders.append(f"{path}:{token}")

    assert offenders == []
