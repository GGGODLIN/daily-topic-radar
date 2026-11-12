#!/bin/bash
set -euo pipefail
SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/probes-daily.sh"
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
mkdir -p "$ROOT/reports/local-analysis" "$ROOT/logs"
printf '%s\n' '# Probes' > "$ROOT/PROBES.md"
MOCK="$ROOT/mock-claude.sh"
cat > "$MOCK" <<EOF
#!/bin/bash
printf '%s\n' "\$*" > "$ROOT/captured-prompt.txt"
printf '%s\n' "\$PATH" > "$ROOT/captured-path.txt"
printf '%s\n' 'stderr-noise-should-stay-in-log' >&2
printf '%s\n' '# Probes Report — mock'
EOF
chmod +x "$MOCK"

run() {
  SOCIAL_INFO_REPO_DIR="$ROOT" SOCIAL_INFO_CLAUDE="$MOCK" bash "$SCRIPT" >/dev/null 2>&1
}

run
[ ! -d "$ROOT/PROBES.md.lock" ]
command grep -qF "$ROOT/PROBES.md" "$ROOT/captured-prompt.txt"
command grep -qF "/opt/homebrew/bin" "$ROOT/captured-path.txt"
! command grep -qF "mcp__claude-in-chrome__" "$ROOT/captured-prompt.txt"
OUT_FILE=$(ls "$ROOT"/reports/local-analysis/*-probes.md | head -1)
! command grep -qF "stderr-noise-should-stay-in-log" "$OUT_FILE"
printf '%s\n' '✅ acquired lock, passed isolated REPO_DIR + homebrew PATH, and isolated stderr from report'

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
printf '%s\n' 'fatal stderr from claude' >&2
exit 7
EOF
chmod +x "$MOCK"
rm -f "$OUT_FILE"
if run; then
  printf '%s\n' 'failing mock unexpectedly succeeded' >&2
  exit 1
fi
[ ! -d "$ROOT/PROBES.md.lock" ]
[ ! -f "$OUT_FILE" ]
printf '%s\n' '✅ writer failure cleans lock and leaves no partial report file'
