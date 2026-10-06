"""15 秒開箱腳本：hook + 3 賣點 + CTA，順便產標題與貼文文案。
有 GEMINI_API_KEY 用 Gemini（文字模型有免費額度）；沒有就用範本 + 你輸入的賣點，完全不花錢也不連網。
"""
import json
import re

from . import config

PROMPT = """你是台灣蝦皮短影音的文案。根據下列商品資訊，寫一支 15 秒開箱短影音腳本，輸出 JSON。
限制：
- 只能使用商品資訊裡有的事實；不可編造規格、功效、認證、銷量或『最低價』；不得有醫療/療效宣稱。
- 繁體中文、口語、節奏快。每個字幕 16 字內。
- voiceover：整支影片的配音稿，總長 60 字內，依序唸 hook、3 個賣點、CTA（CTA 引導點下方商品連結）。
{extra}商品：{title}
價格：{price}
說明：{description}
賣家/我提供的賣點（有的話優先使用）：{user_points}
JSON 欄位：hook, selling_points(3 個字串), cta, voiceover, video_title(30 字內), caption(100 字內), hashtags(3~5 個字串){extra_fields}
"""
VEO_EXTRA = "- video_prompt_1 / video_prompt_2：給影片模型的英文畫面描述（鏡頭、動作、光線），延續同一商品與場景，不要疊字。\n"


def user_points(row) -> list[str]:
    try:
        return [p for p in json.loads(row["script"]).get("user_points", []) if p.strip()]
    except (ValueError, AttributeError):
        return []


def from_template(row) -> dict:
    pts = user_points(row)
    if not pts:  # 退而求其次：商品說明的前幾句（原文，不改寫、不編造）
        pts = [x.strip() for x in re.split(r"[。！!\n]", row["description"]) if 4 <= len(x.strip()) <= 20]
    pts = pts[:3]
    if not pts:
        raise RuntimeError("沒有賣點可用：請在「待產圖」頁填 1~3 個賣點（或設定 GEMINI_API_KEY）")
    title = row["title"][:14]
    hook, cta = f"開箱{title}！", "喜歡的話點下方商品連結"
    return {
        "hook": hook, "selling_points": pts, "cta": cta,
        "voiceover": "，".join([hook, *pts, cta]),
        "video_title": row["title"][:30] or "好物開箱",
        "caption": f"{hook} " + " ".join(f"✔{p}" for p in pts) + f" {cta}",
        "hashtags": ["蝦皮", "好物推薦", "開箱"],
    }


def from_gemini(row) -> dict:
    from google.genai import types

    from .gemini_client import client

    veo = config.VIDEO_PROVIDER == "veo"
    resp = client().models.generate_content(
        model=config.TEXT_MODEL,
        contents=PROMPT.format(
            extra=VEO_EXTRA if veo else "", extra_fields=", video_prompt_1, video_prompt_2" if veo else "",
            title=row["title"], price=row["price"], description=row["description"][:1500],
            user_points="；".join(user_points(row)) or "無",
        ),
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )
    s = json.loads(resp.text)
    if len(s.get("selling_points", [])) != 3:
        raise RuntimeError("腳本賣點不是 3 個")
    return s


def get_script(row) -> dict:
    """已有成品腳本就沿用；否則 Gemini（有 key）或範本。保留 user_points。"""
    try:
        cur = json.loads(row["script"])
    except ValueError:
        cur = {}
    if cur.get("video_title"):
        return cur
    s = from_gemini(row) if config.GEMINI_API_KEY else from_template(row)
    s["user_points"] = cur.get("user_points", [])
    return s
