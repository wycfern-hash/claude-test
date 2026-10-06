from . import config


def client():
    if not config.GEMINI_API_KEY:
        raise SystemExit("請在 .env 設定 GEMINI_API_KEY")
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)


def check() -> list[str]:
    """用一次最小請求測你的 key：文字、產圖各測一次，回報能不能用。"""
    if not config.GEMINI_API_KEY:
        return ["沒有設定 GEMINI_API_KEY"]
    out = []
    c = client()
    try:
        r = c.models.generate_content(model=config.TEXT_MODEL, contents="回覆 OK")
        out.append(f"✅ 文字模型 {config.TEXT_MODEL} 可用（{r.text.strip()[:20]}）→ AI 寫腳本/賣點沒問題")
    except Exception as e:  # noqa: BLE001
        out.append(f"❌ 文字模型不可用：{str(e)[:200]}")
    try:
        r = c.models.generate_content(model=config.IMAGE_MODEL, contents="a plain red apple on white background")
        ok = any(getattr(p, "inline_data", None) for p in r.candidates[0].content.parts)
        out.append(f"{'✅' if ok else '❌'} 產圖模型 {config.IMAGE_MODEL} {'可用 → 產圖可全自動' if ok else '沒回傳圖片'}")
    except Exception as e:  # noqa: BLE001
        out.append(f"❌ 產圖模型不可用（改用手動上傳即可）：{str(e)[:200]}")
    return out
