import assert from 'node:assert/strict'
import crypto from 'node:crypto'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import test from 'node:test'
import { spawn, spawnSync } from 'node:child_process'

const script = '/Users/linhancheng/code/social-info/scripts/local-analysis/evidence-level-sample.mjs'
const TEST_AUDIT_NONCE = '9'.repeat(64)

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
const samplesFileShaFor = (reports, date) => sha256(fs.readFileSync(path.join(reports, `${date}-evidence-level-samples.txt`)))
const argsFor = ({ date = '2026-08-14', root, reports, mode = 'sample', audit = null, auditTranscripts = null, samplesSha256 = null, samplesFileSha256 = null, auditNonce = null, reauditSamplesFileSha256 = null, reauditNonce = null }) => [
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
  ...(auditTranscripts == null ? [] : ['--audit-from-transcripts', auditTranscripts]),
  ...(!['finalize', 'prepare-reaudit'].includes(mode) ? [] : [
    '--samples-sha256',
    samplesSha256 ?? samplesShaFor(reports, date),
    '--samples-file-sha256',
    samplesFileSha256 ?? samplesFileShaFor(reports, date),
    '--audit-nonce',
    auditNonce ?? TEST_AUDIT_NONCE,
  ]),
  ...(mode !== 'finalize' || (reauditSamplesFileSha256 == null && reauditNonce == null) ? [] : [
    '--reaudit-samples-file-sha256',
    reauditSamplesFileSha256 ?? '0',
    '--reaudit-nonce',
    reauditNonce ?? '0',
  ]),
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
const manifestPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level-manifest.json`)
const samplesFor = (fixture, date = '2026-08-14') =>
  JSON.parse(fs.readFileSync(manifestPathFor(fixture, date), 'utf8')).samples
const samplesTextPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level-samples.txt`)
const reauditSamplesTextPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level-reaudit-samples.txt`)
const reportPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level.md`)
const receiptPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level.verified.json`)
const publicationPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level.publish.json`)
const versionPathFor = (fixture, date = '2026-08-14') => path.join(fixture.reports, `${date}-evidence-level.version.json`)
const modeFor = (file) => fs.statSync(file).mode & 0o777
const auditFor = (samples, findings = [], auditNonce = TEST_AUDIT_NONCE) => ({
  audit_nonce: auditNonce,
  rows: samples.map((sample, index) => ({
    timestamp: sample.timestamp,
    session: sample.session,
    path: sample.path,
    result: findings[index]?.length ? 'FAIL' : 'PASS',
    findings: findings[index] ?? [],
  })),
})
const readResultText = (value, startLine = 1) => value.split('\n').map((line, index) => `${startLine + index}\t${line}`).join('\n')
const writeAuditTranscript = ({ directory, name = 'agent-audit.jsonl', samplesPath, samplesText, audit, includeRead = true, includeOutput = true, chunks = null, extraTool = null }) => {
  const content = []
  const readChunks = chunks ?? [{ offset: null, text: samplesText }]
  if (includeRead) {
    readChunks.forEach((chunk, index) => content.push({
      type: 'tool_use',
      name: 'Read',
      id: `read-${index + 1}`,
      input: { file_path: samplesPath, ...(chunk.offset == null ? {} : { offset: chunk.offset }), ...(chunk.limit == null ? {} : { limit: chunk.limit }) },
    }))
  }
  if (extraTool != null) content.push(extraTool)
  const rows = [{ message: { content } }]
  if (includeRead) {
    readChunks.forEach((chunk, index) => rows.push({ message: { content: [{ type: 'tool_result', tool_use_id: `read-${index + 1}`, content: readResultText(chunk.text, chunk.offset ?? 1) }] } }))
  }
  if (includeOutput) rows.push({ message: { content: [{ type: 'tool_use', name: 'StructuredOutput', input: audit }] } })
  fs.writeFileSync(path.join(directory, name), `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`)
}

test('filters known-good and includes known-bad and 200-character boundary', () => {
  const fixture = makeFixture()
  const file = path.join(fixture.project, 'fixture.jsonl')
  writeRows(file, [
    assistant({ text: repeat('a', 250), timestamp: '2026-08-09T01:00:00.000Z', stopReason: 'tool_use', sessionId: 'tool-message' }),
    assistant({ text: repeat('b', 250), timestamp: '2026-08-09T02:00:00.000Z', sidechain: true, sessionId: 'sidechain' }),
    assistant({ text: `${repeat('c', 199)}\n\`\`\`js\n${repeat('x', 100)}\n\`\`\``, timestamp: '2026-08-09T03:00:00.000Z', sessionId: 'boundary-199' }),
    assistant({ text: `${repeat('i', 199)}\n    ${repeat('z', 300)}`, timestamp: '2026-08-09T03:30:00.000Z', sessionId: 'indented-code-199' }),
    assistant({ text: `${repeat('d', 200)}\n\`\`\`js\n${repeat('y', 100)}\n\`\`\``, timestamp: '2026-08-09T04:00:00.000Z', sessionId: 'boundary-200' }),
    assistant({ text: repeat('e', 240), timestamp: '2026-08-09T05:00:00.000Z', sessionId: 'known-bad' }),
  ])

  const packet = run(fixture)
  const samples = samplesFor(fixture)
  assert.equal(packet.eligible, 2)
  assert.equal(packet.sample_count, 2)
  assert.deepEqual(samples.map((sample) => sample.session), ['boundary-200', 'known-bad'])
  assert.equal(samples[0].answer.includes('```js'), true)
  assert.equal(packet.samples_sha256, sha256(JSON.stringify(samples)))
  assert.equal(packet.samples_file_sha256, sha256(fs.readFileSync(samplesTextPathFor(fixture))))
  assert.equal(Object.hasOwn(packet, 'samples_b64'), false)
  const samplesTextPath = samplesTextPathFor(fixture)
  const samplesText = fs.readFileSync(samplesTextPath, 'utf8')
  assert.equal(modeFor(samplesTextPath), 0o600)
  assert.equal(samplesText.includes(`=== BEGIN SAMPLE 1/2 [${packet.challenge.slice(0, 16)}] ===`), true)
  assert.equal(samplesText.includes(`=== END SAMPLE 2/2 [${packet.challenge.slice(0, 16)}] ===`), true)
  assert.equal(samplesText.includes(samples[0].answer), true)
  assert.equal(samplesText.includes(samples[1].answer), true)
})

test('samples at most 20 answers in deterministic total order', () => {
  const fixture = makeFixture()
  const file = path.join(fixture.project, 'many.jsonl')
  const rows = Array.from({ length: 41 }, (_, index) => assistant({
    text: repeat(String(index % 10), 220),
    timestamp: `2026-08-10T00:${String(index).padStart(2, '0')}:00.000Z`,
    sessionId: `session-${String(index).padStart(2, '0')}`,
  }))
  writeRows(file, rows)

  const packet = run(fixture)
  const samples = samplesFor(fixture)
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
    timestamp: '2026-08-10T00:00:00.000Z',
    sessionId: 'z-session',
  })])
  writeRows(path.join(secondProject, 'same.jsonl'), [assistant({
    text: repeat('a', 220),
    timestamp: '2026-08-10T00:00:00.000Z',
    sessionId: 'accent-session',
  })])

  const packet = run(fixture)
  assert.deepEqual(samplesFor(fixture).map((sample) => sample.session), ['z-session', 'accent-session'])
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
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'secret-session',
  })])

  const packet = run(fixture)
  const samples = samplesFor(fixture)
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
  const samplesText = fs.readFileSync(samplesTextPathFor(fixture), 'utf8')
  for (const [index, secret] of secretValues.entries()) {
    assert.equal(persisted.includes(secret), false, `persisted secret index ${index}`)
    assert.equal(samples[0].answer.includes(secret), false, `packet secret index ${index}`)
    assert.equal(samplesText.includes(secret), false, `samples text secret index ${index}`)
  }
  assert.equal(samplesText.includes(trailingClaim), true)
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
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'escaped-quote-session',
  })])

  const packet = run(fixture)
  const answer = samplesFor(fixture)[0].answer
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
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'cookie-claim-session',
  })])

  const packet = run(fixture)
  const answer = samplesFor(fixture)[0].answer
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
    timestamp: '2026-08-14T01:00:00.000Z',
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
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'digest-bound-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
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

test('finalizer rejects a one-byte samples text mutation before publication', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('a', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'file-bound-session',
  })])
  run(fixture)
  const audit = auditFor(samplesFor(fixture))
  const originalFileSha256 = samplesFileShaFor(fixture.reports, '2026-08-14')
  fs.appendFileSync(samplesTextPathFor(fixture), 'x')

  const finalized = run({ ...fixture, mode: 'finalize', audit, samplesFileSha256: originalFileSha256 })
  assert.equal(finalized.ok, false)
  assert.equal(fs.existsSync(publicationPathFor(fixture)), false)
})

test('finalize validates structured rows and generates the complete report', () => {
  const fixture = makeFixture()
  const firstPath = path.join(fixture.project, 'first.jsonl')
  const secondPath = path.join(fixture.project, 'second.jsonl')
  writeRows(firstPath, [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'first-session',
  })])
  writeRows(secondPath, [assistant({
    text: repeat('w', 220),
    timestamp: '2026-08-14T02:00:00.000Z',
    sessionId: 'second-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const manifestPath = manifestPathFor(fixture)
  const reportPath = reportPathFor(fixture)
  const manifestBefore = fs.readFileSync(manifestPath, 'utf8')

  writeRows(path.join(fixture.project, 'concurrent.jsonl'), [assistant({
    text: repeat('x', 220),
    timestamp: '2026-08-14T03:00:00.000Z',
    sessionId: 'concurrent-session',
  })])
  assert.equal(run(fixture).manifest_hash, sampled.manifest_hash)
  assert.equal(fs.readFileSync(manifestPath, 'utf8'), manifestBefore)

  const validAudit = auditFor(samples, [
    [
      { type: 'unsourced-number', quote: 'vvvvv' },
      { type: 'doc-as-evidence', quote: 'vvvv' },
      { type: 'unsourced-mechanism', quote: 'vvv' },
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
  assert.equal(finalized.samples_sha256, sampled.samples_sha256)
  assert.equal(finalized.samples_file_sha256, sampled.samples_file_sha256)
  assert.equal(fs.existsSync(reportPath), true)
  assert.equal(fs.existsSync(path.join(fixture.reports, '2026-08-14-evidence-level.draft.md')), false)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)

  const reportContent = fs.readFileSync(reportPath, 'utf8')
  assert.equal(reportContent.includes('- unsourced-number：2\n- doc-as-evidence：1'), true)
  assert.equal(reportContent.includes('unsourced-mechanism：1'), false)
  assert.equal(reportContent.includes('命中 unsourced-number、unsourced-mechanism、doc-as-evidence'), true)
  const publication = JSON.parse(fs.readFileSync(publicationPathFor(fixture), 'utf8'))
  assert.deepEqual(publication.top_violations, [
    { type: 'unsourced-number', count: 2 },
    { type: 'doc-as-evidence', count: 1 },
  ])
  assert.equal(reportContent.includes('本報告只判單則回答文字；每次最多抽樣 20 則；code 不計入 200 字長度門檻，但完整保留在受評回答；未核對回答所述外部事實真偽。'), true)
  const receiptPath = receiptPathFor(fixture)
  assert.equal(modeFor(reportPath), 0o600)
  assert.equal(modeFor(receiptPath), 0o600)

  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
  assert.equal(receipt.schema_version, 2)
  assert.equal(receipt.samples_file_sha256, sampled.samples_file_sha256)
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

test('v2 receipt invalidates when samples text changes or disappears', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('s', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'receipt-v2-session',
  })])
  run(fixture)
  const audit = auditFor(samplesFor(fixture), [[{ type: 'unsourced-completion', quote: 'sssss' }]])
  assert.equal(run({ ...fixture, mode: 'finalize', audit, reauditSamplesFileSha256: '0', reauditNonce: '0' }).ok, true)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)

  const samplesTextPath = samplesTextPathFor(fixture)
  const samplesText = fs.readFileSync(samplesTextPath)
  fs.appendFileSync(samplesTextPath, 'x')
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
  fs.writeFileSync(samplesTextPath, samplesText)
  assert.equal(run({ ...fixture, mode: 'due' }).due, false)
  fs.rmSync(samplesTextPath)
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
})

test('new pre-cutoff v2 packet cannot downgrade while version commitment remains', () => {
  const fixture = makeFixture()
  const date = '2026-08-13'
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('g', 220),
    timestamp: '2026-08-13T01:00:00.000Z',
    sessionId: 'full-downgrade-session',
  })])
  run({ ...fixture, date })
  const audit = auditFor(samplesFor(fixture, date), [[{ type: 'unsourced-completion', quote: 'ggggg' }]])
  assert.equal(run({ ...fixture, date, mode: 'finalize', audit }).ok, true)
  const manifestPath = manifestPathFor(fixture, date)
  const publicationPath = publicationPathFor(fixture, date)
  const receiptPath = receiptPathFor(fixture, date)
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  const publication = JSON.parse(fs.readFileSync(publicationPath, 'utf8'))
  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
  delete manifest.schema_version
  manifest.manifest_hash = sha256(JSON.stringify({ date: manifest.date, eligible: manifest.eligible, samples: manifest.samples, challenge: manifest.challenge }))
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`)
  delete publication.schema_version
  delete publication.samples_file_sha256
  delete publication.top_violations
  publication.manifest_sha256 = sha256(fs.readFileSync(manifestPath))
  fs.writeFileSync(publicationPath, `${JSON.stringify(publication)}\n`)
  delete receipt.schema_version
  delete receipt.samples_file_sha256
  delete receipt.top_violations
  receipt.manifest_sha256 = publication.manifest_sha256
  receipt.publication_sha256 = sha256(fs.readFileSync(publicationPath))
  fs.writeFileSync(receiptPath, `${JSON.stringify(receipt)}\n`)
  fs.rmSync(samplesTextPathFor(fixture, date))

  assert.equal(fs.existsSync(versionPathFor(fixture, date)), true)
  assert.equal(run({ ...fixture, date, mode: 'due' }).due, true)
})

test('version commitment mismatch bad permissions and missing new v2 are rejected', () => {
  for (const mutation of ['mismatch', 'permissions', 'missing']) {
    const fixture = makeFixture()
    writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
      text: repeat('c', 220),
      timestamp: '2026-08-14T01:00:00.000Z',
      sessionId: `commitment-${mutation}`,
    })])
    run(fixture)
    const versionPath = versionPathFor(fixture)
    if (mutation === 'mismatch') {
      const version = JSON.parse(fs.readFileSync(versionPath, 'utf8'))
      fs.writeFileSync(versionPath, `${JSON.stringify({ ...version, manifest_hash: 'f'.repeat(64) })}\n`)
    }
    if (mutation === 'permissions') fs.chmodSync(versionPath, 0o644)
    if (mutation === 'missing') fs.rmSync(versionPath)
    assert.equal(run({ ...fixture, mode: 'due' }).due, true, mutation)
  }
})

test('schema_version null is rejected for v2 manifests publications and receipts', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('n', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'null-version-session',
  })])
  run(fixture)
  const manifestPath = manifestPathFor(fixture)
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
  manifest.schema_version = null
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`)
  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
})

test('v2 packet cannot downgrade to v1 after samples text disappears', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('d', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'receipt-downgrade-session',
  })])
  run(fixture)
  const audit = auditFor(samplesFor(fixture), [[{ type: 'unsourced-completion', quote: 'ddddd' }]])
  assert.equal(run({ ...fixture, mode: 'finalize', audit }).ok, true)
  const publicationPath = publicationPathFor(fixture)
  const receiptPath = receiptPathFor(fixture)
  const receipt = JSON.parse(fs.readFileSync(receiptPath, 'utf8'))
  delete receipt.schema_version
  delete receipt.samples_file_sha256
  delete receipt.top_violations
  receipt.publication_sha256 = sha256(fs.readFileSync(publicationPath))
  fs.writeFileSync(receiptPath, `${JSON.stringify(receipt)}\n`)
  fs.rmSync(samplesTextPathFor(fixture))

  assert.equal(run({ ...fixture, mode: 'due' }).due, true)
})

test('pre-cutoff markerless v1 manifest can finish publication', () => {
  const fixture = makeFixture()
  const date = '2026-08-13'
  fs.chmodSync(fixture.reports, 0o700)
  const sample = {
    session: 'legacy-finalize-session',
    path: '/tmp/legacy-finalize.jsonl',
    timestamp: '2026-08-13T01:00:00.000Z',
    row_index: 0,
    answer: repeat('l', 220),
  }
  const manifestPayload = { date, eligible: 1, samples: [sample], challenge: 'b'.repeat(64) }
  const manifest = { ...manifestPayload, manifest_hash: sha256(JSON.stringify(manifestPayload)) }
  fs.writeFileSync(manifestPathFor(fixture, date), `${JSON.stringify(manifest)}\n`, { mode: 0o600 })
  const samplesText = [
    `=== BEGIN SAMPLE 1/1 [${manifest.challenge.slice(0, 16)}] ===`,
    `timestamp: ${sample.timestamp}`,
    `session: ${sample.session}`,
    `path: ${sample.path}`,
    'answer:',
    sample.answer,
    `=== END SAMPLE 1/1 [${manifest.challenge.slice(0, 16)}] ===`,
    '',
  ].join('\n')
  fs.writeFileSync(samplesTextPathFor(fixture, date), samplesText, { mode: 0o600 })
  const finalized = run({
    ...fixture,
    date,
    mode: 'finalize',
    audit: auditFor([sample], [[{ type: 'unsourced-number', quote: 'lllll' }]]),
    samplesSha256: sha256(JSON.stringify([sample])),
    samplesFileSha256: sha256(samplesText),
  })

  assert.equal(finalized.ok, true)
  assert.equal(finalized.samples_file_sha256, sha256(samplesText))
  assert.deepEqual(finalized.top_violations, [{ type: 'unsourced-number', count: 1 }])
  assert.equal(Object.hasOwn(JSON.parse(fs.readFileSync(publicationPathFor(fixture, date), 'utf8')), 'schema_version'), false)
  assert.equal(run({ ...fixture, date, mode: 'due' }).due, false)
})

test('pre-cutoff markerless v1 packet remains verified', () => {
  const fixture = makeFixture()
  const date = '2026-08-13'
  const manifestPath = manifestPathFor(fixture, date)
  const publicationPath = publicationPathFor({ ...fixture, reports: fixture.reports }, date)
  const receiptPath = receiptPathFor({ ...fixture, reports: fixture.reports }, date)
  const reportPath = reportPathFor({ ...fixture, reports: fixture.reports }, date)
  fs.chmodSync(fixture.reports, 0o700)
  const manifestPayload = { date, eligible: 0, samples: [], challenge: 'a'.repeat(64) }
  const manifest = { ...manifestPayload, manifest_hash: sha256(JSON.stringify(manifestPayload)) }
  fs.writeFileSync(manifestPath, `${JSON.stringify(manifest)}\n`, { mode: 0o600 })
  const report = 'legacy report\n'
  const publication = {
    date,
    manifest_sha256: sha256(fs.readFileSync(manifestPath)),
    samples_sha256: sha256(JSON.stringify([])),
    report,
    report_sha256: sha256(report),
    eligible: 0,
    sample_count: 0,
    tp_style_violation_count: 0,
    challenge: manifest.challenge,
  }
  fs.writeFileSync(publicationPath, `${JSON.stringify(publication)}\n`, { mode: 0o600 })
  fs.writeFileSync(reportPath, report, { mode: 0o600 })
  const receipt = {
    date,
    report_sha256: publication.report_sha256,
    manifest_sha256: publication.manifest_sha256,
    samples_sha256: publication.samples_sha256,
    publication_sha256: sha256(fs.readFileSync(publicationPath)),
    eligible: 0,
    sample_count: 0,
    tp_style_violation_count: 0,
    challenge: manifest.challenge,
  }
  fs.writeFileSync(receiptPath, `${JSON.stringify(receipt)}\n`, { mode: 0o600 })

  assert.equal(run({ ...fixture, date, mode: 'due' }).due, false)
})

test('concurrent finalizers are idempotent and return one verified challenge', async () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('q', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'finalize-session',
  })])
  const sampled = run(fixture)
  const audit = auditFor(samplesFor(fixture), [[{ type: 'unsourced-completion', quote: 'qqqqq' }]])

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
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'conflicting-finalizer-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const numberAudit = auditFor(samples, [[{ type: 'unsourced-number', quote: 'rrrrr' }]])
  const completionAudit = auditFor(samples, [[{ type: 'unsourced-completion', quote: 'rrrrr' }]])
  const staleLock = path.join(fixture.reports, '2026-08-14-evidence-level.finalize.lock')
  fs.writeFileSync(staleLock, `${JSON.stringify({ pid: 999999 })}\n`, { mode: 0o600 })
  fs.utimesSync(staleLock, new Date('2020-01-01T00:00:00Z'), new Date('2020-01-01T00:00:00Z'))

  const packets = await Promise.all(Array.from({ length: 20 }, (_, index) =>
    runAsync({ ...fixture, mode: 'finalize', audit: index % 2 === 0 ? numberAudit : completionAudit })
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
  run({ ...fixture, date: '2026-07-31', mode: 'sample' })
  const finalized = run({ ...fixture, date: '2026-07-31', mode: 'finalize', audit: auditFor(samplesFor(fixture, '2026-07-31')) })
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

test('prepare-reaudit writes only primary PASS samples with a distinct nonce', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answers.jsonl'), [
    assistant({ text: repeat('a', 220), timestamp: '2026-08-14T01:00:00.000Z', sessionId: 'pass-session' }),
    assistant({ text: repeat('b', 220), timestamp: '2026-08-14T02:00:00.000Z', sessionId: 'fail-session' }),
  ])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const primaryAudit = auditFor(samples, [[], [{ type: 'unsourced-number', quote: 'bbbbb' }]], sampled.audit_nonce)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: primaryAudit })

  const prepared = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  const subsetText = fs.readFileSync(reauditSamplesTextPathFor(fixture), 'utf8')
  assert.equal(prepared.reaudit_sample_count, 1)
  assert.notEqual(prepared.reaudit_nonce, sampled.audit_nonce)
  assert.equal(prepared.reaudit_samples_file_sha256, sha256(subsetText))
  assert.equal(subsetText.includes('pass-session'), true)
  assert.equal(subsetText.includes('fail-session'), false)
})

test('prepare-reaudit accepts zero-based Read results', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answers.jsonl'), [assistant({
    text: repeat('z', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'zero-based-read-session',
  })])
  const sampled = run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  const samplesText = fs.readFileSync(samplesTextPathFor(fixture), 'utf8')
  writeAuditTranscript({
    directory: wfDir,
    samplesPath: samplesTextPathFor(fixture),
    samplesText,
    audit: auditFor(samplesFor(fixture), [], sampled.audit_nonce),
    chunks: [{ offset: 0, text: samplesText }],
  })

  const prepared = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  assert.equal(prepared.reaudit_sample_count, 1)
})

test('v2 finalize rejects primary PASS rows when reaudit is omitted', () => {
  for (const source of ['transcript', 'base64']) {
    const fixture = makeFixture()
    writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
      text: repeat('p', 220),
      timestamp: '2026-08-14T01:00:00.000Z',
      sessionId: `missing-reaudit-${source}`,
    })])
    const sampled = run(fixture)
    const samples = samplesFor(fixture)
    const audit = auditFor(samples, [], sampled.audit_nonce)
    let finalized
    if (source === 'transcript') {
      const wfDir = path.join(fixture.reports, 'wf-transcripts')
      fs.mkdirSync(wfDir, { recursive: true })
      writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit })
      finalized = run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
    } else {
      finalized = run({ ...fixture, mode: 'finalize', audit, auditNonce: sampled.audit_nonce })
    }
    assert.equal(finalized.ok, false, source)
    assert.equal(fs.existsSync(publicationPathFor(fixture)), false, source)
  }
})

test('repeated preparer keeps the first subset artifact stable', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('r', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'stable-reaudit-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samples, [], sampled.audit_nonce) })

  const first = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  const firstText = fs.readFileSync(reauditSamplesTextPathFor(fixture), 'utf8')
  const second = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  assert.deepEqual(second, first)
  assert.equal(fs.readFileSync(reauditSamplesTextPathFor(fixture), 'utf8'), firstText)
})

test('finalize merges PASS-only reaudit and binds subset hash into publication and receipt', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answers.jsonl'), [
    assistant({ text: repeat('a', 220), timestamp: '2026-08-14T01:00:00.000Z', sessionId: 'primary-fail' }),
    assistant({ text: repeat('b', 220), timestamp: '2026-08-14T02:00:00.000Z', sessionId: 'reaudit-fail' }),
    assistant({ text: repeat('c', 220), timestamp: '2026-08-14T03:00:00.000Z', sessionId: 'both-pass' }),
  ])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const primaryAudit = auditFor(samples, [
    [{ type: 'unsourced-number', quote: 'aaaaa' }],
    [],
    [],
  ], sampled.audit_nonce)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, name: 'agent-primary.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: primaryAudit })
  const prepared = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  const subsetSamples = [samples[1], samples[2]]
  const secondAudit = auditFor(subsetSamples, [[{ type: 'doc-as-evidence', quote: 'bbbbb' }], []], prepared.reaudit_nonce)
  writeAuditTranscript({ directory: wfDir, name: 'agent-reaudit.jsonl', samplesPath: reauditSamplesTextPathFor(fixture), samplesText: fs.readFileSync(reauditSamplesTextPathFor(fixture), 'utf8'), audit: secondAudit })

  const finalized = run({
    ...fixture,
    mode: 'finalize',
    auditTranscripts: wfDir,
    auditNonce: sampled.audit_nonce,
    reauditNonce: prepared.reaudit_nonce,
    reauditSamplesFileSha256: prepared.reaudit_samples_file_sha256,
  })
  assert.equal(finalized.ok, true)
  assert.equal(finalized.tp_style_violation_count, 2)
  assert.deepEqual(finalized.top_violations, [
    { type: 'doc-as-evidence', count: 1 },
    { type: 'unsourced-number', count: 1 },
  ])
  const publication = JSON.parse(fs.readFileSync(publicationPathFor(fixture), 'utf8'))
  const receipt = JSON.parse(fs.readFileSync(receiptPathFor(fixture), 'utf8'))
  assert.equal(publication.reaudit_sample_count, 2)
  assert.equal(publication.reaudit_samples_file_sha256, prepared.reaudit_samples_file_sha256)
  assert.equal(publication.reaudit_nonce, prepared.reaudit_nonce)
  assert.equal(receipt.reaudit_sample_count, 2)
  assert.equal(receipt.reaudit_samples_file_sha256, prepared.reaudit_samples_file_sha256)
  assert.equal(receipt.reaudit_nonce, prepared.reaudit_nonce)
})

test('finalize rejects wrong reaudit nonce path and cross-transcript evidence', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'reaudit-closed-session',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, name: 'agent-primary.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samples, [], sampled.audit_nonce) })
  const prepared = run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce })
  writeAuditTranscript({ directory: wfDir, name: 'agent-wrong-path.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samples, [], prepared.reaudit_nonce) })
  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce, reauditNonce: prepared.reaudit_nonce, reauditSamplesFileSha256: prepared.reaudit_samples_file_sha256 }).ok, false)

  fs.rmSync(path.join(wfDir, 'agent-wrong-path.jsonl'))
  writeAuditTranscript({ directory: wfDir, name: 'agent-reaudit-read.jsonl', samplesPath: reauditSamplesTextPathFor(fixture), samplesText: fs.readFileSync(reauditSamplesTextPathFor(fixture), 'utf8'), audit: auditFor(samples, [], prepared.reaudit_nonce), includeOutput: false })
  writeAuditTranscript({ directory: wfDir, name: 'agent-reaudit-output.jsonl', samplesPath: reauditSamplesTextPathFor(fixture), samplesText: '', audit: auditFor(samples, [], prepared.reaudit_nonce), includeRead: false })
  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce, reauditNonce: prepared.reaudit_nonce, reauditSamplesFileSha256: prepared.reaudit_samples_file_sha256 }).ok, false)
})

test('finalize binds explicit null and zero reaudit fields when primary has no PASS rows', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'answer.jsonl'), [assistant({
    text: repeat('f', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'all-primary-fail',
  })])
  const sampled = run(fixture)
  const samples = samplesFor(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samples, [[{ type: 'silent-skip', quote: 'fffff' }]], sampled.audit_nonce) })
  const finalized = run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir, auditNonce: sampled.audit_nonce, reauditSamplesFileSha256: '0', reauditNonce: '0' })
  assert.equal(finalized.ok, true)
  const publication = JSON.parse(fs.readFileSync(publicationPathFor(fixture), 'utf8'))
  const receipt = JSON.parse(fs.readFileSync(receiptPathFor(fixture), 'utf8'))
  assert.equal(publication.reaudit_sample_count, 0)
  assert.equal(publication.reaudit_samples_file_sha256, null)
  assert.equal(publication.reaudit_nonce, null)
  assert.equal(receipt.reaudit_sample_count, 0)
  assert.equal(receipt.reaudit_samples_file_sha256, null)
  assert.equal(receipt.reaudit_nonce, null)
})

test('finalize binds audit rows to the exact samples text read in the same transcript', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'first-session',
  })])
  run(fixture)
  const samples = samplesFor(fixture)
  const validAudit = auditFor(samples, [[{ type: 'unsourced-number', quote: 'vvvvv' }]])
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  fs.writeFileSync(path.join(wfDir, 'agent-scan.jsonl'), `${JSON.stringify({
    message: { content: [{ type: 'tool_use', name: 'StructuredOutput', input: { ok: true, silent: false } }] },
  })}\n`)
  writeAuditTranscript({
    directory: wfDir,
    samplesPath: samplesTextPathFor(fixture),
    samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'),
    audit: validAudit,
  })
  const finalized = run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir })
  assert.equal(finalized.ok, true)
  assert.equal(finalized.tp_style_violation_count, 1)

  const conflicting = spawnSync(process.execPath, argsFor({
    ...fixture,
    mode: 'finalize',
    auditTranscripts: wfDir,
    audit: validAudit,
  }), { encoding: 'utf8' })
  assert.notEqual(conflicting.status, 0)
})

test('finalize rejects transcript read content that differs from the manifest samples', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'tampered-read-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({
    directory: wfDir,
    samplesPath: samplesTextPathFor(fixture),
    samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8').replace(/v/g, 'x'),
    audit: auditFor(samplesFor(fixture)),
  })

  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir }).ok, false)
})

test('finalize rejects an audit transcript that reads any other path', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'extra-read-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({
    directory: wfDir,
    samplesPath: samplesTextPathFor(fixture),
    samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'),
    audit: auditFor(samplesFor(fixture)),
    extraTool: { type: 'tool_use', name: 'Read', id: 'extra-read', input: { file_path: '/tmp/other.txt' } },
  })

  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir }).ok, false)
})

test('finalize skips a newer wrong-nonce transcript and selects the matching run', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'nonce-selection-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  const samplesText = fs.readFileSync(samplesTextPathFor(fixture), 'utf8')
  writeAuditTranscript({ directory: wfDir, name: 'agent-correct.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText, audit: auditFor(samplesFor(fixture)) })
  writeAuditTranscript({ directory: wfDir, name: 'agent-wrong.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText, audit: auditFor(samplesFor(fixture), [], '8'.repeat(64)) })
  fs.utimesSync(path.join(wfDir, 'agent-wrong.jsonl'), new Date(), new Date())

  assert.equal(run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir }).reaudit_sample_count, 1)
})

test('finalize rejects Read and StructuredOutput split across transcripts', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'split-transcript-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, name: 'agent-read.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samplesFor(fixture)), includeOutput: false })
  writeAuditTranscript({ directory: wfDir, name: 'agent-output.jsonl', samplesPath: samplesTextPathFor(fixture), samplesText: '', audit: auditFor(samplesFor(fixture)), includeRead: false })

  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir }).ok, false)
})

test('finalize accepts complete consecutive paginated Read results', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'paginated-read-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  const lines = fs.readFileSync(samplesTextPathFor(fixture), 'utf8').split('\n')
  const split = Math.floor(lines.length / 2)
  writeAuditTranscript({
    directory: wfDir,
    samplesPath: samplesTextPathFor(fixture),
    samplesText: '',
    audit: auditFor(samplesFor(fixture)),
    chunks: [
      { offset: 1, limit: split, text: lines.slice(0, split).join('\n') },
      { offset: Math.max(1, split - 1), limit: lines.length - split + 2, text: lines.slice(Math.max(0, split - 2)).join('\n') },
    ],
  })

  assert.equal(run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir }).reaudit_sample_count, 1)
})

test('finalize accepts literal ellipsis and truncated words inside samples', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: `${repeat('v', 210)} … user wrote truncated here`,
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'literal-truncated-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: fs.readFileSync(samplesTextPathFor(fixture), 'utf8'), audit: auditFor(samplesFor(fixture)) })

  assert.equal(run({ ...fixture, mode: 'prepare-reaudit', auditTranscripts: wfDir }).reaudit_sample_count, 1)
})

test('finalize rejects StructuredOutput emitted before Read results', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'early-output-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  const rows = [
    { message: { content: [{ type: 'tool_use', name: 'StructuredOutput', input: auditFor(samplesFor(fixture)) }] } },
    { message: { content: [{ type: 'tool_use', name: 'Read', id: 'read-1', input: { file_path: samplesTextPathFor(fixture) } }] } },
    { message: { content: [{ type: 'tool_result', tool_use_id: 'read-1', content: readResultText(fs.readFileSync(samplesTextPathFor(fixture), 'utf8')) }] } },
  ]
  fs.writeFileSync(path.join(wfDir, 'agent-audit.jsonl'), `${rows.map((row) => JSON.stringify(row)).join('\n')}\n`)

  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir }).ok, false)
})

test('finalize rejects truncated Read tool results', () => {
  const fixture = makeFixture()
  writeRows(path.join(fixture.project, 'first.jsonl'), [assistant({
    text: repeat('v', 220),
    timestamp: '2026-08-14T01:00:00.000Z',
    sessionId: 'truncated-read-session',
  })])
  run(fixture)
  const wfDir = path.join(fixture.reports, 'wf-transcripts')
  fs.mkdirSync(wfDir, { recursive: true })
  const samplesText = fs.readFileSync(samplesTextPathFor(fixture), 'utf8')
  writeAuditTranscript({ directory: wfDir, samplesPath: samplesTextPathFor(fixture), samplesText: samplesText.slice(0, -10), audit: auditFor(samplesFor(fixture)) })

  assert.equal(run({ ...fixture, mode: 'finalize', auditTranscripts: wfDir }).ok, false)
})
