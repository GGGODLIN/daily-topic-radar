# 04 — 排除 skill-up 假對話

**What to build:** session-audit 把 cwd 落在 `$TMPDIR` 下 `skill-up-<n>/`（專案目錄名含 `-T-skill-up-`）的對話歸為合成對話，不分析、不計入任何統計。主分析與之後的規則標記共用同一個判定，所以這也修好 session-audit 主分析本身的同一缺口。

**Blocked by:** 01 — 前置確認：session-audit 已正式啟用

**Status:** `done`

**Needs:** None — a worker can check every item unaided

**Validation method:** session-audit 既有測試檔新增案例，跑 scan／status 看分類

**Evidence required:** 新案例與既有合成分類案例全部通過的測試輸出

**TDD:** `required`

**TDD seam:** session-audit 指令列 scan → status 的 classification 欄位

- [x] 專案目錄名含 `-T-skill-up-` 的對話被歸為 synthetic、不進分析 — Source: Story 10
- [x] `/var/folders` 底下但不是 skill-up 的對話照常分析，不被擴大排除 — Source: Requirement: 合成對話排除 — Failure: F2
- [x] 既有的記錄旗標與 `eval-roots`／`synthetic-eval` 判定結果不變 — Source: Requirement: 合成對話排除
- [x] skill-up 的 subagent／workflow 子紀錄隨父 session 一起排除 — Source: Story 10 — Failure: F5

## Verification Log

- 2026-10-05 RED：`test_skill_up_project_dir_is_synthetic_with_children` 先失敗，原因是 `SKILL_UP_MAIN` 被送進模型（行為缺失，不是 harness 錯）。GREEN：`session-audit.test.py` 25/25、`session-audit-regressions.test.py` 6/6、`session-audit-entrypoints.test.py` 3/3 全過。
- 既有佇列：scan 的 upsert 在檔案沒變時也會更新 classification／included，所以已排隊的 skill-up 來源下一輪 scan 就會改判 synthetic，不需要清 state。
- 2026-10-05 main inline 實作，事後以 ticket-yagni-runner --manual 補審（臨時 worktree 停在 d0acc3c、base d3fb62d）：keep 65／demote 0／kill 0。
