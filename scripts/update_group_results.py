#!/usr/bin/env python3
"""Validate and merge completed World Cup group results into the config ledger."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.bracket import hydrate_canonical_configs
from worldcup_brazil.cli import _effective_config_path


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).casefold().strip()


def _pair_key(team_a: str, team_b: str) -> tuple[str, str]:
    return tuple(sorted((_normalize(team_a), _normalize(team_b))))


def _score_value(record: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in record:
            continue
        try:
            return int(record[key])
        except (TypeError, ValueError):
            return None
    return None


def _read_json_or_csv(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        raw = payload.get("results") or payload.get("completed_group_matches") or payload.get("matches") or []
    else:
        raw = payload
    if not isinstance(raw, list):
        raise ValueError("arquivo de resultados deve conter uma lista ou uma chave results")
    return [item for item in raw if isinstance(item, dict)]


def _result_teams_and_scores(record: dict[str, Any]) -> tuple[str, int, str, int] | None:
    team_a = str(record.get("team_a") or record.get("home") or record.get("team1") or "").strip()
    team_b = str(record.get("team_b") or record.get("away") or record.get("team2") or "").strip()
    if not team_a or not team_b:
        return None
    score_a = _score_value(record, "score_a", "home_score", "goals_a", "team_a_score")
    score_b = _score_value(record, "score_b", "away_score", "goals_b", "team_b_score")
    if score_a is None or score_b is None:
        score_text = str(record.get("score") or "").strip()
        score_match = re.fullmatch(r"\s*(\d+)\s*[-xX]\s*(\d+)\s*", score_text)
        if score_match:
            score_a = int(score_match.group(1))
            score_b = int(score_match.group(2))
    if score_a is None or score_b is None:
        return None
    return team_a, score_a, team_b, score_b


def _fixture_lookup(config: dict[str, Any]) -> dict[tuple[str, tuple[str, str]], list[dict[str, Any]]]:
    lookup: dict[tuple[str, tuple[str, str]], list[dict[str, Any]]] = {}
    for fixture in config.get("group_fixtures") or []:
        if not isinstance(fixture, dict):
            continue
        group = str(fixture.get("group") or "").strip().upper()
        team_a = str(fixture.get("team_a") or "").strip()
        team_b = str(fixture.get("team_b") or "").strip()
        if not group or not team_a or not team_b:
            continue
        lookup.setdefault((group, _pair_key(team_a, team_b)), []).append(fixture)
    return lookup


def _existing_lookup(config: dict[str, Any]) -> dict[tuple[str, tuple[str, str]], dict[str, Any]]:
    existing: dict[tuple[str, tuple[str, str]], dict[str, Any]] = {}
    for result in config.get("completed_group_matches") or []:
        if not isinstance(result, dict):
            continue
        group = str(result.get("group") or "").strip().upper()
        team_a = str(result.get("team_a") or "").strip()
        team_b = str(result.get("team_b") or "").strip()
        if group and team_a and team_b:
            existing[(group, _pair_key(team_a, team_b))] = result
    return existing


def _canonical_result(record: dict[str, Any], fixtures: list[dict[str, Any]]) -> dict[str, Any]:
    parsed = _result_teams_and_scores(record)
    if parsed is None:
        raise ValueError(f"resultado sem times/placar legíveis: {record}")
    raw_team_a, raw_score_a, raw_team_b, raw_score_b = parsed
    fixture = fixtures[0]
    fixture_team_a = str(fixture.get("team_a") or "").strip()
    fixture_team_b = str(fixture.get("team_b") or "").strip()
    if _normalize(raw_team_a) == _normalize(fixture_team_a):
        score_a, score_b = raw_score_a, raw_score_b
    elif _normalize(raw_team_b) == _normalize(fixture_team_a):
        score_a, score_b = raw_score_b, raw_score_a
    else:
        raise ValueError(f"resultado não casa com a ordem do fixture: {record}")
    canonical = {
        "group": str(fixture.get("group") or "").strip().upper(),
        "team_a": fixture_team_a,
        "score_a": score_a,
        "team_b": fixture_team_b,
        "score_b": score_b,
        "date": fixture.get("date"),
    }
    source = record.get("source") or record.get("source_url")
    if source:
        canonical["source"] = source
    return canonical


def _merge_results(config: dict[str, Any], records: list[dict[str, Any]], *, replace: bool = False) -> dict[str, Any]:
    fixtures = _fixture_lookup(config)
    existing = _existing_lookup(config)
    additions: list[dict[str, Any]] = []
    replacements: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    conflicts: list[str] = []
    unmatched: list[str] = []

    for record in records:
        parsed = _result_teams_and_scores(record)
        if parsed is None:
            unmatched.append(f"resultado sem times/placar legíveis: {record}")
            continue
        team_a, _, team_b, _ = parsed
        group = str(record.get("group") or "").strip().upper()
        candidates: list[tuple[tuple[str, tuple[str, str]], list[dict[str, Any]]]] = []
        if group:
            key = (group, _pair_key(team_a, team_b))
            candidates = [(key, fixtures[key])] if key in fixtures else []
        else:
            pair = _pair_key(team_a, team_b)
            candidates = [(key, value) for key, value in fixtures.items() if key[1] == pair]
        if not candidates:
            unmatched.append(f"{team_a} x {team_b} não casa com group_fixtures")
            continue
        if len(candidates) > 1:
            unmatched.append(f"{team_a} x {team_b} é ambíguo; informe group")
            continue
        key, fixture_matches = candidates[0]
        canonical = _canonical_result(record, fixture_matches)
        current = existing.get(key)
        if current:
            same_score = int(current.get("score_a", -1)) == canonical["score_a"] and int(current.get("score_b", -1)) == canonical["score_b"]
            if same_score:
                skipped.append(canonical)
                continue
            if not replace:
                conflicts.append(
                    f"{canonical['group']}: {canonical['team_a']} x {canonical['team_b']} já existe com placar "
                    f"{current.get('score_a')}-{current.get('score_b')}; novo {canonical['score_a']}-{canonical['score_b']}"
                )
                continue
            current.update(canonical)
            replacements.append(canonical)
            continue
        additions.append(canonical)
        existing[key] = canonical

    return {
        "additions": additions,
        "replacements": replacements,
        "skipped": skipped,
        "conflicts": conflicts,
        "unmatched": unmatched,
    }


def _apply_completed_updates(raw_config: dict[str, Any], additions: list[dict[str, Any]], replacements: list[dict[str, Any]]) -> None:
    completed = list(raw_config.get("completed_group_matches") or [])
    replacement_lookup = {
        (str(item.get("group") or "").strip().upper(), _pair_key(str(item.get("team_a") or ""), str(item.get("team_b") or ""))): item
        for item in replacements
    }
    updated: list[dict[str, Any]] = []
    for item in completed:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        key = (
            str(item.get("group") or "").strip().upper(),
            _pair_key(str(item.get("team_a") or ""), str(item.get("team_b") or "")),
        )
        updated.append(replacement_lookup.pop(key, item))
    updated.extend(replacement_lookup.values())
    updated.extend(additions)
    raw_config["completed_group_matches"] = updated


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and merge completed group results into World Cup config")
    parser.add_argument("--config", type=Path, default=Path("config/worldcup_brazil.json"))
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="write completed_group_matches to the effective config")
    parser.add_argument("--replace", action="store_true", help="replace existing score conflicts")
    args = parser.parse_args(argv)

    config_path = _effective_config_path(args.config)
    if not config_path.exists():
        print(f"config não encontrado: {args.config}", file=sys.stderr)
        return 2
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    hydrate_canonical_configs(config, base_dir=config_path.parent)

    try:
        records = _read_json_or_csv(args.results)
        merge = _merge_results(config, records, replace=args.replace)
    except Exception as exc:
        print(f"erro ao processar resultados: {exc}", file=sys.stderr)
        return 2

    summary = {
        "dry_run": not args.apply,
        "requested_config": str(args.config),
        "effective_config": str(config_path),
        "results_input": str(args.results),
        "would_add": len(merge["additions"]),
        "would_replace": len(merge["replacements"]),
        "skipped_existing": len(merge["skipped"]),
        "conflicts": merge["conflicts"],
        "unmatched": merge["unmatched"],
        "additions": merge["additions"],
    }
    if merge["conflicts"] or merge["unmatched"]:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    if args.apply and (merge["additions"] or merge["replacements"]):
        _apply_completed_updates(raw_config, merge["additions"], merge["replacements"])
        _atomic_write_json(config_path, raw_config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
