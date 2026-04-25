# social-info

Daily AI ecosystem raw aggregator. Pipeline produces a structured Markdown digest each morning at 09:00 Asia/Taipei; downstream consumption is via Claude Code + gh CLI.

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
    type: <one of: hn_algolia, reddit, github_trending, product_hunt, huggingface, rss, rsshub, twitter, threads, wewe_rss>
    enabled: true | false
    tier: 1 | 2
    language: en | zh-TW | zh-CN
    # ... type-specific params
```

See full reference in [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md).

## First-time setup

1. Install uv: https://docs.astral.sh/uv/
2. `uv sync`
3. Copy `.env.example` to `.env`, fill in keys (or set GitHub Secrets for cloud run)
4. Verify: `uv run python -m social_info --smoke`
5. Push to GitHub, set repository Secrets identical to `.env`
6. The daily workflow runs at 09:00 Asia/Taipei automatically

## Local commands

```bash
uv run python -m social_info                    # full pipeline, write today's report
uv run python -m social_info --dry-run          # don't write db / md
uv run python -m social_info --source hn        # subset
uv run python -m social_info --date 2026-04-25  # backfill / specific date
uv run python -m social_info --smoke            # real API, limit=3, print stdout
uv run python -m social_info.health             # 7-day source success rate
uv run pytest                                   # tests
```

## Common problems

- **Public RSSHub returns 410**: try later or self-host (see spec Appendix D)
- **Threads token expired**: refresh with `THREADS_REFRESH_TOKEN`
- **twitterapi.io credit low**: check at https://twitterapi.io dashboard, consider trimming KOLs

## Spec & Plan

- Spec: [docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md](docs/superpowers/specs/2026-04-26-daily-ai-aggregator-design.md)
- Plan: [docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md](docs/superpowers/plans/2026-04-26-daily-ai-aggregator.md)
- Status of in-progress setup: [STATUS.md](STATUS.md)
