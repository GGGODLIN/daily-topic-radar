#!/usr/bin/env python3
"""run/scan 公共入口：歷史保留名額、寫入中的 session 先不送、未變來源不重讀。"""

import importlib.util
import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('throughput_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures


class ThroughputTests(population.PopulationTests):
  def end_session(self, url, path):
    hook = json.dumps({'hook_event_name': 'SessionEnd', 'session_id': 'runtime-id', 'transcript_path': str(path)})
    done = self.cli(['enqueue', *self.flags(url)], stdin=hook)
    self.assertEqual(done.returncode, 0, done.stderr)

  def test_history_gets_reserved_slots_while_new_backlog_remains(self):
    fixtures.ThreadingHTTPServer.request_queue_size = 32
    self.history_source('a-history.jsonl', 'HISTORY_RESERVED', '2026-10-02T00:00:00Z')
    for index in range(17):
      self.source(f'work/z-new-{index:02}.jsonl', f'NEW_BACKLOG_{index:02}', timestamp='2026-10-06T00:00:00Z')
    server, url = self.provider()
    self.assertEqual(self.run_window(url).returncode, 0)
    sent = b''.join(server.bodies)
    self.assertIn(b'HISTORY_RESERVED', sent, 'History must not wait for a new backlog that never drains')
    self.assertIn(b'NEW_BACKLOG_', sent)

  def test_session_still_being_written_waits_until_it_ends(self):
    path = self.source('work/live.jsonl', 'STILL_TYPING')
    server, url = self.provider()
    quiet = self.flags(url) + ['--quiet-seconds', '600']
    self.assertEqual(self.cli(['run', *quiet]).returncode, 0)
    self.assertNotIn(b'STILL_TYPING', b''.join(server.bodies), 'A session written moments ago is still open')
    self.assertEqual(self.source_named(self.status(url), 'live.jsonl')['status'], 'pending')
    self.end_session(url, path)
    self.assertEqual(self.cli(['run', *quiet]).returncode, 0)
    self.assertIn(b'STILL_TYPING', b''.join(server.bodies))

  def test_recently_closed_session_goes_before_quiet_unclosed_backlog(self):
    fixtures.ThreadingHTTPServer.request_queue_size = 32
    cutoff = 1_760_000_000
    for index in range(16):
      backlog = self.source(f'work/b-quiet-{index:02}.jsonl', f'QUIET_BACKLOG_{index:02}')
      os.utime(backlog, (cutoff + 50, cutoff + 50))
    closed = self.source('work/a-closed.jsonl', 'JUST_CLOSED')
    os.utime(closed, (cutoff + 10, cutoff + 10))
    server, url = self.provider()
    self.end_session(url, closed)
    started = datetime.fromtimestamp(cutoff, UTC).isoformat()
    self.assertEqual(self.cli(['run', *self.flags(url, started_at=started)]).returncode, 0)
    self.assertIn(b'JUST_CLOSED', b''.join(server.bodies), 'A session that just ended must not wait behind older open work')

  def test_unchanged_sources_are_not_reread_and_changes_are_picked_up(self):
    path = self.source('work/stable.jsonl', 'FIRST_PASS')
    server, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    os.chmod(path, 0)
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    row = self.source_named(self.status(url), 'stable.jsonl')
    self.assertTrue(row['included'], 'An unchanged source keeps its classification without being read again')
    os.chmod(path, 0o600)
    with path.open('a') as handle:
      handle.write(fixtures.user_line('APPENDED_LATER', session_id=path.stem))
    server.bodies.clear()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertIn(b'APPENDED_LATER', b''.join(server.bodies))


if __name__ == '__main__':
  suite = unittest.TestSuite(ThroughputTests(name) for name in ThroughputTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
