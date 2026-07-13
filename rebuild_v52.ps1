# VoxCPM2 v5.2 重打脚本（构建 + 打包 zip）
# 由独立 Windows 计划任务调用，脱离 Agent 会话运行。
$root       = "D:\AI\Build\VoxCPM2Dist"
$buildScript = Join-Path $root 'build_installer.ps1'
$zip         = Join-Path $root 'VoxCPM2_TTS_v5.2_Setup.zip'
$sevenZip    = 'C:\Program Files\7-Zip\7z.exe'
$log         = Join-Path $root 'build_task.log'

function Log($msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "[$ts] $msg"
    Write-Host "[$ts] $msg"
}

Log "=== 开始 v5.2 重打（含 Banner 修复）==="

# ── 1. 构建（生成 payload/app.7z + output/VoxCPM2_TTS_v5.2_Setup.*）──
Log "[1/2] 运行 build_installer.ps1（7z 压缩 app + InnoSetup 编译，约 15-20 分钟）..."
& $buildScript
if ($LASTEXITCODE -ne 0) { Log "ERROR: build_installer.ps1 退出码 $LASTEXITCODE"; exit 1 }
Log "[1/2] 构建完成"

# 确认 v5.2 产物存在
$v52files = Get-ChildItem "$root\output\VoxCPM2_TTS_v5.2_Setup*" | Select-Object -ExpandProperty FullName
if ($v52files.Count -eq 0) { Log "ERROR: 未在 output/ 找到 v5.2 安装文件"; exit 1 }
Log ("[1/2] 找到 v5.2 安装文件 {0} 个" -f $v52files.Count)

# ── 2. 删除旧 zip 并重新打包 v5.2 安装文件 ──
Log "[2/2] 删除旧 zip 并用 7z 重新打包 v5.2 安装文件..."
if (Test-Path $zip) { Remove-Item $zip -Force }
& $sevenZip a -tzip $zip $v52files | Out-Null
if (-not (Test-Path $zip)) { Log "ERROR: zip 未生成"; exit 1 }
$sizeGB = [math]::Round((Get-Item $zip).Length / 1GB, 3)
$sizeB  = (Get-Item $zip).Length
Log ("[2/2] 完成：{0}（{1} GB，{2} 字节）" -f $zip, $sizeGB, $sizeB)
Log "=== v5.2 重打全部完成 ==="
