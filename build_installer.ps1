# VoxCPM2 TTS 安装包构建脚本（7z 预压缩 + InnoSetup）
# 用法：
#   .\build_installer.ps1                      # 自动检测 7-Zip / NanaZip；缺失则自动下载静默安装 7-Zip
#   .\build_installer.ps1 -CompressorPath "C:\...\NanaZipC.exe"   # 显式指定 nano zip / NanaZip 控制台程序
#
# 说明：
#   - 本机需有 InnoSetup 6（用于编译安装包）。已检测到位于默认路径。
#   - 压缩器优先级：手动指定 > 标准 7-Zip > NanaZip(NanaZipC.exe) > PATH > 自动下载静默安装 7-Zip。
#   - 无论用哪个压缩器生成 app.7z，安装包内解压器统一使用官方 7za.exe（脚本自动获取），
#     因此 nano zip 仅用于“生成”步骤，不影响安装端解压兼容性。

[CmdletBinding()]
param(
    # 可手动指定一个 7z 兼容的压缩程序（例如 nano zip / NanaZip 的控制台程序）。
    [string]$CompressorPath
)

$ErrorActionPreference = 'Stop'
$root    = "D:\AI\Build\VoxCPM2Dist"
$app     = Join-Path $root 'app'
$payload = Join-Path $root 'payload'
$assets  = Join-Path $root 'installer\assets'
New-Item -ItemType Directory -Force -Path $payload | Out-Null

# ── 0. 定位 / 安装 7z 兼容压缩器 ──
function Find-Compressor {
    param([string]$Override)
    if ($Override -and (Test-Path $Override)) {
        return @{ Path = $Override; Type = 'user' }
    }
    # 1) 标准 7-Zip
    $p7 = @('C:\Program Files\7-Zip\7z.exe','C:\Program Files (x86)\7-Zip\7z.exe') |
          Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($p7) { return @{ Path = $p7; Type = '7z' } }
    # 2) NanaZip（nano zip 的常见形态，7-Zip 分支，产出标准 7z 格式）
    $pN = @('C:\Program Files\NanaZip\NanaZipC.exe','C:\Program Files (x86)\NanaZip\NanaZipC.exe') |
          Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $pN) { $pN = (Get-Command NanaZipC -ErrorAction SilentlyContinue).Source }
    if ($pN) { return @{ Path = $pN; Type = 'nana' } }
    # 3) PATH 中的 7z / NanaZipC
    $pc = (Get-Command 7z -ErrorAction SilentlyContinue).Source
    if ($pc) { return @{ Path = $pc; Type = '7z' } }
    $pc2 = (Get-Command NanaZipC -ErrorAction SilentlyContinue).Source
    if ($pc2) { return @{ Path = $pc2; Type = 'nana' } }
    # 4) 都没有 -> 自动下载并静默安装 7-Zip（需联网 + 管理员）
    Write-Host '[0] 未检测到 7-Zip / NanaZip，尝试自动下载并静默安装 7-Zip...'
    $installer = Join-Path $env:TEMP '7z-install.exe'
    if (-not (Test-Path $installer)) {
        Invoke-WebRequest -Uri 'https://7-zip.org/a/7z2408-x64.exe' -OutFile $installer
    }
    Start-Process -FilePath $installer -ArgumentList '/S' -Wait
    $p7 = @('C:\Program Files\7-Zip\7z.exe','C:\Program Files (x86)\7-Zip\7z.exe') |
          Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $p7) { Write-Error '自动安装 7-Zip 失败，请手动安装 7-Zip 或 NanaZip 后重试。'; exit 1 }
    return @{ Path = $p7; Type = '7z' }
}

$c = Find-Compressor -Override $CompressorPath
$sevenZip = $c.Path
Write-Host "[1/4] 使用压缩器 ($($c.Type)): $sevenZip"
# NanaZip 控制台对个别高级开关支持不同，这里用最稳的等价参数。
# 注意：必须用数组（每个 flag 独立元素），否则 PowerShell 会把整串当成一个参数传给 7z，
#       导致 7z 把 "-t7z -mmt=on ..." 当成归档类型 → "Unsupported archive type"。
# 单线程 LZMA2：7z 24.08 多线程(-mmt=on)压 9GB+ 超大单文件(model.safetensors 4.3GB / torch 大 dll) 会间歇性 native 崩溃
# （无报错文本、每次崩在 ~1.2GB 输出处，与文件损坏不同）。-mmt=off 稳定；去掉 -myx=9（超大文件上易崩且收益小）。
# 注意：本机 7z 24.08 的 LZMA2 在压缩 app/ 内 torch CUDA 大 DLL（cusparse/cublas/cudnn 等）时
# 会 native 崩溃（无任何报错，单/多线程、限固实块均无效，每次死在 ~2 分钟 ~1GB 处）。
# 改用 PPMd 算法（非 LZMA2 代码路径）彻底规避该 bug；PPMd 对 exe/DLL 压缩率通常优于 LZMA2。
$compressFlags = @('-t7z','-m0=PPMd','-mmt=off','-mx=7')

# ── 2. 准备 7za.exe（供安装包内解压；独立版，需真正的 7za.exe）──
$sevenZipDir = Split-Path $sevenZip
$src7za = Join-Path $sevenZipDir '7za.exe'

# 2a) 优先复用本机已有的 7za.exe（避免联网下载）
if (-not (Test-Path $src7za)) {
    $candidates = @(
        'C:\Program Files\7-Zip\7za.exe',
        'C:\Program Files (x86)\7-Zip\7za.exe',
        'C:\Program Files\Autodesk\AdODIS\V1\Setup\7za.exe'
    )
    # 扫描 LOCALAPPDATA 下随附的 7za.exe（如 NeteaseDD / uTools 等）
    if (Test-Path $env:LOCALAPPDATA) {
        $candidates += @(Get-ChildItem -Path $env:LOCALAPPDATA -Recurse -Filter '7za.exe' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName)
    }
    foreach ($cand in ($candidates | Select-Object -Unique)) {
        if (Test-Path $cand) { $src7za = $cand; break }
    }
}

# 2b) 本机没有则下载 7-Zip Extra（官方独立版），并解包出 7za.exe
if (-not (Test-Path $src7za)) {
    Write-Host '[2/4] 未找到 7za.exe，下载 7-Zip Extra 并解包...'
    # 注意：最新版 7-Zip 已迁移到 GitHub 发布；7-zip.org/a/ 仅保留 23.01 及更早版本。
    # 优先用 7-zip.org/a 的稳定镜像（其 7za 完全可解压 LZMA2 归档），失败再回退 GitHub 最新版。
    $extraUrls = @(
        'https://7-zip.org/a/7z2301-extra.7z',
        'https://github.com/ip7z/7zip/releases/download/26.02/7z2602-extra.7z'
    )
    $extra = Join-Path $env:TEMP '7z-extra.7z'
    $ok = $false
    foreach ($u in $extraUrls) {
        try {
            if (-not (Test-Path $extra)) {
                Invoke-WebRequest -Uri $u -OutFile $extra -ErrorAction Stop
            }
            $extraDir = Join-Path $env:TEMP '7zextra'
            # 用当前的 7z 兼容压缩器解包（7z.exe 或 NanaZipC.exe 都能解 7z）
            & $sevenZip x $extra "-o$extraDir" -y | Out-Null
            $cand = Join-Path $extraDir '7za.exe'
            if (Test-Path $cand) { $src7za = $cand; $ok = $true; break }
        } catch {
            Write-Host "  下载/解包失败: $u"
        }
    }
    if (-not $ok) { Write-Error '未能取得 7za.exe。'; exit 1 }
}
if (-not (Test-Path $src7za)) { Write-Error '未能取得 7za.exe。'; exit 1 }
Copy-Item $src7za (Join-Path $payload '7za.exe') -Force
Write-Host '[2/4] 已准备 7za.exe'

# ── 3. 预压缩 app -> app.7z（扁平结构）──
$app7z = Join-Path $payload 'app.7z'
if (Test-Path $app7z) { Remove-Item $app7z -Force }
Write-Host '[3/4] 正在压缩 app（PPMd 算法，规避 7z LZMA2 压 torch 大 DLL 的崩溃 bug，约 30-60 分钟）...'
# 先进入 app 目录再归档 *，避免把 app\ 前缀打进归档导致解压出现双层目录
# 注：7z 输出（含报错）重定向到 build_7z.log，避免 | Out-Null 吞掉真实错误导致盲猜
Push-Location $app
try {
    & $sevenZip a $compressFlags $app7z '*' *> (Join-Path $root 'build_7z.log')
} finally {
    Pop-Location
}
if (-not (Test-Path $app7z)) { Write-Error "7z 压缩未产出 app.7z，详见 build_7z.log"; exit 1 }
$logTail = Get-Content (Join-Path $root 'build_7z.log') -Tail 15 -ErrorAction SilentlyContinue
if ($logTail) { Write-Host "--- 7z 日志尾部 ---`n$($logTail -join "`n")" }
$sizeGB = [math]::Round((Get-Item $app7z).Length / 1GB, 2)
Write-Host "[3/4] app.7z 完成: $sizeGB GB"

# ── 4. 编译安装包 ──
$iscc = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
if (-not (Test-Path $iscc)) { $iscc = 'C:\Program Files\Inno Setup 6\ISCC.exe' }
if (-not (Test-Path $iscc)) { Write-Error '未找到 ISCC.exe，请安装 InnoSetup 6。'; exit 1 }
Write-Host '[4/4] 编译安装包（InnoSetup，仅打包 app.7z/7za/ico，很快）...'
& $iscc (Join-Path $root 'installer\VoxCPM2_TTS.iss') | Out-Host
Write-Host "[完成] 安装包已生成于 $root\output"
