@echo off
title Choose Jarvis's speaker
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" choose_speaker.py
echo.
pause
