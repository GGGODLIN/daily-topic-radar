# 工作單 10 核對報告：用 11-03、11-04 真實資料重跑新流程（2026-11-06）

程式版本：t10 worktree，dispatch base `df1d7ff`（含工作單 02–04）。全程沒有呼叫模型，也沒有改程式。完整指令輸出、副本與輔助腳本放在 `reports/local-analysis/retro-replay/ticket-10/`（已 gitignore），下文稱「replay 目錄」。

## 結論

- 正式累積庫沒有被動到：執行前後都是 3216 行，大小 5392255 bytes，修改時間都停在 11-05 12:05:44。
- 兩輪的 cross 驗收都判不通過，原因是舊格式：11-03 有 368 筆、11-04 有 417 筆輸入觀察沒有逐筆去向。逐筆對照後，11-03 有 290 筆、11-04 有 373 筆在 cross 輸出裡完全找不到。
- 走真正的新流程（retro-import 加 ingest），兩輪都整批被拒，副本累積庫寫入 0 筆。原因有兩層：舊 cross 輸出沒有 `events[]`；另外 retro-import 讀的是模型原稿 `reports/cross-session.json`，不是驗收器補過 `events[]` 的 `validated/` 版本（見 F2）。
- 11-04「搜尋輸出過大」（P6）的 10 筆再犯來源觀察共引用 36 行證據。程式拆出 29 個事件，人工拆出 22 個，兩者不一致。多出的 7 個全部是「補救或追查」動作：TaskStop 3 行、縮小範圍重查 3 行、讀取過大輸出檔 1 行。程式一個工具呼叫算一個事件，所以這些動作各自成為一個事件。
- 用真實資料找到兩個會讓 ingest 整批拒收的程式缺陷：同一事件引用不同行時，事件時間不同（F1）；同一行用不同引文引用時也會被拒（F5）。另外驗收器在找不到寫檔紀錄時，會沿用舊的通過結果，新檢查完全沒有跑（F3）。
- 舊格式觀察在摘要裡另外列出、不算進再犯次數，這點符合規格：副本摘要 `legacy_observations: 212`，P6 的 `legacy: 7`（其中 4 筆是舊的 recurrence），而 `recurrence: 0`。

## 正式累積庫行數

| 時間（UTC） | `wc -l records.jsonl` | 大小與修改時間 |
|---|---|---|
| 2026-11-05T19:21:23Z（開始前） | 3216 | 5392255 bytes，Nov 5 12:05:44 |
| 2026-11-05T19:37:53Z（結束時） | 3216 | 5392255 bytes，Nov 5 12:05:44 |

另外用 `find <dir> -newer logs/00-official-store-before.txt -type f | wc -l` 檢查正式累積庫與兩個原 run 目錄，結果都是 0，表示沒有任何檔案比開始時間新。所有 ingest 都用 `--store` 指到 replay 目錄裡的副本。retro-store 的預設 `--store` 會指到正式庫（retro-store.py:22、:1204），所以每一條指令都明確帶了這個參數。

## 做法

1. 複製：把兩個 run 目錄、正式累積庫，以及原本驗收時用過的全部 workflow transcript 目錄，都複製進 replay 目錄。transcript 目錄從原主對話的 validate 指令裡取得：11-03 的主對話是 `006a20ba…`，共 9 個 `wf_*` 目錄；11-04 的主對話是 `3a82d019…`，共 8 個。
2. 路徑改寫：validate 要求 plan 的來源在 run 目錄內，而且寫檔紀錄要落在同一個 run 根目錄（session-retro.py `binding`、`report_writer`）。所以副本內所有檔案（plan、報告、單場文件、transcript）裡出現的 `reports/local-analysis/retro-workflow/<run>` 都改成 `retro-replay/ticket-10/<run>`。各檔的替換次數記在 `logs/01-path-rewrite.txt`。原始 session 檔（plan 的 `sessions[].source`）在 run 目錄外，沒有改，只讀。
3. 依工作單 03 的順序重跑驗收（指令在 replay 目錄的 `scripts/run_validate.sh`）：

```
session-retro.py validate --plan <副本>/plan.json --stage reading       --transcript-dir <該輪全部 reading wf 目錄>
session-retro.py validate --plan <副本>/plan.json --stage session-merge --transcript-dir <該輪全部 session-merge wf 目錄>
session-retro.py validate --plan <副本>/plan.json --stage cross         --transcript-dir <該輪全部 cross wf 目錄>
retro-import.py --run <副本 run> --out <replay>/packet-<run>.json
retro-store.py --store <replay>/store-copy ingest --packet <replay>/packet-<run>.json
```

4. 逐筆核對：用 session-retro 自己的 `accounting_inputs`／`add_event_evidence`／`output_outcomes`（`scripts/account.py`），把舊 cross 輸出的每筆證據也換算成事件鍵，再和輸入逐筆比對。
5. 新流程整批被拒，所以另做診斷 packet（`scripts/diag_packet.py`），只帶觀察、用獨立的 run id，寫進副本：
   - 診斷 A：舊 cross 輸出的觀察，加上驗收器算出的 `events[]`。這相當於 retro-import 改讀 `validated/` 版本時會得到的 packet。
   - 診斷 P6：11-04 的 10 筆 P6 再犯來源觀察，假設 cross 全部保留。
   - 先用 retro-store 自己的單筆檢查剔除會讓整批被拒的觀察（`scripts/precheck.py`），剔除了哪些逐筆記在 log。
   - 之所以不重播原 run 的 `accumulation-packet.json`：它和庫裡已有的 run／discovery 紀錄內容不同（`conflicting entry id: run:2026-11-04-full-1`、`discovery:2026-11-03-full-1:P1`）。庫裡那份應該來自之後改過的 packet，這一點未追查。
6. 假設修正（what-if）：F1、F5 會擋下整批 packet。為了看擋下之後的計數，另做一份假設修正的 packet（`scripts/whatif.py`）：同一事件鍵的事件時間取最早那一行，同一行引用兩種引文時只留第一個。這份只寫進另外兩份副本 `store-whatif`、`store-whatif-p6`，不是程式的實際輸出。

## 驗收 1：重跑 cross 驗收與匯入，寫進副本；正式庫行數不變

| 輪次 | reading | session-merge | cross | retro-import | ingest（新流程） |
|---|---|---|---|---|---|
| 11-03 | pass 75/75 | pass 12/12 | **fail**：`legacy cross report format: tracking_dispositions missing, so 368 input observations have no per-observation destination` | ok：33 discoveries、33 issues | **整批拒收**：`observation_proposals[0].events must match evidence one to one`，寫入 0 |
| 11-04 | pass 93/93 | pass 14/14 | **fail**：同上，417 筆 | ok：48 discoveries、48 issues | **整批拒收**：同上，寫入 0 |

三個階段的驗收都沒有沿用舊結果：每個通過的 unit，agent 路徑都在副本的 transcript 目錄內。

診斷 packet 寫進副本的結果：

| packet | 副本 | 先剔除 | ingest 結果 | 副本行數 |
|---|---|---|---|---|
| 診斷 A 11-04 | store-copy | 6 筆 change_id 為 null | 新增 39 行：38 筆觀察加 1 筆 run；new 38、supplemented 0 | 3216 → 3255 |
| 同一份重播 | store-copy | — | 新增 0；replayed 38，noop 39 | 3255（符合「重播新增 0」） |
| 診斷 A 11-03 | — | 16 筆沒有 evidence | **整批拒收**：`conflicting source quote: …ticket-yagni-runner-no-minimal-spec-contract`（F5）；先檢查還發現 10 個事件鍵有 F1 衝突 | — |
| 診斷 A 11-03（what-if） | store-whatif | 同上，另丟 7 行同點不同引文 | new 77、supplemented 10、replayed 1，新增 88 行 | 3255 → 3343 |
| 診斷 P6（未修正） | — | 0 | **整批拒收**：`conflicting observation in one run: call_721724…\|code-search-exclude-…`（F1） | — |
| 診斷 P6（what-if） | store-whatif-p6 | 0 | new 29、supplemented 7，新增 37 行；重播後 new 0、replayed 36 | 3216 → 3253 |

結論：「寫進副本」只有診斷 A 11-04 是用程式原樣輸出成功寫入的；兩輪的新流程本身都沒有寫進任何一筆。另外，所有寫進副本的觀察，載入判斷都是 `unchecked`，原因見 F8。

## 驗收 2：11-04 P6 的 10 筆來源觀察，人工拆分 vs 程式

人工規則：問題動作（沒排除的搜尋、跑掉的指令、印出過大輸出的指令）算一個事件。它的結果行、停掉它的 TaskStop、縮小範圍重查、讀它存下的過大檔案，都算這個事件的補充證據。只有補救動作本身又犯同樣的錯（例如又印出過大輸出），才另算一個事件。人工判斷依據是逐行讀原始 session 的工具呼叫與結果，完整內容在 replay 目錄的 `logs/p6-raw-lines.txt`。

程式規則：該行恰好有一個工具呼叫時，事件鍵就是 call ID（session-retro.py `locate_event`），所以呼叫行和它的結果行會歸成同一個事件。

| # | 來源 unit | session:行（證據） | 人工事件 | 程式事件鍵（call ID 尾碼） | 一致？ |
|---|---|---|---|---|---|
| 1 | s02-u01 | 01ac4545:178、:180 | E1（grep 兩個大根目錄） | `…c005gew` ×2 | 是（1／1） |
| 2 | 0ecef65f-session | 0ecef65f:258 | E1（:257 python 掃整個 projects 目錄，逾時轉背景 `bic06r6v7`） | `…dRU7tK3r` | |
| | | :269 | E1（TaskStop 停 `bic06r6v7`） | `…T3YFgM`（另一事件） | **否** |
| | | :430 | E2（:419 另一支搜尋逾時，`bu8wjr0iz`；證據只引用了停止行） | `…akiTyRa3`（TaskStop 的 ID） | 數量一致，但鍵不是原搜尋的 ID |
| | | :447 | E3（:435 另一支搜尋逾時，`bywalxo3f`；同上） | `…3xB3qeFoiD`（TaskStop 的 ID） | 同上 |
| | | :2123 | E4（grep 7.3MB） | `…Zik7UrXbk` | 是 |
| | | :2757 | E5（grep 51.4KB） | `…uur5w1GVAYr` | 是（合計 人工 5／程式 6） |
| 3 | 282ce58a-session | :843、:844 | E1（grep trials／memory，155.7KB） | `call_727963` ×2 | 是 |
| | | :1645、:1646 | E2（grep 323KB） | `…ugV8XE_TqW9O` ×2 | 是 |
| | | :1682 | E3（ls 加 grep，868.8KB） | `…bevDQvhEg-` | 是 |
| | | :1691 | E3（Read 那份 868.8KB 存檔，超過 token 上限） | `…ACEYwdfoBv`（另一事件） | **否**（人工 3／程式 4） |
| 4 | s13-u01 | 5b72d19d:66、:67 | E1（107.7KB） | `…mQaxN3JEoZFS6S` ×2 | 是 |
| | | :90 | E2（遞迴 glob，111.2KB） | `…yryPpUbur8` | 是 |
| | | :100 | E2（縮小範圍重查，輸出正常） | `…DCN9ReUz3a`（另一事件） | **否**（人工 2／程式 3） |
| 5 | s14-u01 | 654b5ea5:251 | E1（grep 350.6KB） | `call_1141794` | 是 |
| | | :263 | E1（縮小範圍重查，輸出正常） | `…a03iExeNA`（另一事件） | **否**（人工 1／程式 2） |
| 6 | 6bf0bd37-session | :93、:101 | E1（grep 846.4KB） | `call_30337` ×2 | 是 |
| | | :690、:691 | E2（另一次 grep，exit 1） | `…hiIUmKMrg` ×2 | 是（2／2） |
| 7 | s16-u01 | 71b25c30:224、:225 | E1（grep 551.1KB） | `call_1093563` ×2 | 是（1／1） |
| 8 | d3e41a3e-session | :1221 | E1（grep 110.9KB） | `…H3WflfB64f` | 是 |
| | | :1231 | E1（縮小到單檔重查，輸出正常） | `…uic2Y8KQ`（另一事件） | **否** |
| | | :2527 | E2（grep 逾時轉背景 `bfwdberjh`） | `call_277035` | 是 |
| | | :2538 | E2（TaskStop 停 `bfwdberjh`） | `…eBCi4qidNq`（另一事件） | **否** |
| | | :4332 | E3（lizard／jscpd 30.9KB） | `…165-gVwhZ` | 是 |
| | | :5320 | E4（gh api 遞迴列樹） | `…0O6ozs8ap` | 是（人工 4／程式 6） |
| 9 | s29-u01 | fbf0566d:38 | E1（搜尋逾時轉背景 `bjmptp1yh`） | `…b9kBkEa5` | 是 |
| | | :50 | E1（TaskStop 停 `bjmptp1yh`） | `…tyiTE_9b7`（另一事件） | **否**（人工 1／程式 2） |
| 10 | s39-u01 | 2c858be6:526 | E1（grep 181.2KB） | `…CH-Bb-Ie8` | 是 |
| | | :535 | E2（過濾 E1 存檔，自己又印出 87.6KB） | `…TGgDRp0p` | 是（2／2） |
| **合計** | 10 筆觀察 | 36 行證據 | **22 個事件** | **29 個事件鍵** | 不一致 7 行 |

不一致的原因：

- 7 行都是補救或追查動作，各自有自己的 call ID：TaskStop 3 行（0ecef65f:269、d3e41a3e:2538、fbf0566d:50）、縮小範圍重查 3 行（5b72d19d:100、654b5ea5:263、d3e41a3e:1231）、讀取過大存檔 1 行（282ce58a:1691）。規格對事件的定義是「一次具體動作，識別鍵優先用工具 call ID」，程式照字面執行。問題在於這些動作本身沒有違反規則，卻在累積庫裡各自成為一筆 recurrence 觀察，所以 11-04 的 P6 會算成 29 次，人工判斷是 22 次。
- 0ecef65f:430、:447 的數量一致，但事件鍵錯了。證據只引用 TaskStop 那一行，所以事件鍵是 TaskStop 的 call ID，不是原搜尋（:419、:435）的 call ID。如果另一份報告引用的是同一搜尋的逾時行（:426、:443），就會得到另一把鍵，同一事件會被算成兩次。
- 拆分時另外發現：d3e41a3e 和 f23c3eb1 兩個 session 檔共有 380 個相同的 call ID，看起來是同一段對話分叉出來的兩個檔。程式用 call ID 當鍵，所以兩檔裡同一個事件會正確合成一個（例如 `…H3WflfB64f`），這點和人工判斷一致。但兩份單場報告對它的結論不同：f23c3eb1 是 not_applicable，d3e41a3e 是 recurrence，見驗收 3 與 F7。
- 程式層面，假設修正（what-if）把這 10 筆寫進副本，得到 new 29、supplemented 7，和事件鍵數一致。沒做 what-if 時，F1 會讓整批被拒。
- 補充說明，以下不影響拆分比對：有幾個事件是不是真的違反 P6，我沒有逐一判斷，例如 6bf0bd37:690（對兩個點名的目錄 grep，沒有命中）、d3e41a3e:4332（lizard／jscpd 報表）、d3e41a3e:5320（gh api）。研究筆記原本寫的 11-04 P6「10→1」指的是 cross 把 10 筆來源壓成 1 筆。

## 驗收 3：兩輪 cross 輸出逐筆核對

兩輪都沒有通過，因為舊 cross 輸出沒有 `tracking_dispositions`，所以每一筆輸入觀察都缺去向。下表把每筆輸入觀察（事件 × 改動）拿去對 cross 輸出（輸出的證據也換算成事件鍵後比對），完整清單在 replay 目錄的 `logs/<run>-accounting.json`：

| 項目 | 11-03 | 11-04 |
|---|---|---|
| 輸入報告數 | 25 | 40 |
| 輸入追蹤觀察（筆） | 252 | 213 |
| 其中沒有 evidence（舊的逐對核對） | 37 | 0 |
| 有 evidence 但算不出事件鍵（evidence 是自由文字字串） | 4（都在 f23c3eb1-session） | 2（都在 b5c7dcaa-session） |
| 輸入觀察鍵（事件 × 改動） | 368 | 417 |
| cross 輸出觀察（筆）／鍵 | 70／78 | 39／44 |
| 缺去向（沒有 `tracking_dispositions`） | 368（全部） | 417（全部） |
| 在輸出完全找不到 | 290 | 373 |
| 在輸出、結果相同（隱含保留或合併，但沒有紀錄） | 77 | 44 |
| 在輸出、結果不同，卻沒有改判理由 | 1：e5c57a0f:1432，check-my-stack-snapshot-headers，recurrence → unreviewed | 0 |
| 多份輸入回報同一事件（新契約要求 merged） | 0 | 3，都來自 d3e41a3e／f23c3eb1 分叉檔；其中 `…H3WflfB64f` 的 P6 結果衝突（not_applicable 與 recurrence），三筆都不在輸出 |
| 輸出有、輸入沒有的鍵 | 0 | 0 |

按改動分組的缺漏（只列再犯相關的大宗；所有改動的分組在 log）：

- 11-04 code-search（P6）：58 個輸入鍵中 56 個不在輸出，其中 recurrence 有 29 個，輸出只留 2 個鍵（71b25c30:225、fbf0566d:38）。
- 11-04 browser-routing（P13）：43 個鍵中 41 個不在輸出，其中 recurrence 13 個。
- 11-04 pipestatus-gate（P2）：43 個鍵中 41 個不在輸出，其中 recurrence 5 個。
- 11-04 contract-test-posttooluse（P23）：23 個鍵中 21 個不在輸出，其中 recurrence 8 個。
- 11-03 browser-routing：40 個鍵中 33 個不在輸出，其中 recurrence 3 個。
- 11-03 code-search：35 個鍵中 29 個不在輸出，其中 recurrence 5 個。

這和研究筆記「第 1 題確認」的計數對得上：11-03 輸入 252 筆、輸出 70 筆；11-04 輸入 213 筆、輸出 39 筆；11-03 的 P4 有一筆 recurrence 被改成 unreviewed，沒有來源對照。

## 驗收 4：舊格式觀察另外列出、不算再犯次數

- 副本 `list --summary`：`counts.legacy_observations = 212`，而正式庫的 212 筆 observation 全部沒有 `event_time`（`python3` 讀 records.jsonl 計數）。
- P6 在 store-copy 的摘要是 `{"legacy": 7, "recurrence": 0, "total": 2, "unchecked": 2}`。7 筆舊觀察裡有 4 筆 recurrence，都沒有算進 `recurrence`。store-whatif-p6 是 `{"legacy": 7, "recurrence": 0, "total": 29, "unchecked": 29}`。
- 程式依據：retro-store.py 的 `project_issue` 只把有 `event_time` 的紀錄算進次數，其餘歸到 `legacy`。

## Minor findings

每項都附重現方式。路徑都在 replay 目錄內，指令從 `/Users/linhancheng/code/social-info-wt/t10/scripts/local-analysis` 執行。

- **F1（嚴重度高，會擋整批）同一事件的事件時間取自被引用的那一行，不是事件本身。** 同一個 call 的呼叫行和結果行事件鍵相同，事件時間卻不同（session-retro.py:1555）。ingest 把事件時間放進 `observation_core` 比對（retro-store.py:696），所以同一輪會報 `conflicting observation in one run`（:772），跨輪則報 `a changed observation needs a reclassification reason`。真實資料裡，P6 有 7 個事件鍵、11-03 有 10 個事件鍵會撞到這個問題。重現：`retro-store.py --store <新副本> ingest --packet diag-packet-P6-2026-11-04-full-1-filtered.json`，輸出 `{"ok": false, "error": "conflicting observation in one run: call_721724__tsk16__I3XimsO9_c005gew|code-search-exclude-and-runaway-hot-3m-2026-10-31"}`。規格寫的是「同一事件可以追加證據」。
- **F2 retro-import 讀模型原稿，不讀驗收後的版本。** retro-import 從 `reports/cross-session.json` 取觀察（retro-import.py:19、:293）。`events[]` 只由 validate 寫進 `validated/cross-session.json`（session-retro.py:1668、:1681），原稿不會有。retro-import.test.py 的 fixture 直接把 `events` 放進原稿，所以測試沒有抓到。照現在的接線，新格式 cross 產生的 packet 也會被 ingest 以 `events must match evidence one to one` 拒收。這一條是讀程式碼加上舊資料實跑推得的，沒有用新格式 cross 實跑，因為那一步需要重跑模型，這張單沒做。
- **F3 validate 找不到寫檔紀錄時，會靜默沿用舊的通過結果。** session-retro.py:1699-1702 在 `no successful report` 而且舊結果通過時直接用舊 unit，所以新加的 `events[]` 與逐筆去向檢查都沒有跑，stdout 卻顯示 `pass: true`、errors 為空。重現：第一次嘗試只給一個 transcript 目錄，11-04 有 18 個 reading unit 加上 cross unit 被沿用，cross 顯示通過；證據在 `logs/attempt1-single-transcript-dir/`。主對話如果漏給補跑時用過的 transcript 目錄，也會發生同樣的事。
- **F4 ingest 碰到沒有 evidence 或 change_id 為 null 的觀察，會整批拒收。** retro-store.py:550 等處要求每筆都有。真實舊 cross：11-03 有 16 筆 not_applicable／unreviewed 沒有 evidence，11-04 有 6 筆 change_id 為 null（S84–S87、S096、S19）。新契約的 cross 會不會再產生這類觀察未知；如果會，整輪就進不了庫。重現：`logs/<run>-diagA-precheck.txt`。
- **F5 同一行用不同引文引用，會整批拒收。** retro-store.py:752 要求同一個點的引文完全相同。11-03 有 5 個事件鍵是同一行被不同觀察挑了不同片段（例如 32ca2166:1319 被引用了 4 種引文）。重現：`ingest --packet diag-packet-A-2026-11-03-full-1-filtered.json`，輸出 `conflicting source quote: call_4b095166a816439d9cc25e1273fc4178|chg:2026-11-01:ticket-yagni-runner-no-minimal-spec-contract`。
- **F6 補救與追查動作被算成獨立事件，TaskStop 的鍵也不是原動作的鍵。** 見驗收 2：P6 程式 29 個、人工 22 個。這是設計層面的取捨，不是程式錯誤，要不要處理交給使用者。
- **F7 分叉 session 檔會讓同一事件出現在兩份單場報告，結論還可能不同。** d3e41a3e／f23c3eb1 共有 380 個相同的 call ID。程式的事件鍵能把它們合在一起，但新契約的 cross 必須把它當成 merged，或附理由改判；ingest 遇到同輪結論不同會拒收。
- **F8 tracking-context 缺改動定義，所有載入判斷都是「未查」。** 兩輪的 `tracking-context.json` 只有 `change_id`、`effective_at`，沒有 `kind`、`effective_time`、`new_text`、`name`，所以每個事件都是 `unchecked: change definition (kind, effective_time) not found`，存進庫的結果也是 `unchecked`，recurrence 計為 0。這和工作單 02 的 Minor 一致，這次在真實資料上確認了：P6 29 筆全部是 unchecked。
- **F9 change_id 為 null 時，事件鍵變成字串 `…|None`。** session-retro.py:1533 對 `str(None)` 產生鍵。11-04 有 7 個這種鍵。

## 沒做的部分

- 新格式的 cross 輸出（含逐筆去向）沒有產生，因為只能重跑模型才拿得到，依工作單規定不跑。所以「新流程完整跑通並寫進副本」沒有達成。寫進副本的只有診斷 packet。
- what-if 的計數（store-whatif、store-whatif-p6）建立在假設 F1、F5 已修好的前提上，不是程式現在的行為。
- P6 以外的再犯觀察沒有做人工拆分。P6 各事件是否真的違反規則，也沒有逐一判斷。
- 原 run 目錄的 `accumulation-packet.json` 為什麼和庫裡的 run／discovery 紀錄不同，沒有追查。
- 路徑改寫可能影響 validate 的結果：副本內所有檔案都一致替換了同一個字串，三個階段驗收都通過，而且沒有沿用舊結果，所以判斷沒有影響。但沒有拿未改寫的原 run 用新程式另跑一次對照，因為那樣會寫進原 run 目錄。
