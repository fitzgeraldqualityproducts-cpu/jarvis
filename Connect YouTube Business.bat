@echo off
title Connect YouTube Business
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
if not exist "studio\handover.py" (
  echo Jarvis hasn't got the PC studio update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
echo Signs in to the NEW business channel. Your existing channel stays protected.
".venv\Scripts\python.exe" -m studio connect
echo.
pause
