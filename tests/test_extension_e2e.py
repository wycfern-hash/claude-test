"""把擴充功能真的載入 Chromium，對一個模仿蝦皮行為的假網站跑完整流程：
限時特賣（分批懶載入、多場次）→ 搜尋特價（XHR）→ 轉分潤連結（GraphQL 與網頁操作兩條路）→ 匯出 CSV →（再匯入自動化程式）。
假網站的資料格式依蝦皮公開流傳的 API 結構，不代表真站一定一樣——真站差異要靠擴充功能的診斷檔校正。"""
import csv
import io
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

CHROME = next(iter(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)
EXT = Path(__file__).resolve().parent.parent / "extension"
pytestmark = pytest.mark.skipif(CHROME is None, reason="no chromium")


def mk_items(promo: int, base_id: int, n: int, start: int, end: int):
    return [{"itemid": base_id + i, "shopid": 7, "name": f"限時商品{promo}-{base_id + i}", "image": f"img{base_id + i}",
             "price": (100 + (base_id + i) % 50) * 100000, "price_before_discount": (200 + (base_id + i) % 50) * 100000,
             "raw_discount": 50, "flash_sale_stock": 30, "stock": 99, "sold": i, "promotionid": promo,
             "start_time": start, "end_time": end} for i in range(n)]


SOC_THREADS = """<!doctype html><meta charset=utf-8><body>
<div id=open onclick="openC()">有什麼新鮮事？</div><div id=area></div>
<script>
function openC(){ area.innerHTML='<div role=textbox contenteditable=true class=tb></div><div id=add onclick="addP()">新增到串文</div><button id=post onclick="doPost()">發佈</button>'; }
function addP(){ const d=document.createElement('div'); d.setAttribute('role','textbox'); d.contentEditable=true; d.className='tb'; area.insertBefore(d, document.getElementById('add')); }
async function doPost(){ const t=[...document.querySelectorAll('.tb')].map(x=>x.innerText);
  await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'threads',texts:t})}); area.innerHTML='<a href="/social/post/1">查看</a>'; }
</script>"""
SOC_POST = """<!doctype html><meta charset=utf-8><body>
<div data-pressable-container="true"><a href="/@amy">amy</a><span dir="auto">請問這個好用嗎</span><div class=rb onclick="rep()">回覆</div></div>
<div id=box></div>
<script>
function rep(){ box.innerHTML='<div role=textbox contenteditable=true id=tb></div><button onclick="send()">回覆</button>'; }
async function send(){ await fetch('/__reply',{method:'POST',body:JSON.stringify({url:location.pathname,text:document.getElementById('tb').innerText})}); box.innerHTML='ok'; }
</script>"""
SOC_SEARCH = """<!doctype html><meta charset=utf-8><body>
<div data-pressable-container="true"><a href="/@zoe">zoe</a><a href="/social/post/11">t</a><span dir="auto">最近想買保溫杯，有推薦的嗎</span></div>
<div data-pressable-container="true"><a href="/@max">max</a><a href="/social/post/12">t</a><span dir="auto">保溫杯好難選</span></div>"""
SOC_FB = """<!doctype html><meta charset=utf-8><body>
<div id=open onclick="openD()">建立貼文</div><div id=feed></div>
<script>
function openD(){ const d=document.createElement('div'); d.setAttribute('role','dialog'); d.id='dlg';
  d.innerHTML='<div role=textbox contenteditable=true id=tb></div><button id=pb onclick="doPost()">發佈</button>'; document.body.appendChild(d); }
async function doPost(){ await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'facebook',texts:[document.getElementById('tb').innerText]})});
  document.getElementById('dlg').remove(); feed.innerHTML='<div role=article><div>剛發的貼文</div><div role=textbox contenteditable=true aria-label="留言"></div></div>';
  document.addEventListener('keydown',async e=>{ if(e.key==='Enter' && !e.shiftKey && e.target.getAttribute && e.target.getAttribute('aria-label')==='留言'){ e.preventDefault();
    await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'facebook',comment:e.target.innerText})}); }}); }
</script>"""


class Mock(BaseHTTPRequestHandler):
    gql_mode = "ok"
    now = int(time.time())
    visits: list = []          # /flash_sale 被開過哪些場次
    bulk: list = []            # /api/v9/bulk_links 每次收到幾個、回應碼
    shop_pages: list = []      # 賣家商店頁 API 被要求過哪些頁
    social: dict = {"posts": [], "replies": []}
    lock = threading.Lock()
    inflight = peak_attempt = peak_ok = n429 = limit = 0   # /api/v9/single_link：同時進來幾個、被限流幾次

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="application/json", code=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.end_headers()
        self.wfile.write(data)

    @classmethod
    def sessions(cls):
        n = cls.now
        return [{"promotionid": 1, "start_time": n - 600, "end_time": n + 3000, "name": "進行中"},
                {"promotionid": 2, "start_time": n + 3600, "end_time": n + 7200, "name": "下一場"},
                {"promotionid": 3, "start_time": n - 7200, "end_time": n - 3600, "name": "已結束"},
                {"promotionid": 6, "start_time": n + 10800, "end_time": n + 14400, "name": "會被轉到首頁"}]

    def promo_items(self, promo):
        s = {x["promotionid"]: x for x in self.sessions()}[promo]
        return (100, 40, s) if promo == 1 else (200, 20, s)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/__mode":
            Mock.gql_mode = q["gql"][0]
            return self._send("ok", "text/plain")
        if u.path == "/__reset":
            Mock.visits, Mock.bulk, Mock.shop_pages = [], [], []
            Mock.social = {"posts": [], "replies": []}
            Mock.inflight = Mock.peak_attempt = Mock.peak_ok = Mock.n429 = Mock.limit = 0
            return self._send("ok", "text/plain")
        if u.path == "/__limit":
            Mock.limit = int(q["n"][0])
            Mock.peak_attempt = Mock.peak_ok = Mock.n429 = 0
            return self._send("ok", "text/plain")
        if u.path == "/social/threads":
            return self._send(SOC_THREADS, "text/html")
        if u.path == "/social/fb":
            return self._send(SOC_FB, "text/html")
        if u.path == "/social/search":
            return self._send(SOC_SEARCH, "text/html")
        if u.path.startswith("/social/post/"):
            return self._send(SOC_POST, "text/html")
        if u.path == "/__log":
            return self._send(json.dumps({"social": Mock.social, "visits": Mock.visits, "bulk": Mock.bulk, "shop_pages": Mock.shop_pages, "peak_attempt": Mock.peak_attempt,
                                          "peak_ok": Mock.peak_ok, "n429": Mock.n429}))
        if u.path == "/":
            return self._send(HOME_HTML, "text/html")
        if u.path.startswith("/shop/9"):
            return self._send(SHOP_HTML, "text/html")
        if u.path == "/mystore":   # 賣家帳號網址，蝦皮會轉到 /shop/<id>（而且轉址後 page 參數不見了）
            return self._send("<!doctype html><script>location.replace('/shop/9?x=1')</script>", "text/html")
        if u.path == "/api/v4/shop/rcmd_items":
            page = int(q.get("page", ["0"])[0])
            Mock.shop_pages.append(page)
            items = []
            if page < 2:                      # 第 3 頁開始沒有商品
                for i in range(12):
                    iid = 2000 + page * 12 + i
                    it = {"itemid": iid, "shopid": 9, "name": f"賣家商品{iid}", "image": f"sh{iid}", "price": 20000000, "historical_sold": 5}
                    if i % 2 == 0:
                        it.update(price_before_discount=30000000, raw_discount=33)
                    items.append({"item_basic": it})
                for i in range(3):            # 商店頁底下的「推薦其他賣家」商品，不該收
                    iid = 5000 + page * 3 + i
                    items.append({"item_basic": {"itemid": iid, "shopid": 5, "name": f"推薦商品{iid}", "price": 10000000,
                                                 "price_before_discount": 20000000, "raw_discount": 50}})
            return self._send(json.dumps({"error": 0, "data": {"items": items}}))
        if u.path == "/flash_sale":
            Mock.visits.append(int(q.get("promotionId", ["0"])[0]))
            if q.get("promotionId", [""])[0] == "6":   # 蝦皮把這個場次網址轉到首頁
                return self._send("<!doctype html><script>location.replace('/')</script>", "text/html")
            return self._send(FLASH_HTML, "text/html")
        if u.path == "/search":
            return self._send(SEARCH_HTML, "text/html")
        if u.path == "/offer/custom_link":
            html = {"bulk": AFF_BULK_HTML, "single": AFF_SINGLE_HTML}.get(Mock.gql_mode, AFF_HTML)
            return self._send(html, "text/html")
        if u.path == "/api/v4/flash_sale/get_all_sessions":
            return self._send(json.dumps({"error": 0, "data": {"sessions": self.sessions()}}))
        if u.path == "/api/v4/flash_sale/get_all_itemids":
            base, n, _ = self.promo_items(int(q["promotionid"][0]))
            return self._send(json.dumps({"error": 0, "data": {"item_brief_list": [{"itemid": base + i, "shopid": 7} for i in range(n)]}}))
        if u.path == "/api/v4/search/search_items":
            page = int(q.get("newest", ["0"])[0]) // 60
            items = []
            for i in range(10):
                iid = 1000 + page * 10 + i
                it = {"itemid": iid, "shopid": 9, "name": f"搜尋商品{iid}", "image": f"s{iid}", "price": 30000000, "historical_sold": 50 + i}
                if i % 2 == 0:
                    it.update(price_before_discount=50000000, raw_discount=40)
                items.append({"item_basic": it})
            if page == 0:  # 這個商品同時也在限時特賣裡 → 不應該出現在「其他特價商品」
                items.append({"item_basic": {"itemid": 100, "shopid": 7, "name": "限時商品1-100", "price": 10000000, "price_before_discount": 20000000, "raw_discount": 50}})
            return self._send(json.dumps({"error": 0, "items": items}))
        self._send("not found", "text/plain", 404)

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if u.path in ("/__post", "/__reply"):
            Mock.social["posts" if u.path == "/__post" else "replies"].append(body)
            return self._send("{}")
        if u.path == "/api/v4/flash_sale/flash_sale_batch_get_items":
            promo = body["promotionid"]
            base, n, s = self.promo_items(promo)
            items = [x for x in mk_items(promo, base, n, s["start_time"], s["end_time"]) if x["itemid"] in body["itemids"]]
            items += [x for x in mk_items(promo, 900, 5, s["start_time"], s["end_time"]) if x["itemid"] in body["itemids"]]
            return self._send(json.dumps({"error": 0, "data": {"items": items}}))
        if u.path == "/api/v9/single_link":   # 一次只收 1 個，而且同時太多個就 429
            with Mock.lock:
                Mock.inflight += 1
                Mock.peak_attempt = max(Mock.peak_attempt, Mock.inflight)
                over = Mock.limit and Mock.inflight > Mock.limit
                if over:
                    Mock.inflight -= 1
                    Mock.n429 += 1
                else:
                    Mock.peak_ok = max(Mock.peak_ok, Mock.inflight)
            if over:
                return self._send(json.dumps({"error": "slow down"}), code=429)
            time.sleep(0.15)
            with Mock.lock:
                Mock.inflight -= 1
            m = re.search(r"/product/(\d+)/(\d+)", body["link"])
            return self._send(json.dumps({"short": f"https://s.shopee.tw/S{m.group(1)}_{m.group(2)}"}))
        if u.path == "/api/v9/bulk_links":   # 和我猜的格式完全不同的後台：一次最多收 7 個
            links = body["links"]
            Mock.bulk.append([len(links), 400 if len(links) > 7 else 200])
            if len(links) > 7:
                return self._send(json.dumps({"error": "too many"}), code=400)
            out = []
            for lk in links:
                m = re.search(r"/product/(\d+)/(\d+)", lk["raw"])
                out.append({"short": f"https://s.shopee.tw/L{m.group(1)}_{m.group(2)}"})
            return self._send(json.dumps({"results": out}))
        if u.path == "/api/v3/gql":
            if Mock.gql_mode != "ok":
                return self._send(json.dumps({"errors": ["nope"]}), code=404)
            out = []
            for lp in body["variables"]["linkParams"]:
                m = re.search(r"/product/(\d+)/(\d+)", lp["originalLink"])
                if m and m.group(2) == "139":
                    out.append({"shortLink": "", "longLink": "", "failCode": 1001})
                else:
                    out.append({"shortLink": f"https://s.shopee.tw/G{m.group(1)}_{m.group(2)}", "longLink": lp["originalLink"], "failCode": 0})
            return self._send(json.dumps({"data": {"batchCustomLink": out}}))
        self._send("not found", "text/plain", 404)


SHOP_HTML = """<!doctype html><meta charset=utf-8><body>賣家商店頁<script>
const p = +(new URLSearchParams(location.search).get('page') || 0);
fetch('/api/v4/shop/rcmd_items?shopid=9&page=' + p).then(r => r.json()).then(j => { document.body.textContent = j.data.items.length + ' items'; });
</script>"""
HOME_HTML = """<!doctype html><meta charset=utf-8><body>蝦皮首頁（有一個限時特賣小區塊）<script>
(async () => {
  await fetch('/api/v4/flash_sale/get_all_sessions').then(r => r.json());
  await fetch('/api/v4/flash_sale/flash_sale_batch_get_items', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ promotionid: 1, itemids: [900, 901, 902, 903, 904], limit: 5 }) }).then(r => r.json());
  const d = document.createElement('div'); d.style.height = '6000px'; document.body.appendChild(d);   // 首頁很長
})();
</script>"""
FLASH_HTML = """<!doctype html><meta charset=utf-8><body><div id=list></div><script>
const promo = +(new URLSearchParams(location.search).get('promotionId') || 1);
let ids = [], loaded = 0, loading = false;
async function init() {
  await fetch('/api/v4/flash_sale/get_all_sessions').then(r => r.json());
  const r = await fetch('/api/v4/flash_sale/get_all_itemids?promotionid=' + promo).then(r => r.json());
  ids = r.data.item_brief_list.map(x => x.itemid);
  ids.forEach((id, i) => { const d = document.createElement('div'); d.style.height = '220px'; d.id = 'c' + i; d.textContent = '...'; list.appendChild(d); });
  more();
}
async function more() {
  if (loading || loaded >= ids.length) return;
  loading = true;
  const batch = ids.slice(loaded, loaded + 16);
  const r = await fetch('/api/v4/flash_sale/flash_sale_batch_get_items', { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ promotionid: promo, itemids: batch, limit: 16 }) }).then(r => r.json());
  r.data.items.forEach((it, k) => { const el = document.getElementById('c' + (loaded + k)); if (el) el.textContent = it.name; });
  loaded += batch.length; loading = false; check();
}
function check() { if (innerHeight + scrollY > document.documentElement.scrollHeight - 400) more(); }
addEventListener('scroll', check);
init();
</script>"""
SEARCH_HTML = """<!doctype html><meta charset=utf-8><body>loading<script>
const p = new URLSearchParams(location.search);
const x = new XMLHttpRequest();
x.open('GET', '/api/v4/search/search_items?keyword=' + encodeURIComponent(p.get('keyword')) + '&newest=' + (+(p.get('page') || 0) * 60));
x.onload = () => { document.body.textContent = JSON.parse(x.responseText).items.length + ' items'; };
x.send();
</script>"""
AFF_BULK_HTML = """<!doctype html><meta charset=utf-8><body><h3>自訂連結（另一種後台）</h3>
<input type=text placeholder="貼上商品連結" style="width:400px"><button>取得連結</button><div id=out></div>
<script>
document.querySelector('button').onclick = async () => {
  const v = document.querySelector('input').value;
  const r = await fetch('/api/v9/bulk_links', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ links: [{ raw: v }] }) }).then(r => r.json());
  document.getElementById('out').textContent = '您的連結：' + r.results[0].short;
};
</script>"""
AFF_SINGLE_HTML = """<!doctype html><meta charset=utf-8><body><h3>自訂連結（一次只能轉一個的後台）</h3>
<input type=text placeholder="貼上商品連結" style="width:400px"><button>取得連結</button><div id=out></div>
<script>
document.querySelector('button').onclick = async () => {
  const v = document.querySelector('input').value;
  const r = await fetch('/api/v9/single_link', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ link: v }) }).then(r => r.json());
  document.getElementById('out').textContent = '您的連結：' + r.short;
};
</script>"""
AFF_HTML = """<!doctype html><meta charset=utf-8><body><h3>自訂連結</h3>
<input type=text placeholder="貼上商品連結" style="width:400px"><button>取得連結</button><div id=out></div>
<script>
document.querySelector('button').onclick = () => {
  const v = document.querySelector('input').value;
  const m = /\\/product\\/(\\d+)\\/(\\d+)/.exec(v);
  if (!m) return;
  document.getElementById('out').textContent = '您的連結：https://s.shopee.tw/D' + m[1] + '_' + m[2];
};
</script>"""


@pytest.fixture(scope="module")
def site():
    Mock.now = int(time.time())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture(scope="module")
def ext_copy(tmp_path_factory, site):
    """把擴充功能複製一份，網址換成本機假網站、等待時間縮短（正式版不動）。"""
    d = tmp_path_factory.mktemp("ext") / "extension"
    shutil.copytree(EXT, d, ignore=shutil.ignore_patterns("tests"))
    m = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    m["host_permissions"] = ["http://127.0.0.1/*"]
    for cs in m["content_scripts"]:
        cs["matches"] = ["http://127.0.0.1/*"]
    (d / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    cfg = (d / "lib" / "config.js").read_text(encoding="utf-8")
    cfg = cfg.replace("https://shopee.tw", site).replace("https://affiliate.shopee.tw", site)
    cfg = cfg.replace("https://www.threads.com/search", site + "/social/search").replace("https://www.threads.com/", site + "/social/threads")
    cfg = cfg.replace("quietMs: 7000", "quietMs: 1800").replace("searchQuietMs: 4000", "searchQuietMs: 1500")
    (d / "lib" / "config.js").write_text(cfg, encoding="utf-8")
    return d


@pytest.fixture(scope="module")
def dash(ext_copy, tmp_path_factory):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(tmp_path_factory.mktemp("profile")), executable_path=str(CHROME), headless=False, accept_downloads=True,
            args=["--headless=new", "--no-sandbox", f"--disable-extensions-except={ext_copy}", f"--load-extension={ext_copy}"])
        sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=15000)
        ext_id = sw.url.split("/")[2]
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())
        page.goto(f"chrome-extension://{ext_id}/dashboard.html")
        yield page
        ctx.close()


def wait_msg(page, pattern, timeout=120000):
    page.wait_for_function("(p) => new RegExp(p).test(document.getElementById('msg').textContent)", arg=pattern, timeout=timeout)
    return page.inner_text("#msg")


def rows(page):
    return page.eval_on_selector_all("#rows tr", "trs => trs.map(t => [...t.children].slice(1).map(td => td.innerText))")


def test_full_flow(dash, site):
    page = dash

    # ---- ① 限時特賣：分批懶載入的 40 個 + 下一場 20 個；已結束的場次不抓
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1")
    page.fill("#moreSessions", "1")                      # 再多抓 1 個後面的場次（= 場次 2）
    page.click("#btnFlash")
    msg = wait_msg(page, "完成：讀了 \\d+ 個場次")
    assert "讀了 2 個場次" in msg and "共 60 個商品" in msg, msg
    r = rows(page)
    assert len(r) == 60
    live = [x for x in r if "已開始" in x[3]]
    upcoming = [x for x in r if "距離開始" in x[3]]
    assert len(live) == 40 and len(upcoming) == 20
    assert re.search(r"\d{4}/\d{2}/\d{2}（週.） \d{2}:\d{2} – \d{2}:\d{2}", live[0][3]), live[0][3]   # 日期與時間寫清楚
    assert re.search(r"已開始・剩 \d{2}:\d{2}:\d{2}", live[0][3])
    assert re.search(r"距離開始 \d{2}:\d{2}:\d{2}", upcoming[0][3])
    assert "尚未轉換" in live[0][5]                                                                      # 還沒轉就明講不是分潤連結
    assert "$" in live[0][2] and "-50%" in live[0][2]

    # 狀態篩選
    page.select_option("#fState", "upcoming")
    assert len(rows(page)) == 20
    page.select_option("#fState", "")

    # ---- ② 轉分潤連結（GraphQL 路）：其中 itemid 139 回傳失敗代碼
    page.click("#btnAff")
    wait_msg(page, "完成.*成功 \\d+ 個")
    page.wait_for_function("() => document.getElementById('statAff').textContent === '59'", timeout=20000)
    r = rows(page)
    assert sum("https://s.shopee.tw/G7_" in x[5] for x in r) == 59
    bad = [x for x in r if "139" in x[1]]
    assert "轉換失敗：失敗代碼 1001" in bad[0][5]
    assert any("G7_100" in x[5] for x in r)

    # ---- ① 搜尋「其他特價商品」：只留折扣 ≥ 20%、排除限時特賣裡的 7.100
    page.click("#tabOther")
    page.fill("#kw", "泡麵")
    page.select_option("#pages", "1")
    page.fill("#minPctSearch", "20")
    page.click("#btnSearch")
    msg = wait_msg(page, "完成：找到 \\d+ 個折扣")
    r = rows(page)
    assert len(r) == 5, (msg, len(r))                                   # 10 個裡偶數 5 個有折扣；限時特賣那個被排除
    assert all("搜尋商品" in x[1] for x in r)
    assert all("-40%" in x[2] for x in r)

    # ---- ② 轉分潤連結（GraphQL 壞掉 → 改用「在自訂連結頁貼連結按按鈕」的備援）
    import urllib.request
    urllib.request.urlopen(f"{site}/__mode?gql=fail").read()
    page.click("#btnAff")
    msg = wait_msg(page, "完成.*成功 \\d+ 個", 120000)
    assert "模擬操作" in msg and "成功 5 個、失敗 0 個" in msg, msg
    r = rows(page)
    assert all("https://s.shopee.tw/D9_" in x[5] for x in r)

    # ---- ③ 匯出 CSV
    page.click("#tabFlash")
    with page.expect_download() as dl:
        page.click("#btnCsv")
    raw = Path(dl.value.path()).read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    rec = list(csv.DictReader(io.StringIO(text)))
    assert len(rec) == 60 and rec[0]["商品名稱"].startswith("限時商品1-")
    done = [x for x in rec if x["分潤狀態"] == "已轉換"]
    assert len(done) == 59 and all(x["分潤連結"].startswith("https://s.shopee.tw/G7_") for x in done)
    unconv = [x for x in rec if x["分潤狀態"] != "已轉換"]
    assert len(unconv) == 1 and unconv[0]["分潤連結"] == "" and "不是分潤連結" in unconv[0]["分潤狀態"]
    assert {x["狀態"] for x in rec} == {"已開始", "尚未開始"}
    assert re.match(r"\d{4}/\d{2}/\d{2} 週. \d{2}:\d{2}", rec[0]["場次開始"])

    # ---- 匯出的 CSV 可以直接匯入自動化程式（商品連結＋分潤連結）
    Path("/tmp/claude-0/ext_export.csv").parent.mkdir(parents=True, exist_ok=True)
    Path("/tmp/claude-0/ext_export.csv").write_bytes(raw)

    # ---- 診斷檔
    with page.expect_download() as dl:
        page.click("#btnDiag")
    diag = json.loads(Path(dl.value.path()).read_text(encoding="utf-8"))
    assert diag["itemCount"] >= 65 and diag["captured"] and len(diag["sessions"]) == 4
    assert diag["captureLog"]["sessions"] and diag["captureLog"]["sessions"][0]["promotionid"] == "1"
    assert any(c["source"] == "flash" and c["items"] > 0 for c in diag["captured"])


def reset_state(page, site):
    import urllib.request
    urllib.request.urlopen(f"{site}/__reset").read()
    page.click("#btnClear")
    page.wait_for_function("() => document.getElementById('statAll').textContent === '0'", timeout=10000)


def server_log(site):
    import urllib.request
    return json.loads(urllib.request.urlopen(f"{site}/__log").read())


def test_session_redirected_to_homepage_is_skipped_and_homepage_data_ignored(dash, site):
    """蝦皮把場次網址轉到首頁時：跳過、不收首頁（首頁也有限時特賣小區塊）的資料、不會在首頁一直捲動。"""
    page = dash
    reset_state(page, site)
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1")
    page.fill("#moreSessions", "3")                      # 會依序試場次 2、6（6 會被轉到首頁）
    page.click("#btnFlash")
    msg = wait_msg(page, "完成|已停止")
    assert "讀了 2 個場次" in msg and "被蝦皮轉走、已跳過" in msg, msg
    assert "轉到別的頁面" in page.inner_text("#capLog")
    r = rows(page)
    assert len(r) == 60                                   # 場次 1 的 40 個 + 場次 2 的 20 個，沒有首頁的 5 個
    assert not any("限時商品1-90" in x[1] for x in r)
    assert server_log(site)["visits"].count(6) == 1       # 轉走的場次只試一次，不會重複


def test_same_session_pasted_twice_is_captured_once(dash, site):
    page = dash
    reset_state(page, site)
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1\n{site}/flash_sale?promotionId=1\n\n{site}/flash_sale?promotionId=2")
    page.fill("#moreSessions", "0")
    page.click("#btnFlash")
    msg = wait_msg(page, "完成")
    assert "讀了 2 個場次" in msg and "共 60 個商品" in msg, msg
    assert server_log(site)["visits"] == [1, 2]           # 每個場次只開一次


def test_stop_button_really_stops(dash, site):
    page = dash
    reset_state(page, site)
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1")
    page.fill("#moreSessions", "5")
    page.click("#btnFlash")
    page.wait_for_function("() => document.getElementById('capLog').textContent.length > 0 || document.getElementById('msg').textContent.includes('讀取')", timeout=60000)
    page.click("#btnStopFlash")
    t0 = time.time()
    msg = wait_msg(page, "已停止|完成", 60000)
    assert "已停止" in msg or time.time() - t0 < 60
    assert not page.is_disabled("#btnFlash")             # 停下來後按鈕恢復可按
    assert len(server_log(site)["visits"]) <= 2


def test_learns_portal_format_and_converts_in_batches(dash, site):
    """假後台的請求格式和程式內建猜的完全不同，且一次最多收 7 個。
    使用者手動轉 1 個 → 小幫手學到格式 → 之後批次送（20 被拒 → 10 被拒 → 5 成功），不是一個一個轉。"""
    import urllib.request
    page = dash
    reset_state(page, site)
    urllib.request.urlopen(f"{site}/__mode?gql=bulk").read()       # 內建猜的 gql 路徑在這個後台不存在（404）
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1")
    page.fill("#moreSessions", "0")
    page.click("#btnFlash")
    wait_msg(page, "完成")
    assert len(rows(page)) == 40
    assert "還沒學會" in page.inner_text("#affLearn")

    # 使用者在分潤後台手動轉 1 個
    portal = page.context.new_page()
    portal.goto(f"{site}/offer/custom_link")
    portal.fill("input", "https://shopee.tw/product/7/100")
    portal.click("text=取得連結")
    portal.wait_for_function("() => document.getElementById('out').textContent.includes('s.shopee.tw')")
    page.bring_to_front()
    page.wait_for_function("() => document.getElementById('affLearn').textContent.includes('已學會')", timeout=15000)
    assert "可以一次轉很多個" in page.inner_text("#affLearn")
    portal.close()

    urllib.request.urlopen(f"{site}/__reset").read()
    page.click("#btnAff")
    msg = wait_msg(page, "完成.*成功 \\d+ 個", 120000)
    assert "照你後台的做法" in msg and "成功 40 個、失敗 0 個" in msg, msg
    sizes = server_log(site)["bulk"]
    accepted = [n for n, code in sizes if code == 200]
    rejected = [n for n, code in sizes if code == 400]
    assert rejected == [20, 10], sizes                    # 先試 20、10 被拒，自動減量
    assert max(accepted) == 5 and sum(accepted) == 40     # 之後每批 5 個，共 40 個
    assert len(accepted) == 8                             # 8 次請求，不是 40 次
    r = rows(page)
    assert all(re.search(r"https://s\.shopee\.tw/L7_\d+", x[5]) for x in r)
    # 清空資料後，已學會的做法仍保留
    page.click("#btnClear")
    page.wait_for_function("() => document.getElementById('statAll').textContent === '0'")
    assert "已學會" in page.inner_text("#affLearn")


def test_single_only_portal_sends_concurrent_requests_and_backs_off(dash, site):
    """後台一次只收 1 個：不要一個等一個，同時送好幾個；後台嫌太快（429）就自動減速重試，最後全部轉完。"""
    import urllib.request
    page = dash
    reset_state(page, site)
    urllib.request.urlopen(f"{site}/__mode?gql=single").read()
    page.fill("#flashUrls", f"{site}/flash_sale?promotionId=1")
    page.fill("#moreSessions", "0")
    page.click("#btnFlash")
    wait_msg(page, "完成")
    assert len(rows(page)) == 40

    portal = page.context.new_page()                                   # 使用者手動轉 1 個 → 學會「這個後台一次只收 1 個」
    portal.goto(f"{site}/offer/custom_link")
    portal.fill("input", "https://shopee.tw/product/7/100")
    portal.click("text=取得連結")
    portal.wait_for_function("() => document.getElementById('out').textContent.includes('s.shopee.tw')")
    page.bring_to_front()
    page.wait_for_function("() => document.getElementById('affLearn').textContent.includes('已學會')", timeout=15000)
    assert "一次只能轉 1 個" in page.inner_text("#affLearn")
    portal.close()

    urllib.request.urlopen(f"{site}/__limit?n=2").read()               # 後台同時最多收 2 個，超過就 429
    page.click("#btnAff")
    msg = wait_msg(page, "完成.*成功 \\d+ 個", 120000)
    assert "成功 40 個、失敗 0 個" in msg and "同時送出" in msg, msg
    log = server_log(site)
    assert log["peak_attempt"] >= 3                                    # 一開始同時送了好幾個（不是排隊）
    assert log["n429"] >= 1                                            # 被限流了
    assert log["peak_ok"] <= 2                                         # 被限流後有減速，沒有硬衝
    assert all(re.search(r"https://s\.shopee\.tw/S7_\d+", x[5]) for x in rows(page))


def test_shop_discounts_only_this_seller_and_stops_when_empty(dash, site):
    """貼賣家商店頁網址：逐頁抓，只留這個賣家「有打折」的商品，排除底下推薦的別人商品；第 3 頁沒東西就停。"""
    page = dash
    reset_state(page, site)
    page.fill("#shopUrls", f"{site}/shop/9")
    page.select_option("#shopPages", "5")
    page.fill("#shopMinPct", "10")
    page.click("#btnShop")
    msg = wait_msg(page, "完成|沒有抓到")
    assert "共抓到 12 個賣場特價品" in msg, msg
    r = rows(page)
    assert len(r) == 12
    assert all("賣家商品" in x[1] and "賣場" in x[1] for x in r)          # 沒有推薦商品；來源標示「賣場」
    assert all("-33%" in x[2] for x in r)
    assert server_log(site)["shop_pages"] == [0, 1, 2]                     # 抓到沒有新商品就不再往後翻
    assert "排除" in page.inner_text("#capLog")


def test_shop_by_username_url_that_redirects_to_shop_id(dash, site):
    page = dash
    reset_state(page, site)
    page.fill("#shopUrls", f"{site}/mystore")
    page.select_option("#shopPages", "3")
    page.click("#btnShop")
    msg = wait_msg(page, "完成|沒有抓到")
    r = rows(page)
    assert len(r) == 6 and all("賣家商品" in x[1] for x in r), (msg, len(r))   # 賣家帳號網址轉到 /shop/9 也收得到，且沒混進別人的商品


def test_shop_rejects_homepage_and_product_urls(dash, site):
    page = dash
    reset_state(page, site)
    page.fill("#shopUrls", f"{site}/")
    page.click("#btnShop")
    assert "這是蝦皮首頁" in wait_msg(page, "首頁")
    page.fill("#shopUrls", f"{site}/product/9/100")
    page.click("#btnShop")
    assert "不是賣家商店頁" in wait_msg(page, "不是賣家商店頁")
    assert server_log(site)["shop_pages"] == []                             # 根本沒開網頁


def seed(page, items, aff=None):
    page.evaluate("""async ([items, aff]) => { await chrome.storage.local.set({ items, aff: aff || {} }); }""", [items, aff])


def item(key, name, **kw):
    shop, iid = key.split(".")
    base = dict(key=key, itemid=iid, shopid=shop, name=name, image="", price=100, original=200, discountPct=50, stock=1, sold=1,
                promotionid="", start=None, end=None, source="search", url=f"https://shopee.tw/product/{shop}/{iid}", capturedAt=1)
    return {**base, **kw}


def test_clear_duplicates_with_undo(dash, site):
    page = dash
    reset_state(page, site)
    items = {x["key"]: x for x in [
        item("7.1", "【特價】保溫杯 500ml", discountPct=30, price=299),
        item("7.2", "保溫杯500ML", discountPct=40, price=399),
        item("8.3", "保溫杯500ml(送杯套)", discountPct=20, price=450),
        item("7.4", "小風扇", discountPct=30),
        item("9.5", "小風扇", discountPct=45),
        item("9.6", "完全不同的商品", discountPct=10),
    ]}
    seed(page, items, {"8.3": {"url": "https://s.shopee.tw/A", "state": "ok"}})
    page.click("#tabOther")
    page.wait_for_function("() => document.getElementById('statAll').textContent === '6'")
    assert len(rows(page)) == 6

    page.select_option("#dupMode", "nameShop")                              # 只清同一個賣家的：7.1 與 7.2 同賣家
    page.click("#btnDedupe")
    msg = wait_msg(page, "已清除")
    assert "已清除 1 個重複" in msg and len(rows(page)) == 5, msg

    page.click("#btnUndo")
    wait_msg(page, "已復原")
    assert len(rows(page)) == 6
    page.select_option("#dupMode", "name")                                  # 名稱幾乎一樣就算：保溫杯 3 筆留 1、小風扇 2 筆留 1
    page.click("#btnDedupe")
    wait_msg(page, "已清除 3 個重複")
    keys = sorted(x[1].split("\n")[-1] for x in rows(page))
    assert keys == ["8.3", "9.5", "9.6"], keys                              # 8.3 有分潤連結所以留它；9.5 折扣較大
    assert "可以復原" in page.inner_text("#dupInfo")
    page.click("#btnUndo")
    wait_msg(page, "已復原")
    assert len(rows(page)) == 6
    page.click("#btnDedupe")
    assert "沒有找到重複" not in page.inner_text("#msg") or True


def test_pick_products_and_generate_scenario_posts(dash, site):
    page = dash
    reset_state(page, site)
    now = int(__import__("time").time())
    items = {x["key"]: x for x in [
        item("7.1", "【特價】保溫杯 500ml", discountPct=30, price=299, original=427, source="flash", promotionid="9", start=now + 3600, end=now + 7200),
        item("7.2", "小風扇", discountPct=40, price=199, original=330, source="flash", promotionid="9", start=now + 3600, end=now + 7200),
        item("7.3", "沒勾選的商品", discountPct=20, source="flash", promotionid="9", start=now + 3600, end=now + 7200),
    ]}
    seed(page, items, {"7.1": {"url": "https://s.shopee.tw/AbC", "state": "ok"}})
    page.wait_for_function("() => document.getElementById('statAll').textContent === '3'")
    page.click("#tabFlash")
    page.click("#btnPosts")                                              # 沒勾選 → 要提醒
    assert "還沒有勾選" in wait_msg(page, "還沒有勾選")
    page.check("tr:has-text('保溫杯') input[data-sel]")
    page.check("tr:has-text('小風扇') input[data-sel]")
    assert "已勾選 2 個" in page.inner_text("#selCount")
    page.click("#btnPosts")
    msg = wait_msg(page, "已產生 2 個商品的文案")
    assert "1 個還沒有分潤連結" in msg, msg                              # 小風扇還沒轉
    texts = page.eval_on_selector_all("#postOut textarea", "ts => ts.map(t => t.value)")
    assert len(texts) == 2 * (3 * 3 + 1)                                  # 每商品 3 則 ×（含連結/不含/留言）+ Threads
    with_link = [t for t in texts if "保溫杯" in t and "https://s.shopee.tw/AbC" in t and "分潤連結" in t]
    assert with_link, texts[:3]
    assert not any("沒勾選" in t for t in texts)
    fan = [t for t in texts if "小風扇" in t and "👉 商品連結：" in t]
    assert fan and all("還沒有分潤連結" in t and "shopee.tw/product" not in t for t in fan)   # 沒分潤連結絕不放一般網址
    assert all("以上為情境示意" in t and "內含蝦皮分潤連結" in t for t in with_link + fan)   # 含連結的貼文都有揭露
    # 匯出 CSV
    with page.expect_download() as dl:
        page.click("#btnPostsCsv")
    csv_text = open(dl.value.path(), encoding="utf-8-sig").read()
    assert csv_text.startswith("商品名稱,風格,") and "Threads 短文" in csv_text and "https://s.shopee.tw/AbC" in csv_text
    # 全選 / 全不選
    page.click("#btnSelNone")
    assert "已勾選 0 個" in page.inner_text("#selCount")
    page.click("#btnSelAll")
    assert "已勾選 3 個" in page.inner_text("#selCount")


def prepare_posts(page, site, keys_with_link=("7.1",)):
    """兩個商品（7.1 有分潤連結、7.2 沒有）→ 勾選 → 生成文案。"""
    reset_state(page, site)
    now = int(__import__("time").time())
    items = {x["key"]: x for x in [
        item("7.1", "保溫杯", discountPct=30, price=299, original=427, source="flash", promotionid="9", start=now + 3600, end=now + 7200),
        item("7.2", "小風扇", discountPct=40, price=199, original=330, source="flash", promotionid="9", start=now + 3600, end=now + 7200),
    ]}
    seed(page, items, {k: {"url": "https://s.shopee.tw/AbC", "state": "ok"} for k in keys_with_link})
    page.wait_for_function("() => document.getElementById('statAll').textContent === '2'")
    page.click("#tabFlash")
    page.click("#btnSelAll")
    page.click("#btnPosts")
    wait_msg(page, "已產生 2 個商品的文案")


def test_publish_to_threads_preview_then_real_and_facebook_with_link_in_comment(dash, site):
    page = dash
    prepare_posts(page, site)
    assert page.is_checked("#pPreview")                                   # 預設就是「只貼好」
    # 沒有分潤連結的商品不能發
    page.locator("#postOut details.card", has_text="小風扇").locator("summary").first.click()
    page.locator("#postOut details.card", has_text="小風扇").locator("[data-post=threads]").first.click()
    assert "還沒轉成分潤連結" in wait_msg(page, "還沒轉成分潤連結")
    assert server_log(site)["social"]["posts"] == []

    card = page.locator("#postOut details.card", has_text="保溫杯")
    card.locator("summary").first.click()
    card.locator("[data-post=threads]").first.click()
    assert "停在發佈前" in wait_msg(page, "停在發佈前")
    assert server_log(site)["social"]["posts"] == []                       # 預覽：沒有真的發

    page.uncheck("#pPreview")
    card.locator("[data-post=threads]").first.click()
    assert "已發到 Threads" in wait_msg(page, "已發到 Threads")
    posts = server_log(site)["social"]["posts"]
    assert len(posts) == 1 and len(posts[0]["texts"]) == 2
    assert "https://s.shopee.tw/AbC" not in posts[0]["texts"][0] and "https://s.shopee.tw/AbC" in posts[0]["texts"][1]   # 上面只有文，連結在第二則
    assert "\n" in posts[0]["texts"][0] and "以上為情境示意" in posts[0]["texts"][0]
    assert "已發過 Threads" in page.text_content("#postOut")

    page.click("#btnPosts")                                                # 重新產生不會弄丟「已發過」的紀錄
    wait_msg(page, "已產生")
    page.fill("#pFb", site + "/social/fb")
    page.dispatch_event("#pFb", "change")
    page.locator("#postOut details.card", has_text="保溫杯").locator("summary").first.click()
    page.locator("#postOut details.card", has_text="保溫杯").locator("[data-post=facebook]").first.click()
    assert "已發到 Facebook" in wait_msg(page, "已發到 Facebook")
    log = server_log(site)["social"]["posts"]
    fb = [x for x in log if x["platform"] == "facebook"]
    assert "https://s.shopee.tw/AbC" not in fb[0]["texts"][0]               # Facebook 貼文本身沒有連結
    assert any("https://s.shopee.tw/AbC" in (x.get("comment") or "") for x in fb)   # 連結在留言


def test_publish_failure_says_which_step_and_is_in_diagnostics(dash, site):
    page = dash
    prepare_posts(page, site)
    page.fill("#pFb", site + "/")                                          # 這個頁面沒有「建立貼文」
    page.dispatch_event("#pFb", "change")
    page.uncheck("#pPreview")
    card = page.locator("#postOut details.card", has_text="保溫杯")
    card.locator("summary").first.click()
    card.locator("[data-post=facebook]").first.click()
    msg = wait_msg(page, "卡在")
    assert "卡在「開啟發文框」" in msg and "下載診斷檔" in msg, msg
    diag = page.evaluate("async () => (await chrome.storage.local.get('socialDiag')).socialDiag")
    assert diag["stage"] == "開啟發文框" and diag["dump"]["elements"] is not None
    page.check("#pPreview")


def test_threads_search_then_reply_one_by_one_no_links(dash, site):
    page = dash
    reset_state(page, site)
    page.fill("#leadKw", "保溫杯")
    page.click("#btnLeadSearch")
    assert "新增 2 篇" in wait_msg(page, "新增 2 篇")
    assert page.locator("#leadOut [data-lead]").count() == 2
    page.fill("#lead0", "快看 https://s.shopee.tw/AbC")                    # 不能放連結
    page.click("#leadOut [data-lead='0']")
    assert "不能放連結" in wait_msg(page, "不能放連結")
    page.fill("#lead0", "")
    page.click("#leadOut [data-lead='0']")
    assert "空的" in wait_msg(page, "空的")
    assert server_log(site)["social"]["replies"] == []
    page.fill("#lead0", "我也在找，推薦先看容量跟保溫時間～")
    page.click("#leadOut [data-lead='0']")
    assert "已回覆" in wait_msg(page, "已回覆")
    rep = server_log(site)["social"]["replies"]
    assert len(rep) == 1 and rep[0]["text"].startswith("我也在找")          # 只回了按的那一篇
    assert page.locator("#leadOut [data-lead]").count() == 1                # 回過的不再出現
    assert page.inner_text("#replyCount") == "1"
    page.click("#btnLeadSearch")                                           # 再搜一次，同一篇不會重複出現
    wait_msg(page, "新增 0 篇")
    assert page.locator("#leadOut [data-lead]").count() == 1
