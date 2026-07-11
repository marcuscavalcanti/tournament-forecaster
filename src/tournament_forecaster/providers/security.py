"""Shared sanitization for untrusted local provider metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..errors import TournamentValidationError


REDACTED = "[REDACTED]"
_SENSITIVE_NAMES = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "apisecret",
        "apitoken",
        "appkey",
        "appsecret",
        "auth",
        "authentication",
        "authorization",
        "authtoken",
        "awsaccesskeyid",
        "bearertoken",
        "clientcredential",
        "clientcredentials",
        "clientsecret",
        "credential",
        "credentials",
        "googleaccessid",
        "key",
        "password",
        "passwd",
        "privatekey",
        "proxyauthorization",
        "pwd",
        "refreshtoken",
        "secret",
        "secretkey",
        "securitytoken",
        "sessiontoken",
        "sharedsecret",
        "sig",
        "signature",
        "signingkey",
        "subscriptionkey",
        "token",
        "xamzcredential",
        "xamzsecuritytoken",
        "xamzsignature",
        "xgoogcredential",
        "xgoogsignature",
    }
)


def credential_shaped_name(name: str) -> bool:
    """Return whether a metadata/query key conventionally carries a credential."""

    normalized = re.sub(r"[^a-z0-9]", "", name.casefold())
    return normalized in _SENSITIVE_NAMES


def redact_url(url: str) -> str:
    """Remove userinfo and redact all credential-shaped query values."""

    try:
        parsed = urlsplit(url)
    except ValueError:
        return REDACTED
    netloc = parsed.netloc.rsplit("@", 1)[-1]
    query = urlencode(
        [
            (name, "REDACTED" if credential_shaped_name(name) else value)
            for name, value in parse_qsl(
                parsed.query,
                keep_blank_values=True,
                errors="replace",
            )
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))


def sanitize_metadata(value: object, *, label: str = "metadata") -> object:
    """Freeze metadata after recursively removing credential values and URL userinfo."""

    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TournamentValidationError(f"{label} must use string keys")
            sanitized[key] = (
                REDACTED
                if credential_shaped_name(key)
                else sanitize_metadata(item, label=f"{label}.{key}")
            )
        return MappingProxyType(sanitized)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            sanitize_metadata(item, label=f"{label}[{index}]")
            for index, item in enumerate(value)
        )
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
        except ValueError:
            return value
        if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
            return redact_url(value)
    return value


def serializable_value(value: object) -> object:
    """Convert frozen sanitized metadata into JSON-compatible containers."""

    if isinstance(value, Mapping):
        return {str(key): serializable_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [serializable_value(item) for item in value]
    return value
