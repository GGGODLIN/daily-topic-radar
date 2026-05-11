# HN Comments + GitHub Enrichment Design

> Date: 2026-05-11
> Status: Approved by user
> Implements: stage-1 HN comments fetcher extension + stage-2 GitHub repo enrichment protocol

## Motivation

Today's pipeline gives stage-2 digest only metadata for two high-signal source families:

- **HN entries** — fetcher pulls Algolia metadata only (`excerpt=""`); the actual signal lives in the comments thread (Ask HN / Show HN discussions, technical corrections under news links, paywall-bypass commentary like the FT Microsoft story on 2026-05-10).
- **GitHub repos** — fetchers (`github_trending`, `trendshift`) write only `description[:200]`. Digest stage has to guess whether a trending repo is worth `clone` / `star` / `skip` from a one-line description, which inflates risk of mis-framing or skipping interesting projects.

Two-layer fix:

1. **HN comments** become persistent (stage-1, every daily run, written to DB + raw md).
2. **GitHub repo metadata + README** stays on-demand (stage-2, only when an entry is a candidate for the digest).

## Non-goals

- No HN deep-thread recursion (only top-5 top-level comments).
- No GitHub repo enrichment at stage-1 (would inflate every raw md by 100KB+ when most repos never reach the digest).
- No GitHub URL enrichment for arbitrary mentions inside Reddit threads / 中文 sources (only when the entry itself has a github.com URL and is a digest candidate).
- No changes to Apify-based fetchers, Twitter/X, Threads, or any other source.

## Part A — HN comments (stage-1)

### A1. Fetch path

Use HN's official Firebase API, not Algolia:

```
GET https://hacker-news.firebaseio.com/v0/item/<story_id>.json
→ returns {"kids": [comment_id, ...], ...}

For each of kids[:5]:
GET https://hacker-news.firebaseio.com/v0/item/<comment_id>.json
→ returns {"by": str, "text": str (HTML), "time": int (unix), "deleted": bool, "dead": bool, ...}
```

**Why Firebase, not Algolia**: HN's UI comment order (based on internal vote/flag/age ranking) is exposed via `kids` array on Firebase. Algolia has no `tags=comment,story_<id>` ranking field — only `search_by_date` or full-text relevance, neither of which match "top comments".

**Per-story cost**: Algolia does not expose `kids` (its hit schema is `{title, url, author, points, num_comments, ...}`), so Firebase `item/<story_id>.json` is mandatory to get the kid list. Then 5 Firebase `item/<comment_id>.json` calls for the comments themselves.

```
Existing Algolia call  → list of stories with metadata (1 call total, unchanged)
For each story:
  Firebase /item/<story_id>.json  → kids array (1 call per story)
  Firebase /item/<kid_id>.json    × min(5, len(kids))  → comment payloads
```

Total per story: 1 + up to 5 = 6 HTTP calls. With today's 9 stories: ~54 calls. Firebase has no documented rate limit on read-only public data.

### A2. Error tolerance

- A failed `item/<id>.json` call (network error, deleted comment, dead comment) → skip that comment, do not fail the story.
- A `deleted: true` or `dead: true` comment → skip (don't include in output).
- A failed `item/<story_id>.json` call → skip comments for that story, story still surfaces in raw md with empty comments list.
- HTML in comment `text`: strip via existing `_TAG_RE` pattern in `wewe_rss.py` (reuse the helper, don't copy).

### A3. Item dataclass change

Add to `src/social_info/fetchers/base.py`:

```python
@dataclass
class Item:
    # ... existing fields ...
    comments: list[dict[str, str]] = field(default_factory=list)
```

Each comment dict:
```python
{
    "author": str,          # HN username (the "by" field)
    "text": str,            # plain text, HTML stripped, trimmed to 300 chars
    "posted_at": str,       # ISO timestamp
}
```

`to_db_row()` serializes as `comments_json` (JSON string).

### A4. DB schema migration

In `src/social_info/db.py`:

- Add `comments_json TEXT` to `CREATE TABLE items` (for fresh DBs).
- Add migration block in `init_schema()` mirroring the existing `error_class` / `attempts` pattern:

```python
items_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(items)")}
if "comments_json" not in items_cols:
    self.conn.execute("ALTER TABLE items ADD COLUMN comments_json TEXT")
```

- Update `insert_item()` — no code change needed, it uses `dict.keys()` pattern.
- Update `items_for_date()` — no change, returns whole row including the new column.

In `src/social_info/__main__.py` (the row-to-Item rehydration around line 33):

```python
comments=json.loads(row.get("comments_json") or "[]"),
```

### A5. raw md rendering

Modify `src/social_info/markdown.py:render_item()`:

After the existing `excerpt` block, before `also_appeared_in`:

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

Format example in raw md output:

```
### [Ask HN: Will low quality AI customer support be the new normal?](...)

`hn:front_page` · T1 · 2026-05-10 20:57 UTC · en · by 0-bad-sectors · 💬 2 · ▲ 6

> 💬 Top comments:
> - **@user1**: I've already given up on calling support lines that route through AI. The pattern is always the same: 3 minutes of natural-sounding nonsense, then escalation to a human who...
> - **@user2**: New normal? It already is the new normal at every B2C company I've called this year. The signal is when "speak to a human" stops being honored.

---
```

### A6. Tests

`tests/test_hn_comments.py` (new file):

- `test_fetch_story_with_comments_happy_path` — mock Algolia + Firebase, assert Item has 5 comments with expected shape
- `test_fetch_story_with_deleted_comment_skips_it` — mock returns one `{"deleted": true}` kid → assert that comment not in Item.comments
- `test_fetch_story_with_failed_kid_call_continues` — one kid raises HTTPError → assert other 4 comments still present
- `test_fetch_story_with_no_kids` — `kids` field missing → assert `Item.comments == []`
- `test_comment_text_html_stripped` — comment text `"<p>Hello <a href=...>world</a></p>"` → assert clean text in Item
- `test_comment_text_trimmed_to_300_chars`

Existing tests (`test_classify_error.py`, `test_known_issues.py`) unaffected.

### A7. Daily run cost impact

- API calls: +54/day (or however many HN stories; usually 5-15)
- Wall clock: +5-10 seconds (Firebase responses are small JSON)
- raw md size: +30-50 KB/day (5 comments × 300 chars × 9 stories ≈ 13.5 KB raw, plus formatting)
- DB size: comparable growth
- git diff: each daily commit will show new comment data; commits stay readable

## Part B — GitHub stage-2 enrichment (no code)

This is a **protocol update**, not a code change. Memory entry encodes the rules so future digest runs follow them automatically.

### B1. Trigger conditions

Enrich a github.com URL when **all** of:

1. URL is `https://github.com/<owner>/<repo>` (not a sub-path like `/issues/123` or `/blob/...`)
2. The entry is a **candidate for inclusion in today's digest** — meaning I've already decided this entry has enough signal to write about. Do NOT enrich every github URL on the raw md (would waste tokens on entries I'll skip).
3. The entry's source is one of: `github_trending`, `trendshift`, `hn`, `reddit`, or a 中文 source where the link target is github.com.

Skip enrichment if:
- URL points to a sub-path (issue / PR / file / release page) — these have their own content already.
- Repo already has rich excerpt that answers the digest question — e.g., Trendshift sometimes provides a full sentence; if it's enough to make the clone/star/skip call, don't burn tokens.

### B2. Fetch sequence

For each qualifying URL `https://github.com/<owner>/<repo>`:

```bash
# Step 1: metadata
gh repo view <owner>/<repo> --json description,topics,stargazerCount,forkCount,pushedAt,primaryLanguage,licenseInfo,defaultBranchRef
```

```bash
# Step 2: README first 1500 chars
gh api repos/<owner>/<repo>/contents/README.md --jq '.content' | base64 -d | head -c 1500
# OR
curl -sL https://raw.githubusercontent.com/<owner>/<repo>/HEAD/README.md | head -c 1500
```

```bash
# Step 3: topics-aware positioning docs (only if step 2 didn't surface architecture context)
# Probe in this order, stop after first 200 OK:
gh api repos/<owner>/<repo>/contents/ARCHITECTURE.md
gh api repos/<owner>/<repo>/contents/docs/ARCHITECTURE.md
gh api repos/<owner>/<repo>/contents/docs/QUICKSTART.md
gh api repos/<owner>/<repo>/contents/docs/GETTING_STARTED.md
gh api repos/<owner>/<repo>/contents/GETTING_STARTED.md
# Take first 500 chars of whichever returns content
```

### B3. Token budget guidance

Per repo: ~1500 (README) + ~500 (positioning doc) + ~200 (metadata) = ~2200 chars ≈ ~800 tokens.

If digest stage enriches 10-15 repos: +10-15k input tokens. Acceptable given digest already runs with full raw md (74KB ≈ ~25k tokens).

If running into context budget: cut README to 1000 chars first, drop positioning doc second.

### B4. Output format in digest

Continues using existing OSS schema from `feedback_digest_oss_trending_richness.md`:

> 簡介在前、判斷在後：1-2 sentence summary based on enriched data → vs mainstream 差別 → 跟使用者 stack 契合度 → 成熟度（star count, last push age, license）→ 建議動作（clone / star / skip）

The enrichment data feeds the summary + maturity assessment, not a new format.

### B5. Memory artifact

Create `reference_github_stage2_enrichment.md` capturing the trigger conditions, fetch sequence, and token budget. Update `MEMORY.md` index.

## Risks

1. **Firebase API throttling**: not documented for read-only HN data but possible. Mitigation: existing `httpx.AsyncClient` already has retry config in `fetchers/base.py`'s wrapper; per-comment failure is non-fatal.

2. **HN comment quality varies**: top-5 by HN ranking may include low-signal jokes or "deleted" placeholders. Acceptable — stage-2 digest will filter.

3. **GitHub README enrichment for forks**: `gh repo view` reports the fork's metadata, not parent's. For trending lists this matters less since active repos are usually canonical, but worth checking for surprising entries.

4. **README localization**: some repos have README.zh.md / README.ja.md but no English README.md, or vice versa. Step 2 uses `README.md` (default branch's). Acceptable — if missing, step 2 returns 404 and we fall through to step 3 / skip.

5. **Schema migration safety**: `ALTER TABLE ADD COLUMN` is safe (SQLite supports it idempotently via the existing migration block pattern). New column is nullable, no backfill needed.

## Acceptance criteria

- Daily run on 2026-05-12 includes HN comments in raw md without manual intervention.
- DB has `comments_json` column populated for HN items, NULL for non-HN items.
- raw md formatting for HN items shows `> 💬 Top comments:` block when comments exist.
- `tests/test_hn_comments.py` passes (6 tests).
- No regression on `tests/test_classify_error.py` or `tests/test_known_issues.py`.
- `KNOWN_ISSUES.md` does not gain new entries from this change.
- Memory entry `reference_github_stage2_enrichment.md` exists and is referenced in `MEMORY.md`.
- Next digest produced after this change uses the protocol for github.com URLs and shows enrichment data in the OSS / Trending section entries.

## Out of scope (for follow-up)

- HN comment-level dedup with story (currently no overlap, but if same kid_id appears as a story it would double-count).
- Reddit comments enrichment (Reddit fetcher already pulls `selftext`; comments-thread fetch is a future enhancement).
- GitHub repo enrichment at stage-1 for Trending + Trendshift (would be the natural next step; deferred to keep this scope small).
