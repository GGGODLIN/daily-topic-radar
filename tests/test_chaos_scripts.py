"""Chaos regression tests for scripts/ covering C1-C15 offline defect contracts."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUXDO_SCRIPT = ROOT / "scripts" / "digest-linuxdo-batches.py"
CHECK_TEMPLATE_SCRIPT = ROOT / "scripts" / "check-digest-template.sh"
VPN_SCRIPT = ROOT / "scripts" / "vpn-precheck.sh"
SEEN_SCRIPT = ROOT / "scripts" / "external-feeds-seen-check.sh"
YT_SCRIPT = ROOT / "scripts" / "fetch-youtube-transcript.sh"
DRY_RUN_SCRIPT = ROOT / "scripts" / "dry-run-n-value.py"
RUN_DAILY_SCRIPT = ROOT / "scripts" / "run-daily.sh"


def _run_linuxdo(raw: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LINUXDO_SCRIPT), "--raw", str(raw), "--out", str(out)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_c1_linuxdo_batches_handles_crlf_line_endings(tmp_path: Path) -> None:
    raw = tmp_path / "raw-crlf.md"
    out = tmp_path / "out"
    content = (
        "# daily\r\n\r\n"
        "## LINUX DO\r\n\r\n"
        "### [Post 1](https://linux.do/t/topic/1)\r\n\r\n"
        "`linuxdo:test` · T2\r\n\r\n"
        "> First post body\r\n\r\n"
        "---\r\n"
    )
    raw.write_bytes(content.encode("utf-8"))

    res = _run_linuxdo(raw, out)
    assert res.returncode == 0, res.stderr or res.stdout
    meta = json.loads(res.stdout)
    assert meta["entry_count"] == 1
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["batches"][0]["topic_urls"] == ["https://linux.do/t/topic/1"]


def test_c2_linuxdo_batches_ignores_u2028_fake_headings_and_sections(tmp_path: Path) -> None:
    raw_h = tmp_path / "raw-heading.md"
    out_h = tmp_path / "out-heading"
    raw_h.write_text(
        "# daily\n\n"
        "## LINUX DO (2 items)\n\n"
        "### [Post 1](https://linux.do/t/topic/1)\n\n"
        "Line before ### Fake Heading\nMore text\n\n---\n"
        "### [Post 2](https://linux.do/t/topic/2)\n\n"
        "Second real entry\n\n---\n\n"
        "## Tail Section\n\nKeep tail\n",
        encoding="utf-8",
    )
    res_h = _run_linuxdo(raw_h, out_h)
    assert res_h.returncode == 0, res_h.stderr or res_h.stdout
    meta_h = json.loads(res_h.stdout)
    assert meta_h["entry_count"] == 2
    manifest_h = json.loads((out_h / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_h["batches"][0]["topic_urls"] == [
        "https://linux.do/t/topic/1",
        "https://linux.do/t/topic/2",
    ]

    raw_s = tmp_path / "raw-section.md"
    out_s = tmp_path / "out-section"
    raw_s.write_text(
        "# daily\n\n"
        "## LINUX DO (2 items)\n\n"
        "### [Post 1](https://linux.do/t/topic/1)\n\n"
        "Line before ## Fake Section\nMore text\n\n---\n"
        "### [Post 2](https://linux.do/t/topic/2)\n\n"
        "Second real entry must stay in linuxdo\n\n---\n\n"
        "## Tail Section\n\nKeep tail\n",
        encoding="utf-8",
    )
    res_s = _run_linuxdo(raw_s, out_s)
    assert res_s.returncode == 0, res_s.stderr or res_s.stdout
    meta_s = json.loads(res_s.stdout)
    assert meta_s["entry_count"] == 2
    non_linuxdo = (out_s / "non-linuxdo.md").read_text(encoding="utf-8")
    assert "Post 2" not in non_linuxdo
    assert "Second real entry must stay in linuxdo" not in non_linuxdo
    assert "## Tail Section" in non_linuxdo


def test_c3_linuxdo_batches_invalid_utf8_returns_json_error_without_traceback(tmp_path: Path) -> None:
    raw = tmp_path / "bad-utf8.md"
    out = tmp_path / "out"
    raw.write_bytes(b"\xff\xfe## LINUX DO\n")

    res = _run_linuxdo(raw, out)
    assert res.returncode == 1
    assert "Traceback" not in res.stderr
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert isinstance(payload.get("err"), str) and payload["err"]


def test_c4_linuxdo_batches_parses_topic_url_with_trailing_suffixes(tmp_path: Path) -> None:
    raw = tmp_path / "raw-suffix.md"
    out = tmp_path / "out"
    raw.write_text(
        "# daily\n\n"
        "## LINUX DO (2 items)\n\n"
        "### [Title](https://linux.do/t/topic/123) (2026-11-21)\n\n"
        "> First body\n\n---\n"
        "### [Title](https://linux.do/t/topic/456) [hot]\n\n"
        "> Second body\n\n---\n",
        encoding="utf-8",
    )

    res = _run_linuxdo(raw, out)
    assert res.returncode == 0, res.stderr or res.stdout
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["batches"][0]["topic_urls"] == [
        "https://linux.do/t/topic/123",
        "https://linux.do/t/topic/456",
    ]
    batch_payload = json.loads((out / "batch-001.json").read_text(encoding="utf-8"))
    assert [f["topic_url"] for f in batch_payload["fragments"]] == [
        "https://linux.do/t/topic/123",
        "https://linux.do/t/topic/456",
    ]


def test_linuxdo_batches_handles_paths_with_spaces(tmp_path: Path) -> None:
    space_dir = tmp_path / "dir with space"
    space_dir.mkdir()
    raw = space_dir / "raw input.md"
    out = space_dir / "out dir"
    raw.write_text(
        "# daily\n\n"
        "## LINUX DO (1 items)\n\n"
        "### [Space Path](https://linux.do/t/topic/789)\n\n"
        "> Body inside space dir\n\n---\n",
        encoding="utf-8",
    )

    res = _run_linuxdo(raw, out)
    assert res.returncode == 0, res.stderr or res.stdout
    meta = json.loads(res.stdout)
    assert meta["ok"] is True
    assert meta["entry_count"] == 1
    assert (out / "manifest.json").is_file()


def _make_html(
    *,
    style_tag: str = "<style>body { color: #111; }</style>",
    sub_html: str = '<div class="sub">2026-11-21｜共 10 items</div>',
    body_extra: str = "<table>\n  <tr><td>ok</td></tr>\n</table>",
) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-Hant">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"{style_tag}\n"
        "</head>\n"
        "<body>\n"
        f"{sub_html}\n"
        f"{body_extra}\n"
        "</body>\n"
        "</html>\n"
    )


def _run_check_template(
    html_arg: str,
    raw_md: Path,
    *,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ALLOW_MISSING_EXTERNAL_VERIFY": "1",
        "VERIFY_DIGEST_SH": "/nonexistent",
    }
    return subprocess.run(
        ["bash", str(CHECK_TEMPLATE_SCRIPT), html_arg, str(raw_md)],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("css", "expected_rc"),
    [
        ("table.wide { overflow: hidden; }", 1),
        ("div.wrap { overflow: hidden; }", 1),
        (".wrap.main { overflow: hidden; }", 1),
        ("table { overflow-x: hidden; }", 1),
        ("/* .wrap { overflow: hidden; } */", 0),
        ("/* { */ table { overflow: hidden; }", 1),
    ],
)
def test_c5_check_digest_template_overflow_rules_and_comments(
    tmp_path: Path, css: str, expected_rc: int
) -> None:
    raw_md = tmp_path / "raw.md"
    raw_md.write_text("# raw\n", encoding="utf-8")
    html_file = tmp_path / "digest.html"
    html_file.write_text(_make_html(style_tag=f"<style>{css}</style>"), encoding="utf-8")

    res = _run_check_template(str(html_file), raw_md)
    assert res.returncode == expected_rc, f"css={css!r} stdout={res.stdout} stderr={res.stderr}"


@pytest.mark.parametrize(
    "sub_html",
    [
        '<div class="sub">2026-11-21｜<span>共 10 items</span></div>',
        '<div class="sub">2026-11-21｜<strong>共 10 items</strong></div>',
        "<div class='sub'>2026-11-21｜共 10 items</div>",
    ],
)
def test_c6_check_digest_template_sub_allows_inline_tags_and_single_quotes(
    tmp_path: Path, sub_html: str
) -> None:
    raw_md = tmp_path / "raw.md"
    raw_md.write_text("# raw\n", encoding="utf-8")
    html_file = tmp_path / "digest.html"
    html_file.write_text(_make_html(sub_html=sub_html), encoding="utf-8")

    res = _run_check_template(str(html_file), raw_md)
    assert res.returncode == 0, res.stderr or res.stdout


def test_c7_check_digest_template_case_comments_and_leading_dash_filename(tmp_path: Path) -> None:
    raw_md = tmp_path / "raw.md"
    raw_md.write_text("# raw\n", encoding="utf-8")

    upper_style = tmp_path / "upper-style.html"
    upper_style.write_text(
        _make_html(style_tag='<STYLE type="text/css">body { color: #111; }</STYLE>'),
        encoding="utf-8",
    )
    res_style = _run_check_template(str(upper_style), raw_md)
    assert res_style.returncode == 0, res_style.stderr or res_style.stdout

    upper_tr = tmp_path / "upper-tr.html"
    upper_tr.write_text(
        _make_html(body_extra="<table><TR><TD>a</TD></TR><TR><TD>b</TD></TR></table>"),
        encoding="utf-8",
    )
    res_tr = _run_check_template(str(upper_tr), raw_md)
    assert res_tr.returncode == 1, res_tr.stdout

    comment_tr = tmp_path / "comment-tr.html"
    comment_tr.write_text(
        _make_html(body_extra="<!-- </tr><tr> -->\n<table>\n<tr><td>ok</td></tr>\n</table>"),
        encoding="utf-8",
    )
    res_comment = _run_check_template(str(comment_tr), raw_md)
    assert res_comment.returncode == 0, res_comment.stderr or res_comment.stdout

    dash_bad = tmp_path / "-bad.html"
    dash_bad.write_text(
        _make_html(body_extra="<table><tr><td>a</td></tr><tr><td>b</td></tr></table>"),
        encoding="utf-8",
    )
    res_dash = _run_check_template("-bad.html", raw_md, cwd=tmp_path)
    assert res_dash.returncode == 1, f"stdout={res_dash.stdout} stderr={res_dash.stderr}"


def test_c8_vpn_precheck_null_org_returns_unknown_20(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text(
        '#!/bin/bash\nprintf \'%s\' \'{"ip":"1.2.3.4","city":null,"country":null,"org":null}\'\n',
        encoding="utf-8",
    )
    fake_curl.chmod(fake_curl.stat().st_mode | stat.S_IEXEC)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    env.pop("VPN_PRECHECK_FAKE_ORG", None)
    res = subprocess.run(
        ["bash", str(VPN_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 20, f"stdout={res.stdout} stderr={res.stderr}"
    assert "UNKNOWN:" in res.stdout


@pytest.mark.parametrize(
    "fake_org",
    [
        "AS80751 Normal ISP",
        "AS90092 Residential Net",
        "AS12345 Lawson Telecom",
        "AS12345 Dawson Fiber",
    ],
)
def test_c9_vpn_precheck_no_false_positive_on_asn_or_aws_substrings(fake_org: str) -> None:
    env = {**os.environ, "VPN_PRECHECK_FAKE_ORG": fake_org}
    res = subprocess.run(
        ["bash", str(VPN_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, f"fake_org={fake_org!r} stdout={res.stdout}"
    assert "OK:" in res.stdout


def test_c12_external_feeds_seen_check_whitespace_url_and_prefix_match(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    ext_dir = reports_dir / "external-feeds"
    ext_dir.mkdir(parents=True)

    (tmp_path / "sources.yml").write_text("sources: []\n", encoding="utf-8")
    (reports_dir / "2026-11-21.md").write_text(
        "### [Post 1](https://example.com/blog/post-1)\n",
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-blogs-2026-11-21.json").write_text(
        json.dumps(
            {
                "blogs": [
                    {"url": "   ", "publishedAt": None},
                    {"url": "https://example.com/blog", "publishedAt": "2026-11-21"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-x-2026-11-21.json").write_text(
        json.dumps({"x": []}),
        encoding="utf-8",
    )

    res = subprocess.run(
        ["bash", str(SEEN_SCRIPT), "2026-11-21"],
        env={**os.environ, "SOCIAL_INFO_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    lines = res.stdout.splitlines()
    url_lines = [line for line in lines if line.startswith("URL=")]
    assert not any(line.split("|", 1)[0].strip() == "URL=" for line in url_lines), res.stdout

    blog_line = next(line for line in url_lines if "https://example.com/blog " in line)
    assert "verdict=GENUINE_GAP" in blog_line, res.stdout


def test_c13_external_feeds_seen_check_list_shaped_inputs_do_not_traceback(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    ext_dir = reports_dir / "external-feeds"
    ext_dir.mkdir(parents=True)

    (tmp_path / "sources.yml").write_text("- item1\n", encoding="utf-8")
    (ext_dir / "follow-builders-blogs-2026-11-21.json").write_text("[]", encoding="utf-8")
    (ext_dir / "follow-builders-x-2026-11-21.json").write_text("[]", encoding="utf-8")

    res = subprocess.run(
        ["bash", str(SEEN_SCRIPT), "2026-11-21"],
        env={**os.environ, "SOCIAL_INFO_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0
    assert "Traceback (most recent call last)" not in res.stderr
    assert "BLOGS_SEEN_CHECK=UNAVAILABLE" in res.stdout
    assert "X_HANDLE_OVERLAP=UNAVAILABLE" in res.stdout


def test_c14_fetch_youtube_transcript_vtt_blocks_spoken_tokens_and_ytdlp_flags(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    args_log = bin_dir / "ytdlp-args.txt"
    vtt_fixture = bin_dir / "fixture.vtt"
    vtt_fixture.write_text(
        "WEBVTT\n"
        "Kind: captions\n"
        "Language: en\n\n"
        "NOTE\n"
        "This is a multi-line comment\n"
        "Still inside NOTE block\n\n"
        "STYLE\n"
        "::cue {\n"
        "  color: papayawhip;\n"
        "}\n\n"
        "00:00:01.000 --> 00:00:03.000\n"
        "a --> b\n\n"
        "00:00:04.000 --> 00:00:06.000\n"
        "42\n",
        encoding="utf-8",
    )

    stub = bin_dir / "yt-dlp"
    stub.write_text(
        f"""#!/bin/bash
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
cp "{vtt_fixture}" "${{out_tpl//%(ext)s/en.vtt}}"
exit 0
""",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)

    target_url = "https://www.youtube.com/watch?v=chaos14"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}"}
    res = subprocess.run(
        ["bash", str(YT_SCRIPT), target_url],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    out = res.stdout.strip()
    assert "NOTE" not in out
    assert "multi-line comment" not in out
    assert "STYLE" not in out
    assert "::cue" not in out
    assert "papayawhip" not in out
    assert "a --> b" in out
    assert "42" in out

    logged_args = args_log.read_text(encoding="utf-8").splitlines()
    assert "--no-playlist" in logged_args
    assert "--" in logged_args
    assert logged_args.index("--") < logged_args.index(target_url)


def test_c15_dry_run_n_value_handles_empty_posted_at_tz_offset_and_missing_db(
    tmp_path: Path,
) -> None:
    db_dir = tmp_path / "with-db"
    db_dir.mkdir()
    db_path = db_dir / "state.db"
    now_iso = datetime.now(UTC).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE items (
            id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            canonical_url TEXT NOT NULL,
            title TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            source TEXT NOT NULL,
            source_handle TEXT,
            source_tier INTEGER,
            posted_at TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            author TEXT,
            excerpt TEXT,
            language TEXT,
            engagement_json TEXT,
            also_appeared_in TEXT,
            comments_json TEXT,
            last_surfaced_at TEXT
        )
        """
    )
    conn.execute(
        """
        INSERT INTO items (id, url, canonical_url, title, title_hash, source, posted_at, fetched_at, last_surfaced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("1", "https://example.com/1", "https://example.com/1", "t1", "h1", "rss", "", now_iso, None),
    )
    conn.execute(
        """
        INSERT INTO items (id, url, canonical_url, title, title_hash, source, posted_at, fetched_at, last_surfaced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "2",
            "https://example.com/2",
            "https://example.com/2",
            "t2",
            "h2",
            "rss",
            "2026-10-01T10:00:00+08:00",
            now_iso,
            "2026-10-01T10:00:00+08:00",
        ),
    )
    conn.commit()
    conn.close()

    res_ok = subprocess.run(
        [sys.executable, str(DRY_RUN_SCRIPT)],
        cwd=db_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_ok.returncode == 0, res_ok.stderr
    assert "Items with fetched_at within past 30d: 2" in res_ok.stdout

    empty_dir = tmp_path / "empty-dir"
    empty_dir.mkdir()
    res_missing = subprocess.run(
        [sys.executable, str(DRY_RUN_SCRIPT)],
        cwd=empty_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res_missing.returncode != 0
    assert not (empty_dir / "state.db").exists()


def test_c10_run_daily_watchdog_does_not_use_global_pkill_f_social_info() -> None:
    text = RUN_DAILY_SCRIPT.read_text(encoding="utf-8")
    code_lines = [
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    ]
    code_text = "\n".join(code_lines)
    assert not re.search(r"pkill\b[^\n]*-f[^\n]*social_info", code_text)
    assert "$AGG_PID" in code_text
    assert "TREE_PIDS" in code_text
    assert "trap '' TERM" in code_text


def test_markdown_and_linuxdo_batches_heading_roundtrip(tmp_path: Path) -> None:
    from datetime import datetime

    from social_info.fetchers.base import Item
    from social_info.markdown import render_item

    cases = [
        ("[分享] 新工具發布", "https://linux.do/t/topic/101"),
        ("a](b) c", "https://linux.do/t/topic/102"),
        ("trailing\\", "https://linux.do/t/topic/103"),
    ]
    rendered = []
    for title, url in cases:
        it = Item(
            title=title,
            url=url,
            canonical_url=url,
            source="linuxdo",
            source_handle="dev",
            source_tier=2,
            posted_at=datetime(2026, 11, 21, 0, 0),
            fetched_at=datetime(2026, 11, 21, 0, 0),
            excerpt="body",
        )
        rendered.append(render_item(it))

    raw = "# AI Daily Digest — 2026-11-21\n\n## LINUX DO (3 items)\n\n" + "\n".join(rendered)
    raw_path = tmp_path / "raw.md"
    raw_path.write_text(raw, encoding="utf-8")
    out_dir = tmp_path / "out"
    res = subprocess.run(
        [sys.executable, str(LINUXDO_SCRIPT), "--raw", str(raw_path), "--out", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr + res.stdout
    batch_payload = json.loads((out_dir / "batch-001.json").read_text(encoding="utf-8"))
    assert [f["topic_url"] for f in batch_payload["fragments"]] == [u for _, u in cases]

    import importlib.util

    spec = importlib.util.spec_from_file_location("digest_linuxdo_batches", LINUXDO_SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parsed_pairs = [mod._heading_identity(chunk.splitlines()[0]) for chunk in rendered]
    assert parsed_pairs == cases


def test_c10_watchdog_kills_term_ignoring_child_even_after_parent_exits(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    script = tmp_path / "test_watchdog.sh"
    script.write_text(
        f"""#!/bin/bash
set -euo pipefail
collect_tree() {{
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    collect_tree "$child"
  done
  printf '%s\\n' "$pid"
}}
(
  /bin/bash -c 'trap "" TERM; echo $$ > "{child_pid_file}"; sleep 60' &
  wait
) &
AGG_PID=$!
sleep 0.3
(
  SLEEP_PID=""
  trap '[ -n "$SLEEP_PID" ] && kill "$SLEEP_PID" 2>/dev/null || true; exit 0' TERM INT
  sleep 0.2 &
  SLEEP_PID=$!
  wait "$SLEEP_PID" 2>/dev/null || true
  SLEEP_PID=""
  if kill -0 "$AGG_PID" 2>/dev/null; then
    trap '' TERM
    TREE_PIDS=$(collect_tree "$AGG_PID")
    for p in $TREE_PIDS; do kill -TERM "$p" 2>/dev/null || true; done
    for _ in 1 2; do
      any_alive=0
      for p in $TREE_PIDS; do kill -0 "$p" 2>/dev/null && any_alive=1 && break; done
      [ "$any_alive" -eq 0 ] && break
      sleep 0.2
    done
    EXTRA_PIDS=$(collect_tree "$AGG_PID" 2>/dev/null || true)
    for p in $TREE_PIDS $EXTRA_PIDS; do kill -KILL "$p" 2>/dev/null || true; done
  fi
) &
AGG_WD_PID=$!
wait "$AGG_PID" 2>/dev/null || true
kill "$AGG_WD_PID" 2>/dev/null || true
wait "$AGG_WD_PID" 2>/dev/null || true
STUBBORN=$(cat "{child_pid_file}")
if kill -0 "$STUBBORN" 2>/dev/null; then
  kill -KILL "$STUBBORN" 2>/dev/null || true
  echo "SURVIVED:$STUBBORN" >&2
  exit 1
fi
echo "KILLED:$STUBBORN"
""",
        encoding="utf-8",
    )
    res = subprocess.run(["/bin/bash", str(script)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr + res.stdout
    assert "KILLED:" in res.stdout


def test_external_feeds_seen_check_allows_trailing_slash_query_and_period(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    ext_dir = reports_dir / "external-feeds"
    ext_dir.mkdir(parents=True)
    (tmp_path / "sources.yml").write_text("sources: []\n", encoding="utf-8")
    (reports_dir / "2026-11-21.md").write_text(
        "- https://example.com/a/\n"
        "- https://example.com/b?utm_source=rss\n"
        "- See https://example.com/c.\n",
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-blogs-2026-11-21.json").write_text(
        json.dumps(
            {
                "blogs": [
                    {"url": "https://example.com/a", "publishedAt": "2026-11-21"},
                    {"url": "https://example.com/b", "publishedAt": "2026-11-21"},
                    {"url": "https://example.com/c", "publishedAt": "2026-11-21"},
                ]
            }
        ),
        encoding="utf-8",
    )
    (ext_dir / "follow-builders-x-2026-11-21.json").write_text(json.dumps({"x": []}), encoding="utf-8")
    res = subprocess.run(
        ["bash", str(SEEN_SCRIPT), "2026-11-21"],
        capture_output=True,
        text=True,
        env={**os.environ, "SOCIAL_INFO_DIR": str(tmp_path)},
        check=False,
    )
    assert res.returncode == 0, res.stderr
    for target in ("https://example.com/a", "https://example.com/b", "https://example.com/c"):
        line = next(ln for ln in res.stdout.splitlines() if f"URL={target} |" in ln)
        assert "verdict=ALREADY_SEEN" in line


def test_dry_run_n_value_compatible_with_system_python3(tmp_path: Path) -> None:
    if not Path("/usr/bin/python3").exists():
        pytest.skip("no /usr/bin/python3")
    res = subprocess.run(
        ["/usr/bin/python3", "-I", str(DRY_RUN_SCRIPT)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 1
    assert "ImportError" not in res.stderr
    assert "database not found" in res.stderr

