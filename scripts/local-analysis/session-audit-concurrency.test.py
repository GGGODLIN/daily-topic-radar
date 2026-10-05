#!/usr/bin/env python3
"""只經 CLI 與 localhost HTTP 驗證併發、續跑及等待時限。"""

import importlib.util
import json
import re
import subprocess
import sys
import threading
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('audit_cli_fixtures', Path(__file__).with_name('session-audit.test.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ConcurrencyCliTests(fixtures.SessionAuditCliTest):
  def provider(self, reply):
    server = fixtures.serve(reply)
    self.addCleanup(fixtures.stop, server)
    return server, f'http://127.0.0.1:{server.server_address[1]}'

  def start_run(self, url):
    return subprocess.Popen(
      [sys.executable, str(fixtures.SCRIPT), 'run', *self.flags(url)],
      text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env,
    )

  def finish_run(self, proc):
    if proc.poll() is None:
      proc.kill()
    proc.communicate(timeout=5)

  def test_ten_distinct_sessions_overlap_but_do_not_exceed_ten(self):
    for index in range(12):
      fixtures.write_jsonl(
        self.projects / 'work' / f's-{index:02}.jsonl',
        fixtures.user_line(f'CONCURRENT_{index:02}', session_id=f'session-{index}'),
      )
    lock = threading.Lock()
    release = threading.Event()
    ten_arrived = threading.Event()
    counts = {'active': 0, 'peak': 0}

    def reply(body):
      with lock:
        counts['active'] += 1
        counts['peak'] = max(counts['peak'], counts['active'])
        if counts['active'] == 10:
          ten_arrived.set()
      try:
        release.wait(10)
        return fixtures.analysis([], rule_tags=[]), 200
      finally:
        with lock:
          counts['active'] -= 1

    server, url = self.provider(reply)
    proc = self.start_run(url)
    try:
      reached = ten_arrived.wait(3)
      release.set()
      out, err = proc.communicate(timeout=12)
      self.assertEqual(proc.returncode, 0, err)
      self.assertTrue(reached, f'Expected ten simultaneous requests, peak={counts["peak"]}')
      self.assertEqual(counts['peak'], 10)
      self.assertEqual(len(server.bodies), 12)
      self.assertEqual(json.loads(out)['fragments'], 12)
      status = self.status(url)
      self.assertEqual(len(status['sources']), 12)
      self.assertTrue(all(row['latest_complete'] for row in status['sources']), status)
    finally:
      release.set()
      self.finish_run(proc)

  def test_sources_with_same_session_id_never_overlap(self):
    fixtures.write_jsonl(
      self.projects / 'work' / 'a.jsonl',
      fixtures.user_line('SAME_SESSION_A', session_id='shared-session'),
    )
    fixtures.write_jsonl(
      self.projects / 'work' / 'b.jsonl',
      fixtures.user_line('SAME_SESSION_B', session_id='shared-session'),
    )
    fixtures.write_jsonl(
      self.projects / 'work' / 'c.jsonl',
      fixtures.user_line('OTHER_SESSION', session_id='other-session'),
    )
    lock = threading.Lock()
    release = threading.Event()
    other_arrived = threading.Event()
    counts = {'shared': 0, 'shared_peak': 0}

    def reply(body):
      shared = 'SAME_SESSION_' in fixtures.user_text(body)
      if shared:
        with lock:
          counts['shared'] += 1
          counts['shared_peak'] = max(counts['shared_peak'], counts['shared'])
      else:
        other_arrived.set()
      try:
        release.wait(10)
        return fixtures.analysis([], rule_tags=[]), 200
      finally:
        if shared:
          with lock:
            counts['shared'] -= 1

    _, url = self.provider(reply)
    proc = self.start_run(url)
    try:
      overlap = other_arrived.wait(3)
      release.set()
      _, err = proc.communicate(timeout=12)
      self.assertEqual(proc.returncode, 0, err)
      self.assertTrue(overlap, 'The other session must progress while the first waits')
      self.assertEqual(counts['shared_peak'], 1)
      self.assertTrue(all(row['latest_complete'] for row in self.status(url)['sources']))
    finally:
      release.set()
      self.finish_run(proc)

  def test_next_fragment_uses_the_previous_fragment_continuity(self):
    fixtures.write_jsonl(
      self.projects / 'work' / 'long.jsonl',
      fixtures.user_line('FRAGMENT_ORDER ' * 3500, session_id='long-session'),
    )
    seen = []

    def reply(body):
      text = fixtures.user_text(body)
      previous = re.search(r'continuity:\n([^\n]*)', text)
      seen.append(previous.group(1) if previous else None)
      return fixtures.openai(json.dumps({
        'continuity': f'finished-{len(seen)}', 'findings': [], 'limitations': [], 'rule_tags': [],
      })), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url)])
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertGreaterEqual(len(seen), 3)
    self.assertEqual(seen[0], '')
    self.assertEqual(seen[1:], [f'finished-{index}' for index in range(1, len(seen))])
    self.assertTrue(self.source_named(self.status(url), 'long.jsonl')['latest_complete'])

  def test_response_after_sixty_seconds_is_still_accepted(self):
    fixtures.write_jsonl(
      self.projects / 'work' / 'slow.jsonl',
      fixtures.user_line('SLOW_RESPONSE', session_id='slow-session'),
    )
    entered = threading.Event()
    release = threading.Event()

    def reply(body):
      entered.set()
      release.wait(75)
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    proc = self.start_run(url)
    try:
      self.assertTrue(entered.wait(8), 'The real HTTP request must reach the provider')
      # 真實等待超過舊的 60 秒；不縮時鐘、不 mock 網路 timeout，也不靠改大被測端時限讓它過關。
      self.assertFalse(release.wait(61.25))
      still_waiting = proc.poll() is None
      release.set()
      _, err = proc.communicate(timeout=12)
      self.assertTrue(still_waiting, 'The analyzer must not terminate the request at sixty seconds')
      self.assertEqual(proc.returncode, 0, err)
      self.assertTrue(self.source_named(self.status(url), 'slow.jsonl')['latest_complete'])
    finally:
      release.set()
      self.finish_run(proc)


if __name__ == '__main__':
  names = [name for name in ConcurrencyCliTests.__dict__ if name.startswith('test_')]
  suite = unittest.TestSuite(ConcurrencyCliTests(name) for name in names)
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
