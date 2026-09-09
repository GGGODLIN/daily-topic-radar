## Problem Statement

每日本機分析的 recap 目前由 `local-analysis` workflow 內的一般 agent 執行。當 session 的模型檔位映射到 free(max) 時，recap 會反覆因 180 秒無進度而中止；按 session 拆成四批或八批仍無法可靠完成。使用者希望明天執行每日本機分析時，recap 不再受啟動指令的模型映射影響；但用原生 `claude` 啟動時，仍使用原生 Claude 模型。

## Solution

把 recap 從一般 workflow agent 拆成獨立子程序。入口先啟動 routed recap，再讓 `local-analysis` workflow 跳過內建 recap。原生環境的子程序清除外來模型映射與 relay token，使用 native Opus；任何 vendor marker 或 custom API base 存在時，子程序固定改走本機 CLIProxyAPI，使用 `gpt-5.6-luna(max)`。兩邊完成後，main session 才依既有規則整理 digest。

## User Stories

1. As an 每日本機分析使用者, I want vendor session 的 recap 固定使用 Luna(max), so that free(max) 不會因啟動指令映射而接手 recap。[user: "不論我是用哪種指令啟動cc"]
2. As an 原生 Claude Code 使用者, I want 原生啟動時保留 native Claude 模型, so that 純原生 session 不會被導向第三方 relay。[user: "但如果我是用claude啟動的話，還是希望用原生的"]
3. As an 每日本機分析使用者, I want 新路由在明天執行前接好, so that 下一次每日分析直接使用新設計。[user: "我明天跑每日本機希望是跑新的設計"]
4. As an 維護者, I want relay 或 keys 無法使用時明確失敗, so that 系統不會靜默退回 free(max)。[inferred]
5. As an 維護者, I want routed recap 與其他 channel 的結果在 digest 前會合, so that recap 失敗不會被誤報成完整分析。[evidence: daily-local trigger 的 failed/channel 排檔規則]

## Implementation Decisions

- routed recap 必須以 executable 子程序執行，且拒絕被 source；主 session 的環境不因子程序改動而改變。[evidence: recap-route-pilot source guard、child capture 與 code review]
- 原生環境定義為 `CC_VENDOR` 與 `ANTHROPIC_BASE_URL` 皆空；原生分支清除外來模型檔位與 `ANTHROPIC_AUTH_TOKEN`，明確選 native `opus`。[evidence: marker `ROUTE_NATIVE_V2_OK` 對回 `claude-opus-5` session]
- 其他環境一律只讀 relay 設定中的 `CLIPROXY_BASE_URL` 與 `CLIPROXY_KEY_CC`，不 source keys 檔；relay URL 只接受本機 8317 port。[evidence: recap-route-pilot 210-check fake harness 與 security review]
- vendor 分支清除 `ANTHROPIC_API_KEY`，把主模型、Fable、Opus、Sonnet、Haiku 與 subagent model 都固定成 `gpt-5.6-luna(max)`。[evidence: marker `ROUTE_LUNA_V2_OK` 對回 `gpt-5.6-luna` session]
- `local-analysis` workflow 新增明確參數以跳過內建 recap；預設行為保持相容，只有 daily-local 入口傳入跳過值。[inferred]
- keyword trigger 與 `/daily-local` command 必須使用同一個 routed recap 入口與同一組 workflow args，避免兩條觸發路徑漂移。[evidence: 現有 daily-local command 明文以 hook heredoc 為排檔規則單一來源]
- routed recap 要先啟動，workflow 可並行執行；main session 收到兩者結果後才產 digest。recap 成功時讀既有 report，失敗時把確切錯誤列進 failed。[inferred]

## Testing Decisions

- 沿用最高層路由 seam：假 Claude 捕捉實際收到的 model、route、base URL 與 credential presence，不測內部 shell 實作細節。
- 覆蓋 native、13 種 vendor marker、custom base、缺 keys、remote URL、惡意 keys、source guard 與 child capture。
- 保留兩個最小真實 marker 探針：vendor 路徑必須記錄 `gpt-5.6-luna`；原生路徑必須記錄 `claude-opus-5`。探針不讀 recap。
- 修改 `recap-daily.sh` 後執行新路由契約測試；修改 daily-local command/hook 前依各自契約測試規則枚舉並執行命中的測試。
- 修改 `local-analysis.js` 後執行 agent contract validator 與一個不跑 recap 的 workflow smoke test，確認 skip 參數會讓 recap 不建立 agent call，且回傳結構明示 external recap。
- 不執行完整 recap 作為本次驗證；完整內容與 ledger 行為留給明天真實每日分析觀察。

## Out of Scope

- 不再拆分 recap session 資料。
- 不修改 free(max)、Luna 或其他 channel 的全域模型映射。
- 不修改 Workflow engine 的 180 秒 watchdog。
- 不替其他 workflow 建立跨 endpoint 路由。
- 不改 recap 的分析內容、規則糾正分類或 ledger schema。

## Further Notes

路由原型與測試收據位於 `social-info/.scratch/recap-route-pilot/`。分批否決收據位於 `social-info/.scratch/recap-split-pilot-2026-09-07/RESULT.md`。

實作驗證結論：正式 wrapper 的 vendor marker 探針實際記錄 `gpt-5.6-luna`，原生 marker 探針實際記錄 `claude-opus-5`；假路由契約 139 項全過。`external_recap:true` 會從 workflow due 清單移除 recap，並以 `external_channels` 回傳外部 report 指針；command 與 keyword hook 都要求 recap Bash 與 workflow 完成後才排檔。Security review 無 runtime finding，code review 抓到的重複 recap 風險已修正並以 command contract 重驗。

明天的首次真實每日分析仍是完整內容與 ledger 的第一筆 live 驗證；本次沒有執行 recap prompt。完整模組測試有一個既有 beads-aging 失敗：feature base 與目前版本都為 22 PASS／4 FAIL，與本功能無關，未順手修改。
