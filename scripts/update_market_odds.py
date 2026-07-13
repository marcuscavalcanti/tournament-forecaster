#!/usr/bin/env python3
"""Validate and merge outright market odds into the World Cup config."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldcup_brazil.bracket import hydrate_canonical_configs
from worldcup_brazil.cli import _effective_config_path, load_env_file
from worldcup_brazil.pipeline import _devig_outright_title_probabilities
from tournament_forecaster.providers.security import redact_url


THE_ODDS_API_URL = (
    "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup_winner/odds/"
    "?regions=us,uk,eu&markets=outrights&oddsFormat=decimal&apiKey={THE_ODDS_API_KEY}"
)


class MarketOddsUnavailable(RuntimeError):
    """Expected best-effort failure while fetching or parsing external odds."""

ENGLISH_TEAM_ALIASES = {
    "mexico": "México",
    "south africa": "África do Sul",
    "korea republic": "Coreia do Sul",
    "south korea": "Coreia do Sul",
    "czechia": "Tchéquia",
    "czech republic": "Tchéquia",
    "canada": "Canadá",
    "bosnia and herzegovina": "Bósnia e Herzegovina",
    "bosnia-herzegovina": "Bósnia e Herzegovina",
    "qatar": "Catar",
    "switzerland": "Suíça",
    "brazil": "Brasil",
    "morocco": "Marrocos",
    "haiti": "Haiti",
    "scotland": "Escócia",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "usmnt": "Estados Unidos",
    "paraguay": "Paraguai",
    "australia": "Austrália",
    "turkey": "Turquia",
    "turkiye": "Turquia",
    "germany": "Alemanha",
    "curacao": "Curaçau",
    "curaçao": "Curaçau",
    "ivory coast": "Costa do Marfim",
    "cote d'ivoire": "Costa do Marfim",
    "côte d'ivoire": "Costa do Marfim",
    "ecuador": "Equador",
    "netherlands": "Holanda",
    "holland": "Holanda",
    "japan": "Japão",
    "sweden": "Suécia",
    "tunisia": "Tunísia",
    "belgium": "Bélgica",
    "egypt": "Egito",
    "iran": "Irã",
    "new zealand": "Nova Zelândia",
    "spain": "Espanha",
    "cape verde": "Cabo Verde",
    "saudi arabia": "Arábia Saudita",
    "uruguay": "Uruguai",
    "france": "França",
    "senegal": "Senegal",
    "iraq": "Iraque",
    "norway": "Noruega",
    "argentina": "Argentina",
    "algeria": "Argélia",
    "austria": "Áustria",
    "jordan": "Jordânia",
    "portugal": "Portugal",
    "dr congo": "RD Congo",
    "congo dr": "RD Congo",
    "democratic republic of congo": "RD Congo",
    "uzbekistan": "Uzbequistão",
    "colombia": "Colômbia",
    "england": "Inglaterra",
    "croatia": "Croácia",
    "ghana": "Gana",
    "panama": "Panamá",
}


def _normalize(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(stripped.casefold().replace("-", " ").split())


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        tmp_path = Path(handle.name)
    tmp_path.replace(path)


def _team_aliases(config: dict[str, Any]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    groups_config = config.get("groups_config") if isinstance(config.get("groups_config"), dict) else {}
    groups = groups_config.get("groups") if isinstance(groups_config.get("groups"), dict) else {}
    known_names = {
        str(team.get("name") or "").strip()
        for teams in groups.values()
        for team in (teams if isinstance(teams, list) else [])
        if isinstance(team, dict) and str(team.get("name") or "").strip()
    }
    for teams in groups.values():
        for team in teams if isinstance(teams, list) else []:
            if not isinstance(team, dict):
                continue
            name = str(team.get("name") or "").strip()
            code = str(team.get("code") or "").strip()
            if name:
                aliases[_normalize(name)] = name
            if code and name:
                aliases[_normalize(code)] = name
    for english, local in ENGLISH_TEAM_ALIASES.items():
        if local in known_names:
            aliases[_normalize(english)] = local
    return aliases


def _canonical_team(raw_name: Any, aliases: dict[str, str]) -> str | None:
    return aliases.get(_normalize(raw_name))


def _read_json_payload(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _odds_api_text_from_url(url: str) -> tuple[str, str] | None:
    api_key = os.environ.get("THE_ODDS_API_KEY", "").strip()
    if "{THE_ODDS_API_KEY}" in url:
        if not api_key:
            return None
        url = url.replace("{THE_ODDS_API_KEY}", api_key)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "tournament-forecaster/0.1 (+https://github.com/marcuscavalcanti/tournament-forecaster)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return url, response.read().decode("utf-8", errors="replace")


def _events_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("events", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    return []


def _market_key(raw_key: Any) -> str:
    return _normalize(raw_key).replace(" ", "_")


def _entries_from_the_odds_api_payload(payload: Any, config: dict[str, Any], *, source_url: str) -> list[dict[str, Any]]:
    aliases = _team_aliases(config)
    entries: list[dict[str, Any]] = []
    for event in _events_from_payload(payload):
        for bookmaker in event.get("bookmakers") or []:
            if not isinstance(bookmaker, dict):
                continue
            book = str(bookmaker.get("title") or bookmaker.get("key") or bookmaker.get("bookmaker") or "").strip()
            if not book:
                continue
            for market in bookmaker.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                key = _market_key(market.get("key") or market.get("market") or "")
                if key not in {"outrights", "winner", "futures"}:
                    continue
                for outcome in market.get("outcomes") or []:
                    if not isinstance(outcome, dict):
                        continue
                    team = _canonical_team(outcome.get("name") or outcome.get("team"), aliases)
                    if not team:
                        continue
                    try:
                        decimal_odds = float(outcome.get("price") or outcome.get("decimal_odds") or outcome.get("odds"))
                    except (TypeError, ValueError):
                        continue
                    if decimal_odds <= 1.0:
                        continue
                    entries.append(
                        {
                            "team": team,
                            "decimal_odds": decimal_odds,
                            "bookmaker": book,
                            "source_url": source_url,
                        }
                    )
    entries.sort(key=lambda item: (str(item["bookmaker"]), str(item["team"])))
    return entries


def _accepted_devig_books(entries: list[dict[str, Any]], config: dict[str, Any]) -> set[str]:
    _, evidence = _devig_outright_title_probabilities(
        entries,
        team_name=str(config.get("brazil_team_name") or "Brasil"),
        min_overround=float(config.get("market_outright_min_overround", 1.02)),
        max_overround=float(config.get("market_outright_max_overround", 1.40)),
    )
    accepted: set[str] = set()
    for item in evidence:
        source = str(item.get("source") or "")
        if source.startswith("odds "):
            accepted.add(source.removeprefix("odds "))
    return accepted


def _filter_valid_devig_entries(entries: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    accepted_books = _accepted_devig_books(entries, config)
    return [entry for entry in entries if str(entry.get("bookmaker") or "") in accepted_books]


def _load_entries_from_args(args: argparse.Namespace, config: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]], str]:
    if args.odds_json:
        payload = _read_json_payload(args.odds_json)
        entries = _entries_from_the_odds_api_payload(payload, config, source_url=str(args.odds_json))
        return "local", str(args.odds_json), entries, ""
    if args.from_the_odds_api:
        source_url = redact_url(args.odds_url)
        try:
            fetched = _odds_api_text_from_url(args.odds_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MarketOddsUnavailable(str(exc)) from exc
        if fetched is None:
            return "the-odds-api", source_url, [], "skipped_missing_api_key"
        effective_url, text = fetched
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MarketOddsUnavailable(str(exc)) from exc
        source_url = redact_url(effective_url)
        entries = _entries_from_the_odds_api_payload(payload, config, source_url=source_url)
        return "the-odds-api", source_url, entries, ""
    raise ValueError("informe --odds-json ou --from-the-odds-api")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and merge outright market odds into World Cup config")
    parser.add_argument("--config", type=Path, default=Path("config/worldcup_brazil.json"))
    parser.add_argument("--odds-json", type=Path, help="read a saved The Odds API JSON snapshot")
    parser.add_argument("--from-the-odds-api", action="store_true", help="fetch outright odds from The Odds API")
    parser.add_argument("--odds-url", default=THE_ODDS_API_URL, help="The Odds API URL")
    parser.add_argument("--env-file", type=Path, help="trusted dotenv file loaded only when provided")
    parser.add_argument(
        "--shell-env-file",
        type=Path,
        help="trusted shell-style env file loaded only when provided",
    )
    parser.add_argument("--apply", action="store_true", help="write market_outright_odds to the effective config")
    parser.add_argument("--require", action="store_true", help="fail if no valid de-vigged odds can be ingested")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    load_env_file(args.shell_env_file)
    config_path = _effective_config_path(args.config)
    if not config_path.exists():
        print(f"config não encontrado: {args.config}", file=sys.stderr)
        return 2
    write_config_path = args.config if args.apply and args.config != config_path else config_path
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    config = json.loads(config_path.read_text(encoding="utf-8"))
    hydrate_canonical_configs(config, base_dir=config_path.parent)

    try:
        source_kind, source_input, entries, status = _load_entries_from_args(args, config)
    except MarketOddsUnavailable as exc:
        print(f"erro ao processar odds: {exc}", file=sys.stderr)
        return 2 if args.require else 0
    valid_entries = _filter_valid_devig_entries(entries, config)

    summary = {
        "dry_run": not args.apply,
        "source_kind": source_kind,
        "requested_config": str(args.config),
        "effective_config": str(config_path),
        "write_config": str(write_config_path) if args.apply else None,
        "odds_input": source_input,
        "status": status or ("ok" if valid_entries else "no_valid_devig_odds"),
        "raw_entry_count": len(entries),
        "valid_entry_count": len(valid_entries),
        "accepted_bookmakers": sorted({str(entry["bookmaker"]) for entry in valid_entries}),
    }
    if not valid_entries:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr if args.require else sys.stdout)
        # Odds are best-effort enrichment: only --require (MARKET_ODDS_REQUIRED=1) is fatal. A
        # transient API failure or non-de-vigable field must never abort the daily post pipeline
        # -- the run continues silently with no market anchor, which is the intended contract.
        return 2 if args.require else 0

    if args.apply:
        raw_config["market_outright_odds"] = valid_entries
        _atomic_write_json(write_config_path, raw_config)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
