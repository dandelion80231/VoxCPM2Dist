# VoxCPM2 TTS v5.0 引擎 — 长文本配音/音色统一/交互模式
#
# [v5.0 修复] 自播种（Self-Seeding）音色漂移问题
#   - 根因：VoxCPM2 build_prompt_cache 中，仅传 reference_wav_path
#     时进入 "reference" 模式（只编码声学特征，不知音频内容），
#     导致模型无法彻底分离"说话人声音"与"内容韵律"。
#   - 修复：保存第1段原文作为 prompt_text，后续克隆时同时传入
#     prompt_wav_path + prompt_text + reference_wav_path，
#     使模型进入 "ref_continuation" 模式（同时知声又知文）。
#
# [v4.0 新特性]
#   长文本分段生成 + 音色统一 + 交叉淡入淡出拼接
#   三种模式：固定参考克隆 / Self-Seeding / 逐段 Voice Design
#
# VoxCPM2 语音合成引擎（加载 openbmb/VoxCPM2）
# 适用：长文本配音、语音克隆、音色控制

import argparse
import datetime
import os
import re
import sys
import time

# 内嵌版 Python 不自动添加脚本目录，手动加入以便可靠导入同级模块 text_norm_cn
# （否则 build_text 内的 import 会失败并静默回退到 wetext，导致与网页端归一化不一致）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path

import numpy as np

# ── 配置 ─────────────────────────────────────────────────
MODEL_ID = "openbmb/VoxCPM2"
LOCAL_MODEL_PATH = os.environ.get("VOXCPM_MODEL_DIR", "")
DEVICE = "cuda" if os.environ.get("VOXCPM_DEVICE", "") else "auto"
DEFAULT_OUTPUT_DIR = Path(os.environ.get("VOXCPM_OUTPUT_DIR", str(Path.home() / "Desktop")))
MAX_CHUNK_SIZE = 240


def _resolve_zipenhancer_dir():
    """定位随包发布的 ZipEnhancer 降噪模型目录（离线，纯本地，不联网）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.path.dirname(here)):
        cand = os.path.join(base, "models", "zipenhancer")
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "configuration.json")):
            return cand
    return None


def load_model(no_cache: bool = False, force_reload: bool = False):
    from voxcpm import VoxCPM
    model_path = LOCAL_MODEL_PATH if LOCAL_MODEL_PATH else MODEL_ID
    if not no_cache and not force_reload and hasattr(load_model, "_cached_model"):
        print("[模型] 使用缓存模型...")
        return load_model._cached_model
    print(f"\n[模型] 正在加载: {model_path}")
    t0 = time.time()
    try:
        # 离线降噪：随包模型存在则启用，否则降级为空操作（保持离线安全）
        zpath = _resolve_zipenhancer_dir()
        use_denoiser = False
        if zpath:
            try:
                from voxcpm.zipenhancer import ZipEnhancer
                ZipEnhancer(zpath)
                use_denoiser = True
                print(f"[降噪] 离线降噪模型已启用: {zpath}")
            except Exception as e:
                print(f"[降噪] 模型加载失败，降噪不可用: {e}")
        if LOCAL_MODEL_PATH and os.path.isdir(LOCAL_MODEL_PATH):
            model = VoxCPM.from_pretrained(LOCAL_MODEL_PATH, load_denoiser=use_denoiser, zipenhancer_model_id=zpath if use_denoiser else None, optimize=False, device=DEVICE)
        else:
            model = VoxCPM.from_pretrained(MODEL_ID, load_denoiser=use_denoiser, zipenhancer_model_id=zpath if use_denoiser else None, optimize=False, device=DEVICE)
    except Exception as e:
        print(f"[错误] 模型加载失败: {e}")
        raise
    elapsed = time.time() - t0
    sample_rate = getattr(model.tts_model, "sample_rate", None)
    print(f"[模型] 加载完成，耗时 {elapsed:.1f}s，采样率: {sample_rate}Hz")
    if sample_rate == 48000:
        print("[模型] 已确认 VoxCPM2 — Voice Design 可用")
    if not no_cache:
        load_model._cached_model = model
    return model


def build_text(input_text: str, control: str = None, normalize: bool = False) -> str:
    """构建最终合成文本（添加控制指令）。
    归一化统一使用 text_norm_cn.normalize_text（与网页端同一套规则：
    数字/日期/电话按中文读法、110->妖妖灵、电话1->幺、年份区间->到）。"""
    text = input_text
    if normalize:
        try:
            from text_norm_cn import normalize_text
            text = normalize_text(text)
        except Exception as e:
            print(f"[警告] 自定义文本规范化失败，回退 wetext: {e}")
            try:
                import wetext
                text = wetext.normalize(text, remove_punct=False, Traditional=False)
            except ImportError:
                print("[警告] wetext 未安装，跳过文本规范化")
    if control:
        text = f"({control}){text}"
    return text


def generate(model, text: str, cfg: float = 2.5, steps: int = 15, normalize: bool = False) -> tuple:
    """单段生成（Voice Design 模式，兼容旧版）"""
    control_match = re.search(r"\(([^)]+)\)", text)
    control_str = control_match.group(1) if control_match else "(无)"
    body_text = text.split(")")[-1] if ")" in text else text
    print(f"\n[合成] 控制指令: {control_str} | 正文: {body_text[:50]}...")
    t0 = time.time()
    try:
        wav = model.generate(text=text, cfg_value=cfg, inference_timesteps=steps, normalize=normalize)
    except TypeError:
        wav = model.generate(text=text, cfg_value=cfg, inference_timesteps=steps)
    elapsed = time.time() - t0
    sr = model.tts_model.sample_rate
    duration = len(wav) / sr
    print(f"[合成] 完成: {duration:.1f}s 音频, {elapsed:.1f}s 渲染, RTF {elapsed/duration:.2f}")
    return sr, wav


def generate_chunk(model, text: str, cfg: float = 2.5, steps: int = 15,
                   normalize: bool = False, reference_wav_path: str = None,
                   prompt_wav_path: str = None, prompt_text: str = None) -> tuple:
    """单段合成（支持 Voice Design / Controllable Clone / Ultimate Clone）"""
    import soundfile as sf

    kwargs = {
        "text": text,
        "cfg_value": cfg,
        "inference_timesteps": steps,
    }
    if normalize:
        kwargs["normalize"] = normalize
    if reference_wav_path:
        kwargs["reference_wav_path"] = reference_wav_path
    if prompt_wav_path and prompt_text:
        kwargs["prompt_wav_path"] = prompt_wav_path
        kwargs["prompt_text"] = prompt_text
        if reference_wav_path:
            kwargs["reference_wav_path"] = reference_wav_path
    t0 = time.time()
    wav = model.generate(**kwargs)
    elapsed = time.time() - t0
    sr = model.tts_model.sample_rate
    duration = len(wav) / sr
    return sr, wav, elapsed, duration


def crossfade_concat(audio_list: list, sample_rate: int, fade_ms: int = 80) -> np.ndarray:
    """多段音频等功率交叉淡入淡出拼接（消除段间断裂/爆音）。

    规则：首段不淡入（result 初始即第一段、头部不动），末段不淡出（仅头部与前段尾交叉
    淡化，尾部完整保留）。重叠段使用等功率曲线 cos/sin，感知响度恒定，避免线性淡变中段的下凹。
    """
    if not audio_list:
        return np.array([], dtype=np.float32)
    if len(audio_list) == 1:
        return np.asarray(audio_list[0], dtype=np.float32)
    fade_n = max(1, int(sample_rate * fade_ms / 1000))
    result = np.asarray(audio_list[0], dtype=np.float32).copy()
    for seg in audio_list[1:]:
        seg = np.asarray(seg, dtype=np.float32)
        n = min(fade_n, len(result), len(seg))
        if n <= 1:
            # 段过短无法交叠，直接拼接
            result = np.concatenate([result, seg])
            continue
        tail = result[-n:]
        head = seg[:n]
        t = np.linspace(0.0, 1.0, n, dtype=np.float32)
        # 等功率交叉淡化：尾段渐弱、头段渐强
        result[-n:] = tail * np.cos(t * np.pi / 2) + head * np.sin(t * np.pi / 2)
        result = np.concatenate([result, seg[n:]])
    return result


def _segment_rms(audio: np.ndarray) -> float:
    """计算音频段 RMS（有效值）。"""
    arr = np.asarray(audio, dtype=np.float64)
    if len(arr) == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


def normalize_segments(audio_segments: list, target_mode: str = "mean") -> list:
    """对多段音频做 RMS 音量归一化，使各段感知响度一致。

    target_mode:
      - "mean": 使用所有非静音段的平均 RMS 作为目标（默认，最稳）。
      - "first": 使用第一段的 RMS 作为目标。
    为避免削波，单段缩放后若峰值超过 0.99，会限制增益。
    """
    if not audio_segments or len(audio_segments) < 2:
        return audio_segments
    rms_values = [_segment_rms(seg) for seg in audio_segments]
    valid_rms = [r for r in rms_values if r > 1e-9]
    if not valid_rms:
        return audio_segments
    target_rms = rms_values[0] if target_mode == "first" else float(np.mean(valid_rms))
    if target_rms < 1e-9:
        return audio_segments

    normalized = []
    for seg, rms in zip(audio_segments, rms_values):
        seg_arr = np.asarray(seg, dtype=np.float32)
        if rms < 1e-9:
            normalized.append(seg_arr)
            continue
        scaled = seg_arr * (target_rms / rms)
        peak = np.max(np.abs(scaled)) if len(scaled) else 0.0
        if peak > 0.99:
            scaled = scaled * (0.99 / peak)
        normalized.append(scaled)
    return normalized


def peak_normalize(audio: np.ndarray, peak: float = 0.95) -> np.ndarray:
    """最终峰值限制：把整体峰值拉到目标值，避免输出过小或削波。"""
    arr = np.asarray(audio, dtype=np.float32)
    if len(arr) == 0:
        return arr
    max_amp = float(np.max(np.abs(arr)))
    if max_amp < 1e-9:
        return arr
    return arr * (peak / max_amp)


def split_text(text: str, mode: str = "auto", chunk_size: int = MAX_CHUNK_SIZE) -> list:
    """智能切分长文本"""
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    if mode == "auto":
        current_chunk = ""
        sentences = re.split(r'([。！？；\.\!\?\;，,])', text)
        for i in range(0, len(sentences) - 1, 2):
            sentence = (sentences[i] or "") + (sentences[i + 1] if i + 1 < len(sentences) else "")
            if len(current_chunk) + len(sentence) <= chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
        if current_chunk:
            chunks.append(current_chunk)
    elif mode == "fixed":
        for i in range(0, len(text), chunk_size):
            chunks.append(text[i:i + chunk_size])
    else:
        current_chunk = ""
        sentences = re.split(r'([。！？；\.\!\?\;，,])', text)
        for i in range(0, len(sentences) - 1, 2):
            sentence = sentences[i] + (sentences[i + 1] if i + 1 < len(sentences) else "")
            if len(current_chunk) + len(sentence) <= chunk_size:
                current_chunk += sentence
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = sentence
        if current_chunk:
            chunks.append(current_chunk)
    return chunks


def generate_long_text(model, text: str, cfg: float = 2.5, steps: int = 15,
                       normalize: bool = False, split_mode: str = "auto",
                       chunk_size: int = MAX_CHUNK_SIZE, output_dir: Path = None,
                       base_filename: str = None, reference_audio: str = None,
                       prompt_audio: str = None, prompt_text: str = None,
                       self_seeding: bool = False, update_ref_every: int = 0,
                       crossfade_ms: int = 80) -> list:
    """
    长文本分段生成，支持三种音色统一模式：

    模式A（默认/方式2）: 有 reference_audio → 全部段用固定参考音频克隆（最稳定）
    模式B（方式1）    : 无 reference_audio 但 self_seeding=True → 第1段 Voice Design，后续用第1段音频克隆
    模式C（保留原行为）: 无 reference_audio 且 self_seeding=False → 每段都带控制指令（音色可能不一致）

    [v5.0]
      - 模式B 自播种：保存第1段原文为 seed_prompt_text，后续段传入
        prompt_wav_path + prompt_text，使模型进入 "ref_continuation"
        模式（同时知声又知文），大幅提升克隆保真度。
      - 模式A + CLI 传入 prompt_audio/prompt_text：同样使用 ref_continuation。
    """
    import soundfile as sf

    # 提取控制指令和正文
    control_match = re.search(r"^\(([^)]+)\)", text)
    control = control_match.group(1) if control_match else ""
    body_text = re.sub(r"^\([^)]+\)", "", text)

    print(f"\n[长文本] 正文共 {len(body_text)} 字符，切分模式: {split_mode}，每段上限: {chunk_size}")
    chunks = split_text(body_text, mode=split_mode, chunk_size=chunk_size)
    print(f"[长文本] 已切分为 {len(chunks)} 段")

    # 判断实际使用的模式
    if reference_audio and os.path.exists(reference_audio):
        mode_name = "固定参考音频克隆（方式2）"
        current_ref = reference_audio
    elif self_seeding and control:
        mode_name = "自播种克隆（方式1）"
        current_ref = None
    else:
        mode_name = "逐段音色设计（方式3，音色可能不一致）"
        current_ref = None

    print(f"[长文本] 音色统一模式: {mode_name}")
    if reference_audio and not os.path.exists(reference_audio):
        print(f"[警告] 参考音频不存在: {reference_audio}，将回退到方式1或方式3")
        current_ref = None

    # [v5.0] 自播种模式下，保存第1段原文用于后续段的 prompt_text
    seed_prompt_text = None

    output_files = []
    audio_segments = []

    for i, chunk in enumerate(chunks, 1):
        print(f"\n{'='*60}")
        print(f"[段落] 第 {i}/{len(chunks)} 段")
        print(f"{'='*60}")
        print(f"[段落] 内容: {chunk[:80]}{'...' if len(chunk) > 80 else ''}")

        # 判断当前段的 prompt_wav_path 和 prompt_text
        # 优先级：CLI 传入 > 自播种继承
        effective_prompt_wav = None
        effective_prompt_text = None
        if prompt_audio and prompt_text:
            # 用户通过 CLI 显式传入
            effective_prompt_wav = prompt_audio
            effective_prompt_text = prompt_text
        elif i > 1 and seed_prompt_text and current_ref and os.path.exists(current_ref):
            # [v5.0] 自播种后续段：继承第1段的原文
            effective_prompt_wav = current_ref
            effective_prompt_text = seed_prompt_text

        # 模式A: 固定参考音频
        if current_ref and os.path.exists(current_ref) and (i != 1 or reference_audio):
            sr, wav, elapsed, duration = generate_chunk(
                model, chunk, cfg=cfg, steps=steps, normalize=normalize,
                reference_wav_path=current_ref,
                prompt_wav_path=effective_prompt_wav,
                prompt_text=effective_prompt_text
            )
            clone_type = "ref_continuation" if effective_prompt_text else "reference"
            print(f"[段落] 使用参考音频克隆（{clone_type}），时长 {duration:.1f}s")

            # 可选：定期更新参考音频（保持上下文连贯性，但可能轻微漂移）
            if update_ref_every > 0 and (i % update_ref_every == 0):
                new_ref = os.path.join(tempfile.gettempdir(), f"voxcpm_ref_{i}_{os.getpid()}.wav")
                sf.write(new_ref, wav, sr)
                current_ref = new_ref
                print(f"[段落] 参考音频已更新为第 {i} 段")

        # 模式B: Self-Seeding（第一段 Voice Design，后续克隆）
        elif i == 1 and self_seeding and control and not current_ref:
            chunk_text = f"({control}){chunk}"
            sr, wav, elapsed, duration = generate_chunk(
                model, chunk_text, cfg=cfg, steps=steps, normalize=normalize
            )
            print(f"[段落] 第一段 Voice Design 生成完成，时长 {duration:.1f}s")

            # [v5.0] 保存第1段原文，后续克隆时作为 prompt_text 传入
            seed_prompt_text = chunk_text

            # 保存为后续段的参考音频
            ref_path = os.path.join(tempfile.gettempdir(), f"voxcpm_seed_{os.getpid()}.wav")
            sf.write(ref_path, wav, sr)
            current_ref = ref_path
            print(f"[段落] 已保存为参考音频: {ref_path}")

        # 模式B 后续段，或模式C
        else:
            if current_ref and os.path.exists(current_ref):
                # [v5.0] Self-Seeding 后续段：传入 prompt_text 使用 ref_continuation 模式
                sr, wav, elapsed, duration = generate_chunk(
                    model, chunk, cfg=cfg, steps=steps, normalize=normalize,
                    reference_wav_path=current_ref,
                    prompt_wav_path=effective_prompt_wav,
                    prompt_text=effective_prompt_text
                )
                clone_type = "ref_continuation" if effective_prompt_text else "reference"
                print(f"[段落] 使用自播种参考音频克隆（{clone_type}），时长 {duration:.1f}s")

                if update_ref_every > 0 and (i % update_ref_every == 0):
                    new_ref = os.path.join(tempfile.gettempdir(), f"voxcpm_ref_{i}_{os.getpid()}.wav")
                    sf.write(new_ref, wav, sr)
                    current_ref = new_ref
                    print(f"[段落] 参考音频已更新为第 {i} 段")
            else:
                # 模式C: 逐段 Voice Design
                chunk_text = f"({control}){chunk}" if control else chunk
                sr, wav, elapsed, duration = generate_chunk(
                    model, chunk_text, cfg=cfg, steps=steps, normalize=normalize
                )
                print(f"[段落] 逐段 Voice Design，时长 {duration:.1f}s")

        audio_segments.append(wav)

        if output_dir and base_filename:
            part_file = output_dir / f"{base_filename}_part{i:03d}.wav"
            sf.write(str(part_file), wav, sr)
            output_files.append(part_file)
            print(f"[段落] 已保存: {part_file.name}")

    # 交叉淡入淡出拼接
    if len(audio_segments) > 1:
        print(f"\n[拼接] 正在使用 {crossfade_ms}ms 交叉淡入淡出拼接 {len(audio_segments)} 段音频...")
        audio_segments = normalize_segments(audio_segments, target_mode="mean")
        merged_wav = crossfade_concat(audio_segments, sr, fade_ms=crossfade_ms)
        merged_wav = peak_normalize(merged_wav, peak=0.95)

        if output_dir and base_filename:
            merged_file = output_dir / f"{base_filename}_merged.wav"
            sf.write(str(merged_file), merged_wav, sr)
            output_files.append(merged_file)
            total_duration = len(merged_wav) / sr
            print(f"[拼接] 合并完成: {merged_file.name}，总时长 {total_duration:.1f}s")
    else:
        merged_wav = audio_segments[0] if audio_segments else np.array([])

    return output_files, merged_wav, sr


def resolve_output_path(output: str | None, text: str, suffix: str = "") -> Path:
    import datetime
    if output:
        path = Path(output)
        if path.suffix != ".wav":
            path = path.with_suffix(".wav")
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        return path
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[\\/:*?\"<>|]", "", text[:20]) if text else "output"
    filename = f"{base}_{timestamp}{suffix}.wav"
    output = DEFAULT_OUTPUT_DIR / filename
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def resolve_base_filename(text: str) -> str:
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = re.sub(r"[\\/:*?\"<>|]", "", text[:20]) if text else "output"
    return f"{base}_{timestamp}"


# ── 音色预设 ──────────────────────────────────────────────
VOICE_PRESETS = {
    "sweet_girl": "25岁年轻温柔甜美女声，带一点播音腔，语速稍平缓",
    "warm_woman": "年轻女性，温柔甜美，语速适中",
    "gentleman": "中年男性，温润儒雅，播音腔，语速平缓",
    "energetic_broadcaster": "热情洋溢的中年男性播音员，声音低沉富有磁性",
    "elder_woman": "老年女性，声音温和慈祥，语速缓慢",
    "cool_guy": "年轻男性，声音低沉冷静，略带磁性",
    "cheerful_girl": "年轻女性，活泼开朗，语速偏快",
    "storyteller": "中年男性，深沉有磁性，适合讲故事，节奏平缓",
    "calm_male": "年轻男性，声音沉稳，语速平缓，适合新闻播报",
    "teacher": "中年女性，声音清晰有力，语速适中，适合教学讲解",
    "default": "25岁年轻温柔甜美女声，带一点播音腔，语速稍平缓",
}

# ── 主入口 ─────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="VoxCPM2 TTS v5.0 — 长文本配音/音色统一")
    parser.add_argument("-t", "--text", type=str, help="要合成的文本")
    parser.add_argument("-f", "--file", type=str, help="输入文本文件路径")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出 WAV 文件路径")
    parser.add_argument("--dir", type=str, default=None, help="输出目录（默认桌面）")
    parser.add_argument("--no-baseline", action="store_true", help="不生成基线参考音频")

    # Voice Control
    parser.add_argument("--voice", type=str, default=None, choices=list(VOICE_PRESETS.keys()),
                        help="音色预设名称")
    parser.add_argument("-c", "--control", type=str, default=None,
                        help="控制指令（如：25岁年轻温柔甜美女声）")

    # 音色统一模式（长文本）
    parser.add_argument("--reference", type=str, help="参考音频路径（固定克隆，长文本默认方式）")
    # [v5.0] 显式传入 prompt_audio / prompt_text 可在固定参考模式下
    # 使用 ref_continuation 模式，让模型既知声又知文
    parser.add_argument("--prompt-audio", type=str, help="提示音频路径（Ultimate Clone / ref_continuation）")
    parser.add_argument("--prompt-text", type=str, help="提示音频对应的文本（ref_continuation）")
    parser.add_argument("--self-seeding", "--self_seeding", dest="self_seeding",
                        nargs="?", const=True, default=None, type=lambda x: x != "false",
                        help="自播种模式（第1段 Voice Design，后续克隆）")
    parser.add_argument("--split", type=str, default=None, choices=["auto", "fixed", "sentence"],
                        help="文本切分模式")
    parser.add_argument("--chunk-size", type=int, default=180, help="每段最大字符数")

    # 生成参数
    parser.add_argument("--cfg", type=float, default=2.5, help="CFG scale (默认 2.5)")
    parser.add_argument("--steps", type=int, default=15, help="推理步数 (默认 15)")
    parser.add_argument("--no-normalize", action="store_false", dest="normalize", help="禁用文本规范化")
    parser.add_argument("--no-cache", action="store_true", help="禁用模型缓存")

    # 高级
    parser.add_argument("--update-ref", type=int, default=0,
                        help="每 N 段更新一次参考音频（防漂移，0=不更新）")
    parser.add_argument("--crossfade", type=int, default=80,
                        help="交叉淡入淡出毫秒数（长文本用，默认 80ms）")
    parser.add_argument("--no-cuda", action="store_true", help="强制使用 CPU")
    parser.add_argument("-i", "--interactive", action="store_true", help="交互模式")
    parser.add_argument("--list-voices", action="store_true", help="列出所有音色预设")
    parser.add_argument("--show-config", action="store_true", help="显示当前配置")
    parser.add_argument("-v", "--version", action="store_true", help="显示版本号")

    args = parser.parse_args()

    if args.version:
        print("VoxCPM TTS v5.0 CN — 音色统一版")
        sys.exit(0)

    if args.list_voices:
        print("\n[音色预设列表]")
        for name, desc in sorted(VOICE_PRESETS.items()):
            print(f"  {name:22s}  {desc}")
        sys.exit(0)

    if args.show_config:
        print(f"\n[配置]")
        print(f"  DEVICE:          {DEVICE}")
        print(f"  MODEL_ID:        {LOCAL_MODEL_PATH or MODEL_ID}")
        print(f"  OUTPUT_DIR:      {DEFAULT_OUTPUT_DIR}")
        print(f"  CHUNK_SIZE:      {MAX_CHUNK_SIZE}")
        print(f"  CFG:             {args.cfg}")
        print(f"  STEPS:           {args.steps}")
        print(f"  NORMALIZE:       {args.normalize}")
        print(f"  CROSSFADE_MS:    {args.crossfade}")
        print(f"  UPDATE_REF:      {args.update_ref}")
        sys.exit(0)

    import torch
    if args.no_cuda and torch.cuda.is_available():
        print("[配置] 强制使用 CPU")
        os.environ["CUDA_VISIBLE_DEVICES"] = ""

    if args.interactive:
        model = load_model(no_cache=args.no_cache)
        print(f"\n[交互模式] 输入 'q' 退出，'h' 帮助")
        print(f"[交互模式] 语法: <text> 或 @<file> 或 <text> |desc|")
        while True:
            try:
                line = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not line:
                continue
            if line.lower() in ("q", "quit", "exit"):
                break
            if line == "h":
                print("  <text>         普通合成（默认温柔女声）")
                print("  <text> |desc|  指定音色描述")
                print("  @<file>        朗读文件")
                continue
            if line.startswith("@"):
                filepath = line[1:].strip().strip("\"'")
                if not os.path.exists(filepath):
                    print(f"[错误] 文件不存在: {filepath}")
                    continue
                with open(filepath, "r", encoding="utf-8") as f:
                    text = f.read()
                control = None
                final = build_text(text, control, normalize=args.normalize)
                sr, wav, elapsed, duration = generate_chunk(model, final, cfg=args.cfg, steps=args.steps, normalize=args.normalize)
                out = resolve_output_path(None, text)
                import soundfile as sf
                sf.write(str(out), wav, sr)
                print(f"[保存] {out}")
            else:
                control = None
                if "|" in line:
                    parts = line.rsplit("|", 2)
                    if len(parts) == 3:
                        line, control = parts[0].strip(), parts[1].strip()
                final = build_text(line, control, normalize=args.normalize)
                sr, wav, elapsed, duration = generate_chunk(model, final, cfg=args.cfg, steps=args.steps, normalize=args.normalize)
                out = resolve_output_path(None, line)
                import soundfile as sf
                sf.write(str(out), wav, sr)
                print(f"[保存] {out}")
        sys.exit(0)

    # ── 加载模型 ──
    model = load_model(no_cache=args.no_cache)
    sample_rate = getattr(model.tts_model, "sample_rate", 48000)
    print(f"[设备] 采样率: {sample_rate}Hz")

    # ── 读取输入文本 ──
    if args.file:
        file_path = args.file
        if not os.path.exists(file_path):
            print(f"[错误] 文件不存在: {file_path}")
            sys.exit(1)
        with open(file_path, "r", encoding="utf-8") as f:
            input_text = f.read().strip()
        if not input_text:
            print("[错误] 文件为空")
            sys.exit(1)
        print(f"[输入] 文件: {file_path}")
        print(f"[输入] 字符数: {len(input_text)}")
    elif args.text:
        input_text = args.text.strip()
        print(f"[输入] 文本: {input_text[:80]}{'...' if len(input_text) > 80 else ''}")
    else:
        parser.print_help()
        sys.exit(1)

    # ── 解析控制指令 ──
    control = args.control or VOICE_PRESETS.get(args.voice, VOICE_PRESETS["default"])
    res = re.match(r"^\(([^)]+)\)(.*)", input_text)
    if res:
        control, input_text = res.group(1), res.group(2).strip()

    do_normalize = args.normalize
    final_text = build_text(input_text, control, normalize=do_normalize)

    # ── 设置输出目录 ──
    if args.dir:
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        global_DEFAULT_OUTPUT_DIR = DEFAULT_OUTPUT_DIR

    # ── 判断长文本模式 ──
    is_long = args.file is not None or len(input_text) > MAX_CHUNK_SIZE
    split_mode = args.split

    if is_long:
        # 如果指定了输出目录
        if args.dir:
            DEFAULT_OUTPUT_DIR = Path(args.dir)
            DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        global_DEFAULT_OUTPUT_DIR = DEFAULT_OUTPUT_DIR

        if not split_mode:
            split_mode = "auto"
        print(f"\n[模式] 长文本模式，切分: {split_mode}，分段大小: {args.chunk_size}")

        # 判断音色统一模式
        if args.reference:
            print(f"[模式] 使用固定参考音频: {args.reference}")
        elif args.self_seeding is True or (args.self_seeding is None and control and not args.reference):
            print(f"[模式] 自动启用自播种（方式1）：第1段 Voice Design，后续克隆")
            args.self_seeding = True
        else:
            print(f"[模式] 逐段音色设计（方式3）：每段独立生成，音色可能不一致")

        base_name = resolve_base_filename(input_text)
        output_files, merged_wav, sr = generate_long_text(
            model, final_text, cfg=args.cfg, steps=args.steps,
            normalize=do_normalize, split_mode=split_mode,
            chunk_size=args.chunk_size, output_dir=DEFAULT_OUTPUT_DIR,
            base_filename=base_name,
            reference_audio=args.reference,
            prompt_audio=args.prompt_audio,
            prompt_text=args.prompt_text,
            self_seeding=args.self_seeding,
            update_ref_every=args.update_ref,
            crossfade_ms=args.crossfade
        )

        print(f"\n[完成] 共生成 {len(output_files)} 个文件")
        for f in output_files:
            print(f"  {f.name}")
    else:
        # 短文本模式
        if args.prompt_audio and args.prompt_text:
            print(f"\n[模式] Ultimate Clone 模式")
            sr, wav = model.generate(
                text=final_text, prompt_wav_path=args.prompt_audio,
                prompt_text=args.prompt_text, reference_wav_path=args.reference,
                cfg_value=args.cfg, inference_timesteps=args.steps
            )
        elif args.reference:
            print(f"\n[模式] Controllable Clone 模式")
            sr, wav = model.generate(
                text=final_text, reference_wav_path=args.reference,
                cfg_value=args.cfg, inference_timesteps=args.steps
            )
        else:
            print(f"\n[模式] Voice Design 模式")
            sr, wav = generate(model, final_text, cfg=args.cfg, steps=args.steps, normalize=do_normalize)

        path = resolve_output_path(args.output, input_text)
        import soundfile as sf
        sf.write(str(path), wav, sr)
        print(f"\n[保存] {path}")
        duration = len(wav) / sr
        print(f"[完成] 时长 {duration:.1f}s" if duration else "[完成] 合成结束")

    if args.voice and args.voice in VOICE_PRESETS:
        baseline_control = VOICE_PRESETS[args.voice]
        baseline_text = build_text(input_text[:60], baseline_control, normalize=do_normalize)
        if control and not args.no_baseline and not args.reference and not args.prompt_audio:
            print(f"\n[基线] 生成基线参考音频（用于对比）...")
            sr_b, wav_b = generate(model, baseline_text, cfg=args.cfg, steps=args.steps, normalize=do_normalize)
            baseline_path = resolve_output_path(None, "baseline_" + input_text[:20], suffix="_baseline")
            sf.write(str(baseline_path), wav_b, sr_b)
            print(f"[基线] 已保存: {baseline_path}")


if __name__ == "__main__":
    main()
