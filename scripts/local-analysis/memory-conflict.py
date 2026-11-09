#!/usr/bin/env python3
"""每日記憶矛盾檢查的機械步驟：挑出要檢查的筆記並切批，以及在報告落檔後推進水位線。

由 ~/.claude/workflows/memory-conflict-check.js 呼叫；判斷交給模型，這裡只做確定性的挑選與記帳。
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

INDEX_NAME = re.compile(r'(^|/)(_index_[^/]*|_INDEX|MEMORY)\.md$')
LINK_MD = re.compile(r'\]\(([^)\s#]+\.md)\)')
LINK_WIKI = re.compile(r'\[\[([^\]|#]+)')
FRONT_NAME = re.compile(r'^name:\s*(.+?)\s*$', re.M)
FRONT_DESC = re.compile(r'^description:\s*(.+?)\s*$', re.M)
WORD = re.compile(r'[a-z0-9][a-z0-9._-]{2,}')
CJK = re.compile(r'[一-鿿]+')
FIRST_RUN_HOURS = 36


def rel(root, path):
    return Path(path).relative_to(root).as_posix()


def is_index(relpath):
    return bool(INDEX_NAME.search(relpath))


def is_history(relpath):
    # archive/ and dot folders such as .health-reports/ are kept as history on purpose;
    # flagging them as stale only buries the live notes in noise.
    parts = relpath.split('/')[:-1]
    return any(part == 'archive' or part.startswith('.') for part in parts)


def scan_tree(root):
    notes, indexes = [], []
    for p in sorted(root.rglob('*.md')):
        if not p.is_file() or is_history(rel(root, p)):
            continue
        (indexes if is_index(rel(root, p)) else notes).append(p)
    return notes, indexes


def slug_map(root, notes, texts):
    by_slug = {}
    for p in notes:
        by_slug.setdefault(p.stem, rel(root, p))
        m = FRONT_NAME.search(texts[rel(root, p)])
        if m:
            by_slug.setdefault(m.group(1).strip().strip('"\''), rel(root, p))
    return by_slug


def links_of(root, path, text, by_slug, known):
    out = set()
    for target in LINK_MD.findall(text):
        if target.startswith(('http:', 'https:', 'file:')):
            continue
        cand = (path.parent / target).resolve()
        try:
            r = rel(root, cand)
        except ValueError:
            continue
        if r in known:
            out.add(r)
    for slug in LINK_WIKI.findall(text):
        r = by_slug.get(slug.strip())
        if r:
            out.add(r)
    return out


def tokens(text):
    head = ' '.join(m.group(1) for m in (FRONT_NAME.search(text), FRONT_DESC.search(text)) if m).lower()
    toks = set(WORD.findall(head))
    for run in CJK.findall(head):
        toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def build_graph(root):
    notes, indexes = scan_tree(root)
    texts = {rel(root, p): p.read_text(encoding='utf-8', errors='replace') for p in notes + indexes}
    known = {rel(root, p) for p in notes}
    by_slug = slug_map(root, notes, texts)
    clusters = {}
    for idx in indexes:
        r = rel(root, idx)
        clusters[r] = sorted(links_of(root, idx, texts[r], by_slug, known))
    member_of = {n: [] for n in known}
    for idx_rel, members in sorted(clusters.items()):
        for m in members:
            member_of[m].append(idx_rel)
    out_links = {n: links_of(root, root / n, texts[n], by_slug, known) - {n} for n in known}
    in_links = {n: set() for n in known}
    for n, targets in out_links.items():
        for t in targets:
            in_links[t].add(n)
    toks = {n: tokens(texts[n]) for n in known}
    size = {n: len(texts[n].encode('utf-8')) for n in known}
    mtime = {n: (root / n).stat().st_mtime for n in known}
    return {'known': known, 'clusters': clusters, 'member_of': member_of, 'out': out_links,
            'in': in_links, 'toks': toks, 'size': size, 'mtime': mtime}


def candidates(g, note, cap, byte_cap):
    neighbours = sorted(g['out'][note] | g['in'][note])
    siblings = set()
    for idx in g['member_of'][note]:
        siblings.update(g['clusters'][idx])
    siblings -= set(neighbours) | {note}
    ranked = sorted(siblings, key=lambda s: (-jaccard(g['toks'][note], g['toks'][s]), s))
    picked, used = [], 0
    for c in neighbours + ranked:
        if len(picked) >= cap:
            break
        # One oversized sibling would otherwise crowd out every closer candidate.
        if used + g['size'][c] > byte_cap:
            continue
        picked.append(c)
        used += g['size'][c]
    return picked


def primary_cluster(g, note):
    return g['member_of'][note][0] if g['member_of'][note] else '~unindexed/' + note.split('/')[0].split('_')[0]


def make_batches(g, focus, context_cap, context_bytes, batch_bytes, batch_files):
    batches, cur = [], None
    for note in sorted(focus, key=lambda n: (primary_cluster(g, n), n)):
        ctx = candidates(g, note, context_cap, context_bytes)
        if cur is not None:
            files = set(cur['focus']) | set(cur['context']) | {note} | set(ctx)
            if len(files) > batch_files or sum(g['size'][f] for f in files) > batch_bytes:
                batches.append(cur)
                cur = None
        if cur is None:
            cur = {'focus': [], 'context': []}
        cur['focus'].append(note)
        cur['context'] = sorted((set(cur['context']) | set(ctx)) - set(cur['focus']))
    if cur is not None:
        batches.append(cur)
    for b in batches:
        b['context'] = sorted(set(b['context']) - set(b['focus']))
    return batches


def read_cursor(state_dir):
    p = Path(state_dir) / 'cursor.json'
    if not p.is_file():
        return None
    return float(json.loads(p.read_text(encoding='utf-8'))['last_epoch'])


def select_focus(g, scope, cursor, now, exclude):
    if scope == 'all':
        return sorted(n for n in g['known'] if n not in exclude)
    since = cursor if cursor is not None else now - FIRST_RUN_HOURS * 3600
    return sorted((n for n in g['known'] if g['mtime'][n] > since and n not in exclude), key=lambda n: (g['mtime'][n], n))


def cmd_prepare(a):
    root = Path(a.root).expanduser().resolve()
    now = time.time() if a.now is None else a.now
    exclude = set()
    if a.exclude_file:
        exclude = {line.strip() for line in Path(a.exclude_file).read_text(encoding='utf-8').splitlines() if line.strip()}
    g = build_graph(root)
    focus = select_focus(g, a.scope, read_cursor(a.state), now, exclude)
    batches = make_batches(g, focus, a.context_cap, a.context_bytes, a.batch_bytes, a.batch_files)
    kept, deferred = batches[:a.max_batches], batches[a.max_batches:]
    deferred_focus = [n for b in deferred for n in b['focus']]
    if a.scope == 'changed' and deferred_focus:
        cursor_candidate = min(g['mtime'][n] for n in deferred_focus) - 1e-6
    else:
        cursor_candidate = now
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    listed = []
    for i, b in enumerate(kept, 1):
        bid = f'B{i:02d}'
        manifest = {
            'batch_id': bid,
            'memory_root': str(root),
            'focus': [str(root / n) for n in b['focus']],
            'context': [str(root / n) for n in b['context']],
        }
        path = out_dir / f'batch-{bid}.json'
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        files = b['focus'] + b['context']
        listed.append({'batch_id': bid, 'path': str(path), 'focus_count': len(b['focus']),
                       'file_count': len(files), 'bytes': sum(g['size'][f] for f in files)})
    print(json.dumps({
        'ok': True,
        'scope': a.scope,
        'focus_count': sum(len(b['focus']) for b in kept),
        'deferred_focus_count': len(deferred_focus),
        'cursor_candidate': cursor_candidate,
        'batches': listed,
    }, ensure_ascii=False))


def cmd_commit_cursor(a):
    report = Path(a.report)
    if not report.is_file() or report.stat().st_size == 0:
        print(json.dumps({'ok': False, 'reason': 'report-missing-or-empty', 'report': str(report)}, ensure_ascii=False))
        return 1
    state = Path(a.state)
    state.mkdir(parents=True, exist_ok=True)
    prev = read_cursor(a.state)
    if prev is not None and a.epoch < prev:
        print(json.dumps({'ok': False, 'reason': 'cursor-would-move-backwards', 'previous': prev, 'epoch': a.epoch}))
        return 1
    tmp = state / 'cursor.json.tmp'
    tmp.write_text(json.dumps({'last_epoch': a.epoch, 'date': a.date, 'report': str(report)}, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, state / 'cursor.json')
    print(json.dumps({'ok': True, 'last_epoch': a.epoch, 'date': a.date}, ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    pp = sub.add_parser('prepare')
    pp.add_argument('--root', default='~/.claude/memory')
    pp.add_argument('--state', required=True)
    pp.add_argument('--out-dir', required=True)
    pp.add_argument('--scope', choices=['changed', 'all'], default='changed')
    pp.add_argument('--exclude-file')
    pp.add_argument('--context-cap', type=int, default=6)
    pp.add_argument('--context-bytes', type=int, default=50_000)
    pp.add_argument('--batch-bytes', type=int, default=200_000)
    pp.add_argument('--batch-files', type=int, default=40)
    pp.add_argument('--max-batches', type=int, default=10)
    pp.add_argument('--now', type=float)
    pc = sub.add_parser('commit-cursor')
    pc.add_argument('--state', required=True)
    pc.add_argument('--epoch', type=float, required=True)
    pc.add_argument('--date', required=True)
    pc.add_argument('--report', required=True)
    a = p.parse_args(argv)
    if a.cmd == 'prepare':
        cmd_prepare(a)
        return 0
    return cmd_commit_cursor(a)


if __name__ == '__main__':
    sys.exit(main())
