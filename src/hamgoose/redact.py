"""Secret redaction for persisted worker output and events.

Prevents obvious credentials from being written to mission logs.
"""
from __future__ import annotations

import re

_PATTERNS = [
    # bearer / auth headers
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]+"), "Bearer <REDACTED>"),
    # api keys
    (re.compile(r"(?i)(api[_-]?key|apikey|secret|token|passwd|password|pwd)\s*[:=]\s*['\"]?([A-Za-z0-9._\-]{8,})['\"]?"), r"\1=<REDACTED>"),
    # sk-... openai style
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "<REDACTED_KEY>"),
    # sk-ant, sk-...
    (re.compile(r"sk-ant-[A-Za-z0-9\-]{16,}"), "<REDACTED_KEY>"),
    # aws access keys
    (re.compile(r"AKIA[0-9A-Z]{16}"), "<REDACTED_AWS>"),
    # generic long hex/token that looks like a credential value
    (re.compile(r"(?i)(x-api-key|authorization)\s*[:=]\s*['\"]?[A-Za-z0-9._\-]{8,}['\"]?"), r"\1=<REDACTED>"),
    # basic auth
    (re.compile(r"://[^@/\s:]+:[^@/\s]+@"), "://<REDACTED>@"),
]


def redact(text: str) -> str:
    if not text:
        return text
    out = text
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    return out
