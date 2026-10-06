"""審核 + 控制介面（手機/電腦瀏覽器都能開）。"""
import html
import json
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, gemini_client, imagegen, scriptgen, sourcing, videogen, worker

config.ensure_dirs()


@asynccontextmanager
async def lifespan(_app):
    stop = threading.Event()
    threading.Thread(target=worker.loop, args=(stop,), daemon=True).start()
    yield
    stop.set()


app = FastAPI(lifespan=lifespan)
app.mount("/media", StaticFiles(directory=str(config.DATA_DIR)), name="media")
e = html.escape

CSS = """<meta name=viewport content="width=device-width,initial-scale=1"><style>
body{font:16px system-ui;margin:0 auto;max-width:720px;padding:12px;background:#fafafa}
.card{background:#fff;border:1px solid #ddd;border-radius:10px;padding:12px;margin:12px 0}
img,video{max-width:100%;border-radius:8px}.row{display:flex;gap:8px;flex-wrap:wrap}
.row>label{flex:1;min-width:140px}button{padding:10px 16px;border-radius:8px;border:0;background:#ee4d2d;color:#fff;font-size:16px}
button.g{background:#888}input[type=text],textarea{width:100%;box-sizing:border-box;padding:8px;font:inherit}
nav a{margin-right:12px}.err{color:#c00;font-size:13px}pre{white-space:pre-wrap;font-size:12px}</style>"""
NAV = '<nav><a href="/">總覽</a><a href="/todo">待產圖</a><a href="/images">審圖</a><a href="/flow">待產片</a><a href="/videos">審片</a><a href="/ready">上架包</a></nav>'


def page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8>{CSS}{NAV}{body}")


def back(path: str = "/"):
    return RedirectResponse(path, status_code=303)


@app.get("/")
def home():
    with db.connect() as conn:
        c = db.counts(conn)
        failed = db.by_status(conn, "failed")
    stats = " ｜ ".join(f"{k}: {v}" for k, v in c.items() if v)
    errs = "".join(f'<div class=err>#{r["id"]} {e(r["title"][:20])}：{e(r["error"])}</div>' for r in failed)
    return page(f"""<h2>蝦皮分潤短影音</h2><div class=card>{e(stats) or '尚無商品'}</div>
<form method=post action=/add class=card><b>貼商品連結（一行一個）</b>
<textarea name=urls rows=4 placeholder="https://shopee.tw/..-i.123.456"></textarea><button>加入</button></form>
<div class=card><form method=post action=/fetch-picks><button class=g>從分潤後台抓選品</button></form><br>
<form method=post action=/login><button class=g>開瀏覽器登入蝦皮</button></form>
<form method=post action=/retry-failed style="margin-top:8px"><button class=g>重試失敗項目</button></form>
<form method=post action=/check-key style="margin-top:8px"><button class=g>檢查 Gemini key 能不能用</button></form></div>
{errs}<div class=card><b>執行紀錄</b><pre>{e(chr(10).join(worker.log[-15:]))}</pre></div>""")


@app.post("/add")
def add(urls: str = Form("")):
    with db.connect() as conn:
        try:
            a, d = sourcing.import_urls(conn, urls.splitlines())
            worker.say(f"新增 {a} 筆，重複略過 {d} 筆")
        except ValueError as ex:
            worker.say(str(ex))
    return back()


def _bg(name, fn):
    def run():
        with worker.browser_lock:
            try:
                with db.connect() as conn:
                    worker.say(f"{name}：{fn(conn)}")
            except Exception as ex:  # noqa: BLE001
                worker.say(f"{name} 失敗：{ex}")

    threading.Thread(target=run, daemon=True).start()


@app.post("/fetch-picks")
def fetch_picks():
    _bg("抓選品", sourcing.fetch_picks)
    return back()


@app.post("/login")
def login():
    _bg("登入", lambda conn: sourcing.login())
    return back()


@app.post("/check-key")
def check_key():
    def run():
        for line in gemini_client.check():
            worker.say(line)

    threading.Thread(target=run, daemon=True).start()
    return back()


@app.post("/retry-failed")
def retry_failed():
    with db.connect() as conn:
        conn.execute("UPDATE products SET error='' WHERE status='sourced' AND error LIKE 'imagegen:%'")
        for r in db.by_status(conn, "failed"):
            back_to = "video_approved" if r["video_path"] else ("image_approved" if r["selected_image"] else "sourced")
            db.move(conn, r["id"], back_to, error="")
    return back()


def points_box(r) -> str:
    cur = "\n".join(scriptgen.user_points(r))
    return f'賣點（選填，一行一個，最多 3 個；留空會用 AI/商品說明）<textarea name=points rows=3>{e(cur)}</textarea>'


def save_points(conn, pid: int, points: str) -> None:
    row = db.get(conn, pid)
    script = json.loads(row["script"] or "{}")
    script["user_points"] = [p.strip() for p in points.splitlines() if p.strip()][:3]
    db.update(conn, pid, script=json.dumps(script, ensure_ascii=False))


@app.get("/todo")
def todo():
    """手動產圖：給你參考圖和提示詞，你到 Gemini App/網頁產圖，再把圖傳回來（免 API、不花錢）。"""
    with db.connect() as conn:
        rows = db.by_status(conn, "sourced")
    cards = ""
    for r in rows:
        refs = "".join(f'<a href="/media/ref/{r["id"]}/{p.name}" download><img src="/media/ref/{r["id"]}/{p.name}" width=90></a>'
                       for p in imagegen.ref_files(r["id"]))
        prompts = "".join(
            f'<textarea id=p{r["id"]}_{i} rows=3 readonly>{e(t)}</textarea>'
            f'<button type=button class=g onclick="navigator.clipboard.writeText(document.getElementById(\'p{r["id"]}_{i}\').value)">複製提示詞 {i + 1}</button>'
            for i, t in enumerate(imagegen.prompts_for(r)))
        cards += f"""<form method=post enctype=multipart/form-data class=card action=/todo/{r["id"]}>
<b>#{r["id"]} {e(r["title"] or r["url"])}</b> <a href="{e(r["url"])}" target=_blank>原商品</a>
<div>參考圖（點圖下載，貼進 Gemini 當參考）：{refs or "（尚未抓到，稍等或檢查商品資料）"}</div>
<details><summary>{config.IMAGES_PER_PRODUCT} 個提示詞</summary>{prompts}</details>
{points_box(r)}
<div>上傳 {config.IMAGES_PER_PRODUCT} 張你產好的圖（會依序對應：開場、賣點1~3、結尾）
<input type=file name=files multiple accept="image/*"></div>
<button>上傳並送去產片</button> <button name=skip value=1 class=g formnovalidate>不做這個商品</button></form>"""
    mode = "" if config.IMAGE_PROVIDER == "manual" else "<p>目前 IMAGE_PROVIDER=api，系統會自動產圖，這頁只在手動補圖時用。</p>"
    return page(f"<h2>待產圖（{len(rows)}）</h2>{mode}{cards or '沒有待處理商品'}")


@app.post("/todo/{pid}")
async def todo_upload(pid: int, files: list[UploadFile] = File([]), points: str = Form(""), skip: str = Form("")):
    with db.connect() as conn:
        if skip:
            db.move(conn, pid, "skipped")
            return back("/todo")
        d = config.IMG_DIR / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        saved = []
        for i, f in enumerate(files):
            data = await f.read()
            if not data:
                continue
            ext = "." + f.filename.rsplit(".", 1)[-1].lower() if "." in (f.filename or "") else ".png"
            if ext not in (".png", ".jpg", ".jpeg", ".webp"):
                ext = ".png"
            path = d / f"u{i}{ext}"
            path.write_bytes(data)
            saved.append(str(path.relative_to(config.DATA_DIR)))
        if saved:
            save_points(conn, pid, points)
            db.move(conn, pid, "image_approved", images=json.dumps(saved), selected_image=saved[0],
                    selected_images=json.dumps(saved))
    return back("/todo")


@app.get("/images")
def images():
    with db.connect() as conn:
        rows = db.by_status(conn, "image_review")
    cards = ""
    for r in rows:
        opts = "".join(
            f'<label><input type=checkbox name=selected value="{e(p)}" checked>'
            f'<img src="/media/{e(p)}"></label>' for i, p in enumerate(json.loads(r["images"])))
        cards += f"""<form method=post class=card action=/images/{r["id"]}>
<b>#{r["id"]} {e(r["title"])}</b> <a href="{e(r["url"])}" target=_blank>原商品</a><div class=row>{opts}</div>
{points_box(r)}<button name=act value=approve>核准勾選的圖</button> <button name=act value=reject class=g>退回重產</button>
<button name=act value=skip class=g>不做這個商品</button></form>"""
    return page(f"<h2>審圖（{len(rows)}）</h2>{cards or '沒有待審'}")


@app.post("/images/{pid}")
def images_act(pid: int, act: str = Form(...), selected: list[str] = Form([]), points: str = Form("")):
    with db.connect() as conn:
        if act == "approve" and selected:
            save_points(conn, pid, points)
            db.move(conn, pid, "image_approved", selected_image=selected[0], selected_images=json.dumps(selected))
        elif act == "reject":
            db.move(conn, pid, "sourced", images="[]")
        elif act == "skip":
            db.move(conn, pid, "skipped")
    return back("/images")


@app.get("/flow")
def flow():
    """Flow 產片：下載起始圖、複製提示詞，到 Flow 用你的點數產，再把 mp4 傳回來。"""
    with db.connect() as conn:
        rows = [r for r in db.by_status(conn, "image_approved") if json.loads(r["script"] or "{}").get("video_title")]
    note = "" if config.VIDEO_PROVIDER == "flow" else "<p>目前 VIDEO_PROVIDER 不是 flow，影片會自動合成；這頁只在你想改用 Flow 時用。</p>"
    cards = ""
    for r in rows:
        sc = json.loads(r["script"])
        prompts = "".join(
            f'<textarea id=v{r["id"]}_{i} rows=3 readonly>{e(sc.get(k, ""))}</textarea>'
            f'<button type=button class=g onclick="navigator.clipboard.writeText(document.getElementById(\'v{r["id"]}_{i}\').value)">複製片段 {i} 提示詞</button>'
            for i, k in ((1, "video_prompt_1"), (2, "video_prompt_2")))
        cards += f"""<form method=post enctype=multipart/form-data class=card action=/flow/{r["id"]}>
<b>#{r["id"]} {e(r["title"])}</b>
<div>起始圖（Flow 用 Frames to Video，比例選 9:16）：<a href="/media/{e(r["selected_image"])}" download><img src="/media/{e(r["selected_image"])}" width=120></a></div>
{prompts}
<div>上傳 Flow 下載的 mp4（1~3 段，依選檔順序接起來，總長裁成 15 秒）<input type=file name=files multiple accept="video/*"></div>
<button>上傳並合成</button></form>"""
    return page(f"<h2>待產片（{len(rows)}）</h2>{note}{cards or '沒有待處理商品（審圖核准後，系統備好提示詞才會出現）'}")


@app.post("/flow/{pid}")
def flow_upload(pid: int, files: list[UploadFile] = File([])):
    d = config.VID_DIR / str(pid) / "flow"
    d.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, f in enumerate(files):
        data = f.file.read()
        if data:
            p = d / f"clip{i}.mp4"
            p.write_bytes(data)
            clips.append(p)
    if clips:
        with db.connect() as conn:
            try:
                videogen.finish_flow(conn, pid, clips)
            except Exception as ex:  # noqa: BLE001
                db.update(conn, pid, error=f"flow: {ex}")
                worker.say(f"#{pid} 合成失敗：{ex}")
    return back("/flow")


@app.get("/videos")
def videos():
    with db.connect() as conn:
        rows = db.by_status(conn, "video_review")
    cards = ""
    for r in rows:
        cards += f"""<form method=post class=card action=/videos/{r["id"]}>
<b>#{r["id"]} {e(r["title"])}</b><video src="/media/{e(r["video_path"])}" controls playsinline></video>
標題<input type=text name=video_title value="{e(r["video_title"])}">
文案<textarea name=video_caption rows=4>{e(r["video_caption"])}</textarea>
<button name=act value=approve>核准上架</button> <button name=act value=reject class=g>退回重產</button>
<button name=act value=skip class=g>不做</button></form>"""
    return page(f"<h2>審片（{len(rows)}）</h2>{cards or '沒有待審'}")


@app.post("/videos/{pid}")
def videos_act(pid: int, act: str = Form(...), video_title: str = Form(""), video_caption: str = Form("")):
    with db.connect() as conn:
        if act == "approve":
            db.move(conn, pid, "video_approved", video_title=video_title, video_caption=video_caption)
        elif act == "reject":
            db.move(conn, pid, "image_approved", video_path="", script="{}")
        elif act == "skip":
            db.move(conn, pid, "skipped")
    return back("/videos")


@app.get("/ready")
def ready():
    """手機上架：下載影片、複製標題文案、開商品連結，貼到蝦皮 App。"""
    with db.connect() as conn:
        rows = db.by_status(conn, "video_approved") + db.by_status(conn, "failed")
        rows = [r for r in rows if r["video_path"]]
    cards = ""
    for r in rows:
        text = e(f"{r['video_title']}\n{r['video_caption']}")
        cards += f"""<div class=card><b>#{r["id"]} {e(r["title"])}</b><video src="/media/{e(r["video_path"])}" controls playsinline></video>
<a href="/media/{e(r["video_path"])}" download>下載影片</a>
<textarea id=t{r["id"]} rows=4 readonly>{text}</textarea>
<button type=button onclick="navigator.clipboard.writeText(document.getElementById('t{r["id"]}').value)">複製標題+文案</button>
<a href="{e(r["url"])}" target=_blank>商品連結</a>
<form method=post action=/ready/{r["id"]}><button class=g>我已手動上傳</button></form></div>"""
    return page(f"<h2>上架包（{len(rows)}）</h2>{cards or '沒有待上架'}")


@app.post("/ready/{pid}")
def mark_uploaded(pid: int):
    with db.connect() as conn:
        row = db.get(conn, pid)
        if row["status"] == "failed":
            db.update(conn, pid, status="video_approved")  # failed 先回到可轉換狀態
        db.move(conn, pid, "uploaded", uploaded_at=db.now(), error="")
    return back("/ready")
