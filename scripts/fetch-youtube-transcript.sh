#!/bin/bash
# fetch-youtube-transcript.sh — 抓 YouTube 影片的自動字幕、吐去重後的純文字到 stdout
#
# 用途：stage-2 digest 的「🎓 Matt Pocock 動態」段需要影片內容才能產大綱，
# 但 YouTube Atom feed 只有標題 + 描述。這支補那一段。
#
# 為什麼是 script 不是塞進 workflow prompt：vtt 去重的 regex 每次讓 LLM 重寫會飄，
# 而且失敗需要穩定的 exit code 契約（digest 要靠它決定寫不寫「字幕抓取失敗」）。
#
# 退出碼契約（對齊 fetch-fallback.sh 的設計語言）：
#   0 = 字幕純文字在 stdout
#   1 = yt-dlp 失敗（影片不存在 / 網路 / yt-dlp 被 YouTube 結構改動打壞）
#   2 = 影片存在但沒有 en 字幕軌
#
# ⚠️ 已知維護面：yt-dlp 對 YouTube 結構改動敏感、會週期性壞掉。壞掉時本 script
# 回 exit 1，digest 必須把它寫進系統當天動態段，不准靜默跳過（否則影片大綱會悄悄消失）。
# 修法通常是 `pip install -U yt-dlp` 或 `brew upgrade yt-dlp`。

set -uo pipefail

URL="${1:-}"
if [ -z "$URL" ]; then
  echo "usage: $0 <youtube-url>" >&2
  exit 1
fi

TMPDIR_LOCAL=$(mktemp -d)
trap 'rc=$?; rm -rf "$TMPDIR_LOCAL"; exit $rc' EXIT

if ! yt-dlp --no-update --skip-download --write-auto-sub --sub-lang en \
     --sub-format vtt -o "$TMPDIR_LOCAL/sub.%(ext)s" "$URL" >"$TMPDIR_LOCAL/log" 2>&1; then
  echo "yt-dlp failed for $URL" >&2
  tail -5 "$TMPDIR_LOCAL/log" >&2
  exit 1
fi

VTT=$(find "$TMPDIR_LOCAL" -name 'sub*.vtt' -print -quit)
if [ -z "$VTT" ]; then
  echo "no en subtitle track for $URL" >&2
  exit 2
fi

python3 - "$VTT" <<'PY'
import re
import sys

raw = open(sys.argv[1], encoding="utf-8", errors="replace").read()
out = []
for line in raw.split("\n"):
    line = line.strip()
    if not line or "-->" in line:
        continue
    if line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
        continue
    if re.fullmatch(r"\d+", line):
        continue
    line = re.sub(r"<[^>]+>", "", line).strip()
    if line and (not out or out[-1] != line):
        out.append(line)
print(" ".join(out))
PY
