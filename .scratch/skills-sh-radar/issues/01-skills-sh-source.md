# 01 — skills.sh 榜單端到端接入

**What to build:** 讓 daily radar 以單一 skills.sh source 固定抓取 Trending 與 Hot，各取相同數量的榜上 skill，沿用既有 Item、去重、resurface 與 raw report 管線，並在單一「Agent Skills (skills.sh)」獨立區塊顯示榜別、rank、installs 與發布來源。

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

**Needs:** None — a worker can check every item unaided

**TDD:** required

**TDD seam:** 既有 fetcher contract `fetch(SourceConfig, AsyncClient) → list[Item]` 與 `render_file` 的 raw report 輸出

- [x] 固定抓取 Trending 與 Hot，兩榜共用單一 `limit: 25`，不提供榜單清單或 per-board limit。
- [x] HTML fixture 驗證榜單列能轉成包含 skill URL、canonical URL、發布來源、榜別、rank 與 installs 的 Item。
- [x] parser 尊重 limit；結構失配時回傳空清單，讓既有 empty-source 診斷接手。
- [x] 一個榜單請求失敗時仍保留另一榜成功項目，並沿用既有錯誤診斷慣例。
- [x] 同一 skill 同時出現在兩榜時沿用既有 canonical URL dedup 與 `also seen at` 行為，不新增歷史表或專用資料庫。
- [x] raw report 只新增一個「Agent Skills (skills.sh)」H2，保留 Trending 在前、Hot 在後的榜單順序。
- [x] renderer 明確顯示榜別、rank 與 installs，不將它們誤標為 likes、comments 或通用 score。
- [x] 新 source 接入既有 registry 與設定，README 的 source type 清單同步更新。
- [x] 新增的 fetcher 與 markdown 局部測試全部通過。

## Run metadata

- feature_base_sha: `a18a142f2483686085b83bc7cac7c32ece081653`
