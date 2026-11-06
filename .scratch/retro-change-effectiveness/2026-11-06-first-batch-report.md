# 工作單 11 操作報告：第一批四項送進待決定清單（2026-11-06）

程式版本：social-info `8d37931`（t11 的 dispatch base，也是主 checkout 的 HEAD）。過程中沒有呼叫任何模型，沒有改程式，沒有改 `~/.claude` 的任何檔案，也沒有寫提議決策紀錄。完整的指令輸出與中間檔放在 `reports/local-analysis/retro-replay/ticket-11/`（已 gitignore），下文簡稱「replay 目錄」。

## 結論

- 正式累積庫從 3216 行變成 3222 行，新增 6 筆：4 筆補登、2 筆判定。前 3216 行與備份 `records.jsonl.bak-2026-11-06-before-ticket-11` 逐位元組相同（`head -n 3216 | cmp`）。
- 兩條規則改用段落層級查詢重算，違反場數都超過 3 場：瀏覽器規則 7 場、搜尋規則 19 場。兩條都已經出現在 `pending_decisions.make_hook`。
- pipestatus 的 test-pipe 擋法：改動生效後共擋下 47 次，其中 46 次找得到被擋的指令，46 次全部擋對。但有 16 次把同一條指令裡的改檔或寫檔一起擋掉，所以判「幫倒忙」。這一項已經出現在 `pending_decisions.harmful`，等使用者決定撤回或修改。
- 契約測試的 PostToolUse 提醒判「證據不足」，**沒有進待決定清單**，所以驗收第 4 條只完成 3/4 項。依第四層的規則逐筆列出 10 個事件：9 個擋不住，1 個可能擋得住（eb7250c1）。只要有 1 個不是確定擋不住，規格就不允許判「無效」。抽樣也沒有看到它擋掉任何動作，因為它不會擋。這和研究時「抽 10 筆全是多餘」的方向不同，原因見第 3.2 節。
- 系統沒有建立任何 hook，也沒有撤回任何東西：本票只用了 `backfill`、`verdict`、`list` 三個指令，沒有執行 `proposal`。

## 累積庫新增的紀錄

| 行 | id | 種類 |
|---|---|---|
| 3217 | `backfill:chg:2026-11-01:browser-routing-lcu-playwright-and-stale-uid:1` | 補登，`always_on_rule` |
| 3218 | `backfill:code-search-exclude-and-runaway-hot-3m-2026-10-31:1` | 補登，`always_on_rule` |
| 3219 | `backfill:chg:2026-11-01:contract-test-posttooluse-additional-context:1` | 補登，`hook` |
| 3220 | `backfill:chg:2026-11-02:pipestatus-gate-unprotected-test-pipe:1` | 補登，`hook` |
| 3221 | `verdict:chg:2026-11-02:pipestatus-gate-unprotected-test-pipe:1` | 判定 `harmful`，第 4 層，副作用已檢查 |
| 3222 | `verdict:chg:2026-11-01:contract-test-posttooluse-additional-context:1` | 判定 `insufficient`，第 4 層，副作用已檢查 |

行數：執行前 3216，執行後 3222（replay 目錄 `records-lines-before.txt`、`records-lines-after.txt`）。每個寫入指令都先不帶 `--apply` 跑一次，dry-run 回 `appended: 1` 而且行數不變，確認後才帶 `--apply` 執行（`backfill-dry-run.txt`、`backfill-apply.txt`、`verdict-dry-run.txt`、`verdict-apply.txt`）。

## 1. 補登

| 改動 | 類型 | 錨點（`~/.claude` 現行檔） | 備註 |
|---|---|---|---|
| `chg:2026-11-01:browser-routing-lcu-playwright-and-stale-uid` | always_on_rule | `rules/common/browser-routing.md:4`，整個清單項 | 這次改動在第 4 項末尾加了括號子句（`~/.claude` commit 56be06bb），錨點取整個清單項，因為 session-audit 以清單項為一條規則 |
| `code-search-exclude-and-runaway-hot-3m-2026-10-31` | always_on_rule | `rules/common/code-search.md:13`，「完整枚舉」整段 | 原改動紀錄的 source 指向另一個專案的 runaway.sh。依工作單只補規則段落；runaway.sh 這個 script 項目沒有補登 |
| `chg:2026-11-01:contract-test-posttooluse-additional-context` | hook | `hooks/contract-test-receipt-gate.sh:73`，PostToolUse 提示句 | 和原改動紀錄同一行 |
| `chg:2026-11-02:pipestatus-gate-unprotected-test-pipe` | hook | `hooks/pipestatus-zsh-gate.py:25`，`TEST_PIPE_SWALLOW = re.compile(` | 原改動紀錄寫第 24 行。`~/.claude` 工作區這支檔案有未提交的修改（`git diff --stat`：+37/−1，看起來是 11-05 的尾接遮蔽擋法），這一行因此往下移了一行 |

兩條規則的錨點原文用的是整個段落，和 session-audit 解析出的規則全文完全相等，所以 `resolve_rule` 走的是全文比對，不是子字串比對。補登後 `retro-store rule-anchors` 列出這兩個錨點。

**「pipestatus 連帶擋掉改檔」對應哪一筆改動**：對應 `chg:2026-11-02:pipestatus-gate-unprotected-test-pipe`。依據有三點：

1. research.md 的 hook 表把這一列標成「pipestatus（測試 pipe）」，並寫「hook 建議的 pipefail 修法」。這和該改動的 condition「提示加上 set -o pipefail」一致，也和 audit 的 reason `unprotected-test-pipe-tail-head` 一致（這個 reason 由 `~/.claude` commit 801a19c3 加入）。
2. 研究時寫的「44 次、16 個 session」，正好等於這個 reason 在生效後累計到第 44 筆時的數字：第 44 筆在 `pipestatus-zsh-gate-audit.jsonl:281`（11-05T08:54:30Z），到這筆為止剛好 16 個 session。
3. 另外兩筆改動的 reason 數字對不上。python-c 生效後有 67 筆、38 場；尾接遮蔽有 5 筆、3 場（`pipestatus-reason-mapping.txt`）。

## 2. 兩條規則的段落層級違反場數

查詢指令（只讀；`--state` 給 session-audit 目錄）：

```
session-audit.py --state reports/local-analysis/session-audit --rules-repo ~/.claude \
  --rule-path <規則檔> --rule-quote "<段落全文>" --since <改動 effective_at> rule-violations
```

| 規則 | 起算（effective_at） | 違反 session | 違反次數 | 覆蓋量 | 時間未解析 |
|---|---|---|---|---|---|
| browser-routing 第 4 項 | 2026-11-01T02:17:46Z | **7** | 26 | 959 場，最近分析到 11-06T02:43:44Z | 0 |
| code-search「完整枚舉」 | 2026-10-31T08:42:00Z | **19** | 29 | 1080 場，最近分析到 11-06T02:43:44Z | 0 |

兩條都達到 3 場門檻。`list --summary` 產生待決定項時會再查一次，那時的覆蓋量是 960 場和 1081 場，因為 session-audit 一直在背景分析新 session，違反場數沒有變。

### 原文指針

違反標記來自 session-audit 的免費模型，準確度沒有驗證過（規格記載同類抽查 6/10 正確）。下面的指針是我從引文挑出、看起來對準這次改動新增子句的幾筆。我只讀了 session-audit 存的引文，**沒有逐筆打開原始 session 驗證**。

搜尋規則（新增的子句：對大根目錄搜尋要排除目錄；逾時轉背景時要 TaskStop）：

- `14141fc5-a5d2-4263-b8b6-5772847c2bb0:570`：對 `~/.claude/` 做 `command grep -rn`，沒有排除目錄
- `71b25c30-8bad-4c02-95e2-d492ab707e1a:224`：對 `~/.claude/` 與 `~/Desktop/projects/` 遞迴搜尋，沒有排除目錄
- `6bf0bd37-5247-4af3-962c-bb5604151a19:93`、`:582`：同上，兩個根目錄都沒有排除
- `3a82d019-8b94-4700-9a2c-907ff9d35bd4:1237`：對 `~/.claude` 根目錄遞迴搜尋，沒有排除
- `b16272cd-783d-4605-962b-549774546321:132`：助理自己寫明搜尋逾時轉到背景，而它沒有終止那個背景任務（TaskStop 子句）

依引文判斷，19 場中有 8 場對準新子句：14141fc5、76b00b58、e5ac1e0e、71b25c30、6bf0bd37、3a82d019、dffa1a56、b16272cd。另有 4833d4fe 一場有排除目錄，但清單少了規則列的幾項，算邊緣案例。其餘 10 場是對單一檔案用裸 `grep`，或用 `rg`、Read，屬於同段的「不要用裸 grep」子句，或者是標記誤判。這 8 場的主模型有 7 場是 gemini-4-argon。

瀏覽器規則（新增的子句：Playwright API；狀態變更後沿用舊數字索引）：

- `d50d5752-8279-4f83-8ced-b900c36dc94d:614`：同一次呼叫裡連續填 4 個欄位、再連點 2 個索引，中間沒有重取狀態
- `f23c3eb1-31d5-4044-92c3-05cecd0f395d:2670`：填值、點擊、打字、按鍵後再點另一個索引，中間沒有重取狀態
- `9b02764b-c3ed-4a93-8363-f36e144bfb47:2111`：連點 3 個索引，中間只用 sleep 等待
- `dffa1a56-a8d2-4e2a-bce0-0828fbe077d3:128`：呼叫 `tab.page()`，和研究時 s23 C 類的 `tab.page` 是同一種盲猜 API

這 7 場裡，有 5 場看起來是「狀態變更後連續用索引」：54162473、f23c3eb1、d50d5752、9b02764b、dcb8947e。但這些索引當時是不是真的過期，要看當時的 AX 狀態才能確定，我沒有驗證。cf2708d0、dcb8947e 用了 `tab.playwright.evaluate`，而規則寫的是 `tab.evaluate`；`tab.playwright` 底下支不支援 evaluate 我沒有查證，這兩筆可能是標記誤判。f23c3eb1:471 讀 localStorage，屬於同段的隱私子句。主模型分布是 Argon 4 場、Opus 3 場（`violating-session-models.txt`）。

### 與研究時數字的差異

| 項目 | 研究時 | 這次 | 差異原因 |
|---|---|---|---|
| browser 檔案層（10-31 08:42 後） | Argon 7＋Opus 2＝9 場，29＋3＝32 次 | 9 場、28 次 | 場數相同。次數差 4 次沒能逐筆對帳：不過濾 pending 來源時是 38 次，研究時的 32 次只算 Argon 與 Opus 兩種主模型 |
| browser 段落層（10-30 後） | 目標條目 8 場（檔案層 10 場） | 7 場、26 次（起算改成 11-01T02:17:46Z，結果相同） | d3e41a3e 的 10 筆標記所屬來源目前 `status=pending`，正式查詢只算 complete/partial。不過濾時是 8 場與 10 場，和研究時相同（`rule-count-decomposition.txt`） |
| code-search 檔案層（10-31 08:42 後） | Argon 14＋Opus 14＝28 場，20＋31＝51 次 | 29 場、53 次 | 同段 5 條規則合計。資料庫在研究後新增了分析結果；另外排除了 pending 來源 |
| code-search 段落層（10-30 後） | 標題下 5 個 rule_id 共 36 場，目標段落 22 場 | 目標段落 19 場（10-30 起算與 10-31 08:42 起算結果相同）；整個標題 32 場 | 正式查詢排除了 2 個 pending 來源（d3e41a3e、ff3e1352），不過濾時是 21 場與 34 場。剩下和研究時差的 1 場與 2 場，我沒能重現，可能是資料庫在研究後重新分析過，沒有查證 |

這次的數字比研究時的檔案層數字小，原因有四個：

1. **段落取代整個檔案**：搜尋規則從 29 場降到 19 場；瀏覽器規則從 9 場降到 7 場，其中 2 場違反的是第 3 項的路由條。
2. **時間過濾改用事件時間**：查詢以違反那一行的時間為準，不再用 session 開始時間。這次兩條規則都沒有因此少算或多算，`unresolved_time` 為 0。
3. **只算已完成分析的來源**：status 為 pending 的來源不算，這是研究時那位審查者沒有過濾的條件。
4. **規則身分綁原文**：段落一改寫，查詢就對不回改寫前的舊版。兩條規則在改動生效後都沒有再被改過（`git log` 只有 3f20439f、56be06bb 兩筆，都是改動本身），所以這個因素不影響這次的結果。

session-audit 資料庫仍在背景寫入（查詢時 WAL 的修改時間是 10:55）。pending 的 session 分析完之後，場數可能會再變。

## 3. 兩支 hook 的抽樣與判定

### 3.1 pipestatus test-pipe 擋法（`chg:2026-11-02:pipestatus-gate-unprotected-test-pipe`）

觸發紀錄：`~/.claude/logs/pipestatus-zsh-gate-audit.jsonl` 中 reason 為 `unprotected-test-pipe-tail-head`、時間在 effective_at（11-02T02:58:14Z）之後的列，共 47 筆、18 個 session（第 196～293 行）。audit 只記時間與 session，所以每一筆都要回到 transcript（含 subagent）找出被擋的那個 Bash 呼叫：條件是 tool_result 含有 deny 原文，且時間相差 10 秒內，取最近、而且還沒被其他列對應走的那一筆。47 筆中對回 46 筆，第 289 行（0a015c50，11-05T19:31:36Z）找不到對應 transcript。

**抽樣**：按 audit 行號每 4 筆取 1 筆，共 12 筆，其中 1 筆找不到原文。我另外也把 46 筆全部跑過一遍，用 hook 自己的正規式確認命中位置不在 heredoc 本文裡（`pipestatus-match-location.txt`）。

| audit 行 | session（位置:行） | 模型 | 擋對？ | 連帶擋掉改檔？ | 之後多花 |
|---|---|---|---|---|---|
| 196 | 7a3e30bb main:27042 | Opus | 是（jest 接 tail） | 否 | 1 次重送，+4.5 秒 |
| 200 | 43131f7d subagent agent-a87ffbfe81fd5f271:36 | Sonnet | 是 | 否 | 1 次，+2.6 秒 |
| 204 | 43131f7d subagent agent-a068b4a850f05b4d3:34 | Sonnet | 是 | **是**：同一條指令先用 Python 改 `switch_mode_watchdog.py`、再用 heredoc 寫測試檔，最後才跑測試 | 3 次（查 git status、改用 Edit、改用 Write），+42.5 秒才補完 |
| 208 | c4354a04 main:1248 | Opus | 是 | 否 | 1 次，+2.5 秒 |
| 215 | 43131f7d subagent agent-a67bf0aefed5ef546:83 | Opus | 是 | 否 | 1 次，+2.5 秒 |
| 220 | 43131f7d subagent agent-ae09efa3e489be5e8:67 | Opus | 是 | 否 | 1 次，+3.4 秒 |
| 224 | 43131f7d main:8048 | Opus | 是 | 否 | 1 次，+2.9 秒 |
| 241 | 1e92a320 subagent agent-a7f9e03360780b821:75 | Sonnet | 是 | 否 | 1 次，+2.8 秒 |
| 255 | b3e00e33 main:1197 | Opus | 是 | 否 | 1 次，+3.5 秒 |
| 259 | 9b02764b main:768 | Opus | 是 | 否 | 1 次，+9.2 秒 |
| 272 | 30ce7bb8 main:118 | Opus | 是（build 接 tail） | **是**：同一條指令裡還有 mkdir 與寫暫存檔 | 3 次，+9.3 秒 |
| 289 | 0a015c50 | — | 找不到原文 | — | — |

抽樣結果：11 筆擋對、0 筆擋錯，2 筆連帶擋掉改檔或寫檔。平均每次被擋多花 15/11≈1.36 個工具呼叫；研究時的另一組樣本是 1.55。

**全體 46 筆**：46 筆都是真的測試或建置命令接 tail/head、而且沒有設 pipefail，命中位置都不在 heredoc 本文裡，所以擋錯 0 筆。連帶擋掉改檔或寫檔的有 16 筆：

- 12 筆是原始碼或測試檔：198、201、202、204、205、218、219、221、223、256、261、262。
- 4 筆是暫存檔或測試資料：249 改權限，272、277、290 寫檔。

兩個例子：

- 218→219：同一條「改檔＋跑測試」的指令被擋後原樣重送，又被擋一次。
- 221（43131f7d subagent agent-a0873d90f30e2bd94:36）：被擋後 2.2 秒，它只重跑了測試、沒有重做被一起擋掉的改檔，所以測到的是沒改過的程式。13.2 秒後看 git status 才發現改檔沒做，17.9 秒時補上。

**判定：幫倒忙（`harmful`），第 4 層，副作用已檢查。**

- 依據：觸發紀錄顯示這支 hook 連帶擋掉不該擋的動作（46 筆裡有 16 筆），依規格「幫倒忙優先於有效」。
- 為什麼記成第 4 層：這次做的事是對照過去紀錄（觸發紀錄加 transcript），沒有跑第一層回歸測試，也沒有回放。從第 4 層看有效性，47 筆觸發都擋下了，但改動後已知的再犯（11-04 s29，`claude plugin test` 接 tail，研究時歸 C 類）沒擋到，所以本來只能判「證據不足」。幫倒忙來自另一條證據，也就是 hook 的副作用抽樣，規格把它和層級分開定義。
- **這是我自己做的判斷**：規格同時寫了「第四層只能判無效或證據不足」。我的解讀是這一句限制的是有效性結論，不涵蓋副作用結論。retro-judge 不接受第 4 層帶副作用，那是指回放式的副作用；retro-store `verdict` 本身不檢查層級與判定的組合（05、07 已記為 Minor）。如果 main 認為第 4 層不該出現 harmful，這筆判定要用一筆新的判定取代（最新一筆為準）。
- 判定欄位：`--runs 48 --old-recurrences 48 --new-recurrences 1`，沿用 retro-judge 第 4 層的計法（事件數、事件數、擋不住的事件數）。48 個事件＝47 筆觸發＋1 筆擋不住的已知再犯。證據指針是 `~/.claude/logs/pipestatus-zsh-gate-audit.jsonl:221`（上面例子裡的 221）。

### 3.2 契約測試 PostToolUse 提醒（`chg:2026-11-01:contract-test-posttooluse-additional-context`）

這個提醒的觸發方式：Edit 或 Write 之後自動跑契約測試，wrapper 回非 0 時才注入提醒（`contract-test-receipt-gate.sh:61-77`），而且結尾一律 `exit 0`，不會擋任何動作。所以要找的觸發紀錄是 `~/.claude/logs/contract-test-runs/` 裡 effective_at 之後、`runner=posttooluse`、exit 非 0 的收據。以 session＋目標檔＋目標 mtime 為一次 hook 呼叫，共 27 次、5 個 session，同期的 763 次收據裡 0 次逾時（`contract-test-triggers.txt`）。27 次都在 transcript 找到對應的注入附件：ebdd84c1 1 次、c8ab2c54 15 次、b9cbe3dd 1 次、a6fb4ff7 2 次、0ecef65f 8 次。

**抽樣**：15 筆。c8ab2c54 以外的 12 筆全取，c8ab2c54 那一串連續注入取頭、中、尾 3 筆（`contract-test-sample-resolution.txt`、`contract-test-injection-context.txt`）。表中的行號是用 `grep -n` 重新取的；`contract-test-injection-context.txt` 的產生腳本用 `splitlines()` 計行，a6fb4ff7 那份遇到 U+2028 會多算 2 行，以表中為準。

| 觸發 | session:注入行 | 模型 | 失敗的測試 | 判斷 | 多花 |
|---|---|---|---|---|---|
| #1 11-01T13:15 | ebdd84c1:755 | Argon | review-brainstorm 契約 | 正在連續改檔，3 秒後下一次自動跑就通過了 | 0 |
| #2 11-02T10:05 | c8ab2c54:945 | Opus | c4 派工契約 | 助理在 :956 自己寫「測試會紅是預期中的」，照原計畫繼續改 | 0 |
| #9 | c8ab2c54:991 | Opus | 同上，再加 targets 契約 | 同上，連續改檔中 | 0 |
| #16 | c8ab2c54:1087 | Opus | 4 支契約 | 同上，10:12 前由後續自動跑全部轉綠 | 0 |
| #17 11-03T04:13 | b9cbe3dd:1733 | Argon | sepia 契約 | 下一步 0.7 秒後就去改測試檔，41 秒後通過 | 0 |
| #18 11-04T01:58 | a6fb4ff7:794 | Argon | architecture-visual-gate、transport | 前者正在改，屬於多餘的提醒；後者是 wrapper 在預設語系下 utf-8 解碼失敗（:793、:869），和這次改檔無關 | 照提醒跑 wrapper 失敗後，調查約 235 秒、7 次 Read，最後加 `LC_ALL=C` 才通過 |
| #19 | a6fb4ff7:844 | Argon | transport | 同一個語系問題 | 與 #18 是同一段調查 |
| #20 11-04T08:37 | 0ecef65f:2339 | Argon | ub_mode_grafts | TDD：測試是助理自己先寫紅的（:2300、:2306），提醒沒有帶來新資訊 | 0 |
| #21–#25 | 0ecef65f:2531、2545、2550、2579、2588 | Argon | pr-explain 契約 | 正在跨檔連續改，09:17:22 自動轉綠 | 0 |
| #26 | 0ecef65f:2828 | Argon | transport | 語系問題（:2827、:2868） | 從跑 wrapper 失敗到加 `LC_ALL=C` 重跑約 234 秒 |
| #27 | 0ecef65f:2855 | Argon | transport | 同上 | 與 #26 是同一段 |

抽樣結果：

- 擋錯 0 筆、連帶擋掉 0 筆：PostToolUse 不會擋。
- 11 筆屬於多餘，助理本來就知道或正在改，沒有多做任何事。
- 4 筆（2 段事件）把助理引到一個和改檔無關的 wrapper 語系問題，各多花約 234～235 秒。
- 這 2 段多花的時間能不能算在提醒頭上，不確定。不注入提醒的話，Stop gate 結束時也會要求用 wrapper 補收據；在改動後的 4 次 Stop 攔截裡，助理每次都改跑 wrapper。所以同樣的語系問題很可能只是晚一點出現（推論，沒有實際對照）。

**修正研究時的框架**：研究把這次改動的目標寫成「逾時」，再拿「757 次測試 0 次逾時」說它沒有用武之地。但使用者批准的原文（累積庫第 2327 行的 decision）是「於 contract-test-receipt-gate PostToolUse 未產生收據時當場提示補跑」，範圍包括測試失敗。所以「27 次都不是逾時」不代表觸發錯了對象。

**第 4 層：列出 P23 的每一次事件，逐筆標明擋不擋得住**

| 事件 | 時間 | 改動前後 | 擋得住嗎 | 依據 |
|---|---|---|---|---|
| 8688a95f | 10-29 | 前 | 否 | PreToolUse 不認 worktree 相對路徑與 `python3 -I`，PostToolUse 提醒管不到；這個 session 沒有任何收據 |
| eb7250c1 Stop:1501 | 10-29 | 前 | **可能** | 最後一次自動跑（14:21:08）是失敗，提醒會出現；改動後同一對檔案（deep-check 與 ub_mode_grafts）在 0ecef65f 收到提醒後，助理跑了 wrapper 並補齊收據 |
| 36089ede Stop:2405 | 10-31 | 前 | 否 | 攔截前最後的收據都是通過（08:35:13），攔截是之後的改動造成的，提醒不會出現 |
| 5526cb14、f5d68384 Stop | 10-31 | 前 | 否 | 自動跑全部通過，提醒不會出現 |
| d9f9d664 | 10-31 | 前 | 否 | 沒有 Bash 工具的 subagent，問題在派工規則，與這支 hook 無關 |
| d3e41a3e Stop:2202 | 11-04 | 後 | 否 | 先改測試檔、再裸跑測試，收據過期；這個 session 沒有注入 |
| 0ecef65f Stop:2975 | 11-04 | 後 | 否 | 注入有出現也被照做了，但之後又改了測試檔 `test_skill_verify_one_shot_callers.py`（:2482），收據再次過期 |
| 7a3e30bb Stop:40365 | 11-04 | 後 | 否 | 改測試檔後，用接 tail 的方式裸跑 |
| 91a05f3d Stop:919 | 11-05 | 後 | 否 | 改了 retro-workflow 及其測試檔（self-closeout 的檔案清單），這個 session 沒有注入；有沒有裸跑我沒查 |

合計 10 個事件：9 個擋不住，1 個可能擋得住。改動後的 4 次 Stop 攔截裡，有 3 次確認是「改了測試檔、裸跑測試、收據過期」，也就是 P23 當初 discovery 寫的根因（第 2044 行 cause_hypothesis）；第 4 次（91a05f3d）也改過測試檔。這支提醒只在受保護檔的自動測試失敗時才出現，涵蓋不到這種情況。

**判定：證據不足（`insufficient`），第 4 層，副作用已檢查。**

- 依據：第 4 層只能判「無效」或「證據不足」，而且必須所有事件都擋不住才能判無效；這裡有 1 個可能擋得住。副作用方面沒有擋錯，也沒有連帶擋掉，所以不判幫倒忙。
- 判定欄位：`--runs 10 --old-recurrences 10 --new-recurrences 9`，證據指針是 `~/.claude/logs/contract-test-runs/0ecef65f-2afc-40b1-87c5-78108b5414c5.jsonl:2`（#20 那次觸發的失敗收據）。
- 結果：這一項不會出現在待決定清單，因為清單只列無效與幫倒忙。這和工作單預期的「四項都進清單」不同，我沒有為了讓它進清單而改判。

## 4. 待決定清單的實際輸出

指令：`retro-store.py --store reports/local-analysis/retro-accumulation list --summary --audit-state reports/local-analysis/session-audit --rules-repo ~/.claude`（完整輸出在 `list-summary-after.json`）。執行前同一個指令的 `pending_decisions` 全部是空的，`legacy_observations` 是 212（`list-summary-before.json`）。

```
counts.legacy_observations: 212
pending_decisions.harmful:
  - change_id: chg:2026-11-02:pipestatus-gate-unprotected-test-pipe
    level: 4, open_proposals: [retract, modify]
    verdict_id: verdict:chg:2026-11-02:pipestatus-gate-unprotected-test-pipe:1
pending_decisions.make_hook:
  - change_id: chg:2026-11-01:browser-routing-lcu-playwright-and-stale-uid
    violating_sessions: 7, since: 2026-11-01T02:17:46Z, coverage: {sessions: 960, latest: 2026-11-06T02:46:23Z}
    evidence: 7 筆（每個 session 最新一筆），例如 d50d5752…:677、f23c3eb1…:7412、9b02764b…:2111
  - change_id: code-search-exclude-and-runaway-hot-3m-2026-10-31
    violating_sessions: 19, since: 2026-10-31T08:42:00Z, coverage: {sessions: 1081, latest: 2026-11-06T02:46:23Z}
    evidence: 10 筆，例如 71b25c30…:224、6bf0bd37…:582、3a82d019…:1237、b16272cd…:132
pending_decisions.ineffective / effective / needs_rejudge / side_effects_unchecked / make_hook_unchecked: []
pending_decisions.level_four_only: 2
```

四項裡出現三項：瀏覽器規則、搜尋規則（要不要做成 hook）與 pipestatus（要不要撤或改）。契約測試提醒沒有出現，原因見 3.2。這次 `list` 是只讀的，執行後累積庫仍然是 3222 行。

## 驗收逐條

| 驗收 | 結果 | 證據 |
|---|---|---|
| 四個舊改動各追加一筆補登，寫入類型與段落錨點，原改動紀錄不變 | 通過 | 第 3217–3220 行；前 3216 行與備份 `cmp` 相同 |
| 兩條規則用段落層級查詢重算；滿 3 場才提出，並附至少 3 筆原文指針 | 通過 | 7 場與 19 場；指針見第 2 節（各 4 到 6 筆），清單本身也帶 evidence |
| 兩支 hook 照副作用檢查方式從觸發紀錄抽樣，結果寫成判定紀錄 | 通過 | pipestatus 抽 12 筆（11 筆可判）並全體 46 筆核對；契約測試抽 15/27 筆；判定在第 3221、3222 行 |
| 四項出現在待決定清單；沒有自動建立 hook，也沒有自動撤回 | **部分**：3/4 | 契約測試提醒判證據不足，不進清單；沒有執行 `proposal`，也沒有改 `~/.claude` |
| 重算結果和研究時的檔案層數字不同時，寫出差異與原因 | 通過 | 第 2 節差異表；hook 部分見 3.1 與 3.2 |

## Minor findings（沒有修）

- **M1** `list --summary --audit-state` 要的是 session-audit 目錄。給 `state.sqlite` 檔案路徑時，每條規則都列成 `make_hook_unchecked`，原因只寫 `query-failed`，看不出是 `state-not-directory`。有標成「未檢查」、沒有被當成 0，這部分行為正確，只是原因不夠具體。重現指令：`retro-store.py --store <庫> list --summary --audit-state reports/local-analysis/session-audit/state.sqlite --rules-repo ~/.claude`，輸出在 `list-summary-with-sqlite-path.json`。
- **M2** retro-store `verdict` 不檢查層級與判定的組合（05、07 已記）。本票第 3221 行是「第 4 層＋harmful」，main 要決定這個組合是否成立。
- **M3** `make_hook` 的 evidence 取每個 session 最新的一筆。搜尋規則的段落裡有三個子句，所以清單附的 10 筆中，只有 5 筆對準這次新增的排除目錄或 TaskStop 子句（71b25c30、6bf0bd37、3a82d019、dffa1a56、b16272cd），其餘是裸 grep 子句或 rg。這是段落層級的設計限制，不是程式錯誤，但會讓使用者難以判斷。
- **M4** session-audit 正式查詢會排除 `status=pending` 的來源（目前有 d3e41a3e、ff3e1352）。這些來源分析完之後場數會變；研究時的審查者沒有過濾，所以和這次差 1 到 2 場。

## 沒做的與未驗證的

- 沒有逐筆打開違反標記的原始 session，驗證瀏覽器索引是不是真的過期，也沒有查 `tab.playwright.evaluate` 是否支援。
- 契約測試的兩段語系調查算不算提醒造成的成本，是推論；我沒有做「不提醒」的對照。
- eb7250c1 的「可能擋得住」是用改動後 0ecef65f 的行為類推的，沒有直接證據。
- runaway.sh（搜尋改動的 script 項目）沒有補登，也沒有判定。
- pipestatus 第 289 行找不到對應的 transcript，沒有判斷。
