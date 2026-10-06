"""網頁自動化共用工具：用文字標籤定位、失敗時留診斷資料（截圖 + 頁面元件清單），
這樣 Gemini/Flow/蝦皮 改版或我猜錯標籤時，把 data/debug 的檔案給我就能精準修。"""
import json
import re
import time
from pathlib import Path

from . import config

SITES_PATH = Path("config/browser_sites.json")
DEBUG_DIR = config.DATA_DIR / "debug"

ELEMENTS_JS = """() => [...document.querySelectorAll(
  'button,[role=button],a[href],input,textarea,select,[contenteditable=true],[role=textbox],[role=menuitem],[role=option],[role=tab]')]
  .slice(0, 400).map(e => ({tag: e.tagName.toLowerCase(), role: e.getAttribute('role') || '',
    text: (e.innerText || e.value || '').trim().slice(0, 50), aria: e.getAttribute('aria-label') || '',
    placeholder: e.getAttribute('placeholder') || '', type: e.getAttribute('type') || '',
    visible: !!(e.offsetWidth || e.offsetHeight)}))"""


def rx(pattern: str):
    return re.compile(pattern, re.I)


def site(name: str) -> dict:
    return json.loads(SITES_PATH.read_text(encoding="utf-8"))[name]


def dump_page(page, tag: str) -> str:
    """存截圖 + 可操作元件清單，回傳檔名前綴。"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    base = DEBUG_DIR / f"{time.strftime('%m%d_%H%M%S')}_{re.sub(r'[^0-9A-Za-z_一-鿿]', '_', tag)}"
    try:
        page.screenshot(path=f"{base}.png")
    except Exception:  # noqa: BLE001
        pass
    try:
        info = {"url": page.url, "title": page.title(), "elements": page.evaluate(ELEMENTS_JS)}
        Path(f"{base}.json").write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return str(base)


def step(page, name: str, fn, optional: bool = False):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if optional:
            return None
        base = dump_page(page, name)
        raise RuntimeError(f"卡在「{name}」（診斷檔 {base}.png/.json）：{str(e)[:150]}") from e


def probe_tabs(ctx) -> list[str]:
    """把目前所有分頁的畫面結構存起來（你先手動停在要我看的畫面，再按首頁的按鈕）。"""
    out = []
    for i, pg in enumerate(ctx.pages):
        out.append(dump_page(pg, f"probe{i}"))
    return out


def img_srcs(page, min_px: int) -> set[str]:
    return set(page.evaluate(
        "min => [...document.images].filter(i => i.naturalWidth >= min).map(i => i.src)", min_px))


def wait_new_image(page, before: set[str], min_px: int, timeout_s: int) -> str:
    end = time.time() + timeout_s
    while time.time() < end:
        new = img_srcs(page, min_px) - before
        if new:
            time.sleep(3)  # 讓圖載完整/升到高解析
            return sorted(img_srcs(page, min_px) - before)[-1]
        time.sleep(2)
    raise TimeoutError(f"{timeout_s} 秒內沒有出現新圖片")


def fetch_in_page(page, src: str) -> bytes:
    import base64

    data_url = page.evaluate(
        """async src => { const r = await fetch(src); const b = await r.blob();
        return await new Promise(res => { const f = new FileReader(); f.onload = () => res(f.result); f.readAsDataURL(b); }); }""",
        src)
    return base64.b64decode(data_url.split(",", 1)[1])
