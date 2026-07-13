from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import scripts.update_market_odds as market_odds


def _config_path(tmp_path: Path) -> Path:
    config = {
        "brazil_team_name": "Brasil",
        "groups_config": {
            "groups": {
                "C": [
                    {"name": "Brasil", "code": "BRA"},
                ],
                "J": [
                    {"name": "Argentina", "code": "ARG"},
                ],
                "I": [
                    {"name": "França", "code": "FRA"},
                ],
                "H": [
                    {"name": "Espanha", "code": "ESP"},
                ],
                "L": [
                    {"name": "Inglaterra", "code": "ENG"},
                ],
                "E": [
                    {"name": "Alemanha", "code": "GER"},
                ],
                "F": [
                    {"name": "Holanda", "code": "NED"},
                ],
                "K": [
                    {"name": "Portugal", "code": "POR"},
                ],
            }
        },
        "market_outright_odds": [],
    }
    path = tmp_path / "worldcup_brazil.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


def _odds_payload(*, partial: bool = False) -> list[dict]:
    outcomes = [
        {"name": "Brazil", "price": 6.0},
        {"name": "Argentina", "price": 4.0},
        {"name": "France", "price": 5.0},
    ]
    if not partial:
        outcomes.extend(
            [
                {"name": "Spain", "price": 6.0},
                {"name": "England", "price": 7.0},
                {"name": "Germany", "price": 8.0},
                {"name": "Netherlands", "price": 10.0},
                {"name": "Portugal", "price": 12.0},
            ]
        )
    return [
        {
            "bookmakers": [
                {
                    "key": "book_a",
                    "title": "Book A",
                    "markets": [{"key": "outrights", "outcomes": outcomes}],
                }
            ]
        }
    ]


def test_update_market_odds_applies_valid_deviggable_api_payload(tmp_path: Path) -> None:
    config_path = _config_path(tmp_path)
    odds_path = tmp_path / "odds.json"
    odds_path.write_text(json.dumps(_odds_payload()), encoding="utf-8")

    rc = market_odds.main(["--config", str(config_path), "--odds-json", str(odds_path), "--apply"])

    assert rc == 0
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    odds = updated["market_outright_odds"]
    assert len(odds) == 8
    assert {"team": "Brasil", "decimal_odds": 6.0, "bookmaker": "Book A", "source_url": str(odds_path)} in odds
    assert {"team": "Holanda", "decimal_odds": 10.0, "bookmaker": "Book A", "source_url": str(odds_path)} in odds


def test_update_market_odds_partial_book_is_safe_skip_unless_required(tmp_path: Path) -> None:
    # A non-de-vigable field (partial book) is best-effort enrichment: it must NOT abort the
    # pipeline unless --require is set. Regression for the bug where the updater returned 2 even
    # without --require, so `make daily`/`force` aborted whenever no de-vigable odds were found.
    config_path = _config_path(tmp_path)
    odds_path = tmp_path / "partial-odds.json"
    odds_path.write_text(json.dumps(_odds_payload(partial=True)), encoding="utf-8")
    base = ["--config", str(config_path), "--odds-json", str(odds_path), "--apply"]

    assert market_odds.main(base) == 0
    assert market_odds.main(base + ["--require"]) == 2
    assert json.loads(config_path.read_text(encoding="utf-8"))["market_outright_odds"] == []


def test_update_market_odds_api_failure_is_safe_skip_unless_required(tmp_path: Path, monkeypatch) -> None:
    # Key present but the fetch raises (429 quota / 5xx / network): best-effort must skip (rc 0),
    # never abort the daily post pipeline. Only --require (MARKET_ODDS_REQUIRED=1) makes a fetch
    # failure fatal. Without this, `make daily` couples post generation to The Odds API uptime.
    import urllib.error

    config_path = _config_path(tmp_path)
    monkeypatch.setenv("THE_ODDS_API_KEY", "dummy")

    def _boom(url: str):
        raise urllib.error.URLError("simulated odds API outage")

    monkeypatch.setattr(market_odds, "_odds_api_text_from_url", _boom)
    base = ["--config", str(config_path), "--from-the-odds-api", "--apply"]

    assert market_odds.main(base) == 0
    assert market_odds.main(base + ["--require"]) == 2
    assert json.loads(config_path.read_text(encoding="utf-8"))["market_outright_odds"] == []


def test_update_market_odds_programming_errors_are_not_silenced(tmp_path: Path, monkeypatch) -> None:
    config_path = _config_path(tmp_path)
    odds_path = tmp_path / "odds.json"
    odds_path.write_text(json.dumps(_odds_payload()), encoding="utf-8")

    def _bug(entries, config):
        raise RuntimeError("simulated implementation bug")

    monkeypatch.setattr(market_odds, "_filter_valid_devig_entries", _bug)

    with pytest.raises(RuntimeError, match="simulated implementation bug"):
        market_odds.main(["--config", str(config_path), "--odds-json", str(odds_path), "--apply"])


def test_update_market_odds_without_api_key_is_optional_unless_required(tmp_path: Path, monkeypatch) -> None:
    config_path = _config_path(tmp_path)
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)

    optional_rc = market_odds.main(["--config", str(config_path), "--from-the-odds-api"])
    required_rc = market_odds.main(["--config", str(config_path), "--from-the-odds-api", "--require"])

    assert optional_rc == 0
    assert required_rc == 2
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["market_outright_odds"] == []


def test_update_market_odds_redacts_generic_credentials_from_persisted_api_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _config_path(tmp_path)
    source_url = (
        "https://user:pass@odds.example.test/v4/odds?API_KEY=case-secret&"
        "provider_token=alias-secret#/route=compact?access_token=fragment-secret&view=summary"
    )

    monkeypatch.setattr(
        market_odds,
        "_odds_api_text_from_url",
        lambda _url: (source_url, json.dumps(_odds_payload())),
    )

    assert market_odds.main(["--config", str(config_path), "--from-the-odds-api", "--apply"]) == 0

    persisted = config_path.read_text(encoding="utf-8")
    summary = capsys.readouterr().out
    for secret in ("user", "pass", "case-secret", "alias-secret", "fragment-secret"):
        assert secret not in persisted
        assert secret not in summary
    assert "REDACTED" in persisted
    assert "REDACTED" in summary


@pytest.mark.parametrize(
    ("option", "content"),
    [
        ("--env-file", "THE_ODDS_API_KEY=explicit-file-key\n"),
        ("--shell-env-file", "export THE_ODDS_API_KEY=explicit-file-key\n"),
    ],
)
def test_update_market_odds_loads_only_explicit_environment_files_before_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    option: str,
    content: str,
) -> None:
    config_path = _config_path(tmp_path)
    env_file = tmp_path / "operator.env"
    env_file.write_text(content, encoding="utf-8")
    monkeypatch.delenv("THE_ODDS_API_KEY", raising=False)
    observed: dict[str, str | None] = {}

    def _observe_key(_url: str) -> None:
        observed["key"] = os.environ.get("THE_ODDS_API_KEY")
        return None

    monkeypatch.setattr(market_odds, "_odds_api_text_from_url", _observe_key)

    result = market_odds.main(
        [
            "--config",
            str(config_path),
            "--from-the-odds-api",
            option,
            str(env_file),
        ]
    )

    assert result == 0
    assert observed == {"key": "explicit-file-key"}
