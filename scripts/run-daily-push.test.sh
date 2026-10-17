#!/bin/bash
# run-daily.sh PUSH_GUARD 段回歸測試
#
# 本檔在契約測試底下：改 run-daily.sh 的 PUSH_GUARD_START/END 段後必跑
#   bash scripts/run-daily-push.test.sh
#
# 用 stub git 模擬 push 被 GitHub 擋（GH013）與 push 成功兩條路，驗證：
# set -euo pipefail 下失敗不中斷後續、ALERT 檔與 log 行有出來、成功時清掉舊 ALERT。
# 桌面通知用 PUSH_GUARD_NO_NOTIFY=1 關掉，不在測試裡彈視窗。

set -uo pipefail
cd "$(dirname "$0")/.."
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); echo "  ✅ $1"; }
bad() { FAIL=$((FAIL+1)); echo "  ❌ $1"; }

snippet=$(sed -n '/PUSH_GUARD_START/,/PUSH_GUARD_END/p' scripts/run-daily.sh)
if [ -z "$snippet" ]; then
  bad "run-daily.sh 找不到 PUSH_GUARD_START/END marker（接線遺失）"
  echo "══ PASS=$PASS FAIL=$FAIL ══"; exit 1
fi
ok "run-daily.sh 有 push guard 接線 marker"

STUB=$(mktemp -d)
trap 'rm -rf "$STUB"' EXIT
mk_git() {  # $1 = push 的 exit code
  cat > "$STUB/git" <<EOF
#!/bin/bash
case "\$1" in
  push) echo "remote: error: GH013: Repository rule violations found for refs/heads/main." >&2; exit $1 ;;
  rev-parse) echo "abc1234" ;;
  *) exit 0 ;;
esac
EOF
  chmod +x "$STUB/git"
}

run_snippet() {  # $1 = push exit code；回傳 snippet 在 set -e 下的 exit code
  local tmp; tmp=$(mktemp -d); SNIPPET_TMP="$tmp"
  mk_git "$1"
  ( set -euo pipefail
    export REPO_DIR="$tmp" DATE="2026-10-17" PUSH_GUARD_NO_NOTIFY=1 PATH="$STUB:$PATH"
    eval "$snippet" > "$tmp/out" 2>&1
    touch "$tmp/.reached-end" )
}

echo "── push 被擋（exit 1）──"
run_snippet 1; rc=$?
[ "$rc" -eq 0 ] && ok "set -e 下 push 失敗未中斷後續（exit ${rc}）" || bad "set -e 下 push 失敗讓 snippet 以 ${rc} 結束"
[ -f "$SNIPPET_TMP/.reached-end" ] && ok "失敗後仍執行到 guard 段之後" || bad "失敗後中斷、沒跑到後面"
grep -q 'PUSH-FAILED' "$SNIPPET_TMP/out" && ok "log 有 PUSH-FAILED 行" || bad "log 缺 PUSH-FAILED 行：$(cat "$SNIPPET_TMP/out")"
[ -f "$SNIPPET_TMP/ALERT-push-failed.md" ] && ok "產生 ALERT-push-failed.md" || bad "沒產生 ALERT 檔"
grep -q 'GH013' "$SNIPPET_TMP/ALERT-push-failed.md" 2>/dev/null && ok "ALERT 檔含 GH013 判讀與補救步驟" || bad "ALERT 檔缺 GH013 判讀"
grep -q 'abc1234' "$SNIPPET_TMP/ALERT-push-failed.md" 2>/dev/null && ok "ALERT 檔含本機 commit" || bad "ALERT 檔缺 commit"
rm -rf "$SNIPPET_TMP"

echo "── push 成功（exit 0）──"
run_snippet 0; rc=$?
[ "$rc" -eq 0 ] && ok "push 成功 snippet exit 0" || bad "push 成功卻 exit ${rc}"
grep -q 'Pushed: abc1234' "$SNIPPET_TMP/out" && ok "log 有 Pushed 行" || bad "log 缺 Pushed 行：$(cat "$SNIPPET_TMP/out")"
[ ! -f "$SNIPPET_TMP/ALERT-push-failed.md" ] && ok "成功時沒有 ALERT 檔" || bad "成功卻留下 ALERT 檔"
rm -rf "$SNIPPET_TMP"

echo "── 殘留 ALERT 在下次成功 push 被清掉 ──"
tmp2=$(mktemp -d); echo "殘留" > "$tmp2/ALERT-push-failed.md"; mk_git 0
( set -euo pipefail
  export REPO_DIR="$tmp2" DATE="2026-10-17" PUSH_GUARD_NO_NOTIFY=1 PATH="$STUB:$PATH"
  eval "$snippet" >/dev/null 2>&1 )
[ ! -f "$tmp2/ALERT-push-failed.md" ] && ok "舊 ALERT 檔被清掉" || bad "舊 ALERT 檔沒被清掉（會誤導）"
rm -rf "$tmp2"

echo
echo "══ PASS=$PASS FAIL=$FAIL ══"
[ "$FAIL" -eq 0 ]
