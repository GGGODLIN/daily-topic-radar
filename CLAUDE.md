# social-info / daily-topic-radar

## 路徑

實體在 `~/code/social-info`。`~/Desktop/projects/social-info` 是 **symlink** 指向同一目錄。

**為什麼**：macOS TCC 限制 LaunchAgent 訪問 `~/Desktop` / `~/Documents` / `~/Downloads`，且 FDA 不繼承到不同 codesign identity 的 child binary（homebrew Python）。為了 launchd 跑 daily fetch、又保留 `~/Desktop/projects/<name>` 的工作習慣，做了 symlink 雙路徑。

`~/.claude/projects/` 也做了對應 symlink (`-Users-linhancheng-code-social-info` → `-Users-linhancheng-Desktop-projects-social-info`)，不論從哪條 cwd 進都 hit 同一份 memory + session。

**硬編路徑用 physical path** (`/Users/linhancheng/code/social-info`)，不要用 Desktop 那條（launchd 啟 process 會 resolve 但 TCC 仍會擋）。

## 自動排程

- **Label**: `com.gggodlin.social-info-daily`
- **Plist**: `~/Library/LaunchAgents/com.gggodlin.social-info-daily.plist`
- **Schedule**: 每天 06:00 Asia/Taipei（launchd `StartCalendarInterval`）
- **Wrapper**: `scripts/run-daily.sh` — `uv run python -m social_info` + `git add state.db reports/` + `commit` + `push`
- **Log**: `logs/cron-{date}.log`（gitignored）

電腦睡眠時 launchd 會在喚醒時補跑一次；整夜關機那天就 miss、不追補。

### 常用指令

```bash
# 手動觸發
launchctl kickstart -p gui/$(id -u)/com.gggodlin.social-info-daily

# 看狀態
launchctl print gui/$(id -u)/com.gggodlin.social-info-daily | grep -E "state|active count|last exit"

# 暫停 / 重新載入
launchctl bootout gui/$(id -u)/com.gggodlin.social-info-daily
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.gggodlin.social-info-daily.plist

# debug 直接跑 wrapper
bash scripts/run-daily.sh
tail -f logs/cron-$(date +%Y-%m-%d).log
```

## TCC / FDA setup（一次性）

`/bin/bash` 已加 Full Disk Access（System Settings → Privacy & Security → Full Disk Access）。

**注意**：未來 `brew upgrade python` 換版本後，新 Python binary（`/opt/homebrew/opt/python@<version>/bin/python<version>`）也要加進 FDA，不然 launchd job 會死在 `python: realpath: .venv/bin/: Operation not permitted`。.venv 重建後 `.venv/bin/python` symlink 會指到新版本，舊 FDA 不夠。

## GitHub Actions

`.github/workflows/daily.yml` 只剩 `workflow_dispatch:`（手動 trigger backup），原本的 cron `schedule:` 已拿掉。Cloud IP 跑 Reddit 5 個 sub 會 403，**本地住宅 IP（無 VPN）跑 OK**——但消費級 VPN exit IP 早被 Reddit ban、所以 launchd 跑時 VPN 反而會害 Reddit 5 個 sub 全 403。

**操作注意**：06:00 launchd 觸發前 VPN 應該關著，或者設 split-tunnel 把 reddit.com 排除走真實出口。

## Stage-2 digest 不自動

`reports/{date}.md` 是 raw aggregator 輸出（自動產生）。`reports/digest-{date}.html` 是 Claude 個人化整理（**手動 trigger**，使用者叫我產才做）。

## Digest 前 KNOWN_ISSUES.md 攔截 protocol

`KNOWN_ISSUES.md` 是 pipeline 跑完自動寫的（`src/social_info/known_issues.py`），repo 根目錄。分四區：

- 🚨 **User action required**：401/403、VPN-blocked、API key 失效等需要使用者介入才能補的
- 🛠 **Persistent error**：4xx 持續錯（fetcher 需要更新 schema / actor / parser）
- 🪦 **Stable failures**：≥7 連續失敗、視為 dead source（候選 disable）
- ⏳ **Transient**：retry 用完仍失敗、下次 run 自動再試（通常不需動）

**Protocol（使用者叫我產 digest 時）**：

1. 我先 `cat KNOWN_ISSUES.md`，把 🚨 + 🛠 + 🪦 三區條目列給使用者看
2. **如果有 🚨 條目**：問使用者「要先處理還是直接產 digest 接受 gap」，等回答
   - 處理（例：關 VPN）→ 跑 `uv run python -m social_info --retry-failures` 補資料 → 產 digest
   - 接受 gap → 直接產 digest，但要在 digest 開頭明寫缺哪些社群層 / fetcher gap
3. **如果只有 🛠 / 🪦**：告知使用者哪些 fetcher 需要修、但不阻塞 digest（這層 retry 也救不回，需要 code 改）
4. **如果什麼都沒有**：直接產 digest

不要在沒檢查 KNOWN_ISSUES.md 的情況下直接產 digest — 那等於假設今天資料完整、可能會像 5/8 v1 那樣事後才發現社群層全死。

## Stage-2 digest URL 抓取

digest 階段要展開原文（解讀 / 摘要 / 引用）時，**按來源分流**抓——觀察期累積兩天 11 條網址後，已鎖定路由，**不再並行對比**。一般研究 / 對話用 WebFetch（見 [`~/.claude/skills/research-before-answer/SKILL.md`](file:///Users/linhancheng/.claude/skills/research-before-answer/SKILL.md)）。

**不觸發**：搜尋結果列表（用 WebSearch）／ GitHub 元資料（用 `gh`）／ SDK 文件（用 Context7）。

### 路由（經驗法則）

| 來源 | 主要工具 | 備援 | 備註 |
|---|---|---|---|
| `reddit.com` 全域 | `mcp__pullmd__read_url` | 沒有有效備援 | body + 留言 + 票數 inline 在 `content[0].text`；WebFetch 對 reddit 整域寫死拒絕、`mcp__fetch__fetch` 也 403 |
| X / Twitter | claude-in-chrome MCP（本機已登入 Chrome） | — | WebFetch 回 402、匿名出站普遍被擋；見記憶 `reference_x_tweet_fetch_fallback.md` |
| ithome.com.tw / thehackernews.com / 其他 Cloudflare 系列 | `mcp__fetch__fetch` | — | WebFetch 一律 403；2026-04-30 觀察期實測 5/5 全勝 |
| HN / 一般 RSS / 部落格 / 新聞 | WebFetch（簡單站直接通） | 403/402 → `mcp__fetch__fetch` | 結構簡單站不需要 PullMD |

### 為什麼 PullMD 只在 reddit 留下

兩天 11 條網址裡，PullMD 真正吐出 body 的只有 1 條（reddit）。對其他來源：
- 上游 MCP wrapper bug：body 寫進 `structuredContent` 但 Claude Code 只看得到 `content[0].text` 的 4 欄 metadata
- sqlite 撈法（`~/Desktop/projects/pullmd/data/cache.db`）依賴 WAL checkpoint 時機、container 內無 `sqlite3` CLI 無法手動觸發；2026-04-30 觀察期 6 條 share_id 全 `sqlite_miss`
- 等 [`aeternalabshq/pullmd` PR #3](https://github.com/aeternalabshq/pullmd) merge 把 body inline 後可重評，但對非 reddit 來源**預期沒有不可取代的加分**——`mcp__fetch__fetch` 已經夠用

reddit 場景下 PullMD 仍是唯一選項：reddit 對匿名出站留了一條口子讓它走，其他工具都被擋。

### 失敗處理

- WebFetch 403 + `mcp__fetch__fetch` 也失敗 → 報告使用者，不硬生內容
- PullMD docker 沒起 / MCP 無回應 → reddit 該條沒救，照實標 `pullmd ok=false reason=mcp_down`
- 第一次踩到陌生來源類型時：跑一次 `WebFetch` + 對應路由工具的對比，結果 append log 到 `~/.claude/fetch-experiment-log.md`，格式：

  ```
  [ISO-ts] url=<URL> source=<reddit|hn|x|blog|news|other>
    webfetch ok=<bool> reason=<...>
    <route_tool> ok=<bool> reason=<...>
    verdict: <which_better|tie|all_failed>
    note: <一句觀察>
  ```

### Docker / MCP 前置（reddit 路由用得到）

- `~/Desktop/projects/pullmd/` docker stack 要 up（`docker compose ps` 確認 3 個 container running）
- `~/.claude.json` user-scope `mcpServers.pullmd` 註冊在 `http://localhost:3000/mcp`
- VPN 開著時 PullMD 出站走 VPN 出口 IP，reddit 會把 IP 擋掉 → 跑 digest 前關 VPN 或排除 reddit.com

## 已知 fetcher gap

- Reddit 5 個 sub：cloud IP 必擋；本地住宅 IP OK；VPN 開著時擋（消費級 VPN exit IP 在 ban list）
- Threads `D15iJFBNZ9wgeWAhw` Apify actor 持續 400（payload schema 不合）
- HN 抓 front_page link + 每則 story 的 top 5 comments（Firebase API，2026-05-11 之後）；不抓巢狀回覆
- X 只抓 KOL handle、不抓 reply / quote tweet / search query

對應 memory `feedback_digest_signal_coverage.md`：digest 缺社群討論層、要 surface 為 fetcher gap 不要默默 ignore。
