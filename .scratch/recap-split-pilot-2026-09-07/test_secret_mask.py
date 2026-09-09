import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location("recap_collector", Path(__file__).with_name("collector.py"))
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

class SecretMaskTest(unittest.TestCase):
  def test_credential_values_are_removed(self):
    assignments = [
      "api_key=fixture-only-value",
      "OPENAI_API_KEY=fixture-only-value",
      "MY_PASSWORD=fixture-only-value",
      "GITHUB_TOKEN=fixture-only-value",
      "AWS_SECRET_ACCESS_KEY=fixture-only-value",
      "AWS_SESSION_TOKEN=fixture-only-value",
      '"SecretAccessKey": "fixture-only-value"',
      '"secretAccessKey": "fixture-only-value"',
      '"AccessKeyId": "fixture-only-value"',
      '"api_key": "fixture-only-value"',
      "'client_secret': 'fixture-only-value'",
    ]
    for assignment in assignments:
      with self.subTest(assignment=assignment.split("fixture", 1)[0]):
        masked, count = collector.mask_secrets(assignment)
        self.assertNotIn("fixture-only-value", masked)
        self.assertGreater(count, 0)

  def test_punctuation_is_preserved(self):
    masked, count = collector.mask_secrets("(api_key=fixture-only-value), done")
    self.assertEqual(masked, "(api_key=<REDACTED>), done")
    self.assertEqual(count, 1)

  def test_session_output_masks_json_credential(self):
    acc = collector.new_accumulator("fixture-session")
    row = collector.WindowRow(
      source_file="/fixture/session.jsonl",
      line=1,
      session_id="fixture-session",
      row_type="user",
      timestamp="2026-09-07T00:00:00Z",
      timestamp_dt=datetime(2026, 9, 7, tzinfo=timezone.utc),
      cwd=None,
      content='{"OPENAI_API_KEY": "fixture-only-value"}',
    )
    collector.add_window_row(acc, row)
    self.assertNotIn("fixture-only-value", acc.user_messages[0]["text"])
    self.assertEqual(acc.user_messages[0]["secret_masks"], 1)

if __name__ == "__main__":
  unittest.main()
