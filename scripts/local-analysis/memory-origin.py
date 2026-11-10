#!/usr/bin/env python3
"""記憶筆記「回原對話核對」的機械步驟：找來源對話、抽原文摘錄、挑考卷候選。

判斷交給模型；這裡只做確定性的定位與抽取，不呼叫任何模型。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MEMORY_ROOT = Path.home() / '.claude' / 'memory'
CLAUDE_REPO = Path.home() / '.claude'
PROJECTS_ROOT = Path.home() / '.claude' / 'projects'
ORIGIN = re.compile(r'^\s*originSessionId:\s*([0-9a-f-]{36})\s*$', re.M)
INDEX_NAME = re.compile(r'(^|/)(_index_[^/]*|_INDEX|MEMORY|_orphan_registry)\.md$')
# 寫入當下就錯的更正，用語通常帶「初版／寫錯／誤解」；純漂移多半只寫「已改／已退役」。
CORRECTION = re.compile(r'更正|初版|寫錯|誤解|誤判|誤記|搞錯|是錯的|原本寫|原寫')


def note_paths(root=None):
    """archive/ 與點開頭資料夾刻意不收：那裡本來就是歷史，同 memory-conflict。"""
    root = root if root is not None else MEMORY_ROOT
    for path in sorted(root.rglob('*.md')):
        rel = path.relative_to(root).as_posix()
        parts = rel.split('/')
        if parts[0] == 'archive' or any(p.startswith('.') for p in parts[:-1]):
            continue
        if INDEX_NAME.search(rel):
            continue
        yield path


def origin_of(text):
    match = ORIGIN.search(text)
    return match.group(1) if match else None


def transcript_index(root=None):
    """session id → 主對話 jsonl；subagents/ 底下的不算來源對話。"""
    root = root if root is not None else PROJECTS_ROOT
    index = {}
    for path in root.glob('*/*.jsonl'):
        index.setdefault(path.stem, path)
    return index


def first_version(rel_to_repo):
    """筆記第一次進 git 的 commit，當作「寫入當下」的版本；--follow 跟過搬家。"""
    out = subprocess.run(
        ['git', '-C', str(CLAUDE_REPO), 'log', '--follow', '--diff-filter=A',
         '--format=%h\t%aI', '--', rel_to_repo],
        capture_output=True, text=True, check=False).stdout.strip().splitlines()
    if not out:
        return None, None
    commit, date = out[-1].split('\t')
    return commit, date


SYSTEM_BLOCK = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
# 這些 user 型訊息是 harness 代送的，不是使用者講的話。
NOT_HUMAN = ('<task-notification>', '<local-command-stdout>', '<command-name>', '[SYSTEM NOTIFICATION')
WRITE_TOOLS = {'Write', 'Edit', 'MultiEdit', 'NotebookEdit'}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
            elif isinstance(block, str):
                parts.append(block)
        return '\n'.join(parts)
    return ''


def _clip(text, cap):
    text = text.strip()
    return text if len(text) <= cap else text[:cap] + f'…〔截斷，原長 {len(text)} 字〕'


def parse_transcript(path, note_name, which='first'):
    """回傳事件串（含 1 起算的 jsonl 行號）與寫這條筆記的行號。

    which='first' 用於建立筆記的對話（第一次寫入）；'last' 用於修改它的對話（最後一次寫入）。
    事件只留判斷原意用得到的：使用者原話、助理文字、工具呼叫的輸入與結果。
    """
    events, write_line = [], None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for lineno, raw in enumerate(fh, 1):
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = row.get('type')
            msg = row.get('message') or {}
            content = msg.get('content')
            if kind == 'user' and not row.get('isMeta'):
                if isinstance(content, str):
                    text = SYSTEM_BLOCK.sub('', content).strip()
                    if text and not text.startswith(NOT_HUMAN):
                        events.append({'line': lineno, 'role': 'user', 'text': text})
                elif isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'tool_result':
                            text = _text_of(block.get('content'))
                            events.append({'line': lineno, 'role': 'tool_result', 'text': text})
                        elif block.get('type') == 'text':
                            text = SYSTEM_BLOCK.sub('', block.get('text', '')).strip()
                            if text and not text.startswith(NOT_HUMAN):
                                events.append({'line': lineno, 'role': 'user', 'text': text})
            elif kind == 'assistant' and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'text' and block.get('text', '').strip():
                        events.append({'line': lineno, 'role': 'assistant', 'text': block['text']})
                    elif block.get('type') == 'tool_use':
                        inp = block.get('input') or {}
                        name = block.get('name', '')
                        target = str(inp.get('file_path') or inp.get('notebook_path') or '')
                        if name in WRITE_TOOLS and target.endswith('/' + note_name) and (
                                write_line is None or which == 'last'):
                            write_line = lineno
                        events.append({'line': lineno, 'role': 'tool_use',
                                       'text': f'{name} ' + json.dumps(inp, ensure_ascii=False)})
    return events, write_line


FRONTMATTER = re.compile(r'\A---\n.*?\n---\n', re.S)
NUMBER = re.compile(r'\d+\.\d+|\d{2,}')
YEARISH = re.compile(r'^(19|20)\d\d$')
CODEISH = re.compile(r'`([^`\n]{3,60})`')


def anchors_of(note_text):
    """筆記內文的數字與程式碼片段；frontmatter 不算，裡面的 session id 會亂命中。"""
    body = FRONTMATTER.sub('', note_text)
    nums = {n for n in NUMBER.findall(body) if not YEARISH.match(n)}
    codes = {c.strip() for c in CODEISH.findall(body)}
    return nums | codes


def anchor_segments(events, anchors, cap, top=8, min_hits=3, lookback=8):
    """筆記裡的數字多半抄自某次工具輸出；找出命中最多的輸出，連同產生它的指令一起帶上。

    限制條件（例如只掃大檔）常寫在指令裡、離寫筆記很遠，近窗抽不到。
    不要改成依線索稀有度加權或把指令也納入計分：回測覆蓋率反而從 10/16 掉到 6～7/16。
    """
    if not anchors:
        return []
    scored = []
    for e in events:
        if e['role'] != 'tool_result':
            continue
        hits = sum(1 for a in anchors if a in e['text'])
        if hits >= min_hits:
            scored.append((hits, e['line'], e))
    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen, budget = [], cap
    for _, line, hit in scored[:top]:
        group = [e for e in events if e['role'] == 'tool_use' and line - lookback <= e['line'] < line]
        group.append(hit)
        texts = [(e['line'], e['role'], _clip(e['text'], 2500 if e['role'] == 'tool_result' else 1500))
                 for e in group]
        size = sum(len(t) for _, _, t in texts)
        if size > budget:
            continue
        budget -= size
        chosen.extend(texts)
    return chosen


def excerpt(events, write_line, mode, user_cap=12000, near_cap=12000, near_turns=3,
            note_text='', anchor_cap=12000):
    """依抽法組出摘錄；回傳 [(line, role, text)]，依行號排序、不重複。

    user：整段對話裡使用者說過的話（判斷原意與範圍）。
    near：寫筆記前 near_turns 則使用者訊息以來的所有事件（判斷工具輸出與限制條件）。
    anchor：筆記數字與程式碼片段命中最多的工具輸出，連同前面的指令。
    all：三者合併。找不到寫入點時以對話結尾當寫入點。
    """
    end = write_line or (events[-1]['line'] if events else 0)
    picked = {}
    if mode in ('anchor', 'all'):
        for item in anchor_segments(events, anchors_of(note_text), anchor_cap):
            picked[item] = True
    if mode in ('user', 'both', 'all'):
        users = [e for e in events if e['role'] == 'user']
        # 超過上限時保留離寫入點最近的，因為改寫原意的那句通常就在寫之前或剛寫完。
        users.sort(key=lambda e: abs(e['line'] - end))
        budget = user_cap
        for e in users:
            text = _clip(e['text'], 2000)
            if len(text) > budget:
                continue
            budget -= len(text)
            picked[(e['line'], e['role'], text)] = True
    if mode in ('near', 'both', 'all'):
        before = [e for e in events if e['line'] <= end]
        start_idx, seen = 0, 0
        for idx in range(len(before) - 1, -1, -1):
            if before[idx]['role'] == 'user':
                seen += 1
                if seen == near_turns:
                    start_idx = idx
                    break
        budget = near_cap
        window = before[start_idx:]
        # 從寫入點往回填，預算用完就停：離寫入點越近越相關。
        chosen = []
        for e in reversed(window):
            cap = 1500 if e['role'] == 'tool_result' else (800 if e['role'] == 'tool_use' else 2000)
            text = _clip(e['text'], cap)
            if len(text) > budget:
                break
            budget -= len(text)
            chosen.append((e['line'], e['role'], text))
        for item in chosen:
            picked[item] = True
    return sorted(picked, key=lambda t: t[0])


RELAY = 'http://127.0.0.1:8317/v1'
KEYS_ENV = Path.home() / '.cli-proxy-api' / 'keys.env'

JUDGE_PROMPT = """你是記憶筆記的核對員。下面有一條 AI 助理寫的記憶筆記，以及它當初寫成的那段對話的摘錄（每段前面是對話紀錄的行號與角色）。

請只判斷一件事：**筆記寫入當下，有沒有把這段對話裡的事實記錯。** 例如：
- 把使用者說的話、意圖或要的範圍記錯；
- 對話裡的資料只涵蓋一部分（例如只查了子集、加了過濾條件），筆記卻寫成全體；
- 數字、結論與工具輸出不符；
- 把未拍板的提議寫成已決定，或反過來。

不要算成錯誤的情況：
- 摘錄裡沒有出現、無法對照的內容（沒證據不等於錯，標成 unknown 也不要列為問題）；
- 寫入之後環境或決定才改變（那是過期，不是記錯）；
- 用字精簡、省略細節，但意思沒變。

只回傳一個 JSON 物件，不要其他文字：
{"verdict": "mismatch" | "ok" | "unknown", "issues": [{"claim": "筆記裡錯的那句（逐字）", "evidence_line": 行號, "why": "一句話：對話裡實際是什麼"}]}
verdict 為 ok 或 unknown 時 issues 給空陣列。

=== 筆記 ===
{note}

=== 對話摘錄 ===
{excerpt}
"""


def render_excerpt(items):
    return '\n\n'.join(f'[L{line} {role}]\n{text}' for line, role, text in items)


def build_prompt(note_text, items):
    return JUDGE_PROMPT.replace('{note}', note_text.strip()).replace('{excerpt}', render_excerpt(items))


def relay_key():
    for line in KEYS_ENV.read_text(encoding='utf-8').splitlines():
        if line.startswith('CLIPROXY_KEY_CC='):
            return line.split('=', 1)[1].strip().strip('"\'')
    raise SystemExit(f'CLIPROXY_KEY_CC not found in {KEYS_ENV}')


def call_model(alias, prompt, key, timeout=600, max_tokens=16000):
    """呼叫 relay 上一對一的 alias；回傳 (content, usage, model, error)。"""
    import urllib.error
    import urllib.request
    body = json.dumps({'model': alias, 'max_tokens': max_tokens,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(RELAY + '/chat/completions', data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return '', None, None, f'http {exc.code}: {exc.read()[:300].decode("utf-8", "replace")}'
    except Exception as exc:  # 逾時、連線中斷都要記下來，回測要算失敗率
        return '', None, None, f'{type(exc).__name__}: {exc}'
    choice = (data.get('choices') or [{}])[0]
    content = (choice.get('message') or {}).get('content') or ''
    return content, data.get('usage'), data.get('model'), None


def parse_verdict(content):
    """模型偶爾會包 code fence 或前後加字；取第一個完整 JSON 物件。"""
    text = content.strip()
    start = text.find('{')
    while start != -1:
        depth = 0
        for idx in range(start, len(text)):
            if text[idx] == '{':
                depth += 1
            elif text[idx] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:idx + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict) and obj.get('verdict') in ('mismatch', 'ok', 'unknown'):
                        return obj
                    break
        start = text.find('{', start + 1)
    return None


def cmd_extract(args):
    note_name = Path(args.note).name
    events, write_line = parse_transcript(args.transcript, note_name)
    note_text = Path(args.note_file).read_text(encoding='utf-8') if args.note_file else ''
    items = excerpt(events, write_line, args.mode, args.user_cap, args.near_cap, args.near_turns,
                    note_text, args.anchor_cap)
    print(json.dumps({'note': args.note, 'write_line': write_line, 'mode': args.mode,
                      'chars': sum(len(t) for _, _, t in items),
                      'segments': [{'line': ln, 'role': r, 'text': t} for ln, r, t in items]},
                     ensure_ascii=False))


REPORTS = Path.home() / 'code' / 'social-info' / 'reports' / 'local-analysis'
STATE_DIR = REPORTS / 'memory-origin'
GPT_QUOTA = re.compile(r'GPT 週額度.*?\n\s*週額度\s*(\d+)%.*?(\d\d)-(\d\d) (\d\d):(\d\d) 重設', re.S)


def note_key(rel):
    return rel.replace('/', '__').removesuffix('.md')


def scan_targets(transcripts):
    for path in note_paths():
        text = path.read_text(encoding='utf-8', errors='replace')
        origin = origin_of(text)
        if origin and origin in transcripts:
            yield path.relative_to(MEMORY_ROOT).as_posix(), text, transcripts[origin], 'first'


def recent_writers(since_epoch, root=None):
    """筆記檔名 → 之後最後一次寫它的對話；只翻 since 之後有變動的 jsonl。"""
    root = root if root is not None else PROJECTS_ROOT
    writers = {}
    for path in root.glob('*/*.jsonl'):
        try:
            if path.stat().st_mtime < since_epoch:
                continue
        except OSError:
            continue
        with open(path, encoding='utf-8', errors='replace') as fh:
            for raw in fh:
                if '/.claude/memory/' not in raw or '"tool_use"' not in raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                ts = row.get('timestamp') or ''
                for block in ((row.get('message') or {}).get('content') or []):
                    if not isinstance(block, dict) or block.get('type') != 'tool_use':
                        continue
                    if block.get('name') not in WRITE_TOOLS:
                        continue
                    target = str((block.get('input') or {}).get('file_path') or '')
                    if '/.claude/memory/' in target:
                        name = Path(target).name
                        if ts >= writers.get(name, ('', None))[0]:
                            writers[name] = (ts, path)
    return {name: path for name, (_, path) in writers.items()}


def changed_targets(since_epoch, transcripts, cap):
    """since 之後改過的筆記，最舊的先查；對照最後修改它的對話，找不到才退回建立它的對話。

    回傳 (targets, 下次的起點)：沒處理完的留給下一輪，起點停在最後處理的那條的修改時間。
    """
    changed = []
    for path in note_paths():
        mtime = path.stat().st_mtime
        if mtime > since_epoch:
            changed.append((mtime, path))
    changed.sort()
    writers = recent_writers(since_epoch)
    targets, next_since = [], since_epoch
    for mtime, path in changed:
        if len(targets) >= cap:
            break
        text = path.read_text(encoding='utf-8', errors='replace')
        rel = path.relative_to(MEMORY_ROOT).as_posix()
        writer = writers.get(path.name)
        origin = origin_of(text)
        if writer:
            targets.append((rel, text, writer, 'last'))
        elif origin and origin in transcripts:
            targets.append((rel, text, transcripts[origin], 'first'))
        next_since = mtime
    if len(targets) < cap:
        next_since = max([since_epoch] + [m for m, _ in changed])
    return targets, next_since


def gpt_quota():
    """讀 relay-status 的 GPT 週額度；讀不到回 (None, None)，呼叫端照常跑但不擋。"""
    import datetime as dt
    out = subprocess.run(['relay-status'], capture_output=True, text=True, check=False, timeout=120).stdout
    match = GPT_QUOTA.search(out)
    if not match:
        return None, None
    pct, mon, day, hh, mm = (int(x) for x in match.groups())
    now = dt.datetime.now()
    reset = dt.datetime(now.year, mon, day, hh, mm)
    if reset < now - dt.timedelta(days=1):
        reset = reset.replace(year=now.year + 1)
    return pct, reset


class GptGuard:
    """GPT 週額度到門檻就暫停到重設後：用滿會讓 Codex 與 ccp-gpt 整條路線停擺到重設。"""

    def __init__(self, threshold, every, log):
        import threading
        self.threshold, self.every, self.log = threshold, every, log
        self.calls = 0
        self.lock = threading.Lock()

    def wait_if_needed(self):
        import datetime as dt
        import time
        with self.lock:
            self.calls += 1
            if self.calls % self.every != 1:
                return
            pct, reset = gpt_quota()
            if pct is None or pct < self.threshold:
                return
            resume = reset + dt.timedelta(minutes=10)
            self.log(f'GPT 週額度 {pct}% ≥ {self.threshold}%，暫停到 {resume:%m-%d %H:%M}')
            while dt.datetime.now() < resume:
                time.sleep(300)
            self.log('GPT 額度已重設，繼續')


def scan_one(rel, text, transcript, which, alias, label, run_dir, key, guard):
    out = run_dir / label / f'{note_key(rel)}.json'
    if out.exists():
        return 'skip'
    import time
    events, write_line = parse_transcript(transcript, Path(rel).name, which)
    items = excerpt(events, write_line, 'all', note_text=text)
    prompt = build_prompt(text, items)
    record = None
    for attempt in range(3):
        if guard:
            guard.wait_if_needed()
        t0 = time.time()
        content, usage, model, error = call_model(alias, prompt, key)
        record = {'note': rel, 'transcript': str(transcript), 'alias': alias, 'attempt': attempt + 1,
                  'seconds': round(time.time() - t0, 1), 'usage': usage, 'error': error,
                  'verdict': parse_verdict(content), 'raw': content if not parse_verdict(content) else '',
                  'write_line': write_line, 'prompt_chars': len(prompt)}
        if not error and record['verdict']:
            break
        time.sleep(30 * (attempt + 1))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return 'ok' if record['verdict'] else 'fail'


def cmd_scan(args):
    import concurrent.futures as cf
    import time
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'progress.log'

    def log(msg):
        with open(log_path, 'a', encoding='utf-8') as fh:
            fh.write(time.strftime('%m-%d %H:%M:%S ') + msg + '\n')

    meta_path = run_dir / 'meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    transcripts = transcript_index()
    if args.scope == 'all':
        targets = list(scan_targets(transcripts))
        # 起點用第一次開始掃描的時刻：掃描期間被改的筆記交給下一次增量；中斷後續跑不能把起點往後推。
        meta.setdefault('next_since', meta.get('started_epoch', int(time.time())))
    else:
        since = json.loads(Path(args.cursor).read_text(encoding='utf-8'))['since_epoch']
        targets, next_since = changed_targets(since, transcripts, args.cap)
        meta.setdefault('next_since', next_since)
    meta.update({'scope': args.scope, 'models': args.model, 'targets': len(targets)})
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    if args.limit:
        targets = targets[:args.limit]
    key = relay_key()
    log(f'start: {len(targets)} notes, models {args.model}')
    pools, futures = [], []
    for spec in args.model:
        label, alias = spec.split('=', 1)
        guard = GptGuard(args.gpt_guard, 20, log) if 'codex' in alias and args.gpt_guard > 0 else None
        pool = cf.ThreadPoolExecutor(max_workers=args.workers)
        pools.append(pool)
        futures += [(label, pool.submit(scan_one, rel, text, tr, which, alias, label, run_dir, key, guard))
                    for rel, text, tr, which in targets]
    counts = {}
    for idx, (label, fut) in enumerate(futures, 1):
        status = fut.result()
        counts[(label, status)] = counts.get((label, status), 0) + 1
        if idx % 50 == 0:
            log(f'progress {idx}/{len(futures)} ' + json.dumps({f'{k[0]}:{k[1]}': v for k, v in counts.items()}))
    for pool in pools:
        pool.shutdown()
    summary = {f'{k[0]}:{k[1]}': v for k, v in counts.items()}
    log('done ' + json.dumps(summary))
    print(json.dumps(summary))


def cmd_commit_cursor(args):
    """報告寫好才推進起點；中途失敗時下一輪重查同一批。"""
    meta = json.loads((Path(args.run_dir) / 'meta.json').read_text(encoding='utf-8'))
    # 第一輪全量用的舊版 meta 只記 started_epoch。
    since = meta.get('next_since', meta.get('started_epoch'))
    cursor = Path(args.cursor)
    cursor.parent.mkdir(parents=True, exist_ok=True)
    cursor.write_text(json.dumps({'since_epoch': since, 'from_run': str(args.run_dir)}), encoding='utf-8')
    print(json.dumps({'since_epoch': since}))


def cmd_report(args):
    run_dir = Path(args.run_dir)
    labels = sorted(p.name for p in run_dir.iterdir() if p.is_dir())
    notes = {}
    for label in labels:
        for path in (run_dir / label).glob('*.json'):
            rec = json.loads(path.read_text(encoding='utf-8'))
            notes.setdefault(rec['note'], {})[label] = rec
    flagged, failed = [], []
    for rel, recs in sorted(notes.items()):
        if any((r.get('verdict') or {}).get('verdict') == 'mismatch' for r in recs.values()):
            flagged.append((rel, recs))
        if any(not r.get('verdict') for r in recs.values()):
            failed.append((rel, [lab for lab, r in recs.items() if not r.get('verdict')]))
    lines = [f'# 記憶筆記回原對話核對（{args.date}）', '',
             f'- 掃描筆記 {len(notes)} 條；模型 {", ".join(labels)}；任一模型判「記錯」就列出（聯集）。',
             f'- 被標出 {len(flagged)} 條；沒有可用回覆 {len(failed)} 條（見文末）。',
             '- 回測：兩個模型聯集在 32 題負例上抓到 12 條真的記錯、3 條只有誤報；誤報都出自 terra。',
             '', '## 需要確認的筆記', '',
             '修之前先讀筆記原檔與下列對話行號確認；不算錯就跳過並寫一句理由。只改寫錯的那句，不重寫整篇。', '']
    for rel, recs in flagged:
        lines.append(f'### [{rel}](file://{MEMORY_ROOT / rel})')
        transcript = next(iter(recs.values()))['transcript']
        lines.append(f'- 原對話：`{transcript}`')
        for label, rec in sorted(recs.items()):
            verdict = rec.get('verdict') or {}
            if verdict.get('verdict') != 'mismatch':
                lines.append(f'- {label}：{verdict.get("verdict", "無回覆")}')
                continue
            for issue in verdict.get('issues', []):
                lines.append(f'- {label}：「{issue.get("claim", "")}」→ L{issue.get("evidence_line", "?")}：{issue.get("why", "")}')
        lines.append('')
    if failed:
        lines += ['## 沒有可用回覆', '']
        lines += [f'- {rel}：{", ".join(labs)}' for rel, labs in failed]
    out = Path(args.out)
    if args.silent_if_empty and not flagged and not failed:
        # 每日 channel 只有全文剛好是 __SILENT__ 才算沒事，多一個字都會被當成要處理的報告。
        out.write_text('__SILENT__\n', encoding='utf-8')
    else:
        out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'notes': len(notes), 'flagged': len(flagged), 'failed': len(failed), 'out': str(out)}))


def cmd_candidates(args):
    transcripts = transcript_index()
    count = 0
    with open(args.out, 'w', encoding='utf-8') as fh:
        for path in note_paths():
            text = path.read_text(encoding='utf-8', errors='replace')
            hits = [ln.strip()[:400] for ln in text.splitlines() if CORRECTION.search(ln)]
            if not hits:
                continue
            origin = origin_of(text)
            if not origin or origin not in transcripts:
                continue
            rel = path.relative_to(MEMORY_ROOT).as_posix()
            commit, date = first_version(f'memory/{rel}')
            fh.write(json.dumps({
                'note': rel, 'origin': origin, 'transcript': str(transcripts[origin]),
                'first_commit': commit, 'first_date': date, 'correction_lines': hits,
            }, ensure_ascii=False) + '\n')
            count += 1
    print(json.dumps({'candidates': count, 'out': args.out}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='cmd', required=True)
    cand = sub.add_parser('candidates', help='挑出有更正紀錄、來源對話還在的筆記')
    cand.add_argument('--out', required=True)
    cand.set_defaults(func=cmd_candidates)
    ext = sub.add_parser('extract', help='依抽法輸出一條筆記的原對話摘錄')
    ext.add_argument('--note', required=True)
    ext.add_argument('--transcript', required=True)
    ext.add_argument('--mode', choices=('user', 'near', 'anchor', 'both', 'all'), default='all')
    ext.add_argument('--note-file', help='筆記全文（考卷用寫入當下的 git 版本）；anchor／all 需要')
    ext.add_argument('--anchor-cap', type=int, default=12000)
    ext.add_argument('--user-cap', type=int, default=12000)
    ext.add_argument('--near-cap', type=int, default=12000)
    ext.add_argument('--near-turns', type=int, default=3)
    ext.set_defaults(func=cmd_extract)
    scan = sub.add_parser('scan', help='全量核對每條有來源對話的筆記（可中斷後重跑續接）')
    scan.add_argument('--run-dir', required=True)
    scan.add_argument('--model', action='append', required=True, help='label=alias，可重複')
    scan.add_argument('--workers', type=int, default=2)
    scan.add_argument('--gpt-guard', type=int, default=95,
                      help='GPT 週額度到這個百分比就暫停到重設；0＝不暫停（週額度用完改扣 credit）')
    scan.add_argument('--limit', type=int, default=0)
    scan.add_argument('--scope', choices=('all', 'changed'), default='all')
    scan.add_argument('--cursor', default=str(STATE_DIR / 'cursor.json'))
    scan.add_argument('--cap', type=int, default=30, help='增量每輪最多幾條；每日 channel 有 10 分鐘上限')
    scan.set_defaults(func=cmd_scan)
    cur = sub.add_parser('commit-cursor', help='報告落檔後把起點推進到這一輪的 next_since')
    cur.add_argument('--run-dir', required=True)
    cur.add_argument('--cursor', default=str(STATE_DIR / 'cursor.json'))
    cur.set_defaults(func=cmd_commit_cursor)
    rep = sub.add_parser('report', help='合併各模型結果，寫出要確認的筆記清單')
    rep.add_argument('--run-dir', required=True)
    rep.add_argument('--date', required=True)
    rep.add_argument('--out', required=True)
    rep.add_argument('--silent-if-empty', action='store_true')
    rep.set_defaults(func=cmd_report)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
