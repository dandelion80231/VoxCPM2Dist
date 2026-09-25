@echo off
rem ============================================================
rem  VoxCPM2 OpenAI-compatible API launcher (v5.3.7)
rem  - backend (vox_web_ui.py) auto-starts on port 19001 if down
rem  - OpenAI-compatible adapter serves on port 8020
rem  - API doc: OpenAI_API_QuickRef.md  (install dir root)
rem ============================================================
cd /d "%~dp0"
set "PY=%~dp0..\python_cuda\python.exe"
set "VOXCPM_BACKEND=http://127.0.0.1:19001"
set "VOXCPM_MODEL_DIR=%~dp0..\model\openbmb\VoxCPM2"

rem --- 1) backend :19001 ---
netstat -ano | findstr ":19001 " | findstr /i "LISTENING" >nul 2>&1
if errorlevel 1 (
  echo [backend] port 19001 not listening - starting Web UI backend ^(first run loads model, wait 1-3 min^)...
  start "" "%~dp0..\start_web_ui.bat"
  timeout /t 15 /nobreak >nul
) else (
  echo [backend] port 19001 already up.
)

rem --- 2) adapter :8020 ---
netstat -ano | findstr ":8020 " | findstr /i "LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [adapter] port 8020 already up: http://127.0.0.1:8020/v1
  pause
  exit /b 0
)
echo [adapter] starting OpenAI-compatible API on port 8020 ^-> backend 19001
echo [adapter] quick test: curl http://127.0.0.1:8020/v1/models
"%PY%" "%~dp0voxcpm_openai.py"
pause
