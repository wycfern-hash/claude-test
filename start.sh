#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
[ -f .env ] || cp .env.example .env
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
command -v ffmpeg >/dev/null || echo "[警告] 找不到 ffmpeg，影片合成會失敗，請先安裝"
exec .venv/bin/python -m shopee_clips
