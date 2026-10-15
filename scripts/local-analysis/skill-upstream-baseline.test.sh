#!/bin/bash
# 聚焦：repo/path 已登記但缺 upstream-pinned 時，報基線待對帳，不拿最新版冒充。
# 不跑既有大型 lock/hash 段。fake python 只把 drift 回空；frontmatter parser 仍是真 python。
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FIX=$(mktemp -d "${TMPDIR:-/tmp}/skill-upstream-baseline.XXXXXX")
trap 'rc=$?; rm -rf "$FIX"; exit $rc' EXIT
mkdir -p "$FIX/skills" "$FIX/logs" "$FIX/bin" "$FIX/vendor" "$FIX/agents-skills"

BEFORE=$'---\nname: fx-unpinned\nmetadata:\n  upstream: owner/repo\n  upstream-path: skills/fx/SKILL.md\n---\nbody-unpinned\n'
write_skill() {
  mkdir -p "$FIX/skills/$1"
  printf '%s' "$2" > "$FIX/skills/$1/SKILL.md"
}
write_skill fx-unpinned "$BEFORE"
write_skill fx-incomplete $'---\nname: fx-incomplete\nmetadata:\n  upstream: owner/repo\n---\nbody-incomplete\n'
write_skill fx-pinned-clean $'---\nname: fx-pinned-clean\nmetadata:\n  upstream: owner/repo\n  upstream-path: skills/clean/SKILL.md\n  upstream-pinned: abc1234\n---\nbody-clean\n'
write_skill fx-pinned-behind $'---\nname: fx-pinned-behind\nmetadata:\n  upstream: owner/repo\n  upstream-path: skills/behind/SKILL.md\n  upstream-pinned: def5678\n---\nbody-behind\n'
write_skill fx-path-404 $'---\nname: fx-path-404\nmetadata:\n  upstream: owner/repo\n  upstream-path: skills/missing/SKILL.md\n  upstream-pinned: aaa1111\n---\nbody-404\n'
write_skill fx-api-error $'---\nname: fx-api-error\nmetadata:\n  upstream: owner/repo\n  upstream-path: skills/flap/SKILL.md\n  upstream-pinned: bbb2222\n---\nbody-api\n'
write_skill fx-known-orphan $'---\nname: fx-known-orphan\nmetadata:\n  upstream: owner/repo\n  upstream-status: orphaned\n---\nbody-orphan\n'

cat > "$FIX/bin/gh" <<'EOF'
#!/bin/bash
printf '%s\n' "$*" >> "$GH_LOG"
if [[ "$*" == "auth status" ]]; then
  exit 0
fi
if [[ "$*" == *"contents/skills/missing/SKILL.md"* ]]; then
  printf 'gh: Not Found (HTTP 404)\n' >&2
  exit 1
fi
if [[ "$*" == *"contents/skills/flap/SKILL.md"* ]]; then
  printf 'gh: service unavailable (HTTP 500)\n' >&2
  exit 1
fi
if [[ "$*" == *"contents/"* ]]; then
  printf '%s\n' "deadbeefcontents"
  exit 0
fi
if [[ "$*" == *"/compare/"* ]]; then
  case "$*" in
    *abc1234*) exit 0 ;;
    *def5678*) printf '%s\n' "skills/behind/SKILL.md"; exit 0 ;;
  esac
  printf 'gh: compare should not run\n' >&2
  exit 1
fi
printf 'gh: unexpected call: %s\n' "$*" >&2
exit 1
EOF
chmod +x "$FIX/bin/gh"

REAL_PYTHON=""
for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3 "$(PATH=$(getconf PATH) command -v python3)"; do
  if [ -x "$candidate" ] && "$candidate" -c 'import yaml' >/dev/null 2>&1; then
    REAL_PYTHON="$candidate"
    break
  fi
done
if [ -z "$REAL_PYTHON" ]; then
  echo "SETUP FAIL: 找不到帶 PyYAML 的 python3，frontmatter parser 跑不起來"
  exit 1
fi
cat > "$FIX/bin/python3" <<EOF
#!/bin/bash
script=""
prev=""
for arg in "\$@"; do
  if [[ "\$prev" != "-c" && "\$arg" == *.py ]]; then
    script="\$arg"
    break
  fi
  prev="\$arg"
done
if [[ "\$script" == *skill-lock-drift.py ]]; then
  exit 0
fi
exec "$REAL_PYTHON" "\$@"
EOF
chmod +x "$FIX/bin/python3"

: > "$FIX/gh-log"
PATH="$FIX/bin:$PATH" \
  GH_LOG="$FIX/gh-log" \
  SKILLS_DIR="$FIX/skills" \
  VENDOR_DIR="$FIX/vendor" \
  AGENTS_SKILLS_DIR="$FIX/agents-skills" \
  SKILL_LOCK_FILE="$FIX/missing-lock.json" \
  LOG_DIR="$FIX/logs" \
  LOCAL_ANALYSIS_DATE="2026-01-03" \
  bash "$SCRIPT_DIR/skill-upstream-check-weekly.sh"
report="$FIX/logs/2026-01-03.md"

pass=0
fail=0
check() {
  if grep -qF -- "$2" "$report"; then
    echo "PASS: $1"
    pass=$((pass + 1))
  else
    echo "FAIL: $1 — 預期字串未出現: $2"
    fail=$((fail + 1))
  fi
}
check_absent() {
  if grep -qF -- "$2" "$report"; then
    echo "FAIL: $1 — 不該出現: $2"
    fail=$((fail + 1))
  else
    echo "PASS: $1"
    pass=$((pass + 1))
  fi
}

check "缺 path 保留原不完整 warning" "- ⚠️ **fx-incomplete** — frontmatter 線索不完整（缺 upstream-path 或 upstream-pinned）"
check "path 有效但 pin 空報基線待對帳" "- ⚠️ **fx-unpinned** — 已登記上游來源；基線待對帳（缺 upstream-pinned），不能判定已安裝版本與上游差異"
check "有 pin 且 compare 未含該 path 報 clean" "- ✅ fx-pinned-clean — 對賬點後上游未動該檔（pinned \`abc1234\`）"
check "有 pin 且 compare 含該 path 報 behind" "- ⬆️ **fx-pinned-behind** — 上游有未對賬變更（只看 \`skills/behind/SKILL.md\`；對賬後推進 upstream-pinned）: https://github.com/owner/repo/compare/def5678...main"
check "contents 404 保留 path 消失" "- 🪦 **fx-path-404** — 上游 path 消失（owner/repo@main:skills/missing/SKILL.md）：疑似 orphaned 或改名，人工確認後改 frontmatter（確認移除 → 補 upstream-status: orphaned）"
check "contents 非 404 保留 API 失敗" "- ⚠️ **fx-api-error** — upstream API 查詢失敗（非 404），本輪無法判定 path 是否存在"
check "known orphaned 分支不變" "- ℹ️ fx-known-orphan — 已知 orphaned（上游已移除、不巡；provenance 留檔用）"
check "summary 把待對帳算進 fm_behind" "7 支散檔 fork 對賬巡過（5 支待對賬 / 待確認）"

check_absent "缺 pin 不報成已對賬" "fx-unpinned — 對賬點後上游未動該檔"
check_absent "缺 pin 不報成上游有未對賬變更" "fx-unpinned** — 上游有未對賬變更"

if grep -q 'compare/' "$FIX/gh-log" && ! grep -q 'compare/abc1234' "$FIX/gh-log"; then
  echo "FAIL: 有 pin 的 clean compare 沒被呼叫"
  fail=$((fail + 1))
elif ! grep -q 'compare/abc1234' "$FIX/gh-log"; then
  echo "FAIL: 有 pin 的 clean compare 沒被呼叫"
  fail=$((fail + 1))
elif ! grep -q 'compare/def5678' "$FIX/gh-log"; then
  echo "FAIL: 有 pin 的 behind compare 沒被呼叫"
  fail=$((fail + 1))
elif grep -E 'compare/($|\.\.\.|HEAD|main\.\.\.)' "$FIX/gh-log" | grep -v -e 'abc1234' -e 'def5678' -q; then
  echo "FAIL: 缺 pin 仍打了 compare API"
  fail=$((fail + 1))
else
  compare_count=$(grep -c '/compare/' "$FIX/gh-log")
  if [ "$compare_count" -eq 2 ]; then
    echo "PASS: 只有已知 pin 打 compare，unknown pin 沒有 compare API"
    pass=$((pass + 1))
  else
    echo "FAIL: compare 次數是 ${compare_count}，預期 2"
    fail=$((fail + 1))
  fi
fi

after=$(cat "$FIX/skills/fx-unpinned/SKILL.md")
if [ "${after%$'\n'}" = "${BEFORE%$'\n'}" ]; then
  echo "PASS: input bytes 未變"
  pass=$((pass + 1))
else
  echo "FAIL: input bytes 被改寫"
  printf 'BEFORE=[%s]\nAFTER=[%s]\n' "$BEFORE" "$after" >&2
  fail=$((fail + 1))
fi

echo "----"
echo "${pass} PASS / ${fail} FAIL（report: ${report}）"
[ "$fail" -eq 0 ]
