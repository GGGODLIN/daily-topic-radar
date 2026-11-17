#!/usr/bin/env python3
"""habits-extract.py 的契約測試：過濾條件、切段邊界、檔案權限、失敗退出碼。

用法：python3 -I habits-extract.test.py
"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("habits_extract", HERE / "habits-extract.py")
habits = importlib.util.module_from_spec(spec)
spec.loader.exec_module(habits)

GOOD_PATH = "/Users/x/.claude/projects/-Users-linhancheng-Desktop-projects-demo/sess.jsonl"


def event(ts, text, session="aaaaaaaa-1", path=GOOD_PATH, role="user"):
    return {"role": role, "path": path, "ts": ts, "session": session, "text": text, "kind": "text"}


def run_main(events, date="2026-11-16", out_dir=None):
    tmp = tempfile.mkdtemp()
    events_file = Path(tmp) / "events.ndjson"
    events_file.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
    out_dir = out_dir or str(Path(tmp) / "out")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = habits.main(["--date", date, "--out-dir", out_dir, "--events-file", str(events_file)])
    return rc, stdout.getvalue(), stderr.getvalue(), Path(out_dir)


class FilterTest(unittest.TestCase):
    def kept(self, e):
        return habits.parse_user_message(e) is not None

    def test_known_good(self):
        self.assertTrue(self.kept(event("2026-11-10T01:00:00Z", "請先查證再回答這個問題")))

    def test_known_bad_each_exclusion(self):
        bad = [
            event("2026-11-10T01:00:00Z", "助理訊息內容很長很長", role="assistant"),
            event("2026-11-10T01:00:00Z", "子代理的訊息內容", path=GOOD_PATH.replace("sess.jsonl", "subagents/a.jsonl")),
            event("2026-11-10T01:00:00Z", "其他帳號專案的訊息", path="/Users/x/.claude/projects/-private-tmp-foo/s.jsonl"),
            event("2026-11-10T01:00:00Z", "<command-name>/clear</command-name>"),
            event("2026-11-10T01:00:00Z", "[SYSTEM NOTIFICATION] done"),
            event("2026-11-10T01:00:00Z", "夾帶 <task-notification> 的內文"),
            event("2026-11-10T01:00:00Z", "夾帶 system-reminder 的內文"),
            event("2026-11-10T01:00:00Z", "長" * 2001),
            event("2026-11-10T01:00:00Z", "[main] 代理轉貼的裁決"),
            event("2026-11-10T01:00:00Z", "[watcher] 監看訊息"),
            event("2026-11-10T01:00:00Z", "[playtest-monitor] 回報"),
            event("2026-11-10T01:00:00Z", "Heads up: something"),
            event("2026-11-10T01:00:00Z", "   "),
        ]
        for e in bad:
            self.assertFalse(self.kept(e), e["text"][:30])

    def test_boundary_length(self):
        self.assertTrue(self.kept(event("2026-11-10T01:00:00Z", "長" * 2000)))

    def test_dedup_by_session_ts_prefix(self):
        e = event("2026-11-10T01:00:00Z", "相同訊息內容出現兩次")
        self.assertEqual(len(habits.collect_messages([e, dict(e)])), 1)
        self.assertEqual(len(habits.collect_messages([e, event("2026-11-10T01:00:01Z", "相同訊息內容出現兩次")])), 2)


class SegmentTest(unittest.TestCase):
    def test_bounds_cover_21_days_ending_on_date(self):
        self.assertEqual(
            habits.segment_bounds("2026-11-16"),
            [("2026-10-27", "2026-11-02"), ("2026-11-03", "2026-11-09"), ("2026-11-10", "2026-11-16")],
        )

    def test_boundary_days_land_in_one_segment_only(self):
        msgs = [
            ("2026-10-26T23:59", "s", "p", "窗口之前不收"),
            ("2026-10-27T00:00", "s", "p", "第一段第一天"),
            ("2026-11-02T23:59", "s", "p", "第一段最後一天"),
            ("2026-11-03T00:00", "s", "p", "第二段第一天"),
            ("2026-11-16T23:59", "s", "p", "第三段最後一天"),
            ("2026-11-17T00:00", "s", "p", "窗口之後不收"),
        ]
        placed = [[m[3] for m in msgs if habits.in_segment(m, s, e)] for s, e in habits.segment_bounds("2026-11-16")]
        self.assertEqual(placed, [["第一段第一天", "第一段最後一天"], ["第二段第一天"], ["第三段最後一天"]])


class RenderTest(unittest.TestCase):
    def test_short_replies_in_header_only_long_in_body(self):
        msgs = [("2026-11-10T01:00", "s1", "proj", "可以")] * 3 + [
            ("2026-11-10T02:00", "s2", "proj", "這是一則超過六個字的訊息\n第二行")
        ]
        text, body_count, sessions = habits.render_slice(1, "2026-11-10", "2026-11-16", msgs)
        head, body = text.split("## 訊息全文", 1)
        self.assertIn("可以×3", head)
        self.assertNotIn("可以", body)
        self.assertIn("這是一則超過六個字的訊息 ⏎ 第二行", body)
        self.assertEqual((body_count, sessions), (1, 2))
        self.assertIn("共 4 則使用者訊息", head)


class MainTest(unittest.TestCase):
    def test_writes_private_files_and_reports_counts(self):
        events = [
            event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容"),
            event("2026-11-04T01:00:00Z", "第二段的一則長訊息內容", session="aaaaaaaa-2"),
            event("2026-11-04T02:00:00Z", "好"),
            event("2026-11-12T01:00:00Z", "第三段的一則長訊息內容", session="aaaaaaaa-3"),
        ]
        rc, out, _err, out_dir = run_main(events)
        self.assertEqual(rc, 0)
        packet = json.loads(out)
        self.assertEqual([s["total_messages"] for s in packet["slices"]], [1, 2, 1])
        self.assertEqual([s["body_messages"] for s in packet["slices"]], [1, 1, 1])
        self.assertEqual(stat.S_IMODE(os.stat(out_dir).st_mode), 0o700)
        for s in packet["slices"]:
            self.assertEqual(s["path"], str(out_dir / f"2026-11-16-habits-slice-{s['index']}.txt"))
            self.assertEqual(stat.S_IMODE(os.stat(s["path"]).st_mode), 0o600)

    def test_existing_loose_file_is_tightened_to_0600(self):
        tmp = Path(tempfile.mkdtemp())
        loose = tmp / "2026-11-16-habits-slice-1.txt"
        loose.write_text("舊內容", encoding="utf-8")
        os.chmod(loose, 0o644)
        rc, _out, _err, _ = run_main([event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容")], out_dir=str(tmp))
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(os.stat(loose).st_mode), 0o600)
        self.assertNotIn("舊內容", loose.read_text(encoding="utf-8"))

    def test_symlink_target_refused_and_nothing_written_elsewhere(self):
        tmp = Path(tempfile.mkdtemp())
        victim = tmp / "victim.txt"
        victim.write_text("原樣", encoding="utf-8")
        out_dir = tmp / "out"
        out_dir.mkdir()
        os.symlink(victim, out_dir / "2026-11-16-habits-slice-1.txt")
        with self.assertRaises(OSError):
            run_main([event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容")], out_dir=str(out_dir))
        self.assertEqual(victim.read_text(encoding="utf-8"), "原樣")

    def test_zero_messages_exits_3_and_writes_nothing(self):
        rc, out, err, out_dir = run_main([event("2026-10-28T01:00:00Z", "[main] 只有轉貼")])
        self.assertEqual(rc, 3)
        self.assertEqual(out, "")
        self.assertFalse(out_dir.exists())
        self.assertIn("0 則", err)

    def test_bad_date_and_missing_events_file_exit_2(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(habits.main(["--date", "11/16"]), 2)
            self.assertEqual(habits.main(["--date", "2026-11-16", "--events-file", "/nonexistent/x.ndjson"]), 2)
            self.assertEqual(habits.main(["--date", "2026-11-16", "--max-part-bytes", "100"]), 2)

    def test_oversized_slice_splits_into_0600_parts_preserving_body_count(self):
        events = [
            event(f"2026-11-12T01:{i // 60:02d}:{i % 60:02d}Z", f"第三段長訊息編號 {i:03d} " + "內文填充" * 30, session=f"sess-{i:04d}")
            for i in range(60)
        ] + [event(f"2026-11-12T00:00:0{j}Z", "好") for j in range(4)]
        tmp = Path(tempfile.mkdtemp())
        events_file = tmp / "events.ndjson"
        events_file.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n", encoding="utf-8")
        out_dir = tmp / "out"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = habits.main(["--date", "2026-11-16", "--out-dir", str(out_dir), "--max-part-bytes", "6000", "--events-file", str(events_file)])
        self.assertEqual(rc, 0, stderr.getvalue())
        packet = json.loads(stdout.getvalue())
        s1, _s2, s3 = packet["slices"]
        self.assertNotIn("parts", s1)
        self.assertIn("parts", s3)
        self.assertGreater(len(s3["parts"]), 1)
        self.assertEqual(sum(p["body_messages"] for p in s3["parts"]), s3["body_messages"])
        for idx, part in enumerate(s3["parts"], start=1):
            self.assertEqual(part["part"], idx)
            self.assertEqual(stat.S_IMODE(os.stat(part["path"]).st_mode), 0o600)
            text = Path(part["path"]).read_text(encoding="utf-8")
            self.assertTrue(text.startswith(f"# 第 3 段第 {idx}/{len(s3['parts'])} 批：2026-11-10 ~ 2026-11-16，本批 {part['body_messages']} 則正文訊息"))
            self.assertLessEqual(len(text.encode("utf-8")), 6000)
            if idx == 1:
                self.assertIn("好×4", text)
            else:
                self.assertNotIn("短回覆次數", text)



if __name__ == "__main__":
    unittest.main(verbosity=2)
