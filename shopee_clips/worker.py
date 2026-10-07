"""背景自動流程。人工審核卡在網頁上；你按核准後，下一輪自動往下走。
sourced -> (產圖) -> image_review [你審] -> (產片) -> video_review [你審] -> (上傳雲端 + 手機自動上架 / 匯出上架包)
會花 API 費用、用 Chrome、操作手機的步驟，要在首頁按「開始」(AUTO_RUN) 才會跑；免費的本機步驟（圖片合成影片、匯出上架包）一直在跑。
"""
import threading
import time
import traceback

from . import cloud, config, db, imagegen, social, sourcing, uploader, videogen

browser_lock = threading.Lock()  # 同一個自動化 Chrome 同時只能做一件事
log: list[str] = []
flashes: list[tuple[str, str]] = []  # 顯示在下一個頁面最上方的訊息（顯示一次就清掉）
state = {"step": "", "since": 0.0}  # 目前背景正在做什麼，給首頁即時顯示


def say(msg: str) -> None:
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    log.append(line)
    del log[:-200]


def flash(msg: str, kind: str = "ok") -> None:
    """kind: ok(綠) | err(紅) | warn(黃)。同時寫進執行紀錄。"""
    flashes.append((kind, msg))
    say(msg)


def take_flashes() -> list[tuple[str, str]]:
    out = flashes[:]
    flashes.clear()
    return out


def _set(step: str) -> None:
    state["step"], state["since"] = step, time.time()


def tick() -> None:
    run = config.AUTO_RUN
    with db.connect() as conn:
        steps = []
        if config.AUTO_ENRICH:
            steps.append(("補商品資料", lambda: _locked(sourcing.enrich, conn)))
        if run:
            steps.append(("產圖", lambda: _locked(imagegen.run, conn)))
        steps.append(("產片", lambda: _locked(lambda c: videogen.run(c, allow_ai=run), conn)))
        steps.append(("上傳雲端", lambda: cloud.run(conn)))
        # 沒按「開始」時，上架只匯出上架包，不會去操作手機
        steps.append(("上架", lambda: _locked(lambda c: uploader.run(c, None if run else "manual"), conn)))
        if config.SOCIAL_AUTO:  # 自己在「發文」頁打開才會跑；有每日上限與間隔
            steps.append(("自動發文", lambda: _locked(social.run_queue, conn)))
        if config.SOCIAL_REPLY_AUTO:
            steps.append(("回覆問連結的留言", lambda: _locked(social.auto_replies, conn)))
        for name, fn in steps:
            _set(name)
            try:
                n = fn()
                if n:
                    say(f"{name}：處理 {n} 筆")
            except SystemExit as e:
                say(f"{name} 無法執行：{e}")
            except Exception as e:  # noqa: BLE001
                say(f"{name} 失敗：{e}")
                traceback.print_exc()
    _set("")


def _locked(fn, conn):
    with browser_lock:
        return fn(conn)


def loop(stop: threading.Event) -> None:
    say("背景流程啟動")
    while not stop.is_set():
        tick()
        stop.wait(config.POLL_SECONDS)
