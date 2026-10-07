"""臉書／Threads 情境貼文：有分潤連結才放連結、沒有就放提示（不拿一般連結冒充）、含揭露、可匯出。"""
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from shopee_clips import config, db, posts, providers, worker


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


def add(conn, url, source="", **kw):
    pid = db.add_product(conn, url, title=kw.get("title", "保溫杯"), price="300",
                         description=kw.get("description", "雙層真空保溫。杯蓋可密封防漏。容量 500ml"), source_url=source)
    return pid


def test_template_posts_have_three_styles_and_disclosure_and_link():
    with db.connect() as conn:
        pid = add(conn, "https://shopee.tw/a-i.1.1", "https://s.shopee.tw/AbC")
        data = posts.generate(conn, pid)
        row = db.get(conn, pid)
    assert [p["style"] for p in data["posts"]] == ["story", "dialog", "pain"] and data["threads"]
    full = posts.compose(row, data["posts"][0], True)
    assert "https://s.shopee.tw/AbC" in full and config.POST_DISCLOSURE in full
    assert "https://s.shopee.tw/AbC" not in posts.compose(row, data["posts"][0], False)
    assert "https://s.shopee.tw/AbC" in posts.compose_comment(row, data["posts"][0])
    assert posts.load(row)["posts"][1]["text"].startswith("A：")


def test_plain_link_is_never_used_as_affiliate():
    with db.connect() as conn:
        pid = add(conn, "https://shopee.tw/a-i.1.1")
        data = posts.generate(conn, pid)
        row = db.get(conn, pid)
    assert posts.link_of(row) == ""
    assert posts.PLACEHOLDER in posts.compose(row, data["posts"][0], True)
    assert "https://shopee.tw/a-i.1.1" not in posts.compose(row, data["posts"][0], True)


def test_no_points_and_no_ai_explains_what_to_do():
    with db.connect() as conn:
        pid = add(conn, "https://shopee.tw/a-i.1.1", description="")
        with pytest.raises(RuntimeError, match="沒有賣點"):
            posts.generate(conn, pid)


def test_llm_posts_used_when_text_provider_configured(monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda role: role == "text")
    seen = {}

    def fake(prompt, images=None):
        seen["prompt"] = prompt
        return {"posts": [{"style": "story", "text": "週一早上趕上班……", "comment": "連結在這"},
                          {"style": "dialog", "text": "A：欸\nB：嗯", "comment": ""},
                          {"style": "pain", "text": "你是不是也……", "comment": "看這裡"}], "threads": "短文"}

    monkeypatch.setattr(providers, "text_json", fake)
    with db.connect() as conn:
        pid = add(conn, "https://shopee.tw/a-i.1.1", "https://s.shopee.tw/x")
        data = posts.generate(conn, pid)
    assert data["posts"][0]["text"].startswith("週一") and data["threads"] == "短文"
    assert "雙層真空保溫" in seen["prompt"] and "不要編造" in seen["prompt"] and "網址" in seen["prompt"]


def test_llm_failure_falls_back_to_template(monkeypatch):
    monkeypatch.setattr(providers, "configured", lambda role: role == "text")
    monkeypatch.setattr(providers, "text_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    with db.connect() as conn:
        pid = add(conn, "https://shopee.tw/a-i.1.1", "https://s.shopee.tw/x")
        data = posts.generate(conn, pid)
    assert len(data["posts"]) == 3


def test_posts_page_generate_and_csv(monkeypatch):
    from shopee_clips import web

    with db.connect() as conn:
        add(conn, "https://shopee.tw/a-i.1.1", "https://s.shopee.tw/AbC")
        add(conn, "https://shopee.tw/b-i.2.2", title="風扇")
    c = TestClient(web.app)
    assert "產生貼文" in c.get("/posts").text and "貼文" in c.get("/").text
    r = c.post("/posts/1", follow_redirects=False)
    assert r.status_code == 303
    page = c.get("/posts").text
    assert "生活小故事" in page and "對話情境劇" in page and "https://s.shopee.tw/AbC" in page and "重新產生" in page
    rows = list(csv.reader(io.StringIO(c.get("/posts.csv").content.decode("utf-8-sig"))))
    assert rows[0][0] == "商品ID" and len(rows) == 1 + 4  # 3 則貼文 + Threads
    assert all(r[6] == "https://s.shopee.tw/AbC" for r in rows[1:])
    monkeypatch.setattr(posts, "generate_missing", lambda: (0, 0, ""))
    assert c.post("/posts/all", follow_redirects=False).status_code == 303


def test_generate_missing_skips_done_and_failures_do_not_stop_batch():
    with db.connect() as conn:
        a = add(conn, "https://shopee.tw/a-i.1.1")
        add(conn, "https://shopee.tw/b-i.2.2", title="空的", description="")  # 沒賣點 → 失敗
        add(conn, "https://shopee.tw/c-i.3.3", title="風扇")
        posts.generate(conn, a)
    ok, bad, err = posts.generate_missing()
    assert (ok, bad) == (1, 1) and "沒有賣點" in err
    assert posts.generate_missing()[:2] == (0, 1)  # 已有貼文的不重產；沒賣點的仍失敗


def test_settings_page_has_disclosure_and_saves():
    from shopee_clips import web

    c = TestClient(web.app)
    assert "POST_DISCLOSURE" in c.get("/settings").text
    c.post("/settings", data={"POST_DISCLOSURE": "本文含分潤連結"}, follow_redirects=False)
    assert config.POST_DISCLOSURE == "本文含分潤連結"
