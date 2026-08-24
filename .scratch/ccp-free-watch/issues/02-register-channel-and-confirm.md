# 02 — 註冊 channel 並完成確認

**What to build:** 把已驗證的 `ccp-free-watch` wrapper 接進既有每日本機分析 workflow，讓無變化結果進 `silent`，有 finding 時沿用 `needs_read`、H／M／L 與 pending-actions 流程；不改 workflow 其他行為。

**Blocked by:** 01 — 建立 `ccp-free-watch` daily wrapper

**Status:** completed

**Needs:** Ticket 01 的 wrapper 與 focused test；一次真實 read-only confirmation 需要有效的本機 `ccp-free` runtime 與 OpenRouter key。

**TDD:** required

**TDD seam:** 既有 local-analysis routing contract，從 workflow args 觀察 daily channel registration、runner command、outfile、silent／needs_read mapping

- [x] 先更新 routing contract，證明 workflow 尚未註冊 `ccp-free-watch` 時測試會失敗。
- [x] 只在既有 channel registry 新增一列，設定 daily shell source 與 canonical report path；不得改 workflow control flow、hook prompt、digest、ledger、runner 或其他 channels。
- [x] Routing contract 驗證 daily due、wrapper source、outfile、runner prompt、successful non-silent `needs_read: true` 與 `__SILENT__` 不進 channels。
- [x] 執行 focused wrapper test、routing test、generic shell runner、workflow report contract、recurring promotion、hook regression 與 workflow syntax check。
- [x] [Confirmation] 以真實本機 metadata 與 OpenRouter API 跑一次 read-only wrapper probe；不得切換 model、啟動 trial、寫 ledger 或輸出 secret。
- [x] [Confirmation] 真實 probe 若沒有狀態轉移，canonical report 必須是 `__SILENT__`；若有 finding，內容必須能被 main 的通用 H／M／L 規則直接排檔。
- [x] [Confirmation] Grep workflow、hook、ledger、其他 wrapper 與 git diff，確認本次只新增 wrapper／test、registry 一列與 routing assertions，既有 unrelated working-tree changes未被修改。
- [x] [Confirmation] 掃描 test output、report、baseline 與 tracked diff，fake／real provider key、proxy token非法命中皆為 0。

## Verification Log

- RED：routing contract 加入 `ccp-free-watch` 期待後為 59/63，缺 daily membership、non-Tuesday membership、registration 與 reported count。
- GREEN：workflow registry 只新增一列並更新 channel count；routing contract 63/63。
- Regression：generic shell runner 15/15、workflow report contract PASS、recurring promotion 3/3、daily-local hook 46/46、daily-topic precheck 10/10、RBA regression PASS、Bash／Workflow／test syntax皆通過。
- Scope check：新增 wrapper＋focused test；workflow 只新增 registry 一列與 count；routing test 只新增 daily fixture、call/channel binding 與單一 assertion。Hook、digest、ledger、runner、其他 channel 未修改。
- Secret check：5 個 feature／spec檔掃描 real OpenRouter key與proxy token，非法命中 0；真實 probe 產物 secret 命中 0。
- Confirmation：canonical runner 已寫入 2026-08-24 report、mode 644、SHA marker；baseline mode 600。真實狀態不是 silent，而是 `ccp-free` key 將於 7 天內到期的有效 decision finding；未切換 model、未啟動 trial、未寫 pending-actions ledger。
- Canonical artifact scan：report、baseline、watch log、marker 共 4 檔，real OpenRouter key／proxy token 命中 0。
- TDD gate：init 時 manifest 覆蓋 2 張 required tickets，並在實作前成功 invoke `tdd`；中途狀態回報觸發 Stop hook 後 manifest 被正常 consume，因此收尾 `validate` 回 `decision manifest is missing`。未重新偽造 manifest；RED／GREEN 行為證據保留於本票與 Ticket 01。
