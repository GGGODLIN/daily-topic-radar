#!/usr/bin/env python3
"""Unit and parity tests for memory-ripple-batches.py."""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().with_name("memory-ripple-batches.py")
RUNTIME = Path("~/.claude/hooks/memory-ripple-runtime.py").expanduser()
REAL_MEMORY_DIR = Path("~/.claude/memory").expanduser()


def load_module(name: str, path: Path):
  loader = importlib.machinery.SourceFileLoader(name, str(path))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  mod = importlib.util.module_from_spec(spec)
  loader.exec_module(mod)
  return mod


class MemoryRippleBatchesTest(unittest.TestCase):
  def setUp(self):
    self.mod = load_module("memory_ripple_batches", SCRIPT)
    self.mr = load_module("memory_ripple_runtime", RUNTIME)

  def _run(self, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)

  def test_empty_pending_returns_zero_batches(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      mem = root / "mem"
      mem.mkdir()
      (mem / "reference_alpha_tool.md").write_text("---\nname: alpha\ndescription: alpha tool\n---\nalpha body\n", encoding="utf-8")
      state = root / "state.json"
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T10:00:00+0800"}), encoding="utf-8")
      log = root / "audit.jsonl"
      log.write_text(
        json.dumps({"ts": "2026-11-28T09:00:00+0800", "file": "reference_alpha_tool.md", "fired": True}) + "\n"
        + json.dumps({"ts": "2026-11-28T11:00:00+0800", "file": "reference_alpha_tool.md", "fired": False}) + "\n",
        encoding="utf-8",
      )
      work = root / "work"
      res = self._run("prepare", "--date", "2026-11-28", "--work-dir", str(work), "--state", str(state), "--log", str(log), "--memory-dir", str(mem))
      self.assertEqual(res.returncode, 0, res.stderr)
      out = json.loads(res.stdout)
      self.assertTrue(out["ok"])
      self.assertEqual(out["total_events"], 0)
      self.assertEqual(out["unique_targets"], 0)
      self.assertEqual(out["batches"], [])
      self.assertFalse(out["warn_large"])

  def test_no_cap_and_warn_large_flag(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      mem = root / "mem"
      mem.mkdir()
      log_lines = []
      for i in range(7):
        name = f"reference_widget_topic_{i:02d}.md"
        (mem / name).write_text(f"---\nname: widget-{i}\ndescription: widget topic {i}\n---\nshared widget content {i}\n", encoding="utf-8")
        log_lines.append(json.dumps({"ts": f"2026-11-28T10:00:{i:02d}+0800", "file": name, "relative_path": name, "fired": True, "gemini_kind": "state-reversal"}))
      # Add a duplicate event on item 0 + a missing file
      log_lines.append(json.dumps({"ts": "2026-11-28T10:05:00+0800", "file": "reference_widget_topic_00.md", "relative_path": "reference_widget_topic_00.md", "fired": True, "gemini_kind": "migration"}))
      log_lines.append(json.dumps({"ts": "2026-11-28T10:06:00+0800", "file": "deleted_note.md", "fired": True}))

      state = root / "state.json"
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T09:00:00+0800"}), encoding="utf-8")
      log = root / "audit.jsonl"
      log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
      work = root / "work"

      res = self._run(
        "prepare",
        "--date", "2026-11-28",
        "--work-dir", str(work),
        "--state", str(state),
        "--log", str(log),
        "--memory-dir", str(mem),
        "--batch-size", "3",
        "--warn-threshold", "5",
      )
      self.assertEqual(res.returncode, 0, res.stderr)
      out = json.loads(res.stdout)
      self.assertEqual(out["total_events"], 9)
      self.assertEqual(out["unique_targets"], 7)
      self.assertEqual(out["missing_files"], ["deleted_note.md"])
      self.assertTrue(out["warn_large"])
      self.assertEqual(out["cutoff_ts"], "2026-11-28T10:06:00+0800")
      # 7 items at batch_size=3 -> 3 batches (3, 3, 1), never truncated
      self.assertEqual([b["target_count"] for b in out["batches"]], [3, 3, 1])

      all_items = []
      for b in out["batches"]:
        all_items.extend(json.loads(Path(b["path"]).read_text(encoding="utf-8")))
      self.assertEqual(len(all_items), 7)
      item0 = next(x for x in all_items if x["target_rel"] == "reference_widget_topic_00.md")
      self.assertEqual(item0["event_count"], 2)
      self.assertEqual(item0["kinds"], ["migration", "state-reversal"])
      self.assertEqual(len(item0["siblings_daily"]), 6)

  def test_parity_with_runtime_rank_siblings_on_real_memory(self):
    if not REAL_MEMORY_DIR.is_dir():
      self.skipTest("real memory dir not present")
    _, corpus, _, _ = self.mod.load_corpus(REAL_MEMORY_DIR, self.mr)
    sample = corpus[:8]
    self.assertGreater(len(sample), 0)
    for doc in sample:
      target = doc["path"]
      t1, l1, i1 = self.mr.rank_siblings(target, REAL_MEMORY_DIR)
      t2, l2, i2 = self.mod.rank_siblings_from_corpus(target, corpus, self.mr)
      self.assertEqual(t1, t2)
      self.assertEqual([(round(s, 6), r) for s, _, r in l1[:6]], [(round(s, 6), r) for s, _, r in l2[:6]])
      self.assertEqual([(round(s, 6), r) for s, _, r in i1[:2]], [(round(s, 6), r) for s, _, r in i2[:2]])
      self.assertEqual(len(l1) + len(i1), len(l2) + len(i2))

  def test_commit_cursor_guards_report_and_regression(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      state = root / "state.json"
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T10:00:00+0800", "extra": 1}), encoding="utf-8")
      empty_report = root / "empty.md"
      empty_report.write_text("", encoding="utf-8")
      good_report = root / "good.md"
      good_report.write_text("# Report\n", encoding="utf-8")

      # Empty report refused
      r1 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T12:00:00+0800", "--report", str(empty_report))
      self.assertNotEqual(r1.returncode, 0)

      # Regression refused
      r2 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T09:00:00+0800", "--report", str(good_report))
      self.assertNotEqual(r2.returncode, 0)

      # Valid advance succeeds and preserves extra keys
      r3 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T12:00:00+0800", "--report", str(good_report))
      self.assertEqual(r3.returncode, 0, r3.stderr)
      saved = json.loads(state.read_text(encoding="utf-8"))
      self.assertEqual(saved["last_ripple_ts"], "2026-11-28T12:00:00+0800")
      self.assertEqual(saved["extra"], 1)


if __name__ == "__main__":
  unittest.main()
