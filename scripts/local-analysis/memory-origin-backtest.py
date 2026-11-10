#!/usr/bin/env python3
"""回原對話核對的回測：組考卷、量抽法覆蓋率、跑模型、算分。

考卷與模型回覆含個人筆記與對話原文，一律放在 gitignored 的 reports/local-analysis/memory-origin/backtest/。
"""
import argparse
import concurrent.futures as cf
import importlib.util
import json
import random
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('memory_origin', HERE / 'memory-origin.py')
mo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mo)

WORK = HERE.parent.parent / 'reports' / 'local-analysis' / 'memory-origin' / 'backtest'
LEDGER = mo.MEMORY_ROOT / '.verification-ledger.jsonl'
MODES = ('user', 'near', 'anchor', 'both', 'all')


def git_show(commit, rel):
    return subprocess.run(['git', '-C', str(mo.CLAUDE_REPO), 'show', f'{commit}:memory/{rel}'],
                          capture_output=True, text=True, check=False).stdout


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def ledger_ok_notes():
    latest = {}
    if LEDGER.exists():
        for row in load_jsonl(LEDGER):
            if row.get('file'):
                latest[row['file']] = row.get('verdict')
    return {f for f, v in latest.items() if v == 'ok'}


def first_write_content(transcript, note_name):
    """原對話裡第一次 Write 這條筆記的內容；第一次是 Edit 就無法還原，回 None。"""
    with open(transcript, encoding='utf-8', errors='replace') as fh:
        for raw in fh:
            if note_name not in raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            for block in ((row.get('message') or {}).get('content') or []):
                if not isinstance(block, dict) or block.get('type') != 'tool_use':
                    continue
                inp = block.get('input') or {}
                if str(inp.get('file_path', '')).endswith('/' + note_name):
                    return inp.get('content') if block.get('name') == 'Write' else None
    return None


def mutate_number(note_text, events, rng):
    """人造錯誤：挑一個也出現在原對話工具輸出裡的數字，改成對話裡沒出現過的值。

    只測得出「數字對不上」這一型，比真實錯誤好抓，成績要分開看。
    """
    body_start = len(mo.FRONTMATTER.match(note_text).group(0)) if mo.FRONTMATTER.match(note_text) else 0
    body = note_text[body_start:]
    results = [e for e in events if e['role'] == 'tool_result']
    options = []
    for num in sorted({n for n in mo.NUMBER.findall(body) if not mo.YEARISH.match(n)}):
        if body.count(num) != 1 or '.' in num or len(num) < 2:
            continue
        lines = [e['line'] for e in results if num in e['text']]
        if lines:
            options.append((num, lines))
    if not options:
        return None
    num, lines = rng.choice(options)
    corpus = '\n'.join(e['text'] for e in results)
    for factor in (2, 3, 5, 7):
        new = str(int(num) * factor + 1)
        if new not in corpus and new not in note_text:
            return note_text[:body_start] + body.replace(num, new, 1), num, new, lines[:5]
    return None


def cmd_build_exam(args):
    cands = {r['note']: r for r in load_jsonl(WORK / 'candidates.jsonl')}
    labels = load_jsonl(WORK / 'labels.jsonl')
    rng = random.Random(args.seed)
    items = []
    for lab in labels:
        if lab.get('label') == 'A' and lab['note'] in cands:
            items.append({**cands[lab['note']], 'kind': 'pos', 'expected_error': lab.get('error', ''),
                          'evidence_lines': lab.get('evidence_lines') or []})
    early = set((WORK / 'early.txt').read_text(encoding='utf-8').split()) if (WORK / 'early.txt').exists() else set()
    for lab in labels:
        if lab['note'] in cands and any(frag in lab['note'] for frag in early):
            content = first_write_content(cands[lab['note']]['transcript'], Path(lab['note']).name)
            if content:
                items.append({**cands[lab['note']], 'kind': 'pos_early', 'expected_error': lab.get('reason', ''),
                              'evidence_lines': lab.get('evidence_lines') or [], 'note_text': content})
    drift = [lab for lab in labels if lab.get('label') == 'B' and lab['note'] in cands]
    for lab in rng.sample(drift, min(args.drift, len(drift))):
        items.append({**cands[lab['note']], 'kind': 'neg_drift', 'expected_error': '', 'evidence_lines': []})
    unrelated = [lab for lab in labels if lab.get('label') == 'C' and lab['note'] in cands]
    for lab in rng.sample(unrelated, min(args.unrelated, len(unrelated))):
        items.append({**cands[lab['note']], 'kind': 'neg_unrelated', 'expected_error': '', 'evidence_lines': []})
    # 沒有任何更正字眼、且驗證帳本最新一筆是 ok 的筆記，當「推定正確」；被判錯時要人工複核，不直接算誤報。
    transcripts = mo.transcript_index()
    ok_notes = ledger_ok_notes()
    pool = []
    for path in mo.note_paths():
        rel = path.relative_to(mo.MEMORY_ROOT).as_posix()
        if rel in cands or Path(rel).name not in ok_notes:
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        origin = mo.origin_of(text)
        if mo.CORRECTION.search(text) or not origin or origin not in transcripts:
            continue
        pool.append((rel, origin))
    sampled = rng.sample(pool, min(args.presumed + args.synthetic * 3, len(pool)))
    presumed, spare = sampled[:args.presumed], sampled[args.presumed:]
    for rel, origin in presumed:
        commit, date = mo.first_version(f'memory/{rel}')
        if not commit:
            continue
        items.append({'note': rel, 'origin': origin, 'transcript': str(transcripts[origin]),
                      'first_commit': commit, 'first_date': date, 'kind': 'neg_presumed',
                      'expected_error': '', 'evidence_lines': []})
    made = 0
    for rel, origin in spare:
        if made >= args.synthetic:
            break
        commit, date = mo.first_version(f'memory/{rel}')
        if not commit:
            continue
        text = git_show(commit, rel)
        events, _ = mo.parse_transcript(transcripts[origin], Path(rel).name)
        mutated = mutate_number(text, events, rng)
        if not mutated:
            continue
        new_text, old, new, lines = mutated
        items.append({'note': rel, 'origin': origin, 'transcript': str(transcripts[origin]),
                      'first_commit': commit, 'first_date': date, 'kind': 'pos_synth',
                      'expected_error': f'數字 {old} 被改成 {new}', 'evidence_lines': lines,
                      'note_text': new_text})
        made += 1
    notes_dir = WORK / 'notes'
    notes_dir.mkdir(parents=True, exist_ok=True)
    kept = []
    for idx, item in enumerate(items):
        text = item.pop('note_text', None)
        if text is None:
            text = git_show(item['first_commit'], item['note']) if item.get('first_commit') else ''
        if not text.strip():
            continue
        item['id'] = f'q{idx:03d}'
        (notes_dir / f"{item['id']}.md").write_text(text, encoding='utf-8')
        kept.append(item)
    with open(WORK / 'exam.jsonl', 'w', encoding='utf-8') as fh:
        for item in kept:
            fh.write(json.dumps(item, ensure_ascii=False) + '\n')
    counts = {}
    for item in kept:
        counts[item['kind']] = counts.get(item['kind'], 0) + 1
    print(json.dumps({'items': len(kept), 'by_kind': counts}, ensure_ascii=False))


def exam_items():
    return load_jsonl(WORK / 'exam.jsonl')


def extract_for(item, mode, caps):
    note_text = (WORK / 'notes' / f"{item['id']}.md").read_text(encoding='utf-8')
    events, write_line = mo.parse_transcript(item['transcript'], Path(item['note']).name)
    items = mo.excerpt(events, write_line, mode, caps, caps, 3, note_text, caps)
    return note_text, items, write_line


def cmd_coverage(args):
    rows = []
    for mode in MODES:
        hit = total = 0
        chars = []
        for item in exam_items():
            _, items, write_line = extract_for(item, mode, args.cap)
            chars.append(sum(len(t) for _, _, t in items))
            # 寫筆記那一行本身就是筆記內容，抽到它不算抽到證據。
            evidence = [ln for ln in item['evidence_lines'] if ln != write_line]
            if item['kind'].startswith('pos') and evidence:
                total += 1
                lines = {ln for ln, _, _ in items}
                hit += any(ln in lines for ln in evidence)
        chars.sort()
        rows.append({'mode': mode, 'evidence_hit': hit, 'evidence_total': total,
                     'chars_median': chars[len(chars) // 2] if chars else 0,
                     'chars_max': chars[-1] if chars else 0})
    for row in rows:
        print(json.dumps(row))


def run_one(alias, item, mode, cap, key, out_dir):
    out = out_dir / f"{item['id']}.json"
    if out.exists():
        return 'skip'
    note_text, items, write_line = extract_for(item, mode, cap)
    prompt = mo.build_prompt(note_text, items)
    record = None
    for attempt in range(3):
        t0 = time.time()
        content, usage, model, error = mo.call_model(alias, prompt, key)
        record = {'id': item['id'], 'alias': alias, 'mode': mode, 'attempt': attempt + 1,
                  'seconds': round(time.time() - t0, 1), 'model': model, 'usage': usage,
                  'error': error, 'content': content, 'verdict': mo.parse_verdict(content),
                  'prompt_chars': len(prompt), 'write_line': write_line}
        if not error and record['verdict']:
            break
        # 429／上游中斷時退避再試；解析失敗也重試一次，但三次都失敗就照實記下。
        time.sleep(20 * (attempt + 1))
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return 'ok' if record['verdict'] else 'fail'


def cmd_run(args):
    key = mo.relay_key()
    items = exam_items()
    out_dir = WORK / 'runs' / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, args.alias, item, args.mode, args.cap, key, out_dir) for item in items]
        for fut in cf.as_completed(futures):
            status = fut.result()
            results[status] = results.get(status, 0) + 1
    print(json.dumps({'label': args.label, 'alias': args.alias, **results}))


def load_run(label):
    run_dir = WORK / 'runs' / label
    return {p.stem: json.loads(p.read_text(encoding='utf-8')) for p in run_dir.glob('*.json')}


def flagged(record):
    return bool(record and record.get('verdict') and record['verdict'].get('verdict') == 'mismatch')


def score(items, preds):
    """preds: id -> True/False/None（None＝沒有可用回覆）。每類回「被判 mismatch 數/題數」。"""
    totals, flags, no_answer = {}, {}, 0
    for item in items:
        kind = item['kind']
        totals[kind] = totals.get(kind, 0) + 1
        pred = preds.get(item['id'])
        if pred is None:
            no_answer += 1
            continue
        flags[kind] = flags.get(kind, 0) + int(pred)
    out = {kind: f'{flags.get(kind, 0)}/{n}' for kind, n in sorted(totals.items())}
    out['no_answer'] = no_answer
    return out


def cmd_score(args):
    items = exam_items()
    runs = {label: load_run(label) for label in args.labels}
    rows = []
    for label, recs in runs.items():
        preds = {i: (flagged(r) if r.get('verdict') else None) for i, r in recs.items()}
        usage = [r.get('usage') or {} for r in recs.values()]
        secs = sorted(r['seconds'] for r in recs.values())
        rows.append({'run': label, **score(items, preds),
                     'prompt_tokens': sum(u.get('prompt_tokens') or 0 for u in usage),
                     'completion_tokens': sum(u.get('completion_tokens') or 0 for u in usage),
                     'seconds_median': secs[len(secs) // 2] if secs else None})
    if args.pair:
        a, b = args.pair
        ra, rb = runs[a], runs[b]
        for name, combine in (('union', lambda x, y: x or y), ('intersection', lambda x, y: x and y)):
            preds = {}
            for item in items:
                pa = flagged(ra.get(item['id'])) if ra.get(item['id'], {}).get('verdict') else None
                pb = flagged(rb.get(item['id'])) if rb.get(item['id'], {}).get('verdict') else None
                preds[item['id']] = None if pa is None or pb is None else combine(pa, pb)
            rows.append({'run': f'{name}({a},{b})', **score(items, preds)})
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('build-exam')
    p.add_argument('--drift', type=int, default=10)
    p.add_argument('--presumed', type=int, default=15)
    p.add_argument('--unrelated', type=int, default=10)
    p.add_argument('--synthetic', type=int, default=12)
    p.add_argument('--seed', type=int, default=20261110)
    p.set_defaults(func=cmd_build_exam)
    p = sub.add_parser('coverage')
    p.add_argument('--cap', type=int, default=12000)
    p.set_defaults(func=cmd_coverage)
    p = sub.add_parser('run')
    p.add_argument('--alias', required=True)
    p.add_argument('--label', required=True)
    p.add_argument('--mode', choices=MODES, default='all')
    p.add_argument('--cap', type=int, default=12000)
    p.add_argument('--workers', type=int, default=2)
    p.set_defaults(func=cmd_run)
    p = sub.add_parser('score')
    p.add_argument('labels', nargs='+')
    p.add_argument('--pair', nargs=2)
    p.set_defaults(func=cmd_score)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
