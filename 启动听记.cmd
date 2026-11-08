@echo off
setlocal
cd /d "%~dp0"
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%CD%\.tmp"
set "TMP=%CD%\.tmp"
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import requests, requests_toolbelt, imageio_ffmpeg, tkinter, customtkinter" >nul 2>&1
if errorlevel 1 (
  echo Dependencies missing. Please run setup.cmd first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "app.py"
