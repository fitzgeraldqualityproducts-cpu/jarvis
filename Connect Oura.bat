@echo off
title Connect Oura
cd /d "%~dp0"
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_oura.py
echo.
pause
