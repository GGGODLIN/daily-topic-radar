#!/bin/bash
# Fixture regression for the frontmatter 對賬 leg of skill-upstream-check-weekly.sh
# 四件：known-good（該報 ✅）/ known-bad（該報 ⬆️）/ 邊界 path-404（該報 🪦）/ 邊界 known-orphan（該報 ℹ️）
# pinned 值執行時從上游現值計算，known-good 不會隨上游演進而腐壞
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH_REPO="mattpocock/skills"
WATCH_PATH="skills/engineering/diagnosing-bugs/SKILL.md"
REAL_GH="$(command -v gh)"

latest=$(gh api "repos/$WATCH_REPO/commits?path=$WATCH_PATH&per_page=1" --jq '.[0].sha')
latest_parent=$(gh api "repos/$WATCH_REPO/commits/$latest" --jq '.parents[0].sha')
if [ -z "$latest" ] || [ -z "$latest_parent" ]; then
  echo "SETUP FAIL: 無法取得上游 SHA（gh 未登入？）"
  exit 1
fi

FIX=$(mktemp -d)
trap 'rm -rf "$FIX"' EXIT
mkdir -p "$FIX/skills" "$FIX/logs"

mk() {
  mkdir -p "$FIX/skills/$1"
  printf -- '---\nname: %s\n%s\n---\nbody\n' "$1" "$2" > "$FIX/skills/$1/SKILL.md"
}
mk fx-good "upstream: $WATCH_REPO
upstream-path: $WATCH_PATH
upstream-pinned: ${latest:0:7}"
mk fx-behind "upstream: $WATCH_REPO
upstream-path: $WATCH_PATH
upstream-pinned: ${latest_parent:0:7}"
mk fx-orphan-path "upstream: $WATCH_REPO
upstream-path: skills/engineering/does-not-exist/SKILL.md
upstream-pinned: ${latest:0:7}"
mk fx-known-orphan "upstream: $WATCH_REPO
upstream-status: orphaned"
mk fx-api-error "upstream: $WATCH_REPO
upstream-path: skills/engineering/api-flap/SKILL.md
upstream-pinned: ${latest:0:7}"
mk fx-incomplete "upstream: $WATCH_REPO"

mkdir -p "$FIX/bin"
cat > "$FIX/bin/gh" <<EOF
#!/bin/bash
if [[ "\$*" == *api-flap* ]]; then
  printf 'gh: service unavailable (HTTP 500)\n' >&2
  exit 1
fi
exec "$REAL_GH" "\$@"
EOF
chmod +x "$FIX/bin/gh"

PATH="$FIX/bin:$PATH" SKILLS_DIR="$FIX/skills" LOG_DIR="$FIX/logs" LOCAL_ANALYSIS_DATE="2026-01-02" bash "$SCRIPT_DIR/skill-upstream-check-weekly.sh"
report="$FIX/logs/2026-01-02.md"

pass=0; fail=0
check() {
  if grep -qE "$2" "$report"; then
    echo "PASS: $1"; pass=$((pass+1))
  else
    echo "FAIL: $1 — 預期 pattern 未出現: $2"; fail=$((fail+1))
  fi
}
check "known-good 報 ✅"        "✅ fx-good"
check "known-bad 報 ⬆️"         "⬆️ \*\*fx-behind\*\*"
check "path-404 報 🪦"          "🪦 \*\*fx-orphan-path\*\*"
check "known-orphan 報 ℹ️ 跳過" "ℹ️ fx-known-orphan"
check "非 404 API 錯誤不冒充 path 消失" "⚠️ \*\*fx-api-error\*\* — upstream API 查詢失敗（非 404）"
check "線索不完整列待確認" "⚠️ \*\*fx-incomplete\*\* — frontmatter 線索不完整"
check "summary 計數 6 支 / 4 待確認" "6 支散檔 fork 對賬巡過（4 支待對賬 / 待確認）"

echo "----"
echo "${pass} PASS / ${fail} FAIL（report: ${report}）"
[ "$fail" -eq 0 ]
