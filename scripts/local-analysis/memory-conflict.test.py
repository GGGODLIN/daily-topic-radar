import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('memory-conflict.py')
NOW = 1_800_000_000.0
OLD = NOW - 10 * 86400


def note(name, desc, body=''):
    return f'---\nname: {name}\ndescription: {desc}\n---\n\n{body}\n'


class MemoryConflictPrepare(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.root = base / 'memory'
        self.state = base / 'state'
        self.out = base / 'out'
        (self.root / 'general').mkdir(parents=True)
        files = {
            '_index_alpha.md': '- [n1](n1.md)\n- [n2](n2.md)\n- [n3](n3.md)\n',
            'MEMORY.md': '- [n2](n2.md)\n',
            'n1.md': note('n1', 'relay 分流 port 設定', '見 [[n4-slug]]'),
            'n2.md': note('n2', 'relay 分流 port 舊設定'),
            'n3.md': note('n3', '完全無關的主題'),
            'n4.md': note('n4-slug', '另一個主題'),
            'general/_INDEX.md': '- [g1](g1.md)\n',
            'general/g1.md': note('g1', '一般設定'),
        }
        for rel, text in files.items():
            p = self.root / rel
            p.write_text(text, encoding='utf-8')
            os.utime(p, (OLD, OLD))

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, rel, t):
        os.utime(self.root / rel, (t, t))

    def prepare(self, *extra):
        cmd = [sys.executable, str(SCRIPT), 'prepare', '--root', str(self.root), '--state', str(self.state),
               '--out-dir', str(self.out), '--now', str(NOW), *extra]
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(r.stdout)

    def manifest(self, batch):
        return json.loads(Path(batch['path']).read_text(encoding='utf-8'))

    def test_first_run_checks_only_recent_notes_with_linked_and_sibling_context(self):
        self.touch('n1.md', NOW - 3600)
        out = self.prepare()
        self.assertEqual(out['focus_count'], 1)
        m = self.manifest(out['batches'][0])
        self.assertEqual(m['focus'], [str(self.root / 'n1.md')])
        self.assertEqual({Path(p).name for p in m['context']}, {'n2.md', 'n3.md', 'n4.md'})
        self.assertEqual(out['cursor_candidate'], NOW)
        capped = self.manifest(self.prepare('--context-cap', '1')['batches'][0])
        self.assertEqual([Path(p).name for p in capped['context']], ['n4.md'], 'a linked note outranks cluster siblings')
        capped2 = self.manifest(self.prepare('--context-cap', '2')['batches'][0])
        self.assertEqual({Path(p).name for p in capped2['context']}, {'n4.md', 'n2.md'}, 'the closer sibling outranks the unrelated one')

    def test_oversized_sibling_is_skipped_instead_of_crowding_out_others(self):
        (self.root / 'n2.md').write_text(note('n2', 'relay 分流 port 舊設定', 'x' * 60_000), encoding='utf-8')
        os.utime(self.root / 'n2.md', (OLD, OLD))
        self.touch('n1.md', NOW - 3600)
        m = self.manifest(self.prepare('--context-bytes', '50000')['batches'][0])
        self.assertEqual({Path(p).name for p in m['context']}, {'n3.md', 'n4.md'})

    def test_index_files_are_never_focus(self):
        self.touch('_index_alpha.md', NOW - 60)
        self.touch('MEMORY.md', NOW - 60)
        out = self.prepare()
        self.assertEqual(out['focus_count'], 0)
        self.assertEqual(out['batches'], [])

    def test_cursor_limits_focus_to_notes_changed_after_it(self):
        self.state.mkdir()
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': NOW - 100}), encoding='utf-8')
        self.touch('n1.md', NOW - 200)
        self.touch('n2.md', NOW - 50)
        out = self.prepare()
        focus = [Path(p).name for b in out['batches'] for p in self.manifest(b)['focus']]
        self.assertEqual(focus, ['n2.md'])

    def test_capped_batches_hold_the_cursor_before_the_first_deferred_note(self):
        self.touch('n1.md', NOW - 300)
        self.touch('general/g1.md', NOW - 200)
        out = self.prepare('--batch-files', '1', '--context-cap', '0', '--max-batches', '1')
        self.assertEqual(len(out['batches']), 1)
        self.assertEqual(out['deferred_focus_count'], 1)
        kept = [Path(p).name for p in self.manifest(out['batches'][0])['focus']]
        self.assertEqual(kept, ['n1.md'])
        deferred_mtime = os.stat(self.root / 'general/g1.md').st_mtime
        self.assertLess(out['cursor_candidate'], deferred_mtime, 'the deferred note must still be newer than the next cursor')

    def test_scope_all_respects_exclude_list(self):
        ex = Path(self.tmp.name) / 'exclude.txt'
        ex.write_text('n1.md\nn2.md\n', encoding='utf-8')
        out = self.prepare('--scope', 'all', '--exclude-file', str(ex), '--context-cap', '0')
        focus = sorted(Path(p).name for b in out['batches'] for p in self.manifest(b)['focus'])
        self.assertEqual(focus, ['g1.md', 'n3.md', 'n4.md'])


class MemoryConflictCommitCursor(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / 'state'
        self.report = Path(self.tmp.name) / 'report.md'

    def tearDown(self):
        self.tmp.cleanup()

    def commit(self, epoch):
        cmd = [sys.executable, str(SCRIPT), 'commit-cursor', '--state', str(self.state), '--epoch', str(epoch),
               '--date', '2026-11-10', '--report', str(self.report)]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_missing_report_does_not_move_cursor(self):
        r = self.commit(NOW)
        self.assertEqual(r.returncode, 1)
        self.assertFalse((self.state / 'cursor.json').exists())

    def test_saved_report_moves_cursor_and_refuses_to_go_back(self):
        self.report.write_text('# report\n', encoding='utf-8')
        self.assertEqual(self.commit(NOW).returncode, 0)
        self.assertEqual(json.loads((self.state / 'cursor.json').read_text())['last_epoch'], NOW)
        self.assertEqual(self.commit(NOW - 1).returncode, 1)
        self.assertEqual(json.loads((self.state / 'cursor.json').read_text())['last_epoch'], NOW)


if __name__ == '__main__':
    unittest.main()
