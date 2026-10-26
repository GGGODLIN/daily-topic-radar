#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("session-slim-sweep.py")
OLD = time.time() - 3 * 3600


def transcript(session_id, with_duplicates=True):
  lines = [json.dumps({"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "hello " + "x" * 300000}})]
  if with_duplicates:
    lines.append(json.dumps({"type": "api-request-blob", "hash": "h", "message": {"content": "R" * 50000}}))
    lines.append(json.dumps({"type": "user", "sessionId": session_id, "message": {"role": "user", "content": "with image"},
                             "toolUseResult": {"type": "image", "file": {"base64": "A" * 40000, "type": "image/png"}}}))
  return "\n".join(lines) + "\n"


class SweepTest(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    root = Path(self.tmp.name)
    self.cc, self.pi, self.state, self.audit, self.bin = root / "cc", root / "pi", root / "state", root / "audit", root / "bin"
    for path in (self.cc, self.pi, self.audit, self.bin):
      path.mkdir(parents=True)
    self.live = root / "live.json"
    self.live.write_text("[]")
    herdr = self.bin / "herdr"
    herdr.write_text(
      "#!/bin/sh\n"
      f'[ -f "{root}/herdr-down" ] && exit 1\n'
      f"python3 -c 'import json;print(json.dumps({{\"result\":{{\"agents\":[{{\"agent_session\":{{\"value\":v}}}} for v in json.load(open(\"{self.live}\"))]}}}}))'\n"
    )
    herdr.chmod(0o755)
    self.root = root
    self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", SESSION_SLIM_STATE=str(self.state),
                    SESSION_SLIM_CC_ROOT=str(self.cc), SESSION_SLIM_PI_ROOT=str(self.pi), SESSION_SLIM_AUDIT_STATE=str(self.audit))

  def tearDown(self):
    self.tmp.cleanup()

  def cli(self, *args, stdin=""):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, text=True, capture_output=True,
                          env=self.env, timeout=60, check=False)

  def session(self, name, idle=True, **kwargs):
    path = self.cc / "proj" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript(name, **kwargs))
    if idle:
      os.utime(path, (OLD, OLD))
    return path

  def test_enqueue_accepts_only_transcripts_under_the_projects_root(self):
    inside = self.session("aaa")
    self.assertEqual(self.cli("enqueue", stdin=json.dumps({"transcript_path": str(inside), "session_id": "aaa"})).returncode, 0)
    for payload in ('{"transcript_path": "/etc/passwd"}', '{"transcript_path": "/tmp/x.jsonl"}', "not json", "{}"):
      self.assertEqual(self.cli("enqueue", stdin=payload).returncode, 0)
    lines = (self.state / "queue.jsonl").read_text().splitlines()
    self.assertEqual([json.loads(line)["session_id"] for line in lines], ["aaa"])

  def test_enqueue_refuses_symlinked_or_fifo_queue_without_hanging(self):
    inside = self.session("bbb")
    payload = json.dumps({"transcript_path": str(inside)})
    self.state.mkdir()
    target = self.root / "elsewhere.txt"
    target.write_text("keep\n")
    (self.state / "queue.jsonl").symlink_to(target)
    self.assertEqual(self.cli("enqueue", stdin=payload).returncode, 0)
    self.assertEqual(target.read_text(), "keep\n")
    (self.state / "queue.jsonl").unlink()
    os.mkfifo(self.state / "queue.jsonl")
    self.assertEqual(self.cli("enqueue", stdin=payload).returncode, 0)

  def test_run_slims_ended_sessions_and_leaves_live_or_busy_ones(self):
    ended = self.session("ended")
    live = self.session("living")
    fresh = self.session("fresh", idle=False)
    clean = self.session("clean", with_duplicates=False)
    self.live.write_text(json.dumps(["living"]))
    sizes = {p: p.stat().st_size for p in (ended, live, fresh, clean)}
    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertLess(ended.stat().st_size, sizes[ended] - 80000)
    self.assertEqual(ended.stat().st_mtime, OLD)
    for untouched in (live, fresh, clean):
      self.assertEqual(untouched.stat().st_size, sizes[untouched])
    second = self.cli("run")
    self.assertIn('"cc": 0', second.stdout)

  def test_queued_session_is_processed_right_after_it_leaves_herdr(self):
    path = self.session("queued", idle=False)
    os.utime(path, (time.time() - 120, time.time() - 120))
    self.cli("enqueue", stdin=json.dumps({"transcript_path": str(path), "session_id": "queued"}))
    size = path.stat().st_size
    proc = self.cli("run")
    self.assertIn('"queued": 1', proc.stdout)
    self.assertLess(path.stat().st_size, size)

  def test_disabled_or_herdr_unavailable_changes_nothing(self):
    path = self.session("ccc")
    size = path.stat().st_size
    (self.root / "herdr-down").write_text("")
    self.assertIn("herdr agent list unavailable", self.cli("run").stdout)
    self.assertEqual(path.stat().st_size, size)
    (self.root / "herdr-down").unlink()
    self.state.mkdir(exist_ok=True)
    (self.state / "disabled").write_text("")
    self.assertIn("disabled", self.cli("run").stdout)
    self.assertEqual(path.stat().st_size, size)


if __name__ == "__main__":
  unittest.main(verbosity=2)
