@echo off
title Start YouTube Autopilot
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
echo Checking the business channel sign-in first...
".venv\Scripts\python.exe" -m studio verify
if errorlevel 1 (
  echo The channel check failed, so the autopilot stays off. Double-click "Connect YouTube Business" first.
  pause
  exit /b 1
)
echo Switching on the Chatterbox voice (Connor approved it) if it works on this PC...
".venv\Scripts\python.exe" -m studio engines use on voice --if-ready
".venv\Scripts\python.exe" -m studio autopilot on
echo Jarvis makes Shorts while he is running (start him from the Desktop shortcut).
echo.
pause
