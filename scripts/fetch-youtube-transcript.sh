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
#   2 = 影片存在但沒有 en 字幕軌，或字幕軌無可用語音 cue
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

TMPDIR_LOCAL=$(mktemp -d) || { echo "mktemp failed" >&2; exit 1; }
trap 'rc=$?; rm -rf "$TMPDIR_LOCAL"; exit $rc' EXIT

yt-dlp --ignore-errors --no-update --no-playlist --skip-download --write-subs --write-auto-sub \
  --sub-langs "en.*,en" --socket-timeout 20 \
  --sub-format vtt -o "$TMPDIR_LOCAL/sub.%(ext)s" -- "$URL" >"$TMPDIR_LOCAL/log" 2>&1
yt_rc=$?

shopt -s nullglob
vtt_files=("$TMPDIR_LOCAL"/sub*.vtt)
shopt -u nullglob

if [ "${#vtt_files[@]}" -eq 0 ]; then
  if [ "$yt_rc" -ne 0 ] || command grep -q '^ERROR:' "$TMPDIR_LOCAL/log" 2>/dev/null; then
    echo "yt-dlp failed for $URL" >&2
    tail -5 "$TMPDIR_LOCAL/log" >&2
    exit 1
  fi
  echo "no en subtitle track for $URL" >&2
  exit 2
fi

python3 -I - "$TMPDIR_LOCAL" <<'PY'
import glob
import html
import os
import re
import sys

tmpdir = sys.argv[1]


def priority(path: str) -> tuple[int, str]:
    name = os.path.basename(path)
    if name == "sub.en.vtt":
        return (0, name)
    if "orig" in name:
        return (2, name)
    return (1, name)


_VTT_TIMING_RE = re.compile(
    r"^(?:\d{1,}:)?\d{2}:\d{2}\.\d{3}\s+-->\s+(?:\d{1,}:)?\d{2}:\d{2}\.\d{3}(?:[ \t].*)?$"
)
_VTT_META_BLOCK_RE = re.compile(r"^(?:WEBVTT|NOTE|STYLE|REGION)(?:$|[ \t])")


def extract_cues(path: str) -> list[str]:
    raw = open(path, encoding="utf-8", errors="replace").read()
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    out: list[str] = []
    for block in re.split(r"\n[ \t]*\n", normalized):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        if _VTT_META_BLOCK_RE.match(lines[0]):
            continue
        if _VTT_TIMING_RE.match(lines[0]):
            payload_lines = lines[1:]
        elif len(lines) >= 2 and _VTT_TIMING_RE.match(lines[1]):
            payload_lines = lines[2:]
        else:
            continue
        for line in payload_lines:
            cleaned = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
            if cleaned and (not out or out[-1] != cleaned):
                out.append(cleaned)
    return out


for candidate in sorted(glob.glob(os.path.join(tmpdir, "sub*.vtt")), key=priority):
    cues = extract_cues(candidate)
    if cues:
        print(" ".join(cues))
        sys.exit(0)

print("subtitle track contained no spoken cues", file=sys.stderr)
sys.exit(2)
PY
