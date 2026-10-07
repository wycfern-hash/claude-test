"""臉書／Threads 引流貼文：每個商品配幾則「情境劇」式文案（有場景、有情緒、有一點故事），而不是只念商品規格。
選了腳本 AI（TEXT_PROVIDER）就由 AI 依商品說明寫；沒選就用你填的賣點 + 範本（比較制式）。
貼文本身不含連結（分潤連結可能之後才轉好），輸出／匯出時才依商品目前的分潤連結組合。
"""
import json
import re

from . import config, db, providers

STYLES = (
    ("story", "生活小故事", "第三人稱或第一人稱的生活片段：有時間地點、一個小困擾、遇到這個商品後的小改變，結尾自然帶出商品"),
    ("dialog", "對話情境劇", "兩個人的 LINE／當面對話（用「A：」「B：」或兩個暱稱），一個抱怨或疑惑、一個推薦，像朋友聊天，讓人想看下去"),
    ("pain", "痛點共鳴", "第一句直接戳中一個很多人有的日常痛點，接著用 2~3 句描述當下的狀況，再帶出商品怎麼解決（只講商品說明裡有的功能）"),
)

PROMPT = """你是台灣臉書／Threads 上很會寫貼文的幽默小編，幫分潤好物寫「情境劇」貼文：用一個好笑、有點自嘲、讓人會心一笑的小場景或小對話帶出商品，而不是廣告口吻或念規格。
目標：滑到的人會停下來看完、甚至想留言「我也是」。依下列商品資訊，寫 3 則風格不同的貼文＋1 則 Threads 短文，輸出 JSON。
風格定義（三則的哏要完全不同）：
{styles}
寫法要求：
- 繁體中文、台灣口語、像朋友吐槽；短、有節奏、有反差或自嘲（例如「說好不買的」「錢包：拜託不要」「手已經在結帳」那種），結尾要有個小梗收尾，不要用「你們有沒有類似的經驗？留言告訴我」這種制式問句。
- 每則 50~120 字；第一行就要有梗或有畫面，不要寫「大家好」「週末整理家裡」這類刻意的開場。三則的開頭、結構、梗都不要一樣。
- emoji 最多 0~2 個；hashtag 2~3 個放最後一行。
硬性規定：
- 情境與人物是虛構示意：不要假裝是真人的使用心得、不要編造「用了三個月」「已回購」「客人都說好」之類的經驗或評價。
- 商品的功能、規格只能用下面「說明」裡寫到的；不可編造認證、功效、銷量、「最低價」「全網最便宜」；不得有醫療／療效宣稱。
- 貼文裡不要放任何網址（我會另外加）。
- comment：放在留言區的一句短文（20 字內），輕鬆有趣地請大家看商品連結。
- threads：Threads 用的短文，60 字內，一個梗，不含 hashtag。
商品：{title}
價格：{price}
說明：{description}
我指定的賣點（有的話優先融入）：{user_points}
JSON 格式：{{"posts":[{{"style":"story|dialog|pain","text":"...","comment":"..."}}, ...共 3 則，順序 story、dialog、pain], "threads":"..."}}
"""


def link_of(row) -> str:
    """商品目前的分潤連結；沒有就回傳空字串（絕不拿一般連結冒充）。"""
    for u in (row["source_url"], row["url"]):
        if db.is_affiliate(u):
            return u
    return ""


def _points(row) -> list[str]:
    try:
        pts = [p for p in json.loads(row["script"]).get("user_points", []) if p.strip()]
    except (ValueError, AttributeError):
        pts = []
    if not pts:
        pts = [x.strip() for x in re.split(r"[。！!\n]", row["description"]) if 4 <= len(x.strip()) <= 24]
    return pts[:3]


def from_template(row) -> dict:
    """不用 AI 的範本：幽默吐槽風，只引用你填的賣點／商品說明裡的句子，不編造。"""
    pts = _points(row)
    if not pts:
        raise RuntimeError("沒有賣點可用：請在「待產圖」頁填 1~3 個賣點，或到設定頁選擇腳本 AI 並填 API key，才能寫出情境貼文")
    t = re.sub(r"[【\[].*?[】\]]", "", row["title"]).strip()[:18] or "這個好物"
    bullets = "\n".join(f"✔ {p}" for p in pts)
    tags = "#蝦皮 #手滑 #好物"
    return {
        "posts": [
            {"style": "story", "text": f"我：這個月要存錢，不亂買。\n「{t}」：{pts[0]}。\n我：……\n（手已經在結帳）\n\n{bullets}\n\n{tags}",
             "comment": "想一起手滑的在這 👇"},
            {"style": "dialog", "text": f"A：你又買東西了？\nB：沒有，是它自己跑進購物車的。\nA：什麼東西？\nB：「{t}」，{pts[0]}，我能怎麼辦。\n\n{tags}",
             "comment": "連結放這，後果自負 😂"},
            {"style": "pain", "text": f"理智：「{t}」不是必需品。\n手指：{pts[0]}，先買再說。\n\n{bullets}\n\n{tags}",
             "comment": "要買的話在這 👇"},
        ],
        "threads": f"說好不買的，然後「{t}」{pts[0]}。我的理智已下線。",
    }


def from_llm(row) -> dict:
    from .imagegen import ref_files

    styles = "\n".join(f"- {k}（{zh}）：{desc}" for k, zh, desc in STYLES)
    prompt = PROMPT.format(styles=styles, title=row["title"], price=row["price"],
                           description=row["description"][:1500], user_points="；".join(_points(row)) or "無")
    s = providers.text_json(prompt, [p.read_bytes() for p in ref_files(row["id"])[:2]])
    items = [p for p in s.get("posts", []) if str(p.get("text", "")).strip()]
    if not items:
        raise RuntimeError("AI 沒有回傳貼文內容")
    return {"posts": [{"style": str(p.get("style", "")), "text": str(p["text"]).strip(),
                       "comment": str(p.get("comment", "")).strip()} for p in items[:3]],
            "threads": str(s.get("threads", "")).strip()}


def generate(conn, pid: int) -> dict:
    """產生並存起來（覆蓋舊的）。AI 失敗（額度用完等）且有賣點可用就退回範本。"""
    row = db.get(conn, pid)
    if providers.configured("text"):
        try:
            data = from_llm(row)
        except Exception:  # noqa: BLE001
            data = from_template(row)
    else:
        data = from_template(row)
    data["at"] = db.now()
    conn.execute("UPDATE products SET posts=?, updated_at=? WHERE id=?", (json.dumps(data, ensure_ascii=False), db.now(), pid))
    return data


def load(row) -> dict:
    try:
        d = json.loads(row["posts"])
    except (ValueError, TypeError, KeyError):
        return {}
    return d if isinstance(d, dict) else {}


def style_zh(key: str) -> str:
    return next((zh for k, zh, _ in STYLES if k == key), key or "貼文")


PLACEHOLDER = "【這個商品還沒有分潤連結，先到小幫手轉成分潤連結再貼上】"


def compose(row, item: dict, with_link: bool = True) -> str:
    """完整貼文：正文 + （連結）+ 分潤揭露。"""
    parts = [item["text"].strip()]
    if with_link:
        parts.append("👉 商品連結：" + (link_of(row) or PLACEHOLDER))
    if config.POST_DISCLOSURE.strip():
        parts.append(config.POST_DISCLOSURE.strip())
    return "\n\n".join(parts)


def compose_comment(row, item: dict) -> str:
    c = (item.get("comment") or "商品連結在這 👇").strip()
    return f"{c}\n{link_of(row) or PLACEHOLDER}"


def generate_missing() -> tuple[int, int, str]:
    """替還沒有貼文的商品（不含略過）逐一產生；單一商品失敗不影響其他。回傳 (成功, 失敗, 最後一個錯誤)。"""
    ok = bad = 0
    err = ""
    with db.connect() as conn:
        ids = [r["id"] for r in db.all_products(conn) if r["status"] != "skipped" and not load(r)]
    for pid in ids:
        try:
            with db.connect() as conn:
                generate(conn, pid)
            ok += 1
        except Exception as ex:  # noqa: BLE001
            bad += 1
            err = str(ex)
    return ok, bad, err
