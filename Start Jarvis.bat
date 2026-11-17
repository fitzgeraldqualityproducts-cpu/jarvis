@echo off
title Jarvis
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
set "ARGS=%*"
:run
rem jarvis.py keeps himself running: he restarts after a crash, an update or a self-edit.
".venv\Scripts\python.exe" jarvis.py %ARGS%
if "%errorlevel%"=="3" (
  set "ARGS=--no-display"
  goto run
)
if "%errorlevel%"=="4" (
  timeout /t 6 >nul
  exit /b 0
)
if "%errorlevel%"=="0" exit /b 0
echo.
echo Jarvis has stopped. If something went wrong, send the jarvis.log file in this folder to Claude.
pause
