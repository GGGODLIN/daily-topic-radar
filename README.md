# social-info

Daily AI ecosystem raw aggregator. Pipeline produces a structured Markdown digest each morning at 06:00 Asia/Taipei via local launchd ([scripts/run-daily.sh](/scripts/run-daily.sh)); downstream consumption is via Claude Code + gh CLI.

## How to consume the daily digest

After 10:00 Asia/Taipei, open Claude Code and ask Claude to read the latest report:

```
看一下今天的 AI digest（reports/$(date +%Y-%m-%d).md）
```

Claude will use `gh api` or local `Read` to load the file and do personalized filtering, ranking, summarization, and reporting based on your current focus.

## How to add or remove sources

Edit `sources.yml`. Schema:

```yaml
sources:
  - id: <unique-id>
    type: <one of: hn_algolia, reddit, github_trending, skills_sh, product_hunt, huggingface, rss, rsshub, twitter, threads, wewe_rss>
    enabled: true | false
    tier: 1 | 2
    language: en | zh-TW | zh-CN
    # ... type-specific params
```

See full reference in [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md).

## First-time setup

1. Install uv: https://docs.astral.sh/uv/
2. `uv sync`
3. Copy [`.env.example`](/.env.example) to `.env`, fill in keys (`APIFY_RELAY_URL`, or direct `APIFY_TOKEN_TWITTER` / `APIFY_TOKEN_THREADS`)
4. Verify: `uv run python -m social_info --smoke`
5. Push to GitHub ([scripts/run-daily.sh](/scripts/run-daily.sh) commits and pushes daily `state.db` and `reports/`)
6. Load the local LaunchAgent (`com.gggodlin.social-info-daily`) so [scripts/run-daily.sh](/scripts/run-daily.sh) runs daily at 06:00 Asia/Taipei automatically

## Local commands

```bash
uv run python -m social_info                    # full pipeline, write today's report
uv run python -m social_info --dry-run          # don't write db / md
uv run python -m social_info --source hn        # subset
uv run python -m social_info --date 2026-04-25  # backfill / specific date
uv run python -m social_info --retry-failures   # re-run only sources whose last run failed
uv run python -m social_info --smoke            # real API, limit=3, print stdout
uv run python -m social_info.health             # 7-day source success rate
uv run pytest                                   # tests
```

## Common problems

- **Public RSSHub returns 410**: try later or self-host (see spec Appendix D)
- **Threads fetch failing / quota low**: active Threads fetching uses Apify (`APIFY_RELAY_URL` or `APIFY_TOKEN_THREADS`; legacy Meta API `THREADS_ACCESS_TOKEN` is dormant unless `user_handles` is re-enabled, and `threads_muse` is currently disabled — see [CLAUDE.md](/CLAUDE.md))
- **X / Twitter fetch failing / credit low**: X fetching uses Apify (`APIFY_RELAY_URL` or `APIFY_TOKEN_TWITTER`), not `twitterapi.io`; check the resident Apify relay or Apify account usage and consider trimming KOLs

## Spec & Plan

- Spec: [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md)
- Plan: [docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md](docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md)
- Historical PoC snapshot (2026-04-26): [STATUS.md](/STATUS.md)
