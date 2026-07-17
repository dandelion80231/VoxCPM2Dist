#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多音字 LoRA 训练数据集预处理工具。

把「句子清单 + 录音」整理成 voxcpm 训练脚本要求的 JSON 清单（text / audio 两列）。
训练脚本（training/train_voxcpm_finetune.py）通过
  load_audio_text_datasets(train_manifest=...)
读取该清单（HuggingFace datasets 的 json loader），audio 列会被 Audio(16000) 加载
（非 16k 会自动重采样）。

支持三种输入模式：
  1) --sentences sentences.txt --audio-dir recordings/
       句子文件每行一句；recordings/ 下按行序命名 001.wav, 002.wav ...（或 1.wav ...）
  2) --manifest manifest.csv         （含 text,audio 两列；audio 为 wav 路径）
  3) --sentences sentences.txt --audio-glob "recordings/*.wav"
       句子按文件名字母序与录音一一对应

输出：
  --out 指定的 jsonl（默认 ../lora_data/train.jsonl，相对本脚本在 Scripts/ 下时即 Scripts/lora_data/）

依赖：仅标准库 + （可选）soundfile/torchaudio 用于打印时长（缺失时跳过时长校验）。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path


def _natural_key(name: str):
    """用于按 001.wav/002.wav 或 1.wav/2.wav 自然排序。"""
    import re
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _audio_duration(path: Path) -> float | None:
    """尽量返回秒数；无依赖时返回 None。"""
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return float(info.frames) / float(info.samplerate)
    except Exception:
        pass
    try:
        import torchaudio
        info = torchaudio.info(str(path))
        return float(info.num_frames) / float(info.sample_rate)
    except Exception:
        pass
    return None


def _collect_by_dir(sentences: list[str], audio_dir: Path) -> list[tuple[str, Path]]:
    wavs = sorted(
        [p for p in audio_dir.iterdir() if p.suffix.lower() in (".wav", ".flac", ".mp3")],
        key=lambda p: _natural_key(p.name),
    )
    if len(wavs) != len(sentences):
        print(f"[警告] 句子数({len(sentences)}) 与录音数({len(wavs)}) 不一致，将按较短者截断。")
    pairs = []
    for i, sent in enumerate(sentences):
        if i >= len(wavs):
            break
        pairs.append((sent, wavs[i]))
    return pairs


def _collect_by_glob(sentences: list[str], glob: str) -> list[tuple[str, Path]]:
    import glob as _glob
    wavs = sorted(_glob.glob(glob), key=lambda p: _natural_key(os.path.basename(p)))
    wavs = [Path(p) for p in wavs]
    if len(wavs) != len(sentences):
        print(f"[警告] 句子数({len(sentences)}) 与录音数({len(wavs)}) 不一致，将按较短者截断。")
    pairs = [(sent, wavs[i]) for i, sent in enumerate(sentences) if i < len(wavs)]
    return pairs


def _collect_by_manifest(manifest: Path) -> list[tuple[str, Path]]:
    pairs = []
    with manifest.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            text = (row.get("text") or "").strip()
            audio = (row.get("audio") or "").strip()
            if not text or not audio:
                continue
            pairs.append((text, Path(audio)))
    return pairs


def build(sentences_path: Path | None, audio_dir: Path | None, audio_glob: str | None,
         manifest: Path | None, out: Path, val_split: float):
    if manifest is not None:
        pairs = _collect_by_manifest(manifest)
    else:
        if sentences_path is None:
            print("[错误] 必须提供 --sentences 或 --manifest。")
            sys.exit(2)
        sentences = [ln.strip() for ln in sentences_path.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.strip().startswith("#")]
        if audio_dir is not None:
            pairs = _collect_by_dir(sentences, audio_dir)
        elif audio_glob:
            pairs = _collect_by_glob(sentences, audio_glob)
        else:
            print("[错误] 提供了 --sentences，还需 --audio-dir 或 --audio-glob。")
            sys.exit(2)

    if not pairs:
        print("[错误] 未收集到任何 (text, audio) 样本。")
        sys.exit(2)

    # 校验音频存在 + 时长
    records = []
    total_dur = 0.0
    missing = 0
    for text, ap in pairs:
        if not ap.exists():
            print(f"[缺失] 音频不存在：{ap}  （句子：{text[:20]}...）")
            missing += 1
            continue
        dur = _audio_duration(ap)
        if dur is not None:
            total_dur += dur
        records.append({"text": text, "audio": str(ap)})

    if not records:
        print("[错误] 有效样本为 0（音频全部缺失）。")
        sys.exit(2)

    # 划分 val（可选）
    val_records = []
    if val_split > 0:
        import math
        n_val = max(1, int(len(records) * val_split))
        n_val = min(n_val, len(records) - 1)
        val_records = records[:n_val]
        records = records[n_val:]

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    if val_records:
        val_out = out.parent / "val.jsonl"
        with val_out.open("w", encoding="utf-8") as f:
            for r in val_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"[完成] 训练 {len(records)} 条 -> {out}")
        print(f"[完成] 验证 {len(val_records)} 条 -> {val_out}")
    else:
        print(f"[完成] 共 {len(records)} 条 -> {out}")

    print(f"[统计] 有效样本 {len(records)}，缺失 {missing}，"
          f"总时长约 {total_dur/60.0:.1f} 分钟（无依赖时显示 0）。")
    if total_dur > 0 and total_dur < 300:
        print("[提示] 官方建议至少 5–10 分钟音频；当前偏少，LoRA 效果可能有限。")


def main():
    ap = argparse.ArgumentParser(description="多音字 LoRA 数据集预处理")
    ap.add_argument("--sentences", type=Path, help="句子清单（每行一句，# 开头为注释）")
    ap.add_argument("--audio-dir", type=Path, help="录音目录，文件按 001.wav/002.wav 自然排序对应句子")
    ap.add_argument("--audio-glob", type=str, help="录音通配符，如 recordings/*.wav")
    ap.add_argument("--manifest", type=Path, help="CSV 清单（text,audio 两列）")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "lora_data" / "train.jsonl")
    ap.add_argument("--val-split", type=float, default=0.0, help="验证集比例（0–0.5），默认 0（不分）")
    args = ap.parse_args()
    build(args.sentences, args.audio_dir, args.audio_glob, args.manifest, args.out, args.val_split)


if __name__ == "__main__":
    main()
