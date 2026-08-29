# 02 — 登錄七天觀察 trial

**What to build:** 在既有 trial ledger 登錄 Claude Code sticky prompt pin watcher，讓 2026-09-05 到期時主動提醒 review；review 即使看到 issues 與 CHANGELOG 都沒更新，也必須取得候選版本直接測 sticky prompt。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**Needs:** None — a worker can check every item unaided

**TDD:** waived

**TDD waiver:** docs-only

**TDD waiver approved:** ticket-breakdown-user-approved

- [x] active ledger 新增唯一 H2，started=2026-08-29、status=active、review=2026-09-05。
- [x] detail 指針與 detail 檔前兩行 identity 完全一致。
- [x] detail 明寫 issue／CHANGELOG watcher 只是早期訊號，不保證抓到偷偷修復。
- [x] detail 明寫 review 日無論遠端狀態是否變化都要直接做候選版本行為測試。
- [x] detail 明寫仍壞時延長 pin；恢復後才移除 DISABLE_AUTOUPDATER 並升版。
- [x] 不修改其他 trial entry。
