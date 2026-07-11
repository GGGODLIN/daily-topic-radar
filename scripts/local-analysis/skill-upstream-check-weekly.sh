#!/bin/bash
# Weekly upstream check for git-cloned skills in ~/.claude/skills/
# Read-only: only `git fetch` + compare HEAD vs origin/HEAD, never auto-pull
# Writes report to ~/code/social-info/reports/local-analysis/skill-updates/YYYY-MM-DD.md
#
# Why fetch-only:
# - Some skills have local edits (e.g. deep-research description 中文化) — auto-pull would conflict
# - User decides when to actually pull (script gives the exact command)
# - Matches read-only nature of sibling local-analysis routines

set -uo pipefail

LOG_DIR="$HOME/code/social-info/reports/local-analysis/skill-updates"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date -u +%Y-%m-%d).md"

{
  echo "# Skill upstream check — $(date -u +%Y-%m-%d)"
  echo ""
  echo "掃 \`~/.claude/skills/*/.git\` 看上游有沒有新 commit（read-only，不 auto-pull）"
  echo ""
} > "$LOG_FILE"

found_skills=0
behind_count=0

for skill_git in "$HOME"/.claude/skills/*/.git; do
  [ -d "$skill_git" ] || continue
  parent=$(dirname "$skill_git")
  name=$(basename "$parent")
  found_skills=$((found_skills + 1))

  cd "$parent" || continue
  remote_url=$(git remote get-url origin 2>/dev/null || echo "no remote")

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
  echo "**Summary**: $found_skills 個 git-tracked skill 掃過，$behind_count 個有上游更新"
  echo ""
  echo "Done — $(date -u +%H:%M:%S) UTC"
} >> "$LOG_FILE"

exit 0
