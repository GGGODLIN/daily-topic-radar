#!/usr/bin/env python3
"""Build a retro-store ingest packet from one finished retrospective run.

The packet is a new file. This script never writes the store, never hashes
content, and never promotes a source verdict onto a discovery.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.dont_write_bytecode = True

SCHEMA_VERSION = 1
SCOPE = "整項候選未逐事件語意核查；原始裁決另存"
CROSS_NAMES = ("cross-session.json", "reports/cross-session.json")
PLAN_NAMES = ("plan.json", "input-plan.json")
REVIEW_NAMES = (
  "reports/verify-source.json",
  "reports/verify-current.json",
  "reports/verify-source-addendum.json",
  "reports/verify-current-addendum.json",
)
TITLE_LIMIT = 80


class Reject(ValueError):
  pass


def reject(message):
  raise Reject(message)


def require(condition, message):
  if not condition:
    reject(message)


def load_json(path, label):
  require(os.path.isfile(path) and not os.path.islink(path), "missing " + label + ": " + path)
  try:
    with open(path, encoding="utf-8") as stream:
      value = json.load(stream)
  except (OSError, UnicodeError, json.JSONDecodeError) as error:
    reject(label + " is not readable JSON: " + path)
    raise error
  require(isinstance(value, dict), label + " must be an object: " + path)
  return value


def text_lines(path):
  # splitlines() 會把 U+2028 當成換行，行號就不再是檔案的換行行。
  with open(path, encoding="utf-8") as stream:
    return stream.read().split("\n")


def locate_id(lines, pattern_id):
  token = json.dumps(pattern_id, ensure_ascii=False)
  hits = [number for number, line in enumerate(lines, 1) if re.search(r'"id"\s*:\s*' + re.escape(token), line)]
  require(len(hits) == 1, "pattern id is not a unique cross line: " + pattern_id)
  return hits[0], token


def string_field(record, key):
  value = record.get(key)
  require(isinstance(value, str) and value.strip(), "missing string " + key)
  return value


def list_field(record, key):
  value = record.get(key)
  require(isinstance(value, list), "missing list " + key)
  return value


def pattern_records(cross):
  patterns = list_field(cross, "patterns")
  if "secondary_patterns" in cross:
    secondary_key = "secondary_patterns"
  elif "secondary_findings" in cross:
    secondary_key = "secondary_findings"
  else:
    reject("cross has neither secondary_patterns nor secondary_findings")
  secondary = list_field(cross, secondary_key)
  records = [("primary", item) for item in patterns] + [("secondary", item) for item in secondary]
  seen = set()
  for _role, record in records:
    require(isinstance(record, dict), "pattern must be an object")
    pattern_id = string_field(record, "id")
    require(pattern_id not in seen, "duplicate pattern id: " + pattern_id)
    seen.add(pattern_id)
  return records


def readable_title(record):
  title = record.get("title")
  if isinstance(title, str) and title.strip():
    return title.strip()
  observation = record.get("observation")
  if isinstance(observation, str) and observation.strip():
    sentence = observation.strip().split("。", 1)[0].split("\n", 1)[0]
    return sentence[:TITLE_LIMIT]
  items = record.get("items")
  texts = (
    [
      item[-1].strip()
      for item in items
      if isinstance(item, list) and item and isinstance(item[-1], str) and item[-1].strip()
    ]
    if isinstance(items, list)
    else []
  )
  if texts:
    return "；".join(texts)[:TITLE_LIMIT]
  reject("pattern has no readable title: " + str(record.get("id")))


def issue_binding(pattern_id, record, issue_map):
  mapped = issue_map.get(pattern_id)
  if isinstance(mapped, str):
    issue_id = mapped
    title = readable_title(record)
  elif isinstance(mapped, dict):
    require(
      isinstance(mapped.get("id"), str) and mapped["id"].strip(),
      "issue-map id is missing: " + pattern_id,
    )
    require(
      isinstance(mapped.get("title"), str),
      "issue-map title is missing: " + pattern_id,
    )
    issue_id = mapped["id"]
    title = mapped["title"]
  elif mapped is None:
    issue_id = None
    title = readable_title(record)
  else:
    reject("issue-map value must be an id or {id, title}: " + pattern_id)
  require(isinstance(title, str) and title.strip(), "issue title must be a string: " + pattern_id)
  return issue_id, title


def review_files(run):
  available = []
  limitations = []
  for relative in REVIEW_NAMES:
    path = os.path.join(run, relative)
    if not os.path.lexists(path):
      limitations.append("review file absent: " + relative)
      continue
    if not os.path.isfile(path) or os.path.islink(path):
      limitations.append("review path is not a regular file: " + path)
      continue
    review = load_json(path, relative)
    available.append((relative, os.path.realpath(path), text_lines(path), review))
  return available, limitations


def copied_review(finding, path, line):
  require(isinstance(finding.get("verdict"), str), "critic finding has no verdict")
  return {"finding": dict(finding), "path": path, "line": line}


def matching_reviews(review, pattern_id):
  matches = []
  if review.get("pattern_id") == pattern_id:
    matches.append(review)
  findings = review.get("findings")
  if isinstance(findings, list):
    matches.extend(
      finding
      for finding in findings
      if isinstance(finding, dict) and finding.get("pattern_id") == pattern_id
    )
  return matches


def review_hits(files, pattern_id):
  hits = []
  copied = []
  limitations = []
  token = json.dumps(pattern_id, ensure_ascii=False)
  for relative, path, lines, review in files:
    matches = matching_reviews(review, pattern_id)
    if not matches:
      continue
    found = next(
      (
        number
        for number, line in enumerate(lines, 1)
        if token in line and ('"pattern_id"' in line or '"id"' in line)
      ),
      None,
    )
    if found is None:
      limitations.append("review has no pattern id line: " + relative + " " + pattern_id)
      continue
    hits.append({"path": path, "line": found, "quote": token})
    copied.extend(copied_review(finding, path, found) for finding in matches)
  return hits, copied, limitations


def dispositions(cross):
  rows = cross.get("dispositions")
  if rows is None:
    return None
  require(isinstance(rows, list), "dispositions must be a list")
  counts = {"kept": 0, "merged": 0, "rejected": 0}
  seen = set()
  for row in rows:
    require(isinstance(row, dict), "disposition must be an object")
    item_id = string_field(row, "id")
    verdict = string_field(row, "verdict")
    require(item_id not in seen, "duplicate disposition id: " + item_id)
    seen.add(item_id)
    require(verdict in counts, "unknown disposition verdict: " + verdict)
    counts[verdict] += 1
  referenced = set()
  for key in ("patterns", "secondary_patterns", "secondary_findings"):
    for record in cross.get(key) or []:
      if isinstance(record, dict):
        for item_id in record.get("source_finding_ids") or []:
          require(isinstance(item_id, str), "source finding id must be a string")
          referenced.add(item_id)
  retained = {row["id"] for row in rows if row["verdict"] != "rejected"}
  rejected = sorted(row["id"] for row in rows if row["verdict"] == "rejected")
  return {
    "counts": counts,
    "total": len(seen),
    "unrepresented_ids": sorted(retained - referenced),
    "rejected_ids": rejected,
  }


def legacy_dispositions(cross):
  coverage = cross.get("coverage")
  require(isinstance(coverage, dict), "legacy cross is missing coverage counts")
  needed = (
    "retained_original_finding_ids",
    "source_rejected_original_finding_ids",
    "recommended_source_records",
    "secondary_source_records",
    "unaccounted_original_finding_ids",
  )
  for key in needed:
    require(key in coverage, "legacy coverage is missing " + key)
  retained = coverage["retained_original_finding_ids"]
  rejected = coverage["source_rejected_original_finding_ids"]
  recommended = coverage["recommended_source_records"]
  secondary = coverage["secondary_source_records"]
  unaccounted = coverage["unaccounted_original_finding_ids"]
  for label, value in (
    ("retained", retained),
    ("rejected", rejected),
    ("recommended", recommended),
    ("secondary", secondary),
  ):
    require(isinstance(value, int) and not isinstance(value, bool), label + " count is not an int")
  require(isinstance(unaccounted, list), "unaccounted ids must be a list")
  return {
    "record_counts": {"recommended": recommended, "secondary": secondary},
    "finding_counts": {"retained": retained, "rejected": rejected},
    "unaccounted_original_finding_ids": list(unaccounted),
    "basis": (
      "record counts and finding counts are separate coverage totals; "
      "this is not a per-finding reconciliation"
    ),
  }


def select_cross(run):
  found = [
    os.path.join(run, relative)
    for relative in CROSS_NAMES
    if os.path.isfile(os.path.join(run, relative))
    and not os.path.islink(os.path.join(run, relative))
  ]
  require(found, "missing cross-session.json")
  if len(found) == 1:
    return found[0]
  preferred = os.path.join(run, "reports", "cross-session.json")
  require(preferred in found, "cross-session.json location is not one of the two known formats")
  return preferred


def select_plan(run):
  found = [
    os.path.join(run, name)
    for name in PLAN_NAMES
    if os.path.isfile(os.path.join(run, name)) and not os.path.islink(os.path.join(run, name))
  ]
  require(len(found) == 1, "plan.json or input-plan.json must exist exactly once")
  return found[0]


def run_identity(run, plan, plan_path):
  run_id = plan.get("run_id")
  if not isinstance(run_id, str) or not run_id.strip():
    run_id = os.path.basename(os.path.realpath(run))
  cutoff = plan.get("cutoff")
  require(isinstance(cutoff, str) and cutoff.strip(), "missing cutoff")
  try:
    parsed = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
  except ValueError:
    reject("cutoff is not ISO: " + cutoff)
  require(parsed.tzinfo is not None, "cutoff requires timezone: " + cutoff)
  return run_id, cutoff, plan_path


def build_packet(run, issue_map):
  run = os.path.realpath(run)
  require(os.path.isdir(run) and not os.path.islink(run), "run is not a directory: " + run)
  cross_path = select_cross(run)
  plan_path = select_plan(run)
  cross = load_json(cross_path, "cross")
  plan = load_json(plan_path, "plan")
  run_id, cutoff, plan_path = run_identity(run, plan, plan_path)
  lines = text_lines(cross_path)
  issues = []
  discoveries = []
  seen_issues = {}
  reviews_available, limitations = review_files(run)
  for role, record in pattern_records(cross):
    pattern_id = record["id"]
    line, quote = locate_id(lines, pattern_id)
    mapped_id, title = issue_binding(pattern_id, record, issue_map)
    issue_id = mapped_id if mapped_id is not None else "imported:" + run_id + ":" + pattern_id
    previous = seen_issues.get(issue_id)
    if previous is None:
      seen_issues[issue_id] = title
      issues.append({"id": issue_id, "title": title})
    else:
      require(previous == title, "same issue id has different titles: " + issue_id)
    reviews, copied_reviews, review_limits = review_hits(reviews_available, pattern_id)
    limitations.extend(review_limits)
    raw_reviews = {"role": role, "record_status": record.get("status"), "reviews": copied_reviews}
    discoveries.append(
      {
        "id": run_id + ":" + pattern_id,
        "issue_id": issue_id,
        "source": {"path": os.path.realpath(cross_path), "line": line, "quote": quote},
        "verification": "pending",
        "review_sources": reviews,
        "scope": SCOPE,
        "raw": record,
        "raw_reviews": raw_reviews,
      }
    )
  report = {
    "run_id": run_id,
    "run_id_source": "plan.run_id"
    if isinstance(plan.get("run_id"), str) and plan.get("run_id").strip()
    else "directory name",
    "source": os.path.realpath(cross_path),
    "plan": os.path.realpath(plan_path),
    "cutoff": cutoff,
    "cross_status": cross.get("status"),
    "discoveries": len(discoveries),
    "issues": len(issues),
    "events": 0,
    "limitations": limitations,
  }
  tracking = cross.get("tracking_observations", [])
  require(isinstance(tracking, list), "tracking_observations must be a list")
  if isinstance(cross.get("dispositions"), list):
    report["dispositions"] = dispositions(cross)
  elif "retained_original_finding_ids" in (cross.get("coverage") or {}):
    report["dispositions"] = legacy_dispositions(cross)
  elif cross.get("patterns") == [] and (
    cross.get("secondary_patterns") == [] or cross.get("secondary_findings") == []
  ):
    report["dispositions"] = {
      "counts": {},
      "total": 0,
      "unrepresented_ids": [],
      "basis": "declared empty candidate lists; no finding account was present",
    }
  else:
    reject("cross has no disposition account")
  packet = {
    "schema_version": SCHEMA_VERSION,
    "run": {
      "id": run_id,
      "source": os.path.realpath(cross_path),
      "status": "partial",
      "cutoff": cutoff,
    },
    "issues": issues,
    "discoveries": discoveries,
    "events": [],
    "event_proposals": event_proposals(run, run_id),
    "run_metadata": {
      "plan": os.path.realpath(plan_path),
      "cross_status": cross.get("status"),
      "window": plan.get("window") or [plan.get("window_start") or plan.get("start"), cutoff],
      "raw_dispositions": cross.get("dispositions"),
      "raw_rejected": cross.get("rejected"),
      "raw_coverage": cross.get("coverage"),
      "tracking_observations": tracking,
    },
    "observation_proposals": tracking,
    "import_report": report,
  }
  return packet


def event_proposals(run, run_id):
  proposals = []
  for relative in ("reports/verify-source.json", "reports/verify-current.json"):
    path = os.path.join(run, relative)
    if not os.path.isfile(path) or os.path.islink(path):
      continue
    review = load_json(path, relative)
    findings = review.get("findings")
    require(isinstance(findings, list), relative + " findings must be a list")
    lines = text_lines(path)
    for finding in findings:
      require(isinstance(finding, dict), "critic finding must be an object")
      if finding.get("verdict") != "CONFIRMED":
        continue
      pattern_id = finding.get("pattern_id")
      require(isinstance(pattern_id, str) and pattern_id, "confirmed finding needs pattern_id")
      evidence = finding.get("evidence")
      require(isinstance(evidence, list), "confirmed finding needs evidence")
      token = json.dumps(pattern_id, ensure_ascii=False)
      line = next(
        (
          number
          for number, row in enumerate(lines, 1)
          if token in row and ('"pattern_id"' in row or '"id"' in row)
        ),
        None,
      )
      require(line is not None, "confirmed finding has no review line: " + pattern_id)
      proposals.append(
        {
          "run_id": run_id,
          "pattern_id": pattern_id,
          "review": {"path": os.path.realpath(path), "line": line, "quote": token},
          "evidence": evidence,
        }
      )
  return proposals


def load_issue_map(path):
  if path is None:
    return {}
  value = load_json(path, "issue-map")
  for key, item in value.items():
    require(isinstance(key, str) and key.strip(), "issue-map key must be a pattern id")
    require(isinstance(item, (str, dict)), "issue-map value is invalid: " + key)
  return value


def write_packet(packet, out):
  require(not os.path.lexists(out), "refusing to overwrite: " + out)
  parent = os.path.dirname(os.path.realpath(out))
  require(os.path.isdir(parent), "output directory does not exist: " + parent)
  temporary = out + ".tmp"
  require(not os.path.lexists(temporary), "refusing to overwrite temporary output: " + temporary)
  with open(temporary, "w", encoding="utf-8") as stream:
    json.dump(packet, stream, ensure_ascii=False, indent=2)
    stream.write("\n")
  os.replace(temporary, out)


def main(argv):
  parser = argparse.ArgumentParser(description="Build a retro-store ingest packet")
  parser.add_argument("--run", required=True)
  parser.add_argument("--out", required=True)
  parser.add_argument("--issue-map")
  args = parser.parse_args(argv)
  try:
    packet = build_packet(args.run, load_issue_map(args.issue_map))
    write_packet(packet, args.out)
  except (Reject, OSError, ValueError, TypeError) as error:
    print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
    return 2
  report = packet["import_report"]
  print(json.dumps({"ok": True, "out": os.path.realpath(args.out), **report}, ensure_ascii=False))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv[1:]))
