import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _write_config(path: Path) -> None:
    fixtures = [
        ("Brasil", "Marrocos", "2026-06-13"),
        ("Brasil", "Haiti", "2026-06-19"),
        ("Brasil", "Escócia", "2026-06-24"),
        ("Marrocos", "Haiti", "2026-06-24"),
        ("Marrocos", "Escócia", "2026-06-19"),
        ("Haiti", "Escócia", "2026-06-13"),
    ]
    path.write_text(
        json.dumps(
            {
                "brazil_team_name": "Brasil",
                "brazil_group": "C",
                "groups_config": {
                    "groups": {
                        "C": [
                            {"name": "Brasil"},
                            {"name": "Marrocos"},
                            {"name": "Haiti"},
                            {"name": "Escócia"},
                        ]
                    }
                },
                "group_fixtures": [
                    {"group": "C", "team_a": team_a, "team_b": team_b, "date": date}
                    for team_a, team_b, date in fixtures
                ],
                "completed_group_matches": [
                    {
                        "group": "C",
                        "team_a": "Brasil",
                        "score_a": 1,
                        "team_b": "Marrocos",
                        "score_b": 1,
                        "date": "2026-06-13",
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def test_update_group_results_dry_run_matches_fixture_without_writing(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    results = tmp_path / "results.json"
    _write_config(config)
    results.write_text(
        json.dumps(
            {
                "results": [
                    {
                        "group": "C",
                        "team_a": "Haiti",
                        "score_a": 0,
                        "team_b": "Brasil",
                        "score_b": 3,
                        "source": "manual-test",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/update_group_results.py", "--config", str(config), "--results", str(results)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["dry_run"] is True
    assert summary["would_add"] == 1
    assert len(json.loads(config.read_text(encoding="utf-8"))["completed_group_matches"]) == 1


def test_update_group_results_apply_writes_canonical_fixture_order(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    results = tmp_path / "results.json"
    _write_config(config)
    results.write_text(
        json.dumps(
            [
                {
                    "group": "C",
                    "home": "Haiti",
                    "home_score": 0,
                    "away": "Brasil",
                    "away_score": 3,
                    "source_url": "https://example.test/match",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/update_group_results.py",
            "--config",
            str(config),
            "--results",
            str(results),
            "--apply",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    completed = json.loads(config.read_text(encoding="utf-8"))["completed_group_matches"]
    assert completed[-1] == {
        "group": "C",
        "team_a": "Brasil",
        "score_a": 3,
        "team_b": "Haiti",
        "score_b": 0,
        "date": "2026-06-19",
        "source": "https://example.test/match",
    }


def test_update_group_results_rejects_result_that_does_not_match_fixture(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    results = tmp_path / "results.json"
    _write_config(config)
    results.write_text(
        json.dumps(
            [{"team_a": "Brasil", "score_a": 2, "team_b": "Alemanha", "score_b": 1}],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/update_group_results.py", "--config", str(config), "--results", str(results)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "não casa com group_fixtures" in result.stderr


def test_update_group_results_replace_updates_existing_conflict(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    results = tmp_path / "results.json"
    _write_config(config)
    results.write_text(
        json.dumps(
            [
                {
                    "group": "C",
                    "team_a": "Brasil",
                    "score_a": 2,
                    "team_b": "Marrocos",
                    "score_b": 1,
                    "source": "correction",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/update_group_results.py",
            "--config",
            str(config),
            "--results",
            str(results),
            "--apply",
            "--replace",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    completed = json.loads(config.read_text(encoding="utf-8"))["completed_group_matches"]
    assert len(completed) == 1
    assert completed[0]["score_a"] == 2
    assert completed[0]["score_b"] == 1
    assert completed[0]["source"] == "correction"
