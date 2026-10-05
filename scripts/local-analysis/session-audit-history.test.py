#!/usr/bin/env python3
"""run/status 公共入口：固定回填窗口、review 停點與擴窗續跑。"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('audit_cli_fixtures', Path(__file__).with_name('session-audit.test.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

SINCE = '2026-09-28T10:00:00Z'
UNTIL = '2026-10-05T10:00:00Z'
DEPLOYED = '2026-10-05T09:20:13Z'


class HistoryWindowTests(fixtures.SessionAuditCliTest):
  def provider(self, reply=None):
    server = fixtures.serve(reply if reply is not None else lambda body: (fixtures.analysis([], rule_tags=[]), 200))
    self.addCleanup(fixtures.stop, server)
    return server, f'http://127.0.0.1:{server.server_address[1]}'

  def history_source(self, name, text, timestamp, session_id=None, **extra):
    record = json.loads(fixtures.user_line(text, session_id=session_id if session_id is not None else name, **extra))
    record['timestamp'] = timestamp
    path = self.projects / 'work' / name
    fixtures.write_jsonl(path, json.dumps(record) + '\n')
    # 文件時間刻意不對齊內容，驗證窗口不是把 copied file 的 mtime 當對話時間。
    os.utime(path, (1_700_000_000, 1_700_000_000))
    return path

  def run_window(self, url, since=SINCE):
    return self.cli(['run', *self.flags(url, started_at=DEPLOYED), '--history-since', since, '--history-until', UNTIL])

  def test_seven_day_window_excludes_older_and_synthetic_but_keeps_live_sources(self):
    recent = self.history_source('recent.jsonl', 'RECENT_WORK', '2026-10-02T00:00:00Z')
    older = self.history_source('older.jsonl', 'OLDER_THAN_SEVEN', '2026-09-20T00:00:00Z')
    synthetic = self.history_source('synthetic.jsonl', 'SYNTHETIC_IN_WINDOW', '2026-10-02T00:00:00Z', synthetic=True)
    live = self.projects / 'work' / 'live.jsonl'
    fixtures.write_jsonl(live, fixtures.user_line('LIVE_AFTER_WINDOW', timestamp='2026-10-06T00:00:00Z'))
    before = {p: p.read_bytes() for p in (recent, older, synthetic, live)}
    server, url = self.provider()
    done = self.run_window(url)
    self.assertEqual(done.returncode, 0, done.stderr)
    sent = b''.join(server.bodies)
    self.assertIn(b'RECENT_WORK', sent)
    self.assertIn(b'LIVE_AFTER_WINDOW', sent)
    self.assertNotIn(b'OLDER_THAN_SEVEN', sent)
    self.assertNotIn(b'SYNTHETIC_IN_WINDOW', sent)
    batch = self.status(url)['history_batch']
    self.assertEqual(batch['since'], SINCE)
    self.assertEqual(batch['until'], UNTIL)
    self.assertEqual(batch['days'], 7)
    self.assertEqual(batch['total'], 1)
    self.assertEqual(batch['status'], 'review')
    self.assertTrue(batch['ready_for_review'])
    deferred = self.source_named(self.status(url), 'older.jsonl')
    self.assertEqual(deferred['status'], 'deferred')
    self.assertFalse(deferred['latest_complete'])
    self.assertTrue(all(path.read_bytes() == raw for path, raw in before.items()))
    server.bodies.clear()
    self.assertEqual(self.run_window(url).returncode, 0)
    self.assertEqual(server.bodies, [], 'Reaching review must not automatically schedule older history')

  def test_history_progresses_without_rolling_the_window_and_missed_sources_are_added(self):
    for index in range(12):
      self.history_source(f'old-{index:02}.jsonl', f'BATCH_MEMBER_{index:02}', '2026-10-02T00:00:00Z')
    server, url = self.provider()
    done = self.run_window(url)
    self.assertEqual(done.returncode, 0, done.stderr)
    first = self.status(url)['history_batch']
    self.assertEqual(first['total'], 12)
    self.assertEqual(first['complete'], 10)
    self.assertEqual(first['remaining'], 2)
    self.assertFalse(first['ready_for_review'])
    self.history_source('late.jsonl', 'LATE_DISCOVERY', '2026-10-03T00:00:00Z')
    self.assertEqual(self.run_window(url).returncode, 0)
    final = self.status(url)['history_batch']
    self.assertEqual(final['since'], SINCE)
    self.assertEqual(final['until'], UNTIL)
    self.assertEqual(final['total'], 13)
    self.assertEqual(final['complete'], 13)
    self.assertEqual(final['status'], 'review')
    self.assertEqual(final['remaining'], 0)
    self.assertIn(b'LATE_DISCOVERY', b''.join(server.bodies))

  def test_thirty_day_expansion_reuses_covered_sources_and_keeps_older_history_unqueued(self):
    self.history_source('week.jsonl', 'ALREADY_READ_WEEK', '2026-10-02T00:00:00Z')
    self.history_source('month.jsonl', 'ADDED_IN_MONTH', '2026-09-20T00:00:00Z')
    self.history_source('ancient.jsonl', 'OUTSIDE_MONTH', '2026-08-01T00:00:00Z')
    server, url = self.provider()
    first = self.run_window(url)
    self.assertEqual(first.returncode, 0, first.stderr)
    self.assertEqual(self.status(url)['history_batch']['status'], 'review')
    server.bodies.clear()
    expanded = self.run_window(url, since='2026-09-05T10:00:00Z')
    self.assertEqual(expanded.returncode, 0, expanded.stderr)
    sent = b''.join(server.bodies)
    self.assertIn(b'ADDED_IN_MONTH', sent)
    self.assertNotIn(b'ALREADY_READ_WEEK', sent)
    self.assertNotIn(b'OUTSIDE_MONTH', sent)
    batch = self.status(url)['history_batch']
    self.assertEqual(batch['days'], 30)
    self.assertEqual(batch['total'], 2)
    self.assertEqual(batch['complete'], 2)
    self.assertEqual(batch['status'], 'review')

  def test_live_resume_keeps_the_completed_batch_receipt_and_updates_latest_coverage(self):
    path = self.history_source('resumed.jsonl', 'HISTORY_SNAPSHOT', '2026-10-02T00:00:00Z')
    server, url = self.provider()
    first = self.run_window(url)
    self.assertEqual(first.returncode, 0, first.stderr)
    initial = self.status(url)['history_batch']
    self.assertEqual(initial['status'], 'review')
    certificate = initial.get('completion_sources')
    self.assertTrue(certificate, 'Review needs source hash receipts, not only a zero pending count')
    with path.open('a') as handle:
      handle.write(fixtures.user_line('RESUME_AFTER_WINDOW', session_id='resumed.jsonl', timestamp='2026-10-06T00:00:00Z'))
    server.bodies.clear()
    resumed = self.cli(['run', *self.flags(url, started_at=DEPLOYED)])
    self.assertEqual(resumed.returncode, 0, resumed.stderr)
    self.assertIn(b'RESUME_AFTER_WINDOW', b''.join(server.bodies))
    status = self.status(url)
    batch = status['history_batch']
    self.assertEqual(batch['status'], 'review')
    self.assertEqual(batch['completed_at'], initial['completed_at'])
    self.assertEqual(batch['completion_sources'], certificate)
    latest = self.source_named(status, 'resumed.jsonl')
    self.assertTrue(latest['latest_complete'])
    self.assertNotEqual(latest['source_sha'], certificate[0]['source_sha'])

  def test_failure_blocks_review_and_media_limits_are_not_full_coverage(self):
    self.history_source('failed.jsonl', 'FAILED_MEMBER', '2026-10-02T00:00:00Z')
    image = json.loads(fixtures.user_line('IMAGE_MEMBER', session_id='image-session'))
    image['timestamp'] = '2026-10-02T00:00:00Z'
    image['message']['content'] = [
      {'type': 'text', 'text': 'IMAGE_MEMBER'},
      {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': fixtures.ONE_PIXEL}},
    ]
    path = self.projects / 'work' / 'image.jsonl'
    fixtures.write_jsonl(path, json.dumps(image) + '\n')
    os.utime(path, (1_700_000_000, 1_700_000_000))
    broken = {'value': True}

    def reply(body):
      if 'FAILED_MEMBER' in fixtures.user_text(body) and broken['value']:
        return 'not an analysis', 200
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    first = self.run_window(url)
    self.assertEqual(first.returncode, 0, first.stderr)
    batch = self.status(url)['history_batch']
    self.assertEqual(batch['failed'], 1)
    self.assertEqual(batch['partial'], 1)
    self.assertEqual(batch['remaining'], 1)
    self.assertFalse(batch['ready_for_review'])
    broken['value'] = False
    self.assertEqual(self.run_window(url).returncode, 0)
    batch = self.status(url)['history_batch']
    self.assertTrue(batch['ready_for_review'])
    self.assertFalse(batch['fully_complete'])
    self.assertEqual(batch['status'], 'review-with-limitations')


if __name__ == '__main__':
  suite = unittest.TestSuite(HistoryWindowTests(name) for name in HistoryWindowTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
