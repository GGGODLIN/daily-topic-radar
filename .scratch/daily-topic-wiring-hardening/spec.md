## Problem Statement

每日話題分析的四個 workflow 版本共用三個脆弱接點：缺少日期時仍以 `unknown-date` 啟動 agent、PROBES baseline 透過 LLM 組超長 exact replacement、以及 probe 提供的合法來源 URL 無法通過後置 URL gate。這些問題不屬於各版本的內容策略，卻會造成可避免的 agent 成本、手動補寫與合法 URL 誤報。

## Solution

只硬化四個 daily-topic workflow 版本共用的接線，不改內容、成本或 audit 政策：日期缺失或格式錯誤時在第一個 agent 前 fail-fast；以 deterministic、section-bounded replacement 更新 PROBES 的唯一 `Last seen` 行；從結構化 probe 結果產生 exact trusted URL 清單，交給後置 verifier 放行。既有 verifier caller 保持相容，未列入 raw md、external feeds、固定 utility whitelist 或 trusted probe URL 的連結仍必須失敗。

## User Stories

1. As a daily-topic operator, I want a missing or malformed date rejected before any agent starts, so that an invocation mistake cannot spend an agent turn against `unknown-date`. [evidence: 2026-08-19 first workflow run aborted at input_precheck after one agent and 50,197 tokens]
2. As a daily-topic operator, I want the workflow to return a structured input error for an invalid date, so that the next action is to correct the invocation rather than rerun the aggregator. [inferred]
3. As a workflow maintainer, I want all four daily-topic variants to share the same date-input behavior, so that switching variants cannot reintroduce the same failure. [evidence: all four workflow files contain the same `unknown-date` fallback]
4. As a probe maintainer, I want a new baseline to replace only the target entry’s unique `Last seen` line, so that a long history line does not make writeback fragile. [evidence: 2026-08-19 writeback failed with `Edit failed: String to replace not found in file.`]
5. As a probe maintainer, I want writeback to fail closed when the target heading is missing, duplicated, or lacks exactly one `Last seen` line, so that the wrong probe entry is never modified. [inferred]
6. As a workflow operator, I want deterministic PROBES writeback to report old and new lines plus a verified outcome, so that the digest can state the actual writeback result. [inferred]
7. As a cost-conscious user, I want one mechanical executor to invoke a deterministic writeback helper for the whole batch instead of asking one LLM agent per entry to judge and compose replacements, so that the unavoidable filesystem-capable agent turn stays bounded and the update logic remains deterministic. [inferred]
8. As a digest writer, I want exact URLs supplied by structured probe results to be accepted by the post-write URL gate, so that legitimate release and weekly-watcher links can remain clickable. [evidence: 2026-08-19 v2.1.235 probe URL was the only `1 / 103 bad` URL]
9. As a security-conscious operator, I want probe URLs accepted by exact value rather than by broad domain whitelist, so that the writer cannot invent another URL on the same host and pass the gate. [inferred]
10. As an existing verifier caller, I want the current two- and three-argument invocations to keep working, so that adding trusted probe URLs does not break historical scripts or commands. [evidence: verifier currently accepts digest, raw md, and optional external-feeds directory]
11. As a digest operator, I want the workflow result to expose the trusted probe URLs and the exact verify command, so that main does not have to reconstruct quoting or forget the manifest. [inferred]
12. As a maintainer, I want known-good, known-bad, and boundary fixtures for each changed gate, so that future edits preserve both safety and compatibility. [evidence: gate-authoring checklist requires three fixture classes]
13. As a user of the minimal variant, I want its one-round A/D audit and known coverage gaps left unchanged, so that wiring hardening does not silently increase its cost. [user: "總之純改接線我接受"]
14. As a user of the other variants, I want their FactCheck, Verify, audit lens, model, and fan-out policies left unchanged, so that this change does not alter digest quality policy. [user: "總之純改接線我接受"]

## Implementation Decisions

- Apply the shared wiring behavior symmetrically to the default, full, minimal, and vendor daily-topic workflows because all four contain the same date fallback and LLM WriteBack pattern. [evidence: repository enumeration found one matching date fallback and one matching writeback block in each variant]
- Validate `args.date` as an exact calendar-date string in `YYYY-MM-DD` form before the first phase or agent call. Missing values, malformed values, and impossible calendar dates return `aborted=true`, `abort_stage=input_validation`, a concrete reason, and a next action that tells the caller to pass a valid date. [inferred]
- Do not use `unknown-date` as an executable fallback. [evidence: the fallback caused the 2026-08-19 wasted run]
- Preserve stringified and object Workflow args through the existing coercion behavior. [evidence: workflow scripts already support both forms]
- Replace the WriteBack per-entry agent fan-out with one mechanical executor agent that invokes a deterministic helper for the whole batch. The helper locates the exact probe heading, bounds the search before the next heading, requires exactly one `Last seen` line, replaces that line, and verifies that only the intended line changed. [evidence: official Workflow runtime does not expose filesystem or shell access to script glue]
- Preserve the existing writeback result contract: attempted, succeeded, failed, and skipped reason remain available to WriteDigest and main. Successful entries additionally retain old/new line evidence where the current result schema supports it. [inferred]
- Treat PROBES.md as Editable only within the unique target `Last seen` line. Workflow scripts and verifier logic are Locked; the user controls expansion beyond the three wiring changes. [user: "可以，總之純改接線我接受"]
- Derive trusted probe URLs only from structured `new_signals[].source` fields. Parse exact HTTP(S) URLs from those source strings; do not derive trust from digest HTML, writer output, prose domains, or arbitrary same-host URLs. [inferred]
- Return the exact trusted URL list in the workflow result and return a shell-safe `verify_cmd` that invokes the verifier with those exact URLs. [inferred]
- Extend the verifier interface without reassigning existing arguments: argument 1 remains digest, argument 2 raw md, argument 3 optional external-feeds directory, and arguments 4 onward are exact trusted probe URLs. [evidence: current verifier interface already reserves argument 3 for external feeds]
- Continue to accept URLs found in raw md, external feeds, or the existing utility whitelist. Accept a trusted probe URL only by exact string equality. [inferred]
- Missing trusted URLs preserve current behavior. A URL absent from every accepted source remains a hard failure. [evidence: current verifier exit contract is 0 for all green and 1 for any bad URL]
- Update the trigger-injected verify instruction and the fallback daily-topic command to prefer the returned `verify_cmd`, while retaining the old command shape as a no-manifest fallback. [inferred]
- Do not add a post-write finalize phase, a top-2 HTML section, a new audit round, or a broader whitelist in this change. [user: "總之純改接線我接受"]

## Testing Decisions

- Test the date gate at the existing workflow guard seam. Known-good: a valid leap-day date proceeds. Known-bad: missing date aborts before an agent call. Boundary: malformed and impossible calendar dates abort with `input_validation` rather than `input_precheck`.
- Assert the date guard contract across all four variants, not only the minimal file, because the defect is duplicated across the family.
- Test deterministic writeback using a temporary PROBES fixture. Known-good: one target heading and one long `Last seen` line changes exactly once. Known-bad: missing or duplicated heading／line fails without changing the file. Boundary: another entry’s `Last seen` remains byte-identical.
- Test that the writeback path invokes exactly one mechanical executor for the whole batch, contains no per-entry WriteBack fan-out, and reports attempted／succeeded／failed counts matching the fixture result.
- Test the verifier at its existing shell seam. Known-good: a digest URL absent from raw md but exactly present in trusted arguments passes. Known-bad: the same URL without trust fails. Boundary: a different URL on the same domain still fails, proving no host-wide whitelist expansion.
- Retain existing verifier fixtures for raw-md URLs, external-feed URLs, utility whitelist URLs, and mechanical HARD failures.
- Run the daily-topic trigger contract test because the injected verify instruction changes while version routing and lens descriptions must remain unchanged.
- Run syntax checks for all four Workflow scripts by wrapping their bodies in an async function, matching the existing workflow test convention.
- Use fresh temporary files for mutation tests; never edit the real PROBES.md during tests.

## Out of Scope

- Changing any digest’s content, HTML layout, recommendation section, or voice.
- Changing FactCheck, Verify, audit lenses, audit rounds, model routing, fan-out, or token policy.
- Fixing general writer inference errors beyond the existing audit process.
- Adding a broad GitHub, release, or probe-domain whitelist.
- Refactoring the four large workflow variants into a shared module.
- Repairing historical digest files or rerunning 2026-08-19.
- Adding a new runtime, service, database, or background process.

## Further Notes

The change crosses the repository boundary because the daily-topic workflows, trigger, command, and verifier live under the user’s Claude configuration while their generated report and one existing hook regression test live in this repository. The implementation must preserve family symmetry: every wiring change is either applied to all four variants or accompanied by an explicit reason why one variant cannot use it.

Gate surface classification:

| Surface | Classification | Rule |
|---|---|---|
| Workflow scripts and verifier | Locked | Tests and agents must not relax their own acceptance criteria |
| Target probe `Last seen` line | Editable | Exactly one bounded line may change |
| Other PROBES fields and entries | Locked for this change | Byte-identical after writeback |
| Digest HTML | Unchanged by this feature | Existing WriteDigest and audit flow remain authoritative |
| Scope expansion | Human-controlled | Audit policy, HTML content, and new whitelist rules require a separate decision |

Expected bypass checks:

| Attempted bypass | Expected interception |
|---|---|
| Invoke without date | Input validation aborts before any agent |
| Use a syntactically date-like but impossible date | Calendar validation rejects it |
| Duplicate a probe heading or `Last seen` line | Deterministic writeback fails closed without mutation |
| Put a same-domain but different URL in the digest | Exact trusted-URL check rejects it |
| Omit the trusted manifest | Existing raw／external／utility rules still apply and unknown URLs fail |
