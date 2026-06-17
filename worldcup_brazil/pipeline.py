from __future__ import annotations

import asyncio
import json
import math
import os
import random
import re
import threading
import unicodedata
import urllib.parse
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Callable

from worldcup_brazil.agents import agent_effort_profiles, call_agent, call_all_agents, load_agent_specs_from_config
from worldcup_brazil.bracket import (
    annotate_knockout_matches_with_bracket,
    brazil_bracket_path,
    brazil_bracket_path_candidates,
    hydrate_canonical_configs,
    invalid_configured_knockout_opponents,
)
from worldcup_brazil.consensus import (
    AgentOpinion,
    Consensus,
    DegenerateConsensusError,
    build_consensus,
)
from worldcup_brazil.meeting import (
    _counts_as_consensus_participant,
    _enough_peer_acceptances,
    build_meeting_turn,
    consensus_reached,
)
from worldcup_brazil.models import ReportBundle
from worldcup_brazil.monte_carlo import (
    monte_carlo_compact_summary,
    monte_carlo_path_gate_is_reliable,
    run_brazil_monte_carlo,
    widen_ci_for_monte_carlo_path_uncertainty,
)
from worldcup_brazil.probabilities import SourceSignal, blend_match_estimate
from worldcup_brazil.renderer import render_linkedin_post
from worldcup_brazil.source_memory import SourceMemory
from worldcup_brazil.sources import (
    EvidenceSource,
    EvidenceResult,
)
from worldcup_brazil.watchdog import RunWatchdog


DEFAULT_CUSTOM_HASHTAG = "#CopaComAchismo"
DEFAULT_USD_TO_BRL = 5.4
DEFAULT_MODEL_PRICING_USD_PER_MILLION_TOKENS = {
    "Opus 4.8": {"input": 15.0, "output": 75.0},
    "GPT 5.5": {"input": 10.0, "output": 30.0},
    "Perplexity Pro": {"input": 3.0, "output": 15.0},
    "DeepSeek V4 Pro": {"input": 2.0, "output": 8.0},
    "Gemini Pro": {"input": 1.25, "output": 10.0},
}
DEFAULT_MINIMUM_SOURCE_READY_AGENTS = 3
DEFAULT_SOURCE_PLANNING_REPAIR_ATTEMPTS = 2


def _effort_latency_instruction() -> str:
    return (
        "Use o nível de raciocínio configurado para este run e mantenha resposta rápida; responda rápido "
        "com JSON estrito, sem cadeia de pensamento nem texto fora do formato."
    )


def _agent_owned_fresh_search_contract() -> str:
    return (
        "Contrato único da sala: todos os modelos recebem as mesmas regras, objetivo e escopo. "
        "O mediador não faz busca externa, não escolhe fontes, não injeta evidência e não usa cache. "
        "Cada modelo decide suas próprias fontes, faz busca atualizada no próprio canal, nunca use cache, "
        "e registra source_urls/source_queries. Regra explícita antes da busca: dados da Opta não contam "
        "no Modelo Principal; não inclua Opta em source_urls/source_queries, não use Opta como benchmark, "
        "fonte, ranking, projeção ou âncora. Se fonte falhar, troque por equivalente fresca; não invente dado."
    )


def _is_negated_opta_mention(lowered: str, *, start: int, end: int) -> bool:
    prefix = lowered[max(0, start - 56) : start]
    suffix = lowered[end : min(len(lowered), end + 56)]
    if re.search(
        r"(?:nao|non|sem|exclu\w*|proibid\w*|vedad\w*|ignore\w*|remov\w*|"
        r"substitu\w*|dispens\w*|evit\w*|troc\w*|retir\w*)"
        r"(?:\W+\w+){0,7}\W*$",
        prefix,
    ):
        return True
    if prefix.endswith(("nao-", "non-", "sem-")):
        return True
    if re.search(
        r"^\W*(?:nao|non)\W+(?:conta\w*|vale\w*|entra\w*|inclua|incluir|considere|usar|use|ancora|ancorar)",
        suffix,
    ):
        return True
    if re.search(
        r"^\W*(?:(?:foi|foram|fica\w*|esta\w*)\W+)?(?:\w+\W+){0,2}?(?:proibid\w*|vedad\w*|excluid\w*|reservad\w*|fora\b)",
        suffix,
    ):
        return True
    return False


def _has_opta_marker(value: str) -> bool:
    lowered = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    if "statsperform" in lowered or "stats-perform" in lowered:
        return True
    for match in re.finditer(r"\bopta\b", lowered):
        if _is_negated_opta_mention(lowered, start=match.start(), end=match.end()):
            continue
        return True
    return False


def _has_opta_negation_or_compliance_language(text: str) -> bool:
    normalized = _normalize_text(str(text or ""))
    if not normalized:
        return False
    patterns = (
        r"\b(?:sem|nao|non)\W+(?:usar|uso|usei|utilizar|utilizei|inclu\w*|consider\w*|ancor\w*)"
        r"(?:\W+\w+){0,8}\W+opta",
        r"\b(?:substitu\w*|dispens\w*|evit\w*|troc\w*|retir\w*|exclu\w*|remov\w*)"
        r"(?:\W+\w+){0,8}\W+opta",
        r"\bopta\W+(?:nao\W+conta|fora|excluid\w*|removid\w*|proibid\w*)",
        r"\bopta(?:\W+\w+){0,5}\W+(?:proibid\w*|vedad\w*|fora|excluid\w*|removid\w*)",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _has_fixed_allocation_negation_or_compliance_language(text: str) -> bool:
    normalized = _normalize_text(str(text or ""))
    if not normalized:
        return False
    patterns = (
        r"\b(?:sem|nao|non)\W+(?:usar|uso|usei|utilizar|utilizei|aplicar|apliquei)"
        r"(?:\W+\w+){0,8}\W+(?:alocacao\W+fixa|percentual\W+fixo|peso\W+fixo|quanti/quali)",
        r"\b(?:substitu\w*|dispens\w*|evit\w*|troc\w*|retir\w*|exclu\w*|remov\w*)"
        r"(?:\W+\w+){0,8}\W+(?:alocacao\W+fixa|percentual\W+fixo|peso\W+fixo|quanti/quali)",
        r"\b(?:sem|nao\W+ha|nao\W+uso|nao\W+usei)\W+"
        r"(?:alocacao\W+fixa|percentual\W+fixo|peso\W+fixo|quanti/quali)",
        r"\b(?:alocacao\W+fixa|percentual\W+fixo|peso\W+fixo|quanti/quali)"
        r"\W+(?:proibid\w*|fora|excluid\w*|removid\w*)",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _is_opta_source(source: EvidenceSource) -> bool:
    return any(
        _has_opta_marker(value)
        for value in (source.name, source.url, source.notes)
        if value
    )


def _filter_non_opta_sources(sources: list[EvidenceSource]) -> list[EvidenceSource]:
    return [source for source in sources if not _is_opta_source(source)]


def _valid_http_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _category_from_url(url: str) -> str:
    lower = url.lower()
    if any(token in lower for token in ("injur", "squad", "team", "news", "fifa.com")):
        return "qualitative"
    return "statistical"


def _approx_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, round(len(text) / 4))


def _pricing_for_model(agent: str, config: dict[str, Any]) -> dict[str, float]:
    configured = config.get("model_pricing_usd_per_million_tokens", {})
    raw = configured.get(agent, DEFAULT_MODEL_PRICING_USD_PER_MILLION_TOKENS.get(agent, {}))
    return {
        "input": float(raw.get("input", 0.0)),
        "output": float(raw.get("output", 0.0)),
    }


def _new_token_cost_ledger(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "pricing_basis": (
            "estimativa local: tokens ~= caracteres/4; custo por milhão de tokens configurável em "
            "model_pricing_usd_per_million_tokens; fallback local sem resposta útil conta custo externo zero"
        ),
        "usd_to_brl": float(config.get("usd_to_brl", DEFAULT_USD_TO_BRL)),
        "total": {
            "calls": 0,
            "fallback_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cost_brl": 0.0,
        },
        "by_model": {},
    }


def _json_payload_from_text(text: str) -> dict[str, Any]:
    candidate = str(text or "")
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, flags=re.S)
    if fenced:
        candidate = fenced.group(1)
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_cost_entry(ledger: dict[str, Any], agent: str) -> dict[str, Any]:
    ledger.setdefault(
        "total",
        {
            "calls": 0,
            "fallback_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cost_brl": 0.0,
        },
    )
    ledger.setdefault("by_model", {})
    return ledger["by_model"].setdefault(
        agent,
        {
            "calls": 0,
            "fallback_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cost_brl": 0.0,
            "stages": {},
        },
    )


def _record_token_costs(
    ledger: dict[str, Any],
    *,
    config: dict[str, Any],
    prompt: str,
    opinions: list[Any],
    stage: str,
) -> None:
    prompt_tokens = _approx_tokens(prompt)
    usd_to_brl = float(ledger.get("usd_to_brl", DEFAULT_USD_TO_BRL))
    for opinion in opinions:
        agent = str(opinion.agent)
        completion_text = str(
            getattr(opinion, "raw_text", "")
            or getattr(opinion, "answer", "")
            or getattr(opinion, "summary", "")
        )
        completion_tokens = _approx_tokens(completion_text)
        used_fallback = bool(getattr(opinion, "used_fallback", False))
        pricing = _pricing_for_model(agent, config)
        cost_usd = 0.0
        if not used_fallback:
            cost_usd = (
                prompt_tokens * pricing["input"] / 1_000_000
                + completion_tokens * pricing["output"] / 1_000_000
            )
        cost_brl = cost_usd * usd_to_brl
        entry = _token_cost_entry(ledger, agent)
        stage_entry = entry["stages"].setdefault(
            stage,
            {
                "calls": 0,
                "fallback_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cost_usd": 0.0,
                "cost_brl": 0.0,
            },
        )
        for bucket in (entry, stage_entry, ledger["total"]):
            bucket["calls"] += 1
            bucket["fallback_calls"] += int(used_fallback)
            bucket["prompt_tokens"] += prompt_tokens
            bucket["completion_tokens"] += completion_tokens
            bucket["total_tokens"] += prompt_tokens + completion_tokens
            bucket["cost_usd"] = round(float(bucket["cost_usd"]) + cost_usd, 6)
            bucket["cost_brl"] = round(float(bucket["cost_brl"]) + cost_brl, 6)


def _slots_from_specs(agent_specs: list[Any]) -> list[str]:
    return [spec.slot for spec in agent_specs]


def _non_opta_source_items(opinion: Any) -> list[str]:
    return [
        str(item).strip()
        for item in [
            *getattr(opinion, "source_urls", []),
            *getattr(opinion, "source_queries", []),
        ]
        if str(item).strip() and not _has_opta_marker(str(item))
    ]


def _truncate_for_watchdog(value: str, limit: int = 420) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _source_planning_drop_candidates(config: dict[str, Any]) -> tuple[bool, set[str]]:
    require_source_plan = bool(config.get("require_agent_source_plan", True))
    drop_candidates = config.get("drop_fallback_only_agents")
    if drop_candidates is None:
        drop_candidates = ["*"] if require_source_plan else []
    candidates = {str(agent) for agent in drop_candidates}
    return "*" in candidates, candidates


def _has_fixed_quanti_quali_allocation(text: str) -> bool:
    normalized = _normalize_text(str(text or ""))
    method_markers = (
        "alocacao",
        "alocar",
        "peso",
        "pesar",
        "ponderacao",
        "ponderar",
        "mix",
        "proporcao",
        "razao",
        "quota",
        "divisao",
        "metodo",
        "metodologic",
        "regua",
    )
    quant_markers = ("quanti", "quantitativ", "estatistic", "numer", "dados")
    qual_markers = ("quali", "qualitativ", "contexto", "noticias", "lesoes", "arbitragem")
    odds_context_markers = (
        "odd",
        "odds",
        "cotacao",
        "cotacoes",
        "mercado",
        "sportsbook",
        "bookmaker",
        "casa",
        "casas",
        "aposta",
        "apostas",
        "fracionaria",
        "fracionarias",
        "fractional",
    )
    has_quant = any(marker in normalized for marker in quant_markers)
    has_qual = any(marker in normalized for marker in qual_markers)
    if not has_quant or not has_qual:
        return False
    slash_pair_matches = list(re.finditer(r"\b\d{1,3}\s*/\s*\d{1,3}\b", normalized))
    allocation_slash_pairs = []
    for match in slash_pair_matches:
        local = normalized[max(0, match.start() - 90) : min(len(normalized), match.end() + 90)]
        immediate = normalized[max(0, match.start() - 55) : min(len(normalized), match.end() + 55)]
        looks_like_allocation = (
            any(marker in immediate for marker in quant_markers)
            and any(marker in immediate for marker in qual_markers)
            and any(marker in immediate for marker in method_markers)
        )
        if any(marker in local for marker in odds_context_markers) and not looks_like_allocation:
            continue
        allocation_slash_pairs.append(match)
    fixed_pair = bool(allocation_slash_pairs)
    fixed_percent_pair = bool(re.search(r"\b\d{1,3}\s*%\D{0,90}\b\d{1,3}\s*%", normalized))
    if not fixed_pair and not fixed_percent_pair:
        return False
    has_method_marker = any(marker in normalized for marker in method_markers)
    direct_quant_qual_percent = bool(
        re.search(r"\b\d{1,3}\s*%\D{0,45}(quanti|quantitativ|estatistic|numer|dados)", normalized)
        and re.search(r"\b\d{1,3}\s*%\D{0,45}(quali|qualitativ|contexto|noticias|lesoes|arbitragem)", normalized)
    )
    slash_quant_qual = any(
        re.search(r"(quanti|quantitativ|quali|qualitativ|contexto)", normalized[match.end() : match.end() + 60])
        for match in allocation_slash_pairs
    )
    return has_method_marker or direct_quant_qual_percent or slash_quant_qual


def _opinion_operational_text(opinion: Any) -> str:
    if isinstance(opinion, str):
        return opinion
    return " ".join(
        str(item or "")
        for item in (
            getattr(opinion, "summary", ""),
            getattr(opinion, "opening_argument", ""),
            getattr(opinion, "question", ""),
            getattr(opinion, "answer", ""),
            getattr(opinion, "critique", ""),
            getattr(opinion, "adjustment", ""),
            getattr(opinion, "raw_text", ""),
        )
    )


def _external_search_failure_issue(opinion: Any) -> str | None:
    normalized = _normalize_text(_opinion_operational_text(opinion))
    if not normalized.strip():
        return None

    permission_markers = (
        "permissao nao concedida",
        "erro de permissao",
        "permission denied",
        "permission not granted",
        "not granted",
        "forbidden",
        "access denied",
    )
    search_channel_markers = (
        "websearch",
        "web search",
        "webfetch",
        "web fetch",
        "browser",
        "busca ao vivo",
        "busca externa",
        "fetch externo",
        "ferramenta de busca",
    )
    no_tool_phrases = (
        "nao ha ferramenta de busca",
        "nao ha ferramenta de busca externa",
        "nao tenho ferramenta de busca",
        "nao tenho acesso a ferramenta de busca",
        "sem ferramenta de busca",
        "sem ferramenta de busca externa",
        "sem ferramenta de fetch",
        "nao ha ferramenta de busca externa/fetch disponivel",
        "nao existe ferramenta de busca externa/fetch disponivel",
        "busca externa/fetch nao disponivel",
        "ferramenta de busca externa/fetch nao disponivel",
        "nao ha ferramenta de busca externa ou fetch disponivel",
        "ferramenta de busca externa ou fetch nao disponivel",
        "no external search",
        "no web search",
        "no websearch",
        "no browsing tool",
        "no browser tool",
        "no fetch tool",
        "without web access",
        "web access unavailable",
        "search tool unavailable",
        "fetch tool unavailable",
    )
    cannot_confirm_live_phrases = (
        "nao consigo confirmar paginas em tempo real",
        "nao posso confirmar paginas em tempo real",
        "nao consigo confirmar paginas",
        "nao posso confirmar paginas",
        "sem acesso para confirmar paginas em tempo real",
        "cannot confirm pages in real time",
        "cannot verify pages in real time",
        "cannot browse in real time",
    )

    has_permission_failure = any(marker in normalized for marker in permission_markers) and any(
        marker in normalized for marker in search_channel_markers
    )
    has_no_tool_failure = any(phrase in normalized for phrase in no_tool_phrases)
    has_live_confirmation_failure = any(phrase in normalized for phrase in cannot_confirm_live_phrases)
    has_provisional_prior_without_fetch = (
        "prior provisorio" in normalized
        and any(marker in normalized for marker in ("sem ferramenta", "nao ha ferramenta", "nao consigo confirmar"))
    )

    if (
        has_permission_failure
        or has_no_tool_failure
        or has_live_confirmation_failure
        or has_provisional_prior_without_fetch
    ):
        return "busca/fetch externo indisponível ou sem permissão; source_queries não provam busca executada"
    return None


def _validation_excerpt(text: str, markers: tuple[str, ...] = (), *, limit: int = 220) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    normalized = _normalize_text(raw)
    for marker in markers:
        marker_norm = _normalize_text(marker)
        if not marker_norm:
            continue
        index = normalized.find(marker_norm)
        if index >= 0:
            start = max(0, index - 70)
            end = min(len(raw), index + len(marker) + 110)
            return _truncate_for_watchdog(raw[start:end].strip(), limit=limit)
    return _truncate_for_watchdog(raw, limit=limit)


def _opinion_diagnostic_text(opinion: Any, source_items: list[str] | None = None) -> str:
    return " ".join(
        str(item or "")
        for item in (
            getattr(opinion, "summary", ""),
            getattr(opinion, "opening_argument", ""),
            getattr(opinion, "question", ""),
            getattr(opinion, "answer", ""),
            getattr(opinion, "critique", ""),
            getattr(opinion, "adjustment", ""),
            getattr(opinion, "proposed_next_question", ""),
            getattr(opinion, "leadership_rationale", ""),
            " ".join(source_items or []),
            " ".join(getattr(opinion, "source_urls", []) or []),
            " ".join(getattr(opinion, "source_queries", []) or []),
        )
    )


def _validation_issue(
    *,
    gate_name: str,
    matched_rule: str,
    offending_excerpt: str,
    field: str,
    severity: str,
    recoverability: str,
    repair_hint: str,
) -> dict[str, Any]:
    issue = {
        "gate_name": gate_name,
        "matched_rule": matched_rule,
        "offending_excerpt": _truncate_for_watchdog(offending_excerpt, limit=220),
        "field": field,
        "severity": severity,
        "recoverability": recoverability,
        "repair_hint": repair_hint,
    }
    policy = _reentry_policy_for_validation_issue(issue)
    issue["reentry_eligible"] = bool(policy["eligible"])
    issue["reentry_decision_reason"] = str(policy["decision_reason"])
    return issue


def _issue_text_from_reason(reason: str) -> str:
    normalized = _normalize_text(reason)
    if "source_urls/source_queries" in normalized:
        return "source_urls/source_queries não-Opta ausentes"
    if "benchmark reservado" in normalized or "opta" in normalized:
        return "benchmark reservado/Opta não pode entrar no Modelo Principal"
    if "alocacao fixa" in normalized or "quanti/quali" in normalized:
        return "alocação fixa quanti/quali proibida"
    if "busca/fetch externo indisponivel" in normalized or "sem permissao" in normalized:
        return "busca/fetch externo indisponível ou sem permissão"
    if "429" in normalized or "too many requests" in normalized or "quota" in normalized:
        return "quota/rate-limit impede reentrada automática"
    if "prepayment credits" in normalized or "comprar creditos" in normalized or "billing action required" in normalized:
        return "créditos Gemini/prepayment esgotados"
    if "adversario impossivel" in normalized or "cruzamento oficial" in normalized:
        return "adversário impossível pelo bracket oficial"
    if "resposta parcial" in normalized or "json parcial" in normalized or "sem campos auditaveis" in normalized:
        return "resposta parcial ou sem campos auditáveis"
    return reason or "violação de contrato operacional"


def _semantic_policy_recoverability(*, kind: str, diagnostic_text: str, semantic_policy_stage: str) -> str:
    """Semantic policy hits start repairable; only post-repair affirmative reuse becomes terminal.

    The initial matcher is intentionally conservative: Opta/fixed-allocation hits are language-level
    policies, and negation detection has already produced false positives. Structural gates such as
    bracket impossibility stay terminal elsewhere because they are machine-checkable.
    """
    stage = str(semantic_policy_stage or "initial").strip().lower()
    if stage != "post_repair":
        return "policy_suspected"
    if kind == "opta" and _has_opta_negation_or_compliance_language(diagnostic_text):
        return "policy_suspected"
    if kind == "fixed_allocation" and _has_fixed_allocation_negation_or_compliance_language(diagnostic_text):
        return "policy_suspected"
    return "policy"


def _validation_issue_from_reason(
    *,
    gate_name: str,
    reason: str,
    opinion: Any | None = None,
    source_items: list[str] | None = None,
    field: str = "summary",
    semantic_policy_stage: str = "initial",
) -> dict[str, Any]:
    normalized = _normalize_text(reason)
    diagnostic_text = _opinion_diagnostic_text(opinion, source_items) if opinion is not None else reason
    markers: tuple[str, ...] = ()
    matched_rule = "operational_contract"
    severity = "blocking"
    recoverability = "fatal"
    repair_hint = "Não reentrar automaticamente; requer correção manual ou nova rodada operacional."

    if _is_format_repairable_planning_reason(reason):
        matched_rule = "partial_or_unparseable_payload"
        recoverability = "format"
        markers = ("resposta parcial", "json parcial", "sem campos auditáveis", "sem campos auditaveis")
        repair_hint = "Retry curto: reenviar somente JSON completo e auditável, sem prosa fora do objeto."
    elif "prepayment credits" in normalized or "billing action required" in normalized or "comprar creditos" in normalized:
        matched_rule = "provider_billing_or_prepay_depleted"
        recoverability = "fatal"
        markers = ("prepayment credits", "billing action required", "comprar créditos", "comprar creditos")
        repair_hint = "Não reentrar neste run; comprar créditos/regularizar billing antes de tentar Gemini novamente."
    elif "429" in normalized or "too many requests" in normalized or "rate limit" in normalized or "quota" in normalized:
        matched_rule = "provider_rate_limit_or_quota"
        recoverability = "source"
        markers = ("429", "too many requests", "quota", "rate limit")
        repair_hint = "Não reentrar até cooldown/quota renovar; evitar probes repetidos na mesma janela."
    elif "timeout no planejamento" in normalized or "timed out" in normalized or "timeout" in normalized:
        matched_rule = "planning_timeout"
        recoverability = "source"
        markers = ("timeout", "timed out")
        repair_hint = "Pode reentrar se o probe trouxer fontes auditáveis; não confundir com 429/quota."
    elif "source_urls/source_queries" in normalized:
        matched_rule = "missing_auditable_sources"
        recoverability = "source"
        markers = ("source_urls", "source_queries")
        repair_hint = "Pode reentrar apenas se trouxer source_urls HTTP ou source_queries específicas executadas agora."
    elif "nao trouxe plano de fontes" in normalized or "não trouxe plano de fontes" in reason or "sem fonte auditavel" in normalized:
        matched_rule = "missing_auditable_sources"
        recoverability = "source"
        markers = ("plano de fontes", "fonte auditável", "fonte auditavel")
        repair_hint = "Pode reentrar apenas se trouxer source_urls HTTP ou source_queries específicas executadas agora."
    elif "benchmark reservado" in normalized or "opta" in normalized:
        matched_rule = "reserved_benchmark_opta"
        recoverability = _semantic_policy_recoverability(
            kind="opta",
            diagnostic_text=diagnostic_text,
            semantic_policy_stage=semantic_policy_stage,
        )
        markers = ("Opta", "opta", "benchmark reservado")
        repair_hint = (
            "Repair direcionado: reescrever sem mencionar Opta/benchmark reservado e confirmar fontes não reservadas."
            if recoverability == "policy_suspected"
            else "Não reentrar; remover Opta/benchmark reservado e trazer fontes não reservadas em novo planejamento."
        )
    elif "adversario impossivel" in normalized or "cruzamento oficial" in normalized:
        matched_rule = "impossible_bracket_opponent"
        recoverability = "bracket"
        markers = ("adversário impossível", "adversario impossivel", "cruzamento oficial")
        repair_hint = "Não reentrar; respeitar adversários possíveis pelo bracket oficial configurado."
    elif "alocacao fixa" in normalized or "quanti/quali" in normalized:
        matched_rule = "fixed_quantitative_qualitative_allocation"
        recoverability = _semantic_policy_recoverability(
            kind="fixed_allocation",
            diagnostic_text=diagnostic_text,
            semantic_policy_stage=semantic_policy_stage,
        )
        markers = ("70/30", "60/40", "quanti", "qualit")
        repair_hint = (
            "Repair direcionado: reescrever sem citar fórmula/alocação fixa e confirmar que o peso quanti/quali é livre."
            if recoverability == "policy_suspected"
            else "Não reentrar; modelos devem decidir livremente o peso entre dados quantitativos e qualitativos."
        )
    elif "busca/fetch externo indisponivel" in normalized or "sem permissao" in normalized or "permission" in normalized:
        matched_rule = "external_fetch_unavailable"
        recoverability = "source"
        markers = ("busca/fetch", "sem permissão", "sem permissao", "permission")
        repair_hint = "Não reentrar enquanto a ferramenta/permissão de busca estiver indisponível."

    excerpt = _validation_excerpt(diagnostic_text or _issue_text_from_reason(reason), markers)
    if not excerpt:
        excerpt = _issue_text_from_reason(reason)
    return _validation_issue(
        gate_name=gate_name,
        matched_rule=matched_rule,
        offending_excerpt=excerpt,
        field=field,
        severity=severity,
        recoverability=recoverability,
        repair_hint=repair_hint,
    )


def _reentry_policy_for_validation_issue(issue: dict[str, Any] | None) -> dict[str, Any]:
    if not issue:
        return {
            "eligible": True,
            "decision_reason": "sem issue estruturada; mantendo compatibilidade com política antiga",
        }
    recoverability = str(issue.get("recoverability", "")).strip().lower()
    matched_rule = str(issue.get("matched_rule", "")).strip().lower()
    if recoverability == "format":
        return {
            "eligible": True,
            "decision_reason": "erro de formato é recuperável por retry curto",
        }
    if matched_rule in {"missing_auditable_sources", "planning_timeout", "legacy_reentry_candidate", "consecutive_invalid_votes"}:
        return {
            "eligible": True,
            "decision_reason": "falha recuperável pode reentrar somente se o probe trouxer fonte auditável",
        }
    if matched_rule in {"provider_rate_limit_or_quota", "provider_billing_or_prepay_depleted"}:
        return {
            "eligible": False,
            "decision_reason": "429/quota/billing exige cooldown ou ação externa; probe não resolve",
        }
    if matched_rule == "external_fetch_unavailable":
        return {
            "eligible": False,
            "decision_reason": "ferramenta/permissão de busca indisponível; probe repetiria a mesma falha",
        }
    if recoverability == "policy_suspected":
        return {
            "eligible": True,
            "decision_reason": "violação semântica ambígua/negada ganha repair direcionado antes de remoção terminal",
        }
    if recoverability in {"policy", "bracket", "fatal"}:
        return {
            "eligible": False,
            "decision_reason": "violação real de contrato não ganha reentry automática",
        }
    return {
        "eligible": False,
        "decision_reason": f"recoverability={recoverability or 'desconhecida'} sem política de reentry segura",
    }


def _primary_validation_issue(issues: list[dict[str, Any]] | None, reason: str = "") -> dict[str, Any]:
    if issues:
        return dict(issues[0])
    if _normalize_text(reason) in {"", "removido no planejamento"}:
        return _validation_issue(
            gate_name="legacy_reason",
            matched_rule="legacy_reentry_candidate",
            offending_excerpt=reason or "motivo legado sem issue estruturada",
            field="summary",
            severity="blocking",
            recoverability="source",
            repair_hint="Pode reentrar apenas se o probe trouxer fonte auditável.",
        )
    return _validation_issue_from_reason(gate_name="legacy_reason", reason=reason)


def _reentry_timeout_for_issue(config: dict[str, Any], issue: dict[str, Any] | None, default_timeout: int) -> int:
    if str((issue or {}).get("recoverability", "")).lower() == "format":
        return int(config.get("agent_reentry_format_timeout_seconds", min(60, int(default_timeout))))
    return int(default_timeout)


def _source_planning_relevance_issue(opinion: Any, source_items: list[str]) -> str | None:
    external_search_issue = _external_search_failure_issue(opinion)
    if external_search_issue:
        return external_search_issue
    opinion_text = _normalize_text(
        " ".join(
            [
                str(getattr(opinion, "summary", "")),
                str(getattr(opinion, "opening_argument", "")),
                str(getattr(opinion, "answer", "")),
            ]
        )
    )
    combined = _normalize_text(
        " ".join(
            [
                str(getattr(opinion, "summary", "")),
                str(getattr(opinion, "opening_argument", "")),
                str(getattr(opinion, "answer", "")),
                " ".join(source_items),
            ]
        )
    )
    source_text = _normalize_text(" ".join(source_items))
    off_topic_markers = (
        "fonte tipografica",
        "fontes tipograficas",
        "fonte personalizada",
        "fontes personalizadas",
        "jersey font",
        "jersey-font",
        "camisa",
        "camisas",
        "footyheadlines",
        "fontsport",
    )
    if any(marker in opinion_text for marker in off_topic_markers):
        return "fontes fora do escopo estatístico/qualitativo do futebol competitivo"
    if _has_opta_marker(opinion_text):
        return "referência a benchmark reservado no planejamento sem Opta"
    if _has_fixed_quanti_quali_allocation(opinion_text):
        return "alocação fixa quanti/quali proibida; modelos devem decidir sem percentual fixo"
    relevant_source_markers = (
        "odds",
        "sportsbook",
        "bet365",
        "oddsportal",
        "polymarket",
        "prediction",
        "probabil",
        "elo",
        "rating",
        "ranking",
        "fifa-world-ranking",
        "sofasc",
        "transfermarkt",
        "lesao",
        "injur",
        "corte",
        "arbitr",
        "var",
        "cartao",
        "suspens",
        "world-cup-predictions",
        "expected",
        " xg",
        "fgv",
    )
    if any(marker in combined for marker in off_topic_markers) and not any(
        marker in source_text for marker in relevant_source_markers
    ):
        return "fontes fora do escopo estatístico/qualitativo do futebol competitivo"
    return None


def _source_planning_readiness_report(planning_opinions: list[Any], config: dict[str, Any]) -> dict[str, Any]:
    require_source_plan = bool(config.get("require_agent_source_plan", True))
    drop_all_candidates, candidates = _source_planning_drop_candidates(config)
    semantic_policy_stage = str(config.get("_source_planning_semantic_policy_stage", "initial") or "initial")
    entries: list[dict[str, Any]] = []
    active_agents: list[str] = []
    removed_agents: list[dict[str, Any]] = []

    for opinion in planning_opinions:
        agent = str(getattr(opinion, "agent", "")).strip() or "Modelo sem nome"
        source_items = _non_opta_source_items(opinion)
        is_candidate = drop_all_candidates or agent in candidates
        used_fallback = bool(getattr(opinion, "used_fallback", False))
        lacks_sources = require_source_plan and not source_items
        external_search_issue = _external_search_failure_issue(opinion)
        relevance_issue = external_search_issue or (
            _source_planning_relevance_issue(opinion, source_items) if source_items else None
        )
        removed = (is_candidate and (used_fallback or lacks_sources)) or bool(relevance_issue)
        if used_fallback:
            reason = "fallback operacional: " + _truncate_for_watchdog(getattr(opinion, "summary", ""))
        elif lacks_sources:
            reason = "sem source_urls/source_queries não-Opta"
        elif relevance_issue:
            reason = relevance_issue
        else:
            reason = "plano de fontes próprio e verificável"
        validation_issues = []
        if removed:
            validation_issues = [
                _validation_issue_from_reason(
                    gate_name="source_planning_readiness",
                    reason=reason,
                    opinion=opinion,
                    source_items=source_items,
                    field=(
                        "source_urls/source_queries"
                        if "source_urls/source_queries" in _normalize_text(reason)
                        else "summary"
                    ),
                    semantic_policy_stage=semantic_policy_stage,
                )
            ]
        primary_issue = validation_issues[0] if validation_issues else {}

        entry = {
            "agent": agent,
            "ready": not removed,
            "reason": reason,
            "validation_issues": validation_issues,
            "gate_name": primary_issue.get("gate_name", ""),
            "matched_rule": primary_issue.get("matched_rule", ""),
            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
            "field": primary_issue.get("field", ""),
            "severity": primary_issue.get("severity", ""),
            "recoverability": primary_issue.get("recoverability", ""),
            "repair_hint": primary_issue.get("repair_hint", ""),
            "reentry_eligible": primary_issue.get("reentry_eligible", False) if removed else False,
            "reentry_decision_reason": primary_issue.get("reentry_decision_reason", ""),
            "used_fallback": used_fallback,
            "source_url_count": len([url for url in getattr(opinion, "source_urls", []) if not _has_opta_marker(url)]),
            "source_query_count": len(
                [query for query in getattr(opinion, "source_queries", []) if not _has_opta_marker(query)]
            ),
            "source_items": source_items[:6],
            "summary": _truncate_for_watchdog(getattr(opinion, "summary", "")),
        }
        entries.append(entry)
        if removed:
            removed_agents.append(entry)
        else:
            active_agents.append(agent)

    required_count = int(config.get("minimum_source_ready_agents", DEFAULT_MINIMUM_SOURCE_READY_AGENTS))
    ready_count = len(active_agents)
    return {
        "required_count": required_count,
        "ready_count": ready_count,
        "quorum_met": ready_count >= required_count,
        "active_agents": active_agents,
        "removed_agents": removed_agents,
        "agents": entries,
    }


def _source_planning_quorum_error(report: dict[str, Any]) -> str:
    active = ", ".join(report.get("active_agents", [])) or "nenhum"
    removed_bits = [
        f"{entry['agent']} ({entry['reason']})"
        for entry in report.get("removed_agents", [])
    ]
    removed = "; ".join(removed_bits[:6]) or "nenhum removido"
    return (
        "Quórum insuficiente para debriefing: "
        f"{report.get('ready_count', 0)} modelo(s) trouxeram plano de fontes próprio e verificável; "
        f"mínimo exigido: {report.get('required_count', 0)}. "
        f"Ativos: {active}. Removidos: {removed}."
    )


def _agent_slots_for_watchdog(config: dict[str, Any]) -> list[str]:
    configured = config.get("agents")
    if isinstance(configured, list) and configured:
        return [
            str(item.get("slot", "")).strip()
            for item in configured
            if isinstance(item, dict) and str(item.get("slot", "")).strip()
        ]
    return list(DEFAULT_MODEL_PRICING_USD_PER_MILLION_TOKENS)


def _agent_source_planning_watchdog_extra(config: dict[str, Any]) -> dict[str, Any]:
    group_matches = _default_group_matches(config)
    knockout_matches = _default_knockout_matches(config)
    monte_carlo_summary = monte_carlo_compact_summary(config.get("_monte_carlo_result", {"enabled": False}))
    return {
        "contract": {
            "same_contract_for_all_models": True,
            "mediator_external_fetch": False,
            "mediator_source_selection": False,
            "mediator_cache": False,
            "agent_owned_fresh_search": True,
            "agent_cache_allowed": False,
            "agent_must_choose_sources": True,
            "agent_must_cover_brazil_and_opponents": True,
            "excluded_model_principal_sources": ["Opta"],
            "opta_exclusion_timing": "antes_da_busca",
            "opta_rule": (
                "dados da Opta não contam no Modelo Principal; modelos não devem incluir Opta "
                "em source_urls/source_queries nem usar como benchmark, fonte, ranking, projeção ou âncora"
            ),
            "required_agent_outputs": [
                "self_identification",
                "title_pct",
                "summary",
                "opening_argument",
                "critique",
                "adjustment",
                "source_urls",
                "source_queries",
                "scenario_probabilities",
                "team_context_signals",
            ],
            "team_context_signal_families": [
                "bets/prediction markets",
                "ratings/Elo/FIFA",
                "Sofascore/performance de jogadores",
                "lesões/cortes/notícias recentes",
                "amistosos recentes",
                "arbitragem/VAR/cartões",
                "opinião de imprensa especializada",
            ],
            "team_context_signal_rule": (
                "cada sinal precisa de team, category, rating_delta ou probability_delta_pct, "
                "confidence, rationale e source_url/source_query; sem fonte ou delta numérico, não altera o Monte Carlo"
            ),
            "source_requirement": "source_urls ou source_queries auditáveis escolhidas pelo próprio modelo neste run",
        },
        "operational_knobs": {
            "minimum_source_ready_agents": int(
                config.get("minimum_source_ready_agents", DEFAULT_MINIMUM_SOURCE_READY_AGENTS)
            ),
            "source_planning_repair_attempts": int(
                config.get("source_planning_repair_attempts", DEFAULT_SOURCE_PLANNING_REPAIR_ATTEMPTS)
            ),
            "repair_format_removals_with_quorum": bool(config.get("repair_format_removals_with_quorum", True)),
            "source_planning_format_repair_timeout_seconds": int(
                config.get(
                    "source_planning_format_repair_timeout_seconds",
                    min(90, int(config.get("agent_timeout_seconds", 90))),
                )
            ),
            "repair_reentry_eligible_removals_before_meeting": bool(
                config.get(
                    "repair_reentry_eligible_removals_before_meeting",
                    config.get("repair_reentry_eligible_removals_at_quorum_floor", True),
                )
            ),
            "source_planning_floor_repair_timeout_seconds": int(
                config.get("source_planning_floor_repair_timeout_seconds", config.get("agent_timeout_seconds", 90))
            ),
            "blind_peer_review_enabled": bool(config.get("blind_peer_review_enabled", False)),
            "blind_peer_review_shadow_only": bool(config.get("blind_peer_review_shadow_only", True)),
            "blind_peer_review_on_consensus_exit": bool(config.get("blind_peer_review_on_consensus_exit", True)),
            "blind_peer_review_timeout_seconds": int(config.get("blind_peer_review_timeout_seconds", 90)),
            "blind_peer_review_acceptance_threshold": float(config.get("blind_peer_review_acceptance_threshold", 0.72)),
            "blind_peer_review_max_self_preference_leakage": float(
                config.get("blind_peer_review_max_self_preference_leakage", 0.20)
            ),
            "numeric_chairman_enabled": bool(config.get("numeric_chairman_enabled", True)),
            "llm_council_fast_path_enabled": bool(config.get("llm_council_fast_path_enabled", False)),
            "llm_council_fast_path_shadow_only": bool(config.get("llm_council_fast_path_shadow_only", True)),
            "llm_council_fast_path_min_participants": int(
                config.get(
                    "llm_council_fast_path_min_participants",
                    config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3)),
                )
            ),
            "meeting_min_participants": int(
                config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3))
            ),
            "meeting_quorum_rule": "maioria simples dos participantes ativos da sala",
            "meeting_response_repair_attempts": int(config.get("meeting_response_repair_attempts", 1)),
            "max_agent_title_shift_pct": float(config.get("max_agent_title_shift_pct", 5.0)),
            "max_agent_title_shift_with_sources_pct": float(
                config.get("max_agent_title_shift_with_sources_pct", 8.0)
            ),
            "max_agent_title_pct_abs_cap": float(config.get("max_agent_title_pct_abs_cap", 25.0)),
            "meeting_require_full_path_coverage": bool(config.get("meeting_require_full_path_coverage", True)),
            "parallel_opponent_debriefing_enabled": bool(config.get("parallel_opponent_debriefing_enabled", False)),
            "agent_timeout_seconds": int(config.get("agent_timeout_seconds", 90)),
            "agent_reentry_probe_enabled": bool(config.get("agent_reentry_probe_enabled", False)),
            "agent_reentry_probe_timeout_seconds": int(config.get("agent_reentry_probe_timeout_seconds", 180)),
            "require_agent_source_plan": bool(config.get("require_agent_source_plan", True)),
            "require_auditable_source_urls_for_meeting_votes": bool(
                config.get("require_auditable_source_urls_for_meeting_votes", True)
            ),
            "enforce_bracket_constraints": bool(config.get("enforce_bracket_constraints", True)),
            "bracket_uncertainty_ci_widening": bool(config.get("bracket_uncertainty_ci_widening", True)),
        },
        "scope": {
            "group_name": config.get("group_name", "Grupo não configurado"),
            "brazil_group": config.get("brazil_group"),
            "brazil_expected_group_position": config.get("brazil_expected_group_position"),
            "group_matches_count": len(group_matches),
            "knockout_matches_count": len(knockout_matches),
            "group_opponents": [str(match.get("opponent", "")) for match in group_matches],
            "knockout_scenarios": [
                {
                    "phase": match.get("phase", "Mata-mata"),
                    "opponent": match.get("opponent", ""),
                    "most_likely": bool(match.get("most_likely", False)),
                    "bracket_match_id": match.get("bracket_match_id"),
                    "bracket_brazil_slot": match.get("bracket_brazil_slot"),
                    "bracket_opponent_slots": match.get("bracket_opponent_slots"),
                    "allowed_opponents": match.get("allowed_opponents"),
                }
                for match in knockout_matches
            ],
            "bracket_path": brazil_bracket_path(config),
            "bracket_validation_errors": invalid_configured_knockout_opponents(config),
            "monte_carlo": monte_carlo_summary,
        },
        "agents": {
            "count": len(_agent_slots_for_watchdog(config)),
            "slots": _agent_slots_for_watchdog(config),
        },
    }


def _agent_source_planning_watchdog_detail(config: dict[str, Any]) -> str:
    extra = _agent_source_planning_watchdog_extra(config)
    knobs = extra["operational_knobs"]
    agents = extra["agents"]
    scope = extra["scope"]
    return (
        "contrato único distribuído; "
        f"quorum_min={knobs['minimum_source_ready_agents']}; "
        f"meeting_min_participants={knobs['meeting_min_participants']}; "
        f"meeting_quorum_rule={knobs['meeting_quorum_rule']}; "
        f"self_heal_attempts={knobs['source_planning_repair_attempts']}; "
        f"format_repair={knobs['repair_format_removals_with_quorum']}; "
        f"format_repair_timeout_s={knobs['source_planning_format_repair_timeout_seconds']}; "
        f"pre_meeting_repair={knobs['repair_reentry_eligible_removals_before_meeting']}; "
        f"floor_repair_timeout_s={knobs['source_planning_floor_repair_timeout_seconds']}; "
        f"blind_review={knobs['blind_peer_review_enabled']}; "
        f"blind_review_timeout_s={knobs['blind_peer_review_timeout_seconds']}; "
        f"numeric_chairman={knobs['numeric_chairman_enabled']}; "
        f"fast_path={knobs['llm_council_fast_path_enabled']}; "
        f"meeting_repair_attempts={knobs['meeting_response_repair_attempts']}; "
        f"full_path_coverage={knobs['meeting_require_full_path_coverage']}; "
        f"parallel_opponent_room={knobs['parallel_opponent_debriefing_enabled']}; "
        f"timeout_s={knobs['agent_timeout_seconds']}; "
        f"reentry_probe={knobs['agent_reentry_probe_enabled']}; "
        f"reentry_timeout_s={knobs['agent_reentry_probe_timeout_seconds']}; "
        "mediador=não faz fetch externo/não escolhe fontes/não usa cache; "
        "agentes=busca fresca própria sem cache com source_urls/source_queries; "
        "Opta excluída antes da busca no Modelo Principal; "
        f"bracket_constraints={knobs['enforce_bracket_constraints']}; "
        f"bracket_ci_widening={knobs['bracket_uncertainty_ci_widening']}; "
        f"agents={agents['count']}; "
        f"group_matches={scope['group_matches_count']}; "
        f"knockout_scenarios={scope['knockout_matches_count']}"
    )


def _emit_source_planning_readiness(
    watchdog: RunWatchdog,
    report: dict[str, Any],
    *,
    final: bool = True,
    round_name: str = "source-planning",
) -> None:
    for entry in report.get("agents", []):
        source_hint = ", ".join(entry.get("source_items", [])[:3]) or "sem fonte dinâmica válida"
        status = "ativo" if entry.get("ready") else "removido"
        chat_extra: dict[str, Any] = {}
        if not entry.get("ready"):
            chat_extra = {
                "validation_issues": list(entry.get("validation_issues", []) or []),
                "offending_excerpt": entry.get("offending_excerpt", ""),
                "reentry_eligible": bool(entry.get("reentry_eligible", False)),
                "reentry_decision_reason": entry.get("reentry_decision_reason", ""),
                "recoverability": entry.get("recoverability", ""),
                "matched_rule": entry.get("matched_rule", ""),
            }
        watchdog.chat(
            str(entry.get("agent", "Modelo")),
            (
                f"{status}: {entry.get('reason', 'sem diagnóstico')}; "
                f"{entry.get('summary', '')} | fontes: {source_hint}"
            ),
            round_name=round_name,
            extra=chat_extra,
        )
    status = "finish" if report.get("quorum_met") else ("fail" if final else "check")
    watchdog.event(
        "agent_source_quorum",
        status,
        detail=(
            f"{report.get('ready_count', 0)}/{report.get('required_count', 0)} "
            "modelo(s) prontos para debriefing"
        ),
        extra=report,
    )


def _is_format_repairable_planning_reason(reason: str) -> bool:
    """Classe de remoção recuperável por retry curto de formatação.

    Regressão do run 615b0948 (11/jun): Opus passou no preflight com busca
    funcionando e caiu no planejamento por JSON truncado — defeito de formato,
    não de capacidade nem de ambiente. Remoções por ambiente (busca indisponível,
    429, timeout) NÃO entram aqui: retry de formato não conserta quota."""
    normalized = _normalize_text(str(reason or ""))
    format_markers = (
        "resposta parcial",
        "resposta em json parcial",
        "json parcial",
        "devolver resposta parcial",
        "payload parcial",
        "sem campos auditaveis",
    )
    environment_markers = (
        "429",
        "too many requests",
        "quota",
        "rate limit",
        "session limit",
        "timeout",
        "timed out",
        "sem permissao",
        "permissao nao concedida",
        "permission",
        "sem ferramenta",
        "nao ha ferramenta",
        "busca/fetch externo indisponivel",
        "busca externa/fetch nao disponivel",
        "fetch externo indisponivel",
        "sem resposta externa verificavel",
    )
    return any(marker in normalized for marker in format_markers) and not any(
        marker in normalized for marker in environment_markers
    )


def _source_planning_format_repair_prompt(*, config: dict[str, Any], generated_at: datetime) -> str:
    return (
        "REPARO DE FORMATAÇÃO DO PLANEJAMENTO DE FONTES.\n"
        "Sua resposta anterior chegou com JSON veio parcial, truncado ou sem campos auditáveis. "
        "Reenvie somente o objeto JSON completo e válido; SOMENTE o objeto JSON completo deve aparecer, "
        "sem markdown, sem comentário antes ou depois, e sem texto fora do JSON. "
        "Não refaça o raciocínio em prosa.\n\n"
        f"{_agent_owned_fresh_search_contract()}\n"
        f"{_effort_latency_instruction()}\n\n"
        "Campos obrigatórios no JSON: self_identification, title_pct, summary, opening_argument, critique, "
        "adjustment, source_urls, source_queries, scenario_probabilities, team_context_signals. "
        "Use as fontes que você acabou de buscar neste run; se precisar substituir uma fonte quebrada, use "
        "source_queries específicas e auditáveis. Não invente URL, ranking, score, lesão, odd, notícia ou método. "
        "Source_queries só contam quando representam busca realmente executada por você agora.\n\n"
        f"Gerado em UTC: {generated_at.isoformat()}\n"
        f"Escopo de jogos configurado: {_configured_matches_for_prompt(config)}\n"
    )


async def _repair_format_only_planning_removals(
    *,
    config: dict[str, Any],
    planning_opinions: list[Any],
    source_readiness_report: dict[str, Any],
    agent_specs: list[Any],
    generated_at: datetime,
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    watchdog: RunWatchdog | None,
    token_cost_ledger: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Reparo barato de formato mesmo com quórum atingido, antes das salas abrirem.

    O self-heal clássico só dispara sem quórum; com quórum 3/3 o slot truncado ia
    direto para o caminho caro (probe durante a sala, agora seletivo) ou ficava de
    fora. Uma passada curta aqui devolve a voz extra para a sala de adversários e
    para a rodada 1 da principal, sem afrouxar nenhuma régua de mérito."""
    if not bool(config.get("repair_format_removals_with_quorum", True)):
        return planning_opinions, source_readiness_report
    format_slots = [
        str(entry["agent"])
        for entry in source_readiness_report.get("removed_agents", [])
        if _is_format_repairable_planning_reason(str(entry.get("reason", "")))
    ]
    repair_specs = [spec for spec in agent_specs if spec.slot in format_slots]
    if not repair_specs:
        return planning_opinions, source_readiness_report
    timeout = int(
        config.get(
            "source_planning_format_repair_timeout_seconds",
            min(90, int(config.get("agent_timeout_seconds", 90))),
        )
    )
    if watchdog:
        watchdog.start(
            "agent_source_format_repair",
            detail=(
                f"reparo de formatação para {', '.join(spec.slot for spec in repair_specs)}; "
                f"timeout_s={timeout}; quórum já atingido, sala não espera além deste passo"
            ),
            extra={"agents": [spec.slot for spec in repair_specs], "timeout_seconds": timeout},
        )
    repair_prompt = _source_planning_format_repair_prompt(config=config, generated_at=generated_at)
    raw_repair_opinions = await call_all_agents(
        repair_prompt,
        specs=repair_specs,
        baseline_title_pct=baseline_title_pct,
        timeout=timeout,
        allow_local_fallback=allow_agent_fallback,
    )
    _record_token_costs(
        token_cost_ledger,
        config=config,
        prompt=repair_prompt,
        opinions=raw_repair_opinions,
        stage="source_planning_format_repair",
    )
    repaired_opinions = _sanitize_source_planning_opinions(
        raw_repair_opinions,
        baseline_title_pct=baseline_title_pct,
        config=config,
    )
    merged_opinions = _merge_planning_opinions(list(planning_opinions), repaired_opinions, agent_specs)
    updated_report = _source_planning_readiness_report(merged_opinions, config)
    recovered = [
        slot
        for slot in format_slots
        if slot not in {str(entry["agent"]) for entry in updated_report.get("removed_agents", [])}
    ]
    if watchdog:
        watchdog.finish(
            "agent_source_format_repair",
            detail=(
                f"recuperados: {', '.join(recovered) or 'nenhum'}; "
                f"prontos agora: {updated_report.get('ready_count')}/{updated_report.get('required_count')}"
            ),
            extra={"recovered": recovered},
        )
    return merged_opinions, updated_report


def _source_planning_repair_prompt(
    *,
    config: dict[str, Any],
    generated_at: datetime,
    readiness_report: dict[str, Any],
    attempt_index: int,
) -> str:
    return (
        _source_planning_prompt(config=config, generated_at=generated_at)
        + "\n\nRODADA DE REPARO OPERACIONAL / SELF-HEALING.\n"
        "Você foi rechamado porque a rodada inicial não atingiu o quórum mínimo de 3 modelos com plano de fontes "
        "próprio e verificável. Responda em JSON estrito, sem texto fora do JSON. O objetivo desta rodada não é "
        "vencer o debate: é reparar o contrato operacional trazendo source_urls ou source_queries válidas que você "
        "mesmo escolheu agora, sem cache, para Brasil e adversários/cenários configurados. "
        "Se uma fonte falhou, substitua por fonte equivalente fresca; se uma API/bridge falhou por motivo fora do seu "
        "controle e você não conseguiu executar busca/fetch, explique em summary e não declare source_urls/source_queries "
        "como verificáveis nesta rodada. Source_queries só contam quando representam busca realmente executada por você agora. "
        "Não invente URL, ranking, score ou método. Campos obrigatórios: self_identification, title_pct, summary, "
        "opening_argument, critique, adjustment, source_urls, source_queries.\n\n"
        f"Tentativa de reparo: {attempt_index}\n"
        f"Diagnóstico anterior: {json.dumps(readiness_report, ensure_ascii=False)}\n"
    )


def _merge_planning_opinions(current: list[Any], repaired: list[Any], agent_specs: list[Any]) -> list[Any]:
    by_agent = {str(opinion.agent): opinion for opinion in current}
    for opinion in repaired:
        by_agent[str(opinion.agent)] = opinion
    return [
        by_agent[spec.slot]
        for spec in agent_specs
        if spec.slot in by_agent
    ]


def _quorum_floor_repair_slots(source_readiness_report: dict[str, Any], config: dict[str, Any]) -> list[str]:
    enabled = bool(
        config.get(
            "repair_reentry_eligible_removals_before_meeting",
            config.get("repair_reentry_eligible_removals_at_quorum_floor", True),
        )
    )
    if not enabled:
        return []
    slots: list[str] = []
    for entry in source_readiness_report.get("removed_agents", []):
        if bool(entry.get("reentry_eligible", False)):
            slots.append(str(entry.get("agent", "")))
    return [slot for slot in slots if slot]


def _admit_unresolved_policy_suspected_slots(
    source_readiness_report: dict[str, Any],
    *,
    watchdog: RunWatchdog | None,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    admitted_agents: list[str] = []
    for entry in source_readiness_report.get("agents", []):
        entry_copy = dict(entry)
        issues = list(entry_copy.get("validation_issues", []) or [])
        primary_issue = issues[0] if issues else {}
        if (
            not bool(entry_copy.get("ready", False))
            and str(primary_issue.get("recoverability", "")).lower() == "policy_suspected"
        ):
            agent = str(entry_copy.get("agent", "Modelo sem nome"))
            entry_copy["ready"] = True
            entry_copy["reason"] = (
                "admitido com ressalva após repair esgotado; hit semântico permaneceu ambíguo, "
                "mas não houve violação estrutural confirmada"
            )
            entry_copy["admitted_with_policy_warning"] = True
            entry_copy["reentry_eligible"] = False
            entry_copy["reentry_decision_reason"] = (
                "policy_suspected esgotou repair; fail-open controlado para não matar a sala por falso-positivo"
            )
            admitted_agents.append(agent)
            if watchdog:
                watchdog.event(
                    "agent_source_policy_admit",
                    "warning",
                    detail=(
                        f"{agent} admitido com ressalva após repair esgotado; "
                        f"trecho que disparou: {entry_copy.get('offending_excerpt', '')}"
                    ),
                    extra={
                        "agent": agent,
                        "validation_issues": issues,
                        "offending_excerpt": entry_copy.get("offending_excerpt", ""),
                        "recoverability": "policy_suspected",
                    },
                )
        entries.append(entry_copy)
    if not admitted_agents:
        return source_readiness_report
    active_agents = [str(entry.get("agent", "")) for entry in entries if bool(entry.get("ready", False))]
    removed_agents = [entry for entry in entries if not bool(entry.get("ready", False))]
    updated = dict(source_readiness_report)
    updated.update(
        {
            "agents": entries,
            "active_agents": active_agents,
            "removed_agents": removed_agents,
            "ready_count": len(active_agents),
            "quorum_met": len(active_agents) >= int(source_readiness_report.get("required_count", 0) or 0),
        }
    )
    return updated


def _source_planning_policy_warnings(source_readiness_report: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for entry in source_readiness_report.get("agents", []):
        if bool(entry.get("admitted_with_policy_warning", False)):
            warnings.append(
                "policy_suspected admitido com ressalva: "
                f"{entry.get('agent', 'Modelo sem nome')} — trecho: {entry.get('offending_excerpt', '')}"
            )
    return warnings


async def _repair_quorum_floor_planning_removals(
    *,
    config: dict[str, Any],
    planning_opinions: list[Any],
    source_readiness_report: dict[str, Any],
    agent_specs: list[Any],
    generated_at: datetime,
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    watchdog: RunWatchdog | None,
    token_cost_ledger: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    attempts = max(0, int(config.get("source_planning_repair_attempts", DEFAULT_SOURCE_PLANNING_REPAIR_ATTEMPTS)))
    if attempts <= 0:
        return planning_opinions, source_readiness_report
    timeout = int(config.get("source_planning_floor_repair_timeout_seconds", config.get("agent_timeout_seconds", 90)))
    merged_opinions = list(planning_opinions)
    report = source_readiness_report
    repaired_at_least_once = False
    for attempt_index in range(1, attempts + 1):
        repair_slots = _quorum_floor_repair_slots(report, config)
        repair_specs = [spec for spec in agent_specs if spec.slot in repair_slots]
        if not repair_specs:
            break
        repaired_at_least_once = True
        if watchdog:
            watchdog.start(
                "agent_source_pre_meeting_repair",
                detail=(
                    f"attempt {attempt_index}/{attempts}; removido(s) reparável(eis) antes da sala: "
                    f"{', '.join(spec.slot for spec in repair_specs)}; timeout_s={timeout}"
                ),
                extra={
                    "attempt": attempt_index,
                    "agents": [spec.slot for spec in repair_specs],
                    "timeout_seconds": timeout,
                    "ready_count": report.get("ready_count"),
                    "required_count": report.get("required_count"),
                },
            )
        repair_prompt = _source_planning_repair_prompt(
            config=config,
            generated_at=generated_at,
            readiness_report=report,
            attempt_index=attempt_index,
        )
        raw_repair_opinions = await call_all_agents(
            repair_prompt,
            specs=repair_specs,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
        )
        _record_token_costs(
            token_cost_ledger,
            config=config,
            prompt=repair_prompt,
            opinions=raw_repair_opinions,
            stage=f"source_planning_pre_meeting_repair_{attempt_index}",
        )
        repaired_opinions = _sanitize_source_planning_opinions(
            raw_repair_opinions,
            baseline_title_pct=baseline_title_pct,
            config=config,
        )
        merged_opinions = _merge_planning_opinions(merged_opinions, repaired_opinions, agent_specs)
        report = _source_planning_readiness_report(
            merged_opinions,
            {**config, "_source_planning_semantic_policy_stage": "post_repair"},
        )
        recovered = [
            slot
            for slot in repair_slots
            if slot not in {str(entry.get("agent", "")) for entry in report.get("removed_agents", [])}
        ]
        if watchdog:
            watchdog.finish(
                "agent_source_pre_meeting_repair",
                detail=(
                    f"attempt {attempt_index}/{attempts}; recuperados antes da sala: "
                    f"{', '.join(recovered) or 'nenhum'}; "
                    f"prontos agora: {report.get('ready_count')}/{report.get('required_count')}"
                ),
                extra={"attempt": attempt_index, "recovered": recovered},
            )
    if repaired_at_least_once:
        report = _admit_unresolved_policy_suspected_slots(report, watchdog=watchdog)
    return merged_opinions, report


async def _self_heal_source_planning_quorum(
    *,
    config: dict[str, Any],
    planning_opinions: list[Any],
    source_readiness_report: dict[str, Any],
    agent_specs: list[Any],
    generated_at: datetime,
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    watchdog: RunWatchdog | None,
    token_cost_ledger: dict[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    attempts = max(0, int(config.get("source_planning_repair_attempts", DEFAULT_SOURCE_PLANNING_REPAIR_ATTEMPTS)))
    if attempts <= 0:
        return planning_opinions, source_readiness_report

    current_opinions = list(planning_opinions)
    report = source_readiness_report
    timeout = int(config.get("agent_timeout_seconds", 90))

    for attempt_index in range(1, attempts + 1):
        if bool(report.get("quorum_met")):
            break
        removed_slots = [str(entry["agent"]) for entry in report.get("removed_agents", [])]
        repair_specs = [spec for spec in agent_specs if spec.slot in removed_slots]
        if not repair_specs:
            break
        if watchdog:
            watchdog.start(
                "agent_source_self_heal",
                detail=f"attempt {attempt_index}/{attempts}; retrying {', '.join(spec.slot for spec in repair_specs)}",
                extra={"attempt": attempt_index, "agents": [spec.slot for spec in repair_specs]},
            )

        repair_prompt = _source_planning_repair_prompt(
            config=config,
            generated_at=generated_at,
            readiness_report=report,
            attempt_index=attempt_index,
        )
        raw_repair_opinions = await call_all_agents(
            repair_prompt,
            specs=repair_specs,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
        )
        _record_token_costs(
            token_cost_ledger,
            config=config,
            prompt=repair_prompt,
            opinions=raw_repair_opinions,
            stage=f"source_planning_repair_{attempt_index}",
        )
        repaired_opinions = _sanitize_source_planning_opinions(
            raw_repair_opinions,
            baseline_title_pct=baseline_title_pct,
            config=config,
        )
        current_opinions = _merge_planning_opinions(current_opinions, repaired_opinions, agent_specs)
        report = _source_planning_readiness_report(
            current_opinions,
            {**config, "_source_planning_semantic_policy_stage": "post_repair"},
        )
        if watchdog:
            _emit_source_planning_readiness(
                watchdog,
                report,
                final=False,
                round_name=f"source-planning-repair-{attempt_index}",
            )
            status_detail = (
                f"attempt {attempt_index}/{attempts}; "
                f"{report.get('ready_count', 0)}/{report.get('required_count', 0)} ready"
            )
            if report.get("quorum_met"):
                watchdog.finish("agent_source_self_heal", detail=status_detail)
            else:
                watchdog.event(
                    "agent_source_self_heal",
                    "check",
                    detail=status_detail,
                    extra={"attempt": attempt_index, "quorum_met": False},
                )
    report = _admit_unresolved_policy_suspected_slots(report, watchdog=watchdog)
    return current_opinions, report


def _drop_fallback_only_agent_slots(planning_opinions: list[Any], config: dict[str, Any]) -> list[str]:
    report = _source_planning_readiness_report(planning_opinions, config)
    return [str(entry["agent"]) for entry in report["removed_agents"]]


def _agent_reentry_probe_prompt(
    *,
    config: dict[str, Any],
    generated_at: datetime,
    removed_reason: str,
) -> str:
    matches = _configured_matches_for_prompt(config)
    return (
        "REENTRADA ASSÍNCRONA NA SALA DE DEBRIEFING.\n"
        "A conversa principal não vai esperar por você. Responda SOMENTE JSON estrito e curto. "
        "Objetivo: provar que você pode voltar com plano de fontes próprio, fresco e verificável para a sala. "
        f"{_agent_owned_fresh_search_contract()} "
        f"{_quantitative_qualitative_decision_instruction()} "
        f"{_auditable_sources_instruction(config)} "
        "Não use Opta, não use cache, não invente URL, ranking, odd, lesão, escalação, score ou método. "
        "Campos obrigatórios: self_identification, title_pct, summary, opening_argument, critique, adjustment, "
        "source_urls, source_queries, scenario_probabilities, team_context_signals. "
        "Inclua pelo menos 2 source_urls HTTP verificáveis ou 2 source_queries específicas não-Opta. "
        "Se não tiver fonte verificável, declare isso em summary e não peça reentrada.\n"
        f"Motivo da saída temporária: {removed_reason or 'sem resposta externa verificável'}\n"
        f"Data do run: {generated_at.isoformat()}\n"
        f"Escopo permitido: {json.dumps(matches, ensure_ascii=False)[:5000]}\n"
    )


async def _run_agent_reentry_probe(
    *,
    spec: Any,
    config: dict[str, Any],
    generated_at: datetime,
    baseline_title_pct: float,
    removed_reason: str,
    timeout: int,
) -> tuple[Any | None, str, str]:
    prompt = _agent_reentry_probe_prompt(
        config=config,
        generated_at=generated_at,
        removed_reason=removed_reason,
    )
    try:
        raw_opinion = await call_agent(
            spec,
            prompt,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=False,
        )
    except Exception as exc:
        return None, str(exc), prompt
    sanitized = _sanitize_source_planning_opinions(
        [raw_opinion],
        baseline_title_pct=baseline_title_pct,
        config=config,
    )
    readiness_config = {**config, "minimum_source_ready_agents": 1}
    report = _source_planning_readiness_report(sanitized, readiness_config)
    if bool(report.get("quorum_met")) and sanitized and not bool(getattr(sanitized[0], "used_fallback", False)):
        return sanitized[0], "", prompt
    reason = "não trouxe plano de fontes próprio e verificável"
    removed = report.get("removed_agents", [])
    if removed:
        reason = str(removed[0].get("reason", reason))
    return None, reason, prompt


def _ready_reentry_slots(pending: dict[str, asyncio.Task]) -> list[str]:
    return [slot for slot, task in pending.items() if task.done()]


def _room_majority_quorum(*, configured_min: int, active_count: int) -> int:
    if active_count <= 0:
        return 0
    _ = configured_min  # Backward-compatible parameter; quorum is majority of the active room.
    return int(active_count) // 2 + 1


def _should_schedule_reentry_probe(
    *,
    config: dict[str, Any],
    round_index: int,
    active_count: int,
    configured_min_participants: int,
    consecutive_sterile: int,
) -> tuple[bool, str]:
    policy = str(config.get("agent_reentry_probe_policy", "always")).strip().lower()
    if policy in {"off", "disabled", "never"}:
        return False, "policy disabled"
    if active_count < configured_min_participants:
        return True, "active room below minimum participants"
    if consecutive_sterile > 0 and bool(config.get("agent_reentry_probe_on_sterile_round", True)):
        return True, "sterile room needs recovery"
    min_round = max(1, int(config.get("agent_reentry_probe_min_round", 1)))
    if round_index < min_round:
        return False, f"delayed until round {min_round}"
    if policy in {"always", "eager"}:
        return True, "policy eager"
    if policy in {"quorum_risk", "needed", "latency_first"}:
        return False, (
            f"active room already has {active_count} participant(s), "
            f"minimum is {configured_min_participants}"
        )
    return True, f"unknown policy {policy}; falling back to eager"


@dataclass(frozen=True)
class RunArtifacts:
    bundle: ReportBundle
    post: str
    raw_evidence: list[EvidenceResult]


class SourcePlanningQuorumError(RuntimeError):
    """Raised when too few external agents bring auditable source plans."""


class ReportCoherenceError(RuntimeError):
    """Raised when the final report would publish internally inconsistent probabilities."""


class MeetingConsensusError(RuntimeError):
    """Raised when the meeting cannot produce a valid consensus (sterile room or ceiling without valid votes)."""


def _specs_after_preflight_exclusion(
    agent_specs: list[Any],
    config: dict[str, Any],
    watchdog: Any = None,
) -> list[Any]:
    """Remove do run slots que falharam duro no preflight (ex.: HTTP 429 em toda a cadeia).

    O slot contribuiria zero de qualquer forma (planejamento removido + probes de
    reentrada repetindo o mesmo erro); excluí-lo cedo economiza chamadas, tempo e
    ruído de watchdog. Gate: exclude_slots_failing_preflight (default true); o CLI
    só popula _preflight_failed_slots quando --strict-agents está desligado."""
    failed_slots = {str(slot) for slot in (config.get("_preflight_failed_slots") or []) if str(slot)}
    if not failed_slots or not bool(config.get("exclude_slots_failing_preflight", True)):
        return agent_specs
    excluded = [spec.slot for spec in agent_specs if str(getattr(spec, "slot", "")) in failed_slots]
    if not excluded:
        return agent_specs
    remaining = [spec for spec in agent_specs if str(getattr(spec, "slot", "")) not in failed_slots]
    if watchdog:
        watchdog.event(
            "model_preflight",
            "slot_excluded",
            detail=(
                ", ".join(excluded)
                + " fora do run por falha dura no preflight; sem chamadas de planejamento nem probes de reentrada"
            ),
            extra={"slots": excluded},
        )
    return remaining


def load_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if path.exists():
        config = json.loads(path.read_text(encoding="utf-8"))
        hydrate_canonical_configs(config, base_dir=path.parent)
        return config
    if path.name == "worldcup_brazil.json":
        example_path = path.with_name("worldcup_brazil.example.json")
        if example_path.exists():
            config = json.loads(example_path.read_text(encoding="utf-8"))
            hydrate_canonical_configs(config, base_dir=example_path.parent)
            return config
    return {}


def _apply_runtime_env_overrides(config: dict[str, Any]) -> None:
    mapping = {
        "http_max_attempts": "HTTP_MAX_ATTEMPTS",
        "http_backoff_base_seconds": "HTTP_BACKOFF_BASE_SECONDS",
        "http_backoff_max_seconds": "HTTP_BACKOFF_MAX_SECONDS",
        "http_connect_timeout_seconds": "HTTP_CONNECT_TIMEOUT_SECONDS",
        "agent_bulkhead_default": "AGENT_BULKHEAD_DEFAULT",
        "source_bulkhead_per_host": "SOURCE_BULKHEAD_PER_HOST",
    }
    for config_key, env_key in mapping.items():
        if config_key in config and env_key not in os.environ:
            os.environ[env_key] = str(config[config_key])
    for provider, limit in dict(config.get("agent_bulkheads", {})).items():
        suffix = re.sub(r"[^A-Z0-9]+", "_", str(provider).upper()).strip("_")
        env_key = f"AGENT_BULKHEAD_{suffix}"
        if env_key not in os.environ:
            os.environ[env_key] = str(limit)


def _default_group_matches(config: dict[str, Any]) -> list[dict[str, Any]]:
    return list(
        config.get(
            "group_matches",
            [
                {"opponent": "Adversário 1 a definir", "venue": "A definir"},
                {"opponent": "Adversário 2 a definir", "venue": "A definir"},
                {"opponent": "Adversário 3 a definir", "venue": "A definir"},
            ],
        )
    )


def _default_knockout_matches(config: dict[str, Any]) -> list[dict[str, Any]]:
    matches = list(
        config.get(
            "knockout_matches",
            [
                {
                    "phase": "16 avos",
                    "opponent": "Adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": True,
                    "scenario_pct": 46.0,
                },
                {
                    "phase": "16 avos",
                    "opponent": "Segundo adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": False,
                    "scenario_pct": 24.0,
                },
                {
                    "phase": "Oitavas",
                    "opponent": "Adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": True,
                    "scenario_pct": 38.0,
                },
                {
                    "phase": "Oitavas",
                    "opponent": "Segundo adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": False,
                    "scenario_pct": 22.0,
                },
                {
                    "phase": "Quartas",
                    "opponent": "Adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": True,
                    "brazil_pct": 52.0,
                },
                {
                    "phase": "Quartas",
                    "opponent": "Segundo adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": False,
                    "brazil_pct": 50.0,
                },
                {
                    "phase": "Semifinal",
                    "opponent": "Adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": True,
                    "brazil_pct": 48.0,
                },
                {
                    "phase": "Semifinal",
                    "opponent": "Segundo adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": False,
                    "brazil_pct": 46.0,
                },
                {
                    "phase": "Final",
                    "opponent": "Adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": True,
                    "brazil_pct": 49.0,
                },
                {
                    "phase": "Final",
                    "opponent": "Segundo adversário mais provável a definir",
                    "venue": "A definir",
                    "most_likely": False,
                    "brazil_pct": 48.0,
                },
            ],
        )
    )
    return annotate_knockout_matches_with_bracket(config, matches)


def _base_probability_for_match(match: dict[str, Any], *, knockout: bool) -> float:
    if "brazil_pct" in match:
        return float(match["brazil_pct"])
    return 57.0 if knockout else 62.0


def _optional_float(match: dict[str, Any], key: str) -> float | None:
    if key not in match or match[key] is None:
        return None
    return float(match[key])


def _recent_event_impacts(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw_events = config.get("recent_event_impacts") or []
    if not isinstance(raw_events, list):
        return []
    return [event for event in raw_events if isinstance(event, dict)]


def _event_category(event: dict[str, Any]) -> str:
    raw = _normalize_text(str(event.get("category") or event.get("type") or "qualitative"))
    if raw in {"statistical", "quantitative", "estatistico", "estatistica", "quantitativo", "quanti"}:
        return "statistical"
    return "qualitative"


def _event_float(event: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(event.get(key, default) or default)
    except (TypeError, ValueError):
        return default


def _event_list(event: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw = event.get(key)
        if raw is None:
            continue
        if isinstance(raw, list):
            values.extend(str(item) for item in raw if str(item).strip())
        else:
            values.append(str(raw))
    return values


def _event_applies_to_match(event: dict[str, Any], match: dict[str, Any]) -> bool:
    phases = {_normalize_text(value) for value in _event_list(event, "phase", "phases", "applies_to_phases")}
    match_phase = _normalize_text(str(match.get("phase") or "Fase de grupos"))
    if phases and match_phase not in phases:
        return False

    opponent = _normalize_text(str(match.get("opponent", "")))
    event_teams = {_normalize_text(value) for value in _event_list(event, "team", "teams")}
    event_opponents = {
        _normalize_text(value)
        for value in _event_list(event, "opponent", "opponents", "applies_to_opponents")
    }
    if event_opponents:
        return opponent in event_opponents
    if event_teams:
        return "brasil" in event_teams or "brazil" in event_teams or opponent in event_teams
    return True


def _event_source_reference(event: dict[str, Any]) -> str:
    source_url = str(event.get("source_url") or "").strip()
    if source_url:
        return source_url
    source_query = str(event.get("source_query") or "").strip()
    return source_query or "fonte não informada"


def _event_summary(event: dict[str, Any]) -> str:
    return str(event.get("summary") or event.get("id") or "evento recente").strip()


def _event_detail(event: dict[str, Any]) -> str:
    date = str(event.get("date") or "data não informada").strip()
    summary = _event_summary(event)
    shift = _event_float(event, "brazil_shift_pct")
    scenario_shift = _event_float(event, "scenario_shift_pct")
    return (
        f"{date}: {summary}; categoria={_event_category(event)}; "
        f"efeito Brasil={shift:+.1f} p.p.; efeito cenário={scenario_shift:+.1f} p.p.; "
        f"source={_event_source_reference(event)}"
    )


def _recent_event_signals_for_match(
    match: dict[str, Any],
    *,
    base_pct: float,
    config: dict[str, Any],
) -> tuple[list[SourceSignal], list[SourceSignal]]:
    statistical: list[SourceSignal] = []
    qualitative: list[SourceSignal] = []
    for event in _recent_event_impacts(config):
        if not _event_applies_to_match(event, match):
            continue
        category = _event_category(event)
        shift = _event_float(event, "brazil_shift_pct")
        adjusted_pct = max(1.0, min(99.0, base_pct + shift))
        signal = SourceSignal(
            source=f"recent event impact: {_event_summary(event)}",
            brazil_pct=adjusted_pct,
            opponent_pct=100 - adjusted_pct,
            confidence=_event_float(event, "confidence", 0.66 if category == "statistical" else 0.58),
            detail=_event_detail(event),
        )
        if category == "statistical":
            statistical.append(signal)
        else:
            qualitative.append(signal)
    return statistical, qualitative


def _recent_event_rationale_fragment(match: dict[str, Any], *, config: dict[str, Any]) -> str:
    details = [
        _event_detail(event)
        for event in _recent_event_impacts(config)
        if _event_applies_to_match(event, match)
    ]
    if not details:
        return ""
    return " Eventos recentes aplicados no harness: " + "; ".join(details) + "."


def _scenario_pct_for_match(match: dict[str, Any], *, config: dict[str, Any]) -> float | None:
    scenario_pct = _optional_float(match, "scenario_pct")
    if scenario_pct is None:
        return None
    shift = sum(
        _event_float(event, "scenario_shift_pct")
        for event in _recent_event_impacts(config)
        if _event_applies_to_match(event, match)
    )
    return round(max(0.0, min(100.0, scenario_pct + shift)), 1)


def _is_placeholder_opponent_name(value: Any) -> bool:
    normalized = _normalize_text(str(value or ""))
    return not normalized or "definir" in normalized or "adversario" in normalized


def _widen_ci_for_bracket_uncertainty(estimate: Any, match: dict[str, Any], *, config: dict[str, Any]) -> None:
    if not bool(config.get("bracket_uncertainty_ci_widening", True)):
        return
    if not _is_placeholder_opponent_name(match.get("opponent")):
        return
    allowed_opponents = [
        str(candidate).strip()
        for candidate in match.get("allowed_opponents", [])
        if str(candidate).strip()
    ]
    if len(allowed_opponents) <= 1:
        return
    if estimate.brazil_ci_low is None or estimate.brazil_ci_high is None:
        return

    max_widen = float(config.get("bracket_uncertainty_max_ci_widen_pct", 8.0))
    widen = min(max_widen, round(1.4 + (len(allowed_opponents) - 1) * 0.4, 1))
    estimate.brazil_ci_low = round(max(0.0, estimate.brazil_ci_low - widen / 2.0), 1)
    estimate.brazil_ci_high = round(min(100.0, estimate.brazil_ci_high + widen / 2.0), 1)


def _event_impact_prompt_instruction() -> str:
    return (
        "Contrato de eventos por fase: aplique eventos reais ou eventos simulados de cenário até a Final "
        "com os mesmos critérios da fase de grupos. Para cada jogo ou cenário, qualquer evento que altere "
        "probabilidade precisa trazer date, team, category, summary, source_url ou source_query, "
        "brazil_shift_pct, scenario_shift_pct e confidence. Eventos simulados precisam ser marcados como "
        "hipótese de cenário, baseados em fonte/consulta auditável sobre lesões, cortes, cartões, arbitragem, "
        "descanso, performance ou chaveamento; não transforme hipótese em fato. Se não houver lastro verificável, "
        "declare ausência de evento relevante e use efeito 0."
    )


def _event_impact_criteria_for_prompt() -> dict[str, Any]:
    return {
        "rule": (
            "Aplique os mesmos critérios da fase de grupos em todos os cenários até a Final; "
            "não invente evento, fonte ou efeito."
        ),
        "required_fields": [
            "date",
            "team",
            "category",
            "summary",
            "source_url",
            "source_query",
            "brazil_shift_pct",
            "scenario_shift_pct",
            "confidence",
        ],
        "category_values": ["statistical", "qualitative"],
        "shift_units": "pontos percentuais",
        "field_semantics": {
            "date": "data do evento real ou data/base do cenário simulado",
            "team": "Brasil, adversário ou cenário de chaveamento afetado",
            "category": "statistical para números/performance; qualitative para lesão, corte, arbitragem, cartões, descanso ou contexto",
            "summary": "descrição curta do fato ou da hipótese de cenário",
            "source_url": "URL auditável quando disponível",
            "source_query": "consulta usada quando não houver URL direta ou quando o modelo buscar no próprio canal",
            "brazil_shift_pct": "efeito na chance do Brasil vencer/passar no confronto",
            "scenario_shift_pct": "efeito na chance de esse confronto/caminho acontecer",
            "confidence": "confiança de 0 a 1 no impacto estimado",
        },
        "simulation_guardrail": (
            "Eventos simulados são hipóteses auditáveis de cenário, não fatos; sem source_url/source_query, use shift 0."
        ),
    }


def _event_impact_scenarios_for_prompt(config: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    criteria_fields = _event_impact_criteria_for_prompt()["required_fields"]
    for match in _default_group_matches(config):
        opponent = str(match.get("opponent", "")).strip()
        if not opponent:
            continue
        scenarios.append(
            {
                "phase": "Fase de grupos",
                "opponent": opponent,
                "venue": match.get("venue"),
                "scenario_pct": match.get("scenario_pct"),
                "required_event_fields": criteria_fields,
                "instruction": (
                    "Busque eventos recentes para Brasil e adversário; aplique os mesmos critérios da fase de grupos."
                ),
            }
        )
    for match in _default_knockout_matches(config):
        phase = str(match.get("phase") or "Mata-mata").strip() or "Mata-mata"
        opponent = str(match.get("opponent", "")).strip()
        if not opponent:
            continue
        scenarios.append(
            {
                "phase": phase,
                "opponent": opponent,
                "venue": match.get("venue"),
                "most_likely": bool(match.get("most_likely", False)),
                "scenario_pct": match.get("scenario_pct"),
                "required_event_fields": criteria_fields,
                "instruction": (
                    "Projete eventos reais já conhecidos e eventos simulados de cenário até esta fase "
                    "com os mesmos critérios da fase de grupos; se não houver fonte ou consulta auditável, "
                    "declare ausência de evento relevante e não altere probabilidade."
                ),
            }
        )
    return scenarios


def _market_value_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("market_value_momentum") or {}
    return raw if isinstance(raw, dict) else {}


def _parse_market_value_eur(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().lower()
    if not text:
        return None
    multiplier = 1.0
    if any(token in text for token in ("m", "mi", "milhao", "milhão", "million")):
        multiplier = 1_000_000.0
    elif any(token in text for token in ("k", "mil", "thousand")):
        multiplier = 1_000.0
    cleaned = re.sub(r"[^0-9,.\-]", "", text)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned) * multiplier
    except ValueError:
        return None


def _market_value_entry_eur(entry: dict[str, Any], *, base: str) -> float | None:
    for key in (f"{base}_value_eur", f"{base}_eur", base):
        if key in entry:
            return _parse_market_value_eur(entry.get(key))
    for key in (f"{base}_value_m_eur", f"{base}_m_eur"):
        if key in entry:
            value = _parse_market_value_eur(entry.get(key))
            return value * 1_000_000 if value is not None and value < 1_000_000 else value
    return None


def _market_value_player_weighted_delta_eur(entry: dict[str, Any], *, percent_cap: float = 0.75) -> float:
    old_value = _market_value_entry_eur(entry, base="old")
    new_value = _market_value_entry_eur(entry, base="new")
    if old_value is None or new_value is None or old_value <= 0:
        return 0.0
    delta = new_value - old_value
    pct_delta = delta / old_value
    multiplier = 1.0 + min(abs(pct_delta), max(0.0, float(percent_cap)))
    return delta * multiplier


def _team_market_value_entries(config: dict[str, Any], team: str) -> list[dict[str, Any]]:
    cfg = _market_value_config(config)
    teams = cfg.get("teams") or {}
    normalized_team = _normalize_text(team)
    aliases = {normalized_team}
    if normalized_team in {"brasil", "brazil"}:
        aliases.update({"brasil", "brazil"})
    for name, raw_entries in dict(teams).items():
        if _normalize_text(str(name)) not in aliases:
            continue
        if isinstance(raw_entries, dict):
            raw_entries = raw_entries.get("players", [])
        return [entry for entry in raw_entries if isinstance(entry, dict)]
    return []


def _format_market_value_millions(value_eur: float | None) -> str:
    if value_eur is None:
        return "s/d"
    return f"{value_eur / 1_000_000:.1f}M"


def _market_value_team_summary(config: dict[str, Any], team: str) -> dict[str, Any] | None:
    cfg = _market_value_config(config)
    entries = _team_market_value_entries(config, team)
    if not entries:
        return None
    percent_cap = float(cfg.get("percent_multiplier_cap", 0.75))
    players = []
    nominal_delta = 0.0
    weighted_delta = 0.0
    positive_players = 0
    for entry in entries:
        old_value = _market_value_entry_eur(entry, base="old")
        new_value = _market_value_entry_eur(entry, base="new")
        if old_value is None or new_value is None or old_value <= 0:
            continue
        delta = new_value - old_value
        weighted = _market_value_player_weighted_delta_eur(entry, percent_cap=percent_cap)
        pct_delta = delta / old_value * 100.0
        nominal_delta += delta
        weighted_delta += weighted
        if delta > 0:
            positive_players += 1
        players.append(
            {
                "player": str(entry.get("player") or entry.get("name") or "Jogador"),
                "old_value_eur": old_value,
                "new_value_eur": new_value,
                "delta_eur": delta,
                "pct_delta": pct_delta,
                "weighted_delta_eur": weighted,
                "display": (
                    f"{entry.get('player') or entry.get('name') or 'Jogador'} "
                    f"{_format_market_value_millions(old_value)}->{_format_market_value_millions(new_value)}"
                ),
            }
        )
    if not players:
        return None
    players.sort(key=lambda item: abs(float(item["weighted_delta_eur"])), reverse=True)
    return {
        "team": team,
        "players_tracked": len(players),
        "positive_players": positive_players,
        "nominal_delta_eur": round(nominal_delta, 2),
        "weighted_delta_eur": round(weighted_delta, 2),
        "top_players": players[:3],
    }


def _market_value_momentum_report(config: dict[str, Any]) -> dict[str, Any]:
    cfg = _market_value_config(config)
    if not cfg.get("enabled", False):
        return {"available": False}
    teams = cfg.get("teams") or {}
    summaries = {}
    for team in dict(teams):
        summary = _market_value_team_summary(config, str(team))
        if summary:
            summaries[str(team)] = summary
    return {
        "available": bool(summaries),
        "source": cfg.get("source", "Transfermarkt"),
        "rule": (
            "Peso combina delta nominal em euros e percentual, com teto para o percentual; "
            "um ganho 50M->55M pesa mais que 10M->13M."
        ),
        "teams": summaries,
    }


def _market_value_momentum_signal(
    match: dict[str, Any],
    *,
    base_pct: float,
    config: dict[str, Any],
) -> SourceSignal | None:
    cfg = _market_value_config(config)
    if not cfg.get("enabled", False):
        return None
    opponent = str(match.get("opponent") or "")
    if not opponent or "definir" in _normalize_text(opponent):
        return None
    brazil_summary = _market_value_team_summary(config, "Brasil")
    opponent_summary = _market_value_team_summary(config, opponent)
    if not brazil_summary or not opponent_summary:
        return None
    eur_per_point = float(cfg.get("eur_per_probability_point", 25_000_000))
    max_shift_pct = float(cfg.get("max_shift_pct", 2.5))
    diff = float(brazil_summary["weighted_delta_eur"]) - float(opponent_summary["weighted_delta_eur"])
    shift = max(-max_shift_pct, min(max_shift_pct, diff / max(eur_per_point, 1.0)))
    brazil_pct = max(1.0, min(99.0, base_pct + shift))
    detail = (
        "Transfermarkt market value momentum: "
        f"Brasil {brazil_summary['positive_players']} jogadores em alta, "
        f"delta nominal €{brazil_summary['nominal_delta_eur'] / 1_000_000:.1f}M, "
        f"score ponderado €{brazil_summary['weighted_delta_eur'] / 1_000_000:.1f}M; "
        f"{opponent} {opponent_summary['positive_players']} jogadores em alta, "
        f"delta nominal €{opponent_summary['nominal_delta_eur'] / 1_000_000:.1f}M, "
        f"score ponderado €{opponent_summary['weighted_delta_eur'] / 1_000_000:.1f}M. "
        "Regra: delta nominal em euros pesa mais que percentual isolado; destaques: "
        f"Brasil {', '.join(player['display'] for player in brazil_summary['top_players'])}; "
        f"{opponent} {', '.join(player['display'] for player in opponent_summary['top_players'])}."
    )
    return SourceSignal(
        source="Transfermarkt market value momentum",
        brazil_pct=brazil_pct,
        opponent_pct=100 - brazil_pct,
        confidence=float(cfg.get("confidence", 0.58)),
        detail=detail,
    )


def _signals_for_match(
    match: dict[str, Any],
    *,
    evidence: list[EvidenceResult],
    knockout: bool,
    config: dict[str, Any] | None = None,
) -> tuple[list[SourceSignal], list[SourceSignal]]:
    config = config or {}
    base_pct = _base_probability_for_match(match, knockout=knockout)
    statistical = [
        SourceSignal(
            source=result.source.name,
            brazil_pct=base_pct,
            opponent_pct=100 - base_pct,
            confidence=result.source.confidence if result.ok else result.source.confidence * 0.45,
            detail=result.source.notes,
        )
        for result in evidence
        if result.source.category == "statistical"
    ]
    qualitative_shift = float(match.get("qualitative_shift_pct", 0.0))
    qualitative_pct = max(1.0, min(99.0, base_pct + qualitative_shift))
    qualitative = [
        SourceSignal(
            source=result.source.name,
            brazil_pct=qualitative_pct,
            opponent_pct=100 - qualitative_pct,
            confidence=result.source.confidence if result.ok else result.source.confidence * 0.35,
            detail=result.source.notes,
        )
        for result in evidence
        if result.source.category == "qualitative"
    ]
    market_value_signal = _market_value_momentum_signal(match, base_pct=base_pct, config=config)
    if market_value_signal is not None:
        qualitative.append(market_value_signal)
    event_statistical, event_qualitative = _recent_event_signals_for_match(match, base_pct=base_pct, config=config)
    statistical.extend(event_statistical)
    qualitative.extend(event_qualitative)

    if not statistical:
        statistical.append(
            SourceSignal(
                source="baseline statistical prior",
                brazil_pct=base_pct,
                opponent_pct=100 - base_pct,
                confidence=0.45,
                detail=(
                    "Prior estatístico local usado porque o mediador não injeta fetch externo; "
                    "os ajustes frescos entram pela sala de modelos."
                ),
            )
        )
    if not qualitative:
        qualitative.append(
            SourceSignal(
                source="baseline qualitative prior",
                brazil_pct=qualitative_pct,
                opponent_pct=100 - qualitative_pct,
                confidence=0.35,
                detail=(
                    "Prior qualitativo local usado porque o mediador não injeta fetch externo; "
                    "lesões, forma, arbitragem e elenco entram pela sala de modelos."
                ),
            )
        )
    return statistical, qualitative


def _rationale(
    match: dict[str, Any],
    *,
    evidence: list[EvidenceResult],
    knockout: bool,
    config: dict[str, Any] | None = None,
) -> str:
    config = config or {}
    ok_stat = [r.source.name for r in evidence if r.ok and r.source.category == "statistical"]
    ok_qual = [r.source.name for r in evidence if r.ok and r.source.category == "qualitative"]
    event_fragment = _recent_event_rationale_fragment(match, config=config)
    phase_context = (
        "No mata-mata, a estimativa comprime vantagem técnica porque jogo único aumenta variância, "
        "cartões e arbitragem pesam mais, e três dias completos entre partidas reduzem margem física."
        if knockout
        else "Na fase de grupos, a estimativa favorece estabilidade estatística porque há menor pressão de eliminação direta, "
        "mas saldo, rotação e cartões mudam o incentivo tático."
    )
    custom = match.get("rationale")
    if custom:
        return str(custom) + event_fragment
    if not evidence:
        return (
            f"{phase_context} O cálculo inicial usa priors locais como partitura de base, mas o mediador "
            "não faz busca externa nem cache: as fontes atualizadas entram pelas falas dos modelos e pelo "
            "consenso jogo a jogo. Os modelos usam dados quantitativos e qualitativos sem quota metodológica "
            "fixa; a força das fontes define quais premissas movem as probabilidades."
            f"{event_fragment}"
        )
    return (
        f"{phase_context} O cálculo cruza sinais quantitativos "
        f"({', '.join(ok_stat) if ok_stat else 'sem fonte externa estatística válida no run'}) "
        f"e sinais qualitativos ({', '.join(ok_qual) if ok_qual else 'sem fonte externa qualitativa válida no run'}). "
        "Não há quota fixa entre quanti e quali: só entram premissas com número, fonte/query e efeito em probabilidade."
        f"{event_fragment}"
    )


def _model_scaled_stage_probabilities(title_pct: float) -> dict[str, float]:
    return {
        "quartas": round(min(95.0, max(0.0, title_pct * 5.9)), 1),
        "semifinal": round(min(90.0, max(0.0, title_pct * 3.7)), 1),
        "final": round(min(70.0, max(0.0, title_pct * 2.05)), 1),
        "titulo": round(title_pct, 1),
    }


def _stage_probability_blend_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("stage_probability_blend")
    configured = raw if isinstance(raw, dict) else {}
    enabled = bool(configured.get("enabled", True))
    mc_weight = float(configured.get("monte_carlo_weight", 0.60))
    model_weight = float(configured.get("model_weight", 0.40))
    mc_weight = max(0.0, mc_weight)
    model_weight = max(0.0, model_weight)
    total = mc_weight + model_weight
    if total <= 0:
        mc_weight, model_weight = 0.60, 0.40
        total = 1.0
    return {
        "enabled": enabled,
        "monte_carlo_weight": mc_weight / total,
        "model_weight": model_weight / total,
    }


def _stage_probability_blend_label(config: dict[str, Any]) -> str:
    blend = _stage_probability_blend_config(config)
    mc = round(float(blend["monte_carlo_weight"]) * 100)
    model = round(float(blend["model_weight"]) * 100)
    return f"monte_carlo_model_blend_{mc}_{model}"


def _stage_probability_blend_metadata(config: dict[str, Any]) -> dict[str, Any]:
    blend = _stage_probability_blend_config(config)
    return {
        "enabled": bool(blend["enabled"]),
        "monte_carlo_weight": round(float(blend["monte_carlo_weight"]), 2),
        "model_weight": round(float(blend["model_weight"]), 2),
        "label": _stage_probability_blend_label(config),
    }


def _stage_probabilities(title_pct: float, config: dict[str, Any]) -> dict[str, float]:
    configured = config.get("stage_probabilities")
    if configured:
        return {key: round(float(value), 1) for key, value in configured.items()}
    model_scaled = _model_scaled_stage_probabilities(title_pct)
    monte_carlo_result = config.get("_monte_carlo_result")
    if (
        isinstance(monte_carlo_result, dict)
        and monte_carlo_result.get("enabled")
        and bool((config.get("monte_carlo") or {}).get("use_stage_probabilities", True))
    ):
        stages = monte_carlo_result.get("stage_probabilities") or {}
        mc_scaled = {
            "quartas": round(float(stages.get("quartas", model_scaled["quartas"])), 1),
            "semifinal": round(float(stages.get("semifinal", model_scaled["semifinal"])), 1),
            "final": round(float(stages.get("final", model_scaled["final"])), 1),
            "titulo": round(float(stages.get("titulo", model_scaled["titulo"])), 1),
        }
        blend = _stage_probability_blend_config(config)
        if bool(blend["enabled"]) and float(blend["model_weight"]) > 0:
            return {
                key: round(
                    float(blend["monte_carlo_weight"]) * mc_scaled[key]
                    + float(blend["model_weight"]) * model_scaled[key],
                    1,
                )
                for key in ("quartas", "semifinal", "final", "titulo")
            }
        return mc_scaled
    return model_scaled


def _stage_probability_source(config: dict[str, Any]) -> str:
    if config.get("stage_probabilities"):
        return "configured_stage_probabilities"
    monte_carlo_result = config.get("_monte_carlo_result")
    if (
        isinstance(monte_carlo_result, dict)
        and monte_carlo_result.get("enabled")
        and bool((config.get("monte_carlo") or {}).get("use_stage_probabilities", True))
    ):
        stages = monte_carlo_result.get("stage_probabilities") or {}
        required = ("quartas", "semifinal", "final", "titulo")
        if all(key in stages for key in required):
            blend = _stage_probability_blend_config(config)
            if bool(blend["enabled"]) and float(blend["model_weight"]) > 0:
                return _stage_probability_blend_label(config)
            return "monte_carlo_reconciled_funnel"
        if stages:
            blend = _stage_probability_blend_config(config)
            if bool(blend["enabled"]) and float(blend["model_weight"]) > 0:
                return _stage_probability_blend_label(config) + "_partial"
            return "monte_carlo_partial_agent_scaled_fallback"
    return "agent_scaled_fallback"


def _normalized_ascii_text(value: Any) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()


_MARKET_TITLE_PERCENT_RE = re.compile(r"(?<!\d)(\d{1,2}(?:[,.]\d+)?)\s*%")
_MARKET_TITLE_RANGE_RE = re.compile(
    r"(?<!\d)(\d{1,2}(?:[,.]\d+)?)\s*[-–]\s*(\d{1,2}(?:[,.]\d+)?)\s*%"
)
_MARKET_TITLE_TERMS = (
    "mercado",
    "market",
    "odds",
    "outright",
    "sportsbook",
    "bookmaker",
    "betfair",
    "polymarket",
    "prediction market",
    "de-vig",
    "devig",
)
_TITLE_CONTEXT_TERMS = (
    "titulo",
    "title",
    "hexa",
    "campeao",
    "campeonato",
    "winner",
    "levanta a taca",
)
_MODEL_TITLE_DENY_TERMS = (
    "modelo principal",
    "monte carlo",
    "mc",
    "funil",
    "simulacao",
    "simulation",
    "modelo",
    "model",
)


def _market_title_challenge_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("market_title_challenge")
    configured = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(configured.get("enabled", True)),
        "absolute_gap_pct": float(configured.get("absolute_gap_pct", 3.0)),
        "relative_gap_pct": float(configured.get("relative_gap_pct", 0.40)),
        "min_pct": float(configured.get("min_pct", 1.0)),
        "max_pct": float(configured.get("max_pct", 25.0)),
        "max_evidence_items": int(configured.get("max_evidence_items", 4)),
        "robust_min_candidates": int(configured.get("robust_min_candidates", 5)),
        "robust_low_quantile": float(configured.get("robust_low_quantile", 0.20)),
        "robust_high_quantile": float(configured.get("robust_high_quantile", 0.80)),
    }


def _iter_market_title_texts(meeting_transcript: list[dict[str, Any]]) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    for turn in meeting_transcript or []:
        if not isinstance(turn, dict):
            continue
        round_label = str(turn.get("round") or turn.get("round_index") or "?")
        for key in ("question", "protagonist_question", "prompt"):
            value = str(turn.get(key) or "").strip()
            if value:
                texts.append((f"rodada {round_label} pergunta", value))
        for response in turn.get("responses", []) or []:
            if not isinstance(response, dict):
                continue
            if bool(response.get("removed_from_main", False)):
                continue
            agent = str(response.get("agent") or "modelo").strip() or "modelo"
            for key in ("answer", "summary", "rationale"):
                value = str(response.get(key) or "").strip()
                if value:
                    texts.append((f"rodada {round_label} {agent}", value))
    return texts


def _market_title_candidate_is_model_reference(raw_text: str, start: int, end: int) -> bool:
    prefix = _normalized_ascii_text(raw_text[max(0, start - 80) : start])
    latest_model = max((prefix.rfind(term) for term in _MODEL_TITLE_DENY_TERMS), default=-1)
    latest_market = max((prefix.rfind(term) for term in _MARKET_TITLE_TERMS), default=-1)
    if latest_model != -1 and latest_model > latest_market:
        return True

    suffix = raw_text[end : min(len(raw_text), end + 100)]
    suffix_stop_candidates = [
        position
        for separator in ",.;!?\n"
        if (position := suffix.find(separator)) != -1
    ]
    suffix_stop = min(suffix_stop_candidates) if suffix_stop_candidates else len(suffix)
    attached_suffix = _normalized_ascii_text(suffix[:suffix_stop])
    return any(term in attached_suffix for term in _MODEL_TITLE_DENY_TERMS)


def _market_title_values_from_text(text: str, *, config: dict[str, Any]) -> list[float]:
    settings = _market_title_challenge_config(config)
    values: list[float] = []
    raw_text = str(text or "")
    normalized = _normalized_ascii_text(raw_text)
    if not any(term in normalized for term in _MARKET_TITLE_TERMS):
        return values
    if not any(term in normalized for term in _TITLE_CONTEXT_TERMS):
        return values
    processed_clauses: set[tuple[int, int]] = set()
    for match in _MARKET_TITLE_PERCENT_RE.finditer(raw_text):
        clause_start = max(raw_text.rfind(separator, 0, match.start()) for separator in ".;!?\n") + 1
        clause_end_candidates = [
            position
            for separator in ".;!?\n"
            if (position := raw_text.find(separator, match.end())) != -1
        ]
        clause_end = min(clause_end_candidates) if clause_end_candidates else len(raw_text)
        if (clause_start, clause_end) in processed_clauses:
            continue
        processed_clauses.add((clause_start, clause_end))
        clause = raw_text[clause_start:clause_end]
        clause_normalized = _normalized_ascii_text(clause)
        if not any(term in clause_normalized for term in _MARKET_TITLE_TERMS):
            continue
        if not any(term in clause_normalized for term in _TITLE_CONTEXT_TERMS):
            continue
        consumed_spans: list[tuple[int, int]] = []
        for range_match in _MARKET_TITLE_RANGE_RE.finditer(clause):
            range_start = clause_start + range_match.start()
            range_end = clause_start + range_match.end()
            if _market_title_candidate_is_model_reference(raw_text, range_start, range_end):
                continue
            range_prefix = _normalized_ascii_text(raw_text[clause_start:range_start])
            if not any(term in range_prefix for term in _MARKET_TITLE_TERMS):
                continue
            try:
                low_value = float(range_match.group(1).replace(",", "."))
                high_value = float(range_match.group(2).replace(",", "."))
            except ValueError:
                continue
            for value in sorted((low_value, high_value)):
                if settings["min_pct"] <= value <= settings["max_pct"]:
                    values.append(round(value, 1))
            consumed_spans.append((range_start, range_end))
        for percent_match in _MARKET_TITLE_PERCENT_RE.finditer(clause):
            percent_start = clause_start + percent_match.start()
            percent_end = clause_start + percent_match.end()
            if any(start <= percent_start < end for start, end in consumed_spans):
                continue
            try:
                value = float(percent_match.group(1).replace(",", "."))
            except ValueError:
                continue
            if not (settings["min_pct"] <= value <= settings["max_pct"]):
                continue
            if _market_title_candidate_is_model_reference(raw_text, percent_start, percent_end):
                continue
            prefix = _normalized_ascii_text(raw_text[clause_start:percent_start])
            if not any(term in prefix for term in _MARKET_TITLE_TERMS):
                continue
            values.append(round(value, 1))
    return values


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    bounded_q = max(0.0, min(1.0, float(quantile)))
    if len(ordered) == 1:
        return ordered[0]
    position = bounded_q * (len(ordered) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _market_title_band_from_candidates(candidates: list[float], settings: dict[str, Any]) -> tuple[float, float, str]:
    if not candidates:
        return 0.0, 0.0, "none"
    minimum_for_robust = max(1, int(settings.get("robust_min_candidates", 5)))
    if len(candidates) >= minimum_for_robust:
        low = _percentile(candidates, float(settings.get("robust_low_quantile", 0.20)))
        high = _percentile(candidates, float(settings.get("robust_high_quantile", 0.80)))
        return round(low, 1), round(max(low, high), 1), "robust_percentile"
    return round(min(candidates), 1), round(max(candidates), 1), "min_max"


def _market_title_challenge(
    stage_probabilities: dict[str, float],
    meeting_transcript: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    settings = _market_title_challenge_config(config)
    model_title_pct = round(float(stage_probabilities.get("titulo", 0.0) or 0.0), 1)
    base = {
        "enabled": settings["enabled"],
        "triggered": False,
        "status": "disabled" if not settings["enabled"] else "no_market_signal",
        "decision": "mantem_funil_60_40",
        "model_title_pct": model_title_pct,
        "market_low_pct": None,
        "market_high_pct": None,
        "market_mid_pct": None,
        "absolute_gap_pct": None,
        "relative_gap_pct": None,
        "threshold_absolute_gap_pct": settings["absolute_gap_pct"],
        "threshold_relative_gap_pct": settings["relative_gap_pct"],
        "evidence": [],
    }
    if not settings["enabled"]:
        return base

    candidates: list[float] = []
    evidence: list[dict[str, str]] = []
    for label, text in _iter_market_title_texts(meeting_transcript):
        values = _market_title_values_from_text(text, config=config)
        if not values:
            continue
        candidates.extend(values)
        if len(evidence) < settings["max_evidence_items"]:
            snippet = re.sub(r"\s+", " ", str(text or "").strip())
            evidence.append({"source": label, "snippet": snippet[:260]})

    if not candidates:
        base["evidence"] = evidence
        return base

    market_low, market_high, band_method = _market_title_band_from_candidates(candidates, settings)
    market_mid = round((market_low + market_high) / 2.0, 1)
    absolute_gap = round(abs(market_mid - model_title_pct), 1)
    relative_gap = round(absolute_gap / max(0.1, model_title_pct), 2)
    triggered = (
        absolute_gap >= settings["absolute_gap_pct"]
        and relative_gap >= settings["relative_gap_pct"]
    )
    return {
        **base,
        "triggered": triggered,
        "status": "challenged" if triggered else "within_threshold",
        "decision": "mantem_funil_60_40_mercado_como_desafio" if triggered else "mantem_funil_60_40",
        "market_low_pct": market_low,
        "market_high_pct": market_high,
        "market_mid_pct": market_mid,
        "market_band_method": band_method,
        "market_candidate_count": len(candidates),
        "absolute_gap_pct": absolute_gap,
        "relative_gap_pct": relative_gap,
        "evidence": evidence,
    }


def _market_title_challenge_warning(challenge: dict[str, Any]) -> str:
    if not bool(challenge.get("triggered")):
        return ""
    model = float(challenge.get("model_title_pct") or 0.0)
    low = float(challenge.get("market_low_pct") or 0.0)
    high = float(challenge.get("market_high_pct") or low)
    market = f"{low:.1f}%-{high:.1f}%" if abs(high - low) >= 0.05 else f"{low:.1f}%"
    return (
        "Mercado desafia o funil final 60/40: "
        f"modelo={model:.1f}%, mercado={market}; número principal mantido pelo funil e divergência exposta."
    )


def _bounded_confidence_level(value: Any, *, default: float = 0.95) -> float:
    try:
        confidence_level = float(value)
    except (TypeError, ValueError):
        confidence_level = float(default)
    return max(0.5, min(0.999, confidence_level))


def _uncertainty_config(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("uncertainty")
    return raw if isinstance(raw, dict) else {}


def _confidence_level_for_report(config: dict[str, Any]) -> float:
    uncertainty = _uncertainty_config(config)
    if "confidence_level" in uncertainty:
        return _bounded_confidence_level(uncertainty.get("confidence_level"))
    monte_carlo = config.get("monte_carlo")
    if isinstance(monte_carlo, dict) and "confidence_level" in monte_carlo:
        return _bounded_confidence_level(monte_carlo.get("confidence_level"))
    return 0.95


def _z_for_confidence_level(confidence_level: float) -> float:
    bounded = _bounded_confidence_level(confidence_level)
    return NormalDist().inv_cdf(0.5 + bounded / 2.0)


def _student_t_critical(confidence_level: float, df: int) -> float:
    z = _z_for_confidence_level(confidence_level)
    if df <= 0:
        return z
    if df == 1:
        if confidence_level >= 0.99:
            return 63.657
        if confidence_level >= 0.95:
            return 12.706
    nu = float(df)
    # Cornish-Fisher expansion: close enough for uncertainty widening; exactness is less important than honesty.
    return z + (z**3 + z) / (4.0 * nu) + (5.0 * z**5 + 16.0 * z**3 + 3.0 * z) / (96.0 * nu**2)


def _logit_probability(probability_pct: float) -> float:
    p = max(0.001, min(0.999, float(probability_pct) / 100.0))
    return math.log(p / (1.0 - p))


def _inverse_logit_pct(value: float) -> float:
    return 100.0 / (1.0 + math.exp(-value))


def _interval_midpoint(interval: tuple[float, float]) -> float:
    return (float(interval[0]) + float(interval[1])) / 2.0


def _interval_logit_variance(
    interval: tuple[float, float],
    *,
    confidence_level: float,
) -> float | None:
    low, high = float(interval[0]), float(interval[1])
    if low >= high:
        return None
    z = _z_for_confidence_level(confidence_level)
    if z <= 0:
        return None
    low_logit = _logit_probability(low)
    high_logit = _logit_probability(high)
    sigma = abs(high_logit - low_logit) / (2.0 * z)
    if sigma <= 0:
        return None
    return sigma * sigma


def _combine_probability_intervals_logit(
    *,
    center_pct: float,
    sources: list[tuple[str, tuple[float, float]]],
    confidence_level: float,
    location_gap_fallback_pct: float,
) -> tuple[tuple[float, float], dict[str, Any]]:
    clean_sources: list[tuple[str, tuple[float, float]]] = []
    for label, interval in sources:
        try:
            low, high = float(interval[0]), float(interval[1])
        except (TypeError, ValueError, IndexError):
            continue
        low = max(0.0, min(100.0, low))
        high = max(0.0, min(100.0, high))
        if low > high:
            low, high = high, low
        if high - low <= 0:
            continue
        clean_sources.append((label, (low, high)))
    if not clean_sources:
        return (
            (round(max(0.0, min(100.0, center_pct)), 1), round(max(0.0, min(100.0, center_pct)), 1)),
            {"method": "point_only", "sources": []},
        )

    source_centers = [_interval_midpoint(interval) for _, interval in clean_sources]
    max_location_gap = max(abs(center - float(center_pct)) for center in source_centers)
    envelope = (
        round(max(0.0, min(float(center_pct), *(interval[0] for _, interval in clean_sources))), 1),
        round(min(100.0, max(float(center_pct), *(interval[1] for _, interval in clean_sources))), 1),
    )
    if max_location_gap > location_gap_fallback_pct:
        return envelope, {
            "method": "envelope_fallback",
            "fallback_reason": "location_gap",
            "max_location_gap_pct": round(max_location_gap, 2),
            "location_gap_fallback_pct": round(float(location_gap_fallback_pct), 2),
            "sources": [label for label, _ in clean_sources],
        }

    variances = [
        variance
        for _, interval in clean_sources
        if (variance := _interval_logit_variance(interval, confidence_level=confidence_level)) is not None
    ]
    if not variances:
        return envelope, {
            "method": "envelope_fallback",
            "fallback_reason": "no_valid_variance",
            "sources": [label for label, _ in clean_sources],
        }
    z = _z_for_confidence_level(confidence_level)
    center_logit = _logit_probability(center_pct)
    sigma = math.sqrt(sum(variances))
    combined = (
        round(max(0.0, min(float(center_pct), _inverse_logit_pct(center_logit - z * sigma))), 1),
        round(min(100.0, max(float(center_pct), _inverse_logit_pct(center_logit + z * sigma))), 1),
    )
    return combined, {
        "method": "logit_variance_sum",
        "fallback_reason": "",
        "max_location_gap_pct": round(max_location_gap, 2),
        "sources": [label for label, _ in clean_sources],
        "variance_count": len(variances),
    }


def _model_probability_interval_pct(
    probability_samples: list[float],
    *,
    center_pct: float,
    confidence_level: float,
) -> tuple[float, float] | None:
    samples: list[float] = []
    for value in probability_samples:
        try:
            probability = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 < probability < 100.0:
            samples.append(probability)
    if len(samples) < 2:
        return None
    logits = [_logit_probability(value) for value in samples]
    mean = sum(logits) / len(logits)
    variance = sum((value - mean) ** 2 for value in logits) / max(1, len(logits) - 1)
    if variance <= 0:
        return None
    standard_error = math.sqrt(variance) / math.sqrt(len(logits))
    critical = _student_t_critical(confidence_level, len(logits) - 1)
    low = min(_inverse_logit_pct(mean - critical * standard_error), float(center_pct))
    high = max(_inverse_logit_pct(mean + critical * standard_error), float(center_pct))
    return round(max(0.0, low), 1), round(min(100.0, high), 1)


def _title_samples_for_uncertainty(opinions: list[AgentOpinion]) -> list[float]:
    samples: list[float] = []
    for opinion in opinions:
        if bool(getattr(opinion, "removed_from_main", False)):
            continue
        summary = str(getattr(opinion, "summary", "") or "").lower()
        if "resposta removida" in summary or "fallback operacional" in summary:
            continue
        if getattr(opinion, "used_fallback", False) and not (
            getattr(opinion, "source_urls", None) or getattr(opinion, "source_queries", None)
        ):
            continue
        try:
            samples.append(float(opinion.title_pct))
        except (TypeError, ValueError):
            continue
    return samples


def _report_uncertainty_metadata(config: dict[str, Any]) -> dict[str, Any]:
    uncertainty = _uncertainty_config(config)
    monte_carlo = config.get("monte_carlo") if isinstance(config.get("monte_carlo"), dict) else {}
    confidence_level = _confidence_level_for_report(config)
    return {
        "confidence_level": confidence_level,
        "minimum_declared_coverage": _bounded_confidence_level(
            uncertainty.get("minimum_declared_coverage", 0.95),
            default=0.95,
        ),
        "model_dispersion_method": str(uncertainty.get("model_dispersion_method", "heuristic_dispersion")),
        "rating_uncertainty_enabled": bool(monte_carlo.get("rating_uncertainty_enabled", False)),
    }


def _team_context_warning_messages(monte_carlo_result: dict[str, Any]) -> list[str]:
    team_context = monte_carlo_result.get("team_context")
    if not isinstance(team_context, dict):
        return []
    warnings = team_context.get("warnings")
    if not isinstance(warnings, list):
        return []
    messages: list[str] = []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        team = str(warning.get("team", "") or "").strip()
        reason = str(warning.get("reason", "") or "").strip()
        try:
            rating_delta = float(warning.get("rating_delta"))
        except (TypeError, ValueError):
            continue
        try:
            threshold = float(warning.get("threshold"))
        except (TypeError, ValueError):
            threshold = 0.0
        if not team:
            continue
        if reason == "team_context_delta_above_warning_threshold":
            threshold_text = f"{threshold:.1f}" if threshold > 0 else "configurado"
            messages.append(
                "Ajuste contextual fora da faixa de revisão: "
                f"{team} teve {rating_delta:+.1f} pontos de rating, acima do limiar {threshold_text}; "
                "validar se há dupla contagem ou reação excessiva antes de publicar."
            )
        else:
            messages.append(
                f"Aviso de contexto do Monte Carlo para {team}: {reason or 'sem razão informada'} "
                f"({rating_delta:+.1f} pontos de rating)."
            )
    return messages


def _stage_confidence_intervals(
    probabilities: dict[str, float],
    *,
    dispersion_pct: float,
    warning_count: int,
    config: dict[str, Any],
    model_title_pcts: list[float] | None = None,
) -> dict[str, tuple[float, float]]:
    configured = config.get("stage_confidence_intervals")
    if configured:
        return {key: (round(float(value[0]), 1), round(float(value[1]), 1)) for key, value in configured.items()}

    confidence_level = _confidence_level_for_report(config)
    level_multiplier = _z_for_confidence_level(confidence_level) / _z_for_confidence_level(0.95)
    base_width = min(28.0, max(5.0, 4.0 + dispersion_pct * 0.8 + warning_count * 1.3) * level_multiplier)
    intervals: dict[str, tuple[float, float]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    location_gap_fallback_pct = float(
        _uncertainty_config(config).get("logit_variance_location_gap_fallback_pct", 12.0)
    )
    monte_carlo_result = config.get("_monte_carlo_result")
    mc_stage_uncertainty = {}
    if isinstance(monte_carlo_result, dict) and monte_carlo_result.get("enabled"):
        mc_stage_uncertainty = monte_carlo_result.get("stage_uncertainty_intervals") or {}
    mc_phase_by_key = {
        "quartas": "Quartas",
        "semifinal": "Semifinal",
        "final": "Final",
    }
    for key, value in probabilities.items():
        stage_multiplier = {
            "quartas": 1.10,
            "semifinal": 1.05,
            "final": 1.00,
            "titulo": 0.90,
        }.get(key, 1.0)
        width = base_width * stage_multiplier
        source_intervals: list[tuple[str, tuple[float, float]]] = [
            (
                "operational_base",
                (
                    round(max(0.0, value - width / 2), 1),
                    round(min(100.0, value + width / 2), 1),
                ),
            )
        ]
        if isinstance(monte_carlo_result, dict) and monte_carlo_result.get("enabled"):
            phase_payload = (monte_carlo_result.get("phases") or {}).get(mc_phase_by_key.get(key, ""))
            if isinstance(phase_payload, dict) and phase_payload.get("reach_ci"):
                mc_low, mc_high = phase_payload["reach_ci"]
                source_intervals.append(("monte_carlo_sampling", (float(mc_low), float(mc_high))))
            if isinstance(mc_stage_uncertainty, dict) and key in mc_stage_uncertainty:
                mc_low, mc_high = mc_stage_uncertainty[key]
                source_intervals.append(("monte_carlo_epistemic", (float(mc_low), float(mc_high))))
        if key == "titulo" and (_uncertainty_config(config).get("model_dispersion_method") == "logit_student_t"):
            model_interval = _model_probability_interval_pct(
                model_title_pcts or [],
                center_pct=value,
                confidence_level=confidence_level,
            )
            if model_interval:
                source_intervals.append(("model_dispersion_t", model_interval))
        combined, stage_metadata = _combine_probability_intervals_logit(
            center_pct=value,
            sources=source_intervals,
            confidence_level=confidence_level,
            location_gap_fallback_pct=location_gap_fallback_pct,
        )
        intervals[key] = combined
        metadata[key] = stage_metadata
    config["_stage_interval_metadata"] = metadata
    return intervals


def _quantitative_qualitative_decision_instruction() -> str:
    return (
        "Contrato de decisão quantitativa/qualitativa: números importam e devem sustentar decisões racionais, "
        "mas análise quantitativa e análise qualitativa são ambas necessárias. Use dados quantitativos e qualitativos "
        "para construir uma hipótese auditável que os outros modelos consigam aceitar ou contestar. "
        "Não declare nem use percentual, razão, quota ou peso metodológico para dividir quanti e quali; "
        "percentuais devem aparecer apenas como probabilidades de jogos, cenários, fases ou título. "
        "Use estatística, odds, prediction markets e ratings como uma "
        "das bases possíveis; use fatos, informações verificáveis, análises de especialistas, "
        "lesões, cortes, cartões, arbitragem/VAR, descanso, forma e performance de jogadores para capturar fatores "
        "que os números agregados não mostram bem. Isso é comum no futebol: o qualitativo não substitui os números, "
        "mas pode explicar assimetrias, injustiças de resultado e mudanças recentes que ainda não entraram no preço. "
        "Se Transfermarkt trouxer atualização curta ou quase em tempo real de valor de mercado, use como sinal de "
        "momentum de elenco para Brasil e adversários/cenários hipotéticos: conte quantos jogadores subiram, mas "
        "pondere a influência pelo delta nominal em euros e pela variação percentual, com o nominal dominando o "
        "percentual isolado. Exemplo obrigatório de regra: 50M->55M pesa mais que 10M->13M, apesar do segundo ter "
        "percentual maior."
    )


def _self_identification_instruction() -> str:
    return (
        "Identificação dinâmica obrigatória: inclua self_identification no JSON com name e version "
        "declarados por você mesmo neste run. Não copie automaticamente o nome do slot nem o modelo configurado; "
        "se você não souber sua versão exata, declare a versão como 'não declarado'. "
    )


def _meeting_full_path_instruction(config: dict[str, Any]) -> str:
    group_opponents = ", ".join(
        str(match.get("opponent", "")).strip()
        for match in _default_group_matches(config)
        if str(match.get("opponent", "")).strip()
    )
    phases = ", ".join(
        dict.fromkeys(
            str(match.get("phase", "")).strip()
            for match in _default_knockout_matches(config)
            if str(match.get("phase", "")).strip()
        )
    )
    return (
        "Cobertura obrigatória antes de encerrar consenso: mantenha Brasil e adversários/cenários configurados "
        f"em foco, cubra grupo ({group_opponents}), mata-mata ({phases}) e chance de título. "
        "Se o adversário do mata-mata estiver 'a definir', use exatamente o rótulo do JSON; não invente país. "
        "Não use códigos de chaveamento, países, grupos ou adversários que não estejam no JSON configurado. "
        "Nunca responda com metacomentário operacional sobre rodadas mínimas, cobertura incompleta ou modelos ruins; "
        "se faltar cobertura, formule a próxima pergunta objetiva sobre a fase faltante. "
    )


def _agent_prompt(
    *,
    config: dict[str, Any],
    evidence: list[EvidenceResult],
    generated_at: datetime,
) -> str:
    matches = _configured_matches_for_prompt(config)
    macro_direction = config.get(
        "macro_direction",
        "Brasil na Copa do Mundo de 2026: números e contexto como direcional inicial, sem viés pró-Brasil.",
    )
    return (
        "Você é um dos modelos de debate para um relatório LinkedIn sobre até onde o Brasil pode ir "
        "na Copa do Mundo de 2026. Você está construindo o Modelo Principal. Use apenas fontes de mercado, "
        "ratings independentes, desempenho de jogadores, arbitragem e contexto de elenco. Responda em JSON estrito com as chaves: "
        "self_identification (objeto com name e version), title_pct (número percentual), summary (até 450 caracteres), opening_argument, "
        "critique, adjustment, risks (lista curta), upside (lista curta), source_urls (lista de URLs), "
        "source_queries (lista de buscas), team_context_signals (lista). Antes de estimar, escolha as fontes que você usaria dentro do "
        "direcionamento macro; priorize sportsbooks, prediction markets, ratings, notícias de elenco/lesão "
        "performance dos jogadores via Sofascore, avaliação de arbitragem/VAR/cartões/disciplina e simulações públicas independentes. "
        f"{_agent_owned_fresh_search_contract()} "
        "Não use torcida. Use dados quantitativos e qualitativos conforme a força das fontes encontradas, "
        "sem declarar percentual de divisão metodológica entre eles. "
        f"{_opponent_research_instruction(config)} "
        f"{_quantitative_qualitative_decision_instruction()} "
        f"{_meeting_full_path_instruction(config)} "
        f"{_event_impact_prompt_instruction()} "
        f"{_self_identification_instruction()} "
        f"{_effort_latency_instruction()} "
        "Reconheça incerteza quando fonte externa falhar. Escreva em linguagem de LinkedIn, entendível "
        "para gente que gosta de futebol e dados, mas explique os métodos usados sem jargão desnecessário.\n\n"
        f"Data do run: {generated_at.isoformat()}\n"
        f"Direcionamento macro: {macro_direction}\n"
        f"Jogos/cenários configurados: {json.dumps(matches, ensure_ascii=False)}\n"
    )


def _source_planning_prompt(*, config: dict[str, Any], generated_at: datetime) -> str:
    macro_direction = config.get(
        "macro_direction",
        "Brasil na Copa do Mundo de 2026: números e contexto como direcional inicial, sem viés pró-Brasil.",
    )
    scope = _compact_source_planning_scope(config)
    scope_json = json.dumps(scope, ensure_ascii=False, separators=(",", ":"))
    return (
        "Planeje fontes frescas para a reunião de modelos do Brasil na Copa 2026; ainda não feche consenso. "
        "JSON estrito: self_identification{name,version}, title_pct, summary, opening_argument, critique, adjustment, source_urls, source_queries, team_context_signals. "
        f"{_agent_owned_fresh_search_contract()} "
        f"{_effort_latency_instruction()} "
        "Números importam; combine análise quantitativa e análise qualitativa, fatos, especialistas e futebol. "
        "Use dados quantitativos e qualitativos para formular hipótese auditável, com número, fonte/query e efeito em probabilidade; não declare percentual, razão ou peso metodológico para dividir quanti e quali. "
        "Pesquisa simétrica: Brasil e adversários/cenários, mesmas famílias de fontes: mercados/odds, Elo/FIFA/ratings, Sofascore/performance, lesões/cortes/cartões, arbitragem/VAR, descanso, chaveamento, elenco. "
        "Para team_context_signals, traga sinais por seleção, inclusive adversários prováveis, com team, category, rating_delta ou probability_delta_pct, confidence, rationale e source_url/source_query. "
        "Famílias válidas incluem bets/prediction markets, ratings, Sofascore/performance, lesões/cortes/notícias recentes, amistosos recentes, arbitragem/VAR/cartões e opinião de imprensa especializada. "
        "Sem fonte auditável ou sem delta numérico, o sinal será ignorado pelo Monte Carlo. "
        "Monte Carlo: quando o escopo trouxer monte_carlo.enabled=true, trate a simulação como insumo quantitativo auditável "
        "para scenario_probabilities, adversários prováveis, caminho e IC; aceite ou conteste com fonte/query melhor. "
        "Transfermarkt/valor de mercado: delta nominal + variação; delta nominal domina percentual isolado; 50M->55M pesa mais que 10M->13M. "
        "Eventos: recent_event_impacts/event_impact_scenarios com os mesmos critérios da fase de grupos até a Final; exigir date, team, category, summary, source_url/source_query, brazil_shift_pct, scenario_shift_pct, confidence. "
        "Não use benchmark reservado do comparativo separado nesta sala. Não invente fonte, URL, score, rating, método, adversário ou efeito. Identificação dinâmica: self_identification name/version declarados; se versão incerta, 'não declarado'. "
        "Use só o escopo abaixo; linguagem auditável e entendível para LinkedIn.\n\n"
        f"Data do run: {generated_at.isoformat()}\n"
        f"Direção: {macro_direction}\n"
        f"Escopo compacto: {scope_json}\n"
    )


def _sources_from_agent_opinions(
    opinions: list[Any],
    *,
    max_sources: int,
) -> list[EvidenceSource]:
    sources: list[EvidenceSource] = []
    seen: set[str] = set()
    for opinion in opinions:
        for url in getattr(opinion, "source_urls", []):
            if not _valid_http_url(url) or url in seen or _has_opta_marker(url):
                continue
            seen.add(url)
            host = urllib.parse.urlparse(url).netloc
            if _has_opta_marker(host):
                continue
            sources.append(
                EvidenceSource(
                    name=f"{opinion.agent} selected: {host}",
                    category=_category_from_url(url),
                    url=url,
                    confidence=0.64 if not getattr(opinion, "used_fallback", False) else 0.35,
                    notes=f"Fonte escolhida pelo modelo {opinion.agent} na rodada de planejamento.",
                )
            )
            if len(sources) >= max_sources:
                return sources
    return sources


def _source_plan_by_model(opinions: list[Any]) -> dict[str, dict[str, list[str]]]:
    return {
        opinion.agent: {
            "source_urls": [url for url in getattr(opinion, "source_urls", []) if not _has_opta_marker(url)],
            "source_queries": [query for query in getattr(opinion, "source_queries", []) if not _has_opta_marker(query)],
            "excluded_opta_items": [
                item
                for item in [*getattr(opinion, "source_urls", []), *getattr(opinion, "source_queries", [])]
                if _has_opta_marker(item)
            ],
        }
        for opinion in opinions
    }


def _reported_source_labels_from_agent_opinions(opinions: list[Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for opinion in opinions:
        agent = str(getattr(opinion, "agent", "Modelo")).strip() or "Modelo"
        for url in getattr(opinion, "source_urls", []) or []:
            rendered = str(url).strip()
            if not _valid_http_url(rendered) or _has_opta_marker(rendered):
                continue
            label = f"{agent}: {rendered}"
            if label not in seen:
                seen.add(label)
                labels.append(label)
        for query in getattr(opinion, "source_queries", []) or []:
            rendered = str(query).strip()
            if not rendered or _has_opta_marker(rendered):
                continue
            label = f"{agent} busca: {rendered}"
            if label not in seen:
                seen.add(label)
                labels.append(label)
    return labels


LOW_AUTHORITY_SOURCE_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "instagram.com",
    "capcut.com",
    "tiktok.com",
)


def _is_low_authority_public_source(label_or_url: str) -> bool:
    text = str(label_or_url or "").strip()
    candidate = text.rsplit(" ", 1)[-1]
    if "://" not in candidate and ": " in text:
        candidate = text.rsplit(": ", 1)[-1]
    parsed = urllib.parse.urlparse(candidate)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return any(host == domain or host.endswith("." + domain) for domain in LOW_AUTHORITY_SOURCE_DOMAINS)


def _low_authority_public_source_labels(labels: list[str]) -> list[str]:
    return [label for label in labels if _is_low_authority_public_source(label)]


def _reported_event_source_labels(config: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for event in _recent_event_impacts(config):
        summary = _event_summary(event)
        source_url = str(event.get("source_url") or "").strip()
        source_query = str(event.get("source_query") or "").strip()
        if source_url and _valid_http_url(source_url):
            label = f"Evento recente: {summary} - {source_url}"
        elif source_query:
            label = f"Evento recente busca: {summary} - {source_query}"
        else:
            continue
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


COMMON_NATIONAL_TEAM_MARKERS = {
    "africa do sul",
    "algeria",
    "alemanha",
    "argentina",
    "arabia saudita",
    "australia",
    "austria",
    "belgica",
    "bosnia e herzegovina",
    "cabo verde",
    "canada",
    "catar",
    "chile",
    "colombia",
    "coreia do sul",
    "costa do marfim",
    "croacia",
    "curacau",
    "egito",
    "equador",
    "espanha",
    "estados unidos",
    "eua",
    "franca",
    "gana",
    "holanda",
    "ira",
    "iraque",
    "inglaterra",
    "italia",
    "japao",
    "jordania",
    "marrocos",
    "marrocos",
    "mexico",
    "nigeria",
    "noruega",
    "nova zelandia",
    "panama",
    "paraguai",
    "portugal",
    "rd congo",
    "servia",
    "senegal",
    "suecia",
    "suica",
    "tchequia",
    "tunisia",
    "turquia",
    "uzbequistao",
    "uruguai",
    "haiti",
    "escocia",
}


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _configured_matches_for_prompt(config: dict[str, Any]) -> dict[str, Any]:
    monte_carlo_summary = monte_carlo_compact_summary(config.get("_monte_carlo_result", {"enabled": False}))
    return {
        "group_matches": _default_group_matches(config),
        "completed_group_matches": list(config.get("completed_group_matches", []) or []),
        "knockout_matches": _default_knockout_matches(config),
        "monte_carlo": monte_carlo_summary,
        "path_phase_relevant_groups": config.get("_path_phase_relevant_groups")
        or monte_carlo_summary.get("phase_relevant_groups", {}),
        "path_relevant_group_states": config.get("_path_relevant_group_states")
        or monte_carlo_summary.get("relevant_group_states", {}),
        "parallel_opponent_briefing": config.get("_parallel_opponent_briefing", {}),
        "recent_event_impacts": _recent_event_impacts(config),
        "event_impact_criteria": _event_impact_criteria_for_prompt(),
        "event_impact_scenarios": _event_impact_scenarios_for_prompt(config),
    }


def _compact_dict(raw: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in keys:
        value = raw.get(key)
        if value is None or value == "":
            continue
        compact[key] = value
    return compact


def _compact_group_match_label(match: dict[str, Any]) -> str:
    parts = [
        str(match.get("date") or "data?"),
        str(match.get("opponent") or "adversário?"),
        str(match.get("venue") or "local?"),
    ]
    if match.get("brazil_pct") is not None:
        parts.append(f"BR{_compact_pct(match.get('brazil_pct'))}")
    if match.get("draw_pct") is not None:
        parts.append(f"E{_compact_pct(match.get('draw_pct'))}")
    return " ".join(parts)


def _compact_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return f"{int(number)}%"
    return f"{number:.1f}%"


def _compact_opponent_label(opponent: Any) -> str:
    text = str(opponent or "adversário?").strip() or "adversário?"
    normalized = _normalize_text(text)
    if normalized.startswith("adversario mais provavel"):
        return "mais provável a definir"
    if normalized.startswith("segundo adversario mais provavel"):
        return "segunda opção a definir"
    return text


def _compact_knockout_match_label(match: dict[str, Any]) -> str:
    opponent = _compact_opponent_label(match.get("opponent"))
    option = "mp" if bool(match.get("most_likely", False)) else "2a"
    parts = [
        str(match.get("phase") or "Mata-mata"),
        option if opponent in {"mais provável a definir", "segunda opção a definir"} else f"{option}:{opponent}",
    ]
    if match.get("scenario_pct") is not None:
        parts.append(f"cen{_compact_pct(match.get('scenario_pct'))}")
    if match.get("brazil_pct") is not None:
        parts.append(f"BR{_compact_pct(match.get('brazil_pct'))}")
    if match.get("date") not in (None, "", "A definir"):
        parts.append(f"data={match.get('date')}")
    if match.get("venue") not in (None, "", "A definir"):
        parts.append(f"local={match.get('venue')}")
    return " ".join(parts)


def _compact_source_planning_scope(config: dict[str, Any]) -> dict[str, Any]:
    event_fields = _event_impact_criteria_for_prompt()["required_fields"]
    event_keys = [
        "id",
        "date",
        "team",
        "teams",
        "opponent",
        "opponents",
        "phase",
        "phases",
        "category",
        "summary",
        "source_url",
        "source_query",
        "brazil_shift_pct",
        "scenario_shift_pct",
        "confidence",
    ]
    event_scenarios = ["Fase de grupos"]
    for match in _default_knockout_matches(config):
        phase = str(match.get("phase") or "Mata-mata").strip() or "Mata-mata"
        if phase not in event_scenarios:
            event_scenarios.append(phase)
    bracket_path = [
        {
            "phase": entry.get("phase"),
            "match": entry.get("match_id"),
            "br_slot": entry.get("brazil_slot"),
            "opp_slots": entry.get("opponent_slots"),
            "opp_groups": entry.get("allowed_opponent_groups"),
            "candidates": entry.get("allowed_opponents"),
        }
        for entry in brazil_bracket_path(config)
    ]

    monte_carlo_summary = monte_carlo_compact_summary(config.get("_monte_carlo_result", {"enabled": False}))
    return {
        "group_matches": [_compact_group_match_label(match) for match in _default_group_matches(config)],
        "completed_group_matches": list(config.get("completed_group_matches", []) or []),
        "knockout_matches": [_compact_knockout_match_label(match) for match in _default_knockout_matches(config)],
        "bracket_path": bracket_path,
        "monte_carlo": monte_carlo_summary,
        "path_phase_relevant_groups": monte_carlo_summary.get("phase_relevant_groups", {}),
        "path_relevant_group_states": monte_carlo_summary.get("relevant_group_states", {}),
        "recent_event_impacts": [_compact_dict(event, event_keys) for event in _recent_event_impacts(config)],
        "event_impact_criteria": {
            "rule": "mesmos critérios da fase de grupos até a Final; não invente evento, fonte ou efeito",
            "required_fields": event_fields,
            "shift_units": "pontos percentuais",
            "simulation_guardrail": "eventos simulados são hipóteses auditáveis; sem source_url/source_query, shift 0",
        },
        "event_impact_scenarios": event_scenarios,
    }


def _configured_opponent_labels(config: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for match in _default_group_matches(config):
        opponent = str(match.get("opponent", "")).strip()
        if not opponent:
            continue
        key = _normalize_text(opponent)
        if key in seen:
            continue
        seen.add(key)
        labels.append(opponent)
    for match in _default_knockout_matches(config):
        opponent = str(match.get("opponent", "")).strip()
        if not opponent:
            continue
        key = _normalize_text(opponent)
        if key in seen:
            continue
        seen.add(key)
        phase = str(match.get("phase", "Mata-mata")).strip() or "Mata-mata"
        labels.append(f"{phase}: {opponent}")
    return labels


def _opponent_research_instruction(config: dict[str, Any]) -> str:
    labels = _configured_opponent_labels(config)
    rendered = ", ".join(labels) if labels else "adversários/cenários configurados"
    return (
        "Pesquisa simétrica obrigatória: cada modelo que participa da sala deve buscar informações atualizadas "
        f"para Brasil e adversários/cenários configurados ({rendered}), não só para o Brasil. "
        "Use as mesmas famílias de fontes que usa para o Brasil: bets/prediction markets, sportsbooks/prediction markets, ratings/Elo/rankings, "
        "Sofascore/performance de jogadores, Transfermarkt/valor de mercado/elenco, notícias de lesões/cortes/cartões, "
        "amistosos recentes, opinião de imprensa especializada, arbitragem/VAR/disciplina, descanso e chaveamento. "
        "Registre no JSON source_urls e source_queries com cobertura "
        "de Brasil e adversários; se o adversário estiver a definir, pesquise o cenário, bloco de força, chaveamento "
        "ou perfil provável em vez de inventar um país."
    )


def _meeting_scope_instruction(config: dict[str, Any]) -> str:
    matches = _configured_matches_for_prompt(config)
    return (
        "Escopo obrigatório dos jogos/cenários: "
        f"{json.dumps(matches, ensure_ascii=False)}. "
        "Não invente adversário fora desse JSON. Se o adversário de mata-mata estiver a definir, "
        "pergunte sobre cenário, rating efetivo, chaveamento ou bloco de força, não sobre um país específico não configurado."
    )


def _auditable_sources_instruction(config: dict[str, Any]) -> str:
    allowed_urls = [str(url) for url in config.get("_allowed_fact_source_urls", []) if str(url)]
    if not allowed_urls:
        return (
            "Regras de lastro factual: não invente fonte, score, rating, estudo ou método. "
            "Toda concordância/discordância precisa trazer hipótese racional, número testável e source_urls auditáveis; "
            "sem source_urls auditáveis, a fala será removida do consenso. "
            "Source_urls/source_queries só contam se a busca/fetch foi realmente executada nesta chamada; "
            "se a ferramenta externa estiver indisponível ou sem permissão, declare falha operacional e deixe fontes vazias."
        )
    rendered = ", ".join(allowed_urls[:10])
    return (
        "Regras de lastro factual: não invente fonte, score, rating, estudo ou método. "
        "Toda concordância/discordância precisa trazer hipótese racional, número testável e source_urls auditáveis; "
        "sem source_urls auditáveis, a fala será removida do consenso. "
        "Source_urls/source_queries só contam se a busca/fetch foi realmente executada nesta chamada; "
        "se a ferramenta externa estiver indisponível ou sem permissão, declare falha operacional e deixe fontes vazias. "
        f"Use preferencialmente URLs já fetchadas nesta rodada: {rendered}."
    )


def _group_opponents(config: dict[str, Any]) -> set[str]:
    return {_normalize_text(str(match["opponent"])) for match in _default_group_matches(config)}


def _has_explicit_match_scope(config: dict[str, Any]) -> bool:
    return bool(config.get("group_matches") or config.get("knockout_matches"))


def _configured_opponents(config: dict[str, Any]) -> set[str]:
    configured = {_normalize_text(str(match["opponent"])) for match in _default_group_matches(config)}
    for match in _default_knockout_matches(config):
        opponent = _normalize_text(str(match.get("opponent", "")))
        if opponent and "definir" not in opponent:
            configured.add(opponent)
        for candidate in match.get("allowed_opponents", []) or []:
            candidate_key = _normalize_text(str(candidate))
            if candidate_key:
                configured.add(candidate_key)
    return configured


def _mentioned_national_teams(text: str) -> set[str]:
    normalized = _normalize_text(text)
    return {team for team in COMMON_NATIONAL_TEAM_MARKERS if re.search(rf"\b{re.escape(team)}\b", normalized)}


_COMPLETED_MATCH_CONTEXT_MARKERS = (
    "depois dos jogos",
    "apos os jogos",
    "após os jogos",
    "depois do jogo",
    "apos o jogo",
    "após o jogo",
    "com o placar",
    "placar real",
    "resultado real",
    "jogo de ontem",
    "jogos do final de semana",
    "jogos ja disputados",
    "jogos já disputados",
)


def _completed_match_pairs_from_config(config: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for raw in config.get("completed_group_matches", []) or []:
        if not isinstance(raw, dict):
            continue
        team_a = str(raw.get("team_a") or raw.get("home") or raw.get("team1") or "").strip()
        team_b = str(raw.get("team_b") or raw.get("away") or raw.get("team2") or "").strip()
        if team_a and team_b:
            pairs.add(tuple(sorted((_normalize_text(team_a), _normalize_text(team_b)))))
    return pairs


def _match_pairs_mentioned(text: str) -> set[tuple[str, str]]:
    normalized = _normalize_text(text)
    teams = sorted(_mentioned_national_teams(text), key=len, reverse=True)
    pairs: set[tuple[str, str]] = set()
    for team_a in teams:
        for team_b in teams:
            if team_a >= team_b:
                continue
            direct = rf"\b{re.escape(team_a)}\b\s*(?:x|vs|v|contra)\s*\b{re.escape(team_b)}\b"
            reverse = rf"\b{re.escape(team_b)}\b\s*(?:x|vs|v|contra)\s*\b{re.escape(team_a)}\b"
            if re.search(direct, normalized) or re.search(reverse, normalized):
                pairs.add(tuple(sorted((team_a, team_b))))
    return pairs


def _unverified_completed_match_claim_detail(text: str, config: dict[str, Any]) -> str | None:
    normalized = _normalize_text(text)
    if not any(marker in normalized for marker in _COMPLETED_MATCH_CONTEXT_MARKERS):
        return None
    mentioned_pairs = _match_pairs_mentioned(text)
    if not mentioned_pairs:
        return None
    completed_pairs = _completed_match_pairs_from_config(config)
    missing = sorted(mentioned_pairs - completed_pairs)
    if not missing:
        return None
    rendered = ", ".join(f"{left} x {right}" for left, right in missing[:3])
    return f"jogo(s) tratado(s) como passado sem placar no ledger: {rendered}"



def _segment_is_generic_opponent_universe(segment: str) -> bool:
    normalized = _normalize_text(segment)
    generic_markers = (
        "todos os adversarios",
        "adversarios explicitamente configurados",
        "adversarios configurados",
        "cenarios configurados",
        "universo configurado",
        "json configurado",
        "configurados no json",
        "lista de adversarios",
        "source_urls",
        "source_queries",
        "fontes usadas",
        "plano de fontes",
    )
    return any(marker in normalized for marker in generic_markers)



def _mentions_unconfigured_opponent(text: str, config: dict[str, Any]) -> bool:
    if not _has_explicit_match_scope(config):
        return False
    allowed = _configured_opponents(config) | {"brasil"}
    mentioned = _mentioned_national_teams(text)
    return bool(mentioned - allowed)


def _mentions_unconfigured_group_opponent(question: str, config: dict[str, Any]) -> bool:
    if not config.get("group_matches"):
        return False
    normalized = _normalize_text(question)
    if not re.search(r"\b(grupo|fase de grupos)\b", normalized):
        return False
    allowed = _group_opponents(config) | {"brasil"}
    mentioned = _mentioned_national_teams(question)
    return bool(mentioned - allowed)


def _format_impossible_opponent_reason(detail: dict[str, Any] | None) -> str:
    base = "citar adversário impossível para o cruzamento oficial"
    if not isinstance(detail, dict):
        return base
    phase = str(detail.get("phase", "")).strip()
    invalid = ", ".join(str(item) for item in (detail.get("invalid_opponents") or [])[:4])
    allowed = ", ".join(str(item) for item in (detail.get("allowed_opponents") or [])[:8])
    if not phase or not invalid:
        return base
    suffix = f" ({phase}: citou {invalid}"
    if allowed:
        suffix += f"; permitidos: {allowed}"
    return base + suffix + ")"


MAX_PHASE_CLAIM_DISTANCE_CHARS = 280

_THIRD_PLACE_CONTEXT_MARKERS = (
    "3o lugar",
    "3º lugar",
    "terceiro lugar",
    "terceiros colocados",
    "melhores terceiros",
    "caminho de 3",
    "como terceiro",
)

_BRACKET_CLAIM_MARKERS = (
    "brasil x",
    " x brasil",
    " vs ",
    "contra o brasil",
    "enfrenta",
    "enfrentar",
    " pega ",
    " pegar ",
    "cruza",
    "cruzamento",
    "duelo",
    "confronto",
    "adversario",
    "candidato",
    "mais provavel",
    "rival",
)


def _mention_sentence_is_bracket_claim(normalized: str, position: int) -> bool:
    """Só fiscaliza menções dentro de uma frase que realmente alega confronto/caminho.

    Menção contextual ou histórica ('em 2022 a Croácia eliminou o Brasil nas oitavas',
    'depois das oitavas o caminho passa por Espanha') não é alegação de cruzamento e
    não pode anular a fala inteira — classe de falso positivo dos runs de 10/jun/2026."""
    delimiters = (".", ";", "\n", "|")
    start = max((normalized.rfind(delimiter, 0, position) for delimiter in delimiters), default=-1) + 1
    ends = [
        found
        for delimiter in delimiters
        for found in [normalized.find(delimiter, position)]
        if found != -1
    ]
    end = min(ends) if ends else len(normalized)
    sentence = normalized[start:end]
    return any(marker in sentence for marker in _BRACKET_CLAIM_MARKERS)


def _phase_allowed_opponents_union(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Candidatos por fase como união dos caminhos do Brasil em 1º, 2º e 3º do grupo.

    'Impossível' significa inalcançável por QUALQUER caminho do Brasil, não apenas
    fora do caminho da posição configurada — o Monte Carlo simula caminhos
    alternativos e o briefing pode citá-los legitimamente."""
    base_entries = brazil_bracket_path(config)
    if not base_entries:
        return []
    union: dict[str, dict[str, Any]] = {}
    for entry in base_entries:
        phase = str(entry.get("phase", ""))
        union[phase] = {**entry, "allowed_opponents": list(entry.get("allowed_opponents", []))}
    for position in (1, 2):
        for path in brazil_bracket_path_candidates(config, brazil_group_position=position):
            for entry in path:
                phase = str(entry.get("phase", ""))
                bucket = union.setdefault(phase, {**entry, "allowed_opponents": []})
                for opponent in entry.get("allowed_opponents", []):
                    if opponent not in bucket["allowed_opponents"]:
                        bucket["allowed_opponents"].append(opponent)
    return list(union.values())


def _impossible_bracket_opponent_detail(text: str, config: dict[str, Any]) -> dict[str, Any] | None:
    """Flagra adversário impossível atribuindo cada menção à fase MAIS PRÓXIMA no texto.

    Regressão histórica (runs de 10/jun/2026): o segmentador por pontuação atribuía a
    frase inteira a todas as fases citadas — uma enumeração legítima do caminho
    ('Japão nos 16 avos, depois Equador nas oitavas, e Inglaterra nas quartas')
    flagrava times de grupo/oitavas como impossíveis nos 16 avos (35 remoções falsas;
    sala de adversários 0/49; posições do protagonista anuladas). Agora: menção só
    conta contra a fase do marcador mais próximo dentro de uma janela máxima e só
    dentro de frase que alega confronto/caminho; adversários de grupo configurados,
    o Brasil e janelas com contexto explícito de 3º lugar nunca são flagrados; a
    lista permitida é a união determinística multi-caminho (1º/2º do grupo, com
    inícios ambíguos enumerados)."""
    normalized = _normalize_text(text)
    entries = _phase_allowed_opponents_union(config)
    if not entries:
        return None
    keyword_hits: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        phase_norm = _normalize_text(str(entry.get("phase", ""))).strip()
        if not phase_norm:
            continue
        variants = {phase_norm, f"{phase_norm}s"}
        if phase_norm.endswith("l"):
            variants.add(phase_norm[:-1] + "is")
        for variant in variants:
            if variant in ("final", "finais"):
                pattern = rf"(?<!de )(?<!fase )(?<!reta )\b{re.escape(variant)}\b"
            else:
                pattern = rf"\b{re.escape(variant)}\b"
            for match in re.finditer(pattern, normalized):
                keyword_hits.append((match.start(), entry))
    if not keyword_hits:
        return None

    brazil_norm = _normalize_text(config.get("brazil_team_name", "Brasil"))
    group_opponents_norm = {_normalize_text(opponent) for opponent in _group_opponents(config)}
    for team in sorted(COMMON_NATIONAL_TEAM_MARKERS):
        if team == brazil_norm or team in group_opponents_norm:
            continue
        for match in re.finditer(rf"\b{re.escape(team)}\b", normalized):
            position = match.start()
            window_start = max(0, position - MAX_PHASE_CLAIM_DISTANCE_CHARS)
            window_end = min(len(normalized), position + MAX_PHASE_CLAIM_DISTANCE_CHARS)
            window_text = normalized[window_start:window_end]
            if _segment_is_generic_opponent_universe(window_text):
                continue
            if any(marker in window_text for marker in _THIRD_PLACE_CONTEXT_MARKERS):
                continue
            if not _mention_sentence_is_bracket_claim(normalized, position):
                continue
            nearby_hits = sorted(
                (
                    (abs(hit_position - position), hit_entry)
                    for hit_position, hit_entry in keyword_hits
                    if abs(hit_position - position) <= MAX_PHASE_CLAIM_DISTANCE_CHARS
                ),
                key=lambda item: item[0],
            )
            if not nearby_hits:
                continue
            allowed_in_some_nearby_phase = False
            for _distance, entry in nearby_hits:
                allowed_norm = {
                    _normalize_text(candidate) for candidate in entry.get("allowed_opponents", [])
                }
                allowed_norm.add(brazil_norm)
                if team in allowed_norm:
                    allowed_in_some_nearby_phase = True
                    break
            if allowed_in_some_nearby_phase:
                break
            nearest_entry = nearby_hits[0][1]
            return {
                "phase": str(nearest_entry.get("phase", "")).strip(),
                "brazil_slot": nearest_entry.get("brazil_slot", ""),
                "opponent_slots": nearest_entry.get("opponent_slots", []),
                "allowed_opponents": nearest_entry.get("allowed_opponents", []),
                "invalid_opponents": [team],
            }
    return None


def _invalid_protagonist_question_reason(question: str, config: dict[str, Any]) -> str | None:
    if _has_opta_marker(question):
        return "benchmark reservado para a comparação separada"
    if _has_fixed_quanti_quali_allocation(question):
        return "alocação fixa quanti/quali proibida"
    completed_claim_detail = _unverified_completed_match_claim_detail(question, config)
    if completed_claim_detail:
        return completed_claim_detail
    if _impossible_bracket_opponent_detail(question, config):
        return "adversário impossível para o cruzamento oficial do mata-mata"
    if _mentions_unconfigured_opponent(question, config):
        return "adversário fora do grupo configurado ou país não definido no JSON de cenários"
    return None


def _sanitize_protagonist_question(question: str, *, config: dict[str, Any], protagonist: str) -> str:
    invalid_reason = _invalid_protagonist_question_reason(question, config)
    if invalid_reason == "benchmark reservado para a comparação separada":
        return _ensure_consensus_request(
            "A fala do protagonista foi invalidada pela sala por tentar usar benchmark reservado. "
            "Modelos da sala: ignorem essa fala e sigam sem benchmark externo; qual premissa do Modelo Principal "
            "deve mudar usando apenas odds, prediction markets, Elo/ranking, Sofascore/performance, lesões, "
            "cartões, arbitragem, descanso ou chaveamento?"
        )
    if invalid_reason == "alocação fixa quanti/quali proibida":
        return _ensure_consensus_request(
            "A fala do protagonista foi invalidada pela sala por usar alocação fixa entre dados quantitativos "
            "e qualitativos. Modelos da sala: ignorem essa fala; usem dados quantitativos e qualitativos sem "
            "percentual, razão ou peso metodológico entre eles. Qual premissa auditável deve mudar usando número, "
            "fonte/query e efeito em probabilidade?"
        )
    if invalid_reason == "adversário impossível para o cruzamento oficial do mata-mata":
        detail = _impossible_bracket_opponent_detail(question, config) or {}
        phase = str(detail.get("phase", "mata-mata"))
        brazil_slot = str(detail.get("brazil_slot", "")).strip()
        slots = ", ".join(str(slot) for slot in detail.get("opponent_slots", []) if str(slot))
        candidates = ", ".join(str(candidate) for candidate in detail.get("allowed_opponents", []) if str(candidate))
        return _ensure_consensus_request(
            "A fala do protagonista foi invalidada pela sala por citar adversário impossível no cruzamento oficial. "
            f"Para {phase}, Brasil {brazil_slot} só cruza com slot(s) {slots}; candidatos permitidos: {candidates}. "
            "Modelos da sala: ignorem a fala anterior e debatam apenas esses candidatos oficiais, trazendo "
            "scenario_probabilities e match_probabilities com fonte/query auditável."
        )
    if invalid_reason and "sem placar no ledger" in invalid_reason:
        completed = config.get("completed_group_matches", []) or []
        completed_scores = [
            f"{item.get('team_a')} {item.get('score_a')}-{item.get('score_b')} {item.get('team_b')}"
            for item in completed
            if isinstance(item, dict) and item.get("team_a") and item.get("team_b")
        ]
        rendered = "; ".join(completed_scores) if completed_scores else "nenhum placar realizado configurado"
        return _ensure_consensus_request(
            "A fala do protagonista foi invalidada pela sala por tratar jogo sem placar no ledger como fato consumado. "
            f"Placar(es) realizados disponíveis no ledger: {rendered}. "
            "Modelos da sala: ignorem a fala anterior e recalibrem somente com jogos realizados configurados, "
            "fontes auditáveis e cenários futuros explicitamente marcados como futuros."
        )
    if invalid_reason is None:
        return _ensure_consensus_request(question)
    opponents = ", ".join(match["opponent"] for match in _default_group_matches(config))
    return _ensure_consensus_request(
        "A fala do protagonista foi invalidada pela sala por citar adversário fora do grupo configurado. "
        f"Modelos da sala: ignorem essa fala e restrinjam o debate aos jogos de grupo contra {opponents} "
        "e aos cenários de mata-mata ainda a definir. Qual premissa numérica deve mudar para calibrar as chances "
        "do Brasil sem inventar confronto?"
    )


def _has_source_backed_recalibration(opinion: Any, *, combined: str, config: dict[str, Any] | None) -> bool:
    source_urls, source_queries = _effective_meeting_sources(opinion, config or {})
    if not (source_urls or source_queries):
        return False
    if not _looks_like_meeting_vote(opinion, combined):
        return False
    return _has_rational_hypothesis(combined)


def _has_implausible_title_jump(
    opinion: Any,
    *,
    baseline_title_pct: float,
    config: dict[str, Any] | None,
    combined: str | None = None,
) -> bool:
    max_shift_pct = float((config or {}).get("max_agent_title_shift_pct", 5.0))
    if max_shift_pct <= 0:
        return False
    if getattr(opinion, "title_pct", None) is None:
        return False
    try:
        title_pct = float(getattr(opinion, "title_pct"))
    except (TypeError, ValueError):
        return True
    shift_pct = abs(title_pct - float(baseline_title_pct))
    if shift_pct <= max_shift_pct + 1e-9:
        return False
    cfg = config or {}
    source_backed_shift_pct = float(cfg.get("max_agent_title_shift_with_sources_pct", 8.0))
    absolute_cap_pct = float(cfg.get("max_agent_title_pct_abs_cap", 25.0))
    if (
        combined is not None
        and title_pct <= absolute_cap_pct
        and shift_pct <= source_backed_shift_pct + 1e-9
        and _has_source_backed_recalibration(opinion, combined=combined, config=cfg)
    ):
        return False
    return True


def _has_unusable_meeting_payload(opinion: Any, *, combined: str, config: dict[str, Any] | None) -> bool:
    normalized = _normalize_text(combined)
    has_parse_failure = any(
        marker in normalized
        for marker in (
            "resposta em json parcial",
            "sem resposta parseavel",
        )
    )
    if has_parse_failure:
        return True
    has_no_external_answer_marker = "sem resposta externa utilizavel" in normalized
    if not has_no_external_answer_marker:
        return False
    if bool(getattr(opinion, "used_fallback", False)) or bool(getattr(opinion, "removed_from_main", False)):
        return True
    source_urls, source_queries = _effective_meeting_sources(opinion, config or {})
    if (source_urls or source_queries) and _has_rational_hypothesis(combined):
        return False
    return True


def _auditable_source_urls(opinion: Any) -> list[str]:
    return [
        str(url).strip()
        for url in (getattr(opinion, "source_urls", []) or [])
        if _valid_http_url(str(url).strip()) and not _has_opta_marker(str(url))
    ]


def _auditable_source_queries(opinion: Any) -> list[str]:
    return [
        str(query).strip()
        for query in (getattr(opinion, "source_queries", []) or [])
        if str(query).strip() and not _has_opta_marker(str(query))
    ]


def _agent_source_context_by_agent(opinions: list[Any]) -> dict[str, dict[str, list[str]]]:
    context: dict[str, dict[str, list[str]]] = {}
    for opinion in opinions:
        if getattr(opinion, "used_fallback", False) or getattr(opinion, "removed_from_main", False):
            continue
        agent = str(getattr(opinion, "agent", "") or "").strip()
        if not agent:
            continue
        source_urls = _auditable_source_urls(opinion)
        source_queries = _auditable_source_queries(opinion)
        if not source_urls and not source_queries:
            continue
        current = context.setdefault(agent, {"source_urls": [], "source_queries": []})
        current["source_urls"] = list(dict.fromkeys([*current["source_urls"], *source_urls]))
        current["source_queries"] = list(dict.fromkeys([*current["source_queries"], *source_queries]))
    return context


def _agent_source_context(config: dict[str, Any] | None, agent: str) -> dict[str, list[str]]:
    if not config:
        return {"source_urls": [], "source_queries": []}
    raw_context = config.get("_agent_source_context_by_agent", {})
    if not isinstance(raw_context, dict):
        return {"source_urls": [], "source_queries": []}
    direct = raw_context.get(agent)
    if isinstance(direct, dict):
        return {
            "source_urls": [str(url).strip() for url in direct.get("source_urls", []) if str(url).strip()],
            "source_queries": [
                str(query).strip() for query in direct.get("source_queries", []) if str(query).strip()
            ],
        }
    normalized_agent = _normalize_text(agent)
    for key, value in raw_context.items():
        if _normalize_text(str(key)) == normalized_agent and isinstance(value, dict):
            return {
                "source_urls": [str(url).strip() for url in value.get("source_urls", []) if str(url).strip()],
                "source_queries": [
                    str(query).strip() for query in value.get("source_queries", []) if str(query).strip()
                ],
            }
    return {"source_urls": [], "source_queries": []}


def _effective_meeting_sources(opinion: Any, config: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    explicit_urls = _auditable_source_urls(opinion)
    explicit_queries = _auditable_source_queries(opinion)
    if explicit_urls or explicit_queries:
        return explicit_urls, explicit_queries
    context = _agent_source_context(config, str(getattr(opinion, "agent", "") or ""))
    inherited_urls = [
        url for url in context.get("source_urls", []) if _valid_http_url(url) and not _has_opta_marker(url)
    ]
    inherited_queries = [query for query in context.get("source_queries", []) if query and not _has_opta_marker(query)]
    return list(dict.fromkeys(inherited_urls)), list(dict.fromkeys(inherited_queries))


def _with_inherited_meeting_sources(opinion: Any, config: dict[str, Any] | None) -> Any:
    if _auditable_source_urls(opinion) or _auditable_source_queries(opinion):
        return opinion
    source_urls, source_queries = _effective_meeting_sources(opinion, config)
    if not source_urls and not source_queries:
        return opinion
    return replace(opinion, source_urls=source_urls, source_queries=source_queries)


def _looks_like_meeting_vote(opinion: Any, combined: str) -> bool:
    normalized = _normalize_text(combined)
    if getattr(opinion, "agrees_with_protagonist", None) is not None:
        return True
    return any(
        marker in normalized
        for marker in (
            "concordo",
            "concordamos",
            "aceito",
            "aceitamos",
            "discordo",
            "discordamos",
        )
    )


def _has_rational_hypothesis(combined: str) -> bool:
    normalized = _normalize_text(combined)
    fact_terms = (
        "odds",
        "sportsbook",
        "mercado",
        "prediction",
        "elo",
        "rating",
        "ranking",
        "fifa",
        "sofascore",
        "fbref",
        "xg",
        "lesao",
        "cartao",
        "arbitragem",
        "var",
        "descanso",
        "chaveamento",
        "probabilidade",
        "p.p",
    )
    has_numeric_claim = bool(re.search(r"\d", normalized))
    has_fact_term = any(term in normalized for term in fact_terms)
    return len(normalized) >= 80 and has_numeric_claim and has_fact_term


_DISAGREEMENT_MARKERS = ("discordo", "discordamos", "rejeito", "nao aceito", "não aceito")


def _is_informed_agreement(opinion: Any, combined: str) -> bool:
    """Aceite explícito da hipótese auditável do protagonista, sem novas alegações próprias.

    Deferência informada é movimento legítimo de debate: a exigência de número+fonte
    vale para a tese em discussão e para quem discorda ou propõe ajuste — não para o
    eco que aceita. Regressão histórica (runs de 10/jun/2026): 'concordância sem
    hipótese auditável' removeu 20+ respostas de aceite, a sala nunca acumulou
    aceitação para fechar e morreu estéril. Guardas de qualidade: exige concordância
    estruturada explícita, corpo substantivo referenciando a tese, nenhum marcador de
    discordância, e proíbe injetar mapas de probabilidade novos sem fonte própria."""
    if getattr(opinion, "agrees_with_protagonist", None) is not True:
        return False
    normalized = _normalize_text(combined)
    if any(marker in normalized for marker in _DISAGREEMENT_MARKERS):
        return False
    if len(normalized) < 80:
        return False
    references_thesis = any(
        marker in normalized
        for marker in ("protagonista", "racional", "hipotese", "premissa", "proposta", "tese", "consenso")
    )
    if not references_thesis:
        return False
    has_new_probability_maps = bool(
        getattr(opinion, "match_probabilities", None) or getattr(opinion, "scenario_probabilities", None)
    )
    if has_new_probability_maps:
        return False
    if str(getattr(opinion, "adjustment", "") or "").strip():
        return False
    return True


def _has_unsupported_meeting_vote(
    opinion: Any,
    *,
    combined: str,
    config: dict[str, Any] | None,
) -> bool:
    if not _looks_like_meeting_vote(opinion, combined):
        return False
    cfg = config or {}
    if bool(cfg.get("allow_informed_agreement_votes", True)) and _is_informed_agreement(opinion, combined):
        return False
    source_urls, source_queries = _effective_meeting_sources(opinion, cfg)
    if bool(cfg.get("require_auditable_source_urls_for_meeting_votes", True)) and not (
        source_urls or source_queries
    ):
        return True
    allowed_urls = {str(url) for url in cfg.get("_allowed_fact_source_urls", []) if str(url)}
    explicit_urls = _auditable_source_urls(opinion)
    if allowed_urls and explicit_urls and not any(url in allowed_urls for url in explicit_urls):
        return True
    return not _has_rational_hypothesis(combined)


def _sanitize_main_meeting_opinions(
    opinions: list[Any],
    *,
    baseline_title_pct: float,
    config: dict[str, Any] | None = None,
    semantic_policy_stage: str = "initial",
    admit_policy_suspected: bool = False,
) -> list[Any]:
    sanitized: list[Any] = []
    for opinion in opinions:
        combined = " ".join(
            str(item or "")
            for item in (
                getattr(opinion, "summary", ""),
                getattr(opinion, "opening_argument", ""),
                getattr(opinion, "question", ""),
                getattr(opinion, "answer", ""),
                getattr(opinion, "critique", ""),
                getattr(opinion, "adjustment", ""),
                getattr(opinion, "proposed_next_question", ""),
                getattr(opinion, "leadership_rationale", ""),
                " ".join(getattr(opinion, "source_urls", []) or []),
                " ".join(getattr(opinion, "source_queries", []) or []),
            )
        )
        has_reserved_benchmark = _has_opta_marker(combined)
        impossible_bracket_detail = _impossible_bracket_opponent_detail(combined, config) if config else None
        has_impossible_bracket_opponent = bool(impossible_bracket_detail)
        has_unconfigured_group_opponent = bool(config and _mentions_unconfigured_opponent(combined, config))
        has_unusable_payload = _has_unusable_meeting_payload(opinion, combined=combined, config=config)
        external_search_issue = _external_search_failure_issue(opinion)
        has_fixed_quanti_quali_allocation = _has_fixed_quanti_quali_allocation(combined)
        has_implausible_title_jump = _has_implausible_title_jump(
            opinion,
            baseline_title_pct=baseline_title_pct,
            config=config,
            combined=combined,
        )
        has_unsupported_meeting_vote = _has_unsupported_meeting_vote(
            opinion,
            combined=combined,
            config=config,
        )
        if (
            not has_reserved_benchmark
            and not has_impossible_bracket_opponent
            and not has_unconfigured_group_opponent
            and not has_unusable_payload
            and not external_search_issue
            and not has_fixed_quanti_quali_allocation
            and not has_implausible_title_jump
            and not has_unsupported_meeting_vote
        ):
            sanitized.append(_with_inherited_meeting_sources(opinion, config))
            continue
        issue_reasons: list[str] = []
        if has_reserved_benchmark:
            issue_reasons.append("tentar usar benchmark reservado")
        if has_impossible_bracket_opponent:
            issue_reasons.append(_format_impossible_opponent_reason(impossible_bracket_detail))
        if has_unconfigured_group_opponent:
            issue_reasons.append("citar adversário de grupo fora do JSON configurado")
        if has_unusable_payload:
            issue_reasons.append("devolver resposta parcial ou sem campos auditáveis")
        if external_search_issue:
            issue_reasons.append(external_search_issue)
        if has_fixed_quanti_quali_allocation:
            issue_reasons.append("usar alocação fixa quanti/quali proibida")
        if has_unsupported_meeting_vote:
            issue_reasons.append("responder concordância/discordância sem hipótese auditável")
        if has_implausible_title_jump:
            issue_reasons.append("inconsistência quantitativa no title_pct")

        validation_issues = [
            _validation_issue_from_reason(
                gate_name="main_meeting_sanitizer",
                reason=reason,
                opinion=opinion,
                field="answer",
                semantic_policy_stage=semantic_policy_stage,
            )
            for reason in issue_reasons
        ]
        terminal_issues = [
            issue
            for issue in validation_issues
            if str(issue.get("recoverability", "")).strip().lower() != "policy_suspected"
        ]
        if admit_policy_suspected and validation_issues and not terminal_issues:
            sanitized.append(
                replace(
                    _with_inherited_meeting_sources(opinion, config),
                    used_fallback=False,
                    removed_from_main=False,
                    removal_reason="",
                    validation_issues=validation_issues,
                )
            )
            continue

        primary_issue = terminal_issues[0] if terminal_issues else validation_issues[0]
        removal_reason = _issue_text_from_reason(str(primary_issue.get("offending_excerpt") or ""))
        matched_rule = str(primary_issue.get("matched_rule") or "")
        if matched_rule == "reserved_benchmark_opta":
            removal_reason = "tentar usar benchmark reservado"
        elif matched_rule == "impossible_bracket_opponent":
            removal_reason = _format_impossible_opponent_reason(impossible_bracket_detail)
        elif matched_rule == "fixed_quantitative_qualitative_allocation":
            removal_reason = "usar alocação fixa quanti/quali proibida"
        elif issue_reasons:
            removal_reason = issue_reasons[0]
        sanitized.append(
            replace(
                opinion,
                title_pct=round(float(baseline_title_pct), 1),
                title_pct_source="fallback",
                summary=(
                    f"Resposta removida do Modelo Principal por {removal_reason}; "
                    "não conta como consenso do Modelo Principal."
                ),
                opening_argument="",
                question="",
                answer=(
                    f"Resposta removida do Modelo Principal por {removal_reason}; "
                    "o debate principal deve usar apenas fontes não reservadas ao comparativo separado "
                    "e adversários/cenários do JSON configurado."
                ),
                critique="",
                adjustment="",
                source_urls=[
                    url for url in (getattr(opinion, "source_urls", []) or []) if not _has_opta_marker(url)
                ],
                source_queries=[
                    query for query in (getattr(opinion, "source_queries", []) or []) if not _has_opta_marker(query)
                ],
                match_probabilities={},
                scenario_probabilities={},
                agrees_with_protagonist=None,
                leadership_bid=False,
                proposed_next_question="",
                leadership_rationale="",
                used_fallback=True,
                removed_from_main=True,
                removal_reason=removal_reason,
                validation_issues=validation_issues,
            )
        )
    return sanitized


def _sanitize_source_planning_opinions(
    opinions: list[Any],
    *,
    baseline_title_pct: float,
    config: dict[str, Any] | None = None,
) -> list[Any]:
    sanitized: list[Any] = []
    for opinion in opinions:
        source_urls = [
            str(url).strip()
            for url in (getattr(opinion, "source_urls", []) or [])
            if str(url).strip() and not _has_opta_marker(str(url))
        ]
        source_queries = [
            str(query).strip()
            for query in (getattr(opinion, "source_queries", []) or [])
            if str(query).strip() and not _has_opta_marker(str(query))
        ]
        combined = " ".join(
            str(item or "")
            for item in (
                getattr(opinion, "summary", ""),
                getattr(opinion, "opening_argument", ""),
                getattr(opinion, "answer", ""),
                getattr(opinion, "critique", ""),
                getattr(opinion, "adjustment", ""),
            )
        )
        has_unusable_payload = any(
            marker in _normalize_text(combined)
            for marker in (
                "resposta em json parcial",
                "sem resposta parseavel",
                "sem resposta externa utilizavel",
            )
        )
        external_search_issue = _external_search_failure_issue(opinion)
        has_implausible_title_jump = _has_implausible_title_jump(
            opinion,
            baseline_title_pct=baseline_title_pct,
            config=config,
        )
        has_parseable_sources = bool(source_urls or source_queries)
        if (
            bool(getattr(opinion, "used_fallback", False))
            or bool(external_search_issue)
            or (has_unusable_payload and not has_parseable_sources)
        ):
            reason = (
                external_search_issue
                or (
                    "devolver resposta parcial ou sem campos auditáveis"
                    if has_unusable_payload
                    else "falha operacional sem resposta externa verificável"
                )
            )
            original_summary = str(getattr(opinion, "summary", "") or "").strip()
            rendered_summary = (
                f"Resposta removida do planejamento de fontes por {reason}; "
                "não conta para o quórum de debriefing."
            )
            if original_summary:
                rendered_summary = f"{rendered_summary} Motivo original: {original_summary}"
            validation_issues = [
                _validation_issue_from_reason(
                    gate_name="source_planning_sanitizer",
                    reason=reason,
                    opinion=opinion,
                    source_items=source_urls + source_queries,
                    field="summary",
                )
            ]
            sanitized.append(
                replace(
                    opinion,
                    title_pct=round(float(baseline_title_pct), 1),
                    title_pct_source="fallback",
                    summary=rendered_summary,
                    opening_argument="",
                    question="",
                    answer="Resposta removida do planejamento de fontes; o modelo precisa trazer fontes próprias.",
                    critique="",
                    adjustment="",
                    source_urls=source_urls,
                    source_queries=source_queries,
                    match_probabilities={},
                    scenario_probabilities={},
                    agrees_with_protagonist=None,
                    leadership_bid=False,
                    proposed_next_question="",
                    leadership_rationale="",
                    used_fallback=True,
                    removed_from_main=True,
                    removal_reason=reason,
                    validation_issues=validation_issues,
                )
            )
            continue
        if has_unusable_payload and has_parseable_sources:
            sanitized.append(
                replace(
                    opinion,
                    title_pct=None,
                    title_pct_source="parser_default_rejected",
                    summary=(
                        f"{getattr(opinion, 'summary', '')} "
                        "[payload parcial, mas fontes auditáveis foram extraídas e preservadas para o quórum.]"
                    ).strip(),
                    source_urls=source_urls,
                    source_queries=source_queries,
                    used_fallback=False,
                    removed_from_main=False,
                    removal_reason="",
                    validation_issues=[],
                )
            )
            continue
        if has_implausible_title_jump:
            sanitized.append(
                replace(
                    opinion,
                    title_pct=None,
                    title_pct_source="parser_default_rejected",
                    summary=(
                        f"{getattr(opinion, 'summary', '')} "
                        "[title_pct neutralizado no planejamento por salto quantitativo implausível; "
                        "fontes preservadas para quórum se forem auditáveis.]"
                    ).strip(),
                    source_urls=source_urls,
                    source_queries=source_queries,
                    used_fallback=False,
                    removed_from_main=False,
                    removal_reason="",
                    validation_issues=[],
                )
            )
            continue
        sanitized.append(
            replace(
                opinion,
                source_urls=source_urls,
                source_queries=source_queries,
                used_fallback=False,
                removed_from_main=False,
                removal_reason="",
                validation_issues=[],
            )
        )
    return sanitized


def _ensure_consensus_request(question: str) -> str:
    lowered = question.lower()
    has_consensus_ask = "concord" in lowered and "discord" in lowered
    has_disagreement_rule = "se discord" in lowered and "protagonismo" in lowered
    if has_consensus_ask and has_disagreement_rule:
        return question
    suffix = (
        " Ao responder, cada modelo deve dizer explicitamente se concorda ou discorda do racional do protagonista; "
        "se discordar, precisa apontar o ajuste numérico e a fonte/premissa que justifica assumir o protagonismo. "
        "Se concordar, não fique passivo: proponha uma próxima pergunta melhor apenas quando houver mérito auditável, "
        "sem inventar discordância."
    )
    return question.rstrip() + suffix


def _initial_protagonist_score(opinion: Any) -> tuple[float, int, int, int, str]:
    source_items = _non_opta_source_items(opinion)
    source_urls = [
        str(url).strip()
        for url in (getattr(opinion, "source_urls", []) or [])
        if str(url).strip() and not _has_opta_marker(str(url))
    ]
    source_queries = [
        str(query).strip()
        for query in (getattr(opinion, "source_queries", []) or [])
        if str(query).strip() and not _has_opta_marker(str(query))
    ]
    scenario_probabilities = dict(getattr(opinion, "scenario_probabilities", {}) or {})
    team_context_signals = list(getattr(opinion, "team_context_signals", []) or [])
    text = " ".join(
        str(getattr(opinion, attr, "") or "")
        for attr in ("summary", "opening_argument", "answer", "critique", "adjustment")
    )
    normalized = _normalize_text(text)

    if getattr(opinion, "used_fallback", False):
        base = 0.0
    else:
        base = 100.0
    score = base
    score += min(len(source_urls), 8) * 3.0
    score += min(len(source_queries), 6) * 4.0
    if source_urls and source_queries:
        score += 10.0
    score += min(len(scenario_probabilities), 5) * 4.0
    score += min(len(team_context_signals), 5) * 5.0
    if len(normalized) >= 160:
        score += 8.0
    if "grupo" in normalized and ("mata-mata" in normalized or "chaveamento" in normalized):
        score += 8.0
    if any(
        marker in normalized
        for marker in (
            "resposta em json parcial",
            "sem resposta parseavel",
            "sem resposta externa utilizavel",
        )
    ):
        score -= 70.0
    return (
        score,
        min(len(source_items), 12),
        len(source_queries),
        len(normalized),
        str(getattr(opinion, "agent", "")),
    )


def _initial_protagonist(opinions: list[Any]) -> str:
    if not opinions:
        return "GPT 5.5"
    winner = max(opinions, key=_initial_protagonist_score)
    return winner.agent


def _fallback_question(protagonist: str, previous_turn: dict[str, Any] | None) -> str:
    if previous_turn:
        return _ensure_consensus_request(
            f"{protagonist}: qual premissa da rodada anterior deve mudar para reduzir a dispersão "
            "sem deixar uma única fonte, odds ou ranking dominarem sozinhos?"
        )
    return _ensure_consensus_request(
        f"{protagonist}: olhando as fontes escolhidas por cada modelo, qual é a probabilidade do Brasil "
        "em cada jogo e qual fonte mais ameaça distorcer o consenso?"
    )


def _agent_spec_by_slot(agent_specs: list[Any]) -> dict[str, Any]:
    return {spec.slot: spec for spec in agent_specs}


class _MeetingWatchdogView:
    def __init__(self, watchdog: RunWatchdog, *, meeting_step: str, room_step: str) -> None:
        self._watchdog = watchdog
        self._meeting_step = meeting_step
        self._room_step = room_step

    def _map_step(self, step: str) -> str:
        if step == "model_meeting":
            return self._meeting_step
        if step == "model_room":
            return self._room_step
        return step

    def event(self, step: str, status: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self._watchdog.event(self._map_step(step), status, detail=detail, extra=extra)

    def start(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "start", detail=detail, extra=extra)

    def finish(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "finish", detail=detail, extra=extra)

    def fail(self, step: str, *, detail: str = "", extra: dict[str, Any] | None = None) -> None:
        self.event(step, "fail", detail=detail, extra=extra)

    def chat(self, agent: str, message: str, *, round_name: str) -> None:
        self.event(
            "model_room",
            "chat",
            detail=message,
            extra={"agent": agent, "round": round_name},
        )

    def meeting_question(self, *, round_index: int, protagonist: str, question: str) -> None:
        self.event(
            "model_room",
            "question",
            detail=question,
            extra={"round": round_index, "protagonist": protagonist},
        )

    def meeting_response(self, *, round_index: int, agent: str, answer: str, support_score: float) -> None:
        self.event(
            "model_room",
            "response",
            detail=answer,
            extra={"round": round_index, "agent": agent, "support_score": support_score},
        )


def _meeting_agent_progress_callback(
    watchdog: Any | None,
    *,
    phase: str,
    round_index: int,
) -> Any:
    if watchdog is None:
        return None

    def callback(event: dict[str, Any]) -> None:
        status = str(event.get("status", "event"))
        agent = str(event.get("agent", ""))
        provider = str(event.get("provider", ""))
        elapsed = event.get("elapsed_ms")
        timeout = event.get("timeout_seconds")
        detail_parts = [f"{phase}; round={round_index}", f"agent={agent}"]
        if provider:
            detail_parts.append(f"provider={provider}")
        if timeout is not None and status == "start":
            detail_parts.append(f"timeout_s={timeout}")
        if elapsed is not None and status != "start":
            detail_parts.append(f"elapsed_ms={elapsed}")
        if event.get("error"):
            detail_parts.append(f"error={event['error']}")
        watchdog.event(
            "model_room",
            f"agent_call_{status}",
            detail="; ".join(detail_parts),
            extra={"phase": phase, "round": round_index, **event},
        )

    return callback


async def _protagonist_question(
    *,
    config: dict[str, Any],
    protagonist: str,
    previous_turn: dict[str, Any] | None,
    generated_at: datetime,
    agent_specs: list[Any],
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    timeout: int,
    cancel_event: threading.Event | None = None,
) -> tuple[str, Any, str | None]:
    by_slot = _agent_spec_by_slot(agent_specs)
    spec = by_slot.get(protagonist)
    if spec is None:
        question = _fallback_question(protagonist, previous_turn)
        return _sanitize_protagonist_question(question, config=config, protagonist=protagonist), None, None
    prompt = _protagonist_question_prompt(
        config=config,
        protagonist=protagonist,
        previous_turn=previous_turn,
        generated_at=generated_at,
    )
    try:
        opinion = await call_agent(
            spec,
            prompt,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
            cancel_event=cancel_event,
        )
    except TypeError as exc:
        if "cancel_event" not in str(exc):
            raise
        opinion = await call_agent(
            spec,
            prompt,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
        )
    question = opinion.question or _fallback_question(protagonist, previous_turn)
    invalid_reason = _invalid_protagonist_question_reason(question, config)
    return _sanitize_protagonist_question(question, config=config, protagonist=protagonist), opinion, invalid_reason


def _next_peer_after_invalid_protagonist_question(turn: dict[str, Any], *, current_protagonist: str) -> str:
    candidates = [
        response
        for response in turn.get("responses", [])
        if (
            not bool(response.get("removed_from_main", False))
            and (not bool(response.get("used_fallback", False)) or int(response.get("source_count", 0) or 0) > 0)
            and str(response.get("agent", "")) != current_protagonist
        )
    ]
    if not candidates:
        return current_protagonist
    winner = max(
        candidates,
        key=lambda response: (
            float(response.get("support_score", 0.0)),
            len(str(response.get("answer", ""))),
            str(response.get("agent", "")),
        ),
    )
    return str(winner.get("agent", current_protagonist))


def _blind_peer_review_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("blind_peer_review_enabled", False))


def _blind_peer_review_shadow_only(config: dict[str, Any]) -> bool:
    return bool(config.get("blind_peer_review_shadow_only", True))


def _blind_peer_review_acceptance_threshold(config: dict[str, Any]) -> float:
    return float(config.get("blind_peer_review_acceptance_threshold", 0.72))


def _blind_peer_review_max_self_preference_leakage(config: dict[str, Any]) -> float:
    return float(config.get("blind_peer_review_max_self_preference_leakage", 0.20))


def _blind_peer_review_empty_metadata(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": _blind_peer_review_enabled(config),
        "mode": "shadow",
        "shadow_only": _blind_peer_review_shadow_only(config),
        "acted_on_decision": False,
        "gate_blocked": False,
        "gate_blocked_reasons": [],
        "rounds_reviewed": [],
        "blind_review_score": {},
        "blind_acceptance_count": 0,
        "blind_top_position_id": "",
        "self_preference_leakage": {
            "value": 0.0,
            "self_score_count": 0,
            "reviewer_count": 0,
            "threshold": _blind_peer_review_max_self_preference_leakage(config),
            "exceeds_threshold": False,
        },
        "self_preference_by_reviewer": {},
        "self_preference_by_author": {},
        "errors": [],
    }


_BLIND_REVIEW_CURATED_MASK_TERMS = {
    "anthropic",
    "claude",
    "codex",
    "deepseek",
    "gemini",
    "gpt",
    "openai",
    "perplexity",
    "sonar",
    "opus",
}
_BLIND_REVIEW_MASK_STOPWORDS = {
    "api",
    "cli",
    "pro",
    "v4",
    "v5",
}


def _blind_peer_review_mask_pattern(term: str) -> str:
    stripped = str(term or "").strip()
    if re.fullmatch(r"\d+(?:\.\d+)+", stripped):
        return rf"(?<![\d.]){re.escape(stripped)}(?![\d.])"
    tokens = [token for token in re.split(r"[\s_\-/.]+", str(term or "").strip()) if token]
    if not tokens:
        return ""
    if len(tokens) == 1:
        return rf"\b{re.escape(tokens[0])}\b"
    return r"\b" + r"[\s_\-/.]*".join(re.escape(token) for token in tokens) + r"\b"


def _blind_peer_review_identity_fragments(term: str) -> set[str]:
    fragments: set[str] = set()
    text = str(term or "").strip()
    for version in re.findall(r"(?<![\d.])\d+(?:\.\d+)+(?![\d.])", text):
        fragments.add(version)
    raw_tokens = [token for token in re.split(r"[\s_\-/]+", text) if token]
    for token in raw_tokens:
        normalized = token.strip().lower()
        if re.fullmatch(r"\d+(?:\.\d+)+", normalized):
            fragments.add(token)
    for left, right in zip(raw_tokens, raw_tokens[1:]):
        left_normalized = left.strip().lower()
        right_normalized = right.strip().lower()
        if re.fullmatch(r"v\d+", left_normalized) and right_normalized == "pro":
            fragments.add(f"{left} {right}")
    return fragments


def _blind_peer_review_mask_terms(*, agent_specs: list[Any] | None, agent_slots: list[str]) -> list[str]:
    terms: set[str] = set(_BLIND_REVIEW_CURATED_MASK_TERMS)
    for slot in agent_slots:
        slot_text = str(slot or "").strip()
        if not slot_text:
            continue
        terms.add(slot_text)
        terms.update(_blind_peer_review_identity_fragments(slot_text))
        for token in re.split(r"[\s_\-/.]+", slot_text):
            normalized = token.strip().lower()
            if len(normalized) >= 4 and normalized not in _BLIND_REVIEW_MASK_STOPWORDS:
                terms.add(token)
    for spec in agent_specs or []:
        raw_values: list[Any] = [
            getattr(spec, "slot", ""),
            getattr(spec, "provider", ""),
            getattr(spec, "model", ""),
            *(getattr(spec, "model_fallbacks", None) or []),
        ]
        role_models = getattr(spec, "model_order_by_role", None) or {}
        if isinstance(role_models, dict):
            for values in role_models.values():
                if isinstance(values, list):
                    raw_values.extend(values)
                else:
                    raw_values.append(values)
        for value in raw_values:
            term = str(value or "").strip()
            if not term:
                continue
            terms.add(term)
            terms.update(_blind_peer_review_identity_fragments(term))
            for token in re.split(r"[\s_\-/.]+", term):
                normalized = token.strip().lower()
                if len(normalized) >= 4 and normalized not in _BLIND_REVIEW_MASK_STOPWORDS:
                    terms.add(token)
    return sorted(terms, key=lambda value: (-len(value), value.lower()))


def _blind_peer_review_opinion_mask_terms(opinions: list[Any]) -> list[str]:
    terms: set[str] = set()
    for opinion in opinions:
        for value in (
            getattr(opinion, "self_declared_name", ""),
            getattr(opinion, "self_declared_version", ""),
        ):
            term = str(value or "").strip()
            if not term:
                continue
            terms.add(term)
            terms.update(_blind_peer_review_identity_fragments(term))
    return sorted(terms, key=lambda value: (-len(value), value.lower()))


def _blind_peer_review_public_text(
    text: str,
    *,
    agent_slots: list[str],
    mask_terms: list[str] | None = None,
) -> str:
    clean = str(text or "")
    terms = _blind_peer_review_mask_terms(agent_specs=None, agent_slots=agent_slots)
    terms.extend(mask_terms or [])
    for term in sorted(set(terms), key=lambda value: (-len(value), value.lower())):
        pattern = _blind_peer_review_mask_pattern(term)
        if pattern:
            clean = re.sub(pattern, "modelo", clean, flags=re.I)
    replacements = {
        r"\bprotagonista\b": "posição",
        r"\blíder\b": "posição",
        r"\blider\b": "posição",
        r"\bleader\b": "posição",
    }
    for pattern, replacement in replacements.items():
        clean = re.sub(pattern, replacement, clean, flags=re.I)
    return clean


def _blind_peer_review_shuffle_seed(
    *,
    config: dict[str, Any],
    generated_at: datetime,
    round_index: int,
    room: str,
) -> str:
    base = str(config.get("blind_peer_review_shuffle_seed") or config.get("run_id") or generated_at.isoformat())
    return f"{base}|room={room}|round={round_index}"


def _blind_peer_review_positions(
    opinions: list[Any],
    *,
    agent_slots: list[str],
    mask_terms: list[str] | None = None,
    round_index: int = 0,
    shuffle_seed: str = "",
) -> list[dict[str, Any]]:
    by_agent = {str(getattr(opinion, "agent", "")): opinion for opinion in opinions}
    raw_positions: list[dict[str, Any]] = []
    for slot in agent_slots:
        opinion = by_agent.get(slot)
        if opinion is None or not _counts_as_consensus_participant(opinion):
            continue
        raw_positions.append(
            {
                "_agent": slot,
                "title_pct": (
                    round(float(getattr(opinion, "title_pct")), 1)
                    if getattr(opinion, "title_pct", None) is not None
                    else None
                ),
                "summary": _blind_peer_review_public_text(
                    str(getattr(opinion, "summary", "") or ""),
                    agent_slots=agent_slots,
                    mask_terms=mask_terms,
                )[:700],
                "answer": _blind_peer_review_public_text(
                    str(getattr(opinion, "answer", "") or ""),
                    agent_slots=agent_slots,
                    mask_terms=mask_terms,
                )[:900],
                "source_count": len(getattr(opinion, "source_urls", []) or [])
                + len(getattr(opinion, "source_queries", []) or []),
                "has_match_probabilities": bool(getattr(opinion, "match_probabilities", {}) or {}),
                "has_scenario_probabilities": bool(getattr(opinion, "scenario_probabilities", {}) or {}),
            }
        )
    original_order = [position["_agent"] for position in raw_positions]
    if len(raw_positions) > 1:
        rng = random.Random(f"{shuffle_seed}|round={round_index}")
        rng.shuffle(raw_positions)
        if [position["_agent"] for position in raw_positions] == original_order:
            raw_positions = [*raw_positions[1:], raw_positions[0]]
    positions: list[dict[str, Any]] = []
    for index, position in enumerate(raw_positions, start=1):
        position = dict(position)
        position["position_id"] = f"position_{index}"
        positions.append(position)
    return positions


def _blind_peer_review_prompt(
    *,
    config: dict[str, Any],
    round_index: int,
    positions: list[dict[str, Any]],
    generated_at: datetime,
) -> str:
    public_positions = [
        {key: value for key, value in position.items() if key != "_agent"}
        for position in positions
    ]
    payload = json.dumps(public_positions, ensure_ascii=False)
    fast_path_can_act = bool(config.get("llm_council_fast_path_enabled", False)) and not bool(
        config.get("llm_council_fast_path_shadow_only", True)
    )
    decision_clause = (
        "Esta revisão é gate auxiliar do fast path opt-in: só pode encurtar a sala se cobertura, "
        "aceitação, baixa dispersão, sala paralela e gates determinísticos também passarem; "
        "não substitui Monte Carlo nem validações. "
        if fast_path_can_act
        else "Isto é telemetria: sua resposta não altera a decisão do run. "
    )
    return (
        "Revisão cega em shadow da sala do Brasil. Você receberá posições anônimas, sem nomes de modelos "
        "e sem papel de coordenação. Não tente identificar autoria. Avalie somente accuracy + insight com base em "
        "fontes, coerência com bracket/Monte Carlo, hipótese racional e utilidade para consenso. "
        f"{decision_clause}"
        "Responda JSON estrito com summary e scores=[{position_id, score, accepted, rationale}], "
        "onde score vai de 0.0 a 1.0 e accepted é booleano. "
        f"{_quantitative_qualitative_decision_instruction()} "
        f"Data: {generated_at.isoformat()}. Rodada: {round_index}. "
        f"Posições anônimas: {payload}"
    )


def _blind_peer_review_score_items(opinion: Any) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {}
    for value in (
        getattr(opinion, "raw_text", ""),
        getattr(opinion, "answer", ""),
        getattr(opinion, "summary", ""),
    ):
        payload = _json_payload_from_text(str(value or ""))
        if payload:
            break
    raw_scores = payload.get("scores") or payload.get("rankings") or payload.get("reviews") or []
    if isinstance(raw_scores, dict):
        raw_scores = [
            {"position_id": position_id, **(score if isinstance(score, dict) else {"score": score})}
            for position_id, score in raw_scores.items()
        ]
    if not isinstance(raw_scores, list):
        return []
    items: list[dict[str, Any]] = []
    for raw_item in raw_scores:
        if not isinstance(raw_item, dict):
            continue
        position_id = str(raw_item.get("position_id") or raw_item.get("id") or raw_item.get("position") or "").strip()
        if not position_id:
            continue
        try:
            score = float(raw_item.get("score", raw_item.get("rating", 0.0)))
        except (TypeError, ValueError):
            continue
        score = max(0.0, min(1.0, score))
        accepted_value = raw_item.get("accepted")
        accepted = bool(accepted_value) if isinstance(accepted_value, bool) else score >= 0.72
        items.append(
            {
                "position_id": position_id,
                "score": score,
                "accepted": accepted,
                "rationale": str(raw_item.get("rationale") or raw_item.get("reason") or "")[:280],
            }
        )
    return items


def _aggregate_blind_peer_reviews(
    review_opinions: list[Any],
    *,
    positions: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    base = _blind_peer_review_empty_metadata(config)
    author_by_position = {position["position_id"]: position["_agent"] for position in positions}
    scores_by_position: dict[str, list[float]] = {position["position_id"]: [] for position in positions}
    accepts_by_position: dict[str, int] = {position["position_id"]: 0 for position in positions}
    self_scores: list[float] = []
    external_scores_by_reviewer: dict[str, list[float]] = {}
    self_scores_by_reviewer: dict[str, list[float]] = {}
    self_scores_by_author: dict[str, list[float]] = {}
    external_scores_by_author: dict[str, list[float]] = {position["_agent"]: [] for position in positions}
    threshold = _blind_peer_review_acceptance_threshold(config)

    for opinion in review_opinions:
        reviewer = str(getattr(opinion, "agent", "") or "")
        external_scores_by_reviewer.setdefault(reviewer, [])
        self_scores_by_reviewer.setdefault(reviewer, [])
        for item in _blind_peer_review_score_items(opinion):
            position_id = item["position_id"]
            if position_id not in author_by_position:
                continue
            score = float(item["score"])
            author = author_by_position[position_id]
            if author == reviewer:
                self_scores.append(score)
                self_scores_by_reviewer[reviewer].append(score)
                self_scores_by_author.setdefault(author, []).append(score)
                continue
            scores_by_position[position_id].append(score)
            external_scores_by_reviewer[reviewer].append(score)
            external_scores_by_author.setdefault(author, []).append(score)
            if bool(item.get("accepted", False)) or score >= threshold:
                accepts_by_position[position_id] += 1

    average_scores = {
        position_id: round(sum(scores) / len(scores), 3)
        for position_id, scores in scores_by_position.items()
        if scores
    }
    top_position_id = ""
    if average_scores:
        top_position_id = max(
            average_scores,
            key=lambda position_id: (
                average_scores[position_id],
                accepts_by_position.get(position_id, 0),
                position_id,
            ),
        )

    external_means = [
        sum(scores) / len(scores)
        for scores in external_scores_by_reviewer.values()
        if scores
    ]
    self_mean = sum(self_scores) / len(self_scores) if self_scores else 0.0
    external_mean = sum(external_means) / len(external_means) if external_means else 0.0
    leakage = round(max(0.0, self_mean - external_mean), 3) if self_scores else 0.0
    leakage_threshold = _blind_peer_review_max_self_preference_leakage(config)

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    reviewer_leakage: dict[str, dict[str, Any]] = {}
    for reviewer in sorted(set(self_scores_by_reviewer) | set(external_scores_by_reviewer)):
        reviewer_self = self_scores_by_reviewer.get(reviewer, [])
        reviewer_external = external_scores_by_reviewer.get(reviewer, [])
        self_avg = _mean(reviewer_self)
        external_avg = _mean(reviewer_external)
        reviewer_leakage[reviewer] = {
            "self_mean": round(self_avg, 3),
            "external_mean": round(external_avg, 3),
            "leakage": round(max(0.0, self_avg - external_avg), 3) if reviewer_self else 0.0,
            "self_score_count": len(reviewer_self),
            "external_score_count": len(reviewer_external),
        }

    author_leakage: dict[str, dict[str, Any]] = {}
    for author in sorted(set(self_scores_by_author) | set(external_scores_by_author)):
        author_self = self_scores_by_author.get(author, [])
        author_external = external_scores_by_author.get(author, [])
        self_avg = _mean(author_self)
        external_avg = _mean(author_external)
        author_leakage[author] = {
            "self_mean": round(self_avg, 3),
            "external_mean": round(external_avg, 3),
            "leakage": round(max(0.0, self_avg - external_avg), 3) if author_self else 0.0,
            "self_score_count": len(author_self),
            "external_score_count": len(author_external),
        }

    base.update(
        {
            "blind_review_score": average_scores,
            "blind_acceptance_by_position": accepts_by_position,
            "blind_acceptance_count": int(accepts_by_position.get(top_position_id, 0)) if top_position_id else 0,
            "blind_top_position_id": top_position_id,
            "self_preference_leakage": {
                "value": leakage,
                "self_score_count": len(self_scores),
                "reviewer_count": len(review_opinions),
                "threshold": leakage_threshold,
                "exceeds_threshold": bool(self_scores) and leakage > leakage_threshold,
            },
            "self_preference_by_reviewer": reviewer_leakage,
            "self_preference_by_author": author_leakage,
            "reviewer_count": len(review_opinions),
            "position_count": len(positions),
        }
    )
    return base


async def _run_blind_peer_review_shadow(
    *,
    config: dict[str, Any],
    round_index: int,
    consensus_opinions: list[Any],
    agent_specs: list[Any],
    active_slots: list[str],
    generated_at: datetime,
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    token_cost_ledger: dict[str, Any] | None,
    watchdog: RunWatchdog | None,
) -> dict[str, Any]:
    metadata = _blind_peer_review_empty_metadata(config)
    if not _blind_peer_review_enabled(config):
        return metadata
    room = str(config.get("_meeting_room", "main_brazil") or "main_brazil")
    metadata["room"] = room
    if room != "main_brazil" and not bool(config.get("blind_peer_review_include_side_rooms", False)):
        metadata["errors"] = ["skipped_non_main_room"]
        return metadata
    mask_terms = _blind_peer_review_mask_terms(agent_specs=agent_specs, agent_slots=active_slots)
    mask_terms.extend(_blind_peer_review_opinion_mask_terms(consensus_opinions))
    positions = _blind_peer_review_positions(
        consensus_opinions,
        agent_slots=active_slots,
        mask_terms=mask_terms,
        round_index=round_index,
        shuffle_seed=_blind_peer_review_shuffle_seed(
            config=config,
            generated_at=generated_at,
            round_index=round_index,
            room=room,
        ),
    )
    if len(positions) < 2:
        metadata["errors"] = ["insufficient_positions"]
        return metadata
    timeout = int(config.get("blind_peer_review_timeout_seconds", min(90, int(config.get("agent_timeout_seconds", 90)))))
    prompt = _blind_peer_review_prompt(
        config=config,
        round_index=round_index,
        positions=positions,
        generated_at=generated_at,
    )
    if watchdog:
        watchdog.event(
            "blind_peer_review",
            "start",
            detail=f"round={round_index}; room={room}; shadow_only={_blind_peer_review_shadow_only(config)}",
            extra={
                "round": round_index,
                "room": room,
                "shadow_only": _blind_peer_review_shadow_only(config),
                "position_count": len(positions),
            },
        )
    try:
        review_opinions = await call_all_agents(
            prompt,
            specs=agent_specs,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
            call_role="blind_peer_review",
        )
    except Exception as exc:
        metadata["errors"] = [str(exc)]
        if watchdog:
            watchdog.event(
                "blind_peer_review",
                "fail",
                detail=f"round={round_index}; {exc}",
                extra={"round": round_index, "room": room, "error": str(exc)},
            )
        return metadata
    if token_cost_ledger is not None:
        _record_token_costs(
            token_cost_ledger,
            config=config,
            prompt=prompt,
            opinions=review_opinions,
            stage=f"blind_peer_review_round_{round_index}",
        )
    metadata = _aggregate_blind_peer_reviews(review_opinions, positions=positions, config=config)
    metadata["room"] = room
    metadata["round"] = round_index
    metadata["rounds_reviewed"] = [round_index]
    metadata["acted_on_decision"] = False
    if watchdog:
        watchdog.event(
            "blind_peer_review",
            "finish",
            detail=(
                f"round={round_index}; top={metadata.get('blind_top_position_id') or 'none'}; "
                f"acceptances={metadata.get('blind_acceptance_count', 0)}; shadow only"
            ),
            extra=metadata,
        )
    return metadata


async def _ensure_blind_peer_review_for_turn(
    turn: dict[str, Any],
    *,
    mode: str,
    config: dict[str, Any],
    round_index: int,
    consensus_opinions: list[Any],
    agent_specs: list[Any],
    active_slots: list[str],
    generated_at: datetime,
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    token_cost_ledger: dict[str, Any] | None,
    watchdog: RunWatchdog | None,
) -> None:
    if not _blind_peer_review_enabled(config):
        return
    if isinstance(turn.get("blind_peer_review"), dict):
        return
    if mode != "shadow" and not bool(config.get("blind_peer_review_on_consensus_exit", True)):
        return
    metadata = await _run_blind_peer_review_shadow(
        config=config,
        round_index=round_index,
        consensus_opinions=consensus_opinions,
        agent_specs=agent_specs,
        active_slots=active_slots,
        generated_at=generated_at,
        baseline_title_pct=baseline_title_pct,
        allow_agent_fallback=allow_agent_fallback,
        token_cost_ledger=token_cost_ledger,
        watchdog=watchdog,
    )
    metadata["mode"] = mode
    turn["blind_peer_review"] = metadata


def _blind_peer_review_exit_blocked_reasons(
    turn: dict[str, Any],
    *,
    config: dict[str, Any],
    room_quorum: int,
) -> list[str]:
    if not _blind_peer_review_enabled(config) or _blind_peer_review_shadow_only(config):
        return []
    review = turn.get("blind_peer_review") if isinstance(turn.get("blind_peer_review"), dict) else {}
    if not review:
        return ["blind_review_missing"]
    blocked: list[str] = []
    if review.get("errors"):
        blocked.append("blind_review_errors")
    if int(review.get("blind_acceptance_count", 0) or 0) < int(room_quorum):
        blocked.append("blind_acceptance_missing")
    leakage = review.get("self_preference_leakage") if isinstance(review, dict) else {}
    if isinstance(leakage, dict) and bool(leakage.get("exceeds_threshold", False)):
        blocked.append("self_preference_leakage_high")
    return blocked


def _mark_blind_peer_review_exit_blocked(
    turn: dict[str, Any],
    *,
    blocked_reasons: list[str],
    room_quorum: int,
    watchdog: RunWatchdog | None,
) -> None:
    review = turn.get("blind_peer_review") if isinstance(turn.get("blind_peer_review"), dict) else None
    if review is not None:
        review["gate_blocked"] = True
        review["gate_blocked_reasons"] = list(blocked_reasons)
        review["minimum_blind_acceptances"] = int(room_quorum)
    if watchdog:
        watchdog.event(
            "blind_peer_review",
            "blocked",
            detail=(
                "saída por consenso bloqueada pela revisão cega: "
                + ", ".join(blocked_reasons)
            ),
            extra={
                "round": int(turn.get("round", 0) or 0),
                "mode": (review or {}).get("mode", ""),
                "blocked_reasons": list(blocked_reasons),
                "minimum_blind_acceptances": int(room_quorum),
                "blind_acceptance_count": int((review or {}).get("blind_acceptance_count", 0) or 0),
                "self_preference_leakage": (review or {}).get("self_preference_leakage", {}),
            },
        )


def _blind_peer_review_metadata_from_transcript(
    meeting_transcript: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    reviews = [
        turn.get("blind_peer_review")
        for turn in meeting_transcript
        if isinstance(turn.get("blind_peer_review"), dict)
    ]
    if not reviews:
        return _blind_peer_review_empty_metadata(config)
    latest = dict(reviews[-1])
    latest["rounds_reviewed"] = [
        int(review.get("round", turn.get("round", 0)) or 0)
        for turn, review in zip(
            [turn for turn in meeting_transcript if isinstance(turn.get("blind_peer_review"), dict)],
            reviews,
            strict=False,
        )
    ]
    latest["acted_on_decision"] = any(
        bool((turn.get("llm_council_fast_path") or {}).get("acted_on_decision", False))
        for turn in meeting_transcript
        if isinstance(turn.get("llm_council_fast_path"), dict)
    )
    if latest["acted_on_decision"]:
        latest["mode"] = "fast_path_gate"
    latest["enabled"] = _blind_peer_review_enabled(config)
    latest["shadow_only"] = _blind_peer_review_shadow_only(config)
    return latest


def _llm_council_fast_path_empty_metadata(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(config.get("llm_council_fast_path_enabled", False)),
        "shadow_only": bool(config.get("llm_council_fast_path_shadow_only", True)),
        "acted_on_decision": False,
        "eligible": False,
        "round": None,
        "blocked_reasons": [],
        "blind_acceptance_count": 0,
        "minimum_blind_acceptances": 0,
        "valid_participants": 0,
        "minimum_participants": int(
            config.get(
                "llm_council_fast_path_min_participants",
                config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3)),
            )
        ),
        "consensus_dispersion_pct": None,
        "max_dispersion_pct": float(
            config.get("llm_council_fast_path_max_dispersion_pct", config.get("meeting_consensus_threshold_pct", 2.5))
        ),
    }


def _parallel_opponent_briefing_allows_fast_path(config: dict[str, Any]) -> bool:
    briefing = config.get("_parallel_opponent_briefing")
    if not isinstance(briefing, dict) or not bool(briefing.get("enabled", False)):
        return True
    if bool(briefing.get("failed", False)):
        return False
    if "usable_for_main_room" in briefing:
        return bool(briefing.get("usable_for_main_room", False))
    return int(briefing.get("rounds", 0) or 0) > 0


def _llm_council_fast_path_report_coherence_error(config: dict[str, Any], consensus: Any) -> str:
    try:
        _validate_report_coherence(
            stage_probabilities=_stage_probabilities(float(getattr(consensus, "title_pct", 0.0) or 0.0), config),
            knockout_estimates=[],
            monte_carlo_result=config.get("_monte_carlo_result"),
        )
    except ReportCoherenceError as exc:
        return str(exc)
    return ""


def _llm_council_fast_path_evaluation(
    *,
    config: dict[str, Any],
    round_index: int,
    consensus: Any,
    consensus_opinions: list[Any],
    turn: dict[str, Any],
    coverage_ok: bool,
    majority_accepts: bool,
    room_quorum: int,
    active_slots: list[str],
    report_coherence_error: str = "",
) -> dict[str, Any]:
    metadata = _llm_council_fast_path_empty_metadata(config)
    metadata["round"] = round_index
    metadata["valid_participants"] = sum(
        1 for opinion in consensus_opinions if _counts_as_consensus_participant(opinion)
    )
    metadata["consensus_dispersion_pct"] = round(float(getattr(consensus, "dispersion_pct", 0.0) or 0.0), 3)
    blind_review = turn.get("blind_peer_review") if isinstance(turn.get("blind_peer_review"), dict) else {}
    metadata["blind_acceptance_count"] = int((blind_review or {}).get("blind_acceptance_count", 0) or 0)
    metadata["minimum_blind_acceptances"] = int(room_quorum)

    blocked: list[str] = []
    if not metadata["enabled"]:
        blocked.append("disabled")
    if round_index != 1:
        blocked.append("only_round_1")
    if len(active_slots) < int(metadata["minimum_participants"]):
        blocked.append("participant_floor")
    if metadata["valid_participants"] < int(metadata["minimum_participants"]):
        blocked.append("valid_participant_floor")
    if not coverage_ok:
        blocked.append("coverage_incomplete")
    if not majority_accepts:
        blocked.append("peer_acceptance_missing")
    if float(metadata["consensus_dispersion_pct"] or 0.0) > float(metadata["max_dispersion_pct"]):
        blocked.append("dispersion_too_high")
    if not _blind_peer_review_enabled(config):
        blocked.append("blind_review_disabled")
    if not blind_review:
        blocked.append("blind_review_missing")
    elif (blind_review or {}).get("errors"):
        blocked.append("blind_review_errors")
    elif int(metadata["blind_acceptance_count"]) < int(metadata["minimum_blind_acceptances"]):
        blocked.append("blind_acceptance_missing")
    leakage = (blind_review or {}).get("self_preference_leakage") if isinstance(blind_review, dict) else {}
    if isinstance(leakage, dict) and bool(leakage.get("exceeds_threshold", False)):
        blocked.append("self_preference_leakage_high")
    if not _parallel_opponent_briefing_allows_fast_path(config):
        blocked.append("parallel_opponent_room_unusable")
    if metadata["enabled"] and not report_coherence_error:
        report_coherence_error = _llm_council_fast_path_report_coherence_error(config, consensus)
        if report_coherence_error:
            blocked.append("report_coherence_failed")
    elif report_coherence_error:
        blocked.append("report_coherence_failed")

    metadata["report_coherence_error"] = report_coherence_error
    metadata["blocked_reasons"] = blocked
    metadata["eligible"] = not blocked
    metadata["acted_on_decision"] = bool(metadata["eligible"]) and not bool(metadata["shadow_only"])
    return metadata


def _llm_council_fast_path_metadata_from_transcript(
    meeting_transcript: list[dict[str, Any]],
    *,
    config: dict[str, Any],
) -> dict[str, Any]:
    candidates = [
        turn.get("llm_council_fast_path")
        for turn in meeting_transcript
        if isinstance(turn.get("llm_council_fast_path"), dict)
    ]
    if not candidates:
        return _llm_council_fast_path_empty_metadata(config)
    latest = dict(candidates[-1])
    latest["enabled"] = bool(config.get("llm_council_fast_path_enabled", False))
    latest["shadow_only"] = bool(config.get("llm_council_fast_path_shadow_only", True))
    latest["candidate_rounds"] = [
        int(item.get("round", 0) or 0)
        for item in candidates
        if bool(item.get("eligible", False))
    ]
    latest["acted_rounds"] = [
        int(item.get("round", 0) or 0)
        for item in candidates
        if bool(item.get("acted_on_decision", False))
    ]
    return latest


def _numeric_chairman_metadata(
    *,
    config: dict[str, Any],
    stage_probabilities: dict[str, float],
) -> dict[str, Any]:
    stage_source = _stage_probability_source(config)
    return {
        "enabled": bool(config.get("numeric_chairman_enabled", True)),
        "number_owner": stage_source,
        "primary_number_owner": _stage_probability_blend_label(config),
        "stage_probability_source": stage_source,
        "stage_probability_blend": _stage_probability_blend_metadata(config),
        "llm_role": "40_percent_weighted_input_after_consensus",
        "llm_decides_number": False,
        "bounded_adjustment_only": True,
        "hard_gate": "ReportCoherenceError",
        "post_gate": "report_coherence",
        "stage_probabilities_snapshot": {key: round(float(value), 1) for key, value in stage_probabilities.items()},
    }


async def _run_model_meeting(
    *,
    config: dict[str, Any],
    planning_opinions: list[Any],
    generated_at: datetime,
    agent_specs: list[Any],
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    watchdog: RunWatchdog | None,
    token_cost_ledger: dict[str, Any] | None = None,
    reentry_candidate_specs: list[Any] | None = None,
    reentry_removed_reasons: dict[str, str] | None = None,
    reentry_removed_issues: dict[str, list[dict[str, Any]]] | None = None,
    fast_path_report_coherence_check: Callable[[Any], str] | None = None,
    progress_sink: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[Any, list[Any], list[dict[str, Any]], list[Any]]:
    agent_specs = list(agent_specs)
    planning_opinions = list(planning_opinions)
    protagonist = _initial_protagonist(planning_opinions)
    previous_turn: dict[str, Any] | None = None
    all_opinions: list[Any] = []
    meeting_transcript: list[dict[str, Any]] = []
    final_consensus = None
    final_opinions: list[Any] = []
    last_valid_consensus = None
    last_valid_opinions: list[Any] = []
    max_rounds = int(config.get("meeting_max_rounds", 9))
    min_rounds = int(config.get("meeting_min_rounds", 6))
    configured_min_participants = int(config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3)))
    threshold = float(config.get("meeting_consensus_threshold_pct", 2.5))
    require_peer_acceptance = bool(config.get("meeting_require_peer_acceptance", True))
    timeout = int(config.get("agent_timeout_seconds", 90))
    protagonist_timeout = int(config.get("protagonist_timeout_seconds", timeout))
    breaker_threshold = max(1, int(config.get("meeting_slot_breaker_threshold", 3)))
    stability_delta_pp = float(config.get("meeting_stability_delta_pp", 1.0))
    stability_rounds_required = max(1, int(config.get("meeting_stability_rounds", 2)))
    round_budget_seconds = max(0.0, float(config.get("meeting_round_budget_seconds", 0) or 0))
    sterile_round_limit = max(1, int(config.get("meeting_sterile_round_limit", 2)))
    max_reentries_per_slot = max(0, int(config.get("meeting_max_reentries_per_slot", 1)))
    probe_max_attempts = max(1, int(config.get("agent_reentry_probe_max_attempts", 2)))
    active_slots = _slots_from_specs(agent_specs)
    if progress_sink is not None:
        progress_sink.update(
            {
                "participants": list(active_slots),
                "meeting_transcript": meeting_transcript,
                "all_opinions": all_opinions,
            }
        )
    reentry_enabled = bool(config.get("agent_reentry_probe_enabled", False))
    reentry_timeout = int(config.get("agent_reentry_probe_timeout_seconds", 180))
    reentry_removed_reasons = dict(reentry_removed_reasons or {})
    reentry_removed_issues = {
        str(slot): list(issues or [])
        for slot, issues in (reentry_removed_issues or {}).items()
    }
    reentry_candidates = {
        str(getattr(spec, "slot", "")): spec
        for spec in (reentry_candidate_specs or [])
        if str(getattr(spec, "slot", "")).strip() and str(getattr(spec, "slot", "")) not in active_slots
    }
    pending_reentry_tasks: dict[str, asyncio.Task] = {}
    room_quorum = _room_majority_quorum(
        configured_min=configured_min_participants,
        active_count=len(active_slots),
    )
    protagonist_counts: dict[str, int] = {slot: 0 for slot in active_slots}
    consecutive_invalid: dict[str, int] = {slot: 0 for slot in active_slots}
    stable_rounds = 0
    previous_consensus_title: float | None = None
    consecutive_sterile = 0
    last_round_consensus_valid = False
    exited_with_consensus = False
    round_budget_exhausted = False
    round_budget_warning = ""
    breaker_counts: dict[str, int] = {}
    reentry_counts: dict[str, int] = {}
    probe_attempts: dict[str, int] = {}
    fallback_question_streak: dict[str, int] = {}
    reentry_skip_announced: dict[str, str] = {}
    last_invalid_issues: dict[str, list[dict[str, Any]]] = {}

    if watchdog:
        watchdog.start("model_meeting", detail=f"starting dynamic meeting with protagonist {protagonist}")

    def schedule_reentry_probes(round_index: int) -> None:
        if not reentry_enabled:
            return
        for slot, spec in list(reentry_candidates.items()):
            if slot in active_slots or slot in pending_reentry_tasks:
                continue
            reason = reentry_removed_reasons.get(slot, "sem resposta externa verificável")
            primary_issue = _primary_validation_issue(reentry_removed_issues.get(slot, []), reason)
            issue_policy = _reentry_policy_for_validation_issue(primary_issue)
            if reentry_counts.get(slot, 0) >= max_reentries_per_slot:
                reentry_candidates.pop(slot, None)
                if watchdog:
                    watchdog.event(
                        "model_room",
                        "reentry_cooldown",
                        detail=(
                            f"{slot} já usou {reentry_counts.get(slot, 0)} reentrada(s) "
                            f"(limite {max_reentries_per_slot}); fora até o fim do run"
                        ),
                        extra={
                            "round": round_index,
                            "agent": slot,
                            "validation_issue": primary_issue,
                            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                            "reentry_eligible": False,
                            "reentry_decision_reason": "cooldown de reentry atingido",
                        },
                    )
                continue
            if probe_attempts.get(slot, 0) >= probe_max_attempts:
                reentry_candidates.pop(slot, None)
                if watchdog:
                    watchdog.event(
                        "model_room",
                        "reentry_probe_budget",
                        detail=(
                            f"{slot} esgotou {probe_attempts.get(slot, 0)} tentativa(s) de probe "
                            f"(limite {probe_max_attempts}); sem novas sondagens neste run"
                        ),
                        extra={
                            "round": round_index,
                            "agent": slot,
                            "validation_issue": primary_issue,
                            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                            "reentry_eligible": False,
                            "reentry_decision_reason": "orçamento de probes atingido",
                        },
                    )
                continue
            allowed, schedule_reason = _should_schedule_reentry_probe(
                config=config,
                round_index=round_index,
                active_count=len(active_slots),
                configured_min_participants=configured_min_participants,
                consecutive_sterile=consecutive_sterile,
            )
            if not bool(issue_policy["eligible"]):
                reentry_candidates.pop(slot, None)
                if watchdog:
                    watchdog.event(
                        "agent_reentry_probe",
                        "skipped",
                        detail=(
                            f"round={round_index}; agent={slot}; reentry inelegível; "
                            f"motivo={issue_policy['decision_reason']}; "
                            f"trecho={primary_issue.get('offending_excerpt', '') or 'não disponível'}"
                        ),
                        extra={
                            "round": round_index,
                            "agent": slot,
                            "reason": issue_policy["decision_reason"],
                            "validation_issue": primary_issue,
                            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                            "reentry_eligible": False,
                            "reentry_decision_reason": issue_policy["decision_reason"],
                            "active_slots": active_slots,
                        },
                    )
                continue
            if not allowed:
                if watchdog and reentry_skip_announced.get(slot) != schedule_reason:
                    watchdog.event(
                        "agent_reentry_probe",
                        "skipped",
                        detail=f"round={round_index}; agent={slot}; {schedule_reason}",
                        extra={
                            "round": round_index,
                            "agent": slot,
                            "reason": schedule_reason,
                            "validation_issue": primary_issue,
                            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                            "reentry_eligible": True,
                            "reentry_decision_reason": issue_policy["decision_reason"],
                            "active_slots": active_slots,
                        },
                    )
                reentry_skip_announced[slot] = schedule_reason
                continue
            probe_attempts[slot] = probe_attempts.get(slot, 0) + 1
            probe_timeout = _reentry_timeout_for_issue(config, primary_issue, reentry_timeout)
            pending_reentry_tasks[slot] = asyncio.create_task(
                _run_agent_reentry_probe(
                    spec=spec,
                    config=config,
                    generated_at=generated_at,
                    baseline_title_pct=baseline_title_pct,
                    removed_reason=reason,
                    timeout=probe_timeout,
                )
            )
            if watchdog:
                watchdog.event(
                    "agent_reentry_probe",
                    "start",
                    detail=(
                        f"round={round_index}; agent={slot}; timeout_s={probe_timeout}; "
                        f"removido por {primary_issue.get('matched_rule') or 'contrato'}; "
                        f"trecho que disparou: {primary_issue.get('offending_excerpt', '') or 'não disponível'}; "
                        f"reentry elegível: sim; motivo da decisão: {issue_policy['decision_reason']}"
                    ),
                    extra={
                        "round": round_index,
                        "agent": slot,
                        "timeout_seconds": probe_timeout,
                        "removed_reason": reason,
                        "validation_issue": primary_issue,
                        "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                        "reentry_eligible": True,
                        "reentry_decision_reason": issue_policy["decision_reason"],
                    },
                )

    def collect_finished_reentry_probes(round_index: int) -> None:
        nonlocal active_slots, room_quorum
        for slot in _ready_reentry_slots(pending_reentry_tasks):
            task = pending_reentry_tasks.pop(slot)
            try:
                opinion, reason, prompt = task.result()
            except Exception as exc:
                opinion, reason, prompt = None, str(exc), ""
            if opinion is None:
                failure_issue = _validation_issue_from_reason(
                    gate_name="agent_reentry_probe",
                    reason=reason,
                    field="summary",
                )
                failure_policy = _reentry_policy_for_validation_issue(failure_issue)
                original_issue = _primary_validation_issue(
                    reentry_removed_issues.get(slot, []),
                    reentry_removed_reasons.get(slot, reason),
                )
                primary_issue = failure_issue if not bool(failure_policy["eligible"]) else original_issue
                if not bool(failure_policy["eligible"]):
                    reentry_candidates.pop(slot, None)
                    reentry_removed_reasons[slot] = reason
                    reentry_removed_issues[slot] = [failure_issue]
                if watchdog:
                    watchdog.event(
                        "agent_reentry_probe",
                        "fail",
                        detail=f"round={round_index}; agent={slot}; {reason}",
                        extra={
                            "round": round_index,
                            "agent": slot,
                            "reason": reason,
                            "validation_issue": primary_issue,
                            "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                            "reentry_eligible": bool(primary_issue.get("reentry_eligible", False))
                            and bool(failure_policy["eligible"]),
                            "reentry_decision_reason": primary_issue.get("reentry_decision_reason", ""),
                        },
                    )
                continue
            if reentry_counts.get(slot, 0) >= max_reentries_per_slot:
                reentry_candidates.pop(slot, None)
                continue
            spec = reentry_candidates.pop(slot, None)
            if spec is None or slot in active_slots:
                continue
            agent_specs.append(spec)
            planning_opinions.append(opinion)
            active_slots = _slots_from_specs(agent_specs)
            room_quorum = _room_majority_quorum(
                configured_min=configured_min_participants,
                active_count=len(active_slots),
            )
            protagonist_counts.setdefault(slot, 0)
            consecutive_invalid[slot] = 0
            reentry_counts[slot] = reentry_counts.get(slot, 0) + 1
            probe_attempts[slot] = 0
            if token_cost_ledger is not None:
                _record_token_costs(
                    token_cost_ledger,
                    config=config,
                    prompt=prompt,
                    opinions=[opinion],
                    stage=f"agent_reentry_probe_round_{round_index}",
                )
            if watchdog:
                primary_issue = _primary_validation_issue(
                    reentry_removed_issues.get(slot, []),
                    reentry_removed_reasons.get(slot, ""),
                )
                watchdog.chat(
                    slot,
                    (
                        "reentrou na sala por probe assíncrono com plano de fontes próprio; "
                        f"fontes={', '.join(_non_opta_source_items(opinion)[:3]) or 'fonte auditável'}"
                    ),
                    round_name="agent-reentry",
                    extra={
                        "validation_issue": primary_issue,
                        "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                        "reentry_eligible": True,
                        "reentry_decision_reason": primary_issue.get("reentry_decision_reason", ""),
                    },
                )
                watchdog.event(
                    "agent_reentry_probe",
                    "finish",
                    detail=f"round={round_index}; agent={slot}; reentered active debate",
                    extra={
                        "round": round_index,
                        "agent": slot,
                        "active_slots": active_slots,
                        "validation_issue": primary_issue,
                        "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                        "reentry_eligible": True,
                        "reentry_decision_reason": primary_issue.get("reentry_decision_reason", ""),
                    },
                )

    meeting_started_at = asyncio.get_running_loop().time()
    for round_index in range(1, max_rounds + 1):
        if round_budget_seconds > 0 and round_index > 1:
            elapsed_seconds = asyncio.get_running_loop().time() - meeting_started_at
            allowed_seconds = round_budget_seconds * round_index
            if elapsed_seconds > allowed_seconds:
                round_budget_exhausted = True
                round_budget_warning = (
                    f"orçamento acumulado de rodada excedido antes da rodada {round_index}: "
                    f"{elapsed_seconds:.2f}s usados para limite de {allowed_seconds:.2f}s"
                )
                if watchdog:
                    watchdog.event(
                        "model_meeting",
                        "round_budget_exhausted",
                        detail=round_budget_warning,
                        extra={
                            "round": round_index,
                            "elapsed_seconds": round(elapsed_seconds, 3),
                            "allowed_seconds": round(allowed_seconds, 3),
                        },
                    )
                break
        collect_finished_reentry_probes(round_index)
        schedule_reentry_probes(round_index)
        protagonist_counts[protagonist] = protagonist_counts.get(protagonist, 0) + 1
        question_started_at = asyncio.get_running_loop().time()
        if watchdog:
            watchdog.event(
                "model_room",
                "agent_call_start",
                detail=(
                    f"question_phase; round={round_index}; agent={protagonist}; "
                    f"timeout_s={protagonist_timeout}"
                ),
                extra={
                    "phase": "question",
                    "round": round_index,
                    "agent": protagonist,
                    "timeout_seconds": protagonist_timeout,
                },
            )
        if progress_sink is not None:
            progress_sink["pending_round"] = {
                "round": round_index,
                "protagonist": protagonist,
                "question": "pergunta do protagonista ainda em geração",
                "status": "question_in_progress",
            }
        try:
            question, question_opinion, invalid_question_reason = await _protagonist_question(
                config=config,
                protagonist=protagonist,
                previous_turn=previous_turn,
                generated_at=generated_at,
                agent_specs=agent_specs,
                baseline_title_pct=baseline_title_pct,
                allow_agent_fallback=allow_agent_fallback,
                timeout=protagonist_timeout,
                cancel_event=cancel_event,
            )
        finally:
            if watchdog:
                elapsed_ms = round((asyncio.get_running_loop().time() - question_started_at) * 1000)
                watchdog.event(
                    "model_room",
                    "agent_call_finish",
                    detail=f"question_phase; round={round_index}; agent={protagonist}; elapsed_ms={elapsed_ms}",
                    extra={"phase": "question", "round": round_index, "agent": protagonist, "elapsed_ms": elapsed_ms},
                )
        question_was_fallback = (
            question_opinion is None
            or bool(getattr(question_opinion, "used_fallback", False))
            or bool(getattr(question_opinion, "removed_from_main", False))
            or not str(getattr(question_opinion, "question", "") or "").strip()
        )
        if question_was_fallback:
            fallback_question_streak[protagonist] = fallback_question_streak.get(protagonist, 0) + 1
        else:
            fallback_question_streak[protagonist] = 0
        if question_opinion is not None:
            if token_cost_ledger is not None:
                _record_token_costs(
                    token_cost_ledger,
                    config=config,
                    prompt=_protagonist_question_prompt(
                        config=config,
                        protagonist=protagonist,
                        previous_turn=previous_turn,
                        generated_at=generated_at,
                    ),
                    opinions=[question_opinion],
                    stage=f"meeting_round_{round_index}_question",
                )
        if question_opinion is not None:
            protagonist_position = _sanitize_main_meeting_opinions(
                [question_opinion],
                baseline_title_pct=baseline_title_pct,
                config=config,
            )[0]
        else:
            protagonist_position = AgentOpinion(
                agent=protagonist,
                title_pct=round(float(baseline_title_pct), 1),
                title_pct_source="fallback",
                summary="Protagonista manteve a tese da pergunta; posição sintética criada por ausência de resposta parseável.",
                question=question,
                answer=question,
                agrees_with_protagonist=True,
                used_fallback=True,
            )
        all_opinions.append(protagonist_position)

        if watchdog:
            watchdog.meeting_question(round_index=round_index, protagonist=protagonist, question=question)
        if progress_sink is not None:
            progress_sink["pending_round"] = {
                "round": round_index,
                "protagonist": protagonist,
                "question": question,
                "status": "question_ready",
            }

        responder_specs = [spec for spec in agent_specs if getattr(spec, "slot", None) != protagonist]
        response_prompt = _meeting_response_prompt(
            config=config,
            round_index=round_index,
            protagonist=protagonist,
            question=question,
            previous_turn=previous_turn,
            generated_at=generated_at,
        )
        try:
            raw_opinions = await call_all_agents(
                response_prompt,
                specs=responder_specs,
                baseline_title_pct=baseline_title_pct,
                timeout=timeout,
                allow_local_fallback=allow_agent_fallback,
                progress_callback=_meeting_agent_progress_callback(
                    watchdog,
                    phase="response",
                    round_index=round_index,
                ),
                cancel_event=cancel_event,
            )
        except TypeError as exc:
            if "cancel_event" not in str(exc):
                raise
            raw_opinions = await call_all_agents(
                response_prompt,
                specs=responder_specs,
                baseline_title_pct=baseline_title_pct,
                timeout=timeout,
                allow_local_fallback=allow_agent_fallback,
                progress_callback=_meeting_agent_progress_callback(
                    watchdog,
                    phase="response",
                    round_index=round_index,
                ),
            )
        if token_cost_ledger is not None:
            _record_token_costs(
                token_cost_ledger,
                config=config,
                prompt=response_prompt,
                opinions=raw_opinions,
                stage=f"meeting_round_{round_index}_responses",
            )
        opinions = _sanitize_main_meeting_opinions(
            raw_opinions,
            baseline_title_pct=baseline_title_pct,
            config=config,
        )
        opinions, repair_opinions = await _repair_invalid_meeting_responses(
            config=config,
            round_index=round_index,
            protagonist=protagonist,
            question=question,
            previous_turn=previous_turn,
            generated_at=generated_at,
            responder_specs=responder_specs,
            raw_opinions=raw_opinions,
            sanitized_opinions=opinions,
            baseline_title_pct=baseline_title_pct,
            allow_agent_fallback=allow_agent_fallback,
            timeout=timeout,
        )
        if watchdog and repair_opinions:
            repaired_agents = ", ".join(opinion.agent for opinion in repair_opinions)
            watchdog.event(
                "model_room",
                "repair",
                detail=f"targeted moderator feedback sent to {repaired_agents}",
                extra={"round": round_index, "agents": [opinion.agent for opinion in repair_opinions]},
            )
        all_opinions.extend(opinions)
        consensus_opinions = [protagonist_position, *opinions]
        try:
            consensus = build_consensus(consensus_opinions, agent_slots=active_slots)
            last_round_consensus_valid = True
            consecutive_sterile = 0
            last_valid_consensus = consensus
            last_valid_opinions = consensus_opinions
        except DegenerateConsensusError as exc:
            last_round_consensus_valid = False
            consecutive_sterile += 1
            if watchdog:
                watchdog.event(
                    "model_room",
                    "sterile_round",
                    detail=(
                        f"rodada {round_index} sem nenhum voto válido "
                        f"({consecutive_sterile}/{sterile_round_limit} antes de abortar): {exc}"
                    ),
                    extra={"round": round_index, "consecutive_sterile": consecutive_sterile},
                )
            if consecutive_sterile >= sterile_round_limit:
                for pending_slot, pending_task in pending_reentry_tasks.items():
                    if not pending_task.done():
                        pending_task.cancel()
                if watchdog:
                    watchdog.fail(
                        "model_meeting",
                        detail=(
                            f"sala estéril: {consecutive_sterile} rodadas consecutivas sem voto válido "
                            f"(rodada {round_index} de {max_rounds})"
                        ),
                    )
                raise MeetingConsensusError(
                    f"sala estéril: {consecutive_sterile} rodadas consecutivas sem nenhum voto válido "
                    f"(abortada na rodada {round_index} de {max_rounds})"
                ) from exc
            consensus = Consensus(
                title_pct=round(
                    float(previous_consensus_title if previous_consensus_title is not None else baseline_title_pct),
                    1,
                ),
                agent_summaries={opinion.agent: opinion.summary for opinion in consensus_opinions},
                dispersion_pct=0.0,
                raw_opinions=list(consensus_opinions),
                debate_transcript=[],
                agent_slots=tuple(active_slots),
            )
        turn = build_meeting_turn(
            round_index=round_index,
            protagonist=protagonist,
            question=question,
            opinions=opinions,
            consensus_title_pct=consensus.title_pct,
            protagonist_counts=protagonist_counts,
        )
        if invalid_question_reason:
            turn["invalidated_protagonist_question"] = {
                "agent": protagonist,
                "reason": invalid_question_reason,
                "action": (
                    "fala excluída da influência; modelos pares seguem o debate sem usar o adversário/benchmark inválido"
                ),
            }
            if turn["next_protagonist"] == protagonist:
                turn["next_protagonist"] = _next_peer_after_invalid_protagonist_question(
                    turn,
                    current_protagonist=protagonist,
                )
        turn["coverage"] = _meeting_coverage_report([*meeting_transcript, turn], config)
        meeting_transcript.append(turn)
        if progress_sink is not None:
            progress_sink["pending_round"] = None
            progress_sink["last_completed_round"] = round_index
        if _blind_peer_review_enabled(config) and round_index == 1:
            await _ensure_blind_peer_review_for_turn(
                turn,
                mode="shadow",
                config=config,
                round_index=round_index,
                consensus_opinions=consensus_opinions,
                agent_specs=agent_specs,
                active_slots=active_slots,
                generated_at=generated_at,
                baseline_title_pct=baseline_title_pct,
                allow_agent_fallback=allow_agent_fallback,
                token_cost_ledger=token_cost_ledger,
                watchdog=watchdog,
            )
        if watchdog:
            if invalid_question_reason:
                watchdog.event(
                    "model_room",
                    "invalidation",
                    detail=f"{protagonist}: {invalid_question_reason}",
                    extra={
                        "round": round_index,
                        "agent": protagonist,
                        "action": "excluded_from_influence_continue_meeting",
                    },
                )
            for response in turn["responses"]:
                watchdog.meeting_response(
                    round_index=round_index,
                    agent=response["agent"],
                    answer=response["answer"],
                    support_score=response["support_score"],
                )
            dissenter_count = sum(1 for response in turn["responses"] if response.get("disagreed"))
            if turn["next_protagonist"] != protagonist:
                next_response = next(
                    (
                        response
                        for response in turn["responses"]
                        if response.get("agent") == turn["next_protagonist"]
                    ),
                    {},
                )
                if next_response.get("disagreed"):
                    reason = f"assume protagonismo por discordância da rodada {round_index}; dissenters={dissenter_count}"
                elif next_response.get("leadership_bid"):
                    reason = (
                        f"assume protagonismo por mérito com aceite na rodada {round_index}; "
                        f"pergunta proposta={next_response.get('proposed_next_question', '')}"
                    )
                else:
                    reason = f"assume protagonismo por melhor resposta da rodada {round_index}; dissenters={dissenter_count}"
            elif dissenter_count:
                reason = (
                    f"mantém protagonismo por critério de desempate da rodada {round_index}; "
                    f"dissenters={dissenter_count}"
                )
            else:
                reason = f"mantém protagonismo porque não houve discordância útil na rodada {round_index}"
            watchdog.chat(
                turn["next_protagonist"],
                reason,
                round_name="leader-change",
            )

        for vote_opinion in (protagonist_position, *opinions):
            vote_slot = str(getattr(vote_opinion, "agent", "")).strip()
            if not vote_slot or vote_slot not in active_slots:
                continue
            if _counts_as_consensus_participant(vote_opinion):
                consecutive_invalid[vote_slot] = 0
                if vote_slot != protagonist:
                    fallback_question_streak[vote_slot] = 0
                last_invalid_issues.pop(vote_slot, None)
            else:
                consecutive_invalid[vote_slot] = consecutive_invalid.get(vote_slot, 0) + 1
                issues = list(getattr(vote_opinion, "validation_issues", []) or [])
                if issues:
                    last_invalid_issues[vote_slot] = issues
        for broken_slot in [
            slot for slot in list(active_slots) if consecutive_invalid.get(slot, 0) >= breaker_threshold
        ]:
            if len(active_slots) - 1 < configured_min_participants:
                if watchdog:
                    watchdog.event(
                        "model_room",
                        "breaker_skipped",
                        detail=(
                            f"{broken_slot} atingiu {consecutive_invalid.get(broken_slot, 0)} respostas inválidas "
                            "consecutivas, mas a remoção quebraria o mínimo de participantes da sala"
                        ),
                        extra={"round": round_index, "agent": broken_slot, "active_slots": active_slots},
                    )
                continue
            removed_spec = next(
                (spec for spec in agent_specs if str(getattr(spec, "slot", "")) == broken_slot),
                None,
            )
            breaker_reason = (
                f"circuit breaker: {consecutive_invalid.get(broken_slot, 0)} respostas consecutivas sem voto válido "
                "(removida da sala principal ou fallback sem fonte auditável)"
            )
            breaker_issues = list(last_invalid_issues.get(broken_slot, []))
            if not breaker_issues:
                breaker_issues = [
                    _validation_issue(
                        gate_name="meeting_circuit_breaker",
                        matched_rule="consecutive_invalid_votes",
                        offending_excerpt=breaker_reason,
                        field="answer",
                        severity="blocking",
                        recoverability="source",
                        repair_hint="Pode reentrar apenas se o probe trouxer resposta auditável com fontes próprias.",
                    )
                ]
            agent_specs = [spec for spec in agent_specs if str(getattr(spec, "slot", "")) != broken_slot]
            active_slots = _slots_from_specs(agent_specs)
            room_quorum = _room_majority_quorum(
                configured_min=configured_min_participants,
                active_count=len(active_slots),
            )
            consecutive_invalid[broken_slot] = 0
            breaker_counts[broken_slot] = breaker_counts.get(broken_slot, 0) + 1
            if (
                reentry_enabled
                and removed_spec is not None
                and broken_slot not in reentry_candidates
                and reentry_counts.get(broken_slot, 0) < max_reentries_per_slot
            ):
                reentry_candidates[broken_slot] = removed_spec
                reentry_removed_reasons[broken_slot] = breaker_reason
                reentry_removed_issues[broken_slot] = breaker_issues
            else:
                reentry_candidates.pop(broken_slot, None)
                reentry_removed_issues.pop(broken_slot, None)
                if watchdog and reentry_counts.get(broken_slot, 0) >= max_reentries_per_slot:
                    watchdog.event(
                        "model_room",
                        "reentry_cooldown",
                        detail=(
                            f"{broken_slot} quebrou o circuit breaker {breaker_counts[broken_slot]}x; "
                            f"limite de {max_reentries_per_slot} reentrada(s) atingido — fora até o fim do run"
                        ),
                        extra={"round": round_index, "agent": broken_slot, "breaker_count": breaker_counts[broken_slot]},
                    )
            if turn["next_protagonist"] == broken_slot:
                turn["next_protagonist"] = _next_peer_after_invalid_protagonist_question(
                    turn,
                    current_protagonist=broken_slot,
                )
            if watchdog:
                watchdog.event(
                    "model_room",
                    "circuit_breaker",
                    detail=(
                        f"{broken_slot}: {breaker_reason}; sai das próximas rodadas e só volta por reentrada "
                        "assíncrona com fontes próprias"
                    ),
                    extra={"round": round_index, "agent": broken_slot, "active_slots": active_slots},
                )

        next_candidate = str(turn["next_protagonist"])
        candidate_ineligible = (
            consecutive_invalid.get(next_candidate, 0) > 0
            or fallback_question_streak.get(next_candidate, 0) >= 2
            or next_candidate not in active_slots
        )
        if candidate_ineligible:
            rotated = _next_peer_after_invalid_protagonist_question(turn, current_protagonist=next_candidate)
            if rotated != next_candidate and consecutive_invalid.get(rotated, 0) == 0 and rotated in active_slots:
                turn["next_protagonist"] = rotated
                if watchdog:
                    watchdog.event(
                        "model_room",
                        "protagonist_rotation",
                        detail=(
                            f"{next_candidate} inelegível para protagonismo "
                            f"(voto inválido na rodada ou {fallback_question_streak.get(next_candidate, 0)} "
                            f"pergunta(s) fallback consecutivas); protagonismo forçado para {rotated}"
                        ),
                        extra={"round": round_index, "from": next_candidate, "to": rotated},
                    )
        if str(turn["next_protagonist"]) not in active_slots and active_slots:
            forced = next(
                (slot for slot in active_slots if consecutive_invalid.get(slot, 0) == 0),
                active_slots[0],
            )
            turn["next_protagonist"] = forced
            if watchdog:
                watchdog.event(
                    "model_room",
                    "protagonist_rotation",
                    detail=(
                        f"próximo protagonista fora da sala ativa (removido pelo breaker); "
                        f"protagonismo forçado para {forced}"
                    ),
                    extra={"round": round_index, "to": forced},
                )

        final_consensus = consensus
        final_opinions = consensus_opinions
        protagonist_source_count = len(getattr(protagonist_position, "source_urls", []) or []) + len(
            getattr(protagonist_position, "source_queries", []) or []
        )
        protagonist_counts_for_quorum = (
            not bool(getattr(protagonist_position, "used_fallback", False)) or protagonist_source_count > 0
        )
        minimum_peer_acceptances = max(0, room_quorum - int(protagonist_counts_for_quorum))
        consensus_ready = consensus_reached(
            consensus,
            round_index=round_index,
            minimum_rounds=min_rounds,
            threshold_pct=threshold,
            minimum_participants=configured_min_participants,
            minimum_peer_acceptances=minimum_peer_acceptances,
            last_turn=turn,
            require_peer_acceptance=require_peer_acceptance,
        )
        require_full_coverage = bool(config.get("meeting_require_full_path_coverage", True))
        coverage_complete = bool(turn.get("coverage", {}).get("complete", False))
        coverage_ok = coverage_complete or not require_full_coverage
        majority_accepts = _enough_peer_acceptances(turn, required_acceptances=minimum_peer_acceptances)
        report_coherence_error = ""
        if fast_path_report_coherence_check is not None and bool(config.get("llm_council_fast_path_enabled", False)):
            report_coherence_error = str(fast_path_report_coherence_check(consensus) or "")
        turn["llm_council_fast_path"] = _llm_council_fast_path_evaluation(
            config=config,
            round_index=round_index,
            consensus=consensus,
            consensus_opinions=consensus_opinions,
            turn=turn,
            coverage_ok=coverage_ok,
            majority_accepts=majority_accepts,
            room_quorum=room_quorum,
            active_slots=active_slots,
            report_coherence_error=report_coherence_error,
        )
        if bool(turn["llm_council_fast_path"].get("acted_on_decision", False)):
            exited_with_consensus = True
            object.__setattr__(consensus, "exit_status", "fast_path_consensus")
            if isinstance(turn.get("blind_peer_review"), dict):
                turn["blind_peer_review"]["acted_on_decision"] = True
                turn["blind_peer_review"]["mode"] = "fast_path_gate"
            if watchdog:
                watchdog.event(
                    "model_room",
                    "fast_path_exit",
                    detail=(
                        "fast path opt-in acionado: revisão cega, cobertura, aceitação e baixa dispersão "
                        f"fecharam consenso na rodada {round_index}"
                    ),
                    extra=dict(turn["llm_council_fast_path"]),
                )
            break
        title_stable = (
            previous_consensus_title is not None
            and abs(float(consensus.title_pct) - float(previous_consensus_title)) <= stability_delta_pp
        )
        if round_index >= min_rounds and coverage_ok and majority_accepts and title_stable:
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_consensus_title = float(consensus.title_pct)
        if consensus_ready and coverage_ok:
            await _ensure_blind_peer_review_for_turn(
                turn,
                mode="consensus_exit_candidate",
                config=config,
                round_index=round_index,
                consensus_opinions=consensus_opinions,
                agent_specs=agent_specs,
                active_slots=active_slots,
                generated_at=generated_at,
                baseline_title_pct=baseline_title_pct,
                allow_agent_fallback=allow_agent_fallback,
                token_cost_ledger=token_cost_ledger,
                watchdog=watchdog,
            )
            blind_exit_blocked = _blind_peer_review_exit_blocked_reasons(
                turn,
                config=config,
                room_quorum=room_quorum,
            )
            if blind_exit_blocked:
                _mark_blind_peer_review_exit_blocked(
                    turn,
                    blocked_reasons=blind_exit_blocked,
                    room_quorum=room_quorum,
                    watchdog=watchdog,
                )
                protagonist = turn["next_protagonist"]
                previous_turn = turn
                if pending_reentry_tasks:
                    await asyncio.sleep(0)
                continue
            exited_with_consensus = True
            break
        if stable_rounds >= stability_rounds_required:
            await _ensure_blind_peer_review_for_turn(
                turn,
                mode="stable_exit_candidate",
                config=config,
                round_index=round_index,
                consensus_opinions=consensus_opinions,
                agent_specs=agent_specs,
                active_slots=active_slots,
                generated_at=generated_at,
                baseline_title_pct=baseline_title_pct,
                allow_agent_fallback=allow_agent_fallback,
                token_cost_ledger=token_cost_ledger,
                watchdog=watchdog,
            )
            blind_exit_blocked = _blind_peer_review_exit_blocked_reasons(
                turn,
                config=config,
                room_quorum=room_quorum,
            )
            if blind_exit_blocked:
                _mark_blind_peer_review_exit_blocked(
                    turn,
                    blocked_reasons=blind_exit_blocked,
                    room_quorum=room_quorum,
                    watchdog=watchdog,
                )
                protagonist = turn["next_protagonist"]
                previous_turn = turn
                if pending_reentry_tasks:
                    await asyncio.sleep(0)
                continue
            exited_with_consensus = True
            if watchdog:
                watchdog.event(
                    "model_room",
                    "early_exit",
                    detail=(
                        f"consenso estável por {stable_rounds} rodadas (Δ título ≤ {stability_delta_pp} p.p.) "
                        "com cobertura e aceitação da maioria; sala encerra antes do teto para evitar re-litigação"
                    ),
                    extra={
                        "round": round_index,
                        "consensus_title_pct": consensus.title_pct,
                        "stable_rounds": stable_rounds,
                    },
                )
            break
        if watchdog and consensus_ready and require_full_coverage and not coverage_complete:
            watchdog.event(
                "model_room",
                "coverage_missing",
                detail="consensus numerically ready but full path coverage is incomplete",
                extra=turn.get("coverage", {}),
            )
        protagonist = turn["next_protagonist"]
        previous_turn = turn
        if pending_reentry_tasks:
            await asyncio.sleep(0)

    for slot, task in list(pending_reentry_tasks.items()):
        if task.done():
            continue
        task.cancel()
        primary_issue = _primary_validation_issue(
            reentry_removed_issues.get(slot, []),
            reentry_removed_reasons.get(slot, ""),
        )
        if watchdog:
            watchdog.event(
                "agent_reentry_probe",
                "cancel",
                detail=f"agent={slot}; meeting ended before async probe completed",
                extra={
                    "agent": slot,
                    "validation_issue": primary_issue,
                    "offending_excerpt": primary_issue.get("offending_excerpt", ""),
                    "reentry_eligible": bool(primary_issue.get("reentry_eligible", False)),
                    "reentry_decision_reason": primary_issue.get("reentry_decision_reason", ""),
                },
            )

    if final_consensus is None:
        if watchdog:
            watchdog.fail("model_meeting", detail="nenhuma rodada produziu consenso válido")
        raise MeetingConsensusError("nenhuma rodada produziu consenso válido")
    if not exited_with_consensus and not last_round_consensus_valid:
        if last_valid_consensus is not None:
            if watchdog:
                watchdog.event(
                    "model_meeting",
                    "degraded_publish",
                    detail=(
                        f"teto de {max_rounds} rodadas atingido com rodada final estéril; "
                        f"publicando o último consenso válido de rodada anterior "
                        f"(título {last_valid_consensus.title_pct:.1f}%) em modo degradado"
                    ),
                    extra={
                        "max_rounds": max_rounds,
                        "last_valid_title_pct": last_valid_consensus.title_pct,
                    },
                )
            final_consensus = last_valid_consensus
            final_opinions = last_valid_opinions
            object.__setattr__(final_consensus, "exit_status", "degraded_last_valid")
            object.__setattr__(
                final_consensus,
                "exit_warning",
                (
                    f"teto de {max_rounds} rodadas atingido com rodada final estéril; "
                    "publicado último consenso válido em modo degradado"
                ),
            )
        else:
            if watchdog:
                watchdog.fail(
                    "model_meeting",
                    detail=f"teto de {max_rounds} rodadas atingido sem nenhum voto válido na rodada final",
                )
            raise MeetingConsensusError(
                f"teto de {max_rounds} rodadas atingido sem nenhum voto válido na rodada final; "
                "consenso não pode ser publicado a partir de fallbacks"
            )
    elif round_budget_exhausted:
        object.__setattr__(final_consensus, "exit_status", "round_budget_exhausted")
        object.__setattr__(
            final_consensus,
            "exit_warning",
            round_budget_warning or "orçamento acumulado de rodada excedido; publicado último consenso parcial",
        )
    elif not exited_with_consensus:
        object.__setattr__(final_consensus, "exit_status", "max_rounds_no_consensus")
        object.__setattr__(
            final_consensus,
            "exit_warning",
            f"teto de {max_rounds} rodadas atingido sem consenso explícito; publicar como leitura não consensual",
        )
        if watchdog:
            watchdog.event(
                "model_meeting",
                "max_rounds_no_consensus",
                detail=f"teto de {max_rounds} rodadas atingido sem consenso explícito",
                extra={"max_rounds": max_rounds, "last_title_pct": final_consensus.title_pct},
            )
    if watchdog:
        watchdog.finish(
            "model_meeting",
            detail=f"completed {len(meeting_transcript)} rounds; final title={final_consensus.title_pct:.1f}%",
        )
    return final_consensus, final_opinions, meeting_transcript, all_opinions


def _apply_meeting_match_probabilities(estimates: list[Any], opinions: list[Any]) -> None:
    for estimate in estimates:
        candidates = _opinion_match_probability_values(
            opinions,
            phase=str(getattr(estimate, "phase", "")),
            opponent=str(getattr(estimate, "opponent", "")),
            estimate_scenario_pct=getattr(estimate, "scenario_pct", None),
        )
        if estimate.draw_pct is not None:
            candidates = [pct for pct in candidates if pct <= 100.0 - float(estimate.draw_pct)]
        if not candidates:
            continue
        brazil_pct = round(sum(candidates) / len(candidates), 1)
        _set_estimate_brazil_probability(estimate, brazil_pct)


def _team_context_signal_has_auditable_source(signal: dict[str, Any]) -> bool:
    for key in ("source_url", "source_query", "source", "source_urls", "source_queries"):
        value = signal.get(key)
        if isinstance(value, list) and any(str(item).strip() and not _has_opta_marker(str(item)) for item in value):
            return True
        if isinstance(value, str) and value.strip() and not _has_opta_marker(value):
            return True
    return False


def _team_context_signal_has_numeric_effect(signal: dict[str, Any]) -> bool:
    keys = (
        "rating_delta",
        "rating_delta_points",
        "elo_delta",
        "elo_delta_points",
        "market_rating_delta",
        "context_rating_delta",
        "probability_delta_pct",
        "win_probability_delta_pct",
        "advancement_probability_delta_pct",
        "scenario_probability_delta_pct",
    )
    for key in keys:
        if key not in signal:
            continue
        try:
            float(signal[key])
            return True
        except (TypeError, ValueError):
            return False
    return False


def _apply_agent_team_context_to_monte_carlo_config(config: dict[str, Any], opinions: list[Any]) -> dict[str, Any]:
    mc_config = config.setdefault("monte_carlo", {})
    if not isinstance(mc_config, dict):
        mc_config = {}
        config["monte_carlo"] = mc_config
    team_context = mc_config.setdefault("team_context", {})
    if not isinstance(team_context, dict):
        team_context = {}
        mc_config["team_context"] = team_context

    applied = 0
    ignored = 0
    families: set[str] = set()
    teams: set[str] = set()
    for opinion in opinions:
        if bool(getattr(opinion, "used_fallback", False)):
            continue
        agent = str(getattr(opinion, "agent", "")).strip() or "Modelo"
        for raw_signal in getattr(opinion, "team_context_signals", []) or []:
            if not isinstance(raw_signal, dict):
                ignored += 1
                continue
            signal = dict(raw_signal)
            team = str(signal.get("team") or signal.get("selection") or signal.get("country") or "").strip()
            category = str(signal.get("category") or signal.get("family") or signal.get("source_family") or "").strip()
            if (
                not team
                or not category
                or not _team_context_signal_has_numeric_effect(signal)
                or not _team_context_signal_has_auditable_source(signal)
            ):
                ignored += 1
                continue
            signal["team"] = team
            signal["category"] = category
            signal["agent"] = agent
            team_context.setdefault(team, []).append(signal)
            applied += 1
            teams.add(team)
            families.add(category)

    return {
        "applied_signal_count": applied,
        "ignored_signal_count": ignored,
        "teams_with_context_count": len(teams),
        "source_families": sorted(families),
    }


def _apply_monte_carlo_knockout_scenarios(estimates: list[Any], monte_carlo_result: dict[str, Any]) -> None:
    if not monte_carlo_result.get("enabled"):
        return
    phases = monte_carlo_result.get("phases") or {}
    for phase, payload in phases.items():
        phase_estimates = [
            estimate
            for estimate in estimates
            if str(getattr(estimate, "phase", "")) == str(phase)
            and _is_placeholder_opponent_name(getattr(estimate, "opponent", ""))
        ]
        if not phase_estimates:
            continue
        opponents = list(payload.get("opponents", []))[: len(phase_estimates)]
        if not opponents:
            continue
        phase_estimates.sort(key=lambda estimate: 0 if bool(getattr(estimate, "most_likely", False)) else 1)
        for estimate, opponent_payload in zip(phase_estimates, opponents, strict=False):
            estimate.opponent = str(opponent_payload.get("opponent") or estimate.opponent)
            if opponent_payload.get("scenario_pct") is not None:
                estimate.scenario_pct = round(float(opponent_payload["scenario_pct"]), 1)
            if opponent_payload.get("brazil_pct") is not None:
                _set_estimate_brazil_probability(estimate, float(opponent_payload["brazil_pct"]))


def _opinion_probability_values(
    opinions: list[Any],
    *,
    field: str,
    phase: str,
    opponent: str,
) -> list[float]:
    values: list[float] = []
    phase_key = _normalize_text(phase)
    opponent_key = _normalize_text(opponent)
    for opinion in opinions:
        mapping = getattr(opinion, field, {}) or {}
        for key, value in mapping.items():
            normalized_key = _normalize_text(str(key))
            if phase_key not in normalized_key or opponent_key not in normalized_key:
                continue
            try:
                pct = float(value)
            except (TypeError, ValueError):
                continue
            if 0.0 <= pct <= 100.0:
                values.append(pct)
    return values


def _probability_key_matches(*, key: str, phase: str, opponent: str) -> bool:
    normalized_key = _normalize_text(str(key)).replace("_", " ")
    phase_key = _normalize_text(phase).replace("_", " ")
    opponent_key = _normalize_text(opponent).replace("_", " ")
    if not opponent_key or opponent_key not in normalized_key:
        return False
    if not _is_knockout_estimate(phase):
        return True
    return phase_key in normalized_key or "brasil" in normalized_key


def _float_probability(value: Any) -> float | None:
    try:
        pct = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None
    if 0.0 <= pct <= 100.0:
        return pct
    return None


def _same_probability_value(left: float, right: Any, *, tolerance: float = 0.15) -> bool:
    right_pct = _float_probability(right)
    return right_pct is not None and abs(float(left) - right_pct) <= tolerance


def _is_knockout_estimate(phase: str) -> bool:
    return _normalize_text(phase) not in {"fase de grupos", "grupo", "group", "group stage"}


def _is_contaminated_match_probability(
    opinion: Any,
    *,
    key: str,
    value: float,
    phase: str,
    opponent: str,
    estimate_scenario_pct: float | None = None,
) -> bool:
    if not _is_knockout_estimate(phase):
        return False
    scenario_probabilities = dict(getattr(opinion, "scenario_probabilities", {}) or {})
    for scenario_key, scenario_value in scenario_probabilities.items():
        if not _probability_key_matches(key=str(scenario_key), phase=phase, opponent=opponent):
            continue
        if _same_probability_value(value, scenario_value):
            return True
    return estimate_scenario_pct is not None and _same_probability_value(value, estimate_scenario_pct)


def _opinion_match_probability_values(
    opinions: list[Any],
    *,
    phase: str,
    opponent: str,
    estimate_scenario_pct: float | None = None,
) -> list[float]:
    values: list[float] = []
    for opinion in opinions:
        for key, value in dict(getattr(opinion, "match_probabilities", {}) or {}).items():
            if not _probability_key_matches(key=str(key), phase=phase, opponent=opponent):
                continue
            pct = _float_probability(value)
            if pct is None:
                continue
            if _is_contaminated_match_probability(
                opinion,
                key=str(key),
                value=pct,
                phase=phase,
                opponent=opponent,
                estimate_scenario_pct=estimate_scenario_pct,
            ):
                continue
            values.append(pct)
    return values


def _set_estimate_brazil_probability(estimate: Any, brazil_pct: float) -> None:
    old_low = getattr(estimate, "brazil_ci_low", None)
    old_high = getattr(estimate, "brazil_ci_high", None)
    ci_width = None
    if old_low is not None and old_high is not None:
        try:
            ci_width = max(0.0, float(old_high) - float(old_low))
        except (TypeError, ValueError):
            ci_width = None
    brazil_pct = round(float(brazil_pct), 1)
    estimate.brazil_pct = brazil_pct
    if estimate.draw_pct is None:
        estimate.opponent_pct = round(100.0 - brazil_pct, 1)
    else:
        estimate.opponent_pct = round(max(0.0, 100.0 - brazil_pct - float(estimate.draw_pct)), 1)
    if ci_width is not None:
        estimate.brazil_ci_low = round(max(0.0, brazil_pct - ci_width / 2.0), 1)
        estimate.brazil_ci_high = round(min(100.0, brazil_pct + ci_width / 2.0), 1)


def _validate_report_coherence(
    *,
    stage_probabilities: dict[str, float],
    knockout_estimates: list[Any],
    monte_carlo_result: dict[str, Any] | None,
    group_estimates: list[Any] | None = None,
) -> None:
    errors: list[str] = []
    probability_tolerance = 0.2

    for estimate in group_estimates or []:
        phase = str(getattr(estimate, "phase", "Fase de grupos") or "Fase de grupos")
        opponent = str(getattr(estimate, "opponent", "") or "")
        brazil_pct = _float_probability(getattr(estimate, "brazil_pct", None))
        opponent_pct = _float_probability(getattr(estimate, "opponent_pct", None))
        draw_pct = _float_probability(getattr(estimate, "draw_pct", None))
        if brazil_pct is None or opponent_pct is None:
            continue
        if draw_pct is None:
            total = brazil_pct + opponent_pct
            if abs(total - 100.0) > probability_tolerance:
                errors.append(
                    f"{phase} vs {opponent}: Brasil+adversário={total:.1f}% deveria somar 100.0%"
                )
            continue
        total = brazil_pct + draw_pct + opponent_pct
        if abs(total - 100.0) > probability_tolerance:
            errors.append(f"{phase} vs {opponent}: V+E+D={total:.1f}% deveria somar 100.0%")

    for estimate in knockout_estimates:
        phase = str(getattr(estimate, "phase", ""))
        if not _is_knockout_estimate(phase):
            continue
        opponent = str(getattr(estimate, "opponent", "") or "")
        brazil_pct = _float_probability(getattr(estimate, "brazil_pct", None))
        opponent_pct = _float_probability(getattr(estimate, "opponent_pct", None))
        if brazil_pct is None or opponent_pct is None:
            continue
        total = brazil_pct + opponent_pct
        if abs(total - 100.0) > probability_tolerance:
            errors.append(f"{phase} vs {opponent}: Brasil+adversário={total:.1f}% deveria somar 100.0%")

    stage_order = ("quartas", "semifinal", "final", "titulo")
    for previous_key, next_key in zip(stage_order, stage_order[1:]):
        if previous_key not in stage_probabilities or next_key not in stage_probabilities:
            continue
        previous_value = float(stage_probabilities[previous_key])
        next_value = float(stage_probabilities[next_key])
        if next_value > previous_value + 0.15:
            errors.append(
                f"funil incoerente: {next_key}={next_value:.1f}% maior que {previous_key}={previous_value:.1f}%"
            )

    for estimate in knockout_estimates:
        phase = str(getattr(estimate, "phase", ""))
        if not _is_knockout_estimate(phase):
            continue
        scenario_pct = getattr(estimate, "scenario_pct", None)
        if scenario_pct is None:
            continue
        brazil_pct = _float_probability(getattr(estimate, "brazil_pct", None))
        if brazil_pct is None or not _same_probability_value(brazil_pct, scenario_pct):
            continue
        opponent = str(getattr(estimate, "opponent", "") or "")
        monte_carlo_payload = _monte_carlo_phase_opponents(monte_carlo_result, phase).get(_normalize_text(opponent))
        monte_carlo_brazil_pct = None
        if monte_carlo_payload and monte_carlo_payload.get("brazil_pct") is not None:
            monte_carlo_brazil_pct = _float_probability(monte_carlo_payload.get("brazil_pct"))
        if monte_carlo_brazil_pct is None or abs(monte_carlo_brazil_pct - brazil_pct) > 5.0:
            mc_fragment = (
                f"; Monte Carlo condicional={monte_carlo_brazil_pct:.1f}%"
                if monte_carlo_brazil_pct is not None
                else ""
            )
            errors.append(
                f"{phase} vs {opponent}: brazil_pct={brazil_pct:.1f}% ecoa scenario_pct={float(scenario_pct):.1f}%"
                f"{mc_fragment}"
            )
    if errors:
        raise ReportCoherenceError("Gate de coerência pré-render falhou: " + " | ".join(errors))


def _monte_carlo_phase_opponents(monte_carlo_result: dict[str, Any] | None, phase: str) -> dict[str, dict[str, Any]]:
    if not monte_carlo_result or not monte_carlo_result.get("enabled"):
        return {}
    phase_payload = (monte_carlo_result.get("phases") or {}).get(phase) or {}
    opponents: dict[str, dict[str, Any]] = {}
    for row in phase_payload.get("opponents", []) or []:
        opponent = str(row.get("opponent") or "").strip()
        if opponent:
            opponents[_normalize_text(opponent)] = dict(row)
    return opponents


def _monte_carlo_path_gate_min_iterations(config: dict[str, Any]) -> int:
    mc_config = config.get("monte_carlo") if isinstance(config.get("monte_carlo"), dict) else {}
    return int(mc_config.get("path_gate_min_iterations", 10000))


def _monte_carlo_path_gate_min_rating_coverage_pct(config: dict[str, Any]) -> float:
    mc_config = config.get("monte_carlo") if isinstance(config.get("monte_carlo"), dict) else {}
    return float(mc_config.get("path_gate_min_rating_coverage_pct", 65.0))


def _weighted_scenario_with_monte_carlo(
    scenario_values: list[float],
    *,
    monte_carlo_payload: dict[str, Any] | None,
    prior_weight: float,
) -> float | None:
    mc_value = None
    if monte_carlo_payload and monte_carlo_payload.get("scenario_pct") is not None:
        try:
            mc_value = float(monte_carlo_payload["scenario_pct"])
        except (TypeError, ValueError):
            mc_value = None
    if scenario_values and mc_value is not None:
        return (sum(scenario_values) + mc_value * prior_weight) / (len(scenario_values) + prior_weight)
    if scenario_values:
        return sum(scenario_values) / len(scenario_values)
    if mc_value is not None:
        return mc_value
    return None


def _apply_meeting_knockout_scenarios(
    estimates: list[Any],
    opinions: list[Any],
    *,
    config: dict[str, Any],
    monte_carlo_result: dict[str, Any] | None = None,
) -> None:
    path_by_phase = {entry["phase"]: entry for entry in brazil_bracket_path(config)}
    if not path_by_phase:
        return
    mc_config = config.get("monte_carlo") if isinstance(config.get("monte_carlo"), dict) else {}
    monte_carlo_reliable = monte_carlo_path_gate_is_reliable(
        monte_carlo_result or {},
        min_iterations=_monte_carlo_path_gate_min_iterations(config),
        min_rating_coverage_pct=_monte_carlo_path_gate_min_rating_coverage_pct(config),
    )
    monte_carlo_prior_weight = float(
        mc_config.get(
            "path_gate_reliable_prior_weight" if monte_carlo_reliable else "path_gate_unreliable_prior_weight",
            2.0 if monte_carlo_reliable else 0.35,
        )
    )

    for phase, bracket_entry in path_by_phase.items():
        phase_estimates = [
            estimate
            for estimate in estimates
            if str(getattr(estimate, "phase", "")) == phase
        ]
        if not phase_estimates:
            continue
        monte_carlo_candidates = _monte_carlo_phase_opponents(monte_carlo_result, phase)
        rankings: list[dict[str, Any]] = []
        for opponent in bracket_entry.get("allowed_opponents", []):
            opponent_key = _normalize_text(str(opponent))
            monte_carlo_payload = monte_carlo_candidates.get(opponent_key)
            if monte_carlo_reliable and monte_carlo_candidates and monte_carlo_payload is None:
                continue
            scenario_values = _opinion_probability_values(
                opinions,
                field="scenario_probabilities",
                phase=phase,
                opponent=str(opponent),
            )
            monte_carlo_scenario_pct = None
            if monte_carlo_payload and monte_carlo_payload.get("scenario_pct") is not None:
                monte_carlo_scenario_pct = _float_probability(monte_carlo_payload.get("scenario_pct"))
            match_values = _opinion_match_probability_values(
                opinions,
                phase=phase,
                opponent=str(opponent),
                estimate_scenario_pct=monte_carlo_scenario_pct,
            )
            if not scenario_values and not match_values and not monte_carlo_payload:
                continue
            scenario_avg = _weighted_scenario_with_monte_carlo(
                scenario_values,
                monte_carlo_payload=monte_carlo_payload,
                prior_weight=monte_carlo_prior_weight,
            )
            brazil_pct = round(sum(match_values) / len(match_values), 1) if match_values else None
            if brazil_pct is None and monte_carlo_payload and monte_carlo_payload.get("brazil_pct") is not None:
                try:
                    brazil_pct = round(float(monte_carlo_payload["brazil_pct"]), 1)
                except (TypeError, ValueError):
                    brazil_pct = None
            rankings.append(
                {
                    "opponent": str(opponent),
                    "scenario_pct": round(scenario_avg, 1) if scenario_avg is not None else None,
                    "brazil_pct": brazil_pct,
                    "mentions": len(scenario_values) + len(match_values) + int(bool(monte_carlo_payload)),
                }
            )
        rankings.sort(key=lambda item: (item["scenario_pct"] or 0.0, item["mentions"]), reverse=True)
        if not rankings:
            continue

        phase_estimates.sort(key=lambda estimate: 0 if bool(getattr(estimate, "most_likely", False)) else 1)
        for estimate, ranking in zip(phase_estimates, rankings, strict=False):
            estimate.opponent = ranking["opponent"]
            if ranking["scenario_pct"] is not None:
                estimate.scenario_pct = ranking["scenario_pct"]
            if ranking["brazil_pct"] is not None:
                _set_estimate_brazil_probability(estimate, ranking["brazil_pct"])


def _parallel_opponent_debriefing_enabled(config: dict[str, Any]) -> bool:
    return bool(config.get("parallel_opponent_debriefing_enabled", False))


def _opponent_debriefing_config(config: dict[str, Any]) -> dict[str, Any]:
    knockout_matches: list[dict[str, Any]] = []
    for entry in brazil_bracket_path(config):
        allowed_opponents = [
            str(opponent).strip()
            for opponent in entry.get("allowed_opponents", [])
            if str(opponent).strip()
        ]
        if not allowed_opponents:
            continue
        knockout_matches.append(
            {
                "phase": entry.get("phase"),
                "opponent": "Adversário provável a definir pela sala paralela",
                "most_likely": True,
                "bracket_match_id": entry.get("match_id"),
                "bracket_brazil_slot": entry.get("brazil_slot"),
                "bracket_opponent_slots": entry.get("opponent_slots"),
                "allowed_opponent_groups": entry.get("allowed_opponent_groups"),
                "allowed_opponents": allowed_opponents,
            }
        )
    if not knockout_matches:
        knockout_matches = [dict(match) for match in _default_knockout_matches(config)]
    monte_carlo_summary = monte_carlo_compact_summary(config.get("_monte_carlo_result", {"enabled": False}))

    return {
        **config,
        "_meeting_room": "opponent_path",
        # Contrato de rodadas PRÓPRIO — herdar o da sala principal (min_rounds=6,
        # timeouts de 240s) tornava o timeout de 900s matematicamente garantido:
        # 6 rodadas × ~225-300s nunca cabem no orçamento. Run 615b0948 (11/jun):
        # 4 rodadas completas descartadas, rounds=0, gasto da sala perdido.
        "meeting_min_rounds": int(config.get("opponent_debriefing_min_rounds", 1)),
        "meeting_max_rounds": int(config.get("opponent_debriefing_max_rounds", 3)),
        "meeting_stability_rounds": int(config.get("opponent_debriefing_stability_rounds", 1)),
        "meeting_round_budget_seconds": float(config.get("opponent_debriefing_round_budget_seconds", 0) or 0),
        "agent_timeout_seconds": int(config.get("opponent_debriefing_agent_timeout_seconds", 120)),
        # Sem este override, a 1ª chamada de TODA rodada (pergunta do protagonista)
        # herdava os 210s da sala principal e furava o modelo de orçamento.
        "protagonist_timeout_seconds": int(config.get("opponent_debriefing_protagonist_timeout_seconds", 120)),
        "group_matches": [],
        "knockout_matches": knockout_matches,
        "_path_relevant_group_states": monte_carlo_summary.get("relevant_group_states", {}),
        "_path_phase_relevant_groups": monte_carlo_summary.get("phase_relevant_groups", {}),
        "macro_direction": (
            "Sala paralela de debriefing para adversários prováveis do cruzamento do Brasil. "
            "Simule 16 avos, Oitavas, Quartas, Semifinal e Final dentro do bracket oficial; "
            "use os placares realizados e as tabelas vivas dos grupos de cruzamento expostos em "
            "monte_carlo.relevant_group_states/_path_relevant_group_states como baseline auditável e desafiável, "
            "não como premissa fixa; "
            "placar realizado substitui probabilidade pré-jogo, e tabela viva muda seed, cenário e adversário provável; "
            "para cada candidato permitido, estime scenario_probabilities e match_probabilities com as "
            "mesmas famílias de dados usadas para o Brasil: bets/prediction markets, ratings, Monte Carlo, "
            "Sofascore/performance, lesões/cortes/notícias, amistosos recentes, arbitragem/VAR/cartões, descanso "
            "e imprensa especializada. A sala não decide a chance final do Brasil sozinha; ela acelera e melhora "
            "a escolha dos adversários prováveis que a sala principal usará."
        ),
    }


def _opponent_debriefing_budget_warning(config: dict[str, Any]) -> str | None:
    """Aviso de startup quando o piso de rodadas da sala paralela não cabe no orçamento.

    Pior caso por rodada = pergunta do protagonista (sequencial) + 1,5× timeout de
    agente (respostas em paralelo + consenso/repair), calibrado nos watchdogs de jun/2026."""
    sub_config = _opponent_debriefing_config(config)
    agent_timeout = float(sub_config.get("agent_timeout_seconds", 120))
    protagonist_timeout = float(sub_config.get("protagonist_timeout_seconds", agent_timeout))
    # Pergunta do protagonista (sequencial) + respostas em paralelo + consenso/repair.
    per_round_worst_seconds = protagonist_timeout + 1.5 * agent_timeout
    min_rounds = max(1, int(sub_config.get("meeting_min_rounds", 1)))
    worst_case_seconds = min_rounds * per_round_worst_seconds
    budget_seconds = max(0.001, float(config.get("parallel_opponent_debriefing_timeout_seconds", 900)))
    round_budget_seconds = float(sub_config.get("meeting_round_budget_seconds", 0) or 0)
    if round_budget_seconds > 0 and per_round_worst_seconds > round_budget_seconds:
        return (
            f"sala paralela: ~{per_round_worst_seconds:.0f}s de pior caso por rodada excede "
            f"o orçamento acumulado de rodada ({round_budget_seconds:.0f}s); ajuste "
            "opponent_debriefing_round_budget_seconds ou reduza timeouts da sala lateral"
        )
    if worst_case_seconds > budget_seconds:
        return (
            f"sala paralela: {min_rounds} rodada(s) mínima(s) × ~{per_round_worst_seconds:.0f}s de pior caso "
            f"por rodada = ~{worst_case_seconds:.0f}s não cabe no orçamento de {budget_seconds:.0f}s; "
            "ajuste os knobs opponent_debriefing_*"
        )
    return None


def _knockout_estimates_as_config_matches(estimates: list[Any]) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for estimate in estimates:
        matches.append(
            {
                "phase": str(getattr(estimate, "phase", "")),
                "opponent": str(getattr(estimate, "opponent", "")),
                "most_likely": bool(getattr(estimate, "most_likely", False)),
                "brazil_pct": float(getattr(estimate, "brazil_pct", 0.0)),
                "opponent_pct": float(getattr(estimate, "opponent_pct", 0.0)),
                "scenario_pct": getattr(estimate, "scenario_pct", None),
                "date": getattr(estimate, "match_date", None),
                "venue": getattr(estimate, "venue", None),
                "allowed_opponents": list(getattr(estimate, "allowed_opponents", []) or []),
            }
        )
    return matches


def _parallel_opponent_briefing_for_prompt(result: dict[str, Any], estimates: list[Any]) -> dict[str, Any]:
    if not result.get("enabled") or result.get("failed"):
        return {
            "enabled": bool(result.get("enabled", False)),
            "failed": bool(result.get("failed", False)),
            "error": str(result.get("error", "")),
            "usable_for_main_room": bool(result.get("usable_for_main_room", False)),
            "exit_status": str(result.get("exit_status", "") or ""),
        }
    top_by_phase: dict[str, list[dict[str, Any]]] = {}
    for estimate in estimates:
        phase = str(getattr(estimate, "phase", ""))
        if not phase:
            continue
        top_by_phase.setdefault(phase, []).append(
            {
                "opponent": str(getattr(estimate, "opponent", "")),
                "scenario_pct": getattr(estimate, "scenario_pct", None),
                "brazil_pct": getattr(estimate, "brazil_pct", None),
                "most_likely": bool(getattr(estimate, "most_likely", False)),
            }
        )
    return {
        "enabled": True,
        "failed": False,
        "rounds": int(result.get("rounds", 0) or 0),
        "participants": list(result.get("participants", [])),
        "usable_for_main_room": bool(result.get("usable_for_main_room", False)),
        "exit_status": str(result.get("exit_status", "") or ""),
        "degraded": bool(result.get("degraded", False)),
        "degraded_shadow_only": bool(result.get("degraded_shadow_only", False)),
        "degraded_would_be_usable": bool(result.get("degraded_would_be_usable", False)),
        "top_by_phase": top_by_phase,
        "rule": "sala principal deve usar estes adversários prováveis já reconciliados com bracket/Monte Carlo",
    }


def _side_room_latest_coverage_complete(transcript: list[dict[str, Any]]) -> bool:
    for turn in reversed(transcript):
        coverage = turn.get("coverage")
        if isinstance(coverage, dict):
            return bool(coverage.get("complete", False))
    return False


def _side_room_valid_participant_count(opinions: list[Any]) -> int:
    return sum(1 for opinion in opinions if _counts_as_consensus_participant(opinion))


def _opponent_debriefing_degraded_decision(
    *,
    config: dict[str, Any],
    exit_status: str,
    transcript: list[dict[str, Any]],
    final_opinions: list[Any],
) -> dict[str, Any]:
    enabled = bool(config.get("opponent_debriefing_degraded_consensus_enabled", False))
    shadow_only = bool(config.get("opponent_debriefing_degraded_shadow_only", True))
    minimum_participants = int(config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3)))
    valid_participants = _side_room_valid_participant_count(final_opinions)
    coverage_complete = _side_room_latest_coverage_complete(transcript)
    reasons: list[str] = []
    if not enabled:
        reasons.append("degraded_disabled")
    if exit_status != "degraded_last_valid":
        reasons.append(f"exit_status_{exit_status or 'missing'}")
    if not transcript:
        reasons.append("no_transcript")
    if not coverage_complete:
        reasons.append("coverage_incomplete")
    if valid_participants < minimum_participants:
        reasons.append("insufficient_valid_participants")
    would_be_usable = not reasons
    return {
        "enabled": enabled,
        "shadow_only": shadow_only,
        "would_be_usable": would_be_usable,
        "usable": bool(would_be_usable and not shadow_only),
        "reasons": reasons,
        "valid_participants": valid_participants,
        "minimum_participants": minimum_participants,
        "coverage_complete": coverage_complete,
    }


async def _run_parallel_opponent_debriefing(
    *,
    config: dict[str, Any],
    planning_opinions: list[Any],
    generated_at: datetime,
    agent_specs: list[Any],
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    token_cost_ledger: dict[str, Any],
    watchdog: RunWatchdog | None = None,
    progress_sink: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    opponent_config = _opponent_debriefing_config(config)
    meeting_watchdog = (
        _MeetingWatchdogView(
            watchdog,
            meeting_step="opponent_model_meeting",
            room_step="opponent_model_room",
        )
        if watchdog is not None
        else None
    )
    consensus, final_opinions, transcript, all_opinions = await _run_model_meeting(
        config=opponent_config,
        planning_opinions=planning_opinions,
        generated_at=generated_at,
        agent_specs=agent_specs,
        baseline_title_pct=baseline_title_pct,
        allow_agent_fallback=allow_agent_fallback,
        watchdog=meeting_watchdog,
        token_cost_ledger=token_cost_ledger,
        progress_sink=progress_sink,
        cancel_event=cancel_event,
    )
    exit_status = str(getattr(consensus, "exit_status", "consensus") or "consensus")
    exit_warning = str(getattr(consensus, "exit_warning", "") or "")
    degraded_decision = _opponent_debriefing_degraded_decision(
        config=opponent_config,
        exit_status=exit_status,
        transcript=transcript,
        final_opinions=final_opinions,
    )
    degraded_usable = bool(degraded_decision.get("usable", False))
    usable_for_main_room = exit_status == "consensus" or degraded_usable
    return {
        "enabled": True,
        "consensus": consensus,
        "final_opinions": final_opinions,
        "meeting_transcript": transcript,
        "all_opinions": all_opinions,
        "rounds": len(transcript),
        "participants": _slots_from_specs(agent_specs),
        "exit_status": exit_status,
        "exit_warning": exit_warning,
        "usable_for_main_room": usable_for_main_room,
        "degraded": degraded_usable,
        "degraded_shadow_only": bool(degraded_decision.get("shadow_only", True)),
        "degraded_would_be_usable": bool(degraded_decision.get("would_be_usable", False)),
        "degraded_decision": degraded_decision,
    }


def _agent_debate_prompt(
    *,
    config: dict[str, Any],
    evidence: list[EvidenceResult],
    generated_at: datetime,
    opening_opinions: list[Any],
) -> str:
    opening_lines = []
    for opinion in opening_opinions:
        title_text = (
            f"{float(opinion.title_pct):.1f}%"
            if getattr(opinion, "title_pct", None) is not None
            else "sem número próprio"
        )
        opening_lines.append(
            f"- {opinion.agent}: título={title_text}; "
            f"tese={opinion.summary}; crítica={opinion.critique or 'não informada'}"
        )
    return (
        _agent_prompt(config=config, evidence=evidence, generated_at=generated_at)
        + "\n\nRodada 1 dos outros modelos:\n"
        + "\n".join(opening_lines)
        + "\n\nAgora faça a Rodada 2: critique explicitamente pelo menos uma premissa de outro modelo, "
        "diga se move sua probabilidade de título para cima/baixo/igual e responda em JSON estrito "
        "com self_identification, title_pct, summary, opening_argument, critique e adjustment."
    )


def _room_specific_protagonist_instruction(config: dict[str, Any]) -> str:
    if str(config.get("_meeting_room", "main_brazil") or "main_brazil") != "opponent_path":
        return ""
    return (
        "Nesta sala lateral de cruzamentos, formule uma pergunta decisória: peça aos modelos que escolham "
        "um top-2 por fase entre os adversários permitidos pelo bracket oficial, estimem a chance de cada "
        "cenário acontecer, apontem o dado que moveria essa ordem e digam se o baseline auditável e "
        "desafiável do Monte Carlo deve ser mantido, corrigido ou substituído por evidência fresca. "
        "Não trate o Monte Carlo como premissa fixa; ele é o ponto de partida a ser auditado."
    )


def _opponent_room_top_two_response_contract(config: dict[str, Any]) -> str:
    if str(config.get("_meeting_room", "main_brazil") or "main_brazil") != "opponent_path":
        return ""
    phases = ["16 avos", "Oitavas", "Quartas", "Semifinal", "Final"]
    phase_list = ", ".join(phases)
    return (
        "Contrato específico da sala lateral de cruzamentos: responda fase por fase para "
        f"{phase_list}. Para cada fase, escolha exatamente top-2 adversários permitidos pelo bracket, "
        "inclua a chance do cenário acontecer em scenario_probabilities com chaves no formato "
        "'Fase: Seleção', inclua a chance do Brasil no confronto em match_probabilities quando houver "
        "base auditável, e cite pelo menos uma fonte/query por fase ou explique por que o Monte Carlo/bracket "
        "permanece melhor que a evidência fresca. Cubra explicitamente todas as fases na answer. "
        "Se não houver duas opções fortes numa fase, preencha a segunda como 'Fase: Adversário alternativo a definir' "
        "com rationale, em vez de omitir a fase."
    )


def _protagonist_question_prompt(
    *,
    config: dict[str, Any],
    protagonist: str,
    previous_turn: dict[str, Any] | None,
    generated_at: datetime,
) -> str:
    previous_context = json.dumps(previous_turn or {}, ensure_ascii=False)[:3500]
    macro_direction = config.get(
        "macro_direction",
        "Brasil na Copa do Mundo de 2026: números e contexto como direcional inicial, sem viés pró-Brasil.",
    )
    return (
        f"Você é {protagonist}, o protagonista atual da sala de reunião dos modelos. "
        "Esta é uma reunião de debriefing multi-turn: não trate a sala como uma lista fixa de falas, "
        "porque cada modelo pode falar várias vezes, responder objeções e voltar ao protagonismo. "
        "Faça a próxima pergunta aos outros modelos para reduzir a discordância e chegar "
        "ao consenso sobre o percentual do Brasil jogo a jogo e até onde ele deve ir no Modelo Principal. "
        "A reunião só deve convergir quando o consenso tiver maioria simples entre os participantes ativos da sala, "
        "não porque um contador fixo de falas terminou. "
        f"{_agent_owned_fresh_search_contract()} "
        f"{_effort_latency_instruction()} "
        f"{_self_identification_instruction()} "
        "Responda em JSON estrito com: self_identification, question, title_pct, summary, answer, source_urls, source_queries, scenario_probabilities. "
        "title_pct deve ser sempre um número da chance de título do Brasil, nunca um objeto; "
        "probabilidades jogo a jogo devem ir somente em match_probabilities quando forem necessárias; "
        "probabilidade de um confronto específico acontecer deve ir em scenario_probabilities. "
        "Se uma premissa por seleção deve afetar a simulação de adversários, inclua team_context_signals com team, category, rating_delta ou probability_delta_pct, confidence, rationale e source_url/source_query. "
        "Na sua question, exponha sua opinião/racional em uma frase e pergunte explicitamente se os outros modelos "
        "concordam ou discordam dela, se concordam integralmente e se a sala pode sair com consenso para avançar "
        "para as próximas etapas. "
        "A pergunta precisa citar uma premissa concreta a testar usando odds, prediction markets, "
        "Elo/ranking, Monte Carlo, Sofascore/performance de jogadores, lesões, cortes, cartões, arbitragem/VAR, descanso ou chaveamento. "
        f"{_opponent_research_instruction(config)} "
        f"{_quantitative_qualitative_decision_instruction()} "
        f"{_meeting_full_path_instruction(config)} "
        f"{_opponent_room_top_two_response_contract(config)} "
        f"{_event_impact_prompt_instruction()} "
        f"{_auditable_sources_instruction(config)} "
        f"{_room_specific_protagonist_instruction(config)} "
        "Não faça comparação com benchmarks externos nesta sala; esse passo acontece em prompt separado depois. "
        f"{_meeting_scope_instruction(config)}\n\n"
        f"Data: {generated_at.isoformat()}\n"
        f"Direcionamento macro: {macro_direction}\n"
        f"Rodada anterior: {previous_context}\n"
    )


def _meeting_response_prompt(
    *,
    config: dict[str, Any],
    round_index: int,
    protagonist: str,
    question: str,
    previous_turn: dict[str, Any] | None,
    generated_at: datetime,
) -> str:
    previous_context = json.dumps(previous_turn or {}, ensure_ascii=False)[:3500]
    matches = _configured_matches_for_prompt(config)
    return (
        "Você está na sala de reunião de debriefing dos modelos. Responda à pergunta do protagonista com rigor e clareza. "
        "Não trate a sala como uma lista fixa de falas: você pode falar várias vezes ao longo da reunião, "
        "mudar sua posição, contestar uma premissa anterior ou aceitar uma tese melhor. "
        "O objetivo é formar um consenso aceito por maioria simples dos participantes ativos, não encerrar por quantidade fixa de mensagens. "
        f"{_agent_owned_fresh_search_contract()} "
        f"{_effort_latency_instruction()} "
        f"{_self_identification_instruction()} "
        "Responda em JSON estrito com: self_identification, title_pct, summary, answer, critique, adjustment, source_urls, source_queries, "
        "match_probabilities, scenario_probabilities, team_context_signals, agrees_with_protagonist, leadership_bid, proposed_next_question, leadership_rationale, consensus_check_question. "
        "agrees_with_protagonist deve ser booleano: true se você aceita "
        "o racional numérico do protagonista, false se discorda e quer assumir o protagonismo. "
        "Não fique passivo: mesmo quando aceitar o argumento do protagonista, dispute a liderança por mérito se tiver "
        "uma próxima pergunta melhor, fonte nova ou teste numérico auditável que ajude a sala. Nesse caso, mantenha "
        "agrees_with_protagonist=true, use leadership_bid=true, preencha proposed_next_question e explique em "
        "leadership_rationale. Não invente discordância para virar protagonista: se o argumento for válido, aceite. "
        "Use leadership_bid=false quando sua contribuição não melhorar a próxima rodada. "
        "No fim da sua answer e no campo consensus_check_question, faça uma pergunta direta aos demais modelos: "
        "se eles concordam integralmente com a sua opinião e se a sala pode sair com consenso para avançar para as próximas etapas. "
        "title_pct deve ser sempre um número da chance de título do Brasil, nunca um objeto. "
        "Em match_probabilities, use chaves descritivas como 'Grupo: Marrocos', "
        "'16 avos: Uruguai' ou 'Oitavas: Uruguai' e valores percentuais de chance do Brasil vencer/passar. "
        "Em scenario_probabilities, use chaves como '16 avos: Holanda' para a chance daquele confronto acontecer; "
        "só use candidatos permitidos por allowed_opponents/bracket_opponent_slots. "
        f"{_opponent_room_top_two_response_contract(config)} "
        "Em team_context_signals, registre sinais por seleção com team, category, rating_delta ou probability_delta_pct, confidence, rationale e source_url/source_query; "
        "use as mesmas famílias de dados do Brasil para adversários: bets/prediction markets, ratings, Sofascore/performance, lesões/cortes/notícias recentes, amistosos recentes, arbitragem/VAR/cartões e imprensa especializada. "
        "A decisão não é do orquestrador: ela deve sair do debate. Este é o Modelo Principal: use sportsbooks, "
        "prediction markets, ratings independentes, Monte Carlo, Sofascore/performance de jogadores, lesões, cortes, cartões, "
        "arbitragem/VAR, descanso e chaveamento. Não faça comparação com benchmarks externos nesta resposta; "
        "esse passo acontece em prompt separado depois. "
        f"{_opponent_research_instruction(config)} "
        f"{_quantitative_qualitative_decision_instruction()} "
        f"{_meeting_full_path_instruction(config)} "
        f"{_event_impact_prompt_instruction()} "
        f"{_auditable_sources_instruction(config)} "
        "Use linguagem entendível para LinkedIn, mas explique o método usado. "
        f"{_meeting_scope_instruction(config)}\n\n"
        f"Rodada: {round_index}\n"
        f"Protagonista: {protagonist}\n"
        f"Pergunta: {question}\n"
        f"Data: {generated_at.isoformat()}\n"
        f"Jogos/cenários: {json.dumps(matches, ensure_ascii=False)}\n"
        f"Rodada anterior: {previous_context}\n"
    )


def _is_repairable_meeting_removal(raw_opinion: Any, sanitized_opinion: Any) -> bool:
    if bool(getattr(raw_opinion, "used_fallback", False)):
        return False
    if not bool(getattr(sanitized_opinion, "used_fallback", False)):
        return False
    if bool(getattr(sanitized_opinion, "removed_from_main", False)):
        return True
    summary = str(getattr(sanitized_opinion, "summary", "") or "")
    return "Resposta removida do Modelo Principal" in summary


def _meeting_response_repair_prompt(
    *,
    config: dict[str, Any],
    round_index: int,
    protagonist: str,
    question: str,
    previous_turn: dict[str, Any] | None,
    raw_opinion: Any,
    sanitized_opinion: Any,
    generated_at: datetime,
) -> str:
    matches = _configured_matches_for_prompt(config)
    previous_context = json.dumps(previous_turn or {}, ensure_ascii=False)[:2200]
    bad_text = json.dumps(
        {
            "summary": getattr(raw_opinion, "summary", ""),
            "answer": getattr(raw_opinion, "answer", ""),
            "critique": getattr(raw_opinion, "critique", ""),
            "adjustment": getattr(raw_opinion, "adjustment", ""),
        },
        ensure_ascii=False,
    )[:2200]
    feedback = str(getattr(sanitized_opinion, "summary", "") or "")
    return (
        "Sua resposta anterior foi removida pelo moderador por violar o contrato operacional da sala. "
        "Este feedback é dirigido apenas a você; os outros modelos não precisam repetir a rodada. "
        "Repare a resposta agora em JSON estrito com: self_identification, title_pct, summary, answer, "
        "critique, adjustment, source_urls, source_queries, match_probabilities, scenario_probabilities, agrees_with_protagonist, "
        "leadership_bid, proposed_next_question, leadership_rationale, consensus_check_question. "
        "Não cite adversário de grupo fora do JSON. Para mata-mata sem adversário definido, use apenas a fase "
        "e os rótulos configurados, como '16 avos: Adversário mais provável a definir'. "
        "Não invente fonte, URL, score, rating, lesão, suspensão, arbitragem, adversário ou método. "
        "Se você não conseguiu executar busca/fetch nesta chamada, diga isso em summary e deixe source_urls/source_queries "
        "vazios; consulta planejada não conta como fonte verificável. "
        "Se concordar ou discordar, apresente hipótese auditável com número e fonte/query. "
        "No fim da answer e em consensus_check_question, pergunte diretamente se os demais concordam integralmente "
        "e se a sala pode sair com consenso para avançar para as próximas etapas. "
        "Não use benchmark reservado nem Opta no Modelo Principal. "
        "Se não houver evidência para mover uma probabilidade, diga isso e mantenha shift 0.\n\n"
        f"Data: {generated_at.isoformat()}\n"
        f"Rodada: {round_index}\n"
        f"Protagonista: {protagonist}\n"
        f"Pergunta original: {question}\n"
        f"Feedback do moderador: {feedback}\n"
        f"Sua resposta anterior removida: {bad_text}\n"
        f"Jogos/cenários permitidos: {json.dumps(matches, ensure_ascii=False)}\n"
        f"Rodada anterior: {previous_context}\n"
    )


def _meeting_coverage_report(meeting_transcript: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    combined_parts: list[str] = []
    for turn in meeting_transcript:
        combined_parts.append(str(turn.get("question", "")))
        for response in turn.get("responses", []):
            combined_parts.append(str(response.get("answer", "")))
    text = _normalize_text(" ".join(combined_parts))

    group_opponents = [
        str(match.get("opponent", "")).strip()
        for match in _default_group_matches(config)
        if str(match.get("opponent", "")).strip()
    ]
    knockout_phases = list(
        dict.fromkeys(
            str(match.get("phase", "")).strip()
            for match in _default_knockout_matches(config)
            if str(match.get("phase", "")).strip()
        )
    )
    missing_group = [
        opponent
        for opponent in group_opponents
        if _normalize_text(opponent) not in text
    ]
    missing_phases = [
        phase
        for phase in knockout_phases
        if _normalize_text(phase) not in text
    ]
    title_covered = any(token in text for token in ("titulo", "campeao", "campea", "chance de titulo"))
    return {
        "complete": not missing_group and not missing_phases and title_covered,
        "missing_group_opponents": missing_group,
        "missing_knockout_phases": missing_phases,
        "title_covered": title_covered,
    }


async def _repair_invalid_meeting_responses(
    *,
    config: dict[str, Any],
    round_index: int,
    protagonist: str,
    question: str,
    previous_turn: dict[str, Any] | None,
    generated_at: datetime,
    responder_specs: list[Any],
    raw_opinions: list[Any],
    sanitized_opinions: list[Any],
    baseline_title_pct: float,
    allow_agent_fallback: bool,
    timeout: int,
) -> tuple[list[Any], list[Any]]:
    attempts = max(0, int(config.get("meeting_response_repair_attempts", 1)))
    if attempts <= 0:
        return sanitized_opinions, []

    spec_by_slot = _agent_spec_by_slot(responder_specs)
    checked_by_agent: dict[str, Any] = {}
    raw_repairs: list[Any] = []

    for raw_opinion, sanitized_opinion in zip(raw_opinions, sanitized_opinions, strict=False):
        if not _is_repairable_meeting_removal(raw_opinion, sanitized_opinion):
            continue
        spec = spec_by_slot.get(str(getattr(raw_opinion, "agent", "")))
        if spec is None:
            continue
        repair_prompt = _meeting_response_repair_prompt(
            config=config,
            round_index=round_index,
            protagonist=protagonist,
            question=question,
            previous_turn=previous_turn,
            raw_opinion=raw_opinion,
            sanitized_opinion=sanitized_opinion,
            generated_at=generated_at,
        )
        repaired = await call_agent(
            spec,
            repair_prompt,
            baseline_title_pct=baseline_title_pct,
            timeout=timeout,
            allow_local_fallback=allow_agent_fallback,
        )
        raw_repairs.append(repaired)
        checked = _sanitize_main_meeting_opinions(
            [repaired],
            baseline_title_pct=baseline_title_pct,
            config=config,
            semantic_policy_stage="post_repair",
            admit_policy_suspected=True,
        )[0]
        checked_by_agent[checked.agent] = checked

    if not checked_by_agent:
        return sanitized_opinions, raw_repairs

    merged = [
        checked_by_agent.get(str(getattr(opinion, "agent", "")), opinion)
        for opinion in sanitized_opinions
    ]
    return merged, raw_repairs


def _compose_debate_transcript(opening_opinions: list[Any], final_consensus: Any) -> list[str]:
    lines = []
    for opinion in opening_opinions:
        title_text = (
            f"{float(opinion.title_pct):.1f}%"
            if getattr(opinion, "title_pct", None) is not None
            else "sem número próprio"
        )
        lines.append(f"Rodada 1 - {opinion.agent}: {opinion.summary} Projeção inicial de título: {title_text}.")
    for line in final_consensus.debate_transcript:
        if line.startswith("Rodada 1"):
            lines.append(line.replace("Rodada 1", "Rodada 2 - ajuste pós-crítica", 1))
        elif line.startswith("Rodada 2"):
            lines.append(line.replace("Rodada 2", "Rodada 3 - crítica final", 1))
        elif line.startswith("Rodada 3"):
            lines.append(line.replace("Rodada 3", "Rodada 4 - ajustes finais", 1))
        else:
            lines.append(line)
    return lines


def calculate_model_influence(opening_opinions: list[Any], final_opinions: list[Any], consensus: Any) -> dict[str, float]:
    final_by_agent = {opinion.agent: opinion for opinion in final_opinions}
    opening_by_agent: dict[str, list[Any]] = {}
    for opinion in opening_opinions:
        opening_by_agent.setdefault(opinion.agent, []).append(opinion)
    scores: dict[str, float] = {}
    for agent, final in final_by_agent.items():
        opening_items = opening_by_agent.get(agent, [])
        source_url_count = sum(len(getattr(opinion, "source_urls", [])) for opinion in opening_items)
        source_query_count = sum(len(getattr(opinion, "source_queries", [])) for opinion in opening_items)
        score = 1.0
        if getattr(final, "removed_from_main", False):
            scores[agent] = 0.05
            continue
        if getattr(final, "used_fallback", False):
            score *= 0.35
        if source_url_count:
            score += min(0.6, source_url_count * 0.15)
        if source_query_count:
            score += min(0.4, source_query_count * 0.10)
        if getattr(final, "critique", ""):
            score += 0.25
        if getattr(final, "adjustment", ""):
            score += 0.20
        try:
            final_title_pct = float(final.title_pct) if getattr(final, "title_pct", None) is not None else float(
                consensus.title_pct
            )
        except (TypeError, ValueError):
            final_title_pct = float(consensus.title_pct)
        distance = abs(final_title_pct - float(consensus.title_pct))
        score += max(0.0, 0.8 - distance * 0.08)
        scores[agent] = max(score, 0.05)

    total = sum(scores.values()) or 1.0
    normalized = {agent: round(score / total * 100, 1) for agent, score in scores.items()}
    drift = round(100.0 - sum(normalized.values()), 1)
    if normalized and drift:
        top_agent = max(normalized, key=normalized.get)
        normalized[top_agent] = round(normalized[top_agent] + drift, 1)
    return normalized


def _emit_low_influence_cost_alerts(
    *,
    model_influence_pct: dict[str, float],
    model_token_costs: dict[str, Any] | None,
    config: dict[str, Any],
    warnings: list[str],
    watchdog: RunWatchdog | None,
) -> None:
    """Regressão do run 615b0948 (11/jun): o Perplexity Pro custou US$0,954 (15% do total)
    por 0,5% de influência, com exit 0 e zero sinais. Agente que cobra mas quase não move o
    consenso passava silencioso. Agora: agente abaixo de low_influence_alert_pct com custo > 0
    gera watchdog event 'degraded' e um aviso em bundle.warnings."""
    threshold = float(config.get("low_influence_alert_pct", 2.0))
    by_model = (model_token_costs or {}).get("by_model", {})
    for agent, influence_pct in model_influence_pct.items():
        cost_usd = float(by_model.get(agent, {}).get("cost_usd", 0.0) or 0.0)
        if cost_usd <= 0 or float(influence_pct) >= threshold:
            continue
        if watchdog:
            watchdog.event(
                "model_influence",
                "degraded",
                detail=(
                    f"{agent} custou US${cost_usd:.3f} por apenas {float(influence_pct):.1f}% de "
                    f"influência (limiar {threshold:.1f}%); revise se vale manter o slot no run"
                ),
                extra={
                    "agent": agent,
                    "influence_pct": float(influence_pct),
                    "cost_usd": cost_usd,
                },
            )
        warnings.append(
            f"{agent} custou US${cost_usd:.3f} por apenas {float(influence_pct):.1f}% de influência "
            f"(abaixo do limiar de {threshold:.1f}%); avalie remover o slot para conter custo sem sinal."
        )


def _phase_labels_for_consensus_questions(config: dict[str, Any] | None) -> list[str]:
    if not config:
        return []
    phases = ["Fase de grupos"]
    phases.extend(
        dict.fromkeys(
            str(match.get("phase", "")).strip()
            for match in _default_knockout_matches(config)
            if str(match.get("phase", "")).strip()
        )
    )
    return phases


def _contains_phrase(text: str, phrase: str) -> bool:
    normalized_text = _normalize_text(text)
    normalized_phrase = _normalize_text(phrase).strip()
    if not normalized_phrase:
        return False
    return bool(re.search(rf"\b{re.escape(normalized_phrase)}\b", normalized_text))


def _turn_text(turn: dict[str, Any]) -> str:
    parts = [str(turn.get("question", ""))]
    for response in turn.get("responses", []):
        parts.append(str(response.get("answer", "")))
    return " ".join(parts)


def _turn_mentions_consensus_phase(turn: dict[str, Any], phase: str, config: dict[str, Any] | None) -> bool:
    text = _turn_text(turn)
    normalized = _normalize_text(text)
    if phase == "Fase de grupos":
        if re.search(r"\b(grupo|fase de grupos)\b", normalized):
            return True
        if not config:
            return False
        return any(
            _contains_phrase(text, str(match.get("opponent", "")))
            for match in _default_group_matches(config)
            if str(match.get("opponent", "")).strip()
        )
    return _contains_phrase(text, phase)


def _consensus_questions_by_phase(
    meeting_transcript: list[dict[str, Any]],
    config: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for phase in _phase_labels_for_consensus_questions(config):
        selected_turn = None
        for turn in meeting_transcript:
            if _turn_mentions_consensus_phase(turn, phase, config):
                selected_turn = turn
        if not selected_turn:
            continue
        entries.append(
            {
                "phase": phase,
                "round": int(selected_turn.get("round", 0)),
                "protagonist": str(selected_turn.get("protagonist", "")).strip(),
                "question": str(selected_turn.get("question", "")).strip(),
            }
        )
    return entries


def calculate_model_participation(
    meeting_transcript: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    by_model: dict[str, dict[str, int]] = {}
    total_messages = 0
    valid_messages = 0
    total_questions = 0
    total_responses = 0
    valid_responses = 0
    invalid_responses = 0
    protagonist_counts: dict[str, int] = {}
    rounds: list[dict[str, Any]] = []

    def entry(agent: str) -> dict[str, int]:
        return by_model.setdefault(
            agent,
            {
                "messages": 0,
                "questions": 0,
                "responses": 0,
                "valid_responses": 0,
                "invalid_responses": 0,
            },
        )

    def response_is_valid(response: dict[str, Any]) -> bool:
        if bool(response.get("removed_from_main")):
            return False
        if bool(response.get("used_fallback")) and int(response.get("source_count", 0) or 0) <= 0:
            return False
        return True

    for turn in meeting_transcript:
        protagonist = str(turn.get("protagonist", "")).strip()
        participants: list[str] = []
        seen_participants: set[str] = set()
        if protagonist:
            stats = entry(protagonist)
            stats["messages"] += 1
            stats["questions"] += 1
            protagonist_counts[protagonist] = protagonist_counts.get(protagonist, 0) + 1
            total_messages += 1
            valid_messages += 1
            total_questions += 1
            participants.append(protagonist)
            seen_participants.add(protagonist)
        for response in turn.get("responses", []):
            agent = str(response.get("agent", "")).strip()
            if not agent:
                continue
            if agent not in seen_participants:
                participants.append(agent)
                seen_participants.add(agent)
            stats = entry(agent)
            stats["messages"] += 1
            stats["responses"] += 1
            total_messages += 1
            total_responses += 1
            if response_is_valid(response):
                stats["valid_responses"] += 1
                valid_messages += 1
                valid_responses += 1
            else:
                stats["invalid_responses"] += 1
                invalid_responses += 1
        if protagonist or participants:
            rounds.append(
                {
                    "round": int(turn.get("round", len(rounds) + 1)),
                    "protagonist": protagonist,
                    "protagonist_count": protagonist_counts.get(protagonist, 0) if protagonist else 0,
                    "participants": participants,
                }
            )

    last_turn = meeting_transcript[-1] if meeting_transcript else {}
    last_participants = rounds[-1]["participants"] if rounds else []

    return {
        "total_messages": total_messages,
        "valid_messages": valid_messages,
        "total_questions": total_questions,
        "total_responses": total_responses,
        "valid_responses": valid_responses,
        "invalid_responses": invalid_responses,
        "total_rounds": len(meeting_transcript),
        "protagonist_counts": protagonist_counts,
        "rounds": rounds,
        "last_consensus_round": int(last_turn.get("round", 0)) if last_turn else 0,
        "last_consensus_protagonist": str(last_turn.get("protagonist", "")).strip() if last_turn else "",
        "last_consensus_question": str(last_turn.get("question", "")).strip() if last_turn else "",
        "last_consensus_participants": last_participants,
        "consensus_questions_by_phase": _consensus_questions_by_phase(meeting_transcript, config),
        "by_model": by_model,
    }


def _model_predictions_no_opta(opinions: list[Any]) -> dict[str, dict[str, Any]]:
    predictions: dict[str, dict[str, Any]] = {}
    for opinion in opinions:
        raw_title_pct = getattr(opinion, "title_pct", None)
        try:
            title_pct = round(float(raw_title_pct), 1) if raw_title_pct is not None else None
        except (TypeError, ValueError):
            title_pct = None
        predictions[opinion.agent] = {
            "title_pct": title_pct,
            "title_pct_source": str(getattr(opinion, "title_pct_source", "") or "explicit"),
            "summary": opinion.summary,
            "answer": opinion.answer,
            "source_urls": [url for url in getattr(opinion, "source_urls", []) if not _has_opta_marker(url)],
            "source_queries": [query for query in getattr(opinion, "source_queries", []) if not _has_opta_marker(query)],
            "match_probabilities": dict(getattr(opinion, "match_probabilities", {})),
            "scenario_probabilities": dict(getattr(opinion, "scenario_probabilities", {})),
            "used_fallback": bool(getattr(opinion, "used_fallback", False)),
            "removed_from_main": bool(getattr(opinion, "removed_from_main", False)),
            "removal_reason": str(getattr(opinion, "removal_reason", "") or ""),
        }
    return predictions


def _model_self_identification(opinions: list[Any]) -> dict[str, dict[str, str]]:
    identities: dict[str, dict[str, str]] = {}
    for opinion in opinions:
        agent = str(getattr(opinion, "agent", "")).strip()
        if not agent:
            continue
        name = str(getattr(opinion, "self_declared_name", "") or "").strip()
        version = str(getattr(opinion, "self_declared_version", "") or "").strip()
        if not name and not version:
            continue
        identities[agent] = {
            "name": name,
            "version": version,
            "source": "declarado pelo próprio modelo no JSON da rodada",
        }
    return identities


def _group_summary_from_monte_carlo(config: dict[str, Any], monte_carlo_result: dict[str, Any]) -> str:
    group_state = monte_carlo_result.get("group_state") if isinstance(monte_carlo_result, dict) else {}
    if not isinstance(group_state, dict) or not group_state:
        return str(config.get("group_summary", ""))
    first_pct = group_state.get("brazil_first_pct")
    top2_pct = group_state.get("brazil_top2_pct")
    if first_pct is None and top2_pct is None:
        return str(config.get("group_summary", ""))
    parts: list[str] = []
    if first_pct is not None:
        parts.append(f"Brasil em 1º: ~{float(first_pct):.1f}%")
    if top2_pct is not None:
        parts.append(f"Top-2 do grupo: ~{float(top2_pct):.1f}%")
    completed = group_state.get("completed_results") or []
    if completed:
        scores = [
            str(item.get("score") or "").strip()
            for item in completed
            if isinstance(item, dict) and str(item.get("score") or "").strip()
        ]
        if scores:
            parts.append("placares já condicionados: " + "; ".join(scores[:4]))
    return ". ".join(parts) + "." if parts else str(config.get("group_summary", ""))


async def build_report_bundle(
    *,
    config: dict[str, Any],
    source_memory: SourceMemory,
    generated_at: datetime | None = None,
    allow_agent_fallback: bool = True,
    watchdog: RunWatchdog | None = None,
) -> RunArtifacts:
    _apply_runtime_env_overrides(config)
    generated_at = generated_at or datetime.now(timezone.utc)
    run_id = str(getattr(watchdog, "run_id", "") or config.get("run_id") or "").strip()
    baseline_title_pct = float(config.get("baseline_title_pct", 11.0))
    agent_specs = load_agent_specs_from_config(config)
    agent_specs = _specs_after_preflight_exclusion(agent_specs, config, watchdog)
    minimum_source_ready_agents = int(config.get("minimum_source_ready_agents", 3))
    if len(agent_specs) < minimum_source_ready_agents:
        excluded_by_preflight = ", ".join(
            str(slot) for slot in (config.get("_preflight_failed_slots") or [])
        )
        message = (
            f"Quórum impossível antes do planejamento: {len(agent_specs)} slot(s) ativo(s) "
            f"contra mínimo de {minimum_source_ready_agents}; excluído(s) por falha dura no "
            f"preflight: {excluded_by_preflight or 'nenhum'}."
        )
        if watchdog:
            watchdog.fail("agent_source_planning", detail=message)
        raise SourcePlanningQuorumError(message)
    token_cost_ledger = _new_token_cost_ledger(config)
    monte_carlo_result = run_brazil_monte_carlo(config)
    config["_monte_carlo_result"] = monte_carlo_result
    if watchdog and monte_carlo_result.get("enabled"):
        watchdog.event(
            "monte_carlo",
            "finish",
            detail=(
                f"{monte_carlo_result.get('iterations')} tournament simulations; "
                f"seed={monte_carlo_result.get('seed')}; "
                f"rating_coverage={monte_carlo_result.get('rating_coverage_pct')}%"
            ),
            extra=monte_carlo_compact_summary(monte_carlo_result),
        )
    planning_opinions = []
    if watchdog:
        watchdog.start(
            "agent_source_planning",
            detail=_agent_source_planning_watchdog_detail(config),
            extra=_agent_source_planning_watchdog_extra(config),
        )
    source_planning_prompt = _source_planning_prompt(config=config, generated_at=generated_at)
    raw_planning_opinions = await call_all_agents(
        source_planning_prompt,
        specs=agent_specs,
        baseline_title_pct=baseline_title_pct,
        timeout=int(config.get("agent_timeout_seconds", 90)),
        allow_local_fallback=allow_agent_fallback,
    )
    _record_token_costs(
        token_cost_ledger,
        config=config,
        prompt=source_planning_prompt,
        opinions=raw_planning_opinions,
        stage="source_planning",
    )
    planning_opinions = _sanitize_source_planning_opinions(
        raw_planning_opinions,
        baseline_title_pct=baseline_title_pct,
        config=config,
    )
    source_readiness_report = _source_planning_readiness_report(planning_opinions, config)
    if watchdog:
        _emit_source_planning_readiness(watchdog, source_readiness_report, final=False)
    if not bool(source_readiness_report["quorum_met"]):
        planning_opinions, source_readiness_report = await _self_heal_source_planning_quorum(
            config=config,
            planning_opinions=planning_opinions,
            source_readiness_report=source_readiness_report,
            agent_specs=agent_specs,
            generated_at=generated_at,
            baseline_title_pct=baseline_title_pct,
            allow_agent_fallback=allow_agent_fallback,
            watchdog=watchdog,
            token_cost_ledger=token_cost_ledger,
        )
    else:
        planning_opinions, source_readiness_report = await _repair_format_only_planning_removals(
            config=config,
            planning_opinions=planning_opinions,
            source_readiness_report=source_readiness_report,
            agent_specs=agent_specs,
            generated_at=generated_at,
            baseline_title_pct=baseline_title_pct,
            allow_agent_fallback=allow_agent_fallback,
            watchdog=watchdog,
            token_cost_ledger=token_cost_ledger,
        )
        planning_opinions, source_readiness_report = await _repair_quorum_floor_planning_removals(
            config=config,
            planning_opinions=planning_opinions,
            source_readiness_report=source_readiness_report,
            agent_specs=agent_specs,
            generated_at=generated_at,
            baseline_title_pct=baseline_title_pct,
            allow_agent_fallback=allow_agent_fallback,
            watchdog=watchdog,
            token_cost_ledger=token_cost_ledger,
        )
    source_planning_warnings = _source_planning_policy_warnings(source_readiness_report)
    removed_agent_slots = [str(entry["agent"]) for entry in source_readiness_report["removed_agents"]]
    removed_issues_by_agent = {
        str(entry.get("agent", "")): list(entry.get("validation_issues", []) or [])
        for entry in source_readiness_report.get("removed_agents", [])
    }
    reentry_eligible_removed_slots = {
        str(entry.get("agent", ""))
        for entry in source_readiness_report.get("removed_agents", [])
        if bool(entry.get("reentry_eligible", False))
    }
    active_agent_specs = [spec for spec in agent_specs if spec.slot not in removed_agent_slots]
    reentry_candidate_specs = (
        [spec for spec in agent_specs if spec.slot in reentry_eligible_removed_slots]
        if bool(config.get("agent_reentry_probe_enabled", False))
        else []
    )
    active_planning_opinions = [
        opinion for opinion in planning_opinions if opinion.agent not in removed_agent_slots
    ]
    if watchdog:
        _emit_source_planning_readiness(watchdog, source_readiness_report, final=True)
    if not bool(source_readiness_report["quorum_met"]):
        raise SourcePlanningQuorumError(_source_planning_quorum_error(source_readiness_report))
    if watchdog:
        watchdog.finish("agent_source_planning", detail="source planning round completed without moderator fetch")
    removed_reason_by_agent = {
        str(entry.get("agent", "")): str(entry.get("reason", ""))
        for entry in source_readiness_report.get("removed_agents", [])
    }
    for agent in removed_agent_slots:
        _token_cost_entry(token_cost_ledger, agent)["removed_from_decision"] = True
        if watchdog:
            reason = removed_reason_by_agent.get(agent, "não trouxe plano de fontes próprio e verificável")
            issues = removed_issues_by_agent.get(agent, [])
            primary_issue = _primary_validation_issue(issues, reason)
            reentry_eligible = bool(primary_issue.get("reentry_eligible", False))
            decision_reason = str(primary_issue.get("reentry_decision_reason", ""))
            excerpt = str(primary_issue.get("offending_excerpt", ""))
            detail = (
                f"removido por {primary_issue.get('gate_name') or 'source_planning'}"
                f"/{primary_issue.get('matched_rule') or 'contrato'}: {reason}; "
                f"trecho que disparou: {excerpt or 'não disponível'}; "
                f"reentry elegível: {'sim' if reentry_eligible else 'não'}; "
                f"motivo da decisão: {decision_reason or 'sem política explícita'}"
            )
            watchdog.chat(
                agent,
                detail,
                round_name="agent-removal",
                extra={
                    "validation_issues": issues,
                    "offending_excerpt": excerpt,
                    "reentry_eligible": reentry_eligible,
                    "reentry_decision_reason": decision_reason,
                },
            )

    team_context_report = _apply_agent_team_context_to_monte_carlo_config(config, active_planning_opinions)
    if watchdog:
        watchdog.event(
            "team_context_signals",
            "finish",
            detail=(
                f"{team_context_report['applied_signal_count']} signal(s) applied from active agents; "
                f"{team_context_report['ignored_signal_count']} ignored"
            ),
            extra=team_context_report,
        )
    if team_context_report["applied_signal_count"] or team_context_report["ignored_signal_count"]:
        monte_carlo_result = run_brazil_monte_carlo(config)
        config["_monte_carlo_result"] = monte_carlo_result
        if watchdog and monte_carlo_result.get("enabled"):
            watchdog.event(
                "monte_carlo",
                "finish",
                detail=(
                    "context-adjusted simulation after agent source planning; "
                    f"{monte_carlo_result.get('iterations')} tournament simulations; "
                    f"seed={monte_carlo_result.get('seed')}; "
                    f"rating_coverage={monte_carlo_result.get('rating_coverage_pct')}%"
                ),
                extra=monte_carlo_compact_summary(monte_carlo_result),
            )

    evidence: list[EvidenceResult] = []
    warnings: list[str] = [
        f"Configuração de bracket inválida: {error}"
        for error in invalid_configured_knockout_opponents(config)
    ]
    for team_context_warning in _team_context_warning_messages(monte_carlo_result):
        if team_context_warning not in warnings:
            warnings.append(team_context_warning)

    if watchdog:
        watchdog.start("estimate_matches", detail="building directional quant/qual match estimates with confidence intervals")
        recent_events = _recent_event_impacts(config)
        if recent_events:
            watchdog.event(
                "recent_event_harness",
                "finish",
                detail=f"{len(recent_events)} recent event impact(s) loaded into match estimates and model prompts",
                extra={
                    "events": [
                        {
                            "id": event.get("id"),
                            "date": event.get("date"),
                            "team": event.get("team"),
                            "category": event.get("category"),
                            "summary": event.get("summary"),
                            "source": _event_source_reference(event),
                            "brazil_shift_pct": event.get("brazil_shift_pct"),
                            "scenario_shift_pct": event.get("scenario_shift_pct"),
                        }
                        for event in recent_events
                    ]
                },
            )
    report_confidence_level = _confidence_level_for_report(config)
    group_estimates = []
    for match in _default_group_matches(config):
        statistical, qualitative = _signals_for_match(match, evidence=evidence, knockout=False, config=config)
        group_estimates.append(
            blend_match_estimate(
                brazil="Brasil",
                opponent=str(match["opponent"]),
                phase="Fase de grupos",
                statistical=statistical,
                qualitative=qualitative,
                rationale=_rationale(match, evidence=evidence, knockout=False, config=config),
                draw_pct=_optional_float(match, "draw_pct"),
                match_date=match.get("date"),
                venue=match.get("venue"),
                confidence_level=report_confidence_level,
            )
        )

    knockout_estimates = []
    for match in _default_knockout_matches(config):
        statistical, qualitative = _signals_for_match(match, evidence=evidence, knockout=True, config=config)
        estimate = blend_match_estimate(
            brazil="Brasil",
            opponent=str(match["opponent"]),
            phase=str(match.get("phase", "Mata-mata")),
            statistical=statistical,
            qualitative=qualitative,
            rationale=_rationale(match, evidence=evidence, knockout=True, config=config),
            match_date=match.get("date"),
            most_likely=bool(match.get("most_likely", True)),
            venue=match.get("venue"),
            scenario_pct=_scenario_pct_for_match(match, config=config),
            confidence_level=report_confidence_level,
        )
        _widen_ci_for_bracket_uncertainty(estimate, match, config=config)
        knockout_estimates.append(estimate)
    _apply_monte_carlo_knockout_scenarios(knockout_estimates, monte_carlo_result)
    mc_config_for_ci = config.get("monte_carlo") if isinstance(config.get("monte_carlo"), dict) else {}
    for estimate in knockout_estimates:
        widen_ci_for_monte_carlo_path_uncertainty(
            estimate,
            monte_carlo_result,
            max_widen_pct=float(mc_config_for_ci.get("max_ci_widen_pct", 8.0)),
            min_iterations=int(mc_config_for_ci.get("path_gate_min_iterations", 10000)),
            min_rating_coverage_pct=float(mc_config_for_ci.get("path_gate_min_rating_coverage_pct", 65.0)),
            max_narrow_pct=float(mc_config_for_ci.get("path_gate_max_ci_narrow_pct", 3.0)),
            narrow_uncertainty_threshold_pct=float(
                mc_config_for_ci.get("path_gate_narrow_uncertainty_threshold_pct", 35.0)
            ),
        )

    if watchdog:
        watchdog.finish("estimate_matches", detail=f"{len(group_estimates) + len(knockout_estimates)} scenarios estimated")

    meeting_config = {
        **config,
        "_meeting_room": "main_brazil",
        "_allowed_fact_source_urls": [],
        "_agent_source_context_by_agent": _agent_source_context_by_agent(active_planning_opinions),
    }
    opponent_debriefing_task = None
    opponent_debriefing_result: dict[str, Any] = {"enabled": False}
    opponent_debriefing_progress: dict[str, Any] = {}
    opponent_cancel_event = threading.Event()
    if _parallel_opponent_debriefing_enabled(config) and active_agent_specs:
        budget_warning = _opponent_debriefing_budget_warning(config)
        if budget_warning:
            warnings.append(budget_warning)
            if watchdog:
                watchdog.event("opponent_model_meeting", "budget_warning", detail=budget_warning)
        opponent_debriefing_task = asyncio.create_task(
            _run_parallel_opponent_debriefing(
                config=meeting_config,
                planning_opinions=active_planning_opinions,
                generated_at=generated_at,
                agent_specs=active_agent_specs,
                baseline_title_pct=baseline_title_pct,
                allow_agent_fallback=allow_agent_fallback,
                token_cost_ledger=token_cost_ledger,
                watchdog=watchdog,
                progress_sink=opponent_debriefing_progress,
                cancel_event=opponent_cancel_event,
            )
        )
        if watchdog:
            watchdog.start(
                "opponent_model_meeting",
                detail=(
                    "side debriefing room started for likely bracket opponents before main Brazil meeting; "
                    "same agents, same source/bracket rules; main room waits for reconciled top-2"
                ),
            )
    if opponent_debriefing_task is not None:
        try:
            opponent_timeout = max(0.001, float(config.get("parallel_opponent_debriefing_timeout_seconds", 900)))
            opponent_debriefing_result = await asyncio.wait_for(
                opponent_debriefing_task,
                timeout=opponent_timeout,
            )
            opponent_opinions = [
                *opponent_debriefing_result.get("final_opinions", []),
                *opponent_debriefing_result.get("all_opinions", []),
            ]
            side_room_usable = bool(opponent_debriefing_result.get("usable_for_main_room", False))
            if side_room_usable:
                _apply_meeting_knockout_scenarios(
                    knockout_estimates,
                    opponent_opinions,
                    config=config,
                    monte_carlo_result=monte_carlo_result,
                )
                _apply_meeting_match_probabilities(knockout_estimates, opponent_opinions)
            else:
                exit_status = str(opponent_debriefing_result.get("exit_status", "unknown") or "unknown")
                exit_warning = str(opponent_debriefing_result.get("exit_warning", "") or "")
                warnings.append(
                    "Sala paralela de adversários terminou sem consenso explícito utilizável "
                    f"({exit_status}); sala principal seguiu com Monte Carlo/top-2 base."
                    + (f" {exit_warning}" if exit_warning else "")
                )
            meeting_config["knockout_matches"] = _knockout_estimates_as_config_matches(knockout_estimates)
            meeting_config["_parallel_opponent_briefing"] = _parallel_opponent_briefing_for_prompt(
                opponent_debriefing_result,
                knockout_estimates,
            )
            if watchdog:
                for turn in opponent_debriefing_result.get("meeting_transcript", []):
                    watchdog.event(
                        "opponent_model_room",
                        "question",
                        detail=str(turn.get("question", "")),
                        extra={
                            "round": int(turn.get("round", 0)),
                            "protagonist": str(turn.get("protagonist", "")),
                        },
                    )
                    for response in turn.get("responses", []):
                        watchdog.event(
                            "opponent_model_room",
                            "response",
                            detail=str(response.get("answer", "")),
                            extra={
                                "round": int(turn.get("round", 0)),
                                "agent": str(response.get("agent", "")),
                                "support_score": response.get("support_score"),
                            },
                        )
                watchdog.finish(
                    "opponent_model_meeting",
                    detail=(
                        f"completed {opponent_debriefing_result.get('rounds', 0)} side opponent round(s); "
                        + (
                            "main room receives reconciled bracket top-2"
                            if side_room_usable
                            else "main room keeps Monte Carlo/top-2 base because side room had no explicit consensus"
                        )
                    ),
                    extra={
                        "participants": opponent_debriefing_result.get("participants", []),
                        "rounds": opponent_debriefing_result.get("rounds", 0),
                        "usable_for_main_room": side_room_usable,
                        "exit_status": opponent_debriefing_result.get("exit_status"),
                        "knockout_matches_for_main_room": meeting_config.get("knockout_matches", []),
                    },
                )
        except asyncio.TimeoutError:
            opponent_timeout = max(0.001, float(config.get("parallel_opponent_debriefing_timeout_seconds", 900)))
            opponent_cancel_event.set()
            opponent_debriefing_result = {
                "enabled": True,
                "failed": True,
                "timed_out": True,
                "timeout_seconds": opponent_timeout,
                "rounds": len(opponent_debriefing_progress.get("meeting_transcript", []) or []),
                "participants": list(opponent_debriefing_progress.get("participants", []) or []),
                "meeting_transcript": list(opponent_debriefing_progress.get("meeting_transcript", []) or []),
                "all_opinions": list(opponent_debriefing_progress.get("all_opinions", []) or []),
                "pending_round": opponent_debriefing_progress.get("pending_round"),
                "partial_progress_available": bool(opponent_debriefing_progress),
                "usable_for_main_room": False,
                "error": f"sala paralela excedeu timeout total de {opponent_timeout}s",
            }
            meeting_config["_parallel_opponent_briefing"] = _parallel_opponent_briefing_for_prompt(
                opponent_debriefing_result,
                knockout_estimates,
            )
            warnings.append(
                "Sala paralela de adversários excedeu o timeout total; sala principal seguiu com Monte Carlo/top-2 já estimados."
            )
            if watchdog:
                watchdog.fail(
                    "opponent_model_meeting",
                    detail=(
                        f"timeout total de {opponent_timeout}s; main room continues with Monte Carlo/top-2 estimates"
                    ),
                    extra={"timeout_seconds": opponent_timeout},
                )
        except Exception as exc:
            opponent_debriefing_result = {"enabled": True, "failed": True, "error": str(exc)}
            meeting_config["_parallel_opponent_briefing"] = _parallel_opponent_briefing_for_prompt(
                opponent_debriefing_result,
                knockout_estimates,
            )
            warnings.append(f"Sala paralela de adversários falhou sem afetar o consenso principal: {exc}.")
            if watchdog:
                watchdog.fail("opponent_model_meeting", detail=str(exc))
    meeting_config["knockout_matches"] = _knockout_estimates_as_config_matches(knockout_estimates)
    meeting_config.setdefault(
        "_parallel_opponent_briefing",
        _parallel_opponent_briefing_for_prompt(opponent_debriefing_result, knockout_estimates),
    )

    def _main_room_fast_path_report_coherence_check(candidate_consensus: Any) -> str:
        try:
            _validate_report_coherence(
                stage_probabilities=_stage_probabilities(
                    float(getattr(candidate_consensus, "title_pct", 0.0) or 0.0),
                    meeting_config,
                ),
                group_estimates=group_estimates,
                knockout_estimates=knockout_estimates,
                monte_carlo_result=monte_carlo_result,
            )
        except ReportCoherenceError as exc:
            return str(exc)
        return ""

    consensus, opinions, meeting_transcript, meeting_opinions = await _run_model_meeting(
        config=meeting_config,
        planning_opinions=active_planning_opinions,
        generated_at=generated_at,
        agent_specs=active_agent_specs,
        baseline_title_pct=baseline_title_pct,
        allow_agent_fallback=allow_agent_fallback,
        watchdog=watchdog,
        token_cost_ledger=token_cost_ledger,
        reentry_candidate_specs=reentry_candidate_specs,
        reentry_removed_reasons=removed_reason_by_agent,
        reentry_removed_issues=removed_issues_by_agent,
        fast_path_report_coherence_check=_main_room_fast_path_report_coherence_check,
    )
    meeting_exit_status = str(getattr(consensus, "exit_status", "consensus") or "consensus")
    meeting_exit_warning = str(getattr(consensus, "exit_warning", "") or "")
    if meeting_exit_status != "consensus" and meeting_exit_warning:
        warnings.append(meeting_exit_warning)
    _apply_meeting_knockout_scenarios(
        knockout_estimates,
        opinions,
        config=config,
        monte_carlo_result=monte_carlo_result,
    )
    _apply_meeting_match_probabilities([*group_estimates, *knockout_estimates], opinions)
    if removed_agent_slots:
        warnings.append(
            "Removidos da jogada por não trazer plano de fontes próprio e verificável: "
            + ", ".join(removed_agent_slots)
            + "."
        )
    for agent, reason in removed_reason_by_agent.items():
        if "Google Gemini billing action required" not in reason:
            continue
        warnings.append(
            f"Ação necessária: {agent} removido porque os créditos pré-pagos do Gemini acabaram; "
            "comprar créditos em https://ai.studio/projects para o modelo voltar à sala. "
            f"Detalhe: {reason}"
        )
    configured_min_participants = int(config.get("meeting_min_participants", config.get("meeting_min_real_agents", 3)))
    if len(active_agent_specs) < configured_min_participants:
        warnings.append(
            "Quórum reduzido na sala: "
            f"{len(active_agent_specs)} participante(s) ativo(s) com fontes próprias contra mínimo configurado de "
            f"{configured_min_participants}; consenso calculado pela maioria simples dos participantes ativos."
        )
    if any(opinion.used_fallback for opinion in opinions):
        warnings.append("Um ou mais agentes usaram fallback local; confira chaves/API/webfetch antes de publicar.")
    used_sources = list(
        dict.fromkeys(
            [
                *_reported_source_labels_from_agent_opinions([*active_planning_opinions, *meeting_opinions, *opinions]),
                *_reported_event_source_labels(config),
            ]
        )
    )
    if not used_sources:
        warnings.append(
            "Nenhum modelo reportou source_urls/source_queries auditáveis; revise APIs/bridges antes de publicar."
        )
    low_authority_sources = _low_authority_public_source_labels(used_sources)
    if low_authority_sources:
        warnings.append(
            "Fontes sociais/vídeo de baixa autoridade foram mantidas apenas como evidência relatada pelo modelo, "
            "não como base estatística: "
            + ", ".join(low_authority_sources[:5])
            + ("." if len(low_authority_sources) <= 5 else f" (+{len(low_authority_sources) - 5} outras).")
        )

    model_influence_pct = calculate_model_influence([*active_planning_opinions, *meeting_opinions], opinions, consensus)
    _emit_low_influence_cost_alerts(
        model_influence_pct=model_influence_pct,
        model_token_costs=token_cost_ledger,
        config=config,
        warnings=warnings,
        watchdog=watchdog,
    )

    stage_probabilities = _stage_probabilities(consensus.title_pct, config)
    market_title_challenge = _market_title_challenge(
        stage_probabilities,
        meeting_transcript,
        config=config,
    )
    market_title_warning = _market_title_challenge_warning(market_title_challenge)
    if market_title_warning:
        warnings.append(market_title_warning)
        if watchdog:
            watchdog.event(
                "market_title_challenge",
                "warn",
                detail=market_title_warning,
                extra=market_title_challenge,
            )
    stage_confidence_intervals = _stage_confidence_intervals(
        stage_probabilities,
        dispersion_pct=consensus.dispersion_pct,
        warning_count=len(warnings),
        config=config,
        model_title_pcts=_title_samples_for_uncertainty(opinions),
    )
    if watchdog:
        watchdog.start("report_coherence", detail="validating funnel monotonicity and scenario/match probability separation")
    try:
        _validate_report_coherence(
            stage_probabilities=stage_probabilities,
            group_estimates=group_estimates,
            knockout_estimates=knockout_estimates,
            monte_carlo_result=monte_carlo_result,
        )
    except ReportCoherenceError as exc:
        if watchdog:
            watchdog.fail("report_coherence", detail=str(exc))
        raise
    if watchdog:
        watchdog.finish("report_coherence", detail="report probability coherence checks passed")
    final_rationale = config.get(
        "final_rationale",
        (
            "Quando cruzamos sportsbooks, prediction markets, ratings e avaliação qualitativa da semana, "
            "o retrato atual é de seleção candidata, mas não de favorita automática: a chance de título "
            "fica muito mais sensível ao cruzamento de quartas/semifinal do que à fase de grupos."
        ),
    )

    bundle = ReportBundle(
        run_id=run_id,
        generated_at_iso=generated_at.isoformat(),
        group_matches=group_estimates,
        knockout_matches=knockout_estimates,
        stage_probabilities=stage_probabilities,
        final_rationale=final_rationale,
        sources=used_sources,
        agent_summaries=consensus.agent_summaries,
        warnings=warnings,
        custom_hashtag=str(config.get("custom_hashtag", DEFAULT_CUSTOM_HASHTAG)),
        group_name=str(config.get("group_name", "GRUPO A")),
        group_summary=_group_summary_from_monte_carlo(config, monte_carlo_result),
        stage_confidence_intervals=stage_confidence_intervals,
        debate_transcript=[],
        meeting_transcript=meeting_transcript,
        source_plan_by_model=_source_plan_by_model(active_planning_opinions),
        model_influence_pct=model_influence_pct,
        model_participation=calculate_model_participation(meeting_transcript, config=config),
        agent_effort_profiles=agent_effort_profiles(active_agent_specs),
        model_token_costs=token_cost_ledger,
        model_predictions_no_opta=_model_predictions_no_opta(opinions),
        model_self_identification=_model_self_identification([*active_planning_opinions, *meeting_opinions, *opinions]),
        metadata={
            "run_id": run_id,
            "agent_title_consensus_pct": consensus.title_pct,
            "agent_opening_consensus_pct": meeting_transcript[0]["consensus_title_pct"] if meeting_transcript else consensus.title_pct,
            "agent_dispersion_pct": consensus.dispersion_pct,
            "uncertainty": _report_uncertainty_metadata(config),
            "stage_interval_metadata": config.get("_stage_interval_metadata", {}),
            "monte_carlo": monte_carlo_compact_summary(monte_carlo_result),
            "group_state": monte_carlo_result.get("group_state", {}),
            "meeting_rounds": len(meeting_transcript),
            "meeting_exit_status": meeting_exit_status,
            "meeting_exit_warning": meeting_exit_warning,
            "meeting_transcript": meeting_transcript,
            "blind_peer_review_shadow": _blind_peer_review_metadata_from_transcript(
                meeting_transcript,
                config=config,
            ),
            "llm_council_fast_path": _llm_council_fast_path_metadata_from_transcript(
                meeting_transcript,
                config=config,
            ),
            "numeric_chairman": _numeric_chairman_metadata(
                config=config,
                stage_probabilities=stage_probabilities,
            ),
            "market_title_challenge": market_title_challenge,
            "parallel_opponent_debriefing": {
                "enabled": bool(opponent_debriefing_result.get("enabled", False)),
                "failed": bool(opponent_debriefing_result.get("failed", False)),
                "timed_out": bool(opponent_debriefing_result.get("timed_out", False)),
                "timeout_seconds": opponent_debriefing_result.get(
                    "timeout_seconds",
                    config.get("parallel_opponent_debriefing_timeout_seconds"),
                ),
                "rounds": int(opponent_debriefing_result.get("rounds", 0) or 0),
                "participants": list(opponent_debriefing_result.get("participants", [])),
                "meeting_transcript": list(opponent_debriefing_result.get("meeting_transcript", [])),
                "pending_round": opponent_debriefing_result.get("pending_round"),
                "partial_progress_available": bool(opponent_debriefing_result.get("partial_progress_available", False)),
                "error": str(opponent_debriefing_result.get("error", "")),
                "exit_status": str(opponent_debriefing_result.get("exit_status", "") or ""),
                "exit_warning": str(opponent_debriefing_result.get("exit_warning", "") or ""),
                "usable_for_main_room": bool(opponent_debriefing_result.get("usable_for_main_room", False)),
                "degraded": bool(opponent_debriefing_result.get("degraded", False)),
                "degraded_shadow_only": bool(opponent_debriefing_result.get("degraded_shadow_only", False)),
                "degraded_would_be_usable": bool(opponent_debriefing_result.get("degraded_would_be_usable", False)),
                "degraded_decision": opponent_debriefing_result.get("degraded_decision", {}),
            },
            "_parallel_opponent_briefing": dict(meeting_config.get("_parallel_opponent_briefing", {})),
            "removed_agent_slots": removed_agent_slots,
            "removed_agent_reasons": removed_reason_by_agent,
            "removed_agent_validation_issues": removed_issues_by_agent,
            "source_planning_warnings": source_planning_warnings,
            "agent_source_planning": {
                opinion.agent: {
                    "source_urls": opinion.source_urls,
                    "source_queries": opinion.source_queries,
                }
                for opinion in planning_opinions
            },
            "mediator_role": {
                "external_fetch": False,
                "source_cache": False,
                "source_selection": False,
                "description": (
                    "Mediador distribui contrato único, registra a sala e renderiza; "
                    "cada modelo faz sua própria busca fresca e reporta source_urls/source_queries."
                ),
            },
            "selected_sources": [],
            "agent_reported_sources": used_sources,
            "model_self_identification": _model_self_identification([*active_planning_opinions, *meeting_opinions, *opinions]),
            "recent_event_impacts": _recent_event_impacts(config),
            "market_value_momentum": _market_value_momentum_report(config),
        },
    )
    if watchdog:
        watchdog.start("render_post", detail="rendering LinkedIn post with debate and confidence intervals")
    artifacts = RunArtifacts(bundle=bundle, post=render_linkedin_post(bundle), raw_evidence=evidence)
    if watchdog:
        watchdog.finish("render_post", detail=f"post has {len(artifacts.post.splitlines())} lines")
    return artifacts


def build_report_bundle_sync(**kwargs: Any) -> RunArtifacts:
    return asyncio.run(build_report_bundle(**kwargs))
