@echo off
title Install Studio Tools
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
echo Installing the YouTube studio tools: Chatterbox voice and whisperX timing (on the NVIDIA card),
echo Manim animation and Remotion captions. Free, one time, about 10 GB. This can take 20-40 minutes.
".venv\Scripts\python.exe" -m studio engines install all
echo.
pause
