#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专项测试：用 base / LoRA 各自合成「行」字绕口令，输出 wav 供人耳对比读音。

用法：
  python verify_xing.py --model-dir <VoxCPM2 目录> --out verify_xing_baseline
  python verify_xing.py --model-dir <VoxCPM2 目录> --lora-dir lora_output/step_0000500 --out verify_xing_lora
"""
from __future__ import annotations
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_lora import save_wav  # 复用保存逻辑

# 行 字绕口令：行 须在 xíng(行/能行) 与 háng(行业/一行/内行) 间正确切换
TWISTER = ("人要是行，干一行行一行，一行行，行行行。"
           "行行行，干哪行都行。"
           "要是不行，干一行不行一行，一行不行行行不行。"
           "行行不行，干哪行都不行 。"
           "要想行行行，首先一行行。"
           "成为行业内的内行，行行成内行")


def main():
    ap = argparse.ArgumentParser(description="行字绕口令专项验证")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--lora-dir", default="")
    ap.add_argument("--ref", default=None,
                    help="参考音 wav（可选）。不填用默认音色。填了则 base/LoRA 共用同音色便于对比。")
    ap.add_argument("--out", default="verify_xing_out")
    ap.add_argument("--device", default="")
    ap.add_argument("--max-len", type=int, default=1200)
    args = ap.parse_args()

    from voxcpm import VoxCPM
    init_kwargs = dict(
        model_path=args.model_dir if os.path.isdir(args.model_dir) else None,
        load_denoiser=False,
        optimize=False,
        device=args.device or None,
    )
    if args.lora_dir:
        from lora_helper import resolve_lora
        init_kwargs["model_path"] = args.model_dir
        init_kwargs = resolve_lora(init_kwargs, args.lora_dir)

    model = VoxCPM.from_pretrained(
        args.model_dir,
        **{k: v for k, v in init_kwargs.items()
           if k in ("load_denoiser", "zipenhancer_model_id", "optimize",
                    "device", "lora_config", "lora_weights_path")})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    gen_kwargs = dict(text=TWISTER, max_len=args.max_len)
    if args.ref:
        gen_kwargs["reference_wav_path"] = args.ref
    print(f"[verify_xing] 合成中（{'LoRA=' + args.lora_dir if args.lora_dir else '基线无LoRA'}）...")
    wav = model.generate(**gen_kwargs)
    save_wav(out / "xing_twister.wav", wav, sr=48000)
    print(f"[完成] 输出：{(out / 'xing_twister.wav').resolve()}")


if __name__ == "__main__":
    main()
