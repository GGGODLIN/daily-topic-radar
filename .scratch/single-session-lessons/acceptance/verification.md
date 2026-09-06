# 單次解題經驗：本地驗收

## 範圍

修改的是既有 distill 的 D 線分析指令。TDD 沿用使用者批准的 waived / no-stable-seam；本報告不宣稱 RED→GREEN。測試使用合成 session 與凍結的完整正文／決策資料，真的執行修改後的分析指令，不是比對 prompt 關鍵字。

資料取得接縫有明確替換：測試不執行正式 Step 1 抽取，改由隔離 JSON 提供窗口、ledger、原始事件、正文與決策；模型以 Read 讀該檔，再產生原格式報告。這驗證候選分析與報告行為，不證明自然工作中的偵測準確率、收益、所有來源都能找到，或正式每日排檔已實跑。

## 版本與輸入

- feature_base_sha：`18b24633082a4bb4b05a112ee2f0ce847e883991`。
- 修改後 distill-weekly.sh SHA-256：`af61767e0b32b26b37cec4831fbd4f4bc8241a954c53a99c1ef6431aea62b3d4`。
- [固定測資](file:///Users/linhancheng/code/social-info/.scratch/single-session-lessons/acceptance/input.json) SHA-256：`1a52275bfdf2d4100e539cb5f9bdee6cd77830994d4845f9d43e91943f619ec4`。
- 模型透過既有 claude 入口執行；兩次回報 model 均為 `z-ai/glm-5.3-flash`。開啟 safe-mode、工具清單只保留 Read，MCP／skills／slash command 停用，不允許讀寫正式資料。installed plugin metadata 不是本次安裝。
- 完整組分析 ledger-a/b/c/d/e；無合格組只分析 b/c/d/e。兩次均 `is_error=false`、`terminal_reason=completed`。

## 預期與實際

| 場景 | 預期 | 實際 | 判定 |
|---|---|---|---|
| 單次新解法，有對應結果 | 不受跨日門檻擋住，列做法／來源／落點 | 完整組 D-1 列出移除檔尾空白紀錄；引用 a-check、a-import 與 expected=actual、checksum 未變，建議補入既有 table-import | 匹配 |
| 簡介沒寫、正文已有 | 不列為新的 D 線候選 | ledger-b 的 strip-repeated-headers 明確依正文覆蓋而排除 | 匹配 |
| 已否決做法雖有成功退出 | 不重提該做法 | ledger-c 的 accept-invalid 不列為可重用經驗；完整組區分它與保留驗證的空白紀錄處理 | 匹配 |
| 只有 assistant 自稱成功＋無關命令正常退出 | 不列候選 | ledger-d 的 pwd 與口頭成功不被當修復證據 | 匹配 |
| 有 tool_use、沒有對應 tool_result | 不列候選，只揭露限制 | ledger-e 不列候選，資料限制說明結果缺失 | 匹配 |
| 本輪全無合格經驗 | 明說 D 線無合格候選，不另列待確認 | 無合格組明寫「D 線無合格單次解題經驗」；沒有新增待辦 | 匹配 |
| 禁止模型修改資產 | 只讀測資並輸出報告 | 兩次 stream 的 tool_use 各只有一次 Read，target 都是固定測資；沒有 Write/Edit/Bash/stage_skill | 匹配於本次工具隔離環境 |

結果來源：
- [完整組原始 stream](file:///private/tmp/claude-501/-Users-linhancheng-Desktop-projects/dbd6580d-43fb-402a-863a-027bf8b584f5/tasks/bd5rkaqnt.output)
- [無合格組原始 stream](file:///private/tmp/claude-501/-Users-linhancheng-Desktop-projects/dbd6580d-43fb-402a-863a-027bf8b584f5/tasks/b3yfcxxji.output)

沒有合格的「D 線經驗」不代表整份報告都沒有提案：原有 B 線在合成資料中仍會報執行漂移。本次不改舊 B 線的候選品質；不得把此測試說成整個 distill 已無誤報。

只讀工具清單限制了測試的修改能力，不能單靠它證明正式、具有其他工具的模型永遠不會違反指令。正式變更只新增報告規則，沒有新增自動寫入程式、排程或服務。

## 既有行為與測試

- `git diff --check` 與 `bash -n scripts/local-analysis/distill-weekly.sh` exit 0。
- 既有 `local-analysis-workflow.test.sh` → `local-analysis workflow report contract: PASS`。
- 專案既有全套 pytest → `204 passed in 2.45s`；此數字代表既有 Python 測試通過，不是新 D 線有204個測試。[原始輸出](file:///private/tmp/claude-501/-Users-linhancheng-Desktop-projects/dbd6580d-43fb-402a-863a-027bf8b584f5/tasks/b2z4kao2l.output)
- 與 feature_base_sha 逐段比對：extract、cross_day、existing_and_drift、errors 四個原分析段落皆 byte-identical；只有前言新增 D、移除「唯一判斷步驟」的衝突說法、插入 D 線與報告出口。未修改 workflow 註冊、排檔 hook 或 ledger-reconcile。
- `git diff --name-only` 唯一 tracked 改動為 distill-weekly.sh；測試前後輸入與正式變更檔 SHA 不變。未以整台機器沒有變動作宣稱。
- `tdd-policy validate` → `valid:true, required_count:0, ticket_count:1`；決策 manifest 保留於 [decision.json](file:///Users/linhancheng/.claude/logs/matt-tdd/dbd6580d-43fb-402a-863a-027bf8b584f5/call_TmVebQKSpwt1mX1ND3F9Fu0v/decision.json)。

## 尚未做

程式審查已回覆 APPROVE、無功能性缺陷，提示改善已記票內 Minor 而未擴改。收尾的手動 YAGNI 判定要求刪除前置流程文件；使用者已同意保留文件並將此列為範圍誤判，票的 completed 狀態已成功寫入，沒有修改原判定或關閉 gate；未 commit、push，未啟動正式週二分析或寫正式待辦。後續日常產出的效益、模型長期遵從與正式報告消費效果尚未驗證。驗收用的原始工具結果是合成資料，不代表曾執行真實 table-ingest。
