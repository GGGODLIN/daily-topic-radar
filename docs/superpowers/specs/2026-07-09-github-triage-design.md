# GitHub Triage — 面向補漏 + scope 擴大

**Date**: 2026-07-09
**Status**: Design v1.1 (T2 review applied, pending user re-review)
**Revision**: v1.1 apply T2 review findings — B1 選 (i) 加 GitHub search API by description + spin out Phase 4 (triage + stack context + trial-review) 到獨立 spec (driver C 找免費池 use case)。本 spec 從原 5 phase 縮成 4 phase (1 修 trendshift → 2 dedup N 天 re-surface → 2.5 GitHub search API by description → 3 擴 scope + 拿掉 keyword filter → 5 verify)。Phase 4 相關 sections 標 SPUN OUT、Data flow / Error handling / Testing 部分段落 stale、待 v2 clean up。
**Affected paths (本 spec v1.1、Phase 4 相關已 spun out)**:
- `src/social_info/fetchers/trendshift.py`（Phase 1 修 fetcher）
- `src/social_info/fetchers/github_trending.py`（Phase 3 拿掉 keyword filter）
- `src/social_info/fetchers/github_search.py`（Phase 2.5 新增、by description + name）
- `src/social_info/dedup.py`（Phase 2 抽 `DedupResult`、5-case behavior）
- `src/social_info/pipeline.py`（Phase 2 呼叫端處理 resurface_ids）
- `sources.yml`（Phase 3 github_trending 擴 languages + since；Phase 2.5 新增 `github_search` source）
- `state.db` schema（Phase 2 items 加 `last_surfaced_at` column）

---

## TL;DR

Daily digest 對 GitHub trending 段兩面向問題：**(1) 以前看過的 repo 不再出現**（mattpocock/skills 起點、被 pipeline dedup 靜默過濾）+ **(2) scope 太窄**（3 lang × 1 freq × 10 keyword、且 trending 結構性不吐 stable 過峰值 repo）。v1.1 分 4 phase：Phase 1 修 trendshift → Phase 2 pipeline dedup 加 N 天 re-surface → Phase 2.5 加 GitHub search API by description + name（實測 `q=skills+in:name+claude+in:description` 一發撈到 5 個 100K+ 星系統性漏 repo、含 mattpocock/skills）→ Phase 3 擴 fetcher scope（8 lang × 3 freq、拿掉 keyword filter）→ Phase 5 端到端 verify。原 Phase 4（triage + stack context + trial-review）spin out 到獨立 spec `2026-07-09-triage-free-pool-experiment-design.md`（driver C 找免費池 use case、獨立 spec 有自己 kill criteria）。

---

## Motivation

### 起點 driver（A 補漏）

User 觀察：**mattpocock/skills**（161K star、7/8 剛 push、description = "Skills for Real Engineers. Straight from my .claude directory."）過去 4/30 出現在 daily digest 一次、之後 2 個多月沒再出現。AI 生態變動快、這個 pattern 對 user 有害。

### 對話走歪的 driver（B 感官診斷）

原本純想理解「digest 中 GitHub trending 越來越少」是不是感官偏誤。實測數據顯示過濾率其實 53-80%（raw md → digest）、不是單調遞減、感官體感來自 `<details>` 摺疊 + 判斷欄「⏭ 跳過」比例高。**這個面向已解**（不需要動 pipeline）。

### 順便 driver（C 免費池找場景）

CLIProxyAPI relay 2026-07-05 部署完當日 park、`ds-flash` 免費池（Zen 5 顆 + NVIDIA 2 顆跨家 fallback）找不到 daily use case。Triage stage 剛好對得上。

**主要 driver = A**、B 已隱含解、C 是加分項不強求。

---

## Findings — 三個並存機制

實測拆解「mattpocock/skills 為何不再出現」：

1. **Pipeline dedup 一次入表永不再現（主機制）**
   - `items` table `id` = PRIMARY KEY、`Deduper.process()` 只保留 id 不存在的 new items
   - 4/30 入表 → 之後 trendshift / github_trending 再撈到都因 id 衝突被丟
   - **沒有「N 天後可以再 surface」機制**

2. **Trendshift fetcher 壞了（次要）**
   - 過去 5 天 raw md `trendshift` 命中 = 0
   - 修好也於事無補：dedup 那關會擋

3. **GitHub trending 結構性不 surface 過峰值 stable repo（結構）**
   - 我 curl 現在 `github.com/trending/typescript?since=weekly|monthly` 都沒 mattpocock/skills
   - 8 lang × 3 freq 全抓 370 candidates 也沒
   - 因為 trending 只列「本週爆點」、mattpocock/skills 161K star 但已不在 rising 期

**跟 memory recall 誤 skip 無關**（那是 CC 端行為、跟 pipeline mechanism 分開）。

---

## Related independent work

- **P3 memory 時效規則已於 2026-07-09 上午落地**：`CLAUDE.md` 加「引用 memory tool eval 結論的時效檢查」段 + `_index_tool_eval_outcomes.md` cluster header 加「⏰ 時效規則」段。跟本 spec 獨立（那個管 CC 端 memory recall、本 spec 管 pipeline mechanism），保留當 cross-ref。
- **me-distill NOOP tech debt**：`~/Documents/me-profile/_cron.log` 顯示 5/18 → 6/15 全 NOOP、`about_me.md` 24 天沒動、`_manifest.json` 9 天沒動。跟本 spec 獨立、暫不修。本 spec v1.1 不 depend me-profile（stack context 相關 Phase 4b spin out 到獨立 spec）— tech debt 修不修完全不影響本 spec。

---

## Architecture — 5 phase 增量落地

每 phase 3-5 天 verify、不 regress 才進下一 phase。全部完成 2-3 週。

| Phase | 內容 | 風險 | 驗收 |
|---|---|---|---|
| **1** | 修 trendshift fetcher | 低 | 3-5 天有 trendshift 資料進 raw md |
| **2** | Pipeline dedup 加「N 天可 re-surface」 | 中 | mattpocock/skills 這類 30+ 天前看過、若在 fetcher 抓得到範圍內、可 re-surface |
| **2.5** | 新增 GitHub search API by description（`sources.yml` 新 `github_search` source）| 低 | mattpocock/skills 等 5 個 100K+ 星系統性漏 repo 命中；日均 <10 net-new |
| **3** | Fetcher 擴 scope（8 lang × 3 freq、拿掉 keyword filter；keyword list 保留當未來 fallback rule）| 低 | Raw md 從 5-7 個 GitHub items / day → ~300 unique |
| **4** | ~~Golden × model / stack distill / triage landing~~ **SPUN OUT** → 見 `2026-07-09-triage-free-pool-experiment-design.md`（尚未撰寫、driver C 找免費池 use case 獨立 spec） | — | 本 spec 不管 |
| **5** | 端到端 verify | 低 | mattpocock/skills 這類 case 能重新 surface（含新增 github_search source 命中） |

**Phase 1-3 都是純 pipeline-side 改動**、獨立可跑可驗、不 depend Phase 4。Phase 4c 掛掉不影響 Phase 1-3。

### Phase 1: 修 trendshift fetcher

- **檔案**：`src/social_info/fetchers/trendshift.py`
- **步驟**：curl trendshift.io 確認現況、比對 fetcher 抓的 URL / HTML 結構 / cookie / status code、根因修
- **驗收**：連續 3-5 天 raw md 有 trendshift 命中

### Phase 2: Pipeline dedup 加「N 天可 re-surface」

- **檔案**：`src/social_info/dedup.py`（Deduper.process() 契約重定義）+ `pipeline.py`（呼叫端處理 resurface_ids）+ `state.db` schema（items 加 `last_surfaced_at TEXT` column）
- **前置 schema audit**：sqlite3 dump `items` 現有 columns、確認 `posted_at` 存在（migration 依賴）
- **Contract 重定義**：`Deduper.process()` 返回 `DedupResult { new_items: List[Item], resurface_ids: List[str] }`；pipeline 端統一負責 write（`last_surfaced_at` update + INSERT new + raw md 把 resurface items 也 render）
- **5-case behavior table**：

  | Case | L1 (id) hit | L2 (title_hash) hit | last_surfaced_at | Behavior |
  |---|---|---|---|---|
  | 1 全新 | No | No | (n/a) | insert + 加 new_items |
  | 2 純 L2 hit + <N | No | Yes | < N 天 | 走現有 higher-tier-wins；不 resurface |
  | 3 純 L2 hit + ≥N | No | Yes | ≥ N 天 | higher-tier-wins + `last_surfaced_at` update + 加 resurface_ids |
  | 4 L1 hit + <N | Yes | (n/a) | < N 天 | Skip |
  | 5 L1 hit + ≥N | Yes | (n/a) | ≥ N 天 | `last_surfaced_at` update + 加 resurface_ids |

- **N 值**：**初值 30、實測選定**。Phase 3 完成後跑 dry-run：模擬過去 30 天 raw md 若 apply 各 N 值（15/30/60）會多幾條 resurface + 多的是不是 user 要的、據此定 N
- **Migration**：`ALTER TABLE items ADD COLUMN last_surfaced_at TEXT`；`UPDATE items SET last_surfaced_at = posted_at WHERE last_surfaced_at IS NULL`；dev sandbox 先跑、對照 items count 前後應相等
- **驗收**：mattpocock/skills 若還在活躍（gh api 查 pushed_at 近 7 天）→ **在 fetcher 現況能抓到的前提下**、應於 30 天內重新 surface（不解 fetcher 抓不到 stable repo 的問題、那是 B1 決策範疇）

### Phase 2.5: 新增 GitHub search API source (by description + name)

- **檔案**：`sources.yml` 加新 source `github_search`、`src/social_info/fetchers/github_search.py` 新增
- **動機**：實測 mattpocock/skills `topics = []`（空）、trending 8 lang × 3 freq 全抓 370 candidates 也沒它；但 `gh api /search/repositories?q=skills+in:name+claude+in:description&sort=stars&order=desc` 一發撈到 5 個 100K+ 星系統性漏 repo（ECC 227K / andrej-karpathy-skills 189K / mattpocock/skills 161K / caveman 86K / graphify 80K）
- **實作**：
  - Source 用 gh CLI (`gh api /search/repositories?q=...`)、reuse [[bitbucket-api]] `bb_api.sh` 類似 CLI-wrapper pattern
  - Query template 存 `sources.yml`、初值 5-8 個：
    ```yaml
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
  - `{7d}` = fetcher 換成 `date -v-7d +%Y-%m-%d` runtime substitute
  - Rate limit safeguard：GitHub search API 30 queries / hour authenticated、每天 06:00 launchd 用 8 個 query（一次跑完） < 一小時限額
- **Volume 實測**（過去 3 個月 pushed）：
  - `claude+in:description+stars:>10000` = 94 個（3 個月）≈ 1/day
  - `anthropic+in:description+stars:>5000` = 12 個（3 個月）≈ 0.1/day
  - `stars:>1000+topic:llm+pushed:>7d` = 475 個（1 週）≈ 68/day
  - `stars:>1000+topic:mcp+pushed:>7d` = 280 個（1 週）≈ 40/day
- **驗收**：連續 3 天 raw md 有 github_search source 命中 + mattpocock/skills 命中至少一次

### Phase 3: Fetcher 擴 scope

- **檔案**：`sources.yml`（github_trending 段）+ `src/social_info/fetchers/github_trending.py`
- **改動**：
  - `languages: [python, typescript, rust, go, c, cpp, java, swift]`
  - `since: [daily, weekly, monthly]`（fetcher 改成迭代 since list）
  - **拿掉 `_matches_ai` keyword filter**、全抓；但**保留 keyword 列表 in config**（`ai_keywords` + 新增 `lo_keywords: [crypto, trading bot, game, wallet, ...]`）當 Phase 4c fallback 分類 rule 用、不再用來 drop items
- **實測 baseline**：8 lang × 3 freq 全抓 = 370 unique（dedup 25.6% overlap）
- **驗收**：Raw md 從 5-7 個 GitHub items / day → ~300 unique

### Phase 4: ~~原 4a / 4b-1 / 4b-2 / 4c 段~~ **SPUN OUT**

**Spun out to**: `2026-07-09-triage-free-pool-experiment-design.md`（TBD、尚未撰寫、待本 spec Phase 1-3 + 5 完成後才啟動）

**Rationale (T2 review B1 + B2 + B8)**：
- 原 Phase 4（免費池 triage）服務 driver C（「找免費池 use case」）、跟本 spec driver A（「補漏」）混一鍋
- Reviewer 「one thing to change」 = spin out Phase 4 讓本 spec focused
- Driver A 已由 Phase 2.5（GitHub search API by description）有 real solution、不需 triage 補位
- Triage 是 point-in-time classification、對 recall（該進 raw md 沒進）零貢獻

**獨立 spec 應含（本 spec 不管、留給獨立 spec）**：
- Golden × model 側比（含付費 Haiku baseline anchor 對照、cascade B5）
- 現場蒸餾 stack（`scripts/stack-distill.py`）+ 寫回 me-profile 3 檔 + `config/triage-stack-context.md`
- Trial-review 30 天 refresh nudge（`~/Desktop/projects/.claude/trials/active.md` 加一條 entry）
- 現場蒸餾 workaround exit criteria（me-distill NOOP tech debt 修好後拆掉，cascade B6）
- Triage stage landing（`src/social_info/triage.py`、pipeline call、state.db schema 加 triage_relevance/why、KNOWN_ISSUES 按 severity 分區、`--retry-triage` flag）
- API 細節：relay endpoint `http://127.0.0.1:8317`、model per Phase 4a 選定、batch 30 / 並發 5、三層 fallback、osascript notification alert
- 獨立 kill criteria（e.g. 30 天內 triage 沒實質改變 user 讀 digest 行為 → park）

**Cascade findings 自動 resolved by spin out**：
- B2 driver ratio 錯配 → 本 spec 只 driver A、resolved
- B5 付費 baseline anchor → 屬 Phase 4a 議題、進獨立 spec 再處理
- B6 stack refresh exit criteria → 屬 Phase 4b 議題、進獨立 spec 再處理
- B8 更簡單 alternative → 本 spec 採納 (i) GitHub search API、resolved

### Phase 5: 端到端 verify

- **不動 code**
- **步驟**：
  1. mattpocock/skills 對照 30 天 raw md 有沒有 re-surface
  2. 抽 10 個過去 4-6 月出現過的 stable AI repo、看 re-surface 狀況
  3. 對比 Phase 3 前後 raw md GitHub items 數量
  4. 對比 P3 memory rule 是否有 real usage（有沒有 tool eval 條目觸發重評）
- **產出**：Verification report 到 `docs/philip/`

---

## Data flow

### Fetcher 端（06:00 launchd `com.gggodlin.social-info-daily`）

```
sources.yml
  → fetchers/*.py (parallel fetch, 含新 github_search source)
    → items (raw list)
      → dedup.Deduper.process()
        → DedupResult { new_items, resurface_ids }
          → pipeline write:
              - INSERT new_items 進 items table
              - UPDATE resurface_ids SET last_surfaced_at = now()
          → markdown.render() → reports/YYYY-MM-DD.md
              (含 new_items + resurface_ids 對應的 items rows)
          → fetch_runs UPDATE (per-source status / attempts / net_new / resurface_count)
          → known_issues.write() (若任一 source failed)
```

### Digest 端（手動 trigger daily-topic-analysis workflow）

```
raw md + state.db
  → workflow pre-flight (KNOWN_ISSUES / WATCH / PROBES / external-feeds)
  → 主軸抽取
    → URL fact-check
      → sonnet subagent 寫 digest HTML
        → digest-YYYY-MM-DD.html
```

### ~~Triage failure flow~~ / ~~Stack refresh flow~~

**SPUN OUT** to `2026-07-09-triage-free-pool-experiment-design.md`（TBD）。本 spec 不含 triage、也不含 stack context 現場蒸餾。

---

## Error handling

### ~~Triage 失敗~~ / ~~Relay park / OAuth expired~~

**SPUN OUT** to `2026-07-09-triage-free-pool-experiment-design.md`（TBD）。Relay dep 屬 driver C spec、本 spec 不引入。

### GitHub search API rate limit（Phase 2.5 新增）

- Symptom: `/search/repositories` API 過 30 queries/hour 回 403
- Handling: sources.yml 內 `github_search` per_query_limit 30、每天 06:00 launchd 一次跑完 8 個 query（< 30/hr 限額）
- Fallback: 429 / 403 → 該 query 標 fetch_runs status=rate_limited、下次 06:00 retry
- Auth: `gh api` 走既有 `gh auth` 已登入 token、無需新增 credential

### Fetcher scope 擴後 rate limit

- Symptom: GitHub trending 24 requests / 6 分鐘、可能 rate limit
- Handling: asyncio.Semaphore 限並發 = 4、加 100ms 間隔
- Fallback: 429 → 該 lang × freq 標 fetch_runs status=rate_limited、下次 retry

### Dedup migration bug

- Risk: `last_surfaced_at` 加欄位時舊 rows null 導致 Deduper 判斷錯
- Handling: migration 時 `UPDATE items SET last_surfaced_at = posted_at WHERE last_surfaced_at IS NULL`
- Test: dev sandbox 跑 migration + 對照 items count 前後應相等

### ~~Stack context 過時~~

**SPUN OUT** to `2026-07-09-triage-free-pool-experiment-design.md`（TBD）。Stack context 屬 triage stage 依賴、本 spec 不用。

---

## Testing / verification

### Per-phase

| Phase | 驗收條件 | 觀察窗口 |
|---|---|---|
| 1 | 連續 3-5 天 raw md 有 trendshift 命中（非 0）| 5 天 |
| 2 | mattpocock/skills（或其他 30+ 天前看過的 active repo）在 fetcher 抓得到範圍內能 re-surface | 30-45 天 |
| 2.5 | GitHub search API source 命中 mattpocock/skills 等 5 個系統性漏 repo + 連續 3 天有 net-new | 3 天 |
| 3 | Raw md GitHub items 從 5-7 個/day → ~300 unique/day | 3 天 |
| 4 | ~~SPUN OUT to 獨立 spec、本 spec 不驗~~ | — |
| 5 | mattpocock/skills 這類 case 能重新 surface（trendshift + dedup + github_search 三管道任一即可）+ 對照 Phase 3 前後 raw md 數 | one-shot |

### 整體 north star

**Baseline (Phase 3 前必量)**: 現有 daily digest 「🛠 GitHub 倉庫觀察」段 hi + med tier repo 平均數 / day。抽過去 14 天 digest HTML count 出來當 Phase 0 anchor、其他 metric 才有比較基準。

**Primary metric**: 過去 4-6 月出現過的 stable AI repo（sample size 10）、Phase 5 完成後 30-45 天內 re-surface 比例 > 50%

**Secondary metric**: 上面 Phase 0 baseline × 3-5 倍（實測 baseline 後定門檻、不用 dart-throwing 直接寫 3-5）

---

## Decisions log

1. **Driver**: 本 spec 專注 driver A（補漏、mattpocock/skills 起點）；driver C（免費池找場景）已 spin out 到獨立 spec
2. **面向 1 解法**: (a) 改 pipeline dedup 加 N 天 re-surface + (b) 修 trendshift（雙管齊下）
3. **面向 2 解法**: (i) 加 GitHub search API by description + name（Phase 2.5）+ (ii) 擴 fetcher scope（8 lang × 3 freq）+ 拿掉 keyword filter（Phase 3）
4. **T2 review B1 disposition**: 選 (i) 加 GitHub search API — 實測 `q=skills+in:name+claude+in:description` 一發撈到 5 個 100K+ 星系統性漏 repo（ECC / karpathy-skills / mattpocock/skills / caveman / graphify）；「加 topic:X」對 mattpocock/skills 無效（其 topics=[]）、必須走 description 搜
5. **T2 review B1+B2+B8 混合方案**: (i) + spin out Phase 4（triage + stack context + trial-review）到獨立 spec；本 spec 從 5 phase 縮成 4 phase（+ Phase 2.5）
6. **實作順序**: 分階段增量、每 phase 3-5 天 verify、Phase 1 → 2 → 2.5 → 3 → 5 順向做、獨立 spec 待本 spec 完成後才啟動
7. **P3 memory rule**: 已於今日上午獨立落地、跟本 spec cross-ref；ECC 227K stars 命中觸發 P3 重評 = 本 rule 第一個 real usage test
8. **me-distill NOOP**: 獨立 tech debt、本 spec 不修；Phase 4 spin out 讓「stack context 現場蒸餾」也一併移到獨立 spec

---

## Out of scope

以下不進本 spec、預留給未來獨立處理：

- **Digest 呈現層改動**（拿掉 `<details>` 摺疊、hi/med 全展等）— 面向 B「感官」的可能解、已判定不需動
- **Triage stage + stack context + trial-review pattern + golden × model 側比** — spin out 到獨立 spec `2026-07-09-triage-free-pool-experiment-design.md`（TBD、driver C 找免費池 use case）
- **me-distill NOOP 修復** — 獨立 investigation、本 spec 不依賴 me-profile live update
- **其他 consumer 使用 stack context**（digest LLM / research-before-answer 等）— 屬獨立 spec 議題、本 spec 不管
- **加更多 non-trending source**（Product Hunt AI 分類 / arxiv 等）— Phase 2.5 GitHub search API 已補 stable AI repo 這條、其他 source 未來需要再說

---

## Cross-references

- Trigger 起點對話 memory：this session（2026-07-09）
- P3 memory rule: `~/.claude/CLAUDE.md` 「引用 memory tool eval 結論的時效檢查」段 + `~/.claude/memory/_index_tool_eval_outcomes.md` cluster header
- CLIProxyAPI relay setup: `~/Desktop/projects/cliproxyapi-setup/CLAUDE.md` + `~/.cli-proxy-api/keys.env` + memory `project_cliproxyapi_relay`
- me-profile canonical: `~/Documents/me-profile/` + pointer `~/.claude/me-profile-pointer.md`
- Existing local-analysis pattern: `~/code/social-info/CLAUDE.md` 「本機分析 routine」段
- Existing KNOWN_ISSUES pattern: `~/code/social-info/src/social_info/known_issues.py`
- Existing trial-review pattern: `~/Desktop/projects/.claude/hooks/trial-review.sh` + `~/Desktop/projects/.claude/trials/active.md`
- fetcher-probe 實測數據: `/private/tmp/claude-501/.../scratchpad/fetcher-probe.py`（370 candidates baseline）
