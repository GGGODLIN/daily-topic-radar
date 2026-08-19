# 02 — Deterministic PROBES writeback

**What to build:** 當 probe 提供新 baseline 時，四個 daily-topic workflow 由單一 mechanical executor agent 一次呼叫 section-bounded deterministic helper，只更新目標 probe section 內唯一的 `Last seen` 行；executor 不判斷或組 replacement，也不再 per-entry fan-out，不符合唯一性條件時 fail closed、不修改檔案。

**Blocked by:** 01 — 日期輸入 fail-fast

**Status:** completed

**Needs:** None — a worker can check every item unaided

**TDD:** `required`

**TDD seam:** 暫時 PROBES fixture 經 writeback 後的檔案 bytes 與結構化 writeback result

- [x] 單一目標 heading、單一 `Last seen` 行與超長歷史內容的 fixture 能成功更新一次。
- [x] heading 缺失、heading 重複、目標 section 缺少 `Last seen`，或該 section 有多條 `Last seen` 時，writeback 回報失敗且 fixture bytes 完全不變。
- [x] 目標 section 外的其他 entry 與欄位保持 byte-identical。
- [x] writeback result 如實回報 attempted、succeeded、failed、skipped reason，以及成功時可用的 old/new line evidence。
- [x] 四個 workflow 都以單一 mechanical executor agent 一次呼叫 deterministic helper；不再 per-entry fan-out，executor 不判斷或組 replacement。
- [x] 測試只使用 temporary fixture，不修改真實 PROBES.md。
- [x] writeback publication 使用 same-directory temporary file、flush/sync 與 atomic rename；程序中斷不會留下截斷的目標檔案。
- [x] target 帶 exact expected old `Last seen` line；stale replay 在 mutation 前 fail closed。
- [x] concurrent writers 以 lock 或等價 compare-and-swap 序列化；衝突者 fail closed，不靜默覆蓋另一筆更新。
- [x] baseline 拒絕 ASCII control characters 與 U+2028/U+2029 line separators。
- [x] executor 不把外部衍生的自然語言直接放進 acting prompt；payload 以 opaque encoding 傳遞，且 helper 仍重算／驗證所有可寫欄位。

## Verification Log

- 2026-08-19 — **Gate conclusion／resolved by correction:** 原 criterion 要求 Workflow script glue 直接 mutation 且零 agent，但官方 runtime 明文禁止 workflow 本身直接存取 filesystem 或 shell；檔案操作必須由 agent 執行。契約改為「單一 mechanical executor agent 一次呼叫 deterministic helper」，保留 deterministic replacement、fail-closed、structured result 與 temporary fixture TDD，移除 per-entry LLM 判斷與 fan-out。修正前 production／test 修改數為 0。證據：[Claude Code Dynamic Workflows — Behavior and limits](https://code.claude.com/docs/en/workflows#behavior-and-limits)。
- 2026-08-19 — **Final review blocker:** 兩個 reviewer 都確認 helper 的整檔 `writeFileSync` 不提供程序中斷安全、stale replay 或多 writer lost-update 防護；其中程序中斷已實測把 100 MB temporary fixture 截成 0 bytes，並行 writer 已重現 20/20 輪遺失其中一筆更新。另 acting executor 直接讀取外部衍生 baseline，存在 prompt-injection 到 mutation 的路徑。依 GPT 實作收斂規則，未經使用者拍板不擴大本輪修正；commit 被此項阻塞。
- 2026-08-19 — **Final resolution:** helper 改用 same-directory temporary file、`fsync` 與 atomic rename；`expected_old_line` CAS、shared `PROBES.md.lock`、control-character rejection 與 opaque base64 payload 都接進四版 production workflow。`probes-daily.sh` 也在啟動 Claude writer 前取得同一把 lock，collision 不啟動 writer，正常與失敗 exit 都清理 lock。獨立 verifier 對 Ticket 02 的 11 項 acceptance 全部判 PASS。
- 2026-08-19 — **Known limitations:** `SIGKILL`／主機中斷可能留下 stale lock 與 temporary file，需人工清理；rename 成功後若在輸出 structured result 前 hard-kill，檔案狀態與 workflow 收據可能不一致，且 daily report 也未使用 atomic publication；helper 的 atomic rename 會把原本 `0644` 的 mode 改成 `0600`。這三項列為 MEDIUM limitation，沒有擴大本 ticket 的 acceptance contract。
