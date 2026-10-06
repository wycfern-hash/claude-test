import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from shopee_clips import cloud, config, db, phone, uploader


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name, sub in (("DATA_DIR", ""), ("DB_PATH", "clips.db"), ("REF_DIR", "ref"), ("IMG_DIR", "images"),
                      ("VID_DIR", "videos"), ("BROWSER_PROFILE", "bp")):
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(config, "ENV_PATH", tmp_path / ".env")
    for k in [n for n, _, _ in config.SPEC]:
        monkeypatch.delenv(k, raising=False)
    config.reload()
    monkeypatch.setattr(time, "sleep", lambda s: None)          # 測試不真的等
    yield
    config.reload()


class FakeEl:
    def __init__(self, dev, kw):
        self.dev, self.kw = dev, kw

    def wait(self, timeout=0):
        return self.dev.visible(self.kw)

    def wait_gone(self, timeout=0):
        return not self.dev.visible(self.kw)

    def click(self):
        self.dev.log.append(("click", self.kw))
        self.dev.on_click(self.kw)


class FakeDevice:
    """假的 uiautomator2 裝置：screen 是目前畫面上的文字集合，點到按鈕可以換畫面。"""

    def __init__(self, screens, start="home"):
        self.screens, self.screen, self.log, self.pushed, self.shells, self.typed = screens, start, [], [], [], []

    def visible(self, kw):
        texts = self.screens[self.screen]["texts"]
        for key in ("textMatches", "descriptionMatches", "resourceIdMatches"):
            if key in kw:
                return any(re.fullmatch(kw[key], t, re.S) for t in texts)
        return False

    def __call__(self, **kw):
        return FakeEl(self, kw)

    def on_click(self, kw):
        for texts, nxt in self.screens[self.screen].get("go", []):
            if self.visible(kw) and any(re.fullmatch(kw[next(k for k in kw if k.endswith("Matches"))], t, re.S) for t in texts):
                self.screen = nxt
                return

    def window_size(self):
        return (1000, 2000)

    def click_xy(self, x, y):
        self.log.append(("xy", x, y))

    def send_keys(self, text, clear=True):
        self.typed.append(text)

    def app_start(self, pkg, stop=True):
        self.log.append(("start", pkg))

    def app_current(self):
        return {"package": "com.shopee.tw"}

    def press(self, k):
        self.log.append(("press", k))

    def push(self, src, dst):
        self.pushed.append((src, dst))

    def shell(self, cmd):
        self.shells.append(cmd)

    def screenshot(self, path):
        open(path, "wb").write(b"png")

    def dump_hierarchy(self):
        return ('<hierarchy><node text="發佈" content-desc="" resource-id="a:id/b" class="android.widget.Button" clickable="true" bounds="[0,0][10,10]"/>'
                '<node text="" content-desc="" class="android.view.View" clickable="false"/></hierarchy>')


# FakeDevice.click 要同時支援 d.click(x, y)
FakeDevice.click = FakeDevice.click_xy


def device():
    return FakeDevice({
        "home": {"texts": ["影片", "首頁"], "go": [(["影片"], "videos")]},
        "videos": {"texts": ["新增", "影片牆"], "go": [(["新增"], "post")]},
        "post": {"texts": ["說明", "標記商品", "發佈"], "go": [(["標記商品"], "tag"), (["發佈"], "done")]},
        "tag": {"texts": ["搜尋", "選擇"], "go": [(["選擇"], "post")]},
        "done": {"texts": ["發佈成功"]},
    })


STEPS = {
    "gap_seconds": [0, 0], "between_videos_seconds": [0, 0], "default_timeout": 1, "delete_after_success": True,
    "steps": [
        {"name": "開蝦皮", "launch": True},
        {"name": "影片分頁", "tap": {"text": "影片|Video"}},
        {"name": "略過這步", "tap": {"text": "不存在的按鈕"}, "optional": True},
        {"name": "新增", "tap": {"text": "新增"}},
        {"name": "選片", "tap_ratio": [0.25, 0.5]},
        {"name": "填文案", "type": {"into": {"text": "說明"}, "text": "{caption}"}},
        {"name": "標記商品", "tap": {"text": "標記商品"}},
        {"name": "貼連結", "type": {"into": {"text": "搜尋"}, "text": "{product_link}"}},
        {"name": "選商品", "tap": {"text": "選擇"}},
        {"name": "發佈", "tap": {"text": "發佈"}, "publish": True},
        {"name": "成功", "wait": {"text": "成功"}, "publish": True},
    ],
}


def setup_steps(tmp_path, monkeypatch, steps=None):
    f = tmp_path / "steps.json"
    f.write_text(json.dumps(steps or STEPS, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(config, "PHONE_STEPS_PATH", f)


def approved(conn, n=1, **kw):
    pid = db.add_product(conn, f"https://shopee.tw/p-i.1.{n}", title=f"保溫杯{n}", source_url=f"https://s.shopee.tw/aff{n}")
    v = config.DATA_DIR / "videos" / str(pid)
    v.mkdir(parents=True, exist_ok=True)
    (v / "final.mp4").write_bytes(b"video")
    db.update(conn, pid, video_path=f"videos/{pid}/final.mp4", video_title="標題", video_caption="文案 #開箱", **kw)
    db.move(conn, pid, "image_review")
    db.move(conn, pid, "image_approved")
    db.move(conn, pid, "video_review")
    db.move(conn, pid, "video_approved")
    return pid


def test_subst_only_known_variables():
    assert phone.subst("{caption} {unknown} {x}", {"caption": "好", "x": 1}) == "好 {unknown} 1"


def test_variables_use_original_affiliate_link():
    with db.connect() as conn:
        pid = approved(conn)
        v = phone.variables(db.get(conn, pid), "1.mp4")
    assert v["product_link"] == "https://s.shopee.tw/aff1" and v["product_url"].endswith("i.1.1")
    assert v["caption"] == "標題\n文案 #開箱" and v["product_title"] == "保溫杯1"


def test_full_run_publishes_and_cleans_up(tmp_path, monkeypatch):
    setup_steps(tmp_path, monkeypatch)
    config.save_env({"UPLOAD_MODE": "phone_auto"})
    d = device()
    with db.connect() as conn:
        pid = approved(conn)
        assert uploader.run(conn, connect_fn=lambda: d) == 1
        assert db.get(conn, pid)["status"] == "uploaded"
    assert d.pushed[0][1] == "/sdcard/Movies/ShopeeClips/1.mp4"
    assert any("MEDIA_SCANNER_SCAN_FILE" in c for sh in d.shells for c in sh)         # 掃描讓相簿看到
    assert ["rm", "-f", "/sdcard/Movies/ShopeeClips/1.mp4"] in d.shells               # 成功後清掉手機上的檔案
    assert d.typed == ["標題\n文案 #開箱", "https://s.shopee.tw/aff1"]                  # 文案、分潤連結
    assert ("start", "com.shopee.tw") in d.log and ("xy", 250, 1000) in d.log
    assert d.screen == "done"


def test_dryrun_stops_before_publish(tmp_path, monkeypatch):
    setup_steps(tmp_path, monkeypatch)
    d = device()
    with db.connect() as conn:
        pid = approved(conn)
        assert uploader.run(conn, "phone_dryrun", connect_fn=lambda: d) == 0
        assert db.get(conn, pid)["status"] == "video_approved"                         # 沒發佈，狀態不變
    assert d.screen == "post" and d.typed                                               # 停在發佈前的畫面，欄位都填好了


def test_failure_saves_diagnostics_exports_package_and_stops_batch(tmp_path, monkeypatch):
    broken = json.loads(json.dumps(STEPS))
    broken["steps"][3] = {"name": "新增", "tap": {"text": "找不到的新增"}}                 # 蝦皮改版 → 找不到按鈕
    setup_steps(tmp_path, monkeypatch, broken)
    config.save_env({"UPLOAD_MODE": "phone_auto"})
    d = device()
    with db.connect() as conn:
        p1, p2 = approved(conn, 1), approved(conn, 2)
        assert uploader.run(conn, connect_fn=lambda: d) == 0
        r1, r2 = db.get(conn, p1), db.get(conn, p2)
    assert r1["status"] == "failed" and "第 4 步「新增」" in r1["error"] and "診斷檔" in r1["error"]
    assert r2["status"] == "video_approved" and len(d.pushed) == 1                      # 出錯就停，不在亂掉的 App 上繼續發下一支
    dbg = list((config.DATA_DIR / "debug").glob("phone_*"))
    assert any(p.suffix == ".png" for p in dbg) and any(p.suffix == ".json" for p in dbg)
    assert (config.DATA_DIR / "ready" / str(p1) / "video.mp4").exists()                 # 備援上架包
    assert "https://s.shopee.tw/aff1" in (config.DATA_DIR / "ready" / str(p1) / "文案.txt").read_text(encoding="utf-8")


def test_daily_cap_and_manual_mode(tmp_path, monkeypatch):
    setup_steps(tmp_path, monkeypatch)
    config.save_env({"UPLOAD_MODE": "phone_auto", "DAILY_UPLOAD_CAP": "1"})
    d = device()
    with db.connect() as conn:
        p1, p2 = approved(conn, 1), approved(conn, 2)
        d2 = device()

        def conn_fn():
            return d if not d.pushed else d2

        assert uploader.run(conn, connect_fn=lambda: d) == 1
        assert uploader.run(conn, connect_fn=lambda: d2) == 0                            # 今天額度用完
        assert (db.get(conn, p1)["status"], db.get(conn, p2)["status"]) == ("uploaded", "video_approved")
        assert uploader.run(conn, "manual") == 0                                          # manual：只匯出上架包
        assert (config.DATA_DIR / "ready" / str(p2) / "video.mp4").exists()


def test_probe_parses_hierarchy():
    base = phone.probe(device(), "x y")
    data = json.loads(open(base + ".json", encoding="utf-8").read())
    assert data["app"]["package"] == "com.shopee.tw"
    assert data["elements"] == [{"text": "發佈", "desc": "", "id": "a:id/b", "class": "Button", "clickable": True, "bounds": "[0,0][10,10]"}]


def test_connect_error_is_helpful(monkeypatch):
    import uiautomator2

    def boom(*a, **k):
        raise RuntimeError("no device")

    monkeypatch.setattr(uiautomator2, "connect", boom)
    with pytest.raises(phone.PhoneError, match="USB 偵錯.*安全設定"):
        phone.connect()


def test_default_steps_file_is_valid():
    meta = json.loads(open("config/shopee_app_steps.json", encoding="utf-8").read())
    assert meta["steps"][0].get("launch") and any(s.get("publish") for s in meta["steps"])
    for s in meta["steps"]:
        for key in ("tap", "wait", "wait_gone"):
            for pat in (s.get(key) or {}).values():
                if isinstance(pat, str):
                    re.compile(pat)
        if "type" in s and s["type"].get("into"):
            for pat in s["type"]["into"].values():
                re.compile(pat)


def test_legacy_upload_modes_map_to_phone():
    config.save_env({"UPLOAD_MODE": "dryrun"})
    assert config.UPLOAD_MODE == "phone_dryrun"
    config.save_env({"UPLOAD_MODE": "auto"})
    assert config.UPLOAD_MODE == "phone_auto"


# ---------------------------------------------------------------- 雲端
class FakeS3:
    def __init__(self):
        self.up, self.presigned = [], []

    def upload_file(self, path, bucket, key, ExtraArgs=None):
        self.up.append((bucket, key))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append((op, Params["Key"], ExpiresIn))
        return f"https://signed.example/{Params['Key']}?sig=1"


def test_cloud_upload_presigned_and_public_base():
    s3 = FakeS3()
    with db.connect() as conn:
        pid = approved(conn)
        assert cloud.run(conn, s3) == 0                               # 沒設定 → 不做事
        config.save_env({"CLOUD_BUCKET": "b", "CLOUD_ACCESS_KEY": "a", "CLOUD_SECRET_KEY": "s"})
        assert cloud.run(conn, s3) == 1
        assert db.get(conn, pid)["cloud_url"] == f"https://signed.example/shopee_clips/{pid}/video.mp4?sig=1"
        assert s3.presigned[0][2] == 7 * 24 * 3600 and (("b", f"shopee_clips/{pid}/video.mp4") in s3.up)
        assert cloud.run(conn, s3) == 0                               # 已有連結不重傳
        p2 = approved(conn, 2)
        config.save_env({"CLOUD_PUBLIC_BASE": "https://cdn.example.com/"})
        cloud.run(conn, s3)
        assert db.get(conn, p2)["cloud_url"] == f"https://cdn.example.com/shopee_clips/{p2}/video.mp4"


def test_cloud_failure_is_recorded_once():
    class Bad(FakeS3):
        def upload_file(self, *a, **k):
            raise RuntimeError("AccessDenied")

    config.save_env({"CLOUD_BUCKET": "b", "CLOUD_ACCESS_KEY": "a", "CLOUD_SECRET_KEY": "s"})
    with db.connect() as conn:
        p1, p2 = approved(conn, 1), approved(conn, 2)
        assert cloud.run(conn, Bad()) == 0
        assert "cloud: AccessDenied" in db.get(conn, p1)["error"] and db.get(conn, p2)["error"] == ""   # 設定壞了就停，不逐支重試


def test_qr_svg():
    assert cloud.qr_svg("https://example.com/a").startswith("<svg")


# ---------------------------------------------------------------- 網頁
def test_ready_list_and_csv_pages():
    from shopee_clips.web import app

    c = TestClient(app)
    with db.connect() as conn:
        pid = approved(conn)
        db.update(conn, pid, cloud_url="https://cdn.example.com/v.mp4")
        db.add_product(conn, "https://shopee.tw/x-i.2.2", title="另一個")
    ready = c.get("/ready").text
    assert "雲端下載連結" in ready and "<svg" in ready and "https://s.shopee.tw/aff1" in ready
    lst = c.get("/list").text
    assert "管理列表" in lst and "待上架（1）" in lst and "待產圖（1）" in lst
    sourced = c.get("/list?status=sourced").text
    assert "另一個" in sourced and "<td>1</td>" not in sourced                       # 篩選只剩待產圖那一筆
    csv_ = c.get("/list.csv")
    assert csv_.headers["content-type"].startswith("text/csv") and "https://cdn.example.com/v.mp4" in csv_.text
    assert "https://s.shopee.tw/aff1" in csv_.text
    for page in ("/", "/settings"):
        h = c.get(page).text
        assert "測試手機連線" in h if page == "/" else ("phone_dryrun" in h and "Bucket" in h and "網頁上傳頁" not in h)


def test_excel_keeps_original_link(tmp_path, monkeypatch):
    from openpyxl import Workbook

    from shopee_clips import sourcing

    monkeypatch.setattr(sourcing, "expand_short", lambda u: "https://shopee.tw/product/5/6")
    wb = Workbook()
    wb.active.append(["商品連結", "商品名稱"])
    wb.active.append(["https://s.shopee.tw/abc", "A"])
    wb.active.append(["https://shopee.tw/b-i.7.8", "B"])
    wb.save(tmp_path / "x.xlsx")
    with db.connect() as conn:
        sourcing.import_excel(conn, str(tmp_path / "x.xlsx"))
        a = conn.execute("SELECT * FROM products WHERE shopee_key='5.6'").fetchone()
        b = conn.execute("SELECT * FROM products WHERE shopee_key='7.8'").fetchone()
    assert a["source_url"] == "https://s.shopee.tw/abc" and a["url"].endswith("/product/5/6")   # 分潤短連結保留，標記商品要用
    assert b["source_url"] == ""                                                                  # 本來就是完整連結，不重複存
