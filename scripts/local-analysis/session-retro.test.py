#!/usr/bin/env python3
"""Acceptance tests at the local retrospective CLI seam; fixtures never call agents."""

import copy
import importlib.util
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

  def retro(self):
    spec = importlib.util.spec_from_file_location("session_retro_fixture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

  def composition_agent(self, name, reads, report_path, report, write=True):
    folder = self.root / name
    folder.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, path in enumerate(reads):
      tool_id = f"read-{name}-{index}"
      content = path.read_text(encoding="utf-8")
      rows.append(assistant([{"type": "tool_use", "id": tool_id, "name": "Read",
                             "input": {"file_path": str(path)}}]))
      rows.append(user([{"type": "tool_result", "tool_use_id": tool_id,
                         "content": "\n".join(f"{line}\t{text}" for line, text in enumerate(content.split("\n"), 1))}]))
    dump(report_path, report)
    if write:
      rows.append(assistant([{"type": "tool_use", "id": f"write-{name}", "name": "Write",
                             "input": {"file_path": str(report_path), "content": json.dumps(report)}}]))
      rows.append(user([{"type": "tool_result", "tool_use_id": f"write-{name}", "content": "created"}]))
    (folder / "agent-fixture.jsonl").write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows), encoding="utf-8")
    return folder

  def composition_fixture(self, analyst_reads=True, with_pattern=False):
    retro = self.retro()
    run = self.root / "composition-run"
    (run / "reports").mkdir(parents=True)
    (run / "validated").mkdir()
    plan = {"schema_version": 1, "run_id": run.name, "scope": "all", "sessions": [],
            "models": {}, "concurrency": 1, "focus": "test", "depth": "full",
            "window_start": RECENT, "cutoff": NOW, "references": [], "effort": "high"}
    dump(run / "plan.json", plan)
    dump(run / "canonical.json", {"human": [{"source_line": 1, "event_time": RECENT, "scope": "window", "text": "evidence"}]})
    binding = {"plan": plan}
    for stage in ("reading", "session-merge"):
      dump(run / f"validation-{stage}.json", {"schema_version": 1, "run_id": run.name,
        "stage": stage, "pass": True, "binding": binding, "units": [],
        "counts": {"expected": 0, "passed": 0, "failed": 0}, "reusable_units": []})
    source_path = run / "reports" / "source.json"
    source = {"unit": "source", "session": "cross-session", "status": "complete",
              "findings": [], "patterns": [], "secondary_patterns": [], "dispositions": []}
    if with_pattern:
      source["patterns"] = [{"id": "P1", "source_finding_ids": [], "evidence": [], "counterevidence": "bounded"}]
    dump(source_path, source)
    partition_path = run / "cross-partition.json"
    dump(partition_path, {"plan": str(run / "plan.json"), "source_count": 1,
                          "items": [{"unit": "cross-b01", "input_reports": [str(source_path)], "records": 0}]})
    tracking_path = run / "tracking-context.json"
    dump(tracking_path, {})
    analyst_path = run / "reports" / "cross-b01.json"
    analyst_report = {"unit": "cross-b01", "session": "cross-session", "status": "complete",
                      "patterns": [], "secondary_patterns": [], "dispositions": []}
    if with_pattern:
      analyst_report["patterns"] = [{"id": "cross-b01-P1", "source_finding_ids": [], "evidence": [], "counterevidence": "bounded"}]
    analyst_dir = self.composition_agent("analyst-writer", [partition_path, tracking_path, source_path] if analyst_reads else [], analyst_path, analyst_report)
    if not analyst_reads:
      reader_dir = self.composition_agent("analyst-reader", [partition_path, tracking_path, source_path], analyst_path, analyst_report, write=False)
    else:
      reader_dir = None
    synthesis_path = run / "cross-synthesis-packet.json"
    dump(synthesis_path, {"session": "cross-session", "purpose": "no final approval", "batches": [{
      "unit": "cross-b01", "patterns": analyst_report["patterns"], "dispositions": [], "limitations": []}]})
    partial_path = run / "reports" / "cross-session.json"
    partial = {"unit": "cross-session", "session": "cross-session", "status": "partial",
               "patterns": [], "secondary_patterns": [], "dispositions": [],
               "tracking_observations": [], "tracking_dispositions": []}
    final_dir = self.composition_agent("final-writer", [synthesis_path, tracking_path], partial_path, partial)
    remaining_path = run / "cross-final-remaining.json"
    dump(remaining_path, {"existing_patterns": [], "existing_secondary_patterns": [],
      "pending_candidates": [], "pending_prior_rejections": [], "all_source_ids": []})
    decision_artifacts = []
    for index in range(1, 7):
      unit = f"cross-decide-{index:02d}"
      input_path = run / f"{unit}-input.json"
      report_path = run / "reports" / f"{unit}.json"
      dump(input_path, {"unit": unit, "existing_patterns": [], "existing_secondary_patterns": [], "candidates": []})
      decision_report = {"unit": unit, "status": "complete", "decisions": [], "new_groups": [], "limitations": []}
      directory = self.composition_agent(f"{unit}-writer", [input_path], report_path, decision_report)
      decision_artifacts.append({"unit": unit, "input_path": str(input_path), "report_path": str(report_path),
                                 "transcript_dirs": [str(directory)]})
    taxonomy_input_path = run / "cross-taxonomy-input.json"
    dump(taxonomy_input_path, {"existing_patterns": [], "existing_secondary_patterns": [], "groups": []})
    taxonomy_path = run / "reports" / "cross-taxonomy.json"
    taxonomy_report = {"unit": "cross-taxonomy", "status": "complete", "mapping": [], "definitions": [], "limitations": []}
    taxonomy_dir = self.composition_agent("taxonomy-writer", [taxonomy_input_path], taxonomy_path, taxonomy_report)
    transfer_path = run / "cross-tracking-transfer.json"
    dump(transfer_path, {"tracking_observations": [], "tracking_dispositions": []})
    manifest = {"schema_version": 1, "kind": "cross-composition", "run_id": run.name, "run_path": str(run),
      "plan_path": str(run / "plan.json"), "source_partition": str(partition_path), "tracking_context": str(tracking_path),
      "partial_report": str(partial_path), "output_report": "reports/cross-composed.json",
      "analyst_artifacts": [{"unit": "cross-b01", "input_reports": [str(source_path)],
        "input_packets": [str(partition_path), str(tracking_path)], "report_path": str(analyst_path),
        "transcript_dirs": [str(analyst_dir)] + ([str(reader_dir)] if reader_dir else [])}],
      "semantic_artifacts": {"synthesis_packet": str(synthesis_path), "remaining": str(remaining_path),
        "taxonomy_input": str(taxonomy_input_path), "taxonomy_artifact": {"unit": "cross-taxonomy", "report_path": str(taxonomy_path), "transcript_dirs": [str(taxonomy_dir)]},
        "decision_artifacts": decision_artifacts, "tracking_transfer": str(transfer_path)},
      "final_artifact": {"unit": "cross-session", "report_path": str(partial_path), "transcript_dirs": [str(final_dir)]}}
    manifest_path = run / "composition-manifest.json"
    dump(manifest_path, manifest)
    return retro, run, manifest_path, plan

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

  def test_inventory_estimates_omit_thinking_and_api_request_blobs_and_include_window_counts(self):
    self.source("bloated", [
      user("older human", timestamp=OLD),
      assistant([{"type": "api-request-blob", "text": "B" * 1000000}, {"type": "text", "text": "older answer"}], timestamp=OLD),
      user("recent human", timestamp=RECENT),
      assistant([{"type": "thinking", "thinking": "T" * 1000000}, {"type": "text", "text": "recent answer"}], timestamp=RECENT),
    ])
    _, data = self.inventory()
    day = data["options"]["day"]
    self.assertEqual(day["estimated_pages"], 1)
    self.assertEqual(day["estimated_reader_units"], 1)
    self.assertEqual(day["window_estimated_pages"], 1)
    self.assertEqual(day["window_estimated_reader_units"], 1)
    self.assertEqual(day["default_estimated_pages"], 1)
    self.assertEqual(day["default_window_estimated_pages"], 1)
    self.assertEqual(day["long_sessions"], [])

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
    self.source("old-unknown", [user(promptSource=None, origin={}, timestamp=OLD)])
    self.source("empty-meta", [{"type": "mode", "timestamp": RECENT}, {"type": "system", "timestamp": RECENT}])
    self.source("local-cmd", [
      {"type": "user", "isMeta": True, "timestamp": RECENT, "message": {"role": "user", "content": "<local-command-caveat>local</local-command-caveat>"}},
      {"type": "user", "timestamp": RECENT, "message": {"role": "user", "content": "<command-name>/effort</command-name>"}},
      {"type": "user", "timestamp": RECENT, "message": {"role": "user", "content": "<local-command-stdout>Cancelled</local-command-stdout>"}},
    ])
    inventory, data = self.inventory()
    self.assertIn("empty-meta", {r["session"] for r in data["excluded"]})
    self.assertIn("local-cmd", {r["session"] for r in data["excluded"]})
    self.assertNotIn("empty-meta", {r["session"] for r in data["unknown"]})
    self.assertNotIn("local-cmd", {r["session"] for r in data["unknown"]})
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
    self.assertEqual([u["session"] for u in plan["unknown"]], ["unknown"])
    self.assertNotIn("activity", plan["unknown"][0])
    self.assertNotIn("snapshot", plan["unknown"][0])
    self.assertTrue(plan["limits"]["thinking_omitted"])
    self.assertEqual(plan["page_count"], 2)
    self.assertEqual(plan["models"]["read"], result["models"]["read"])
    self.assertEqual(plan["models"], {"read": "group/gemini-pro:high", "merge": "group/gemini-pro:high", "verify": "gpt-6.1-sol(xhigh)[1m]",
                                      "repair": "devin-plugin/swe-2:high"})
    self.assertEqual(plan["efforts"], {"read": "high", "merge": "high", "verify": "xhigh"})
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
    self.assertEqual(plan["models"], {"read": "read-sol", "merge": "merge-sol", "verify": "verify-sol",
                                      "repair": "devin-plugin/swe-2:high"})
    self.assertEqual(plan["concurrency"], 2)
    self.assertTrue(plan["limits"]["narrow_coverage"])
    self.cli("prepare", "--inventory", inventory, "--out", self.new("bad"), "--window", "day", "--concurrency", "5", code=2)
    self.cli("prepare", "--inventory", inventory, "--out", self.new("bad"), "--window", "last-success", code=2)

  def test_default_window_keeps_older_context_on_demand_but_out_of_evidence(self):
    path, plan = self.prepare([user("rule: never touch migrations", timestamp=OLD), assistant("ok", timestamp=OLD),
                               user("continue", timestamp=OLD), assistant("edited migrations"), user("you broke the rule")])
    self.assertEqual(plan["depth"], "window")
    session = plan["sessions"][0]
    document = Path(session["document"]).read_text()
    context = Path(session["context_document"]).read_text()
    self.assertNotIn("never touch migrations", document)
    self.assertIn("[line=3 timestamp=2026-10-18T01:00:00Z older user]\ncontinue", document)
    self.assertIn("[line=1 timestamp=2026-10-18T01:00:00Z older user]\nrule: never touch migrations", context)
    self.assertNotIn("continue", context)
    packet = json.loads(Path(session["shards"][0]["packet"]).read_text())
    self.assertEqual(packet["context_document"], session["context_document"])
    item = self.stage(path, "reading")["items"][0]
    self.assertEqual(item["context_document"], session["context_document"])
    canonical = json.loads((path.parent / "canonical.json").read_text())
    self.assertEqual([e["source_line"] for e in canonical[session["session"]]], [3, 4, 5])

  def test_full_depth_has_no_context_document(self):
    path, plan = self.prepare([user("old rule", timestamp=OLD), assistant("recent")], depth="full")
    session = plan["sessions"][0]
    self.assertNotIn("context_document", session)
    self.assertIn("old rule", Path(session["document"]).read_text())
    self.assertFalse((Path(session["document"]).parent / "context.txt").exists())
    self.assertNotIn("context_document", self.stage(path, "reading")["items"][0])

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
                               (text, 1, True), ('精確 "錯字"\n下一行', 2, False), ("不可拼接", 2, False)]:
      with self.subTest(quote=quote, line=line):
        report = self.report(item, [{"id": item["unit"] + "-F1", "evidence": [
          {"session": "human", "source_line": line, "quote": quote}]}])
        folder = self.transcript(item, report)
        self.validate(path, "reading", [folder], code=0 if valid else 2)
        if valid:
          normalized = json.loads((path.parent / "validated" / (item["unit"] + ".json")).read_text())
          evidence = normalized["findings"][0]["evidence"][0]
          self.assertEqual(evidence["event_time"], RECENT)
          # A miscopied line is rebound only in the validated copy, and the copy says what it changed.
          self.assertEqual(evidence["source_line"], 2)
          self.assertEqual("correction" in evidence, line != 2)
          self.assertEqual(json.loads(Path(item["report_path"]).read_text())["findings"][0]["evidence"][0]["source_line"], line)

  def test_quote_misses_are_auto_corrected_only_when_unambiguous_and_all_listed(self):
    repeated = "repeat phrase that is long enough"
    path, _ = self.prepare([user(repeated), assistant("filler a"), assistant("filler b"), assistant("filler c"),
                            assistant("filler d"), assistant("ok"), user(repeated), assistant("**bold claim** about the plan")],
                           repair_model="devin-plugin/swe-2:high")
    plan = json.loads(path.read_text())
    self.assertEqual(plan["models"]["repair"], "devin-plugin/swe-2:high")
    item = self.stage(path, "reading")["items"][0]
    unit = item["unit"]
    formatting = {"session": "human", "source_line": 8, "quote": "bold claim about the plan"}
    ambiguous = {"session": "human", "source_line": 4, "quote": repeated}
    short_far = {"session": "human", "source_line": 2, "quote": "ok"}
    report = self.report(item, [{"id": unit + "-F1", "evidence": [formatting, ambiguous, short_far]}])
    self.validate(path, "reading", [self.transcript(item, report)], code=2)
    failed = json.loads((path.parent / "validation-reading.json").read_text())["units"][0]
    self.assertEqual(len(failed["errors"]), 2)
    self.assertEqual([(f["source_line"], f["record_id"]) for f in failed["quote_failures"]], [(4, unit + "-F1"), (2, unit + "-F1")])
    self.assertTrue(all(f["document"] == item["source_document"] and f["offset"] >= 1 and f["limit"] >= 1
                        for f in failed["quote_failures"]))
    repair = self.stage(path, "reading")["failures"][0]
    self.assertEqual(len(repair["quote_failures"]), 2)

    report = self.report(item, [{"id": unit + "-F1", "evidence": [formatting]}])
    result = self.validate(path, "reading", [self.transcript(item, report)])
    self.assertEqual(result["counts"]["auto_corrected"], 1)
    evidence = json.loads((path.parent / "validated" / (unit + ".json")).read_text())["findings"][0]["evidence"][0]
    self.assertEqual(evidence["quote"], "bold claim** about the plan")
    self.assertEqual(evidence["correction"], "format-same-line")
    self.assertEqual(evidence["corrected_from"], {"source_line": 8, "quote": "bold claim about the plan"})

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
        {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "unreviewed"}])))
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
    report["tracking_observations"] = [{"issue_id": "iss-1", "change_id": "chg-1", "outcome": "unreviewed"}]
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder], code=2)
    report["patterns"][0]["counterevidence"] = "bounded counterevidence"
    folder = self.transcript(merge, report, reads=[])
    self.validate(path, "session-merge", [folder])
    cross = self.stage(path, "cross")["items"][0]
    sid = report["patterns"][0]["id"]
    cross_report = self.report(cross, patterns=[{"id": "P1", "source_finding_ids": [sid], "evidence": evidence,
      "counterevidence": "bounded counterevidence"}],
      tracking_observations=[{"issue_id": "iss-1", "change_id": "chg-1", "outcome": "unreviewed"}], dispositions=[])
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

  def test_prepare_redacts_cjk_password_labels_in_plain_and_nested_tool_text(self):
    rows = [
      user("整理連假後工作現況"),
      assistant([
        {"type": "text", "text": "• 初始密碼：leakiampass99\n• 密碼長度：12_SAFE_LEN\n• 忘記密碼：SAFE_RESET_NOTE"},
        {"type": "tool_result", "tool_use_id": "t1", "content": '{"body":"• 初始密碼：`leaknested88`\\n• 帳號：SAFE_ACCOUNT","初始密碼":"leakkey77","key":"leakbareproviderkey9876543210","nested":{"key":"SAFE_SHORT_KEY"}}'},
      ]),
    ]
    _, plan = self.prepare(rows)
    doc = Path(plan["sessions"][0]["document"]).read_text(encoding="utf-8")
    for secret in ("leakiampass99", "leaknested88", "leakkey77", "leakbareproviderkey9876543210"):
      self.assertNotIn(secret, doc)
    for safe in ("12_SAFE_LEN", "SAFE_RESET_NOTE", "SAFE_ACCOUNT", "SAFE_SHORT_KEY"):
      self.assertIn(safe, doc)


  def test_validate_adds_event_time_and_load_judgement_to_tracking_observations(self):
    # Times, keys and verdicts below are written out by hand from the fixture, not read back from the CLI.
    instructions = {"type": "attachment", "timestamp": "2026-10-20T00:50:00Z", "attachment": {
      "type": "instructions", "files": [{"path": "/fixture/RULES.md", "type": "User", "content": "搜尋大目錄時必須排除 node_modules"}]}}
    action = assistant([{"type": "tool_use", "id": "toolu_fixture_X", "name": "Bash", "input": {"command": "ls"}}],
                       timestamp="2026-10-20T01:05:00Z")
    path, plan = self.prepare([instructions, user(), action])
    run = path.parent
    dump(run / "tracking-context.json", {"changes": [
      {"id": "chg-rule", "kind": "rule", "effective_time": "2026-10-20T00:30:00Z", "new_text": "搜尋大目錄時必須排除 node_modules"},
      {"id": "chg-hook", "kind": "hook", "effective_time": "2026-10-20T01:10:00Z"}]})
    evidence = [{"session": "human", "source_line": 3, "quote": "ls", "role": "action", "event_time": "2000-01-01T00:00:00Z"}]
    observations = [{"issue_id": "iss-1", "change_id": change, "outcome": "recurrence", "evidence": evidence}
                    for change in ("chg-rule", "chg-hook", "chg-unknown")]
    stray = {"issue_id": "iss-2", "change_id": "chg-rule", "outcome": "recurrence",
             "evidence": [{"session": "human", "source_line": 999, "quote": "ls", "role": "action", "event_time": "2000-01-01T00:00:00Z"}]}
    observations.append(stray)
    items = self.stage(path, "reading")["items"]
    dirs = [self.transcript(item, self.report(item, tracking_observations=observations)) for item in items]
    self.validate(path, "reading", dirs)
    validated = json.loads((run / "validated" / (items[0]["unit"] + ".json")).read_text())
    by_change = {obs["change_id"]: obs["events"][0] for obs in validated["tracking_observations"][:3]}
    for event in by_change.values():
      self.assertEqual(event["event_key"], "toolu_fixture_X")
      self.assertEqual(event["event_time"], "2026-10-20T01:05:00Z")
    self.assertEqual(by_change["chg-rule"]["loaded"], "loaded")
    self.assertEqual(by_change["chg-rule"]["observation_key"], "toolu_fixture_X|chg-rule")
    self.assertEqual(by_change["chg-hook"]["loaded"], "not_loaded")
    self.assertEqual(by_change["chg-hook"]["basis"], "event_before_effective")
    self.assertEqual(by_change["chg-unknown"]["loaded"], "unchecked")
    self.assertTrue(by_change["chg-unknown"]["reason"])
    # The model-written event_time never survives into the validated copy.
    for obs in validated["tracking_observations"][:3]:
      self.assertEqual(obs["evidence"][0]["event_time"], "2026-10-20T01:05:00Z")
    # Even when the raw time cannot be found, the model's value is dropped instead of kept.
    unresolved = validated["tracking_observations"][3]
    self.assertNotIn("event_time", unresolved["evidence"][0])
    self.assertEqual(unresolved["events"][0]["loaded"], "unchecked")


  def cross_case(self):
    """Reading validated for three small sessions; returns what a cross writer would see."""
    for name, records in CROSS_SESSIONS.items():
      self.source(name, records())
    path, _ = self.prepare()
    items = {item["session"]: item for item in self.stage(path, "reading")["items"]}
    dirs = [self.transcript(items[name], self.report(items[name], tracking_observations=observations))
            for name, observations in CROSS_READINGS.items()]
    self.validate(path, "reading", dirs)
    self.validate(path, "session-merge", [])
    return path, self.stage(path, "cross")["items"][0], {name: item["unit"] for name, item in items.items()}

  def cross_report(self, cross, units, mutate=None):
    report = self.report(cross, patterns=[], secondary_patterns=[], dispositions=[],
                         tracking_observations=copy.deepcopy(CROSS_OUTPUT_OBSERVATIONS),
                         tracking_dispositions=cross_destinations(units))
    if mutate:
      mutate(report)
    return report

  def cross_errors(self, path, cross, report, code=2):
    result = self.validate(path, "cross", [self.transcript(cross, report, reads=[])], code=code)
    return " | ".join(error for unit in result["units"] for error in unit["errors"])

  def test_cross_gives_every_input_observation_exactly_one_destination(self):
    path, cross, units = self.cross_case()
    inputs = fixture_inputs(units)
    report = self.cross_report(cross, units)
    # Reachability witnesses: the fixture must exercise all four destinations, or P1/P2 below prove little.
    self.assertEqual({d["disposition"] for d in report["tracking_dispositions"]},
                     {"kept", "merged", "reclassified", "rejected"})
    self.assertEqual(len(inputs), 8)
    self.assertTrue(oracle_p1(inputs, report["tracking_dispositions"]))
    self.assertTrue(oracle_p2(inputs, report))
    self.cross_errors(path, cross, report, code=0)
    validated = json.loads((path.parent / "validated" / "cross-session.json").read_text())
    self.assertTrue(oracle_p1(inputs, validated["tracking_dispositions"]))
    self.assertTrue(oracle_p2(inputs, validated))

  def test_events_written_by_the_model_on_an_observation_without_evidence_never_survive(self):
    fake = {"observation_key": "toolu_B2|chg-search", "event_key": "toolu_B2", "change_id": "chg-search",
            "event_time": "1900-01-01T00:00:00Z", "loaded": "loaded"}
    path, cross, units = self.cross_case()

    def forge(report):
      forged = report["tracking_observations"][3]
      self.assertEqual(forged["outcome"], "unreviewed")
      del forged["evidence"]
      forged["events"] = [fake]

    # The forged event would be the only thing carrying toolu_B2 into the output; with events re-derived it is absent.
    errors = self.cross_errors(path, cross, self.cross_report(cross, units, forge), code=2)
    self.assertIn("toolu_B2", errors)
    self.assertFalse((path.parent / "validated" / "cross-session.json").exists())
    # Control: the same observation with its evidence in place passes, so the failure above is the forged events.
    self.cross_errors(path, cross, self.cross_report(cross, units), code=0)

  def test_validated_reading_has_no_model_written_events_on_an_unreviewed_observation(self):
    path, _ = self.prepare([user(), call("toolu_F", RECENT)])
    item = self.stage(path, "reading")["items"][0]
    fake = {"observation_key": "toolu_F|chg-1", "event_key": "toolu_F", "change_id": "chg-1",
            "event_time": "1900-01-01T00:00:00Z", "loaded": "loaded"}
    observation = {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "unreviewed", "events": [fake]}
    self.validate(path, "reading", [self.transcript(item, self.report(item, tracking_observations=[observation]))])
    saved = json.loads((path.parent / "validated" / f"{item['unit']}.json").read_text())
    self.assertEqual(saved["tracking_observations"][0]["events"], [])
    self.assertFalse("1900-01-01" in json.dumps(saved))

  def test_cross_rejects_an_input_observation_without_a_destination_and_names_it(self):
    path, cross, units = self.cross_case()
    inputs = fixture_inputs(units)
    for key in inputs:
      def drop(report, key=key):
        report["tracking_dispositions"] = [d for d in report["tracking_dispositions"] if d["observation_key"] != key]
      event_key, change_id = key.split("|")
      with self.subTest(key=key):
        # Calibration: the test's own P1 must fail on the mutated report, or this case proves nothing.
        mutated = self.cross_report(cross, units, drop)
        self.assertFalse(oracle_p1(inputs, mutated["tracking_dispositions"]))
        errors = self.cross_errors(path, cross, mutated)
        self.assertTrue(event_key in errors and change_id in errors, errors)

  def test_cross_rejects_reclassified_or_rejected_without_a_reason(self):
    path, cross, units = self.cross_case()
    inputs = fixture_inputs(units)
    for key, disposition in (("toolu_B2|chg-search", "reclassified"), ("toolu_B3|chg-hook", "rejected")):
      for blank in (None, "", "   "):
        def strip(report, key=key, blank=blank):
          entry = next(d for d in report["tracking_dispositions"] if d["observation_key"] == key)
          if blank is None:
            entry.pop("reason")
          else:
            entry["reason"] = blank
        with self.subTest(key=key, reason=blank):
          mutated = self.cross_report(cross, units, strip)
          self.assertEqual(next(d for d in mutated["tracking_dispositions"] if d["observation_key"] == key)["disposition"], disposition)
          self.assertFalse(oracle_p2(inputs, mutated))
          errors = self.cross_errors(path, cross, mutated)
          self.assertTrue(key.split("|")[0] in errors and "reason" in errors, errors)

  def test_cross_only_merges_the_same_event_and_never_across_events(self):
    path, cross, units = self.cross_case()
    cases = {
      "different event, same rule": ("toolu_B1|chg-search", "toolu_B2|chg-search"),
      "same event, different change": ("toolu_A1|chg-hook", "toolu_A1|chg-search"),
    }
    for label, (key, into) in cases.items():
      def wrong_target(report, key=key, into=into):
        entry = next(d for d in report["tracking_dispositions"] if d["observation_key"] == key)
        entry.update({"disposition": "merged", "merged_into": into})
      with self.subTest(label):
        errors = self.cross_errors(path, cross, self.cross_report(cross, units, wrong_target))
        self.assertTrue(key.split("|")[0] in errors and into.split("|")[0] in errors and "merge" in errors, errors)

  def test_legacy_cross_report_without_per_observation_destinations_is_reported_as_legacy(self):
    path, cross, units = self.cross_case()
    def legacy(report):
      del report["tracking_dispositions"]
    errors = self.cross_errors(path, cross, self.cross_report(cross, units, legacy))
    self.assertIn("legacy", errors)
    self.assertIn("tracking_dispositions", errors)

  def test_cross_rejects_a_second_destination_for_the_same_observation(self):
    path, cross, units = self.cross_case()
    def twice(report):
      report["tracking_dispositions"].append({"observation_key": "toolu_A1|chg-search", "disposition": "rejected", "reason": "also dropped"})
    mutated = self.cross_report(cross, units, twice)
    self.assertFalse(oracle_p1(fixture_inputs(units), mutated["tracking_dispositions"]))
    errors = self.cross_errors(path, cross, mutated)
    self.assertTrue("toolu_A1" in errors and "chg-search" in errors and "more than one" in errors, errors)

  def test_cross_requires_merge_with_every_source_listed_when_an_event_was_reported_twice(self):
    path, cross, units = self.cross_case()
    beta = units["beta"]
    def entry(report):
      return next(d for d in report["tracking_dispositions"] if d["observation_key"] == "toolu_B1|chg-search")
    def as_kept(report):
      for field in ("merged_into", "merged_from"):
        entry(report).pop(field)
      entry(report)["disposition"] = "kept"
    def missing_source(report):
      entry(report)["merged_from"] = [{"unit": beta, "issue_id": "iss-1"}]
    def invented_source(report):
      entry(report)["merged_from"].append({"unit": beta, "issue_id": "iss-9"})
    for label, mutate in (("kept though reported twice", as_kept), ("a source left out", missing_source), ("a source invented", invented_source)):
      with self.subTest(label):
        errors = self.cross_errors(path, cross, self.cross_report(cross, units, mutate))
        self.assertTrue("toolu_B1" in errors and "chg-search" in errors and "merged_from" in errors, errors)

  def test_cross_rejects_merging_an_observation_only_one_input_reported(self):
    path, cross, units = self.cross_case()
    def lone_merge(report):
      next(d for d in report["tracking_dispositions"] if d["observation_key"] == "toolu_C1|chg-search").update(
        {"disposition": "merged", "merged_into": "toolu_C1|chg-search", "merged_from": [{"unit": units["gamma"], "issue_id": "iss-2"}]})
    errors = self.cross_errors(path, cross, self.cross_report(cross, units, lone_merge))
    self.assertTrue("toolu_C1" in errors and "nothing to merge" in errors, errors)

  def test_cross_rejects_an_outcome_that_differs_from_the_input_without_being_reclassified(self):
    path, cross, units = self.cross_case()
    inputs = fixture_inputs(units)
    def silently_changed(report):
      entry = next(d for d in report["tracking_dispositions"] if d["observation_key"] == "toolu_B2|chg-search")
      entry.update({"disposition": "kept"})
      entry.pop("reason"), entry.pop("to_outcome")
    mutated = self.cross_report(cross, units, silently_changed)
    self.assertFalse(oracle_p2(inputs, mutated))
    errors = self.cross_errors(path, cross, mutated)
    self.assertTrue("toolu_B2" in errors and "chg-search" in errors and "outcome" in errors, errors)

  def test_cross_rejects_a_reclassification_that_the_output_does_not_show(self):
    path, cross, units = self.cross_case()
    def wrong_target(report):
      next(d for d in report["tracking_dispositions"] if d["observation_key"] == "toolu_B2|chg-search")["to_outcome"] = "not_applicable"
    errors = self.cross_errors(path, cross, self.cross_report(cross, units, wrong_target))
    self.assertTrue("toolu_B2" in errors and "to_outcome" in errors, errors)

  def test_cross_rejects_a_kept_observation_missing_from_the_output_and_a_rejected_one_still_in_it(self):
    path, cross, units = self.cross_case()
    def gamma_vanishes(report):
      report["tracking_observations"] = [o for o in report["tracking_observations"] if o["evidence"][0]["session"] != "gamma"]
    def rejected_returns(report):
      report["tracking_observations"][1]["evidence"].append({"session": "beta", "source_line": 4, "quote": "ls", "role": "action"})
    for label, mutate, key, wording in (("kept but gone", gamma_vanishes, "toolu_C1", "absent"),
                                         ("rejected but cited", rejected_returns, "toolu_B3", "still cited")):
      with self.subTest(label):
        errors = self.cross_errors(path, cross, self.cross_report(cross, units, mutate))
        self.assertTrue(key in errors and wording in errors, errors)

  def test_cross_rejects_an_unknown_destination_name(self):
    path, cross, units = self.cross_case()
    def odd(report):
      report["tracking_dispositions"][0]["disposition"] = "dropped"
    errors = self.cross_errors(path, cross, self.cross_report(cross, units, odd))
    self.assertTrue("toolu_A1" in errors and "dropped" in errors, errors)

  def test_all_three_stages_derive_the_same_event_key(self):
    path, _ = self.prepare([user("source finding"), call("toolu_F5", RECENT), assistant("x" * 850000)])
    items = self.stage(path, "reading")["items"]
    self.assertEqual(len(items), 2)
    evidence = [{"session": "human", "source_line": 2, "quote": "ls", "role": "action"}]
    observation = {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence", "evidence": evidence}
    ids = [item["unit"] + ":f" for item in items]
    finding = lambda key: {"id": key, "evidence": [{"session": "human", "source_line": 1, "quote": "source finding"}],
                           "counterevidence": "bounded counterevidence"}
    dirs = [self.transcript(item, self.report(item, [finding(key)], tracking_observations=[observation] if n == 0 else []))
            for n, (item, key) in enumerate(zip(items, ids))]
    self.validate(path, "reading", dirs)
    merge = self.stage(path, "session-merge")["items"][0]
    quote = [{"session": "human", "source_line": 1, "quote": "source finding"}]
    pattern = {"id": merge["unit"] + "-P1", "source_finding_ids": ids, "evidence": quote, "counterevidence": "bounded counterevidence"}
    merged = self.report(merge, patterns=[pattern], tracking_observations=[observation],
                         dispositions=[{"source_finding_id": key, "disposition": "merged", "reason": "same cause"} for key in ids])
    self.validate(path, "session-merge", [self.transcript(merge, merged, reads=[])])
    cross = self.stage(path, "cross")["items"][0]
    sid = pattern["id"]
    report = self.report(cross, patterns=[{"id": "P1", "source_finding_ids": [sid], "evidence": quote, "counterevidence": "bounded counterevidence"}],
                         tracking_observations=[observation], dispositions=[{"source_record_id": sid, "disposition": "kept", "reason": "relevant"}],
                         tracking_dispositions=[{"observation_key": "toolu_F5|chg-1", "disposition": "kept"}])
    self.validate(path, "cross", [self.transcript(cross, report, reads=[])])
    keys = {}
    for stage, unit in (("reading", items[0]["unit"]), ("session-merge", merge["unit"]), ("cross", "cross-session")):
      validated = json.loads((path.parent / "validated" / (unit + ".json")).read_text())
      keys[stage] = [event["observation_key"] for obs in validated["tracking_observations"] for event in obs["events"]]
    self.assertEqual(keys, {"reading": ["toolu_F5|chg-1"], "session-merge": ["toolu_F5|chg-1"], "cross": ["toolu_F5|chg-1"]})


  # ---- real-data fixes (ticket 10 findings F1, F4, F9) ------------------------------------

  def test_every_line_of_one_tool_call_gives_the_same_event_time_in_validate(self):
    called, answered = "2026-10-20T01:05:00Z", "2026-10-20T01:07:00Z"
    path, _ = self.prepare([user(), call("toolu_R1", called), result_row("toolu_R1", answered)])
    dump(path.parent / "tracking-context.json", {"changes": [
      {"id": "chg-1", "kind": "hook", "effective_time": "2026-10-20T00:30:00Z"}]})
    observations = [
      {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence",
       "evidence": [{"session": "human", "source_line": 2, "quote": "ls", "role": "action"}]},
      {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence",
       "evidence": [{"session": "human", "source_line": 3, "quote": "output", "role": "action"}]}]
    items = self.stage(path, "reading")["items"]
    self.validate(path, "reading", [self.transcript(item, self.report(item, tracking_observations=observations)) for item in items])
    validated = json.loads((path.parent / "validated" / (items[0]["unit"] + ".json")).read_text())
    for observation in validated["tracking_observations"]:
      event = observation["events"][0]
      self.assertEqual((event["event_key"], event["event_time"], event["source_line"]), ("toolu_R1", called, 2))
      self.assertEqual(observation["evidence"][0]["event_time"], called)
    # The cited line and quote stay as written: the result line is still the evidence the model pointed at.
    self.assertEqual([o["evidence"][0]["source_line"] for o in validated["tracking_observations"]], [2, 3])
    self.assertEqual(validated["tracking_observations"][1]["evidence"][0]["quote"], "output")

  def tracking_errors(self, observations, code=2):
    path, _ = self.prepare([user(), call("toolu_T", RECENT)])
    items = self.stage(path, "reading")["items"]
    dirs = [self.transcript(item, self.report(item, tracking_observations=observations)) for item in items]
    result = self.validate(path, "reading", dirs, code=code)
    return " | ".join(error for unit in result["units"] for error in unit["errors"])

  def test_tracking_observation_without_a_change_id_fails_and_names_which_one(self):
    good = {"issue_id": "iss-ok", "change_id": "chg-1", "outcome": "recurrence",
            "evidence": [{"session": "human", "source_line": 2, "quote": "ls", "role": "action"}]}
    self.tracking_errors([good], code=0)
    for label, mutate in (("null", lambda o: o.update(change_id=None)), ("missing", lambda o: o.pop("change_id")),
                          ("blank", lambda o: o.update(change_id=" "))):
      bad = {**good, "issue_id": "iss-" + label}
      mutate(bad)
      errors = self.tracking_errors([good, bad])
      self.assertIn("tracking_observations[1]", errors, label)
      self.assertIn("iss-" + label, errors, label)
      self.assertIn("change_id", errors, label)
    # An unreviewed observation still has to say which change it is about.
    self.assertIn("change_id", self.tracking_errors([{"issue_id": "iss-u", "change_id": None, "outcome": "unreviewed"}]))

  def test_tracking_observation_needs_structured_evidence_unless_unreviewed(self):
    pointer = {"session": "human", "source_line": 2, "quote": "ls", "role": "action"}
    for outcome in ("recurrence", "opportunity_met", "not_applicable"):
      errors = self.tracking_errors([{"issue_id": "iss-" + outcome, "change_id": "chg-1", "outcome": outcome}])
      self.assertIn("iss-" + outcome, errors)
      self.assertIn("evidence", errors)
    free_text = {"issue_id": "iss-free", "change_id": "chg-1", "outcome": "recurrence", "evidence": "ran ls at line 2"}
    self.assertIn("iss-free", self.tracking_errors([free_text]))
    for label, broken in (("quote", {k: v for k, v in pointer.items() if k != "quote"}),
                          ("source_line", {**pointer, "source_line": "2"}), ("session", {**pointer, "session": ""})):
      errors = self.tracking_errors([{"issue_id": "iss-" + label, "change_id": "chg-1", "outcome": "recurrence", "evidence": [broken]}])
      self.assertIn("iss-" + label, errors, label)
    # Positive controls: the same shapes pass when they are complete, and unreviewed may have no evidence at all.
    self.tracking_errors([{"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence", "evidence": [pointer]},
                          {"issue_id": "iss-2", "change_id": "chg-1", "outcome": "unreviewed"}], code=0)

  def test_session_merge_and_cross_apply_the_same_tracking_checks(self):
    path, _ = self.prepare([user("source finding"), assistant("x" * 850000)])
    evidence = [{"session": "human", "source_line": 1, "quote": "source finding"}]
    seen = {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "unreviewed"}
    ids, dirs = [], []
    for item in self.stage(path, "reading")["items"]:
      ids.append(item["unit"] + ":f")
      finding = {"id": ids[-1], "evidence": evidence, "counterevidence": "bounded counterevidence"}
      dirs.append(self.transcript(item, self.report(item, [finding], tracking_observations=[seen])))
    self.validate(path, "reading", dirs)
    merge = self.stage(path, "session-merge")["items"][0]
    pattern = {"id": merge["unit"] + "-P1", "source_finding_ids": ids, "evidence": evidence, "counterevidence": "bounded counterevidence"}
    merged = [{"source_finding_id": key, "disposition": "merged", "reason": "same cause"} for key in ids]

    def merge_with(observations, code):
      report = self.report(merge, patterns=[pattern], tracking_observations=observations, dispositions=merged)
      result = self.validate(path, "session-merge", [self.transcript(merge, report, reads=[])], code=code)
      return " | ".join(error for unit in result["units"] for error in unit["errors"])

    errors = merge_with([seen, {"issue_id": "iss-null", "change_id": None, "outcome": "unreviewed"}], 2)
    self.assertIn("tracking_observations[1]", errors)
    self.assertIn("iss-null", errors)
    errors = merge_with([seen, {"issue_id": "iss-bare", "change_id": "chg-1", "outcome": "recurrence"}], 2)
    self.assertIn("iss-bare", errors)
    merge_with([seen, {"issue_id": "iss-ok", "change_id": "chg-2", "outcome": "unreviewed"}], 0)
    cross = self.stage(path, "cross")["items"][0]
    cross_pattern = {"id": "P1", "source_finding_ids": [pattern["id"]], "evidence": evidence, "counterevidence": "bounded counterevidence"}
    kept = [{"source_record_id": pattern["id"], "disposition": "kept", "reason": "relevant"}]

    def cross_with(observations, code):
      report = self.report(cross, patterns=[cross_pattern], tracking_observations=observations, dispositions=kept)
      result = self.validate(path, "cross", [self.transcript(cross, report, reads=[])], code=code)
      return " | ".join(error for unit in result["units"] for error in unit["errors"])

    errors = cross_with([seen, {"issue_id": "iss-null", "change_id": None, "outcome": "unreviewed"}], 2)
    self.assertIn("tracking_observations[1]", errors)
    self.assertIn("iss-null", errors)
    self.assertIn("iss-bare", cross_with([seen, {"issue_id": "iss-bare", "change_id": "chg-1", "outcome": "not_applicable"}], 2))
    cross_with([seen, {"issue_id": "iss-ok", "change_id": "chg-2", "outcome": "unreviewed"}], 0)

  def test_an_observation_without_a_change_never_becomes_a_none_key(self):
    path, _ = self.prepare([user(), call("toolu_N", RECENT)])
    dump(path.parent / "tracking-context.json", {"changes": [
      {"id": "chg-1", "kind": "hook", "effective_time": "2026-10-20T00:30:00Z"}]})
    bad = {"issue_id": "iss-null", "change_id": None, "outcome": "recurrence",
           "evidence": [{"session": "human", "source_line": 2, "quote": "ls", "role": "action"}]}
    items = self.stage(path, "reading")["items"]
    dirs = [self.transcript(item, self.report(item, tracking_observations=[bad])) for item in items]
    self.validate(path, "reading", dirs, code=2)
    # A rejected unit leaves no validated copy for a later stage to read an "…|None" key from.
    self.assertFalse((path.parent / "validated" / (items[0]["unit"] + ".json")).exists())
    good = {**bad, "change_id": "chg-1"}
    dirs = [self.transcript(item, self.report(item, tracking_observations=[good])) for item in items]
    self.validate(path, "reading", dirs)
    validated = json.loads((path.parent / "validated" / (items[0]["unit"] + ".json")).read_text())
    self.assertEqual(validated["tracking_observations"][0]["events"][0]["observation_key"], "toolu_N|chg-1")

  # ---- evidence roles (ticket 10 finding F6): only an action is an event, a context supports one ----

  def point(self, session, line, role, quote="ls", **extra):
    return {"session": session, "source_line": line, "quote": quote, "role": role, **extra}

  def test_every_tracking_evidence_needs_a_role_and_the_error_names_which_one(self):
    good = {"issue_id": "iss-ok", "change_id": "chg-1", "outcome": "recurrence", "evidence": [self.point("human", 2, "action")]}
    self.tracking_errors([good], code=0)
    for label, role in (("missing", None), ("unknown", "bystander"), ("cased", "Action"), ("blank", "")):
      bad = {**good, "issue_id": "iss-" + label, "evidence": [self.point("human", 2, "action"), self.point("human", 2, role)]}
      if role is None:
        del bad["evidence"][1]["role"]
      errors = self.tracking_errors([good, bad])
      self.assertIn("tracking_observations[1]", errors, label)
      self.assertIn("iss-" + label, errors, label)
      self.assertIn("evidence[1]", errors, label)
      self.assertIn("role", errors, label)
      self.assertNotIn("evidence[0]", errors, label)
    # The same shape passes with either valid role on the second entry.
    for role in ("action", "context"):
      self.tracking_errors([{**good, "evidence": [self.point("human", 2, "action"), self.point("human", 2, role)]}], code=0)

  def test_an_observation_that_is_not_unreviewed_needs_at_least_one_action(self):
    for outcome in ("recurrence", "opportunity_met", "not_applicable"):
      only_context = {"issue_id": "iss-" + outcome, "change_id": "chg-1", "outcome": outcome, "evidence": [self.point("human", 2, "context")]}
      errors = self.tracking_errors([only_context])
      self.assertIn("iss-" + outcome, errors)
      self.assertIn("at least one evidence with role 'action'", errors)
      # Positive control: adding one action to the same evidence makes it pass.
      with_action = {**only_context, "evidence": [self.point("human", 2, "action"), self.point("human", 2, "context", quote="output")]}
      self.tracking_errors([with_action], code=0)
    # Unreviewed is exempt from the action requirement but not from the role value.
    self.tracking_errors([{"issue_id": "iss-u", "change_id": "chg-1", "outcome": "unreviewed"}], code=0)
    self.assertIn("role", self.tracking_errors([{"issue_id": "iss-u2", "change_id": "chg-1", "outcome": "unreviewed",
                                                 "evidence": [{"session": "human", "source_line": 2, "quote": "ls"}]}]))

  def too_large_session(self):
    """Deidentified 11-04 shape: two blown-up searches, each followed by TaskStop, a narrower retry and a read of the saved output."""
    stamps = {n: f"2026-10-20T01:0{n}:00Z" for n in range(2, 8)}
    path, _ = self.prepare([user(), call("toolu_S1", stamps[2]), call("toolu_STOP1", stamps[3]), call("toolu_NARROW", stamps[4]),
                            call("toolu_S2", stamps[5]), call("toolu_READ", stamps[6]), call("toolu_STOP2", stamps[7])])
    dump(path.parent / "tracking-context.json", {"changes": [
      {"id": "chg-search", "kind": "hook", "effective_time": "2026-10-20T00:30:00Z"},
      {"id": "chg-other", "kind": "hook", "effective_time": "2026-10-20T00:30:00Z"}]})
    return path, stamps

  def test_events_come_only_from_action_evidence_and_context_joins_the_nearest_earlier_action(self):
    path, stamps = self.too_large_session()
    two_actions = {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "recurrence", "evidence": [
      self.point("human", 2, "action"), self.point("human", 3, "context"),
      self.point("human", 4, "context", event_key="toolu_FORGED", event_time="2000-01-01T00:00:00Z"),
      self.point("human", 5, "action"), self.point("human", 6, "context"), self.point("human", 7, "context")]}
    # Both contexts precede every action, so they fall back to that session's first action (line 5), not the last (6).
    context_first = {"issue_id": "iss-2", "change_id": "chg-other", "outcome": "recurrence", "evidence": [
      self.point("human", 3, "context"), self.point("human", 4, "context"), self.point("human", 5, "action"),
      self.point("human", 6, "action")]}
    items = self.stage(path, "reading")["items"]
    self.validate(path, "reading", [self.transcript(item, self.report(item, tracking_observations=[two_actions, context_first]))
                                    for item in items])
    validated = json.loads((path.parent / "validated" / (items[0]["unit"] + ".json")).read_text())
    first, second = validated["tracking_observations"]
    # Six evidence entries, two actions: two events, not six.
    self.assertEqual(len(first["evidence"]), 6)
    self.assertEqual([e["observation_key"] for e in first["events"]], ["toolu_S1|chg-search", "toolu_S2|chg-search"])
    self.assertEqual([e["event_time"] for e in first["events"]], [stamps[2], stamps[5]])
    self.assertEqual([(e["source_line"], e["role"], e.get("event_key")) for e in first["evidence"]],
                     [(2, "action", None), (3, "context", "toolu_S1"), (4, "context", "toolu_S1"),
                      (5, "action", None), (6, "context", "toolu_S2"), (7, "context", "toolu_S2")])
    # A model-written event_key or time on a context never survives; both come from the raw session.
    self.assertEqual(first["evidence"][2]["event_time"], stamps[4])
    self.assertEqual([e["event_key"] for e in first["evidence"] if e["role"] == "context" and e["source_line"] != 4],
                     ["toolu_S1", "toolu_S2", "toolu_S2"])
    self.assertEqual([e["observation_key"] for e in second["events"]], ["toolu_S2|chg-other", "toolu_READ|chg-other"])
    self.assertEqual([e["event_key"] for e in second["evidence"] if e["role"] == "context"], ["toolu_S2", "toolu_S2"])

  def test_a_context_on_the_result_line_of_an_action_joins_that_action_not_the_one_before(self):
    # A tool result resolves to its call's line, so the result of S2 sits on S2's own line, not after it.
    path, _ = self.prepare([user(), call("toolu_S1", RECENT), call("toolu_S2", RECENT), result_row("toolu_S2", RECENT)])
    dump(path.parent / "tracking-context.json", {"changes": [
      {"id": "chg-1", "kind": "hook", "effective_time": "2026-10-20T00:30:00Z"}]})
    observation = {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence", "evidence": [
      self.point("human", 2, "action"), self.point("human", 3, "action"), self.point("human", 4, "context", quote="output")]}
    items = self.stage(path, "reading")["items"]
    self.validate(path, "reading", [self.transcript(item, self.report(item, tracking_observations=[observation])) for item in items])
    validated = json.loads((path.parent / "validated" / (items[0]["unit"] + ".json")).read_text())
    self.assertEqual(validated["tracking_observations"][0]["evidence"][2]["event_key"], "toolu_S2")

  def test_a_context_in_a_session_with_no_action_fails_and_names_which_one(self):
    self.source("alpha", [user(), call("toolu_A1", RECENT)])
    self.source("beta", [user(), call("toolu_B1", RECENT)])
    path, _ = self.prepare()
    items = {item["session"]: item for item in self.stage(path, "reading")["items"]}
    orphan = {"issue_id": "iss-orphan", "change_id": "chg-1", "outcome": "recurrence",
              "evidence": [self.point("alpha", 2, "action"), self.point("beta", 2, "context")]}

    def validate_with(observation, code):
      dirs = [self.transcript(items[name], self.report(items[name], tracking_observations=[observation] if name == "alpha" else []))
              for name in ("alpha", "beta")]
      result = self.validate(path, "reading", dirs, code=code)
      return " | ".join(error for unit in result["units"] for error in unit["errors"])

    errors = validate_with(orphan, 2)
    self.assertIn("iss-orphan", errors)
    self.assertIn("evidence[1]", errors)
    self.assertIn("beta", errors)
    self.assertIn("no action", errors)
    # Positive control: an action in the context's own session gives it a home, and it joins that action's event.
    homed = {**orphan, "evidence": [*orphan["evidence"], self.point("beta", 2, "action")]}
    validate_with(homed, 0)
    validated = json.loads((path.parent / "validated" / (items["alpha"]["unit"] + ".json")).read_text())
    context = validated["tracking_observations"][0]["evidence"][1]
    self.assertEqual((context["session"], context["event_key"]), ("beta", "toolu_B1"))

  def test_all_three_stages_check_roles_and_cross_accounts_per_action_event(self):
    path, _ = self.prepare([user("source finding"), call("toolu_R", RECENT), call("toolu_R2", RECENT), assistant("x" * 850000)])
    items = self.stage(path, "reading")["items"]
    quote = [{"session": "human", "source_line": 1, "quote": "source finding"}]
    seen = {"issue_id": "iss-1", "change_id": "chg-1", "outcome": "recurrence",
            "evidence": [self.point("human", 2, "action"), self.point("human", 3, "context")]}
    ids = [item["unit"] + ":f" for item in items]
    dirs = [self.transcript(item, self.report(item, [{"id": key, "evidence": quote, "counterevidence": "bounded counterevidence"}],
                                              tracking_observations=[seen] if n == 0 else []))
            for n, (item, key) in enumerate(zip(items, ids))]
    self.validate(path, "reading", dirs)
    merge = self.stage(path, "session-merge")["items"][0]
    pattern = {"id": merge["unit"] + "-P1", "source_finding_ids": ids, "evidence": quote, "counterevidence": "bounded counterevidence"}
    merged = [{"source_finding_id": key, "disposition": "merged", "reason": "same cause"} for key in ids]
    bad = {
      "no role": {**seen, "evidence": [{"session": "human", "source_line": 2, "quote": "ls"}]},
      "only context": {**seen, "evidence": [self.point("human", 3, "context")]},
    }

    def merge_with(observation, code):
      report = self.report(merge, patterns=[pattern], tracking_observations=[observation], dispositions=merged)
      result = self.validate(path, "session-merge", [self.transcript(merge, report, reads=[])], code=code)
      return " | ".join(error for unit in result["units"] for error in unit["errors"])

    for label, observation in bad.items():
      self.assertTrue(merge_with(observation, 2), label)
    merge_with(seen, 0)
    cross = self.stage(path, "cross")["items"][0]
    cross_pattern = {"id": "P1", "source_finding_ids": [pattern["id"]], "evidence": quote, "counterevidence": "bounded counterevidence"}
    kept = [{"source_record_id": pattern["id"], "disposition": "kept", "reason": "relevant"}]

    def cross_with(observation, destinations, code):
      report = self.report(cross, patterns=[cross_pattern], tracking_observations=[observation], dispositions=kept,
                           tracking_dispositions=destinations)
      result = self.validate(path, "cross", [self.transcript(cross, report, reads=[])], code=code)
      return " | ".join(error for unit in result["units"] for error in unit["errors"])

    one = [{"observation_key": "toolu_R|chg-1", "disposition": "kept"}]
    for label, observation in bad.items():
      self.assertTrue(cross_with(observation, one, 2), label)
    # One destination for the one action; the context's own call (toolu_R2) owes none and may not be given one.
    cross_with(seen, one, 0)
    validated = json.loads((path.parent / "validated" / "cross-session.json").read_text())
    kept_observation = validated["tracking_observations"][0]
    self.assertEqual([e["observation_key"] for e in kept_observation["events"]], ["toolu_R|chg-1"])
    self.assertEqual([(e["role"], e.get("event_key")) for e in kept_observation["evidence"]], [("action", None), ("context", "toolu_R")])
    extra = one + [{"observation_key": "toolu_R2|chg-1", "disposition": "kept"}]
    self.assertIn("unknown input observation", cross_with(seen, extra, 2))


def inst(text, timestamp, path="/fixture/RULES.md"):
  return {"type": "attachment", "timestamp": timestamp, "attachment": {
    "type": "instructions", "files": [{"path": path, "type": "User", "content": text}]}}


def call(call_id, timestamp):
  return assistant([{"type": "tool_use", "id": call_id, "name": "Bash", "input": {"command": "ls"}}], timestamp=timestamp)


def result_row(call_id, timestamp):
  return {"type": "user", "timestamp": timestamp, "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": call_id, "content": "output"}]}}


def skill_text(name, body, timestamp, meta=True):
  return {"type": "user", "isMeta": meta, "timestamp": timestamp, "message": {"role": "user", "content": [
    {"type": "text", "text": f"Base directory for this skill: /fixture/skills/{name}\n\n{body}"}]}}


def invoked(name, content, timestamp):
  return {"type": "attachment", "timestamp": timestamp, "attachment": {
    "type": "invoked_skills", "skills": [{"name": name, "path": "userSettings:" + name, "content": content}]}}


# Deidentified from the 2026-11-03/04 cross reports: one observation may cite several events, one action can break
# two changes, one event can be reported twice, and the four destinations all occur.
CROSS_SESSIONS = {
  "alpha": lambda: [user(), call("toolu_A1", RECENT), call("toolu_A2", RECENT), call("toolu_A3", RECENT)],
  "beta": lambda: [user(), call("toolu_B1", RECENT), call("toolu_B2", RECENT), call("toolu_B3", RECENT)],
  "gamma": lambda: [user(), call("toolu_C1", RECENT)],
}

CROSS_READINGS = {
  "alpha": [
    {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "recurrence",
     "evidence": [{"session": "alpha", "source_line": 2, "quote": "ls", "role": "action"}, {"session": "alpha", "source_line": 3, "quote": "ls", "role": "action"}]},
    {"issue_id": "iss-1", "change_id": "chg-hook", "outcome": "recurrence",
     "evidence": [{"session": "alpha", "source_line": 2, "quote": "ls", "role": "action"}]},
    {"issue_id": "iss-2", "change_id": "chg-search", "outcome": "not_applicable",
     "evidence": [{"session": "alpha", "source_line": 4, "quote": "ls", "role": "action"}]}],
  "beta": [
    {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "recurrence",
     "evidence": [{"session": "beta", "source_line": 2, "quote": "ls", "role": "action"}]},
    {"issue_id": "iss-4", "change_id": "chg-search", "outcome": "recurrence",
     "evidence": [{"session": "beta", "source_line": 2, "quote": "ls", "role": "action"}]},
    {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "recurrence",
     "evidence": [{"session": "beta", "source_line": 3, "quote": "ls", "role": "action"}]},
    {"issue_id": "iss-3", "change_id": "chg-hook", "outcome": "recurrence",
     "evidence": [{"session": "beta", "source_line": 4, "quote": "ls", "role": "action"}]}],
  "gamma": [
    {"issue_id": "iss-2", "change_id": "chg-search", "outcome": "opportunity_met",
     "evidence": [{"session": "gamma", "source_line": 2, "quote": "ls", "role": "action"}]}],
}

CROSS_OUTPUT_OBSERVATIONS = [
  {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "recurrence",
   "evidence": [{"session": "alpha", "source_line": 2, "quote": "ls", "role": "action"}, {"session": "alpha", "source_line": 3, "quote": "ls", "role": "action"},
                {"session": "beta", "source_line": 2, "quote": "ls", "role": "action"}]},
  {"issue_id": "iss-1", "change_id": "chg-hook", "outcome": "recurrence",
   "evidence": [{"session": "alpha", "source_line": 2, "quote": "ls", "role": "action"}]},
  {"issue_id": "iss-2", "change_id": "chg-search", "outcome": "not_applicable",
   "evidence": [{"session": "alpha", "source_line": 4, "quote": "ls", "role": "action"}]},
  {"issue_id": "iss-1", "change_id": "chg-search", "outcome": "unreviewed",
   "evidence": [{"session": "beta", "source_line": 3, "quote": "ls", "role": "action"}]},
  {"issue_id": "iss-2", "change_id": "chg-search", "outcome": "opportunity_met",
   "evidence": [{"session": "gamma", "source_line": 2, "quote": "ls", "role": "action"}]},
]


def cross_destinations(units):
  beta = units["beta"]
  return [
    {"observation_key": "toolu_A1|chg-search", "disposition": "kept"},
    {"observation_key": "toolu_A2|chg-search", "disposition": "kept"},
    {"observation_key": "toolu_A1|chg-hook", "disposition": "kept"},
    {"observation_key": "toolu_A3|chg-search", "disposition": "kept"},
    {"observation_key": "toolu_B1|chg-search", "disposition": "merged", "merged_into": "toolu_B1|chg-search",
     "merged_from": [{"unit": beta, "issue_id": "iss-1"}, {"unit": beta, "issue_id": "iss-4"}]},
    {"observation_key": "toolu_B2|chg-search", "disposition": "reclassified", "to_outcome": "unreviewed",
     "reason": "the event predates the change taking effect"},
    {"observation_key": "toolu_B3|chg-hook", "disposition": "rejected", "reason": "the hook never ran on this action"},
    {"observation_key": "toolu_C1|chg-search", "disposition": "kept"},
  ]


def fixture_tool_id(session, line):
  record = CROSS_SESSIONS[session]()[line - 1]
  return record["message"]["content"][0]["id"]


def fixture_inputs(units):
  """key -> {"outcomes": set, "sources": set of (unit, issue)}, recomputed only from the fixture above."""
  inputs = {}
  for session, observations in CROSS_READINGS.items():
    for observation in observations:
      for evidence in observation["evidence"]:
        key = f"{fixture_tool_id(session, evidence['source_line'])}|{observation['change_id']}"
        entry = inputs.setdefault(key, {"outcomes": set(), "sources": set()})
        entry["outcomes"].add(observation["outcome"])
        entry["sources"].add((units[session], observation["issue_id"]))
  return inputs


def output_outcomes(report):
  outcomes = {}
  for observation in report["tracking_observations"]:
    for evidence in observation["evidence"]:
      key = f"{fixture_tool_id(evidence['session'], evidence['source_line'])}|{observation['change_id']}"
      outcomes.setdefault(key, set()).add(observation["outcome"])
  return outcomes


def oracle_p1(inputs, destinations):
  keys = [d["observation_key"] for d in destinations]
  return all(keys.count(key) == 1 for key in inputs) and all(key in inputs for key in keys)


def oracle_p2(inputs, report):
  """Wherever the output differs from the input (or the observation is dropped), a nonblank reason exists."""
  outputs = output_outcomes(report)
  for entry in report["tracking_dispositions"]:
    key = entry["observation_key"]
    changed = key not in outputs or outputs[key] != inputs[key]["outcomes"]
    if changed and not (isinstance(entry.get("reason"), str) and entry["reason"].strip()):
      return False
  return True


EFFECTIVE = "2026-11-01T00:00:00Z"
OLD_RULE = "搜尋時不需排除目錄"
NEW_RULE = "搜尋大目錄時必須排除 .git 與 node_modules"
BEFORE = "2026-10-30T00:00:00Z"
AFTER = "2026-11-01T01:00:00Z"
LATER = "2026-11-02T03:04:05Z"


class EventEvidenceCLI(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name).resolve()

  def session(self, name, rows, raw=None):
    path = self.root / f"{name}.jsonl"
    lines = [json.dumps({"sessionId": name, **row}, ensure_ascii=False) for row in rows]
    for position, text in (raw or {}).items():
      lines.insert(position - 1, text)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path

  def judge(self, path, change, line=None, tool_call_id=None, code=0):
    args = ["event-evidence", "--session-file", path, "--change", json.dumps(change)]
    if line is not None:
      args += ["--source-line", line]
    if tool_call_id is not None:
      args += ["--tool-call-id", tool_call_id]
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), *map(str, args)], text=True, capture_output=True)
    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
    return json.loads(result.stdout)

  def change(self, kind, change_id="chg-1", **extra):
    base = {"id": change_id, "kind": kind, "effective_time": EFFECTIVE}
    if kind in ("rule", "skill", "command"):
      base["new_text"] = NEW_RULE
    if kind in ("skill", "command"):
      base["name"] = "demo"
    return {**base, **extra}

  def test_event_key_prefers_tool_call_id_then_session_line(self):
    path = self.session("sess-alpha", [user(timestamp=AFTER), call("toolu_A", LATER), assistant("plain", timestamp=LATER)])
    hook = self.change("hook")
    by_line = self.judge(path, hook, line=2)
    self.assertEqual((by_line["event_key"], by_line["event_key_kind"], by_line["source_line"]), ("toolu_A", "tool_call_id", 2))
    by_id = self.judge(path, hook, line=99, tool_call_id="toolu_A")
    self.assertEqual((by_id["event_key"], by_id["source_line"]), ("toolu_A", 2))
    no_id = self.judge(path, hook, line=3)
    self.assertEqual((no_id["event_key"], no_id["event_key_kind"]), ("sess-alpha:3", "session_line"))
    missing = self.judge(path, hook, tool_call_id="toolu_nope")
    self.assertEqual((missing["loaded"], missing["event_key"]), ("unchecked", "toolu_nope"))
    self.assertTrue(missing["reason"])

  def test_one_action_two_changes_share_event_key_but_not_observation_key(self):
    path = self.session("sess-two", [user(timestamp=AFTER), call("toolu_B", LATER)])
    first = self.judge(path, self.change("hook", "chg-1"), line=2)
    second = self.judge(path, self.change("script", "chg-2"), line=2)
    self.assertEqual(first["event_key"], second["event_key"])
    self.assertEqual(first["observation_key"], "toolu_B|chg-1")
    self.assertEqual(second["observation_key"], "toolu_B|chg-2")

  def test_event_time_comes_from_the_raw_row(self):
    path = self.session("sess-time", [user(timestamp=AFTER), call("toolu_T", "2026-11-02T11:04:05+08:00")])
    result = self.judge(path, self.change("hook"), line=2)
    self.assertEqual(result["event_time"], "2026-11-02T03:04:05Z")

  def test_rule_loaded_only_when_last_instructions_attachment_has_new_text(self):
    rule = self.change("rule")
    loaded = self.session("rule-new", [inst(OLD_RULE, BEFORE), user(timestamp=AFTER), inst(NEW_RULE, AFTER), call("toolu_R", LATER)])
    result = self.judge(loaded, rule, line=4)
    self.assertEqual((result["loaded"], result["basis"], result["load_line"]), ("loaded", "instructions_attachment", 3))
    stale = self.session("rule-old", [inst(OLD_RULE, BEFORE), user(timestamp=AFTER), call("toolu_R", LATER)])
    result = self.judge(stale, rule, line=3)
    self.assertEqual((result["loaded"], result["basis"]), ("not_loaded", "last_load_is_old_version"))
    early = self.session("rule-early", [inst(NEW_RULE, BEFORE), call("toolu_R", LATER)])
    result = self.judge(early, rule, line=2)
    self.assertEqual((result["loaded"], result["basis"]), ("not_loaded", "load_time_outside_effective_window"))
    before = self.session("rule-pre", [inst(NEW_RULE, BEFORE), call("toolu_R", BEFORE)])
    self.assertEqual(self.judge(before, rule, line=2)["basis"], "event_before_effective")

  def test_compact_reinjection_separates_events_before_and_after_it(self):
    rows = [inst(OLD_RULE, BEFORE), call("toolu_E1", AFTER), {"type": "system", "subtype": "compact_boundary", "timestamp": AFTER},
            inst(NEW_RULE, "2026-11-01T02:00:00Z"), call("toolu_E2", LATER)]
    path = self.session("rule-compact", rows)
    rule = self.change("rule")
    self.assertEqual(self.judge(path, rule, line=2)["loaded"], "not_loaded")
    self.assertEqual(self.judge(path, rule, line=5)["loaded"], "loaded")
    # A re-injection of the old text after the new one supersedes it, since only the last injection counts.
    regress = self.session("rule-regress", [inst(NEW_RULE, AFTER), inst(OLD_RULE, "2026-11-01T02:00:00Z"), call("toolu_E3", LATER)])
    self.assertEqual(self.judge(regress, rule, line=3)["loaded"], "not_loaded")

  def test_skill_and_command_use_the_last_invoke_or_reinjection(self):
    skill = self.change("skill")
    rows = [skill_text("demo", OLD_RULE, BEFORE), call("toolu_S1", AFTER), skill_text("demo", NEW_RULE, "2026-11-01T02:00:00Z"),
            call("toolu_S2", LATER), invoked("demo", "stale " + OLD_RULE, "2026-11-02T04:00:00Z"), call("toolu_S3", "2026-11-02T05:00:00Z"),
            skill_text("other", NEW_RULE, "2026-11-02T06:00:00Z"), call("toolu_S4", "2026-11-02T07:00:00Z")]
    path = self.session("skill-flow", rows)
    self.assertEqual(self.judge(path, skill, line=2)["loaded"], "not_loaded")
    result = self.judge(path, skill, line=4)
    self.assertEqual((result["loaded"], result["basis"], result["load_line"]), ("loaded", "skill_invoke", 3))
    self.assertEqual(self.judge(path, skill, line=6)["loaded"], "not_loaded")
    # Another skill carrying the new text must not count as this skill's load.
    self.assertEqual(self.judge(path, skill, line=8)["loaded"], "not_loaded")
    never = self.session("skill-never", [user(timestamp=AFTER), call("toolu_S5", LATER)])
    self.assertEqual(self.judge(never, skill, line=2)["basis"], "never_loaded")
    command = self.change("command")
    marker = {"type": "user", "timestamp": AFTER, "message": {"role": "user", "content": "<command-name>/demo</command-name>"}}
    body = {"type": "user", "isMeta": True, "timestamp": AFTER, "message": {"role": "user", "content": [{"type": "text", "text": NEW_RULE}]}}
    path = self.session("command-flow", [marker, body, call("toolu_C1", LATER)])
    self.assertEqual(self.judge(path, command, line=3)["loaded"], "loaded")
    old_body = {**body, "message": {"role": "user", "content": [{"type": "text", "text": OLD_RULE}]}}
    path = self.session("command-old", [marker, old_body, call("toolu_C2", LATER)])
    self.assertEqual(self.judge(path, command, line=3)["loaded"], "not_loaded")

  def test_command_load_stops_at_the_next_command_instead_of_borrowing_its_expansion(self):
    command = self.change("command")
    text = lambda body: {"type": "user", "isMeta": True, "timestamp": AFTER, "message": {"role": "user", "content": [{"type": "text", "text": body}]}}
    marker = lambda name: {"type": "user", "timestamp": AFTER, "message": {"role": "user", "content": f"<command-name>/{name}</command-name>"}}
    # /demo only ever expanded to the old text; /other later expanded to the new text, which is not /demo's.
    rows = [marker("demo"), text(OLD_RULE), call("toolu_X1", LATER), marker("other"), text(NEW_RULE), call("toolu_X2", "2026-11-02T05:00:00Z")]
    path = self.session("command-borrowed", rows)
    result = self.judge(path, command, line=6)
    self.assertEqual((result["loaded"], result["basis"], result["load_line"]), ("not_loaded", "last_load_is_old_version", 1))
    # Control: the same new text expanded by /demo itself after the other command does count.
    again = self.session("command-reinvoked", rows[:5] + [marker("demo"), text(NEW_RULE), call("toolu_X3", "2026-11-02T05:00:00Z")])
    self.assertEqual(self.judge(again, command, line=8)["loaded"], "loaded")

  def test_hook_and_script_load_follows_effective_time_to_the_second(self):
    for kind in ("hook", "script"):
      exact = self.session(f"{kind}-exact", [user(timestamp=AFTER), call("toolu_H", EFFECTIVE)])
      result = self.judge(exact, self.change(kind), line=2)
      self.assertEqual((result["loaded"], result["basis"], result["load_time"]), ("loaded", "effective_time", EFFECTIVE))
      early = self.session(f"{kind}-early", [user(timestamp=AFTER), call("toolu_H", "2026-10-31T23:59:59Z")])
      result = self.judge(early, self.change(kind), line=2)
      self.assertEqual((result["loaded"], result["basis"]), ("not_loaded", "event_before_effective"))

  def test_unreadable_evidence_is_unchecked_with_a_reason_never_loaded(self):
    rule = self.change("rule")
    good = [inst(NEW_RULE, AFTER), call("toolu_U", LATER)]
    self.assertEqual(self.judge(self.session("u-good", good), rule, line=2)["loaded"], "loaded")
    results = {
      "missing-file": self.judge(self.root / "absent.jsonl", rule, line=2),
      "no-attachment": self.judge(self.session("u-none", [user(timestamp=AFTER), call("toolu_U", LATER)]), rule, line=2),
      "damaged-before": self.judge(self.session("u-damaged", good, raw={1: "{not json"}), rule, line=3),
      "damaged-event": self.judge(self.session("u-event", good, raw={2: "{not json"}), rule, line=2),
      "line-out-of-range": self.judge(self.session("u-range", good), rule, line=40),
      "no-new-text": self.judge(self.session("u-text", good), {**rule, "new_text": ""}, line=2),
    }
    for name, result in results.items():
      self.assertEqual(result["loaded"], "unchecked", name)
      self.assertTrue(result["reason"], name)
    # A damaged line after the event cannot change what was loaded before it.
    after = self.session("u-after", good, raw={3: "{not json"})
    self.assertEqual(self.judge(after, rule, line=2)["loaded"], "loaded")

  def test_every_line_of_one_tool_call_is_one_event_at_the_calls_own_time(self):
    answered = "2026-11-02T03:09:00Z"
    path = self.session("sess-beta", [user(timestamp=AFTER), call("toolu_A", LATER), result_row("toolu_A", answered),
                                      assistant("plain", timestamp="2026-11-02T05:00:00Z")])
    hook = self.change("hook")
    for line in (2, 3):
      found = self.judge(path, hook, line=line)
      self.assertEqual((found["event_key"], found["source_line"], found["event_time"]), ("toolu_A", 2, LATER), line)
    self.assertEqual(self.judge(path, hook, tool_call_id="toolu_A")["event_time"], LATER)
    # Controls: an action with no call keeps the line it was cited on, and a result whose call is missing keeps its own line.
    plain = self.judge(path, hook, line=4)
    self.assertEqual((plain["event_key"], plain["source_line"], plain["event_time"]), ("sess-beta:4", 4, "2026-11-02T05:00:00Z"))
    orphan = self.session("sess-orphan", [user(timestamp=AFTER), result_row("toolu_Z", answered)])
    found = self.judge(orphan, hook, line=2)
    self.assertEqual((found["event_key"], found["source_line"], found["event_time"]), ("toolu_Z", 2, answered))

  def test_event_evidence_needs_a_change_id_and_never_builds_a_none_key(self):
    path = self.session("sess-gamma", [user(timestamp=AFTER), call("toolu_A", LATER)])
    for change in ({"kind": "hook", "effective_time": EFFECTIVE}, {"id": None, "kind": "hook", "effective_time": EFFECTIVE}):
      error = self.judge(path, change, line=2, code=2)["error"]
      self.assertIn("id", error)
      self.assertNotIn("None", error)
    self.assertEqual(self.judge(path, self.change("hook", change_id="chg-ok"), line=2)["observation_key"], "toolu_A|chg-ok")

  def test_malformed_change_is_a_usage_error(self):
    path = self.session("bad-change", [user(timestamp=AFTER)])
    for change, line, word in (({"id": "c", "kind": "mystery", "effective_time": EFFECTIVE}, 1, "kind"),
                               ({"id": "c", "kind": "hook", "effective_time": "tomorrow"}, 1, "timestamp"),
                               (self.change("hook"), None, "source-line")):
      self.assertIn(word, self.judge(path, change, line=line, code=2)["error"])

  def test_cross_composition_manifest_check_requires_a_real_source_chain(self):
    manifest = self.root / "composition.json"
    dump(manifest, {"schema_version": 1, "kind": "cross-composition", "run_id": "run",
                    "source_partition": "partition.json", "analyst_artifacts": [],
                    "semantic_artifacts": [], "transcript_dirs": []})
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "compose-cross", "--manifest", manifest, "--check-only"],
                            text=True, capture_output=True)
    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
    payload = json.loads(result.stdout)
    self.assertIn("composition", payload["error"])
    self.assertNotIn("invalid choice", payload["error"])

  def test_cross_composition_rejects_a_report_without_a_matching_write_receipt(self):
    manifest = self.root / "composition.json"
    dump(manifest, {"schema_version": 1, "kind": "cross-composition", "run_id": "run",
                    "source_partition": "partition.json", "analyst_artifacts": [],
                    "semantic_artifacts": [], "transcript_dirs": []})
    report = self.root / "forged.json"
    dump(report, {"unit": "cross-b01", "session": "cross-session", "status": "complete"})
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "compose-cross", "--manifest", manifest, "--check-only"],
                            text=True, capture_output=True)
    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
    self.assertIn("composition", json.loads(result.stdout)["error"])

  def test_composition_reads_must_come_from_the_report_writer(self):
    fixture = RetroCLI()
    fixture.setUp()
    self.addCleanup(fixture.tmp.cleanup)
    retro, _run, manifest, _plan = fixture.composition_fixture(analyst_reads=False)
    with self.assertRaises(retro.Invalid) as error:
      retro.composition_manifest_chain(manifest)
    self.assertIn("did not fully read", str(error.exception))

  def test_composition_verify_requires_two_critic_seats_and_uses_composed_cross(self):
    fixture = RetroCLI()
    fixture.setUp()
    self.addCleanup(fixture.tmp.cleanup)
    retro, run, manifest, _plan = fixture.composition_fixture()
    retro.composition_check(manifest, write=True)
    plan_path = run / "plan.json"
    verify = fixture.cli("stage-args", "--plan", plan_path, "--stage", "verify")
    items = verify["items"]
    self.assertEqual([item["input_reports"] for item in items], [[str(run / "validated" / "cross-session.json")]] * 2)
    source_report = {"session": "cross-session", "unit": "verify-source", "status": "complete", "findings": []}
    source_dir = fixture.composition_agent("verify-source", [run / "validated" / "cross-session.json"], run / "reports" / "verify-source.json", source_report)
    fixture.validate(plan_path, "verify", [source_dir], code=2)
    parts = []
    for index, item in enumerate(items, 1):
      unit = f"verify-current-p{index}"
      report_path = run / "reports" / f"{unit}.json"
      report = {"session": "cross-session", "unit": unit, "status": "complete", "findings": []}
      directory = fixture.composition_agent(unit, [run / "validated" / "cross-session.json"], report_path, report)
      parts.append({"unit": unit, "report_path": str(report_path), "transcript_dirs": [str(directory)], "pattern_ids": []})
    parts_path = run / "verify-current-parts.json"
    dump(parts_path, {"schema_version": 1, "kind": "verify-current-parts", "run_id": run.name,
                      "unit": "verify-current", "parts": parts})
    result = fixture.cli("validate", "--plan", plan_path, "--stage", "verify", "--critic-parts", parts_path)
    self.assertTrue(result["pass"])
    self.assertTrue((run / "validated" / "verify-current.json").is_file())
    formal = json.loads((run / "validation-verify.json").read_text())
    self.assertTrue(formal["pass"])
    self.assertEqual({row["unit"] for row in formal["units"]}, {"verify-source", "verify-current"})
    (run / "final.md").write_text("Fixture final report", encoding="utf-8")
    self.assertEqual(fixture.cli("finish", "--plan", plan_path)["status"], "complete")
    missing = json.loads(parts_path.read_text())
    missing["parts"][0]["pattern_ids"] = ["missing-pattern"]
    dump(parts_path, missing)
    self.assertEqual(fixture.cli("validate", "--plan", plan_path, "--stage", "verify", "--critic-parts", parts_path, code=2)["pass"], False)
    duplicate = json.loads(parts_path.read_text())
    duplicate["parts"][0]["pattern_ids"] = ["duplicate-pattern"]
    duplicate["parts"][1]["pattern_ids"] = ["duplicate-pattern"]
    dump(parts_path, duplicate)
    self.assertEqual(fixture.cli("validate", "--plan", plan_path, "--stage", "verify", "--critic-parts", parts_path, code=2)["pass"], False)
    no_read = fixture.composition_agent("no-current-read", [], run / "reports" / "no-current-read.json",
                                        {"session": "cross-session", "unit": "verify-current-p1", "status": "complete", "findings": []})
    item = {"session": "cross-session", "unit": "verify-current-p1", "report_path": str(run / "reports" / "no-current-read.json"),
            "input_reports": [str(run / "validated" / "cross-session.json")]}
    self.assertIn("did not read", "; ".join(retro.critic_read_errors(retro.transcript_agents([no_read])[0], item, {"findings": []}, {}, {"sessions": []})))

  def test_composition_critic_slice_and_index_match_full_report_and_require_own_reads(self):
    retro = RetroCLI().retro()
    run = self.root
    cross = run / "cross.json"
    slice_path = run / "slice.json"
    index_path = run / "index.json"
    patterns = [{"id": "P1", "observation": "first", "evidence": ["complete"]}, {"id": "P2", "observation": "second"}]
    dump(cross, {"patterns": patterns})
    dump(slice_path, {"patterns": patterns[:1]})
    index = [{"id": x["id"], "observation": x["observation"], "sessions": 0, "matched_issue_id": None, "current_check_paths": []} for x in patterns]
    dump(index_path, index)
    item = {"unit": "verify-current-p1", "input_reports": [str(cross)], "pattern_ids": ["P1"], "slice_path": str(slice_path), "index_path": str(index_path)}
    self.assertEqual(retro.critic_input_paths(item), [str(slice_path), str(index_path)])
    writer = {"operations": [{"name": "Read", "input": {"file_path": str(path)}, "text": "\n".join(f"{n}\t{line}" for n, line in enumerate(path.read_text().split("\n"), 1))} for path in (slice_path, index_path)]}
    self.assertEqual(retro.critic_read_errors(writer, item, {"findings": []}, {}, {"sessions": []}), [])
    self.assertTrue(retro.critic_read_errors({"operations": []}, item, {"findings": []}, {}, {"sessions": []}))
    dump(slice_path, {"patterns": [{"id": "P1", "observation": "first"}]})
    with self.assertRaisesRegex(retro.Invalid, "slice differs"):
      retro.critic_input_paths(item)
    dump(slice_path, {"patterns": patterns[:1]})
    dump(index_path, index[:1])
    with self.assertRaisesRegex(retro.Invalid, "index differs"):
      retro.critic_input_paths(item)

  def test_composition_critic_parts_preserve_primary_and_current_read_boundaries(self):
    fixture = RetroCLI()
    fixture.setUp()
    self.addCleanup(fixture.tmp.cleanup)
    retro, run, _manifest, _plan = fixture.composition_fixture()
    cross_path = run / "validated" / "cross-session.json"
    dump(cross_path, {"patterns": [{"id": "P1"}], "secondary_patterns": [{"id": "S1"}]})
    current = run / "current.txt"
    current.write_text("current guard\n", encoding="utf-8")
    report = {"findings": [{"pattern_id": "P1", "verdict": "CONFIRMED",
      "evidence": [{"session": "human", "source_line": 1, "quote": "evidence"}],
      "current_evidence": [{"path": str(current), "line": 1, "observation": "guard"}]}]}
    retro.verify_verdicts(report, {"input_reports": [str(cross_path)]}, {"human": [{"source_line": 1, "text": "evidence", "scope": "window", "event_time": RECENT}]})
    document = run / "source.txt"
    document.write_text("evidence\n", encoding="utf-8")
    item = {"unit": "verify-current-p1", "input_reports": [str(cross_path)]}
    plan = {"sessions": [{"session": "human", "document": str(document)}]}
    def writer(paths):
      return {"operations": [{"name": "Read", "input": {"file_path": str(path)}, "text": "\n".join(f"{i}\t{line}" for i, line in enumerate(path.read_text().split("\n"), 1))} for path in paths]}
    own = writer([cross_path, document, current])
    self.assertEqual(retro.critic_read_errors(own, item, report, {}, plan), [])
    self.assertTrue(retro.critic_read_errors(writer([cross_path, document]), item, report, {}, plan))
    current.write_text("changed guard\n", encoding="utf-8")
    self.assertTrue(retro.critic_read_errors(own, item, report, {}, plan))
    report["findings"][0]["current_evidence"] = []
    self.assertTrue(retro.critic_read_errors(own, item, report, {}, plan))
    part_path = run / "reports" / "part.json"
    dump(part_path, {})
    manifest = run / "parts.json"
    part = {"unit": "verify-current-p1", "report_path": str(part_path), "transcript_dirs": [str(fixture.root / "analyst-writer")], "pattern_ids": ["P1"]}
    dump(manifest, {"schema_version": 1, "kind": "verify-current-parts", "run_id": run.name, "unit": "verify-current", "parts": [part, {**part, "pattern_ids": []}]})
    with self.assertRaisesRegex(retro.Invalid, "duplicate critic part unit"):
      retro.verify_parts_context(manifest, run)

  def test_composition_taxonomy_mapping_joins_by_key_and_rejects_duplicate_or_missing_keys(self):
    fixture = RetroCLI()
    fixture.setUp()
    self.addCleanup(fixture.tmp.cleanup)
    retro, _run, manifest, _plan = fixture.composition_fixture()
    chain = retro.composition_manifest_chain(manifest)
    chain["source_records"] = [{"id": "source-1", "evidence": []}]
    chain["partial"] = {"patterns": [], "secondary_patterns": [], "dispositions": []}
    chain["remaining"] = {"pending_prior_rejections": []}
    chain["taxonomy_input"] = {"groups": [
      {"key": "G001", "unit": "cross-decide-01", "target": "group-one"},
      {"key": "G002", "unit": "cross-decide-01", "target": "group-two"}]}
    chain["taxonomy"] = {"mapping": [{"key": "G002", "target": "N2"}, {"key": "G001", "target": "N1"}],
                           "definitions": [{"target": "N1", "observation": "one"}, {"target": "N2", "observation": "two"}]}
    chain["decisions"] = [{"artifact": {"unit": "cross-decide-01"},
      "input": {"candidates": [{"id": "candidate-1", "pending_source_ids": ["source-1"]}]},
      "report": {"decisions": [{"id": "candidate-1", "verdict": "retain_new", "target": "group-one", "reason": "keep"}]}}]
    chain["transfer"] = {"tracking_observations": [], "tracking_dispositions": []}
    output = retro.composition_output(chain)
    self.assertEqual(output["secondary_patterns"][0]["observation"], "one")
    with self.assertRaises(retro.Invalid):
      retro.taxonomy_mapping_by_key(chain["taxonomy_input"]["groups"], [{"key": "G001", "target": "N1"}, {"key": "G001", "target": "N2"}])
    with self.assertRaises(retro.Invalid):
      retro.taxonomy_mapping_by_key(chain["taxonomy_input"]["groups"], [{"key": "G001", "target": "N1"}])


if __name__ == "__main__":
  unittest.main(verbosity=2)
