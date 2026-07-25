#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立基准集推理驱动（复刻 verify_lora.py 的加载逻辑, 句子源改为基准 json）

一次调用 = 一个模型(base 或 挂载 LoRA), 把基准 json 里所有句合成到 out-dir。
BEFORE/AFTER 对比需跑两次:
  python_cuda\\python.exe run_diag_infer.py --model-dir MODEL --sentences data/diag_benchmark.json --out-dir diag_base
  python_cuda\\python.exe run_diag_infer.py --model-dir MODEL --lora-dir lora_output\\step_0001200 --sentences data/diag_benchmark.json --out-dir diag_lora1200

注意: 必须用 VoxCPM2Dist 的 python_cuda 环境(含 voxcpm + CUDA), 不是系统 python。
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(description="独立基准集推理")
    ap.add_argument("--model-dir", required=True, help="VoxCPM2 模型目录")
    ap.add_argument("--lora-dir", default="", help="step_XXXXXXX 目录(可选, 不填=基线)")
    ap.add_argument("--sentences", required=True, help="基准 json (gen_diag_testset.py 产出)")
    ap.add_argument("--out-dir", default="diag_out")
    ap.add_argument("--device", default="")
    ap.add_argument("--max-len", type=int, default=600)
    ap.add_argument("--ref", default=None, help="参考音 wav(可选, 共用音色便于对比)")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 句(冒烟测试用)")
    args = ap.parse_args()

    bench = json.loads(Path(args.sentences).read_text(encoding="utf-8"))
    if args.limit:
        bench = bench[: args.limit]

    from voxcpm import VoxCPM
    init_kwargs = dict(
        model_path=args.model_dir if Path(args.model_dir).is_dir() else None,
        load_denoiser=False, optimize=False, device=args.device or None,
    )
    if args.lora_dir:
        # lora_helper 在 VoxCPM2Dist/app/Scripts/ 下
        scripts_dir = Path(args.model_dir).resolve().parent.parent.parent / "Scripts"
        sys.path.insert(0, str(scripts_dir))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lora_helper import resolve_lora
        init_kwargs = resolve_lora(init_kwargs, args.lora_dir)

    print(f"[diag] 加载模型: {args.model_dir}  lora={args.lora_dir or '无'}  句数={len(bench)}",
          flush=True)
    model = VoxCPM.from_pretrained(
        args.model_dir,
        **{k: v for k, v in init_kwargs.items()
           if k in ("load_denoiser", "zipenhancer_model_id", "optimize",
                    "device", "lora_config", "lora_weights_path")})

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for item in bench:
        key = item["key"]
        sent = item["sentence"]
        try:
            gen_kwargs = dict(text=sent, max_len=args.max_len)
            if args.ref:
                gen_kwargs["reference_wav_path"] = args.ref
            wav = model.generate(**gen_kwargs)
            import numpy as np
            wav = np.asarray(wav, dtype=np.float32).flatten()
            peak = float(np.abs(wav).max()) if wav.size else 0.0
            if peak > 0:
                wav = wav / peak * 0.95
            import soundfile as sf
            sf.write(str(out / f"{key}.wav"), wav, 48000)
            print(f"[ok] {key}: {sent[:20]}...", flush=True)
        except Exception as e:
            print(f"[失败] {key}: {e}", flush=True)
    print(f"[完成] 输出目录：{out.resolve()}", flush=True)


if __name__ == "__main__":
    main()
