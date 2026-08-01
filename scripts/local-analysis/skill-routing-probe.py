#!/usr/bin/env python3
"""skill-routing-probe.py — 一次性量測「該觸發的 skill 真的會被選中嗎」（2026-08-01，抽件 5）。

## 為什麼要這支

既有三個機制都在檢查 description 這段**文字本身**：長度閘（寫入時擋太短）、每日 LLM 巡檢
（讀鑑別力）、每日碰撞偵測（兩兩相似度）。沒有一個在測**實際選擇行為**——兩個 skill 文字
不撞、各自夠長夠具體、LLM 讀起來都清楚，但真的餵一個任務進來仍可能選錯或兩個都沒選。
使用者 2026-07-12（activation-cases）與 2026-07-31（trigger-health 的「已知限制」段）
各記過一次這個缺口，兩次都擱置成「另案」。

## 題目從哪來（這是本設計的核心）

**不由 LLM 出題**。題庫是從 session jsonl 挖出來的真實配對：使用者當時實際打的那句話，
以及那句話當下實際被 invoke 的 skill。1,282 組、涵蓋 116 個 skill。

OpenSpace 的反例正是這一點：它的 eval_plan 由寫 skill 的那個 LLM 自己出題（出題者＝考生），
而它的 prompt 第 719-721 行明文禁止 `-enhanced` 疊名、還把 `panel-component-enhanced-enhanced`
寫成反例，產物仍長出 37 個 `-enhanced`、最深三層。**寫在 prompt 裡的規則不會自我執行。**

## 這支測的是什麼（誠實聲明）

測的是**行為一致性**：description 現況下，同一句話還會不會選到當初那個 skill。
**不是正確性**——歷史上選的那個未必是對的，當時可能就選錯了。所以：
  - 命中率低 → 確定有問題（要嘛現在錯、要嘛當初錯），值得看
  - 命中率高 → 只說明穩定，不證明當初的選擇是對的

## 副作用防護

`claude -p` 會**真的執行**選中的 skill——首次可行性測試就讓 save-session 實際寫了一個
session 檔（已清除）。所以探針一律封掉會動狀態的工具，只留「選哪個 skill」這一步。

用法：
  python3 skill-routing-probe.py --plan          # 只印題目、不跑（先看要花多少）
  python3 skill-routing-probe.py --run           # 實跑
  --pairs N   每個 skill 抽幾題（預設 3）
  --skills a,b,c   指定要測的 skill（預設取碰撞偵測最相近那幾組）
"""

import argparse
import json
import random
import re
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HOME = Path.home()
PROJECTS = HOME / ".claude" / "projects"
OUT = HOME / "code" / "social-info" / "reports" / "local-analysis" / "skill-routing-probe.json"

BLOCKED_TOOLS = [
    "Write", "Edit", "NotebookEdit", "Bash", "Agent", "Task",
    "TaskCreate", "TaskUpdate", "TaskStop", "Workflow", "CronCreate", "CronDelete",
]

DEFAULT_TARGETS = [
    "save-session", "resume-session",
    "bitbucket-pr-review", "bitbucket-pr-mutation",
    "check-my-stack", "github-repo-research",
]


def mine_pairs():
    pairs = defaultdict(list)
    for p in PROJECTS.rglob("*.jsonl"):
        if "/subagents/" in str(p):
            continue
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        last_user = None
        for line in lines:
            if '"Skill"' not in line and '"type":"user"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            msg = d.get("message") or {}
            if d.get("type") == "user":
                c = msg.get("content")
                txt = None
                if isinstance(c, str):
                    txt = c
                elif isinstance(c, list):
                    txt = " ".join(
                        b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
                    )
                if txt and txt.strip() and not txt.lstrip().startswith("<"):
                    last_user = txt.strip()
            elif d.get("type") == "assistant":
                for b in (msg.get("content") or []):
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if b.get("name") != "Skill":
                        continue
                    s = (b.get("input") or {}).get("skill")
                    if s and last_user and 12 < len(last_user) < 260:
                        if "\n" not in last_user:
                            pairs[s.split(":")[-1]].append(last_user)
                        last_user = None
    return pairs


def build_plan(targets, per_skill, seed=20260801):
    """⚠️ 必須濾掉「原話裡直接打了 /<skill-name>」的配對。那是手打 slash 明示，答對只證明
    模型看得懂斜線、完全不測 description routing——留著會系統性灌高命中率。首版計畫 16 題
    裡有 5 題是這種，佔 31%。"""
    random.seed(seed)
    pairs = mine_pairs()
    plan = []
    skipped = defaultdict(int)
    for t in targets:
        qs = list(dict.fromkeys(pairs.get(t, [])))
        explicit = re.compile(r"/" + re.escape(t) + r"\b")
        usable = []
        for q in qs:
            if explicit.search(q):
                skipped[t] += 1
                continue
            usable.append(q)
        if not usable:
            continue
        for q in random.sample(usable, min(per_skill, len(usable))):
            plan.append({"expected": t, "question": q})
    return plan, {k: len(v) for k, v in pairs.items()}, dict(skipped)


def run_one(question, timeout=180):
    cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose",
           "--disallowedTools"] + BLOCKED_TOOLS
    try:
        r = subprocess.run(
            cmd, input=question, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"picked": None, "tools": [], "error": "timeout"}
    picked, tools = None, []
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except Exception:
            continue
        for b in ((d.get("message") or {}).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tools.append(b.get("name"))
                if b.get("name") == "Skill" and picked is None:
                    picked = ((b.get("input") or {}).get("skill") or "").split(":")[-1]
    return {"picked": picked, "tools": tools[:8], "error": None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--pairs", type=int, default=3)
    ap.add_argument("--skills", default=None)
    args = ap.parse_args()

    targets = args.skills.split(",") if args.skills else DEFAULT_TARGETS
    plan, inventory, skipped = build_plan(targets, args.pairs)

    if args.plan or not args.run:
        print(f"題庫涵蓋 {len(inventory)} 個 skill、共 {sum(inventory.values())} 組真實配對")
        if skipped:
            tot = sum(skipped.values())
            print(f"已排除 {tot} 題「原話直接打 /<skill>」（手打明示、不測 routing）：{skipped}")
        print(f"\n本次計畫 {len(plan)} 題（每 skill 最多 {args.pairs} 題）：\n")
        for i, c in enumerate(plan, 1):
            print(f"  {i:2d}. [{c['expected']}] {c['question'][:80]}")
        print(f"\n封掉的工具：{' '.join(BLOCKED_TOOLS)}")
        print("預估：每題約 60-140 秒（首發實測 135s）")
        return

    results = []
    t0 = time.time()
    for i, c in enumerate(plan, 1):
        r = run_one(c["question"])
        hit = r["picked"] == c["expected"]
        results.append({**c, **r, "hit": hit})
        mark = "✅" if hit else ("⏱" if r["error"] else "❌")
        print(f"  {mark} [{i}/{len(plan)}] 期望 {c['expected']} → 實際 {r['picked']}", flush=True)

    hits = sum(1 for r in results if r["hit"])
    none = sum(1 for r in results if r["picked"] is None)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=1))
    print(f"\n命中 {hits}/{len(results)}（{hits/max(len(results),1):.0%}）、完全沒選 skill {none} 題")
    print(f"耗時 {time.time()-t0:.0f}s，明細寫入 {OUT}")


if __name__ == "__main__":
    sys.exit(main())
