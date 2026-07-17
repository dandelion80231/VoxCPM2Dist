#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多音字 LoRA 验证脚本：用（带/不带）LoRA 的模型合成难读句子，输出 wav 供人工试听。

对比用法：
  1) 基线（无 LoRA）：
     python verify_lora.py --model-dir <VoxCPM2 模型目录> --out-dir verify_baseline
  2) 挂载 LoRA：
     python verify_lora.py --model-dir <VoxCPM2 模型目录> \
         --lora-dir <训练产出的 step_XXXXXXX 目录> --out-dir verify_lora

然后听 verify_baseline/ 与 verify_lora/ 下同名 wav，确认多音字是否读对。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np


def save_wav(path: Path, wav: np.ndarray, sr: int = 48000):
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = np.asarray(wav, dtype=np.float32).flatten()
    # 归一化到 [-0.95, 0.95] 避免削波
    peak = float(np.abs(wav).max()) if wav.size else 0.0
    if peak > 0:
        wav = wav / peak * 0.95
    try:
        import soundfile as sf
        sf.write(str(path), wav, sr)
        return
    except Exception:
        pass
    try:
        import torch
        import torchaudio
        torchaudio.save(str(path), torch.from_numpy(wav).unsqueeze(0), sr)
        return
    except Exception:
        pass
    try:
        from scipy.io import wavfile
        wavfile.write(str(path), sr, (wav * 32767).astype(np.int16))
        return
    except Exception:
        pass
    # 最后兜底：写 raw float32
    wav.astype(np.float32).tofile(str(path.with_suffix(".f32raw")))


# 难读多音字测试句（每句都会触发至少一个常见异读字）
TEST_SENTENCES = [
    ("hang_xing", "一行行行行，每行行距都不相同。"),
    ("bank",      "银行行长去银行了，办理业务。"),
    ("music",     "他爱好音乐，喜欢弹钢琴。"),
    ("weight",    "这个重量很重，重新称一下更准确。"),
    ("huan",      "他还钱了，还是没还，我得去问。"),
    ("hao",       "老师好，这题好难，我好想知道。"),
    ("fa",        "发现头发掉了，发现新大陆很兴奋。"),
    ("liang",     "电量不足，量一量体温再决定。"),
    ("jia",       "假如放假，我会去假山旁散步。"),
    ("xue",       "鲜血直流，他血债血偿的誓言惊人。"),
    ("zhong",     "重要的事情，他又重做了一遍。"),
    ("chang",     "长大之后，他常常去长长的小巷。"),
    ("le",        "得了大奖，还得继续努力。"),
    ("ying",      "应该应允，他响应了号召。"),
    ("tiao",      "空调调低了温度，他调整了坐姿。"),
    ("dan",       "子弹上膛，他弹琴的手指很灵活。"),
    ("bei",       "背着书包，他背对着墙。"),
    ("zhuan",     "转动转盘，他转身离开了。"),
    ("she",       "宿舍里，他舍近求远。"),
    ("xing",      "兴奋之下，他兴趣更浓了。"),
]


def main():
    ap = argparse.ArgumentParser(description="多音字 LoRA 验证")
    ap.add_argument("--model-dir", required=True, help="VoxCPM2 模型目录")
    ap.add_argument("--lora-dir", default="", help="训练产出的 step_XXXXXXX 目录（可选，不填=基线）")
    ap.add_argument("--out-dir", default="verify_out")
    ap.add_argument("--device", default="")
    ap.add_argument("--max-len", type=int, default=600)
    args = ap.parse_args()

    from voxcpm import VoxCPM
    init_kwargs = dict(
        model_path=args.model_dir if os.path.isdir(args.model_dir) else None,
        load_denoiser=False,
        optimize=False,
        device=args.device or None,
    )
    # from_pretrained 接受 model_path 作为第一个位置参数（同 local path）
    if args.lora_dir:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lora_helper import resolve_lora
        init_kwargs["model_path"] = args.model_dir
        init_kwargs = resolve_lora(init_kwargs, args.lora_dir)

    print(f"[verify] 加载模型: {args.model_dir}  lora={args.lora_dir or '无'}")
    model = VoxCPM.from_pretrained(args.model_dir, **{k: v for k, v in init_kwargs.items()
                                                      if k in ("load_denoiser", "zipenhancer_model_id",
                                                               "optimize", "device", "lora_config",
                                                               "lora_weights_path")})

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for key, sent in TEST_SENTENCES:
        try:
            wav = model.generate(text=sent, max_len=args.max_len)
            save_wav(out / f"{key}.wav", wav, sr=48000)
            print(f"[ok] {key}: {sent[:24]}...")
        except Exception as e:
            print(f"[失败] {key}: {e}")
    print(f"[完成] 输出目录：{out.resolve()}")


if __name__ == "__main__":
    main()
