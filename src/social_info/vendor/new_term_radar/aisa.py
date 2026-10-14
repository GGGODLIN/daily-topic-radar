"""Client for AIsa, the radar's one paid dependency: its tool router (X posts, Google
Trends and whois data through DataForSEO) and its OpenAI-compatible chat endpoint (the
name judge). One key: AISA_API_KEY, or the one the AIsa CLI stores when you log in.

The tool router runs a batch's calls one by one, about 10 s each, so batches are kept to
BATCH calls and the timeout is long. Every call is billed: a failed batch is never
retried here, so one bad day costs one batch at most.

Prices seen in October 2026: an account's last 20 posts $0.003, one Trends task $0.0012,
one whois overview query about $0.13.
"""
from __future__ import annotations


class AisaError(RuntimeError):
    pass


class PaidCapabilityBlocked(AisaError):
    """Raised before any key lookup or paid request."""


def _blocked(name: str) -> None:
    raise PaidCapabilityBlocked(
        f"{name} is disabled; paid AIsa calls are not available"
    )


def key() -> str:
    """Disabled. The upstream lookup is not run."""
    _blocked("aisa.key")
    return ""


def _post(url: str, body: dict, timeout: int) -> dict:
    _blocked("aisa._post")
    return {}


def call(calls: list[dict]) -> tuple[dict[str, dict], int]:
    """Disabled. An empty batch is not treated as a successful paid call."""
    _blocked("aisa.call")
    return {}, 0


def call_all(calls: list[dict]) -> tuple[dict[str, dict], int]:
    """Disabled. Batches are not sent."""
    _blocked("aisa.call_all")
    return {}, 0


def chat(model: str, system: str, user: str, max_tokens: int = 1500) -> str:
    """Disabled. The chat endpoint is not called."""
    _blocked("aisa.chat")
    return ""
