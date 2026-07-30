#!/bin/bash
# Weekly upstream check for git-cloned skills in ~/.claude/skills/
# Read-only: only `git fetch` + compare HEAD vs origin/HEAD, never auto-pull
# Writes report to ~/code/social-info/reports/local-analysis/skill-updates/YYYY-MM-DD.md
#
# Why fetch-only:
# - Some skills have local edits (e.g. deep-research description 中文化) — auto-pull would conflict
# - User decides when to actually pull (script gives the exact command)
# - Matches read-only nature of sibling local-analysis routines
#
# 兩種被巡邏的 skill 形態（2026-07-28 起）：
# 1. git clone 型（帶 .git 目錄）→ fetch + HEAD 比對（下方第一段迴圈）
# 2. 散檔 fork 型（無 .git）→ SKILL.md frontmatter 自帶線索：
#      upstream: <owner/repo> / upstream-path: <path> / upstream-pinned: <sha>
#      選配 upstream-branch:（預設 main）、upstream-status: orphaned（已知上游移除、不巡）
#    upstream-pinned 語意 =「已對賬到此 commit」：只在看過 diff、拍板吸收與否之後推進；
#    不得為了讓報告閉嘴而推進——推進本身是對賬 ritual 的最後一步

set -uo pipefail

SKILLS_DIR="${SKILLS_DIR:-$HOME/.claude/skills}"
LOG_DIR="${LOG_DIR:-$HOME/code/social-info/reports/local-analysis/skill-updates}"
ANALYSIS_DATE="${LOCAL_ANALYSIS_DATE:-$(date +%Y-%m-%d)}"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$ANALYSIS_DATE.md"

{
  echo "# Skill upstream check — $ANALYSIS_DATE"
  echo ""
  echo "掃 \`~/.claude/skills/*/.git\` 看上游有沒有新 commit（read-only，不 auto-pull）"
  echo ""
} > "$LOG_FILE"

found_skills=0
behind_count=0

for skill_git in "$SKILLS_DIR"/*/.git; do
  [ -d "$skill_git" ] || continue
  parent=$(dirname "$skill_git")
  name=$(basename "$parent")
  found_skills=$((found_skills + 1))

  cd "$parent" || continue
  remote_url=$(git remote get-url origin 2>/dev/null || echo "no remote")

  # local-only repo（無 origin）不是巡邏失效——沒有 upstream 可巡，中性跳過不進 ⚠️ 升級段
  # （2026-07-15 me-distill 誤報 fetch failed 修正）
  if [ "$remote_url" = "no remote" ]; then
    echo "- ℹ️ $name — local-only repo（無 origin remote）、無 upstream 可巡，跳過" >> "$LOG_FILE"
    continue
  fi

  if ! git fetch --quiet origin 2>/dev/null; then
    echo "- ⚠️ **$name** — fetch failed ($remote_url)" >> "$LOG_FILE"
    continue
  fi

  local_sha=$(git rev-parse --short HEAD 2>/dev/null)
  remote_sha=$(git rev-parse --short '@{u}' 2>/dev/null || echo "")

  if [ -z "$remote_sha" ]; then
    echo "- ⚠️ **$name** — 無 upstream tracking ($remote_url)" >> "$LOG_FILE"
    continue
  fi

  if [ "$local_sha" = "$remote_sha" ]; then
    echo "- ✅ $name — up to date (\`$local_sha\`)" >> "$LOG_FILE"
  else
    behind=$(git rev-list --count HEAD..'@{u}' 2>/dev/null || echo "?")
    behind_count=$((behind_count + 1))
    {
      echo "- ⬇️ **$name — $behind commits behind**"
      echo "  - Local: \`$local_sha\` / Remote: \`$remote_sha\`"
      echo "  - Remote URL: $remote_url"
      echo "  - 更新指令: \`cd $parent && git pull\`"
      echo "  - 最新 commits:"
      git log --oneline "HEAD..@{u}" 2>/dev/null | head -5 | sed 's/^/    - /'
      # capability delta（2026-07-11 起、SkilLock 借鑑）：審更新看能力面新增，不用讀全文 diff
      cap_delta=$(git diff HEAD..'@{u}' 2>/dev/null | grep '^+' | grep -oE "https?://[^ )\"']+|curl |wget |npx |sudo |rm -rf|chmod " | sort | uniq -c | sort -rn | head -8)
      if [ -n "$cap_delta" ]; then
        echo "  - ⚠️ capability delta（更新新增的 network / exec 面，pull 前過目）:"
        echo "$cap_delta" | sed 's/^/    - /'
      fi
    } >> "$LOG_FILE"
  fi
done

# --- 散檔 fork frontmatter 對賬巡邏（2026-07-28 加）---
# 散檔複製型 fork 無 .git、上面迴圈照不到；線索由 SKILL.md frontmatter 自帶（欄位語意見檔頭）。
# 與 marker 型段落（下方 skill-creator / debugging）不同：behind 會每週持續報，直到對賬推進 pinned。
# 已知限制：compare API 的 files 上限 300 檔，超大 delta 可能漏列 path（本用例 delta 極小、可接受）。
fm_watched=0
fm_behind=0
{
  echo ""
  echo "## 散檔 fork 對賬巡邏（frontmatter 線索、pinned 語意）"
} >> "$LOG_FILE"
if ! command -v gh >/dev/null 2>&1 || ! gh auth status >/dev/null 2>&1; then
  echo "- ⚠️ gh 不可用或未登入，散檔 fork 對賬整段跳過（勿當全綠）" >> "$LOG_FILE"
else
  for sk_md in "$SKILLS_DIR"/*/SKILL.md; do
    [ -f "$sk_md" ] || continue
    sk_name=$(basename "$(dirname "$sk_md")")
    fm=$(awk '/^---[[:space:]]*$/{c++; next} c==1{print} c>=2{exit}' "$sk_md")
    up_repo=$(printf '%s\n' "$fm" | sed -n 's/^upstream:[[:space:]]*//p' | head -1)
    [ -n "$up_repo" ] || continue
    fm_watched=$((fm_watched + 1))
    up_status=$(printf '%s\n' "$fm" | sed -n 's/^upstream-status:[[:space:]]*//p' | head -1)
    if [ "$up_status" = "orphaned" ]; then
      echo "- ℹ️ ${sk_name} — 已知 orphaned（上游已移除、不巡；provenance 留檔用）" >> "$LOG_FILE"
      continue
    fi
    up_path=$(printf '%s\n' "$fm" | sed -n 's/^upstream-path:[[:space:]]*//p' | head -1)
    up_pin=$(printf '%s\n' "$fm" | sed -n 's/^upstream-pinned:[[:space:]]*//p' | head -1)
    up_branch=$(printf '%s\n' "$fm" | sed -n 's/^upstream-branch:[[:space:]]*//p' | head -1)
    up_branch="${up_branch:-main}"
    if [ -z "$up_path" ] || [ -z "$up_pin" ]; then
      echo "- ⚠️ **${sk_name}** — frontmatter 線索不完整（缺 upstream-path 或 upstream-pinned）" >> "$LOG_FILE"
      fm_behind=$((fm_behind + 1))
      continue
    fi
    contents_error=$(mktemp)
    if ! gh api "repos/$up_repo/contents/$up_path?ref=$up_branch" --jq .sha >/dev/null 2> "$contents_error"; then
      if grep -q 'HTTP 404' "$contents_error"; then
        echo "- 🪦 **${sk_name}** — 上游 path 消失（${up_repo}@${up_branch}:${up_path}）：疑似 orphaned 或改名，人工確認後改 frontmatter（確認移除 → 補 upstream-status: orphaned）" >> "$LOG_FILE"
      else
        echo "- ⚠️ **${sk_name}** — upstream API 查詢失敗（非 404），本輪無法判定 path 是否存在" >> "$LOG_FILE"
      fi
      rm -f "$contents_error"
      fm_behind=$((fm_behind + 1))
      continue
    fi
    rm -f "$contents_error"
    cmp_files=$(gh api "repos/$up_repo/compare/$up_pin...$up_branch" --jq '.files[].filename' 2>/dev/null) || cmp_files="__CMP_FAIL__"
    if [ "$cmp_files" = "__CMP_FAIL__" ]; then
      echo "- ⚠️ **${sk_name}** — compare 失敗（pinned \`${up_pin}\` 可能已不在上游、或 API 失敗），人工確認" >> "$LOG_FILE"
      fm_behind=$((fm_behind + 1))
    elif printf '%s\n' "$cmp_files" | grep -Fxq "$up_path"; then
      echo "- ⬆️ **${sk_name}** — 上游有未對賬變更（只看 \`${up_path}\`；對賬後推進 upstream-pinned）: https://github.com/${up_repo}/compare/${up_pin}...${up_branch}" >> "$LOG_FILE"
      fm_behind=$((fm_behind + 1))
    else
      echo "- ✅ ${sk_name} — 對賬點後上游未動該檔（pinned \`${up_pin}\`）" >> "$LOG_FILE"
    fi
  done
  if [ "$fm_watched" -eq 0 ]; then
    echo "- ⚠️ 掃到 0 支帶 upstream 線索的 skill——散檔 fork 全部脫離雷達，確認 frontmatter 欄位還在" >> "$LOG_FILE"
  fi
fi

# --- skill-creator 上游巡邏（2026-06-12 加）---
# 本地 skill-creator 是官方舊版 fork（出處注記在其 SKILL.md 頭），刻意不同步；
# 這段只巡邏 anthropics/skills 上游有沒有新 commit，surface 給使用者決定，絕不 auto-pull。
SC_MARKER="$LOG_DIR/.skill-creator-upstream-seen"
sc_latest=$(gh api "repos/anthropics/skills/commits?path=skills/skill-creator&per_page=1" --jq '.[0].sha[:8] + " " + .[0].commit.committer.date[:10] + " " + (.[0].commit.message | split("\n")[0])' 2>/dev/null) || sc_latest=""
{
  echo ""
  echo "## skill-creator 上游巡邏（fork 對沖，不同步只通報）"
  if [ -z "${sc_latest:-}" ]; then
    echo "- ⚠️ gh api 查詢失敗，本週跳過"
  else
    sc_seen=$(cat "$SC_MARKER" 2>/dev/null || echo "")
    if [ "$sc_latest" = "$sc_seen" ]; then
      echo "- ✅ 無新 commit（latest: ${sc_latest}）"
    else
      echo "- 🔔 **上游有新動態**: $sc_latest"
      echo "  - 本地是刻意凍結的 fork（base 0f77e501）——看一眼有沒有值得 cherry-pick 的想法即可，不要整支同步"
      echo "$sc_latest" > "$SC_MARKER"
    fi
  fi
} >> "$LOG_FILE"

# --- systematic-debugging 上游巡邏（2026-07-09 加）---
# 本地 debugging（fork 自 systematic-debugging）是 obra/superpowers 6.1.1 fork（差異 = Phase 3 多假設，注記在 SKILL.md 頭）；
# 這段只巡邏上游 skills/systematic-debugging 新 commit 讓 fork 決定要不要吸收（PreToolUse hook 擋 upstream 挑取、見 ~/.claude/hooks/superpowers-systematic-debugging-shadow.sh）。
SD_MARKER="$LOG_DIR/.systematic-debugging-upstream-seen"
sd_latest=$(gh api "repos/obra/superpowers/commits?path=skills/systematic-debugging&per_page=1" --jq '.[0] | .sha[:8] + " " + .commit.committer.date[:10] + " " + (.commit.message | split("\n")[0])' 2>/dev/null) || sd_latest=""
{
  echo ""
  echo "## debugging 上游巡邏（fork 對沖，不同步只通報）"
  if [ -z "${sd_latest:-}" ]; then
    echo "- ⚠️ gh api 查詢失敗，本週跳過"
  else
    sd_seen=$(cat "$SD_MARKER" 2>/dev/null || echo "")
    if [ "$sd_latest" = "$sd_seen" ]; then
      echo "- ✅ 無新 commit（latest: ${sd_latest}）"
    else
      echo "- 🔔 **上游有新動態**: $sd_latest"
      echo "  - 本地 fork base = superpowers 6.1.1，Phase 3 改多假設——看新 commit 有沒有值得吸收的想法"
      echo "$sd_latest" > "$SD_MARKER"
    fi
  fi
} >> "$LOG_FILE"

# --- CC 新功能雷達巡邏（2026-07-11 加、awesome-claude-code absorb #28）---
# shanraisshan/claude-code-best-practice README 的「🔥 Hot」表 = 活維護的 CC beta/新功能雷達
# （每項附官方 doc + 出處），只通報新 commit、內容判讀交給使用者。
HOT_MARKER="$LOG_DIR/.cc-hot-radar-seen"
hot_latest=$(gh api "repos/shanraisshan/claude-code-best-practice/commits?path=README.md&per_page=1" --jq '.[0] | .sha[:8] + " " + .commit.committer.date[:10] + " " + (.commit.message | split("\n")[0])' 2>/dev/null) || hot_latest=""
{
  echo ""
  echo "## CC 新功能雷達巡邏（claude-code-best-practice 🔥 Hot 表）"
  if [ -z "${hot_latest:-}" ]; then
    echo "- ⚠️ gh api 查詢失敗，本週跳過"
  else
    hot_seen=$(cat "$HOT_MARKER" 2>/dev/null || echo "")
    if [ "$hot_latest" = "$hot_seen" ]; then
      echo "- ✅ 無更新（latest: ${hot_latest}）"
    else
      echo "- 🔔 **Hot 表有更新**: $hot_latest"
      echo "  - 看 https://github.com/shanraisshan/claude-code-best-practice 的 🔥 Hot 表有沒有新 CC 功能值得跟進"
      echo "$hot_latest" > "$HOT_MARKER"
    fi
  fi
} >> "$LOG_FILE"

# --- ⚠️ 升級段（2026-07-10 加）---
# 巡邏失敗曾連續 5 週每週有印 ⚠️ 但被 digest 濃縮掉沒進注意力；
# ⚠️ = 巡邏機制本身失效（非「本週沒新東西」），首次出現即升級、digest 端有對應保留規則。
warn_lines=$(grep '⚠️' "$LOG_FILE" || true)
if [ -n "$warn_lines" ]; then
  {
    echo ""
    echo "## 🚨 巡邏警告升級（digest 必須原文轉載本段、不得濃縮省略）"
    echo ""
    echo "$warn_lines"
    echo ""
    echo "（⚠️ 代表巡邏機制本身失效——出現一次就要處理，不要當背景雜訊）"
  } >> "$LOG_FILE"
fi

{
  echo ""
  echo "---"
  echo "**Summary**: ${found_skills} 個 git-tracked skill 掃過（${behind_count} 個落後）；${fm_watched} 支散檔 fork 對賬巡過（${fm_behind} 支待對賬 / 待確認）"
  echo ""
  echo "Done — $(date -u +%H:%M:%S) UTC"
} >> "$LOG_FILE"

exit 0
