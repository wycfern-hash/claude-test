"""零成本影片：5 張圖 × 3 秒 = 15 秒，ffmpeg 緩慢推近 + 字幕，可選免費 TTS 配音。
5 張圖對應 5 個節拍：開箱 hook / 賣點1 / 賣點2 / 賣點3 / CTA。
"""
import asyncio
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import config

W, H, FPS, SECONDS_EACH, TOTAL = 1080, 1920, 30, 3, 15
FONT_CANDIDATES = [
    "C:/Windows/Fonts/msjh.ttc", "C:/Windows/Fonts/msjhbd.ttc",
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def find_font() -> str:
    for p in ([config.FONT_PATH] if config.FONT_PATH else []) + FONT_CANDIDATES:
        if p and Path(p).exists():
            return p
    raise RuntimeError("找不到中文字型，請在 .env 設定 FONT_PATH 指向一個含中文的 .ttf/.ttc")


def beats(script: dict) -> list[str]:
    """5 句字幕。圖不足 5 張時，由 build() 循環使用圖片。"""
    pts = (script.get("selling_points") or [])[:3]
    pts += [""] * (3 - len(pts))
    return [script.get("hook", ""), *pts, script.get("cta", "")]


def _cover(img: Image.Image) -> Image.Image:
    img = img.convert("RGB")
    s = max(W / img.width, H / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    x, y = (img.width - W) // 2, (img.height - H) // 2
    return img.crop((x, y, x + W, y + H))


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + ([cur] if cur else [])


def caption_overlay(text: str, out: Path) -> None:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if text:
        d = ImageDraw.Draw(layer)
        font = ImageFont.truetype(find_font(), 78)
        lines = _wrap(d, text, font, W - 160)
        lh = 100
        top = int(H * 0.70)
        d.rounded_rectangle((60, top - 30, W - 60, top + lh * len(lines) + 20), 36, fill=(0, 0, 0, 150))
        for i, ln in enumerate(lines):
            w = d.textlength(ln, font=font)
            d.text(((W - w) / 2, top + i * lh), ln, font=font, fill=(255, 255, 255, 255))
    layer.save(out)


def tts(text: str, out: Path) -> bool:
    """免費 edge-tts（非官方、免金鑰）。失敗就回 False，影片照樣無配音輸出。"""
    if not (config.TTS and text):
        return False
    try:
        import edge_tts

        rate = max(0, min(40, int((len(text) / 65 - 1) * 100)))
        comm = edge_tts.Communicate(text, config.TTS_VOICE, rate=f"+{rate}%")
        asyncio.run(comm.save(str(out)))
        return out.exists()
    except Exception:  # noqa: BLE001
        return False


def build(image_paths: list[Path], script: dict, out: Path) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("需要安裝 ffmpeg")
    if not image_paths:
        raise RuntimeError("沒有圖片")
    work = out.parent / "work"
    work.mkdir(parents=True, exist_ok=True)
    segs = []
    for i, text in enumerate(beats(script)):
        bg, ov, seg = work / f"bg{i}.png", work / f"ov{i}.png", work / f"seg{i}.mp4"
        _cover(Image.open(image_paths[i % len(image_paths)])).save(bg)
        caption_overlay(text if config.SUBTITLES else "", ov)
        frames = SECONDS_EACH * FPS
        zoom = f"zoompan=z='min(zoom+0.0007,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}"
        subprocess.run(
            ["ffmpeg", "-y", "-loop", "1", "-i", str(bg), "-i", str(ov), "-filter_complex",
             f"[0:v]{zoom}[z];[z][1:v]overlay=0:0,format=yuv420p", "-t", str(SECONDS_EACH),
             "-r", str(FPS), "-c:v", "libx264", "-an", str(seg)],
            check=True, capture_output=True,
        )
        segs.append(seg)
    lst = work / "list.txt"
    lst.write_text("".join(f"file '{s.resolve()}'\n" for s in segs))
    silent = work / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(silent)],
                   check=True, capture_output=True)
    voice = work / "voice.mp3"
    if tts(script.get("voiceover", ""), voice):
        subprocess.run(["ffmpeg", "-y", "-i", str(silent), "-i", str(voice), "-filter_complex", "[1:a]apad[a]",
                        "-map", "0:v", "-map", "[a]", "-t", str(TOTAL), "-c:v", "copy", "-c:a", "aac", str(out)],
                       check=True, capture_output=True)
    else:
        shutil.copy(silent, out)
    return out
