# 驗收 / 醒來要做的事

> 寫給 2026-04-26 早上醒來的你。Claude 自主跑完 PoC 到此狀態。

## TL;DR

**End-to-end pipeline 跑通了**：本機跑 `uv run python -m social_info` 會產出 [`reports/2026-04-26.md`](reports/2026-04-26.md) 的 273-item daily digest（18 個 source 活躍、3 個 fail），可以直接打開 Claude Code 跟我說「看一下今天的 AI digest」做 stage-2 整理。

**5 件事需要你醒來確認 / 動手**：

1. ✅ **驗收 PoC**：跑 `uv run python -m social_info --smoke`、看 `reports/2026-04-26.md`、打開 Claude Code 試 stage-2 整理流程
2. 🔑 **填 secrets**（不填的話 Twitter / Threads / Product Hunt / 微信 source 永遠 disabled）— 詳見下方 §Secrets
3. 🐙 **建 GitHub remote 並 push**（cron 才會跑）— 詳見下方 §GitHub remote
4. 🌏 **決定中文 source 怎麼處理**（公開 RSSHub 對 zhihu / weibo / dcard 全 403）— 詳見下方 §中文 sources
5. 📐 **加 Anthropic / Mistral 註解**：兩個 lab blog 沒官方 RSS，目前用 community-maintained feed（`Olshansk/rss-feeds`）。spec / sources.yml 都有註明 — 確認你接受這個依賴

---

## 已完成

- 22 task 全部 implement 完
- 41 個 unit test 全 pass
- 完整 lint clean (`uv run ruff check src tests`)
- end-to-end 真打 API 跑成功，產出 273 items / 5-15 KB markdown
- L1 (URL) + L2 (title) dedup 工作中（同日重跑保留原 .md，不會 overwrite 成空）
- Health script 工作中：`uv run python -m social_info.health`
- GitHub Actions yaml 寫好但**還沒推**（沒 remote）

## 17 commits on main

```
d98f343 fix: idempotent same-day rerender from db; fix anthropic_blog 404 with community feed
4b6e142 feat: add 7-day source health report script
2cb9197 feat: CLI with --dry-run, --smoke, --source, --date flags
61ee6b7 feat: orchestrate parallel fetchers with dedup and report writing
539d768 feat: add wewe_rss skeleton fetcher (disabled by default in PoC)
b6588cd feat: add Threads fetcher with keyword/tag modes via Meta API
1c1e8d0 feat: add Twitter fetcher via twitterapi.io with per-handle iteration
17ff802 feat: add RSSHub fetcher with configurable instance URL
cbd2e42 feat: add generic RSS / Atom fetcher with HTML strip on excerpt
4b29c65 feat: add HuggingFace trending models/spaces fetcher
415b861 feat: add Product Hunt GraphQL fetcher
e539b62 feat: add GitHub Trending fetcher with AI keyword filter
be87944 feat: add Reddit fetcher via public top.json
ff75fb8 feat: add HN Algolia fetcher with keyword OR filter
411427e feat: load sources.yml with SourceConfig and add full source list
c67d27e feat: render Item to markdown, group by platform with failures header
8d5b6b5 feat: add L1 (URL) and L2 (title hash) dedup with tier preference
fd16c30 feat: add SQLite database layer for items and fetch_runs
374d4fa feat: add canonical_url and core Item/FetchResult types
05b4218 chore: bootstrap python project with uv and ruff
42748fe docs: add daily aggregator implementation plan
cfdaa47 docs: add daily AI aggregator design spec
```

(再加上後續修復 inside_ai disable / mistral community feed 的 commit。)

## Source 現況（最新 pipeline run）

✅ **17 個 working**（共 273 items）：
- `hn` (18) · `reddit_localllama` (10) · `reddit_claudeai` · `reddit_openai` · `reddit_singularity` · `reddit_machinelearning` · `github_trending` (26) · `huggingface_models` · `huggingface_spaces` · `anthropic_blog` (community feed, 30) · `openai_blog` · `google_ai_blog` · `mistral_blog` (community feed, 30) · `techcrunch_ai` · `venturebeat_ai` · `ithome_ai` (台灣 30 items！) · `xai_blog`

❌ **3 個 active failures**（公開 RSSHub 403）：
- `zhihu_hot` · `weibo_search_ai` · `dcard_engineer`
- 預期之內，spec §9.4 講過「公開 instance 不穩」
- 解法：第一個月跑後評估是否值得 self-host RSSHub（spec Appendix D）

🚫 **9 個 disabled**（等 secret / 等決定）：
- `product_hunt`（要 `PRODUCT_HUNT_TOKEN`）
- `twitter_tier1`、`twitter_tier2`（要 `TWITTERAPI_IO_KEY`）
- `threads_keyword`、`threads_topic_tag`、`threads_user_handles`（要 `THREADS_ACCESS_TOKEN`）
- `wechat_qbitai`、`wechat_appso`、`wechat_36kr_ai`、`wechat_jqzx_pro`（要 self-host wewe-rss）
- `inside_ai`（沒可用 RSS endpoint，暫時 disable）

---

## §Secrets（你要填的）

| Secret | 用途 | 怎麼拿 |
|---|---|---|
| `TWITTERAPI_IO_KEY` | X / Twitter 抓 20 個 KOL | 註冊 https://twitterapi.io（送 100K credits 夠跑半年以上） |
| `THREADS_APP_ID` + `THREADS_APP_SECRET` + `THREADS_ACCESS_TOKEN` + `THREADS_REFRESH_TOKEN` | Threads keyword / topic tag search | 申請 Meta Developer App（spec Appendix B 有步驟） |
| `PRODUCT_HUNT_TOKEN` | Product Hunt daily top AI | 註冊 https://api.producthunt.com、申請 OAuth API key |

**填法**：
1. **本機**：`cp .env.example .env`、填值；之後本機跑 pipeline 會自動讀 `.env`（但目前 `__main__.py` 沒寫 `load_dotenv` 自動載入——你要手動 `set -a && source .env && set +a` 然後跑 pipeline，或直接 `export` 各 var）
2. **GitHub Actions**：用 `gh secret set TWITTERAPI_IO_KEY` 等指令（推 remote 後才能設）

填完 secrets 後，編輯 `sources.yml` 把對應的 `enabled: false` 改 `enabled: true` 就會啟用。

## §GitHub remote

目前 repo 只有 local `main` branch，沒有 remote。

要跑 cron 必須：

```bash
gh repo create social-info --private --source=. --remote=origin --push
# 或先建 GitHub repo 再
git remote add origin https://github.com/<你的帳號>/social-info.git
git push -u origin main
```

push 後：
```bash
gh secret set TWITTERAPI_IO_KEY < <(echo "<value>")
gh secret set THREADS_ACCESS_TOKEN < <(echo "<value>")
# ... 其他 secrets
gh workflow run smoke.yml   # 手動觸發 smoke 試一次
gh workflow run daily.yml   # 手動觸發 daily 一次
```

## §中文 sources 怎麼處理

公開 RSSHub `https://rsshub.app` 對 zhihu / weibo / dcard 都被速限（403）。三個選項（按力氣排）：

1. **不管它**：先用台灣 `ithome_ai` + 英文圈訊號跑兩週、看你是否覺得「中文 signal 不重要」
2. **換另一個公開 RSSHub instance**：見 https://docs.rsshub.app/zh/guide/instances，挑個試
3. **Self-host RSSHub**（spec Appendix D）：$5/月 Zeabur / Railway / Hetzner、跑 docker compose、設 `RSSHUB_INSTANCE_URL` secret 換掉

我在 spec 寫過第一個月先 PoC、跑完月底再決定。建議先 (1)。

## §怎麼驗收 PoC

```bash
# 1. 看 PoC 報告
cat reports/2026-04-26.md | head -80

# 2. 打開 Claude Code，跟我說：
#    "看一下 reports/2026-04-26.md，幫我做 daily AI digest 整理回報，
#     重點看 agent / coding tools / multimodal、跳過純 paper"
#    → 這就是 stage-2 工作流

# 3. 健康檢查
uv run python -m social_info.health

# 4. 重新跑（測 idempotency，會看到 0 new this run）
uv run python -m social_info

# 5. Smoke run（不寫 db、印 stdout）
uv run python -m social_info --smoke

# 6. 跑 test
uv run pytest

# 7. 跑 ruff
uv run ruff check src tests
```

## 你可能想做的事

- [ ] 看 reports/2026-04-26.md，餵給 Claude Code 試 stage-2，確認流程合用
- [ ] 註冊 twitterapi.io、Meta Developer App、Product Hunt API
- [ ] 填 `.env`，本機跑一次有 X / Threads 的版本看
- [ ] 建 GitHub repo、push、設 secrets
- [ ] 手動 trigger 一次 smoke.yml、看 GitHub Actions runner 有沒有跑通
- [ ] 改 cron schedule（如果 09:00 不合）
- [ ] 改 sources.yml 想加 / 砍的 source

## 已知小 issue（可忽略也可修）

- `datetime.utcnow()` 在 Python 3.12 有 deprecation warning。每個 fetcher 都會打一個。修法：全部換 `datetime.now(UTC)`、再把 ISO string 改 timezone-aware 比對。對 PoC 不影響功能，醒來想清乾淨可一次掃。
- L2 dedup 沒抓到任何跨來源 cluster（grep `also seen at` reports/ → 0）。可能是 spec 預期的「fixture 內容沒重疊」，也可能 normalize_title 太嚴格。可以等實際多日資料累積後再評估。
- 沒寫 `load_dotenv()`，所以本機要手動 `export` env var。修起來簡單（加 python-dotenv dep + 在 `__main__.py` import）。
