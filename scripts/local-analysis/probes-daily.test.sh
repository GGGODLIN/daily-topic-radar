#!/bin/bash
set -euo pipefail
SCRIPT="/Users/linhancheng/code/social-info/scripts/local-analysis/probes-daily.sh"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/reports/local-analysis" "$ROOT/logs"
printf '%s\n' '# Probes' > "$ROOT/PROBES.md"
MOCK="$ROOT/mock-claude.sh"
printf '%s\n' '#!/bin/bash' 'printf "%s\n" "# mock report"' > "$MOCK"
chmod +x "$MOCK"

run() {
  SOCIAL_INFO_REPO_DIR="$ROOT" SOCIAL_INFO_CLAUDE="$MOCK" bash "$SCRIPT" >/dev/null 2>&1
}

run
[ ! -d "$ROOT/PROBES.md.lock" ]
printf '%s\n' '✅ acquired lock, invoked mock writer, and cleaned lock'

mkdir "$ROOT/PROBES.md.lock"
if run; then
  printf '%s\n' 'writer unexpectedly ran while lock was held' >&2
  exit 1
fi
[ -d "$ROOT/PROBES.md.lock" ]
rmdir "$ROOT/PROBES.md.lock"
printf '%s\n' '✅ existing lock blocks writer with non-zero exit'

cat > "$MOCK" <<'EOF'
#!/bin/bash
exit 7
EOF
chmod +x "$MOCK"
if run; then
  printf '%s\n' 'failing mock unexpectedly succeeded' >&2
  exit 1
fi
[ ! -d "$ROOT/PROBES.md.lock" ]
printf '%s\n' '✅ writer failure still cleans lock'
