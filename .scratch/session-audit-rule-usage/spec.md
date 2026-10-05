# session-audit 規則使用量（全域規則摩擦＋零使用清單）

## Problem Statement

使用者的常駐規則（`~/.claude/CLAUDE.md` 與 `~/.claude/rules/common/`）越寫越多，但沒有持續的證據說明哪條規則真的在對話裡派上用場、哪條常被違反、哪條根本沒遇過場合。2026-08-25 的 rule-review 是一次性快照，之後沒更新；backpass 試用（2026-09-28～10-05）能給出逐條規則的訊號，但抽查準確率 6/10、會拿當前規則判舊對話、讀不到 `rules/`，已 KILL。瘦身機制 `/rules-slim` 被 digest 提醒 6 次、真人執行 0 次，手上也沒有使用量資料可用。

## Solution

讓 session-audit（真 free 池逐段讀自然 session；2026-10-05 仍在部署中，正式啟用並有真實輸出後才接）在同一次分析裡，多標出「這段跟哪幾條常駐規則有關、有沒有照做」。同一份標記分兩個出口：

- 違反 → 「全域規則摩擦」，以規則為單位進摩擦待折，交 `/trial-review` 拍板。
- 沒遇到場合 → 連續 4 個有覆蓋的週都沒遇到場合的規則，列成零使用候選清單（概念同死碼偵測），放進 rules-size 週報，經 daily-local「可選」欄呈現，給 `/rules-slim` 當瘦身素材。

這是未定價問題（沒有「死規則每次造成多少損失」的基線），範圍以最簡版為上限：不做完整週統計表、不做模型分類。

## User Stories

1. As 使用者, I want 一份「多久沒遇到場合」的規則清單, so that 我知道哪些常駐規則可能是死碼 [user: "感覺它算出 28 條規則在 61 個 session 裡都沒出現過這個很有用啊？"]
2. As 使用者, I want 這件事用 session-audit 同一個引擎做、輸出另一種摩擦, so that 不用再養一套分析器 [user: "就是用同一個引擎，輸出另一種摩擦對吧，這應該叫做跟全域規則的摩擦？"]
3. As 使用者, I want 零使用清單交給瘦身機制消費, so that 瘦身有使用量證據可看 [user: "喔對，所以2應該是瘦身素材，應該要給瘦身機制消費？"]
4. As 使用者, I want 規則摩擦在待折清單裡一條規則一個條目, so that 同一件事重複出現不會把清單塞滿 [user: "我覺得條目改為by rule不就好了"]
5. As 使用者, I want 未拍板的規則條目持續累加、拍板過後再犯就開新條目並寫明上次處置, so that 我分得出「還沒處理在累積」與「處理過還在犯」 [user: "正確"]
6. As 使用者, I want 零使用只算「確實沒遇到場合」，不含「場合出現但沒照做」, so that 違規與死碼不混在一起 [user: "零使用清單的意思是確實沒觸發時機？而不是該觸發沒觸發？"]
7. As 使用者, I want 連續 4 週零使用才列入清單, so that 一個月一次的低頻情境不被誤判 [user: "可以四週"]
8. As 使用者, I want 零使用清單出現在 daily-local 執行完高中低後的「可選」欄、不回也沒關係, so that 我有空才處理、不會被追 [user: "還是要加個欄位是 讓我決定要不要做都可以，在整個每日分析包含高中低都做完之後寫出來給我看"]
9. As 使用者, I want 每段用該對話當時生效的規則版本判, so that 規則改版後舊對話不被誤判違規 [evidence: backpass 第二輪重跑抽查 #4，2026-09-30 對話被 2026-10-05 14:40 改寫後的規則判成違規；review-evidence/2026-10-05-755eec5b.md]
10. As 使用者, I want skill-up 評測等合成對話完全不進統計, so that 使用量不被假對話灌水 [evidence: backpass 首輪 100 份樣本中 71 份來自 `/private/var/folders/…/T/skill-up-*`；memory reference_skill_up_synthetic_sessions_in_projects_dir_2026_10_05]
11. As 使用者, I want 範圍涵蓋 CLAUDE.md 與 rules/common, so that 不重演 backpass 讀不到 rules/ 而提出重複建議 [evidence: backpass 第二輪提案 e5 與 rules/common/trial-observation.md 第 5 行同義]

## Implementation Decisions

- **同一引擎、同一次讀取**：在 session-audit 既有的逐段分析請求裡加入規則標記，不另開一輪模型呼叫。模型回傳每段「相關規則清單」，每項含規則識別、`applied|violated`、逐字證據引文；場合沒出現的規則不回報。現有 findings 結構與 prompt 的「只做 agent-observation、不給修法」約束不變。[user: "就是用同一個引擎，輸出另一種摩擦對吧"]
- **預設送規則全文**：每段請求附上該段時間點的常駐規則全文；`REQUEST_UTF8_BUDGET`（目前 24000，程式自註為工程預設、非容量實測）是否調整由第一張票實測決定。實測撞到 free 池某腿的上限時，才改用規則索引（編號＋一句摘要）；索引之後要不要再補送原文、怎麼補，等真的需要退回時再設計，本 spec 不預先決定。[user: "但我現在free池那些context window應該都有支援1M"；user: "可以"]
- **第一張票＝容量實測**：用最長分段＋規則全文對 free 池實送多次，記錄回應模型、finish_reason、HTTP 狀態與 token 數；只有這張票的結果能決定 budget 與是否退回索引。[user: "可以"]
- **規則版本取自 git**：常駐規則檔在 `~/.claude` repo 內，每個 session 依其對話時間取一次當時最新的 commit 版本（不必每段查）；取不到版本（檔案不在 git、git 失敗）時，該 session 的規則標記記為未分析並寫 limitation，不退回用當前版本。commit 時間不等於生效時間（規則檔可能先改、晚 commit），所以這個版本是「最佳候選」，報告與清單要標出它是用哪個 commit 判的，不宣稱確定。[evidence: backpass 抽查 #4]
- **規則識別**：以「規則檔路徑＋所在標題路徑＋條目原文」為一條規則；條目原文被改寫即視為新規則，零使用計數重新起算。[inferred；user: "可以照推薦"（YAGNI review F15 選 a）]
- **合成對話排除**：除既有的記錄旗標與 `eval-roots`／`synthetic-eval` 路徑段，另排除已證實的 skill-up 特徵：cwd 落在 `$TMPDIR` 下的 `skill-up-<n>/`（專案目錄名含 `-T-skill-up-`）。不擴大成整個 `/var/folders`，因為只有 skill-up 有證據。這也修正 session-audit 主分析本身的同一缺口。[evidence: session-audit.py 第 41、509–515、676 行只看旗標與路徑段；skill-up jsonl 抽查 synthetic 類欄位 0 筆]
- **全域規則摩擦以規則為單位**：promote 時，同一條規則的違規合併成待折段的一個條目（`signal_type=agent-observation`、`flags=speculation`、`target=rule:<檔>#<標題>`），附違規次數與各 session 的 `source_ref`／引文。條目仍在 `## 待折` 時新違規往同一條目累加；該規則已有拍板（已折／已否決／休眠）之後再犯，開新條目並寫上次處置與日期。不設最低次數門檻。沿用既有 promote 的檔案鎖與「不改寫既有行」保護：條目首行（`- ` 開頭）寫好後不再改，之後的違規以縮排子行追加在條目下方（每行一筆 `source_ref`＋引文），次數由子行數得出；這樣摩擦掃描仍只把首行算成一個待折項。[user: "我覺得條目改為by rule不就好了"；user: "正確"]
- **權限邊界不變**：規則摩擦是 agent-observation，依 PROTOCOL 沒有 user／expert／test 佐證不得推薦折入；session-audit 不改規則檔、skill、hook。[evidence: friction PROTOCOL authority gate；session-audit SYSTEM_PROMPT]
- **最小狀態**：只存兩樣——每條規則「最後一次遇到場合（applied 或 violated）的時間」，以及每週「成功分析的段落數」。不做每規則×每週的完整次數表。成功段數為 0 的週算「沒覆蓋」：缺資料不是零。[inferred]
- **零使用清單**：以完整規則名冊對照（不能只看曾被回報過的規則），連續 4 個有覆蓋的週都沒遇到場合的規則列為候選；中間有一週沒覆蓋就重新起算，不把不連續的週拼成 4 週。每條附：規則原文與識別、判斷用的規則 commit、觀察期間、最近一次遇到場合的時間或「開始觀察後未見」。不附模型分類。呈現為「觀察範圍內未見場合的候選」，不是已證明的死碼。[user: "可以四週"；user: "零使用清單的意思是確實沒觸發時機？"]
- **出口接 rules-size 週報＋可選欄**：零使用清單寫成 rules-size 週報的一段；daily-local 第 12 條已讀「最近一份 rules-size 報告」，在可選欄多列一行「🪦 零使用規則 N 條 → /rules-slim」。不新增 channel、提醒或排程。[inferred]
- **`/rules-slim` 只接讀取**：讓它在流程中讀到 rules-size 週報的零使用候選段並呈現給使用者；不新增「零使用」正式判斷標準，也不放寬「2026-08-25 判留的條目不重判」的規矩。最終刪改仍逐段由使用者拍板。修改走 skill-creator。v2 條件：第一份清單出現後使用者實際跑 `/rules-slim`，且有具體條目被「不重判」規矩擋住。[user: "應該要給瘦身機制消費？"；user: "可以照推薦"（YAGNI review F14 選 a）]

## Testing Decisions

- 好測試只看外部行為：給定假模型回應與假 session，驗證摩擦檔、零使用清單、報告的內容；不驗內部函式或 SQL 細節。
- **主切入點：session-audit CLI 端到端**（`scan → run → promote/report`），沿用 `session-audit.test.py` 的本機假模型 HTTP server（`ThreadingHTTPServer` Recorder）與 `test_promote_uses_real_source_ref_and_skips_duplicates`、`test_synthetic_self_and_probe_classification` 這類既有案例形狀。要覆蓋：
  - 同規則三筆違規 → 待折只有一個條目、次數 3；
  - 條目已搬到已折段後再違規 → 新條目帶上次處置；
  - 用含兩個 commit 的假規則 git repo，驗證舊對話段取到舊版規則；
  - `-T-skill-up-` 專案目錄的對話不進統計；
  - 成功段數為 0 的週打斷連續計數；連續 4 個有覆蓋的週零使用才進清單；從未被回報過的規則也會出現在清單；
  - 規則版本取不到 → 該 session 標未分析、不改用當前版。
- **daily-local hook 文字測試**：`daily-local-analysis-trigger.test.sh` 加斷言，可選欄含零使用規則一行並指向 `/rules-slim`。
- **`/rules-slim`**：無既有契約測試；照規則以 skill-creator 的 RED → GREEN → REFACTOR 行為驗證收尾。
- **容量實測**（第一張票）是真實 free 池呼叫，不是單元測試；收據要列每次的回應模型與 finish_reason。

## Out of Scope

- 自動修改任何規則、skill、hook 或產品程式碼。
- `~/.codex/AGENTS.md` 與公司 repo 內的規則檔。
- 圖片／影音內容的規則判斷（沿用 session-audit 對媒體的未分析標註）。
- 歷史 session 回填的排程策略（沿用 session-audit 既有回填，不另訂）。
- 自動刪除零使用規則；清單只是素材。
- 準確率保證：free 模型可能看漏場合，也可能誤判違規（backpass 同類判斷抽查只有 6/10 正確），所以零使用清單只是候選、規則摩擦條目都標 speculation，兩者都由人拍板。
- 每規則×每週的完整次數表、零使用規則的模型分類：v2 再議，條件是有人實際需要次數趨勢，或第一份清單被 lint 類規則塞滿而造成誤判。

## Further Notes

- **前置條件**：session-audit 本體仍在部署中（2026-10-05 17:14 另一 session commit「reconcile main for session audit deployment」，且開了 `fix/session-audit-scan-memory` worktree）。實作要等它正式啟用、有真實輸出後再接，並與該工作協調，不平行改同一檔。
- **觀察分兩段**（上線日起算）：+7 天看標記、規則摩擦合併、版本判定、合成排除與 free 池容量；+30 天看第一份零使用清單是否出現在可選欄、`/rules-slim` 讀得到。登記一筆 trial，第一段 review 時把日期改到 +30。[user: "那這樣review可能要分兩段，有一段要放30天後比較準對吧"]
- **停損條件**：+30 天第一份零使用清單出現後，若使用者仍沒跑過一次 `/rules-slim`，零使用這條出口降為 v2 或停掉；規則摩擦出口另外看 `/trial-review` 有沒有實際處理這些條目。
- 已知盲點：lint／formatter 擋住的規則（如 2 空格縮排）照做時對話裡不留痕跡，模型可能認不出它的場合，會被誤列零使用；清單用「未見場合的候選」措辭，就是為了不讓人把它當死碼直接刪。
- 來源討論：trial-review session 755eec5b（2026-10-05）；backpass 證據在 `~/Desktop/projects/.claude/trials/review-evidence/2026-10-05-755eec5b.md`。
