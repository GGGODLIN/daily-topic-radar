#!/usr/bin/env python3
"""Deterministic batch preparer and cursor committer for incremental memory-ripple scans.

Loads ~/.claude/memory once in memory and reproduces the exact word-boundary BM25
sibling ranking from ~/.claude/hooks/memory-ripple-runtime.py without an upper target cap.
"""

import argparse
import importlib.machinery
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

DEFAULT_STATE = Path("~/.claude/logs/memory-channel-state.json").expanduser()
DEFAULT_LOG = Path("~/.claude/logs/memory-ripple-audit.jsonl").expanduser()
DEFAULT_MEMORY_DIR = Path("~/.claude/memory").expanduser()
DEFAULT_RUNTIME = Path("~/.claude/hooks/memory-ripple-runtime.py").expanduser()
DEFAULT_SINCE = "2026-09-25T00:00:00+0800"
DEFAULT_BATCH_SIZE = 20
DEFAULT_WARN_THRESHOLD = 100


def load_runtime(runtime_path: Path):
  loader = importlib.machinery.SourceFileLoader("memory_ripple_runtime", str(runtime_path))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  module = importlib.util.module_from_spec(spec)
  loader.exec_module(module)
  return module


def load_since(state_path: Path):
  """Return (since_ts, state_existed). Fail closed if state file exists but is unreadable/corrupt."""
  if not state_path.exists():
    return DEFAULT_SINCE, False
  try:
    data = json.loads(state_path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise RuntimeError(f"corrupt_or_unreadable_state_file: {state_path}: {exc}") from exc
  if not isinstance(data, dict):
    raise RuntimeError(f"invalid_state_root_type: {state_path}")
  since = data.get("last_ripple_ts")
  if since is None or (isinstance(since, str) and not since.strip()):
    return DEFAULT_SINCE, True
  if not isinstance(since, str):
    raise RuntimeError(f"invalid_last_ripple_ts_type: {state_path}")
  return since.strip(), True


def collect_pending_events(log_path: Path, since: str, state_existed: bool):
  """Return (events, malformed_lines). Fail closed if state exists yet audit log is missing."""
  if not log_path.exists():
    if state_existed:
      raise RuntimeError(f"audit_log_missing_with_existing_state: {log_path}")
    return [], 0
  if not log_path.is_file():
    raise RuntimeError(f"audit_log_not_regular_file: {log_path}")
  events = []
  malformed_lines = 0
  with log_path.open("r", encoding="utf-8", errors="replace") as handle:
    for line in handle:
      line = line.strip()
      if not line:
        continue
      try:
        ev = json.loads(line)
      except json.JSONDecodeError:
        malformed_lines += 1
        continue
      if not isinstance(ev, dict):
        malformed_lines += 1
        continue
      ts = ev.get("ts") or ""
      if ev.get("fired") is True and isinstance(ts, str) and ts > since:
        events.append(ev)
  events.sort(key=lambda item: (str(item.get("ts") or ""), str(item.get("file") or "")))
  return events, malformed_lines


def load_corpus(memory_dir: Path, mr):
  """Load all memory .md files once. Fail closed if memory_dir is missing or traversal fails."""
  expanded = os.path.expanduser(str(memory_dir))
  if not os.path.isdir(expanded):
    raise RuntimeError(f"memory_dir_unavailable: {expanded}")
  root_str = os.path.realpath(expanded)
  if not os.path.isdir(root_str) or not os.access(root_str, os.R_OK | os.X_OK):
    raise RuntimeError(f"memory_dir_unreadable: {root_str}")

  corpus = []
  by_basename = {}

  def on_walk_error(err):
    raise RuntimeError(f"memory_dir_walk_error: {err}") from err

  for current_root, dirnames, filenames in os.walk(root_str, followlinks=False, onerror=on_walk_error):
    dirnames[:] = sorted(
      d for d in dirnames
      if d not in mr.EXCLUDED_DIRS and not os.path.islink(os.path.join(current_root, d))
    )
    for filename in sorted(filenames):
      if not filename.endswith(".md") or filename in mr.EXCLUDED_FILES:
        continue
      full_path = os.path.join(current_root, filename)
      if os.path.islink(full_path):
        continue
      real_candidate, text = mr._safe_read_regular_md(full_path, root_str)
      if real_candidate is None:
        raise RuntimeError(f"memory_file_unreadable: {full_path}")
      candidate = Path(real_candidate)
      rel_path = os.path.relpath(real_candidate, root_str)
      rel_stem = os.path.splitext(rel_path)[0].replace("/", " ").replace("_", " ").replace("-", " ")
      meta_text = rel_stem + " " + mr._frontmatter_meta(text)
      bag = mr._words_of(meta_text) * 2 + mr._words_of(text)
      counts = {}
      for word in bag:
        counts[word] = counts.get(word, 0) + 1
      doc = {
        "path": candidate,
        "real": real_candidate,
        "rel": rel_path,
        "is_index": mr._is_index_doc(candidate),
        "base_dl": len(bag),
        "counts": counts,
        "meta_lower": meta_text.lower(),
        "body_lower": text.lower(),
      }
      corpus.append(doc)
      by_basename.setdefault(filename, []).append(doc)
  return root_str, corpus, by_basename


def rank_siblings_from_corpus(target_file: Path, corpus, mr):
  source_real = os.path.realpath(os.path.expanduser(str(target_file)))
  tokens = mr.relative_tokens(str(target_file))
  if not tokens or not corpus:
    return tokens, [], []
  cjk_tokens = [tok for tok in tokens if not tok.isascii()]
  docs = []
  for doc in corpus:
    if doc["real"] == source_real:
      continue
    if cjk_tokens:
      counts = dict(doc["counts"])
      extra_dl = 0
      for ctok in cjk_tokens:
        ctf = doc["meta_lower"].count(ctok) * 2 + doc["body_lower"].count(ctok)
        if ctf:
          counts[ctok] = ctf
          extra_dl += ctf
      dl = max(doc["base_dl"] + extra_dl, 1)
    else:
      counts = doc["counts"]
      dl = max(doc["base_dl"], 1)
    docs.append({
      "path": doc["path"],
      "rel": doc["rel"],
      "is_index": doc["is_index"],
      "dl": dl,
      "counts": counts,
    })
  if not docs:
    return tokens, [], []
  total_docs = len(docs)
  avgdl = sum(d["dl"] for d in docs) / total_docs
  idf = {}
  for token in tokens:
    df = sum(1 for d in docs if token in d["counts"])
    idf[token] = math.log(1.0 + (total_docs - df + 0.5) / (df + 0.5))
  leaves = []
  indexes = []
  for doc in docs:
    score = 0.0
    for token in tokens:
      tf = doc["counts"].get(token, 0)
      if tf:
        denom = tf + mr.BM25_K1 * (1.0 - mr.BM25_B + mr.BM25_B * (doc["dl"] / avgdl))
        score += idf[token] * ((tf * (mr.BM25_K1 + 1.0)) / denom)
    if score <= 0.0:
      continue
    entry = (score, doc["path"], doc["rel"])
    if doc["is_index"]:
      indexes.append(entry)
    else:
      leaves.append(entry)
  leaves.sort(key=lambda item: (-round(item[0], 6), item[2]))
  indexes.sort(key=lambda item: (-round(item[0], 6), item[2]))
  return tokens, leaves, indexes


def cmd_prepare(args):
  state_path = Path(args.state).expanduser()
  log_path = Path(args.log).expanduser()
  memory_dir = Path(args.memory_dir).expanduser()
  runtime_path = Path(args.runtime).expanduser()
  work_dir = Path(args.work_dir).expanduser()
  batch_size = max(1, int(args.batch_size))
  warn_threshold = max(1, int(args.warn_threshold))

  try:
    if args.since:
      since, state_existed = args.since, state_path.exists()
    else:
      since, state_existed = load_since(state_path)
    events, malformed_lines = collect_pending_events(log_path, since, state_existed)
    mr = load_runtime(runtime_path)
    _, corpus, by_basename = load_corpus(memory_dir, mr)
  except (RuntimeError, OSError) as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    return 1

  work_dir.mkdir(parents=True, exist_ok=True)
  manifest_path = work_dir / "manifest.json"

  if not events:
    summary = {
      "ok": True,
      "date": args.date,
      "since": since,
      "cutoff_ts": since,
      "oldest_ts": "",
      "total_events": 0,
      "unique_targets": 0,
      "malformed_lines": malformed_lines,
      "missing_files": [],
      "warn_large": False,
      "warn_threshold": warn_threshold,
      "manifest_path": str(manifest_path),
      "batches": [],
    }
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0

  # Real memory-ripple.sh audit entries only write basename `file` (no relative_path).
  # Match all corpus files with that basename so same-named files across domains are all covered.
  grouped = {}
  missing_files = []
  missing_seen = set()

  for ev in events:
    ts = str(ev.get("ts") or "")
    fname = str(ev.get("file") or "").strip()
    kind = str(ev.get("gemini_kind") or ev.get("kind") or "unknown")

    matched_docs = by_basename.get(fname, []) if fname else []
    if not matched_docs:
      miss_key = fname or "(empty)"
      if miss_key not in missing_seen:
        missing_seen.add(miss_key)
        missing_files.append(miss_key)
      continue

    for doc in matched_docs:
      rec = grouped.get(doc["rel"])
      if rec is None:
        rec = {
          "target": str(doc["path"]),
          "target_rel": doc["rel"],
          "first_ts": ts,
          "latest_ts": ts,
          "event_count": 0,
          "kinds": set(),
        }
        grouped[doc["rel"]] = rec
      rec["event_count"] += 1
      if ts > rec["latest_ts"]:
        rec["latest_ts"] = ts
      if ts < rec["first_ts"]:
        rec["first_ts"] = ts
      if kind:
        rec["kinds"].add(kind)

  ordered_records = sorted(grouped.values(), key=lambda r: (r["latest_ts"], r["target_rel"]))
  items = []
  for rec in ordered_records:
    tokens, leaves, indexes = rank_siblings_from_corpus(Path(rec["target"]), corpus, mr)
    daily, tail = mr._partition_siblings(leaves, indexes)
    items.append({
      "target": rec["target"],
      "target_rel": rec["target_rel"],
      "first_ts": rec["first_ts"],
      "latest_ts": rec["latest_ts"],
      "event_count": rec["event_count"],
      "kinds": sorted(rec["kinds"]),
      "tokens": tokens,
      "siblings_daily": [
        {"kind": kind, "score": round(score, 2), "path": str(path), "rel": rel}
        for score, kind, _tier, path, rel in daily
      ],
      "tail_count": len(tail),
    })

  batches = []
  for idx in range(0, len(items), batch_size):
    chunk = items[idx:idx + batch_size]
    batch_num = idx // batch_size + 1
    batch_id = f"batch-{batch_num:02d}"
    batch_path = work_dir / f"{batch_id}.json"
    batch_path.write_text(json.dumps(chunk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    batches.append({
      "batch_id": batch_id,
      "path": str(batch_path),
      "target_count": len(chunk),
    })

  oldest_ts = str(events[0].get("ts") or "")
  cutoff_ts = str(events[-1].get("ts") or since)
  summary = {
    "ok": True,
    "date": args.date,
    "since": since,
    "cutoff_ts": cutoff_ts,
    "oldest_ts": oldest_ts,
    "total_events": len(events),
    "unique_targets": len(items),
    "malformed_lines": malformed_lines,
    "missing_files": sorted(missing_files),
    "warn_large": len(items) >= warn_threshold,
    "warn_threshold": warn_threshold,
    "manifest_path": str(manifest_path),
    "batches": batches,
  }
  manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  print(json.dumps(summary, ensure_ascii=False))
  return 0


def cmd_commit_cursor(args):
  state_path = Path(args.state).expanduser()
  report_path = Path(args.report).expanduser()
  new_ts = str(args.ts or "").strip()

  if not new_ts:
    print("ERROR: --ts is required", file=sys.stderr)
    return 2
  if not report_path.is_file() or report_path.stat().st_size == 0:
    print(f"ERROR: report missing or empty: {report_path}", file=sys.stderr)
    return 1

  if args.manifest:
    manifest_path = Path(args.manifest).expanduser()
    try:
      manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
      print(f"ERROR: failed to read manifest: {exc}", file=sys.stderr)
      return 1
    expected_ts = str(manifest.get("cutoff_ts") or "").strip() if isinstance(manifest, dict) else ""
    if not expected_ts or new_ts != expected_ts:
      print(f"ERROR: ts mismatch against manifest ({new_ts} != {expected_ts})", file=sys.stderr)
      return 1

  existing = {}
  if state_path.exists():
    try:
      loaded = json.loads(state_path.read_text(encoding="utf-8"))
      if not isinstance(loaded, dict):
        print(f"ERROR: state file root must be object: {state_path}", file=sys.stderr)
        return 1
      existing = loaded
    except (OSError, json.JSONDecodeError) as exc:
      print(f"ERROR: failed to read state file: {exc}", file=sys.stderr)
      return 1

  old_ts = str(existing.get("last_ripple_ts") or "")
  if old_ts and new_ts < old_ts:
    print(f"ERROR: cursor regression refused ({new_ts} < {old_ts})", file=sys.stderr)
    return 1

  existing["last_ripple_ts"] = new_ts
  state_path.parent.mkdir(parents=True, exist_ok=True)
  tmp_path = state_path.with_name(state_path.name + f".tmp.{os.getpid()}")
  tmp_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
  os.replace(tmp_path, state_path)
  print(json.dumps({"ok": True, "state": str(state_path), "previous_ts": old_ts, "last_ripple_ts": new_ts}, ensure_ascii=False))
  return 0


def main(argv=None):
  parser = argparse.ArgumentParser(description="Prepare incremental memory-ripple batches and commit cursor")
  sub = parser.add_subparsers(dest="cmd", required=True)

  p_prep = sub.add_parser("prepare", help="Prepare incremental batches from memory-ripple-audit.jsonl")
  p_prep.add_argument("--date", required=True)
  p_prep.add_argument("--work-dir", required=True)
  p_prep.add_argument("--state", default=str(DEFAULT_STATE))
  p_prep.add_argument("--log", default=str(DEFAULT_LOG))
  p_prep.add_argument("--memory-dir", default=str(DEFAULT_MEMORY_DIR))
  p_prep.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
  p_prep.add_argument("--since", default="")
  p_prep.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
  p_prep.add_argument("--warn-threshold", type=int, default=DEFAULT_WARN_THRESHOLD)
  p_prep.set_defaults(func=cmd_prepare)

  p_commit = sub.add_parser("commit-cursor", help="Advance last_ripple_ts after report write")
  p_commit.add_argument("--state", default=str(DEFAULT_STATE))
  p_commit.add_argument("--ts", required=True)
  p_commit.add_argument("--report", required=True)
  p_commit.add_argument("--manifest", default="")
  p_commit.set_defaults(func=cmd_commit_cursor)

  parsed = parser.parse_args(argv)
  return parsed.func(parsed)


if __name__ == "__main__":
  sys.exit(main())
