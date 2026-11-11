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


class FixTest(unittest.TestCase):
    NOTE = '---\nname: n\ndescription: 掃了 27 個 session\n---\n第一段 302 次。\n下一行。\n\n第二段。\n'

    def test_check_edits_marks_missing_and_duplicate_old(self):
        edits = mo.check_edits('a 1 b 1', [
            {'action': 'fix', 'old': 'a', 'new': 'A'},
            {'action': 'fix', 'old': '1', 'new': '2'},
            {'action': 'fix', 'old': 'zz', 'new': 'y'},
            {'action': 'keep', 'old': '', 'new': ''}])
        self.assertEqual([e.get('valid') for e in edits], [True, False, False, None])

    def test_correction_line_goes_after_block_and_outside_frontmatter(self):
        edits = mo.check_edits(self.NOTE, [
            {'action': 'fix', 'old': '302 次', 'new': '1511 次', 'evidence_line': 9, 'why': 'w'},
            {'action': 'fix', 'old': '27 個 session', 'new': '28 個 session', 'evidence_line': 3, 'why': 'w'}])
        text, applied, skipped = mo.apply_edits(self.NOTE, edits, '2026-11-11', '/t.jsonl')
        self.assertEqual((len(applied), skipped), (2, []))
        front = mo.FRONTMATTER.match(text)
        self.assertIn('description: 掃了 28 個 session', front.group(0))
        self.assertNotIn('更正行', front.group(0))
        blocks = text[front.end():].split('\n\n')
        self.assertTrue(blocks[0].startswith('> 更正行 2026-11-11：「27 個 session」'))
        self.assertEqual(blocks[1], '第一段 1511 次。\n下一行。')
        self.assertTrue(blocks[2].startswith('> 更正行 2026-11-11：「302 次」改成「1511 次」'))
        self.assertTrue(blocks[2].endswith('L9'))
        self.assertEqual(blocks[3], '第二段。\n')

    def test_second_edit_on_replaced_text_is_skipped(self):
        edits = mo.check_edits('x 302 y', [
            {'action': 'fix', 'old': '302', 'new': '303', 'why': 'w'},
            {'action': 'fix', 'old': '302 y', 'new': '304 y', 'why': 'w'}])
        text, applied, skipped = mo.apply_edits('x 302 y', edits, 'd', '/t')
        self.assertTrue(text.startswith('x 303 y\n\n> 更正行'))
        self.assertEqual((len(applied), len(skipped)), (1, 1))

    def test_bucket_and_parse_edits(self):
        bad = {'verdict': {'verdict': 'mismatch'}}
        ok = {'verdict': {'verdict': 'ok'}}
        self.assertEqual(mo.bucket_of({'a': bad, 'b': bad}), 'both')
        self.assertEqual(mo.bucket_of({'a': ok, 'b': bad}), 'b-only')
        self.assertIsNone(mo.bucket_of({'a': ok, 'b': ok}))
        self.assertEqual(mo.parse_edits('```json\n{"edits": []}\n```'), {'edits': []})

    def test_fix_excerpt_adds_cited_line_outside_excerpt(self):
        events = [{'line': n, 'role': 'tool_result', 'text': f'out {n}'} for n in range(1, 40)]
        events.insert(0, {'line': 0, 'role': 'user', 'text': 'go'})
        items = mo.fix_excerpt(events, 39, 'no numbers', [{'evidence_line': 2}])
        self.assertIn(2, {ln for ln, _, _ in items})

    def test_apply_skips_note_changed_after_proposal(self):
        tmp = Path(tempfile.mkdtemp())
        memory = tmp / 'memory'
        memory.mkdir()
        (memory / 'n.md').write_text('changed', encoding='utf-8')
        fix_dir = tmp / 'fix' / 'opus'
        fix_dir.mkdir(parents=True)
        (fix_dir / 'n.json').write_text(json.dumps({
            'note': 'n.md', 'transcript': '/t', 'note_text': 'x 302',
            'edits': [{'action': 'fix', 'old': '302', 'new': '303', 'valid': True}]}), encoding='utf-8')
        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            args = type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False})
            mo.cmd_apply(args)
        finally:
            mo.MEMORY_ROOT = saved
        self.assertEqual((memory / 'n.md').read_text(encoding='utf-8'), 'changed')


if __name__ == '__main__':
    unittest.main()
