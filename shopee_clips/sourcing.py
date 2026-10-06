"""選品：不用蝦皮 API。三種進件方式：
  1. add / import-csv：你手動貼商品連結（最穩）
  2. fetch-picks：用 Playwright 開你已登入的分潤後台選品頁，抓頁面上的商品連結
商品詳情（標題、價格、圖片）從商品頁的 og meta / JSON-LD 讀，不依賴會改版的 CSS class。
"""
import csv
import json
import re

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


def _launch(p, headless: bool):
    config.ensure_dirs()
    # 蝦皮有反爬，建議 headed + 持久化 profile（用你自己登入過的瀏覽器狀態）
    return p.chromium.launch_persistent_context(str(config.BROWSER_PROFILE), headless=headless)


def login() -> None:
    """開一個有頭瀏覽器讓你『手動』登入蝦皮；之後的 session 存在 data/browser_profile。"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://shopee.tw")
        # 你在彈出的瀏覽器裡手動登入（分潤後台、短影音後台都登入），登入完把視窗關掉即可
        while ctx.pages:
            try:
                ctx.pages[0].wait_for_event("close", timeout=0)
            except Exception:  # noqa: BLE001
                break
        ctx.close()


def fetch_picks(conn, limit: int = 30) -> tuple[int, int]:
    from playwright.sync_api import sync_playwright

    if not config.AFFILIATE_PICKS_URL:
        raise SystemExit("請先在 .env 設定 AFFILIATE_PICKS_URL")
    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.AFFILIATE_PICKS_URL)
        page.wait_for_load_state("networkidle")
        for _ in range(5):  # 觸發懶載入
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(800)
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        ctx.close()
    urls = list(dict.fromkeys(h for h in hrefs if PRODUCT_LINK_RE.search(h)))[:limit]
    return import_urls(conn, urls)


def enrich(conn) -> int:
    """替只有網址的商品補標題/價格/參考圖（讀商品頁 og meta + JSON-LD）。"""
    from playwright.sync_api import sync_playwright

    rows = [r for r in db.by_status(conn, "sourced") if not r["title"] or r["ref_images"] == "[]"]
    if not rows:
        return 0
    n = 0
    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
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
        ctx.close()
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
