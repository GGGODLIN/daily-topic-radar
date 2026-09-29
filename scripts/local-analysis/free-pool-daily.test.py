import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


WRAPPER = Path(__file__).with_name("free-pool-daily.sh")
BOUNDARIES = r'''
function /usr/bin/nc() { [[ "$*" != *"${TEST_DOWN_PORT:-never}"* ]]; }
function /usr/bin/curl() {
  for url in "$@"; do :; done
  printf '%s\n' "$url" >> "$TEST_PROBES"
  case "$url" in *8320*) printf '000';; *) printf '200';; esac
}
function /usr/bin/pgrep() { return 1; }
source "$1"
'''


class UnavailableModel(http.server.BaseHTTPRequestHandler):
  def do_POST(self):
    self.server.requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
    self.send_response(503)
    self.end_headers()
    self.wfile.write(b'{"error":"no eligible accounts"}')

  def log_message(self, *_args):
    pass


class FreePoolDailyTest(unittest.TestCase):
  def run_probe(self, config, down_port="never"):
    with tempfile.TemporaryDirectory() as root:
      base = Path(root)
      config_path = base / "config.yaml"
      if config is not None:
        config_path.write_text(json.dumps(config))
      accounts = base / "accounts.json"
      accounts.write_text(json.dumps({"accounts": [{"status": "active"}]}))
      before = accounts.read_bytes()
      env = {**os.environ, "FREE_POOL_RELAY_CONFIG": str(config_path),
             "FREE_POOL_CLINE_ACCOUNTS": str(accounts), "FREE_POOL_PY_YAML": sys.executable,
             "FREE_POOL_OUT_DIR": str(base / "out"), "FREE_POOL_LOG_DIR_PATH": str(base / "log"),
             "FREE_POOL_MIMO_HEALTH": "http://127.0.0.1:8320/health",
             "FREE_POOL_PORT_RELAY": "18317", "FREE_POOL_PORT_LITELLM": "18000",
             "FREE_POOL_PORT_CLINE": "13457", "FREE_POOL_AR_URL": "http://127.0.0.1:18002",
             "TEST_DOWN_PORT": down_port, "TEST_PROBES": str(base / "probes"),
             "LOCAL_ANALYSIS_DATE": "2026-09-28"}
      run = subprocess.run(["/bin/bash", "-c", BOUNDARIES, "test", str(WRAPPER)],
                           env=env, capture_output=True, text=True, timeout=15)
      self.assertEqual(run.returncode, 0, run.stderr)
      self.assertEqual(accounts.read_bytes(), before)
      if config is not None:
        self.assertEqual(json.loads(config_path.read_text()), config)
      return (base / "out/2026-09-28-free-pool.md").read_text(), (base / "probes").read_text()

  def test_retired_desktop_is_neither_probed_nor_reported(self):
    report, probes = self.run_probe({"openai-compatibility": []})
    self.assertEqual(report, "__SILENT__\n")
    self.assertNotIn("8320", probes)

  def test_active_cline_mimo_failure_still_reported(self):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), UnavailableModel)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
      config = {"openai-compatibility": [{"name": "cline-free-mimo26-free",
        "base-url": f"http://127.0.0.1:{server.server_port}/v1",
        "models": [{"name": "free-mimo26", "alias": "free"}]}]}
      report, _ = self.run_probe(config)
      self.assertIn("[cline-free-mimo26-free] hello 失敗：HTTP 503", report)
      self.assertEqual([r["model"] for r in server.requests], ["free-mimo26"])
      self.assertNotIn("[mimo]", report)
    finally:
      server.shutdown()
      thread.join()
      server.server_close()

  def test_missing_config_is_not_green(self):
    report, _ = self.run_probe(None)
    self.assertIn("[relay-config]", report)
    self.assertIn("fail-loud", report)

  def test_relay_down_is_still_reported(self):
    report, _ = self.run_probe({"openai-compatibility": []}, down_port="18317")
    self.assertIn("[relay]", report)
    self.assertNotEqual(report, "__SILENT__\n")


if __name__ == "__main__":
  unittest.main()
