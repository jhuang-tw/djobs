"""Bounded shared secret redaction for memory, diagnostics, and CLI output.

Pattern matching cannot identify every possible secret. Explicit sensitive
metadata keys and recognized token/header/path shapes are always masked; an
additional conservative entropy rule handles unlabelled opaque token strings.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from re import Pattern
from typing import Any

REDACTION_VERSION = "djobs-privacy-v2"
_REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    categories: tuple[str, ...]
    redaction_count: int


@dataclass(frozen=True, slots=True)
class _Rule:
    category: str
    pattern: Pattern[str]
    replacement: str


_SECRET_SUFFIX = (
    r"(?:api[_-]?key|secret[_-]?access[_-]?key|access[_-]?key|"
    r"access[_-]?token|auth[_-]?token|refresh[_-]?token|token|"
    r"password|passwd|private[_-]?key|client[_-]?secret|secret|authorization|"
    r"accountkey|sharedaccesskey|connectionstring)"
)
_SECRET_KEY = rf"[A-Za-z0-9_.-]*{_SECRET_SUFFIX}"
_RULES: tuple[_Rule, ...] = (
    _Rule(
        "pem_private_key",
        re.compile(
            r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?"
            r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
            re.IGNORECASE | re.DOTALL,
        ),
        _REDACTED,
    ),
    _Rule(
        "authorization_header",
        re.compile(r"(?i)(\bauthorization\s*:\s*)(?:bearer|basic)\s+[^\s,;]+"),
        rf"\1{_REDACTED}",
    ),
    _Rule(
        "bearer_token",
        re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"),
        rf"\1{_REDACTED}",
    ),
    _Rule(
        "url_password",
        re.compile(r"(://[^:/\s]+:)[^@\s]+@"),
        rf"\1{_REDACTED}@",
    ),
    _Rule(
        "github_token",
        re.compile(r"\b(?:github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),
        _REDACTED,
    ),
    _Rule(
        "openai_api_key",
        re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b"),
        _REDACTED,
    ),
    _Rule("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"), _REDACTED),
    _Rule("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"), _REDACTED),
    _Rule("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), _REDACTED),
    _Rule(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
        _REDACTED,
    ),
    _Rule(
        "quoted_assignment",
        re.compile(
            rf"(?i)(?P<prefix>['\"]?)(?P<name>{_SECRET_KEY})(?P=prefix)"
            r"(?P<separator>\s*[:=]\s*)(?P<quote>['\"])(?!<redacted>)(?P<value>.*?)(?P=quote)"
        ),
        rf"\g<prefix>\g<name>\g<prefix>\g<separator>\g<quote>{_REDACTED}\g<quote>",
    ),
    _Rule(
        "assignment",
        re.compile(
            rf"(?i)(?P<prefix>['\"]?)(?P<name>{_SECRET_KEY})(?P=prefix)"
            r"(?P<separator>\s*[:=]\s*)(?!<redacted>)(?P<value>[^'\"\s,;][^\s,;]*)"
        ),
        rf"\g<prefix>\g<name>\g<prefix>\g<separator>{_REDACTED}",
    ),
    _Rule(
        "quoted_flag",
        re.compile(
            rf"(?i)(?P<name>--{_SECRET_KEY})(?P<separator>\s+)"
            r"(?P<quote>['\"])(?!<redacted>)(?P<value>.*?)(?P=quote)"
        ),
        rf"\g<name>\g<separator>\g<quote>{_REDACTED}\g<quote>",
    ),
    _Rule(
        "flag",
        re.compile(
            rf"(?i)(?P<name>--{_SECRET_KEY})(?P<separator>\s+)"
            r"(?!<redacted>)(?P<value>[^'\"\s,;][^\s,;]*)"
        ),
        rf"\g<name>\g<separator>{_REDACTED}",
    ),
    _Rule(
        "cookie_header",
        re.compile(r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)(?!<redacted>)[^\r\n]+"),
        rf"\1{_REDACTED}",
    ),
    _Rule(
        "windows_credential_path",
        re.compile(
            r"(?i)[A-Z]:[\\/](?:[^\s'\"<>]+[\\/])*"
            r"(?:Microsoft[\\/]Credentials|Microsoft[\\/]Vault|\.aws|\.ssh)"
            r"(?:[\\/][^\s'\"<>]*)?"
        ),
        _REDACTED,
    ),
)
_OPAQUE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_+/=-]{32,512}(?![A-Za-z0-9_])")


def _opaque_token(value: str) -> bool:
    if re.fullmatch(r"[0-9a-fA-F-]+", value):
        return False
    if (
        min(
            sum(c.isupper() for c in value),
            sum(c.islower() for c in value),
            sum(c.isdigit() for c in value),
        )
        < 5
    ):
        return False
    counts = Counter(value)
    entropy = -sum((n / len(value)) * math.log2(n / len(value)) for n in counts.values())
    return entropy >= 4.0


def redact(value: Any) -> RedactionResult:
    text = str(value or "")
    categories: list[str] = []
    count = 0
    for rule in _RULES:
        text, replacements = rule.pattern.subn(rule.replacement, text)
        if replacements:
            count += replacements
            if rule.category not in categories:
                categories.append(rule.category)

    def opaque_replacement(match: re.Match[str]) -> str:
        nonlocal count
        if not _opaque_token(match.group()):
            return match.group()
        count += 1
        if "opaque_token" not in categories:
            categories.append("opaque_token")
        return _REDACTED

    text = _OPAQUE.sub(opaque_replacement, text)
    return RedactionResult(text=text, categories=tuple(categories), redaction_count=count)


def redact_text(value: Any) -> str:
    return redact(value).text


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Redact structured metadata without corrupting JSON or losing authority keys."""

    if _depth > 12:
        return "<omitted-nested-data>"
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = str(key)
            sensitive = re.fullmatch(_SECRET_KEY, name, re.IGNORECASE) is not None
            sensitive = sensitive or name.casefold() in {"cookie", "cookies", "set-cookie"}
            result[redact_text(name)] = (
                _REDACTED if sensitive else redact_value(item, _depth=_depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [redact_value(item, _depth=_depth + 1) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value)
