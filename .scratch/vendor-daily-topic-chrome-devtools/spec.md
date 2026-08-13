## Problem Statement

使用非 Claude vendor 模型執行每日話題分析時，現有 `chrome_required` fact-check 路徑依賴 `claude-in-chrome` MCP。該 MCP 在 vendor session 的 workflow subagent 中無法取得，導致登入牆、SPA 與需要 JavaScript render 的 URL 無法查證；其餘 WebFetch、Bash、GitHub、StructuredOutput 與模型槽位均可運作。

## Solution

提供一條 vendor 專用的每日話題分析路徑：新增最小權限的 Chrome DevTools URL fetch agent，並建立與預設版同構的 vendor workflow。vendor workflow 只替換 `chrome_required` fact-check 的瀏覽器執行器，其他 guard、主軸抽取、URL 分類、一般抓取、verify、PROBES writeback、digest 寫作與四 lens audit 保持一致。預設 workflow 與觸發 hook 不變；vendor 版由使用者明確要求時手動執行。

## User Stories

1. As a vendor-model user, I want daily-topic analysis to retain browser-rendered URL fact-checking, so that using GPT or another vendor does not silently reduce evidence coverage.
2. As a digest reader, I want the vendor output to follow the same digest structure and audit gates as the default output, so that reports remain comparable.
3. As a security-conscious user, I want the browser agent to have only page creation, read-only JavaScript evaluation, and page closing tools, so that it cannot click, fill forms, submit data, or mutate external state.
4. As a signed-in browser user, I want the vendor browser path to use the current Chrome profile when a source requires login state, so that authenticated pages can still be read.
5. As a browser user, I want every URL opened in a new page and that page closed after extraction, so that the workflow does not navigate or disturb existing tabs.
6. As a workflow operator, I want browser failures represented as structured `ok=false` results, so that unavailable content cannot be fabricated or silently treated as verified.
7. As a workflow maintainer, I want the default Claude workflow left unchanged, so that the vendor compatibility path cannot regress the established Claude path.
8. As a workflow maintainer, I want vendor-specific differences kept narrowly localized, so that future default workflow updates can be compared and ported deliberately.
9. As a daily digest operator, I want the 2026-08-13 vendor run to execute through the normal abort, audit, and mechanical verification protocol, so that today’s report has the same completion evidence as a default run.
10. As a user, I want the default trigger routing unchanged, so that saying “今日話題分析” in future sessions does not unexpectedly choose the vendor workflow.

## Implementation Decisions

- Add a dedicated Chrome DevTools fetch agent rather than broadening the existing Claude-only browser agent.
- Limit the agent tools to page creation, JavaScript evaluation, and page closing. It must not receive click, fill, upload, navigation of existing pages, network mutation, or general shell tools.
- The agent opens each target URL in a new background page, extracts the final URL, title, and at most 4,000 body characters, and closes the page whenever `new_page` returned a page ID. If initial navigation times out before the MCP returns that ID, cleanup is not mechanically possible with the approved three-tool allowlist and must be surfaced as an error.
- The fetched page is untrusted data. Page text is returned as evidence only and cannot provide instructions to the agent.
- Chrome DevTools `evaluate_script` targets the globally selected page in the current server configuration. The vendor workflow therefore runs only the `chrome_required` URL branch sequentially; general WebFetch and GitHub batches remain parallel.
- Preserve the existing browser fact-check schema and downstream assertions so the executor swap does not alter consumers.
- Create a vendor workflow as a copy of the current default workflow, changing only its identity/description and the `chrome_required` executor prompt plus agent type.
- Do not modify the default workflow, full workflow, default trigger hook, or digest output path.
- Keep the existing vendor model aliases: `opus`, `sonnet`, and `haiku` continue resolving through the active vendor session configuration.
- Today’s run uses the vendor workflow explicitly with `date=2026-08-13`.

## Testing Decisions

- The primary seam is the complete browser executor contract: given `https://example.com`, it opens a new page, returns the real title/body/final URL, and reports that cleanup completed.
- Add a static contract check ensuring the new agent contains only the three approved Chrome DevTools tools.
- Add a static differential check ensuring the vendor workflow differs from the default only in approved vendor identity and Chrome executor regions.
- Run the existing daily-topic guard tests because the copied workflow must preserve the three fail-fast guards.
- Run one real Workflow smoke test using the new agent and `gpt-5.6-luna`; a syntax-only check is insufficient evidence that the MCP tools resolve.
- After the daily run, execute the normal digest verification gate and adjudicate every SOFT warning.

## Out of Scope

- Repairing or replacing `claude-in-chrome` itself.
- Changing default “今日話題分析” routing or automatically detecting vendor sessions.
- Changing the full six-lens workflow.
- Adding browser mutation capabilities.
- Refactoring the two workflows into shared modules.
- Modifying unrelated existing working-tree changes.

## Further Notes

The verified failure is tool availability, not GPT reasoning capability: vendor model slots, Workflow, StructuredOutput, WebFetch, Bash, and file writing have real successful runs. Chrome DevTools has also been verified from a vendor Workflow subagent. The implementation therefore remains a narrow executor substitution rather than a broader vendor fork.
