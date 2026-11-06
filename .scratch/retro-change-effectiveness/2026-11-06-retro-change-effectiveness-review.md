# 規格審查第 2 級：retro 改動有效性 spec（2026-11-06）

受審：[spec.md](/.scratch/retro-change-effectiveness/spec.md)，YAGNI 審查後版本（commit d767602）。

## Run

- 審查級別：第 2 級（席位 A 架構批判、席位 B 新人理解），使用者選「先再審一輪做得對不對」。
- 前輪紀錄：同目錄 `2026-11-06-retro-change-effectiveness-yagni-review.md` 有 11 條 disposition，其中 7 條 actionable，沒有連續零 actionable 的橡皮圖章訊號。
- 第一輪：原定照跨模型規則走 pi-subtask（GPT_SOL），兩席都在啟動時回 `model_state_mismatch`，退回原生 architecture-critic（Opus），屬同族自審。
  - 席位 A：Blocker 1、High 9、Medium 13、Nit 7，附自洽表 23 列、引證表、驗證設計 8 項（7 FAIL）。
  - 席位 B：兩段重述，其中「判定怎麼產生」無法重述；Blocker 1、High 5、Medium 11、Nit 6。
- 修 pi-subtask：根因是 pi 1.4.0 只有在模型設定寫出 xhigh 對照時才支援 xhigh，runner 沒寫，xhigh 被降成 high，核對失敗。已在 `~/.claude` 修正並加回歸測試（runner 測試 32/32、接線測試 3/3）。
- 第二輪：兩席改由 `gpt-6.1-sol(xhigh)` 重審同一份規格（已含第一輪兩條 Blocker 的修正）。
  - 席位 A：High 4、Medium 4、Nit 1，自洽表 16 列、引證表、驗證設計 8 項（6 FAIL）。
  - 席位 B：兩段重述正確；Blocker 1、High 4、Medium 1。
- 收斂：
  - 承重問題：「一次獨立事件」沒有貫穿全流程的定義，計數、判定與提議都建立在它上面。
  - 第一個驗證：用改好的 ingest 匯入 2026-11-04 那一輪，照事件定義拆分 P6 的 10 筆來源觀察，看匯入後的獨立事件數是否等於人工拆分結果；一輪資料、一次執行即可判斷。

## 合併後的 findings 與處置

| ID | 內容 | 來源席 | 處置 |
|---|---|---|---|
| F1 | ingest 不讀 cross 的追蹤觀察，逐筆記錄最後一步仍靠手動 | Opus A B1 | 改為 ingest 直接寫入觀察；研究筆記錯誤說法加更正行 |
| F2 | 判定怎麼產生（時機、執行者、對應規則）沒寫，新人重述失敗 | Opus B B1 | 新增判定流程 |
| F3 | 事件與觀察的鍵沒分開，會少算或多算 | GPT A H1、Opus A H2、GPT B H4、Opus B M3 | 事件用工具 call ID 或動作行號，觀察＝事件＋改動 |
| F4 | 時間戳證明不了載入的是新版；缺「載入不晚於事件」 | Opus A H1、GPT A H2、GPT B H3、Opus B M1 | 共用判斷讀 `instructions` 附件；時間順序約束；判不出時記未查 |
| F5 | 「沒載入」的事件被丟掉，三類失敗原因分不出來 | Opus A H3、Opus B H4 | 新增「沒載入」結果與「涵蓋與否」欄 |
| F6 | 規則以檔案＋標題識別會混算；查詢用 session 開始時間 | Opus A H4、M7，GPT A M5，Opus B M5 | 段落錨點查詢、事件時間過濾、回傳覆蓋量；第一批數字標為待重算 |
| F7 | 沿用問題層級的決策會翻掉批准狀態 | Opus A H6、GPT A H3、Opus B M6 | 新增提議決策紀錄 |
| F8 | 判定沒有規則、作者兼裁判、停止推進可能被判有效、pilot 兼校準與判定 | Opus A H7、GPT A H4、GPT B H5、Opus B H6 | 判斷式優先、盲化判斷、同時檢查任務推進、pilot 結果凍結且只算校準 |
| F9 | 第一張工作單測錯組、案例只挑失敗 | Opus A H8 | 先用原樣 context 重現，再量舊規則組；加一個生效前案例 |
| F10 | 回放做不到時副作用檢查沒有方法 | GPT B Blocker | 第三層另跑不適用任務對照；做不到則標「副作用未檢查」 |
| F11 | 只存下一步無法稽核或接續 | GPT A M7、Opus A N1、Opus B M10 | 保存小型執行紀錄，不存整段前文 |
| F12 | 模型不可用時第三層同樣失效；暫時性錯誤會被當成做不到 | Opus A M6、Opus B M9 | 直接改走第四層；暫時性錯誤先重試；摘要列出卡在第四層的數量 |
| F13 | accumulation.md 不在更新範圍，會給出矛盾指示 | Opus A M9、GPT A M8 | 納入三份文件同步與交接測試 |
| F14 | 只追加的改判、重播與 P3 範圍沒有一致契約 | GPT A M6、Opus A M8 | 追加更正紀錄；ingest 分列四種結果；P3 比該批最新狀態；重播零新增 |
| F15 | `effective` 在問題層級、判定在改動層級；值域沒寫 | Opus A M3、GPT B M6、Opus B M11 | 逐改動顯示；補對照 |
| F16 | 元件與流程沒介紹 | Opus B H1、GPT B H2 | 新增「名詞與流程」 |
| F17 | 兩種改動類型推不出五種載入規則；重犯條件和既有 condition 重複 | Opus A M1、Opus B H2 | 改動登記「類型＋位置」清單；重犯條件就是 condition |
| F18 | 「判為無效」和判定值撞名 | Opus A M2、Opus B H3、GPT A Nit | 改為「驗證不成立，判證據不足」 |
| F19 | 3 場門檻與「可調整」字樣矛盾 | GPT A Nit | 3 場固定，改要使用者重新決定 |
| F20 | evidence 標籤指向 research.md 沒有的內容 | Opus A M11 | research.md 補「第三輪補充」 |
| F21 | 回歸測試會把私人輸入帶進公開 repo | Opus A M12 | 先去敏 |
| F22 | 回放做不到時整場次數沒有值 | Opus A M13 | 第一張工作單一併訂整場次數 |
| F23 | 規則違反已有 session-audit 摩擦檔管道，新清單會重複 | Opus A H5 | 上呈使用者 |
| F24 | 舊紀錄處理未決，會擋住判定與第一批 | Opus A H9、GPT A M6 | 上呈使用者；決定前不得把舊時間當事件時間 |
| F25 | hook 副作用數字與分模型場數未獨立重驗 | GPT A 引證表 | 規格標為實作後重驗 |
| F26 | 其餘小項：Argon 是否免費、全表掃描效能、兩個門檻散在不同處、訪談代號、用詞不一 | Opus A N2–N7、Opus B N1–N6 | 不改規格 |

## Dispositions

- disposition: F1 | actionable | main=actionable | user=n/a | ingest 不讀 observation_proposals（grep 0 筆）已實證
- disposition: F2 | misread | main=misread | user=n/a | 新人席重述失敗，補判定流程
- disposition: F3 | actionable | main=actionable | user=n/a | 兩輪四席一致，沿用 accumulation.md 既有動作位置契約
- disposition: F4 | actionable | main=actionable | user=n/a | instructions 附件存在且 compact 後重記
- disposition: F5 | actionable | main=actionable | user=n/a | obs-23 B 類實例
- disposition: F6 | actionable | main=actionable | user=n/a | session-audit.py:3411-3412 自述混算
- disposition: F7 | actionable | main=actionable | user=n/a | retro-store.py:580-585 批准檢查以問題為單位
- disposition: F8 | actionable | main=actionable | user=n/a | 驗證設計兩輪共 13 項 FAIL 的共同根因
- disposition: F9 | actionable | main=actionable | user=n/a | 兩個案例當時已載入新規則
- disposition: F10 | actionable | main=actionable | user=n/a | 新人席重述斷裂點
- disposition: F11 | actionable | main=actionable | user=n/a | 不存前文的前提下保留可稽核配方
- disposition: F12 | actionable | main=actionable | user=n/a | 單點失效追蹤
- disposition: F13 | actionable | main=actionable | user=n/a | accumulation.md:31-38 與新規則衝突
- disposition: F14 | actionable | main=actionable | user=n/a | retro-store.py:282-298 同 id 不同內容拒收
- disposition: F15 | actionable | main=actionable | user=n/a | retro-store.py:759、:782
- disposition: F16 | misread | main=misread | user=n/a | 兩輪新人席都無法從文件得知元件關係
- disposition: F17 | actionable | main=actionable | user=n/a | retro-store.py:635 condition 必填
- disposition: F18 | actionable | main=actionable | user=n/a | 字面撞名
- disposition: F19 | actionable | main=actionable | user=n/a | 使用者已拍板 3 場
- disposition: F20 | actionable | main=actionable | user=n/a | 補資料與量法
- disposition: F21 | actionable | main=actionable | user=n/a | repo 公開
- disposition: F22 | actionable | main=actionable | user=n/a | 自洽表第 8 列
- disposition: F23 | tradeoff | main=tradeoff | user=accepted | 使用者選 a：已有改動的規則走新清單，摩擦檔跳過，其他照舊
- disposition: F24 | tradeoff | main=tradeoff | user=accepted | 使用者選 a：舊觀察標舊格式不參與判定，舊改動第一次判定時補登
- disposition: F25 | actionable | main=actionable | user=n/a | 第一批數字標為實作後重驗
- disposition: F26 | immaterial | main=immaterial | user=n/a | F26：都不改變範圍、行為或風險，實作時順手處理即可
