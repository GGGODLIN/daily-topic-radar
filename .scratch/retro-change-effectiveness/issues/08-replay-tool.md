# 08 — 回放工具（條件票）

**Condition:** 只在 01 的結論是「可行」時執行。01 不可行時刪除本票，文字規則類改從整場測試開始，09 的文件要照這個結果寫。

**What to build:** 回放工具。

- 重建事件之前的對話：系統提示與規則依事件當時的版本重建，只替換目標規則段落，其餘內容逐字相同。
- 用原 session 的模型、思考深度與路由產生下一步，次數照 01 凍結的值。
- 呼叫模型前先顯示成本估算，由使用者決定要不要跑。
- 保存小型執行紀錄，存在不進版控的報告目錄，不存整段前文。
- 同一套流程也用來回放不相關的時刻，做副作用檢查。
- 輸出交給 07 判斷。

**Blocked by:**
- 01 — 回放可行性驗證（關卡）
- 02 — 事件與「有沒有載入新版」的共用判斷
- 07 — 重犯判斷

**Status:** `done`

**Needs:** None — a worker can check every item unaided（假模型端點與 fixture session 紀錄；不呼叫真實模型）

**Validation method:** 回放工具的 CLI 測試，用假模型端點與 fixture session 紀錄。

**Evidence required:** 測試輸出顯示五件事：
- 新舊兩組的前文只差目標規則段落；跟原請求的差異逐項列出。
- 送出的模型、思考深度與路由與原 session 一致。
- 呼叫模型前有成本估算。
- 執行紀錄欄位齊全。
- 各種失敗情況回傳正確結果，沒有產生判定。

**Tests:** `required`

**Test seam:** 回放工具的指令輸出、送往假端點的請求內容，以及執行紀錄檔。

**Regression scope:** 02 的載入判斷與 07 的判定流程不變。

- [x] 重建事件之前的對話，規則依事件當時的版本重建；新舊兩組之間只差目標規則段落，其餘前文逐字相同；跟原請求的差異逐項列在執行紀錄 — Source: Story 5（2026-11-06 依使用者「照建議」修正：原文為「只替換目標規則段落，其餘內容逐字相同」，01 實測從 session 紀錄自組只有原請求 81–95% 的 token）
- [x] 送出的模型、思考深度與路由與原 session 一致；走中轉站的模型走同一個中轉站，不轉送其他服務 — Source: Story 6 — Failure: F3
- [x] 呼叫模型前顯示 token 與費用估算，等使用者決定；沒有核准就不呼叫 — Source: Story 8
- [x] 每次執行保存執行紀錄：來源指針、規則版本、替換位置、模型設定、每次嘗試與失敗、判斷規則版本、成本估算與實際花費（拿不到標未知）；不存整段前文 — Source: Requirement: 分層驗證（執行紀錄） — Failure: F4
- [x] 缺 session 紀錄或找不到當時的規則版本時，回報「無法回放」與原因，指示改走第三層 — Source: Story 7 — Failure: F2
- [x] 逾時或中轉站回錯時先重試，不算做不到；模型或路由已不可用時，回報改走第四層 — Source: Requirement: 分層驗證（模型不可用） — Failure: F4
- [x] 實驗沒有跑完（中途失敗或被中斷）時不產生判定；要接續時先重新顯示成本估算 — Source: Requirement: 分層驗證（執行紀錄） — Failure: F1
- [x] 能用同一套流程回放不相關的時刻（同模型、改動生效後、改動不適用的事件點），供副作用檢查 — Source: Story 10

## Verification Log

- 2026-11-06 01 判可行，本票啟用。重建方式依使用者「照建議」選 a：沿用 01 原型從 session 紀錄自組請求，不啟動 Claude Code；驗收第 1 條與 Evidence 第 1 項的字句已照此修正。另依 spec「分層驗證」01 結果，每個事件先跑原樣 5 次的前置重現檢查，至少 2 次重現才可用。
- 2026-11-06 完成（commit 6512207，新檔 `retro-replay.py`、`retro-replay.test.py`）：紅燈基線 26 個測試全紅（工具檔不存在）；worker 另做 5 種變異都被抓到。綠燈 retro-replay `Ran 26 tests ... OK`、retro-judge 20 OK、session-retro 58 OK（整合證據角色後 64 OK）。用 3 個真實 session（不呼叫模型）確認重建結果與 01 原型的 model、system、tools、messages 逐欄相同；這只證明與原型一致，不證明與原請求一致。
- 介面：`retro-replay.py run --spec SPEC.json [--approve] [--resume RUNDIR] ...`、`retro-replay.py judge-input --run DIR [--side-effects DIR ...]`。狀態：complete 0、awaiting_approval 0、cannot_replay 10（next_layer 3）、unfaithful 11（前置檢查不足 2 次）、model_unavailable 12（next_layer 4）、incomplete 13。沒有 `--approve` 只輸出估算、不讀金鑰、不建執行目錄。`run.json` 不存 system、tools、messages；`actual.billed_usd` 一律 unknown。complete 時自動寫 07 的輸入 `judge-input.json`。
- 估算（不呼叫模型）：一個事件一輪 20 次呼叫，case 1 牌價 US$17.85／上限 US$35.70（每次輸入估 426,263 token，刻意取「字元數÷4.5」與原 usage 較大者，比 01 實測約 31 萬偏高）、case 2 US$10.39／20.77、case 3 US$11.76／23.51。估算不含沒推進補跑與模型判讀的呼叫。
- worker 自訂判斷：前置檢查用 retro-judge 的 `classify_group`（importlib 載入，不另寫）；兩次呼叫預設間隔 20 秒（01 用 60 秒，沒有依據）；副作用檢查預設每組 5 次（01 沒凍結）；「模型不可用」只認 400／403／404／410 且回應提到 model 與 not found／unavailable／deprecated；只支援 `kind: rule`，skill／command 回 cannot_replay。
- Minor：暫時錯誤重試、模型不可用、SIGINT 中斷都只用假端點驗過。01 報告寫案例一 17 個工具，重建出 16 個，沒追原因。附件列的 `rendered` 欄位（記實際渲染文字與角色）可能是縮小 81–95% token 差距的線索，本票沒採用。
- 2026-11-06 ticket-yagni：kill 1／demote 2／keep 60；審查員註明缺 01 凍結參數。照做：刪 `leaves` 與 `rule_text_outside_instructions` 欄位；`JudgeHandoff` 縮成只檢查 `judge-input.json` 欄位齊全、工具不產生判定也不寫累積庫（commit e7cd761）。不採用：`arm_need`（沒推進補跑），01 凍結規則第 5 點明寫同組補跑 1 次。retro-replay `Ran 26 tests ... OK`、retro-judge 20 OK。
- 2026-11-06 收尾審查（python-reviewer／typescript-reviewer／security-reviewer，pi-subtask 跨模型 gpt-6.1-sol(high)，8 段）：以下判定成立，main 抽查程式碼確認；套用 final-gate-materiality 後沒有一項可判「成立但不重要」（安全項屬 G4，其餘會寫出錯誤判定或資料且事後看不出，不適用 M2；測試缺口經審查員變異證明測試仍綠，不適用 M4）。處置：修。
  - [HIGH] retro-replay.py:745 --resume 不核對實驗身分（Acceptance-miss）
  - [HIGH] retro-replay.py:562 HTTP 200 的 error envelope 算有效樣本（Acceptance-miss）
  - [HIGH] retro-replay.py:835 副作用合併接受不同模型（Acceptance-miss）
  - [MEDIUM] retro-replay.py:645 前置判讀 ModelFailure 未捕捉、紀錄停在 running（Acceptance-miss）
  - [LOW→G4] retro-replay.py:539 跟隨跨來源重新導向外送 relay 金鑰（Acceptance-miss）
  - [LOW→G4] retro-replay.py:555 錯誤本文先截短再遮罩，金鑰片段落盤（安全，無標記）
