#!/usr/bin/env python3
"""memory-origin.py 確定性部分的測試：抽原文、找改過的筆記、起點推進、靜默報告。不呼叫模型。"""
import concurrent.futures
import datetime as dt
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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

    def test_already_scanned_without_post_since_writer_is_skipped(self):
        runs = self.tmp / 'runs' / 'full' / 'swe2max'
        runs.mkdir(parents=True)
        (runs / 'a.json').write_text('{"verdict":{"verdict":"ok"}}', encoding='utf-8')
        # editor timestamp is 2026-11-10T00:00:00Z (1794268800); set since after that so b.md has no post-since writer either
        os.utime(self.memory / 'a.md', (1794300000, 1794300000))
        os.utime(self.memory / 'c.md', (1794300100, 1794300100))
        os.utime(self.editor, (1794300200, 1794300200))
        targets, next_since = mo.changed_targets(
            1794270000, {self.origin_path.stem: self.origin_path}, 10, runs_root=self.tmp / 'runs')
        self.assertEqual([t[0] for t in targets], ['c.md'])
        self.assertEqual(next_since, 1794300100)

    def test_commit_cursor_holds_before_unanswered_note(self):
        run = self.tmp / 'run'
        (run / 'a').mkdir(parents=True)
        (run / 'meta.json').write_text(json.dumps({
            'prev_since': 1000.0, 'next_since': 1300.0,
            'target_mtimes': {'ok.md': 1100.0, 'dead.md': 1200.0, 'later.md': 1300.0}}), encoding='utf-8')
        for note in ('ok.md', 'later.md'):
            (run / 'a' / f'{mo.note_key(note)}.json').write_text(
                json.dumps({'note': note, 'verdict': {'verdict': 'ok'}}), encoding='utf-8')
        cur = self.tmp / 'cursor.json'
        mo.cmd_commit_cursor(type('A', (), {'run_dir': str(run), 'cursor': str(cur)}))
        saved = json.loads(cur.read_text(encoding='utf-8'))['since_epoch']
        self.assertGreater(saved, 1100.0)
        self.assertLess(saved, 1200.0)


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

    def test_report_tells_reader_to_fix_with_three_agreeing_fixers(self):
        bad = {'verdict': 'mismatch', 'issues': [{'claim': 'x', 'evidence_line': 3, 'why': 'y'}]}
        text = self._run({'a': {'verdict': 'ok', 'issues': []}, 'b': bad}, True)
        steps = text[text.index('## 怎麼修'):text.index('## 需要確認的筆記')]
        self.assertIn('--bucket any', steps)
        for label in ('sol-high', 'swe2max', 'opus'):
            self.assertIn(f'--model {label}=', steps)
        self.assertIn('--agree-with', steps)
        self.assertIn('--date d --dry-run', steps)
        self.assertIn('不要手改', steps)


class FixSelectionTest(unittest.TestCase):
    def test_any_bucket_picks_every_flagged_note(self):
        tmp = Path(tempfile.mkdtemp())
        memory, run = tmp / 'memory', tmp / 'run'
        memory.mkdir()
        bad = {'verdict': 'mismatch', 'issues': [{'claim': 'c', 'evidence_line': 1, 'why': 'w'}]}
        ok = {'verdict': 'ok', 'issues': []}
        for note, (a, b) in {'both.md': (bad, bad), 'one.md': (ok, bad), 'none.md': (ok, ok)}.items():
            (memory / note).write_text('text', encoding='utf-8')
            for label, verdict in (('a', a), ('b', b)):
                (run / label).mkdir(parents=True, exist_ok=True)
                (run / label / f'{note}.json').write_text(json.dumps(
                    {'note': note, 'transcript': '/t', 'verdict': verdict}), encoding='utf-8')
        seen = []
        saved = (mo.MEMORY_ROOT, mo.relay_key, mo.fix_one, mo.write_time_text)
        mo.MEMORY_ROOT, mo.relay_key, mo.write_time_text = memory, (lambda: 'k'), (lambda rel: None)
        mo.fix_one = lambda note, *rest: seen.append(note) or 'ok'
        try:
            for bucket, expected in ((['any'], ['both.md', 'one.md']), (['both'], ['both.md'])):
                seen.clear()
                args = type('A', (), {'run_dir': str(run), 'out_dir': str(tmp / 'fix'), 'model': ['x=y'],
                                      'bucket': bucket, 'notes': None, 'limit': 0, 'workers': 1})
                mo.cmd_fix(args)
                self.assertEqual(sorted(seen), expected)
        finally:
            mo.MEMORY_ROOT, mo.relay_key, mo.fix_one, mo.write_time_text = saved


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

    def test_frontmatter_fields_other_than_description_are_not_editable(self):
        note = '---\nname: n\ndescription: 掃了 27 個\nmetadata:\n  originSessionId: aaa\n---\nbody\n'
        edits = mo.check_edits(note, [
            {'action': 'fix', 'old': 'originSessionId: aaa', 'new': 'originSessionId: bbb'},
            {'action': 'fix', 'old': '27 個', 'new': '28 個'},
            {'action': 'fix', 'old': 'body', 'new': 'BODY'}])
        self.assertEqual([e['valid'] for e in edits], [False, True, True])

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

    def test_agreed_fixes_keeps_only_issues_every_other_fixer_fixes(self):
        mine = [{'issue': 1, 'action': 'fix'}, {'issue': 2, 'action': 'fix'},
                {'issue': 3, 'action': 'fix'}, {'issue': 4, 'action': 'keep'}]
        swe = [{'issue': 1, 'action': 'fix'}, {'issue': 2, 'action': 'fix'}, {'issue': 3, 'action': 'keep'}]
        opus = [{'issue': 1, 'action': 'fix'}, {'issue': 2, 'action': 'keep'}]
        kept = mo.agreed_fixes(mine, [swe, opus])
        self.assertEqual([(e['issue'], e['action']) for e in kept], [(1, 'fix'), (4, 'keep')])
        self.assertEqual(mo.agreed_fixes(mine, []), mine)

    def test_apply_needs_every_agreeing_fixer_to_have_seen_the_note(self):
        tmp = Path(tempfile.mkdtemp())
        memory = tmp / 'memory'
        memory.mkdir()
        (memory / 'n.md').write_text('x 302 y 7', encoding='utf-8')
        sol, swe = tmp / 'fix' / 'sol', tmp / 'fix' / 'swe'
        for d in (sol, swe):
            d.mkdir(parents=True)
        edits = [{'issue': 1, 'action': 'fix', 'old': '302', 'new': '303', 'why': 'w'},
                 {'issue': 2, 'action': 'fix', 'old': 'y 7', 'new': 'y 8', 'why': 'w'}]
        (sol / 'n.json').write_text(json.dumps({'note': 'n.md', 'transcript': '/t', 'note_text': 'x 302 y 7',
                                                'edits': edits}), encoding='utf-8')
        (swe / 'n.json').write_text(json.dumps({'edits': [{'issue': 1, 'action': 'fix'}, {'issue': 2, 'action': 'keep'}]}),
                                    encoding='utf-8')
        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            for agree in ([str(swe), str(tmp / 'missing')], [str(swe)]):
                args = type('A', (), {'fix_dir': str(sol), 'date': 'd', 'notes': None, 'dry_run': False,
                                      'agree_with': agree})
                mo.cmd_apply(args)
        finally:
            mo.MEMORY_ROOT = saved
        text = (memory / 'n.md').read_text(encoding='utf-8')
        self.assertTrue(text.startswith('x 303 y 7'))
        self.assertEqual(text.count('> 更正行'), 1)

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
            args = type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False,
                                  'agree_with': []})
            mo.cmd_apply(args)
        finally:
            mo.MEMORY_ROOT = saved
        self.assertEqual((memory / 'n.md').read_text(encoding='utf-8'), 'changed')

    def test_strip_correction_lines_and_preserve_mtime_and_last_writer_first_text(self):
        raw = '---\noriginSessionId: 11111111-1111-1111-1111-111111111111\n---\n正確句子。\n\n> 更正行 2026-11-11：「舊錯句」改成「正確句子」，寫入當下就記錯\n\n第二段。\n'
        stripped = mo.strip_correction_lines(raw)
        self.assertNotIn('舊錯句', stripped)
        self.assertIn('正確句子。', stripped)
        self.assertIn('第二段。', stripped)

        tmp = Path(tempfile.mkdtemp())
        memory, run = tmp / 'memory', tmp / 'run'
        memory.mkdir()
        note = memory / 'edited.md'
        note.write_text('---\noriginSessionId: 11111111-1111-1111-1111-111111111111\n---\nnew body\n', encoding='utf-8')
        os.utime(note, (1500, 1500))
        (run / 'a').mkdir(parents=True)
        (run / 'a' / 'edited.json').write_text(json.dumps({
            'note': 'edited.md', 'transcript': '/p/22222222-2222-2222-2222-222222222222.jsonl', 'which': 'last',
            'verdict': {'verdict': 'mismatch', 'issues': [{'claim': 'new body', 'evidence_line': 1, 'why': 'w'}]}}),
            encoding='utf-8')
        seen_first = []
        saved = (mo.MEMORY_ROOT, mo.relay_key, mo.fix_one, mo.write_time_text)
        mo.MEMORY_ROOT, mo.relay_key, mo.write_time_text = memory, (lambda: 'k'), (lambda rel: 'old first body')
        mo.fix_one = lambda n, nt, first, *rest: seen_first.append((first, rest[-1])) or 'ok'
        try:
            mo.cmd_fix(type('A', (), {'run_dir': str(run), 'out_dir': str(tmp / 'fix'), 'model': ['x=y'],
                                      'bucket': ['any'], 'notes': None, 'limit': 0, 'workers': 1}))
            self.assertEqual(seen_first, [(None, 'last')])
            fix_dir = tmp / 'apply_fix'
            fix_dir.mkdir()
            (fix_dir / 'edited.json').write_text(json.dumps({
                'note': 'edited.md', 'transcript': '/t', 'note_text': note.read_text(encoding='utf-8'),
                'edits': [{'action': 'fix', 'old': 'new body', 'new': 'fixed body', 'why': 'w', 'valid': True}]}),
                encoding='utf-8')
            mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': '2026-11-17', 'notes': None,
                                        'dry_run': False, 'agree_with': []}))
            self.assertEqual(int(note.stat().st_mtime), 1500)
        finally:
            mo.MEMORY_ROOT, mo.relay_key, mo.fix_one, mo.write_time_text = saved


class RegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmps = []

    def tearDown(self):
        for d in self._tmps:
            shutil.rmtree(d, ignore_errors=True)

    def _mkdtemp(self):
        p = Path(tempfile.mkdtemp())
        self._tmps.append(p)
        return p

    # O1
    def test_first_json_handles_braces_inside_strings(self):
        v1 = mo.parse_verdict('prefix\n```json\n{"verdict": "mismatch", "issues": [{"claim": "a } b", "evidence_line": 2, "why": "plain"}]}\n```')
        self.assertIsInstance(v1, dict)
        self.assertEqual(v1['issues'][0]['claim'], 'a } b')

        v2 = mo.parse_verdict('{"verdict": "mismatch", "issues": [{"claim": "a { b", "evidence_line": 2, "why": "plain"}]}')
        self.assertIsInstance(v2, dict)
        self.assertEqual(v2['issues'][0]['claim'], 'a { b')

        e_raw = 'Result:\n{"edits": [{"issue": 1, "action": "fix", "old": "a } } b", "new": "c { d", "why": "w"}]}'
        e = mo.parse_edits(e_raw)
        self.assertIsInstance(e, dict)
        self.assertEqual(e['edits'][0]['old'], 'a } } b')
        self.assertEqual(e['edits'][0]['new'], 'c { d')

    # O2
    def test_in_metadata_allows_edit_starting_at_description_prefix(self):
        note1 = '---\nname: n\ndescription: 掃了 27 個\n---\nbody\n'
        edits1 = mo.check_edits(note1, [{'action': 'fix', 'old': 'description: 掃了 27 個', 'new': 'description: 掃了 28 個'}])
        self.assertTrue(edits1[0]['valid'], edits1[0].get('invalid_reason'))

        note2 = '---\nname: n\ndescription: 舊\n---\nbody\n'
        edits2 = mo.check_edits(note2, [{'action': 'fix', 'old': 'description: 舊', 'new': 'description: 新'}])
        self.assertTrue(edits2[0]['valid'], edits2[0].get('invalid_reason'))

    # O3
    def test_check_edits_blocks_multiline_or_newline_injection_in_description(self):
        note = '---\nname: n\ndescription: 掃了 27 個\noriginSessionId: 11111111-1111-1111-1111-111111111111\n---\nbody\n'
        edits = mo.check_edits(note, [
            {'action': 'fix', 'old': '27 個\noriginSessionId: 11111111-1111-1111-1111-111111111111', 'new': '28 個'},
            {'action': 'fix', 'old': '27 個\n', 'new': '28 個 '},
            {'action': 'fix', 'old': '27 個', 'new': '28 個\n---\ninjected'},
        ])
        for idx, edit in enumerate(edits):
            self.assertIs(edit['valid'], False, f'case {idx} should be blocked')

    # O4
    def test_recent_writers_compares_timestamps_chronologically(self):
        tmp = self._mkdtemp()
        proj = tmp / 'p'
        proj.mkdir()
        target = '/x/.claude/memory/b.md'
        since = 1794260000  # 2026-11-09T21:33:20Z

        t_plus8 = proj / '11111111-1111-1111-1111-111111111111.jsonl'
        t_03z = proj / '22222222-2222-2222-2222-222222222222.jsonl'
        write_jsonl(t_plus8, [dict(tool_use('Edit', {'file_path': target}), timestamp='2026-11-10T10:00:00+08:00')])
        write_jsonl(t_03z, [dict(tool_use('Edit', {'file_path': target}), timestamp='2026-11-10T03:00:00Z')])
        writers = mo.recent_writers(since, root=tmp)
        self.assertEqual(writers['b.md'], t_03z)

        tmp2 = self._mkdtemp()
        proj2 = tmp2 / 'p'
        proj2.mkdir()
        t_sec = proj2 / '33333333-3333-3333-3333-333333333333.jsonl'
        t_ms = proj2 / '44444444-4444-4444-4444-444444444444.jsonl'
        write_jsonl(t_sec, [dict(tool_use('Edit', {'file_path': target}), timestamp='2026-11-10T03:00:00Z')])
        write_jsonl(t_ms, [dict(tool_use('Edit', {'file_path': target}), timestamp='2026-11-10T03:00:00.500Z')])
        writers2 = mo.recent_writers(since, root=tmp2)
        self.assertEqual(writers2['b.md'], t_ms)

    # O5
    def test_gpt_quota_handles_missing_binary_timeout_and_leap_day(self):
        with mock.patch.object(mo.subprocess, 'run', side_effect=FileNotFoundError('missing')):
            self.assertEqual(mo.gpt_quota(), (None, None))

        with mock.patch.object(mo.subprocess, 'run', side_effect=subprocess.TimeoutExpired('relay-status', 120)):
            self.assertEqual(mo.gpt_quota(), (None, None))

        class FakeDateTime(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime(2026, 11, 23, 10, 0)

        fake_proc = type('P', (), {'stdout': 'GPT 週額度\n  週額度 92%  02-29 12:00 重設\n'})()
        with mock.patch.object(mo.subprocess, 'run', return_value=fake_proc), \
             mock.patch('datetime.datetime', FakeDateTime):
            pct, reset = mo.gpt_quota()
        self.assertTrue((pct is None and reset is None) or (pct == 92 and isinstance(reset, dt.datetime)))

    # O6
    def test_atomic_writes_leave_no_temp_files_and_preserve_target_on_error(self):
        tmp = self._mkdtemp()
        memory = tmp / 'memory'
        memory.mkdir()
        target = memory / 'n.md'
        target.write_text('x 302 y', encoding='utf-8')

        run = tmp / 'run'
        (run / 'a').mkdir(parents=True)
        (run / 'a' / 'n.json').write_text(json.dumps({
            'note': 'n.md', 'transcript': '/t',
            'verdict': {'verdict': 'mismatch', 'issues': [{'claim': '302', 'evidence_line': 1, 'why': 'w'}]}}),
            encoding='utf-8')
        (run / 'meta.json').write_text(json.dumps({
            'prev_since': 1000.0, 'next_since': 1300.0, 'target_mtimes': {'n.md': 1100.0}}), encoding='utf-8')

        fix_dir = tmp / 'fix' / 'sol'
        fix_dir.mkdir(parents=True)
        (fix_dir / 'n.json').write_text(json.dumps({
            'note': 'n.md', 'transcript': '/t', 'note_text': 'x 302 y',
            'edits': [{'action': 'fix', 'old': '302', 'new': '303', 'why': 'w', 'valid': True}]}),
            encoding='utf-8')

        out_dir = tmp / 'out_dir'
        out_dir.mkdir()
        out_report = out_dir / 'r.md'
        cur_dir = tmp / 'cur_dir'
        cur_dir.mkdir()
        cur_file = cur_dir / 'cursor.json'

        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            with mock.patch('os.replace', wraps=os.replace) as spy:
                mo.cmd_report(type('A', (), {'run_dir': str(run), 'date': 'd', 'out': str(out_report), 'silent_if_empty': False}))
                mo.cmd_commit_cursor(type('A', (), {'run_dir': str(run), 'cursor': str(cur_file)}))
                mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False, 'agree_with': []}))

            replaced_dsts = {Path(c.args[1]).resolve() for c in spy.call_args_list if len(c.args) >= 2}
            self.assertIn(out_report.resolve(), replaced_dsts)
            self.assertIn(cur_file.resolve(), replaced_dsts)
            self.assertIn(target.resolve(), replaced_dsts)
            self.assertEqual([p.name for p in out_dir.iterdir()], ['r.md'])
            self.assertEqual([p.name for p in cur_dir.iterdir()], ['cursor.json'])
            self.assertEqual([p.name for p in memory.iterdir()], ['n.md'])

            # Simulate replace failure mid-write: original content stays intact and no temp files remain.
            out_report.write_text('ORIG_REPORT', encoding='utf-8')
            cur_file.write_text('ORIG_CURSOR', encoding='utf-8')
            target.write_text('ORIG_NOTE', encoding='utf-8')
            (fix_dir / 'n.json').write_text(json.dumps({
                'note': 'n.md', 'transcript': '/t', 'note_text': 'ORIG_NOTE',
                'edits': [{'action': 'fix', 'old': 'ORIG_NOTE', 'new': 'NEW_NOTE', 'why': 'w', 'valid': True}]}),
                encoding='utf-8')

            with mock.patch('os.replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    mo.cmd_report(type('A', (), {'run_dir': str(run), 'date': 'd', 'out': str(out_report), 'silent_if_empty': False}))
                with self.assertRaises(OSError):
                    mo.cmd_commit_cursor(type('A', (), {'run_dir': str(run), 'cursor': str(cur_file)}))
                with self.assertRaises(OSError):
                    mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False, 'agree_with': []}))

            self.assertEqual(out_report.read_text(encoding='utf-8'), 'ORIG_REPORT')
            self.assertEqual(cur_file.read_text(encoding='utf-8'), 'ORIG_CURSOR')
            self.assertEqual(target.read_text(encoding='utf-8'), 'ORIG_NOTE')
            self.assertEqual([p.name for p in out_dir.iterdir()], ['r.md'])
            self.assertEqual([p.name for p in cur_dir.iterdir()], ['cursor.json'])
            self.assertEqual([p.name for p in memory.iterdir()], ['n.md'])
        finally:
            mo.MEMORY_ROOT = saved

    # O7
    def test_frontmatter_handles_crlf_empty_and_eof_terminated(self):
        crlf = '---\r\nname: n\r\noriginSessionId: 11111111-1111-1111-1111-111111111111\r\ndescription: 27 個\r\n---\r\n共 42 筆 `foo.py`\r\n'
        self.assertEqual(mo.anchors_of(crlf), {'42', 'foo.py'})
        self.assertEqual(mo.body_of(crlf), '共 42 筆 `foo.py`')
        crlf_edits = mo.check_edits(crlf, [
            {'action': 'fix', 'old': 'originSessionId: 11111111-1111-1111-1111-111111111111',
             'new': 'originSessionId: 22222222-2222-2222-2222-222222222222'},
            {'action': 'fix', 'old': '27 個', 'new': '28 個', 'why': 'w'},
        ])
        self.assertEqual([e['valid'] for e in crlf_edits], [False, True])
        crlf_out, applied, skipped = mo.apply_edits(crlf, crlf_edits, '2026-11-11', '/t')
        self.assertEqual((len(applied), skipped), (1, []))
        fm_crlf = mo.FRONTMATTER.match(crlf_out)
        self.assertIsNotNone(fm_crlf)
        self.assertIn('description: 28 個', fm_crlf.group(0))
        self.assertNotIn('更正行', fm_crlf.group(0))
        self.assertIn('> 更正行', crlf_out[fm_crlf.end():])

        empty = '---\n---\n共 42 筆\n'
        self.assertEqual(mo.anchors_of(empty), {'42'})
        self.assertEqual(mo.body_of(empty), '共 42 筆')
        empty_edits = mo.check_edits(empty, [{'action': 'fix', 'old': '42 筆', 'new': '43 筆', 'why': 'w'}])
        self.assertEqual([e['valid'] for e in empty_edits], [True])
        empty_out, applied_e, _ = mo.apply_edits(empty, empty_edits, '2026-11-11', '/t')
        self.assertEqual(len(applied_e), 1)
        self.assertTrue(empty_out.startswith('---\n---\n共 43 筆'))

        eof = '---\nname: n\ndescription: 27 個\n---'
        self.assertEqual(mo.anchors_of(eof), set())
        self.assertEqual(mo.body_of(eof), '')
        eof_edits = mo.check_edits(eof, [
            {'action': 'fix', 'old': 'name: n', 'new': 'name: m'},
            {'action': 'fix', 'old': '27 個', 'new': '28 個', 'why': 'w'},
        ])
        self.assertEqual([e['valid'] for e in eof_edits], [False, True])
        eof_out, applied_eof, _ = mo.apply_edits(eof, eof_edits, '2026-11-11', '/t')
        self.assertEqual(len(applied_eof), 1)
        fm_eof = mo.FRONTMATTER.match(eof_out)
        self.assertIsNotNone(fm_eof)
        self.assertIn('description: 28 個', fm_eof.group(0))
        self.assertNotIn('更正行', fm_eof.group(0))
        self.assertTrue(any(ln.startswith('> 更正行') for ln in eof_out[fm_eof.end():].splitlines()))

    # O8
    def test_excerpt_all_deduplicates_long_event_picked_by_anchor_and_near(self):
        long_res = 'anchors 111 222 333 ' + ('z' * 1980)  # 2000 chars: >1500 (near cap) and <=2500 (anchor cap)
        events = [
            {'line': 1, 'role': 'user', 'text': 'go'},
            {'line': 2, 'role': 'tool_use', 'text': 'Bash {"command": "scan"}'},
            {'line': 3, 'role': 'tool_result', 'text': long_res},
            {'line': 4, 'role': 'tool_use', 'text': 'Write {"file_path": "/x/.claude/memory/n.md"}'},
        ]
        items = mo.excerpt(events, 4, 'all', near_turns=3, note_text='111 222 333')
        line3 = [item for item in items if item[0] == 3]
        self.assertEqual(len(line3), 1)
        self.assertEqual(len(line3[0][2]), 2000)

    # O9
    def test_build_prompt_and_fix_prompt_do_not_expand_placeholders_in_note(self):
        note_text = 'note body mentioning {excerpt} and {first} and {issues}'
        items = [(1, 'user', 'UNIQUE_TRANSCRIPT_MARKER')]
        issues = [{'claim': 'c', 'evidence_line': 1, 'why': 'w'}]

        p1 = mo.build_prompt(note_text, items)
        note_part1, excerpt_part1 = p1.split('=== 對話摘錄 ===', 1)
        self.assertIn(note_text, note_part1)
        self.assertNotIn('UNIQUE_TRANSCRIPT_MARKER', note_part1)
        self.assertIn('UNIQUE_TRANSCRIPT_MARKER', excerpt_part1)

        p2 = mo.build_fix_prompt(note_text, 'first with {issues} and {excerpt}', issues, items)
        before_first, rest = p2.split('=== 寫入當下的版本', 1)
        first_part, after_first = rest.split('=== 核對員標出的問題 ===', 1)
        issues_part, excerpt_part2 = after_first.split('=== 對話摘錄 ===', 1)
        self.assertIn(note_text, before_first)
        self.assertNotIn('UNIQUE_TRANSCRIPT_MARKER', before_first)
        self.assertIn('first with {issues} and {excerpt}', first_part)
        self.assertNotIn('UNIQUE_TRANSCRIPT_MARKER', first_part)
        self.assertNotIn('UNIQUE_TRANSCRIPT_MARKER', issues_part)
        self.assertIn('UNIQUE_TRANSCRIPT_MARKER', excerpt_part2)

    # O10
    def test_null_and_non_dict_json_values_do_not_crash(self):
        self.assertEqual(mo._text_of([{'type': 'text', 'text': None}]), '')

        tmp = self._mkdtemp()
        tr = tmp / 'bad.jsonl'
        tr.write_text('\n'.join([
            'null',
            '[]',
            json.dumps({'type': 'user', 'message': {'content': [{'type': 'text', 'text': None}]}}),
            json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'name': 'Write', 'input': None}]}}),
            json.dumps({'type': 'user', 'message': {'content': 'real user message'}}),
        ]) + '\n', encoding='utf-8')
        events, wl = mo.parse_transcript(tr, 'note.md')
        self.assertIsNone(wl)
        self.assertTrue(any(e['text'] == 'real user message' for e in events))

        self.assertEqual(mo.flagged_issues({'a': {'verdict': {'verdict': 'mismatch', 'issues': None}}}), [])
        self.assertEqual(mo.flagged_issues({'a': {'verdict': {'verdict': 'mismatch', 'issues': [None]}}}), [])

        checked = mo.check_edits('abc', [None, {'action': 'fix', 'old': 123, 'new': 'x'}])
        self.assertFalse(any(isinstance(e, dict) and e.get('valid') for e in checked))

        agreed = mo.agreed_fixes([None, {'issue': 1, 'action': 'fix'}], [[None, {'issue': 1, 'action': 'fix'}]])
        self.assertTrue(any(isinstance(e, dict) and e.get('issue') == 1 for e in agreed))

    # O11
    def test_changed_targets_does_not_skip_same_mtime_notes_at_cap_boundary(self):
        tmp = self._mkdtemp()
        memory = tmp / '.claude' / 'memory'
        memory.mkdir(parents=True)
        projects = tmp / 'projects' / 'p'
        projects.mkdir(parents=True)
        origin = '11111111-1111-1111-1111-111111111111'
        tr = projects / f'{origin}.jsonl'
        write_jsonl(tr, [{'type': 'user', 'message': {'content': 'hi'}}])
        for name, mtime in (('a.md', 1500), ('b.md', 1500), ('c.md', 1500), ('d.md', 1600)):
            p = memory / name
            p.write_text(f'---\noriginSessionId: {origin}\n---\nbody {name}\n', encoding='utf-8')
            os.utime(p, (mtime, mtime))

        saved = (mo.MEMORY_ROOT, mo.PROJECTS_ROOT)
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = memory, tmp / 'projects'
        try:
            t1, next_since = mo.changed_targets(1000, {origin: tr}, cap=2, runs_root=tmp / 'runs')
            t2, next_since2 = mo.changed_targets(next_since, {origin: tr}, cap=2, runs_root=tmp / 'runs')
        finally:
            mo.MEMORY_ROOT, mo.PROJECTS_ROOT = saved

        self.assertEqual([t[0] for t in t1], ['a.md', 'b.md', 'c.md'])
        self.assertEqual(next_since, 1500)
        self.assertEqual([t[0] for t in t2], ['d.md'])
        self.assertEqual(next_since2, 1600)

    # O12
    def test_scan_one_and_fix_one_catch_missing_transcript_oserror(self):
        tmp = self._mkdtemp()
        missing = tmp / 'does-not-exist.jsonl'
        run_dir = tmp / 'run'
        run_dir.mkdir()
        res_scan = mo.scan_one('n.md', 'body', missing, 'first', 'alias', 'lbl', run_dir, 'key', None)
        self.assertEqual(res_scan, 'fail')
        fail_lines = (run_dir / 'failures.jsonl').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(fail_lines), 1)
        self.assertEqual(json.loads(fail_lines[0])['note'], 'n.md')

        fix_fail = tmp / 'fix_failures.jsonl'
        res_fix = mo.fix_one(
            'n.md', 'body', None, [{'claim': 'c', 'evidence_line': 1, 'why': 'w'}],
            missing, None, 'alias', tmp / 'out.json', fix_fail, 'key')
        self.assertEqual(res_fix, 'fail')
        fix_lines = fix_fail.read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(fix_lines), 1)
        self.assertEqual(json.loads(fix_lines[0])['note'], 'n.md')

    # O13
    def test_cmd_scan_closes_pools_on_exception_and_thread_safe_append(self):
        self.assertTrue(hasattr(mo, '_APPEND_LOCK'))

        tmp = self._mkdtemp()
        memory = tmp / '.claude' / 'memory'
        memory.mkdir(parents=True)
        projects = tmp / 'projects' / 'p'
        projects.mkdir(parents=True)
        origin = '11111111-1111-1111-1111-111111111111'
        write_jsonl(projects / f'{origin}.jsonl', [{'type': 'user', 'message': {'content': 'hi'}}])
        (memory / 'n.md').write_text(f'---\noriginSessionId: {origin}\n---\nbody\n', encoding='utf-8')
        run_dir = tmp / 'run'

        created = []
        RealPool = concurrent.futures.ThreadPoolExecutor

        class TrackingPool(RealPool):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.shutdown_called = 0
                created.append(self)

            def shutdown(self, wait=True, *, cancel_futures=False):
                self.shutdown_called += 1
                return super().shutdown(wait=wait, cancel_futures=cancel_futures)

        def boom(*a, **kw):
            raise RuntimeError('boom')

        saved = (mo.MEMORY_ROOT, mo.PROJECTS_ROOT, mo.relay_key, mo.scan_one)
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = memory, tmp / 'projects'
        mo.relay_key = lambda: 'k'
        mo.scan_one = boom
        try:
            args = type('A', (), {
                'run_dir': str(run_dir), 'model': ['a=m'], 'workers': 1, 'gpt_guard': 0,
                'limit': 0, 'scope': 'all', 'cursor': str(tmp / 'cursor.json'), 'cap': 10})
            with mock.patch('concurrent.futures.ThreadPoolExecutor', TrackingPool):
                with self.assertRaises(RuntimeError):
                    mo.cmd_scan(args)
        finally:
            mo.MEMORY_ROOT, mo.PROJECTS_ROOT, mo.relay_key, mo.scan_one = saved

        self.assertTrue(created)
        self.assertTrue(all(p.shutdown_called > 0 for p in created))

    # O14
    def test_corrupt_json_and_blank_failures_lines_do_not_crash_load_report_apply(self):
        tmp = self._mkdtemp()
        memory = tmp / 'memory'
        memory.mkdir()
        (memory / 'valid.md').write_text('x 302 y', encoding='utf-8')

        run = tmp / 'run'
        (run / 'a').mkdir(parents=True)
        (run / 'a' / 'valid.json').write_text(json.dumps({
            'note': 'valid.md', 'transcript': '/t',
            'verdict': {'verdict': 'mismatch', 'issues': [{'claim': '302', 'evidence_line': 1, 'why': 'w'}]}}),
            encoding='utf-8')
        (run / 'a' / 'corrupt.json').write_text('{broken json', encoding='utf-8')
        (run / 'failures.jsonl').write_text(
            '\n{broken failure line\n' + json.dumps({'note': 'gone.md', 'label': 'a', 'error': '429', 'raw': ''}) + '\n',
            encoding='utf-8')

        labels, notes = mo.load_scan(run)
        self.assertEqual(labels, ['a'])
        self.assertIn('valid.md', notes)

        out_report = tmp / 'report.md'
        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            mo.cmd_report(type('A', (), {'run_dir': str(run), 'date': 'd', 'out': str(out_report), 'silent_if_empty': False}))
            report_text = out_report.read_text(encoding='utf-8')
            self.assertIn('valid.md', report_text)
            self.assertIn('gone.md', report_text)

            fix_dir = tmp / 'fix' / 'sol'
            fix_dir.mkdir(parents=True)
            (fix_dir / 'valid.json').write_text(json.dumps({
                'note': 'valid.md', 'transcript': '/t', 'note_text': 'x 302 y',
                'edits': [{'action': 'fix', 'old': '302', 'new': '303', 'why': 'w', 'valid': True}]}),
                encoding='utf-8')
            (fix_dir / 'corrupt.json').write_text('{broken fix json', encoding='utf-8')
            mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False, 'agree_with': []}))
            self.assertTrue((memory / 'valid.md').read_text(encoding='utf-8').startswith('x 303 y'))
        finally:
            mo.MEMORY_ROOT = saved

    # O15
    def test_note_paths_skips_broken_symlink_and_md_dir_and_report_creates_parent(self):
        tmp = self._mkdtemp()
        memory = tmp / 'memory'
        memory.mkdir()
        real = memory / 'real.md'
        real.write_text('ok', encoding='utf-8')
        (memory / 'broken.md').symlink_to(tmp / 'nonexistent.md')
        (memory / 'dir.md').mkdir()

        self.assertEqual(list(mo.note_paths(memory)), [real])

        run = tmp / 'run'
        (run / 'a').mkdir(parents=True)
        nested_report = tmp / 'nested' / 'dir' / 'report.md'
        mo.cmd_report(type('A', (), {'run_dir': str(run), 'date': 'd', 'out': str(nested_report), 'silent_if_empty': True}))
        self.assertTrue(nested_report.exists())

        nested_cands = tmp / 'nested2' / 'dir' / 'cands.jsonl'
        saved = (mo.MEMORY_ROOT, mo.PROJECTS_ROOT)
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = memory, tmp / 'projects'
        try:
            mo.cmd_candidates(type('A', (), {'out': str(nested_cands)}))
            self.assertTrue(nested_cands.exists())
        finally:
            mo.MEMORY_ROOT, mo.PROJECTS_ROOT = saved

    # O16
    def test_commit_cursor_handles_null_next_since_and_prev_since(self):
        tmp = self._mkdtemp()
        run = tmp / 'run'
        (run / 'a').mkdir(parents=True)
        (run / 'meta.json').write_text(json.dumps({
            'next_since': None, 'started_epoch': 1200.0, 'prev_since': None,
            'target_mtimes': {'dead.md': 1150.0}}), encoding='utf-8')
        cur = tmp / 'cursor.json'
        mo.cmd_commit_cursor(type('A', (), {'run_dir': str(run), 'cursor': str(cur)}))
        saved = json.loads(cur.read_text(encoding='utf-8'))['since_epoch']
        self.assertIsNotNone(saved)
        self.assertLess(saved, 1150.0)

    # O17
    def test_cmd_scan_limit_updates_meta_targets_and_mtimes(self):
        tmp = self._mkdtemp()
        memory = tmp / '.claude' / 'memory'
        memory.mkdir(parents=True)
        projects = tmp / 'projects' / 'p'
        projects.mkdir(parents=True)
        origin = '11111111-1111-1111-1111-111111111111'
        write_jsonl(projects / f'{origin}.jsonl', [{'type': 'user', 'message': {'content': 'hi'}}])
        for name, mtime in (('n1.md', 1100), ('n2.md', 1200), ('n3.md', 1300)):
            p = memory / name
            p.write_text(f'---\noriginSessionId: {origin}\n---\nbody {name}\n', encoding='utf-8')
            os.utime(p, (mtime, mtime))
        cur = tmp / 'cursor.json'
        cur.write_text(json.dumps({'since_epoch': 1000}), encoding='utf-8')
        run_dir = tmp / 'run'

        saved = (mo.MEMORY_ROOT, mo.PROJECTS_ROOT, mo.relay_key, mo.scan_one)
        mo.MEMORY_ROOT, mo.PROJECTS_ROOT = memory, tmp / 'projects'
        mo.relay_key = lambda: 'k'
        mo.scan_one = lambda *a, **kw: 'ok'
        try:
            mo.cmd_scan(type('A', (), {
                'run_dir': str(run_dir), 'model': ['a=m'], 'workers': 1, 'gpt_guard': 0,
                'limit': 1, 'scope': 'changed', 'cursor': str(cur), 'cap': 10}))
        finally:
            mo.MEMORY_ROOT, mo.PROJECTS_ROOT, mo.relay_key, mo.scan_one = saved

        meta = json.loads((run_dir / 'meta.json').read_text(encoding='utf-8'))
        self.assertEqual(meta['targets'], 1)
        self.assertEqual(len(meta['target_mtimes']), 1)
        self.assertEqual(meta['next_since'], 1100)

    # O18
    def test_cmd_apply_rejects_path_traversal_and_symlink_target(self):
        tmp = self._mkdtemp()
        memory = tmp / 'memory'
        memory.mkdir()
        outside = tmp / 'outside.md'
        outside.write_text('SAFE_OUTSIDE', encoding='utf-8')
        real_outside = tmp / 'real_outside.md'
        real_outside.write_text('SAFE_REAL', encoding='utf-8')
        (memory / 'sym.md').symlink_to(real_outside)

        fix_dir = tmp / 'fix' / 'sol'
        fix_dir.mkdir(parents=True)
        (fix_dir / 'trav.json').write_text(json.dumps({
            'note': '../outside.md', 'transcript': '/t', 'note_text': 'SAFE_OUTSIDE',
            'edits': [{'action': 'fix', 'old': 'SAFE_OUTSIDE', 'new': 'HACKED', 'why': 'w', 'valid': True}]}),
            encoding='utf-8')
        (fix_dir / 'sym.json').write_text(json.dumps({
            'note': 'sym.md', 'transcript': '/t', 'note_text': 'SAFE_REAL',
            'edits': [{'action': 'fix', 'old': 'SAFE_REAL', 'new': 'HACKED', 'why': 'w', 'valid': True}]}),
            encoding='utf-8')

        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False, 'agree_with': []}))
        finally:
            mo.MEMORY_ROOT = saved

        self.assertEqual(outside.read_text(encoding='utf-8'), 'SAFE_OUTSIDE')
        self.assertEqual(real_outside.read_text(encoding='utf-8'), 'SAFE_REAL')

    # O19
    def test_cmd_apply_preserves_mtime_nanoseconds(self):
        tmp = self._mkdtemp()
        memory = tmp / 'memory'
        memory.mkdir()
        note = memory / 'n.md'
        note.write_text('x 302 y', encoding='utf-8')
        original_ns = 1700000000 * 10**9 + 123456789
        os.utime(note, ns=(original_ns, original_ns))
        actual_ns = note.stat().st_mtime_ns
        self.assertEqual(actual_ns, original_ns)

        fix_dir = tmp / 'fix' / 'sol'
        fix_dir.mkdir(parents=True)
        (fix_dir / 'n.json').write_text(json.dumps({
            'note': 'n.md', 'transcript': '/t', 'note_text': 'x 302 y',
            'edits': [{'action': 'fix', 'old': '302', 'new': '303', 'why': 'w', 'valid': True}]}),
            encoding='utf-8')

        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, memory
        try:
            mo.cmd_apply(type('A', (), {'fix_dir': str(fix_dir), 'date': 'd', 'notes': None, 'dry_run': False, 'agree_with': []}))
        finally:
            mo.MEMORY_ROOT = saved

        self.assertEqual(note.stat().st_mtime_ns, original_ns)

    def test_sol_grafts_origin_frontmatter_nested_archive_ambiguous_index_and_link_escape(self):
        tmp = self._mkdtemp()
        mem = tmp / 'memory'
        (mem / 'nested' / 'archive').mkdir(parents=True)
        (mem / 'nested' / 'archive' / 'old.md').write_text('x', encoding='utf-8')
        (mem / 'nested' / 'keep.md').write_text('x', encoding='utf-8')
        self.assertEqual([p.name for p in mo.note_paths(mem)], ['keep.md'])

        uid = '12345678-abcd-1234-abcd-123456789012'
        self.assertIsNone(mo.origin_of(f'---\nname: x\n---\noriginSessionId: {uid}\n'))
        self.assertEqual(mo.origin_of(f'---\noriginSessionId: "{uid.upper()}"\n---\nbody\n'), uid)

        proj = tmp / 'projects'
        (proj / 'p1').mkdir(parents=True)
        (proj / 'p2').mkdir(parents=True)
        (proj / 'p1' / f'{uid}.jsonl').write_text('{}\n', encoding='utf-8')
        (proj / 'p2' / f'{uid}.jsonl').write_text('{}\n', encoding='utf-8')
        (proj / 'p1' / 'unique.jsonl').write_text('{}\n', encoding='utf-8')
        idx = mo.transcript_index(proj)
        self.assertNotIn(uid, idx)
        self.assertIn('unique', idx)

        run_dir = tmp / 'run'
        (run_dir / 'judge').mkdir(parents=True)
        rel = '有空格/[例]#1.md'
        (run_dir / 'judge' / 'item.json').write_text(json.dumps({
            'note': rel, 'transcript': '/t.jsonl',
            'verdict': {'verdict': 'mismatch', 'issues': [{'claim': 'c', 'actual': 'a'}]},
        }), encoding='utf-8')
        out_md = tmp / 'out.md'
        saved, mo.MEMORY_ROOT = mo.MEMORY_ROOT, mem
        try:
            mo.cmd_report(type('A', (), {'run_dir': str(run_dir), 'out': str(out_md), 'date': 'd', 'silent_if_empty': False}))
        finally:
            mo.MEMORY_ROOT = saved
        md = out_md.read_text(encoding='utf-8')
        self.assertIn('### [有空格/\\[例\\]#1.md](file://', md)
        self.assertIn('%E6%9C%89%E7%A9%BA%E6%A0%BC/%5B%E4%BE%8B%5D%231.md)', md)


if __name__ == '__main__':
    unittest.main()
