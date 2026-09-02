#!/bin/bash
# Fixture regression for skill-upstream-check-weekly.sh
# frontmatter 九件沿用動態上游 SHA；installed lock cases 使用 Git tree hash literal 與一筆 real lock sample
# pinned 值執行時從上游現值計算，known-good 不會隨上游演進而腐壞
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HASH_HELPER="$SCRIPT_DIR/skill-lock-drift.py"
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
trap 'rc=$?; rm -rf "$FIX"; exit $rc' EXIT
mkdir -p "$FIX/skills" "$FIX/logs" "$FIX/agents-skills/exact-skill" "$FIX/agents-skills/tampered-skill" "$FIX/agents-skills/unrelated-skill"
printf 'original\n' > "$FIX/agents-skills/exact-skill/SKILL.md"
printf 'tampered\n' > "$FIX/agents-skills/tampered-skill/SKILL.md"
printf 'unrelated\n' > "$FIX/agents-skills/unrelated-skill/SKILL.md"
cat > "$FIX/skill-lock.json" <<'JSON'
{
  "version": 3,
  "skills": {
    "exact-skill": {
      "skillFolderHash": "69b20525656d07c110596c0dc80bdb92893838dc"
    },
    "tampered-skill": {
      "skillFolderHash": "69b20525656d07c110596c0dc80bdb92893838dc"
    },
    "missing-skill": {
      "skillFolderHash": "69b20525656d07c110596c0dc80bdb92893838dc"
    },
    "unverifiable-skill": {
      "skillFolderHash": ""
    }
  }
}
JSON

SOURCE_ROOT="$FIX/source-repo"
SOURCE_SKILL="$SOURCE_ROOT/skills/scaffolded-skill"
mkdir -p "$SOURCE_SKILL/rules"
printf 'agent runtime\n' > "$SOURCE_SKILL/AGENTS.md"
printf 'skill runtime\n' > "$SOURCE_SKILL/SKILL.md"
printf 'rule runtime\n' > "$SOURCE_SKILL/rules/runtime.md"
printf 'source readme\n' > "$SOURCE_SKILL/README.md"
printf '{"source":"fixture"}\n' > "$SOURCE_SKILL/metadata.json"
printf 'generated sections\n' > "$SOURCE_SKILL/rules/_sections.md"
printf 'generated template\n' > "$SOURCE_SKILL/rules/_template.md"
git -C "$SOURCE_ROOT" init --quiet
git -C "$SOURCE_ROOT" add --all --force
source_tree=$(git -C "$SOURCE_ROOT" write-tree)
source_full_hash=$(git -C "$SOURCE_ROOT" rev-parse "$source_tree:skills/scaffolded-skill")
for installed_name in scaffolded-skill scaffolded-tampered-skill; do
  mkdir -p "$FIX/agents-skills/$installed_name/rules"
  cp "$SOURCE_SKILL/AGENTS.md" "$FIX/agents-skills/$installed_name/AGENTS.md"
  cp "$SOURCE_SKILL/SKILL.md" "$FIX/agents-skills/$installed_name/SKILL.md"
  cp "$SOURCE_SKILL/rules/runtime.md" "$FIX/agents-skills/$installed_name/rules/runtime.md"
done
printf 'tampered runtime\n' > "$FIX/agents-skills/scaffolded-tampered-skill/rules/runtime.md"
cat > "$FIX/source-scaffolding-lock.json" <<JSON
{
  "version": 3,
  "skills": {
    "scaffolded-skill": {
      "source": "fixture/skills",
      "sourceUrl": "https://example.test/fixture.git",
      "skillPath": "skills/scaffolded-skill/SKILL.md",
      "skillFolderHash": "$source_full_hash"
    },
    "scaffolded-tampered-skill": {
      "source": "fixture/skills",
      "sourceUrl": "https://example.test/fixture.git",
      "skillPath": "skills/scaffolded-skill/SKILL.md",
      "skillFolderHash": "$source_full_hash"
    },
    "scaffolded-missing-skill": {
      "source": "fixture/skills",
      "sourceUrl": "https://example.test/fixture.git",
      "skillPath": "skills/scaffolded-skill/SKILL.md",
      "skillFolderHash": "$source_full_hash"
    }
  }
}
JSON
source_runtime_hash=$(python3 - "$HASH_HELPER" "$FIX/agents-skills/scaffolded-skill" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("drift", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
skill = Path(sys.argv[2])
root_fd = os.open(skill.parent, os.O_RDONLY | os.O_DIRECTORY)
try:
  print(module.hash_skill(root_fd, skill.name, module.INSTALLER_EXCLUDED_PATHS))
finally:
  os.close(root_fd)
PY
)
python3 - "$FIX/source-scaffolding-lock.json" "$FIX/source-scaffolding-baseline.json" "$source_runtime_hash" <<'PY'
import json
import sys
from pathlib import Path

lock = json.loads(Path(sys.argv[1]).read_text())
records = []
for name, entry in lock["skills"].items():
  records.append({
    "skill": name,
    "source": entry["source"],
    "sourceUrl": entry["sourceUrl"],
    "skillPath": entry["skillPath"],
    "sourceTreeHash": entry["skillFolderHash"],
    "runtimeTreeHash": sys.argv[3],
    "provenanceCommit": "deadbeef",
    "provenanceReason": "offline source projection fixture",
  })
Path(sys.argv[2]).write_text(json.dumps({"version": 1, "scope": list(lock["skills"]), "entries": records}))
PY

mk() {
  mkdir -p "$FIX/skills/$1"
  printf -- '---\nname: %s\n%s\n---\nbody\n' "$1" "$2" > "$FIX/skills/$1/SKILL.md"
}
mk fx-good "upstream: $WATCH_REPO
upstream-path: $WATCH_PATH
upstream-pinned: ${latest:0:7}"
mk fx-metadata-good "metadata:
  upstream: $WATCH_REPO
  upstream-path: $WATCH_PATH
  upstream-pinned: ${latest:0:7}"
mk fx-inline-quoted "metadata: {upstream: \"$WATCH_REPO\", upstream-path: '$WATCH_PATH', upstream-pinned: \"${latest:0:7}\"}"
mk fx-master-branch "metadata:
  upstream: $WATCH_REPO
  upstream-path: $WATCH_PATH
  upstream-pinned: ${latest:0:7}
  upstream-branch: master"
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
if [[ "\$*" == *"?ref=master"* || "\$*" == *"...master"* ]]; then
  printf '%s\n' "\$*" >> "$FIX/master-gh-calls"
  exec "$REAL_GH" "\${@//master/main}"
fi
exec "$REAL_GH" "\$@"
EOF
chmod +x "$FIX/bin/gh"

PATH="$FIX/bin:$PATH" SKILLS_DIR="$FIX/skills" AGENTS_SKILLS_DIR="$FIX/agents-skills" SKILL_LOCK_FILE="$FIX/skill-lock.json" LOG_DIR="$FIX/logs" LOCAL_ANALYSIS_DATE="2026-01-02" bash "$SCRIPT_DIR/skill-upstream-check-weekly.sh"
report="$FIX/logs/2026-01-02.md"

pass=0; fail=0
check() {
  if grep -qE "$2" "$report"; then
    echo "PASS: $1"; pass=$((pass+1))
  else
    echo "FAIL: $1 — 預期 pattern 未出現: $2"; fail=$((fail+1))
  fi
}
check "legacy known-good 報 ✅" "✅ fx-good"
check "metadata known-good 報 ✅" "✅ fx-metadata-good"
check "inline quoted metadata 報 ✅" "✅ fx-inline-quoted"
check "master branch override 報 ✅" "✅ fx-master-branch"
if [ "$(grep -c 'ref=master' "$FIX/master-gh-calls" 2>/dev/null || true)" -ge 1 ] && [ "$(grep -c '\.\.\.master' "$FIX/master-gh-calls" 2>/dev/null || true)" -ge 1 ]; then
  echo "PASS: master branch 傳入 contents 與 compare API"; pass=$((pass+1))
else
  echo "FAIL: master branch 未傳入 contents 與 compare API"; fail=$((fail+1))
fi
check "known-bad 報 ⬆️"         "⬆️ \*\*fx-behind\*\*"
check "path-404 報 🪦"          "🪦 \*\*fx-orphan-path\*\*"
check "known-orphan 報 ℹ️ 跳過" "ℹ️ fx-known-orphan"
check "非 404 API 錯誤不冒充 path 消失" "⚠️ \*\*fx-api-error\*\* — upstream API 查詢失敗（非 404）"
check "線索不完整列待確認" "⚠️ \*\*fx-incomplete\*\* — frontmatter 線索不完整"
check "summary 計數 9 支 / 4 待確認" "9 支散檔 fork 對賬巡過（4 支待對賬 / 待確認）"
check "tampered installed skill 報 drift" "⚠️ \*\*tampered-skill\*\* — installed skill 與 lock hash 不符"
check "missing installed skill 報 drift" "⚠️ \*\*missing-skill\*\* — lock 有紀錄，但已安裝 skill 目錄不存在"
primary_lock_drift_count=$(python3 - "$report" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
section = text.split("## Installed skill lock drift（read-only）", 1)[1].split("\n## ", 1)[0]
print(section.count("installed skill 與 lock hash 不符"))
PY
)
if ! grep -qF '**exact-skill**' "$report" && ! grep -qF '**unrelated-skill**' "$report" && ! grep -qF '**unverifiable-skill**' "$report" && [ "$primary_lock_drift_count" -eq 1 ]; then
  echo "PASS: exact/empty hash silent、unrelated dir ignored、tampered 只報一筆"; pass=$((pass+1))
else
  echo "FAIL: exact/empty/unrelated 被誤報，或 tampered primary drift 筆數不等於一"; fail=$((fail+1))
fi

source_scaffold_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/agents-skills" --lock-file "$FIX/source-scaffolding-lock.json" --baseline-file "$FIX/source-scaffolding-baseline.json" 2>&1)
source_scaffold_status=$?
if [ "$source_scaffold_status" -eq 0 ] \
  && [[ "$source_scaffold_output" != *"**scaffolded-skill**"* ]] \
  && [[ "$source_scaffold_output" == *"⚠️ **scaffolded-tampered-skill** — installed skill 與 lock hash 不符"* ]] \
  && [[ "$source_scaffold_output" == *"⚠️ **scaffolded-missing-skill** — lock 有紀錄，但已安裝 skill 目錄不存在"* ]]; then
  echo "PASS: source scaffolding exclusion preserves runtime check"; pass=$((pass+1))
else
  echo "FAIL: source scaffolding exclusion produced wrong result: $source_scaffold_output"; fail=$((fail+1))
fi

for excluded_type_name in scaffolded-directory-excluded-skill scaffolded-empty-directory-excluded-skill scaffolded-symlink-excluded-skill; do
  mkdir -p "$FIX/agents-skills/$excluded_type_name/rules"
  cp "$SOURCE_SKILL/AGENTS.md" "$FIX/agents-skills/$excluded_type_name/AGENTS.md"
  cp "$SOURCE_SKILL/SKILL.md" "$FIX/agents-skills/$excluded_type_name/SKILL.md"
  cp "$SOURCE_SKILL/rules/runtime.md" "$FIX/agents-skills/$excluded_type_name/rules/runtime.md"
done
mkdir -p "$FIX/agents-skills/scaffolded-directory-excluded-skill/README.md"
printf 'directory payload\n' > "$FIX/agents-skills/scaffolded-directory-excluded-skill/README.md/payload"
mkdir -p "$FIX/agents-skills/scaffolded-empty-directory-excluded-skill/README.md"
ln -s SKILL.md "$FIX/agents-skills/scaffolded-symlink-excluded-skill/metadata.json"
python3 - "$FIX/source-scaffolding-type-baseline.json" "$source_full_hash" "$source_runtime_hash" <<'PY'
import json
import sys
from pathlib import Path

names = (
  "scaffolded-directory-excluded-skill",
  "scaffolded-empty-directory-excluded-skill",
  "scaffolded-symlink-excluded-skill",
)
records = [
  {
    "skill": name,
    "source": "fixture/skills",
    "sourceUrl": "https://example.test/fixture.git",
    "skillPath": "skills/scaffolded-skill/SKILL.md",
    "sourceTreeHash": sys.argv[2],
    "runtimeTreeHash": sys.argv[3],
    "provenanceCommit": "deadbeef",
    "provenanceReason": "offline source projection fixture",
  }
  for name in names
]
Path(sys.argv[1]).write_text(json.dumps({"version": 1, "scope": list(names), "entries": records}))
PY
excluded_type_checks=0
for excluded_type_name in scaffolded-directory-excluded-skill scaffolded-empty-directory-excluded-skill scaffolded-symlink-excluded-skill; do
  cat > "$FIX/$excluded_type_name-lock.json" <<JSON
{
  "version": 3,
  "skills": {
    "$excluded_type_name": {
      "source": "fixture/skills",
      "sourceUrl": "https://example.test/fixture.git",
      "skillPath": "skills/scaffolded-skill/SKILL.md",
      "skillFolderHash": "$source_full_hash"
    }
  }
}
JSON
  excluded_type_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/agents-skills" --lock-file "$FIX/$excluded_type_name-lock.json" --baseline-file "$FIX/source-scaffolding-type-baseline.json" 2>&1)
  excluded_type_status=$?
  if [ "$excluded_type_status" -eq 2 ] && [[ "$excluded_type_output" == *"驗證失敗"* ]]; then
    excluded_type_checks=$((excluded_type_checks+1))
  fi
done
if [ "$excluded_type_checks" -eq 3 ]; then
  echo "PASS: excluded directory, empty directory, and symlink fail closed"; pass=$((pass+1))
else
  echo "FAIL: excluded non-regular path was hidden"; fail=$((fail+1))
fi

python3 - "$HOME/.agents/.skill-lock.json" "$FIX/real-sample-lock.json" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
name = "web-design-guidelines"
entry = source["skills"][name]
Path(sys.argv[2]).write_text(json.dumps({"version": source["version"], "skills": {name: entry}}))
PY
real_sample_output=$(python3 "$HASH_HELPER" --skills-dir "$HOME/.agents/skills" --lock-file "$FIX/real-sample-lock.json" 2>&1)
real_sample_status=$?
if [ "$real_sample_status" -eq 0 ] && [ -z "$real_sample_output" ]; then
  echo "PASS: real lock sample reproduces expected Git tree hash"; pass=$((pass+1))
else
  echo "FAIL: real lock sample hash mismatch: $real_sample_output"; fail=$((fail+1))
fi

python3 - "$HOME/.agents/.skill-lock.json" "$FIX/vercel-sample-lock.json" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
name = "vercel-react-best-practices"
entry = source["skills"][name]
Path(sys.argv[2]).write_text(json.dumps({"version": source["version"], "skills": {name: entry}}))
PY
vercel_sample_output=$(python3 "$HASH_HELPER" --skills-dir "$HOME/.agents/skills" --lock-file "$FIX/vercel-sample-lock.json" 2>&1)
vercel_sample_status=$?
if [ "$vercel_sample_status" -eq 0 ] && [ -z "$vercel_sample_output" ]; then
  echo "PASS: Vercel source full-tree lock uses runtime projection baseline"; pass=$((pass+1))
else
  echo "FAIL: Vercel source projection baseline mismatch: $vercel_sample_output"; fail=$((fail+1))
fi

python3 - "$HOME/.agents/.skill-lock.json" "$FIX/accepted-mirror-lock.json" <<'PY'
import json
import sys
from pathlib import Path

source = json.loads(Path(sys.argv[1]).read_text())
names = (
    "vercel-composition-patterns",
    "vercel-react-best-practices",
    "vercel-react-native-skills",
    "caveman",
    "find-skills",
    "grill-me",
    "grill-with-docs",
    "strategic-compact",
    "zoom-out",
    "design-taste-frontend",
)
entries = {name: source["skills"][name] for name in names}
Path(sys.argv[2]).write_text(json.dumps({"version": source["version"], "skills": entries}))
PY
accepted_mirror_output=$(python3 "$HASH_HELPER" --skills-dir "$HOME/.agents/skills" --lock-file "$FIX/accepted-mirror-lock.json" 2>&1)
accepted_mirror_status=$?
if [ "$accepted_mirror_status" -eq 0 ] && [ -z "$accepted_mirror_output" ]; then
  echo "PASS: accepted fork/mirror baselines are quiet"; pass=$((pass+1))
else
  echo "FAIL: accepted fork/mirror baseline mismatch: $accepted_mirror_output"; fail=$((fail+1))
fi

mkdir -p "$FIX/accepted-tamper"
for accepted_name in vercel-composition-patterns vercel-react-best-practices vercel-react-native-skills caveman find-skills grill-me grill-with-docs strategic-compact zoom-out design-taste-frontend; do
  cp -R "$HOME/.agents/skills/$accepted_name" "$FIX/accepted-tamper/$accepted_name"
  printf '\ntampered runtime byte\n' >> "$FIX/accepted-tamper/$accepted_name/SKILL.md"
done
tampered_mirror_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/accepted-tamper" --lock-file "$FIX/accepted-mirror-lock.json" 2>&1)
tampered_mirror_status=$?
tampered_mirror_count=$(printf '%s\n' "$tampered_mirror_output" | grep -c 'installed skill 與 lock hash 不符' || true)
if [ "$tampered_mirror_status" -eq 0 ] && [ "$tampered_mirror_count" -eq 10 ]; then
  echo "PASS: every accepted baseline runtime tamper reports"; pass=$((pass+1))
else
  echo "FAIL: accepted baseline runtime tamper count was $tampered_mirror_count: $tampered_mirror_output"; fail=$((fail+1))
fi

python3 - "$HASH_HELPER" <<'PY'
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location("drift", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as temp:
  skills = Path(temp) / "skills"
  skill = skills / "stable-skill"
  skill.mkdir(parents=True)
  (skill / "SKILL.md").write_text("stable\n")
  root_fd = os.open(skills, os.O_RDONLY | os.O_DIRECTORY)
  try:
    expected_runtime = module.hash_skill(root_fd, "stable-skill", module.INSTALLER_EXCLUDED_PATHS)
  finally:
    os.close(root_fd)
  entry = module.LockEntry(
    "stable-skill",
    "stable-skill",
    "0000000000000000000000000000000000000000",
    "fixture/skills",
    "https://example.test/fixture.git",
    "skills/stable-skill/SKILL.md",
  )
  baseline = {
    (entry.directory, entry.source, entry.source_url, entry.skill_path, entry.expected_hash): expected_runtime
  }
  original_hash = module.hash_skill
  calls = [0]
  def unstable_hash(root_fd, directory, excluded_paths=frozenset()):
    result = original_hash(root_fd, directory, excluded_paths=excluded_paths)
    calls[0] += 1
    if calls[0] == 2:
      (skill / "SKILL.md").write_text("stable\ntampered\n")
    return result
  module.hash_skill = unstable_hash
  try:
    module.verify(skills, (entry,), baseline)
  except module.VerificationError:
    raise SystemExit(0)
  raise SystemExit(1)
PY
stability_probe_status=$?
if [ "$stability_probe_status" -eq 0 ]; then
  echo "PASS: concurrent runtime mutation fails closed"; pass=$((pass+1))
else
  echo "FAIL: concurrent runtime mutation was not detected"; fail=$((fail+1))
fi

python3 - "$FIX/extra-baseline.json" <<'PY'
import json
import sys
from pathlib import Path

baseline_path = Path("/Users/linhancheng/code/social-info/scripts/local-analysis/skill-lock-source-baseline.json")
data = json.loads(baseline_path.read_text())
extra = dict(data["entries"][0])
extra["skill"] = "improve"
Path(sys.argv[1]).write_text(json.dumps({"version": data["version"], "entries": data["entries"] + [extra]}))
PY
baseline_scope_probe() {
  python3 - "$HASH_HELPER" "$1" "$HOME/.agents/.skill-lock.json" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("drift", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
entries = module.load_lock(sys.argv[3])
try:
  module.load_baseline(Path(sys.argv[2]), entries)
except module.VerificationError:
  raise SystemExit(0)
raise SystemExit(1)
PY
}
baseline_extra_status=0
baseline_scope_probe "$FIX/extra-baseline.json" || baseline_extra_status=$?
if [ "$baseline_extra_status" -eq 0 ]; then
  echo "PASS: baseline scope rejects extra entries"; pass=$((pass+1))
else
  echo "FAIL: baseline scope accepted an extra entry"; fail=$((fail+1))
fi

mkdir -p "$FIX/symlink-root/safe-link-skill" "$FIX/outside"
printf 'value\n' > "$FIX/symlink-root/safe-link-skill/target.txt"
ln -s target.txt "$FIX/symlink-root/safe-link-skill/link.txt"
printf 'secret-content\n' > "$FIX/outside/secret.txt"
cat > "$FIX/safe-link-lock.json" <<'JSON'
{"version":3,"skills":{"safe-link-skill":{"skillFolderHash":"e33dfa8fd1ad31ebef9f0190aeb908d087f83305"}}}
JSON
safe_link_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/safe-link-lock.json" 2>&1)
safe_link_status=$?
if [ "$safe_link_status" -eq 0 ] && [ -z "$safe_link_output" ]; then
  echo "PASS: safe symlink hashes as Git tree entry without following"; pass=$((pass+1))
else
  echo "FAIL: safe symlink hash mismatch: $safe_link_output"; fail=$((fail+1))
fi

mkdir -p "$FIX/symlink-root/crlf-skill"
printf 'original\r\n' > "$FIX/symlink-root/crlf-skill/SKILL.md"
cat > "$FIX/crlf-lock.json" <<'JSON'
{"version":3,"skills":{"crlf-skill":{"skillFolderHash":"3afbb6d34ff6516a8e15e798778d89b18ea75a1a"}}}
JSON
crlf_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/crlf-lock.json" 2>&1)
crlf_status=$?
if [ "$crlf_status" -eq 0 ] && [ -z "$crlf_output" ]; then
  echo "PASS: CRLF bytes match Git tree hash without normalization"; pass=$((pass+1))
else
  echo "FAIL: CRLF hashing diverged from Git tree: $crlf_output"; fail=$((fail+1))
fi

mkdir -p "$FIX/symlink-root/outside-link-skill"
ln -s ../../outside/secret.txt "$FIX/symlink-root/outside-link-skill/leak.txt"
cat > "$FIX/outside-link-lock.json" <<'JSON'
{"version":3,"skills":{"outside-link-skill":{"skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
outside_link_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/outside-link-lock.json" 2>&1)
outside_link_status=$?
if [ "$outside_link_status" -eq 2 ] && [[ "$outside_link_output" == *"驗證失敗"* ]] && [[ "$outside_link_output" != *"secret-content"* ]] && [[ "$outside_link_output" != *"$FIX/outside"* ]]; then
  echo "PASS: outside-root symlink fails safely without leaking path or content"; pass=$((pass+1))
else
  echo "FAIL: outside-root symlink handling unsafe: $outside_link_output"; fail=$((fail+1))
fi

ln -s "$FIX/outside" "$FIX/symlink-root/top-link"
cat > "$FIX/top-link-lock.json" <<'JSON'
{"version":3,"skills":{"top-link":{"skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
top_link_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/top-link-lock.json" 2>&1)
top_link_status=$?
if [ "$top_link_status" -eq 2 ] && [[ "$top_link_output" == *"驗證失敗"* ]] && [[ "$top_link_output" != *"$FIX/outside"* ]]; then
  echo "PASS: top-level skill symlink outside root fails safely"; pass=$((pass+1))
else
  echo "FAIL: top-level symlink handling unsafe: $top_link_output"; fail=$((fail+1))
fi

cat > "$FIX/traversal-lock.json" <<'JSON'
{"version":3,"skills":{"../outside":{"skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
traversal_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/traversal-lock.json" 2>&1)
traversal_status=$?
if [ "$traversal_status" -eq 2 ] && [[ "$traversal_output" == *"驗證失敗"* ]] && [[ "$traversal_output" != *"$FIX/outside"* ]]; then
  echo "PASS: traversal lock entry fails before outside-root read"; pass=$((pass+1))
else
  echo "FAIL: traversal entry handling unsafe: $traversal_output"; fail=$((fail+1))
fi

python3 - "$FIX/deep-root/deep-skill" <<'PY'
import os
import sys
from pathlib import Path

root = Path(sys.argv[1])
root.mkdir(parents=True)
flags = os.O_RDONLY | os.O_DIRECTORY
fds = [os.open(root, flags)]
try:
  for _ in range(140):
    os.mkdir("d", dir_fd=fds[-1])
    fds.append(os.open("d", flags, dir_fd=fds[-1]))
finally:
  for descriptor in reversed(fds):
    os.close(descriptor)
PY
cat > "$FIX/deep-lock.json" <<'JSON'
{"version":3,"skills":{"deep-skill":{"skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
python3 "$HASH_HELPER" --skills-dir "$FIX/deep-root" --lock-file "$FIX/deep-lock.json" > "$FIX/deep.out" 2> "$FIX/deep.err"
deep_status=$?
if [ "$deep_status" -eq 2 ] && grep -Fxq -- '- ⚠️ installed skill lock 驗證失敗 — lock schema、entry 或 skill 路徑不安全' "$FIX/deep.out" && [ ! -s "$FIX/deep.err" ]; then
  echo "PASS: excessive tree depth fails with fixed secret-safe output"; pass=$((pass+1))
else
  echo "FAIL: excessive tree depth did not fail closed"; fail=$((fail+1))
fi

python3 - "$FIX/oversized-lock.json" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps({"version": 3, "skills": {}, "padding": "x" * 1100000}))
PY
python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/oversized-lock.json" > "$FIX/oversized.out" 2> "$FIX/oversized.err"
oversized_status=$?
if [ "$oversized_status" -eq 2 ] && grep -Fxq -- '- ⚠️ installed skill lock 驗證失敗 — lock schema、entry 或 skill 路徑不安全' "$FIX/oversized.out" && [ ! -s "$FIX/oversized.err" ]; then
  echo "PASS: oversized valid lock fails before unbounded JSON load"; pass=$((pass+1))
else
  echo "FAIL: oversized valid lock was read without a size bound"; fail=$((fail+1))
fi

printf '{' > "$FIX/growing-lock.json"
python3 - "$HASH_HELPER" "$FIX/growing-lock.json" <<'PY'
import importlib.util
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("drift", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original_read = module.os.read
chunks = iter((b"{", b"}"))
module.os.read = lambda _descriptor, _size: next(chunks, b"")
try:
  module.read_json_file(Path(sys.argv[2]), 1)
except module.VerificationError:
  raise SystemExit(0)
finally:
  module.os.read = original_read
raise SystemExit(1)
PY
growing_status=$?
if [ "$growing_status" -eq 0 ]; then
  echo "PASS: same-FD read catches post-fstat growth"; pass=$((pass+1))
else
  echo "FAIL: same-FD read accepted an oversized growth"; fail=$((fail+1))
fi

python3 - "$FIX/huge-number-lock.json" <<'PY'
import sys
from pathlib import Path

Path(sys.argv[1]).write_text('{"version":' + ('9' * 5000) + ',"skills":{}}')
PY
python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/huge-number-lock.json" > "$FIX/huge-number.out" 2> "$FIX/huge-number.err"
huge_number_status=$?
if [ "$huge_number_status" -eq 2 ] && grep -Fxq -- '- ⚠️ installed skill lock 驗證失敗 — lock schema、entry 或 skill 路徑不安全' "$FIX/huge-number.out" && [ ! -s "$FIX/huge-number.err" ]; then
  echo "PASS: oversized JSON number fails with fixed secret-safe output"; pass=$((pass+1))
else
  echo "FAIL: oversized JSON number leaked traceback or wrong status"; fail=$((fail+1))
fi

cat > "$FIX/partial-source-lock.json" <<'JSON'
{"version":3,"skills":{"partial":{"source":"fixture","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
partial_source_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/partial-source-lock.json" 2>&1)
partial_source_status=$?
if [ "$partial_source_status" -eq 2 ] && [[ "$partial_source_output" == *"驗證失敗"* ]]; then
  echo "PASS: partial source metadata fails closed"; pass=$((pass+1))
else
  echo "FAIL: partial source metadata was accepted"; fail=$((fail+1))
fi

printf '{"version":999,"skills":{}}\n' > "$FIX/unknown-lock.json"
printf '{"version":3,"skills":' > "$FIX/malformed-lock.json"
unknown_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/unknown-lock.json" 2>&1)
unknown_status=$?
malformed_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/malformed-lock.json" 2>&1)
malformed_status=$?
if [ "$unknown_status" -eq 2 ] && [ "$malformed_status" -eq 2 ] && [[ "$unknown_output" == *"驗證失敗"* ]] && [[ "$malformed_output" == *"驗證失敗"* ]]; then
  echo "PASS: unknown and malformed lock schemas fail safely"; pass=$((pass+1))
else
  echo "FAIL: malformed lock handling did not fail closed"; fail=$((fail+1))
fi

ln -s "$FIX/safe-link-lock.json" "$FIX/symlink-lock.json"
symlink_lock_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/symlink-lock.json" 2>&1)
symlink_lock_status=$?
missing_baseline_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/safe-link-lock.json" --baseline-file "$FIX/missing-baseline.json" 2>&1)
missing_baseline_status=$?
if [ "$symlink_lock_status" -eq 2 ] && [ "$missing_baseline_status" -eq 2 ] \
  && [[ "$symlink_lock_output" == *"驗證失敗"* ]] \
  && [[ "$missing_baseline_output" == *"驗證失敗"* ]] \
  && [[ "$symlink_lock_output" != *"Traceback"* ]] \
  && [[ "$missing_baseline_output" != *"Traceback"* ]]; then
  echo "PASS: unsafe lock paths and missing baselines fail closed"; pass=$((pass+1))
else
  echo "FAIL: unsafe lock path or missing baseline escaped fail-closed handling"; fail=$((fail+1))
fi

cat > "$FIX/duplicate-lock.json" <<'JSON'
{"version":3,"skills":{"duplicate":{"skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc","skillFolderHash":""}}}
JSON
duplicate_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/duplicate-lock.json" 2>&1)
duplicate_status=$?
cat > "$FIX/duplicate-baseline.json" <<'JSON'
{"version":1,"scope":["caveman"],"entries":[{"skill":"caveman","source":"mattpocock/skills","sourceUrl":"https://github.com/mattpocock/skills.git","skillPath":"skills/productivity/caveman/SKILL.md","sourceTreeHash":"17972a1bdbf5909b9dbdc435ad348f0bf45f8664","runtimeTreeHash":"3a83c0077deff918cf33168302c876ec25ba7a06","provenanceCommit":"392c65fa7c16ea4d837ca4a3730f82425628762d","provenanceReason":"fixture","runtimeTreeHash":"0000000000000000000000000000000000000000"}]}
JSON
baseline_duplicate_status=0
baseline_scope_probe "$FIX/duplicate-baseline.json" || baseline_duplicate_status=$?
cat > "$FIX/malformed-source-url-lock.json" <<'JSON'
{"version":3,"skills":{"malformed-url":{"source":"fixture","sourceUrl":"file://[","skillPath":"skills/x/SKILL.md","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
malformed_source_url_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/malformed-source-url-lock.json" 2>&1)
malformed_source_url_status=$?
cat > "$FIX/empty-host-url-lock.json" <<'JSON'
{"version":3,"skills":{"empty-host":{"source":"fixture","sourceUrl":"https://","skillPath":"skills/x/SKILL.md","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
empty_host_url_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/empty-host-url-lock.json" 2>&1)
empty_host_url_status=$?
cat > "$FIX/relative-url-lock.json" <<'JSON'
{"version":3,"skills":{"relative-url":{"source":"fixture","sourceUrl":"not-a-url","skillPath":"skills/x/SKILL.md","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
relative_url_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/relative-url-lock.json" 2>&1)
relative_url_status=$?
cat > "$FIX/empty-scp-url-lock.json" <<'JSON'
{"version":3,"skills":{"empty-scp":{"source":"fixture","sourceUrl":"git@:","skillPath":"skills/x/SKILL.md","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
empty_scp_url_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/empty-scp-url-lock.json" 2>&1)
empty_scp_url_status=$?
cat > "$FIX/host-only-scp-url-lock.json" <<'JSON'
{"version":3,"skills":{"host-only-scp":{"source":"fixture","sourceUrl":"git@host:","skillPath":"skills/x/SKILL.md","skillFolderHash":"69b20525656d07c110596c0dc80bdb92893838dc"}}}
JSON
host_only_scp_url_output=$(python3 "$HASH_HELPER" --skills-dir "$FIX/symlink-root" --lock-file "$FIX/host-only-scp-url-lock.json" 2>&1)
host_only_scp_url_status=$?
if [ "$duplicate_status" -eq 2 ] && [ "$baseline_duplicate_status" -eq 0 ] \
  && [ "$malformed_source_url_status" -eq 2 ] \
  && [ "$empty_scp_url_status" -eq 2 ] && [ "$host_only_scp_url_status" -eq 2 ] \
  && [ "$empty_host_url_status" -eq 2 ] && [ "$relative_url_status" -eq 2 ] \
  && [[ "$duplicate_output" == *"驗證失敗"* ]] \
  && [[ "$malformed_source_url_output" == *"驗證失敗"* ]] \
  && [[ "$empty_host_url_output" == *"驗證失敗"* ]] \
  && [[ "$relative_url_output" == *"驗證失敗"* ]] \
  && [[ "$empty_scp_url_output" == *"驗證失敗"* ]] \
  && [[ "$host_only_scp_url_output" == *"驗證失敗"* ]]; then
  echo "PASS: duplicate keys and malformed source URLs fail closed"; pass=$((pass+1))
else
  echo "FAIL: duplicate keys or malformed source URL bypassed fail-closed handling"; fail=$((fail+1))
fi

echo "----"
echo "${pass} PASS / ${fail} FAIL（report: ${report}）"
[ "$fail" -eq 0 ]
