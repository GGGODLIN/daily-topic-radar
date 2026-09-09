# Recap split pilot 結果

## 狀態流

單體版失敗 → 四批版只完成第一批 → 八批版第一批失敗 → 不整併正式每日本機流程。

## 實際結果

### 單體版

Run：`wf_e310ab96-c24`。

單體階段沒有有效結果。journal 記錄 5 個 started agent；main 停止前均有工具使用，之後中止。這輪沒有進入分批階段，不能當成分批測試。

### 四批版

Run：`wf_76cd3851-c1e`。

- batch-1：status=ok。固定輸入 70,621 bytes，10 sessions；agent trace 為 `z-ai/glm-5.3-flash`。成功前 engine 曾另啟動失敗嘗試，實際 retry 數依 journal 為準。
- batch-2：technical_failure，原文 `Error: agent stalled on all 6 attempts (no progress for 180000ms each)`。固定輸入 71,486 bytes。
- batch-3、batch-4、synthesis：依腳本停止規則跳過。

此輪證明分批可以保存局部成功，但四批無法可靠完成整天 recap。

### 八批版

Run：`wf_bc4f9f12-2ef`。

- extract-1：technical_failure，原文 `Error: agent stalled on all 6 attempts (no progress for 180000ms each)`。
- 其餘七批、兩個中間統整與 final：依腳本停止規則跳過。
- 第一批固定輸入 41,621 bytes，只含 1 個 session。workflow 收據為 agents_done=0、agents_error=1、tool_uses=34、subagent_tokens=0；其中 0 tokens 是 workflow usage 收據，不代表 trace 沒有模型輸出或工具活動。
- journal 有 6 個 started agent；各 trace 均為 `z-ai/glm-5.3-flash`，有 3–8 個工具呼叫，最後多在工具後約 3 分鐘收到 `[Request interrupted by user]`。

此輪顯示把資料降到單一 session 仍會停滯。輸入 bytes 不是充分解釋；同時四批版較大的第一批曾成功，因此不能用單次結果推導固定大小門檻。

## 判定

- **受測模型自己的能力**：沒有有效證據證明 free(max) 無法理解 recap。部分批次曾產出結構化結果；失敗發生在完成前。
- **整套派工系統**：指定 free(max) 時，單體、四批與八批都無法穩定完成。共同表面是 engine 的 180 秒無進度中止；wrapper 雖有較長預設值，是否傳入或被 runtime 採用尚未證實。
- **這次實驗是否有效**：足以否決「只按 session 拆成四或八批，就能可靠解決」；不足以比較最終報告品質，因沒有一輪分批版完成所有抽取與統整。

## 不整併的理由

正式 recap 不改。分批能留下局部 checkpoint，但也增加 agent 數與統整階段；目前沒有端到端成功收據。把這版接入每日分析會把可重現的部分失敗變成正式行為。

## Collector 安全收尾

初次 Python review 發現未加引號 credential 遮罩缺口；修正後複查再發現 AWS SDK camelCase 欄位缺口。兩輪均已精確修正並加入 `test_secret_mask.py`。

最終獨立複查結果：`SecretAccessKey`、`secretAccessKey`、`AccessKeyId` 與 `add_window_row` 最終輸出都將 fixture 遮罩，`secret_masks=1`；3 tests OK，`py_compile` 通過。main 以最終 scanner 重掃曾送入模型的 14 份固定 JSON，credential 形狀命中 0。這只覆蓋明列格式，不承諾辨識任意秘密文字。沒有重跑 collection 或任何 Workflow。

一般 code-reviewer 因 API stream INTERNAL_ERROR 未產 verdict；Python 安全 reviewer 完成最終複查。此缺口屬暫時 collector，不影響正式 recap 程式；實驗結論維持不整併。

## 已知限制

- 固定輸入是 live files 重建，不是早上執行時的歷史 snapshot；manifest 記錄 1 個讀取中變動、但該檔沒有納入 window session。
- pilot 沒帶正式 alias 表與當日 rule-family 統計，不能宣稱完整覆蓋舊 recap 契約。
- agent trace 與 bridge request 缺共同 request ID。上游 TTFT 與 agent 中止只可做時間相鄰推論。
- 沒有再測「session 內切段」或改 watchdog。依同 root cause 的修正額度，本輪停止，不追加第三種拆法。

## 產物

- 固定資料、collector 與測試：`/Users/linhancheng/code/social-info/.scratch/recap-split-pilot-2026-09-07/`
- 四批 script：`/Users/linhancheng/.claude/projects/-Users-linhancheng-Desktop-projects-gggodlin-blog/d84fd7e1-8f7b-4d78-85b3-5172d40e9b4a/workflows/scripts/recap-split-only-pilot-wf_76cd3851-c1e.js`
- 八批 script：`/Users/linhancheng/.claude/projects/-Users-linhancheng-Desktop-projects-gggodlin-blog/d84fd7e1-8f7b-4d78-85b3-5172d40e9b4a/workflows/scripts/recap-split-eight-pilot-wf_bc4f9f12-2ef.js`
