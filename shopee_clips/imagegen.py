"""AI 產圖。賣家圖只當『參考輸入』，輸出必須是全新構圖；賣家原圖不會被上傳或放進影片。"""
import json
from pathlib import Path

import httpx

from . import config, db
from . import providers

IMAGE_PROMPT = """請參考附圖中的商品，重新生成一張全新的商品形象照（9:16 直式）。
規則：
- 商品本體的外型、顏色、材質、功能特徵必須與參考圖一致，不可誇大或改變功能。
- 嚴禁複製參考圖的構圖、背景、道具、模特兒、浮水印、賣場 logo 與疊字；請換新的拍攝角度與乾淨的新場景。
- 畫面中不要出現任何文字、浮水印、價格標籤，不要出現清楚的人臉。
- 風格：明亮、乾淨、有質感的生活情境或棚拍，適合短影音開場畫面。
商品名稱：{title}
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


def generate_for(conn, row) -> list[str]:
    refs = download_refs(row["id"], json.loads(row["ref_images"]))
    if not refs:
        raise RuntimeError("沒有參考圖，請先執行 enrich")
    out_dir = config.IMG_DIR / str(row["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = []
    for n in range(config.IMAGES_PER_PRODUCT):
        prompt = IMAGE_PROMPT.format(title=row["title"], variation=VARIATIONS[n % len(VARIATIONS)])
        f = out_dir / f"{n}.png"
        f.write_bytes(providers.image_bytes(prompt, refs))
        saved.append(str(f.relative_to(config.DATA_DIR)))
    return saved


def prompts_for(row) -> list[str]:
    """手動模式：把這些提示詞連同參考圖貼到 Gemini App/網頁，一張一張產。"""
    return [IMAGE_PROMPT.format(title=row["title"], variation=v) for v in VARIATIONS[: config.IMAGES_PER_PRODUCT]]


def ref_files(pid: int) -> list[Path]:
    d = config.REF_DIR / str(pid)
    return sorted(d.glob("*.jpg")) if d.exists() else []


def _manual_only() -> bool:
    """手動模式，或選了 API 供應商但還沒填 key → 不自動產，留在「待產圖」頁讓你手動上傳。"""
    return config.IMAGE_PROVIDER == "manual" or not providers.configured("image")


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
    for row in db.by_status(conn, "sourced"):
        if _manual_only() or row["error"].startswith("imagegen:"):
            continue
        try:
            imgs = generate_for(conn, row)
        except Exception as e:  # noqa: BLE001
            db.update(conn, row["id"], error=f"imagegen: {str(e)[:300]}")
            conn.commit()
            continue
        db.move(conn, row["id"], "image_review", images=json.dumps(imgs), error="")
        conn.commit()
        n += 1
    return n
