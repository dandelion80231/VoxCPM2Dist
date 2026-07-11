@echo off
chcp 65001 >nul 2>&1
title VoxCPM2 Web UI
cd /d "%~dp0"
set "VOXCPM_MODEL_DIR=%~dp0model\openbmb\VoxCPM2"
set "VOXCPM_OUTPUT_DIR=%USERPROFILE%\Desktop"
set PYTHONPATH=

echo VoxCPM2 Web UI starting...
echo Model: %VOXCPM_MODEL_DIR%
echo Output: %VOXCPM_OUTPUT_DIR%
echo.

start "" "%~dp0python_cuda\python.exe" "%~dp0Scripts\vox_web_ui.py" --port 8000 --host 127.0.0.1

exit
