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

NPM_HIGH="$TMP/npm-high"
NPM_ACTIVE="$TMP/npm-active"
NPM_BIN="$TMP/npm-bin"
mkdir -p "$NPM_HIGH/@qwen-code/qwen-code" "$NPM_ACTIVE/@qwen-code/qwen-code" "$NPM_BIN"
printf '{"name":"@qwen-code/qwen-code","version":"0.21.10","bin":{"qwen":"cli-entry.js"}}' > "$NPM_HIGH/@qwen-code/qwen-code/package.json"
printf '{"name":"@qwen-code/qwen-code","version":"0.21.6","bin":{"qwen":"cli-entry.js"}}' > "$NPM_ACTIVE/@qwen-code/qwen-code/package.json"
printf '#!/usr/bin/env node\n' > "$NPM_HIGH/@qwen-code/qwen-code/cli-entry.js"
printf '#!/usr/bin/env node\n' > "$NPM_ACTIVE/@qwen-code/qwen-code/cli-entry.js"
ln -s "$NPM_ACTIVE/@qwen-code/qwen-code/cli-entry.js" "$NPM_BIN/qwen"
cat > "$NPM_BIN/npm" <<'EOF'
#!/bin/bash
case "$*" in
  "view @qwen-code/qwen-code version") printf '0.21.11\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$NPM_BIN/npm" "$NPM_BIN/qwen"
printf '[{"name":"@qwen-code/qwen-code","manager":"npm-g","source":"@qwen-code/qwen-code"}]' > "$TMP/m-npm.json"
out_npm=$(PATH="$NPM_BIN:/usr/bin:/bin" CCTOOL_NPM_ROOTS="$NPM_HIGH:$NPM_ACTIVE" CCTOOL_MANIFEST="$TMP/m-npm.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
python3 - "$out_npm" "$NPM_ACTIVE" "$NPM_HIGH" <<'PY' || fail "npm 多 prefix 斷言失敗"
import json, sys
packet = json.loads(sys.argv[1])
active_root, other_root = sys.argv[2:]
assert packet['errors'] == [], packet['errors']
assert packet['updates'] == [{
  'name': '@qwen-code/qwen-code',
  'manager': 'npm-g',
  'current': '0.21.6',
  'latest': '0.21.11',
  'source': '@qwen-code/qwen-code',
  'notes': '',
  'active_install': active_root,
  'other_installs': [{'version': '0.21.10', 'root': other_root}],
}], packet['updates']
print('✅ 測 4 npm 多 prefix：current 採 PATH 作用中安裝，其他版本分欄保留')
PY

UV_HOME="$TMP/uv-home"
UV_BIN="$TMP/uv-bin"
UV_META="$UV_HOME/.local/share/uv/tools/skillevaluator/lib/python3.13/site-packages/skillevaluator-0.2.1.dist-info"
mkdir -p "$UV_BIN" "$UV_META"
printf '%s\n' 'Name: skillevaluator' 'Version: 0.2.1' > "$UV_META/METADATA"
printf '%s' '{"url":"https://github.com/NVIDIA/SkillEvaluator.git","vcs_info":{"vcs":"git","commit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}' > "$UV_META/direct_url.json"
cat > "$UV_BIN/uv" <<'EOF'
#!/bin/bash
if [ "$*" = "tool list" ]; then printf 'skillevaluator v0.2.1\n- skillevaluator\n'; else exit 1; fi
EOF
cat > "$UV_BIN/gh" <<'EOF'
#!/bin/bash
if [ "$*" = "api repos/NVIDIA/SkillEvaluator/commits/HEAD --jq .sha" ]; then printf 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n'; else exit 1; fi
EOF
chmod +x "$UV_BIN/uv" "$UV_BIN/gh"
printf '[{"name":"skillevaluator","manager":"uv-git","source":"NVIDIA/SkillEvaluator"}]' > "$TMP/m-uv-git.json"
out_uv_git=$(HOME="$UV_HOME" PATH="$UV_BIN:/usr/bin:/bin" CCTOOL_MANIFEST="$TMP/m-uv-git.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
python3 - "$out_uv_git" <<'PY' || fail "uv-git 斷言失敗"
import json, sys
packet = json.loads(sys.argv[1])
assert packet['errors'] == [], packet['errors']
assert packet['updates'] == [{
  'name': 'skillevaluator',
  'manager': 'uv-git',
  'current': 'aaaaaaaa',
  'latest': 'bbbbbbbb',
  'source': 'NVIDIA/SkillEvaluator',
  'notes': '',
}], packet['updates']
print('✅ 測 5 uv-git：讀 direct_url commit 並比對 GitHub HEAD')
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

# mcp-npx：釘版 MCP server 從 ~/.claude.json 讀版本、比 npm latest；未釘版與雙檔不一致進 errors
NPM_BIN="$TMP/npmbin"
mkdir -p "$NPM_BIN"
cat > "$NPM_BIN/npm" <<'EOF'
#!/bin/bash
case "$*" in
  "view chrome-devtools-mcp version") printf '1.8.0\n' ;;
  *) exit 1 ;;
esac
EOF
chmod +x "$NPM_BIN/npm"
printf '%s' '{"mcpServers":{"chrome-devtools":{"command":"npx","args":["chrome-devtools-mcp@1.6.0","--autoConnect"]},"ctx":{"command":"npx","args":["-y","@upstash/context7-mcp@latest"]},"local-stdio":{"command":"python3","args":["server.py"]}}}' > "$TMP/c1.json"
printf '%s' '{"mcpServers":{"chrome-devtools":{"command":"npx","args":["chrome-devtools-mcp@1.6.0","--autoConnect"]}}}' > "$TMP/c2.json"
printf '%s' '{"mcpServers":{"chrome-devtools":{"command":"npx","args":["chrome-devtools-mcp@1.7.0","--autoConnect"]}}}' > "$TMP/c3.json"
printf '[{"name":"chrome-devtools","manager":"mcp-npx","source":"chrome-devtools-mcp"},{"name":"ctx","manager":"mcp-npx"}]' > "$TMP/m5.json"
out5=$(PATH="$NPM_BIN:$PATH" CCTOOL_CLAUDE_JSON="$TMP/c1.json:$TMP/c2.json" CCTOOL_MANIFEST="$TMP/m5.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
[ $? -eq 0 ] || fail "mcp-npx: exit code != 0"
echo "$out5" | python3 -c "
import sys, json
d = json.load(sys.stdin)
u = [x for x in d['updates'] if x['name']=='chrome-devtools']
assert u and u[0]['manager']=='mcp-npx' and u[0]['current']=='1.6.0' and u[0]['latest']=='1.8.0', 'mcp-npx 釘版比對錯: '+str(u)
assert len(u[0]['configs'])==2, 'configs 應列出兩份 .claude.json: '+str(u[0])
e = [x for x in d['errors'] if x['name']=='ctx']
assert e and 'unpinned' in e[0]['reason'], '未釘版 (@latest) 應進 errors: '+str(d['errors'])
assert not any(x['name']=='local-stdio' for x in d['discovered']+d['errors']+d['updates']), '非 npx 的 stdio server 不該被 mcp-npx 撿到'
print('✅ 測 5 mcp-npx：釘版 1.6.0 vs npm 1.8.0 進 updates、兩份 config 並列；@latest 進 errors(unpinned)；非 npx server 不撿')
" || fail "mcp-npx 斷言失敗"
out6=$(PATH="$NPM_BIN:$PATH" CCTOOL_CLAUDE_JSON="$TMP/c1.json:$TMP/c3.json" CCTOOL_MANIFEST="$TMP/m5.json" CCTOOL_IGNORE="$TMP/i.txt" "$HELPER" --json 2>/dev/null)
echo "$out6" | python3 -c "
import sys, json
d = json.load(sys.stdin)
e = [x for x in d['errors'] if x['name']=='chrome-devtools']
assert e and 'mismatch' in e[0]['reason'], '兩份 config 釘不同版應進 errors(mismatch): '+str(d['errors'])
assert not any(x['name']=='chrome-devtools' for x in d['updates']), 'mismatch 時不該進 updates'
print('✅ 測 6 mcp-npx：兩份 config 釘版不一致 → errors(config mismatch)、不比對')
" || fail "mcp-npx mismatch 斷言失敗"

echo "🎉 ALL PASS"
