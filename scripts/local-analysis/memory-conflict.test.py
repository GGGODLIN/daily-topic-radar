import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name('memory-conflict.py')
NOW = 1_800_000_000.0
OLD = NOW - 10 * 86400


def load_module():
    spec = importlib.util.spec_from_file_location('memory_conflict', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_cli(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


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
            'archive/old/a1.md': note('a1', '封存的舊筆記'),
            '.health-reports/r1.md': note('r1', '健檢報告'),
        }
        for rel, text in files.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
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

    def test_archive_and_dot_folders_are_never_checked(self):
        self.touch('archive/old/a1.md', NOW - 60)
        self.touch('.health-reports/r1.md', NOW - 60)
        self.assertEqual(self.prepare()['focus_count'], 0)
        out = self.prepare('--scope', 'all', '--context-cap', '0')
        focus = {Path(p).name for b in out['batches'] for p in self.manifest(b)['focus']}
        self.assertNotIn('a1.md', focus)
        self.assertNotIn('r1.md', focus)

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

    def test_capped_runs_take_the_oldest_notes_first_so_the_cursor_moves(self):
        # g1's cluster sorts after _index_alpha by name; capping in name order would defer it on every run.
        self.touch('general/g1.md', NOW - 300)
        self.touch('n1.md', NOW - 200)
        args = ('--batch-files', '1', '--context-cap', '0', '--max-batches', '1')
        first = self.prepare(*args)
        self.assertEqual([Path(p).name for p in self.manifest(first['batches'][0])['focus']], ['g1.md'])
        self.state.mkdir()
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': first['cursor_candidate']}), encoding='utf-8')
        second = self.prepare(*args)
        self.assertEqual([Path(p).name for p in self.manifest(second['batches'][0])['focus']], ['n1.md'])

    def test_same_cluster_newer_note_is_deferred_instead_of_rescanned(self):
        # n1 (-300) and n2 (-100) share _index_alpha; g1 (-200) is in general/.
        # Old cluster-first batching bundled [n1, n2] + [g1], scanned [n1, n2] on run 1, held cursor before g1 (-200), and rescanned n2 on run 2.
        self.touch('n1.md', NOW - 300)
        self.touch('general/g1.md', NOW - 200)
        self.touch('n2.md', NOW - 100)
        args = ('--batch-files', '2', '--context-cap', '0', '--max-batches', '1')
        r1 = self.prepare(*args)
        self.assertEqual(sorted(Path(p).name for p in self.manifest(r1['batches'][0])['focus']), ['g1.md', 'n1.md'])
        self.state.mkdir()
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': r1['cursor_candidate']}), encoding='utf-8')
        r2 = self.prepare(*args)
        self.assertEqual([Path(p).name for p in self.manifest(r2['batches'][0])['focus']], ['n2.md'])

    def test_scope_all_respects_exclude_list(self):
        ex = Path(self.tmp.name) / 'exclude.txt'
        ex.write_text('n1.md\nn2.md\n', encoding='utf-8')
        out = self.prepare('--scope', 'all', '--exclude-file', str(ex), '--context-cap', '0')
        focus = sorted(Path(p).name for b in out['batches'] for p in self.manifest(b)['focus'])
        self.assertEqual(focus, ['g1.md', 'n3.md', 'n4.md'])

    def test_max_batches_zero_and_negative_do_not_crash_or_drop(self):
        self.touch('n1.md', NOW - 200)
        out1 = self.prepare('--max-batches', '0')
        self.assertTrue(out1['ok'])
        self.assertEqual(out1['batches'], [])
        self.assertEqual(out1['focus_count'], 0)
        self.assertEqual(out1['deferred_focus_count'], 1)
        self.assertLess(out1['cursor_candidate'], os.stat(self.root / 'n1.md').st_mtime)

        self.touch('n2.md', NOW - 100)
        out2 = self.prepare('--max-batches', '0')
        self.assertTrue(out2['ok'])
        self.assertEqual(out2['batches'], [])
        self.assertEqual(out2['focus_count'], 0)
        self.assertEqual(out2['deferred_focus_count'], 2)

        out3 = self.prepare('--scope', 'all', '--max-batches', '-1')
        self.assertTrue(out3['ok'])
        self.assertEqual(out3['batches'], [])
        self.assertEqual(out3['focus_count'], 0)
        self.assertEqual(out3['deferred_focus_count'], 5)

    def test_same_mtime_notes_exceeding_max_batches_advance_cursor(self):
        for rel_p in ('n1.md', 'n2.md', 'n3.md'):
            self.touch(rel_p, NOW - 200)
        self.touch('general/g1.md', NOW - 100)
        args = ('--batch-files', '1', '--context-cap', '0', '--max-batches', '1')
        first = self.prepare(*args)
        kept = {Path(p).name for b in first['batches'] for p in self.manifest(b)['focus']}
        self.assertEqual(kept, {'n1.md', 'n2.md', 'n3.md'})
        self.assertEqual(first['deferred_focus_count'], 1)
        self.assertGreater(first['cursor_candidate'], NOW - 200)
        self.assertLess(first['cursor_candidate'], os.stat(self.root / 'general/g1.md').st_mtime)

        self.state.mkdir(exist_ok=True)
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': first['cursor_candidate']}), encoding='utf-8')
        second = self.prepare(*args)
        second_kept = [Path(p).name for b in second['batches'] for p in self.manifest(b)['focus']]
        self.assertEqual(second_kept, ['g1.md'])

    def test_sub_microsecond_mtime_gap_does_not_move_cursor_backwards(self):
        base_ns = int((NOW - 200) * 10**9)
        cursor = (base_ns - 250) / 1e9
        self.state.mkdir(exist_ok=True)
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': cursor}), encoding='utf-8')
        os.utime(self.root / 'n1.md', ns=(base_ns, base_ns))
        os.utime(self.root / 'general/g1.md', ns=(base_ns + 500, base_ns + 500))
        n1_mtime = os.stat(self.root / 'n1.md').st_mtime
        g1_mtime = os.stat(self.root / 'general/g1.md').st_mtime
        self.assertGreater(n1_mtime, cursor)
        self.assertGreater(g1_mtime, n1_mtime)

        out = self.prepare('--batch-files', '1', '--context-cap', '0', '--max-batches', '1')
        self.assertEqual(out['deferred_focus_count'], 1)
        self.assertGreaterEqual(out['cursor_candidate'], cursor)
        self.assertLess(out['cursor_candidate'], g1_mtime)

        report = Path(self.tmp.name) / 'report.md'
        report.write_text('# report\n', encoding='utf-8')
        rc = run_cli('commit-cursor', '--state', str(self.state), '--epoch', str(out['cursor_candidate']),
                     '--date', '2026-11-10', '--report', str(report))
        self.assertEqual(rc.returncode, 0, rc.stdout + rc.stderr)

    def test_prepare_cleans_stale_batches_in_reused_out_dir(self):
        out1 = self.prepare('--scope', 'all', '--batch-files', '1', '--context-cap', '0')
        self.assertGreater(len(out1['batches']), 1)
        self.assertGreater(len(list(self.out.glob('batch-*.json'))), 1)

        out2 = self.prepare()
        self.assertEqual(out2['batches'], [])
        self.assertEqual(list(self.out.glob('batch-*.json')), [])

        self.prepare('--scope', 'all', '--batch-files', '1', '--context-cap', '0')
        self.touch('n1.md', NOW - 60)
        out3 = self.prepare()
        self.assertEqual(len(out3['batches']), 1)
        self.assertEqual(sorted(p.name for p in self.out.glob('batch-*.json')), ['batch-B01.json'])

    def test_nan_and_inf_rejected_in_epoch_now_and_cursor(self):
        report = Path(self.tmp.name) / 'report.md'
        report.write_text('# report\n', encoding='utf-8')
        self.state.mkdir(exist_ok=True)
        (self.state / 'cursor.json').write_text(json.dumps({'last_epoch': NOW - 500}), encoding='utf-8')

        for bad in ('nan', 'inf', '-inf'):
            rc = run_cli('commit-cursor', '--state', str(self.state), '--epoch', bad,
                         '--date', '2026-11-10', '--report', str(report))
            self.assertEqual(rc.returncode, 1)
            self.assertNotIn('Traceback', rc.stderr)
            dc = json.loads(rc.stdout)
            self.assertFalse(dc['ok'])
            self.assertEqual(dc['reason'], 'invalid-epoch')
            self.assertEqual(json.loads((self.state / 'cursor.json').read_text())['last_epoch'], NOW - 500)

            rp = run_cli('prepare', '--root', str(self.root), '--state', str(self.state),
                         '--out-dir', str(self.out), '--now', bad)
            self.assertEqual(rp.returncode, 1)
            self.assertNotIn('Traceback', rp.stderr)
            dp = json.loads(rp.stdout)
            self.assertFalse(dp['ok'])
            self.assertEqual(dp['reason'], 'invalid-now')

        for raw in ('{"last_epoch": NaN}', '{"last_epoch": Infinity}'):
            (self.state / 'cursor.json').write_text(raw, encoding='utf-8')
            rp = run_cli('prepare', '--root', str(self.root), '--state', str(self.state),
                         '--out-dir', str(self.out), '--now', str(NOW))
            self.assertEqual(rp.returncode, 1)
            self.assertEqual(json.loads(rp.stdout)['reason'], 'invalid-cursor')

            rc = run_cli('commit-cursor', '--state', str(self.state), '--epoch', str(NOW),
                         '--date', '2026-11-10', '--report', str(report))
            self.assertEqual(rc.returncode, 1)
            self.assertEqual(json.loads(rc.stdout)['reason'], 'invalid-cursor')

    def test_corrupted_or_boolean_cursor_returns_structured_error(self):
        report = Path(self.tmp.name) / 'report.md'
        report.write_text('# report\n', encoding='utf-8')
        self.state.mkdir(exist_ok=True)
        for raw in ('{bad json', '[]', '{}', '{"last_epoch": true}'):
            (self.state / 'cursor.json').write_text(raw, encoding='utf-8')
            rp = run_cli('prepare', '--root', str(self.root), '--state', str(self.state),
                         '--out-dir', str(self.out), '--now', str(NOW))
            self.assertEqual(rp.returncode, 1)
            self.assertNotIn('Traceback', rp.stderr)
            dp = json.loads(rp.stdout)
            self.assertFalse(dp['ok'])
            self.assertEqual(dp['reason'], 'invalid-cursor')

            rc = run_cli('commit-cursor', '--state', str(self.state), '--epoch', str(NOW),
                         '--date', '2026-11-10', '--report', str(report))
            self.assertEqual(rc.returncode, 1)
            self.assertNotIn('Traceback', rc.stderr)
            dc = json.loads(rc.stdout)
            self.assertFalse(dc['ok'])
            self.assertEqual(dc['reason'], 'invalid-cursor')

    def test_missing_root_and_missing_exclude_file_return_structured_error(self):
        missing_root = Path(self.tmp.name) / 'does-not-exist'
        rp = run_cli('prepare', '--root', str(missing_root), '--state', str(self.state),
                     '--out-dir', str(self.out), '--now', str(NOW))
        self.assertEqual(rp.returncode, 1)
        self.assertNotIn('Traceback', rp.stderr)
        dp = json.loads(rp.stdout)
        self.assertFalse(dp['ok'])
        self.assertEqual(dp['reason'], 'root-not-found')

        missing_ex = Path(self.tmp.name) / 'missing-exclude.txt'
        re_ = run_cli('prepare', '--root', str(self.root), '--state', str(self.state),
                      '--out-dir', str(self.out), '--now', str(NOW), '--exclude-file', str(missing_ex))
        self.assertEqual(re_.returncode, 1)
        self.assertNotIn('Traceback', re_.stderr)
        de = json.loads(re_.stdout)
        self.assertFalse(de['ok'])
        self.assertEqual(de['reason'], 'exclude-file-missing')

    def test_frontmatter_name_and_desc_do_not_leak_from_body(self):
        mod = load_module()
        no_fm = 'plain body\nname: leaked-slug\ndescription: relay port config\n'
        empty_fm = '---\ntags: [demo]\n---\n\nname: leaked-slug-2\ndescription: relay port config\n'
        real_fm = note('real-slug', 'relay port config', 'body text')
        p1 = self.root / 'no_fm.md'
        p2 = self.root / 'empty_fm.md'
        p3 = self.root / 'real_fm.md'
        texts = {'no_fm.md': no_fm, 'empty_fm.md': empty_fm, 'real_fm.md': real_fm}
        by_slug = mod.slug_map(self.root, [p1, p2, p3], texts)
        self.assertNotIn('leaked-slug', by_slug)
        self.assertNotIn('leaked-slug-2', by_slug)
        self.assertEqual(by_slug.get('real-slug'), 'real_fm.md')
        self.assertEqual(mod.tokens(no_fm), set())
        self.assertEqual(mod.tokens(empty_fm), set())
        self.assertIn('relay', mod.tokens(real_fm))
        self.assertIn('port', mod.tokens(real_fm))

    def test_markdown_link_with_fragment_anchor_is_included(self):
        (self.root / 'n3.md').write_text(note('n3', '完全無關的主題', '見 [see section](n4.md#details)'), encoding='utf-8')
        self.touch('n3.md', NOW - 60)
        m = self.manifest(self.prepare('--context-cap', '1')['batches'][0])
        self.assertEqual([Path(p).name for p in m['context']], ['n4.md'])

    def test_symlinked_root_and_symlinked_note_file(self):
        mod = load_module()
        link_root = Path(self.tmp.name) / 'memory_link'
        link_root.symlink_to(self.root)
        g = mod.build_graph(link_root)
        self.assertEqual(g['clusters']['_index_alpha.md'], ['n1.md', 'n2.md', 'n3.md'])
        self.assertIn('_index_alpha.md', g['member_of']['n1.md'])

        outside = Path(self.tmp.name) / 'outside.md'
        outside.write_text(note('outside', '外部筆記'), encoding='utf-8')
        (self.root / 'escape.md').symlink_to(outside)
        notes, _ = mod.scan_tree(self.root)
        self.assertNotIn('escape.md', [p.name for p in notes])
        out = self.prepare('--scope', 'all', '--context-cap', '0')
        focus = {Path(p).name for b in out['batches'] for p in self.manifest(b)['focus']}
        self.assertNotIn('escape.md', focus)

    def test_unreadable_note_file_is_skipped_in_build_graph(self):
        if os.geteuid() == 0:
            self.skipTest('root ignores chmod 000')
        bad = self.root / 'unreadable.md'
        bad.write_text(note('unreadable', '無權限筆記'), encoding='utf-8')
        os.utime(bad, (OLD, OLD))
        os.chmod(bad, 0o000)
        try:
            out = self.prepare('--scope', 'all', '--context-cap', '0')
            self.assertTrue(out['ok'])
            focus = {Path(p).name for b in out['batches'] for p in self.manifest(b)['focus']}
            self.assertNotIn('unreadable.md', focus)
        finally:
            os.chmod(bad, 0o644)

    def test_exclude_file_normalizes_paths_and_case_insensitive_fs(self):
        ex = Path(self.tmp.name) / 'exclude.txt'
        ex.write_text(f'./n1.md\ngeneral/../n2.md\n{self.root / "n3.md"}\n', encoding='utf-8')
        out = self.prepare('--scope', 'all', '--exclude-file', str(ex), '--context-cap', '0')
        focus = sorted(Path(p).name for b in out['batches'] for p in self.manifest(b)['focus'])
        self.assertEqual(focus, ['g1.md', 'n4.md'])

        if (self.root / 'N4.MD').exists():
            ex_ci = Path(self.tmp.name) / 'exclude_ci.txt'
            ex_ci.write_text('N1.MD\n', encoding='utf-8')
            out_ci = self.prepare('--scope', 'all', '--exclude-file', str(ex_ci), '--context-cap', '0')
            focus_ci = {Path(p).name for b in out_ci['batches'] for p in self.manifest(b)['focus']}
            self.assertNotIn('n1.md', focus_ci)

            (self.root / 'n3.md').write_text(note('n3', '完全無關的主題', '見 [upper](N4.MD)'), encoding='utf-8')
            self.touch('n3.md', NOW - 60)
            m2 = self.manifest(self.prepare('--context-cap', '1')['batches'][0])
            self.assertEqual([Path(p).name for p in m2['context']], ['n4.md'])


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

    def test_concurrent_commit_cursor_uses_lock_and_unique_tmp(self):
        self.report.write_text('# report\n', encoding='utf-8')
        worker_code = (
            "import importlib.util, io, json, sys, time\n"
            "from contextlib import redirect_stdout\n"
            "spec = importlib.util.spec_from_file_location('mc', sys.argv[1])\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(mod)\n"
            "state, rep, base_epoch, start_at = sys.argv[2], sys.argv[3], float(sys.argv[4]), float(sys.argv[5])\n"
            "idx = int(sys.argv[6])\n"
            "delay = start_at - time.monotonic()\n"
            "if delay > 0:\n"
            "    time.sleep(delay)\n"
            "results = []\n"
            "for r in range(5):\n"
            "    ep = base_epoch + r * 10 + idx\n"
            "    buf = io.StringIO()\n"
            "    with redirect_stdout(buf):\n"
            "        rc = mod.main(['commit-cursor', '--state', state, '--epoch', str(ep),\n"
            "                       '--date', '2026-11-10', '--report', rep])\n"
            "    results.append((ep, rc, buf.getvalue().strip()))\n"
            "print(json.dumps(results))\n"
        )
        start_at = time.monotonic() + 0.12
        procs = [
            subprocess.Popen(
                [sys.executable, '-c', worker_code, str(SCRIPT), str(self.state),
                 str(self.report), str(NOW), str(start_at), str(i)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            for i in range(1, 7)
        ]
        committed = []
        for p in procs:
            out, err = p.communicate(timeout=15)
            self.assertEqual(p.returncode, 0, f'worker crashed: {err}')
            for ep, rc, line in json.loads(out):
                data = json.loads(line)
                if rc == 0:
                    self.assertTrue(data['ok'])
                    committed.append(ep)
                else:
                    self.assertEqual(rc, 1)
                    self.assertFalse(data['ok'])
                    self.assertEqual(data['reason'], 'cursor-would-move-backwards')

        self.assertIn(NOW + 46, committed)
        final_data = json.loads((self.state / 'cursor.json').read_text(encoding='utf-8'))
        self.assertEqual(final_data['last_epoch'], max(committed))
        self.assertEqual(final_data['last_epoch'], NOW + 46)

    def test_sol_grafts_encoded_links_ambiguous_slug_and_first_focus_cap(self):
        mc = load_module()
        mem = self.state.parent / 'sol-mem'
        def write_note(rel_path, body):
            p = mem / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding='utf-8')
        write_note('two words.md', '---\nname: spaced\n---\nbody\n')
        write_note('a/dup.md', '---\nname: dup\n---\nbody\n')
        write_note('b/dup.md', '---\nname: dup\n---\nbody\n')
        write_note('src.md', '---\nname: src\n---\n[t](two%20words.md) [[dup]]\n')
        g = mc.build_graph(mem)
        self.assertIn('two words.md', g['out']['src.md'])
        self.assertNotIn('a/dup.md', g['out']['src.md'])
        self.assertNotIn('b/dup.md', g['out']['src.md'])

        # First focus respects batch_files cap
        for i in range(4):
            write_note(f'nb{i}.md', f'---\nname: nb{i}\n---\nbody\n')
        write_note('hub.md', '---\nname: hub\n---\n' + ' '.join(f'[n](nb{i}.md)' for i in range(4)) + '\n')
        g2 = mc.build_graph(mem)
        batches = mc.make_batches(g2, ['hub.md'], context_cap=6, context_bytes=100000, batch_bytes=100000, batch_files=2)
        self.assertEqual(len(batches), 1)
        self.assertLessEqual(len(batches[0]['focus']) + len(batches[0]['context']), 2)


if __name__ == '__main__':
    unittest.main()
