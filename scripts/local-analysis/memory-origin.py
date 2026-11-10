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


def note_paths(root=MEMORY_ROOT):
    """archive/ 與點開頭資料夾刻意不收：那裡本來就是歷史，同 memory-conflict。"""
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


def transcript_index(root=PROJECTS_ROOT):
    """session id → 主對話 jsonl；subagents/ 底下的不算來源對話。"""
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


def parse_transcript(path, note_name):
    """回傳事件串（含 1 起算的 jsonl 行號）與第一次寫這條筆記的行號。

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
                        if write_line is None and name in WRITE_TOOLS and target.endswith('/' + note_name):
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
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
