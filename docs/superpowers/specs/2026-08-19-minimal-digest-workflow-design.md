# 極簡版 daily-topic-analysis — 第四支 workflow 變體

**Date**: 2026-08-19
**Status**: Design v1 (pending user review)
**Driver**: 成本（a）＋額度撐不住時仍要有 digest（c）——使用者 2026-08-19 拍板

**Affected paths**:
- `~/.claude/workflows/daily-topic-analysis-minimal.js`（新增）
- `~/.claude/hooks/daily-topic-analysis-trigger.sh`（加分派分支）
- `~/.claude/hooks/daily-topic-analysis-trigger.test.sh`（補斷言）
- `~/.claude/scripts/verify-digest-mechanical.py`（修 H4 既有 bug）
- `~/.claude/workflows/tests/daily-topic-vendor.test.mjs`（確認不誤抓新檔）
- `~/.claude/commands/daily-topic.md`（改成指向 hook、不列版本清單）
- [CLAUDE.md](/CLAUDE.md)（同上）
- [.claude/hooks/digest-write-verify.sh](/.claude/hooks/digest-write-verify.sh)（修硬編的 lens 數敘述）

---

## TL;DR

在既有三支 daily-topic-analysis 變體之外新增第四支「極簡版」，**agent 數 30.3 → 8.0（−74%）、每跑成本 $30.93 → $14.45（−53%）**，digest 內容量不變。

砍的是驗證層與空轉支路：整個 FactCheck phase（14.3 個 agent，產出從未進 digest）、Verify B（4 個 agent，60 天零命中）、external-feeds（49 天只貢獻 2 則真新）、DigestAudit 從 4 個 lens 減為 2 個並降 sonnet。matt-videos 改條件派工（90% 的日子不派 agent）。

**極簡版不需要 vendor 變體**——vendor 版與預設版的全部差異都在 chrome fact-check 那一段，砍掉 FactCheck 之後兩者同構。所以是四支檔不是五支。

---

## Motivation

### 成本結構

2026-08-19 對 2026-08-10 之後 7 次乾淨 run 的 jsonl 逐筆量測（以 `message.id` 去重，不去重會虛報 cache_write 2.62 倍）：

| Phase | agent | 成本 | 佔比 |
|---|---|---|---|
| DigestAudit | 4.0 | $15.57 | 50.3% |
| WriteDigest | 1.0 | $6.65 | 21.5% |
| FactCheck | 14.3 | $3.13 | 10.1% |
| Themes | 1.1 | $1.95 | 6.3% |
| PreFlight | 4.0 | $1.67 | 5.4% |
| Verify | 4.0 | $0.92 | 3.0% |
| WriteBack | 0.9 | $0.71 | 2.3% |
| KnownIssuesGate | 1.0 | $0.33 | 1.1% |
| **合計** | **30.3** | **$30.93** | |

金額是公開牌價換算的估計（Opus $5/$25、Sonnet $3/$15、Haiku $1/$5、cache write 1.25x、cache read 0.10x），未與任何帳單對帳。用途是排序與相對權重，不是支出。另有 main session 側約 $21/跑（上限值，未拆出純 digest 工作）。

### 成本模型：agent 數是唯一有效槓桿

全部 33 個 agent 的 prompt 字元總和 74,451、schema 總和 36,162，而 workflow 檔頭記錄的實測是「per-URL 平均 80K input、其中 ~78K 是每 agent 重複載入的固定開銷」。兩者相符：**單一 agent 的成本 ≈ 78K 固定開銷 + 自身 tool 輸出**。

推論：縮 prompt 幾乎不省錢，砍 agent 數才有效。每跑 50.5% 的 fresh input 花在「agent 開機」上（$5.74/跑、18.5%）。

### 產了沒人用的東西

- **FactCheck**：抓回來的 `snippet` / `gh_stars` / `gh_description` / `gh_latest_release` 一個字都沒進 WriteDigest 的 prompt，只進了 4 個計數。唯一的 URL 真實性 gate `verify-daily-digest.sh` 是純 grep（digest 每個 href 必須在 raw md 找得到），完全不讀 fact_check。DigestAudit 的四個 lens 的輸入也不含 fact_check。
- **github 分支是純重工**：FactCheck 抓一次 gh metadata，WriteDigest 的 prompt 又叫 writer 自己跑 `gh repo view` 補（因為 raw md 沒有 star 數），DigestAudit lens F 再查一次。同一批 repo 一天被 `gh` 打三輪，只有第二輪的結果真的進 digest。
- **Verify B**：2026-06-18 至 2026-08-17 每一份 digest 的 `parallel_b_misaligned` 全部是 0。唯一非零出現在 2026-08-18（4 抽 1），但 payload 只有計數沒有逐條明細，digest 原文自己寫「無法定位是哪一則，記錄但不推測」。程式上不降級 `ok`，對 pipeline 零副作用。
- **external-feeds**：2026-07-01 至 08-18 共 49 天、digest 唯一 URL 4,895 個，其中「raw md 沒有、只有 feed 有」的只有 8 個；8 個裡 6 個是同 3 篇 Anthropic 舊文重複回傳。真正新的只有 2 則推文。對照 2026-06-05 試讀結案時的 14%，現在是 4%。
- **matt-videos**：2026-07-21 至 08-19 共 30 天，`rss:youtube_matt` 條目只出現在 3 天。其餘 27 天該 agent 的產出就是空陣列。但命中日產值極高（2026-08-04 的 🎓 段佔當天 digest 62%、08-06 佔 31%），所以是條件派工不是砍。

---

## Design

### Pipeline

| 順序 | Phase | agent | 做什麼 | 原模型 | 極簡版模型 |
|---|---|---|---|---|---|
| 1 | KnownIssuesGate | `known_issues_gate` ×1 | 讀 KNOWN_ISSUES.md 的 🚨 條目，對可重試失敗自動跑 `--retry-failures`（≤2 次、90s 預算）；量 raw md 大小與段落數餵 Guard A0/A/C | sonnet | sonnet |
| 2 | PreFlight | `watch` ×1 | 讀 WATCH.md 追蹤清單，在 raw md 找命中 | sonnet | sonnet |
| | | `probes` ×1 | 主動抓 PROBES.md 列的外部來源，比對 baseline 找新訊號 | sonnet | sonnet |
| | | `matt_videos` ×0.1 | **先 grep raw md 的 `rss:youtube_matt`，0 筆就不派 agent**；有才派 | opus + medium | **sonnet** |
| | | ~~`external_feeds`~~ | ~~抓 follow-builders feed 當 backstop~~ | haiku | **砍** |
| 3 | Themes | `themes` ×1 | 掃 raw md 抽主軸（title / summary / related_items_count / key_urls）。**不再做 URL 分類** | opus + xhigh | opus + xhigh |
| 4 | ~~FactCheck~~ | ~~chrome / fetch-batch~~ | ~~逐 URL 抓真實性~~ | haiku / sonnet | **整段砍** |
| 5 | Verify | 無 agent | Verify A（純 JS 硬斷言、零 token）保留；Verify B 砍 | sonnet ×4 | **無 LLM** |
| 6 | WriteBack | `writeback-{n}` ×0.9 | probes 有新 baseline 時寫回 PROBES.md 的 Last seen | sonnet | sonnet |
| 7 | WriteDigest | `write-digest` ×1 | 讀 raw md + payload，寫 digest HTML | sonnet | sonnet |
| 8 | DigestAudit | `audit:A` ×1、`audit:D` ×1 | 兩個零 prior lens 並行二審 | opus + xhigh ×4 | **sonnet ×2** |

### 模型配比

| 檔位 | 現況 lean | 極簡版 |
|---|---|---|
| opus + xhigh | 5（themes 1 + audit 4） | 1（themes） |
| opus + medium | 1（matt_videos，每天） | 0 |
| sonnet | 17 | 7.0 |
| haiku | 10 | 0 |
| **總計** | **30.3** | **8.0** |

`themes` 保 opus + xhigh 的理由：它抽的主軸是 digest 的骨架，是唯一一刀會動到內容品質的地方。降 sonnet 只省 $0.78（佔極簡版 5%），風險最大報酬最小。

### DigestAudit 保留 A 與 D、砍 F 與 E

| lens | 職責 | 成本（opus） | 去留 | 理由 |
|---|---|---|---|---|
| F 機械對位 | 數字、計數、引用逐條對位 | $5.05 | **砍** | 最貴，且它自己的 prompt 就寫著數字類確定性檢查已由 `verify-digest-mechanical.py` 機械重算、不需重做 |
| A 來源限定詞 | 來源的限定條件有沒有在轉述時掉失 | $3.70 | **留** | 2026-08-10 補回的兩個之一 |
| D 紀律對位 | 對照使用者既有紀律與偏好，抓捏造 | $3.07 | **留** | 同上；2026-08-10 抓到「捏造使用者偏好撐起最高優先序建議」 |
| E 跨天沿用重估 | 前幾天沿用的敘述今天是否仍成立 | $3.75 | **砍** | 2026-08-10 只跑 F+E 的組合實測不足 |

2026-08-10 的前例：只跑 F+E 的第 1 輪抓到 6 個 material，main 補派兩輪覆蓋 A/B/C/D 又抓出 15 個。A 與 D 是當天使用者拍板加回的命中率最高的兩軸。

### 極簡版的 audit 只跑 1 輪

hook 現行規則是「workflow 的 DigestAudit 是第 1 輪，main 最多再派 2 輪，本輪 material ≥ 1 就派下一輪（subagent 用 `routed-judge`）」。

**極簡版分支必須覆寫這條**：修 findings，但不 re-audit。

理由：極簡版第 1 輪只有 2 個 sonnet lens，抓得比 4 個 opus lens 少是預期內的。若沿用續輪規則，漏掉的錯會在第 2、3 輪由 opus + xhigh 抓到——省下的成本從 main session 原封不動走回來，「極簡」只是把帳搬家。

### 極簡版不需要 vendor 變體

vendor 版與預設版的 49 行差異全部落在：檔頭註解、`meta.name` / `description`、`workflowName`、`FACT_CHECK_CHROME_SCHEMA` 區塊、`chromeFactCheck` 執行段、一行 chrome_required 敘述。

極簡版砍掉整個 FactCheck 之後這些 code 都不存在，因此極簡版本身就是 vendor 通用的。最終是四支檔：

```
daily-topic-analysis.js          預設版（lean）
daily-topic-analysis-full.js     完整版
daily-topic-analysis-vendor.js   預設版的 vendor 鏡像
daily-topic-analysis-minimal.js  極簡版（vendor 通用）
```

---

## 成本預估

| | 現況 lean | 極簡版 | 差 |
|---|---|---|---|
| agent 數 | 30.3 | 8.0 | −74% |
| 每跑成本 | $30.93 | $14.45 | −53% |
| 牆鐘 | 46–60 分 | 未量測 | |

拆解：`write-digest` $6.65 + `audit A+D`（sonnet）$4.06 + `themes` $1.95 + `writeback` $0.71 + `probes` $0.40 + `known_issues_gate` $0.33 + `watch` $0.30 + `matt_videos` $0.05。

`write-digest` 一支佔 46%。它是唯一真的在產內容的 agent，內容量不變就是地板。

Sonnet 降價換算用 ×0.6（牌價 $3/$15 對 Opus $5/$25）。注意 Sonnet 5 目前有 introductory $2/$10（2026-08-31 到期），到期後同一支 pipeline 會自然變貴約 15%。

---

## 品質代價（已知缺口，不得靜默出貨）

砍掉的驗證換成三道零 token 的機械檢查：

| 防線 | 抓得到 | 抓不到 |
|---|---|---|
| Verify A（workflow 內 JS） | 結構性斷言違規，會把條目降級 `ok=false` | 語意問題 |
| `verify-daily-digest.sh` | digest 每個 href 必須在 raw md 找得到，否則 exit 1 | 連結對但敘述錯 |
| `verify-digest-mechanical.py` | 計數、圖表分項加總、跨天數字序列、指標符號、比例算術 | 因果腦補、跨條目焊接 |

加上 DigestAudit 的 A + D 兩軸（sonnet）。

**明確缺口，回報時必須列出**：

1. F 軸（機械對位）不跑——`verify-digest-mechanical.py` 覆蓋數字類確定性檢查，但引用逐條對位不覆蓋
2. E 軸（跨天沿用重估）不跑——前幾天沿用的敘述今天是否仍成立無人檢查。2026-07-30 加這一軸正是為了補「正常版本間隔期」連四天沒被抓到的盲區
3. B 軸（焊接與內部一致）與 C 軸（基準宣稱）不跑——這兩軸在預設版就已經不跑
4. audit 只跑 1 輪，material 未經第二輪確認

---

## 連帶改動

| 檔案 | 改什麼 | 測試 |
|---|---|---|
| `workflows/daily-topic-analysis-minimal.js` | 新增 | 需一併加 |
| `hooks/daily-topic-analysis-trigger.sh` | 加「極簡版」關鍵字分支，**放在 model id 判斷之前**（與「完整版」同層）；該分支 `lens_desc` 明寫只跑 A/D 兩軸、且不派額外輪次 | 24 條斷言不會紅（不動既有優先序與關鍵字） |
| `hooks/daily-topic-analysis-trigger.test.sh` | 補約 6 條：極簡版無 model → minimal、極簡版 + claude id → minimal、極簡版 + gpt id → minimal、「完整版」與「極簡版」同句的優先序、`lens_desc` 含「只跑 A/D」、`lens_desc` 含「不派額外輪次」 | — |
| `scripts/verify-digest-mechanical.py` | 修 H4：容忍 digest 沒有 `chrome_ok_count` / `parallel_ok_count`。**這是既有 bug**，最近 9 份 digest 有 4 份（08-13/14/15/16）已經沒有這欄位 | 有回歸測試 |
| `workflows/tests/daily-topic-vendor.test.mjs` | 硬編兩個路徑做全檔 byte 等價比對。極簡版無 vendor 對應，只需確認識別正則不誤抓 `-minimal.js` | — |
| `commands/daily-topic.md` | line 8-10 現在寫「兩個版本」，磁碟上已有三支。改成「版本分派由 hook 決定」、不列檔名與 lens 數 | 無測試 |
| [CLAUDE.md](/CLAUDE.md) | line 78 / 79 / 80 / 100 需改；整份 0 次提到 vendor 版。同樣改成指向 hook | 無測試 |
| [.claude/hooks/digest-write-verify.sh](/.claude/hooks/digest-write-verify.sh) | line 84-92 硬編「第 1 輪已由 workflow 跑過 6 個並行專職 lens」並列出 F/A/B/C/D/E。這句對現在的預設版（4 lens）就已錯 | 回歸測試不碰 lens 數，改了不會叫 |

### 文件不再落後的做法

`commands/daily-topic.md` line 25 已宣告「main 側流程以 hook 注入內容為 SSOT」，但 line 8-10 又自己列版本清單——它違反自己，且已漏掉 vendor 版。[CLAUDE.md](/CLAUDE.md) 同樣漏掉。

改法：兩份都刪掉版本列舉，只留一句指向 hook。若要保留清單，配一支測試斷言「hook 內出現的每個 `wf_path` 都在文件內出現」。

---

## Testing

動工前後都要跑：

```bash
bash ~/.claude/hooks/daily-topic-analysis-trigger.test.sh
node ~/.claude/workflows/tests/daily-topic-vendor.test.mjs
node ~/.claude/workflows/tests/daily-topic-guards.test.mjs
node --check ~/.claude/workflows/daily-topic-analysis-minimal.js
bash /Users/linhancheng/code/social-info/.claude/hooks/digest-write-verify.test.sh
```

`daily-topic-guards.test.mjs` 是把 Guard A0/A/B/C 的條件式**手抄**成鏡像常數、不 import workflow 檔。極簡版若動任何 guard 條件，必須成對修改該測試。極簡版預設不動 guard。

零成本的 fan-out 驗證：`measure.mjs`（本次分析產出的 harness，位於 scratchpad）用 stub 注入 `agent` / `parallel` / `phase` / `log` / `args` 五個 runtime global，讓 workflow 在零 LLM 呼叫下跑完並逐個記下 agent 規格。改完 workflow 用它跑前後對照，是驗證「這次改動砍掉幾個 agent」最便宜的證據來源。

---

## Open questions

1. **`watch` agent 的 schema 錯配**（獨立於本 spec、不擋上線）：`WATCH_SURFACE_SCHEMA` 每筆 required 含 `issue_url` 與 `state`（GitHub issue 形狀），但 2026-08-17 新加的「AI 工具限時優惠／名額」entry 兩者都沒有；prompt 寫「讀 `## Active` 段」而檔案實際段名是 `## Active Issues` 與 `## Watched Topics`。使用者 2026-08-19 拍板「先修 schema 再決定去留」。
2. **完整版 × vendor 這一格今天不存在**：hook 的「完整版」判斷在 model id 之前，所以非 Claude session 說「完整版」仍走 full。加極簡版時要不要一併補這格，未拍板。
3. **牆鐘時間未量測**：極簡版砍掉的多是並行段，實際省多少時間要跑一次才知道。

---

## 未經對抗審查

本設計的候選清單來自 2026-08-19 的四路解剖（script 結構 / 歷史 run 實際成本 / 下游消費鏈 / 接線面），三個對抗 lens（品質崩壞 / 歷史翻案 / vendor 可行性）因額度中止未執行。使用者知情並選擇繼續。

具體未覆蓋：沒有逐條去翻最近幾天的 digest 驗證「這條砍了會少哪段實例」；沒有查 git log 找「砍了又加回」的往返；沒有查哪些縮減在 vendor 環境失效。08-10 的教訓是從 workflow script 的註解讀來的，不等於完整查證。
