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
