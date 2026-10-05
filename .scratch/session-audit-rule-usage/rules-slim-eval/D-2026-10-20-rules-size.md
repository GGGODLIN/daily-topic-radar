# Rules 檔長度健康度 Weekly — 2026-10-14

**門檻（P-len、harness-audit-2026-07-03 拍板）**：CLAUDE.md ≤ 14000 bytes、rules 檔 ≤ 9728 bytes、scope 總和 ≤ 46080 bytes
**Scope**：~/.claude/CLAUDE.md + rules/common/*.md + rules/external/*.md（不含 MEMORY.md、project CLAUDE.md）
## 超標檔案（1）

| 檔案 | 現況 bytes | Cap | 超標量 |
|---|---|---|---|
| `/Users/linhancheng/.claude/rules/common/dispatch-and-verify.md` | 10540 | 9728 | 超 812 bytes |

## 🎯 建議處理

找出該砍哪幾段、產草稿逐段拍板：`/rules-slim`（手動觸發，排檔不會自動跑它）

## 🪦 零使用規則候選（0）
本週沒有候選
