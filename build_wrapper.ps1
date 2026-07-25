# 包装脚本：运行 build_installer.ps1 并把全部输出（含 7z / ISCC）重定向到 build_run.log
# 由计划任务 VoxBuild5304 调用，脱离 Agent 进程树独立运行。
$ErrorActionPreference = 'Continue'
$root = "D:\AI\Build\VoxCPM2Dist"
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -Path "$root\build_run.log" -Value "`n===== BUILD START $timestamp =====" -Encoding UTF8
& "$root\build_installer.ps1" *> "$root\build_run.log"
Add-Content -Path "$root\build_run.log" -Value "===== BUILD EXIT $timestamp (code $LASTEXITCODE) =====" -Encoding UTF8
