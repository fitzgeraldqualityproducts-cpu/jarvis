@echo off
title Install Local Brain
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
if not exist "local_brain.py" (
  echo Jarvis hasn't got his local brain update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
echo Installing Jarvis's local brain (Ollama + a small model + Chroma). Free, one time, about 3 GB.
".venv\Scripts\python.exe" local_brain.py install
echo.
pause
