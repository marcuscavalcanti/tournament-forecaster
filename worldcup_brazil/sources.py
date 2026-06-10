from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from worldcup_brazil.source_memory import SourceMemory


DEFAULT_TIMEOUT_SECONDS = 25
DEFAULT_HTTP_MAX_ATTEMPTS = 3
DEFAULT_HTTP_BACKOFF_BASE_SECONDS = 1.0
DEFAULT_HTTP_BACKOFF_MAX_SECONDS = 12.0
RETRYABLE_HTTP_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class EvidenceSource:
    name: str
    category: str
    url: str
    confidence: float
    requires_env: str | None = None
    notes: str = ""


@dataclass(frozen=True)
class EvidenceResult:
    source: EvidenceSource
    ok: bool
    text: str
    error: str = ""


DEFAULT_SOURCES = [
    EvidenceSource(
        name="FIFA men's ranking",
        category="statistical",
        url="https://inside.fifa.com/fifa-world-ranking/men",
        confidence=0.78,
        notes="Ranking oficial; bom para força de seleção, fraco para odds de um jogo específico.",
    ),
    EvidenceSource(
        name="World Football Elo Ratings",
        category="statistical",
        url="https://www.eloratings.net/Brazil",
        confidence=0.82,
        notes="Rating histórico de seleções; útil para converter diferença de força em probabilidade.",
    ),
    EvidenceSource(
        name="Polymarket / prediction market search",
        category="statistical",
        url="https://gamma-api.polymarket.com/events?limit=25&search=World%20Cup%202026%20Brazil",
        confidence=0.72,
        notes="Mercado preditivo; liquidez e regras do mercado precisam ser checadas a cada run.",
    ),
    EvidenceSource(
        name="The Odds API World Cup outrights",
        category="statistical",
        url=(
            "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup_winner/odds/"
            "?regions=us,uk,eu&markets=outrights&oddsFormat=decimal&apiKey={THE_ODDS_API_KEY}"
        ),
        confidence=0.86,
        requires_env="THE_ODDS_API_KEY",
        notes="Sportsbooks; precisa de chave e remoção de vigorish antes de virar probabilidade.",
    ),
    EvidenceSource(
        name="FIFA match centre and news",
        category="qualitative",
        url="https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026",
        confidence=0.62,
        notes="Contexto de calendário, sede e notícias oficiais.",
    ),
    EvidenceSource(
        name="Transfermarkt Brazil squad and market values",
        category="qualitative",
        url="https://www.transfermarkt.com.br/brasil/kader/verein/3439",
        confidence=0.55,
        notes=(
            "Sinais de elenco, lesões, cortes e valor de mercado. Se houver atualização curta, comparar "
            "variação nominal em euros e variação percentual por jogador, sem deixar percentual isolado dominar."
        ),
    ),
    EvidenceSource(
        name="Sofascore Brazil player performance",
        category="qualitative",
        url="https://www.sofascore.com/football/team/brazil/4748",
        confidence=0.60,
        notes=(
            "Performance recente de jogadores, notas, forma individual e sinais de queda/subida "
            "que podem afetar o palpite sem depender de narrativa."
        ),
    ),
    EvidenceSource(
        name="FIFA refereeing and disciplinary context",
        category="qualitative",
        url="https://inside.fifa.com/refereeing",
        confidence=0.52,
        notes=(
            "Contexto de arbitragem, VAR, disciplina, cartões e possíveis distorções de resultado; "
            "útil quando o jogo anterior teve vencedor ou perdedor pouco justo."
        ),
    ),
]


def _format_url(source: EvidenceSource) -> str:
    url = source.url
    for key, value in os.environ.items():
        placeholder = "{" + key + "}"
        if placeholder in url:
            url = url.replace(placeholder, value)
    return url


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _http_max_attempts() -> int:
    return max(1, _env_int("HTTP_MAX_ATTEMPTS") or DEFAULT_HTTP_MAX_ATTEMPTS)


def _http_backoff_base_seconds() -> float:
    value = os.environ.get("HTTP_BACKOFF_BASE_SECONDS")
    if not value:
        return DEFAULT_HTTP_BACKOFF_BASE_SECONDS
    try:
        return max(0.0, float(value))
    except ValueError:
        return DEFAULT_HTTP_BACKOFF_BASE_SECONDS


def _http_backoff_max_seconds() -> float:
    value = os.environ.get("HTTP_BACKOFF_MAX_SECONDS")
    if not value:
        return DEFAULT_HTTP_BACKOFF_MAX_SECONDS
    try:
        return max(0.0, float(value))
    except ValueError:
        return DEFAULT_HTTP_BACKOFF_MAX_SECONDS


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    value = headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def _retry_delay_seconds(attempt_index: int, exc: urllib.error.HTTPError) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after, _http_backoff_max_seconds())
    base = _http_backoff_base_seconds()
    exponential = base * (2 ** max(0, attempt_index - 1))
    jitter = random.uniform(0.0, min(base, 1.0)) if base > 0 else 0.0
    return min(exponential + jitter, _http_backoff_max_seconds())


def _is_retryable_http_error(exc: urllib.error.HTTPError) -> bool:
    return int(getattr(exc, "code", 0) or 0) in RETRYABLE_HTTP_STATUS_CODES


def _open_url_with_retries(request: urllib.request.Request, *, timeout: int) -> Any:
    attempts = _http_max_attempts()
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if attempt >= attempts or not _is_retryable_http_error(exc):
                raise
            time.sleep(_retry_delay_seconds(attempt, exc))
    raise RuntimeError("unreachable retry loop")


def _strip_text(payload: bytes, content_type: str) -> str:
    text = payload.decode("utf-8", errors="replace")
    if "json" in content_type:
        try:
            return json.dumps(json.loads(text), ensure_ascii=False)[:8000]
        except json.JSONDecodeError:
            return text[:8000]
    text = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", text, flags=re.I)
    text = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()[:8000]


def fetch_source(source: EvidenceSource, *, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> EvidenceResult:
    if source.requires_env and not os.environ.get(source.requires_env):
        return EvidenceResult(
            source=source,
            ok=False,
            text="",
            error=f"missing environment variable {source.requires_env}",
        )

    url = _format_url(source)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "worldcup2026-brazil-radar/0.1 (+https://github.com/marcuscavalcanti/worldcup2026)"
        },
    )
    try:
        with _open_url_with_retries(request, timeout=timeout) as response:
            content_type = response.headers.get("Content-Type", "")
            text = _strip_text(response.read(2_000_000), content_type)
            return EvidenceResult(source=source, ok=True, text=text)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return EvidenceResult(source=source, ok=False, text="", error=str(exc))


async def fetch_sources_concurrently(
    sources: list[EvidenceSource],
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[EvidenceResult]:
    limit = max(1, _env_int("SOURCE_BULKHEAD_PER_HOST") or _env_int("SOURCE_BULKHEAD_DEFAULT") or 3)
    semaphores: dict[str, asyncio.Semaphore] = {}

    async def fetch_with_bulkhead(source: EvidenceSource) -> EvidenceResult:
        host = urllib.parse.urlparse(_format_url(source)).netloc or source.name
        semaphore = semaphores.setdefault(host, asyncio.Semaphore(limit))
        async with semaphore:
            return await asyncio.to_thread(fetch_source, source, timeout=timeout)

    tasks = [fetch_with_bulkhead(source) for source in sources]
    return list(await asyncio.gather(*tasks))


def load_sources_from_config(config: dict[str, Any]) -> list[EvidenceSource]:
    configured = config.get("sources")
    if not configured:
        return DEFAULT_SOURCES

    sources = []
    for item in configured:
        sources.append(
            EvidenceSource(
                name=item["name"],
                category=item.get("category", "statistical"),
                url=item["url"],
                confidence=float(item.get("confidence", 0.5)),
                requires_env=item.get("requires_env"),
                notes=item.get("notes", ""),
            )
        )
    return sources


def order_sources_with_memory(sources: list[EvidenceSource], memory: SourceMemory) -> list[EvidenceSource]:
    ranked_names = memory.ranked_sources([source.name for source in sources])
    by_name = {source.name: source for source in sources}
    return [by_name[name] for name in ranked_names if name in by_name]
