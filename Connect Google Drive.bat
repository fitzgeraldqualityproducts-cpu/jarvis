@echo off
title Connect Google Drive
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
if not exist "studio\drive.py" (
  echo Jarvis hasn't got the Google Drive update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
echo Connects your Google Drive so finished Shorts are backed up there.
echo Jarvis can only see the files it puts there itself, nothing else in your Drive.
".venv\Scripts\python.exe" -m studio drive connect %*
echo.
pause
