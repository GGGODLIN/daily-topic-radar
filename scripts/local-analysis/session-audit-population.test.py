#!/usr/bin/env python3
"""scan/run/status：人工 main 準入與子會話身分核對。"""

import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('population_fixtures', Path(__file__).with_name('session-audit-history.test.py'))
history = importlib.util.module_from_spec(spec)
spec.loader.exec_module(history)
fixtures = history.fixtures


class PopulationTests(history.HistoryWindowTests):
  def source(self, relative, text, **extra):
    path = self.projects / relative
    record = json.loads(fixtures.user_line(text, session_id=path.stem, promptSource='typed', origin={'kind': 'human'}))
    record.update(extra)
    fixtures.write_jsonl(path, json.dumps(record) + '\n', bind_session=False)
    return path

  def child(self, parent, agent, text, **extra):
    path = parent.with_suffix('') / 'subagents' / f'agent-{agent}.jsonl'
    fields = {'sessionId': parent.stem, 'agentId': agent, 'isSidechain': True, **extra}
    return self.source(path.relative_to(self.projects), text, **fields)

  def test_only_human_main_and_identity_matched_children_reach_provider(self):
    parent = self.source('work/human.jsonl', 'HUMAN_WORK')
    self.child(parent, 'one', 'CHILD_WORK')
    self.source('work/sdk.jsonl', 'SDK_BACKGROUND', promptSource='sdk')
    self.source('work/unlabelled.jsonl', 'UNLABELLED', promptSource=None)
    self.source('work/forwarded.jsonl', '[main] FORWARDED_WORK')
    self.source('work/meta.jsonl', 'META_INPUT', isMeta=True)
    self.source('work/wrapper.jsonl', '<task-notification>WRAPPER_INPUT</task-notification>')
    self.source('-Users-demo--config-backpass-user/backpass.jsonl', 'BACKPASS_INPUT', promptSource='sdk')
    self.source('-private-tmp-probe/probe.jsonl', 'TYPED_TEST_INPUT')
    journal = self.projects / 'work' / 'human' / 'subagents' / 'workflows' / 'wf-x' / 'journal.jsonl'
    fixtures.write_jsonl(journal, json.dumps({'type': 'launched', 'timestamp': '2026-10-02T00:00:00Z'}) + '\n')
    _, url = self.provider()
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    report = self.status(url)
    included = {row['name'] for row in report['sources'] if row['included']}
    self.assertEqual(included, {'human.jsonl', 'agent-one.jsonl'})
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    sent = b''.join(self.provider_bodies(url))
    self.assertIn(b'HUMAN_WORK', sent)
    self.assertIn(b'CHILD_WORK', sent)
    for marker in ['SDK_BACKGROUND', 'UNLABELLED', 'FORWARDED_WORK', 'META_INPUT', 'WRAPPER_INPUT', 'BACKPASS_INPUT', 'TYPED_TEST_INPUT']:
      self.assertNotIn(marker.encode(), sent)

  def provider(self, reply=None):
    server, url = super().provider(reply)
    self.servers = getattr(self, 'servers', {})
    self.servers[url] = server
    return server, url

  def provider_bodies(self, url):
    return self.servers[url].bodies

  def test_orphan_wrong_session_or_wrong_agent_are_not_included(self):
    parent = self.source('work/human.jsonl', 'HUMAN_WORK')
    self.child(parent, 'wrong-session', 'WRONG_SESSION', sessionId='different-main')
    self.child(parent, 'wrong-agent', 'WRONG_AGENT', agentId='different-agent')
    self.child(parent, 'not-sidechain', 'NOT_SIDECHAIN', isSidechain=False)
    self.source('work/missing/subagents/agent-orphan.jsonl', 'ORPHAN', sessionId='missing', agentId='orphan', isSidechain=True)
    _, url = self.provider()
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    included = {row['name'] for row in self.status(url)['sources'] if row['included']}
    self.assertEqual(included, {'human.jsonl'})

  def test_child_in_window_uses_parent_metadata_outside_window_without_sending_parent(self):
    parent = self.source('work/old-human.jsonl', 'OUTSIDE_WINDOW_PARENT', timestamp='2026-08-01T00:00:00Z')
    child = self.child(parent, 'recent', 'RECENT_CHILD', timestamp='2026-10-02T00:00:00Z')
    fixtures.os.utime(parent, (1_700_000_000, 1_700_000_000))
    fixtures.os.utime(child, (1_700_000_000, 1_700_000_000))
    server, url = self.provider()
    self.assertEqual(self.run_window(url).returncode, 0)
    sent = b''.join(server.bodies)
    self.assertIn(b'RECENT_CHILD', sent)
    self.assertNotIn(b'OUTSIDE_WINDOW_PARENT', sent)
    self.assertTrue(self.source_named(self.status(url), 'agent-recent.jsonl')['included'])

  def test_resume_with_human_participation_admits_existing_parent_and_child(self):
    parent = self.source('work/resumed.jsonl', 'SDK_START', promptSource='sdk')
    self.child(parent, 'one', 'CHILD_WORK')
    server, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertEqual(server.bodies, [])
    with parent.open('a') as handle:
      handle.write(fixtures.user_line('HUMAN_RESUME', session_id=parent.stem, promptSource='typed', origin={'kind': 'human'}))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    sent = b''.join(server.bodies)
    self.assertIn(b'HUMAN_RESUME', sent)
    self.assertIn(b'CHILD_WORK', sent)

  def test_reclassification_stops_requests_and_preserves_stored_evidence(self):
    parent = self.source('work/reclassified.jsonl', 'STORED_FINDING')
    server, url = self.provider(lambda body: (fixtures.analysis([fixtures.finding('STORED_FINDING')], rule_tags=[]), 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    before = self.status(url)
    self.assertEqual(len(before['candidates']), 1)
    self.assertTrue(before['segments'])
    record = json.loads(parent.read_text())
    record['promptSource'] = 'sdk'
    parent.write_text(json.dumps(record) + '\n')
    server.bodies.clear()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    after = self.status(url)
    self.assertEqual(server.bodies, [])
    self.assertFalse(self.source_named(after, parent.name)['included'])
    self.assertEqual(after['candidates'], [])
    self.assertEqual(after['segments'], before['segments'])
    self.assertTrue(parent.exists())

  def test_canonical_parent_id_is_not_confused_with_resume_runtime_id(self):
    parent = self.source('work/human.jsonl', 'CANONICAL_PARENT', session_id='resumed-runtime')
    self.child(parent, 'one', 'CANONICAL_CHILD', session_id='child-runtime')
    server, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    report = self.status(url)
    self.assertTrue(self.source_named(report, 'agent-one.jsonl')['included'])
    self.assertIn(b'CANONICAL_PARENT', b''.join(server.bodies))
    self.assertIn(b'CANONICAL_CHILD', b''.join(server.bodies))


if __name__ == '__main__':
  suite = unittest.TestSuite(PopulationTests(name) for name in PopulationTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
