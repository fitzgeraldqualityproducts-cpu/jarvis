@echo off
title Take Over YouTube Studio
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" if exist "%USERPROFILE%\Jarvis\.venv\Scripts\python.exe" cd /d "%USERPROFILE%\Jarvis"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Jarvis isn't installed on this PC yet. Double-click "Install Jarvis" first.
  pause
  exit /b 1
)
if not exist "studio\handover.py" (
  echo Jarvis hasn't got the PC studio update yet. Say "Hey Jarvis, check for updates", wait a minute, then try again.
  pause
  exit /b 1
)
echo Moves the YouTube business from the Mac to this PC, from the "Jarvis Studio Handover.zip" file.
echo Do this only after running "Hand YouTube Studio to PC" on the Mac.
".venv\Scripts\python.exe" -m studio handover import %*
echo.
pause
