"""上架。蝦皮短影音只有手機版，所以沒有網頁自動上架。
UPLOAD_MODE：
  manual        只匯出上架包（影片+標題文案+商品連結）；「上架包」頁可下載/掃 QR/複製，你在手機自己傳
  phone_dryrun  Android 手機自動操作蝦皮 App，做到「按發佈」前停下讓你檢查（第一次務必先跑這個）
  phone_auto    手機自動操作並發佈（受每日上限限制）
"""
from . import config, db, package, phone


def run(conn, mode: str | None = None, connect_fn=None) -> int:
    mode = mode or config.UPLOAD_MODE
    if mode in ("phone_dryrun", "phone_auto"):
        return phone.run(conn, mode, connect_fn)
    for r in db.by_status(conn, "video_approved"):
        if r["video_path"] and not (package.package_dir(r) / "video.mp4").exists():
            package.export_package(r)
    return 0
