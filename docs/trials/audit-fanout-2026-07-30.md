# Digest audit 三層改造觀察記錄

- **開案**：2026-07-30
- **起因**：2026-07-29 與 2026-07-30 連兩天撞滿 3 輪 audit 上限
- **改造範圍**：機械檢查下放 script（層 1）＋ 單一 auditor 拆 6 個並行 lens（層 2）＋ 新增跨天沿用重估 lens（層 3）
- **輪次上限**：**維持 3 輪不變**（使用者 2026-07-30 拍板：先觀察，穩定 1〜2 輪通過再降）

---

## 一、明後天要對照的觀察目標

每天的 digest 回報已被 trigger hook 強制要求附「audit 輪次表 + lens 分佈 + 層 1 命中數」，直接拿那張表對照下面判準。

### 主指標

| 指標 | 2026-07-30 基準（改造前） | 成功的樣子 | 失敗的樣子 |
|---|---|---|---|
| R1 material | 5 | **上升到 ~10+**（後輪的被前移） | 仍在 5 左右 → lens 沒把後輪的問題往前拉 |
| R2 material | 4 | **0**（→ 兩輪收工） | 穩定 ≥ 1 → lens 切分仍有縫 |
| R3 是否需要跑 | 跑了、3 material | 不需要跑 | 仍跑滿且有 material |
| lens 分佈 | 無此資料 | 看得出哪個 lens 有效 | 某 lens 連日 0 筆 → 該角度空轉或 prompt 無效 |
| 層 1 HARD 命中 | 0（當天已手修完才上線） | 偶爾 > 0 都算正常 | 連日 0 且 R1 出現同類數字錯 → detector 正則沒命中實際措辭 |
| 層 1 SOFT 命中 | 1（codex-security 兩量級數字） | 每天 0〜3 筆、逐筆判定 | > 5 筆 → 誤報太多、會被忽略，要收緊 |

### 降上限的判準

**連續 2〜3 天出現「R1 material 高、R2 material = 0」** → 可把上限從 3 輪降到 2 輪。

反之若 R2 穩定有 material，先看 lens 分佈決定補哪個角度，不要直接加輪次。

### 特別要盯的兩件事

1. **lens 缺席**：workflow result 的 `lenses_ran` < `lenses_total` 表示某 lens 的 agent 回 null，那個角度當天完全沒被覆蓋。回報時要揭露，不能當成「該角度沒問題」。
2. **前輪修補引入新錯**：2026-07-30 第 3 輪的 4 筆有 3 筆 material，全落在前輪改動處或前輪未觸及的外部基準上。第 2/3 輪的 prompt 已明寫要把改動處當新內容嚴格對位。

---

## 二、研究發現（四條規律）

### 規律 1：這不是品質退化，是檢測器換代

翻了 25 天的第 1 輪 audit journal，分水嶺極清楚：

| 期間 | 天數 | 第 1 輪 findings | 平均/天 | 其中 inference_error |
|---|---|---|---|---|
| 07-06〜07-24 | 19 | **3** | 0.2 | **0** |
| 07-25〜07-27 | 3 | 18 | 6.0 | 15 |
| 07-28〜07-30 | 3 | 24 | 8.0 | 20 |

`inference_error`（推論超出來源）是 **07-25 才加進 schema 的第 4 類**。加之前 19 天幾乎天天 pass（總共只 3 筆，全是 URL/ID 對錯位）；加之後跳 30 倍。25 天累計 45 筆裡 35 筆屬這一類。

所以連兩天撞 3 輪不是 digest 變爛，是開始檢查一個以前完全沒看的維度、存量錯誤一次湧出。3 輪上限規則本身也正好誕生在 07-27（第 4 類上線第 3 天）。

**查法備忘**：journal 在 `~/.claude/projects/-Users-linhancheng-code-social-info/<session>/subagents/workflows/wf_*/journal.jsonl`，grep `hallucinations` 後抽 `"type"` / `"severity"` 配對。抽取時要先把 `\"` 反轉義，否則 JSON 巢在字串裡抓不到。

### 規律 2：錯誤密度集中在 voice 段，差 21 倍

2026-07-30 的 21 筆按元素攤開：

| 元素 | 單位數 | findings | **密度** |
|---|---|---|---|
| `voice` 個人評論 | 10 段 | **11** | **1.10 /段** |
| `summary` 主軸摘要 | 13 段 | 4 | 0.31 /段 |
| 表格判斷欄 | 19 欄 | 2 | 0.105 /欄 |
| `li` 條目 | 58 條 | 3 | **0.052 /條** |

改造前的 prompt 要求「58 個 li + 10 段 voice 全數 audit 不抽樣」，等於把 84% 的單位配給只產出 14% 錯誤的地方。

### 規律 3：輪次遞進 = 證據成本遞進，不是資訊不可得

| 證據層級 | R1 | R2 | R3 |
|---|---|---|---|
| T1 單檔 grep / 可 script | 5 | 2 | 0 |
| T2 跨條目比對（2+ 條目交叉） | 2 | 3 | 0 |
| T3 讀完整原文語意 | 0 | 2 | 1 |
| T4 跨天檔案 | 2 | 0 | 0 |
| T5 外部檔案 / 指令 | 1 | 0 | 2 |
| T6 純推理 | 0 | 0 | 1 |

**關鍵數字：第 2+3 輪 11 筆裡有 8 筆（73%）只需要 raw md 內的資訊**——第 1 輪本來就查得到。差別在於把標題當「對位鑰匙」（有沒有這條），沒當「內容」讀（它說了什麼）。兩個最貴的例子都藏在標題裡：

- `SK Hynix stock fell some 40% in the last 30 days, finally cheap RAM and GPUs again?` — 後半句社群問的是會不會變便宜，digest 拿它當「成本收緊」證據，方向相反
- `Anthropic is finding bugs faster than Microsoft can fix them` — 講 find vs fix 的不對稱，digest 讀成兩家公司修補速度比較

### 規律 4：跨天沿用的錯誤，單日 audit 結構性抓不到

「正常版本間隔期」這句的軌跡：

| 日期 | 寫的天數 | 該句在 digest | 當天 audit 抓到？ |
|---|---|---|---|
| 07-27 | 連續第 2 天 | 1 處 | ❌ |
| 07-28 | 連續第 3 天 | 2 處 | ❌ |
| 07-29 | 連續第 4 天 | 2 處 | ❌ |
| 07-30 | 連續第 5 天 | 2 處 | 只有第 3 輪跑了 `gh release list` 才抓到 |

**連續四天、每天都跑 audit、沒有一輪抓到**。天數從 2 翻到 5、常態判斷從未重估，而實際發版間隔中位數 0.99 天、歷史最大 3.01 天——5 天早就在區間外。

同型案例：`softcane/hamza` 在 07-29 被 surface 為「1 star 的出站遮罩候選」，07-30 被寫成使用者既有的「出站遮罩紀律」並用來論證另一個工具值得試。

根因是結構性的：auditor 的 INPUT 只有今天的 raw md + digest，看不到前一份 digest，也沒被要求問「這句的前提今天還成立嗎」。看起來像既有知識的句子在 raw md 裡查不到對應，就被當背景資訊放過。這正是層 3（lens E）要補的洞。

---

## 三、改造內容與驗證狀態

### 檔案清單

| 層 | 檔案 | 狀態 |
|---|---|---|
| 1 | `~/.claude/scripts/verify-digest-mechanical.py` | 新增 |
| 1 | `~/.claude/scripts/verify-digest-mechanical.test.sh` | 新增（三件 fixture） |
| 1 | `~/.claude/scripts/verify-daily-digest.sh` | 接上 detector，exit code 契約 0/1/2 不變 |
| 2+3 | `~/.claude/workflows/daily-topic-analysis.js` | DigestAudit 改 6 lens 並行 + 合併去重 |
| — | `~/.claude/hooks/daily-topic-analysis-trigger.sh` | 加輪次表回報硬性要求 |
| — | `.claude/hooks/digest-write-verify.sh`（本 repo、gitignored） | 第 2/3 輪 prompt 加「前輪已跑 6 lens」交接 |
| — | `.claude/hooks/digest-write-verify.test.sh`（本 repo、gitignored） | 加第 6 件 fixture（URL 綠 + 機械紅） |

### 六個 lens 的分工

| lens | 負責的錯法 |
|---|---|
| F 機械對位 | fabricated / url_mismatch / id_wrong，全量窮舉 |
| A 來源限定詞 | 限定詞被吃掉 / 強度被升級 / 標題後半與摘要末句被丟掉 / 兩組指標混談 |
| B 焊接與內部一致 | 「A 所以 B」兩端行號不同源 / 時序 / 因果腦補 / 跨段口徑與指路 |
| C 基準宣稱 | 正常・首見・最高・連續 N 天・普遍 等基準詞必附來源；跨天比較一律 grep 舊檔 |
| D 紀律對位 | voice 引用的 CLAUDE.md 條文與 `[[wiki]]` 是否真存在且語意相符 |
| E 跨天沿用重估 | 比對前一份 digest，找前提已變卻照抄的沿用句 |

層 1 的機械檢查分兩級：**HARD** 併入 gate exit 1（🔁 計數 / stale 計數 / 圖表分項加總 / 跨天數字序列）；**SOFT** 只印警告不阻擋（指標符號誤用 / 同 repo 數字量級衝突 / 比例措辭算術）。分級理由是不誤殺阻擋出貨，同時把訊號送到眼前。

### 已驗證

| 項目 | 結果 |
|---|---|
| 層 1 detector 三件 fixture | 19 pass / 0 fail — known-good 不誤殺、5 種 known-bad 全抓（都是當天真實犯過的錯注回去）、3 種邊界（off-by-one 要攔、容差內不誤報、無宣稱不攔） |
| 層 1 接線 | 真實 digest exit 0；「URL 96/96 全綠 + 機械 HARD 命中」→ exit 1 |
| 合併去重邏輯 | 18 pass / 0 fail — 跨 lens 撞同筆、severity 升級、同行不同 type 不誤併、agent null、欄位畸形、當天 20 筆情境重播 |
| 6 lens 定義 | 代號無重複無缺漏、schema enum 對齊、workflow `node --check` 通過 |
| 本 repo hook | 6 件 fixture 全過 |
| trigger hook | 語法 + 實際觸發輸出含全部新指令 |

### 未驗證（明後天才知道）

**6 個 lens 的實際 LLM 產出品質**。驗過的是接線、合併、schema、fixture，不是「lens 真的抓得比較準」。

---

## 四、回退

```bash
# ~/.claude（三個改檔 + 兩個新檔）
git -C ~/.claude revert e102899

# 本 repo 的 hook（gitignored、走檔案備份）
cp .claude/hooks/digest-write-verify.sh.bak-2026-07-30 .claude/hooks/digest-write-verify.sh
```

- `~/.claude` 改造前座標：`392c65f`（三個被改的檔在那裡都是 clean）
- 本 repo 的改造前 digest 基準：`b3dfbfc`（[reports/digest-2026-07-30.html](/reports/digest-2026-07-30.html)，已修過三輪的版本）

---

## 五、待決事項

1. **Active ownership 已移回全域 ledger**：使用者先前指示「不要放全域的、放這個專案就好」，是因當時誤以為沒有現成全域 trial；確認既有 `digest-audit-severity-gate` 後，2026-07-30 最新拍板改由 `~/Desktop/projects/.claude/trials/active.md` 的該 H2 擔任唯一 active owner，review date 為 2026-08-06。本檔保留完整技術史、改造細節與回退資訊，不另維護 review 提醒或第二套 active 判準。
2. **上限何時降**：見上方「降上限的判準」。
3. **層 1 SOFT 誤報率**：`S2 同 repo 數字衝突` 目前對「同一 repo 在表格與 li 帶不同量級數字」會報，但正常情況下 stars 與 Trendshift ▲ 本來就不同量級。連幾天觀察誤報頻率，> 5 筆/天就收緊判準或降級為純資訊。

---

## 六、過程中修掉的自造 bug（都是「假綠」形狀）

寫測試框架時撞了三次同一個形狀——**印綠燈但斷言沒執行**：

1. fixture 建置失敗（pattern 過期）沒計入 fail → 13 pass / 0 fail 卻有 2 件從未跑過
2. `report()` 收到空 rows 時只印標題不計 fail → 條件運算優先序寫錯（`[] if x is None else [...] or [(...)]`）
3. detector 從檔名推年份失敗時吐 20 筆垃圾 SOFT 警告掩蓋真訊號

三處的原因都寫進了各自檔案的註解。這正是 dispatch-and-verify 紀律裡「繞過驗證而不是通過驗證」的形狀，只是出現在自己寫的測試框架裡。
