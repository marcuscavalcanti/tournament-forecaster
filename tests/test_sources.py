import io
import urllib.error

from worldcup_brazil.pipeline import _filter_non_opta_sources, _is_opta_source
from worldcup_brazil.sources import DEFAULT_SOURCES, EvidenceSource, fetch_source, fetch_sources_concurrently


def test_default_sources_include_player_performance_and_refereeing_context() -> None:
    names = [source.name for source in DEFAULT_SOURCES]

    assert "Sofascore Brazil player performance" in names
    assert "FIFA refereeing and disciplinary context" in names
    assert "Transfermarkt Brazil squad and market values" in names


def test_filter_non_opta_sources_excludes_opta_from_my_model() -> None:
    sources = [
        EvidenceSource(
            name="Opta World Cup projection",
            category="statistical",
            url="https://theanalyst.com/opta/world-cup",
            confidence=0.9,
        ),
        EvidenceSource(
            name="World Football Elo Ratings",
            category="statistical",
            url="https://www.eloratings.net/Brazil",
            confidence=0.8,
        ),
    ]

    filtered = _filter_non_opta_sources(sources)

    assert _is_opta_source(sources[0])
    assert [source.name for source in filtered] == ["World Football Elo Ratings"]


def test_fetch_source_retries_http_503_before_returning_ok(monkeypatch) -> None:
    calls = {"count": 0}
    sleeps = []
    monkeypatch.delenv("HTTP_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_BASE_SECONDS", raising=False)
    monkeypatch.delenv("HTTP_BACKOFF_MAX_SECONDS", raising=False)

    class Headers:
        def get(self, key, default=""):
            return "application/json"

    class Response:
        headers = Headers()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, _limit):
            return b'{"source": "ok"}'

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                url="https://source.example.test",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=io.BytesIO(b"unavailable"),
            )
        return Response()

    monkeypatch.setattr("worldcup_brazil.sources.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("worldcup_brazil.sources.time.sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr("worldcup_brazil.sources.random.uniform", lambda _low, _high: 0.0)

    result = fetch_source(
        EvidenceSource(
            name="Retryable source",
            category="statistical",
            url="https://source.example.test",
            confidence=0.8,
        ),
        timeout=3,
    )

    assert result.ok is True
    assert result.text == '{"source": "ok"}'
    assert calls["count"] == 2
    assert sleeps == [1.0]


def test_fetch_sources_concurrently_uses_source_bulkhead_per_host(monkeypatch) -> None:
    import asyncio

    running_by_host = {}
    max_running_by_host = {}
    order = []

    async def fake_to_thread(func, source, *, timeout):
        host = source.url.split("/")[2]
        running_by_host[host] = running_by_host.get(host, 0) + 1
        max_running_by_host[host] = max(max_running_by_host.get(host, 0), running_by_host[host])
        order.append(("start", source.name))
        await asyncio.sleep(0)
        running_by_host[host] -= 1
        order.append(("finish", source.name))
        return __import__("worldcup_brazil.sources").sources.EvidenceResult(source=source, ok=True, text="ok")

    monkeypatch.setattr("worldcup_brazil.sources.asyncio.to_thread", fake_to_thread)
    monkeypatch.setenv("SOURCE_BULKHEAD_PER_HOST", "1")
    sources = [
        EvidenceSource(name="A1", category="statistical", url="https://a.example/1", confidence=0.7),
        EvidenceSource(name="A2", category="statistical", url="https://a.example/2", confidence=0.7),
        EvidenceSource(name="B1", category="statistical", url="https://b.example/1", confidence=0.7),
    ]

    results = asyncio.run(fetch_sources_concurrently(sources, timeout=3))

    assert [result.source.name for result in results] == ["A1", "A2", "B1"]
    assert max_running_by_host["a.example"] == 1
    assert max_running_by_host["b.example"] == 1
    assert ("start", "B1") in order[:2]


def test_sources_open_url_retries_transient_urlerror(monkeypatch) -> None:
    """Espelho de agents.py (auditoria 11/jun): blip de DNS/TLS na busca de fontes
    era single-shot e derrubava a coleta da rodada sem usar as 3 tentativas."""
    import urllib.error

    from worldcup_brazil import sources

    attempts = {"count": 0}

    def fake_urlopen(request, timeout):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise urllib.error.URLError("dns blip")
        return "RESPONSE"

    monkeypatch.setenv("HTTP_MAX_ATTEMPTS", "3")  # não herdar env do host
    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)

    assert sources._open_url_with_retries(object(), timeout=5) == "RESPONSE"
    assert attempts["count"] == 2


def test_sources_retry_after_cap_mirrors_agents(monkeypatch) -> None:
    """O clone de retry em sources.py precisa do MESMO contrato de Retry-After do
    agents.py — o pin só no original deixava o espelho regredir sem alarme."""
    import urllib.error

    from worldcup_brazil import sources

    exc = urllib.error.HTTPError(
        url="https://api.example.com/x", code=429, msg="Too Many Requests",
        hdrs={"Retry-After": "30"}, fp=None,
    )

    monkeypatch.delenv("RETRY_AFTER_MAX_SECONDS", raising=False)
    assert sources._retry_delay_seconds(1, exc) == 30.0

    monkeypatch.setenv("RETRY_AFTER_MAX_SECONDS", "20")
    assert sources._retry_delay_seconds(1, exc) == 20.0
