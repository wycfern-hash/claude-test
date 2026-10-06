"""背景自動流程。人工審核卡在網頁上；你按核准後，下一輪自動往下走。
sourced -> (自動 enrich + 產圖) -> image_review [你審] -> (自動產片) -> video_review [你審] -> (自動上架/匯出上架包)
"""
import threading
import time
import traceback

from . import config, db, imagegen, sourcing, uploader, videogen

browser_lock = threading.Lock()  # 同一個 profile 同時只能一個 Playwright
log: list[str] = []


def say(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    log.append(line)
    del log[:-200]


def tick() -> None:
    with db.connect() as conn:
        steps = [
            ("補商品資料", lambda: _locked(sourcing.enrich, conn)),
            ("產圖", lambda: imagegen.run(conn)),
            ("產片", lambda: videogen.run(conn)),
            ("上架", lambda: _locked(uploader.run, conn)),
        ]
        for name, fn in steps:
            try:
                n = fn()
                if n:
                    say(f"{name}：處理 {n} 筆")
            except SystemExit as e:
                say(f"{name} 無法執行：{e}")
            except Exception as e:  # noqa: BLE001
                say(f"{name} 失敗：{e}")
                traceback.print_exc()


def _locked(fn, conn):
    with browser_lock:
        return fn(conn)


def loop(stop: threading.Event) -> None:
    say("背景流程啟動")
    while not stop.is_set():
        tick()
        stop.wait(config.POLL_SECONDS)
