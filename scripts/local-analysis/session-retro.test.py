#!/usr/bin/env python3
"""Acceptance tests at the local retrospective CLI seam; fixtures never call agents."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("session-retro.py")
NOW = "2026-10-20T12:00:00+08:00"
RECENT = "2026-10-20T01:00:00Z"
OLD = "2026-10-18T01:00:00Z"


def user(text="human request", timestamp=RECENT, **fields):
  return {"type": "user", "timestamp": timestamp, "promptSource": "typed",
          "origin": {"kind": "human"}, "message": {"role": "user", "content": text}, **fields}


def assistant(text="answer", timestamp=RECENT, **fields):
  return {"type": "assistant", "timestamp": timestamp,
          "message": {"role": "assistant", "content": text}, **fields}


def dump(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class RetroCLI(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name).resolve()
    self.projects = self.root / "projects"
    self.projects.mkdir()
    self.reports = self.root / "reports"
    self.reports.mkdir()
    self.serial = 0

  def new(self, name):
    self.serial += 1
    return self.root / f"{name}-{self.serial}"

  def cli(self, *args, code=0):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)],
                            text=True, capture_output=True)
    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
    return json.loads(result.stdout)

  def source(self, name, records, project="work"):
    path = self.projects / project / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps({"sessionId": name, **row}, ensure_ascii=False)
                              for row in records) + "\n", encoding="utf-8")
    return path

  def inventory(self):
    out = self.new("inventory")
    result = self.cli("inventory", "--projects-root", self.projects,
                      "--reports-root", self.reports, "--out", out, "--now", NOW)
    self.assertEqual(Path(result["inventory_path"]), out / "inventory.json")
    return out / "inventory.json", json.loads((out / "inventory.json").read_text())

  def prepare(self, records=None, **options):
    if records is not None:
      self.source("human", records)
    inventory, _ = self.inventory()
    out = self.new("run")
    args = ["prepare", "--inventory", inventory, "--out", out, "--window", "day"]
    for key, value in options.items():
      args += ["--" + key.replace("_", "-"), value]
    summary = self.cli(*args)
    self.assertTrue(summary["confirmation_required"])
    plan = json.loads(Path(summary["plan_path"]).read_text())
    self.assertEqual(plan["schema_version"], 1)
    return out / "plan.json", plan

  def stage(self, plan, name, code=0):
    return self.cli("stage-args", "--plan", plan, "--stage", name, code=code)

  def validate(self, plan, stage, transcripts, code=0):
    args = ["validate", "--plan", plan, "--stage", stage]
    for path in transcripts:
      args += ["--transcript-dir", path]
    return self.cli(*args, code=code)

  def report(self, item, findings=None, **extra):
    return {"session": item.get("session", "cross-session"), "unit": item["unit"], "status": "complete",
            "source_document": item.get("source_document"),
            "findings": [] if findings is None else findings, **extra}

  def transcript(self, item, report, omit=(), prefix="", reads=None, write=True, alias=None):
    folder = self.new("transcripts")
    folder.mkdir()
    rows = []
    packet = json.loads(Path(item["packet_path"]).read_text()) if "packet_path" in item else {}
    pages = packet.get("pages", []) + packet.get("request_index_pages", [])
    paths = [Path(page["path"] if isinstance(page, dict) else page) for page in pages]
    if reads is not None:
      paths = [Path(path) for path in reads]
    for n, path in enumerate(paths):
      if str(path) in {str(x) for x in omit}:
        continue
      content = path.read_text()
      # Read ranges can overlap; validation must union actual returned lines, including Unicode separators.
      lines = content.split("\n")
      halfway = max(1, len(lines) // 2)
      for part, (start, chunk) in enumerate([(1, lines[:halfway]), (halfway + 1, lines[halfway:])]):
        tid = f"read-{n}-{part}"
        rows.append(assistant([{"type": "tool_use", "id": tid, "name": "Read",
                                "input": {"file_path": str(path), "offset": start, "limit": len(chunk)}}]))
        text = prefix + "\n".join(f"{start + j}\t{line}" for j, line in enumerate(chunk))
        rows.append(user([{"type": "tool_result", "tool_use_id": tid, "content": text}]))
    report_path = Path(alias) if alias else Path(item["report_path"])
    dump(report_path, report)
    if write:
      rows.append(assistant([{"type": "tool_use", "id": "write", "name": "Write",
                              "input": {"file_path": str(report_path), "content": json.dumps(report)}}]))
      rows.append(user([{"type": "tool_result", "tool_use_id": "write", "content": "File created successfully"}]))
    (folder / "agent-fixture.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    return folder

  def read_pass(self, path, findings=None):
    items = self.stage(path, "reading")["items"]
    dirs = [self.transcript(item, self.report(item, findings)) for item in items]
    self.validate(path, "reading", dirs)
    return items, dirs

  def test_inventory_classifies_whole_sessions_activity_and_aliases(self):
    self.source("typed", [user()])
    self.source("queued", [{"type": "attachment", "timestamp": RECENT,
                            "attachment": {"type": "queued_command", "prompt": "queued human"}}])
    self.source("slash", [user("<command-name>/help</command-name>", promptSource=None)])
    self.source("resumed", [user("sdk setup", promptSource="sdk"), user("human resume")])
    self.source("worker", [user("terminal work", promptSource="sdk", origin={"kind": "terminal-worker"}), user()])
    self.source("sdk", [user(promptSource="sdk"),
                        {"type": "attachment", "timestamp": RECENT,
                         "attachment": {"type": "queued_command", "prompt": "<task-notification>\nhook notice"}}])
    self.source("unknown", [user(promptSource=None, origin={})])
    self.source("eval", [user(isSynthetic=True)])
    self.source("forward", [user("[main] forwarded", cwd=str(self.root / "worker-cwd"))])
    self.source("child", [user(isSidechain=True)])
    self.source("metadata-new", [user(timestamp=OLD), {"type": "file-history-snapshot", "timestamp": RECENT}])
    self.source("future", [user(timestamp="2026-10-20T04:00:01Z")])
    self.source("boundary", [user(timestamp="2026-10-19T12:00:00+08:00")])
    self.source("nested", [user()], project="work/deeper")
    (self.projects / "alias").symlink_to(self.projects / "work", target_is_directory=True)
    inv_path, data = self.inventory()
    self.assertEqual(data["options"]["day"]["sessions_count"], 5)
    self.assertEqual(data["options"]["day"]["forwarded_sessions_count"], 1)
    self.assertEqual(data["options"]["day"]["forwarded_sessions"], ["forward"])
    included = {r["session"] for r in data["sessions"] if r["classification"] == "human"}
    self.assertIn("resumed", included)
    self.assertNotIn("sdk", included)
    self.assertEqual(next(r for r in data["sessions"] if r["session"] == "slash")["slash_only"], True)
    self.assertIn("unknown", {r["session"] for r in data["unknown"]})
    self.assertIn("worker", {r["session"] for r in data["excluded"]})
    self.assertIn("sdk", {r["session"] for r in data["excluded"]})
    self.assertEqual(next(r for r in data["excluded"] if r["session"] == "forward")["project"],
                     str((self.root / "worker-cwd").resolve()))
    self.assertNotIn("nested", {r["session"] for r in data["sessions"]})
    self.assertEqual(data["options"]["day"]["cutoff"], "2026-10-20T04:00:00Z")
    fwd_run = self.new("fwd-run")
    self.cli("prepare", "--inventory", inv_path, "--out", fwd_run, "--window", "day")
    fwd_plan = json.loads((fwd_run / "plan.json").read_text())
    fwd_sess = next(s for s in fwd_plan["sessions"] if s["session"] == "forward")
    self.assertIn("[main] forwarded", Path(fwd_sess["human_context"]).read_text())
    no_fwd_run = self.new("no-fwd-run")
    self.cli("prepare", "--inventory", inv_path, "--out", no_fwd_run, "--window", "day", "--exclude-forwarded")
    no_fwd_plan = json.loads((no_fwd_run / "plan.json").read_text())
    self.assertNotIn("forward", {s["session"] for s in no_fwd_plan["sessions"]})

  def test_queued_block_content_is_classified_and_rendered_as_text(self):
    self.source("queued-blocks", [{"type": "attachment", "timestamp": RECENT,
      "attachment": {"type": "queued_command", "prompt": [{"type": "text", "text": "queued block request"}]}}])
    path, plan = self.prepare()
    self.assertEqual(plan["session_count"], 1)
    document = Path(plan["sessions"][0]["document"]).read_text()
    self.assertIn("queued block request", document)
    self.assertNotIn("{'type': 'text'", document)

  def test_invalid_utf8_line_is_reported_without_losing_other_records(self):
    source = self.source("human", [user("valid before corruption")])
    with source.open("ab") as handle:
      handle.write(b'{"type":"user","text":"\xf0broken"}\n')
      handle.write((json.dumps(assistant("valid after corruption")) + "\n").encode())
    _, data = self.inventory()
    row = next(row for row in data["sessions"] if row["session"] == "human")
    self.assertEqual(row["limitations"], [{"line": 2, "reason": "invalid-utf8"}])
    snapshot = Path(row["snapshot"]).read_text()
    self.assertIn("valid before corruption", snapshot)
    self.assertIn("valid after corruption", snapshot)
    self.assertEqual(len(snapshot.split("\n")), 4)
    self.assertEqual(data["options"]["day"]["sessions_count"], 1)

  def test_last_success_only_accepts_valid_global_completions(self):
    self.source("human", [user()])
    base = {"schema_version": 1, "run_id": "good", "scope": "all", "status": "complete",
            "validation_pass": True, "window_start": OLD, "cutoff": "2026-10-19T00:00:00Z"}
    dump(self.reports / "good/completion.json", base)
    for name, fields in [("project", {"scope": {"projects": ["/work"]}}),
                         ("failed", {"validation_pass": False}), ("partial", {"status": "partial"}),
                         ("schema", {"schema_version": 2})]:
      dump(self.reports / name / "completion.json", {**base, "cutoff": RECENT, **fields})
    _, data = self.inventory()
    self.assertEqual(data["last_success"], "2026-10-19T00:00:00Z")
    (self.reports / "good/completion.json").unlink()
    _, data = self.inventory()
    self.assertIsNone(data["last_success"])
    self.assertIsNone(data["options"]["last-success"])

  def test_prepare_uses_snapshot_redacts_before_paging_and_lists_unknown(self):
    source = self.source("human", [user("before snapshot"), assistant("x" * 239990 + " ghp_" + "a" * 30)])
    self.source("unknown", [user(promptSource=None, origin={})])
    inventory, data = self.inventory()
    source.write_text(json.dumps(user("LIVE_CHANGED")))
    out = self.new("run")
    result = self.cli("prepare", "--inventory", inventory, "--out", out, "--window", "day")
    plan = json.loads(Path(result["plan_path"]).read_text())
    session = plan["sessions"][0]
    document = Path(session["document"]).read_text()
    self.assertIn("before snapshot", document)
    self.assertNotIn("LIVE_CHANGED", document)
    self.assertNotIn("ghp_" + "a" * 30, document)
    self.assertIn("[REDACTED]", document)
    self.assertEqual(plan["unknown"][0]["session"], "unknown")
    self.assertTrue(plan["limits"]["thinking_omitted"])
    self.assertEqual(plan["page_count"], 2)
    self.assertEqual(plan["models"]["read"], result["models"]["read"])
    self.assertEqual(plan["models"], {"read": "gpt-6.1-terra(max)[1m]", "merge": "gpt-6.1-terra(max)[1m]", "verify": "gpt-6.1-sol(xhigh)[1m]"})
    self.assertEqual(plan["efforts"], {"read": "max", "merge": "max", "verify": "xhigh"})
    self.assertIn("[line=1 timestamp=2026-10-20T01:00:00Z window user]", document)
    for page in session["shards"][0]["pages"]:
      self.assertTrue(Path(page["path"]).read_text().endswith("END_PAGE " + page["id"] + "\n"))
    self.assertEqual((Path(data["sessions"][0]["snapshot"]).parent.stat().st_mode & 0o777), 0o700)
    self.cli("prepare", "--inventory", inventory, "--out", out, "--window", "day", code=2)

  def test_prepare_window_models_scope_and_invalid_arguments(self):
    self.source("human", [user("old necessary context", timestamp=OLD), assistant("old response", timestamp=OLD), user("recent")])
    inventory, _ = self.inventory()
    out = self.new("run")
    self.cli("prepare", "--inventory", inventory, "--out", out, "--window", "custom", "--start", OLD,
             "--project", self.projects / "work", "--depth", "window", "--read-model", "read-sol",
             "--merge-model", "merge-sol", "--verify-model", "verify-sol", "--effort", "max", "--concurrency", "2")
    plan = json.loads((out / "plan.json").read_text())
    self.assertNotEqual(plan["scope"], "all")
    self.assertEqual(plan["models"], {"read": "read-sol", "merge": "merge-sol", "verify": "verify-sol"})
    self.assertEqual(plan["concurrency"], 2)
    self.assertTrue(plan["limits"]["narrow_coverage"])
    self.cli("prepare", "--inventory", inventory, "--out", self.new("bad"), "--window", "day", "--concurrency", "5", code=2)
    self.cli("prepare", "--inventory", inventory, "--out", self.new("bad"), "--window", "last-success", code=2)

  def test_reading_requires_every_page_and_index_from_report_writer(self):
    path, plan = self.prepare([user(), assistant("a" * 30000)])
    item = self.stage(path, "reading")["items"][0]
    packet = json.loads(Path(item["packet_path"]).read_text())
    missing = packet["pages"][-1]["path"]
    first = self.transcript(item, self.report(item), omit=[missing])
    result = self.validate(path, "reading", [first], code=2)
    self.assertFalse(result["pass"])
    self.stage(path, "session-merge", code=2)
    # A different agent reading the missing page cannot repair the report writer's coverage.
    unrelated = self.transcript(item, self.report(item), write=False, reads=[Path(missing)])
    self.validate(path, "reading", [first, unrelated], code=2)
    no_index = self.transcript(item, self.report(item), omit=packet["request_index_pages"])
    self.validate(path, "reading", [no_index], code=2)
    good = self.transcript(item, self.report(item))
    self.validate(path, "reading", [good])
    self.assertEqual(self.stage(path, "reading")["items"], [])

  def test_read_receipts_unicode_history_and_quote_normalization(self):
    text = 'Unicode 合法  Output too large history "quoted"'
    path, plan = self.prepare([user(text)])
    item = self.stage(path, "reading")["items"][0]
    evidence = {"session": "human", "source_line": 1, "quote": '\\"quoted\\"'}
    report = self.report(item, [{"id": item["unit"] + ":f1", "evidence": [evidence]}])
    folder = self.transcript(item, report)
    self.validate(path, "reading", [folder])
    normalized = json.loads((path.parent / "validated" / (item["unit"] + ".json")).read_text())
    self.assertEqual(normalized["findings"][0]["evidence"][0]["event_time"], RECENT)
    self.assertEqual(normalized["findings"][0]["evidence"][0]["scope"], "window")
    self.assertNotIn("event_time", json.loads(Path(item["report_path"]).read_text())["findings"][0]["evidence"][0])

  def test_nested_canonical_json_quotes_remain_exact_and_line_bound(self):
    text = '精確 "原句"\n下一行'
    block = {"type": "tool_use", "id": "nested", "name": "Bash",
             "input": {"nested": [{"text": text}], "separate": ["不可", "拼接"]}}
    path, _ = self.prepare([user(), assistant([block])])
    item = self.stage(path, "reading")["items"][0]
    for quote, line, valid in [(text, 2, True), (json.dumps(text, ensure_ascii=False)[1:-1], 2, True),
                               (text, 1, False), ('精確 "錯字"\n下一行', 2, False), ("不可拼接", 2, False)]:
      with self.subTest(quote=quote, line=line):
        report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
          {"session": "human", "source_line": line, "quote": quote}]}])
        folder = self.transcript(item, report)
        self.validate(path, "reading", [folder], code=0 if valid else 2)
        if valid:
          normalized = json.loads((path.parent / "validated" / (item["unit"] + ".json")).read_text())
          self.assertEqual(normalized["findings"][0]["evidence"][0]["event_time"], RECENT)

  def test_json_inside_tool_text_decodes_without_joining_or_rewriting(self):
    encoded = json.dumps({"messages": "先按照你的建議處理", "pieces": ["不能", "拼接"]}, ensure_ascii=True)
    block = {"type": "tool_result", "tool_use_id": "nested", "content": [{"type": "text", "text": encoded}]}
    path, _ = self.prepare([user(), user([block])])
    item = self.stage(path, "reading")["items"][0]
    for quote, valid in [("先按照你的建議處理", True), ("不能拼接", False), ("先按照你的建議修理", False)]:
      report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
        {"session": "human", "source_line": 2, "quote": quote}]}])
      folder = self.transcript(item, report)
      self.validate(path, "reading", [folder], code=0 if valid else 2)

  def bash_transcript(self, item, report, change="", success=True, reads=None):
    folder = self.transcript(item, report, write=False, reads=reads)
    packet = json.loads(Path(item["packet_path"]).read_text())
    count = len(packet["pages"])
    base = {**report, "findings": [], "read_pages": []}
    body = ("import json\nfrom pathlib import Path\n"
      f"root=Path({str(Path(item['report_path']).parent.parent)!r})\ns={item['session']!r}\n"
      "def ev(n,q): return {'session':s,'source_line':n,'quote':q}\n"
      f"r={base!r}\n"
      f"r['read_pages']=[f'{item['unit']}-p{{i:02}}' for i in range(1,{count + 1})]\n"
      f"r['findings'].append({{'id': {item['unit'] + '-F1'!r}, 'evidence': [ev(1,'human request')]}})\n"
      f"p=root/{'reports/' + item['unit'] + '.json'!r}\n"
      "with p.open('x') as f: json.dump(r,f,ensure_ascii=False,indent=2); f.write('\\n')\n")
    body = change(body) if callable(change) else body + change
    rows = [assistant([{"type": "tool_use", "id": "bash", "name": "Bash", "input": {
      "command": "python3 -I - <<'PY'\n" + body + "PY"}}])]
    if success:
      rows.append(user([{"type": "tool_result", "tool_use_id": "bash", "content": "created", "is_error": False}]))
    agent = folder / "agent-fixture.jsonl"
    with agent.open("a") as handle:
      handle.write("\n" + "\n".join(json.dumps(row) for row in rows))
    return folder

  def test_bash_report_literal_assembly_binds_original_read_coverage(self):
    path, _ = self.prepare([user()])
    item = self.stage(path, "reading")["items"][0]
    report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
      {"session": "human", "source_line": 1, "quote": "human request"}]}], read_pages=[item["unit"] + "-p01"])
    folder = self.bash_transcript(item, report)
    self.validate(path, "reading", [folder])

  def test_bash_report_rejects_missing_result_mismatch_target_and_unknown_forms(self):
    path, _ = self.prepare([user()])
    item = self.stage(path, "reading")["items"][0]
    report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
      {"session": "human", "source_line": 1, "quote": "human request"}]}], read_pages=[item["unit"] + "-p01"])
    variants = [dict(success=False), dict(change=lambda b: b.replace("human request", "different")),
      dict(change=lambda b: b.replace("p=root/", "p=root/'wrong'/")),
      dict(change=lambda b: b.replace("r=", "evil()\nr=", 1)),
      dict(change=lambda b: b.replace("r=", "other=unknown\nr=", 1)),
      dict(change="r.clear()\n"), dict(change="evil()\n"), dict(change="for x in unknown: pass\n"), dict(reads=[]),
      dict(change=lambda b: b.replace("json.dump(r,f", "json.dump({},f")),
      dict(change=lambda b: b.replace("r['read_pages']=", "alias=r\nr['read_pages']=").replace("p=root/", "r=alias\np=root/")),
      dict(change=lambda b: b.replace("r['read_pages']=", "r=[]\nr[0]=1\nr['read_pages']="))]
    for options in variants:
      with self.subTest(options=options):
        folder = self.bash_transcript(item, report, **options)
        self.validate(path, "reading", [folder], code=2)

  def test_later_exact_edit_preserves_original_writer_coverage(self):
    path, _ = self.prepare([user("correct quote"), assistant("other")])
    item = self.stage(path, "reading")["items"][0]
    report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
      {"session": "human", "source_line": 2, "quote": "correct quote"}]}])
    original = self.transcript(item, report)
    repair = self.transcript(item, report, write=False, reads=[])
    agent = repair / "agent-fixture.jsonl"
    rows = [assistant([{"type": "tool_use", "id": "edit", "name": "Edit", "input": {
      "file_path": item["report_path"], "old_string": '"source_line": 2', "new_string": '"source_line": 1'}}]),
      user([{"type": "tool_result", "tool_use_id": "edit", "content": "edited"}])]
    agent.write_text("\n".join(json.dumps(row) for row in rows))
    Path(item["report_path"]).write_text(json.dumps(report).replace('"source_line": 2', '"source_line": 1'))
    self.validate(path, "reading", [original, repair])
    validation = json.loads((path.parent / "validation-reading.json").read_text())
    self.assertEqual(validation["units"][0]["agent"], str(original / "agent-fixture.jsonl"))
    rows[0]["message"]["content"][0]["input"]["old_string"] = "not present"
    agent.write_text("\n".join(json.dumps(row) for row in rows))
    self.validate(path, "reading", [original, repair], code=2)

  def test_bad_json_quote_timestamp_null_partial_and_absent_write_fail(self):
    path, _ = self.prepare([user("literal evidence")])
    item = self.stage(path, "reading")["items"][0]
    base = {"id": item["unit"] + ":f", "evidence": [{"session": "human", "source_line": 1, "quote": "literal evidence"}]}
    bad_quote = json.loads(json.dumps(base))
    bad_quote["evidence"][0]["quote"] = "literal fabricated evidence"
    bad_time = json.loads(json.dumps(base))
    bad_time["evidence"][0]["event_time"] = OLD
    for report in [None, self.report(item, status="partial"), self.report(item, findings=[] , status="failed"),
                   self.report(item, [bad_quote]), self.report(item, [bad_time]),
                   {"session": item["session"], "unit": item["unit"], "status": "complete"}]:
      with self.subTest(report=report):
        folder = self.transcript(item, report)
        self.validate(path, "reading", [folder], code=2)
    folder = self.transcript(item, self.report(item))
    Path(item["report_path"]).write_text("{broken")
    self.validate(path, "reading", [folder], code=2)
    folder = self.transcript(item, self.report(item), write=False)
    self.validate(path, "reading", [folder], code=2)
    folder = self.transcript(item, self.report(item))
    agent = folder / "agent-fixture.jsonl"
    rows = [json.loads(x) for x in agent.read_text().split("\n")]
    rows[1]["message"]["content"][0]["is_error"] = True
    agent.write_text("\n".join(json.dumps(x) for x in rows))
    self.validate(path, "reading", [folder], code=2)

  def test_report_write_typo_reuses_real_output_and_binding_rejects_model_change(self):
    path, _ = self.prepare([user()])
    item = self.stage(path, "reading")["items"][0]
    alias = path.parent / "reports" / "mistyped.json"
    folder = self.transcript(item, self.report(item), alias=alias)
    self.validate(path, "reading", [folder])
    self.assertEqual(self.stage(path, "reading")["items"], [])
    plan = json.loads(path.read_text())
    plan["models"]["read"] = "changed-model"
    dump(path, plan)
    self.stage(path, "cross", code=2)
    self.assertEqual(len(self.stage(path, "reading")["items"]), 1)

  def test_merge_cross_dispositions_and_two_critics_required_before_finish(self):
    path, plan = self.prepare([user("source finding"), assistant("x" * 850000)])
    items = self.stage(path, "reading")["items"]
    self.assertEqual(len(items), 2)
    dirs = []
    ids = []
    for item in items:
      ids.append(item["unit"] + ":f")
      finding = {"id": ids[-1], "evidence": [{"session": "human", "source_line": 1, "quote": "source finding"}],
                 "counterevidence": "bounded counterevidence"}
      dirs.append(self.transcript(item, self.report(item, [finding], tracking_observations=[
        {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "not_applicable"}])))
    self.validate(path, "reading", dirs)
    merge = self.stage(path, "session-merge")["items"][0]
    evidence = [{"session": "human", "source_line": 1, "quote": "source finding"}]
    report = self.report(merge, patterns=[{"id": merge["unit"] + "-P1", "source_finding_ids": ids, "evidence": evidence}])
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder], code=2)
    report["dispositions"] = [{"source_finding_id": key, "disposition": "merged", "reason": "same cause"} for key in ids]
    # Dropping tracking_observations or counterevidence must fail validation.
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder], code=2)
    report["tracking_observations"] = [{"issue_id": "iss-1", "change_id": "chg-1", "outcome": "not_applicable"}]
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder], code=2)
    report["patterns"][0]["counterevidence"] = "bounded counterevidence"
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder])
    cross = self.stage(path, "cross")["items"][0]
    sid = report["patterns"][0]["id"]
    cross_report = self.report(cross, patterns=[{"id": "P1", "source_finding_ids": [sid], "evidence": evidence,
      "counterevidence": "bounded counterevidence"}],
      tracking_observations=[{"issue_id": "iss-1", "change_id": "chg-1", "outcome": "not_applicable"}], dispositions=[])
    folder = self.transcript(cross, cross_report, reads=[])
    self.validate(path, "cross", [folder], code=2)
    cross_report["dispositions"] = [{"source_record_id": sid, "disposition": "kept", "reason": "relevant"}]
    folder = self.transcript(cross, cross_report, reads=[])
    self.validate(path, "cross", [folder])
    critics = self.stage(path, "verify")["items"]
    self.assertEqual(len(critics), 2)
    dirs = [self.transcript(critics[0], self.report(critics[0], verdicts=[
      {"pattern_id": "P1", "verdict": "CONFIRMED", "evidence": [{"path": str(path), "quote": "scope"}]}]), reads=[])]
    self.validate(path, "verify", dirs, code=2)
    self.cli("finish", "--plan", path, code=2)
    for critic in critics:
      dirs.append(self.transcript(critic, self.report(critic, verdicts=[
        {"pattern_id": "P1", "verdict": "UNCERTAIN", "evidence": [{"path": str(path), "quote": "scope"}]}]), reads=critic["input_reports"]))
    self.validate(path, "verify", dirs, code=2)
    dirs = []
    for critic in critics:
      verdict = {"pattern_id": "P1", "verdict": "UNCERTAIN", "evidence": evidence}
      dirs.append(self.transcript(critic, self.report(critic, verdicts=[verdict]),
        reads=critic["input_reports"] + critic["source_documents"]))
    self.validate(path, "verify", dirs)
    self.cli("finish", "--plan", path, code=2)
    (path.parent / "final.md").write_text("Verified local report")
    self.cli("finish", "--plan", path)
    completion = json.loads((path.parent / "completion.json").read_text())
    self.assertTrue(completion["validation_pass"])
    self.assertEqual(completion["cutoff"], "2026-10-20T04:00:00Z")

  def test_zero_findings_is_valid_and_missing_inputs_are_not_zero(self):
    path, _ = self.prepare([user()])
    self.read_pass(path)
    self.validate(path, "session-merge", [])
    cross = self.stage(path, "cross")["items"][0]
    empty = self.report(cross, patterns=[], secondary_patterns=[], dispositions=[])
    folder = self.transcript(cross, empty, reads=[])
    selected = cross["input_reports"][0]
    self.validate(path, "cross", [folder])
    self.assertEqual(self.stage(path, "cross")["items"], [])
    Path(selected).unlink()
    self.stage(path, "verify", code=2)

  def test_project_filter_uses_session_cwd_and_stage_specific_effort(self):
    self.source("human", [user(cwd=str(self.root / "actual-project"))])
    inventory, data = self.inventory()
    self.assertIn(str(self.root / "actual-project"), data["options"]["day"]["projects"])
    result = self.cli("prepare", "--inventory", inventory, "--out", self.new("model-run"), "--window", "day",
      "--project", self.root / "actual-project", "--read-effort", "medium", "--merge-effort", "high", "--verify-effort", "max")
    reading = self.stage(Path(result["plan_path"]), "reading")
    self.assertEqual(reading["effort"], "medium")

  def test_project_last_success_uses_matching_scope(self):
    self.source("human", [user()])
    scope = {"projects": [str((self.projects / "work").resolve())], "excluded_sessions": []}
    dump(self.reports / "project/completion.json", {"schema_version": 1, "run_id": "project", "scope": scope,
      "status": "complete", "validation_pass": True, "window_start": OLD, "cutoff": "2026-10-19T00:00:00Z"})
    inventory, _ = self.inventory()
    summary = self.cli("prepare", "--inventory", inventory, "--out", self.new("project-run"),
      "--window", "last-success", "--project", self.projects / "work")
    self.assertEqual(summary["window"]["window_start"], "2026-10-19T00:00:00Z")

  def test_source_parse_gap_blocks_completion_even_with_stage_receipts(self):
    source = self.source("human", [user(), {"type": "assistant", "message": {"content": "missing timestamp"}}])
    with source.open("a") as handle:
      handle.write("{broken\n")
    path, plan = self.prepare()
    self.assertTrue(plan["sessions"][0]["limitations"])
    self.read_pass(path)
    self.validate(path, "session-merge", [])
    cross = self.stage(path, "cross")["items"][0]
    folder = self.transcript(cross, self.report(cross, patterns=[], dispositions=[]), reads=[])
    self.validate(path, "cross", [folder])
    critics = self.stage(path, "verify")["items"]
    dirs = [self.transcript(c, self.report(c, findings=[]), reads=c["input_reports"]) for c in critics]
    self.validate(path, "verify", dirs)
    (path.parent / "final.md").write_text("Report of the readable excerpt only")
    self.cli("finish", "--plan", path, code=2)
    self.assertFalse((path.parent / "completion.json").exists())

  def test_critic_requires_real_source_reads(self):
    path, _ = self.prepare([user("evidence")])
    self.read_pass(path)
    self.validate(path, "session-merge", [])
    cross = self.stage(path, "cross")["items"][0]
    cross_report = self.report(cross, patterns=[{"id": "P1", "source_finding_ids": ["s01-u01-F1"],
      "evidence": [{"session": "human", "source_line": 1, "quote": "evidence"}]}], dispositions=[])
    # Build the preceding valid candidate through the public CLI before probing the critic seam.
    item = {"session": "human", "unit": "s01-u01", "report_path": str(path.parent / "reports/s01-u01.json"),
      "packet_path": str(path.parent / "s01/u01/packet.json")}
    finding = {"id": "s01-u01-F1", "evidence": [{"session": "human", "source_line": 1, "quote": "evidence"}]}
    folder = self.transcript(item, self.report(item, [finding]))
    self.validate(path, "reading", [folder])
    self.validate(path, "session-merge", [])
    cross = self.stage(path, "cross")["items"][0]
    cross_report["dispositions"] = [{"id": "s01-u01-F1", "verdict": "kept", "reason": "supported"}]
    folder = self.transcript(cross, cross_report, reads=[])
    self.validate(path, "cross", [folder])
    critics = self.stage(path, "verify")["items"]
    directories = []
    for critic in critics:
      report = self.report(critic, findings=[{"id": critic["unit"] + "-F1", "pattern_id": "P1", "verdict": "CONFIRMED",
        "evidence": [{"session": "human", "source_line": 1, "quote": "evidence"}], "current_evidence": []}])
      directories.append(self.transcript(critic, report, reads=[]))
    result = self.validate(path, "verify", directories, code=2)
    self.assertEqual(result["counts"]["failed"], 2)

  def test_usage_message_ids_dedup_and_unknown_is_explicit(self):
    folder = self.new("usage")
    folder.mkdir()
    row = assistant([{"type": "tool_use", "id": "tool1", "name": "Read", "input": {}}])
    row["message"].update({"id": "msg1", "usage": {"input_tokens": 10, "output_tokens": 3,
      "cache_read_input_tokens": 7, "cache_creation_input_tokens": 2}})
    final = json.loads(json.dumps(row))
    final["message"]["usage"]["output_tokens"] = 8
    unknown = assistant("no usage")
    unknown["message"]["id"] = "msg2"
    (folder / "agent-one.jsonl").write_text("\n".join(json.dumps(x) for x in [row, final, unknown]))
    result = self.cli("usage", "--transcript-dir", folder, "--transcript-dir", folder)
    self.assertEqual(result["totals"], {"input": 10, "output": 8, "cache_read": 7, "cache_creation": 2})
    self.assertEqual(result["tool_uses"], 1)
    self.assertEqual(result["unknown_messages"], 1)
    self.assertFalse(result["billing"])

  def test_stage_args_writes_embedded_workflow_script_requiring_caller_confirmation(self):
    path, _ = self.prepare([user("source finding"), assistant("x" * 850000)])
    out = self.stage(path, "reading")
    script = Path(out["workflow_script_path"])
    self.assertEqual(script, path.parent.resolve() / "workflow-reading.js")
    self.assertTrue(script.is_file())
    runner = """
import { readFile } from 'node:fs/promises'
const src = (await readFile(process.argv[1], 'utf8')).replace(/^export const meta =/m, 'const meta =')
const fn = new Function('args', 'agent', 'parallel', 'log', `return (async () => {\\n${src}\\n})()`)
const unconfirmed = await fn({}, async () => null, async fns => Promise.all(fns.map(f => f())), () => {})
const calls = []
const confirmed = await fn({ confirmed: true }, async (p, opts) => {
  calls.push(opts.label)
  return { session: 'human', unit: opts.label, status: 'complete', report_path: JSON.parse(p.split('工作單=')[1].split('。輸出只能是')[0]).report_path, limitations: [] }
}, async fns => Promise.all(fns.map(f => f())), () => {})
console.log(JSON.stringify({ unconfirmed, confirmed_ok: confirmed.ok, calls }))
"""
    run = subprocess.run(["node", "--input-type=module", "-e", runner, str(script)], text=True, capture_output=True)
    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
    parsed = json.loads(run.stdout)
    self.assertEqual(parsed["unconfirmed"]["reason"], "confirmation-required")
    self.assertTrue(parsed["confirmed_ok"])
    self.assertEqual(parsed["calls"], ["s01-u01", "s01-u02"])
    gate = subprocess.run([sys.executable, "-I", str(Path.home() / ".claude/hooks/workflow-routing-gate.py")],
                          input=json.dumps({"tool_name": "Workflow", "tool_input": {"scriptPath": str(script), "args": {"confirmed": True}}}),
                          text=True, capture_output=True)
    self.assertEqual(gate.returncode, 0, gate.stdout + gate.stderr)
    self.assertEqual(gate.stdout.strip(), "")
    first_dir = self.transcript(out["items"][0], self.report(out["items"][0]))
    self.validate(path, "reading", [first_dir], code=2)
    refreshed = self.stage(path, "reading")
    rerun = subprocess.run(["node", "--input-type=module", "-e", runner, refreshed["workflow_script_path"]], text=True, capture_output=True)
    self.assertEqual(json.loads(rerun.stdout)["calls"], ["s01-u02"])


if __name__ == "__main__":
  unittest.main(verbosity=2)
