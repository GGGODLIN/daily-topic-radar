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
SHARD_CHARS = 6 * 140000
STAGES = ("reading", "session-merge", "cross", "verify")
DESTINATIONS = ("kept", "merged", "reclassified", "rejected")
ACCOUNTING_ERROR_CAP = 25
DEFAULT_MODEL = "gpt-6.1-sol[1m]"
DEFAULT_ARGON_MODEL = "google/gemini-4-argon:high"
DEFAULT_TERRA_MODEL = "gpt-6.1-terra"
DEFAULT_SOL_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORTS = {"read": "high", "merge": "high", "verify": "xhigh"}
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
  if any(row.get("promptSource") == "sdk" for row in rows):
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
  table = read_model_table()
  return format_model(table["GPT_TERRA"], DEFAULT_EFFORTS["read"])


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


def count_body_pages_and_units(body):
  units = 0
  total_pages = 0
  for position in range(0, max(1, len(body)), SHARD_CHARS):
    units += 1
    total_pages += len(slice_page_chunks(body[position:position + SHARD_CHARS]))
  return total_pages, units


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
                    "projects": projects, "long_sessions": [row["session"] for row in selected if row["estimated_chars"] > SHARD_CHARS],
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


def pages(text, directory, prefix):
  directory.mkdir(parents=True, exist_ok=True)
  result = []
  for number, chunk in enumerate(slice_page_chunks(text), 1):
    page_id = f"{prefix}-p{number:02d}"
    path = directory / f"p{number:02d}.txt"
    path.write_text(chunk + "\nEND_PAGE " + page_id + "\n", encoding="utf-8")
    result.append({"id": page_id, "path": str(path)})
  return result


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
    for unit_number, position in enumerate(range(0, max(1, len(body)), SHARD_CHARS), 1):
      unit = f"{name}-u{unit_number:02d}"
      unit_dir = home / f"u{unit_number:02d}"
      page_list = pages(body[position:position + SHARD_CHARS], unit_dir, unit)
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
  models = {"read": args.read_model if args.read_model is not None else DEFAULT_ARGON_MODEL,
            "merge": args.merge_model if args.merge_model is not None else DEFAULT_ARGON_MODEL,
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


def prior_validation(plan, run, stage, must_pass=True):
  path = run / f"validation-{stage}.json"
  require(path.exists(), "missing preceding validation: " + stage)
  result = load(path)
  require(result.get("run_id") == plan["run_id"] and result.get("stage") == stage, "validation identity mismatch")
  require(result.get("binding") == binding(plan, run), "source, plan, or models changed since validation")
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


def stage_items(plan, run, stage, enforce=True):
  if enforce:
    for previous in STAGES[:STAGES.index(stage)]:
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
  return [{"session": "cross-session", "unit": unit, "report_path": str(run / "reports" / (unit + ".json")),
           "input_reports": [str(validated_path(run, "cross-session"))], "source_documents": documents, **contexts}
          for unit in ("verify-source", "verify-current")]


def stage_args(args):
  plan, run = plan_context(args.plan)
  items = stage_items(plan, run, args.stage)
  failures = []
  reused = []
  validation_file = run / f"validation-{args.stage}.json"
  if validation_file.exists():
    try:
      previous = prior_validation(plan, run, args.stage, must_pass=False)
    except (ValueError, OSError, KeyError, TypeError):
      previous = None
      failures.append({"unit": "validation", "errors": ["previous validation stale or invalid; no units reused"]})
    if previous:
      reused = [unit["unit"] for unit in previous["units"] if unit["pass"]]
      failures.extend({"unit": unit["unit"], "errors": unit["errors"],
                       **({"quote_failures": unit["quote_failures"]} if unit.get("quote_failures") else {})}
                      for unit in previous["units"] if not unit["pass"])
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
      if not isinstance(report, dict) or report.get("unit") != item["unit"] or report.get("session") != item.get("session", "cross-session"):
        continue
      # A receipt typo can point at a real report in this run, never at another run's output.
      run_root = expected_path.parent.parent
      if not target.is_relative_to(run_root):
        continue
      writes = [op for op in agent["operations"] if operation_path(op) == target and op["name"] in {"Write", "Edit"}]
      state = bash_states.get(target)
      for op in writes:
        fields = op["input"]
        if op["name"] == "Write":
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
  require(len(found) == len(ids) and set(found) == ids, "missing or duplicate critic verdicts")


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


def critic_read_errors(agent, item, report, canonical, plan):
  errors = []
  for path in item["input_reports"]:
    required = {n for n, line in enumerate(Path(path).read_text().split("\n"), 1) if line.strip()}
    if required - read_lines(agent, path):
      errors.append("critic did not read the cross-session report")
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
    if item["unit"] == "verify-current" and verdict["verdict"] != "UNCERTAIN":
      current = verdict.get("current_evidence", [])
      if not current:
        errors.append(f"current-state verdict lacks current evidence (pattern={verdict.get('pattern_id')})")
      for evidence in current:
        path = Path(evidence.get("path", "")).expanduser().resolve()
        if not any(op["name"] == "Read" and operation_path(op) == path and op["text"].strip() for op in agent["operations"]):
          errors.append(f"current-state evidence lacks successful Read (pattern={verdict.get('pattern_id')}, path={path})")
  return errors


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
  usable = [(number, row) for number, row, _ in rows if row and number < before and row.get("type") != "assistant"]
  for at, (number, row) in enumerate(usable):
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
        body = [text for follow_number, follow in usable[at:]
                if follow_number == number or (follow.get("isMeta") and follow_number > number)
                for text in flat_strings(follow.get("message", follow.get("content", "")))]
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
  """Tracking observations carry model-written evidence; time and load judgement come from the raw session."""
  observations = report.get("tracking_observations")
  if not isinstance(observations, list):
    return
  sources = {s["session"]: s["source"] for s in plan["sessions"]}
  context = None
  if (run / "tracking-context.json").is_file():
    with suppress(ValueError, OSError):
      context = load(run / "tracking-context.json")
  for observation in observations:
    evidence = observation.get("evidence") if isinstance(observation, dict) else None
    entries = [evidence] if isinstance(evidence, dict) else evidence if isinstance(evidence, list) else []
    change_id = observation.get("change_id") if isinstance(observation, dict) else None
    spec = find_change(context, change_id)
    try:
      change = parse_change({"id": change_id, **spec}) if spec else None
    except Invalid:
      change = None
    events = []
    for entry in entries:
      if not isinstance(entry, dict):
        continue
      entry.pop("event_time", None)
      session = entry.get("session", item.get("session"))
      try:
        event = event_evidence(sources.get(session, ""), change_id, change, source_line=entry.get("source_line"))
      except Invalid as error:
        event = {"loaded": "unchecked", "basis": "unchecked", "reason": str(error), "event_time": None}
      if event["event_time"]:
        entry["event_time"] = event["event_time"]
      events.append(event)
    if events:
      observation["events"] = events


def validate(args):
  plan, run = plan_context(args.plan)
  items = stage_items(plan, run, args.stage)
  contract = binding(plan, run)
  canonical = load(run / "canonical.json")
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
  unresolved_unknown = [u for u in plan.get("unknown", []) if not is_local_command_only_entry(u)]
  require(not any(s.get("limitations") for s in plan["sessions"]) and not unresolved_unknown, "source parsing or classification gaps prevent complete coverage")
  for stage in STAGES:
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
  prep.add_argument("--concurrency", type=int, choices=range(1, 5), default=4)
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
                "event-evidence": event_evidence_command}
    result = handlers[args.command](args)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("pass") is False else 0
  except (OSError, ValueError, KeyError, TypeError, ImportError) as error:
    print(json.dumps({"pass": False, "error": str(error)}, ensure_ascii=False))
    return 2


if __name__ == "__main__":
  sys.exit(main())

