#!/usr/bin/env python3
"""session-telemetry.py 與 session-telemetry-weekly.sh 合成 fixture 單元與端對端測試。

跑法：python3 /Users/linhancheng/code/social-info/scripts/local-analysis/session-telemetry.test.py
"""

import datetime as dt
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT_PY = HERE / "session-telemetry.py"
WRAPPER_SH = HERE / "session-telemetry-weekly.sh"

spec = importlib.util.spec_from_file_location("session_telemetry", SCRIPT_PY)
st = importlib.util.module_from_spec(spec)
spec.loader.exec_module(st)


def iso_at(day_str: str, offset_sec: int = 0) -> str:
    # 固定用本地時區中午為基準，避免跨 UTC 午夜時日期漂移
    local_tz = dt.datetime.now().astimezone().tzinfo
    base = dt.datetime.combine(dt.date.fromisoformat(day_str), dt.time(12, 0, 0), tzinfo=local_tz)
    t = base + dt.timedelta(seconds=offset_sec)
    return t.isoformat()


def write_jsonl(path: Path, lines: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_lines = [line if isinstance(line, str) else json.dumps(line, ensure_ascii=False) for line in lines]
    path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")


def user_msg(ts: str, text: str, **extra) -> dict:
    obj = {
        "type": "user",
        "timestamp": ts,
        "entrypoint": extra.pop("entrypoint", "cli"),
        "message": {"role": "user", "content": text},
    }
    obj.update(extra)
    return obj


def asst_usage_msg(ts: str, mid: str, model: str, inp: int, cc: int, cr: int, out: int, blocks: list = None) -> dict:
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": mid,
            "model": model,
            "role": "assistant",
            "usage": {
                "input_tokens": inp,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr,
                "output_tokens": out,
            },
            "content": blocks or [{"type": "text", "text": "ok"}],
        },
    }


def tool_edit(tid: str, file_path: str, tool_name: str = "Edit") -> dict:
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    return {"type": "tool_use", "id": tid, "name": tool_name, "input": {key: file_path}}


def tool_agent(tid: str, subagent_type: str, tool_name: str = "Agent") -> dict:
    return {"type": "tool_use", "id": tid, "name": tool_name, "input": {"subagent_type": subagent_type, "description": "task"}}


def tool_result_msg(ts: str, tool_use_id: str, is_error: bool = False, content: str = "ok") -> dict:
    blk = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
    if is_error:
        blk["is_error"] = True
    return {
        "type": "user",
        "timestamp": ts,
        "entrypoint": "cli",
        "message": {"role": "user", "content": [blk]},
    }


class SessionTelemetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        self.root.mkdir(parents=True, exist_ok=True)
        self.ref_date = dt.date(2026, 11, 19)

    def tearDown(self):
        self.tmp.cleanup()

    def test_human_vs_auto_and_worker_separation(self):
        # 1. 真人純主控
        write_jsonl(
            self.root / "-Users-linhancheng-Desktop-projects-app" / "orch-1.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "幫我修這個 bug", promptSource="typed"),
                asst_usage_msg(
                    iso_at("2026-11-18", 10),
                    "m-orch-1",
                    "claude-opus-4-1",
                    100, 200, 300, 400,
                    [tool_edit("e1", "/Users/x/app/main.py"), tool_agent("a1", "routed-impl")],
                ),
            ],
        )
        # 2. worktree worker（目錄含 worktree）
        write_jsonl(
            self.root / "-Users-linhancheng-Desktop-projects-app-worktrees-wt1" / "wrk-wt.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "實作票 1", promptSource="typed"),
                asst_usage_msg(
                    iso_at("2026-11-18", 10),
                    "m-wrk-1",
                    "claude-sonnet-4-5",
                    50, 0, 0, 50,
                    [tool_edit("e2", "/Users/x/app/wt.ts"), tool_edit("e3", "/Users/x/app/wt2.ts")],
                ),
            ],
        )
        # 3. Herdr worker（首句 [walky-talky ...）
        write_jsonl(
            self.root / "-Users-linhancheng-Desktop-projects-app" / "wrk-wt2.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "[walky-talky wt_123] 接手這個任務", promptSource="typed"),
                asst_usage_msg(
                    iso_at("2026-11-18", 10),
                    "m-wrk-2",
                    "claude-sonnet-4-5",
                    10, 0, 0, 10,
                    [tool_edit("e4", "/Users/x/app/w2.go")],
                ),
            ],
        )
        # 4. 自動 sdk-cli 測試檔（不計入真人）
        write_jsonl(
            self.root / "-Users-linhancheng-Desktop-projects-app" / "auto-sdk.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "automated prompt", entrypoint="sdk-cli"),
                asst_usage_msg(
                    iso_at("2026-11-18", 10),
                    "m-auto-1",
                    "claude-haiku-4-5",
                    999, 0, 0, 1,
                    [tool_edit("e5", "/Users/x/app/auto.py")],
                ),
            ],
        )
        # 5. /tmp 零 typed prompt（tmp-harness，排除）
        write_jsonl(
            self.root / "-private-tmp-smoke-test" / "auto-tmp.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "hello from cli fallback without promptSource"),
                asst_usage_msg(iso_at("2026-11-18", 10), "m-auto-2", "claude-haiku-4-5", 10, 0, 0, 10),
            ],
        )
        # 6. cli 只有 system wrapper（cli-zero-human-turns，排除）
        write_jsonl(
            self.root / "-Users-linhancheng-Desktop-projects-app" / "auto-sys.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "<system-reminder>hook</system-reminder>"),
                asst_usage_msg(iso_at("2026-11-18", 10), "m-auto-3", "claude-haiku-4-5", 10, 0, 0, 10),
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        m1 = rep["metric1_self_edit_vs_impl"]
        qr = rep["quality_receipt"]

        self.assertEqual(qr["window_sessions_total"], 6)
        self.assertEqual(qr["window_sessions_human"], 3)
        self.assertEqual(qr["window_sessions_orchestrator"], 1)
        self.assertEqual(qr["window_sessions_worker"], 2)
        self.assertEqual(qr["excluded_automated_sessions"], 3)
        self.assertEqual(qr["excluded_by_reason"], {"sdk-cli": 1, "tmp-harness": 1, "cli-zero-human-turns": 1})

        self.assertEqual(m1["orchestrator_only"]["edits_code"], 1)
        self.assertEqual(m1["orchestrator_only"]["impl_dispatches"], 1)
        self.assertEqual(m1["orchestrator_only"]["ratio_code_to_impl"], 1.0)
        self.assertEqual(m1["worker_only"]["edits_code"], 3)
        self.assertEqual(m1["worker_only"]["impl_dispatches"], 0)

    def test_code_vs_non_code_and_monthly_trend(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-repo"
        # 2026-11（30 天視窗內 + 趨勢內）
        write_jsonl(
            proj / "nov-orch.jsonl",
            [
                user_msg(iso_at("2026-11-15", 0), "改碼與文件"),
                asst_usage_msg(
                    iso_at("2026-11-15", 10),
                    "m-nov",
                    "claude-opus-4-1",
                    10, 0, 0, 10,
                    [
                        tool_edit("c1", "/repo/src/app.py"),
                        tool_edit("c2", "/repo/Makefile", "Write"),
                        tool_edit("c3", "/repo/nb.ipynb", "NotebookEdit"),
                        tool_edit("cfg1", "/repo/package.json"),
                        tool_edit("md1", "/repo/README.md"),
                        tool_edit("mem1", "/Users/x/.claude/memory/note.md"),
                        tool_edit("scr1", "/Users/x/scratchpad/test.py"),
                        tool_agent("imp1", "routed-impl"),
                        tool_agent("imp2", "routed-mech"),
                    ],
                ),
            ],
        )
        # 2026-09（超過 30 天，但仍在近 4 個月趨勢 2026-08..2026-11 內）
        write_jsonl(
            proj / "sep-orch.jsonl",
            [
                user_msg(iso_at("2026-09-10", 0), "九月純主控"),
                asst_usage_msg(
                    iso_at("2026-09-10", 10),
                    "m-sep",
                    "claude-opus-4-1",
                    10, 0, 0, 10,
                    [
                        tool_edit("s1", "/repo/a.ts"),
                        tool_edit("s2", "/repo/b.ts"),
                        tool_edit("s3", "/repo/c.ts"),
                        tool_agent("simp1", "blog-phase-runner"),
                    ],
                ),
            ],
        )
        # 2026-09 的 worker，不得污染純主控趨勢
        write_jsonl(
            proj / "sep-worker.jsonl",
            [
                user_msg(iso_at("2026-09-12", 0), "[main] worker task"),
                asst_usage_msg(
                    iso_at("2026-09-12", 10),
                    "m-sep-w",
                    "claude-sonnet-4-5",
                    10, 0, 0, 10,
                    [tool_edit("sw1", "/repo/w.ts")],
                ),
            ],
        )
        # 2026-11-25 未來日期（相對於 --date 2026-11-19 應排除）
        write_jsonl(
            proj / "future-orch.jsonl",
            [
                user_msg(iso_at("2026-11-25", 0), "未來 session"),
                asst_usage_msg(iso_at("2026-11-25", 10), "m-fut", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("f1", "/repo/f.py")]),
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        orch = rep["metric1_self_edit_vs_impl"]["orchestrator_only"]
        self.assertEqual(orch["edits_code"], 3)
        self.assertEqual(orch["edits_config"], 1)
        self.assertEqual(orch["edits_non_code"], 3)
        self.assertEqual(orch["edits_all"], 7)
        self.assertEqual(orch["impl_dispatches"], 2)
        self.assertEqual(orch["ratio_code_to_impl"], 1.5)

        trend = {row["month"]: row for row in rep["metric1_self_edit_vs_impl"]["trend_by_month"]}
        self.assertEqual(list(trend.keys()), ["2026-08", "2026-09", "2026-10", "2026-11"])
        self.assertEqual(trend["2026-09"]["sessions"], 1)
        self.assertEqual(trend["2026-09"]["edits_code"], 3)
        self.assertEqual(trend["2026-09"]["impl_dispatches"], 1)
        self.assertEqual(trend["2026-09"]["ratio_code_to_impl"], 3.0)
        self.assertEqual(trend["2026-11"]["edits_code"], 3)
        self.assertEqual(trend["2026-11"]["impl_dispatches"], 2)

    def test_post_review_60m_burst_detection(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-burst"
        # 命中案：code-reviewer 回報後 60m 內未派實作且連改 3 次程式碼（中間穿插 .md 不算斷也不算次）
        write_jsonl(
            proj / "hit-burst.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "審查後自己修", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 10), "b1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("rev1", "code-reviewer")]),
                tool_result_msg(iso_at("2026-11-18", 50), "rev1"),
                asst_usage_msg(iso_at("2026-11-18", 100), "b2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("be1", "/p/a.py")]),
                asst_usage_msg(iso_at("2026-11-18", 200), "b3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("bmd", "/p/README.md")]),
                asst_usage_msg(iso_at("2026-11-18", 300), "b4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("be2", "/p/b.py")]),
                asst_usage_msg(iso_at("2026-11-18", 600), "b5", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("be3", "/p/a.py")]),
            ],
        )
        # 未命中 1：中間有 routed-impl，切成 2 + 3（後 3 次在 impl 之後不算審查後未派實作）
        write_jsonl(
            proj / "miss-impl.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "先自己改兩次再派 impl", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 10), "m1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("r2", "security-reviewer")]),
                tool_result_msg(iso_at("2026-11-18", 30), "r2"),
                asst_usage_msg(iso_at("2026-11-18", 60), "m2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("me1", "/p/x.ts")]),
                asst_usage_msg(iso_at("2026-11-18", 120), "m3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("me2", "/p/y.ts")]),
                asst_usage_msg(iso_at("2026-11-18", 180), "m4", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("mi1", "routed-impl")]),
                asst_usage_msg(iso_at("2026-11-18", 240), "m5", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("me3", "/p/z.ts")]),
                asst_usage_msg(iso_at("2026-11-18", 300), "m6", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("me4", "/p/z.ts")]),
                asst_usage_msg(iso_at("2026-11-18", 360), "m7", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("me5", "/p/z.ts")]),
            ],
        )
        # 未命中 2：第 3 次修改超過審查回報後 60m（3600s）
        write_jsonl(
            proj / "miss-over60m.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "超時", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 0), "o1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("r3", "python-reviewer")]),
                tool_result_msg(iso_at("2026-11-18", 50), "r3"),
                asst_usage_msg(iso_at("2026-11-18", 100), "o2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("oe1", "/p/a.py")]),
                asst_usage_msg(iso_at("2026-11-18", 200), "o3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("oe2", "/p/b.py")]),
                asst_usage_msg(iso_at("2026-11-18", 3700), "o4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("oe3", "/p/c.py")]),
            ],
        )
        # 未命中 3：非純 5 審查角色（task-verifier）
        write_jsonl(
            proj / "miss-non-pure5.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "非純5角色", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 0), "np1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("r4", "task-verifier")]),
                tool_result_msg(iso_at("2026-11-18", 5), "r4"),
                asst_usage_msg(iso_at("2026-11-18", 10), "np2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("npe1", "/p/a.py")]),
                asst_usage_msg(iso_at("2026-11-18", 20), "np3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("npe2", "/p/b.py")]),
                asst_usage_msg(iso_at("2026-11-18", 30), "np4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("npe3", "/p/c.py")]),
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        m2 = rep["metric2_post_review_60m_bursts"]
        self.assertEqual(m2["orchestrator_only_episodes_ge3_within_60m"], 1)
        self.assertEqual(m2["orchestrator_only_unique_sessions_within_60m"], 1)
        self.assertEqual(m2["orchestrator_only_episodes_ge3_any_window"], 2)
        self.assertEqual(m2["top_samples"][0]["roles"], ["code-reviewer"])
        self.assertEqual(m2["top_samples"][0]["code_edits_within_60m"], 3)
        self.assertEqual(m2["top_samples"][0]["distinct_files_within_60m"], 2)

    def test_tokens_dedup_models_roles_tooluseid_and_fault_tolerance(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-tok"
        # 主 session：同一 message.id 出現三次串流更新；重複 tool_use.id；含一條壞 JSON 行
        write_jsonl(
            proj / "sess-tok.jsonl",
            [
                user_msg(iso_at("2026-11-17", 0), "測 token 與角色", promptSource="typed"),
                "{corrupt-main-line",
                asst_usage_msg(iso_at("2026-11-17", 10), "dup-msg-1", "claude-opus-4-1", 100, 200, 300, 10, [tool_agent("tid-1", "routed-judge")]),
                asst_usage_msg(iso_at("2026-11-17", 11), "dup-msg-1", "claude-opus-4-1", 100, 200, 300, 50, [tool_agent("tid-1", "routed-judge")]),
                asst_usage_msg(iso_at("2026-11-17", 12), "dup-msg-1", "claude-opus-4-1", 100, 200, 300, 400),
                asst_usage_msg(
                    iso_at("2026-11-17", 20),
                    "msg-2",
                    "argon-v1",
                    100, 0, 0, 100,
                    [
                        tool_agent("tid-2", "code-reviewer"),
                        tool_agent("tid-3", "routed-impl"),
                        tool_agent("tid-4", "deep-explore"),
                        tool_agent("tid-5", "general-purpose"),
                    ],
                ),
            ],
        )
        # subagent：對上 tid-1 與 tid-2（共 5 個 main_agent_tids -> 40.0%），且包含壞行、gpt-5.4-pro、sonnet、haiku
        sub_dir = proj / "sess-tok" / "subagents"
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "agent-1.meta.json").write_text(json.dumps({"agentType": "routed-judge", "toolUseId": "tid-1"}), encoding="utf-8")
        write_jsonl(
            sub_dir / "agent-1.jsonl",
            [
                asst_usage_msg(iso_at("2026-11-17", 15), "sub-m1", "gpt-5.4-pro", 50, 50, 100, 100),
                asst_usage_msg(iso_at("2026-11-17", 16), "sub-m1", "gpt-5.4-pro", 50, 50, 100, 200),
                "broken-sub-json{{{",
            ],
        )
        (sub_dir / "agent-2.meta.json").write_text(json.dumps({"agentType": "code-reviewer", "toolUseId": "tid-2"}), encoding="utf-8")
        write_jsonl(
            sub_dir / "agent-2.jsonl",
            [
                asst_usage_msg(iso_at("2026-11-17", 25), "sub-m2", "claude-sonnet-4-5", 20, 0, 80, 100),
                asst_usage_msg(iso_at("2026-11-17", 26), "sub-m3", "claude-haiku-4-5", 10, 0, 40, 50),
            ],
        )

        # 無法讀取的壞檔（測試 skipped 計數）
        unreadable = proj / "unreadable.jsonl"
        write_jsonl(unreadable, [user_msg(iso_at("2026-11-17", 0), "x", promptSource="typed")])
        os.chmod(unreadable, 0)
        try:
            rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        finally:
            os.chmod(unreadable, stat.S_IRUSR | stat.S_IWUSR)

        # 角色佔比（5 次各 20%）
        m3 = rep["metric3_delegation_roles"]
        self.assertEqual(m3["total_main_delegations"], 5)
        for rk in ("judge", "review_verify", "impl", "recon", "other"):
            self.assertEqual(m3["roles"][rk]["count"], 1)
            self.assertEqual(m3["roles"][rk]["pct"], 20.0)

        # Token 四分拆 + 去重
        m4 = rep["metric4_tokens_and_models"]
        # main = dup-msg-1 (100,200,300,400 = 1000) + msg-2 (100,0,0,100 = 200) -> (200,200,300,500 = 1200)
        self.assertEqual(m4["main"]["input_tokens"], 200)
        self.assertEqual(m4["main"]["cache_creation_input_tokens"], 200)
        self.assertEqual(m4["main"]["cache_read_input_tokens"], 300)
        self.assertEqual(m4["main"]["output_tokens"], 500)
        self.assertEqual(m4["main"]["total"], 1200)
        self.assertEqual(m4["main"]["pure_output_pct"], 41.67)

        # sub = sub-m1 (50,50,100,200 = 400) + sub-m2 (20,0,80,100 = 200) + sub-m3 (10,0,40,50 = 100) -> (80,50,220,350 = 700)
        self.assertEqual(m4["subagent"]["input_tokens"], 80)
        self.assertEqual(m4["subagent"]["cache_creation_input_tokens"], 50)
        self.assertEqual(m4["subagent"]["cache_read_input_tokens"], 220)
        self.assertEqual(m4["subagent"]["output_tokens"], 350)
        self.assertEqual(m4["subagent"]["total"], 700)
        self.assertEqual(m4["subagent"]["pure_output_pct"], 50.0)

        self.assertEqual(m4["combined"]["total"], 1900)
        self.assertEqual(m4["combined"]["output_tokens"], 850)
        self.assertEqual(m4["combined"]["pure_output_pct"], 44.74)

        # 模型族群（gpt-5.4-pro 歸 gpt，不歸 opus/pro）
        fams = m4["by_model_family"]
        self.assertEqual(fams["opus/pro"]["total"], 1000)
        self.assertEqual(fams["argon"]["total"], 200)
        self.assertEqual(fams["gpt"]["total"], 400)
        self.assertEqual(fams["sonnet/flash"]["total"], 200)
        self.assertEqual(fams["haiku"]["total"], 100)
        self.assertEqual(fams["other"]["total"], 0)

        # 品質收據
        qr = rep["quality_receipt"]
        self.assertEqual(qr["skipped_main_files"], 1)
        self.assertEqual(qr["json_line_parse_errors"], 2)
        self.assertEqual(qr["tool_use_id_crosscheck"]["main_agent_tids"], 5)
        self.assertEqual(qr["tool_use_id_crosscheck"]["matched_tids"], 2)
        self.assertEqual(qr["tool_use_id_crosscheck"]["match_rate_pct"], 40.0)

    def test_cli_json_and_wrapper_markdown_e2e(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-e2e"
        write_jsonl(
            proj / "e2e-1.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "e2e test", promptSource="typed"),
                asst_usage_msg(
                    iso_at("2026-11-18", 10),
                    "e2e-m1",
                    "claude-opus-4-1",
                    100, 100, 100, 100,
                    [tool_agent("e2e-rev", "code-reviewer")],
                ),
                tool_result_msg(iso_at("2026-11-18", 15), "e2e-rev"),
                asst_usage_msg(iso_at("2026-11-18", 20), "e2e-m2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("ee1", "/app/a.py")]),
                asst_usage_msg(iso_at("2026-11-18", 30), "e2e-m3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("ee2", "/app/b.py")]),
                asst_usage_msg(iso_at("2026-11-18", 40), "e2e-m4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("ee3", "/app/c.py")]),
            ],
        )

        # 1. CLI --format json
        cli_res = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_PY),
                "--projects-dir", str(self.root),
                "--date", "2026-11-19",
                "--format", "json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        parsed = json.loads(cli_res.stdout)
        self.assertEqual(parsed["metric2_post_review_60m_bursts"]["orchestrator_only_episodes_ge3_within_60m"], 1)

        # 2. Wrapper session-telemetry-weekly.sh 端對端產出 Markdown
        out_dir = Path(self.tmp.name) / "reports"
        env = os.environ.copy()
        env["LOCAL_ANALYSIS_DATE"] = "2026-11-19"
        env["SESSION_TELEMETRY_OUT_DIR"] = str(out_dir)
        env["SESSION_TELEMETRY_PROJECTS_DIR"] = str(self.root)

        subprocess.run(["bash", str(WRAPPER_SH)], env=env, check=True)
        out_md = out_dir / "2026-11-19-session-telemetry.md"
        self.assertTrue(out_md.is_file())
        md = out_md.read_text(encoding="utf-8")

        for expected in (
            "# Session 遙測健康度 Weekly — 2026-11-19",
            "## 1. 真人純主控：僅程式碼自改 ÷ 實作派工比",
            "### 近 4 個月趨勢（真人純主控）",
            "## 2. 審查後 60 分鐘內純主控連環自改案數",
            "## 3. 真人互動委派角色佔比",
            "## 4. 真人互動 Token 四分拆與模型族群佔比",
            "純 `output` 佔比焦點",
            "`argon`",
            "`gpt`",
            "## 觀測口徑與品質收據",
            "跨檔去重 message 數",
            "原始 JSONL 截斷／壞行數",
            "toolUseId",
            "> 口徑說明：",
        ):
            self.assertIn(expected, md)

    def test_cross_file_message_id_dedup(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-fork"
        # 兩份不同主檔共享同一段前段歷史（相同 message.id）＋各自一筆無 id 匿名紀錄
        write_jsonl(
            proj / "fork-parent.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "原本對話", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 10), "shared-msg-1", "claude-opus-4-1", 1000, 2000, 3000, 50),
                asst_usage_msg(iso_at("2026-11-18", 20), "shared-msg-2", "claude-opus-4-1", 500, 0, 1000, 200),
                asst_usage_msg(iso_at("2026-11-18", 30), "", "claude-sonnet-4-5", 10, 0, 0, 10),
            ],
        )
        write_jsonl(
            proj / "fork-child.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "原本對話", promptSource="typed"),
                # shared-msg-1 在分岔檔有更完整的 output_tokens（應取 max=400，不翻倍）
                asst_usage_msg(iso_at("2026-11-18", 10), "shared-msg-1", "claude-opus-4-1", 1000, 2000, 3000, 400),
                asst_usage_msg(iso_at("2026-11-18", 20), "shared-msg-2", "claude-opus-4-1", 500, 0, 1000, 200),
                # 無 id 匿名紀錄以 (file_path, line_no) 區分，不得被誤併
                asst_usage_msg(iso_at("2026-11-18", 40), "", "claude-sonnet-4-5", 20, 0, 0, 30),
                asst_usage_msg(iso_at("2026-11-18", 50), "child-only-1", "claude-opus-4-1", 100, 0, 0, 100),
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        m4 = rep["metric4_tokens_and_models"]
        qr = rep["quality_receipt"]

        # shared-msg-1: (1000, 2000, 3000, 400) = 6400
        # shared-msg-2: (500, 0, 1000, 200) = 1700
        # child-only-1: (100, 0, 0, 100) = 200
        # anon parent: (10, 0, 0, 10) = 20
        # anon child: (20, 0, 0, 30) = 50
        # 合計 = (1630, 2000, 4000, 740) = 8370
        self.assertEqual(m4["main"]["input_tokens"], 1630)
        self.assertEqual(m4["main"]["cache_creation_input_tokens"], 2000)
        self.assertEqual(m4["main"]["cache_read_input_tokens"], 4000)
        self.assertEqual(m4["main"]["output_tokens"], 740)
        self.assertEqual(m4["main"]["total"], 8370)
        self.assertEqual(m4["combined"]["total"], 8370)
        self.assertEqual(m4["by_model_family"]["opus/pro"]["total"], 8300)
        self.assertEqual(m4["by_model_family"]["sonnet/flash"]["total"], 70)
        self.assertEqual(qr["cross_file_deduped_messages"], 2)

    def test_post_review_tool_result_timestamp_and_failed_edits(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-rev-timing"
        # 案例 1：審查於 T+0 派出、T+12m (720s) 回報（且回報內容 >32KB），
        # T+65m (3900s，距派出 >60m 但距回報 53m) 的第 3 次連改應計入
        big_review_body = "review finding " * 3000
        write_jsonl(
            proj / "case1-delayed-return.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "審查跑 12 分鐘", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 0), "c1-m1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("rev-delay", "code-reviewer")]),
                tool_result_msg(iso_at("2026-11-18", 720), "rev-delay", content=big_review_body),
                asst_usage_msg(iso_at("2026-11-18", 1200), "c1-m2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c1-e1", "/p/a.py")]),
                tool_result_msg(iso_at("2026-11-18", 1205), "c1-e1"),
                asst_usage_msg(iso_at("2026-11-18", 2400), "c1-m3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c1-e2", "/p/b.py")]),
                tool_result_msg(iso_at("2026-11-18", 2405), "c1-e2"),
                asst_usage_msg(iso_at("2026-11-18", 3900), "c1-m4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c1-e3", "/p/c.py")]),
                tool_result_msg(iso_at("2026-11-18", 3905), "c1-e3"),
            ],
        )
        # 案例 2：審查沒有收到 tool_result（未完成），後續 3 次自改不觸發
        write_jsonl(
            proj / "case2-unreturned-review.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "未回報審查", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 0), "c2-m1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("rev-lost", "security-reviewer")]),
                asst_usage_msg(iso_at("2026-11-18", 10), "c2-m2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c2-e1", "/p/x.py")]),
                asst_usage_msg(iso_at("2026-11-18", 20), "c2-m3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c2-e2", "/p/y.py")]),
                asst_usage_msg(iso_at("2026-11-18", 30), "c2-m4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c2-e3", "/p/z.py")]),
            ],
        )
        # 案例 3：3 次程式碼 Edit 中有 1 次 is_error=true（僅剩 2 次成功），不計入「≥3 次連改」且排除於主 session 自改次數
        write_jsonl(
            proj / "case3-failed-edit.jsonl",
            [
                user_msg(iso_at("2026-11-18", 0), "其中一次編輯失敗", promptSource="typed"),
                asst_usage_msg(iso_at("2026-11-18", 0), "c3-m1", "claude-opus-4-1", 10, 0, 0, 10, [tool_agent("rev-ok", "python-reviewer")]),
                tool_result_msg(iso_at("2026-11-18", 60), "rev-ok"),
                asst_usage_msg(iso_at("2026-11-18", 100), "c3-m2", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c3-e1", "/p/a.py")]),
                tool_result_msg(iso_at("2026-11-18", 105), "c3-e1"),
                asst_usage_msg(iso_at("2026-11-18", 200), "c3-m3", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c3-e2", "/p/b.py")]),
                tool_result_msg(iso_at("2026-11-18", 205), "c3-e2", is_error=True, content="old_string not found"),
                asst_usage_msg(iso_at("2026-11-18", 300), "c3-m4", "claude-opus-4-1", 10, 0, 0, 10, [tool_edit("c3-e3", "/p/c.py")]),
                tool_result_msg(iso_at("2026-11-18", 305), "c3-e3"),
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        m1 = rep["metric1_self_edit_vs_impl"]["orchestrator_only"]
        m2 = rep["metric2_post_review_60m_bursts"]

        # 只有案例 1 命中 60m 連改（案例 2 無回報、案例 3 扣掉失敗編輯僅剩 2 次）
        self.assertEqual(m2["orchestrator_only_episodes_ge3_within_60m"], 1)
        self.assertEqual(m2["orchestrator_only_unique_sessions_within_60m"], 1)
        self.assertEqual(m2["orchestrator_only_episodes_ge3_any_window"], 1)
        self.assertEqual(m2["top_samples"][0]["code_edits_within_60m"], 3)
        # 成功程式碼編輯數：case1(3) + case2(3) + case3(2) = 8（不含 case3 的 1 次 is_error=true）
        self.assertEqual(m1["edits_code"], 8)
        self.assertEqual(m1["edits_all"], 8)

    def test_blank_lines_and_non_session_widget_log_excluded(self):
        proj = self.root / "-Users-linhancheng-Desktop-projects-clean"
        write_jsonl(
            proj / "clean-1.jsonl",
            [
                "",
                "   ",
                user_msg(iso_at("2026-11-18", 0), "空白行不算壞行", promptSource="typed"),
                "\t",
                asst_usage_msg(iso_at("2026-11-18", 10), "cl-1", "claude-opus-4-1", 10, 0, 0, 10),
            ],
        )
        # 非 session 的 widget-log 目錄即使有損毀行也不應掃入或計入 parse error
        write_jsonl(
            self.root / "widget-log" / "2026-11.jsonl",
            [
                '{"ts":"2026-11-18T12:00:00Z","cpu":12}',
                '{"broken-interleaved-widget-log',
                'tail-fragment"}',
            ],
        )

        rep = st.analyze_projects(self.root, days=30, trend_months=4, ref_date=self.ref_date)
        qr = rep["quality_receipt"]
        self.assertEqual(qr["window_sessions_total"], 1)
        self.assertEqual(qr["excluded_automated_sessions"], 0)
        self.assertEqual(qr["json_line_parse_errors"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
