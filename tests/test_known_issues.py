"""Test known_issues.render markdown output structure."""
from datetime import datetime, timedelta, timezone

from social_info.known_issues import render

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 5, 8, 9, 0, tzinfo=TAIPEI)


def _issue(source: str, klass: str, fails: int = 1, last_ok: str | None = None) -> dict:
    return {
        "source": source,
        "last_error": f"mock error for {source}",
        "last_error_class": klass,
        "last_attempts": 4,
        "last_failed_at": "2026-05-08T01:00:00+00:00",
        "last_ok_at": last_ok,
        "consecutive_fail_runs": fails,
    }


def test_empty() -> None:
    out = render([], NOW)
    assert "✅ 所有 source 上次 fetch 都成功" in out


def test_user_action_section() -> None:
    out = render([_issue("reddit_localllama", "user_action_required")], NOW)
    assert "🚨 User action required" in out
    assert "reddit_localllama" in out
    assert "VPN" in out


def test_persistent_section() -> None:
    out = render([_issue("threads_keyword", "persistent_error")], NOW)
    assert "🛠 Persistent error" in out
    assert "threads_keyword" in out


def test_stable_promotion_at_7_fails() -> None:
    out = render([_issue("threads_keyword", "persistent_error", fails=7)], NOW)
    assert "🪦 Stable failures" in out
    # not in persistent section
    assert "🛠 Persistent error — fetcher 需要更新 (1)" not in out


def test_transient_section() -> None:
    out = render([_issue("twitter_tier1", "transient")], NOW)
    assert "⏳ Transient" in out


def test_multi_classes() -> None:
    issues = [
        _issue("reddit_localllama", "user_action_required"),
        _issue("threads_keyword", "persistent_error", fails=7),
        _issue("twitter_tier1", "transient"),
    ]
    out = render(issues, NOW)
    assert "🚨 User action required (1)" in out
    assert "🪦 Stable failures" in out
    assert "⏳ Transient" in out
