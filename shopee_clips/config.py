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
SELECTORS_PATH = Path("config/shopee_upload.json")

# (名稱, 預設值, 型別)。網頁設定頁與 reload() 共用這份清單。
SPEC = [
    # 腳本文案（LLM）
    ("TEXT_PROVIDER", "", str),            # 空白=未選擇（不用 AI，吃你填的賣點）| gemini | openai | claude
    ("TEXT_MODEL", "", str),               # 沒有預設，由使用者選/填
    # 圖片
    ("IMAGE_PROVIDER", "", str),           # 空白=未選擇（手動上傳）| gemini | openai | browser | manual
    ("IMAGE_MODEL", "", str),
    ("DEFAULT_IMAGE_SOURCE", "web", str),  # 新商品預設圖片來源：web=上網找圖當參考(AI 重新生成) | ai=純 AI 生成(不需參考圖)；每個商品可單獨改
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
    ("DAILY_GEN_CAP", 10, int),
    ("DAILY_UPLOAD_CAP", 5, int),
    ("UPLOAD_MODE", "manual", str),        # manual | dryrun | auto
    ("POLL_SECONDS", 60, int),
    ("PORT", 8000, int),
    ("APP_PASSWORD", "", str),             # 設了之後網頁要帳密(任意帳號名)才能進，手機/區網使用建議設
    ("AFFILIATE_PICKS_URL", "", str),
    ("SHOPEE_VIDEO_UPLOAD_URL", "", str),
]
SECRETS = {"GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "FAL_KEY", "APP_PASSWORD"}

# 設定頁「模型」欄的下拉建議（只是建議，沒有預設值；也可自己輸入任何模型名稱）
MODEL_SUGGESTIONS = {
    ("text", "gemini"): ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-pro"],
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
