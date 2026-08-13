#!/usr/bin/env bash
# cc-tool-updates-daily.sh 的 smoke test（測 --json 確定性部分：三名單分類 + JSON 結構 + graceful）。
# 用 fixture manifest/ignore 注入（CCTOOL_MANIFEST/IGNORE env），只查 sem 一個工具的網路比對。
set -uo pipefail
HELPER="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/cc-tool-updates-daily.sh"
TMP="$(mktemp -d)"
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT
fail(){ echo "❌ FAIL: $1"; exit 1; }

[ -x "$HELPER" ] || fail "helper 不存在或不可執行: $HELPER"

# fixture：白名單追 sem（本機 cargo-git 有）、黑名單忽略 cargo-bundle（本機有）
printf '[{"name":"sem","manager":"cargo-git","source":"Ataraxy-Labs/sem"}]' > "$TMP/m.json"
printf 'cargo-bundle\n' > "$TMP/i.txt"

out=$(CCTOOL_MANIFEST="$TMP/m.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
rc=$?
[ $rc -eq 0 ] || fail "exit code $rc != 0"

echo "$out" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert set(d) >= {'updates','discovered','errors'}, 'JSON 缺 key: '+str(list(d))
assert isinstance(d['updates'], list) and isinstance(d['discovered'], list) and isinstance(d['errors'], list), '三 key 非 list'
disc = {x['name'] for x in d['discovered']}
assert 'sem' not in disc, 'sem 在白名單卻被當 discovered'
assert 'cargo-bundle' not in disc, 'cargo-bundle 在黑名單卻被當 discovered'
assert len(d['discovered']) > 0, 'discovered 為空（本機應有未分類工具，發現層可能壞了）'
print('✅ 測 1 三名單分類：白(sem)追蹤、黑(cargo-bundle)忽略、其餘進 discovered（'+str(len(d['discovered']))+' 個）')
print('✅ 測 2 JSON 結構：updates/discovered/errors 三 list 齊全')
" || fail "JSON 斷言失敗"

# Homebrew revision：用固定 stub 驗相同 revision、舊 revision、多 keg 順序與 revision=0
BREW_BIN="$TMP/bin"
mkdir -p "$BREW_BIN"
cat > "$BREW_BIN/brew" <<'EOF'
#!/bin/bash
case "$BREW_SCENARIO:$*" in
  same:"list --versions --formula") printf 'python@3.12 3.12.13_4 3.12.12_5\n' ;;
  older:"list --versions --formula") printf 'python@3.12 3.12.13_3 3.12.12_5\n' ;;
  zero:"list --versions --formula") printf 'ast-grep 0.45.1\n' ;;
  segments:"list --versions --formula") printf 'example 1.2_5 1.2.1\n' ;;
  *:"leaves") printf 'python@3.12\nast-grep\n' ;;
  same:"info --json=v2 python@3.12"|older:"info --json=v2 python@3.12") printf '{"formulae":[{"versions":{"stable":"3.12.13"},"revision":4}]}' ;;
  zero:"info --json=v2 ast-grep") printf '{"formulae":[{"versions":{"stable":"0.45.1"},"revision":0}]}' ;;
  segments:"info --json=v2 example") printf '{"formulae":[{"versions":{"stable":"1.2.1"},"revision":0}]}' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$BREW_BIN/brew"
printf '[{"name":"python@3.12","manager":"brew","source":"python@3.12"}]' > "$TMP/m-brew.json"
printf '[{"name":"ast-grep","manager":"brew","source":"ast-grep"}]' > "$TMP/m-brew-zero.json"
printf '[{"name":"example","manager":"brew","source":"example"}]' > "$TMP/m-brew-segments.json"
out_same=$(PATH="$BREW_BIN:/usr/bin:/bin" BREW_SCENARIO=same CCTOOL_MANIFEST="$TMP/m-brew.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
out_older=$(PATH="$BREW_BIN:/usr/bin:/bin" BREW_SCENARIO=older CCTOOL_MANIFEST="$TMP/m-brew.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
out_zero=$(PATH="$BREW_BIN:/usr/bin:/bin" BREW_SCENARIO=zero CCTOOL_MANIFEST="$TMP/m-brew-zero.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
out_segments=$(PATH="$BREW_BIN:/usr/bin:/bin" BREW_SCENARIO=segments CCTOOL_MANIFEST="$TMP/m-brew-segments.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
python3 - "$out_same" "$out_older" "$out_zero" "$out_segments" <<'PY' || fail "Homebrew revision 斷言失敗"
import json, sys
same, older, zero, segments = (json.loads(value) for value in sys.argv[1:])
assert same['errors'] == [], same['errors']
assert same['updates'] == [], same['updates']
assert older['errors'] == [], older['errors']
assert older['updates'] == [{'name': 'python@3.12', 'manager': 'brew', 'current': '3.12.13_3', 'latest': '3.12.13_4', 'source': 'python@3.12', 'notes': ''}], older['updates']
assert zero['errors'] == [], zero['errors']
assert zero['updates'] == [], zero['updates']
assert segments['errors'] == [], segments['errors']
assert segments['updates'] == [], segments['updates']
print('✅ 測 3 Homebrew revision：多 keg 取新版、舊 revision 正確升級、revision=0 無後綴、基礎版本段落優先')
PY

# graceful：fixture manifest 含不存在的 manager → 進 errors 不崩
printf '[{"name":"nonexistent-xyz","manager":"cargo-git","source":"no/such-repo"}]' > "$TMP/m2.json"
out2=$(CCTOOL_MANIFEST="$TMP/m2.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
rc2=$?
[ $rc2 -eq 0 ] || fail "graceful: 不存在工具讓 exit code $rc2 != 0"
echo "$out2" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert any(e['name']=='nonexistent-xyz' for e in d['errors']), '不存在工具未進 errors'
print('✅ 測 4 graceful：不存在工具進 errors、exit 0、不崩')
" || fail "graceful 斷言失敗"

echo "🎉 ALL PASS"
