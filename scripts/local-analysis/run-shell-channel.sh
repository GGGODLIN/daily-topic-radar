#!/bin/bash
set -euo pipefail

WRAPPER="$1"
OUT="$2"
ANALYSIS_DATE="$3"
FORCE="${4:-false}"
MARKER="$OUT.complete.sha256"
OUT_DIR="$(dirname "$OUT")"
OUT_NAME="$(basename "$OUT")"
MARKER_NAME="$(basename "$MARKER")"

mkdir -p "$OUT_DIR"

if [ "$FORCE" != "true" ] && [ -s "$OUT" ] && [ -s "$MARKER" ] && (
  cd "$OUT_DIR"
  shasum -a 256 -c "$MARKER_NAME" >/dev/null 2>&1
); then
  exit 0
fi

BACKUP="$OUT.previous"
rm -f "$MARKER"
if [ -e "$OUT" ]; then
  rm -f "$BACKUP"
  mv "$OUT" "$BACKUP"
fi
if ! LOCAL_ANALYSIS_DATE="$ANALYSIS_DATE" bash "$WRAPPER"; then
  rm -f "$OUT"
  exit 1
fi
if [ ! -s "$OUT" ]; then
  rm -f "$OUT"
  exit 1
fi
(
  cd "$OUT_DIR"
  shasum -a 256 "$OUT_NAME" > "$MARKER_NAME.tmp"
  mv "$MARKER_NAME.tmp" "$MARKER_NAME"
)
rm -f "$BACKUP"
