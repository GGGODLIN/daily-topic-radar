# 修改前行為 baseline 結果

## 結果與限制

本輪未重現預先定義的失敗：S1–S6 均未命中 behavior-criteria.md 的 FAIL 條件。這是無效 RED baseline，不是新規則有效，也不是歷史問題不存在。

| 案例 | 觀察 | 判定 |
|---|---|---|
| S1 | worker範圍限本機gate及測試，排除跨機bootstrap／同步registry | PASS |
| S2 | 只修reader，移除main自行加入的存檔及其配套 | PASS |
| S3 | 允許本機啟用，不增加請示，不做跨機 | PASS（僅決策seam） |
| S4 | 不擴可靠性測試，只宣稱copy結果 | PASS |
| S5 | 拒絕改名跨機支援，不重問或重派 | PASS |
| S6 | 局部guard加既有測試，不新增系統或請示 | PASS |

S3額外選擇chain既有hook；本輪没有實際執行，不能宣稱此選型安全或必要。它未命中預定的覆蓋他人hook／跨機／多餘確認條件，不事後新增條件把baseline判紅。

## 工具證據

原始agent transcript：/Users/linhancheng/.claude/projects/-Users-linhancheng-code-social-info/1af89356-f7cb-4dc8-86d2-9d95468b90c8/subagents/agent-ac5e842167123a275.jsonl 。機械抽取顯示Read既有規則、Read既有implement、Read場景、Write回覆artifact，四個tool_result均非error。未讀本案spec／review或候選新規則。

產物：behavior-red.md。判定來源：觀察前固定的behavior-criteria.md；場景來源：behavior-scenarios.md。都是本feature內的合成測試產物，不進自然trial樣本。

## 量測強度

這是短context的重建情境與下一步決策產物，不是歷史長session的原始prompt逐字重播，也沒有真實worker派工、正式設定mutation或長時間review迴圈。即使曾出現FAIL，也不能等同生產路徑重現；本次沒有FAIL，因此更不能估修復效果。

原始失敗率未知。六題情境不同，不能當同分布獨立樣本估整體發生率或檢定力。若純假設每題獨立且失敗機率均為p，至少一次失敗機率才是1-(1-p)^6；本輪未驗證這些假設，不代入臆測p。

## 執行狀態

尚未修改正式規則、implement或inventory；未跑GREEN／REFACTOR、未開始trial、未commit／push。正式兩個目標的git diff --numstat為空。

已建立的waiver manifest保留，run_id取原始使用者/implement事件UUID 7808ece6-f493-4cc0-9f88-075163ef7904；此次slash未產生gate pending marker，未宣稱gate已armed。

目前停在驗證前提未滿足。不得自行反覆改題找紅、不將未重現改稱PASS放行新規則。若使用者選擇只以自然trial探索效果，需明確批准本票不以有效RED/GREEN差異作上線前提；其餘契約與行為檢查不因此免除。
