@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto validate
set "PROJECT_PYTHON="
for /d %%P in (".runtime\cpython-3.12*") do if exist "%%~fP\python.exe" set "PROJECT_PYTHON=%%~fP\python.exe"
if defined PROJECT_PYTHON (
  "%PROJECT_PYTHON%" -m venv .venv
) else (
  py -3.12 -m venv .venv
)
if errorlevel 1 exit /b 1
:validate
".venv\Scripts\python.exe" -c "import sys,struct,tkinter; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Python 3.12 x64 required'"
if errorlevel 1 (
  echo Python 3.12 x64 with tkinter is required. Existing environments were not replaced.
  exit /b 1
)
exit /b 0
