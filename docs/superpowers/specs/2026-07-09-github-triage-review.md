# Review — GitHub Triage Spec (T2)

**Date**: 2026-07-09
**Tier**: T2 (single architecture-critic, fresh-context)
**Spec**: `2026-07-09-github-triage-design.md`
**Reviewer**: architecture-critic subagent

## Findings disposition (2026-07-09 applied to spec v1.1)

| ID | Severity | Disposition |
|---|---|---|
| B1 | Blocker | **ACT** — 選 (i) 加 GitHub search API by description 為 Phase 2.5；實測撈到 5 個系統性漏 100K+ 星 repo（含 mattpocock/skills） |
| B2 | High | **RESOLVED by spin out** — driver C 相關 Phase 4 spun out 到獨立 spec、本 spec 只 driver A |
| B3 | High | **ACT** — Phase 2 加 5-case behavior table + `DedupResult { new_items, resurface_ids }` refactor |
| B8 | High | **ACT** — Phase 2.5 = B8 (b) GitHub search API 路徑；B8 (a) PROBES watchlist 未採納（PROBES 是 point-in-time watch、search API 是 discovery） |
| B4 | Medium | **ACT** — Phase 2 N 值改 dry-run based；north star 加 Phase 3 前必量 baseline |
| B5 | Medium | **DEFERRED to Phase 4 獨立 spec** — 付費 Haiku baseline anchor 屬 model 選型議題、進獨立 spec |
| B6 | Medium | **DEFERRED to Phase 4 獨立 spec** — Stack refresh exit criteria 屬 Phase 4b 議題、進獨立 spec |
| B7 | Medium | **ACT** — Relay park 加 osascript notification 主動 alert；但因 Phase 4 spun out、實際落地在獨立 spec |
| B9 | Nit | **ACT** — Phase 2 加 schema audit 前置 + `DedupResult` refactor 明寫 |
| B10 | Nit | **ACT** — `stack-distill.py` 從 `scratchpad/` → `scripts/`；但因 Phase 4b spun out、實際落地在獨立 spec |
| B11 | Nit | **ACT** — KNOWN_ISSUES triage failure 按 severity 分進現有四區；但因 Phase 4c spun out、實際落地在獨立 spec |

**摘要**：11 findings → 8 ACT（其中 3 條因 Phase 4 spin out 實際落地在獨立 spec）、2 DEFERRED to 獨立 spec、1 RESOLVED by spin out。No IGNORE / 反駁 cases。

---


---

## Blocker

### B1. Spec 自證 Phase 2 + Phase 3 解不了 driver A

**What**: Spec Findings 段自己寫「curl `github.com/trending/typescript?since=weekly|monthly` 都沒 mattpocock/skills」「8 lang × 3 freq 全抓 370 candidates 也沒」— trending API 結構性不吐 stable 過峰值 repo。但 Phase 2 (N 天 re-surface) 只在「source 又吐出來」時 unlock；Phase 3 (擴 scope) 也已被 spec 自己證明抓不到目標 repo。

**Where**: Findings #3 vs Phase 2 驗收（「30 天內重新 surface」）vs Phase 5 驗收（「mattpocock/skills 對照 raw md 有沒有 re-surface」）。

**Why**: 主 driver 的成功驗收條件被 spec 自己內部證據反證。Phase 2+3 完成後 Phase 5 大概率 fail、失敗原因 spec 一開始就寫在 Findings。做完 3 週工可能還在原地。

**Suggested change**: (i) 拉「Fetcher 增加 non-trending source」從 out-of-scope 進來、加 Phase 2.5 = curated GitHub search API (`gh api /search/repositories?q=pushed:>N+topic:ai`) 或 explicit watchlist；或 (ii) 承認 driver A 這輪不解、正名 driver 排序為 C 主 A 輔。

---

## High

### B2. Driver ratio 跟工程量 ratio 錯配

**What**: Spec 寫「主要 driver = A、C 是加分項不強求」。但 Phase 1 (trendshift) + Phase 2 (dedup) 才服務 A；Phase 4a/4b-1/4b-2/4c 全部服務 C、且是總工程量絕大部分。Triage 是 point-in-time classification、對 recall (該進 raw md 沒進) 零貢獻。

**Where**: Architecture 表 + Data flow 三段幾乎全部 Phase 4 相關。

**Why**: 典型「主 driver 是藉口、實際想做的是 C」錯配。混在一起 review / verify 判準糊掉 — Phase 4c triage 成功率 > 80% 跟 A 是否被解沒關係。

**Suggested change**: Phase 4a/4b/4c 全部 spin out 成獨立 spec（「CLIProxyAPI relay 找 daily use case — triage 實驗」）、有自己 kill criteria。本 spec 只保留 Phase 1-3 + 5、變「補漏 spec」、2-3 週壓到 5-7 天。

### B3. Dedup 改動 semantics 不完整

**What**: Spec 說加 `last_surfaced_at` + 「id 存在但 > N 天 → 允許 re-surface + 更新 `last_surfaced_at`」。但沒說：
1. Re-surface 是 update existing row + 塞進 `new_items` return list（下游認 new）？還是只 update timestamp、不塞進 new_items（raw md 不會出現）？
2. `dedup.py` 現有 L2 title_hash tier（higher-tier-wins 邏輯）— L2 collision path 完全沒被 spec 觸及。同一 repo URL 微差（trailing slash / query）走 L2 時、Phase 2 是否適用？

**Where**: Phase 2 步驟 vs `dedup.py:44-103` 兩層邏輯。

**Why**: `Deduper.process()` 現在 contract 是「return 要 persist 的 new items」。Re-surface 語義破壞現有 contract；L2 path 不處理則 re-surface 只在 L1 hit 時 work、部分 case silent skip。

**Suggested change**: Phase 2 明確定義新 contract、附 8 種 case 表格（L1/L2 × <30d/>30d × hit/miss × ...）對應 behavior。

### B8. 更簡單的 alternative 沒評估

**What**: Driver A = 「特定 stable AI repo 不再出現」。Spec 沒評估：
- **(a) Extend PROBES.md pattern**：user 手動維護 30-50 個「不想漏」repo、`gh api` 查最近 pushed_at / stars → PROBES 拉進 digest。零 pipeline / 零 relay / 100% recall for named repos。
- **(b) GitHub search API 換 source**：`/search/repositories?q=stars:>1000+pushed:>N+topic:ai`。比 trending API 精確、正是 mattpocock/skills 這種 case 該用的 source。

**Where**: Motivation → Findings → Architecture 直接跳到「改 dedup + 擴 scope + 加 triage」、無 alternatives 段。

**Why**: PROBES.md pattern 已成熟、user 熟悉、user 自己維護清單有最強 signal。Spec 主 solution 是 3 週 pipeline + relay + me-profile chore + 30 天 review nudge — 為了「10 個 repo 要看到」用 sledgehammer 打蚊子。memory `user_core_mode_skill_crystallization` 顯示 user stance 是「不蓋 harness」。

**Suggested change**: Motivation 加「Alternatives considered」子段、至少列 (a)(b) 並說為何 reject。若 reject 不了、主 solution 換掉。

---

## Medium

### B4. N=30 + 「3-5x 提升」metric 沒 baseline

**What**: N=30 無論證（「GitHub trending 週期」一句帶過）。Phase 3 baseline 370 unique/day 有實測、但沒估「加 N=30 re-surface 後每天實際 net-new 會膨脹到多少」。North star「hi + med tier / day 提升 3-5 倍」也無 Phase 0 baseline 數。

**Where**: Phase 2 (N=30)、Phase 3 baseline、secondary metric。

**Suggested change**: Phase 3 完成後跑 dry-run 模擬 N=30 對過去 30 天 raw md 的影響、用實測選 N。North star 標數字前先量 Phase 0 baseline (現在 digest hi+med 幾條/day)。

### B5. Phase 4a golden × model 缺付費 baseline anchor + sample 不足

**What**: 對照組全部 free-tier from same relay pool、沒付費 baseline (e.g. Haiku) 當 reference。50 sample × 3-class accuracy CI 相當寬、相鄰 model 差 2-3% 都在 sampling noise。

**Why**: 沒 baseline anchor → 「> 80%」門檻 arbitrary、可能 lock 到不夠好的 model + 沒察覺。

**Suggested change**: 加付費 baseline (Haiku on same 50 samples) 當 upper bound。若 free-tier 最好那個離 baseline > 15% → free-tier 不夠、Phase 4c 應該用付費（跟 driver C 相衝、但 signal 才對）。Sample size 100+。

### B6. Stack refresh workaround 無 exit criteria + 打賭 user 會 action nudge

**What**: Phase 4b-2 30 天 trial-review nudge 是 me-distill NOOP 的 workaround。沒定「me-distill 修好後這 workaround 拆掉」條件。memory `user_new_tool_research_default_adopt` 記載 35 session 9/11 靠 user 推、被動 nudge 命中率不高。

**Suggested change**: 加 exit criteria（「me-distill 恢復運作 + 連續 7 天有實質 diff → 拆 workaround」）；或承認 stack context 對 triage 影響小、Phase 4c 用簡化 prompt 不吃 stack、砍 4b 整段。

### B7. Relay dependency 無 alerting

**What**: Layer 3 fallback 只寫 KNOWN_ISSUES.md、等 user 早上讀 digest 才知道。OAuth 過期是常見 case。user 若不看 KNOWN_ISSUES 段直接讀主體、triage tier 全 keyword 分類、漸漸不信任 tier 但不知系統 degraded。

**Suggested change**: Layer 3 觸發時 osascript notification (bumblebee scan 已用同 pattern)；或 fetch_runs status=degraded 額外寫 `ALERT-triage.md` 到 repo root。

---

## Nit

### B9. `last_surfaced_at` migration + 更新落點細節 sloppy

`items` table 是否有 `posted_at` column 沒 verify。spec 沒說 `last_surfaced_at` 更新落在 `Deduper.process()` 內（破壞 read-only contract）還是 `pipeline` 端。**Suggested**: 附 schema audit 前置步驟；`Deduper` 若要 write 就明寫、或抽 `DedupResult { new_items, resurface_ids }` 讓 pipeline 端統一 write。

### B10. `stack-distill.py` 位置錯配長期意圖

scratchpad session-specific 隨手 dir、`~/code/social-info/scratchpad/` gitignored、30 天後未必還在。若要 rerun 就放 `scripts/` (跟 `scripts/local-analysis/*.sh` 同層)。

### B11. `🔮 Triage failures` 分類軸跟現有 4 區不同

現有 KNOWN_ISSUES 四區都是 fetcher-level severity；新增 processor-level 平行類、taxonomy 亂。**Suggested**: triage failure 按 severity 分進現有四區（OAuth 過期 → 🚨、5xx 持續 → 🛠、暫時 → ⏳）；不要新增第五類。

---

**If I could only change one thing**: 把 Phase 4 (triage + stack context + trial-review) 全部從本 spec 抽出來變獨立 spec、本 spec 只保留 Phase 1-3 + 5、並解封 out-of-scope「Fetcher 增加 non-trending source」加 Phase 2.5 = curated GitHub search API 當第二 source、主 driver A 才實質有解、也避免 driver ratio 跟工程量 ratio 錯配。
