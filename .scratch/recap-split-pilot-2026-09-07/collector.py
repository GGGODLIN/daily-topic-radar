from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

OUTPUT_DIR = Path(__file__).resolve().parent
BATCH_DIR = OUTPUT_DIR / "batches"
HOME = Path.home()
CLAUDE_DIR = HOME / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
MEMORY_DIR = CLAUDE_DIR / "memory"
CONFIG_REPO = CLAUDE_DIR
REPO_ROOTS = [HOME / "Desktop" / "work", HOME / "Desktop" / "projects", HOME / "code"]
TIMEZONE = ZoneInfo("Asia/Taipei")
WINDOW_START = datetime(2026, 9, 6, 11, 10, tzinfo=TIMEZONE)
WINDOW_END = datetime(2026, 9, 7, 11, 10, tzinfo=TIMEZONE)
WINDOW_START_UTC = WINDOW_START.astimezone(timezone.utc)
WINDOW_END_UTC = WINDOW_END.astimezone(timezone.utc)
USER_LIMIT = 500
ASSISTANT_LIMIT = 500
BATCH_COUNT = 4

SYSTEM_REMINDER_RE = re.compile(
    r"<system-reminder\b[^>]*>.*?(?:</system-reminder\s*>|$)", re.IGNORECASE | re.DOTALL
)
NOISE_BLOCK_RE = re.compile(
    r"<(?:local-command-caveat|local-command-stdout|local-command-stderr|"
    r"command-message|command-name|command-args|task-notification)\b[^>]*>.*?</"
    r"(?:local-command-caveat|local-command-stdout|local-command-stderr|"
    r"command-message|command-name|command-args|task-notification)\s*>",
    re.IGNORECASE | re.DOTALL,
)
TAG_RE = re.compile(r"</?(?:local-command-caveat|local-command-stdout|local-command-stderr|command-message|command-name|command-args|task-notification)\b[^>]*>", re.IGNORECASE)
COMMAND_NAME_RE = re.compile(r"<command-name\b[^>]*>\s*(/[^<\s]+)", re.IGNORECASE)
BASE_DIRECTORY_RE = re.compile(r"(?ms)^Base directory for this skill:.*$")
NOISE_PREFIXES = (
    "Caveat:",
    "Shell cwd",
    "Stop hook feedback",
    "AUTO-SAVE",
    "Base directory for this skill:",
    "Available agent types for the Agent tool:",
    "MCP Server Instructions:",
)
MEANINGFUL_COMMANDS = {"/wait-what", "/wait-what-plus"}
PEM_RE = re.compile(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", re.DOTALL)
BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
SECRET_KEY_NAME = (
    r"(?:[A-Za-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|auth[_-]?token|"
    r"refresh[_-]?token|session[_-]?token|client[_-]?secret|private[_-]?key|"
    r"secret[_-]?access[_-]?key|access[_-]?key[_-]?id|password|passwd|secret|token)"
    r"(?:[_-][A-Za-z0-9]+)*"
)
KV_RE = re.compile(
    rf"(?i)((?:\"{SECRET_KEY_NAME}\"|'{SECRET_KEY_NAME}'|{SECRET_KEY_NAME})\s*[:=]\s*)"
    r"(?:\"[^\"\n]{4,}\"|'[^'\n]{4,}'|[^\s,;\]}\)]+)"
)
TOKEN_RES = [
    re.compile(r"(?i)\bsk-ant-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
]


@dataclass(frozen=True)
class WindowRow:
    source_file: str
    line: int
    session_id: str
    row_type: str
    timestamp: str
    timestamp_dt: datetime
    cwd: str | None
    content: Any


@dataclass
class FileInventory:
    path: Path
    size_before: int
    mtime_before: float
    size_after: int | None = None
    mtime_after: float | None = None
    read_error: str | None = None
    parse_errors: int = 0
    rows_in_window: int = 0
    session_ids: set[str] | None = None

    def __post_init__(self) -> None:
        if self.session_ids is None:
            self.session_ids = set()


@dataclass
class SessionAccumulator:
    session_id: str
    source_files: set[str]
    cwd_values: set[str]
    user_messages: list[dict[str, Any]]
    first_activity: tuple[datetime, str, int, str] | None
    last_activity: tuple[datetime, str, int, str] | None
    raw_user_rows: int
    dropped_user_noise: int
    assistant_rows: int
    assistant_text_rows: int
    assistant_claim: dict[str, Any] | None
    user_secret_masks: int
    assistant_secret_masks: int
    truncated_message_count: int
    truncated_chars: int


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def in_window(value: datetime) -> bool:
    return WINDOW_START_UTC <= value < WINDOW_END_UTC


def format_mtime(value: float) -> str:
    return iso(datetime.fromtimestamp(value, timezone.utc))


def mask_secrets(text: str) -> tuple[str, int]:
    count = 0

    def replace_pem(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "<REDACTED>"

    text = PEM_RE.sub(replace_pem, text)

    def replace_kv(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}<REDACTED>"

    text = KV_RE.sub(replace_kv, text)

    def replace_bearer(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "Bearer <REDACTED>"

    text = BEARER_RE.sub(replace_bearer, text)

    for pattern in TOKEN_RES:
        def replace_token(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return "<REDACTED>"

        text = pattern.sub(replace_token, text)
    return text, count


def flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        value = block.get("text")
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def clean_message_text(content: Any) -> tuple[str | None, str]:
    raw = flatten_content(content)
    if not raw:
        return None, "empty"
    command_names = [name for name in COMMAND_NAME_RE.findall(raw) if name in MEANINGFUL_COMMANDS]
    text = SYSTEM_REMINDER_RE.sub("", raw)
    text = NOISE_BLOCK_RE.sub("", text)
    text = TAG_RE.sub("", text)
    text = BASE_DIRECTORY_RE.sub("", text)
    text = text.strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if text and not text.startswith(NOISE_PREFIXES):
        return text, "text"
    if command_names:
        return "\n".join(dict.fromkeys(command_names)), "command_signal"
    return None, "noise"


def truncate_and_mask(text: str, limit: int) -> tuple[str, int, int, int]:
    masked, secret_count = mask_secrets(text)
    truncated_chars = max(0, len(text) - limit)
    return masked[:limit], truncated_chars, secret_count, len(text)


def row_from_json(path: Path, line_no: int, row: Any) -> WindowRow | None:
    if not isinstance(row, dict) or row.get("type") not in {"user", "assistant"}:
        return None
    timestamp = row.get("timestamp")
    timestamp_dt = parse_timestamp(timestamp)
    if timestamp_dt is None or not in_window(timestamp_dt):
        return None
    session_id = row.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        session_id = path.stem
    cwd = row.get("cwd") if isinstance(row.get("cwd"), str) else None
    message = row.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return WindowRow(
        source_file=str(path),
        line=line_no,
        session_id=session_id,
        row_type=row["type"],
        timestamp=timestamp,
        timestamp_dt=timestamp_dt,
        cwd=cwd,
        content=content,
    )


def new_accumulator(session_id: str) -> SessionAccumulator:
    return SessionAccumulator(
        session_id=session_id,
        source_files=set(),
        cwd_values=set(),
        user_messages=[],
        first_activity=None,
        last_activity=None,
        raw_user_rows=0,
        dropped_user_noise=0,
        assistant_rows=0,
        assistant_text_rows=0,
        assistant_claim=None,
        user_secret_masks=0,
        assistant_secret_masks=0,
        truncated_message_count=0,
        truncated_chars=0,
    )


def add_window_row(acc: SessionAccumulator, row: WindowRow) -> None:
    acc.source_files.add(row.source_file)
    if row.cwd:
        acc.cwd_values.add(row.cwd)
    activity_key = (row.timestamp_dt, row.source_file, row.line)
    activity_value = (row.timestamp_dt, row.source_file, row.line, row.timestamp)
    if acc.first_activity is None or activity_key < acc.first_activity[:3]:
        acc.first_activity = activity_value
    if acc.last_activity is None or activity_key > acc.last_activity[:3]:
        acc.last_activity = activity_value
    text, kind = clean_message_text(row.content)
    if row.row_type == "user":
        acc.raw_user_rows += 1
        if text is None:
            acc.dropped_user_noise += 1
            return
        stored, truncated_chars, secret_count, source_chars = truncate_and_mask(text, USER_LIMIT)
        message = {
            "line": row.line,
            "source_file": row.source_file,
            "timestamp": row.timestamp,
            "role": "user",
            "kind": kind,
            "text": stored,
            "source_chars": source_chars,
            "stored_chars": len(stored),
            "truncated_chars": truncated_chars,
            "secret_masks": secret_count,
        }
        acc.user_messages.append(message)
        acc.user_secret_masks += secret_count
        acc.truncated_chars += truncated_chars
        if truncated_chars:
            acc.truncated_message_count += 1
        return
    acc.assistant_rows += 1
    if text is None:
        return
    acc.assistant_text_rows += 1
    stored, truncated_chars, secret_count, source_chars = truncate_and_mask(text, ASSISTANT_LIMIT)
    claim = {
        "line": row.line,
        "source_file": row.source_file,
        "timestamp": row.timestamp,
        "role": "assistant",
        "kind": "claim",
        "summary_method": "last-visible-text-excerpt",
        "text": stored,
        "source_chars": source_chars,
        "stored_chars": len(stored),
        "truncated_chars": truncated_chars,
        "secret_masks": secret_count,
    }
    current_key = (row.timestamp_dt, row.source_file, row.line)
    previous_key = None
    if acc.assistant_claim:
        previous_dt = parse_timestamp(acc.assistant_claim["timestamp"])
        previous_key = (previous_dt, acc.assistant_claim["source_file"], acc.assistant_claim["line"])
    if previous_key is None or current_key > previous_key:
        acc.assistant_claim = claim
    acc.assistant_secret_masks += secret_count
    acc.truncated_chars += truncated_chars
    if truncated_chars:
        acc.truncated_message_count += 1


def finalize_session(acc: SessionAccumulator, repo_resolver: RepoResolver) -> dict[str, Any]:
    user_messages = sorted(
        acc.user_messages,
        key=lambda item: (parse_timestamp(item["timestamp"]) or WINDOW_START_UTC, item["source_file"], item["line"]),
    )
    cwd_values = sorted(acc.cwd_values)
    repo_values = sorted({repo for cwd in cwd_values if (repo := repo_resolver(cwd))})
    result: dict[str, Any] = {
        "session_id": acc.session_id,
        "source_files": sorted(acc.source_files),
        "cwd": cwd_values[0] if cwd_values else None,
        "cwd_variants": cwd_values,
        "repo": repo_values[0] if repo_values else None,
        "repo_variants": repo_values,
        "window": {
            "first_activity_timestamp": acc.first_activity[3] if acc.first_activity else None,
            "last_activity_timestamp": acc.last_activity[3] if acc.last_activity else None,
        },
        "user_messages": user_messages,
        "assistant_last_claim": acc.assistant_claim,
        "extraction": {
            "raw_user_rows_in_window": acc.raw_user_rows,
            "valid_user_messages": len(user_messages),
            "dropped_user_noise": acc.dropped_user_noise,
            "assistant_rows_in_window": acc.assistant_rows,
            "assistant_text_rows_in_window": acc.assistant_text_rows,
            "truncated_message_count": acc.truncated_message_count,
            "truncated_chars": acc.truncated_chars,
            "secret_masks": acc.user_secret_masks + acc.assistant_secret_masks,
        },
    }
    return result


class RepoResolver:
    def __init__(self) -> None:
        self.cache: dict[str, str | None] = {}

    def __call__(self, cwd: str) -> str | None:
        if cwd in self.cache:
            return self.cache[cwd]
        value: str | None = None
        try:
            result = subprocess.run(
                ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
            if result.returncode == 0 and result.stdout.strip():
                value = str(Path(result.stdout.strip()).resolve())
        except (OSError, subprocess.SubprocessError):
            value = None
        self.cache[cwd] = value
        return value


def build_sessions(rows: Iterable[WindowRow], repo_resolver: RepoResolver | None = None) -> list[dict[str, Any]]:
    resolver = repo_resolver or RepoResolver()
    accumulators: dict[str, SessionAccumulator] = {}
    for row in rows:
        if not in_window(row.timestamp_dt):
            continue
        accumulator = accumulators.setdefault(row.session_id, new_accumulator(row.session_id))
        add_window_row(accumulator, row)
    return [
        finalize_session(accumulators[session_id], resolver)
        for session_id in sorted(accumulators)
    ]


def enumerate_session_candidates() -> list[Path]:
    candidates: list[Path] = []
    if not PROJECTS_DIR.exists():
        return candidates
    for directory, dirnames, filenames in os.walk(PROJECTS_DIR, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name != "subagents")
        for filename in sorted(filenames):
            if not filename.endswith(".jsonl"):
                continue
            path = Path(directory) / filename
            try:
                if path.stat().st_mtime >= WINDOW_START_UTC.timestamp():
                    candidates.append(path)
            except OSError:
                continue
    return sorted(candidates)


def parse_session_file(path: Path) -> tuple[list[WindowRow], FileInventory]:
    before = path.stat()
    inventory = FileInventory(path, before.st_size, before.st_mtime)
    rows: list[WindowRow] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_no, line in enumerate(handle, 1):
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    inventory.parse_errors += 1
                    continue
                parsed = row_from_json(path, line_no, raw)
                if parsed is None:
                    continue
                rows.append(parsed)
                inventory.rows_in_window += 1
                inventory.session_ids.add(parsed.session_id)
    except OSError as error:
        inventory.read_error = f"{type(error).__name__}: {error}"
    try:
        after = path.stat()
        inventory.size_after = after.st_size
        inventory.mtime_after = after.st_mtime
    except OSError:
        pass
    return rows, inventory


def collect_sessions() -> tuple[list[dict[str, Any]], list[FileInventory]]:
    all_rows: list[WindowRow] = []
    inventories: list[FileInventory] = []
    for path in enumerate_session_candidates():
        rows, inventory = parse_session_file(path)
        all_rows.extend(rows)
        inventories.append(inventory)
    return build_sessions(all_rows), inventories


def discover_repositories() -> list[Path]:
    discovered: set[str] = set()
    for root in REPO_ROOTS:
        if not root.exists():
            continue
        for directory, dirnames, filenames in os.walk(root, followlinks=False):
            current = Path(directory)
            try:
                depth = len(current.relative_to(root).parts)
            except ValueError:
                continue
            if depth >= 2:
                dirnames[:] = []
            else:
                dirnames[:] = sorted(dirnames)
            if ".git" not in dirnames:
                continue
            git_dir = current / ".git"
            try:
                if git_dir.is_dir() and not git_dir.is_symlink():
                    discovered.add(str(current.resolve()))
            except OSError:
                continue
    return [Path(path) for path in sorted(discovered)]


def git_rows(repo: Path, extra: str | None = None) -> list[tuple[str, str, str, str, str, int]]:
    command = [
        "git",
        "-C",
        str(repo),
        "log",
        "--branches",
        f"--since={WINDOW_START.isoformat()}",
        f"--until={WINDOW_END.isoformat()}",
        "--no-merges",
        "--pretty=format:%H%x1f%cI%x1f%s%x1f%an",
    ]
    if extra:
        command.insert(-1, extra)
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    rows: list[tuple[str, str, str, str, str, int]] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split("\x1f", 3)
        if len(fields) != 4:
            continue
        sha, timestamp, subject, author = fields
        timestamp_dt = parse_timestamp(timestamp)
        if timestamp_dt is None or not in_window(timestamp_dt) or sha in seen:
            continue
        seen.add(sha)
        safe_subject, secret_masks = mask_secrets(subject)
        rows.append((sha, timestamp, safe_subject, author, str(repo), secret_masks))
    return rows


def lookup_commit_body(repo: Path, sha: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--pretty=format:%b", sha],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def make_revert_signal(
    repo: Path,
    sha: str,
    timestamp: str,
    subject: str,
    author: str,
    body: str,
) -> dict[str, Any] | None:
    timestamp_dt = parse_timestamp(timestamp)
    if timestamp_dt is None or not in_window(timestamp_dt):
        return None
    original_match = re.search(r"This reverts commit ([0-9a-f]{7,40})", body, re.IGNORECASE)
    original_sha = original_match.group(1) if original_match else None
    original = lookup_commit(repo, original_sha) if original_sha else None
    safe_subject, _ = mask_secrets(subject)
    return {
        "repo": str(repo),
        "reverting_sha": sha,
        "timestamp": timestamp,
        "subject": safe_subject,
        "author": author,
        "original_sha": original_sha,
        "original": original,
        "within_14_days": original is not None
        and original.get("timestamp_dt") is not None
        and (timestamp_dt - original["timestamp_dt"]).total_seconds() <= 14 * 86400
        and timestamp_dt >= original["timestamp_dt"],
    }


def git_revert_rows(repo: Path, subject_commits: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    command = [
        "git",
        "-C",
        str(repo),
        "log",
        "--branches",
        f"--since={WINDOW_START.isoformat()}",
        f"--until={WINDOW_END.isoformat()}",
        "--no-merges",
        "--grep=This reverts commit",
        "--pretty=format:%H%x1f%cI%x1f%s%x1f%an%x1f%b%x1e",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        result = None
    signals_by_sha: dict[str, dict[str, Any]] = {}
    if result is not None and result.returncode == 0:
        for record in result.stdout.split("\x1e"):
            fields = record.split("\x1f", 4)
            if len(fields) != 5:
                continue
            sha, timestamp, subject, author, body = fields
            signal = make_revert_signal(repo, sha, timestamp, subject, author, body)
            if signal is not None:
                signals_by_sha[sha] = signal
    for commit in subject_commits:
        subject = commit["subject"]
        if not subject.lower().startswith("revert"):
            continue
        sha = commit["sha"]
        if sha in signals_by_sha:
            continue
        signal = make_revert_signal(
            repo,
            sha,
            commit["timestamp"],
            subject,
            commit["author"],
            lookup_commit_body(repo, sha),
        )
        if signal is not None:
            signals_by_sha[sha] = signal
    return [signals_by_sha[sha] for sha in sorted(signals_by_sha)]


def lookup_commit(repo: Path, sha: str | None) -> dict[str, Any] | None:
    if not sha:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", f"--pretty=format:%cI%x1f%s", sha],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    fields = result.stdout.split("\x1f", 1)
    if len(fields) != 2:
        return None
    timestamp, subject = fields
    timestamp_dt = parse_timestamp(timestamp)
    safe_subject, _ = mask_secrets(subject)
    return {"sha": sha, "timestamp": timestamp, "timestamp_dt": timestamp_dt, "subject": safe_subject}


def collect_context() -> tuple[dict[str, Any], dict[str, Any]]:
    repositories = discover_repositories()
    repo_entries: list[dict[str, Any]] = []
    all_repo_commits: list[dict[str, Any]] = []
    revert_signals: list[dict[str, Any]] = []
    for repo in repositories:
        commits = [
            {
                "sha": sha,
                "timestamp": timestamp,
                "subject": subject,
                "author": author,
                "repo": source_repo,
                "secret_masks": secret_masks,
            }
            for sha, timestamp, subject, author, source_repo, secret_masks in git_rows(repo)
        ]
        repo_entries.append({"repo": str(repo), "commits": commits})
        all_repo_commits.extend(commits)
        for signal in git_revert_rows(repo, commits):
            if signal.get("original"):
                signal["original"].pop("timestamp_dt", None)
            revert_signals.append(signal)
    config_commits = [
        {
            "sha": sha,
            "timestamp": timestamp,
            "subject": subject,
            "author": author,
            "repo": source_repo,
            "secret_masks": secret_masks,
        }
        for sha, timestamp, subject, author, source_repo, secret_masks in git_rows(CONFIG_REPO)
    ]
    memory_entries = collect_memory_entries()
    context = {
        "schema_version": "recap-split-pilot/v1",
        "source": "recap-daily.sh four-line rubric",
        "window": window_object(),
        "repo_commits": {
            "discovery_roots": [str(root) for root in REPO_ROOTS],
            "discovery_rule": "find roots at maxdepth 2 for .git directories, resolve repo paths with pwd -P semantics, deduplicate by realpath",
            "git_rule": "git log --branches --since/--until fixed window --no-merges; fields are sha, committer timestamp, subject, author",
            "repositories": repo_entries,
            "revert_signals": revert_signals,
        },
        "claude_config_commits": {
            "repo": str(CONFIG_REPO),
            "git_rule": "git log --branches --since/--until fixed window --no-merges",
            "commits": config_commits,
        },
        "memory_entries": {
            "root": str(MEMORY_DIR),
            "mtime_rule": "mtime in [window.start, window.end); read only first 10 lines and frontmatter description",
            "entries": memory_entries,
        },
        "excluded_inputs": [
            "today's recap or digest products",
            "repo diffs and commit bodies except targeted deterministic revert signal lookup",
            "session tool_result blocks and assistant thinking/tool_use blocks",
            "formal rule-adherence and codebase-alias ledgers",
        ],
    }
    summary = {
        "repo_count": len(repositories),
        "repo_commit_count": len(all_repo_commits),
        "claude_config_commit_count": len(config_commits),
        "commit_count": len(all_repo_commits) + len(config_commits),
        "memory_entry_count": len(memory_entries),
        "revert_signal_count": len(revert_signals),
        "repo_commit_secret_masks": sum(commit["secret_masks"] for commit in all_repo_commits),
        "claude_config_commit_secret_masks": sum(commit["secret_masks"] for commit in config_commits),
        "memory_secret_masks": sum(entry["secret_masks"] for entry in memory_entries),
    }
    return context, summary


def collect_memory_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if not MEMORY_DIR.exists():
        return entries
    for directory, dirnames, filenames in os.walk(MEMORY_DIR, followlinks=False):
        dirnames[:] = sorted(dirnames)
        for filename in sorted(filenames):
            if not filename.endswith(".md"):
                continue
            path = Path(directory) / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            if not (WINDOW_START_UTC.timestamp() <= stat.st_mtime < WINDOW_END_UTC.timestamp()):
                continue
            description, description_line = read_frontmatter_description(path)
            safe_description, secret_masks = mask_secrets(description)
            entries.append(
                {
                    "path": str(path),
                    "mtime": format_mtime(stat.st_mtime),
                    "description": safe_description,
                    "description_line": description_line,
                    "frontmatter_only": True,
                    "secret_masks": secret_masks,
                }
            )
    return entries


def read_frontmatter_description(path: Path) -> tuple[str, int | None]:
    lines: list[str] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(10):
                line = handle.readline()
                if not line:
                    break
                lines.append(line)
    except OSError:
        return path.name, None
    if not lines:
        return path.name, None
    if lines[0].strip() != "---":
        return path.name, None
    in_frontmatter = True
    for index, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            in_frontmatter = False
            break
        if not in_frontmatter:
            break
        match = re.match(r"^\s*description\s*:\s*(.*)$", line.rstrip("\n"))
        if not match:
            continue
        value = match.group(1).strip()
        if value in {"|", ">"}:
            continuation: list[str] = []
            for continuation_line in lines[index - 1 :]:
                stripped = continuation_line.strip()
                if stripped and not continuation_line[:1].isspace():
                    break
                if stripped:
                    continuation.append(stripped)
            separator = " " if value == ">" else "\n"
            return separator.join(continuation) or path.name, index
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        return value or path.name, index
    return path.name, None


def window_object() -> dict[str, Any]:
    return {
        "timezone": "Asia/Taipei",
        "start": WINDOW_START.isoformat(),
        "end": WINDOW_END.isoformat(),
        "interval": "[start, end)",
        "start_utc": iso(WINDOW_START_UTC),
        "end_utc": iso(WINDOW_END_UTC),
    }


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_json(path: Path, value: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(value).encode("utf-8")
    path.write_bytes(encoded)
    return len(encoded)


def session_serialized_size(session: dict[str, Any]) -> int:
    return len(canonical_json(session).encode("utf-8"))


def make_batches(sessions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items = sorted(
        ((session_serialized_size(session), session) for session in sessions),
        key=lambda item: (-item[0], item[1]["session_id"]),
    )
    buckets = [{"batch_id": f"batch-{index:02d}", "sessions": [], "session_serialized_bytes": 0} for index in range(1, BATCH_COUNT + 1)]
    for size, session in items:
        bucket = min(buckets, key=lambda item: (item["session_serialized_bytes"], item["batch_id"]))
        bucket["sessions"].append(session)
        bucket["session_serialized_bytes"] += size
    batch_records: list[dict[str, Any]] = []
    for bucket in buckets:
        batch_records.append(
            {
                "schema_version": "recap-split-pilot/v1",
                "kind": "batch",
                "batch_id": bucket["batch_id"],
                "batch_index": int(bucket["batch_id"].rsplit("-", 1)[1]),
                "batch_count": BATCH_COUNT,
                "window": window_object(),
                "session_count": len(bucket["sessions"]),
                "session_serialized_bytes": bucket["session_serialized_bytes"],
                "sessions": bucket["sessions"],
            }
        )
    sizes = [
        {
            "session_id": session["session_id"],
            "serialized_bytes": session_serialized_size(session),
        }
        for session in sorted(sessions, key=lambda item: item["session_id"])
    ]
    return batch_records, sizes


def summarize_sessions(sessions: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "session_count": len(sessions),
        "user_message_count": sum(len(session["user_messages"]) for session in sessions),
        "raw_user_rows_in_window": sum(session["extraction"]["raw_user_rows_in_window"] for session in sessions),
        "assistant_rows_in_window": sum(session["extraction"]["assistant_rows_in_window"] for session in sessions),
        "assistant_claim_count": sum(1 for session in sessions if session["assistant_last_claim"]),
        "noise_user_rows_dropped": sum(session["extraction"]["dropped_user_noise"] for session in sessions),
        "truncated_message_count": sum(session["extraction"]["truncated_message_count"] for session in sessions),
        "truncated_chars": sum(session["extraction"]["truncated_chars"] for session in sessions),
        "secret_mask_count": sum(session["extraction"]["secret_masks"] for session in sessions),
    }


def manifest_for(
    sessions: list[dict[str, Any]],
    inventories: list[FileInventory],
    context_summary: dict[str, Any],
    batch_records: list[dict[str, Any]],
    session_sizes: list[dict[str, Any]],
    output_sizes: dict[str, int],
) -> dict[str, Any]:
    included_ids = {session["session_id"] for session in sessions}
    session_summary = summarize_sessions(sessions)
    counts = {**session_summary, **context_summary}
    counts["secret_mask_count_total"] = session_summary["secret_mask_count"] + sum(
        context_summary.get(name, 0)
        for name in (
            "repo_commit_secret_masks",
            "claude_config_commit_secret_masks",
            "memory_secret_masks",
        )
    )
    candidate_records = []
    for inventory in inventories:
        changed = (
            inventory.size_after is not None
            and inventory.mtime_after is not None
            and (inventory.size_after != inventory.size_before or inventory.mtime_after != inventory.mtime_before)
        )
        candidate_records.append(
            {
                "path": str(inventory.path),
                "size_before": inventory.size_before,
                "mtime_before": format_mtime(inventory.mtime_before),
                "size_after": inventory.size_after,
                "mtime_after": format_mtime(inventory.mtime_after) if inventory.mtime_after is not None else None,
                "changed_during_read": changed,
                "rows_in_window": inventory.rows_in_window,
                "session_ids": sorted(inventory.session_ids),
                "included_session_ids": sorted(included_ids.intersection(inventory.session_ids)),
                "parse_errors": inventory.parse_errors,
                "read_error": inventory.read_error,
            }
        )
    return {
        "schema_version": "recap-split-pilot/v1",
        "kind": "manifest",
        "window": window_object(),
        "input_policy": {
            "session_candidate_rule": "enumerate top-level project jsonl candidates with mtime >= window.start; exclude any path containing subagents; use row timestamp for [start, end) inclusion",
            "memory_rule": "enumerate ~/.claude/memory/**/*.md with mtime in [start, end); read only first 10 lines for frontmatter description",
            "repo_rule": "discover .git directories under the wrapper roots at maxdepth 2, resolve with pwd -P semantics, deduplicate realpaths, use local --branches and no merges",
            "recap_outputs_used": False,
            "model_called": False,
            "subagent_called": False,
            "formal_ledgers_written": False,
        },
        "counts": {
            **counts,
            "session_candidate_file_count": len(inventories),
            "session_candidate_parse_error_count": sum(item.parse_errors for item in inventories),
            "session_candidate_changed_during_read_count": sum(
                1
                for item in inventories
                if item.size_after is not None
                and item.mtime_after is not None
                and (item.size_after != item.size_before or item.mtime_after != item.mtime_before)
            ),
        },
        "session_candidates": candidate_records,
        "session_serialized_sizes": session_sizes,
        "batches": [
            {
                "batch_id": batch["batch_id"],
                "file": str(BATCH_DIR / f"{batch['batch_id']}.json"),
                "session_ids": [session["session_id"] for session in batch["sessions"]],
                "session_count": batch["session_count"],
                "session_serialized_bytes": batch["session_serialized_bytes"],
            }
            for batch in batch_records
        ],
        "outputs": output_sizes,
        "notes": [
            "This is a live-source snapshot collected for the fixed window; it is not a reconstruction of a prior recap run.",
            "Existing source files may have been appended or changed since the historical target snapshot; the collector records only observed read-time metadata and does not invent complete history.",
            "Assistant context is a deterministic last visible-text excerpt marked kind=claim, not a verified activity fact.",
            "Rule-adherence, retrieval-miss, codebase-alias, and commit-outcome rubric signals remain available to a downstream analyst; this collector does not decide which user turns are corrections.",
        ],
    }


def run_collection() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sessions, inventories = collect_sessions()
    context, context_summary = collect_context()
    sessions = sorted(sessions, key=lambda item: item["session_id"])
    monolithic = {
        "schema_version": "recap-split-pilot/v1",
        "kind": "monolithic",
        "window": window_object(),
        "session_count": len(sessions),
        "sessions": sessions,
    }
    batch_records, session_sizes = make_batches(sessions)
    output_sizes: dict[str, int] = {
        str(OUTPUT_DIR / "collector.py"): (OUTPUT_DIR / "collector.py").stat().st_size,
        str(OUTPUT_DIR / "requirements.md"): (OUTPUT_DIR / "requirements.md").stat().st_size,
    }
    output_sizes[str(OUTPUT_DIR / "context.json")] = write_json(OUTPUT_DIR / "context.json", context)
    output_sizes[str(OUTPUT_DIR / "monolithic.json")] = write_json(OUTPUT_DIR / "monolithic.json", monolithic)
    for batch in batch_records:
        output_sizes[str(BATCH_DIR / f"{batch['batch_id']}.json")] = write_json(
            BATCH_DIR / f"{batch['batch_id']}.json", batch
        )
    manifest = manifest_for(
        sessions,
        inventories,
        context_summary,
        batch_records,
        session_sizes,
        output_sizes,
    )
    output_sizes[str(OUTPUT_DIR / "manifest.json")] = write_json(OUTPUT_DIR / "manifest.json", manifest)
    print(json.dumps({"status": "ok", "output_sizes": output_sizes, "counts": manifest["counts"]}, ensure_ascii=False, sort_keys=True))


def make_fixture_row(
    source_file: str,
    line: int,
    session_id: str,
    row_type: str,
    timestamp: str,
    content: Any,
) -> WindowRow:
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        raise AssertionError("fixture timestamp is invalid")
    return WindowRow(source_file, line, session_id, row_type, timestamp, parsed, "/fixture/repo", content)


def run_self_test() -> None:
    fixture_path = str(OUTPUT_DIR / "fixture.jsonl")
    rows = [
        make_fixture_row(fixture_path, 1, "fixture-a", "user", "2026-09-06T03:09:59Z", "outside window"),
        make_fixture_row(fixture_path, 2, "fixture-a", "user", "2026-09-06T03:10:00Z", "keep this"),
        make_fixture_row(fixture_path, 3, "fixture-a", "user", "2026-09-06T03:11:00Z", "<system-reminder>ignore this</system-reminder>"),
        make_fixture_row(fixture_path, 4, "fixture-a", "assistant", "2026-09-06T03:12:00Z", "assistant context"),
        make_fixture_row(fixture_path, 5, "fixture-a", "assistant", "2026-09-06T03:13:00Z", [{"type": "tool_result", "content": "do not copy"}]),
        make_fixture_row(fixture_path, 6, "fixture-b", "user", "2026-09-06T04:00:00Z", "second session"),
    ]
    duplicate_rows = [
        make_fixture_row(fixture_path + ":copy", 7, "fixture-a", "user", "2026-09-06T03:14:00Z", "same session remains one"),
    ]
    sessions = build_sessions(rows + duplicate_rows, RepoResolver())
    assert len(sessions) == 2
    assert {session["session_id"] for session in sessions} == {"fixture-a", "fixture-b"}
    fixture_a = next(session for session in sessions if session["session_id"] == "fixture-a")
    assert [message["line"] for message in fixture_a["user_messages"]] == [2, 7]
    assert fixture_a["window"]["first_activity_timestamp"] == "2026-09-06T03:10:00Z"
    assert fixture_a["window"]["last_activity_timestamp"] == "2026-09-06T03:14:00Z"
    assert fixture_a["extraction"]["dropped_user_noise"] == 1
    assert fixture_a["assistant_last_claim"]["line"] == 4
    for session in sessions:
        for message in session["user_messages"]:
            assert message["source_file"].startswith(str(OUTPUT_DIR))
            assert message["line"] in {2, 6, 7}
    fixture_sessions = sessions + [
        {
            **session,
            "session_id": f"extra-{index}",
        }
        for index, session in enumerate(sessions, 1)
    ]
    batches, _ = make_batches(fixture_sessions)
    assert len(batches) == BATCH_COUNT
    batch_ids = [session["session_id"] for batch in batches for session in batch["sessions"]]
    expected_ids = [session["session_id"] for session in fixture_sessions]
    assert sorted(batch_ids) == sorted(expected_ids)
    assert len(batch_ids) == len(set(batch_ids))
    print("fixture self-test: PASS")


def main() -> None:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        run_self_test()
        return
    if len(sys.argv) != 1:
        raise SystemExit("usage: python3 collector.py [--self-test]")
    run_collection()


if __name__ == "__main__":
    main()
