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
    ("TEXT_PROVIDER", "auto", str),        # auto(依填的 key 自動決定) | gemini | openai | claude | template(不用 AI，吃你填的賣點)
    ("TEXT_MODEL", "", str),               # 空白 = 該供應商預設
    # 圖片
    ("IMAGE_PROVIDER", "auto", str),       # auto | gemini | openai | browser | manual
    ("IMAGE_MODEL", "", str),
    ("IMAGES_PER_PRODUCT", 5, int),
    # 影片
    ("VIDEO_MODE", "slideshow", str),      # 預設影片類型：slideshow=5 張圖合成 | ai=用新圖+腳本讓 AI 生成（每個商品可在審圖頁單獨改）
    ("VIDEO_PROVIDER", "auto", str),       # AI 影片引擎（VIDEO_MODE=ai 時用）：auto | veo | fal | flow_browser | flow
    ("VIDEO_MODEL", "", str),
    ("VIDEO_CLIP_SECONDS", 0, int),        # 單段秒數，0 = 供應商預設（veo 8、fal 5）
    ("FAL_EXTRA_ARGS", "", str),           # fal 模型額外參數 JSON，例如 {"duration":"5"}
    # 金鑰
    ("GEMINI_API_KEY", "", str),
    ("OPENAI_API_KEY", "", str),
    ("OPENAI_BASE_URL", "", str),          # 相容 OpenAI 的服務（如 DeepSeek）才需要
    ("ANTHROPIC_API_KEY", "", str),
    ("FAL_KEY", "", str),
    # 影片後製
    ("TTS", True, bool),
    ("TTS_VOICE", "zh-TW-HsiaoChenNeural", str),  # 曉臻
    ("SUBTITLES", True, bool),
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

DEFAULT_MODELS = {
    ("text", "gemini"): "gemini-2.5-flash",
    ("text", "openai"): "gpt-4.1-mini",
    ("text", "claude"): "claude-haiku-4-5-20251001",
    ("image", "gemini"): "gemini-2.5-flash-image",
    ("image", "openai"): "gpt-image-1",
    ("video", "veo"): "veo-3.1-generate-preview",
    ("video", "fal"): "fal-ai/kling-video/v2.1/standard/image-to-video",
}
DEFAULT_CLIP_SECONDS = {"veo": 8, "fal": 5}


def _cast(raw: str, typ):
    if typ is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return typ(raw) if typ is str else typ(raw or 0)


RAW: dict = {}  # 設定頁顯示用：使用者實際填的值（保留 "auto"，不是解析後的結果）


def raw(name: str) -> str:
    return RAW.get(name, "")


def _resolve_auto() -> None:
    """供應商留「自動」時，依填了哪些 key 決定：Gemini > OpenAI > Claude/fal。沒 key 就退回不花錢的做法。"""
    g = globals()
    if g["TEXT_PROVIDER"] == "auto":
        g["TEXT_PROVIDER"] = ("gemini" if g["GEMINI_API_KEY"] else "openai" if g["OPENAI_API_KEY"]
                              else "claude" if g["ANTHROPIC_API_KEY"] else "template")
    if g["IMAGE_PROVIDER"] == "auto":
        g["IMAGE_PROVIDER"] = "gemini" if g["GEMINI_API_KEY"] else "openai" if g["OPENAI_API_KEY"] else "manual"
    if g["VIDEO_PROVIDER"] == "auto":
        g["VIDEO_PROVIDER"] = "veo" if g["GEMINI_API_KEY"] else "fal" if g["FAL_KEY"] else "veo"


def summary() -> list[str]:
    """設定頁最上面的『目前自動啟用了什麼』。"""
    g = globals()
    out = []
    t = g["TEXT_PROVIDER"]
    out.append("腳本/賣點：" + ("AI（" + t + "）自己生成" if t != "template" else "尚未填 key → 用你填的賣點 + 範本"))
    i = g["IMAGE_PROVIDER"]
    out.append("產圖：" + {"manual": "尚未填 key → 你手動上傳", "browser": "操控 Chrome 用 Gemini 網頁"}.get(i, f"AI（{i}）自動產"))
    v = g["VIDEO_PROVIDER"]
    has_video_key = (v == "veo" and bool(g["GEMINI_API_KEY"])) or (v == "fal" and bool(g["FAL_KEY"])) or v in ("flow", "flow_browser")
    out.append("AI 生成影片（類型 B）：" + (f"可用（{v}）" if has_video_key else "尚無 → 需要 Gemini key（Veo）或 fal key；類型 A 圖片合成不需要"))
    return out


def reload() -> None:
    load_dotenv(ENV_PATH, override=True)
    g = globals()
    RAW.clear()
    for name, default, typ in SPEC:
        raw_v = os.getenv(name)
        RAW[name] = str(default) if raw_v is None else raw_v
        g[name] = default if raw_v is None else _cast(raw_v, typ)
    # 舊版相容：VIDEO_PROVIDER=slideshow 代表「圖片合成」模式；其他明確的引擎值且沒設 VIDEO_MODE 代表 AI 模式
    if g["VIDEO_PROVIDER"] == "slideshow":
        g["VIDEO_PROVIDER"] = "auto"
        RAW["VIDEO_PROVIDER"] = "auto"
        if os.getenv("VIDEO_MODE") is None:
            g["VIDEO_MODE"] = "slideshow"
    elif os.getenv("VIDEO_PROVIDER") not in (None, "", "auto") and os.getenv("VIDEO_MODE") is None:
        g["VIDEO_MODE"] = "ai"
    if g["IMAGE_PROVIDER"] == "api":  # 舊值
        g["IMAGE_PROVIDER"] = RAW["IMAGE_PROVIDER"] = "auto"
    _resolve_auto()


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
    """role: text | image | video。使用者有填就用，否則該供應商預設。"""
    provider = globals()[f"{role.upper()}_PROVIDER"]
    return globals()[f"{role.upper()}_MODEL"] or DEFAULT_MODELS.get((role, provider), "")


def clip_seconds() -> int:
    return VIDEO_CLIP_SECONDS or DEFAULT_CLIP_SECONDS.get(VIDEO_PROVIDER, 8)


def ensure_dirs() -> None:
    for d in (DATA_DIR, REF_DIR, IMG_DIR, VID_DIR, BROWSER_PROFILE):
        d.mkdir(parents=True, exist_ok=True)


reload()
