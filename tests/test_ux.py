"""新手流程：匯入後要有回饋、知道下一步；花錢的步驟要按「開始」才跑；不會再卡住沒反應。"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from shopee_clips import config, db, imagegen, providers, videogen, worker


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


CSV = "商品名稱,商品連結,分潤連結,價格\n保溫杯,https://shopee.tw/a-i.1.1,https://s.shopee.tw/x,300\n風扇,https://shopee.tw/b-i.2.2,https://s.shopee.tw/y,199\n".encode("utf-8-sig")


def wait_import(c, tries=100):
    from shopee_clips import web

    for _ in range(tries):
        if not web.IMPORT["running"]:
            return
        time.sleep(0.05)
    raise AssertionError("匯入沒有結束")


def test_import_shows_progress_then_result_with_next_step():
    from shopee_clips import web

    c = TestClient(app := __import__("shopee_clips.web", fromlist=["app"]).app)
    r = c.post("/import-file", files={"file": ("蝦皮.csv", CSV, "text/csv")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/import-status"           # 一定會被帶到結果頁，不是停在原地沒反應
    wait_import(c)
    page = c.get("/import-status").text
    assert "匯入完成：新增 <b>2</b>" in page and "按「開始自動處理」" in page            # 明確告訴使用者下一步
    assert web.IMPORT["result"]["added"] == 2


def test_import_failure_explains_why_and_shows_headers():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    c.post("/import-file", files={"file": ("x.csv", "水果,數量\n蘋果,3\n".encode("utf-8"), "text/csv")})
    wait_import(c)
    page = c.get("/import-status").text
    assert "沒有匯入任何商品" in page and "水果、數量" in page


def test_import_unreadable_file_does_not_hang():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    c.post("/import-file", files={"file": ("壞.xlsx", b"this is not a real xlsx", "application/octet-stream")})
    wait_import(c)
    assert "沒有匯入任何商品" in c.get("/import-status").text                             # 不會卡在「匯入中」


def test_home_is_a_guided_checklist_and_live_fragment():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    home = c.get("/").text
    for token in ("① 設定 AI 服務".replace("① ", ""), "匯入選品檔", "開始自動處理", "審圖", "審片"):
        assert token in home
    assert "sourced" not in home                                                         # 不再出現看不懂的英文狀態
    assert "前往設定" in home and "還沒有商品" in c.get("/fragment/live").text
    c.post("/import-file", files={"file": ("a.csv", CSV, "text/csv")})
    wait_import(c)
    live = c.get("/fragment/live").text
    assert "共 2 個商品" in live and "待產圖" in live and "開始" in live


def test_buttons_are_not_grey_disabled_looking():
    css = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app).get("/").text
    assert "button.g,.btn.g{background:#fff" in css and "background:#888" not in css      # 次要按鈕是白底黑框，看得出可以按


def test_flash_messages_show_once():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    c.post("/add", data={"urls": "https://shopee.tw/a-i.1.1\nhttps://shopee.tw/a-i.1.1"})
    first = c.get("/").text
    assert "已加入 1 個商品，重複略過 1 個" in first
    assert '<div class="flash ok">✅ 已加入' not in c.get("/").text                        # 提示框看過就消失（紀錄裡仍保留）


def test_auto_run_toggle_and_gate(monkeypatch):
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    called = []
    monkeypatch.setattr(imagegen, "run", lambda conn: called.append("img") or 0)
    monkeypatch.setattr(videogen, "run", lambda conn, allow_ai=True: called.append(("vid", allow_ai)) or 0)
    from shopee_clips import cloud, uploader

    monkeypatch.setattr(cloud, "run", lambda conn: 0)
    monkeypatch.setattr(uploader, "run", lambda conn, mode=None, connect_fn=None: called.append(("up", mode)) or 0)
    worker.tick()
    assert called == [("vid", False), ("up", "manual")]                                   # 沒按開始：不產圖、不產 AI 影片、不操作手機
    called.clear()
    c.post("/auto-run", data={"on": "1"})
    page = c.get("/").text
    assert config.AUTO_RUN and "圖片 AI」還沒設定好" in page and "步驟 ①" in page           # 沒設定 AI 就按開始：會明講為什麼不會產圖
    worker.tick()
    assert called == ["img", ("vid", True), ("up", None)]
    c.post("/auto-run", data={"on": "0"})
    assert not config.AUTO_RUN and "已暫停" in c.get("/").text
    config.save_env({"GEMINI_API_KEY": "k", "IMAGE_PROVIDER": "gemini", "IMAGE_MODEL": "m"})
    c.post("/auto-run", data={"on": "1"})
    assert "已開始自動處理" in c.get("/").text                                              # 設定好了：正常開始


def test_enrich_does_not_open_chrome_unless_enabled(monkeypatch):
    from shopee_clips import sourcing

    called = []
    monkeypatch.setattr(sourcing, "enrich", lambda conn: called.append(1) or 0)
    monkeypatch.setattr(imagegen, "run", lambda conn: 0)
    monkeypatch.setattr(videogen, "run", lambda conn, allow_ai=True: 0)
    worker.tick()
    assert called == []                                                                   # 預設不會偷偷開 Chrome
    config.save_env({"AUTO_ENRICH": "1"})
    worker.tick()
    assert called == [1]


def test_default_image_source_auto_never_gets_stuck(monkeypatch):
    config.save_env({"GEMINI_API_KEY": "k", "IMAGE_PROVIDER": "gemini", "IMAGE_MODEL": "m", "IMAGES_PER_PRODUCT": "1"})
    from io import BytesIO

    from PIL import Image

    def fake(prompt, refs):
        b = BytesIO()
        Image.new("RGB", (300, 400), "red").save(b, "PNG")
        return b.getvalue()

    monkeypatch.setattr(providers, "image_bytes", fake)
    with db.connect() as conn:
        pid = db.add_product(conn, "https://shopee.tw/a-i.1.1", title="保溫杯")                # 沒有任何參考圖
        assert imagegen.source_for(db.get(conn, pid)) == "ai"                               # 自動：沒圖就純 AI，不會永遠等
        assert imagegen.run(conn) == 1 and db.get(conn, pid)["status"] == "image_review"
        p2 = db.add_product(conn, "https://shopee.tw/b-i.2.2", title="風扇")
        d = config.REF_DIR / str(p2)
        d.mkdir(parents=True)
        Image.new("RGB", (300, 300), "blue").save(d / "0.jpg")
        assert imagegen.source_for(db.get(conn, p2)) == "web"                               # 有參考圖就用參考圖


def test_api_image_generation_respects_daily_cap(monkeypatch):
    config.save_env({"GEMINI_API_KEY": "k", "IMAGE_PROVIDER": "gemini", "IMAGE_MODEL": "m", "IMAGES_PER_PRODUCT": "1", "DAILY_GEN_CAP": "2"})
    calls = []
    from io import BytesIO

    from PIL import Image

    def fake(prompt, refs):
        calls.append(1)
        b = BytesIO()
        Image.new("RGB", (300, 400), "red").save(b, "PNG")
        return b.getvalue()

    monkeypatch.setattr(providers, "image_bytes", fake)
    with db.connect() as conn:
        for n in range(5):
            db.add_product(conn, f"https://shopee.tw/p-i.1.{n}", title=f"商品{n}")
        assert imagegen.run(conn) == 2 and len(calls) == 2                                  # 匯入 5 個，今天最多只花 2 個的錢
        assert imagegen.run(conn) == 0                                                      # 額度用完，不會繼續


def test_ai_video_waits_for_start(monkeypatch):
    config.save_env({"VIDEO_MODE": "ai", "VIDEO_PROVIDER": "veo"})
    with db.connect() as conn:
        pid = db.add_product(conn, "https://shopee.tw/a-i.1.1", title="杯")
        db.update(conn, pid, script=json.dumps({"user_points": ["a", "b", "c"]}))
        db.move(conn, pid, "image_approved", selected_image="x.png", selected_images='["x.png"]')
        assert videogen.run(conn, allow_ai=False) == 0
        assert db.get(conn, pid)["status"] == "image_approved" and db.get(conn, pid)["error"] == ""   # 沒按開始：不報錯、不花錢


def test_status_page_reports_environment():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    page = c.get("/status").text
    for token in ("Python", "ffmpeg", "Google Chrome", "腳本 AI", "圖片 AI", "自動處理", "把這一頁截圖給我"):
        assert token in page


def test_todo_page_explains_state_and_has_no_broken_engine_text():
    c = TestClient(__import__("shopee_clips.web", fromlist=["app"]).app)
    c.post("/import-file", files={"file": ("a.csv", CSV, "text/csv")})
    wait_import(c)
    todo = c.get("/todo").text
    assert "還沒設定圖片 AI" in todo and "引擎：，" not in todo and "尚未選擇 AI 影片服務" in todo
    config.save_env({"GEMINI_API_KEY": "k", "IMAGE_PROVIDER": "gemini", "IMAGE_MODEL": "m"})
    assert "按「▶ 開始自動處理」" in c.get("/todo").text
    config.save_env({"AUTO_RUN": "1"})
    assert "已開始自動處理" in c.get("/todo").text


def test_per_product_source_default_label_and_values():
    from shopee_clips.web import app

    c = TestClient(app)
    with db.connect() as conn:
        pid = db.add_product(conn, "https://shopee.tw/a-i.1.1", title="杯")
    todo = c.get("/todo").text
    assert "跟隨預設" in todo
    c.post(f"/products/{pid}/opts", data={"image_source": "ai", "character_id": "0", "back": "/todo"})
    with db.connect() as conn:
        assert db.get(conn, pid)["image_source"] == "ai"
    c.post(f"/products/{pid}/opts", data={"image_source": "", "character_id": "0", "back": "/todo"})
    with db.connect() as conn:
        assert db.get(conn, pid)["image_source"] == ""                                      # 回到跟隨預設
