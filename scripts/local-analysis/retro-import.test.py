#!/usr/bin/env python3
"""CLI fixtures for the two known retrospective cross formats."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("retro-import.py")
CUTOFF = "2026-10-20T04:22:56Z"


def dump(path, value):
  path.parent.mkdir(parents=True, exist_ok=True)
  path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def modern_cross():
  primary = {
    "id": "P1",
    "status": "CONFIRMED",
    "observation": "primary observation",
    "counterevidence": "primary counter",
    "cause_hypothesis": "primary cause",
    "environment_change_candidate": "do not apply",
    "source_finding_ids": ["kept-1"],
    "evidence": [{"session": "session-a", "source_line": 4, "quote": "kept line"}],
  }
  secondary = {
    "id": "S1",
    "observation": "secondary observation",
    "counterevidence": "secondary counter",
    "cause_hypothesis": "secondary cause",
    "environment_change_candidate": "leave it",
    "source_finding_ids": ["merged-1"],
    "evidence": [{"session": "session-b", "source_line": 8, "quote": "merged\nline"}],
  }
  return {
    "status": "complete",
    "patterns": [primary],
    "secondary_patterns": [secondary],
    "dispositions": [
      {"id": "kept-1", "verdict": "kept", "reason": "kept"},
      {"id": "merged-1", "verdict": "merged", "reason": "merged"},
      {"id": "rejected-1", "verdict": "rejected", "reason": "not represented"},
    ],
  }


def legacy_cross():
  return {
    "status": "complete_with_limitations",
    "coverage": {
      "retained_original_finding_ids": 2,
      "source_rejected_original_finding_ids": 1,
      "recommended_source_records": 1,
      "secondary_source_records": 1,
      "unaccounted_original_finding_ids": [],
    },
    "patterns": [
      {
        "id": "P1",
        "title": "legacy primary",
        "status": "resolved",
        "counterevidence": "legacy counter",
        "cause_hypothesis": "legacy cause",
        "environment_change_candidate": "already done",
        "source_records": [{"session": "old", "index": 0}],
      }
    ],
    "secondary_patterns": [
      {
        "id": "S01",
        "session": "old",
        "items": [[0, "-S01-F1", "window", "resolved", "secondary text"]],
        "counterevidence_boundary": "not a shared cause",
      }
    ],
    "rejected": [{"source_finding_ids": ["old-rejected"], "reason": "not a defect"}],
  }


class ImportCLI(unittest.TestCase):
  def setUp(self):
    self.tmp = tempfile.TemporaryDirectory()
    self.addCleanup(self.tmp.cleanup)
    self.root = Path(self.tmp.name)

  def cli(self, *args, code=0):
    result = subprocess.run(
      [sys.executable, "-I", str(SCRIPT), *[str(arg) for arg in args]],
      text=True,
      capture_output=True,
    )
    self.assertEqual(result.returncode, code, result.stdout + result.stderr)
    payload = result.stdout if code == 0 else result.stderr
    self.assertNotIn("Traceback", result.stderr)
    return json.loads(payload)

  def run_dir(self, name, cross, plan, reviews=True, cross_at="reports/cross-session.json"):
    root = self.root / name
    dump(root / cross_at, cross)
    dump(root / plan, {"run_id": name, "cutoff": CUTOFF, "window_start": CUTOFF})
    if reviews:
      finding = {
        "id": "F1",
        "pattern_id": "P1",
        "verdict": "CONFIRMED",
        "evidence": [{"session": "session-a", "quote": "exact quote"}],
        "current_evidence": [{"path": "rule.md", "line": 4, "quote": "still open"}],
        "future_field": {"kept": True},
      }
      dump(root / "reports/verify-source.json", {"findings": [finding]})
      dump(
        root / "reports/verify-current.json",
        {"findings": [{**finding, "verdict": "UNCERTAIN"}]},
      )
    return root

  def test_modern_keeps_every_record_pending_and_reports_unrepresented(self):
    run = self.run_dir("modern", modern_cross(), "plan.json")
    out = self.root / "modern-packet.json"
    summary = self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual([item["id"] for item in packet["discoveries"]], ["modern:P1", "modern:S1"])
    self.assertEqual(packet["events"], [])
    self.assertEqual(packet["run"]["status"], "partial")
    self.assertNotIn("completion", packet)
    self.assertFalse((run / "completion.json").exists())
    originals = modern_cross()["patterns"] + modern_cross()["secondary_patterns"]
    for discovery, original in zip(packet["discoveries"], originals, strict=True):
      self.assertEqual(discovery["verification"], "pending")
      self.assertEqual(discovery["raw"], original)
      self.assertIn("counterevidence", discovery["raw"])
    self.assertEqual(summary["dispositions"]["counts"], {"kept": 1, "merged": 1, "rejected": 1})
    self.assertEqual(summary["dispositions"]["unrepresented_ids"], [])
    self.assertEqual(summary["dispositions"]["rejected_ids"], ["rejected-1"])
    self.assertEqual(packet["issues"][0]["title"], "primary observation")
    self.assertEqual(packet["run_metadata"]["raw_dispositions"], modern_cross()["dispositions"])
    review = packet["discoveries"][0]["raw_reviews"]["reviews"][0]
    self.assertEqual(review["finding"]["verdict"], "CONFIRMED")
    self.assertEqual(review["finding"]["evidence"][0]["quote"], "exact quote")
    self.assertEqual(review["finding"]["current_evidence"][0]["quote"], "still open")
    self.assertEqual(review["finding"]["future_field"], {"kept": True})
    self.assertGreaterEqual(review["line"], 1)
    self.assertEqual(packet["discoveries"][0]["verification"], "pending")
    self.assertEqual(packet["event_proposals"][0]["evidence"][0]["quote"], "exact quote")
    self.assertEqual(len(packet["event_proposals"]), 1)

  def test_legacy_top_level_cross_uses_directory_name_without_run_id(self):
    run = self.root / "legacy-run"
    dump(run / "cross-session.json", legacy_cross())
    dump(run / "input-plan.json", {"cutoff": CUTOFF, "start": CUTOFF})
    out = self.root / "legacy-packet.json"
    summary = self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual(packet["run"]["id"], "legacy-run")
    self.assertEqual(summary["run_id_source"], "directory name")
    self.assertEqual(packet["discoveries"][0]["raw"]["title"], "legacy primary")
    self.assertEqual(packet["discoveries"][1]["raw"]["items"][0][4], "secondary text")
    self.assertTrue(all(item["verification"] == "pending" for item in packet["discoveries"]))
    self.assertEqual(summary["dispositions"]["record_counts"], {"recommended": 1, "secondary": 1})
    self.assertEqual(summary["dispositions"]["finding_counts"], {"retained": 2, "rejected": 1})
    self.assertEqual(packet["issues"][1]["title"], "secondary text")
    self.assertEqual(packet["run_metadata"]["raw_coverage"]["retained_original_finding_ids"], 2)
    self.assertEqual(
      packet["run_metadata"]["raw_rejected"][0]["source_finding_ids"], ["old-rejected"]
    )
    self.assertIn("review file absent", " ".join(summary["limitations"]))

  def test_issue_map_fixes_title_and_leaves_other_patterns_separate(self):
    run = self.run_dir("mapped", modern_cross(), "plan.json", reviews=False)
    issue_map = self.root / "map.json"
    dump(issue_map, {"P1": {"id": "stable-issue", "title": "fixed title"}})
    out = self.root / "mapped-packet.json"
    self.cli("--run", run, "--out", out, "--issue-map", issue_map)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual(packet["issues"][0], {"id": "stable-issue", "title": "fixed title"})
    self.assertEqual(packet["issues"][1]["id"], "imported:mapped:S1")
    self.assertEqual(packet["discoveries"][0]["issue_id"], "stable-issue")

  def test_matched_issue_id_in_record_binds_without_external_map(self):
    cross = modern_cross()
    cross["patterns"][0]["matched_issue_id"] = "prior-issue-1"
    cross["patterns"][0]["matched_issue_title"] = "prior canonical title"
    run = self.run_dir("inline-mapped", cross, "plan.json", reviews=False)
    out = self.root / "inline-mapped-packet.json"
    self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual(packet["issues"][0], {"id": "prior-issue-1", "title": "prior canonical title"})
    self.assertEqual(packet["discoveries"][0]["issue_id"], "prior-issue-1")

  def test_missing_cross_missing_cutoff_and_existing_output_are_rejected(self):
    bare = self.root / "bare"
    bare.mkdir()
    missing = self.cli("--run", bare, "--out", self.root / "unused.json", code=2)
    self.assertIn("missing cross-session.json", missing["error"])
    run = self.run_dir("no-cutoff", modern_cross(), "plan.json", reviews=False)
    (run / "plan.json").write_text("{}\n", encoding="utf-8")
    no_cutoff = self.cli("--run", run, "--out", self.root / "no-cutoff.json", code=2)
    self.assertIn("missing cutoff", no_cutoff["error"])
    self.assertFalse((self.root / "no-cutoff.json").exists())
    existing = self.root / "existing.json"
    existing.write_text("{}\n", encoding="utf-8")
    occupied = self.run_dir("occupied", modern_cross(), "plan.json", reviews=False)
    refused = self.cli("--run", occupied, "--out", existing, code=2)
    self.assertIn("overwrite", refused["error"])
    self.assertEqual(existing.read_text(encoding="utf-8"), "{}\n")

  def test_unknown_secondary_key_is_rejected_rather_than_zero(self):
    cross = modern_cross()
    cross["secondary_findings"] = cross.pop("secondary_patterns")
    run = self.run_dir("renamed", cross, "plan.json", reviews=False)
    out = self.root / "renamed.json"
    self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual(len(packet["discoveries"]), 2)
    alien = dict(cross)
    alien.pop("secondary_findings")
    alien["secondary_notes"] = [{"id": "N1"}]
    other = self.run_dir("alien", alien, "plan.json", reviews=False)
    error = self.cli("--run", other, "--out", self.root / "alien.json", code=2)
    self.assertIn("secondary_patterns", error["error"])
    self.assertFalse((self.root / "alien.json").exists())

  def test_partial_status_does_not_write_completion_or_change_the_run(self):
    run = self.run_dir("partial", modern_cross(), "plan.json", reviews=False)
    before = sorted(path.relative_to(run).as_posix() for path in run.rglob("*"))
    out = self.root / "partial.json"
    summary = self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    after = sorted(path.relative_to(run).as_posix() for path in run.rglob("*"))
    self.assertEqual(before, after)
    self.assertEqual(packet["run"]["status"], "partial")
    self.assertEqual(summary["events"], 0)
    self.assertNotIn("completion", json.dumps(packet))

  def test_line_separator_stays_on_its_file_line_and_naive_cutoff_is_rejected(self):
    cross = modern_cross()
    cross["patterns"][0]["observation"] = "before after"
    run = self.run_dir("lines", cross, "plan.json", reviews=False)
    target = run / "reports" / "cross-session.json"
    target.write_text(target.read_text(encoding="utf-8").replace("\\u2028", " "), encoding="utf-8")
    out = self.root / "lines.json"
    self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertIn(" ", packet["discoveries"][0]["raw"]["observation"])
    self.assertEqual(packet["discoveries"][0]["source"]["quote"], '"P1"')
    naive = self.run_dir("naive", modern_cross(), "plan.json", reviews=False)
    naive.joinpath("plan.json").write_text(
      json.dumps({"run_id": "naive", "cutoff": "2026-10-20T04:22:56"}) + "\n",
      encoding="utf-8",
    )
    error = self.cli("--run", naive, "--out", self.root / "naive.json", code=2)
    self.assertIn("timezone", error["error"])
    self.assertNotIn("Traceback", json.dumps(error))

  def test_empty_candidate_lists_keep_tracking_observations_unconfirmed(self):
    observation = {
      "issue_id": "tracked-issue",
      "change_id": "change-1",
      "outcome": "opportunity_met",
      "evidence": [{"session": "session-a", "source_line": 3, "quote": "did the thing"}],
      "limitations": "one window only",
    }
    cross = {
      "status": "complete",
      "patterns": [],
      "secondary_patterns": [],
      "dispositions": [],
      "tracking_observations": [observation],
    }
    run = self.run_dir("empty", cross, "plan.json", reviews=False)
    out = self.root / "empty.json"
    summary = self.cli("--run", run, "--out", out)
    packet = json.loads(out.read_text(encoding="utf-8"))
    self.assertEqual(packet["discoveries"], [])
    self.assertEqual(packet["issues"], [])
    self.assertEqual(packet["events"], [])
    self.assertEqual(summary["dispositions"]["total"], 0)
    self.assertEqual(summary["dispositions"]["unrepresented_ids"], [])
    self.assertEqual(packet["run_metadata"]["tracking_observations"], [observation])
    self.assertEqual(packet["observation_proposals"], [observation])
    self.assertNotIn("confirmed", json.dumps(packet["observation_proposals"]))


if __name__ == "__main__":
  unittest.main()
