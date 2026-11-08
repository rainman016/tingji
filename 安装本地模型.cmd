@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0"
if not exist ".tmp" mkdir ".tmp"
set "TEMP=%CD%\.tmp"
set "TMP=%CD%\.tmp"
set "PIP_CACHE_DIR=%CD%\.cache\pip"
call ensure_env.cmd
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failure
".venv\Scripts\python.exe" -m pip --isolated install --cache-dir "%CD%\.cache\pip" -r requirements.txt
if errorlevel 1 goto failure
".venv\Scripts\python.exe" bootstrap_local.py
if errorlevel 1 goto failure
".venv\Scripts\python.exe" install_english.py
if errorlevel 1 goto failure
".venv\Scripts\python.exe" install_language.py --models-only
if errorlevel 1 goto failure
echo Local setup complete. Open the launcher to start.
pause
exit /b 0
:failure
echo Installation incomplete. Run this installer again to retry.
pause
exit /b 1
