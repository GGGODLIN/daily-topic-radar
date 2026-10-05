#!/usr/bin/env python3
"""run/status：失敗來源不能長期擋住同一批未讀來源。"""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('history_queue_fixtures', Path(__file__).with_name('session-audit-history.test.py'))
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)
# 測試服務的等待佇列必須容納分析器同時送出的請求，否則會把連線拒收誤認為排序失敗。
history.fixtures.ThreadingHTTPServer.request_queue_size = 32


class QueueTests(history.HistoryWindowTests):
  def test_failed_history_does_not_starve_unread_history_and_still_blocks_review(self):
    for index in range(10):
      self.history_source(f'a-failed-{index:02}.jsonl', 'BROKEN_REPLY', '2026-10-02T00:00:00Z')
    self.history_source('z-unread.jsonl', 'UNREAD_HISTORY', '2026-10-02T00:00:00Z')
    server, url = self.provider(lambda body: ('not JSON', 200) if 'BROKEN_REPLY' in history.fixtures.user_text(body) else (history.fixtures.analysis([], rule_tags=[]), 200))
    self.assertEqual(self.run_window(url).returncode, 0)
    initial = self.status(url)['history_batch']
    self.assertEqual(initial['failed'], 10)
    self.assertEqual(initial['pending'], 1)
    server.bodies.clear()
    self.assertEqual(self.run_window(url).returncode, 0)
    self.assertTrue(any(b'UNREAD_HISTORY' in body for body in server.bodies), 'Failed sources must leave a send opportunity for unread history')
    batch = self.status(url)['history_batch']
    self.assertEqual(batch['complete'], 1)
    self.assertEqual(batch['failed'], 10)
    self.assertFalse(batch['ready_for_review'])
    server.bodies.clear()
    self.assertEqual(self.run_window(url).returncode, 0)
    self.assertEqual(len(server.bodies), 10, 'Failed sources remain eligible; they were not silently dropped')

  def test_failed_live_sources_do_not_starve_other_live_sources(self):
    for index in range(16):
      history.fixtures.write_jsonl(self.projects / 'work' / f'a-live-failed-{index:02}.jsonl', history.fixtures.user_line('BROKEN_LIVE_REPLY', session_id=f'live-{index}'))
    history.fixtures.write_jsonl(self.projects / 'work' / 'z-live-unread.jsonl', history.fixtures.user_line('UNREAD_LIVE', session_id='unread-live'))
    server, url = self.provider(lambda body: ('not JSON', 200) if 'BROKEN_LIVE_REPLY' in history.fixtures.user_text(body) else (history.fixtures.analysis([], rule_tags=[]), 200))
    command = ['run', *self.flags(url, started_at=history.DEPLOYED)]
    self.assertEqual(self.cli(command).returncode, 0)
    server.bodies.clear()
    self.assertEqual(self.cli(command).returncode, 0)
    self.assertTrue(any(b'UNREAD_LIVE' in body for body in server.bodies), 'Failed live sources must not repeatedly consume every send opportunity')
    self.assertEqual(self.source_named(self.status(url), 'z-live-unread.jsonl')['status'], 'complete')


if __name__ == '__main__':
  suite = unittest.TestSuite(QueueTests(name) for name in QueueTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
