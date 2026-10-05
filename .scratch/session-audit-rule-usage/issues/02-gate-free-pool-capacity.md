# 02 — 前置確認：free 池容量實測

**What to build:** 用 session-audit 會送出的最長分段，加上常駐規則全文（`~/.claude/CLAUDE.md`＋`rules/common/`），對 free 池實送多次，確認是否讀得完。結果決定兩件事：`REQUEST_UTF8_BUDGET` 要不要調整、條件票 03（規則索引退路）要不要做。

**Blocked by:** None — can start immediately

**Status:** `ready-for-agent`

**Needs:** 本機 CLIProxyAPI relay 在跑、free 池可用

**Validation method:** 實送至少 5 次，逐次記錄；比較「最長分段＋規則全文」與「最長分段單獨」兩種大小

**Evidence required:** 收據檔逐次列出：送出 bytes、回應模型、finish_reason、HTTP 狀態、prompt／completion tokens；結論一句「全文可行／需退回索引」

**TDD:** `waived`

**TDD waiver:** `non-executable-artifact`

**TDD waiver approved:** `ticket-breakdown-user-approved`

- [x] [Gate] 收據列出每次的回應模型與 finish_reason — Source: Requirement: 第一張票＝容量實測
- [x] [Gate] 回 200 但 finish_reason 為 length／max_tokens 或回應截斷時算失敗，不算通過 — Source: Requirement: 第一張票＝容量實測 — Failure: F4
- [x] [Gate] free 池換腿到不同模型時，每個實際回應的模型分開記錄，結論不以單一腿成功代表整池 — Source: Requirement: 預設送規則全文 — Failure: F5
- [x] [Gate] 用的是實際最長分段與當前規則全文的真實大小，不是估計值 — Source: Requirement: 預設送規則全文 — Failure: F6

## Verification Log

- 2026-10-05 實測 10 次（收據：`../capacity/2026-10-05-receipt.jsonl`，腳本 `../capacity/capacity_probe.py`，sha256 524bc989…）。分段取 15 個最大自然 session（排除 skill-up、subagents、workflows、本 session）切出的最長段：16,884 bytes（= 現行 `instruction_budget()` 上限）；規則全文 11 檔 39,698 bytes。
  - 只送分段：5/5 HTTP 200、finish_reason=stop、JSON 可解析；request 28,641 bytes、prompt 4,715 tokens。
  - 分段＋規則全文（max_tokens=4000）：5/5 HTTP 200、stop、JSON 可解析；request 95,711 bytes（JSON 跳脫後）、prompt 16,403 tokens、completion 最高 1,151 tokens。
  - 10 次的回應模型全是 `xiaomi/mimo-v2.6-flash`，沒有換腿。relay 設定裡 alias `free` 另有 agentrouter-glm、agentrouter-astra、free-ds、free-muse、free-bunny 等腿啟用中，**這些腿未驗證**；結論只代表 mimo-v2.6-flash 這條腿。其他腿若撐不住，既有程式把 HTTP 失敗／finish-length 記為該段未完成，不會靜默算成功。
- **結論：全文可行**。條件票 03 不做；`REQUEST_UTF8_BUDGET` 維持 24000（分段預算不變，規則段另計）；帶規則的請求 max_tokens 用 `OUTPUT_RESERVE`（4000），舊值 1500 在第 4 次（1,151 tokens）已接近上限。
- Minor：同一段 5 次只有 1 次回報 rule_tags（1 筆），其餘 0 筆。這是容量以外的「模型會不會漏報」問題，會讓零使用清單偏高；留給 09 真實資料確認時觀察，不在本票追。
