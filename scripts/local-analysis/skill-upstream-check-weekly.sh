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

{
  echo ""
  echo "---"
  echo "**Summary**: $found_skills 個 git-tracked skill 掃過，$behind_count 個有上游更新"
  echo ""
  echo "Done — $(date -u +%H:%M:%S) UTC"
} >> "$LOG_FILE"

exit 0
