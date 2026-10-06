from . import config


def client():
    if not config.GEMINI_API_KEY:
        raise SystemExit("請在 .env 設定 GEMINI_API_KEY")
    from google import genai

    return genai.Client(api_key=config.GEMINI_API_KEY)
