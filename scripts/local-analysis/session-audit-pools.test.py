#!/usr/bin/env python3
"""run 公共入口：號池健康度決定何時暫停與恢復；健康度讀不到時才退回連續失敗門檻。"""

import importlib.util
import json
import sqlite3
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

spec = importlib.util.spec_from_file_location('pools_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures

MIMO_MODEL = 'cline-free/mimo-v2.6-flash'


class Health(BaseHTTPRequestHandler):
  def do_GET(self):
    usable, total = self.server.pools[self.path.strip('/')]
    if self.path.strip('/') == 'cline2api':
      accounts = [{'status': 'active', 'modelCooldowns': {} if index < usable else {MIMO_MODEL: '2099-01-01T00:00:00Z'}} for index in range(total)]
      body = {'success': True, 'data': {'accounts': accounts}}
    else:
      body = {'object': 'list', 'data': [{'provider': 'workbuddy', 'enabled': True, 'ready': index < usable} for index in range(total)]}
    raw = json.dumps(body).encode()
    self.send_response(200)
    self.send_header('Content-Type', 'application/json')
    self.send_header('Content-Length', str(len(raw)))
    self.end_headers()
    self.wfile.write(raw)

  def log_message(self, *_args):
    pass


class PoolHealthTests(population.PopulationTests):
  def setUp(self):
    super().setUp()
    fixtures.ThreadingHTTPServer.request_queue_size = 64
    self.health = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    self.health.pools = {'cline2api': (19, 19), 'cli2api': (7, 7)}
    threading.Thread(target=self.health.serve_forever, daemon=True).start()
    self.addCleanup(self.health.server_close)
    self.addCleanup(self.health.shutdown)

  def sessions(self, count, prefix='POOL_WORK'):
    for index in range(count):
      self.source(f'work/{prefix.lower()}-{index:02}.jsonl', f'{prefix}_{index:02}')

  def model_of(self, body):
    return json.loads(body)['model']

  def run_flags(self, url, health=True, probe=None):
    base = f'http://127.0.0.1:{self.health.server_address[1]}'
    pools = {
      'mimo26-pool': {'kind': 'cline2api', 'url': f'{base}/cline2api', 'model': MIMO_MODEL},
      'workbuddy-v41': {'kind': 'cli2api', 'url': f'{base}/cli2api' if health else 'http://127.0.0.1:9/none', 'provider': 'workbuddy'},
    }
    extra = ['--direct-legs', 'mimo26-pool:15,workbuddy-v41:15', '--pool-health', json.dumps(pools)]
    if probe is not None:
      extra += ['--leg-probe-seconds', str(probe)]
    return self.flags(url) + extra

  def wb_failing_provider(self, seen):
    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'workbuddy-v41':
        return '{"error": "upstream"}', 500
      return fixtures.analysis([], rule_tags=[]), 200
    return self.provider(reply)

  def state_rows(self, sql):
    with sqlite3.connect(self.state / 'state.sqlite') as connection:
      return connection.execute(sql).fetchall()

  def test_leg_with_live_accounts_is_not_paused_by_a_run_of_errors(self):
    self.sessions(12)
    seen = []
    _, url = self.wb_failing_provider(seen)
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertGreaterEqual(seen.count('workbuddy-v41'), 4)
    self.sessions(3, 'LATER')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertIn('workbuddy-v41', seen, 'Errors while the pool still has live accounts are not a reason to pause')

  def test_failure_on_a_dead_pool_pauses_at_once_and_health_is_recorded(self):
    self.health.pools['cli2api'] = (0, 7)
    self.sessions(6)
    seen = []
    _, url = self.wb_failing_provider(seen)
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertLessEqual(seen.count('workbuddy-v41'), 6, 'Only the requests already in flight when the pool proved dead')
    self.assertEqual([alias for (alias,) in self.state_rows('SELECT alias FROM leg_pauses')], ['workbuddy-v41'])
    health = dict((alias, (usable, total)) for alias, usable, total in self.state_rows('SELECT alias, usable, total FROM leg_health'))
    self.assertEqual(health, {'mimo26-pool': (19, 19), 'workbuddy-v41': (0, 7)})

  def test_error_confirmed_by_a_dead_pool_pauses_at_once(self):
    self.sessions(6)
    seen = []

    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'workbuddy-v41':
        self.health.pools['cli2api'] = (0, 7)
        return '{"error": "quota"}', 429
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertLess(seen.count('workbuddy-v41'), 30)
    self.sessions(3, 'LATER')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertNotIn('workbuddy-v41', seen, 'A failure the pool confirms as dead pauses the leg without waiting for 30')

  def test_thirty_straight_errors_pause_when_health_is_unreadable(self):
    # 要夠多的工作才能讓 WorkBuddy 連錯 30 次：MiMo 那半邊會很快把其餘片段做完。
    self.sessions(80)
    seen = []
    _, url = self.wb_failing_provider(seen)
    self.assertEqual(self.cli(['run', *self.run_flags(url, health=False)], timeout=60).returncode, 0)
    self.assertGreaterEqual(seen.count('workbuddy-v41'), 30)
    self.sessions(3, 'LATER')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url, health=False)], timeout=30).returncode, 0)
    self.assertNotIn('workbuddy-v41', seen)

  def test_paused_leg_resumes_once_the_pool_has_live_accounts(self):
    self.health.pools['cli2api'] = (0, 7)
    self.sessions(6)
    seen = []
    down = {'value': True}

    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'workbuddy-v41' and down['value']:
        return '{"error": "quota"}', 429
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertEqual([alias for (alias,) in self.state_rows('SELECT alias FROM leg_pauses')], ['workbuddy-v41'])
    self.health.pools['cli2api'] = (3, 7)
    down['value'] = False
    self.sessions(6, 'REVIVED')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url)], timeout=30).returncode, 0)
    self.assertIn('workbuddy-v41', seen)
    self.assertEqual(self.state_rows('SELECT alias FROM leg_pauses WHERE alias = "workbuddy-v41"'), [])

  def test_paused_leg_sends_one_probe_when_due_and_resumes_if_it_works(self):
    self.health.pools['cli2api'] = (0, 7)
    self.sessions(6)
    seen = []
    works = {'value': False}

    def reply(body):
      seen.append(self.model_of(body))
      if self.model_of(body) == 'workbuddy-v41' and not works['value']:
        return '{"error": "upstream"}', 500
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    self.assertEqual(self.cli(['run', *self.run_flags(url, probe=0)], timeout=30).returncode, 0)
    self.sessions(6, 'PROBE_FAIL')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url, probe=0)], timeout=30).returncode, 0)
    self.assertEqual(seen.count('workbuddy-v41'), 1, 'A dead pool gets one probe per interval, not a full share')
    works['value'] = True
    self.sessions(6, 'PROBE_OK')
    self.assertEqual(self.cli(['run', *self.run_flags(url, probe=0)], timeout=30).returncode, 0)
    self.assertEqual(self.state_rows('SELECT alias FROM leg_pauses'), [], 'A successful probe ends the pause')
    self.sessions(6, 'AFTER_PROBE')
    seen.clear()
    self.assertEqual(self.cli(['run', *self.run_flags(url, probe=0)], timeout=30).returncode, 0)
    self.assertGreater(seen.count('workbuddy-v41'), 1, 'After the probe the leg is back to its full share')


if __name__ == '__main__':
  suite = unittest.TestSuite(PoolHealthTests(name) for name in PoolHealthTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
