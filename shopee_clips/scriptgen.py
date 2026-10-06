"""15 秒開箱腳本：hook + 3 賣點 + CTA，順便產標題、貼文文案、配音稿。
TEXT_PROVIDER 選 gemini/openai/claude 就由 AI 看商品說明+商品圖自己歸納賣點；選 template 或沒填 key 就用你填的賣點 + 範本。
"""
import json
import math
import re

from . import characters, config, providers

PROMPT = """你是台灣蝦皮短影音的文案。根據下列商品資訊（以及附圖中看得到的商品），自己歸納 3 個最吸引人的賣點，寫一支 15 秒開箱短影音腳本，輸出 JSON。
限制：
- 只能使用商品說明或附圖裡確實有的事實；不可編造規格、功效、認證、銷量或『最低價』；不得有醫療/療效宣稱。
- 繁體中文、口語、節奏快。每個字幕 16 字內。
- voiceover：整支影片的配音稿，總長 60 字內，依序唸 hook、3 個賣點、CTA（CTA 引導點下方商品連結）。
{extra}商品：{title}
價格：{price}
說明：{description}
我指定的賣點（有的話優先使用，沒有就由你歸納）：{user_points}
JSON 欄位：hook, selling_points(3 個字串), cta, voiceover, video_title(30 字內), caption(100 字內), hashtags(3~5 個字串){extra_fields}
"""


def clips_needed() -> int:
    """要幾段影片才湊滿 15 秒。"""
    if config.VIDEO_PROVIDER == "flow_browser":
        return max(1, min(config.FLOW_CLIPS_PER_PRODUCT, 3))
    return max(1, math.ceil(15 / config.clip_seconds()))


def video_prompts(script: dict, n: int) -> list[str]:
    ps = script.get("video_prompts") or [script.get("video_prompt_1", ""), script.get("video_prompt_2", "")]
    ps = [p for p in ps if p] or ["Vertical 9:16 product showcase, soft light, slow push-in, no text overlays."]
    return [ps[i % len(ps)] for i in range(n)]


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
        raise RuntimeError("沒有賣點可用：請在「待產圖」頁填 1~3 個賣點，或到設定頁選擇 AI 腳本供應商並填 API key")
    title = row["title"][:14]
    hook, cta = f"開箱{title}！", "喜歡的話點下方商品連結"
    t = row["title"]
    char = characters.get(row)
    who = f"{char['description']}. " if char else ""
    return {
        "hook": hook, "selling_points": pts, "cta": cta,
        "voiceover": "，".join([hook, *pts, cta]),
        "video_title": t[:30] or "好物開箱",
        "caption": f"{hook} " + " ".join(f"✔{p}" for p in pts) + f" {cta}",
        "hashtags": ["蝦皮", "好物推薦", "開箱"],
        "video_prompts": [
            f"Vertical 9:16 product video: {who}unboxing {t} on a clean table, soft natural light, slow camera push-in, no text overlays.",
            f"Vertical 9:16 {who}close-up details and everyday use of {t}, smooth handheld camera, warm light, no text overlays.",
            f"Vertical 9:16 hero shot of {t}{(' held by ' + char['description']) if char else ''} on a gradient studio background, slow rotation, no text overlays.",
        ],
    }


def from_llm(row, mode: str = "slideshow") -> dict:
    from .imagegen import ref_files

    n = clips_needed()
    with_video = mode == "ai"
    extra = f"- video_prompts：{n} 個給影片模型的英文畫面描述（鏡頭、動作、光線），依序延續同一商品與場景，不要疊字。\n" if with_video else ""
    extra += characters.video_block(characters.get(row))
    prompt = PROMPT.format(
        extra=extra, extra_fields=", video_prompts(字串陣列)" if with_video else "",
        title=row["title"], price=row["price"], description=row["description"][:1500],
        user_points="；".join(user_points(row)) or "無")
    s = providers.text_json(prompt, [p.read_bytes() for p in ref_files(row["id"])[:3]])
    if len(s.get("selling_points", [])) != 3:
        raise RuntimeError("腳本賣點不是 3 個")
    return s


def get_script(row, mode: str = "slideshow") -> dict:
    """已有成品腳本就沿用（AI 影片模式還需要有影片提示詞，沒有就重生）；否則 AI（有 key）或範本。保留 user_points。"""
    try:
        cur = json.loads(row["script"])
    except ValueError:
        cur = {}
    if cur.get("video_title") and (mode != "ai" or cur.get("video_prompts") or cur.get("video_prompt_1")):
        return cur
    if providers.configured("text"):
        try:
            s = from_llm(row, mode)
        except Exception:  # noqa: BLE001  額度用完等 → 有賣點可用就退回範本，否則把錯誤丟出來
            s = from_template(row)
    else:
        s = from_template(row)
    s["user_points"] = cur.get("user_points", [])
    return s
