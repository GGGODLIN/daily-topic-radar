import assert from 'node:assert/strict'
import fs from 'node:fs'

const workflowPath = process.env.LOCAL_ANALYSIS_WORKFLOW ?? '/Users/linhancheng/.claude/workflows/local-analysis.js'
const source = fs.readFileSync(workflowPath, 'utf8').replace('export const meta =', 'const meta =')
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const runWorkflow = new AsyncFunction('args', 'agent', 'parallel', 'phase', 'log', source)

const run = async (overrides = {}) => {
  const calls = []
  const agent = async (prompt, options) => {
    calls.push({ prompt, options })
    if (Object.hasOwn(overrides, options.label)) return overrides[options.label]
    if (options.label === 'evidence-level-due') {
      return { date: '2026-09-26', due: false, last_success_date: '2026-09-20', days_since: 6 }
    }
    if (options.schema?.properties?.summary) {
      return { summary: `${options.label} 摘要`, report_markdown: `# ${options.label}\n\n掃描完成。\n`, deferred_writeback: '' }
    }
    if (options.label === 'llm-report-check') return { present: [], missing: [] }
    return { ok: true, silent: false }
  }
  const result = await runWorkflow(
    { date: '2026-09-26', weekday: 6, external_recap: true },
    agent,
    async (tasks) => Promise.all(tasks.map(async (task) => { try { return await task() } catch { return null } })),
    () => {},
    () => {},
  )
  return { result, calls }
}

const { result, calls } = await run({
  memory: { summary: '有狀態待寫回', report_markdown: '# Memory Audit\n\n## Summary\n正常\n', deferred_writeback: '掃描 cutoff 與狀態值；依原 source 在報告落檔後處理。' },
})
assert.ok(Array.isArray(result.pending_reports), 'LLM reports must be handed to main instead of requiring an agent Write')
const memory = result.pending_reports.find((report) => report.key === 'memory')
assert.equal(memory.report_path, '/Users/linhancheng/code/social-info/reports/local-analysis/2026-09-26-memory.md')
assert.ok(memory.report_markdown.startsWith('# Memory Audit'))
assert.ok(memory.deferred_writeback.includes('cutoff'))
assert.ok(!result.ran.includes('memory'), 'a returned report is not a persisted report')
assert.ok(!result.channels.some((channel) => channel.key === 'memory'))
assert.ok(!result.failed.includes('memory'), 'a valid report waiting for main is not a scan failure')
assert.ok(result.ran.includes('failure-mode'), 'shell receipt handling stays unchanged')
assert.equal(result.external_channels[0].key, 'recap')
assert.ok(!calls.some(({ options }) => ['llm-report-check', 'memory-recovery'].includes(options.label)))
const prompt = calls.find(({ options }) => options.label === 'memory').prompt
assert.ok(prompt.includes('不要寫報告檔'))
assert.ok(prompt.includes('deferred_writeback'))
assert.ok(prompt.includes('不要提前提交'))
console.log(JSON.stringify({ case: 'main owns report persistence and dependent writeback', status: 'PASS' }))

const invalid = await run({
  memory: null,
  'wiki-cross-link': { summary: '宣稱成功', report_markdown: '  ', deferred_writeback: '' },
  'wiki-graduation': { summary: '只回摘要' },
  'wiki-lint': { summary: '', report_markdown: '# Report', deferred_writeback: '' },
  'wiki-stale': { summary: '摘要', report_markdown: '# Report', deferred_writeback: ['not a string'] },
})
for (const key of ['memory', 'wiki-cross-link', 'wiki-graduation', 'wiki-lint', 'wiki-stale']) {
  assert.ok(invalid.result.failed.includes(key), `${key} must fail closed without a valid report packet`)
  assert.ok(!invalid.result.ran.includes(key))
  assert.ok(!invalid.result.pending_reports.some((report) => report.key === key))
}
assert.equal(invalid.calls.filter(({ options }) => options.label === 'memory').length, 1)
console.log(JSON.stringify({ case: 'missing and malformed report packets never reuse stale files', status: 'PASS' }))
