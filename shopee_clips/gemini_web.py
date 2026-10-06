"""操控你的 Chrome，用 Gemini 網頁（你的訂閱）產圖。不用 API key。
每張圖開一個新對話：附上賣家參考圖 + 提示詞 → 等新圖出現 → 下載。
標籤在 config/browser_sites.json；按鈕猜錯時看 data/debug 的診斷檔來修。
"""
import re
import time
from pathlib import Path

from . import config, db
from .imagegen import prompts_for, ref_files
from .webauto import fetch_in_page, img_srcs, rx, site, step, wait_new_image


def _attach(page, L, files: list[str], t: int) -> None:
    def go():
        inp = page.locator("input[type=file]")
        if inp.count():
            inp.first.set_input_files(files)
        else:
            page.get_by_role("button", name=rx(L["upload_menu_button"])).first.click(timeout=t)
            with page.expect_file_chooser(timeout=t) as fc:
                page.get_by_text(rx(L["upload_files_item"])).first.click(timeout=t)
            fc.value.set_files(files)
        page.wait_for_timeout(4000)  # 等縮圖上傳完

    step(page, "gemini_附上參考圖", go)


def _download(page, L, src: str, out: Path, t: int) -> None:
    page.evaluate("src => { for (const i of document.images) if (i.src === src) i.setAttribute('data-sc', '1'); }", src)
    img = page.locator('img[data-sc="1"]').first
    try:  # 1) 下載按鈕（通常是原尺寸）
        img.hover()
        with page.expect_download(timeout=t) as dl:
            page.get_by_role("button", name=rx(L["download_button"])).first.click(timeout=t)
        dl.value.save_as(str(out))
        return
    except Exception:  # noqa: BLE001
        pass
    try:  # 2) 頁面內直接抓圖檔
        out.write_bytes(fetch_in_page(page, src))
        return
    except Exception:  # noqa: BLE001
        pass
    img.screenshot(path=str(out))  # 3) 最後手段：截圖該元素（解析度較低）


def one_image(ctx, L, prompt: str, refs: list[Path], out: Path) -> None:
    t = L["step_timeout_sec"] * 1000
    page = ctx.new_page()
    try:
        page.goto(L["url"])
        page.wait_for_load_state("domcontentloaded")
        if "accounts.google.com" in page.url:
            raise RuntimeError("尚未登入 Google：請先在自動化專用 Chrome 登入（首頁按「開啟自動化 Chrome」）")
        _attach(page, L, [str(p) for p in refs], t)

        def type_prompt():
            box = page.get_by_role("textbox").first
            box.click(timeout=t)
            box.fill(L["prompt_prefix"] + prompt)

        step(page, "gemini_填提示詞", type_prompt)
        before = img_srcs(page, 1)  # 含參考圖縮圖，之後只認「新出現」的圖
        step(page, "gemini_送出", lambda: page.get_by_role("button", name=rx(L["send_button"])).last.click(timeout=t))
        src = step(page, "gemini_等待產圖", lambda: wait_new_image(page, before, L["image_min_px"], L["generate_timeout_sec"]))
        step(page, "gemini_下載", lambda: _download(page, L, src, out, t))
    finally:
        page.close()


def generate_images(ctx, row) -> list[str]:
    L = site("gemini")
    refs = ref_files(row["id"])[:3]
    if not refs:
        raise RuntimeError("沒有參考圖（先讓程式補商品資料）")
    d = config.IMG_DIR / str(row["id"])
    d.mkdir(parents=True, exist_ok=True)
    saved = []
    for n, prompt in enumerate(prompts_for(row)):
        out = d / f"{n}.png"
        one_image(ctx, L, prompt, refs, out)
        saved.append(str(out.relative_to(config.DATA_DIR)))
        time.sleep(L["gap_seconds"])
    return saved


def run(conn, cap_left: int) -> int:
    from . import browser

    rows = [r for r in db.by_status(conn, "sourced") if not r["error"].startswith("imagegen:")][:cap_left]
    if not rows:
        return 0
    n = 0
    with browser.open_context() as ctx:
        for row in rows:
            try:
                imgs = generate_images(ctx, row)
            except Exception as e:  # noqa: BLE001
                db.update(conn, row["id"], error=f"imagegen: {str(e)[:300]}")
                conn.commit()
                continue
            import json

            db.move(conn, row["id"], "image_review", images=json.dumps(imgs), error="")
            conn.commit()
            n += 1
    return n
