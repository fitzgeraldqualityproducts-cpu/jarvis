@echo off
title Fix Jarvis's brain
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" -BrainOnly
