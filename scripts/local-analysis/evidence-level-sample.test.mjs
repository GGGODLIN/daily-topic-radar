import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { spawn, spawnSync } from 'node:child_process'

const script = '/Users/linhancheng/code/social-info/scripts/local-analysis/evidence-level-sample.mjs'

const makeFixture = () => {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'evidence-level-'))
  const root = path.join(base, 'projects')
  const reports = path.join(base, 'reports')
  const project = path.join(root, 'fixture')
  fs.mkdirSync(project, { recursive: true })
  fs.mkdirSync(reports, { recursive: true })
  return { base, root, reports, project }
}

const writeRows = (file, rows) => {
  fs.writeFileSync(file, `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`)
}

const assistant = ({ text, timestamp, stopReason = 'end_turn', sidechain = false, sessionId = 'session' }) => ({
  type: 'assistant',
  isSidechain: sidechain,
  sessionId,
  timestamp,
  message: {
    stop_reason: stopReason,
    content: [{ type: 'text', text }],
  },
})

const sha256 = (value) => crypto.createHash('sha256').update(value).digest('hex')
const samplesShaFor = (reports, date) => {
  const manifest = JSON.parse(fs.readFileSync(path.join(reports, `${date}-evidence-level-manifest.json`), 'utf8'))
  return sha256(JSON.stringify(manifest.samples))
}
const argsFor = ({ date = '2026-08-06', root, reports, mode = 'sample', audit = null, samplesSha256 = null }) => [
  script,
  '--date',
  date,
  '--root',
  root,
  '--reports',
  reports,
  '--mode',
  mode,
  ...(audit == null ? [] : ['--audit-b64', Buffer.from(JSON.stringify(audit)).toString('base64')]),
  ...(mode !== 'finalize' ? [] : ['--samples-sha256', samplesSha256 ?? samplesShaFor(reports, date)]),
]

const run = (options) => {
  const result = spawnSync(process.execPath, argsFor(options), { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  return JSON.parse(result.stdout)
}

const runAsync = (options) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, argsFor(options))
  let stdout = ''
  let stderr = ''
  child.stdout.setEncoding('utf8')
  child.stderr.setEncoding('utf8')
  child.stdout.on('data', (chunk) => { stdout += chunk })
  child.stderr.on('data', (chunk) => { stderr += chunk })
  child.on('error', reject)
  child.on('close', (status) => {
    if (status !== 0) {
      reject(new Error(stderr))
      return
    }
    resolve(JSON.parse(stdout))
  })
})

const repeat = (value, length) => value.repeat(length)
const samplesFrom = (packet) => JSON.parse(Buffer.from(packet.samples_b64, 'base64').toString('utf8'))
const manifestPathFor = (fixture, date = '2026-08-06') => path.join(fixture.reports, `${date}-evidence-level-manifest.json`)
const reportPathFor = (fixture, date = '2026-08-06') => path.join(fixture.reports, `${date}-evidence-level.md`)
const receiptPathFor = (fixture, date = '2026-08-06') => path.join(fixture.reports, `${date}-evidence-level.verified.json`)
const publicationPathFor = (fixture, date = '2026-08-06') => path.join(fixture.reports, `${date}-evidence-level.publish.json`)
const modeFor = (file) => fs.statSync(file).mode & 0o777
const auditFor = (samples, findings = []) => ({
  rows: samples.map((sample, index) => ({
    timestamp: sample.timestamp,
    session: sample.session,
    path: sample.path,
    result: findings[index]?.length ? 'FAIL' : 'PASS',
    findings: findings[index] ?? [],
  })),
})

test('filters known-good and includes known-bad and 200-character boundary', () => {
  const fixture = makeFixture()
  const file = path.join(fixture.project, 'fixture.jsonl')
  writeRows(file, [
    assistant({ text: repeat('a', 250), timestamp: '2026-08-01T01:00:00.000Z', stopReason: 'tool_use', sessionId: 'tool-message' }),
    assistant({ text: repeat('b', 250), timestamp: '2026-08-01T02:00:00.000Z', sidechain: true, sessionId: 'sidechain' }),
    assistant({ text: `${repeat('c', 199)}\n\`\`\`js\n${repeat('x', 100)}\n\`\`\``, timestamp: '2026-08-01T03:00:00.000Z', sessionId: 'boundary-199' }),
    assistant({ text: `${repeat('i', 199)}\n    ${repeat('z', 300)}`, timestamp: '2026-08-01T03:30:00.000Z', sessionId: 'indented-code-199' }),
    assistant({ text: `${repeat('d', 200)}\n\`\`\`js\n${repeat('y', 100)}\n\`\`\``, timestamp: '2026-08-01T04:00:00.000Z', sessionId: 'boundary-200' }),
    assistant({ text: repeat('e', 240), timestamp: '2026-08-01T05:00:00.000Z', sessionId: 'known-bad' }),
  ])

  const packet = run(fixture)
  const samples = samplesFrom(packet)
  assert.equal(packet.eligible, 2)
  assert.equal(packet.sample_count, 2)
  assert.deepEqual(samples.map((sample) => sample.session), ['boundary-200', 'known-bad'])
  assert.equal(samples[0].answer.includes('```js'), true)
})

test('samples at most 20 answers in deterministic total order', () => {
  const fixture = makeFixture()
  const file = path.join(fixture.project, 'many.jsonl')
  const rows = Array.from({ length: 41 }, (_, index) => assistant({
    text: repeat(String(index % 10), 220),
    timestamp: `2026-08-02T00:${String(index).padStart(2, '0')}:00.000Z`,
    sessionId: `session-${String(index).padStart(2, '0')}`,
  }))
  writeRows(file, rows)

  const packet = run(fixture)
  const samples = samplesFrom(packet)
  assert.equal(packet.eligible, 41)
  assert.equal(packet.sample_count, 20)
  assert.equal(samples.length, 20)
  assert.deepEqual(
    samples.map((sample) => sample.row_index),
    [0, 2, 4, 6, 8, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29, 32, 34, 36, 38, 40]
  )
})

test('uses timestamp path and row index as a locale-independent total order', () => {
  const fixture = makeFixture()
  const firstProject = path.join(fixture.root, 'z-project')
  const secondProject = path.join(fixture.root, 'ä-project')
  fs.mkdirSync(firstProject)
  fs.mkdirSync(secondProject)
  writeRows(path.join(firstProject, 'same.jsonl'), [assistant({
    text: repeat('z', 220),
    timestamp: '2026-08-02T00:00:00.000Z',
    sessionId: 'z-session',
  })])
  writeRows(path.join(secondProject, 'same.jsonl'), [assistant({
    text: repeat('a', 220),
    timestamp: '2026-08-02T00:00:00.000Z',
    sessionId: 'accent-session',
  })])

  const packet = run(fixture)
  assert.deepEqual(samplesFrom(packet).map((sample) => sample.session), ['z-session', 'accent-session'])
})

test('persists full redacted answers in private artifacts', () => {
  const fixture = makeFixture()
  const secretValues = [
    'sk-ant-api03-FAKESECRET0123456789',
    'ghp_FAKESECRET012345678901234567890123456',
    'xoxb-1234567890-1234567890-FAKESECRET',
    'AKIAFAKESECRET123456',
    'GENERIC-SECRET-987654321',
    'secret value with spaces',
    'dXNlcjpiYXNpYy1mYWtlLXNlY3JldA==',
    'abc123',
    'COOKIE-SESSION-FAKE-111',
    'COOKIE-SID-FAKE-222',
    'SET-COOKIE-SESSION-FAKE-333',
    'QUOTED-COOKIE-FAKE-444',
    'QUOTED-SET-COOKIE-FAKE-555',
    'CURL-COOKIE-FAKE-666',
    'JSON-COOKIE-FAKE-777',
    'ESCAPED-COOKIE-FAKE-888',
    'ESCAPED-PASSWORD-FAKE-999',
    'fullwidth-token-secret',
    'UNQUOTED-HEADER-COOKIE-111',
    'UNQUOTED-HEADER-SET-COOKIE-222',
    'CURL-B-COOKIE-333',
    'CURL-LONG-COOKIE-444',
  ]
  const trailingClaim = '已完成全部驗證但此句沒有證據'
  const fullwidthClaim = '這段全形標點後的聲明必須保留'
  const longTail = repeat('長', 9000)
  writeRows(path.join(fixture.project, 'secret.jsonl'), [assistant({
    text: `Authorization: Bearer ${secretValues[0]}\napi_key=${secretValues[1]}\nslack=${secretValues[2]}\naws=${secretValues[3]}\n{"password": "${secretValues[4]}"}\nclient_secret="${secretValues[5]}"\nAuthorization: Basic ${secretValues[6]}\ntoken=${secretValues[7]} ${trailingClaim}\nCookie: session=${secretValues[8]}; sid=${secretValues[9]}; theme=dark\nSet-Cookie: session_id=${secretValues[10]}; Path=/; HttpOnly\n> Cookie: session=${secretValues[11]}\n  < Set-Cookie: sid=${secretValues[12]}\ncurl -H 'Cookie: session=${secretValues[13]}' https://example.test/after-cookie\n{"Cookie": "session=${secretValues[14]}"}\n{\\"Cookie\\": \\"session=${secretValues[15]}\\"}\n{\\"password\\": \\"${secretValues[16]}\\"}\ntoken=${secretValues[17]}，${fullwidthClaim}\ncurl -H Cookie:session=${secretValues[18]} https://example.test/unquoted-header\ncurl --header=Set-Cookie:sid=${secretValues[19]} https://example.test/unquoted-set-header\ncurl -b 'session=${secretValues[20]}' https://example.test/curl-b\ncurl --cookie 'sid=${secretValues[21]}' https://example.test/curl-cookie\n${longTail}`,
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'secret-session',
  })])

  const packet = run(fixture)
  const samples = samplesFrom(packet)
  const manifestPath = manifestPathFor(fixture)
  const persisted = fs.readFileSync(manifestPath, 'utf8')

  assert.equal(samples[0].answer.endsWith(longTail), true)
  assert.equal(samples[0].answer.length > 6000, true)
  assert.equal(samples[0].answer.includes(trailingClaim), true)
  assert.equal(persisted.includes(trailingClaim), true)
  assert.equal(samples[0].answer.includes(fullwidthClaim), true)
  assert.equal(persisted.includes(fullwidthClaim), true)
  assert.equal(samples[0].answer.includes('Cookie: session=[REDACTED]; sid=[REDACTED]; theme=[REDACTED]'), true)
  assert.equal(samples[0].answer.includes('Set-Cookie: session_id=[REDACTED]; Path=[REDACTED]; HttpOnly'), true)
  assert.equal(samples[0].answer.includes('> Cookie: session=[REDACTED]'), true)
  assert.equal(samples[0].answer.includes('< Set-Cookie: sid=[REDACTED]'), true)
  assert.equal(samples[0].answer.includes("curl -H 'Cookie: session=[REDACTED]' https://example.test/after-cookie"), true)
  assert.equal(samples[0].answer.includes('{"Cookie": "session=[REDACTED]"}'), true)
  assert.equal(samples[0].answer.includes('{\\"Cookie\\": \\"session=[REDACTED]\\"}'), true)
  assert.equal(samples[0].answer.includes('{\\"password\\": \\"[REDACTED]\\"}'), true)
  assert.equal(samples[0].answer.includes('https://example.test/unquoted-header'), true)
  assert.equal(samples[0].answer.includes('https://example.test/unquoted-set-header'), true)
  assert.equal(samples[0].answer.includes('https://example.test/curl-b'), true)
  assert.equal(samples[0].answer.includes('https://example.test/curl-cookie'), true)
  for (const [index, secret] of secretValues.entries()) {
    assert.equal(persisted.includes(secret), false, `persisted secret index ${index}`)
    assert.equal(samples[0].answer.includes(secret), false, `packet secret index ${index}`)
  }
  assert.equal(persisted.includes('[REDACTED]'), true)
  assert.equal(modeFor(fixture.reports), 0o700)
  assert.equal(modeFor(manifestPath), 0o600)
})

test('redacts quoted sensitive assignments through escaped quotes while preserving trailing claims', () => {
  const fixture = makeFixture()
  const secretTail = 'ESCAPED-QUOTE-SECRET-TAIL-123456'
  const trailingClaim = '已完成全部驗證但沒有附上證據'
  const backslash = String.fromCharCode(0x5c)
  writeRows(path.join(fixture.project, 'escaped-quote.jsonl'), [assistant({
    text: `{${backslash}"password${backslash}":${backslash}"prefix${secretTail}${backslash.repeat(5)}"} ${trailingClaim}\n${repeat('補', 220)}`,
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'escaped-quote-session',
  })])

  const packet = run(fixture)
  const answer = samplesFrom(packet)[0].answer
  const persisted = fs.readFileSync(manifestPathFor(fixture), 'utf8')

  assert.equal(answer.includes(secretTail), false)
  assert.equal(persisted.includes(secretTail), false)
  assert.equal(answer.includes(trailingClaim), true)
  assert.equal(persisted.includes(trailingClaim), true)
})

test('redacts cookie pair values while preserving same-line unsupported claims', () => {
  const fixture = makeFixture()
  const cookieSecret = 'COOKIE-SECRET-ONE-123456'
  const setCookieSecret = 'SET-COOKIE-SECRET-TWO-123456'
  const cookieClaim = '已完成 Cookie 全部驗證但沒有證據'
  const setCookieClaim = '已完成 Set-Cookie 全部驗證但沒有證據'
  writeRows(path.join(fixture.project, 'cookie-claim.jsonl'), [assistant({
    text: `Cookie: session=${cookieSecret}; theme=dark：${cookieClaim}\nSet-Cookie: sid=${setCookieSecret}; Path=/; HttpOnly。${setCookieClaim}\n${repeat('補', 220)}`,
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'cookie-claim-session',
  })])

  const packet = run(fixture)
  const answer = samplesFrom(packet)[0].answer
  const persisted = fs.readFileSync(manifestPathFor(fixture), 'utf8')

  assert.equal(answer.includes(cookieSecret), false)
  assert.equal(answer.includes(setCookieSecret), false)
  assert.equal(persisted.includes(cookieSecret), false)
  assert.equal(persisted.includes(setCookieSecret), false)
  assert.equal(answer.includes(`Cookie: session=[REDACTED]; theme=[REDACTED]：${cookieClaim}`), true)
  assert.equal(answer.includes(`Set-Cookie: sid=[REDACTED]; Path=[REDACTED]; HttpOnly。${setCookieClaim}`), true)
  assert.equal(persisted.includes(cookieClaim), true)
  assert.equal(persisted.includes(setCookieClaim), true)
})

test('publishes one complete manifest under concurrent sampling', async () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'concurrent.jsonl'), [assistant({
    text: repeat('m', 220),
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'manifest-session',
  })])

  const packets = await Promise.all(Array.from({ length: 24 }, () => runAsync(fixture)))
  assert.equal(new Set(packets.map((packet) => packet.manifest_hash)).size, 1)
  assert.equal(new Set(packets.map((packet) => packet.challenge)).size, 1)
  const persisted = JSON.parse(fs.readFileSync(manifestPathFor(fixture), 'utf8'))
  assert.equal(persisted.manifest_hash, packets[0].manifest_hash)
  assert.equal(persisted.challenge, packets[0].challenge)
  assert.equal(persisted.samples[0].session, 'manifest-session')
})

test('finalizer rejects an audit bound to different sampled answer content', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('a', 220),
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'digest-bound-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFrom(sampled)
  const audit = auditFor(samples)
  const oldSamplesSha256 = sha256(JSON.stringify(samples))
  const manifestPath = manifestPathFor(fixture)
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  manifest.samples[0].answer = repeat('b', 220)
  manifest.challenge = 'c'.repeat(64)
  const payload = {
    date: manifest.date,
    eligible: manifest.eligible,
    samples: manifest.samples,
    challenge: manifest.challenge,
  }
  manifest.manifest_hash = sha256(JSON.stringify(payload))
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, { mode: 0o600 })

  const finalized = run({ ...fixture, mode: 'finalize', audit, samplesSha256: oldSamplesSha256 })
  assert.equal(finalized.ok, false)
  assert.equal(fs.existsSync(publicationPathFor(fixture)), false)
})

test('finalize validates structured rows and generates the complete report', () => {
  const fixture = makeFixture()
  const firstPath = path.join(fixture.project, 'first.jsonl')
  const secondPath = path.join(fixture.project, 'second.jsonl')
  writeRows(firstPath, [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'first-session',
  })])
  writeRows(secondPath, [assistant({
    text: repeat('w', 220),
    timestamp: '2026-08-06T02:00:00.000Z',
    sessionId: 'second-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFrom(sampled)
  const manifestPath = manifestPathFor(fixture)
  const reportPath = reportPathFor(fixture)
  const manifestBefore = fs.readFileSync(manifestPath, 'utf8')

  writeRows(path.join(fixture.project, 'concurrent.jsonl'), [assistant({
    text: repeat('x', 220),
    timestamp: '2026-08-06T03:00:00.000Z',
    sessionId: 'concurrent-session',
  })])
  assert.equal(run(fixture).manifest_hash, sampled.manifest_hash)
  assert.equal(fs.readFileSync(manifestPath, 'utf8'), manifestBefore)

  const validAudit = auditFor(samples, [
    [
      { type: 'unsourced-number', quote: 'vvvvv' },
      { type: 'doc-as-evidence', quote: 'vvvv' },
    ],
    [{ type: 'unsourced-number', quote: 'wwwww' }],
  ])
  const reordered = { rows: [...validAudit.rows].reverse() }
  assert.equal(run({ ...fixture, mode: 'finalize', audit: reordered }).ok, false)

  const invalidQuote = structuredClone(validAudit)
  invalidQuote.rows[0].findings[0].quote = 'not in the answer'
  assert.equal(run({ ...fixture, mode: 'finalize', audit: invalidQuote }).ok, false)

  const duplicateType = structuredClone(validAudit)
  duplicateType.rows[0].findings.push({ type: 'unsourced-number', quote: 'vvv' })
  assert.equal(run({ ...fixture, mode: 'finalize', audit: duplicateType }).ok, false)

  const finalized = run({ ...fixture, mode: 'finalize', audit: validAudit })
  assert.equal(finalized.ok, true)
  assert.equal(finalized.challenge, sampled.challenge)
  assert.equal(finalized.eligible, 2)
  assert.equal(finalized.sample_count, 2)
  assert.equal(finalized.tp_style_violation_count, 2)
  assert.equal(fs.existsSync(reportPath), true)
  assert.equal(fs.existsSync(path.join(fixture.reports, '2026-08-06-evidence-level.draft.md')), false)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)

  const reportContent = fs.readFileSync(reportPath, 'utf8')
  assert.equal(reportContent.includes('- unsourced-number：2\n- doc-as-evidence：1'), true)
  assert.equal(reportContent.includes('命中 unsourced-number、doc-as-evidence'), true)
  assert.equal(reportContent.includes('本報告只判單則回答文字；每次最多抽樣 20 則；code 不計入 200 字長度門檻，但完整保留在受評回答；未核對回答所述外部事實真偽。'), true)
  const receiptPath = receiptPathFor(fixture)
  assert.equal(modeFor(reportPath), 0o600)
  assert.equal(modeFor(receiptPath), 0o600)

  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
  fs.writeFileSync(receiptPath, `${JSON.stringify({ ...receipt, challenge: 'f'.repeat(64) })}\n`)
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
  fs.writeFileSync(receiptPath, `${JSON.stringify({ ...receipt, sample_count: receipt.sample_count + 1 })}\n`)
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
  fs.writeFileSync(receiptPath, `${JSON.stringify(receipt)}\n`)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)

  fs.appendFileSync(reportPath, '\nchanged\n')
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
  fs.writeFileSync(reportPath, reportContent)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)
  fs.appendFileSync(manifestPath, ' ')
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
})

test('concurrent finalizers are idempotent and return one verified challenge', async () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('q', 220),
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'finalize-session',
  })])
  const sampled = run(fixture)
  const audit = auditFor(samplesFrom(sampled))

  const packets = await Promise.all(Array.from({ length: 12 }, () => runAsync({ ...fixture, mode: 'finalize', audit })))
  assert.equal(packets.every((packet) => packet.ok), true)
  assert.equal(new Set(packets.map((packet) => packet.challenge)).size, 1)
  assert.equal(packets[0].challenge, sampled.challenge)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)
  assert.equal(run({ ...fixture, mode: 'finalize', audit: { rows: [] } }).ok, true)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)

  const reportPath = reportPathFor(fixture)
  const receiptPath = receiptPathFor(fixture)
  const expectedReport = fs.readFileSync(reportPath, 'utf8')
  fs.writeFileSync(reportPath, 'corrupt report')
  fs.rmSync(receiptPath)
  const recovered = run({ ...fixture, mode: 'finalize', audit: { rows: [] } })
  assert.equal(recovered.ok, true)
  assert.equal(fs.readFileSync(reportPath, 'utf8'), expectedReport)
  assert.equal(fs.existsSync(receiptPath), true)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)
})

test('conflicting concurrent finalizers publish one atomic first-wins report without touching stale locks', async () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('r', 220),
    timestamp: '2026-08-06T01:00:00.000Z',
    sessionId: 'conflicting-finalizer-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFrom(sampled)
  const passAudit = auditFor(samples)
  const failAudit = auditFor(samples, [[{ type: 'unsourced-completion', quote: 'rrrrr' }]])
  const staleLock = path.join(fixture.reports, '2026-08-06-evidence-level.finalize.lock')
  fs.writeFileSync(staleLock, `${JSON.stringify({ pid: 999999 })}\n`, { mode: 0o600 })
  fs.utimesSync(staleLock, new Date('2020-01-01T00:00:00Z'), new Date('2020-01-01T00:00:00Z'))

  const packets = await Promise.all(Array.from({ length: 20 }, (_, index) =>
    runAsync({ ...fixture, mode: 'finalize', audit: index % 2 === 0 ? passAudit : failAudit })
  ))
  const counts = new Set(packets.map((packet) => packet.tp_style_violation_count))
  const publication = JSON.parse(fs.readFileSync(publicationPathFor(fixture), 'utf8'))
  const report = fs.readFileSync(reportPathFor(fixture), 'utf8')

  assert.equal(packets.every((packet) => packet.ok), true)
  assert.equal(counts.size, 1)
  assert.equal(publication.tp_style_violation_count, packets[0].tp_style_violation_count)
  assert.equal(report, publication.report)
  assert.equal(fs.existsSync(staleLock), true)
  assert.equal(modeFor(publicationPathFor(fixture)), 0o600)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)
})

test('uses report filename dates for the seven-day due boundary', () => {
  const fixture = makeFixture()
  fs.writeFileSync(path.join(fixture.reports, '2026-07-30-evidence-level.md'), 'partial report')
  fs.writeFileSync(path.join(fixture.reports, '2026-08-01-evidence-level.md'), 'unverified report')
  const sampled = run({ ...fixture, date: '2026-07-31', mode: 'sample' })
  const finalized = run({ ...fixture, date: '2026-07-31', mode: 'finalize', audit: auditFor(samplesFrom(sampled)) })
  assert.equal(finalized.ok, true)
  fs.utimesSync(path.join(fixture.reports, '2026-07-31-evidence-level.md'), new Date('2020-01-01T00:00:00Z'), new Date('2020-01-01T00:00:00Z'))

  const sixDays = run({ ...fixture, date: '2026-08-06', mode: 'due' })
  const sevenDays = run({ ...fixture, date: '2026-08-07', mode: 'due' })
  assert.deepEqual(sixDays, {
    date: '2026-08-06',
    due: false,
    last_success_date: '2026-07-31',
    days_since: 6,
  })
  assert.deepEqual(sevenDays, {
    date: '2026-08-07',
    due: true,
    last_success_date: '2026-07-31',
    days_since: 7,
  })

  fs.rmSync(path.join(fixture.reports, '2026-07-31-evidence-level.md'))
  assert.deepEqual(run({ ...fixture, date: '2026-08-07', mode: 'due' }), {
    date: '2026-08-07',
    due: true,
    last_success_date: null,
    days_since: null,
  })
})
