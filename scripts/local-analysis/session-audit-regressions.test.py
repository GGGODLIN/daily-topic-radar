import importlib.util
import json
import os
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('audit_cli_fixtures', Path(__file__).with_name('session-audit.test.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ContractRepairTests(fixtures.SessionAuditCliTest):
  def provider(self, reply):
    server = fixtures.serve(reply)
    self.addCleanup(fixtures.stop, server)
    return server, f'http://127.0.0.1:{server.server_address[1]}'

  def promote_file(self):
    path = self.friction / 'workflow-general.md'
    path.write_text('# Friction\n\n## 待折\n\n## 休眠\n\n## 已折／已否決\n')
    return path

  def test_resolution_can_cite_a_new_quote_and_link_to_the_earlier_incident(self):
    path = self.projects / 'work' / 's1.jsonl'
    fixtures.write_jsonl(path, fixtures.user_line('EARLY_INCIDENT'))
    response = {'value': fixtures.analysis([fixtures.finding('EARLY_INCIDENT', observation='broken-widget')])}
    _, url = self.provider(lambda body: (response['value'], 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    candidate = self.status(url)['candidates'][0]
    self.assertTrue(candidate.get('issue_ref'), 'cross-segment incidents need a stable reference')
    fixed = fixtures.finding('LATER_SUCCESS', status='resolved', observation='widget-is-now-fixed', source_line=2)
    fixed['issue_ref'] = candidate['issue_ref']
    response['value'] = fixtures.analysis([fixed])
    with path.open('a') as handle:
      handle.write(fixtures.user_line('LATER_SUCCESS'))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertEqual(self.status(url)['candidates'], [])
    self.promote_file()
    promoted = self.cli(['promote', *self.flags(url)])
    self.assertEqual(json.loads(promoted.stdout)['appended'], 0)

  def test_legacy_line_reference_does_not_swallow_two_distinct_same_line_issues(self):
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line('ISSUE_APPLE and ISSUE_ORANGE'))
    _, url = self.provider(lambda body: (fixtures.analysis([
      fixtures.finding('ISSUE_APPLE', observation='apple-problem'),
      fixtures.finding('ISSUE_ORANGE', observation='orange-problem'),
    ]), 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    target = self.promote_file()
    target.write_text(target.read_text().replace('## 待折\n', '## 待折\n- old [source_ref=session:s1#1; signal_type=agent-observation] legacy\n'))
    first = self.cli(['promote', *self.flags(url)])
    self.assertEqual(first.returncode, 0, first.stderr)
    self.assertEqual(json.loads(first.stdout)['appended'], 2)
    self.assertIn('apple-problem', target.read_text())
    self.assertIn('orange-problem', target.read_text())
    second = self.cli(['promote', *self.flags(url)])
    self.assertEqual(json.loads(second.stdout)['appended'], 0)

  def test_line_ten_reference_does_not_match_line_one(self):
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line('LINE_ONE_ISSUE'))
    _, url = self.provider(lambda body: (fixtures.analysis([fixtures.finding('LINE_ONE_ISSUE')]), 200))
    self.cli(['run', *self.flags(url)])
    target = self.promote_file()
    target.write_text(target.read_text().replace('## 待折\n', '## 待折\n- old [source_ref=session:s1#10; signal_type=agent-observation] another-line\n'))
    result = self.cli(['promote', *self.flags(url)])
    self.assertEqual(json.loads(result.stdout)['appended'], 1)

  def test_historical_pending_source_does_not_block_a_complete_new_candidate(self):
    new = self.projects / 'work' / 'a-new.jsonl'
    old = self.projects / 'work' / 'z-old.jsonl'
    fixtures.write_jsonl(new, fixtures.user_line('NEW_CANDIDATE'))
    fixtures.write_jsonl(old, fixtures.user_line('old text ' * 10000, session_id='old'))
    os.utime(old, (0, 0))
    def reply(body):
      text = body.decode()
      findings = [fixtures.finding('NEW_CANDIDATE')] if 'NEW_CANDIDATE' in text else []
      return fixtures.analysis(findings), 200
    _, url = self.provider(reply)
    flags = self.flags(url)
    flags[flags.index('--started-at') + 1] = '2026-10-04T00:00:00Z'
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertEqual(self.source_named(self.status(url), 'z-old.jsonl')['status'], 'pending')
    self.promote_file()
    result = self.cli(['promote', *flags])
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertEqual(json.loads(result.stdout)['appended'], 1)

  def test_embedded_json_cookie_and_authorization_text_is_redacted(self):
    text = 'Response: {"cookie":{"sid":"JSONCOOKIESECRET"},"authorization":"JSONAUTHSECRET"} SAFE_MARKER'
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line(text))
    server, url = self.provider(lambda body: (fixtures.analysis([]), 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    sent = b''.join(server.bodies).decode()
    self.assertNotIn('JSONCOOKIESECRET', sent)
    self.assertNotIn('JSONAUTHSECRET', sent)
    self.assertIn('SAFE_MARKER', sent)

  def test_unredacted_image_stays_local_and_cannot_be_complete(self):
    record = json.loads(fixtures.user_line('image context'))
    record['message']['content'] = [
      {'type': 'text', 'text': 'SAFE_IMAGE_CONTEXT'},
      {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': fixtures.ONE_PIXEL}},
    ]
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', json.dumps(record) + '\n')
    server, url = self.provider(lambda body: (fixtures.analysis([]), 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    sent = b''.join(server.bodies).decode()
    self.assertNotIn('image_url', sent)
    self.assertNotIn(fixtures.ONE_PIXEL, sent)
    row = self.source_named(self.status(url), 's1.jsonl')
    self.assertEqual(row['status'], 'partial')
    self.assertFalse(row['latest_complete'])
    self.assertIn('media-unredacted', row['limitations'])


if __name__ == '__main__':
  suite = unittest.TestSuite(ContractRepairTests(name) for name in ContractRepairTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner().run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
