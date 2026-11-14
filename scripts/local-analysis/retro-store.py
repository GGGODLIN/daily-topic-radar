#!/usr/bin/env python3
"""Append-only accumulation store for local retrospective packets.

JSONL under the store directory is the only record of truth. Mutation commands
validate the whole batch before appending. Replay of the same id with the same
payload is a no-op; a different payload is a conflict. Content is never hashed.
"""

import argparse
import fcntl
import json
import os
import stat
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True

# 入口可能在別的 repo cwd。預設庫釘這支檔所在 repo 的物理根，不跟 cwd。
DEFAULT_STORE = str(
  Path(__file__).resolve().parents[2] / "reports/local-analysis/retro-accumulation"
)
RECORDS = "records.jsonl"
VERIFICATIONS = ("pending", "confirmed", "refuted", "uncertain")
ACTIONS = ("approve", "hold", "decline")
PROGRESS = ("planned", "in_progress", "blocked", "implemented")
OUTCOMES = ("opportunity_met", "recurrence", "not_applicable", "unreviewed")
# ingest 才寫得出的結果。observe 的人工補單仍只收 OUTCOMES。
RESULTS = ("invalid", "not_loaded", "unchecked")
LOAD_STATES = ("loaded", "not_loaded", "unchecked")
# 這兩種依據下，最後一次載入若早於生效時間，是 session 開場時改動還不存在，不是改動放錯位置。
STALE_LOAD_BASES = ("last_load_is_old_version", "load_time_outside_effective_window")
DESTINATIONS = ("kept", "merged", "reclassified", "rejected")
COVERAGE = ("yes", "no", "unknown")
# action 產生事件；context 是佐證，掛在同一觀察某個事件底下。沒有 role 的證據是舊格式。
EVIDENCE_ROLES = ("action", "context")
# 判定值對照規格：有效、無效、幫倒忙、證據不足。
VERDICTS = ("effective", "ineffective", "harmful", "insufficient")
# 證據不足與沒判定的改動都是空值：未知不寫成 false。
EFFECTIVE_OF = {"effective": True, "ineffective": False, "harmful": False, "insufficient": None}
LEVELS = (1, 2, 3, 4)
SIDE_EFFECTS = ("checked", "unchecked")
CHANGE_KINDS = ("always_on_rule", "skill", "command", "hook", "script")
PROPOSAL_KINDS = ("make_hook", "retract", "modify")
PROPOSAL_DECISIONS = ("accepted", "rejected")
# 這兩種提議的對象一定是改動；拒絕或接受其中之一就處理掉待決定清單裡的那一項。
CHANGE_PROPOSALS = ("retract", "modify")
# 使用者定的值：同一條規則在改動生效後於 3 個以上不同 session 被違反，才提出「要不要做成 hook」。
MAKE_HOOK_THRESHOLD = 3
# 最多附幾場的原文指針；門檻是 3，所以永遠夠用。
MAKE_HOOK_EVIDENCE = 10
AUDIT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "session-audit.py")
NOTICE = ("<task-notification>", "<system-reminder>", "<cross-session-message", "[main]")


class Invalid(ValueError):
  pass


def require(condition, message):
  if not condition:
    raise Invalid(message)


def die(message):
  print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
  raise SystemExit(2)


def moment(value, label="timestamp"):
  require(isinstance(value, str) and value.strip(), label + " must be an ISO string")
  try:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError as error:
    raise Invalid(label + " is not ISO: " + value) from error
  require(parsed.tzinfo is not None, label + " requires timezone")
  return parsed


def ident(value, label):
  require(
    isinstance(value, str) and value.strip() and "\n" not in value,
    label + " must be a non-empty id",
  )
  return value


def text(value, label):
  require(isinstance(value, str), label + " must be a string")
  return value


def stable(value):
  return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def same(left, right):
  return stable(left) == stable(right)


def anchor(value, label):
  require(isinstance(value, dict), label + " must be an object")
  keys = {"path", "line", "quote"}
  require(set(value) == keys, label + " fields must be path, line, quote")
  require(isinstance(value["path"], str) and value["path"], label + " path is required")
  require(
    isinstance(value["line"], int) and not isinstance(value["line"], bool) and value["line"] >= 1,
    label + " line must be a positive integer",
  )
  require(isinstance(value["quote"], str) and value["quote"], label + " quote is required")
  return {"path": value["path"], "line": value["line"], "quote": value["quote"]}


def anchors(value, label, packet=None):
  require(isinstance(value, list), label + " must be a list")
  kept = []
  for index, item in enumerate(value):
    point = anchor(item, label + "[" + str(index) + "]")
    if packet is not None:
      accept_source(packet, item, point)
    kept.append(point)
  return kept


def accept_source(_packet, _raw, point):
  # 只認 caller 給的 path:line 真的包含 quote。wrapper 命中不能代替 quote。
  require(file_contains(point), "quote is not on the source line")
  return point


def line_text(path, line):
  require(
    os.path.isfile(path) and not os.path.islink(path), "source is not a regular file: " + path
  )
  path = os.path.realpath(path)
  try:
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  except OSError as error:
    raise Invalid("cannot read source: " + path) from error
  try:
    with os.fdopen(handle, "r", encoding="utf-8", newline="") as stream:
      rows = split_lines(stream.read())
  except UnicodeError as error:
    raise Invalid("source is not utf-8: " + path) from error
  require(1 <= line <= len(rows), "source line is out of range: " + path + ":" + str(line))
  return rows[line - 1]


def file_contains(item):
  return item["quote"] in line_text(item["path"], item["line"])


def jsonl_records(path):
  require(
    os.path.isfile(path) and not os.path.islink(path), "decision source is not a regular file"
  )
  path = os.path.realpath(path)
  try:
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  except OSError as error:
    raise Invalid("cannot read decision source") from error
  try:
    with os.fdopen(handle, "r", encoding="utf-8") as stream:
      raw = split_lines(stream.read())
  except UnicodeError as error:
    raise Invalid("decision source is not utf-8") from error
  rows = []
  for number, line in enumerate(raw, 1):
    if not line.strip():
      continue
    try:
      row = json.loads(line)
    except json.JSONDecodeError as error:
      raise Invalid("decision source line " + str(number) + " is not JSON") from error
    require(isinstance(row, dict), "decision source line " + str(number) + " is not an object")
    rows.append((number, row))
  return rows


def user_texts(message):
  content = message.get("content")
  if isinstance(content, str):
    return [content]
  if not isinstance(content, list):
    return []
  texts = []
  for block in content:
    if isinstance(block, str):
      texts.append(block)
    elif (
      isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ):
      texts.append(block["text"])
    elif isinstance(block, dict) and block.get("type") == "tool_result":
      raise Invalid("decision source is a tool_result")
  return texts


def human_row(path, line, quote):
  require(os.path.splitext(path)[1].lower() == ".jsonl", "decision source must be a jsonl file")
  matched = None
  for number, row in jsonl_records(path):
    if number != line:
      continue
    require(row.get("type") == "user", "decision source type must be user")
    message = row.get("message")
    require(
      isinstance(message, dict) and message.get("role") == "user",
      "decision source role must be user",
    )
    require(
      isinstance(row.get("timestamp"), str) and row.get("timestamp"),
      "decision source needs a timestamp",
    )
    require(isinstance(row.get("uuid"), str) and row.get("uuid"), "decision source needs a uuid")
    texts = user_texts(message)
    require(len(texts) == 1, "decision source must have one user text")
    body = texts[0]
    stripped = body.lstrip()
    require(not stripped.startswith(NOTICE), "decision source is a wrapped notification")
    require(quote in body, "decision quote is not in the user text")
    matched = row
  require(matched is not None, "decision source line is out of range")
  return matched


def human_quote(path, line, quote):
  return human_row(path, line, quote)["uuid"]


def verify_anchor(item, *, human=False):
  if human:
    return human_quote(item["path"], item["line"], item["quote"])
  require(file_contains(item), "quote is not on the source line")
  return None


def verify_all(items, *, human=False):
  seen = None
  for item in items:
    found = verify_anchor(item, human=human)
    if human:
      seen = found
  return seen


def split_lines(raw):
  # splitlines 會把 U+2028 當換行，行號會對不到原始檔。
  return raw.split("\n")


def entry(kind, entry_id, body):
  return {"id": entry_id, "type": kind, **body}


def load_entries(handle):
  handle.seek(0)
  raw = handle.read()
  if isinstance(raw, bytes):
    try:
      raw = raw.decode("utf-8")
    except UnicodeError as error:
      raise Invalid("store contains non-utf8 bytes") from error
  rows = []
  for number, line in enumerate(split_lines(raw), 1):
    if not line.strip():
      continue
    try:
      row = json.loads(line)
    except json.JSONDecodeError as error:
      raise Invalid("damaged store line " + str(number)) from error
    require(
      isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("type"), str),
      "damaged store line " + str(number),
    )
    rows.append(row)
  return rows


def by_id(rows):
  found = {}
  for row in rows:
    previous = found.get(row["id"])
    require(previous is None or same(previous, row), "conflicting entry id: " + row["id"])
    found[row["id"]] = row
  return found


def append_rows(handle, rows):
  handle.seek(0, os.SEEK_END)
  if handle.tell() and not raw_endswith_newline(handle):
    handle.write("\n")
  payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
  handle.write(payload)
  handle.flush()
  os.fsync(handle.fileno())


def raw_endswith_newline(handle):
  position = handle.tell()
  if position == 0:
    return True
  handle.seek(position - 1)
  last = handle.read(1)
  handle.seek(position)
  return last == "\n"


def plan_append(existing, proposed):
  index = by_id(existing)
  fresh = []
  skipped = []
  for row in proposed:
    current = index.get(row["id"])
    if current is None:
      fresh.append(row)
      index[row["id"]] = row
      continue
    if row["type"] == "event" and current["type"] == "event" and not same(current, row):
      fresh.extend(event_supplements(current, row, index))
      skipped.append(row["id"])
      continue
    require(same(current, row), "conflicting entry id: " + row["id"])
    skipped.append(row["id"])
  return fresh, skipped


def point_id(event_id, kind, point):
  # 一點一筆。鍵只有 event、來源種類與 path:line，不帶整包、不 hash。
  return "event-evidence:" + event_id + ":" + kind + ":" + point["path"] + ":" + str(point["line"])


def link_id(event_id, issue):
  return "event-link:" + event_id + ":" + issue


def add_once(index, row, added):
  previous = index.get(row["id"])
  if previous is None:
    added.append(row)
    index[row["id"]] = row
    return
  require(same(previous, row), "conflicting entry id: " + row["id"])


def event_supplements(current, row, index):
  require(current["session"] == row["session"], "event session cannot change: " + current["id"])
  require(current["event_at"] == row["event_at"], "event time cannot change: " + current["id"])
  added = []
  groups = (
    ("anchor", row["anchors"], current["anchors"]),
    ("review", row["review_sources"], current["review_sources"]),
  )
  for kind, points, owned in groups:
    for point in points:
      if any(same(point, old) for old in owned):
        continue
      entry_id = point_id(current["id"], kind, point)
      supplement = entry(
        "event_evidence", entry_id, {"event_id": current["id"], "kind": kind, "source": point}
      )
      previous = index.get(entry_id)
      if previous is not None:
        require(
          previous["source"]["quote"] == point["quote"], "conflicting source quote: " + entry_id
        )
        continue
      add_once(index, supplement, added)
  for issue in row["issue_ids"]:
    if issue in current["issue_ids"]:
      continue
    add_once(
      index,
      entry(
        "event_link", link_id(current["id"], issue), {"event_id": current["id"], "issue_id": issue}
      ),
      added,
    )
  return added


def issue_ids(rows):
  return {row["issue_id"] for row in rows if row["type"] == "issue"}


def approved(rows, issue):
  decision = latest(rows, "decision", issue)
  return decision is not None and decision["action"] == "approve"


def latest(rows, kind, issue):
  found = [row for row in rows if row["type"] == kind and row.get("issue_id") == issue]
  return found[-1] if found else None


def open_store(path, create):
  path = os.path.abspath(os.path.expanduser(path))
  if os.path.lexists(path):
    require(
      not os.path.islink(path) and stat.S_ISDIR(os.lstat(path).st_mode),
      "store must be a real directory",
    )
  elif create:
    os.makedirs(path, mode=0o700)
  else:
    raise Invalid("store does not exist: " + path)
  if os.path.isdir(path) and not os.path.islink(path):
    os.chmod(path, 0o700)
  target = os.path.join(path, RECORDS)
  if os.path.lexists(target):
    info = os.lstat(target)
    require(
      not stat.S_ISLNK(info.st_mode) and stat.S_ISREG(info.st_mode),
      "store file must be a regular file",
    )
    flags = os.O_RDWR | os.O_NOFOLLOW
  else:
    require(create, "store file does not exist")
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
  handle = os.open(target, flags, 0o600)
  os.chmod(target, 0o600)
  return path, handle


@contextmanager
def locked(path, create=False):
  folder, handle = open_store(path, create)
  stream = os.fdopen(handle, "r+", encoding="utf-8")
  try:
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
    yield folder, stream
    stream.flush()
  finally:
    try:
      fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
      stream.close()


def packet_entries(packet):
  require(isinstance(packet, dict), "packet must be an object")
  require(packet.get("schema_version") == 1, "unsupported packet schema")
  run = packet.get("run")
  require(isinstance(run, dict), "run is required")
  run_id = ident(run.get("id"), "run.id")
  require(run.get("status") in ("complete", "partial"), "run.status must be complete or partial")
  moment(run.get("cutoff"), "run.cutoff")
  require(isinstance(run.get("source"), str) and run["source"], "run.source is required")
  issues = packet.get("issues", [])
  discoveries = packet.get("discoveries", [])
  events = packet.get("events", [])
  require(
    isinstance(issues, list) and isinstance(discoveries, list) and isinstance(events, list),
    "issues, discoveries, and events must be lists",
  )
  known = set()
  rows = []
  for item in issues:
    require(isinstance(item, dict), "issue must be an object")
    issue = ident(item.get("id"), "issue.id")
    require(issue not in known, "duplicate issue id in packet: " + issue)
    known.add(issue)
    body = {
      "issue_id": issue,
      "title": text(item.get("title", ""), "issue.title"),
      "related_to": [ident(x, "related_to") for x in item.get("related_to", [])],
    }
    require(isinstance(item.get("related_to", []), list), "related_to must be a list")
    require("title" in item, "issue.title is required")
    rows.append(entry("issue", "issue:" + issue, body))
  for item in discoveries:
    require(isinstance(item, dict), "discovery must be an object")
    discovery = ident(item.get("id"), "discovery.id")
    issue = ident(item.get("issue_id"), "discovery.issue_id")
    require(issue in known, "unknown issue reference: " + issue)
    verification = item.get("verification")
    require(verification in VERIFICATIONS, "discovery.verification is invalid")
    source = anchor(item.get("source"), "discovery.source")
    accept_source(packet, item.get("source"), source)
    reviews = anchors(item.get("review_sources"), "discovery.review_sources", packet)
    if verification == "confirmed":
      require(reviews, "confirmed discovery needs review_sources")
    require(isinstance(item.get("scope"), str), "discovery.scope is required")
    body = {
      "issue_id": issue,
      "source": source,
      "verification": verification,
      "review_sources": reviews,
      "scope": item["scope"],
    }
    for key in ("note", "candidate", "counterevidence", "raw", "raw_reviews"):
      if key in item:
        body[key] = item[key]
    rows.append(entry("discovery", "discovery:" + discovery, body))
  for item in events:
    require(isinstance(item, dict), "event must be an object")
    event = ident(item.get("id"), "event.id")
    require(isinstance(item.get("session"), str) and item["session"], "event.session is required")
    moment(item.get("event_at"), "event.event_at")
    verification = item.get("verification")
    require(verification in ("confirmed", "uncertain", "refuted"), "event.verification is invalid")
    points = anchors(item.get("anchors"), "event.anchors", packet)
    reviews = anchors(item.get("review_sources"), "event.review_sources", packet)
    if verification == "confirmed":
      require(points, "confirmed event needs anchors")
      require(reviews, "confirmed event needs review_sources")
    require(isinstance(item.get("scope"), str), "event.scope is required")
    links = [ident(x, "event.issue_ids") for x in item.get("issue_ids", [])]
    require(isinstance(item.get("issue_ids"), list) and links, "event.issue_ids is required")
    unknown = [x for x in links if x not in known]
    require(not unknown, "unknown issue reference: " + ",".join(unknown))
    body = {
      "session": item["session"],
      "event_at": item["event_at"],
      "anchors": points,
      "verification": verification,
      "review_sources": reviews,
      "scope": item["scope"],
      "issue_ids": links,
    }
    rows.append(entry("event", "event:" + event, body))
  run_body = {
    "run_id": run_id,
    "source": run["source"],
    "status": run["status"],
    "cutoff": run["cutoff"],
  }
  if "run_metadata" in packet:
    run_body["run_metadata"] = packet.get("run_metadata")
  rows.append(entry("run", "run:" + run_id, run_body))
  return rows


def packet_destinations(value):
  require(isinstance(value, list), "tracking_dispositions must be a list")
  found = {}
  for index, item in enumerate(value):
    label = "tracking_dispositions[" + str(index) + "]"
    require(isinstance(item, dict), label + " must be an object")
    key = ident(item.get("observation_key"), label + ".observation_key")
    require(key not in found, label + " repeats the destination of " + key)
    decision = item.get("disposition")
    require(decision in DESTINATIONS, label + ".disposition is invalid")
    if decision in ("reclassified", "rejected"):
      require(
        isinstance(item.get("reason"), str) and item["reason"].strip(), label + " needs a reason"
      )
    if decision == "reclassified":
      require(item.get("to_outcome") in OUTCOMES, label + ".to_outcome is invalid")
    if decision == "merged":
      require(isinstance(item.get("merged_from"), list), label + ".merged_from must be a list")
    found[key] = item
  return found


def optional_coverage(value, label):
  require(value is None or value in COVERAGE, label + " must be yes, no or unknown")
  return value


def unkeyed_reason(item):
  """Why an unreviewed observation with no evidence cannot become an event observation, else None.

  Evidence is what gives an observation its event key. Only an unreviewed one may lack it; every other
  gap or wrong type is a malformed packet and still rejects the batch.
  """
  if not isinstance(item, dict) or item.get("outcome") != "unreviewed":
    return None
  if item.get("evidence") not in (None, []) or item.get("events") not in (None, []):
    return None
  issue = item.get("issue_id")
  change_id = item.get("change_id")
  if not (isinstance(issue, str) and issue.strip()):
    return None
  if change_id is not None and not (isinstance(change_id, str) and change_id.strip()):
    return None
  return "unreviewed and no evidence, so there is no event to key" + (
    "" if change_id is not None else "; change_id is also missing"
  )


def unkeyed_observations(packet):
  found = []
  for item in packet.get("observation_proposals", []):
    reason = unkeyed_reason(item)
    if reason is not None:
      found.append({"issue_id": item["issue_id"], "change_id": item.get("change_id"), "reason": reason})
  return found


def proposal_events(item, label, destinations):
  require(isinstance(item, dict), label + " must be an object")
  if unkeyed_reason(item) is not None:
    return []
  issue = ident(item.get("issue_id"), label + ".issue_id")
  change_id = ident(item.get("change_id"), label + ".change_id")
  outcome = item.get("outcome")
  require(outcome in OUTCOMES, label + ".outcome is invalid")
  covered = optional_coverage(item.get("covered"), label + ".covered")
  evidence = item.get("evidence")
  events = item.get("events")
  require(isinstance(evidence, list) and evidence, label + ".evidence must be a non-empty list")
  roles = [point.get("role") if isinstance(point, dict) else None for point in evidence]
  if all(role is None for role in roles):
    return legacy_events(item, label, issue, change_id, outcome)
  for position, role in enumerate(roles):
    require(role in EVIDENCE_ROLES, label + ".evidence[" + str(position) + "].role must be action or context")
  actions = [point for point, role in zip(evidence, roles) if role == "action"]
  require(actions, label + ".evidence needs at least one action")
  # events[] 由驗收器逐筆從 action 證據產生；缺少或對不上，代表它沒走過驗收，不當舊格式收。
  require(
    isinstance(events, list) and len(events) == len(actions),
    label + ".events must match the action evidence one to one",
  )
  found = []
  for position, (event, point) in enumerate(zip(events, actions)):
    where = label + ".events[" + str(position) + "]"
    require(isinstance(event, dict), where + " must be an object")
    key = ident(event.get("observation_key"), where + ".observation_key")
    event_key = ident(event.get("event_key"), where + ".event_key")
    require(
      event.get("change_id") == change_id and key == event_key + "|" + change_id,
      where + " does not match its event key and change",
    )
    loaded = event.get("loaded")
    require(loaded in LOAD_STATES, where + ".loaded is invalid")
    require(isinstance(event.get("basis"), str) and event["basis"], where + ".basis is required")
    reason = event.get("reason")
    require(reason is None or isinstance(reason, str), where + ".reason must be a string")
    if loaded != "loaded":
      require(reason is not None and reason.strip(), where + " needs a reason unless loaded")
    at = event.get("event_time")
    if at is not None:
      moment(at, where + ".event_time")
    if event.get("load_time") is not None:
      moment(event["load_time"], where + ".load_time")
    require(at is not None or loaded != "loaded", where + " is loaded but has no event_time")
    session = point.get("session")
    line = point.get("source_line")
    quote = point.get("quote")
    require(isinstance(session, str) and session.strip(), where + " evidence needs a session")
    require(
      isinstance(line, int) and not isinstance(line, bool) and line >= 1,
      where + " evidence source_line must be a positive integer",
    )
    require(isinstance(quote, str) and quote.strip(), where + " evidence needs a quote")
    coverage = optional_coverage(event.get("covered", covered), where + ".covered")
    destination = destinations.get(key)
    if destination is not None:
      require(
        destination["disposition"] != "rejected",
        where + " is rejected in tracking_dispositions but still cited: " + key,
      )
      if destination["disposition"] == "reclassified":
        require(destination["to_outcome"] == outcome, where + " outcome is not the to_outcome")
    found.append(
      {
        "kind": "action",
        "key": key,
        "label": where,
        "issue_id": issue,
        "change_id": change_id,
        "outcome": outcome,
        "event": event,
        "point": {"session": session, "source_line": line, "quote": quote},
        "covered": coverage if coverage is not None else "unknown",
        "destination": destination,
      }
    )
  by_event = {entry["event"]["event_key"]: entry for entry in found}
  for position, (point, role) in enumerate(zip(evidence, roles)):
    if role != "context":
      continue
    where = label + ".evidence[" + str(position) + "]"
    home = by_event.get(point.get("event_key"))
    require(home is not None, where + " is a context and needs the event_key of one of this observation's events")
    session, line, quote = point.get("session"), point.get("source_line"), point.get("quote")
    require(isinstance(session, str) and session.strip(), where + " needs a session")
    require(isinstance(line, int) and not isinstance(line, bool) and line >= 1, where + " source_line must be a positive integer")
    require(isinstance(quote, str) and quote.strip(), where + " needs a quote")
    found.append(
      {
        "kind": "context",
        "key": home["key"],
        "label": where,
        "issue_id": issue,
        "change_id": change_id,
        "point": {"session": session, "source_line": line, "quote": quote, "role": "context"},
      }
    )
  return found


def legacy_events(item, label, issue, change_id, outcome):
  """Evidence without roles comes from a report written before roles existed.

  Which entries were the mistaken action is unknown, so no event can be keyed and no time is trusted: the
  observation is written without event_time (the store's legacy shape), listed apart and never counted.
  """
  evidence = item["evidence"]
  points = []
  for position, point in enumerate(evidence):
    where = label + ".evidence[" + str(position) + "]"
    require(isinstance(point, dict), where + " must be an object")
    session, line, quote = point.get("session"), point.get("source_line"), point.get("quote")
    require(isinstance(session, str) and session.strip(), where + " needs a session")
    require(isinstance(line, int) and not isinstance(line, bool) and line >= 1, where + " source_line must be a positive integer")
    require(isinstance(quote, str) and quote.strip(), where + " needs a quote")
    points.append({"session": session, "source_line": line, "quote": quote})
  # 舊 cross 的去向以事件鍵為單位；這些鍵在舊格式下不產生觀察，但去向不能因此被當成找不到歸宿。
  carried = [
    event["observation_key"]
    for event in item.get("events") or []
    if isinstance(event, dict) and isinstance(event.get("observation_key"), str)
  ]
  key = "legacy:" + issue + "|" + change_id + "|" + outcome + "|" + points[0]["session"] + ":" + str(points[0]["source_line"])
  return [
    {
      "kind": "legacy",
      "key": key,
      "label": label + ".evidence[" + str(position) + "]",
      "issue_id": issue,
      "change_id": change_id,
      "outcome": outcome,
      "point": point,
      "carries": carried,
    }
    for position, point in enumerate(points)
  ]


def packet_observations(packet):
  """Validate the observation sections alone; the store is not needed to say a packet is malformed."""
  proposals = packet.get("observation_proposals", [])
  require(isinstance(proposals, list), "observation_proposals must be a list")
  destinations = packet_destinations(packet.get("tracking_dispositions", []))
  found = []
  for index, item in enumerate(proposals):
    found.extend(proposal_events(item, "observation_proposals[" + str(index) + "]", destinations))
  landed = {item["key"] for item in found if item["kind"] != "legacy"}
  landed.update(key for item in found for key in item.get("carries", ()))
  for key, destination in destinations.items():
    require(
      destination["disposition"] == "rejected" or key in landed,
      "tracking_dispositions says " + destination["disposition"] + " but no observation carries " + key,
    )
  rejected = [
    {"observation_key": key, "reason": item["reason"]}
    for key, item in destinations.items()
    if item["disposition"] == "rejected"
  ]
  return found, rejected, bool(proposals) or bool(destinations)


def judge(event, outcome, effective_at):
  """What the stored change says about one event. The store's effective_at outranks the packet's claim."""
  at = event.get("event_time")
  if at is not None and moment(at, "event_time") < moment(effective_at, "effective_at"):
    return "invalid", "event at " + at + " is before the change took effect at " + effective_at
  loaded_at = event.get("load_time")
  if (
    event["loaded"] == "not_loaded"
    and event["basis"] in STALE_LOAD_BASES
    and loaded_at is not None
    and moment(loaded_at, "load_time") < moment(effective_at, "effective_at")
  ):
    return "invalid", (
      "session 載入的版本早於生效時間（最後載入 " + loaded_at + "，生效 " + effective_at + "）"
    )
  if event["basis"] == "event_before_effective":
    return "unchecked", "the packet put the event before the change took effect, but the stored effective_at does not"
  if event["loaded"] == "loaded":
    return outcome, event.get("reason")
  return event["loaded"], event["reason"]


def observation_bodies(rows, run_id, proposals):
  changes = {row["id"]: row for row in rows if row["type"] == "change"}
  built = []
  for item in proposals:
    found = changes.get("change:" + item["change_id"])
    require(
      found is not None and found["issue_id"] == item["issue_id"],
      item["label"] + ": observation change is not on this issue",
    )
    if item["kind"] == "context":
      built.append({"key": item["key"], "body": {"run_id": run_id, "evidence": item["point"]}, "kind": "context"})
      continue
    if item["kind"] == "legacy":
      body = {
        "issue_id": item["issue_id"],
        "observation_id": item["key"],
        "change_id": item["change_id"],
        "outcome": item["outcome"],
        "evidence": item["point"],
        "run_id": run_id,
      }
      built.append({"key": item["key"], "body": body, "destination": None, "kind": "legacy"})
      continue
    event = item["event"]
    result, why = judge(event, item["outcome"], found["effective_at"])
    body = {
      "issue_id": item["issue_id"],
      "observation_id": item["key"],
      "change_id": item["change_id"],
      "outcome": result,
      "reported_outcome": item["outcome"],
      "at": event.get("event_time"),
      "event_time": event.get("event_time"),
      "event_key": event["event_key"],
      "session": item["point"]["session"],
      "loaded": event["loaded"],
      "basis": event["basis"],
      "reason": why,
      "evidence": item["point"],
      "run_id": run_id,
    }
    if result == "recurrence":
      body["covered"] = item["covered"]
    destination = item["destination"]
    if destination is not None:
      body["cross_disposition"] = {
        name: value for name, value in destination.items() if name != "observation_key"
      }
    built.append({"key": item["key"], "body": body, "destination": destination, "kind": "action"})
  return built


def observation_core(body):
  # 判斷結果的欄位。載入原因的文字改寫不構成改判。
  return (
    body["issue_id"],
    body["change_id"],
    body["outcome"],
    body.get("event_time"),
    body.get("covered"),
  )


def remember(points, key, point):
  """Quotes already stored for one observation, per cited line, in the order they were written."""
  quotes = points.setdefault(key, {}).setdefault((point["session"], point["source_line"]), [])
  if point["quote"] not in quotes:
    quotes.append(point["quote"])
  return quotes


def observation_history(rows):
  """Per observation id: its base row and corrections in file order, plus every evidence point and run seen."""
  history = {}
  points = {}
  runs = {}
  for row in rows:
    kind = row["type"]
    if kind not in ("observation", "observation_correction", "observation_evidence", "observation_run"):
      continue
    key = row["observation_id"]
    if kind in ("observation", "observation_correction"):
      history.setdefault(key, []).append(row)
    if isinstance(row.get("evidence"), dict):
      remember(points, key, row["evidence"])
    if isinstance(row.get("run_id"), str):
      runs.setdefault(key, set()).add(row["run_id"])
  return history, points, runs


def current_observations(rows):
  history, _points, _runs = observation_history(rows)
  return [states[-1] for states in history.values()]


def plan_observations(rows, built):
  history, points, runs = observation_history(rows)
  fresh = []
  tally = {
    "new": 0,
    "replayed": 0,
    "supplemented": 0,
    "corrected": 0,
    "legacy": {"new": 0, "replayed": 0, "supplemented": 0},
  }

  def settle(key, body, bucket):
    # 同一事件的證據點：這句引文記過就是重播，沒記過才是補證。引文是證據、不是事件身分，
    # 同一行換引文不構成衝突。
    point = body["evidence"]
    place = (point["session"], point["source_line"])
    known = points.get(key, {}).get(place, [])
    if point["quote"] in known:
      bucket["replayed"] += 1
      return False
    # 同一行的第二句起加序號：id 不能和第一句的補證撞在一起，又要重播時算得出同一個 id。
    suffix = ":" + str(len(known)) if known else ""
    row = entry(
      "observation_evidence",
      "observation-evidence:" + key + ":" + place[0] + ":" + str(place[1]) + suffix,
      {"observation_id": key, "run_id": body["run_id"], "evidence": point},
    )
    fresh.append(row)
    remember(points, key, point)
    bucket["supplemented"] += 1
    return True

  # 佐證掛在事件的觀察上，所以排在所有觀察之後處理，不論它在證據清單的哪個位置。
  ordered = [item for item in built if item["kind"] != "context"] + [
    item for item in built if item["kind"] == "context"
  ]
  for item in ordered:
    key, body = item["key"], item["body"]
    states = history.get(key, [])
    bucket = tally["legacy"] if item["kind"] == "legacy" else tally
    if item["kind"] == "context":
      require(states, "context evidence has no observation to attach to: " + key)
      settle(key, body, tally)
      continue
    if not states:
      row = entry("observation", "observation:" + key, body)
      fresh.append(row)
      history[key] = [row]
      remember(points, key, body["evidence"])
      runs.setdefault(key, set()).add(body["run_id"])
      bucket["new"] += 1
      continue
    same_run = next((state for state in states if state.get("run_id") == body["run_id"]), None)
    if same_run is not None:
      require(
        observation_core(same_run) == observation_core(body),
        "conflicting observation in one run: " + key,
      )
      settle(key, body, bucket)
    elif body["run_id"] in runs.get(key, set()):
      # 這一輪先前只補過證，結果之後可能已被更正；重播它不能把結果改回去。
      settle(key, body, bucket)
    elif observation_core(states[-1]) == observation_core(body):
      # 結果相同又沒補到新證據的一輪不會寫任何列；沒有這筆紀錄，之後別輪更正了結果，
      # 重播這一輪就認不出它已處理過，會把較新的更正改回去。
      if not settle(key, body, bucket):
        fresh.append(
          entry(
            "observation_run",
            "observation-run:" + key + ":" + body["run_id"],
            {"observation_id": key, "run_id": body["run_id"]},
          )
        )
      runs.setdefault(key, set()).add(body["run_id"])
    else:
      destination = item["destination"] or {}
      reason = destination.get("reason") if destination.get("disposition") == "reclassified" else None
      require(reason, "a changed observation needs a reclassification reason: " + key)
      number = sum(1 for state in states if state["type"] == "observation_correction") + 1
      row = entry(
        "observation_correction",
        "observation-correction:" + key + ":" + str(number),
        {**body, "supersedes": states[-1]["id"], "correction_reason": reason},
      )
      fresh.append(row)
      states.append(row)
      remember(points, key, body["evidence"])
      runs.setdefault(key, set()).add(body["run_id"])
      tally["corrected"] += 1
  return fresh, tally


def ingest(store, packet_path, apply):
  packet = json.loads(read_regular(packet_path))
  proposed = packet_entries(packet)
  proposals, rejected, tracked = packet_observations(packet)
  unkeyed = unkeyed_observations(packet)
  run_id = packet["run"]["id"]

  def summary(fresh, noop, tally):
    out = {
      "ok": True,
      "command": "ingest",
      "apply": apply,
      "appended": len(fresh),
      "noop": noop,
      "ids": [row["id"] for row in fresh],
    }
    if tracked:
      out["observations"] = {**tally, "rejected": rejected, "unkeyed": unkeyed}
    return out

  # 驗證先於開檔。全新 dry-run 不建立空 records。
  if not apply and not os.path.lexists(
    os.path.join(os.path.abspath(os.path.expanduser(store)), RECORDS)
  ):
    extra, tally = plan_observations([], observation_bodies([], run_id, proposals))
    return summary(proposed + extra, 0, tally)
  with locked(store, create=apply) as (_folder, handle):
    existing = load_entries(handle)
    fresh, skipped = plan_append(existing, proposed)
    extra, tally = plan_observations(existing, observation_bodies(existing, run_id, proposals))
    fresh = fresh + extra
    if apply and fresh:
      append_rows(handle, fresh)
  return summary(fresh, len(skipped) + tally["replayed"] + tally["legacy"]["replayed"], tally)


def read_regular(path):
  path = os.path.realpath(path)
  require(os.path.isfile(path) and not os.path.islink(path), "packet is not a regular file")
  try:
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  except OSError as error:
    raise Invalid("cannot read packet") from error
  with os.fdopen(handle, "r", encoding="utf-8") as stream:
    return stream.read()


def decision_entry(issue, action, source, note, *, uuid=None):
  if uuid is None:
    uuid = verify_anchor(source, human=True)
  entry_id = uuid + ":" + issue + ":" + action
  body = {"issue_id": issue, "action": action, "source": source, "event_uuid": uuid}
  if note is not None:
    body["note"] = note
  return entry("decision", entry_id, body)


def is_human_turn(row):
  if row.get("isSidechain") or row.get("isMeta") or row.get("type") != "user":
    return False
  message = row.get("message")
  if not isinstance(message, dict) or message.get("role") != "user":
    return False
  content = message.get("content")
  if isinstance(content, str):
    parts = [content]
  elif isinstance(content, list):
    parts = []
    for block in content:
      if isinstance(block, dict) and block.get("type") == "tool_result":
        return False
      if isinstance(block, str):
        parts.append(block)
      elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
        parts.append(block["text"])
  else:
    return False
  joined = "\n".join(parts).lstrip()
  return bool(joined) and not joined.startswith((*NOTICE, "<local-command-stdout>", "<command-name>"))


def assistant_visible_text(row):
  message = row.get("message") if isinstance(row.get("message"), dict) else None
  if row.get("type") != "assistant" and (message is None or message.get("role") != "assistant"):
    return ""
  content = message.get("content") if message is not None else row.get("content")
  if isinstance(content, str):
    return content
  if not isinstance(content, list):
    return ""
  return "\n".join(
    block if isinstance(block, str) else block["text"]
    for block in content
    if isinstance(block, str)
    or (isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str))
  )


def issue_markers(existing):
  issues = {row["issue_id"]: row for row in existing if row["type"] == "issue"}
  derived = {}
  owners = {}
  for iid, irow in issues.items():
    tokens = set()
    for seg in iid.split(":"):
      if len(seg) >= 6 and seg != "imported":
        tokens.add(seg)
    title = (irow.get("title") or "").strip()
    if len(title) >= 4:
      tokens.add(title)
    core = title.split("（", 1)[0].split("(", 1)[0].strip()
    if len(core) >= 6:
      tokens.add(core)
    for row in existing:
      if row["type"] == "discovery" and row.get("issue_id") == iid:
        cand = (row.get("candidate") or "").strip()
        if len(cand) >= 8:
          tokens.add(cand)
    derived[iid] = tokens
    for tok in tokens:
      owners[tok] = owners.get(tok, 0) + 1
  return {
    iid: {iid, *(tok for tok in derived[iid] if owners[tok] == 1)}
    for iid in issues
  }


def presented_issues_in_text(body, markers):
  if not body:
    return set()
  return {iid for iid, toks in markers.items() if any(tok and tok in body for tok in toks)}


def verify_presented_before_decision(source, targets, existing):
  # 短答（如「1, 2 都做」）不含完整 issue_id 時，核對該句之前的 assistant 候選呈現回合確實列過每一個目標問題，
  # 防止把同一句短答順手綁到從未在場呈現的其他問題。
  need = [iid for iid in targets if iid not in source["quote"]]
  if not need:
    return
  markers = issue_markers(existing)
  groups = [[]]
  for number, row in jsonl_records(source["path"]):
    if number >= source["line"]:
      break
    if is_human_turn(row):
      groups.append([])
      continue
    shown = assistant_visible_text(row)
    if shown:
      groups[-1].append(shown)
  recent_text = "\n".join(groups[-1])
  recent_hits = presented_issues_in_text(recent_text, markers)
  if all(iid in recent_hits for iid in need):
    return
  latest_table_text = ""
  for earlier in reversed(groups[:-1]):
    chunk = "\n".join(earlier)
    if len(presented_issues_in_text(chunk, markers)) >= 2:
      latest_table_text = chunk
      break
  combined_hits = presented_issues_in_text((latest_table_text + "\n" + recent_text).strip(), markers)
  for iid in need:
    require(
      iid in combined_hits,
      f"issue {iid} was not presented in assistant messages before line {source['line']}",
    )


def parse_decide_issues(args):
  raw_items = []
  if getattr(args, "issue", None) is not None:
    raw_items.append(ident(args.issue, "issue"))
  for group in getattr(args, "issues", None) or []:
    require(isinstance(group, str) and group.strip(), "issues must be a non-empty id list")
    parts = [part.strip() for part in group.split(",")]
    require(all(parts), "issues contains an empty id")
    for part in parts:
      raw_items.append(ident(part, "issue"))
  require(bool(raw_items), "either --issue or --issues is required")
  seen = set()
  ordered = []
  for item in raw_items:
    require(item not in seen, "duplicate issue in decide: " + item)
    seen.add(item)
    ordered.append(item)
  return ordered


def decide(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  targets = parse_decide_issues(args)
  uuid = verify_anchor(source, human=True)
  rows = [decision_entry(issue, args.action, source, args.note, uuid=uuid) for issue in targets]
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    known = {item["issue_id"] for item in existing if item["type"] == "issue"}
    for issue in targets:
      require(issue in known, "unknown issue reference: " + issue)
    if len(targets) > 1:
      verify_presented_before_decision(source, targets, existing)
    fresh, skipped = plan_append(existing, rows)
    for row in fresh:
      issue = row["issue_id"]
      bound_other = next(
        (
          item["issue_id"]
          for item in existing
          if item["type"] == "decision"
          and item.get("event_uuid") == uuid
          and item.get("issue_id") != issue
        ),
        None,
      )
      if bound_other is not None:
        require(
          issue in source["quote"],
          f"event_uuid {uuid} already bound to {bound_other}; quote must explicitly name {issue}",
        )
    if args.apply and fresh:
      append_rows(handle, fresh)
  payload = {
    "ok": True,
    "command": "decision",
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": rows[0]["id"],
  }
  if len(rows) > 1:
    payload["ids"] = [row["id"] for row in rows]
  return payload


def require_approved(rows, issue):
  require(
    any(row["type"] == "issue" and row["issue_id"] == issue for row in rows),
    "unknown issue reference: " + issue,
  )
  require(approved(rows, issue), "issue is not approved: " + issue)


def progress_id(rows, issue, body):
  # Caller 沒給 progress id。同一 issue、同一狀態與同一來源視為重播；換內容才用序號另立。
  matches = [
    row
    for row in rows
    if row["type"] == "progress"
    and row["issue_id"] == issue
    and same({key: row.get(key) for key in body}, body)
  ]
  if matches:
    return matches[-1]["id"]
  used = sum(1 for row in rows if row["type"] == "progress" and row["issue_id"] == issue)
  return "progress:" + issue + ":" + str(used + 1)


def progress(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  verify_anchor(source)
  issue = ident(args.issue, "issue")
  body = {
    "issue_id": issue,
    "state": args.state,
    "note": text(args.note, "note"),
    "source": source,
  }
  if args.next is not None:
    body["next"] = text(args.next, "next")
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    require_approved(existing, issue)
    row = entry("progress", progress_id(existing, issue, body), body)
    fresh, skipped = plan_append(existing, [row])
    if args.apply and fresh:
      append_rows(handle, fresh)
  return {
    "ok": True,
    "command": "progress",
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": row["id"],
  }


def change(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  verify_anchor(source)
  require(isinstance(args.condition, str) and args.condition.strip(), "condition is required")
  moment(args.effective_at, "effective_at")
  issue = ident(args.issue, "issue")
  change_id = ident(args.id, "change.id")
  row = entry(
    "change",
    "change:" + change_id,
    {
      "issue_id": issue,
      "change_id": change_id,
      "effective_at": args.effective_at,
      "source": source,
      "condition": args.condition,
    },
  )
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    require_approved(existing, issue)
    fresh, skipped = plan_append(existing, [row])
    if args.apply and fresh:
      append_rows(handle, fresh)
  return {
    "ok": True,
    "command": "change",
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": row["id"],
  }


def observe(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  verify_anchor(source)
  moment(args.at, "at")
  issue = ident(args.issue, "issue")
  observation = ident(args.id, "observation.id")
  change_id = ident(args.change, "change")
  row = entry(
    "observation",
    "observation:" + observation,
    {
      "issue_id": issue,
      "observation_id": observation,
      "change_id": change_id,
      "outcome": args.outcome,
      "at": args.at,
      "event_time": args.at,
      "source": source,
    },
  )
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    found = next(
      (
        item
        for item in existing
        if item["type"] == "change" and item["id"] == "change:" + change_id
      ),
      None,
    )
    require(
      found is not None and found["issue_id"] == issue,
      "observation change is not on this issue",
    )
    require(
      moment(args.at, "at") >= moment(found["effective_at"], "effective_at"),
      "recurrence before the change is rejected"
      if args.outcome == "recurrence"
      else "observation is before the change",
    )
    fresh, skipped = plan_append(existing, [row])
    if args.apply and fresh:
      append_rows(handle, fresh)
  return {
    "ok": True,
    "command": "observe",
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": row["id"],
  }


def count(value, label):
  require(
    isinstance(value, int) and not isinstance(value, bool) and value >= 0,
    label + " must be a non-negative integer",
  )
  return value


def find_change(rows, change_id):
  found = next(
    (row for row in rows if row["type"] == "change" and row["id"] == "change:" + change_id), None
  )
  require(found is not None, "unknown change reference: " + change_id)
  return found


def numbered_id(rows, kind, change_id, body):
  # 同一改動、同內容視為重播；換內容才用序號另立。判定的「最新」就是檔案裡最後一筆。
  mine = [row for row in rows if row["type"] == kind and row["change_id"] == change_id]
  for row in mine:
    if same({key: row.get(key) for key in body}, body):
      return row["id"]
  return kind + ":" + change_id + ":" + str(len(mine) + 1)


def append_one(args, build_row):
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    row = build_row(existing)
    fresh, skipped = plan_append(existing, [row])
    if args.apply and fresh:
      append_rows(handle, fresh)
  return {
    "ok": True,
    "command": args.command,
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": row["id"],
  }


def verdict(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  verify_anchor(source)
  change_id = ident(args.change, "change")
  require(args.verdict in VERDICTS, "verdict must be one of " + ", ".join(VERDICTS))
  require(args.level in LEVELS, "level must be 1, 2, 3 or 4")
  require(args.side_effects in SIDE_EFFECTS, "side_effects must be checked or unchecked")
  moment(args.at, "at")
  body = {
    "change_id": change_id,
    "verdict": args.verdict,
    "level": args.level,
    "model": ident(args.model, "model"),
    "route": ident(args.route, "route"),
    "runs": count(args.runs, "runs"),
    "old_recurrences": count(args.old_recurrences, "old_recurrences"),
    "new_recurrences": count(args.new_recurrences, "new_recurrences"),
    "source": source,
    "judged_by": ident(args.by, "judged_by"),
    "side_effects": args.side_effects,
    "at": args.at,
  }

  def build(existing):
    body["issue_id"] = find_change(existing, change_id)["issue_id"]
    return entry("verdict", numbered_id(existing, "verdict", change_id, body), body)

  return append_one(args, build)


def backfill(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  verify_anchor(source)
  change_id = ident(args.change, "change")
  require(args.kind in CHANGE_KINDS, "kind must be one of " + ", ".join(CHANGE_KINDS))
  body = {"change_id": change_id, "kind": args.kind, "source": source}

  def build(existing):
    body["issue_id"] = find_change(existing, change_id)["issue_id"]
    return entry("backfill", numbered_id(existing, "backfill", change_id, body), body)

  return append_one(args, build)


def proposal_target(args):
  rule_flags = (args.rule_path, args.rule_line, args.rule_quote)
  given = [flag is not None for flag in rule_flags]
  require(all(given) or not any(given), "rule target needs path, line and quote together")
  require(
    (args.change is not None) != all(given), "give exactly one target: a change or a rule anchor"
  )
  if args.change is not None:
    change_id = ident(args.change, "change")
    return {"change_id": change_id}, "change:" + change_id
  point = anchor({"path": args.rule_path, "line": args.rule_line, "quote": args.rule_quote}, "rule")
  # 行號會隨規則檔編輯漂移；比對對象用路徑加原文。
  return {"rule": point}, "rule:" + point["path"] + "|" + point["quote"]


def proposal(args):
  require(args.kind in PROPOSAL_KINDS, "kind must be one of " + ", ".join(PROPOSAL_KINDS))
  require(
    args.decision in PROPOSAL_DECISIONS, "decision must be one of " + ", ".join(PROPOSAL_DECISIONS)
  )
  target, key = proposal_target(args)
  # make_hook 的待決定清單只查規則錨點的決定；掛在 change 上的 make_hook 決定寫了也不會生效。
  if args.kind == "make_hook":
    require("rule" in target, "make_hook needs a rule anchor (--rule-path --rule-line --rule-quote) as its target")
  else:
    require("change_id" in target, args.kind + " needs a change as its target")
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  # 真人原話沿用 decide 的驗證；決定時間取自那筆 session 紀錄。
  found = human_row(source["path"], source["line"], source["quote"])
  decided_at = found["timestamp"]
  moment(decided_at, "decision source timestamp")
  uuid = found["uuid"]
  body = {
    "proposal": args.kind,
    "target": target,
    "target_key": key,
    "decision": args.decision,
    "source": source,
    "event_uuid": uuid,
    "decided_at": decided_at,
  }
  entry_id = "proposal-decision:" + uuid + ":" + args.kind + ":" + key

  def build(existing):
    if "change_id" in target:
      body["issue_id"] = find_change(existing, target["change_id"])["issue_id"]
    return entry("proposal_decision", entry_id, body)

  return append_one(args, build)


def counts(rows, issue):
  bucket = {name: 0 for name in VERIFICATIONS}
  for row in rows:
    if row["type"] == "discovery" and row["issue_id"] == issue:
      bucket[row["verification"]] += 1
  events = []
  for row in rows:
    if row["type"] == "event" and issue in row["issue_ids"] and row["id"] not in events:
      events.append(row["id"])
  for row in rows:
    if row["type"] == "event_link" and row["issue_id"] == issue and row["event_id"] not in events:
      events.append(row["event_id"])
  return bucket, events


def issue_sessions(rows, issue):
  seen = []
  for row in rows:
    if row["type"] == "discovery" and row["issue_id"] == issue:
      raw = row.get("raw")
      if isinstance(raw, dict):
        for s in raw.get("source_sessions") or []:
          if isinstance(s, str) and s.strip() and s.strip() not in seen:
            seen.append(s.strip())
        for ev in raw.get("evidence") or []:
          s = ev.get("session") if isinstance(ev, dict) else None
          if isinstance(s, str) and s.strip() and s.strip() not in seen:
            seen.append(s.strip())
    elif row["type"] == "event" and issue in row.get("issue_ids", []):
      s = row.get("session")
      if isinstance(s, str) and s.strip() and s.strip() not in seen:
        seen.append(s.strip())
  return seen


def change_views(rows, now):
  """One view per change: its latest verdict, `effective`, filed items, and whether it needs a re-judge."""
  verdicts = {}
  filed = {}
  for row in rows:
    if row["type"] == "verdict":
      verdicts[row["change_id"]] = row
    elif row["type"] == "backfill":
      item = {"kind": row["kind"], "source": row["source"]}
      known = filed.setdefault(row["change_id"], [])
      if not any(same(item, old) for old in known):
        known.append(item)
  views = []
  for row in rows:
    if row["type"] != "change":
      continue
    change_id = row["change_id"]
    latest_verdict = verdicts.get(change_id)
    later = 0
    if latest_verdict is not None:
      cut = moment(latest_verdict["at"], "verdict.at")
      # 只有已載入新版的再犯（outcome 為 recurrence）算判定之後的新證據。
      later = sum(
        1
        for obs in now
        if obs["change_id"] == change_id
        and obs["outcome"] == "recurrence"
        and isinstance(obs.get("event_time"), str)
        and moment(obs["event_time"], "event_time") > cut
      )
    views.append(
      {
        "id": change_id,
        "issue_id": row["issue_id"],
        "effective_at": row["effective_at"],
        "items": filed.get(change_id, []),
        "verdict": "insufficient" if latest_verdict is None else latest_verdict["verdict"],
        "verdict_id": None if latest_verdict is None else latest_verdict["id"],
        "verdict_at": None if latest_verdict is None else latest_verdict["at"],
        "level": None if latest_verdict is None else latest_verdict["level"],
        "effective": None if latest_verdict is None else EFFECTIVE_OF[latest_verdict["verdict"]],
        "needs_rejudge": later > 0,
        "recurrences_after_verdict": later,
        "side_effects_unchecked": latest_verdict is not None
        and latest_verdict["side_effects"] == "unchecked",
      }
    )
  return views


def latest_proposals(rows):
  found = {}
  for row in rows:
    if row["type"] == "proposal_decision":
      found[(row["proposal"], row["target_key"])] = row
  return found


def open_proposals(view, proposals):
  """Retract/modify proposals still waiting on the user for a bad verdict; [] once one was accepted."""
  cut = moment(view["verdict_at"], "verdict.at")
  waiting = []
  for kind in CHANGE_PROPOSALS:
    row = proposals.get((kind, "change:" + view["id"]))
    # 只有不早於判定的決定算數；判定在拒絕之後才下的，就是拒絕之後的新證據。
    stands = row is not None and moment(row["decided_at"], "decided_at") >= cut
    if stands and row["decision"] == "accepted":
      return []
    if not (stands and row["decision"] == "rejected"):
      waiting.append(kind)
  return waiting


def pending_decisions(rows, views):
  proposals = latest_proposals(rows)
  out = {
    "ineffective": [],
    "harmful": [],
    "needs_rejudge": [],
    "effective": [],
    "side_effects_unchecked": [],
    "level_four_only": 0,
    "make_hook": [],
    "make_hook_unchecked": [],
  }
  for view in views:
    if view["verdict_id"] is None:
      continue
    core = {
      "change_id": view["id"],
      "issue_id": view["issue_id"],
      "verdict_id": view["verdict_id"],
      "level": view["level"],
    }
    if view["verdict"] in ("ineffective", "harmful"):
      waiting = open_proposals(view, proposals)
      if waiting:
        out[view["verdict"]].append({**core, "open_proposals": waiting})
    if view["needs_rejudge"]:
      out["needs_rejudge"].append(
        {
          **core,
          "verdict": view["verdict"],
          "recurrences_after_verdict": view["recurrences_after_verdict"],
        }
      )
    if view["verdict"] == "effective":
      out["effective"].append({**core, "side_effects_unchecked": view["side_effects_unchecked"]})
      if view["side_effects_unchecked"]:
        out["side_effects_unchecked"].append(core)
    if view["level"] == 4:
      out["level_four_only"] += 1
  return out


def rule_anchor_list(rows):
  """Paragraph anchors of always-on-rule changes: the filed items (backfill) of kind always_on_rule."""
  changes = {row["change_id"]: row for row in rows if row["type"] == "change"}
  found = []
  for row in rows:
    if row["type"] != "backfill" or row["kind"] != "always_on_rule" or row["change_id"] not in changes:
      continue
    change = changes[row["change_id"]]
    item = {
      "change_id": change["change_id"],
      "issue_id": change["issue_id"],
      "effective_at": change["effective_at"],
      "path": row["source"]["path"],
      "quote": row["source"]["quote"],
    }
    if not any(same(item, old) for old in found):
      found.append(item)
  return found


def rule_anchors(store):
  target = os.path.join(os.path.abspath(os.path.expanduser(store)), RECORDS)
  if not os.path.lexists(target):
    return {"ok": True, "command": "rule-anchors", "anchors": []}
  with locked(store) as (_folder, handle):
    rows = load_entries(handle)
  return {"ok": True, "command": "rule-anchors", "anchors": rule_anchor_list(rows)}


def audit_query(audit_state, rules_repo, anchor_item, since):
  command = [
    sys.executable, "-I", AUDIT, "rule-violations", "--state", audit_state,
    "--rule-path", anchor_item["path"], "--rule-quote", anchor_item["quote"], "--since", since,
  ]
  if rules_repo:
    command += ["--rules-repo", rules_repo]
  try:
    done = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    result = json.loads(done.stdout) if done.returncode == 0 else None
  except (OSError, subprocess.TimeoutExpired, ValueError):
    result = None
  return result if isinstance(result, dict) else None


def make_hook_pending(rows, audit_state, rules_repo):
  """Rules with a change anchor that were broken in >= 3 distinct sessions since the change (or the last refusal).

  Only proposes. A rule the audit could not resolve or has not analyzed is listed as unchecked, never as zero.
  """
  anchors = rule_anchor_list(rows)
  if not anchors:
    return {"make_hook": [], "make_hook_unchecked": []}
  if not audit_state:
    return {"make_hook": [], "make_hook_unchecked": [{"reason": "audit-state-not-given"}]}
  proposals = latest_proposals(rows)
  latest = {}
  for item in anchors:
    key = (item["path"], item["quote"])
    if key not in latest or moment(item["effective_at"], "effective_at") >= moment(
      latest[key]["effective_at"], "effective_at"
    ):
      latest[key] = item
  proposed = []
  unchecked = []
  for (path, quote), item in latest.items():
    since = item["effective_at"]
    decision = proposals.get(("make_hook", "rule:" + path + "|" + quote))
    if decision is not None:
      if decision["decision"] == "accepted":
        continue
      # 拒絕只重置這一個提議的起點：之後又累積到門檻才再提。
      if moment(decision["decided_at"], "decided_at") > moment(since, "since"):
        since = decision["decided_at"]
    rule = {"path": path, "quote": quote}
    result = audit_query(audit_state, rules_repo, item, since)
    if result is None:
      unchecked.append({"rule": rule, "change_id": item["change_id"], "reason": "query-failed"})
    elif not result.get("found"):
      unchecked.append({"rule": rule, "change_id": item["change_id"], "reason": result.get("reason", "rule-not-found")})
    elif result.get("status") == "not_analyzed":
      unchecked.append({"rule": rule, "change_id": item["change_id"], "reason": "not-analyzed"})
    elif result["violating_sessions"] >= MAKE_HOOK_THRESHOLD:
      newest = {}
      for entry in result["violations"]:
        newest[entry["session_id"]] = entry
      evidence = sorted(newest.values(), key=lambda entry: entry["event_time"])[-MAKE_HOOK_EVIDENCE:]
      proposed.append(
        {
          "proposal": "make_hook",
          "rule": rule,
          "change_id": item["change_id"],
          "issue_id": item["issue_id"],
          "since": since,
          "violating_sessions": result["violating_sessions"],
          "evidence": evidence,
          "coverage": result["coverage"],
        }
      )
  return {"make_hook": proposed, "make_hook_unchecked": unchecked}


def project_issue(rows, issue_row, current=None, views=None):
  issue = issue_row["issue_id"]
  bucket, events = counts(rows, issue)
  sessions = issue_sessions(rows, issue)
  decision = latest(rows, "decision", issue)
  progress_row = latest(rows, "progress", issue)
  now = current_observations(rows) if current is None else current
  changes = [
    view for view in (change_views(rows, now) if views is None else views) if view["issue_id"] == issue
  ]
  mine = [row for row in now if row["issue_id"] == issue]
  # 沒有事件時間的舊觀察，時間多半是寫入時間；另列、不算次數。
  observations = [row for row in mine if "event_time" in row]
  legacy = len(mine) - len(observations)
  correct = sum(1 for row in observations if row["outcome"] == "opportunity_met")
  recurring = [row for row in observations if row["outcome"] == "recurrence"]
  recurrence = len(recurring)
  # 不適用與未讀不是機會。沒有 correct/recurrence 仍是證據不足，不把未知寫成無效。
  evidence = "observed" if correct or recurrence else "insufficient_evidence"
  return {
    "id": issue,
    "title": issue_row["title"],
    "related_to": issue_row.get("related_to", []),
    "source_sessions": sessions,
    "discoveries": bucket,
    "events": events,
    "decision": None if decision is None else {"action": decision["action"], "id": decision["id"]},
    "progress": None
    if progress_row is None
    else {"state": progress_row["state"], "id": progress_row["id"]},
    "changes": changes,
    "observations": {
      "correct": correct,
      "recurrence": recurrence,
      "total": len(observations),
      "invalid": sum(1 for row in observations if row["outcome"] == "invalid"),
      "not_loaded": sum(1 for row in observations if row["outcome"] == "not_loaded"),
      "unchecked": sum(1 for row in observations if row["outcome"] == "unchecked"),
      "recurrence_by_coverage": {
        name: sum(1 for row in recurring if row.get("covered") == name) for name in COVERAGE
      },
      "legacy": legacy,
      "evidence": evidence,
      # 一個問題可有好幾個改動、各自有判定；問題層級不合併成單一值，值在每個改動上。
      "effective": None,
    },
  }


def evidence_for(rows, issue):
  own = {row["id"] for row in rows if row["type"] == "event" and issue in row["issue_ids"]}
  linked = {
    row["event_id"] for row in rows if row["type"] == "event_link" and row["issue_id"] == issue
  }
  return [
    row for row in rows if row["type"] == "event_evidence" and row["event_id"] in own | linked
  ]


def detail(rows, issue):
  return {
    "discoveries": [row for row in rows if row["type"] == "discovery" and row["issue_id"] == issue],
    "events": [row for row in rows if row["type"] == "event" and row["id"] in counts(rows, issue)[1]],
    "event_evidence": evidence_for(rows, issue),
    "event_links": [
      row for row in rows if row["type"] == "event_link" and row["issue_id"] == issue
    ],
    "decisions": [row for row in rows if row["type"] == "decision" and row["issue_id"] == issue],
    "progress_entries": [
      row for row in rows if row["type"] == "progress" and row["issue_id"] == issue
    ],
    "change_entries": [row for row in rows if row["type"] == "change" and row["issue_id"] == issue],
    "verdict_entries": [row for row in rows if row["type"] == "verdict" and row["issue_id"] == issue],
    "backfill_entries": [
      row for row in rows if row["type"] == "backfill" and row["issue_id"] == issue
    ],
    "proposal_decisions": [
      row for row in rows if row["type"] == "proposal_decision" and row.get("issue_id") == issue
    ],
    "observation_entries": [
      row for row in rows if row["type"] == "observation" and row["issue_id"] == issue
    ],
    "observation_corrections": [
      row for row in rows if row["type"] == "observation_correction" and row["issue_id"] == issue
    ],
    "observation_latest": [row for row in current_observations(rows) if row["issue_id"] == issue],
  }


def list_issues(
  store, issue, summary=False, decision_filter=None, with_changes=False, audit_state=None, rules_repo=None
):
  target = os.path.join(os.path.abspath(os.path.expanduser(store)), RECORDS)
  if not os.path.lexists(os.path.abspath(os.path.expanduser(store))) or not os.path.lexists(target):
    require(issue is None, "unknown issue reference: " + str(issue))
    out = {"ok": True, "command": "list", "issues": []}
    if summary:
      out["counts"] = {
        "total_issues": 0,
        "approved": 0,
        "hold": 0,
        "decline": 0,
        "undecided": 0,
        "with_changes": 0,
        "legacy_observations": 0,
      }
      out["pending_decisions"] = pending_decisions([], [])
    return out
  with locked(store) as (_folder, handle):
    rows = load_entries(handle)
  selected = [
    row for row in rows if row["type"] == "issue" and (issue is None or row["issue_id"] == issue)
  ]
  require(issue is None or selected, "unknown issue reference: " + str(issue))
  all_projected = []
  now = current_observations(rows)
  views = change_views(rows, now)
  for row in selected:
    projected = project_issue(rows, row, now, views)
    if issue is not None:
      projected["records"] = detail(rows, row["issue_id"])
    all_projected.append(projected)
  issues = all_projected
  if decision_filter is not None:
    if decision_filter == "undecided":
      issues = [item for item in issues if item["decision"] is None]
    else:
      issues = [
        item for item in issues if (item["decision"] or {}).get("action") == decision_filter
      ]
  if with_changes:
    issues = [item for item in issues if item["changes"]]
  if summary and issue is None:
    issues = [
      item
      for item in issues
      if item["decision"] is not None
      or item["changes"]
      or len(item.get("source_sessions", [])) >= 4
    ]
  out = {"ok": True, "command": "list", "issues": issues}
  if summary:
    out["counts"] = {
      "total_issues": len(all_projected),
      "returned_issues": len(issues),
      "approved": sum(
        1 for item in all_projected if (item["decision"] or {}).get("action") == "approve"
      ),
      "hold": sum(1 for item in all_projected if (item["decision"] or {}).get("action") == "hold"),
      "decline": sum(
        1 for item in all_projected if (item["decision"] or {}).get("action") == "decline"
      ),
      "undecided": sum(1 for item in all_projected if item["decision"] is None),
      "with_changes": sum(1 for item in all_projected if item["changes"]),
      "legacy_observations": sum(item["observations"]["legacy"] for item in all_projected),
    }
    # 待決定清單看全庫的改動，不受 --issue 或 --decision 篩選影響。
    out["pending_decisions"] = pending_decisions(rows, views)
    out["pending_decisions"].update(make_hook_pending(rows, audit_state, rules_repo))
  return out


def kind_from_path(path):
  """(kind, name) the file's location implies, in session-retro's vocabulary; (None, None) when it says nothing."""
  parts = Path(path).parts
  base, folders = parts[-1], parts[:-1]
  if base == "SKILL.md" and "skills" in folders:
    return "skill", folders[-1]
  # 最近的目錄優先：skills/x/scripts/run.sh 是 script，不是 skill。
  for folder in reversed(folders):
    if folder == "rules" and base.endswith(".md"):
      return "rule", None
    if folder == "commands" and base.endswith(".md"):
      return "command", base[: -len(".md")]
    if folder == "hooks":
      return "hook", None
    if folder == "scripts":
      return "script", None
  return None, None


def tracking_change(row, filed):
  """What session-retro validate reads for one change: kind, effective_time, new_text, name."""
  out = {
    "id": row["change_id"],
    "issue_id": row["issue_id"],
    "effective_time": row["effective_at"],
    "new_text": row["source"]["quote"],
    "condition": row["condition"],
    "source": row["source"],
    "kind_source": None,
  }
  kind, name = None, None
  if filed is not None:
    # 補登的類型比路徑準；store 的 always_on_rule 在 session-retro 叫 rule。
    kind = "rule" if filed["kind"] == "always_on_rule" else filed["kind"]
    out["kind_source"] = "backfill"
    # 載入檢查比的是補登指到的段落，不是當初記下的改動原文；原文不在規則檔裡就會誤判成沒載入。
    out["new_text"] = filed["source"]["quote"]
    out["source"] = filed["source"]
    for source in (filed["source"], row["source"]):
      path_kind, path_name = kind_from_path(source["path"])
      if path_kind == kind:
        name = path_name
        break
  # 沒補登就不給類型，讓載入判斷回未查：使用者 2026-11-06 選定只認第一次判定時的補登，不依檔案位置猜。
  if kind is not None:
    out["kind"] = kind
  if name is not None:
    out["name"] = name
  return out


def tracking_context(store, issue):
  """Read-only: the issues and change definitions a retro run's tracking-context.json needs from the store."""
  folder = os.path.abspath(os.path.expanduser(store))
  if not os.path.lexists(folder) or not os.path.lexists(os.path.join(folder, RECORDS)):
    require(issue is None, "unknown issue reference: " + str(issue))
    return {"ok": True, "command": "tracking-context", "issues": [], "changes": []}
  with locked(store) as (_folder, handle):
    rows = load_entries(handle)
  require(
    issue is None or any(row["type"] == "issue" and row["issue_id"] == issue for row in rows),
    "unknown issue reference: " + str(issue),
  )
  filed = {}
  for row in rows:
    if row["type"] == "backfill":
      filed[row["change_id"]] = row
  changes = [
    tracking_change(row, filed.get(row["change_id"]))
    for row in rows
    if row["type"] == "change" and (issue is None or row["issue_id"] == issue)
  ]
  issues = []
  for row in rows:
    if row["type"] != "issue" or (issue is not None and row["issue_id"] != issue):
      continue
    mine = [change["id"] for change in changes if change["issue_id"] == row["issue_id"]]
    sessions = issue_sessions(rows, row["issue_id"])
    decision = latest(rows, "decision", row["issue_id"])
    progress_row = latest(rows, "progress", row["issue_id"])
    # 與 list --summary 同一個門檻：已決定、有改動或跨 4 場以上的問題才是 cross 要比對的背景。
    if issue is None and decision is None and not mine and len(sessions) < 4:
      continue
    issues.append(
      {
        "id": row["issue_id"],
        "title": row["title"],
        "source_sessions": sessions,
        "decision": None if decision is None else decision["action"],
        "progress": None if progress_row is None else progress_row["state"],
        "change_ids": mine,
      }
    )
  return {"ok": True, "command": "tracking-context", "issues": issues, "changes": changes}


def emit(payload):
  print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build():
  parser = argparse.ArgumentParser(prog="retro-store")
  parser.add_argument("--store", default=DEFAULT_STORE)
  commands = parser.add_subparsers(dest="command", required=True)

  ingest_cmd = commands.add_parser("ingest")
  ingest_cmd.add_argument("--packet", required=True)
  ingest_cmd.add_argument("--apply", action="store_true")

  listed = commands.add_parser("list")
  listed.add_argument("--issue")
  listed.add_argument("--summary", action="store_true")
  listed.add_argument("--decision", choices=[*ACTIONS, "undecided"])
  listed.add_argument("--with-changes", action="store_true")
  # 要不要做成 hook 的提議要查 session-audit 的狀態庫；不給就不查，清單會標明沒查。
  listed.add_argument("--audit-state")
  listed.add_argument("--rules-repo")

  commands.add_parser("rule-anchors")

  context_cmd = commands.add_parser("tracking-context")
  context_cmd.add_argument("--issue")

  decision = commands.add_parser("decide")
  decision.add_argument("--issue")
  decision.add_argument("--issues", action="append", default=[])
  decision.add_argument("--action", required=True, choices=ACTIONS)
  decision.add_argument("--source", required=True)
  decision.add_argument("--line", required=True, type=int)
  decision.add_argument("--quote", required=True)
  decision.add_argument("--note")
  decision.add_argument("--apply", action="store_true")

  progressed = commands.add_parser("progress")
  progressed.add_argument("--issue", required=True)
  progressed.add_argument("--state", required=True, choices=PROGRESS)
  progressed.add_argument("--note", required=True)
  progressed.add_argument("--next")
  progressed.add_argument("--source", required=True)
  progressed.add_argument("--line", required=True, type=int)
  progressed.add_argument("--quote", required=True)
  progressed.add_argument("--apply", action="store_true")

  changed = commands.add_parser("change")
  changed.add_argument("--issue", required=True)
  changed.add_argument("--id", required=True)
  changed.add_argument("--effective-at", required=True)
  changed.add_argument("--source", required=True)
  changed.add_argument("--line", required=True, type=int)
  changed.add_argument("--quote", required=True)
  changed.add_argument("--condition", required=True)
  changed.add_argument("--apply", action="store_true")

  observed = commands.add_parser("observe")
  observed.add_argument("--issue", required=True)
  observed.add_argument("--id", required=True)
  observed.add_argument("--change", required=True)
  observed.add_argument("--outcome", required=True, choices=OUTCOMES)
  observed.add_argument("--at", required=True)
  observed.add_argument("--source", required=True)
  observed.add_argument("--line", required=True, type=int)
  observed.add_argument("--quote", required=True)
  observed.add_argument("--apply", action="store_true")

  judged = commands.add_parser("verdict")
  judged.add_argument("--change", required=True)
  judged.add_argument("--verdict", required=True)
  judged.add_argument("--level", required=True, type=int)
  judged.add_argument("--model", required=True)
  judged.add_argument("--route", required=True)
  judged.add_argument("--runs", required=True, type=int)
  judged.add_argument("--old-recurrences", required=True, type=int)
  judged.add_argument("--new-recurrences", required=True, type=int)
  judged.add_argument("--source", required=True)
  judged.add_argument("--line", required=True, type=int)
  judged.add_argument("--quote", required=True)
  judged.add_argument("--by", required=True)
  judged.add_argument("--side-effects", required=True)
  judged.add_argument("--at", required=True)
  judged.add_argument("--apply", action="store_true")

  filed = commands.add_parser("backfill")
  filed.add_argument("--change", required=True)
  filed.add_argument("--kind", required=True)
  filed.add_argument("--source", required=True)
  filed.add_argument("--line", required=True, type=int)
  filed.add_argument("--quote", required=True)
  filed.add_argument("--apply", action="store_true")

  proposed = commands.add_parser("proposal")
  proposed.add_argument("--kind", required=True)
  proposed.add_argument("--decision", required=True)
  proposed.add_argument("--change")
  proposed.add_argument("--rule-path")
  proposed.add_argument("--rule-line", type=int)
  proposed.add_argument("--rule-quote")
  proposed.add_argument("--source", required=True)
  proposed.add_argument("--line", required=True, type=int)
  proposed.add_argument("--quote", required=True)
  proposed.add_argument("--apply", action="store_true")
  return parser


def main(argv):
  parser = build()
  try:
    args = parser.parse_args(argv)
    if args.command == "ingest":
      payload = ingest(args.store, args.packet, args.apply)
    elif args.command == "list":
      payload = list_issues(
        args.store,
        args.issue,
        summary=args.summary,
        decision_filter=args.decision,
        with_changes=args.with_changes,
        audit_state=args.audit_state,
        rules_repo=args.rules_repo,
      )
    elif args.command == "rule-anchors":
      payload = rule_anchors(args.store)
    elif args.command == "tracking-context":
      payload = tracking_context(args.store, args.issue)
    elif args.command == "decide":
      payload = decide(args)
    elif args.command == "progress":
      payload = progress(args)
    elif args.command == "change":
      payload = change(args)
    elif args.command == "verdict":
      payload = verdict(args)
    elif args.command == "backfill":
      payload = backfill(args)
    elif args.command == "proposal":
      payload = proposal(args)
    else:
      payload = observe(args)
  except Invalid as error:
    die(str(error))
  except json.JSONDecodeError:
    die("packet is not JSON")
  except OSError as error:
    die(error.strerror or "store io failed")
  emit(payload)


if __name__ == "__main__":
  main(sys.argv[1:])
