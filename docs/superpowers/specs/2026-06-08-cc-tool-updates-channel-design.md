# CC 工具更新檢查 channel — Design

**日期**：2026-06-08
**狀態**：design（待 implementation plan）
**所屬**：social-info local-analysis workflow 新增 channel

## 目標

在既有 local-analysis daily workflow 加一個 `cc-tool-updates` channel，追蹤「我手動裝/pin 的 CC 生態工具有沒有新版」，純通知讓我決定要不要手動升（不自動升）。動機：cargo `--git --tag` pin（如 sem）、brew、npm-g、uv tool、MCP/plugin 這些工具**都不會自動提醒更新**，pin 死的尤其完全靜默。

涵蓋 4 來源：cargo/手動 pin CLI、brew formula、npm global / uv tool、MCP server / plugin。

## 核心架構：shell channel（shell-first，無更新不派 LLM）

單一 shell channel（像 bumblebee）。確定性工作全 shell、可測；重要性判斷交給 workflow 既有的 digest LLM 階段（不另派每日 agent）：

```
cc-tool-updates channel (shell kind)
└─ cc-tool-updates-daily.sh   枚舉 5 manager + 三名單分類 + 比對 upstream
     有 update → 順手抓 release notes 摘要放進 report
     無 update 且無新發現 → 輸出 sentinel（靜默）
     → 落檔 → workflow digest 階段濃縮（重要性判斷在這輕量發生）
```

**為何 shell-first（從原 llm 改回）**：多數天無更新，每天派 LLM agent 純浪費；版本比對是確定性工作該用 shell（快、準、可測）。重要性判斷不失——helper 有 update 時抓 release notes 摘要當素材，digest LLM 濃縮時自然帶出「該升/可緩」語意，但不需專門每日 agent。

## 三名單分類（發現層核心）

不是「不在 manifest 就 untracked」，而是三態：

- **白名單** = `cc-tool-manifest.json`，要追更新的 CC 工具（含 upstream source）
- **黑名單** = `cc-tool-ignore.txt`，已知不追的系統工具（ripgrep / jq…），只列 name
- **新發現** = 枚舉已裝 − 白 − 黑 → 真正第一次見到的工具

好處：系統工具歸黑一次後**永久靜默**，每天只冒真正新見的工具，噪音趨近零、名單自我成長。

### 非同步分類（背景 workflow 不能即時問）

daily workflow 背景跑、使用者不在場 → 「歸哪個名單」**不即時問**。改成：digest 報告列出「待分類」清單（新發現的工具 + LLM 對「像不像 CC 工具」的初判），使用者事後手動編名單、或叫 CC 幫編。

## 資料結構

### manifest（白名單）`cc-tool-manifest.json`
只記「追什麼」，**不記 current version**（由 helper 動態偵測，升級後不用手動改）：
```json
[
  {"name":"sem","manager":"cargo-git","source":"Ataraxy-Labs/sem"},
  {"name":"semble","manager":"uv-tool","source":"semble"}
]
```
`manager` ∈ {cargo-git, github-release, brew, npm-g, uv-tool, mcp}。`source` = repo / formula / pkg name。

### ignore（黑名單）`cc-tool-ignore.txt`
一行一個 name，純忽略清單。

## helper 邏輯（`cc-tool-updates-daily.sh`，預設輸出 markdown report、`--json` 給測試）

對 manifest 每筆：偵測 installed → 查 upstream latest → 比對：

| manager | installed 來源 | upstream latest 來源 |
|---|---|---|
| cargo-git | `~/.cargo/.crates2.json` 的 tag/commit | `gh api repos/<source>/releases/latest`（404 fallback `/tags`）|
| github-release | binary `--version` | 同上 gh api |
| brew | `brew list --versions` | `brew info --json=v2 <source>` |
| npm-g | `npm ls -g --json` | `npm view <source> version` |
| uv-tool | `uv tool list` | PyPI `https://pypi.org/pypi/<source>/json` |
| mcp | **第一版只枚舉發現、不比對版本** | — |

**MCP 第一版降級**：版本比對最雜（npm/uvx/binary/git 來源各異、`claude mcp list` 不一定給版本），第一版 MCP 只進發現層枚舉，不做版本比對。其餘 4 來源做完整比對。

有 update 時順手抓 **release notes 摘要**（gh api release body 前幾行 / npm·PyPI 的 description）放進 report，當 digest 階段判斷「該升/可緩」的素材。

發現層：枚舉各 manager 已裝（`cargo install --list` / `brew leaves` / `npm ls -g` / `uv tool list` / `claude mcp list`）− 白 − 黑 = 新發現。

輸出兩種：預設 **markdown report**（給 channel 落檔）；`--json`（給測試）：
```json
{
  "updates":[{"name","manager","current","latest","source","notes"}],
  "discovered":[{"name","manager"}],
  "errors":[{"name","reason"}]
}
```
**所有查詢失敗進 errors 不中斷**（graceful；單一工具查不到不該擋掉整個 channel）。

## 報告格式 + 重要性判斷（在 digest 階段）

shell channel 的 markdown report 由 helper 直接產（含 release notes 摘要）；「該升/可緩」語意判斷與「新發現像不像 CC 工具」初判，由 workflow 既有的 **digest LLM 階段**濃縮時帶出，不另派每日 agent。

helper 產的 report：
```
### 有更新
- rtk 0.4→0.5 — release notes: 修了 X（安全相關）
- sem v0.8→v0.9 — release notes: 效能改進
### 待分類（新發現，不在白/黑名單）
- foo（cargo-git）
- bar（brew）
```
digest LLM 拿到後濃縮成「rtk 該升（安全）、sem 可緩（純效能）；新發現 foo/bar 待你歸類」。

## 靜默機制

shell channel：helper `updates` 空且無 discovered → 輸出 sentinel（單行 `__SILENT__` 或空 report）→ workflow digest 階段 filter 掉。**writing-plans 第一步先 spike 驗證**：bumblebee 0-findings 怎麼靜默 / workflow digest 能不能 filter sentinel；不支援就在 digest 段加一個 filter `__SILENT__` 的步驟。

## init（一次性建初始名單）

第一次跑前要建初始白/黑名單，否則第一次跑會把所有已裝工具都當「新發現」轟炸。init 流程：
1. helper 枚舉 5 manager 全部已裝工具
2. CC（我）對每個初判：明顯 CC 工具 → 提議白名單（附 manager+source）、明顯系統工具 → 提議黑名單、不確定 → 待使用者定
3. 使用者 review 三堆、確認/調整
4. 寫成初始 `cc-tool-manifest.json` + `cc-tool-ignore.txt`

白名單初始至少含 `sem` + active trials 裡 pin 的工具（rtk 等）。

## 測試

`cc-tool-updates-daily.test.sh`（測 helper 的 `--json` 確定性部分）：
- 餵 fixture manifest（一個明知有舊版 + 一個最新）→ 斷言 JSON 結構 + updates 偵測正確
- 三名單分類正確（白→追、黑→忽略、其餘→discovered）
- graceful：manager 缺 / 無網路 → 進 errors 不崩、exit 0

## workflow 整合

CHANNELS array 加（shell kind，wrapper 自己落檔到 outfile）：
```js
{ key:'tool-updates', freq:'daily', kind:'shell', src:`${W}/cc-tool-updates-daily.sh`, outfile:`${OUT_DIR}/${DATE}-tool-updates.md` }
```

## 落檔清單

| 檔 | repo |
|---|---|
| `scripts/local-analysis/cc-tool-updates-daily.sh`（shell channel = helper，預設 markdown / `--json` 測試用） | social-info |
| `scripts/local-analysis/cc-tool-updates-daily.test.sh` | social-info |
| `scripts/local-analysis/cc-tool-manifest.json`（白名單） | social-info |
| `scripts/local-analysis/cc-tool-ignore.txt`（黑名單） | social-info |
| `~/.claude/workflows/local-analysis.js`（CHANNELS +1） | ~/.claude |

## 非目標（YAGNI）

- 不自動升級工具（純通知）
- 不做 changelog 全文存檔（只即時讀判斷重要性）
- 不在 channel 內做即時互動分類（背景 workflow 限制 → 非同步）
- 第一版不做 MCP 版本比對（只枚舉發現；MCP 來源異質、複雜度高，待其餘 4 來源穩了再評估）
- skill 更新不重做（已有 `skill-upstream-check-weekly`）
