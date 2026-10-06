"""操控「你自己的 Chrome」。程式在你電腦上開一個自動化專用的 Chrome（獨立資料夾 data/browser_profile），
你在裡面手動登入 Google（Gemini/Flow）和蝦皮一次，之後程式透過 CDP 連上去操作。
用真的 Chrome + 手動登入，Google 才不會把登入當成不安全的自動化瀏覽器擋掉。程式不接觸你的帳密。
"""
import os
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
]


def find_chrome() -> str:
    for c in [config.CHROME_PATH, *CANDIDATES]:
        if not c:
            continue
        p = c if os.path.isabs(c) else shutil.which(c)
        if p and Path(p).exists():
            return p
    raise RuntimeError("找不到 Chrome，請安裝 Google Chrome，或在 .env 設定 CHROME_PATH")


def port_open() -> bool:
    try:
        socket.create_connection(("127.0.0.1", config.CDP_PORT), 0.5).close()
        return True
    except OSError:
        return False


def ensure_chrome() -> None:
    if port_open():
        return
    config.ensure_dirs()
    args = [find_chrome(), f"--remote-debugging-port={config.CDP_PORT}", f"--user-data-dir={config.BROWSER_PROFILE.resolve()}",
            "--no-first-run", "--no-default-browser-check", "about:blank"]
    if config.CHROME_HEADLESS:
        args[1:1] = ["--headless=new", "--no-sandbox"]
    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        if port_open():
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome 沒有成功開啟除錯連線（請先把已開著的自動化 Chrome 視窗全部關掉再試）")


@contextmanager
def open_context():
    """連上自動化專用 Chrome；離開時只中斷連線，不會關掉你的 Chrome 視窗。"""
    from playwright.sync_api import sync_playwright

    ensure_chrome()
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f"http://127.0.0.1:{config.CDP_PORT}")
        try:
            yield b.contexts[0] if b.contexts else b.new_context()
        finally:
            b.close()


def open_login_tabs() -> None:
    """開自動化 Chrome 並打開 Google / Flow / 蝦皮分頁，讓你手動登入。"""
    with open_context() as ctx:
        for url in ("https://gemini.google.com", "https://labs.google/fx/tools/flow", "https://shopee.tw"):
            ctx.new_page().goto(url)
