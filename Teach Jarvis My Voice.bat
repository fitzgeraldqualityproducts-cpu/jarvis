@echo off
title Teach Jarvis My Voice
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  echo Already installed? Use the Jarvis icon on your Desktop, or press Windows+R, type %%USERPROFILE%%\Jarvis and press Enter.
  pause
  exit /b 1
)
if not exist "setup_voiceprint.py" (
  echo Jarvis hasn't got his voice update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_voiceprint.py
echo.
pause
