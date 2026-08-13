#!/usr/bin/env node

import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

const parseArgs = (argv) => {
  const values = {}
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]
    const value = argv[index + 1]
    if (!key?.startsWith('--') || value == null) throw new Error('arguments must be --key value pairs')
    values[key.slice(2)] = value
  }
  return values
}

const parseDate = (value) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value ?? '')) throw new Error('date must be YYYY-MM-DD')
  const parsed = new Date(`${value}T00:00:00.000Z`)
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) throw new Error('date must be valid')
  return parsed
}

const formatDate = (value) => value.toISOString().slice(0, 10)
const addDays = (value, days) => new Date(value.getTime() + days * 86400000)

const REPORT_TABLE_HEADER = '| timestamp | session | path | 結果 | 類型 | 最短原句 | 說明 |'
const REPORT_TABLE_SEPARATOR = '|---|---|---|---|---|---|---|'
const LIMITATIONS = '本報告只判單則回答文字；每次最多抽樣 20 則；code 不計入 200 字長度門檻，但完整保留在受評回答；未核對回答所述外部事實真偽。'
const VIOLATION_TYPES = new Set(['unsourced-completion', 'unsourced-number', 'unsourced-mechanism', 'doc-as-evidence', 'silent-skip'])
const VIOLATION_ORDER = new Map([...VIOLATION_TYPES].map((type, index) => [type, index]))
const EVIDENCE_V2_START_DATE = '2026-08-14'

const digestText = (value) => crypto.createHash('sha256').update(value).digest('hex')
const digestFile = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const manifestPathFor = (reports, date) => path.join(reports, `${date}-evidence-level-manifest.json`)
const samplesTextPathFor = (reports, date) => path.join(reports, `${date}-evidence-level-samples.txt`)
const reauditSamplesTextPathFor = (reports, date) => path.join(reports, `${date}-evidence-level-reaudit-samples.txt`)
const publicationPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.publish.json`)
const reportPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.md`)
const receiptPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.verified.json`)
const versionPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.version.json`)
const ensurePrivateDirectory = (directory) => {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
  fs.chmodSync(directory, 0o700)
}
const isPrivateFile = (file) => (fs.statSync(file).mode & 0o077) === 0
const hasVerifiedReport = (reports, date) => {
  if (!fs.existsSync(reports) || (fs.statSync(reports).mode & 0o077) !== 0) return false
  const reportPath = reportPathFor(reports, date)
  const manifestPath = manifestPathFor(reports, date)
  const publicationPath = publicationPathFor(reports, date)
  const receiptPath = receiptPathFor(reports, date)
  if (![reportPath, manifestPath, publicationPath, receiptPath].every((file) => fs.existsSync(file) && isPrivateFile(file))) return false
  try {
    const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
    const publication = JSON.parse(fs.readFileSync(publicationPath, 'utf8'))
    const packetValid = receipt.date === date &&
      receipt.report_sha256 === digestFile(reportPath) &&
      receipt.manifest_sha256 === digestFile(manifestPath) &&
      receipt.publication_sha256 === digestFile(publicationPath)
    const receiptVersion = Object.hasOwn(receipt, 'schema_version') ? receipt.schema_version : 1
    const publicationVersion = Object.hasOwn(publication, 'schema_version') ? publication.schema_version : 1
    const expectedVersion = date >= EVIDENCE_V2_START_DATE ? 2 : receiptVersion
    if (!packetValid || receiptVersion !== publicationVersion || receiptVersion !== expectedVersion || ![1, 2].includes(receiptVersion)) return false
    if (receiptVersion === 1) return true
    const samplesTextPath = samplesTextPathFor(reports, date)
    if (!fs.existsSync(samplesTextPath) || !isPrivateFile(samplesTextPath) || receipt.samples_file_sha256 !== digestFile(samplesTextPath)) return false
    if (receipt.reaudit_sample_count === 0) return receipt.reaudit_samples_file_sha256 === null && receipt.reaudit_nonce === null
    const reauditSamplesTextPath = reauditSamplesTextPathFor(reports, date)
    return fs.existsSync(reauditSamplesTextPath) && isPrivateFile(reauditSamplesTextPath) && receipt.reaudit_samples_file_sha256 === digestFile(reauditSamplesTextPath)
  } catch {
    return false
  }
}

const findLastSuccessDate = (reports, date) => {
  if (!fs.existsSync(reports)) return null
  return fs.readdirSync(reports, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name.match(/^(\d{4}-\d{2}-\d{2})-evidence-level\.md$/)?.[1] ?? null)
    .filter((entryDate) => entryDate != null && entryDate <= date && verifiedPacket(reports, entryDate) != null)
    .sort()
    .at(-1) ?? null
}

const buildDuePacket = (reports, date) => {
  const lastSuccessDate = findLastSuccessDate(reports, date)
  if (lastSuccessDate == null) {
    return { date, due: true, last_success_date: null, days_since: null }
  }
  const daysSince = Math.floor((parseDate(date).getTime() - parseDate(lastSuccessDate).getTime()) / 86400000)
  return {
    date,
    due: daysSince >= 7,
    last_success_date: lastSuccessDate,
    days_since: daysSince,
  }
}

const removeCode = (value) => value
  .replace(/```[\s\S]*?```/g, '')
  .replace(/~~~[\s\S]*?~~~/g, '')
  .replace(/^(?: {4}|\t).*$(?:\r?\n)?/gm, '')
  .replace(/`[^`\n]*`/g, '')

const extractText = (content) => {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .filter((item) => item && typeof item === 'object' && item.type === 'text' && typeof item.text === 'string')
    .map((item) => item.text)
    .join('\n')
}

const FULLWIDTH_STOPS = new Set(['，', '；', '。', '！', '？', '、', '：'])

const lineEndFrom = (value, start) => {
  const carriageReturn = value.indexOf('\r', start)
  const newline = value.indexOf('\n', start)
  const candidates = [carriageReturn, newline].filter((index) => index >= 0)
  return candidates.length === 0 ? value.length : Math.min(...candidates)
}

const quotedBoundsAt = (value, start, limit) => {
  const quote = value[start]
  if (quote === '"' || quote === "'") {
    let index = start + 1
    while (index < limit) {
      if (value[index] === '\\') {
        index += Math.min(2, limit - index)
        continue
      }
      if (value[index] === quote) {
        return { contentStart: start + 1, contentEnd: index, after: index + 1 }
      }
      index += 1
    }
    return { contentStart: start + 1, contentEnd: limit, after: limit }
  }

  const escapedQuote = value[start + 1]
  if (value[start] !== '\\' || (escapedQuote !== '"' && escapedQuote !== "'")) return null
  let index = start + 2
  while (index < limit) {
    if (value[index] !== '\\') {
      index += 1
      continue
    }
    let runEnd = index
    while (runEnd < limit && value[runEnd] === '\\') runEnd += 1
    if (runEnd < limit && value[runEnd] === escapedQuote && (runEnd - index) % 4 === 1) {
      return { contentStart: start + 2, contentEnd: index, after: runEnd + 1 }
    }
    index = runEnd < limit && value[runEnd] === escapedQuote ? runEnd + 1 : runEnd
  }
  return { contentStart: start + 2, contentEnd: limit, after: limit }
}

const replaceRanges = (value, ranges) => {
  const ordered = [...ranges].sort((left, right) => left.start - right.start || left.end - right.end)
  let cursor = 0
  let result = ''
  for (const range of ordered) {
    if (range.start < cursor || range.end <= range.start) continue
    result += `${value.slice(cursor, range.start)}[REDACTED]`
    cursor = range.end
  }
  return result + value.slice(cursor)
}

const isCookieNameCharacter = (character) =>
  character != null && !/[=;,\s"'\\，；。！？、]/.test(character)

const cookiePairRanges = (value, start, end) => {
  const ranges = []
  let segmentStart = start
  while (segmentStart < end) {
    let segmentEnd = segmentStart
    while (
      segmentEnd < end &&
      value[segmentEnd] !== ';' &&
      value[segmentEnd] !== ',' &&
      !FULLWIDTH_STOPS.has(value[segmentEnd])
    ) segmentEnd += 1

    let index = segmentStart
    while (index < segmentEnd && /\s/.test(value[index])) index += 1
    const nameStart = index
    while (index < segmentEnd && isCookieNameCharacter(value[index])) index += 1
    while (index < segmentEnd && /\s/.test(value[index])) index += 1
    if (index > nameStart && value[index] === '=') {
      index += 1
      while (index < segmentEnd && /\s/.test(value[index])) index += 1
      const bounds = quotedBoundsAt(value, index, segmentEnd)
      if (bounds != null) {
        ranges.push({ start: bounds.contentStart, end: bounds.contentEnd })
      } else {
        const valueStart = index
        while (index < segmentEnd && !/\s/.test(value[index]) && value[index] !== '"' && value[index] !== "'") index += 1
        ranges.push({ start: valueStart, end: index })
      }
    }

    if (segmentEnd >= end || value[segmentEnd] !== ';') break
    segmentStart = segmentEnd + 1
  }
  return ranges
}

const cookieValueRanges = (value, start) => {
  const lineEnd = lineEndFrom(value, start)
  const bounds = quotedBoundsAt(value, start, lineEnd)
  return bounds == null
    ? cookiePairRanges(value, start, lineEnd)
    : cookiePairRanges(value, bounds.contentStart, bounds.contentEnd)
}

const redactNamedCookieHeaders = (value) => {
  const ranges = []
  const pattern = /\b(?:Set-Cookie|Cookie)(?:\\?["'])?\s*:\s*/gi
  for (let match = pattern.exec(value); match != null; match = pattern.exec(value)) {
    ranges.push(...cookieValueRanges(value, match.index + match[0].length))
  }
  return replaceRanges(value, ranges)
}

const redactCookieOptions = (value) => {
  const ranges = []
  const pattern = /(?:^|\s)(?:-b|--cookie)(?:\s+|=)/gim
  for (let match = pattern.exec(value); match != null; match = pattern.exec(value)) {
    ranges.push(...cookieValueRanges(value, match.index + match[0].length))
  }
  return replaceRanges(value, ranges)
}

const redactCookieHeaders = (value) => redactCookieOptions(redactNamedCookieHeaders(value))

const redactSensitiveAssignments = (value) => {
  const ranges = []
  const pattern = /(?:\\?["'])?(?:api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|token|client[_-]?secret|aws[_-]?secret[_-]?access[_-]?key|secret|password|passwd|pwd)(?:\\?["'])?\s*[:=]\s*/gi
  for (let match = pattern.exec(value); match != null; match = pattern.exec(value)) {
    const start = match.index + match[0].length
    const lineEnd = lineEndFrom(value, start)
    const bounds = quotedBoundsAt(value, start, lineEnd)
    if (bounds != null) {
      ranges.push({ start: bounds.contentStart, end: bounds.contentEnd })
      pattern.lastIndex = bounds.after
      continue
    }
    let end = start
    while (end < lineEnd && !/[\s,;，；。！？、]/.test(value[end])) end += 1
    ranges.push({ start, end })
    pattern.lastIndex = end
  }
  return replaceRanges(value, ranges)
}

const redactCredentials = (value) => redactSensitiveAssignments(redactCookieHeaders(value)
  .replace(/-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----/g, '[REDACTED]')
  .replace(/\bAuthorization\s*:\s*Basic\s+[A-Za-z0-9+/=]{8,}/gi, 'Authorization: Basic [REDACTED]')
  .replace(/\bBearer\s+[A-Za-z0-9._~+/=-]{12,}/gi, 'Bearer [REDACTED]')
  .replace(/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, '[REDACTED]')
  .replace(/\b(?:sk-ant-[A-Za-z0-9_-]{12,}|sk-(?:proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[A-Z0-9]{16})\b/g, '[REDACTED]'))
  .replace(/(https?:\/\/[^\s/:@]+:)[^\s/@]+@/gi, '$1[REDACTED]@')

const listJsonlFiles = (root) => {
  if (!fs.existsSync(root)) return []
  return fs.readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .flatMap((entry) => {
      const directory = path.join(root, entry.name)
      return fs.readdirSync(directory, { withFileTypes: true })
        .filter((file) => file.isFile() && file.name.endsWith('.jsonl'))
        .map((file) => path.join(directory, file.name))
    })
    .filter((file) => !file.includes(`${path.sep}subagents${path.sep}`) && !file.includes(`${path.sep}workflows${path.sep}`))
    .sort()
}

const collectEligible = (root, date) => {
  const currentDate = parseDate(date)
  const start = `${formatDate(addDays(currentDate, -6))}T00:00:00.000Z`
  const end = `${date}T23:59:59.999Z`
  const eligible = []

  for (const file of listJsonlFiles(root)) {
    const rows = fs.readFileSync(file, 'utf8').split('\n')
    rows.forEach((line, rowIndex) => {
      if (line.trim() === '') return
      let row
      try {
        row = JSON.parse(line)
      } catch {
        return
      }
      const message = row.message ?? {}
      const timestamp = row.timestamp
      if (
        row.type !== 'assistant' ||
        row.isSidechain === true ||
        message.stop_reason !== 'end_turn' ||
        typeof timestamp !== 'string' ||
        timestamp < start ||
        timestamp > end
      ) return
      const answer = extractText(message.content)
      if (removeCode(answer).trim().length < 200) return
      eligible.push({
        session: row.sessionId ?? path.basename(file, '.jsonl'),
        path: file,
        timestamp,
        row_index: rowIndex,
        answer: redactCredentials(answer),
      })
    })
  }

  const compareText = (left, right) => left === right ? 0 : left < right ? -1 : 1
  return eligible.sort((left, right) =>
    compareText(left.timestamp, right.timestamp) ||
    compareText(left.path, right.path) ||
    left.row_index - right.row_index
  )
}

const sampleEvenly = (items, limit = 20) => {
  if (items.length <= limit) return items
  return Array.from({ length: limit }, (_, index) =>
    items[Math.round(index * (items.length - 1) / (limit - 1))]
  )
}

const sameKeys = (value, keys) =>
  value && typeof value === 'object' && !Array.isArray(value) &&
  JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...keys].sort())
const hashJson = (value) => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex')
const parseVersionCommitment = (reports, date) => {
  const file = versionPathFor(reports, date)
  if (!fs.existsSync(file)) return null
  if (!isPrivateFile(file)) return false
  try {
    const commitment = JSON.parse(fs.readFileSync(file, 'utf8'))
    return sameKeys(commitment, ['date', 'schema_version', 'manifest_hash']) && commitment.date === date && commitment.schema_version === 2 && /^[a-f0-9]{64}$/.test(commitment.manifest_hash) ? commitment : false
  } catch {
    return false
  }
}
const parseManifest = (file, date) => {
  if (!fs.existsSync(file) || !isPrivateFile(file)) return null
  try {
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'))
    const v1Keys = ['date', 'eligible', 'samples', 'challenge', 'manifest_hash']
    const v2Keys = ['schema_version', 'date', 'eligible', 'samples', 'challenge', 'manifest_hash']
    const isV2 = Object.hasOwn(manifest, 'schema_version')
    const commitment = parseVersionCommitment(path.dirname(file), date)
    if (commitment === false) return null
    if (isV2 ? !sameKeys(manifest, v2Keys) || manifest.schema_version !== 2 || commitment == null : !sameKeys(manifest, v1Keys) || commitment != null || date >= EVIDENCE_V2_START_DATE) return null
    if (manifest.date !== date || !Number.isInteger(manifest.eligible) || manifest.eligible < 0 || !Array.isArray(manifest.samples)) return null
    if (!/^[a-f0-9]{64}$/.test(manifest.challenge) || manifest.samples.length !== Math.min(manifest.eligible, 20)) return null
    const samplesValid = manifest.samples.every((sample) =>
      sameKeys(sample, ['session', 'path', 'timestamp', 'row_index', 'answer']) &&
      typeof sample.session === 'string' && sample.session !== '' &&
      typeof sample.path === 'string' && sample.path !== '' &&
      typeof sample.timestamp === 'string' &&
      Number.isInteger(sample.row_index) && sample.row_index >= 0 &&
      typeof sample.answer === 'string'
    )
    if (!samplesValid) return null
    const payload = {
      ...(isV2 ? { schema_version: 2 } : {}),
      date: manifest.date,
      eligible: manifest.eligible,
      samples: manifest.samples,
      challenge: manifest.challenge,
    }
    if (manifest.manifest_hash !== hashJson(payload)) return null
    if (isV2 && commitment.manifest_hash !== manifest.manifest_hash) return null
    return manifest
  } catch {
    return null
  }
}
const loadOrCreateManifest = (root, reports, date) => {
  ensurePrivateDirectory(reports)
  const file = manifestPathFor(reports, date)
  const existing = parseManifest(file, date)
  if (existing != null) return { file, manifest: existing }
  if (fs.existsSync(file)) throw new Error('existing evidence-level manifest is invalid')
  const eligibleAnswers = collectEligible(root, date)
  const payload = {
    schema_version: 2,
    date,
    eligible: eligibleAnswers.length,
    samples: sampleEvenly(eligibleAnswers),
    challenge: crypto.randomBytes(32).toString('hex'),
  }
  const manifest = { ...payload, manifest_hash: hashJson(payload) }
  const temporary = `${file}.${process.pid}.tmp`
  fs.rmSync(temporary, { force: true })
  fs.writeFileSync(temporary, `${JSON.stringify(manifest)}\n`, { flag: 'wx', mode: 0o600 })
  try {
    fs.linkSync(temporary, file)
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error
  } finally {
    fs.rmSync(temporary, { force: true })
  }
  const commitment = { date, schema_version: 2, manifest_hash: manifest.manifest_hash }
  publishFirstWins(versionPathFor(reports, date), `${JSON.stringify(commitment)}\n`)
  const persisted = parseManifest(file, date)
  if (persisted == null) throw new Error('evidence-level manifest persistence failed')
  return { file, manifest: persisted }
}
const parseAudit = (value, manifest) => {
  if (typeof value !== 'string' || value === '' || value.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) return null
  try {
    const audit = JSON.parse(Buffer.from(value, 'base64').toString('utf8'))
    if (!sameKeys(audit, ['audit_nonce', 'rows']) || !/^[a-f0-9]{64}$/.test(audit.audit_nonce) || !Array.isArray(audit.rows) || audit.rows.length !== manifest.samples.length) return null
    const rows = audit.rows.map((row, index) => {
      const sample = manifest.samples[index]
      if (!sameKeys(row, ['timestamp', 'session', 'path', 'result', 'findings'])) return null
      if (row.timestamp !== sample.timestamp || row.session !== sample.session || row.path !== sample.path) return null
      if (!['PASS', 'FAIL'].includes(row.result) || !Array.isArray(row.findings)) return null
      const findings = row.findings.map((finding) => {
        if (!sameKeys(finding, ['type', 'quote'])) return null
        if (!VIOLATION_TYPES.has(finding.type) || typeof finding.quote !== 'string' || finding.quote === '') return null
        if (finding.quote.includes('|') || /[\r\n]/.test(finding.quote) || !sample.answer.includes(finding.quote)) return null
        return finding
      })
      if (findings.some((finding) => finding == null)) return null
      if (new Set(findings.map((finding) => finding.type)).size !== findings.length) return null
      if ((row.result === 'PASS') !== (findings.length === 0)) return null
      return { ...row, findings: [...findings].sort((left, right) => VIOLATION_ORDER.get(left.type) - VIOLATION_ORDER.get(right.type)) }
    })
    return rows.some((row) => row == null) ? null : { auditNonce: audit.audit_nonce, rows }
  } catch {
    return null
  }
}
const violationCounts = (rows) => {
  const counts = new Map()
  for (const row of rows) {
    for (const finding of row.findings) counts.set(finding.type, (counts.get(finding.type) ?? 0) + 1)
  }
  return [...counts.entries()].sort((left, right) => right[1] - left[1] || (left[0] < right[0] ? -1 : left[0] > right[0] ? 1 : 0))
}
const tableCell = (value) => String(value).replace(/\|/g, '／').replace(/[\r\n]+/g, ' ')
const shortestQuote = (findings) => findings
  .map((finding) => finding.quote)
  .sort((left, right) => left.length - right.length || (left < right ? -1 : left > right ? 1 : 0))[0]
const renderReport = (manifest, rows) => {
  const counts = violationCounts(rows)
  const violationCount = rows.filter((row) => row.result === 'FAIL').length
  const conclusion = violationCount === 0
    ? `本週抽樣 ${rows.length} 則，未命中證據層級違規。`
    : `本週抽樣 ${rows.length} 則，${violationCount} 則命中證據層級違規，最常見為 ${counts[0][0]}。`
  const top = counts.length === 0
    ? '無'
    : counts.slice(0, 2).map(([type, count]) => `- ${type}：${count}`).join('\n')
  const reportRows = rows.map((row) => {
    const types = row.findings.length === 0 ? '無' : row.findings.map((finding) => finding.type).join('、')
    const quote = row.findings.length === 0 ? '未命中' : shortestQuote(row.findings)
    const explanation = row.findings.length === 0 ? '未命中五種違規' : `命中 ${types}`
    return `| ${tableCell(row.timestamp)} | ${tableCell(row.session)} | ${tableCell(row.path)} | ${row.result} | ${types} | ${tableCell(quote)} | ${explanation} |`
  })
  return `${conclusion}\n\n## 重複違規 Top 2\n\n${top}\n\n## 數量\n\n- 抽樣數：${rows.length}\n- eligible：${manifest.eligible}\n- TP-style violation count：${violationCount}\n\n## 逐案核對\n\n${REPORT_TABLE_HEADER}\n${REPORT_TABLE_SEPARATOR}${reportRows.length === 0 ? '' : `\n${reportRows.join('\n')}`}\n\n## 限制\n\n${LIMITATIONS}\n`
}
const topViolationsFromReport = (report) => {
  const section = report.match(/## 重複違規 Top 2\n\n([\s\S]*?)\n\n## 數量/)?.[1]
  if (section == null || section === '無') return []
  const items = section.split('\n').map((line) => line.match(/^- ([a-z-]+)：(\d+)$/)).map((match) => match == null ? null : ({ type: match[1], count: Number(match[2]) }))
  return items.some((item) => item == null) ? [] : items
}
const packetFor = (date, reportPath, manifestPath, manifest, violationCount, topViolations, ok) => ({
  date,
  ok,
  report_path: reportPath,
  manifest_path: manifestPath,
  eligible: manifest?.eligible ?? null,
  sample_count: manifest?.samples.length ?? null,
  tp_style_violation_count: violationCount,
  top_violations: topViolations,
  samples_sha256: ok && manifest != null ? digestText(JSON.stringify(manifest.samples)) : null,
  samples_file_sha256: ok ? null : null,
  audit_nonce: null,
  reaudit_sample_count: 0,
  reaudit_samples_file_sha256: null,
  reaudit_nonce: null,
  challenge: ok ? manifest?.challenge ?? null : null,
})
const parsePublication = (file, date, manifestPath, manifest) => {
  if (!fs.existsSync(file) || !isPrivateFile(file)) return null
  try {
    const publication = JSON.parse(fs.readFileSync(file, 'utf8'))
    const v1Keys = ['date', 'manifest_sha256', 'samples_sha256', 'report', 'report_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'challenge']
    const v2Keys = ['schema_version', 'date', 'manifest_sha256', 'samples_sha256', 'samples_file_sha256', 'report', 'report_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'top_violations', 'audit_nonce', 'reaudit_sample_count', 'reaudit_samples_file_sha256', 'reaudit_nonce', 'challenge']
    const isV2 = Object.hasOwn(publication, 'schema_version')
    if (isV2 ? !sameKeys(publication, v2Keys) || publication.schema_version !== 2 : !sameKeys(publication, v1Keys) || date >= EVIDENCE_V2_START_DATE) return null
    if ((Object.hasOwn(manifest, 'schema_version') ? 2 : 1) !== (isV2 ? 2 : 1)) return null
    if (publication.date !== date || publication.challenge !== manifest.challenge) return null
    if (publication.manifest_sha256 !== digestFile(manifestPath) || publication.samples_sha256 !== digestText(JSON.stringify(manifest.samples))) return null
    if (publication.report_sha256 !== digestText(publication.report)) return null
    if (publication.eligible !== manifest.eligible || publication.sample_count !== manifest.samples.length) return null
    if (!Number.isInteger(publication.tp_style_violation_count) || publication.tp_style_violation_count < 0 || publication.tp_style_violation_count > manifest.samples.length) return null
    if (isV2) {
      if (!/^[a-f0-9]{64}$/.test(publication.samples_file_sha256) || !/^[a-f0-9]{64}$/.test(publication.audit_nonce)) return null
      if (!Number.isInteger(publication.reaudit_sample_count) || publication.reaudit_sample_count < 0 || publication.reaudit_sample_count > publication.sample_count) return null
      if (publication.reaudit_sample_count === 0 ? publication.reaudit_samples_file_sha256 !== null || publication.reaudit_nonce !== null : !/^[a-f0-9]{64}$/.test(publication.reaudit_samples_file_sha256) || !/^[a-f0-9]{64}$/.test(publication.reaudit_nonce) || publication.reaudit_nonce === publication.audit_nonce) return null
      if (!Array.isArray(publication.top_violations) || publication.top_violations.length > 2) return null
      if (!publication.top_violations.every((item) => sameKeys(item, ['type', 'count']) && VIOLATION_TYPES.has(item.type) && Number.isInteger(item.count) && item.count > 0 && item.count <= publication.tp_style_violation_count)) return null
      if (new Set(publication.top_violations.map((item) => item.type)).size !== publication.top_violations.length) return null
      if ((publication.tp_style_violation_count === 0) !== (publication.top_violations.length === 0)) return null
      const sortedTop = [...publication.top_violations].sort((left, right) => right.count - left.count || (left.type < right.type ? -1 : left.type > right.type ? 1 : 0))
      if (JSON.stringify(publication.top_violations) !== JSON.stringify(sortedTop)) return null
      const topSection = publication.report.match(/## 重複違規 Top 2\n\n([\s\S]*?)\n\n## 數量/)?.[1]
      const expectedTopSection = publication.top_violations.length === 0 ? '無' : publication.top_violations.map((item) => `- ${item.type}：${item.count}`).join('\n')
      if (topSection !== expectedTopSection) return null
    }
    return publication
  } catch {
    return null
  }
}
const verifiedPacket = (reports, date) => {
  if (!hasVerifiedReport(reports, date)) return null
  const manifestPath = manifestPathFor(reports, date)
  const publicationPath = publicationPathFor(reports, date)
  const reportPath = reportPathFor(reports, date)
  const receiptPath = receiptPathFor(reports, date)
  const manifest = parseManifest(manifestPath, date)
  if (manifest == null) return null
  const publication = parsePublication(publicationPath, date, manifestPath, manifest)
  if (publication == null) return null
  try {
    const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
    const v1Keys = ['date', 'report_sha256', 'manifest_sha256', 'samples_sha256', 'publication_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'challenge']
    const v2Keys = ['schema_version', 'date', 'report_sha256', 'manifest_sha256', 'samples_sha256', 'samples_file_sha256', 'publication_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'top_violations', 'audit_nonce', 'reaudit_sample_count', 'reaudit_samples_file_sha256', 'reaudit_nonce', 'challenge']
    const isV2 = Object.hasOwn(receipt, 'schema_version')
    if (isV2 ? !sameKeys(receipt, v2Keys) || receipt.schema_version !== 2 : !sameKeys(receipt, v1Keys) || date >= EVIDENCE_V2_START_DATE) return null
    if ((Object.hasOwn(publication, 'schema_version') ? 2 : 1) !== (isV2 ? 2 : 1)) return null
    if (receipt.date !== date || receipt.challenge !== publication.challenge) return null
    if (receipt.report_sha256 !== publication.report_sha256 || receipt.manifest_sha256 !== publication.manifest_sha256) return null
    if (receipt.samples_sha256 !== publication.samples_sha256) return null
    if (receipt.schema_version === 2 && (publication.schema_version !== 2 || receipt.samples_file_sha256 !== publication.samples_file_sha256 || receipt.audit_nonce !== publication.audit_nonce || receipt.reaudit_sample_count !== publication.reaudit_sample_count || receipt.reaudit_samples_file_sha256 !== publication.reaudit_samples_file_sha256 || receipt.reaudit_nonce !== publication.reaudit_nonce || JSON.stringify(receipt.top_violations) !== JSON.stringify(publication.top_violations))) return null
    if (receipt.publication_sha256 !== digestFile(publicationPath)) return null
    if (receipt.eligible !== publication.eligible || receipt.sample_count !== publication.sample_count) return null
    if (receipt.tp_style_violation_count !== publication.tp_style_violation_count) return null
    if (fs.readFileSync(reportPath, 'utf8') !== publication.report) return null
    const samplesTextPath = samplesTextPathFor(reports, date)
    return {
      ...packetFor(date, reportPath, manifestPath, manifest, receipt.tp_style_violation_count, publication.top_violations ?? topViolationsFromReport(publication.report), true),
      samples_sha256: publication.samples_sha256,
      samples_file_sha256: publication.samples_file_sha256 ?? (fs.existsSync(samplesTextPath) ? digestFile(samplesTextPath) : null),
      audit_nonce: publication.audit_nonce ?? null,
      reaudit_sample_count: publication.reaudit_sample_count ?? 0,
      reaudit_samples_file_sha256: publication.reaudit_samples_file_sha256 ?? null,
      reaudit_nonce: publication.reaudit_nonce ?? null,
    }
  } catch {
    return null
  }
}
const uniqueTemporaryPath = (file) => `${file}.${process.pid}.${crypto.randomBytes(8).toString('hex')}.tmp`
const replaceFile = (file, content) => {
  const temporary = uniqueTemporaryPath(file)
  fs.writeFileSync(temporary, content, { flag: 'wx', mode: 0o600 })
  try {
    fs.renameSync(temporary, file)
    fs.chmodSync(file, 0o600)
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}
const publishFirstWins = (file, content) => {
  const temporary = uniqueTemporaryPath(file)
  fs.writeFileSync(temporary, content, { flag: 'wx', mode: 0o600 })
  try {
    try {
      fs.linkSync(temporary, file)
      return true
    } catch (error) {
      if (error?.code !== 'EEXIST') throw error
      return false
    }
  } finally {
    fs.rmSync(temporary, { force: true })
  }
}
const materializePublication = (reports, date, publication) => {
  const manifestPath = manifestPathFor(reports, date)
  const publicationPath = publicationPathFor(reports, date)
  const reportPath = reportPathFor(reports, date)
  const receiptPath = receiptPathFor(reports, date)
  replaceFile(reportPath, publication.report)
  replaceFile(receiptPath, `${JSON.stringify({
    ...(publication.schema_version === 2 ? { schema_version: 2 } : {}),
    date,
    report_sha256: publication.report_sha256,
    manifest_sha256: publication.manifest_sha256,
    samples_sha256: publication.samples_sha256,
    ...(publication.schema_version === 2 ? { samples_file_sha256: publication.samples_file_sha256 } : {}),
    publication_sha256: digestFile(publicationPath),
    eligible: publication.eligible,
    sample_count: publication.sample_count,
    tp_style_violation_count: publication.tp_style_violation_count,
    ...(publication.schema_version === 2 ? {
      top_violations: publication.top_violations,
      audit_nonce: publication.audit_nonce,
      reaudit_sample_count: publication.reaudit_sample_count,
      reaudit_samples_file_sha256: publication.reaudit_samples_file_sha256,
      reaudit_nonce: publication.reaudit_nonce,
    } : {}),
    challenge: publication.challenge,
  })}\n`)
  return verifiedPacket(reports, date) ?? packetFor(date, reportPath, manifestPath, null, null, [], false)
}
const transcriptFiles = (root) => {
  if (!fs.existsSync(root)) return []
  const files = []
  const visit = (directory) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const target = path.join(directory, entry.name)
      if (entry.isDirectory()) visit(target)
      if (entry.isFile() && /^agent-.*\.jsonl$/.test(entry.name)) files.push(target)
    }
  }
  visit(root)
  return files.sort((left, right) => fs.statSync(right).mtimeMs - fs.statSync(left).mtimeMs)
}
const restoreReadText = (content, expectedStartLine) => {
  if (typeof content !== 'string' || /^<tool_use_error>/m.test(content)) return null
  const lines = content.split('\n')
  const restored = []
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].match(/^(\d+)\t(.*)$/)
    if (match == null || Number(match[1]) !== expectedStartLine + index) return null
    restored.push(match[2])
  }
  return restored.join('\n')
}
const auditB64FromTranscripts = (root, samplesTextPath, expectedText, auditNonce) => {
  for (const file of transcriptFiles(root)) {
    const pending = new Map()
    const chunks = []
    const seenIds = new Set()
    let invalidTranscript = false
    for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
      if (line === '') continue
      let row
      try {
        row = JSON.parse(line)
      } catch {
        continue
      }
      const content = row?.message?.content
      if (!Array.isArray(content)) continue
      for (const item of content) {
        if (item?.type === 'tool_use' && item?.name === 'Read' && item?.input?.file_path !== samplesTextPath) {
          invalidTranscript = true
          break
        }
        if (item?.type === 'tool_use' && item?.name !== 'Read' && item?.name !== 'StructuredOutput') {
          invalidTranscript = true
          break
        }
        if (item?.type === 'tool_use' && item?.name === 'Read' && item?.input?.file_path === samplesTextPath && typeof item.id === 'string') {
          const inputKeys = ['file_path', ...(item.input.offset == null ? [] : ['offset']), ...(item.input.limit == null ? [] : ['limit'])]
          if (seenIds.has(item.id) || !sameKeys(item.input, inputKeys) || (item.input.offset != null && (!Number.isInteger(item.input.offset) || item.input.offset < 0)) || (item.input.limit != null && (!Number.isInteger(item.input.limit) || item.input.limit <= 0))) return null
          seenIds.add(item.id)
          pending.set(item.id, item.input)
          continue
        }
        if (item?.type === 'tool_result' && typeof item.tool_use_id === 'string' && pending.has(item.tool_use_id)) {
          const input = pending.get(item.tool_use_id)
          const text = restoreReadText(item.content, input.offset ?? 1)
          if (text == null) return null
          chunks.push({ startLine: input.offset ?? 1, limit: input.limit ?? null, text })
          pending.delete(item.tool_use_id)
          continue
        }
        if (item?.type === 'tool_use' && item?.name === 'StructuredOutput' && sameKeys(item.input, ['audit_nonce', 'rows']) && item.input.audit_nonce === auditNonce) {
          if (pending.size !== 0 || chunks.length === 0) return null
          const ordered = [...chunks].sort((left, right) => left.startLine - right.startLine)
          const restored = new Map()
          for (const chunk of ordered) {
            const lines = chunk.text.split('\n')
            if (chunk.limit != null && lines.length > chunk.limit) return null
            for (let index = 0; index < lines.length; index += 1) {
              const lineNumber = chunk.startLine + index
              const existing = restored.get(lineNumber)
              if (existing != null && existing !== lines[index]) return null
              restored.set(lineNumber, lines[index])
            }
          }
          if (restored.size === 0) return null
          const lastLine = Math.max(...restored.keys())
          const complete = Array.from({ length: lastLine }, (_, index) => restored.get(index + 1))
          if (complete.some((line) => line == null)) return null
          if (complete.join('\n') === expectedText) return Buffer.from(JSON.stringify(item.input), 'utf8').toString('base64')
          return null
        }
      }
      if (invalidTranscript) break
    }
  }
  return null
}
const prepareReaudit = (reports, date, transcriptsRoot, samplesSha256, samplesFileSha256, auditNonce) => {
  ensurePrivateDirectory(reports)
  const existingPath = reauditSamplesTextPathFor(reports, date)
  if (fs.existsSync(existingPath)) {
    const existingText = fs.readFileSync(existingPath, 'utf8')
    const marker = existingText.match(/^=== BEGIN SAMPLE 1\/\d+ \[([a-f0-9]{16})\] ===$/m)?.[1]
    const count = Number(existingText.match(/^=== BEGIN SAMPLE 1\/(\d+) /m)?.[1])
    if (marker != null && Number.isInteger(count) && count > 0) {
      const nonceFile = `${existingPath}.nonce`
      if (fs.existsSync(nonceFile) && isPrivateFile(nonceFile)) {
        const nonce = fs.readFileSync(nonceFile, 'utf8').trim()
        if (/^[a-f0-9]{64}$/.test(nonce) && nonce.startsWith(marker)) return { date, reaudit_sample_count: count, reaudit_samples_file_sha256: digestFile(existingPath), reaudit_nonce: nonce }
      }
    }
    return null
  }
  const manifest = parseManifest(manifestPathFor(reports, date), date)
  const samplesTextPath = samplesTextPathFor(reports, date)
  if (manifest == null || transcriptsRoot == null || samplesSha256 !== digestText(JSON.stringify(manifest.samples)) || !fs.existsSync(samplesTextPath) || samplesFileSha256 !== digestFile(samplesTextPath)) return null
  const auditB64 = auditB64FromTranscripts(transcriptsRoot, samplesTextPath, renderSamplesText(manifest), auditNonce)
  const auditPacket = parseAudit(auditB64, manifest)
  if (auditPacket == null || auditPacket.auditNonce !== auditNonce) return null
  const passSamples = manifest.samples.filter((sample, index) => auditPacket.rows[index].result === 'PASS')
  if (passSamples.length === 0) return null
  const reauditNonce = crypto.randomBytes(32).toString('hex')
  const reauditText = renderSampleRowsText(passSamples, reauditNonce)
  const reauditSamplesTextPath = reauditSamplesTextPathFor(reports, date)
  if (!publishFirstWins(reauditSamplesTextPath, reauditText)) return prepareReaudit(reports, date, transcriptsRoot, samplesSha256, samplesFileSha256, auditNonce)
  publishFirstWins(`${reauditSamplesTextPath}.nonce`, `${reauditNonce}\n`)
  return {
    date,
    reaudit_sample_count: passSamples.length,
    reaudit_samples_file_sha256: digestFile(reauditSamplesTextPath),
    reaudit_nonce: reauditNonce,
  }
}
const finalizeReport = (reports, date, auditB64, transcriptsRoot, samplesSha256, samplesFileSha256, auditNonce, reauditSamplesFileSha256, reauditNonce) => {
  ensurePrivateDirectory(reports)
  const existing = verifiedPacket(reports, date)
  if (existing != null) return existing
  const manifestPath = manifestPathFor(reports, date)
  const publicationPath = publicationPathFor(reports, date)
  const reportPath = reportPathFor(reports, date)
  const manifest = parseManifest(manifestPath, date)
  if (manifest == null) return packetFor(date, reportPath, manifestPath, null, null, [], false)

  const published = parsePublication(publicationPath, date, manifestPath, manifest)
  if (published != null) return materializePublication(reports, date, published)
  if (fs.existsSync(publicationPath)) return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  if (samplesSha256 !== digestText(JSON.stringify(manifest.samples))) {
    return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  }
  const samplesTextPath = samplesTextPathFor(reports, date)
  if (!fs.existsSync(samplesTextPath) || samplesFileSha256 !== digestFile(samplesTextPath)) {
    return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  }

  const expectedSamplesText = renderSamplesText(manifest)
  const transcriptAuditB64 = transcriptsRoot == null ? null : auditB64FromTranscripts(transcriptsRoot, samplesTextPath, expectedSamplesText, auditNonce)
  const auditPacket = parseAudit(transcriptsRoot == null ? auditB64 : transcriptAuditB64, manifest)
  if (auditPacket == null || auditPacket.auditNonce !== auditNonce) return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  const primaryPassSamples = manifest.samples.filter((sample, index) => auditPacket.rows[index].result === 'PASS')
  const reauditDeclared = reauditSamplesFileSha256 != null || reauditNonce != null
  let reauditPacket = null
  let reauditSamplesFileHash = null
  let reauditAuditNonce = null
  if (primaryPassSamples.length > 0 && Object.hasOwn(manifest, 'schema_version') && !reauditDeclared) return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  if (primaryPassSamples.length > 0 && reauditDeclared) {
    const reauditSamplesTextPath = reauditSamplesTextPathFor(reports, date)
    if (transcriptsRoot == null || !/^[a-f0-9]{64}$/.test(reauditNonce ?? '') || reauditNonce === auditNonce || !fs.existsSync(reauditSamplesTextPath) || reauditSamplesFileSha256 !== digestFile(reauditSamplesTextPath)) {
      return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
    }
    const expectedReauditText = renderSampleRowsText(primaryPassSamples, reauditNonce)
    const reauditManifest = { samples: primaryPassSamples }
    const reauditAuditB64 = auditB64FromTranscripts(transcriptsRoot, reauditSamplesTextPath, expectedReauditText, reauditNonce)
    reauditPacket = parseAudit(reauditAuditB64, reauditManifest)
    if (reauditPacket == null || reauditPacket.auditNonce !== reauditNonce) return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
    reauditSamplesFileHash = reauditSamplesFileSha256
    reauditAuditNonce = reauditNonce
  } else if (primaryPassSamples.length === 0 && reauditDeclared && (reauditSamplesFileSha256 !== '0' || reauditNonce !== '0')) {
    return packetFor(date, reportPath, manifestPath, manifest, null, [], false)
  }
  let passIndex = 0
  const rows = auditPacket.rows.map((row) => {
    if (row.result === 'FAIL' || reauditPacket == null) return row
    const reauditRow = reauditPacket.rows[passIndex]
    passIndex += 1
    return reauditRow.result === 'PASS' ? row : reauditRow
  })
  const violationCount = rows.filter((row) => row.result === 'FAIL').length
  const topViolations = violationCounts(rows).slice(0, 2).map(([type, count]) => ({ type, count }))
  const report = renderReport(manifest, rows)
  const isV2 = Object.hasOwn(manifest, 'schema_version')
  const publication = {
    ...(isV2 ? { schema_version: 2 } : {}),
    date,
    manifest_sha256: digestFile(manifestPath),
    samples_sha256: samplesSha256,
    ...(isV2 ? { samples_file_sha256: samplesFileSha256 } : {}),
    report,
    report_sha256: digestText(report),
    eligible: manifest.eligible,
    sample_count: manifest.samples.length,
    tp_style_violation_count: violationCount,
    ...(isV2 ? {
      top_violations: topViolations,
      audit_nonce: auditNonce,
      reaudit_sample_count: reauditPacket == null ? 0 : primaryPassSamples.length,
      reaudit_samples_file_sha256: reauditSamplesFileHash,
      reaudit_nonce: reauditAuditNonce,
    } : {}),
    challenge: manifest.challenge,
  }
  publishFirstWins(publicationPath, `${JSON.stringify(publication)}\n`)
  const winner = parsePublication(publicationPath, date, manifestPath, manifest)
  return winner == null
    ? packetFor(date, reportPath, manifestPath, manifest, null, [], false)
    : materializePublication(reports, date, winner)
}

const sampleMarker = (manifest, index, position) =>
  `=== ${position} SAMPLE ${index + 1}/${manifest.samples.length} [${manifest.challenge.slice(0, 16)}] ===`
const renderSampleRowsText = (samples, challenge) => `${samples.map((sample, index) => [
  `=== BEGIN SAMPLE ${index + 1}/${samples.length} [${challenge.slice(0, 16)}] ===`,
  `timestamp: ${sample.timestamp}`,
  `session: ${sample.session}`,
  `path: ${sample.path}`,
  'answer:',
  sample.answer,
  `=== END SAMPLE ${index + 1}/${samples.length} [${challenge.slice(0, 16)}] ===`,
].join('\n')).join('\n\n')}\n`
const renderSamplesText = (manifest) => renderSampleRowsText(manifest.samples, manifest.challenge)

const main = () => {
  const options = parseArgs(process.argv.slice(2))
  const date = options.date
  const root = options.root
  const reports = options.reports
  const mode = options.mode ?? 'sample'
  parseDate(date)
  if (typeof root !== 'string' || typeof reports !== 'string') throw new Error('root and reports are required')
  if (!['due', 'sample', 'prepare-reaudit', 'finalize'].includes(mode)) throw new Error('mode must be due, sample, prepare-reaudit, or finalize')
  if (mode === 'prepare-reaudit') {
    const packet = prepareReaudit(reports, date, options['audit-from-transcripts'], options['samples-sha256'], options['samples-file-sha256'], options['audit-nonce'])
    process.stdout.write(`${JSON.stringify(packet ?? { date, reaudit_sample_count: 0, reaudit_samples_file_sha256: null, reaudit_nonce: null })}\n`)
    return
  }
  if (mode === 'finalize') {
    const transcriptsRoot = options['audit-from-transcripts']
    if (transcriptsRoot != null && options['audit-b64'] != null) throw new Error('audit-from-transcripts excludes audit-b64')
    process.stdout.write(`${JSON.stringify(finalizeReport(reports, date, options['audit-b64'], transcriptsRoot, options['samples-sha256'], options['samples-file-sha256'], options['audit-nonce'], options['reaudit-samples-file-sha256'], options['reaudit-nonce']))}\n`)
    return
  }

  const duePacket = buildDuePacket(reports, date)
  if (mode === 'due' || !duePacket.due) {
    process.stdout.write(`${JSON.stringify(duePacket)}\n`)
    return
  }

  const { manifest } = loadOrCreateManifest(root, reports, date)
  const samplesTextPath = samplesTextPathFor(reports, date)
  replaceFile(samplesTextPath, renderSamplesText(manifest))
  process.stdout.write(`${JSON.stringify({
    ...duePacket,
    audit_nonce: crypto.randomBytes(32).toString('hex'),
    eligible: manifest.eligible,
    sample_count: manifest.samples.length,
    samples_sha256: digestText(JSON.stringify(manifest.samples)),
    samples_file_sha256: digestFile(samplesTextPath),
    manifest_hash: manifest.manifest_hash,
    challenge: manifest.challenge,
  })}\n`)
}

main()
