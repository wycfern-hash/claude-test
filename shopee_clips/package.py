"""上架包：影片 + 標題文案 + 商品連結。手機手動上架、雲端上傳、自動上架失敗時的備援都用它。"""
import shutil
from pathlib import Path

from . import config


def package_dir(row) -> Path:
    return config.DATA_DIR / "ready" / str(row["id"])


def export_package(row) -> Path:
    d = package_dir(row)
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "video.mp4").exists():
        shutil.copy(config.DATA_DIR / row["video_path"], d / "video.mp4")
    from . import db

    if db.is_affiliate(row["source_url"]):
        link_line = f"分潤連結：{row['source_url']}"
    else:
        link_line = f"商品連結（一般連結，沒有分潤）：{row['url']}"
    (d / "文案.txt").write_text(f"{row['video_title']}\n\n{row['video_caption']}\n\n{link_line}\n", encoding="utf-8")
    return d
