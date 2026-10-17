"""Mask credential-shaped tokens before anything is stored or rendered.

Why this exists: the aggregator stores social posts verbatim, and on 2026-10-17 a
LINUX DO post carried the author's own VolcEngine Ark key. GitHub push protection
(the repo is public) blocked the 06:00 commit, so the whole day's data stayed
local and nothing told anyone until the daily-local closeout noticed.

Patterns are deliberately narrow (provider prefix + length) rather than
entropy-based: a false positive costs one token inside a digest quote, a false
negative costs a blocked push. detect-secrets / gitleaks would do this better but
add a dependency and a baseline file to a launchd job that only needs a regex.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from social_info.fetchers.base import FetchResult, Item

_KINDS: tuple[tuple[str, str], ...] = (
    ("ark", r"ark-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?:-[0-9a-z]{3,})?"),
    # OpenAI / Anthropic / DeepSeek / Moonshot all use sk-… with optional vendor infix (sk-ant-, sk-proj-).
    ("sk", r"sk-[A-Za-z0-9_-]{20,}"),
    ("aws", r"AKIA[0-9A-Z]{16}"),
    ("github", r"gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,}"),
    ("google", r"AIza[0-9A-Za-z_-]{35}"),
    ("slack", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("stripe", r"[sr]k_live_[0-9a-zA-Z]{24,}"),
    ("groq", r"gsk_[A-Za-z0-9]{20,}"),
    ("hf", r"hf_[A-Za-z0-9]{30,}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)

# The look-around keeps prefixes from firing inside longer identifiers (e.g. "task-…" must not match "sk-…").
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:" + "|".join(f"(?P<{kind}>{pattern})" for kind, pattern in _KINDS) + r")(?![A-Za-z0-9_-])"
)


def _replacement(match: re.Match[str]) -> str:
    kind = match.lastgroup or "token"
    return f"[REDACTED-KEY:{kind}]"


def redact_text(text: str) -> str:
    if not text:
        return text
    return _TOKEN_RE.sub(_replacement, text)


def _redact_mapping_values(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: redact_text(value) if isinstance(value, str) else value for key, value in mapping.items()}


def redact_item(item: Item) -> Item:
    return replace(
        item,
        title=redact_text(item.title),
        excerpt=redact_text(item.excerpt),
        content=redact_text(item.content),
        comments=[_redact_mapping_values(comment) for comment in item.comments],
        metadata=_redact_mapping_values(item.metadata),
    )


def redact_result(result: FetchResult) -> FetchResult:
    if not result.items:
        return result
    return replace(result, items=[redact_item(item) for item in result.items])


def redact_row(row: dict[str, Any]) -> dict[str, Any]:
    return _redact_mapping_values(row)
