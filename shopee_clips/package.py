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
    link = row["source_url"] or row["url"]
    (d / "文案.txt").write_text(f"{row['video_title']}\n\n{row['video_caption']}\n\n商品：{link}\n", encoding="utf-8")
    return d
