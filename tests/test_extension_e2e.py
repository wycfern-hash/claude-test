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


class Mock(BaseHTTPRequestHandler):
    gql_mode = "ok"
    now = int(time.time())

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
                {"promotionid": 3, "start_time": n - 7200, "end_time": n - 3600, "name": "已結束"}]

    def promo_items(self, promo):
        s = {x["promotionid"]: x for x in self.sessions()}[promo]
        return (100, 40, s) if promo == 1 else (200, 20, s)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == "/__mode":
            Mock.gql_mode = q["gql"][0]
            return self._send("ok", "text/plain")
        if u.path == "/flash_sale":
            return self._send(FLASH_HTML, "text/html")
        if u.path == "/search":
            return self._send(SEARCH_HTML, "text/html")
        if u.path == "/offer/custom_link":
            return self._send(AFF_HTML, "text/html")
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
        if u.path == "/api/v4/flash_sale/flash_sale_batch_get_items":
            promo = body["promotionid"]
            base, n, s = self.promo_items(promo)
            items = [x for x in mk_items(promo, base, n, s["start_time"], s["end_time"]) if x["itemid"] in body["itemids"]]
            return self._send(json.dumps({"error": 0, "data": {"items": items}}))
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
    return page.eval_on_selector_all("#rows tr", "trs => trs.map(t => [...t.children].map(td => td.innerText))")


def test_full_flow(dash, site):
    page = dash

    # ---- ① 限時特賣：分批懶載入的 40 個 + 下一場 20 個；已結束的場次不抓
    page.fill("#flashUrl", f"{site}/flash_sale?promotionId=1")
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
    page.wait_for_function("document.getElementById('statAff').textContent === '59'", timeout=20000)
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
    assert diag["itemCount"] >= 65 and diag["captured"] and len(diag["sessions"]) == 3
    assert any(c["source"] == "flash" and c["items"] > 0 for c in diag["captured"])
