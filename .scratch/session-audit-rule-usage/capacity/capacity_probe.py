#!/usr/bin/env python3
"""02 容量實測：最長真實分段 ± 常駐規則全文，實送 free 池，逐次記錄。"""

import importlib.util
import json
import sys
import time
from pathlib import Path

HOME = Path.home()
SCRIPT = HOME / "code/social-info/scripts/local-analysis/session-audit.py"
spec = importlib.util.spec_from_file_location("sa", SCRIPT)
sa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sa)

SELF = "755eec5b-070f-4f5a-8a3c-6e4ef7e827b2"
RULE_FILES = [HOME / ".claude/CLAUDE.md", *sorted((HOME / ".claude/rules/common").glob("*.md"))]
RULES_TEXT = "\n\n".join(f"<rule-file path=\"{p.relative_to(HOME / '.claude')}\">\n{p.read_text()}\n</rule-file>" for p in RULE_FILES)
RULE_ASK = (
  "\n另外輸出 rule_tags：陣列，每項 {file, heading, verdict(applied|violated), quote}。"
  "只列本段實際遇到場合的規則；quote 必須是本段原文逐字子字串。下面是當時生效的常駐規則全文：\n"
  "<rules>\n" + RULES_TEXT + "\n</rules>"
)


def candidates():
  root = HOME / ".claude/projects"
  files = [
    p for p in root.rglob("*.jsonl")
    if "-T-skill-up-" not in str(p) and SELF not in str(p) and "subagents" not in p.parts and "workflows" not in p.parts
  ]
  files.sort(key=lambda p: p.stat().st_size, reverse=True)
  return files[:15]


def longest_chunk():
  best = None
  budget = sa.instruction_budget()
  for path in candidates():
    raw = path.read_bytes()
    document = sa.build_document(raw)[0]
    for start, end, text in sa.split_bytes(document.encode(), budget):
      size = end - start
      if best is None or size > best[0]:
        best = (size, path, text)
  return best


def main():
  rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
  key, err = sa.load_key(str(HOME / ".cli-proxy-api/keys.env"))
  if err:
    sys.exit(err)
  err, model = sa.choose_model(str(HOME / ".cli-proxy-api/config.yaml"))
  if err:
    sys.exit(err)
  url = sa.endpoint_for("http://127.0.0.1:8317")
  size, path, chunk = longest_chunk()
  rows = []
  for variant in ("segment-only", "segment+rules"):
    for attempt in range(rounds):
      payload, _piece, _used, _skipped = sa.request_payload(model, "", chunk, [])
      if variant == "segment+rules":
        payload["messages"][0]["content"] += RULE_ASK
        payload["max_tokens"] = sa.OUTPUT_RESERVE
      sent = len(json.dumps(payload).encode())
      started = time.time()
      code, raw = sa.post_json(url, key, payload)
      elapsed = round(time.time() - started, 1)
      row = {"variant": variant, "attempt": attempt + 1, "sent_bytes": sent, "http": code, "elapsed_s": elapsed}
      try:
        obj = json.loads(raw.decode("utf-8", "replace")) if raw else {}
      except json.JSONDecodeError:
        obj = {}
      choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
      usage = obj.get("usage") or {}
      content = (choice.get("message") or {}).get("content") or ""
      parsed = None
      text = content.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
      try:
        parsed = json.loads(text)
      except json.JSONDecodeError:
        pass
      row.update(
        model=obj.get("model"),
        finish_reason=choice.get("finish_reason"),
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
        json_ok=isinstance(parsed, dict),
        rule_tags=len(parsed.get("rule_tags") or []) if isinstance(parsed, dict) else None,
        pass_=code == 200 and choice.get("finish_reason") not in {"length", "max_tokens"} and isinstance(parsed, dict),
      )
      rows.append(row)
      print(json.dumps(row, ensure_ascii=False), flush=True)
  print(json.dumps({"segment_bytes": size, "rules_bytes": len(RULES_TEXT.encode()), "rule_files": len(RULE_FILES), "source_size": path.stat().st_size, "source_is_natural_candidate": True}, ensure_ascii=False))


if __name__ == "__main__":
  main()
