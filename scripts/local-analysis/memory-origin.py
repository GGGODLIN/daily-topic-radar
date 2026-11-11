#!/usr/bin/env python3
"""記憶筆記「回原對話核對」：找來源對話、抽原文摘錄、送模型核對（scan）與產生修正提案（fix）、套用修正（apply）。

判斷與改寫交給模型；定位、抽取、套用都是確定性的程式。
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


def first_json(content, accept):
    """模型偶爾會包 code fence 或前後加字；取第一個 accept() 成立的完整 JSON 物件。"""
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
                    if isinstance(obj, dict) and accept(obj):
                        return obj
                    break
        start = text.find('{', start + 1)
    return None


def parse_verdict(content):
    return first_json(content, lambda obj: obj.get('verdict') in ('mismatch', 'ok', 'unknown'))


FIX_PROMPT = """你是記憶筆記的修正員。兩位核對員拿下面這條 AI 助理寫的記憶筆記，對照它當初寫成的那段對話，標出幾處可能「寫入當下就記錯」的地方。核對員會誤報。

請逐條判斷，只修真的記錯的：
1. 先在對話摘錄裡找證據（核對員給的行號只是線索，可能指錯）。證據確實和筆記矛盾才算記錯；摘錄裡找不到證據、只是說法不同但意思沒錯、或是寫入之後情況才改變，一律不改。
2. 如果下面附了「寫入當下的版本」，而被標的句子不在那個版本裡，代表是之後其他對話加的，這段對話對照不了，不改。
3. 要改時只做最小改動：換掉錯的字詞、數字或範圍，保留原本的語氣、格式與其餘內容；不順手潤飾別處，不補充對話裡沒有的資訊。改後的內容必須能在對話摘錄裡找到依據。
4. 開頭 frontmatter 只有 description 可以改；originSessionId、name、metadata 等欄位記的是筆記本身，不是對話裡的事實，一律不改。

只回傳一個 JSON 物件，不要其他文字：
{"edits": [{"issue": 編號, "action": "fix" 或 "keep", "old": "筆記裡要換掉的原文（逐字照抄，長度要讓它在筆記裡只出現一次）", "new": "換上的文字", "evidence_line": 行號, "why": "一句話：對話裡實際是什麼，或為什麼不改"}]}
每個被標的問題都要回一筆；action 為 keep 時 old 與 new 給空字串。兩個問題指同一處錯誤時，一筆 fix、另一筆 keep 並說明同上。

=== 筆記（現行版本） ===
{note}
{first}
=== 核對員標出的問題 ===
{issues}

=== 對話摘錄 ===
{excerpt}
"""


def parse_edits(content):
    return first_json(content, lambda obj: isinstance(obj.get('edits'), list))


def flagged_issues(recs):
    """合併各模型標出的問題。不告訴修正員是誰標的、幾個人標到，免得它看人數下判斷。"""
    issues = []
    for label in sorted(recs):
        verdict = recs[label].get('verdict') or {}
        if verdict.get('verdict') != 'mismatch':
            continue
        for issue in verdict.get('issues') or []:
            issues.append({'label': label, 'claim': str(issue.get('claim', '')),
                           'evidence_line': issue.get('evidence_line'), 'why': str(issue.get('why', ''))})
    return issues


def fix_excerpt(events, write_line, note_text, issues, cap=3000):
    """核對員看過的摘錄，再補上它們引用、但摘錄沒抽到的行。"""
    items = excerpt(events, write_line, 'all', note_text=note_text)
    have = {ln for ln, _, _ in items}
    wanted = set()
    for issue in issues:
        try:
            wanted.add(int(issue['evidence_line']))
        except (TypeError, ValueError):
            continue
    extra = [(e['line'], e['role'], _clip(e['text'], cap)) for e in events if e['line'] in wanted - have]
    return sorted(set(items) | set(extra), key=lambda t: t[0])


def build_fix_prompt(note_text, first_text, issues, items):
    first = ''
    if first_text:
        first = f'\n=== 寫入當下的版本（與現行版本不同；只用來判斷哪些句子是後來才加的） ===\n{first_text.strip()}\n'
    listed = '\n'.join(f'[{idx}] 「{it["claim"]}」→ L{it["evidence_line"]}：{it["why"]}'
                       for idx, it in enumerate(issues, 1))
    # excerpt 最後換：對話原文裡可能剛好出現 {note} 之類的字樣。
    return (FIX_PROMPT.replace('{note}', note_text.strip()).replace('{first}', first)
            .replace('{issues}', listed).replace('{excerpt}', render_excerpt(items)))


def _in_metadata(note_text, pos):
    """pos 落在 frontmatter、而且不是 description 那一行。

    originSessionId、name、metadata 這些欄位記的是筆記本身，不是對話裡的事實；
    試修時兩個模型都把 originSessionId 改回較早的 session，改了反而讓核對找錯對話。
    """
    front = FRONTMATTER.match(note_text)
    if not front or pos >= front.end():
        return False
    line_start = note_text.rfind('\n', 0, pos) + 1
    return not note_text[line_start:pos + 1].lstrip().startswith('description:')


def check_edits(note_text, edits):
    """old 要在現行筆記裡剛好出現一次才能機械套用；不合格的保留下來交人看，不丟掉。"""
    checked = []
    for edit in edits:
        edit = dict(edit)
        if edit.get('action') == 'fix':
            old, new = edit.get('old') or '', edit.get('new')
            count = note_text.count(old) if old else 0
            edit['valid'] = count == 1 and isinstance(new, str) and new != old
            if not edit['valid']:
                edit['invalid_reason'] = ('old 不在筆記裡' if count == 0 else
                                          f'old 出現 {count} 次' if count > 1 else 'new 缺少或與 old 相同')
            elif _in_metadata(note_text, note_text.index(old)):
                edit['valid'] = False
                edit['invalid_reason'] = 'frontmatter 只改 description，其他欄位不改'
        checked.append(edit)
    return checked


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
    if not record['verdict']:
        # 失敗的不寫結果檔：續跑只跳過有結果的，限流或逾時的這條下一輪會自動重問。
        with open(run_dir / 'failures.jsonl', 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'note': rel, 'label': label, 'error': record['error'],
                                 'raw': record['raw'][:300]}, ensure_ascii=False) + '\n')
        return 'fail'
    out.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return 'ok'


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


def load_scan(run_dir):
    """掃描結果：note → {label: record}；label 是 run_dir 底下的子資料夾名。"""
    labels = sorted(p.name for p in run_dir.iterdir() if p.is_dir())
    notes = {}
    for label in labels:
        for path in (run_dir / label).glob('*.json'):
            rec = json.loads(path.read_text(encoding='utf-8'))
            notes.setdefault(rec['note'], {})[label] = rec
    return labels, notes


def cmd_report(args):
    run_dir = Path(args.run_dir)
    labels, notes = load_scan(run_dir)
    flagged, failed = [], []
    for rel, recs in sorted(notes.items()):
        if any((r.get('verdict') or {}).get('verdict') == 'mismatch' for r in recs.values()):
            flagged.append((rel, recs))
        if any(not r.get('verdict') for r in recs.values()):
            failed.append((rel, [lab for lab, r in recs.items() if not r.get('verdict')]))
    # 失敗的沒有結果檔，要從 failures.jsonl 補回來；之後重跑成功的就不算。
    fail_log = run_dir / 'failures.jsonl'
    if fail_log.exists():
        still = {}
        for line in fail_log.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row['label'] not in notes.get(row['note'], {}):
                still.setdefault(row['note'], set()).add(row['label'])
        known = {rel for rel, _ in failed}
        failed += [(rel, sorted(labs)) for rel, labs in sorted(still.items()) if rel not in known]
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


def bucket_of(recs):
    """哪些模型標了它：全部模型都標＝both，否則 <label>-only。"""
    flagged = sorted(label for label, rec in recs.items()
                     if (rec.get('verdict') or {}).get('verdict') == 'mismatch')
    if not flagged:
        return None
    return 'both' if len(recs) > 1 and len(flagged) == len(recs) else '+'.join(flagged) + '-only'


def write_time_text(rel):
    """筆記第一次進 git 的內容；搬過家而找不到時回 None，修正員就只看現行版本。"""
    commit, _ = first_version(f'memory/{rel}')
    if not commit:
        return None
    out = subprocess.run(['git', '-C', str(CLAUDE_REPO), 'show', f'{commit}:memory/{rel}'],
                         capture_output=True, text=True, check=False)
    return out.stdout if out.returncode == 0 else None


def body_of(text):
    return FRONTMATTER.sub('', text).strip()


def fix_one(note, note_text, first_text, issues, transcript, write_line, alias, out_path, fail_log, key):
    if out_path.exists():
        return 'skip'
    import time
    events, found = parse_transcript(transcript, Path(note).name)
    items = fix_excerpt(events, write_line or found, note_text, issues)
    prompt = build_fix_prompt(note_text, first_text, issues, items)
    parsed = None
    for attempt in range(3):
        t0 = time.time()
        content, usage, model, error = call_model(alias, prompt, key)
        parsed = None if error else parse_edits(content)
        if parsed:
            break
        time.sleep(30 * (attempt + 1))
    if not parsed:
        with open(fail_log, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'note': note, 'alias': alias, 'error': error, 'raw': content[:300]},
                                ensure_ascii=False) + '\n')
        return 'fail'
    # 存下當時的筆記全文：套用前比對，筆記被別的 session 改過就不套，避免蓋掉別人的修改。
    record = {'note': note, 'transcript': str(transcript), 'alias': alias, 'model': model,
              'seconds': round(time.time() - t0, 1), 'usage': usage, 'prompt_chars': len(prompt),
              'had_first_version': bool(first_text), 'issues': issues, 'note_text': note_text,
              'edits': check_edits(note_text, parsed['edits'])}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return 'ok'


def cmd_fix(args):
    import concurrent.futures as cf
    _, notes = load_scan(Path(args.run_dir))
    picked = [(rel, recs) for rel, recs in sorted(notes.items()) if bucket_of(recs) in args.bucket]
    if args.notes:
        wanted = set(Path(args.notes).read_text(encoding='utf-8').split())
        picked = [(rel, recs) for rel, recs in picked if rel in wanted]
    if args.limit:
        picked = picked[:args.limit]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs, gone = [], []
    for rel, recs in picked:
        path = MEMORY_ROOT / rel
        if not path.exists():
            gone.append(rel)
            continue
        text = path.read_text(encoding='utf-8')
        first = write_time_text(rel)
        first = first if first and body_of(first) != body_of(text) else None
        rec = next(iter(recs.values()))
        jobs.append((rel, text, first, flagged_issues(recs), rec['transcript'], rec.get('write_line')))
    key = relay_key()
    counts = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        for spec in args.model:
            label, alias = spec.split('=', 1)
            futures += [pool.submit(fix_one, rel, text, first, issues, tr, wl, alias,
                                    out_dir / label / f'{note_key(rel)}.json', out_dir / 'failures.jsonl', key)
                        for rel, text, first, issues, tr, wl in jobs]
        for fut in cf.as_completed(futures):
            status = fut.result()
            counts[status] = counts.get(status, 0) + 1
    print(json.dumps({'notes': len(jobs), 'gone': gone, **counts}, ensure_ascii=False))


CORRECTION_LINE = '> 更正行 {date}：「{old}」改成「{new}」，寫入當下就記錯（{why}）；現況來源：原對話 `{transcript}` L{line}'


def _short(text, cap=80):
    text = ' '.join(str(text).split())
    return text if len(text) <= cap else text[:cap] + '…'


def apply_edits(text, edits, date, transcript):
    """依序套用合格的修正，每處後面接一行更正行（格式見 feedback_memory_cluster_maintenance）。

    更正行要獨立成段，所以放在該段落結束（下一個空行）之後；改到 frontmatter 時放內文開頭，
    塞進 frontmatter 會弄壞它。回傳 (新內容, 套用的, 跳過的)。
    """
    applied, skipped = [], []
    source = str(transcript).replace(str(Path.home()), '~')
    for edit in edits:
        if edit.get('action') != 'fix' or not edit.get('valid'):
            continue
        old, new = edit['old'], edit['new']
        if text.count(old) != 1:
            skipped.append({**edit, 'skip_reason': '套用前面幾處後，old 已不存在或不唯一'})
            continue
        pos = text.index(old)
        text = text[:pos] + new + text[pos + len(old):]
        line = CORRECTION_LINE.format(date=date, old=_short(old), new=_short(new), why=_short(str(edit.get('why', '')).rstrip('。. '), 120),
                                      transcript=source, line=edit.get('evidence_line', '?'))
        front = FRONTMATTER.match(text)
        if front and pos < front.end():
            text = text[:front.end()] + line + '\n\n' + text[front.end():].lstrip('\n')
        else:
            end = text.find('\n\n', pos + len(new))
            text = text.rstrip('\n') + '\n\n' + line + '\n' if end == -1 else text[:end] + '\n\n' + line + text[end:]
        applied.append(edit)
    return text, applied, skipped


def cmd_apply(args):
    fix_dir = Path(args.fix_dir)
    wanted = set(Path(args.notes).read_text(encoding='utf-8').split()) if args.notes else None
    log_path = fix_dir.parent / ('apply-preview.jsonl' if args.dry_run else 'applied.jsonl')
    stats = {}
    for path in sorted(fix_dir.glob('*.json')):
        rec = json.loads(path.read_text(encoding='utf-8'))
        if wanted is not None and rec['note'] not in wanted:
            continue
        target = MEMORY_ROOT / rec['note']
        current = target.read_text(encoding='utf-8') if target.exists() else None
        if current != rec['note_text']:
            # 提案之後筆記被改過（含已經套用過）：不套，留給下一輪增量重查。
            stats['changed_since_proposal'] = stats.get('changed_since_proposal', 0) + 1
            continue
        # 套用前用現行規則重驗：規則收緊後，先前產生的提案也要過新的檢查。
        new_text, applied, skipped = apply_edits(current, check_edits(current, rec['edits']), args.date,
                                                 rec['transcript'])
        if not applied:
            stats['nothing_to_apply'] = stats.get('nothing_to_apply', 0) + 1
            continue
        if args.dry_run:
            preview = fix_dir.parent / 'preview' / path.name.replace('.json', '.md')
            preview.parent.mkdir(parents=True, exist_ok=True)
            preview.write_text(new_text, encoding='utf-8')
        else:
            target.write_text(new_text, encoding='utf-8')
        stats['notes'] = stats.get('notes', 0) + 1
        stats['edits'] = stats.get('edits', 0) + len(applied)
        stats['skipped_edits'] = stats.get('skipped_edits', 0) + len(skipped)
        with open(log_path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps({'note': rec['note'], 'fix_file': str(path), 'applied': applied,
                                 'skipped': skipped}, ensure_ascii=False) + '\n')
    print(json.dumps(stats, ensure_ascii=False))


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
    fix = sub.add_parser('fix', help='對被標出的筆記產生修正提案（只寫提案檔，不動筆記）')
    fix.add_argument('--run-dir', required=True, help='scan 的結果資料夾')
    fix.add_argument('--out-dir', required=True)
    fix.add_argument('--model', action='append', required=True, help='label=alias，可重複')
    fix.add_argument('--bucket', action='append', required=True, help='both／<label>-only，可重複')
    fix.add_argument('--notes', help='只處理這個檔案列出的筆記（空白分隔的相對路徑）')
    fix.add_argument('--limit', type=int, default=0)
    fix.add_argument('--workers', type=int, default=5)
    fix.set_defaults(func=cmd_fix)
    app = sub.add_parser('apply', help='把修正提案套用到筆記，每處加一行更正行')
    app.add_argument('--fix-dir', required=True, help='fix 輸出的單一模型資料夾')
    app.add_argument('--date', required=True)
    app.add_argument('--notes')
    app.add_argument('--dry-run', action='store_true', help='改寫結果寫到 preview/，不動筆記')
    app.set_defaults(func=cmd_apply)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
