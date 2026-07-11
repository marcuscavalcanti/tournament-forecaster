from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tournament_forecaster.cli import main
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.providers.odds import preview_odds, redact_url
from tournament_forecaster.resources import resource_path


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
        (
            "https://encoded%40user:p%40ss@example.test/path?"
            "X-Amz-Credential=AKIAIOSFODNN7EXAMPLE%2Fscope&"
            "AWSAccessKeyId=aws-key&X-Goog-Credential=goog%2Fscope&x=1"
        ),
        (
            "https://example.test/path?X%2DAmz%2DCredential=encoded-credential&"
            "PASSWORD=opensesame&Authorization=Bearer%20private&AUTH=basic&x=1"
        ),
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
    assert "AKIAIOSFODNN7EXAMPLE" not in redacted
    assert "aws-key" not in redacted
    assert "goog" not in redacted
    assert "encoded-credential" not in redacted
    assert "opensesame" not in redacted
    assert "private" not in redacted
    assert "basic" not in redacted
    assert redacted.count("REDACTED") >= 1
    assert "x=1" in redacted


def test_redact_url_preserves_benign_names_that_merely_end_in_key() -> None:
    redacted = redact_url(
        "https://example.test/path?ranking_key=rank&team_key=team&"
        "public_key=public&monkey=banana&hockey=puck&key=raw-key&"
        "private_key=raw-private"
    )

    assert "ranking_key=rank" in redacted
    assert "team_key=team" in redacted
    assert "public_key=public" in redacted
    assert "monkey=banana" in redacted
    assert "hockey=puck" in redacted
    assert "raw-key" not in redacted
    assert "raw-private" not in redacted
    assert redacted.count("REDACTED") == 2


@pytest.mark.parametrize(
    "credential_name",
    [
        "x-api-key",
        "XApiKey",
        "provider_api_key",
        "providerApiKey",
        "football-data-token",
        "footballDataToken",
        "access_token",
        "accessToken",
        "client_secret",
        "clientSecret",
    ],
)
def test_redact_url_handles_provider_prefixes_and_name_variants(
    credential_name: str,
) -> None:
    redacted = redact_url(
        f"https://example.test/path?{credential_name}=provider-secret&"
        "public_key=public&ranking_key=rank"
    )

    assert "provider-secret" not in redacted
    assert "REDACTED" in redacted
    assert "public_key=public" in redacted
    assert "ranking_key=rank" in redacted


@pytest.mark.parametrize(
    "credential_name",
    [
        "x-auth-key",
        "xAuthKey",
        "provider_auth_key",
        "providerAuthKey",
        "consumer_key",
        "consumerKey",
        "client-key",
        "clientKey",
    ],
)
def test_redact_url_handles_terminal_key_credential_phrases(
    credential_name: str,
) -> None:
    redacted = redact_url(
        f"https://example.test/path?{credential_name}=credential-secret&"
        "public_key=public&team_key=team"
    )

    assert "credential-secret" not in redacted
    assert "REDACTED" in redacted
    assert "public_key=public" in redacted
    assert "team_key=team" in redacted


def test_metadata_preserves_benign_key_suffixes_but_redacts_exact_conventions(
    tmp_path: Path,
) -> None:
    source = _odds_source(
        tmp_path,
        odds=[
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "metadata": {
                    "ranking_key": "rank",
                    "team_key": "team",
                    "public_key": "public",
                    "monkey": "banana",
                    "hockey": "puck",
                    "access_key": "access-secret",
                    "private_key": "private-secret",
                },
            }
        ],
    )

    metadata = preview_odds(source).to_dict()["records"][0]["metadata"]

    assert metadata == {
        "ranking_key": "rank",
        "team_key": "team",
        "public_key": "public",
        "monkey": "banana",
        "hockey": "puck",
        "access_key": "[REDACTED]",
        "private_key": "[REDACTED]",
    }


def test_metadata_redacts_provider_prefixed_and_camel_case_credentials(
    tmp_path: Path,
) -> None:
    source = _odds_source(
        tmp_path,
        odds=[
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "metadata": {
                    "x-api-key": "x-secret",
                    "provider_api_key": "provider-secret",
                    "footballDataToken": "football-secret",
                    "accessToken": "access-secret",
                    "client_secret": "client-secret",
                    "public_key": "public",
                    "monkey": "banana",
                },
            }
        ],
    )

    metadata = preview_odds(source).to_dict()["records"][0]["metadata"]

    assert metadata == {
        "x-api-key": "[REDACTED]",
        "provider_api_key": "[REDACTED]",
        "footballDataToken": "[REDACTED]",
        "accessToken": "[REDACTED]",
        "client_secret": "[REDACTED]",
        "public_key": "public",
        "monkey": "banana",
    }


def test_nested_metadata_redacts_terminal_key_credential_phrases(
    tmp_path: Path,
) -> None:
    source = _odds_source(
        tmp_path,
        odds=[
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "metadata": {
                    "nested": [
                        {
                            "x-auth-key": "x-auth-secret",
                            "providerAuthKey": "provider-auth-secret",
                            "consumer_key": "consumer-secret",
                            "consumerKey": "consumer-camel-secret",
                            "client-key": "client-secret-value",
                            "clientKey": "client-camel-secret",
                            "public_key": "public",
                            "ranking_key": "rank",
                            "team_key": "team",
                            "monkey": "banana",
                            "hockey": "puck",
                        }
                    ]
                },
            }
        ],
    )

    metadata = preview_odds(source).to_dict()["records"][0]["metadata"]

    assert metadata == {
        "nested": [
            {
                "x-auth-key": "[REDACTED]",
                "providerAuthKey": "[REDACTED]",
                "consumer_key": "[REDACTED]",
                "consumerKey": "[REDACTED]",
                "client-key": "[REDACTED]",
                "clientKey": "[REDACTED]",
                "public_key": "public",
                "ranking_key": "rank",
                "team_key": "team",
                "monkey": "banana",
                "hockey": "puck",
            }
        ]
    }


def test_odds_metadata_is_recursively_sanitized_and_serializable(tmp_path: Path) -> None:
    source = _odds_source(
        tmp_path,
        odds=[
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "metadata": {
                    "safe": "kept",
                    "api_token": "top-secret-token",
                    "nested": [
                        {
                            "Password": "nested-password",
                            "feed": (
                                "https://user:pass@example.test/feed?"
                                "X-Goog-Credential=goog-secret&region=br"
                            ),
                        }
                    ],
                },
            }
        ],
    )

    serialized = json.dumps(preview_odds(source).to_dict(), sort_keys=True)

    for secret in (
        "top-secret-token",
        "nested-password",
        "goog-secret",
        "user",
        "pass@",
    ):
        assert secret not in serialized
    assert serialized.count("[REDACTED]") == 2
    assert "X-Goog-Credential=REDACTED" in serialized
    assert '"safe": "kept"' in serialized


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
        {
            "odds": [
                {
                    "market": "champion",
                    "selection_id": "alpha",
                    "decimal_odds": 2.0,
                    "source_url": "https://[malformed",
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


@pytest.mark.parametrize("input_kind", ["missing", "invalid-utf8"])
def test_odds_input_failures_are_validation_errors(
    tmp_path: Path,
    input_kind: str,
) -> None:
    source = tmp_path / "odds.json"
    if input_kind == "invalid-utf8":
        source.write_bytes(b"\xff")

    with pytest.raises(TournamentValidationError, match="odds.*(read|UTF-8)"):
        preview_odds(source)


def test_cli_odds_surface_is_preview_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
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


def test_odds_schema_and_runtime_accept_case_insensitive_https(tmp_path: Path) -> None:
    source = _odds_source(
        tmp_path,
        odds=[
            {
                "market": "champion",
                "selection_id": "alpha-club",
                "decimal_odds": 4.25,
                "source_url": "HTTPS://odds.example.test/feed",
            }
        ],
    )
    assert preview_odds(source).records[0].source_url == (
        "https://odds.example.test/feed"
    )
    with resource_path("schemas", "odds.import.schema.json") as schema_path:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
    pattern = schema["properties"]["odds"]["items"]["properties"]["source_url"][
        "pattern"
    ]

    assert pattern == "^[Hh][Tt][Tt][Pp][Ss]?://"
    assert re.match(pattern, "HTTPS://odds.example.test/feed")
