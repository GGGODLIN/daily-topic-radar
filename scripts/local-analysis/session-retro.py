#!/usr/bin/env python3
"""Local, snapshot-based retrospective CLI."""

import argparse
import ast
import copy
import importlib.util
import json
import math
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.dont_write_bytecode = True
PAGE_CHARS = 240000
MAX_PAGE_BYTES = 240000
MAX_PAGE_LINES = 1800
MAX_SHARD_PAGES = 2
# 超過 2 頁的大單在單跳 32,768 token 上限下易思考耗盡；僅尾頁不足半頁時併入前片，避免拆出單頁孤片而失去跨頁脈絡。
SHARD_TAIL_MERGE_BYTES = MAX_PAGE_BYTES // 2
SHARD_CHARS = MAX_SHARD_PAGES * PAGE_CHARS
STAGES = ("reading", "session-merge", "cross", "verify")
DESTINATIONS = ("kept", "merged", "reclassified", "rejected")
# action 是犯錯（或照做）的那個動作，只有它產生事件；context 是佐證（停掉指令、縮小範圍重查、讀存檔），歸到某個事件底下。
EVIDENCE_ROLES = ("action", "context")
ACCOUNTING_ERROR_CAP = 25
DEFAULT_MODEL = "gpt-6.1-sol[1m]"
DEFAULT_GEMINI_PRO_MODEL = "group/gemini-pro:medium"
DEFAULT_TERRA_MODEL = "gpt-6.1-terra"
DEFAULT_SOL_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORTS = {"read": "medium", "merge": "medium", "verify": "xhigh"}
DEFAULT_REPAIR_MODEL = "devin-plugin/swe-2:high"
WORKFLOW_TEMPLATE = Path.home() / ".claude/workflows/retro-workflow.js"
REFERENCES = [str(Path.home() / ".claude/references/retro-workflow" / name)
              for name in ("matt-retro.md", "writing-for-agents.md")]


class Invalid(ValueError):
  pass


def require(condition, message):
  if not condition:
    raise Invalid(message)


def moment(value):
  require(isinstance(value, str), "timestamp must be an ISO string")
  try:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError as error:
    raise Invalid("invalid timestamp: " + value) from error
  require(result.tzinfo is not None, "timestamp requires timezone")
  return result.astimezone(UTC)


def iso(value):
  return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def load(path):
  return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, data):
  path = Path(path)
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def new_dir(path):
  path = Path(path).expanduser().absolute()
  require(not path.exists(), "out must be a new directory")
  path.mkdir(parents=True, mode=0o700)
  return path


def records(path):
  for number, line in enumerate(Path(path).read_text(encoding="utf-8").split("\n"), 1):
    if not line.strip():
      continue
    try:
      row = json.loads(line)
      require(isinstance(row, dict), "record is not an object")
    except (ValueError, TypeError) as error:
      yield number, None, str(error)
      continue
    yield number, row, None


def queued(row):
  attachment = row.get("attachment")
  return isinstance(attachment, dict) and attachment.get("type") == "queued_command"


def active(row):
  return row.get("type") in {"user", "assistant"} or queued(row)


def content_text(content):
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    return "\n".join(block["text"] for block in content
                     if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
  return ""


SKIP_PREFIXES = ("[main]", "<task-notification>", "<system-reminder>", "<cross-session-message", "<local-command-stdout>")


def forwarded_input(row):
  if row.get("isSidechain") or row.get("isMeta") or row.get("type") != "user":
    return False
  text = content_text(row.get("message", {}).get("content", row.get("content", ""))).lstrip()
  return text.startswith(("[main]", "<cross-session-message"))


def human_input(row):
  if row.get("isSidechain") or row.get("isMeta"):
    return False
  origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
  if origin.get("kind") not in (None, "human"):
    return False
  if queued(row):
    text = content_text(row["attachment"].get("prompt", "")).strip()
    return bool(text) and not text.startswith(SKIP_PREFIXES)
  if row.get("type") != "user":
    return False
  text = content_text(row.get("message", {}).get("content", row.get("content", ""))).strip()
  if not text or text.startswith(SKIP_PREFIXES):
    return False
  return row.get("promptSource") == "typed" or (origin.get("kind") == "human" and text.startswith(("<command-name>", "<command-message>")))


def local_command_only_rows(rows):
  active_rows = [row for row in rows if active(row)]
  return bool(active_rows) and all(
    row.get("type") == "user" and not queued(row) and (
      row.get("isMeta") is True or content_text(row.get("message", {}).get("content", row.get("content", ""))).lstrip().startswith(
        ("<local-command-caveat>", "<local-command-stdout>", "<command-name>", "<command-message>")
      )
    )
    for row in active_rows
  )


def sdk_origin_row(row):
  # Newer non-interactive `claude -p` runs record turnOrigin/entrypoint instead of promptSource="sdk".
  return (row.get("promptSource") == "sdk"
          or row.get("turnOrigin") == "sdk"
          or row.get("entrypoint") in {"sdk-cli", "sdk-ts", "sdk-py"})


def classify(rows, source, limits=None):
  human = any(human_input(row) for row in rows)
  reasons = set()
  for row in rows:
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    if row.get("isSidechain") is True or row.get("agentId"):
      reasons.add("sidechain-or-agent")
    if any(row.get(key) is True for key in ("isSynthetic", "synthetic", "isEvaluation", "isTest")) or row.get("sessionKind") in {"synthetic", "evaluation", "eval"} or row.get("promptSource") in {"synthetic", "evaluation", "eval"}:
      reasons.add("evaluation")
    if row.get("sessionKind") in {"worker", "terminal-worker", "terminal_worker"} or origin.get("kind") in {"worker", "terminal-worker", "terminal_worker", "agent", "forwarded", "dispatch", "subagent"}:
      reasons.add("worker-or-forwarded")
    if forwarded_input(row):
      reasons.add("forwarded")
  if any(part in {"eval-roots", "synthetic-eval"} or "-T-skill-up-" in part or "-collie-canary-project" in part for part in source.parts):
    reasons.add("evaluation-path")
  if reasons:
    return "excluded", sorted(reasons)
  if human:
    return "human", []
  if any(sdk_origin_row(row) for row in rows):
    return "excluded", ["sdk-without-human"]
  if not any(active(row) for row in rows) and not limits:
    return "excluded", ["no-active-messages"]
  if local_command_only_rows(rows) and not limits:
    return "excluded", ["local-command-only"]
  return "unknown", ["human-origin-unconfirmed"]


def read_model_table():
  table = {"GPT_TERRA": DEFAULT_TERRA_MODEL, "GPT_SOL": DEFAULT_SOL_MODEL}
  path = Path.home() / ".claude/config/gpt-models.env"
  if path.exists():
    for line in path.read_text(encoding="utf-8").split("\n"):
      match = re.fullmatch(r"\s*(?:export\s+)?(GPT_TERRA|GPT_SOL)\s*=\s*['\"]?([A-Za-z0-9_.:/+\[\]-]+)['\"]?\s*", line)
      if match:
        table[match.group(1)] = re.sub(r"\[1m\]$", "", match.group(2))
  return table


def format_model(base, effort=None):
  base = re.sub(r"\[1m\]$", "", base)
  if effort and not re.search(r"\([a-z]+\)$", base):
    base = f"{base}({effort})"
  return base + "[1m]"


def read_model():
  return DEFAULT_GEMINI_PRO_MODEL


def latest_success(root, cutoff, scope="all"):
  times = []
  for path in Path(root).glob("*/completion.json"):
    try:
      data = load(path)
      if isinstance(data, dict) and data.get("schema_version") == 1 and data.get("scope") == scope and data.get("status") == "complete" and data.get("validation_pass") is True:
        end = moment(data["cutoff"])
        if moment(data["window_start"]) <= end <= cutoff and isinstance(data.get("run_id"), str):
          times.append(end)
    except (OSError, ValueError, KeyError, TypeError):
      continue
  return max(times) if times else None


def sanitizer():
  spec = importlib.util.spec_from_file_location("retro_readonly_audit", Path(__file__).with_name("session-audit.py"))
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def visible_blocks(row, audit=None):
  blocks = []
  media = 0
  if queued(row):
    prompt_text = content_text(row["attachment"].get("prompt", ""))
    blocks.append(("user-queued", audit.redact_text(prompt_text) if audit else prompt_text))
  content = row.get("message", {}).get("content", row.get("content"))
  role = str(row.get("type", "unknown"))
  if isinstance(content, str):
    blocks.append((role, audit.redact_text(content) if audit else content))
  elif isinstance(content, list):
    for block in content:
      if not isinstance(block, dict):
        continue
      kind = block.get("type")
      if kind in {"thinking", "redacted_thinking", "api-request-blob", "api_request"}:
        continue
      if kind in {"image", "document", "audio", "video"}:
        media += 1
        continue
      clean = copy.deepcopy(block) if audit else block
      if kind == "tool_result" and isinstance(clean.get("content"), list):
        kept = []
        for part in clean["content"]:
          if isinstance(part, dict) and part.get("type") in {"image", "document", "audio", "video"}:
            media += 1
          else:
            kept.append(part)
        clean = {**clean, "content": kept}
      if kind == "text":
        raw_text = str(clean.get("text", ""))
        text = audit.redact_text(raw_text) if audit else raw_text
      else:
        text = json.dumps(audit.redact_obj(clean) if audit else clean, ensure_ascii=False)
      blocks.append((role + ("-" + kind if kind != "text" and isinstance(kind, str) else ""), text))
  return blocks, media


def slice_page_chunks(text):
  if not text:
    return [""]
  chunks = []
  cur = []
  cur_bytes = 0
  cur_lines = 0
  for line in text.splitlines(keepends=True):
    line_bytes = len(line.encode("utf-8"))
    if cur_bytes + line_bytes <= MAX_PAGE_BYTES and cur_lines + 1 <= MAX_PAGE_LINES:
      cur.append(line)
      cur_bytes += line_bytes
      cur_lines += 1
      continue
    if line_bytes <= MAX_PAGE_BYTES:
      if cur:
        chunks.append("".join(cur))
      cur, cur_bytes, cur_lines = [line], line_bytes, 1
      continue
    start = 0
    while start < len(line):
      budget = MAX_PAGE_BYTES - cur_bytes if cur else MAX_PAGE_BYTES
      if budget <= 0:
        chunks.append("".join(cur))
        cur, cur_bytes, cur_lines = [], 0, 0
        budget = MAX_PAGE_BYTES
      end = min(len(line), start + budget)
      while len(line[start:end].encode("utf-8")) > budget and end > start + 1:
        end = start + max(1, (end - start) * budget // len(line[start:end].encode("utf-8")))
      piece = line[start:end]
      cur.append(piece)
      cur_bytes += len(piece.encode("utf-8"))
      cur_lines += 1
      start = end
      if start < len(line):
        chunks.append("".join(cur))
        cur, cur_bytes, cur_lines = [], 0, 0
  if cur:
    chunks.append("".join(cur))
  return chunks


def shard_page_chunks(text):
  chunks = slice_page_chunks(text)
  groups = [chunks[i:i + MAX_SHARD_PAGES] for i in range(0, len(chunks), MAX_SHARD_PAGES)]
  if len(groups) >= 2 and len(groups[-1]) == 1 and len(groups[-1][0].encode("utf-8")) < SHARD_TAIL_MERGE_BYTES:
    groups[-2].extend(groups.pop())
  return groups


def count_body_pages_and_units(body):
  groups = shard_page_chunks(body)
  return sum(len(group) for group in groups), len(groups)


def build_visible_events(numbered_rows, start=None, include_forwarded=True, audit=None):
  events = []
  seen = set()
  total_media = 0
  for line, stamp, obj in numbered_rows:
    blocks, media = visible_blocks(obj, audit)
    total_media += media
    message_id = obj.get("message", {}).get("id")
    for index, (label, text) in enumerate(blocks):
      key = (message_id, label, index, text)
      if label.startswith("assistant") and message_id and key in seen:
        continue
      seen.add(key)
      scope = "window" if (start is None or start <= stamp) else "older"
      events.append({"source_line": line, "event_time": iso(stamp), "label": label, "text": text,
                     "human": human_input(obj) or (include_forwarded and forwarded_input(obj)), "scope": scope})
  return events, total_media


def slice_window_events(events):
  current = [i for i, event in enumerate(events) if event["scope"] == "window"]
  first = min(current) if current else len(events)
  prior_humans = [i for i, event in enumerate(events[:first]) if event["human"]]
  context_start = prior_humans[-1] if prior_humans else first
  return events[context_start:]


def render_events_body(events):
  return "\n".join(f"[line={event['source_line']} timestamp={event['event_time']} {event['scope']} {event['label']}]\n{event['text']}\n"
                   for event in events)


def inventory(args):
  root = Path(args.projects_root).expanduser().resolve()
  cutoff = moment(args.now) if args.now else datetime.now(UTC)
  out = new_dir(args.out)
  private = out / "private"
  private.mkdir(mode=0o700)
  found = set()
  sessions = []
  active_rows_by_session = {}
  for alias in sorted(root.glob("*/*.jsonl")):
    source = alias.resolve()
    if source in found or not source.is_file():
      continue
    found.add(source)
    raw = source.read_bytes()
    selected = []
    numbered_active = []
    kept = []
    activities = []
    limits = []
    for number, encoded_line in enumerate(raw.split(b"\n"), 1):
      try:
        line = encoded_line.decode("utf-8")
      except UnicodeDecodeError:
        kept.append("")
        limits.append({"line": number, "reason": "invalid-utf8"})
        continue
      if not line.strip():
        kept.append("")
        continue
      try:
        row = json.loads(line)
        require(isinstance(row, dict), "record is not an object")
        stamp = moment(row["timestamp"]) if row.get("timestamp") else None
      except (ValueError, TypeError):
        kept.append("")
        limits.append({"line": number, "reason": "invalid-record-or-timestamp"})
        continue
      if stamp and stamp > cutoff:
        kept.append("")
        continue
      if active(row) and stamp is None:
        kept.append("")
        limits.append({"line": number, "reason": "activity-without-timestamp"})
        continue
      kept.append(line)
      selected.append(row)
      if active(row):
        activities.append(iso(stamp))
        numbered_active.append((number, stamp, row))
    category, reasons = classify(selected, source, limits)
    snapshot = private / f"s{len(sessions) + 1:02d}.jsonl"
    snapshot.write_text("\n".join(kept), encoding="utf-8")
    snapshot.chmod(0o600)
    inputs = [row for row in selected if human_input(row)]
    slash_only = bool(inputs) and all(not queued(row) and content_text(row.get("message", {}).get("content", "")).lstrip().startswith(("<command-name>", "<command-message>")) for row in inputs)
    project = next((row["cwd"] for row in reversed(selected) if human_input(row) and isinstance(row.get("cwd"), str)),
                   next((row["cwd"] for row in reversed(selected) if isinstance(row.get("cwd"), str)), str(source.parent)))
    if category == "human" or (category == "excluded" and reasons == ["forwarded"]):
      active_rows_by_session[source.stem] = numbered_active
    sessions.append({"session": source.stem, "source": str(source), "project": str(Path(project).expanduser().resolve()),
                     "snapshot": str(snapshot), "classification": category, "reasons": reasons,
                     "activity": activities, "slash_only": slash_only, "limitations": limits,
                     "estimated_chars": sum(len(line) for line in kept)})
  last = latest_success(args.reports_root, cutoff)
  options = {}
  for key, start in (("day", cutoff - timedelta(days=1)), ("last-success", last)):
    if start is None:
      options[key] = None
      continue
    selected = [row for row in sessions if row["classification"] == "human" and any(start <= moment(t) <= cutoff for t in row["activity"])]
    forwarded = [row for row in sessions if row["classification"] == "excluded" and row.get("reasons") == ["forwarded"] and any(start <= moment(t) <= cutoff for t in row["activity"])]
    projects = {}
    for row in selected:
      projects[row["project"]] = projects.get(row["project"], 0) + 1
    full_pages = full_units = win_pages = win_units = 0
    def_full_pages = def_full_units = def_win_pages = def_win_units = 0
    long_sessions = []
    for row in selected + forwarded:
      events, _ = build_visible_events(active_rows_by_session.get(row["session"], []), start=start)
      full_body = render_events_body(events)
      row["estimated_chars"] = len(full_body)
      fp, fu = count_body_pages_and_units(full_body)
      wp, wu = count_body_pages_and_units(render_events_body(slice_window_events(events)))
      def_full_pages += fp
      def_full_units += fu
      def_win_pages += wp
      def_win_units += wu
      if row["classification"] == "human":
        full_pages += fp
        full_units += fu
        win_pages += wp
        win_units += wu
        if fu > 1:
          long_sessions.append(row["session"])
    options[key] = {"window_start": iso(start), "cutoff": iso(cutoff), "sessions_count": len(selected),
                    "forwarded_sessions_count": len(forwarded), "forwarded_sessions": [row["session"] for row in forwarded],
                    "estimated_pages": full_pages,
                    "estimated_reader_units": full_units,
                    "window_estimated_pages": win_pages,
                    "window_estimated_reader_units": win_units,
                    "default_estimated_pages": def_full_pages,
                    "default_estimated_reader_units": def_full_units,
                    "default_window_estimated_pages": def_win_pages,
                    "default_window_estimated_reader_units": def_win_units,
                    "projects": projects, "long_sessions": long_sessions,
                    "estimate_basis": "visible blocks after omitting thinking/api-request-blob; redaction can slightly change length"}
  scoped_completions = []
  for completion_path in Path(args.reports_root).glob("*/completion.json"):
    try:
      completion = load(completion_path)
      scope = completion.get("scope")
      if isinstance(scope, dict):
        baseline = latest_success(args.reports_root, cutoff, scope)
        if baseline:
          scoped_completions.append({"scope": scope, "cutoff": iso(baseline)})
    except (OSError, ValueError, KeyError, TypeError):
      continue
  data = {"schema_version": 1, "cutoff": iso(cutoff), "projects_root": str(root),
          "scoped_completions": scoped_completions,
          "last_success": iso(last) if last else None, "options": options, "sessions": sessions,
          "unknown": [s for s in sessions if s["classification"] == "unknown"],
          "excluded": [s for s in sessions if s["classification"] == "excluded"], "default_model": read_model()}
  save(out / "inventory.json", data)
  return {"inventory_path": str(out / "inventory.json"), "options": options, "unknown_count": len(data["unknown"]), "excluded_count": len(data["excluded"]), "model": data["default_model"]}


def write_page_chunks(chunks, directory, prefix):
  directory.mkdir(parents=True, exist_ok=True)
  result = []
  for number, chunk in enumerate(chunks, 1):
    page_id = f"{prefix}-p{number:02d}"
    path = directory / f"p{number:02d}.txt"
    path.write_text(chunk + "\nEND_PAGE " + page_id + "\n", encoding="utf-8")
    result.append({"id": page_id, "path": str(path)})
  return result


def pages(text, directory, prefix):
  return write_page_chunks(slice_page_chunks(text), directory, prefix)


def prepare(args):
  data = load(args.inventory)
  require(data.get("schema_version") == 1, "unsupported inventory schema")
  cutoff = moment(data["cutoff"])
  project_filter = {str(Path(p).expanduser().resolve()) for p in args.project}
  requested_scope = "all" if not project_filter and not args.exclude_session else {"projects": sorted(project_filter), "excluded_sessions": args.exclude_session}
  if args.window == "last-success" and requested_scope != "all":
    candidates = [moment(c["cutoff"]) for c in data.get("scoped_completions", []) if c["scope"] == requested_scope]
    if data.get("last_success"):
      candidates.append(moment(data["last_success"]))
    require(bool(candidates), "no validated last-success baseline for selected scope")
    start = max(candidates)
  elif args.window == "custom":
    require(args.start is not None, "custom window requires --start")
    start = moment(args.start)
  else:
    option = data["options"][args.window]
    require(option is not None, "no validated global last-success baseline")
    start = moment(option["window_start"])
  require(start <= cutoff, "window start is after cutoff")
  project_filter = {str(Path(p).expanduser().resolve()) for p in args.project}
  selection = [row for row in data["sessions"]
               if (row["classification"] == "human" or (args.include_forwarded and row["classification"] == "excluded" and row.get("reasons") == ["forwarded"]))
               and (not project_filter or row["project"] in project_filter)
               and row["session"] not in args.exclude_session
               and any(start <= moment(t) <= cutoff for t in row["activity"])]
  require(bool(selection), "no human sessions in selected window")
  out = new_dir(args.out)
  audit = sanitizer()
  sessions = []
  canonical = {}
  total_media = 0
  for number, row in enumerate(selection, 1):
    name = f"s{number:02d}"
    home = out / name
    home.mkdir()
    events = []
    seen = set()
    for line, obj, error in records(row["snapshot"]):
      if error or not active(obj):
        continue
      stamp = moment(obj["timestamp"])
      if stamp > cutoff:
        continue
      blocks, media = visible_blocks(obj, audit)
      total_media += media
      message_id = obj.get("message", {}).get("id")
      for index, (label, text) in enumerate(blocks):
        key = (message_id, label, index, text)
        if label.startswith("assistant") and message_id and key in seen:
          continue
        seen.add(key)
        events.append({"source_line": line, "event_time": iso(stamp), "label": label, "text": text,
                       "human": human_input(obj) or (args.include_forwarded and forwarded_input(obj)), "scope": "window" if start <= stamp else "older"})
    context_events = []
    if args.depth == "window":
      current = [i for i, event in enumerate(events) if event["scope"] == "window"]
      first = min(current) if current else len(events)
      prior_humans = [i for i, event in enumerate(events[:first]) if event["human"]]
      context_start = prior_humans[-1] if prior_humans else first
      context_events = events[:context_start]
      events = events[context_start:]
    # canonical stays window-only so evidence cannot cite the on-demand context; earlier runs already read it.
    canonical[row["session"]] = events
    rendered = []
    requests = []
    for event in events:
      text = f"[line={event['source_line']} timestamp={event['event_time']} {event['scope']} {event['label']}]\n{event['text']}\n"
      rendered.append(text)
      if event["human"]:
        requests.append(text)
    document = home / "document.txt"
    document.write_text("\n".join(rendered), encoding="utf-8")
    request_text = "\n".join(requests) if requests else "No confirmed human request in this selected excerpt.\n"
    index_pages = pages(request_text, home / "requests", name + "-requests")
    index_paths = [page["path"] for page in index_pages]
    human_context = home / "human.txt"
    human_context.write_text(request_text, encoding="utf-8")
    context_ref = {}
    if context_events:
      context_document = home / "context.txt"
      context_document.write_text(render_events_body(context_events), encoding="utf-8")
      context_ref = {"context_document": str(context_document)}
    body = document.read_text(encoding="utf-8")
    shards = []
    for unit_number, page_chunks in enumerate(shard_page_chunks(body), 1):
      unit = f"{name}-u{unit_number:02d}"
      unit_dir = home / f"u{unit_number:02d}"
      page_list = write_page_chunks(page_chunks, unit_dir, unit)
      packet_path = unit_dir / "packet.json"
      report_path = out / "reports" / f"{unit}.json"
      packet = {"session": row["session"], "unit": unit, "source_document": str(document), **context_ref,
                "window_start": iso(start), "cutoff": iso(cutoff), "pages": page_list,
                "request_index_pages": index_paths, "report_path": str(report_path)}
      save(packet_path, packet)
      shards.append({"id": unit, "packet": str(packet_path), "report": str(report_path), "pages": page_list})
    session_report = out / "reports" / f"{name}-session.json"
    session_packet = home / "session.json"
    session = {"session": row["session"], "source": row["source"], "project": row["project"],
               "document": str(document), **context_ref, "human_context": str(human_context), "request_index_pages": index_paths,
               "shards": shards, "report": str(session_report), "packet": str(session_packet),
               "limitations": row["limitations"]}
    save(session_packet, {"session": row["session"], "unit": row["session"] + "-session",
                         "source_document": str(document), **context_ref, "input_reports": [s["report"] for s in shards],
                         "report_path": str(session_report)})
    sessions.append(session)
  table = read_model_table()
  efforts = {key: getattr(args, key + "_effort") or args.effort or DEFAULT_EFFORTS[key] for key in ("read", "merge", "verify")}
  models = {"read": args.read_model if args.read_model is not None else DEFAULT_GEMINI_PRO_MODEL,
            "merge": args.merge_model if args.merge_model is not None else DEFAULT_GEMINI_PRO_MODEL,
            "verify": args.verify_model if args.verify_model is not None else format_model(table["GPT_SOL"], efforts["verify"])}
  # Quote-only repairs find and copy one sentence; on the 16 historical cases the program could not settle,
  # free swe-2 passed all 16 after re-validation, reading ranges only (2026-11-05 replay).
  models["repair"] = args.repair_model if args.repair_model is not None else DEFAULT_REPAIR_MODEL
  scope ="all" if not project_filter and not args.exclude_session else {"projects": sorted(project_filter), "excluded_sessions": args.exclude_session}
  plan = {"schema_version": 1, "run_id": out.name, "scope": scope, "window_start": iso(start), "cutoff": iso(cutoff),
          "depth": args.depth, "focus": args.focus, "models": models, "effort": args.effort or DEFAULT_EFFORTS["read"],
          "efforts": efforts, "concurrency": args.concurrency,
          "sessions": sessions, "session_count": len(sessions), "shard_count": sum(len(s["shards"]) for s in sessions),
          "page_count": sum(len(u["pages"]) for s in sessions for u in s["shards"]), "media_omitted": total_media,
          "unknown": data["unknown"], "excluded": data["excluded"], "references": REFERENCES,
          "limits": {"page_chars": PAGE_CHARS, "shard_chars": SHARD_CHARS, "thinking_omitted": True,
                     "api_request_blobs_omitted": True, "duplicate_assistant_blocks_omitted": True,
                     "narrow_coverage": args.depth == "window", "media_not_read": True,
                     "redaction": "existing session-audit patterns only; not arbitrary private content"}}
  selected_ids = {row["session"] for row in selection}

  def in_window_scope(row):
    return ((not project_filter or row["project"] in project_filter)
            and row["session"] not in args.exclude_session
            and row["session"] not in selected_ids
            and any(start <= moment(t) <= cutoff for t in row.get("activity", [])))

  # Private inventory snapshots and full historical activity timestamp arrays are omitted from plan.json.
  plan["unknown"] = [{k: v for k, v in row.items() if k not in ("snapshot", "activity")}
                     for row in plan["unknown"] if in_window_scope(row)]
  plan["excluded"] = [{k: v for k, v in row.items() if k not in ("snapshot", "activity")}
                      for row in plan["excluded"] if in_window_scope(row)]
  save(out / "canonical.json", canonical)
  save(out / "plan.json", plan)
  return {"plan_path": str(out / "plan.json"), "session_count": plan["session_count"], "shard_count": plan["shard_count"],
          "page_count": plan["page_count"], "models": models, "window": {"window_start": iso(start), "cutoff": iso(cutoff)},
          "confirmation_required": True}


def plan_context(path):
  path = Path(path).resolve()
  plan = load(path)
  require(plan.get("schema_version") == 1, "unsupported plan schema")
  require(plan.get("run_id") == path.parent.name, "plan run identity mismatch")
  return plan, path.parent


def binding(plan, run):
  require(all(Path(path).resolve().is_relative_to(run) for s in plan["sessions"]
              for path in (s["document"], s.get("context_document", s["document"]))), "plan source outside run")
  return {"plan": plan}


def validated_path(run, unit):
  require(isinstance(unit, str) and re.fullmatch(r"[A-Za-z0-9_:-]+", unit), "unsafe unit id")
  return run / "validated" / (unit + ".json")


def verify_part_reuse(plan, run, context, unit):
  require(unit.get("pass") is True, "verify-current part failed: " + unit.get("unit", "unknown"))
  part = next((part for part in context["parts"] if part["unit"] == unit.get("unit")), None)
  require(part is not None, "verify-current parts unit changed")
  require(load(unit["validated_path"]) == unit["report"], "validated critic part changed: " + part["unit"])
  require(load(unit["actual_report_path"]) == unit["original_report"], "original critic part changed: " + part["unit"])
  transcript_dirs = list(dict.fromkeys([str(path) for path in part["transcript_dirs"]] + (unit.get("transcript_dirs") or [])))
  if not transcript_dirs and unit.get("agent"):
    transcript_dirs = [str(Path(unit["agent"]).resolve().parent)]
  require(transcript_dirs, "successful critic part has no transcript directories")
  agents = transcript_agents(transcript_dirs)
  item = {"session": "cross-session", "unit": part["unit"], "report_path": str(part["report_path"]),
          "input_reports": [str(validated_path(run, "cross-session"))]}

  item.update({key: part[key] for key in ("slice_path", "index_path", "pattern_ids") if key in part})
  agent, target, report = report_writer(agents, item)
  require(target == part["report_path"] and report == unit["original_report"], "critic part writer changed: " + part["unit"])
  canonical = load(run / "canonical.json")
  check_report(report, item, "verify", canonical)
  require(not critic_read_errors(agent, item, report, canonical, plan, reuse=True), "critic part reads changed: " + part["unit"])
  require({row.get("pattern_id") for row in report.get("verdicts", report.get("findings", []))} == set(part["pattern_ids"]),
          "critic part assignment changed: " + part["unit"])


def verify_parts_reuse_candidates(plan, run, context, result):
  units = {unit["unit"]: unit for unit in result.get("units", [])}
  require(set(units) == {part["unit"] for part in context["parts"]}, "verify-current parts units changed")
  reused = []
  failures = []
  for unit in units.values():
    if not unit.get("pass"):
      failures.append({"unit": unit["unit"], "errors": unit.get("errors", [])})
      continue
    try:
      verify_part_reuse(plan, run, context, unit)
      reused.append(unit["unit"])
    except (OSError, ValueError, KeyError, TypeError) as error:
      failures.append({"unit": unit["unit"], "errors": [str(error)]})
  return reused, failures


def prior_verify_parts(plan, run, must_pass=True):
  path = run / "validation-verify-current-parts.json"
  result = load(path)
  require(result.get("schema_version") == 1 and result.get("run_id") == plan["run_id"]
          and result.get("stage") == "verify-current-parts",
          "verify-current parts validation is missing or invalid")
  if must_pass:
    require(result.get("pass") is True, "verify-current parts validation is missing or failed")
  if (run / "composition-validation.json").exists():
    prior_composition(plan, run)
  else:
    prior_validation(plan, run, "cross")
  manifest_value = result.get("critic_parts_manifest", result.get("composition_manifest"))
  context = verify_parts_context(manifest_value, run, require_outputs=False,
                                 require_slices=not (run / "composition-validation.json").exists())
  require(context["manifest"] == result.get("manifest"), "critic parts manifest changed since validation")
  units = {unit["unit"]: unit for unit in result.get("units", [])}
  require(set(units) == {part["unit"] for part in context["parts"]}, "verify-current parts units changed")
  for part in context["parts"]:
    unit = units[part["unit"]]
    if not unit.get("pass"):
      require(not must_pass, "verify-current part failed: " + part["unit"])
      continue
    verify_part_reuse(plan, run, context, unit)
  if must_pass:
    require(load(result["validated_path"]) == result["report"], "composed verify-current report changed")
  return result


def prior_validation(plan, run, stage, must_pass=True, check_parts=True):
  path = run / f"validation-{stage}.json"
  require(path.exists(), "missing preceding validation: " + stage)
  result = load(path)
  require(result.get("run_id") == plan["run_id"] and result.get("stage") == stage, "validation identity mismatch")
  require(result.get("binding") == binding(plan, run), "source, plan, or models changed since validation")
  if stage == "verify" and check_parts and (run / "validation-verify-current-parts.json").exists():
    prior_verify_parts(plan, run, must_pass=must_pass)
  if stage == "verify" and (run / "composition-validation.json").exists():
    prior_composition(plan, run)
  if must_pass:
    require(result.get("pass") is True, "preceding validation failed: " + stage)
  for unit in result.get("units", []):
    if unit.get("pass") is True:
      require(load(unit["validated_path"]) == unit["report"], "validated report changed: " + unit["unit"])
      require(load(unit["actual_report_path"]) == unit["original_report"], "original report changed: " + unit["unit"])
  return result


def selected_report(plan, run, session):
  stage = "session-merge" if len(session["shards"]) > 1 else "reading"
  unit = session["session"] + "-session" if stage == "session-merge" else session["shards"][0]["id"]
  result = prior_validation(plan, run, stage)
  require(any(row["unit"] == unit and row["pass"] for row in result["units"]), "selected report not validated")
  return str(validated_path(run, unit))


def verify_parts_items(plan, run, context):
  documents = [s["document"] for s in plan["sessions"]]
  contexts = [s["context_document"] for s in plan["sessions"] if s.get("context_document")]
  items = []
  for part in context["parts"]:
    item = {"session": "cross-session", "unit": part["unit"], "report_path": str(part["report_path"]),
            "input_reports": [str(validated_path(run, "cross-session"))],
            "source_documents": documents, "pattern_ids": part["pattern_ids"],
            **({"context_documents": contexts} if contexts else {})}
    if "slice_path" in part:
      item.update({"slice_path": part["slice_path"], "index_path": part["index_path"]})
    items.append(item)
  return items


def stage_items(plan, run, stage, enforce=True):
  composition_enabled = (run / "composition-validation.json").exists()
  if enforce:
    for previous in STAGES[:STAGES.index(stage)]:
      if previous == "cross" and stage == "verify" and composition_enabled:
        prior_composition(plan, run)
      else:
        prior_validation(plan, run, previous)
  documents = [s["document"] for s in plan["sessions"]]
  contexts = {"context_documents": [s["context_document"] for s in plan["sessions"] if s.get("context_document")]}
  contexts = contexts if contexts["context_documents"] else {}

  def context(s):
    return {"context_document": s["context_document"]} if s.get("context_document") else {}

  if stage == "reading":
    return [{"session": s["session"], "unit": u["id"], "packet_path": u["packet"], "report_path": u["report"],
             "source_document": s["document"], **context(s), "request_index_pages": s["request_index_pages"]}
            for s in plan["sessions"] for u in s["shards"]]
  if stage == "session-merge":
    return [{"session": s["session"], "unit": s["session"] + "-session", "packet_path": s["packet"],
             "report_path": s["report"], "source_document": s["document"], **context(s),
             "request_index_pages": s["request_index_pages"],
             "input_reports": [str(validated_path(run, u["id"])) for u in s["shards"]]}
            for s in plan["sessions"] if len(s["shards"]) > 1]
  if stage == "cross":
    return [{"session": "cross-session", "unit": "cross-session", "report_path": str(run / "reports/cross-session.json"),
             "input_reports": [selected_report(plan, run, s) for s in plan["sessions"]], "source_documents": documents,
             **contexts}]
  cross_file = validated_path(run, "cross-session")
  span = primary_patterns_span(cross_file.read_text(encoding="utf-8")) if cross_file.is_file() else None
  primary_range = {"primary_read_range": {"offset": span[0], "limit": span[1] - span[0] + 1}} if span else {}
  return [{"session": "cross-session", "unit": unit, "report_path": str(run / "reports" / (unit + ".json")),
           "input_reports": [str(cross_file)], **primary_range, "source_documents": documents, **contexts}
          for unit in ("verify-source", "verify-current")]


def stage_args(args):
  plan, run = plan_context(args.plan)
  require(not args.critic_parts or args.stage == "verify", "critic parts require verify stage")
  critic_context = None
  parts_reused = []
  parts_failed = []
  if args.stage == "verify" and args.critic_parts:
    stage_items(plan, run, args.stage)
    critic_context = verify_parts_context(args.critic_parts, run, require_outputs=False)
    parts_validation = run / "validation-verify-current-parts.json"
    if parts_validation.exists():
      try:
        prior_parts = load(parts_validation)
        manifest_value = prior_parts.get("critic_parts_manifest", prior_parts.get("composition_manifest"))
        if Path(manifest_value).expanduser().resolve() == critic_context["path"]:
          require(prior_parts.get("run_id") == plan["run_id"] and prior_parts.get("binding") == binding(plan, run),
                  "critic parts validation identity changed")
          parts_reused, parts_failed = verify_parts_reuse_candidates(plan, run, critic_context, prior_parts)
      except (ValueError, OSError, KeyError, TypeError):
        parts_reused = []
    items = verify_parts_items(plan, run, critic_context)
    source_item = next(item for item in stage_items(plan, run, args.stage) if item["unit"] == "verify-source")
    items.insert(0, source_item)
  else:
    items = stage_items(plan, run, args.stage)
  failures = []
  reused = []
  validation_file = run / f"validation-{args.stage}.json"
  if validation_file.exists():
    try:
      previous = prior_validation(plan, run, args.stage, must_pass=False, check_parts=False if critic_context else True)
    except (ValueError, OSError, KeyError, TypeError):
      previous = None
      failures.append({"unit": "validation", "errors": ["previous validation stale or invalid; no units reused"]})
    if previous:
      reused = [unit["unit"] for unit in previous["units"] if unit["pass"]]
      if critic_context:
        reused = [unit for unit in parts_reused if unit in {item["unit"] for item in items}]
        reused.extend(unit["unit"] for unit in previous["units"] if unit["unit"] == "verify-source" and unit["pass"])
      failures.extend({"unit": unit["unit"], "errors": unit["errors"],
                       **({"quote_failures": unit["quote_failures"]} if unit.get("quote_failures") else {})}
                      for unit in previous["units"] if not unit["pass"])
      failures.extend(parts_failed)
      items = [item for item in items if item["unit"] not in reused]
  payload = {"stage": args.stage, "plan_path": str(Path(args.plan).resolve()), "models": plan["models"],
             "effort": plan.get("efforts", {}).get("read" if args.stage == "reading" else "verify" if args.stage == "verify" else "merge", plan["effort"]), "concurrency": plan["concurrency"], "items": items, "confirmed": False,
             "run_id": plan["run_id"], "focus": plan["focus"], "depth": plan["depth"],
             "window_start": plan["window_start"], "cutoff": plan["cutoff"], "references": plan["references"],
             "reused_units": reused, "failures": failures}
  template = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
  marker = "const preset = null"
  require(template.count(marker) == 1, "workflow template missing preset marker")
  embedded = json.dumps(payload, ensure_ascii=False).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
  script_path = run / f"workflow-{args.stage}.js"
  script_path.write_text(template.replace(marker, f"const preset = {embedded}", 1), encoding="utf-8")
  return {**payload, "workflow_script_path": str(script_path)}


def result_text(value):
  if isinstance(value, str):
    return value
  if isinstance(value, list):
    return "\n".join(result_text(block.get("text", block.get("content", "")))
                     for block in value if isinstance(block, dict))
  return ""


def transcript_agents(directories):
  agents = []
  seen = set()
  for directory in directories:
    root = Path(directory).resolve()
    require(root.is_dir(), "transcript directory missing")
    for path in sorted(root.rglob("agent-*.jsonl")):
      if path.resolve() in seen:
        continue
      seen.add(path.resolve())
      uses = {}
      results = {}
      messages = {}
      for _, row, error in records(path):
        require(not error, "malformed agent transcript: " + str(path))
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if row.get("type") == "assistant" and message.get("id"):
          messages[message["id"]] = message.get("usage")
        blocks = message.get("content", [])
        if not isinstance(blocks, list):
          continue
        for block in blocks:
          if not isinstance(block, dict):
            continue
          if row.get("type") == "assistant" and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
            uses[block["id"]] = block
          if row.get("type") == "user" and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
            results[block["tool_use_id"]] = block
      operations = []
      for key, use in uses.items():
        result = results.get(key)
        if not result or result.get("is_error") is True:
          continue
        operations.append({"name": use.get("name"), "input": use.get("input", {}),
                           "text": result_text(result.get("content")), "tool_id": key})
      agents.append({"path": str(path.resolve()), "operations": operations, "messages": messages, "tool_ids": set(uses)})
  return agents


def operation_path(operation):
  value = operation["input"].get("file_path", operation["input"].get("path"))
  return Path(value).expanduser().resolve() if isinstance(value, str) else None


def strip_read_prefix(line):
  match = re.match(r"^\s*\d+(?:\t|[→│|]) ?(.*)$", line)
  return match.group(1) if match else None


def read_lines(agent, path):
  path = Path(path).resolve()
  expected = path.read_text(encoding="utf-8").split("\n")
  covered = set()
  for op in agent["operations"]:
    if op["name"] != "Read" or operation_path(op) != path:
      continue
    # Only numbered, returned source lines count; an END_PAGE claim alone is not coverage.
    cursor = max(1, op["input"].get("offset", 1))
    for line in op["text"].split("\n"):
      match = re.match(r"^\s*(\d+)(?:\t|[→│|])(.*)$", line)
      if match:
        number, text = int(match.group(1)), match.group(2)
      else:
        number, text = cursor, line
      if 1 <= number <= len(expected) and text == expected[number - 1] and expected[number - 1].strip():
        covered.add(number)
      cursor = number + 1
  return covered


def transcript_read_lines(agent, path):
  # Reuse/finish checks run after users may edit or archive external workspace files cited in current_evidence;
  # verify that the recorded Read output itself returned a nonblank line at that line number.
  path = Path(path).expanduser().resolve()
  covered = set()
  for op in agent["operations"]:
    if op["name"] != "Read" or operation_path(op) != path:
      continue
    cursor = max(1, op["input"].get("offset", 1))
    for line in op["text"].split("\n"):
      match = re.match(r"^\s*(\d+)(?:\t|[→│|])(.*)$", line)
      if match:
        number, text = int(match.group(1)), match.group(2)
      else:
        number, text = cursor, line
      if number >= 1 and text.strip():
        covered.add(number)
      cursor = number + 1
  return covered


def coverage_errors(agent, item):
  packet = load(item["packet_path"])
  errors = []
  for page in packet["pages"] + packet["request_index_pages"]:
    path = Path(page["path"] if isinstance(page, dict) else page).resolve()
    required = {n for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1) if line.strip()}
    missing = required - read_lines(agent, path)
    if missing:
      errors.append(f"unread lines: {path} ({len(missing)})")
  return errors


def bash_report(operation, item):
  # Historical commands are untrusted data; only this bounded construction grammar is accepted.
  command = operation["input"].get("command", "")
  match = re.fullmatch(r"python3 -I - <<'PY'\n(.*)\nPY\s*", command, re.S)
  if not match:
    return None
  env = {}
  helper = ast.parse("def ev(n,q): return {'session':s,'source_line':n,'quote':q}").body[0]

  def shape(node):
    return ast.dump(node, include_attributes=False)

  def value(node):
    if isinstance(node, ast.Constant) and type(node.value) in (str, int, bool, type(None)):
      return node.value
    if isinstance(node, ast.Name) and node.id in env:
      return copy.deepcopy(env[node.id])
    if isinstance(node, ast.Dict) and all(k is not None for k in node.keys):
      return {value(k): value(v) for k, v in zip(node.keys, node.values, strict=True)}
    if isinstance(node, ast.List):
      return [value(v) for v in node.elts]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
      left, right = value(node.left), value(node.right)
      require(type(left) is type(right) is str, "unsupported addition")
      return left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
      left, right = value(node.left), value(node.right)
      require(isinstance(left, Path) and isinstance(right, str), "unsupported path")
      return left / right
    if isinstance(node, ast.Call) and not node.keywords:
      if isinstance(node.func, ast.Name) and node.func.id == "Path" and len(node.args) == 1:
        return Path(value(node.args[0]))
      if isinstance(node.func, ast.Name) and node.func.id == "ev" and "ev" in env and len(node.args) == 2:
        return {"session": env["s"], "source_line": value(node.args[0]), "quote": value(node.args[1])}
    if isinstance(node, ast.JoinedStr):
      parts = []
      for part in node.values:
        if isinstance(part, ast.Constant):
          parts.append(part.value)
        else:
          require(isinstance(part, ast.FormattedValue) and part.conversion == -1, "unsupported format")
          spec = value(part.format_spec) if part.format_spec else ""
          require(spec in ("", "02"), "unsupported format specifier")
          parts.append(format(value(part.value), spec))
      return "".join(parts)
    if isinstance(node, ast.ListComp) and len(node.generators) == 1:
      gen = node.generators[0]
      require(not gen.ifs and not gen.is_async and isinstance(gen.target, ast.Name), "unsupported comprehension")
      call = gen.iter
      require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "range"
              and not call.keywords and len(call.args) == 2, "unsupported range")
      start, stop = [value(arg) for arg in call.args]
      require(type(start) is type(stop) is int and 0 <= stop - start <= 1000, "unbounded range")
      result = []
      for number in range(start, stop):
        env[gen.target.id] = number
        result.append(value(node.elt))
      env.pop(gen.target.id, None)
      return result
    raise Invalid("unsupported report expression")

  try:
    tree = ast.parse(match[1])
    for index, node in enumerate(tree.body):
      if shape(node) in (shape(ast.parse("import json").body[0]), shape(ast.parse("from pathlib import Path").body[0])):
        continue
      if shape(node) == shape(helper):
        env["ev"] = True
        continue
      if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id not in {"ev", "json", "Path"}:
          assigned = value(node.value)
          require(not isinstance(node.value, ast.Name) or not isinstance(assigned, (dict, list)), "container aliases unsupported")
          env[target.id] = assigned
          continue
        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == "r":
          require(isinstance(env.get("r"), dict), "report assignment requires a dict")
          env["r"][value(target.slice)] = value(node.value)
          continue
      if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if (isinstance(call.func, ast.Attribute) and call.func.attr == "append" and not call.keywords
            and len(call.args) == 1 and shape(call.func.value) == shape(ast.parse("r['findings']", mode="eval").body)):
          env["r"]["findings"].append(value(call.args[0]))
          continue
      writer = ast.parse("with p.open('x') as f: json.dump(r,f,ensure_ascii=False,indent=2); f.write('\\n')").body[0]
      require(shape(node) == shape(writer), "unsupported report statement")
      target = env["p"].resolve()
      require(target == Path(item["report_path"]).resolve(), "Bash report target mismatch")
      suffix = tree.body[index + 1:]
      if suffix:
        packet = load(item["packet_path"])
        count = len(packet["pages"])
        page_dir = item["unit"].replace("-u", "/u")
        check = ("a=json.loads(p.read_text())\n"
          f"assert len(a['read_pages'])=={count} and not a['unread_pages']\n"
          "for finding in a['findings']:\n for e in finding['evidence']:\n"
          f"  text=''.join((root/f'{page_dir}/p{{i:02}}.txt').read_text() for i in range(1,{count + 1}))\n"
          '  assert f"[line={e[\'source_line\']} " in text\n'
          "  assert e['quote'] in text.replace('\\\\n','\\n').replace('\\\\\"','\"'),(e['source_line'],e['quote'])\n"
          "print(json.dumps({'report':str(p),'pages':len(a['read_pages']),'findings':len(a['findings']),'json_valid':True,'quotes_present':True},ensure_ascii=False))")
        require([shape(n) for n in suffix] == [shape(n) for n in ast.parse(check).body], "unsupported post-write statements")
      report = env["r"]
      require(report.get("unit") == item["unit"] and report.get("session") == item["session"], "Bash identity mismatch")
      return target, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
  except (ValueError, TypeError, KeyError, AttributeError, SyntaxError, OverflowError):
    return None
  return None


def report_writer(agents, item):
  candidates = []
  expected_path = Path(item["report_path"]).resolve()
  for agent_index, agent in enumerate(agents):
    paths = []
    bash_states = {}
    for op in agent["operations"]:
      if op["name"] == "Bash":
        reconstructed = bash_report(op, item)
        if reconstructed:
          target, state = reconstructed
          bash_states[target] = state
          if target not in paths:
            paths.append(target)
      if op["name"] in {"Write", "Edit"} and operation_path(op) is not None:
        target = operation_path(op)
        if target not in paths:
          paths.append(target)
    for target in paths:
      if not target.is_file():
        continue
      try:
        report = load(target)
      except (ValueError, OSError):
        continue
      expected_session = item.get("session")
      if not isinstance(report, dict) or report.get("unit") != item["unit"] or (expected_session is not None and report.get("session") != expected_session):
        continue
      # A receipt typo can point at a real report in this run, never at another run's output.
      run_root = expected_path.parent.parent
      if not target.is_relative_to(run_root):
        continue
      writes = [op for op in agent["operations"] if operation_path(op) == target and op["name"] in {"Read", "Write", "Edit"}]
      state = bash_states.get(target)
      for op in writes:
        fields = op["input"]
        if op["name"] == "Read":
          if state is None and re.fullmatch(r"verify-current-p\d+", item["unit"]):
            text = "\n".join(strip_read_prefix(line) if strip_read_prefix(line) is not None else line
                             for line in op["text"].split("\n"))
            try:
              json.loads(text)
              state = text
            except ValueError:
              pass
        elif op["name"] == "Write":
          state = fields.get("content")
        elif isinstance(state, str) and isinstance(fields.get("old_string"), str) and isinstance(fields.get("new_string"), str):
          old = fields["old_string"]
          if not old or (state.count(old) != 1 and not fields.get("replace_all")) or old not in state:
            state = None
            break
          state = state.replace(old, fields["new_string"], -1 if fields.get("replace_all") else 1)
      if state is None:
        continue
      for later in agents[agent_index + 1:]:
        for op in later["operations"]:
          fields = op["input"]
          if op["name"] != "Edit" or operation_path(op) != target:
            continue
          old, new = fields.get("old_string"), fields.get("new_string")
          if not isinstance(old, str) or not old or not isinstance(new, str) or state.count(old) != 1 or fields.get("replace_all"):
            state = None
            break
          state = state.replace(old, new, 1)
        if state is None:
          break
      if state is not None:
        try:
          if json.loads(state) != report:
            continue
        except (ValueError, TypeError):
          continue
      else:
        continue
      candidates.append((agent, target, report))
  require(bool(candidates), "no successful report Write/Edit/Bash evidence with matching identity and artifact")
  # Later transcript arguments are repair attempts; never union another writer's Read evidence.
  return candidates[-1]


def composition_path(run, value, label):
  require(isinstance(value, str) and value, label + " requires a path")
  path = Path(value).expanduser().resolve()
  require(path.is_relative_to(run), label + " outside run")
  require(path.is_file(), label + " missing: " + str(path))
  return path


def composition_transcript_dirs(values, label):
  require(isinstance(values, list) and values, label + " requires transcript directories")
  paths = []
  for value in values:
    require(isinstance(value, str) and value, label + " has an invalid transcript directory")
    path = Path(value).expanduser().resolve()
    require(path.is_dir(), label + " transcript directory missing: " + str(path))
    paths.append(path)
  return paths


def composition_context(manifest_path):
  manifest_path = Path(manifest_path).expanduser().resolve()
  data = load(manifest_path)
  require(isinstance(data, dict) and data.get("schema_version") == 1 and data.get("kind") == "cross-composition",
          "unsupported composition manifest")
  run = Path(data.get("run_path", "")).expanduser().resolve()
  require(run.is_dir() and manifest_path.parent == run, "composition manifest must live at run root")
  plan_path = composition_path(run, data.get("plan_path"), "plan_path")
  plan, plan_run = plan_context(plan_path)
  require(plan_run == run and data.get("run_id") == plan["run_id"], "composition run identity mismatch")
  require(data.get("plan_path") == str(plan_path), "composition plan path must be canonical")
  return data, run, plan, manifest_path


def composition_require_reads(agents, paths, label):
  for path in paths:
    path = Path(path).resolve()
    require(path.is_file(), label + " input missing: " + str(path))
    required = {number for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1) if line.strip()}
    covered = set()
    for agent in agents:
      covered.update(read_lines(agent, path))
    require(required <= covered, label + " did not fully read " + str(path))


def composition_writer(artifact, run, cache, label, expected_session=None):
  unit = artifact.get("unit")
  report_path = composition_path(run, artifact.get("report_path"), label + " report")
  dirs = composition_transcript_dirs(artifact.get("transcript_dirs"), label)
  key = tuple(str(path) for path in dirs)
  if key not in cache:
    cache[key] = transcript_agents(dirs)
  agents = cache[key]
  require(agents, label + " has no agent transcript")
  item = {"unit": unit, "report_path": str(report_path)}
  if expected_session is not None:
    item["session"] = expected_session
  agent, target, report = report_writer(agents, item)
  require(target == report_path, label + " report was not written at the manifest path")
  return {"agent": agent, "target": target, "report": report, "agents": agents, "writer_agent": agent, "dirs": dirs}


def composition_batch_report(artifact, source_reports, run, plan, canonical, cache, label):
  input_packets = [composition_path(run, path, label + " input packet") for path in artifact.get("input_packets", [])]
  source_reports = [Path(path).resolve() for path in source_reports]
  dirs = composition_transcript_dirs(artifact.get("transcript_dirs"), label)
  key = tuple(str(path) for path in dirs)
  if key not in cache:
    cache[key] = transcript_agents(dirs)
  result = composition_writer(artifact, run, cache, label, expected_session="cross-session")
  base = result["report"]
  composition_require_reads([result["writer_agent"]], input_packets + source_reports, label)
  require(base.get("unit") == artifact.get("unit"), label + " report identity mismatch")
  supplement = artifact.get("supplement")
  supplement_result = None
  combined = copy.deepcopy(base)
  if supplement is not None:
    supplement_paths = [composition_path(run, path, label + " supplement input") for path in supplement.get("input_packets", [])]
    supplement_report = composition_path(run, supplement.get("source_report"), label + " supplement source") if supplement.get("source_report") else None
    supplement_dirs = composition_transcript_dirs(supplement.get("transcript_dirs"), label + " supplement")
    supplement_key = tuple(str(path) for path in supplement_dirs)
    if supplement_key not in cache:
      cache[supplement_key] = transcript_agents(supplement_dirs)
    supplement_result = composition_writer(supplement, run, cache, label + " supplement", expected_session="cross-session")
    composition_require_reads([supplement_result["writer_agent"]], supplement_paths + ([supplement_report] if supplement_report else []), label + " supplement")
    extra = supplement_result["report"]
    require(extra.get("status") == "complete", label + " supplement is not complete")
    for key in ("patterns", "secondary_patterns", "dispositions"):
      combined.setdefault(key, [])
      combined[key].extend(copy.deepcopy(extra.get(key, [])))
    combined["supplement_limitations"] = extra.get("limitations", [])
  combined["tracking_observations"] = []
  for path in source_reports:
    combined["tracking_observations"].extend(copy.deepcopy(load(path).get("tracking_observations", [])))
  inputs, problems = accounting_inputs({"input_reports": [str(path) for path in source_reports]})
  require(not problems, "; ".join(problems))
  destinations = []
  for key, entry in inputs.items():
    require(len(entry["outcomes"]) == 1, "conflicting source outcomes need explicit judgment: " + key)
    row = {"observation_key": key, "disposition": "kept"}
    if len(entry["sources"]) > 1:
      row.update({"disposition": "merged", "merged_into": key,
                  "merged_from": [{"unit": unit, "issue_id": issue} for unit, issue in sorted(entry["sources"])]})
    destinations.append(row)
  combined["tracking_dispositions"] = destinations
  combined["status"] = "complete"
  item = {"session": "cross-session", "unit": artifact["unit"], "input_reports": [str(path) for path in source_reports]}
  normalized = check_report(combined, item, "cross", canonical)
  add_event_evidence(normalized, item, plan, run)
  check_tracking_accounting(normalized, item)
  return {"base": base, "supplement": supplement_result, "normalized": normalized,
          "writer": result, "source_reports": source_reports}


def composition_manifest_chain(manifest_path):
  manifest, run, plan, manifest_path = composition_context(manifest_path)
  prior_validation(plan, run, "reading")
  prior_validation(plan, run, "session-merge")
  canonical = load(run / "canonical.json")
  partition_path = composition_path(run, manifest.get("source_partition"), "source_partition")
  tracking_context_path = composition_path(run, manifest.get("tracking_context"), "tracking_context")
  partial_path = composition_path(run, manifest.get("partial_report"), "partial_report")
  output_value = manifest.get("output_report")
  require(isinstance(output_value, str) and output_value, "output_report requires a path")
  output_path = (run / output_value).resolve()
  require(output_path.is_relative_to(run) and output_path != partial_path, "output_report must be a new path beside the partial report")
  partition = load(partition_path)
  require(isinstance(partition, dict) and isinstance(partition.get("items"), list) and partition["items"], "invalid cross partition")
  partition_by_unit = {item.get("unit"): item for item in partition["items"]}
  require(len(partition_by_unit) == len(partition["items"]), "duplicate cross partition unit")
  analyst = manifest.get("analyst_artifacts")
  require(isinstance(analyst, list) and [item.get("unit") for item in analyst] == [item.get("unit") for item in partition["items"]],
          "analyst artifacts do not match source partition")
  cache = {}
  batches = []
  writer_artifacts = []
  source_reports = []
  for artifact in analyst:
    partition_item = partition_by_unit[artifact["unit"]]
    expected_inputs = [str(Path(path).resolve()) for path in partition_item.get("input_reports", [])]
    actual_inputs = [str(Path(path).expanduser().resolve()) for path in artifact.get("input_reports", [])]
    require(actual_inputs == expected_inputs, artifact["unit"] + " input reports differ from partition")
    packet_paths = [composition_path(run, path, artifact["unit"] + " input packet") for path in artifact.get("input_packets", [])]
    require(str(partition_path) in {str(path) for path in packet_paths}, artifact["unit"] + " does not bind source partition")
    require(str(tracking_context_path) in {str(path) for path in packet_paths}, artifact["unit"] + " does not bind tracking context")
    for path in expected_inputs:
      source_reports.append(Path(path).resolve())
    batch = composition_batch_report(artifact, expected_inputs, run, plan, canonical, cache, artifact["unit"])
    batches.append(batch)
    writer_artifacts.append({"unit": artifact["unit"], "report_path": str(batch["writer"]["target"]),
                             "writer": batch["writer"]["agent"]["path"]})
    if batch["supplement"]:
      writer_artifacts.append({"unit": artifact["supplement"]["unit"], "report_path": str(batch["supplement"]["target"]),
                               "writer": batch["supplement"]["agent"]["path"]})
  source_reports = list(dict.fromkeys(source_reports))
  source_records = []
  for path in source_reports:
    source = load(path)
    require(isinstance(source, dict) and source.get("status") == "complete", "input report missing or incomplete: " + str(path))
    source_records.extend(report_records(source))
  source_ids = [record.get("id") for record in source_records]
  require(all(isinstance(key, str) for key in source_ids) and len(source_ids) == len(set(source_ids)), "invalid source record ids")
  synthesis_path = composition_path(run, manifest["semantic_artifacts"]["synthesis_packet"], "synthesis_packet")
  synthesis_agents = composition_transcript_dirs(manifest["final_artifact"].get("transcript_dirs"), "final artifact")
  if tuple(str(path) for path in synthesis_agents) not in cache:
    cache[tuple(str(path) for path in synthesis_agents)] = transcript_agents(synthesis_agents)
  final_agents = cache[tuple(str(path) for path in synthesis_agents)]
  synthesis = load(synthesis_path)
  require(synthesis.get("session") == "cross-session" and "no final approval" in synthesis.get("purpose", ""),
          "synthesis packet is not an intermediate packet")
  expected_batches = []
  for artifact, batch in zip(analyst, batches):
    normalized = batch["normalized"]
    expected_batches.append({"unit": artifact["unit"],
                             "patterns": normalized.get("patterns", []) + normalized.get("secondary_patterns", []),
                             "dispositions": normalized["dispositions"],
                             "limitations": batch["base"].get("limitations", [])})
  require(synthesis.get("batches") == expected_batches, "synthesis packet differs from report-writer outputs")
  old_result = composition_writer(manifest["final_artifact"], run, cache, "final artifact", expected_session="cross-session")
  composition_require_reads([old_result["writer_agent"]], [synthesis_path, tracking_context_path], "final artifact")
  partial = old_result["report"]
  require(partial.get("status") == "partial", "final artifact must preserve the partial report boundary")
  writer_artifacts.append({"unit": "cross-session", "report_path": str(old_result["target"]),
                           "writer": old_result["agent"]["path"]})
  require(partial_path == old_result["target"], "partial_report does not match final artifact")
  semantic = manifest["semantic_artifacts"]
  remaining_path = composition_path(run, semantic["remaining"], "remaining")
  remaining = load(remaining_path)
  old_ids = {entry.get("id") for entry in partial.get("dispositions", [])}
  packet_patterns = [pattern for batch in expected_batches for pattern in batch["patterns"]]
  packet_dispositions = [entry for batch in expected_batches for entry in batch["dispositions"]]
  pending = []
  for pattern in packet_patterns:
    pending_ids = [key for key in pattern.get("source_finding_ids", []) if key not in old_ids]
    if pending_ids:
      pending.append({**copy.deepcopy(pattern), "pending_source_ids": pending_ids})
  pending_rejections = [entry for entry in packet_dispositions if entry.get("id") not in old_ids and entry.get("verdict") == "rejected"]
  expected_remaining = {"existing_patterns": partial.get("patterns", []),
                        "existing_secondary_patterns": partial.get("secondary_patterns", []),
                        "pending_candidates": pending, "pending_prior_rejections": pending_rejections,
                        "all_source_ids": [entry.get("id") for entry in packet_dispositions]}
  require(remaining == expected_remaining, "remaining candidate packet does not match the source chain")
  decision_artifacts = semantic.get("decision_artifacts")
  require(isinstance(decision_artifacts, list) and [x.get("unit") for x in decision_artifacts] == [f"cross-decide-{i:02d}" for i in range(1, 7)],
          "decision artifacts must contain cross-decide-01..06")
  decisions = []
  for index, artifact in enumerate(decision_artifacts):
    input_path = composition_path(run, artifact.get("input_path"), artifact["unit"] + " input")
    output = composition_writer(artifact, run, cache, artifact["unit"])
    composition_require_reads([output["writer_agent"]], [input_path], artifact["unit"])
    decision_input = load(input_path)
    start = index * 70
    stop = min(len(pending), start + 70)
    expected_input = {"unit": artifact["unit"], "existing_patterns": partial.get("patterns", []),
                      "existing_secondary_patterns": partial.get("secondary_patterns", []),
                      "candidates": pending[start:stop]}
    require(decision_input == expected_input, artifact["unit"] + " input differs from remaining packet")
    report = output["report"]
    require(report.get("status") == "complete" and {row.get("id") for row in report.get("decisions", [])} ==
            {row.get("id") for row in decision_input["candidates"]}, artifact["unit"] + " decisions are incomplete")
    require(all(isinstance(row.get("reason"), str) and row["reason"].strip() for row in report["decisions"]),
            artifact["unit"] + " decision reason missing")
    decisions.append({"artifact": artifact, "input": decision_input, "report": report, "writer": output})
    writer_artifacts.append({"unit": artifact["unit"], "report_path": str(output["target"]), "writer": output["agent"]["path"]})
  taxonomy_input_path = composition_path(run, semantic["taxonomy_input"], "taxonomy input")
  taxonomy_artifact = semantic["taxonomy_artifact"]
  taxonomy_output = composition_writer(taxonomy_artifact, run, cache, "taxonomy")
  composition_require_reads([taxonomy_output["writer_agent"]], [taxonomy_input_path], "taxonomy")
  groups = []
  for decision in decisions:
    for row in decision["report"].get("new_groups", []):
      groups.append({"key": f"G{len(groups) + 1:03d}", "unit": decision["artifact"]["unit"], **copy.deepcopy(row)})
  taxonomy_input = load(taxonomy_input_path)
  expected_taxonomy_input = {"existing_patterns": partial.get("patterns", []),
                             "existing_secondary_patterns": partial.get("secondary_patterns", []), "groups": groups}
  require(taxonomy_input == expected_taxonomy_input, "taxonomy input differs from decision outputs")
  taxonomy = taxonomy_output["report"]
  require(taxonomy.get("status") == "complete" and isinstance(taxonomy.get("mapping"), list) and isinstance(taxonomy.get("definitions"), list),
          "taxonomy report is incomplete")
  mapping = taxonomy_mapping_by_key(taxonomy_input["groups"], taxonomy["mapping"])
  targets = {row.get("target") for row in mapping.values()}
  require(all(isinstance(target, str) and target for target in targets), "taxonomy mapping has an invalid target")
  require({row.get("target") for row in taxonomy["definitions"]} == targets, "taxonomy definitions do not cover mapping targets")
  writer_artifacts.append({"unit": "cross-taxonomy", "report_path": str(taxonomy_output["target"]), "writer": taxonomy_output["agent"]["path"]})
  transfer_path = composition_path(run, semantic["tracking_transfer"], "tracking transfer")
  transfer = load(transfer_path)
  expected_tracking = []
  for path in source_reports:
    for observation in load(path).get("tracking_observations") or []:
      row = copy.deepcopy(observation)
      row.pop("events", None)
      expected_tracking.append(row)
  inputs, problems = accounting_inputs({"input_reports": [str(path) for path in source_reports]})
  require(not problems, "; ".join(problems))
  expected_destinations = []
  for key, entry in inputs.items():
    row = {"observation_key": key, "disposition": "kept"}
    if len(entry["sources"]) > 1:
      row.update({"disposition": "merged", "merged_into": key,
                  "merged_from": [{"unit": unit, "issue_id": issue} for unit, issue in sorted(entry["sources"])]})
    expected_destinations.append(row)
  require(transfer == {"tracking_observations": expected_tracking, "tracking_dispositions": expected_destinations},
          "tracking transfer differs from source evidence")
  return {"manifest": manifest, "manifest_path": manifest_path, "run": run, "plan": plan, "canonical": canonical,
          "partial": partial, "partial_path": partial_path, "output_path": output_path, "source_reports": source_reports,
          "source_records": source_records, "source_ids": source_ids, "packet": synthesis, "batches": batches,
          "remaining": remaining, "decisions": decisions, "taxonomy_input": taxonomy_input, "taxonomy": taxonomy,
          "transfer": transfer, "writer_artifacts": writer_artifacts, "cache": cache}


def taxonomy_mapping_by_key(groups, mapping):
  require(isinstance(groups, list) and isinstance(mapping, list), "taxonomy mapping must be arrays")
  group_keys = [group.get("key") for group in groups]
  map_keys = [row.get("key") for row in mapping]
  require(all(isinstance(key, str) and key for key in group_keys + map_keys), "taxonomy mapping keys must be nonempty strings")
  require(len(group_keys) == len(set(group_keys)), "taxonomy input has duplicate group key")
  require(len(map_keys) == len(set(map_keys)), "taxonomy mapping has duplicate key")
  require(set(map_keys) == set(group_keys), "taxonomy mapping has missing or unknown key")
  return {row["key"]: row for row in mapping}


def composition_output(chain):
  manifest = chain["manifest"]
  partial = chain["partial"]
  source_records = {record["id"]: record for record in chain["source_records"]}
  source_ids = list(source_records)
  old_patterns = partial.get("patterns", [])
  old_secondary = partial.get("secondary_patterns", [])
  groups = {entry["id"]: copy.deepcopy(entry) for entry in old_patterns + old_secondary}
  refs = {}
  for group in old_patterns + old_secondary:
    refs.setdefault(group["id"], set()).update(group["source_finding_ids"])
  mapping_by_key = taxonomy_mapping_by_key(chain["taxonomy_input"]["groups"], chain["taxonomy"]["mapping"])
  labels = {(group["unit"], group["target"]): mapping_by_key[group["key"]]["target"]
            for group in chain["taxonomy_input"]["groups"]}
  for definition in chain["taxonomy"]["definitions"]:
    groups[definition["target"]] = copy.deepcopy(definition)
  decisions = {entry["id"]: copy.deepcopy(entry) for entry in partial.get("dispositions", [])}
  for decision in chain["decisions"]:
    by_id = {candidate["id"]: candidate for candidate in decision["input"]["candidates"]}
    for row in decision["report"]["decisions"]:
      candidate = by_id[row["id"]]
      ids = candidate["pending_source_ids"]
      if row["verdict"] == "rejected":
        for record_id in ids:
          decisions[record_id] = {"id": record_id, "verdict": "rejected", "reason": row["reason"]}
      else:
        target = row["target"] if row["verdict"] == "merge_existing" else labels.get((decision["artifact"]["unit"], row["target"]))
        require(target in groups, "decision target is not in taxonomy output")
        refs.setdefault(target, set()).update(ids)
        for record_id in ids:
          decisions[record_id] = {"id": record_id, "verdict": "merged", "reason": row["reason"]}
  for row in chain["remaining"]["pending_prior_rejections"]:
    decisions[row["id"]] = copy.deepcopy(row)
  require(set(decisions) == set(source_ids), "final composition does not account for every source id")
  retained = set().union(*refs.values()) if refs else set()
  for record_id in retained:
    if decisions[record_id]["verdict"] == "rejected":
      decisions[record_id] = {"id": record_id, "verdict": "merged",
                              "reason": "同一來源仍有已保留候選；部分候選拒絕不刪除其餘獨立問題。"}
  issues = {issue["id"]: issue for issue in load(chain["run"] / "tracking-context.json").get("issues", [])}
  patterns, secondary = [], []
  for target, ids in refs.items():
    base = copy.deepcopy(groups[target])
    source = [source_records[record_id] for record_id in sorted(ids)]
    evidence = []
    for record in source:
      for entry in record.get("evidence", []):
        if entry not in evidence:
          evidence.append(copy.deepcopy(entry))
    sessions = sorted({entry["session"] for entry in evidence})
    matched = issues.get(base.get("matched_issue_id"))
    matched = matched if matched and not matched.get("decision") else None
    historical = matched.get("source_sessions", []) if matched else []
    eligible = target.startswith("P") or len(set(sessions) | set(historical)) >= 4
    base.update({"source_finding_ids": sorted(ids), "source_sessions": sessions, "evidence": evidence,
                 "counterevidence": [{"source_record_id": record["id"], "value": record["counterevidence"]}
                                      for record in source if record.get("counterevidence")],
                 "impact": base.get("impact", "範圍與實際影響仍待查核；原始來源反證與限制逐筆保留。"),
                 "needs_current_check": True})
    if matched:
      base["matched_issue_title"] = matched["title"]
    else:
      base["matched_issue_id"] = None
      base["matched_issue_title"] = None
    base.pop("target", None)
    if eligible:
      base["id"] = f"P{len(patterns) + 1}"
      patterns.append(base)
    else:
      base["id"] = f"S{len(secondary) + 1}"
      secondary.append(base)
  return {"session": "cross-session", "unit": "cross-session", "status": "complete",
          "coverage": copy.deepcopy(partial.get("coverage", {"source_records": len(source_ids)})),
          "patterns": patterns, "secondary_patterns": secondary,
          "dispositions": [decisions[record_id] for record_id in source_ids],
          "tracking_observations": copy.deepcopy(chain["transfer"]["tracking_observations"]),
          "tracking_dispositions": copy.deepcopy(chain["transfer"]["tracking_dispositions"]),
          "limitations": ["本檔由固定 composition manifest 以真實 agent Write/Edit 軌跡與封閉組裝規則重建；原 partial report 保留未覆寫。",
                          f"本次機械驗收覆蓋 {len(source_ids)} 筆 source records 與 {len(chain['transfer']['tracking_observations'])} 筆 tracking observations；只證來源鏈與結構完整，不宣稱語意正確。",
                          "semantic decision、taxonomy 與 current-state 判斷仍須由 verify-source / verify-current 驗收。"],
          "assembly_provenance": {"kind": "deterministic-cross-composition", "manifest_path": str(chain["manifest_path"]),
                                  "partial_report": str(chain["partial_path"]),
                                  "source_record_count": len(source_ids),
                                  "tracking_observation_count": len(chain["transfer"]["tracking_observations"]),
                                  "writer_artifacts": chain["writer_artifacts"],
                                  "tracking_policy": "source observations copied without model-written events; event identity is re-derived by add_event_evidence"}}


def composition_check(manifest_path, write=False, expected_output=None):
  chain = composition_manifest_chain(manifest_path)
  raw = composition_output(chain)
  final_item = {"session": "cross-session", "unit": "cross-session",
                "input_reports": [str(path) for path in chain["source_reports"]]}
  normalized = check_report(raw, final_item, "cross", chain["canonical"])
  add_event_evidence(normalized, final_item, chain["plan"], chain["run"])
  check_tracking_accounting(normalized, final_item)
  output_path = chain["output_path"]
  validated_path = chain["run"] / "validated" / "cross-session.json"
  if expected_output is not None:
    require(output_path == Path(expected_output).resolve(), "composition output path changed")
    require(output_path.is_file() and load(output_path) == normalized, "composition output changed since validation")
    require(validated_path.is_file() and load(validated_path) == normalized, "validated composition changed since validation")
  if write:
    require(not output_path.exists() and not validated_path.exists() and not (chain["run"] / "composition-validation.json").exists(),
            "composition output already exists; refusing overwrite")
    save(output_path, normalized)
    save(validated_path, normalized)
    validation = {"schema_version": 1, "run_id": chain["plan"]["run_id"], "stage": "cross", "pass": True,
                  "composition_manifest": str(chain["manifest_path"]), "manifest": chain["manifest"],
                  "output_report": str(output_path), "validated_path": str(validated_path),
                  "counts": {"source_records": len(chain["source_ids"]), "patterns": len(normalized["patterns"]),
                             "secondary_patterns": len(normalized["secondary_patterns"]),
                             "dispositions": len(normalized["dispositions"]),
                             "tracking_observations": len(normalized["tracking_observations"]),
                             "tracking_dispositions": len(normalized["tracking_dispositions"])}}
    save(chain["run"] / "composition-validation.json", validation)
  return {"pass": True, "manifest_path": str(chain["manifest_path"]), "output_report": str(output_path),
          "validated_path": str(validated_path), "counts": {"source_records": len(chain["source_ids"]),
          "patterns": len(normalized["patterns"]), "secondary_patterns": len(normalized["secondary_patterns"]),
          "dispositions": len(normalized["dispositions"]), "tracking_observations": len(normalized["tracking_observations"]),
          "tracking_dispositions": len(normalized["tracking_dispositions"])}, "check_only": not write}


def prior_composition(plan, run):
  path = run / "composition-validation.json"
  result = load(path)
  require(result.get("schema_version") == 1 and result.get("run_id") == plan["run_id"] and result.get("stage") == "cross"
          and result.get("pass") is True, "composition validation is missing or failed")
  manifest_path = Path(result.get("composition_manifest", "")).resolve()
  manifest, manifest_run, manifest_plan, _ = composition_context(manifest_path)
  require(manifest_run == run and manifest_plan == plan and result.get("manifest") == manifest,
          "composition manifest changed since validation")
  composition_check(manifest_path, expected_output=result["output_report"])
  return result


def composition_validation_summary(plan, run):
  result = prior_composition(plan, run)
  return {"pass": True, "validation_path": str(run / "composition-validation.json"),
          "output_report": result["output_report"], "validated_path": result["validated_path"], "counts": result["counts"]}


def compose_cross(args):
  return composition_check(args.manifest, write=not args.check_only)


def canonical_strings(text, depth=0):
  yield text
  if depth >= 4:
    return
  try:
    decoded = json.loads(text)
  except (ValueError, TypeError, RecursionError):
    return

  def strings(value):
    if isinstance(value, str):
      yield from canonical_strings(value, depth + 1)
    elif isinstance(value, dict):
      for child in value.values():
        yield from strings(child)
    elif isinstance(value, list):
      for child in value:
        yield from strings(child)

  yield from strings(decoded)


class EvidenceErrors(ValueError):
  """Every unmatched quote in one report, so a repair sees them all instead of one per round."""

  def __init__(self, failures):
    super().__init__("; ".join(failure["error"] for failure in failures))
    self.failures = failures


QUOTE_MISS = "quote is not a contiguous source block match"
FORMAT_NOISE = re.compile(r"[\s*`_#>]")
# Agents miscopy line numbers and drop markdown markers; 2026-11-02..11-04 repairs were mostly those two.
# Limits come from replaying those 54 failures against the fixes Terra made: the auto fixes that picked an
# irrelevant event were a 7-character phrase, "(Bash completed with no output)" (in 11 events), a
# format-only match 1948 lines away and a unique match 820 lines away; agreeing rebinds reached 155 lines.
NEAR_LINES = 3
LOOSE_MIN_CHARS = 8
FAR_LINES = 200
BOILERPLATE_EVENTS = 10


def quote_variants(quote):
  quotes = [quote]
  try:
    decoded = json.loads('"' + quote + '"')
    if isinstance(decoded, str):
      quotes.append(decoded)
  except ValueError:
    pass
  return quotes


def strict_match(event, quotes):
  return any(q in text for text in canonical_strings(event["text"]) for q in quotes)


def loose_span(event, quotes):
  """The exact source text a quote names once markdown markers and whitespace are ignored, if it occurs once."""
  targets = [t for t in (FORMAT_NOISE.sub("", q) for q in quotes) if len(t) >= LOOSE_MIN_CHARS]
  for text in canonical_strings(event["text"]):
    kept = [i for i, ch in enumerate(text) if not FORMAT_NOISE.match(ch)]
    squeezed = "".join(text[i] for i in kept)
    for target in targets:
      start = squeezed.find(target)
      if start >= 0 and squeezed.find(target, start + 1) < 0:
        return text[kept[start]:kept[start + len(target) - 1] + 1]
  return None


def only(rows):
  return rows[0] if len({row[0]["source_line"] for row in rows}) == 1 else None


def correct_quote(events, source_line, quotes):
  """A cited quote's single unambiguous home; None leaves the decision to a model."""
  near = lambda e: abs(e["source_line"] - source_line) <= NEAR_LINES
  cited = [(e, span) for e in events if e["source_line"] == source_line for span in [loose_span(e, quotes)] if span]
  if cited:
    return "format-same-line", cited[0]
  if len(FORMAT_NOISE.sub("", quotes[0])) < LOOSE_MIN_CHARS:
    return None, None
  exact = [(e, None) for e in events if strict_match(e, quotes)]
  if len({e["source_line"] for e, _ in exact}) > BOILERPLATE_EVENTS:
    return None, None
  within = [r for r in exact if abs(r[0]["source_line"] - source_line) <= FAR_LINES]
  for rule, rows in (("unique-line", within if only(exact) else []), ("nearest-line", [r for r in exact if near(r[0])])):
    if only(rows):
      return rule, only(rows)
  loose = [(e, span) for e in events if near(e) for span in [loose_span(e, quotes)] if span]
  if only(loose):
    return "format-nearest-line", only(loose)
  return None, None


def normalize_evidence(evidence, item, canonical):
  require(isinstance(evidence, dict), "evidence must be an object")
  session = evidence.get("session", item.get("session"))
  require(session in canonical, "unknown evidence session")
  source_line = evidence.get("source_line")
  require(isinstance(source_line, int) and not isinstance(source_line, bool), "source_line must be an integer")
  quote = evidence.get("quote")
  require(isinstance(quote, str) and bool(quote.strip()), "empty evidence quote")
  quotes = quote_variants(quote)
  matching = [event for event in canonical[session] if event["source_line"] == source_line and strict_match(event, quotes)]
  if matching:
    event = matching[0]
    if "event_time" in evidence:
      require(moment(evidence["event_time"]) == moment(event["event_time"]), "event_time differs from source")
    return {**evidence, "session": session, "event_time": event["event_time"], "scope": event["scope"]}
  rule, found = correct_quote(canonical[session], source_line, quotes)
  require(rule is not None, f"{QUOTE_MISS} (source_line={source_line}, quote={quote!r})")
  event, span = found
  # The original report keeps the agent's text; only the validated copy is corrected, and says so.
  return {**evidence, "session": session, "source_line": event["source_line"], "quote": span or quote,
          "event_time": event["event_time"], "scope": event["scope"],
          "corrected_from": {"source_line": source_line, "quote": quote}, "correction": rule}


EVENT_HEADER = re.compile(r"^\[line=(\d+) timestamp=")
QUOTE_EVENTS_AROUND = 3
QUOTE_WINDOW_MAX_LINES = 400


def quote_windows(failures, plan):
  """Where a repairer should look: the cited event and a few around it, not the whole document."""
  documents = {s["session"]: s["document"] for s in plan["sessions"]}
  headers = {}
  windows = []
  for failure in failures:
    document = documents.get(failure["session"])
    window = {**failure, "document": document}
    if document and isinstance(failure.get("source_line"), int):
      if document not in headers:
        lines = Path(document).read_text(encoding="utf-8").split("\n")
        headers[document] = ([(number, int(m.group(1))) for number, line in enumerate(lines, 1)
                              for m in [EVENT_HEADER.match(line)] if m], len(lines))
      marks, total = headers[document]
      if marks:
        at = next((i for i, (_, line) in enumerate(marks) if line >= failure["source_line"]), len(marks) - 1)
        # Long neighbours must not push the cited event out of a capped window.
        start = max(marks[max(0, at - QUOTE_EVENTS_AROUND)][0], marks[at][0] - QUOTE_WINDOW_MAX_LINES // 4)
        after = at + QUOTE_EVENTS_AROUND + 1
        end = marks[after][0] - 1 if after < len(marks) else total
        window.update({"offset": start, "limit": min(end - start + 1, QUOTE_WINDOW_MAX_LINES)})
    windows.append(window)
  return windows


def normalize_record_evidence(records, item, canonical):
  failures = []
  for record in records:
    kept = []
    for evidence in evidence_list(record):
      try:
        kept.append(normalize_evidence(evidence, item, canonical))
      except ValueError as error:
        if not str(error).startswith(QUOTE_MISS):
          raise
        failures.append({"error": str(error), "record_id": record.get("id"),
                         "session": evidence.get("session", item.get("session")),
                         "source_line": evidence.get("source_line"), "quote": evidence.get("quote")})
    record["evidence"] = kept
  if failures:
    raise EvidenceErrors(failures)


def evidence_list(record):
  evidence = record.get("evidence")
  if isinstance(evidence, dict):
    evidence = [evidence]
  if evidence is None and "source_line" in record:
    evidence = [{key: record[key] for key in ("session", "source_line", "quote", "event_time") if key in record}]
  require(isinstance(evidence, list) and bool(evidence), "finding requires nonempty evidence")
  return evidence


def report_records(report):
  result = []
  for key in ("findings", "patterns", "secondary_patterns"):
    values = report.get(key, [])
    require(isinstance(values, list), key + " must be an array")
    require(all(isinstance(value, dict) for value in values), key + " contains a nonobject")
    result.extend(values)
  return result


def check_dispositions(report, item, stage):
  source_records = []
  expected_tracking = set()
  for path in item["input_reports"]:
    source = load(path)
    require(isinstance(source, dict) and source.get("status") == "complete", "input report missing or incomplete")
    source_records.extend(report_records(source))
    for obs in source.get("tracking_observations") or []:
      # A cross observation with evidence is accounted event by event (check_tracking_accounting), not by pair.
      if isinstance(obs, dict) and not (stage == "cross" and obs.get("evidence")):
        expected_tracking.add((obs.get("issue_id"), obs.get("change_id")))
  source_ids = [r.get("id") for r in source_records]
  require(all(isinstance(key, str) for key in source_ids) and len(set(source_ids)) == len(source_ids), "invalid or duplicate input record ids")
  by_id = {r["id"]: r for r in source_records}
  expected = set(source_ids)
  if expected_tracking:
    tracking = report.get("tracking_observations")
    require(isinstance(tracking, list), "tracking_observations required when inputs contain tracking observations")
    actual_tracking = {(obs.get("issue_id"), obs.get("change_id")) for obs in tracking if isinstance(obs, dict) and obs.get("outcome")}
    require(expected_tracking <= actual_tracking, "missing tracking observations from input reports")
  dispositions = report.get("dispositions")
  require(isinstance(dispositions, list), "dispositions required, including zero-input merges")
  disposition_key = "source_finding_id" if stage == "session-merge" else "source_record_id"
  refs_key = "source_finding_ids" if stage == "session-merge" else "source_record_ids"
  actual = []
  retained = set()
  for entry in dispositions:
    require(isinstance(entry, dict), "invalid disposition")
    key = entry.get(disposition_key, entry.get("id"))
    decision = entry.get("disposition", entry.get("verdict"))
    require(key in expected, "disposition references unknown source id")
    require(decision in {"kept", "merged", "rejected"}, "invalid disposition decision")
    require(isinstance(entry.get("reason"), str) and bool(entry["reason"].strip()), "disposition reason required")
    actual.append(key)
    if decision in {"kept", "merged"}:
      retained.add(key)
  require(len(actual) == len(expected) and set(actual) == expected, "missing or duplicate disposition")
  cited = set()
  for record in report_records(report):
    refs = record.get(refs_key, record.get("source_finding_ids"))
    require(isinstance(refs, list) and bool(refs), "output record requires source ids")
    require(all(key in expected for key in refs), "output record has invented source id")
    require(all(key in retained for key in refs), "output cites a rejected source record")
    if any(by_id[key].get("counterevidence") for key in refs):
      require(bool(record.get("counterevidence")), "output record dropped source counterevidence")
    cited.update(refs)
  require(retained <= cited, "kept/merged source not cited by any output")


def accounting_inputs(item):
  """observation_key -> what the inputs said about it. Evidence without a derived event key is a problem, not zero."""
  inputs = {}
  problems = []
  for path in item["input_reports"]:
    source = load(path)
    for obs in source.get("tracking_observations") or []:
      if not isinstance(obs, dict) or not obs.get("evidence"):
        continue
      events = [e for e in obs.get("events") or [] if isinstance(e, dict) and isinstance(e.get("observation_key"), str)]
      if not events:
        problems.append(f"input observation has evidence but no event identity (unit={source.get('unit')}, "
                        f"issue_id={obs.get('issue_id')}, change_id={obs.get('change_id')}); validate the earlier stage again")
      for event in events:
        entry = inputs.setdefault(event["observation_key"], {"event_key": event.get("event_key"), "change_id": event.get("change_id"),
                                                             "outcomes": set(), "sources": set()})
        entry["outcomes"].add(obs.get("outcome"))
        entry["sources"].add((source.get("unit"), obs.get("issue_id")))
  return inputs, problems


def output_outcomes(report):
  outcomes = {}
  for obs in report.get("tracking_observations") or []:
    if isinstance(obs, dict):
      for event in obs.get("events") or []:
        if isinstance(event, dict) and isinstance(event.get("observation_key"), str):
          outcomes.setdefault(event["observation_key"], set()).add(obs.get("outcome"))
  return outcomes


def check_tracking_accounting(report, item):
  """Every input observation (event + change) has exactly one destination; changed or dropped ones carry a reason.

  Needs the events[] that add_event_evidence derives, so it runs after that. Event keys are the same
  ones reading and session-merge already carry, so the three stages cannot disagree about identity.
  """
  inputs, problems = accounting_inputs(item)
  if not inputs and not problems:
    return
  errors = problems[:ACCOUNTING_ERROR_CAP] + ([f"(+{len(problems) - ACCOUNTING_ERROR_CAP} more without event identity)"]
                                              if len(problems) > ACCOUNTING_ERROR_CAP else [])
  entries = report.get("tracking_dispositions")
  require(entries is not None, f"legacy cross report format: tracking_dispositions missing, so {len(inputs)} input "
                               "observations have no per-observation destination")
  require(isinstance(entries, list), "tracking_dispositions must be an array")

  def label(key):
    known = inputs.get(key)
    return f"event_key={known['event_key']}, change_id={known['change_id']}" if known else f"observation_key={key}"

  by_key = {}
  for entry in entries:
    key = entry.get("observation_key") if isinstance(entry, dict) else None
    if key not in inputs:
      errors.append(f"destination for an unknown input observation (observation_key={key})")
      continue
    if key in by_key:
      errors.append(f"more than one destination for input observation ({label(key)})")
      continue
    by_key[key] = entry
  for key in inputs:
    if key not in by_key:
      errors.append(f"no destination for input observation ({label(key)})")
  outputs = output_outcomes(report)
  for key, entry in by_key.items():
    decision = entry.get("disposition")
    if decision not in DESTINATIONS:
      errors.append(f"invalid destination {decision!r} ({label(key)})")
      continue
    if decision in ("reclassified", "rejected") and not (isinstance(entry.get("reason"), str) and entry["reason"].strip()):
      errors.append(f"{decision} requires a reason ({label(key)})")
    shown = outputs.get(key)
    if decision == "rejected":
      if shown is not None:
        errors.append(f"rejected but still cited in tracking_observations ({label(key)})")
    elif shown is None:
      errors.append(f"{decision} but absent from tracking_observations ({label(key)})")
    elif decision == "reclassified":
      if shown != {entry.get("to_outcome")}:
        errors.append(f"to_outcome {entry.get('to_outcome')!r} does not match the output outcome ({label(key)})")
    elif shown != inputs[key]["outcomes"]:
      errors.append(f"outcome differs from the input but the destination is {decision}; use reclassified with a reason ({label(key)})")
    sources = inputs[key]["sources"]
    if decision == "kept" and len(sources) > 1:
      errors.append(f"reported by {len(sources)} input observations, so it must be merged with merged_from listing each ({label(key)})")
    if decision == "merged":
      if entry.get("merged_into") != key:
        errors.append(f"cannot merge across events or changes: {key} into {entry.get('merged_into')}")
      elif len(sources) < 2:
        errors.append(f"nothing to merge: only one input observation reported it ({label(key)})")
      else:
        listed = {(m.get("unit"), m.get("issue_id")) for m in entry.get("merged_from") or [] if isinstance(m, dict)}
        if listed != sources:
          errors.append(f"merged_from must list exactly the merged sources ({label(key)})")
  # A whole run can owe hundreds of destinations; the first ones are enough to repair from and the rest are counted.
  shown = errors[:ACCOUNTING_ERROR_CAP * 2]
  require(not errors, "; ".join(shown + ([f"(+{len(errors) - len(shown)} more errors)"] if len(errors) > len(shown) else [])))


def verify_verdicts(report, item, canonical):
  cross = load(item["input_reports"][0])
  ids = {r["id"] for r in cross.get("patterns", [])}
  allowed_ids = set(item.get("pattern_ids", ids))
  require(allowed_ids <= ids, "critic part references unknown pattern")
  verdicts = report.get("verdicts", report.get("findings"))
  require(isinstance(verdicts, list), "verdicts required")
  found = []
  for verdict in verdicts:
    require(isinstance(verdict, dict), "invalid critic verdict")
    require(verdict.get("pattern_id") in ids, "critic references unknown pattern")
    require(verdict.get("verdict") in {"CONFIRMED", "REFUTED", "UNCERTAIN"}, "invalid critic verdict decision")
    evidence = evidence_list(verdict)
    normalized = []
    for entry in evidence:
      if "source_line" in entry:
        normalized.append(normalize_evidence(entry, item, canonical))
      else:
        require(isinstance(entry.get("path"), str) and isinstance(entry.get("quote"), str) and bool(entry["quote"].strip()), "critic evidence requires source or path/quote")
        path = Path(entry["path"]).resolve()
        require(path.is_file() and entry["quote"] in path.read_text(encoding="utf-8"), "critic evidence quote does not match local file")
        normalized.append(entry)
    verdict["evidence"] = normalized
    found.append(verdict["pattern_id"])
  require(len(found) == len(allowed_ids) and set(found) == allowed_ids, "missing or duplicate critic part verdicts")


def check_tracking_shape(report):
  """Each tracking observation names its change, and carries structured evidence unless it is unreviewed.

  Without a change id the observation key becomes "...|None"; without evidence there is no event to key.
  Both used to pass here and then made ingest refuse the whole run.
  """
  observations = report.get("tracking_observations")
  if observations is None:
    return
  require(isinstance(observations, list), "tracking_observations must be an array")
  errors = []
  for index, obs in enumerate(observations):
    if not isinstance(obs, dict):
      errors.append(f"tracking_observations[{index}] is not an object")
      continue
    name = f"tracking_observations[{index}] (issue_id={obs.get('issue_id')}, change_id={obs.get('change_id')})"
    if not (isinstance(obs.get("change_id"), str) and obs["change_id"].strip()):
      errors.append(f"{name}: change_id is missing or null")
    evidence = obs.get("evidence")
    if obs.get("outcome") == "unreviewed" and evidence in (None, []):
      continue
    points = evidence if isinstance(evidence, list) else None
    if not points:
      errors.append(f"{name}: evidence must be a nonempty list of {{session, source_line, quote}} unless the outcome is unreviewed")
    elif not all(isinstance(point, dict) and isinstance(point.get("session"), str) and point["session"].strip()
                 and isinstance(point.get("source_line"), int) and not isinstance(point["source_line"], bool) and point["source_line"] >= 1
                 and isinstance(point.get("quote"), str) and point["quote"].strip() for point in points):
      errors.append(f"{name}: every evidence entry needs session, a positive integer source_line and a quote")
    else:
      # 沒有 role 就分不出哪些是事件、哪些是佐證；補救動作會被各算一個事件（11-04 搜尋輸出過大：人工 22、程式 29）。
      unroled = [position for position, point in enumerate(points) if point.get("role") not in EVIDENCE_ROLES]
      errors.extend(f"{name}: evidence[{position}] role must be 'action' or 'context'" for position in unroled)
      if not unroled and obs.get("outcome") != "unreviewed" and not any(point["role"] == "action" for point in points):
        errors.append(f"{name}: needs at least one evidence with role 'action' (the mistaken or followed action itself)")
  shown = errors[:ACCOUNTING_ERROR_CAP]
  require(not errors, "; ".join(shown + ([f"(+{len(errors) - len(shown)} more tracking observation errors)"] if len(errors) > len(shown) else [])))


def check_report(report, item, stage, canonical):
  require(isinstance(report, dict), "report must be an object")
  require(report.get("status") == "complete", "report is missing, partial, or failed")
  require(report.get("unit") == item["unit"] and report.get("session") == item.get("session", "cross-session"), "report identity mismatch")
  if report.get("source_document") is not None and item.get("source_document"):
    require(Path(report["source_document"]).resolve() == Path(item["source_document"]).resolve(), "report source document mismatch")
  record_key = "findings" if stage == "reading" else "patterns" if stage in {"session-merge", "cross"} else "findings" if "findings" in report else "verdicts"
  require(isinstance(report.get(record_key), list), record_key + " must be an array; missing/null is not zero")
  require(not report.get("overflow"), "overflow requires partial coverage")
  if stage != "verify":
    check_tracking_shape(report)
  normalized = copy.deepcopy(report)
  all_records = report_records(normalized)
  ids = [r.get("id") for r in all_records]
  require(all(isinstance(key, str) and (key.startswith(item["unit"] + "-") or key.startswith(item["unit"] + ":") or (stage == "cross" and re.fullmatch(r"P\d+|S\d+", key))) for key in ids), "record id must have full unit prefix")
  require(len(ids) == len(set(ids)), "duplicate output record ids")
  if stage == "reading":
    normalize_record_evidence(all_records, item, canonical)
  elif stage in {"session-merge", "cross"}:
    check_dispositions(normalized, item, stage)
    normalize_record_evidence(all_records, item, canonical)
  else:
    verify_verdicts(normalized, item, canonical)
  return normalized


def critic_input_paths(item):
  if not item.get("slice_path"):
    return item["input_reports"]
  cross = load(item["input_reports"][0])
  assigned = set(item["pattern_ids"])
  selected = [row for row in cross["patterns"] if row["id"] in assigned]
  index = [{"id": row["id"], "observation": row.get("observation"),
            "sessions": len(row.get("source_sessions", [])),
            "matched_issue_id": row.get("matched_issue_id"),
            "current_check_paths": row.get("current_check_paths", [])} for row in cross["patterns"]]
  require(load(item["slice_path"])["patterns"] == selected, "critic slice differs from assigned full patterns")
  require(load(item["index_path"]) == index, "critic index differs from full primary index")
  return [item["slice_path"], item["index_path"]]


def primary_patterns_span(text):
  # Critics only judge primary patterns; requiring them to load multi-MB trailing tracking_observations overflows 922k-1M windows.
  decoder = json.JSONDecoder()
  length = len(text)

  def skip_ws(pos):
    while pos < length and text[pos] in " \t\r\n":
      pos += 1
    return pos

  pos = skip_ws(0)
  if pos >= length or text[pos] != "{":
    return None
  pos = skip_ws(pos + 1)
  if pos < length and text[pos] == "}":
    return None
  try:
    while pos < length:
      key_start = pos
      key, pos = decoder.raw_decode(text, pos)
      pos = skip_ws(pos)
      if pos >= length or text[pos] != ":":
        return None
      pos = skip_ws(pos + 1)
      _, val_end = decoder.raw_decode(text, pos)
      if key == "patterns":
        return (text.count("\n", 0, key_start) + 1, text.count("\n", 0, val_end) + 1)
      pos = skip_ws(val_end)
      if pos < length and text[pos] == ",":
        pos = skip_ws(pos + 1)
      elif pos < length and text[pos] == "}":
        return None
      else:
        return None
  except ValueError:
    return None
  return None


def critic_required_lines(path, primary_only=False):
  text = Path(path).read_text(encoding="utf-8")
  lines = text.split("\n")
  if primary_only:
    span = primary_patterns_span(text)
    if span is not None:
      return {n for n in range(span[0], span[1] + 1) if lines[n - 1].strip()}
  return {n for n, line in enumerate(lines, 1) if line.strip()}


def critic_read_errors(agent, item, report, canonical, plan, reuse=False):
  errors = []
  primary_only = not item.get("slice_path")
  for path in critic_input_paths(item):
    required = critic_required_lines(path, primary_only=primary_only)
    if required - read_lines(agent, path):
      errors.append("critic did not read assigned verify input fully")
  documents = {s["session"]: Path(s["document"]).resolve() for s in plan["sessions"]}
  verdicts = report.get("verdicts", report.get("findings", []))
  for verdict in verdicts:
    if not any("source_line" in e for e in evidence_list(verdict)):
      errors.append("critic verdict requires canonical source evidence")
    for evidence in evidence_list(verdict):
      if "source_line" in evidence:
        path = documents[evidence.get("session")]
        quote = evidence["quote"]
        esc1 = json.dumps(quote, ensure_ascii=False)[1:-1]
        esc2 = json.dumps(esc1, ensure_ascii=False)[1:-1]
        quotes = {quote, esc1, esc2}
        if not any(op["name"] == "Read" and operation_path(op) == path and any(q in op["text"] for q in quotes) for op in agent["operations"]):
          errors.append(f"critic source quote lacks its own successful Read evidence (pattern={verdict.get('pattern_id')}, session={evidence.get('session')}, source_line={evidence.get('source_line')}, quote={quote!r})")
    if (item["unit"] == "verify-current" or re.fullmatch(r"verify-current-p\d+", item["unit"])) and verdict["verdict"] != "UNCERTAIN":
      current = verdict.get("current_evidence", [])
      if not current:
        errors.append(f"current-state verdict lacks current evidence (pattern={verdict.get('pattern_id')})")
      run_root = Path(item["input_reports"][0]).resolve().parent.parent if item.get("input_reports") else None
      for evidence in current:
        path = Path(evidence.get("path", "")).expanduser().resolve()
        line = evidence.get("line")
        inside_run = run_root is not None and path.is_relative_to(run_root)
        if reuse:
          ok = isinstance(line, int) and (line in read_lines(agent, path) if inside_run else line in transcript_read_lines(agent, path))
        elif inside_run:
          ok = path.is_file() and isinstance(line, int) and line in read_lines(agent, path)
        else:
          # External workspace files can shift concurrently between the critic's Read and main's validate call.
          ok = path.is_file() and isinstance(line, int) and line in transcript_read_lines(agent, path)
        if not ok:
          errors.append(f"current-state evidence lacks matching successful Read (pattern={verdict.get('pattern_id')}, path={path})")
  return errors


def verify_parts_context(manifest_path, run, require_outputs=True, require_slices=False):
  path = Path(manifest_path).expanduser().resolve()
  require(path.is_file() and path.is_relative_to(run), "critic parts manifest must be inside run")
  data = load(path)
  require(isinstance(data, dict) and data.get("schema_version") == 1 and data.get("kind") == "verify-current-parts",
          "unsupported critic parts manifest")
  require(data.get("run_id") == run.name and data.get("unit") == "verify-current", "critic parts identity mismatch")
  parts = data.get("parts")
  require(isinstance(parts, list) and parts, "critic parts manifest requires at least one part")
  all_ids = []
  normalized = []
  seen_units = set()
  for part in parts:
    require(isinstance(part, dict) and isinstance(part.get("unit"), str) and re.fullmatch(r"verify-current-p\d+", part["unit"]),
            "invalid critic part unit")
    require(part["unit"] not in seen_units, "duplicate critic part unit")
    seen_units.add(part["unit"])
    report_value = part.get("report_path")
    report_path = Path(report_value).expanduser().resolve() if isinstance(report_value, str) and report_value else None
    require(report_path is not None and report_path.is_relative_to(run), "critic part report outside run")
    transcript_values = part.get("transcript_dirs", [])
    require(isinstance(transcript_values, list) and all(isinstance(value, str) and value for value in transcript_values),
            "critic part transcript directories must be paths")
    transcript_dirs = [Path(value).expanduser().resolve() for value in transcript_values]
    if require_outputs:
      require(report_path.is_file(), "critic part report missing: " + str(report_path))
      transcript_dirs = composition_transcript_dirs(transcript_values, part["unit"])
    pattern_ids = part.get("pattern_ids")
    require(isinstance(pattern_ids, list) and all(isinstance(key, str) and key for key in pattern_ids),
            "critic part pattern_ids must be nonempty strings")
    require(len(pattern_ids) == len(set(pattern_ids)), "duplicate critic part pattern id")
    require(not set(pattern_ids) & set(all_ids), "critic part pattern ids overlap")
    all_ids.extend(pattern_ids)
    slice_fields = {}
    has_slice = part.get("slice_path") is not None or part.get("index_path") is not None
    if has_slice:
      require(part.get("slice_path") is not None and part.get("index_path") is not None,
              "critic parts require both slice_path and index_path")
      for field in ("slice_path", "index_path"):
        value = Path(part.get(field, "")).expanduser().resolve()
        require(value.is_file() and value.is_relative_to(run), "critic " + field + " outside run or missing")
        slice_fields[field] = str(value)
    elif require_slices:
      require(False, "critic parts require slice_path and index_path")
    normalized.append({"unit": part["unit"], "report_path": report_path,
                       "transcript_dirs": transcript_dirs, "pattern_ids": pattern_ids, **slice_fields})
  cross_path = validated_path(run, "cross-session")
  require(cross_path.is_file(), "validated cross report missing")
  cross = load(cross_path)
  expected = {row.get("id") for row in cross.get("patterns", [])}
  require(all(isinstance(key, str) and key for key in expected), "cross report contains invalid pattern id")
  require(set(all_ids) == expected and len(all_ids) == len(expected), "critic parts must cover every pattern exactly once")
  for part in normalized:
    critic_input_paths({**part, "input_reports": [str(cross_path)]})
  return {"manifest": data, "parts": normalized, "cross": cross, "path": path}


def validate_verify_source(args, plan, run, canonical):
  item = next(item for item in stage_items(plan, run, "verify", enforce=False) if item["unit"] == "verify-source")
  agents = transcript_agents(args.transcript_dir)
  result = {"unit": item["unit"], "pass": False, "errors": []}
  try:
    agent, target, original = report_writer(agents, item)
    require(target == Path(item["report_path"]).resolve(), "verify-source report path differs from stage item")

    report = check_report(original, item, "verify", canonical)
    errors = critic_read_errors(agent, item, report, canonical, plan)
    require(not errors, "; ".join(errors))
    destination = validated_path(run, item["unit"])
    save(destination, report)
    result.update({"pass": True, "agent": agent["path"], "actual_report_path": str(target),
                   "validated_path": str(destination), "original_report": original, "report": report,
                   "transcript_dirs": list(args.transcript_dir)})
  except (OSError, ValueError, KeyError, TypeError) as error:
    result["errors"].append(str(error))
  return result


def validate_verify_parts(args, plan, run, canonical, source_unit=None):
  if source_unit is None:
    source_validation = prior_validation(plan, run, "verify", must_pass=False, check_parts=False)
    source_unit = next(unit for unit in source_validation["units"] if unit["unit"] == "verify-source" and unit["pass"])
  context = verify_parts_context(args.critic_parts, run, require_outputs=False,
                                 require_slices=not (run / "composition-validation.json").exists())
  units = []
  union = []
  previous_path = run / "validation-verify-current-parts.json"
  previous = load(previous_path) if previous_path.exists() else {}
  for part in context["parts"]:
    prior = next((unit for unit in previous.get("units", []) if unit.get("unit") == part["unit"]), {})
    transcript_dirs = list(dict.fromkeys([str(path) for path in part["transcript_dirs"]]
                                        + prior.get("transcript_dirs", []) + list(args.transcript_dir)))
    item = {"session": "cross-session", "unit": part["unit"], "report_path": str(part["report_path"]),
            "input_reports": [str(validated_path(run, "cross-session"))]}
    item.update({key: part[key] for key in ("slice_path", "index_path", "pattern_ids") if key in part})
    result = {"unit": part["unit"], "pass": False, "errors": [], "transcript_dirs": transcript_dirs}
    try:
      agents = transcript_agents(transcript_dirs)
      agent, target, original = report_writer(agents, item)
      require(target == part["report_path"], "critic part report path differs from manifest")
      report = check_report(original, item, "verify", canonical)
      verdicts = report.get("verdicts", report.get("findings", []))
      require({row.get("pattern_id") for row in verdicts} == set(part["pattern_ids"]), "critic part verdicts differ from assigned patterns")
      result["errors"].extend(critic_read_errors(agent, item, report, canonical, plan))
      require(not result["errors"], "; ".join(result["errors"]))
      destination = validated_path(run, part["unit"])
      save(destination, report)
      result.update({"pass": True, "agent": agent["path"], "actual_report_path": str(target),
                     "validated_path": str(destination), "original_report": original, "report": report})
      union.extend(verdicts)
    except (OSError, ValueError, KeyError, TypeError) as error:
      if not result["errors"]:
        result["errors"].append(str(error))
      if prior.get("pass") is True:
        with suppress(OSError, ValueError, KeyError, TypeError):
          agents = transcript_agents(transcript_dirs)
          agent, _, _ = report_writer(agents, item)
          if Path(agent["path"]).resolve() == Path(prior["agent"]).resolve():
            verify_part_reuse(plan, run, context, prior)
            result = {**prior, "transcript_dirs": transcript_dirs}
            union.extend(prior["report"].get("verdicts", prior["report"].get("findings", [])))
    units.append(result)
  counts = {"expected": len(units), "passed": sum(unit["pass"] for unit in units), "failed": sum(not unit["pass"] for unit in units)}
  validation = {"schema_version": 1, "run_id": plan["run_id"], "stage": "verify-current-parts", "pass": counts["failed"] == 0,
                "binding": binding(plan, run), "critic_parts_manifest": str(context["path"]), "manifest": context["manifest"],
                "units": units, "counts": counts, "reusable_units": [unit["unit"] for unit in units if unit["pass"]]}
  if counts["failed"]:
    validation["errors"] = [error for unit in units for error in unit["errors"]]
    save(run / "validation-verify-current-parts.json", validation)
    formal = {"schema_version": 1, "run_id": plan["run_id"], "stage": "verify", "pass": False,
              "binding": binding(plan, run), "units": [source_unit, {"unit": "verify-current", "pass": False,
              "errors": ["critic part validation failed"]}],
              "counts": {"expected": 2, "passed": int(source_unit.get("pass") is True), "failed": 1, "auto_corrected": 0},
              "reusable_units": ["verify-source"] if source_unit.get("pass") else []}
    save(run / "validation-verify.json", formal)
    return {"pass": False, "validation_path": str(run / "validation-verify-current-parts.json"),
            "counts": counts, "units": [{"unit": unit["unit"], "pass": unit["pass"], "errors": unit["errors"]} for unit in units]}
  combined = {"session": "cross-session", "unit": "verify-current", "status": "complete", "verdicts": union}
  check_report(combined, {"session": "cross-session", "unit": "verify-current", "input_reports": [str(validated_path(run, "cross-session"))]}, "verify", canonical)
  destination = validated_path(run, "verify-current")
  save(destination, combined)
  validation.update({"validated_path": str(destination), "report": combined})
  save(run / "validation-verify-current-parts.json", validation)
  current_unit = {"unit": "verify-current", "pass": True, "errors": [], "actual_report_path": str(destination),
                  "validated_path": str(destination), "original_report": combined, "report": combined,
                  "critic_parts_validation": str(run / "validation-verify-current-parts.json")}
  formal = {"schema_version": 1, "run_id": plan["run_id"], "stage": "verify", "pass": True, "binding": binding(plan, run),
            "units": [source_unit, current_unit], "counts": {"expected": 2, "passed": 2, "failed": 0, "auto_corrected": 0},
            "reusable_units": ["verify-source", "verify-current"]}
  save(run / "validation-verify.json", formal)
  return {"pass": True, "validation_path": str(run / "validation-verify-current-parts.json"), "validated_path": str(destination),
          "counts": counts, "units": [{"unit": unit["unit"], "pass": unit["pass"], "errors": unit["errors"]} for unit in units]}


CHANGE_KINDS = ("rule", "skill", "command", "hook", "script")
CONTENT_KINDS = ("rule", "skill", "command")
SKILL_HEADER = re.compile(r"Base directory for this skill: (\S+)")


def flat_strings(value):
  if isinstance(value, str):
    yield value
  elif isinstance(value, dict):
    for child in value.values():
      yield from flat_strings(child)
  elif isinstance(value, list):
    for child in value:
      yield from flat_strings(child)


def parse_change(change):
  """A caller mistake (bad kind or time) is a usage error; unreadable session evidence is not."""
  require(isinstance(change, dict), "change must be an object")
  require(isinstance(change.get("id"), str) and bool(change["id"]), "change requires id")
  require(change.get("kind") in CHANGE_KINDS, "change kind must be one of " + ", ".join(CHANGE_KINDS))
  parsed = {**change, "effective": moment(change.get("effective_time"))}
  if change["kind"] in ("skill", "command"):
    require(isinstance(change.get("name"), str) and bool(change["name"]), "skill and command changes require name")
  return parsed


def raw_session_rows(path):
  """Raw line numbers, the same ones prepare writes as source_line."""
  rows = []
  for number, raw in enumerate(Path(path).read_bytes().split(b"\n"), 1):
    if not raw.strip():
      continue
    try:
      row = json.loads(raw.decode("utf-8"))
      require(isinstance(row, dict), "record is not an object")
      rows.append((number, row, None))
    except (ValueError, TypeError):
      rows.append((number, None, "damaged line"))
  return rows


def tool_ids(row):
  message = row.get("message")
  content = message.get("content") if isinstance(message, dict) else None
  uses, results = [], []
  for block in content if isinstance(content, list) else []:
    if isinstance(block, dict) and block.get("type") == "tool_use" and isinstance(block.get("id"), str):
      uses.append((block["id"], block))
    elif isinstance(block, dict) and block.get("type") == "tool_result" and isinstance(block.get("tool_use_id"), str):
      results.append((block["tool_use_id"], block))
  return uses, results


def locate_event(rows, source_line, tool_call_id):
  """(line, key, kind) or an error; the call ID wins over any cited line."""
  if tool_call_id is not None:
    for want in (0, 1):
      for number, row, _ in rows:
        if row is not None and any(key == tool_call_id for key, _ in tool_ids(row)[want]):
          return number, tool_call_id, "tool_call_id", None
    return None, tool_call_id, "tool_call_id", "tool call id not found in session"
  by_line = {number: row for number, row, _ in rows}
  if source_line not in by_line:
    return None, None, None, "source_line is outside the session file"
  if by_line[source_line] is None:
    return None, None, None, "event line is damaged"
  uses, results = tool_ids(by_line[source_line])
  calls = uses or results
  # Several calls on one line cannot be told apart without a call ID, so that action keeps the line key.
  if len(calls) == 1:
    # The call row and its result row are one event. Taking the cited row's own time gave one event two
    # times, and ingest rejects an event whose time changes between reports.
    return locate_event(rows, source_line, calls[0][0])
  return source_line, None, None, None


def rule_loads(rows, before):
  loads = []
  for number, row, _ in rows:
    attachment = row.get("attachment") if row else None
    if number < before and isinstance(attachment, dict) and attachment.get("type") == "instructions":
      loads.append((number, row.get("timestamp"), list(flat_strings(attachment))))
  return loads


def skill_loads(rows, before, kind, name):
  loads = []
  ordered = [(number, row) for number, row, _ in rows if row and number < before]
  for at, (number, row) in enumerate(ordered):
    if row.get("type") == "assistant":
      continue
    attachment = row.get("attachment")
    if isinstance(attachment, dict) and attachment.get("type") == "invoked_skills":
      for entry in attachment.get("skills") if isinstance(attachment.get("skills"), list) else []:
        if isinstance(entry, dict) and entry.get("name") == name:
          loads.append((number, row.get("timestamp"), [str(entry.get("content", ""))]))
      continue
    for leaf in flat_strings(row.get("message", row.get("content", ""))):
      header = SKILL_HEADER.match(leaf.lstrip())
      if kind == "skill" and header and header.group(1).rstrip("/").rsplit("/", 1)[-1] == name:
        loads.append((number, row.get("timestamp"), [leaf]))
      elif kind == "command" and any(f"<command-name>{slash}{name}</command-name>" in leaf for slash in ("/", "")):
        # Only the expansion right behind this invoke is its text. The next interaction (an assistant turn, a
        # tool result, a user message) or the next command ends it, so a later command's expansion is never borrowed.
        body = list(flat_strings(row.get("message", row.get("content", ""))))
        for _follow_number, follow in ordered[at + 1:]:
          texts = list(flat_strings(follow.get("message", follow.get("content", ""))))
          if not follow.get("isMeta") or any("<command-name>" in text for text in texts):
            break
          body.extend(texts)
        loads.append((number, row.get("timestamp"), body))
  return loads


def event_evidence(session_file, change_id, change=None, source_line=None, tool_call_id=None):
  """Which event this is, when it happened, and whether the changed text was loaded then.

  Every answer is derived from the raw session file. The caller supplies no time, so a model-written
  event_time can never reach the result. "unchecked" always carries a reason and is never read as loaded.
  """
  require(source_line is not None or tool_call_id is not None, "event-evidence requires --source-line or --tool-call-id")
  # A missing change would otherwise become the key suffix "|None" and look like a real observation.
  require(isinstance(change_id, str) and bool(change_id.strip()), "event-evidence requires a change id")
  session = Path(session_file).expanduser()
  out = {"session": session.stem, "change_id": change_id, "event_key": None, "event_key_kind": None,
         "observation_key": None, "source_line": source_line, "event_time": None, "loaded": "unchecked",
         "basis": "unchecked", "load_line": None, "load_time": None, "reason": None}

  def unchecked(reason):
    return {**out, "reason": reason}

  def keyed(key, kind):
    out.update({"event_key": key, "event_key_kind": kind, "observation_key": f"{key}|{change_id}"})

  if tool_call_id is not None:
    keyed(tool_call_id, "tool_call_id")
  elif isinstance(source_line, int) and not isinstance(source_line, bool):
    keyed(f"{session.stem}:{source_line}", "session_line")
  else:
    return unchecked("source_line must be an integer")
  if not session.is_file():
    return unchecked("session file not found")
  rows = raw_session_rows(session)
  line, key, kind, error = locate_event(rows, source_line, tool_call_id)
  if error:
    return unchecked(error)
  if key is not None:
    keyed(key, kind)
  out["source_line"] = line
  row = next(row for number, row, _ in rows if number == line)
  try:
    event_time = moment(row.get("timestamp"))
  except Invalid:
    return unchecked("event row has no valid timestamp")
  out["event_time"] = iso(event_time)
  if change is None:
    return unchecked("change definition (kind, effective_time) not found")
  effective = change["effective"]
  if event_time < effective:
    return {**out, "loaded": "not_loaded", "basis": "event_before_effective",
            "reason": "event happened before the change took effect"}
  if change["kind"] in ("hook", "script"):
    return {**out, "loaded": "loaded", "basis": "effective_time", "load_time": iso(effective),
            "reason": "event is at or after the effective time, so the file on disk was the new version"}
  damaged = [number for number, parsed, _ in rows if parsed is None and number < line]
  if damaged:
    return unchecked("damaged session lines before the event: " + ",".join(map(str, damaged)))
  new_text = change.get("new_text") or ""
  if not new_text.strip():
    return unchecked("new_text is missing, so the version cannot be compared")
  if change["kind"] == "rule":
    loads = rule_loads(rows, line)
    if not loads:
      return unchecked("no instructions attachment before the event")
    basis = "instructions_attachment"
  else:
    loads = skill_loads(rows, line, change["kind"], change["name"])
    basis = change["kind"] + "_invoke"
    if not loads:
      return {**out, "loaded": "not_loaded", "basis": "never_loaded",
              "reason": f"{change['kind']} {change['name']} was never invoked or re-injected before the event"}
  number, stamp, leaves = loads[-1]
  out["load_line"] = number
  try:
    loaded_at = moment(stamp)
  except Invalid:
    return unchecked("the last load has no valid timestamp")
  out["load_time"] = iso(loaded_at)
  if not any(new_text in leaf for leaf in leaves):
    return {**out, "loaded": "not_loaded", "basis": "last_load_is_old_version",
            "reason": "the last load before the event does not contain the new text"}
  if not effective <= loaded_at <= event_time:
    return {**out, "loaded": "not_loaded", "basis": "load_time_outside_effective_window",
            "reason": "load time is not within effective time <= load time <= event time"}
  return {**out, "loaded": "loaded", "basis": basis, "reason": "the last load before the event contains the new text"}


def find_change(context, change_id):
  if isinstance(context, dict):
    if change_id in (context.get("change_id"), context.get("id")) and "kind" in context and "effective_time" in context:
      return context
    for child in context.values():
      found = find_change(child, change_id)
      if found:
        return found
  elif isinstance(context, list):
    for child in context:
      found = find_change(child, change_id)
      if found:
        return found
  return None


def add_event_evidence(report, item, plan, run):
  """Tracking observations carry model-written evidence; time and load judgement come from the raw session.

  Only an action entry becomes an event. A context entry joins the nearest earlier action of its own
  session (the first action when none is earlier) and carries that event_key, so ingest never recomputes it.
  """
  observations = report.get("tracking_observations")
  if not isinstance(observations, list):
    return
  sources = {s["session"]: s["source"] for s in plan["sessions"]}
  context = None
  if (run / "tracking-context.json").is_file():
    with suppress(ValueError, OSError):
      context = load(run / "tracking-context.json")
  orphans = []
  for index, observation in enumerate(observations):
    evidence = observation.get("evidence") if isinstance(observation, dict) else None
    entries = [evidence] if isinstance(evidence, dict) else evidence if isinstance(evidence, list) else []
    change_id = observation.get("change_id") if isinstance(observation, dict) else None
    spec = find_change(context, change_id)
    try:
      change = parse_change({"id": change_id, **spec}) if spec else None
    except Invalid:
      change = None
    events = []
    actions = []
    supports = []
    for position, entry in enumerate(entries):
      if not isinstance(entry, dict):
        continue
      entry.pop("event_time", None)
      entry.pop("event_key", None)
      session = entry.get("session", item.get("session"))
      try:
        event = event_evidence(sources.get(session, ""), change_id, change, source_line=entry.get("source_line"))
      except Invalid as error:
        event = {"loaded": "unchecked", "basis": "unchecked", "reason": str(error), "event_time": None}
      if event["event_time"]:
        entry["event_time"] = event["event_time"]
      line = event.get("source_line") or entry.get("source_line")
      if entry.get("role") == "context":
        supports.append((position, entry, session, line))
      else:
        events.append(event)
        actions.append((session, line, event))
    for position, entry, session, line in supports:
      mine = [action for action in actions if action[0] == session]
      if not mine:
        orphans.append(f"tracking_observations[{index}] (issue_id={observation.get('issue_id')}, change_id={change_id}): "
                       f"context evidence[{position}] (session={session}, source_line={entry.get('source_line')}) "
                       "has no action in the same session")
        continue
      earlier = [action for action in mine if action[1] <= line]
      home = max(earlier, key=lambda action: action[1]) if earlier else min(mine, key=lambda action: action[1])
      entry["event_key"] = home[2].get("event_key")
    # Always overwrite: an observation without action evidence must not keep events the model wrote itself.
    if isinstance(observation, dict):
      observation["events"] = events
  shown = orphans[:ACCOUNTING_ERROR_CAP]
  require(not orphans, "; ".join(shown + ([f"(+{len(orphans) - len(shown)} more context evidence without an action)"]
                                          if len(orphans) > len(shown) else [])))


def validate(args):
  plan, run = plan_context(args.plan)
  canonical = load(run / "canonical.json")
  contract = binding(plan, run)
  if args.stage == "verify" and args.critic_parts:
    if (run / "composition-validation.json").exists():
      prior_composition(plan, run)
    else:
      prior_validation(plan, run, "cross")
    previous = prior_validation(plan, run, "verify", must_pass=False, check_parts=False) if (run / "validation-verify.json").exists() else {"units": []}
    source_unit = next((unit for unit in previous["units"] if unit["unit"] == "verify-source" and unit["pass"]), None)
    if source_unit is None:
      source_unit = validate_verify_source(args, plan, run, canonical)
    require(source_unit.get("pass") is True, "verify-source must pass before verify-current parts")
    save(run / "validation-verify.json", {"schema_version": 1, "run_id": plan["run_id"], "stage": "verify",
      "pass": False, "binding": binding(plan, run), "units": [source_unit],
      "counts": {"expected": 1, "passed": 1, "failed": 0, "auto_corrected": 0}, "reusable_units": ["verify-source"]})
    return validate_verify_parts(args, plan, run, canonical, source_unit)
  items = stage_items(plan, run, args.stage)
  agents = transcript_agents(args.transcript_dir)
  previous = None
  if (run / f"validation-{args.stage}.json").exists():
    with suppress(ValueError, OSError, KeyError, TypeError):
      previous = prior_validation(plan, run, args.stage, must_pass=False)
  units = []
  for item in items:
    unit = {"unit": item["unit"], "pass": False, "errors": []}
    try:
      agent, target, original = report_writer(agents, item)
      try:
        normalized = check_report(original, item, args.stage, canonical)
        if args.stage in ("reading", "session-merge", "cross"):
          add_event_evidence(normalized, item, plan, run)
        if args.stage == "cross":
          check_tracking_accounting(normalized, item)
      except EvidenceErrors as misses:
        normalized = None
        unit["errors"].extend(failure["error"] for failure in misses.failures)
        unit["quote_failures"] = quote_windows(misses.failures, plan)
      if args.stage == "reading":
        unit["errors"].extend(coverage_errors(agent, item))
      if args.stage == "verify" and normalized is not None:
        unit["errors"].extend(critic_read_errors(agent, item, normalized, canonical, plan))
      require(not unit["errors"], "; ".join(unit["errors"]))
      destination = validated_path(run, item["unit"])
      save(destination, normalized)
      corrections = [{"record_id": record.get("id"), "rule": e["correction"], "from": e["corrected_from"],
                      "to": {"source_line": e["source_line"], "quote": e["quote"]}}
                     for record in report_records(normalized) for e in record.get("evidence", [])
                     if isinstance(e, dict) and "correction" in e]
      unit.update({"pass": True, "agent": agent["path"], "actual_report_path": str(target),
                   "validated_path": str(destination), "original_report": original, "report": normalized,
                   "auto_corrections": corrections})
    except (OSError, ValueError, KeyError, TypeError) as error:
      msg = str(error)
      if msg.startswith("no successful report") and Path(item["report_path"]).is_file():
        try:
          load(Path(item["report_path"]))
        except ValueError as json_error:
          msg = f"{msg} (JSON parse error in {item['report_path']}: {json_error})"
      if not unit["errors"]:
        unit["errors"].append(msg)
      # No new writer is not evidence of failure of a previously validated, unchanged unit.
      if previous and not any(Path(a["path"]) == Path(p.get("agent", "")) for a in agents for p in previous["units"] if p["unit"] == item["unit"]):
        prior = next((p for p in previous["units"] if p["unit"] == item["unit"] and p["pass"]), None)
        if prior and msg.startswith("no successful report"):
          unit = prior
    units.append(unit)
  success = all(unit["pass"] for unit in units)
  result = {"schema_version": 1, "run_id": plan["run_id"], "stage": args.stage, "pass": success,
            "binding": contract, "units": units,
            "counts": {"expected": len(items), "passed": sum(u["pass"] for u in units), "failed": sum(not u["pass"] for u in units),
                       "auto_corrected": sum(len(u.get("auto_corrections", [])) for u in units)},
            "reusable_units": [u["unit"] for u in units if u["pass"]]}
  path = run / f"validation-{args.stage}.json"
  save(path, result)
  return {"pass": success, "validation_path": str(path), "counts": result["counts"],
          "reusable_units": result["reusable_units"],
          "units": [{k: u[k] for k in ("unit", "pass", "errors")} for u in units]}


def usage(args):
  agents = transcript_agents(args.transcript_dir)
  fields = {"input": "input_tokens", "output": "output_tokens", "cache_read": "cache_read_input_tokens", "cache_creation": "cache_creation_input_tokens"}
  totals = {key: 0 for key in fields}
  unknown = 0
  zeros = 0
  tool_ids = set()
  for agent in agents:
    tool_ids.update(agent["tool_ids"])
    for data in agent["messages"].values():
      if not isinstance(data, dict) or not any(key in data for key in fields.values()):
        unknown += 1
        continue
      values = {key: data.get(source, 0) for key, source in fields.items()}
      require(all(isinstance(v, (int, float)) and v >= 0 for v in values.values()), "invalid usage value")
      if not any(values.values()):
        zeros += 1
      if any(source not in data for source in fields.values()):
        unknown += 1
      for key, value in values.items():
        totals[key] += value
  return {"totals": totals, "unknown_messages": unknown, "all_zero_messages": zeros,
          "agent_count": len(agents), "tool_uses": len(tool_ids), "billing": False,
          "limitations": ["visible transcript usage only; unknown fields are not measured zero"]}


def is_resolved_non_human_unknown(entry):
  if not isinstance(entry, dict) or entry.get("reasons") != ["human-origin-unconfirmed"] or entry.get("limitations"):
    return False
  source = Path(entry.get("source", ""))
  if not source.is_file():
    return False
  rows = []
  for _, row, err in records(source):
    if err:
      return False
    rows.append(row)
  return local_command_only_rows(rows) or (not any(human_input(row) for row in rows) and any(sdk_origin_row(row) for row in rows))


def is_local_command_only_entry(entry):
  if not isinstance(entry, dict) or entry.get("reasons") != ["human-origin-unconfirmed"] or entry.get("limitations"):
    return False
  source = Path(entry.get("source", ""))
  if not source.is_file():
    return False
  rows = []
  for _, row, err in records(source):
    if err:
      return False
    rows.append(row)
  return local_command_only_rows(rows)


def finish(args):
  plan, run = plan_context(args.plan)
  unresolved_unknown = [u for u in plan.get("unknown", []) if not is_resolved_non_human_unknown(u)]
  require(not any(s.get("limitations") for s in plan["sessions"]) and not unresolved_unknown, "source parsing or classification gaps prevent complete coverage")
  composition = composition_validation_summary(plan, run) if (run / "composition-validation.json").exists() else None
  if composition is None:
    for stage in STAGES:
      prior_validation(plan, run, stage)
  else:
    for stage in ("reading", "session-merge"):
      prior_validation(plan, run, stage)
  critics = prior_validation(plan, run, "verify")
  require({u["unit"] for u in critics["units"] if u["pass"]} == {"verify-source", "verify-current"}, "both critics must pass")
  report = run / "final.md"
  require(report.is_file() and bool(report.read_text(encoding="utf-8").strip()), "final.md missing or empty")
  completion = {"schema_version": 1, "run_id": plan["run_id"], "scope": plan["scope"],
                "window_start": plan["window_start"], "cutoff": plan["cutoff"], "status": "complete",
                "validation_pass": True, "report_path": str(report)}
  save(run / "completion.json", completion)
  return {"completion_path": str(run / "completion.json"), "status": "complete"}


class Parser(argparse.ArgumentParser):
  def error(self, message):
    raise Invalid(message)


def parser():
  root = Parser(description=__doc__)
  commands = root.add_subparsers(dest="command", required=True)
  repo = Path(__file__).resolve().parents[2]
  inv = commands.add_parser("inventory")
  inv.add_argument("--projects-root", default=str(Path.home() / ".claude/projects"))
  inv.add_argument("--reports-root", default=str(repo / "reports/local-analysis/retro-workflow"))
  inv.add_argument("--out", required=True)
  inv.add_argument("--now")
  prep = commands.add_parser("prepare")
  prep.add_argument("--inventory", required=True)
  prep.add_argument("--out", required=True)
  prep.add_argument("--window", required=True, choices=["day", "last-success", "custom"])
  prep.add_argument("--start")
  prep.add_argument("--project", action="append", default=[])
  prep.add_argument("--exclude-session", action="append", default=[])
  prep.add_argument("--include-forwarded", dest="include_forwarded", action="store_true", default=True)
  prep.add_argument("--exclude-forwarded", dest="include_forwarded", action="store_false")
  # window re-reads only the last request before the window; full re-read ~half of each daily run (2026-10-31..11-04).
  prep.add_argument("--depth", choices=["full", "window"], default="window")
  prep.add_argument("--focus", default="全面")
  # Magpie Google 通道為 concurrency=1、排隊逾時 600s；大背景單每輪約 200s，並行超過 2 席會讓後段請求排隊逾時斷線。
  prep.add_argument("--concurrency", type=int, choices=range(1, 17), default=2)
  for stage in ("read", "merge", "verify"):
    prep.add_argument("--" + stage + "-model")
    prep.add_argument("--" + stage + "-effort", choices=["low", "medium", "high", "xhigh", "max"] )
  prep.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
  prep.add_argument("--repair-model")
  for name in ("stage-args", "validate", "finish"):
    sub = commands.add_parser(name)
    sub.add_argument("--plan", required=True)
    if name != "finish":
      sub.add_argument("--stage", required=True, choices=STAGES)
    if name == "validate":
      sub.add_argument("--transcript-dir", action="append", default=[])
      sub.add_argument("--critic-parts")
    if name == "stage-args":
      sub.add_argument("--critic-parts")
  compose = commands.add_parser("compose-cross")
  compose.add_argument("--manifest", required=True)
  compose.add_argument("--check-only", action="store_true")
  event = commands.add_parser("event-evidence")
  event.add_argument("--session-file", required=True)
  event.add_argument("--source-line", type=int)
  event.add_argument("--tool-call-id")
  event.add_argument("--change", required=True, help="JSON object, or a path to one: id, kind, effective_time, new_text, name")
  metering = commands.add_parser("usage")
  metering.add_argument("--transcript-dir", action="append", required=True)
  return root


def event_evidence_command(args):
  text = args.change.strip()
  try:
    spec = json.loads(text if text.startswith("{") else Path(text).read_text(encoding="utf-8"))
  except (ValueError, OSError) as error:
    raise Invalid("--change is neither a JSON object nor a readable JSON file") from error
  change = parse_change(spec)
  return event_evidence(args.session_file, change["id"], change, args.source_line, args.tool_call_id)


def main():
  try:
    args = parser().parse_args()
    handlers = {"inventory": inventory, "prepare": prepare, "stage-args": stage_args,
                "validate": validate, "usage": usage, "finish": finish,
                "event-evidence": event_evidence_command, "compose-cross": compose_cross}
    result = handlers[args.command](args)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("pass") is False else 0
  except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
    print(json.dumps({"pass": False, "error": str(error)}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
  sys.exit(main())

