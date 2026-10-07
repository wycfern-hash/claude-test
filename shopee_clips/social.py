"""Threads / Facebook 粉絲專頁：用你登入的自動化 Chrome 發文、在自己的貼文下補留言、回覆自己貼文底下的留言（不用 API）。
- 佇列：「貼文」頁產生的情境文案 → 排進佇列 → 預覽（只貼好、不發佈）或發佈；背景自動發要自己打開開關，且有每日上限與間隔。
- 只發有「分潤連結」的商品；一個商品在一個平台只發一次。
- 只回覆「你自己貼文」底下的留言；有人問連結的可以自動回，其他留言先產生草稿，由你確認再送出。
標籤與 CSS 在 config/social_sites.json；猜錯時看 data/debug 的診斷檔來修。
"""
import hashlib
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, db, posts, providers
from .webauto import dump_page, rx, step

SITES_PATH = Path("config/social_sites.json")
PLATFORMS = {"threads": "Threads", "facebook": "Facebook 粉絲專頁"}
LINK_ASK = re.compile(r"連結|鏈結|link|網址|哪裡買|哪邊買|在哪買|在哪裡|怎麼買|怎麼訂|購買|下單|多少錢|價格|求|\+1|ㄅ", re.I)


def site(name: str) -> dict:
    return json.loads(SITES_PATH.read_text(encoding="utf-8"))[name]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ 佇列
def images_for(row, limit: int = 4) -> list[str]:
    try:
        imgs = json.loads(row["selected_images"]) or ([row["selected_image"]] if row["selected_image"] else [])
    except ValueError:
        imgs = []
    return [i for i in imgs if (config.DATA_DIR / i).exists()][:limit]


def queue(conn, pid: int, platform: str, item_index: int = 0) -> int | None:
    """把某商品已產生的第 item_index 則貼文排進佇列。沒有分潤連結不排；已排過/發過回傳 None。"""
    if platform not in PLATFORMS:
        raise ValueError("不支援的平台")
    row = db.get(conn, pid)
    data = posts.load(row)
    items = data.get("posts") or []
    if not items:
        raise RuntimeError("這個商品還沒有產生貼文，請先到「貼文」頁產生")
    if not posts.link_of(row):
        raise RuntimeError("這個商品還沒有分潤連結，不會排進發文佇列（避免發出沒有分潤的貼文）")
    it = items[min(item_index, len(items) - 1)]
    in_body = config.SOCIAL_LINK_IN == "body"
    text = posts.compose(row, it, in_body)
    comment = "" if in_body else posts.compose_comment(row, it)
    cur = conn.execute(
        "INSERT OR IGNORE INTO social_posts (product_id,platform,text,comment,images,created_at) VALUES (?,?,?,?,?,?)",
        (pid, platform, text, comment, json.dumps(images_for(row)), db.now()))
    return cur.lastrowid if cur.rowcount else None


def rows(conn, status: str = ""):
    q = "SELECT s.*, p.title FROM social_posts s JOIN products p ON p.id=s.product_id"
    if status:
        return conn.execute(q + " WHERE s.status=? ORDER BY s.id", (status,)).fetchall()
    return conn.execute(q + " ORDER BY s.id").fetchall()


def posted_today(conn) -> int:
    day = _now().date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM social_posts WHERE status='posted' AND posted_at LIKE ?", (day + "%",)).fetchone()[0]


def next_allowed(conn) -> datetime | None:
    """最近一次發文 + 間隔（含 0~20% 隨機，依最近一次的 id 固定，不會每次重算而亂跳）。"""
    r = conn.execute("SELECT id, posted_at FROM social_posts WHERE status='posted' ORDER BY posted_at DESC LIMIT 1").fetchone()
    if not r or not r["posted_at"]:
        return None
    gap = config.SOCIAL_MIN_GAP_MIN * (1 + random.Random(r["id"]).random() * 0.2)
    return datetime.fromisoformat(r["posted_at"]) + timedelta(minutes=gap)


# ------------------------------------------------------------------ 瀏覽器動作
def _human(page, lo=400, hi=1200) -> None:
    page.wait_for_timeout(random.randint(lo, hi))


def type_text(page, box, text: str) -> None:
    """貼進輸入框；換行用 Shift+Enter（單純 Enter 在這些網站會直接送出）。"""
    box.click()
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln:
            page.keyboard.insert_text(ln)
        if i < len(lines) - 1:
            page.keyboard.press("Shift+Enter")
    _human(page, 200, 600)


def _check_login(page, L, platform: str) -> None:
    if re.search(r"/login|/accounts/login", page.url) or (
            page.get_by_role("button", name=rx(L["login_markers"])).count() and not page.get_by_role("textbox").count()):
        raise RuntimeError(f"尚未登入 {PLATFORMS[platform]}：請先在自動化專用 Chrome 登入（首頁按「開啟自動化 Chrome」）")


def _attach(page, files: list[str], scope=None) -> None:
    inp = (scope or page).locator("input[type=file]")
    if inp.count():
        inp.first.set_input_files(files)
        page.wait_for_timeout(2500)


def _threads(ctx, L, row, preview: bool) -> str:
    t = L["step_timeout_sec"] * 1000
    page = ctx.new_page()
    keep = False
    try:
        page.goto(L["url"])
        page.wait_for_load_state("domcontentloaded")
        _check_login(page, L, "threads")
        step(page, "threads_開啟發文框", lambda: page.get_by_text(rx(L["composer_open"])).first.click(timeout=t))
        _human(page)
        step(page, "threads_填文字", lambda: type_text(page, page.get_by_role("textbox").last, row["text"]))
        files = [str(config.DATA_DIR / i) for i in json.loads(row["images"])]
        if files:
            step(page, "threads_附圖", lambda: _attach(page, files), optional=True)
        if row["comment"]:                                  # 連結放在串文的第二則（自己的貼文下面）
            step(page, "threads_新增串文", lambda: page.get_by_text(rx(L["add_to_thread"])).first.click(timeout=t))
            _human(page)
            step(page, "threads_填第二則", lambda: type_text(page, page.get_by_role("textbox").last, row["comment"]))
        if preview:
            keep = True
            return "已預填，尚未發佈"
        step(page, "threads_發佈", lambda: page.get_by_role("button", name=rx(L["post_button"])).last.click(timeout=t))
        page.wait_for_timeout(2500)
        link = step(page, "threads_取得貼文連結", lambda: page.get_by_role("link", name=rx(L["view_post_link"])).first.get_attribute("href", timeout=4000), optional=True)
        return (link if link and link.startswith("http") else ("https://www.threads.com" + link if link else "")) or ""
    finally:
        if not keep:
            page.close()


def _facebook(ctx, L, row, preview: bool) -> str:
    t = L["step_timeout_sec"] * 1000
    if not config.SOCIAL_FB_PAGE_URL:
        raise RuntimeError("還沒填粉絲專頁網址：到設定頁「Threads / Facebook 發文」填 SOCIAL_FB_PAGE_URL")
    page = ctx.new_page()
    keep = False
    try:
        page.goto(config.SOCIAL_FB_PAGE_URL)
        page.wait_for_load_state("domcontentloaded")
        _check_login(page, L, "facebook")
        step(page, "facebook_開啟發文框", lambda: page.get_by_text(rx(L["composer_open"])).first.click(timeout=t))
        _human(page)
        dlg = page.get_by_role("dialog")
        scope = dlg if dlg.count() else page
        step(page, "facebook_填文字", lambda: type_text(page, scope.get_by_role("textbox").first, row["text"]))
        files = [str(config.DATA_DIR / i) for i in json.loads(row["images"])]
        if files:
            def attach():
                if not scope.locator("input[type=file]").count():
                    scope.get_by_role("button", name=rx(L["photo_button"])).first.click(timeout=t)
                _attach(page, files, scope)
            step(page, "facebook_附圖", attach, optional=True)
        step(page, "facebook_下一步", lambda: scope.get_by_role("button", name=rx(L["next_button"])).first.click(timeout=3000), optional=True)
        if preview:
            keep = True
            return "已預填，尚未發佈"
        step(page, "facebook_發佈", lambda: scope.get_by_role("button", name=rx(L["post_button"])).last.click(timeout=t))
        page.wait_for_timeout(4000)
        if row["comment"]:                                  # 發完後新貼文會出現在動態最上面，就在原頁補留言（不重新整理）
            art = page.locator("div[role=article]").first
            def comment():
                box = art.get_by_role("textbox", name=rx(L["comment_box"])).first
                type_text(page, box, row["comment"])
                page.keyboard.press("Enter")
                page.wait_for_timeout(2000)
            step(page, "facebook_補第一則留言", comment)
        return ""
    finally:
        if not keep:
            page.close()


def post_one(ctx, row, preview: bool = False) -> str:
    L = site(row["platform"])
    fn = _threads if row["platform"] == "threads" else _facebook
    return fn(ctx, L, row, preview)


def run_queue(conn, only_id: int | None = None, preview: bool = False, ctx=None) -> int:
    """發佇列裡的貼文。only_id=手動指定一則（不受每日上限/間隔限制，預覽也不計）；否則依每日上限與間隔，一次最多發一則。"""
    if only_id:
        todo = [r for r in rows(conn, "queued") + rows(conn, "failed") if r["id"] == only_id]
    else:
        if posted_today(conn) >= config.SOCIAL_DAILY_CAP:
            return 0
        na = next_allowed(conn)
        if na and _now() < na:
            return 0
        todo = rows(conn, "queued")[:1]
    if not todo:
        return 0
    from . import browser

    def go(c) -> int:
        n = 0
        for r in todo:
            try:
                url = post_one(c, r, preview)
            except Exception as e:  # noqa: BLE001
                conn.execute("UPDATE social_posts SET status='failed', error=? WHERE id=?", (str(e)[:300], r["id"]))
                conn.commit()
                continue
            if preview:
                conn.execute("UPDATE social_posts SET error=? WHERE id=?", (url, r["id"]))
            else:
                conn.execute("UPDATE social_posts SET status='posted', error='', post_url=?, posted_at=? WHERE id=?", (url, db.now(), r["id"]))
                n += 1
            conn.commit()
        return n

    if ctx is not None:
        return go(ctx)
    with browser.open_context() as c:
        return go(c)


# ------------------------------------------------------------------ 回覆自己貼文底下的留言
def ckey(author: str, text: str) -> str:
    return hashlib.sha1(f"{author}\n{text}".encode()).hexdigest()[:16]


def scan_comments(ctx, platform: str, url: str) -> list[dict]:
    """開你自己的貼文，讀出留言（作者、內容）。"""
    L = site(platform)
    page = ctx.new_page()
    try:
        page.goto(url)
        page.wait_for_load_state("domcontentloaded")
        _check_login(page, L, platform)
        page.wait_for_timeout(2500)
        for _ in range(3):  # 往下捲一點，載入更多留言
            page.mouse.wheel(0, 2000)
            page.wait_for_timeout(800)
        out = step(page, f"{platform}_讀取留言", lambda: page.evaluate(
            """([sel, a, t]) => [...document.querySelectorAll(sel)].map(e => ({
                author: ((e.querySelector(a) || {}).innerText || '').trim(),
                text: ((e.querySelector(t) || {}).innerText || '').trim()})).filter(x => x.text)""",
            [L["comment_selector"], L["comment_author_selector"], L["comment_text_selector"]]))
        return out
    finally:
        page.close()


def draft_for(comment: str, row) -> tuple[str, str]:
    """回傳 (類型, 草稿)。有人問連結 → 直接給你的分潤連結；其他 → AI 或簡短回覆。"""
    link = posts.link_of(row) if row else ""
    if LINK_ASK.search(comment) and link:
        return "link", f"謝謝你的留言！商品連結在這 👉 {link}"
    if providers.configured("text"):
        try:
            s = providers.text_json(
                "你是台灣臉書／Threads 的貼文作者，請用一句自然、親切的繁體中文口語回覆這則留言（30 字內，不要放任何網址，"
                f"不要編造商品功能或評價，不要硬推銷）。輸出 JSON：{{\"reply\":\"...\"}}\n留言：{comment[:200]}")
            if str(s.get("reply", "")).strip():
                return "chat", str(s["reply"]).strip()
        except Exception:  # noqa: BLE001
            pass
    return "chat", "謝謝你的留言～有問題都可以問我 😊"


def collect(conn, platform: str, url: str, product_id: int = 0, ctx=None) -> int:
    """掃描留言並產生回覆草稿（同一則留言只記一次，之後不會重複產生）。回傳新草稿數。"""
    from . import browser

    row = db.get(conn, product_id) if product_id else None
    if ctx is None:
        with browser.open_context() as c:
            found = scan_comments(c, platform, url)
    else:
        found = scan_comments(ctx, platform, url)
    n = 0
    for c in found:
        kind, text = draft_for(c["text"], row)
        cur = conn.execute(
            "INSERT OR IGNORE INTO social_replies (platform,post_url,product_id,ckey,author,comment,draft,kind,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (platform, url, product_id, ckey(c["author"], c["text"]), c["author"], c["text"], text, kind, "draft", db.now()))
        n += cur.rowcount
    return n


def replies(conn, status: str = ""):
    q = "SELECT * FROM social_replies"
    return conn.execute(q + (" WHERE status=?" if status else "") + " ORDER BY id", (status,) if status else ()).fetchall()


def replied_today(conn) -> int:
    day = _now().date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM social_replies WHERE status='sent' AND sent_at LIKE ?", (day + "%",)).fetchone()[0]


def send_reply(ctx, r) -> None:
    L = site(r["platform"])
    t = L["step_timeout_sec"] * 1000
    page = ctx.new_page()
    try:
        page.goto(r["post_url"])
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(2500)
        item = page.locator(L["comment_selector"]).filter(has_text=r["comment"][:30]).first
        step(page, f"{r['platform']}_點回覆", lambda: item.get_by_text(rx(L["reply_button"])).first.click(timeout=t))
        _human(page)
        step(page, f"{r['platform']}_填回覆", lambda: type_text(page, page.get_by_role("textbox").last, r["draft"]))
        step(page, f"{r['platform']}_送出回覆", lambda: page.get_by_role("button", name=rx(L["reply_send_button"])).last.click(timeout=t))
        page.wait_for_timeout(2000)
    finally:
        page.close()


def send_replies(conn, ids: list[int] | None = None, auto_only: bool = False, ctx=None, pause: bool = True) -> int:
    """送出已核准的回覆（ids 指定）；auto_only=只送「有人問連結」的草稿（自動模式用）。受每日上限限制，每則之間隨機等待。"""
    from . import browser

    left = config.SOCIAL_REPLY_DAILY_CAP - replied_today(conn)
    if left <= 0:
        return 0
    if auto_only:
        todo = [r for r in replies(conn, "draft") if r["kind"] == "link"]
    else:
        todo = [r for r in replies(conn, "approved") if ids is None or r["id"] in ids]
    todo = todo[:left]
    if not todo:
        return 0

    def go(c) -> int:
        n = 0
        for r in todo:
            try:
                send_reply(c, r)
            except Exception as e:  # noqa: BLE001
                conn.execute("UPDATE social_replies SET status='failed', error=? WHERE id=?", (str(e)[:300], r["id"]))
                conn.commit()
                continue
            conn.execute("UPDATE social_replies SET status='sent', error='', sent_at=? WHERE id=?", (db.now(), r["id"]))
            conn.commit()
            n += 1
            if pause:
                time.sleep(random.randint(20, 60))   # 每則之間隨機隔一下，不要一口氣連發
        return n

    if ctx is not None:
        return go(ctx)
    with browser.open_context() as c:
        return go(c)


# ------------------------------------------------------------------ 找話題：搜尋 Threads 相關貼文 → 草稿 → 你逐篇確認才回
def search_threads(ctx, keyword: str, limit: int = 15) -> list[dict]:
    L = site("threads")
    page = ctx.new_page()
    try:
        from urllib.parse import quote

        page.goto(L["search_url"].format(q=quote(keyword)))
        page.wait_for_load_state("domcontentloaded")
        _check_login(page, L, "threads")
        page.wait_for_timeout(3000)
        for _ in range(2):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(900)
        found = step(page, "threads_搜尋", lambda: page.evaluate(
            """([sel, ln, au, tx]) => [...document.querySelectorAll(sel)].map(e => {
                const a = e.querySelector(ln);
                return {url: a ? a.href : '', author: ((e.querySelector(au) || {}).innerText || '').trim(),
                        text: [...e.querySelectorAll(tx)].map(x => x.innerText.trim()).filter(Boolean).join(' ')};
            }).filter(x => x.url && x.text)""",
            [L["search_item_selector"], L["search_link_selector"], L["search_author_selector"], L["search_text_selector"]]))
        seen, out = set(), []
        for f in found:
            if f["url"] not in seen:
                seen.add(f["url"])
                out.append(f)
        return out[:limit]
    finally:
        page.close()


def lead_draft(text: str, keyword: str) -> str:
    """針對那篇貼文的內容寫一則真的有回應的短回覆（不放連結、不推銷）。沒選 AI 就留空讓你自己寫。"""
    if not providers.configured("text"):
        return ""
    try:
        s = providers.text_json(
            "你是台灣 Threads 的一般使用者，要回覆下面這篇貼文。請寫一則自然、友善、有回應到貼文內容的繁體中文口語回覆"
            f"（50 字內）。規定：不要放任何網址或商品連結、不要推銷或提到購買、不要編造個人經驗細節；沒有話可說就回空字串。\n"
            f"搜尋關鍵字：{keyword}\n貼文：{text[:300]}\n輸出 JSON：{{\"reply\":\"...\"}}")
        return str(s.get("reply", "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def find_topics(conn, keyword: str, ctx=None) -> int:
    from . import browser

    if ctx is None:
        with browser.open_context() as c:
            found = search_threads(c, keyword)
    else:
        found = search_threads(ctx, keyword)
    n = 0
    for f in found:
        if conn.execute("SELECT 1 FROM social_leads WHERE url=?", (f["url"],)).fetchone():
            continue
        conn.execute("INSERT INTO social_leads (keyword,url,author,text,draft,created_at) VALUES (?,?,?,?,?,?)",
                     (keyword, f["url"], f["author"], f["text"], lead_draft(f["text"], keyword), db.now()))
        n += 1
    return n


def leads(conn, status: str = ""):
    return conn.execute("SELECT * FROM social_leads" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                        (status,) if status else ()).fetchall()


def lead_replied_today(conn) -> int:
    day = _now().date().isoformat()
    return conn.execute("SELECT COUNT(*) FROM social_leads WHERE status='sent' AND sent_at LIKE ?", (day + "%",)).fetchone()[0]


def send_lead(conn, lead_id: int, text: str, ctx=None) -> None:
    """回覆一篇別人的貼文（只在你按下那一篇的「送出」時才會執行；受每日上限限制）。"""
    from . import browser

    r = conn.execute("SELECT * FROM social_leads WHERE id=?", (lead_id,)).fetchone()
    text = text.strip()
    if not r or r["status"] == "sent":
        raise RuntimeError("這篇已經回覆過了")
    if not text:
        raise RuntimeError("回覆內容是空的，請先寫一則回覆")
    if re.search(r"https?://|shp\.ee|s\.shopee", text, re.I):
        raise RuntimeError("「找話題」的回覆不能放連結（對別人的貼文貼商品連結會被當成垃圾訊息）")
    if lead_replied_today(conn) >= config.SOCIAL_LEAD_DAILY_CAP:
        raise RuntimeError(f"今天已經回了 {config.SOCIAL_LEAD_DAILY_CAP} 篇，達到每日上限（設定頁可調）")
    L = site("threads")
    t = L["step_timeout_sec"] * 1000

    def go(c):
        page = c.new_page()
        try:
            page.goto(r["url"])
            page.wait_for_load_state("domcontentloaded")
            _check_login(page, L, "threads")
            page.wait_for_timeout(2500)
            step(page, "threads_點回覆", lambda: page.get_by_text(rx(L["reply_button"])).first.click(timeout=t))
            _human(page)
            step(page, "threads_填回覆", lambda: type_text(page, page.get_by_role("textbox").last, text))
            step(page, "threads_送出回覆", lambda: page.get_by_role("button", name=rx(L["reply_send_button"])).last.click(timeout=t))
            page.wait_for_timeout(2000)
        finally:
            page.close()

    try:
        if ctx is not None:
            go(ctx)
        else:
            with browser.open_context() as c:
                go(c)
    except Exception as e:  # noqa: BLE001
        conn.execute("UPDATE social_leads SET status='failed', error=? WHERE id=?", (str(e)[:300], lead_id))
        conn.commit()
        raise
    conn.execute("UPDATE social_leads SET status='sent', draft=?, error='', sent_at=? WHERE id=?", (text, db.now(), lead_id))


_last_scan = 0.0


def auto_replies(conn) -> int:
    """背景：每 30 分鐘掃一次「最近 3 天發出且有貼文網址」的貼文，自動回有人問連結的留言（其他留言只產生草稿等你確認）。"""
    global _last_scan
    if time.time() - _last_scan < 1800:
        return 0
    _last_scan = time.time()
    since = (_now() - timedelta(days=3)).isoformat()
    posted = [r for r in rows(conn, "posted") if r["post_url"] and (r["posted_at"] or "") >= since]
    if not posted:
        return 0
    from . import browser

    with browser.open_context() as c:
        for r in posted:
            try:
                collect(conn, r["platform"], r["post_url"], r["product_id"], ctx=c)
                conn.commit()
            except Exception:  # noqa: BLE001
                continue
        return send_replies(conn, auto_only=True, ctx=c)
