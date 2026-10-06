#!/usr/bin/env python3
"""CLI／localhost HTTP：完整回件保留，真正未完成仍補處理。"""

import importlib.util
import json
import os
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('audit_cli_fixtures', Path(__file__).with_name('session-audit.test.py'))
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class ReplyClassificationTests(fixtures.SessionAuditCliTest):
  def provider(self, reply):
    server = fixtures.serve(reply)
    self.addCleanup(fixtures.stop, server)
    return server, f'http://127.0.0.1:{server.server_address[1]}'

  def run_source(self, url, text='SOURCE_MARKER'):
    path = self.projects / 'work' / 's1.jsonl'
    fixtures.write_jsonl(path, fixtures.user_line(text))
    result = self.cli(['run', *self.flags(url)])
    self.assertEqual(result.returncode, 0, result.stderr)
    return path, self.status(url)

  def test_complete_prose_is_retained_redacted_and_not_read_again(self):
    content = '已讀完這段。格式需整理。Authorization: Bearer MODELREPLYSECRET'
    server, url = self.provider(lambda body: (fixtures.openai(content), 200))
    path, report = self.run_source(url)
    row = self.source_named(report, 's1.jsonl')
    self.assertEqual(row['status'], 'partial')
    self.assertFalse(row['latest_complete'])
    self.assertIn('response-needs-review', row['limitations'])
    replies = report['retained_replies']
    self.assertEqual(len(replies), 1)
    self.assertEqual(replies[0]['status'], 'review')
    self.assertIn('已讀完這段', replies[0]['content'])
    self.assertNotIn('MODELREPLYSECRET', replies[0]['content'])
    self.assertEqual(report['candidates'], [])
    self.assertEqual(report['rules']['weekly_segments'], [])
    before = len(server.bodies)
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertEqual(len(server.bodies), before)
    with path.open('a') as handle:
      handle.write(fixtures.user_line('APPENDED_ONLY', source_line=2))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertIn('APPENDED_ONLY', fixtures.user_text(server.bodies[-1]))
    self.assertNotIn('SOURCE_MARKER', fixtures.user_text(server.bodies[-1]))

  def test_missing_analysis_fields_remain_unknown_not_zero_rule_usage(self):
    server, url = self.provider(lambda body: (fixtures.openai(json.dumps({'findings': [], 'rule_tags': []})), 200))
    _, report = self.run_source(url)
    self.assertEqual(self.source_named(report, 's1.jsonl')['status'], 'partial')
    self.assertIn('missing-required', report['retained_replies'][0]['limitations'])
    self.assertEqual(report['rules']['weekly_segments'], [])
    self.assertEqual(report['rules']['tags'], [])
    self.assertEqual(report['rules']['last_seen'], [])
    self.assertEqual(report['candidates'], [])
    proc = self.cli(['report', *self.flags(url)])
    self.assertEqual(proc.returncode, 0, proc.stderr)
    self.assertIn('retained-replies parsed=0 review=1', proc.stdout)
    self.assertEqual(len(server.bodies), 1)

  def test_invalid_findings_do_not_discard_a_verified_sibling(self):
    unknown = fixtures.finding('SOURCE_MARKER', observation='unknown-reference')
    unknown['issue_ref'] = 'session:not-registered#1:unverified'
    bad_quote = fixtures.finding('NOT_IN_SOURCE', observation='bad-quote')
    good = fixtures.finding('SOURCE_MARKER', observation='verified-sibling')
    _, url = self.provider(lambda body: (fixtures.analysis([unknown, bad_quote, good], rule_tags=[]), 200))
    _, report = self.run_source(url)
    self.assertEqual([item['observation'] for item in report['candidates']], ['verified-sibling'])
    row = self.source_named(report, 's1.jsonl')
    self.assertEqual(row['status'], 'partial')
    self.assertIn('unknown-issue-ref', row['limitations'])
    self.assertIn('quote-not-in-snapshot', row['limitations'])
    self.assertEqual(report['retained_replies'][0]['status'], 'review')
    self.assertIn('bad-quote', report['retained_replies'][0]['content'])
    self.assertEqual(report['rules']['weekly_segments'], [])

  def test_incomplete_or_unreadable_envelopes_still_require_another_request(self):
    replies = {
      'truncated': fixtures.openai('TRUNCATED_TEXT', finish='length'),
      'empty': fixtures.openai('   '),
      'outer-json': 'not an API envelope',
      'missing-finish': json.dumps({'choices': [{'message': {'content': 'Looks complete'}}]}),
      'bad-message': json.dumps({'choices': [{'finish_reason': 'stop', 'message': ['not a message object']}]}),
    }
    for name in replies:
      fixtures.write_jsonl(self.projects / 'work' / f'{name}.jsonl', fixtures.user_line(f'CASE_{name}'))
    server, url = self.provider(lambda body: (next(payload for name, payload in replies.items() if f'CASE_{name}' in fixtures.user_text(body)), 200))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    report = self.status(url)
    self.assertTrue(all(row['status'] == 'failed' and not row['latest_complete'] for row in report['sources']))
    self.assertEqual(report.get('retained_replies', []), [])
    self.assertEqual(report['segments'], [])
    before = len(server.bodies)
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertEqual(len(server.bodies), before + len(replies))

  def test_wrong_field_types_are_classified_without_crashing_the_run(self):
    fixtures.write_jsonl(self.projects / 'work' / 'inner.jsonl', fixtures.user_line('BAD_INNER_FIELDS'))
    fixtures.write_jsonl(self.projects / 'work' / 'outer.jsonl', fixtures.user_line('BAD_FINISH_FIELD'))
    broken = fixtures.finding('BAD_INNER_FIELDS')
    broken['status'] = []
    good = fixtures.finding('BAD_INNER_FIELDS', observation='valid-sibling')
    def reply(body):
      if 'BAD_FINISH_FIELD' in fixtures.user_text(body):
        return fixtures.openai('Looks complete', finish=[]), 200
      return fixtures.analysis([broken, good], rule_tags=[]), 200
    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url)])
    self.assertEqual(done.returncode, 0, done.stderr)
    report = self.status(url)
    self.assertEqual(self.source_named(report, 'inner.jsonl')['status'], 'partial')
    self.assertEqual(self.source_named(report, 'outer.jsonl')['status'], 'failed')
    self.assertEqual([item['observation'] for item in report['candidates']], ['valid-sibling'])
    self.assertEqual(len(report['retained_replies']), 1)
    self.assertIn('missing-required', report['retained_replies'][0]['limitations'])

  def test_unverified_rule_fields_do_not_become_zero_usage_coverage(self):
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line('RULE_FIELD_MARKER'))
    def reply(body):
      label = fixtures.label_for(body, 'BASE_RULE_INDENT')
      tags = [{'rule': label, 'verdict': [], 'source_line': 1, 'quote': 'RULE_FIELD_MARKER'}]
      return fixtures.analysis([fixtures.finding('RULE_FIELD_MARKER', observation='verified-finding')], rule_tags=tags), 200
    _, url = self.provider(reply)
    done = self.cli(['run', *self.flags(url)])
    self.assertEqual(done.returncode, 0, done.stderr)
    report = self.status(url)
    self.assertEqual([item['observation'] for item in report['candidates']], ['verified-finding'])
    self.assertEqual(report['rules']['weekly_segments'], [])
    self.assertEqual(report['rules']['last_seen'], [])
    self.assertIn('rule-tags-unverified', self.source_named(report, 's1.jsonl')['limitations'])

  def test_truncated_reply_is_retried_with_a_smaller_fragment(self):
    seen = []
    def reply(body):
      seen.append(len(fixtures.user_text(body)))
      # 輸出被截斷通常是這段要寫的發現太多；切小後同一段能完整回覆。
      if len(fixtures.user_text(body)) > 6000:
        return fixtures.openai('{"continuity": "cut', finish='length'), 200
      return fixtures.analysis([], rule_tags=[]), 200
    _, url = self.provider(reply)
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line('LONG_PROSE ' * 1000))
    for _ in range(4):
      self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    row = self.source_named(self.status(url), 's1.jsonl')
    self.assertNotEqual(row['status'], 'failed', row)
    self.assertNotIn('finish-length', row['limitations'])
    self.assertTrue(any(size <= 6000 for size in seen), 'A truncated fragment must be resent smaller')

  def test_reply_still_truncated_after_shrinking_stays_failed_as_truncated(self):
    _, url = self.provider(lambda body: (fixtures.openai('{"continuity": "cut', finish='length'), 200))
    fixtures.write_jsonl(self.projects / 'work' / 's1.jsonl', fixtures.user_line('ALWAYS_TOO_LONG ' * 1000))
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    row = self.source_named(self.status(url), 's1.jsonl')
    self.assertEqual(row['status'], 'failed')
    self.assertIn('finish-length', row['limitations'])

  def test_http_failure_and_dropped_connection_are_not_complete_responses(self):
    fixtures.write_jsonl(self.projects / 'work' / 'http.jsonl', fixtures.user_line('HTTP_FAILURE'))
    fixtures.write_jsonl(self.projects / 'work' / 'dropped.jsonl', fixtures.user_line('DROPPED_CONNECTION'))
    server, url = self.provider(lambda body: (None, 200) if 'DROPPED_CONNECTION' in fixtures.user_text(body) else (fixtures.openai('wrong status'), 503))
    done = self.cli(['run', *self.flags(url)])
    self.assertEqual(done.returncode, 0, done.stderr)
    report = self.status(url)
    self.assertTrue(all(row['status'] == 'failed' for row in report['sources']))
    self.assertEqual(report.get('retained_replies', []), [])
    self.assertEqual(report['rules']['weekly_segments'], [])
    self.assertEqual(report['candidates'], [])
    self.assertEqual(len(server.bodies), 2)

  def test_review_marker_survives_later_valid_fragments_and_history_receipt(self):
    path = self.projects / 'work' / 's1.jsonl'
    fixtures.write_jsonl(path, fixtures.user_line('LONG_SOURCE ' * 6000, timestamp='2026-10-02T00:00:00Z'))
    os.utime(path, (1_700_000_000, 1_700_000_000))
    seen = []
    def reply(body):
      seen.append(body)
      return (fixtures.openai('完整分析文字，待整理。') if len(seen) == 1 else fixtures.analysis([], rule_tags=[])), 200
    _, url = self.provider(reply)
    flags = self.flags(url, started_at='2026-10-05T09:20:13Z') + ['--history-since', '2026-09-28T10:00:00Z', '--history-until', '2026-10-05T10:00:00Z']
    for _ in range(6):
      done = self.cli(['run', *flags])
      self.assertEqual(done.returncode, 0, done.stderr)
      if self.status(url)['history_batch']['ready_for_review']:
        break
    report = self.status(url)
    row = self.source_named(report, 's1.jsonl')
    self.assertEqual(row['status'], 'partial')
    self.assertFalse(row['latest_complete'])
    self.assertIn('response-needs-review', row['limitations'])
    self.assertGreater(len(report['retained_replies']), 1)
    self.assertEqual(report['history_batch']['status'], 'review-with-limitations')
    self.assertFalse(report['history_batch']['fully_complete'])
    self.assertEqual(report['history_batch']['partial'], 1)
    before = len(seen)
    self.assertEqual(self.cli(['run', *flags]).returncode, 0)
    self.assertEqual(len(seen), before)


if __name__ == '__main__':
  suite = unittest.TestSuite(ReplyClassificationTests(name) for name in ReplyClassificationTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
