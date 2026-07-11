#!/bin/bash
# distill-weekly.sh — 流程固化偵察 channel（weekly-tue）。
# 借鑑 MiMoCode /distill（2026-06-12 評估）：解固化的「發現瓶頸」——
# 使用者沒意識到的重複工作流永遠不會被固化。機器記帳「重複了什麼」，
# 使用者裁決「值不值得固化」。本 channel 永不自建資產，零候選是合法結果。
set -euo pipefail
cd /

PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:/Users/linhancheng/.local/bin"
export PATH

CLAUDE="/Users/linhancheng/.local/bin/claude"
REPO_DIR="/Users/linhancheng/code/social-info"
OUT_DIR="$REPO_DIR/reports/local-analysis"
LOG_DIR="$REPO_DIR/logs"

mkdir -p "$OUT_DIR" "$LOG_DIR"
DATE=$(date +%Y-%m-%d)
OUT="$OUT_DIR/$DATE-distill.md"
LOG="$LOG_DIR/local-analysis-distill-$DATE.log"

cd "$REPO_DIR"

PROMPT=$(cat <<'EOF'
你是流程固化偵察員。從 session 指紋 ledger 做三件事：(A) 找「重複出現但尚未固化」的工作流 (B) 對已固化的高頻流程做執行漂移抽查 (C) 從錯誤指紋 digest 找反覆絆倒 agent 的同型錯誤。產出繁中報告到 stdout。只偵察、只建議——**絕不自建或自改任何 skill / command / workflow**，固化與修改由使用者拍板。

## Step 1 — 更新 ledger + 錯誤 digest（確定性，照跑不要改）

```bash
bash /Users/linhancheng/code/social-info/scripts/local-analysis/distill-extract.sh
bash /Users/linhancheng/code/social-info/scripts/local-analysis/distill-errors.sh
```

## Step 2 — 讀完整 ledger

`/Users/linhancheng/code/social-info/reports/local-analysis/distill-candidates.jsonl`
每行 `{date, session, intent, seq}`——intent 是該 session 首個 user prompt（截 120 字）、seq 是工具序列指紋（`|` 分隔、相鄰去重、截 80 步）。讀全部歷史不是只讀本週（跨週重複才是重點）。行數多時先用 jq 抽 `{date,intent}` 總覽再 drill 可疑群的 seq。

## Step 3 — 語意聚類

判斷哪些 session 是「同一個工作流」：看 intent 的語意相似 + seq 的結構相似（同類工具序列形狀）。這是本 channel 唯一需要判斷力的步驟——jq 給的是原料，「這幾次算不算同一件事」由你判。寧可漏不可硬湊。

## Step 4 — 跨日門檻

每群數「不同 date 數」：**≥3 個不同日**才算候選。同一天跑五次不算（可能是單次任務的迭代），跨三天以上重複才是「會一直回來的工作流」。

## Step 5 — 既有資產對照（先盤點再提案）

```bash
ls ~/.claude/skills/ ~/.claude/commands/ ~/.claude/workflows/
```
加上 plugin skills（`ls ~/.claude/plugins/cache/*/*/ 2>/dev/null | head -30` 概覽即可）。候選若已被既有資產覆蓋 → 標「已覆蓋，skip」或「可 extend <既有資產>」，不提新建。

## Step 6 — 漂移抽查（B 線：已固化流程 vs 實際執行）

對 Step 5 標「已覆蓋」的群中**頻率最高的 2-3 個**（revealed-consumption 排序，不要全掃）：

1. 從 ledger filter 出 seq 含對應 `Skill:<名>` 或該流程特徵序列的 session
2. 讀該 skill / command / workflow 的本體，列出它 prescribe 的關鍵步驟（3-6 個錨點即可，不用全部）
3. 比對實際 seq，找四類漂移：
   - **跳步**：skill 寫了但軌跡常缺的步驟（附「N/M 個 session 缺此步」計數）
   - **加步**：軌跡反覆出現但 skill 沒寫的步驟（skill 落後於實際做法）
   - **重複手寫**：同一支 skill 的多次執行裡反覆出現同形狀的現場 code（seq 看到重複的 `Bash:jq` / `Bash:python3` 類指紋 → drill jsonl 比對完整指令確認同構）→ 提案「這段收進該 skill scripts/ 成固定檔」
   - 必要時抽 1-2 個 session 的 jsonl 看「skill 跑完使用者糾正」事件
4. 漂移發現 = 修改提案（改哪支、哪段、為什麼），跟 A 線候選一起進報告。提案限**最小 bounded edit**（一次一個錨點），下週才歸因得出來是哪個改動起作用。**絕不直接改 skill**——自動改寫固化資產是使用者明確否決的軸
5. **修後對數字**：上週報告有 B 線修改提案的資產 → 先查提案有沒有被落實（讀 skill 本體比對提案內容）。落實了 → 本週必附同一漂移指標 vs 上週的對照（降了沒）；未落實 → 一行「提案未落實、第 N 週」，讓 pending 狀態進報告不沉默消失
6. 無漂移 → 一行「抽查 X/Y/Z 無顯著漂移」帶過

完成後接 Step 6.5。

## Step 6.5 — 錯誤指紋（C 線：反覆絆倒 agent 的同型錯誤）

digest 在 `/Users/linhancheng/code/social-info/reports/local-analysis/distill-errors.txt`（Step 1 已更新；每段 `=== session: <路徑> (errors: N)` + 逐筆 `[時間] [error] <摘要>`）。檔案可能 >100KB，**不要整檔讀**：

1. 先做頻次總覽：`grep '\[error\]' <digest> | sed 's/^\[[^]]*\] //' | cut -c1-60 | sort | uniq -c | sort -rn | head -20`
2. 對 top 群判「同型」：同一種錯誤訊息形狀 = 同型（例：「File has not been read yet」「String to replace not found」各是一型）
3. 雜訊過濾：平行呼叫連帶取消（`Cancelled: parallel tool call`）、一次性環境問題（網路抖動 / 單日 API 錯）不算訊號
4. 跨日門檻同 Step 4：同型錯誤 **≥3 個不同日**出現才算候選；用 digest 內時間戳數不同日
5. 候選 = 修正提案訊號：drill 該型錯誤所在 session 的 `=== session:` 標頭看是哪類工作流反覆產生它，提案指向對應資產（某支 skill 缺步驟 / 某 hook 該擋 / 某流程該補 guard）。提案紀律同 Step 6 第 4 點（最小 bounded edit、絕不直接改）
6. 零候選 → 一行「C 線無跨日同型錯誤」帶過

完成後接 Step 7。

## Step 7 — 輸出報告

A 線每條候選：工作流一句話描述 + 證據（哪幾天、幾個 session、intent 樣本）+ 建議形態（skill / command / workflow / 「extend 既有 X」）+ 一句為什麼值得固化 + **可機檢性**：這個流程的成敗能自動打分嗎？能 → 附一句「固化前可拿過去 N 個 session 當考題驗證」的具體驗法；不能 → 標「靠人工判斷」即可，不硬湊。
B 線每條漂移：哪支資產 + 漂移類型（跳步/加步/糾正）+ 計數證據 + 修改提案。
C 線每條候選：錯誤同型描述 + 頻次與跨日計數 + 來源工作流 + 修正提案指向的資產。
**A 線零候選、B 線無漂移、C 線無同型錯誤 → 各明寫一行帶過，不要硬湊。**
EOF
)

"$CLAUDE" -p "$PROMPT" > "$OUT" 2>> "$LOG"
echo "distill report → $OUT"
