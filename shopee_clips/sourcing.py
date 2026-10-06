"""選品：不用蝦皮 API。三種進件方式：
  1. 匯入蝦皮分潤後台下載的 CSV（或 Excel）／手動貼商品連結（最穩）
  2. fetch-picks：用 Playwright 開你已登入的分潤後台選品頁，抓頁面上的商品連結
商品詳情（標題、價格、圖片）從商品頁的 og meta / JSON-LD 讀，不依賴會改版的 CSS class。
"""
import csv
import io
import json
import re
from pathlib import Path
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


ALIASES = {
    "url": ["商品連結", "商品網址", "商品鏈接", "商品頁連結", "商品頁網址", "連結", "網址", "url", "link", "product link", "product url",
            "product_url", "item url"],
    "aff": ["分潤連結", "推廣連結", "聯盟連結", "推廣短連結", "短連結", "短網址", "affiliate link", "offer link", "affiliate url", "tracking link"],
    "title": ["商品名稱", "商品標題", "名稱", "標題", "品名", "title", "name", "product name", "item name"],
    "price": ["價格", "售價", "商品價格", "price"],
    "desc": ["商品描述", "商品說明", "描述", "簡介", "description"],
    "img": ["商品圖片", "商品圖", "圖片", "圖片連結", "圖片網址", "主圖", "封面", "image", "image url", "image_url", "image link", "picture"],
    "item": ["商品id", "商品編號", "item id", "itemid", "item_id", "product id", "productid"],
    "shop": ["店鋪id", "商店id", "賣場id", "店鋪編號", "shop id", "shopid", "shop_id", "seller id"],
    "char": ["主角", "角色", "人物", "presenter", "character"],
    "src": ["圖片來源", "image source"],
    "p1": ["賣點1", "賣點一", "賣點 1", "selling point 1"],
    "p2": ["賣點2", "賣點二", "賣點 2", "selling point 2"],
    "p3": ["賣點3", "賣點三", "賣點 3", "selling point 3"],
}
SHORT_HOSTS = ("s.shopee.tw", "shp.ee", "shope.ee", "vn.shp.ee", "s.shopee.com")
Cell = tuple  # (顯示文字, 超連結目標)；CSV 沒有超連結，目標為空字串


def expand_short(url: str) -> str:
    """分潤短連結（s.shopee.tw/xxx）→ 跟著轉址取出含商品 ID 的完整網址。"""
    r = httpx.get(url, follow_redirects=True, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    return unquote(str(r.url))


def _norm(h) -> str:
    h = re.sub(r"[（(].*?[）)]", "", str(h or "")).lower()  # 「商品名稱(必填)」→「商品名稱」
    return re.sub(r"[\s_\-:：*＊]+", "", h)


def _find_cols(header: list) -> dict:
    """先精確比對表頭；再用「含有」比對（只限含中文且 4 字以上的別名，避免 shop name 被當成 name）。"""
    norm = [_norm(h) for h in header]
    cols: dict = {}
    for exact in (True, False):
        for key, names in ALIASES.items():
            if key in cols:
                continue
            ns = [_norm(n) for n in names if exact or (len(_norm(n)) >= 4 and re.search(r"[^\x00-\x7f]", n))]
            for i, h in enumerate(norm):
                if h and i not in cols.values() and ((h in ns) if exact else any(n in h for n in ns)):
                    cols[key] = i
                    break
    return cols


def _url_of(cell: Cell) -> str:
    v, link = (str(cell[0] or "").strip(), cell[1] or "")
    if v.lower().startswith("http"):
        return v
    m = re.search(r'HYPERLINK\(\s*"([^"]+)"', v, re.I)  # Excel 匯出的 =HYPERLINK("網址","文字")
    if m:
        return m.group(1)
    if link:
        return link
    if re.fullmatch(r"(?:[\w-]+\.)+[a-z]{2,}/\S+", v, re.I) and ("shopee" in v or "shp.ee" in v):
        return "https://" + v
    return ""


def _num(v: str) -> str:
    v = str(v or "").strip()
    return v[:-2] if re.fullmatch(r"\d+\.0", v) else v


def _has_link_source(cols: dict) -> bool:
    return "url" in cols or "aff" in cols or ("item" in cols and "shop" in cols)


def _guess_url_col(rows: list[list[Cell]], hdr_i: int) -> int | None:
    """表頭認不得時，看內容：哪一欄多半是蝦皮連結。"""
    width = max((len(r) for r in rows), default=0)
    for c in range(width):
        vals = [_url_of(r[c]) for r in rows[hdr_i + 1:] if c < len(r) and str(r[c][0] or "").strip()]
        if vals and sum(1 for v in vals if "shopee" in v or any(h in v for h in SHORT_HOSTS)) / len(vals) >= 0.5:
            return c
    return None


def import_table(conn, rows: list[list[Cell]], label: str = "") -> dict:
    """匯入一張表（Excel 工作表或 CSV）。自動辨識表頭；支援商品連結 / 分潤連結 / 商品ID+店鋪ID；
    也會用內容猜哪一欄是連結。回傳 {added, dup, failed:[(位置, 原因)]}。"""
    res = {"added": 0, "dup": 0, "failed": []}
    rows = [r for r in rows if any(str(c[0] or "").strip() or c[1] for c in r)]
    where = f"{label} " if label else ""
    hdr_i, cols = None, {}
    for i, r in enumerate(rows[:10]):
        c = _find_cols([x[0] for x in r])
        if _has_link_source(c):
            hdr_i, cols = i, c
            break
    if hdr_i is None and rows:  # 表頭認不得：第一列當表頭，用內容猜連結欄
        c = _find_cols([x[0] for x in rows[0]])
        guess = _guess_url_col(rows, 0)
        if guess is not None:
            hdr_i, cols = 0, {**c, "url": guess}
    if hdr_i is None:
        heads = "、".join(str(x[0]) for x in (rows[0] if rows else []) if str(x[0]).strip())[:200]
        res["failed"].append((label or "檔案", f"找不到商品連結欄。你的檔案欄位是：{heads or '（空白）'}。請確認有「商品連結」（或「商品ID」+「店鋪ID」）欄位"))
        return res
    by_name = {c["name"]: c["id"] for c in db.list_characters(conn)}
    for n, r in enumerate(rows[hdr_i + 1:], hdr_i + 2):
        def cell(k):
            return r[cols[k]] if k in cols and cols[k] < len(r) else ("", "")

        def get(k):
            return _num(cell(k)[0]) if k in ("item", "shop") else str(cell(k)[0] or "").strip()

        purl, aurl = (_url_of(cell("url")) if "url" in cols else ""), (_url_of(cell("aff")) if "aff" in cols else "")
        ids_url = f"https://shopee.tw/product/{get('shop')}/{get('item')}" if get("shop") and get("item") else ""
        url = purl or aurl or ids_url
        if not url:
            res["failed"].append((f"{where}第{n}列", "這一列沒有商品連結（也沒有商品ID+店鋪ID）"))
            continue
        original = aurl or purl  # 上架標記商品時優先用分潤連結
        try:
            if db.shopee_key(url) is None and any(h in url for h in SHORT_HOSTS):
                try:
                    url = expand_short(url)
                except Exception:  # noqa: BLE001
                    if not ids_url:
                        raise
                    url = ids_url
            imgs = [u for u in re.split(r"[\s,;|]+", get("img")) if u.startswith("http")][:6]
            pid = db.add_product(conn, url, title=get("title"), price=get("price"), description=get("desc"),
                                 ref_images=imgs, source_url=original)
        except Exception as e:  # noqa: BLE001
            res["failed"].append((f"{where}第{n}列", str(e)[:120]))
            continue
        if pid is None:
            res["dup"] += 1
            continue
        res["added"] += 1
        if get("char") in by_name:  # 「主角」欄填的名稱對得上，就套用
            db.update(conn, pid, character_id=by_name[get("char")])
        if get("src") in ("web", "ai", "上網找", "AI 生成", "AI"):
            db.update(conn, pid, image_source="ai" if "AI" in get("src") or get("src") == "ai" else "web")
        pts = [get(k) for k in ("p1", "p2", "p3") if get(k)]
        if pts:
            db.update(conn, pid, script=json.dumps({"user_points": pts}, ensure_ascii=False))
    return res


def _merge(total: dict, part: dict) -> dict:
    total["added"] += part["added"]
    total["dup"] += part["dup"]
    total["failed"] += part["failed"]
    return total


def decode_text(b: bytes) -> str:
    """蝦皮/Excel 匯出的 CSV 可能是 UTF-8（含 BOM）、UTF-16、或繁中 Big5(cp950)。"""
    if b.startswith((b"\xff\xfe", b"\xfe\xff")):
        return b.decode("utf-16")
    for enc in ("utf-8-sig", "cp950", "gb18030"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace")


def read_csv(path: str) -> list[list[Cell]]:
    text = decode_text(Path(path).read_bytes())
    lines = text.splitlines()
    if lines and lines[0].lower().startswith("sep=") and len(lines[0]) <= 6:  # Excel 的 sep=, 第一行
        text, lines = "\n".join(lines[1:]), lines[1:]
    sample = "\n".join(lines[:20])
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",\t;|").delimiter
    except csv.Error:
        first = lines[0] if lines else ""
        delim = max(",\t;|", key=first.count)
    return [[(c, "") for c in row] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def import_csv(conn, path: str) -> dict:
    """匯入 CSV（蝦皮分潤後台下載的檔案）。回傳 {added, dup, failed}。"""
    return import_table(conn, read_csv(path))


def import_excel(conn, path: str) -> dict:
    """匯入 Excel（.xlsx）。每個工作表各自辨識表頭；支援文字網址與超連結儲存格。"""
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    res = {"added": 0, "dup": 0, "failed": []}
    for ws in wb.worksheets:
        rows = [[(c.value if c.value is not None else "", c.hyperlink.target if c.hyperlink and c.hyperlink.target else "")
                 for c in r] for r in ws.iter_rows()]
        _merge(res, import_table(conn, rows, ws.title if len(wb.worksheets) > 1 else ""))
    return res


def import_file(conn, path: str) -> dict:
    """依副檔名匯入：.csv / .tsv / .txt → CSV；.xlsx / .xlsm → Excel。"""
    ext = Path(path).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        return import_excel(conn, path)
    if ext in (".csv", ".tsv", ".txt"):
        return import_csv(conn, path)
    if ext == ".xls":
        return {"added": 0, "dup": 0, "failed": [(Path(path).name, "不支援舊版 .xls，請用 Excel 另存成 .xlsx 或 .csv")]}
    return {"added": 0, "dup": 0, "failed": [(Path(path).name, f"不認得的檔案類型 {ext or '(無副檔名)'}，請用 .csv 或 .xlsx")]}


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


def needs_enrich(r) -> bool:
    """要不要去商品頁補資料。試過就不再重試（避免每分鐘重開瀏覽器）；純 AI 生成且已有標題的商品不需要。"""
    if r["error"].startswith("enrich"):
        return False
    if r["title"] and (r["image_source"] or config.DEFAULT_IMAGE_SOURCE) == "ai":
        return False
    return not r["title"] or r["ref_images"] == "[]"


def enrich(conn) -> int:
    """替只有網址的商品補標題/價格/參考圖（讀商品頁 og meta + JSON-LD）。"""
    from . import browser

    rows = [r for r in db.by_status(conn, "sourced") if needs_enrich(r)]
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
                description=info["description"], ref_images=json.dumps(info["images"][:4]),
                error="" if info["images"] else "enrich: 商品頁沒抓到圖片（請「上網找圖」或改用純 AI 生成）",
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
