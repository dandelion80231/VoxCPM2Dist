#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多音字 LoRA 听测(自定义句表版)
读 --sentences-file(JSON: [[key, text], ...]), 用 (带/不带) LoRA 的模型合成, 输出 wav。
对比用法:
  python verify_listen.py --model-dir <MODEL> --sentences-file s.json --out-dir verify_listen_base
  python verify_listen.py --model-dir <MODEL> --lora-dir <step_XXXX> --sentences-file s.json --out-dir verify_listen_0400
"""
import argparse
import os
import json
import sys
from pathlib import Path

import numpy as np


def save_wav(path: Path, wav: np.ndarray, sr: int = 48000):
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = np.asarray(wav, dtype=np.float32).flatten()
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
    wav.astype(np.float32).tofile(str(path.with_suffix(".f32raw")))


def main():
    ap = argparse.ArgumentParser(description="多音字 LoRA 验证(自定义句表)")
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--lora-dir", default="", help="step_XXXX 目录(可选, 不填=基线)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--sentences-file", required=True, help="JSON: [[key, text], ...]")
    ap.add_argument("--device", default="")
    ap.add_argument("--max-len", type=int, default=600)
    ap.add_argument("--ref", default=None,
                    help="参考音 wav(可选). 不填则默认音色, 仍可正常出声.")
    args = ap.parse_args()

    with open(args.sentences_file, encoding="utf-8") as f:
        TEST_SENTENCES = json.load(f)

    from voxcpm import VoxCPM
    init_kwargs = dict(
        model_path=args.model_dir if os.path.isdir(args.model_dir) else None,
        load_denoiser=False,
        optimize=False,
        device=args.device or None,
    )
    if args.lora_dir:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lora_helper import resolve_lora
        init_kwargs["model_path"] = args.model_dir
        init_kwargs = resolve_lora(init_kwargs, args.lora_dir)

    print(f"[verify_listen] 加载模型: {args.model_dir}  lora={args.lora_dir or '无'}  n={len(TEST_SENTENCES)}")
    model = VoxCPM.from_pretrained(args.model_dir, **{k: v for k, v in init_kwargs.items()
                                                      if k in ("load_denoiser", "zipenhancer_model_id",
                                                               "optimize", "device", "lora_config",
                                                               "lora_weights_path")})

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for key, sent in TEST_SENTENCES:
        try:
            gen_kwargs = dict(text=sent, max_len=args.max_len)
            if args.ref:
                gen_kwargs["reference_wav_path"] = args.ref
            wav = model.generate(**gen_kwargs)
            save_wav(out / f"{key}.wav", wav, sr=48000)
            print(f"[ok] {key}: {sent[:24]}...")
        except Exception as e:
            print(f"[失败] {key}: {e}")
    print(f"[完成] 输出目录：{out.resolve()}")


if __name__ == "__main__":
    main()
