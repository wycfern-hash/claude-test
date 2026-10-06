@echo off
setlocal
cd /d "%~dp0"
if not exist .env if exist .env.example copy .env.example .env >nul

set PY=python
where py >nul 2>nul && set PY=py -3

if not exist .venv\Scripts\python.exe (
  echo First run: installing packages, this takes about 1-2 minutes...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo.
    echo ERROR: could not create the Python environment.
    echo Install Python 3.11 or newer from python.org and tick "Add python.exe to PATH", then run this file again.
    pause
    exit /b 1
  )
  .venv\Scripts\python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo ERROR: package install failed. Read the messages above.
    pause
    exit /b 1
  )
)

where ffmpeg >nul 2>nul || echo [WARNING] ffmpeg not found - video making will fail. Run in a command prompt: winget install ffmpeg

echo.
echo Starting. Open http://localhost:8000 in your browser. Keep this window open.
.venv\Scripts\python -m shopee_clips
pause
