#!/usr/bin/env python3
"""run/status 公共入口：最新新來源先送；歷史保留名額的契約在 throughput 測試。"""

import importlib.util
import unittest
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('priority_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures


class PriorityTests(population.PopulationTests):
  def test_newest_pending_session_gets_a_slot_before_older_new_backlog(self):
    fixtures.ThreadingHTTPServer.request_queue_size = 32
    cutoff = 1_760_000_000
    for index in range(16):
      older_new = self.source(f'work/a-older-{index:02}.jsonl', f'OLDER_NEW_{index:02}')
      fixtures.os.utime(older_new, (cutoff + 10, cutoff + 10))
    newest = self.source('work/z-newest.jsonl', 'NEWEST_CLOSED')
    fixtures.os.utime(newest, (cutoff + 20, cutoff + 20))
    started = datetime.fromtimestamp(cutoff, UTC).isoformat()
    server, url = self.provider()
    done = self.cli(['run', *self.flags(url, started_at=started)])
    self.assertEqual(done.returncode, 0, done.stderr)
    sent = b''.join(server.bodies)
    self.assertTrue(b'NEWEST_CLOSED' in sent, 'The latest session must not wait behind the existing new backlog')
    status = self.status(url)
    self.assertTrue(self.source_named(status, 'z-newest.jsonl')['latest_complete'])
    self.assertEqual(sum(r['status'] == 'pending' for r in status['sources'] if r['included']), 1)

  def test_failed_new_source_does_not_block_idle_history_forever(self):
    self.history_source('old.jsonl', 'HISTORY_IDLE', '2026-10-02T00:00:00Z')
    self.source('work/new-failed.jsonl', 'NEW_FAILURE', timestamp='2026-10-06T00:00:00Z')
    server, url = self.provider(lambda body: ('not JSON', 200) if 'NEW_FAILURE' in fixtures.user_text(body) else (fixtures.analysis([], rule_tags=[]), 200))
    self.assertEqual(self.run_window(url).returncode, 0)
    status = self.status(url)
    self.assertEqual(self.source_named(status, 'new-failed.jsonl')['status'], 'failed')
    self.assertTrue(self.source_named(status, 'old.jsonl')['latest_complete'])
    self.assertIn(b'HISTORY_IDLE', b''.join(server.bodies))


if __name__ == '__main__':
  suite = unittest.TestSuite(PriorityTests(name) for name in PriorityTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
