# 多音字 LoRA 训练启动器（使用离线 python_cuda）
# 用法：在 GPU 机上 `powershell -ExecutionPolicy Bypass -File train_polyphone_lora.ps1`
# 前置：训练清单已就位（二选一）
#   A. 免录制：python training\bootstrap_lora_audio.py --ref 参考音.wav  -> Scripts\lora_audio\train.jsonl
#   B. 手动录制：python prepare_polyphone_lora_data.py --sentences polyphone_sentences.txt --audio-dir 录音目录
#       -> Scripts\lora_data\train.jsonl（此时需把 yaml 的 train_manifest 改回 ../lora_data/train.jsonl）
# 可选：`..\..\python_cuda\python.exe -m pip install tensorboardX matplotlib` 启用训练日志/频谱图
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $scriptDir '..\python_cuda\python.exe'
if (-not (Test-Path $py)) { Write-Host "未找到离线 python: $py"; exit 1 }
$trainScript = Join-Path $scriptDir 'training\train_voxcpm_finetune.py'
$cfg = Join-Path $scriptDir 'training\voxcpm_finetune_lora.yaml'
Write-Host "=== 开始多音字 LoRA 训练 ==="
Write-Host "python : $py"
Write-Host "script : $trainScript"
Write-Host "config : $cfg"
& $py $trainScript --config_path $cfg
Write-Host "=== 训练结束（退出码 $LASTEXITCODE）==="
