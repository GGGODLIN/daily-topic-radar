#!/usr/bin/env python3
"""run 公共入口：名額一空就補、跑到一半也接新 session、新的嚴格優先、失敗每輪只試一次、到期收尾、不卡住 SessionEnd。"""

import importlib.util
import json
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('scheduler_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures


class SchedulerTests(population.PopulationTests):
  def setUp(self):
    super().setUp()
    fixtures.ThreadingHTTPServer.request_queue_size = 64

  def markers(self, server, prefix):
    return [index for index, body in enumerate(server.bodies) if prefix.encode() in body]

  def test_every_eligible_session_is_sent_in_one_run_without_per_run_caps(self):
    for index in range(60):
      self.source(f'work/s-{index:02}.jsonl', f'NO_CAP_{index:02}')
    server, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)], timeout=30).returncode, 0)
    pending = [row['name'] for row in self.status(url)['sources'] if row['included'] and row['status'] != 'complete']
    self.assertEqual(pending, [], 'A run keeps refilling free slots instead of stopping at a per-run quota')

  def test_session_that_appears_mid_run_is_picked_up_by_the_rescan(self):
    for index in range(3):
      self.source(f'work/slow-{index}.jsonl', f'SLOW_WORK_{index}')
    planted = threading.Event()

    def reply(body):
      if not planted.is_set():
        planted.set()
        self.source('work/z-arrived.jsonl', 'ARRIVED_MID_RUN')
      time.sleep(2)
      return fixtures.analysis([], rule_tags=[]), 200

    server, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url), '--rescan-seconds', '1'], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertTrue(self.markers(server, 'ARRIVED_MID_RUN'), 'A session that becomes ready while slots are busy must not wait for the next run')
    self.assertEqual(self.source_named(self.status(url), 'z-arrived.jsonl')['status'], 'complete')

  def test_history_only_takes_slots_after_new_work_is_dispatched(self):
    self.history_source('a-history.jsonl', 'HISTORY_LAST', '2026-10-02T00:00:00Z')
    for index in range(49):
      self.source(f'work/z-new-{index:02}.jsonl', f'NEW_FIRST_{index:02}', timestamp='2026-10-06T00:00:00Z')
    server, url = self.provider()
    self.assertEqual(self.run_window(url).returncode, 0)
    history = self.markers(server, 'HISTORY_LAST')
    self.assertTrue(history, 'History still runs in the same round once new work is out')
    self.assertGreaterEqual(history[0], 30, 'No slots are held back for history while new sessions are waiting')

  def test_failed_source_is_tried_once_per_run_even_across_rescans(self):
    self.source('work/a-broken.jsonl', 'BROKEN_ONCE')
    for index in range(3):
      self.source(f'work/b-slow-{index}.jsonl', f'SLOW_PEER_{index}')

    def reply(body):
      if 'BROKEN_ONCE' in fixtures.user_text(body):
        return 'not JSON', 200
      time.sleep(2.5)
      return fixtures.analysis([], rule_tags=[]), 200

    server, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url), '--rescan-seconds', '1'], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(len(self.markers(server, 'BROKEN_ONCE')), 1, 'Failed sources wait for the next run instead of looping every rescan')
    self.assertEqual(self.source_named(self.status(url), 'a-broken.jsonl')['status'], 'failed')

  def test_run_stops_starting_work_at_its_lifetime_and_keeps_in_flight_replies(self):
    for index in range(40):
      self.source(f'work/s-{index:02}.jsonl', f'LIFETIME_{index:02}')

    def reply(body):
      time.sleep(1.5)
      return fixtures.analysis([], rule_tags=[]), 200

    server, url = self.provider(reply)
    started = time.monotonic()
    done = self.cli(['run', *self.flags(url), '--max-run-seconds', '1'], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertLess(time.monotonic() - started, 10)
    rows = [row for row in self.status(url)['sources'] if row['included']]
    finished = sum(row['status'] == 'complete' for row in rows)
    self.assertEqual(finished, len(server.bodies), 'Every request already sent is accepted when the run winds down')
    self.assertLess(finished, 40, 'No new work starts after the lifetime')

  def test_session_end_hook_is_not_blocked_while_replies_are_pending(self):
    path = self.source('work/rewritten.jsonl', 'FIRST_VERSION')
    hold = threading.Event()

    def reply(body):
      if 'SECOND_VERSION' in fixtures.user_text(body):
        hold.wait(10)
      return fixtures.analysis([], rule_tags=[]), 200

    server, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.source('work/rewritten.jsonl', 'SECOND_VERSION')
    run = subprocess.Popen([sys.executable, str(fixtures.SCRIPT), 'run', *self.flags(url)], env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    self.addCleanup(lambda: run.poll() is None and run.kill())
    deadline = time.monotonic() + 8
    while not self.markers(server, 'SECOND_VERSION') and time.monotonic() < deadline:
      time.sleep(0.1)
    hook = json.dumps({'hook_event_name': 'SessionEnd', 'session_id': 'other', 'transcript_path': str(path)})
    started = time.monotonic()
    enqueued = self.cli(['enqueue', *self.flags(url)], stdin=hook)
    waited = time.monotonic() - started
    hold.set()
    run.communicate(timeout=20)
    self.assertEqual(enqueued.returncode, 0, enqueued.stderr)
    self.assertLess(waited, 4, 'The run must not hold a write transaction open across an HTTP wait')


if __name__ == '__main__':
  suite = unittest.TestSuite(SchedulerTests(name) for name in SchedulerTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
