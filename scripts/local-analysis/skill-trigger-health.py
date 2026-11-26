#!/usr/bin/env python3
"""skill-trigger-health channel 核心掃描器（純機械、零 LLM；判讀歸 main session）。

三段輸出：
  1. ⚠️ 警報：分類為 auto-only 的 skill 在 30 天窗內出現手動 slash（任何一次即報）
  2. 統計儀表：每 skill 的 auto invoke（distinct tool_use id）/ manual slash（distinct msg uuid）
  3. 死庫存候選：安裝 > min-age 天且 90 天窗內零 invoke（每輪限量列出、其餘計數）

分類 registry：reports/local-analysis/.skill-trigger-registry.json
  {name: {"mode": "auto-only|manual-by-design|dual", "decided": bool, "decided_by": "user|agent"}}
  decided=true 鎖定分類不覆寫（預設為使用者逐項拍板；授權下自決須標 decided_by="agent" 保持來源可辨）；decided=false 每輪按 frontmatter 重新自動猜。

範圍宣告（no silent caps）：只掃自有 ~/.claude/skills + ~/.claude/commands；
plugin 前綴（含冒號）的 Skill invoke 不對映；死庫存軸是 90 天窗、非真 lifetime。
死庫存軸排除 settings.json skillOverrides 設 name-only 的 skill（2026-08-04 拍板）：
name-only 只注入名字一行（~5 tok）、無 description 誤觸發面、由 router/手動叫用，
零 invoke 是設計預期而非死庫存訊號；排除數在報告標題註記、不靜默。
registry mode 為 hook-driven 的同樣排除（2026-09-22 拍板）：這類 command 由 hook 注入 workflow
指令觸發、不經 Skill tool（例 daily-topic），計數器結構上看不到；使用者原則是
「零成本／看不到的 skill 不進死庫存警報」。
計數紀律：auto 按 distinct tool_use id、manual 按 distinct message uuid 去重
（resume/fork 會複製訊息，原始命中數灌水 3-13 倍，前案見 memory adhd extract 洞察 2）。
"""
import argparse
import datetime
import json
import math
import os
import re
import select
import subprocess
import sys
import tempfile
import time

CMD_RE = re.compile(r"<command-name>/([A-Za-z0-9_-]+)</command-name>")
DUAL_RE = re.compile(r"invokable (manually|via)|手動|slash command|also invokable", re.IGNORECASE)
BARE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_frontmatter(path):
  desc, disable = "", False
  try:
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
      lines = fh.read().splitlines()
  except OSError:
    return desc, disable
  if not lines or lines[0].strip() != "---":
    return desc, disable
  desc_parts = []
  in_desc_block = False
  for line in lines[1:]:
    if line.strip() == "---":
      break
    if in_desc_block:
      if line.startswith((" ", "\t")) or not line.strip():
        part = line.strip()
        if part:
          desc_parts.append(part)
        continue
      in_desc_block = False
    if line.startswith("description:"):
      val = line[len("description:"):].strip()
      if val in ("|", ">", "|-", ">-", "|+", ">+", ""):
        in_desc_block = True
        desc_parts = []
      else:
        desc_parts = [val]
    elif line.startswith("disable-model-invocation:"):
      val = line[len("disable-model-invocation:"):].split("#", 1)[0].strip().lower()
      disable = val == "true"
  if desc_parts:
    desc = " ".join(desc_parts)
  return desc, disable


def build_inventory(skills_dir, commands_dir):
  inv = {}
  try:
    skill_dirs = sorted(
      (e for e in os.scandir(skills_dir) if not e.name.startswith(".")),
      key=lambda e: e.name,
    )
  except OSError:
    skill_dirs = []
  for entry in skill_dirs:
    skill_md = os.path.join(entry.path, "SKILL.md")
    if not os.path.lexists(skill_md):
      continue
    name = entry.name
    desc, disable = read_frontmatter(skill_md)
    if disable:
      guess = "manual-by-design"
    elif DUAL_RE.search(desc) or f"/{name}" in desc:
      guess = "dual"
    else:
      guess = "auto-only"
    inv[name] = {"source": "skill", "guess": guess, "path": skill_md, "disable": disable}
  try:
    cmd_entries = sorted(
      (e for e in os.scandir(commands_dir) if not e.name.startswith(".") and e.name.endswith(".md")),
      key=lambda e: e.name,
    )
  except OSError:
    cmd_entries = []
  for entry in cmd_entries:
    cmd_md = entry.path
    name = entry.name[:-3]
    _, cmd_disable = read_frontmatter(cmd_md)
    if name in inv:
      inv[name]["source"] = "skill+command"
      inv[name]["guess"] = "dual"
      inv[name]["disable"] = inv[name].get("disable", False) or cmd_disable
    else:
      inv[name] = {"source": "command", "guess": "manual-by-design", "path": cmd_md, "disable": cmd_disable}
  return inv


def _atomic_write_text(path, text):
  parent = os.path.dirname(os.path.abspath(path))
  os.makedirs(parent, exist_ok=True)
  tmp_path = None
  try:
    with tempfile.NamedTemporaryFile(
      mode="w",
      encoding="utf-8",
      dir=parent,
      prefix=os.path.basename(path) + ".",
      suffix=".tmp",
      delete=False,
    ) as fh:
      tmp_path = fh.name
      fh.write(text)
    os.replace(tmp_path, path)
    tmp_path = None
  finally:
    if tmp_path is not None:
      try:
        os.unlink(tmp_path)
      except OSError:
        pass


def _load_registry_checked(path, inventory):
  registry = {}
  load_ok = True
  if os.path.exists(path):
    try:
      with open(path, encoding="utf-8") as fh:
        loaded = json.load(fh)
      if isinstance(loaded, dict):
        registry = loaded
      else:
        print(
          f"WARNING: registry {path} is not a JSON object; preserving file on disk and using defaults",
          file=sys.stderr,
        )
        load_ok = False
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
      print(
        f"WARNING: failed to read registry {path} ({exc}); preserving file on disk and using defaults",
        file=sys.stderr,
      )
      load_ok = False
  new_names = {name for name in inventory if name not in registry}
  for name, meta in inventory.items():
    raw_entry = registry.get(name)
    entry = raw_entry if isinstance(raw_entry, dict) else {}
    if entry.get("decided") is True:
      registry[name] = entry
      continue
    preserved = {k: v for k, v in entry.items() if k not in ("mode", "decided", "decided_by")}
    registry[name] = {**preserved, "mode": meta["guess"], "decided": False}
  return registry, new_names, load_ok


def load_registry(path, inventory):
  registry, new_names, _ = _load_registry_checked(path, inventory)
  return registry, new_names


def save_registry(path, registry):
  text = json.dumps(dict(sorted(registry.items())), ensure_ascii=False, indent=1)
  _atomic_write_text(path, text)


_fromisoformat = datetime.datetime.fromisoformat


def parse_ts(rec, fallback):
  ts = rec.get("timestamp")
  if isinstance(ts, str):
    s = ts.strip()
    if s.endswith(("Z", "z")):
      s = s[:-1] + "+00:00"
    try:
      val = _fromisoformat(s).timestamp()
      if math.isfinite(val) and val > 0:
        return val
    except ValueError:
      pass
    return fallback
  if isinstance(ts, bool):
    return fallback
  if isinstance(ts, (int, float)):
    val = float(ts)
    if math.isfinite(val) and val > 0:
      return val / 1000.0 if val >= 1e11 else val
  return fallback


def parse_iso_date(val):
  if not isinstance(val, str) or not val.strip():
    return 0.0
  try:
    s = val.strip()
    if s.endswith(("Z", "z")):
      s = s[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
      dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.timestamp()
  except ValueError:
    return 0.0


def _record_event(store, name, key, ts):
  bucket = store.setdefault(name, {})
  if key in bucket:
    if ts < bucket[key]:
      bucket[key] = ts
  else:
    bucket[key] = ts


def _iter_project_jsonl_files(projects_dir, scan_cut):
  try:
    proj_entries = sorted(
      (e for e in os.scandir(projects_dir) if not e.name.startswith(".") and "-private-var-folders-" not in e.name),
      key=lambda e: e.name,
    )
  except OSError:
    return
  for proj_entry in proj_entries:
    try:
      if not proj_entry.is_dir():
        continue
      file_entries = sorted(
        (f for f in os.scandir(proj_entry.path) if not f.name.startswith(".") and f.name.endswith(".jsonl")),
        key=lambda f: f.name,
      )
    except OSError:
      continue
    for f_entry in file_entries:
      try:
        mtime = f_entry.stat().st_mtime
      except OSError:
        continue
      if mtime >= scan_cut:
        yield f_entry.path, mtime


def scan(projects_dir, names, alert_days, dead_days, now, max_remind_days=0):
  alert_cut = now - alert_days * 86400
  dead_cut = now - dead_days * 86400
  remind_cut = now - max(0, max_remind_days) * 86400
  scan_cut = min(alert_cut, dead_cut, remind_cut)
  auto_ids, manual_uuids = {}, {}
  files = 0
  loads = json.loads
  cmd_findall = CMD_RE.findall
  for path, mtime in _iter_project_jsonl_files(projects_dir, scan_cut):
    files += 1
    try:
      fh = open(path, "rb", buffering=32768)
    except OSError:
      continue
    with fh:
      for line_no, raw in enumerate(fh, 1):
        has_tu = b'"tool_use"' in raw
        has_cmd = b"<command-name>/" in raw
        has_skill = b'"Skill"' in raw and b'"skill"' in raw and (has_tu or b"\\u" in raw)
        has_wf = has_tu and b'"Workflow"' in raw
        if not (has_cmd or has_skill or has_wf):
          continue
        try:
          rec = loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
          continue
        if not isinstance(rec, dict):
          continue
        raw_msg = rec.get("message")
        msg = raw_msg if isinstance(raw_msg, dict) else {}
        ts = parse_ts(rec, mtime)
        if ts > now:
          continue
        role = msg.get("role")
        content = msg.get("content")
        if (has_skill or has_wf) and role == "assistant" and isinstance(content, list):
          for block_idx, block in enumerate(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
              continue
            bname = block.get("name")
            raw_inp = block.get("input")
            inp = raw_inp if isinstance(raw_inp, dict) else {}
            raw_bid = block.get("id")
            ev_key = raw_bid if isinstance(raw_bid, str) and raw_bid else f"{path}:{line_no}:{block_idx}"
            if bname == "Skill":
              skill = inp.get("skill")
              if isinstance(skill, str) and skill in names:
                _record_event(auto_ids, skill, ev_key, ts)
            elif bname == "Workflow":
              raw_wf = inp.get("name")
              wf = raw_wf if isinstance(raw_wf, str) else ""
              if not wf and isinstance(inp.get("scriptPath"), str) and inp["scriptPath"]:
                wf = os.path.splitext(os.path.basename(inp["scriptPath"]))[0]
              if wf in names:
                _record_event(auto_ids, wf, ev_key, ts)
        if has_cmd and role == "user":
          blob = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
          raw_uuid = rec.get("uuid")
          ev_key = raw_uuid if isinstance(raw_uuid, str) and raw_uuid else f"{path}:{line_no}"
          for hit in cmd_findall(blob):
            if hit in names:
              _record_event(manual_uuids, hit, ev_key, ts)
  def bucket(store):
    out = {}
    for name, events in store.items():
      out[name] = {
        "recent": sum(1 for t in events.values() if t >= alert_cut),
        "window": sum(1 for t in events.values() if t >= dead_cut),
        "last_ts": max(events.values()) if events else 0.0,
      }
    return out
  return bucket(auto_ids), bucket(manual_uuids), files


def install_date(git_dir, rel_path):
  if not git_dir:
    return None
  try:
    out = subprocess.run(
      ["git", "-C", git_dir, "log", "--diff-filter=A", "--format=%at", "-1", "--", rel_path],
      capture_output=True, text=True, timeout=15,
    )
    val = out.stdout.strip()
    return int(val) if val else None
  except (OSError, subprocess.SubprocessError, ValueError):
    return None


_GIT_PATHSPEC_MAGIC = frozenset("*?[:\\")


def _batch_git_log_prefixes(rels):
  prefixes = set()
  for rel in rels:
    top = rel.split("/", 1)[0] if "/" in rel else rel
    if not top or top == ".":
      return []
    prefixes.add(top)
    if len(prefixes) > 8:
      return []
  return sorted(prefixes)


def _batch_git_safe(git_dir):
  if any(f"GIT_{mode}_PATHSPECS" in os.environ for mode in ("LITERAL", "GLOB", "NOGLOB", "ICASE")):
    return False
  try:
    cfg = subprocess.run(
      ["git", "-C", git_dir, "config", "--type=bool", "--get-regexp", r"^log\.(follow|showsignature)$"],
      capture_output=True, timeout=15,
    )
    if cfg.returncode not in (0, 1) or cfg.stderr or b"true" in cfg.stdout:
      return False
  except (OSError, subprocess.SubprocessError):
    return False
  return True


def batch_install_dates(git_dir, rel_paths):
  """Batch-resolve install timestamps equivalent to per-file `git log --diff-filter=A --format=%at -1 -- <rel>`."""
  if not git_dir or not rel_paths:
    return {}
  if len(rel_paths) < 2 or not _batch_git_safe(git_dir):
    return {rel: install_date(git_dir, rel) for rel in rel_paths}

  batch_targets = set()
  fallback_rels = []
  for rel in rel_paths:
    if (
      not rel
      or os.path.isabs(rel)
      or rel in (".", "..")
      or rel.startswith("../")
      or any(ch in _GIT_PATHSPEC_MAGIC for ch in rel)
    ):
      fallback_rels.append(rel)
    else:
      batch_targets.add(rel)

  results = {}
  ambiguous = set()
  batch_failed = False
  if batch_targets:
    cmd = [
      "git", "-C", git_dir, "log",
      "--relative", "--full-history", "-m", "--no-renames",
      "--diff-filter=AD", "--format=%x1e%at", "--name-status", "-z",
    ]
    prefixes = _batch_git_log_prefixes(batch_targets)
    if prefixes:
      cmd.append("--")
      cmd.extend(prefixes)
    proc = None
    deadline = time.monotonic() + 15.0
    try:
      proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
      cur_ts = None
      pending_status = None
      buf = b""
      stdout_fd = proc.stdout.fileno()
      while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise subprocess.TimeoutExpired(cmd, 15.0)
        ready, _, _ = select.select([stdout_fd], [], [], remaining)
        if not ready:
          raise subprocess.TimeoutExpired(cmd, 15.0)
        chunk = os.read(stdout_fd, 32768)
        if not chunk:
          break
        buf += chunk
        parts = buf.split(b"\x00")
        buf = parts.pop()
        for raw_tok in parts:
          if not raw_tok:
            continue
          if pending_status is not None:
            path = os.fsdecode(raw_tok)
            st = pending_status
            pending_status = None
            if path in batch_targets:
              if st == "A":
                if path in results:
                  ambiguous.add(path)
                else:
                  results[path] = cur_ts
              else:
                if path not in results:
                  ambiguous.add(path)
            continue
          if raw_tok[0] == 0x1E:
            try:
              cur_ts = int(raw_tok[1:])
            except ValueError:
              cur_ts = None
            continue
          if raw_tok[0] == 0x0A:
            raw_tok = raw_tok[1:]
          if raw_tok == b"A":
            pending_status = "A"
          elif raw_tok == b"D":
            pending_status = "D"
      proc.stdout.close()
      rc = proc.wait(timeout=max(0.1, deadline - time.monotonic()))
      if rc != 0:
        batch_failed = True
    except (OSError, subprocess.SubprocessError):
      batch_failed = True
      if proc is not None:
        try:
          proc.kill()
          proc.wait(timeout=5)
        except (OSError, subprocess.SubprocessError):
          pass
    if batch_failed:
      results.clear()
      ambiguous.clear()
      fallback_rels.extend(sorted(batch_targets))

  for rel in ambiguous:
    results[rel] = install_date(git_dir, rel)
  for rel in fallback_rels:
    results[rel] = install_date(git_dir, rel)
  return results


def norm_repo_path(val):
  if not isinstance(val, str) or not val.strip():
    return ""
  return os.path.realpath(os.path.expanduser(val.strip()))


def read_matching_ledger(cand_path, real_repo):
  try:
    with open(cand_path, encoding="utf-8") as f:
      data = json.load(f)
    if isinstance(data, dict) and norm_repo_path(data.get("repo_path")) == real_repo:
      return data
  except (OSError, UnicodeDecodeError, json.JSONDecodeError):
    pass
  return None


def load_deep_check_ledger(state_dir, repo_dir):
  real_repo = norm_repo_path(repo_dir)
  if not state_dir or not os.path.isdir(state_dir) or not real_repo:
    return None
  base = os.path.basename(real_repo)
  parent = os.path.basename(os.path.dirname(real_repo))
  candidates = [
    os.path.join(state_dir, f"{base}.json"),
    os.path.join(state_dir, f"{parent}-{base}.json"),
  ]
  try:
    candidates.extend(
      e.path for e in sorted(
        (e for e in os.scandir(state_dir) if not e.name.startswith(".") and e.name.endswith(".json")),
        key=lambda e: e.name,
      )
    )
  except OSError:
    pass
  for cand in candidates:
    if os.path.exists(cand):
      matched = read_matching_ledger(cand, real_repo)
      if matched is not None:
        return matched
  return None


def git_rev_list_count(repo_dir, *rev_args):
  try:
    r = subprocess.run(
      ["git", "-C", repo_dir, "rev-list", "--count", *rev_args],
      capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0 and r.stdout.strip().isdigit():
      return int(r.stdout.strip())
  except (OSError, subprocess.SubprocessError):
    pass
  return None


def count_uninspected_commits(real_repo, ledger, since_30d):
  head_sha = str(ledger.get("head_sha") or "").strip()
  if head_sha:
    cnt = git_rev_list_count(real_repo, f"{head_sha}..HEAD")
    if cnt is not None:
      return cnt
  since = str(ledger.get("checked_at") or since_30d).strip()
  if BARE_DATE_RE.match(since):
    since = f"{since}T00:00:00Z"
  return git_rev_list_count(real_repo, f"--since={since}", "HEAD") or 0


def iter_candidate_repos(repo_roots):
  seen_repos = set()
  for root in repo_roots:
    root = os.path.expanduser(root.strip())
    if not root or not os.path.isdir(root):
      continue
    for entry in sorted(os.listdir(root)):
      repo_dir = os.path.join(root, entry)
      if not os.path.isdir(repo_dir) or not os.path.exists(os.path.join(repo_dir, ".git")):
        continue
      real_repo = norm_repo_path(repo_dir)
      if real_repo and real_repo not in seen_repos:
        seen_repos.add(real_repo)
        yield entry, real_repo


def _index_deep_check_ledgers(state_dir):
  """Load state_dir/*.json once, preserving load_deep_check_ledger precedence."""
  by_name = {}
  first_by_repo = {}
  if not state_dir or not os.path.isdir(state_dir):
    return by_name, first_by_repo
  try:
    entries = sorted(
      (e for e in os.scandir(state_dir) if e.name.endswith(".json")),
      key=lambda e: e.name,
    )
  except OSError:
    return by_name, first_by_repo
  for entry in entries:
    try:
      with open(entry.path, encoding="utf-8") as f:
        data = json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
      continue
    if not isinstance(data, dict):
      continue
    rp = norm_repo_path(data.get("repo_path"))
    if not rp:
      continue
    slim = {
      "head_sha": data.get("head_sha"),
      "checked_at": data.get("checked_at"),
      "skipped_at": data.get("skipped_at"),
    }
    by_name[entry.name] = (rp, slim)
    if not entry.name.startswith(".") and rp not in first_by_repo:
      first_by_repo[rp] = slim
  return by_name, first_by_repo


def _lookup_indexed_ledger(ledger_index, real_repo):
  if not real_repo:
    return None
  by_name, first_by_repo = ledger_index
  base = os.path.basename(real_repo)
  parent = os.path.basename(os.path.dirname(real_repo))
  for fname in (f"{base}.json", f"{parent}-{base}.json"):
    hit = by_name.get(fname)
    if hit is not None and hit[0] == real_repo:
      return hit[1]
  return first_by_repo.get(real_repo)


def evaluate_repo_candidate(entry, real_repo, state_dir, now, remind_days, since_30d, min_commits, ledger_index=None):
  ledger = _lookup_indexed_ledger(ledger_index, real_repo) if ledger_index is not None else load_deep_check_ledger(state_dir, real_repo)
  if ledger:
    last_activity = max(parse_iso_date(ledger.get("checked_at")), parse_iso_date(ledger.get("skipped_at")))
    if last_activity > 0 and (now - last_activity) < remind_days * 86400:
      return None
    count = count_uninspected_commits(real_repo, ledger, since_30d)
    return (count, f"{entry}（+{count} commits）") if count >= min_commits else None
  count = git_rev_list_count(real_repo, f"--since={since_30d}", "HEAD") or 0
  return (count, f"{entry}（+{count} commits，首次健檢）") if count >= min_commits else None


def _read_loose_head_sha(repo_dir):
  git_dir = os.path.join(repo_dir, ".git")
  if not os.path.isdir(git_dir):
    return None
  try:
    with open(os.path.join(git_dir, "HEAD"), encoding="utf-8") as fh:
      head = fh.read(256).strip()
  except OSError:
    return None
  if head.startswith("ref: "):
    ref = head[5:].strip()
    ref_path = os.path.join(git_dir, *ref.split("/"))
    try:
      with open(ref_path, encoding="utf-8") as fh:
        sha = fh.read(64).strip()
    except OSError:
      return None
    return sha if len(sha) == 40 else None
  return head if len(head) == 40 else None


def _batch_git_rev_list_counts(jobs, max_workers=None):
  """Run `git -C <repo> rev-list --count <args...>` in bounded concurrent waves with shared deadline & cleanup."""
  if not jobs:
    return []
  if max_workers is None:
    max_workers = min(16, max(4, (os.cpu_count() or 4) * 2))
  results = [None] * len(jobs)
  for start in range(0, len(jobs), max_workers):
    wave = jobs[start : start + max_workers]
    procs = []
    try:
      for idx, (repo_dir, rev_args) in enumerate(wave, start):
        try:
          proc = subprocess.Popen(
            ["git", "-C", repo_dir, "rev-list", "--count", *rev_args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
          )
          procs.append((idx, proc, time.monotonic() + 10.0))
        except OSError:
          continue
      for idx, proc, deadline in procs:
        try:
          out, _ = proc.communicate(timeout=max(0.05, deadline - time.monotonic()))
          if proc.returncode == 0:
            val = out.strip()
            if val.isdigit():
              results[idx] = int(val)
        except (OSError, subprocess.SubprocessError):
          try:
            proc.kill()
            proc.wait(timeout=5)
          except (OSError, subprocess.SubprocessError):
            pass
    finally:
      for _, proc, _ in procs:
        if proc.poll() is None:
          try:
            proc.kill()
            proc.wait(timeout=2)
          except (OSError, subprocess.SubprocessError):
            pass
  return results


def _ledger_since_arg(ledger, since_30d):
  since = str(ledger.get("checked_at") or since_30d).strip()
  if BARE_DATE_RE.match(since):
    since = f"{since}T00:00:00Z"
  return since


def nominate_deep_check_repos(state_dir, repo_roots, now, remind_days, min_commits=20):
  since_30d = datetime.datetime.fromtimestamp(now - 30 * 86400, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
  ledger_index = _index_deep_check_ledgers(state_dir)
  candidates = []
  pending = []
  primary_jobs = []
  for entry, real_repo in iter_candidate_repos(repo_roots):
    ledger = _lookup_indexed_ledger(ledger_index, real_repo)
    if ledger:
      last_activity = max(parse_iso_date(ledger.get("checked_at")), parse_iso_date(ledger.get("skipped_at")))
      if last_activity > 0 and (now - last_activity) < remind_days * 86400:
        continue
      head_sha = str(ledger.get("head_sha") or "").strip()
      if head_sha:
        if len(head_sha) == 40 and _read_loose_head_sha(real_repo) == head_sha:
          if 0 >= min_commits:
            candidates.append((0, f"{entry}（+0 commits）"))
          continue
        pending.append((entry, real_repo, ledger, True))
        primary_jobs.append((real_repo, (f"{head_sha}..HEAD",)))
      else:
        since = _ledger_since_arg(ledger, since_30d)
        pending.append((entry, real_repo, ledger, False))
        primary_jobs.append((real_repo, (f"--since={since}", "HEAD")))
    else:
      pending.append((entry, real_repo, None, False))
      primary_jobs.append((real_repo, (f"--since={since_30d}", "HEAD")))

  primary_counts = _batch_git_rev_list_counts(primary_jobs)
  fallback_pending = []
  fallback_jobs = []
  for (entry, real_repo, ledger, used_sha), cnt in zip(pending, primary_counts):
    if cnt is None and used_sha:
      since = _ledger_since_arg(ledger, since_30d)
      fallback_pending.append((entry, ledger))
      fallback_jobs.append((real_repo, (f"--since={since}", "HEAD")))
      continue
    count = cnt or 0
    if count >= min_commits:
      suffix = "" if ledger else "，首次健檢"
      candidates.append((count, f"{entry}（+{count} commits{suffix}）"))

  if fallback_jobs:
    fallback_counts = _batch_git_rev_list_counts(fallback_jobs)
    for (entry, _ledger), cnt in zip(fallback_pending, fallback_counts):
      count = cnt or 0
      if count >= min_commits:
        candidates.append((count, f"{entry}（+{count} commits）"))

  candidates.sort(key=lambda x: (-x[0], x[1]))
  return [label for _, label in candidates[:2]]


def main():
  ap = argparse.ArgumentParser()
  ap.add_argument("--date", required=True)
  ap.add_argument("--out", required=True)
  ap.add_argument("--projects-dir", default=os.path.expanduser("~/.claude/projects"))
  ap.add_argument("--skills-dir", default=os.path.expanduser("~/.claude/skills"))
  ap.add_argument("--commands-dir", default=os.path.expanduser("~/.claude/commands"))
  ap.add_argument("--registry", default="/Users/linhancheng/code/social-info/reports/local-analysis/.skill-trigger-registry.json")
  ap.add_argument("--claude-git-dir", default=os.path.expanduser("~/.claude"))
  ap.add_argument("--deep-check-state-dir", default=os.path.expanduser("~/.claude/skills/deep-check/state"))
  ap.add_argument("--repo-roots", default=f"{os.path.expanduser('~/Desktop/work/calyx')},{os.path.expanduser('~/Desktop/projects')}")
  ap.add_argument("--alert-window-days", type=int, default=30)
  ap.add_argument("--dead-window-days", type=int, default=90)
  ap.add_argument("--dead-min-age-days", type=int, default=90)
  ap.add_argument("--dead-cap", type=int, default=3)
  args = ap.parse_args()

  if args.alert_window_days < 1 or args.dead_window_days < 1:
    print("Error: --alert-window-days and --dead-window-days must be >= 1", file=sys.stderr)
    sys.exit(2)
  if args.dead_min_age_days < 0 or args.dead_cap < 0:
    print("Error: --dead-min-age-days and --dead-cap must be >= 0", file=sys.stderr)
    sys.exit(2)
  for label, dpath in (
    ("--projects-dir", args.projects_dir),
    ("--skills-dir", args.skills_dir),
    ("--commands-dir", args.commands_dir),
  ):
    if not dpath or not os.path.isdir(dpath):
      print(f"Error: {label} directory does not exist: {dpath}", file=sys.stderr)
      sys.exit(2)

  now = time.time()
  inventory = build_inventory(args.skills_dir, args.commands_dir)
  registry, new_names, registry_load_ok = _load_registry_checked(args.registry, inventory)
  if registry_load_ok:
    save_registry(args.registry, registry)
  max_remind_days = 0
  for entry in registry.values():
    if isinstance(entry, dict):
      rd = entry.get("remind_after_days")
      if isinstance(rd, int) and not isinstance(rd, bool) and rd > max_remind_days:
        max_remind_days = rd
  auto, manual, files = scan(
    args.projects_dir,
    set(inventory),
    args.alert_window_days,
    args.dead_window_days,
    now,
    max_remind_days=max_remind_days,
  )

  name_only = set()
  for sp in (os.path.expanduser("~/.claude/settings.json"), os.path.expanduser("~/.claude-team/settings.json")):
    try:
      with open(sp, encoding="utf-8") as fh:
        cfg = json.load(fh)
      if not isinstance(cfg, dict):
        continue
      ov = cfg.get("skillOverrides")
      if not isinstance(ov, dict):
        continue
      name_only.update(k for k, v in ov.items() if v == "name-only")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
      continue

  alerts, rows, dead, reminders, undecided, new_undecided = [], [], [], [], [], []
  dead_name_only_excluded = 0
  dead_hook_driven_excluded = 0
  dead_disabled_excluded = 0
  dead_remind_excluded = 0
  repo_roots = [r for r in args.repo_roots.split(",") if r.strip()]
  sorted_inventory = sorted(inventory.items())
  needed_rels = []
  rel_by_name = {}
  if args.claude_git_dir:
    for name, meta in sorted_inventory:
      raw_reg = registry.get(name)
      reg_entry = raw_reg if isinstance(raw_reg, dict) else {}
      mode = reg_entry.get("mode") or meta["guess"]
      remind_days = reg_entry.get("remind_after_days")
      has_remind = isinstance(remind_days, int) and not isinstance(remind_days, bool) and remind_days > 0
      a = auto.get(name)
      m = manual.get(name)
      need_inst = False
      if has_remind:
        last_invoked = max(a["last_ts"] if a else 0.0, m["last_ts"] if m else 0.0)
        last_skipped = parse_iso_date(reg_entry.get("last_skipped_at"))
        prior_ts = max(last_invoked, last_skipped)
        if prior_ts == 0.0 or (now - prior_ts) >= remind_days * 86400:
          need_inst = True
      elif ((a["window"] if a else 0) + (m["window"] if m else 0)) == 0:
        if name not in name_only and mode != "hook-driven" and not meta.get("disable"):
          need_inst = True
      if need_inst:
        rel = os.path.relpath(meta["path"], args.claude_git_dir)
        rel_by_name[name] = rel
        needed_rels.append(rel)
  install_dates = batch_install_dates(args.claude_git_dir, needed_rels)

  empty_stat = {"recent": 0, "window": 0, "last_ts": 0.0}
  for name, meta in sorted_inventory:
    raw_reg = registry.get(name)
    reg_entry = raw_reg if isinstance(raw_reg, dict) else {}
    mode = reg_entry.get("mode") or meta["guess"]
    decided = reg_entry.get("decided") is True
    remind_days = reg_entry.get("remind_after_days")
    has_remind = isinstance(remind_days, int) and not isinstance(remind_days, bool) and remind_days > 0
    a = auto.get(name, empty_stat)
    m = manual.get(name, empty_stat)
    rel = rel_by_name.get(name)
    inst = install_dates.get(rel) if rel is not None else None
    if has_remind:
      last_invoked = max(a.get("last_ts", 0.0), m.get("last_ts", 0.0))
      last_skipped = parse_iso_date(reg_entry.get("last_skipped_at"))
      ref_ts = max(last_invoked, last_skipped, float(inst or 0.0))
      if ref_ts == 0.0 or (now - ref_ts) >= remind_days * 86400:
        days_ago = int((now - ref_ts) / 86400) if ref_ts > 0 else remind_days
        hint = str(reg_entry.get("remind_hint") or "").strip()
        hint_part = f"｜{hint}" if hint else ""
        if name == "deep-check":
          top_repos = nominate_deep_check_repos(args.deep_check_state_dir, repo_roots, now, remind_days)
          if top_repos:
            reminders.append(f"- `{name}`（距上次 {days_ago} 天{hint_part}）→ 推薦目標：{'、'.join(top_repos)}")
        else:
          reminders.append(f"- `{name}`（距上次 {days_ago} 天{hint_part}）")
    if not decided:
      undecided.append(f"{name}（自動猜 {mode}、source {meta['source']}）")
      if name in new_names:
        new_undecided.append(f"{name}（自動猜 {mode}、source {meta['source']}）")
    if mode == "auto-only" and m["recent"] > 0:
      alerts.append(
        f"- ⚠️ `{name}`（auto-only）{args.alert_window_days} 天內被手動 slash **{m['recent']} 次**（同窗 auto {a['recent']} 次）——該自動觸發卻要人手打"
      )
    if a["recent"] or m["recent"]:
      status_tag = " (未拍板)" if not decided else (" (agent自決)" if reg_entry.get("decided_by") == "agent" else "")
      rows.append(f"| `{name}` | {mode}{status_tag} | {a['recent']} | {m['recent']} | {a['window'] + m['window']} |")
    if a["window"] + m["window"] == 0:
      if has_remind:
        dead_remind_excluded += 1
        continue
      if name in name_only:
        dead_name_only_excluded += 1
        continue
      if mode == "hook-driven":
        dead_hook_driven_excluded += 1
        continue
      if meta.get("disable"):
        dead_disabled_excluded += 1
        continue
      if inst and (now - inst) >= args.dead_min_age_days * 86400:
        dead.append((inst, name, mode))
  dead.sort()

  lines = [
    f"# Skill trigger health — {args.date}",
    "",
    f"範圍：自有 skills（{sum(1 for v in inventory.values() if 'skill' in v['source'])}）+ commands（{sum(1 for v in inventory.values() if v['source'] == 'command')}）；plugin 前綴 invoke 不對映；掃 {files} 個 jsonl（mtime {args.dead_window_days} 天內）；死庫存軸 = {args.dead_window_days} 天窗非真 lifetime。",
    "",
    "## 警報（auto-only 被手動 slash）",
    "",
  ]
  lines += alerts if alerts else ["無。"]
  lines += ["", f"## 🔔 久未執行提醒（{len(reminders)}）", ""]
  lines += reminders if reminders else ["無。"]
  lines += ["", f"## 統計儀表（{args.alert_window_days} 天窗、僅列有活動者，共 {len(rows)}/{len(inventory)}）", "", "| skill | mode | auto | manual | 90d 合計 |", "|---|---|---|---|---|"]
  lines += rows if rows else []
  lines += ["", f"## 死庫存候選（安裝 ≥ {args.dead_min_age_days} 天且 {args.dead_window_days} 天零 invoke；本輪列 {min(len(dead), args.dead_cap)}/{len(dead)}；另排除 name-only {dead_name_only_excluded} 個——router 選單成員、零 invoke 屬設計預期；排除 hook-driven {dead_hook_driven_excluded} 個——由 hook 注入觸發、不經 Skill tool，計數器看不到；排除 disable-model-invocation {dead_disabled_excluded} 個——零常駐 token 成本；排除 remind_after_days {dead_remind_excluded} 個——低頻週期提醒工具）", ""]
  for inst, name, mode in dead[: args.dead_cap]:
    age = int((now - inst) / 86400)
    lines.append(f"- `{name}`（{mode}、安裝 {age} 天、{args.dead_window_days} 天零 invoke）→ 留/殺候選")
  if len(dead) > args.dead_cap:
    lines.append(f"- ……另 {len(dead) - args.dead_cap} 個候選排隊（下輪輪替）")
  if not dead:
    lines.append("無。")
  lines += ["", f"## 待拍板分類（registry decided=false 共 {len(undecided)}；漸進制——只在警報／死庫存／skill 結案時逐項拍，不做全量批次，2026-08-07 拍板）", ""]
  if new_undecided:
    lines.append(f"本輪新增 {len(new_undecided)} 項：")
    lines += [f"- {u}" for u in new_undecided]
    lines.append("")
    lines.append(f"拍板方式：改 {args.registry} 對應 entry 的 mode 並設 decided=true（使用者逐項拍板省略 decided_by；授權下由 agent 自決則補 decided_by=\"agent\" 保持來源可辨）。")
  else:
    lines.append("本輪無新增；既有未拍板項留在 registry、遇事件才逐項拍。")

  _atomic_write_text(args.out, "\n".join(lines) + "\n")
  print(f"skill-trigger-health: alerts={len(alerts)} active={len(rows)} dead={len(dead)} undecided={len(undecided)}")


if __name__ == "__main__":
  main()
