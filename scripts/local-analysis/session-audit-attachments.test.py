#!/usr/bin/env python3
"""run/status：系統資訊附件不擋完成；排隊送出的使用者原話要讀進分析。"""

import importlib.util
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('attachment_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures


class AttachmentTests(population.PopulationTests):
  def attachment(self, path, payload):
    record = {'type': 'attachment', 'sessionId': path.stem, 'timestamp': '2026-10-05T00:00:01.000Z', 'attachment': payload}
    with path.open('a') as handle:
      handle.write(json.dumps(record, ensure_ascii=False) + '\n')

  def test_system_metadata_attachments_do_not_block_completion(self):
    path = self.source('work/s1.jsonl', 'PLAIN_WORK')
    self.attachment(path, {'type': 'environment', 'snapshot': {'cwd': '/work'}})
    self.attachment(path, {'type': 'date', 'date': '2026-10-05'})
    self.attachment(path, {'type': 'skill_listing', 'content': 'skill catalogue', 'names': ['a'], 'skillCount': 1, 'isInitial': True})
    self.attachment(path, {'type': 'hook_additional_context', 'content': ['hook note'], 'hookEvent': 'UserPromptSubmit', 'hookName': 'x', 'toolUseID': 't'})
    _, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    row = self.source_named(self.status(url), 's1.jsonl')
    self.assertEqual(row['status'], 'complete', row)
    self.assertNotIn('unreadable-attachment', row['limitations'])

  def test_queued_user_message_is_sent_for_analysis(self):
    path = self.source('work/s1.jsonl', 'PLAIN_WORK')
    self.attachment(path, {'type': 'queued_command', 'prompt': 'QUEUED_USER_WORDS 這樣不對', 'commandMode': 'prompt', 'origin': {'kind': 'human'}})
    server, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    self.assertIn('QUEUED_USER_WORDS', ''.join(fixtures.user_text(body) for body in server.bodies))

  def test_unknown_attachment_without_text_still_marks_partial(self):
    path = self.source('work/s1.jsonl', 'PLAIN_WORK')
    self.attachment(path, {'type': 'brand_new_kind', 'payload': {'x': 1}})
    _, url = self.provider()
    self.assertEqual(self.cli(['run', *self.flags(url)]).returncode, 0)
    row = self.source_named(self.status(url), 's1.jsonl')
    self.assertEqual(row['status'], 'partial')
    self.assertIn('unreadable-attachment', row['limitations'])


if __name__ == '__main__':
  suite = unittest.TestSuite(AttachmentTests(name) for name in AttachmentTests.__dict__ if name.startswith('test_'))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
