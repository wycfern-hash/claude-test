"""用假的 Gemini 頁面驗證「瀏覽器產圖」流程的管線（附圖 → 填提示詞 → 送出 → 等新圖 → 下載）。
不代表真正的 Gemini 介面一定吻合——標籤是猜的，真站要靠 data/debug 診斷檔校正。"""
import http.server
import json
import threading
from pathlib import Path

import pytest

from shopee_clips import config, db

CHROME = next(iter(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")), None)

PAGE = """<!doctype html><meta charset=utf-8><body>
<div role=textbox contenteditable=true id=box style="border:1px solid;min-height:30px"></div>
<button aria-label="上傳" onclick="document.getElementById('menu').style.display='block'">+</button>
<div id=menu style="display:none"><div id=pick onclick="pickFile()">上傳檔案</div></div>
<div id=chat></div>
<button aria-label="傳送" onclick="send()">go</button>
<script>
function pickFile(){ const i=document.createElement('input'); i.type='file'; i.multiple=true; i.style.display='none';
  i.onchange=()=>{ for (const f of i.files){ const im=new Image(); im.src=URL.createObjectURL(f); im.width=40; chat.appendChild(im);} };
  document.body.appendChild(i); i.click(); }
function send(){ setTimeout(()=>{ const c=document.createElement('canvas'); c.width=c.height=700; const x=c.getContext('2d');
  x.fillStyle='#e33'; x.fillRect(0,0,700,700); const im=new Image(); im.src=c.toDataURL('image/png'); chat.appendChild(im);
  const a=document.createElement('a'); a.href=im.src; a.download='g.png'; const b=document.createElement('button');
  b.setAttribute('aria-label','下載'); b.textContent='dl'; b.onclick=()=>a.click(); chat.appendChild(b); }, 1200); }
</script>"""


@pytest.fixture
def mock_site():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


@pytest.mark.skipif(CHROME is None, reason="no chromium")
def test_gemini_web_pipeline(tmp_path, monkeypatch, mock_site):
    from PIL import Image

    from shopee_clips import gemini_web, webauto

    for name, sub in (("DATA_DIR", ""), ("DB_PATH", "clips.db"), ("REF_DIR", "ref"), ("IMG_DIR", "images"),
                      ("VID_DIR", "videos"), ("BROWSER_PROFILE", "bp")):
        monkeypatch.setattr(config, name, tmp_path / sub if sub else tmp_path)
    monkeypatch.setattr(webauto, "DEBUG_DIR", tmp_path / "debug")
    monkeypatch.setattr(config, "CHROME_PATH", str(CHROME))
    monkeypatch.setattr(config, "CHROME_HEADLESS", True)
    monkeypatch.setattr(config, "CDP_PORT", 9333)
    monkeypatch.setattr(config, "IMAGES_PER_PRODUCT", 2)
    labels = json.loads(Path("config/browser_sites.json").read_text())["gemini"]
    labels |= {"url": mock_site, "gap_seconds": 0, "generate_timeout_sec": 30, "step_timeout_sec": 10}
    monkeypatch.setattr(gemini_web, "site", lambda n: labels)

    with db.connect() as conn:
        pid = db.add_product(conn, "https://shopee.tw/A-i.1.2", title="保溫杯")
    ref = config.REF_DIR / str(pid)
    ref.mkdir(parents=True)
    Image.new("RGB", (300, 300), "blue").save(ref / "0.jpg")
    with db.connect() as conn:
        n = gemini_web.run(conn, cap_left=5)
        row = db.get(conn, pid)
    try:
        assert n == 1 and row["status"] == "image_review", row["error"]
        imgs = json.loads(row["images"])
        assert len(imgs) == 2
        assert all(Image.open(config.DATA_DIR / p).size == (700, 700) for p in imgs)
    finally:
        import subprocess
        subprocess.run(["pkill", "-f", "remote-debugging-port=9333"])
