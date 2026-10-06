# 蝦皮分潤短影音自動化

不用蝦皮 API、不用 AI 助理在旁邊跑。設定一次後 `./start.sh`（Windows 點 `start.bat`）就會自己跑：

```
貼商品連結／抓分潤後台選品 → 自動補商品資料 → Gemini 重新產圖 → 【你審圖】
→ 自動寫 15 秒腳本(3 賣點+CTA) → Veo 產片 → 【你審片】 → 自動上架 或 匯出上架包(手機傳)
```

## 各環節怎麼跑
| 環節 | 預設 | 其他選項 |
|---|---|---|
| 產圖 | `IMAGE_PROVIDER=auto`：有 `GEMINI_API_KEY` 就**自動產**；產不出來（沒額度/模型不開放）就留在「待產圖」頁，給你參考圖+提示詞，你在 Gemini App 產完上傳 | `manual` 一律手動；`api` 只用 API |
| 賣點/腳本 | **AI 自己生成**：Gemini 文字模型看商品說明+商品圖，歸納 3 賣點、寫腳本、標題、文案、配音稿（免費額度即可）。可在待產圖/審圖頁填賣點覆蓋 | 沒 key：用你填的賣點+範本 |
| 產片 | `VIDEO_PROVIDER=slideshow`：5 張圖×3 秒=15 秒，推近動畫+字幕+配音，全自動、免費 | `flow`：用 Flow 點數產，上傳 mp4 回來（見下）；`veo`：API，付費 |
| 配音 | 曉臻（`zh-TW-HsiaoChenNeural`，免費 edge-tts） | `TTS=0` 不配音 |
| 字幕 | 影片上只會有賣點「內容」文字，不會出現 hook/賣點1/CTA 這類標籤 | `SUBTITLES=0` 完全不要字幕 |

### 用 Flow 的點數產片
Flow 沒有官方 API，程式不能替你操作它。流程：`.env` 設 `VIDEO_PROVIDER=flow` → 審圖核准後「待產片」頁出現起始圖與 2 段英文提示詞 → 你到 Flow 用 Frames to Video（9:16）產 → 把下載的 mp4 傳回該頁 → 程式自動接成 15 秒、轉 1080×1920、換上曉臻配音 → 進審片。

### 你的 Gemini key 能不能用？
首頁按「檢查 Gemini key 能不能用」，會各測一次文字與產圖，結果顯示在執行紀錄。免費 key 通常文字可用；產圖不一定。

## 開始
1. `cp .env.example .env`（全免費模式不用改任何東西）。
2. `./start.sh`（Windows 點 `start.bat`），電腦開 `http://localhost:8000`，手機同 Wi-Fi 開終端機印出的網址。需要先裝 Python 3.11+ 與 ffmpeg。
3. 網頁按「開瀏覽器登入蝦皮」，手動登入（分潤後台＋短影音後台），登入完關掉視窗。帳密不會被程式儲存，只留瀏覽器 session 在 `data/browser_profile`。
4. 貼商品連結（一行一個）→「待產圖」頁拿參考圖與提示詞去 Gemini 產圖並上傳 → 程式自動合成影片 →「審片」核准 →「上架包」頁（或自動上架）。

## 規則
- 賣家圖只當 AI 參考輸入，存在 `data/ref`，**不會上傳、不會進影片**；輸出必須是全新構圖。
- 5 張圖依序對應 hook／賣點1／賣點2／賣點3／CTA，字幕來自腳本。
- 一商品一影片：以商品 ID 去重，同商品不會再進流程。
- `DAILY_GEN_CAP` 限制每日產片數（Veo 按秒計費），`DAILY_UPLOAD_CAP` 限制每日上架數。

## 上架模式（`.env` 的 `UPLOAD_MODE`）
- `manual`（預設）：核准後到「上架包」頁，下載影片、複製標題文案，手機或電腦自己傳。
- `dryrun`：電腦自動填好檔案、文案、綁商品，但不按發佈，讓你檢查。**第一次請先跑這個**，卡住的步驟會截圖到 `data/debug/`，到 `config/shopee_upload.json` 改按鈕文字即可。
- `auto`：填完自動發佈。失敗的項目會自動留上架包。

## 影片
Flow 沒有官方 API，預設改用同一家的 Veo API（`VIDEO_PROVIDER=veo`）。想繼續手動用 Flow：設 `manual`，審圖後依 `shopee_clips/videogen.py` 的 export/import 流程操作。

## 測試
`pytest`（不連網，不呼叫 Gemini/蝦皮）。
