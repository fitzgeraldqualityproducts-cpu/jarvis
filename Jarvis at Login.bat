@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
rem Only start at sign-in if it was turned on ("start_at_login": true in config.json).
findstr /C:"\"start_at_login\": true" config.json >nul 2>&1 || exit /b 0
set PYTHONUTF8=1
set "PATH=%USERPROFILE%\.local\bin;%APPDATA%\npm;%PATH%"
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  echo Already installed? Use the Jarvis icon on your Desktop, or press Windows+R, type %%USERPROFILE%%\Jarvis and press Enter.
  pause
  exit /b 1
)
:run
".venv\Scripts\python.exe" jarvis.py --no-display
if "%errorlevel%"=="3" goto run
