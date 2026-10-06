"""產片。VIDEO_PROVIDER：
  slideshow（預設、免費）：用核准的 5 張圖 + ffmpeg 合成 15 秒（slideshow.py）
  veo（付費）：Veo API，Flow 網頁沒有官方 API
  manual：匯出提示詞，自己在 Flow 產
Veo 單支最長約 8 秒 → 15 秒 = 兩段（開箱+賣點1 / 賣點2+3+CTA）用 ffmpeg 接起來再裁成 15 秒。
想繼續用 Flow 手動產：VIDEO_PROVIDER=manual，執行 export-prompts，產完把 mp4 命名 <id>.mp4 放進 data/videos/manual/，再 import-manual。
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import config, db
from .gemini_client import client
from . import slideshow
from .scriptgen import get_script

TARGET_SECONDS = 15


def _veo_clip(prompt: str, image_path: Path, out: Path) -> None:
    from google.genai import types

    c = client()
    op = c.models.generate_videos(
        model=config.VIDEO_MODEL,
        prompt=prompt,
        image=types.Image(image_bytes=image_path.read_bytes(), mime_type="image/png"),
        config=types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=8),
    )
    while not op.done:
        time.sleep(10)
        op = c.operations.get(op)
    if not op.response or not op.response.generated_videos:
        raise RuntimeError("Veo 沒有回傳影片（可能被安全過濾）")
    vid = op.response.generated_videos[0]
    c.files.download(file=vid.video)
    vid.video.save(str(out))


def concat_trim(clips: list[Path], out: Path, seconds: int = TARGET_SECONDS) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("需要安裝 ffmpeg")
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in clips))
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-t", str(seconds),
         "-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", str(out)],
        check=True, capture_output=True,
    )
    lst.unlink()


def build_caption(s: dict) -> str:
    tags = " ".join(t if t.startswith("#") else f"#{t}" for t in s.get("hashtags", []))
    return f"{s['caption']}\n{tags}".strip()


def run(conn) -> int:
    n = 0
    for row in db.by_status(conn, "image_approved"):
        if config.VIDEO_PROVIDER == "veo" and db.generated_today(conn) >= config.DAILY_GEN_CAP:
            break
        try:
            script = get_script(row)
            d = config.VID_DIR / str(row["id"])
            d.mkdir(parents=True, exist_ok=True)
            final = d / "final.mp4"
            if config.VIDEO_PROVIDER == "slideshow":
                imgs = [config.DATA_DIR / p for p in json.loads(row["selected_images"])] or [config.DATA_DIR / row["selected_image"]]
                slideshow.build(imgs, script, final)
            elif config.VIDEO_PROVIDER == "manual":
                db.update(conn, row["id"], script=json.dumps(script, ensure_ascii=False))
                conn.commit()
                continue
            else:
                img = config.DATA_DIR / row["selected_image"]
                clips = []
                for i, key in enumerate(("video_prompt_1", "video_prompt_2"), 1):
                    p = d / f"clip{i}.mp4"
                    _veo_clip(script[key], img, p)
                    clips.append(p)
                concat_trim(clips, final)
        except Exception as e:  # noqa: BLE001
            db.update(conn, row["id"], error=f"videogen: {e}")
            conn.commit()
            continue
        db.move(
            conn, row["id"], "video_review",
            script=json.dumps(script, ensure_ascii=False),
            video_path=str(final.relative_to(config.DATA_DIR)),
            video_title=script["video_title"], video_caption=build_caption(script), error="",
        )
        conn.commit()
        n += 1
    return n


def export_prompts(conn, path: str = "prompts_for_flow.md") -> int:
    rows = db.by_status(conn, "image_approved")
    lines = []
    for r in rows:
        s = json.loads(r["script"]) or {}
        lines += [f"## #{r['id']} {r['title']}", f"起始圖：{config.DATA_DIR / r['selected_image']}",
                  f"片段1：{s.get('video_prompt_1','')}", f"片段2：{s.get('video_prompt_2','')}", ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return len(rows)


def import_manual(conn) -> int:
    n = 0
    for r in db.by_status(conn, "image_approved"):
        src = config.VID_DIR / "manual" / f"{r['id']}.mp4"
        s = json.loads(r["script"]) or {}
        if src.exists() and s:
            db.move(conn, r["id"], "video_review", video_path=str(src.relative_to(config.DATA_DIR)),
                    video_title=s["video_title"], video_caption=build_caption(s))
            n += 1
    return n
