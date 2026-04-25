---
name: Daily AI Aggregator
description: 每日自動聚合各大社群 AI 技術話題的 raw aggregator，產出結構化 markdown 給下游 Claude 整理
type: design-spec
created: 2026-04-26
status: draft
framework: superpowers
---

# Daily AI Aggregator — Design Spec

## 1. 使用者與用途

- **主要消費者**：個人單人使用
- **主要用途**：每天 10:00 Asia/Taipei 後消費前一日 AI 圈動態
- **真實工作流（兩段式）**：
  1. **Pipeline 段（自動）**：GitHub Actions cron 每日 09:00 Asia/Taipei 跑，產出當日 markdown 並 commit 進 repo
  2. **Claude 段（手動）**：使用者開 Claude Code，請 Claude 透過 gh CLI 拉當日 `.md`，由 Claude 做個人化篩選 / 排序 / 摘要 / 回報

## 2. 設計原則

### 2.1 Raw vs Smart 分工

Pipeline **不**做：LLM 摘要、評分、cluster、排序、語意去重、主動 crawl 原文全文。所有 judgment 交給下游 Claude。

判準：「不確定要不要加 metadata」時預設加；「不確定要不要過濾」時預設不過濾。冗餘給 Opus 看是 OK 的，過濾掉的東西救不回來。

### 2.2 紀錄完善 > 過濾精煉

- Failed source 紀錄在 `fetch_runs` 表 + `.md` 檔頭，讓下游 Claude 知道資料缺口
- 跨來源同現紀錄保留在 `also_appeared_in` 欄位
- Tier 標籤、語言、互動數等所有可得 metadata 都寫進去

### 2.3 介面就是 Claude Code + gh

- 不做 email / Slack / Discord / web UI 通知
- 一切以 GitHub repo 為事實來源
- 使用者透過 Claude Code 對話 + gh CLI 來查看 / 管理

## 3. 內容範圍

### 3.1 關注切片（要）

- **Agent / Tool use / Coding**：agentic framework、coding agent、MCP、tool integration、orchestration
- **Infra / DevX / 商業動態**：inference、serving、AI startup 動態、fundraising、產品落地
- **多模態 / 生成式內容**：圖像、影片、語音、Veo / Sora / Suno 類

### 3.2 排除（不要）

- 學術 paper / research（arXiv、Hugging Face papers）
- 機器之心非 Pro 部分
- Crypto / DeFAI / 加密代理經濟
- 純 benchmark 跑分帳號

### 3.3 語言

- 英文
- 繁體中文（台灣）
- 簡體中文（中國）

## 4. Source 來源清單

### 4.1 英文主力（無需鑑權）

| Source ID | Type | 說明 | Tier |
|---|---|---|---|
| `hn` | hn_algolia | HN front_page，title 含任一 keyword（OR 邏輯）：LLM、AI、agent、MCP、coding agent、multimodal、Claude、GPT、Cursor | 1 |
| `reddit_localllama` | reddit | r/LocalLLaMA top.json?t=day | 1 |
| `reddit_claudeai` | reddit | r/ClaudeAI top.json?t=day | 1 |
| `reddit_openai` | reddit | r/OpenAI top.json?t=day | 1 |
| `reddit_singularity` | reddit | r/singularity top.json?t=day | 2 |
| `reddit_machinelearning` | reddit | r/MachineLearning top.json?t=day（過濾 paper flair） | 2 |
| `github_trending` | github_trending | daily Python / TypeScript / Rust + AI 過濾 | 1 |
| `product_hunt` | product_hunt | yesterday top AI products GraphQL | 2 |
| `huggingface_models` | huggingface | trending models API | 2 |
| `huggingface_spaces` | huggingface | trending spaces API | 2 |

### 4.2 英文 Lab / Media（純 RSS）

| Source ID | URL | Tier |
|---|---|---|
| `anthropic_blog` | https://www.anthropic.com/news.rss | 1 |
| `openai_blog` | https://openai.com/blog/rss.xml | 1 |
| `google_ai_blog` | https://blog.google/technology/ai/rss/ | 2 |
| `mistral_blog` | https://mistral.ai/news/rss | 2 |
| `xai_blog` | https://x.ai/news/rss | 2 |
| `techcrunch_ai` | https://techcrunch.com/category/artificial-intelligence/feed/ | 2 |
| `venturebeat_ai` | https://venturebeat.com/category/ai/feed/ | 2 |

### 4.3 X / Twitter（透過 twitterapi.io）

**Tier 1（20 個帳號）**：

`@karpathy`、`@simonw`、`@swyx`、`@AlexAlbert__`、`@AnthropicAI`、`@sama`、`@gdb`、`@aravsrinivas`、`@hwchase17`、`@logangkilpatrick`、`@c_valenzuelab`、`@itsPaulAi`、`@op7418`、`@dotey`、`@steipete`、`@emollick`、`@deepseek_ai`、`@kaifulee`、`@Cursor`、`@Replit`

**Tier 2（5 個待 spot check）**：

`@saj_adib`、`@chaseleantj`、`@icreatelife`、`@andrewallenxo`、`@iwoszapar`

抓取規格：每天每帳號抓 last 24h tweets，limit 10 條 / 帳號。

### 4.4 Threads（官方 API）

**Keyword search（`search_type=TOP`、`since=24h`）**：

- 英文：`Claude`、`Cursor`、`MCP`、`coding agent`、`Codex`、`OpenAI`、`Anthropic`、`Gemini`、`AI startup`、`Sora`、`Veo`、`Suno`、`Midjourney`、`Runway`
- 中文：`AI 工具`、`生成式 AI`、`AI 應用`

**Topic tag search（`search_mode=TAG`、`search_type=TOP`）**：

- `AI`、`Generative AI`、`AI Tools`（具體 tag 是否 valid 要實際打 API 試，第一輪 PoC 把候選一個個試一下，留下有結果的）

**User handles**：骨架預留欄位但 list 為空（disabled）。等使用者之後在 Threads 看到值得追的人再加。

### 4.5 中文（中國）

| Source ID | Method | Status |
|---|---|---|
| `zhihu_hot` | RSSHub `/zhihu/hot`（公開 instance） | enabled |
| `weibo_search_ai` | RSSHub `/weibo/search/AI`（公開 instance） | enabled |
| `wechat_qbitai` | wewe-rss 自部署（量子位） | **disabled (PoC 第一個月)** |
| `wechat_appso` | wewe-rss 自部署（APPSO） | **disabled** |
| `wechat_36kr_ai` | wewe-rss 自部署（36 氪 AI） | **disabled** |
| `wechat_jqzx_pro` | wewe-rss 自部署（機器之心 Pro） | **disabled** |

### 4.6 中文（台灣）

| Source ID | URL | Status |
|---|---|---|
| `ithome_ai` | https://www.ithome.com.tw/rss/category/ai | enabled |
| `inside_ai` | https://www.inside.com.tw/feed/ai | enabled |
| `dcard_engineer` | RSSHub `/dcard/posts/工程師`（公開 instance） | enabled |

**砍掉**：PTT Tech_Job / Soft_Job、Dcard 軟體工程板（密度低、Threads 取代）。

## 5. 輸出規格

### 5.1 檔案位置

- `reports/YYYY-MM-DD.md`（按 Asia/Taipei 日期命名）
- 永久保留（不做 weekly / monthly rollup）

### 5.2 檔案級結構（按 platform 分群）

```markdown
# AI Daily Digest — 2026-04-26

> generated_at: 2026-04-26T09:00:00+08:00 (Asia/Taipei)
> total_items: 67  |  sources_active: 12  |  sources_failed: 1
> failures:
>   - dcard_engineer: timeout after 30s

---

## X / Twitter (18 items)

[items, sorted by tier asc, then engagement desc]

## Threads (8 items)

[...]

## Reddit (12 items)

## Hacker News (5 items)

## GitHub Trending (8 items)

## Product Hunt / HuggingFace (4 items)

## Lab Blogs & Releases (3 items)

## English Tech Media (5 items)

## 中文 / 微信 + 知乎 (10 items)

## 中文 / 台灣 (3 items)
```

### 5.3 單條 item 渲染

```markdown
### [Title here](https://example.com/url)

`x:@karpathy` · T1 · 2026-04-26 14:23 UTC · en · ♥ 1.2K · 💬 84

> excerpt 前 200 字（推文全文 / RSS description / HN 自帶內文）...

---
```

### 5.4 規模目標

- Dedup 前約 90-130 條 / 日
- Dedup 後約 50-70 條 / 日
- 檔案大小約 5-15 KB / 日

## 6. Per-item Schema

```yaml
- id: string             # SHA1(canonical_url)，主 key
  title: string
  url: string            # 原始 URL
  canonical_url: string  # 去 utm / fbclid / ref / source 等 tracking 參數
  source: string         # 平台大類: "x" / "reddit" / "hn" / "rss" / "rsshub" / ...
  source_handle: string  # 子來源: "@karpathy" / "r/LocalLLaMA" / "anthropic_blog"
  source_tier: int       # 1 / 2
  posted_at: ISO8601
  fetched_at: ISO8601
  author: string
  excerpt: string        # ≤ 200 字（RSS desc / 推文全文 / HN text）
  language: string       # "en" / "zh-TW" / "zh-CN"
  engagement:
    likes: int
    comments: int
    score: int           # 平台特定（HN points / Reddit upvotes / GitHub stars）
  also_appeared_in:      # L2 dedup 的同事件記錄
    - source: string
      source_handle: string
      url: string
```

**注意**：不主動 crawl 原文全文，excerpt 只用平台 / RSS 自帶內容。

## 7. Dedup 策略

### 7.1 L1 — Canonical URL 確定性過濾（必做）

- 剝除 tracking 參數（`utm_*`、`fbclid`、`ref`、`source`）
- `SHA1(canonical_url)` → `items.id`
- 看到 id 已存在就 skip

### 7.2 L2 — Normalized Title Hash（必做）

- Normalize 步驟：
  1. Unicode NFKC normalize
  2. lowercase（含中文不影響）
  3. 移除所有 Unicode punctuation（categories `P*`，含中英文標點）
  4. 多個 whitespace（含全形空格）合併為單個 ASCII space
  5. trim 前後空白
- `SHA1(normalized_title)` → `items.title_hash`
- 同 title_hash 多條時：保留 tier 最高的，其他併進 `also_appeared_in`

### 7.3 L3 — Vector / SemHash 語意去重（不做）

理由：對個人 30-80 條 / 日規模 over-engineering。跨語系語意去重會丟失中文評論的觀點。交給下游 Claude 自行判斷。

### 7.4 限制承認

L2 只能抓「相同標題」轉貼，「相似標題改寫」（例：「OpenAI Releases GPT-5」vs「OpenAI just dropped GPT-5」）抓不到。第一個月跑滿後再評估誤差，決定是否加 fuzzy match（normalized token Jaccard ≥ 0.7）。

## 8. SQLite Schema

```sql
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
CREATE INDEX idx_items_title_hash ON items(title_hash);
CREATE INDEX idx_items_posted_at ON items(posted_at);

CREATE TABLE fetch_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  status TEXT,                   -- "ok" / "failed" / "timeout"
  items_fetched INTEGER,
  error TEXT
);
CREATE INDEX idx_fetch_runs_source ON fetch_runs(source);
```

### 8.1 保留策略

- `items`：永久（用於跨日去重）。每天 ~50 條 × 365 天 ≈ 18K rows，幾 MB，OK。
- `fetch_runs`：滾動 90 天（手動 prune script，每月跑一次）。

### 8.2 儲存位置

- `state.db` commit 進 repo
- 每次 workflow start `git pull`、結束 `git commit + push`
- 重複跑同一天 → L1 dedup 保證冪等

## 9. 執行環境

### 9.1 Cron

- GitHub Actions `daily.yml`
- Cron expression：`0 1 * * *`（UTC 01:00 = 09:00 Asia/Taipei，台灣無夏令時）
- 加 `workflow_dispatch` 手動觸發
- 加可選 `--date YYYY-MM-DD` 參數可指定日期重跑

### 9.2 Runner Stack

- `ubuntu-latest`
- Python 3.12 via `uv`（uv.lock 鎖版本）
- 主要 lib：
  - `httpx`（async HTTP）
  - `feedparser`（RSS parsing）
  - `PyYAML`（config）
  - `sqlite3`（stdlib）
  - `beautifulsoup4`（GitHub Trending HTML parsing）

### 9.3 Secrets（GitHub Repository Secrets）

| Secret | 來源 |
|---|---|
| `TWITTERAPI_IO_KEY` | twitterapi.io 註冊（送 100K credits） |
| `THREADS_APP_ID` | Meta Developer Console |
| `THREADS_APP_SECRET` | 同上 |
| `THREADS_ACCESS_TOKEN` | OAuth flow，refresh 邏輯放 workflow |
| `THREADS_REFRESH_TOKEN` | 同上 |
| `RSSHUB_INSTANCE_URL` | 預設 `https://rsshub.app`；之後 self-host 換 |
| `WEWE_RSS_URL` | 暫不設定（PoC 階段 disabled） |
| `WEWE_RSS_KEY` | 同上 |

### 9.4 Error Handling

- 每個 fetcher 是 independent async function，包在 try/except
- 任一 fetcher 失敗只該 source 缺資料，整體 pipeline 繼續
- 失敗紀錄寫進 `fetch_runs` 表 + `.md` 檔頭 `failures` 區塊

### 9.5 補跑邏輯

**不自動補跑**。理由：對 raw aggregator 漏一天可接受，信號通常多日 persistent，且各平台 24h 視窗 API 也補不了昨天。提供 `workflow_dispatch` UI + `--date` 參數讓使用者手動觸發。

## 10. Repo 結構

```
social-info/
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── .env.example
├── sources.yml              ← 加減 source 的入口
├── state.db                 ← SQLite, commit in repo
├── reports/
│   └── YYYY-MM-DD.md
├── src/social_info/
│   ├── __main__.py          ← uv run python -m social_info
│   ├── config.py            ← 讀 sources.yml
│   ├── pipeline.py          ← fetch → dedup → write 主流程
│   ├── db.py                ← sqlite3 wrapper
│   ├── dedup.py             ← L1 + L2
│   ├── markdown.py          ← render item / file / failures 區塊
│   ├── url_utils.py         ← canonical URL
│   ├── health.py            ← 7 天各 source 成功率報表
│   └── fetchers/
│       ├── base.py          ← Item / FetchResult types
│       ├── hn.py
│       ├── reddit.py
│       ├── github_trending.py
│       ├── product_hunt.py
│       ├── huggingface.py
│       ├── rss.py           ← 通用 RSS（lab blogs / TechCrunch / iThome / INSIDE）
│       ├── rsshub.py        ← 通用 RSSHub call（zhihu hot / dcard / weibo）
│       ├── twitter.py       ← twitterapi.io
│       ├── threads.py       ← Meta Threads API
│       └── wewe_rss.py      ← 微信公眾號（PoC 階段 disabled，但程式碼仍寫好）
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   ├── test_dedup.py
│   ├── test_url_utils.py
│   ├── test_markdown.py
│   ├── test_db.py
│   └── fetchers/test_*.py
└── .github/workflows/
    ├── daily.yml            ← cron 09:00 Asia/Taipei + workflow_dispatch
    ├── test.yml             ← on push / PR
    └── smoke.yml            ← workflow_dispatch only，真打 API 測試
```

### 10.1 Dev 工作流

```bash
uv sync                                                # 安裝
uv run python -m social_info                           # 跑全 pipeline 寫今天 .md
uv run python -m social_info --dry-run                 # 不寫檔不更新 db
uv run python -m social_info --source hn,reddit_*      # 只跑指定 source（debug）
uv run python -m social_info --date 2026-04-25         # 跑指定日期（補跑）
uv run python -m social_info --smoke                   # 真打 API 各 fetcher 限 limit=3
uv run python -m social_info.health                    # 印 7 天各 source 成功率
uv run pytest                                          # 跑單元測試與 mock fetcher
```

### 10.2 .gitignore

```
__pycache__/
.venv/
.pytest_cache/
.env
```

注意：`state.db` 與 `reports/*.md` **不**加 .gitignore。

## 11. Testing 策略

### 11.1 單元測試（必做）

- `url_utils.canonical_url`：tracking 參數移除、相對 URL、邊界 case
- `dedup.l1_check` / `dedup.l2_merge`：known input → known output
- `markdown.render_item` / `render_file`：known input → exact string
- `db` schema 操作

### 11.2 Fetcher Parser 測試（mock）

- 每個 fetcher 配 `tests/fixtures/<source>_response.json`（真實 API response 樣本）
- 測 parser：raw response → Item list
- 不打真 API
- 失敗情境：timeout / 4xx / malformed JSON / 空 list

### 11.3 Smoke Test（手動）

- `uv run python -m social_info --smoke`
- 每個 enabled fetcher 真打 API 跑一次（limit=3）
- 印 markdown render 結果到 stdout
- 不寫 db / 不寫 .md
- 對應 `smoke.yml` workflow_dispatch 觸發，GitHub Actions 一鍵跑

### 11.4 不做的事

- E2E 真打 API 自動測試（CI flaky）
- coverage 數字追求
- mutation / property-based / snapshot

### 11.5 框架

`pytest` + `pytest-asyncio` + `pytest-httpx`

### 11.6 CI 編排

- `daily.yml` 不依賴 `test.yml` 通過（避免 test breakage 阻斷 daily 跑）

## 12. 監控（介面：Claude Code + gh CLI）

### 12.1 三層

- **Layer 1 — `.md` 檔頭 failures 區塊**：每天 10:00 Asia/Taipei 後使用者打開時自然看到
- **Layer 2 — GitHub Actions workflow status**：整體 fail（commit 階段出錯等）→ GitHub 預設 email；個別 fetcher 失敗只進 fetch_runs 表，不影響 workflow 狀態
- **Layer 3 — 每週手動 health 復盤**：使用者跟 Claude Code 說「跑 health check」→ Claude 用 gh 連 repo 跑 `uv run python -m social_info.health` 或讀 fetch_runs 表，回報 7 天各 source 成功率

### 12.2 不做

- Slack / Discord / 自訂 email 通知
- 獨立 web dashboard
- 即時 alerting

## 13. 完整工作流圖

```
[每天 09:00 Asia/Taipei]
  ↓
GitHub Actions cron 觸發 daily.yml
  ↓
runner 跑 social_info pipeline
  ├─ git pull state.db
  ├─ 平行 fetch 所有 enabled sources（asyncio.gather）
  ├─ normalize + dedup (L1 URL hash, L2 title hash)
  ├─ write reports/YYYY-MM-DD.md
  ├─ update state.db
  └─ git commit + push (state.db + 當日 .md)
  ↓
[10:00+] 使用者打開 Claude Code
  ↓
"看一下今天的 AI digest" → Claude 用 gh 讀最新 .md
  ↓
Claude 對 raw md 做個人化篩選 / 排序 / 摘要 / 回報
```

## 14. Phasing

決策：**直接做到底，不分週**。一次 implement 全部 fetcher 與 pipeline。

但 PoC 簡化：
- 微信公眾號（wewe-rss）：sources.yml 條目寫好但 `enabled: false`，程式碼仍寫好可即時切換
- 公開 RSSHub 為預設 instance（`https://rsshub.app`）
- 第一個月跑滿後評估，決定是否花一個下午上 self-host RSSHub + wewe-rss

## 15. Open Questions（暫存、不阻塞）

- twitterapi.io credit 用完時的應變（升級付費 / 換 scrape provider / 砍 KOL 數）
- 微信公眾號 self-host 平台選擇（Zeabur / Hetzner / Railway）— 若決定上
- KOL Twitter Lists 自動匯入（如某人公開維護的 AI list）是否有意義
- Threads 葛如鈞 / iKala 等台灣帳號要不要 enable user_handles 抓取
- L2 dedup fuzzy match 是否要加（第一個月跑後評估）

## 16. Out of Scope（明確排除）

- LLM 摘要 / 評分 / cluster / 排序（pipeline 內）
- 跨語系語意去重（SemHash / Vector embedding）
- 主動 crawl 原文全文（Crawl4AI / Firecrawl）
- Email / Slack / Discord / 自訂通知
- 獨立 web dashboard / GitHub Pages 展示
- E2E 真打 API 自動測試（保留為手動 smoke）
- 自動補跑漏日（保留為手動 dispatch）
- Monthly / weekly rollup 整理
- 學術 paper / arXiv / research 內容
- Crypto / DeFAI 相關內容

## Appendix A：sources.yml 完整範例

```yaml
defaults:
  language_default: en
  excerpt_max_chars: 200
  fetch_timeout_seconds: 30

sources:
  # === 英文主力 ===
  - id: hn
    type: hn_algolia
    enabled: true
    tier: 1
    keywords: [LLM, AI, agent, MCP, coding agent, multimodal, Claude, GPT]
    limit: 30

  - id: reddit_localllama
    type: reddit
    enabled: true
    tier: 1
    subreddit: LocalLLaMA
    time_window: day
    limit: 10

  - id: reddit_claudeai
    type: reddit
    enabled: true
    tier: 1
    subreddit: ClaudeAI
    time_window: day
    limit: 10

  # ... 其他 reddit / github_trending / product_hunt / huggingface 同形式

  # === 英文 lab blogs / media（純 RSS）===
  - id: anthropic_blog
    type: rss
    enabled: true
    tier: 1
    url: https://www.anthropic.com/news.rss
    language: en

  - id: openai_blog
    type: rss
    enabled: true
    tier: 1
    url: https://openai.com/blog/rss.xml
    language: en

  # ... 其他 RSS

  # === X / Twitter（透過 twitterapi.io）===
  - id: twitter_tier1
    type: twitter
    enabled: true
    tier: 1
    handles:
      - karpathy
      - simonw
      - swyx
      - AlexAlbert__
      - AnthropicAI
      - sama
      - gdb
      - aravsrinivas
      - hwchase17
      - logangkilpatrick
      - c_valenzuelab
      - itsPaulAi
      - op7418
      - dotey
      - steipete
      - emollick
      - deepseek_ai
      - kaifulee
      - Cursor
      - Replit
    per_handle_limit: 10
    time_window_hours: 24

  - id: twitter_tier2
    type: twitter
    enabled: false  # 待 spot check 後手動 enable
    tier: 2
    handles:
      - saj_adib
      - chaseleantj
      - icreatelife
      - andrewallenxo
      - iwoszapar

  # === Threads ===
  - id: threads_keyword
    type: threads
    enabled: true
    tier: 1
    mode: keyword
    search_type: TOP
    queries:
      - Claude
      - Cursor
      - MCP
      - coding agent
      - Codex
      - OpenAI
      - Anthropic
      - Gemini
      - AI startup
      - Sora
      - Veo
      - Suno
      - Midjourney
      - Runway
      - AI 工具
      - 生成式 AI
      - AI 應用
    per_query_limit: 5
    time_window_hours: 24

  - id: threads_topic_tag
    type: threads
    enabled: true
    tier: 1
    mode: tag
    search_type: TOP
    queries:
      - AI
      - Generative AI
      - AI Tools
    per_query_limit: 10
    time_window_hours: 24

  - id: threads_user_handles
    type: threads
    enabled: false  # 骨架預留，等使用者填名單
    tier: 2
    mode: user
    handles: []

  # === 中文（中國）===
  - id: zhihu_hot
    type: rsshub
    enabled: true
    tier: 1
    path: /zhihu/hot
    language: zh-CN

  - id: weibo_search_ai
    type: rsshub
    enabled: true
    tier: 2
    path: /weibo/search/AI
    language: zh-CN

  - id: wechat_qbitai
    type: wewe_rss
    enabled: false  # PoC 第一個月 disabled
    tier: 1
    account_id: qbitai  # 量子位
    language: zh-CN

  - id: wechat_appso
    type: wewe_rss
    enabled: false
    tier: 1
    account_id: appsolution  # APPSO
    language: zh-CN

  - id: wechat_36kr_ai
    type: wewe_rss
    enabled: false
    tier: 2
    account_id: thirtysix_kr_ai  # 36 氪 AI
    language: zh-CN

  - id: wechat_jqzx_pro
    type: wewe_rss
    enabled: false
    tier: 2
    account_id: almosthuman_pro  # 機器之心 Pro
    language: zh-CN

  # === 中文（台灣）===
  - id: ithome_ai
    type: rss
    enabled: true
    tier: 2
    url: https://www.ithome.com.tw/rss/category/ai
    language: zh-TW

  - id: inside_ai
    type: rss
    enabled: true
    tier: 2
    url: https://www.inside.com.tw/feed/ai
    language: zh-TW

  - id: dcard_engineer
    type: rsshub
    enabled: true
    tier: 2
    path: /dcard/posts/工程師
    language: zh-TW
```

## Appendix B：Threads OAuth Setup 步驟

1. 到 [Meta for Developers](https://developers.facebook.com/) 用個人 Meta 帳號登入
2. Create App → 選 Business 類型 → 加 Threads API product
3. 在 App Settings 拿 App ID 與 App Secret，存進 GitHub Secrets（`THREADS_APP_ID` / `THREADS_APP_SECRET`）
4. Threads API → Use Cases → Add Access to Threads API
5. 申請權限：`threads_basic`、`threads_keyword_search`、`threads_read_replies`（read-only basic 不需 App Review）
6. 在 OAuth Tools 跑 authorization flow 拿 access token + refresh token
7. 存進 GitHub Secrets（`THREADS_ACCESS_TOKEN` / `THREADS_REFRESH_TOKEN`）
8. workflow 中每次呼叫前檢查 token 過期、必要時 refresh

## Appendix C：twitterapi.io 註冊與 Credit 監控

1. 到 [twitterapi.io](https://twitterapi.io) 註冊（送 100K free credits）
2. 拿 API key，存進 GitHub Secrets（`TWITTERAPI_IO_KEY`）
3. 估算消耗：20 KOL × 10 tweets × 30 天 = 6000 tweets / 月 → free credits 可跑 ~16 個月
4. Health script（Section 12 Layer 3）讀帳戶 credit balance 並印出，每週手動跑一次
5. Credit 用完前 1 個月開始評估：升級付費 vs 砍 KOL 數量 vs 換 scrape provider

## Appendix D：未來 Self-host RSSHub / wewe-rss 指南（第一個月跑後再決定是否需要）

選一台 $5 / 月小 VPS（Zeabur、Railway、Hetzner CX22 任一）：

```yaml
# docker-compose.yml
version: '3'
services:
  rsshub:
    image: diygod/rsshub:latest
    ports:
      - "1200:1200"
    environment:
      - NODE_ENV=production
      - CACHE_TYPE=redis
      - REDIS_URL=redis://redis:6379/
    depends_on:
      - redis

  wewe-rss:
    image: cooderl/wewe-rss-sqlite:latest
    ports:
      - "4000:4000"
    volumes:
      - ./data:/app/data
    environment:
      - SERVER_ORIGIN_URL=https://your-domain.com:4000

  redis:
    image: redis:alpine
```

部署後：
1. 設定 Cloudflare DNS 指向 VPS、開 HTTPS（Caddy / nginx + Let's Encrypt）
2. 把 endpoint URL 寫進 GitHub Secrets `RSSHUB_INSTANCE_URL` 與 `WEWE_RSS_URL`
3. wewe-rss 進 `https://your-domain.com:4000` 用微信讀書 app 掃 QR code 登入（取得授權）
4. sources.yml 把 `wechat_*` 的 `enabled: true`
5. 下次 daily 跑就會抓到微信公眾號內容

## Appendix E：未來決策檢查點

第一個月跑滿後（2026-05-26 後）回顧：

- [ ] 中文 signal 對 daily reading 有實際幫助嗎？沒有 → wewe-rss 永久不上
- [ ] 公開 RSSHub 失敗率多少？> 30% → 上 self-host
- [ ] L2 dedup 漏掉多少同事件？大於可忍受 → 加 fuzzy match
- [ ] X tier 2 KOL spot check 結果？quality 高 → enable
- [ ] Threads keyword vs topic tag 哪個 signal 好？砍掉 signal 差的
- [ ] twitterapi.io credit 消耗速度？低於預期 → 加 KOL；高於預期 → 砍 KOL
