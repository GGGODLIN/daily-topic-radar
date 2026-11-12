#!/bin/bash
set -euo pipefail
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bumblebee-daily.sh"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

mkdir -p "$ROOT/vendor/bumblebee/bin" "$ROOT/vendor/bumblebee/catalog/threat_intel" "$ROOT/bin"
echo '{"entries":[{"id":"e1"}]}' > "$ROOT/vendor/bumblebee/catalog/threat_intel/c.json"
git -C "$ROOT/vendor/bumblebee/catalog" init -q
git -C "$ROOT/vendor/bumblebee/catalog" config user.email "test@example.com"
git -C "$ROOT/vendor/bumblebee/catalog" config user.name "Test"
git -C "$ROOT/vendor/bumblebee/catalog" add .
git -C "$ROOT/vendor/bumblebee/catalog" commit -q -m "init"

cat > "$ROOT/bin/gh" <<'EOF'
#!/bin/bash
echo "v0.1.2"
EOF
chmod +x "$ROOT/bin/gh"

# Case 1: findings > 0 writes ALERT-bumblebee.md
cat > "$ROOT/vendor/bumblebee/bin/bumblebee" <<'EOF'
#!/bin/bash
if [ "${1:-}" = "--version" ]; then echo "bumblebee v0.1.2"; exit 0; fi
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--output-file" ]; then out="$2"; shift 2; else shift; fi
done
cat > "$out" <<'NDJSON'
{"record_type":"finding","package_name":"bad.ext","version":"1.0.0","ecosystem":"editor-extension","catalog_name":"bad","catalog_id":"id-1","source_file":"/tmp/pkg.json","severity":"critical"}
{"record_type":"scan_summary","status":"complete","package_records_suppressed":100}
NDJSON
EOF
chmod +x "$ROOT/vendor/bumblebee/bin/bumblebee"

BUMBLEBEE_REPO_DIR="$ROOT" BUMBLEBEE_NO_NOTIFY=1 LOCAL_ANALYSIS_DATE="2026-11-11" bash "$SCRIPT" >/dev/null
[ -f "$ROOT/ALERT-bumblebee.md" ]
command grep -qF "bad.ext@1.0.0" "$ROOT/ALERT-bumblebee.md"
echo "✅ findings > 0 creates ALERT-bumblebee.md"

# Case 2: failed scan does NOT erase existing ALERT-bumblebee.md
cat > "$ROOT/vendor/bumblebee/bin/bumblebee" <<'EOF'
#!/bin/bash
if [ "${1:-}" = "--version" ]; then echo "bumblebee v0.1.2"; exit 0; fi
exit 2
EOF
chmod +x "$ROOT/vendor/bumblebee/bin/bumblebee"

BUMBLEBEE_REPO_DIR="$ROOT" BUMBLEBEE_NO_NOTIFY=1 LOCAL_ANALYSIS_DATE="2026-11-12" bash "$SCRIPT" >/dev/null
[ -f "$ROOT/ALERT-bumblebee.md" ]
echo "✅ failed scan preserves existing ALERT-bumblebee.md"

# Case 3: complete scan with findings == 0 auto-clears stale ALERT-bumblebee.md
cat > "$ROOT/vendor/bumblebee/bin/bumblebee" <<'EOF'
#!/bin/bash
if [ "${1:-}" = "--version" ]; then echo "bumblebee v0.1.2"; exit 0; fi
out=""
while [ $# -gt 0 ]; do
  if [ "$1" = "--output-file" ]; then out="$2"; shift 2; else shift; fi
done
cat > "$out" <<'NDJSON'
{"record_type":"scan_summary","status":"complete","package_records_suppressed":24014}
NDJSON
EOF
chmod +x "$ROOT/vendor/bumblebee/bin/bumblebee"

BUMBLEBEE_REPO_DIR="$ROOT" BUMBLEBEE_NO_NOTIFY=1 LOCAL_ANALYSIS_DATE="2026-11-12" bash "$SCRIPT" >/dev/null
[ ! -f "$ROOT/ALERT-bumblebee.md" ]
echo "✅ clean complete scan removes stale ALERT-bumblebee.md"
