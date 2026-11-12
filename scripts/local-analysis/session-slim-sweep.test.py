#!/usr/bin/env python3
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("session-slim-sweep.py")
OLD = time.time() - 3 * 3600
START = "Wed Nov 11 18:30:44 2026"


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
    self.registry = root / "sessions"
    self.registry.mkdir()
    self.procs = root / "procs.json"
    self.procs.write_text("[]")
    self.opened = root / "opened.json"
    self.opened.write_text("[]")
    self.afsctool_calls = root / "afsctool.jsonl"
    fakes = {
      "ps": f'[ -f "{root}/ps-down" ] && exit 1\n'
            f"python3 -c 'import json;[print(\"%6d %s %s\" % tuple(p)) for p in json.load(open(\"{self.procs}\"))]'\n",
      "lsof": f"python3 -c 'import json,sys;hit=sys.argv[-1] in json.load(open(\"{self.opened}\"));hit and print(123);sys.exit(0 if hit else 1)' \"$@\"\n",
      "afsctool": f"python3 -c 'import json,os,sys;open(\"{self.afsctool_calls}\",\"a\").write(json.dumps(sys.argv[1:])+\"\\n\");os.utime(sys.argv[-1],None)' \"$@\"\n",
    }
    for name, body in fakes.items():
      fake = self.bin / name
      fake.write_text("#!/bin/sh\n" + body)
      fake.chmod(0o755)
    self.root = root
    self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", SESSION_SLIM_STATE=str(self.state),
                    SESSION_SLIM_CC_ROOT=str(self.cc), SESSION_SLIM_PI_ROOT=str(self.pi), SESSION_SLIM_AUDIT_STATE=str(self.audit),
                    SESSION_SLIM_REGISTRY=str(self.registry))

  def tearDown(self):
    self.tmp.cleanup()

  def cli(self, *args, stdin="", env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, text=True, capture_output=True,
                          env=self.env if env is None else env, timeout=60, check=False)

  def session(self, name, idle=True, **kwargs):
    path = self.cc / "proj" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(transcript(name, **kwargs))
    if idle:
      os.utime(path, (OLD, OLD))
    return path

  def running(self, session_id, pid, registry=True, start=START, comm="/Users/x/.local/bin/claude"):
    procs = json.loads(self.procs.read_text())
    procs.append([pid, start, comm])
    self.procs.write_text(json.dumps(procs))
    if registry:
      (self.registry / f"{pid}.json").write_text(json.dumps({"pid": pid, "procStart": START, "sessionId": session_id}))

  def pi_session(self, name):
    path = self.pi / "proj" / f"2026-11-01_{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * (300 * 1024))
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
    self.running("living", 101)
    self.running(None, 7, registry=False, comm="/Applications/Claude.app/Contents/Helpers/chrome-native-host")
    sizes = {p: p.stat().st_size for p in (ended, live, fresh, clean)}
    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertLess(ended.stat().st_size, sizes[ended] - 80000)
    self.assertEqual(ended.stat().st_mtime, OLD)
    for untouched in (live, fresh, clean):
      self.assertEqual(untouched.stat().st_size, sizes[untouched])
    second = self.cli("run")
    self.assertIn('"cc": 0', second.stdout)

  def test_queued_session_is_processed_right_after_its_process_exits(self):
    path = self.session("queued", idle=False)
    os.utime(path, (time.time() - 120, time.time() - 120))
    self.cli("enqueue", stdin=json.dumps({"transcript_path": str(path), "session_id": "queued"}))
    size = path.stat().st_size
    proc = self.cli("run")
    self.assertIn('"queued": 1', proc.stdout)
    self.assertLess(path.stat().st_size, size)

  def test_unknown_live_sessions_change_nothing_and_keep_the_queue(self):
    path = self.session("ccc")
    size = path.stat().st_size
    self.cli("enqueue", stdin=json.dumps({"transcript_path": str(path), "session_id": "ccc"}))
    (self.root / "ps-down").write_text("")
    self.assertIn("live sessions unknown: ps failed", self.cli("run").stdout)
    (self.root / "ps-down").unlink()
    self.running("other", 202, registry=False)
    self.assertIn("claude pid 202 has no readable registry", self.cli("run").stdout)
    self.procs.write_text("[]")
    self.running("other", 303, start="Thu Nov 12 01:00:00 2026")
    self.assertIn("claude pid 303 registry does not match", self.cli("run").stdout)
    self.assertEqual(path.stat().st_size, size)
    self.assertEqual(len((self.state / "queue.jsonl").read_text().splitlines()), 1)

  def test_pi_session_held_open_by_a_process_is_not_compressed(self):
    closed, held = self.pi_session("closed"), self.pi_session("held")
    self.opened.write_text(json.dumps([str(held)]))
    proc = self.cli("run")
    self.assertIn('"pi": 1', proc.stdout, proc.stderr)
    logged = [json.loads(line)["path"] for line in (self.state / "log.jsonl").read_text().splitlines()]
    self.assertEqual(logged, [str(closed)])

  def test_disabled_changes_nothing(self):
    path = self.session("ddd")
    size = path.stat().st_size
    self.state.mkdir(exist_ok=True)
    (self.state / "disabled").write_text("")
    self.assertIn("disabled", self.cli("run").stdout)
    self.assertEqual(path.stat().st_size, size)

  def test_cold_plain_text_cc_sessions_compress_and_preserve_mtime_ns(self):
    base_s = int(time.time())
    cold = self.session("cold-plain", idle=False, with_duplicates=False)
    cold_ns = (base_s - 31 * 86400) * 10**9 + 123456789
    os.utime(cold, ns=(cold_ns, cold_ns))

    warm = self.session("warm-plain", idle=False, with_duplicates=False)
    warm_ns = (base_s - 29 * 86400) * 10**9 + 987654321
    os.utime(warm, ns=(warm_ns, warm_ns))

    cold_live = self.session("living-cold", idle=False, with_duplicates=False)
    live_ns = (base_s - 35 * 86400) * 10**9 + 111222333
    os.utime(cold_live, ns=(live_ns, live_ns))
    self.running("living-cold", 404)

    cold_sub = self.cc / "proj" / "living-cold" / "subagents" / "agent-1.jsonl"
    cold_sub.parent.mkdir(parents=True, exist_ok=True)
    cold_sub.write_text(transcript("living-cold", with_duplicates=False))
    os.utime(cold_sub, ns=(live_ns, live_ns))

    tiny = self.cc / "proj" / "tiny-cold.jsonl"
    tiny.write_text('{"type":"user","message":{"content":"short"}}\n')
    os.utime(tiny, ns=(cold_ns, cold_ns))

    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertIn('"cc_cold": 1', proc.stdout)
    calls = [json.loads(line) for line in self.afsctool_calls.read_text().splitlines()]
    self.assertEqual(calls, [["-c", "-T", "LZFSE", str(cold)]])
    self.assertEqual(cold.stat().st_mtime_ns, cold_ns)
    self.assertEqual(warm.stat().st_mtime_ns, warm_ns)
    self.assertEqual(cold_live.stat().st_mtime_ns, live_ns)
    self.assertEqual(cold_sub.stat().st_mtime_ns, live_ns)
    logged = [json.loads(line) for line in (self.state / "log.jsonl").read_text().splitlines()]
    self.assertEqual([row["path"] for row in logged], [str(cold)])
    self.assertEqual(logged[0]["kind"], "cc_cold")

    second = self.cli("run")
    self.assertEqual(second.returncode, 0, second.stderr)
    self.assertIn('"cc_cold": 0', second.stdout)
    self.assertEqual(len(self.afsctool_calls.read_text().splitlines()), 1)

  def test_missing_afsctool_safely_skips_cold_plain_text_cc(self):
    (self.bin / "afsctool").unlink()
    cold = self.session("no-afsctool", idle=False, with_duplicates=False)
    cold_ns = (int(time.time()) - 32 * 86400) * 10**9 + 555666777
    os.utime(cold, ns=(cold_ns, cold_ns))
    no_brew_env = dict(self.env, PATH=f"{self.bin}:/usr/bin:/bin")
    proc = self.cli("run", env=no_brew_env)
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertIn('"cc_cold": 0', proc.stdout)
    self.assertEqual(cold.stat().st_mtime_ns, cold_ns)
    self.assertEqual(json.loads((self.state / "seen.json").read_text()), {})

  @unittest.skipUnless(shutil.which("afsctool", path="/opt/homebrew/bin:/usr/local/bin"), "real afsctool unavailable")
  def test_already_compressed_cold_cc_is_skipped(self):
    real_afsctool = shutil.which("afsctool", path="/opt/homebrew/bin:/usr/local/bin")
    already = self.session("already-compressed", idle=False, with_duplicates=False)
    subprocess.run([real_afsctool, "-c", "-T", "LZFSE", str(already)], check=True, capture_output=True)
    cold_ns = (int(time.time()) - 40 * 86400) * 10**9 + 222333444
    os.utime(already, ns=(cold_ns, cold_ns))
    if not (already.stat().st_flags & stat.UF_COMPRESSED):
      self.skipTest("filesystem did not set UF_COMPRESSED")
    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertIn('"cc_cold": 0', proc.stdout)
    self.assertFalse(self.afsctool_calls.exists())
    self.assertEqual(already.stat().st_mtime_ns, cold_ns)


if __name__ == "__main__":
  unittest.main(verbosity=2)
