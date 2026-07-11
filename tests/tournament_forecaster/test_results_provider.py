from __future__ import annotations

import json
from pathlib import Path

import pytest

from tournament_forecaster.cli import main
from tournament_forecaster.config import load_tournament
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.group_fixtures import list_group_fixtures
from tournament_forecaster.providers.results import apply_results, preview_results
from tournament_forecaster.resources import resource_path


def _config(tmp_path: Path) -> Path:
    destination = tmp_path / "tournament.json"
    with resource_path("data", "templates", "group-knockout", "tournament.json") as source:
        destination.write_bytes(Path(source).read_bytes())
    return destination


def _json_source(tmp_path: Path, results: list[dict[str, object]]) -> Path:
    source = tmp_path / "results.json"
    source.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "offline-fixture",
                "retrieved_at": "2026-07-11T12:00:00Z",
                "results": results,
            }
        ),
        encoding="utf-8",
    )
    return source


def _result(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "status": "final",
        "stage_id": "group-stage",
        "home_team": "Alpha Club",
        "away_team": "Bravo Town",
        "home_score": 2,
        "away_score": 1,
        "leg": 1,
        "source_id": "fixture-101",
    }
    row.update(overrides)
    return row


def _configured_match_id(config: Path) -> str:
    fixtures = list_group_fixtures(load_tournament(config), "group-stage")
    return next(
        fixture.match_id
        for fixture in fixtures
        if {fixture.home_team_id, fixture.away_team_id} == {"alpha-club", "bravo-town"}
    )


def test_preview_apply_and_repreview_classify_addition_then_idempotent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])

    preview = preview_results(config, source, format="json")

    expected_match_id = _configured_match_id(config)
    assert [fact.match_id for fact in preview.additions] == [expected_match_id]
    assert preview.idempotent == ()
    assert preview.conflicts == ()
    assert preview.unmatched == ()
    assert preview.source_provenance.provider == "offline-fixture"
    assert preview.source_provenance.retrieved_at == "2026-07-11T12:00:00+00:00"

    apply_results(config, preview)
    tournament = load_tournament(config)
    assert [(match.match_id, match.score.home, match.score.away) for match in tournament.completed_matches] == [
        (expected_match_id, 2, 1)
    ]

    repeated = preview_results(config, source, format="json")
    assert repeated.additions == ()
    assert [fact.match_id for fact in repeated.idempotent] == [expected_match_id]
    before = config.read_bytes()
    apply_results(config, repeated)
    assert config.read_bytes() == before


def test_csv_preview_resolves_unique_aliases_and_infers_configured_fixture_id(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    document = json.loads(config.read_text(encoding="utf-8"))
    document["teams"][0]["aliases"] = ["Alpha"]
    document["teams"][1]["aliases"] = ["Bravo"]
    config.write_text(json.dumps(document), encoding="utf-8")
    source = tmp_path / "results.csv"
    source.write_text(
        "status,stage_id,home_team,away_team,home_score,away_score,leg,provider,retrieved_at,source_id\n"
        "final,group-stage,Alpha,Bravo,0,0,1,offline-csv,2026-07-11T12:00:00Z,csv-1\n",
        encoding="utf-8",
    )

    preview = preview_results(config, source, format="csv")

    assert len(preview.additions) == 1
    assert preview.additions[0].home_team_id == "alpha-club"
    assert preview.additions[0].away_team_id == "bravo-town"
    assert preview.additions[0].match_id == _configured_match_id(config)


def test_preview_separates_conflicts_and_unmatched_rows_and_apply_refuses_them(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initial = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    apply_results(config, initial)
    source = _json_source(
        tmp_path,
        [
            _result(home_score=1, away_score=2),
            _result(home_team="Unknown FC"),
        ],
    )

    preview = preview_results(config, source, format="json")

    assert preview.additions == ()
    assert len(preview.conflicts) == 1
    assert preview.conflicts[0].existing.score.home == 2
    assert preview.conflicts[0].incoming.score.home == 1
    assert len(preview.unmatched) == 1
    assert "Unknown FC" in preview.unmatched[0].reason
    with pytest.raises(TournamentValidationError, match="conflict.*unmatched"):
        apply_results(config, preview)


def test_apply_detects_stale_preview_and_explicit_replacement_remains_atomic(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    original = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    config.write_text(config.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(TournamentValidationError, match="changed since preview"):
        apply_results(config, original)

    fresh = preview_results(config, original.source_path, format="json")
    apply_results(config, fresh)
    replacement_source = _json_source(tmp_path, [_result(home_score=3, away_score=1)])
    replacement = preview_results(config, replacement_source, format="json")
    with pytest.raises(TournamentValidationError, match="conflict"):
        apply_results(config, replacement)

    apply_results(config, replacement, replace_conflicts=True)
    assert load_tournament(config).completed_matches[0].score.home == 3
    assert not list(tmp_path.glob(".tournament.json.*.tmp"))


@pytest.mark.parametrize(
    "row",
    [
        _result(status="scheduled"),
        _result(stage_id="not-a-stage"),
        _result(home_score=1, away_score=0, winner_team="Bravo Town"),
        _result(match_id="semi-final-1", stage_id="semi-finals"),
    ],
)
def test_preview_rejects_non_final_impossible_and_contradictory_rows(
    tmp_path: Path,
    row: dict[str, object],
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [row])

    with pytest.raises(TournamentValidationError):
        preview_results(config, source, format="json")


def test_cli_results_import_previews_by_default_and_requires_apply_for_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])
    before = config.read_bytes()

    assert main(["update-results", "--config", str(config), "--source", str(source)]) == 0
    preview_output = capsys.readouterr().out
    assert "additions: 1" in preview_output
    assert "Preview only" in preview_output
    assert config.read_bytes() == before

    assert main(
        ["update-results", "--config", str(config), "--source", str(source), "--apply"]
    ) == 0
    applied_output = capsys.readouterr().out
    assert "Applied results: 1 addition" in applied_output
    assert len(load_tournament(config).completed_matches) == 1
