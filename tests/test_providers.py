import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from shopee_clips import config, db, providers


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


def test_parse_json_tolerant():
    assert providers.parse_json('好的：\n```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(RuntimeError):
        providers.parse_json("no json")


def test_settings_save_and_mask():
    from shopee_clips.web import app

    c = TestClient(app)
    r = c.post("/settings", data={"TEXT_PROVIDER": "openai", "OPENAI_API_KEY": "sk-secret-1234",
                                  "IMAGES_PER_PRODUCT": "abc", "TTS": "0", "TEXT_MODEL": "x\nEVIL=1"},
               follow_redirects=False)
    assert r.status_code == 303
    assert config.TEXT_PROVIDER == "openai" and config.OPENAI_API_KEY == "sk-secret-1234"
    assert config.IMAGES_PER_PRODUCT == 5 and config.TTS is False          # 非法數字被忽略
    assert not any(ln.startswith("EVIL") for ln in config.ENV_PATH.read_text().splitlines())   # 換行不能夾帶新設定
    page = c.get("/settings").text
    assert "sk-secret-1234" not in page and "…1234" in page                # 網頁不回傳金鑰
    c.post("/settings", data={"OPENAI_API_KEY": ""})                        # 留空 = 不變更
    assert config.OPENAI_API_KEY == "sk-secret-1234"


def test_password_gate():
    from shopee_clips.web import app

    config.save_env({"APP_PASSWORD": "pw123"})
    c = TestClient(app)
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("me", "wrong")).status_code == 401
    assert c.get("/", auth=("me", "pw123")).status_code == 200


def test_configured_and_models():
    assert not providers.configured("image")
    config.save_env({"GEMINI_API_KEY": "k"})
    assert providers.configured("image") and providers.configured("text") and providers.configured("video") is False
    assert config.model_for("image") == "gemini-2.5-flash-image"
    config.save_env({"VIDEO_PROVIDER": "fal", "FAL_KEY": "f"})
    assert providers.configured("video") and "kling" in config.model_for("video") and config.clip_seconds() == 5


def _row(conn, **kw):
    pid = db.add_product(conn, "https://shopee.tw/A-i.1.2", title="保溫杯", description="內膽304不鏽鋼。保溫12小時。")
    if kw:
        db.update(conn, pid, **kw)
    return pid


def test_api_image_then_video_pipeline(monkeypatch):
    """gemini 產圖 → 審圖核准 → veo 逐段產片 → 接成 15 秒進審片（供應商呼叫全部 mock）。"""
    from PIL import Image

    from shopee_clips import imagegen, videogen

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    config.save_env({"GEMINI_API_KEY": "k", "TTS": "0", "IMAGES_PER_PRODUCT": "3", "VIDEO_PROVIDER": "veo"})
    with db.connect() as conn:
        pid = _row(conn, ref_images='["http://x/0.jpg"]')
    ref = config.REF_DIR / str(pid)
    ref.mkdir(parents=True)
    Image.new("RGB", (200, 200), "blue").save(ref / "0.jpg")
    calls = []

    def fake_img(prompt, refs):
        import io
        calls.append(prompt)
        b = io.BytesIO()
        Image.new("RGB", (512, 768), (len(calls) * 60, 80, 120)).save(b, "PNG")
        return b.getvalue()

    monkeypatch.setattr(providers, "image_bytes", fake_img)
    with db.connect() as conn:
        assert imagegen.run(conn) == 1
        r = db.get(conn, pid)
        assert r["status"] == "image_review" and len(json.loads(r["images"])) == 3
        db.move(conn, pid, "image_approved", selected_image=json.loads(r["images"])[0], selected_images=r["images"])

    seen = []

    def fake_clip(prompt, image_path, out):
        seen.append((prompt, image_path.name))
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=720x1280:d=8:r=24", "-pix_fmt", "yuv420p", str(out)],
                       check=True, capture_output=True)

    monkeypatch.setattr(providers, "video_clip", fake_clip)
    with db.connect() as conn:
        assert videogen.run(conn) == 1
        r = db.get(conn, pid)
        assert r["status"] == "video_review" and "保溫12小時" in r["video_caption"]
    assert len(seen) == 2 and len({s[1] for s in seen}) == 2          # veo 每段 8 秒 → 2 段，起始圖不同
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(config.DATA_DIR / r["video_path"])], capture_output=True, text=True).stdout
    assert 14.5 < float(probe) < 15.5


def test_llm_script_and_fallback(monkeypatch):
    from shopee_clips import scriptgen

    config.save_env({"GEMINI_API_KEY": "k", "VIDEO_PROVIDER": "fal", "FAL_KEY": "f"})
    with db.connect() as conn:
        pid = _row(conn)
        got = {}

        def fake_text(prompt, images=None):
            got["prompt"] = prompt
            return {"hook": "h", "selling_points": ["a", "b", "c"], "cta": "c", "voiceover": "v", "video_title": "t",
                    "caption": "cap", "hashtags": ["x"], "video_prompts": ["p1", "p2", "p3"]}

        monkeypatch.setattr(providers, "text_json", fake_text)
        s = scriptgen.get_script(db.get(conn, pid))
        assert s["selling_points"] == ["a", "b", "c"] and "3 個給影片模型" in got["prompt"]   # fal 5 秒 → 3 段
        db.update(conn, pid, script="{}")

        def boom(*a, **k):
            raise RuntimeError("quota")

        monkeypatch.setattr(providers, "text_json", boom)
        s2 = scriptgen.get_script(db.get(conn, pid))                      # AI 失敗 → 退回商品說明原句，不編造
        assert s2["selling_points"] == ["內膽304不鏽鋼", "保溫12小時"]


def test_openai_and_claude_payloads(monkeypatch):
    captured = {}

    class FakeOpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    captured["openai"] = kw
                    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": 1}'))])

    monkeypatch.setattr(providers, "_openai", lambda: FakeOpenAI)
    config.save_env({"TEXT_PROVIDER": "openai"})
    assert providers.text_json("hi", [b"img"]) == {"ok": 1}
    c = captured["openai"]["messages"][0]["content"]
    assert c[1]["image_url"]["url"].startswith("data:image/jpeg;base64,") and captured["openai"]["model"] == "gpt-4.1-mini"

    class FakeAnthropic:
        def __init__(self, api_key):
            self.messages = self

        def create(self, **kw):
            captured["claude"] = kw
            return SimpleNamespace(content=[SimpleNamespace(text='前言 {"ok": 2}')])

    import sys
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(Anthropic=FakeAnthropic))
    config.save_env({"TEXT_PROVIDER": "claude", "ANTHROPIC_API_KEY": "k"})
    assert providers.text_json("hi", [b"img"]) == {"ok": 2}
    assert captured["claude"]["messages"][0]["content"][0]["type"] == "image"


def test_check_reports_missing_key():
    assert "❌" in providers.check("text")           # 預設 gemini 但沒填 key
    config.save_env({"VIDEO_PROVIDER": "slideshow"})
    assert "✅" in providers.check("video")
