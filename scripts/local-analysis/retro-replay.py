#!/usr/bin/env python3
"""Replay the moment a resident rule was broken, once per rule version, and hand the replies to retro-judge.

`run` rebuilds the conversation before one event from the raw session record (the 01 prototype's way: no
Claude Code is started), swaps only the target rule paragraph, asks the session's own model for the next
step through the session's own relay, and keeps a small run record. It shows a token and cost estimate
first and calls nothing without --approve. It never decides a verdict: a finished primary run leaves
`judge-input.json`, which retro-judge reads as is.

Frozen parameters come from the 01 feasibility report: five unchanged-rule calls first (at least two must
recur the violation, else the rebuild is not faithful), then ten per rule version, and up to three retries
for a transient error that do not count as an attempt.
"""

import argparse
import copy
import importlib.util
import json
import math
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
SESSION_RETRO = HERE / "session-retro.py"
JUDGE_SCRIPT = HERE / "retro-judge.py"
REPO = HERE.parents[1]

PRECHECK_RUNS = 5
MIN_REPRODUCED = 2
ARM_RUNS = 10
SIDE_EFFECT_RUNS = 5
MAX_RETRIES = 3
OUTPUT_TOKENS_ESTIMATE = 4000
SCHEMA_VERSION = 1

# 狀態對應的離開碼；2 留給用法錯誤（和 retro-judge 一致）。
EXIT_CODES = {"complete": 0, "awaiting_approval": 0, "cannot_replay": 10, "unfaithful": 11,
              "model_unavailable": 12, "incomplete": 13}
FINAL_STATUSES = ("complete", "cannot_replay", "unfaithful", "model_unavailable")

SENTINEL = "\u0001RETRO-RULE-SITE-{}\u0001"
CC_INSTRUCTIONS_HEAD = (
  "Codebase and user instructions are shown below. Be sure to adhere to these instructions. "
  "IMPORTANT: These instructions OVERRIDE any default behavior and you MUST follow them exactly as written."
)
TYPE_SUFFIX = {
  "Project": " (project instructions, checked into the codebase)",
  "Local": " (user's private project instructions, not checked in)",
  "AutoMem": " (user's auto-memory, persists across conversations)",
  "Managed": " (organization-managed policy instructions)",
  "User": " (user's private global instructions for all projects)",
}
# 這些附件類型是機器內部紀錄，不會送進模型。
NOT_SENT_ATTACHMENTS = {"prompt_snapshot", "prompt_render_point", "deferred_tools_record", "credential_org",
                        "command_permissions", "hook_success", "async_hook_response"}
# 其餘類型只有這幾種照二進位檔裡的範本重建；剩下的用欄位原文或 JSON 近似，差異要如實列出。
TEMPLATE_ATTACHMENTS = {"instructions", "output_style", "date", "model", "skill_listing", "mcp_instructions_delta",
                        "output_style_instructions"}
WIRE_FLAGS = ("systemTurns", "toolChangeHeader", "inlineTools", "contextRendering")
MODEL_GONE = re.compile(
  r"not[ _]found|unknown|unavailable|unsupported|not supported|does not exist|no longer|deprecated|invalid model",
  re.IGNORECASE)


class Usage(Exception):
  pass


class CannotReplay(Exception):
  pass


def now():
  return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def emit(payload):
  print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def load_judge():
  spec = importlib.util.spec_from_file_location("retro_judge", JUDGE_SCRIPT)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def save_json(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  temp = path.with_name(path.name + ".tmp")
  temp.write_text(json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
  os.replace(temp, path)


# ---- 輸入檢查 ---------------------------------------------------------------------------


def load_spec(path):
  try:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError) as error:
    raise Usage("spec is not readable JSON") from error
  need = {"change": dict, "event": dict, "route": dict, "condition": str, "judge": dict}
  if not isinstance(spec, dict) or spec.get("schema_version") != SCHEMA_VERSION:
    raise Usage("spec must be an object with schema_version 1")
  for key, kind in need.items():
    if not isinstance(spec.get(key), kind):
      raise Usage("spec." + key + " is missing or has the wrong type")
  if spec.get("purpose", "primary") not in ("primary", "side_effect"):
    raise Usage("spec.purpose must be primary or side_effect")
  for section, keys in (("change", ("id", "kind", "effective_time", "rule_path", "old_text", "new_text")),
                        ("event", ("session_file", "response_line")),
                        ("route", ("relay", "key_env", "label")),
                        ("judge", ("by", "evidence"))):
    for key in keys:
      if spec[section].get(key) in (None, ""):
        raise Usage("spec." + section + "." + key + " is missing")
  if not isinstance(spec["event"]["response_line"], int) or isinstance(spec["event"]["response_line"], bool):
    raise Usage("spec.event.response_line must be an integer")
  return spec


def origin_of(url):
  parts = urlsplit(url)
  return (parts.scheme + "://" + parts.netloc).lower()


def endpoint_for(spec, override):
  route = spec["route"]
  endpoint = override or route["relay"].rstrip("/") + route.get("path", "/v1/messages")
  # 走中轉站的模型一定走原本那個中轉站；換成別的來源就是把請求轉送到其他服務。
  if origin_of(endpoint) != origin_of(route["relay"]):
    raise Usage("endpoint is outside the session relay " + route["relay"] + "; the replay must use the same relay")
  return endpoint


# ---- 重建 -------------------------------------------------------------------------------


def read_rows(path):
  rows = []
  for number, raw in enumerate(Path(path).read_bytes().split(b"\n"), 1):
    if not raw.strip():
      continue
    try:
      row = json.loads(raw.decode("utf-8"))
    except ValueError:
      row = None
    rows.append((number, row if isinstance(row, dict) else None))
  return rows


def chain_to_root(entries, start_uuid):
  by_uuid = {row["uuid"]: (number, row) for number, row in entries if row.get("uuid")}
  out, uuid = [], start_uuid
  while uuid:
    if uuid not in by_uuid:
      raise CannotReplay("the conversation chain is broken: parent " + str(uuid) + " is not in the session file")
    number, row = by_uuid[uuid]
    out.append((number, row))
    uuid = row.get("parentUuid")
  return out[::-1]


def render_attachment(attachment):
  kind = attachment["type"]
  if kind in NOT_SENT_ATTACHMENTS:
    return None
  if kind == "instructions":
    parts = []
    for file in attachment.get("files", []):
      parts.append("Contents of " + file["path"] + TYPE_SUFFIX.get(file.get("type"), "") + ":\n"
                   + file["content"].strip())
    return CC_INSTRUCTIONS_HEAD + "\n" + "\n".join(parts) if parts else None
  if kind == "output_style":
    return attachment["style"] + " output style is active. Remember to follow the specific guidelines for this style."
  if kind == "model":
    return attachment.get("text")
  if kind == "date":
    return "Today's date is " + attachment["date"] + "."
  if kind == "skill_listing":
    return "The following skills are available for use with the Skill tool:\n\n" + attachment["content"]
  if kind == "mcp_instructions_delta":
    return "# MCP Server Instructions\n\n" + "\n\n".join(attachment.get("addedBlocks", []))
  if kind == "output_style_instructions":
    return attachment["style"].get("prompt")
  for key in ("content", "text", "additionalContext", "message"):
    if isinstance(attachment.get(key), str):
      return attachment[key]
  rest = {k: v for k, v in attachment.items() if k != "type"}
  return "[" + kind + "] " + json.dumps(rest, ensure_ascii=False)


def version_in(content, old_text, new_text):
  """(version, text) of the target paragraph in one rule file, None when neither is there.

  The new paragraph may contain the old one (a rule that only gained a clause), so the longer text is
  looked for first; a paragraph that occurs more than once cannot be swapped safely."""
  candidates = sorted((("old", old_text), ("new", new_text)), key=lambda c: -len(c[1]))
  for version, text in candidates:
    count = content.count(text)
    if count > 1:
      raise CannotReplay("the target paragraph occurs " + str(count) + " times in the rule file")
    if count == 1:
      return version, text
  return None


def resolve(value, texts):
  """Fill the site placeholders. Done on the parsed structure, so quotes and newlines need no escaping."""
  if isinstance(value, str):
    for index, text in enumerate(texts):
      value = value.replace(SENTINEL.format(index), text)
    return value
  if isinstance(value, dict):
    return {key: resolve(child, texts) for key, child in value.items()}
  if isinstance(value, list):
    return [resolve(child, texts) for child in value]
  return value


def leaves(value):
  if isinstance(value, str):
    yield value
  elif isinstance(value, dict):
    for child in value.values():
      yield from leaves(child)
  elif isinstance(value, list):
    for child in value:
      yield from leaves(child)


def snapshots_before(entries, line):
  return [row["attachment"] for number, row in entries
          if number < line and row.get("type") == "attachment"
          and isinstance(row.get("attachment"), dict) and row["attachment"].get("type") == "prompt_snapshot"]


def rebuild(rows, line, change, max_tokens):
  """Template request (target paragraph left as placeholders), the sites, and an itemized difference report."""
  by_line = dict(rows)
  event = by_line.get(line)
  if event is None or event.get("type") != "assistant" or not event.get("parentUuid"):
    raise CannotReplay("response_line " + str(line) + " is not an assistant record with a parent in the session file")
  damaged = [number for number, row in rows if row is None and number < line]
  if damaged:
    raise CannotReplay("damaged session lines before the event: " + ",".join(map(str, damaged)))
  model = event.get("requestedModel")
  if not isinstance(model, str) or not model:
    raise CannotReplay("the session record has no requestedModel for the event")
  entries = [(number, row) for number, row in rows if row is not None]
  nodes = chain_to_root(entries, event["parentUuid"])
  snapshots = snapshots_before(entries, line)
  if not snapshots:
    raise CannotReplay("no prompt_snapshot before the event")
  snapshot = snapshots[-1]
  with_tools = [s for s in snapshots if s.get("tools")]
  tool_source = with_tools[-1] if with_tools else {}
  tools = [{"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("schema") or {"type": "object"}}
           for t in tool_source.get("tools", [])]
  names = {t["name"] for t in tools}
  report = {"deferred_added": [], "removed": [], "thinking_dropped": 0, "tool_reference": 0,
            "template": {}, "approximated": {}, "not_sent": {}}
  sites, last_rule_file = [], "absent"
  messages = []

  def push(role, blocks):
    if messages and messages[-1]["role"] == role:
      messages[-1]["content"].extend(blocks)
    else:
      messages.append({"role": role, "content": list(blocks)})

  open_assistant, pending = None, []
  for number, row in nodes:
    kind = row.get("type")
    if kind == "attachment":
      attachment = row["attachment"]
      if attachment["type"] == "deferred_tools_record":
        for entry in attachment.get("entries", []):
          if entry["name"] not in names:
            tools.append({"name": entry["name"], "description": entry["description"], "input_schema": entry["input_schema"]})
            names.add(entry["name"])
            report["deferred_added"].append(entry["name"])
        continue
      if attachment["type"] == "deferred_tools_delta" and attachment.get("removedNames"):
        gone = set(attachment["removedNames"])
        tools[:] = [t for t in tools if t["name"] not in gone]
        names -= gone
        report["removed"].extend(sorted(gone))
      if attachment["type"] == "instructions":
        attachment = copy.deepcopy(attachment)
        for file in attachment.get("files", []):
          if file.get("path") != change["rule_path"]:
            continue
          found = version_in(file["content"], change["old_text"], change["new_text"])
          last_rule_file = found[0] if found else None
          if found:
            file["content"] = file["content"].replace(found[1], SENTINEL.format(len(sites)), 1)
            sites.append({"session_line": number, "path": change["rule_path"], "recorded": found[0]})
      text = render_attachment(attachment)
      kind_name = attachment["type"]
      if kind_name in NOT_SENT_ATTACHMENTS:
        report["not_sent"][kind_name] = report["not_sent"].get(kind_name, 0) + 1
      if text:
        bucket = "template" if kind_name in TEMPLATE_ATTACHMENTS else "approximated"
        report[bucket][kind_name] = report[bucket].get(kind_name, 0) + 1
        block = {"type": "text", "text": "<system-reminder>\n" + text + "\n</system-reminder>"}
        if open_assistant is None:
          push("user", [block])
        else:
          pending.append(block)
      continue
    if kind == "assistant":
      message = row["message"]
      blocks = []
      for content in message["content"]:
        if content["type"] in ("thinking", "redacted_thinking"):
          report["thinking_dropped"] += 1
          continue
        blocks.append({k: v for k, v in content.items() if k in ("type", "text", "id", "name", "input")})
      if open_assistant is not None and open_assistant == message.get("id"):
        messages[-1]["content"].extend(blocks)
      elif blocks:
        if pending:
          push("user", pending)
          pending = []
        push("assistant", blocks)
        open_assistant = message.get("id")
      continue
    if kind == "user":
      content = row["message"]["content"]
      blocks = [{"type": "text", "text": content}] if isinstance(content, str) else []
      if not isinstance(content, str):
        for block in content:
          if block.get("type") == "tool_result":
            block = dict(block)
            if isinstance(block.get("content"), list):
              kept = []
              for part in block["content"]:
                if part.get("type") in ("text", "image"):
                  kept.append(part)
                else:
                  report["tool_reference"] += 1
                  kept.append({"type": "text", "text": "[" + str(part.get("type")) + ": " + str(part.get("tool_name", "")) + "]"})
              block["content"] = kept
            block = {k: v for k, v in block.items() if k in ("type", "tool_use_id", "content", "is_error")}
          blocks.append(block)
      if open_assistant is not None and any(b.get("type") == "tool_result" for b in blocks):
        pending.extend(blocks)
      else:
        if pending:
          push("user", pending)
          pending = []
        open_assistant = None
        push("user", blocks)
  if pending:
    push("user", pending)
  if last_rule_file == "absent":
    raise CannotReplay("no instructions attachment before the event carries the rule file " + change["rule_path"])
  if last_rule_file is None:
    raise CannotReplay("rule version not found: neither the old nor the new paragraph is in the last instructions "
                       "attachment before the event")
  system = []
  prompt = snapshot.get("systemPrompt")
  for text in (snapshot.get("cliPrefix"), "\n\n".join(prompt) if isinstance(prompt, list) else prompt):
    if text:
      system.append({"type": "text", "text": text})
  template = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
  if tools:
    template["tools"] = tools
  report["outside"] = sum(1 for leaf in leaves(template["messages"])
                          if change["old_text"] in leaf or change["new_text"] in leaf)
  usage = (event.get("message") or {}).get("usage") or {}
  original = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
  facts = {
    "model": model, "effort": event.get("effort"), "client_version": event.get("version"),
    "original_input_tokens": original or None,
    "flags": {k: snapshot[k] for k in WIRE_FLAGS if k in snapshot},
    "snapshot_tools": len(tool_source.get("tools", [])), "tools_from_first_snapshot": not with_tools,
  }
  return template, sites, report, facts


def arm_bodies(template, sites):
  """The recorded arm keeps each site as the session had it; the swapped arm puts the other version everywhere."""
  recorded = [s["recorded"] for s in sites]
  other = {"old": "new", "new": "old"}[recorded[-1]]
  return {"original": recorded, "swapped": [other] * len(sites)}


def build_arm(template, sites, versions, change):
  texts = [change[v + "_text"] for v in versions]
  return resolve(template, texts)


def describe_differences(report, facts):
  suffix = re.search(r":([A-Za-z0-9_-]+)$", facts["model"])
  items = [
    {"id": "special_wire_formats_not_reproduced", "flags": facts["flags"], "client_version": facts["client_version"],
     "detail": "the client's system-turn, tool-change-header and inline-tool wire formats are not reproduced; "
               "every request uses the classic tools parameter and puts attachments in user turns"},
    {"id": "thinking_blocks_dropped", "count": report["thinking_dropped"],
     "detail": "assistant thinking blocks carry empty signatures and are left out"},
    {"id": "tool_reference_as_text", "count": report["tool_reference"],
     "detail": "tool_reference blocks become text placeholders"},
    {"id": "attachments_approximated", "types": report["approximated"],
     "detail": "attachment types without a known template are rendered from their field text or as JSON"},
    {"id": "attachments_rendered_from_template", "types": report["template"]},
    {"id": "attachments_not_sent", "types": report["not_sent"]},
    {"id": "tools_assembled", "snapshot_tools": facts["snapshot_tools"], "deferred_added": report["deferred_added"],
     "removed": report["removed"], "tools_from_first_snapshot": facts["tools_from_first_snapshot"],
     "detail": "tools come from the last snapshot that lists them plus the deferred-tools record"},
    {"id": "streaming_and_beta_headers", "detail": "the original request streamed and carried beta headers; "
                                                    "the replay does neither"},
    {"id": "thinking_depth_carrier", "carrier": "model_suffix" if suffix else "none",
     "session_effort": facts["effort"],
     "detail": "depth rides on the model string only; whether the original also sent a separate effort "
               "parameter is not known"},
  ]
  if report["outside"]:
    items.append({"id": "rule_text_outside_instructions", "count": report["outside"],
                  "detail": "the target paragraph also appears outside the instructions attachment and is not swapped"})
  return items


# ---- 事件證據與版本 ---------------------------------------------------------------------


def event_evidence(session_file, change, line):
  argv = [sys.executable, "-I", str(SESSION_RETRO), "event-evidence", "--session-file", str(session_file),
          "--source-line", str(line), "--change",
          json.dumps({"id": change["id"], "kind": change["kind"], "effective_time": change["effective_time"],
                      "new_text": change["new_text"]})]
  done = subprocess.run(argv, text=True, capture_output=True)
  try:
    result = json.loads(done.stdout)
  except ValueError as error:
    raise Usage("event-evidence gave no JSON") from error
  if result.get("pass") is False:
    raise Usage("event-evidence refused the change: " + str(result.get("error")))
  return result


def prepare(spec, args):
  """Everything that decides whether a replay is possible, before any model call."""
  change, event = spec["change"], spec["event"]
  session = Path(event["session_file"]).expanduser()
  if change["kind"] != "rule":
    raise CannotReplay("replay rebuilds resident rules only; a " + str(change["kind"]) + " change goes to layer 3")
  if not session.is_file():
    raise CannotReplay("session file not found: " + str(session))
  line = event["response_line"]
  evidence = event_evidence(session, change, event.get("source_line") or line)
  if evidence["loaded"] == "unchecked":
    raise CannotReplay("cannot tell which rule version the session had: " + str(evidence["reason"]))
  if spec.get("purpose", "primary") == "side_effect" and evidence["loaded"] != "loaded":
    raise CannotReplay("the change was not loaded at this moment (" + str(evidence["basis"])
                       + "), so it cannot serve as a side-effect check")
  template, sites, report, facts = rebuild(read_rows(session), line, change, args.max_tokens)
  versions = arm_bodies(template, sites)
  recorded = sites[-1]["recorded"]
  if (recorded == "new") != (evidence["loaded"] == "loaded"):
    raise CannotReplay("event evidence (" + str(evidence["basis"]) + ") and the recorded instructions disagree "
                       "about the rule version")
  return {"session": session, "evidence": evidence, "template": template, "sites": sites, "report": report,
          "facts": facts, "versions": versions, "recorded": recorded}


# ---- 估算 -------------------------------------------------------------------------------


def estimate(ctx, change, args, remaining):
  chars = max(len(json.dumps(build_arm(ctx["template"], ctx["sites"], v, change), ensure_ascii=False))
              for v in ctx["versions"].values())
  rebuilt = math.ceil(chars / args.chars_per_token)
  original = ctx["facts"]["original_input_tokens"]
  # 重建請求只有原請求的 81-95% token，原 usage 比較大時取它，估算寧可偏高。
  per_call = max(rebuilt, original or 0)
  cap_applies = per_call > args.cap_threshold
  multiplier = args.cap_multiplier if cap_applies else 1.0

  def usd(price_in, price_out):
    return per_call / 1e6 * price_in + OUTPUT_TOKENS_ESTIMATE / 1e6 * price_out

  list_call = usd(args.in_price, args.out_price)
  cap_call = usd(args.in_price * multiplier, args.out_price * multiplier)
  notes = ["output is estimated at " + str(OUTPUT_TOKENS_ESTIMATE) + " tokens per call, thinking included",
           "a response that did not move the task forward is run once more; those calls are not in the totals"]
  if ctx["method"] == "model":
    notes.append("the recurrence condition is judged by a model; those judge calls are not in the totals")
  return {
    "input_tokens_per_call": per_call, "input_tokens_rebuilt_est": rebuilt, "input_tokens_original": original,
    "chars_per_token": args.chars_per_token, "output_tokens_per_call": OUTPUT_TOKENS_ESTIMATE,
    "calls_remaining": remaining,
    "usd_list": {k: round(v * list_call, 6) for k, v in remaining.items()},
    "usd_cap": {k: round(v * cap_call, 6) for k, v in remaining.items()},
    "prices": {"input_per_million": args.in_price, "output_per_million": args.out_price,
               "cap_multiplier_applied": multiplier, "cap_threshold_tokens": args.cap_threshold},
    "billing": "the relay bill is not known; these are price-list estimates",
    "notes": notes,
  }


def ok_attempts(record, arm):
  return [a for a in record["attempts"] if a["arm"] == arm and a["outcome"] == "ok"]


def arm_need(record, arm, size):
  ok = ok_attempts(record, arm)
  moved = sum(1 for a in ok if a["progressed"])
  # 每個沒推進的回應只補跑 1 次；補跑的又沒推進就不再補，該組不足，由 retro-judge 判證據不足。
  allowed = size + sum(1 for a in ok[:size] if not a["progressed"])
  return max(0, min(size - moved, allowed - len(ok)))


def remaining_calls(record):
  size = record["plan"]["arm_runs"]
  total = arm_need(record, "original", size) + arm_need(record, "swapped", size)
  if record["purpose"] == "side_effect" or record.get("precheck"):
    return {"precheck": 0, "total": total}
  return {"precheck": max(0, PRECHECK_RUNS - len(ok_attempts(record, "original"))), "total": total}


def next_arm(record):
  if record["purpose"] == "primary" and not record.get("precheck"):
    return "original" if len(ok_attempts(record, "original")) < PRECHECK_RUNS else None
  size = record["plan"]["arm_runs"]
  needs = [arm for arm in ("original", "swapped") if arm_need(record, arm, size) > 0]
  if not needs:
    return None
  return min(needs, key=lambda arm: len(ok_attempts(record, arm)))


# ---- 模型呼叫 ---------------------------------------------------------------------------


def post(endpoint, key, payload, timeout):
  request = urllib.request.Request(endpoint, data=payload, method="POST", headers={
    "content-type": "application/json", "anthropic-version": "2023-06-01", "authorization": "Bearer " + key})
  started = time.monotonic()
  status, raw, error = 0, b"", None
  try:
    with urllib.request.urlopen(request, timeout=timeout) as response:
      status, raw = response.status, response.read()
  except urllib.error.HTTPError as caught:
    status, raw = caught.code, caught.read()
  except (socket.timeout, TimeoutError):
    error = "timeout"
  except OSError as caught:
    error = "connection error: " + type(caught).__name__
  secs = round(time.monotonic() - started, 2)
  parsed = None
  if status:
    try:
      parsed = json.loads(raw.decode("utf-8"))
    except ValueError:
      parsed = None
    if status != 200:
      error = raw[:300].decode("utf-8", "replace")
  return {"status": status, "secs": secs, "payload": parsed, "error": error.replace(key, "[key]") if error else None}


def classify(result):
  status, payload = result["status"], result["payload"]
  if status == 200:
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
      return "ok"
    return "transient"
  text = result["error"] or ""
  if status in (400, 403, 404, 410) and re.search("model", text, re.IGNORECASE) and MODEL_GONE.search(text):
    return "unavailable"
  if status in (0, 408, 429) or status >= 500:
    return "transient"
  return "fatal"


def attempt_once(endpoint, key, body, args):
  payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
  tries = []
  for retry in range(MAX_RETRIES + 1):
    result = post(endpoint, key, payload, args.timeout)
    kind = classify(result)
    entry = {"status": result["status"], "secs": result["secs"]}
    if kind != "ok" and result["error"]:
      entry["error"] = result["error"][:300]
    elif kind != "ok":
      entry["error"] = "the 200 reply has no content list"
    tries.append(entry)
    if kind == "ok":
      return "ok", tries, result["payload"]
    if kind != "transient":
      return ("unavailable" if kind == "unavailable" else "failed"), tries, None
    if retry < MAX_RETRIES:
      time.sleep(args.retry_wait)
  return "failed", tries, None


def usage_of(payload):
  usage = payload.get("usage")
  keys = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
  if not isinstance(usage, dict) or not all(isinstance(usage.get(k), int) for k in ("input_tokens", "output_tokens")):
    return None
  return {k: usage.get(k) or 0 for k in keys}


def keep_blocks(blocks):
  kept, omitted = [], {}
  for block in blocks:
    if isinstance(block, dict) and block.get("type") in ("text", "tool_use"):
      kept.append(block)
    elif isinstance(block, dict):
      omitted[str(block.get("type"))] = omitted.get(str(block.get("type")), 0) + 1
  return kept, omitted


def add_actual(record, usage, args):
  actual = record["actual"]
  if usage is None:
    actual["usage_complete"] = False
    return
  for key, value in usage.items():
    actual["usage"][key] += value
  total_in = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]
  multiplier = args.cap_multiplier if total_in > args.cap_threshold else 1.0
  actual["usage_est_usd_list"] = round(actual["usage_est_usd_list"] + total_in / 1e6 * args.in_price
                                       + usage["output_tokens"] / 1e6 * args.out_price, 6)
  actual["usage_est_usd_cap"] = round(actual["usage_est_usd_cap"] + multiplier * (
    total_in / 1e6 * args.in_price + usage["output_tokens"] / 1e6 * args.out_price), 6)


# ---- 執行與紀錄 -------------------------------------------------------------------------


def judge_config(args):
  return {"endpoint": args.judge_endpoint, "model": args.judge_model, "timeout": args.judge_timeout}


def judge_method(judge, spec, args):
  """The predicate (or the model) that reads the replies. Only used here to tell whether the original recurred."""
  predicate = judge.predicate_for(spec["condition"])
  if predicate is None and not (args.judge_endpoint and args.judge_model):
    raise Usage("this condition needs a model: give --judge-endpoint and --judge-model")
  return predicate


def precheck(record, judge, predicate, spec, args):
  first = ok_attempts(record, "original")[:PRECHECK_RUNS]
  runs = [{"id": "r" + str(a["n"]), "content": a["content"]} for a in first]
  counts, _ = judge.classify_group(runs, spec["condition"], predicate, judge_config(args), {"calls": 0})
  return {"runs": PRECHECK_RUNS, "reproduced": counts["violated"], "required": MIN_REPRODUCED,
          "passed": counts["violated"] >= MIN_REPRODUCED}


def finish(record, status, reason=None, next_layer=None):
  record["status"] = status
  record["result"] = {"status": status, "reason": reason, "next_layer": next_layer}
  record["updated_at"] = now()


def execute(record, run_dir, ctx, spec, args, endpoint, judge, predicate):
  change = spec["change"]
  bodies = {arm: build_arm(ctx["template"], ctx["sites"], v, change) for arm, v in ctx["versions"].items()}
  key = os.environ[spec["route"]["key_env"]]
  original = ctx["facts"]["original_input_tokens"]
  made = 0
  try:
    while True:
      if record["purpose"] == "primary" and not record.get("precheck") \
          and len(ok_attempts(record, "original")) >= PRECHECK_RUNS:
        record["precheck"] = precheck(record, judge, predicate, spec, args)
        save_json(run_dir / "run.json", record)
        if not record["precheck"]["passed"]:
          finish(record, "unfaithful", "重建不忠實: the unchanged conversation recurred the violation "
                 + str(record["precheck"]["reproduced"]) + " of " + str(PRECHECK_RUNS) + " times (need "
                 + str(MIN_REPRODUCED) + "); no verdict. Try another event, or run a whole-session test (layer 3)")
          return made
      arm = next_arm(record)
      if arm is None:
        finish(record, "complete")
        return made
      if made:
        time.sleep(args.gap)
      outcome, tries, payload = attempt_once(endpoint, key, bodies[arm], args)
      made += 1
      version = ctx["versions"][arm][-1]
      entry = {"n": len(record["attempts"]) + 1, "arm": arm, "version": version,
               "trial": len(ok_attempts(record, arm)) + 1, "outcome": outcome, "tries": tries}
      if outcome == "ok":
        blocks, omitted = keep_blocks(payload["content"])
        usage = usage_of(payload)
        entry.update(content=blocks, omitted_blocks=omitted, usage=usage, progressed=judge.progressed(blocks),
                     stop_reason=payload.get("stop_reason"))
        if usage and original:
          entry["input_ratio_to_original"] = round(
            (usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]) / original, 4)
        add_actual(record, usage, args)
      record["attempts"].append(entry)
      record["updated_at"] = now()
      save_json(run_dir / "run.json", record)
      if outcome == "unavailable":
        finish(record, "model_unavailable", "the relay says the model or route is no longer available ("
               + str(tries[-1].get("error", ""))[:120] + "); layer 3 needs this model too, so go to layer 4", 4)
        return made
      if outcome == "failed":
        finish(record, "incomplete", "the relay kept failing after " + str(MAX_RETRIES) + " retries (last: HTTP "
               + str(tries[-1]["status"]) + "); resume to continue, no verdict was produced")
        return made
  except KeyboardInterrupt:
    finish(record, "incomplete", "interrupted; resume to continue, no verdict was produced")
    return made


def compose_judge_input(record, side_records):
  def runs(rec, version, prefix=""):
    return [{"id": prefix + "r" + str(a["n"]), "content": a["content"]}
            for a in rec["attempts"] if a["outcome"] == "ok" and a["version"] == version]

  document = {
    "schema_version": SCHEMA_VERSION, "change_id": record["change"]["id"], "level": 2,
    "model": record["model_config"]["model"], "route": record["model_config"]["route_label"],
    "by": record["judge"]["by"], "at": record["judge"]["at"], "evidence": record["judge"]["evidence"],
    "condition": record["judge"]["condition"],
    "runs": {"old": runs(record, "old"), "new": runs(record, "new")},
  }
  if side_records:
    document["side_effects"] = {
      side: [run for index, rec in enumerate(side_records) for run in runs(rec, side, "s" + str(index) + "-")]
      for side in ("old", "new")}
  return document


def load_record(path):
  try:
    return json.loads((Path(path) / "run.json").read_text(encoding="utf-8"))
  except (OSError, ValueError) as error:
    raise Usage("not a run directory: " + str(path)) from error


def run_command(args):
  spec = load_spec(args.spec)
  purpose = spec.get("purpose", "primary")
  if args.runs is not None and purpose != "side_effect":
    raise Usage("--runs only applies to a side-effect run; the primary run uses the frozen counts")
  endpoint = endpoint_for(spec, args.endpoint)
  judge = load_judge()
  predicate = judge_method(judge, spec, args)
  change = spec["change"]
  resume = Path(args.resume) if args.resume else None
  record = load_record(resume) if resume else None
  if record and record["status"] in FINAL_STATUSES:
    return {"ok": record["status"] == "complete", "command": "run", "status": record["status"], "run_dir": str(resume),
            "reason": record["result"]["reason"], "next_layer": record["result"]["next_layer"], "called_model": False,
            "already_final": True}
  run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + re.sub(r"[^A-Za-z0-9._-]+", "-", change["id"])
  run_dir = resume or Path(args.out_dir) / run_id
  if not resume and run_dir.exists():
    raise Usage("run directory already exists: " + str(run_dir) + "; use --resume or another --run-id")
  try:
    ctx = prepare(spec, args)
  except CannotReplay as error:
    record = {"schema_version": SCHEMA_VERSION, "run_id": run_dir.name, "purpose": purpose, "created_at": now(),
              "source": {"session_file": spec["event"]["session_file"], "response_line": spec["event"]["response_line"]},
              "change": {"id": change["id"], "kind": change["kind"], "rule_path": change["rule_path"],
                         "effective_time": change["effective_time"]},
              "attempts": []}
    finish(record, "cannot_replay", str(error), 3)
    save_json(run_dir / "run.json", record)
    return {"ok": False, "command": "run", "status": "cannot_replay", "run_dir": str(run_dir), "reason": str(error),
            "next_layer": 3, "called_model": False}
  ctx["method"] = "predicate" if predicate else "model"
  size = (args.runs or SIDE_EFFECT_RUNS) if purpose == "side_effect" else ARM_RUNS
  if record is None:
    record = {
      "schema_version": SCHEMA_VERSION, "run_id": run_dir.name, "purpose": purpose, "status": "running",
      "created_at": now(), "updated_at": now(),
      "source": {"session": ctx["session"].stem, "session_file": str(ctx["session"]),
                 "response_line": spec["event"]["response_line"],
                 "source_line": ctx["evidence"]["source_line"], "event_time": ctx["evidence"]["event_time"],
                 "event_key": ctx["evidence"]["event_key"]},
      "change": {"id": change["id"], "kind": change["kind"], "rule_path": change["rule_path"],
                 "effective_time": change["effective_time"]},
      "rule_versions": {"recorded": ctx["recorded"], "basis": ctx["evidence"]["basis"],
                        "load_line": ctx["evidence"]["load_line"], "load_time": ctx["evidence"]["load_time"],
                        "old_text": change["old_text"], "new_text": change["new_text"],
                        "arms": {"original": ctx["recorded"], "swapped": ctx["versions"]["swapped"][-1]},
                        "text_outside_instructions": ctx["report"]["outside"]},
      "replacements": ctx["sites"],
      "model_config": {
        "model": ctx["facts"]["model"], "session_effort": ctx["facts"]["effort"],
        "thinking_depth": {"source": "model_suffix", "value": re.search(r":([A-Za-z0-9_-]+)$", ctx["facts"]["model"]).group(1)}
        if re.search(r":([A-Za-z0-9_-]+)$", ctx["facts"]["model"]) else {"source": "none_recorded"},
        "relay": spec["route"]["relay"], "route_label": spec["route"]["label"], "endpoint": endpoint,
        "key_env": spec["route"]["key_env"], "max_tokens": args.max_tokens, "stream": False, "beta_headers": False},
      "judge_rule": {"method": ctx["method"], "name": predicate[0] if predicate else None,
                     "version": predicate[1] if predicate else None},
      "judge": {"by": spec["judge"]["by"], "at": spec["judge"].get("at") or now(),
                "evidence": spec["judge"]["evidence"], "condition": spec["condition"]},
      "plan": {"precheck_runs": PRECHECK_RUNS if purpose == "primary" else 0, "arm_runs": size,
               "min_reproduced": MIN_REPRODUCED, "max_retries": MAX_RETRIES},
      "differences": describe_differences(ctx["report"], ctx["facts"]),
      "attempts": [],
      "actual": {"billed_usd": "unknown", "usage_complete": True, "usage_est_usd_list": 0.0, "usage_est_usd_cap": 0.0,
                 "usage": {"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
                           "output_tokens": 0}},
    }
  shown = estimate(ctx, change, args, remaining_calls(record))
  if not args.approve:
    print("預估 " + str(shown["calls_remaining"]["total"]) + " 次呼叫、每次約 " + str(shown["input_tokens_per_call"])
          + " 輸入 token，牌價約 US$" + str(shown["usd_list"]["total"]) + "（上限價 US$" + str(shown["usd_cap"]["total"])
          + "）。未核准，不呼叫模型；確認後加 --approve 重跑。", file=sys.stderr)
    return {"ok": True, "command": "run", "status": "awaiting_approval", "called_model": False, "estimate": shown,
            "run_dir": None, "reason": None, "next_layer": None, "differences": describe_differences(ctx["report"], ctx["facts"])}
  if spec["route"]["key_env"] not in os.environ:
    raise Usage("the relay key variable " + spec["route"]["key_env"] + " is not set")
  record["status"] = "running"
  record.pop("result", None)
  record["estimate"] = shown
  record.setdefault("estimate_history", []).append({"at": now(), "calls_remaining": shown["calls_remaining"],
                                                    "usd_list": shown["usd_list"], "usd_cap": shown["usd_cap"]})
  save_json(run_dir / "run.json", record)
  made = execute(record, run_dir, ctx, spec, args, endpoint, judge, predicate)
  document_path = None
  if record["status"] == "complete" and purpose == "primary":
    document_path = run_dir / "judge-input.json"
    save_json(document_path, compose_judge_input(record, []))
  save_json(run_dir / "run.json", record)
  return {"ok": record["status"] == "complete", "command": "run", "status": record["status"], "run_dir": str(run_dir),
          "reason": record["result"]["reason"], "next_layer": record["result"]["next_layer"], "called_model": made > 0,
          "calls_made": made, "estimate": shown, "precheck": record.get("precheck"),
          "judge_input": str(document_path) if document_path else None, "actual": record["actual"]}


def judge_input_command(args):
  record = load_record(args.run)
  if record["purpose"] != "primary" or record["status"] != "complete":
    raise Usage("the primary run is not complete (status " + str(record["status"]) + "), so there is nothing to judge")
  sides = []
  for path in args.side_effects:
    side = load_record(path)
    if side["purpose"] != "side_effect" or side["status"] != "complete" or side["change"]["id"] != record["change"]["id"]:
      raise Usage("side-effect run " + str(path) + " is not a complete side-effect run of the same change")
    sides.append(side)
  target = Path(args.out) if args.out else Path(args.run) / "judge-input.json"
  save_json(target, compose_judge_input(record, sides))
  return {"ok": True, "command": "judge-input", "path": str(target), "side_effect_runs": len(sides)}


# ---- CLI --------------------------------------------------------------------------------


def build_parser():
  parser = argparse.ArgumentParser(prog="retro-replay", description=__doc__)
  commands = parser.add_subparsers(dest="command", required=True)
  run = commands.add_parser("run")
  run.add_argument("--spec", required=True)
  run.add_argument("--out-dir", default=str(REPO / "reports/local-analysis/retro-replay/runs"))
  run.add_argument("--run-id")
  run.add_argument("--resume")
  run.add_argument("--endpoint")
  run.add_argument("--approve", action="store_true")
  run.add_argument("--runs", type=int)
  run.add_argument("--gap", type=float, default=20.0)
  run.add_argument("--retry-wait", type=float, default=30.0)
  run.add_argument("--timeout", type=float, default=900.0)
  run.add_argument("--max-tokens", type=int, default=32768)
  run.add_argument("--chars-per-token", type=float, default=4.5)
  run.add_argument("--in-price", type=float, default=2.0)
  run.add_argument("--out-price", type=float, default=10.0)
  run.add_argument("--cap-threshold", type=int, default=200000)
  run.add_argument("--cap-multiplier", type=float, default=2.0)
  run.add_argument("--judge-endpoint", default=os.environ.get("RETRO_JUDGE_ENDPOINT"))
  run.add_argument("--judge-model", default=os.environ.get("RETRO_JUDGE_MODEL"))
  run.add_argument("--judge-timeout", type=float, default=120.0)
  join = commands.add_parser("judge-input")
  join.add_argument("--run", required=True)
  join.add_argument("--side-effects", action="append", default=[])
  join.add_argument("--out")
  return parser


def stop_on_term(_signal, _frame):
  raise KeyboardInterrupt()


def main(argv):
  signal.signal(signal.SIGINT, signal.default_int_handler)
  signal.signal(signal.SIGTERM, stop_on_term)
  args = build_parser().parse_args(argv)
  try:
    result = run_command(args) if args.command == "run" else judge_input_command(args)
  except Usage as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
    return 2
  emit(result)
  return EXIT_CODES.get(result.get("status"), 0)


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
