from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_forecaster.cli import main
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.providers.odds import preview_odds, redact_url


def _odds_source(tmp_path: Path, **overrides: object) -> Path:
    document: dict[str, object] = {
        "schema_version": 1,
        "provider": "offline-odds-fixture",
        "retrieved_at": "2026-07-11T12:30:00Z",
        "odds": [
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "bookmaker": "Fixture Book",
                "source_url": "https://user:pass@odds.example.test/v1?api_key=abc%20123&API_KEY=second&region=br",
            }
        ],
    }
    document.update(overrides)
    source = tmp_path / "odds.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    return source


def test_preview_odds_validates_and_preserves_only_diagnostic_provenance(
    tmp_path: Path,
) -> None:
    preview = preview_odds(_odds_source(tmp_path))

    assert preview.provenance.provider == "offline-odds-fixture"
    assert preview.provenance.retrieved_at == "2026-07-11T12:30:00+00:00"
    assert preview.records[0].decimal_odds == 4.25
    assert preview.records[0].source_url == (
        "https://odds.example.test/v1?api_key=REDACTED&API_KEY=REDACTED&region=br"
    )
    serialized = preview.to_dict()
    forbidden = {"ratings", "stage_probabilities", "championship_probability", "probability"}
    assert forbidden.isdisjoint(serialized)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pass@example.test/path?token=plain&x=1",
        "https://example.test/path?ToKeN=encoded%20secret&x=1",
        "https://example.test/path?signature=one&signature=two&x=1",
        "https://example.test/path?x=1&client_secret=hunter2&apiKey=value",
    ],
)
def test_redact_url_removes_userinfo_and_all_sensitive_duplicate_values(url: str) -> None:
    redacted = redact_url(url)

    assert "user" not in redacted
    assert "pass" not in redacted
    assert "plain" not in redacted
    assert "encoded" not in redacted
    assert "secret" not in redacted.lower().replace("client_secret", "")
    assert "hunter2" not in redacted
    assert "value" not in redacted
    assert redacted.count("REDACTED") >= 1
    assert "x=1" in redacted


@pytest.mark.parametrize(
    "overrides",
    [
        {"retrieved_at": "not-a-timestamp"},
        {"odds": [{"market": "champion", "selection_id": "alpha", "decimal_odds": 1.0}]},
        {
            "odds": [
                {
                    "market": "champion",
                    "selection_id": "alpha",
                    "decimal_odds": 2.0,
                    "source_url": "file:///tmp/private-odds.json",
                }
            ]
        },
        {"championship_probability": 0.5},
    ],
)
def test_preview_odds_rejects_invalid_or_probability_mutating_documents(
    tmp_path: Path,
    overrides: dict[str, object],
) -> None:
    with pytest.raises(TournamentValidationError):
        preview_odds(_odds_source(tmp_path, **overrides))


def test_cli_odds_surface_is_preview_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _odds_source(tmp_path)

    assert main(["update-odds", "--source", str(source)]) == 0
    output = capsys.readouterr().out
    assert "Odds preview" in output
    assert "records: 1" in output
    assert "provenance only" in output


def test_cli_odds_surface_rejects_apply_because_odds_never_mutate_core_state(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as error:
        main(["update-odds", "--source", str(_odds_source(tmp_path)), "--apply"])

    assert error.value.code == 2
