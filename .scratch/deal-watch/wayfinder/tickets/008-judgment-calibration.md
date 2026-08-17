---
status: open
type: prototype
blocked-by: []
---

## Question

判讀 LLM（gemini-3.7-flash-high）的推送門檻「有具體可行動內容才推」怎麼調教到使用者信任的程度？使用者要求（2026-08-17 grilling A2）：上線前用**過去一段時間的真實訊號**回填測試——抓歷史三源資料、讓 LLM 逐筆判「推／沉底」、使用者逐筆確認效果、迭代 prompt。已定（2026-08-17 grilling）：回填時間窗＝過去 14 天；過關判準＝使用者逐筆看完主觀點頭即可、不設同意率數字（使用者拍板，否決 80% 門檻提案）；與「日期注入 P0」對 gemini-3.7-flash-high 的重測合併執行。沉底帳（票 004）上線後持續餵此迴路。

## Resolution
