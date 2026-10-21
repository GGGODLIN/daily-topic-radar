#!/usr/bin/env python3
"""依 session 工作目錄改送模型：calyx 公司 session 的 audit-tail 改走帶公司帳號的群組，其他 session 照原腿。"""

import importlib.util
import json
import threading
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('workdir_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures

CALYX = '/Users/u/Desktop/work/calyx'
ROUTES = f'audit-tail:{CALYX}:audit-tail-calyx'


def load_audit():
  spec = importlib.util.spec_from_file_location('session_audit_mod', Path(__file__).with_name('session-audit.py'))
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


class WorkdirHelperTests(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    cls.audit = load_audit()

  def test_only_root_and_its_children_are_routed(self):
    routes = self.audit.parse_workdir_routes(ROUTES)
    self.assertEqual(self.audit.routed_model('audit-tail', CALYX, routes), 'audit-tail-calyx')
    self.assertEqual(self.audit.routed_model('audit-tail', CALYX + '/saas', routes), 'audit-tail-calyx')
    self.assertEqual(self.audit.routed_model('audit-tail', CALYX + '-old', routes), 'audit-tail')
    self.assertEqual(self.audit.routed_model('audit-tail', None, routes), 'audit-tail')
    self.assertEqual(self.audit.routed_model('argon-low', CALYX, routes), 'argon-low')

  def test_bad_route_specs_fail(self):
    for bad in ['audit-tail:relative:x', 'audit-tail:/:x', 'audit-tail:/a/:x', ':/a:x', 'audit-tail:/a:']:
      with self.assertRaises(SystemExit, msg=bad):
        self.audit.parse_workdir_routes(bad)


class WorkdirRunTests(population.PopulationTests):
  def test_calyx_session_goes_to_the_calyx_model_and_others_keep_the_leg(self):
    self.source('work/calyx.jsonl', 'CALYX_WORK', cwd=CALYX + '/calyx-dashboard-v2')
    self.source('work/personal.jsonl', 'PERSONAL_WORK', cwd='/Users/u/Desktop/projects')
    seen = {}
    lock = threading.Lock()

    def reply(body):
      payload = json.loads(body)
      text = json.dumps(payload['messages'])
      with lock:
        seen['CALYX_WORK' if 'CALYX_WORK' in text else 'PERSONAL_WORK' if 'PERSONAL_WORK' in text else 'other'] = payload['model']
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url), '--direct-legs', 'audit-tail:2', '--max-sessions', '2', '--workdir-routes', ROUTES], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen, {'CALYX_WORK': 'audit-tail-calyx', 'PERSONAL_WORK': 'audit-tail'})


if __name__ == '__main__':
  # 只跑本檔的類別：匯入的 population 模組也帶測試類別，unittest.main() 會把它們一起跑。
  loader = unittest.TestLoader()
  suite = unittest.TestSuite([loader.loadTestsFromTestCase(WorkdirHelperTests)] + [WorkdirRunTests(name) for name in WorkdirRunTests.__dict__ if name.startswith('test_')])
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
