#!/usr/bin/env python3
"""session-audit.py 的 CLI 行為測試。

跑法：python3 scripts/local-analysis/session-audit.test.py
假 provider 只在 localhost。不讀真實 transcript、keys 或 relay。
"""

import hashlib
import json
import os
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


def analysis(findings, limitations=None):
  return openai(
    json.dumps(
      {
        "continuity": "next",
        "findings": findings,
        "limitations": limitations or [],
      },
      ensure_ascii=False,
    )
  )


def user_line(text, session_id="s1", cwd="/work/natural", **extra):
  record = {
    "type": "user",
    "sessionId": session_id,
    "cwd": cwd,
    "timestamp": "2026-10-05T00:00:00.000Z",
    "message": {"role": "user", "content": text},
  }
  record.update(extra)
  return json.dumps(record, ensure_ascii=False) + "\n"


def write_jsonl(path, text):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(text)


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
    self.env = os.environ.copy()
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
    write_jsonl(self.projects / "work" / "huge.jsonl", json.dumps(record) + "\n")
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
    write_jsonl(self.projects / "work" / "tools.jsonl", "\n".join(lines) + "\n")
    # QUOTE_TOOL 在第二筆 tool_result，JSONL 第 2 行。
    server = serve(lambda body: (analysis([finding("QUOTE_TOOL", source_line=2)]), 200))
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
    self.assertEqual(self.source_named(report, "probe.jsonl")["classification"], "unknown")
    self.assertTrue(self.source_named(report, "probe.jsonl")["included"])
    self.assertTrue(self.source_named(report, "bench.jsonl")["included"])

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

    locked = original.replace(
      "## 待折\n",
      "## 待折\n"
      "<!-- friction-review-lock:v1 session=other "
      "claimed_at=2026-10-05T00:00:00Z expires_at=2026-10-06T00:00:00Z -->\n",
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
      "\n".join(
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
    self.assertTrue(self.source_named(report, "sdk.jsonl")["included"])
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
    write_jsonl(self.projects / "work" / "promo2.jsonl", user_line("QUOTE_PROMOTE_REAL", session_id="sess-real"))
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


def analysis_with_media(media):
  return openai(
    json.dumps(
      {"continuity": "next", "findings": [], "limitations": [], "media": media},
      ensure_ascii=False,
    )
  )


if __name__ == "__main__":
  unittest.main(verbosity=2)
