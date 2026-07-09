# Phase 0 Baseline — GitHub triage spec Phase 3 前

**Date measured**: 2026-07-09
**Purpose**: Phase 3 完成後 secondary metric 對照組（相比 Phase 0 提升 3-5 倍的門檻）
**Source**: 過去 14 天 `reports/digest-*.html`「🛠 GitHub 倉庫觀察」段 verdict class count

2026-07-08: hi=2 med=2
2026-07-07: hi=2 med=2
2026-07-06: hi=1 med=2
2026-07-05: hi=0 med=3
2026-07-04: hi=0 med=3
2026-07-03: hi=0 med=4
2026-07-02: hi=0 med=5
2026-07-01: hi=0 med=4
2026-06-30: hi=0 med=5
2026-06-29: hi=1 med=5
2026-06-28: hi=1 med=6
2026-06-27: hi=1 med=5
2026-06-26: hi=1 med=9

=== Phase 0 Baseline (13 days) ===
avg hi / day:  0.7
avg med / day: 4.2
avg hi+med / day: 4.9

## N-value dry-run result (2026-07-09)

Ran `scripts/dry-run-n-value.py` against production `state.db`（8,544 items with `fetched_at` within past 30 天）:

- N=15: 4,329 items / 30 days (~144.3 / day)
- N=30: 423 items / 30 days (~14.1 / day)  ← 選定
- N=60: 165 items / 30 days (~5.5 / day)

**Rationale**：N=15 每天 ~144 個 resurface、會把 digest 灌爆；N=30 落在 <20/day 門檻內（~14.1/day），resurface 頻率合理又不會太保守錯過 30+ 天前看過的 stable repo；維持既有 `Deduper(db, resurface_days=30)` 預設，於 `pipeline.py` 補 `RESURFACE_DAYS = 30` module-level 常數當唯一真相來源。
