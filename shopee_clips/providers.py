"""AI 供應商（你自己填 API key）。三個角色各自選供應商：
  腳本 text : gemini | openai(含相容 OpenAI 的服務) | claude
  圖片 image: gemini | openai
  影片 video: veo(Gemini API) | fal(fal.ai 上的 Kling/Wan 等圖生影片模型)
每個函式只做『呼叫一次、拿回結果』，重試/上限/狀態由上層處理。
"""
import base64
import json
import re
import time
from pathlib import Path

import httpx

from . import config


class NotConfigured(RuntimeError):
    pass


def configured(role: str) -> bool:
    """該角色目前選的供應商，金鑰有沒有填。"""
    need = {
        ("text", "gemini"): "GEMINI_API_KEY", ("text", "openai"): "OPENAI_API_KEY", ("text", "claude"): "ANTHROPIC_API_KEY",
        ("image", "gemini"): "GEMINI_API_KEY", ("image", "openai"): "OPENAI_API_KEY",
        ("video", "veo"): "GEMINI_API_KEY", ("video", "fal"): "FAL_KEY",
    }.get((role, getattr(config, f"{role.upper()}_PROVIDER")))
    return bool(need and getattr(config, need))


def _need(key_name: str) -> str:
    v = getattr(config, key_name)
    if not v:
        raise NotConfigured(f"尚未設定 {key_name}（到網頁「設定」頁填寫）")
    return v


def _gemini():
    from google import genai

    return genai.Client(api_key=_need("GEMINI_API_KEY"))


def _openai():
    from openai import OpenAI

    return OpenAI(api_key=_need("OPENAI_API_KEY"), base_url=config.OPENAI_BASE_URL or None)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def parse_json(text: str) -> dict:
    """容忍模型在 JSON 前後多寫字或包 ``` 。"""
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        raise RuntimeError(f"模型沒有回傳 JSON：{text[:120]}")
    return json.loads(text[a : b + 1])


# ---------------------------------------------------------------- 腳本
def text_json(prompt: str, images: list[bytes] | None = None) -> dict:
    images = images or []
    p = config.TEXT_PROVIDER
    model = config.model_for("text")
    if p == "gemini":
        from google.genai import types

        parts = [types.Part.from_bytes(data=i, mime_type="image/jpeg") for i in images]
        r = _gemini().models.generate_content(
            model=model, contents=[*parts, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"))
        return parse_json(r.text)
    if p == "openai":
        content = [{"type": "text", "text": prompt}] + [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{_b64(i)}"}} for i in images]
        r = _openai().chat.completions.create(
            model=model, messages=[{"role": "user", "content": content}], response_format={"type": "json_object"})
        return parse_json(r.choices[0].message.content)
    if p == "claude":
        import anthropic

        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": _b64(i)}}
                   for i in images] + [{"type": "text", "text": prompt}]
        r = anthropic.Anthropic(api_key=_need("ANTHROPIC_API_KEY")).messages.create(
            model=model, max_tokens=2000, messages=[{"role": "user", "content": content}])
        return parse_json(r.content[0].text)
    raise NotConfigured(f"TEXT_PROVIDER={p} 不是 AI 供應商")


# ---------------------------------------------------------------- 圖片
def image_bytes(prompt: str, refs: list[Path]) -> bytes:
    p = config.IMAGE_PROVIDER
    model = config.model_for("image")
    if p == "gemini":
        from google.genai import types

        parts = [types.Part.from_bytes(data=r.read_bytes(), mime_type="image/jpeg") for r in refs]
        resp = _gemini().models.generate_content(model=model, contents=[prompt, *parts])
        for part in resp.candidates[0].content.parts:
            if getattr(part, "inline_data", None) and part.inline_data.data:
                return part.inline_data.data
        raise RuntimeError("Gemini 沒有回傳圖片（可能被安全過濾）")
    if p == "openai":
        files = [open(r, "rb") for r in refs]
        try:
            r = _openai().images.edit(model=model, image=files, prompt=prompt, size="1024x1536")
        finally:
            for f in files:
                f.close()
        return base64.b64decode(r.data[0].b64_json)
    raise NotConfigured(f"IMAGE_PROVIDER={p} 不是 API 供應商")


# ---------------------------------------------------------------- 影片
def video_clip(prompt: str, image_path: Path, out: Path) -> None:
    p = config.VIDEO_PROVIDER
    model = config.model_for("video")
    if p == "veo":
        from google.genai import types

        c = _gemini()
        op = c.models.generate_videos(
            model=model, prompt=prompt,
            image=types.Image(image_bytes=image_path.read_bytes(), mime_type="image/png"),
            config=types.GenerateVideosConfig(aspect_ratio="9:16", duration_seconds=config.clip_seconds()))
        while not op.done:
            time.sleep(10)
            op = c.operations.get(op)
        if not op.response or not op.response.generated_videos:
            raise RuntimeError("Veo 沒有回傳影片（可能被安全過濾）")
        vid = op.response.generated_videos[0]
        c.files.download(file=vid.video)
        vid.video.save(str(out))
        return
    if p == "fal":
        import os

        import fal_client

        os.environ["FAL_KEY"] = _need("FAL_KEY")
        args = {"prompt": prompt, "image_url": fal_client.upload_file(str(image_path)), "aspect_ratio": "9:16"}
        if config.FAL_EXTRA_ARGS:
            args |= json.loads(config.FAL_EXTRA_ARGS)
        res = fal_client.subscribe(model, arguments=args)
        url = res["video"]["url"]
        r = httpx.get(url, timeout=300, follow_redirects=True)
        r.raise_for_status()
        out.write_bytes(r.content)
        return
    raise NotConfigured(f"VIDEO_PROVIDER={p} 不是 API 供應商")


# ---------------------------------------------------------------- 連線測試
def check(role: str) -> str:
    """設定頁的『測試』按鈕。文字：極小請求；圖片：實際產 1 張（會計費）；影片：只檢查金鑰有填（避免誤燒錢）。"""
    try:
        if role == "text":
            if config.TEXT_PROVIDER == "template":
                return "✅ 腳本：不使用 AI（範本 + 你填的賣點）"
            r = text_json('回傳 JSON：{"ok": true}')
            return f"✅ 腳本 {config.TEXT_PROVIDER}/{config.model_for('text')} 可用（{r}）"
        if role == "image":
            if config.IMAGE_PROVIDER in ("manual", "browser"):
                return f"✅ 圖片：{config.IMAGE_PROVIDER} 模式不需 API"
            from PIL import Image
            import io

            buf = io.BytesIO()
            Image.new("RGB", (256, 256), "white").save(buf, "JPEG")
            tmp = config.DATA_DIR / "_check.jpg"
            tmp.write_bytes(buf.getvalue())
            data = image_bytes("a plain red apple, studio photo, no text", [tmp])
            return f"✅ 圖片 {config.IMAGE_PROVIDER}/{config.model_for('image')} 可用（收到 {len(data)//1024} KB）"
        if role == "video":
            if config.VIDEO_PROVIDER == "veo":
                _need("GEMINI_API_KEY")
            elif config.VIDEO_PROVIDER == "fal":
                _need("FAL_KEY")
            return f"✅ 影片 {config.VIDEO_PROVIDER}：金鑰已填（為避免花錢，不實際產片測試）"
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"\s+", " ", str(e))[:250]
        return f"❌ {role} 失敗：{msg}"
    return "?"
