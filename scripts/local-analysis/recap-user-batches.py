#!/usr/bin/env python3
"""Prepare fixed-window user messages without semantic prefiltering."""

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path

NOISE_PREFIXES = (
  '<system-reminder>', '<task-notification>', '[SYSTEM NOTIFICATION',
  '<cross-session-message', '[main]', '<local-command',
  'Base directory for this skill:', 'Caveat:', 'Shell cwd',
  'Stop hook feedback', 'AUTO-SAVE',
)
CORRECTION_COMMANDS = {'/wait-what', '/wait-what-plus'}


def moment(value):
  parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
  if parsed.tzinfo is None:
    raise ValueError('timestamp requires a timezone')
  return parsed.astimezone(UTC)


def human_text(row):
  if row.get('type') != 'user' or row.get('isMeta') or row.get('isSidechain'):
    return ''
  origin = row.get('origin')
  if isinstance(origin, dict) and origin.get('kind') not in (None, 'human'):
    return ''
  if row.get('isSynthetic') or row.get('isCompactSummary'):
    return ''
  message = row.get('message')
  content = message.get('content', row.get('content', '')) if isinstance(message, dict) else row.get('content', '')
  if isinstance(content, list):
    content = '\n'.join(block['text'] for block in content
                        if isinstance(block, dict) and block.get('type') == 'text'
                        and isinstance(block.get('text'), str))
  if not isinstance(content, str):
    return ''
  text = content.strip()
  if not text or text.startswith(NOISE_PREFIXES):
    return ''
  if text.startswith(('<command-name>', '<command-message>', '<command-args>')):
    command = re.search(r'<command-name>([^<]+)</command-name>', text)
    return command.group(1) if command and command.group(1) in CORRECTION_COMMANDS else ''
  return text[:400]


def collect(root, start, end):
  messages = []
  invalid_records = 0
  missing_timestamps = 0
  files = sorted(path for path in root.rglob('*.jsonl')
                 if 'subagents' not in path.relative_to(root).parts)
  for path in files:
    with path.open('rb') as source:
      for line_number, line in enumerate(source, 1):
        if not line.strip():
          continue
        try:
          row = json.loads(line)
          if not isinstance(row, dict):
            raise ValueError('record is not an object')
        except ValueError:
          invalid_records += 1
          continue
        text = human_text(row)
        if not text:
          continue
        try:
          stamp = moment(row['timestamp'])
        except (KeyError, TypeError, ValueError, AttributeError):
          missing_timestamps += 1
          continue
        if start < stamp <= end:
          messages.append({
            'path': str(path), 'session': path.name, 'line': line_number,
            'timestamp': row['timestamp'], 'text': text,
          })
  messages.sort(key=lambda row: (moment(row['timestamp']), row['path'], row['line']))
  return messages, {
    'files_enumerated': len(files), 'invalid_records': invalid_records,
    'human_messages_without_valid_timestamp': missing_timestamps,
  }


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--root', required=True)
  parser.add_argument('--start', required=True)
  parser.add_argument('--end', required=True)
  parser.add_argument('--out', required=True)
  parser.add_argument('--batch-size', type=int, default=40)
  args = parser.parse_args()
  start, end = moment(args.start), moment(args.end)
  if start >= end or args.batch_size < 1:
    parser.error('start must precede end and batch-size must be positive')
  root = Path(args.root).expanduser().resolve()
  if not root.is_dir():
    parser.error('root must be an existing directory')
  out = Path(args.out).resolve()
  out.mkdir(parents=True, exist_ok=False, mode=0o700)
  messages, diagnostics = collect(root, start, end)
  batches = []
  for offset in range(0, len(messages), args.batch_size):
    batch = messages[offset:offset + args.batch_size]
    path = out / f'batch-{len(batches) + 1:04d}.jsonl'
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in batch), encoding='utf-8')
    batches.append({'path': str(path), 'count': len(batch)})
  manifest = out / 'manifest.json'
  manifest.write_text(json.dumps({
    'window_start': args.start, 'window_end': args.end,
    'message_count': len(messages), 'batches': batches,
    'diagnostics': diagnostics,
  }, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
  print(manifest)


if __name__ == '__main__':
  main()
