#!/usr/bin/env python3
import fcntl
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

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
    self.details = root / "details.json"
    self.details.write_text("[]")
    self.opened = root / "opened.json"
    self.opened.write_text("[]")
    self.afsctool_calls = root / "afsctool.jsonl"
    fakes = {
      "ps": f'[ -f "{root}/ps-down" ] && exit 1\n'
            f'if [ "$1" = "-p" ]; then\n'
            f'  [ -f "{root}/ps-gone" ] && exit 1\n'
            f'  if [ -f "{root}/ps-detail-down" ]; then echo "ps failed" >&2; exit 1; fi\n'
            f"  python3 -c 'import json,sys;[print(\"%6d %s %s\" % tuple(p)) for p in json.load(open(\"{self.details}\")) if str(p[0]) == sys.argv[1]]' \"$2\"\n"
            f'else\n'
            f"  python3 -c 'import json;[print(\"%6d %s %s\" % tuple(p)) for p in json.load(open(\"{self.procs}\"))]'\n"
            f'fi\n',
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

  def cli(self, *args, stdin="", env=None, timeout=60):
    return subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, text=True, capture_output=True,
                          env=self.env if env is None else env, timeout=timeout, check=False)

  def load_module(self, extra_env=None):
    merged = dict(self.env)
    if extra_env:
      merged.update(extra_env)
    spec = importlib.util.spec_from_file_location("session_slim_sweep_test_mod", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, merged):
      spec.loader.exec_module(module)
    return module

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

  def test_chrome_native_host_does_not_block_ended_sessions(self):
    ended = self.session("ended-with-host")
    live = self.session("living-with-host")
    size, live_size = ended.stat().st_size, live.stat().st_size
    self.running("living-with-host", 101)
    self.running(None, 202, registry=False)
    self.details.write_text(json.dumps([[202, START, "/Users/x/.local/bin/claude --chrome-native-host"]]))
    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertIn('"cc": 1', proc.stdout)
    self.assertLess(ended.stat().st_size, size)
    self.assertEqual(live.stat().st_size, live_size)

  def test_query_only_claude_does_not_block_ended_sessions(self):
    self.running(None, 202, registry=False)
    for flag in ("--version", "-v", "--help", "-h"):
      with self.subTest(flag=flag):
        path = self.session(f"query-{flag}")
        size = path.stat().st_size
        self.details.write_text(json.dumps([[202, START, f"/Users/x/.local/bin/claude {flag}"]]))
        proc = self.cli("run")
        self.assertIn('"cc": 1', proc.stdout)
        self.assertLess(path.stat().st_size, size)

  def test_process_exiting_after_ps_snapshot_does_not_block_sweep(self):
    child = subprocess.Popen(["/usr/bin/true"])
    child.wait(timeout=5)
    self.running(None, child.pid, registry=False)
    (self.root / "ps-gone").write_text("")
    path = self.session("exited-after-snapshot")
    size = path.stat().st_size
    proc = self.cli("run")
    self.assertIn('"cc": 1', proc.stdout)
    self.assertLess(path.stat().st_size, size)

  def test_dialogue_registry_startup_window_waits_for_registration(self):
    child = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    path = self.session("startup-ended")
    live = self.session("startup-living")
    size, live_size = path.stat().st_size, live.stat().st_size
    self.running("startup-living", child.pid, registry=False)
    self.details.write_text(json.dumps([[child.pid, START, "/Users/x/.local/bin/claude --print hello"]]))
    ready = threading.Timer(0.7, lambda: (self.registry / f"{child.pid}.json").write_text(
      json.dumps({"pid": child.pid, "procStart": START, "sessionId": "startup-living"})))
    ready.start()
    try:
      proc = self.cli("run")
      self.assertIn('"cc": 1', proc.stdout)
      self.assertLess(path.stat().st_size, size)
      self.assertEqual(live.stat().st_size, live_size)
      self.assertIsNone(child.poll())
    finally:
      ready.cancel()
      ready.join()
      child.stdin.close()
      child.wait(timeout=5)

  def test_dialogue_registry_teardown_window_waits_for_kernel_exit(self):
    child = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    path = self.session("teardown-ended")
    size = path.stat().st_size
    self.running("teardown-ending", child.pid, registry=False)
    self.details.write_text(json.dumps([[child.pid, START, "/Users/x/.local/bin/claude --print hello"]]))

    def finish():
      self.procs.write_text("[]")
      child.stdin.close()

    exiting = threading.Timer(1.05, finish)
    exiting.start()
    try:
      proc = self.cli("run")
      self.assertIn('"cc": 1', proc.stdout)
      self.assertLess(path.stat().st_size, size)
    finally:
      exiting.cancel()
      exiting.join()
      if not child.stdin.closed:
        child.stdin.close()
      child.wait(timeout=5)

  def test_empty_ps_result_does_not_exempt_a_still_existing_process(self):
    self.running(None, os.getpid(), registry=False)
    (self.root / "ps-gone").write_text("")
    path = self.session("still-alive")
    size = path.stat().st_size
    proc = self.cli("run")
    self.assertIn(f"claude pid {os.getpid()} has no readable registry", proc.stdout)
    self.assertEqual(path.stat().st_size, size)

  def test_detail_query_error_does_not_exempt_an_unknown_process(self):
    self.running(None, 202, registry=False)
    (self.root / "ps-detail-down").write_text("")
    path = self.session("ps-query-error")
    size = path.stat().st_size
    proc = self.cli("run")
    self.assertIn("claude pid 202 has no readable registry", proc.stdout)
    self.assertEqual(path.stat().st_size, size)

  def test_unknown_claude_is_not_exempted_by_a_native_host_flag_in_prompt(self):
    path = self.session("unknown-with-prompt")
    size = path.stat().st_size
    self.running(None, 202, registry=False)
    for args in ('-p "explain --chrome-native-host"', '-p "--version"', '--print "hello"', '--version extra'):
      with self.subTest(args=args):
        self.details.write_text(json.dumps([[202, START, f"/Users/x/.local/bin/claude {args}"]]))
        proc = self.cli("run")
        self.assertIn("claude pid 202 has no readable registry", proc.stdout)
        self.assertEqual(path.stat().st_size, size)

  def test_reused_native_host_pid_does_not_exempt_unknown_claude(self):
    path = self.session("reused-host-pid")
    size = path.stat().st_size
    self.running(None, 202, registry=False)
    self.details.write_text(json.dumps([[202, "Thu Nov 12 01:00:00 2026", "/Users/x/.local/bin/claude --chrome-native-host"]]))
    proc = self.cli("run")
    self.assertIn("claude pid 202 has no readable registry", proc.stdout)
    self.assertEqual(path.stat().st_size, size)

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

  def test_symlinked_cc_root_session_key_and_dedup(self):
    real_cc = self.root / "real_cc"
    (real_cc / "proj").mkdir(parents=True)
    link_cc = self.root / "link_cc"
    link_cc.symlink_to(real_cc)

    live_path = real_cc / "proj" / "live-sess.jsonl"
    live_path.write_text(transcript("live-sess"))
    settled = time.time() - 120
    os.utime(live_path, (settled, settled))
    self.running("live-sess", 101)

    ended_path = real_cc / "proj" / "ended-sess.jsonl"
    ended_path.write_text(transcript("ended-sess"))
    os.utime(ended_path, (OLD, OLD))

    cold_path = real_cc / "proj" / "cold-plain.jsonl"
    cold_path.write_text(transcript("cold-plain", with_duplicates=False))
    cold_ns = (int(time.time()) - 31 * 86400) * 10**9 + 123456789
    os.utime(cold_path, ns=(cold_ns, cold_ns))

    mod = self.load_module({"SESSION_SLIM_CC_ROOT": str(link_cc)})
    with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      self.assertTrue(mod.enqueue(json.dumps({"transcript_path": str(link_cc / "proj" / "live-sess.jsonl"), "session_id": "live-sess"}), state=self.state))
      self.assertTrue(mod.enqueue(json.dumps({"transcript_path": str(link_cc / "proj" / "ended-sess.jsonl"), "session_id": "ended-sess"}), state=self.state))
      self.assertEqual(mod.session_key(live_path.resolve()), "live-sess")
      self.assertEqual(mod.cc_cold_candidates({}, set(), time.time(), exclude={cold_path.resolve()}), [])

      live_size = live_path.stat().st_size
      ended_size = ended_path.stat().st_size
      res = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
      self.assertEqual(res["cc"], 1)
      self.assertEqual(res["cc_cold"], 1)
      self.assertEqual(live_path.stat().st_size, live_size)
      self.assertLess(ended_path.stat().st_size, ended_size)

  def test_take_queue_recovers_orphaned_taking_and_requeues_on_lock_busy(self):
    mod = self.load_module()
    self.state.mkdir(parents=True, exist_ok=True)
    settled = time.time() - 120

    # (a) Orphaned queue.taking.jsonl without queue.jsonl is recovered
    p_a = self.session("s2a", idle=False)
    os.utime(p_a, (settled, settled))
    orig_a = p_a.stat().st_size
    (self.state / "queue.taking.jsonl").write_text(json.dumps({"path": str(p_a.resolve())}) + "\n")
    with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      res_a = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
    self.assertEqual(res_a["cc"], 1)
    self.assertLess(p_a.stat().st_size, orig_a)

    # (b) Both queue.taking.jsonl and queue.jsonl are processed without overwriting
    p_b1 = self.session("s2b-1", idle=False)
    p_b2 = self.session("s2b-2", idle=False)
    os.utime(p_b1, (settled, settled))
    os.utime(p_b2, (settled, settled))
    orig_b1, orig_b2 = p_b1.stat().st_size, p_b2.stat().st_size
    (self.state / "queue.taking.jsonl").write_text(json.dumps({"path": str(p_b1.resolve())}) + "\n")
    (self.state / "queue.jsonl").write_text(json.dumps({"path": str(p_b2.resolve())}) + "\n")
    with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      res_b = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
    self.assertEqual(res_b["cc"], 2)
    self.assertLess(p_b1.stat().st_size, orig_b1)
    self.assertLess(p_b2.stat().st_size, orig_b2)

    # (c) Exclusive flock on audit/run.lock skips and re-queues for next run
    p_c = self.session("s2c", idle=False)
    os.utime(p_c, (settled, settled))
    orig_c = p_c.stat().st_size
    (self.state / "queue.jsonl").write_text(json.dumps({"path": str(p_c.resolve())}) + "\n")
    real_load_slim = mod.load_slim

    def fast_slim():
      s = real_load_slim()
      s.LOCK_WAIT_SECONDS = 0
      return s

    lock_fh = open(self.audit / "run.lock", "a+")
    try:
      fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
      with mock.patch.object(mod, "load_slim", fast_slim), mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
        res_c1 = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
      self.assertEqual(res_c1["cc"], 0)
      self.assertEqual(p_c.stat().st_size, orig_c)
      self.assertIn("session-audit lock busy", (self.state / "log.jsonl").read_text())
    finally:
      lock_fh.close()

    with mock.patch.object(mod, "load_slim", fast_slim), mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      res_c2 = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
    self.assertEqual(res_c2["cc"], 1)
    self.assertLess(p_c.stat().st_size, orig_c)

  def test_sweep_lock_blocks_concurrent_run_and_save_seen_atomic_unique_tmp(self):
    self.state.mkdir(parents=True, exist_ok=True)
    ended = self.session("s3-ended")
    orig = ended.stat().st_size
    self.cli("enqueue", stdin=json.dumps({"transcript_path": str(ended), "session_id": "s3-ended"}))

    lock_fh = open(self.state / "sweep.lock", "a+")
    try:
      fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
      proc = self.cli("run", timeout=5)
    finally:
      lock_fh.close()
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertEqual(json.loads(proc.stdout), {"skip": "sweep lock busy"})
    self.assertEqual(ended.stat().st_size, orig)
    self.assertEqual(len((self.state / "queue.jsonl").read_text().splitlines()), 1)

    mod = self.load_module()
    threads = 8
    barrier = threading.Barrier(threads)
    errors = []

    def worker(tid):
      try:
        barrier.wait(timeout=5)
        for j in range(20):
          mod.save_seen(self.state, {"tid": tid, "iter": j})
      except Exception as exc:  # noqa: BLE001
        errors.append(exc)

    pool = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in pool:
      t.start()
    for t in pool:
      t.join(timeout=10)
    self.assertEqual(errors, [])
    seen_file = self.state / "seen.json"
    self.assertIsInstance(json.loads(seen_file.read_text()), dict)
    self.assertEqual(seen_file.stat().st_mode & 0o777, 0o600)

  def test_live_unknown_during_queue_wait_fails_closed_for_cc_and_pi(self):
    q_path = self.session("q-sess", idle=False)
    settled = time.time() - 120
    os.utime(q_path, (settled, settled))
    ended_cc = self.session("s4-ended-cc")
    cc_size = ended_cc.stat().st_size
    ended_pi = self.pi_session("s4-ended-pi")

    mod = self.load_module()
    mod.QUEUE_WAIT_SECONDS = 0.2
    self.state.mkdir(parents=True, exist_ok=True)
    mod.enqueue(json.dumps({"transcript_path": str(q_path), "session_id": "q-sess"}), state=self.state)

    calls = {"n": 0}

    def fake_live():
      calls["n"] += 1
      if calls["n"] == 1:
        return {"q-sess"}
      raise mod.LiveUnknown("pid 999")

    real_sleep = time.sleep
    with mock.patch.object(mod, "live_sessions", fake_live), \
         mock.patch.object(mod.time, "sleep", lambda s: real_sleep(min(s, 0.02))), \
         mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      res = mod.run(state=self.state, audit_state=self.audit, wait_queue=True)

    self.assertIn("skip", res)
    self.assertEqual(ended_cc.stat().st_size, cc_size)
    self.assertEqual(ended_pi.stat().st_mtime, OLD)
    self.assertFalse(self.afsctool_calls.exists())
    queued_paths = [Path(json.loads(line)["path"]).resolve() for line in (self.state / "queue.jsonl").read_text().splitlines()]
    self.assertIn(q_path.resolve(), queued_paths)

  def test_multiple_transitioning_pids_and_stale_registry_wait(self):
    # (a) Two transitioning pids whose registry files appear at 0.25s and 0.6s
    c1 = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    c2 = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    ended_a = self.session("s5a-ended")
    size_a = ended_a.stat().st_size
    self.running("s5a-1", c1.pid, registry=False)
    self.running("s5a-2", c2.pid, registry=False)
    self.details.write_text(json.dumps([
      [c1.pid, START, "/Users/x/.local/bin/claude --print hello"],
      [c2.pid, START, "/Users/x/.local/bin/claude --print hello"],
    ]))
    t1 = threading.Timer(0.25, lambda: (self.registry / f"{c1.pid}.json").write_text(
      json.dumps({"pid": c1.pid, "procStart": START, "sessionId": "s5a-1"})))
    t2 = threading.Timer(0.6, lambda: (self.registry / f"{c2.pid}.json").write_text(
      json.dumps({"pid": c2.pid, "procStart": START, "sessionId": "s5a-2"})))
    t1.start()
    t2.start()
    try:
      proc_a = self.cli("run")
      self.assertEqual(proc_a.returncode, 0, proc_a.stderr)
      self.assertIn('"cc": 1', proc_a.stdout)
      self.assertLess(ended_a.stat().st_size, size_a)
    finally:
      t1.cancel()
      t2.cancel()
      t1.join()
      t2.join()
      for child in (c1, c2):
        child.stdin.close()
        child.wait(timeout=5)

    # (b) Pre-existing stale mismatched registry file overwritten with valid procStart at 0.3s
    self.procs.write_text("[]")
    self.details.write_text("[]")
    for old_reg in self.registry.glob("*.json"):
      old_reg.unlink()

    c3 = subprocess.Popen(["/bin/cat"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
    ended_b = self.session("s5b-ended")
    size_b = ended_b.stat().st_size
    self.running("s5b-1", c3.pid, registry=False)
    self.details.write_text(json.dumps([[c3.pid, START, "/Users/x/.local/bin/claude --print hello"]]))
    (self.registry / f"{c3.pid}.json").write_text(
      json.dumps({"pid": c3.pid, "procStart": "Thu Nov 12 01:00:00 2026", "sessionId": "s5b-1"}))
    t3 = threading.Timer(0.3, lambda: (self.registry / f"{c3.pid}.json").write_text(
      json.dumps({"pid": c3.pid, "procStart": START, "sessionId": "s5b-1"})))
    t3.start()
    try:
      proc_b = self.cli("run")
      self.assertEqual(proc_b.returncode, 0, proc_b.stderr)
      self.assertIn('"cc": 1', proc_b.stdout)
      self.assertLess(ended_b.stat().st_size, size_b)
    finally:
      t3.cancel()
      t3.join()
      c3.stdin.close()
      c3.wait(timeout=5)

  def test_malformed_json_types_and_empty_queue_path_do_not_crash(self):
    self.assertEqual(self.cli("enqueue", stdin="null").returncode, 0)
    self.assertEqual(self.cli("enqueue", stdin="[]").returncode, 0)

    self.running("s6-live", 501, registry=False)
    (self.registry / "501.json").write_text("null")
    proc_b = self.cli("run")
    self.assertEqual(proc_b.returncode, 0, proc_b.stderr)
    self.assertIn("live sessions unknown", proc_b.stdout)

    self.procs.write_text("[]")
    (self.registry / "501.json").unlink()
    self.state.mkdir(parents=True, exist_ok=True)
    (self.state / "seen.json").write_text("[]")
    ended = self.session("s6-ended")
    orig = ended.stat().st_size
    proc_c = self.cli("run")
    self.assertEqual(proc_c.returncode, 0, proc_c.stderr)
    self.assertIn('"cc": 1', proc_c.stdout)
    self.assertLess(ended.stat().st_size, orig)

    (self.state / "queue.jsonl").write_text('{"path": ""}\n')
    proc_d = self.cli("run")
    self.assertEqual(proc_d.returncode, 0, proc_d.stderr)

  def test_symlink_log_and_fifo_transcript_do_not_crash_or_hang(self):
    # (a) Symlinked state/log.jsonl does not crash or write outside
    self.state.mkdir(parents=True, exist_ok=True)
    outside_log = self.root / "outside_log.txt"
    outside_log.write_text("keep\n")
    (self.state / "log.jsonl").symlink_to(outside_log)
    self.session("s7-ended")
    proc_a = self.cli("run", timeout=5)
    self.assertEqual(proc_a.returncode, 0, proc_a.stderr)
    self.assertEqual(outside_log.read_text(), "keep\n")
    (self.state / "log.jsonl").unlink()

    # (b) FIFO and symlink inside CC_ROOT do not hang or modify outside target
    proj = self.cc / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    fifo_tx = proj / "fifo.jsonl"
    os.mkfifo(fifo_tx)
    os.utime(fifo_tx, (OLD, OLD))
    outside_tx = self.root / "outside_tx.jsonl"
    outside_tx.write_text(transcript("outside-tx"))
    os.utime(outside_tx, (OLD, OLD))
    orig_tx = outside_tx.stat().st_size
    link_tx = proj / "link.jsonl"
    link_tx.symlink_to(outside_tx)
    mod = self.load_module()
    self.assertFalse(mod.has_duplicates(link_tx))
    proc_b = self.cli("run", timeout=5)
    self.assertEqual(proc_b.returncode, 0, proc_b.stderr)
    self.assertEqual(outside_tx.stat().st_size, orig_tx)
    fifo_tx.unlink()
    link_tx.unlink()

    # (c) FIFO at state/queue.jsonl exits 0 without hanging
    os.mkfifo(self.state / "queue.jsonl")
    proc_c = self.cli("run", timeout=5)
    self.assertEqual(proc_c.returncode, 0, proc_c.stderr)

  def test_sqlite_error_and_afsctool_timeout_do_not_abort_batch(self):
    (self.audit / "state.sqlite").write_bytes(b"not a sqlite db")
    ended_cc = self.session("s8-cc")
    cold_cc = self.session("s8-cold", idle=False, with_duplicates=False)
    cold_ns = (int(time.time()) - 31 * 86400) * 10**9 + 123456789
    os.utime(cold_cc, ns=(cold_ns, cold_ns))

    proc = self.cli("run")
    self.assertEqual(proc.returncode, 0, proc.stderr)
    logs = [json.loads(line) for line in (self.state / "log.jsonl").read_text().splitlines()]
    by_path = {Path(row["path"]).resolve(): row for row in logs if "path" in row}
    self.assertIn("error", by_path[ended_cc.resolve()])
    self.assertEqual(by_path[cold_cc.resolve()].get("kind"), "cc_cold")
    self.assertNotIn("error", by_path[cold_cc.resolve()])
    seen_paths = {Path(k).resolve() for k in json.loads((self.state / "seen.json").read_text())}
    self.assertIn(cold_cc.resolve(), seen_paths)
    self.assertNotIn(ended_cc.resolve(), seen_paths)

    # (b) compress_one timeout and non-zero afsctool exit
    mod = self.load_module()
    slow_afsctool = self.root / "afsctool-slow"
    slow_afsctool.write_text("#!/bin/sh\nsleep 2\n")
    slow_afsctool.chmod(0o755)
    cold_slow = self.session("s8-slow", idle=False, with_duplicates=False)
    os.utime(cold_slow, ns=(cold_ns, cold_ns))
    mod.COMPRESS_TIMEOUT_SECONDS = 0.2
    res_timeout = mod.compress_one(cold_slow, str(slow_afsctool))
    self.assertIn("error", res_timeout)

    ended_cc.unlink()
    cold_cc.unlink()
    cold_slow.unlink()
    (self.state / "seen.json").unlink()
    (self.state / "log.jsonl").unlink()
    (self.bin / "afsctool").write_text('#!/bin/sh\necho "compress failed" >&2\nexit 1\n')
    (self.bin / "afsctool").chmod(0o755)
    cold_fail = self.session("s8-fail", idle=False, with_duplicates=False)
    os.utime(cold_fail, ns=(cold_ns, cold_ns))
    with mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
    seen_after = {Path(k).resolve() for k in json.loads((self.state / "seen.json").read_text())}
    self.assertNotIn(cold_fail.resolve(), seen_after)
    fail_logs = [json.loads(line) for line in (self.state / "log.jsonl").read_text().splitlines()]
    self.assertTrue(any(Path(r.get("path", "")).resolve() == cold_fail.resolve() and "error" in r for r in fail_logs))

  def test_toctou_recheck_skips_reopened_or_modified_pi_and_cold_after_cc_lock(self):
    self.session("s9-cc")
    cold_file = self.session("s9-cold", idle=False, with_duplicates=False)
    cold_ns = (int(time.time()) - 31 * 86400) * 10**9 + 123456789
    os.utime(cold_file, ns=(cold_ns, cold_ns))
    pi_file = self.pi_session("s9-pi")

    mod = self.load_module()
    real_load_slim = mod.load_slim

    def mutating_load_slim():
      s = real_load_slim()

      def fake_slim_one(path, audit_state, audit):
        with open(cold_file, "a") as fh:
          fh.write('{"type":"user","message":{"content":"mutated"}}\n')
        self.opened.write_text(json.dumps([str(pi_file), str(pi_file.resolve())]))
        return {"path": str(path), "before": 100, "after": 50}

      s.slim_one = fake_slim_one
      return s

    with mock.patch.object(mod, "load_slim", mutating_load_slim), mock.patch.dict(os.environ, {"PATH": self.env["PATH"]}):
      mod.run(state=self.state, audit_state=self.audit, wait_queue=False)

    self.assertFalse(self.afsctool_calls.exists())

  def test_sol_grafts_spaced_replay_dir_queue_and_unparseable_ps(self):
    # Sol S07: spaced JSON colon in api-request marker is detected
    mod = self.load_module()
    spaced = self.root / "spaced.jsonl"
    spaced.write_text('{"type" : "api-request", "body": "x"}\n')
    self.assertTrue(mod.has_duplicates(spaced))

    # Sol S14: queue.jsonl as directory yields controlled skip
    self.state.mkdir(parents=True, exist_ok=True)
    (self.state / "queue.jsonl").mkdir()
    with mock.patch.object(mod, "_resolve_live_sessions", return_value=set()):
      res = mod.run(state=self.state, audit_state=self.audit, wait_queue=False)
    self.assertIn("queue unavailable", res.get("skip", ""))
    (self.state / "queue.jsonl").rmdir()

    # Sol S13: unparseable claude ps row raises LiveUnknown
    fake_ps = mock.Mock(stdout="bad-row /usr/local/bin/claude\n")
    with mock.patch.object(mod.subprocess, "run", return_value=fake_ps):
      with self.assertRaises(mod.LiveUnknown):
        mod.live_sessions()


if __name__ == "__main__":
  unittest.main(verbosity=2)
