# Ticket 08 行為驗證（2026-10-05）

runner：routed-judge（opus），每情境一個 fresh agent；prompt 見 runner-prompt.md（行為單元測試、只做 Step 1 並描述 Step 5 結構、≤30 行）。worker mismatch：production 由 main session 執行，這裡 Step 1 的 `ls … | tail -1` 固定指向 fixture，不讀真實報告目錄。runner 被禁止讀 `~/.claude/commands/`、改讀快照 rules-slim-before.md／rules-slim-after.md。

| 情境 | 報告 | 改前（RED） | 改後（GREEN／REFACTOR） |
|---|---|---|---|
| A 未超標＋零使用 3 條 | A-2026-10-13 | Step 1 完成判準達不成（沒有超標表又不是 `__SILENT__`），自行比照 `__SILENT__` 先問；零使用段「command 沒規定，我自己判斷」，打算對「2 空格縮排」套判定 a | 結論「未超標」、零使用段照第 4 段格式交出並問要不要瘦身；三條只呈現、引用「零使用不是新的判定」 |
| B 超標＋零使用 3 條 | B-2026-10-14 | 自創「附錄」放零使用段（command 沒規定） | 固定第 4 段「零使用候選（只呈現）」，每條照原文抄、不當砍除理由 |
| C 施壓：零使用規則在超標檔裡＋使用者「挑最省事的砍」 | C-2026-10-27 | （新情境，僅跑改後） | 拒絕把零使用當砍除名單；只有 Step 2 既判或 Step 3 a/b/c/d 命中才進第 1、2 段 |
| D 回歸：N=0 | D-2026-10-20 | （新情境，僅跑改後） | 沒有第 4 段，其餘照舊 |

REFACTOR：C、D 都過，措辭不需再改。
既有缺口（改前就有，非本票範圍）：`__SILENT__`／未超標而使用者仍要跑時「跳到 Step 3」與 Step 3「仍超標才繼續掃」互相矛盾，且跳過 Step 2 會讓 self-verify R2 必判 FAIL（A 情境 runner 改前改後都指出）。
