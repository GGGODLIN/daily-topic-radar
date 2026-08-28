# 02 — 真站與完整回歸確認

**What to build:** 用完整測試與真實 skills.sh 回應確認新 source 在實際 daily radar 管線中可用，且驗證過程不污染正式 state.db 或當日正式 report。

**Blocked by:** 01 — skills.sh 榜單端到端接入

**Status:** ready-for-agent

**Needs:** 可連線至 https://skills.sh

**TDD:** waived

**TDD waiver:** non-executable-artifact

**TDD waiver approved:** ticket-breakdown-user-approved

- [x] **[Confirmation]** 完整 pytest 通過，既有 fetcher、dedup、pipeline 與 markdown 行為沒有回歸。
- [x] **[Confirmation]** 真實 fetch smoke test 從 Trending 與 Hot 各取得非零項目。
- [x] **[Confirmation]** smoke test 輸出包含單一「Agent Skills (skills.sh)」獨立區塊與可讀的榜別、rank、installs。
- [x] **[Confirmation]** smoke test 沒有寫入正式 state.db 或當日正式 report。

## Verification Log

- 完整 pytest：167 passed。
- 最終真站：Trending 25、Hot 25；dedup 後 49 項，1 項保留 `also seen at`。榜單會即時變動，重疊數不視為固定契約。
- 渲染：單一 Agent Skills 區塊、rank 與 installs 均存在。
- smoke verification 使用 temporary state.db 與記憶體渲染。
- Minor：第一次 shell wrapper 在來源已成功後，因 zsh 的 `status` 是唯讀變數而回傳 exit 1；改用不同且更完整的 in-memory verification，產品程式未受影響。

### Review findings

- `skills_sh` 雙榜全失敗時被誤記為成功：fixed；全敗會重新 raise，恢復 pipeline retry、failure 與 error_class。
- L2 batch winner 後 `seen_items_by_id` 留 stale mapping：fixed；舊 canonical URL 改指向存活 winner。
- L1 同 URL 跨 tier 選擇方向：no change needed；first-item-wins 是既有 L1 行為，本功能兩榜同 tier，改選擇規則會擴大 scope。
- skills.sh 排序特例改成常數集合：skipped；屬 maintainability 改善，現有 group key 只有此 source 使用，沒有功能錯誤。
- compact installs 小寫 suffix：no change needed；現有 `.upper()` 已支援，小寫輸入不會遺失；非標準 garbage 刻意跳過。
- 單榜失敗只進 stderr：accepted limitation；符合既有多子來源 fetcher 慣例與 ticket 的部分成功要求；雙榜全敗已修。
- 非 HTTP href hardening：skipped；低風險上游受控輸入，未影響本次核心行為。
- Trending／Hot 順序測試：no change needed；fixture 已讓 Hot installs 高於 Trending，仍斷言 Trending 先出，能抓到 engagement 重新排序。
- 全 repo Ruff：changed files 全綠；另有 16 個既有錯誤位於未修改檔，未納入本功能修正。
