"""「上網找圖」：用你的 Chrome 開 Google / Bing / Yahoo 圖片搜尋，自動收集結果當候選；你在網頁勾選要的，
勾選的圖只會當「AI 產圖的參考」，不會原樣用在影片（避免盜圖）。來源網址會寫進該商品的「來源與授權紀錄」。
找不到圖的商品，改用「AI 生成」即可。注意：自動讀取搜尋結果頁可能違反搜尋引擎的使用條款，請自行評估、控制量。
"""
import base64
import io
import json
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from PIL import Image

from . import config, db

ENGINES = {
    "google": "https://www.google.com/search?tbm=isch&q={q}",
    "bing": "https://www.bing.com/images/search?q={q}",
    "yahoo": "https://tw.images.search.yahoo.com/search/images?p={q}",
}
ENGINE_NAMES = {"google": "Google", "bing": "Bing", "yahoo": "Yahoo"}
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MIN_SIDE = 150

# Bing 的 a.iusc 帶有原圖網址(murl)與縮圖(turl)；其他引擎退而求其次抓頁面上夠大的 <img>
COLLECT_JS = """(limit) => {
  const out = [];
  for (const a of document.querySelectorAll('a.iusc')) {
    try { const m = JSON.parse(a.getAttribute('m')); out.push({full: m.murl || '', thumb: m.turl || ''}); } catch (e) {}
  }
  if (!out.length) for (const i of document.images)
    if (i.naturalWidth >= 120 && i.naturalHeight >= 120) out.push({full: '', thumb: i.currentSrc || i.src});
  return out.slice(0, limit * 2);
}"""


def cand_dir(pid: int) -> Path:
    return config.DATA_DIR / "ref_candidates" / str(pid)


def search_url(engine: str, query: str) -> str:
    return ENGINES.get(engine, ENGINES["google"]).format(q=quote(query))


def _to_jpeg(data: bytes) -> bytes | None:
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if min(im.size) < MIN_SIDE:
            return None
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "JPEG", quality=90)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def _fetch(page, url: str) -> bytes | None:
    if not url:
        return None
    if url.startswith("data:"):
        try:
            return base64.b64decode(url.split(",", 1)[1])
        except Exception:  # noqa: BLE001
            return None
    if url.startswith("blob:"):
        from .webauto import fetch_in_page

        try:
            return fetch_in_page(page, url)
        except Exception:  # noqa: BLE001
            return None
    try:
        r = httpx.get(url, headers=UA, timeout=10, follow_redirects=True)
        return r.content if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def search(ctx, pid: int, engine: str, query: str, limit: int = 20) -> int:
    """搜尋並把候選圖存到 data/ref_candidates/<pid>/；回傳張數。"""
    d = cand_dir(pid)
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    page = ctx.new_page()
    try:
        page.goto(search_url(engine, query))
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)
        for _ in range(3):  # 觸發懶載入
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(700)
        items = page.evaluate(COLLECT_JS, limit)
        meta, n = [], 0
        for it in items:
            if n >= limit:
                break
            data = _to_jpeg(_fetch(page, it["full"]) or b"") or _to_jpeg(_fetch(page, it["thumb"]) or b"")
            if not data:
                continue
            (d / f"c{n}.jpg").write_bytes(data)
            meta.append({"file": f"c{n}.jpg", "source": it["full"] or it["thumb"][:120], "engine": engine})
            n += 1
            time.sleep(0.05)
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        return n
    finally:
        page.close()


def candidates(pid: int) -> list[dict]:
    m = cand_dir(pid) / "meta.json"
    return json.loads(m.read_text(encoding="utf-8")) if m.exists() else []


def _next_pick_index(pid: int) -> int:
    d = config.REF_DIR / str(pid)
    return len(list(d.glob("w*.jpg"))) if d.exists() else 0


def add_picks(conn, pid: int, files: list[str]) -> int:
    """把勾選的候選圖加入該商品的參考圖（w*.jpg），並記下來源。"""
    meta = {m["file"]: m for m in candidates(pid)}
    d = config.REF_DIR / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    notes, n = [], 0
    for f in files:
        src = cand_dir(pid) / Path(f).name
        if Path(f).name not in meta or not src.exists():
            continue
        shutil.copy(src, d / f"w{_next_pick_index(pid)}.jpg")
        notes.append(f"{meta[Path(f).name]['engine']}：{meta[Path(f).name]['source']}")
        n += 1
    if n:
        row = db.get(conn, pid)
        stamp = time.strftime("%Y-%m-%d")
        db.update(conn, pid, ref_notes=(row["ref_notes"] + "\n" if row["ref_notes"] else "") + "\n".join(f"[{stamp}] 上網找圖 {x}" for x in notes))
    return n


def add_uploaded(conn, pid: int, blobs: list[tuple[str, bytes]]) -> int:
    """你自己找的圖（從別處存下來）直接上傳當參考。"""
    d = config.REF_DIR / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    n = 0
    for name, data in blobs:
        jpg = _to_jpeg(data)
        if jpg:
            (d / f"w{_next_pick_index(pid)}.jpg").write_bytes(jpg)
            n += 1
    if n:
        row = db.get(conn, pid)
        db.update(conn, pid, ref_notes=(row["ref_notes"] + "\n" if row["ref_notes"] else "") + f"[{time.strftime('%Y-%m-%d')}] 自行上傳 {n} 張參考圖")
    return n
