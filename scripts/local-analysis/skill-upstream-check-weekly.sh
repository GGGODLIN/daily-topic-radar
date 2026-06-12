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
    } >> "$LOG_FILE"
  fi
done

# --- skill-creator 上游巡邏（2026-06-12 加）---
# 本地 skill-creator 是官方舊版 fork（出處注記在其 SKILL.md 頭），刻意不同步；
# 這段只巡邏 anthropics/skills 上游有沒有新 commit，surface 給使用者決定，絕不 auto-pull。
SC_MARKER="$LOG_DIR/.skill-creator-upstream-seen"
sc_latest=$(gh api "repos/anthropics/skills/commits?path=skills/skill-creator&per_page=1" --jq '.[0].sha[:8] + " " + .[0].commit.committer.date[:10] + " " + (.[0].commit.message | split("\n")[0])' 2>/dev/null || echo "")
{
  echo ""
  echo "## skill-creator 上游巡邏（fork 對沖，不同步只通報）"
  if [ -z "$sc_latest" ]; then
    echo "- ⚠️ gh api 查詢失敗，本週跳過"
  else
    sc_seen=$(cat "$SC_MARKER" 2>/dev/null || echo "")
    if [ "$sc_latest" = "$sc_seen" ]; then
      echo "- ✅ 無新 commit（latest: $sc_latest）"
    else
      echo "- 🔔 **上游有新動態**: $sc_latest"
      echo "  - 本地是刻意凍結的 fork（base 0f77e501）——看一眼有沒有值得 cherry-pick 的想法即可，不要整支同步"
      echo "$sc_latest" > "$SC_MARKER"
    fi
  fi
} >> "$LOG_FILE"

{
  echo ""
  echo "---"
  echo "**Summary**: $found_skills 個 git-tracked skill 掃過，$behind_count 個有上游更新"
  echo ""
  echo "Done — $(date -u +%H:%M:%S) UTC"
} >> "$LOG_FILE"

exit 0
