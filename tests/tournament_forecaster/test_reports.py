from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time
from xml.etree import ElementTree

import pytest

from tournament_forecaster.domain import Forecast, MatchupProbability
from tournament_forecaster.errors import TournamentValidationError


def _forecast() -> Forecast:
    return Forecast(
        run_id="run-report-0001",
        generated_at="2026-07-11T12:00:00+00:00",
        tournament_id="synthetic-cup",
        focus_team_id="north-city",
        stage_probabilities={
            "final": 0.41,
            "group-stage": 1.0,
            "semi-finals": 0.72,
        },
        stage_order=("group-stage", "semi-finals", "final"),
        matchup_probabilities=(
            MatchupProbability("semi-finals", "river-town", 0.36),
            MatchupProbability("final", "east-city", 0.21),
        ),
        championship_probability=0.27,
        confidence_intervals={
            "group-stage": (1.0, 1.0),
            "semi-finals": (0.68, 0.76),
            "final": (0.37, 0.45),
            "championship_probability": (0.23, 0.31),
        },
        input_provenance=({"kind": "preset", "name": "Synthetic Cup"},),
        warnings=(),
        tournament_display_name="Synthetic <Cup> & Friends",
        team_display_names={
            "north-city": "North <City> & Co",
            "river-town": "River Town",
            "east-city": "East City",
        },
        simulation={"seed": 17, "iterations": 250, "confidence_level": 0.95},
    )


def _destination(tmp_path: Path, name: str = "north-city") -> Path:
    return tmp_path / "outputs" / "synthetic-cup" / name


def test_report_bundle_is_one_versioned_generation_at_stable_public_paths(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    paths = write_report_bundle(_forecast(), destination)

    assert destination.is_symlink()
    assert not Path(os.readlink(destination)).is_absolute()
    resolved = destination.resolve(strict=True)
    assert resolved != destination
    assert resolved.name.startswith(f"v{Forecast.SCHEMA_VERSION}-run-report-0001-")
    assert sorted(path.name for path in resolved.iterdir()) == [
        "bracket.svg",
        "forecast.json",
        "report.md",
    ]
    assert paths.current == destination
    assert paths.generation == resolved
    assert paths.json == resolved / "forecast.json"
    assert paths.markdown == resolved / "report.md"
    assert paths.svg == resolved / "bracket.svg"
    assert paths.current_json == destination / "forecast.json"
    assert paths.current_markdown == destination / "report.md"
    assert paths.current_svg == destination / "bracket.svg"
    assert all(path.is_file() and path.stat().st_size > 0 for path in paths)

    document = json.loads(paths.json.read_text(encoding="utf-8"))
    assert document["schema_version"] == Forecast.SCHEMA_VERSION
    assert document["run_id"] == "run-report-0001"
    assert document["stage_order"] == ["group-stage", "semi-finals", "final"]
    assert document["simulation"] == {
        "confidence_level": 0.95,
        "iterations": 250,
        "seed": 17,
    }

    markdown = paths.markdown.read_text(encoding="utf-8")
    assert "# Synthetic &lt;Cup&gt; &amp; Friends forecast" in markdown
    assert "North &lt;City&gt; &amp; Co" in markdown
    assert "27.0%" in markdown
    assert markdown.index("group-stage") < markdown.index("semi-finals") < markdown.index("final")

    svg_text = paths.svg.read_text(encoding="utf-8")
    root = ElementTree.fromstring(svg_text)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.attrib["viewBox"] == "0 0 960 540"
    assert "North &lt;City&gt; &amp; Co" in svg_text
    assert svg_text.index("group-stage") < svg_text.index("semi-finals") < svg_text.index("final")
    assert "<script" not in svg_text.lower()
    assert "href=" not in svg_text.lower()
    assert "/Users/" not in svg_text


def test_rendered_reports_publish_a_complete_generation_but_return_two_paths(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports import write_rendered_reports

    destination = _destination(tmp_path, "rerendered")
    paths = write_rendered_reports(_forecast(), destination)

    assert tuple(path.name for path in paths) == ("report.md", "bracket.svg")
    assert destination.is_symlink()
    assert paths.current == destination
    assert paths.generation == destination.resolve(strict=True)
    assert all(path.parent == paths.generation for path in paths)
    assert sorted(path.name for path in destination.resolve().iterdir()) == [
        "bracket.svg",
        "forecast.json",
        "report.md",
    ]
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0001"


@pytest.mark.parametrize("conflict", ["directory", "file", "symlink"])
def test_public_destination_conflicts_are_rejected_before_mutation(
    tmp_path: Path,
    conflict: str,
) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    destination.parent.mkdir(parents=True)
    if conflict == "directory":
        destination.mkdir()
        (destination / "user-file.txt").write_text("keep", encoding="utf-8")
    elif conflict == "file":
        destination.write_text("keep", encoding="utf-8")
    else:
        untrusted = tmp_path / "untrusted"
        untrusted.mkdir()
        destination.symlink_to(untrusted, target_is_directory=True)

    with pytest.raises(ValueError, match="not owned by Tournament Forecaster"):
        write_report_bundle(_forecast(), destination)

    assert not (destination.parent / ".tournament-forecast").exists()
    if conflict == "directory":
        assert (destination / "user-file.txt").read_text(encoding="utf-8") == "keep"
    elif conflict == "file":
        assert destination.read_text(encoding="utf-8") == "keep"
    else:
        assert destination.is_symlink()


def test_stale_owned_state_is_rejected_without_changing_public_pointer(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    write_report_bundle(_forecast(), destination)
    old_pointer = os.readlink(destination)
    control_owner = destination.parent / ".tournament-forecast" / "owner.json"
    control_owner.write_text('{"owner":"someone-else"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="stale or unowned report state"):
        write_report_bundle(replace(_forecast(), run_id="run-report-0002"), destination)

    assert os.readlink(destination) == old_pointer
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0001"


def test_staging_metadata_cannot_escape_owned_recovery_paths(tmp_path: Path) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    write_report_bundle(_forecast(), destination)
    staging = destination.parent / ".tournament-forecast" / "north-city" / "staging"
    malicious_generation = "../../../outside-sentinel"
    (staging / "malicious.json").write_text(
        json.dumps(
            {
                "digest": "0" * 64,
                "generation": malicious_generation,
                "layout_version": 1,
                "owner": "tournament-forecaster",
                "state": "staging",
            }
        ),
        encoding="utf-8",
    )
    sentinel = (
        destination.parent
        / ".tournament-forecast"
        / "north-city"
        / "metadata"
        / f"{malicious_generation}.json"
    ).resolve()
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("keep\n", encoding="utf-8")

    with pytest.raises(ValueError, match="stale or unowned report state"):
        write_report_bundle(_forecast(), destination)

    assert sentinel.read_text(encoding="utf-8") == "keep\n"


def test_untrusted_internal_pointer_symlink_is_rejected(tmp_path: Path) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    write_report_bundle(_forecast(), destination)
    generation = destination.resolve().name
    pointers = destination.parent / ".tournament-forecast" / "north-city" / "pointers"
    (pointers / "untrusted").symlink_to(
        f".tournament-forecast/north-city/generations/../generations/{generation}",
        target_is_directory=True,
    )

    with pytest.raises(ValueError, match="stale or unowned report state"):
        write_report_bundle(_forecast(), destination)

    assert (pointers / "untrusted").is_symlink()


@pytest.mark.parametrize("parent_conflict", ["file", "symlink"])
def test_parent_conflicts_are_rejected_before_reporter_state_is_created(
    tmp_path: Path,
    parent_conflict: str,
) -> None:
    from tournament_forecaster.reports import write_report_bundle

    parent = tmp_path / "parent"
    if parent_conflict == "file":
        parent.write_text("keep", encoding="utf-8")
    else:
        real_parent = tmp_path / "real-parent"
        real_parent.mkdir()
        parent.symlink_to(real_parent, target_is_directory=True)
    destination = parent / "north-city"

    message = "report parent conflicts" if parent_conflict == "file" else "ancestor symlink"
    with pytest.raises(ValueError, match=message):
        write_report_bundle(_forecast(), destination)

    if parent_conflict == "file":
        assert parent.read_text(encoding="utf-8") == "keep"
    else:
        assert list((tmp_path / "real-parent").iterdir()) == []


def test_platform_without_atomic_symlink_publication_fails_loudly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as reports

    destination = _destination(tmp_path)

    def unsupported(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError("symlinks unavailable")

    monkeypatch.setattr(os, "symlink", unsupported)
    with pytest.raises(ValueError, match="atomic report publication is unavailable"):
        reports.write_report_bundle(_forecast(), destination)

    assert not os.path.lexists(destination)


class _InjectedMutationFailure(OSError):
    pass


def _assert_visible_generation_is_coherent(destination: Path) -> str | None:
    if not os.path.lexists(destination):
        return None
    assert destination.is_symlink()
    generation = destination.resolve(strict=True)
    document = json.loads((generation / "forecast.json").read_text(encoding="utf-8"))
    run_id = str(document["run_id"])
    assert f"`{run_id}`" in (generation / "report.md").read_text(encoding="utf-8")
    svg = (generation / "bracket.svg").read_text(encoding="utf-8")
    assert f"Run {run_id}" in svg
    ElementTree.fromstring(svg)
    return run_id


def _assert_no_unmarked_final_directories(destination: Path) -> None:
    control = destination.parent / ".tournament-forecast"
    if not control.exists():
        return
    assert (control / "owner.json").is_file()
    focus = control / destination.name
    if not focus.exists():
        return
    assert (focus / "owner.json").is_file()
    staging = focus / "staging"
    for directory in (path for path in staging.iterdir() if path.is_dir()):
        if directory.name.startswith(".stage-init-"):
            continue
        assert (directory / ".stage-owner.json").is_file() or (
            staging / f"{directory.name}.json"
        ).is_file()


def test_every_first_publication_mutation_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    observed: list[tuple[str, Path]] = []
    probe = _destination(tmp_path / "probe")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            publication,
            "_after_filesystem_mutation",
            lambda operation, path: observed.append((operation, path)),
        )
        publication.write_report_bundle(_forecast(), probe)
    assert observed
    assert {
        "mkdir-parent",
        "mkdir",
        "write",
        "rename",
        "promote-generation",
        "symlink",
        "swap-pointer",
        "unlink",
    } <= {operation for operation, _path in observed}

    for failure_index in range(len(observed)):
        destination = _destination(tmp_path / f"failure-{failure_index}")
        calls = 0

        def fail_after_mutation(_operation: str, _path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == failure_index + 1:
                raise _InjectedMutationFailure(f"mutation {calls}")

        with monkeypatch.context() as patcher:
            patcher.setattr(
                publication,
                "_after_filesystem_mutation",
                fail_after_mutation,
            )
            with pytest.raises(_InjectedMutationFailure, match="mutation"):
                publication.write_report_bundle(_forecast(), destination)

        assert _assert_visible_generation_is_coherent(destination) in {
            None,
            "run-report-0001",
        }
        _assert_no_unmarked_final_directories(destination)
        publication.write_report_bundle(_forecast(), destination)
        assert _assert_visible_generation_is_coherent(destination) == "run-report-0001"


def test_every_update_mutation_preserves_a_coherent_pointer_and_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    replacement = replace(
        _forecast(),
        run_id="run-report-0002",
        generated_at="2026-07-11T13:00:00+00:00",
    )
    probe = _destination(tmp_path / "probe-update")
    publication.write_report_bundle(_forecast(), probe)
    observed: list[tuple[str, Path]] = []
    with monkeypatch.context() as patcher:
        patcher.setattr(
            publication,
            "_after_filesystem_mutation",
            lambda operation, path: observed.append((operation, path)),
        )
        publication.write_report_bundle(replacement, probe)
    assert observed

    for failure_index in range(len(observed)):
        destination = _destination(tmp_path / f"update-failure-{failure_index}")
        publication.write_report_bundle(_forecast(), destination)
        calls = 0

        def fail_after_mutation(_operation: str, _path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == failure_index + 1:
                raise _InjectedMutationFailure(f"mutation {calls}")

        with monkeypatch.context() as patcher:
            patcher.setattr(
                publication,
                "_after_filesystem_mutation",
                fail_after_mutation,
            )
            with pytest.raises(_InjectedMutationFailure, match="mutation"):
                publication.write_report_bundle(replacement, destination)

        assert _assert_visible_generation_is_coherent(destination) in {
            "run-report-0001",
            "run-report-0002",
        }
        _assert_no_unmarked_final_directories(destination)
        publication.write_report_bundle(replacement, destination)
        assert _assert_visible_generation_is_coherent(destination) == "run-report-0002"


def test_every_fallback_first_publish_temp_creation_is_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    monkeypatch.setattr(publication, "_USE_DIR_FD", False)
    observed: list[Path] = []
    probe = _destination(tmp_path / "probe-fallback-first")
    with monkeypatch.context() as patcher:
        patcher.setattr(
            publication,
            "_after_filesystem_mutation",
            lambda operation, path: observed.append(path)
            if operation == "write-temp"
            else None,
        )
        publication.write_report_bundle(_forecast(), probe)

    assert len(observed) == 8
    assert all(publication._ATOMIC_TEMP_NAME.fullmatch(path.name) for path in observed)

    for failure_index in range(len(observed)):
        destination = _destination(tmp_path / f"fallback-first-{failure_index}")
        temporary_paths: list[Path] = []

        def fail_after_temp_creation(operation: str, path: Path) -> None:
            if operation != "write-temp":
                return
            temporary_paths.append(path)
            if len(temporary_paths) == failure_index + 1:
                raise _InjectedMutationFailure(f"fallback temp {len(temporary_paths)}")

        with monkeypatch.context() as patcher:
            patcher.setattr(
                publication,
                "_after_filesystem_mutation",
                fail_after_temp_creation,
            )
            with pytest.raises(_InjectedMutationFailure, match="fallback temp"):
                publication.write_report_bundle(_forecast(), destination)

        interrupted_temp = temporary_paths[-1]
        assert interrupted_temp.exists()
        assert _assert_visible_generation_is_coherent(destination) is None

        publication.write_report_bundle(_forecast(), destination)

        assert not interrupted_temp.exists()
        assert _assert_visible_generation_is_coherent(destination) == "run-report-0001"


def test_every_fallback_update_temp_creation_preserves_old_pointer_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    monkeypatch.setattr(publication, "_USE_DIR_FD", False)
    replacement = replace(
        _forecast(),
        run_id="run-report-0002",
        generated_at="2026-07-11T13:00:00+00:00",
    )
    probe = _destination(tmp_path / "probe-fallback-update")
    publication.write_report_bundle(_forecast(), probe)
    observed: list[Path] = []
    with monkeypatch.context() as patcher:
        patcher.setattr(
            publication,
            "_after_filesystem_mutation",
            lambda operation, path: observed.append(path)
            if operation == "write-temp"
            else None,
        )
        publication.write_report_bundle(replacement, probe)

    assert len(observed) == 6
    assert all(publication._ATOMIC_TEMP_NAME.fullmatch(path.name) for path in observed)

    for failure_index in range(len(observed)):
        destination = _destination(tmp_path / f"fallback-update-{failure_index}")
        publication.write_report_bundle(_forecast(), destination)
        old_pointer = os.readlink(destination)
        temporary_paths: list[Path] = []

        def fail_after_temp_creation(operation: str, path: Path) -> None:
            if operation != "write-temp":
                return
            temporary_paths.append(path)
            if len(temporary_paths) == failure_index + 1:
                raise _InjectedMutationFailure(f"fallback temp {len(temporary_paths)}")

        with monkeypatch.context() as patcher:
            patcher.setattr(
                publication,
                "_after_filesystem_mutation",
                fail_after_temp_creation,
            )
            with pytest.raises(_InjectedMutationFailure, match="fallback temp"):
                publication.write_report_bundle(replacement, destination)

        interrupted_temp = temporary_paths[-1]
        assert interrupted_temp.exists()
        assert os.readlink(destination) == old_pointer
        assert _assert_visible_generation_is_coherent(destination) == "run-report-0001"

        publication.write_report_bundle(replacement, destination)

        assert not interrupted_temp.exists()
        assert _assert_visible_generation_is_coherent(destination) == "run-report-0002"


def _init_orphan_path(
    publication: object,
    destination: Path,
    kind: str,
    token: str,
) -> Path:
    layout = publication._layout(destination)  # type: ignore[attr-defined]
    if kind == "control":
        return layout.parent / f".tournament-forecast.init-{token}"
    if kind == "focus":
        return layout.control / f".{destination.name}.focus-init-{token}"
    return layout.staging / f".stage-init-{token}"


@pytest.mark.parametrize("kind", ["control", "focus", "stage"])
@pytest.mark.parametrize("shape", ["empty", "owned-layout"])
def test_retry_reclaims_strictly_recognized_init_orphans(
    tmp_path: Path,
    kind: str,
    shape: str,
) -> None:
    import tournament_forecaster.reports.publication as publication

    destination = _destination(tmp_path)
    publication.write_report_bundle(_forecast(), destination)
    orphan = _init_orphan_path(publication, destination, kind, "c" * 32)
    orphan.mkdir()

    if shape == "owned-layout" and kind == "control":
        (orphan / "owner.json").write_text(
            publication._json_text(publication._control_owner()),
            encoding="utf-8",
        )
    elif shape == "owned-layout" and kind == "focus":
        (orphan / "owner.json").write_text(
            publication._json_text(publication._focus_owner(destination.name)),
            encoding="utf-8",
        )
        for name in ("generations", "metadata", "staging", "pointers"):
            (orphan / name).mkdir()
    elif shape == "owned-layout":
        (orphan / ".stage-owner.json").write_text(
            publication._json_text(
                publication._staging_metadata(
                    "v2-orphan-0123456789abcdef",
                    "0" * 64,
                )
            ),
            encoding="utf-8",
        )

    publication.write_report_bundle(_forecast(), destination)

    assert not os.path.lexists(orphan)
    assert _assert_visible_generation_is_coherent(destination) == "run-report-0001"


@pytest.mark.parametrize("kind", ["control", "focus", "stage"])
@pytest.mark.parametrize("hostile_shape", ["bad-uuid", "symlink", "ambiguous"])
def test_retry_preserves_hostile_init_lookalikes(
    tmp_path: Path,
    kind: str,
    hostile_shape: str,
) -> None:
    import tournament_forecaster.reports.publication as publication

    destination = _destination(tmp_path)
    publication.write_report_bundle(_forecast(), destination)
    old_pointer = os.readlink(destination)
    token = "not-a-uuid" if hostile_shape == "bad-uuid" else "d" * 32
    hostile = _init_orphan_path(publication, destination, kind, token)
    sentinel_root = tmp_path / f"{kind}-{hostile_shape}-sentinel"

    if hostile_shape == "symlink":
        sentinel_root.mkdir()
        sentinel = sentinel_root / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        hostile.symlink_to(sentinel_root, target_is_directory=True)
    else:
        hostile.mkdir()
        sentinel = hostile / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")

    with pytest.raises(ValueError, match="stale or unowned report state"):
        publication.write_report_bundle(_forecast(), destination)

    assert os.path.lexists(hostile)
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert os.readlink(destination) == old_pointer


@pytest.mark.parametrize(
    "residue_location",
    ["marked-stage", "staging", "metadata"],
)
def test_owned_atomic_write_residue_is_recovered_on_retry(
    tmp_path: Path,
    residue_location: str,
) -> None:
    import tournament_forecaster.reports.publication as publication

    destination = _destination(tmp_path)
    publication.write_report_bundle(_forecast(), destination)
    old_pointer = os.readlink(destination)
    layout = publication._layout(destination)
    generation = "v2-interrupted-0123456789abcdef"
    token = "a" * 32
    write_token = "b" * 32

    if residue_location == "marked-stage":
        stage = layout.staging / f".stage-init-{token}"
        stage.mkdir()
        (stage / ".stage-owner.json").write_text(
            publication._json_text(
                publication._staging_metadata(generation, "0" * 64)
            ),
            encoding="utf-8",
        )
        residue = stage / f".forecast.json.{write_token}.tmp"
    elif residue_location == "staging":
        residue = layout.staging / f".{token}.json.{write_token}.tmp"
    else:
        residue = layout.metadata / f".{generation}.json.{write_token}.tmp"
    residue.write_text("partial", encoding="utf-8")

    replacement = replace(
        _forecast(),
        run_id="run-report-0002",
        generated_at="2026-07-11T13:00:00+00:00",
    )
    publication.write_report_bundle(replacement, destination)

    assert os.readlink(destination) != old_pointer
    assert _assert_visible_generation_is_coherent(destination) == "run-report-0002"
    assert not residue.exists()


def test_mutation_sensitive_traversal_never_reopens_an_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    if not publication._USE_DIR_FD:
        pytest.skip("directory-fd traversal is unavailable")
    real_open = publication.os.open

    def audited_open(
        path: str | bytes | int,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        lexical = Path(os.fsdecode(path)) if not isinstance(path, int) else None
        if (
            lexical is not None
            and lexical.is_absolute()
            and lexical != Path(lexical.anchor)
        ):
            raise AssertionError(f"reopened checked absolute path: {lexical}")
        kwargs = {} if dir_fd is None else {"dir_fd": dir_fd}
        return real_open(path, flags, mode, **kwargs)

    monkeypatch.setattr(publication.os, "open", audited_open)

    paths = publication.write_report_bundle(_forecast(), _destination(tmp_path))

    assert paths.json.is_file()


def test_traversal_destination_is_rejected_before_mutation(tmp_path: Path) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = tmp_path / "safe" / ".." / "escaped" / "north-city"

    with pytest.raises(ValueError, match="must not contain '..'"):
        write_report_bundle(_forecast(), destination)

    assert not (tmp_path / "escaped").exists()


def test_ancestor_symlink_inserted_after_preflight_is_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as publication

    output_root = tmp_path / "output-alias"
    real_root = tmp_path / "real-output"
    (real_root / "synthetic-cup").mkdir(parents=True)
    destination = output_root / "synthetic-cup" / "north-city"
    real_render = publication.render_json_report

    def insert_symlink(forecast: Forecast) -> str:
        output_root.symlink_to(real_root, target_is_directory=True)
        return real_render(forecast)

    monkeypatch.setattr(publication, "render_json_report", insert_symlink)

    with pytest.raises(ValueError, match="ancestor symlink"):
        publication.write_report_bundle(_forecast(), destination)

    assert list((real_root / "synthetic-cup").iterdir()) == []


def test_interruption_before_generation_promotion_leaves_old_generation_and_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as reports

    destination = _destination(tmp_path)
    write_report = reports.write_report_bundle
    write_report(_forecast(), destination)
    old_pointer = os.readlink(destination)
    replacement = replace(
        _forecast(),
        run_id="run-report-0002",
        generated_at="2026-07-11T13:00:00+00:00",
    )

    def interrupt(_source: Path, _destination: Path) -> None:
        raise OSError("injected interruption before generation promotion")

    monkeypatch.setattr(reports, "_promote_staged_generation", interrupt)
    with pytest.raises(OSError, match="injected interruption"):
        write_report(replacement, destination)

    assert os.readlink(destination) == old_pointer
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0001"
    staging = destination.parent / ".tournament-forecast" / "north-city" / "staging"
    assert any(staging.iterdir())

    monkeypatch.undo()
    write_report(replacement, destination)
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0002"
    assert list(staging.iterdir()) == []


def test_interruption_before_pointer_swap_keeps_old_generation_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as reports

    destination = _destination(tmp_path)
    reports.write_report_bundle(_forecast(), destination)
    old_pointer = os.readlink(destination)
    replacement = replace(
        _forecast(),
        run_id="run-report-0002",
        generated_at="2026-07-11T13:00:00+00:00",
    )

    def interrupt(_source: Path, _destination: Path) -> None:
        raise OSError("injected interruption before pointer swap")

    monkeypatch.setattr(reports, "_swap_public_pointer", interrupt)
    with pytest.raises(OSError, match="injected interruption"):
        reports.write_report_bundle(replacement, destination)

    assert os.readlink(destination) == old_pointer
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0001"

    monkeypatch.undo()
    reports.write_report_bundle(replacement, destination)
    assert os.readlink(destination) != old_pointer
    assert json.loads((destination / "forecast.json").read_text(encoding="utf-8"))[
        "run_id"
    ] == "run-report-0002"


def test_invalid_svg_fails_before_the_public_pointer_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tournament_forecaster.reports.publication as reports

    destination = _destination(tmp_path)
    reports.write_report_bundle(_forecast(), destination)
    old_pointer = os.readlink(destination)
    monkeypatch.setattr(reports, "render_bracket_svg", lambda _forecast: "<svg>\x01</svg>")

    with pytest.raises(TournamentValidationError, match="valid XML"):
        reports.write_report_bundle(
            replace(_forecast(), run_id="run-report-0002"),
            destination,
        )

    assert os.readlink(destination) == old_pointer


def test_returned_snapshot_paths_never_observe_a_later_public_generation(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports import write_report_bundle

    destination = _destination(tmp_path)
    initial = replace(_forecast(), run_id="run-reader-0000")
    snapshot = write_report_bundle(initial, destination)
    stop = threading.Event()
    errors: list[str] = []
    observed: set[str] = set()

    def read_generations() -> None:
        while not stop.is_set():
            try:
                document = json.loads(
                    snapshot.json.read_text(encoding="utf-8")
                )
                run_id = str(document["run_id"])
                markdown = snapshot.markdown.read_text(encoding="utf-8")
                svg = snapshot.svg.read_text(encoding="utf-8")
                if f"`{run_id}`" not in markdown or f"Run {run_id}" not in svg:
                    errors.append(run_id)
                    return
                ElementTree.fromstring(svg)
                observed.add(run_id)
            except BaseException as error:  # pragma: no cover - assertion reports details
                errors.append(repr(error))
                return

    reader = threading.Thread(target=read_generations)
    reader.start()
    try:
        for index in range(1, 13):
            write_report_bundle(
                replace(
                    _forecast(),
                    run_id=f"run-reader-{index:04d}",
                    generated_at=f"2026-07-11T13:00:{index:02d}+00:00",
                ),
                destination,
            )
            time.sleep(0.001)
    finally:
        stop.set()
        reader.join(timeout=5)

    assert not reader.is_alive()
    assert errors == []
    assert observed == {"run-reader-0000"}
    assert json.loads(snapshot.json.read_text(encoding="utf-8"))["run_id"] == (
        "run-reader-0000"
    )


def test_markdown_sanitizes_all_accepted_user_text_contexts() -> None:
    from tournament_forecaster.reports.markdown_report import render_markdown_report

    attack = (
        "Line one\r\n# forged heading | forged cell <script>alert(1)</script> "
        "![remote](https://example.invalid/pixel.png) file:///Users/alice/secret "
        "/home/alice/private /root/private /var/home/alice/private "
        "C:\\Users\\alice\\token - forged list"
    )
    forecast = replace(
        _forecast(),
        generated_at=attack,
        tournament_display_name=attack,
        team_display_names={
            "north-city": attack,
            "river-town": attack,
            "east-city": "East City",
        },
        warnings=(attack,),
    )

    markdown = render_markdown_report(forecast)

    assert markdown.splitlines()[0].startswith("# Line one ")
    assert [line for line in markdown.splitlines() if line.startswith("# ")] == [
        markdown.splitlines()[0]
    ]
    assert "<script" not in markdown.lower()
    assert "![" not in markdown
    assert "file://" not in markdown.lower()
    assert "/Users/" not in markdown
    assert "/home/" not in markdown
    assert "/root/" not in markdown
    assert "/var/home/" not in markdown
    assert "C:\\Users\\" not in markdown
    assert "\\| forged cell" in markdown
    assert "&lt;script&gt;" in markdown
    warning_lines = [line for line in markdown.splitlines() if line.startswith("- ")]
    assert len(warning_lines) == 1


def test_forecast_rejects_non_xml_1_0_text() -> None:
    with pytest.raises(TournamentValidationError, match="XML 1.0-safe"):
        replace(_forecast(), tournament_display_name="Unsafe\x01Cup")


@pytest.mark.parametrize(
    "stage_order",
    [
        ("group-stage", "group-stage", "final"),
        ("group-stage", "final"),
        ("group-stage", "semi-finals", "final", "extra-stage"),
    ],
)
def test_forecast_stage_order_must_be_a_duplicate_free_permutation(
    stage_order: tuple[str, ...],
) -> None:
    with pytest.raises(TournamentValidationError, match="stage order"):
        replace(_forecast(), stage_order=stage_order)


def test_forecast_loader_rejects_non_finite_and_unsupported_documents(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports.json_report import load_forecast

    valid = _forecast().to_dict()

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(
        json.dumps({**valid, "schema_version": Forecast.SCHEMA_VERSION + 1}),
        encoding="utf-8",
    )
    with pytest.raises(TournamentValidationError, match="unsupported forecast schema version"):
        load_forecast(unsupported)

    non_finite = tmp_path / "non-finite.json"
    non_finite.write_text(
        json.dumps(valid).replace(
            '"championship_probability": 0.27',
            '"championship_probability": NaN',
        ),
        encoding="utf-8",
    )
    with pytest.raises(TournamentValidationError, match="must be finite"):
        load_forecast(non_finite)



def test_frozen_pre_stage_order_v2_artifact_uses_insertion_order_fallback() -> None:
    from tournament_forecaster.reports.json_report import load_forecast

    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "forecast-v2-pre-stage-order.json"
    )
    document = json.loads(fixture.read_text(encoding="utf-8"))

    forecast = load_forecast(fixture)

    assert forecast.stage_order == tuple(document["stage_probabilities"])
    assert forecast.to_dict()["stage_order"] == list(document["stage_probabilities"])


def test_current_v2_forecast_round_trip_preserves_explicit_stage_order(
    tmp_path: Path,
) -> None:
    from tournament_forecaster.reports.json_report import load_forecast

    path = tmp_path / "current-v2.json"
    path.write_text(json.dumps(_forecast().to_dict()), encoding="utf-8")

    loaded = load_forecast(path)

    assert loaded.to_dict() == _forecast().to_dict()
    assert loaded.stage_order == ("group-stage", "semi-finals", "final")
