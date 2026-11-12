"""Behavioral tests for fetch-youtube-transcript.sh and external-feeds-seen-check.sh."""
import json
import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
YT_SCRIPT = ROOT / "scripts" / "fetch-youtube-transcript.sh"
SEEN_SCRIPT = ROOT / "scripts" / "external-feeds-seen-check.sh"


def _make_stub_ytdlp(bin_dir: Path, vtt_content: str | None) -> Path:
    args_log = bin_dir / "ytdlp-args.txt"
    stub = bin_dir / "yt-dlp"
    vtt_file = bin_dir / "fixture.vtt"
    if vtt_content is not None:
        vtt_file.write_text(vtt_content, encoding="utf-8")
    body = f"""#!/bin/bash
printf '%s\\n' "$@" > "{args_log}"
out_tpl=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then
    out_tpl="$2"
    shift 2
  else
    shift
  fi
done
if [ -f "{vtt_file}" ] && [ -n "$out_tpl" ]; then
  target="${{out_tpl//%(ext)s/en-GB.vtt}}"
  cp "{vtt_file}" "$target"
fi
exit 0
"""
    stub.write_text(body, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return args_log


def test_youtube_transcript_decodes_entities_and_requests_manual_and_regional_en(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    vtt = (
        "WEBVTT\nKind: captions\nLanguage: en-GB\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "TypeScript Array&lt;string&gt; =&gt; don&#39;t repeat\n\n"
        "00:00:03.000 --> 00:00:05.000\n"
        "<c>TypeScript Array&lt;string&gt; =&gt; don&#39;t repeat</c>\n"
    )
    args_log = _make_stub_ytdlp(bin_dir, vtt)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    res = subprocess.run(
        ["bash", str(YT_SCRIPT), "https://www.youtube.com/watch?v=abc"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "TypeScript Array<string> => don't repeat"
    logged = args_log.read_text(encoding="utf-8")
    assert "--write-subs" in logged or "--write-sub" in logged
    assert "en.*,en" in logged


def test_youtube_transcript_exits_2_on_cue_free_vtt(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_stub_ytdlp(bin_dir, "WEBVTT\nKind: captions\nLanguage: en\n\n")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    res = subprocess.run(
        ["bash", str(YT_SCRIPT), "https://www.youtube.com/watch?v=empty"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 2


def test_youtube_transcript_survives_secondary_track_429_and_skips_empty_track(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "yt-dlp"
    stub.write_text(
        """#!/bin/bash
out_tpl=""
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out_tpl="$2"; shift 2; else shift; fi
done
printf 'WEBVTT\\nKind: captions\\nLanguage: en-CA\\n\\n' > "${out_tpl//%(ext)s/en-CA.vtt}"
printf 'WEBVTT\\n\\n00:00:01.000 --> 00:00:02.000\\nRecovered from en-orig\\n' > "${out_tpl//%(ext)s/en-orig.vtt}"
echo "ERROR: Unable to download video subtitles for 'en': HTTP Error 429: Too Many Requests" >&2
exit 1
""",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    res = subprocess.run(
        ["bash", str(YT_SCRIPT), "https://www.youtube.com/watch?v=partial429"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "Recovered from en-orig"


def test_external_feeds_seen_check_marks_same_day_raw_seen_and_ignores_future(tmp_path: Path):
    reports_dir = tmp_path / "reports"
    ext_dir = reports_dir / "external-feeds"
    ext_dir.mkdir(parents=True)

    (tmp_path / "sources.yml").write_text(
        "sources:\n  - id: twitter_tier1\n    type: twitter\n    enabled: true\n    handles: [karpathy, swyx]\n",
        encoding="utf-8",
    )
    (reports_dir / "2026-10-15.md").write_text(
        "### [Postmortem](https://anthropic.com/news/postmortem)\n",
        encoding="utf-8",
    )
    (reports_dir / "2026-10-16.md").write_text(
        "### [Future](https://openai.com/index/future-only)\n",
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-blogs-2026-10-15.json").write_text(
        json.dumps({
            "blogs": [
                {"url": "https://anthropic.com/news/postmortem", "publishedAt": "2026-10-15"},
                {"url": "https://openai.com/index/future-only", "publishedAt": "2026-10-15"},
            ]
        }),
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-x-2026-10-15.json").write_text(
        json.dumps({"x": [{"handle": "karpathy"}, {"handle": "rauchg"}]}),
        encoding="utf-8",
    )

    res = subprocess.run(
        ["bash", str(SEEN_SCRIPT), "2026-10-15"],
        capture_output=True,
        text=True,
        env={**os.environ, "SOCIAL_INFO_DIR": str(tmp_path)},
        check=False,
    )
    assert res.returncode == 0
    lines = res.stdout.splitlines()
    postmortem_line = next(line for line in lines if "https://anthropic.com/news/postmortem" in line)
    future_line = next(line for line in lines if "https://openai.com/index/future-only" in line)
    assert "verdict=ALREADY_SEEN" in postmortem_line
    assert "2026-10-15.md" in postmortem_line
    assert "verdict=GENUINE_GAP" in future_line
    assert "BLOGS_SEEN_CHECK=OK" in res.stdout
    assert "X_HANDLE_OVERLAP=OK" in res.stdout
