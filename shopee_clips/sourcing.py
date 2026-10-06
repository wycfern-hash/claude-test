"""選品：不用蝦皮 API。三種進件方式：
  1. add / import-csv：你手動貼商品連結（最穩）
  2. fetch-picks：用 Playwright 開你已登入的分潤後台選品頁，抓頁面上的商品連結
商品詳情（標題、價格、圖片）從商品頁的 og meta / JSON-LD 讀，不依賴會改版的 CSS class。
"""
import csv
import json
import re
from urllib.parse import unquote

import httpx

from . import config, db

PRODUCT_LINK_RE = re.compile(r"shopee\.tw/.*(-i\.\d+\.\d+|/product/\d+/\d+)")


def import_urls(conn, urls) -> tuple[int, int]:
    added = dup = 0
    for u in urls:
        u = u.strip()
        if not u or u.startswith("#"):
            continue
        if db.add_product(conn, u) is None:
            dup += 1
        else:
            added += 1
    return added, dup


def import_csv(conn, path: str) -> tuple[int, int]:
    """CSV 欄位：url 必填；title/price 選填。"""
    added = dup = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            pid = db.add_product(conn, row["url"].strip(), row.get("title", ""), row.get("price", ""))
            added, dup = (added + 1, dup) if pid else (added, dup + 1)
    return added, dup


ALIASES = {
    "url": ["商品連結", "商品網址", "商品鏈接", "分潤連結", "推廣連結", "連結", "網址", "url", "link", "product link", "product_url"],
    "title": ["商品名稱", "商品標題", "名稱", "標題", "品名", "title", "name", "product name"],
    "price": ["價格", "售價", "price"],
    "p1": ["賣點1", "賣點一", "賣點 1", "selling point 1"],
    "p2": ["賣點2", "賣點二", "賣點 2", "selling point 2"],
    "p3": ["賣點3", "賣點三", "賣點 3", "selling point 3"],
}
SHORT_HOSTS = ("s.shopee.tw", "shp.ee", "shope.ee", "vn.shp.ee", "s.shopee.com")


def expand_short(url: str) -> str:
    """分潤短連結（s.shopee.tw/xxx）→ 跟著轉址取出含商品 ID 的完整網址。"""
    r = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    return unquote(str(r.url))


def _find_cols(header: list) -> dict:
    cols = {}
    for i, h in enumerate(header):
        h = str(h or "").strip().lower()
        for key, names in ALIASES.items():
            if key not in cols and h in [n.lower() for n in names]:
                cols[key] = i
    return cols


def _cell_url(cell) -> str:
    v = str(cell.value or "").strip()
    if v.startswith("http"):
        return v
    if cell.hyperlink and cell.hyperlink.target:
        return cell.hyperlink.target
    return ""


def import_excel(conn, path: str) -> dict:
    """匯入 Excel（.xlsx）。自動辨識表頭（商品連結/商品名稱/價格/賣點1~3 等），商品連結欄必填；
    支援文字網址、超連結儲存格、分潤短連結。回傳 {added, dup, failed:[(列號, 原因)]}。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    res = {"added": 0, "dup": 0, "failed": []}
    for ws in wb.worksheets:
        rows = list(ws.iter_rows())
        hdr_i = next((i for i, r in enumerate(rows[:10]) if "url" in _find_cols([c.value for c in r])), None)
        if hdr_i is None:
            res["failed"].append((ws.title, "找不到「商品連結」欄（表頭請含：商品連結 / 商品網址 / 連結 / url）"))
            continue
        cols = _find_cols([c.value for c in rows[hdr_i]])
        for r in rows[hdr_i + 1:]:
            url = _cell_url(r[cols["url"]]) if cols["url"] < len(r) else ""
            if not url:
                continue
            get = lambda k: str(r[cols[k]].value or "").strip() if k in cols and cols[k] < len(r) else ""  # noqa: E731
            try:
                if db.shopee_key(url) is None and any(h in url for h in SHORT_HOSTS):
                    url = expand_short(url)
                pid = db.add_product(conn, url, title=get("title"), price=get("price"))
            except Exception as e:  # noqa: BLE001
                res["failed"].append((f"{ws.title} 第{r[0].row}列", str(e)[:120]))
                continue
            if pid is None:
                res["dup"] += 1
                continue
            res["added"] += 1
            pts = [get(k) for k in ("p1", "p2", "p3") if get(k)]
            if pts:
                db.update(conn, pid, script=json.dumps({"user_points": pts}, ensure_ascii=False))
    return res


def login() -> None:
    """開自動化專用 Chrome + Google/Flow/蝦皮分頁；你在裡面手動登入一次即可（帳密不經過程式）。"""
    from . import browser

    browser.open_login_tabs()


def fetch_picks(conn, limit: int = 30) -> tuple[int, int]:
    from . import browser

    if not config.AFFILIATE_PICKS_URL:
        raise SystemExit("請先在 .env 設定 AFFILIATE_PICKS_URL")
    with browser.open_context() as ctx:
        page = ctx.new_page()
        page.goto(config.AFFILIATE_PICKS_URL)
        page.wait_for_load_state("networkidle")
        for _ in range(5):  # 觸發懶載入
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(800)
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        page.close()
    urls = list(dict.fromkeys(h for h in hrefs if PRODUCT_LINK_RE.search(h)))[:limit]
    return import_urls(conn, urls)


def enrich(conn) -> int:
    """替只有網址的商品補標題/價格/參考圖（讀商品頁 og meta + JSON-LD）。"""
    from . import browser

    rows = [r for r in db.by_status(conn, "sourced") if not r["title"] or r["ref_images"] == "[]"]
    if not rows:
        return 0
    n = 0
    with browser.open_context() as ctx:
        page = ctx.new_page()
        for r in rows:
            try:
                page.goto(r["url"])
                page.wait_for_load_state("networkidle")
                info = page.evaluate(_EXTRACT_JS)
            except Exception as e:  # noqa: BLE001
                db.update(conn, r["id"], error=f"enrich: {e}")
                continue
            db.update(
                conn, r["id"],
                title=info["title"] or r["title"], price=info["price"] or r["price"],
                description=info["description"], ref_images=json.dumps(info["images"][:4]), error="",
            )
            n += 1
        page.close()
    return n


_EXTRACT_JS = """() => {
  const meta = n => (document.querySelector(`meta[property="${n}"],meta[name="${n}"]`)||{}).content || '';
  let title = meta('og:title') || document.title, price = '', images = [];
  const og = meta('og:image'); if (og) images.push(og);
  for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
    try { const j = JSON.parse(s.textContent); const o = Array.isArray(j) ? j[0] : j;
      if (o && o.offers) price = (o.offers.price || (o.offers.lowPrice) || '') + '';
      if (o && o.image) images.push(...[].concat(o.image)); } catch (e) {}
  }
  document.querySelectorAll('img[src*="susercontent"]').forEach(i => images.push(i.src.replace(/_tn$/, '')));
  return {title, price, description: meta('og:description'), images: [...new Set(images)]};
}"""
