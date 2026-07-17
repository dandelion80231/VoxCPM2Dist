#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VoxCPM2 自举合成「多音字 LoRA」训练音频（免录制）。

用 VoxCPM2 基础模型 + 可选参考音，把 polyphone_sentences.txt 的每一句直接合成成
wav，并产出训练用 JSONL（text / audio 两列），可直接作为
train_voxcpm_finetune.py 的 train_manifest。

⚠️ 自举的固有风险（务必先看）：
   合成音频的「读音」来自基础模型本身。若基础模型在某句也读错了某个多音字，
   该句训练目标就是错的，LoRA 可能学到错误读音。为降低风险：
   1) 句子已由 xiaohk/pinyin_data 的示例词构造，上下文已尽量锁定正确读音；
   2) 先用 --preview 20 合成少量试听，确认读音无误再跑全量；
   3) 训练后用 verify_lora.py 复测，对仍读错的句单独人工补录并重训。

用法（在用户 GPU 机，用 app/python_cuda/python.exe 执行）：
  # 试听前 20 句（写 lora_audio/preview.jsonl + 001~020.wav）
  python bootstrap_lora_audio.py --preview 20

  # 全量合成（写 lora_audio/train.jsonl + 001~NNNN.wav），可选 --ref 决定音色
  python bootstrap_lora_audio.py --ref "参考音.wav"
  python bootstrap_lora_audio.py --ref "参考音.wav" --limit 300   # 先用子集跑通

  # 然后训练（train_polyphone_lora.ps1 会自动读 voxcpm_finetune_lora.yaml）
  powershell -ExecutionPolicy Bypass -File ../train_polyphone_lora.ps1

  # 试听后若发现某句模型读错（如把"千乘之国"的"乘"读成 chéng），把它排除，
  # 避免错误读音污染训练集：
  python bootstrap_lora_audio.py --exclude 17
  # 也可把多个错句写进 lora_audio/exclude.txt（每行一个序号，或逗号分隔），
  # 重跑时自动应用；--exclude 用于临时追加。
  # 部分句"听不清"多为音量过小，默认已做峰值归一化；如不需要可 --no-normalize。
  # 模型对少数病理句（极长重复绕口令）可能吐出大段静音/过长音频，脚本已内置「退化样本
  # 防护」：RMS 过低或时长超长会自动用更小的 max_len 重试，仍不达标则跳过并记入
  # lora_audio/degenerate_log.txt，绝不会把静音样本写进训练清单污染 LoRA。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))  # 让 import voxcpm 可达


def main():
    ap = argparse.ArgumentParser(description="自举合成多音字 LoRA 训练音频（免录制）")
    ap.add_argument("--sentences", type=Path, default=ROOT / "../polyphone_sentences.txt",
                    help="句子清单（每行一句，# 开头为注释）")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "../lora_audio",
                    help="wav 与 JSONL 输出目录")
    ap.add_argument("--ref", default=None,
                    help="参考音 wav 路径（决定合成音色，可选；不填则用模型默认声）")
    ap.add_argument("--model-dir", type=Path, default=ROOT / "../../model/openbmb/VoxCPM2",
                    help="本地模型目录（离线，不联网）")
    ap.add_argument("--device", default=None, help="强制设备，如 cuda / cpu")
    ap.add_argument("--limit", type=int, default=0, help="只合成前 N 句（0=全部）")
    ap.add_argument("--start", type=int, default=0, help="从第 N 句开始（0 基）")
    ap.add_argument("--max-len", type=int, default=4096, help="生成最大 token 长度")
    ap.add_argument("--cfg", type=float, default=2.0, help="CFG 引导强度")
    ap.add_argument("--steps", type=int, default=10, help="扩散推理步数")
    ap.add_argument("--preview", type=int, default=0,
                    help="预览模式：只合成前 N 句并写 preview.jsonl，供试听后再跑全量")
    ap.add_argument("--exclude", type=str, default="",
                    help="额外排除的句子序号（1 基全局序号，逗号分隔，如 17 或 8,14,17）；"
                         "模型读错的句应排除，避免错误读音污染训练集")
    ap.add_argument("--exclude-file", type=Path, default=ROOT / "../lora_audio/exclude.txt",
                    help="排除清单文件（每行一个序号或逗号分隔）；存在则自动应用，便于逐步累积反馈")
    ap.add_argument("--no-normalize", dest="normalize", action="store_false",
                    help="关闭峰值归一化（默认开启，解决部分句合成音量过小/听不清）")
    ap.add_argument("--min-rms", type=float, default=0.04,
                    help="退化判定的 RMS 下限：归一化后 RMS 低于此值视为静音样本（默认 0.04）")
    ap.add_argument("--max-dur", type=float, default=25.0,
                    help="退化判定的时长上限（秒）：超过视为异常过长（默认 25.0）")
    ap.add_argument("--degenerate-retries", type=int, default=2,
                    help="命中退化后，用减半的 max_len 重试次数（默认 2）")
    ap.add_argument("--extra", type=Path, default=ROOT / "../polyphone_extra.txt",
                    help="额外句子文件（如用户提供的绕口令/多音字测试）；存在则合并进训练集，追加在末尾")
    args = ap.parse_args()

    # 重依赖延迟导入（仅运行期需要，import 本模块做语法检查时不触发）
    import numpy as np
    import torch
    import soundfile as sf
    from voxcpm import VoxCPM

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[bootstrap] device = {device}")

    print("=" * 64)
    print("⚠️  自举合成警告：合成音频的读音来自基础模型。若基础模型在某句")
    print("    读错多音字，该句训练目标即错误，LoRA 可能学到错误读音。")
    print("    建议：先用 --preview 20 试听，确认读音正确后再跑全量；")
    print("    训练后用 verify_lora.py 复测，对仍读错的句单独人工补录重训。")
    print("=" * 64)

    print(f"[bootstrap] loading model from {args.model_dir} ...")
    model = VoxCPM.from_pretrained(
        str(args.model_dir),
        load_denoiser=False,   # 离线安全，且训练目标不需要降噪
        optimize=False,
        device=device,
    )
    sr = int(model.tts_model.sample_rate)
    print(f"[bootstrap] model sample_rate = {sr}")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    is_preview = args.preview > 0
    limit = args.preview if is_preview else args.limit
    manifest = out_dir / ("preview.jsonl" if is_preview else "train.jsonl")

    lines = [ln.strip() for ln in Path(args.sentences).read_text(encoding="utf-8").splitlines()
             if ln.strip() and not ln.startswith("#")]
    # 合并额外句（用户提供的绕口令/多音字测试），追加在末尾，全局序号接在主线之后
    if args.extra and Path(args.extra).exists():
        extra_lines = [ln.strip() for ln in Path(args.extra).read_text(encoding="utf-8").splitlines()
                       if ln.strip() and not ln.startswith("#")]
        if extra_lines:
            lines.extend(extra_lines)
            print(f"[bootstrap] 已合并额外句文件 {args.extra} -> +{len(extra_lines)} 句（追加在末尾）")
    seg = lines[args.start:]
    if limit:
        seg = seg[:limit]

    # 排除清单：exclude-file（持久化，逐步累积）+ 命令行 --exclude（临时追加）
    exclude_set = set()

    def _add_spec(spec):
        for s in spec.split(","):
            s = s.strip()
            if s.isdigit():
                exclude_set.add(int(s))

    if args.exclude_file and Path(args.exclude_file).exists():
        _add_spec(Path(args.exclude_file).read_text(encoding="utf-8"))
    _add_spec(args.exclude)
    if exclude_set:
        print(f"[bootstrap] 排除句序号: {sorted(exclude_set)}（模型读错，避免污染训练集）")

    recs = []
    for i, text in enumerate(seg, 1):
        gidx = args.start + i  # 全局有效句序号（1 基），与 preview 显示一致
        if gidx in exclude_set:
            print(f"[skip] 句 {gidx}（已排除） {text[:24]}")
            continue
        try:
            audio = model.generate(
                text=text,
                reference_wav_path=args.ref,
                max_len=args.max_len,
                cfg_value=args.cfg,
                inference_timesteps=args.steps,
            )
            audio = np.asarray(audio, dtype=np.float32)
        except Exception as e:
            print(f"[WARN] 句 {i} 合成失败: {e}")
            continue
        if args.normalize:
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 0:
                audio = audio / peak * 0.95  # 峰值拉到 0.95，统一听感、不爆音

        # ---- 退化样本防护：模型对某些病理句（如极长重复绕口令）会吐大段静音/过长音频 ----
        # 这类样本若进训练集会污染 LoRA（学到「该句→静音」），必须拦截。判定：RMS 过低
        # （归一化后接近静音）或时长离谱超长。命中后先用更小的 max_len 重试，仍不达标则
        # 跳过该句并记入 degenerate_log.txt，绝不写进训练清单。
        dur = (len(audio) / sr) if sr else 0.0
        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        if rms < args.min_rms or dur > args.max_dur:
            retry_ok = False
            cur_max = args.max_len
            for _ in range(args.degenerate_retries):
                cur_max = max(512, cur_max // 2)
                try:
                    audio2 = np.asarray(model.generate(
                        text=text, reference_wav_path=args.ref,
                        max_len=cur_max, cfg_value=args.cfg,
                        inference_timesteps=args.steps,
                    ), dtype=np.float32)
                except Exception as e:
                    print(f"[WARN] 句 {gidx} 重试合成失败: {e}")
                    break
                p2 = float(np.max(np.abs(audio2))) if audio2.size else 0.0
                if p2 > 0:
                    audio2 = audio2 / p2 * 0.95
                d2 = (len(audio2) / sr) if sr else 0.0
                r2 = float(np.sqrt(np.mean(audio2 ** 2))) if audio2.size else 0.0
                if r2 >= args.min_rms and d2 <= args.max_dur:
                    audio, dur, rms = audio2, d2, r2
                    retry_ok = True
                    print(f"[retry] 句 {gidx} 用 max_len={cur_max} 重合成通过 dur={d2:.1f}s")
                    break
            if not retry_ok:
                print(f"[SKIP-DEGENERATE] 句 {gidx} 静音/过长，已跳过避免污染训练集: {text[:24]}")
                with open(out_dir / "degenerate_log.txt", "a", encoding="utf-8") as _lf:
                    _lf.write(f"{gidx}\t{text}\n")
                continue

        wav_path = out_dir / f"{gidx:04d}.wav"
        sf.write(str(wav_path), audio, sr)
        recs.append({"text": text, "audio": str(wav_path.resolve())})
        print(f"[ok] #{gidx} ({i}/{len(seg)}) {len(audio) / sr:5.2f}s  {text[:24]}")

    with open(manifest, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[done] 写出 {len(recs)} 条 -> {manifest}")
    if is_preview:
        print("[提示] 预览模式：试听上面的 wav，确认读音正确后再去掉 --preview 跑全量。")


if __name__ == "__main__":
    main()
