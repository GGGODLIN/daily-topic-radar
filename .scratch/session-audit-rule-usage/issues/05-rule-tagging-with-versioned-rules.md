# 05 — 規則標記＋規則版本＋最小狀態

**What to build:** session-audit 分析每段時附上該 session 當時的常駐規則全文（或 03 成立時的索引），模型在既有 findings 之外回報「這段用到哪幾條規則、applied 或 violated、逐字引文」。規則版本每個 session 從 `~/.claude` 的 git 取一次「對話時間前最新的 commit」，並記下用哪個 commit。狀態只存每條規則最後一次遇到場合的時間，以及每週成功分析的段數。

**Blocked by:** 01 — 前置確認：session-audit 已正式啟用；02 — 前置確認：free 池容量實測；03 — 條件票：規則索引退路（僅在 03 成立時）

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（假模型 server＋臨時 git repo）

**Validation method:** session-audit 測試以假模型回規則標記，用含兩個 commit 的臨時規則 repo 驗版本選擇，跑 run → status／report

**Evidence required:** 測試輸出；status／report 顯示每條規則的最後遇到時間、每週覆蓋段數與判斷用 commit

**TDD:** `required`

**TDD seam:** session-audit 指令列 run（本機假模型 HTTP server）→ status／report 輸出

- [x] 舊 session 用它對話時間前的舊版規則判，新 session 用新版 — Source: Story 9
- [x] 規則範圍含 `CLAUDE.md` 與 `rules/common/` 各檔 — Source: Story 11
- [x] 回報的每筆規則標記附判斷用的 commit — Source: Requirement: 規則版本取自 git
- [x] git 取不到版本時，該 session 的規則標記記為未分析並寫 limitation，不改用當前版 — Source: Requirement: 規則版本取自 git — Failure: F4
- [x] 模型回報不存在的規則、或引文不是原文子字串時丟棄該筆 — Source: Requirement: 同一引擎、同一次讀取 — Failure: F2
- [x] 模型呼叫失敗或回應格式錯誤的段不算進該週覆蓋 — Source: Requirement: 最小狀態 — Failure: F4
- [x] 既有 findings 欄位、prompt 的「只做 agent-observation」約束與既有測試結果不變 — Source: Requirement: 同一引擎、同一次讀取

## Verification Log

- 2026-10-05 worker（routed-impl）交付 patch sha256 ff1e3056…，main 重算一致；base 3776857。RED：8 條新測試因 status 沒有 `rules` 區、prompt 沒有規則全文與 `rules-version-unavailable` 而紅（功能缺失）。GREEN：main 獨立重跑 `session-audit.test.py` 33/33、`regressions` 6/6、`entrypoints` 3/3。
- main 唯讀 smoke：新版 `status` 讀現役 state 不報錯，`rules` 區為空（尚未有帶規則的 run）。
- Minor：週別與 last_seen 取 session 最早 timestamp，長 session 跨週時會偏早（report 已註明）；多段 session 的逐段覆蓋計數沒有專門測試；真實 free 池回的 rule_tags 品質未知，留給 09。
- Minor：worker 對真實 `~/.claude` 2026-10-01 commit 切出 134 條規則、規則區塊 33,785 bytes。
- 2026-10-05 ticket-yagni verdict（kill 0／demote 3／keep 81，verdict 檔在 `~/.claude/logs/ticket-yagni/…/05/…/verdict.json`）。收到時 05 已合進 main（main 先整合、verdict 後到，流程順序錯在 main），06／07 worker 正以 4ecc774 為底開工，所以延到 06／07 回來後才處理。main 逐條判定：
  - `connect`（逐段帳本 `rule_coverage`）：**不接受**。逐段列是「來源改寫重跑時只算當前 generation」的去重依據，換成每週計數器會在重跑時重複計數（Failure: F6）；07 還要依「該段 session 用的版本含不含這條規則」算有覆蓋週，需要 session→commit 的連結。`rule_sessions` 是 spec 要求的「記下用哪個 commit」。
  - `store_rule_tags`（每筆標記都存）：**部分接受**。applied 標記只需要更新 last_seen：07 往回走遇到的第一個有標記週，就是 last_seen 所在週，所以不必存逐筆 applied。violated 逐筆要留，因為 06 的子行需要每筆的 source_ref 與引文。待 07 回來確認它沒依賴逐筆 applied 後再砍。
  - `rules_snapshot`（status 重列全部 tags）：**接受**。status 只列 violated（06 用）與彙總；applied 逐筆不列。
- 2026-10-05 已套用接受的兩條（main 自做，不送回原 worker：它的 worktree 已移除，06／07 已建在這段狀態上，整合脈絡在 main）：新增 `rule_last_seen`（每條規則一列，只在對話時間更新時覆寫）；`rule_tags` 只存 violated；status 的 `last_seen` 改讀 `rule_last_seen`、`tags` 只剩 violated；07 的 `zero_use_facts` 改讀 `rule_last_seen`。原本斷言 applied 逐筆列的 4 條測試改成斷言 last_seen（被砍項目的測試隨項目改）。全套：`session-audit.test.py` 48 OK、regressions OK、entrypoints OK、`rules-size-weekly.test.sh` 22/0。
- Minor：`rule_last_seen` 不跟 generation／included 綁定；來源後來被改判 self／synthetic 時，它先前留下的 last_seen 不會撤回。
