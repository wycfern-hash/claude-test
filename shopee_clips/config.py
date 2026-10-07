"""設定。來源：.env（網頁「設定」頁會直接改寫它）。模組層級變數在 reload() 時更新，其他模組以 config.X 讀取即可。"""
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ENV_PATH = Path(".env")
load_dotenv(ENV_PATH)

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "clips.db"
REF_DIR = DATA_DIR / "ref"  # 賣家原圖：只當 AI 參考輸入，絕不上傳、不放進影片
IMG_DIR = DATA_DIR / "images"
VID_DIR = DATA_DIR / "videos"
BROWSER_PROFILE = DATA_DIR / "browser_profile"  # 自動化專用 Chrome 的資料夾（登入狀態在這，不存帳密）
PHONE_STEPS_PATH = Path("config/shopee_app_steps.json")

# (名稱, 預設值, 型別)。網頁設定頁與 reload() 共用這份清單。
SPEC = [
    # 腳本文案（LLM）
    ("TEXT_PROVIDER", "", str),            # 空白=未選擇（不用 AI，吃你填的賣點）| gemini | openai | claude
    ("TEXT_MODEL", "", str),               # 沒有預設，由使用者選/填
    # 圖片
    ("IMAGE_PROVIDER", "", str),           # 空白=未選擇（手動上傳）| gemini | openai | browser | manual
    ("IMAGE_MODEL", "", str),
    ("DEFAULT_IMAGE_SOURCE", "auto", str),  # 新商品預設圖片來源：auto=有參考圖就用、沒有就純 AI 生成 | web=一定要參考圖(AI 重新生成) | ai=純 AI 生成；每個商品可單獨改
    ("IMAGES_PER_PRODUCT", 5, int),
    # 影片
    ("VIDEO_MODE", "slideshow", str),      # 預設影片類型：slideshow=5 張圖合成 | ai=用新圖+腳本讓 AI 生成（每個商品可在審圖頁單獨改）
    ("VIDEO_PROVIDER", "", str),           # 類型 B 的 AI 影片服務：空白=未選擇 | veo | fal | flow_browser | flow
    ("VIDEO_MODEL", "", str),
    ("VIDEO_CLIP_SECONDS", 0, int),        # 單段秒數，0 = 供應商預設（veo 8、fal 5）
    ("FAL_EXTRA_ARGS", "", str),           # fal 模型額外參數 JSON，例如 {"duration":"5"}
    # 金鑰
    ("GEMINI_API_KEY", "", str),
    ("OPENAI_API_KEY", "", str),
    ("OPENAI_BASE_URL", "", str),          # 相容 OpenAI 的服務（如 DeepSeek）才需要
    ("ANTHROPIC_API_KEY", "", str),
    ("FAL_KEY", "", str),
    # 主角（出鏡人物）
    ("DEFAULT_CHARACTER_ID", 0, int),      # 新商品預設搭配的主角，0 = 不出現人物（每個商品可再單獨改）
    # 影片後製
    ("TTS", True, bool),
    ("TTS_VOICE", "zh-TW-HsiaoChenNeural", str),  # 曉臻
    ("SUBTITLES", True, bool),
    ("AI_LABEL", True, bool),              # 影片左上角顯示「AI 生成」標示
    ("FONT_PATH", "", str),
    # 瀏覽器
    ("CDP_PORT", 9222, int),
    ("CHROME_PATH", "", str),
    ("CHROME_HEADLESS", False, bool),
    ("FLOW_CLIPS_PER_PRODUCT", 2, int),
    # 流程
    ("AUTO_RUN", False, bool),            # 自動處理開關（花 API 費用/用 Chrome 的步驟要按「開始」才會跑）
    ("AUTO_ENRICH", False, bool),         # 背景自動開 Chrome 去蝦皮商品頁補標題/圖片（預設關）
    ("DAILY_GEN_CAP", 10, int),
    ("DAILY_UPLOAD_CAP", 5, int),
    ("UPLOAD_MODE", "manual", str),        # manual=只匯出上架包 | phone_dryrun=手機自動操作但不按發佈 | phone_auto=手機自動發佈
    ("POLL_SECONDS", 60, int),
    ("PORT", 8000, int),
    ("APP_PASSWORD", "", str),             # 設了之後網頁要帳密(任意帳號名)才能進，手機/區網使用建議設
    ("AFFILIATE_PICKS_URL", "", str),
    # Android 手機自動上架（蝦皮短影音只有手機版）
    ("ALLOW_PLAIN_LINK", False, bool),    # 允許沒有分潤連結的商品用一般連結上架（預設不允許）
    ("PHONE_SERIAL", "", str),             # 接了多支手機時指定序號；空白=唯一那支
    ("PHONE_PACKAGE", "com.shopee.tw", str),
    # 臉書／Threads 貼文
    ("POST_DISCLOSURE", "（以上為情境示意）※ 內含蝦皮分潤連結，經由連結購買我可能獲得佣金，不影響你的售價。", str),
    # Threads / Facebook 粉絲專頁 自動發文與回留言（操控你的 Chrome，不用 API）
    ("SOCIAL_AUTO", False, bool),          # 背景自動發佇列裡的貼文（要在「發文」頁按「開始自動發文」）
    ("SOCIAL_DAILY_CAP", 3, int),          # 每天最多自動發幾則（所有平台合計）
    ("SOCIAL_MIN_GAP_MIN", 90, int),       # 兩則之間至少隔幾分鐘（再加 0~20% 隨機）
    ("SOCIAL_LINK_IN", "comment", str),    # comment=連結放自己貼文的第一則留言 | body=連結放貼文裡
    ("SOCIAL_FB_PAGE_URL", "", str),       # 你的粉絲專頁網址
    ("SOCIAL_REPLY_AUTO", False, bool),    # 自動回「有人問連結」的留言（其他留言一律等你確認）
    ("SOCIAL_REPLY_DAILY_CAP", 15, int),   # 每天最多回幾則留言
    ("SOCIAL_LEAD_DAILY_CAP", 10, int),    # 「找話題」每天最多回幾篇別人的貼文（每篇都要你按一下確認）
    # 雲端（S3 相容：AWS S3 / Cloudflare R2 / Backblaze B2 / MinIO）：影片上傳後產生下載連結，手機任何網路都能下載
    ("CLOUD_ENDPOINT", "", str),
    ("CLOUD_BUCKET", "", str),
    ("CLOUD_ACCESS_KEY", "", str),
    ("CLOUD_SECRET_KEY", "", str),
    ("CLOUD_PUBLIC_BASE", "", str),        # bucket 已公開時填網址前綴；空白=用 7 天有效的預簽名連結
]
SECRETS = {"GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FAL_KEY", "APP_PASSWORD", "CLOUD_ACCESS_KEY", "CLOUD_SECRET_KEY"}

# 設定頁「模型」欄的下拉建議（只是建議，沒有預設值；也可自己輸入任何模型名稱）
MODEL_SUGGESTIONS = {
    ("text", "gemini"): ["gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.1-pro-preview"],
    ("text", "openai"): ["gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini", "gpt-4o"],
    ("text", "claude"): ["claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"],
    ("image", "gemini"): ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"],
    ("image", "openai"): ["gpt-image-1"],
    ("video", "veo"): ["veo-3.1-generate-preview", "veo-3.1-fast-generate-preview", "veo-3.0-generate-001", "veo-3.0-fast-generate-001"],
    ("video", "fal"): ["fal-ai/kling-video/v2.1/standard/image-to-video", "fal-ai/kling-video/v2.1/master/image-to-video"],
}
KEY_FOR = {"gemini": "GEMINI_API_KEY", "veo": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY",
           "claude": "ANTHROPIC_API_KEY", "fal": "FAL_KEY"}
DEFAULT_CLIP_SECONDS = {"veo": 8, "fal": 5}


def _cast(raw: str, typ):
    if typ is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return typ(raw) if typ is str else typ(raw or 0)


RAW: dict = {}  # 設定頁顯示用：使用者實際填的值（保留 "auto"，不是解析後的結果）


def raw(name: str) -> str:
    return RAW.get(name, "")


def reload() -> None:
    load_dotenv(ENV_PATH, override=True)
    g = globals()
    RAW.clear()
    for name, default, typ in SPEC:
        raw_v = os.getenv(name)
        RAW[name] = str(default) if raw_v is None else raw_v
        g[name] = default if raw_v is None else _cast(raw_v, typ)
    # 蝦皮短影音沒有網頁版：舊的 dryrun/auto（網頁上架）改對應到手機版
    g["UPLOAD_MODE"] = {"dryrun": "phone_dryrun", "auto": "phone_auto"}.get(g["UPLOAD_MODE"], g["UPLOAD_MODE"])
    RAW["UPLOAD_MODE"] = g["UPLOAD_MODE"]
    # 舊版值正規化：不再有 auto；VIDEO_PROVIDER=slideshow 代表「類型 A 圖片合成」
    for k in ("TEXT_PROVIDER", "IMAGE_PROVIDER", "VIDEO_PROVIDER"):
        if g[k] in ("auto", "api", "template", "slideshow"):
            if g[k] == "slideshow" and os.getenv("VIDEO_MODE") is None:
                g["VIDEO_MODE"] = "slideshow"
            g[k] = RAW[k] = ""


def save_env(updates: dict) -> None:
    """把設定寫回 .env（保留其他行），並立即生效。空字串的秘密欄位視為『不變更』由呼叫端處理。"""
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, ln in enumerate(lines):
        key = ln.split("=", 1)[0].strip()
        if key in updates and not ln.lstrip().startswith("#"):
            lines[i] = f"{key}={updates[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in updates.items() if k not in seen]
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = str(v)
    reload()


def model_for(role: str) -> str:
    """role: text | image | video。沒有預設：使用者沒選就是空字串。"""
    return globals()[f"{role.upper()}_MODEL"]


def clip_seconds() -> int:
    return VIDEO_CLIP_SECONDS or DEFAULT_CLIP_SECONDS.get(VIDEO_PROVIDER, 8)


def ensure_dirs() -> None:
    for d in (DATA_DIR, REF_DIR, IMG_DIR, VID_DIR, BROWSER_PROFILE):
        d.mkdir(parents=True, exist_ok=True)


reload()
