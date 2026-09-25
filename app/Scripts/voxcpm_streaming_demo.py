# -*- coding: utf-8 -*-
"""
voxcpm_streaming_demo.py — VoxCPM2 流式合成演示（官方特性: 实时流式输出）

功能:
- 使用引擎级 model.generate_streaming() 逐块生成音频（NVIDIA GPU 上 RTF 可低至 ~0.3）
- 打印每个 chunk 的到达时刻/时长（观测流式节奏）
- 合并全部 chunk 写出 streaming_demo.wav
- 支持 --reference 参考音频（克隆）；默认预设音色 default

用法（双击或命令行）:
  python Scripts/voxcpm_streaming_demo.py
  python Scripts/voxcpm_streaming_demo.py --reference myref.wav --text "你好"

说明:
- 流式适合「边合成边播放/边传输」的实时场景（如语音助手、直播配音）；
  批量离线生产仍用 voxcpm_tts_v5_longtext.py（管线已含重试/长文切分/降噪）。
- 本演示无额外依赖（numpy/soundfile/torch 均已内置）。
"""
import argparse
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser(description="VoxCPM2 流式合成演示")
    ap.add_argument("--text", default="欢迎使用 VoxCPM 语音合成。这是一个流式合成演示，音频块会边生成边到达。")
    ap.add_argument("--reference", default="", help="参考音频路径（可控制克隆）；留空=预设音色 default")
    ap.add_argument("--out", default="streaming_demo.wav", help="输出 wav 路径")
    ap.add_argument("--seed", type=int, default=42, help="可复现种子")
    args = ap.parse_args()

    # ---- 与 web_ui 相同的模型定位逻辑 ----
    def _find_model_dir():
        env = os.environ.get("VOXCPM_MODEL_DIR")
        if env:
            return env
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(base, "model", "openbmb", "VoxCPM2")
        return cand if os.path.isdir(cand) else None

    model_dir = _find_model_dir()
    print(f"[demo] model_dir = {model_dir}")
    if model_dir and not os.path.isdir(model_dir):
        print(f"[demo] 错误: 模型目录不存在: {model_dir}")
        print("       请设置环境变量 VOXCPM_MODEL_DIR 指向 4.7GB 模型目录（安装时若选了不含模型版本，模型需另行获取）")
        sys.exit(1)

    import random
    import numpy as np
    import torch

    # 播种（与 CLI/Adapter 三层一致）
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)

    import soundfile as sf
    from voxcpm import VoxCPM

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print("[demo] 加载模型（约 30-60 秒）...")
    t0 = time.time()
    if model_dir and os.path.isdir(model_dir):
        model = VoxCPM.from_pretrained(model_dir, load_denoiser=False, optimize=False, device=device)
    else:
        model = VoxCPM.from_pretrained("openbmb/VoxCPM2", load_denoiser=False, optimize=False, device=device)
    print(f"[demo] 模型加载完成，用时 {time.time()-t0:.1f}s")

    text = args.text
    ref = args.reference or ""
    if ref:
        print(f"[demo] 使用参考音频: {ref}")
        if not os.path.exists(ref):
            sys.exit(f"[demo] 参考音频不存在: {ref}")

    print(f"[demo] 流式合成: {text!r}")
    t_start = time.time()
    chunks = []
    t_first = None
    gen_kwargs = {"text": text, "reference_wav_path": ref or None}
    for i, chunk in enumerate(model.generate_streaming(**gen_kwargs)):
        now = time.time()
        if t_first is None:
            t_first = now
            print(f"[demo] 首块到达: {now - t_start:.2f}s")
        chunks.append(chunk.detach().cpu().float().numpy() if hasattr(chunk, "detach") else np.asarray(chunk))
        print(f"  chunk {i + 1:2d}: {len(chunks[-1]) / model.tts_model.sample_rate:.2f}s 音频 (累计 {sum(len(c) for c in chunks) / model.tts_model.sample_rate:.2f}s)")

    audio = np.concatenate(chunks, axis=0)
    sr = model.tts_model.sample_rate
    total = time.time() - t_start
    dur = len(audio) / sr
    sf.write(args.out, audio.astype("float32"), sr)
    print(f"[demo] 完成: 共 {len(chunks)} 块, 音频 {dur:.2f}s, 总耗时 {total:.2f}s, RTF {total / max(dur, 1e-6):.2f}")
    print(f"[demo] 输出: {os.path.abspath(args.out)}")
    if t_first is not None:
        print(f"[demo] 首块延迟 (TTFB): {t_first - t_start:.2f}s — 实时场景下用户等待时长")


if __name__ == "__main__":
    main()
