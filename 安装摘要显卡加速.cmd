@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 请先完成听记本地环境安装。
    pause
    exit /b 1
)
if not exist ".tmp" mkdir ".tmp"
set "PYTHONUTF8=1"
set "TEMP=%~dp0.tmp"
set "TMP=%~dp0.tmp"
echo 将下载约42MB官方显卡摘要引擎，在项目独立目录安装并验证GPU卸载。
echo 原CPU引擎保留，不修改显卡驱动；首次验证会短时加载现有摘要模型。
".venv\Scripts\python.exe" -B install_summary_gpu.py
if errorlevel 1 (
    echo 安装或GPU验证未完成，原CPU引擎仍可使用。
    pause
    exit /b 1
)
echo 完成。重新打开听记，选择“自动（优先显卡）”即可用于摘要。
pause
