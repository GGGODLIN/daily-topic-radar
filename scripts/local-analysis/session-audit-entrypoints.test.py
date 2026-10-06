import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DIR = Path(__file__).resolve().parent
DATE = '2026-10-05'


class ReportEntrypointTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.state = self.root / 'state'
    self.out_dir = self.root / 'reports'
    self.out = self.out_dir / f'{DATE}-session-audit.md'
    self.env = {
      **os.environ,
      'SESSION_AUDIT_STATE': str(self.state),
      'SESSION_AUDIT_OUT_DIR': str(self.out_dir),
      'SESSION_AUDIT_PYTHON': sys.executable,
    }

  def run_reader(self):
    return subprocess.run(
      ['bash', str(DIR / 'run-shell-channel.sh'), str(DIR / 'session-audit-daily.sh'), str(self.out), DATE, 'true'],
      env=self.env, capture_output=True, text=True, timeout=10,
    )

  def test_missing_state_reports_not_started_without_creating_database(self):
    result = self.run_reader()
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertTrue(self.out.is_file())
    self.assertTrue(Path(str(self.out) + '.complete').is_file())
    self.assertFalse(self.state.exists())
    self.assertIn('sources=0', self.out.read_text())

  def test_refused_state_does_not_publish_zero_as_a_successful_report(self):
    self.state.symlink_to(self.root / 'outside', target_is_directory=True)
    result = self.run_reader()
    self.assertNotEqual(result.returncode, 0)
    self.assertFalse(self.out.exists())
    self.assertFalse(Path(str(self.out) + '.complete').exists())

  def test_corrupt_state_does_not_publish_zero_as_a_successful_report(self):
    self.state.mkdir()
    (self.state / 'state.sqlite').write_text('not a sqlite database')
    result = self.run_reader()
    self.assertNotEqual(result.returncode, 0)
    self.assertFalse(self.out.exists())


if __name__ == '__main__':
  unittest.main()
