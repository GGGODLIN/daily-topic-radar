# 04 — 排除 skill-up 假對話

**What to build:** session-audit 把 cwd 落在 `$TMPDIR` 下 `skill-up-<n>/`（專案目錄名含 `-T-skill-up-`）的對話歸為合成對話，不分析、不計入任何統計。主分析與之後的規則標記共用同一個判定，所以這也修好 session-audit 主分析本身的同一缺口。

**Blocked by:** 01 — 前置確認：session-audit 已正式啟用

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided

**Validation method:** session-audit 既有測試檔新增案例，跑 scan／status 看分類

**Evidence required:** 新案例與既有合成分類案例全部通過的測試輸出

**TDD:** `required`

**TDD seam:** session-audit 指令列 scan → status 的 classification 欄位

- [ ] 專案目錄名含 `-T-skill-up-` 的對話被歸為 synthetic、不進分析 — Source: Story 10
- [ ] `/var/folders` 底下但不是 skill-up 的對話照常分析，不被擴大排除 — Source: Requirement: 合成對話排除 — Failure: F2
- [ ] 既有的記錄旗標與 `eval-roots`／`synthetic-eval` 判定結果不變 — Source: Requirement: 合成對話排除
- [ ] skill-up 的 subagent／workflow 子紀錄隨父 session 一起排除 — Source: Story 10 — Failure: F5
