#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VoxCPM2 多音字 LoRA 训练启动器（项目级 glue 脚本）
=====================================================

本项目目标：用 2031 句（520 v19 + 1511 v23）「强制正确读音」的合成语音（音频即正确读音），
训练一个 VoxCPM2 的 LoRA，专门纠正多音字 (polyphone) 的读音。

本脚本只做三件事（重活交给仓库里已经过验证的训练入口，避免重复造轮子）：
  1) 把合成好的 wavs + 文本源，整理成 VoxCPM2 训练要求的 JSONL 清单
     （每行 {"text": <中文句>, "audio": <wav 绝对路径>}）；
  2) 生成一份 LoRA 训练 YAML 配置（结构 1:1 等价于仓库现有
     VoxCPM2Dist/app/Scripts/training/voxcpm_finetune_lora.yaml）；
  3) 调用仓库训练入口 train_voxcpm_finetune.py 启动训练。

为什么不复刻训练循环、而复用现有入口：
  train_voxcpm_finetune.py 已包含：
    - LoRA 接入（voxcpm.model.voxcpm2.LoRAConfig）
    - datasets 5.x 的 soundfile 离线补丁（绕开缺失的 ffmpeg / torchcodec，
      见该文件第 56-78 行的 _audio_decode_soundfile monkeypatch）
    - 余弦+warmup 调度、梯度累积、断点续训、SIGTERM/SIGINT 安全存盘等
  直接复用最稳，且保证超参与仓库其它 LoRA 实验一致。

运行环境（重要）：
  - 仅用系统 Python 3.12：C:/Users/000/AppData/Local/Programs/Python/Python312/python.exe
    （该环境已装 voxcpm 2.0.3 + torch 2.12.1+cu126，且 voxcpm.training.Accelerator
     是仓库自带精简版，不需要单独装 accelerate）
  - 不要动 Python 环境、不要 pip install；若训练入口报缺包，先对照下面「需验证项」。

用法：
  # 仅生成 manifest + yaml，并打印启动命令（默认，安全，不真正开训）：
  python train_lora.py

  # 真正开始训练（会拉起训练入口，长任务）：
  python train_lora.py --run

  # 只造数据、不写 yaml、不训练：
  python train_lora.py --build-only

  # 用其它文本字段 / 模型目录 / 轮数：
  python train_lora.py --text-field sentence --model-dir "D:/AI/.../VoxCPM2" --epochs 20

所有超参都是文件顶部常量，按需改；改完直接重跑即可。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

# ============================================================================
# 0) 路径常量（按你机器实际情况调整）
# ============================================================================
PROJECT_DIR = Path(__file__).resolve().parent                       # D:\AI\Build\多音字\
PYTHON_EXE = r"C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe"

# 仓库里已经过验证的训练入口（直接复用，不要另写训练循环）
ENTRYPOINT = Path(r"D:\AI\Build\VoxCPM2Dist\app\Scripts\training\train_voxcpm_finetune.py")

# VoxCPM2 模型 checkpoint 目录（含 config.json / model.safetensors / audiovae.pth / tokenizer.json）
# 与 ENTRYPOINT 同级仓库里自带一份；若你想把模型放到本项目下，复制后改这里即可。
MODEL_DIR = Path(r"D:\AI\Build\VoxCPM2Dist\app\model\openbmb\VoxCPM2")

# 数据源（模式A：直接吃合并清单的 audio 绝对路径）
WAVS_DIR = PROJECT_DIR / "wavs_v23"                                 # 仅 fallback 用；实际走下方 merged_manifest
WAVS_MANIFEST = PROJECT_DIR / "data" / "merged_manifest.jsonl"      # 合并清单：520 v19 + 1511 v23 = 2031 句（audio 绝对路径）
TEACHER_TEXT = PROJECT_DIR / "data" / "merged_teacher_text.jsonl"   # 合并教师文本（兜底）

# 产物
OUT_MANIFEST = PROJECT_DIR / "lora_train.jsonl"
OUT_VAL_MANIFEST = PROJECT_DIR / "lora_val.jsonl"
OUT_CONFIG = PROJECT_DIR / "lora_config.yaml"
OUT_DIR = PROJECT_DIR / "lora_output"

# ============================================================================
# 1) 可调超参（★ 这些都是建议默认值，务必按你的 GPU/数据量调整 ★）
# ============================================================================
TEXT_FIELD = "sentence"        # 与 wav 配对的文本字段。
WAV_INDEX_MODE = "idx"          # wav 文件名编号方案："idx"=用数据 idx 字段；"line"=用 0 基行号。
                                # ⚠️ 必须和「合成 agent 产 wav 时用的编号」一致；不要用混合回退（会错位）。
                                #    最稳妥：让合成 agent 直接产出 wavs_manifest.jsonl，本脚本优先吃它。
                                #   sentence    = 原始中文句（推荐：LoRA 学「句子→正确读音」）
                                #   cosy3_text  = CosyVoice 风格强制拼音标注 [f][á]...
                                #   it2_text    = 空格分隔拼音 fa2 mu4 zheng1 ...
                                # ⚠️ 必须与「合成这些 wav 时 TTS 实际吃掉的文本」一致，否则音文错位。
                                #    见本文件底部「需与合成 agent 确认」。

# ---- LoRA 配置（音频条件模型：改读音不动音色的关键就在这三个开关）----
LORA_R = 16                     # rank。仓库现有多音字 yaml 用 32；16 更轻、更不易过拟合。
LORA_ALPHA = 32                 # alpha（缩放）。通常 = 2*r 或 = r。
LORA_DROPOUT = 0.0
LORA_ENABLE_LM = True           # ✅ 改 base_lm + residual_lm（文本→语义/读音映射，多音字在这里修）
LORA_ENABLE_DIT = False         # ❌ 不动 feat_decoder（声学/音色）。音色在推理时由
                                #    reference_wav_path 克隆，与 LoRA 通道隔离；开了会改音色。
LORA_ENABLE_PROJ = False        # ❌ 投影层同理不动，保持跨音色兼容。

# ---- 优化器 / 调度 ----
LEARNING_RATE = 2e-4            # 团队建议 2e-4；仓库现有 yaml 用 1e-4，均可。LoRA 常用 1e-4~3e-4。
WEIGHT_DECAY = 0.01
WARMUP_STEPS = 100

# ---- 训练步数（入口是 step-based，不是 epoch-based；这里用轮数换算）----
EPOCHS = 12                     # 训练轮数。2031 句 / 有效批次 8 ≈ 254 步/轮 → 12 轮 ≈ 3047 步
BATCH_SIZE = 1                  # 物理批次（8GB VRAM 下调为 1 显存宽松、速度快 2~3x）
GRAD_ACCUM_STEPS = 8            # 梯度累积；有效批次 = BATCH_SIZE * GRAD_ACCUM_STEPS = 8
NUM_WORKERS = 2                 # Windows(spawn) 下每个 worker 是完整进程；RAM 紧可降到 1
MAX_BATCH_TOKENS = 8192         # 按估算 token 数过滤超长样本；0 关闭
MAX_GRAD_NORM = 1.0             # 梯度裁剪；0 = 关闭

# ---- 日志 / 存盘频率 ----
LOG_INTERVAL = 10
VALID_INTERVAL = 100            # 必须 <= 换算出的 num_iters
SAVE_INTERVAL = 200             # 每 200 步存一次检查点（800步共4个: 200/400/600/800）

VAL_SPLIT = 0.0                 # 验证集比例（0~0.5）。0 = 不分（单全集训练）。建议留 0.05~0.1。

# VoxCPM2 音频规格（写死，来自 model config.json；改了会 assert 失败）
SAMPLE_RATE = 16000             # AudioVAE 编码器输入采样率（V2=16000，必须一致）
OUT_SAMPLE_RATE = 48000         # 仅用于 TensorBoard 音频回放日志（解码器输出 48k）

# 损失权重（与仓库一致）
LAMBDAS = {"loss/diff": 1.0, "loss/stop": 1.0}


# ============================================================================
# 2) 清单构建：wavs + 文本 -> VoxCPM2 训练 JSONL
# ============================================================================
def _read_teacher_records() -> list[dict]:
    if not TEACHER_TEXT.exists():
        raise FileNotFoundError(f"找不到教师文本：{TEACHER_TEXT}")
    recs = []
    with TEACHER_TEXT.open("r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            recs.append(json.loads(ln))
    return recs


def _resolve_wav(wav_index: int) -> Path | None:
    """wav 命名 = {wav_index:04d}.wav。命名方案由 --wav-index-mode 决定，不做混合回退。"""
    cand = WAVS_DIR / f"{wav_index:04d}.wav"
    return cand if cand.exists() else None


def build_manifest(text_field: str = TEXT_FIELD, val_split: float = VAL_SPLIT) -> tuple[int, int]:
    """
    返回 (train_count, val_count)。
    优先吃合成 agent 写的 wavs_manifest.jsonl；否则按 wavs/ + 教师文本自动配对。
    """
    # ⚠️ 安全护栏：cosy3_text / it2_text 是「喂给 TTS 强制读音」的控制串（音素/拼音标注），
    # 不是目标中文文本。VoxCPM2 训练 text 必须是原始中文 sentence，否则模型会学去输出
    # 音素/拼音标注，整个训练就错了。synth-batch-ref / env-fix-ab 已确认这一点。
    _CONTROL_STRING_FIELDS = {"cosy3_text", "it2_text"}
    if text_field in _CONTROL_STRING_FIELDS:
        print(
            f"[警告] --text-field={text_field} 是 TTS 的强制读音控制串（音素/拼音标注），"
            f"不是目标中文文本！VoxCPM2 训练 text 必须是原始中文 sentence。\n"
            f"        继续会用错文本训练。建议去掉 --text-field（默认 sentence）或显式 --text-field sentence。"
        )

    if not WAVS_DIR.exists():
        raise FileNotFoundError(
            f"找不到 wavs 目录：{WAVS_DIR}\n"
            f"请先由合成 agent 产出 wav（wavs/{{index:04d}}.wav）和 wavs_manifest.jsonl，再跑本脚本。"
        )

    records: list[dict] = []

    if WAVS_MANIFEST.exists():
        # ---- 模式 A：直接消费合成 agent 的清单 ----
        # 真实 schema（synth-batch-ref 产出）：每行一个 JSON 对象，含
        #   index:int（= v19_teacher_text.jsonl 的 idx，1-based；wav 文件名用它编码）
        #   wav:str（相对项目根 D:\AI\Build\多音字 的路径，如 "wavs/0001.wav"）
        #   sentence:str（原始中文句 = 训练目标文本）
        #   model / sample_rate / char / pinyin_mark / pinyin_t3 / cosy3_text / it2_text（元数据/控制串）
        # 选定主引擎 = IndexTTS2（env-fix-ab A/B 结论），model 字段为 "it2"；
        # it2 路径用 data/v19_teacher_text.clean.jsonl（489 行，30 个坏 it2 行已剔除）。
        # 训练文本恒为 sentence（与引擎无关）。映射：text = sentence；audio = PROJECT_DIR / wav。
        # 文件名编号 = {idx:04d}（1-based），每行 idx 唯一，与 0-based 行号无关。
        print(f"[manifest] 使用合成清单：{WAVS_MANIFEST}")
        with WAVS_MANIFEST.open("r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                row = json.loads(ln)
                # 兼容多种字段名：text / sentence / src；audio / wav / path
                text = (row.get("text") or row.get("sentence") or row.get("src") or "").strip()
                audio = (row.get("audio") or row.get("wav") or row.get("path") or "").strip()
                if not text and text_field in row:
                    text = str(row[text_field]).strip()
                if not audio:
                    continue
                ap = Path(audio)
                if not ap.is_absolute():
                    # 清单里的相对路径是相对 PROJECT_DIR 的（如 "wavs/0001.wav"），
                    # 不是相对 WAVS_DIR，否则会变成 PROJECT/wavs/wavs/0001.wav 而找不到。
                    ap = (PROJECT_DIR / ap).resolve()
                if not ap.exists():
                    print(f"[manifest][warn] 音频缺失，跳过：{ap}")
                    continue
                records.append({"text": text, "audio": str(ap)})
    else:
        # ---- 模式 B：按 wavs/ + 教师文本自动配对 ----
        print(f"[manifest] 未找到 {WAVS_MANIFEST.name}，按 wavs/ + {TEACHER_TEXT.name} 自动配对")
        teacher = _read_teacher_records()
        for i, rec in enumerate(teacher):
            # wav 编号方案：idx = 用数据里的 idx 字段；line = 用 0 基行号
            wav_index = int(rec["idx"]) if WAV_INDEX_MODE == "idx" and "idx" in rec else i
            text = str(rec.get(text_field, "")).strip()
            if not text:
                print(f"[manifest][warn] 第 {i} 行缺文本字段 '{text_field}'，跳过")
                continue
            wav = _resolve_wav(wav_index)
            if wav is None:
                print(f"[manifest][warn] 缺 wav（{WAV_INDEX_MODE}={wav_index}, line={i}），跳过：{text[:20]}...")
                continue
            records.append({"text": text, "audio": str(wav.resolve())})

    if not records:
        raise RuntimeError("有效 (text, audio) 样本为 0，未生成任何清单。")

    # 划分验证集（可选）
    val_records = []
    if val_split > 0:
        n_val = max(1, int(len(records) * val_split))
        n_val = min(n_val, len(records) - 1)
        val_records = records[:n_val]
        records = records[n_val:]

    OUT_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    with OUT_MANIFEST.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if val_records:
        with OUT_VAL_MANIFEST.open("w", encoding="utf-8") as f:
            for r in val_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[manifest] 训练 {len(records)} 条 -> {OUT_MANIFEST}")
        print(f"[manifest] 验证 {len(val_records)} 条 -> {OUT_VAL_MANIFEST}")
    else:
        print(f"[manifest] 共 {len(records)} 条（无验证集）-> {OUT_MANIFEST}")

    return len(records), len(val_records)


# ============================================================================
# 3) 写出 LoRA 训练 YAML（结构 1:1 等价于仓库 voxcpm_finetune_lora.yaml）
# ============================================================================
def write_config(num_train_samples: int) -> Path:
    effective_batch = max(1, BATCH_SIZE * GRAD_ACCUM_STEPS)
    num_iters = max(1, math.ceil(EPOCHS * num_train_samples / effective_batch))
    max_steps = num_iters  # 调度步数 = 训练步数

    # 频率不能大于总步数，否则永远不触发
    valid_interval = min(VALID_INTERVAL, max(1, num_iters))
    save_interval = min(SAVE_INTERVAL, max(1, num_iters))

    lines = []
    lines.append("# VoxCPM2 多音字 LoRA 训练配置（由 train_lora.py 生成）")
    lines.append(f"# 训练样本数={num_train_samples}，有效批次={effective_batch}，"
                 f"约 {EPOCHS} 轮 -> num_iters={num_iters}")
    lines.append("")
    lines.append(f"pretrained_path: {MODEL_DIR}")
    lines.append(f"train_manifest: {OUT_MANIFEST}")
    lines.append(f"val_manifest: {OUT_VAL_MANIFEST if OUT_VAL_MANIFEST.exists() else ''}")
    lines.append(f"sample_rate: {SAMPLE_RATE}        # 必须与 AudioVAE 编码器输入采样率一致（V2=16000）")
    lines.append(f"out_sample_rate: {OUT_SAMPLE_RATE}    # 仅用于 TensorBoard 音频回放")
    lines.append(f"batch_size: {BATCH_SIZE}")
    lines.append(f"grad_accum_steps: {GRAD_ACCUM_STEPS}       # 有效批次 = {effective_batch}")
    lines.append(f"num_workers: {NUM_WORKERS}")
    lines.append(f"num_iters: {num_iters}")
    lines.append(f"log_interval: {LOG_INTERVAL}")
    lines.append(f"valid_interval: {valid_interval}")
    lines.append(f"save_interval: {save_interval}")
    lines.append(f"learning_rate: {LEARNING_RATE}")
    lines.append(f"weight_decay: {WEIGHT_DECAY}")
    lines.append(f"warmup_steps: {WARMUP_STEPS}")
    lines.append(f"max_steps: {max_steps}")
    lines.append(f"max_batch_tokens: {MAX_BATCH_TOKENS}")
    lines.append(f"max_grad_norm: {MAX_GRAD_NORM}")
    lines.append(f"save_path: {OUT_DIR}")
    lines.append(f"tensorboard: {OUT_DIR / 'logs'}")
    lines.append("lambdas:")
    for k, v in LAMBDAS.items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    lines.append("# LoRA 配置（推理端必须用本目录保存的 lora_config.json 重建，保证 r/alpha 一致）")
    lines.append("# ⚠️ 多音字修复：只改读音、不动音色！")
    lines.append("#   enable_lm=true  : 改 base_lm + residual_lm（文本→语义/读音映射，多音字在这里）")
    lines.append("#   enable_dit=false: 不动 feat_decoder（声学/音色）。音色由推理参考音克隆，与 LoRA 隔离")
    lines.append("#   enable_proj=false: 投影层同理不动，保持跨音色兼容")
    lines.append("lora:")
    lines.append(f"  enable_lm: {str(LORA_ENABLE_LM).lower()}")
    lines.append(f"  enable_dit: {str(LORA_ENABLE_DIT).lower()}")
    lines.append(f"  enable_proj: {str(LORA_ENABLE_PROJ).lower()}")
    lines.append(f"  r: {LORA_R}")
    lines.append(f"  alpha: {LORA_ALPHA}")
    lines.append(f"  dropout: {LORA_DROPOUT}")

    OUT_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    OUT_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[config] 写出 -> {OUT_CONFIG}")
    return OUT_CONFIG


# ============================================================================
# 4) 启动命令
# ============================================================================
def launch_command(config_path: Path) -> list[str]:
    return [PYTHON_EXE, str(ENTRYPOINT), "--config_path", str(config_path)]


def main():
    global MODEL_DIR, EPOCHS, TEXT_FIELD, WAV_INDEX_MODE
    ap = argparse.ArgumentParser(description="VoxCPM2 多音字 LoRA 训练启动器")
    ap.add_argument("--text-field", default=TEXT_FIELD, help="与 wav 配对的文本字段（默认 sentence）")
    ap.add_argument("--wav-index-mode", default=WAV_INDEX_MODE, choices=["idx", "line"],
                    help="无 manifest 时，wav 文件名编号方案：idx=用数据 idx 字段；line=用 0 基行号")
    ap.add_argument("--model-dir", default=str(MODEL_DIR), help="VoxCPM2 模型目录")
    ap.add_argument("--epochs", type=int, default=EPOCHS, help="训练轮数（换算成 num_iters）")
    ap.add_argument("--run", action="store_true", help="真正启动训练（默认只造数据+打印命令）")
    ap.add_argument("--build-only", action="store_true", help="只造 manifest，不写 yaml、不训练")
    args = ap.parse_args()

    MODEL_DIR = Path(args.model_dir)
    EPOCHS = args.epochs
    TEXT_FIELD = args.text_field
    WAV_INDEX_MODE = args.wav_index_mode

    # 0) 前置检查
    if not MODEL_DIR.exists():
        sys.exit(f"[错误] 模型目录不存在：{MODEL_DIR}\n请确认 VoxCPM2 checkpoint 已就位（含 config.json）。")
    if not ENTRYPOINT.exists():
        sys.exit(f"[错误] 训练入口不存在：{ENTRYPOINT}")
    if not Path(PYTHON_EXE).exists():
        sys.exit(f"[错误] 指定的 Python 不存在：{PYTHON_EXE}\n请用系统 Python 3.12（含 voxcpm 2.0.3）。")

    # 1) 造清单
    n_train, n_val = build_manifest(text_field=TEXT_FIELD)

    if args.build_only:
        print("[done] 仅生成 manifest，结束。")
        return

    # 2) 写 yaml
    cfg = write_config(n_train)

    # 3) 打印 / 启动
    cmd = launch_command(cfg)
    print("\n==================== 启动命令 ====================")
    print(" ".join(cmd))
    print("==================================================\n")
    print("训练产物（LoRA 权重 + lora_config.json）将写入：")
    print(f"  {OUT_DIR}")
    print("提示：Ctrl+C / SIGTERM 会安全存盘到 latest/ 并可续训。")

    if args.run:
        print("\n[run] 启动训练中（长任务，日志见 stdout 与 TensorBoard）...")
        # 用项目目录作为 CWD，避免相对路径歧义
        rc = subprocess.run(cmd, cwd=str(PROJECT_DIR))
        sys.exit(rc.returncode)
    else:
        print("[dry-run] 未加 --run，未真正开训。确认无误后加 --run 启动。")


if __name__ == "__main__":
    main()
