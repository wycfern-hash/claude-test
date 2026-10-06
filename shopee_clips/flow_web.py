"""操控你的 Chrome，在 Flow 用你的點數產影片（Frames to Video：起始圖 + 提示詞）。實驗性、未驗證。
每個商品產 FLOW_CLIPS_PER_PRODUCT 段（預設 2 段，各約 8 秒），產完交給 videogen.finish_flow 接成 15 秒並配音。
注意：每段都會消耗你的 Flow 點數；DAILY_GEN_CAP 限制每天處理幾個商品。
"""
import json
import time
from pathlib import Path

from . import config, db
from .scriptgen import video_prompts
from .webauto import fetch_in_page, rx, site, step


def _new_videos(page) -> set[str]:
    return set(page.evaluate("() => [...document.querySelectorAll('video')].map(v => v.currentSrc || v.src).filter(Boolean)"))


def one_clip(ctx, L, start_img: Path, prompt: str, out: Path) -> None:
    t = L["step_timeout_sec"] * 1000
    page = ctx.new_page()
    try:
        page.goto(L["url"])
        page.wait_for_load_state("domcontentloaded")
        if "accounts.google.com" in page.url:
            raise RuntimeError("尚未登入 Google：請先在自動化專用 Chrome 登入")
        step(page, "flow_新專案", lambda: page.get_by_text(rx(L["new_project_button"])).first.click(timeout=t), optional=True)
        page.wait_for_timeout(3000)
        step(page, "flow_切換影格轉影片", lambda: page.get_by_text(rx(L["mode_frames_to_video"])).first.click(timeout=t), optional=True)

        def upload():
            inp = page.locator("input[type=file]")
            if inp.count():
                inp.first.set_input_files(str(start_img))
            else:
                page.get_by_role("button", name=rx(L["upload_menu_button"])).first.click(timeout=t)
                with page.expect_file_chooser(timeout=t) as fc:
                    page.get_by_text(rx(L["upload_files_item"])).first.click(timeout=t)
                fc.value.set_files(str(start_img))
            page.wait_for_timeout(4000)

        step(page, "flow_上傳起始圖", upload)
        step(page, "flow_選9比16", lambda: page.get_by_text(rx(L["aspect_portrait"])).first.click(timeout=5000), optional=True)

        def type_prompt():
            box = page.get_by_role("textbox").first
            box.click(timeout=t)
            box.fill(prompt)

        step(page, "flow_填提示詞", type_prompt)
        before = _new_videos(page)
        step(page, "flow_產生", lambda: page.get_by_role("button", name=rx(L["generate_button"])).last.click(timeout=t))

        def wait_done():
            end = time.time() + L["generate_timeout_sec"]
            while time.time() < end:
                new = _new_videos(page) - before
                if new:
                    return sorted(new)[-1]
                time.sleep(5)
            raise TimeoutError("等不到影片（可能點數不足或產生失敗，看診斷截圖）")

        src = step(page, "flow_等待影片", wait_done)

        def download():
            try:
                page.locator("video").last.hover()
                with page.expect_download(timeout=t * 2) as dl:
                    page.get_by_role("button", name=rx(L["download_button"])).first.click(timeout=t)
                    page.get_by_text(rx(L["download_quality_item"])).first.click(timeout=5000)
                dl.value.save_as(str(out))
            except Exception:  # noqa: BLE001
                out.write_bytes(fetch_in_page(page, src))

        step(page, "flow_下載", download)
    finally:
        page.close()


def generate_clips(ctx, row, script: dict) -> list[Path]:
    L = site("flow")
    imgs = [config.DATA_DIR / p for p in json.loads(row["selected_images"])] or [config.DATA_DIR / row["selected_image"]]
    n = max(1, min(config.FLOW_CLIPS_PER_PRODUCT, 3))
    starts = [imgs[min(i * len(imgs) // n, len(imgs) - 1)] for i in range(n)]  # 分散取圖，兩段不會從同一張開始
    prompts = video_prompts(script, n)
    d = config.VID_DIR / str(row["id"]) / "flow"
    d.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (img, prompt) in enumerate(zip(starts, prompts)):
        out = d / f"clip{i}.mp4"
        one_clip(ctx, L, img, prompt, out)
        clips.append(out)
        time.sleep(L["gap_seconds"])
    return clips


def run(conn, cap_left: int) -> int:
    from . import browser
    from .scriptgen import get_script
    from .videogen import finish_flow

    rows = [r for r in db.by_status(conn, "image_approved") if not r["error"].startswith(("videogen:", "flow:"))][:cap_left]
    if not rows:
        return 0
    n = 0
    with browser.open_context() as ctx:
        for row in rows:
            try:
                script = get_script(row)
                db.update(conn, row["id"], script=json.dumps(script, ensure_ascii=False))
                clips = generate_clips(ctx, row, script)
                finish_flow(conn, row["id"], clips)
            except Exception as e:  # noqa: BLE001
                db.update(conn, row["id"], error=f"videogen: {str(e)[:300]}")
                conn.commit()
                continue
            conn.commit()
            n += 1
    return n
