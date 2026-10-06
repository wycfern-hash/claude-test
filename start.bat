@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist .env copy .env.example .env >nul
if not exist .venv (
  echo 第一次執行：安裝套件中（約 1-2 分鐘）...
  python -m venv .venv || (echo 找不到 Python，請先安裝 Python 3.11+ 並勾選 Add to PATH & pause & exit /b 1)
  .venv\Scripts\pip install -r requirements.txt
)
where ffmpeg >nul 2>nul || echo [警告] 找不到 ffmpeg，影片合成會失敗。請在命令列執行: winget install ffmpeg
.venv\Scripts\python -m shopee_clips
pause
