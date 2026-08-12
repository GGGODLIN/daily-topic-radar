#!/bin/bash
# ledger-reconcile.test.sh — 每個閘一個 fixture，證明它真的擋/放，不是「跑得動」而已。
# 用法：bash ledger-reconcile.test.sh
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$DIR/ledger-reconcile.py"
TMP=$(mktemp -d)
trap 'rc=$?; rm -rf "$TMP"; exit $rc' EXIT

PASS=0
FAIL=0

check() {
  local name="$1" expect="$2" actual="$3"
  if [ "$expect" = "$actual" ]; then
    PASS=$((PASS + 1)); printf '  ok   %-52s %s\n' "$name" "$actual"
  else
    FAIL=$((FAIL + 1)); printf '  FAIL %-52s expect=%s actual=%s\n' "$name" "$expect" "$actual"
  fi
}

bucket_of() {
  python3 - "$1" "$2" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
title = sys.argv[2]
for b in ("forced_high", "min_medium", "open", "bottom_only", "silent"):
    for it in d.get(b, []):
        if it["title"] == title:
            print(b); raise SystemExit
print("excluded")
PY
}

field_of() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
title, field = sys.argv[2], sys.argv[3]
rows = [json.loads(l) for l in open(d["_ledger_for_test"]) if l.strip()]
for r in rows:
    if r["title"] == title:
        print(r.get(field)); raise SystemExit
print("MISSING")
PY
}

run() {  # run <ledger> <date> [findings] [--apply]
  local ledger="$1" date="$2" findings="${3:-}" apply="${4:-}"
  local args=(--date "$date" --ledger "$ledger" --json --no-health)
  [ -n "$findings" ] && args+=(--findings "$findings")
  [ -n "$apply" ] && args+=("$apply")
  python3 "$SCRIPT" "${args[@]}" 2>/dev/null \
    | python3 -c "import json,sys; d=json.load(sys.stdin); d['_ledger_for_test']='$ledger'; print(json.dumps(d,ensure_ascii=False))"
}

echo "== 閘位判定 =="
cat > "$TMP/gates.jsonl" <<'EOF'
{"title":"pending-c1","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}
{"title":"pending-c2","first_seen":"2026-07-01","last_seen":"2026-07-01","count":2,"status":"pending","note":""}
{"title":"pending-c3","first_seen":"2026-07-01","last_seen":"2026-07-01","count":3,"status":"pending","note":""}
{"title":"kept-no-esc","first_seen":"2026-07-01","last_seen":"2026-07-01","count":9,"status":"kept","note":""}
{"title":"kept-esc-below","first_seen":"2026-07-01","last_seen":"2026-07-01","count":3,"status":"kept","escalate_at":5,"note":""}
{"title":"kept-esc-reached","first_seen":"2026-07-01","last_seen":"2026-07-01","count":5,"status":"kept","escalate_at":5,"note":""}
{"title":"observing-no-esc","first_seen":"2026-07-01","last_seen":"2026-07-01","count":7,"status":"observing","note":""}
{"title":"pending-esc-below","first_seen":"2026-07-01","last_seen":"2026-07-01","count":4,"status":"pending","escalate_at":9,"note":""}
{"title":"due-future","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","next_due":"2026-08-10","note":""}
{"title":"due-past","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","next_due":"2026-07-20","note":""}
{"title":"already-done","first_seen":"2026-07-01","last_seen":"2026-07-01","count":9,"status":"done","note":""}
{"title":"already-killed","first_seen":"2026-07-01","last_seen":"2026-07-01","count":9,"status":"killed","note":""}
EOF
run "$TMP/gates.jsonl" 2026-07-30 > "$TMP/g.json"
check "pending count=1 -> open"            open        "$(bucket_of "$TMP/g.json" pending-c1)"
check "pending count=2 -> min_medium"      min_medium  "$(bucket_of "$TMP/g.json" pending-c2)"
check "pending count=3 -> forced_high"     forced_high "$(bucket_of "$TMP/g.json" pending-c3)"
check "kept 無 escalate_at -> silent"      silent      "$(bucket_of "$TMP/g.json" kept-no-esc)"
check "kept count<escalate_at -> silent"   silent      "$(bucket_of "$TMP/g.json" kept-esc-below)"
check "kept count>=escalate_at -> high"    forced_high "$(bucket_of "$TMP/g.json" kept-esc-reached)"
check "observing 無 escalate_at -> silent" silent      "$(bucket_of "$TMP/g.json" observing-no-esc)"
check "escalate_at 覆蓋 count>=3 門檻"      bottom_only "$(bucket_of "$TMP/g.json" pending-esc-below)"
check "next_due 未到 -> silent"            silent      "$(bucket_of "$TMP/g.json" due-future)"
check "next_due 已過 -> 解除靜默"           open        "$(bucket_of "$TMP/g.json" due-past)"
check "status=done -> excluded"            excluded    "$(bucket_of "$TMP/g.json" already-done)"
check "status=killed -> excluded"          excluded    "$(bucket_of "$TMP/g.json" already-killed)"

echo "== count 累加語意 =="
cat > "$TMP/count.jsonl" <<'EOF'
{"title":"seen-earlier","first_seen":"2026-07-01","last_seen":"2026-07-29","count":1,"status":"pending","note":""}
{"title":"seen-today","first_seen":"2026-07-01","last_seen":"2026-07-30","count":1,"status":"pending","note":""}
{"title":"terminal","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"done","note":""}
{"title":"due-future","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","next_due":"2026-08-10","note":""}
EOF
cat > "$TMP/f1.json" <<'EOF'
[{"title":"x","match":"seen-earlier","channel":"memory"},
 {"title":"y","match":"seen-today","channel":"memory"},
 {"title":"z","match":"terminal","channel":"memory"},
 {"title":"w","match":"due-future","channel":"memory"},
 {"title":"brand-new-item","match":null,"channel":"wiki-lint","source_key":"wiki-lint:brand-new-item"},
 {"title":"bad","match":"no-such-title","channel":"memory"}]
EOF
run "$TMP/count.jsonl" 2026-07-30 "$TMP/f1.json" --apply > "$TMP/c.json"
check "last_seen 較舊 -> count+1"          2 "$(field_of "$TMP/c.json" seen-earlier count)"
check "last_seen=今日 -> count 不變"        1 "$(field_of "$TMP/c.json" seen-today count)"
check "terminal 不被 finding 喚醒"          1 "$(field_of "$TMP/c.json" terminal count)"
check "next_due 未到 count 不累加"          1 "$(field_of "$TMP/c.json" due-future count)"
check "next_due 未到 last_seen 仍更新" 2026-07-30 "$(field_of "$TMP/c.json" due-future last_seen)"
check "match=null -> 新增 entry"            1 "$(field_of "$TMP/c.json" brand-new-item count)"
check "新 entry status=pending"       pending "$(field_of "$TMP/c.json" brand-new-item status)"
check "新 entry 保留 source_key" wiki-lint:brand-new-item "$(field_of "$TMP/c.json" brand-new-item source_key)"
check "match 指向不存在 -> 進 unmatched" 1 \
  "$(python3 -c "import json;print(len(json.load(open('$TMP/c.json'))['unmatched_findings']))")"

echo "== 存在性證據 =="
cat > "$TMP/ev.jsonl" <<EOF
{"title":"檢查 $SCRIPT 是否還在","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}
{"title":"檢查 /no/such/file-xyz.md 是否還在","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}
{"title":"沒有任何可查物的抽象拍板項","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}
EOF
run "$TMP/ev.jsonl" 2026-07-30 > "$TMP/e.json"
ev() { python3 -c "
import json,sys
d=json.load(open('$TMP/e.json'))
for it in d['open']:
    if it['title'].startswith('$1'):
        print(json.dumps(it.get('verify_evidence'),ensure_ascii=False)); raise SystemExit
print('MISSING')"; }
check "存在的檔 -> exists true"  true  "$(ev 檢查\ /Users | python3 -c "import json,sys; v=json.load(sys.stdin); print(str(any(c.get('exists') for c in v)).lower())" 2>/dev/null || echo skip)"
check "不存在的檔 -> exists false" false "$(ev 檢查\ /no | python3 -c "import json,sys; v=json.load(sys.stdin); print(str(all(c.get('exists') for c in v)).lower())" 2>/dev/null || echo skip)"
check "無可查物 -> verify_evidence 空陣列" "[]" "$(ev 沒有任何)"

echo "== --decide：status 轉換的唯一機械路徑 =="
cat > "$TMP/dec.jsonl" <<'EOF'
{"title":"to-done","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":"原註"}
{"title":"to-killed","first_seen":"2026-07-01","last_seen":"2026-07-01","count":2,"status":"pending","note":""}
{"title":"to-kept-esc","first_seen":"2026-07-01","last_seen":"2026-07-01","count":3,"status":"pending","note":""}
{"title":"to-cycle","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}
EOF
cat > "$TMP/d1.json" <<'EOF'
[{"match":"to-done","verdict":"done","note":"已修：證據 X"},
 {"match":"to-killed","verdict":"killed","note":"範圍外"},
 {"match":"to-kept-esc","verdict":"kept","escalate_at":8},
 {"match":"to-cycle","verdict":"pending","next_due":"2026-08-06","note":"7 天週期"}]
EOF
run "$TMP/dec.jsonl" 2026-07-30 "" --apply >/dev/null 2>&1
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide "$TMP/d1.json" --apply --json --no-health >/dev/null 2>&1
D="$TMP/d.after.json"; printf '{"_ledger_for_test":"%s"}' "$TMP/dec.jsonl" > "$D"
check "verdict=done 寫進 status"        done     "$(field_of "$D" to-done status)"
check "note 是追加不是覆寫"       "原註 ｜ 已修：證據 X" "$(field_of "$D" to-done note)"
check "verdict=killed 寫進 status"      killed   "$(field_of "$D" to-killed status)"
check "kept 同時設 escalate_at"         8        "$(field_of "$D" to-kept-esc escalate_at)"
check "pending 同時設 next_due"  2026-08-06      "$(field_of "$D" to-cycle next_due)"
check "拍板後 count 不被動到"           1        "$(field_of "$D" to-cycle count)"

python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"resolved"}]') --apply --no-health >/dev/null 2>&1
check "verdict 不在白名單 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"no-such","verdict":"done"}]') --apply --no-health >/dev/null 2>&1
check "match 指不到 -> 非 0 退出"       1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":"8/6"}]') --apply --no-health >/dev/null 2>&1
check "next_due 格式錯 -> 非 0 退出"    1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":"2026-02-31"}]') --apply --no-health >/dev/null 2>&1
check "next_due 日曆日期錯 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":"20260818"}]') --apply --no-health >/dev/null 2>&1
check "next_due 非標準日期 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":""}]') --apply --no-health >/dev/null 2>&1
check "next_due 空字串 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":0}]') --apply --no-health >/dev/null 2>&1
check "next_due 數字 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/dec.jsonl" --decide <(echo '[{"match":"to-done","verdict":"pending","next_due":"   "}]') --apply --no-health >/dev/null 2>&1
check "next_due 空白 -> 非 0 退出" 1 "$?"

echo "== 沒有 --apply 不得改動 ledger =="
cp "$TMP/gates.jsonl" "$TMP/untouched.jsonl"
BEFORE=$(shasum -a 256 < "$TMP/untouched.jsonl")
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/untouched.jsonl" --findings "$TMP/f1.json" --no-health >/dev/null 2>&1
AFTER=$(shasum -a 256 < "$TMP/untouched.jsonl")
check "dry-run ledger 位元不變" "$BEFORE" "$AFTER"

echo "== findings channel 白名單：旁路要 fail-closed =="
cat > "$TMP/missing-channel.json" <<'EOF'
[{"title":"missing-channel-item","match":null}]
EOF
cat > "$TMP/unknown-channel.json" <<'EOF'
[{"title":"unknown-channel-item","match":null,"channel":"typo-channel"}]
EOF
cat > "$TMP/beads-channel.json" <<'EOF'
[{"title":"beads-item","match":null,"channel":"beads-aging"}]
EOF
for case_name in missing-channel unknown-channel beads-channel; do
  cp "$TMP/gates.jsonl" "$TMP/$case_name.jsonl"
  BEFORE=$(shasum -a 256 < "$TMP/$case_name.jsonl")
  python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/$case_name.jsonl" --findings "$TMP/$case_name.json" --apply --no-health >/dev/null 2>&1
  STATUS=$?
  AFTER=$(shasum -a 256 < "$TMP/$case_name.jsonl")
  check "$case_name -> 非 0 退出" 1 "$STATUS"
  check "$case_name -> ledger 位元不變" "$BEFORE" "$AFTER"
done

echo "== 壞輸入要明確失敗、不得靜默 =="
printf '{"title":"ok","status":"pending","count":1,"first_seen":"2026-07-01","last_seen":"2026-07-01","note":""}\nNOT JSON\n' > "$TMP/broken.jsonl"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/broken.jsonl" --no-health >/dev/null 2>&1
check "壞 jsonl -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-7-3 --ledger "$TMP/gates.jsonl" --no-health >/dev/null 2>&1
check "壞日期格式 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 2026-02-31 --ledger "$TMP/gates.jsonl" --no-health >/dev/null 2>&1
check "壞日曆日期 -> 非 0 退出" 1 "$?"
python3 "$SCRIPT" --date 20260730 --ledger "$TMP/gates.jsonl" --no-health >/dev/null 2>&1
check "非標準日期 -> 非 0 退出" 1 "$?"
printf '%s\n' '{"title":"bad-due","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","next_due":"","note":""}' > "$TMP/bad-due.jsonl"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/bad-due.jsonl" --no-health >/dev/null 2>&1
check "ledger 空 next_due -> 非 0 退出" 1 "$?"

echo "== 漏收週報偵測（late_unconsumed_reports）=="
late_count() {
  python3 - "$1" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(len(d.get("late_unconsumed_reports", [])))
PY
}
printf '%s\n' '{"title":"seed","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}' > "$TMP/late.jsonl"
printf '## 判讀\n🚨 本週抽驗 3 個 session，共 3 個 rubric FAIL。\n' > "$TMP/2026-07-29-rba-verify.md"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/late.jsonl" --no-health --json > "$TMP/late-out.json" 2>/dev/null
check "警告報告無收據 -> 列入漏收" 1 "$(late_count "$TMP/late-out.json")"
printf '%s\n' '{"title":"seed","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":"","source_key":"rba-verify:2026-07-29"}' > "$TMP/late.jsonl"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/late.jsonl" --no-health --json > "$TMP/late-out.json" 2>/dev/null
check "收據存在 -> 不列漏收" 0 "$(late_count "$TMP/late-out.json")"
printf '## 判讀\n本週抽驗 3 個 session，0 FAIL。\n' > "$TMP/2026-07-29-rba-verify.md"
printf '%s\n' '{"title":"seed","first_seen":"2026-07-01","last_seen":"2026-07-01","count":1,"status":"pending","note":""}' > "$TMP/late.jsonl"
python3 "$SCRIPT" --date 2026-07-30 --ledger "$TMP/late.jsonl" --no-health --json > "$TMP/late-out.json" 2>/dev/null
check "報告無警告 -> 不列漏收" 0 "$(late_count "$TMP/late-out.json")"

echo
echo "pass=$PASS fail=$FAIL"
[ "$FAIL" -eq 0 ]
