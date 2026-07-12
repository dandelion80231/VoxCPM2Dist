"""
VoxCPM2 Web UI — v5.0 一体化界面
================================
本地 Web 服务器 + 浏览器 UI，无需安装任何依赖（除了 voxcpm 自带的）。
启动后自动打开浏览器，访问 http://localhost:18978（端口被占用时自动顺延）

架构：
  - FastAPI HTTP API（模型常驻后台线程）
  - 嵌入 HTML/CSS/JS（单文件，无前端构建）
  - 异步任务队列（模型加载 + TTS 合成）
"""

import argparse
import asyncio
import datetime
import json
import os
import queue
import re
import shutil
import sys
import tempfile

# 内嵌版 Python(python_cuda)不会把脚本所在目录加入 sys.path，
# 手动加入以便导入同级模块（text_norm_cn 等），否则双击 .bat 会因
# ModuleNotFoundError 静默崩溃
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

# ── 依赖检查 ─────────────────────────────────────────────
try:
    import soundfile as sf
    HAS_SF = True
except ImportError:
    HAS_SF = False

try:
    import uvicorn
    from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
    import webbrowser
    HAS_WEB = True
except ImportError:
    HAS_WEB = False

# ── 引擎核心（内嵌，避免导入整个 v5 脚本的环境问题）─────────
MODEL_ID = "openbmb/VoxCPM2"
MAX_CHUNK_SIZE = 240
executor = ThreadPoolExecutor(max_workers=2)

# ── 音色预设 ─────────────────────────────────────────────
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

# 示例 / 方言音色芯片（点击填入音色描述，便于新手）
EXAMPLE_VOICES = [
    ("温柔忧郁女孩", "温柔忧郁的女孩，声音轻柔带一丝哀伤"),
    ("深宫太后", "威严的古代太后，庄重缓慢，自带威压"),
    ("暴躁驾校教练", "暴躁的驾校教练，语速快、语气冲、爱吐槽"),
    ("阳光少年", "阳光开朗的少年，活力十足，语速轻快"),
    ("新闻男主播", "沉稳的新闻男主播，字正腔圆，语速平缓"),
    ("睡前故事姐姐", "温柔的睡前故事姐姐，舒缓轻柔，令人放松"),
    ("粤语少女", "自然亲切的粤语年轻女性"),
    ("河南大叔", "朴实憨厚的河南方言大叔"),
]

# ── 状态 ─────────────────────────────────────────────────
state_lock = threading.Lock()
_cached_model = None
_model_loading = False
_model_loaded = False
_denoiser_available = False
_model_error: Optional[str] = None
_device_pref: Optional[str] = None  # None=自动检测; 'cuda'/'cpu'=用户指定
CONFIG_PATH = Path(__file__).resolve().parent / "voxcpm_web_config.json"

# 全局合成速度统计（用于更准确地预估剩余时间）
_avg_lock = threading.Lock()
_global_avg_seconds_per_char: float = 0.0
_output_dir = Path(os.environ.get("VOXCPM_OUTPUT_DIR", str(Path.home() / "Desktop")))


# 启动时从配置文件恢复路径
def _load_config():
    global _output_dir
    try:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if cfg.get("output_dir"):
                _output_dir = Path(cfg["output_dir"])
            if cfg.get("model_dir"):
                os.environ["VOXCPM_MODEL_DIR"] = cfg["model_dir"]
    except Exception:
        pass


def _save_config():
    try:
        model_dir = os.environ.get("VOXCPM_MODEL_DIR", "")
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "output_dir": str(_output_dir),
                "model_dir": model_dir,
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[VoxCPM2] 配置保存失败: {e}")


# ── 控制台显示/隐藏（Windows）────────────────────────────────
# 注意：Windows Terminal 等现代终端可能不允许子进程彻底隐藏窗口，
# 因此会先尝试隐藏；若仍可见则回退到最小化，至少把窗口移出屏幕。
_console_visible: bool = True

def _get_console_hwnd() -> Optional[int]:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        return ctypes.windll.kernel32.GetConsoleWindow()
    except Exception:
        return None

def _is_console_visible() -> bool:
    """返回命令行窗口的真实可见状态（最小化也视为不可见）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        hwnd = _get_console_hwnd()
        if not hwnd:
            return False
        visible = ctypes.windll.user32.IsWindowVisible(hwnd)
        minimized = ctypes.windll.user32.IsIconic(hwnd)
        return bool(visible) and not bool(minimized)
    except Exception:
        return False

def _set_console_visible(visible: bool) -> bool:
    global _console_visible
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        import threading
        hwnd = _get_console_hwnd()
        if not hwnd:
            return False
        # 在主线程里用同步 ShowWindow 更可靠；后台线程用 ShowWindowAsync
        if threading.current_thread() is threading.main_thread():
            show = ctypes.windll.user32.ShowWindow
        else:
            show = ctypes.windll.user32.ShowWindowAsync
        SW_HIDE = 0
        SW_SHOW = 5
        SW_MINIMIZE = 6
        SW_RESTORE = 9
        if visible:
            show(hwnd, SW_RESTORE)
            show(hwnd, SW_SHOW)
        else:
            show(hwnd, SW_HIDE)
            # 部分终端（Windows Terminal）会忽略 SW_HIDE，此时回退为最小化
            if threading.current_thread() is threading.main_thread():
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    show(hwnd, SW_MINIMIZE)
            else:
                # 后台线程用异步 API，需等消息队列处理后再检查
                time.sleep(0.15)
                if ctypes.windll.user32.IsWindowVisible(hwnd):
                    show(hwnd, SW_MINIMIZE)
        _console_visible = _is_console_visible()
        return True
    except Exception as e:
        print(f"[VoxCPM2] 控制台显示/隐藏失败: {e}")
        return False

def _toggle_console() -> bool:
    # 以真实窗口状态为准，避免内部状态与实际窗口不同步
    return _set_console_visible(not _is_console_visible())

_load_config()

# ── 任务队列 ──────────────────────────────────────────────
task_queue = queue.Queue()
task_results: dict = {}
task_lock = threading.Lock()

TEMP_DIR = Path(tempfile.gettempdir()) / "voxcpm_web_ui"
TEMP_DIR.mkdir(parents=True, exist_ok=True)


# ══════════════════════════════════════════════════════════
#  引擎核心
# ══════════════════════════════════════════════════════════

def _resolve_zipenhancer_dir():
    """定位随包发布的 ZipEnhancer 降噪模型目录（离线，纯本地，不联网）。"""
    import sys
    anchors = []
    try:
        anchors.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    if getattr(sys, "frozen", False):
        # PyInstaller 冻结后，模型随 exe 目录或 _MEIPASS 解包
        try:
            anchors.append(os.path.dirname(os.path.abspath(sys.executable)))
        except Exception:
            pass
        if hasattr(sys, "_MEIPASS"):
            anchors.append(sys._MEIPASS)
    seen = set()
    for a in anchors:
        if a in seen:
            continue
        seen.add(a)
        cand = os.path.join(a, "models", "zipenhancer")
        if os.path.isdir(cand) and os.path.isfile(os.path.join(cand, "configuration.json")):
            return cand
        parent = os.path.dirname(a)
        cand2 = os.path.join(parent, "models", "zipenhancer")
        if os.path.isdir(cand2) and os.path.isfile(os.path.join(cand2, "configuration.json")):
            return cand2
    return None


def resolve_model_dir(local_path: str = "") -> str:
    """解析模型目录：优先用户指定，无效时回退到分发版自带本地权重，最后回退到 MODEL_ID。"""
    local_path = (local_path or os.environ.get("VOXCPM_MODEL_DIR", "")).strip()
    base = Path(__file__).resolve().parent

    user_candidates = []
    if local_path:
        user_candidates.append(local_path)
        user_candidates.append(os.path.join(local_path, "openbmb", "VoxCPM2"))

    default_candidates = [
        str(base.parent / "model" / "openbmb" / "VoxCPM2"),
        str(base / "model" / "openbmb" / "VoxCPM2"),
    ]

    # 1. 优先使用包含 config.json 的有效路径
    for p in user_candidates + default_candidates:
        if os.path.isfile(os.path.join(p, "config.json")):
            return p

    # 2. 没有有效 config.json 时回退到存在的目录（优先分发版默认路径，避免用户误选错误目录导致报错）
    for p in default_candidates + user_candidates:
        if os.path.isdir(p):
            return p

    # 3. 全都不存在：回退到 HF repo id（离线环境会失败，但报错路径明确）
    return MODEL_ID


def load_model(force_reload: bool = False):
    global _cached_model, _model_loading, _model_loaded, _model_error, _denoiser_available
    with state_lock:
        if _model_loaded and _cached_model is not None and not force_reload:
            return _cached_model
        if _model_loading:
            return None
        _model_loading = True
        _model_error = None

    model_path = resolve_model_dir()

    try:
        from voxcpm import VoxCPM
        # 离线降噪：若随包发布 zipenhancer 模型则启用，否则降级为空操作（保持离线安全）
        use_denoiser = False
        zpath = _resolve_zipenhancer_dir()
        if zpath:
            try:
                from voxcpm.zipenhancer import ZipEnhancer
                ZipEnhancer(zpath)  # 预加载验证（纯本地，不联网）
                use_denoiser = True
                print(f"[VoxCPM2] 离线降噪模型已启用: {zpath}")
            except Exception as e:
                print(f"[VoxCPM2] 降噪模型加载失败，降噪将不可用: {e}")
                use_denoiser = False
        _denoiser_available = use_denoiser
        # 运行设备：用户指定优先，否则自动检测
        import torch
        device = _device_pref if _device_pref else ("cuda" if torch.cuda.is_available() else "cpu")
        print(f"[VoxCPM2] 正在加载模型: {model_path} (device={device})")
        model = VoxCPM.from_pretrained(
            model_path,
            load_denoiser=use_denoiser,
            zipenhancer_model_id=zpath if use_denoiser else None,
            optimize=False,
            device=device
        )
        _cached_model = model
        with state_lock:
            _model_loaded = True
            _model_loading = False
        print("[VoxCPM2] 模型加载完成")
        return model
    except Exception as e:
        _model_error = str(e)
        with state_lock:
            _model_loading = False
        print(f"[VoxCPM2] 模型加载失败: {e}")
        raise


def unload_model():
    """手动卸载模型，释放显存/内存。"""
    global _cached_model, _model_loaded, _model_loading, _model_error
    with state_lock:
        _cached_model = None
        _model_loaded = False
        _model_loading = False
        _model_error = None
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    print("[VoxCPM2] 模型已卸载")


def _load_model_background(force: bool = False):
    """在后台线程中执行模型加载，供手动触发使用。"""
    try:
        load_model(force_reload=force)
    except Exception as e:
        print(f"[VoxCPM2] 手动加载模型失败: {e}")


def split_text(text: str, chunk_size: int = MAX_CHUNK_SIZE) -> list:
    if len(text) <= chunk_size:
        return [text]
    chunks = []
    sentences = re.split(r'([。！？；\.\!\?\;，,])', text)
    current = ""
    for i in range(0, len(sentences) - 1, 2):
        s = (sentences[i] or "") + (sentences[i + 1] if i + 1 < len(sentences) else "")
        if len(current) + len(s) <= chunk_size:
            current += s
        else:
            if current:
                chunks.append(current)
            current = s
    if current:
        chunks.append(current)
    return chunks


from text_norm_cn import normalize_text

def resample_audio(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """音频重采样（优先 librosa，回退 scipy/numpy 线性插值）。"""
    try:
        import librosa
        return librosa.resample(audio.astype(np.float32), orig_sr=sr_in, target_sr=sr_out)
    except Exception:
        pass
    try:
        from scipy.signal import resample as sp_resample
        n = int(round(len(audio) * sr_out / sr_in))
        return sp_resample(audio, n)
    except Exception:
        pass
    # 简单线性插值回退
    n = int(round(len(audio) * sr_out / sr_in))
    if n <= 1:
        return audio
    xp = np.linspace(0, len(audio) - 1, len(audio))
    x = np.linspace(0, len(audio) - 1, n)
    return np.interp(x, xp, audio).astype(audio.dtype)


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


def synthesize(args: dict) -> dict:
    """
    后台 TTS 任务函数。
    args: {
        job_id, text, voice, control, mode, reference_wav,
        prompt_wav, prompt_text, cfg, steps, normalize, crossfade, chunk_size
    }
    返回: {job_id, status, message, output_files, output_wav, duration, error}
    """
    global _output_dir, _global_avg_seconds_per_char
    job_id = args["job_id"]
    text = args["text"]
    voice = args.get("voice", "default")
    # 自定义音色描述优先；否则回退到左侧预设
    control_text = args.get("control_text")
    control = control_text or VOICE_PRESETS.get(voice, VOICE_PRESETS["default"])
    mode = args.get("mode", "voice_design")  # voice_design | fixed_clone | self_seeding
    reference_wav = args.get("reference_wav")
    prompt_wav = args.get("prompt_wav")
    prompt_text = args.get("prompt_text")
    cfg = float(args.get("cfg", 2.5))
    steps = int(args.get("steps", 15))
    normalize = str(args.get("normalize", "true")).lower() in ("true", "1", "yes", True)
    denoise = str(args.get("denoise", "false")).lower() in ("true", "1", "yes", True)
    prompt_text = args.get("prompt_text") or None  # 终极克隆：参考音频的转录文本
    crossfade = int(args.get("crossfade", 80))
    chunk_size = int(args.get("chunk_size", 180))
    target_sr = args.get("target_sr", "native")

    with task_lock:
        start_ts = task_results[job_id].get("start_time", time.time())
        est_total = task_results[job_id].get("estimated_total_seconds", 5.0)
        task_results[job_id] = {
            "status": "loading_model",
            "progress": 0,
            "display_progress": 0,
            "message": "正在加载模型...",
            "start_time": start_ts,
            "estimated_total_seconds": est_total,
            "elapsed_seconds": 0,
            "remaining_seconds": est_total,
        }

    try:
        model = load_model()
        if model is None:
            raise RuntimeError("模型加载失败")
    except Exception as e:
        with task_lock:
            task_results[job_id] = {"status": "error", "message": f"模型加载失败: {e}"}
        return task_results[job_id]

    # control 前缀在分段循环内按 chunk 拼接

    with task_lock:
        start_ts = task_results[job_id].get("start_time", time.time())
        est_total = task_results[job_id].get("estimated_total_seconds", 5.0)
        task_results[job_id] = {
            "status": "synthesizing",
            "progress": 5,
            "display_progress": 5,
            "message": "正在切分文本...",
            "start_time": start_ts,
            "estimated_total_seconds": est_total,
            "elapsed_seconds": time.time() - start_ts,
            "remaining_seconds": max(0, est_total - (time.time() - start_ts)),
        }

    chunks = split_text(text, chunk_size=chunk_size)
    total_chunks = len(chunks)

    # 多段 voice_design 自动走 self-seeding：用第一段固定音色作为后续段的参考，
    # 避免长文本每段都重新按 control 描述采样导致音色不一致。
    use_self_seeding = (mode == "self_seeding") or (mode == "voice_design" and total_chunks > 1)

    # 统一走分段循环（单段也走这里，确保 fixed_clone / self_seeding 对短文本同样生效）
    audio_segments = []
    current_ref = reference_wav if (reference_wav and os.path.exists(reference_wav)) else None
    seed_prompt_text = None
    seed_ref_path = None
    synthesis_elapsed_total = 0.0
    synthesis_chars_total = 0

    for i, chunk in enumerate(chunks, 1):
        # 进度按「已完成段数」计算：第一段开始前应为 5%，避免一起步就 50%+
        progress = int(5 + 80 * (i - 1) / total_chunks)
        with task_lock:
            now = time.time()
            r = task_results[job_id]
            start_ts = r.get("start_time", now)
            elapsed = now - start_ts
            estimated_total = r.get("estimated_total_seconds", max(5.0, len(text) * 0.12))
            # 根据实际耗时动态修正剩余时间
            if i > 1 and elapsed > 0:
                estimated_total = max(estimated_total, elapsed * total_chunks / (i - 1))
            r.update({
                "status": "synthesizing",
                "progress": progress,
                "display_progress": progress,
                "message": (f"正在合成第 {i}/{total_chunks} 段..." if total_chunks > 1 else "正在合成..."),
                "start_time": start_ts,
                "estimated_total_seconds": estimated_total,
                "elapsed_seconds": elapsed,
                "remaining_seconds": max(0, estimated_total - elapsed),
            })
            task_results[job_id] = r

        processed_chunk = normalize_text(chunk) if normalize else chunk
        chunk_text = f"({control}){processed_chunk}" if control else processed_chunk
        chunk_start = time.time()
        try:
            if mode == "fixed_clone" and current_ref:
                # 固定参考克隆 / 终极克隆（参考音频 + 转录文本）
                kwargs = {
                    "text": processed_chunk,
                    "cfg_value": cfg,
                    "inference_timesteps": steps,
                    "reference_wav_path": current_ref,
                    "normalize": normalize,
                    "denoise": denoise,
                }
                if prompt_text and i == 1:
                    # 终极克隆：参考音频即 prompt，配用户提供的转录文本
                    kwargs["prompt_wav_path"] = current_ref
                    kwargs["prompt_text"] = prompt_text
                elif seed_prompt_text:
                    kwargs["prompt_wav_path"] = seed_ref_path
                    kwargs["prompt_text"] = seed_prompt_text
                wav = model.generate(**kwargs)
            elif use_self_seeding and i == 1:
                # 第一段 Voice Design
                wav = model.generate(text=chunk_text, cfg_value=cfg, inference_timesteps=steps, normalize=normalize, denoise=denoise)
                seed_prompt_text = chunk_text
                seed_ref_path = os.path.join(TEMP_DIR, f"seed_{job_id}.wav")
                sf.write(seed_ref_path, wav, model.tts_model.sample_rate)
                current_ref = seed_ref_path
            elif use_self_seeding and current_ref and seed_prompt_text:
                # 后续段：ref_continuation
                wav = model.generate(
                    text=processed_chunk,
                    cfg_value=cfg,
                    inference_timesteps=steps,
                    reference_wav_path=current_ref,
                    prompt_wav_path=seed_ref_path,
                    prompt_text=seed_prompt_text,
                    normalize=normalize,
                    denoise=denoise,
                )
            else:
                wav = model.generate(text=chunk_text, cfg_value=cfg, inference_timesteps=steps, normalize=normalize, denoise=denoise)

            chunk_elapsed = time.time() - chunk_start
            chunk_chars = len(processed_chunk)
            synthesis_elapsed_total += chunk_elapsed
            synthesis_chars_total += chunk_chars
            with _avg_lock:
                if synthesis_chars_total > 0:
                    avg = synthesis_elapsed_total / synthesis_chars_total
                    if _global_avg_seconds_per_char > 0:
                        _global_avg_seconds_per_char = _global_avg_seconds_per_char * 0.7 + avg * 0.3
                    else:
                        _global_avg_seconds_per_char = avg

            sr = model.tts_model.sample_rate
            audio_segments.append(wav)
        except Exception as e:
            with task_lock:
                task_results[job_id] = {"status": "error", "message": f"第 {i} 段合成失败: {e}"}
            return task_results[job_id]

    # 拼接
    if len(audio_segments) > 1:
        with task_lock:
            r = task_results[job_id]
            now = time.time()
            r.update({
                "status": "synthesizing",
                "progress": 90,
                "display_progress": 90,
                "message": "正在拼接音频...",
                "elapsed_seconds": now - r.get("start_time", now),
                "remaining_seconds": 0,
            })
            task_results[job_id] = r
        audio_segments = normalize_segments(audio_segments, target_mode="mean")
        merged = crossfade_concat(audio_segments, sr, fade_ms=crossfade)
        merged = peak_normalize(merged, peak=0.95)
    else:
        merged = audio_segments[0] if audio_segments else np.array([])

    # 输出采样率重采样（可选）
    out_sr = sr
    if target_sr and str(target_sr).lower() not in ("native", "none", ""):
        try:
            tgt = int(target_sr)
            if tgt > 0 and tgt != sr:
                merged = resample_audio(merged, sr, tgt)
                out_sr = tgt
        except Exception as e:
            print(f"[VoxCPM2] 重采样失败，使用原生采样率: {e}")

    duration = len(merged) / out_sr if len(merged) else 0

    # 保存（文件名使用 ASCII，避免中文名导致 FileResponse 头编码失败）
    safe_name = "voxcpm_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    out_dir = _output_dir / "VoxCPM_Outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_wav = out_dir / f"{safe_name}.wav"
    try:
        sf.write(str(out_wav), merged, out_sr)
    except Exception as e:
        # soundfile 不可用时的降级
        try:
            import wave
            with wave.open(str(out_wav), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes((merged * 32767).astype(np.int16).tobytes())
        except Exception as ew:
            with task_lock:
                task_results[job_id] = {"status": "error", "message": f"保存音频失败: {ew}"}
            return task_results[job_id]

    with task_lock:
        r = task_results[job_id]
        now = time.time()
        r.update({
            "status": "done",
            "progress": 100,
            "display_progress": 100,
            "message": f"合成完成！时长 {duration:.1f}s",
            "output_wav": out_wav.name,
            "duration": duration,
            "sample_rate": out_sr,
            "num_chunks": total_chunks,
            "elapsed_seconds": now - r.get("start_time", now),
            "remaining_seconds": 0,
        })
        task_results[job_id] = r

    return task_results[job_id]


def submit_task(args: dict) -> str:
    job_id = str(uuid.uuid4())[:8]
    args["job_id"] = job_id
    text = args.get("text", "")
    # 用历史每字符耗时预估，无历史则用保守默认值；未加载模型时预留加载时间
    with _avg_lock:
        per_char = _global_avg_seconds_per_char if _global_avg_seconds_per_char > 0 else 0.35
    base_load = 25.0 if not _model_loaded else 0.0
    estimated = max(10.0, base_load + len(text) * per_char)
    task_queue.put(args)
    with task_lock:
        task_results[job_id] = {
            "status": "queued",
            "progress": 0,
            "display_progress": 0,
            "message": "任务已排队",
            "start_time": time.time(),
            "estimated_total_seconds": estimated,
            "elapsed_seconds": 0,
            "remaining_seconds": estimated,
        }
    return job_id


# ── 后台工作线程 ──────────────────────────────────────────
def worker_loop():
    while True:
        args = task_queue.get()
        if args is None:
            break
        try:
            synthesize(args)
        except Exception as e:
            with task_lock:
                jid = args.get("job_id", "unknown")
                task_results[jid] = {"status": "error", "message": str(e)}


worker_thread = threading.Thread(target=worker_loop, daemon=True)
worker_thread.start()


# ══════════════════════════════════════════════════════════
#  Web UI（嵌入 HTML）
# ══════════════════════════════════════════════════════════
HTML_CONTENT = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VoxCPM2 语音合成</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #161b27;
    --surface2: #1e2535;
    --border: #2a3347;
    --accent: #6c8eff;
    --accent2: #4ecdc4;
    --text: #e8eaf0;
    --text2: #8892a8;
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #f87171;
  }
  /* 浅色主题 */
  [data-theme="light"] {
    --bg: #f4f6fb;
    --surface: #ffffff;
    --surface2: #eef1f7;
    --border: #d8deea;
    --accent: #3b65ff;
    --accent2: #0fa3a3;
    --text: #1a1f2b;
    --text2: #5b6678;
    --green: #16a34a;
    --yellow: #d97706;
    --red: #dc2626;
  }
  /* 主题切换平滑过渡（仅颜色类属性，保证 60fps） */
  body, header, .sidebar, .content, .param-card, .text-card, .ref-card,
  .envbar, .env-item, .voice-btn, .control-card, .history-card, .progress-card,
  .modal, .toast, .status-badge, .app-icon, .theme-switch, .custom-voice-box {
    transition: background 0.3s ease, color 0.3s ease, border-color 0.3s ease;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }

  /* ── 顶栏 ── */
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 8px 24px;
    min-height: 64px;
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    flex-shrink: 0;
  }
  .logo {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 1px;
  }
  .logo span { color: var(--text); font-weight: 400; }
  .header-left { display: flex; align-items: center; gap: 12px; }
  .header-sample-rate {
    display: flex; align-items: center; gap: 8px;
    font-size: 12px; color: var(--text2);
    padding: 6px 10px;
    border: 1px solid var(--border); border-radius: 8px;
    background: var(--surface2);
    margin-left: 4px;
  }
  .header-sample-rate .k { color: var(--text2); }
  .header-env {
    display: flex; flex-wrap: wrap; gap: 6px;
    align-items: center;
    margin-left: auto;
    margin-right: 4px;
  }
  .app-icon {
    width: 36px; height: 36px;
    border-radius: 9px;
    object-fit: contain;
    flex-shrink: 0;
  }
  .console-toggle {
    width: 30px; height: 30px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    font-size: 14px;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer;
    flex-shrink: 0;
    transition: background 0.15s, border-color 0.15s, transform 0.15s;
  }
  .console-toggle:hover { background: var(--accent); border-color: var(--accent); color: #fff; transform: scale(1.05); }
  .theme-switch {
    display: flex; align-items: center; gap: 2px;
    padding: 3px; border-radius: 10px;
    background: var(--surface2); border: 1px solid var(--border);
  }
  .theme-switch button {
    width: 30px; height: 28px; border-radius: 7px;
    border: none; background: transparent; cursor: pointer;
    font-size: 15px; display: flex; align-items: center; justify-content: center;
    transition: background 0.15s;
  }
  .theme-switch button.active { background: var(--accent); }
  .theme-switch input[type="color"] {
    width: 26px; height: 26px; padding: 0; border: none; background: none;
    border-radius: 6px; cursor: pointer; margin-left: 2px;
  }
  .header-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .status-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text2);
    padding: 4px 10px;
    border-radius: 20px;
    background: var(--surface2);
    border: 1px solid var(--border);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--text2);
    transition: background 0.3s;
  }
  .status-dot.ready { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .status-dot.loading { background: var(--yellow); animation: pulse 1s infinite; }
  .status-dot.error { background: var(--red); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  /* ── 主布局 ── */
  main {
    display: flex;
    flex: 1;
    overflow: hidden;
  }

  /* ── 左侧边栏 ── */
  .sidebar {
    width: 280px;
    flex-shrink: 0;
    border-right: 1px solid var(--border);
    padding: 20px 16px;
    overflow-y: auto;
    background: var(--surface);
  }
  .sidebar h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: var(--text2);
    margin-bottom: 12px;
  }
  .voice-grid {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .voice-btn {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    text-align: left;
    transition: all 0.15s;
    font-size: 13px;
  }
  .voice-btn:hover { border-color: var(--accent); background: rgba(108,142,255,0.08); transform: translateY(-1px); }
  .voice-btn.active {
    border-color: var(--accent);
    background: rgba(108,142,255,0.15);
    color: var(--accent);
  }
  .voice-icon {
    width: 34px; height: 34px;
    border-radius: 10px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
    flex-shrink: 0;
    transition: transform 0.15s, box-shadow 0.15s;
    box-shadow: 0 3px 8px rgba(108,142,255,0.25);
  }
  .voice-btn:hover .voice-icon { transform: translateY(-1px) scale(1.05); box-shadow: 0 5px 12px rgba(108,142,255,0.35); }
  .voice-btn.active .voice-icon { background: #fff; color: var(--accent); box-shadow: 0 4px 10px rgba(108,142,255,0.4); }
  .voice-name { font-weight: 500; }
  .voice-desc { font-size: 11px; color: var(--text2); margin-top: 1px; line-height: 1.3; }
  .voice-btn.active .voice-desc { color: rgba(108,142,255,0.7); }

  /* ── 主内容区 ── */
  .content {
    flex: 1;
    display: flex;
    flex-direction: column;
    padding: 24px;
    gap: 20px;
    overflow-y: auto;
  }

  /* ── 参数面板 ── */
  .params-row {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
  }
  .param-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 200px;
    flex: 1;
    transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
  }
  .param-card:hover { border-color: rgba(108,142,255,0.4); box-shadow: 0 6px 18px rgba(0,0,0,0.08); }
  .param-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 12px; min-height: 36px; }
  .param-title { display: flex; align-items: baseline; gap: 10px; flex: 1; min-width: 0; }
  .param-label {
    font-size: 15px;
    font-weight: 700;
    color: var(--text);
    text-transform: uppercase;
    letter-spacing: 1px;
    flex-shrink: 0;
  }
  .param-value {
    font-size: 28px;
    font-weight: 800;
    color: var(--accent);
    line-height: 1;
  }
  .param-desc { font-size: 12px; color: var(--text2); white-space: nowrap; text-align: right; }
  .param-desc-wrap { display: flex; flex-direction: column; align-items: flex-end; text-align: right; gap: 1px; }
  .param-desc-line { font-size: 12px; color: var(--text2); line-height: 1.35; }
  .param-desc-right { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }
  .param-toggle {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 10px; border-radius: 20px;
    background: var(--surface2); border: 1px solid var(--border);
    font-size: 12px; color: var(--text2); cursor: pointer;
    transition: all 0.15s; user-select: none;
  }
  .param-toggle:hover { border-color: var(--accent); color: var(--accent); }
  .param-toggle input { width: 14px; height: 14px; accent-color: var(--accent); cursor: pointer; }
  input[type="range"] {
    width: 100%;
    accent-color: var(--accent);
    cursor: pointer;
    -webkit-appearance: none; appearance: none;
    height: 6px; border-radius: 3px;
    background: var(--surface2); outline: none;
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
    transition: transform 0.1s, box-shadow 0.1s;
  }
  input[type="range"]::-webkit-slider-thumb:hover { transform: scale(1.1); box-shadow: 0 3px 10px rgba(108,142,255,0.5); }
  input[type="range"]::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
  }
  input[type="range"]::-moz-range-progress { background: var(--accent); height: 6px; border-radius: 3px; }

  /* ── 文本输入 ── */
  .text-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 1;
    min-height: 180px;
  }
  .text-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .text-card-header h3 { font-size: 14px; color: var(--text2); }
  .char-count { font-size: 12px; color: var(--text2); }
  .text-area-wrap {
    position: relative;
    flex: 1;
    display: flex;
    min-height: 120px;
  }
  .text-area-hint {
    position: absolute;
    right: 12px;
    bottom: 10px;
    font-size: 12px;
    color: var(--text2);
    pointer-events: none;
    opacity: 0.8;
  }
  textarea {
    flex: 1;
    background: transparent;
    border: none;
    outline: none;
    color: var(--text);
    font-size: 15px;
    line-height: 1.7;
    resize: none;
    font-family: inherit;
    padding: 0 0 26px 0;
  }
  textarea::placeholder { color: var(--text2); }
  textarea:not(:placeholder-shown) ~ .text-area-hint { display: none; }

  /* ── 参考音频上传 ── */
  .ref-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
  }
  .ref-card h3 { font-size: 12px; color: var(--text2); margin-bottom: 10px; }
  .ref-modes {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .mode-btn {
    flex: 1;
    padding: 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text2);
    cursor: pointer;
    font-size: 12px;
    text-align: center;
    transition: all 0.15s;
  }
  .mode-btn:hover { border-color: var(--accent); color: var(--accent); }
  .mode-btn.active { border-color: var(--accent); background: rgba(108,142,255,0.15); color: var(--accent); }
  .upload-area {
    border: 2px dashed var(--border);
    border-radius: 8px;
    padding: 16px;
    text-align: center;
    cursor: pointer;
    transition: all 0.15s;
    color: var(--text2);
    font-size: 13px;
  }
  .upload-area:hover { border-color: var(--accent); background: rgba(108,142,255,0.05); }
  .upload-area.drag-over { border-color: var(--accent2); background: rgba(78,205,196,0.08); }
  .upload-area input { display: none; }
  .ref-info { margin-top: 8px; font-size: 12px; color: var(--green); display: none; }

  /* ── 底部操作栏 ── */
  .action-bar {
    display: flex;
    gap: 12px;
    align-items: center;
  }
  .btn-primary {
    flex: 1;
    height: 48px;
    border-radius: 10px;
    border: none;
    background: linear-gradient(135deg, var(--accent), #8b5cf6);
    color: #fff;
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    transition: opacity 0.15s, transform 0.1s;
  }
  .btn-primary:hover { opacity: 0.9; }
  .btn-primary:active { transform: scale(0.98); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary {
    height: 48px;
    padding: 0 20px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text);
    font-size: 14px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .btn-secondary:hover { border-color: var(--accent); color: var(--accent); }

  /* ── 进度条 ── */
  .progress-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    display: none;
  }
  .progress-card.visible { display: block; }
  .progress-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
  .progress-msg { font-size: 13px; color: var(--text2); }
  .progress-time { font-size: 11px; color: var(--text2); margin-top: 2px; opacity: 0.8; }
  .progress-pct { font-size: 13px; font-weight: 600; color: var(--accent); }
  .progress-bar-wrap {
    height: 6px;
    border-radius: 3px;
    background: var(--surface2);
    overflow: hidden;
  }
  .progress-bar-fill {
    height: 100%;
    border-radius: 3px;
    background: linear-gradient(90deg, var(--accent), var(--accent2));
    width: 0%;
    transition: width 0.3s;
  }

  /* ── 历史记录 ── */
  .history-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
    max-height: 280px;
    overflow-y: auto;
  }
  .history-card h3 { font-size: 12px; color: var(--text2); margin-bottom: 10px; }
  .history-empty { font-size: 13px; color: var(--text2); text-align: center; padding: 20px; }
  .history-item {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px;
    border-radius: 8px;
    border: 1px solid transparent;
    transition: all 0.15s;
    cursor: pointer;
  }
  .history-item:hover { background: var(--surface2); border-color: var(--border); }
  .history-play {
    width: 32px; height: 32px;
    border-radius: 50%;
    background: var(--accent);
    color: #fff;
    border: none;
    cursor: pointer;
    font-size: 13px;
    display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
    transition: background 0.15s;
  }
  .history-play:hover { background: var(--accent2); }
  .history-info { flex: 1; min-width: 0; }
  .history-text { font-size: 12px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .history-meta { font-size: 11px; color: var(--text2); margin-top: 2px; }
  .history-download {
    padding: 4px 10px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text2);
    font-size: 11px;
    cursor: pointer;
    text-decoration: none;
    transition: all 0.15s;
    flex-shrink: 0;
  }
  .history-download:hover { border-color: var(--accent); color: var(--accent); }

  /* ── 音频播放器 ── */
  audio { display: none; }

  /* ── Toast ── */
  .toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 12px 18px;
    font-size: 13px;
    color: var(--text);
    z-index: 1000;
    opacity: 0;
    transform: translateY(10px);
    transition: all 0.3s;
    pointer-events: none;
  }
  .toast.visible { opacity: 1; transform: none; }
  .toast.error { border-color: var(--red); color: var(--red); }
  .toast.success { border-color: var(--green); color: var(--green); }
  /* ── 参考音频试听 / 终极克隆 / 高级 ── */
  .ref-preview { margin-top: 10px; background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
  .ref-preview-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--text2); margin-bottom: 6px; }
  .ref-dur { color: var(--accent2); }
  .ref-warn { margin-top: 6px; font-size: 11px; color: var(--yellow); display: none; }
  .prompt-text-wrap { margin-top: 10px; }
  .prompt-text-input { width: 100%; min-height: 56px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); padding: 8px; font-size: 13px; font-family: inherit; resize: vertical; outline: none; }
  .prompt-text-input:focus { border-color: var(--accent); }
  .toggle-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; color: var(--text); cursor: pointer; }
  .toggle-row input { width: 16px; height: 16px; accent-color: var(--accent); }

  /* ── 环境信息面板 ── */
  .env-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-top: 16px; }
  .env-card h3 { font-size: 11px; text-transform: uppercase; letter-spacing: 1.5px; color: var(--text2); margin-bottom: 10px; }
  .env-row { display: flex; justify-content: space-between; gap: 8px; font-size: 12px; padding: 5px 0; border-bottom: 1px dashed var(--border); }
  .env-row:last-child { border-bottom: none; }
  .env-key { color: var(--text2); flex-shrink: 0; }
  .env-val { color: var(--text); text-align: right; word-break: break-all; }
  .env-val.good { color: var(--green); }
  .env-val.bad { color: var(--red); }

  /* ── 音色描述 + 示例芯片 ── */
  .control-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 16px;
  }
  .control-card h3 { font-size: 12px; color: var(--text2); margin-bottom: 8px; }
  .example-chips { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 6px; }
  .example-chip {
    padding: 5px 10px;
    border-radius: 14px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text2);
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .example-chip:hover { border-color: var(--accent); color: var(--accent); }

  /* ── 参考音频按钮行 ── */
  .ref-actions { display: flex; gap: 8px; margin-bottom: 10px; padding: 6px; border: 2px dashed transparent; border-radius: 8px; transition: all 0.15s; }
  .ref-actions.drag-over { border-color: var(--accent2); background: rgba(78,205,196,0.08); }
  #micBtn.recording { border-color: var(--red); color: var(--red); animation: pulse 1s infinite; }

  /* ── 设置弹窗 ── */
  .modal-mask {
    position: fixed; inset: 0;
    background: rgba(0,0,0,0.6);
    display: none; align-items: center; justify-content: center;
    z-index: 2000;
  }
  .modal {
    width: 540px; max-width: 92vw;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 20px;
    box-shadow: 0 20px 60px rgba(0,0,0,0.5);
  }
  .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
  .modal-head h3 { font-size: 15px; }
  .path-input {
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    color: var(--text);
    padding: 10px;
    font-size: 13px;
    font-family: inherit;
    outline: none;
  }
  .path-input:focus { border-color: var(--accent); }
  .path-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
  .path-row .btn-secondary { flex-shrink: 0; height: 38px; padding: 0 14px; font-size: 13px; }
  .modal-actions { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }

  /* ── 顶部环境栏（横向排列，现已并入 header）── */
  .envbar {
    display: flex; flex-wrap: wrap; gap: 6px;
    align-items: center;
  }
  .env-item {
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; padding: 4px 8px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--surface2);
    white-space: nowrap;
  }
  .env-item .k { color: var(--text2); }
  .env-item .v { color: var(--text); font-weight: 600; }
  .env-item .v.good { color: var(--green); }
  .env-item .v.bad { color: var(--red); }
  .env-dev-btns { display: flex; gap: 3px; }
  .env-dev-btns button {
    padding: 3px 8px; border-radius: 4px;
    border: 1px solid var(--border); background: transparent;
    color: var(--text2); cursor: pointer; font-size: 11px; transition: all 0.15s;
  }
  .env-dev-btns button.active { border-color: var(--accent); background: rgba(108,142,255,0.15); color: var(--accent); }
  .env-select {
    background: var(--bg); color: var(--text);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 3px 5px; font-size: 11px; outline: none; cursor: pointer;
  }
  .env-select:focus { border-color: var(--accent); }
  .env-select.good { color: var(--green); border-color: var(--green); }
  .env-select.bad { color: var(--red); border-color: var(--red); }

  /* ── 自定义音色保存框 ── */
  .custom-voice-box {
    margin-top: 12px;
    border: 1px dashed var(--border);
    border-radius: 10px;
    padding: 10px 12px;
    display: flex; flex-direction: column; gap: 8px;
  }
  .custom-voice-box .cv-title { font-size: 11px; color: var(--text2); text-transform: uppercase; letter-spacing: 1px; }
  .custom-voice-box input.cv-name {
    width: 100%; background: var(--bg); border: 1px solid var(--border);
    border-radius: 8px; color: var(--text); padding: 8px 10px; font-size: 13px;
    font-family: inherit; outline: none;
  }
  .custom-voice-box input.cv-name:focus { border-color: var(--accent); }
  .custom-voice-box .cv-hint { font-size: 11px; color: var(--text2); line-height: 1.4; }
  .custom-voice-box .cv-saved { font-size: 11px; color: var(--green); display: none; }
  .voice-btn.custom-save {
    justify-content: center; color: var(--accent); border-style: dashed;
  }
  .voice-btn.custom-save:hover { background: rgba(108,142,255,0.10); }

</style>
</head>
<body>

<header>
  <img src="/VoxCPM_App.ico" class="app-icon" alt="VoxCPM2">
  <button class="console-toggle" id="consoleToggle" onclick="toggleConsole()" title="显示/隐藏命令行窗口">🖥️</button>
  <div class="header-left">
    <div class="logo">Vox<span>CPM2</span></div>
    <div style="font-size:13px;color:var(--text2);">语音合成工具 v5.0</div>
  </div>
  <div class="header-sample-rate">
    <span class="k">采样率</span>
    <select class="env-select" id="envSr" onchange="setSampleRate(this.value)">
      <option value="native">模型原生</option>
      <option value="16000">16 kHz</option>
      <option value="22050">22.05 kHz</option>
      <option value="24000">24 kHz</option>
      <option value="44100">44.1 kHz</option>
      <option value="48000">48 kHz</option>
    </select>
  </div>
  <div class="envbar" id="envBar">
    <div class="env-item"><span class="k">Python</span><span class="v" id="envPy">-</span></div>
    <div class="env-item"><span class="k">运行设备</span><span class="env-dev-btns" id="envDev"><button data-dev="cuda" onclick="setDevice('cuda')">GPU</button><button data-dev="cpu" onclick="setDevice('cpu')">CPU</button></span></div>
    <div class="env-item">
      <span class="k">模型状态</span>
      <select class="env-select" id="envModel" onchange="handleModelAction(this.value)">
        <option value="status">未加载</option>
        <option value="load">加载模型</option>
      </select>
    </div>
    <div class="env-item"><span class="k">模型目录</span><span class="v" id="envModelDir" style="max-width:220px;overflow:hidden;text-overflow:ellipsis;">-</span></div>
    <div class="env-item"><span class="k">输出目录</span><span class="v" id="envOutDir" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;">-</span></div>
    <div class="env-item"><span class="k">音频保存于</span><span class="v" id="envOutSub" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;">-</span></div>
  </div>
  <div class="header-right">
    <div class="theme-switch" id="themeSwitch">
      <button data-theme="dark" onclick="setTheme('dark')" title="深色">🌙</button>
      <button data-theme="light" onclick="setTheme('light')" title="浅色">☀️</button>
      <input type="color" id="customBg" onchange="setCustomBg(this.value)" title="自定义背景色">
    </div>
    <button class="status-badge" style="cursor:pointer; border:1px solid var(--border); background:var(--surface2)" onclick="openSettings()" title="路径设置">⚙ 设置</button>
    <div class="status-badge">
      <div class="status-dot" id="modelDot"></div>
      <span id="modelStatus">未初始化</span>
    </div>
  </div>
</header>

<main>
  <!-- 左侧音色选择 -->
  <aside class="sidebar">
    <h3>音色预设</h3>
    <div class="voice-grid" id="voiceGrid">
      <!-- JS 填充 -->
    </div>
    <div class="custom-voice-box" id="customVoiceBox">
      <div class="cv-title">自定义音色</div>
      <input class="cv-name" id="customVoiceName" placeholder="音色名称，如「解说大叔」">
      <div class="cv-hint">将保存上方「音色描述」框中的当前内容</div>
      <button class="voice-btn custom-save" onclick="saveCustomVoice()">＋ 保存为预设</button>
      <div class="cv-saved" id="cvSaved">✅ 已保存</div>
    </div>
  </aside>

  <!-- 主内容 -->
  <div class="content">

    <!-- 参数调节 -->
    <div class="params-row">
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">CFG 强度</div>
            <div class="param-value" id="cfgVal">2.0</div>
          </div>
          <div class="param-desc-wrap">
            <div class="param-desc-line">控制音色一致性</div>
            <div class="param-desc-line">↑ 更稳定，↓ 更多变化</div>
          </div>
        </div>
        <input type="range" id="cfgSlider" min="1" max="3" step="0.1" value="2.0">
      </div>
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">推理步数</div>
            <div class="param-value" id="stepsVal">15</div>
          </div>
          <div class="param-desc">↑ 质量更好但更慢</div>
        </div>
        <input type="range" id="stepsSlider" min="5" max="30" step="1" value="15">
      </div>
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">淡入淡出</div>
            <div class="param-value" id="crossfadeVal">80ms</div>
          </div>
          <div class="param-desc">段间平滑过渡</div>
        </div>
        <input type="range" id="crossfadeSlider" min="0" max="200" step="10" value="80">
      </div>
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">高级参数</div>
            <div class="param-value" id="chunkVal">180</div>
          </div>
          <div class="param-desc-right">
            <div class="param-desc">分段长度（字）</div>
            <label class="param-toggle" title="数字归一化">
              <input type="checkbox" id="normalizeToggle" checked>
              <span>数字归一化</span>
            </label>
          </div>
        </div>
        <input type="range" id="chunkSlider" min="60" max="400" step="20" value="180">
      </div>
    </div>

    <!-- 文本输入 -->
    <div class="text-card">
      <div class="text-card-header">
        <h3>待合成文本</h3>
        <div style="display:flex;gap:8px;align-items:center;">
          <span class="char-count" id="charCount">0 字符</span>
          <button class="mode-btn" id="txtUploadBtn" onclick="document.getElementById('txtFileInput').click()" style="padding:4px 10px;font-size:11px;cursor:pointer;">📄 上传TXT</button>
          <input type="file" id="txtFileInput" accept=".txt,text/plain" style="display:none">
        </div>
      </div>
      <div class="text-area-wrap">
        <textarea id="textInput" placeholder="在此输入要合成语音的文本..."></textarea>
        <div class="text-area-hint">或使用上方「上传TXT」按钮加载文本文件</div>
      </div>
    </div>

    <!-- 音色描述 + 示例 -->
    <div class="control-card">
      <h3>音色描述（可选，留空使用左侧预设；也可写方言/角色）</h3>
      <textarea id="controlText" class="prompt-text-input" placeholder="例如：25岁温柔甜美女声，带一点播音腔。或『深宫太后，威严庄重』『河南方言大叔』"></textarea>
      <div class="example-chips" id="exampleChips"></div>
    </div>

    <!-- 参考音频 -->
    <div class="ref-card">
      <h3>音色统一模式（可选）</h3>
      <div class="ref-modes">
        <button class="mode-btn active" data-mode="voice_design" onclick="setMode('voice_design', this)">
          音色设计<br><span style="font-size:10px;color:var(--text2)">文字描述音色</span>
        </button>
        <button class="mode-btn" data-mode="fixed_clone" onclick="setMode('fixed_clone', this)">
          固定参考克隆<br><span style="font-size:10px;color:var(--text2)">上传/录制参考音频</span>
        </button>
        <button class="mode-btn" data-mode="self_seeding" onclick="setMode('self_seeding', this)">
          自播种<br><span style="font-size:10px;color:var(--text2)">首段设计后续克隆</span>
        </button>
      </div>
      <div class="ref-actions" id="refActions" style="display:none">
        <button class="mode-btn" style="flex:1" onclick="document.getElementById('refFile').click()">📂 上传参考音频</button>
        <button class="mode-btn" id="micBtn" style="flex:1" onclick="toggleRecord()">🎤 录音</button>
        <input type="file" id="refFile" accept=".wav,.mp3,audio/*" style="display:none">
      </div>
      <div class="ref-info" id="refInfo"></div>
      <div class="ref-preview" id="refPreview" style="display:none">
        <div class="ref-preview-head">
          <span>🎧 参考音频试听</span>
          <span class="ref-dur" id="refDur"></span>
        </div>
        <audio id="refAudio" controls preload="metadata" style="width:100%"></audio>
        <div class="ref-warn" id="refWarn"></div>
      </div>
      <div class="prompt-text-wrap" id="promptWrap" style="display:none">
        <label class="param-label">参考音频文本（终极克隆，可选）</label>
        <textarea id="promptText" class="prompt-text-input" placeholder="填入参考音频的原文转录，可显著提升音色相似度与稳定性（留空则为普通固定克隆）" oninput="onPromptInput()"></textarea>
        <div class="ref-warn" id="ultimateHint" style="display:none">⚠ 终极克隆模式：将忽略上方「音色描述」，以参考音频 + 文本还原音色。</div>
      </div>
      <label class="toggle-row" id="denoiseRow">
        <input type="checkbox" id="denoiseToggle">
        <span>启用降噪（denoise，内置离线降噪模型，勾选即生效；未内置时为空操作）</span>
      </label>
    </div>

    <!-- 进度 -->
    <div class="progress-card" id="progressCard">
      <div class="progress-header">
        <div>
          <span class="progress-msg" id="progressMsg">正在合成...</span>
          <div class="progress-time" id="progressTime">已运行 00:00 / 预计剩余 --:--</div>
        </div>
        <span class="progress-pct" id="progressPct">0%</span>
      </div>
      <div class="progress-bar-wrap">
        <div class="progress-bar-fill" id="progressBar"></div>
      </div>
    </div>

    <!-- 操作栏 -->
    <div class="action-bar">
      <button class="btn-secondary" onclick="clearText()">清空文本</button>
      <button class="btn-primary" id="synthBtn" onclick="doSynthesize()">
        <span id="synthBtnIcon">🔊</span>
        <span id="synthBtnText">开始合成</span>
      </button>
    </div>

    <!-- 历史 -->
    <div class="history-card">
      <h3>最近合成记录</h3>
      <div id="historyList">
        <div class="history-empty">暂无记录</div>
      </div>
    </div>

  </div>
</main>

<audio id="audioPlayer"></audio>
<div class="toast" id="toast"></div>

<!-- 设置弹窗 -->
<div class="modal-mask" id="settingsModal">
  <div class="modal">
    <div class="modal-head">
      <h3>路径设置</h3>
      <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="closeSettings()">✕</button>
    </div>
    <div class="param-label">模型权重目录（VOXCPM_MODEL_DIR）</div>
    <div class="path-row">
      <input id="modelDirInput" class="path-input" placeholder="如 C:\Program Files\VoxCPM2 TTS\model">
      <button class="btn-secondary" onclick="selectFolder('modelDirInput','选择模型权重目录')">浏览...</button>
    </div>
    <div class="param-desc">模型下载/读取位置；修改后下次合成将重新加载模型。</div>
    <div class="param-label" style="margin-top:14px">音频输出目录（VOXCPM_OUTPUT_DIR）</div>
    <div class="path-row">
      <input id="outputDirInput" class="path-input" placeholder="如 D:\VoxCPM_Outputs">
      <button class="btn-secondary" onclick="selectFolder('outputDirInput','选择音频输出目录')">浏览...</button>
    </div>
    <div class="param-desc">合成音频保存于此目录下的 VoxCPM_Outputs。修改立即生效。</div>
    <div class="modal-actions">
      <button class="btn-secondary" onclick="closeSettings()">取消</button>
      <button class="btn-primary" style="flex:0 0 auto; padding:0 22px; height:40px" onclick="savePaths()">保存</button>
    </div>
  </div>
</div>

<script>
// ── 全局状态 ─────────────────────────────────────
let selectedVoice = 'default';
let currentMode = 'voice_design';
let refFile = null;
let recState = null;
let pollingInterval = null;
let currentJobId = null;
let history = [];

// ── 初始化 ─────────────────────────────────────
const VOICE_LIST = {
  default: { icon: '🎤', name: '默认音色', desc: '25岁温柔女声' },
  sweet_girl: { icon: '👧', name: '甜美女孩', desc: '25岁温柔女声，播音腔' },
  warm_woman: { icon: '👩', name: '温柔女性', desc: '温柔甜美，语速适中' },
  gentleman: { icon: '👨', name: '温雅绅士', desc: '中年男性，温润儒雅' },
  energetic_broadcaster: { icon: '🎙️', name: '热情播音', desc: '低沉磁性，男性播音' },
  elder_woman: { icon: '👵', name: '慈祥老人', desc: '老年女性，语速缓慢' },
  cool_guy: { icon: '😎', name: '酷感男生', desc: '年轻男性，低沉冷静' },
  cheerful_girl: { icon: '😊', name: '活泼女生', desc: '开朗活泼，语速偏快' },
  storyteller: { icon: '🧔', name: '故事大王', desc: '中年男性，深沉磁性' },
  calm_male: { icon: '👤', name: '沉稳男声', desc: '新闻播报风格' },
  teacher: { icon: '👩‍🏫', name: '教学老师', desc: '中年女性，清晰有力' },
};

const EXAMPLES = [
  ['温柔忧郁女孩', '温柔忧郁的女孩，声音轻柔带一丝哀伤'],
  ['深宫太后', '威严的古代太后，庄重缓慢，自带威压'],
  ['暴躁驾校教练', '暴躁的驾校教练，语速快、语气冲、爱吐槽'],
  ['阳光少年', '阳光开朗的少年，活力十足，语速轻快'],
  ['新闻男主播', '沉稳的新闻男主播，字正腔圆，语速平缓'],
  ['睡前故事姐姐', '温柔的睡前故事姐姐，舒缓轻柔，令人放松'],
  ['粤语少女', '自然亲切的粤语年轻女性'],
  ['河南大叔', '朴实憨厚的河南方言大叔'],
];

async function init() {
  initTheme();
  initConsole();
  loadCustomVoices();
  renderVoices();
  renderExamples();
  loadHistory();
  bindSliders();
  bindTextArea();
  bindRefUpload();
  bindModelStatus();
  loadPaths();
  // 默认预设填入音色描述
  const defBtn = document.querySelector('.voice-btn[data-id="default"]');
  if (defBtn) selectVoice('default', defBtn);
  // 预热模型
  fetch('/api/ping').catch(() => {});
}

function renderVoices() {
  const grid = document.getElementById('voiceGrid');
  grid.innerHTML = '';
  const all = [...Object.entries(VOICE_LIST), ...CUSTOM_VOICES.map(v => [v.id, v])];
  for (const [id, v] of all) {
    const isCustom = typeof id === 'string' && id.startsWith('custom_');
    const btn = document.createElement('button');
    btn.className = 'voice-btn' + (id === selectedVoice ? ' active' : '') + (isCustom ? ' custom-preset' : '');
    btn.dataset.id = id;
    btn.onclick = () => selectVoice(id, btn);
    btn.innerHTML = `
      <div class="voice-icon">${v.icon}</div>
      <div>
        <div class="voice-name">${v.name}${isCustom ? ' <span style="font-size:10px;color:var(--text2)">★</span>' : ''}</div>
        <div class="voice-desc">${v.desc}</div>
      </div>`;
    grid.appendChild(btn);
  }
}

function renderExamples() {
  const box = document.getElementById('exampleChips');
  box.innerHTML = '';
  for (const [label, desc] of EXAMPLES) {
    const chip = document.createElement('button');
    chip.className = 'example-chip';
    chip.textContent = label;
    chip.title = desc;
    chip.onclick = () => {
      document.getElementById('controlText').value = desc;
      selectVoice('default', document.querySelector('.voice-btn[data-id="default"]'));
      setMode('voice_design', document.querySelector('.mode-btn[data-mode="voice_design"]'));
    };
    box.appendChild(chip);
  }
}

function selectVoice(id, btn) {
  selectedVoice = id;
  document.querySelectorAll('.voice-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // 将预设描述填入「音色描述」框，便于查看/微调
  const v = VOICE_LIST[id];
  if (v) document.getElementById('controlText').value = v.desc;
}

function setMode(mode, btn) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const isClone = mode === 'fixed_clone';
  document.getElementById('refActions').style.display = isClone ? 'flex' : 'none';
  document.getElementById('promptWrap').style.display = (isClone || mode === 'self_seeding') ? 'block' : 'none';
  document.getElementById('denoiseRow').style.display = 'flex';
  if (!isClone && mode !== 'self_seeding') {
    document.getElementById('refPreview').style.display = 'none';
    document.getElementById('refAudio').pause();
    document.getElementById('ultimateHint').style.display = 'none';
    document.getElementById('voiceGrid').style.opacity = '1';
  }
}

function onPromptInput() {
  const v = document.getElementById('promptText').value.trim();
  const ultimate = v.length > 0 && currentMode === 'fixed_clone';
  document.getElementById('voiceGrid').style.opacity = ultimate ? '0.4' : '1';
  document.getElementById('ultimateHint').style.display = ultimate ? 'block' : 'none';
}

function bindSliders() {
  const cfg = document.getElementById('cfgSlider');
  const steps = document.getElementById('stepsSlider');
  const xf = document.getElementById('crossfadeSlider');
  const chunk = document.getElementById('chunkSlider');
  cfg.oninput = () => document.getElementById('cfgVal').textContent = cfg.value;
  steps.oninput = () => document.getElementById('stepsVal').textContent = steps.value;
  xf.oninput = () => document.getElementById('crossfadeVal').textContent = xf.value + 'ms';
  chunk.oninput = () => document.getElementById('chunkVal').textContent = chunk.value;
}

function bindTextArea() {
  const ta = document.getElementById('textInput');
  ta.oninput = () => {
    document.getElementById('charCount').textContent = ta.value.length + ' 字符';
  };
  // TXT 文件上传处理
  const txtInput = document.getElementById('txtFileInput');
  txtInput.onchange = () => {
    const file = txtInput.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      ta.value = e.target.result;
      document.getElementById('charCount').textContent = ta.value.length + ' 字符';
      showToast('已加载: ' + file.name, 'success');
    };
    reader.onerror = () => showToast('文件读取失败', 'error');
    reader.readAsText(file, 'utf-8');
    txtInput.value = '';
  };
}

function bindRefUpload() {
  const inp = document.getElementById('refFile');
  const onPick = (file) => {
    if (!file) return;
    refFile = file;
    document.getElementById('refInfo').style.display = 'block';
    document.getElementById('refInfo').textContent = '✅ ' + file.name;
    previewRef(file);
  };
  inp.onchange = () => onPick(inp.files[0]);
  // 拖拽上传绑定到按钮行
  const area = document.getElementById('refActions');
  area.ondragover = e => { e.preventDefault(); area.classList.add('drag-over'); };
  area.ondragleave = () => area.classList.remove('drag-over');
  area.ondrop = e => {
    e.preventDefault();
    area.classList.remove('drag-over');
    const audioFile = Array.from(e.dataTransfer.files).find(f => f.type.startsWith('audio/') || /\.(wav|mp3)$/i.test(f.name));
    onPick(audioFile || e.dataTransfer.files[0]);
  };
}

// ── 麦克风录制（Web Audio PCM -> WAV）─────────────
async function toggleRecord() {
  const btn = document.getElementById('micBtn');
  if (recState && recState.recording) { stopRecord(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const AC = window.AudioContext || window.webkitAudioContext;
    const ac = new AC();
    const src = ac.createMediaStreamSource(stream);
    const node = ac.createScriptProcessor(4096, 1, 1);
    const gain = ac.createGain(); gain.gain.value = 0; // 静音输出，避免回授
    const chunks = [];
    node.onaudioprocess = (e) => {
      chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    };
    src.connect(node); node.connect(gain); gain.connect(ac.destination);
    recState = { recording: true, stream, ac, src, node, gain, chunks };
    btn.classList.add('recording');
    btn.textContent = '⏹ 停止录音';
    showToast('录音中...点击停止', 'success');
  } catch (e) {
    showToast('无法访问麦克风: ' + e.message, 'error');
  }
}

function stopRecord() {
  const s = recState; if (!s) return;
  try {
    s.node.disconnect(); s.gain.disconnect(); s.src.disconnect();
    s.stream.getTracks().forEach(t => t.stop());
    s.ac.close();
  } catch (e) {}
  let len = 0; s.chunks.forEach(c => len += c.length);
  const samples = new Float32Array(len);
  let off = 0; s.chunks.forEach(c => { samples.set(c, off); off += c.length; });
  const sr = s.ac.sampleRate;
  const wav = encodeWAV(samples, sr);
  const blob = new Blob([wav], { type: 'audio/wav' });
  const file = new File([blob], 'microphone_' + Date.now() + '.wav', { type: 'audio/wav' });
  refFile = file;
  document.getElementById('refInfo').style.display = 'block';
  document.getElementById('refInfo').textContent = '✅ 麦克风录音: ' + file.name;
  previewRef(file);
  const btn = document.getElementById('micBtn');
  btn.classList.remove('recording');
  btn.textContent = '🎤 录音';
  recState = null;
}

function encodeWAV(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
  writeStr(0, 'RIFF');
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, 'WAVE');
  writeStr(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeStr(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  let off = 44;
  for (let i = 0; i < samples.length; i++) {
    let s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    off += 2;
  }
  return view;
}

function previewRef(file) {
  const MAX = 50; // 参考音频时长上限（秒，对齐官方）
  const audio = document.getElementById('refAudio');
  const url = URL.createObjectURL(file);
  audio.src = url;
  document.getElementById('refPreview').style.display = 'block';
  audio.onloadedmetadata = () => {
    const dur = audio.duration || 0;
    document.getElementById('refDur').textContent = dur.toFixed(1) + 's';
    const warn = document.getElementById('refWarn');
    if (dur > MAX) {
      warn.style.display = 'block';
      warn.textContent = '⚠ 参考音频较长（>' + MAX + 's），克隆可能音色漂移，建议截取核心语句';
    } else {
      warn.style.display = 'none';
    }
  };
}

async function bindModelStatus() {
  await pollStatus();
  setInterval(pollStatus, 3000);
}

async function pollStatus() {
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    const dot = document.getElementById('modelDot');
    const txt = document.getElementById('modelStatus');
    const state = d.state || 'loading';
    dot.className = 'status-dot ' + state;
    const labels = { ready: '模型就绪', loading: '加载中...', error: '加载失败', idle: '未加载' };
    txt.textContent = labels[state] || state;

    // 同步顶部环境栏里的「模型状态」下拉
    renderModelState(state, state === 'ready');
  } catch {}
}

async function loadPaths() {
  try {
    const r = await fetch('/api/paths');
    const d = await r.json();
    renderEnvBar(d);
  } catch (e) {
    showToast('环境信息获取失败', 'error');
  }
}

// ── 设置弹窗 ─────────────────────────────────────
async function selectFolder(inputId, title) {
  try {
    const r = await fetch('/api/select_folder?title=' + encodeURIComponent(title));
    const d = await r.json();
    if (d.ok && d.path) {
      document.getElementById(inputId).value = d.path;
    } else if (d.error) {
      showToast(d.error, 'error');
    }
  } catch (e) {
    showToast('无法打开目录选择器: ' + e.message, 'error');
  }
}
async function openSettings() {
  try {
    const r = await fetch('/api/paths');
    const d = await r.json();
    document.getElementById('modelDirInput').value = (d.model_dir && d.model_dir.indexOf('未设置') < 0) ? d.model_dir : '';
    document.getElementById('outputDirInput').value = d.output_dir || '';
  } catch {}
  document.getElementById('settingsModal').style.display = 'flex';
}
function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }
async function savePaths() {
  const model_dir = document.getElementById('modelDirInput').value.trim();
  const output_dir = document.getElementById('outputDirInput').value.trim();
  try {
    const r = await fetch('/api/set_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_dir, output_dir })
    });
    const d = await r.json();
    if (d.ok) {
      showToast('路径已保存' + (model_dir ? '，下次合成将重载模型' : ''), 'success');
      closeSettings();
      loadPaths();
    } else {
      showToast(d.error || '保存失败', 'error');
    }
  } catch (e) {
    showToast('保存失败: ' + e.message, 'error');
  }
}

// ── 自定义音色（持久化到 localStorage）──
let CUSTOM_VOICES = [];
function loadCustomVoices() {
  try {
    CUSTOM_VOICES = JSON.parse(localStorage.getItem('voxcpm_custom_voices') || '[]');
  } catch { CUSTOM_VOICES = []; }
}
function saveCustomVoice() {
  const name = document.getElementById('customVoiceName').value.trim();
  const desc = document.getElementById('controlText').value.trim();
  if (!name) { showToast('请先输入音色名称', 'error'); return; }
  if (!desc) { showToast('「音色描述」为空，无法保存', 'error'); return; }
  const id = 'custom_' + Date.now();
  CUSTOM_VOICES.push({ id, name, desc, icon: '⭐' });
  try { localStorage.setItem('voxcpm_custom_voices', JSON.stringify(CUSTOM_VOICES)); } catch {}
  renderVoices();
  const btn = document.querySelector('.voice-btn[data-id="' + id + '"]');
  if (btn) selectVoice(id, btn);
  document.getElementById('customVoiceName').value = '';
  const saved = document.getElementById('cvSaved');
  saved.style.display = 'block';
  setTimeout(() => saved.style.display = 'none', 2000);
  showToast('已保存自定义音色：' + name, 'success');
}

// ── 顶部环境栏（横向）──
function renderModelState(state, isLoaded) {
  const sel = document.getElementById('envModel');
  if (!sel) return;
  const labels = { ready: '已加载', loading: '加载中...', error: '加载失败', idle: '未加载' };
  const label = labels[state] || '未加载';
  const options = [`<option value="status">${label}</option>`];
  if (state === 'idle' || state === 'error') {
    options.push('<option value="load">加载模型</option>');
  }
  if (state === 'ready' || state === 'loading') {
    options.push('<option value="unload">卸载模型</option>');
  }
  sel.innerHTML = options.join('');
  sel.className = 'env-select ' + (state === 'ready' ? 'good' : (state === 'error' ? 'bad' : ''));
}

async function handleModelAction(action) {
  if (action === 'load') {
    try {
      const r = await fetch('/api/load_model', { method: 'POST' });
      const d = await r.json();
      showToast(d.message || '已启动模型加载', d.ok ? 'info' : 'error');
    } catch (e) { showToast('加载请求失败: ' + e.message, 'error'); }
  } else if (action === 'unload') {
    try {
      const r = await fetch('/api/unload_model', { method: 'POST' });
      const d = await r.json();
      showToast(d.message || '模型已卸载', d.ok ? 'info' : 'error');
    } catch (e) { showToast('卸载请求失败: ' + e.message, 'error'); }
  }
  setTimeout(pollStatus, 200);
  setTimeout(loadPaths, 200);
}

function renderEnvBar(d) {
  document.getElementById('envPy').textContent = d.python_version || '-';
  const dev = (d.device || 'cpu').toLowerCase();
  document.querySelectorAll('#envDev button').forEach(b => {
    b.classList.toggle('active', b.dataset.dev === dev);
  });
  renderModelState(d.model_state || 'idle', d.model_loaded);
  const setTxt = (id, v) => {
    const el = document.getElementById(id);
    el.textContent = v || '-';
    el.title = v || '-';
  };
  setTxt('envModelDir', (d.model_dir && d.model_dir.indexOf('未设置') < 0) ? d.model_dir : '-');
  setTxt('envOutDir', d.output_dir);
  setTxt('envOutSub', d.output_subdir);
  // 采样率下拉：应用本地保存的输出采样率选择
  const srSel = document.getElementById('envSr');
  const savedSr = localStorage.getItem('voxcpm_sr');
  if (savedSr) srSel.value = savedSr;
}

// ── 命令行窗口显示/隐藏 ──
async function toggleConsole() {
  try {
    const r = await fetch('/api/toggle_console', { method: 'POST' });
    const d = await r.json();
    updateConsoleIcon(d.visible);
    if (d.ok) {
      showToast(d.message || (d.visible ? '命令行窗口已显示' : '命令行窗口已隐藏'), 'success');
    } else {
      showToast('命令行窗口切换失败，可能当前终端不支持此操作', 'error');
    }
  } catch {}
}
function updateConsoleIcon(visible) {
  const btn = document.getElementById('consoleToggle');
  if (!btn) return;
  btn.textContent = visible ? '🖥️' : '👁️';
  btn.title = visible ? '点击隐藏命令行窗口' : '点击显示命令行窗口';
}
async function initConsole() {
  try {
    const r = await fetch('/api/console_status');
    const d = await r.json();
    updateConsoleIcon(d.visible);
    // 非 Windows 平台隐藏该按钮
    if (d.supported === false) {
      const btn = document.getElementById('consoleToggle');
      if (btn) btn.style.display = 'none';
    }
  } catch {}
}

// ── 运行设备切换 ──
async function setDevice(dev) {
  try {
    const r = await fetch('/api/set_device', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ device: dev })
    });
    const d = await r.json();
    if (d.ok) {
      showToast('运行设备已切换为 ' + dev.toUpperCase() + '（下次合成生效）', 'success');
      loadPaths();
    } else {
      showToast(d.error || '切换失败', 'error');
    }
  } catch (e) {
    showToast('切换失败: ' + e.message, 'error');
  }
}

// ── 输出采样率切换（仅保存选择，下次合成生效）──
function setSampleRate(v) {
  localStorage.setItem('voxcpm_sr', v);
  showToast('输出采样率：' + (v === 'native' ? '模型原生' : (parseInt(v) / 1000) + ' kHz') + '（下次合成生效）', 'success');
}

// ── 主题切换（深色 / 浅色 / 自定义背景）──
function applyTheme(theme, customBg) {
  const root = document.documentElement;
  if (theme === 'custom' && customBg) {
    root.setAttribute('data-theme', 'custom');
    root.style.setProperty('--bg', customBg);
    const c = hexToRgb(customBg);
    const lum = (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255;
    const dark = lum < 0.5;
    root.style.setProperty('--text', dark ? '#e8eaf0' : '#1a1f2b');
    root.style.setProperty('--text2', dark ? '#8892a8' : '#5b6678');
    root.style.setProperty('--surface', mix(c, dark ? 255 : 0, 0.08));
    root.style.setProperty('--surface2', mix(c, dark ? 255 : 0, 0.14));
    root.style.setProperty('--border', mix(c, dark ? 255 : 0, 0.22));
  } else {
    root.setAttribute('data-theme', theme);
    ['--bg','--surface','--surface2','--border','--text','--text2'].forEach(v => root.style.removeProperty(v));
  }
  document.querySelectorAll('#themeSwitch button[data-theme]').forEach(b => {
    b.classList.toggle('active', b.dataset.theme === theme);
  });
  localStorage.setItem('voxcpm_theme', theme);
  if (theme === 'custom') localStorage.setItem('voxcpm_custom_bg', customBg);
}
function setTheme(t) { applyTheme(t); }
function setCustomBg(color) { applyTheme('custom', color); }
function initTheme() {
  const t = localStorage.getItem('voxcpm_theme') || 'dark';
  const bg = localStorage.getItem('voxcpm_custom_bg');
  applyTheme(t, bg);
}
function hexToRgb(hex) {
  hex = hex.replace('#', '');
  if (hex.length === 3) hex = hex.split('').map(x => x + x).join('');
  const n = parseInt(hex, 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}
function mix(c, target, amt) {
  const r = Math.round(c.r + (target - c.r) * amt);
  const g = Math.round(c.g + (target - c.g) * amt);
  const b = Math.round(c.b + (target - c.b) * amt);
  return 'rgb(' + r + ',' + g + ',' + b + ')';
}

function clearText() { document.getElementById('textInput').value = ''; document.getElementById('charCount').textContent = '0 字符'; }

// ── 合成 ─────────────────────────────────────
async function doSynthesize() {
  const text = document.getElementById('textInput').value.trim();
  if (!text) { showToast('请输入要合成的文本', 'error'); return; }

  const btn = document.getElementById('synthBtn');
  btn.disabled = true;
  document.getElementById('synthBtnIcon').textContent = '⏳';
  document.getElementById('synthBtnText').textContent = '合成中...';
  document.getElementById('progressCard').classList.add('visible');
  updateProgress(0, '正在提交任务...');

  const formData = new FormData();
  formData.append('text', text);
  formData.append('voice', selectedVoice);
  formData.append('control_text', document.getElementById('controlText').value.trim());
  formData.append('mode', currentMode);
  formData.append('cfg', document.getElementById('cfgSlider').value);
  formData.append('steps', document.getElementById('stepsSlider').value);
  formData.append('crossfade', document.getElementById('crossfadeSlider').value);
  formData.append('chunk_size', document.getElementById('chunkSlider').value);
  formData.append('normalize', document.getElementById('normalizeToggle').checked ? 'true' : 'false');
  formData.append('denoise', document.getElementById('denoiseToggle').checked ? 'true' : 'false');
  formData.append('target_sr', localStorage.getItem('voxcpm_sr') || 'native');
  const pt = document.getElementById('promptText').value.trim();
  if (pt) formData.append('prompt_text', pt);
  if (refFile && currentMode === 'fixed_clone') {
    formData.append('reference_wav', refFile);
  }

  try {
    const r = await fetch('/api/tts', { method: 'POST', body: formData });
    const d = await r.json();
    if (d.error) { showToast(d.error, 'error'); resetBtn(); return; }
    currentJobId = d.job_id;
    startPolling(d.job_id);
  } catch (e) {
    showToast('请求失败: ' + e.message, 'error');
    resetBtn();
  }
}

function startPolling(jobId) {
  if (pollingInterval) clearInterval(pollingInterval);
  pollingInterval = setInterval(() => pollJob(jobId), 800);
}

async function pollJob(jobId) {
  try {
    const r = await fetch('/api/status/' + jobId);
    const d = await r.json();
    updateProgress(d.display_progress || d.progress || 0, d.message || '处理中...', d.elapsed_seconds, d.remaining_seconds);
    if (d.status === 'done') {
      clearInterval(pollingInterval);
      onDone(d);
    } else if (d.status === 'error') {
      clearInterval(pollingInterval);
      showToast('合成失败: ' + d.message, 'error');
      resetBtn();
    }
  } catch {}
}

function updateProgress(pct, msg, elapsed, remaining) {
  document.getElementById('progressPct').textContent = pct + '%';
  document.getElementById('progressMsg').textContent = msg;
  document.getElementById('progressBar').style.width = pct + '%';
  const timeEl = document.getElementById('progressTime');
  if (!timeEl) return;
  const fmt = s => {
    if (typeof s !== 'number' || Number.isNaN(s)) return '--:--';
    const m = Math.floor(s / 60);
    const sec = Math.floor(s % 60);
    return m.toString().padStart(2, '0') + ':' + sec.toString().padStart(2, '0');
  };
  const eStr = fmt(elapsed);
  const rStr = fmt(remaining);
  timeEl.textContent = `已运行 ${eStr} / 预计剩余 ${rStr}`;
}

function onDone(d) {
  showToast('合成完成！时长 ' + (d.duration || 0).toFixed(1) + 's', 'success');
  addHistory({ text: document.getElementById('textInput').value.slice(0, 50), wav: d.output_wav, duration: d.duration });
  playAudio(d.output_wav);
  document.getElementById('progressCard').classList.remove('visible');
  resetBtn();
}

function resetBtn() {
  document.getElementById('synthBtn').disabled = false;
  document.getElementById('synthBtnIcon').textContent = '🔊';
  document.getElementById('synthBtnText').textContent = '开始合成';
}

// ── 音频播放 ─────────────────────────────────────
async function playAudio(wavName) {
  const player = document.getElementById('audioPlayer');
  player.src = '/api/audio/' + wavName;
  try { await player.play(); } catch {}
}

// ── 历史记录 ─────────────────────────────────────
function loadHistory() {
  try {
    history = JSON.parse(localStorage.getItem('voxcpm_history') || '[]');
    renderHistory();
  } catch {}
}

function addHistory(item) {
  history.unshift({ ...item, time: new Date().toLocaleTimeString() });
  if (history.length > 20) history = history.slice(0, 20);
  localStorage.setItem('voxcpm_history', JSON.stringify(history));
  renderHistory();
}

function renderHistory() {
  const list = document.getElementById('historyList');
  if (!history.length) { list.innerHTML = '<div class="history-empty">暂无记录</div>'; return; }
  list.innerHTML = history.map((h, i) => `
    <div class="history-item">
      <button class="history-play" onclick="playAudio('${h.wav}')">▶</button>
      <div class="history-info">
        <div class="history-text">${escHtml(h.text)}</div>
        <div class="history-meta">${h.time} · ${h.duration ? h.duration.toFixed(1) + 's' : ''}</div>
      </div>
      <a class="history-download" href="/api/audio/${h.wav}" download>下载</a>
    </div>`).join('');
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ── Toast ─────────────────────────────────────
let toastTimer = null;
function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast visible ' + type;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('visible'), 3000);
}

init();
</script>
</body>
</html>
"""


# ══════════════════════════════════════════════════════════
#  FastAPI 路由
# ══════════════════════════════════════════════════════════
if HAS_WEB:
    app = FastAPI(title="VoxCPM2 Web UI")

    @app.get("/")
    async def index():
        return HTMLResponse(content=HTML_CONTENT, media_type="text/html")

    @app.get("/VoxCPM_App.ico")
    async def serve_icon():
        base = Path(__file__).resolve().parent
        for cand in [base.parent / "VoxCPM_App.ico",
                     base.parent.parent / "installer" / "assets" / "VoxCPM_App.ico"]:
            if cand.exists():
                return FileResponse(str(cand))
        raise HTTPException(404, "icon not found")

    @app.get("/api/ping")
    async def ping():
        return {"ok": True}

    @app.get("/api/voices")
    async def list_voices():
        return JSONResponse({"voices": VOICE_PRESETS})

    @app.get("/api/paths")
    async def get_paths():
        import torch
        with state_lock:
            state = "loading" if _model_loading else ("ready" if _model_loaded else "idle")
            sr = _cached_model.tts_model.sample_rate if (_model_loaded and _cached_model) else None
            denoiser = _denoiser_available
        model_dir = resolve_model_dir()
        out_sub = _output_dir / "VoxCPM_Outputs"
        return JSONResponse({
            "app_dir": str(Path(__file__).resolve().parent),
            "model_dir": model_dir,
            "output_dir": str(_output_dir),
            "output_subdir": str(out_sub),
            "python_version": ".".join(map(str, sys.version_info[:3])),
            "cuda_available": torch.cuda.is_available(),
            "device": _device_pref if _device_pref else ("cuda" if torch.cuda.is_available() else "cpu"),
            "sample_rate": sr,
            "model_loaded": _model_loaded,
            "model_state": state,
            "denoiser_available": denoiser,
        })

    @app.get("/api/status")
    async def status():
        with state_lock:
            state = "loading" if _model_loading else ("ready" if _model_loaded else "idle")
            err = _model_error
        return JSONResponse({"state": state, "error": err})

    @app.post("/api/load_model")
    async def load_model_endpoint():
        with state_lock:
            if _model_loading:
                return JSONResponse({"ok": False, "error": "模型正在加载中，请稍候"})
            if _model_loaded and _cached_model is not None:
                return JSONResponse({"ok": True, "message": "模型已加载"})
        threading.Thread(target=lambda: _load_model_background(force=True), daemon=True).start()
        return JSONResponse({"ok": True, "message": "模型加载任务已启动"})

    @app.post("/api/unload_model")
    async def unload_model_endpoint():
        unload_model()
        return JSONResponse({"ok": True, "message": "模型已卸载"})

    @app.get("/api/console_status")
    async def console_status():
        return JSONResponse({"visible": _is_console_visible(), "supported": sys.platform == "win32"})

    @app.post("/api/toggle_console")
    async def toggle_console():
        ok = _toggle_console()
        visible = _is_console_visible()
        return JSONResponse({"ok": ok, "visible": visible, "message": "命令行窗口已显示" if visible else "命令行窗口已收起"})

    @app.get("/api/status/{job_id}")
    async def job_status(job_id: str):
        with task_lock:
            result = task_results.get(job_id, {"status": "not_found", "message": "任务不存在"})
            result = dict(result)
        status = result.get("status")
        if status in ("queued", "loading_model", "synthesizing"):
            now = time.time()
            start = result.get("start_time", now)
            elapsed = now - start
            estimated = result.get("estimated_total_seconds", elapsed + 1)
            # 如果实际耗时已接近或超过预估，动态放宽
            if estimated <= elapsed * 0.95:
                estimated = elapsed * 1.2
            actual = result.get("progress", 0)
            # 基于时间平滑模拟当前进度，让进度条每 1-5% 跳动
            simulated = min(89, 5 + 80 * elapsed / estimated) if estimated > 0 else actual
            display = min(89, max(actual, simulated))
            # 至少比上次显示多 1%，保证肉眼可见跳动
            last_display = result.get("display_progress", 0)
            display = min(89, max(int(display), last_display + 1))
            result["display_progress"] = display
            result["elapsed_seconds"] = elapsed
            result["remaining_seconds"] = max(0, estimated - elapsed)
        return JSONResponse(result)

    @app.post("/api/set_config")
    async def set_config(req: Request):
        global _output_dir
        try:
            data = await req.json()
        except Exception:
            data = {}
        model_dir = (data.get("model_dir") or "").strip()
        output_dir = (data.get("output_dir") or "").strip()
        if output_dir:
            try:
                p = Path(output_dir)
                p.mkdir(parents=True, exist_ok=True)
                _output_dir = p
            except Exception as e:
                return JSONResponse({"ok": False, "error": f"输出目录无效: {e}"})
        if model_dir:
            # 自动修正到包含 config.json 的有效路径；若用户选错目录，会回退到分发版默认路径
            resolved = resolve_model_dir(model_dir)
            os.environ["VOXCPM_MODEL_DIR"] = resolved
            # 触发下次合成重载模型
            with state_lock:
                _model_loaded = False
                _cached_model = None
                _model_loading = False
        _save_config()
        return JSONResponse({
            "ok": True,
            "model_dir": os.environ.get("VOXCPM_MODEL_DIR", ""),
            "output_dir": str(_output_dir),
        })

    @app.post("/api/set_device")
    async def set_device(req: Request):
        global _device_pref
        try:
            data = await req.json()
        except Exception:
            data = {}
        dev = (data.get("device") or "").strip().lower()
        if dev not in ("cuda", "cpu"):
            return JSONResponse({"ok": False, "error": "device 必须是 cuda 或 cpu"})
        _device_pref = dev
        # 触发下次合成在指定设备上重载模型
        with state_lock:
            _model_loaded = False
            _cached_model = None
            _model_loading = False
        return JSONResponse({"ok": True, "device": _device_pref})

    def _win_select_folder(title: str = "选择文件夹") -> Optional[str]:
        """Windows 原生文件夹选择对话框（ctypes，无需 tkinter）。"""
        if sys.platform != "win32":
            return None
        import ctypes
        from ctypes import wintypes

        BIF_RETURNONLYFSDIRS = 0x00000001
        BIF_NEWDIALOGSTYLE = 0x00000040

        class BROWSEINFO(ctypes.Structure):
            _fields_ = [
                ("hwndOwner", wintypes.HWND),
                ("pidlRoot", wintypes.LPCVOID),
                ("pszDisplayName", wintypes.LPWSTR),
                ("lpszTitle", wintypes.LPCWSTR),
                ("ulFlags", wintypes.UINT),
                ("lpfn", wintypes.LPCVOID),
                ("lParam", wintypes.LPARAM),
                ("iImage", wintypes.INT),
            ]

        Ole32 = ctypes.OleDLL("ole32")
        Shell32 = ctypes.windll.shell32
        Ole32.CoInitialize(None)
        try:
            bi = BROWSEINFO()
            display_name = ctypes.create_unicode_buffer(260)
            bi.hwndOwner = 0
            bi.pidlRoot = None
            bi.pszDisplayName = ctypes.cast(ctypes.addressof(display_name), wintypes.LPWSTR)
            bi.lpszTitle = title
            bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
            bi.lpfn = None
            bi.lParam = 0
            bi.iImage = 0
            pidl = Shell32.SHBrowseForFolderW(ctypes.byref(bi))
            if not pidl:
                return None
            path = ctypes.create_unicode_buffer(260)
            if Shell32.SHGetPathFromIDListW(pidl, path):
                Ole32.CoTaskMemFree(pidl)
                return path.value
            Ole32.CoTaskMemFree(pidl)
            return None
        finally:
            Ole32.CoUninitialize()

    @app.get("/api/select_folder")
    async def select_folder(title: str = "选择文件夹"):
        if sys.platform != "win32":
            return JSONResponse({"ok": False, "error": "本地目录选择仅支持 Windows"})
        loop = asyncio.get_event_loop()
        path = await loop.run_in_executor(None, _win_select_folder, title)
        return JSONResponse({"ok": bool(path), "path": path, "error": None if path else "未选择目录"})

    @app.post("/api/tts")
    async def tts_request(
        text: str = Form(...),
        voice: str = Form("default"),
        control_text: str = Form(""),
        mode: str = Form("voice_design"),
        cfg: float = Form(2.5),
        steps: int = Form(15),
        crossfade: int = Form(80),
        chunk_size: int = Form(180),
        normalize: str = Form("true"),
        denoise: str = Form("false"),
        target_sr: str = Form("native"),
        prompt_text: str = Form(""),
        reference_wav: UploadFile = File(None),
    ):
        if not text.strip():
            raise HTTPException(400, "文本不能为空")

        # 保存上传的参考音频
        ref_wav_path = None
        if reference_wav and mode == "fixed_clone":
            suffix = Path(reference_wav.filename).suffix or ".wav"
            ref_wav_path = str(TEMP_DIR / f"ref_{uuid.uuid4().hex[:8]}{suffix}")
            with open(ref_wav_path, "wb") as f:
                shutil.copyfileobj(reference_wav.file, f)

        job_id = submit_task({
            "text": text,
            "voice": voice,
            "control_text": control_text,
            "mode": mode,
            "cfg": cfg,
            "steps": steps,
            "crossfade": crossfade,
            "chunk_size": chunk_size,
            "normalize": normalize,
            "denoise": denoise,
            "target_sr": target_sr,
            "prompt_text": prompt_text,
            "reference_wav": ref_wav_path,
        })
        return JSONResponse({"job_id": job_id, "status": "queued"})

    @app.get("/api/audio/{filename}")
    async def serve_audio(filename: str):
        # 安全检查：只允许 TEMP_DIR 下的文件
        safe_name = os.path.basename(filename)
        audio_path = TEMP_DIR / safe_name
        if not audio_path.exists():
            audio_path = _output_dir / "VoxCPM_Outputs" / safe_name
        if not audio_path.exists():
            raise HTTPException(404, "文件不存在")
        return FileResponse(
            path=str(audio_path),
            media_type="audio/wav",
            filename=safe_name,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"}
        )


def run_server(port: int = 18978, host: str = "127.0.0.1", hide_console: bool = True):
    if not HAS_WEB:
        print("[错误] 缺少依赖: uvicorn, fastapi, starlette")
        print("请运行: pip install uvicorn fastapi")
        return

    import socket as _socket

    def _pick_port(p):
        for cand in range(p, p + 50):
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            try:
                _s.bind((host, cand))
                _s.close()
                return cand
            except OSError:
                _s.close()
        return None

    actual_port = _pick_port(port)
    if actual_port is None:
        print(f"\n[错误] 端口 {port} ~ {port + 49} 均被占用，无法启动服务器。")
        print("请关闭占用端口的程序，或换用其他起始端口后重试。")
        input("按回车键退出...")
        return
    if actual_port != port:
        print(f"提示：端口 {port} 已被占用，已自动改用端口 {actual_port}")

    port = actual_port
    url = f"http://{host}:{port}"
    print(f"\n{'='*50}")
    print(f"  VoxCPM2 Web UI 已启动")
    print(f"  访问地址: {url}")
    print(f"  按 Ctrl+C 停止服务器")
    print(f"{'='*50}\n")

    # 自动打开浏览器
    def open_browser():
        time.sleep(1.5)
        try:
            webbrowser.open(url)
        except Exception:
            pass

    threading.Thread(target=open_browser, daemon=True).start()

    # 启动后自动隐藏命令行窗口（Windows），网页按钮可随时重新显示
    # 仅当服务器确实启动成功后才隐藏，避免失败时被静默吞掉
    started = {"ok": False}
    try:
        app.add_event_handler("startup", lambda: started.__setitem__("ok", True))
    except Exception:
        pass
    if hide_console and sys.platform == "win32":
        def hide_later():
            time.sleep(2.5)
            if started["ok"]:
                _set_console_visible(False)
        threading.Thread(target=hide_later, daemon=True).start()

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except OSError as e:
        print(f"\n[错误] 无法在 {host}:{port} 启动服务器：{e}")
        print("该端口可能已被其他程序占用。请换用其他端口后重试，例如：")
        print(f"  python vox_web_ui.py --port 8010")
        input("按回车键退出...")


# ══════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VoxCPM2 Web UI — 本地语音合成")
    parser.add_argument("--port", type=int, default=18978, help="HTTP 端口 (默认 18978)")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址 (默认 127.0.0.1)")
    parser.add_argument("--model-dir", type=str, default="", help="本地模型目录")
    parser.add_argument("--output-dir", type=str, default="", help="输出目录")
    parser.add_argument("--show-console", action="store_true", help="保留命令行窗口显示（调试用）")
    args = parser.parse_args()

    if args.model_dir:
        os.environ["VOXCPM_MODEL_DIR"] = args.model_dir
    if args.output_dir:
        os.environ["VOXCPM_OUTPUT_DIR"] = args.output_dir

    run_server(port=args.port, host=args.host, hide_console=not args.show_console)
