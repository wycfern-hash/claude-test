"""AI 產圖。賣家圖只當『參考輸入』，輸出必須是全新構圖；賣家原圖不會被上傳或放進影片。"""
import json
from pathlib import Path

import httpx

from . import config, db
from .gemini_client import client

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
    from google.genai import types

    refs = download_refs(row["id"], json.loads(row["ref_images"]))
    if not refs:
        raise RuntimeError("沒有參考圖，請先執行 enrich")
    parts = [types.Part.from_bytes(data=p.read_bytes(), mime_type="image/jpeg") for p in refs]
    out_dir = config.IMG_DIR / str(row["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    c, saved = client(), []
    for n in range(config.IMAGES_PER_PRODUCT):
        prompt = IMAGE_PROMPT.format(title=row["title"], variation=VARIATIONS[n % len(VARIATIONS)])
        resp = c.models.generate_content(model=config.IMAGE_MODEL, contents=[prompt, *parts])
        for part in resp.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                f = out_dir / f"{n}.png"
                f.write_bytes(part.inline_data.data)
                saved.append(str(f.relative_to(config.DATA_DIR)))
                break
    if not saved:
        raise RuntimeError("Gemini 沒有回傳圖片（可能被安全過濾）")
    return saved


def prompts_for(row) -> list[str]:
    """手動模式：把這些提示詞連同參考圖貼到 Gemini App/網頁，一張一張產。"""
    return [IMAGE_PROMPT.format(title=row["title"], variation=v) for v in VARIATIONS[: config.IMAGES_PER_PRODUCT]]


def ref_files(pid: int) -> list[Path]:
    d = config.REF_DIR / str(pid)
    return sorted(d.glob("*.jpg")) if d.exists() else []


def run(conn) -> int:
    n = 0
    if config.IMAGE_PROVIDER == "manual":
        # 不呼叫任何 API，只把賣家參考圖抓下來，讓「待產圖」頁顯示給你
        for row in db.by_status(conn, "sourced"):
            if json.loads(row["ref_images"]) and not ref_files(row["id"]):
                try:
                    download_refs(row["id"], json.loads(row["ref_images"]))
                except Exception as e:  # noqa: BLE001
                    db.update(conn, row["id"], error=f"refs: {e}")
        return 0
    for row in db.by_status(conn, "sourced"):
        try:
            imgs = generate_for(conn, row)
        except Exception as e:  # noqa: BLE001
            db.update(conn, row["id"], error=f"imagegen: {e}")
            conn.commit()
            continue
        db.move(conn, row["id"], "image_review", images=json.dumps(imgs), error="")
        conn.commit()
        n += 1
    return n
