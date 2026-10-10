#!/usr/bin/env python3
"""run 公共入口：超大 session 切塊同時送、塊內照舊接續；小 session 不受影響；中斷後只補沒完成的塊。"""

import importlib.util
import json
import threading
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('blocks_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures

LINES = 60
BLOCK = ['--block-bytes', '40000']


class BlockTests(population.PopulationTests):
  def setUp(self):
    super().setUp()
    fixtures.ThreadingHTTPServer.request_queue_size = 64

  def big(self, name='work/giant.jsonl', lines=LINES):
    path = self.projects / name
    filler = 'x' * 2000
    text = ''.join(fixtures.user_line(f'BLOCKLINE_{index:03} {filler}', session_id=path.stem) for index in range(lines))
    fixtures.write_jsonl(path, text)
    return path

  def recorder(self, fail=None, delay=0.3):
    lock = threading.Lock()
    log = {'requests': [], 'active': 0, 'peak': 0}

    def reply(body):
      text = fixtures.user_text(body)
      markers = sorted({int(part[10:13]) for part in text.split() if part.startswith('BLOCKLINE_')})
      continuity = text.split('continuity:\n', 1)[1].split('\n\n<transcript>', 1)[0] if 'continuity:\n' in text else ''
      with lock:
        log['requests'].append((markers, continuity))
        log['active'] += 1
        log['peak'] = max(log['peak'], log['active'])
      try:
        time.sleep(delay)
        if fail is not None and fail(markers):
          return '{"error": "upstream"}', 500
        carry = f'CARRY_{markers[-1]:03}' if markers else ''
        return json.dumps({'choices': [{'message': {'content': json.dumps({'continuity': carry, 'findings': [], 'limitations': [], 'rule_tags': []})}, 'finish_reason': 'stop'}]}), 200
      finally:
        with lock:
          log['active'] -= 1

    return log, reply

  def sent_lines(self, log):
    return [index for markers, _ in log['requests'] for index in markers]

  def test_a_giant_session_runs_its_blocks_side_by_side(self):
    self.big()
    log, reply = self.recorder()
    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertGreater(log['peak'], 1, 'Blocks of one giant session must be in flight together')
    self.assertEqual(self.source_named(self.status(url), 'giant.jsonl')['status'], 'complete')
    lines = self.sent_lines(log)
    self.assertEqual(sorted(set(lines)), list(range(LINES)), 'Every line is analyzed')

  def test_each_block_starts_fresh_and_chains_continuity_inside(self):
    self.big()
    log, reply = self.recorder()
    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60).returncode, 0)
    fresh = [markers for markers, continuity in log['requests'] if not continuity.strip()]
    self.assertGreater(len(fresh), 1, 'Each block after the first starts without the earlier block summary')
    for markers, continuity in log['requests']:
      if 'CARRY_' in continuity:
        carried = int(continuity.split('CARRY_')[1][:3])
        self.assertEqual(carried, markers[0] - 1, 'Inside a block the summary comes from the fragment right before')

  def test_a_session_below_the_block_size_stays_strictly_serial(self):
    self.big(lines=10)
    log, reply = self.recorder()
    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60).returncode, 0)
    self.assertEqual(log['peak'], 1)
    self.assertEqual(sum(1 for _, continuity in log['requests'] if not continuity.strip()), 1)

  def test_a_failed_block_is_resent_without_redoing_finished_blocks(self):
    self.big()
    broken = {'on': True}
    log, reply = self.recorder(fail=lambda markers: broken['on'] and 45 in markers)
    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60).returncode, 0)
    self.assertNotEqual(self.source_named(self.status(url), 'giant.jsonl')['status'], 'complete')
    broken['on'] = False
    log['requests'].clear()
    self.assertEqual(self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60).returncode, 0)
    lines = self.sent_lines(log)
    self.assertIn(45, lines)
    self.assertNotIn(0, lines, 'A finished block is not sent again')
    self.assertEqual(self.source_named(self.status(url), 'giant.jsonl')['status'], 'complete')

  def test_a_session_already_half_done_serially_is_split_from_where_it_stopped(self):
    self.big()
    broken = {'on': True}
    log, reply = self.recorder(fail=lambda markers: broken['on'] and 20 in markers)
    _, url = self.provider(reply)
    no_split = ['--block-bytes', '100000000']
    self.assertEqual(self.cli(['run', *self.flags(url, extra=no_split)], timeout=60).returncode, 0)
    self.assertEqual(log['peak'], 1)
    broken['on'] = False
    log['requests'].clear()
    log['peak'] = 0
    self.assertEqual(self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60).returncode, 0)
    lines = self.sent_lines(log)
    self.assertNotIn(0, lines, 'Work finished before the split is kept')
    self.assertEqual(sorted(set(lines)), list(range(min(lines), LINES)))
    self.assertLessEqual(min(lines), 20)
    self.assertGreater(log['peak'], 1)
    self.assertEqual(self.source_named(self.status(url), 'giant.jsonl')['status'], 'complete')

  def test_a_copied_giant_session_waits_until_the_first_copy_finishes(self):
    # 2026-10-10 正式環境：workflow 的 .GOLDEN 備份與原檔是同一份對話，兩份同時切塊、塊名撞在一起，收尾時整輪當掉（KeyError），連 21 輪。
    original = self.big()
    copy = self.projects / 'work' / 'backup' / original.name
    copy.parent.mkdir()
    fixtures.write_jsonl(copy, original.read_text(), bind_session=False)
    log, reply = self.recorder()
    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url, extra=BLOCK)], timeout=60)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(sorted(self.sent_lines(log)), sorted(list(range(LINES)) * 2), 'Both copies are analyzed in full')
    rows = [row for row in self.status(url)['sources'] if row['name'] == original.name]
    self.assertEqual([row['status'] for row in rows], ['complete', 'complete'])


if __name__ == '__main__':
  suite = unittest.TestSuite(BlockTests(name) for name in BlockTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
