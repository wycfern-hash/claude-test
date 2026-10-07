"""Threads / Facebook 粉絲專頁：用你登入的自動化 Chrome 發文，並可搜尋 Threads 留言/貼文後逐篇回覆（不用 API）。
- 發文：文字在上面，分潤連結放在「第一則留言」（Threads 是串文第二則、Facebook 是貼文下面的留言）。
- 只發有分潤連結的商品；一個商品在一個平台只發一次。
- 搜尋回覆：每一篇都要你按「回覆」才會送出；回覆內容不能放連結；每天最多 20 篇。
標籤與 CSS 在 config/social_sites.json；猜錯時看 data/debug 的診斷檔來修。
"""
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import config, db, posts, providers
from .webauto import rx, step

SITES_PATH = Path("config/social_sites.json")
PLATFORMS = {"threads": "Threads", "facebook": "Facebook 粉絲專頁"}
REPLY_DAILY_CAP = 20  # 搜尋回覆每天上限（保護你的帳號，不開放調整）


def site(name: str) -> dict:
    return json.loads(SITES_PATH.read_text(encoding="utf-8"))[name]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ------------------------------------------------------------------ 瀏覽器小工具
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
        raise RuntimeError(f"尚未登入 {PLATFORMS[platform]}：請先按「發文」頁上方的按鈕，在開出的 Chrome 登入")


def _attach(page, files: list[str], scope=None) -> None:
    inp = (scope or page).locator("input[type=file]")
    if inp.count():
        inp.first.set_input_files(files)
        page.wait_for_timeout(2500)


# ------------------------------------------------------------------ 發文
def link_of(row) -> str:
    return posts.link_of(row)


def images_for(row, limit: int = 4) -> list[str]:
    try:
        imgs = json.loads(row["selected_images"]) or ([row["selected_image"]] if row["selected_image"] else [])
    except ValueError:
        imgs = []
    return [str(config.DATA_DIR / i) for i in imgs if (config.DATA_DIR / i).exists()][:limit]


def is_posted(conn, pid: int, platform: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM social_posts WHERE product_id=? AND platform=? AND status='posted'", (pid, platform)).fetchone())


def _threads(ctx, L, text: str, comment: str, files: list[str], preview: bool) -> bool:
    t = L["step_timeout_sec"] * 1000
    page = ctx.new_page()
    keep = False
    try:
        page.goto(L["url"])
        page.wait_for_load_state("domcontentloaded")
        _check_login(page, L, "threads")
        step(page, "threads_開啟發文框", lambda: page.get_by_text(rx(L["composer_open"])).first.click(timeout=t))
        _human(page)
        step(page, "threads_填文字", lambda: type_text(page, page.get_by_role("textbox").last, text))
        if files:
            step(page, "threads_附圖", lambda: _attach(page, files), optional=True)
        step(page, "threads_新增串文", lambda: page.get_by_text(rx(L["add_to_thread"])).first.click(timeout=t))
        _human(page)
        step(page, "threads_填連結留言", lambda: type_text(page, page.get_by_role("textbox").last, comment))
        if preview:
            keep = True
            return False
        step(page, "threads_發佈", lambda: page.get_by_role("button", name=rx(L["post_button"])).last.click(timeout=t))
        page.wait_for_timeout(2500)
        return True
    finally:
        if not keep:
            page.close()


def _facebook(ctx, L, text: str, comment: str, files: list[str], preview: bool) -> bool:
    t = L["step_timeout_sec"] * 1000
    if not config.SOCIAL_FB_PAGE_URL:
        raise RuntimeError("還沒填粉絲專頁網址：請在「發文」頁上方填入你的粉絲專頁網址")
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
        step(page, "facebook_填文字", lambda: type_text(page, scope.get_by_role("textbox").first, text))
        if files:
            def attach():
                if not scope.locator("input[type=file]").count():
                    scope.get_by_role("button", name=rx(L["photo_button"])).first.click(timeout=t)
                _attach(page, files, scope)
            step(page, "facebook_附圖", attach, optional=True)
        step(page, "facebook_下一步", lambda: scope.get_by_role("button", name=rx(L["next_button"])).first.click(timeout=3000), optional=True)
        if preview:
            keep = True
            return False
        step(page, "facebook_發佈", lambda: scope.get_by_role("button", name=rx(L["post_button"])).last.click(timeout=t))
        page.wait_for_timeout(4000)
        art = page.locator("div[role=article]").first      # 發完後新貼文在動態最上面，就在原頁補留言

        def put_comment():
            box = art.get_by_role("textbox", name=rx(L["comment_box"])).first
            type_text(page, box, comment)
            page.keyboard.press("Enter")
            page.wait_for_timeout(2000)

        step(page, "facebook_留言放連結", put_comment)
        return True
    finally:
        if not keep:
            page.close()


def publish(conn, pid: int, platform: str, preview: bool = False, ctx=None) -> str:
    """把商品的第 1 則貼文發出去（preview=只貼好、停在發佈前讓你自己按）。回傳給人看的結果。"""
    from . import browser

    if platform not in PLATFORMS:
        raise ValueError("不支援的平台")
    row = db.get(conn, pid)
    items = posts.load(row).get("posts") or []
    if not items:
        raise RuntimeError("這個商品還沒有貼文：請先到「貼文」頁產生")
    if not link_of(row):
        raise RuntimeError("這個商品還沒有分潤連結，不會發（避免發出沒有分潤的貼文）")
    if is_posted(conn, pid, platform):
        raise RuntimeError(f"這個商品已經發過 {PLATFORMS[platform]} 了")
    text = posts.compose(row, items[0], False)
    comment = posts.compose_comment(row, items[0])
    files = images_for(row)
    fn = _threads if platform == "threads" else _facebook

    def go(c) -> bool:
        return fn(c, site(platform), text, comment, files, preview)

    if ctx is not None:
        done = go(ctx)
    else:
        with browser.open_context() as c:
            done = go(c)
    if not done:
        return "已貼好，停在發佈前：請看那個 Chrome 視窗，確認沒問題就自己按「發佈」。"
    conn.execute("INSERT OR REPLACE INTO social_posts (product_id,platform,text,comment,status,created_at,posted_at) VALUES (?,?,?,?,'posted',?,?)",
                 (pid, platform, text, comment, db.now(), db.now()))
    return f"已發到 {PLATFORMS[platform]}"


# ------------------------------------------------------------------ Threads：關鍵字搜尋 → 逐篇回覆
def search_threads(ctx, keyword: str, limit: int = 20) -> list[dict]:
    L = site("threads")
    page = ctx.new_page()
    try:
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
    """有選腳本 AI 才預寫一則有回應內容的短回覆（不放連結、不推銷）；沒選就留空讓你自己寫。"""
    if not providers.configured("text"):
        return ""
    try:
        s = providers.text_json(
            "你是台灣 Threads 的一般使用者，要回覆下面這篇貼文。請寫一則自然、友善、有點幽默、有回應到貼文內容的繁體中文口語回覆"
            f"（50 字內）。規定：不要放任何網址或商品連結、不要推銷或提到購買、不要編造個人經驗細節；沒有話可說就回空字串。\n"
            f"搜尋關鍵字：{keyword}\n貼文：{text[:300]}\n輸出 JSON：{{\"reply\":\"...\"}}")
        return str(s.get("reply", "")).strip()
    except Exception:  # noqa: BLE001
        return ""


def find_topics(conn, keyword: str, ctx=None) -> int:
    """搜尋並存下新的結果（同一篇不會重複出現）。回傳新增篇數。"""
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


def leads(conn, keyword: str = ""):
    q = "SELECT * FROM social_leads WHERE status IN ('new','failed')"
    if keyword:
        return conn.execute(q + " AND keyword=? ORDER BY id DESC", (keyword,)).fetchall()
    return conn.execute(q + " ORDER BY id DESC").fetchall()


def replied_today(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM social_leads WHERE status='sent' AND sent_at LIKE ?", (_today() + "%",)).fetchone()[0]


def send_lead(conn, lead_id: int, text: str, ctx=None) -> None:
    """回覆一篇貼文（只在你按那一篇的「回覆」時執行）。"""
    from . import browser

    r = conn.execute("SELECT * FROM social_leads WHERE id=?", (lead_id,)).fetchone()
    text = text.strip()
    if not r or r["status"] == "sent":
        raise RuntimeError("這篇已經回覆過了")
    if not text:
        raise RuntimeError("回覆內容是空的，請先寫一則回覆")
    if re.search(r"https?://|shp\.ee|s\.shopee", text, re.I):
        raise RuntimeError("回覆不能放連結（在別人的貼文貼商品連結會被當成垃圾訊息）")
    if replied_today(conn) >= REPLY_DAILY_CAP:
        raise RuntimeError(f"今天已經回了 {REPLY_DAILY_CAP} 篇，明天再繼續（保護你的帳號）")
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
