#!/bin/bash
set -euo pipefail

WORKFLOW="/Users/linhancheng/.claude/workflows/local-analysis.js"

grep -F '不要寫報告檔' "$WORKFLOW" >/dev/null
grep -F 'report_markdown（完整正文，不是摘要）' "$WORKFLOW" >/dev/null
grep -F 'deferred_writeback' "$WORKFLOW" >/dev/null
grep -F '不要提前提交' "$WORKFLOW" >/dev/null
grep -F 'pending_reports:' "$WORKFLOW" >/dev/null
node --input-type=module - "$WORKFLOW" <<'NODE'
import fs from 'node:fs'

const workflow = fs.readFileSync(process.argv[2], 'utf8')
const start = workflow.indexOf('const llmPrompt = (c) => {')
const end = workflow.indexOf('\n}\n\nconst shellPrompt', start)
if (start < 0 || end < 0) throw new Error('llmPrompt function not found')
const expression = workflow.slice(start + 'const llmPrompt = '.length, end + 2)
const llmPrompt = Function('OUT_DIR', 'DATE', `return ${expression}`)('/tmp/reports', '2026-08-19')
const recap = llmPrompt({ key: 'recap', src: '/tmp/recap-daily.sh' })
const ordinary = llmPrompt({ key: 'wiki-lint', src: '/tmp/wiki-lint-daily.sh' })
for (const phrase of [
  '舊 claude -p wrapper',
  'stdout 報告規則在本子任務改為 report_markdown',
  '先完成 source 指示要求的兩個 ledger append',
  '最後只用 StructuredOutput 回傳資料',
]) {
  if (!recap.includes(phrase)) throw new Error(`recap completion contract missing: ${phrase}`)
}
if (ordinary.includes('舊 claude -p wrapper')) throw new Error('recap-specific contract leaked into ordinary channel')
for (const phrase of ['已派出的單一 channel 子任務', '不要重新啟動 /daily-local', '分析來源唯讀', '不要寫報告檔']) {
  if (!ordinary.includes(phrase)) throw new Error(`channel scope contract missing: ${phrase}`)
}
const shellStart = workflow.indexOf('const shellPrompt = (c) => {')
const shellEnd = workflow.indexOf('\n}\n', shellStart)
const shellExpression = workflow.slice(shellStart + 'const shellPrompt = '.length, shellEnd + 2)
const shellPrompt = Function('W', 'DATE', 'FORCE_SHELL', `return ${shellExpression}`)('/tmp/wrappers', '2026-08-19', false)
const shell = shellPrompt({ key: 'failure-mode', src: '/tmp/failure-mode.sh', outfile: '/tmp/report.md' })
for (const phrase of ['已派出的單一 channel 子任務', '不要重新啟動 /daily-local']) {
  if (!shell.includes(phrase)) throw new Error(`shell scope contract missing: ${phrase}`)
}
NODE
if grep -E '用 (Bash|Write 工具)把完整 markdown report 寫到' "$WORKFLOW" >/dev/null; then
  exit 1
fi

printf 'local-analysis workflow report contract: PASS\n'
