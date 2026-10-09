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
PRODUCTION_LEGS = 'mimo26-pool:15,workbuddy-v41:15,grok-4.7:10'


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
    self.assertEqual(self.peaks(LEGS, self.grok_usage(40), full=30), {'free': 16, 'workbuddy-v41': 7, 'grok-4.7': 7})

  def peaks(self, legs, quota, full, sessions=48, extra=()):
    fixtures.ThreadingHTTPServer.request_queue_size = 64
    self.sessions(sessions)
    lock = threading.Lock()
    active, peak = {}, {}
    arrived = threading.Event()

    def reply(body):
      model = self.model_of(body)
      with lock:
        active[model] = active.get(model, 0) + 1
        peak[model] = max(peak.get(model, 0), active[model])
        if sum(active.values()) == full:
          arrived.set()
      try:
        arrived.wait(5)
        return fixtures.analysis([], rule_tags=[]), 200
      finally:
        with lock:
          active[model] -= 1

    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url), '--direct-legs', legs, '--grok-quota-file', str(quota), *extra], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    return peak

  def test_free_only_runs_stay_under_the_session_cap(self):
    # 20 個 session 全部到齊才放行：沒有上限時峰值會到 20，有上限時卡在 10 等逾時。
    peak = self.peaks('', self.grok_usage(10), full=20, sessions=20, extra=['--max-sessions', '10'])
    self.assertEqual(peak, {'free': 10}, 'Incremental runs on the free chain keep to the configured cap')

  def test_production_legs_add_ten_grok_slots_on_top_of_the_two_pools(self):
    peak = self.peaks(PRODUCTION_LEGS, self.grok_usage(10), full=40)
    self.assertEqual(peak, {'mimo26-pool': 15, 'workbuddy-v41': 15, 'grok-4.7': 10})

  def test_grok_over_its_line_leaves_its_slots_empty_instead_of_overloading_the_pools(self):
    peak = self.peaks(PRODUCTION_LEGS, self.grok_usage(50), full=30)
    self.assertEqual(peak, {'mimo26-pool': 15, 'workbuddy-v41': 15}, 'Each pool keeps the 15 slots the user set even while Grok sits out')

  def pinned_flags(self, url):
    return self.flags(url) + ['--direct-legs', 'mimo26-pool:15,workbuddy-v41:15']

  def test_pinned_legs_never_touch_the_free_chain(self):
    self.sessions(40)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.pinned_flags(url)], timeout=30).returncode, 0)
    self.assertNotIn('free', seen, 'With every slot pinned the run must not fall through to unvetted free-chain legs')
    self.assertEqual(set(seen), {'mimo26-pool', 'workbuddy-v41'})

  def test_isolated_failure_moves_the_fragment_to_the_other_leg(self):
    self.sessions(4)
    seen = []
    lock = threading.Lock()

    def reply(body):
      model = self.model_of(body)
      with lock:
        seen.append((model, fixtures.user_text(body)))
        first_mimo = model == 'mimo26-pool' and [m for m, _ in seen].count('mimo26-pool') == 1
      if first_mimo:
        return '{"error": "upstream"}', 500
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.pinned_flags(url)]).returncode, 0)
    failed_text = next(text for model, text in seen if model == 'mimo26-pool')
    marker = next(word for word in failed_text.split() if word.startswith('LEG_WORK_'))
    self.assertTrue(any(model == 'workbuddy-v41' and marker in text for model, text in seen), 'The failed fragment is resent on the other leg')
    rows = [row for row in self.status(url)['sources'] if row['included']]
    self.assertTrue(all(row['status'] == 'complete' for row in rows), rows)
    self.source('work/later.jsonl', 'AFTER_BLIP')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.pinned_flags(url)]).returncode, 0)
    self.assertIn('mimo26-pool', [model for model, _ in seen], 'One error does not pause a leg')

  def test_both_legs_down_leaves_work_pending_not_failed(self):
    # 健康度讀不到時兩條腿都要各自連錯 30 次才停，所以要夠多工作。
    self.sessions(40)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      return '{"error": "upstream"}', 500

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.pinned_flags(url)]).returncode, 0)
    self.assertNotIn('free', seen)
    rows = [row for row in self.status(url)['sources'] if row['included']]
    self.assertTrue(all(row['status'] == 'pending' for row in rows), 'Dead pools are not a fault of the sessions: ' + str([row['status'] for row in rows]))
    seen.clear()
    self.assertEqual(self.cli(['run', *self.pinned_flags(url)]).returncode, 0)
    self.assertEqual(seen, [], 'Both legs stay paused, so the next run waits instead of sending')

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
    # Grok 沒有號池健康度可讀，只靠連續失敗門檻（30 次）；它只分到 7 槽，要夠多工作才錯得到 30 次。
    self.sessions(160)
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
    self.assertGreaterEqual(seen.count('grok-4.7'), 30)
    for index in range(3):
      self.source(f'work/later-{index}.jsonl', f'AFTER_OUTAGE_{index}')
    seen.clear()
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertNotIn('grok-4.7', seen, 'A Grok that keeps failing is paused instead of eating every request')

  def test_grok_is_skipped_at_fifty_percent_or_when_usage_is_unknown(self):
    self.sessions(3)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.leg_flags(url, self.grok_usage(49))]).returncode, 0)
    self.assertIn('grok-4.7', seen, 'Below 50% Grok still takes its slots')
    seen.clear()
    for index in range(3):
      self.source(f'work/full-{index}.jsonl', f'NEAR_LIMIT_{index}')
    self.assertEqual(self.cli(['run', *self.leg_flags(url, self.grok_usage(50))]).returncode, 0)
    self.source('work/stale.jsonl', 'STALE_SAMPLE')
    stale = self.grok_usage(10, at=datetime.fromtimestamp(time.time() - 3600, UTC))
    self.assertEqual(self.cli(['run', *self.leg_flags(url, stale)]).returncode, 0)
    self.assertNotIn('grok-4.7', seen)
    self.assertIn('free', seen)


if __name__ == '__main__':
  suite = unittest.TestSuite(DirectLegTests(name) for name in DirectLegTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
