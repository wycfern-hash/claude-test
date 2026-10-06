import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DB_PATH = DATA_DIR / "clips.db"
REF_DIR = DATA_DIR / "ref"  # 賣家原圖：只當 AI 參考輸入，絕不上傳、不放進影片
IMG_DIR = DATA_DIR / "images"
VID_DIR = DATA_DIR / "videos"
BROWSER_PROFILE = DATA_DIR / "browser_profile"  # 你手動登入後的 session，不存帳密
SELECTORS_PATH = Path("config/shopee_upload.json")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
IMAGE_MODEL = os.getenv("IMAGE_MODEL", "gemini-2.5-flash-image")
TEXT_MODEL = os.getenv("TEXT_MODEL", "gemini-2.5-flash")
VIDEO_MODEL = os.getenv("VIDEO_MODEL", "veo-3.1-generate-preview")
IMAGES_PER_PRODUCT = _int("IMAGES_PER_PRODUCT", 5)
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "auto")  # auto=有 key 就自動產，失敗改手動上傳 | manual | api | browser=操控你的 Chrome 用 Gemini 網頁產圖
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "slideshow")  # slideshow(免費) | flow(你自己在 Flow 產、上傳回來) | flow_browser(操控你的 Chrome 在 Flow 產) | veo(付費)
FONT_PATH = os.getenv("FONT_PATH", "")
SUBTITLES = os.getenv("SUBTITLES", "1") == "1"  # slideshow 影片是否燒入字幕（只有內容文字，不會有「賣點1」這類標籤）
TTS = os.getenv("TTS", "1") == "1"  # 免費 edge-tts 配音
TTS_VOICE = os.getenv("TTS_VOICE", "zh-TW-HsiaoChenNeural")
CDP_PORT = _int("CDP_PORT", 9222)
CHROME_PATH = os.getenv("CHROME_PATH", "")
CHROME_HEADLESS = os.getenv("CHROME_HEADLESS", "0") == "1"  # 僅供測試/無螢幕環境
FLOW_CLIPS_PER_PRODUCT = _int("FLOW_CLIPS_PER_PRODUCT", 2)
DAILY_UPLOAD_CAP = _int("DAILY_UPLOAD_CAP", 5)
DAILY_GEN_CAP = _int("DAILY_GEN_CAP", 10)  # 每日最多產幾支影片（Veo 要錢）
UPLOAD_MODE = os.getenv("UPLOAD_MODE", "manual")  # manual | dryrun | auto
POLL_SECONDS = _int("POLL_SECONDS", 60)
PORT = _int("PORT", 8000)
AFFILIATE_PICKS_URL = os.getenv("AFFILIATE_PICKS_URL", "")
SHOPEE_VIDEO_UPLOAD_URL = os.getenv("SHOPEE_VIDEO_UPLOAD_URL", "")


def ensure_dirs() -> None:
    for d in (DATA_DIR, REF_DIR, IMG_DIR, VID_DIR, BROWSER_PROFILE):
        d.mkdir(parents=True, exist_ok=True)
