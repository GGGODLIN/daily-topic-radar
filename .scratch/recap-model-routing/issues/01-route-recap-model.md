# 01 — 把 recap wrapper 改成原生 Opus／vendor Luna 分流

**What to build:** 直接執行 recap wrapper 時，原生環境使用 native Opus；任何 vendor marker 或 custom API base 存在時，只讓 recap 子程序切到本機 CLIProxyAPI 的 Luna(max)。relay 或 keys 不可用時明確失敗，不退回 free(max)。

**Blocked by:** None — can start immediately

**Status:** completed

**Needs:** 本機 CLIProxyAPI 與現有 Claude 登入只供最小 marker 探針；假 Claude 測試可離線跑。

**Validation method:** 先以假 Claude 對正式 wrapper 建立失敗測試，再接入已驗證 route contract；執行路由、安全限制與 shell 語法測試。最後各跑一個只回固定 marker 的 vendor/native 真實探針，不讀 recap。

**Evidence required:** 假 Claude 測試顯示 0 failures；vendor marker 對應 session model 為 gpt-5.6-luna；native marker 對應 session model 為 claude-opus-5；缺 keys、remote URL、惡意 keys 與 source 都 fail closed；未執行 recap。

**TDD:** required

**TDD seam:** 以環境變數與假 Claude 執行正式 recap wrapper，觀察 child 收到的 model、base URL、credential presence 與退出狀態。

- [x] 原生環境清除外來模型映射與 relay token，明確選 native Opus。— Source: Story 2
- [x] vendor 或 custom base 環境只讀兩個 relay 設定、限定本機 8317，固定所有模型檔位為 Luna(max)。— Source: Story 1
- [x] relay／keys／路由設定無效時非零退出，且不退回 free(max)。— Source: Story 4
- [x] 正式 wrapper 的 recap 分析內容與 ledger schema 不變。— Source: Out of Scope

## Verification Log

- RED：正式 wrapper 缺 `RECAP_CLAUDE_BIN` 等 route seam，`recap-model-route.test.zsh` 非零退出。
- GREEN：`recap-model-route: checks=139 failures=0`；正式 wrapper marker 探針分別記錄 `gpt-5.6-luna` 與 `claude-opus-5`，沒有執行 recap prompt。
- Security review：無 credential、auth、endpoint、fail-open 或 RCE finding。
- YAGNI：正式 `recap-daily.sh`、`run_recap` 與正式 route test 均 keep；其餘 kill/demote 是未納入 commit 的暫時實驗或其他 ticket／session 檔。
