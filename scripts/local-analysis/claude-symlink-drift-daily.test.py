#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("claude-symlink-drift-daily.sh")


class RepairTemplateTest(unittest.TestCase):
  def test_generated_repair_preserves_account_state(self):
    with tempfile.TemporaryDirectory() as temporary:
      home = Path(temporary)
      default = home / ".claude"
      default.mkdir()
      protected = [".claude.json", ".claude.json.backup.fixture", "backups", "policy-limits.json", ".DS_Store", ".last-update-result.json", "mcp-needs-auth-cache.json", "remote-settings.json", "state", "dev-mods", "policy-limits.json.stamp.json"]
      directories = {"backups", "state", "dev-mods"}
      for root in (default, home / ".claude-team-s", home / ".claude-team-p"):
        root.mkdir(exist_ok=True)
        for name in protected + ["shared.txt"]:
          target = root / name
          if name in directories:
            target.mkdir()
            target = target / "account.txt"
          target.write_text(str(root))
      script = home / "scan.sh"
      source = SCRIPT.read_text()
      setting = 'REPO_DIR="/Users/linhancheng/code/social-info"'
      self.assertEqual(source.count(setting), 1)
      script.write_text(source.replace(setting, f'REPO_DIR="{home}"'))
      env = {**os.environ, "HOME": str(home), "LOCAL_ANALYSIS_DATE": "fixture"}
      result = subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True)
      self.assertEqual(result.returncode, 0, result.stderr)
      report = (home / "reports/local-analysis/fixture-symlink-drift.md").read_text()
      self.assertIn("Drift count: **2**", report)
      repair = report.split("```bash\n", 1)[1].split("```", 1)[0]
      result = subprocess.run(["bash", "-e", "-c", repair], env=env, capture_output=True, text=True)
      self.assertEqual(result.returncode, 0, result.stderr)
      for root in (home / ".claude-team-s", home / ".claude-team-p"):
        for name in protected:
          with self.subTest(account=root.name, protected=name):
            target = root / name
            self.assertFalse(target.is_symlink())
            if name in directories:
              target = target / "account.txt"
            self.assertEqual(target.read_text(), str(root))
        self.assertTrue((root / "shared.txt").is_symlink())
        self.assertEqual((root / "shared.txt").resolve(), (default / "shared.txt").resolve())


if __name__ == "__main__":
  unittest.main()
