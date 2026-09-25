@echo off
rem ============================================================
rem  VoxCPM2 TTS - Command-Line (CLI) Interactive Menu Launcher
rem  Double-click to open the console TTS menu.
rem  Delegates to Scripts\Launch_TTS_Menu.bat
rem  (ASCII-only content: .bat must stay ASCII on GBK systems)
rem ============================================================
chcp 65001 >nul 2>&1
call "%~dp0Scripts\Launch_TTS_Menu.bat"
