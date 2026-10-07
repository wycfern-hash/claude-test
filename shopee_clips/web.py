"""審核 + 控制介面（手機/電腦瀏覽器都能開）。"""
import html
import json
import threading
import time
from contextlib import asynccontextmanager

import base64
import secrets
import shutil
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import browser, characters, cloud, config, db, imagegen, imgsearch, phone, providers, scriptgen, sourcing, videogen, webauto, worker

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
body{font:16px/1.5 system-ui;margin:0 auto;max-width:760px;padding:12px;background:#f6f6f6;color:#222}
h2{margin:8px 0}.card{background:#fff;border:1px solid #ddd;border-radius:12px;padding:14px;margin:12px 0}
img,video{max-width:100%;border-radius:8px}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:end}
.row>label{flex:1;min-width:140px}
button,.btn{display:inline-block;padding:11px 18px;border-radius:10px;border:2px solid #ee4d2d;background:#ee4d2d;color:#fff;font-size:16px;cursor:pointer;text-decoration:none}
button.g,.btn.g{background:#fff;color:#333;border:2px solid #888}
button.big,.btn.big{font-size:18px;padding:14px 26px}
button.ok,.btn.ok{background:#1a9b4b;border-color:#1a9b4b}
button:hover,.btn:hover{filter:brightness(.93)}
input[type=text],input[type=password],textarea,select{width:100%;box-sizing:border-box;padding:8px;font:inherit}
input[type=file]{padding:8px 0;max-width:100%}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;padding:6px 0 10px;border-bottom:1px solid #ddd}
nav a{text-decoration:none;color:#333;font-weight:600}nav a:hover{color:#ee4d2d}
.err{color:#c00;font-size:13px}pre{white-space:pre-wrap;font-size:12px}small{color:#666}
.flash{border-radius:10px;padding:12px 14px;margin:12px 0;font-weight:600}
.flash.ok{background:#e4f6ea;border:1px solid #1a9b4b}.flash.err{background:#fde8e8;border:1px solid #c00}
.flash.warn{background:#fff6dc;border:1px solid #d9a400}
.step{display:flex;gap:12px}.num{flex:none;width:34px;height:34px;border-radius:50%;background:#ee4d2d;color:#fff;
 display:flex;align-items:center;justify-content:center;font-weight:700}.num.done{background:#1a9b4b}
.step>.body{flex:1;min-width:0}.todo{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:8px 0;border-top:1px solid #eee}
.todo .btn{flex:none;white-space:nowrap}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:6px 4px;border-top:1px solid #eee;text-align:left;vertical-align:top}
details>summary{cursor:pointer;font-weight:600}
</style>"""
NAV = ('<nav><a href="/">開始</a><a href="/todo">待產圖</a><a href="/images">審圖</a><a href="/videos">審片</a>'
       '<a href="/ready">上架包</a><a href="/list">管理列表</a><a href="/characters">主角</a><a href="/settings">設定</a>'
       '<a href="/status">檢查</a></nav>')


def flashes_html() -> str:
    return "".join(f'<div class="flash {k}">{e(m)}</div>' for k, m in worker.take_flashes())


def page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><meta charset=utf-8>{CSS}{NAV}{flashes_html()}{body}")


def back(path: str = "/"):
    return RedirectResponse(path, status_code=303)


STATUS_ZH = {"sourced": "待產圖", "image_review": "待審圖", "image_approved": "待產片", "video_review": "待審片",
             "video_approved": "待上架", "uploaded": "已上架", "failed": "失敗", "skipped": "略過"}
NEXT_STEP = {"sourced": ("產圖中／等你處理", "/todo"), "image_review": ("等你審圖", "/images"), "image_approved": ("產影片中", "/flow"),
             "video_review": ("等你審片", "/videos"), "video_approved": ("等你上架", "/ready"), "uploaded": ("完成", "/list"),
             "failed": ("失敗，看原因", "/list?status=failed"), "skipped": ("略過", "/list")}
IMPORT = {"running": False, "name": "", "done": 0, "total": 0, "result": None, "started": 0.0}


def _setup_ok() -> tuple[bool, list[str]]:
    """最低可用設定：腳本、圖片至少各有一個可用的做法（AI 服務或手動）。"""
    lines = providers.summary()
    ready = providers.configured("text") and (providers.configured("image") or config.IMAGE_PROVIDER in ("browser", "manual"))
    return ready, lines


def live_html() -> str:
    """首頁會每幾秒自己更新的區塊：目前在做什麼、各狀態數量、等你處理的事。"""
    with db.connect() as conn:
        counts = db.counts(conn)
        sourced = db.by_status(conn, "sourced")
    total = sum(counts.values())
    busy = ""
    if IMPORT["running"]:
        busy = f"⏳ 正在匯入選品檔… {IMPORT['done']}/{IMPORT['total'] or '?'}"
    elif worker.state["step"] and (config.AUTO_RUN or worker.state["step"] in ("產片", "補商品資料")):
        busy = f"⏳ 背景正在：{worker.state['step']}"
    if total == 0:
        return f'<div class="card">{busy or "還沒有商品。請先做步驟 ②「匯入選品檔」。"}</div>'
    chips = " ".join(f"{STATUS_ZH[k]} <b>{counts[k]}</b>" for k in STATUS_ZH if counts.get(k)) or "—"
    todo = ""
    for key, label, url in (("image_review", "個商品等你審圖", "/images"), ("video_review", "支影片等你審片", "/videos"),
                            ("video_approved", "支影片可以上架", "/ready")):
        if counts.get(key):
            todo += (f'<div class=todo><div><b>{counts[key]}</b> {label}</div>'
                     f'<a class="btn ok" href="{url}">前往處理 →</a></div>')
    if sourced:
        if config.AUTO_RUN and providers.configured("image"):
            todo += f'<div class=todo><div><b>{len(sourced)}</b> 個商品排隊產圖中（每日上限 {config.DAILY_GEN_CAP} 個）</div><a class="btn g" href="/todo">查看</a></div>'
        else:
            todo += (f'<div class=todo><div><b>{len(sourced)}</b> 個商品還沒有圖片。按下面步驟 ③「開始」讓 AI 自動產，'
                     f'或到「待產圖」手動處理</div><a class="btn g" href="/todo">待產圖</a></div>')
    if counts.get("failed"):
        todo += f'<div class=todo><div class=err><b>{counts["failed"]}</b> 個失敗</div><a class="btn g" href="/list?status=failed">看原因</a></div>'
    return f'<div class="card">{f"<div class=flash style=margin-top:0>{e(busy)}</div>" if busy else ""}<b>目前進度</b>（共 {total} 個商品）<br>{chips}{todo}</div>'


@app.get("/fragment/live")
def fragment_live():
    return HTMLResponse(live_html())


@app.get("/")
def home():
    ready, lines = _setup_ok()
    with db.connect() as conn:
        counts = db.counts(conn)
        failed = db.by_status(conn, "failed")
        recent = db.all_products(conn)[-8:]
    total = sum(counts.values())
    errs = "".join(f'<div class=err>#{r["id"]} {e(r["title"][:20])}：{e(r["error"])}</div>' for r in failed)
    run_btn = ('<form method=post action=/auto-run><input type=hidden name=on value=0><button class="g big">⏸ 暫停自動處理</button></form>'
               if config.AUTO_RUN else
               '<form method=post action=/auto-run><input type=hidden name=on value=1><button class="ok big">▶ 開始自動處理</button></form>')
    run_note = ("自動處理<b>執行中</b>：會依設定自動產圖、產 AI 影片、操作手機上架。" if config.AUTO_RUN else
                f"按下「開始」後，程式會依你的設定自動產圖、產片（<b>使用 API 費用或點數</b>，每日上限 {config.DAILY_GEN_CAP} 個商品，可在設定調整）。"
                "不按的話只會做免費的步驟。隨時可以暫停。")
    rows = "".join(f'<tr><td>{r["id"]}</td><td>{e((r["video_title"] or r["title"] or r["url"])[:26])}</td>'
                   f'<td><a href="{NEXT_STEP[r["status"]][1]}">{STATUS_ZH[r["status"]]}</a></td></tr>' for r in recent)
    table = (f'<div class=card><b>最近的商品</b><table>{rows}</table><p><a href="/list">看全部（{total}）→</a></p></div>' if total else "")
    setup_state = "✅ 已可使用" if ready else "還沒完成"
    return page(f"""<h2>蝦皮分潤短影音</h2>
<div class=card><b>怎麼用（照著 ①②③④ 做）</b><br>
<small>① 設定 AI 服務 → ② 匯入蝦皮分潤的選品檔 → ③ 按「開始」讓程式自動產圖、寫腳本、做影片 → ④ 你只負責「審圖」「審片」和「上架」。</small></div>

<div class=card><div class=step><div class="num {'done' if ready else ''}">①</div><div class=body>
<b>設定 AI 服務</b> <small>{setup_state}</small>
<div><small>{'<br>'.join(e(x) for x in lines)}</small></div>
<p><a class="btn {'g' if ready else ''}" href="/settings">{'檢查設定' if ready else '前往設定（選服務、填 API key）→'}</a></p></div></div></div>

<form method=post action=/import-file enctype=multipart/form-data class=card><div class=step><div class="num {'done' if total else ''}">②</div><div class=body>
<b>匯入選品檔</b> <small>蝦皮分潤後台下載的 .csv（或 .xlsx）</small><br>
<input type=file name=file accept=".csv,.xlsx,.xlsm,.tsv,.txt" required>
<button class=big>匯入</button>
<p><small>欄位自動辨識，不用改檔案。按「匯入」後會顯示處理進度與結果。</small></p></div></div></form>

<div class=card><div class=step><div class="num {'done' if config.AUTO_RUN else ''}">③</div><div class=body>
<b>自動處理</b><p><small>{run_note}</small></p>{run_btn}</div></div></div>

<div id=live>{live_html()}</div>
{table}{errs}

<details class=card><summary>其他：貼連結加入、批次設定、進階工具</summary>
<form method=post action=/add><p><b>貼商品連結（一行一個）</b></p>
<textarea name=urls rows=3 placeholder="https://shopee.tw/..-i.123.456"></textarea><p><button class=g>加入</button></p></form>
<form method=post action=/apply-defaults><p><b>批次：套用到所有尚未產圖的商品</b></p>
<div class=row><label>圖片來源<select name=image_source><option value="">不變更</option><option value=auto>自動（有參考圖就用，沒有就純 AI）</option><option value=web>上網找圖當參考</option><option value=ai>純 AI 生成</option></select></label>
<label>搭配主角<select name=character_id><option value=keep>不變更</option>{_char_options(-1, "不加人物")}</select></label><button class=g>套用</button></div></form>
<hr><p><b>進階工具</b>（一般用不到）</p>
<form method=post action=/enrich style="margin:6px 0"><button class=g>從蝦皮商品頁補標題/圖片（會開啟 Chrome）</button></form>
<form method=post action=/fetch-picks style="margin:6px 0"><button class=g>從分潤後台抓選品（會開啟 Chrome）</button></form>
<form method=post action=/login style="margin:6px 0"><button class=g>開啟自動化 Chrome（登入 Google 用）</button></form>
<form method=post action=/phone-check style="margin:6px 0"><button class=g>測試手機連線</button></form>
<form method=post action=/phone-probe style="margin:6px 0"><button class=g>擷取手機目前畫面（校正上架步驟用）</button></form>
<form method=post action=/probe style="margin:6px 0"><button class=g>擷取目前分頁畫面結構（除錯用）</button></form>
<form method=post action=/retry-failed style="margin:6px 0"><button class=g>重試失敗項目</button></form>
</details>

<details class=card><summary>執行紀錄</summary><pre>{e(chr(10).join(worker.log[-25:]))}</pre></details>
<script>
setInterval(() => fetch('/fragment/live').then(r => r.text()).then(h => {{ document.getElementById('live').innerHTML = h; }}).catch(() => {{}}), 4000);
</script>""")


@app.post("/auto-run")
def auto_run(on: str = Form("0")):
    config.save_env({"AUTO_RUN": "1" if on == "1" else "0"})
    if on != "1":
        worker.flash("⏸ 已暫停自動處理。", "ok")
    elif not providers.configured("image") and config.IMAGE_PROVIDER != "browser":
        worker.flash("已開始，但「圖片 AI」還沒設定好，所以不會自動產圖。請先完成步驟 ①：到設定頁選圖片服務並填 API key。", "warn")
    else:
        worker.flash("▶ 已開始自動處理。程式會在背景依序產圖、產片；有東西要你處理時，首頁會出現綠色按鈕。", "ok")
    return back()


@app.post("/enrich")
def enrich_now():
    _bg("補商品資料", sourcing.enrich, "已從蝦皮商品頁補完 {} 個商品的標題與圖片")
    return back()


@app.post("/add")
def add(urls: str = Form("")):
    with db.connect() as conn:
        try:
            a, d = sourcing.import_urls(conn, urls.splitlines())
            worker.flash(f"✅ 已加入 {a} 個商品" + (f"，重複略過 {d} 個" if d else "") + "。", "ok" if a else "warn")
        except ValueError as ex:
            worker.flash(f"❌ {ex}", "err")
    return back()


def _bg(name, fn, ok_msg: str = "{}"):
    """在背景跑一件事（會用到自動化 Chrome），完成/失敗都用醒目訊息告訴你。"""
    worker.flash(f"⏳ {name}：已開始，完成後會在這裡顯示（可先做別的事）。", "warn")

    def run():
        with worker.browser_lock:
            try:
                with db.connect() as conn:
                    worker.flash(f"✅ {name}：" + ok_msg.format(fn(conn)), "ok")
            except Exception as ex:  # noqa: BLE001
                worker.flash(f"❌ {name} 失敗：{ex}", "err")

    threading.Thread(target=run, daemon=True).start()


@app.post("/import-file")
@app.post("/import-excel")  # 舊路徑，相容
def import_file(file: UploadFile = File(...)):
    if IMPORT["running"]:
        worker.flash("上一個檔案還在匯入中，請等它做完。", "warn")
        return back("/import-status")
    inbox = config.DATA_DIR / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    suffix = Path(file.filename or "").suffix.lower() or ".csv"
    path = inbox / (time.strftime("%m%d_%H%M%S_") + "選品" + suffix)
    path.write_bytes(file.file.read())
    IMPORT.update(running=True, name=file.filename or path.name, done=0, total=0, result=None, started=time.time())

    def progress(done, total):
        IMPORT["done"], IMPORT["total"] = done, total

    def run():  # 分潤短連結要逐一展開，100 筆可能要一兩分鐘，所以放背景並顯示進度
        try:
            with db.connect() as conn:
                IMPORT["result"] = sourcing.import_file(conn, str(path), progress)
        except Exception as ex:  # noqa: BLE001
            IMPORT["result"] = {"added": 0, "dup": 0, "failed": [("檔案", f"讀取失敗：{ex}")]}
        r = IMPORT["result"]
        worker.say(f"匯入 {IMPORT['name']}：新增 {r['added']}、重複 {r['dup']}、失敗 {len(r['failed'])}")
        IMPORT["running"] = False

    threading.Thread(target=run, daemon=True).start()
    return back("/import-status")


@app.get("/import-status")
def import_status():
    if IMPORT["running"]:
        total = IMPORT["total"] or "?"
        return HTMLResponse(f"<!doctype html><meta charset=utf-8><meta http-equiv=refresh content=2>{CSS}{NAV}"
                            f"<h2>匯入中…</h2><div class=card>檔案：{e(IMPORT['name'])}<br>進度：<b>{IMPORT['done']} / {total}</b><br>"
                            f"<small>每筆如果是分潤短連結，需要連到蝦皮展開，可能要一點時間。這頁會自動更新，不要關掉。</small></div>")
    r = IMPORT["result"]
    if not r:
        return back("/")
    fails = "".join(f"<tr><td>{e(str(w))}</td><td>{e(why)}</td></tr>" for w, why in r["failed"][:30])
    ok = r["added"] > 0 or bool(r.get("upgraded"))
    dup_txt = "，重複略過 %d 個" % r["dup"] if r["dup"] else ""
    if r.get("upgraded"):
        dup_txt += f"，其中 <b>{r['upgraded']}</b> 個已補上分潤連結"
    aff_warn = (f'<div class="flash warn">⚠ 其中 <b>{r.get("no_aff", 0)}</b> 個商品沒有分潤連結（檔案裡沒有「分潤連結」欄，或不是 s.shopee.tw 短連結）。'
                f'上架時標記商品會用一般連結，<b>不會有分潤</b>。請先用「蝦皮特賣分潤小幫手」擴充功能轉成分潤連結，或在分潤後台產生連結後再匯入。</div>'
                if ok and r.get("no_aff") else "")
    head = (f'<div class="flash ok">✅ 匯入完成：新增 <b>{r["added"]}</b> 個商品{dup_txt}。</div>{aff_warn}' if (ok or r.get("upgraded")) else
            '<div class="flash err">❌ 沒有匯入任何商品。看下面的原因。</div>')
    nxt = ('<p>下一步：</p><p><a class="btn ok big" href="/">回到首頁，按「開始自動處理」→</a></p>'
           '<p><a class="btn g" href="/list">先看管理列表</a> <a class="btn g" href="/todo">待產圖</a></p>' if ok else
           '<p><a class="btn big" href="/">回首頁</a></p>')
    problems = (f'<div class=card><b>有問題的列（{len(r["failed"])}）</b><table><tr><th>位置</th><th>原因</th></tr>{fails}</table></div>'
                if r["failed"] else "")
    return page(f"<h2>匯入結果：{e(IMPORT['name'])}</h2>{head}{problems}<div class=card>{nxt}</div>")


@app.post("/phone-check")
def phone_check():
    def run():
        try:
            d = phone.connect()
            info = d.info
            worker.flash(f"✅ 手機已連線：{info.get('productName') or info.get('brand', '')}，螢幕 {info.get('displayWidth')}×{info.get('displayHeight')}；"
                         f"目前 App：{d.app_current().get('package')}", "ok")
        except Exception as ex:  # noqa: BLE001
            worker.flash(f"❌ {ex}", "err")

    worker.flash("⏳ 正在連線手機…（約幾秒，完成後重新整理本頁）", "warn")
    threading.Thread(target=run, daemon=True).start()
    return back()


@app.post("/phone-probe")
def phone_probe():
    def run():
        try:
            worker.flash("✅ 已存到 " + phone.probe(phone.connect(), "manual") + ".png/.json（把 data/debug 資料夾給我即可）", "ok")
        except Exception as ex:  # noqa: BLE001
            worker.flash(f"❌ {ex}", "err")

    threading.Thread(target=run, daemon=True).start()
    return back()


@app.post("/probe")
def probe():
    def run(conn):
        with browser.open_context() as ctx:
            return "已存到 " + ", ".join(webauto.probe_tabs(ctx)[:3]) + "…（整個 data/debug 資料夾給我即可）"

    _bg("擷取畫面", run)
    return back()


@app.post("/fetch-picks")
def fetch_picks():
    _bg("抓選品", lambda conn: "新增 %d 個、重複 %d 個" % sourcing.fetch_picks(conn))
    return back()


@app.post("/login")
def login():
    _bg("開啟自動化 Chrome", lambda conn: (sourcing.login(), "已開啟，請在跳出的 Chrome 登入 Google")[1])
    return back()


@app.post("/retry-failed")
def retry_failed():
    with db.connect() as conn:
        conn.execute("UPDATE products SET error='' WHERE status IN ('sourced','image_approved') "
                     "AND (error LIKE 'imagegen:%' OR error LIKE 'videogen:%' OR error LIKE 'flow:%' OR error LIKE 'enrich:%')")
        n = 0
        for r in db.by_status(conn, "failed"):
            back_to = "video_approved" if r["video_path"] else ("image_approved" if r["selected_image"] else "sourced")
            db.move(conn, r["id"], back_to, error="")
            n += 1
    worker.flash(f"已把 {n} 個失敗的商品放回流程重試；其他卡住的項目也已解除。", "ok")
    return back()


def _char_options(sel: int, blank: str = "不加人物") -> str:
    with db.connect() as conn:
        chars = db.list_characters(conn)
    opts = f'<option value="0" {"selected" if not sel else ""}>{e(blank)}</option>'
    return opts + "".join(f'<option value="{c["id"]}" {"selected" if c["id"] == sel else ""}>{e(c["name"])}</option>' for c in chars)


SRC_LABEL = {"auto": "自動（有參考圖就用，沒有就純 AI 生成）", "web": "上網找圖當參考（AI 重新生成）", "ai": "純 AI 生成（不需參考圖）"}


def opts_form(r, back: str) -> str:
    """每個商品：圖片來源與搭配主角。圖片來源留空 = 跟隨設定頁預設。"""
    cur = r["image_source"]
    opts = (f'<option value="" {"selected" if not cur else ""}>跟隨預設（{SRC_LABEL.get(config.DEFAULT_IMAGE_SOURCE, "")[:2]}）</option>'
            + "".join(f'<option value="{k}" {"selected" if k == cur else ""}>{v}</option>' for k, v in SRC_LABEL.items() if k != "auto"))
    return f"""<form method=post action=/products/{r["id"]}/opts class=row>
<input type=hidden name=back value="{e(back)}">
<label>圖片來源<select name=image_source>{opts}</select></label>
<label>搭配主角<select name=character_id>{_char_options(r["character_id"])}</select></label>
<button class=g>套用</button></form>"""


def mode_box(r) -> str:
    cur = r["video_mode"] or config.VIDEO_MODE
    ai_note = ("（引擎：" + config.VIDEO_PROVIDER + "，會花 API 費用/Flow 點數）" if config.VIDEO_PROVIDER
               else "（尚未選擇 AI 影片服務，請先到設定頁選）")
    return (f'<div>影片類型：<label><input type=radio name=video_mode value=slideshow {"checked" if cur == "slideshow" else ""}> A. 圖片合成 15 秒（免費）</label> '
            f'<label><input type=radio name=video_mode value=ai {"checked" if cur == "ai" else ""}> B. AI 生成影片（新圖+腳本）{e(ai_note)}</label></div>')


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
        ai_only = imagegen.source_for(r) == "ai"
        refs = "".join(f'<a href="/media/ref/{r["id"]}/{p.name}" download><img src="/media/ref/{r["id"]}/{p.name}" width=90></a>'
                       for p in imagegen.ref_files(r["id"]))
        char = characters.get(r)
        port = characters.portrait(char)
        port_html = f'<a href="/media/{e(char["ref_image"])}" download><img src="/media/{e(char["ref_image"])}" width=90></a>' if port else ""
        prompts = "".join(
            f'<textarea id=p{r["id"]}_{i} rows=3 readonly>{e(t)}</textarea>'
            f'<button type=button class=g onclick="navigator.clipboard.writeText(document.getElementById(\'p{r["id"]}_{i}\').value)">複製提示詞 {i + 1}</button>'
            for i, t in enumerate(imagegen.prompts_for(r)))
        if ai_only:
            ref_block = "<div>圖片來源：純 AI 生成（不使用任何參考圖）。</div>"
        else:
            ref_block = (f'<div>參考圖（點圖下載，貼進 Gemini 當參考）：{refs or "（尚無：請按「上網找圖」，或改用純 AI 生成）"} '
                         f'<a href="/find/{r["id"]}"><button type=button class=g>上網找圖</button></a></div>')
        if char:
            ref_block += f'<div>主角：{e(char["name"])} {port_html or "（尚無形象照，到「主角」頁上傳或產生可讓人物更一致）"}</div>'
        cards += f"""<div class=card><b>#{r["id"]} {e(r["title"] or r["url"])}</b> <a href="{e(r["url"])}" target=_blank>原商品</a>
{opts_form(r, "/todo")}</div>
<form method=post enctype=multipart/form-data class=card action=/todo/{r["id"]}>
{ref_block}
<details><summary>{config.IMAGES_PER_PRODUCT} 個提示詞</summary>{prompts}</details>
{points_box(r)}{mode_box(r)}
<div>上傳 {config.IMAGES_PER_PRODUCT} 張你產好的圖（會依序對應：開場、賣點1~3、結尾）
<input type=file name=files multiple accept="image/*"></div>
<button>上傳並送去產片</button> <button name=skip value=1 class=g formnovalidate>不做這個商品</button></form>"""
    if providers.configured("image"):
        mode = (f'<div class="flash {"ok" if config.AUTO_RUN else "warn"}">'
                + ("✅ 已開始自動處理：AI 會自動幫下面的商品產圖，產好會出現在「審圖」。這頁只在你想手動補圖、換主角或找圖時用。"
                   if config.AUTO_RUN else "AI 圖片服務已設定好，但還沒開始。請回首頁按「▶ 開始自動處理」，下面的商品就會自動產圖。")
                + '</div>' + ('' if config.AUTO_RUN else '<p><a class="btn ok" href="/">回首頁開始 →</a></p>'))
    else:
        mode = ('<div class="flash warn">還沒設定圖片 AI，所以這些商品不會自動產圖。你可以：'
                '<a href="/settings">到設定頁選圖片 AI 並填 API key</a>，或在下面每個商品手動上傳圖片。</div>')
    return page(f"<h2>待產圖（{len(rows)}）</h2>{mode}{cards or '<div class=card>沒有待處理商品。先到首頁匯入選品檔。</div>'}")


@app.post("/todo/{pid}")
async def todo_upload(pid: int, files: list[UploadFile] = File([]), points: str = Form(""), skip: str = Form(""),
                      video_mode: str = Form("")):
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
                    selected_images=json.dumps(saved), video_mode=video_mode if video_mode in ("slideshow", "ai") else "")
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
        cards += f"""<div class=card><small>要換主角或圖片來源：先改這裡按「套用」，再按下面「退回重產」。</small>{opts_form(r, "/images")}</div>
<form method=post class=card action=/images/{r["id"]}>
<b>#{r["id"]} {e(r["title"])}</b> <a href="{e(r["url"])}" target=_blank>原商品</a><div class=row>{opts}</div>
{points_box(r)}{mode_box(r)}<button name=act value=approve>核准勾選的圖</button> <button name=act value=reject class=g>退回重產</button>
<button name=act value=skip class=g>不做這個商品</button></form>"""
    return page(f"<h2>審圖（{len(rows)}）</h2>{cards or '沒有待審'}")


@app.post("/images/{pid}")
def images_act(pid: int, act: str = Form(...), selected: list[str] = Form([]), points: str = Form(""),
               video_mode: str = Form("")):
    with db.connect() as conn:
        if act == "approve" and selected:
            save_points(conn, pid, points)
            db.move(conn, pid, "image_approved", selected_image=selected[0], selected_images=json.dumps(selected),
                    video_mode=video_mode if video_mode in ("slideshow", "ai") else "")
        elif act == "reject":
            db.move(conn, pid, "sourced", images="[]")
        elif act == "skip":
            db.move(conn, pid, "skipped")
    return back("/images")


@app.get("/flow")
def flow():
    """Flow 產片：下載起始圖、複製提示詞，到 Flow 用你的點數產，再把 mp4 傳回來。"""
    with db.connect() as conn:
        rows = [r for r in db.by_status(conn, "image_approved")
                if videogen.effective_mode(r) == "ai" and json.loads(r["script"] or "{}").get("video_title")]
    note = "" if config.VIDEO_PROVIDER == "flow" else "<p>目前 AI 影片引擎不是「手動 Flow」，AI 影片會自動產；這頁只在引擎選 flow 時用。</p>"
    cards = ""
    for r in rows:
        sc = json.loads(r["script"])
        prompts = "".join(
            f'<textarea id=v{r["id"]}_{i} rows=3 readonly>{e(p)}</textarea>'
            f'<button type=button class=g onclick="navigator.clipboard.writeText(document.getElementById(\'v{r["id"]}_{i}\').value)">複製片段 {i} 提示詞</button>'
            for i, p in enumerate(scriptgen.video_prompts(sc, scriptgen.clips_needed()), 1))
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


# ------------------------------------------------------------------ 設定頁 + 簡易密碼
@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if config.APP_PASSWORD:
        ok = False
        h = request.headers.get("authorization", "")
        if h.startswith("Basic "):
            try:
                pw = base64.b64decode(h[6:]).decode().split(":", 1)[1]
                ok = secrets.compare_digest(pw, config.APP_PASSWORD)
            except Exception:  # noqa: BLE001
                ok = False
        if not ok:
            return Response("需要密碼", status_code=401, headers={"WWW-Authenticate": 'Basic realm="shopee_clips"'})
    return await call_next(request)


BLANK = "— 請選擇 —"
ROLE_CARDS = [  # (role, 標題, 服務欄位, 模型欄位, [(值, 名稱)], 不選的說明)
    ("text", "腳本文案（賣點、腳本、標題、貼文文案、配音稿）", "TEXT_PROVIDER", "TEXT_MODEL",
     [("gemini", "Google Gemini"), ("openai", "OpenAI / 相容服務"), ("claude", "Anthropic Claude")],
     "不選 = 不用 AI，用你填的賣點 + 範本"),
    ("image", "圖片（賣家圖當參考，重新生成全新商品圖）", "IMAGE_PROVIDER", "IMAGE_MODEL",
     [("gemini", "Google Gemini"), ("openai", "OpenAI"), ("browser", "操控我的 Chrome 用 Gemini 網頁（免 API）"),
      ("manual", "手動上傳（免 API）")],
     "不選 = 手動上傳"),
    ("video", "AI 生成影片（影片類型 B）", "VIDEO_PROVIDER", "VIDEO_MODEL",
     [("veo", "Google Veo"), ("fal", "fal.ai（Kling 等）"), ("flow_browser", "操控我的 Chrome 用 Flow 點數（免 API）"),
      ("flow", "手動：我自己在 Flow 產、上傳 mp4")],
     "不選 = 只能用類型 A 圖片合成"),
]
OTHER_OPTIONS = {
    "DEFAULT_IMAGE_SOURCE": [("auto", "自動（有參考圖就用，沒有就純 AI 生成）"), ("web", "上網找圖當參考（AI 重新生成）"), ("ai", "純 AI 生成（不需參考圖）")],
    "AUTO_ENRICH": [("0", "關（預設）"), ("1", "開（背景自動開 Chrome 去蝦皮商品頁補標題/圖片）")],
    "AI_LABEL": [("1", "開（影片左上角顯示「AI 生成」）"), ("0", "關")],
    "VIDEO_MODE": [("slideshow", "A. 圖片合成 15 秒（免費、不用 API）"), ("ai", "B. AI 生成影片（用新圖+腳本，花錢/點數）")],
    "ALLOW_PLAIN_LINK": [("0", "不允許（預設）：沒有分潤連結的商品不會自動上架"), ("1", "允許用一般連結（發了不會有分潤）")],
    "UPLOAD_MODE": [("manual", "只匯出上架包（我在手機自己傳）"), ("phone_dryrun", "Android 手機自動操作，但不按發佈（先測這個）"),
                    ("phone_auto", "Android 手機自動操作並發佈")],
    "TTS": [("1", "開（曉臻）"), ("0", "關")],
    "SUBTITLES": [("1", "開（只含賣點內容文字）"), ("0", "關")],
}
LABELS = {
    "IMAGES_PER_PRODUCT": "每商品幾張圖", "VIDEO_MODE": "預設影片類型（每個商品可在審圖頁單獨改）", "DEFAULT_IMAGE_SOURCE": "預設圖片來源（每個商品可單獨改）", "AUTO_ENRICH": "自動補商品資料", "ALLOW_PLAIN_LINK": "沒有分潤連結時", "AI_LABEL": "「AI 生成」標示",
    "VIDEO_CLIP_SECONDS": "AI 影片單段秒數（0=依服務預設）", "FAL_EXTRA_ARGS": "fal 額外參數 JSON（選填）",
    "OPENAI_BASE_URL": "OpenAI Base URL（用相容 OpenAI 的服務才填）", "TTS": "配音", "TTS_VOICE": "配音聲音", "SUBTITLES": "字幕",
    "DAILY_GEN_CAP": "每日最多用 AI 產幾支影片（花錢/點數上限）", "DAILY_UPLOAD_CAP": "每日最多上架幾支", "UPLOAD_MODE": "上架方式",
    "PHONE_SERIAL": "手機序號（接多支手機才需要）", "PHONE_PACKAGE": "蝦皮 App 套件名稱",
    "CLOUD_ENDPOINT": "Endpoint（R2/B2/MinIO 才填；AWS S3 留空）", "CLOUD_BUCKET": "Bucket 名稱", "CLOUD_ACCESS_KEY": "Access key",
    "CLOUD_SECRET_KEY": "Secret key", "CLOUD_PUBLIC_BASE": "公開網址前綴（bucket 已公開才填；留空=7 天預簽名連結）",
    "AFFILIATE_PICKS_URL": "分潤後台選品頁網址（選填）",
    "APP_PASSWORD": "網頁密碼（選填；手機/區網使用建議設）", "FLOW_CLIPS_PER_PRODUCT": "Flow 每商品幾段",
}
SPEC_TYPES = {n: t for n, _, t in config.SPEC}


def _field(name: str) -> str:
    label = e(LABELS.get(name, name))
    if name in OTHER_OPTIONS:
        cur = getattr(config, name)
        curv = ("1" if cur else "0") if SPEC_TYPES[name] is bool else cur
        opts = "".join(f'<option value="{v}" {"selected" if v == curv else ""}>{e(t)}</option>' for v, t in OTHER_OPTIONS[name])
        return f"<label>{label}<select name={name}>{opts}</select></label>"
    if name in config.SECRETS:
        return (f'<label>{label}<input type=password name={name} autocomplete=off '
                f'placeholder="{"已設定，留空=不變更" if getattr(config, name) else ""}"></label>')
    return f'<label>{label}<input type=text name={name} value="{e(config.raw(name))}"></label>'


def _key_hint(provider: str) -> str:
    k = config.KEY_FOR.get(provider)
    return f"✅ 已設定（…{getattr(config, k)[-4:]}）" if k and getattr(config, k) else "尚未設定"


def _role_card(role, title, pfield, mfield, options, none_hint) -> str:
    cur = getattr(config, pfield)
    opts = f'<option value="" {"selected" if not cur else ""}>{BLANK}</option>' + "".join(
        f'<option value="{v}" {"selected" if v == cur else ""}>{e(t)}</option>' for v, t in options)
    needs_key = cur in config.KEY_FOR
    sugg = "".join(f'<option value="{e(m)}">' for m in config.MODEL_SUGGESTIONS.get((role, cur), []))
    test = {"text": "測試", "image": "測試（會實際產 1 張圖，約幾分錢）", "video": "檢查 key 與模型"}[role]
    return f"""<div class=card id=card_{role}><b>{e(title)}</b> <small>{e(none_hint)}</small>
<label>用誰<select name={pfield} id=sel_{role}>{opts}</select></label>
<div id=fields_{role} style="display:{'block' if needs_key else 'none'}">
<label>模型（點一下從清單選，或自己輸入名稱）<input type=text name={mfield} id=model_{role} list=dl_{role} value="{e(config.raw(mfield))}" autocomplete=off></label>
<datalist id=dl_{role}>{sugg}</datalist>
<label>API key <small id=hint_{role}>{_key_hint(cur)}</small><input type=password name=key_{role} autocomplete=off placeholder="貼上這一家的 key；留空=不變更"></label>
<button type=submit formaction="/settings/test/{role}" class=g formnovalidate>{test}</button></div></div>"""


def _settings_js() -> str:
    meta = {role: {v: {"key": v in config.KEY_FOR, "models": config.MODEL_SUGGESTIONS.get((role, v), []),
                       "hint": _key_hint(v)} for v, _ in opts} for role, _, _, _, opts, _ in ROLE_CARDS}
    return f"""<script>
const META = {json.dumps(meta, ensure_ascii=False)};
function sync(role, changed) {{
  const v = document.getElementById('sel_' + role).value, m = (META[role] || {{}})[v];
  document.getElementById('fields_' + role).style.display = m && m.key ? 'block' : 'none';
  if (!m) return;
  document.getElementById('hint_' + role).textContent = m.hint;
  document.getElementById('dl_' + role).innerHTML = m.models.map(x => '<option value="' + x + '">').join('');
  if (changed) document.getElementById('model_' + role).value = '';   // 換一家就不沿用上一家的模型名稱
}}
for (const role of Object.keys(META)) {{
  document.getElementById('sel_' + role).addEventListener('change', () => sync(role, true));
  sync(role, false);
}}
</script>"""


def _card(title: str, names: list[str]) -> str:
    return f'<div class=card><b>{e(title)}</b>{"".join(_field(n) for n in names)}</div>'


@app.get("/settings")
def settings(saved: int = 0):
    note = "<div class=card>✅ 已儲存並立即生效</div>" if saved else ""
    status = "".join(f"<li>{e(x)}</li>" for x in providers.summary())
    cards = "".join(_role_card(*c) for c in ROLE_CARDS)
    body = (f'<div class=card><b>目前狀態</b><ul>{status}</ul><small>每一項都由你選擇服務並填入那一家的 key；沒有任何預設的服務或模型。</small></div>'
            + cards + _card("預設值", ["DEFAULT_IMAGE_SOURCE", "VIDEO_MODE"]) + _card("配音、字幕與標示", ["TTS", "TTS_VOICE", "SUBTITLES", "AI_LABEL"])
            + _card("流程與上架（蝦皮短影音只有手機版）", ["DAILY_GEN_CAP", "DAILY_UPLOAD_CAP", "UPLOAD_MODE", "AFFILIATE_PICKS_URL", "APP_PASSWORD"])
            + _card("Android 手機自動上架（用 USB 偵錯操作蝦皮 App）", ["PHONE_SERIAL", "PHONE_PACKAGE"])
            + _card("雲端上傳（選填，S3 相容）", ["CLOUD_ENDPOINT", "CLOUD_BUCKET", "CLOUD_ACCESS_KEY", "CLOUD_SECRET_KEY", "CLOUD_PUBLIC_BASE"])
            + '<details class=card><summary><b>進階</b></summary>'
            + "".join(_field(n) for n in ["AUTO_ENRICH", "ALLOW_PLAIN_LINK", "IMAGES_PER_PRODUCT", "OPENAI_BASE_URL", "VIDEO_CLIP_SECONDS", "FAL_EXTRA_ARGS", "FLOW_CLIPS_PER_PRODUCT"])
            + "</details>")
    return page(f"""<h2>設定</h2>{note}<style>label{{display:block;margin:8px 0}}select,input[type=text],input[type=password]{{width:100%;box-sizing:border-box;padding:8px;font:inherit}}</style>
<form method=post action=/settings>{body}<button>儲存設定</button></form>{_settings_js()}
<div class=card><b>最近紀錄</b><pre>{e(chr(10).join(worker.log[-8:]))}</pre></div>""")


@app.post("/settings")
async def settings_save(request: Request):
    form = await request.form()
    updates = {}
    for name, default, typ in config.SPEC:
        if name not in form:
            continue
        v = str(form[name]).replace("\n", " ").replace("\r", " ").strip()
        if name in config.SECRETS and not v:
            continue  # 秘密欄位留空 = 不變更
        if typ is int:
            try:
                int(v or 0)
            except ValueError:
                continue
        updates[name] = v
    for role, _, pfield, _, _, _ in ROLE_CARDS:  # 每張卡的 key 存到「該卡選的那一家」的金鑰欄位
        key_env = config.KEY_FOR.get(updates.get(pfield, getattr(config, pfield)))
        k = str(form.get(f"key_{role}", "")).replace("\n", "").replace("\r", "").strip()
        if key_env and k:
            updates[key_env] = k
    config.save_env(updates)
    return back("/settings?saved=1")


@app.post("/settings/test/{role}")
async def settings_test(role: str, request: Request):
    await settings_save(request)  # 先存目前表單內容再測
    if role in ("text", "image", "video"):
        def run():
            msg = providers.check(role)
            worker.flash(msg, "ok" if msg.startswith(("✅", "ℹ️")) else "err")

        worker.flash("⏳ 測試中…完成後會顯示在這裡（重新整理本頁）", "warn")
        threading.Thread(target=run, daemon=True).start()
    return back("/settings?saved=1")


# ------------------------------------------------------------------ 圖片來源 / 主角 / 找圖
VOICES = [("zh-TW-HsiaoChenNeural", "曉臻（女）"), ("zh-TW-HsiaoYuNeural", "曉雨（女）"), ("zh-TW-YunJheNeural", "雲哲（男）")]
SEARCHING: set[int] = set()  # 正在上網找圖的商品


def _safe_back(path: str) -> str:
    return path if path.startswith("/") and not path.startswith("//") else "/"


@app.post("/products/{pid}/opts")
def product_opts(pid: int, image_source: str = Form(""), character_id: int = Form(0), back: str = Form("/")):
    with db.connect() as conn:
        db.update(conn, pid, image_source=image_source if image_source in ("web", "ai") else "",  # 空白/auto = 跟隨預設
                  
                  character_id=character_id if db.get_character(conn, character_id) else 0)
    return RedirectResponse(_safe_back(back), status_code=303)


@app.post("/apply-defaults")
def apply_defaults(image_source: str = Form(""), character_id: str = Form("keep")):
    """批次：把圖片來源/主角套用到所有『尚未產圖』的商品。"""
    n = 0
    with db.connect() as conn:
        for r in db.by_status(conn, "sourced"):
            f = {}
            if image_source in ("web", "ai"):
                f["image_source"] = image_source
            if character_id != "keep" and character_id.isdigit():
                f["character_id"] = int(character_id) if db.get_character(conn, int(character_id)) else 0
            if f:
                db.update(conn, r["id"], **f)
                n += 1
    worker.flash(f"✅ 已套用到 {n} 個尚未產圖的商品。", "ok" if n else "warn")
    return back()


@app.get("/find/{pid}")
def find_page(pid: int, engine: str = "google", q: str = "", msg: str = ""):
    with db.connect() as conn:
        r = db.get(conn, pid)
    if not r:
        return back("/todo")
    query = q or r["title"]
    cands = imgsearch.candidates(pid)
    busy = pid in SEARCHING
    picked = imagegen.ref_files(pid)
    eng = "".join(f'<label><input type=radio name=engine value={k} {"checked" if k == engine else ""}>{v}</label> '
                  for k, v in imgsearch.ENGINE_NAMES.items())
    grid = "".join(f'<label style="display:inline-block;margin:4px"><input type=checkbox name=files value="{e(c["file"])}">'
                   f'<img src="/media/ref_candidates/{pid}/{e(c["file"])}" width=110></label>' for c in cands)
    picked_html = "".join(f'<img src="/media/ref/{pid}/{p.name}" width=80> ' for p in picked if p.name.startswith("w"))
    refresh = '<meta http-equiv=refresh content=3>' if busy else ""
    return page(f"""{refresh}<h2>上網找圖：#{pid} {e(r["title"][:30])}</h2>
<p>找來的圖<b>只當 AI 產圖的參考</b>（用來讓 AI 畫出正確的包裝與外觀），不會原樣用在影片裡。找不到好圖就 <a href="/todo">回待產圖頁改用「純 AI 生成」</a>。</p>
{f'<div class=card>{e(msg)}</div>' if msg else ''}{'<div class=card>搜尋中…請稍候（會自動重新整理）。需要自動化 Chrome 已開啟。</div>' if busy else ''}
<form method=post action=/find/{pid}/search class=card><label>搜尋關鍵字<input type=text name=q value="{e(query)}"></label>{eng}
<button>一鍵找圖</button></form>
<form method=post action=/find/{pid}/add class=card><b>候選圖（勾選要當參考的）</b><div>{grid or '尚無候選；按上面「一鍵找圖」，或自己搜尋後把圖存下來上傳。'}</div>
<button>加入勾選的圖當參考</button></form>
<form method=post enctype=multipart/form-data action=/find/{pid}/upload class=card><b>或上傳你自己找的圖</b><input type=file name=files multiple accept="image/*"><button class=g>上傳</button></form>
<div class=card><b>目前已選的參考圖</b><div>{picked_html or '尚無'}</div>
<form method=post action=/find/{pid}/notes><label>來源與授權紀錄（選填；自動記錄來源網址，你可補充授權說明）
<textarea name=notes rows=4>{e(r["ref_notes"])}</textarea></label><button class=g>儲存紀錄</button></form></div>
<a href="/todo">← 回待產圖</a>""")


@app.post("/find/{pid}/search")
def find_search(pid: int, q: str = Form(""), engine: str = Form("google")):
    with db.connect() as conn:
        r = db.get(conn, pid)
    query = q.strip() or (r["title"] if r else "")
    SEARCHING.add(pid)

    def run():
        with worker.browser_lock:
            try:
                with browser.open_context() as ctx:
                    n = imgsearch.search(ctx, pid, engine, query)
                worker.flash(f"✅ #{pid} 找圖完成：{engine} 取得 {n} 張候選圖" if n else f"⚠ #{pid} 沒找到合適的圖，換個關鍵字或搜尋引擎，或改用純 AI 生成", "ok" if n else "warn")
            except Exception as ex:  # noqa: BLE001
                worker.flash(f"❌ #{pid} 找圖失敗：{ex}", "err")
            finally:
                SEARCHING.discard(pid)

    threading.Thread(target=run, daemon=True).start()
    return back(f"/find/{pid}?engine={engine}")


@app.post("/find/{pid}/add")
async def find_add(pid: int, request: Request):
    form = await request.form()
    with db.connect() as conn:
        n = imgsearch.add_picks(conn, pid, [str(x) for x in form.getlist("files")])
    return back(f"/find/{pid}?msg=已加入+{n}+張參考圖")


@app.post("/find/{pid}/upload")
def find_upload(pid: int, files: list[UploadFile] = File([])):
    with db.connect() as conn:
        n = imgsearch.add_uploaded(conn, pid, [(f.filename or "", f.file.read()) for f in files])
    return back(f"/find/{pid}?msg=已上傳+{n}+張參考圖")


@app.post("/find/{pid}/notes")
def find_notes(pid: int, notes: str = Form("")):
    with db.connect() as conn:
        db.update(conn, pid, ref_notes=notes.strip())
    return back(f"/find/{pid}?msg=已儲存紀錄")


# ---- 主角
def _save_portrait(conn, cid: int, data: bytes) -> bool:
    jpg = imgsearch._to_jpeg(data)
    if not jpg:
        return False
    d = config.DATA_DIR / "characters"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{cid}.jpg").write_bytes(jpg)
    conn.execute("UPDATE characters SET ref_image=? WHERE id=?", (f"characters/{cid}.jpg", cid))
    return True


def _voice_select(name: str, cur: str) -> str:
    opts = "".join(f'<option value="{v}" {"selected" if v == cur else ""}>{t}</option>' for v, t in VOICES)
    return f"<select name={name}>{opts}</select>"


@app.get("/characters")
def characters_page(msg: str = ""):
    with db.connect() as conn:
        chars = db.list_characters(conn)
    cards = ""
    for c in chars:
        pic = (f'<img src="/media/{e(c["ref_image"])}?v={int(time.time())}" width=110>' if c["ref_image"] else "<small>尚無形象照</small>")
        delete = "" if c["builtin"] else f'<form method=post action=/characters/{c["id"]}/delete style="display:inline"><button class=g>刪除</button></form>'
        gen = (f'<form method=post action=/characters/{c["id"]}/generate style="display:inline"><button class=g>用 AI 產生形象照</button></form>'
               if providers.configured("image") else "")
        cards += f"""<div class=card><b>{e(c["name"])}</b> {"<small>（內建・虛構 AI 角色）</small>" if c["builtin"] else ""}
<div>{pic}</div><small>{e(c["description"])}</small>
<form method=post enctype=multipart/form-data action=/characters/{c["id"]}/portrait class=row>
<input type=file name=file accept="image/*"><button class=g>上傳形象照</button></form>
<form method=post action=/characters/{c["id"]}/voice class=row><label>配音聲音{_voice_select("voice", c["voice"])}</label><button class=g>儲存</button></form>
{gen}{delete}</div>"""
    default = f'<form method=post action=/characters/default class=card><label>新商品預設搭配的主角<select name=character_id>{_char_options(config.DEFAULT_CHARACTER_ID, "不加人物")}</select></label><button>儲存預設</button></form>'
    add = f"""<form method=post enctype=multipart/form-data action=/characters/new class=card><b>新增自訂主角</b>
<label>名稱<input type=text name=name></label>
<label>外貌/穿搭描述（英文最佳；有形象照可留空）<textarea name=description rows=3 placeholder="a cheerful Taiwanese woman in her 30s, short hair, green cardigan"></textarea></label>
<label>形象照（選填；建議正面、清楚、單人）<input type=file name=file accept="image/*"></label>
<label>配音聲音{_voice_select("voice", VOICES[0][0])}</label><button>新增</button></form>"""
    return page(f"""<h2>主角</h2>{f'<div class=card>{e(msg)}</div>' if msg else ''}
<p>選了主角，AI 產圖與影片提示詞會讓這個人出現並展示商品；有<b>形象照</b>會當參考，讓同一個主角在不同圖片/影片裡長得一致。
內建角色都是虛構的 AI 角色。自訂形象照請只用<b>你本人、已取得同意的人，或 AI 生成的角色</b>，不要使用他人肖像。</p>
{default}{cards}{add}""")


@app.post("/characters/default")
def characters_default(character_id: int = Form(0)):
    config.save_env({"DEFAULT_CHARACTER_ID": str(character_id)})
    return back("/characters?msg=已儲存預設主角")


@app.post("/characters/new")
def characters_new(name: str = Form(...), description: str = Form(""), voice: str = Form(VOICES[0][0]),
                   file: UploadFile = File(None)):
    data = file.file.read() if file is not None else b""
    if not name.strip() or not (description.strip() or data):
        return back("/characters?msg=請填名稱，並至少提供外貌描述或形象照")
    desc = description.strip() or "the person shown in the character reference image"
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO characters (key,name,description,voice,builtin) VALUES (?,?,?,?,0)",
                           (f"custom-{secrets.token_hex(4)}", name.strip(), desc, voice))
        if data and not _save_portrait(conn, cur.lastrowid, data):
            return back("/characters?msg=已新增，但形象照無法讀取（請換一張）")
    return back("/characters?msg=已新增")


@app.post("/characters/{cid}/portrait")
def characters_portrait(cid: int, file: UploadFile = File(...)):
    with db.connect() as conn:
        ok = _save_portrait(conn, cid, file.file.read())
    return back("/characters?msg=" + ("已更新形象照" if ok else "圖片無法讀取"))


@app.post("/characters/{cid}/voice")
def characters_voice(cid: int, voice: str = Form(...)):
    if voice in dict(VOICES):
        with db.connect() as conn:
            conn.execute("UPDATE characters SET voice=? WHERE id=?", (voice, cid))
    return back("/characters")


@app.post("/characters/{cid}/generate")
def characters_generate(cid: int):
    def run():
        try:
            with db.connect() as conn:
                c = db.get_character(conn, cid)
                data = providers.image_bytes(characters.PORTRAIT_PROMPT.format(desc=c["description"]), [])
                _save_portrait(conn, cid, data)
            worker.flash(f"✅ 主角「{c['name']}」形象照已產生", "ok")
        except Exception as ex:  # noqa: BLE001
            worker.flash(f"❌ 產生形象照失敗：{ex}", "err")

    threading.Thread(target=run, daemon=True).start()
    return back("/characters?msg=產生中，完成後重新整理本頁（費用依你的圖片服務計）")


@app.post("/characters/{cid}/delete")
def characters_delete(cid: int):
    with db.connect() as conn:
        c = db.get_character(conn, cid)
        if c and not c["builtin"]:
            conn.execute("UPDATE products SET character_id=0 WHERE character_id=?", (cid,))
            conn.execute("DELETE FROM characters WHERE id=?", (cid,))
            if config.DEFAULT_CHARACTER_ID == cid:
                config.save_env({"DEFAULT_CHARACTER_ID": "0"})
    return back("/characters")


# ------------------------------------------------------------------ 管理列表
@app.get("/list")
def list_page(status: str = ""):
    with db.connect() as conn:
        counts = db.counts(conn)
        rows = db.all_products(conn, status if status in STATUS_ZH else "")
    tabs = f'<a href="/list">全部（{sum(counts.values())}）</a> ' + " ".join(
        f'<a href="/list?status={k}">{v}（{counts.get(k, 0)}）</a>' for k, v in STATUS_ZH.items())
    body = ""
    for r in rows:
        video = f'<a href="/media/{e(r["video_path"])}" target=_blank>影片</a> ' if r["video_path"] else ""
        cl = f'<a href="{e(r["cloud_url"])}" target=_blank>☁</a> ' if r["cloud_url"] else ""
        extra = f'<div class=err>{e(r["error"])}</div>' if r["error"] else ""
        body += (f'<tr><td>{r["id"]}</td><td>{e((r["video_title"] or r["title"])[:28])}{extra}</td>'
                 f'<td>{STATUS_ZH.get(r["status"], r["status"])}</td>'
                 f'<td>{video}{cl}<a href="{e(r["source_url"] or r["url"])}" target=_blank>商品</a></td></tr>')
    return page(f"""<h2>管理列表</h2><p>{tabs}</p><p><a href="/list.csv"><button type=button class=g>匯出 CSV</button></a></p>
<table style="width:100%;border-collapse:collapse;font-size:14px"><tr><th>#</th><th>標題</th><th>狀態</th><th>連結</th></tr>{body}</table>
{'' if rows else '沒有項目'}""")


@app.get("/list.csv")
def list_csv():
    import csv
    import io

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "狀態", "商品名稱", "商品連結", "原始連結", "影片標題", "影片文案", "影片檔", "雲端連結", "錯誤"])
    with db.connect() as conn:
        for r in db.all_products(conn):
            w.writerow([r["id"], STATUS_ZH.get(r["status"], r["status"]), r["title"], r["url"], r["source_url"],
                        r["video_title"], r["video_caption"], r["video_path"], r["cloud_url"], r["error"]])
    return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=shopee_clips.csv"})


# ------------------------------------------------------------------ 檢查（遇到問題時把這頁截圖給我）
@app.get("/status")
def status_page():
    import platform
    import sys

    from . import slideshow

    def row(ok, label, detail=""):
        return f"<tr><td>{'✅' if ok else '❌'}</td><td>{e(label)}</td><td><small>{e(detail)}</small></td></tr>"

    rows = [row(True, "Python", f"{sys.version.split()[0]} / {platform.system()} {platform.release()}"),
            row(bool(shutil.which("ffmpeg")), "ffmpeg（做影片需要）", shutil.which("ffmpeg") or "找不到：命令列執行 winget install ffmpeg")]
    try:
        rows.append(row(True, "Google Chrome（找圖/Gemini 網頁/Flow 才需要）", browser.find_chrome()))
    except Exception as ex:  # noqa: BLE001
        rows.append(row(False, "Google Chrome（找圖/Gemini 網頁/Flow 才需要）", str(ex)))
    try:
        rows.append(row(True, "中文字型（影片字幕）", slideshow.find_font()))
    except Exception as ex:  # noqa: BLE001
        rows.append(row(False, "中文字型（影片字幕）", str(ex)))
    try:
        import uiautomator2  # noqa: F401
        rows.append(row(True, "手機自動化套件 uiautomator2", "已安裝（手機是否連線請在首頁「進階工具」測試）"))
    except Exception as ex:  # noqa: BLE001
        rows.append(row(False, "手機自動化套件 uiautomator2", str(ex)))
    for role, name in (("text", "腳本 AI"), ("image", "圖片 AI"), ("video", "AI 影片")):
        m = providers.missing(role)
        p = getattr(config, f"{role.upper()}_PROVIDER")
        rows.append(row(m is None and bool(p), name, f"服務：{p or '未選擇'}" + (f"；{m}" if m else "")))
    with db.connect() as conn:
        counts = db.counts(conn)
    rows.append(row(True, "資料資料夾", str(config.DATA_DIR.resolve())))
    rows.append(row(config.ENV_PATH.exists(), "設定檔 .env", str(config.ENV_PATH.resolve())))
    rows.append(row(True, "商品數量", "、".join(f"{STATUS_ZH[k]} {v}" for k, v in counts.items() if v) or "0"))
    rows.append(row(config.AUTO_RUN, "自動處理", "執行中" if config.AUTO_RUN else "未開始（首頁按「開始」）"))
    rows.append(row(True, "背景目前", worker.state["step"] or "閒置"))
    return page(f"""<h2>檢查</h2><p><small>遇到問題時，把這一頁截圖給我，我就能知道是哪裡有問題。</small></p>
<div class=card><table>{''.join(rows)}</table></div>
<div class=card><b>最近紀錄</b><pre>{e(chr(10).join(worker.log[-40:]))}</pre></div>""")


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


def aff_link_html(r) -> str:
    if db.is_affiliate(r["source_url"]):
        return f'<a href="{e(r["source_url"])}" target=_blank>分潤連結</a>'
    return (f'<span class="err">⚠ 沒有分潤連結</span> <a href="{e(r["url"])}" target=_blank>一般商品連結（發了不會有分潤）</a>')


def cloud_html(r) -> str:
    """雲端下載連結 + QR（手機掃一下就能下載影片，不必在同一個 Wi-Fi）。"""
    if not r["cloud_url"]:
        return ""
    return (f'<div><a href="{e(r["cloud_url"])}" target=_blank>☁ 雲端下載連結</a>'
            f'<div style="width:140px;margin-top:6px">{cloud.qr_svg(r["cloud_url"])}</div></div>')


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
{aff_link_html(r)}
{cloud_html(r)}
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
