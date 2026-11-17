@echo off
cd /d "%~dp0"
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
:run
".venv\Scripts\python.exe" jarvis.py --no-display
if "%errorlevel%"=="3" goto run
