@echo off
chcp 65001

call :say "正在下载 VoxCPM2 主模型到 model\openbmb\VoxCPM2（需联网，支持断点续传）..."
python_cuda\python.exe download_model.py
call :say "下载脚本已执行完毕。若上方提示有文件未下载成功，请检查网络后重跑本脚本，或改用 README 中的网盘/夸克链接手动放置模型。"
pause
goto :eof

:say
set "MSG=%~1"
powershell -NoProfile -Command "Write-Host $env:MSG"
goto :eof