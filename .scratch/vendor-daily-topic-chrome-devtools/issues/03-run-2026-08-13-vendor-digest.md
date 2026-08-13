# 03 — 執行 2026-08-13 vendor 話題分析

**What to build:** 使用 vendor workflow 完成 2026-08-13 stage-2 digest，依既有中止、audit 與 verify protocol 處理所有結果，產出可交付的今日話題分析。

**Blocked by:** 02 — 建立 vendor daily-topic workflow

**Status:** ready-for-agent

**Needs:** 當日 raw md 已存在且內容成形；Chrome DevTools MCP 可用；必要時可讀目前 Chrome 登入態。

**TDD:** waived

**TDD waiver:** non-executable-artifact

**TDD waiver approved:** ticket-breakdown-user-approved

- [ ] Workflow 未中止；若 guard 中止則依既有 protocol 處理，不繞過門檻
- [ ] WriteDigest 成功產出當日 HTML
- [ ] 每輪 audit finding 均逐筆查證與修正，續輪規則與三輪上限照既有 protocol
- [ ] 最終 URL audit 與機械 HARD 檢查 exit 0
- [ ] 每筆機械 SOFT 警告均判定為真錯並修正，或明列誤報理由
- [ ] 最終回報包含 audit 輪次表、第 1 輪 lens 分布、HARD／SOFT 數、PROBES writeback 與未跑 B/C lens 的覆蓋缺口
