#!/usr/bin/env python3
"""run 公共入口：free 之外的直連腿分攤名額，用完或出錯時退回 free，不卡住、不把來源標失敗。"""

import importlib.util
import json
import threading
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

spec = importlib.util.spec_from_file_location('legs_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures

LEGS = 'workbuddy-v41:7,grok-4.7:7'


class DirectLegTests(population.PopulationTests):
  def grok_usage(self, pct, at=None):
    path = self.root / 'grok-quota-samples.jsonl'
    sample = {'at': (at or datetime.now(UTC)).isoformat(), 'pct': pct, 'period_start': '2026-10-07T09:15:20+00:00', 'tokens': 1, 'usd': 1, 'requests': 1}
    path.write_text(json.dumps(sample) + '\n')
    return path

  def leg_flags(self, url, quota):
    return self.flags(url) + ['--direct-legs', LEGS, '--grok-quota-file', str(quota)]

  def sessions(self, count):
    for index in range(count):
      self.source(f'work/s-{index:02}.jsonl', f'LEG_WORK_{index:02}')

  def model_of(self, body):
    return json.loads(body)['model']

  def test_each_leg_carries_its_share_of_slots(self):
    fixtures.ThreadingHTTPServer.request_queue_size = 64
    self.sessions(32)
    lock = threading.Lock()
    active, peak = {}, {}
    arrived = threading.Event()

    def reply(body):
      model = self.model_of(body)
      with lock:
        active[model] = active.get(model, 0) + 1
        peak[model] = max(peak.get(model, 0), active[model])
        if sum(active.values()) == 30:
          arrived.set()
      try:
        arrived.wait(5)
        return fixtures.analysis([], rule_tags=[]), 200
      finally:
        with lock:
          active[model] -= 1

    _, url = self.provider(reply)
    done = self.cli(['run', *self.leg_flags(url, self.grok_usage(40))], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(peak, {'free': 16, 'workbuddy-v41': 7, 'grok-4.7': 7})

  def test_failed_direct_leg_falls_back_to_free_and_pauses(self):
    self.sessions(3)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'workbuddy-v41':
        return '{"error": "insufficient credits"}', 402
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    flags = self.leg_flags(url, self.grok_usage(40))
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    rows = [row for row in self.status(url)['sources'] if row['included']]
    self.assertTrue(all(row['status'] == 'complete' for row in rows), rows)
    self.assertIn('workbuddy-v41', seen)
    for index in range(3):
      self.source(f'work/later-{index}.jsonl', f'AFTER_PAUSE_{index}')
    seen.clear()
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertNotIn('workbuddy-v41', seen, 'Failed legs stay paused instead of failing every request again')

  def test_grok_keeps_its_slots_after_an_isolated_error(self):
    self.sessions(3)
    seen = []
    lock = threading.Lock()

    def reply(body):
      model = self.model_of(body)
      with lock:
        seen.append(model)
        first_grok = model == 'grok-4.7' and seen.count('grok-4.7') == 1
      if first_grok:
        return '{"error": "upstream"}', 500
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    flags = self.leg_flags(url, self.grok_usage(40))
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    rows = [row for row in self.status(url)['sources'] if row['included']]
    self.assertTrue(all(row['status'] == 'complete' for row in rows), rows)
    for index in range(3):
      self.source(f'work/later-{index}.jsonl', f'AFTER_BLIP_{index}')
    seen.clear()
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertIn('grok-4.7', seen, 'Below the quota line one Grok error must not cost it 30 minutes of slots')

  def test_grok_pauses_after_repeated_errors(self):
    self.sessions(12)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'grok-4.7':
        return '{"error": "upstream"}', 500
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    flags = self.leg_flags(url, self.grok_usage(40))
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    rows = [row for row in self.status(url)['sources'] if row['included']]
    self.assertTrue(all(row['status'] == 'complete' for row in rows), rows)
    self.assertGreaterEqual(seen.count('grok-4.7'), 3)
    for index in range(3):
      self.source(f'work/later-{index}.jsonl', f'AFTER_OUTAGE_{index}')
    seen.clear()
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertNotIn('grok-4.7', seen, 'A Grok that keeps failing is paused instead of eating every request')

  def test_grok_is_skipped_at_ninety_five_percent_or_when_usage_is_unknown(self):
    self.sessions(3)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.leg_flags(url, self.grok_usage(94))]).returncode, 0)
    self.assertIn('grok-4.7', seen, 'Below 95% Grok still takes its slots')
    seen.clear()
    for index in range(3):
      self.source(f'work/full-{index}.jsonl', f'NEAR_LIMIT_{index}')
    self.assertEqual(self.cli(['run', *self.leg_flags(url, self.grok_usage(95))]).returncode, 0)
    self.source('work/stale.jsonl', 'STALE_SAMPLE')
    stale = self.grok_usage(10, at=datetime.fromtimestamp(time.time() - 3600, UTC))
    self.assertEqual(self.cli(['run', *self.leg_flags(url, stale)]).returncode, 0)
    self.assertNotIn('grok-4.7', seen)
    self.assertIn('free', seen)


if __name__ == '__main__':
  suite = unittest.TestSuite(DirectLegTests(name) for name in DirectLegTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
