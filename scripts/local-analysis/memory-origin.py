#!/usr/bin/env python3
"""記憶筆記「回原對話核對」：找來源對話、抽原文摘錄、送模型核對（scan）與產生修正提案（fix）、套用修正（apply）。

判斷與改寫交給模型；定位、抽取、套用都是確定性的程式。
"""
import argparse
import datetime as dt
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

MEMORY_ROOT = Path.home() / '.claude' / 'memory'
CLAUDE_REPO = Path.home() / '.claude'
PROJECTS_ROOT = Path.home() / '.claude' / 'projects'
ORIGIN = re.compile(r'^[ \t]*originSessionId:[ \t]*(.*)$', re.M)
SESSION_ID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', re.I)
ORIGIN_FRONTMATTER = re.compile(r'\A---[ \t]*\r?\n(?:.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)', re.S)
INDEX_NAME = re.compile(r'(^|/)(_index_[^/]*|_INDEX|MEMORY|_orphan_registry)\.md$')
# 寫入當下就錯的更正，用語通常帶「初版／寫錯／誤解」；純漂移多半只寫「已改／已退役」。
CORRECTION = re.compile(r'更正|初版|寫錯|誤解|誤判|誤記|搞錯|是錯的|原本寫|原寫')
CORRECTION_LINE_BLOCK = re.compile(r'^[ \t]*>[ \t]*更正行[^\n]*(?:\n|$)', re.M)


def strip_correction_lines(note_text):
    """核對與抽錨點前移除既有的更正行：更正行保留了舊錯句引文與『寫入當下就記錯』字樣，留著會觸發重報回音。"""
    return re.sub(r'\n{3,}', '\n\n', CORRECTION_LINE_BLOCK.sub('', note_text))


def note_paths(root=None):
    """archive/ 與點開頭資料夾刻意不收：那裡本來就是歷史，同 memory-conflict。"""
    root = root if root is not None else MEMORY_ROOT
    for path in sorted(root.rglob('*.md')):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        parts = rel.split('/')
        if any(p == 'archive' or p.startswith('.') for p in parts[:-1]):
            continue
        if INDEX_NAME.search(rel):
            continue
        yield path


def origin_of(text):
    front = ORIGIN_FRONTMATTER.match(text)
    fields = ORIGIN.findall(front.group()) if front else []
    if len(fields) != 1:
        return None
    value = fields[0].strip()
    if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
        value = value[1:-1].strip()
    return value.lower() if SESSION_ID.fullmatch(value) else None


def transcript_index(root=None):
    """session id → 主對話 jsonl；subagents/ 底下的不算來源對話。"""
    root = root if root is not None else PROJECTS_ROOT
    index, ambiguous = {}, set()
    for path in root.glob('*/*.jsonl'):
        if not path.is_file():
            continue
        key = path.stem.lower()
        if key in index:
            try:
                if index[key].resolve() != path.resolve():
                    ambiguous.add(key)
            except OSError:
                ambiguous.add(key)
        index.setdefault(key, path)
    return {key: path for key, path in index.items() if key not in ambiguous}


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
                text = block.get('text')
                if isinstance(text, str):
                    parts.append(text)
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
            if not isinstance(row, dict):
                continue
            kind = row.get('type')
            msg = row.get('message')
            if not isinstance(msg, dict):
                continue
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
                            raw_text = block.get('text')
                            if not isinstance(raw_text, str):
                                continue
                            text = SYSTEM_BLOCK.sub('', raw_text).strip()
                            if text and not text.startswith(NOT_HUMAN):
                                events.append({'line': lineno, 'role': 'user', 'text': text})
            elif kind == 'assistant' and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get('type') == 'text':
                        raw_text = block.get('text')
                        if isinstance(raw_text, str) and raw_text.strip():
                            events.append({'line': lineno, 'role': 'assistant', 'text': raw_text})
                    elif block.get('type') == 'tool_use':
                        inp = block.get('input')
                        if not isinstance(inp, dict):
                            inp = {}
                        name = block.get('name')
                        if not isinstance(name, str):
                            name = ''
                        target = str(inp.get('file_path') or inp.get('notebook_path') or '')
                        if name in WRITE_TOOLS and target.endswith('/' + note_name) and (
                                write_line is None or which == 'last'):
                            write_line = lineno
                        events.append({'line': lineno, 'role': 'tool_use',
                                       'text': f'{name} ' + json.dumps(inp, ensure_ascii=False)})
    return events, write_line


FRONTMATTER = re.compile(r'\A---[ \t]*\r?\n(?:.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)', re.S)
NUMBER = re.compile(r'\d+\.\d+|\d{2,}')
YEARISH = re.compile(r'^(19|20)\d\d$')
CODEISH = re.compile(r'`([^`\n]{3,60})`')


def anchors_of(note_text):
    """筆記內文的數字與程式碼片段；frontmatter 不算，裡面的 session id 會亂命中。"""
    body = FRONTMATTER.sub('', note_text)
    nums = {n for n in NUMBER.findall(body) if not YEARISH.match(n)}
    codes = {c.strip() for c in CODEISH.findall(body)}
    return nums | codes


def _anchor_segments_indexed(events, anchors, cap, top=8, min_hits=3, lookback=8):
    if not anchors:
        return []
    scored = []
    for idx, e in enumerate(events):
        if e['role'] != 'tool_result':
            continue
        hits = sum(1 for a in anchors if a in e['text'])
        if hits >= min_hits:
            scored.append((hits, e['line'], idx, e))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    chosen, budget = [], cap
    for _, line, hit_idx, hit in scored[:top]:
        group = [(idx, e) for idx, e in enumerate(events)
                 if e['role'] == 'tool_use' and line - lookback <= e['line'] < line]
        group.append((hit_idx, hit))
        texts = [(idx, e['line'], e['role'], _clip(e['text'], 2500 if e['role'] == 'tool_result' else 1500))
                 for idx, e in group]
        size = sum(len(t) for _, _, _, t in texts)
        if size > budget:
            continue
        budget -= size
        chosen.extend(texts)
    return chosen


def anchor_segments(events, anchors, cap, top=8, min_hits=3, lookback=8):
    """筆記裡的數字多半抄自某次工具輸出；找出命中最多的輸出，連同產生它的指令一起帶上。

    限制條件（例如只掃大檔）常寫在指令裡、離寫筆記很遠，近窗抽不到。
    不要改成依線索稀有度加權或把指令也納入計分：回測覆蓋率反而從 10/16 掉到 6～7/16。
    """
    return [(ln, role, text) for _, ln, role, text in
            _anchor_segments_indexed(events, anchors, cap, top=top, min_hits=min_hits, lookback=lookback)]


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

    def add_pick(idx, line, role, text):
        prev = picked.get(idx)
        if prev is None or len(text) > len(prev[2]):
            picked[idx] = (line, role, text)

    if mode in ('anchor', 'all'):
        for idx, line, role, text in _anchor_segments_indexed(events, anchors_of(note_text), anchor_cap):
            add_pick(idx, line, role, text)
    if mode in ('user', 'both', 'all'):
        users = [(idx, e) for idx, e in enumerate(events) if e['role'] == 'user']
        # 超過上限時保留離寫入點最近的，因為改寫原意的那句通常就在寫之前或剛寫完。
        users.sort(key=lambda item: (abs(item[1]['line'] - end), item[0]))
        budget = user_cap
        for idx, e in users:
            text = _clip(e['text'], 2000)
            if len(text) > budget:
                continue
            budget -= len(text)
            add_pick(idx, e['line'], e['role'], text)
    if mode in ('near', 'both', 'all'):
        before = [(idx, e) for idx, e in enumerate(events) if e['line'] <= end]
        start_idx, seen = 0, 0
        for pos_idx in range(len(before) - 1, -1, -1):
            if before[pos_idx][1]['role'] == 'user':
                seen += 1
                if seen == near_turns:
                    start_idx = pos_idx
                    break
        budget = near_cap
        window = before[start_idx:]
        # 從寫入點往回填，預算用完就停：離寫入點越近越相關。
        chosen = []
        for idx, e in reversed(window):
            cap = 1500 if e['role'] == 'tool_result' else (800 if e['role'] == 'tool_use' else 2000)
            text = _clip(e['text'], cap)
            if len(text) > budget:
                break
            budget -= len(text)
            chosen.append((idx, e['line'], e['role'], text))
        for idx, line, role, text in chosen:
            add_pick(idx, line, role, text)
    return [picked[idx] for idx in sorted(picked, key=lambda i: (picked[i][0], i))]


RELAY = 'http://127.0.0.1:8317/v1'
KEYS_ENV = Path.home() / '.cli-proxy-api' / 'keys.env'
CLAUDE_BIN = Path.home() / '.local' / 'bin' / 'claude'
NATIVE_CONFIG_DIRS = (
    Path.home() / '.claude',
    Path.home() / '.claude-team-s',
    Path.home() / '.claude-team-p',
)
QUOTA_CACHE_DIR = Path.home() / '.claude' / 'cache'
NATIVE_STRIP_PREFIXES = ('ANTHROPIC_', 'CLAUDE_CODE_', 'CLAUDECODE', 'CLAUDE_PID', 'CLAUDE_EFFORT')

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


def _fill_template(template, values):
    pattern = re.compile('|'.join(re.escape(k) for k in values))
    return pattern.sub(lambda m: values[m.group(0)], template)


def render_excerpt(items):
    return '\n\n'.join(f'[L{line} {role}]\n{text}' for line, role, text in items)


def build_prompt(note_text, items):
    return _fill_template(JUDGE_PROMPT, {
        '{note}': note_text.strip(),
        '{excerpt}': render_excerpt(items),
    })


def relay_key():
    for line in KEYS_ENV.read_text(encoding='utf-8').splitlines():
        if line.startswith('CLIPROXY_KEY_CC='):
            return line.split('=', 1)[1].strip().strip('"\'')
    raise SystemExit(f'CLIPROXY_KEY_CC not found in {KEYS_ENV}')


def _native_email_of(config_dir):
    meta = Path.home() / '.claude.json' if config_dir == Path.home() / '.claude' else config_dir / '.claude.json'
    if not meta.exists():
        return None
    try:
        data = json.loads(meta.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None
    acct = data.get('oauthAccount')
    return acct.get('emailAddress') if isinstance(acct, dict) else None


def _native_quota_blocked(email):
    if not email:
        return None
    safe = email.replace('@', '_at_').replace('/', '_')
    cache = QUOTA_CACHE_DIR / f'quota-{safe}.json'
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding='utf-8')).get('data') or {}
    except (OSError, json.JSONDecodeError):
        return None
    now = dt.datetime.now(dt.timezone.utc)
    for win, limit in (('five_hour', 95), ('seven_day', 99)):
        entry = data.get(win)
        if not isinstance(entry, dict):
            continue
        util = entry.get('utilization')
        if not isinstance(util, (int, float)) or util < limit:
            continue
        resets_at = entry.get('resets_at')
        if isinstance(resets_at, str):
            try:
                if dt.datetime.fromisoformat(resets_at.replace('Z', '+00:00')) <= now:
                    continue
            except ValueError:
                pass
        return f'{email} {win}={util}%'
    return None


def _call_native_claude(model, prompt, timeout=600):
    """走本機原生 Claude 訂閱（輪詢有餘額的 config dir），失敗或全額滿時回傳錯誤供呼叫端退回 relay。"""
    if not CLAUDE_BIN.exists():
        return '', None, None, f'native claude missing: {CLAUDE_BIN}'
    base_env = {k: v for k, v in os.environ.items() if not k.startswith(NATIVE_STRIP_PREFIXES)}
    base_env['CC_VENDOR'] = 'headless-channel'
    base_env['CLAUDE_CODE_MAX_RETRIES'] = '0'
    default_dir = Path.home() / '.claude'
    seen_emails = set()
    errors = []
    for cfg_dir in NATIVE_CONFIG_DIRS:
        if not cfg_dir.exists():
            continue
        email = _native_email_of(cfg_dir)
        if email:
            if email in seen_emails:
                continue
            seen_emails.add(email)
            blocked = _native_quota_blocked(email)
            if blocked:
                errors.append(f'quota_blocked({blocked})')
                continue
        env = dict(base_env)
        if cfg_dir != default_dir:
            env['CLAUDE_CONFIG_DIR'] = str(cfg_dir)
        cmd = [str(CLAUDE_BIN), '--model', model, '--output-format', 'json',
               '--no-session-persistence', '--tools', '', '--setting-sources', '', '-p']
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, timeout=timeout)
        except Exception as exc:
            errors.append(f'{cfg_dir.name}: {type(exc).__name__}: {exc}')
            continue
        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else None
        except json.JSONDecodeError:
            payload = None
        if proc.returncode == 0 and isinstance(payload, dict) and payload.get('is_error') is False \
                and payload.get('subtype') == 'success' and isinstance(payload.get('result'), str) \
                and payload['result'].strip():
            served = next(iter((payload.get('modelUsage') or {}).keys()), model)
            tag = f'native:{served}@{email}' if email else f'native:{served}'
            return payload['result'], payload.get('usage'), tag, None
        reason = (payload.get('result') if isinstance(payload, dict) else '') or proc.stderr.strip() or f'exit {proc.returncode}'
        errors.append(f'{email or cfg_dir.name}: {str(reason)[:160]}')
    return '', None, None, 'native failed: ' + '; '.join(errors or ['no config dir'])


def _call_relay(alias, prompt, key, timeout=600, max_tokens=16000):
    import urllib.error
    import urllib.request
    body = json.dumps({'model': alias, 'max_tokens': max_tokens,
                       'messages': [{'role': 'user', 'content': prompt}]}).encode()
    req = urllib.request.Request(RELAY + '/chat/completions', data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key,
        'User-Agent': 'memory-origin'})
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


def call_model(alias, prompt, key, timeout=600, max_tokens=16000):
    """呼叫模型或逗號分隔的備援鏈（支援 native:<model> 走本機訂閱，失敗自動退下一棒）；回傳 (content, usage, model, error)。"""
    parts = [p.strip() for p in alias.split(',') if p.strip()]
    errors = []
    for part in parts:
        if part.startswith('native:'):
            content, usage, model, err = _call_native_claude(part.split(':', 1)[1], prompt, timeout=timeout)
        else:
            content, usage, model, err = _call_relay(part, prompt, key, timeout=timeout, max_tokens=max_tokens)
        if not err and content.strip():
            return content, usage, model, None
        errors.append(f'{part} -> {err or "empty reply"}')
    return '', None, None, ' | '.join(errors)


def first_json(content, accept):
    """模型偶爾會包 code fence 或前後加字；取第一個 accept() 成立的完整 JSON 物件。"""
    text = content.strip()
    decoder = json.JSONDecoder()
    start = text.find('{')
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find('{', start + 1)
            continue
        if isinstance(obj, dict) and accept(obj):
            return obj
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
        verdict = recs[label].get('verdict')
        if not isinstance(verdict, dict) or verdict.get('verdict') != 'mismatch':
            continue
        raw_issues = verdict.get('issues')
        if not isinstance(raw_issues, list):
            continue
        for issue in raw_issues:
            if not isinstance(issue, dict):
                continue
            issues.append({'label': label, 'claim': str(issue.get('claim', '')),
                           'evidence_line': issue.get('evidence_line'), 'why': str(issue.get('why', ''))})
    return issues


def fix_excerpt(events, write_line, note_text, issues, cap=3000):
    """核對員看過的摘錄，再補上它們引用、但摘錄沒抽到的行。"""
    items = excerpt(events, write_line, 'all', note_text=note_text)
    have = {ln for ln, _, _ in items}
    wanted = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        try:
            wanted.add(int(issue['evidence_line']))
        except (TypeError, ValueError, KeyError):
            continue
    extra = [(e['line'], e['role'], _clip(e['text'], cap)) for e in events if e['line'] in wanted - have]
    return sorted(set(items) | set(extra), key=lambda t: t[0])


def build_fix_prompt(note_text, first_text, issues, items):
    first = ''
    if first_text:
        first = f'\n=== 寫入當下的版本（與現行版本不同；只用來判斷哪些句子是後來才加的） ===\n{first_text.strip()}\n'
    listed = '\n'.join(f'[{idx}] 「{it["claim"]}」→ L{it["evidence_line"]}：{it["why"]}'
                       for idx, it in enumerate(issues, 1))
    return _fill_template(FIX_PROMPT, {
        '{note}': note_text.strip(),
        '{first}': first,
        '{issues}': listed,
        '{excerpt}': render_excerpt(items),
    })


def _in_metadata(note_text, pos, old='', new=''):
    """pos 落在 frontmatter、而且不是 description 那一行。

    originSessionId、name、metadata 這些欄位記的是筆記本身，不是對話裡的事實；
    試修時兩個模型都把 originSessionId 改回較早的 session，改了反而讓核對找錯對話。
    """
    front = FRONTMATTER.match(note_text)
    if not front or pos >= front.end():
        return False
    if '\n' in old or '\r' in old or '\n' in new or '\r' in new:
        return True
    line_start = note_text.rfind('\n', 0, pos) + 1
    line_end = note_text.find('\n', line_start)
    if line_end == -1:
        line_end = len(note_text)
    if old and pos + len(old) > line_end:
        return True
    orig_line = note_text[line_start:line_end].rstrip('\r')
    if not orig_line.lstrip().startswith('description:'):
        return True
    if old:
        replaced_line = (note_text[line_start:pos] + new + note_text[pos + len(old):line_end]).rstrip('\r')
        if not replaced_line.lstrip().startswith('description:'):
            return True
    return False


def check_edits(note_text, edits):
    """old 要在現行筆記裡剛好出現一次才能機械套用；不合格的保留下來交人看，不丟掉。"""
    if not isinstance(edits, list):
        return []
    checked = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        edit = dict(edit)
        if edit.get('action') == 'fix':
            old, new = edit.get('old'), edit.get('new')
            if not isinstance(old, str) or not old or not isinstance(new, str):
                edit['valid'] = False
                edit['invalid_reason'] = 'old 或 new 格式不正確'
            else:
                count = note_text.count(old)
                edit['valid'] = count == 1 and new != old
                if not edit['valid']:
                    edit['invalid_reason'] = ('old 不在筆記裡' if count == 0 else
                                              f'old 出現 {count} 次' if count > 1 else 'new 缺少或與 old 相同')
                elif _in_metadata(note_text, note_text.index(old), old, new):
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


def _parse_ts(ts):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return dt.datetime.fromisoformat(ts.replace('Z', '+00:00')).timestamp()
    except ValueError:
        return None


def _ts_after(ts, since_epoch):
    if since_epoch <= 0 or not ts:
        return True
    parsed = _parse_ts(ts)
    return True if parsed is None else parsed > since_epoch


def _scanned_note_keys(runs_root):
    keys = set()
    if not runs_root or not runs_root.is_dir():
        return keys
    for run in runs_root.iterdir():
        if not run.is_dir():
            continue
        for sub in run.iterdir():
            if not sub.is_dir():
                continue
            for js in sub.glob('*.json'):
                keys.add(js.stem)
    return keys


def recent_writers(since_epoch, root=None):
    """筆記相對路徑／檔名 → since 之後最後一次寫它的對話；只採計時間戳在 since 之後的寫入事件。"""
    root = root if root is not None else PROJECTS_ROOT
    writers = {}
    for path in root.glob('*/*.jsonl'):
        try:
            if path.stat().st_mtime < since_epoch:
                continue
        except OSError:
            continue
        try:
            with open(path, encoding='utf-8', errors='replace') as fh:
                for raw in fh:
                    if '/.claude/memory/' not in raw or '"tool_use"' not in raw:
                        continue
                    try:
                        row = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict):
                        continue
                    ts = row.get('timestamp')
                    if not _ts_after(ts, since_epoch):
                        continue
                    ts_epoch = _parse_ts(ts)
                    key_ts = ts_epoch if ts_epoch is not None else float('-inf')
                    msg = row.get('message')
                    if not isinstance(msg, dict):
                        continue
                    content = msg.get('content')
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not isinstance(block, dict) or block.get('type') != 'tool_use':
                            continue
                        if block.get('name') not in WRITE_TOOLS:
                            continue
                        inp = block.get('input')
                        if not isinstance(inp, dict):
                            continue
                        target = str(inp.get('file_path') or '')
                        if '/.claude/memory/' in target:
                            rel = os.path.normpath(target.split('/.claude/memory/', 1)[1]).replace(os.sep, '/')
                            if key_ts >= writers.get(rel, (float('-inf'), None))[0]:
                                writers[rel] = (key_ts, path)
        except OSError:
            continue
    return {k: path for k, (_, path) in writers.items()}


def changed_targets(since_epoch, transcripts, cap, runs_root=None):
    """since 之後改過的筆記，最舊的先查；對照最後修改它的對話，未曾掃過的新筆記才退回建立它的對話。

    回傳 (targets, 下次的起點)：沒處理完的留給下一輪，起點停在最後處理的那條的修改時間。
    """
    if runs_root is None and MEMORY_ROOT == Path.home() / '.claude' / 'memory':
        runs_root = STATE_DIR / 'runs'
    scanned = _scanned_note_keys(runs_root)
    changed = []
    for path in note_paths():
        mtime = path.stat().st_mtime
        if mtime > since_epoch:
            changed.append((mtime, path))
    changed.sort()
    writers = recent_writers(since_epoch)
    targets, next_since = [], since_epoch
    for mtime, path in changed:
        if len(targets) >= cap and mtime > next_since:
            break
        text = path.read_text(encoding='utf-8', errors='replace')
        rel = path.relative_to(MEMORY_ROOT).as_posix()
        writer = writers.get(rel) or writers.get(path.name)
        origin = origin_of(text)
        if writer:
            targets.append((rel, text, writer, 'last'))
        elif origin and origin in transcripts and note_key(rel) not in scanned:
            targets.append((rel, text, transcripts[origin], 'first'))
        next_since = mtime
    if len(targets) < cap:
        next_since = max([since_epoch] + [m for m, _ in changed])
    return targets, next_since


def gpt_quota():
    """讀 relay-status 的 GPT 週額度；讀不到回 (None, None)，呼叫端照常跑但不擋。"""
    try:
        out = subprocess.run(['relay-status'], capture_output=True, text=True, check=False, timeout=120).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    match = GPT_QUOTA.search(out)
    if not match:
        return None, None
    pct, mon, day, hh, mm = (int(x) for x in match.groups())
    now = dt.datetime.now()
    reset = None
    for offset in range(5):
        try:
            candidate = dt.datetime(now.year + offset, mon, day, hh, mm)
        except ValueError:
            continue
        if candidate >= now - dt.timedelta(days=1):
            reset = candidate
            break
    if reset is None:
        return None, None
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


_APPEND_LOCK = threading.Lock()


def _append_line(path, text):
    path = Path(path)
    with _APPEND_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(text)


def _atomic_write_text(path, text, preserve_stat=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', dir=path.parent, prefix=f'.{path.name}.', suffix='.tmp',
            encoding='utf-8', delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            fh.write(text)
            fh.flush()
        if preserve_stat is not None:
            os.chmod(tmp_path, stat.S_IMODE(preserve_stat.st_mode))
            os.utime(tmp_path, ns=(preserve_stat.st_atime_ns, preserve_stat.st_mtime_ns))
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def scan_one(rel, text, transcript, which, alias, label, run_dir, key, guard):
    out = run_dir / label / f'{note_key(rel)}.json'
    if out.exists():
        return 'skip'
    import time
    clean_text = strip_correction_lines(text)
    try:
        events, write_line = parse_transcript(transcript, Path(rel).name, which)
    except OSError as exc:
        _append_line(run_dir / 'failures.jsonl',
                     json.dumps({'note': rel, 'label': label, 'error': f'{type(exc).__name__}: {exc}',
                                 'raw': ''}, ensure_ascii=False) + '\n')
        return 'fail'
    items = excerpt(events, write_line, 'all', note_text=clean_text)
    prompt = build_prompt(clean_text, items)
    record = None
    for attempt in range(3):
        if guard:
            guard.wait_if_needed()
        t0 = time.time()
        content, usage, model, error = call_model(alias, prompt, key)
        record = {'note': rel, 'transcript': str(transcript), 'which': which, 'alias': alias, 'attempt': attempt + 1,
                  'seconds': round(time.time() - t0, 1), 'usage': usage, 'error': error,
                  'verdict': parse_verdict(content), 'raw': content if not parse_verdict(content) else '',
                  'write_line': write_line, 'prompt_chars': len(prompt)}
        if not error and record['verdict']:
            break
        time.sleep(30 * (attempt + 1))
    if not record['verdict']:
        # 失敗的不寫結果檔：續跑只跳過有結果的，限流或逾時的這條下一輪會自動重問。
        _append_line(run_dir / 'failures.jsonl',
                     json.dumps({'note': rel, 'label': label, 'error': record['error'],
                                 'raw': record['raw'][:300]}, ensure_ascii=False) + '\n')
        return 'fail'
    _atomic_write_text(out, json.dumps(record, ensure_ascii=False, indent=1))
    return 'ok'


def cmd_scan(args):
    import concurrent.futures as cf
    import time
    run_dir = Path(args.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / 'progress.log'

    def log(msg):
        _append_line(log_path, time.strftime('%m-%d %H:%M:%S ') + msg + '\n')

    meta_path = run_dir / 'meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8')) if meta_path.exists() else {}
    transcripts = transcript_index()
    if args.scope == 'all':
        targets = list(scan_targets(transcripts))
        if args.limit:
            targets = targets[:args.limit]
        # 起點用第一次開始掃描的時刻：掃描期間被改的筆記交給下一次增量；中斷後續跑不能把起點往後推。
        if meta.get('next_since') is None:
            started = meta.get('started_epoch')
            meta['next_since'] = started if started is not None else int(time.time())
    else:
        since = json.loads(Path(args.cursor).read_text(encoding='utf-8'))['since_epoch']
        effective_cap = min(args.cap, args.limit) if args.limit else args.cap
        targets, next_since = changed_targets(since, transcripts, effective_cap)
        if args.limit and len(targets) > args.limit:
            targets = targets[:args.limit]
            next_since = max([since] + [(MEMORY_ROOT / rel).stat().st_mtime
                                        for rel, *_ in targets if (MEMORY_ROOT / rel).exists()])
        meta.setdefault('prev_since', since)
        meta.setdefault('next_since', next_since)
        meta.setdefault('target_mtimes', {rel: (MEMORY_ROOT / rel).stat().st_mtime
                                          for rel, *_ in targets if (MEMORY_ROOT / rel).exists()})
    meta.update({'scope': args.scope, 'models': args.model, 'targets': len(targets)})
    _atomic_write_text(meta_path, json.dumps(meta, ensure_ascii=False))
    key = relay_key()
    log(f'start: {len(targets)} notes, models {args.model}')
    pools, futures = [], []
    counts = {}
    try:
        for spec in args.model:
            label, alias = spec.split('=', 1)
            guard = GptGuard(args.gpt_guard, 20, log) if 'codex' in alias and args.gpt_guard > 0 else None
            pool = cf.ThreadPoolExecutor(max_workers=args.workers)
            pools.append(pool)
            futures += [(label, pool.submit(scan_one, rel, text, tr, which, alias, label, run_dir, key, guard))
                        for rel, text, tr, which in targets]
        for idx, (label, fut) in enumerate(futures, 1):
            status = fut.result()
            counts[(label, status)] = counts.get((label, status), 0) + 1
            if idx % 50 == 0:
                log(f'progress {idx}/{len(futures)} ' + json.dumps({f'{k[0]}:{k[1]}': v for k, v in counts.items()}))
    finally:
        for pool in pools:
            pool.shutdown(wait=True, cancel_futures=True)
    summary = {f'{k[0]}:{k[1]}': v for k, v in counts.items()}
    log('done ' + json.dumps(summary))
    print(json.dumps(summary))


def cmd_commit_cursor(args):
    """報告寫好才推進起點；若當輪有筆記雙模型皆無可用回覆，起點停在最早無回覆筆記之前避免漏掃。"""
    run_dir = Path(args.run_dir)
    meta = json.loads((run_dir / 'meta.json').read_text(encoding='utf-8'))
    # 第一輪全量用的舊版 meta 只記 started_epoch。
    since = meta.get('next_since')
    if since is None:
        since = meta.get('started_epoch')
    target_mtimes = meta.get('target_mtimes') or {}
    if target_mtimes:
        _, notes = load_scan(run_dir)
        unanswered = [mtime for rel, mtime in target_mtimes.items()
                      if not any(r.get('verdict') for r in notes.get(rel, {}).values())]
        if unanswered:
            prev_since = meta.get('prev_since')
            if prev_since is None:
                prev_since = 0
            since = max(prev_since, min(unanswered) - 1e-6)
    cursor = Path(args.cursor)
    _atomic_write_text(cursor, json.dumps({'since_epoch': since, 'from_run': str(args.run_dir)}))
    print(json.dumps({'since_epoch': since}))


def load_scan(run_dir):
    """掃描結果：note → {label: record}；label 是 run_dir 底下的子資料夾名。"""
    labels = sorted(p.name for p in run_dir.iterdir() if p.is_dir())
    notes = {}
    for label in labels:
        for path in (run_dir / label).glob('*.json'):
            try:
                rec = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue
            if not isinstance(rec, dict) or not isinstance(rec.get('note'), str):
                continue
            notes.setdefault(rec['note'], {})[label] = rec
    return labels, notes


FIXERS = ('sol-high=group/legacy-codex-gpt-6-1-sol-high', 'swe2max=group/swe2max-pool', 'opus=native:opus,group/claude-opus-5-5')


def fix_steps(run_dir, date):
    """報告裡的修法：三個修正員都同意才改，讀報告的 session 照指令跑、不手改。

    2026-11-11 全量經驗：單一模型照核對員的問題清單改，抽 20 處改錯 3 處；
    Sol、SWE-2、Opus 都同意才改的樣本沒有改錯（見記憶 project_memory_origin_check_2026_11_10）。
    """
    script = Path(__file__).resolve()
    # 讀報告的 session 不一定在 repo 目錄下執行，路徑一律給絕對的。
    run_dir = Path(run_dir).resolve()
    fix_dir = STATE_DIR / 'fixes' / run_dir.name
    models = ' '.join(f'--model {spec}' for spec in FIXERS)
    apply = (f'python3 -I {script} apply --fix-dir {fix_dir}/sol-high --agree-with {fix_dir}/swe2max '
             f'--agree-with {fix_dir}/opus --date {date}')
    return ['## 怎麼修', '',
            '不要直接手改筆記：照下面三步，Sol high、SWE-2 Max、Opus 5.5 三個修正員都說要改的地方才改。',
            '',
            f'1. 產生提案（只寫提案檔，不動筆記）：`python3 -I {script} fix --run-dir {run_dir} --out-dir {fix_dir} {models} --bucket any`',
            f'2. 預覽：`{apply} --dry-run`，逐處對照 `{fix_dir}/apply-preview.jsonl` 的改法與證據行；'
            '改錯的那處，把 sol-high 提案檔裡同一個 issue 改成 `"action": "keep"`。',
            f'3. 套用：`{apply}`，再到 `~/.claude` commit `{fix_dir}/applied.jsonl` 列出的筆記。',
            '',
            '三個修正員沒有都同意、但你認為真的記錯的筆記，列進 digest 交使用者決定，不要手改。', '']


def cmd_report(args):
    run_dir = Path(args.run_dir)
    labels, notes = load_scan(run_dir)
    flagged, failed = [], []
    for rel, recs in sorted(notes.items()):
        if any(isinstance(r.get('verdict'), dict) and r['verdict'].get('verdict') == 'mismatch'
               for r in recs.values()):
            flagged.append((rel, recs))
        if any(not isinstance(r.get('verdict'), dict) for r in recs.values()):
            failed.append((rel, [lab for lab, r in recs.items() if not isinstance(r.get('verdict'), dict)]))
    # 失敗的沒有結果檔，要從 failures.jsonl 補回來；之後重跑成功的就不算。
    fail_log = run_dir / 'failures.jsonl'
    if fail_log.exists():
        still = {}
        for line in fail_log.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            if not isinstance(row, dict) or 'note' not in row or 'label' not in row:
                continue
            if not isinstance(row['note'], str) or not isinstance(row['label'], str):
                continue
            if row['label'] not in notes.get(row['note'], {}):
                still.setdefault(row['note'], set()).add(row['label'])
        known = {rel for rel, _ in failed}
        failed += [(rel, sorted(labs)) for rel, labs in sorted(still.items()) if rel not in known]
    lines = [f'# 記憶筆記回原對話核對（{args.date}）', '',
             f'- 掃描筆記 {len(notes)} 條；模型 {", ".join(labels)}；任一模型判「記錯」就列出（聯集）。',
             f'- 被標出 {len(flagged)} 條；沒有可用回覆 {len(failed)} 條（見文末）。',
             '- 回測：兩個模型聯集在 32 題負例上抓到 12 條真的記錯、3 條只有誤報；誤報都出自 terra。',
             '']
    if flagged:
        lines += fix_steps(run_dir, args.date)
    lines += ['## 需要確認的筆記', '']
    for rel, recs in flagged:
        label_md = rel.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
        uri_path = quote(str(MEMORY_ROOT / rel), safe='/:')
        lines.append(f'### [{label_md}](file://{uri_path})')
        transcript = next(iter(recs.values()))['transcript']
        lines.append(f'- 原對話：`{transcript}`')
        for label, rec in sorted(recs.items()):
            verdict = rec.get('verdict')
            if not isinstance(verdict, dict) or verdict.get('verdict') != 'mismatch':
                lines.append(f'- {label}：{verdict.get("verdict", "無回覆") if isinstance(verdict, dict) else "無回覆"}')
                continue
            raw_issues = verdict.get('issues')
            if not isinstance(raw_issues, list):
                continue
            for issue in raw_issues:
                if not isinstance(issue, dict):
                    continue
                lines.append(f'- {label}：「{issue.get("claim", "")}」→ L{issue.get("evidence_line", "?")}：{issue.get("why", "")}')
        lines.append('')
    if failed:
        lines += ['## 沒有可用回覆', '']
        lines += [f'- {rel}：{", ".join(labs)}' for rel, labs in failed]
    out = Path(args.out)
    if args.silent_if_empty and not flagged and not failed:
        # 每日 channel 只有全文剛好是 __SILENT__ 才算沒事，多一個字都會被當成要處理的報告。
        _atomic_write_text(out, '__SILENT__\n')
    else:
        _atomic_write_text(out, '\n'.join(lines) + '\n')
    print(json.dumps({'notes': len(notes), 'flagged': len(flagged), 'failed': len(failed), 'out': str(out)}))


def bucket_of(recs):
    """哪些模型標了它：全部模型都標＝both，否則 <label>-only。"""
    flagged = sorted(label for label, rec in recs.items()
                     if isinstance(rec.get('verdict'), dict) and rec['verdict'].get('verdict') == 'mismatch')
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


def fix_one(note, note_text, first_text, issues, transcript, write_line, alias, out_path, fail_log, key,
            which='first'):
    if out_path.exists():
        return 'skip'
    import time
    try:
        events, found = parse_transcript(transcript, Path(note).name, which)
    except OSError as exc:
        _append_line(fail_log,
                     json.dumps({'note': note, 'alias': alias, 'error': f'{type(exc).__name__}: {exc}', 'raw': ''},
                                ensure_ascii=False) + '\n')
        return 'fail'
    items = fix_excerpt(events, write_line or found, strip_correction_lines(note_text), issues)
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
        _append_line(fail_log,
                     json.dumps({'note': note, 'alias': alias, 'error': error, 'raw': content[:300]},
                                ensure_ascii=False) + '\n')
        return 'fail'
    # 存下當時的筆記全文：套用前比對，筆記被別的 session 改過就不套，避免蓋掉別人的修改。
    record = {'note': note, 'transcript': str(transcript), 'which': which, 'alias': alias, 'model': model,
              'seconds': round(time.time() - t0, 1), 'usage': usage, 'prompt_chars': len(prompt),
              'had_first_version': bool(first_text), 'issues': issues, 'note_text': note_text,
              'edits': check_edits(note_text, parsed['edits'])}
    _atomic_write_text(out_path, json.dumps(record, ensure_ascii=False, indent=1))
    return 'ok'


def cmd_fix(args):
    import concurrent.futures as cf
    _, notes = load_scan(Path(args.run_dir))
    picked = [(rel, recs) for rel, recs in sorted(notes.items())
              if bucket_of(recs) and ('any' in args.bucket or bucket_of(recs) in args.bucket)]
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
        rec = next(iter(recs.values()))
        origin = origin_of(text)
        which = rec.get('which') or (
            'last' if origin and Path(rec['transcript']).stem != origin else 'first')
        # 對照後續修改對話（which='last'）時不附建立筆記的首版，否則 FIX_PROMPT 第 2 條會拒修後來改壞的句子。
        first = write_time_text(rel) if which == 'first' else None
        first = first if first and body_of(first) != body_of(text) else None
        jobs.append((rel, text, first, flagged_issues(recs), rec['transcript'], rec.get('write_line'), which))
    key = relay_key()
    counts = {}
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        for spec in args.model:
            label, alias = spec.split('=', 1)
            futures += [pool.submit(fix_one, rel, text, first, issues, tr, wl, alias,
                                    out_dir / label / f'{note_key(rel)}.json', out_dir / 'failures.jsonl', key, which)
                        for rel, text, first, issues, tr, wl, which in jobs]
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
        if not isinstance(edit, dict) or edit.get('action') != 'fix' or not edit.get('valid'):
            continue
        old, new = edit.get('old'), edit.get('new')
        if not isinstance(old, str) or not old or not isinstance(new, str):
            continue
        if text.count(old) != 1:
            skipped.append({**edit, 'skip_reason': '套用前面幾處後，old 已不存在或不唯一'})
            continue
        pos = text.index(old)
        text = text[:pos] + new + text[pos + len(old):]
        line = CORRECTION_LINE.format(date=date, old=_short(old), new=_short(new), why=_short(str(edit.get('why', '')).rstrip('。. '), 120),
                                      transcript=source, line=edit.get('evidence_line', '?'))
        front = FRONTMATTER.match(text)
        if front and pos < front.end():
            prefix = text[:front.end()]
            if not prefix.endswith('\n'):
                prefix += '\n'
            text = prefix + line + '\n\n' + text[front.end():].lstrip('\r\n')
        else:
            end = text.find('\n\n', pos + len(new))
            text = text.rstrip('\n') + '\n\n' + line + '\n' if end == -1 else text[:end] + '\n\n' + line + text[end:]
        applied.append(edit)
    return text, applied, skipped


def agreed_fixes(edits, others):
    """只留其他修正員也對同一個問題說要改的 fix；改法用主修正員的。

    Terra 單獨標出的筆記只靠 Sol 修，抽 20 處改錯 3 處（兩個 GPT 家族一起讀錯）；
    加上別家模型都同意才改，同一份樣本沒有改錯。
    """
    if not isinstance(edits, list):
        return []

    def fixes(other):
        if not isinstance(other, list):
            return set()
        return {e.get('issue') for e in other if isinstance(e, dict) and e.get('action') == 'fix'}

    agreed = set.intersection(*(fixes(o) for o in others)) if others else None
    return [e for e in edits
            if isinstance(e, dict) and (e.get('action') != 'fix' or agreed is None or e.get('issue') in agreed)]


def cmd_apply(args):
    fix_dir = Path(args.fix_dir)
    wanted = set(Path(args.notes).read_text(encoding='utf-8').split()) if args.notes else None
    log_path = fix_dir.parent / ('apply-preview.jsonl' if args.dry_run else 'applied.jsonl')
    stats = {}
    for path in sorted(fix_dir.glob('*.json')):
        try:
            rec = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            stats['corrupt_fix'] = stats.get('corrupt_fix', 0) + 1
            continue
        if (not isinstance(rec, dict) or not isinstance(rec.get('note'), str)
                or not isinstance(rec.get('edits'), list)):
            stats['corrupt_fix'] = stats.get('corrupt_fix', 0) + 1
            continue
        if wanted is not None and rec['note'] not in wanted:
            continue
        if args.agree_with:
            # 其他修正員沒有這條的結果（失敗或沒被送去看）就當不同意，整條不改。
            other_paths = [Path(d) / path.name for d in args.agree_with]
            if not all(p.exists() for p in other_paths):
                stats['no_agreement'] = stats.get('no_agreement', 0) + 1
                continue
            other_edits = []
            corrupt_other = False
            for p in other_paths:
                try:
                    other_rec = json.loads(p.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError, KeyError, TypeError):
                    corrupt_other = True
                    break
                if not isinstance(other_rec, dict) or not isinstance(other_rec.get('edits'), list):
                    corrupt_other = True
                    break
                other_edits.append(other_rec['edits'])
            if corrupt_other:
                stats['no_agreement'] = stats.get('no_agreement', 0) + 1
                continue
            rec['edits'] = agreed_fixes(rec['edits'], other_edits)
        target = MEMORY_ROOT / rec['note']
        if target.is_symlink() or target.is_dir() or not target.resolve().is_relative_to(MEMORY_ROOT.resolve()):
            stats['invalid_target'] = stats.get('invalid_target', 0) + 1
            continue
        current = target.read_text(encoding='utf-8') if target.exists() else None
        if current != rec.get('note_text'):
            # 提案之後筆記被改過（含已經套用過）：不套，留給下一輪增量重查。
            stats['changed_since_proposal'] = stats.get('changed_since_proposal', 0) + 1
            continue
        # 套用前用現行規則重驗：規則收緊後，先前產生的提案也要過新的檢查。
        new_text, applied, skipped = apply_edits(current, check_edits(current, rec['edits']), args.date,
                                                 rec.get('transcript', ''))
        if not applied:
            stats['nothing_to_apply'] = stats.get('nothing_to_apply', 0) + 1
            continue
        if args.dry_run:
            preview = fix_dir.parent / 'preview' / path.name.replace('.json', '.md')
            _atomic_write_text(preview, new_text)
        else:
            st = target.stat()
            # 保留原 mtime：避免 apply 自己的寫回把剛修完的筆記排回下一輪增量隊尾製造回音。
            _atomic_write_text(target, new_text, preserve_stat=st)
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
    out_lines = []
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
        out_lines.append(json.dumps({
            'note': rel, 'origin': origin, 'transcript': str(transcripts[origin]),
            'first_commit': commit, 'first_date': date, 'correction_lines': hits,
        }, ensure_ascii=False) + '\n')
        count += 1
    _atomic_write_text(args.out, ''.join(out_lines))
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
    fix.add_argument('--bucket', action='append', required=True, help='both／<label>-only／any（任一模型標出），可重複')
    fix.add_argument('--notes', help='只處理這個檔案列出的筆記（空白分隔的相對路徑）')
    fix.add_argument('--limit', type=int, default=0)
    fix.add_argument('--workers', type=int, default=5)
    fix.set_defaults(func=cmd_fix)
    app = sub.add_parser('apply', help='把修正提案套用到筆記，每處加一行更正行')
    app.add_argument('--fix-dir', required=True, help='fix 輸出的單一模型資料夾')
    app.add_argument('--date', required=True)
    app.add_argument('--notes')
    app.add_argument('--dry-run', action='store_true', help='改寫結果寫到 preview/，不動筆記')
    app.add_argument('--agree-with', action='append', default=[],
                     help='其他修正員的結果資料夾，可重複；只套用它們也說要改的問題')
    app.set_defaults(func=cmd_apply)
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
