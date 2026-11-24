@echo off
title Connect Stock Footage
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
if not exist "studio\footage.py" (
  echo Jarvis hasn't got the stock footage update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
echo Saves a free Pixabay key so some scenes in the Shorts can use real stock footage. NASA footage needs no key.
".venv\Scripts\python.exe" -m studio footage connect
echo.
pause
