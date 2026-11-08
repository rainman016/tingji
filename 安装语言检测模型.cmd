@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%CD%\.tmp"
set "TMP=%CD%\.tmp"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
if not exist ".venv\Scripts\python.exe" (
  echo Please run setup.cmd first.
  pause
  exit /b 1
)
call ensure_env.cmd
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" install_language.py
if errorlevel 1 (
  echo Installation failed. Run this installer again to resume.
) else (
  echo Language detector ready. Restart the app to use automatic language detection.
)
pause
