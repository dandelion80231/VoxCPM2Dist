"""分段 TTS 幻觉/跑飞守卫（借鉴 voicebox chunked_tts 的 runaway 检测 + 内静音裁剪）。

纯 numpy + 标准库，无第三方依赖。供 voxcpm_tts_v5_longtext.py（CLI 长文本）
与 vox_web_ui.py（Web 合成循环）共用：

  - trim_internal_silence: 每段生成后裁掉「段内长静音及其后的幻觉噪声」
    （模型偶发 [语音][≥1.2s 静音][幻觉噪声/回声]，裁掉后拼接更干净）。
  - has_runaway: 能量域检测 [语音][静音≥2s][再有语音] 形态——这是 TTS 模型
    漏 EOS、续写幻觉的可靠信号（前后静音不算，必须有语音夹在中间）。
  - guard_chunk: 生成+裁剪；若检出 runaway 形态且允许重试，自动把该段文本
    对半拆开分别重生成（递归，最多 max_depth 层、每段不小于 min_chars），
    即「跑飞的段自动拆小重生成」。

来源：jamiepine/voicebox backend/utils/chunked_tts.py + backend/utils/audio.py
（Apache-2.0，此处为适配 VoxCPM 分段管线做的简化移植，算法口径保持一致）。
"""

from __future__ import annotations

import numpy as np

__all__ = ["has_runaway", "trim_internal_silence", "guard_chunk"]

_FRAME_MS = 20
_SILENCE_DB = -40.0


def _frame_rms(audio: np.ndarray, sample_rate: int, frame_ms: int = _FRAME_MS):
    """逐帧 RMS 数组 + 帧长（采样数）。帧长无效时返回 (空, 0)。"""
    frame_len = int(sample_rate * frame_ms / 1000)
    arr = np.asarray(audio, dtype=np.float32).ravel()
    if frame_len <= 0 or len(arr) < frame_len:
        return np.zeros(0, dtype=np.float32), frame_len
    n = len(arr) // frame_len
    seg = arr[: n * frame_len].reshape(n, frame_len)
    return np.sqrt(np.mean(seg * seg, axis=1)), frame_len


def has_runaway(audio, sample_rate: int, max_silence_ms: int = 2000) -> bool:
    """检测 [语音][静音≥max_silence_ms][再有语音] 的跑飞形态。

    段首/段尾静音不算（必须被两侧语音夹住），这是幻觉续写的可靠信号。"""
    rms, frame_len = _frame_rms(audio, sample_rate)
    if len(rms) == 0:
        return False
    thr = 10.0 ** (_SILENCE_DB / 20)
    max_sil = max(1, int(max_silence_ms / _FRAME_MS))
    seen_speech = False
    run = 0
    for v in rms:
        if v >= thr:
            if seen_speech and run >= max_sil:
                return True
            seen_speech = True
            run = 0
        elif seen_speech:
            run += 1
    return False


def trim_internal_silence(
    audio,
    sample_rate: int,
    max_internal_silence_ms: int = 1200,
    keep_tail_ms: int = 200,
    fade_ms: int = 30,
) -> np.ndarray:
    """裁掉段内长静音及其后的幻觉噪声：

    1. 找到首个语音帧（保留 1 帧头垫）；
    2. 向前扫描，遇到静音间隙 ≥ max_internal_silence_ms 即在间隙起点处截断；
    3. 截断点之后再去掉尾静音，但保留 keep_tail_ms 的尾垫；
    4. 末端加 fade_ms 余弦淡出，消除截断点击声。

    无任何长静音/幻觉时返回内容等价音频（仅可能去掉前导静音），不放大不改电平。"""
    arr = np.asarray(audio, dtype=np.float32).ravel()
    rms, frame_len = _frame_rms(arr, sample_rate)
    if len(rms) == 0 or frame_len == 0:
        return arr
    thr = 10.0 ** (_SILENCE_DB / 20)
    n_frames = len(rms)
    is_speech = rms >= thr
    first = int(np.argmax(is_speech)) if is_speech.any() else 0
    first = max(0, first - 1)  # 留 1 帧头垫

    max_sil = max(1, int(max_internal_silence_ms / _FRAME_MS))
    cut = n_frames
    run = 0
    for i in range(first, n_frames):
        if is_speech[i]:
            run = 0
        else:
            run += 1
            if run >= max_sil:
                cut = i - run + 1
                break

    # 截断点之前再去尾静音（保留 keep_tail_ms 尾垫）
    keep_frames = int(keep_tail_ms / _FRAME_MS)
    end = cut
    while end > first and not is_speech[end - 1]:
        end -= 1
    end = min(end + keep_frames, cut)

    start_sample = first * frame_len
    end_sample = min(end * frame_len, len(arr))
    trimmed = arr[start_sample:end_sample].copy()

    fade_n = int(sample_rate * fade_ms / 1000)
    if fade_n > 0 and len(trimmed) > fade_n:
        t = np.linspace(0.0, np.pi / 2, fade_n, dtype=np.float32)
        trimmed[-fade_n:] *= np.cos(t) ** 2
    return trimmed


def guard_chunk(
    wav,
    sample_rate: int,
    regen_fn,
    chunk_text: str,
    min_chars: int = 100,
    max_depth: int = 2,
    depth: int = 0,
    log=print,
) -> np.ndarray:
    """对已生成的一段 wav 做幻觉守卫：

    1. 始终先 trim_internal_silence（裁段内长静音/幻觉尾）；
    2. 若 regen_fn 非 None 且本段文本足够长（>min_chars）且裁剪后仍检出
       runaway 形态 → 把文本对半拆开分别重生成（递归 depth+1，上限 max_depth），
       再拼接返回。regen_fn(t) 需返回「文本 t 的生成结果 wav」（闭包携带该段
       相同的音色/参考参数）。
    3. 不允许/无需重试时（regen_fn 为 None、段太短、已达递归上限）直接返回裁剪结果。
    """
    wav = trim_internal_silence(wav, sample_rate)
    if (
        regen_fn is not None
        and depth < max_depth
        and len(chunk_text or "") > min_chars
        and has_runaway(wav, sample_rate)
    ):
        mid = len(chunk_text) // 2
        log(
            f"[守卫] 本段({len(chunk_text)}字)检出「语音→长静音→语音」跑飞形态，"
            f"自动对半拆段重生成（第 {depth + 1}/{max_depth} 层）"
        )
        left = guard_chunk(
            regen_fn(chunk_text[:mid]),
            sample_rate,
            regen_fn,
            chunk_text[:mid],
            min_chars,
            max_depth,
            depth + 1,
            log,
        )
        right = guard_chunk(
            regen_fn(chunk_text[mid:]),
            sample_rate,
            regen_fn,
            chunk_text[mid:],
            min_chars,
            max_depth,
            depth + 1,
            log,
        )
        return np.concatenate([left, right]).astype(np.float32)
    return wav
