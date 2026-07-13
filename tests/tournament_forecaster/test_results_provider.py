from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import re
import stat
from collections.abc import Callable
from pathlib import Path

import pytest

import tournament_forecaster.providers as providers_facade
from tournament_forecaster.cli import main
from tournament_forecaster.config import load_tournament
from tournament_forecaster.errors import TournamentValidationError
from tournament_forecaster.group_fixtures import list_group_fixtures
from tournament_forecaster.providers import results as results_provider
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


def _patch_final_transition(
    monkeypatch: pytest.MonkeyPatch,
    inject: Callable[[str | os.PathLike[str], int], None],
) -> None:
    exchange = getattr(results_provider, "_atomic_exchange_at", None)
    if exchange is not None:

        def exchange_with_injection(
            parent_descriptor: int,
            source_name: str,
            destination_name: str,
        ) -> None:
            inject(destination_name, parent_descriptor)
            exchange(parent_descriptor, source_name, destination_name)

        monkeypatch.setattr(
            results_provider,
            "_atomic_exchange_at",
            exchange_with_injection,
        )
        return

    original_replace = os.replace

    def replace_with_injection(
        source: str | os.PathLike[str],
        destination: str | os.PathLike[str],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if dst_dir_fd is not None:
            inject(destination, dst_dir_fd)
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", replace_with_injection)


def _marked_config_bytes(config: Path, marker: str) -> bytes:
    document = json.loads(config.read_text(encoding="utf-8"))
    document["metadata"]["concurrent_edit"] = marker
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()


def _atomic_install_regular(
    parent_descriptor: int,
    destination_name: str,
    content: bytes,
    label: str,
) -> tuple[int, int]:
    temporary_name = f".{destination_name}.{label}.tmp"
    descriptor = os.open(
        temporary_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent_descriptor,
    )
    try:
        os.write(descriptor, content)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(
        temporary_name,
        destination_name,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
    )
    installed = os.stat(
        destination_name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    return installed.st_dev, installed.st_ino


def _atomic_install_non_regular(
    parent_descriptor: int,
    destination_name: str,
    entry_kind: str,
    symlink_target: Path,
) -> tuple[int, int]:
    temporary_name = f".{destination_name}.injected-{entry_kind}"
    if entry_kind == "symlink":
        os.symlink(str(symlink_target), temporary_name, dir_fd=parent_descriptor)
    else:
        os.mkfifo(temporary_name, 0o600, dir_fd=parent_descriptor)
    os.replace(
        temporary_name,
        destination_name,
        src_dir_fd=parent_descriptor,
        dst_dir_fd=parent_descriptor,
    )
    installed = os.stat(
        destination_name,
        dir_fd=parent_descriptor,
        follow_symlinks=False,
    )
    return installed.st_dev, installed.st_ino


def _paths_with_identity(parent: Path, identity: tuple[int, int]) -> list[Path]:
    matches: list[Path] = []
    for candidate in parent.rglob("*"):
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        if (candidate_stat.st_dev, candidate_stat.st_ino) == identity:
            matches.append(candidate)
    return matches


def _regular_paths_with_bytes(parent: Path, content: bytes) -> list[Path]:
    matches: list[Path] = []
    for candidate in parent.rglob("*"):
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(candidate_stat.st_mode) and candidate.read_bytes() == content:
            matches.append(candidate)
    return matches


def _provider_artifact_paths(parent: Path, target_name: str) -> list[Path]:
    paths: list[Path] = []
    for root in parent.iterdir():
        if not root.name.startswith(f".{target_name}."):
            continue
        paths.append(root)
        if root.is_dir() and not root.is_symlink():
            paths.extend(root.rglob("*"))
    return sorted(paths)


def _reported_paths_under(message: str, parent: Path) -> list[Path]:
    pattern = re.compile(re.escape(str(parent)) + r"/[^,;]+")
    return [Path(match.group(0).rstrip(". )")) for match in pattern.finditer(message)]


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

    before = config.read_bytes()
    receipt = apply_results(config, preview)
    assert receipt.changed is True
    assert receipt.backup_path is not None
    assert receipt.backup_path.read_bytes() == before
    tournament = load_tournament(config)
    assert [
        (match.match_id, match.score.home, match.score.away)
        for match in tournament.completed_matches
    ] == [(expected_match_id, 2, 1)]

    repeated = preview_results(config, source, format="json")
    assert repeated.additions == ()
    assert [fact.match_id for fact in repeated.idempotent] == [expected_match_id]
    before = config.read_bytes()
    repeated_receipt = apply_results(config, repeated)
    assert repeated_receipt.changed is False
    assert repeated_receipt.backup_path is None
    assert config.read_bytes() == before


def test_provider_facade_exports_apply_result() -> None:
    assert providers_facade.ApplyResult is results_provider.ApplyResult
    assert "ApplyResult" in providers_facade.__all__


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

    with pytest.raises(
        TournamentValidationError,
        match="changed.*commit|content.*changed",
    ) as captured:
        apply_results(config, preview)

    assert injected is True
    assert config.read_bytes() == concurrent_bytes
    assert not list(tmp_path.glob(".tournament.json.*.tmp"))
    assert captured.value.__cause__ is not None
    assert "tournament config content changed before commit" in str(
        captured.value.__cause__
    )
    artifacts = _provider_artifact_paths(tmp_path, config.name)
    assert artifacts
    message = str(captured.value)
    for artifact in artifacts:
        assert str(artifact) in message


def test_apply_preserves_destination_edit_injected_inside_final_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    concurrent_bytes = config.read_bytes() + b"\n"
    injected = False

    def inject_destination_edit(
        destination: str | os.PathLike[str],
        parent_descriptor: int,
    ) -> None:
        nonlocal injected
        if not injected:
            injected = True
            descriptor = os.open(
                destination,
                os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW,
                dir_fd=parent_descriptor,
            )
            try:
                os.write(descriptor, concurrent_bytes)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    _patch_final_transition(monkeypatch, inject_destination_edit)

    with pytest.raises(TournamentValidationError, match="commit boundary|concurrent"):
        apply_results(config, preview)

    assert injected is True
    assert config.read_bytes() == concurrent_bytes


def test_apply_atomic_destination_replacement_inside_final_transition_survives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    concurrent_document = json.loads(config.read_text(encoding="utf-8"))
    concurrent_document["metadata"]["concurrent_atomic_edit"] = "preserved"
    concurrent_bytes = (
        json.dumps(concurrent_document, indent=2, sort_keys=True) + "\n"
    ).encode()
    original_replace = os.replace
    injected = False
    concurrent_identity: tuple[int, int] | None = None

    def inject_atomic_destination_replacement(
        destination: str | os.PathLike[str],
        parent_descriptor: int,
    ) -> None:
        nonlocal concurrent_identity, injected
        if injected:
            return
        injected = True
        temporary_name = f".{config.name}.concurrent-atomic.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_descriptor,
        )
        try:
            os.write(descriptor, concurrent_bytes)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        original_replace(
            temporary_name,
            destination,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        installed_stat = os.stat(
            destination,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        concurrent_identity = (installed_stat.st_dev, installed_stat.st_ino)

    _patch_final_transition(monkeypatch, inject_atomic_destination_replacement)

    with pytest.raises(TournamentValidationError, match="commit boundary|concurrent"):
        apply_results(config, preview)

    assert injected is True
    assert concurrent_identity is not None
    assert config.read_bytes() == concurrent_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == concurrent_identity
    assert json.loads(config.read_bytes())["metadata"]["concurrent_atomic_edit"] == (
        "preserved"
    )
    assert not list(tmp_path.glob(".tournament.json.*.tmp"))


@pytest.mark.parametrize("entry_role", ["destination", "prepared"])
@pytest.mark.parametrize("entry_kind", ["symlink", "fifo"])
def test_apply_recovers_non_regular_entry_installed_inside_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_role: str,
    entry_kind: str,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    sentinel = tmp_path / "symlink-target.txt"
    sentinel.write_text("sentinel stays untouched\n", encoding="utf-8")
    original_exchange = results_provider._atomic_exchange_at
    original_entry_identity = results_provider._entry_identity_at
    injected_identity: tuple[int, int] | None = None

    def exchange_with_non_regular_entry(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal injected_identity
        destination_identity = original_entry_identity(
            parent_descriptor,
            destination_name,
            "test commit destination",
        )
        if (
            injected_identity is None
            and (destination_identity.device, destination_identity.inode)
            == (preview.config_identity.device, preview.config_identity.inode)
        ):
            victim_name = (
                destination_name if entry_role == "destination" else source_name
            )
            injected_identity = _atomic_install_non_regular(
                parent_descriptor,
                victim_name,
                entry_kind,
                sentinel,
            )
        original_exchange(parent_descriptor, source_name, destination_name)

    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_at",
        exchange_with_non_regular_entry,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert injected_identity is not None
    assert sentinel.read_text(encoding="utf-8") == "sentinel stays untouched\n"
    injected_paths = _paths_with_identity(tmp_path, injected_identity)
    assert injected_paths
    original_paths = _regular_paths_with_bytes(tmp_path, original_bytes)
    assert original_paths
    if entry_role == "destination":
        assert (config.lstat().st_dev, config.lstat().st_ino) == injected_identity
    else:
        assert config.read_bytes() == original_bytes
    recovery_paths = {
        path
        for path in (*injected_paths, *original_paths)
        if path != config
    }
    assert recovery_paths
    message = str(captured.value)
    for recovery_path in recovery_paths:
        assert str(recovery_path) in message


@pytest.mark.parametrize("entry_kind", ["regular", "symlink", "fifo"])
def test_cleanup_quarantines_entry_substituted_after_exact_role_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_kind: str,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    concurrent_bytes = _marked_config_bytes(config, f"cleanup-{entry_kind}")
    sentinel = tmp_path / "cleanup-symlink-target.txt"
    sentinel.write_text("sentinel stays untouched\n", encoding="utf-8")
    original_exact_check = results_provider._entry_is_exact_regular_file_at
    original_move = results_provider._atomic_move_no_replace_at
    cleanup_armed = False
    exchange_name: str | None = None
    injected_identity: tuple[int, int] | None = None

    def exact_check_then_arm_cleanup(
        parent_descriptor: int,
        filename: str,
        canonical_path: Path,
        expected_identity: object,
        expected_bytes: bytes,
    ) -> bool:
        nonlocal cleanup_armed, exchange_name
        exact = original_exact_check(
            parent_descriptor,
            filename,
            canonical_path,
            expected_identity,
            expected_bytes,
        )
        if (
            exact
            and expected_identity == preview.config_identity
            and filename != config.name
        ):
            cleanup_armed = True
            exchange_name = filename
        return exact

    def inject_substitute(parent_descriptor: int, filename: str) -> None:
        nonlocal injected_identity
        if injected_identity is not None:
            return
        if entry_kind == "regular":
            injected_identity = _atomic_install_regular(
                parent_descriptor,
                filename,
                concurrent_bytes,
                "cleanup-substitute",
            )
        else:
            injected_identity = _atomic_install_non_regular(
                parent_descriptor,
                filename,
                entry_kind,
                sentinel,
            )

    def move_after_substitution(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        if (
            cleanup_armed
            and exchange_name is not None
            and source_name == exchange_name
        ):
            inject_substitute(source_descriptor, exchange_name)
        original_move(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    monkeypatch.setattr(
        results_provider,
        "_entry_is_exact_regular_file_at",
        exact_check_then_arm_cleanup,
    )
    monkeypatch.setattr(
        results_provider,
        "_atomic_move_no_replace_at",
        move_after_substitution,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert injected_identity is not None
    assert config.read_bytes() == original_bytes
    assert json.loads(config.read_bytes())["completed_matches"] == []
    assert sentinel.read_text(encoding="utf-8") == "sentinel stays untouched\n"
    injected_paths = _paths_with_identity(tmp_path, injected_identity)
    assert injected_paths
    message = str(captured.value)
    for recovery_path in injected_paths:
        assert recovery_path != config
        assert str(recovery_path) in message
    hidden_paths = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(f".{config.name}.")
        and path.name != f".{config.name}.lock"
    ]
    for recovery_path in hidden_paths:
        assert str(recovery_path) in message


def test_quarantine_no_replace_preserves_injected_destination_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    injected_bytes = b"same-user quarantine destination\n"
    injected_identity: tuple[int, int] | None = None

    def inject_destination(
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal injected_identity
        if injected_identity is None:
            injected_identity = _atomic_install_regular(
                destination_descriptor,
                destination_name,
                injected_bytes,
                "quarantine-destination",
            )

    native_move = getattr(results_provider, "_atomic_move_no_replace_at", None)
    if native_move is not None:

        def move_with_injected_destination(
            source_descriptor: int,
            source_name: str,
            destination_descriptor: int,
            destination_name: str,
        ) -> None:
            if destination_name == "entry":
                inject_destination(destination_descriptor, destination_name)
            native_move(
                source_descriptor,
                source_name,
                destination_descriptor,
                destination_name,
            )

        monkeypatch.setattr(
            results_provider,
            "_atomic_move_no_replace_at",
            move_with_injected_destination,
        )
    else:
        original_rename = os.rename

        def rename_with_injected_destination(
            source: str | os.PathLike[str],
            destination: str | os.PathLike[str],
            *,
            src_dir_fd: int | None = None,
            dst_dir_fd: int | None = None,
        ) -> None:
            if os.fspath(destination) == "entry" and dst_dir_fd is not None:
                inject_destination(dst_dir_fd, "entry")
            original_rename(
                source,
                destination,
                src_dir_fd=src_dir_fd,
                dst_dir_fd=dst_dir_fd,
            )

        monkeypatch.setattr(os, "rename", rename_with_injected_destination)
        supported_dir_fd = set(os.supports_dir_fd)
        supported_dir_fd.discard(original_rename)
        supported_dir_fd.add(rename_with_injected_destination)
        monkeypatch.setattr(os, "supports_dir_fd", supported_dir_fd)

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert injected_identity is not None
    assert config.read_bytes() == original_bytes
    assert _paths_with_identity(
        tmp_path,
        (preview.config_identity.device, preview.config_identity.inode),
    )
    injected_paths = _paths_with_identity(tmp_path, injected_identity)
    assert injected_paths
    message = str(captured.value)
    assert "no-replace" in message
    for path in injected_paths:
        assert str(path) in message
    artifacts = _provider_artifact_paths(tmp_path, config.name)
    for artifact in artifacts:
        assert str(artifact) in message


def test_no_replace_preflight_collision_preserves_both_entries_before_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    original_identity = (config.stat().st_dev, config.stat().st_ino)
    injected_bytes = b"same-user preflight destination\n"
    injected_identity: tuple[int, int] | None = None
    provider_identity: tuple[int, int] | None = None
    exchange_called = False
    native_move = results_provider._atomic_move_no_replace_at
    native_exchange = results_provider._atomic_exchange_at

    def move_with_collision(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal injected_identity, provider_identity
        if destination_name != "entry" and injected_identity is None:
            source_stat = os.stat(
                source_name,
                dir_fd=source_descriptor,
                follow_symlinks=False,
            )
            provider_identity = (source_stat.st_dev, source_stat.st_ino)
            injected_identity = _atomic_install_regular(
                destination_descriptor,
                destination_name,
                injected_bytes,
                "preflight-destination",
            )
        native_move(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )

    def track_exchange(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal exchange_called
        exchange_called = True
        native_exchange(parent_descriptor, source_name, destination_name)

    monkeypatch.setattr(
        results_provider,
        "_atomic_move_no_replace_at",
        move_with_collision,
    )
    monkeypatch.setattr(results_provider, "_atomic_exchange_at", track_exchange)

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert exchange_called is False
    assert config.read_bytes() == original_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == original_identity
    assert injected_identity is not None
    assert provider_identity is not None
    injected_paths = _paths_with_identity(tmp_path, injected_identity)
    provider_paths = _paths_with_identity(tmp_path, provider_identity)
    assert injected_paths
    assert provider_paths
    message = str(captured.value)
    for path in (*injected_paths, *provider_paths):
        assert str(path) in message
    reported_paths = _reported_paths_under(message, tmp_path)
    assert reported_paths
    assert all(path.exists() for path in reported_paths)


def test_post_move_quarantine_failure_restores_original_from_actual_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    original_identity = (config.stat().st_dev, config.stat().st_ino)
    native_move = results_provider._atomic_move_no_replace_at
    native_fsync = os.fsync
    quarantine_descriptor: int | None = None
    vanished_exchange_name: str | None = None
    injected = False

    def arm_after_quarantine_move(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal quarantine_descriptor, vanished_exchange_name
        native_move(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )
        if destination_name == "entry":
            quarantine_descriptor = destination_descriptor
            vanished_exchange_name = source_name

    def fail_first_quarantine_fsync(descriptor: int) -> None:
        nonlocal injected
        if descriptor == quarantine_descriptor and not injected:
            injected = True
            raise OSError(errno.EIO, "injected post-move quarantine failure")
        native_fsync(descriptor)

    monkeypatch.setattr(
        results_provider,
        "_atomic_move_no_replace_at",
        arm_after_quarantine_move,
    )
    monkeypatch.setattr(os, "fsync", fail_first_quarantine_fsync)

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert injected is True
    assert config.read_bytes() == original_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == original_identity
    assert json.loads(config.read_bytes())["completed_matches"] == []
    assert vanished_exchange_name is not None
    vanished_path = tmp_path / vanished_exchange_name
    assert not vanished_path.exists()
    message = str(captured.value)
    assert str(vanished_path) not in message
    reported_paths = _reported_paths_under(message, tmp_path)
    assert reported_paths
    assert all(path.exists() for path in reported_paths)

    structured_type = getattr(results_provider, "_QuarantineOperationError", None)
    assert structured_type is not None
    current: BaseException | None = captured.value
    structured_error: BaseException | None = None
    while current is not None:
        if isinstance(current, structured_type):
            structured_error = current
            break
        current = current.__cause__
    assert structured_error is not None
    state = structured_error.state
    assert state.moved is True
    actual_quarantine_path = tmp_path / state.relative_path
    assert actual_quarantine_path.exists()
    quarantined_document = json.loads(actual_quarantine_path.read_bytes())
    assert len(quarantined_document["completed_matches"]) == 1


def test_post_move_rollback_rejection_reports_provider_canonical_truthfully(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    native_move = results_provider._atomic_move_no_replace_at
    native_fsync = os.fsync
    native_rename = results_provider._atomic_rename_at
    quarantine_descriptor: int | None = None
    vanished_exchange_name: str | None = None
    injected = False
    rollback_rejected = False

    def arm_after_quarantine_move(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
    ) -> None:
        nonlocal quarantine_descriptor, vanished_exchange_name
        native_move(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
        )
        if destination_name == "entry":
            quarantine_descriptor = destination_descriptor
            vanished_exchange_name = source_name

    def fail_first_quarantine_fsync(descriptor: int) -> None:
        nonlocal injected
        if descriptor == quarantine_descriptor and not injected:
            injected = True
            raise OSError(errno.EIO, "injected post-move quarantine failure")
        native_fsync(descriptor)

    def reject_cross_directory_exchange(
        source_descriptor: int,
        source_name: str,
        destination_descriptor: int,
        destination_name: str,
        *,
        flags: int,
        operation: str,
    ) -> None:
        nonlocal rollback_rejected
        if (
            flags == results_provider._RENAME_EXCHANGE
            and source_descriptor != destination_descriptor
        ):
            rollback_rejected = True
            raise TournamentValidationError("injected cross-directory exchange rejection")
        native_rename(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
            flags=flags,
            operation=operation,
        )

    monkeypatch.setattr(
        results_provider,
        "_atomic_move_no_replace_at",
        arm_after_quarantine_move,
    )
    monkeypatch.setattr(os, "fsync", fail_first_quarantine_fsync)
    monkeypatch.setattr(
        results_provider,
        "_atomic_rename_at",
        reject_cross_directory_exchange,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert injected is True
    assert rollback_rejected is True
    assert len(json.loads(config.read_bytes())["completed_matches"]) == 1
    assert _regular_paths_with_bytes(tmp_path, original_bytes)
    assert vanished_exchange_name is not None
    vanished_path = tmp_path / vanished_exchange_name
    assert not vanished_path.exists()
    message = str(captured.value)
    assert "provider config remains canonical" in message
    assert str(vanished_path) not in message
    reported_paths = _reported_paths_under(message, tmp_path)
    assert reported_paths
    assert all(path.exists() for path in reported_paths)


def test_rollback_restores_newer_destination_and_reports_every_recovery_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    edit_c_bytes = _marked_config_bytes(config, "C-before-commit")
    edit_d_bytes = _marked_config_bytes(config, "D-before-rollback")
    original_exchange = results_provider._atomic_exchange_at
    original_entry_identity = results_provider._entry_identity_at
    edit_c_identity: tuple[int, int] | None = None
    edit_d_identity: tuple[int, int] | None = None

    def exchange_with_two_destination_edits(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal edit_c_identity, edit_d_identity
        destination_identity = original_entry_identity(
            parent_descriptor,
            destination_name,
            "test recovery destination",
        )
        if (
            edit_c_identity is None
            and (destination_identity.device, destination_identity.inode)
            == (preview.config_identity.device, preview.config_identity.inode)
        ):
            edit_c_identity = _atomic_install_regular(
                parent_descriptor,
                destination_name,
                edit_c_bytes,
                "edit-c",
            )
        elif edit_c_identity is not None and edit_d_identity is None:
            source_identity = original_entry_identity(
                parent_descriptor,
                source_name,
                "test rollback source",
            )
            if (source_identity.device, source_identity.inode) == edit_c_identity:
                edit_d_identity = _atomic_install_regular(
                    parent_descriptor,
                    destination_name,
                    edit_d_bytes,
                    "edit-d",
                )
        original_exchange(parent_descriptor, source_name, destination_name)

    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_at",
        exchange_with_two_destination_edits,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert edit_c_identity is not None
    assert edit_d_identity is not None
    assert config.read_bytes() == edit_d_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == edit_d_identity
    edit_c_paths = _paths_with_identity(tmp_path, edit_c_identity)
    assert edit_c_paths
    message = str(captured.value)
    for recovery_path in edit_c_paths:
        assert recovery_path != config
        assert str(recovery_path) in message
    hidden_paths = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(f".{config.name}.")
        and path.name != f".{config.name}.lock"
    ]
    assert hidden_paths
    for recovery_path in hidden_paths:
        assert str(recovery_path) in message


def test_destination_installed_after_rollback_remains_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    edit_c_bytes = _marked_config_bytes(config, "C-before-commit")
    edit_d_bytes = _marked_config_bytes(config, "D-after-rollback")
    original_exchange = results_provider._atomic_exchange_at
    original_entry_identity = results_provider._entry_identity_at
    edit_c_identity: tuple[int, int] | None = None
    edit_d_identity: tuple[int, int] | None = None

    def exchange_with_initial_destination_edit(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
    ) -> None:
        nonlocal edit_c_identity
        destination_identity = original_entry_identity(
            parent_descriptor,
            destination_name,
            "test destination",
        )
        if (
            edit_c_identity is None
            and (destination_identity.device, destination_identity.inode)
            == (preview.config_identity.device, preview.config_identity.inode)
        ):
            edit_c_identity = _atomic_install_regular(
                parent_descriptor,
                destination_name,
                edit_c_bytes,
                "edit-c-before-commit",
            )
        original_exchange(parent_descriptor, source_name, destination_name)

    def observe_with_post_rollback_destination(
        parent_descriptor: int,
        filename: str,
        label: str,
    ) -> object:
        nonlocal edit_d_identity
        if label == "rolled-back source" and edit_d_identity is None:
            edit_d_identity = _atomic_install_regular(
                parent_descriptor,
                config.name,
                edit_d_bytes,
                "edit-d-after-rollback",
            )
        return original_entry_identity(parent_descriptor, filename, label)

    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_at",
        exchange_with_initial_destination_edit,
    )
    monkeypatch.setattr(
        results_provider,
        "_entry_identity_at",
        observe_with_post_rollback_destination,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert edit_c_identity is not None
    assert edit_d_identity is not None
    assert config.read_bytes() == edit_d_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == edit_d_identity
    assert json.loads(config.read_bytes())["metadata"]["concurrent_edit"] == (
        "D-after-rollback"
    )
    original_paths = _regular_paths_with_bytes(tmp_path, original_bytes)
    assert original_paths
    message = str(captured.value)
    assert "installed after rollback remains canonical" in message
    for recovery_path in original_paths:
        assert recovery_path != config
        assert str(recovery_path) in message
    hidden_paths = [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(f".{config.name}.")
        and path.name != f".{config.name}.lock"
    ]
    for recovery_path in hidden_paths:
        assert str(recovery_path) in message


def test_apply_parent_swap_inside_final_transition_cannot_return_success(
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
    swapped = False

    def inject_parent_swap(
        destination: str | os.PathLike[str],
        parent_descriptor: int,
    ) -> None:
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(detached_parent)
            parent.mkdir()
            (parent / config.name).write_bytes(decoy_bytes)

    _patch_final_transition(monkeypatch, inject_parent_swap)

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


def test_apply_fails_closed_before_mutation_without_atomic_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    before = config.read_bytes()
    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_function",
        None,
        raising=False,
    )

    with pytest.raises(TournamentValidationError, match="exchange.*unavailable"):
        apply_results(config, preview)

    assert config.read_bytes() == before
    assert not (tmp_path / f".{config.name}.lock").exists()


def test_apply_fails_closed_before_mutation_without_no_replace_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    before = config.read_bytes()
    monkeypatch.setattr(
        results_provider,
        "_RENAME_NO_REPLACE",
        None,
        raising=False,
    )

    with pytest.raises(TournamentValidationError, match="no-replace.*unavailable"):
        apply_results(config, preview)

    assert config.read_bytes() == before
    assert not (tmp_path / f".{config.name}.lock").exists()


def test_runtime_no_replace_rejection_happens_before_canonical_exchange(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    original_bytes = config.read_bytes()
    original_identity = (config.stat().st_dev, config.stat().st_ino)
    native_rename = results_provider._atomic_exchange_function
    assert native_rename is not None
    observed_flags: list[int] = []

    def reject_no_replace_on_filesystem(
        source_descriptor: int,
        source_name: bytes,
        destination_descriptor: int,
        destination_name: bytes,
        flags: int,
    ) -> int:
        observed_flags.append(flags)
        if flags == results_provider._RENAME_NO_REPLACE:
            ctypes.set_errno(errno.EOPNOTSUPP)
            return -1
        return native_rename(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
            flags,
        )

    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_function",
        reject_no_replace_on_filesystem,
    )

    with pytest.raises(TournamentValidationError, match="no-replace") as captured:
        apply_results(config, preview)

    assert observed_flags
    assert results_provider._RENAME_EXCHANGE not in observed_flags
    assert config.read_bytes() == original_bytes
    assert (config.stat().st_dev, config.stat().st_ino) == original_identity
    reported_paths = _reported_paths_under(str(captured.value), tmp_path)
    assert reported_paths
    assert all(path.exists() for path in reported_paths)


def test_first_native_exchange_runtime_rejection_reports_retained_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    preview = preview_results(config, _json_source(tmp_path, [_result()]), format="json")
    before = config.read_bytes()
    before_identity = (config.stat().st_dev, config.stat().st_ino)
    native_rename = results_provider._atomic_exchange_function
    assert native_rename is not None
    rejected = False

    def reject_exchange_on_filesystem(
        source_descriptor: int,
        source_name: bytes,
        destination_descriptor: int,
        destination_name: bytes,
        flags: int,
    ) -> int:
        nonlocal rejected
        if flags == results_provider._RENAME_EXCHANGE:
            rejected = True
            ctypes.set_errno(errno.EOPNOTSUPP)
            return -1
        return native_rename(
            source_descriptor,
            source_name,
            destination_descriptor,
            destination_name,
            flags,
        )

    monkeypatch.setattr(
        results_provider,
        "_atomic_exchange_function",
        reject_exchange_on_filesystem,
    )

    with pytest.raises(TournamentValidationError) as captured:
        apply_results(config, preview)

    assert rejected is True
    assert config.read_bytes() == before
    assert (config.stat().st_dev, config.stat().st_ino) == before_identity
    message = str(captured.value)
    assert "runtime filesystem rejected atomic exchange after setup" in message
    artifacts = _provider_artifact_paths(tmp_path, config.name)
    assert artifacts
    for artifact in artifacts:
        assert str(artifact) in message


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
    assert "Backup retained:" in applied_output
    backup_path = Path(applied_output.rsplit("Backup retained: ", 1)[1].strip())
    assert backup_path.read_bytes() == before
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
