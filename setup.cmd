@echo off
setlocal
cd /d "%~dp0"
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%CD%\.tmp"
set "TMP=%CD%\.tmp"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
call ensure_env.cmd
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m pip --isolated install --cache-dir "%CD%\.cache\pip" -r requirements.txt
if errorlevel 1 goto failure
echo Setup complete. Open the launcher to start.
pause
exit /b 0
:failure
echo Setup failed. Python 3.12 x64 with tkinter and network access are required.
pause
exit /b 1
