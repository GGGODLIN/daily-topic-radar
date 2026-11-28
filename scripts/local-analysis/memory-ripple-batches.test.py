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
      self.assertEqual(out["malformed_lines"], 0)
      self.assertEqual(out["batches"], [])
      self.assertFalse(out["warn_large"])
      self.assertEqual(out["manifest_path"], str(work / "manifest.json"))

  def test_basename_matching_no_cap_warn_large_and_malformed_count(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      mem = root / "mem"
      mem.mkdir()
      log_lines = ["{not-valid-json"]
      for i in range(7):
        name = f"reference_widget_topic_{i:02d}.md"
        (mem / name).write_text(f"---\nname: widget-{i}\ndescription: widget topic {i}\n---\nshared widget content {i}\n", encoding="utf-8")
        # Real memory-ripple.sh logs only basename `file` (no relative_path)
        log_lines.append(json.dumps({"ts": f"2026-11-28T10:00:{i:02d}+0800", "file": name, "fired": True, "gemini_kind": "state-reversal"}))
      log_lines.append(json.dumps({"ts": "2026-11-28T10:05:00+0800", "file": "reference_widget_topic_00.md", "fired": True, "gemini_kind": "migration"}))
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
      self.assertEqual(out["malformed_lines"], 1)
      self.assertEqual(out["missing_files"], ["deleted_note.md"])
      self.assertTrue(out["warn_large"])
      self.assertEqual(out["cutoff_ts"], "2026-11-28T10:06:00+0800")
      self.assertEqual([b["target_count"] for b in out["batches"]], [3, 3, 1])

      all_items = []
      for b in out["batches"]:
        all_items.extend(json.loads(Path(b["path"]).read_text(encoding="utf-8")))
      self.assertEqual(len(all_items), 7)
      item0 = next(x for x in all_items if x["target_rel"] == "reference_widget_topic_00.md")
      self.assertEqual(item0["event_count"], 2)
      self.assertEqual(item0["kinds"], ["migration", "state-reversal"])
      self.assertEqual(len(item0["siblings_daily"]), 6)

  def test_fail_closed_on_missing_memory_dir_corrupt_state_and_missing_log(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      mem = root / "mem"
      mem.mkdir()
      (mem / "note.md").write_text("hello\n", encoding="utf-8")
      state = root / "state.json"
      log = root / "audit.jsonl"
      log.write_text("", encoding="utf-8")
      work = root / "work"

      # 1. Missing memory dir must fail closed (not report all missing_files)
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T09:00:00+0800"}), encoding="utf-8")
      r_mem = self._run("prepare", "--date", "2026-11-28", "--work-dir", str(work), "--state", str(state), "--log", str(log), "--memory-dir", str(root / "no-such-mem"))
      self.assertNotEqual(r_mem.returncode, 0)
      self.assertIn("memory_dir_unavailable", r_mem.stderr)

      # 2. Corrupt state file must fail closed (not fall back to 2026-09-25)
      state.write_text("{corrupt", encoding="utf-8")
      r_state = self._run("prepare", "--date", "2026-11-28", "--work-dir", str(work), "--state", str(state), "--log", str(log), "--memory-dir", str(mem))
      self.assertNotEqual(r_state.returncode, 0)
      self.assertIn("corrupt_or_unreadable_state_file", r_state.stderr)

      # 3. Existing state with missing audit log must fail closed
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T09:00:00+0800"}), encoding="utf-8")
      r_log = self._run("prepare", "--date", "2026-11-28", "--work-dir", str(work), "--state", str(state), "--log", str(root / "missing-audit.jsonl"), "--memory-dir", str(mem))
      self.assertNotEqual(r_log.returncode, 0)
      self.assertIn("audit_log_missing_with_existing_state", r_log.stderr)

  def test_parity_with_runtime_rank_siblings_on_leaf_and_cjk_targets(self):
    # First test explicit CJK token parity in isolated fixture
    with tempfile.TemporaryDirectory() as tmp:
      mem = Path(tmp)
      (mem / "reference_記憶波及_排程.md").write_text("---\nname: ripple-cjk\ndescription: 記憶波及 排程\n---\n記憶波及 排程 舊規則\n", encoding="utf-8")
      (mem / "feedback_記憶波及_停用.md").write_text("---\nname: ripple-cjk-2\ndescription: 記憶波及\n---\n記憶波及 已停用\n", encoding="utf-8")
      (mem / "_index_test.md").write_text("---\nname: idx\ndescription: 記憶波及 排程\n---\n記憶波及\n", encoding="utf-8")
      _, fcorpus, _ = self.mod.load_corpus(mem, self.mr)
      cjk_target = mem / "reference_記憶波及_排程.md"
      t1, l1, i1 = self.mr.rank_siblings(cjk_target, mem)
      t2, l2, i2 = self.mod.rank_siblings_from_corpus(cjk_target, fcorpus, self.mr)
      self.assertTrue(any(not tok.isascii() for tok in t1), f"expected CJK token, got {t1}")
      self.assertEqual(t1, t2)
      self.assertEqual([(round(s, 6), r) for s, _, r in l1], [(round(s, 6), r) for s, _, r in l2])
      self.assertEqual([(round(s, 6), r) for s, _, r in i1], [(round(s, 6), r) for s, _, r in i2])

    # Then verify evenly-spaced non-index leaf docs on real ~/.claude/memory
    if not REAL_MEMORY_DIR.is_dir():
      self.skipTest("real memory dir not present")
    _, corpus, _ = self.mod.load_corpus(REAL_MEMORY_DIR, self.mr)
    leaves = [doc for doc in corpus if not doc["is_index"]]
    self.assertGreater(len(leaves), 8)
    step = max(1, len(leaves) // 8)
    sample = leaves[::step][:8]
    for doc in sample:
      target = doc["path"]
      t1, l1, i1 = self.mr.rank_siblings(target, REAL_MEMORY_DIR)
      t2, l2, i2 = self.mod.rank_siblings_from_corpus(target, corpus, self.mr)
      self.assertEqual(t1, t2)
      self.assertEqual([(round(s, 6), r) for s, _, r in l1[:6]], [(round(s, 6), r) for s, _, r in l2[:6]])
      self.assertEqual([(round(s, 6), r) for s, _, r in i1[:2]], [(round(s, 6), r) for s, _, r in i2[:2]])
      self.assertEqual(len(l1) + len(i1), len(l2) + len(i2))

  def test_commit_cursor_guards_report_manifest_and_regression(self):
    with tempfile.TemporaryDirectory() as tmp:
      root = Path(tmp)
      state = root / "state.json"
      state.write_text(json.dumps({"last_ripple_ts": "2026-11-28T10:00:00+0800", "extra": 1}), encoding="utf-8")
      manifest = root / "manifest.json"
      manifest.write_text(json.dumps({"cutoff_ts": "2026-11-28T12:00:00+0800"}), encoding="utf-8")
      empty_report = root / "empty.md"
      empty_report.write_text("", encoding="utf-8")
      good_report = root / "good.md"
      good_report.write_text("# Report\n", encoding="utf-8")

      # Empty report refused
      r1 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T12:00:00+0800", "--manifest", str(manifest), "--report", str(empty_report))
      self.assertNotEqual(r1.returncode, 0)

      # Regression refused
      r2 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T09:00:00+0800", "--report", str(good_report))
      self.assertNotEqual(r2.returncode, 0)

      # Manifest cutoff mismatch refused (blocks hallucinated/advanced ts)
      r_bad_ts = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T12:00:01+0800", "--manifest", str(manifest), "--report", str(good_report))
      self.assertNotEqual(r_bad_ts.returncode, 0)
      self.assertIn("ts mismatch against manifest", r_bad_ts.stderr)

      # Valid advance succeeds and preserves extra keys
      r3 = self._run("commit-cursor", "--state", str(state), "--ts", "2026-11-28T12:00:00+0800", "--manifest", str(manifest), "--report", str(good_report))
      self.assertEqual(r3.returncode, 0, r3.stderr)
      saved = json.loads(state.read_text(encoding="utf-8"))
      self.assertEqual(saved["last_ripple_ts"], "2026-11-28T12:00:00+0800")
      self.assertEqual(saved["extra"], 1)


if __name__ == "__main__":
  unittest.main()
