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

`.github/workflows/daily.yml` 只剩 `workflow_dispatch:`（手動 trigger backup），原本的 cron `schedule:` 已拿掉。Cloud IP 跑 Reddit 5 個 sub 會 403，本地 IP 也常被間歇性 ban，要徹底解 Reddit 還是要走 OAuth API 或 RSSHub fallback——目前還沒做。

## Stage-2 digest 不自動

`reports/{date}.md` 是 raw aggregator 輸出（自動產生）。`reports/digest-{date}.html` 是 Claude 個人化整理（**手動 trigger**，使用者叫我產才做）。

## 已知 fetcher gap

- Reddit 5 個 sub 持續 403（cloud + 本地 IP 都會被間歇性擋）
- Threads `D15iJFBNZ9wgeWAhw` Apify actor 持續 400（payload schema 不合）
- HN 只抓 front_page link 不抓 comments
- X 只抓 KOL handle、不抓 reply / quote tweet / search query

對應 memory `feedback_digest_signal_coverage.md`：digest 缺社群討論層、要 surface 為 fetcher gap 不要默默 ignore。
