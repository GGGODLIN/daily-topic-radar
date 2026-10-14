# 共用觀察池與研究選題雷達

`data/topic-pool.db` 在去重前保存每次成功觀察，包含來源已提供的正文。`state.db` 維持既有摘要格式，不新增資料表或欄位。

`--pool` 預設使用 `data/topic-pool.db`。`--dry-run` 與 `--smoke` 不寫入觀察池。`--analyze-pool` 只分析本機資料，不抓外部來源；`--seed-pool` 將指定日期的既有摘要匯入並標記 `legacy_excerpt`，不假裝它們是完整正文。

現有每日抓取指令不用加參數，會同時保存觀察池、產生雷達並接進 raw。只重算本機資料時，在 repo 根目錄執行：

```bash
uv run python -m social_info --analyze-pool --date YYYY-MM-DD
```

如果要建立歷史摘要的起始基準，就按日期從早到晚執行下列指令。來源身分依同一份 `sources.yml` 辨識；無法唯一辨識的舊資料保留 `unknown`，不猜來源。

```bash
uv run python -m social_info --analyze-pool --seed-pool --date YYYY-MM-DD
```

`data/topic-radar.json` 保存獨立的名稱歷史。正文池、名稱歷史與雷達報告皆留在本機，不跟 `state.db` 一起提交。daily topic 仍讀當日 raw，而不是直接查 SQLite。

雷達輸出 `reports/topic-radar/YYYY-MM-DD.{json,md}`，並將 `<!-- topic-radar:start -->` 區段接進 raw 日報。首次有資料的日期用作 `baseline_only`；後續日期才會產生新名稱候選。同日重跑保留當天候選。

RSS 正文來自 feed，不是額外抓取的文章頁。描述、摘要與缺正文的資料需保留限制標記；Hugging Face 的尾綴合併沿用上游規則，不代表語意上每個版本都相同。

`src/social_info/vendor/new_term_radar_terms.py` 保留上游 [radar/terms.py](https://github.com/zhuyansen/new-term-radar/blob/dd2f41b481b59a45aaad6426e719d49c29bf0c07/radar/terms.py) 的固定 commit，並附 `src/social_info/vendor/NEW_TERM_RADAR_LICENSE`。不計算或保存內容 hash。

## 原工具的免費觀察

`free_radar` 是另一個收集入口，使用原工具免費模組與自己的快照；它不取代本機名稱雷達。現有來源先抓取並存池，原工具再讀池中 X 正文、抓 HF trendingScore、近期建立的 GitHub repo，以及目錄 sitemap。新增抓取結果進同一個觀察池與 raw，不另抓 X、既有 HF likes7d 或既有 GitHub 查詢。

原工具設定見 [new-term-radar.json](/new-term-radar.json)，功能與費用盤點見 [來源配置方案](/docs/topic-radar-sources.md#免費原工具的共用接線)。`--dry-run`／`--smoke` 略過這個需保存比較快照的收集器並印出原因；正式抓取只選 `--source free_radar` 即可，不重跑既有來源。HF 與 sitemap 首次建立快照，只能報首次觀察，後續比較才有新進榜／新收錄。GitHub 平均星數成長是從建立時間算出的代理訊號，不是每天逐點量測，也不代表工具品質。

daily topic 的 Themes 與 WriteDigest 接點讀「新來源觀察」及原工具報告：可增加新 repo 研究、模型／demo 候選與目錄新增工具的線索，保留來源 URL、值得研究的原因及首次基準／失敗／摘要不足狀態。同 URL 與原來源交叉引用，不算獨立佐證。付費 SEO、Reddit 搜尋、評論痛點、新賣家、收入和本人 Search Console 授權都不啟用。

本輪正式成品採今日 digest 的增量更新，保留已完成主軸與既有查證，僅依新收集的證據增補研究段並更新來源數據；不是重新跑會重抓既有來源的完整 workflow。之後一般 daily topic 仍由原 workflow 產製。
