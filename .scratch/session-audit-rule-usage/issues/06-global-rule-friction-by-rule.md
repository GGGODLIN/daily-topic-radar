# 06 — 全域規則摩擦：一條規則一個條目

**What to build:** promote 時把 violated 的規則標記寫進摩擦檔的 `## 待折`：同一條規則只開一個條目（首行 `- ` 開頭，`signal_type=agent-observation`、`flags=speculation`、`target=rule:<檔>#<標題>`），之後的違規以縮排子行追加（每行一筆 `source_ref`＋引文），首行不改。該規則條目已離開待折（已折／已否決／休眠）後再犯，開新條目並寫上次處置與日期。沿用既有 promote 的檔案鎖。

**Blocked by:** 05 — 規則標記＋規則版本＋最小狀態

**Status:** `ready-for-agent`

**Needs:** None — a worker can check every item unaided（臨時摩擦檔）

**Validation method:** session-audit 測試對臨時摩擦檔跑 promote，檢查檔案內容；另跑一次摩擦待折掃描確認計數

**Evidence required:** promote 前後摩擦檔 diff；掃描輸出的待折項數

**TDD:** `required`

**TDD seam:** session-audit 指令列 promote 對臨時摩擦檔的寫入結果

- [ ] 同一規則 3 筆違規 → 待折 1 個條目、3 行子行 — Source: Story 4
- [ ] 條目仍在待折時新違規只追加子行，首行內容不變 — Source: Story 5
- [ ] 條目已搬到已折段後再犯 → 新條目，寫上次處置與日期 — Source: Story 5
- [ ] 只違反一次也會建立條目（無最低次數門檻） — Source: Story 4
- [ ] 同一 `source_ref` 不重複追加 — Source: Requirement: 全域規則摩擦以規則為單位 — Failure: F6
- [ ] 摩擦檔被其他 session 鎖住或帶 `friction-review-lock` 時拒寫、不留半套 — Source: Requirement: 全域規則摩擦以規則為單位 — Failure: F1
- [ ] 摩擦待折掃描只把首行算成一項，子行不被算成獨立待折項 — Source: Story 4 — Failure: F5
- [ ] 既有非規則類 candidate 的 promote 行為不變 — Source: Requirement: 權限邊界不變
