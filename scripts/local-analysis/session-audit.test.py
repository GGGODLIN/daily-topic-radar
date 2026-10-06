#!/usr/bin/env python3
"""session-audit.py 的 CLI 行為測試。

跑法：python3 scripts/local-analysis/session-audit.test.py
假 provider 只在 localhost。不讀真實 transcript、keys 或 relay。
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).with_name("session-audit.py")
ONE_PIXEL = (
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
SAFE_RELAY = """
openai-compatibility:
  - name: local-free
    base-url: http://127.0.0.1:9
    models:
      - name: demo-free
        alias: free
      - name: demo-smart
        alias: free-smart
"""
GROK_RELAY = """
openai-compatibility:
  - name: grok-owner
    base-url: http://127.0.0.1:9
    models:
      - name: grok-3
        alias: free
"""


def openai(content, finish="stop"):
  return json.dumps(
    {"choices": [{"finish_reason": finish, "message": {"content": content}}]},
    ensure_ascii=False,
  )


def finding(quote, status="unresolved", observation="obs", recurrence=False, target="unknown", source_line=1):
  # source_line 是 JSONL 物理行，不是 helper 預設。quote 在別行就傳那個行號。
  return {
    "evidence": [{"source_line": source_line, "quote": quote}],
    "status": status,
    "independent_recurrence": recurrence,
    "target": target,
    "observation": observation,
    "check_direction": "核對原文這一句",
  }


def analysis(findings, limitations=None, rule_tags=None):
  body = {
    "continuity": "next",
    "findings": findings,
    "limitations": limitations or [],
  }
  if rule_tags is not None:
    body["rule_tags"] = rule_tags
  return openai(json.dumps(body, ensure_ascii=False))


def user_line(text, session_id="s1", cwd="/work/natural", **extra):
  record = {
    "type": "user",
    "sessionId": session_id,
    "cwd": cwd,
    "promptSource": "typed",
    "origin": {"kind": "human"},
    "timestamp": "2026-10-05T00:00:00.000Z",
    "message": {"role": "user", "content": text},
  }
  record.update(extra)
  return json.dumps(record, ensure_ascii=False) + "\n"


def write_jsonl(path, text, bind_session=True):
  path.parent.mkdir(parents=True, exist_ok=True)
  if bind_session:
    # 舊 fixture 隨意填 sessionId；有效來源現在必須對得上主檔與子 agent，負例用原樣寫入。
    parent = None
    for marker in ("subagents", "workflows"):
      if marker in path.parts:
        parent = Path(*path.parts[:path.parts.index(marker)]).with_suffix(".jsonl")
        break
    lines = []
    for line in text.splitlines():
      try:
        record = json.loads(line)
      except ValueError:
        lines.append(line)
        continue
      if isinstance(record, dict) and "sessionId" in record:
        record["sessionId"] = parent.stem if parent is not None else path.stem
        if record.get("type") == "user":
          record.setdefault("promptSource", "typed")
          record.setdefault("origin", {"kind": "human"})
        if parent is not None:
          record.update(agentId=path.stem.removeprefix("agent-"), isSidechain=True)
      lines.append(json.dumps(record, ensure_ascii=False))
    text = "\n".join(lines) + "\n"
  path.write_text(text)


def git(repo, *args, date=None):
  env = os.environ.copy()
  env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
  if date:
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
  done = subprocess.run(
    ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
    capture_output=True, text=True, env=env, check=True,
  )
  return done.stdout.strip()


def commit_rules(repo, files, date, message="rules"):
  # files: {相對路徑: 內容}；內容為 None 代表刪檔。
  repo.mkdir(parents=True, exist_ok=True)
  if not (repo / ".git").exists():
    git(repo, "init", "-q")
  for name, text in files.items():
    target = repo / name
    if text is None:
      target.unlink()
      continue
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
  git(repo, "add", "-A")
  git(repo, "commit", "-q", "-m", message, date=date)
  return git(repo, "rev-parse", "HEAD")


BASE_RULES = {
  "CLAUDE.md": "# 全域設定\n\n## 程式碼風格\n\n- BASE_RULE_FALLBACK 使用 ?? 而非 || 做 value fallback\n- BASE_RULE_INDENT 使用 2 空格縮排\n",
  "rules/common/alpha.md": "# Alpha\n\nALPHA_PARAGRAPH_RULE 這一段沒有清單，整段算一條。\n",
}


def system_text(body):
  return json.loads(body)["messages"][0]["content"]


def user_text(body):
  content = json.loads(body)["messages"][-1]["content"]
  return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)


def label_for(body, marker):
  match = re.search(r"\[(R\d+)\][^\n]*" + re.escape(marker), system_text(body))
  return match.group(1) if match else None


def no_timestamp_line(text, session_id="s1"):
  record = json.loads(user_line(text, session_id=session_id))
  del record["timestamp"]
  return json.dumps(record, ensure_ascii=False) + "\n"


class Recorder(BaseHTTPRequestHandler):
  protocol_version = "HTTP/1.1"

  def log_message(self, fmt, *args):
    return

  def do_POST(self):
    length = int(self.headers.get("Content-Length") or "0")
    body = self.rfile.read(length)
    self.server.bodies.append(body)
    self.server.paths.append(self.path)
    self.server.auths.append(self.headers.get("Authorization"))
    if self.server.hold_first and not self.server.held_once:
      self.server.held_once = True
      self.server.got.set()
      self.server.release.wait(10)
    payload, code = self.server.reply(body)
    if payload is None:
      # 模擬上游讀完請求就斷線：client 在讀回應狀態行時收到 RemoteDisconnected，與讀逾時同一條未包裝路徑。
      self.close_connection = True
      return
    data = payload.encode() if isinstance(payload, str) else payload
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)


def serve(reply, hold=False):
  server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
  server.bodies = []
  server.paths = []
  server.auths = []
  server.reply = reply
  server.hold_first = hold
  server.held_once = False
  server.got = threading.Event()
  server.release = threading.Event()
  thread = threading.Thread(target=server.serve_forever, daemon=True)
  thread.start()
  server.thread = thread
  return server


def stop(server):
  server.shutdown()
  server.server_close()


class SessionAuditCliTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.root = Path(self.tmp.name)
    self.projects = self.root / "projects"
    self.state = self.root / "state"
    self.friction = self.root / "friction"
    self.projects.mkdir()
    self.friction.mkdir()
    self.relay = self.root / "relay.yaml"
    self.relay.write_text(SAFE_RELAY)
    self.keys = self.root / "keys.env"
    self.keys.write_text(
      "\n".join(
        [
          "CLIPROXY_BASE_URL=http://evil.example",
          "CLIPROXY_KEY_CC=unit-test-key",
          f"PWN={self.root / 'PWNED'}",
          "IGNORED=$(/usr/bin/touch " + str(self.root / "PWNED") + ")",
          "",
        ]
      )
    )
    self.rules_repo = self.root / "rules-repo"
    self.base_sha = commit_rules(self.rules_repo, BASE_RULES, "2026-09-01T00:00:00+00:00")
    self.env = os.environ.copy()
    # 預設 --rules-repo 是 ~/.claude；測試不得碰真實規則。
    self.env["HOME"] = str(self.root / "home")
    self.env.pop("CLAUDE_CODE_SESSION_ID", None)
    self.env.pop("ANTHROPIC_BASE_URL", None)
    self.env["ANTHROPIC_BASE_URL"] = "http://203.0.113.9/paid"

  def tearDown(self):
    self.tmp.cleanup()

  def flags(self, url, started_at="1970-01-01T00:00:00Z", extra=None):
    args = [
      "--projects",
      str(self.projects),
      "--state",
      str(self.state),
      "--relay-config",
      str(self.relay),
      "--keys-file",
      str(self.keys),
      "--friction-root",
      str(self.friction),
      "--relay-url",
      url,
      "--rules-repo",
      str(self.rules_repo),
      # fixture 剛寫完的檔一律算已關閉；寫入中跳過的行為由 throughput 測試單獨覆蓋。
      "--quiet-seconds",
      "0",
    ]
    if started_at is not None:
      args.extend(["--started-at", started_at])
    if extra:
      args.extend(extra)
    return args

  def cli(self, args, stdin="", env=None, timeout=12):
    return subprocess.run(
      [sys.executable, str(SCRIPT), *args],
      input=stdin,
      text=True,
      capture_output=True,
      timeout=timeout,
      env=env or self.env,
      check=False,
    )

  def status(self, url):
    proc = self.cli(["status", *self.flags(url)])
    self.assertEqual(proc.returncode, 0, proc.stderr)
    return json.loads(proc.stdout)

  def source_named(self, status, name):
    rows = [row for row in status["sources"] if row["name"] == name]
    self.assertEqual(len(rows), 1, status)
    return rows[0]

  def test_enqueue_scan_run_status(self):
    path = self.projects / "work" / "s1.jsonl"
    write_jsonl(path, user_line("請看 QUOTE_HAPPY", session_id="s1"))
    server = serve(lambda body: (analysis([finding("QUOTE_HAPPY", observation="happy-obs")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    hook = json.dumps(
      {
        "hook_event_name": "SessionEnd",
        "session_id": "s1",
        "transcript_path": str(path),
        "cwd": "/work/natural",
      }
    )
    queued = self.cli(["enqueue", *self.flags(url)], stdin=hook)
    self.assertEqual(queued.returncode, 0, queued.stderr)
    self.assertEqual(server.bodies, [])
    scanned = self.cli(["scan", *self.flags(url)])
    self.assertEqual(scanned.returncode, 0, scanned.stderr)
    ran = self.cli(["run", *self.flags(url)])
    self.assertEqual(ran.returncode, 0, ran.stderr)
    self.assertTrue(server.bodies)
    self.assertEqual(server.auths[0], "Bearer unit-test-key")
    self.assertNotIn("unit-test-key", server.bodies[0].decode())
    self.assertIn('"model": "free"', server.bodies[0].decode())
    self.assertNotIn(b"203.0.113.9", server.bodies[0])
    report = self.status(url)
    row = self.source_named(report, "s1.jsonl")
    self.assertTrue(row["included"])
    self.assertEqual(row["status"], "complete")
    self.assertTrue(row["latest_complete"])
    self.assertEqual(report["hints"], 1)
    self.assertEqual([item["observation"] for item in report["candidates"]], ["happy-obs"])
    self.assertFalse((self.root / "PWNED").exists())
    self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
    self.assertEqual((self.state / "state.sqlite").stat().st_mode & 0o777, 0o600)
    before = len(server.bodies)
    reported = self.cli(["report", *self.flags(url)])
    self.assertEqual(reported.returncode, 0, reported.stderr)
    self.assertIn("happy-obs", reported.stdout)
    self.assertIn("只呈概況，不另建摩擦H/M/L", reported.stdout)
    self.assertEqual(len(server.bodies), before)

  def test_missed_event_scanned_once_across_symlink(self):
    real = self.projects / "work" / "missed.jsonl"
    write_jsonl(real, user_line("MISSED_MARKER"))
    link = self.projects / "work" / "missed-link.jsonl"
    link.symlink_to(real)
    server = serve(lambda body: (analysis([finding("MISSED_MARKER")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    included = [row for row in report["sources"] if row["included"]]
    self.assertEqual([row["name"] for row in included], ["missed.jsonl"])
    self.assertIn("MISSED_MARKER", b"".join(server.bodies).decode())
    self.assertEqual(report["hints"], 0)

  def test_resume_sees_appended_content(self):
    path = self.projects / "work" / "grow.jsonl"
    write_jsonl(path, user_line("QUOTE_OLD"))
    seen = []

    def reply(body):
      text = body.decode()
      seen.append(text)
      if "NEW_AFTER_MARKER" in text:
        # 追加列是第 2 行；spec 對照 user_line 一筆一行，不能沿用預設 source_line=1。
        return analysis([finding("NEW_AFTER_MARKER", observation="saw-new", source_line=2)]), 200
      return analysis([finding("QUOTE_OLD", observation="saw-old")]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    first = self.status(url)
    self.assertTrue(self.source_named(first, "grow.jsonl")["latest_complete"])
    self.assertTrue(first["segments"])
    with path.open("a") as handle:
      handle.write(user_line("NEW_AFTER_MARKER"))
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertTrue(any("NEW_AFTER_MARKER" in text for text in seen))
    second = self.status(url)
    self.assertTrue(self.source_named(second, "grow.jsonl")["latest_complete"])
    self.assertIn("saw-new", [item["observation"] for item in second["candidates"]])

  def test_update_during_run_is_not_latest_complete(self):
    path = self.projects / "work" / "race.jsonl"
    write_jsonl(path, user_line("QUOTE_RACE"))
    server = serve(lambda body: (analysis([finding("QUOTE_RACE")]), 200), hold=True)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    proc = subprocess.Popen(
      [sys.executable, str(SCRIPT), "run", *self.flags(url)],
      text=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      env=self.env,
    )
    try:
      self.assertTrue(server.got.wait(5), "run 沒有打到 stub provider")
      with path.open("a") as handle:
        handle.write(user_line("APPENDED_DURING_RUN"))
      server.release.set()
      out, err = proc.communicate(timeout=8)
    finally:
      if proc.poll() is None:
        proc.kill()
    self.assertEqual(proc.returncode, 0, err)
    row = self.source_named(self.status(url), "race.jsonl")
    self.assertNotEqual(row["status"], "complete")
    self.assertFalse(row["latest_complete"])
    self.assertIn("APPENDED_DURING_RUN", path.read_text())
    self.assertNotIn("APPENDED_DURING_RUN", out)

  def test_huge_tool_is_not_dropped(self):
    secret_key = (
      "-----BEGIN PRIVATE KEY-----\n"
      + ("A" * 20000)
      + "\nUNIQUEPRIVATELINE\n"
      + ("A" * 20000)
      + "\n-----END PRIVATE KEY-----\n"
    )
    tool = (
      "START_MARKER\n"
      + secret_key
      + "MIDDLE_VISIBLE\n"
      + ("B" * 30000)
      + "\nEND_MARKER\n"
      + "sk-ant-SUPERSECRETTOKENVALUE1234567890\n"
      + "session-audit-secret@example.com\n"
      + "Authorization: Bearer leak-me-please\n"
      + "Cookie: session=leakcookievalue\n"
      + "http://user:leakuserinfo@example.com/a\n"
      + "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.UNIQUEJWTTAIL99\n"
      + "+19998887766\n"
    )
    record = {
      "type": "assistant",
      "sessionId": "huge",
      "message": {
        "role": "assistant",
        "content": [
          {
            "type": "tool_use",
            "id": "toolu_huge",
            "name": "Bash",
            "input": {"command": tool},
          }
        ],
      },
    }
    write_jsonl(self.projects / "work" / "huge.jsonl", user_line("檢查工具輸出", session_id="huge") + json.dumps(record) + "\n")
    server = serve(lambda body: (analysis([]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    for marker in ("START_MARKER", "MIDDLE_VISIBLE", "END_MARKER", "toolu_huge"):
      self.assertIn(marker, sent)
    for secret in (
      "UNIQUEPRIVATELINE",
      "SUPERSECRETTOKENVALUE",
      "session-audit-secret@example.com",
      "leak-me-please",
      "leakcookievalue",
      "leakuserinfo",
      "UNIQUEJWTTAIL99",
      "19998887766",
      "BEGIN PRIVATE KEY",
    ):
      self.assertNotIn(secret, sent)
    row = self.source_named(self.status(url), "huge.jsonl")
    self.assertEqual(row["status"], "complete")
    self.assertGreaterEqual(len(server.bodies), 2)

  def test_tool_use_result_kept_and_thinking_excluded(self):
    lines = [
      json.dumps(
        {
          "type": "assistant",
          "sessionId": "tools",
          "message": {
            "role": "assistant",
            "content": [
              {"type": "thinking", "thinking": "THINK_SECRET_SHOULD_NOT_LEAK"},
              {"type": "text", "text": "assistant says ASSIST_VISIBLE"},
              {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Bash",
                "input": {"command": "echo TOOL_INPUT_VISIBLE"},
              },
            ],
          },
        }
      ),
      json.dumps(
        {
          "type": "user",
          "sessionId": "tools",
          "message": {
            "role": "user",
            "content": [
              {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": "TOOL_RESULT_VISIBLE QUOTE_TOOL",
              }
            ],
          },
        }
      ),
    ]
    write_jsonl(self.projects / "work" / "tools.jsonl", user_line("核對工具結果", session_id="tools") + "\n".join(lines) + "\n")
    # 真人輸入在第一行，QUOTE_TOOL 的 tool_result 現在位於第三行。
    server = serve(lambda body: (analysis([finding("QUOTE_TOOL", source_line=3)]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    for marker in ("ASSIST_VISIBLE", "TOOL_INPUT_VISIBLE", "TOOL_RESULT_VISIBLE", "toolu_1"):
      self.assertIn(marker, sent)
    self.assertNotIn("THINK_SECRET_SHOULD_NOT_LEAK", sent)
    row = self.source_named(self.status(url), "tools.jsonl")
    self.assertGreaterEqual(row["excluded_thinking"], 1)

  def test_unreadable_media_partial_and_unredacted_image_stays_local(self):
    sentinel = self.root / "sentinel.bin"
    sentinel.write_text("SENTINEL_UNIQUE_BYTES")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    self.addCleanup(listener.close)
    external = f"http://127.0.0.1:{listener.getsockname()[1]}/secret.png"
    partial = {
      "type": "user",
      "sessionId": "media",
      "message": {
        "role": "user",
        "content": [
          {"type": "text", "text": "QUOTE_PARTIAL"},
          {"type": "image", "source": {"type": "url", "url": external}},
          {"type": "image", "source": {"type": "file", "path": str(sentinel)}},
          {
            "type": "audio",
            "source": {"type": "base64", "media_type": "audio/wav", "data": "AAAA"},
          },
        ],
      },
    }
    image = {
      "type": "user",
      "sessionId": "image",
      "message": {
        "role": "user",
        "content": [
          {"type": "text", "text": "INLINE_IMAGE_SESSION"},
          {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": ONE_PIXEL},
          },
        ],
      },
    }
    write_jsonl(self.projects / "work" / "partial.jsonl", json.dumps(partial) + "\n")
    write_jsonl(self.projects / "work" / "image.jsonl", json.dumps(image) + "\n")

    def reply(body):
      text = body.decode()
      if "QUOTE_PARTIAL" in text:
        return analysis([finding("QUOTE_PARTIAL")]), 200
      if "INLINE_IMAGE_SESSION" in text or ONE_PIXEL in text:
        return analysis([finding("INLINE_IMAGE_SESSION")]), 200
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    self.assertNotIn(ONE_PIXEL, sent)
    self.assertIn("INLINE_IMAGE_SESSION", sent)
    self.assertNotIn("image_url", sent)
    self.assertNotIn("SENTINEL_UNIQUE_BYTES", sent)
    self.assertNotIn(external, sent)
    with self.assertRaises(TimeoutError):
      listener.accept()
    report = self.status(url)
    partial_row = self.source_named(report, "partial.jsonl")
    self.assertEqual(partial_row["status"], "partial")
    self.assertFalse(partial_row["latest_complete"])
    self.assertIn("unsupported_external_attachment", partial_row["limitations"])
    self.assertIn("unreadable_media", partial_row["limitations"])
    image_row = self.source_named(report, "image.jsonl")
    self.assertEqual(image_row["status"], "partial")
    self.assertFalse(image_row["latest_complete"])
    self.assertIn("media-not-sent", image_row["limitations"])
    self.assertIn("media-unredacted", image_row["limitations"])

  def test_upstream_drop_does_not_crash_run(self):
    # 免費腿讀逾時／斷線時，urllib 不會包成 URLError；沒接住就整輪 run 當掉、其他來源也不分析。
    write_jsonl(self.projects / "work" / "drop.jsonl", user_line("MARKER_DROP"))
    write_jsonl(self.projects / "work" / "fine.jsonl", user_line("MARKER_FINE"))
    server = serve(lambda body: (None, 0) if b"MARKER_DROP" in body else (analysis([]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    ran = self.cli(["run", *self.flags(url)])
    self.assertEqual(ran.returncode, 0, ran.stderr)
    self.assertNotIn("Traceback", ran.stderr)
    report = self.status(url)
    self.assertNotEqual(self.source_named(report, "drop.jsonl")["status"], "complete")
    self.assertEqual(self.source_named(report, "fine.jsonl")["status"], "complete")

  def test_model_format_and_http_error_not_complete(self):
    for name, marker in (
      ("http.jsonl", "MARKER_HTTP"),
      ("bad.jsonl", "MARKER_BADJSON"),
      ("missing.jsonl", "MARKER_MISSING"),
      ("length.jsonl", "MARKER_LENGTH"),
    ):
      write_jsonl(self.projects / "work" / name, user_line(marker))

    def reply(body):
      text = body.decode()
      if "MARKER_HTTP" in text:
        return "UPSTREAM_SECRET_BODY", 500
      if "MARKER_BADJSON" in text:
        return openai("this is not json"), 200
      if "MARKER_MISSING" in text:
        return openai(json.dumps({"continuity": ""})), 200
      if "MARKER_LENGTH" in text:
        return (
          openai(
            json.dumps({"continuity": "x", "findings": [], "limitations": []}),
            "length",
          ),
          200,
        )
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    ran = self.cli(["run", *self.flags(url)])
    self.assertEqual(ran.returncode, 0, ran.stderr)
    self.assertNotIn("UPSTREAM_SECRET_BODY", ran.stderr)
    report = self.status(url)
    for name in ("http.jsonl", "bad.jsonl", "missing.jsonl", "length.jsonl"):
      row = self.source_named(report, name)
      self.assertNotEqual(row["status"], "complete", name)
      self.assertFalse(row["latest_complete"], name)

  def test_resolved_not_candidate_unresolved_is(self):
    write_jsonl(
      self.projects / "work" / "judge.jsonl",
      user_line("QUOTE_UNRESOLVED and QUOTE_RESOLVED and QUOTE_RESOLVED_AGAIN"),
    )

    def reply(body):
      return (
        analysis(
          [
            finding("QUOTE_UNRESOLVED", "unresolved", "still-broken", False),
            finding("QUOTE_RESOLVED", "resolved", "fixed-once", False),
            finding("QUOTE_RESOLVED_AGAIN", "resolved", "fixed-recurred", True),
          ]
        ),
        200,
      )

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    observations = [item["observation"] for item in report["candidates"]]
    self.assertIn("still-broken", observations)
    self.assertIn("fixed-recurred", observations)
    self.assertNotIn("fixed-once", observations)

  def test_child_workflow_inherits_natural_parent(self):
    write_jsonl(
      self.projects / "work" / "natural-1.jsonl",
      user_line("bench research BENCH_RESEARCH_MAIN", session_id="natural-1"),
    )
    write_jsonl(
      self.projects / "work" / "natural-1" / "subagents" / "agent-a.jsonl",
      user_line("CHILD_SUB_MARKER bench", session_id="agent-a"),
    )
    write_jsonl(
      self.projects / "work" / "natural-1" / "workflows" / "wf-a.jsonl",
      user_line("CHILD_WF_MARKER", session_id="wf-a"),
    )
    write_jsonl(
      self.projects / "work" / "syn-1.jsonl",
      user_line("SYN_PARENT", session_id="syn-1", synthetic=True),
    )
    write_jsonl(
      self.projects / "work" / "syn-1" / "subagents" / "agent-b.jsonl",
      user_line("CHILD_OF_SYNTH", session_id="agent-b"),
    )

    def reply(body):
      text = body.decode()
      for quote in ("BENCH_RESEARCH_MAIN", "CHILD_SUB_MARKER", "CHILD_WF_MARKER"):
        if quote in text:
          return analysis([finding(quote)]), 200
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    for marker in ("BENCH_RESEARCH_MAIN", "CHILD_SUB_MARKER", "CHILD_WF_MARKER"):
      self.assertIn(marker, sent)
    self.assertNotIn("CHILD_OF_SYNTH", sent)
    self.assertNotIn("SYN_PARENT", sent)
    report = self.status(url)
    self.assertTrue(self.source_named(report, "natural-1.jsonl")["included"])
    self.assertTrue(self.source_named(report, "agent-a.jsonl")["included"])
    self.assertTrue(self.source_named(report, "wf-a.jsonl")["included"])
    synthetic = self.source_named(report, "syn-1.jsonl")
    child = self.source_named(report, "agent-b.jsonl")
    self.assertFalse(synthetic["included"])
    self.assertEqual(synthetic["classification"], "synthetic")
    self.assertFalse(child["included"])
    self.assertEqual(child["classification"], "synthetic")

  def test_synthetic_self_and_probe_classification(self):
    write_jsonl(
      self.projects / "eval-roots" / "case.jsonl",
      user_line("EVAL_ROOT_MARKER", session_id="eval-1"),
    )
    write_jsonl(
      self.projects / "work" / "self-session-id.jsonl",
      user_line("SELF_MARKER", session_id="self-session-id"),
    )
    write_jsonl(
      self.projects / "work" / "probe.jsonl",
      user_line("PROBE_CWD_KEEP", session_id="probe-1", cwd="/tmp/probe-lab/not-eval"),
    )
    write_jsonl(
      self.projects / "work" / "bench.jsonl",
      user_line("this is a bench run BENCH_MAIN_KEEP", session_id="bench-1"),
    )
    env = self.env.copy()
    env["CLAUDE_CODE_SESSION_ID"] = "self-session-id"

    def reply(body):
      text = body.decode()
      for quote in ("PROBE_CWD_KEEP", "BENCH_MAIN_KEEP", "SELF_MARKER"):
        if quote in text:
          return analysis([finding(quote)]), 200
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)], env=env).returncode, 0)
    ambient = self.status(url)
    self.assertNotEqual(self.source_named(ambient, "self-session-id.jsonl")["classification"], "self")
    self.assertTrue(self.source_named(ambient, "self-session-id.jsonl")["included"])
    self.assertEqual(self.cli(["run", *self.flags(url, extra=["--self-session", "self-session-id"])], env=env).returncode, 0)
    sent = b"".join(server.bodies).decode()
    self.assertIn("PROBE_CWD_KEEP", sent)
    self.assertIn("BENCH_MAIN_KEEP", sent)
    self.assertNotIn("EVAL_ROOT_MARKER", sent)
    self.assertNotIn("SELF_MARKER", sent)
    report = self.status(url)
    self.assertEqual(self.source_named(report, "case.jsonl")["classification"], "synthetic")
    self.assertFalse(self.source_named(report, "case.jsonl")["included"])
    self.assertEqual(self.source_named(report, "self-session-id.jsonl")["classification"], "self")
    self.assertFalse(self.source_named(report, "self-session-id.jsonl")["included"])
    self.assertEqual(self.source_named(report, "probe.jsonl")["classification"], "human-main")
    self.assertTrue(self.source_named(report, "probe.jsonl")["included"])
    self.assertTrue(self.source_named(report, "bench.jsonl")["included"])

  def test_skill_up_project_dir_is_synthetic_with_children(self):
    skill_up = self.projects / "-private-var-folders-ab-cd-T-skill-up-3"
    write_jsonl(
      skill_up / "su-1.jsonl",
      user_line("SKILL_UP_MAIN", session_id="su-1", cwd="/private/var/folders/ab/cd/T/skill-up-3"),
    )
    write_jsonl(
      skill_up / "su-1" / "subagents" / "agent-su.jsonl",
      user_line("SKILL_UP_SUB", session_id="agent-su"),
    )
    write_jsonl(
      skill_up / "su-1" / "workflows" / "wf-su.jsonl",
      user_line("SKILL_UP_WF", session_id="wf-su"),
    )
    write_jsonl(
      self.projects / "-private-var-folders-ab-cd-T-other-lab" / "tmp-1.jsonl",
      user_line("TMP_OTHER_KEEP", session_id="tmp-1", cwd="/private/var/folders/ab/cd/T/other-lab"),
    )
    write_jsonl(
      self.projects / "eval-roots" / "case.jsonl",
      user_line("EVAL_ROOT_MARKER", session_id="eval-1"),
    )
    write_jsonl(
      self.projects / "work" / "flagged.jsonl",
      user_line("FLAGGED_MARKER", session_id="flagged-1", synthetic=True),
    )
    server = serve(lambda body: (analysis([]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    for marker in ("SKILL_UP_MAIN", "SKILL_UP_SUB", "SKILL_UP_WF", "EVAL_ROOT_MARKER", "FLAGGED_MARKER"):
      self.assertNotIn(marker, sent)
    self.assertIn("TMP_OTHER_KEEP", sent)
    report = self.status(url)
    for name in ("su-1.jsonl", "agent-su.jsonl", "wf-su.jsonl", "case.jsonl", "flagged.jsonl"):
      row = self.source_named(report, name)
      self.assertEqual(row["classification"], "synthetic", name)
      self.assertFalse(row["included"], name)
    other = self.source_named(report, "tmp-1.jsonl")
    self.assertEqual(other["classification"], "human-main")
    self.assertTrue(other["included"])

  def test_source_hash_unchanged_after_read(self):
    path = self.projects / "work" / "hash.jsonl"
    write_jsonl(path, user_line("QUOTE_HASH"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    server = serve(lambda body: (analysis([finding("QUOTE_HASH")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    for command in ("enqueue", "scan", "run", "status", "report"):
      stdin = ""
      if command == "enqueue":
        stdin = json.dumps({"hook_event_name": "SessionEnd", "session_id": "s1"})
      proc = self.cli([command, *self.flags(url)], stdin=stdin)
      self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

  def test_state_symlink_hardlink_fifo_refuse_without_hanging(self):
    real = self.root / "real-state"
    real.mkdir()
    link = self.root / "link-state"
    link.symlink_to(real)
    base = [
      "--projects",
      str(self.projects),
      "--relay-config",
      str(self.relay),
      "--keys-file",
      str(self.keys),
      "--friction-root",
      str(self.friction),
      "--relay-url",
      "http://127.0.0.1:9",
    ]
    try:
      linked = self.cli(["enqueue", "--state", str(link), *base], stdin="{}", timeout=3)
    except subprocess.TimeoutExpired:
      self.fail("symlink state hung")
    self.assertNotEqual(linked.returncode, 0)
    self.assertIn("state-symlink", linked.stderr)
    self.assertEqual(list(real.iterdir()), [])

    fifo = self.root / "state-fifo"
    os.mkfifo(fifo)
    try:
      fifo_proc = self.cli(["scan", "--state", str(fifo), *base], timeout=3)
    except subprocess.TimeoutExpired:
      self.fail("fifo state hung")
    self.assertNotEqual(fifo_proc.returncode, 0)
    self.assertIn("state-fifo", fifo_proc.stderr)

    hard = self.root / "hard-state"
    hard.mkdir()
    db = hard / "state.sqlite"
    db.write_text("original-bytes")
    os.link(db, hard / "state.sqlite.link")
    try:
      hard_proc = self.cli(["scan", "--state", str(hard), *base], timeout=3)
    except subprocess.TimeoutExpired:
      self.fail("hardlink state hung")
    self.assertNotEqual(hard_proc.returncode, 0)
    self.assertIn("state-hardlink", hard_proc.stderr)
    self.assertEqual(db.read_text(), "original-bytes")

  def test_forbidden_alias_fail_closed(self):
    self.relay.write_text(GROK_RELAY)
    write_jsonl(self.projects / "work" / "blocked.jsonl", user_line("QUOTE_BLOCKED"))
    server = serve(lambda body: (analysis([finding("QUOTE_BLOCKED")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    ran = self.cli(["run", *self.flags(url)])
    self.assertEqual(ran.returncode, 0, ran.stderr)
    self.assertEqual(server.bodies, [])
    self.assertIn("alias-forbidden", ran.stdout + ran.stderr)
    row = self.source_named(self.status(url), "blocked.jsonl")
    self.assertNotEqual(row["status"], "complete")
    self.assertFalse(row["latest_complete"])

  def test_promote_appends_without_rewriting_or_following_model_path(self):
    target = self.root / "do-not-create" / "friction-log.md"
    write_jsonl(self.projects / "work" / "promo.jsonl", user_line("QUOTE_PROMOTE"))
    friction = self.friction / "workflow-general.md"
    original = "\n".join(
      [
        "# 摩擦",
        "",
        "## 待折",
        "",
        "- 2026-01-01 old pending item",
        "",
        "## 休眠",
        "",
        "## 已折／已否決",
        "",
        "- 2026-01-02 old closed item",
        "",
      ]
    )
    friction.write_text(original)

    def reply(body):
      return (
        analysis(
          [
            finding(
              "QUOTE_PROMOTE",
              "unresolved",
              "needs-check",
              False,
              str(target),
            )
          ]
        ),
        200,
      )

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    promoted = self.cli(["promote", *self.flags(url)])
    self.assertEqual(promoted.returncode, 0, promoted.stderr)
    updated = friction.read_text()
    self.assertIn("- 2026-01-01 old pending item", updated)
    self.assertIn("- 2026-01-02 old closed item", updated)
    self.assertIn("signal_type=agent-observation", updated)
    self.assertIn("target=unknown", updated)
    self.assertIn("flags=speculation", updated)
    self.assertNotIn(str(target), updated)
    self.assertFalse(target.exists())
    old_lines = original.splitlines()
    cursor = 0
    for line in updated.splitlines():
      if cursor < len(old_lines) and line == old_lines[cursor]:
        cursor += 1
    self.assertEqual(cursor, len(old_lines))

    expires = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
    locked = original.replace(
      "## 待折\n",
      "## 待折\n"
      "<!-- friction-review-lock:v1 session=other "
      f"claimed_at=2026-10-05T00:00:00Z expires_at={expires} -->\n",
    )
    friction.write_text(locked)
    refused = self.cli(["promote", *self.flags(url)])
    self.assertNotEqual(refused.returncode, 0)
    self.assertEqual(friction.read_text(), locked)

  def test_scan_append_clears_latest_and_keeps_segments(self):
    path = self.projects / "work" / "grow.jsonl"
    write_jsonl(path, user_line("QUOTE_SCAN_OLD"))
    server = serve(lambda body: (analysis([finding("QUOTE_SCAN_OLD")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    before = self.status(url)
    segment_shas = [item["source_sha"] for item in before["segments"]]
    self.assertTrue(segment_shas)
    self.assertTrue(self.source_named(before, "grow.jsonl")["latest_complete"])
    with path.open("a") as handle:
      handle.write(user_line("QUOTE_SCAN_NEW"))
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    after = self.status(url)
    row = self.source_named(after, "grow.jsonl")
    self.assertEqual(row["status"], "pending")
    self.assertFalse(row["latest_complete"])
    self.assertTrue(any(item["source_sha"] in segment_shas for item in after["segments"]))

  def test_roles_tools_and_visible_attachment_not_dropped(self):
    role_lines = [
      {"type": "user", "uuid": "rec-user", "sessionId": "role", "message": {"role": "user", "content": "SAME_SENTENCE"}},
      {
        "type": "assistant",
        "uuid": "rec-assistant",
        "sessionId": "role",
        "message": {
          "role": "assistant",
          "content": [
            {"type": "text", "text": "SAME_SENTENCE"},
            {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "/tmp/same.txt"}},
          ],
        },
      },
      {
        "type": "user",
        "sessionId": "role",
        "message": {
          "role": "user",
          "content": [
            {"type": "tool_result", "tool_use_id": "toolu_read", "is_error": True, "content": "RESULT_ERR_MARKER"}
          ],
        },
      },
    ]
    write_jsonl(
      self.projects / "work" / "role.jsonl",
      "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in role_lines),
    )
    write_jsonl(
      self.projects / "work" / "vis.jsonl",
      user_line("核對附件與 hook 內容", session_id="vis") + "\n".join(
        [
          json.dumps(
            {
              "type": "attachment",
              "sessionId": "vis",
              "attachment": {
                "stdout": "VISIBLE_ATTACH_STDOUT",
                "text": "VISIBLE_ATTACH_TEXT",
                "command": "echo VISIBLE_ATTACH_CMD",
              },
            }
          ),
          json.dumps(
            {
              "type": "system",
              "sessionId": "vis",
              "hookAdditionalContext": "VISIBLE_SYS_HOOK",
              "content": "VISIBLE_SYS_CONTENT",
            }
          ),
          json.dumps(
            {
              "type": "assistant",
              "sessionId": "vis",
              "message": {
                "role": "assistant",
                "content": [
                  {
                    "type": "tool_use",
                    "id": "toolu_cookie",
                    "name": "Bash",
                    "input": {"Cookie": "JSONCOOKIESECRET", "authorization": "Bearer leakauthjson"},
                  }
                ],
              },
            }
          ),
        ]
      )
      + "\n",
    )
    write_jsonl(
      self.projects / "work" / "sdk.jsonl",
      user_line("SDK_MARKER", session_id="sdk", promptSource="sdk"),
    )
    server = serve(lambda body: (analysis([]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    sent = b"".join(server.bodies).decode()
    self.assertGreaterEqual(sent.count("SAME_SENTENCE"), 2)
    self.assertIn("role=user", sent)
    self.assertIn("role=assistant", sent)
    self.assertIn("Read", sent)
    self.assertIn("toolu_read", sent)
    self.assertIn("RESULT_ERR_MARKER", sent)
    self.assertIn("is_error=true", sent)
    self.assertIn("VISIBLE_ATTACH_STDOUT", sent)
    self.assertIn("VISIBLE_ATTACH_TEXT", sent)
    self.assertIn("VISIBLE_ATTACH_CMD", sent)
    self.assertIn("VISIBLE_SYS_HOOK", sent)
    self.assertIn("VISIBLE_SYS_CONTENT", sent)
    self.assertNotIn("JSONCOOKIESECRET", sent)
    self.assertNotIn("leakauthjson", sent)
    report = self.status(url)
    self.assertEqual(self.source_named(report, "sdk.jsonl")["classification"], "unknown")
    self.assertFalse(self.source_named(report, "sdk.jsonl")["included"])
    self.assertNotIn("SDK_MARKER", sent)
    role_segments = [item for item in report["segments"] if item["name"] == "role.jsonl"]
    self.assertTrue(any(item["tool_use_id"] == "toolu_read" and item["source_sha"] for item in role_segments))

  def test_old_fragment_cap_and_no_silent_age(self):
    old = self.projects / "work" / "old.jsonl"
    write_jsonl(old, user_line("OLD_FRAG_MARK\n" + ("y" * 60000), session_id="old-session"))
    os.utime(old, (1_700_000_000, 1_700_000_000))
    write_jsonl(self.projects / "work" / "new.jsonl", user_line("NEW_ONLY_MARKER", session_id="new-session"))
    server = serve(lambda body: (analysis([]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    ran = self.cli(["run", *self.flags(url, started_at="2026-01-01T00:00:00Z")])
    self.assertEqual(ran.returncode, 0, ran.stderr)
    kinds = []
    for body in server.bodies:
      text = body.decode("utf-8", "replace")
      if "NEW_ONLY_MARKER" in text:
        kinds.append("new")
      elif "OLD_FRAG_MARK" in text:
        kinds.append("old")
    self.assertEqual(kinds[0], "new")
    self.assertEqual(kinds.count("old"), 1)
    server.bodies.clear()
    self.assertEqual(self.cli(["run", *self.flags(url, started_at="2026-01-01T00:00:00Z")]).returncode, 0)
    # doc_offset>0 的 old 下輪仍只 1 fragment，不能升成 new。
    self.assertEqual(len(server.bodies), 1)
    self.assertNotEqual(self.source_named(self.status(url), "old.jsonl")["status"], "complete")
    fresh = self.projects / "work" / "ancient-small.jsonl"
    write_jsonl(fresh, user_line("ANCIENT_STILL_NEW\n" + ("z" * 20000) + "\nANCIENT_STILL_END"))
    os.utime(fresh, (1_700_000_000, 1_700_000_000))
    server.bodies.clear()
    self.assertEqual(self.cli(["run", *self.flags(url, started_at=None)]).returncode, 0)
    joined = b"".join(server.bodies)
    self.assertIn(b"ANCIENT_STILL_NEW", joined)
    self.assertIn(b"ANCIENT_STILL_END", joined)

  def test_context_error_shrinks_without_skipping(self):
    write_jsonl(
      self.projects / "work" / "http.jsonl",
      user_line("HTTP_START_MARKER " + ("q" * 12000) + " HTTP_END_MARKER"),
    )
    seen = []

    def reply(body):
      seen.append(body)
      if len(body) > 9000:
        return "too-big", 413
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertTrue(any(len(body) > 9000 for body in seen))
    ok = [body for body in seen if len(body) <= 9000]
    self.assertTrue(any(b"HTTP_START_MARKER" in body for body in ok))
    self.assertTrue(any(b"HTTP_END_MARKER" in body for body in ok))
    self.assertLess(len(ok[0]), len(seen[0]))
    self.assertTrue(self.source_named(self.status(url), "http.jsonl")["latest_complete"])

  def test_enqueue_returns_while_run_holds_http(self):
    write_jsonl(self.projects / "work" / "lock.jsonl", user_line("LOCK_MARKER"))
    server = serve(lambda body: (analysis([finding("LOCK_MARKER")]), 200), hold=True)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    proc = subprocess.Popen(
      [sys.executable, str(SCRIPT), "run", *self.flags(url)],
      text=True,
      stdout=subprocess.PIPE,
      stderr=subprocess.PIPE,
      env=self.env,
    )
    try:
      self.assertTrue(server.got.wait(8))
      started = time.monotonic()
      queued = self.cli(
        ["enqueue", *self.flags(url)],
        stdin=json.dumps({"hook_event_name": "SessionEnd", "session_id": "s1"}),
        timeout=3,
      )
      self.assertLess(time.monotonic() - started, 3)
      self.assertEqual(queued.returncode, 0, queued.stderr)
    finally:
      server.release.set()
      proc.communicate(timeout=8)

  def test_images_are_per_chunk_and_unmatched_media_is_partial(self):
    red = "IMGA_" + ("A" * 40)
    other = "IMGB_" + ("B" * 40)
    huge = "IMGHUGE_" + ("H" * 30000)
    records = [
      {
        "type": "user",
        "sessionId": "img",
        "message": {
          "role": "user",
          "content": [
            {"type": "text", "text": "IMAGE_A_TEXT"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": red}},
          ],
        },
      },
      {"type": "user", "sessionId": "img", "message": {"role": "user", "content": "PAD_MARK\n" + ("p" * 20000)}},
      {
        "type": "user",
        "sessionId": "img",
        "message": {
          "role": "user",
          "content": [
            {"type": "text", "text": "IMAGE_B_TEXT"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": other}},
          ],
        },
      },
    ]
    write_jsonl(
      self.projects / "work" / "img.jsonl",
      "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
    )
    write_jsonl(
      self.projects / "work" / "hugeimg.jsonl",
      json.dumps(
        {
          "type": "user",
          "sessionId": "hugeimg",
          "message": {
            "role": "user",
            "content": [
              {"type": "text", "text": "HUGE_IMAGE_TEXT"},
              {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": huge}},
            ],
          },
        }
      )
      + "\n",
    )

    def reply(body):
      text = body.decode("utf-8", "replace")
      if "IMAGE_A_TEXT" in text or "IMAGE_B_TEXT" in text or "HUGE_IMAGE_TEXT" in text:
        return (
          analysis_with_media(
            [{"source_line": 1, "block": 1, "readable": True, "image_id": "1:1"}]
          ),
          200,
        )
      return analysis([]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    first = next(body.decode("utf-8", "replace") for body in server.bodies if "IMAGE_A_TEXT" in body.decode())
    self.assertNotIn("IMGA_", first)
    self.assertNotIn("IMGB_", first)
    self.assertTrue(any("IMAGE_B_TEXT" in body.decode() for body in server.bodies))
    self.assertNotIn("image_url", b"".join(server.bodies).decode())
    report = self.status(url)
    image_row = self.source_named(report, "img.jsonl")
    self.assertEqual(image_row["status"], "partial")
    self.assertIn("media-not-sent", image_row["limitations"])
    self.assertIn("media-unredacted", image_row["limitations"])
    huge_row = self.source_named(report, "hugeimg.jsonl")
    self.assertEqual(huge_row["status"], "partial")
    self.assertTrue("media-not-sent" in huge_row["limitations"] or "media-not-read" in huge_row["limitations"])
    self.assertNotIn("IMGHUGE_", b"".join(server.bodies).decode())

  def test_promote_uses_real_source_ref_and_skips_duplicates(self):
    write_jsonl(self.projects / "work" / "sess-real.jsonl", user_line("QUOTE_PROMOTE_REAL", session_id="sess-real"))
    friction = self.friction / "workflow-general.md"
    friction.write_text("# 摩擦\n\n## 待折\n\n- old\n\n## 已折／已否決\n\n- closed\n")
    server = serve(lambda body: (analysis([finding("QUOTE_PROMOTE_REAL", observation="needs-real")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    first = self.cli(["promote", *self.flags(url)])
    self.assertEqual(first.returncode, 0, first.stderr)
    text = friction.read_text()
    self.assertIn("source_ref=session:sess-real#1", text)
    self.assertNotIn("source_ref=session:candidate#0", text)
    second = self.cli(["promote", *self.flags(url)])
    self.assertEqual(second.returncode, 0, second.stderr)
    self.assertEqual(friction.read_text().count("source_ref=session:sess-real#1"), 1)

  RULE_FRICTION = (
    "# 摩擦\n\n## 待折\n\n- 2026-01-01 old pending item\n\n"
    "## 休眠\n\n## 已折／已否決\n\n- 2026-01-02 old closed item\n"
  )
  ALPHA_TARGET = "rule:rules/common/alpha.md#Alpha"

  def violation_provider(self, names, verdicts=None):
    # 每個 name 一個 session；規則編號每次請求不同，沿用 tagging_provider 從 system 文字找。
    verdicts = verdicts or {}
    for index, name in enumerate(names, 1):
      write_jsonl(
        self.projects / "work" / f"{name.lower()}.jsonl",
        user_line(f"{name} 違規對話", session_id=name.lower(), timestamp=f"2026-10-0{index}T10:00:00.000Z"),
      )
    return self.tagging_provider(
      [(name, "ALPHA_PARAGRAPH_RULE", verdicts.get(name, "violated"), name) for name in names]
    )

  def run_then_promote(self, url, expect=0):
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    promoted = self.cli(["promote", *self.flags(url)])
    self.assertEqual(promoted.returncode, expect, promoted.stderr)
    return promoted

  def friction_sections(self, text):
    sections = {}
    title = ""
    for line in text.splitlines():
      if line.startswith("## "):
        title = line
        sections[title] = []
      else:
        sections.setdefault(title, []).append(line)
    return sections

  def rule_entry_lines(self, text, target=None):
    target = target or self.ALPHA_TARGET
    pending = self.friction_sections(text)["## 待折"]
    # target 尾端是 @<規則 identity>；這裡只認檔＋標題前綴，identity 由各測試另驗。
    return [line for line in pending if line.startswith("- ") and re.search(rf"target={re.escape(target)}@[0-9a-f]{{16}};", line)]

  def mixed_provider(self):
    # 同一段既有一般 finding（QUOTE_FIND）也有規則違規（QUOTE_VIOL）。
    def reply(body):
      label = label_for(body, "ALPHA_PARAGRAPH_RULE")
      tags = [{"rule": label, "verdict": "violated", "source_line": 1, "quote": "QUOTE_VIOL"}] if label else []
      return analysis([finding("QUOTE_FIND", observation="plain-finding")], rule_tags=tags), 200

    server = serve(reply)
    return server, f"http://127.0.0.1:{server.server_address[1]}"

  def test_rule_three_violations_make_one_entry_with_three_sublines(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(
      ["VIOL_A", "VIOL_B", "VIOL_C", "VIOL_D"], verdicts={"VIOL_D": "applied"}
    )
    self.addCleanup(stop, server)
    promoted = json.loads(self.run_then_promote(url).stdout)
    self.assertEqual((promoted["rule_entries"], promoted["rule_sublines"]), (1, 3), promoted)
    text = friction.read_text()
    pending = self.friction_sections(text)["## 待折"]
    entries = self.rule_entry_lines(text)
    self.assertEqual(len(entries), 1, text)
    entry = entries[0]
    for expected in ("signal_type=agent-observation", "flags=speculation", 'feedback_quote="VIOL_A"', "source_ref=rule:rules/common/alpha.md#Alpha@"):
      self.assertIn(expected, entry)
    self.assertIn("ALPHA_PARAGRAPH_RULE", entry)
    at = pending.index(entry)
    sublines = pending[at + 1 : at + 4]
    self.assertEqual(len(sublines), 3, pending)
    for name, line in zip(("viol_a", "viol_b", "viol_c"), sublines, strict=True):
      self.assertTrue(line.startswith("  - source_ref=session:" + name + "#1:"), line)
      self.assertIn('quote="VIOL_' + name[-1].upper() + '"', line)
      self.assertIn("conversation_time=2026-10-0", line)
      self.assertRegex(line, r"rules_commit=[0-9a-f]{40}")
    self.assertNotIn("viol_d", text, "applied 標記不進摩擦檔")

  def test_rule_entry_still_pending_only_gets_new_sublines(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_A", "VIOL_B", "VIOL_C"])
    self.addCleanup(stop, server)
    (self.projects / "work" / "viol_c.jsonl").rename(self.root / "viol_c.hold")
    self.run_then_promote(url)
    first = friction.read_text()
    entry = self.rule_entry_lines(first)[0]
    self.assertEqual(len([line for line in first.splitlines() if line.startswith("  - source_ref=")]), 2)
    (self.root / "viol_c.hold").rename(self.projects / "work" / "viol_c.jsonl")
    promoted = json.loads(self.run_then_promote(url).stdout)
    self.assertEqual((promoted["rule_entries"], promoted["rule_sublines"]), (0, 1), promoted)
    second = friction.read_text()
    self.assertEqual(self.rule_entry_lines(second), [entry], "首行一字不變、不另開條目")
    self.assertEqual(len([line for line in second.splitlines() if line.startswith("  - source_ref=")]), 3)
    lines = second.splitlines()
    at = lines.index(entry)
    self.assertEqual([line.split("#1:")[0].split("session:")[1] for line in lines[at + 1 : at + 4]], ["viol_a", "viol_b", "viol_c"])
    old_lines = first.splitlines()
    cursor = 0
    for line in lines:
      if cursor < len(old_lines) and line == old_lines[cursor]:
        cursor += 1
    self.assertEqual(cursor, len(old_lines), "舊行都還在且順序不變")

  def test_rule_entry_moved_out_of_pending_opens_new_entry_with_last_disposal(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_A"])
    self.addCleanup(stop, server)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    # 已折段的舊行要帶同一條規則的完整 target（含 identity），才算同一條規則再犯。
    rule_id = self.status(url)["rules"]["tags"][0]["rule"]
    target = f"{self.ALPHA_TARGET}@{rule_id}"
    moved = (
      "- 2026-09-30 [proposal_source=user@s1; user_decision=rejected@2026-10-01T00:00:00Z; implementation=N-A] "
      f"[source_ref={target}; signal_type=agent-observation; target={target}; "
      'feedback_quote="x"; why="y"; flags=speculation] 常駐規則違規（session-audit 自動彙整，次數見下方子行）'
    )
    friction.write_text(self.RULE_FRICTION + moved + "\n")
    promoted = json.loads(self.cli(["promote", *self.flags(url)]).stdout)
    self.assertEqual((promoted["rule_entries"], promoted["rule_sublines"]), (1, 1), promoted)
    text = friction.read_text()
    entries = self.rule_entry_lines(text)
    self.assertEqual(len(entries), 1, text)
    # 首行 2026-09-30 是捕捉日；拍板日在 user_decision。
    self.assertTrue(entries[0].endswith("（上次處置：已折／已否決 2026-10-01）"), entries[0])
    self.assertIn(moved, text.splitlines(), "已折段的舊行不動")

  def test_single_rule_violation_still_creates_entry(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_A"])
    self.addCleanup(stop, server)
    promoted = json.loads(self.run_then_promote(url).stdout)
    self.assertEqual((promoted["appended"], promoted["rule_entries"], promoted["rule_sublines"]), (0, 1, 1), promoted)
    text = friction.read_text()
    self.assertEqual(len(self.rule_entry_lines(text)), 1)
    self.assertNotIn("上次處置", text)

  def test_rules_sharing_a_heading_get_separate_entries(self):
    # 同一標題下常有十幾條規則；合成一條就看不出違反哪條，why 也只會是第一條的原文。
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    for index, name in enumerate(("VIOL_FB", "VIOL_IN"), 1):
      write_jsonl(
        self.projects / "work" / f"{name.lower()}.jsonl",
        user_line(f"{name} 違規對話", session_id=name.lower(), timestamp=f"2026-10-0{index}T10:00:00.000Z"),
      )
    server, url = self.tagging_provider(
      [("VIOL_FB", "BASE_RULE_FALLBACK", "violated", "VIOL_FB"), ("VIOL_IN", "BASE_RULE_INDENT", "violated", "VIOL_IN")]
    )
    promoted = json.loads(self.run_then_promote(url).stdout)
    self.assertEqual((promoted["rule_entries"], promoted["rule_sublines"]), (2, 2), promoted)
    pending = self.friction_sections(friction.read_text())["## 待折"]
    entries = [line for line in pending if line.startswith("- ") and "常駐規則違規" in line]
    self.assertEqual(len(entries), 2, pending)
    fallback = next(line for line in entries if 'feedback_quote="VIOL_FB"' in line)
    indent = next(line for line in entries if 'feedback_quote="VIOL_IN"' in line)
    self.assertIn("BASE_RULE_FALLBACK", fallback)
    self.assertNotIn("BASE_RULE_INDENT", fallback)
    self.assertIn("BASE_RULE_INDENT", indent)
    self.assertNotEqual(
      re.search(r"target=([^;]+);", fallback).group(1), re.search(r"target=([^;]+);", indent).group(1)
    )

  def test_rule_subline_source_ref_never_repeats(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_A", "VIOL_B"])
    self.addCleanup(stop, server)
    self.run_then_promote(url)
    once = friction.read_text()
    again = json.loads(self.cli(["promote", *self.flags(url)]).stdout)
    self.assertEqual((again["rule_entries"], again["rule_sublines"]), (0, 0), again)
    self.assertEqual(friction.read_text(), once)
    # 同一 source_ref 已出現在檔案任何位置（這裡是休眠段的舊行）也不追加。
    refs = {tag["quote"]: tag["source_ref"] for tag in self.status(url)["rules"]["tags"]}
    seeded = self.RULE_FRICTION.replace("## 休眠\n", f"## 休眠\n\n- 2026-09-01 [source_ref={refs['VIOL_A']}; target=unknown] 舊\n")
    friction.write_text(seeded)
    self.cli(["promote", *self.flags(url)])
    text = friction.read_text()
    self.assertEqual(text.count(refs["VIOL_A"]), 1)
    self.assertEqual(text.count(refs["VIOL_B"]), 1)

  def test_rule_promote_refused_when_locked_and_leaves_nothing(self):
    friction = self.friction / "workflow-general.md"
    server, url = self.violation_provider(["VIOL_A", "VIOL_B"])
    self.addCleanup(stop, server)
    friction.write_text(self.RULE_FRICTION)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    locked = self.RULE_FRICTION.replace(
      "## 待折\n",
      "## 待折\n<!-- friction-review-lock:v1 session=other "
      "claimed_at=2026-10-05T00:00:00Z expires_at=2099-10-06T00:00:00Z -->\n",
    )
    friction.write_text(locked)
    refused = self.cli(["promote", *self.flags(url)])
    self.assertNotEqual(refused.returncode, 0)
    self.assertEqual(friction.read_text(), locked)
    friction.write_text(self.RULE_FRICTION)
    holder = os.open(self.friction / ".workflow-general.md.friction-review.lock", os.O_CREAT | os.O_RDWR, 0o600)
    self.addCleanup(os.close, holder)
    fcntl.flock(holder, fcntl.LOCK_EX)
    busy = self.cli(["promote", *self.flags(url)])
    fcntl.flock(holder, fcntl.LOCK_UN)
    self.assertEqual(busy.returncode, 3, busy.stderr)
    self.assertEqual(friction.read_text(), self.RULE_FRICTION, "被鎖時整次拒寫，不留半套")

  def test_rule_sublines_do_not_count_as_pending_items(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_A", "VIOL_B", "VIOL_C"])
    self.addCleanup(stop, server)
    self.run_then_promote(url)
    pending = self.friction_sections(friction.read_text())["## 待折"]
    # 摩擦待折掃描（trial-review.sh）的口徑：待折段內以 "- " 開頭的行才算一項。
    self.assertEqual(len([line for line in pending if line.startswith("- ")]), 2, pending)
    self.assertEqual(len([line for line in pending if line.startswith("  - ")]), 3, pending)

  def test_candidate_only_promote_output_has_no_rule_keys(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    write_jsonl(self.projects / "work" / "only.jsonl", user_line("QUOTE_ONLY", session_id="only"))
    server = serve(lambda body: (analysis([finding("QUOTE_ONLY")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    promoted = self.run_then_promote(url)
    self.assertEqual(promoted.stdout.strip(), json.dumps({"command": "promote", "appended": 1}))

  def test_rule_entries_do_not_change_session_candidate_rows(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    write_jsonl(self.projects / "work" / "mixed.jsonl", user_line("QUOTE_FIND QUOTE_VIOL", session_id="mixed"))
    server, url = self.mixed_provider()
    self.addCleanup(stop, server)
    promoted = self.run_then_promote(url)
    self.assertEqual(json.loads(promoted.stdout)["appended"], 1)
    text = friction.read_text()
    candidate = [line for line in text.splitlines() if "plain-finding" in line]
    self.assertEqual(len(candidate), 1)
    self.assertRegex(
      candidate[0],
      r'^- \d{4}-\d{2}-\d{2} \[source_ref=session:mixed#1:[0-9a-f]{16}; signal_type=agent-observation; '
      r'target=unknown; feedback_quote="QUOTE_FIND"; why="unknown"; flags=speculation\] plain-finding 核對原文這一句$',
    )
    self.assertEqual(len(self.rule_entry_lines(text)), 1)
    self.assertEqual(promoted.stdout.strip(), json.dumps({"command": "promote", "appended": 1, "rule_entries": 1, "rule_sublines": 1}))

  def test_daily_writes_out_without_creating_state(self):
    out_dir = self.root / "daily-out"
    env = self.env.copy()
    env["SESSION_AUDIT_STATE"] = str(self.root / "missing-state")
    env["SESSION_AUDIT_OUT_DIR"] = str(out_dir)
    env["LOCAL_ANALYSIS_DATE"] = "2026-10-05"
    script = Path(__file__).with_name("session-audit-daily.sh")
    direct = subprocess.run(["bash", str(script)], text=True, capture_output=True, env=env, check=False)
    self.assertEqual(direct.returncode, 0, direct.stderr)
    out_file = out_dir / "2026-10-05-session-audit.md"
    self.assertTrue(out_file.is_file())
    self.assertGreater(out_file.stat().st_size, 0)
    self.assertIn("只呈概況，不另建摩擦H/M/L", out_file.read_text())
    self.assertFalse((self.root / "missing-state").exists())
    runner = Path(__file__).with_name("run-shell-channel.sh")
    ran = subprocess.run(
      ["bash", str(runner), str(script), str(out_file), "2026-10-05", "true"],
      text=True,
      capture_output=True,
      env=env,
      check=False,
    )
    self.assertEqual(ran.returncode, 0, ran.stderr)
    self.assertTrue(Path(str(out_file) + ".complete.sha256").is_file())

  def test_deleted_source_does_not_abort_the_rest(self):
    write_jsonl(self.projects / "work" / "gone.jsonl", user_line("GONE_MARKER"))
    write_jsonl(self.projects / "work" / "keep.jsonl", user_line("KEEP_MARKER"))
    server = serve(lambda body: (analysis([finding("KEEP_MARKER")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    (self.projects / "work" / "gone.jsonl").unlink()
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    gone = self.source_named(report, "gone.jsonl")
    self.assertEqual(gone["status"], "missing")
    self.assertFalse(gone["latest_complete"])
    self.assertEqual(self.source_named(report, "keep.jsonl")["status"], "complete")
    self.assertIn("KEEP_MARKER", b"".join(server.bodies).decode())

  # ---- Ticket 05：規則標記＋規則版本＋最小狀態 ----

  def tagging_provider(self, plan):
    # plan: [(session_marker, rule_marker, verdict, quote)]；規則編號每次請求不同，所以從 system 文字找。
    def reply(body):
      tags = []
      for session_marker, rule_marker, verdict, quote in plan:
        if session_marker not in user_text(body):
          continue
        label = label_for(body, rule_marker)
        if label:
          tags.append({"rule": label, "verdict": verdict, "source_line": 1, "quote": quote})
      return analysis([], rule_tags=tags), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    return server, f"http://127.0.0.1:{server.server_address[1]}"

  def two_version_repo(self):
    revised = dict(BASE_RULES)
    revised["CLAUDE.md"] = BASE_RULES["CLAUDE.md"].replace(
      "BASE_RULE_INDENT 使用 2 空格縮排", "NEW_RULE_INDENT 使用 4 空格縮排"
    )
    new_sha = commit_rules(self.rules_repo, revised, "2026-10-03T00:00:00+00:00")
    return self.base_sha, new_sha

  def tags_by_quote(self, status):
    return {tag["quote"]: tag for tag in status["rules"]["tags"]}

  def test_rule_version_follows_conversation_time(self):
    old_sha, new_sha = self.two_version_repo()
    write_jsonl(self.projects / "work" / "old.jsonl", user_line("SESSION_OLD 舊對話", session_id="old", timestamp="2026-09-20T10:00:00.000Z"))
    write_jsonl(self.projects / "work" / "new.jsonl", user_line("SESSION_NEW 新對話", session_id="new", timestamp="2026-10-04T10:00:00.000Z"))
    server, url = self.tagging_provider([
      ("SESSION_OLD", "BASE_RULE_INDENT", "applied", "SESSION_OLD"),
      ("SESSION_NEW", "NEW_RULE_INDENT", "violated", "SESSION_NEW"),
    ])
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    old_bodies = [system_text(body) for body in server.bodies if "SESSION_OLD" in user_text(body)]
    new_bodies = [system_text(body) for body in server.bodies if "SESSION_NEW" in user_text(body)]
    self.assertTrue(old_bodies and new_bodies)
    self.assertIn("BASE_RULE_INDENT", old_bodies[0])
    self.assertNotIn("NEW_RULE_INDENT", old_bodies[0])
    self.assertIn("NEW_RULE_INDENT", new_bodies[0])
    self.assertNotIn("BASE_RULE_INDENT", new_bodies[0])
    # applied 只留在 last_seen（最小狀態），violated 另有逐筆標記。
    status = self.status(url)
    seen = {item["commit"]: item for item in status["rules"]["last_seen"]}
    self.assertEqual(seen[old_sha]["verdict"], "applied")
    self.assertEqual(seen[new_sha]["verdict"], "violated")
    self.assertNotEqual(seen[old_sha]["rule"], seen[new_sha]["rule"])
    tags = self.tags_by_quote(status)
    self.assertEqual(list(tags), ["SESSION_NEW"])
    self.assertEqual(tags["SESSION_NEW"]["commit"], new_sha)

  def test_rule_scope_is_claude_md_plus_rules_common_files(self):
    files = dict(BASE_RULES)
    files["rules/common/beta.md"] = (
      "# Beta\n\n## 細則\n\n"
      "- BETA_COMMON_RULE 第一行\n  縮排續行 BETA_CONTINUATION\n"
      "- BETA_SECOND 第二條\n\n"
      "1. NUMBERED_RULE 編號條目\n"
    )
    files["rules/common/notes.txt"] = "- TXT_NOT_RULE\n"
    files["rules/other/gamma.md"] = "- OTHER_DIR_RULE\n"
    files["rules/common/sub/delta.md"] = "- NESTED_RULE\n"
    commit_rules(self.rules_repo, files, "2026-10-03T00:00:00+00:00")
    write_jsonl(self.projects / "work" / "s1.jsonl", user_line("SESSION_SCOPE 內容", timestamp="2026-10-04T10:00:00.000Z"))
    server, url = self.tagging_provider([
      ("SESSION_SCOPE", "BASE_RULE_FALLBACK", "applied", "SESSION_SCOPE"),
    ])
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    system = system_text(server.bodies[0])
    for present in ("BASE_RULE_FALLBACK", "ALPHA_PARAGRAPH_RULE", "BETA_COMMON_RULE", "BETA_CONTINUATION", "BETA_SECOND", "NUMBERED_RULE"):
      self.assertIn(present, system)
    for absent in ("TXT_NOT_RULE", "OTHER_DIR_RULE", "NESTED_RULE"):
      self.assertNotIn(absent, system)
    labels = {label_for(server.bodies[0], name) for name in ("BETA_COMMON_RULE", "BETA_SECOND", "NUMBERED_RULE", "ALPHA_PARAGRAPH_RULE")}
    self.assertNotIn(None, labels)
    self.assertEqual(len(labels), 4, "每個清單項與無清單段落各自一條規則")
    self.assertRegex(system, r"BETA_COMMON_RULE 第一行\n  縮排續行 BETA_CONTINUATION")
    tag = self.status(url)["rules"]["last_seen"][0]
    self.assertEqual(tag["path"], "CLAUDE.md")
    self.assertEqual(tag["heading"], "全域設定 > 程式碼風格")

  def test_rule_version_unavailable_is_unanalyzed_and_never_uses_current(self):
    # 一份沒有 timestamp；一份早於規則 repo 的第一個 commit（2026-09-01）。
    write_jsonl(self.projects / "work" / "nostamp.jsonl", no_timestamp_line("NOSTAMP_QUOTE 沒有時間", session_id="nostamp"))
    write_jsonl(self.projects / "work" / "early.jsonl", user_line("EARLY_QUOTE 很早的對話", session_id="early", timestamp="2026-08-01T10:00:00.000Z"))

    def reply(body):
      quote = "NOSTAMP_QUOTE" if "NOSTAMP_QUOTE" in user_text(body) else "EARLY_QUOTE"
      # 模型照樣回標記；沒送規則的請求，這些標記都不能收。
      return analysis([finding(quote, observation=f"obs-{quote}")], rule_tags=[{"rule": "R1", "verdict": "violated", "source_line": 1, "quote": quote}]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertEqual(len(server.bodies), 2)
    for body in server.bodies:
      self.assertNotIn("BASE_RULE_FALLBACK", system_text(body))
      self.assertNotIn("ALPHA_PARAGRAPH_RULE", system_text(body))
      self.assertEqual(json.loads(body)["max_tokens"], 1500)
    report = self.status(url)
    for name in ("nostamp.jsonl", "early.jsonl"):
      row = self.source_named(report, name)
      self.assertIn("rules-version-unavailable", row["limitations"])
      self.assertEqual(row["status"], "complete")
    self.assertEqual(report["rules"]["tags"], [])
    self.assertEqual(report["rules"]["weekly_segments"], [])
    self.assertEqual(sorted(item["observation"] for item in report["candidates"]), ["obs-EARLY_QUOTE", "obs-NOSTAMP_QUOTE"])
    sessions = {item["name"]: item for item in report["rules"]["sessions"]}
    self.assertTrue(sessions["nostamp.jsonl"]["unavailable"])
    self.assertTrue(sessions["early.jsonl"]["unavailable"])
    self.assertIsNone(sessions["early.jsonl"]["commit"])

  def test_rule_version_unavailable_when_git_fails(self):
    write_jsonl(self.projects / "work" / "s1.jsonl", user_line("GITFAIL_QUOTE", timestamp="2026-10-04T10:00:00.000Z"))
    not_git = self.root / "not-git"
    not_git.mkdir()
    server = serve(lambda body: (analysis([finding("GITFAIL_QUOTE", observation="still-analyzed")]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    flags = self.flags(url)
    flags[flags.index("--rules-repo") + 1] = str(not_git)
    self.assertEqual(self.cli(["run", *flags]).returncode, 0)
    self.assertNotIn("BASE_RULE", system_text(server.bodies[0]))
    report = self.status(url)
    row = self.source_named(report, "s1.jsonl")
    self.assertIn("rules-version-unavailable", row["limitations"])
    self.assertEqual(row["status"], "complete")
    self.assertEqual([item["observation"] for item in report["candidates"]], ["still-analyzed"])

  def test_invalid_rule_tags_are_dropped_without_touching_findings(self):
    write_jsonl(self.projects / "work" / "s1.jsonl", user_line("VALID_QUOTE 與別的字", timestamp="2026-10-04T10:00:00.000Z"))

    def reply(body):
      label = label_for(body, "BASE_RULE_INDENT")
      tags = [
        {"rule": label, "verdict": "applied", "source_line": 1, "quote": "VALID_QUOTE"},
        {"rule": "R999", "verdict": "violated", "source_line": 1, "quote": "VALID_QUOTE"},
        {"rule": label, "verdict": "maybe", "source_line": 1, "quote": "VALID_QUOTE"},
        {"rule": label, "verdict": "violated", "source_line": 1, "quote": "NOT_IN_TRANSCRIPT"},
        {"rule": label, "verdict": "violated", "source_line": 7, "quote": "VALID_QUOTE"},
        "not-an-object",
      ]
      return analysis([finding("VALID_QUOTE", observation="kept-finding")], rule_tags=tags), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    self.assertEqual(report["rules"]["tags"], [], "applied 不留逐筆標記")
    self.assertEqual([item["verdict"] for item in report["rules"]["last_seen"]], ["applied"])
    self.assertEqual([item["observation"] for item in report["candidates"]], ["kept-finding"])
    self.assertEqual(self.source_named(report, "s1.jsonl")["status"], "complete")

  def test_failed_segments_do_not_count_toward_weekly_coverage(self):
    stamp = {"now": "2026-10-05T10:00:00.000Z", "old": "2026-09-20T10:00:00.000Z"}
    for name, marker in (("ok-now", "OK_NOW"), ("ok-old", "OK_OLD"), ("http", "HTTP_FAIL"), ("badjson", "BAD_JSON"), ("trunc", "TRUNCATED")):
      write_jsonl(
        self.projects / "work" / f"{name}.jsonl",
        user_line(f"{marker} 內容", session_id=name, timestamp=stamp["old"] if name == "ok-old" else stamp["now"]),
      )

    def reply(body):
      text = user_text(body)
      if "HTTP_FAIL" in text:
        return "{}", 500
      if "BAD_JSON" in text:
        return openai("這不是 JSON"), 200
      if "TRUNCATED" in text:
        return openai("{\"continuity\":", finish="length"), 200
      return analysis([], rule_tags=[]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    self.assertEqual(
      report["rules"]["weekly_segments"],
      [{"week": "2026-W38", "segments": 1}, {"week": "2026-W41", "segments": 1}],
    )
    self.assertEqual(self.source_named(report, "http.jsonl")["status"], "failed")
    self.assertEqual(self.source_named(report, "badjson.jsonl")["status"], "partial")
    self.assertEqual(self.source_named(report, "trunc.jsonl")["status"], "failed")

  def test_rules_request_appends_to_unchanged_prompt_with_output_reserve(self):
    spec = importlib.util.spec_from_file_location("session_audit_module", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    write_jsonl(self.projects / "work" / "s1.jsonl", user_line("PROMPT_QUOTE", timestamp="2026-10-04T10:00:00.000Z"))
    server = serve(lambda body: (analysis([finding("PROMPT_QUOTE", observation="plain-finding")], rule_tags=[]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    payload = json.loads(server.bodies[0])
    system = payload["messages"][0]["content"]
    self.assertTrue(system.startswith(module.SYSTEM_PROMPT))
    self.assertGreater(len(system), len(module.SYSTEM_PROMPT))
    self.assertIn("只做 agent-observation", system)
    self.assertIn("[R1]", system)
    self.assertEqual(payload["max_tokens"], module.OUTPUT_RESERVE)
    self.assertEqual([item["observation"] for item in self.status(url)["candidates"]], ["plain-finding"])

  def test_status_and_report_expose_rule_state(self):
    old_sha, new_sha = self.two_version_repo()
    write_jsonl(self.projects / "work" / "old.jsonl", user_line("SESSION_OLD 舊對話", session_id="old", timestamp="2026-09-20T10:00:00.000Z"))
    write_jsonl(self.projects / "work" / "new.jsonl", user_line("SESSION_NEW 新對話", session_id="new", timestamp="2026-10-04T10:00:00.000Z"))
    server, url = self.tagging_provider([
      ("SESSION_OLD", "ALPHA_PARAGRAPH_RULE", "applied", "SESSION_OLD"),
      ("SESSION_NEW", "ALPHA_PARAGRAPH_RULE", "violated", "SESSION_NEW"),
    ])
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    rules = report["rules"]
    self.assertEqual(len(rules["last_seen"]), 1, "兩次遇到的是同一條未改寫規則")
    seen = rules["last_seen"][0]
    self.assertEqual(seen["last_seen"], "2026-10-04T10:00:00Z")
    self.assertEqual(seen["commit"], new_sha)
    self.assertEqual(seen["path"], "rules/common/alpha.md")
    self.assertEqual(rules["weekly_segments"], [{"week": "2026-W38", "segments": 1}, {"week": "2026-W40", "segments": 1}])
    commits = {item["name"]: item["commit"] for item in rules["sessions"]}
    self.assertEqual(commits, {"old.jsonl": old_sha, "new.jsonl": new_sha})
    before = len(server.bodies)
    text = self.cli(["report", *self.flags(url)]).stdout
    self.assertEqual(len(server.bodies), before)
    for expected in ("2026-W38", "2026-W40", old_sha, new_sha):
      self.assertIn(expected, text)


  # ---- Ticket 07：零使用候選清單 ----

  ZERO_USE_HEADING = "## 🪦 零使用規則候選"
  WEEK_STAMPS = {
    "W37": "2026-09-08T10:00:00.000Z",
    "W38": "2026-09-15T10:00:00.000Z",
    "W39": "2026-09-22T10:00:00.000Z",
    "W40": "2026-09-29T10:00:00.000Z",
    "W41": "2026-10-06T10:00:00.000Z",
  }

  def commit_zero_rules(self, files, date="2026-09-02T00:00:00+00:00"):
    return commit_rules(self.rules_repo, files, date)

  def zero_week_session(self, week, marker=None, stamp=None):
    marker = marker or f"ZW_{week}"
    write_jsonl(
      self.projects / "work" / f"{marker}.jsonl",
      user_line(f"{marker} 內容", session_id=marker, timestamp=stamp or self.WEEK_STAMPS[week]),
    )
    return marker

  def zero_use(self, extra=None, state=None):
    args = ["zero-use", "--state", str(state or self.state), "--rules-repo", str(self.rules_repo), *(extra or [])]
    proc = self.cli(args)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    return proc.stdout

  def zero_items(self, text):
    return [line for line in text.splitlines() if line.startswith("- ")]

  def item_for(self, text, marker):
    found = [line for line in self.zero_items(text) if marker in line]
    self.assertEqual(len(found), 1, f"{marker} 應剛好出現在一個候選項目：\n{text}")
    return found[0]

  ZERO_RULES = {
    "CLAUDE.md": (
      "# 全域設定\n\n## 零使用測試\n\n"
      "- ZU_NEVER 從未被模型回報的規則\n"
      "- ZU_OLD 很久以前遇到過的規則\n"
      "- ZU_VIOLATED 中間被違反過的規則\n"
      "- ZU_RECENT 最新一週遇到的規則\n"
    ),
    "rules/common/alpha.md": None,
  }

  def test_zero_use_lists_rules_quiet_for_four_covered_weeks(self):
    sha = self.commit_zero_rules(self.ZERO_RULES)
    plan = [
      (self.zero_week_session("W37"), "ZU_OLD", "applied", "ZW_W37"),
      (self.zero_week_session("W38"), "NO_SUCH_RULE_MARKER", "applied", "ZW_W38"),
      (self.zero_week_session("W39"), "ZU_VIOLATED", "violated", "ZW_W39"),
      (self.zero_week_session("W40"), "NO_SUCH_RULE_MARKER", "applied", "ZW_W40"),
      (self.zero_week_session("W41"), "ZU_RECENT", "applied", "ZW_W41"),
    ]
    server, url = self.tagging_provider(plan)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    before = len(server.bodies)
    text = self.zero_use()
    self.assertEqual(len(server.bodies), before, "zero-use 唯讀，不呼叫模型")
    lines = text.splitlines()
    self.assertEqual(lines[0], f"{self.ZERO_USE_HEADING}（2）")
    self.assertEqual(
      lines[1],
      "連續 4 個有覆蓋的週沒遇到場合；觀察範圍內未見場合的候選，不是已證明的死碼",
    )
    self.assertEqual(len(self.zero_items(text)), 2, text)
    never = self.item_for(text, "ZU_NEVER")
    old = self.item_for(text, "ZU_OLD")
    for absent in ("ZU_VIOLATED", "ZU_RECENT"):
      self.assertNotIn(absent, text)
    # 從未被回報過的規則也依名冊列入；觀察期間是連續計數走過的週。
    self.assertIn("2026-W37～2026-W41", never)
    self.assertIn("開始觀察後未見", never)
    self.assertIn("2026-W38～2026-W41", old)
    self.assertIn("2026-09-08T10:00:00Z", old)
    self.assertNotIn("開始觀察後未見", old)
    for item in (never, old):
      self.assertIn(sha[:12], item)
      self.assertIn("CLAUDE.md > 全域設定 > 零使用測試", item)
      self.assertRegex(item, r"`[0-9a-f]{16}`")
      for banned in ("死碼", "沒用"):
        self.assertNotIn(banned, item)
    tag = next(t for t in self.status(url)["rules"]["last_seen"] if t["last_seen"].startswith("2026-09-08"))
    self.assertIn(f"`{tag['rule']}`", old)

  def test_zero_use_gap_week_restarts_the_count_without_stitching(self):
    self.commit_zero_rules(self.ZERO_RULES)
    for week in ("W37", "W38", "W40", "W41"):
      self.zero_week_session(week)
    self.zero_week_session("W39", marker="ZW_FAIL_W39")

    def reply(body):
      if "ZW_FAIL_W39" in user_text(body):
        return "{}", 500
      return analysis([], rule_tags=[]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertEqual(
      self.status(url)["rules"]["weekly_segments"],
      [{"week": f"2026-W{n}", "segments": 1} for n in (37, 38, 40, 41)],
    )
    text = self.zero_use()
    self.assertEqual(text.splitlines()[0], f"{self.ZERO_USE_HEADING}（0）")
    self.assertIn("本週沒有候選", text)
    self.assertEqual(self.zero_items(text), [])

  def test_zero_use_rewritten_or_added_rule_restarts_from_zero(self):
    sha_a = self.commit_zero_rules({
      "CLAUDE.md": "# 全域設定\n\n- ZU_STABLE 不變的規則\n- ZU_REWRITE_V1 舊文字\n",
      "rules/common/alpha.md": None,
    })
    sha_b = self.commit_zero_rules({
      "CLAUDE.md": "# 全域設定\n\n- ZU_STABLE 不變的規則\n- ZU_REWRITE_V2 新文字\n- ZU_ADDED 新增的規則\n",
    }, date="2026-09-29T00:00:00+00:00")
    for week in ("W37", "W38", "W39", "W40", "W41"):
      self.zero_week_session(week)
    server = serve(lambda body: (analysis([], rule_tags=[]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    text = self.zero_use()
    self.assertEqual(text.splitlines()[0], f"{self.ZERO_USE_HEADING}（1）")
    stable = self.item_for(text, "ZU_STABLE")
    for absent in ("ZU_REWRITE_V1", "ZU_REWRITE_V2", "ZU_ADDED"):
      self.assertNotIn(absent, text)
    # 觀察期間跨過兩個規則版本，判斷用 commit 全列。
    self.assertIn(sha_a[:12], stable)
    self.assertIn(sha_b[:12], stable)
    self.assertIn("2026-W37～2026-W41", stable)

  def test_zero_use_missing_or_empty_state_is_an_empty_list(self):
    text = self.zero_use(state=self.root / "no-such-state")
    self.assertEqual(text.splitlines()[0], f"{self.ZERO_USE_HEADING}（0）")
    self.assertIn("本週沒有候選", text)
    self.assertFalse((self.root / "no-such-state").exists(), "唯讀指令不建 state")
    server = serve(lambda body: (analysis([], rule_tags=[]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    empty = self.zero_use()
    self.assertEqual(empty.splitlines()[0], f"{self.ZERO_USE_HEADING}（0）")
    self.assertIn("本週沒有候選", empty)

  def test_orphan_sublines_of_another_rule_are_not_counted_as_this_rules(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    server, url = self.violation_provider(["VIOL_B1", "VIOL_B2"])
    self.addCleanup(stop, server)
    hold = self.root / "viol_b2.hold"
    (self.projects / "work" / "viol_b2.jsonl").rename(hold)
    self.run_then_promote(url)
    first = friction.read_text().splitlines()
    entry = self.rule_entry_lines("\n".join(first))[0]
    target = re.search(r"; target=(rule:[^;]+);", entry).group(1)
    # 另一條規則的首行被 helper 搬走後，它的縮排子行會留在這個條目後面。
    foreign = "rule:rules/common/alpha.md#Alpha@aaaaaaaaaaaaaaaa"
    orphans = [
      f'  - source_ref=session:orphan{n}#1:{n:016x}; target={foreign}; quote="ORPHAN_{n}"; '
      f"conversation_time=2026-09-01T00:00:00Z; rules_commit={'0' * 40}"
      for n in (1, 2)
    ]
    at = first.index(entry)
    first[at + 2 : at + 2] = orphans
    friction.write_text("\n".join(first) + "\n")
    hold.rename(self.projects / "work" / "viol_b2.jsonl")
    promoted = json.loads(self.run_then_promote(url).stdout)
    self.assertEqual((promoted["rule_entries"], promoted["rule_sublines"]), (0, 1), promoted)
    lines = friction.read_text().splitlines()
    at = lines.index(entry)
    self.assertEqual(lines[at + 3 : at + 5], orphans, "別條規則的孤兒子行一字不變，且仍在新子行之後")
    self.assertIn("session:viol_b2#1:", lines[at + 2], "新子行接在自己的最後一個子行後")
    self.assertRegex(lines[at + 1], r"^  - source_ref=session:viol_b1#1:[0-9a-f]{16}; target=")
    self.assertRegex(
      lines[at + 2],
      r"^  - source_ref=session:viol_b2#1:[0-9a-f]{16}; target=" + re.escape(target) + r"; quote=",
    )

  MIXED_RULES = (
    "# 全域設定\n\n## 混合段落\n\n"
    "PARA_ONE 第一段第一行\nPARA_ONE_TAIL 第一段第二行\n\n"
    "PARA_TWO 第二段\n\n"
    "| TABLE_HEAD | 欄 |\n|---|---|\n| TABLE_ROW | 值 |\n\n"
    "```\nFENCED_CODE_TEXT\n```\n\n"
    "- LIST_ITEM_X 清單項\n"
  )

  def test_paragraphs_and_tables_under_a_heading_with_list_items_are_rules(self):
    self.commit_zero_rules({"CLAUDE.md": self.MIXED_RULES, "rules/common/alpha.md": None})
    for week in ("W38", "W39", "W40", "W41"):
      self.zero_week_session(week)
    server = serve(lambda body: (analysis([], rule_tags=[]), 200))
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    system = system_text(server.bodies[0])
    labels = {marker: label_for(server.bodies[0], marker) for marker in ("PARA_ONE ", "PARA_TWO", "TABLE_HEAD", "LIST_ITEM_X")}
    self.assertNotIn(None, labels.values(), labels)
    self.assertEqual(len(set(labels.values())), 4, "段落、段落、表格、清單項各自一條")
    self.assertIn("PARA_ONE 第一段第一行\nPARA_ONE_TAIL 第一段第二行", system)
    self.assertRegex(system, r"\[R\d+\] \| TABLE_HEAD \| 欄 \|\n\|---\|---\|\n\| TABLE_ROW \| 值 \|")
    self.assertNotIn("FENCED_CODE_TEXT", system, "fenced code 不當規則")
    text = self.zero_use()
    for marker in ("PARA_ONE ", "PARA_TWO", "TABLE_HEAD", "LIST_ITEM_X"):
      self.item_for(text, marker)
    self.assertNotIn("FENCED_CODE_TEXT", text)

  def test_response_without_rule_tags_list_is_not_coverage(self):
    for name, marker in (("miss", "RT_MISSING"), ("bad", "RT_NOTLIST"), ("empty", "RT_EMPTY")):
      write_jsonl(
        self.projects / "work" / f"{name}.jsonl",
        user_line(f"{marker} 內容", session_id=name, timestamp="2026-10-05T10:00:00.000Z"),
      )

    def reply(body):
      text = user_text(body)
      if "RT_MISSING" in text:
        return analysis([finding("RT_MISSING", observation="obs-missing")]), 200
      if "RT_NOTLIST" in text:
        broken = {"continuity": "next", "findings": [finding("RT_NOTLIST", observation="obs-notlist")], "limitations": [], "rule_tags": "none"}
        return openai(json.dumps(broken, ensure_ascii=False)), 200
      return analysis([finding("RT_EMPTY", observation="obs-empty")], rule_tags=[]), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    report = self.status(url)
    self.assertEqual(report["rules"]["weekly_segments"], [{"week": "2026-W41", "segments": 1}], "只有回了 rule_tags 清單（含空清單）的段算覆蓋")
    self.assertEqual(sorted(item["observation"] for item in report["candidates"]), ["obs-empty", "obs-missing", "obs-notlist"])
    for name in ("miss.jsonl", "bad.jsonl"):
      row = self.source_named(report, name)
      self.assertIn("rule-tags-missing", row["limitations"])
      self.assertEqual(row["status"], "complete")
    self.assertNotIn("rule-tags-missing", self.source_named(report, "empty.jsonl")["limitations"])

  def test_context_error_with_rules_drops_the_rules_block_before_shrinking(self):
    write_jsonl(self.projects / "work" / "s1.jsonl", user_line("CTX_QUOTE 內容", timestamp="2026-10-04T10:00:00.000Z"))

    def reply(body):
      if "<rules>" in system_text(body):
        return "{}", 413
      tags = [{"rule": "R1", "verdict": "violated", "source_line": 1, "quote": "CTX_QUOTE"}]
      return analysis([finding("CTX_QUOTE", observation="kept")], rule_tags=tags), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertEqual(len(server.bodies), 2, "帶規則 413 後只重送一次")
    self.assertEqual(user_text(server.bodies[0]), user_text(server.bodies[1]), "先拿掉規則區塊，transcript 不縮")
    self.assertEqual(json.loads(server.bodies[1])["max_tokens"], 1500)
    report = self.status(url)
    row = self.source_named(report, "s1.jsonl")
    self.assertEqual(row["status"], "complete")
    self.assertIn("rules-dropped-context", row["limitations"])
    self.assertEqual([item["observation"] for item in report["candidates"]], ["kept"])
    self.assertEqual(report["rules"]["weekly_segments"], [], "拿掉規則的段不算覆蓋")
    self.assertEqual((report["rules"]["tags"], report["rules"]["last_seen"]), ([], []), "不收這段的 rule_tags")

  def test_transient_git_failure_is_not_persisted_and_new_segments_retry(self):
    # 暫時失敗不寫成永久，但已完成的 session 不整份重送（spec：歷史回填策略不另訂）；之後追加的段才重新取規則。
    path = self.projects / "work" / "s1.jsonl"
    write_jsonl(path, user_line("GIT_RETRY 內容", timestamp="2026-10-04T10:00:00.000Z"))

    def reply(body):
      label = label_for(body, "BASE_RULE_INDENT")
      quote = "GIT_LATER" if "GIT_LATER" in user_text(body) else "GIT_RETRY"
      line = 2 if quote == "GIT_LATER" else 1
      tags = [{"rule": label, "verdict": "applied", "source_line": line, "quote": quote}] if label else []
      return analysis([finding(quote, observation=quote.lower(), source_line=line)], rule_tags=tags), 200

    server = serve(reply)
    self.addCleanup(stop, server)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    broken = self.flags(url)
    broken[broken.index("--rules-repo") + 1] = str(self.root / "no-such-repo")
    self.assertEqual(self.cli(["run", *broken]).returncode, 0)
    first = self.status(url)
    self.assertIn("rules-version-unavailable", self.source_named(first, "s1.jsonl")["limitations"])
    self.assertEqual(first["rules"]["sessions"], [], "git 暫時失敗不寫進 rule_sessions")
    sent = len(server.bodies)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    self.assertEqual(len(server.bodies), sent, "已完成的 session 不整份重送")
    self.assertEqual(self.status(url)["rules"]["weekly_segments"], [])
    with path.open("a") as handle:
      handle.write(user_line("GIT_LATER 追加", timestamp="2026-10-04T11:00:00.000Z"))
    self.assertEqual(self.cli(["scan", *self.flags(url)]).returncode, 0)
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    later = self.status(url)
    session = later["rules"]["sessions"][0]
    self.assertEqual((session["commit"], session["unavailable"]), (self.base_sha, None))
    self.assertEqual([item["verdict"] for item in later["rules"]["last_seen"]], ["applied"])
    self.assertEqual(later["rules"]["weekly_segments"], [{"week": "2026-W40", "segments": 1}])

  def test_last_disposal_date_is_the_decision_date_not_the_capture_date(self):
    friction = self.friction / "workflow-general.md"
    friction.write_text(self.RULE_FRICTION)
    names = ("VIOL_AL", "VIOL_FB", "VIOL_IN")
    for index, name in enumerate(names, 1):
      write_jsonl(
        self.projects / "work" / f"{name.lower()}.jsonl",
        user_line(f"{name} 違規對話", session_id=name.lower(), timestamp=f"2026-10-0{index}T10:00:00.000Z"),
      )
    server, url = self.tagging_provider([
      ("VIOL_AL", "ALPHA_PARAGRAPH_RULE", "violated", "VIOL_AL"),
      ("VIOL_FB", "BASE_RULE_FALLBACK", "violated", "VIOL_FB"),
      ("VIOL_IN", "BASE_RULE_INDENT", "violated", "VIOL_IN"),
    ])
    self.assertEqual(self.cli(["run", *self.flags(url)]).returncode, 0)
    targets = {tag["quote"]: f"rule:{tag['path']}#{tag['heading']}@{tag['rule']}" for tag in self.status(url)["rules"]["tags"]}

    def old_line(target, head, tail=""):
      return (
        f"- {head}[source_ref={target}; signal_type=agent-observation; target={target}; "
        f'feedback_quote="x"; why="y"; flags=speculation] 常駐規則違規（session-audit 自動彙整，次數見下方子行）{tail}'
      )

    decided = "2026-10-06 [proposal_source=user@s1; user_decision=rejected@2026-11-20T08:00:00Z; implementation=N-A] "
    seeded = self.RULE_FRICTION.replace(
      "## 休眠\n", "## 休眠\n\n" + old_line(targets["VIOL_FB"], "2026-10-01 ", " [dormant since=2026-11-02; wake-when=需使用者主動重提]") + "\n"
    )
    seeded += old_line(targets["VIOL_AL"], decided) + "\n" + old_line(targets["VIOL_IN"], "2026-09-30 ") + "\n"
    friction.write_text(seeded)
    self.assertEqual(self.cli(["promote", *self.flags(url)]).returncode, 0)
    pending = self.friction_sections(friction.read_text())["## 待折"]

    def fresh(quote):
      found = [line for line in pending if line.startswith("- ") and f"target={targets[quote]};" in line]
      self.assertEqual(len(found), 1, pending)
      return found[0]

    self.assertTrue(fresh("VIOL_AL").endswith("（上次處置：已折／已否決 2026-11-20）"), fresh("VIOL_AL"))
    self.assertTrue(fresh("VIOL_FB").endswith("（上次處置：休眠 2026-11-02）"), fresh("VIOL_FB"))
    self.assertTrue(fresh("VIOL_IN").endswith("（上次處置：已折／已否決 2026-09-30（捕捉日））"), fresh("VIOL_IN"))


def analysis_with_media(media):
  return openai(
    json.dumps(
      {"continuity": "next", "findings": [], "limitations": [], "media": media},
      ensure_ascii=False,
    )
  )


if __name__ == "__main__":
  unittest.main(verbosity=2)
