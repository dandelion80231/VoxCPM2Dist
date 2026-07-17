@echo off
chcp 65001 >nul 2>&1
title VoxCPM2 Web UI
cd /d "%~dp0"
set "VOXCPM_MODEL_DIR=%~dp0model\openbmb\VoxCPM2"
set "VOXCPM_OUTPUT_DIR=%USERPROFILE%\Desktop"
set PYTHONPATH=
if not exist cache mkdir cache

start "" "%~dp0python_cuda\python.exe" "%~dp0Scripts\vox_web_ui.py" --port 18978 --host 127.0.0.1

exit
