# HN Comments + GitHub Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add HN top-5 comments to stage-1 raw md (DB-persisted), and record a stage-2 protocol for enriching GitHub repo URLs with metadata + README at digest time.

**Architecture:** Two-layer signal extension. Part A is a code change: HN fetcher chains Algolia (story list) → Firebase (`item/<id>.json`) for `kids` array → Firebase for top-5 comment payloads, attaches to `Item.comments` (new dataclass field, persisted as `comments_json` TEXT column, migrated via existing `ALTER TABLE ADD COLUMN` pattern). Markdown renderer adds a "💬 Top comments" block under the existing excerpt blockquote. Part B is a memory artifact only: a `reference_github_stage2_enrichment.md` capturing trigger conditions, `gh repo view` + raw README fetch sequence, and token budget; no fetcher change.

**Tech Stack:** Python 3.12, httpx, pytest 8 (asyncio_mode=auto), pytest-httpx, SQLite 3, `gh` CLI (Part B only, not at runtime).

**Spec reference:** [docs/superpowers/specs/2026-05-11-hn-comments-github-enrichment-design.md](/docs/superpowers/specs/2026-05-11-hn-comments-github-enrichment-design.md)

---

## File Structure

**Modified files (Part A):**
- `src/social_info/fetchers/base.py` — add `comments` field to `Item`, serialize to `comments_json` in `to_db_row()`
- `src/social_info/db.py` — `CREATE TABLE items` adds `comments_json TEXT`; `init_schema()` adds idempotent `ALTER TABLE` migration
- `src/social_info/__main__.py` — `_row_to_item` rehydrates `comments` from `comments_json`
- `src/social_info/markdown.py` — `render_item()` emits `> 💬 Top comments:` block when `item.comments` non-empty
- `src/social_info/fetchers/hn.py` — wire Firebase comment fetcher after Algolia loop

**New test files:**
- `tests/test_item_comments_plumbing.py` — Item dataclass, DB migration, markdown render (the plumbing for the new field)
- `tests/test_hn_comments.py` — HN fetcher Firebase logic

**New memory artifact (Part B):**
- `~/.claude/projects/-Users-linhancheng-code-social-info/memory/reference_github_stage2_enrichment.md`
- Update `~/.claude/projects/-Users-linhancheng-code-social-info/memory/MEMORY.md` index

---

## Task 1: Item dataclass — add `comments` field

**Files:**
- Modify: `src/social_info/fetchers/base.py`
- Test: `tests/test_item_comments_plumbing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_item_comments_plumbing.py`:

```python
"""Plumbing tests for the Item.comments field, DB migration, and markdown render."""
import json
import sqlite3
from datetime import datetime

import pytest

from social_info.fetchers.base import Item


def _sample_item(**overrides) -> Item:
    base = dict(
        title="t",
        url="https://example.com/x",
        canonical_url="https://example.com/x",
        source="hn",
        source_handle="front_page",
        source_tier=1,
        posted_at=datetime(2026, 5, 11, 12, 0),
        fetched_at=datetime(2026, 5, 11, 12, 0),
    )
    base.update(overrides)
    return Item(**base)


def test_item_default_comments_is_empty_list():
    item = _sample_item()
    assert item.comments == []


def test_item_to_db_row_serializes_comments_as_json():
    comments = [
        {"author": "alice", "text": "hello", "posted_at": "2026-05-11T11:00:00"},
        {"author": "bob", "text": "world", "posted_at": "2026-05-11T11:05:00"},
    ]
    item = _sample_item(comments=comments)
    row = item.to_db_row(item_id="abc", title_hash="xyz")
    assert "comments_json" in row
    assert json.loads(row["comments_json"]) == comments


def test_item_to_db_row_empty_comments_serializes_to_empty_list():
    item = _sample_item()
    row = item.to_db_row(item_id="abc", title_hash="xyz")
    assert row["comments_json"] == "[]"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v
```

Expected: 3 tests fail with `AttributeError: 'Item' object has no attribute 'comments'` (or similar).

- [ ] **Step 3: Add `comments` field to Item**

Edit `src/social_info/fetchers/base.py` — inside `@dataclass class Item`, after the `also_appeared_in` field:

```python
    comments: list[dict[str, str]] = field(default_factory=list)
```

And inside `to_db_row()`, add to the returned dict (after `also_appeared_in` key):

```python
            "comments_json": json.dumps(self.comments),
```

Final relevant portion of `base.py`:

```python
@dataclass
class Item:
    """Normalized item ready for dedup + markdown rendering."""

    title: str
    url: str
    canonical_url: str
    source: str
    source_handle: str
    source_tier: int
    posted_at: datetime
    fetched_at: datetime
    author: str = ""
    excerpt: str = ""
    language: str = "en"
    engagement: dict[str, int] = field(default_factory=dict)
    also_appeared_in: list[dict[str, str]] = field(default_factory=list)
    comments: list[dict[str, str]] = field(default_factory=list)

    def to_db_row(self, item_id: str, title_hash: str) -> dict[str, Any]:
        return {
            "id": item_id,
            "url": self.url,
            "canonical_url": self.canonical_url,
            "title": self.title,
            "title_hash": title_hash,
            "source": self.source,
            "source_handle": self.source_handle,
            "source_tier": self.source_tier,
            "posted_at": self.posted_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat(),
            "author": self.author,
            "excerpt": self.excerpt,
            "language": self.language,
            "engagement_json": json.dumps(self.engagement),
            "also_appeared_in": json.dumps(self.also_appeared_in),
            "comments_json": json.dumps(self.comments),
        }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/social_info/fetchers/base.py tests/test_item_comments_plumbing.py
git commit -m "feat: add comments field to Item dataclass

Item now carries a list[dict] of comments (author/text/posted_at),
serialized to comments_json on DB insert.

Refs: docs/superpowers/specs/2026-05-11-hn-comments-github-enrichment-design.md"
```

---

## Task 2: DB schema — add `comments_json` column + idempotent migration

**Files:**
- Modify: `src/social_info/db.py`
- Test: `tests/test_item_comments_plumbing.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_item_comments_plumbing.py`:

```python
from pathlib import Path

from social_info.db import Database


def test_init_schema_creates_comments_json_on_fresh_db(tmp_path: Path):
    db = Database(tmp_path / "fresh.db")
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()


def test_init_schema_migrates_existing_db_without_comments_json(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    legacy = sqlite3.connect(str(db_path))
    legacy.executescript(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            source_handle TEXT,
            source_tier INTEGER,
            posted_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            author TEXT,
            excerpt TEXT,
            language TEXT,
            engagement_json TEXT,
            also_appeared_in TEXT
        );
        CREATE TABLE fetch_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT,
            items_fetched INTEGER,
            error TEXT
        );
        """
    )
    legacy.commit()
    legacy.close()

    db = Database(db_path)
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()


def test_init_schema_is_idempotent_with_comments_json(tmp_path: Path):
    db = Database(tmp_path / "twice.db")
    db.init_schema()
    db.init_schema()
    cols = {r["name"] for r in db.conn.execute("PRAGMA table_info(items)")}
    assert "comments_json" in cols
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v -k "init_schema"
```

Expected: 3 tests fail — `comments_json` not in items table.

- [ ] **Step 3: Add column to schema + migration to `init_schema()`**

Edit `src/social_info/db.py`. In `SCHEMA` string, add the column to `CREATE TABLE items`:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    title TEXT NOT NULL,
    title_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    source_handle TEXT,
    source_tier INTEGER,
    posted_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    author TEXT,
    excerpt TEXT,
    language TEXT,
    engagement_json TEXT,
    also_appeared_in TEXT,
    comments_json TEXT
);
...
```

In `init_schema()`, after the existing `fetch_runs` migration block, add the items migration block:

```python
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        existing_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(fetch_runs)")
        }
        if "error_class" not in existing_cols:
            self.conn.execute("ALTER TABLE fetch_runs ADD COLUMN error_class TEXT")
        if "attempts" not in existing_cols:
            self.conn.execute("ALTER TABLE fetch_runs ADD COLUMN attempts INTEGER")

        existing_item_cols = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(items)")
        }
        if "comments_json" not in existing_item_cols:
            self.conn.execute("ALTER TABLE items ADD COLUMN comments_json TEXT")

        self.conn.commit()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v
```

Expected: all tests pass (6 total now: 3 from Task 1 + 3 new).

- [ ] **Step 5: Commit**

```bash
git add src/social_info/db.py tests/test_item_comments_plumbing.py
git commit -m "feat: add comments_json column to items table with migration

ALTER TABLE add column mirrors existing error_class / attempts pattern.
Idempotent — repeated init_schema() calls are safe."
```

---

## Task 3: __main__._row_to_item — rehydrate `comments` from DB row

**Files:**
- Modify: `src/social_info/__main__.py`
- Test: `tests/test_item_comments_plumbing.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_item_comments_plumbing.py`:

```python
from social_info.__main__ import _row_to_item


def _sample_row(comments_json: str | None = None) -> dict:
    return {
        "title": "t",
        "url": "https://example.com/x",
        "canonical_url": "https://example.com/x",
        "source": "hn",
        "source_handle": "front_page",
        "source_tier": 1,
        "posted_at": "2026-05-11T12:00:00",
        "fetched_at": "2026-05-11T12:00:00",
        "author": "",
        "excerpt": "",
        "language": "en",
        "engagement_json": "{}",
        "also_appeared_in": "[]",
        "comments_json": comments_json,
    }


def test_row_to_item_parses_comments_json():
    comments = [{"author": "alice", "text": "hi", "posted_at": "2026-05-11T11:00:00"}]
    row = _sample_row(comments_json=json.dumps(comments))
    item = _row_to_item(row)
    assert item.comments == comments


def test_row_to_item_null_comments_json_becomes_empty_list():
    row = _sample_row(comments_json=None)
    item = _row_to_item(row)
    assert item.comments == []


def test_row_to_item_missing_comments_json_key_becomes_empty_list():
    row = _sample_row()
    del row["comments_json"]
    item = _row_to_item(row)
    assert item.comments == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v -k "row_to_item"
```

Expected: 3 fail — `_row_to_item` doesn't pass `comments` arg, so `item.comments == []` but only because of dataclass default; the JSON parse case fails because the comments aren't being read.

- [ ] **Step 3: Update `_row_to_item`**

Edit `src/social_info/__main__.py` — inside `_row_to_item()`, add the `comments=` line to the `Item(...)` constructor call:

```python
def _row_to_item(row: dict) -> Item:
    return Item(
        title=row["title"],
        url=row["url"],
        canonical_url=row["canonical_url"],
        source=row["source"],
        source_handle=row["source_handle"] or "",
        source_tier=row["source_tier"] or 2,
        posted_at=datetime.fromisoformat(row["posted_at"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        author=row.get("author") or "",
        excerpt=row.get("excerpt") or "",
        language=row.get("language") or "en",
        engagement=json.loads(row.get("engagement_json") or "{}"),
        also_appeared_in=json.loads(row.get("also_appeared_in") or "[]"),
        comments=json.loads(row.get("comments_json") or "[]"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v
```

Expected: all 9 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/social_info/__main__.py tests/test_item_comments_plumbing.py
git commit -m "feat: rehydrate Item.comments from comments_json in _row_to_item"
```

---

## Task 4: Markdown render — emit `💬 Top comments` block

**Files:**
- Modify: `src/social_info/markdown.py`
- Test: `tests/test_item_comments_plumbing.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_item_comments_plumbing.py`:

```python
from social_info.markdown import render_item


def test_render_item_with_comments_emits_block():
    item = _sample_item(
        comments=[
            {"author": "alice", "text": "this is the top comment", "posted_at": "2026-05-11T11:00:00"},
            {"author": "bob", "text": "second take", "posted_at": "2026-05-11T11:05:00"},
        ]
    )
    out = render_item(item)
    assert "💬 Top comments" in out
    assert "**@alice**" in out
    assert "this is the top comment" in out
    assert "**@bob**" in out


def test_render_item_without_comments_omits_block():
    item = _sample_item()
    out = render_item(item)
    assert "💬 Top comments" not in out


def test_render_item_truncates_comment_text_over_300_chars():
    long_text = "x" * 500
    item = _sample_item(
        comments=[{"author": "a", "text": long_text, "posted_at": "2026-05-11T11:00:00"}]
    )
    out = render_item(item)
    assert ("x" * 300 + "…") in out
    assert ("x" * 301) not in out


def test_render_item_comments_strips_newlines_inline():
    item = _sample_item(
        comments=[{"author": "a", "text": "line1\nline2\nline3", "posted_at": "2026-05-11T11:00:00"}]
    )
    out = render_item(item)
    assert "line1 line2 line3" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v -k "render_item"
```

Expected: 4 tests fail — `💬 Top comments` not in output (and the no-block case might already pass).

- [ ] **Step 3: Update `render_item()` to emit the comments block**

Edit `src/social_info/markdown.py`. In `render_item()`, **after** the existing `excerpt` block (`if item.excerpt:`) and **before** the `also_appeared_in` block, insert:

```python
    if item.comments:
        lines.append("> 💬 Top comments:")
        for c in item.comments:
            text = c.get("text", "").replace("\n", " ").strip()
            if len(text) > 300:
                text = text[:300] + "…"
            author = c.get("author", "?")
            lines.append(f"> - **@{author}**: {text}")
        lines.append("")
```

Resulting `render_item()` (relevant block):

```python
    if item.excerpt:
        excerpt = item.excerpt.replace("\n", " ").strip()
        lines.append(f"> {excerpt}")
        lines.append("")

    if item.comments:
        lines.append("> 💬 Top comments:")
        for c in item.comments:
            text = c.get("text", "").replace("\n", " ").strip()
            if len(text) > 300:
                text = text[:300] + "…"
            author = c.get("author", "?")
            lines.append(f"> - **@{author}**: {text}")
        lines.append("")

    if item.also_appeared_in:
        ...
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_item_comments_plumbing.py -v
```

Expected: all 13 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/social_info/markdown.py tests/test_item_comments_plumbing.py
git commit -m "feat: render Top comments block under HN entries in raw md

Items with non-empty .comments now emit a blockquote section under
the excerpt with up to 5 entries, each trimmed to 300 chars and
newline-flattened."
```

---

## Task 5: HN fetcher — Firebase comments helper (pure async, mocked)

**Files:**
- Modify: `src/social_info/fetchers/hn.py`
- Test: `tests/test_hn_comments.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_hn_comments.py`:

```python
"""Test HN fetcher's Firebase comment enrichment path."""
from datetime import datetime

import httpx
import pytest
from pytest_httpx import HTTPXMock

from social_info.config import SourceConfig
from social_info.fetchers import hn


def _algolia_response(story_id: str = "48087925", title: str = "Test story") -> dict:
    return {
        "hits": [
            {
                "objectID": story_id,
                "title": title,
                "url": "https://example.com/article",
                "author": "tester",
                "created_at": "2026-05-10T20:57:00.000Z",
                "points": 6,
                "num_comments": 2,
            }
        ]
    }


def _firebase_story(story_id: str, kid_ids: list[int]) -> dict:
    return {
        "id": int(story_id),
        "type": "story",
        "by": "tester",
        "title": "Test story",
        "url": "https://example.com/article",
        "kids": kid_ids,
        "time": 1747000000,
    }


def _firebase_comment(comment_id: int, by: str, text: str, *, deleted: bool = False, dead: bool = False) -> dict:
    base = {"id": comment_id, "type": "comment", "time": 1747000100}
    if deleted:
        base["deleted"] = True
        return base
    if dead:
        base["dead"] = True
    base["by"] = by
    base["text"] = text
    return base


def _source() -> SourceConfig:
    return SourceConfig(
        id="hn_algolia",
        type="hn_algolia",
        tier=1,
        enabled=True,
        language="en",
        params={"limit": 5},
    )


async def test_fetch_attaches_top5_comments_to_item(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("100"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/100.json",
        json=_firebase_story("100", [201, 202, 203, 204, 205, 206]),
    )
    for cid, by, text in [
        (201, "alice", "first"),
        (202, "bob", "second"),
        (203, "carol", "third"),
        (204, "dave", "fourth"),
        (205, "eve", "fifth"),
    ]:
        httpx_mock.add_response(
            url=f"https://hacker-news.firebaseio.com/v0/item/{cid}.json",
            json=_firebase_comment(cid, by, text),
        )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items) == 1
    item = items[0]
    assert len(item.comments) == 5
    expected_posted_at = (
        datetime.utcfromtimestamp(1747000100).replace(microsecond=0).isoformat()
    )
    assert item.comments[0] == {
        "author": "alice",
        "text": "first",
        "posted_at": expected_posted_at,
    }
    assert [c["author"] for c in item.comments] == ["alice", "bob", "carol", "dave", "eve"]


async def test_fetch_skips_deleted_and_dead_comments(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("101"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/101.json",
        json=_firebase_story("101", [301, 302, 303]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/301.json",
        json=_firebase_comment(301, "?", "?", deleted=True),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/302.json",
        json=_firebase_comment(302, "?", "junk", dead=True),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/303.json",
        json=_firebase_comment(303, "alive", "real comment"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items[0].comments) == 1
    assert items[0].comments[0]["author"] == "alive"


async def test_fetch_continues_when_one_kid_call_fails(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("102"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/102.json",
        json=_firebase_story("102", [401, 402, 403]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/401.json",
        json=_firebase_comment(401, "ok1", "first"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/402.json",
        status_code=500,
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/403.json",
        json=_firebase_comment(403, "ok2", "third"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    authors = [c["author"] for c in items[0].comments]
    assert authors == ["ok1", "ok2"]


async def test_fetch_handles_story_with_no_kids(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("103"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/103.json",
        json={"id": 103, "type": "story", "by": "x", "title": "no kids", "time": 1747000000},
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert items[0].comments == []


async def test_fetch_handles_story_call_failure(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("104"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/104.json",
        status_code=500,
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    assert len(items) == 1
    assert items[0].comments == []


async def test_fetch_strips_html_and_trims_text(httpx_mock: HTTPXMock):
    long = "x" * 500
    httpx_mock.add_response(
        url__regex=r"https://hn\.algolia\.com/api/v1/search_by_date.*",
        json=_algolia_response("105"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/105.json",
        json=_firebase_story("105", [501, 502]),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/501.json",
        json=_firebase_comment(501, "html_user", f"<p>Hello <a href='x'>world</a></p>"),
    )
    httpx_mock.add_response(
        url="https://hacker-news.firebaseio.com/v0/item/502.json",
        json=_firebase_comment(502, "long_user", f"<p>{long}</p>"),
    )

    async with httpx.AsyncClient() as http:
        items = await hn.fetch(_source(), http)

    texts = {c["author"]: c["text"] for c in items[0].comments}
    assert texts["html_user"] == "Hello world"
    assert texts["long_user"] == "x" * 300 + "…"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_hn_comments.py -v
```

Expected: 6 tests fail — current `hn.fetch()` doesn't call Firebase, so `item.comments` is always `[]` (but the comments field exists from Task 1, so failure mode is "comments is empty" not "attribute missing").

- [ ] **Step 3: Add Firebase fetcher + integrate into `hn.fetch()`**

Edit `src/social_info/fetchers/hn.py`. Add imports and Firebase logic:

```python
"""Hacker News fetcher via Algolia API, comments enrichment via Firebase API."""
import asyncio
import re
from datetime import datetime, timedelta

import httpx

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://hn.algolia.com/api/v1/search_by_date"
FIREBASE_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
MAX_COMMENTS = 5
COMMENT_MAX_CHARS = 300

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _matches_any_keyword(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = title.lower()
    return any(k.lower() in lower for k in keywords)


def _clean_comment_text(raw: str) -> str:
    text = _WS_RE.sub(" ", _TAG_RE.sub("", raw or "")).strip()
    if len(text) > COMMENT_MAX_CHARS:
        return text[:COMMENT_MAX_CHARS] + "…"
    return text


async def _fetch_one_comment(http: httpx.AsyncClient, comment_id: int) -> dict | None:
    try:
        resp = await http.get(FIREBASE_URL.format(id=comment_id), timeout=15.0)
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception:
        return None
    if data.get("deleted") or data.get("dead"):
        return None
    by = data.get("by") or ""
    text = _clean_comment_text(data.get("text") or "")
    if not by or not text:
        return None
    ts = data.get("time")
    posted_at = (
        datetime.utcfromtimestamp(int(ts)).replace(microsecond=0).isoformat()
        if ts
        else ""
    )
    return {"author": by, "text": text, "posted_at": posted_at}


async def _fetch_comments_for_story(
    http: httpx.AsyncClient, story_id: str
) -> list[dict]:
    try:
        resp = await http.get(FIREBASE_URL.format(id=story_id), timeout=15.0)
        resp.raise_for_status()
        story = resp.json() or {}
    except Exception:
        return []
    kids = (story.get("kids") or [])[:MAX_COMMENTS]
    if not kids:
        return []
    fetched = await asyncio.gather(*[_fetch_one_comment(http, k) for k in kids])
    return [c for c in fetched if c is not None]


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    keywords = source.params.get("keywords", [])
    limit = source.params.get("limit", 30)
    since_ts = int((utcnow() - timedelta(hours=24)).timestamp())

    resp = await http.get(
        API_URL,
        params={
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts}",
            "hitsPerPage": limit,
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = utcnow()
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        story_id = hit.get("objectID") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={story_id}"
        if not title or not url:
            continue
        if not _matches_any_keyword(title, keywords):
            continue
        try:
            posted_at = datetime.fromisoformat(
                hit["created_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        comments = (
            await _fetch_comments_for_story(http, story_id) if story_id else []
        )
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="hn",
            source_handle="front_page",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=hit.get("author") or "",
            excerpt="",
            language="en",
            engagement={
                "score": int(hit.get("points") or 0),
                "comments": int(hit.get("num_comments") or 0),
            },
            comments=comments,
        ))
    return items
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_hn_comments.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/social_info/fetchers/hn.py tests/test_hn_comments.py
git commit -m "feat(hn): fetch top-5 comments per story via Firebase API

Chains Algolia (story list, unchanged) -> Firebase item endpoint for
kids array -> Firebase per-comment lookup for up to 5 top-level
comments. HTML stripped, text trimmed to 300 chars. Deleted/dead/
errored comment fetches are skipped silently so partial failure
doesn't fail the whole story.

Refs: docs/superpowers/specs/2026-05-11-hn-comments-github-enrichment-design.md"
```

---

## Task 6: Smoke test — verify real HN end-to-end

**Files:**
- No code change. Manual verification.

- [ ] **Step 1: Run smoke against real HN**

```bash
uv run python -m social_info --smoke --source hn_algolia
```

Expected: prints rendered HN items to stdout. Each item should include a `> 💬 Top comments:` block with `> - **@username**: text` entries.

- [ ] **Step 2: Inspect output**

Visually confirm at least one item shows real top comments and that:
- Author handles match HN UI
- Comment text is HTML-stripped (no `<p>`, `<a>` tags)
- Long comments end with `…`
- Newlines in comment bodies are flattened

- [ ] **Step 3: Run dry-run against real HN to verify DB write path doesn't break**

```bash
uv run python -m social_info --dry-run --source hn_algolia
```

Expected: prints `DRY-RUN: <N> new items, 0 failures` without exception.

- [ ] **Step 4: Run full test suite to verify no regression**

```bash
uv run pytest -v
```

Expected: all tests pass (existing `test_classify_error.py`, `test_known_issues.py` plus new `test_item_comments_plumbing.py`, `test_hn_comments.py`).

- [ ] **Step 5: Commit (no-op or doc update)**

If everything passes, no code change. If smoke surfaces a bug, fix it inline with a new follow-up commit before proceeding.

If a `KNOWN_ISSUES.md` regeneration is needed (e.g., schema migration ran on `state.db`), re-run the full pipeline once locally:

```bash
uv run python -m social_info --source hn_algolia
git diff state.db reports/$(date +%Y-%m-%d).md KNOWN_ISSUES.md
```

(`state.db` is a binary file, so `git diff` will say "binary differs" — that's expected.)

---

## Task 7: GitHub stage-2 protocol — memory artifact

**Files:**
- Create: `/Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/reference_github_stage2_enrichment.md`
- Modify: `/Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/MEMORY.md`

- [ ] **Step 1: Write the memory file**

Create `/Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/reference_github_stage2_enrichment.md`:

```markdown
---
name: GitHub repo stage-2 enrichment protocol
description: How and when to fetch gh repo metadata + README at digest time before writing OSS / Trending entries
type: reference
---

當 daily digest 要寫進開源 / Trending entry 時，對符合條件的 github.com URL **先 enrich 再判斷**。Fetcher 層不抓 README（會浪費 token 在最後 90% 不會進 digest 的 entry）；digest 階段才按需抓。

## 觸發條件（all of）

1. URL host 是 `github.com`、path 是 `<owner>/<repo>` 純粹形式（不是 `/issues/...`、`/pull/...`、`/blob/...`、`/releases/...` 子路徑——那些子路徑各自已有內容、不需 repo 層級 enrichment）
2. 該 entry **已經被選為 digest 候選**（不是掃整份 raw md 的 github URL；只有「我打算寫進今天 digest 的條目」才 enrich）
3. Entry 的 source 是 `github_trending` / `trendshift` / `hn` / `reddit` / 中文 source 裡 link 到 github.com 的

跳過 enrichment 的場景：
- 子路徑（issue / PR / file / release）
- raw md excerpt 已經足以做 clone / star / skip 判斷時，不要為了完整性 burn token

## Fetch 步驟（按順序）

```bash
# Step 1 — metadata（必跑）
gh repo view <owner>/<repo> --json description,topics,stargazerCount,forkCount,pushedAt,primaryLanguage,licenseInfo,defaultBranchRef

# Step 2 — README 開頭 1500 chars
gh api repos/<owner>/<repo>/contents/README.md --jq '.content' | base64 -d | head -c 1500
# 或：
curl -sL https://raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md | head -c 1500

# Step 3 — topics-aware 定位文件（只在 step 2 沒帶出架構脈絡時跑）
# 按順序試、第一個 200 OK 就停：
gh api repos/<owner>/<repo>/contents/ARCHITECTURE.md
gh api repos/<owner>/<repo>/contents/docs/ARCHITECTURE.md
gh api repos/<owner>/<repo>/contents/docs/QUICKSTART.md
gh api repos/<owner>/<repo>/contents/docs/GETTING_STARTED.md
gh api repos/<owner>/<repo>/contents/GETTING_STARTED.md
# 取前 500 chars
```

## Token 預算

每 repo enrich 約 800 tokens（1500 README + 500 positioning + 200 metadata）。Digest 一輪 enrich 10-15 repos = +10-15k input tokens。Digest 既有 raw md 約 25k tokens，總成本可接受。

超出 budget 的話：先把 README 砍到 1000 chars，再不夠才丟 positioning doc。

## 對應 digest output

繼續用 [feedback_digest_oss_trending_richness.md](feedback_digest_oss_trending_richness.md) 的 schema：簡介在前、判斷在後。enrich 後的資料餵進「該 clone / star 追 / 跳過」判斷的「成熟度」+「跟 stack 契合度」兩格。

## 為什麼是 stage-2 不是 stage-1

Stage-1 enrich 每 daily run 要拉 10-15 個 README、raw md 多 30-50KB、每天 commit。但 90% 的 trending repo 不會進 digest（不夠特別 / 跟 user stack 無關 / 跳過），預先抓全是浪費。Stage-2 改成「我已經決定要寫這條」才 enrich，token 用在刀口上。

對比 HN comments 走 stage-1（fetcher 每天抓）：HN comments 是 entry 本身的核心 signal，跟 story metadata 是綁定的，留言區常常才是新聞。所以 stage-1 cache 合理。
```

- [ ] **Step 2: Update MEMORY.md index**

Edit `/Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/MEMORY.md`. Append a new line (keep alphabetical / topical grouping with existing entries):

```markdown
- [GitHub stage-2 enrichment protocol](reference_github_stage2_enrichment.md) — digest 時對 github.com URL 拉 `gh repo view` + README 1500c + topics-aware doc 500c，不在 stage-1 抓
```

- [ ] **Step 3: Verify memory artifact loads**

No automated test for this — confirm by:

```bash
ls -la /Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/reference_github_stage2_enrichment.md
grep -F "reference_github_stage2_enrichment" /Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/memory/MEMORY.md
```

Expected: file exists, index line present.

- [ ] **Step 4: No-op step**

(No commit needed — these are user memory files outside the repo and are not version-controlled.)

- [ ] **Step 5: No-op step**

(Done.)

---

## Verification Checklist

After all 7 tasks complete:

- [ ] `uv run pytest -v` shows all tests passing (existing 18 + new ~13 = ~31)
- [ ] `uv run python -m social_info --smoke --source hn_algolia` shows `💬 Top comments` blocks
- [ ] `git log --oneline` shows ~5 conventional commits (one per code task)
- [ ] `sqlite3 state.db "PRAGMA table_info(items)" | grep comments_json` returns a row
- [ ] Memory file at `~/.claude/projects/-Users-linhancheng-code-social-info/memory/reference_github_stage2_enrichment.md` exists
- [ ] `MEMORY.md` index includes the new entry
- [ ] No new entries in `KNOWN_ISSUES.md` caused by this change
- [ ] Spec acceptance criteria (section "Acceptance criteria" in design doc) all met

---

## Notes for the implementer

- **DRY**: `_clean_comment_text()` and the regex patterns `_TAG_RE` / `_WS_RE` parallel `wewe_rss.py`. Do NOT import them — keep modules independent. The copy is intentional (2 places, low maintenance cost; refactoring to a shared helper is YAGNI).

- **YAGNI**: do not add a `MAX_COMMENTS` config knob to `sources.yml`. The constant is in `hn.py`. If the user wants to tune, they edit one number.

- **Frequent commits**: each task ends with one commit. Do not bundle.

- **Asyncio safety**: `_fetch_comments_for_story()` uses `asyncio.gather(...)` for parallel kid fetches. If a future change introduces a per-source rate limit, this should be revisited.

- **Naive UTC timestamps**: `posted_at` for comments uses `datetime.utcfromtimestamp(...).replace(microsecond=0).isoformat()`. This matches the rest of the codebase which uses naive UTC strings. Do not switch to tz-aware here — it would break the `_row_to_item` symmetry with other fields.

- **Migration safety**: the `ALTER TABLE items ADD COLUMN comments_json TEXT` line will run on the user's live `state.db` the first time the pipeline runs after this change. SQLite handles this atomically and the new column defaults to NULL for existing rows.

- **Part B has no code**: Task 7 is documentation-only. The protocol takes effect the next time the user asks for a digest — Claude reads the memory and follows the steps. No daily-run impact.
