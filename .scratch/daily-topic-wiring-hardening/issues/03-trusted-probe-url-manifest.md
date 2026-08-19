# 03 — Trusted probe URL manifest 接入 verify gate

**What to build:** workflow 從結構化 `new_signals[].source` 取出 exact HTTP(S) probe URL，將 trusted URL 清單與 shell-safe `verify_cmd` 放進 workflow result；後置 verifier 從第 4 個參數起接受 exact trusted URLs，並保留既有前三個參數與所有既有來源規則。

**Blocked by:** 02 — Deterministic PROBES writeback

**Status:** completed

**Needs:** None — a worker can check every item unaided

**TDD:** `required`

**TDD seam:** `verify-daily-digest.sh` 的 CLI exit code 與 workflow result 的 trusted URL／verify command contract

- [x] digest URL 不在 raw md，但以 exact value 傳入 trusted arguments 時 verifier exit 0。
- [x] 同一 digest URL 未傳入 trusted arguments，且不在其他既有允許來源時，verifier exit 1。
- [x] 同 domain 的不同 URL 仍 exit 1，不形成 host-wide whitelist。
- [x] 原有兩參數與三參數 caller 維持相容；第 3 個參數仍是 external-feeds directory，第 4 個起才是 trusted probe URLs。
- [x] workflow 只從結構化 `new_signals[].source` 解析 trusted URL，不信任 digest HTML、writer prose 或任意同網域 URL。
- [x] 四個 workflow 都回傳 exact trusted URL 清單與可直接執行的 `verify_cmd`。
- [x] trigger-injected verify instruction 與 fallback daily-topic command 優先使用 workflow 回傳的 `verify_cmd`，沒有 manifest 時保留舊 command shape。
- [x] 既有 raw-md、external-feed、utility whitelist、mechanical HARD fixtures 維持通過／攔截行為。
- [x] source 中包裹 URL 的 Markdown／HTML／全形標點不會污染 trusted manifest；query string 的 HTML entity 仍能通過 exact URL gate。
- [x] source URL parser 維持 exact URL equality，不擴成 host-wide whitelist。

## Verification Log

- 2026-08-19 — **Final review blocker:** 兩個 reviewer 都用本次真實 structured probe source 重現 URL extractor 將全形括號後的 prose 黏進 URL：manifest 得到 `https://github.com/anthropics/claude-code/releases/tag/v2.1.235（由`，digest href 則是乾淨 URL，exact equality 為 false。這代表 Ticket 03 原始合法 probe URL 誤殺仍會發生。另 HTML entity/query-string canonicalization 尚未覆蓋。依 GPT 實作收斂規則，未經使用者拍板不擴大本輪修正；commit 被此項阻塞。
- 2026-08-19 — **Final resolution:** 四版 production projection 現在都保留合法 balanced parentheses、尾端 `!` 與 query punctuation，剝除 Markdown／HTML wrapper、全形 prose punctuation，並 bounded canonicalize named、decimal 與 hexadecimal ampersand entities。原 verifier 以四版最終 manifest 重跑 F1 得到 `ok=true`，F2 也確認 named／numeric／nested entity exit 0；same-host different URL 仍 exit 1，沒有擴大 whitelist。
