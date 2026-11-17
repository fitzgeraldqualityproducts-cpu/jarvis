@echo off
title Choose Jarvis's speaker
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  echo Already installed? Use the Jarvis icon on your Desktop, or press Windows+R, type %%USERPROFILE%%\Jarvis and press Enter.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" choose_speaker.py
echo.
pause
