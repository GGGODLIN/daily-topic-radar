#!/usr/bin/env python3
"""memory-origin.py 確定性部分的測試：抽原文、找改過的筆記、起點推進、靜默報告。不呼叫模型。"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('memory_origin', HERE / 'memory-origin.py')
mo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mo)


def write_jsonl(path, rows):
    path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows) + '\n', encoding='utf-8')


def tool_use(name, inp):
    return {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': name, 'input': inp}]}}


def tool_result(text):
    return {'type': 'user', 'message': {'content': [{'type': 'tool_result', 'content': text}]}}


class ExtractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.transcript = self.tmp / 'abc.jsonl'
        write_jsonl(self.transcript, [
            {'type': 'user', 'message': {'content': '只看大檔就好'}},                      # 1
            tool_use('Bash', {'command': 'scan --min-size 8MB'}),                           # 2
            tool_result('27 sessions, ratio 1.52 3.53 170 calls'),                          # 3
            {'type': 'user', 'isMeta': True, 'message': {'content': 'skill body'}},       # 4
            {'type': 'user', 'message': {'content': '<task-notification>done</task-notification>'}},  # 5
            tool_use('Write', {'file_path': '/x/.claude/memory/note.md', 'content': 'v1'}),  # 6
            {'type': 'user', 'message': {'content': '不對，是全部 session'}},              # 7
            tool_use('Edit', {'file_path': '/x/.claude/memory/note.md'}),                    # 8
        ])

    def test_write_line_first_and_last(self):
        _, first = mo.parse_transcript(self.transcript, 'note.md')
        _, last = mo.parse_transcript(self.transcript, 'note.md', 'last')
        self.assertEqual((first, last), (6, 8))

    def test_harness_messages_are_not_user_words(self):
        events, _ = mo.parse_transcript(self.transcript, 'note.md')
        users = [e['line'] for e in events if e['role'] == 'user']
        self.assertEqual(users, [1, 7])

    def test_anchor_finds_result_far_from_write_and_its_command(self):
        events, write_line = mo.parse_transcript(self.transcript, 'note.md')
        note = '---\noriginSessionId: 12345678-1234-1234-1234-123456789012\n---\n共 27 個 session，比值 1.52–3.53，170 次'
        items = mo.excerpt(events, write_line, 'anchor', note_text=note)
        self.assertEqual({ln for ln, _, _ in items}, {2, 3})

    def test_anchor_ignores_frontmatter_and_years(self):
        anchors = mo.anchors_of('---\noriginSessionId: 4029-488\n---\n2026 年 共 27 筆 `poll-ratio.py`')
        self.assertEqual(anchors, {'27', 'poll-ratio.py'})

    def test_all_mode_includes_user_correction_after_write(self):
        events, write_line = mo.parse_transcript(self.transcript, 'note.md')
        items = mo.excerpt(events, write_line, 'all', note_text='27 1.52 170')
        self.assertIn(7, {ln for ln, _, _ in items})


class ChangedTargetsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        # recent_writers 只認路徑裡有 /.claude/memory/ 的寫入，測試目錄要長得一樣。
        self.memory = self.tmp / '.claude' / 'memory'
        (self.memory / 'archive').mkdir(parents=True)
        self.projects = self.tmp / 'projects' / 'p'
        self.projects.mkdir(parents=True)
        origin = '11111111-1111-1111-1111-111111111111'
        self.origin_path = self.projects / f'{origin}.jsonl'
        write_jsonl(self.origin_path, [{'type': 'user', 'message': {'content': 'hi'}}])
        for idx, name in enumerate(('a.md', 'b.md', 'c.md')):
            path = self.memory / name
            path.write_text(f'---\noriginSessionId: {origin}\n---\nbody {name}\n', encoding='utf-8')
            os.utime(path, (1000 + idx * 100, 1000 + idx * 100))
        (self.memory / 'archive' / 'old.md').write_text('archived', encoding='utf-8')
        (self.memory / 'MEMORY.md').write_text('index', encoding='utf-8')
        editor = self.projects / '22222222-2222-2222-2222-222222222222.jsonl'
        write_jsonl(editor, [dict(tool_use('Edit', {'file_path': str(self.memory / 'b.md')}),
                                  timestamp='2026-11-10T00:00:00Z')])
        self._patch = (mo.MEMORY_ROOT, mo.PROJECTS_ROOT)
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = self.memory, self.tmp / 'projects'
        self.editor = editor

    def tearDown(self):
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = self._patch

    def _targets(self, since, cap):
        return mo.changed_targets(since, {self.origin_path.stem: self.origin_path}, cap)

    def test_editor_session_wins_over_origin(self):
        targets, _ = self._targets(0, 10)
        by_note = {rel: (Path(tr).name, which) for rel, _, tr, which in targets}
        self.assertEqual(by_note['b.md'], (self.editor.name, 'last'))
        self.assertEqual(by_note['a.md'], (self.origin_path.name, 'first'))
        self.assertNotIn('archive/old.md', by_note)
        self.assertNotIn('MEMORY.md', by_note)

    def test_cap_leaves_rest_for_next_round(self):
        targets, next_since = self._targets(0, 2)
        self.assertEqual([t[0] for t in targets], ['a.md', 'b.md'])
        self.assertEqual(next_since, 1100)
        targets, next_since = self._targets(next_since, 2)
        self.assertEqual([t[0] for t in targets], ['c.md'])
        self.assertEqual(next_since, 1200)


class ReportTest(unittest.TestCase):
    def _run(self, verdicts, silent):
        tmp = Path(tempfile.mkdtemp())
        for label, verdict in verdicts.items():
            (tmp / label).mkdir()
            (tmp / label / 'n.json').write_text(json.dumps({
                'note': 'n.md', 'transcript': '/t.jsonl', 'verdict': verdict}), encoding='utf-8')
        out = tmp / 'r.md'
        args = type('A', (), {'run_dir': str(tmp), 'date': 'd', 'out': str(out), 'silent_if_empty': silent})
        mo.cmd_report(args)
        return out.read_text(encoding='utf-8')

    def test_silent_only_when_nothing_flagged(self):
        ok = {'verdict': 'ok', 'issues': []}
        self.assertEqual(self._run({'a': ok, 'b': ok}, True).strip(), '__SILENT__')

    def test_failure_without_result_file_is_reported(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / 'a').mkdir()
        (tmp / 'failures.jsonl').write_text(json.dumps({'note': 'gone.md', 'label': 'a', 'error': '429', 'raw': ''}) + '\n',
                                            encoding='utf-8')
        out = tmp / 'r.md'
        args = type('A', (), {'run_dir': str(tmp), 'date': 'd', 'out': str(out), 'silent_if_empty': True})
        mo.cmd_report(args)
        text = out.read_text(encoding='utf-8')
        self.assertNotEqual(text.strip(), '__SILENT__')
        self.assertIn('- gone.md：a', text)

    def test_flagged_by_one_model_is_listed(self):
        bad = {'verdict': 'mismatch', 'issues': [{'claim': 'x', 'evidence_line': 3, 'why': 'y'}]}
        text = self._run({'a': {'verdict': 'ok', 'issues': []}, 'b': bad}, True)
        self.assertIn('### [n.md]', text)
        self.assertIn('b：「x」→ L3：y', text)


if __name__ == '__main__':
    unittest.main()
