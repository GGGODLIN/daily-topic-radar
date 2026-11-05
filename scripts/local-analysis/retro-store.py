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


def human_quote(path, line, quote):
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
  return matched["uuid"]


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


def proposal_events(item, label, destinations):
  require(isinstance(item, dict), label + " must be an object")
  issue = ident(item.get("issue_id"), label + ".issue_id")
  change_id = ident(item.get("change_id"), label + ".change_id")
  outcome = item.get("outcome")
  require(outcome in OUTCOMES, label + ".outcome is invalid")
  covered = optional_coverage(item.get("covered"), label + ".covered")
  evidence = item.get("evidence")
  events = item.get("events")
  require(isinstance(evidence, list) and evidence, label + ".evidence must be a non-empty list")
  # events[] 由驗收器逐筆從 evidence 產生；缺少或對不上，代表它沒走過驗收，不當舊格式收。
  require(
    isinstance(events, list) and len(events) == len(evidence),
    label + ".events must match evidence one to one",
  )
  found = []
  for position, (event, point) in enumerate(zip(events, evidence)):
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
    require(isinstance(point, dict), label + ".evidence[" + str(position) + "] must be an object")
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
  return found


def packet_observations(packet):
  """Validate the observation sections alone; the store is not needed to say a packet is malformed."""
  proposals = packet.get("observation_proposals", [])
  require(isinstance(proposals, list), "observation_proposals must be a list")
  destinations = packet_destinations(packet.get("tracking_dispositions", []))
  found = []
  for index, item in enumerate(proposals):
    found.extend(proposal_events(item, "observation_proposals[" + str(index) + "]", destinations))
  landed = {item["key"] for item in found}
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
    built.append({"key": item["key"], "body": body, "destination": destination})
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


def observation_history(rows):
  """Per observation id: its base row and corrections in file order, plus every evidence point and run seen."""
  history = {}
  points = {}
  runs = {}
  for row in rows:
    kind = row["type"]
    if kind not in ("observation", "observation_correction", "observation_evidence"):
      continue
    key = row["observation_id"]
    if kind != "observation_evidence":
      history.setdefault(key, []).append(row)
    if isinstance(row.get("evidence"), dict):
      point = row["evidence"]
      points.setdefault(key, {})[(point["session"], point["source_line"])] = point["quote"]
    if isinstance(row.get("run_id"), str):
      runs.setdefault(key, set()).add(row["run_id"])
  return history, points, runs


def current_observations(rows):
  history, _points, _runs = observation_history(rows)
  return [states[-1] for states in history.values()]


def plan_observations(rows, built):
  history, points, runs = observation_history(rows)
  fresh = []
  tally = {"new": 0, "replayed": 0, "supplemented": 0, "corrected": 0}

  def settle(key, body):
    # 同一事件的證據點：已記過就是重播，沒記過才是補證。
    point = body["evidence"]
    place = (point["session"], point["source_line"])
    known = points.setdefault(key, {}).get(place)
    if known is None:
      row = entry(
        "observation_evidence",
        "observation-evidence:" + key + ":" + place[0] + ":" + str(place[1]),
        {"observation_id": key, "run_id": body["run_id"], "evidence": point},
      )
      fresh.append(row)
      points[key][place] = point["quote"]
      tally["supplemented"] += 1
      return
    require(known == point["quote"], "conflicting source quote: " + key)
    tally["replayed"] += 1

  for item in built:
    key, body = item["key"], item["body"]
    states = history.get(key, [])
    if not states:
      row = entry("observation", "observation:" + key, body)
      fresh.append(row)
      history[key] = [row]
      points.setdefault(key, {})[(body["evidence"]["session"], body["evidence"]["source_line"])] = (
        body["evidence"]["quote"]
      )
      runs.setdefault(key, set()).add(body["run_id"])
      tally["new"] += 1
      continue
    same_run = next((state for state in states if state.get("run_id") == body["run_id"]), None)
    if same_run is not None:
      require(
        observation_core(same_run) == observation_core(body),
        "conflicting observation in one run: " + key,
      )
      settle(key, body)
    elif body["run_id"] in runs.get(key, set()):
      # 這一輪先前只補過證，結果之後可能已被更正；重播它不能把結果改回去。
      settle(key, body)
    elif observation_core(states[-1]) == observation_core(body):
      settle(key, body)
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
      points.setdefault(key, {})[(body["evidence"]["session"], body["evidence"]["source_line"])] = (
        body["evidence"]["quote"]
      )
      runs.setdefault(key, set()).add(body["run_id"])
      tally["corrected"] += 1
  return fresh, tally


def ingest(store, packet_path, apply):
  packet = json.loads(read_regular(packet_path))
  proposed = packet_entries(packet)
  proposals, rejected, tracked = packet_observations(packet)
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
      out["observations"] = {**tally, "rejected": rejected}
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
  return summary(fresh, len(skipped) + tally["replayed"], tally)


def read_regular(path):
  path = os.path.realpath(path)
  require(os.path.isfile(path) and not os.path.islink(path), "packet is not a regular file")
  try:
    handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
  except OSError as error:
    raise Invalid("cannot read packet") from error
  with os.fdopen(handle, "r", encoding="utf-8") as stream:
    return stream.read()


def decision_entry(issue, action, source, note):
  uuid = verify_anchor(source, human=True)
  entry_id = uuid + ":" + issue + ":" + action
  body = {"issue_id": issue, "action": action, "source": source, "event_uuid": uuid}
  if note is not None:
    body["note"] = note
  return entry("decision", entry_id, body)


def decide(args):
  source = anchor({"path": args.source, "line": args.line, "quote": args.quote}, "source")
  issue = ident(args.issue, "issue")
  row = decision_entry(issue, args.action, source, args.note)
  with locked(args.store, create=args.apply) as (_folder, handle):
    existing = load_entries(handle)
    require(
      any(item["type"] == "issue" and item["issue_id"] == issue for item in existing),
      "unknown issue reference: " + issue,
    )
    fresh, skipped = plan_append(existing, [row])
    if args.apply and fresh:
      append_rows(handle, fresh)
  return {
    "ok": True,
    "command": "decision",
    "apply": args.apply,
    "appended": len(fresh),
    "noop": len(skipped),
    "id": row["id"],
  }


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


def project_issue(rows, issue_row, current=None):
  issue = issue_row["issue_id"]
  bucket, events = counts(rows, issue)
  sessions = issue_sessions(rows, issue)
  decision = latest(rows, "decision", issue)
  progress_row = latest(rows, "progress", issue)
  changes = [row for row in rows if row["type"] == "change" and row["issue_id"] == issue]
  now = current_observations(rows) if current is None else current
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
    "changes": [{"id": row["change_id"], "effective_at": row["effective_at"]} for row in changes],
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
    "observation_entries": [
      row for row in rows if row["type"] == "observation" and row["issue_id"] == issue
    ],
    "observation_corrections": [
      row for row in rows if row["type"] == "observation_correction" and row["issue_id"] == issue
    ],
    "observation_latest": [row for row in current_observations(rows) if row["issue_id"] == issue],
  }


def list_issues(store, issue, summary=False, decision_filter=None, with_changes=False):
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
    return out
  with locked(store) as (_folder, handle):
    rows = load_entries(handle)
  selected = [
    row for row in rows if row["type"] == "issue" and (issue is None or row["issue_id"] == issue)
  ]
  require(issue is None or selected, "unknown issue reference: " + str(issue))
  all_projected = []
  now = current_observations(rows)
  for row in selected:
    projected = project_issue(rows, row, now)
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
  return out


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

  decision = commands.add_parser("decide")
  decision.add_argument("--issue", required=True)
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
      )
    elif args.command == "decide":
      payload = decide(args)
    elif args.command == "progress":
      payload = progress(args)
    elif args.command == "change":
      payload = change(args)
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
