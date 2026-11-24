# Skill Benchmark: cloud-sandbox

> ⚠️ **Overall verdict: INCOMPLETE — Required evidence is missing**

One or more required evaluation tiers did not complete, so this benchmark is not publication-complete.

## Evaluation Metadata

- Skill: `cloud-sandbox`
- Evaluation date: not recorded (legacy or non-live result)
- Evaluator version: `0.3.0`
- Evaluated source: not recorded (not supplied by the orchestration input)
- Evaluated source revision: not recorded (not supplied by the orchestration input)
- Evaluator container revision: not recorded (not supplied by the orchestration input)
- Agents: not recorded (legacy or non-live result)
- Tasks: not recorded (legacy or non-live result)
- Dataset digest: not recorded (legacy or non-live result)
- Attempts per task: 1
- Environment: not recorded (legacy or non-live result)
- Tier 3 evidence: required for publication
- Tier 3 live evaluation: SKIPPED — Tier 3 live evaluation skipped: A public LLM provider is required for live evaluation: No provider is configured. For NVIDIA Build, set: export SKILL\_EVAL\_LLM\_PROVIDER=nv\_build export NVIDIA\_API\_KEY='your-api-key' Get a key: https&#58;//build.nvidia.com Other SKILL\_EVAL\_LLM\_PROVIDER values and their keys: openai -&gt; OPENAI\_API\_KEY anthropic -&gt; ANTHROPIC\_API\_KEY More providers and setup options: https&#58;//docs.nvidia.com/skills/skillevaluator/configuration

## What This Report Answers

The three-tier evaluation checks whether the skill:

- is safe to use;
- produces correct answers;
- is discovered and activated when needed;
- helps the agent complete the user's goal and expected workflow; and
- avoids wasted skill and tool usage.

## Results at a Glance

Tier 3 live-agent scores were not available. See the tier status table for what ran.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **INCOMPLETE** | Missing trustworthy evidence from skillspector, semgrep, gitleaks |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Prerequisite unavailable |
| Tier 3 | Live agent evaluation | **SKIPPED (ADVISORY)** | Tier 3 live evaluation skipped: A public LLM provider is required for live evaluation: No provider is configured. For NVIDIA Build, set: export SKILL\_EVAL\_LLM\_PROVIDER=nv\_build export NVIDIA\_API\_KEY='your-api-key' Get a key: https&#58;//build.nvidia.com Other SKILL\_EVAL\_LLM\_PROVIDER values and their keys: openai -&gt; OPENAI\_API\_KEY anthropic -&gt; ANTHROPIC\_API\_KEY More providers and setup options: https&#58;//docs.nvidia.com/skills/skillevaluator/configuration |

Tier 2 validation was skipped and executed 0 checks.
- Prerequisite unavailable

Tier 3 validation was skipped and executed 0 checks.
- Tier 3 live evaluation skipped: A public LLM provider is required for live evaluation: No provider is configured. For NVIDIA Build, set: export SKILL\_EVAL\_LLM\_PROVIDER=nv\_build export NVIDIA\_API\_KEY='your-api-key' Get a key: https&#58;//build.nvidia.com Other SKILL\_EVAL\_LLM\_PROVIDER values and their keys: openai -&gt; OPENAI\_API\_KEY anthropic -&gt; ANTHROPIC\_API\_KEY More providers and setup options: https&#58;//docs.nvidia.com/skills/skillevaluator/configuration

## Blocking Findings

- **HIGH** PII/personal\_paths: Personal macOS user path: devin (`references/devin.md:8`)
- **HIGH** PII/personal\_paths: Personal Windows user path: Administrator (`references/devin.md:9`)
- **HIGH** PII/emails: Non-placeholder email address: qwe70302&#64;gmail.com — 2 occurrences (references/boxes-dev.md lines 3, 17) (`references/boxes-dev.md:3`)
- **HIGH** SCHEMA/author\_missing: Author not specified in metadata (`SKILL.md`)
- **MEDIUM** SCHEMA/body\_recommended\_section: Missing recommended section: '## Instructions' (`SKILL.md`)

Test execution limitations:

- No standard Python test-file candidates found; target tests were not executed and coverage was not measured. Consider adding tests.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **HIGH** PII/personal\_paths: Personal macOS user path: devin (`references/devin.md:8`)
- **HIGH** PII/personal\_paths: Personal Windows user path: Administrator (`references/devin.md:9`)
- **HIGH** PII/emails: Non-placeholder email address: qwe70302&#64;gmail.com — 2 occurrences (references/boxes-dev.md lines 3, 17) (`references/boxes-dev.md:3`)
- **HIGH** SCHEMA/author\_missing: Author not specified in metadata (`SKILL.md`)
- **MEDIUM** QUALITY/quality\_correctness: No documented scripts in table format (`SKILL.md`)
- 14 additional finding(s) are available in the full evaluation artifacts.

</details>

## Scoring Methodology

<details>
<summary>Show dimension definitions, source signals, and thresholds</summary>

| Dimension | Question | Scored signals |
|---|---|---|
| Security | Is it safe to use? | `security` (100%) |
| Correctness | Is the answer correct? | `accuracy` (100%) |
| Discoverability | Was the right skill loaded when needed? | `skill_execution` (100%) |
| Effectiveness | Did the skill help complete the task? | `goal_accuracy` (50%) + `behavior_check` (50%) |
| Efficiency | Did it avoid wasted tool or skill usage? | `skill_efficiency` (100%) |

- Dimension bands: PASS at 50% or above; NEUTRAL from 40% to below 50%; FAIL below 40%.
- Overall Tier 3 lift: PASS at +5 points or more; FAIL at -10 points or less; values between those bands are NEUTRAL.
- Overall verdict: PASS only when every configured dimension passes for at least one supported agent. Lift is reported as diagnostic evidence and does not override this gate.
- The 50% attempt pass threshold is a separate per-task gate; it is not the dimension pass threshold.
- Effectiveness is the equal-weight mean of goal completion (`goal_accuracy`) and expected workflow adherence (`behavior_check`).
- Token efficiency is a separate report-only signal. It does not change a dimension score or the overall verdict.

</details>

## Freshness

Regenerate this benchmark when the skill, evaluation dataset, target agent/model, evaluator version, environment, or scoring policy changes.
