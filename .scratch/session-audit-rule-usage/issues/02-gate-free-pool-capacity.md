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

- [ ] [Gate] 收據列出每次的回應模型與 finish_reason — Source: Requirement: 第一張票＝容量實測
- [ ] [Gate] 回 200 但 finish_reason 為 length／max_tokens 或回應截斷時算失敗，不算通過 — Source: Requirement: 第一張票＝容量實測 — Failure: F4
- [ ] [Gate] free 池換腿到不同模型時，每個實際回應的模型分開記錄，結論不以單一腿成功代表整池 — Source: Requirement: 預設送規則全文 — Failure: F5
- [ ] [Gate] 用的是實際最長分段與當前規則全文的真實大小，不是估計值 — Source: Requirement: 預設送規則全文 — Failure: F6
