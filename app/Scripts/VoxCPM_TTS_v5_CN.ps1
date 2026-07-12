# VoxCPM2 TTS 启动器 v5.0 中文版
#
# [v5.0 改进]
#   - 方式1/2：预生成多语调参考音频（cfg 3.0 / steps 20）
#   - 音色几乎无漂移（与用户验证方法一致）
#   - 默认 80ms 交叉淡入淡出

$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$APP_DIR   = Split-Path -Parent $SCRIPT_DIR
$PYTHON    = Join-Path $APP_DIR "python_cuda\python.exe"
$SCRIPT    = Join-Path $SCRIPT_DIR "voxcpm_tts_v5_longtext.py"

# ---------- 模型/输出路径（离线优先使用随包模型） ----------
$env:VOXCPM_MODEL_DIR = Join-Path $APP_DIR "model\openbmb\VoxCPM2"
if (-not $env:HF_ENDPOINT) { $env:HF_ENDPOINT = "https://hf-mirror.com" }
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

function Show-Banner {
    Clear-Host
    Write-Host "                                          " -ForegroundColor Cyan
    Write-Host " V   V  OOO  X   X  CCC  PPPP  M   M  222  " -ForegroundColor Cyan
    Write-Host " V   V O   O  X X  C   C P   P MM MM 2   2 " -ForegroundColor Cyan
    Write-Host " V   V O   O   X   C     PPPP  M M M   22  " -ForegroundColor Cyan
    Write-Host "  V V  O   O  X X  C   C P     M   M  2    " -ForegroundColor Cyan
    Write-Host "   V    OOO  X   X  CCC  P     M   M 22222 " -ForegroundColor Cyan
    Write-Host "+==========================================+" -ForegroundColor Cyan
    Write-Host "|                                          |" -ForegroundColor Cyan
    Write-Host "|   VoxCPM2 语音合成工具 v5.0 音色统一版   |" -ForegroundColor Cyan
    Write-Host "|                                          |" -ForegroundColor Cyan
    Write-Host "|   长文本配音 / 音色一致 / 交叉淡入淡出   |" -ForegroundColor Cyan
    Write-Host "|                                          |" -ForegroundColor Cyan
    Write-Host "+==========================================+" -ForegroundColor Cyan
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
    Write-Host "  10 - 长文本文件配音（方式1：自播种，自动生成参考）"
    Write-Host "  11 - 长文本文件配音（方式3：逐段音色设计）"
    Write-Host "  12 - 生成参考音频（用于长文本统一音色）"
    Write-Host ""
    Write-Host "[其他]" -ForegroundColor Yellow
    Write-Host "  13 - 克隆已有音频（Controllable Clone）"
    Write-Host "  14 - 终极克隆（Ultimate Clone）"
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
            $argList += $ExtraArgs -split " "
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
        $argList += $ExtraArgs -split " "
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
        "1" { "自播种克隆（自动生成参考）" }
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
            # 两步法（与用户测试确认的方法一致）：
            # 第1步：预生成专用参考音频（多句长文本，丰富语调/韵律采样）
            # 第2步：用这个固定参考克隆全文（音色最稳定，几乎无漂移）
            Write-Host ""
            Write-Host "[自播种] 第1步：正在生成专用参考音频（多语调长句）..." -ForegroundColor Cyan
            $refText = "你好，欢迎使用语音合成系统。今天将为您带来一段精彩的语音合成演示，让我们一起体验人工智能技术带来的便捷与乐趣。我们的技术正在不断进步，力求为您提供更加自然流畅的语音体验。"
            $refFile = Join-Path $desktop "voxcpm_seed_ref.wav"
            # 参考音频用更高 cfg/steps 确保音色一致性
            & $PYTHON $SCRIPT -t $refText -c $Control --cfg 3.0 --steps 20 -o $refFile
            if (-not (Test-Path $refFile)) {
                Write-Host "[错误] 参考音频生成失败！" -ForegroundColor Red
                return
            }
            Write-Host "[自播种] 参考音频已保存: $refFile" -ForegroundColor Green
            # 第2步：全文用固定参考克隆
            $argList += @("--reference", $refFile, "--crossfade", "80")
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
