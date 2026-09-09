# Recap 模型路由設計驗證

## 結論

設計可行，尚未接入正式每日本機流程。

```text
原生環境（CC_VENDOR 與 ANTHROPIC_BASE_URL 皆空）
→ 清除外來模型映射與 ANTHROPIC_AUTH_TOKEN
→ claude --model opus
→ 真實 trace：claude-opus-5

任何 vendor 或 custom base 環境
→ 只讀 keys 檔的 CLIPROXY_BASE_URL / CLIPROXY_KEY_CC
→ 限定 relay URL 為 http://127.0.0.1:8317 或 http://localhost:8317
→ 清除 ANTHROPIC_API_KEY
→ 主模型與所有子檔位固定 gpt-5.6-luna(max)
→ 真實 trace：gpt-5.6-luna
```

## 實測

### Fake harness

執行：`/Users/linhancheng/code/social-info/.scratch/recap-route-pilot/test-route.zsh`

Fresh 結果：`checks=210 failures=0 vendor_cases=13 base_only_cases=1 native_cases=1 remote_url_cases=1 malicious_keys_cases=1 missing_keys_cases=1 source_rejection_cases=1 isolation_cases=1`。

涵蓋：13 種 CC_VENDOR、只有 ANTHROPIC_BASE_URL 的 custom endpoint、原生環境、缺 keys、remote relay URL、keys 內惡意 shell 命令、禁止 source，以及 child capture 的 model/route/base/credential presence。測試不使用真 secret，只比較 fixture token 是否相符。

### 最小真實探針

兩個探針只要求固定字串，不讀 recap 或本機資料，不使用工具。

- vendor 模擬：父環境設為 `CC_VENDOR=glm` 與 z.ai base；runner 切到 local relay，stdout=`ROUTE_LUNA_V2_OK`。session `/Users/linhancheng/.claude/projects/-Users-linhancheng-Desktop-projects-gggodlin-blog/6a62220c-998c-47fc-a2dc-39fe88006872.jsonl` 記錄 model=`gpt-5.6-luna`。
- native 模擬：移除 vendor/base、刻意保留 fixture ANTHROPIC_AUTH_TOKEN；runner 清除 token，stdout=`ROUTE_NATIVE_V2_OK`、stderr 空。session `/Users/linhancheng/.claude/projects/-Users-linhancheng-Desktop-projects-gggodlin-blog/587bfd65-18bc-4162-8f81-8d7a015fe43a.jsonl` 記錄 model=`claude-opus-5`。

Luna probe stderr 有 `claude-code:unrecognized_model` 與 connectors disabled 警告；請求仍完成且 trace 記錄 Luna。現行 recap 只讀本機檔案，不依賴 claude.ai connectors；若日後加入 connector source，需重新驗證。

## 安全與 code review

- code-reviewer：最新版 V1/V2 通過，CRITICAL/HIGH/MEDIUM/LOW finding 均為 0，Verdict APPROVE。
- security-reviewer 初查三項：native 殘留 auth token、remote relay URL、source 任意 keys shell。三項已修；重驗確認 runtime 問題關閉，無高信心 credential 外洩、auth confusion、endpoint 誤路由或 fail-open finding。
- security-reviewer 仍指出 parent isolation assertion 本身無法證明 child process 不改 parent。這是測試論證限制，不是 runtime finding：正式設計必須以 executable 呼叫 runner，runner 也會拒絕被 source。不要把 subshell assertion 說成 OS process isolation 的實驗證明。

## 未做

- 沒有執行 recap prompt。
- 沒有讀過去一天 session、commit 或 memory。
- 沒有修改 `local-analysis.js`、`recap-daily.sh`、daily-local command 或 hook。
- 沒有驗證完整 recap 的時間、內容或 ledger 寫入；本報告只證模型與端點路由。

## 正式接入的最小形狀

正式接入時，保留這個 runner 的 route contract，讓 daily-local 把 recap 從一般 workflow agent 拆到獨立可執行子程序。原生環境走 native Opus；其他環境固定 local relay Luna。relay 或 keys 不可用時 fail closed，不退回 free(max)。其餘 channel 不改模型路由。
