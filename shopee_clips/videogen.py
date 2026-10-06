"""產片。VIDEO_PROVIDER：
  slideshow（預設、免費）：用核准的 5 張圖 + ffmpeg 合成 15 秒（slideshow.py）
  veo（付費）：Veo API，Flow 網頁沒有官方 API
  flow：你用 Flow 的點數產（Flow 沒有官方 API，不能自動操作）。程式先備好提示詞與起始圖，
        你在「待產片」頁下載圖、複製提示詞、到 Flow 產 1~3 段，上傳 mp4，程式自動接起來裁成 15 秒並換上配音
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
            elif config.VIDEO_PROVIDER == "flow":
                db.update(conn, row["id"], script=json.dumps(script, ensure_ascii=False), error="")
                conn.commit()
                continue  # 等你在「待產片」頁上傳 Flow 影片
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


def finish_flow(conn, pid: int, clips: list[Path]) -> None:
    """把 Flow 下載的片段接成 15 秒、統一成 1080x1920、移除原音換成曉臻配音，進審片。"""
    row = db.get(conn, pid)
    script = get_script(row)
    d = config.VID_DIR / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    final, joined = d / "final.mp4", d / "joined.mp4"
    norm = []
    for i, c in enumerate(clips):
        n = d / f"norm{i}.mp4"
        subprocess.run(["ffmpeg", "-y", "-i", str(c), "-vf",
                        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p",
                        "-an", "-c:v", "libx264", str(n)], check=True, capture_output=True)
        norm.append(n)
    lst = d / "list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p in norm))
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-t", str(TARGET_SECONDS),
                    "-c", "copy", str(joined)], check=True, capture_output=True)
    voice = d / "voice.mp3"
    if slideshow.tts(script.get("voiceover", ""), voice):
        subprocess.run(["ffmpeg", "-y", "-i", str(joined), "-i", str(voice), "-filter_complex", "[1:a]apad[a]",
                        "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "copy", "-c:a", "aac", str(final)],
                       check=True, capture_output=True)
    else:
        shutil.copy(joined, final)
    db.move(conn, pid, "video_review", script=json.dumps(script, ensure_ascii=False),
            video_path=str(final.relative_to(config.DATA_DIR)),
            video_title=script["video_title"], video_caption=build_caption(script), error="")
