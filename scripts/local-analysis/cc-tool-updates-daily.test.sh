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

# graceful：fixture manifest 含不存在的 manager → 進 errors 不崩
printf '[{"name":"nonexistent-xyz","manager":"cargo-git","source":"no/such-repo"}]' > "$TMP/m2.json"
out2=$(CCTOOL_MANIFEST="$TMP/m2.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
rc2=$?
[ $rc2 -eq 0 ] || fail "graceful: 不存在工具讓 exit code $rc2 != 0"
echo "$out2" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert any(e['name']=='nonexistent-xyz' for e in d['errors']), '不存在工具未進 errors'
print('✅ 測 3 graceful：不存在工具進 errors、exit 0、不崩')
" || fail "graceful 斷言失敗"

echo "🎉 ALL PASS"
