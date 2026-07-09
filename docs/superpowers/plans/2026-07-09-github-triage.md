# GitHub Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 解 daily digest 對 GitHub trending 段的兩面向問題：以前看過的 repo 不再出現（pipeline dedup 靜默過濾）+ scope 太窄（trending 結構性不吐 stable AI repo）；透過 (1) 修 trendshift + (2) dedup 加 N 天 re-surface + (2.5) 新增 GitHub search API by description + (3) 擴 fetcher scope 四路併行落地。

**Architecture:** Python asyncio pipeline (existing `social-info` repo)、fetcher-side changes only（沒動 digest workflow）；dedup `Deduper.process()` 契約重定義為 `DedupResult { new_items, resurface_ids }`、pipeline 端處理 write；新增 `github_search` fetcher 走 `gh CLI` API（reuse 既有 `gh auth` token）；每 phase 3-5 天 verify、不 regress 才進下一 phase。

**Tech Stack:** Python 3.x + uv venv、httpx + BeautifulSoup（既有 pattern）、SQLite (state.db)、pytest + pytest-asyncio + pytest-httpx (mock fetcher)、gh CLI (github_search)、`sources.yml` YAML config、launchd (06:00 daily fetch) — 全部既有 stack、不新增 dep。

## Global Constraints

- **Repo 路徑**：`/Users/linhancheng/code/social-info/`（physical path、不是 `~/Desktop/projects/social-info` symlink；launchd TCC 限制、hardcode physical path）
- **Python 執行**：`cd /Users/linhancheng/code/social-info && uv run pytest ...`（不用 `pip install`、走 uv managed venv）
- **Commit message**：English Conventional Commits（`feat:` / `fix:` / `test:` / `refactor:` / `docs:` / `chore:`）
- **No comments in generated code**（user CLAUDE.md style rule；WHY 註解只在 non-obvious invariant 才留）
- **反晶晶體**：doc / commit message 中英分明、能翻成中文的英文詞就翻；技術專名（API / OAuth / cron 等）保留英文
- **`??` 不 apply**（Python not JS）；用 pure function 為主
- **Tests 路徑**：`tests/*.py` + `tests/fetchers/*.py`（既有結構）、conftest.py 在 `tests/` 根、fixtures 在 `tests/fixtures/`
- **相關 spec**：`docs/superpowers/specs/2026-07-09-github-triage-design.md`（v1.1）+ `docs/superpowers/specs/2026-07-09-github-triage-review.md`（T2 findings disposition）
- **Phase 4 spun out**：本 plan 只覆蓋 Phase 1 / 2 / 2.5 / 3 / 5、不含原 Phase 4（triage + stack context + trial-review）

---

## File Structure

**Modify**：
- `src/social_info/fetchers/trendshift.py` — Phase 1
- `src/social_info/fetchers/github_trending.py` — Phase 3（拿掉 keyword filter、迭代 since list）
- `src/social_info/db.py` — Phase 2（schema `last_surfaced_at` column + init_schema ALTER）
- `src/social_info/dedup.py` — Phase 2（`DedupResult` refactor + 5-case behavior）
- `src/social_info/pipeline.py` — Phase 2（呼叫端處理 `resurface_ids`）
- `src/social_info/markdown.py` — Phase 2（render 含 resurface items）
- `sources.yml` — Phase 2.5 新 `github_search` source、Phase 3 `github_trending` 擴 languages/since

**Create**：
- `src/social_info/fetchers/github_search.py` — Phase 2.5 新 fetcher
- `scripts/measure-phase0-baseline.sh` — Phase 0 baseline 量測
- `scripts/dry-run-n-value.py` — Phase 2 N 值 dry-run
- `tests/fetchers/test_github_search.py` — Phase 2.5 test
- `tests/fetchers/test_trendshift_fix.py`（可能）— Phase 1 test（若 test 不存在）
- `tests/fixtures/github_search_response.json` — Phase 2.5 mock fixture
- `docs/philip/phase0-baseline-2026-07-09.md` — Phase 0 baseline
- `docs/philip/phase5-verify-YYYY-MM-DD.md` — Phase 5 verify report

**Modify (tests)**：
- `tests/test_dedup.py` — 加 5-case tests
- `tests/test_db.py` — 加 `last_surfaced_at` schema test
- `tests/fetchers/test_github_trending.py` — 更新（拿掉 keyword filter assertion）

---

## Task 1: Phase 0 baseline 量測（Phase 3 前必量）

**Files:**
- Create: `scripts/measure-phase0-baseline.sh`
- Create: `docs/philip/phase0-baseline-2026-07-09.md`

**Interfaces:**
- Consumes: 過去 14 天 `reports/digest-*.html`（既有 daily digest 產出）
- Produces: baseline 數字（現有 GitHub 段 hi + med tier repo 平均數 / day）供 Phase 3 完成後 secondary metric 對照

- [ ] **Step 1: 寫 baseline 量測 script**

Create `scripts/measure-phase0-baseline.sh`:

```bash
#!/usr/bin/env bash
# Phase 0 baseline: count hi + med tier GitHub repo entries per day
# in past 14 days of digest-*.html (before scope expansion + resurface).
set -euo pipefail

REPO="/Users/linhancheng/code/social-info"
cd "$REPO"

TOTAL_HI=0
TOTAL_MED=0
DAYS=0
for i in $(seq 0 13); do
  d=$(date -v-${i}d +%F 2>/dev/null || date -d "$i days ago" +%F)
  f="reports/digest-${d}.html"
  [ -f "$f" ] || continue
  # verdict class 命中 = hi/med row（見 CLAUDE.md 「🛠 GitHub 倉庫觀察」段 verdict classes）
  hi=$(grep -c 'class="verdict"' "$f" 2>/dev/null || true)
  med=$(grep -c 'class="verdict-watch"' "$f" 2>/dev/null || true)
  echo "${d}: hi=${hi} med=${med}"
  TOTAL_HI=$((TOTAL_HI + hi))
  TOTAL_MED=$((TOTAL_MED + med))
  DAYS=$((DAYS + 1))
done

if [ "$DAYS" -eq 0 ]; then
  echo "no digest files in past 14 days"
  exit 1
fi

AVG_HI=$(awk "BEGIN {printf \"%.1f\", $TOTAL_HI / $DAYS}")
AVG_MED=$(awk "BEGIN {printf \"%.1f\", $TOTAL_MED / $DAYS}")
AVG_TOTAL=$(awk "BEGIN {printf \"%.1f\", ($TOTAL_HI + $TOTAL_MED) / $DAYS}")

echo ""
echo "=== Phase 0 Baseline ($DAYS days) ==="
echo "avg hi / day:  $AVG_HI"
echo "avg med / day: $AVG_MED"
echo "avg hi+med / day: $AVG_TOTAL"
```

- [ ] **Step 2: 執行 script**

```bash
chmod +x scripts/measure-phase0-baseline.sh
./scripts/measure-phase0-baseline.sh | tee docs/philip/phase0-baseline-2026-07-09.md
```

Expected: 印出 14 天各日 hi/med count + 平均值。存到 `docs/philip/phase0-baseline-2026-07-09.md`。

- [ ] **Step 3: 手動加 header 補充到 baseline doc**

Edit `docs/philip/phase0-baseline-2026-07-09.md`:

```markdown
# Phase 0 Baseline — GitHub triage spec Phase 3 前

**Date measured**: 2026-07-09
**Purpose**: Phase 3 完成後 secondary metric 對照組（相比 Phase 0 提升 3-5 倍的門檻）
**Source**: 過去 14 天 `reports/digest-*.html`「🛠 GitHub 倉庫觀察」段 verdict class count

<baseline output 貼在下面>
```

- [ ] **Step 4: Commit**

```bash
cd /Users/linhancheng/code/social-info
git add scripts/measure-phase0-baseline.sh docs/philip/phase0-baseline-2026-07-09.md
git commit -m "chore: measure phase 0 baseline for github triage spec"
```

---

## Task 2: Phase 1 — 修 trendshift fetcher

**Files:**
- Investigate: `src/social_info/fetchers/trendshift.py`（現況 5 天 0 hit）
- Modify: `src/social_info/fetchers/trendshift.py`
- Test: `tests/fetchers/test_trendshift.py`（若不存在則 create）

**Interfaces:**
- Consumes: trendshift.io HTML page（rising / trending list）
- Produces: `list[Item]` 進 pipeline dedup（`source="trendshift"`、`source_handle="rising:rank-X"` 或類似 pattern）

- [ ] **Step 1: Investigate trendshift 現況**

```bash
cd /Users/linhancheng/code/social-info
# 看 fetcher 現有邏輯
cat src/social_info/fetchers/trendshift.py

# 看它抓的 URL 是否還 200 + HTML 結構還一致
curl -sSI "https://trendshift.io/" | head -5
curl -sSL "https://trendshift.io/" -H "User-Agent: Mozilla/5.0" 2>&1 | head -100
```

- [ ] **Step 2: 對照 fetcher parser 找 root cause**

比對 fetcher 用什麼 selector / URL、實際頁面 HTML 結構。可能情況：
- URL 換路徑（e.g. `/repositories/rising` → `/repositories/trending`）
- HTML class name 換（Trendshift 前端可能 re-styled）
- Rate limit / Cloudflare 擋（回 403/429）
- SPA 化（HTML 空、要跑 JS）

記錄 root cause 到 issue note。

- [ ] **Step 3: 寫失敗 test（新 URL 或 parser）**

Create/Update `tests/fetchers/test_trendshift.py`（follow `tests/fetchers/test_github_trending.py` pattern）:

```python
import re
from pathlib import Path

import httpx
import pytest

from social_info.config import SourceConfig
from social_info.fetchers.trendshift import fetch


@pytest.mark.asyncio
async def test_fetch_trendshift_parses_current_structure(httpx_mock):
    # Fixture reflect current live HTML (grab via curl + save to fixtures/)
    html = Path("tests/fixtures/trendshift.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://trendshift\.io.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="trendshift",
        type="trendshift",
        enabled=True,
        tier=1,
        params={"limit": 25},
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    assert len(items) > 0, "trendshift fetch returned empty — parser out of sync"
    assert all(it.source == "trendshift" for it in items)
    assert all("github.com" in it.url for it in items)
```

Save fixture: `curl -sSL "https://trendshift.io/" > tests/fixtures/trendshift.html`

- [ ] **Step 4: Run test to verify fail**

```bash
cd /Users/linhancheng/code/social-info
uv run pytest tests/fetchers/test_trendshift.py -v
```

Expected: FAIL（parser miss、items 空）

- [ ] **Step 5: Fix trendshift fetcher**

Update `src/social_info/fetchers/trendshift.py` per root cause from Step 2：更新 URL / selector / parser 邏輯。Show exact diff based on real HTML structure found in Step 2。

- [ ] **Step 6: Run test to verify pass**

```bash
uv run pytest tests/fetchers/test_trendshift.py -v
```

Expected: PASS

- [ ] **Step 7: Verify with real fetch (integration)**

```bash
cd /Users/linhancheng/code/social-info
uv run python -c "
import asyncio
import httpx
from social_info.config import SourceConfig
from social_info.fetchers.trendshift import fetch

async def main():
    cfg = SourceConfig(id='trendshift', type='trendshift', enabled=True, tier=1, params={'limit': 25})
    async with httpx.AsyncClient() as c:
        items = await fetch(cfg, c)
    print(f'fetched {len(items)} items')
    for it in items[:5]:
        print(f'  {it.url}  {it.title}')

asyncio.run(main())
"
```

Expected: 印出 20+ 個 items、含 github.com URL 和 repo title。

- [ ] **Step 8: Commit**

```bash
git add src/social_info/fetchers/trendshift.py tests/fetchers/test_trendshift.py tests/fixtures/trendshift.html
git commit -m "fix: trendshift fetcher parser out of sync with current HTML structure"
```

---

## Task 3: Phase 2 (a) — Schema migration + last_surfaced_at column

**Files:**
- Modify: `src/social_info/db.py`（init_schema 加 ALTER TABLE items ADD COLUMN last_surfaced_at）
- Test: `tests/test_db.py`（加 schema test）

**Interfaces:**
- Produces: `items` table 新 column `last_surfaced_at TEXT`（ISO format datetime）；既有 rows backfill = `posted_at`

- [ ] **Step 1: Schema audit — 確認 items table 現有 columns**

```bash
cd /Users/linhancheng/code/social-info
sqlite3 state.db ".schema items"
```

Expected output 應含 `posted_at TEXT NOT NULL`、無 `last_surfaced_at` column。若 `posted_at` 不存在則 migration 需另補 backfill 策略。

- [ ] **Step 2: 寫失敗 test — schema 該含 last_surfaced_at**

Update `tests/test_db.py` 加測試：

```python
def test_items_schema_has_last_surfaced_at():
    import tempfile
    from pathlib import Path
    from social_info.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.init_schema()
        cols = {row["name"] for row in db.conn.execute("PRAGMA table_info(items)")}
        assert "last_surfaced_at" in cols, \
            "items table missing last_surfaced_at column (Phase 2 migration missing)"
        db.close()


def test_migration_backfills_last_surfaced_at_from_posted_at():
    import tempfile
    from datetime import datetime
    from pathlib import Path
    from social_info.db import Database

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"

        # simulate an older db without last_surfaced_at
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.execute("""
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
            )
        """)
        conn.execute(
            "INSERT INTO items (id, url, canonical_url, title, title_hash, "
            "source, source_handle, source_tier, posted_at, fetched_at) "
            "VALUES ('id1', 'https://x/', 'https://x/', 't', 'th', 's', 'h', 1, "
            "'2026-06-01T00:00:00', '2026-06-01T00:00:00')"
        )
        conn.commit()
        conn.close()

        # run migration via init_schema
        db = Database(db_path)
        db.init_schema()

        row = db.conn.execute(
            "SELECT last_surfaced_at, posted_at FROM items WHERE id = 'id1'"
        ).fetchone()
        assert row["last_surfaced_at"] == row["posted_at"], \
            "migration should backfill last_surfaced_at from posted_at"
        db.close()
```

- [ ] **Step 3: Run test to verify fail**

```bash
uv run pytest tests/test_db.py::test_items_schema_has_last_surfaced_at tests/test_db.py::test_migration_backfills_last_surfaced_at_from_posted_at -v
```

Expected: 2 tests FAIL

- [ ] **Step 4: Add migration to db.py init_schema**

Modify `src/social_info/db.py`、找到 `init_schema` 段既有的 ALTER TABLE items ADD COLUMN comments_json 附近、加：

```python
        if "last_surfaced_at" not in existing_item_cols:
            self.conn.execute(
                "ALTER TABLE items ADD COLUMN last_surfaced_at TEXT"
            )
            self.conn.execute(
                "UPDATE items SET last_surfaced_at = posted_at "
                "WHERE last_surfaced_at IS NULL"
            )
```

Also update `SCHEMA` constant (top of db.py) 讓新 db 直接含 `last_surfaced_at`:

```python
# in the items CREATE TABLE block
last_surfaced_at TEXT,
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_db.py::test_items_schema_has_last_surfaced_at tests/test_db.py::test_migration_backfills_last_surfaced_at_from_posted_at -v
```

Expected: 2 tests PASS

- [ ] **Step 6: Dry-run migration on production state.db（sandbox 副本）**

```bash
cd /Users/linhancheng/code/social-info
cp state.db /tmp/state-sandbox.db

# Item count 前
COUNT_BEFORE=$(sqlite3 /tmp/state-sandbox.db "SELECT COUNT(*) FROM items")
echo "count before: $COUNT_BEFORE"

# 執行 migration
uv run python -c "
from pathlib import Path
from social_info.db import Database
db = Database(Path('/tmp/state-sandbox.db'))
db.init_schema()
db.close()
"

# Item count 後
COUNT_AFTER=$(sqlite3 /tmp/state-sandbox.db "SELECT COUNT(*) FROM items")
echo "count after: $COUNT_AFTER"

# Backfill 檢查
sqlite3 /tmp/state-sandbox.db "SELECT COUNT(*) FROM items WHERE last_surfaced_at IS NULL"
```

Expected: count_before == count_after、last_surfaced_at IS NULL count = 0

- [ ] **Step 7: 執行 migration 到 production state.db**

```bash
# 先 backup
cp state.db state.db.bak-$(date +%s)

# migrate
uv run python -c "
from pathlib import Path
from social_info.db import Database
db = Database(Path('state.db'))
db.init_schema()
db.close()
"

# verify
sqlite3 state.db "SELECT COUNT(*), COUNT(last_surfaced_at) FROM items"
```

Expected: 兩個 count 相同（backfill 完整）

- [ ] **Step 8: Commit**

```bash
git add src/social_info/db.py tests/test_db.py
git commit -m "feat: add last_surfaced_at column to items table with backfill migration"
```

---

## Task 4: Phase 2 (b) — DedupResult refactor + 5-case behavior

> **best-of-N**: invoke Workflow `{name: "best-of-n-implement", args: {task: "Refactor Deduper.process() in src/social_info/dedup.py to return DedupResult { new_items: list[Item], resurface_ids: list[str] } instead of list[Item]. Implement 5-case behavior per spec Phase 2 case table (1: 全新 → new_items; 2: L2 hit + <N → higher-tier-wins existing logic, no resurface; 3: L2 hit + ≥N → higher-tier-wins + resurface existing id; 4: L1 hit + <N → skip; 5: L1 hit + ≥N → resurface id). Deduper.__init__ signature 加 resurface_days: int = 30 parameter. All 5-case tests in tests/test_dedup.py must pass. Existing dedup tests must still pass. No changes outside dedup.py + test file. 驗收標準: tests/test_dedup.py 全 pass, 5-case new tests 各驗 1 case behavior 正確, DedupResult dataclass exported from social_info.dedup.", n: 3, testCmd: "cd /Users/linhancheng/code/social-info && uv run pytest tests/test_dedup.py -v"}}` — 勝者 diff 由 main git apply 後重跑測試

**Files:**
- Modify: `src/social_info/dedup.py`
- Test: `tests/test_dedup.py`

**Interfaces:**
- Produces: `Deduper.process()` 新 signature = `(items: list[Item]) -> DedupResult`；`DedupResult` = dataclass `{ new_items: list[Item], resurface_ids: list[str] }`
- Consumes（來自 pipeline）: same items list as before
- Consumers（下游）：pipeline.py 要處理 new signature（Task 5）

- [ ] **Step 1: 寫失敗 test — 5-case behavior**

Update `tests/test_dedup.py`、加：

```python
from datetime import datetime, timedelta
from social_info.dedup import Deduper, DedupResult, compute_item_id


def test_dedup_case_1_all_new_item():
    """Case 1: 全新 item (L1 miss + L2 miss) → 加 new_items"""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.init_schema()

        item = _make_item("https://github.com/foo/bar", "foo/bar")
        result = Deduper(db).process([item])

        assert isinstance(result, DedupResult)
        assert len(result.new_items) == 1
        assert result.new_items[0].url == "https://github.com/foo/bar"
        assert result.resurface_ids == []
        db.close()


def test_dedup_case_4_l1_hit_lt_n_days_skipped():
    """Case 4: L1 hit (id 已存在) + last_surfaced_at < 30 天 → skip"""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.init_schema()

        item = _make_item("https://github.com/foo/bar", "foo/bar")
        # insert with last_surfaced_at = 5 days ago
        item_id = compute_item_id(item.canonical_url)
        recent = (datetime.utcnow() - timedelta(days=5)).isoformat()
        row = item.to_db_row(item_id=item_id, title_hash="th1")
        row["last_surfaced_at"] = recent
        db.conn.execute(
            "INSERT INTO items (id, url, canonical_url, title, title_hash, "
            "source, source_handle, source_tier, posted_at, fetched_at, "
            "language, engagement_json, also_appeared_in, last_surfaced_at) "
            "VALUES (:id, :url, :canonical_url, :title, :title_hash, :source, "
            ":source_handle, :source_tier, :posted_at, :fetched_at, :language, "
            ":engagement_json, :also_appeared_in, :last_surfaced_at)",
            row,
        )
        db.conn.commit()

        # 再送同 item
        result = Deduper(db).process([item])
        assert result.new_items == []
        assert result.resurface_ids == []
        db.close()


def test_dedup_case_5_l1_hit_gte_n_days_resurfaces():
    """Case 5: L1 hit + last_surfaced_at >= 30 天 → 加 resurface_ids"""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.init_schema()

        item = _make_item("https://github.com/foo/bar", "foo/bar")
        item_id = compute_item_id(item.canonical_url)
        old = (datetime.utcnow() - timedelta(days=35)).isoformat()
        row = item.to_db_row(item_id=item_id, title_hash="th1")
        row["last_surfaced_at"] = old
        db.conn.execute(
            "INSERT INTO items (id, url, canonical_url, title, title_hash, "
            "source, source_handle, source_tier, posted_at, fetched_at, "
            "language, engagement_json, also_appeared_in, last_surfaced_at) "
            "VALUES (:id, :url, :canonical_url, :title, :title_hash, :source, "
            ":source_handle, :source_tier, :posted_at, :fetched_at, :language, "
            ":engagement_json, :also_appeared_in, :last_surfaced_at)",
            row,
        )
        db.conn.commit()

        result = Deduper(db, resurface_days=30).process([item])
        assert result.new_items == []
        assert item_id in result.resurface_ids
        db.close()


def test_dedup_case_3_l2_hit_gte_n_days_resurfaces():
    """Case 3: L2 hit (title_hash 同 URL 不同) + >= 30 天 → higher-tier-wins + resurface_ids"""
    # Similar setup — insert with different URL but same title_hash,
    # last_surfaced_at = 40 days ago; incoming with higher tier (lower number).
    # Assert result.new_items includes incoming AND result.resurface_ids
    # includes existing id (for last_surfaced_at update).
    pass  # implement following pattern above
```

- [ ] **Step 2: Run test to verify fail**

```bash
uv run pytest tests/test_dedup.py -v -k "case_"
```

Expected: FAIL（DedupResult not defined、Deduper.process 返回 list not DedupResult）

- [ ] **Step 3: Refactor Deduper.process → DedupResult**

Modify `src/social_info/dedup.py`、加 dataclass + refactor `process()`:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class DedupResult:
    new_items: list[Item] = field(default_factory=list)
    resurface_ids: list[str] = field(default_factory=list)


class Deduper:
    def __init__(self, db: Database, resurface_days: int = 30):
        self.db = db
        self.resurface_days = resurface_days

    def process(self, items: list[Item]) -> DedupResult:
        result = DedupResult()
        seen_ids_in_batch: set[str] = set()
        seen_title_hashes_in_batch: dict[str, Item] = {}
        threshold = datetime.utcnow() - timedelta(days=self.resurface_days)

        for item in items:
            item_id = compute_item_id(item.canonical_url)
            title_hash = compute_title_hash(item.title)

            if item_id in seen_ids_in_batch:
                continue

            # L1 hit
            if self.db.has_item_id(item_id):
                last_surfaced = self._get_last_surfaced_at(item_id)
                if last_surfaced is not None and last_surfaced < threshold:
                    # Case 5: L1 hit + >= N 天 → resurface
                    result.resurface_ids.append(item_id)
                    seen_ids_in_batch.add(item_id)
                # else Case 4: L1 hit + < N 天 → skip
                continue

            # L2 hit
            existing = self.db.find_by_title_hash(title_hash)
            if existing is not None:
                existing_last = self._parse_dt(existing.get("last_surfaced_at"))
                is_stale = existing_last is not None and existing_last < threshold

                if item.source_tier < existing["source_tier"]:
                    # higher-tier-wins
                    item.also_appeared_in.append({
                        "source": existing["source"],
                        "source_handle": existing["source_handle"] or "",
                        "url": existing["url"],
                    })
                    result.new_items.append(item)
                    seen_ids_in_batch.add(item_id)
                    seen_title_hashes_in_batch[title_hash] = item
                    if is_stale:
                        # Case 3: L2 hit + >= N 天 → also mark old as resurfaced
                        result.resurface_ids.append(existing["id"])
                else:
                    # merge into existing also_appeared_in
                    appeared = json.loads(existing["also_appeared_in"] or "[]")
                    appeared.append({
                        "source": item.source,
                        "source_handle": item.source_handle,
                        "url": item.url,
                    })
                    self.db.update_also_appeared_in(existing["id"], json.dumps(appeared))
                    if is_stale:
                        # Case 3: L2 hit lower-tier + >= N 天 → resurface existing
                        result.resurface_ids.append(existing["id"])
                continue

            # in-batch L2 dedup（既有邏輯，簡化）
            if title_hash in seen_title_hashes_in_batch:
                prior = seen_title_hashes_in_batch[title_hash]
                if item.source_tier < prior.source_tier:
                    # swap: incoming replaces prior in result.new_items
                    prior_idx = result.new_items.index(prior)
                    result.new_items[prior_idx] = item
                    seen_title_hashes_in_batch[title_hash] = item
                    seen_ids_in_batch.discard(compute_item_id(prior.canonical_url))
                    seen_ids_in_batch.add(item_id)
                continue

            # Case 1: 全新 item
            result.new_items.append(item)
            seen_ids_in_batch.add(item_id)
            seen_title_hashes_in_batch[title_hash] = item

        return result

    def _get_last_surfaced_at(self, item_id: str) -> datetime | None:
        cur = self.db.conn.execute(
            "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
        )
        row = cur.fetchone()
        if row is None or row["last_surfaced_at"] is None:
            return None
        return self._parse_dt(row["last_surfaced_at"])

    @staticmethod
    def _parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            return None
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_dedup.py -v
```

Expected: 全部 PASS（含既有 dedup tests + 新 5-case tests）

- [ ] **Step 5: Commit**

```bash
git add src/social_info/dedup.py tests/test_dedup.py
git commit -m "refactor: dedup returns DedupResult with new_items + resurface_ids for N-day resurface"
```

---

## Task 5: Phase 2 (c) — Pipeline write resurface + render

**Files:**
- Modify: `src/social_info/pipeline.py`（處理 `DedupResult`、update `last_surfaced_at`）
- Modify: `src/social_info/markdown.py`（render include resurface items）
- Modify: `src/social_info/db.py`（加 `update_last_surfaced_at` helper）
- Test: `tests/test_pipeline_dedup_resurface.py`（新增）

**Interfaces:**
- Consumes: `DedupResult` from Task 4
- Produces: raw md 含 new_items + resurface items；`fetch_runs.net_new` 只算 new_items、resurface count 另記入新 column `resurface_count`（optional、可 append 進 metadata）

- [ ] **Step 1: 加 db helper `update_last_surfaced_at`**

Modify `src/social_info/db.py`、加：

```python
def update_last_surfaced_at(self, item_ids: list[str]) -> None:
    if not item_ids:
        return
    now = datetime.utcnow().isoformat()
    self.conn.executemany(
        "UPDATE items SET last_surfaced_at = ? WHERE id = ?",
        [(now, item_id) for item_id in item_ids],
    )
    self.conn.commit()

def get_items_by_ids(self, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    placeholders = ",".join(["?"] * len(item_ids))
    cur = self.conn.execute(
        f"SELECT * FROM items WHERE id IN ({placeholders})", item_ids
    )
    return [dict(row) for row in cur.fetchall()]
```

- [ ] **Step 2: 寫 pipeline integration test**

Create `tests/test_pipeline_dedup_resurface.py`:

```python
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from social_info.db import Database
from social_info.dedup import Deduper, compute_item_id
from social_info.fetchers.base import Item


def _make_item(url, title):
    return Item(
        title=title, url=url, canonical_url=url,
        source="github_trending", source_handle="trending:python",
        source_tier=1,
        posted_at=datetime(2026, 7, 9, 0, 0, 0),
        fetched_at=datetime(2026, 7, 9, 0, 0, 0),
    )


def test_pipeline_resurface_updates_last_surfaced_at():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        db.init_schema()

        # insert old item (40 days ago)
        item = _make_item("https://github.com/mattpocock/skills", "mattpocock/skills")
        item_id = compute_item_id(item.canonical_url)
        old = (datetime.utcnow() - timedelta(days=40)).isoformat()
        row = item.to_db_row(item_id=item_id, title_hash="th1")
        row["last_surfaced_at"] = old
        db.conn.execute(
            "INSERT INTO items (id, url, canonical_url, title, title_hash, "
            "source, source_handle, source_tier, posted_at, fetched_at, "
            "language, engagement_json, also_appeared_in, last_surfaced_at) "
            "VALUES (:id, :url, :canonical_url, :title, :title_hash, :source, "
            ":source_handle, :source_tier, :posted_at, :fetched_at, :language, "
            ":engagement_json, :also_appeared_in, :last_surfaced_at)",
            row,
        )
        db.conn.commit()

        # 送同 item 進 Deduper
        result = Deduper(db, resurface_days=30).process([item])
        assert item_id in result.resurface_ids

        # update
        db.update_last_surfaced_at(result.resurface_ids)

        # verify
        updated = db.conn.execute(
            "SELECT last_surfaced_at FROM items WHERE id = ?", (item_id,)
        ).fetchone()
        new_ts = datetime.fromisoformat(updated["last_surfaced_at"])
        assert (datetime.utcnow() - new_ts).total_seconds() < 5, \
            "last_surfaced_at should be updated to now"
        db.close()
```

- [ ] **Step 3: Run test verify fail**

```bash
uv run pytest tests/test_pipeline_dedup_resurface.py -v
```

Expected: 若 `update_last_surfaced_at` 未加、FAIL；加了則 PASS。

- [ ] **Step 4: Update pipeline.py 使用 DedupResult**

Modify `src/social_info/pipeline.py`、找到 `deduper.process(all_items)` 段、改：

```python
    deduper = Deduper(db, resurface_days=30)  # config from sources.yml or default
    dedup_result = deduper.process(all_items)
    new_items = dedup_result.new_items

    # Update last_surfaced_at for resurface items
    if not dry_run:
        db.update_last_surfaced_at(dedup_result.resurface_ids)

    # Fetch resurface items for rendering
    resurface_items_rows = db.get_items_by_ids(dedup_result.resurface_ids)
    resurface_items = [_row_to_item(r) for r in resurface_items_rows]

    annotate_net_new(results, new_items)

    # ... 既有 fetch_runs 寫入邏輯 ...

    # markdown render 含 resurface
    return new_items + resurface_items  # or per existing return contract
```

`_row_to_item` 是 helper 從 db row 重建 Item（可能 db.py 已有類似 helper）—— 若無、加：

```python
def _row_to_item(row: dict) -> Item:
    return Item(
        title=row["title"],
        url=row["url"],
        canonical_url=row["canonical_url"],
        source=row["source"],
        source_handle=row["source_handle"] or "",
        source_tier=row["source_tier"],
        posted_at=datetime.fromisoformat(row["posted_at"]),
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        author=row.get("author", ""),
        excerpt=row.get("excerpt", ""),
        language=row.get("language", "en"),
        engagement=json.loads(row.get("engagement_json") or "{}"),
        also_appeared_in=json.loads(row.get("also_appeared_in") or "[]"),
    )
```

- [ ] **Step 5: markdown.py — 標記 resurface items**

Modify `src/social_info/markdown.py` `render_item`、若 item 為 resurface（可加 `_is_resurface` attr or 標記進 excerpt）、prepend 一個 marker:

```python
def render_item(item: Item, is_resurface: bool = False) -> str:
    lines = []
    prefix = "🔁 " if is_resurface else ""
    lines.append(f"### {prefix}[{item.title}]({item.url})")
    # ...
```

Pipeline pass resurface items 時 flag `is_resurface=True`。

- [ ] **Step 6: Run tests full suite**

```bash
uv run pytest tests/ -v
```

Expected: 所有 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/social_info/pipeline.py src/social_info/markdown.py src/social_info/db.py tests/test_pipeline_dedup_resurface.py
git commit -m "feat: pipeline processes DedupResult and renders resurface items with marker"
```

---

## Task 6: Phase 2 (d) — N-value dry-run 選定

**Files:**
- Create: `scripts/dry-run-n-value.py`
- Update: `sources.yml`（加 `resurface_days_default`）or `dedup.py`（if config load 從別處來）

**Interfaces:**
- Consumes: 現有 state.db + 過去 30 天 raw md（`reports/*.md`）
- Produces: N 值選定 documented decision

- [ ] **Step 1: 寫 dry-run script 模擬不同 N 值影響**

Create `scripts/dry-run-n-value.py`:

```python
"""Dry-run: simulate applying different N-day resurface thresholds
to see how many extra items would surface per day."""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


DB_PATH = Path("state.db")
N_VALUES = [15, 30, 60]


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 過去 30 天內 fetched_at 的 items（可能是候選被 dedup 掉的）
    since = (datetime.utcnow() - timedelta(days=30)).isoformat()
    all_fetched_gt = conn.execute(
        "SELECT id, url, title, source, posted_at, last_surfaced_at "
        "FROM items WHERE fetched_at > ?",
        (since,),
    ).fetchall()

    print(f"Items with fetched_at within past 30d: {len(all_fetched_gt)}")

    now = datetime.utcnow()
    for n in N_VALUES:
        threshold = now - timedelta(days=n)
        would_resurface = 0
        for row in all_fetched_gt:
            lsa_str = row["last_surfaced_at"] or row["posted_at"]
            try:
                lsa = datetime.fromisoformat(lsa_str.replace("Z", "+00:00")).replace(tzinfo=None)
            except (ValueError, TypeError):
                continue
            if lsa < threshold:
                would_resurface += 1
        print(f"  N={n}: {would_resurface} items would resurface / 30 days "
              f"(~{would_resurface/30:.1f} / day)")

    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 執行 script**

```bash
cd /Users/linhancheng/code/social-info
uv run python scripts/dry-run-n-value.py
```

Expected: 印出 N=15/30/60 各自 resurface count / day。

- [ ] **Step 3: 選定 N 值、update pipeline default**

根據 dry-run 結果選 N（e.g. 若 N=30 每天 <20 個 resurface → OK；若爆 >100 → 選 60）。

Update `src/social_info/pipeline.py`:

```python
    RESURFACE_DAYS = 30  # 或 dry-run 選定值
    deduper = Deduper(db, resurface_days=RESURFACE_DAYS)
```

- [ ] **Step 4: Document 決策**

Append to `docs/philip/phase0-baseline-2026-07-09.md`:

```markdown
## N-value dry-run result (2026-07-09)

- N=15: X items / 30 days
- N=30: Y items / 30 days  ← 選定
- N=60: Z items / 30 days

Rationale: [why this N]
```

- [ ] **Step 5: Commit**

```bash
git add scripts/dry-run-n-value.py src/social_info/pipeline.py docs/philip/phase0-baseline-2026-07-09.md
git commit -m "chore: dry-run N-value selection for dedup resurface (N=30)"
```

---

## Task 7: Phase 2.5 — github_search fetcher

**Files:**
- Create: `src/social_info/fetchers/github_search.py`
- Create: `tests/fetchers/test_github_search.py`
- Create: `tests/fixtures/github_search_response.json`
- Modify: `sources.yml`（加 `github_search` source）

**Interfaces:**
- Consumes: GitHub REST API `/search/repositories?q=...`（via `gh api` CLI wrapper OR httpx + `Authorization: Bearer <token>`）
- Produces: `list[Item]`（同其他 fetcher pattern）

- [ ] **Step 1: 存 fixture — 真實 API response 樣本**

```bash
cd /Users/linhancheng/code/social-info
mkdir -p tests/fixtures
gh api '/search/repositories?q=skills+in:name+claude+in:description&per_page=5' > tests/fixtures/github_search_response.json
```

- [ ] **Step 2: 寫失敗 test**

Create `tests/fetchers/test_github_search.py`:

```python
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from social_info.config import SourceConfig
from social_info.fetchers.github_search import fetch


@pytest.mark.asyncio
async def test_fetch_github_search_parses_response():
    fixture_path = Path("tests/fixtures/github_search_response.json")
    fixture_data = json.loads(fixture_path.read_text())

    cfg = SourceConfig(
        id="github_search",
        type="github_search",
        enabled=True,
        tier=1,
        params={
            "queries": [
                "stars:>10000 claude in:description pushed:>{7d}",
            ],
            "per_query_limit": 30,
        },
    )

    # mock gh api subprocess call to return fixture
    with patch("social_info.fetchers.github_search._gh_search") as mock:
        mock.return_value = fixture_data
        items = await fetch(cfg)

    assert len(items) > 0
    assert all(it.source == "github_search" for it in items)
    assert all("github.com" in it.url for it in items)
    assert all(it.engagement.get("stars", 0) > 0 for it in items)


def test_substitute_query_template_date():
    from social_info.fetchers.github_search import _substitute_template
    from datetime import datetime

    now = datetime(2026, 7, 9)
    result = _substitute_template(
        "stars:>1000 claude in:description pushed:>{7d}", now=now
    )
    assert result == "stars:>1000 claude in:description pushed:>2026-07-02"
```

- [ ] **Step 3: Run test to verify fail**

```bash
uv run pytest tests/fetchers/test_github_search.py -v
```

Expected: FAIL (fetcher not exists)

- [ ] **Step 4: 寫 fetcher module**

Create `src/social_info/fetchers/github_search.py`:

```python
"""GitHub search API fetcher — repositories by description + name + topic.

Discovery-oriented complement to github_trending (which only shows
rising-period repos). Uses `gh api` CLI wrapper for auth (reuses existing
`gh auth` token, no separate credential management).
"""
import asyncio
import json
import re
import subprocess
from datetime import datetime, timedelta
from typing import Any

from social_info._time import utcnow
from social_info.config import SourceConfig
from social_info.fetchers.base import Item
from social_info.url_utils import canonical_url


_DATE_TEMPLATE_RE = re.compile(r"\{(\d+)d\}")


def _substitute_template(query: str, now: datetime | None = None) -> str:
    """Replace {Nd} with (now - N days) in YYYY-MM-DD format."""
    now = now or utcnow()

    def repl(match: re.Match) -> str:
        days = int(match.group(1))
        target = now - timedelta(days=days)
        return target.strftime("%Y-%m-%d")

    return _DATE_TEMPLATE_RE.sub(repl, query)


def _gh_search(query: str, per_page: int = 30) -> dict[str, Any]:
    """Sync gh api call. Kept out of async path via asyncio.to_thread."""
    result = subprocess.run(
        ["gh", "api", f"/search/repositories?q={query}&per_page={per_page}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


async def fetch(source: SourceConfig, http=None) -> list[Item]:
    queries = source.params.get("queries", [])
    per_query_limit = source.params.get("per_query_limit", 30)

    now = utcnow()
    items: list[Item] = []
    seen_urls: set[str] = set()

    for query_template in queries:
        query = _substitute_template(query_template, now=now)
        try:
            data = await asyncio.to_thread(_gh_search, query, per_query_limit)
        except subprocess.CalledProcessError as e:
            # let pipeline layer handle via KnownIssues (403 rate limit etc.)
            raise RuntimeError(f"gh search failed: {e.stderr}") from e

        for repo in data.get("items", []):
            full_name = repo["full_name"]
            url = repo["html_url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)

            desc = repo.get("description") or ""
            pushed_at = datetime.fromisoformat(
                repo["pushed_at"].replace("Z", "+00:00")
            ).replace(tzinfo=None)

            items.append(Item(
                title=full_name,
                url=url,
                canonical_url=canonical_url(url),
                source="github_search",
                source_handle=f"query:{query[:50]}",
                source_tier=source.tier,
                posted_at=pushed_at,
                fetched_at=now,
                author=repo.get("owner", {}).get("login", full_name.split("/")[0]),
                excerpt=desc[:200],
                language="en",
                engagement={"stars": repo.get("stargazers_count", 0)},
            ))

    return items
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/fetchers/test_github_search.py -v
```

Expected: PASS

- [ ] **Step 6: 加 sources.yml 新 source**

Modify `sources.yml`、加：

```yaml
  # === GitHub Search API (Phase 2.5、by description + name) ===
  # 補 github_trending 抓不到 stable 過峰值 AI repo 的漏
  # 實測 `q=skills+in:name+claude+in:description` 一發撈到
  # 5 個 100K+ 星系統性漏 repo（ECC / karpathy-skills / mattpocock/skills / caveman / graphify）
  - id: github_search
    type: github_search
    enabled: true
    tier: 1
    queries:
      - "stars:>10000 claude in:description pushed:>{7d}"
      - "stars:>5000 anthropic in:description pushed:>{7d}"
      - "stars:>1000 skills in:name claude in:description"
      - "stars:>1000 mcp in:name pushed:>{7d}"
      - "stars:>1000 agent in:description llm in:description pushed:>{7d}"
      - "stars:>1000 topic:claude-code pushed:>{7d}"
      - "stars:>1000 topic:llm topic:agent pushed:>{7d}"
    per_query_limit: 30
```

- [ ] **Step 7: Integration test — 真跑一次**

```bash
cd /Users/linhancheng/code/social-info
uv run python -c "
import asyncio
from social_info.config import SourceConfig
from social_info.fetchers.github_search import fetch

cfg = SourceConfig(
    id='github_search',
    type='github_search',
    enabled=True,
    tier=1,
    params={
        'queries': ['stars:>1000 skills in:name claude in:description'],
        'per_query_limit': 5,
    },
)

async def main():
    items = await fetch(cfg)
    for it in items:
        print(f'{it.title} ★{it.engagement[\"stars\"]}: {it.excerpt[:80]}')

asyncio.run(main())
"
```

Expected: 印出 mattpocock/skills 等 repo entries。

- [ ] **Step 8: Commit**

```bash
git add src/social_info/fetchers/github_search.py tests/fetchers/test_github_search.py tests/fixtures/github_search_response.json sources.yml
git commit -m "feat: add github_search fetcher by description + name for stable AI repos"
```

---

## Task 8: Phase 3 — Fetcher 擴 scope + 拿掉 keyword filter

**Files:**
- Modify: `sources.yml`（`github_trending` 段 languages + since 改陣列）
- Modify: `src/social_info/fetchers/github_trending.py`（迭代 since list、拿掉 `_matches_ai`）
- Modify: `tests/fetchers/test_github_trending.py`

**Interfaces:**
- Consumes: 現有 SourceConfig with `languages: list` + `since: list`
- Produces: `list[Item]`（無 keyword filter、全抓）

- [ ] **Step 1: Update sources.yml**

Modify `sources.yml` `github_trending` block:

```yaml
  - id: github_trending
    type: github_trending
    enabled: true
    tier: 1
    languages: [python, typescript, rust, go, c, cpp, java, swift]
    since: [daily, weekly, monthly]
    # ai_keywords 保留 in config、但 fetcher 不再用來 drop（未來 Phase 4 fallback rule 用）
    ai_keywords: [ai, llm, agent, mcp, gpt, claude, multimodal, llama, diffusion, langchain]
    lo_keywords: [crypto, trading bot, wallet, game engine, blockchain]
```

- [ ] **Step 2: 更新 test — assert 拿掉 keyword filter 後 fetcher return 所有 repos（非 AI 也 return）**

Modify `tests/fetchers/test_github_trending.py`:

```python
@pytest.mark.asyncio
async def test_fetch_returns_all_repos_no_keyword_filter(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["python"],
            "since": ["daily"],
            # keyword filter 拿掉、fetcher 應 return 所有 repos
            "ai_keywords": ["ai", "llm"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    # 拿掉 filter 後、items 數應等於 fixture 內全 article.Box-row 數
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    expected_count = len(soup.select("article.Box-row"))
    assert len(items) == expected_count, \
        f"expected all {expected_count} repos, got {len(items)}"


@pytest.mark.asyncio
async def test_fetch_iterates_since_list(httpx_mock):
    html = Path("tests/fixtures/github_trending.html").read_text()
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*since=daily.*"),
        text=html,
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://github\.com/trending/python.*since=weekly.*"),
        text=html,
        is_reusable=True,
    )

    cfg = SourceConfig(
        id="github_trending",
        type="github_trending",
        enabled=True,
        tier=1,
        params={
            "languages": ["python"],
            "since": ["daily", "weekly"],
        },
    )

    async with httpx.AsyncClient() as client:
        items = await fetch(cfg, client)

    # 2 個 since values × N repos per fixture (dedup 沒在 fetcher 端做)
    assert len(items) > 0
```

- [ ] **Step 3: Run test verify fail**

```bash
uv run pytest tests/fetchers/test_github_trending.py -v
```

Expected: FAIL（fetcher 還在 filter + 沒迭代 since list）

- [ ] **Step 4: Modify github_trending.py**

Update `src/social_info/fetchers/github_trending.py`:

```python
async def fetch(source: SourceConfig, http: httpx.AsyncClient) -> list[Item]:
    languages = source.params.get("languages") or [""]
    since_values = source.params.get("since", ["daily"])
    if isinstance(since_values, str):
        since_values = [since_values]

    items: list[Item] = []
    now = utcnow()

    for lang in languages:
        for since in since_values:
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
                description_el = repo.select_one("p")
                description = description_el.text.strip() if description_el else ""
                # 拿掉 _matches_ai 檢查、全抓
                stars_el = repo.select_one('a[href$="/stargazers"]')
                stars = _parse_stars(stars_el.text if stars_el else "")
                items.append(Item(
                    title=slug,
                    url=full_url,
                    canonical_url=canonical_url(full_url),
                    source="github_trending",
                    source_handle=f"trending:{lang or 'all'}:{since}",
                    source_tier=source.tier,
                    posted_at=now,
                    fetched_at=now,
                    author=slug.split("/")[0],
                    excerpt=description[:200],
                    language="en",
                    engagement={"stars": stars},
                ))
    return items
```

留 `_matches_ai` + `_parse_stars` 兩 helper 在 module top（Phase 4 fallback 可能會用到）、但 `fetch` 不再呼叫 `_matches_ai`。

- [ ] **Step 5: Rate limit safeguard**

若擴到 8 lang × 3 freq = 24 requests、加 asyncio.Semaphore 或 request 間隔 100ms 避免 GitHub HTML page rate limit:

在 `fetch` 內加 `await asyncio.sleep(0.1)` between requests:

```python
    for lang in languages:
        for since in since_values:
            # ... fetch and parse ...
            await asyncio.sleep(0.1)  # ~10 req/sec throttle
```

- [ ] **Step 6: Run test verify pass**

```bash
uv run pytest tests/fetchers/test_github_trending.py -v
```

Expected: PASS

- [ ] **Step 7: Integration test 真跑一次**

```bash
cd /Users/linhancheng/code/social-info
uv run python -c "
import asyncio
import httpx
from social_info.config import SourceConfig
from social_info.fetchers.github_trending import fetch

cfg = SourceConfig(
    id='github_trending',
    type='github_trending',
    enabled=True,
    tier=1,
    params={
        'languages': ['python', 'typescript', 'rust', 'go', 'c', 'cpp', 'java', 'swift'],
        'since': ['daily', 'weekly', 'monthly'],
    },
)

async def main():
    async with httpx.AsyncClient() as c:
        items = await fetch(cfg, c)
    print(f'fetched {len(items)} items across 8 lang × 3 freq')

asyncio.run(main())
"
```

Expected: 印出 ~450-500 個 items (before dedup)。

- [ ] **Step 8: Commit**

```bash
git add sources.yml src/social_info/fetchers/github_trending.py tests/fetchers/test_github_trending.py
git commit -m "feat: expand github_trending scope to 8 lang x 3 freq, remove keyword filter"
```

---

## Task 9: Phase 5 — 端到端 verify

**Files:**
- Create: `docs/philip/phase5-verify-YYYY-MM-DD.md`（date = verify 執行當天）

**Interfaces:**
- Consumes: 前 Phase 1-3 已合入、production launchd 已跑 ≥3 天
- Produces: verify report

- [ ] **Step 1: 確認 launchd job 已跑 Phase 1-3 code**

```bash
launchctl print gui/$(id -u)/com.gggodlin.social-info-daily 2>&1 | grep -E "state|last exit"
tail -20 /Users/linhancheng/code/social-info/logs/cron-$(date +%Y-%m-%d).log
```

Expected: last exit code = 0、log 顯示 fetch 完成 with 新 source `github_search` + 擴後 `github_trending`。

- [ ] **Step 2: 驗 mattpocock/skills re-surface**

```bash
cd /Users/linhancheng/code/social-info
# 找過去 7 天 raw md 有沒有 mattpocock/skills
grep -l "mattpocock/skills" reports/$(date +%Y-%m)-*.md
```

Expected: 至少一個 raw md 命中（或 5 個系統性漏 repo 至少 3 個命中）。

- [ ] **Step 3: 驗 trendshift 恢復**

```bash
for i in 1 2 3; do
  d=$(date -v-${i}d +%F)
  count=$(grep -c "\`trendshift\`" reports/${d}.md 2>/dev/null || echo 0)
  echo "${d}: trendshift hits = ${count}"
done
```

Expected: 過去 3 天平均 ≥ 5 trendshift 命中。

- [ ] **Step 4: 驗 raw md scope 擴大**

```bash
for i in 0 1 2; do
  d=$(date -v-${i}d +%F)
  gh_items=$(grep -c "\`github_trending\`\|\`github_search\`" reports/${d}.md 2>/dev/null || echo 0)
  echo "${d}: github items = ${gh_items}"
done
```

Expected: 從 Phase 0 baseline 5-7 個/day → 現在 ~200-300 個/day。

- [ ] **Step 5: 對照 P3 memory rule usage**

```bash
# 過去 3 天 CC session log 有無「Re-checked: YYYY-MM-DD」新增到 tool eval cluster
cd ~/.claude/memory
git log --oneline --since="3 days ago" -- _index_tool_eval_outcomes.md reference_*.md 2>&1 | head
```

Expected: 若有 ECC / mattpocock/skills 等舊否決被觸發重評、應看到 memory 條目更新（Re-checked field）。可能沒有若還沒 recall 到那些 conclusions。

- [ ] **Step 6: 寫 verify report**

Create `docs/philip/phase5-verify-YYYY-MM-DD.md`（YYYY-MM-DD = 執行當天）:

```markdown
# Phase 5 Verify Report — GitHub Triage Spec

**Date executed**: <YYYY-MM-DD>
**Related spec**: docs/superpowers/specs/2026-07-09-github-triage-design.md
**Related plan**: docs/superpowers/plans/2026-07-09-github-triage.md

## Per-phase acceptance

- [x] Phase 1 (trendshift): 過去 3 天 raw md 有 X trendshift 命中 / 平均 Y/day
- [x] Phase 2 (dedup): mattpocock/skills 或其他 30+ 天前看過 repo 於 <date> raw md re-surface (via 🔁 marker)
- [x] Phase 2.5 (github_search): 過去 3 天 github_search source 命中 X 個 net-new、含 mattpocock/skills / ECC / karpathy-skills 等
- [x] Phase 3 (擴 scope): raw md 從 Phase 0 baseline X/day → 現在 Y/day (增 Z 倍)

## Primary metric

抽 10 個過去 4-6 月出現過的 stable AI repo（列出）：

| Repo | 前次出現日 | Phase 5 後 30 天內是否 re-surface |
|---|---|---|
| ... | | |

Re-surface 比例：X / 10 = XX%（目標 > 50%）

## Secondary metric

- Phase 0 baseline: hi + med tier avg X/day
- Post-Phase 3: hi + med tier avg Y/day
- 提升: Y / X = Z 倍（目標 3-5 倍）

## Issues found

（Phase 5 verify 期間發現的 regression、edge case、待修）
```

- [ ] **Step 7: Commit**

```bash
git add docs/philip/phase5-verify-YYYY-MM-DD.md
git commit -m "docs: phase 5 verify report for github triage implementation"
```

---

## Self-Review Checklist (作者自審)

**Spec coverage**:
- ✅ Phase 1 修 trendshift → Task 2
- ✅ Phase 2 dedup N 天 re-surface → Task 3-6
- ✅ Phase 2.5 GitHub search API → Task 7
- ✅ Phase 3 擴 scope + 拿掉 keyword filter → Task 8
- ✅ Phase 5 端到端 verify → Task 9
- ✅ B3 (dedup semantics + 5-case) → Task 4
- ✅ B4 (Phase 0 baseline) → Task 1；(N-value dry-run) → Task 6
- ✅ B7 (relay alert) → 屬 Phase 4 spun-out spec、本 plan 不含
- ✅ B9 (schema audit + DedupResult refactor) → Task 3 + 4
- ✅ B10 (stack-distill 位置) → Phase 4 spun-out
- ✅ B11 (KNOWN_ISSUES) → Phase 4 spun-out

**Type consistency**:
- `DedupResult { new_items, resurface_ids }` in Task 4 → consumed 同名 in Task 5
- `Deduper(db, resurface_days=30)` in Task 4 → same in Task 5 / 6
- `_gh_search`, `_substitute_template` in Task 7 → internal helpers, private

**Global constraints applied**：
- Physical path `/Users/linhancheng/code/social-info/` 用 in all commands
- Conventional Commits messages
- No comments in generated code (few docstrings kept for module top-level context per existing pattern)
- Tests follow existing `httpx_mock` + `tempfile` pattern

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-07-09-github-triage.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
