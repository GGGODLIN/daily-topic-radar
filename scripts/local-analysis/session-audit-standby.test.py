#!/usr/bin/env python3
"""run 公共入口：增量平常只用主線；主線冷卻中或積壓太久才開備用線，任何時候都不超過各線名額。"""

import importlib.util
import json
import os
import sqlite3
import threading
import time
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('standby_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures

PRIMARY = 'argon-low:2'
STANDBY = 'audit-tail:10'
HOUR = 3600


def served(model, content):
  return json.dumps({"model": f"served-{model}", "choices": [{"finish_reason": "stop", "message": {"content": content}}]})


class StandbyTests(population.PopulationTests):
  def sessions(self, count, age=0, prefix='s'):
    for index in range(count):
      path = self.source(f'work/{prefix}-{index:02}.jsonl', f'STANDBY_WORK_{prefix}_{index:02}')
      if age:
        moment = time.time() - age
        os.utime(path, (moment, moment))

  def tracking(self, fail=None):
    lock = threading.Lock()
    seen, active, peak = [], {}, {}

    def reply(body):
      model = json.loads(body)['model']
      text = fixtures.user_text(body)
      with lock:
        seen.append((model, text))
        count = len(seen)
        active[model] = active.get(model, 0) + 1
        peak[model] = max(peak.get(model, 0), active[model])
      try:
        time.sleep(0.05)
        outcome = fail(model, count, text) if fail else None
        return outcome or (served(model, json.loads(fixtures.analysis([], rule_tags=[]))['choices'][0]['message']['content']), 200)
      finally:
        with lock:
          active[model] -= 1

    _, url = self.provider(reply)
    return url, seen, peak

  def run_legs(self, url):
    flags = [*self.flags(url), '--direct-legs', PRIMARY, '--standby-legs', STANDBY, '--max-sessions', '2']
    done = self.cli(['run', *flags], timeout=60)
    self.assertEqual(done.returncode, 0, done.stderr)

  def db(self, sql, params=()):
    connection = sqlite3.connect(self.state / 'state.sqlite')
    try:
      rows = connection.execute(sql, params).fetchall()
      connection.commit()
      return rows
    finally:
      connection.close()

  def statuses(self, url):
    return {row['status'] for row in self.status(url)['sources'] if row['included']}

  def test_healthy_primary_runs_alone_within_its_slots(self):
    self.sessions(12)
    url, seen, peak = self.tracking()
    self.run_legs(url)
    self.assertEqual({model for model, _ in seen}, {'argon-low'}, 'The standby stays idle while the primary is healthy and nothing waits long')
    self.assertLessEqual(peak['argon-low'], 2)
    self.assertEqual(self.statuses(url), {'complete'})
    self.assertEqual({row[0] for row in self.db('SELECT model FROM model_replies')}, {'served-argon-low'})

  def test_rate_limited_fragment_moves_to_standby_and_primary_cools_down(self):
    self.sessions(6)
    failed = []

    def fail(model, count, text):
      if model == 'argon-low' and not failed:
        failed.append(next(word for word in text.split() if word.startswith('STANDBY_WORK_')))
        return '{"error": "rate limited"}', 429
      return None

    url, seen, peak = self.tracking(fail)
    self.run_legs(url)
    resent = [model for model, text in seen if failed[0] in text]
    self.assertEqual(resent, ['argon-low', 'audit-tail'], 'The rate-limited fragment goes to the standby, never back to the primary')
    self.assertLessEqual([model for model, _ in seen].count('argon-low'), 2, 'One 429 cools the primary down at once')
    self.assertEqual(self.statuses(url), {'complete'})
    self.assertEqual(self.db("SELECT alias FROM leg_pauses"), [('argon-low',)])
    self.sessions(3, prefix='later')
    seen.clear()
    self.run_legs(url)
    self.assertEqual({model for model, _ in seen}, {'audit-tail'}, 'The cooldown outlives the run, so the next run keeps using the standby')

  def test_backlog_older_than_the_on_line_opens_standby(self):
    self.sessions(12, age=20 * HOUR)
    url, seen, peak = self.tracking()
    self.run_legs(url)
    self.assertEqual({model for model, _ in seen}, {'argon-low', 'audit-tail'})
    self.assertLessEqual(peak['argon-low'], 2)
    self.assertEqual(self.statuses(url), {'complete'})

  def test_backlog_switch_holds_between_the_lines_and_closes_below_the_off_line(self):
    _, url = self.provider()
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    self.db("INSERT INTO standby_switches (name, since) VALUES ('backlog', ?)", (time.time() - HOUR,))
    self.sessions(12, age=5 * HOUR)
    url, seen, _ = self.tracking()
    self.run_legs(url)
    self.assertIn('audit-tail', {model for model, _ in seen}, 'Five hours is above the off line, so an open switch stays open across runs')
    self.sessions(6, prefix='fresh')
    seen.clear()
    self.run_legs(url)
    self.assertEqual({model for model, _ in seen}, {'argon-low'}, 'Once nothing waits past the off line the run is back to the primary alone')
    self.assertEqual(self.db("SELECT name FROM standby_switches"), [])

  def test_standby_failures_never_push_the_primary_past_its_slots(self):
    self.sessions(12, age=20 * HOUR)
    url, seen, peak = self.tracking(lambda model, count, text: ('{"error": "down"}', 500) if model == 'audit-tail' else None)
    self.run_legs(url)
    self.assertLessEqual(peak['argon-low'], 2, 'Fragments bounced off the standby wait for a primary slot instead of overfilling it')
    self.assertEqual(self.statuses(url), {'complete'})

  def test_probe_needs_a_readable_analysis_before_the_primary_resumes(self):
    _, url = self.provider()
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    self.db("INSERT INTO leg_pauses (alias, until) VALUES ('argon-low', ?)", (time.time() - 1,))
    self.sessions(4)
    url, seen, _ = self.tracking(lambda model, count, text: (served(model, 'not an analysis'), 200) if model == 'argon-low' else None)
    self.run_legs(url)
    self.assertIn('argon-low', [model for model, _ in seen], 'A due probe is sent')
    self.assertEqual(self.db("SELECT alias FROM leg_pauses"), [('argon-low',)], 'A reply that is not an analysis does not end the cooldown')


if __name__ == '__main__':
  suite = unittest.TestSuite(StandbyTests(name) for name in StandbyTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
