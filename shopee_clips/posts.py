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

PROMPT = """你是台灣臉書／Threads 的生活風格小編，幫分潤好物寫「情境劇」貼文：用一個小場景或小對話帶出商品，而不是條列規格叫賣。
請依下列商品資訊，寫 3 則風格不同的貼文＋1 則 Threads 短文，輸出 JSON。
風格定義：
{styles}
硬性規定：
- 繁體中文、台灣口語，像真人朋友在聊天；每則 120~220 字，第一行是一句會讓人想繼續看的開頭（不要寫「大家好」）。
- 情境與人物是虛構的示意：不要假裝是真人的使用心得、不要編造「用了三個月」「已回購」「客人都說好」之類的經驗或評價。
- 商品的功能、規格只能用下面「說明」裡寫到的；不可編造認證、功效、銷量、「最低價」「全網最便宜」；不得有醫療／療效宣稱。
- 貼文裡不要放任何網址（我會另外加）。結尾用一句自然的提問，引導大家留言互動（例如問大家有沒有類似經驗）。
- emoji 適量（每則 0~4 個）。hashtag 3~5 個放在最後一行。
- comment：放在留言區的一句短文（20 字內），自然地請大家看商品連結，不要硬賣。
- threads：Threads 用的短文，80 字內，一個小情境＋一句結尾，不含 hashtag。
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
    pts = _points(row)
    if not pts:
        raise RuntimeError("沒有賣點可用：請在「待產圖」頁填 1~3 個賣點，或到設定頁選擇腳本 AI 並填 API key，才能寫出情境貼文")
    t = row["title"][:20] or "這個好物"
    bullets = "\n".join(f"✔ {p}" for p in pts)
    tags = "#蝦皮 #好物推薦 #生活小物"
    return {
        "posts": [
            {"style": "story",
             "text": f"前幾天又為了生活上的小瑣事煩了一下……後來試了「{t}」，才發現原來可以這麼省事。\n\n{bullets}\n\n你們有沒有類似的小困擾？留言跟我說 👇\n{tags}",
             "comment": "想看商品的在這 👇"},
            {"style": "dialog",
             "text": f"A：最近有沒有推薦的好物？\nB：我最近在用「{t}」\nA：好用嗎？\nB：{pts[0]}。\n\n你身邊也有這種愛分享的朋友嗎？😂\n{tags}",
             "comment": "朋友問的那款在這裡"},
            {"style": "pain",
             "text": f"有沒有人也遇過這種狀況？每次都覺得「應該有更好的方法吧」。\n\n「{t}」有這幾點：\n{bullets}\n\n你最想解決哪一個？留言告訴我！\n{tags}",
             "comment": "商品連結放這 👇"},
        ],
        "threads": f"每次遇到小麻煩都在想有沒有更好的辦法。最近發現「{t}」：{pts[0]}。你們呢？",
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
