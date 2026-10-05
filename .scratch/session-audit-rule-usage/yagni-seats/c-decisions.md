resolved_model: claude-fable-5-1

# YAGNI 審查 C — Implementation Decisions（D1–D12）＋ Testing Decisions（T1–T12）

審查範圍：只審 packet Part 1 的 Implementation Decisions 與 Testing Decisions；User Stories 不逐條審，只在核對引文時引用。只依 packet 判斷，未讀其他檔。

## 1. 一頁最簡版方案（v1 上限）

目標（使用者原話可追到的部分）：同一個 session-audit 引擎多輸出「規則摩擦」（A2），待折以規則為單位、未拍板累加、拍板後再犯開新條目（A4、A5），連續 4 週沒遇到場合的規則列成零使用清單（A1、A6），放進 daily-local 可選欄給 `/rules-slim` 消費（A2、A3），預設送規則全文、第一張票先實測容量（A7）。

1. **Prompt 加一段**：在既有逐段請求末尾附上 `~/.claude/CLAUDE.md` 與 `rules/common/*.md` 全文，要求模型在既有 findings 之外多回 `rules: [{id, status: applied|violated, quote}]`；沒遇到場合的規則不回。`id = <檔路徑>#<標題路徑>#<同標題下序號>`。規則版本：依**該 session** 的對話時間 `git log -1 --before=<time> -- <檔>` 取版本；取不到就把該 session 的規則標記記為未分析並寫 limitation。
2. **合成排除**：session 的專案目錄名含 `-private-var-folders-` 整個不進分析（主分析與規則標記同一個 filter）。
3. **promote**：`violated` 依 `id` 合併成 `## 待折` 一個條目（`target=rule:<檔>#<標題>`），累加次數與 `source_ref`／引文；條目已離開待折後再犯 → 新條目，寫上次處置與日期。不設最低次數。
4. **週表與零使用**：每 `id` 每週記 applied／violated 次數。該週成功分析段數為 0 → 該週不計。連續 4 個計入週 applied＋violated＝0 → 列入零使用清單，寫成 rules-size 週報一段；daily-local 第 12 條在可選欄多一行「零使用規則 N 條 → /rules-slim」。不新增 channel、提醒、排程。
5. **`/rules-slim`**：新增「零使用」一條判斷標準，讀上述段；修改走 skill-creator。
6. **第一張票**：容量實測（最長分段＋規則全文對 free 池實送，記回應模型／finish_reason／HTTP／token），結果決定 budget 與是否退回索引。
7. **測試**：session-audit CLI 端到端（假模型 HTTP server）、daily-local hook 文字斷言、`/rules-slim` 走 skill-creator 行為驗證。

這一頁已涵蓋 A1–A8 每一句使用者原話對應到的機制。spec 超出此頁的部分見第 4 節。

## 2. 分母檢查

**未定價。** spec 給了頻率側數字（📏 提醒 6 次、`/rules-slim` 真人執行 0 次、rule-review 一次性快照 2026-08-25），但沒有回答「每條死規則造成什麼損失、損失多少」（context bytes？誤行為次數？）；而且驅動整件事的「28 條規則在 61 個 session 零出現」出自 skill-up 汙染 36/61 的那一輪（Part 3 第 2 列），尚未在乾淨樣本上重現。依 rubric，未定價問題的解法以第 1 節最簡版為上限。

附帶的反證（對本批 decisions 的規模有影響）：消費端 `/rules-slim` 在 9/1 之後 0 次真人 invoke（Part 3 第 6 列）。這條產線的下游目前沒有需求行為，只有使用者 A3 的意願表述；因此任何「讓清單更精緻」的 decision（分類、覆蓋門檻、改寫重算）在有第一份清單被真的消費前，都沒有定價依據。

## 3. 逐條表

來源類：a＝使用者原話（已核對 Part 2 引文）／b＝量測證據（Part 3 或 spec 內出處）／c＝模型推導。引文只支持方向時，機制部分標 c。

| # | 條目 | 來源類 | verdict | 一句理由 |
|---|---|---|---|---|
| D1 | 同一引擎、同一次讀取；每段回規則清單＋applied\|violated＋引文；沒場合不回報 | a（A2「同一個引擎，輸出另一種摩擦」）；輸出欄位 c | keep | 引文支持同引擎；「同一次請求」是不另養分析器的最簡實作；applied\|violated 二分是 US6（使用者問句）必需，砍掉就分不出死碼與違規。 |
| D2 | 預設送規則全文；budget 由第一張票實測決定；撞上限才退索引 | a（A7「可以」針對此三段提案） | keep | 引文逐字支持機制；注意使用者「free 池都支援 1M」是未驗證信念，唯一實測是 24749 bytes／3905 tokens（Part 3 第 9 列），D3 正是為此而存在。 |
| D3 | 第一張票＝容量實測，記回應模型／finish_reason／HTTP／token | a（A7「可以」）；記錄欄位 c | keep | 使用者同意；記錄欄位是判定 budget 的最少資料，砍掉任一欄就無法判「撞到哪條腿的上限」。 |
| D4 | 規則版本取自 git，依分段時間取當時版本；取不到→未分析，不退回當前版 | b（backpass 抽查 #4：9/30 對話被 10/05 改寫規則判違規）；git 機制 c | keep | 證據直接展示砍掉的損失（回填時舊段被新規則判違規、進待折要使用者拍板）；「不退回當前版」正是 bug 本身，不能砍。可簡化：粒度改為每 session 取一次版本即可（一個 session 跨規則 commit 的情況可忽略），不必每段查。 |
| D5 | 規則識別＝檔路徑＋標題路徑＋條目原文；原文改寫即新規則，零使用計數重算 | c（[inferred]） | demote-v2 | 識別本身必要（砍掉就無法計數，最簡版用路徑＋標題＋序號）；但「改寫即新規則、重算 4 週」是推導，砍掉的損失只是「大改過的規則可能早一輪進清單」（仍由人拍板），而留著的代價是使用者常改規則檔（trial 更新、rules-slim）→ 多數規則永遠到不了 4 週，清單長期空白。 |
| D6 | 合成對話排除：另依 cwd 排除 `/private/var/folders/**`；同時修主分析缺口 | b（session-audit.py 第 41、509–515、676 行只看旗標與路徑段；synthetic 欄位 0 筆；71/100、584 個 skill-up 目錄） | keep | 證據直接支持機制：旗標路徑抓不到、只有 cwd 能分；「順便修主分析」是同一個 filter，不是範圍擴張。 |
| D7 | 規則摩擦以規則為單位：合併條目、未拍板累加、拍板後再犯開新條目寫上次處置、不設門檻；沿用鎖與不改寫保護 | a（A4「條目改為 by rule」、A5「正確」含「不用設門檻」） | keep | 引文逐字支持四個行為；signal_type／flags／target 格式是接既有 promote 的必要欄位。注意 spec 自己點出的張力：既有「不改寫既有行」保護與「累加＝改寫條目」衝突，實作時要先決定是改寫行還是在條目下追加子行，這不是 YAGNI 問題但會影響 T3。 |
| D8 | 權限邊界不變：agent-observation 無佐證不得推薦折入；不改規則／skill／hook | b（friction PROTOCOL authority gate；session-audit SYSTEM_PROMPT） | keep | 是零成本的「不做」約束，砍掉等於放寬既有閘門。 |
| D9 | 每週使用量表：applied／violated／session 數／「覆蓋是否足夠」；覆蓋不足的週不計入零使用連續週 | c（[inferred]）；4 週本身來自 a（A6） | demote-v2 | 週計數必要（沒有它算不出使用者說的 4 週）；但「覆蓋是否足夠」沒有定義門檻值，砍掉的損失只是「free 池部分失敗那週可能讓某條規則早一週進清單」。v1 只做「該週成功段數為 0 → 不計」，門檻值等第一份真實週表出來再定。 |
| D10 | 零使用清單：連續 4 個覆蓋足夠週 applied＋violated＝0；每條附模型初步分類三選一 | a（A6「可以四週」；零使用語意來自使用者問句，assistant 回答不在 packet）；分類 c（對應 US9 [inferred]） | demote-v2 | 4 週清單留；「三選一分類」砍掉的損失是使用者可能誤刪 lint 擋住的規則——但清單只是素材、刪改逐段由人拍板（Out of Scope 第 5 條），且模型在「零次出現」的前提下只能看規則文字猜類別，證據為零。等第一份清單被真的消費後再看要不要分類。 |
| D11 | 出口接 rules-size 週報一段＋daily-local 第 12 條可選欄多一行；不新增 channel／提醒／排程 | a 方向（A2「給瘦身機制消費」、A3 可選欄）；接法 c | keep | 接法是重用既有讀取路徑（daily-local 第 12 條已讀 rules-size 報告）的最小接線；砍掉就沒有任何出口，A1 的清單無處可見。 |
| D12 | `/rules-slim` 新增「零使用」判斷標準；允許以新證據重開 2026-08-25 判「留」的條目；走 skill-creator | a（A2「應該要給瘦身機制消費？」）；重開子句 c；skill-creator 是使用者全域規則 | keep | 不加判斷標準則清單無人消費；「允許重開」砍掉的損失具體：零使用規則幾乎都在 8/25 判「留」的集合裡（當時沒有使用量資料），不重開就等於整份清單對 rules-slim 無效。 |
| T1 | 好測試只看外部行為，不驗內部函式或 SQL | c | keep | 是約束不是功能，零成本；砍掉會讓 T3–T9 退化成綁實作細節的測試。 |
| T2 | 主切入點：session-audit CLI 端到端，沿用既有假模型 HTTP server 與既有案例形狀 | a（A7 三個測試切入點「可以」） | keep | 使用者同意；重用既有 Recorder 不新增測試基礎設施。 |
| T3 | 同規則三筆違規 → 待折一條目、次數 3 | a（驗 D7） | keep | 直接對應 A4／A5 行為；是最小合併案例。 |
| T4 | 條目已搬到已折段後再違規 → 新條目帶上次處置 | a（驗 D7，A5「正確」） | keep | 直接對應使用者確認的第二種行為。 |
| T5 | 含兩個 commit 的假規則 git repo，舊對話段取到舊版 | b（驗 D4） | keep | 對應 backpass 抽查 #4 的失敗模式；若 D4 粒度改每 session，測試案例不變。 |
| T6 | `-private-var-folders-*-T-skill-up-*` 目錄的對話不進統計 | b（驗 D6） | keep | 對應 71/100 汙染證據。 |
| T7 | 覆蓋不足的週不累計零使用 | c（驗 D9 門檻） | demote-v2 | 隨 D9：v1 只測「成功段數為 0 的週被跳過」，門檻值版的測試等門檻定義出來再寫。 |
| T8 | 連續 4 週零使用才進清單 | a（驗 D10，A6） | keep | 直接對應「可以四週」。 |
| T9 | 規則版本取不到 → 該段標未分析、不改用當前版 | b（驗 D4 fail policy） | keep | 砍掉就無法保證不重演抽查 #4。 |
| T10 | daily-local hook 文字測試：可選欄含零使用一行並指向 `/rules-slim` | a（A7、A3） | keep | 使用者同意的切入點；是 D11 唯一可觀察的外部行為。 |
| T11 | `/rules-slim` 走 skill-creator RED→GREEN→REFACTOR | a（A7）＋使用者全域規則 | keep | 全域規則強制，不是本 spec 新增的成本。 |
| T12 | 容量實測收據列每次回應模型與 finish_reason | a（A7、D3） | keep | 是 D3 的驗收形式；沒有收據就無法做「只有這張票能決定 budget」的判定。 |

反方下限核對：本席輸出 4 條 demote-v2、0 條 kill；所有 c 類條目（D5、D9、D10 分類、D11 接法、D12 重開子句、T1、T7）都在理由欄寫了「砍掉的具體損失」。

## 4. spec 超出最簡版的部分

| 來源條目 | 超出的部分 | 處置 | 砍掉的具體損失 |
|---|---|---|---|
| D5 | 「條目原文被改寫即視為新規則，零使用計數重新起算」 | demote-v2 | 大改過的規則可能早一輪進清單；反之留著會讓常改的規則永遠累積不到 4 週（使用者規則檔改動頻繁：trial 登記、rules-slim、本 spec 自己也要改 rules-slim）。 |
| D9 | 「該週覆蓋是否足夠」門檻（未定義數值） | demote-v2（v1 降為「成功段數 0 → 不計週」） | free 池部分失敗那週可能讓規則早一週列入；清單是人工拍板素材，誤差可承受。 |
| D10 | 每條零使用規則附模型初步分類三選一 | demote-v2 | 使用者可能誤刪 lint 擋住的規則；但刪改逐段由人拍板（Out of Scope），且模型在零出現前提下只能憑規則文字猜，分類無證據基礎。 |
| D4 | 每「段」取規則版本（最簡版改每 session 取一次） | keep 但簡化 | 無損失：一個 session 橫跨規則 commit 的情況在 packet 內沒有任何證據。 |
| T7 | 覆蓋不足門檻的測試 | demote-v2 | 隨 D9。 |

未超出但要提醒實作方的兩點（非 YAGNI）：
- D7 的「累加＝改寫既有條目」與既有「不改寫既有行」保護直接衝突，spec 只說「在同一把鎖內完成並保留原行內容」，沒說怎麼保留；T3 的斷言形狀取決於這個決定。
- D2 的前提「free 池都支援 1M」在 packet 內無證據，唯一實測是單腿 mimo-v2.6-flash 24749 bytes；D3 不能省。

## 推翻條件

- 若 packet 外有證據顯示使用者規則檔每月改動 < 1 次，D5 的「改寫重算」損失論點不成立，D5 可回 keep。
- 若第一份真實週表顯示 free 池部分失敗是常態（例如每週有 >20% 段落失敗），D9／T7 的門檻應提前到 v1。
- 若 `/rules-slim` 在第一份零使用清單出現後被真人執行且使用者在拍板時表示分不出 lint 類規則，D10 的分類應回 keep。
