"""Auto-generate KNOWN_ISSUES.md from db.current_known_issues() output.

Two tiers:
- 🚨 user action required — likely VPN / API key / actor schema, retry won't help
- 🪦 stable failures (≥7 consecutive failed runs since last ok)
- ⏳ transient (still failing after auto-retry exhausted) — retry next run
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TAIPEI = timezone(timedelta(hours=8))

ACTION_HINTS: dict[str, str] = {
    "reddit_localllama": "VPN 開著時 Reddit 必擋。關 VPN 或 split-tunnel exclude reddit.com。",
    "reddit_claudeai": "VPN 開著時 Reddit 必擋。關 VPN 或 split-tunnel exclude reddit.com。",
    "reddit_openai": "VPN 開著時 Reddit 必擋。關 VPN 或 split-tunnel exclude reddit.com。",
    "reddit_singularity": "VPN 開著時 Reddit 必擋（或 reddit 偶發 5xx）。關 VPN 試一次。",
    "reddit_machinelearning": "VPN 開著時 Reddit 必擋。關 VPN 或 split-tunnel exclude reddit.com。",
    "threads_keyword": "Apify actor D15iJFBNZ9wgeWAhw schema 不合，已知持續壞。換 actor 或 sources.yml 設 enabled:false。",
    "twitter_tier1": "X 偶發 ReadError。先試 retry，仍失敗檢查 RSS hub / scrape 設定。",
}

DEFAULT_HINTS: dict[str, str] = {
    "user_action_required": "401/403 — 多半是 VPN / API key / 認證問題，需要你介入。",
    "persistent_error": "4xx 持續錯誤 — 多半是 source schema / API 變更，需要你介入更新 fetcher。",
    "transient": "暫時性錯誤 — 自動 retry 用完仍失敗，下次 run 會再試。",
}

HEADER_TMPL = (
    "# Known Issues — auto-updated by social_info pipeline\n\n"
    "> Last updated: {ts} (Asia/Taipei)\n"
    "> 來源 source 上次 fetch 失敗的最終狀態。pipeline 已自動 retry transient errors，"
    "出現在這裡的代表 retry 配額耗盡或屬於需要人介入的類別。\n\n"
)


def _format_iso(ts: str | None) -> str:
    if not ts:
        return "（無紀錄）"
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M CST")
    except (ValueError, TypeError):
        return ts


def _hint_for(source: str, error_class: str) -> str:
    return ACTION_HINTS.get(source) or DEFAULT_HINTS.get(error_class, "")


def _issue_line(issue: dict[str, Any]) -> str:
    source = issue["source"]
    err = issue["last_error"] or "(no error message)"
    last_ok = _format_iso(issue.get("last_ok_at"))
    consecutive = issue.get("consecutive_fail_runs", 1)
    attempts = issue.get("last_attempts", 1)
    klass = issue.get("last_error_class") or "transient"
    hint = _hint_for(source, klass)
    return (
        f"- **{source}** ({klass}) — {err}\n"
        f"  - last ok: {last_ok} · consecutive fails: {consecutive} · last attempts: {attempts}\n"
        f"  - → {hint}\n"
    )


def render(issues: list[dict[str, Any]], generated_at: datetime) -> str:
    user_action: list[dict[str, Any]] = []
    persistent: list[dict[str, Any]] = []
    transient: list[dict[str, Any]] = []
    stable: list[dict[str, Any]] = []

    STABLE_THRESHOLD = 7
    for issue in issues:
        if issue.get("consecutive_fail_runs", 0) >= STABLE_THRESHOLD:
            stable.append(issue)
            continue
        klass = issue.get("last_error_class") or "transient"
        if klass == "user_action_required":
            user_action.append(issue)
        elif klass == "persistent_error":
            persistent.append(issue)
        else:
            transient.append(issue)

    parts: list[str] = [HEADER_TMPL.format(ts=generated_at.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M"))]

    if not issues:
        parts.append("✅ 所有 source 上次 fetch 都成功，沒有需要處理的 issue。\n")
        return "".join(parts)

    if user_action:
        parts.append(f"## 🚨 User action required ({len(user_action)})\n\n")
        parts.extend(_issue_line(i) for i in user_action)
        parts.append("\n")

    if persistent:
        parts.append(f"## 🛠 Persistent error — fetcher 需要更新 ({len(persistent)})\n\n")
        parts.extend(_issue_line(i) for i in persistent)
        parts.append("\n")

    if stable:
        parts.append(f"## 🪦 Stable failures (≥7 連續失敗) ({len(stable)})\n\n")
        parts.extend(_issue_line(i) for i in stable)
        parts.append("\n")

    if transient:
        parts.append(f"## ⏳ Transient — retry 用完仍失敗、下次 run 會再試 ({len(transient)})\n\n")
        parts.extend(_issue_line(i) for i in transient)
        parts.append("\n")

    return "".join(parts)


def write(issues: list[dict[str, Any]], out_dir: Path, generated_at: datetime) -> Path:
    out_path = out_dir / "KNOWN_ISSUES.md"
    out_path.write_text(render(issues, generated_at), encoding="utf-8")
    return out_path
