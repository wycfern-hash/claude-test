"""AI 產圖。賣家圖只當『參考輸入』，輸出必須是全新構圖；賣家原圖不會被上傳或放進影片。"""
import json
from pathlib import Path

import httpx

from . import characters, config, db
from . import providers

IMAGE_PROMPT = """請參考附圖中的商品，重新生成一張全新的商品形象照（9:16 直式）。
規則：
- 商品本體的外型、顏色、材質、功能特徵必須與參考圖一致，不可誇大或改變功能。
- 嚴禁複製參考圖的構圖、背景、道具、模特兒、浮水印、賣場 logo 與疊字；請換新的拍攝角度與乾淨的新場景。
- 畫面中不要出現任何文字、浮水印、價格標籤。
{person_rule}
- 風格：明亮、乾淨、有質感的生活情境或棚拍，適合短影音開場畫面。
商品名稱：{title}
變化重點：{variation}
"""
AI_ONLY_PROMPT = """請生成一張全新的商品形象照（9:16 直式）。沒有參考照片，請依下列文字描述畫出商品。
規則：
- 依描述畫出商品本體；不要自行加上品牌 logo、商標或任何文字，不要誇大或編造功能。
- 畫面中不要出現任何文字、浮水印、價格標籤。
{person_rule}
- 風格：明亮、乾淨、有質感的生活情境或棚拍，適合短影音開場畫面。
商品名稱：{title}
商品描述：{description}
賣點：{points}
變化重點：{variation}
"""
VARIATIONS = ["開場主視覺：斜 45 度、簡約乾淨背景", "細節特寫：微距質感、淺景深", "桌面生活情境：柔和自然光", "居家使用情境：自然、有生活感", "結尾主視覺：商品置中、漸層棚拍背景"]


def download_refs(pid: int, urls: list[str]) -> list[Path]:
    d = config.REF_DIR / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    out = []
    for i, u in enumerate(urls[:3]):
        p = d / f"{i}.jpg"
        if not p.exists():
            r = httpx.get(u, timeout=30, follow_redirects=True)
            r.raise_for_status()
            p.write_bytes(r.content)
        out.append(p)
    return out


def source_for(row) -> str:
    """圖片來源：web=上網找圖當參考，AI 重新生成；ai=純 AI 生成（不需參考圖）。
    auto（預設）：有參考圖（CSV 的圖片連結、你在「找圖」挑的、上傳的）就用 web，沒有就 ai。"""
    src = row["image_source"] or config.DEFAULT_IMAGE_SOURCE
    if src == "auto":
        return "web" if ref_files(row["id"]) else "ai"
    return src if src in ("web", "ai") else "ai"


def collect_refs(row) -> list[Path]:
    """產圖用的商品參考圖：你在「找圖」挑的優先（w*.jpg），沒有才用商品頁抓到的。最多 4 張。"""
    files = ref_files(row["id"])
    picks = [p for p in files if p.name.startswith("w")]
    return (picks or files)[:4]


def ready_for_gen(row) -> bool:
    """純 AI 生成隨時可以產；上網找圖模式要先有參考圖（沒有就等你去找圖）。"""
    return source_for(row) == "ai" or bool(ref_files(row["id"]))


def generate_for(conn, row) -> list[str]:
    src = source_for(row)
    refs = [] if src == "ai" else collect_refs(row)
    if src == "web" and not refs:
        raise RuntimeError("尚無參考圖：請到「待產圖」頁上網找圖，或改用 AI 生成")
    char = characters.get(row)
    if characters.portrait(char):
        refs = [*refs, characters.portrait(char)]  # 主角形象照放最後一張
    out_dir = config.IMG_DIR / str(row["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for n in range(config.IMAGES_PER_PRODUCT):
        prompt = prompts_for(row, char)[n]
        f = out_dir / f"{n}.png"
        f.write_bytes(providers.image_bytes(prompt, refs))
        saved.append(str(f.relative_to(config.DATA_DIR)))
    return saved


def prompts_for(row, char="__lookup__") -> list[str]:
    """每張圖的提示詞（含主角規則）。手動模式：把這些提示詞連同參考圖貼到 Gemini App/網頁，一張一張產。"""
    if char == "__lookup__":
        char = characters.get(row)
    rule = characters.image_block(char)
    n = config.IMAGES_PER_PRODUCT
    if source_for(row) == "ai":
        try:
            pts = "；".join(json.loads(row["script"]).get("user_points", [])) or "（無）"
        except ValueError:
            pts = "（無）"
        return [AI_ONLY_PROMPT.format(title=row["title"], description=row["description"][:600] or "（無）", points=pts,
                                      variation=VARIATIONS[i % len(VARIATIONS)], person_rule=rule) for i in range(n)]
    return [IMAGE_PROMPT.format(title=row["title"], variation=VARIATIONS[i % len(VARIATIONS)], person_rule=rule) for i in range(n)]


def ref_files(pid: int) -> list[Path]:
    d = config.REF_DIR / str(pid)
    return sorted(d.glob("*.jpg")) if d.exists() else []


def _manual_only() -> bool:
    """手動模式，或選了 API 供應商但還沒填 key → 不自動產，留在「待產圖」頁讓你手動上傳。"""
    return not providers.configured("image")


def run(conn) -> int:
    """auto：有 key 就自動產圖；失敗（沒額度、模型不開放）不重試，改留在「待產圖」頁讓你手動上傳。
    browser：操控你的 Chrome 用 Gemini 網頁產圖（見 gemini_web.py）。"""
    for row in db.by_status(conn, "sourced"):
        if json.loads(row["ref_images"]) and not ref_files(row["id"]):
            try:
                download_refs(row["id"], json.loads(row["ref_images"]))
            except Exception as e:  # noqa: BLE001
                db.update(conn, row["id"], error=f"refs: {e}")
                conn.commit()
    if config.IMAGE_PROVIDER == "browser":
        from . import gemini_web

        return gemini_web.run(conn, config.DAILY_GEN_CAP - db.images_today(conn))
    n = 0
    cap_left = config.DAILY_GEN_CAP - db.images_today(conn)  # API 費用上限（每日最多處理幾個商品）
    for row in db.by_status(conn, "sourced"):
        if _manual_only() or row["error"].startswith("imagegen:") or not ready_for_gen(row):
            continue
        if cap_left <= 0:
            break
        try:
            imgs = generate_for(conn, row)
        except Exception as e:  # noqa: BLE001
            db.update(conn, row["id"], error=f"imagegen: {str(e)[:300]}")
            conn.commit()
            continue
        db.move(conn, row["id"], "image_review", images=json.dumps(imgs), error="")
        conn.commit()
        n += 1
        cap_left -= 1
    return n
