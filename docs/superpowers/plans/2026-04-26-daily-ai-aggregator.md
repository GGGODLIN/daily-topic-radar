# Daily AI Aggregator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-based GitHub Actions cron pipeline that aggregates AI ecosystem signals from English / Chinese (CN + TW) social platforms and emits a daily structured Markdown file for a downstream Claude Code conversation to consume.

**Architecture:** Two-stage decoupled design. Stage 1 (this plan): pipeline does fetch → normalize → dedup (URL hash + title hash) → render markdown → commit. Stage 2 (out of scope): user runs Claude Code, which uses gh CLI to read the daily `.md` and does personalized filtering/ranking/summarization.

**Tech Stack:** Python 3.12 + uv; httpx (async); feedparser (RSS); PyYAML (config); sqlite3 (stdlib); BeautifulSoup4 (GitHub Trending HTML); pytest + pytest-asyncio + pytest-httpx; GitHub Actions cron.

**Spec:** [`docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md`](../specs/2026-04-26-daily-ai-aggregator-design.md)

---

## File Structure

### Created in this plan

```
pyproject.toml
uv.lock                                   (uv generates)
.python-version
.gitignore
.env.example
sources.yml
ruff.toml
src/social_info/
├── __init__.py
├── __main__.py                           CLI entry: python -m social_info
├── config.py                             Load + validate sources.yml
├── pipeline.py                           fetch → dedup → render orchestration
├── db.py                                 sqlite3 wrapper for items + fetch_runs
├── dedup.py                              L1 (URL) + L2 (title hash)
├── markdown.py                           Render item / file / failures section
├── url_utils.py                          canonical_url
├── health.py                             7-day source success-rate report
└── fetchers/
    ├── __init__.py
    ├── base.py                           Item dataclass + FetchResult + helpers
    ├── hn.py                             HN Algolia API
    ├── reddit.py                         Reddit top.json
    ├── github_trending.py                trending HTML scrape
    ├── product_hunt.py                   GraphQL
    ├── huggingface.py                    Models + Spaces API
    ├── rss.py                            Generic RSS (lab blogs, tech media)
    ├── rsshub.py                         RSSHub instance call
    ├── twitter.py                        twitterapi.io
    ├── threads.py                        Meta Threads API + token refresh
    └── wewe_rss.py                       WeChat (skeleton; disabled default)
tests/
├── __init__.py
├── conftest.py
├── fixtures/                             real-API response samples
├── test_url_utils.py
├── test_db.py
├── test_dedup.py
├── test_markdown.py
├── test_config.py
└── fetchers/
    ├── __init__.py
    ├── test_hn.py
    ├── test_reddit.py
    ├── test_github_trending.py
    ├── test_product_hunt.py
    ├── test_huggingface.py
    ├── test_rss.py
    ├── test_rsshub.py
    ├── test_twitter.py
    └── test_threads.py
.github/workflows/
├── daily.yml                             cron + workflow_dispatch
├── test.yml                              on push / PR
└── smoke.yml                             workflow_dispatch (real API)
README.md
```

### Generated at runtime (gitignored or tracked depending on file)

```
state.db                                  TRACKED in repo (commit each run)
reports/YYYY-MM-DD.md                     TRACKED in repo (commit each run)
.venv/                                    gitignored
__pycache__/                              gitignored
.pytest_cache/                            gitignored
.env                                      gitignored
```

---

## Task Sequencing

```
A1 → A2 → A3 → A4 → B5 → B6 →
  C7 ┬─ C8 ─ C9 ─ C10 ─ C11 ─ C12 ─ C13 ─ C14 ─ C15 ─ C16
                                                          ↓
                                          D17 → D18 → D19 → E20 → E21 → E22
```

Phase A & B are sequential (each depends on the previous). Phase C fetchers are independent — can be done in any order or in parallel. Phase D / E sequential.

---

## Phase A: Infrastructure & Core

### Task A1: Project Bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `.python-version`
- Create: `.gitignore`
- Create: `ruff.toml`
- Create: `src/social_info/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `.python-version`**

```
3.12
```

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "social-info"
version = "0.1.0"
description = "Daily AI ecosystem raw aggregator"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "feedparser>=6.0",
    "pyyaml>=6.0",
    "beautifulsoup4>=4.12",
]

[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-httpx>=0.30",
    "ruff>=0.6",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/social_info"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 3: Create `ruff.toml`**

```toml
line-length = 100
target-version = "py312"

[lint]
select = ["E", "F", "I", "B", "UP", "SIM"]
ignore = ["E501"]
```

- [ ] **Step 4: Create `.gitignore`**

```
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
.env
*.egg-info/
dist/
build/
```

- [ ] **Step 5: Create empty stub files**

`src/social_info/__init__.py`:
```python
"""Daily AI ecosystem raw aggregator."""
__version__ = "0.1.0"
```

`tests/__init__.py`: empty file

`tests/conftest.py`:
```python
"""Shared pytest fixtures."""
```

- [ ] **Step 6: Install + verify**

```bash
uv sync
uv run python -c "import social_info; print(social_info.__version__)"
uv run ruff check src tests
uv run pytest --collect-only
```

Expected:
- `0.1.0` printed
- ruff passes (no files yet so trivially clean)
- pytest collects 0 tests, no error

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .python-version .gitignore ruff.toml src/ tests/
git commit -m "chore: bootstrap python project with uv and ruff"
```

---

### Task A2: Core Types & URL Utilities

**Files:**
- Create: `src/social_info/fetchers/__init__.py`
- Create: `src/social_info/fetchers/base.py`
- Create: `src/social_info/url_utils.py`
- Create: `tests/test_url_utils.py`

- [ ] **Step 1: Write failing test for `canonical_url`**

`tests/test_url_utils.py`:
```python
import pytest
from social_info.url_utils import canonical_url


@pytest.mark.parametrize("input_url,expected", [
    ("https://example.com/a?utm_source=x&utm_medium=email&id=1",
     "https://example.com/a?id=1"),
    ("https://example.com/a?fbclid=abc123",
     "https://example.com/a"),
    ("https://example.com/a?ref=hn",
     "https://example.com/a"),
    ("https://example.com/a?source=rss",
     "https://example.com/a"),
    ("https://example.com/a?utm_campaign=x&keep=1&utm_term=y",
     "https://example.com/a?keep=1"),
    ("https://example.com/a/",
     "https://example.com/a"),
    ("HTTPS://Example.COM/A?ID=1",
     "https://example.com/A?ID=1"),
    ("https://example.com/a#frag",
     "https://example.com/a"),
    ("https://example.com/a?b=2&a=1",
     "https://example.com/a?a=1&b=2"),
])
def test_canonical_url(input_url, expected):
    assert canonical_url(input_url) == expected
```

- [ ] **Step 2: Run test — verify fail**

```bash
uv run pytest tests/test_url_utils.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'social_info.url_utils'`

- [ ] **Step 3: Implement `url_utils.canonical_url`**

`src/social_info/url_utils.py`:
```python
"""URL canonicalization for dedup."""
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

TRACKING_PARAM_PREFIXES = ("utm_",)
TRACKING_PARAM_NAMES = frozenset({
    "fbclid", "gclid", "msclkid", "yclid",
    "ref", "ref_src", "ref_url", "source",
    "mc_cid", "mc_eid", "_hsenc", "_hsmi",
})


def canonical_url(url: str) -> str:
    """Strip tracking params, normalize host, sort remaining params, drop fragment."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or parts.path

    if not path:
        path = "/"
    elif parts.path.endswith("/") and parts.path != "/":
        path = parts.path.rstrip("/")
    else:
        path = parts.path

    kept = []
    for k, v in parse_qsl(parts.query, keep_blank_values=True):
        if any(k.startswith(p) for p in TRACKING_PARAM_PREFIXES):
            continue
        if k in TRACKING_PARAM_NAMES:
            continue
        kept.append((k, v))
    kept.sort()
    query = urlencode(kept)

    return urlunsplit((scheme, netloc, path, query, ""))
```

- [ ] **Step 4: Run test — verify pass**

```bash
uv run pytest tests/test_url_utils.py -v
```

Expected: 9 passed

- [ ] **Step 5: Implement core types in `fetchers/base.py`**

`src/social_info/fetchers/__init__.py`: empty file

`src/social_info/fetchers/base.py`:
```python
"""Core types shared by all fetchers."""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any
import json


@dataclass
class Item:
    """Normalized item ready for dedup + markdown rendering."""
    title: str
    url: str
    canonical_url: str
    source: str           # "x" / "reddit" / "hn" / "rss" / "rsshub" / ...
    source_handle: str    # "@karpathy" / "r/LocalLLaMA" / "anthropic_blog"
    source_tier: int      # 1 or 2
    posted_at: datetime
    fetched_at: datetime
    author: str = ""
    excerpt: str = ""
    language: str = "en"
    engagement: dict[str, int] = field(default_factory=dict)
    also_appeared_in: list[dict[str, str]] = field(default_factory=list)

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
        }


@dataclass
class FetchResult:
    """Result of one fetcher run."""
    source_id: str
    items: list[Item] = field(default_factory=list)
    ok: bool = True
    error: str = ""
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None

    def items_count(self) -> int:
        return len(self.items)
```

- [ ] **Step 6: Run lint + tests**

```bash
uv run ruff check src tests
uv run pytest -v
```

Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/social_info/url_utils.py src/social_info/fetchers/ tests/test_url_utils.py
git commit -m "feat: add canonical_url and core Item/FetchResult types"
```

---

### Task A3: SQLite Database Layer

**Files:**
- Create: `src/social_info/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

`tests/test_db.py`:
```python
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from social_info.db import Database


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d


def test_init_creates_tables(db):
    cur = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur}
    assert "items" in tables
    assert "fetch_runs" in tables


def test_insert_and_query_item(db):
    row = {
        "id": "abc",
        "url": "https://example.com/a",
        "canonical_url": "https://example.com/a",
        "title": "Hello",
        "title_hash": "h1",
        "source": "hn",
        "source_handle": "front_page",
        "source_tier": 1,
        "posted_at": "2026-04-26T08:00:00",
        "fetched_at": "2026-04-26T09:00:00",
        "author": "user",
        "excerpt": "summary",
        "language": "en",
        "engagement_json": json.dumps({"score": 100}),
        "also_appeared_in": "[]",
    }
    db.insert_item(row)

    assert db.has_item_id("abc") is True
    assert db.has_item_id("xyz") is False


def test_has_title_hash(db):
    row = {
        "id": "abc", "url": "u", "canonical_url": "u", "title": "T",
        "title_hash": "TITLEHASH", "source": "hn", "source_handle": "fp",
        "source_tier": 1, "posted_at": "2026-04-26T08:00:00",
        "fetched_at": "2026-04-26T09:00:00", "author": "", "excerpt": "",
        "language": "en", "engagement_json": "{}", "also_appeared_in": "[]",
    }
    db.insert_item(row)
    found = db.find_by_title_hash("TITLEHASH")
    assert found is not None
    assert found["id"] == "abc"


def test_log_fetch_run(db):
    db.log_fetch_run(
        source="hn",
        started_at=datetime(2026, 4, 26, 9, 0, 0),
        ended_at=datetime(2026, 4, 26, 9, 0, 5),
        status="ok",
        items_fetched=10,
        error="",
    )
    cur = db.conn.execute("SELECT source, status, items_fetched FROM fetch_runs")
    rows = list(cur)
    assert rows == [("hn", "ok", 10)]


def test_recent_fetch_runs(db):
    db.log_fetch_run("hn", datetime(2026, 4, 25, 9, 0), datetime(2026, 4, 25, 9, 0, 5), "ok", 10, "")
    db.log_fetch_run("hn", datetime(2026, 4, 26, 9, 0), datetime(2026, 4, 26, 9, 0, 5), "failed", 0, "boom")
    rows = db.recent_fetch_runs(days=7)
    assert len(rows) == 2
```

- [ ] **Step 2: Run tests — verify fail**

```bash
uv run pytest tests/test_db.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement `db.py`**

`src/social_info/db.py`:
```python
"""SQLite layer for items + fetch_runs."""
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

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
    also_appeared_in TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_title_hash ON items(title_hash);
CREATE INDEX IF NOT EXISTS idx_items_posted_at ON items(posted_at);

CREATE TABLE IF NOT EXISTS fetch_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT,
    items_fetched INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_source ON fetch_runs(source);
CREATE INDEX IF NOT EXISTS idx_fetch_runs_started ON fetch_runs(started_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def has_item_id(self, item_id: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM items WHERE id = ? LIMIT 1", (item_id,))
        return cur.fetchone() is not None

    def find_by_title_hash(self, title_hash: str) -> dict[str, Any] | None:
        cur = self.conn.execute(
            "SELECT * FROM items WHERE title_hash = ? LIMIT 1", (title_hash,)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def insert_item(self, row: dict[str, Any]) -> None:
        cols = ",".join(row.keys())
        placeholders = ",".join("?" for _ in row)
        self.conn.execute(
            f"INSERT OR IGNORE INTO items ({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )
        self.conn.commit()

    def update_also_appeared_in(self, item_id: str, also_appeared_in_json: str) -> None:
        self.conn.execute(
            "UPDATE items SET also_appeared_in = ? WHERE id = ?",
            (also_appeared_in_json, item_id),
        )
        self.conn.commit()

    def log_fetch_run(
        self,
        source: str,
        started_at: datetime,
        ended_at: datetime,
        status: str,
        items_fetched: int,
        error: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO fetch_runs (source, started_at, ended_at, status, items_fetched, error) VALUES (?, ?, ?, ?, ?, ?)",
            (source, started_at.isoformat(), ended_at.isoformat(), status, items_fetched, error),
        )
        self.conn.commit()

    def recent_fetch_runs(self, days: int = 7) -> list[dict[str, Any]]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        cur = self.conn.execute(
            "SELECT * FROM fetch_runs WHERE started_at >= ? ORDER BY started_at DESC",
            (since,),
        )
        return [dict(r) for r in cur]

    def close(self) -> None:
        self.conn.close()
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/test_db.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/social_info/db.py tests/test_db.py
git commit -m "feat: add SQLite database layer for items and fetch_runs"
```

---

### Task A4: Dedup Logic (L1 + L2)

**Files:**
- Create: `src/social_info/dedup.py`
- Create: `tests/test_dedup.py`

- [ ] **Step 1: Write failing tests**

`tests/test_dedup.py`:
```python
import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from social_info.db import Database
from social_info.dedup import (
    compute_item_id,
    compute_title_hash,
    normalize_title,
    Deduper,
)
from social_info.fetchers.base import Item


def test_normalize_title_strips_punctuation_and_lowercases():
    assert normalize_title("OpenAI Releases GPT-5!") == "openai releases gpt 5"
    assert normalize_title("中文 標題（測試）") == "中文 標題 測試"
    assert normalize_title("  multiple   spaces  ") == "multiple spaces"


def test_normalize_title_handles_full_width_space():
    # NFKC should normalize full-width to ASCII
    assert normalize_title("hello　world") == "hello world"


def test_compute_item_id_uses_canonical_url():
    item_id = compute_item_id("https://example.com/a")
    assert item_id == hashlib.sha1(b"https://example.com/a").hexdigest()


def test_compute_title_hash():
    h = compute_title_hash("OpenAI Releases GPT-5!")
    expected = hashlib.sha1("openai releases gpt 5".encode()).hexdigest()
    assert h == expected


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = Database(Path(tmp) / "test.db")
        d.init_schema()
        yield d
        d.close()


def _make_item(url, title, source="hn", handle="front_page", tier=1):
    return Item(
        title=title,
        url=url,
        canonical_url=url,
        source=source,
        source_handle=handle,
        source_tier=tier,
        posted_at=datetime(2026, 4, 26, 8, 0, 0),
        fetched_at=datetime(2026, 4, 26, 9, 0, 0),
    )


def test_l1_dedup_skips_seen_url(db):
    deduper = Deduper(db)
    item1 = _make_item("https://example.com/a", "Hello")
    new1 = deduper.process([item1])
    assert len(new1) == 1

    item_dup = _make_item("https://example.com/a", "Hello")
    new2 = deduper.process([item_dup])
    assert len(new2) == 0


def test_l2_dedup_merges_same_title_keeps_higher_tier(db):
    deduper = Deduper(db)
    # First arrival: tier 2
    item_t2 = _make_item("https://a.com/x", "OpenAI Releases GPT-5!", source="rss", handle="vb", tier=2)
    deduper.process([item_t2])

    # Second arrival: tier 1, different URL but same normalized title
    item_t1 = _make_item("https://b.com/y", "openai releases GPT 5", source="x", handle="@sama", tier=1)
    new = deduper.process([item_t1])

    # Tier-1 item should win — kept as a NEW row, tier-2 should now appear in its also_appeared_in
    assert len(new) == 1
    assert new[0].source_tier == 1
    appeared = new[0].also_appeared_in
    assert any(a["source"] == "rss" for a in appeared)


def test_l2_dedup_lower_tier_arrival_merged_into_existing(db):
    deduper = Deduper(db)
    # First arrival: tier 1
    item_t1 = _make_item("https://a.com/x", "OpenAI Releases GPT-5!", source="x", handle="@sama", tier=1)
    new1 = deduper.process([item_t1])
    assert len(new1) == 1

    # Second arrival: tier 2, different URL same title — should NOT yield a new item
    item_t2 = _make_item("https://b.com/y", "OPENAI Releases gpt 5!!", source="rss", handle="vb", tier=2)
    new2 = deduper.process([item_t2])
    assert len(new2) == 0
```

- [ ] **Step 2: Run tests — verify fail**

```bash
uv run pytest tests/test_dedup.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement `dedup.py`**

`src/social_info/dedup.py`:
```python
"""Two-tier dedup: L1 by canonical URL, L2 by normalized title hash."""
import hashlib
import json
import re
import unicodedata

from social_info.db import Database
from social_info.fetchers.base import Item

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """NFKC + lowercase + strip Unicode punctuation + collapse whitespace."""
    t = unicodedata.normalize("NFKC", title)
    t = t.lower()
    # Replace any char that is neither word nor whitespace
    t = _PUNCT_RE.sub(" ", t)
    t = _WS_RE.sub(" ", t).strip()
    return t


def compute_item_id(canonical_url: str) -> str:
    return hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()


def compute_title_hash(title: str) -> str:
    return hashlib.sha1(normalize_title(title).encode("utf-8")).hexdigest()


class Deduper:
    """Filters incoming Items against the items table.

    Returns the list of Items that should be persisted as NEW rows.
    For L2 collisions: if incoming is higher tier (lower number) than the
    stored one, incoming wins and the existing record's source is merged
    into the new item's also_appeared_in. If incoming is lower tier or
    equal, it gets merged into the existing row's also_appeared_in instead.
    """

    def __init__(self, db: Database):
        self.db = db

    def process(self, items: list[Item]) -> list[Item]:
        new_items: list[Item] = []
        seen_ids_in_batch: set[str] = set()
        seen_title_hashes_in_batch: dict[str, Item] = {}

        for item in items:
            item_id = compute_item_id(item.canonical_url)
            title_hash = compute_title_hash(item.title)

            # L1 — within batch
            if item_id in seen_ids_in_batch:
                continue
            # L1 — against db
            if self.db.has_item_id(item_id):
                continue

            # L2 — against db
            existing = self.db.find_by_title_hash(title_hash)
            if existing is not None:
                if item.source_tier < existing["source_tier"]:
                    # incoming wins; existing's source goes into also_appeared_in
                    item.also_appeared_in.append({
                        "source": existing["source"],
                        "source_handle": existing["source_handle"] or "",
                        "url": existing["url"],
                    })
                    new_items.append(item)
                    seen_ids_in_batch.add(item_id)
                    seen_title_hashes_in_batch[title_hash] = item
                else:
                    # merge incoming into existing row's also_appeared_in
                    appeared = json.loads(existing["also_appeared_in"] or "[]")
                    appeared.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                    self.db.update_also_appeared_in(existing["id"], json.dumps(appeared))
                continue

            # L2 — within batch
            if title_hash in seen_title_hashes_in_batch:
                prior = seen_title_hashes_in_batch[title_hash]
                if item.source_tier < prior.source_tier:
                    # incoming wins, swap them
                    new_items.remove(prior)
                    item.also_appeared_in.append({
                        "source": prior.source,
                        "source_handle": prior.source_handle,
                        "url": prior.url,
                    })
                    new_items.append(item)
                    seen_title_hashes_in_batch[title_hash] = item
                    seen_ids_in_batch.add(item_id)
                else:
                    prior.also_appeared_in.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                continue

            new_items.append(item)
            seen_ids_in_batch.add(item_id)
            seen_title_hashes_in_batch[title_hash] = item

        return new_items
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/test_dedup.py -v
```

Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/social_info/dedup.py tests/test_dedup.py
git commit -m "feat: add L1 (URL) and L2 (title hash) dedup with tier preference"
```

---

## Phase B: Output

### Task B5: Markdown Rendering

**Files:**
- Create: `src/social_info/markdown.py`
- Create: `tests/test_markdown.py`

- [ ] **Step 1: Write failing tests**

`tests/test_markdown.py`:
```python
from datetime import datetime

from social_info.fetchers.base import Item, FetchResult
from social_info.markdown import render_item, render_file


def _item(**overrides):
    base = dict(
        title="Cursor Composer ships parallel edits",
        url="https://example.com/post",
        canonical_url="https://example.com/post",
        source="x",
        source_handle="@karpathy",
        source_tier=1,
        posted_at=datetime(2026, 4, 26, 14, 23, 0),
        fetched_at=datetime(2026, 4, 26, 9, 0, 0),
        author="Andrej Karpathy",
        excerpt="A nice take on parallel composer edits...",
        language="en",
        engagement={"likes": 1234, "comments": 84},
    )
    base.update(overrides)
    return Item(**base)


def test_render_item_basic():
    out = render_item(_item())
    assert "[Cursor Composer ships parallel edits](https://example.com/post)" in out
    assert "x:@karpathy" in out
    assert "T1" in out
    assert "♥ 1234" in out
    assert "💬 84" in out
    assert "> A nice take on parallel composer edits..." in out
    assert out.endswith("---\n")


def test_render_item_no_engagement():
    out = render_item(_item(engagement={}))
    # no ♥ symbol when no likes
    assert "♥" not in out


def test_render_item_with_also_appeared_in():
    item = _item()
    item.also_appeared_in = [
        {"source": "rss", "source_handle": "techcrunch_ai", "url": "https://tc.com/x"},
        {"source": "hn", "source_handle": "front_page", "url": "https://news.yc.com/i?id=123"},
    ]
    out = render_item(item)
    assert "also seen at" in out.lower() or "also_appeared_in" in out.lower() or "also:" in out.lower()
    assert "techcrunch_ai" in out
    assert "front_page" in out


def test_render_file_groups_by_platform():
    items = [
        _item(source="x", source_handle="@karpathy"),
        _item(source="reddit", source_handle="r/LocalLLaMA",
              url="https://r.com/a", canonical_url="https://r.com/a"),
        _item(source="hn", source_handle="front_page",
              url="https://news.yc.com/x", canonical_url="https://news.yc.com/x"),
    ]
    failures = []
    out = render_file(
        date="2026-04-26",
        generated_at=datetime(2026, 4, 26, 9, 0, 0),
        items=items,
        failures=failures,
    )
    assert "# AI Daily Digest — 2026-04-26" in out
    assert "## X / Twitter" in out
    assert "## Reddit" in out
    assert "## Hacker News" in out
    assert "total_items: 3" in out


def test_render_file_with_failures():
    out = render_file(
        date="2026-04-26",
        generated_at=datetime(2026, 4, 26, 9, 0, 0),
        items=[],
        failures=[
            FetchResult(source_id="dcard_engineer", ok=False, error="timeout after 30s"),
        ],
    )
    assert "sources_failed: 1" in out
    assert "dcard_engineer" in out
    assert "timeout after 30s" in out
```

- [ ] **Step 2: Run tests — verify fail**

```bash
uv run pytest tests/test_markdown.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement `markdown.py`**

`src/social_info/markdown.py`:
```python
"""Markdown rendering for daily digest files."""
from datetime import datetime
from collections import defaultdict

from social_info.fetchers.base import Item, FetchResult


PLATFORM_GROUP_ORDER = [
    ("x", "X / Twitter"),
    ("threads", "Threads"),
    ("reddit", "Reddit"),
    ("hn", "Hacker News"),
    ("github_trending", "GitHub Trending"),
    ("product_hunt", "Product Hunt"),
    ("huggingface", "HuggingFace"),
    ("rss_lab", "Lab Blogs & Releases"),
    ("rss_media", "English Tech Media"),
    ("rsshub_zh_cn", "中文 / 微信 + 知乎 + 微博"),
    ("wewe_rss", "中文 / 微信公眾號"),
    ("rss_zh_tw", "中文 / 台灣"),
    ("rsshub_zh_tw", "中文 / 台灣 RSSHub"),
]


def _group_key_for_source(source: str, source_handle: str) -> str:
    """Bucket a (source, handle) pair into one of the platform groups."""
    if source == "rss":
        if any(k in source_handle for k in ("anthropic", "openai", "google", "mistral", "xai")):
            return "rss_lab"
        if any(k in source_handle for k in ("ithome", "inside")):
            return "rss_zh_tw"
        return "rss_media"
    if source == "rsshub":
        if any(k in source_handle for k in ("zhihu", "weibo")):
            return "rsshub_zh_cn"
        if "dcard" in source_handle:
            return "rsshub_zh_tw"
        return "rsshub_zh_cn"
    return source


def render_item(item: Item) -> str:
    lines = []
    lines.append(f"### [{item.title}]({item.url})")
    lines.append("")

    meta_parts = [
        f"`{item.source}:{item.source_handle}`",
        f"T{item.source_tier}",
        item.posted_at.strftime("%Y-%m-%d %H:%M UTC"),
        item.language,
    ]
    if item.author and item.author != item.source_handle.lstrip("@"):
        meta_parts.append(f"by {item.author}")
    if item.engagement.get("likes"):
        meta_parts.append(f"♥ {item.engagement['likes']}")
    if item.engagement.get("comments"):
        meta_parts.append(f"💬 {item.engagement['comments']}")
    if item.engagement.get("score") and item.source != "x":
        meta_parts.append(f"▲ {item.engagement['score']}")
    lines.append(" · ".join(meta_parts))
    lines.append("")

    if item.excerpt:
        excerpt = item.excerpt.replace("\n", " ").strip()
        lines.append(f"> {excerpt}")
        lines.append("")

    if item.also_appeared_in:
        seen = "; ".join(
            f"{a['source']}:{a['source_handle']}" for a in item.also_appeared_in
        )
        lines.append(f"_also seen at: {seen}_")
        lines.append("")

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def render_file(
    date: str,
    generated_at: datetime,
    items: list[Item],
    failures: list[FetchResult],
) -> str:
    grouped: dict[str, list[Item]] = defaultdict(list)
    for it in items:
        grouped[_group_key_for_source(it.source, it.source_handle)].append(it)

    for k in grouped:
        grouped[k].sort(key=lambda x: (x.source_tier, -sum(x.engagement.values())))

    sources_active = len({(i.source, i.source_handle) for i in items})

    lines = [
        f"# AI Daily Digest — {date}",
        "",
        f"> generated_at: {generated_at.isoformat()} (Asia/Taipei)",
        f"> total_items: {len(items)}  |  sources_active: {sources_active}  |  sources_failed: {len(failures)}",
    ]
    if failures:
        lines.append("> failures:")
        for f in failures:
            lines.append(f">   - {f.source_id}: {f.error}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for key, label in PLATFORM_GROUP_ORDER:
        bucket = grouped.get(key, [])
        if not bucket:
            continue
        lines.append(f"## {label} ({len(bucket)} items)")
        lines.append("")
        for it in bucket:
            lines.append(render_item(it))

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/test_markdown.py -v
```

Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/social_info/markdown.py tests/test_markdown.py
git commit -m "feat: render Item to markdown, group by platform with failures header"
```

---

### Task B6: Config Loader & sources.yml Sample

**Files:**
- Create: `src/social_info/config.py`
- Create: `sources.yml`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write failing tests**

`tests/test_config.py`:
```python
import tempfile
from pathlib import Path

from social_info.config import load_config, SourceConfig


SAMPLE_YML = """
defaults:
  language_default: en
  excerpt_max_chars: 200
  fetch_timeout_seconds: 30

sources:
  - id: hn
    type: hn_algolia
    enabled: true
    tier: 1
    keywords: [LLM, AI]
    limit: 30

  - id: reddit_localllama
    type: reddit
    enabled: true
    tier: 1
    subreddit: LocalLLaMA
    time_window: day
    limit: 10

  - id: wechat_qbitai
    type: wewe_rss
    enabled: false
    tier: 1
    account_id: qbitai
    language: zh-CN
"""


def test_load_returns_sourceconfig_objects():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sources.yml"
        p.write_text(SAMPLE_YML)
        cfg = load_config(p)

    assert cfg.defaults["fetch_timeout_seconds"] == 30
    assert len(cfg.sources) == 3

    s0 = cfg.sources[0]
    assert isinstance(s0, SourceConfig)
    assert s0.id == "hn"
    assert s0.type == "hn_algolia"
    assert s0.enabled is True
    assert s0.tier == 1
    assert s0.params["keywords"] == ["LLM", "AI"]


def test_enabled_sources_only_returns_enabled():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "sources.yml"
        p.write_text(SAMPLE_YML)
        cfg = load_config(p)

    enabled = cfg.enabled_sources()
    assert len(enabled) == 2
    assert all(s.enabled for s in enabled)
    assert "wechat_qbitai" not in {s.id for s in enabled}
```

- [ ] **Step 2: Run tests — verify fail**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 3: Implement `config.py`**

`src/social_info/config.py`:
```python
"""sources.yml loader."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SourceConfig:
    id: str
    type: str
    enabled: bool
    tier: int
    language: str = "en"
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Config:
    defaults: dict[str, Any]
    sources: list[SourceConfig]

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources if s.enabled]


_TOP_LEVEL_KEYS = {"id", "type", "enabled", "tier", "language"}


def load_config(path: Path) -> Config:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    defaults = raw.get("defaults", {})
    raw_sources = raw.get("sources", [])

    sources: list[SourceConfig] = []
    for entry in raw_sources:
        if "id" not in entry or "type" not in entry:
            raise ValueError(f"source entry missing id/type: {entry!r}")
        params = {k: v for k, v in entry.items() if k not in _TOP_LEVEL_KEYS}
        sources.append(SourceConfig(
            id=entry["id"],
            type=entry["type"],
            enabled=entry.get("enabled", True),
            tier=entry.get("tier", 2),
            language=entry.get("language", defaults.get("language_default", "en")),
            params=params,
        ))

    return Config(defaults=defaults, sources=sources)
```

- [ ] **Step 4: Run tests — verify pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 2 passed

- [ ] **Step 5: Create real `sources.yml`**

Copy verbatim from spec Appendix A. Save to `sources.yml` at repo root.

- [ ] **Step 6: Verify `sources.yml` parses**

```bash
uv run python -c "from social_info.config import load_config; from pathlib import Path; cfg = load_config(Path('sources.yml')); print(f'sources: {len(cfg.sources)}, enabled: {len(cfg.enabled_sources())}')"
```

Expected: prints something like `sources: 25, enabled: 18`

- [ ] **Step 7: Commit**

```bash
git add src/social_info/config.py tests/test_config.py sources.yml
git commit -m "feat: load sources.yml with SourceConfig and add full source list"
```

---

## Phase C: Fetchers

> **Pattern shared by all fetchers:**
> - Each fetcher exposes an `async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]`
> - Tests mock httpx via `pytest-httpx`'s `httpx_mock` fixture
> - Fixtures (real-API responses) saved as JSON / XML / HTML in `tests/fixtures/`
> - Failures (timeout / 4xx / malformed) are caught at the orchestrator level, not in the fetcher

### Task C7: HN Fetcher

**Files:**
- Create: `src/social_info/fetchers/hn.py`
- Create: `tests/fetchers/__init__.py`
- Create: `tests/fetchers/test_hn.py`
- Create: `tests/fixtures/hn_response.json`

- [ ] **Step 1: Capture real HN Algolia response**

```bash
curl 'https://hn.algolia.com/api/v1/search_by_date?tags=story&numericFilters=created_at_i>0&hitsPerPage=3' > tests/fixtures/hn_response.json
```

(Or: write a small fixture by hand based on https://hn.algolia.com/api docs — the structure is `{"hits": [{"objectID", "title", "url", "author", "points", "num_comments", "created_at"}, ...]}`.)

- [ ] **Step 2: Write failing test**

`tests/fetchers/__init__.py`: empty

`tests/fetchers/test_hn.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.hn import fetch


@pytest.mark.asyncio
async def test_fetch_hn_parses_response(httpx_mock):
    fixture = json.loads((Path("tests/fixtures/hn_response.json")).read_text())
    httpx_mock.add_response(
        url__startswith="https://hn.algolia.com/api/v1/search_by_date",
        json=fixture,
    )

    cfg = SourceConfig(
        id="hn", type="hn_algolia", enabled=True, tier=1,
        params={"keywords": ["AI", "LLM"], "limit": 30},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0
    item = items[0]
    assert item.source == "hn"
    assert item.source_handle == "front_page"
    assert item.source_tier == 1
    assert item.url
    assert item.title
    assert "score" in item.engagement


@pytest.mark.asyncio
async def test_fetch_hn_filters_by_keyword(httpx_mock):
    fixture = {
        "hits": [
            {"objectID": "1", "title": "AI breakthrough in agents",
             "url": "https://example.com/a", "author": "u1", "points": 100,
             "num_comments": 10, "created_at": "2026-04-26T08:00:00.000Z"},
            {"objectID": "2", "title": "Cooking pasta the Italian way",
             "url": "https://example.com/b", "author": "u2", "points": 50,
             "num_comments": 5, "created_at": "2026-04-26T08:30:00.000Z"},
        ]
    }
    httpx_mock.add_response(
        url__startswith="https://hn.algolia.com/api/v1/search_by_date",
        json=fixture,
    )

    cfg = SourceConfig(
        id="hn", type="hn_algolia", enabled=True, tier=1,
        params={"keywords": ["AI"], "limit": 30},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    assert "AI" in items[0].title
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_hn.py -v
```

Expected: FAIL `ModuleNotFoundError`

- [ ] **Step 4: Implement `fetchers/hn.py`**

`src/social_info/fetchers/hn.py`:
```python
"""Hacker News fetcher via Algolia API."""
from datetime import datetime, timedelta
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://hn.algolia.com/api/v1/search_by_date"


def _matches_any_keyword(title: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = title.lower()
    return any(k.lower() in lower for k in keywords)


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    keywords = source.params.get("keywords", [])
    limit = source.params.get("limit", 30)
    since_ts = int((datetime.utcnow() - timedelta(hours=24)).timestamp())

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
    now = datetime.utcnow()
    for hit in data.get("hits", []):
        title = hit.get("title") or ""
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title or not url:
            continue
        if not _matches_any_keyword(title, keywords):
            continue
        try:
            posted_at = datetime.fromisoformat(hit["created_at"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
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
        ))
    return items
```

- [ ] **Step 5: Run tests — verify pass**

```bash
uv run pytest tests/fetchers/test_hn.py -v
```

Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/hn.py tests/fetchers/__init__.py tests/fetchers/test_hn.py tests/fixtures/hn_response.json
git commit -m "feat: add HN Algolia fetcher with keyword OR filter"
```

---

### Task C8: Reddit Fetcher

**Files:**
- Create: `src/social_info/fetchers/reddit.py`
- Create: `tests/fetchers/test_reddit.py`
- Create: `tests/fixtures/reddit_response.json`

- [ ] **Step 1: Capture fixture**

```bash
curl -A 'social-info/0.1 (test fixture capture)' 'https://www.reddit.com/r/LocalLLaMA/top.json?t=day&limit=3' > tests/fixtures/reddit_response.json
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_reddit.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.reddit import fetch


@pytest.mark.asyncio
async def test_fetch_reddit_parses_top_json(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/reddit_response.json").read_text())
    httpx_mock.add_response(
        url__startswith="https://www.reddit.com/r/LocalLLaMA/top.json",
        json=fixture,
    )

    cfg = SourceConfig(
        id="reddit_localllama", type="reddit", enabled=True, tier=1,
        params={"subreddit": "LocalLLaMA", "time_window": "day", "limit": 10},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0
    item = items[0]
    assert item.source == "reddit"
    assert item.source_handle == "r/LocalLLaMA"
    assert item.source_tier == 1
    assert item.url
    assert "score" in item.engagement
    assert "comments" in item.engagement
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_reddit.py -v
```

- [ ] **Step 4: Implement `fetchers/reddit.py`**

```python
"""Reddit fetcher via public top.json endpoint."""
from datetime import datetime
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

USER_AGENT = "social-info/0.1 (daily AI raw aggregator; personal use)"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    subreddit = source.params["subreddit"]
    time_window = source.params.get("time_window", "day")
    limit = source.params.get("limit", 10)

    url = f"https://www.reddit.com/r/{subreddit}/top.json"
    resp = await http.get(
        url,
        params={"t": time_window, "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = datetime.utcnow()
    for child in data.get("data", {}).get("children", []):
        post = child.get("data", {})
        title = post.get("title") or ""
        link = post.get("url") or f"https://www.reddit.com{post.get('permalink', '')}"
        if not title or not link:
            continue
        # Skip media posts that have no real outbound article (use permalink instead)
        if post.get("is_self") or post.get("post_hint") == "image":
            link = f"https://www.reddit.com{post.get('permalink', '')}"
        excerpt = (post.get("selftext") or "").strip()[:200]
        try:
            posted_at = datetime.utcfromtimestamp(post.get("created_utc", 0))
        except (TypeError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="reddit",
            source_handle=f"r/{subreddit}",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=post.get("author") or "",
            excerpt=excerpt,
            language="en",
            engagement={
                "score": int(post.get("score") or 0),
                "comments": int(post.get("num_comments") or 0),
            },
        ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_reddit.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/reddit.py tests/fetchers/test_reddit.py tests/fixtures/reddit_response.json
git commit -m "feat: add Reddit fetcher via public top.json"
```

---

### Task C9: GitHub Trending Fetcher

**Files:**
- Create: `src/social_info/fetchers/github_trending.py`
- Create: `tests/fetchers/test_github_trending.py`
- Create: `tests/fixtures/github_trending.html`

- [ ] **Step 1: Capture HTML fixture**

```bash
curl 'https://github.com/trending/python?since=daily' > tests/fixtures/github_trending.html
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_github_trending.py`:
```python
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.github_trending import fetch


@pytest.mark.asyncio
async def test_fetch_github_trending_parses_html(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url__startswith="https://github.com/trending/python",
        text=html,
    )

    cfg = SourceConfig(
        id="github_trending", type="github_trending", enabled=True, tier=1,
        params={"languages": ["python"], "since": "daily", "ai_keywords": ["ai", "llm", "agent"]},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    # AI-related repos in the trending page should appear
    assert all(it.source == "github_trending" for it in items)
    if items:
        assert "github.com" in items[0].url
        assert "stars" in items[0].engagement
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_github_trending.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/github_trending.py`:
```python
"""GitHub Trending HTML scraper."""
from datetime import datetime
import httpx
from bs4 import BeautifulSoup

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


def _matches_ai(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    lower = text.lower()
    return any(k.lower() in lower for k in keywords)


def _parse_stars(text: str) -> int:
    text = (text or "").strip().replace(",", "").replace("k", "000").replace(".", "")
    try:
        return int(text)
    except ValueError:
        return 0


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    languages = source.params.get("languages", [""])  # [""] = no language filter
    since = source.params.get("since", "daily")
    keywords = source.params.get("ai_keywords", [])

    items: list[Item] = []
    now = datetime.utcnow()
    for lang in languages:
        url = f"https://github.com/trending/{lang}".rstrip("/") + f"?since={since}"
        resp = await http.get(url, timeout=30.0)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for repo in soup.select("article.Box-row"):
            link_el = repo.select_one("h2 a")
            if not link_el:
                continue
            slug = link_el.get("href", "").strip("/")
            if not slug:
                continue
            full_url = f"https://github.com/{slug}"
            title = slug
            description_el = repo.select_one("p")
            description = (description_el.text.strip() if description_el else "")
            if not _matches_ai(f"{title} {description}", keywords):
                continue
            stars_el = repo.select_one('a[href$="/stargazers"]')
            stars = _parse_stars(stars_el.text if stars_el else "")
            items.append(Item(
                title=title,
                url=full_url,
                canonical_url=canonical_url(full_url),
                source="github_trending",
                source_handle=f"trending:{lang or 'all'}",
                source_tier=source.tier,
                posted_at=now,  # trending has no per-repo timestamp; use fetch time
                fetched_at=now,
                author=slug.split("/")[0],
                excerpt=description[:200],
                language="en",
                engagement={"stars": stars},
            ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_github_trending.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/github_trending.py tests/fetchers/test_github_trending.py tests/fixtures/github_trending.html
git commit -m "feat: add GitHub Trending fetcher with AI keyword filter"
```

---

### Task C10: Product Hunt Fetcher

**Files:**
- Create: `src/social_info/fetchers/product_hunt.py`
- Create: `tests/fetchers/test_product_hunt.py`
- Create: `tests/fixtures/product_hunt_response.json`

- [ ] **Step 1: Build minimal fixture by hand**

`tests/fixtures/product_hunt_response.json`:
```json
{
  "data": {
    "posts": {
      "edges": [
        {
          "node": {
            "id": "1",
            "name": "ClaudeCraft",
            "tagline": "Build agents in minutes",
            "url": "https://www.producthunt.com/posts/claudecraft",
            "votesCount": 234,
            "commentsCount": 12,
            "createdAt": "2026-04-25T18:00:00Z",
            "user": {"name": "Alice"},
            "topics": {"nodes": [{"name": "Artificial Intelligence"}]}
          }
        }
      ]
    }
  }
}
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_product_hunt.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.product_hunt import fetch


@pytest.mark.asyncio
async def test_fetch_product_hunt(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/product_hunt_response.json").read_text())
    httpx_mock.add_response(
        url__startswith="https://api.producthunt.com",
        json=fixture,
    )

    cfg = SourceConfig(
        id="product_hunt", type="product_hunt", enabled=True, tier=2,
        params={"topic": "artificial-intelligence", "limit": 10},
    )

    async with httpx.AsyncClient(headers={"Authorization": "Bearer fake"}) as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "product_hunt"
    assert "ClaudeCraft" in item.title
    assert item.engagement["votes"] == 234
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_product_hunt.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/product_hunt.py`:
```python
"""Product Hunt GraphQL fetcher.

Note: this fetcher requires a Bearer token via httpx.AsyncClient headers,
provided by the orchestrator from PRODUCT_HUNT_TOKEN env var.
"""
from datetime import datetime
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_URL = "https://api.producthunt.com/v2/api/graphql"

QUERY = """
query DailyAITopProducts($topic: String!, $limit: Int!) {
  posts(topic: $topic, first: $limit, order: VOTES) {
    edges {
      node {
        id
        name
        tagline
        url
        votesCount
        commentsCount
        createdAt
        user { name }
      }
    }
  }
}
"""


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    topic = source.params.get("topic", "artificial-intelligence")
    limit = source.params.get("limit", 10)
    resp = await http.post(
        API_URL,
        json={"query": QUERY, "variables": {"topic": topic, "limit": limit}},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    edges = data.get("data", {}).get("posts", {}).get("edges", [])

    items: list[Item] = []
    now = datetime.utcnow()
    for edge in edges:
        n = edge.get("node", {})
        if not n:
            continue
        title = f"{n['name']} — {n.get('tagline', '')}".strip(" —")
        url = n["url"]
        try:
            posted_at = datetime.fromisoformat(n["createdAt"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=url,
            canonical_url=canonical_url(url),
            source="product_hunt",
            source_handle="daily_top_ai",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=(n.get("user") or {}).get("name", ""),
            excerpt=n.get("tagline", "")[:200],
            language="en",
            engagement={
                "votes": int(n.get("votesCount") or 0),
                "comments": int(n.get("commentsCount") or 0),
            },
        ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_product_hunt.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/product_hunt.py tests/fetchers/test_product_hunt.py tests/fixtures/product_hunt_response.json
git commit -m "feat: add Product Hunt GraphQL fetcher"
```

---

### Task C11: HuggingFace Fetcher

**Files:**
- Create: `src/social_info/fetchers/huggingface.py`
- Create: `tests/fetchers/test_huggingface.py`
- Create: `tests/fixtures/hf_models.json`

- [ ] **Step 1: Capture fixture**

```bash
curl 'https://huggingface.co/api/models?sort=likes7d&direction=-1&limit=3' > tests/fixtures/hf_models.json
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_huggingface.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.huggingface import fetch


@pytest.mark.asyncio
async def test_fetch_hf_models(httpx_mock):
    fixture = json.loads(Path("tests/fixtures/hf_models.json").read_text())
    httpx_mock.add_response(
        url__startswith="https://huggingface.co/api/models",
        json=fixture,
    )

    cfg = SourceConfig(
        id="huggingface_models", type="huggingface", enabled=True, tier=2,
        params={"category": "models", "limit": 10},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert all(it.source == "huggingface" for it in items)
    assert all("huggingface.co" in it.url for it in items)
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_huggingface.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/huggingface.py`:
```python
"""HuggingFace Hub trending models / spaces fetcher."""
from datetime import datetime
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    category = source.params.get("category", "models")  # "models" or "spaces"
    limit = source.params.get("limit", 10)
    url = f"https://huggingface.co/api/{category}"
    resp = await http.get(
        url,
        params={"sort": "likes7d", "direction": -1, "limit": limit},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()

    items: list[Item] = []
    now = datetime.utcnow()
    for entry in data:
        repo_id = entry.get("id") or entry.get("modelId") or ""
        if not repo_id:
            continue
        full_url = f"https://huggingface.co/{repo_id}"
        title = repo_id
        try:
            posted_at = datetime.fromisoformat(entry["lastModified"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            posted_at = now
        items.append(Item(
            title=title,
            url=full_url,
            canonical_url=canonical_url(full_url),
            source="huggingface",
            source_handle=f"trending:{category}",
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=repo_id.split("/")[0] if "/" in repo_id else "",
            excerpt=entry.get("pipeline_tag", "") or "",
            language="en",
            engagement={
                "likes": int(entry.get("likes") or 0),
                "downloads": int(entry.get("downloads") or 0),
            },
        ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_huggingface.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/huggingface.py tests/fetchers/test_huggingface.py tests/fixtures/hf_models.json
git commit -m "feat: add HuggingFace trending models/spaces fetcher"
```

---

### Task C12: Generic RSS Fetcher

**Files:**
- Create: `src/social_info/fetchers/rss.py`
- Create: `tests/fetchers/test_rss.py`
- Create: `tests/fixtures/sample_rss.xml`

- [ ] **Step 1: Build a minimal RSS fixture**

`tests/fixtures/sample_rss.xml`:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Anthropic News</title>
  <item>
    <title>Claude 4.7 Opus released</title>
    <link>https://www.anthropic.com/news/claude-4-7-opus</link>
    <description>Today we are releasing Claude 4.7 Opus.</description>
    <pubDate>Fri, 25 Apr 2026 18:00:00 GMT</pubDate>
    <author>Anthropic</author>
  </item>
  <item>
    <title>New API features</title>
    <link>https://www.anthropic.com/news/api-features</link>
    <description>API additions include batch and caching.</description>
    <pubDate>Thu, 24 Apr 2026 12:00:00 GMT</pubDate>
    <author>Anthropic</author>
  </item>
</channel></rss>
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_rss.py`:
```python
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.rss import fetch


@pytest.mark.asyncio
async def test_fetch_rss_basic(httpx_mock):
    xml = Path("tests/fixtures/sample_rss.xml").read_text()
    httpx_mock.add_response(
        url__startswith="https://www.anthropic.com/news.rss",
        text=xml,
    )

    cfg = SourceConfig(
        id="anthropic_blog", type="rss", enabled=True, tier=1,
        language="en",
        params={"url": "https://www.anthropic.com/news.rss"},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    item = items[0]
    assert item.source == "rss"
    assert item.source_handle == "anthropic_blog"
    assert item.title == "Claude 4.7 Opus released"
    assert item.excerpt
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_rss.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/rss.py`:
```python
"""Generic RSS / Atom fetcher (lab blogs, tech media, Taiwan media)."""
from datetime import datetime
from time import mktime

import feedparser
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    url = source.params["url"]
    limit = source.params.get("limit", 30)
    resp = await http.get(url, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = datetime.utcnow()
    for entry in parsed.entries[:limit]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if entry.get("published_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.published_parsed))
        elif entry.get("updated_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.updated_parsed))
        else:
            posted_at = now
        excerpt = (entry.get("summary") or entry.get("description") or "").strip()
        # crude HTML strip: remove tags, collapse whitespace
        import re as _re
        excerpt = _re.sub(r"<[^>]+>", "", excerpt)
        excerpt = _re.sub(r"\s+", " ", excerpt).strip()[:200]

        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="rss",
            source_handle=source.id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
        ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_rss.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/rss.py tests/fetchers/test_rss.py tests/fixtures/sample_rss.xml
git commit -m "feat: add generic RSS / Atom fetcher with HTML strip on excerpt"
```

---

### Task C13: RSSHub Fetcher

**Files:**
- Create: `src/social_info/fetchers/rsshub.py`
- Create: `tests/fetchers/test_rsshub.py`

- [ ] **Step 1: Write test (reuses RSS parsing under the hood)**

`tests/fetchers/test_rsshub.py`:
```python
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.rsshub import fetch


@pytest.mark.asyncio
async def test_fetch_rsshub_path_appended_to_instance(httpx_mock, monkeypatch):
    monkeypatch.setenv("RSSHUB_INSTANCE_URL", "https://rsshub.app")
    xml = Path("tests/fixtures/sample_rss.xml").read_text()
    httpx_mock.add_response(
        url__startswith="https://rsshub.app/zhihu/hot",
        text=xml,
    )

    cfg = SourceConfig(
        id="zhihu_hot", type="rsshub", enabled=True, tier=1,
        language="zh-CN",
        params={"path": "/zhihu/hot"},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 2
    assert items[0].source == "rsshub"
    assert items[0].source_handle == "zhihu_hot"
    assert items[0].language == "zh-CN"
```

- [ ] **Step 2: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_rsshub.py -v
```

- [ ] **Step 3: Implement**

`src/social_info/fetchers/rsshub.py`:
```python
"""RSSHub fetcher: combine instance URL + path, parse as RSS."""
import os
from datetime import datetime
from time import mktime

import feedparser
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


def _instance() -> str:
    return os.environ.get("RSSHUB_INSTANCE_URL", "https://rsshub.app").rstrip("/")


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    path = source.params["path"]
    if not path.startswith("/"):
        path = "/" + path
    url = _instance() + path
    limit = source.params.get("limit", 30)

    resp = await http.get(url, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = datetime.utcnow()
    for entry in parsed.entries[:limit]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if entry.get("published_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.published_parsed))
        else:
            posted_at = now
        import re as _re
        excerpt = (entry.get("summary") or entry.get("description") or "")
        excerpt = _re.sub(r"<[^>]+>", "", excerpt)
        excerpt = _re.sub(r"\s+", " ", excerpt).strip()[:200]
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="rsshub",
            source_handle=source.id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
        ))
    return items
```

- [ ] **Step 4: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_rsshub.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/social_info/fetchers/rsshub.py tests/fetchers/test_rsshub.py
git commit -m "feat: add RSSHub fetcher with configurable instance URL"
```

---

### Task C14: Twitter Fetcher (twitterapi.io)

**Files:**
- Create: `src/social_info/fetchers/twitter.py`
- Create: `tests/fetchers/test_twitter.py`
- Create: `tests/fixtures/twitterapi_user_tweets.json`

- [ ] **Step 1: Build minimal fixture by hand**

Reference docs: https://docs.twitterapi.io/api-reference/endpoint/get-user-tweets

`tests/fixtures/twitterapi_user_tweets.json`:
```json
{
  "tweets": [
    {
      "id": "12345",
      "text": "Spec-driven development beats vibe coding for serious projects.",
      "createdAt": "2026-04-26T08:00:00.000Z",
      "url": "https://twitter.com/karpathy/status/12345",
      "author": {"userName": "karpathy", "name": "Andrej Karpathy"},
      "likeCount": 1234,
      "replyCount": 89,
      "retweetCount": 200
    }
  ]
}
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_twitter.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.twitter import fetch


@pytest.mark.asyncio
async def test_fetch_twitter_per_handle(httpx_mock, monkeypatch):
    monkeypatch.setenv("TWITTERAPI_IO_KEY", "fake-key")
    fixture = json.loads(Path("tests/fixtures/twitterapi_user_tweets.json").read_text())
    httpx_mock.add_response(
        url__startswith="https://api.twitterapi.io",
        json=fixture,
    )

    cfg = SourceConfig(
        id="twitter_tier1", type="twitter", enabled=True, tier=1,
        params={"handles": ["karpathy"], "per_handle_limit": 10, "time_window_hours": 24},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "x"
    assert item.source_handle == "@karpathy"
    assert item.engagement["likes"] == 1234


@pytest.mark.asyncio
async def test_fetch_twitter_no_key_raises(monkeypatch):
    monkeypatch.delenv("TWITTERAPI_IO_KEY", raising=False)
    cfg = SourceConfig(
        id="twitter_tier1", type="twitter", enabled=True, tier=1,
        params={"handles": ["karpathy"]},
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="TWITTERAPI_IO_KEY"):
            await fetch(cfg, client)
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_twitter.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/twitter.py`:
```python
"""X / Twitter fetcher via twitterapi.io."""
import os
from datetime import datetime, timedelta
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_BASE = "https://api.twitterapi.io"


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    api_key = os.environ.get("TWITTERAPI_IO_KEY")
    if not api_key:
        raise RuntimeError("TWITTERAPI_IO_KEY env var not set")

    handles = source.params.get("handles", [])
    per_handle_limit = source.params.get("per_handle_limit", 10)
    window_hours = source.params.get("time_window_hours", 24)
    since_iso = (datetime.utcnow() - timedelta(hours=window_hours)).isoformat() + "Z"

    items: list[Item] = []
    now = datetime.utcnow()
    headers = {"X-API-Key": api_key}

    for handle in handles:
        resp = await http.get(
            f"{API_BASE}/twitter/user/last_tweets",
            params={"userName": handle, "limit": per_handle_limit, "sinceTime": since_iso},
            headers=headers,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        for tw in data.get("tweets", []):
            text = tw.get("text", "").strip()
            url = tw.get("url") or f"https://twitter.com/{handle}/status/{tw['id']}"
            try:
                posted_at = datetime.fromisoformat(tw["createdAt"].replace("Z", "+00:00")).replace(tzinfo=None)
            except (KeyError, ValueError):
                posted_at = now
            items.append(Item(
                title=text[:120] + ("…" if len(text) > 120 else ""),
                url=url,
                canonical_url=canonical_url(url),
                source="x",
                source_handle=f"@{handle}",
                source_tier=source.tier,
                posted_at=posted_at,
                fetched_at=now,
                author=(tw.get("author") or {}).get("name", "") or handle,
                excerpt=text[:200],
                language="en",
                engagement={
                    "likes": int(tw.get("likeCount") or 0),
                    "comments": int(tw.get("replyCount") or 0),
                    "retweets": int(tw.get("retweetCount") or 0),
                },
            ))
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_twitter.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/twitter.py tests/fetchers/test_twitter.py tests/fixtures/twitterapi_user_tweets.json
git commit -m "feat: add Twitter fetcher via twitterapi.io with per-handle iteration"
```

---

### Task C15: Threads Fetcher (Meta API)

**Files:**
- Create: `src/social_info/fetchers/threads.py`
- Create: `tests/fetchers/test_threads.py`
- Create: `tests/fixtures/threads_keyword_search.json`

- [ ] **Step 1: Build minimal fixture**

`tests/fixtures/threads_keyword_search.json`:
```json
{
  "data": [
    {
      "id": "100001",
      "text": "Cursor Composer parallel edits saved me an hour today.",
      "permalink": "https://www.threads.net/@taiwan_dev/post/100001",
      "username": "taiwan_dev",
      "timestamp": "2026-04-26T05:00:00+0000",
      "like_count": 88,
      "replies_count": 12
    }
  ]
}
```

- [ ] **Step 2: Write test**

`tests/fetchers/test_threads.py`:
```python
import json
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.threads import fetch


@pytest.mark.asyncio
async def test_fetch_threads_keyword_mode(httpx_mock, monkeypatch):
    monkeypatch.setenv("THREADS_ACCESS_TOKEN", "fake-token")
    fixture = json.loads(Path("tests/fixtures/threads_keyword_search.json").read_text())
    httpx_mock.add_response(
        url__startswith="https://graph.threads.net/v1.0/keyword_search",
        json=fixture,
    )

    cfg = SourceConfig(
        id="threads_keyword", type="threads", enabled=True, tier=1,
        params={
            "mode": "keyword",
            "search_type": "TOP",
            "queries": ["Cursor"],
            "per_query_limit": 5,
            "time_window_hours": 24,
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) == 1
    item = items[0]
    assert item.source == "threads"
    assert "@taiwan_dev" in item.source_handle or item.author == "taiwan_dev"
    assert item.engagement["likes"] == 88
```

- [ ] **Step 3: Run — verify fail**

```bash
uv run pytest tests/fetchers/test_threads.py -v
```

- [ ] **Step 4: Implement**

`src/social_info/fetchers/threads.py`:
```python
"""Threads (Meta) fetcher.

Three modes:
- keyword: /keyword_search with q=<term>, search_type=TOP|RECENT
- tag:     /keyword_search with q=<tag>, search_mode=TAG
- user:    /<user_id>/threads (skeleton only, disabled until handles provided)
"""
import os
from datetime import datetime, timedelta
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url

API_BASE = "https://graph.threads.net/v1.0"

_FIELDS = "id,text,permalink,username,timestamp,like_count,replies_count"


async def _search_one(
    http: httpx.AsyncClient,
    query: str,
    search_type: str,
    search_mode: str | None,
    since_iso: str,
    limit: int,
    token: str,
) -> list[dict]:
    params = {
        "q": query,
        "search_type": search_type,
        "fields": _FIELDS,
        "limit": limit,
        "since": since_iso,
        "access_token": token,
    }
    if search_mode:
        params["search_mode"] = search_mode
    resp = await http.get(f"{API_BASE}/keyword_search", params=params, timeout=30.0)
    resp.raise_for_status()
    return resp.json().get("data", [])


def _post_to_item(post: dict, source_tier: int, language: str, source_handle: str) -> Item:
    text = (post.get("text") or "").strip()
    url = post.get("permalink") or f"https://www.threads.net/post/{post.get('id')}"
    try:
        posted_at = datetime.strptime(post["timestamp"], "%Y-%m-%dT%H:%M:%S%z").replace(tzinfo=None)
    except (KeyError, ValueError):
        posted_at = datetime.utcnow()
    username = post.get("username") or ""
    return Item(
        title=text[:120] + ("…" if len(text) > 120 else ""),
        url=url,
        canonical_url=canonical_url(url),
        source="threads",
        source_handle=source_handle or (f"@{username}" if username else ""),
        source_tier=source_tier,
        posted_at=posted_at,
        fetched_at=datetime.utcnow(),
        author=username,
        excerpt=text[:200],
        language=language,
        engagement={
            "likes": int(post.get("like_count") or 0),
            "comments": int(post.get("replies_count") or 0),
        },
    )


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    token = os.environ.get("THREADS_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("THREADS_ACCESS_TOKEN env var not set")

    mode = source.params.get("mode", "keyword")
    search_type = source.params.get("search_type", "TOP")
    queries = source.params.get("queries", [])
    per_query_limit = source.params.get("per_query_limit", 5)
    window_hours = source.params.get("time_window_hours", 24)
    language = source.language or "en"
    since_iso = (datetime.utcnow() - timedelta(hours=window_hours)).strftime("%Y-%m-%dT%H:%M:%S+0000")

    if mode == "user":
        # Skeleton; real impl iterates over user_ids when provided
        return []

    search_mode = "TAG" if mode == "tag" else None

    items: list[Item] = []
    for q in queries:
        posts = await _search_one(http, q, search_type, search_mode, since_iso, per_query_limit, token)
        source_handle = f"{mode}:{q}"
        items.extend(_post_to_item(p, source.tier, language, source_handle) for p in posts)
    return items
```

- [ ] **Step 5: Run — verify pass**

```bash
uv run pytest tests/fetchers/test_threads.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/social_info/fetchers/threads.py tests/fetchers/test_threads.py tests/fixtures/threads_keyword_search.json
git commit -m "feat: add Threads fetcher with keyword/tag modes via Meta API"
```

---

### Task C16: wewe-rss Skeleton Fetcher

**Files:**
- Create: `src/social_info/fetchers/wewe_rss.py`

- [ ] **Step 1: Implement skeleton (no test — disabled by default in sources.yml)**

`src/social_info/fetchers/wewe_rss.py`:
```python
"""WeChat Official Account fetcher via self-hosted wewe-rss instance.

DISABLED BY DEFAULT in sources.yml during the first month of PoC.
Once self-host is set up:
1. Run wewe-rss docker (see spec Appendix D)
2. Set WEWE_RSS_URL and WEWE_RSS_KEY env vars
3. Flip the wechat_* sources to enabled: true in sources.yml

The wewe-rss instance exposes per-account RSS feeds at
{WEWE_RSS_URL}/feeds/{account_id}.atom (or similar) — we delegate
the actual parsing to the generic RSS fetcher path.
"""
import os
from datetime import datetime
from time import mktime

import feedparser
import httpx

from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    base = os.environ.get("WEWE_RSS_URL")
    if not base:
        raise RuntimeError("WEWE_RSS_URL env var not set (wewe-rss source enabled but not configured)")
    key = os.environ.get("WEWE_RSS_KEY", "")
    account_id = source.params["account_id"]
    limit = source.params.get("limit", 20)

    feed_url = f"{base.rstrip('/')}/feeds/{account_id}.atom"
    resp = await http.get(
        feed_url,
        params={"key": key} if key else None,
        timeout=30.0,
    )
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)

    items: list[Item] = []
    now = datetime.utcnow()
    for entry in parsed.entries[:limit]:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        if entry.get("published_parsed"):
            posted_at = datetime.fromtimestamp(mktime(entry.published_parsed))
        else:
            posted_at = now
        import re as _re
        excerpt = (entry.get("summary") or "")
        excerpt = _re.sub(r"<[^>]+>", "", excerpt)
        excerpt = _re.sub(r"\s+", " ", excerpt).strip()[:200]
        items.append(Item(
            title=title,
            url=link,
            canonical_url=canonical_url(link),
            source="wewe_rss",
            source_handle=account_id,
            source_tier=source.tier,
            posted_at=posted_at,
            fetched_at=now,
            author=entry.get("author", "") or "",
            excerpt=excerpt,
            language=source.language,
            engagement={},
        ))
    return items
```

- [ ] **Step 2: Verify import works**

```bash
uv run python -c "from social_info.fetchers import wewe_rss; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add src/social_info/fetchers/wewe_rss.py
git commit -m "feat: add wewe_rss skeleton fetcher (disabled by default in PoC)"
```

---

## Phase D: Pipeline & CLI

### Task D17: Pipeline Orchestration

**Files:**
- Create: `src/social_info/pipeline.py`

- [ ] **Step 1: Implement `pipeline.py`**

`src/social_info/pipeline.py`:
```python
"""End-to-end orchestration: load config → fetch all → dedup → render."""
import asyncio
from datetime import datetime
from pathlib import Path

import httpx

from social_info.config import Config, SourceConfig, load_config
from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id, compute_title_hash
from social_info.fetchers.base import FetchResult, Item
from social_info.markdown import render_file

# Map type → fetcher module
from social_info.fetchers import (
    hn, reddit, github_trending, product_hunt, huggingface,
    rss, rsshub, twitter, threads, wewe_rss,
)

FETCHER_REGISTRY = {
    "hn_algolia": hn.fetch,
    "reddit": reddit.fetch,
    "github_trending": github_trending.fetch,
    "product_hunt": product_hunt.fetch,
    "huggingface": huggingface.fetch,
    "rss": rss.fetch,
    "rsshub": rsshub.fetch,
    "twitter": twitter.fetch,
    "threads": threads.fetch,
    "wewe_rss": wewe_rss.fetch,
}


async def _run_one_fetcher(source: SourceConfig, http: httpx.AsyncClient) -> FetchResult:
    started = datetime.utcnow()
    fetcher = FETCHER_REGISTRY.get(source.type)
    if not fetcher:
        return FetchResult(source_id=source.id, ok=False,
                          error=f"unknown source type: {source.type}",
                          started_at=started, ended_at=datetime.utcnow())
    try:
        items = await fetcher(source, http)
        return FetchResult(source_id=source.id, items=items, ok=True,
                          started_at=started, ended_at=datetime.utcnow())
    except Exception as e:
        return FetchResult(source_id=source.id, items=[], ok=False,
                          error=f"{type(e).__name__}: {e}",
                          started_at=started, ended_at=datetime.utcnow())


async def run_pipeline(
    config: Config,
    db: Database,
    *,
    only_sources: list[str] | None = None,
    dry_run: bool = False,
    limit_per_source: int | None = None,
) -> tuple[list[Item], list[FetchResult]]:
    """Run all enabled fetchers in parallel, dedup, and return (new_items, all_results)."""
    enabled = config.enabled_sources()
    if only_sources:
        enabled = [s for s in enabled if s.id in only_sources]

    async with httpx.AsyncClient(follow_redirects=True) as http:
        results = await asyncio.gather(*[_run_one_fetcher(s, http) for s in enabled])

    # Log fetch_runs
    if not dry_run:
        for r in results:
            db.log_fetch_run(
                source=r.source_id,
                started_at=r.started_at,
                ended_at=r.ended_at or r.started_at,
                status="ok" if r.ok else "failed",
                items_fetched=r.items_count(),
                error=r.error,
            )

    all_items: list[Item] = []
    for r in results:
        if not r.ok:
            continue
        items = r.items[:limit_per_source] if limit_per_source else r.items
        all_items.extend(items)

    # Dedup
    deduper = Deduper(db)
    new_items = deduper.process(all_items)

    # Insert into items table
    if not dry_run:
        for it in new_items:
            row = it.to_db_row(
                item_id=compute_item_id(it.canonical_url),
                title_hash=compute_title_hash(it.title),
            )
            db.insert_item(row)

    return new_items, results


def write_report(
    new_items: list[Item],
    failures: list[FetchResult],
    out_dir: Path,
    date: str,
    generated_at: datetime,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md = render_file(date=date, generated_at=generated_at, items=new_items, failures=failures)
    out_path = out_dir / f"{date}.md"
    out_path.write_text(md, encoding="utf-8")
    return out_path
```

- [ ] **Step 2: Verify imports + lint**

```bash
uv run ruff check src
uv run python -c "from social_info.pipeline import run_pipeline; print('ok')"
```

- [ ] **Step 3: Commit**

```bash
git add src/social_info/pipeline.py
git commit -m "feat: orchestrate parallel fetchers with dedup and report writing"
```

---

### Task D18: CLI / `__main__`

**Files:**
- Create: `src/social_info/__main__.py`

- [ ] **Step 1: Implement CLI**

`src/social_info/__main__.py`:
```python
"""CLI entrypoint: `uv run python -m social_info [--flags]`."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

from social_info.config import load_config
from social_info.db import Database
from social_info.markdown import render_item
from social_info.pipeline import run_pipeline, write_report

TAIPEI = timezone(timedelta(hours=8))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Daily AI raw aggregator")
    p.add_argument("--config", type=Path, default=Path("sources.yml"))
    p.add_argument("--db", type=Path, default=Path("state.db"))
    p.add_argument("--reports", type=Path, default=Path("reports"))
    p.add_argument("--source", type=str, default=None,
                  help="comma-separated source ids to run (default: all enabled)")
    p.add_argument("--date", type=str, default=None,
                  help="YYYY-MM-DD (Asia/Taipei) for output filename; default = today")
    p.add_argument("--dry-run", action="store_true",
                  help="don't write db / .md")
    p.add_argument("--smoke", action="store_true",
                  help="real API + limit=3 per source + print to stdout, no write")
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    config = load_config(args.config)
    db = Database(args.db)
    db.init_schema()

    only_sources = [s.strip() for s in args.source.split(",")] if args.source else None
    limit_per_source = 3 if args.smoke else None
    dry_run = args.dry_run or args.smoke

    new_items, results = await run_pipeline(
        config, db,
        only_sources=only_sources,
        dry_run=dry_run,
        limit_per_source=limit_per_source,
    )
    failures = [r for r in results if not r.ok]

    if args.smoke:
        print(f"--- SMOKE RUN ---")
        print(f"Sources requested: {len(results)}, succeeded: {len(results) - len(failures)}")
        for it in new_items[:30]:
            print(render_item(it))
        for f in failures:
            print(f"FAILED: {f.source_id}: {f.error}")
        db.close()
        return 0

    if dry_run:
        print(f"DRY-RUN: {len(new_items)} new items, {len(failures)} failures")
        db.close()
        return 0

    now_tw = datetime.now(TAIPEI)
    date = args.date or now_tw.strftime("%Y-%m-%d")
    out = write_report(new_items, failures, args.reports, date, now_tw)
    print(f"Wrote {out} ({len(new_items)} items, {len(failures)} failures)")
    db.close()
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify CLI parses**

```bash
uv run python -m social_info --help
```

Expected: prints argparse help

- [ ] **Step 3: Verify dry-run on a tiny subset (HN only — no auth needed)**

```bash
uv run python -m social_info --source hn --dry-run
```

Expected:
- Possibly fails because the only-source flag isn't enough on its own (HN requires no env vars), or succeeds with `DRY-RUN: N new items, 0 failures`

- [ ] **Step 4: Commit**

```bash
git add src/social_info/__main__.py
git commit -m "feat: CLI with --dry-run, --smoke, --source, --date flags"
```

---

### Task D19: Health Script

**Files:**
- Create: `src/social_info/health.py`

- [ ] **Step 1: Implement health script**

`src/social_info/health.py`:
```python
"""Print a 7-day per-source success-rate report.

Run: `uv run python -m social_info.health`
"""
import argparse
from collections import defaultdict
from pathlib import Path

from social_info.db import Database


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=Path("state.db"))
    p.add_argument("--days", type=int, default=7)
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    db = Database(args.db)

    runs = db.recent_fetch_runs(days=args.days)
    by_source: dict[str, list[dict]] = defaultdict(list)
    for r in runs:
        by_source[r["source"]].append(r)

    print(f"=== Health report (last {args.days} days) ===")
    print(f"{'source':<30} {'runs':>5} {'ok':>5} {'fail':>5} {'rate':>7} {'last_error'}")
    for source, rs in sorted(by_source.items()):
        ok = sum(1 for r in rs if r["status"] == "ok")
        fail = sum(1 for r in rs if r["status"] != "ok")
        rate = ok / len(rs) * 100 if rs else 0
        last_err = next((r["error"] for r in rs if r["status"] != "ok"), "")
        print(f"{source:<30} {len(rs):>5} {ok:>5} {fail:>5} {rate:>6.1f}% {last_err[:60]}")

    db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs (empty db acceptable)**

```bash
uv run python -m social_info.health
```

Expected: prints header even if no rows

- [ ] **Step 3: Commit**

```bash
git add src/social_info/health.py
git commit -m "feat: add 7-day source health report script"
```

---

## Phase E: CI & Docs

### Task E20: GitHub Actions Workflows

**Files:**
- Create: `.github/workflows/daily.yml`
- Create: `.github/workflows/test.yml`
- Create: `.github/workflows/smoke.yml`

- [ ] **Step 1: Create `daily.yml`**

`.github/workflows/daily.yml`:
```yaml
name: Daily Aggregate

on:
  schedule:
    - cron: "0 1 * * *"  # 09:00 Asia/Taipei
  workflow_dispatch:
    inputs:
      date:
        description: "YYYY-MM-DD (Asia/Taipei) for output filename"
        required: false
        default: ""
      source:
        description: "comma-separated source ids (default: all enabled)"
        required: false
        default: ""

permissions:
  contents: write

jobs:
  aggregate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - run: uv sync
      - name: Run aggregator
        env:
          TWITTERAPI_IO_KEY: ${{ secrets.TWITTERAPI_IO_KEY }}
          THREADS_ACCESS_TOKEN: ${{ secrets.THREADS_ACCESS_TOKEN }}
          THREADS_REFRESH_TOKEN: ${{ secrets.THREADS_REFRESH_TOKEN }}
          THREADS_APP_ID: ${{ secrets.THREADS_APP_ID }}
          THREADS_APP_SECRET: ${{ secrets.THREADS_APP_SECRET }}
          PRODUCT_HUNT_TOKEN: ${{ secrets.PRODUCT_HUNT_TOKEN }}
          RSSHUB_INSTANCE_URL: ${{ secrets.RSSHUB_INSTANCE_URL || 'https://rsshub.app' }}
          WEWE_RSS_URL: ${{ secrets.WEWE_RSS_URL }}
          WEWE_RSS_KEY: ${{ secrets.WEWE_RSS_KEY }}
        run: |
          ARGS=""
          if [ -n "${{ inputs.date }}" ]; then ARGS="$ARGS --date ${{ inputs.date }}"; fi
          if [ -n "${{ inputs.source }}" ]; then ARGS="$ARGS --source ${{ inputs.source }}"; fi
          uv run python -m social_info $ARGS
      - name: Commit & push
        run: |
          git config user.name "social-info-bot"
          git config user.email "social-info@users.noreply.github.com"
          git add state.db reports/
          if git diff --cached --quiet; then
            echo "No changes"
          else
            git commit -m "chore: daily aggregate $(date -u +%Y-%m-%d)"
            git push
          fi
```

- [ ] **Step 2: Create `test.yml`**

`.github/workflows/test.yml`:
```yaml
name: Tests

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run ruff check src tests
      - run: uv run pytest -v
```

- [ ] **Step 3: Create `smoke.yml`**

`.github/workflows/smoke.yml`:
```yaml
name: Smoke Test (real API)

on:
  workflow_dispatch:

jobs:
  smoke:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
        with:
          python-version: "3.12"
      - run: uv sync
      - name: Smoke run
        env:
          TWITTERAPI_IO_KEY: ${{ secrets.TWITTERAPI_IO_KEY }}
          THREADS_ACCESS_TOKEN: ${{ secrets.THREADS_ACCESS_TOKEN }}
          PRODUCT_HUNT_TOKEN: ${{ secrets.PRODUCT_HUNT_TOKEN }}
          RSSHUB_INSTANCE_URL: ${{ secrets.RSSHUB_INSTANCE_URL || 'https://rsshub.app' }}
        run: uv run python -m social_info --smoke
```

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/
git commit -m "ci: add daily / test / smoke GitHub Actions workflows"
```

---

### Task E21: README + .env.example

**Files:**
- Create: `README.md`
- Create: `.env.example`

- [ ] **Step 1: Create `.env.example`**

`.env.example`:
```bash
# X / Twitter via twitterapi.io (https://twitterapi.io)
TWITTERAPI_IO_KEY=

# Threads (Meta) via official API
# Apply at https://developers.facebook.com → create app → add Threads API product
THREADS_APP_ID=
THREADS_APP_SECRET=
THREADS_ACCESS_TOKEN=
THREADS_REFRESH_TOKEN=

# Product Hunt API (https://api.producthunt.com)
PRODUCT_HUNT_TOKEN=

# RSSHub instance — public default OK for first month, swap for self-host later
RSSHUB_INSTANCE_URL=https://rsshub.app

# wewe-rss (WeChat) — leave empty during PoC; sources.yml has them disabled
WEWE_RSS_URL=
WEWE_RSS_KEY=
```

- [ ] **Step 2: Create `README.md`**

`README.md`:
```markdown
# social-info

Daily AI ecosystem raw aggregator. Pipeline produces a structured Markdown digest each morning at 09:00 Asia/Taipei; downstream consumption is via Claude Code + gh CLI.

## How to consume the daily digest

After 10:00 Asia/Taipei, open Claude Code and ask Claude to read the latest report:

```
看一下今天的 AI digest（reports/$(date +%Y-%m-%d).md）
```

Claude will use `gh api` or local `Read` to load the file and do personalized filtering, ranking, summarization, and reporting based on your current focus.

## How to add or remove sources

Edit `sources.yml`. Schema:

```yaml
sources:
  - id: <unique-id>
    type: <one of: hn_algolia, reddit, github_trending, product_hunt, huggingface, rss, rsshub, twitter, threads, wewe_rss>
    enabled: true | false
    tier: 1 | 2
    language: en | zh-TW | zh-CN
    # ... type-specific params
```

See full reference in [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md).

## First-time setup

1. Install uv: https://docs.astral.sh/uv/
2. `uv sync`
3. Copy `.env.example` to `.env`, fill in keys (or set GitHub Secrets for cloud run)
4. Verify: `uv run python -m social_info --smoke`
5. Push to GitHub, set repository Secrets identical to `.env`
6. The daily workflow runs at 09:00 Asia/Taipei automatically

## Local commands

```bash
uv run python -m social_info                    # full pipeline, write today's report
uv run python -m social_info --dry-run          # don't write db / md
uv run python -m social_info --source hn        # subset
uv run python -m social_info --date 2026-04-25  # backfill / specific date
uv run python -m social_info --smoke            # real API, limit=3, print stdout
uv run python -m social_info.health             # 7-day source success rate
uv run pytest                                   # tests
```

## Common problems

- **Public RSSHub returns 410**: try later or self-host (see spec Appendix D)
- **Threads token expired**: refresh with `THREADS_REFRESH_TOKEN`
- **twitterapi.io credit low**: check at https://twitterapi.io dashboard, consider trimming KOLs

## Spec & Plan

- Spec: [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md)
- Plan: [docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md](docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md)
```

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs: add README with setup, commands, and consumption flow"
```

---

### Task E22: End-to-End Smoke Run

**Files:** none (verification only)

- [ ] **Step 1: Set up local `.env`**

```bash
cp .env.example .env
# fill in at minimum: TWITTERAPI_IO_KEY (skip if you want only no-auth fetchers)
```

- [ ] **Step 2: Run smoke locally — no-auth fetchers only first**

```bash
uv run python -m social_info --source hn,reddit_localllama,github_trending,anthropic_blog --smoke
```

Expected:
- Real API hits succeed
- Some Items printed in markdown render to stdout
- No tracebacks

- [ ] **Step 3: Run full smoke (with auth fetchers)**

After Twitter / Threads / Product Hunt secrets are set in `.env`:

```bash
set -a && source .env && set +a
uv run python -m social_info --smoke
```

Expected:
- All enabled sources tried
- Failures (if any) clearly logged with their `source_id` + error

- [ ] **Step 4: Run full pipeline once locally and inspect output**

```bash
uv run python -m social_info
ls reports/
cat reports/$(date +%Y-%m-%d).md | head -80
```

Expected:
- `reports/YYYY-MM-DD.md` created
- File header shows `total_items`, `sources_active`, `sources_failed`
- Items grouped by platform
- `state.db` updated (next run on same items will skip them)

- [ ] **Step 5: Run twice in a row to verify idempotency**

```bash
uv run python -m social_info
uv run python -m social_info
```

Expected:
- Second run shows `0 new items` (or near-zero) due to L1 dedup

- [ ] **Step 6: Commit any sources.yml tweaks discovered during smoke**

```bash
# if you found issues that need source tweaks
git add sources.yml
git commit -m "chore: tune sources.yml after first smoke run"
```

- [ ] **Step 7: Push to GitHub, set Secrets, trigger smoke.yml manually**

```bash
git push -u origin main
gh secret set TWITTERAPI_IO_KEY < <(echo "$TWITTERAPI_IO_KEY")
# ... repeat for other secrets
gh workflow run smoke.yml
gh run watch
```

Expected: smoke.yml completes successfully on Actions runner

- [ ] **Step 8: Manually trigger first daily.yml run to seed the repo with today's report**

```bash
gh workflow run daily.yml
gh run watch
```

Expected:
- `reports/YYYY-MM-DD.md` and `state.db` get committed by the bot
- Workflow log shows `Wrote reports/YYYY-MM-DD.md (...)`

---

## Self-Review (Plan Author)

**Spec coverage check:**

- ✅ §1 使用者與用途 → covered by D17/D18 pipeline + README D21
- ✅ §2 設計原則 → enforced by architecture (no LLM in pipeline; metadata preserved; CLI/gh interface)
- ✅ §3 內容範圍 → encoded in `sources.yml` (B6) + HN keyword filter (C7)
- ✅ §4 Source 清單 → all 10 fetcher tasks (C7-C16) + sources.yml (B6)
- ✅ §5 輸出規格 → markdown rendering B5 + filename in pipeline D17
- ✅ §6 Per-item Schema → `Item` dataclass A2
- ✅ §7 Dedup → `dedup.py` A4
- ✅ §8 SQLite Schema → `db.py` A3
- ✅ §9 執行環境 → `.github/workflows/daily.yml` E20 + secrets in README E21
- ✅ §10 Repo 結構 → file structure section (this plan)
- ✅ §11 Testing → tests in every fetcher task + `test.yml` E20
- ✅ §12 監控 → `health.py` D19 + `daily.yml` E20 (workflow status)
- ✅ §13 完整工作流 → diagram in README E21 + cron in `daily.yml`
- ✅ §14 Phasing → wewe_rss disabled by default in sources.yml B6
- ✅ §15 Open Questions → not impl tasks but flagged in spec, future work
- ✅ §16 Out of Scope → tasks correctly omit LLM / SemHash / web UI / email

**Placeholder scan:** none of the "TBD/TODO/similar to/add appropriate" patterns. Each task contains complete code.

**Type consistency:** `Item` fields used identically across A2 (definition), A3 (`to_db_row`), A4 (Deduper), B5 (markdown), and all fetchers. `FetchResult` used in pipeline D17 + markdown render B5 (`failures` argument).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for tasks 7-16 (fetchers) which are independent and parallelizable.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints. Best if you want close turn-by-turn collaboration.

**Which approach?**
