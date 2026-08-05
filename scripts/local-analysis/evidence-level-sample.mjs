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

const digestText = (value) => crypto.createHash('sha256').update(value).digest('hex')
const digestFile = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')
const manifestPathFor = (reports, date) => path.join(reports, `${date}-evidence-level-manifest.json`)
const publicationPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.publish.json`)
const reportPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.md`)
const receiptPathFor = (reports, date) => path.join(reports, `${date}-evidence-level.verified.json`)
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
    return receipt.date === date &&
      receipt.report_sha256 === digestFile(reportPath) &&
      receipt.manifest_sha256 === digestFile(manifestPath) &&
      receipt.publication_sha256 === digestFile(publicationPath)
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
const parseManifest = (file, date) => {
  if (!fs.existsSync(file) || !isPrivateFile(file)) return null
  try {
    const manifest = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (!sameKeys(manifest, ['date', 'eligible', 'samples', 'challenge', 'manifest_hash'])) return null
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
      date: manifest.date,
      eligible: manifest.eligible,
      samples: manifest.samples,
      challenge: manifest.challenge,
    }
    return manifest.manifest_hash === hashJson(payload) ? manifest : null
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
  const persisted = parseManifest(file, date)
  if (persisted == null) throw new Error('evidence-level manifest persistence failed')
  return { file, manifest: persisted }
}
const parseAudit = (value, manifest) => {
  if (typeof value !== 'string' || value === '' || value.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(value)) return null
  try {
    const audit = JSON.parse(Buffer.from(value, 'base64').toString('utf8'))
    if (!sameKeys(audit, ['rows']) || !Array.isArray(audit.rows) || audit.rows.length !== manifest.samples.length) return null
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
    return rows.some((row) => row == null) ? null : rows
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
const packetFor = (date, reportPath, manifestPath, manifest, violationCount, ok) => ({
  date,
  ok,
  report_path: reportPath,
  manifest_path: manifestPath,
  eligible: manifest?.eligible ?? null,
  sample_count: manifest?.samples.length ?? null,
  tp_style_violation_count: violationCount,
  challenge: ok ? manifest?.challenge ?? null : null,
})
const parsePublication = (file, date, manifestPath, manifest) => {
  if (!fs.existsSync(file) || !isPrivateFile(file)) return null
  try {
    const publication = JSON.parse(fs.readFileSync(file, 'utf8'))
    if (!sameKeys(publication, ['date', 'manifest_sha256', 'samples_sha256', 'report', 'report_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'challenge'])) return null
    if (publication.date !== date || publication.challenge !== manifest.challenge) return null
    if (publication.manifest_sha256 !== digestFile(manifestPath) || publication.samples_sha256 !== digestText(JSON.stringify(manifest.samples))) return null
    if (publication.report_sha256 !== digestText(publication.report)) return null
    if (publication.eligible !== manifest.eligible || publication.sample_count !== manifest.samples.length) return null
    if (!Number.isInteger(publication.tp_style_violation_count) || publication.tp_style_violation_count < 0 || publication.tp_style_violation_count > manifest.samples.length) return null
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
    if (!sameKeys(receipt, ['date', 'report_sha256', 'manifest_sha256', 'samples_sha256', 'publication_sha256', 'eligible', 'sample_count', 'tp_style_violation_count', 'challenge'])) return null
    if (receipt.date !== date || receipt.challenge !== publication.challenge) return null
    if (receipt.report_sha256 !== publication.report_sha256 || receipt.manifest_sha256 !== publication.manifest_sha256) return null
    if (receipt.samples_sha256 !== publication.samples_sha256) return null
    if (receipt.publication_sha256 !== digestFile(publicationPath)) return null
    if (receipt.eligible !== publication.eligible || receipt.sample_count !== publication.sample_count) return null
    if (receipt.tp_style_violation_count !== publication.tp_style_violation_count) return null
    if (fs.readFileSync(reportPath, 'utf8') !== publication.report) return null
    return packetFor(date, reportPath, manifestPath, manifest, receipt.tp_style_violation_count, true)
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
    date,
    report_sha256: publication.report_sha256,
    manifest_sha256: publication.manifest_sha256,
    samples_sha256: publication.samples_sha256,
    publication_sha256: digestFile(publicationPath),
    eligible: publication.eligible,
    sample_count: publication.sample_count,
    tp_style_violation_count: publication.tp_style_violation_count,
    challenge: publication.challenge,
  })}\n`)
  return verifiedPacket(reports, date) ?? packetFor(date, reportPath, manifestPath, null, null, false)
}
const finalizeReport = (reports, date, auditB64, samplesSha256) => {
  ensurePrivateDirectory(reports)
  const existing = verifiedPacket(reports, date)
  if (existing != null) return existing
  const manifestPath = manifestPathFor(reports, date)
  const publicationPath = publicationPathFor(reports, date)
  const reportPath = reportPathFor(reports, date)
  const manifest = parseManifest(manifestPath, date)
  if (manifest == null) return packetFor(date, reportPath, manifestPath, null, null, false)

  const published = parsePublication(publicationPath, date, manifestPath, manifest)
  if (published != null) return materializePublication(reports, date, published)
  if (fs.existsSync(publicationPath)) return packetFor(date, reportPath, manifestPath, manifest, null, false)
  if (samplesSha256 !== digestText(JSON.stringify(manifest.samples))) {
    return packetFor(date, reportPath, manifestPath, manifest, null, false)
  }

  const rows = parseAudit(auditB64, manifest)
  if (rows == null) return packetFor(date, reportPath, manifestPath, manifest, null, false)
  const violationCount = rows.filter((row) => row.result === 'FAIL').length
  const report = renderReport(manifest, rows)
  const publication = {
    date,
    manifest_sha256: digestFile(manifestPath),
    samples_sha256: samplesSha256,
    report,
    report_sha256: digestText(report),
    eligible: manifest.eligible,
    sample_count: manifest.samples.length,
    tp_style_violation_count: violationCount,
    challenge: manifest.challenge,
  }
  publishFirstWins(publicationPath, `${JSON.stringify(publication)}\n`)
  const winner = parsePublication(publicationPath, date, manifestPath, manifest)
  return winner == null
    ? packetFor(date, reportPath, manifestPath, manifest, null, false)
    : materializePublication(reports, date, winner)
}

const main = () => {
  const options = parseArgs(process.argv.slice(2))
  const date = options.date
  const root = options.root
  const reports = options.reports
  const mode = options.mode ?? 'sample'
  parseDate(date)
  if (typeof root !== 'string' || typeof reports !== 'string') throw new Error('root and reports are required')
  if (!['due', 'sample', 'finalize'].includes(mode)) throw new Error('mode must be due, sample, or finalize')
  if (mode === 'finalize') {
    process.stdout.write(`${JSON.stringify(finalizeReport(reports, date, options['audit-b64'], options['samples-sha256']))}\n`)
    return
  }

  const duePacket = buildDuePacket(reports, date)
  if (mode === 'due' || !duePacket.due) {
    process.stdout.write(`${JSON.stringify(duePacket)}\n`)
    return
  }

  const { manifest } = loadOrCreateManifest(root, reports, date)
  process.stdout.write(`${JSON.stringify({
    ...duePacket,
    eligible: manifest.eligible,
    sample_count: manifest.samples.length,
    samples_b64: Buffer.from(JSON.stringify(manifest.samples)).toString('base64'),
    manifest_hash: manifest.manifest_hash,
    challenge: manifest.challenge,
  })}\n`)
}

main()
