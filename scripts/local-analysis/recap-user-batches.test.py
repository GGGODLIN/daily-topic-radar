#!/usr/bin/env python3
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('recap-user-batches.py')


class RecapUserBatchesTest(unittest.TestCase):
  def test_collects_non_keyword_corrections_with_exact_window_and_batches(self):
    with tempfile.TemporaryDirectory() as temp:
      root = Path(temp) / 'projects'
      project = root / 'example'
      project.mkdir(parents=True)
      source = project / 'session.jsonl'
      rows = [
        {'type': 'user', 'timestamp': '2026-10-24T00:00:00Z', 'message': {'content': '排除下界'}},
        {'type': 'user', 'timestamp': '2026-10-24T00:00:01Z', 'message': {'content': '沒看到workflow啊'}},
        {'type': 'user', 'timestamp': '2026-10-24T08:00:00+08:00', 'message': {'content': '同一下界也排除'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': [{'type': 'tool_result', 'content': '工具內不要'}, {'type': 'text', 'text': '又停了？'}]}},
        {'type': 'user', 'timestamp': '2026-10-25T00:00:00Z', 'message': {'content': '你還是沒有動作'}},
        {'type': 'user', 'timestamp': '2026-10-25T00:00:01Z', 'message': {'content': '排除上界之外'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'isMeta': True, 'message': {'content': '注入文字'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': '<task-notification>背景通知</task-notification>'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': 'Base directory for this skill: /skill\n指示'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': '<command-message>wait-what</command-message>\n<command-name>/wait-what</command-name>\n很長的注入正文'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'origin': {'kind': 'worker'}, 'message': {'content': 'worker 轉送'}},
        {'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': '普通回應也要讀'}},
      ]
      source.write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in rows) + '\n')
      os.utime(source, (1792886401, 1792886401))
      sub = project / 'subagents'
      sub.mkdir()
      (sub / 'agent.jsonl').write_text(json.dumps(rows[1]))
      out = Path(temp) / 'batches'
      run = subprocess.run([sys.executable, '-I', str(SCRIPT), '--root', str(root), '--start', '2026-10-24T00:00:00Z', '--end', '2026-10-25T00:00:00Z', '--out', str(out), '--batch-size', '2'], capture_output=True, text=True)
      self.assertEqual(run.returncode, 0, run.stderr)
      manifest = json.loads(Path(run.stdout.strip()).read_text())
      self.assertEqual(manifest['message_count'], 5)
      self.assertEqual([x['count'] for x in manifest['batches']], [2, 2, 1])
      messages = [json.loads(line) for batch in manifest['batches'] for line in Path(batch['path']).read_text().splitlines()]
      self.assertEqual([x['text'] for x in messages], ['沒看到workflow啊', '又停了？', '/wait-what', '普通回應也要讀', '你還是沒有動作'])
      self.assertEqual([x['line'] for x in messages], [2, 4, 10, 12, 5])
      self.assertTrue(all(x['path'] == str(source.resolve()) and x['session'] == 'session.jsonl' for x in messages))

  def test_reports_unparseable_records_without_inventing_message_timestamps(self):
    with tempfile.TemporaryDirectory() as temp:
      root = Path(temp) / 'projects'
      root.mkdir()
      (root / 'session.jsonl').write_text('\n'.join([
        'not-json',
        json.dumps({'type': 'user', 'message': {'content': '有內容但沒有時間'}}),
        json.dumps({'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': None}),
        json.dumps({'type': 'user', 'timestamp': '2026-10-24T12:00:00Z', 'message': {'content': '普通對話'}}),
      ]) + '\n')
      with (root / 'session.jsonl').open('ab') as source:
        source.write(b'{"broken":"\xff"}\n')
      run = subprocess.run([sys.executable, '-I', str(SCRIPT), '--root', str(root), '--start', '2026-10-24T00:00:00Z', '--end', '2026-10-25T00:00:00Z', '--out', str(Path(temp) / 'out')], capture_output=True, text=True)
      self.assertEqual(run.returncode, 0, run.stderr)
      manifest = json.loads(Path(run.stdout.strip()).read_text())
      self.assertEqual(manifest['message_count'], 1)
      self.assertEqual(manifest['diagnostics']['invalid_records'], 2)
      self.assertEqual(manifest['diagnostics']['human_messages_without_valid_timestamp'], 1)


if __name__ == '__main__':
  unittest.main()
