"""Bounded secret redaction shared by memory, diagnostics, and CLI output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern
from typing import Any

_REDACTED = "<redacted>"


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """Redacted text plus non-sensitive category metadata."""

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
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?"
            r"-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
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
    _Rule(
        "anthropic_api_key",
        re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        _REDACTED,
    ),
    _Rule(
        "google_api_key",
        re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
        _REDACTED,
    ),
    _Rule(
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        _REDACTED,
    ),
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
)


def redact(value: Any) -> RedactionResult:
    """Redact common credentials without returning any original secret metadata."""

    text = str(value or "")
    categories: list[str] = []
    count = 0
    for rule in _RULES:
        text, replacements = rule.pattern.subn(rule.replacement, text)
        if replacements:
            count += replacements
            if rule.category not in categories:
                categories.append(rule.category)
    return RedactionResult(text=text, categories=tuple(categories), redaction_count=count)


def redact_text(value: Any) -> str:
    """Compatibility helper returning only redacted text."""

    return redact(value).text
