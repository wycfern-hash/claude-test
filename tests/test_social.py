"""Threads / Facebook 粉絲專頁 發文、回留言、找話題。
瀏覽器部分只對「假的 Threads/Facebook 頁面」驗證流程；真站的按鈕文字是猜的，要靠 data/debug 診斷檔校正。"""
import http.server
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from shopee_clips import browser, config, db, posts, social, webauto, worker

CHROME = next(iter(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)

THREADS = """<!doctype html><meta charset=utf-8><body>
<div id=open onclick="openC()">有什麼新鮮事？</div><div id=area></div>
<script>
const parts=[]; let files=0;
function openC(){ area.innerHTML='<div role=textbox contenteditable=true class=tb></div><div id=add onclick="addP()">新增到串文</div>'
  +'<input type=file id=f style="display:none" onchange="files=this.files.length"><button id=post onclick="doPost()">發佈</button>'; }
function addP(){ const d=document.createElement('div'); d.setAttribute('role','textbox'); d.contentEditable=true; d.className='tb'; area.insertBefore(d, add); }
async function doPost(){ const t=[...document.querySelectorAll('.tb')].map(x=>x.innerText);
  await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'threads',texts:t,files:document.getElementById('f').files.length})});
  area.innerHTML='<a href="/post/1">查看</a>'; }
</script>"""

POST_PAGE = """<!doctype html><meta charset=utf-8><body>
<div data-pressable-container="true"><a href="/@amy">amy</a><span dir="auto">請問連結在哪裡？</span><div class=rb onclick="rep(this)">回覆</div></div>
<div data-pressable-container="true"><a href="/@bob">bob</a><span dir="auto">看起來不錯耶</span><div class=rb onclick="rep(this)">回覆</div></div>
<div id=box></div>
<script>
function rep(el){ const who=el.parentElement.querySelector('span').innerText;
  box.innerHTML='<div role=textbox contenteditable=true id=tb></div><button onclick="send(\\''+who+'\\')">回覆</button>'; }
async function send(who){ await fetch('/__reply',{method:'POST',body:JSON.stringify({to:who,text:document.getElementById('tb').innerText})}); box.innerHTML='ok'; }
</script>"""

SEARCH = """<!doctype html><meta charset=utf-8><body>
<div data-pressable-container="true"><a href="/@zoe">zoe</a><a href="/post/11">t</a><span dir="auto">最近想買保溫杯，有推薦的嗎</span></div>
<div data-pressable-container="true"><a href="/@max">max</a><a href="/post/12">t</a><span dir="auto">保溫杯好難選</span></div>
<script>/* 回覆流程共用 /post 頁 */</script>"""

FB = """<!doctype html><meta charset=utf-8><body>
<div id=open onclick="openD()">建立貼文</div><div id=feed></div>
<script>
function openD(){ const d=document.createElement('div'); d.setAttribute('role','dialog'); d.id='dlg';
  d.innerHTML='<div role=textbox contenteditable=true id=tb></div><button onclick="photo()">相片/影片</button><button id=pb onclick="doPost()">發佈</button>';
  document.body.appendChild(d); }
function photo(){ const i=document.createElement('input'); i.type='file'; i.id='f'; i.style.display='none'; document.getElementById('dlg').appendChild(i); }
async function doPost(){ const f=document.getElementById('f'); await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'facebook',texts:[document.getElementById('tb').innerText],files:f?f.files.length:0})});
  document.getElementById('dlg').remove(); feed.innerHTML='<div role=article><div>剛發的貼文</div><div role=textbox contenteditable=true aria-label="留言"></div></div>';
  document.addEventListener('keydown',async e=>{ if(e.key==='Enter' && !e.shiftKey && e.target.getAttribute && e.target.getAttribute('aria-label')==='留言'){ e.preventDefault();
    await fetch('/__post',{method:'POST',body:JSON.stringify({platform:'facebook',comment:e.target.innerText})}); e.target.innerText=''; }}); }
</script>"""


@pytest.fixture
def mock_site():
    log = {"posts": [], "replies": []}

    class H(http.server.BaseHTTPRequestHandler):
        def _send(self, body, ctype="text/html; charset=utf-8"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/__log":
                return self._send(json.dumps(log), "application/json")
            page = {"/threads": THREADS, "/fb": FB, "/search": SEARCH}.get(u.path, POST_PAGE)
            if u.path == "/fb2":
                page = FB
            self._send(page)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            (log["replies"] if self.path == "/__reply" else log["posts"]).append(body)
            self._send("{}", "application/json")

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    yield base, lambda: json.loads(__import__("urllib.request").request.urlopen(base + "/__log").read())
    srv.shutdown()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name, sub in (("DATA_DIR", ""), ("DB_PATH", "clips.db"), ("REF_DIR", "ref"), ("IMG_DIR", "images"),
                      ("VID_DIR", "videos"), ("BROWSER_PROFILE", "bp")):
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    for k in [n for n, _, _ in config.SPEC]:
        monkeypatch.delenv(k, raising=False)
    config.reload()
    worker.flashes.clear()
    yield
    config.reload()


def product(conn, url="https://shopee.tw/a-i.1.1", aff="https://s.shopee.tw/AbC", title="保溫杯", with_posts=True):
    pid = db.add_product(conn, url, title=title, price="300", description="雙層真空保溫。杯蓋可密封防漏", source_url=aff)
    if with_posts:
        posts.generate(conn, pid)
    return pid


# ------------------------------------------------------------ 不需要瀏覽器的規則
def test_queue_requires_posts_and_affiliate_link_and_is_unique():
    with db.connect() as conn:
        no_posts = product(conn, with_posts=False)
        with pytest.raises(RuntimeError, match="還沒有產生貼文"):
            social.queue(conn, no_posts, "threads")
        plain = product(conn, "https://shopee.tw/b-i.2.2", aff="", title="一般連結")
        with pytest.raises(RuntimeError, match="沒有分潤連結"):
            social.queue(conn, plain, "threads")
        ok = product(conn, "https://shopee.tw/c-i.3.3", title="風扇")
        assert social.queue(conn, ok, "threads") and social.queue(conn, ok, "facebook")
        assert social.queue(conn, ok, "threads") is None          # 同商品同平台只發一次
        with pytest.raises(ValueError):
            social.queue(conn, ok, "instagram")


def test_link_placement_comment_vs_body():
    with db.connect() as conn:
        pid = product(conn)
        social.queue(conn, pid, "threads")
        r = social.rows(conn)[0]
    assert "https://s.shopee.tw/AbC" not in r["text"] and "https://s.shopee.tw/AbC" in r["comment"]   # 預設：連結放留言
    assert config.POST_DISCLOSURE in r["text"]
    config.save_env({"SOCIAL_LINK_IN": "body"})
    with db.connect() as conn:
        pid = product(conn, "https://shopee.tw/d-i.4.4", "https://s.shopee.tw/Zzz", "風扇")
        social.queue(conn, pid, "threads")
        r = [x for x in social.rows(conn) if x["product_id"] == pid][0]
    assert "https://s.shopee.tw/Zzz" in r["text"] and r["comment"] == ""


def test_daily_cap_and_min_gap_block_auto_posting(monkeypatch):
    called = []
    monkeypatch.setattr(social, "post_one", lambda *a, **k: called.append(1) or "")
    with db.connect() as conn:
        for i, n in enumerate(("a", "b", "c")):
            pid = product(conn, f"https://shopee.tw/{n}-i.{i + 1}.{i + 1}", f"https://s.shopee.tw/{n}", n)
            social.queue(conn, pid, "threads")
        assert social.run_queue(conn, ctx=object()) == 1                     # 一次最多一則
        assert social.run_queue(conn, ctx=object()) == 0                     # 間隔（預設 90 分鐘）還沒到
        config.save_env({"SOCIAL_MIN_GAP_MIN": "0", "SOCIAL_DAILY_CAP": "1"})
        assert social.run_queue(conn, ctx=object()) == 0                     # 間隔 OK 但今天已達上限
        config.save_env({"SOCIAL_DAILY_CAP": "5"})
        assert social.run_queue(conn, ctx=object()) == 1                     # 上限放寬就繼續
        assert social.posted_today(conn) == 2 and len(called) == 2
        config.save_env({"SOCIAL_MIN_GAP_MIN": "90"})
        conn.execute("UPDATE social_posts SET posted_at='2000-01-01T00:00:00+00:00' WHERE status='posted'")
        assert social.run_queue(conn, ctx=object()) == 1                     # 上次發文很久以前 → 間隔早就過了


def test_draft_for_link_questions_uses_affiliate_link_only():
    with db.connect() as conn:
        pid = product(conn)
        row = db.get(conn, pid)
    kind, text = social.draft_for("請問連結在哪裡？", row)
    assert kind == "link" and "https://s.shopee.tw/AbC" in text
    kind, text = social.draft_for("看起來不錯耶", row)
    assert kind == "chat" and "http" not in text


def test_lead_reply_rules_no_links_no_empty_and_daily_cap():
    with db.connect() as conn:
        conn.execute("INSERT INTO social_leads (keyword,url,author,text,created_at) VALUES ('k','http://x/post/1','a','t','now')")
        with pytest.raises(RuntimeError, match="不能放連結"):
            social.send_lead(conn, 1, "看這個 https://s.shopee.tw/AbC", ctx=object())
        with pytest.raises(RuntimeError, match="空的"):
            social.send_lead(conn, 1, "  ", ctx=object())
        config.save_env({"SOCIAL_LEAD_DAILY_CAP": "0"})
        with pytest.raises(RuntimeError, match="每日上限"):
            social.send_lead(conn, 1, "好看", ctx=object())


def test_social_page_and_queue_actions():
    from shopee_clips import web

    with db.connect() as conn:
        product(conn)
        product(conn, "https://shopee.tw/b-i.2.2", aff="", title="沒連結")
    c = TestClient(web.app)
    page = c.get("/social").text
    assert "發文佇列" in page and "排進 Threads" in page and "沒有分潤連結" in page and "找話題" in page
    assert c.post("/social/queue", data={"pid": 1, "platform": "threads"}, follow_redirects=False).status_code == 303
    assert "已排" in c.get("/social").text
    c.post("/social/queue", data={"pid": 2, "platform": "threads"}, follow_redirects=False)
    with db.connect() as conn:
        assert len(social.rows(conn)) == 1
    c.post("/social/post/1", data={"skip": "1"}, follow_redirects=False)
    with db.connect() as conn:
        assert social.rows(conn)[0]["status"] == "skipped"
    assert c.post("/social/auto", data={"on": "1"}, follow_redirects=False).status_code == 303 and config.SOCIAL_AUTO
    c.post("/social/auto", data={"on": "0"}, follow_redirects=False)
    assert not config.SOCIAL_AUTO and "SOCIAL_FB_PAGE_URL" in c.get("/settings").text


# ------------------------------------------------------------ 假的 Threads / Facebook 頁面
def labels(base):
    t = json.loads(Path("config/social_sites.json").read_text(encoding="utf-8"))
    t["threads"] |= {"url": base + "/threads", "search_url": base + "/search?q={q}", "step_timeout_sec": 8,
                     "search_link_selector": "a[href*='/post/']"}
    t["facebook"] |= {"step_timeout_sec": 8}
    return t


@pytest.fixture
def chrome(monkeypatch, mock_site):
    base, _ = mock_site
    monkeypatch.setattr(webauto, "DEBUG_DIR", config.DATA_DIR / "debug")
    monkeypatch.setattr(config, "CHROME_PATH", str(CHROME))
    monkeypatch.setattr(config, "CHROME_HEADLESS", True)
    monkeypatch.setattr(config, "CDP_PORT", 9335)
    L = labels(base)
    monkeypatch.setattr(social, "site", lambda n: L[n])
    monkeypatch.setattr(config, "SOCIAL_FB_PAGE_URL", base + "/fb")
    try:
        with browser.open_context() as ctx:
            yield ctx, base
    finally:
        import subprocess
        subprocess.run(["pkill", "-f", "remote-debugging-port=933[5]"])   # [5] 讓比對字串不會出現在自己的指令列


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_threads_preview_then_publish_with_thread_comment_and_image(chrome, mock_site):
    from PIL import Image

    ctx, base = chrome
    _, getlog = mock_site
    with db.connect() as conn:
        pid = product(conn)
        img = config.DATA_DIR / "images" / "1" / "0.png"
        img.parent.mkdir(parents=True)
        Image.new("RGB", (50, 50), "red").save(img)
        conn.execute("UPDATE products SET selected_images=? WHERE id=?", (json.dumps(["images/1/0.png"]), pid))
        social.queue(conn, pid, "threads")
        sid = social.rows(conn)[0]["id"]
        social.run_queue(conn, only_id=sid, preview=True, ctx=ctx)       # 預覽：不發佈
        assert getlog()["posts"] == [] and social.rows(conn)[0]["status"] == "queued"
        assert "尚未發佈" in social.rows(conn)[0]["error"]
        for pg in list(ctx.pages):
            if pg.url.startswith(base):
                pg.close()
        assert social.run_queue(conn, only_id=sid, ctx=ctx) == 1
        r = social.rows(conn)[0]
    log = getlog()["posts"]
    assert len(log) == 1 and log[0]["platform"] == "threads" and log[0]["files"] == 1
    assert len(log[0]["texts"]) == 2                                      # 串文：貼文 + 第一則留言（連結）
    assert "https://s.shopee.tw/AbC" in log[0]["texts"][1] and "https://s.shopee.tw/AbC" not in log[0]["texts"][0]
    assert "\n" in log[0]["texts"][0]                                      # 換行有保留
    assert r["status"] == "posted" and r["post_url"].endswith("/post/1")


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_facebook_page_post_and_first_comment(chrome, mock_site):
    ctx, base = chrome
    _, getlog = mock_site
    with db.connect() as conn:
        pid = product(conn)
        social.queue(conn, pid, "facebook")
        assert social.run_queue(conn, only_id=social.rows(conn)[0]["id"], ctx=ctx) == 1, social.rows(conn)[0]["error"]
    log = getlog()["posts"]
    assert log[0]["platform"] == "facebook" and log[0]["texts"][0].strip()
    assert any("https://s.shopee.tw/AbC" in (x.get("comment") or "") for x in log)   # 第一則留言有連結


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_failed_post_is_recorded_with_diagnostics(chrome, monkeypatch):
    ctx, base = chrome
    L = labels(base)
    L["threads"]["composer_open"] = "不存在的按鈕文字"
    monkeypatch.setattr(social, "site", lambda n: L[n])
    with db.connect() as conn:
        pid = product(conn)
        social.queue(conn, pid, "threads")
        assert social.run_queue(conn, only_id=1, ctx=ctx) == 0
        r = social.rows(conn)[0]
    assert r["status"] == "failed" and "threads_開啟發文框" in r["error"]
    assert list((config.DATA_DIR / "debug").glob("*.png"))


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_scan_comments_drafts_once_and_sends_only_approved_and_auto_link(chrome, mock_site):
    ctx, base = chrome
    _, getlog = mock_site
    url = base + "/post/1"
    with db.connect() as conn:
        pid = product(conn)
        assert social.collect(conn, "threads", url, pid, ctx=ctx) == 2
        assert social.collect(conn, "threads", url, pid, ctx=ctx) == 0      # 同一則留言不會重複產生
        ds = social.replies(conn)
        by = {d["author"]: d for d in ds}
        assert by["amy"]["kind"] == "link" and "https://s.shopee.tw/AbC" in by["amy"]["draft"]
        assert by["bob"]["kind"] == "chat"
        assert social.send_replies(conn, ctx=ctx, pause=False) == 0         # 還沒核准，不會送
        assert social.send_replies(conn, auto_only=True, ctx=ctx, pause=False) == 1   # 只自動回「問連結」那則
        assert getlog()["replies"][0]["to"].startswith("請問連結") and "https://s.shopee.tw/AbC" in getlog()["replies"][0]["text"]
        conn.execute("UPDATE social_replies SET status='approved', draft='謝謝你～' WHERE author='bob'")
        assert social.send_replies(conn, ctx=ctx, pause=False) == 1
        assert getlog()["replies"][1]["text"] == "謝謝你～"
        config.save_env({"SOCIAL_REPLY_DAILY_CAP": "2"})
        assert social.send_replies(conn, auto_only=True, ctx=ctx, pause=False) == 0   # 達每日上限


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_find_topics_then_reply_one_by_one_without_links(chrome, mock_site):
    ctx, base = chrome
    _, getlog = mock_site
    with db.connect() as conn:
        assert social.find_topics(conn, "保溫杯", ctx=ctx) == 2
        assert social.find_topics(conn, "保溫杯", ctx=ctx) == 0             # 同一篇不會重複
        ls = social.leads(conn)
        assert {x["author"] for x in ls} == {"zoe", "max"} and all(x["draft"] == "" for x in ls)   # 沒選 AI → 不預寫
        z = [x for x in ls if x["author"] == "zoe"][0]
        # 找話題的網址是 /post/11：假站用同一個回覆頁
        social.send_lead(conn, z["id"], "我也在找，推薦先看容量跟保溫時間～", ctx=ctx)
        assert social.leads(conn, "sent")[0]["author"] == "zoe"
    assert getlog()["replies"][-1]["text"].startswith("我也在找")


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_login_tabs_open_only_what_is_asked_and_not_twice(monkeypatch, mock_site):
    base, _ = mock_site
    monkeypatch.setattr(config, "CHROME_PATH", str(CHROME))
    monkeypatch.setattr(config, "CHROME_HEADLESS", True)
    monkeypatch.setattr(config, "CDP_PORT", 9336)
    try:
        browser.open_login_tabs([base + "/threads", base + "/fb"])
        browser.open_login_tabs([base + "/threads", base + "/fb"])       # 已經開著，不會再開
        with browser.open_context() as ctx:
            urls = [p.url for p in ctx.pages]
        assert sum(u.startswith(base) for u in urls) == 2, urls           # 要開的 2 個各開 1 次；第二次呼叫沒有再多開
        assert not any(h in u for u in urls for h in ("google", "shopee", "flow"))
    finally:
        import subprocess
        subprocess.run(["pkill", "-f", "remote-debugging-port=933[6]"])


def test_default_login_tabs_no_longer_include_social_sites():
    assert not any("threads" in u or "facebook" in u for u in browser.LOGIN_URLS)
    assert "shopee.tw" in browser.LOGIN_URLS[-1]
