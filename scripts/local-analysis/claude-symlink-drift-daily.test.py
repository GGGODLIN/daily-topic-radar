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
      protected = [".claude.json", ".claude.json.backup.fixture", "backups", "policy-limits.json", ".DS_Store", ".last-update-result.json", "mcp-needs-auth-cache.json", "remote-settings.json", "state", "dev-mods", "policy-limits.json.stamp.json", "stats-cache.json"]
      signatures = [
        "policy-limits.json.signature-iat.json",
        "policy-limits.json.signature.json",
        "remote-settings.json.signature-iat.json",
        "remote-settings.json.signature.json",
      ]
      protected += signatures
      repairable = [
        "shared.txt",
        "policy-limits.json.signature-iat.json.backup",
        "policy-limits.json.signature.json.backup",
        "remote-settings.json.signature-iat.json.backup",
        "remote-settings.json.signature.json.backup",
      ]
      directories = {"backups", "state", "dev-mods"}
      for root in (default, home / ".claude-team-s", home / ".claude-team-p"):
        root.mkdir(exist_ok=True)
        for name in protected + repairable:
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
      self.assertIn("Drift count: **10**", report)
      whitelist = report.split("## 結果", 1)[0]
      for name in signatures:
        self.assertIn(f"`{name}`", whitelist)
      for root in (home / ".claude-team-s", home / ".claude-team-p"):
        for name in signatures:
          self.assertNotIn(f"`{root / name}`", report)
        for name in repairable:
          self.assertIn(f"`{root / name}` (file)", report)
      for root in (home / ".claude-team-s", home / ".claude-team-p"):
        cache = root / "stats-cache.json"
        self.assertFalse(cache.is_symlink())
        self.assertEqual(cache.read_text(), str(root))
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
        for name in repairable:
          with self.subTest(account=root.name, repairable=name):
            self.assertTrue((root / name).is_symlink())
            self.assertEqual((root / name).resolve(), (default / name).resolve())


if __name__ == "__main__":
  unittest.main()
