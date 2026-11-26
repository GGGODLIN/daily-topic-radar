#!/usr/bin/env python3
"""每日記憶矛盾檢查的機械步驟：挑出要檢查的筆記並切批，以及在報告落檔後推進水位線。

由 ~/.claude/workflows/memory-conflict-check.js 呼叫；判斷交給模型，這裡只做確定性的挑選與記帳。
"""
import argparse
import fcntl
import json
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
from collections import Counter, defaultdict
from itertools import chain
from pathlib import Path
from urllib.parse import unquote

INDEX_NAME = re.compile(r'(^|/)(_index_[^/]*|_INDEX|MEMORY)\.md$')
LINK_MD = re.compile(r'\]\((?:<([^>\n#]+\.md)(?:#[^>\n]*)?>|([^)\s#]+\.md)(?:#[^)\s]*)?)\)', re.I)
LINK_WIKI = re.compile(r'\[\[([^\]|#\n]+)(?:[|#][^\]\n]*)?\]\]')
FRONT = re.compile(r'\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)', re.S)
FRONT_NAME = re.compile(r'^name:\s*(.+?)\s*$', re.M)
FRONT_DESC = re.compile(r'^description:\s*(.+?)\s*$', re.M)
WORD = re.compile(r'[a-z0-9][a-z0-9._-]{2,}')
CJK = re.compile(r'[一-鿿]+')
HAN = r'㐀-䶿一-鿿\U00020000-\U000323af'
# Start at the first underscore in a run to avoid rescanning Markdown dividers.
SEARCH_TERM = re.compile(rf'([{HAN}]+)|(?<!_)(?:--)?_*[^{HAN}\W_]+(?:(?:[.-]|_+)[^{HAN}\W_]+)*_*')
FENCE_LINE = re.compile(r'^[ \t]{0,3}(`{3,}|~{3,})(.*)$')
HEADING = re.compile(r'^ {0,3}#{1,6}[ \t]+(.+)$', re.M)
MARKDOWN_TARGET = re.compile(r'\]\((?:<[^<>\n]*>|[^()\n]*)\)')
INLINE_CODE = re.compile(r'(?<!`)(`{1,2})(?!`)([^`\n]{1,256})\1(?!`)')
CODE_MARKER = re.compile(r'^--|_|[a-z0-9][A-Z][a-z]')
BODY_SEARCH_CHARS = 32_768
CODE_ANCHOR_MIN_SIM = 0.025
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
    root = Path(root).resolve()
    notes, indexes = [], []
    for p in sorted(root.rglob('*.md')):
        if p.is_symlink() or not p.is_file() or is_history(rel(root, p)):
            continue
        (indexes if is_index(rel(root, p)) else notes).append(p)
    return notes, indexes


def frontmatter(text):
    m = FRONT.match(text)
    return m.group(1) if m else ''


def slug_map(root, notes, texts):
    by_slug = {}
    for p in notes:
        r = rel(root, p)
        by_slug.setdefault(p.stem.casefold(), set()).add(r)
        m = FRONT_NAME.search(frontmatter(texts[r]))
        if m:
            alias = m.group(1).strip().strip('"\'').casefold()
            if alias:
                by_slug.setdefault(alias, set()).add(r)
    return {slug: next(iter(paths)) for slug, paths in by_slug.items() if len(paths) == 1}


def resolve_known(root, cand, known, by_fold):
    resolved = Path(cand).resolve()
    try:
        r = rel(root, resolved)
    except ValueError:
        return None
    if r in known:
        return r
    hit = by_fold.get(r.casefold())
    try:
        if hit and resolved.exists() and os.path.samefile(resolved, root / hit):
            return hit
    except OSError:
        return None
    return None


def links_of(root, path, text, by_slug, known, by_fold=None):
    if by_fold is None:
        by_fold = {n.casefold(): n for n in known}
    out = set()
    for bracketed, plain in LINK_MD.findall(text):
        target = bracketed or plain
        if re.match(r'^[a-z][a-z0-9+.-]*:|^//', target, re.I):
            continue
        r = resolve_known(root, path.parent / unquote(target), known, by_fold)
        if r:
            out.add(r)
    for slug in LINK_WIKI.findall(text):
        r = by_slug.get(slug.strip().casefold())
        if r:
            out.add(r)
    return out


def tokens(text):
    fm = frontmatter(text)
    head = ' '.join(m.group(1) for m in (FRONT_NAME.search(fm), FRONT_DESC.search(fm)) if m).lower()
    toks = set(WORD.findall(head))
    for run in CJK.findall(head):
        toks.update(run[i:i + 2] for i in range(len(run) - 1))
    return toks


def search_terms(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    for match in SEARCH_TERM.finditer(text):
        term = match[0]
        if match[1]:
            yield from (term[i:i + 2] for i in range(len(term) - 1))
        elif 2 <= len(term) <= 96 and not term.isdecimal():
            yield term


def _split_prose_and_code(body):
    """Split markdown body into (prose_text, fenced_code_text) respecting CommonMark fence char & length."""
    prose, fenced = [], []
    open_char, open_len = None, 0
    for line in body.splitlines(keepends=True):
        m = FENCE_LINE.match(line)
        if m:
            marker, rest = m.group(1), m.group(2)
            ch, length = marker[0], len(marker)
            if open_char is None:
                if ch == '`' and '`' in rest:
                    prose.append(line)
                else:
                    open_char, open_len = ch, length
                continue
            if ch == open_char and length >= open_len and not rest.strip():
                open_char, open_len = None, 0
                continue
        (prose if open_char is None else fenced).append(line)
    return ''.join(prose), ''.join(fenced)


def note_terms(text, vocabulary):
    fm = FRONT.match(text)
    start = fm.end() if fm else 0
    body = MARKDOWN_TARGET.sub(']', text[start:start + BODY_SEARCH_CHARS])
    prose, fenced = _split_prose_and_code(body)
    weights = dict.fromkeys(search_terms(prose), 1)
    for term in search_terms(fenced):
        if term not in weights:
            weights[term] = 1
    if fm:
        for field, weight in ((FRONT_NAME, 1), (FRONT_DESC, 2)):
            match = field.search(fm[1])
            if match:
                for term in search_terms(match[1][:4096]):
                    if weight > weights.get(term, 0):
                        weights[term] = weight
    # Only extract headings and inline code anchors from prose, never from fenced code blocks.
    for heading in HEADING.finditer(prose):
        for term in search_terms(heading[1]):
            weights[term] = 3
    identifiers = {match[0].casefold()
                   for span in INLINE_CODE.finditer(prose)
                   for match in SEARCH_TERM.finditer(unicodedata.normalize('NFKC', span[2]))
                   if 6 <= len(match[0]) <= 96 and CODE_MARKER.search(match[0])
                   and any(char.isalpha() for char in match[0])}
    return {vocabulary.setdefault(term, term): weight for term, weight in weights.items()}, identifiers


def search_index(terms_by_note):
    """Normalize field weights in place and build cross-cluster postings."""
    frequency = Counter(term for terms, _ in terms_by_note.values() for term in terms)
    corpus_size = len(terms_by_note)
    idf = {term: math.log((corpus_size + 1) / (count + 1)) + 1
           for term, count in frequency.items()}
    rare_limit = max(2, corpus_size // 20)
    code_limit = max(2, min(8, corpus_size // 8))
    anchors, code_anchors = defaultdict(set), defaultdict(set)
    vectors = {}
    for note, (terms, identifiers) in terms_by_note.items():
        for term, weight in terms.items():
            if weight > 1 and 2 <= frequency[term] <= rare_limit:
                anchors[term].add(note)
            terms[term] = weight * idf[term]
        norm = math.sqrt(math.fsum(weight ** 2 for weight in terms.values()))
        if norm > 0:
            for term in terms:
                terms[term] /= norm
        vectors[note] = terms
        for term in identifiers:
            if 2 <= frequency[term] <= code_limit:
                code_anchors[term].add(note)
    return vectors, dict(anchors), dict(code_anchors)


def similarity(a, b):
    return math.fsum(a[term] * b[term] for term in a.keys() & b.keys())


def build_graph(root):
    root = Path(root).resolve()
    notes, indexes = scan_tree(root)
    texts, mtime = {}, {}
    kept_notes, kept_indexes = [], []
    for p, bucket in [(p, kept_notes) for p in notes] + [(p, kept_indexes) for p in indexes]:
        try:
            with p.open('r', encoding='utf-8', errors='replace') as fh:
                r = rel(root, p)
                texts[r] = fh.read()
                mtime[r] = os.fstat(fh.fileno()).st_mtime
        except OSError:
            continue
        bucket.append(p)
    known = {rel(root, p) for p in kept_notes}
    by_fold = {n.casefold(): n for n in known}
    by_slug = slug_map(root, kept_notes, texts)
    clusters = {}
    for idx in kept_indexes:
        r = rel(root, idx)
        clusters[r] = sorted(links_of(root, idx, texts[r], by_slug, known, by_fold))
    member_of = {n: [] for n in known}
    for idx_rel, members in sorted(clusters.items()):
        for m in members:
            member_of[m].append(idx_rel)
    out_links = {n: links_of(root, root / n, texts[n], by_slug, known, by_fold) - {n} for n in known}
    in_links = {n: set() for n in known}
    for n, targets in out_links.items():
        for t in targets:
            in_links[t].add(n)
    vocabulary, terms, size = {}, {}, {}
    for n in sorted(known):
        text = texts.pop(n)
        size[n] = len(text.encode('utf-8'))
        terms[n] = note_terms(text, vocabulary)
    vectors, anchors, code_anchors = search_index(terms)
    mtime = {n: mtime[n] for n in known}
    return {'known': known, 'clusters': clusters, 'member_of': member_of, 'out': out_links,
            'in': in_links, 'vectors': vectors, 'anchors': anchors, 'code_anchors': code_anchors,
            'size': size, 'mtime': mtime}


def related_candidates(g, note, neighbours):
    siblings = set()
    for idx in g['member_of'][note]:
        siblings.update(g['clusters'][idx])
    excluded = neighbours | {note}
    siblings -= excluded
    vectors = g['vectors']
    query = vectors[note]
    scores = {s: similarity(query, vectors[s]) for s in siblings}
    evidence = defaultdict(list)
    code_hits = set()
    for term, weight in query.items():
        matches = g['anchors'].get(term)
        if matches and note in matches:
            for candidate in matches:
                if candidate not in excluded and candidate not in siblings:
                    evidence[candidate].append(weight * vectors[candidate][term])
        code_matches = g['code_anchors'].get(term)
        if code_matches and note in code_matches:
            code_hits.update(code_matches)
    outside = {candidate for candidate, overlap in evidence.items()
               if len(overlap) >= 2 and math.fsum(overlap) >= 0.30}
    for candidate in code_hits - excluded - siblings - outside:
        sim = similarity(query, vectors[candidate])
        if sim >= CODE_ANCHOR_MIN_SIM:
            scores[candidate] = sim
    for candidate in outside - excluded - siblings:
        scores[candidate] = similarity(query, vectors[candidate])
    yield from sorted(scores, key=lambda c: (-scores[c], c not in siblings, c))


def candidates(g, note, cap, byte_cap):
    if cap <= 0:
        return []
    neighbours = g['out'][note] | g['in'][note]
    picked, used = [], 0
    for c in chain(sorted(neighbours), related_candidates(g, note, neighbours)):
        # One oversized candidate must not crowd out smaller ones.
        if used + g['size'][c] > byte_cap:
            continue
        picked.append(c)
        used += g['size'][c]
        if len(picked) >= cap:
            break
    return picked


def primary_cluster(g, note):
    return g['member_of'][note][0] if g['member_of'][note] else '~unindexed/' + note.split('/')[0].split('_')[0]


def make_batches(g, focus, context_cap, context_bytes, batch_bytes, batch_files, _ctx_cache=None):
    batches, cur = [], None
    eff_cap = min(context_cap, max(0, batch_files - 1))
    for note in sorted(focus, key=lambda n: (primary_cluster(g, n), n)):
        eff_bytes = min(context_bytes, max(0, batch_bytes - g['size'][note]))
        cache_key = (note, eff_cap, eff_bytes)
        if _ctx_cache is not None and cache_key in _ctx_cache:
            ctx = _ctx_cache[cache_key]
        else:
            ctx = candidates(g, note, eff_cap, eff_bytes)
            if _ctx_cache is not None:
                _ctx_cache[cache_key] = ctx
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


def _reject_constant(val):
    raise ValueError(f'invalid JSON constant: {val}')


def _atomic_write(target_path, prefix, text):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', dir=target_path.parent, prefix=prefix, suffix='.tmp',
            encoding='utf-8', delete=False,
        ) as fh:
            tmp_path = fh.name
            fh.write(text)
        os.replace(tmp_path, target_path)
    except BaseException:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def read_cursor(state_dir):
    p = Path(state_dir) / 'cursor.json'
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding='utf-8'), parse_constant=_reject_constant)
    if not isinstance(data, dict) or 'last_epoch' not in data:
        raise ValueError('invalid cursor')
    val = data['last_epoch']
    if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
        raise ValueError('invalid cursor')
    return float(val)


def select_focus(g, scope, cursor, now, exclude):
    if scope == 'all':
        return sorted(n for n in g['known'] if n not in exclude)
    since = cursor if cursor is not None else now - FIRST_RUN_HOURS * 3600
    return sorted((n for n in g['known'] if g['mtime'][n] > since and n not in exclude), key=lambda n: (g['mtime'][n], n))


def capped_changed_batches(g, focus, context_cap, context_bytes, batch_bytes, batch_files, max_batches):
    """按 mtime 由舊到新取最大前綴 focus[:k] 使切批數 <= max_batches，避免同 cluster 新筆記被反覆重掃。"""
    if max_batches <= 0 or not focus:
        return [], list(focus)
    ctx_cache = {}
    all_batches = make_batches(g, focus, context_cap, context_bytes, batch_bytes, batch_files, ctx_cache)
    if len(all_batches) <= max_batches:
        return all_batches, []
    lo, hi = 1, len(focus)
    best_k, best_batches = 1, make_batches(g, focus[:1], context_cap, context_bytes, batch_bytes, batch_files, ctx_cache)[:max_batches]
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = make_batches(g, focus[:mid], context_cap, context_bytes, batch_bytes, batch_files, ctx_cache)
        if len(cand) <= max_batches:
            best_k, best_batches = mid, cand
            lo = mid + 1
        else:
            hi = mid - 1
    # 若切點剛好落在相同 mtime 的多則筆記中間，退回該秒之前，確保 cursor_candidate 不切斷同秒筆記；全同秒則整組保留。
    split_mtime = g['mtime'][focus[best_k]]
    trimmed_k = best_k
    while trimmed_k > 0 and g['mtime'][focus[trimmed_k - 1]] >= split_mtime:
        trimmed_k -= 1
    if trimmed_k > 0:
        best_k = trimmed_k
    else:
        while best_k < len(focus) and g['mtime'][focus[best_k]] <= split_mtime:
            best_k += 1
    best_batches = make_batches(g, focus[:best_k], context_cap, context_bytes, batch_bytes, batch_files, ctx_cache)
    best_batches.sort(key=lambda b: min(g['mtime'][n] for n in b['focus']))
    return best_batches, focus[best_k:]


def cmd_prepare(a):
    now = time.time() if a.now is None else a.now
    if not math.isfinite(now):
        print(json.dumps({'ok': False, 'reason': 'invalid-now', 'now': str(now)}, ensure_ascii=False, allow_nan=False))
        return 1
    root = Path(a.root).expanduser().resolve()
    if not root.is_dir():
        print(json.dumps({'ok': False, 'reason': 'root-not-found', 'root': str(root)}, ensure_ascii=False, allow_nan=False))
        return 1
    exclude_lines = []
    if a.exclude_file:
        try:
            exclude_lines = [line.strip() for line in Path(a.exclude_file).read_text(encoding='utf-8').splitlines() if line.strip()]
        except OSError:
            print(json.dumps({'ok': False, 'reason': 'exclude-file-missing', 'exclude_file': str(a.exclude_file)}, ensure_ascii=False, allow_nan=False))
            return 1
    try:
        cursor = read_cursor(a.state)
    except (ValueError, OSError):
        print(json.dumps({'ok': False, 'reason': 'invalid-cursor', 'cursor': str(Path(a.state) / 'cursor.json')}, ensure_ascii=False, allow_nan=False))
        return 1
    g = build_graph(root)
    by_fold = {n.casefold(): n for n in g['known']}
    exclude = set()
    for raw in exclude_lines:
        p = Path(raw).expanduser()
        cand = p if p.is_absolute() else root / p
        hit = resolve_known(root, cand, g['known'], by_fold)
        if hit:
            exclude.add(hit)
        else:
            try:
                exclude.add(rel(root, cand.resolve()))
            except ValueError:
                exclude.add(raw)
    focus = select_focus(g, a.scope, cursor, now, exclude)
    if a.scope == 'changed':
        kept, deferred_focus = capped_changed_batches(
            g, focus, a.context_cap, a.context_bytes, a.batch_bytes, a.batch_files, a.max_batches)
    else:
        batches = make_batches(g, focus, a.context_cap, a.context_bytes, a.batch_bytes, a.batch_files)
        batches.sort(key=lambda b: min(g['mtime'][n] for n in b['focus']))
        limit = max(0, a.max_batches)
        kept, deferred = batches[:limit], batches[limit:]
        deferred_focus = [n for b in deferred for n in b['focus']]
    if a.scope == 'changed' and deferred_focus:
        min_deferred = min(g['mtime'][n] for n in deferred_focus)
        kept_max = max((g['mtime'][n] for b in kept for n in b['focus']), default=None)
        floor = kept_max if kept_max is not None else cursor
        if floor is not None:
            cursor_candidate = max(floor, min_deferred - 1e-6)
            if floor < min_deferred and cursor_candidate >= min_deferred:
                cursor_candidate = floor
        else:
            cursor_candidate = min_deferred - 1e-6
    else:
        cursor_candidate = now
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob('batch-*.json'):
        if old.is_file() or old.is_symlink():
            old.unlink()
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
        _atomic_write(path, f'.batch-{bid}.', json.dumps(manifest, ensure_ascii=False, indent=1, allow_nan=False))
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
    }, ensure_ascii=False, allow_nan=False))
    return 0


def cmd_commit_cursor(a):
    if not math.isfinite(a.epoch):
        print(json.dumps({'ok': False, 'reason': 'invalid-epoch', 'epoch': str(a.epoch)}, ensure_ascii=False, allow_nan=False))
        return 1
    report = Path(a.report)
    if not report.is_file() or report.stat().st_size == 0:
        print(json.dumps({'ok': False, 'reason': 'report-missing-or-empty', 'report': str(report)}, ensure_ascii=False, allow_nan=False))
        return 1
    state = Path(a.state)
    state.mkdir(parents=True, exist_ok=True)
    lock_fh = (state / 'cursor.lock').open('a+', encoding='utf-8')
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX)
        try:
            prev = read_cursor(a.state)
        except (ValueError, OSError):
            print(json.dumps({'ok': False, 'reason': 'invalid-cursor', 'cursor': str(state / 'cursor.json')}, ensure_ascii=False, allow_nan=False))
            return 1
        if prev is not None and a.epoch < prev:
            print(json.dumps({'ok': False, 'reason': 'cursor-would-move-backwards', 'previous': prev, 'epoch': a.epoch}, ensure_ascii=False, allow_nan=False))
            return 1
        _atomic_write(
            state / 'cursor.json',
            '.cursor.',
            json.dumps({'last_epoch': a.epoch, 'date': a.date, 'report': str(report)}, ensure_ascii=False, allow_nan=False),
        )
    finally:
        lock_fh.close()
    print(json.dumps({'ok': True, 'last_epoch': a.epoch, 'date': a.date}, ensure_ascii=False, allow_nan=False))
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
    pp.add_argument('--batch-bytes', type=int, default=110_000)
    pp.add_argument('--batch-files', type=int, default=22)
    pp.add_argument('--max-batches', type=int, default=14)
    pp.add_argument('--now', type=float)
    pc = sub.add_parser('commit-cursor')
    pc.add_argument('--state', required=True)
    pc.add_argument('--epoch', type=float, required=True)
    pc.add_argument('--date', required=True)
    pc.add_argument('--report', required=True)
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    norm_argv = []
    i = 0
    while i < len(raw_argv):
        tok = raw_argv[i]
        if tok in ('--epoch', '--now') and i + 1 < len(raw_argv):
            norm_argv.append(f'{tok}={raw_argv[i + 1]}')
            i += 2
            continue
        norm_argv.append(tok)
        i += 1
    a = p.parse_args(norm_argv)
    if a.cmd == 'prepare':
        return cmd_prepare(a)
    return cmd_commit_cursor(a)


if __name__ == '__main__':
    sys.exit(main())
