@echo off
rem ============================================================
rem  VoxCPM2 WebUI  -  launch WITH a visible console window
rem  Double-click to start. The console window shows live logs
rem  and can be shown/hidden by the console-toggle button in the UI.
rem  (ASCII-only content: .bat must stay ASCII on GBK systems.)
rem ============================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"
set "VOXCPM_MODEL_DIR=%~dp0model\openbmb\VoxCPM2"
set PYTHONPATH=
if not exist cache mkdir cache
start "VoxCPM2 WebUI Console" "%~dp0python_cuda\python.exe" "%~dp0Scripts\vox_web_ui.py" --port 18978 --host 127.0.0.1
exit
