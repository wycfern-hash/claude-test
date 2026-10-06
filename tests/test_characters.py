import html
import http.server
import io
import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from shopee_clips import characters, config, db, imagegen, imgsearch, providers, scriptgen


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name, sub in (("DATA_DIR", ""), ("DB_PATH", "clips.db"), ("REF_DIR", "ref"), ("IMG_DIR", "images"),
                      ("VID_DIR", "videos"), ("BROWSER_PROFILE", "bp")):
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    for k in [n for n, _, _ in config.SPEC]:
        monkeypatch.delenv(k, raising=False)
    config.reload()
    yield
    config.reload()


def png(color="red", size=(400, 500)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, "PNG")
    return b.getvalue()


def add(conn, n=1, **kw):
    pid = db.add_product(conn, f"https://shopee.tw/p-i.1.{n}", title=f"保溫杯{n}", description="內膽304不鏽鋼。保溫12小時。")
    if kw:
        db.update(conn, pid, **kw)
    return pid


def char_id(conn, key):
    return conn.execute("SELECT id FROM characters WHERE key=?", (key,)).fetchone()["id"]


# ---------------------------------------------------------------- 主角資料與預設
def test_builtin_characters_seeded_and_default_applied():
    with db.connect() as conn:
        keys = {c["key"] for c in db.list_characters(conn)}
        assert {"hands", "girl20", "guy20"} <= keys and all(c["builtin"] for c in db.list_characters(conn))
        assert db.get(conn, add(conn, 1))["character_id"] == 0              # 預設不加人物
        gid = char_id(conn, "girl20")
    config.save_env({"DEFAULT_CHARACTER_ID": str(gid)})
    with db.connect() as conn:
        assert db.get(conn, add(conn, 2))["character_id"] == gid            # 新商品套用預設主角


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3

    c = sqlite3.connect(config.DB_PATH)
    c.execute("CREATE TABLE products (id INTEGER PRIMARY KEY AUTOINCREMENT, shopee_key TEXT UNIQUE, url TEXT, title TEXT DEFAULT '',"
              " price TEXT DEFAULT '', description TEXT DEFAULT '', ref_images TEXT DEFAULT '[]', status TEXT DEFAULT 'sourced',"
              " images TEXT DEFAULT '[]', selected_image TEXT DEFAULT '', selected_images TEXT DEFAULT '[]', script TEXT DEFAULT '{}',"
              " video_path TEXT DEFAULT '', video_title TEXT DEFAULT '', video_caption TEXT DEFAULT '', error TEXT DEFAULT '',"
              " created_at TEXT, updated_at TEXT, uploaded_at TEXT)")
    c.commit()
    c.close()
    with db.connect() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(products)")}
    assert {"video_mode", "character_id", "image_source", "ref_notes"} <= cols


# ---------------------------------------------------------------- 提示詞與產圖
def test_prompts_include_character_rules_and_portrait():
    with db.connect() as conn:
        pid = add(conn)
        row = db.get(conn, pid)
        assert "不要出現清楚的人臉" in imagegen.prompts_for(row)[0]          # 沒主角：不出現人臉
        db.update(conn, pid, character_id=char_id(conn, "girl20"))
        row = db.get(conn, pid)
        p = imagegen.prompts_for(row)[0]
        assert "mid-20s" in p and "不要出現清楚的人臉" not in p and "主角參考圖" not in p
        conn.execute("UPDATE characters SET ref_image='characters/1.jpg' WHERE key='girl20'")
    assert "主角參考圖" in imagegen.prompts_for(row)[0]                       # 有形象照：要求同一個人
    with db.connect() as conn:
        db.update(conn, pid, character_id=char_id(conn, "hands"))
        assert "只出現手與前臂" in imagegen.prompts_for(db.get(conn, pid))[0]  # 只露手：不露臉


def test_generate_passes_portrait_last_and_web_source_needs_refs(monkeypatch):
    config.save_env({"IMAGES_PER_PRODUCT": "2"})
    seen = []
    monkeypatch.setattr(providers, "image_bytes", lambda prompt, refs: seen.append((prompt, [p.name for p in refs])) or png())
    with db.connect() as conn:
        pid = add(conn)
        gid = char_id(conn, "girl20")
        d = config.DATA_DIR / "characters"
        d.mkdir()
        (d / f"{gid}.jpg").write_bytes(png("blue"))
        conn.execute("UPDATE characters SET ref_image=? WHERE id=?", (f"characters/{gid}.jpg", gid))
        db.update(conn, pid, character_id=gid)
        conn.commit()  # 主角查詢走獨立連線（正式流程是網頁儲存時就已提交）
        with pytest.raises(RuntimeError, match="尚無參考圖"):                  # 上網找圖模式沒參考圖 → 不能產
            imagegen.generate_for(conn, db.get(conn, pid))
        ref = config.REF_DIR / str(pid)
        ref.mkdir(parents=True)
        (ref / "0.jpg").write_bytes(png("green"))
        imgs = imagegen.generate_for(conn, db.get(conn, pid))
    assert len(imgs) == 2 and seen[0][1] == ["0.jpg", f"{gid}.jpg"]            # 商品參考圖在前，主角形象照最後


def test_ai_source_needs_no_refs_and_uses_text(monkeypatch):
    config.save_env({"IMAGES_PER_PRODUCT": "2"})
    seen = []
    monkeypatch.setattr(providers, "image_bytes", lambda prompt, refs: seen.append((prompt, list(refs))) or png())
    with db.connect() as conn:
        pid = add(conn, image_source="ai", script=json.dumps({"user_points": ["好清洗"]}, ensure_ascii=False))
        imagegen.generate_for(conn, db.get(conn, pid))
    prompt, refs = seen[0]
    assert refs == [] and "沒有參考照片" in prompt and "保溫杯1" in prompt and "304" in prompt and "好清洗" in prompt
    assert "不要自行加上品牌 logo" in prompt


def test_run_waits_silently_for_refs_but_ai_source_runs(monkeypatch):
    config.save_env({"GEMINI_API_KEY": "k", "IMAGE_PROVIDER": "gemini", "IMAGE_MODEL": "m", "IMAGES_PER_PRODUCT": "1"})
    monkeypatch.setattr(providers, "image_bytes", lambda prompt, refs: png())
    with db.connect() as conn:
        waiting, ai = add(conn, 1), add(conn, 2, image_source="ai")
        assert imagegen.run(conn) == 1
        assert db.get(conn, waiting)["status"] == "sourced" and db.get(conn, waiting)["error"] == ""   # 等你去找圖，不報錯
        assert db.get(conn, ai)["status"] == "image_review"


def test_collect_refs_prefers_picked():
    with db.connect() as conn:
        pid = add(conn)
    d = config.REF_DIR / str(pid)
    d.mkdir(parents=True)
    for n in ("0.jpg", "1.jpg", "w0.jpg"):
        (d / n).write_bytes(png())
    with db.connect() as conn:
        assert [p.name for p in imagegen.collect_refs(db.get(conn, pid))] == ["w0.jpg"]    # 你挑的優先於商品頁抓的


# ---------------------------------------------------------------- 腳本 / 配音
def test_script_and_video_prompts_mention_character(monkeypatch):
    config.save_env({"TEXT_PROVIDER": "gemini", "GEMINI_API_KEY": "k", "TEXT_MODEL": "m", "VIDEO_PROVIDER": "fal",
                     "FAL_KEY": "f", "VIDEO_MODEL": "m"})
    got = {}

    def fake(prompt, images=None):
        got["prompt"] = prompt
        return {"hook": "h", "selling_points": ["a", "b", "c"], "cta": "c", "voiceover": "v", "video_title": "t",
                "caption": "c", "hashtags": [], "video_prompts": ["p"]}

    monkeypatch.setattr(providers, "text_json", fake)
    with db.connect() as conn:
        pid = add(conn, character_id=char_id(conn, "guy20"))
        row = db.get(conn, pid)
        scriptgen.get_script(row, "ai")
        assert "short neat black hair" in got["prompt"] and "主角" in got["prompt"]
        tpl = scriptgen.from_template(db.get(conn, add(conn, 2, character_id=char_id(conn, "hands"),
                                                       script=json.dumps({"user_points": ["a"]}))))
        assert all("hands and forearms" in p for p in tpl["video_prompts"][:2])


def test_voice_follows_character(monkeypatch):
    from shopee_clips import slideshow, videogen

    config.save_env({"VIDEO_MODE": "slideshow"})
    voices = []

    def fake_build(imgs, script, out, voice=None):
        voices.append(voice)
        out.write_bytes(b"x")

    monkeypatch.setattr(slideshow, "build", fake_build)
    with db.connect() as conn:
        for n, key in ((1, "guy20"), (2, None)):
            pid = add(conn, n, script=json.dumps({"user_points": ["a", "b", "c"]}), character_id=char_id(conn, key) if key else 0)
            db.move(conn, pid, "image_approved", selected_image="x.png", selected_images='["x.png"]')
        videogen.run(conn)
    assert voices == ["zh-TW-YunJheNeural", None]                              # 沒主角 → 用設定頁預設（曉臻）


# ---------------------------------------------------------------- 網頁
def test_character_pages_and_options():
    from shopee_clips.web import app

    c = TestClient(app)
    html_ = c.get("/characters").text
    assert "只露手（不露臉）" in html_ and "虛構 AI 角色" in html_ and "不要使用他人肖像" in html_
    r = c.post("/characters/new", data={"name": "我自己", "voice": "zh-TW-YunJheNeural", "description": ""},
               files={"file": ("me.png", png("pink", (500, 600)), "image/png")})
    assert r.status_code == 200
    with db.connect() as conn:
        me = conn.execute("SELECT * FROM characters WHERE name='我自己'").fetchone()
        assert me and me["ref_image"] and (config.DATA_DIR / me["ref_image"]).exists() and "reference image" in me["description"]
        assert c.post("/characters/new", data={"name": "空的", "description": ""}).status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM characters WHERE name='空的'").fetchone()[0] == 0   # 沒描述沒照片 → 不新增
        pid = add(conn)
    c.post("/characters/default", data={"character_id": str(me["id"])})
    assert config.DEFAULT_CHARACTER_ID == me["id"]
    # 每個商品的圖片來源 + 主角
    c.post(f"/products/{pid}/opts", data={"image_source": "ai", "character_id": str(me["id"]), "back": "/todo"})
    with db.connect() as conn:
        r = db.get(conn, pid)
        assert (r["image_source"], r["character_id"]) == ("ai", me["id"])
    todo = c.get("/todo").text
    assert "純 AI 生成（不使用任何參考圖）" in todo and "我自己" in todo and "上網找圖" not in todo.split("<form method=post enctype")[1]
    # 批次套用
    with db.connect() as conn:
        p2 = add(conn, 2)
    c.post("/apply-defaults", data={"image_source": "ai", "character_id": "0"})
    with db.connect() as conn:
        assert db.get(conn, p2)["image_source"] == "ai" and db.get(conn, p2)["character_id"] == 0
    # 刪除自訂主角 → 商品恢復不加人物；內建不可刪
    c.post(f"/characters/{me['id']}/delete")
    with db.connect() as conn:
        assert db.get(conn, pid)["character_id"] == 0 and not conn.execute("SELECT 1 FROM characters WHERE id=?", (me["id"],)).fetchone()
        hid = char_id(conn, "hands")
    c.post(f"/characters/{hid}/delete")
    with db.connect() as conn:
        assert conn.execute("SELECT 1 FROM characters WHERE id=?", (hid,)).fetchone()
    assert config.DEFAULT_CHARACTER_ID == 0


def test_no_open_redirect():
    from shopee_clips.web import app

    c = TestClient(app)
    with db.connect() as conn:
        pid = add(conn)
    r = c.post(f"/products/{pid}/opts", data={"back": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_find_page_pick_upload_and_notes():
    from shopee_clips.web import app

    c = TestClient(app)
    with db.connect() as conn:
        pid = add(conn)
    cd = imgsearch.cand_dir(pid)
    cd.mkdir(parents=True)
    for n in range(2):
        (cd / f"c{n}.jpg").write_bytes(png(size=(300, 300)))
    (cd / "meta.json").write_text(json.dumps([{"file": f"c{n}.jpg", "source": f"http://img/{n}.jpg", "engine": "bing"} for n in range(2)]))
    page = c.get(f"/find/{pid}").text
    assert "只當 AI 產圖的參考" in page and "c0.jpg" in page and "Google" in page and "Yahoo" in page
    c.post(f"/find/{pid}/add", data={"files": ["c1.jpg", "../../etc/passwd"]})          # 非候選檔名會被忽略
    ref = config.REF_DIR / str(pid)
    assert [p.name for p in ref.glob("w*.jpg")] == ["w0.jpg"]
    c.post(f"/find/{pid}/upload", files=[("files", ("mine.png", png(), "image/png")), ("files", ("bad.txt", b"nope", "text/plain"))])
    assert sorted(p.name for p in ref.glob("w*.jpg")) == ["w0.jpg", "w1.jpg"]
    with db.connect() as conn:
        notes = db.get(conn, pid)["ref_notes"]
    assert "bing：http://img/1.jpg" in notes and "自行上傳 1 張" in notes                # 來源紀錄
    c.post(f"/find/{pid}/notes", data={"notes": "已取得授權"})
    with db.connect() as conn:
        assert db.get(conn, pid)["ref_notes"] == "已取得授權"
    assert c.get(f"/find/{pid}?msg=ok").status_code == 200


# ---------------------------------------------------------------- Excel
def test_excel_character_and_source_columns(tmp_path):
    from openpyxl import Workbook

    from shopee_clips import sourcing

    wb = Workbook()
    ws = wb.active
    ws.append(["商品連結", "商品名稱", "主角", "圖片來源"])
    ws.append(["https://shopee.tw/a-i.1.1", "A", "只露手（不露臉）", "AI 生成"])
    ws.append(["https://shopee.tw/b-i.1.2", "B", "不存在的人", "上網找"])
    wb.save(tmp_path / "x.xlsx")
    with db.connect() as conn:
        sourcing.import_excel(conn, str(tmp_path / "x.xlsx"))
        a = conn.execute("SELECT * FROM products WHERE shopee_key='1.1'").fetchone()
        b = conn.execute("SELECT * FROM products WHERE shopee_key='1.2'").fetchone()
        assert (a["character_id"], a["image_source"]) == (char_id(conn, "hands"), "ai")
        assert (b["character_id"], b["image_source"]) == (0, "web")


# ---------------------------------------------------------------- 「AI 生成」標示
def test_ai_label_overlay(tmp_path):
    from shopee_clips import slideshow

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=white:s=1080x1920:d=1:r=10", "-pix_fmt", "yuv420p", str(v)],
                   check=True, capture_output=True)

    def corner_is_dark():
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(v), "-frames:v", "1", "-vf", "crop=200:60:40:100,scale=1:1,format=gray",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        return raw[0] < 200

    config.save_env({"AI_LABEL": "0"})
    slideshow.apply_ai_label(v)
    assert not corner_is_dark()                                                   # 關閉：不動
    config.save_env({"AI_LABEL": "1"})
    slideshow.apply_ai_label(v)
    assert corner_is_dark()                                                       # 開啟：左上角出現標示


# ---------------------------------------------------------------- 上網找圖（用假搜尋頁驗證 Chrome 流程）
CHROME = next(iter(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)


@pytest.fixture
def mock_search():
    big, small = png("orange", (600, 800)), png("gray", (40, 40))

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            port = self.server.server_port
            if self.path.startswith("/img/"):
                data = small if self.path.endswith("tiny.png") else big
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
            elif self.path.startswith("/bing"):
                items = [{"murl": f"http://127.0.0.1:{port}/img/{i}.png", "turl": f"http://127.0.0.1:{port}/img/t{i}.png"} for i in range(3)]
                items.append({"murl": f"http://127.0.0.1:{port}/img/tiny.png", "turl": ""})   # 太小的圖會被濾掉
                data = ("<html><body>" + "".join(f'<a class="iusc" m="{html.escape(json.dumps(m))}">x</a>' for m in items)
                        + "</body></html>").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            else:  # google 類：頁面上直接是 data URI 縮圖
                import base64
                src = "data:image/png;base64," + base64.b64encode(big).decode()
                data = f'<html><body><img src="{src}" width=300 height=400><img src="{src}" width=300 height=400></body></html>'.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_image_search_collects_candidates_and_picks(monkeypatch, mock_search):
    from shopee_clips import browser

    monkeypatch.setattr(config, "CHROME_PATH", str(CHROME))
    monkeypatch.setattr(config, "CHROME_HEADLESS", True)
    monkeypatch.setattr(config, "CDP_PORT", 9335)
    monkeypatch.setitem(imgsearch.ENGINES, "bing", mock_search + "/bing?q={q}")
    monkeypatch.setitem(imgsearch.ENGINES, "google", mock_search + "/google?q={q}")
    with db.connect() as conn:
        pid = add(conn)
    try:
        with browser.open_context() as ctx:
            n_bing = imgsearch.search(ctx, pid, "bing", "保溫杯")
            bing_meta = imgsearch.candidates(pid)
            n_google = imgsearch.search(ctx, pid, "google", "保溫杯")
    finally:
        subprocess.run(["pkill", "-f", "remote-debugging-por[t]=9335"])
    assert n_bing == 3 and all(m["source"].startswith("http://127.0.0.1") and m["engine"] == "bing" for m in bing_meta)
    assert n_google == 2 and len(imgsearch.candidates(pid)) == 2                  # 重新搜尋會換掉舊候選
    with db.connect() as conn:
        assert imgsearch.add_picks(conn, pid, ["c0.jpg"]) == 1
        assert "上網找圖" in db.get(conn, pid)["ref_notes"]
    assert Image.open(config.REF_DIR / str(pid) / "w0.jpg").size == (600, 800)


def test_enrich_is_not_retried_forever():
    from shopee_clips import sourcing

    with db.connect() as conn:
        pid = add(conn)                                         # 有標題、沒參考圖、預設「上網找圖」
        assert sourcing.needs_enrich(db.get(conn, pid))
        db.update(conn, pid, error="enrich: 商品頁沒抓到圖片")
        assert not sourcing.needs_enrich(db.get(conn, pid))     # 試過就不再每分鐘重開瀏覽器
        db.update(conn, pid, error="", image_source="ai")
        assert not sourcing.needs_enrich(db.get(conn, pid))     # 純 AI 生成且已有標題 → 不需要
        p2 = db.add_product(conn, "https://shopee.tw/q-i.1.77")  # 沒標題 → 還是要補
        db.update(conn, p2, image_source="ai")
        assert sourcing.needs_enrich(db.get(conn, p2))
