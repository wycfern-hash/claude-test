"""主角（出鏡人物）。內建幾個虛構 AI 角色，也可自訂、上傳形象照。
選了主角後：產圖提示詞會要求主角出現並展示商品（有形象照就一併當參考，確保同一個人）；
AI 影片提示詞帶入主角外貌；配音聲音跟著主角走。注意：自訂形象照請只用你本人、已取得同意的人，或 AI 生成的角色。
"""
from pathlib import Path

from . import config, db

PORTRAIT_PROMPT = ("Photorealistic half-body portrait of {desc}. Facing the camera, soft studio light, plain neutral light-grey "
                   "background, sharp focus, no text, no watermark. This is a fictional AI-generated character.")


def get(row) -> dict | None:
    """商品選的主角（沒選回 None）。"""
    cid = row["character_id"]
    if not cid:
        return None
    with db.connect() as conn:
        c = db.get_character(conn, cid)
    return dict(c) if c else None


def portrait(char: dict | None) -> Path | None:
    if char and char["ref_image"]:
        p = config.DATA_DIR / char["ref_image"]
        return p if p.exists() else None
    return None


def voice_for(row) -> str | None:
    c = get(row)
    return (c["voice"] or None) if c else None


def image_block(char: dict | None) -> str:
    """放進產圖提示詞的人物規則。"""
    if not char:
        return "- 畫面中不要出現清楚的人臉。"
    hands = "no face" in char["description"]
    lines = [f"- 畫面中要有一位主角：{char['description']}。"
             + ("只出現手與前臂，不要露臉。" if hands else "主角自然地使用或展示商品，面向鏡頭或側身皆可。")
             + "商品仍是畫面重點，外型必須與商品參考圖一致。"]
    if char["ref_image"]:
        lines.append("- 最後一張附圖是『主角參考圖』：主角必須與它是同一個人（臉、髮型、膚色、年齡感一致），服裝與場景可以不同。")
    return "\n".join(lines)


def video_block(char: dict | None) -> str:
    """放進腳本/影片提示詞的人物描述。"""
    if not char:
        return ""
    return (f"- 影片中的主角（出鏡人物）：{char['description']}。video_prompts 要讓這位主角在畫面中使用/展示商品，"
            "外貌與穿搭在每一段保持一致，不要出現文字疊字。\n")
