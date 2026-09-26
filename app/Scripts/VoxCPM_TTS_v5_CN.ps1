# VoxCPM2 TTS 启动器 中文版（版本号见 app/version.txt）
#
# [v5.2 改进]
#   - 方式1/2：预生成多语调参考音频（cfg 3.0 / steps 20）
#   - 音色几乎无漂移（与用户验证方法一致）
#   - 默认 80ms 交叉淡入淡出

$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$APP_DIR   = Split-Path -Parent $SCRIPT_DIR
# 版本号统一从 app/version.txt 读取（单一数据源，避免散落硬编码漏改）
$VERSION = (Get-Content (Join-Path $APP_DIR 'version.txt') -Encoding UTF8 -Raw).Trim() -replace '^\uFEFF',''
if (-not $VERSION) { $VERSION = '5.3' }
$PYTHON    = Join-Path $APP_DIR "python_cuda\python.exe"
$SCRIPT    = Join-Path $SCRIPT_DIR "voxcpm_tts_v5_longtext.py"

# 引擎 stdout/stderr 强制 UTF-8：子进程默认继承 GBK 控制台码页，第三方库日志含
# PUA/生僻字符时 print 会抛 UnicodeEncodeError（v5.3.7 菜单新增项实测发现）。
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8       = "1"

# ---------- 模型/输出路径（离线优先使用随包模型） ----------
$env:VOXCPM_MODEL_DIR = Join-Path $APP_DIR "model\openbmb\VoxCPM2"
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }

# ---------- 无模型版：缺失主模型时给出明确指引，并可选一键下载 ----------
$modelSafetensors = Join-Path $APP_DIR "model\openbmb\VoxCPM2\model.safetensors"
if (-not (Test-Path $modelSafetensors)) {
    Write-Host ""
    Write-Host "【提示】未检测到 VoxCPM2 主模型权重 (model.safetensors)。" -ForegroundColor Yellow
    Write-Host "请通过以下任一方式获取后重启本程序：" -ForegroundColor Yellow
    Write-Host "  1) 双击安装目录下的「下载模型.bat」一键下载（需联网）" -ForegroundColor Cyan
    Write-Host "  2) 从网盘下载模型专用包，解压到 model\openbmb\VoxCPM2 目录" -ForegroundColor Cyan
    Write-Host "  3) 从 HuggingFace(openbmb/VoxCPM2) 或 ModelScope 手动下载放入" -ForegroundColor Cyan
    Write-Host ""
    $ans = Read-Host "是否现在运行「下载模型.bat」下载模型？(Y/N)"
    if ($ans -match '^[Yy]') {
        & (Join-Path $APP_DIR "下载模型.bat")
        if (-not (Test-Path $modelSafetensors)) {
            Write-Host "【提示】下载未完成或失败，请按上述方式手动获取后重启。" -ForegroundColor Yellow
            pause
            exit
        }
        Write-Host "【完成】模型已就绪，继续启动..." -ForegroundColor Green
    } else {
        Write-Host "请先获取模型再使用本工具。" -ForegroundColor Yellow
        pause
        exit
    }
}
$env:VOXCPM_OUTPUT_DIR = [System.Environment]::GetFolderPath('Desktop')

# ---------- 桌面路径获取（兼容中文/英文系统） ----------
$desktop = [System.Environment]::GetFolderPath('Desktop')

# 音色预设映射表
$VOICE_PRESETS = @{
    "1" = "25岁年轻温柔甜美女声，带一点播音腔，语速稍平缓"
    "2" = "年轻女性，活泼开朗，语速偏快"
    "3" = "年轻男性，声音沉稳，语速平缓，适合新闻播报"
    "4" = "年轻男性，声音低沉冷静，略带磁性"
    "sweet_girl" = "25岁年轻温柔甜美女声，带一点播音腔，语速稍平缓"
    "warm_woman" = "年轻女性，温柔甜美，语速适中"
    "gentleman" = "中年男性，温润儒雅，播音腔，语速平缓"
    "energetic_broadcaster" = "热情洋溢的中年男性播音员，声音低沉富有磁性"
    "elder_woman" = "老年女性，声音温和慈祥，语速缓慢"
    "cool_guy" = "年轻男性，声音低沉冷静，略带磁性"
    "cheerful_girl" = "年轻女性，活泼开朗，语速偏快"
    "storyteller" = "中年男性，深沉有磁性，适合讲故事，节奏平缓"
    "calm_male" = "年轻男性，声音沉稳，语速平缓，适合新闻播报"
    "teacher" = "中年女性，声音清晰有力，语速适中，适合教学讲解"
}

function Resolve-Voice {
    param($InputStr)
    $trimmed = $InputStr.Trim()
    if ($VOICE_PRESETS.ContainsKey($trimmed)) {
        return $VOICE_PRESETS[$trimmed]
    }
    return $trimmed
}

function Get-DisplayWidth {
    # 计算字符串在等宽终端的显示宽度（CJK 等东亚宽字符占 2 格，其余占 1 格）
    param([string]$s)
    $w = 0
    foreach ($c in $s.ToCharArray()) {
        if ($c -ge 0x2E80) { $w += 2 } else { $w += 1 }
    }
    return $w
}

function Format-BannerLine {
    # 按显示宽度居中填充到 $width 格，两侧补 |，保证右 | 与边框对齐
    param([string]$s, [int]$width = 58)
    $w = Get-DisplayWidth $s
    $pad = [Math]::Max(0, $width - $w)
    $left = [Math]::Floor($pad / 2)
    $right = $pad - $left
    return '|' + (' ' * $left) + $s + (' ' * $right) + '|'
}

function Show-Banner {
    Clear-Host
    Write-Host "                                                            " -ForegroundColor Cyan
    Write-Host "V   V     OOO     X   X     CCC     PPPP     M   M     222  " -ForegroundColor Cyan
    Write-Host "V   V    O   O     X X     C   C    P   P    MM MM    2   2 " -ForegroundColor Cyan
    Write-Host "V   V    O   O      X      C        PPPP     M M M      2  "  -ForegroundColor Cyan
    Write-Host " V V     O   O     X X     C   C    P        M   M     2   "  -ForegroundColor Cyan
    Write-Host "  V       OOO     X   X     CCC     P        M   M    22222 " -ForegroundColor Cyan
    Write-Host "+==========================================================+" -ForegroundColor Cyan
    Write-Host (Format-BannerLine "") -ForegroundColor Cyan
    Write-Host (Format-BannerLine "VoxCPM2 语音合成工具 v$VERSION 音色统一版") -ForegroundColor Cyan
    Write-Host (Format-BannerLine "") -ForegroundColor Cyan
    Write-Host (Format-BannerLine "长文本配音 / 音色一致 / 交叉淡入淡出") -ForegroundColor Cyan
    Write-Host (Format-BannerLine "") -ForegroundColor Cyan
    Write-Host "+==========================================================+" -ForegroundColor Cyan
}
function Show-Menu {
    Show-Banner
    Write-Host "[快速命令]" -ForegroundColor Yellow
    Write-Host "  1  - 温柔女声（默认）"
    Write-Host "  2  - 活泼女声"
    Write-Host "  3  - 沉稳男声"
    Write-Host "  4  - 磁性男声"
    Write-Host "  5  - 自定义音色"
    Write-Host "  6  - 交互模式"
    Write-Host "  7  - 列出音色预设"
    Write-Host "  8  - 查看配置"
    Write-Host ""
    Write-Host "[长文本配音]" -ForegroundColor Yellow
    Write-Host "  9  - 长文本文件配音（方式2：固定参考音频，最稳定）"
    Write-Host "  10 - 长文本文件配音（方式1：自播种，第1段当种子）"
    Write-Host "  11 - 长文本文件配音（方式3：逐段音色设计）"
    Write-Host "  12 - 生成参考音频（用于长文本统一音色）"
    Write-Host ""
    Write-Host "[其他]" -ForegroundColor Yellow
    Write-Host "  13 - 克隆已有音频（Controllable Clone）"
    Write-Host "  14 - 终极克隆（Ultimate Clone）"
    Write-Host ""
    Write-Host "[音色档案 / 语料]（与 Web UI 共用后端）" -ForegroundColor Yellow
    Write-Host "  15 - 音色档案管理（列表/保存/删除/导出/导入）"
    Write-Host "  16 - 多音字语料管理（导入/导出）"
    Write-Host ""
    Write-Host "[合成参数]（与 Web UI 设置页等价）" -ForegroundColor Yellow
    Write-Host "  17 - 可复现种子合成（同种子结果可复现）"
    Write-Host "  18 - 时间戳 / SRT 字幕（词/字级对齐，首用自动下载 qwen3 模型）"
    Write-Host "  19 - 多音字 LoRA 挂载合成"
    Write-Host "  20 - CPU 模式合成（--no-cuda）"
    Write-Host "  21 - 指定输出目录合成"
    Write-Host "  22 - 防漂移：每 N 段更新一次参考音频"
    Write-Host "  0  - 退出"
    Write-Host ""
    Write-Host "[直接输入] 输入任意文本直接合成（>180字自动长文本自播种模式）" -ForegroundColor Green
    Write-Host "[高级用法] 输入完整 Python 参数（如: -f 文件.txt --reference ref.wav）" -ForegroundColor Green
    Write-Host ""
}

function Invoke-TTS {
    param($Text, $Voice = $null, $Control = $null, $ExtraArgs = "")

    # 解析音色描述：优先 Control，其次 Voice 预设，最后默认温柔女声
    if ($Control) {
        $controlStr = $Control
    } elseif ($Voice) {
        $controlStr = Resolve-Voice -InputStr $Voice
    } else {
        $controlStr = $VOICE_PRESETS["sweet_girl"]
    }

    # 超过 180 字：自动走「自播种（第1段当种子）」长文本流程
    # （第1段 Voice Design -> 作为后续段参考克隆 -> 自动分段 -> 交叉淡入淡出 -> 段间 RMS 归一化）
    # 不预生成参考音频，比固定参考更快，且与网页端行为一致。
    if ($Text.Length -gt 180) {
        Write-Host ""
        Write-Host "[长文本自动模式] 文本超过 180 字，自动使用自播种长文本流程（第1段当种子，更快）" -ForegroundColor Cyan

        $argList = @("-t", $Text, "-c", $controlStr,
                     "--self-seeding",
                     "--split", "auto", "--chunk-size", "180",
                     "--crossfade", "80")
        if ($ExtraArgs) {
            $argList += @($ExtraArgs -split "\s+")
        }

        Write-Host ""
        Write-Host "[合成中] 长文本分段处理中（自播种模式），请稍候..." -ForegroundColor Cyan
        & $PYTHON $SCRIPT @argList
        Write-Host ""
        Write-Host "[完成] 长文本合成结束！" -ForegroundColor Green
        return
    }

    # 短文本（≤180 字）：原逻辑
    $argList = @("-t", $Text)
    if ($Voice) {
        $argList += @("--voice", $Voice)
    } elseif ($Control) {
        $argList += @("-c", $Control)
    }
    if ($ExtraArgs) {
        $argList += @($ExtraArgs -split "\s+")
    }

    Write-Host ""
    Write-Host "[合成中] 请稍候..." -ForegroundColor Cyan
    & $PYTHON $SCRIPT @argList
    Write-Host ""
    Write-Host "[完成] 合成结束！" -ForegroundColor Green
}

function Invoke-LongText {
    param($FilePath, $Control, $Mode = "2", $ExtraArgs = "")

    $FilePath = $FilePath.Trim().Trim([char]34)

    if (-not (Test-Path $FilePath)) {
        Write-Host ""
        Write-Host "[错误] 文件不存在: $FilePath" -ForegroundColor Red
        Write-Host "[提示] 请检查路径是否正确，不要带引号" -ForegroundColor Yellow
        return
    }

    $modeName = switch ($Mode) {
        "2" { "固定参考音频克隆（最稳定）" }
        "1" { "自播种克隆（第1段当种子）" }
        "3" { "逐段音色设计（音色可能不一致）" }
        default { "固定参考音频克隆" }
    }

    Write-Host ""
    Write-Host "[模式] $modeName" -ForegroundColor Cyan

    $argList = @("-f", $FilePath, "-c", $Control, "--split", "auto", "--chunk-size", "180")

    switch ($Mode) {
        "2" {
            $refPath = Read-Host "请输入参考音频路径（留空则自动生成）"
            $refPath = $refPath.Trim().Trim([char]34)
            if (-not $refPath) {
                Write-Host "[参考音频] 正在生成多语调参考音频（更丰富的韵律采样）..." -ForegroundColor Yellow
                $refText = "你好，欢迎使用语音合成系统。今天将为您带来一段精彩的语音合成演示，让我们一起体验人工智能技术带来的便捷与乐趣。我们的技术正在不断进步，力求为您提供更加自然流畅的语音体验。"
                $refFile = Join-Path $desktop "ref_voice.wav"
                & $PYTHON $SCRIPT -t $refText -c $Control --cfg 3.0 --steps 20 -o $refFile
                Write-Host "[参考音频] 已保存: $refFile" -ForegroundColor Green
                $refPath = $refFile
            }
            if (-not (Test-Path $refPath)) {
                Write-Host "[错误] 参考音频不存在: $refPath" -ForegroundColor Red
                return
            }
            $argList += @("--reference", $refPath)
        }
        "1" {
            # 真正自播种：第1段 Voice Design 直接当后续段参考（不预生成参考音频）
            # 锚定正文开头，更连贯；省去整条参考生成 pass，更快；与「>180字自动路径」行为一致
            Write-Host ""
            Write-Host "[自播种] 第1段当种子：后续段锚定正文开头音色（不预生成参考）" -ForegroundColor Cyan
            $argList += @("--self-seeding", "--crossfade", "80")
        }
        "3" {
            $argList += "--no-self-seeding"
        }
    }

    if ($ExtraArgs) {
        $argList += $ExtraArgs -split " "
    }

    Write-Host "[合成中] 长文本分段处理中，请稍候..." -ForegroundColor Cyan
    & $PYTHON $SCRIPT @argList
    Write-Host ""
    Write-Host "[完成] 长文本合成结束！" -ForegroundColor Green
}

while ($true) {
    Show-Menu
    $choice = Read-Host "请输入命令或文本"

    switch ($choice) {
        "0" {
            Write-Host "再见！" -ForegroundColor Green
            exit
        }
        "1" {
            $text = Read-Host "请输入文本"
            Invoke-TTS -Text $text -Voice "sweet_girl"
            pause
        }
        "2" {
            $text = Read-Host "请输入文本"
            Invoke-TTS -Text $text -Voice "cheerful_girl"
            pause
        }
        "3" {
            $text = Read-Host "请输入文本"
            Invoke-TTS -Text $text -Voice "calm_male"
            pause
        }
        "4" {
            $text = Read-Host "请输入文本"
            Invoke-TTS -Text $text -Voice "cool_guy"
            pause
        }
        "5" {
            $text = Read-Host "请输入文本"
            $control = Read-Host "请输入音色描述（如：25岁年轻温柔甜美女声，带一点播音腔）"
            Invoke-TTS -Text $text -Control $control
            pause
        }
        "6" {
            Write-Host ""
            Write-Host "[交互模式] 输入 q 退出，h 查看帮助" -ForegroundColor Yellow
            & $PYTHON $SCRIPT -i
            Write-Host ""
            Write-Host "[交互模式结束]" -ForegroundColor Green
            pause
        }
        "7" {
            & $PYTHON $SCRIPT --list-voices
            pause
        }
        "8" {
            & $PYTHON $SCRIPT --show-config
            pause
        }
        "9" {
            $file = Read-Host "请输入文件路径"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设）"
            $control = Resolve-Voice -InputStr $controlInput
            Invoke-LongText -FilePath $file -Control $control -Mode "2"
            pause
        }
        "10" {
            $file = Read-Host "请输入文件路径"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设）"
            $control = Resolve-Voice -InputStr $controlInput
            Invoke-LongText -FilePath $file -Control $control -Mode "1"
            pause
        }
        "11" {
            $file = Read-Host "请输入文件路径"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设）"
            $control = Resolve-Voice -InputStr $controlInput
            Invoke-LongText -FilePath $file -Control $control -Mode "3"
            pause
        }
        "12" {
            Write-Host ""
            Write-Host "[生成参考音频] 用于长文本统一音色" -ForegroundColor Yellow
            $text = Read-Host "请输入参考文本（建议一句简短的话）"
            if (-not $text) { $text = "你好，欢迎使用语音合成系统。" }
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设）"
            $control = Resolve-Voice -InputStr $controlInput
            $output = Read-Host "请输入保存路径（留空默认桌面 ref_voice.wav）"
            $output = $output.Trim().Trim([char]34)
            if (-not $output) { $output = Join-Path $desktop "ref_voice.wav" }
            & $PYTHON $SCRIPT -t $text -c $control -o $output
            Write-Host ""
            Write-Host "[完成] 参考音频已保存: $output" -ForegroundColor Green
            Write-Host "[提示] 后续长文本配音时使用 --reference $output" -ForegroundColor Yellow
            pause
        }
        "13" {
            Write-Host ""
            Write-Host "[克隆模式] 使用已有音频作为参考，合成新文本" -ForegroundColor Yellow
            $text = Read-Host "请输入要合成的文本"
            $ref = Read-Host "请输入参考音频路径"
            $ref = $ref.Trim().Trim([char]34)
            if (-not (Test-Path $ref)) {
                Write-Host "[错误] 参考音频不存在！" -ForegroundColor Red
                pause
                continue
            }
            & $PYTHON $SCRIPT -t $text --reference $ref
            Write-Host ""
            Write-Host "[完成] 克隆合成结束！" -ForegroundColor Green
            pause
        }
        "14" {
            Write-Host ""
            Write-Host "[终极克隆] 使用参考音频+原文本，最高保真度" -ForegroundColor Yellow
            $text = Read-Host "请输入要合成的文本"
            $ref = Read-Host "请输入参考音频路径"
            $ref = $ref.Trim().Trim([char]34)
            $refText = Read-Host "请输入参考音频对应的原文本"
            if (-not (Test-Path $ref)) {
                Write-Host "[错误] 参考音频不存在！" -ForegroundColor Red
                pause
                continue
            }
            & $PYTHON $SCRIPT -t $text --prompt-audio $ref --prompt-text $refText --reference $ref
            Write-Host ""
            Write-Host "[完成] 终极克隆合成结束！" -ForegroundColor Green
            pause
        }
        "15" {
            Write-Host ""
            Write-Host "[音色档案管理] 与 Web UI 共用（voxcpm_profiles.json，随包已含 8 个预设档案）" -ForegroundColor Yellow
            Write-Host "  1 - 列出全部档案"
            Write-Host "  2 - 保存当前配置为档案"
            Write-Host "  3 - 删除档案"
            Write-Host "  4 - 导出档案 JSON（留空自动存 exports\ 目录）"
            Write-Host "  5 - 导入档案 JSON"
            Write-Host "  0 - 返回"
            $psel = Read-Host "请选择"
            switch ($psel) {
                "1" {
                    & $PYTHON $SCRIPT --list-profiles
                }
                "2" {
                    $pname = (Read-Host "档案名称（不可含 ':'，如：深宫太后").Trim()
                    if ([string]::IsNullOrWhiteSpace($pname)) {
                        Write-Host "[错误] 档案名不能为空" -ForegroundColor Red
                        break
                    }
                    $pv = (Read-Host "预设音色（1/2/3/4 或预设名，留空默认").Trim()
                    $pc = (Read-Host "音色描述（可留空").Trim()
                    $pm = Read-Host "模式: 1=默认 voice_design  2=自播种  3=固定参考（留空=1）"
                    $pargs = @("--profile-save", $pname)
                    if ($pv) { $pargs += @("--voice", $pv) }
                    if ($pc) { $pargs += @("-c", $pc) }
                    if ($pm -eq "2") { $pargs += "--self-seeding" }
                    if ($pm -eq "3") {
                        $pref = (Read-Host "参考音频路径").Trim().Trim([char]34)
                        $ppt  = (Read-Host "参考音频原文本（可留空").Trim()
                        if (-not (Test-Path $pref)) {
                            Write-Host "[错误] 参考音频不存在: $pref" -ForegroundColor Red
                            break
                        }
                        $pargs += @("--reference", $pref)
                        if ($ppt) { $pargs += @("--prompt-text", $ppt) }
                    }
                    & $PYTHON $SCRIPT @pargs
                }
                "3" {
                    $pname = Read-Host "要删除的档案名"
                    & $PYTHON $SCRIPT --profile-delete $pname
                }
                "4" {
                    $ppath = (Read-Host "导出路径（留空自动存 exports\ 目录").Trim().Trim([char]34)
                    if ($ppath) { & $PYTHON $SCRIPT --profile-export $ppath } else { & $PYTHON $SCRIPT --profile-export }
                }
                "5" {
                    $ipath = (Read-Host "要导入的档案 JSON 文件").Trim().Trim([char]34)
                    if (-not (Test-Path $ipath)) {
                        Write-Host "[错误] 文件不存在: $ipath" -ForegroundColor Red
                    } else {
                        & $PYTHON $SCRIPT --profile-import $ipath
                    }
                }
            }
            pause
        }
        "16" {
            Write-Host ""
            Write-Host "[多音字语料] 与 Web UI 共用；每行一条多音字修正（同 Web UI 语料编辑器口径，坏行导入时自动跳过）" -ForegroundColor Yellow
            Write-Host "  1 - 导入语料文本文件（UTF-8）"
            Write-Host "  2 - 导出语料到文件（留空自动存 exports\ 目录）"
            Write-Host "  0 - 返回"
            $csel = Read-Host "请选择"
            switch ($csel) {
                "1" {
                    $cpath = (Read-Host "要导入的语料文件").Trim().Trim([char]34)
                    if (-not (Test-Path $cpath)) {
                        Write-Host "[错误] 文件不存在: $cpath" -ForegroundColor Red
                    } else {
                        & $PYTHON $SCRIPT --corpus-import $cpath
                    }
                }
                "2" {
                    $cpath = (Read-Host "导出路径（留空自动存 exports\ 目录").Trim().Trim([char]34)
                    if ($cpath) { & $PYTHON $SCRIPT --corpus-export $cpath } else { & $PYTHON $SCRIPT --corpus-export }
                }
            }
            pause
        }
        "17" {
            Write-Host ""
            Write-Host "[可复现种子] 同种子 + 同文本 + 同设置 结果近似一致（官方特性；留空=随机）" -ForegroundColor Yellow
            $text = Read-Host "请输入文本"
            $seed = Read-Host "种子数字（如 42，留空随机）"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设，留空默认温柔女声）"
            $ctrl = if ($controlInput) { Resolve-Voice -InputStr $controlInput } else { $null }
            $extra = ""
            if ($seed) { $extra = "--seed $seed" }
            Invoke-TTS -Text $text -Control $ctrl -ExtraArgs $extra
            pause
        }
        "18" {
            Write-Host ""
            Write-Host "[时间戳/SRT] 输出词/字级时间戳（.timestamps.json；Qwen3 对齐优先，首用自动下载 ~1.75GB；不可用自动降级 whisper）" -ForegroundColor Yellow
            $text = Read-Host "请输入文本"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设，留空默认温柔女声）"
            $wantSrt = Read-Host "同时输出 SRT 字幕文件？(Y/N)"
            $ctrl = if ($controlInput) { Resolve-Voice -InputStr $controlInput } else { $null }
            $extra = "--timestamps"
            if ($wantSrt -match "^[Yy]") { $extra = "--timestamps --timestamps-srt" }
            Invoke-TTS -Text $text -Control $ctrl -ExtraArgs $extra
            pause
        }
        "19" {
            Write-Host ""
            Write-Host "[多音字 LoRA] 挂载多音字修正 LoRA 权重（训练产出的 step_XXXXXXX 目录，或 lora_weights.safetensors / .ckpt）；留空=不挂载" -ForegroundColor Yellow
            $lora = (Read-Host "LoRA 权重路径（留空跳过").Trim().Trim([char]34)
            $text = Read-Host "请输入文本"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设，留空默认温柔女声）"
            if ($lora -and -not (Test-Path $lora)) {
                Write-Host "[错误] LoRA 路径不存在: $lora" -ForegroundColor Red
                pause
                continue
            }
            $loraArgs = @()
            if ($lora) { $loraArgs += @("--lora", $lora) }
            $loraArgs += @("-t", $text)
            $ctrl = if ($controlInput) { Resolve-Voice -InputStr $controlInput } else { $null }
            if ($ctrl) { $loraArgs += @("-c", $ctrl) }
            if ($text.Length -gt 180) { $loraArgs += @("--self-seeding", "--split", "auto", "--chunk-size", "180", "--crossfade", "80") }
            Write-Host ""
            Write-Host "[合成中] 请稍候..." -ForegroundColor Cyan
            & $PYTHON $SCRIPT @loraArgs
            Write-Host ""
            Write-Host "[完成] LoRA 合成结束！" -ForegroundColor Green
            pause
        }
        "20" {
            Write-Host ""
            Write-Host "[CPU 模式] 强制 CPU 推理（--no-cuda；无 GPU / 显存不足场景，速度较慢）" -ForegroundColor Yellow
            $text = Read-Host "请输入文本"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设，留空默认温柔女声）"
            $ctrl = if ($controlInput) { Resolve-Voice -InputStr $controlInput } else { $null }
            Invoke-TTS -Text $text -Control $ctrl -ExtraArgs "--no-cuda"
            pause
        }
        "21" {
            Write-Host ""
            Write-Host "[指定输出目录] 结果 wav 写入指定目录（默认桌面）" -ForegroundColor Yellow
            $outdir = (Read-Host "输出目录（留空=桌面").Trim().Trim([char]34)
            $text = Read-Host "请输入文本"
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设，留空默认温柔女声）"
            $dirArgs = @()
            if ($outdir) {
                if (-not (Test-Path $outdir)) { New-Item -ItemType Directory -Path $outdir -Force | Out-Null }
                $dirArgs += @("--dir", $outdir)
            }
            $dirArgs += @("-t", $text)
            $ctrl = if ($controlInput) { Resolve-Voice -InputStr $controlInput } else { $null }
            if ($ctrl) { $dirArgs += @("-c", $ctrl) }
            if ($text.Length -gt 180) { $dirArgs += @("--self-seeding", "--split", "auto", "--chunk-size", "180", "--crossfade", "80") }
            Write-Host ""
            Write-Host "[合成中] 请稍候..." -ForegroundColor Cyan
            & $PYTHON $SCRIPT @dirArgs
            $destDesc = "桌面"
            if ($outdir) { $destDesc = $outdir }
            Write-Host ""
            Write-Host "[完成] 合成结束！输出目录: $destDesc" -ForegroundColor Green
            pause
        }
        "22" {
            Write-Host ""
            Write-Host "[防漂移] 长文本固定参考模式：每 N 段更新一次参考音频，防后段音色漂移（0=不更新）" -ForegroundColor Yellow
            $file = Read-Host "请输入文本文件路径"
            $file = $file.Trim().Trim([char]34)
            if (-not (Test-Path $file)) {
                Write-Host "[错误] 文件不存在: $file" -ForegroundColor Red
                pause
                continue
            }
            $controlInput = Read-Host "请输入音色描述（或输入 1/2/3/4 使用预设）"
            $control = Resolve-Voice -InputStr $controlInput
            $n = Read-Host "参考更新间隔（段数，留空或 0=不更新）"
            $refPath = (Read-Host "参考音频路径（留空自动生成").Trim().Trim([char]34)
            $argList = @("-f", $file, "-c", $control, "--split", "auto", "--chunk-size", "180")
            if ($n -and $n -ne "0") { $argList += @("--update-ref", $n) }
            if (-not $refPath) {
                Write-Host "[参考音频] 正在生成多语调参考音频（更丰富的韵律采样）..." -ForegroundColor Yellow
                $refText = "你好，欢迎使用语音合成系统。今天将为您带来一段精彩的语音合成演示，让我们一起体验人工智能技术带来的便捷与乐趣。我们的技术正在不断进步，力求为您提供更加自然流畅的语音体验。"
                $refFile = Join-Path $desktop "ref_voice_upd.wav"
                & $PYTHON $SCRIPT -t $refText -c $control --cfg 3.0 --steps 20 -o $refFile
                $refPath = $refFile
            }
            if (-not (Test-Path $refPath)) {
                Write-Host "[错误] 参考音频不存在: $refPath" -ForegroundColor Red
                pause
                continue
            }
            $argList += @("--reference", $refPath)
            Write-Host ""
            Write-Host "[合成中] 长文本分段处理中（防漂移模式），请稍候..." -ForegroundColor Cyan
            & $PYTHON $SCRIPT @argList
            Write-Host ""
            Write-Host "[完成] 防漂移合成结束！" -ForegroundColor Green
            pause
        }
        default {
            if ($choice -match "^-") {
                Write-Host ""
                Write-Host "[高级模式] 透传参数执行..." -ForegroundColor Cyan
                $argList = $choice -split "\s+"
                & $PYTHON $SCRIPT @argList
                Write-Host ""
                Write-Host "[完成]" -ForegroundColor Green
            } else {
                Invoke-TTS -Text $choice
            }
            pause
        }
    }
}
