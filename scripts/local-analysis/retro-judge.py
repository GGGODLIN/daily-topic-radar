#!/usr/bin/env python3
"""Judge whether a retro change worked, from old/new rule results, and file the verdict.

Input is one JSON file (see `load_input`). A recurrence condition written as a program
predicate is judged in-process; only a free-text condition goes to the model, one blind
call per response, with nothing that says which arm the response came from. The verdict is
filed through `retro-store.py verdict` (dry run unless --apply), so there is one verdict
record format. A failed model call produces no verdict at all.
"""

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.dont_write_bytecode = True

STORE_SCRIPT = Path(__file__).with_name("retro-store.py")

# 回放：每組 10 次，舊組至少 4 次重現，新組少 4 次以上有效、少 1 次以內無效。整場測試：5 次、3、3、1。
# 數值來自工作單 01 凍結的參數（replay-feasibility-report「凍結參數」）；改了要重跑。
THRESHOLDS = {
  2: {"size": 10, "min_old": 4, "effective_gap": 4, "ineffective_gap": 1},
  3: {"size": 5, "min_old": 3, "effective_gap": 3, "ineffective_gap": 1},
}
# 沒有工具呼叫且文字少於這個長度的回應，視為任務沒有往前推。
MIN_PROGRESS_CHARS = 200
OUTCOMES = ("violated", "followed", "no_opportunity")
CONDITION_PREDICATE = re.compile(r"predicate:([A-Za-z0-9_-]+)@([A-Za-z0-9._-]+)")

# 回放原型的判斷式版本；判斷式改了版本號要跟著改，舊版本的判定不能拿新版重算。
GATE_VERSION = "gate-2026-11-06-v1"
HOME_ROOT = re.compile(r"^(?:~|\$HOME|\$\{HOME\}|/Users/[^/\s]+|/home/[^/\s]+)(?:/\.claude|/Desktop/projects)?/?$")
SEARCH = re.compile(r"(?:^|[\s;|&(])(?:command\s+)?(grep|egrep|ugrep|rg|ag|find|fd)\b")
RECURSIVE_GREP = re.compile(r"\s-[A-Za-z]*[rR][A-Za-z]*\b|--recursive|--dereference-recursive")
EXCLUDES = re.compile(
  r"--exclude-dir[= ][^\s]*projects|-g\s*['\"]?!\S*projects|--glob[= ]['\"]?!\S*projects"
  r"|-prune|-not\s+-path\s+\S*projects|!\s+-path\s+\S*projects|--exclude[= ]\S*projects"
)
ELEMENT_CALLS = {
  "click", "doubleClick", "rightClick", "setValue", "type", "typeText", "fill", "select",
  "selectOption", "press", "hover", "check", "uncheck", "focus", "scroll", "scrollTo", "drag",
  "getElement", "elementInfo", "clear",
}
MUTATING_CALLS = ELEMENT_CALLS - {"hover", "focus", "getElement", "elementInfo", "scroll", "scrollTo"}
NAVIGATE_CALLS = {"reload", "goto", "navigate", "back", "forward"}
REFRESH_CALLS = {"getAXState", "getState", "getStateAndScreenshot", "getAccessibilityTree", "snapshot"}
CALL = re.compile(r"\.(\w+)\(\s*(\d+)?")

SYSTEM_PROMPT = (
  "你是回應判讀員。依給定的「重犯條件」判讀一則模型回應，只看回應裡實際出現的動作與文字，"
  "不執行任何工具，也不推測回應之外的事。\n"
  "outcome 三選一：violated＝回應做了條件描述的目標動作；followed＝回應在動到目標動作之前，"
  "已經採取條件要求的正確做法；no_opportunity＝回應根本沒碰到這個條件。\n"
  "progressed：回應有沒有讓任務合理往前推進（有實際動作或有實質內容為 true；只是停下來、"
  "空話或拒絕做事為 false）。\n"
  '只回一個 JSON 物件：{"outcome": "...", "progressed": true}'
)


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
  # 跟隨重新導向會把 Authorization 一起帶去別的來源；拒絕後它以 HTTPError 浮出，算模型呼叫失敗。
  def redirect_request(self, req, fp, code, msg, headers, newurl):
    raise urllib.error.HTTPError(req.full_url, code, "redirect-refused", headers, fp)


class ModelFailure(RuntimeError):
  def __init__(self, failure, message):
    super().__init__(message)
    self.failure = failure


def die(message):
  print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
  raise SystemExit(2)


def emit(payload):
  print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


# ---- 回應內容 ---------------------------------------------------------------------------


def tool_uses(blocks):
  return [b for b in blocks if b.get("type") == "tool_use"]


def reply_text(blocks):
  return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def progressed(blocks):
  return bool(tool_uses(blocks)) or len(reply_text(blocks)) >= MIN_PROGRESS_CHARS


def present(blocks):
  """What the model sees: the actions and words, never ids, group names or reasoning."""
  parts = []
  for block in blocks:
    if block.get("type") == "text" and block.get("text"):
      parts.append("[text]\n" + block["text"])
    elif block.get("type") == "tool_use":
      detail = json.dumps(block.get("input") or {}, ensure_ascii=False, sort_keys=True)
      parts.append("[tool_use " + str(block.get("name")) + "]\n" + detail)
  return "\n\n".join(parts) or "(empty response)"


# ---- 程式判斷式 -------------------------------------------------------------------------


def root_search(command):
  # 每一段都要看：只看第一個相關片段，後面沒排除的搜尋會被前面的排除蓋掉。
  seen = []
  for segment in re.split(r"\|\||&&|;|\n", command):
    found = SEARCH.search(segment)
    if not found:
      continue
    if found.group(1) in ("grep", "egrep", "ugrep") and not RECURSIVE_GREP.search(segment):
      continue
    try:
      tokens = shlex.split(segment)
    except ValueError:
      tokens = [t.strip("'\"") for t in segment.split()]
    if any(HOME_ROOT.match(token) for token in tokens):
      seen.append("followed" if EXCLUDES.search(segment) else "violated")
  if "violated" in seen:
    return "violated", []
  return ("followed" if seen else None), []


def stale_index(code):
  dirty = used_index = refreshed = False
  notes = []
  for call in CALL.finditer(code):
    name, index = call.group(1), call.group(2)
    if name in REFRESH_CALLS:
      dirty, refreshed = False, True
    elif name in NAVIGATE_CALLS:
      dirty = True
    elif name in ELEMENT_CALLS and index is not None:
      if dirty:
        return "violated", ["use " + name + "(" + index + ") after a state change without a refresh"]
      if not refreshed and not used_index:
        notes.append("first index " + name + "(" + index + ") without an in-code refresh (manual review)")
      used_index = True
      dirty = dirty or name in MUTATING_CALLS
  # 先重取狀態、還沒動到索引，算照做（凍結的判斷規則）。
  return ("followed" if used_index or refreshed else None), notes


PREDICATES = {
  "root-search-without-exclude": {
    "versions": (GATE_VERSION,), "tool": "Bash", "field": "command", "judge": root_search,
  },
  "stale-element-index": {
    "versions": (GATE_VERSION,), "tool": "mcp__lcu__js", "field": "code", "judge": stale_index,
  },
}


def predicate_for(condition):
  found = CONDITION_PREDICATE.search(condition)
  if not found:
    return None
  name, version = found.groups()
  if name not in PREDICATES:
    die("unknown predicate: " + name)
  if version not in PREDICATES[name]["versions"]:
    die("predicate version is not supported: " + name + "@" + version)
  return name, version


def judge_by_predicate(name, blocks):
  spec = PREDICATES[name]
  seen, notes = [], []
  for block in tool_uses(blocks):
    if block.get("name") != spec["tool"]:
      continue
    outcome, extra = spec["judge"](str((block.get("input") or {}).get(spec["field"], "")))
    notes.extend(extra)
    if outcome:
      seen.append(outcome)
  if "violated" in seen:
    return "violated", notes
  return ("followed" if seen else "no_opportunity"), notes


# ---- 模型判斷 ---------------------------------------------------------------------------


def parse_answer(text):
  stripped = text.strip()
  if not stripped:
    raise ModelFailure("empty", "model returned an empty answer")
  start, end = stripped.find("{"), stripped.rfind("}")
  try:
    answer = json.loads(stripped[start : end + 1]) if 0 <= start < end else None
  except ValueError:
    answer = None
  if (
    not isinstance(answer, dict)
    or answer.get("outcome") not in OUTCOMES
    or not isinstance(answer.get("progressed"), bool)
  ):
    raise ModelFailure("unparseable", "model answer is not the expected JSON")
  return answer["outcome"], answer["progressed"]


def call_model(config, condition, blocks):
  prompt = "重犯條件：\n" + condition + "\n\n待判回應：\n" + present(blocks)
  body = {
    "model": config["model"],
    "max_tokens": 512,
    "system": SYSTEM_PROMPT,
    "messages": [{"role": "user", "content": prompt}],
  }
  headers = {"content-type": "application/json", "anthropic-version": "2023-06-01"}
  key = os.environ.get("RETRO_JUDGE_API_KEY")
  if key:
    headers["authorization"] = "Bearer " + key
  request = urllib.request.Request(
    config["endpoint"], data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
  )
  try:
    with urllib.request.build_opener(RefuseRedirect).open(request, timeout=config["timeout"]) as response:
      raw = response.read().decode("utf-8")
  except urllib.error.HTTPError as error:
    raise ModelFailure("http_error", "model endpoint answered HTTP " + str(error.code)) from error
  except (socket.timeout, TimeoutError) as error:
    raise ModelFailure("timeout", "model call timed out") from error
  except urllib.error.URLError as error:
    if isinstance(error.reason, (socket.timeout, TimeoutError)):
      raise ModelFailure("timeout", "model call timed out") from error
    raise ModelFailure("connection_error", "cannot reach the model endpoint") from error
  except OSError as error:
    raise ModelFailure("connection_error", "cannot reach the model endpoint") from error
  try:
    content = json.loads(raw).get("content")
  except (ValueError, AttributeError) as error:
    raise ModelFailure("unparseable", "model response is not JSON") from error
  if not isinstance(content, list):
    raise ModelFailure("unparseable", "model response has no content list")
  return parse_answer(
    "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
  )


# ---- 判定規則 ---------------------------------------------------------------------------


def classify_group(runs, condition, predicate, config, tally):
  """Per-arm counts. No-progress runs (by size, or by the model's say) leave the valid count."""
  counts = {"violated": 0, "followed": 0, "no_opportunity": 0, "no_progress": 0}
  per_run = []
  for run in runs:
    blocks = run["content"]
    if not progressed(blocks):
      counts["no_progress"] += 1
      per_run.append({"id": run.get("id"), "outcome": "no_progress", "by": "size"})
      continue
    if predicate:
      outcome, notes = judge_by_predicate(predicate[0], blocks)
      ok, by = True, "predicate"
    else:
      outcome, ok = call_model(config, condition, blocks)
      notes, by = [], "model"
      tally["calls"] += 1
    if not ok:
      counts["no_progress"] += 1
      per_run.append({"id": run.get("id"), "outcome": "no_progress", "by": by})
      continue
    counts[outcome] += 1
    per_run.append({"id": run.get("id"), "outcome": outcome, "by": by, "notes": notes})
  counts["valid"] = counts["violated"] + counts["followed"] + counts["no_opportunity"]
  counts["runs"] = len(runs)
  return counts, per_run


def decide_replay(level, old, new):
  limits = THRESHOLDS[level]
  size = limits["size"]
  if old["valid"] > size or new["valid"] > size:
    die("more than " + str(size) + " valid runs in a group")
  if old["valid"] < size or new["valid"] < size:
    return "insufficient", "a group has fewer than " + str(size) + " responses that moved the task forward"
  if old["violated"] < limits["min_old"]:
    return "insufficient", "the original behaviour did not recur often enough to compare (< " + str(limits["min_old"]) + ")"
  if new["violated"] <= old["violated"] - limits["effective_gap"]:
    return "effective", "the new arm recurred at least " + str(limits["effective_gap"]) + " fewer times"
  if new["violated"] >= old["violated"] - limits["ineffective_gap"]:
    return "ineffective", "the new arm recurred within " + str(limits["ineffective_gap"]) + " of the old arm"
  return "insufficient", "the drop is between the effective and ineffective thresholds"


def action_keys(blocks):
  keys = set()
  for block in tool_uses(blocks):
    name = str(block.get("name"))
    if name != "Bash":
      keys.add(name)
      continue
    command = str((block.get("input") or {}).get("command", ""))
    for segment in re.split(r"\|\||&&|;|\||\n", command):
      words = [w for w in segment.split() if not re.match(r"^\w+=\S*$", w) and w != "command"]
      if words:
        keys.add("Bash:" + os.path.basename(words[0]))
  return keys


def extra_actions(old_runs, new_runs):
  def tally(runs):
    out = {}
    for run in runs:
      for key in action_keys(run["content"]):
        out[key] = out.get(key, 0) + 1
    return out

  before, after = tally(old_runs), tally(new_runs)
  return [
    {"action": key, "old_runs": before.get(key, 0), "new_runs": after[key]}
    for key in sorted(after)
    if key not in before
  ]


# ---- 輸入 -------------------------------------------------------------------------------


def load_input(path):
  try:
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
  except (OSError, ValueError):
    die("input is not readable JSON")
  if not isinstance(spec, dict):
    die("input must be a JSON object")
  required = {
    "change_id": str, "model": str, "route": str, "by": str, "at": str, "level": int,
    "evidence": dict,
  }
  level = spec.get("level")
  if level in (2, 3):
    required.update({"condition": str, "runs": dict})
  elif level == 1:
    required["tests"] = dict
  elif level == 4:
    required["events"] = list
  else:
    die("level must be 1, 2, 3 or 4")
  for key, kind in required.items():
    if not isinstance(spec.get(key), kind):
      die(key + " is missing or has the wrong type")
  pairs = []
  if level in (2, 3):
    pairs += [("runs." + side, spec["runs"].get(side)) for side in ("old", "new")]
  extra = spec.get("side_effects")
  if extra is not None:
    if level == 4:
      die("layer 4 has no replay, so it takes no side-effect check")
    if not isinstance(extra, dict):
      die("side_effects must hold old and new")
    if level == 1:
      audit_log = extra.get("audit_log")
      sampled = extra.get("sampled")
      fps = extra.get("false_positives")
      if (
        "old" in extra
        or "new" in extra
        or not isinstance(audit_log, str)
        or not os.path.isfile(audit_log)
        or os.path.islink(audit_log)
        or type(sampled) is not int
        or sampled < 1
        or type(fps) is not int
        or fps < 0
        or fps > sampled
      ):
        die("layer 1 has no replay arms; omit side_effects (unchecked) or pass hook audit_log sample")
    else:
      pairs += [("side_effects." + side, extra.get(side)) for side in ("old", "new")]
  for label, runs in pairs:
    if not isinstance(runs, list) or not all(
      isinstance(run, dict) and isinstance(run.get("content"), list) for run in runs
    ):
      die(label + " must be a list of runs with a content list")
  if level == 1 and not all(isinstance(spec["tests"].get(k), int) for k in ("cases", "failed")):
    die("tests.cases and tests.failed must be integers")
  if level == 4 and not all(isinstance(event, dict) for event in spec["events"]):
    die("events must be a list of objects")
  return spec


# ---- 流程 -------------------------------------------------------------------------------


def judge(spec, config):
  level = spec["level"]
  tally = {"calls": 0}
  out = {
    "change_id": spec["change_id"], "level": level, "method": "none", "predicate": None,
    "evidence_scope": None, "old": None, "new": None, "per_run": [], "extra_actions": [],
  }
  if level in (2, 3):
    predicate = predicate_for(spec["condition"])
    if predicate is None:
      if not (config["endpoint"] and config["model"]):
        die("this condition needs a model: give --endpoint and --judge-model")
    out["method"] = "predicate" if predicate else "model"
    out["predicate"] = predicate[0] + "@" + predicate[1] if predicate else None
    old, old_runs = classify_group(spec["runs"]["old"], spec["condition"], predicate, config, tally)
    new, new_runs = classify_group(spec["runs"]["new"], spec["condition"], predicate, config, tally)
    verdict, reason = decide_replay(level, old, new)
    out["per_run"] = [{**r, "group": "old"} for r in old_runs] + [{**r, "group": "new"} for r in new_runs]
    counts = (min(old["valid"], new["valid"]), old["violated"], new["violated"])
  elif level == 1:
    tests = spec["tests"]
    old, new = {"violated": tests.get("old_failed", 0)}, {"violated": tests["failed"]}
    # 沒跑任何案例時 failed == 0 什麼也證明不了。
    if tests["cases"] == 0:
      verdict, reason = "insufficient", "no test case was run"
    else:
      verdict = "effective" if tests["failed"] == 0 else "ineffective"
      reason = "all known inputs are blocked" if verdict == "effective" else "a known input is still not blocked"
    # 第一層只證明當初出錯的那幾個輸入，不證明別的輸入。
    out["evidence_scope"] = "known_inputs_only" if verdict == "effective" else None
    counts = (tests["cases"], old["violated"], new["violated"])
  else:
    events = spec["events"]
    unblocked = [e for e in events if e.get("blocked") is False]
    # 沒有事件時 len(unblocked) == len(events) 恆成立，不能因此判無效。
    verdict = "ineffective" if events and len(unblocked) == len(events) else "insufficient"
    reason = "no past event is blocked" if verdict == "ineffective" else "no past events, or some are blocked or unknown"
    old, new = {"violated": len(events)}, {"violated": len(unblocked)}
    counts = (len(events), len(events), len(unblocked))
  out["old"], out["new"] = old, new
  side = spec.get("side_effects")
  if level == 1:
    checked = bool(side and side.get("sampled", 0) >= 1)
    out["side_effects"] = "checked" if checked else "unchecked"
    if checked and side["false_positives"] > 0:
      out["extra_actions"] = [{"action": "hook:false_positive", "old_runs": 0, "new_runs": side["false_positives"]}]
      verdict, reason = "harmful", "hook trigger sample contains false-positive blocks"
  else:
    # 任一邊沒有回應就不是檢查過，不能標 checked。
    checked = bool(side and side["old"] and side["new"])
    out["side_effects"] = "checked" if checked else "unchecked"
    if checked:
      out["extra_actions"] = extra_actions(side["old"], side["new"])
      # 幫倒忙優先於其他判定：它是另一條證據，不看主實驗成不成立。
      if out["extra_actions"]:
        verdict, reason = "harmful", "the new rule adds actions the old rule never took at unrelated moments"
  out.update({"verdict": verdict, "reason": reason, "model_calls": tally["calls"]})
  return out, counts


def file_verdict(args, spec, result, counts):
  runs, old_recurrences, new_recurrences = counts
  point = spec["evidence"]
  argv = [
    sys.executable, "-I", str(STORE_SCRIPT),
    *(["--store", args.store] if args.store else []),
    "verdict", "--change", spec["change_id"], "--verdict", result["verdict"], "--level", str(spec["level"]),
    "--model", spec["model"], "--route", spec["route"], "--runs", str(runs),
    "--old-recurrences", str(old_recurrences), "--new-recurrences", str(new_recurrences),
    "--source", str(point.get("path")), "--line", str(point.get("line")), "--quote", str(point.get("quote")),
    "--by", spec["by"], "--side-effects", result["side_effects"], "--at", spec["at"],
  ]
  if args.apply:
    argv.append("--apply")
  done = subprocess.run(argv, text=True, capture_output=True)
  if done.returncode != 0:
    try:
      message = json.loads(done.stderr)["error"]
    except (ValueError, KeyError, TypeError):
      message = "retro-store refused the verdict"
    die(message)
  return json.loads(done.stdout)


def build():
  parser = argparse.ArgumentParser(prog="retro-judge")
  parser.add_argument("--store")
  parser.add_argument("--input", required=True)
  parser.add_argument("--endpoint", default=os.environ.get("RETRO_JUDGE_ENDPOINT"))
  parser.add_argument("--judge-model", default=os.environ.get("RETRO_JUDGE_MODEL"))
  parser.add_argument("--timeout", type=float, default=120.0)
  parser.add_argument("--apply", action="store_true")
  return parser


def main(argv):
  args = build().parse_args(argv)
  config = {"endpoint": args.endpoint, "model": args.judge_model, "timeout": args.timeout}
  try:
    spec = load_input(args.input)
    result, counts = judge(spec, config)
    stored = file_verdict(args, spec, result, counts)
  except ModelFailure as error:
    payload = {"ok": False, "error": str(error), "failure": error.failure, "verdict": None}
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(3)
  emit({"ok": True, "command": "judge", "apply": args.apply, **result, "store": stored})


if __name__ == "__main__":
  main(sys.argv[1:])
