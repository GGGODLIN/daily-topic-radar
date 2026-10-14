"""LINUX DO latest topics via the public Discourse RSS feed.

httpx is rejected by the site's edge challenge while the system curl binary is
served the feed. curl is the transport only; a challenge or a 429 stops the
source and is not retried. /latest.rss already spans public categories.
"""
import asyncio
import re
import subprocess
from datetime import datetime, timedelta
from urllib.parse import urlencode

import feedparser
from bs4 import BeautifulSoup, NavigableString

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

FEED_URL = "https://linux.do/latest.rss"
USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"
# A short page inside the window is not proof that collection is complete.
PAGE_SIZE = 100
DEFAULT_WINDOW_HOURS = 24
DEFAULT_MAX_PAGES = 50
REQUEST_GAP_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 25
# With a 60s gap, the 3000s budget can stop collection before the 50-page
# cap. Keep the runner's 3300s hard limit; retain validated partial items
# and report an incomplete window instead of extending the run.
FETCH_BUDGET_SECONDS = 3000

_POST_COUNT_RE = re.compile(r"^\d+\s*个帖子\s*-\s*\d+\s*位参与者$")
_READ_FULL_RE = re.compile(r"^(?:阅读完整话题|Read the full topic)$", re.IGNORECASE)
_SKIP_TAGS = frozenset({"script", "style", "svg"})
_BLANK_RUN_RE = re.compile(r"\n{3,}")


class LinuxDoFetchError(RuntimeError):
    """Raised when the feed cannot be read completely inside the window."""

    def __init__(self, message: str, *, kept: list[Item] | None = None) -> None:
        super().__init__(message)
        # Only pages already checked for order, uniqueness, and dates. A page
        # that itself failed stays out, so its items cannot leak in here.
        self.kept = list(kept or [])


def _is_post_count(node) -> bool:
    return bool(_POST_COUNT_RE.fullmatch(node.get_text(" ", strip=True)))


def _is_read_full(node) -> bool:
    return bool(_READ_FULL_RE.fullmatch(node.get_text(" ", strip=True)))


def _drop_rss_footer(soup: BeautifulSoup) -> None:
    """Remove only the Discourse footer appended after the opening post.

    That footer is the trailing post-count block, and a read-full link only
    when it is the last block. The same link or sentence earlier in the post
    is content; a document-wide match would delete it.
    """
    blocks = [node for node in soup.find_all(["p", "div"]) if node.parent == soup]
    if not blocks:
        return
    if _is_read_full(blocks[-1]):
        blocks[-1].decompose()
        blocks.pop()
    if blocks and _is_post_count(blocks[-1]):
        blocks[-1].decompose()


def _render_html(node, parts: list[str], code_blocks: list[str]) -> None:
    if isinstance(node, NavigableString):
        parts.append(str(node))
        return
    name = getattr(node, "name", None)
    if not name or name in _SKIP_TAGS:
        return
    if name == "img":
        src = node.get("src") or ""
        if src:
            parts.append(f"![{node.get('alt') or ''}]({src})")
        return
    if name == "br":
        parts.append("\n")
        return
    if name == "hr":
        parts.append("\n\n---\n\n")
        return
    if name == "pre":
        # Placeholder so the later blank-line collapse cannot touch code.
        token = f"\x00PRE{len(code_blocks)}\x00"
        code_blocks.append(node.get_text())
        parts.append(f"\n\n{token}\n\n")
        return
    if name == "a":
        href = (node.get("href") or "").strip()
        inner: list[str] = []
        for child in node.children:
            _render_html(child, inner, code_blocks)
        text = "".join(inner).strip()
        if href and text and text != href:
            parts.append(f"[{text}]({href})")
        elif href:
            parts.append(href)
        else:
            parts.append(text)
        return
    for child in getattr(node, "children", ()):
        _render_html(child, parts, code_blocks)
    if name in {"p", "li", "blockquote", "h1", "h2", "h3", "h4", "div", "aside"}:
        parts.append("\n\n")


def _restore_code_blocks(text: str, code_blocks: list[str]) -> str:
    def restore(match: re.Match[str]) -> str:
        code = code_blocks[int(match.group(1))]
        if code.endswith("\n"):
            code = code[:-1]
        return f"```\n{code}\n```"

    return re.sub(r"\x00PRE(\d+)\x00", restore, text)


def _strip_html(markup: str) -> str:
    soup = BeautifulSoup(markup or "", "html.parser")
    _drop_rss_footer(soup)
    code_blocks: list[str] = []
    parts: list[str] = []
    for child in soup.children:
        _render_html(child, parts, code_blocks)
    text = _BLANK_RUN_RE.sub("\n\n", "".join(parts))
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    return _restore_code_blocks(text, code_blocks)


def _fetch_page_text(page: int) -> str:
    """Sync curl. Kept off the event loop via asyncio.to_thread."""
    url = f"{FEED_URL}?{urlencode({'page': page, 'per_page': PAGE_SIZE})}"
    try:
        result = subprocess.run(
            [
                "curl",
                "-sS",
                "-A",
                USER_AGENT,
                "--max-time",
                str(REQUEST_TIMEOUT_SECONDS),
                "-w",
                "\n__SOCIAL_INFO_HTTP__%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=REQUEST_TIMEOUT_SECONDS + 5,
        )
    except subprocess.TimeoutExpired as exc:
        raise LinuxDoFetchError(f"curl timed out on page {page}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise LinuxDoFetchError(f"curl failed on page {page}: {detail[:300]}")
    # -w appends the code with no newline. A marker keeps a body that itself
    # ends in a status-shaped line from being read as the HTTP code.
    marker = "\n__SOCIAL_INFO_HTTP__"
    if marker not in result.stdout:
        raise LinuxDoFetchError(f"linux.do page {page} had no HTTP status")
    body, _, status_text = result.stdout.rpartition(marker)
    try:
        status = int(status_text.strip())
    except ValueError:
        status = 0
    if status == 429:
        raise LinuxDoFetchError(f"linux.do rate limited (429) on page {page}; stopped")
    if status != 200:
        raise LinuxDoFetchError(f"linux.do HTTP {status} on page {page}")
    return body


def _entry_posted_at(entry, page: int, index: int) -> datetime:
    # feedparser's *_parsed is UTC struct_time. mktime would read it as local.
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        raise LinuxDoFetchError(
            f"linux.do page {page} entry {index} has no pubDate"
        )
    return datetime(*parsed[:6])


def _parse_page(body: str, source: SourceConfig, now: datetime, page: int) -> tuple[list[Item], int]:
    """Return items and the raw entry count. Count is not the kept-item count."""
    if "<rss" not in body or "<channel" not in body:
        raise LinuxDoFetchError(f"linux.do page {page} was not an RSS feed")
    parsed = feedparser.parse(body)
    if getattr(parsed, "bozo", False):
        raise LinuxDoFetchError(f"linux.do page {page} was not valid RSS")
    entries = list(parsed.entries)
    items: list[Item] = []
    for index, entry in enumerate(entries):
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            raise LinuxDoFetchError(
                f"linux.do page {page} entry {index} has no title or link"
            )
        posted_at = _entry_posted_at(entry, page, index)
        category = (entry.get("tags") or [{}])[0].get("term", "") if entry.get("tags") else ""
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="linuxdo",
            source_handle=category or "latest",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author") or "",
            excerpt=_strip_html(entry.get("summary") or entry.get("description") or ""),
            language=source.language,
            engagement={},
            metadata={"content_kind": "summary"},
        ))
    return items, len(entries)


def _assert_page_moves_backward(page: int, page_items: list[Item], previous_oldest: datetime | None) -> None:
    dates = [item.posted_at for item in page_items]
    if dates != sorted(dates, reverse=True):
        raise LinuxDoFetchError(f"linux.do page {page} is not newest-first")
    if previous_oldest is not None and dates[-1] >= previous_oldest:
        raise LinuxDoFetchError(f"linux.do page {page} did not advance past the previous page")


async def fetch(source: SourceConfig, http=None) -> list[Item]:
    window_hours = source.params.get("window_hours", DEFAULT_WINDOW_HOURS)
    max_pages = source.params.get("max_pages", DEFAULT_MAX_PAGES)
    cutoff = utcnow() - timedelta(hours=window_hours)
    now = utcnow()
    started = utcnow()

    collected: list[Item] = []
    seen_urls: set[str] = set()
    previous_oldest: datetime | None = None
    for page in range(max_pages):
        # Gap and the request timeout both count. A check only before the
        # gap lets the last page run past the budget by a full timeout.
        needed = REQUEST_TIMEOUT_SECONDS + (REQUEST_GAP_SECONDS if page else 0)
        if (utcnow() - started).total_seconds() + needed > FETCH_BUDGET_SECONDS:
            raise LinuxDoFetchError(
                f"linux.do fetch budget of {FETCH_BUDGET_SECONDS}s exhausted before the "
                f"{window_hours}h window ended; not a complete day",
                kept=collected,
            )
        if page:
            await asyncio.sleep(REQUEST_GAP_SECONDS)
        try:
            body = await asyncio.to_thread(_fetch_page_text, page)
            page_items, raw_count = _parse_page(body, source, now, page)
        except LinuxDoFetchError as exc:
            raise LinuxDoFetchError(str(exc), kept=collected) from exc
        if raw_count == 0:
            raise LinuxDoFetchError(f"linux.do page {page} had no entries", kept=collected)
        page_urls = [item.canonical_url for item in page_items]
        if len(page_urls) != len(set(page_urls)):
            raise LinuxDoFetchError(f"linux.do page {page} repeats an entry", kept=collected)
        if seen_urls and set(page_urls) <= seen_urls:
            raise LinuxDoFetchError(
                f"linux.do page {page} repeated the previous page", kept=collected
            )
        try:
            _assert_page_moves_backward(page, page_items, previous_oldest)
        except LinuxDoFetchError as exc:
            raise LinuxDoFetchError(str(exc), kept=collected) from exc
        oldest = min(item.posted_at for item in page_items)
        fresh = [item for item in page_items if item.canonical_url not in seen_urls]
        seen_urls.update(item.canonical_url for item in fresh)
        collected.extend(item for item in fresh if item.posted_at >= cutoff)
        # Valid items remain useful even when the page cannot prove full coverage.
        if oldest >= cutoff and raw_count < PAGE_SIZE:
            raise LinuxDoFetchError(
                f"linux.do page {page} is short and still inside the "
                f"{window_hours}h window; not treating it as the end of the feed",
                kept=collected,
            )
        previous_oldest = oldest
        if previous_oldest < cutoff:
            return collected
    raise LinuxDoFetchError(
        f"linux.do still inside the {window_hours}h window after {max_pages} pages; "
        "refusing to truncate silently",
        kept=collected,
    )
