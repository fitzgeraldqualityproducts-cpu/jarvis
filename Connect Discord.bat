@echo off
title Connect Discord
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
if not exist "setup_discord.py" (
  echo This Jarvis is out of date. Double-click "Install Jarvis" in the new Jarvis-Windows folder first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_discord.py
echo.
pause
