from __future__ import annotations

import fcntl
import json
import os
import stat
from pathlib import Path

import pytest

from tournament_forecaster.cli import main
from tournament_forecaster.config import load_tournament
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.group_fixtures import list_group_fixtures
from tournament_forecaster.providers.results import apply_results, preview_results
from tournament_forecaster.resources import resource_path


def _config(tmp_path: Path) -> Path:
    return _template_config(tmp_path, "group-knockout")


def _template_config(tmp_path: Path, template: str) -> Path:
    destination = tmp_path / "tournament.json"
    with resource_path("data", "templates", template, "tournament.json") as source:
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


def _complete_group_stage(config: Path) -> None:
    document = json.loads(config.read_text(encoding="utf-8"))
    tournament = load_tournament(config)
    fixtures = list_group_fixtures(tournament, "group-stage")
    document["completed_matches"] = [
        {
            "match_id": fixture.match_id,
            "stage_id": "group-stage",
            "home_team_id": fixture.home_team_id,
            "away_team_id": fixture.away_team_id,
            "score": {"home": 0, "away": 0},
            "leg": fixture.leg,
        }
        for fixture in fixtures
    ]
    config.write_text(json.dumps(document), encoding="utf-8")


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
    assert [
        (match.match_id, match.score.home, match.score.away)
        for match in tournament.completed_matches
    ] == [(expected_match_id, 2, 1)]

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


@pytest.mark.parametrize("path_kind", ["config", "source"])
def test_preview_rejects_symlinked_leaf_files(
    tmp_path: Path,
    path_kind: str,
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])
    original = config if path_kind == "config" else source
    link = tmp_path / f"linked-{original.name}"
    link.symlink_to(original)
    if path_kind == "config":
        config = link
    else:
        source = link

    with pytest.raises(TournamentValidationError, match="symlink"):
        preview_results(config, source, format="json")


def test_preview_accepts_stable_ancestor_alias_and_binds_resolved_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    config = _config(real_parent)
    source = _json_source(real_parent, [_result()])
    alias = tmp_path / "alias"
    alias.symlink_to(real_parent, target_is_directory=True)

    preview = preview_results(
        alias / config.name,
        alias / source.name,
        format="json",
    )

    assert preview.config_path == config.resolve(strict=True)
    assert preview.source_path == source.resolve(strict=True)
    assert preview.config_parent_identity.path == real_parent.resolve(strict=True)
    assert preview.config_parent_identity.device == real_parent.stat().st_dev
    assert preview.config_parent_identity.inode == real_parent.stat().st_ino


def test_preview_records_canonical_regular_file_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])
    monkeypatch.chdir(tmp_path)

    preview = preview_results(
        config.relative_to(tmp_path),
        source.relative_to(tmp_path),
        format="json",
    )

    assert preview.config_path == config.resolve(strict=True)
    assert preview.source_path == source.resolve(strict=True)
    assert preview.config_identity.device == config.stat().st_dev
    assert preview.config_identity.inode == config.stat().st_ino
    assert preview.config_identity.size == config.stat().st_size
    assert preview.source_identity.inode == source.stat().st_ino
    assert preview.config_parent_identity.path == config.parent.resolve(strict=True)


def test_apply_rejects_same_bytes_at_a_different_inode_before_mutation(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])
    preview = preview_results(config, source, format="json")
    original_bytes = config.read_bytes()
    old_path = tmp_path / "old-tournament.json"
    config.replace(old_path)
    config.write_bytes(original_bytes)

    with pytest.raises(TournamentValidationError, match="identity|changed"):
        apply_results(config, preview)

    assert config.read_bytes() == original_bytes


def test_apply_rejects_symlink_swap_before_mutation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original = tmp_path / "original-tournament.json"
    config.replace(original)
    before = original.read_bytes()
    config.symlink_to(original)

    with pytest.raises(TournamentValidationError, match="symlink"):
        apply_results(config, preview)

    assert config.is_symlink()
    assert original.read_bytes() == before


def test_apply_parent_swap_cannot_redirect_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "active"
    parent.mkdir()
    config = _config(parent)
    preview = preview_results(config, _json_source(parent, [_result()]), format="json")
    original_bytes = config.read_bytes()
    detached_parent = tmp_path / "detached"
    decoy_bytes = b'{"decoy": true}\n'
    original_fsync = os.fsync
    swapped = False

    def swap_parent_at_temp_fsync(descriptor: int) -> None:
        nonlocal swapped
        original_fsync(descriptor)
        if not swapped and stat.S_ISREG(os.fstat(descriptor).st_mode):
            swapped = True
            parent.rename(detached_parent)
            parent.mkdir()
            (parent / config.name).write_bytes(decoy_bytes)

    monkeypatch.setattr(os, "fsync", swap_parent_at_temp_fsync)

    with pytest.raises(TournamentValidationError, match="parent.*changed|changed.*parent"):
        apply_results(config, preview)

    assert swapped is True
    assert (parent / config.name).read_bytes() == decoy_bytes
    assert (detached_parent / config.name).read_bytes() == original_bytes
    assert not list(parent.glob(".tournament.json.*.tmp"))
    assert not list(detached_parent.glob(".tournament.json.*.tmp"))


def test_apply_revalidates_digest_after_temp_fsync_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    concurrent_bytes = config.read_bytes() + b"\n"
    original_fsync = os.fsync
    injected = False

    def inject_concurrent_edit(descriptor: int) -> None:
        nonlocal injected
        original_fsync(descriptor)
        if not injected and stat.S_ISREG(os.fstat(descriptor).st_mode):
            injected = True
            config.write_bytes(concurrent_bytes)

    monkeypatch.setattr(os, "fsync", inject_concurrent_edit)

    with pytest.raises(TournamentValidationError, match="changed.*commit|content.*changed"):
        apply_results(config, preview)

    assert injected is True
    assert config.read_bytes() == concurrent_bytes
    assert not list(tmp_path.glob(".tournament.json.*.tmp"))


def test_apply_preserves_destination_edit_injected_inside_final_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    concurrent_bytes = config.read_bytes() + b"\n"
    original_replace = os.replace
    injected = False

    def replace_with_destination_edit(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal injected
        if not injected and dst_dir_fd is not None:
            injected = True
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                dir_fd=dst_dir_fd,
            )
            try:
                os.write(descriptor, concurrent_bytes)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", replace_with_destination_edit)

    with pytest.raises(TournamentValidationError, match="commit boundary|concurrent"):
        apply_results(config, preview)

    assert injected is True
    assert config.read_bytes() == concurrent_bytes


def test_apply_parent_swap_inside_final_replace_cannot_return_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "active"
    parent.mkdir()
    config = _config(parent)
    preview = preview_results(config, _json_source(parent, [_result()]), format="json")
    original_bytes = config.read_bytes()
    detached_parent = tmp_path / "detached"
    decoy_bytes = b'{"decoy": true}\n'
    original_replace = os.replace
    swapped = False

    def replace_with_parent_swap(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if not swapped and dst_dir_fd is not None:
            swapped = True
            parent.rename(detached_parent)
            parent.mkdir()
            (parent / config.name).write_bytes(decoy_bytes)
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", replace_with_parent_swap)

    with pytest.raises(TournamentValidationError, match="parent.*changed|detached"):
        apply_results(config, preview)

    assert swapped is True
    assert (parent / config.name).read_bytes() == decoy_bytes
    assert (detached_parent / config.name).read_bytes() == original_bytes


def test_apply_honors_existing_project_writer_lock(tmp_path: Path) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    before = config.read_bytes()
    lock_path = config.parent / f".{config.name}.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(TournamentValidationError, match="locked.*writer|writer.*lock"):
            apply_results(config, preview)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

    assert config.read_bytes() == before


def test_apply_fails_closed_without_directory_descriptor_primitives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    before = config.read_bytes()
    monkeypatch.setattr(os, "supports_dir_fd", set())

    with pytest.raises(TournamentValidationError, match="race-resistant.*unavailable"):
        apply_results(config, preview)

    assert config.read_bytes() == before


def test_relative_apply_rejects_changed_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    source = _json_source(tmp_path, [_result()])
    monkeypatch.chdir(tmp_path)
    relative_config = Path(config.name)
    preview = preview_results(relative_config, Path(source.name), format="json")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    with pytest.raises(TournamentValidationError, match="different|path|file"):
        apply_results(relative_config, preview)

    assert json.loads(config.read_text(encoding="utf-8"))["completed_matches"] == []


def test_preview_rejects_non_regular_input_as_validation_error(tmp_path: Path) -> None:
    config = _config(tmp_path)
    source_directory = tmp_path / "results-directory"
    source_directory.mkdir()

    with pytest.raises(TournamentValidationError, match="regular file"):
        preview_results(config, source_directory, format="json")


@pytest.mark.parametrize(
    "csv_text",
    [
        (
            "status,status,stage_id,home_team,away_team,home_score,away_score,"
            "provider,retrieved_at\n"
            "final,final,group-stage,Alpha Club,Bravo Town,1,0,offline,"
            "2026-07-11T12:00:00Z\n"
        ),
        (
            "status,stage_id,home_team,away_team,home_score,provider,retrieved_at\n"
            "final,group-stage,Alpha Club,Bravo Town,1,offline,"
            "2026-07-11T12:00:00Z\n"
        ),
        (
            "status,stage_id,home_team,away_team,home_score,away_score,"
            "provider,retrieved_at,unexpected\n"
            "final,group-stage,Alpha Club,Bravo Town,1,0,offline,"
            "2026-07-11T12:00:00Z,\n"
        ),
        (
            "status,stage_id,home_team,away_team,home_score,away_score,"
            "provider,retrieved_at\n"
            "final,group-stage,Alpha Club,Bravo Town,1,0,offline,"
            "2026-07-11T12:00:00Z,surplus\n"
        ),
        (
            "status,stage_id,home_team,away_team,home_score,away_score,"
            "provider,retrieved_at\n"
            '"final,group-stage,Alpha Club,Bravo Town,1,0,offline,'
            "2026-07-11T12:00:00Z\n"
        ),
    ],
    ids=("duplicate-header", "missing-header", "unknown-header", "surplus", "quoting"),
)
def test_csv_rejects_malformed_headers_and_rows(
    tmp_path: Path,
    csv_text: str,
) -> None:
    config = _config(tmp_path)
    source = tmp_path / "malformed.csv"
    source.write_text(csv_text, encoding="utf-8")

    with pytest.raises(TournamentValidationError, match="CSV|header|column"):
        preview_results(config, source, format="csv")


def test_cli_reports_invalid_input_as_exit_2_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    source = tmp_path / "invalid.csv"
    source.write_bytes(b"status,provider,retrieved_at\n\xff,offline,now\n")

    assert main(
        [
            "update-results",
            "--config",
            os.fspath(config),
            "--source",
            os.fspath(source),
            "--format",
            "csv",
        ]
    ) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("error: ")
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("explicit_match_id", [False, True])
def test_group_result_rejects_reversed_configured_orientation(
    tmp_path: Path,
    explicit_match_id: bool,
) -> None:
    config = _config(tmp_path)
    row = _result(home_team="Bravo Town", away_team="Alpha Club")
    if explicit_match_id:
        row["match_id"] = _configured_match_id(config)

    with pytest.raises(TournamentValidationError, match="home-away order"):
        preview_results(config, _json_source(tmp_path, [row]), format="json")


@pytest.mark.parametrize("explicit_match_id", [False, True])
def test_league_result_rejects_reversed_configured_orientation(
    tmp_path: Path,
    explicit_match_id: bool,
) -> None:
    config = _template_config(tmp_path, "league-knockout")
    row = {
        "status": "final",
        "stage_id": "league-stage",
        "home_team": "Beacon Town",
        "away_team": "Alpha FC",
        "home_score": 1,
        "away_score": 0,
        "leg": 1,
    }
    if explicit_match_id:
        row["match_id"] = "league-1"

    with pytest.raises(TournamentValidationError, match="home-away order"):
        preview_results(config, _json_source(tmp_path, [row]), format="json")


def test_knockout_result_rejects_reversed_configured_orientation(tmp_path: Path) -> None:
    config = _config(tmp_path)
    _complete_group_stage(config)
    row = {
        "status": "final",
        "match_id": "semi-final-1",
        "stage_id": "semi-finals",
        "home_team": "Foxtrot Rovers",
        "away_team": "Alpha Club",
        "home_score": 0,
        "away_score": 1,
        "leg": 1,
    }

    with pytest.raises(TournamentValidationError, match="pairing|home-away order"):
        preview_results(config, _json_source(tmp_path, [row]), format="json")


@pytest.mark.parametrize("template", ["group-knockout", "league-knockout"])
def test_table_result_rejects_declared_winner_on_draw(
    tmp_path: Path,
    template: str,
) -> None:
    config = _template_config(tmp_path, template)
    if template == "group-knockout":
        row = _result(home_score=1, away_score=1, winner_team="Alpha Club")
    else:
        row = {
            "status": "final",
            "match_id": "league-1",
            "stage_id": "league-stage",
            "home_team": "Alpha FC",
            "away_team": "Beacon Town",
            "home_score": 1,
            "away_score": 1,
            "winner_team": "Alpha FC",
        }

    with pytest.raises(TournamentValidationError, match="draw.*winner|winner.*draw"):
        preview_results(config, _json_source(tmp_path, [row]), format="json")


def test_two_leg_knockout_winner_uses_aggregate_not_final_leg_score(
    tmp_path: Path,
) -> None:
    config = _template_config(tmp_path, "group-two-leg-knockout")
    _complete_group_stage(config)
    rows = [
        {
            "status": "final",
            "match_id": "semi-final-1",
            "stage_id": "semi-finals",
            "home_team": "Alpha Club",
            "away_team": "Foxtrot Rovers",
            "home_score": 3,
            "away_score": 0,
            "leg": 1,
        },
        {
            "status": "final",
            "match_id": "semi-final-1",
            "stage_id": "semi-finals",
            "home_team": "Foxtrot Rovers",
            "away_team": "Alpha Club",
            "home_score": 1,
            "away_score": 0,
            "leg": 2,
            "winner_team": "Alpha Club",
        },
    ]

    preview = preview_results(config, _json_source(tmp_path, rows), format="json")

    assert [(fact.leg, fact.winner_team_id) for fact in preview.additions] == [
        (1, None),
        (2, "alpha-club"),
    ]


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


def test_result_metadata_is_recursively_sanitized_before_preview_and_apply(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    source = _json_source(
        tmp_path,
        [
            _result(
                metadata={
                    "credential": "result-credential",
                    "nested": [
                        {
                            "access_key_id": "AKIARESULTSECRET",
                            "url": (
                                "https://user:pass@example.test/result?"
                                "X-Amz-Signature=signed-secret&region=br"
                            ),
                        }
                    ],
                }
            )
        ],
    )

    preview = preview_results(config, source, format="json")
    serialized_preview = json.dumps(preview.to_dict(), sort_keys=True)

    for secret in (
        "result-credential",
        "AKIARESULTSECRET",
        "signed-secret",
        "user",
        "pass@",
    ):
        assert secret not in serialized_preview
    assert serialized_preview.count("[REDACTED]") == 2
    apply_results(config, preview)
    serialized_config = config.read_text(encoding="utf-8")
    assert "result-credential" not in serialized_config
    assert "AKIARESULTSECRET" not in serialized_config
    assert "signed-secret" not in serialized_config
    assert "X-Amz-Signature=REDACTED" in serialized_config


def test_unmatched_and_existing_conflict_metadata_cannot_leak_in_preview(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    initial = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    apply_results(config, initial)
    document = json.loads(config.read_text(encoding="utf-8"))
    document["completed_matches"][0]["metadata"] = {
        "password": "existing-password"
    }
    config.write_text(json.dumps(document), encoding="utf-8")
    source = _json_source(
        tmp_path,
        [
            _result(home_score=3, metadata={"auth_token": "incoming-token"}),
            _result(
                home_team="Unknown FC",
                metadata={"client_secret": "unmatched-secret"},
            ),
        ],
    )

    preview = preview_results(config, source, format="json")
    serialized = json.dumps(preview.to_dict(), sort_keys=True)

    assert len(preview.conflicts) == 1
    assert len(preview.unmatched) == 1
    for secret in ("existing-password", "incoming-token", "unmatched-secret"):
        assert secret not in serialized
    assert serialized.count("[REDACTED]") == 3


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


def test_cli_prints_exact_conflict_diff_before_explicit_replacement(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = _config(tmp_path)
    initial = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    apply_results(config, initial)
    source = _json_source(
        tmp_path,
        [_result(home_score=1, away_score=2, winner_team="Bravo Town")],
    )
    match_id = _configured_match_id(config)

    assert main(
        [
            "update-results",
            "--config",
            str(config),
            "--source",
            str(source),
            "--apply",
            "--replace-conflicts",
        ]
    ) == 0
    output = capsys.readouterr().out
    expected_diff = (
        f"  conflict {match_id} leg 1:\n"
        "    existing: alpha-club 2-1 bravo-town; winner: none\n"
        "    incoming: alpha-club 1-2 bravo-town; winner: bravo-town\n"
        "    reason: incoming result differs from immutable completed fact\n"
    )
    assert expected_diff in output
    assert output.index(expected_diff) < output.index("Applied results:")
    assert load_tournament(config).completed_matches[0].score.away == 2
