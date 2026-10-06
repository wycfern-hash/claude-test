# 蝦皮分潤短影音自動化

不用蝦皮 API、不用 AI 助理在旁邊跑。設定一次後 `./start.sh`（Windows 點 `start.bat`）就會自己跑：

```
貼商品連結／抓分潤後台選品 → 自動補商品資料 → Gemini 重新產圖 → 【你審圖】
→ 自動寫 15 秒腳本(3 賣點+CTA) → Veo 產片 → 【你審片】 → 自動上架 或 匯出上架包(手機傳)
```

## API key：貼上就自動啟用（網頁「設定」頁）
不用選供應商。把 key 貼到設定頁最上面的欄位，程式依填了哪些 key 自動決定：腳本/產圖用 Gemini > OpenAI > Claude，AI 影片用 Gemini（Veo）> fal。設定頁會列出「目前自動啟用了什麼」。供應商與模型的下拉選單收在最下面的「進階」，平常不用動。

| 你填的 key | 自動啟用 |
|---|---|
| 只填 Gemini | 腳本 + 產圖 + 類型 B 的 Veo 影片，全包 |
| 只填 OpenAI | 腳本 + 產圖（類型 B 需另填 Gemini 或 fal key） |
| 只填 Claude | 腳本（產圖需另填 Gemini/OpenAI，或手動上傳） |
| 只填 fal | 類型 B 影片 |
| 都不填 | 腳本用你填的賣點 + 範本、圖手動上傳、影片用類型 A 圖片合成（全免費） |

### 可用的供應商一覽
| 環節 | 可選供應商 | 需要的 key |
|---|---|---|
| 腳本文案（賣點、腳本、標題、文案、配音稿） | `gemini`／`openai`（含相容服務，填 Base URL）／`claude`／`template`（不用 AI，吃你填的賣點） | 對應供應商的 key |
| 圖片 | `gemini`／`openai`／`browser`（操控 Chrome 用 Gemini 網頁）／`manual`（手動上傳） | API 供應商要 key；後兩者不用 |
| 影片 | 兩種類型見下方；類型 B 的引擎：`veo`（Gemini API）／`fal`（fal.ai 的 Kling 等）／`flow_browser`／`flow` | veo 用 Gemini key；fal 用 FAL_KEY |

### 兩種影片類型（每個商品可單獨選，預設在設定頁）
| | A. 圖片合成 | B. AI 生成影片 |
|---|---|---|
| 做法 | 核准的 5 張新圖 × 3 秒，推近動畫 + 字幕 + 曉臻配音，ffmpeg 本機合成 | 用新生成的圖當起始畫面，依腳本的「影片提示詞」讓 AI 逐段生成，接成 15 秒並換上曉臻配音 |
| 費用 | 免費、不限量、不用 API | 花 API 費用（veo/fal）或 Flow 點數；受 `DAILY_GEN_CAP` 每日上限限制 |
| 腳本用途 | 5 個字幕（hook／3 賣點／CTA，畫面上不會出現這些標籤字樣） | 腳本會多產生影片提示詞（鏡頭、動作、光線） |

在「審圖」或「待產圖」頁，每個商品都有 A/B 選項；沒選就用設定頁的預設。同一批商品可以混用，A 不計入每日 AI 上限。一個商品仍然只會有一支影片。

- **腳本會自己生成**：選 AI 供應商並填 key，程式會把商品說明＋商品圖交給模型，自己歸納 3 個賣點，產出 hook、腳本、配音稿、標題、貼文文案、hashtag。你在待產圖/審圖頁填的賣點會優先採用。沒填 key 或 AI 失敗時，退回你填的賣點（或商品說明原句），不會編造。
- **一支 Gemini key 可以同時做腳本 + 圖片 + 影片（Veo）**；不想用付費影片就選 `slideshow`。
- 設定頁每區有「測試」按鈕（圖片測試會實際產 1 張；影片只檢查 key，避免誤燒錢）。`DAILY_GEN_CAP` 限制每天處理幾個商品。
- 影片 API 單段只有幾秒（veo 8 秒、fal 約 5 秒），程式自動分 2~3 段產、接成 15 秒、轉 1080×1920、換上曉臻配音。
- 手機/區網使用請在設定頁填「網頁密碼」。
- 配音 `zh-TW-HsiaoChenNeural` = 曉臻（免費 edge-tts）；字幕只含賣點內容，不會出現「賣點1/hook/CTA」標籤，`SUBTITLES=0` 可全關。

### 不用 API key：讓程式操控你的 Chrome（實驗性、**尚未在真實 Gemini/Flow 介面驗證**）
設定頁把圖片選 `browser`、影片選 `flow_browser`。
1. 首頁按「開啟自動化 Chrome」，會開一個**獨立資料夾**的 Chrome（`data/browser_profile`），在裡面手動登入 Google 與蝦皮一次。程式不接觸帳密；登入狀態留在這個 Chrome。
2. 程式透過 CDP 連上它：每張圖開新對話，附賣家參考圖+提示詞，等新圖出現再下載；Flow 則用「起始圖+提示詞」產 2 段、下載，再接成 15 秒換上曉臻配音。
3. `DAILY_GEN_CAP`（預設每天 10 個商品）限制每日處理量，因為你的 Gemini/Flow 點數與每日額度有限。失敗不會自動重試（避免白燒點數），首頁按「重試失敗項目」。
4. **按鈕文字是猜的**：失敗時 `data/debug/` 會有截圖與頁面元件清單（`.png` + `.json`）。把整個資料夾給我，我改 `config/browser_sites.json` 的標籤即可。也可以先手動把 Chrome 停在 Gemini/Flow 的某個畫面，按首頁「擷取目前分頁畫面結構」。
5. 注意：自動操作 Google 網頁介面可能違反其使用條款、帳號有被限制的風險，請自行評估；程式每步之間有間隔，量也有上限。

### 用 Excel 匯入選品
首頁「匯入 Excel」或 `python -m shopee_clips import-excel 選品.xlsx`。表頭自動辨識：**商品連結**（必填；也認 商品網址/連結/url）、商品名稱、價格、賣點1~3（選填，有填就當指定賣點）。支援文字網址、超連結儲存格、分潤短連結（s.shopee.tw 會自動展開）；同商品自動去重。

### 手動用 Flow（不讓程式操控瀏覽器）
`VIDEO_PROVIDER=flow`：審圖核准後「待產片」頁給起始圖與提示詞，你到 Flow 產完把 mp4 傳回，程式接成 15 秒並配音。


## 開始（Windows）
1. 安裝 [Python 3.11+](https://www.python.org/downloads/)（安裝時勾 **Add python.exe to PATH**）、Google Chrome、ffmpeg（命令列執行 `winget install ffmpeg`）。
2. 下載這個專案（GitHub → Code → Download ZIP）並解壓縮。
3. 不用手改任何檔案：啟動後到網頁「設定」頁填 API key、選供應商即可（會自動存成 `.env`）。
4. 雙擊 `start.bat`。第一次會自動安裝套件，之後秒開。黑色視窗是程式本體，**要開著**，關掉就停。
5. 瀏覽器開 `http://localhost:8000`（手機同 Wi-Fi 開視窗裡印出的網址）。
6. 首頁「開啟自動化 Chrome」→ 在跳出的 Chrome 登入 Google 與蝦皮 →「匯入 Excel」→ 之後到「審圖」「審片」「上架包」頁操作。

（Mac/Linux 用 `./start.sh`。）

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
