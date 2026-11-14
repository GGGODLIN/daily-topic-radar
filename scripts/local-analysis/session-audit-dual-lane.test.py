#!/usr/bin/env python3
"""run/status 公共入口：非對稱雙軌（eco/quality）、前景讓路與 15 發/10m 限速、402 停腿及 P2/P3 屬性測試。"""

import importlib.util
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SLIM_SCRIPT = Path(__file__).with_name('session-slim.py')

spec = importlib.util.spec_from_file_location('dual_lane_fixtures', Path(__file__).with_name('session-audit-population.test.py'))
population = importlib.util.module_from_spec(spec)
spec.loader.exec_module(population)
fixtures = population.fixtures


class ConcurrencyHandler(BaseHTTPRequestHandler):
  protocol_version = 'HTTP/1.1'

  def log_message(self, *_args):
    return

  def do_GET(self):
    payload, code, delay = self.server.get_state()
    if delay > 0:
      time.sleep(delay)
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    try:
      self.send_response(code)
      self.send_header('Content-Type', 'application/json')
      self.send_header('Content-Length', str(len(data)))
      self.end_headers()
      self.wfile.write(data)
    except OSError:
      return


def oracle_p2_primary_isolated(before_snap, after_snap):
  """Independent Oracle P2: shadow steps never mutate primary sources.doc_offset, sources.continuity, or rule_tags."""
  before_primary = {
    row['inode']: (row['doc_offset'], row['continuity'])
    for row in before_snap['sources']
  }
  after_primary = {
    row['inode']: (row['doc_offset'], row['continuity'])
    for row in after_snap['sources']
  }
  if before_primary != after_primary:
    return False, f'sources primary cursor mutated on shadow step: {before_primary} -> {after_primary}'
  if before_snap['rule_tags'] != after_snap['rule_tags']:
    return False, f'rule_tags mutated on shadow step: {len(before_snap["rule_tags"])} -> {len(after_snap["rule_tags"])}'
  return True, 'ok'


def oracle_p3_yield_no_false_complete(before_snap, after_snap, incomplete_inodes, yielded):
  """Independent Oracle P3: yielding on unexhausted sources never marks them complete or failed."""
  if not yielded:
    return True, 'no-yield'
  for row in after_snap['sources']:
    if row['inode'] in incomplete_inodes:
      if row['status'] in ('complete', 'failed'):
        return False, f'unexhausted source {row["inode"]} marked {row["status"]} during yield'
      if row['latest_complete']:
        return False, f'unexhausted source {row["inode"]} marked latest_complete=1 during yield'
  return True, 'ok'


class DualLaneBaseTests(population.PopulationTests):
  def setUp(self):
    super().setUp()
    self.bg_mode_file = self.root / 'bg-mode.json'
    self.concurrency_state = {'payload': {'active': []}, 'code': 200, 'delay': 0.0}
    self.concurrency_lock = threading.Lock()
    self.concurrency_server = ThreadingHTTPServer(('127.0.0.1', 0), ConcurrencyHandler)
    self.concurrency_server.get_state = self._get_concurrency_state
    threading.Thread(target=self.concurrency_server.serve_forever, daemon=True).start()
    self.addCleanup(self.concurrency_server.server_close)
    self.addCleanup(self.concurrency_server.shutdown)
    self.concurrency_url = f'http://127.0.0.1:{self.concurrency_server.server_address[1]}/v1/magpie/concurrency'

  def _get_concurrency_state(self):
    with self.concurrency_lock:
      return (
        self.concurrency_state['payload'],
        self.concurrency_state['code'],
        self.concurrency_state['delay'],
      )

  def set_concurrency(self, active=None, code=200, delay=0.0):
    with self.concurrency_lock:
      self.concurrency_state['payload'] = {'active': active if active is not None else []}
      self.concurrency_state['code'] = code
      self.concurrency_state['delay'] = delay

  def set_bg_mode(self, mode):
    self.bg_mode_file.write_text(json.dumps({'mode': mode}))

  def dual_flags(self, url, direct='bg-audit-primary:2', shadow='bg-audit-shadow:2', standby='', concurrency_url=None, cap_10m=15, extra=()):
    args = [
      *self.flags(url),
      '--direct-legs', direct,
      '--shadow-legs', shadow,
      '--bg-mode-file', str(self.bg_mode_file),
      '--concurrency-url', self.concurrency_url if concurrency_url is None else concurrency_url,
      '--hydrogen-cap-10m', str(cap_10m),
      '--max-sessions', '2',
    ]
    if standby:
      args.extend(['--standby-legs', standby])
    if extra:
      args.extend(extra)
    return args

  def db_query(self, sql, params=()):
    with sqlite3.connect(self.state / 'state.sqlite') as conn:
      conn.row_factory = sqlite3.Row
      return [dict(row) for row in conn.execute(sql, params).fetchall()]

  def db_tables(self):
    with sqlite3.connect(self.state / 'state.sqlite') as conn:
      return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

  def state_snapshot(self):
    db_path = self.state / 'state.sqlite'
    if not db_path.exists():
      return {'sources': [], 'rule_tags': [], 'shadow_cursors': [], 'shadow_replies': [], 'findings': []}
    with sqlite3.connect(db_path) as conn:
      conn.row_factory = sqlite3.Row
      tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
      sources = [
        {
          'inode': r['inode'],
          'doc_offset': r['doc_offset'],
          'continuity': r['continuity'],
          'status': r['status'],
          'latest_complete': bool(r['latest_complete']),
          'generation': r['generation'],
        }
        for r in conn.execute('SELECT inode, doc_offset, continuity, status, latest_complete, generation FROM sources ORDER BY inode').fetchall()
      ] if 'sources' in tables else []
      rule_tags = [
        (r['inode'], r['generation'], r['rule_id'], r['verdict'], r['source_line'], r['quote'])
        for r in conn.execute('SELECT inode, generation, rule_id, verdict, source_line, quote FROM rule_tags ORDER BY id').fetchall()
      ] if 'rule_tags' in tables else []
      shadow_cursors = [
        dict(r) for r in conn.execute('SELECT * FROM shadow_cursors ORDER BY inode, generation, lane').fetchall()
      ] if 'shadow_cursors' in tables else []
      shadow_replies = [
        dict(r) for r in conn.execute('SELECT * FROM shadow_replies ORDER BY inode, generation, lane, doc_start').fetchall()
      ] if 'shadow_replies' in tables else []
      findings = [
        dict(r) for r in conn.execute('SELECT * FROM findings ORDER BY id').fetchall()
      ] if 'findings' in tables else []
      return {
        'sources': sources,
        'rule_tags': rule_tags,
        'shadow_cursors': shadow_cursors,
        'shadow_replies': shadow_replies,
        'findings': findings,
      }


class ConcreteCapabilityExamples(DualLaneBaseTests):
  """Role 1: Deterministic acceptance examples for dual-lane, legacy migration, yield, fail-open, and HTTP 402."""

  def test_eco_mode_runs_primary_only_and_skips_shadow(self):
    self.set_bg_mode('eco')
    self.source('work/eco.jsonl', 'ECO_MARKER')
    seen = []

    def reply(body):
      data = json.loads(body)
      seen.append(data['model'])
      label = fixtures.label_for(body, 'BASE_RULE_FALLBACK')
      tags = [{'rule': label, 'verdict': 'violated', 'source_line': 1, 'quote': 'ECO_MARKER'}] if label else []
      return fixtures.analysis([fixtures.finding('ECO_MARKER', observation='eco-primary')], rule_tags=tags), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.dual_flags(url)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen, ['bg-audit-primary'])
    snap = self.state_snapshot()
    self.assertEqual(snap['shadow_cursors'], [])
    self.assertEqual(snap['shadow_replies'], [])
    self.assertEqual([f['lane'] for f in snap['findings']], ['primary'])
    self.assertEqual(len(snap['rule_tags']), 1)

  def test_quality_mode_runs_both_lanes_and_isolates_shadow_writes(self):
    self.set_bg_mode('quality')
    self.source('work/qual.jsonl', 'QUAL_MARKER')
    requests = []

    def reply(body):
      data = json.loads(body)
      model = data['model']
      requests.append((model, fixtures.system_text(body)))
      if model == 'bg-audit-primary':
        label = fixtures.label_for(body, 'BASE_RULE_FALLBACK')
        tags = [{'rule': label, 'verdict': 'violated', 'source_line': 1, 'quote': 'QUAL_MARKER'}] if label else []
        return fixtures.analysis([fixtures.finding('QUAL_MARKER', observation='primary-obs')], rule_tags=tags), 200
      return fixtures.analysis([fixtures.finding('QUAL_MARKER', observation='shadow-obs')]), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.dual_flags(url)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    models = [m for m, _ in requests]
    self.assertEqual(set(models), {'bg-audit-primary', 'bg-audit-shadow'})
    for m, sys_txt in requests:
      if m == 'bg-audit-shadow':
        self.assertNotIn('<rules>', sys_txt, 'Shadow requests must be sent with with_rules=False')
    snap = self.state_snapshot()
    self.assertEqual([s['status'] for s in snap['sources']], ['complete'])
    self.assertEqual(len(snap['shadow_cursors']), 1)
    self.assertEqual(snap['shadow_cursors'][0]['lane'], 'bg-audit-shadow')
    self.assertEqual(snap['shadow_cursors'][0]['status'], 'complete')
    self.assertGreater(snap['shadow_cursors'][0]['doc_offset'], 0)
    self.assertEqual(len(snap['shadow_replies']), 1)
    self.assertEqual(snap['shadow_replies'][0]['lane'], 'bg-audit-shadow')
    by_lane = {f['lane']: f['observation'] for f in snap['findings']}
    self.assertEqual(by_lane, {'primary': 'primary-obs', 'bg-audit-shadow': 'shadow-obs'})
    self.assertEqual(len(snap['rule_tags']), 1)

  def test_legacy_sqlite_upgrades_with_lane_default_primary_and_shadow_tables(self):
    self.set_bg_mode('quality')
    path = self.source('work/legacy.jsonl', 'LEGACY_MARKER')
    _, url = self.provider(lambda body: (fixtures.analysis([fixtures.finding('LEGACY_MARKER', observation='after-upgrade')]), 200))
    # Initialize DB using scan first, then downgrade schema to pre-dual-lane shape with historical findings.
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    with sqlite3.connect(self.state / 'state.sqlite') as conn:
      inode = conn.execute('SELECT inode FROM sources').fetchone()[0]
      conn.executescript(
        """
        DROP TABLE IF EXISTS shadow_cursors;
        DROP TABLE IF EXISTS shadow_replies;
        DROP TABLE IF EXISTS hydrogen_sends;
        CREATE TABLE findings_legacy (
          id INTEGER PRIMARY KEY,
          inode TEXT NOT NULL,
          generation INTEGER NOT NULL,
          status TEXT NOT NULL,
          independent_recurrence INTEGER NOT NULL,
          target TEXT NOT NULL,
          observation TEXT NOT NULL,
          check_direction TEXT NOT NULL,
          quote TEXT NOT NULL,
          source_line INTEGER NOT NULL,
          issue_ref TEXT NOT NULL DEFAULT ''
        );
        DROP TABLE findings;
        ALTER TABLE findings_legacy RENAME TO findings;
        """
      )
      conn.execute(
        "INSERT INTO findings (inode, generation, status, independent_recurrence, target, observation, check_direction, quote, source_line, issue_ref) "
        "VALUES (?, 1, 'unresolved', 0, 'unknown', 'hist-obs', 'chk', 'LEGACY_MARKER', 1, 'hist-ref-1')",
        (inode,),
      )
      conn.execute("UPDATE sources SET status = 'complete', latest_complete = 1, file_size = ?, doc_offset = 100 WHERE inode = ?", (path.stat().st_size, inode))
      conn.commit()
    # Read-only status before write migration must succeed with zero errors.
    pre = self.status(url)
    self.assertEqual([c['observation'] for c in pre['candidates']], ['hist-obs'])
    # Run in quality mode upgrades schema and runs shadow on the already-complete source.
    done = self.cli(['run', *self.dual_flags(url)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    tables = self.db_tables()
    self.assertIn('shadow_cursors', tables)
    self.assertIn('shadow_replies', tables)
    snap = self.state_snapshot()
    hist = next(f for f in snap['findings'] if f['observation'] == 'hist-obs')
    self.assertEqual(hist['lane'], 'primary')
    shadow = [f for f in snap['findings'] if f['lane'] == 'bg-audit-shadow']
    self.assertEqual(len(shadow), 1)
    self.assertEqual(shadow[0]['observation'], 'after-upgrade')
    # Read-only status after upgrade shows both without error.
    post = self.status(url)
    self.assertEqual(sorted(c['observation'] for c in post['candidates']), ['after-upgrade', 'hist-obs'])

  def test_foreground_concurrency_yields_primary_while_shadow_proceeds_then_resumes_when_idle(self):
    self.set_bg_mode('quality')
    self.source('work/yield.jsonl', 'YIELD_WORK')
    seen = []

    def reply(body):
      model = json.loads(body)['model']
      seen.append(model)
      return fixtures.analysis([fixtures.finding('YIELD_WORK', observation=f'obs-{model}')]), 200

    _, url = self.provider(reply)
    # Active foreground Google Gemini request -> primary yields, shadow continues!
    self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}])
    done = self.cli(['run', *self.dual_flags(url)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen, ['bg-audit-shadow'])
    snap = self.state_snapshot()
    self.assertEqual(snap['sources'][0]['status'], 'pending')
    self.assertEqual(snap['sources'][0]['doc_offset'], 0)
    self.assertEqual(snap['shadow_cursors'][0]['status'], 'complete')
    self.assertEqual(self.db_query('SELECT alias FROM leg_pauses'), [])

    # Background / antigravity entries do NOT trigger yield -> primary resumes and completes.
    seen.clear()
    self.set_concurrency(active=[
      {'agent': 'session-audit', 'model': 'gemini-4-hydrogen'},
      {'agent': 'claude-code', 'model': 'antigravity/gemini-pro'},
      {'agent': 'claude-code', 'model': 'bg-audit-primary'},
    ])
    done2 = self.cli(['run', *self.dual_flags(url)], timeout=30)
    self.assertEqual(done2.returncode, 0, done2.stderr)
    self.assertEqual(seen, ['bg-audit-primary'])
    snap2 = self.state_snapshot()
    self.assertEqual(snap2['sources'][0]['status'], 'complete')
    self.assertTrue(snap2['sources'][0]['latest_complete'])

  def test_hydrogen_10m_cap_yields_primary_without_false_complete_while_shadow_continues(self):
    self.set_bg_mode('quality')
    for idx in range(3):
      self.source(f'work/cap-{idx}.jsonl', f'CAP_WORK_{idx}')
    seen = []

    def reply(body):
      model = json.loads(body)['model']
      seen.append(model)
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.dual_flags(url, cap_10m=2)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen.count('bg-audit-primary'), 2)
    self.assertEqual(seen.count('bg-audit-shadow'), 3)
    snap = self.state_snapshot()
    statuses = sorted(s['status'] for s in snap['sources'])
    self.assertEqual(statuses, ['complete', 'complete', 'pending'])
    self.assertTrue(all(sc['status'] == 'complete' for sc in snap['shadow_cursors']))

  def test_concurrency_endpoint_timeout_and_error_fail_open_for_primary(self):
    self.set_bg_mode('eco')
    self.source('work/failopen.jsonl', 'FAILOPEN_WORK')
    seen = []

    def reply(body):
      seen.append(json.loads(body)['model'])
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    # 1. Delay longer than 2.5s timeout -> fail-open
    self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}], delay=3.2)
    done = self.cli(['run', *self.dual_flags(url, shadow='')], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen, ['bg-audit-primary'])
    self.assertEqual(self.state_snapshot()['sources'][0]['status'], 'complete')

    # 2. HTTP 500 from concurrency endpoint -> fail-open
    seen.clear()
    self.source('work/failopen2.jsonl', 'FAILOPEN_WORK_2')
    self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}], code=500, delay=0.0)
    done2 = self.cli(['run', *self.dual_flags(url, shadow='')], timeout=30)
    self.assertEqual(done2.returncode, 0, done2.stderr)
    self.assertEqual(seen, ['bg-audit-primary'])

  def test_http_402_immediately_pauses_primary_and_shadow_legs(self):
    self.set_bg_mode('quality')
    self.source('work/p402.jsonl', 'HTTP_402_WORK')
    seen = []

    def reply(body):
      model = json.loads(body)['model']
      seen.append(model)
      if model in ('bg-audit-primary', 'bg-audit-shadow'):
        return '{"error": "quota expired"}', 402
      return fixtures.analysis([], rule_tags=[]), 200

    _, url = self.provider(reply)
    done = self.cli(['run', *self.dual_flags(url, standby='audit-tail:10')], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)
    self.assertEqual(seen.count('bg-audit-primary'), 1, 'Primary must pause on first 402 and fail over to standby')
    self.assertEqual(seen.count('bg-audit-shadow'), 1, 'Shadow must pause immediately on first 402 instead of waiting 30 failures')
    self.assertIn('audit-tail', seen)
    paused = {r['alias'] for r in self.db_query('SELECT alias FROM leg_pauses')}
    self.assertEqual(paused, {'bg-audit-primary', 'bg-audit-shadow'})
    snap = self.state_snapshot()
    self.assertEqual(snap['sources'][0]['status'], 'complete')
    self.assertEqual(snap['shadow_cursors'], [])

    # Pre-seed a partial shadow_cursor, clear leg_pauses, and confirm 402 leaves it pending (not failed)
    inode = snap['sources'][0]['inode']
    with sqlite3.connect(self.state / 'state.sqlite') as conn:
      conn.execute(
        "INSERT INTO shadow_cursors VALUES (?, 1, 'bg-audit-shadow', 10, 'ctx', 'pending', 0)",
        (inode,),
      )
      conn.execute("DELETE FROM leg_pauses")
      conn.commit()
    done2 = self.cli(['run', *self.dual_flags(url, standby='audit-tail:10')], timeout=30)
    self.assertEqual(done2.returncode, 0, done2.stderr)
    snap2 = self.state_snapshot()
    self.assertEqual(len(snap2['shadow_cursors']), 1)
    self.assertEqual(snap2['shadow_cursors'][0]['status'], 'pending')

  def test_shadow_started_at_cutoff_skips_historical_complete_and_slim_rebases_shadow_offsets(self):
    self.set_bg_mode('quality')
    old_path = self.source('work/old-oct.jsonl', 'OLD_OCT_SESSION')
    new_path = self.source('work/new-nov.jsonl', 'NEW_NOV_SESSION')
    os.utime(old_path, (1760000000, 1760000000))  # < 2026-11-15T00:00:00Z
    seen = []

    def reply(body):
      data = json.loads(body)
      seen.append((data['model'], fixtures.user_text(body)))
      return fixtures.analysis([fixtures.finding('NEW_NOV_SESSION', observation='nov-obs')]), 200

    _, url = self.provider(reply)
    # Mark old_path complete in DB (simulating 12k historical complete sessions since 2026-10-05)
    self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
    with sqlite3.connect(self.state / 'state.sqlite') as conn:
      conn.execute("UPDATE sources SET status = 'complete', latest_complete = 1 WHERE path LIKE '%old-oct.jsonl'")
      conn.commit()

    done = self.cli(
      ['run', *self.dual_flags(url), '--shadow-started-at', '2026-11-15T00:00:00+08:00'],
      timeout=30,
    )
    self.assertEqual(done.returncode, 0, done.stderr)
    shadow_texts = [txt for model, txt in seen if model == 'bg-audit-shadow']
    self.assertEqual(len(shadow_texts), 1)
    self.assertIn('NEW_NOV_SESSION', shadow_texts[0])
    self.assertFalse(any('OLD_OCT_SESSION' in txt for _, txt in seen))

    # Verify session-slim rebases shadow_cursors and shadow_replies without error
    slim_res = subprocess.run(
      [sys.executable, str(SLIM_SCRIPT), str(new_path), '--state', str(self.state), '--no-compress'],
      capture_output=True, text=True, timeout=15,
    )
    self.assertEqual(slim_res.returncode, 0, slim_res.stderr)


class DualLanePropertyTests(DualLaneBaseTests):
  """Role 2 & Role 3: Conditional PBT Shape 1 Guardians for P2 & P3 + Independent Oracle Calibration."""

  def _run_step_cli(self, url, direct='bg-audit-primary:2', shadow='bg-audit-shadow:2', standby=''):
    done = self.cli(['run', *self.dual_flags(url, direct=direct, shadow=shadow, standby=standby)], timeout=30)
    self.assertEqual(done.returncode, 0, done.stderr)

  def test_conditional_pbt_shape1_p2_and_p3_over_random_action_sequences(self):
    witnesses = {'yield_count': 0, 'shadow_step_count': 0, 'mode_toggle_count': 0, 'append_count': 0, 's402_count': 0}

    for seed in range(6):
      with self.subTest(seed=seed):
        # Fresh isolated state per seed
        self.tearDown()
        self.setUp()
        rng = random.Random(20261115 + seed)
        mode = 'quality'
        self.set_bg_mode(mode)
        transcript_path = self.source('work/pbt.jsonl', f'PBT_INIT_{seed}')
        chunk_idx = 0

        behavior = {'primary_code': 200, 'shadow_code': 200}

        def reply(body):
          model = json.loads(body)['model']
          if model == 'bg-audit-primary':
            if behavior['primary_code'] != 200:
              return '{"error": "primary-402"}', behavior['primary_code']
            label = fixtures.label_for(body, 'BASE_RULE_FALLBACK')
            tags = []
            if label and 'PBT_INIT_' in fixtures.user_text(body):
              tags = [{'rule': label, 'verdict': 'violated', 'source_line': 1, 'quote': f'PBT_INIT_{seed}'}]
            return fixtures.analysis([], rule_tags=tags), 200
          if model == 'bg-audit-shadow':
            if behavior['shadow_code'] != 200:
              return '{"error": "shadow-402"}', behavior['shadow_code']
            return fixtures.analysis([]), 200
          return fixtures.analysis([], rule_tags=[]), 200

        _, url = self.provider(reply)
        actions = [
          'append_transcript',
          'step_primary_ok',
          'step_primary_yield',
          'step_primary_402',
          'step_shadow_ok',
          'step_shadow_402',
          'toggle_bg_mode',
        ]
        # Ensure deterministic coverage of every action plus random interleaving
        sequence = list(actions) + [rng.choice(actions) for _ in range(8)]
        rng.shuffle(sequence)

        for step_num, action in enumerate(sequence):
          # Clear leg_pauses only if an ok step is testing recovery after prior 402
          if action == 'append_transcript':
            chunk_idx += 1
            with transcript_path.open('a') as handle:
              handle.write(fixtures.user_line(f'PBT_CHUNK_{seed}_{chunk_idx}', session_id=transcript_path.stem))
            witnesses['append_count'] += 1
            continue

          if action == 'toggle_bg_mode':
            mode = 'eco' if mode == 'quality' else 'quality'
            self.set_bg_mode(mode)
            witnesses['mode_toggle_count'] += 1
            continue

          before = self.state_snapshot()
          incomplete_before = {
            r['inode'] for r in before['sources'] if r['status'] != 'complete'
          }
          if not before['sources']:
            # Scan first if DB doesn't exist yet so incomplete_before is accurate when yielding
            self.assertEqual(self.cli(['scan', *self.flags(url)]).returncode, 0)
            before = self.state_snapshot()
            incomplete_before = {r['inode'] for r in before['sources'] if r['status'] != 'complete'}

          if action == 'step_primary_yield':
            self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}])
            behavior['primary_code'] = 200
            behavior['shadow_code'] = 200
            self._run_step_cli(url)
            after = self.state_snapshot()
            ok3, msg3 = oracle_p3_yield_no_false_complete(before, after, incomplete_before, yielded=True)
            self.assertTrue(ok3, f'seed={seed} step={step_num} {action}: {msg3}')
            if incomplete_before:
              witnesses['yield_count'] += 1
            # In eco mode (no shadow), primary yield is also a no-op on primary; if quality mode ran shadow, P2 still holds!
            ok2, msg2 = oracle_p2_primary_isolated(before, after)
            self.assertTrue(ok2, f'seed={seed} step={step_num} {action}: {msg2}')

          elif action == 'step_shadow_ok':
            # Isolate shadow by ensuring quality mode and yielding primary via concurrency
            prev_mode = mode
            self.set_bg_mode('quality')
            self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}])
            with sqlite3.connect(self.state / 'state.sqlite') as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-shadow'")
              conn.commit()
            behavior['shadow_code'] = 200
            self._run_step_cli(url)
            after = self.state_snapshot()
            ok2, msg2 = oracle_p2_primary_isolated(before, after)
            self.assertTrue(ok2, f'seed={seed} step={step_num} {action}: {msg2}')
            ok3, msg3 = oracle_p3_yield_no_false_complete(before, after, incomplete_before, yielded=True)
            self.assertTrue(ok3, f'seed={seed} step={step_num} {action}: {msg3}')
            witnesses['shadow_step_count'] += 1
            self.set_bg_mode(prev_mode)

          elif action == 'step_shadow_402':
            prev_mode = mode
            self.set_bg_mode('quality')
            self.set_concurrency(active=[{'agent': 'claude-code', 'model': 'gemini-4-argon'}])
            with sqlite3.connect(self.state / 'state.sqlite') as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-shadow'")
              conn.commit()
            behavior['shadow_code'] = 402
            self._run_step_cli(url)
            after = self.state_snapshot()
            ok2, msg2 = oracle_p2_primary_isolated(before, after)
            self.assertTrue(ok2, f'seed={seed} step={step_num} {action}: {msg2}')
            ok3, msg3 = oracle_p3_yield_no_false_complete(before, after, incomplete_before, yielded=True)
            self.assertTrue(ok3, f'seed={seed} step={step_num} {action}: {msg3}')
            witnesses['s402_count'] += 1
            self.set_bg_mode(prev_mode)

          elif action == 'step_primary_402':
            self.set_concurrency(active=[])
            with sqlite3.connect(self.state / 'state.sqlite') as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-primary'")
              conn.commit()
            behavior['primary_code'] = 402
            behavior['shadow_code'] = 200
            self._run_step_cli(url, standby='audit-tail:10')
            witnesses['s402_count'] += 1

          elif action == 'step_primary_ok':
            self.set_concurrency(active=[])
            with sqlite3.connect(self.state / 'state.sqlite') as conn:
              conn.execute("DELETE FROM leg_pauses WHERE alias = 'bg-audit-primary'")
              conn.commit()
            behavior['primary_code'] = 200
            behavior['shadow_code'] = 200
            self._run_step_cli(url)

        # Final idle drain: primary must finish remaining bytes with status == 'complete'
        self.set_bg_mode('quality')
        self.set_concurrency(active=[])
        with sqlite3.connect(self.state / 'state.sqlite') as conn:
          conn.execute('DELETE FROM leg_pauses')
          conn.commit()
        behavior['primary_code'] = 200
        behavior['shadow_code'] = 200
        self._run_step_cli(url)
        final = self.state_snapshot()
        self.assertEqual([r['status'] for r in final['sources']], ['complete'])
        self.assertEqual([r['status'] for r in final['shadow_cursors']], ['complete'])

    # Reachability witnesses across the PBT batch
    self.assertGreater(witnesses['yield_count'], 0)
    self.assertGreater(witnesses['shadow_step_count'], 0)
    self.assertGreater(witnesses['mode_toggle_count'], 0)
    self.assertGreater(witnesses['append_count'], 0)
    self.assertGreater(witnesses['s402_count'], 0)

  def test_oracle_calibration_detects_local_bad_states(self):
    """Role 3: Independent Oracles P2 and P3 individually return False on test-only bad states."""
    base_before = {
      'sources': [{'inode': 'in-1', 'doc_offset': 50, 'continuity': 'c1', 'status': 'pending', 'latest_complete': False, 'generation': 1}],
      'rule_tags': [('in-1', 1, 'R1', 'applied', 1, 'hello')],
    }
    # 1. P2 bad state A: shadow step mutates sources.doc_offset
    bad_p2_offset = {
      'sources': [{'inode': 'in-1', 'doc_offset': 99, 'continuity': 'c1', 'status': 'pending', 'latest_complete': False, 'generation': 1}],
      'rule_tags': [('in-1', 1, 'R1', 'applied', 1, 'hello')],
    }
    ok, _ = oracle_p2_primary_isolated(base_before, bad_p2_offset)
    self.assertFalse(ok, 'Oracle P2 must return False when sources.doc_offset changes on a shadow step')

    # 2. P2 bad state B: shadow step mutates rule_tags
    bad_p2_tags = {
      'sources': [{'inode': 'in-1', 'doc_offset': 50, 'continuity': 'c1', 'status': 'pending', 'latest_complete': False, 'generation': 1}],
      'rule_tags': [('in-1', 1, 'R1', 'applied', 1, 'hello'), ('in-1', 1, 'R2', 'violated', 2, 'shadow')],
    }
    ok, _ = oracle_p2_primary_isolated(base_before, bad_p2_tags)
    self.assertFalse(ok, 'Oracle P2 must return False when rule_tags changes on a shadow step')

    # 3. P3 bad state: unexhausted source marked complete during yield
    bad_p3_complete = {
      'sources': [{'inode': 'in-1', 'doc_offset': 50, 'continuity': 'c1', 'status': 'complete', 'latest_complete': True, 'generation': 1}],
      'rule_tags': [('in-1', 1, 'R1', 'applied', 1, 'hello')],
    }
    ok, _ = oracle_p3_yield_no_false_complete(base_before, bad_p3_complete, incomplete_inodes={'in-1'}, yielded=True)
    self.assertFalse(ok, 'Oracle P3 must return False when unexhausted source is marked complete during yield')


if __name__ == '__main__':
  suite = unittest.TestSuite()
  for cls in (ConcreteCapabilityExamples, DualLanePropertyTests):
    for name in cls.__dict__:
      if name.startswith('test_'):
        suite.addTest(cls(name))
  result = unittest.TextTestRunner(verbosity=2).run(suite)
  raise SystemExit(0 if result.wasSuccessful() else 1)
