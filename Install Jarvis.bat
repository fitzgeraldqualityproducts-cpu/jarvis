@echo off
title Installing Jarvis
cd /d "%~dp0"
if not exist "%~dp0install_windows.ps1" (
  echo Please unzip the Jarvis folder first: right-click the zip file, choose "Extract All", then
  echo open the new folder and double-click "Install Jarvis" in there.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
