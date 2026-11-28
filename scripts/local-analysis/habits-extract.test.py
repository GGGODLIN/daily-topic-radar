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
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT_PATH = HERE / "habits-extract.py"
spec = importlib.util.spec_from_file_location("habits_extract", SCRIPT_PATH)
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

    def test_early_calendar_date_exits_2_without_writing(self):
        rc, out, err, out_dir = run_main([], date="0001-01-02")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("--date", err)
        self.assertFalse(out_dir.exists())

    def test_unsatisfiable_part_limits_leave_existing_output_intact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out_dir = root / "out"
            out_dir.mkdir(mode=0o755)
            os.chmod(out_dir, 0o755)
            stale = out_dir / "2026-11-16-habits-slice-3-part-1.txt"
            stale.write_text("prior output", encoding="utf-8")
            events = [event("2026-10-28T00:00:00Z", "第一段的一則長訊息內容")]
            events += [
                event(f"2026-11-12T01:{i:02d}:00Z", "內容" * 500)
                for i in range(habits.MAX_PARTS_PER_SLICE + 1)
            ]
            source = root / "events.jsonl"
            source.write_text("\n".join(json.dumps(row) for row in events), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = habits.main([
                    "--date", "2026-11-16", "--out-dir", str(out_dir), "--events-file", str(source),
                    "--max-part-bytes", "4000",
                ])
            self.assertEqual((rc, stdout.getvalue()), (2, ""))
            self.assertIn("第 3 段無法切成", stderr.getvalue())
            self.assertEqual(list(out_dir.iterdir()), [stale])
            self.assertEqual(stale.read_text(encoding="utf-8"), "prior output")
            self.assertEqual(stat.S_IMODE(out_dir.stat().st_mode), 0o755)

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

    def test_split_slice_parts_respects_4000_byte_limit_without_dropping_split(self):
        msgs_many = [
            (f"2026-11-12T01:{i // 60:02d}:{i % 60:02d}Z", f"sess-{i:04d}", "demo", f"訊息 {i:03d} " + "內文" * 35)
            for i in range(40)
        ]
        parts = habits.split_slice_parts(3, "2026-11-10", "2026-11-16", msgs_many, max_part_bytes=4000)
        self.assertGreater(len(parts), 1)
        for _part_no, part_text, _cnt in parts:
            self.assertLessEqual(len(part_text.encode("utf-8")), 4000)

        short_msgs = [(f"2026-11-12T00:{j:02d}:00Z", "s", "demo", f"短{j:02d}") for j in range(25)]
        body_msgs = [(f"2026-11-12T01:{j:02d}:00Z", "s", "demo", f"長訊息編號{j:02d} " + "內容" * 28) for j in range(19)]
        all_msgs = short_msgs + body_msgs
        full_text, _, _ = habits.render_slice(3, "2026-11-10", "2026-11-16", all_msgs)
        self.assertGreater(len(full_text.encode("utf-8")), 4000)
        parts2 = habits.split_slice_parts(3, "2026-11-10", "2026-11-16", all_msgs, max_part_bytes=4000)
        self.assertGreater(len(parts2), 1)
        for _part_no, part_text, _cnt in parts2:
            self.assertLessEqual(len(part_text.encode("utf-8")), 4000)

    def test_symlink_out_dir_refused_without_chmodding_target(self):
        tmp = Path(tempfile.mkdtemp())
        victim_dir = tmp / "victim-dir"
        victim_dir.mkdir(mode=0o755)
        os.chmod(victim_dir, 0o755)
        link_dir = tmp / "out-link"
        os.symlink(victim_dir, link_dir)
        with self.assertRaises(OSError):
            run_main([event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容")], out_dir=str(link_dir))
        self.assertEqual(stat.S_IMODE(os.stat(victim_dir).st_mode), 0o755)
        self.assertEqual(list(victim_dir.iterdir()), [])

    def test_stale_part_symlink_and_late_slice_symlink_fail_atomically(self):
        tmp = Path(tempfile.mkdtemp())
        victim = tmp / "victim.txt"
        victim.write_text("原樣", encoding="utf-8")
        out_dir = tmp / "out"
        out_dir.mkdir()
        os.symlink(victim, out_dir / "2026-11-16-habits-slice-1-part-2.txt")
        with self.assertRaises(OSError):
            run_main([event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容")], out_dir=str(out_dir))
        self.assertFalse((out_dir / "2026-11-16-habits-slice-1.txt").exists())

        out_dir2 = tmp / "out2"
        out_dir2.mkdir()
        os.symlink(victim, out_dir2 / "2026-11-16-habits-slice-2.txt")
        with self.assertRaises(OSError):
            run_main([
                event("2026-10-28T01:00:00Z", "第一段的一則長訊息內容"),
                event("2026-11-04T01:00:00Z", "第二段的一則長訊息內容"),
            ], out_dir=str(out_dir2))
        self.assertFalse((out_dir2 / "2026-11-16-habits-slice-1.txt").exists())
        self.assertEqual(victim.read_text(encoding="utf-8"), "原樣")

    def test_non_dict_jsonl_and_non_string_fields_are_skipped(self):
        tmp = Path(tempfile.mkdtemp())
        events_file = tmp / "mixed.ndjson"
        events_file.write_bytes(
            b'null\n123\n[]\n'
            + json.dumps({"role": "user", "path": GOOD_PATH, "ts": 12345, "session": "s1", "text": "bad ts type"}).encode() + b'\n'
            + json.dumps({"role": "user", "path": GOOD_PATH, "ts": "2026-10-28T01:00:00Z", "session": "s2", "text": 999}).encode() + b'\n'
            + b'\xff\xfe invalid utf8\n'
            + json.dumps(event("2026-10-28T01:00:00Z", "這是一則合法使用者訊息內容"), ensure_ascii=False).encode("utf-8") + b'\n'
        )
        out_dir = tmp / "out"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            rc = habits.main(["--date", "2026-11-16", "--out-dir", str(out_dir), "--events-file", str(events_file)])
        self.assertEqual(rc, 0, stderr.getvalue())
        packet = json.loads(stdout.getvalue())
        self.assertEqual(packet["slices"][0]["total_messages"], 1)

    def test_strict_date_and_timezone_aware_segment_bucketing_and_ordering(self):
        with self.assertRaises(ValueError):
            habits.segment_bounds("20261116")
        self.assertFalse(habits.in_segment(("2026-10-32T01:00:00Z", "s", "p", "非法日期"), "2026-10-27", "2026-11-02"))
        e_earlier = event("2026-11-03T01:00:00+08:00", "台北凌晨一點其實是前一天UTC十七點", session="s-tz")
        e_later = event("2026-11-02T20:00:00Z", "UTC晚上八點比台北凌晨一點晚", session="s-utc")
        collected = habits.collect_messages([e_later, e_earlier])
        self.assertEqual([m[1] for m in collected], ["s-tz", "s-utc"])
        bounds = habits.segment_bounds("2026-11-16")
        self.assertTrue(habits.in_segment(collected[0], *bounds[0]))
        self.assertFalse(habits.in_segment(collected[0], *bounds[1]))

    def test_fullwidth_unicode_digits_in_timestamp_rejected(self):
        self.assertIsNone(habits.parse_iso_timestamp("2026-11-10T２３:00:00Z"))
        self.assertEqual(
            habits.parse_iso_timestamp("2026-11-10T23:00:00.500Z"),
            ("2026-11-10T23:00:00.500000", "2026-11-10"),
        )

    def test_noisy_stderr_over_64kib_does_not_deadlock(self):
        tmp = Path(tempfile.mkdtemp())
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        fake_ccsi = bin_dir / "ccsi"
        ev_json = json.dumps(event("2026-11-15T10:00:00Z", "大量 stderr 警告下不可死結的使用者訊息"), ensure_ascii=False)
        fake_ccsi.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "sys.stderr.write(('warn ' + 'x' * 80 + '\\n') * 1500)\n"
            "sys.stderr.flush()\n"
            f"sys.stdout.write({ev_json!r} + '\\n')\n"
            "sys.stdout.flush()\n",
            encoding="utf-8",
        )
        fake_ccsi.chmod(0o755)
        out_dir = tmp / "out"
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ.get('PATH', '')}")
        res = subprocess.run(
            [sys.executable, "-I", str(SCRIPT_PATH), "--date", "2026-11-16", "--out-dir", str(out_dir)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout)["slices"][2]["total_messages"], 1)

    def test_legacy_ccsi_fallback_normal_and_force_color(self):
        tmp = Path(tempfile.mkdtemp())
        bin_dir = tmp / "bin"
        bin_dir.mkdir()
        legacy_ccsi = bin_dir / "ccsi"
        ev_json = json.dumps(event("2026-11-15T10:00:00Z", "舊版 ccsi 退回相容旗標測試訊息"), ensure_ascii=False)
        legacy_ccsi.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "if '--role' in sys.argv or '--exclude-subagents' in sys.argv:\n"
            "    sys.stderr.write('\\x1b[31musage: ccsi\\x1b[0m\\nccsi: error: unrecognized arguments: --role user --exclude-subagents\\n')\n"
            "    sys.exit(2)\n"
            f"sys.stdout.write({ev_json!r} + '\\n')\n",
            encoding="utf-8",
        )
        legacy_ccsi.chmod(0o755)
        for color_env in ({}, {"FORCE_COLOR": "1", "PYTHON_COLORS": "1"}):
            out_dir = tmp / f"out_{len(color_env)}"
            env = dict(os.environ, PATH=f"{bin_dir}:{os.environ.get('PATH', '')}", **color_env)
            res = subprocess.run(
                [sys.executable, "-I", str(SCRIPT_PATH), "--date", "2026-11-16", "--out-dir", str(out_dir)],
                env=env, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(json.loads(res.stdout)["slices"][2]["total_messages"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
