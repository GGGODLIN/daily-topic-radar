# 04 — 四版整合驗證

**What to build:** 在前三張 wiring ticket 完成後，以一輪 fresh verification 證據確認 default、full、minimal、vendor 四個 workflow 都具備相同的日期、PROBES writeback、trusted URL 接線，且本次修改沒有漂移任何內容策略或成本政策。

**Blocked by:** 01 — 日期輸入 fail-fast；02 — Deterministic PROBES writeback；03 — Trusted probe URL manifest 接入 verify gate

**Status:** completed

**Needs:** None — a worker can check every item unaided

**TDD:** `waived`

**TDD waiver:** `non-executable-artifact`

**TDD waiver approved:** `ticket-breakdown-user-approved`

- [x] 四支 workflow 語法檢查通過。
- [x] 日期、deterministic writeback、trusted URL fixtures 全部通過，且輸出包含 known-good、known-bad、boundary 證據。
- [x] daily-topic trigger routing／lens description contract test 通過。
- [x] 既有 URL known-good、known-bad 與 mechanical HARD fixtures 通過。
- [x] grep 枚舉確認四版都有三項新 wiring contract。
- [x] grep 枚舉確認 audit lens、model routing、fan-out、FactCheck／Verify 與 HTML contract 沒有被這次修改改變。

## Verification Log

- 2026-08-19 — **Final integration:** 四版 syntax 全部通過；date guard `24/24`、trigger contract `38/38`、mechanical verifier `19/19`、URL verifier `6/6`、shared-lock `3/3` 與 writeback regression 13 項全部通過。Python suite 為 `160 passed`。獨立 verifier 最後重跑四版 production trusted manifest，default／full／minimal／vendor 都得到 `ok=true`；F1、F2 blocker 均解除。
- 2026-08-19 — **Policy audit:** scoped diff 沒有改 audit lens、非 WriteBack model／fan-out、FactCheck／Verify、cost 或 HTML contract。minimal 仍維持單輪 A/D 與原 coverage gaps。
