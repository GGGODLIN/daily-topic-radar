"""規則違反查詢測試共用的 fixture 建構器（不是測試檔，不被 session-audit-*.test.py 載入）。

資料庫 schema 與規則身分都由 session-audit.py 自己產生；只有列內容由測試逐筆指定，
所以預期值可以從 fixture 本身用手算。全部寫在呼叫端給的臨時目錄，不碰正式 state、摩擦檔或累積庫。
"""

import importlib.util
import json
import os
import subprocess
from pathlib import Path

AUDIT = Path(__file__).with_name("session-audit.py")


def load_audit():
  spec = importlib.util.spec_from_file_location("audit_for_rule_fixture", AUDIT)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def git(repo, *args, date=None):
  env = os.environ.copy()
  env.update({"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})
  if date:
    env["GIT_AUTHOR_DATE"] = date
    env["GIT_COMMITTER_DATE"] = date
  done = subprocess.run(
    ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
    capture_output=True, text=True, env=env, check=True,
  )
  return done.stdout.strip()


class RuleFixture:
  """一個臨時規則 repo＋一個臨時 session-audit state＋若干合成 session 檔。"""

  def __init__(self, root):
    self.root = Path(root)
    self.audit = load_audit()
    self.rules_repo = self.root / "rules-repo"
    self.state = self.root / "audit-state"
    self.sessions = self.root / "sessions"
    self.sessions.mkdir(parents=True, exist_ok=True)
    self.state.mkdir(parents=True, exist_ok=True)
    self.serial = 0
    # 讓 schema 由 session-audit 自己建。
    self.audit.connect(self.state, write=True).close()

  def commit_rules(self, files, date):
    self.rules_repo.mkdir(parents=True, exist_ok=True)
    if not (self.rules_repo / ".git").exists():
      git(self.rules_repo, "init", "-q")
    for name, text in files.items():
      target = self.rules_repo / name
      target.parent.mkdir(parents=True, exist_ok=True)
      target.write_text(text)
    git(self.rules_repo, "add", "-A")
    git(self.rules_repo, "commit", "-q", "-m", "rules", date=date)
    return git(self.rules_repo, "rev-parse", "HEAD")

  def rule_at(self, sha, marker):
    loaded = self.audit.load_rules(str(self.rules_repo), sha)
    found = [rule for rule in loaded["labels"].values() if marker in rule["text"]]
    assert len(found) == 1, (marker, len(found))
    return found[0]

  def line_of(self, path, marker):
    for number, line in enumerate((self.rules_repo / path).read_text().splitlines(), 1):
      if marker in line:
        return number
    raise AssertionError(marker)

  def add_session(self, name, stamps, commit, violations=(), covered=True, mtime=None, status="complete", session_id=None):
    """stamps[n-1] 是第 n 行 session 紀錄的時間；violations 是 (規則標記字串, 行號, 引文)。

    covered=True 代表整份都完成規則分析；給整數 n 代表只有前 n 行完成（其後的段落回覆缺 rule_tags）。
    name 可含子目錄；session_id 預設等於 name，給了就讓不同檔案共用同一個 session。
    """
    self.serial += 1
    session_id = session_id or name
    path = self.sessions / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, stamp in enumerate(stamps, 1):
      rows.append(json.dumps({
        "type": "user", "sessionId": session_id, "cwd": "/work/natural", "timestamp": stamp,
        "message": {"role": "user", "content": f"{name} line {index}"},
      }))
    path.write_text("\n".join(rows) + "\n")
    last = mtime if mtime is not None else path.stat().st_mtime
    os.utime(path, (last, last))
    inode = f"inode-{self.serial}"
    earliest = min(stamp for stamp in stamps if stamp)
    db = self.audit.connect(self.state, write=True)
    try:
      db.execute(
        """INSERT INTO sources (inode, path, name, classification, included, status, latest_complete,
                                generation, session_id, mtime)
           VALUES (?, ?, ?, 'natural', 1, ?, 1, 1, ?, ?)""",
        (inode, str(path), path.name, status, session_id, last),
      )
      db.execute(
        "INSERT INTO rule_sessions (inode, session_id, conversation_time, commit_sha, unavailable) VALUES (?, ?, ?, ?, '')",
        (inode, session_id, earliest, commit),
      )
      # 每行一個 10 byte 的來源段；完成規則分析的視窗從 0 起算，到第 covered 行結束。
      for number in range(1, len(stamps) + 1):
        db.execute(
          """INSERT INTO segments (inode, generation, doc_start, doc_end, line, block, byte_offset, role, record_type,
                                   record_uuid, tool_name, tool_use_id, status)
             VALUES (?, 1, ?, ?, ?, 0, 0, 'user', 'user', '', '', '', 'analyzed')""",
          (inode, (number - 1) * 10, number * 10, number),
        )
      lines = len(stamps) if covered is True else int(covered)
      if lines:
        db.execute("INSERT INTO rule_coverage (inode, generation, doc_start, week) VALUES (?, 1, 0, '2026-W44')", (inode,))
        db.execute(
          "INSERT INTO model_replies (inode, generation, doc_start, doc_end, content, status) VALUES (?, 1, 0, ?, '', 'parsed')",
          (inode, lines * 10),
        )
      for marker, line, quote in violations:
        rule = self.rule_at(commit, marker)
        db.execute(
          """INSERT INTO rule_tags (inode, generation, rule_id, rule_path, rule_heading, verdict, source_line,
                                    source_ref, quote, conversation_time, commit_sha)
             VALUES (?, 1, ?, ?, ?, 'violated', ?, ?, ?, ?, ?)""",
          (inode, rule["id"], rule["path"], rule["heading"], line, self.audit.event_ref(name, line, quote), quote, earliest, commit),
        )
      db.commit()
    finally:
      db.close()
    return path
