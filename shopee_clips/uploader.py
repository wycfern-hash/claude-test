"""電腦版網頁自動上架。用『文字標籤』定位，標籤在 config/shopee_upload.json，改標籤不用改程式。
UPLOAD_MODE：
  manual  只匯出上架包（影片+標題+文案+商品連結），你用手機/電腦自己傳
  dryrun  自動填好全部欄位但不按發佈，視窗留給你檢查（第一次務必先跑這個）
  auto    填完直接發佈（受每日上限限制）
"""
import json
import re
from pathlib import Path

from . import config, db

DEBUG_DIR = config.DATA_DIR / "debug"


def load_labels() -> dict:
    return json.loads(config.SELECTORS_PATH.read_text(encoding="utf-8"))


def package_dir(row) -> Path:
    return config.DATA_DIR / "ready" / str(row["id"])


def export_package(row) -> Path:
    """上架包：影片、文案、商品連結，手機傳也行。"""
    import shutil

    d = package_dir(row)
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(config.DATA_DIR / row["video_path"], d / "video.mp4")
    (d / "文案.txt").write_text(f"{row['video_title']}\n\n{row['video_caption']}\n\n商品：{row['url']}\n", encoding="utf-8")
    return d


def _step(page, name: str, fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        shot = DEBUG_DIR / f"{name}.png"
        try:
            page.screenshot(path=str(shot))
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError(f"卡在「{name}」（截圖 {shot}）：{e}") from e


def upload_one(page, row, labels: dict, publish: bool) -> None:
    t = labels.get("step_timeout_sec", 60) * 1000
    page.goto(config.SHOPEE_VIDEO_UPLOAD_URL)
    page.wait_for_load_state("networkidle")
    video = str((config.DATA_DIR / row["video_path"]).resolve())
    _step(page, "選擇影片", lambda: page.set_input_files(labels["file_input"], video, timeout=t))
    text = f"{row['video_title']}\n{row['video_caption']}"

    def fill_caption():
        box = page.get_by_placeholder(re.compile(labels["caption_box"])).first
        box.fill(text, timeout=t)

    _step(page, "填文案", fill_caption)
    _step(page, "點新增商品", lambda: page.get_by_text(re.compile(labels["add_product_button"])).first.click(timeout=t))

    def bind():
        s = page.get_by_placeholder(re.compile(labels["product_search_box"])).first
        s.fill(row["title"][:40], timeout=t)
        s.press("Enter")
        page.wait_for_timeout(2500)
        page.get_by_text(re.compile(labels["product_first_result"])).first.click(timeout=t)

    _step(page, "綁定商品", bind)
    if not publish:
        return
    _step(page, "按發佈", lambda: page.get_by_role("button", name=re.compile(labels["publish_button"])).last.click(timeout=t))
    _step(page, "確認成功", lambda: page.get_by_text(re.compile(labels["success_text"])).first.wait_for(timeout=t))


def run(conn, mode: str | None = None) -> int:
    mode = mode or config.UPLOAD_MODE
    rows = db.by_status(conn, "video_approved")
    if not rows:
        return 0
    if mode == "manual":
        for r in rows:
            export_package(r)
        return 0
    if not config.SHOPEE_VIDEO_UPLOAD_URL:
        raise RuntimeError("UPLOAD_MODE 需要 SHOPEE_VIDEO_UPLOAD_URL")
    from playwright.sync_api import sync_playwright

    from .sourcing import _launch

    labels, n = load_labels(), 0
    with sync_playwright() as p:
        ctx = _launch(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for r in rows:
            if mode == "auto" and db.uploaded_today(conn) >= config.DAILY_UPLOAD_CAP:
                break
            try:
                upload_one(page, r, labels, publish=(mode == "auto"))
            except Exception as e:  # noqa: BLE001
                export_package(r)  # 失敗就留上架包給你手動傳
                db.move(conn, r["id"], "failed", error=f"upload: {e}")
                conn.commit()
                continue
            if mode == "dryrun":
                page.wait_for_event("close", timeout=0)  # 你檢查完把視窗關掉
                break
            db.move(conn, r["id"], "uploaded", uploaded_at=db.now(), error="")
            conn.commit()
            n += 1
        ctx.close()
    return n
