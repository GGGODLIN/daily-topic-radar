#!/usr/bin/env python3
"""skill-evidence-audit.py 的回歸 fixture。

鎖住的線：
  (1) 四個訊號各自抓得到、缺的抓不到（訊號定義是這支的全部判斷力）
  (2) grade 分級門檻正確
  (3) origin 解析正確——**分 origin 是本檔最重要的修正**：不分的話 31 個外部 skill 被誤判
      「最該退役」，分了之後收斂成 2 個。這條迴歸防止有人把 origin 拿掉。
  (4) INVENTORY 表格雜訊（分隔列 / 表頭 / 粗體名）不得被當成 skill 名
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "sea", Path(__file__).parent / "skill-evidence-audit.py"
)
sea = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sea)

PASS = FAIL = 0


def check(name, want, got):
    global PASS, FAIL
    if want == got:
        print(f"  PASS  {name}")
        PASS += 1
    else:
        print(f"  FAIL  {name}\n        want {want!r}\n        got  {got!r}")
        FAIL += 1


print("\n=== 訊號偵測 ===")
check("踩坑段", True, sea.doc_signals("## 踩過的坑\n- 撞過 X")["pit"])
check("反例也算踩坑類", True, sea.doc_signals("反例：不要這樣做")["pit"])
check("量化數字（次）", True, sea.doc_signals("實際跑了 47 次")["num"])
check("量化數字（百分比）", True, sea.doc_signals("命中率 91%")["num"])
check("量化數字（分數式）", True, sea.doc_signals("通過 18/18")["num"])
check("實測提及", True, sea.doc_signals("2026-08-01 實測驗證")["test"])
check("fixture 也算實測", True, sea.doc_signals("備 fixture 回歸")["test"])
check("具體日期", True, sea.doc_signals("2026-07-12 拍板")["date"])
bare = sea.doc_signals("這是一個純描述的 skill，沒有任何證據。")
check("純散文四個訊號全無", 0, sum(bare.values()))

print("\n=== 分級門檻 ===")
check("4 訊號 → strong", "strong", sea.grade({"a": 1, "b": 1, "c": 1, "d": 1}))
check("3 訊號 → strong", "strong", sea.grade({"a": 1, "b": 1, "c": 1, "d": 0}))
check("2 訊號 → weak", "weak", sea.grade({"a": 1, "b": 1, "c": 0, "d": 0}))
check("1 訊號 → weak", "weak", sea.grade({"a": 1, "b": 0, "c": 0, "d": 0}))
check("0 訊號 → bare", "bare", sea.grade({"a": 0, "b": 0, "c": 0, "d": 0}))

print("\n=== origin 解析（本檔最重要的修正，不得退化）===")
with tempfile.TemporaryDirectory() as td:
    sd = Path(td)
    (sd / "INVENTORY.md").write_text(
        "\n".join(
            [
                "# Skill Inventory",
                "",
                "| Skill | Origin | Audit | 備註 |",
                "| ----- | ------ | ----- | ---- |",
                "| my-skill | self-written | yes | 自寫 |",
                "| their-skill | Anthropic 官方 | no | clone |",
                "| **bold-skill** | self-written | yes | 粗體名 |",
                "| forked-one | Anthropic 官方 (frozen fork) | yes (patched) | fork |",
            ]
        )
    )
    o = sea.origins(sd)
    check("self-written → self", "self", o.get("my-skill"))
    check("外部 → cloned", "cloned", o.get("their-skill"))
    check("粗體名去星號", "self", o.get("bold-skill"))
    check("frozen fork 仍算 cloned", "cloned", o.get("forked-one"))
    check("表頭不得被當 skill", None, o.get("Skill"))
    check("分隔列不得被當 skill", None, o.get("-----"))

print("\n=== collect：只收有 SKILL.md 的目錄 ===")
with tempfile.TemporaryDirectory() as td:
    sd = Path(td)
    (sd / "has-skill").mkdir()
    (sd / "has-skill" / "SKILL.md").write_text("## 踩過的坑\n2026-08-01 實測 5 次")
    (sd / "no-skill").mkdir()
    (sd / "no-skill" / "README.md").write_text("x")
    (sd / "INVENTORY.md").write_text("| Skill | Origin |\n| has-skill | self-written |")
    rows = sea.collect(sd)
    check("只收到 1 個", 1, len(rows))
    check("名稱正確", "has-skill", rows[0]["skill"])
    check("四訊號全中 → strong", "strong", rows[0]["evidence"])

print("\n=== Markdown 表格邊界與多表格狀態機 ===")
with tempfile.TemporaryDirectory() as td:
    sd = Path(td)
    (sd / "INVENTORY.md").write_text(
        "\n".join(
            [
                "| Skill | Origin |",
                "| ----- | ------ |",
                "|nospace-self|self-written|",
                "  | indented-self | self-written |",
                "| [linked-skill](linked-skill/SKILL.md) | self-written |",
                "| **bold-annotated** (patched) | self-written |",
                r"| pipe-name \| alias | self-written |",
                "| blank-origin |   |",
                "| missing-col |",
                "",
                "Skill | Origin",
                "--- | ---",
                "outerless-self | self-written",
                "",
                "```markdown",
                "| Skill | Origin |",
                "| --- | --- |",
                "| fenced-fake | self-written |",
                "```",
                "",
                "## 無 Origin 欄的其他表格（不得污染或覆寫）",
                "| Skill | 結論 | 證據 |",
                "| --- | --- | --- |",
                "| nospace-self | 保留 | 實測 |",
                "| unrelated-row | 略 | x |",
                "",
                "| 情境 | 動作 |",
                "| --- | --- |",
                "| 新工具 | 紀錄 |",
            ]
        ),
        encoding="utf-8",
    )
    o = sea.origins(sd)
    check("無空白前導 pipe → self", "self", o.get("nospace-self"))
    check("縮排表格列 → self", "self", o.get("indented-self"))
    check("無外層 pipe 表格 → self", "self", o.get("outerless-self"))
    check("Markdown 連結展開 → self", "self", o.get("linked-skill"))
    check("粗體附註記去星號 → self", "self", o.get("bold-annotated"))
    check("跳脫 pipe 不錯欄 → self", "self", o.get("pipe-name"))
    check("空 Origin 欄不誤判 cloned", None, o.get("blank-origin"))
    check("缺 Origin 欄不誤判 cloned", None, o.get("missing-col"))
    check("code fence 內表格忽略", None, o.get("fenced-fake"))
    check("非 Origin 表格不污染鍵名", None, o.get("unrelated-row"))
    check("中文說明表不污染鍵名", None, o.get("情境"))

print("\n=== 異常檔案系統與 UTF-8 bytes ===")
with tempfile.TemporaryDirectory() as td:
    sd = Path(td)
    (sd / "INVENTORY.md").mkdir()
    check("INVENTORY.md 為目錄不崩潰", {}, sea.origins(sd))
    missing = sd / "does-not-exist"
    check("不存在 skills-dir collect 回空陣列", [], sea.collect(missing))
    (sd / "utf8-skill").mkdir()
    (sd / "utf8-skill" / "SKILL.md").write_text("中文測試", encoding="utf-8")
    utf8_rows = sea.collect(sd)
    check("bytes 回報真實 UTF-8 位元組數 12", 12, utf8_rows[0]["bytes"])

print("\n=== 訊號精準度邊界 ===")
check("斜線日期 2026/08/01 不誤報量化數字", False, sea.doc_signals("2026/08/01 更新紀錄")["num"])
check("大寫 Fixture 命中 test", True, sea.doc_signals("## Fixture 說明")["test"])
check("大寫 Probe 命中 test", True, sea.doc_signals("Probe verified")["test"])
check("長編號 12026-99-999 不誤報日期", False, sea.doc_signals("編號 12026-99-999")["date"])
check("無效日曆日期 2026-02-31 不誤報日期", False, sea.doc_signals("日期 2026-02-31")["date"])

print("\n=== JSONL / settings.json 畸形節點容錯 ===")
import io
import contextlib
import subprocess
with tempfile.TemporaryDirectory() as td:
    fake_home = Path(td)
    proj = fake_home / ".claude" / "projects" / "p1"
    proj.mkdir(parents=True)
    (proj / "bad.jsonl").write_text(
        '["Skill"]\n'
        '{"type":"assistant","message":null,"extra":"Skill"}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":"not-a-dict"}]}}\n'
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"ok-skill"}}]}}\n',
        encoding="utf-8",
    )
    (fake_home / ".claude" / "settings.json").write_text('{"skillOverrides": null}\n', encoding="utf-8")
    skills_dir = fake_home / ".claude" / "skills"
    (skills_dir / "ok-skill").mkdir(parents=True)
    (skills_dir / "ok-skill" / "SKILL.md").write_text("純文字", encoding="utf-8")
    res = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).parent / "skill-evidence-audit.py"), "--skills-dir", str(skills_dir)],
        env={"HOME": str(fake_home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    check("畸形 JSONL 與 null skillOverrides 正常完成", 0, res.returncode)
    res_missing = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).parent / "skill-evidence-audit.py"), "--skills-dir", str(fake_home / "no-such-dir")],
        env={"HOME": str(fake_home), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    check("CLI --skills-dir 不存在乾淨非 0 退出無 traceback", (True, False), (res_missing.returncode != 0, "Traceback" in res_missing.stderr))

print("\n=== Wave 44 盲審與 Step D 邊界回歸（非法 UTF-8 / locale / 512 KiB 分塊 / fork 退回）===")
import json
with tempfile.TemporaryDirectory() as td:
    fh = Path(td)
    proj = fh / ".claude" / "projects" / "edge"
    proj.mkdir(parents=True)
    skills_dir = fh / ".claude" / "skills"
    for sk in ("ce_a", "ce_b", "straddle", "big3", "lonecr", "noeof", "esc_lit", "café", "caf"):
        (skills_dir / sk).mkdir(parents=True)
        (skills_dir / sk / "SKILL.md").write_text(f"# {sk}\n", encoding="utf-8")

    # 非法 UTF-8 反例 A/B + CRLF / 孤立 CR / 缺尾換行 / \u 跳脫
    (proj / "utf8_edges.jsonl").write_bytes(
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Sk\xffill","input":{"skill":"ce_a"}}]}}\n'
        b'{"type":"assistant","message":{"content":[{"type":"tool\xff_use","name":"Skill","input":{"skill":"ce_b"}}]}}\r'
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"lonecr"}}]}}\r\n'
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"\\u0053kill","input":{"skill":"esc_lit"}}]}}\n'
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"esc_lit","k":"\\u0053kill"}}]}}\n'
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"noeof"}}]}}'
    )
    # 512 KiB 邊界跨塊 needle + >1 MiB 跨三塊長行 + 剛好 512 KiB 檔 + 空檔
    pad_512k = b"a" * (512 * 1024 - 64)
    (proj / "straddle.jsonl").write_bytes(
        b'{"type":"assistant","p":"' + pad_512k + b'","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"straddle"}}]}}\n'
        b'{"type":"assistant","p":"' + (b"b" * (1200 * 1024)) + b'","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"big3"}}]}}\n'
    )
    (proj / "exact512k.jsonl").write_bytes(b" " * (512 * 1024))
    (proj / "empty.jsonl").write_bytes(b"")
    # 補齊 16+ 檔觸發 fork 多行程
    for i in range(18):
        (proj / f"shard_{i:02d}.jsonl").write_text(
            '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"ce_a"}}]}}\n',
            encoding="utf-8",
        )

    r_edge = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).parent / "skill-evidence-audit.py"), "--skills-dir", str(skills_dir), "--json"],
        env={"HOME": str(fh), "PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
        capture_output=True, text=True,
    )
    check("UTF-8/分塊/多行程回歸執行成功", 0, r_edge.returncode)
    got_map = {row["skill"]: row["invokes"] for row in json.loads(r_edge.stdout)}
    check("反例 A Sk\\xffill + 18 分片計 19", 19, got_map["ce_a"])
    check("反例 B tool\\xff_use 計 1", 1, got_map["ce_b"])
    check("512 KiB 跨塊 needle 計 1", 1, got_map["straddle"])
    check(">1 MiB 跨三塊長行計 1", 1, got_map["big3"])
    check("孤立 CR 計 1", 1, got_map["lonecr"])
    check("缺尾換行計 1", 1, got_map["noeof"])
    check("純 \\u0053kill 不計、含字面 Skill 計 1", 1, got_map["esc_lit"])

    # 非 UTF-8 locale 退回 text mode 與基線等價
    fh_loc = Path(td) / "loc_home"
    proj_loc = fh_loc / ".claude" / "projects" / "p1"
    proj_loc.mkdir(parents=True)
    (proj_loc / "latin1.jsonl").write_bytes(
        b'{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Skill","input":{"skill":"caf\xe9"}}]}}\n'
    )
    r_loc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).parent / "skill-evidence-audit.py"), "--skills-dir", str(skills_dir), "--json"],
        env={"HOME": str(fh_loc), "PATH": "/usr/bin:/bin", "LC_ALL": "en_US.ISO8859-1", "LANG": "en_US.ISO8859-1"},
        capture_output=True,
    )
    check("Latin-1 locale 正常退出", 0, r_loc.returncode)
    loc_rows = json.loads(r_loc.stdout.decode("latin-1"))
    loc_map = {row["skill"]: row["invokes"] for row in loc_rows}
    check("Latin-1 locale 計進 café 而非 caf", (1, 0), (loc_map.get("café"), loc_map.get("caf")))

print(f"\n---- {PASS} passed, {FAIL} failed ----")
sys.exit(1 if FAIL else 0)
