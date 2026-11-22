#!/usr/bin/env python3
"""CLI seam tests for retro-replay. The model relay is an external service, so it is faked with a local
HTTP server that returns pre-written replies and keeps every request it received. The session record is a
synthetic, de-identified fixture; the replay tool and session-retro (event evidence) are the real ones.
The hand-off to retro-judge is checked by the shape of judge-input.json only."""

import importlib.util
import json
import math
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPT = Path(__file__).with_name("retro-replay.py")

CHANGE = "chg-search-exclude-1"
EFFECTIVE = "2026-11-01T00:00:00Z"
LOAD_TIME = "2026-11-02T00:00:00Z"
JUDGED_AT = "2026-11-06T00:00:00Z"
RULE_PATH = "/home/tester/.claude/rules/common/code-search.md"
# The new paragraph contains the old one, like a rule that only gained a clause. It also carries quotes and
# backticks, so a substitution that forgets JSON escaping would corrupt the request.
OLD_TEXT = "Search wide roots recursively when unsure."
NEW_TEXT = OLD_TEXT + ' Exclude `projects` and "cache" dirs.'
MODEL = "relay/fixture-model:high"
KEY_ENV = "FAKE_RELAY_KEY"
KEY = "synthetic-relay-key-7c1e"
PREDICATE = "predicate:root-search-without-exclude@gate-2026-11-06-v1"
VIOLATING = 'command grep -rn "needle" ~/.claude'
COMPLIANT = 'command grep -rn "needle" ~/.claude --exclude-dir={.git,node_modules,projects,cache}'
UNRELATED = "ls /tmp/scratch"
USER_MARK = "MARK-USER-PROMPT find the needle"
ASSISTANT_MARK = "MARK-ASSISTANT-EARLIER"
RESULT_MARK = "MARK-TOOL-RESULT"
SYSTEM_MARK = "MARK-SYSTEM-PROMPT"
AFTER_MARK = "MARK-AFTER-EVENT"
EVENT_MARK = "MARK-ORIGINAL-EVENT-REPLY"
REPLY_MARK = "MARK-MODEL-REPLY-TEXT"
USAGE = {"input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0, "output_tokens": 30}


def rule_body(paragraph):
  return "# Code search\n\nIntro line.\n\n" + paragraph + "\n\nTail line.\n"


def session_rows(recorded, marker="", instructions=True, event_text=VIOLATING, with_rule=True):
  """Rows of a small session: instructions, two snapshots (the first without tools), a deferred tool record,
  one earlier tool exchange, the event response, and one row after it."""
  paragraph = {"new": NEW_TEXT, "old": OLD_TEXT}.get(recorded, "A different paragraph.")
  files = [{"path": "/home/tester/.claude/CLAUDE.md", "type": "User", "content": "Global note. MARK-GLOBAL" + marker}]
  if with_rule:
    files.append({"path": RULE_PATH, "type": "User", "content": rule_body(paragraph)})
  rows = []

  def add(kind, stamp, **fields):
    number = len(rows) + 1
    row = {"type": kind, "uuid": f"u{number}", "parentUuid": f"u{number - 1}" if number > 1 else None,
           "timestamp": stamp, "sessionId": "fixture-session", **fields}
    rows.append(row)
    return row

  if instructions:
    add("attachment", LOAD_TIME, attachment={"type": "instructions", "files": files})
  else:
    add("attachment", LOAD_TIME, attachment={"type": "credential_org", "org": "x"})
  add("attachment", LOAD_TIME, attachment={"type": "date", "date": "2026-11-02"})
  add("attachment", LOAD_TIME, attachment={
    "type": "prompt_snapshot", "cliPrefix": "CLI-PREFIX-LINE", "systemPrompt": [SYSTEM_MARK + marker, "Second part."],
    "systemTurns": True, "toolChangeHeader": True, "inlineTools": False, "contextRendering": "announced"})
  add("user", "2026-11-02T00:01:00Z", message={"role": "user", "content": USER_MARK + marker})
  add("attachment", "2026-11-02T00:01:30Z", attachment={
    "type": "prompt_snapshot", "cliPrefix": "CLI-PREFIX-LINE", "systemPrompt": [SYSTEM_MARK + marker, "Second part."],
    "tools": [{"name": "Bash", "description": "Run shell", "schema": {"type": "object", "properties": {}}}],
    "systemTurns": True, "toolChangeHeader": True, "inlineTools": False, "contextRendering": "announced"})
  add("attachment", "2026-11-02T00:01:40Z", attachment={"type": "deferred_tools_record", "entries": [
    {"name": "DeferredTool", "description": "loaded later", "input_schema": {"type": "object"}}]})
  add("assistant", "2026-11-02T00:02:00Z", requestedModel=MODEL, effort="xhigh", version="9.9.9", message={
    "id": "msg_early", "role": "assistant", "model": "fixture-model", "content": [
      {"type": "thinking", "thinking": "private", "signature": ""},
      {"type": "text", "text": ASSISTANT_MARK + marker},
      {"type": "tool_use", "id": "toolu_early", "name": "Bash", "input": {"command": "ls"}}], "usage": USAGE})
  add("user", "2026-11-02T00:02:30Z", message={"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_early", "content": [
      {"type": "text", "text": RESULT_MARK + marker}, {"type": "tool_reference", "tool_name": "DeferredTool"}]}]})
  add("attachment", "2026-11-02T00:02:40Z", attachment={"type": "ide_selection", "content": "selected lines"})
  add("assistant", "2026-11-02T00:05:00Z", requestedModel=MODEL, effort="xhigh", version="9.9.9", message={
    "id": "msg_event", "role": "assistant", "model": "fixture-model", "content": [
      {"type": "text", "text": EVENT_MARK + marker},
      {"type": "tool_use", "id": "toolu_event", "name": "Bash", "input": {"command": event_text}}],
    "usage": {"input_tokens": 60, "cache_read_input_tokens": 20, "cache_creation_input_tokens": 0, "output_tokens": 30}})
  rows.append({"type": "user", "uuid": "u11", "parentUuid": "u10", "timestamp": "2026-11-02T00:05:30Z",
               "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "toolu_event", "content": AFTER_MARK}]}})
  return rows


EVENT_LINE = 10


def strings(value):
  if isinstance(value, str):
    yield value
  elif isinstance(value, dict):
    for child in value.values():
      yield from strings(child)
  elif isinstance(value, list):
    for child in value:
      yield from strings(child)


def version_in(body):
  """Which rule paragraph a request carries. The new paragraph contains the old one, so it is checked first."""
  texts = list(strings(body.get("messages")))
  if any(NEW_TEXT in text for text in texts):
    return "new"
  if any(OLD_TEXT in text for text in texts):
    return "old"
  return None


def answer(command):
  return {
    "content": [
      {"type": "thinking", "thinking": "hidden reasoning", "signature": "sig"},
      {"type": "text", "text": REPLY_MARK},
      {"type": "tool_use", "id": "toolu_reply", "name": "Bash", "input": {"command": command}},
    ],
    "usage": {"input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0,
              "output_tokens": 30},
  }


class FakeRelay:
  """Pre-written replies. `reply(index, body, owner)` returns (status, payload, delay_seconds)."""

  def __init__(self, reply=None, new_violations=3):
    self.requests = []
    self.new_seen = 0
    self.new_violations = new_violations
    self.reply = reply or self.by_version
    owner = self

    class Handler(BaseHTTPRequestHandler):
      def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("content-length", "0")))
        body = json.loads(raw.decode("utf-8"))
        index = len(owner.requests)
        owner.requests.append({"path": self.path, "authorization": self.headers.get("authorization"),
                               "raw": raw, "body": body})
        status, payload, delay = owner.reply(index, body, owner)
        if delay:
          threading.Event().wait(delay)
        data = json.dumps(payload).encode("utf-8")
        try:
          self.send_response(status)
          self.send_header("content-type", "application/json")
          self.send_header("content-length", str(len(data)))
          if isinstance(payload, dict) and payload.get("location"):
            self.send_header("location", payload["location"])
          self.end_headers()
          self.wfile.write(data)
        except OSError:
          pass

      # 被跟隨的重新導向會以 GET 到達；也要記一筆，否則「對方沒收到」的斷言抓不到它。
      def do_GET(self):
        owner.requests.append({"path": self.path, "authorization": self.headers.get("authorization"),
                               "raw": b"", "body": {}})
        data = b"{}"
        self.send_response(200)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

      def log_message(self, *_args):
        pass

    self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    self.server.daemon_threads = True
    self.origin = "http://127.0.0.1:" + str(self.server.server_address[1])
    threading.Thread(target=self.server.serve_forever, daemon=True).start()

  @staticmethod
  def by_version(_index, body, owner):
    """The old paragraph always breaks the rule; the new one breaks it for the first few calls only."""
    version = version_in(body)
    if version == "old":
      return 200, answer(VIOLATING), 0
    owner.new_seen += 1
    return 200, answer(VIOLATING if owner.new_seen <= owner.new_violations else COMPLIANT), 0

  def stop(self):
    self.server.shutdown()
    self.server.server_close()


def error_reply(status, payload=None):
  return status, payload or {"type": "error", "error": {"type": "api_error", "message": "boom"}}, 0


class ReplayCase(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)
    self.out = self.root / "runs"
    self.serial = 0
    self.relays = []

  def tearDown(self):
    for relay in self.relays:
      relay.stop()

  # ---- fixture plumbing --------------------------------------------------------------

  def relay(self, reply=None, **options):
    fake = FakeRelay(reply, **options)
    self.relays.append(fake)
    return fake

  def write_session(self, rows, name="session.jsonl", raw_tail=""):
    path = self.root / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + raw_tail, encoding="utf-8")
    return path

  def evidence(self):
    path = self.root / "evidence.txt"
    path.write_text("replayed reply of the old paragraph\n", encoding="utf-8")
    return {"path": str(path), "line": 1, "quote": "replayed reply of the old paragraph"}

  def spec(self, relay, session=None, effective=EFFECTIVE, purpose="primary", **changes):
    session = session if session is not None else self.write_session(session_rows("new"))
    body = {
      "schema_version": 1,
      "purpose": purpose,
      "change": {"id": CHANGE, "kind": "rule", "effective_time": effective, "rule_path": RULE_PATH,
                 "old_text": OLD_TEXT, "new_text": NEW_TEXT},
      "event": {"session_file": str(session), "response_line": EVENT_LINE},
      "route": {"relay": relay.origin, "path": "/v1/messages", "key_env": KEY_ENV, "label": "fixture-relay"},
      "condition": PREDICATE,
      "judge": {"by": "main", "at": JUDGED_AT, "evidence": self.evidence()},
    }
    body.update(changes)
    self.serial += 1
    path = self.root / f"spec-{self.serial}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path

  def mutated(self, spec, change):
    body = json.loads(Path(spec).read_text(encoding="utf-8"))
    change(body)
    self.serial += 1
    path = self.root / f"spec-{self.serial}.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path

  def run_cli(self, *args, env=None):
    environment = {**os.environ, KEY_ENV: KEY, **(env or {})}
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), *[str(a) for a in args]],
                            text=True, capture_output=True, env=environment)
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    return result, payload

  def replay(self, spec, *extra, approve=True, run_id="run-1", env=None):
    args = ["run", "--spec", spec, "--out-dir", self.out, "--run-id", run_id, "--gap", 0, "--retry-wait", 0,
            "--timeout", 5, "--chars-per-token", 4, *extra]
    if approve:
      args.append("--approve")
    return self.run_cli(*args, env=env)

  def record(self, run_id="run-1"):
    return json.loads((self.out / run_id / "run.json").read_text(encoding="utf-8"))

  def judge_input_path(self, run_id="run-1"):
    return self.out / run_id / "judge-input.json"

  def everything_written(self):
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(self.out.rglob("*")) if p.is_file())

  def split_requests(self, relay):
    arms = {"new": [], "old": []}
    for request in relay.requests:
      arms[version_in(request["body"])].append(request)
    return arms

  def complete_run(self, relay=None, **kwargs):
    relay = relay or self.relay()
    result, payload = self.replay(self.spec(relay), **kwargs)
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    return relay, payload


class Rebuild(ReplayCase):
  def test_arms_differ_only_in_the_target_rule_paragraph(self):
    for recorded, effective in (("new", EFFECTIVE), ("old", "2026-11-03T00:00:00Z")):
      with self.subTest(recorded=recorded):
        relay = self.relay()
        session = self.write_session(session_rows(recorded), name=f"{recorded}.jsonl")
        result, payload = self.replay(self.spec(relay, session, effective), run_id=recorded)
        self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
        arms = self.split_requests(relay)
        self.assertEqual((len(arms["new"]), len(arms["old"])), (10, 10))
        new_text = json.dumps(arms["new"][0]["body"], ensure_ascii=False)
        old_text = json.dumps(arms["old"][0]["body"], ensure_ascii=False)
        escaped_new, escaped_old = json.dumps(NEW_TEXT, ensure_ascii=False)[1:-1], json.dumps(OLD_TEXT)[1:-1]
        # Swapping the old paragraph into the new request must give the old request back byte for byte, so
        # nothing but the paragraph can differ between the arms.
        self.assertEqual(new_text.replace(escaped_new, escaped_old), old_text)
        self.assertEqual(new_text.count(escaped_new), 1)
        # Every request of one arm is identical to the others; the arm that was recorded in the session
        # carries the session's own paragraph.
        self.assertEqual(len({r["raw"] for r in arms["new"]}), 1)
        self.assertEqual(len({r["raw"] for r in arms["old"]}), 1)
        record = self.record(recorded)
        self.assertEqual(record["rule_versions"]["recorded"], recorded)
        original_version = record["rule_versions"]["arms"]["original"]
        self.assertEqual(original_version, recorded)

  def test_rebuild_follows_the_session_and_stops_before_the_event(self):
    relay, _ = self.complete_run()
    request = relay.requests[0]["body"]
    blob = json.dumps(request, ensure_ascii=False)
    for mark in (USER_MARK, ASSISTANT_MARK, RESULT_MARK, SYSTEM_MARK, "CLI-PREFIX-LINE", "selected lines"):
      self.assertTrue(mark in blob, "rebuilt request lacks " + mark)
    # The deferred tool comes from the deferred-tools record, and Bash from the second snapshot (the first
    # snapshot of a session has no tools).
    self.assertEqual({t["name"] for t in request["tools"]}, {"Bash", "DeferredTool"})
    # Positive control: the strings exist in the session file, so their absence from the request means something.
    session_text = (self.root / "session.jsonl").read_text(encoding="utf-8")
    self.assertTrue(AFTER_MARK in session_text and EVENT_MARK in session_text)
    self.assertFalse(AFTER_MARK in blob, "a row after the event reached the request")
    self.assertFalse(EVENT_MARK in blob, "the original reply to the event reached the request")
    self.assertTrue(request["messages"][-1]["role"] == "user", "the request must end on a user turn")
    self.assertFalse('"thinking"' in blob, "thinking blocks must not be sent")

  def test_differences_from_the_original_request_are_itemized(self):
    self.complete_run()
    differences = {item["id"]: item for item in self.record()["differences"]}
    for expected in ("special_wire_formats_not_reproduced", "thinking_blocks_dropped", "tool_reference_as_text",
                     "attachments_approximated", "streaming_and_beta_headers", "thinking_depth_carrier",
                     "tools_assembled"):
      self.assertIn(expected, differences)
    wire = differences["special_wire_formats_not_reproduced"]
    self.assertEqual(wire["flags"], {"systemTurns": True, "toolChangeHeader": True, "inlineTools": False,
                                     "contextRendering": "announced"})
    self.assertEqual(wire["client_version"], "9.9.9")
    self.assertEqual(differences["thinking_blocks_dropped"]["count"], 1)
    self.assertEqual(differences["tool_reference_as_text"]["count"], 1)
    self.assertEqual(differences["attachments_approximated"]["types"], {"ide_selection": 1})
    self.assertEqual(differences["tools_assembled"]["deferred_added"], ["DeferredTool"])
    self.assertEqual(differences["thinking_depth_carrier"]["session_effort"], "xhigh")


class Route(ReplayCase):
  def test_model_thinking_depth_and_route_follow_the_session(self):
    relay, payload = self.complete_run()
    for request in relay.requests:
      self.assertEqual(request["body"]["model"], MODEL)
      self.assertEqual(request["path"], "/v1/messages")
      self.assertEqual(request["authorization"], "Bearer " + KEY)
      # Depth rides on the model string suffix; a separate depth parameter would be a guess.
      self.assertFalse("thinking" in request["body"] or "effort" in request["body"])
      self.assertFalse(request["body"].get("stream", False))
    config = self.record()["model_config"]
    self.assertEqual(config["model"], MODEL)
    self.assertEqual(config["thinking_depth"], {"source": "model_suffix", "value": "high"})
    self.assertEqual(config["session_effort"], "xhigh")
    self.assertEqual(config["relay"], relay.origin)
    self.assertEqual(config["key_env"], KEY_ENV)
    shown = self.everything_written() + json.dumps(payload)
    self.assertFalse(KEY in shown, "the relay key leaked into the record or the output")

  def test_endpoint_outside_the_session_relay_is_refused_before_any_call(self):
    relay, other = self.relay(), self.relay()
    result, payload = self.replay(self.spec(relay), "--endpoint", other.origin + "/v1/messages")
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual((len(relay.requests), len(other.requests)), (0, 0))
    self.assertTrue("relay" in (result.stderr + result.stdout))
    # Positive control: the same relay origin is accepted and used.
    same, _ = self.replay(self.spec(relay), "--endpoint", relay.origin + "/v1/messages", run_id="run-2")
    self.assertEqual(same.returncode, 0, same.stdout + same.stderr)
    self.assertEqual(len(other.requests), 0)
    self.assertGreater(len(relay.requests), 0)


class RouteSafety(ReplayCase):
  def test_relay_redirect_is_not_followed_so_the_key_goes_nowhere_else(self):
    elsewhere = self.relay()
    relay = self.relay(lambda i, b, o: (302, {"location": elsewhere.origin + "/v1/messages"}, 0))
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
    self.assertEqual(result.returncode, 13)
    self.assertEqual(len(relay.requests), 1)
    self.assertEqual(len(elsewhere.requests), 0)
    self.assertFalse(self.judge_input_path().exists())
    self.assertEqual(self.record()["attempts"][0]["outcome"], "failed")

  def test_error_text_is_masked_before_it_is_cut_so_a_key_on_the_cut_never_reaches_the_record(self):
    # The relay echoes the key right across the 300-character cut of the stored error text: its first 20
    # characters are inside the cut and the rest is outside.
    echoed = "x" * 279 + KEY
    relay = self.relay(lambda i, b, o: (500, echoed, 0))
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
    # Positive control: the cut text would hold 20 characters of the key and no whole key to mask.
    cut = json.dumps(echoed)[:300]
    self.assertTrue(KEY[:20] in cut and KEY not in cut)
    stored = self.record()["attempts"][0]["tries"][0]["error"]
    self.assertTrue("[key]" in stored and len(stored) <= 300)
    self.assertFalse(KEY[:20] in self.everything_written(), "a piece of the relay key reached the run record")


class Approval(ReplayCase):
  def estimate_for(self, relay, *extra, run_id="run-1"):
    result, payload = self.replay(self.spec(relay), *extra, run_id=run_id, approve=False)
    return result, payload

  def test_no_approval_prints_an_estimate_and_never_calls_the_model(self):
    relay = self.relay()
    result, payload = self.estimate_for(relay, "--in-price", 2, "--out-price", 10, "--cap-threshold", 1,
                                        "--cap-multiplier", 2)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual(len(relay.requests), 0)
    self.assertEqual(payload["status"], "awaiting_approval")
    self.assertFalse(payload["called_model"])
    self.assertFalse((self.out / "run-1" / "run.json").exists(), "an unapproved estimate must not open a run")
    estimate = payload["estimate"]
    self.assertEqual(estimate["calls_remaining"], {"precheck": 5, "total": 20})
    self.assertEqual(estimate["output_tokens_per_call"], 4000)
    tokens = estimate["input_tokens_per_call"]
    self.assertGreater(tokens, 0)
    per_call_list = tokens / 1e6 * 2 + 4000 / 1e6 * 10
    self.assertAlmostEqual(estimate["usd_list"]["total"], 20 * per_call_list, places=4)
    self.assertAlmostEqual(estimate["usd_list"]["precheck"], 5 * per_call_list, places=4)
    # Every call of this fixture is above the (lowered) long-context threshold, so the cap price is 2x.
    self.assertAlmostEqual(estimate["usd_cap"]["total"], 2 * 20 * per_call_list, places=4)
    # Positive control: the same invocation with approval calls the model.
    approved, _ = self.replay(self.spec(relay), run_id="run-2")
    self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
    self.assertGreater(len(relay.requests), 0)

  def test_estimate_counts_the_rebuilt_request(self):
    relay = self.relay()
    _, payload = self.estimate_for(relay)
    self.replay(self.spec(relay), run_id="run-2")
    sent_chars = len(relay.requests[0]["raw"].decode("utf-8"))
    # The fixture is plain ASCII, so characters equal bytes, and 4 characters per token was asked for.
    self.assertEqual(payload["estimate"]["input_tokens_rebuilt_est"], math.ceil(sent_chars / 4))

  def test_estimate_is_on_disk_before_the_first_call(self):
    seen = {}

    def reply(index, body, owner):
      if index == 0:
        seen["record"] = json.loads((self.out / "run-1" / "run.json").read_text(encoding="utf-8"))
      return FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    self.complete_run(relay)
    self.assertEqual(seen["record"]["status"], "running")
    self.assertEqual(seen["record"]["attempts"], [])
    self.assertGreater(seen["record"]["estimate"]["usd_list"]["total"], 0)


class RunRecord(ReplayCase):
  def test_record_has_every_field_and_keeps_no_prior_context(self):
    relay, _ = self.complete_run()
    record = self.record()
    self.assertEqual(record["source"]["session"], "session")
    self.assertEqual(record["source"]["response_line"], EVENT_LINE)
    self.assertEqual(record["source"]["event_time"], "2026-11-02T00:05:00Z")
    self.assertEqual(record["change"]["id"], CHANGE)
    versions = record["rule_versions"]
    self.assertEqual((versions["recorded"], versions["basis"]), ("new", "instructions_attachment"))
    self.assertEqual(versions["old_text"], OLD_TEXT)
    self.assertEqual(versions["new_text"], NEW_TEXT)
    self.assertEqual(record["replacements"], [{"session_line": 1, "path": RULE_PATH, "recorded": "new"}])
    self.assertEqual(record["judge_rule"], {"method": "predicate", "name": "root-search-without-exclude",
                                            "version": "gate-2026-11-06-v1"})
    self.assertEqual(record["precheck"], {"runs": 5, "reproduced": 3, "required": 2, "passed": True})
    self.assertEqual(len(record["attempts"]), 20)
    first = record["attempts"][0]
    self.assertEqual((first["arm"], first["version"], first["trial"], first["outcome"]), ("original", "new", 1, "ok"))
    self.assertEqual(first["tries"], [{"status": 200, "secs": first["tries"][0]["secs"]}])
    self.assertEqual(first["usage"]["output_tokens"], 30)
    self.assertEqual(first["omitted_blocks"], {"thinking": 1})
    self.assertTrue(REPLY_MARK in json.dumps(first["content"]))
    self.assertEqual(record["actual"]["billed_usd"], "unknown")
    self.assertEqual(record["actual"]["usage"]["output_tokens"], 20 * 30)
    self.assertEqual(record["actual"]["usage"]["input_tokens"], 20 * 100)
    self.assertEqual(record["actual"]["usage"]["cache_read_input_tokens"], 20 * 50)
    self.assertTrue(record["actual"]["usage_complete"])
    self.assertIn("usd_list", record["estimate"])
    self.assertEqual(record["status"], "complete")
    blob = self.everything_written()
    # Positive control: the marks were sent to the relay and the reply mark is stored, so the scan can fail.
    sent = relay.requests[0]["raw"].decode("utf-8")
    for mark in (USER_MARK, ASSISTANT_MARK, RESULT_MARK, SYSTEM_MARK):
      self.assertTrue(mark in sent, mark + " was not sent")
      self.assertFalse(mark in blob, mark + " (prior context) was stored in the run directory")
    self.assertTrue(REPLY_MARK in blob)


class CannotReplay(ReplayCase):
  def assert_cannot_replay(self, relay, spec, reason_part):
    result, payload = self.replay(spec)
    self.assertEqual(payload.get("status"), "cannot_replay", result.stdout + result.stderr)
    self.assertEqual(payload["next_layer"], 3)
    self.assertTrue(reason_part in payload["reason"], payload["reason"])
    self.assertEqual(len(relay.requests), 0)
    self.assertFalse(self.judge_input_path().exists(), "no judge input may exist when replay is impossible")
    self.assertEqual(self.record()["status"], "cannot_replay")
    self.assertEqual(self.record()["result"]["next_layer"], 3)

  def test_missing_session_record_points_to_layer_3(self):
    relay = self.relay()
    self.assert_cannot_replay(relay, self.spec(relay, session=self.root / "gone.jsonl"), "session file")

  def test_missing_rule_version_points_to_layer_3(self):
    relay = self.relay()
    session = self.write_session(session_rows("other"))
    self.assert_cannot_replay(relay, self.spec(relay, session=session), "rule version")

  def test_no_instructions_attachment_points_to_layer_3(self):
    relay = self.relay()
    session = self.write_session(session_rows("new", instructions=False))
    self.assert_cannot_replay(relay, self.spec(relay, session=session), "no instructions attachment")

  def test_replayable_session_is_not_reported_as_impossible(self):
    # Positive control for the three cases above: the same shapes of input, with the rule in place, replay.
    relay = self.relay()
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    self.assertIsNone(payload.get("next_layer"))


class Failures(ReplayCase):
  def test_transient_errors_are_retried_and_do_not_count_as_attempts(self):
    script = {0: error_reply(429), 1: error_reply(500), 2: (200, {"not": "a reply"}, 0)}

    def reply(index, body, owner):
      return script[index] if index in script else FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    record = self.record()
    self.assertEqual(len(relay.requests), 23)
    self.assertEqual(len(record["attempts"]), 20)
    # The third try answered 200 with a body that is no reply; that is a relay fault, so it was retried too.
    self.assertEqual([t["status"] for t in record["attempts"][0]["tries"]], [429, 500, 200, 200])
    self.assertEqual(record["attempts"][0]["outcome"], "ok")

  def test_retries_that_run_out_leave_the_run_incomplete_not_impossible(self):
    def reply(index, body, owner):
      return error_reply(503) if index < 4 else FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
    self.assertIsNone(payload.get("next_layer"))
    self.assertEqual(len(relay.requests), 4)  # one call and three retries
    self.assertFalse(self.judge_input_path().exists())
    record = self.record()
    self.assertEqual(record["attempts"][0]["outcome"], "failed")
    self.assertEqual([t["status"] for t in record["attempts"][0]["tries"]], [503, 503, 503, 503])

  def test_timeout_is_a_transient_error(self):
    def reply(index, body, owner):
      return (200, answer(VIOLATING), 1.5) if index == 0 else FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    result, payload = self.replay(self.spec(relay), "--timeout", 0.3)
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    self.assertEqual(self.record()["attempts"][0]["tries"][0]["status"], 0)

  def test_http_200_error_envelope_is_a_transient_error_not_a_sample(self):
    marker = "MARK-ENVELOPE-CONTENT"
    envelopes = [
      {"type": "error", "error": {"type": "overloaded_error", "message": "busy"},
       "content": [{"type": "text", "text": marker}]},
      {"error": {"message": "busy"}, "content": [{"type": "text", "text": marker}]},
    ]
    for number, envelope in enumerate(envelopes):
      with self.subTest(envelope=sorted(envelope)):
        # Retries run out: the run stops incomplete and no envelope content is a sample.
        stuck = self.relay(lambda i, b, o: (200, envelope, 0) if i < 4 else FakeRelay.by_version(i, b, o))
        result, payload = self.replay(self.spec(stuck), run_id="stuck-" + str(number))
        self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
        self.assertEqual(len(stuck.requests), 4)
        self.assertEqual(self.record("stuck-" + str(number))["attempts"][0]["outcome"], "failed")
        self.assertFalse((self.out / ("stuck-" + str(number)) / "judge-input.json").exists())
        # One envelope, then a real reply: the envelope was retried and its content is nowhere on record.
        brief = self.relay(lambda i, b, o: (200, envelope, 0) if i == 0 else FakeRelay.by_version(i, b, o))
        done, finished = self.replay(self.spec(brief), run_id="brief-" + str(number))
        self.assertEqual(finished.get("status"), "complete", done.stdout + done.stderr)
        first = self.record("brief-" + str(number))["attempts"][0]
        self.assertEqual([t["status"] for t in first["tries"]], [200, 200])
        self.assertEqual(len(brief.requests), 21)
    # Positive control: the marker is real content of the envelope that was refused.
    self.assertTrue(marker in json.dumps(envelopes))
    self.assertFalse(marker in self.everything_written(), "error envelope content reached the run record")

  def test_unavailable_model_goes_to_layer_4_without_a_verdict(self):
    missing = {"type": "error", "error": {"type": "not_found_error", "message": "model: relay/fixture-model:high"}}

    def reply(index, body, owner):
      return (404, missing, 0) if index == 2 else FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "model_unavailable", result.stdout + result.stderr)
    self.assertEqual(payload["next_layer"], 4)
    self.assertEqual(len(relay.requests), 3)  # no retry of an unavailable model, and nothing after it
    self.assertFalse(self.judge_input_path().exists())
    self.assertEqual(self.record()["result"]["next_layer"], 4)


class Interrupted(ReplayCase):
  def test_failed_run_makes_no_judge_input_and_resume_shows_the_estimate_first(self):
    broken = {"on": True}

    def reply(index, body, owner):
      if broken["on"] and index >= 7:
        return error_reply(500)
      return FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    spec = self.spec(relay)
    result, payload = self.replay(spec)
    self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
    self.assertFalse(self.judge_input_path().exists())
    done = len([a for a in self.record()["attempts"] if a["outcome"] == "ok"])
    self.assertEqual(done, 7)
    before = len(relay.requests)
    broken["on"] = False
    # Resume without approval: the estimate is shown again, for the remaining calls only, and nothing is sent.
    again, shown = self.run_cli("run", "--spec", spec, "--out-dir", self.out, "--resume", self.out / "run-1",
                                "--gap", 0, "--retry-wait", 0, "--chars-per-token", 4)
    self.assertEqual(shown.get("status"), "awaiting_approval", again.stdout + again.stderr)
    self.assertEqual(shown["estimate"]["calls_remaining"]["total"], 20 - done)
    self.assertEqual(len(relay.requests), before)
    self.assertFalse(self.judge_input_path().exists())
    # Positive control: with approval the same resume finishes and only then a judge input exists.
    final, finished = self.run_cli("run", "--spec", spec, "--out-dir", self.out, "--resume", self.out / "run-1",
                                   "--gap", 0, "--retry-wait", 0, "--chars-per-token", 4, "--approve")
    self.assertEqual(finished.get("status"), "complete", final.stdout + final.stderr)
    self.assertEqual(len(relay.requests) - before, 20 - done)
    self.assertTrue(self.judge_input_path().exists())
    self.assertEqual(len(self.record()["attempts"]), 20 + 1)  # the failed attempt stays on the record

  def test_interrupt_stops_without_a_verdict(self):
    hit = threading.Event()

    def reply(index, body, owner):
      if index == 2:
        hit.set()
        return 200, answer(VIOLATING), 20
      return FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    spec = self.spec(relay)
    process = subprocess.Popen(
      [sys.executable, "-I", str(SCRIPT), "run", "--spec", str(spec), "--out-dir", str(self.out), "--run-id", "run-1",
       "--gap", "0", "--retry-wait", "0", "--timeout", "60", "--approve"],
      text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, KEY_ENV: KEY})
    self.assertTrue(hit.wait(20), "the run never reached the third call")
    time.sleep(0.2)
    process.send_signal(signal.SIGINT)
    out, err = process.communicate(timeout=20)
    payload = json.loads(out)
    self.assertEqual(payload["status"], "incomplete", out + err)
    self.assertTrue("interrupted" in payload["reason"])
    self.assertFalse(self.judge_input_path().exists())
    record = self.record()
    self.assertEqual(record["status"], "incomplete")
    self.assertEqual(len([a for a in record["attempts"] if a["outcome"] == "ok"]), 2)


class JudgeModelDown(ReplayCase):
  TEXT_CONDITION = "回應用遞迴搜尋掃 home 根目錄，卻沒有排除目錄"

  @staticmethod
  def reading(_index, body, _owner):
    text = json.dumps(body, ensure_ascii=False)
    outcome = "followed" if "--exclude-dir" in text else "violated"
    return 200, {"content": [{"type": "text", "text": json.dumps({"outcome": outcome, "progressed": True})}]}, 0

  def test_precheck_judge_failure_leaves_the_run_incomplete_and_resumable(self):
    state = {"down": True}
    judge = self.relay(lambda i, b, o: error_reply(503) if state["down"] else self.reading(i, b, o))
    relay = self.relay()
    judge_args = ("--judge-endpoint", judge.origin + "/v1/messages", "--judge-model", "judge-x")
    spec = self.spec(relay, condition=self.TEXT_CONDITION)
    result, payload = self.replay(spec, *judge_args)
    self.assertFalse("Traceback" in result.stderr, "the judge model failure escaped as a traceback")
    self.assertEqual(result.returncode, 13, result.stdout + result.stderr)
    self.assertEqual(payload.get("status"), "incomplete")
    self.assertTrue("judge" in payload["reason"], payload["reason"])
    self.assertEqual(self.record()["status"], "incomplete")
    self.assertFalse(self.judge_input_path().exists())
    sent = len(relay.requests)
    self.assertEqual(sent, 5)
    # Resume once the judge model is back: the run finishes from the five replies already on record.
    state["down"] = False
    final, finished = self.run_cli("run", "--spec", spec, "--out-dir", self.out, "--resume", self.out / "run-1",
                                   "--gap", 0, "--retry-wait", 0, "--chars-per-token", 4, "--approve", *judge_args)
    self.assertEqual(finished.get("status"), "complete", final.stdout + final.stderr)
    self.assertEqual(len(relay.requests) - sent, 15)


class ResumeIdentity(ReplayCase):
  def broken_run(self):
    state = {"on": True}

    def reply(index, body, owner):
      if state["on"] and index >= 7:
        return error_reply(500)
      return FakeRelay.by_version(index, body, owner)

    relay = self.relay(reply)
    spec = self.spec(relay)
    result, payload = self.replay(spec)
    self.assertEqual(payload.get("status"), "incomplete", result.stdout + result.stderr)
    state["on"] = False
    return relay, spec

  def resume(self, spec):
    return self.run_cli("run", "--spec", spec, "--out-dir", self.out, "--resume", self.out / "run-1",
                        "--gap", 0, "--retry-wait", 0, "--chars-per-token", 4, "--approve")

  def test_resume_refuses_a_spec_that_differs_from_the_recorded_experiment(self):
    relay, spec = self.broken_run()
    before = len(relay.requests)
    on_disk = (self.out / "run-1" / "run.json").read_bytes()
    other_session = self.root / "other-session.jsonl"
    other_session.write_text((self.root / "session.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
    cases = {
      "change.id": lambda b: b["change"].update(id="chg-another-change"),
      "change.rule_path": lambda b: b["change"].update(rule_path="/home/tester/.claude/rules/common/other.md"),
      "change.old_text": lambda b: b["change"].update(old_text=OLD_TEXT + " Changed."),
      "change.new_text": lambda b: b["change"].update(new_text=NEW_TEXT + " Changed."),
      "event.session_file": lambda b: b["event"].update(session_file=str(other_session)),
      "event.response_line": lambda b: b["event"].update(response_line=EVENT_LINE + 1),
      "route.label": lambda b: b["route"].update(label="another-relay"),
      "condition": lambda b: b.update(condition="predicate:stale-element-index@gate-2026-11-06-v1"),
      "purpose": lambda b: b.update(purpose="side_effect"),
    }
    for label, change in cases.items():
      with self.subTest(label=label):
        result, _ = self.resume(self.mutated(spec, change))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue(label in result.stderr, result.stderr)
        self.assertEqual(len(relay.requests), before)
        self.assertEqual((self.out / "run-1" / "run.json").read_bytes(), on_disk)
    # The model string comes from the session record, so rewrite that record in place.
    rows = session_rows("new")
    for row in rows:
      if "requestedModel" in row:
        row["requestedModel"] = "relay/another-model:high"
    self.write_session(rows)
    result, _ = self.resume(spec)
    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
    self.assertTrue("model" in result.stderr, result.stderr)
    self.assertEqual(len(relay.requests), before)
    self.assertEqual((self.out / "run-1" / "run.json").read_bytes(), on_disk)
    # Positive control: the original spec and session still resume to the end.
    self.write_session(session_rows("new"))
    final, finished = self.resume(spec)
    self.assertEqual(finished.get("status"), "complete", final.stdout + final.stderr)
    self.assertGreater(len(relay.requests), before)


class Precheck(ReplayCase):
  def test_original_that_does_not_recur_is_an_unfaithful_rebuild_with_no_judge_input(self):
    relay = self.relay(lambda i, b, o: (200, answer(COMPLIANT), 0))
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "unfaithful", result.stdout + result.stderr)
    self.assertTrue("重建不忠實" in payload["reason"])
    self.assertEqual(len(relay.requests), 5)  # the precheck only; the swapped arm is never paid for
    self.assertFalse(self.judge_input_path().exists())
    self.assertEqual(self.record()["precheck"], {"runs": 5, "reproduced": 0, "required": 2, "passed": False})

  def test_exactly_two_recurrences_pass_the_precheck(self):
    # Control for the case above: the boundary value passes and the run goes on to both arms.
    relay = self.relay(new_violations=2)
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    self.assertEqual(self.record()["precheck"]["reproduced"], 2)
    self.assertEqual(len(relay.requests), 20)

  def test_one_recurrence_fails_the_precheck(self):
    relay = self.relay(new_violations=1)
    result, payload = self.replay(self.spec(relay))
    self.assertEqual(payload.get("status"), "unfaithful", result.stdout + result.stderr)
    self.assertEqual(len(relay.requests), 5)


class SideEffects(ReplayCase):
  def unrelated_session(self, recorded="new"):
    return self.write_session(session_rows(recorded, marker="-unrelated", event_text=UNRELATED),
                              name="unrelated.jsonl")

  def test_an_unrelated_moment_replays_with_the_same_flow_and_merges_into_the_judge_input(self):
    relay = self.relay(lambda i, b, o: (200, answer(UNRELATED), 0))
    primary = self.relay()
    self.complete_run(primary)
    spec = self.spec(relay, session=self.unrelated_session(), purpose="side_effect")
    result, payload = self.replay(spec, "--runs", 3, run_id="side-1")
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)
    arms = self.split_requests(relay)
    self.assertEqual((len(arms["new"]), len(arms["old"])), (3, 3))  # no precheck: the two arms only
    record = self.record("side-1")
    self.assertEqual(record["purpose"], "side_effect")
    self.assertNotIn("precheck", record)
    self.assertEqual(record["model_config"]["model"], MODEL)
    merged = self.root / "merged.json"
    done, shown = self.run_cli("judge-input", "--run", self.out / "run-1", "--side-effects", self.out / "side-1",
                               "--out", merged)
    self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
    combined = json.loads(merged.read_text(encoding="utf-8"))
    self.assertEqual((len(combined["side_effects"]["old"]), len(combined["side_effects"]["new"])), (3, 3))
    self.assertEqual((len(combined["runs"]["old"]), len(combined["runs"]["new"])), (10, 10))
    self.assertTrue(UNRELATED in json.dumps(combined["side_effects"]["old"][0]["content"]))

  def test_a_moment_where_the_change_was_not_loaded_cannot_serve_as_a_side_effect_check(self):
    relay = self.relay()
    spec = self.spec(relay, session=self.unrelated_session("old"), purpose="side_effect")
    result, payload = self.replay(spec, run_id="side-2")
    self.assertEqual(payload.get("status"), "cannot_replay", result.stdout + result.stderr)
    self.assertTrue("not loaded" in payload["reason"])
    self.assertEqual(len(relay.requests), 0)

  def test_judge_input_refuses_an_unfinished_side_effect_run(self):
    primary = self.relay()
    self.complete_run(primary)
    relay = self.relay(lambda i, b, o: error_reply(500))
    result, payload = self.replay(self.spec(relay, session=self.unrelated_session(), purpose="side_effect"),
                               "--runs", 3, run_id="side-3")
    self.assertEqual(payload.get("status"), "incomplete")
    done, _ = self.run_cli("judge-input", "--run", self.out / "run-1", "--side-effects", self.out / "side-3",
                           "--out", self.root / "refused.json")
    self.assertNotEqual(done.returncode, 0)
    self.assertFalse((self.root / "refused.json").exists())


class SideEffectMerge(ReplayCase):
  def side_run(self, run_id, session=None, **changes):
    relay = self.relay(lambda i, b, o: (200, answer(UNRELATED), 0))
    session = session or self.write_session(session_rows("new", marker="-unrelated", event_text=UNRELATED),
                                            name=run_id + ".jsonl")
    result, payload = self.replay(self.spec(relay, session=session, purpose="side_effect", **changes),
                                  "--runs", 3, run_id=run_id)
    self.assertEqual(payload.get("status"), "complete", result.stdout + result.stderr)

  def test_judge_input_refuses_side_effect_runs_of_another_model_route_or_rule_version(self):
    self.complete_run()
    other_model = session_rows("new", marker="-unrelated", event_text=UNRELATED)
    for row in other_model:
      if "requestedModel" in row:
        row["requestedModel"] = "relay/another-model:high"
    self.side_run("side-model", session=self.write_session(other_model, name="other-model.jsonl"))
    relay = self.relay()
    self.side_run("side-route", route={"relay": relay.origin, "path": "/v1/messages", "key_env": KEY_ENV,
                                       "label": "another-relay"})
    self.side_run("side-rule")
    path = self.out / "side-rule" / "run.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["rule_versions"]["new_text"] = NEW_TEXT + " Changed."
    path.write_text(json.dumps(record), encoding="utf-8")
    for name, label in (("side-model", "model"), ("side-route", "route"), ("side-rule", "rule")):
      with self.subTest(side=name):
        target = self.root / (name + ".json")
        done, _ = self.run_cli("judge-input", "--run", self.out / "run-1", "--side-effects", self.out / name,
                               "--out", target)
        self.assertEqual(done.returncode, 2, done.stdout + done.stderr)
        self.assertTrue(label in done.stderr, done.stderr)
        self.assertFalse(target.exists())


class JudgeHandoff(ReplayCase):
  def test_finished_run_writes_the_judge_input_and_decides_nothing(self):
    _, payload = self.complete_run()
    document = json.loads(self.judge_input_path().read_text(encoding="utf-8"))
    self.assertEqual((document["schema_version"], document["level"], document["change_id"]), (1, 2, CHANGE))
    self.assertEqual((document["model"], document["route"], document["by"], document["at"]),
                     (MODEL, "fixture-relay", "main", JUDGED_AT))
    self.assertEqual(document["condition"], PREDICATE)
    self.assertEqual(document["evidence"]["quote"], "replayed reply of the old paragraph")
    self.assertEqual((len(document["runs"]["old"]), len(document["runs"]["new"])), (10, 10))
    ids = [run["id"] for side in ("old", "new") for run in document["runs"][side]]
    self.assertEqual(len(set(ids)), 20)
    for side in ("old", "new"):
      for run in document["runs"][side]:
        self.assertTrue(run["content"] and all(b["type"] in ("text", "tool_use") for b in run["content"]))
    # The old group holds the replies to the old paragraph: every one of them is the violating command.
    def commands(run):
      return [b["input"]["command"] for b in run["content"] if b["type"] == "tool_use"]

    self.assertTrue(all(commands(run) == [VIOLATING] for run in document["runs"]["old"]))
    self.assertTrue(any(commands(run) == [COMPLIANT] for run in document["runs"]["new"]))
    # The tool decides nothing and writes no store: its output and record carry no verdict, and the run
    # directory holds the record and the judge input only.
    self.assertNotIn("verdict", payload)
    self.assertNotIn("verdict", self.record())
    self.assertNotIn("verdict", document)
    written = sorted(str(p.relative_to(self.out)) for p in self.out.rglob("*") if p.is_file())
    self.assertEqual(written, ["run-1/judge-input.json", "run-1/run.json"])


class BoundaryAndMutationGuards(ReplayCase):
  def start_raw_relay(self, script_fn):
    """Raw TCP server for truncated HTTP responses; script_fn(idx) returns raw bytes."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    stop = threading.Event()
    calls = []

    def serve():
      srv.settimeout(0.2)
      while not stop.is_set():
        try:
          conn, _ = srv.accept()
        except socket.timeout:
          continue
        except OSError:
          break
        try:
          conn.settimeout(1.0)
          buf = b""
          while b"\r\n\r\n" not in buf:
            chunk = conn.recv(4096)
            if not chunk:
              break
            buf += chunk
          hdr, _, rest = buf.partition(b"\r\n\r\n")
          cl = 0
          for ln in hdr.split(b"\r\n"):
            if ln.lower().startswith(b"content-length:"):
              cl = int(ln.split(b":", 1)[1].strip())
          while len(rest) < cl:
            chunk = conn.recv(4096)
            if not chunk:
              break
            rest += chunk
          idx = len(calls)
          calls.append(rest)
          conn.sendall(script_fn(idx, rest))
          conn.shutdown(socket.SHUT_WR)
          time.sleep(0.02)
        except OSError:
          pass
        finally:
          conn.close()
      srv.close()

    threading.Thread(target=serve, daemon=True).start()
    self.addCleanup(stop.set)
    return f"http://127.0.0.1:{port}", calls

  def test_exit_codes_for_cannot_replay_unfaithful_and_model_unavailable(self):
    # Explicit exit code contract check (kills M-R12).
    relay = self.relay()
    r_cannot, _ = self.replay(self.spec(relay, session=self.root / "missing.jsonl"), run_id="ec-10")
    self.assertEqual(r_cannot.returncode, 10)
    relay_unf = self.relay(lambda i, b, o: (200, answer(COMPLIANT), 0))
    r_unf, _ = self.replay(self.spec(relay_unf), run_id="ec-11")
    self.assertEqual(r_unf.returncode, 11)
    missing = {"type": "error", "error": {"type": "not_found_error", "message": "model: relay/fixture-model:high"}}
    relay_unavail = self.relay(lambda i, b, o: (404, missing, 0))
    r_unavail, _ = self.replay(self.spec(relay_unavail), run_id="ec-12")
    self.assertEqual(r_unavail.returncode, 12)

  def test_truncated_http_408_retry_and_malformed_usage_handling(self):
    # Truncated HTTP (IncompleteRead) is retried as transient without Traceback (R-BUG-1).
    def raw_script(idx, _raw):
      if idx < 4:
        return b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{\"content\":"
      body = json.dumps(answer(VIOLATING)).encode("utf-8")
      return f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body

    origin, calls = self.start_raw_relay(raw_script)
    spec_path = self.spec(self.relay())
    spec_path = self.mutated(spec_path, lambda b: b["route"].update(relay=origin))
    res, payload = self.replay(spec_path, run_id="trunc-1")
    self.assertEqual(res.returncode, 13, res.stdout + res.stderr)
    self.assertNotIn("Traceback", res.stderr)
    self.assertEqual(payload["status"], "incomplete")
    self.assertEqual(len(calls), 4)

    # HTTP 408 is retried as transient (kills M-R09), and malformed usage marks usage_complete=False (R-BUG-4).
    def reply_408_and_bad_usage(index, body, owner):
      if index == 0:
        return error_reply(408)
      status, ans, delay = FakeRelay.by_version(index, body, owner)
      if index == 1:
        ans = {**ans, "usage": {"input_tokens": 100, "output_tokens": 30, "cache_read_input_tokens": "bad"}}
      return status, ans, delay

    relay = self.relay(reply_408_and_bad_usage)
    res2, payload2 = self.replay(self.spec(relay), run_id="http-408")
    self.assertEqual(payload2.get("status"), "complete", res2.stdout + res2.stderr)
    self.assertEqual([t["status"] for t in self.record("http-408")["attempts"][0]["tries"]], [408, 200])
    self.assertFalse(self.record("http-408")["actual"]["usage_complete"])

  def test_no_progress_supplement_state_machine_caps_at_one_retry_per_stalled_reply(self):
    # Kills M-R06 (must supplement once) and M-R07 (must NOT supplement a second time if retry stalls).
    silent = {"content": [{"type": "text", "text": "ok"}], "usage": USAGE}

    # Case 1: swapped arm stalls once on its first call, then progresses -> 11 ok attempts on swapped arm (10 progressed).
    swapped_seen = {"n": 0}

    def stall_once(index, body, owner):
      if version_in(body) == "old":
        swapped_seen["n"] += 1
        if swapped_seen["n"] == 1:
          return 200, silent, 0
      return FakeRelay.by_version(index, body, owner)

    relay1 = self.relay(stall_once)
    res1, payload1 = self.replay(self.spec(relay1), run_id="stall-once")
    self.assertEqual(payload1.get("status"), "complete", res1.stdout + res1.stderr)
    swapped_ok = [a for a in self.record("stall-once")["attempts"] if a["arm"] == "swapped" and a["outcome"] == "ok"]
    self.assertEqual(len(swapped_ok), 11)
    self.assertEqual(sum(1 for a in swapped_ok if a["progressed"]), 10)

    # Case 2: swapped arm stalls on call 1 AND on its supplement call 11 -> stops at 11 ok (9 progressed), no 12th call.
    swapped_seen2 = {"n": 0}

    def stall_supplement_too(index, body, owner):
      if version_in(body) == "old":
        swapped_seen2["n"] += 1
        if swapped_seen2["n"] in (1, 11):
          return 200, silent, 0
      return FakeRelay.by_version(index, body, owner)

    relay2 = self.relay(stall_supplement_too)
    res2, payload2 = self.replay(self.spec(relay2), run_id="stall-twice")
    self.assertEqual(payload2.get("status"), "complete", res2.stdout + res2.stderr)
    swapped_ok2 = [a for a in self.record("stall-twice")["attempts"] if a["arm"] == "swapped" and a["outcome"] == "ok"]
    self.assertEqual(len(swapped_ok2), 11)
    self.assertEqual(sum(1 for a in swapped_ok2 if a["progressed"]), 9)

  def test_balanced_arm_scheduling_after_precheck(self):
    # Kills M-R08: after 5 precheck calls on original, swapped must catch up before original runs #6.
    relay, _ = self.complete_run(run_id="balanced-1")
    arms_order = [a["arm"] for a in self.record("balanced-1")["attempts"]]
    self.assertEqual(arms_order[:5], ["original"] * 5)
    self.assertEqual(arms_order[5:10], ["swapped"] * 5)
    # After 10 calls (5 each), both arms alternate within a spread of at most 1.
    self.assertEqual(arms_order[10], "original")
    self.assertEqual(arms_order[11], "swapped")

  def test_duplicate_paragraph_removed_deferred_tool_version_disagreement_and_cycle(self):
    # M-R02: duplicate target paragraph in rule file -> cannot_replay (exit 10).
    rows_dup = session_rows("new")
    rows_dup[0]["attachment"]["files"][1]["content"] = NEW_TEXT + "\n\n" + NEW_TEXT + "\n"
    relay = self.relay()
    r_dup, p_dup = self.replay(self.spec(relay, session=self.write_session(rows_dup, name="dup.jsonl")), run_id="dup-1")
    self.assertEqual(r_dup.returncode, 10)
    self.assertEqual(p_dup["status"], "cannot_replay")
    self.assertIn("2 times", p_dup["reason"])

    # M-R03: deferred_tools_delta removedNames actually strips the tool from rebuilt request.
    rows_rem = session_rows("new")
    rows_rem.insert(6, {
      "type": "attachment", "uuid": "u6b", "parentUuid": "u6",
      "timestamp": "2026-11-02T00:01:50Z", "sessionId": "fixture-session",
      "attachment": {"type": "deferred_tools_delta", "removedNames": ["DeferredTool"]},
    })
    rows_rem[7]["parentUuid"] = "u6b"
    sess_rem = self.write_session(rows_rem, name="rem.jsonl")
    relay_rem = self.relay()
    sp_rem = self.mutated(self.spec(relay_rem, session=sess_rem), lambda b: b["event"].update(response_line=EVENT_LINE + 1))
    r_rem, p_rem = self.replay(sp_rem, run_id="rem-1")
    self.assertEqual(p_rem.get("status"), "complete", r_rem.stdout + r_rem.stderr)
    self.assertEqual({t["name"] for t in relay_rem.requests[0]["body"]["tools"]}, {"Bash"})
    diffs = {d["id"]: d for d in self.record("rem-1")["differences"]}
    self.assertEqual(diffs["tools_assembled"]["removed"], ["DeferredTool"])

    # M-R04: event_evidence says not_loaded (effective_time > LOAD_TIME) while instructions carry NEW_TEXT -> cannot_replay.
    r_dis, p_dis = self.replay(self.spec(self.relay(), effective="2026-11-02T00:03:00Z"), run_id="disagree-1")
    self.assertEqual(r_dis.returncode, 10)
    self.assertEqual(p_dis["status"], "cannot_replay")
    self.assertIn("disagree", p_dis["reason"])

    # R-BUG-3: cyclic parentUuid -> cannot_replay (exit 10), never infinite loop.
    rows_cyc = session_rows("new")
    rows_cyc[0]["parentUuid"] = rows_cyc[1]["uuid"]
    r_cyc, p_cyc = self.replay(self.spec(self.relay(), session=self.write_session(rows_cyc, name="cyc.jsonl")), run_id="cyc-1")
    self.assertEqual(r_cyc.returncode, 10)
    self.assertEqual(p_cyc["status"], "cannot_replay")
    self.assertIn("cycle", p_cyc["reason"])

  def test_estimate_cap_threshold_exact_equality_empty_key_bad_scheme_and_cli_bounds(self):
    # M-R05: per_call == cap_threshold must NOT apply cap multiplier (strict >).
    relay = self.relay()
    _, est_base = self.replay(self.spec(relay), approve=False, run_id="est-base")
    exact_tokens = est_base["estimate"]["input_tokens_per_call"]
    _, est_eq = self.replay(self.spec(relay), "--cap-threshold", exact_tokens, "--cap-multiplier", 2, approve=False, run_id="est-eq")
    self.assertEqual(est_eq["estimate"]["prices"]["cap_multiplier_applied"], 1.0)
    _, est_gt = self.replay(self.spec(relay), "--cap-threshold", exact_tokens - 1, "--cap-multiplier", 2, approve=False, run_id="est-gt")
    self.assertEqual(est_gt["estimate"]["prices"]["cap_multiplier_applied"], 2.0)

    # R-BUG-2: empty relay key rejected with exit 2.
    r_empty_key, _ = self.replay(self.spec(relay), run_id="empty-key", env={KEY_ENV: ""})
    self.assertEqual(r_empty_key.returncode, 2)
    self.assertIn("not set or empty", r_empty_key.stderr)

    # R-BUG-5: file:// relay rejected, --runs 0/-1 rejected, --chars-per-token 0 rejected, array run.json rejected.
    sp_file = self.mutated(self.spec(relay), lambda b: b["route"].update(relay="file:///etc/passwd"))
    r_file, _ = self.replay(sp_file, run_id="bad-scheme")
    self.assertEqual(r_file.returncode, 2)

    r_runs0, _ = self.replay(self.spec(relay, purpose="side_effect"), "--runs", 0, run_id="runs-0")
    self.assertEqual(r_runs0.returncode, 2)

    r_cpt0, _ = self.replay(self.spec(relay), "--chars-per-token", 0, approve=False, run_id="cpt-0")
    self.assertEqual(r_cpt0.returncode, 2)

    bad_dir = self.out / "bad-resume"
    bad_dir.mkdir(parents=True)
    (bad_dir / "run.json").write_text("[]", encoding="utf-8")
    r_badrec, _ = self.run_cli("run", "--spec", self.spec(relay), "--out-dir", self.out, "--resume", bad_dir, "--approve")
    self.assertEqual(r_badrec.returncode, 2)

    # R-BUG-3: save_json leaves no .tmp behind on failure.
    mod_spec = importlib.util.spec_from_file_location("retro_replay_mod", SCRIPT)
    rmod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(rmod)
    target = self.root / "atomic-clean" / "run.json"
    with self.assertRaises(TypeError):
      rmod.save_json(target, {"bad": object()})
    self.assertFalse(target.with_name(target.name + ".tmp").exists())


if __name__ == "__main__":
  unittest.main()
