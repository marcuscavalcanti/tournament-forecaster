import json
import subprocess
import sys
from pathlib import Path

from scripts.update_group_results import _fifa_calendar_api_url


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
                            {"name": "Brasil", "code": "BRA"},
                            {"name": "Marrocos", "code": "MAR"},
                            {"name": "Haiti", "code": "HAI"},
                            {"name": "Escócia", "code": "SCO"},
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


def test_fifa_calendar_query_extends_one_utc_day_after_last_local_fixture() -> None:
    config = {
        "group_fixtures": [
            {"group": "J", "team_a": "Jordânia", "team_b": "Argentina", "date": "2026-06-27"},
        ],
        "results_fetch_through_date": "2026-07-05",
    }

    url = _fifa_calendar_api_url("https://api.fifa.com/api/v3/calendar/matches", config)

    assert "from=2026-06-27T00%3A00%3A00Z" in url
    assert "to=2026-07-05T23%3A59%3A59Z" in url


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


def test_update_group_results_apply_creates_local_config_when_default_falls_back_to_example(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    requested = config_dir / "worldcup_brazil.json"
    example = config_dir / "worldcup_brazil.example.json"
    results = tmp_path / "results.json"
    _write_config(example)
    before_example = example.read_text(encoding="utf-8")
    results.write_text(
        json.dumps(
            [
                {
                    "group": "C",
                    "team_a": "Haiti",
                    "score_a": 0,
                    "team_b": "Brasil",
                    "score_b": 3,
                    "source": "fifa",
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
            str(requested),
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
    assert requested.exists()
    assert example.read_text(encoding="utf-8") == before_example
    summary = json.loads(result.stdout)
    assert summary["effective_config"] == str(example)
    assert summary["write_config"] == str(requested)
    completed = json.loads(requested.read_text(encoding="utf-8"))["completed_group_matches"]
    assert completed[-1]["team_a"] == "Brasil"
    assert completed[-1]["score_a"] == 3


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


def test_update_group_results_extracts_completed_scores_from_fifa_html(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    html = tmp_path / "fifa.html"
    _write_config(config)
    html.write_text(
        """
        <html><body>
        <script id="__NEXT_DATA__" type="application/json">
        {
          "props": {
            "pageProps": {
              "pageData": {
                "content": [
                  {
                    "matches": [
                      {
                        "idMatch": "400000001",
                        "seasonName": [{"locale": "en-GB", "description": "FIFA World Cup 2026™"}],
                        "stageName": [{"locale": "en-GB", "description": "First Stage"}],
                        "matchDate": "2026-06-19",
                        "teamACountryCode": "BRA",
                        "teamBCountryCode": "HAI",
                        "teamAScore": 3,
                        "teamBScore": 0,
                        "resultType": 1
                      },
                      {
                        "idMatch": "400000002",
                        "seasonName": [{"locale": "en-GB", "description": "FIFA World Cup 2026™"}],
                        "stageName": [{"locale": "en-GB", "description": "First Stage"}],
                        "matchDate": "2026-06-24",
                        "teamACountryCode": "BRA",
                        "teamBCountryCode": "SCO",
                        "teamAScore": null,
                        "teamBScore": null,
                        "resultType": 0
                      }
                    ]
                  }
                ]
              }
            }
          }
        }
        </script>
        </body></html>
        """,
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/update_group_results.py",
            "--config",
            str(config),
            "--fifa-html",
            str(html),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["source_kind"] == "fifa"
    assert summary["would_add"] == 1
    assert summary["additions"][0]["team_a"] == "Brasil"
    assert summary["additions"][0]["score_a"] == 3
    assert summary["additions"][0]["team_b"] == "Haiti"
    assert summary["additions"][0]["score_b"] == 0
    assert summary["additions"][0]["source"] == "https://inside.fifa.com/data-centre/matches#400000001"


def test_update_group_results_extracts_completed_scores_from_fifa_api_json(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = tmp_path / "fifa-api.json"
    _write_config(config)
    payload.write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "IdMatch": "400000001",
                        "IdCompetition": "17",
                        "IdSeason": "285023",
                        "StageName": [{"Locale": "en-GB", "Description": "First Stage"}],
                        "Date": "2026-06-19T22:00:00Z",
                        "Home": {"Abbreviation": "BRA", "Score": 3},
                        "Away": {"Abbreviation": "HAI", "Score": 0},
                        "HomeTeamScore": 3,
                        "AwayTeamScore": 0,
                        "ResultType": 1,
                    },
                    {
                        "IdMatch": "400000002",
                        "IdCompetition": "17",
                        "IdSeason": "285023",
                        "StageName": [{"Locale": "en-GB", "Description": "First Stage"}],
                        "Date": "2026-06-24T22:00:00Z",
                        "Home": {"Abbreviation": "BRA", "Score": None},
                        "Away": {"Abbreviation": "SCO", "Score": None},
                        "HomeTeamScore": None,
                        "AwayTeamScore": None,
                        "ResultType": 0,
                    },
                ]
            },
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
            "--fifa-api-json",
            str(payload),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["source_kind"] == "fifa"
    assert summary["would_add"] == 1
    assert summary["additions"][0]["team_a"] == "Brasil"
    assert summary["additions"][0]["score_a"] == 3
    assert summary["additions"][0]["team_b"] == "Haiti"
    assert summary["additions"][0]["score_b"] == 0
    assert summary["additions"][0]["date"] == "2026-06-19"
    assert summary["additions"][0]["source"] == "https://inside.fifa.com/data-centre/matches#400000001"


def test_update_group_results_extracts_completed_knockout_scores_from_fifa_api_json(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = tmp_path / "fifa-api.json"
    _write_config(config)
    raw_config = json.loads(config.read_text(encoding="utf-8"))
    raw_config["groups_config"]["groups"]["F"] = [
        {"name": "Holanda", "code": "NED"},
        {"name": "Japão", "code": "JPN"},
        {"name": "Suécia", "code": "SWE"},
        {"name": "Tunísia", "code": "TUN"},
    ]
    raw_config["groups_config"]["groups"]["E"] = [
        {"name": "Alemanha", "code": "GER"},
        {"name": "Curaçau", "code": "CUW"},
        {"name": "Costa do Marfim", "code": "CIV"},
        {"name": "Equador", "code": "ECU"},
    ]
    raw_config["groups_config"]["groups"]["I"] = [
        {"name": "França", "code": "FRA"},
        {"name": "Senegal", "code": "SEN"},
        {"name": "Iraque", "code": "IRQ"},
        {"name": "Noruega", "code": "NOR"},
    ]
    config.write_text(json.dumps(raw_config, ensure_ascii=False, indent=2), encoding="utf-8")
    payload.write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "IdMatch": "400021516",
                        "StageName": [{"Locale": "en-GB", "Description": "Round of 32"}],
                        "Date": "2026-06-29T17:00:00Z",
                        "LocalDate": "2026-06-29T12:00:00Z",
                        "Home": {"Abbreviation": "BRA", "IdTeam": "43924", "Score": 2},
                        "Away": {"Abbreviation": "JPN", "IdTeam": "43819", "Score": 1},
                        "HomeTeamScore": 2,
                        "AwayTeamScore": 1,
                        "Winner": "43924",
                        "ResultType": 1,
                    },
                    {
                        "IdMatch": "400021514",
                        "StageName": [{"Locale": "en-GB", "Description": "Round of 32"}],
                        "Date": "2026-06-30T17:00:00Z",
                        "LocalDate": "2026-06-30T12:00:00Z",
                        "Home": {"Abbreviation": "CIV", "IdTeam": "43854", "Score": 1},
                        "Away": {"Abbreviation": "NOR", "IdTeam": "43881", "Score": 2},
                        "HomeTeamScore": 1,
                        "AwayTeamScore": 2,
                        "Winner": "43881",
                        "ResultType": 1,
                    },
                ]
            },
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
            "--fifa-api-json",
            str(payload),
            "--apply",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["would_add"] == 2
    completed = json.loads(config.read_text(encoding="utf-8"))["completed_knockout_matches"]
    assert completed == [
        {
            "phase": "16 avos",
            "team_a": "Brasil",
            "score_a": 2,
            "team_b": "Japão",
            "score_b": 1,
            "winner": "Brasil",
            "date": "2026-06-29",
            "source": "https://inside.fifa.com/data-centre/matches#400021516",
            "match_id": "400021516",
        },
        {
            "phase": "16 avos",
            "team_a": "Costa do Marfim",
            "score_a": 1,
            "team_b": "Noruega",
            "score_b": 2,
            "winner": "Noruega",
            "date": "2026-06-30",
            "source": "https://inside.fifa.com/data-centre/matches#400021514",
            "match_id": "400021514",
        },
    ]


def test_update_group_results_skips_fifa_live_score_until_result_is_final(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = tmp_path / "fifa-api.json"
    _write_config(config)
    payload.write_text(
        json.dumps(
            {
                "Results": [
                    {
                        "IdMatch": "400000001",
                        "IdCompetition": "17",
                        "IdSeason": "285023",
                        "StageName": [{"Locale": "en-GB", "Description": "First Stage"}],
                        "Date": "2026-06-24T22:00:00Z",
                        "Home": {"Abbreviation": "BRA", "Score": 1},
                        "Away": {"Abbreviation": "SCO", "Score": 0},
                        "HomeTeamScore": 1,
                        "AwayTeamScore": 0,
                        "ResultType": 0,
                    }
                ]
            },
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
            "--fifa-api-json",
            str(payload),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "nenhum placar final" in result.stderr


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


def test_update_group_results_does_not_conflict_when_existing_score_is_reversed_fixture_order(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    results = tmp_path / "results.json"
    _write_config(config)
    payload = json.loads(config.read_text(encoding="utf-8"))
    payload["completed_group_matches"].append(
        {
            "group": "C",
            "team_a": "Escócia",
            "score_a": 1,
            "team_b": "Haiti",
            "score_b": 0,
            "date": "2026-06-13",
            "source": "manual-reversed",
        }
    )
    config.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    results.write_text(
        json.dumps(
            [
                {
                    "group": "C",
                    "team_a": "Haiti",
                    "score_a": 0,
                    "team_b": "Escócia",
                    "score_b": 1,
                    "source": "fifa-canonical",
                }
            ],
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
    assert summary["conflicts"] == []
    assert summary["skipped_existing"] == 1
