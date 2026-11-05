# dotey 貼文與 retro 自我進化論文：研究筆記（2026-11-05）

本檔是 [spec.md](/.scratch/retro-change-effectiveness/spec.md) 的證據來源。原始抓取檔（貼文留言、論文轉文字檔）沒有放進 repo，只保留公開連結。

## 來源

- 貼文：[dotey 2026-11-04 貼文](https://x.com/dotey/status/2117706197797114009)（LCU 讀正文；`x-fetch.sh --full` 補 24 則留言）
- 論文：[arXiv 2610.34938 PDF](https://arxiv.org/pdf/2610.34938)，v1 2026-10-26 提交；下文 `paper-flow.txt:行號` 是對 v1 PDF 執行 `pdftotext`（不加 `-layout`）後的行號，可重現
- 論文測的 skill：[mattpocock/skills 的 retro SKILL.md](https://github.com/mattpocock/skills/blob/b0618bc/skills/engineering/retro/SKILL.md)（commit b0618bc）
- 貼文附圖是論文第一頁截圖，沒有額外資訊

## 論文設計（paper-flow.txt:114-259）

1. 30 題 SWE-bench Verified：前 10 題從五個 repo 隨機抽；後 20 題刻意挑「先前碰過環境問題」的 repo（Astropy、pytest、Sphinx、SymPy）。
2. Claude Code 2.1.295＋Opus 5.5 非互動跑完每題，再在同 session 跑 /retro，得到 125 條建議。
3. 編碼：兩個 LLM（Fable 5.1 讀完整 trace、GPT-6.1-sol 讀 trace 摘錄）各自標，trigger 一致 109/125（κ=0.67），機制一致 109/125（κ=0.84），第一作者裁 91 處分歧。
4. 符合條件的 33 條 → 實作 24 條 → 11 條能在別題重現 → 加 1 條作者手寫的 pre-commit 對照組 = 12 項改動、13 次轉移測試。
5. 每次轉移：同 repo 另一題，先確認環境會重現問題，原設定跑 5 次、改後設定跑 5 次。量的是「目標動作有沒有再出現」（recurrence），不是任務成功率。
6. 三個分開記錄的狀態：encountered（這次 session 真的碰到這類問題）、covered（改動的內容剛好涵蓋碰到的那幾個實例）、loaded／consulted（改動進了 context／agent 真的讀了檔或跑了指令）。

## dotey 引用數字逐條核對

| dotey 說 | 論文原文 | 位置 | 結果 |
|---|---|---|---|
| 125 條建議，101 條有依據 | 101 experience-driven、19 prior-driven、5 weak | 358-359 | ✓（dotey 沒提 5 條 weak） |
| 19 條無關，18 條是格式檢查 | lint 佔 19 條 prior-driven 的 18 條 | 333-334 | ✓ |
| /retro 指令把「沒有 pre-commit／CI」算 finding | 「A repo with no guardrail … is itself a finding」 | SKILL.md:18；論文 334-336 | ✓ |
| pre-commit 對照組 5 次一次都沒讀 | 檔案在 5 個 session 都存在，沒有 session 讀或跑 pre-commit | 366-369 | ✓，但漏了原因：這些 session 根本不 commit，commit hook 不會觸發（327-328） |
| 24 條落實、11 項可測、加 1 對照共 12 項 | 同 | 196-204 | ✓ |
| 13 次測試 7 次有效 | 同 | 361-366 | ✓ |
| SymPy bin/test：5/5 → 0/5 | 同 | 375-377 | ✓ |
| Astropy 失敗清單：3/5 → 0/5 | 同 | 378-383 | ✓ |
| 剩 5 項 1 項降到 2/5、4 項沒變 | 同 | 384-398、490-493 | ✓ |
| Sphinx 控制題 0/5 → 4/5，82s → 245s | 同 | 399-403、417 | ✓ |
| SymPy CLAUDE.md 順帶的 lint 指令讓 88s → 156s | 同 | 411-415 | ✓ |
| 成功次數最多差 1 次 | 同 | 407-409 | ✓ |
| Claude Code＋Opus 5.5 | Claude Code 2.1.295 with Opus 5.5 | 119 | ✓ |

結論：dotey 的數字全部對得上原文，沒有誇大。

## 貼文沒講、但值得知道的

1. **自動檢查類建議大多是空殼**：38 條「加自動檢查」的建議裡只有 9 條在任務環境裡真的能報錯；另外 28 條要嘛缺工具、要嘛是 commit hook 而 session 從不 commit（325-330）。
2. **失敗原因不是 AI 沒讀**：所有改後 session 都把 context 檔載入了，通常也真的去讀引用的檔（369-371）。沒效的 5 項是內容沒涵蓋下次碰到的那幾個測試，不是被忽略。
3. **規則寫法會製造額外工作**：Sphinx 那份清單寫「不在清單裡的失敗一律當成你造成的」，清單不完整時反而觸發它本想避免的比對（400-404）。
4. **編碼是 LLM 做的**：人只裁分歧，κ 反映兩個 LLM 對兩種輸入的一致度，不是人類評分者一致度（170-171）。
5. **作者自承的限制**：測試題是挑過會重現問題的，可能高估效果；每組 5 次、7 個有效事件，不能證明「涵蓋與否」就是差異的原因（多個屬性同時不同、分組在標記之後）；第一作者標記時看得到 agent 讀了哪些檔，不完全盲測；實作改動時 Claude Code 在原 session 裡，改動檔可能帶了建議文字以外的資訊（494-505）。
6. **論文很短**：4 頁、arXiv 原始檔 11 KB，兩個研究問題，偏短論文／workshop 規格。資料與 trace 放在 Zenodo（526，連結未點開驗證）。

## 相關研究（論文第 II 節引用，未逐篇讀原文）

- Lulla 等：Codex 跑 124 個 PR，有 AGENTS.md 時中位耗時 99 → 70 秒（90-91）
- Zhang 等：58 題 SWE-bench Verified，隨機社群規則檔和精選規則通過率相同（92-94）
- Lin 等「Harness updating is not harness benefit」：區分更新 harness 與真正受益（555-560）
- BenchTrace：文字冒險跨子任務轉移經驗，六種方法全部輸給不更新記憶的 baseline（106-109）

## 留言區（24 則，非全量）

主要論點分四類：

- 要有可驗收標準才收建議（anyueyi1、iJimmyChan、AutoWithAI_CN、JasonYu0205、yanyuli17「每條建議附可重現的失敗案例，沒有就不收」）
- 規則膨胀、越改越長（Sh1nj1Re1、iJimmyChan、Lmin0711「最後沒人敢刪」）；ruoye_wu：「最有價值的復盤結果可能是刪掉一條過時規則」
- 規則要寫觸發條件（johnroodepic：命令式規則沒有觸發條件，不是全觸發就是永不觸發）
- 批評研究方法太隨意（ErwinWu000，一句話無論據）

## 追問更正：/retro-workflow 就是 Matt 的 /retro（2026-11-05 第二輪）

- 之前寫「本機沒裝 /retro，同類機制是 /retro-workflow」，框架錯了。`~/.claude/commands/retro-workflow.md` 第 9 行寫「Matt 的 retro 與 writing-for-agents 是方法」；`~/.claude/workflows/retro-workflow.js:39` 要求每個工作單位先讀 `references/retro-workflow/matt-retro.md`；`provenance.md:3` 釘在 Matt revision 49dd158。matt-retro.md 也含「is itself a finding」那句（`grep -c` = 1）。
- 本機版在 Matt 原版外加了：多場選樣、跨場合併、兩席反向查核、≥4 獨立場次門檻（第 76 行）、修後觀察（第 19 行）、不自動套用（第 88 行）。

### 累積庫數據（`retro-store.py list` / `list --with-changes`，2026-11-05 跑）

| 項目 | 數字 | 代表什麼 |
|---|---|---|
| 問題總數 | 1194 | 歷來入庫的候選問題 |
| 批准 | 46 | 你拍板要處理的 |
| 有生效改動 | 44 | 已實作並記了生效時間 |
| 有觀察證據 | 29 / 44 | 至少有一次 opportunity_met 或 recurrence |
| 證據不足 | 15 / 44 | 還沒碰到適用機會或沒查 |
| 觀察總筆數 | 212 | 正確執行 77、復發 15、其餘 120（212−77−15）是不適用或未查 |
| 出現過復發的改動 | 10 | 改了但同類事件又發生 |
| 有正確執行且零復發 | 19 | 目前看起來有用，但未正式判定 |
| `effective` 已判定 | 0 / 44 | retro-store.py:782 寫死 None |

復發最多的兩條：「無過濾的全樹遞迴搜尋或未截斷輸出」（復發 4、正確 2、觀察 7）、「LCU cua_repl API 盲猜與單次塞入過多等待導致逾時重置」（復發 3、正確 1、觀察 4）。

格式檢查偏向：標題關鍵字（lint、pre-commit、guardrail、CI、eslint 等）在 1194 條中只命中 6 條，內容都是真實事件，沒有「repo 沒 guardrail 所以該加」型。原因未驗證，只搜了標題。

### 對照論文的量測點

| 論文量測點 | 你的 /retro | 出處 |
|---|---|---|
| 有沒有碰到問題（encountered） | 有：opportunity_met 與 not_applicable 分開記 | retro-store.py:29 |
| 行為有沒有變（recurrence） | 有：recurrence | retro-store.py:29、761-762 |
| 改動適用範圍（repo 通用 vs 單場實例） | 間接有：≥4 獨立場次才進拍板，單場實例清單進不了主項 | retro-workflow.md:76 |
| agent 有沒有讀到規則（loaded／consulted） | 沒有：observe 只有 opportunity_met／recurrence／not_applicable／unreviewed | retro-store.py:29 |
| 不相關任務的副作用（control task） | 沒有：不適用場次記 not_applicable 就結束 | retro-store.py:29、763 |
| 無效改動撤回 | 沒有：PROGRESS 只有 planned／in_progress／blocked／implemented；effective 永遠 None | retro-store.py:28、782 |

## 第三輪研究：用論文框架檢查你的 15 筆復發與 hook 副作用（2026-11-05）

方法：3 個 routed-judge agent 讀原始 session jsonl，逐筆分類 10 個改動的 15 筆 recurrence 觀察；1 個 agent 量 4 支 hook 生效後的觸發與多餘觸發。全程唯讀，沒有寫入 retro-store。main 抽查 3 項，都對得上：e5ac1e0e.jsonl:24 新規則在 context、:145 沒排除就 grep `~/.claude`；validated/s23-u01.json:1005 結果填 recurrence，:1019 限制欄寫「早於 change 生效時間」；`~/.claude/logs/pipestatus-zsh-gate-audit.jsonl` 生效後 44 筆、16 個 session。

### 分類定義
- A 觀察無效：事件在生效前、session 載入的是舊版，或其實不是同類問題
- B 沒載入／沒觸發
- C 載入了，但內容沒涵蓋這次的狀況
- D 載入也涵蓋了，agent 沒照做
- E 證據不足

### 15 筆結果

| 問題 | 觀察 | 改動類型 | 分類 | 一句理由 |
|---|---|---|---|---|
| 10-19 P2 公開測試混入私人資料 | 10-31 obs-03 | 單一 repo 測試 | C（也沒載入） | 改的是 magpie 的掃描測試，事件發生在 collie，清單也抓不到那個本機 profile 名稱 |
| 10-19 P5 先輸出過大再縮範圍 | 10-31 obs-01 | command | C（也沒載入） | 過大輸出出現在查歷史，不在 self-closeout；self-closeout 本身有照做 |
| 10-20 P1 問了沒等回答就做 | 10-31 obs-18 | 資料檔＋文件 | C（也沒載入） | 改動只修正舊紀錄的出處，不約束「等回答」這個行為 |
| 10-26 P2 合併刪減追蹤欄位 | 11-04 s01 | script 驗收器 | C | 驗收器有跑也通過，但只檢查「問題＋改動」組合在不在，不檢查 outcome |
| 10-28 P2 外層成功內層錯誤 | 11-04 s29 | hook | C | hook 的測試指令清單沒有 `claude plugin test` |
| 10-30 P6 全樹搜尋／過大輸出 | 10-31 obs-22 | always-on 規則＋script | C | 有照規則排除，但清單沒有 memory/；`--summary` 仍有 94.6KB；彙總裡另有生效前與舊版 session 的單場 |
| 10-30 P6 | 11-01 obs-02 | always-on 規則 | C | 有照規則排除也有 TaskStop，卻卡在不在清單上的 file-history、trials；另 9 筆來源未核 |
| 10-30 P6 | 11-03 s11 | always-on 規則 | D | 新規則已載入，第一次搜 `~/.claude` 根目錄沒排除（該 session 跑 gemini-4-argon 代理） |
| 10-30 P6 | 11-04 s16 | always-on 規則 | D | 新規則已載入，搜兩個點名根目錄沒排除，命中的 cache、plugins 都在清單裡 |
| 10-30 P7 pgrep 自命中 | 10-31 obs-23 | command | B | 修正只在要 invoke 才載入的 workflow-monitor，手寫 pgrep 的 session 看不到 |
| 10-30 P8 背景 subagent 未回就輸出 | 10-31 obs-24 | skill | A | 事件在生效前約 18 小時，也沒走 check-my-stack；單場報告限制欄已寫明，結果欄仍填 recurrence |
| 10-30 P13 LCU API 盲猜／舊索引 | 11-01 obs-15 | always-on 規則 | D | 新規則已載入，填完欄位仍沿用舊索引 48，被回 stale |
| 10-30 P13 | 11-03 s07 | always-on 規則 | D | 改過表單後仍用 20 分鐘前的索引連點（這次沒出錯） |
| 10-30 P13 | 11-04 s23 | always-on 規則 | C | 根因是猜了不存在的 `tab.page`，規則只點名 waitForLoadState、tab.evaluate、舊索引 |
| 10-30 P23 契約測試 gate 摩擦 | 11-04 s15 | hook | C | 改的 PostToolUse 有觸發；這次的攔截來自另一支 preflight gate「第一次碰就攔」 |

合計：C 9（其中 3 筆改動當時也沒載入）、D 4、B 1、A 1。D 全部是 always-on 規則。

### 量測本身的問題
1. 無效觀察被算進復發：obs-24 整筆無效；obs-22、obs-23 的彙總裡也混有生效前事件，或 session 在改動前就開始、context 裡是舊版規則。
2. 驗收器漏掉 outcome：第一組 agent 重算 2026-11-03-full-1 的 cross 階段，輸入 252 筆追蹤觀察，41 個「問題＋改動」組合都保留了，但有 9 個「組合＋outcome」不見，例如一筆 recurrence 被合併成 unreviewed，`validate --stage cross` 仍回 `pass: true`（006a20ba.jsonl:1688、:1691）。信心中等：agent 用的輸入清單是近似挑的，筆數對得上，但沒對到 plan 的正式清單。若成立，庫裡的復發數偏低。
3. cross-session.json 的彙總觀察（obs-22、obs-23、obs-02）不附證據，要拆回單場才看得出有效與否。

### 第 1 題確認：cross 階段把觀察壓成每組一筆（正式輸入清單重算）
- 驗收器只比對 (issue_id, change_id) 組合是否保留，不看 outcome 或筆數：session-retro.py:1188、:1196、:1197（`expected_tracking <= actual_tracking`）。
- 這和 cross 的 prompt 契約一致：「每組配對保留代表觀察即可」（2026-11-03-full-1/workflow-cross.js），所以缺口在契約，不只在驗收器。
- 下游照筆數計數：retro-store.py:761-762 依觀察紀錄筆數算 correct／recurrence。

> 更正行 2026-11-06：撤下「retro-import.py:375-413 直接轉入」；retro-import 只把 cross 的追蹤觀察放進 packet 的 `observation_proposals`，retro-store 的 ingest 不讀這一段，觀察紀錄只能由 main 逐筆執行 `observe` 建立；現況來源：`command grep -c observation_proposals scripts/local-analysis/retro-store.py` 為 0。
- 2026-11-03-full-1：輸入 252 筆／79 個組合＋outcome／41 組合 → 輸出 70 筆，41 組合都在，9 個組合＋outcome 消失。其中可能真遺失的只有 S34 的 1 筆 opportunity_met、P6 的 1 筆 recurrence（證據弱）；P4 recurrence→unreviewed 是有理由的改判，但輸出沒留來源對照。
- 2026-11-01-full-1：輸入 223 → 輸出 35；recurrence 改成 unreviewed 的有 4 組合共 5 筆，但每筆都附 `source_observations` 與改判理由，可追溯。
- 2026-11-04-full-1：輸入 213 → 輸出 39；recurrence 筆數被壓縮，例如 P6 10→1、pipestatus P2 4→1、P13 4→1、P23 2→1；輸出沒有 `source_observations`。
- 意義：累積庫的再犯次數是「每輪每組最多一筆」，不是事件數，明顯偏低。第 1 題修法改為：每次事件都記、驗收逐筆核對、改判必附理由（待使用者確認是否仍屬第 1 題 a）。
- 重算腳本是一次性暫存檔，沒有保留；重算方法是依 session-retro 的 cross 選檔規則取正式輸入報告，以 (issue_id, change_id, outcome) 計數後和 cross 輸出比對。

### hook 副作用

| hook | 生效後觸發 | 抽樣 | 真陽性 | 偽陽性 | 不明 | 重點 |
|---|---|---|---|---|---|---|
| pipestatus（測試 pipe） | 44 次、16 個 session | 10 | 9 | 0 | 1 | 都擋對，但每次被擋平均多 1.55 個 tool call；13 筆連同一指令裡的改檔一起擋掉；14 筆後面用 `;` 接指令，hook 建議的 pipefail 修法無效 |
| contract-test PostToolUse 注入 | 27 次、5 個 session | 10 | 0 | 10 | 0 | 8 筆是 agent 本來就在連續改檔、沒多做事；2 筆是測試在預設語系下壞掉，各多花 212、244 秒。這次改動要處理的「逾時」在 757 次測試裡 0 次 |
| ticket-yagni-runner（無最簡版） | 21 次、6 個 session | 10 | 1 | 3 | 6 | 3 筆被 main 拒絕的砍除建議，原因是審查方看不到 brief，不能確定是這次改動造成 |
| question-tool-conflict | 0 次 | 0 | – | – | – | 生效後只觀察約 11 小時 |

量不到的：always-on 規則的副作用（沒有觸發訊號）、hook 放行時的成本、沒有對照組。

### 撤回與既有量測
- 除了 /retro-workflow 本身，沒有任何程式讀 retro-store 的觀察來撤回改動（`command grep -rl retro-store` 結果）；`effective` 從 commit 5dfcd36 起就寫死成 None。
- /rules-slim 只因大小超標或零使用砍規則，不看改動有沒有效（rules-slim.md:40、:210）。
- session-audit 從 2026-07-05 起逐段標記常駐規則 applied／violated，`state.sqlite` 的 `rule_tags` 有 25,684 筆 violated。生效後 browser-routing 有 31 筆、code-search 有 52 筆 violated。這可以直接拿來量 D 類，不必另外重讀 session。

## 第三輪補充：模型與違反數（2026-11-05～06 本 session 統計）

- **再犯 session 的模型**：15 筆再犯拆回單場共 18 個 session，取每個 session 出現最多次的 assistant 模型：16 個是 gemini-4-argon（含 `google/gemini-4-argon:high`），2 個是 claude-opus-5-5（P7 pgrep 的 B 類、P13 `tab.page` 的 C 類）。4 筆「載入也涵蓋卻沒照做」全是 Argon；其中 54162473 是混用 session，事件行（:1005–1012）的模型是 Argon。
- **分母（估計）**：10-30 之後的主對話，第一個 assistant 模型是 Argon 143 份、Luna 125 份、Opus 80 份，Argon 約占三分之一，不只算回顧範圍內的場次（2026-11-06 架構審查 Opus 席粗估）。
- **session-audit 違反數（檔案層）**：10-31 08:42Z 之後，`rule_tags` 的 violated 依 session 主模型分組：browser-routing 有 Argon 7 場 29 次、Opus 2 場 3 次；code-search 有 Argon 14 場 20 次、Opus 14 場 31 次；同期分析的 session 有 Argon 66 場、Opus 49 場。這是整份規則檔的計數。
- **段落層級重算**（2026-11-06 架構審查 Opus 席，10-30 之後）：code-search 的「Code search」標題下有 5 個不同 rule_id，共 36 場；真正的目標段落（完整枚舉）只有 22 場。browser-routing 檔案層 10 場，目標條目 8 場。
- **載入舊版的例子**（第二組分類）：
  - s06（36089ede）：事件在改動生效後，但 session 在生效前開始，載入的是舊版（`instructions` 附件在 :1688，08:28Z），這個 session 自己寫了新版。
  - s20（0f8be4f5）：最後一次 compact 在 10-30T21:52Z，載入的是舊版。
  - compact 之後 `instructions` 附件會重新注入新版；沒有 compact、只換模型時也會重記一次（9b02764b:1342）。

## 對照本機 /retro-workflow（第一輪：只讀了 grep 命中的行；框架已被「追問更正」段取代）

- 第一輪誤寫成「本機沒有裝 /retro，/retro-workflow 是同類機制」；實際上 /retro-workflow 就是 Matt 的 /retro 加跨場編排，見「追問更正」段。
- 第 19 行已寫：`implemented` 不等於改善有效；`observe` 分記「適用機會的正確執行、新事件、無適用、未查」；沒觀察資料就說證據不足。這已對應論文的 encountered（適用機會）與 recurrence（新事件）。
- 論文另外兩個量測點，grep 沒找到對應字樣（未讀全檔，未驗證是否以其他名稱存在）：
  1. consulted：改後 agent 有沒有真的讀到那條規則
  2. control task：規則在不相關任務上有沒有增加工作量
- 論文的「範圍」觀察（repo 通用資訊會持續有用、特定環境實例清單換題就失效）可當收候選時的分類問題。以上為推論，未在本機資料上驗證。
