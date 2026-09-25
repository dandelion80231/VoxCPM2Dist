"""
VoxCPM2 Web UI — 一体化界面（版本号见 app/version.txt）
================================
本地 Web 服务器 + 浏览器 UI，无需安装任何依赖（除了 voxcpm 自带的）。
启动后自动打开浏览器，访问 http://localhost:18978（端口被占用时自动顺延）

架构：
  - FastAPI HTTP API（模型常驻后台线程）
  - 嵌入 HTML/CSS/JS（单文件，无前端构建）
  - 异步任务队列（模型加载 + TTS 合成）
"""

# pyright: reportPossiblyUnboundVariable = none

import argparse
import asyncio
import datetime
import json
import os
import queue
import re
import shutil
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import numpy as np

# 内嵌版 Python(python_cuda)不会把脚本所在目录加入 sys.path，
# 手动加入以便导入同级模块（text_norm_cn 等），否则双击 .bat 会因
# ModuleNotFoundError 静默崩溃
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 允许从 app/ 根导入 download_model（与 Scripts/ 同级的下载脚本）
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
  sys.path.insert(0, _APP_ROOT)
try:
  import download_model as _dlmod

  HAS_DL = True
except Exception as _e_dl:  # 极端情况（如缺 urllib），仍保证 UI 可启动
  _dlmod = None
  HAS_DL = False
  print(f"[VoxCPM2] 模型下载模块不可用: {_e_dl}")

# qwen3 时间戳对齐模型管理（设置页可选下载/删除；不可用不影响主功能）
try:
  import voxcpm_timestamps_qwen as _tsq

  HAS_TS = True
except Exception as _e_ts:
  _tsq = None
  HAS_TS = False
  print(f"[VoxCPM2] qwen3 时间戳模块不可用: {_e_ts}")

_QWEN_MODEL_DIR_DEFAULT = os.path.join(_APP_ROOT, "models", "qwen3_aligner")
_QWEN_MODEL_DIR = os.environ.get("VOXCPM_TS_MODEL_DIR") or _QWEN_MODEL_DIR_DEFAULT
_qwen_lock = threading.Lock()
_qwen_state = {
  "status": "idle",  # idle | downloading | done | error | cancelled
  "file": None,
  "percent": None,
  "message": "",
  "started_at": None,
  "finished_at": None,
}
_qwen_thread: list = [None]

# 本地模块导入（sys.path 已设置，紧跟其后）
try:
  from text_norm_cn import normalize_text
except Exception:
  normalize_text = None

# 公共后端函数层（Web UI 与 CLI 共用；含语料/profile 管理，模型目录解析见 commit2）
try:
  import voxcpm_api

  HAS_API = True
except Exception as _e_api:
  HAS_API = False
  print(f"[VoxCPM2] 公共后端函数层不可用: {_e_api}")

# ── 依赖检查 ─────────────────────────────────────────────
try:
  import soundfile as sf

  HAS_SF = True
except ImportError:
  HAS_SF = False

try:
  import webbrowser

  import uvicorn
  from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
  from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

  HAS_WEB = True
except ImportError:
  HAS_WEB = False

# ── 引擎核心（内嵌，避免导入整个 v5 脚本的环境问题）─────────
MODEL_ID = "openbmb/VoxCPM2"
MAX_CHUNK_SIZE = 240
executor = ThreadPoolExecutor(max_workers=2)


# ── 总式数值转换（任何失败返回默认值，绝不抛异常）─────────────
def _safe_int(value, default: int = 0) -> int:
  try:
    return int(value)
  except (TypeError, ValueError, OverflowError):
    return default


def _safe_float(value, default: float = 0.0) -> float:
  try:
    return float(value)
  except (TypeError, ValueError, OverflowError):
    return default


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
_model_error: str | None = None
_device_pref: str | None = None  # None=自动检测; 'cuda'/'cpu'=用户指定
CONFIG_PATH = Path(__file__).resolve().parent / "voxcpm_web_config.json"

# 全局合成速度统计（用于更准确地预估剩余时间）
_avg_lock = threading.Lock()
_global_avg_seconds_per_char: float = 0.0
_output_dir = Path(os.environ.get("VOXCPM_OUTPUT_DIR", str(Path.home() / "Desktop")))

# 多音字 LoRA 权重路径（训练产物的 step_XXXXXXX 目录，或 lora_weights.safetensors/.ckpt 文件）。
# 留空 = 不挂载 LoRA，使用原版模型；设置后下次合成将重载模型并挂载 LoRA。
_lora_weights_path: str = ""
# LoRA 实际加载结果（模型加载后填充），供状态面板如实显示，避免「假成功」。
_lora_load_info: tuple | None = (
  None  # (loaded_count, skipped_count) 或 None（尚未加载/未配置）
)
_lora_resolve_error: str = ""  # resolve_lora 失败原因（路径错/配置坏等），空=未失败


# 启动时从配置文件恢复路径
def _load_config():
  global _output_dir, _lora_weights_path, _QWEN_MODEL_DIR
  try:
    if CONFIG_PATH.exists():
      with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
      if cfg.get("output_dir"):
        _output_dir = Path(cfg["output_dir"])
      if cfg.get("model_dir"):
        os.environ["VOXCPM_MODEL_DIR"] = cfg["model_dir"]
      if cfg.get("lora_weights_path"):
        _lora_weights_path = cfg["lora_weights_path"]
      if cfg.get("ts_model_dir"):
        _QWEN_MODEL_DIR = cfg["ts_model_dir"]
        os.environ["VOXCPM_TS_MODEL_DIR"] = cfg["ts_model_dir"]
  except Exception:
    _load_failed = True  # 有意吞没：配置文件缺失/损坏时保持默认路径


def _save_config():
  try:
    model_dir = os.environ.get("VOXCPM_MODEL_DIR", "")
    ts_dir = "" if _QWEN_MODEL_DIR == _QWEN_MODEL_DIR_DEFAULT else _QWEN_MODEL_DIR
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
      json.dump(
        {
          "output_dir": str(_output_dir),
          "model_dir": model_dir,
          "lora_weights_path": _lora_weights_path,
          "ts_model_dir": ts_dir,
        },
        f,
        ensure_ascii=False,
        indent=2,
      )
  except Exception as e:
    print(f"[VoxCPM2] 配置保存失败: {e}")


# ── 控制台显示/隐藏（Windows）────────────────────────────────
# 注意：Windows Terminal 等现代终端可能不允许子进程彻底隐藏窗口，
# 因此会先尝试隐藏；若仍可见则回退到最小化，至少把窗口移出屏幕。
_console_visible: bool = True


def _get_console_hwnd() -> int | None:
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


# ── 控制台窗口管理（切换始终可用）────────────────────
# 有原生控制台（如 .bat 启动）→ 直接显/隐它。
# 无原生控制台（后台/无头启动）→ 管理一个「日志 tail 窗口」（独立 CREATE_NEW_CONSOLE
# 窗口 tail 应用日志），保证任何启动方式下「显示/隐藏」都能用，点了不报错。
_tail_proc = None       # tail 窗口的 Popen
_tail_hwnd = 0          # tail 窗口 HWND（0=未找到）
_tail_visible = False   # tail 窗口当前是否显示
_TAIL_TITLE = "VoxCPM2_LOG_TAIL"


def _log_tail_path():
  """稳定应用日志（_install_diagnostics 必写），tail 窗口跟随它。"""
  return Path(__file__).resolve().parent.parent / "cache" / "web_ui.log"


def _open_tail_console() -> bool:
  global _tail_proc, _tail_hwnd, _tail_visible
  import ctypes
  import subprocess
  import time

  u32 = ctypes.windll.user32
  u32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
  u32.FindWindowW.restype = ctypes.c_void_p
  # 若之前开过且窗口还在 → 恢复显示
  if _tail_hwnd and u32.IsWindow(_tail_hwnd):
    u32.ShowWindow(_tail_hwnd, 9)  # SW_RESTORE
    u32.SetForegroundWindow(_tail_hwnd)
    _tail_visible = True
    return True
  log = _log_tail_path()
  try:
    if not log.exists():
      log.parent.mkdir(parents=True, exist_ok=True)
      log.touch()
    ps_cmd = (
      '$Host.UI.RawUI.WindowTitle = "{t}"; '
      'Get-Content -LiteralPath "{p}" -Wait -Encoding UTF8'
    ).format(t=_TAIL_TITLE, p=str(log))
    _tail_proc = subprocess.Popen(
      ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps_cmd],
      creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0x00000010),
      close_fds=True,
    )
  except Exception as e:
    print(f"[VoxCPM2] 打开日志窗口失败: {e}")
    _tail_proc = None
    return False
  # 按标题找 tail 窗口 HWND（powershell 启动稍慢，重试）
  for _ in range(25):
    time.sleep(0.2)
    h = u32.FindWindowW(None, _TAIL_TITLE)
    if h:
      _tail_hwnd = int(h)
      break
  _tail_visible = True
  return True


def _close_tail_console() -> bool:
  global _tail_proc, _tail_hwnd, _tail_visible
  # 结束 tail 进程 → 其 CREATE_NEW_CONSOLE 窗口自动关闭，不留残留
  try:
    if _tail_proc and _tail_proc.poll() is None:
      _tail_proc.terminate()
  except Exception:
    pass
  _tail_proc = None
  _tail_hwnd = 0
  _tail_visible = False
  return True


def _toggle_console() -> bool:
  # 切换始终可用：有原生控制台 → 显/隐它；没有 → 管理日志 tail 窗口。
  if sys.platform != "win32":
    return False
  if _get_console_hwnd():
    return _set_console_visible(not _is_console_visible())
  if not _tail_visible:
    return _open_tail_console()
  return _close_tail_console()


def _console_effectively_visible() -> bool:
  """用户可见的「控制台」（原生控制台或 tail 窗口）当前是否显示。"""
  if sys.platform != "win32":
    return False
  if _get_console_hwnd():
    return _is_console_visible()
  return _tail_visible


# ── 全局日志落盘 + 异常兜底 ──────────────────────────────
# 让进程无论以何种方式启动（bat / 快捷方式 / 直接运行）、无论控制台是否隐藏，
# 都保留最后的输出与崩溃堆栈，便于排查「点击下载后直接退出」这类无痕迹问题。
def _install_diagnostics():
  import traceback as _tb

  try:
    _log_dir = Path(__file__).resolve().parent.parent / "cache"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _logf = open(_log_dir / "web_ui.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115  (long-lived handle for module-lifetime _Tee logging; can't use `with` without re-indenting 4000+ lines)

    class _Tee:
      def __init__(self, *streams):
        self._streams = streams

      def write(self, s):
        for o in self._streams:
          try:
            o.write(s)
          except Exception:
            _stream_write_failed = True  # 某流写失败则跳过，继续写其余流

      def flush(self):
        for o in self._streams:
          try:
            o.flush()
          except Exception:
            _stream_flush_failed = True  # 某流刷失败则跳过

      def isatty(self):
        for o in self._streams:
          try:
            if o.isatty():
              return True
          except Exception:
            _stream_isatty_failed = True  # 某流 isatty 检查失败则继续检查其余流
        return False

      def fileno(self):
        for o in self._streams:
          try:
            return o.fileno()
          except Exception:
            _stream_fileno_failed = True  # 某流无 fileno 则继续，全部失败才抛 OSError
        raise OSError("no fileno")

    if sys.stdout is not None:
      sys.stdout = _Tee(sys.stdout, _logf)
    if sys.stderr is not None:
      sys.stderr = _Tee(sys.stderr, _logf)
  except Exception:
    _logf = None

  def _write_crash(tag, et, ev, tb):
    try:
      with open(
        Path(__file__).resolve().parent.parent / "cache" / "web_ui_crash.log",
        "a",
        encoding="utf-8",
      ) as f:
        f.write(f"\n=== {tag} @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        _tb.print_exception(et, ev, tb, file=f)
    except Exception:
      _exc_log_ok = False  # 日志写失败不能再抛异常，静默降级

  def _main_hook(et, ev, tb):
    _write_crash("未捕获异常(主线程)", et, ev, tb)
    _tb.print_exception(et, ev, tb)

  try:
    sys.excepthook = _main_hook
  except Exception:
    _main_hook_ok = False  # 极少发生；保留系统默认 excepthook

  def _thread_hook(args):
    _write_crash("线程未捕获异常", args.exc_type, args.exc_value, args.exc_traceback)
    # 若崩溃发生在下载线程，把前端状态标记为错误，避免一直转圈
    try:
      with _dl_lock:
        if _dl_state.get("status") in ("scanning", "downloading"):
          _dl_state["status"] = "error"
          _dl_state["message"] = f"下载线程异常: {args.exc_value}"
    except Exception:
      _dl_hook_ok = False  # 下载线程崩溃时标记前端状态，避免一直转圈；标记失败则静默

  try:
    threading.excepthook = _thread_hook
  except Exception:
    _thread_hook_ok = False  # 极少发生；保留默认线程钩子


_install_diagnostics()

_load_config()

# ── 任务队列 ──────────────────────────────────────────────
task_queue = queue.Queue()
task_results: dict = {}
task_lock = threading.Lock()

# 缓存目录默认放在安装目录下 cache/voxcpm_web_ui，而非系统临时目录。
# 原因：Windows 存储感知/磁盘清理常删除 AppData\Local\Temp 下的子目录，
# 导致 self-seeding 写种子或上传参考音频时报 "Error opening ...: System error"。
# __file__ 在打包后为 <安装根>/Scripts/vox_web_ui.py，.parent.parent 即安装根目录。
TEMP_DIR = Path(__file__).resolve().parent.parent / "cache" / "voxcpm_web_ui"
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
    _anchor_probe_ok = False  # 定位失败则尝试下一个锚点
  if getattr(sys, "frozen", False):
    # PyInstaller 冻结后，模型随 exe 目录或 _MEIPASS 解包
    try:
      anchors.append(os.path.dirname(os.path.abspath(sys.executable)))
    except Exception:
      _anchor_probe_ok = False  # 定位失败则尝试下一个锚点
    if hasattr(sys, "_MEIPASS"):
      _mepass = getattr(sys, "_MEIPASS", None)
      if _mepass:
        anchors.append(_mepass)
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
    if os.path.isdir(cand2) and os.path.isfile(
      os.path.join(cand2, "configuration.json")
    ):
      return cand2
  return None


_legacy_model_cache = ""
_legacy_model_scanned = False


def _find_legacy_model_dir() -> str:
  """自动迁移：扫描常见安装盘根（C:/D:/E: 第一层），寻找旧版安装目录里已下载的模型。

  no-model 版重新安装后默认目录没有权重，但用户之前（带模型版/旧安装目录）可能已下载过；
  找到即自动使用，避免“明明有模型却提示未检测到”。带缓存，盘根 listdir 毫秒级。
  返回有效模型目录（含 model.safetensors + config.json），找不到返回 ""。
  """
  global _legacy_model_cache, _legacy_model_scanned
  if _legacy_model_scanned:
    return _legacy_model_cache
  _legacy_model_scanned = True
  found = ""
  found_size = 0
  try:
    for letter in "CDEFGHIJKLNOPQRSTUWXYZ":
      drive = f"{letter}:\\"
      try:
        names = os.listdir(drive)
      except Exception:
        continue
      for name in names:
        root = os.path.join(drive, name)
        for c in (
          os.path.join(root, "model", "openbmb", "VoxCPM2"),
          os.path.join(root, "app", "model", "openbmb", "VoxCPM2"),
        ):
          if os.path.isfile(os.path.join(c, "model.safetensors")) and os.path.isfile(
            os.path.join(c, "config.json")
          ):
            try:
              sz = os.path.getsize(os.path.join(c, "model.safetensors"))
            except Exception:
              sz = 0
            if sz > found_size:
              found, found_size = c, sz
  except Exception:
    pass
  _legacy_model_cache = found
  if found:
    print(f"[VoxCPM2] 自动发现旧安装目录的模型：{found}（已启用）")
  return found


def resolve_model_dir(local_path: str = "") -> str:
  """解析模型目录：优先环境变量 VOXCPM_MODELS_DIR（新标准）→ VOXCPM_MODEL_DIR（兼容旧键）
  → 分发版自带本地权重 → 回退 MODEL_ID。"""
  local_path = (
    local_path
    or os.environ.get("VOXCPM_MODELS_DIR", "")
    or os.environ.get("VOXCPM_MODEL_DIR", "")
  ).strip()
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

  # 2.5 自动迁移：扫描旧安装目录里已下载的模型（no-model 版重装后不丢旧模型）
  legacy = _find_legacy_model_dir()
  if legacy:
    return legacy

  # 3. 全都不存在：回退到 HF repo id（离线环境会失败，但报错路径明确）
  return MODEL_ID


def model_present() -> bool:
  """判断必需的 VoxCPM2 主模型权重是否存在（无模型版安装包需用户自行下载）。"""
  mp = resolve_model_dir()
  if not os.path.isdir(mp):
    return False
  return os.path.isfile(os.path.join(mp, "model.safetensors")) and os.path.isfile(
    os.path.join(mp, "config.json")
  )


# 关键模型文件的「合理最小体积」下限（字节），用于检出下载不完整/损坏。
_MODEL_MIN_SIZE = {
  "model.safetensors": 1_000_000_000,  # 真实约 4.6GB
  "audiovae.pth": 100_000_000,  # 真实约 0.6GB
  "tokenizer.json": 1_000_000,  # 真实数 MB~数十 MB
}


def verify_model_files():
  """逐项校验必需模型文件：是否存在、体积是否合理（可检出缺失/下载不全/损坏）。"""
  mp = resolve_model_dir()
  fnames = (
    _dlmod.FILES
    if (HAS_DL and _dlmod is not None)
    else [
      "model.safetensors",
      "audiovae.pth",
      "config.json",
      "special_tokens_map.json",
      "tokenizer.json",
      "tokenizer_config.json",
      "tokenization_voxcpm2.py",
    ]
  )
  files, missing = [], []
  for f in fnames:
    p = os.path.join(mp, f)
    if os.path.isfile(p):
      sz = os.path.getsize(p)
      ok = sz > 0 and sz >= _MODEL_MIN_SIZE.get(f, 1)
      issue = "" if ok else ("文件为空" if sz == 0 else "体积异常偏小，可能下载不完整")
      files.append({"name": f, "ok": bool(ok), "size": sz, "issue": issue})
    else:
      missing.append(f)
      files.append({"name": f, "ok": False, "size": 0, "issue": "文件缺失"})
  all_ok = (not missing) and all(x["ok"] for x in files)
  return files, missing, all_ok


# ── 网页内模型下载（后台线程 + 进度）─────────────
_dl_lock = threading.Lock()
_dl_state = {
  "status": "idle",  # idle | scanning | downloading | done | error | cancelled
  "phase": None,
  "file": None,
  "file_index": 0,
  "file_count": 0,
  "downloaded": 0,
  "total": None,
  "percent": None,
  "overall_percent": None,
  "message": "",
  "started_at": None,
  "finished_at": None,
}
_dl_thread: list = [None]  # 用列表存线程引用，便于在函数内修改


def _dl_progress(p: dict):
  with _dl_lock:
    for k, v in p.items():
      if v is not None:
        _dl_state[k] = v


def _is_download_cancelled(exc: BaseException) -> bool:
  """总式：判定是否为下载取消异常（布尔判断移出 except 块）。"""
  return _dlmod is not None and isinstance(exc, _dlmod._DownloadCancelled)


def _dl_run():
  try:
    if HAS_DL and _dlmod is not None:
      ok_main, ok_zip = _dlmod.download_models(
        progress_cb=_dl_progress,
        should_stop=lambda: _dl_state.get("status") == "cancelled",
      )
    else:
      ok_main = False
    with _dl_lock:
      if _dl_state.get("status") == "cancelled":
        pass  # 已在 cancel 接口标记
      elif not ok_main:
        _dl_state["status"] = "error"
        _dl_state["message"] = (
          "主模型下载未完成，请检查网络后重试，或双击「下载模型.bat」手动下载。"
        )
        _dl_state["finished_at"] = time.time()
      else:
        _dl_state["status"] = "done"
        _dl_state["phase"] = "done"
        _dl_state["percent"] = 100
        _dl_state["overall_percent"] = 100
        _dl_state["message"] = (
          "模型下载完成。可前往右上角「模型状态 → 加载模型」开始使用。"
        )
        _dl_state["finished_at"] = time.time()
  except Exception as _e_cancel:
    if _is_download_cancelled(_e_cancel):
      with _dl_lock:
        _dl_state["status"] = "cancelled"
        _dl_state["message"] = "已取消下载。可重新点击下载，已下载部分将自动续传。"
        _dl_state["finished_at"] = time.time()
    else:
      with _dl_lock:
        _dl_state["status"] = "error"
        _dl_state["message"] = f"下载失败: {_e_cancel}"
        _dl_state["finished_at"] = time.time()
  finally:
    _dl_thread[0] = None


def _model_missing_detail() -> str:
  """生成「模型缺失」的友好指引文本（控制台 / 异常信息通用）。"""
  mp = resolve_model_dir()
  return (
    "未找到 VoxCPM2 主模型权重（model.safetensors）。\n"
    "期望模型目录: " + mp + "\n"
    "获取方式（任选其一）:\n"
    "  1) 双击运行安装目录下的「下载模型.bat」一键下载（需联网，支持断点续传）；\n"
    "  2) 从网盘下载模型专用包，解压到上述 model\\openbmb\\VoxCPM2 目录；\n"
    "  3) 手动从 HuggingFace(openbmb/VoxCPM2) 或 ModelScope(OpenBMB/VoxCPM2) 下载后放入该目录。\n"
    "放置完成后重新启动本程序即可。"
  )


def _build_lora_kwargs():
  """构建传给 VoxCPM.from_pretrained 的 LoRA 参数字典，并返回解析状态。

  返回 (kwargs, info)：
  - kwargs：含 lora_config / lora_weights_path（失败时为 {}）。
  - info：resolve_lora 的结构化状态（ok / reason / r / alpha …），供状态面板如实显示，
    杜绝「路径填错却谎称已挂载」的静默失败。

  使用 lora_helper.resolve_lora 从训练产物的 lora_config.json 重建与训练一致的
  LoRAConfig（关键是 r / alpha），规避「只给权重路径→自动建默认 r=8→与训练 r 形状
  不匹配→加载失败」的隐藏坑。
  """
  global _lora_resolve_error
  if not _lora_weights_path:
    return {}, {"ok": False, "reason": "未配置 LoRA 权重路径"}
  try:
    from lora_helper import resolve_lora
  except Exception as e:
    print(f"[LoRA] lora_helper 导入失败，忽略 LoRA：{e}")
    return {}, {"ok": False, "reason": f"lora_helper 导入失败：{e}"}
  kwargs: dict = {}
  kwargs, info = resolve_lora(kwargs, _lora_weights_path)
  _lora_resolve_error = "" if info.get("ok") else info.get("reason", "未知原因")
  return kwargs, info


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

  if not model_present():
    msg = _model_missing_detail()
    _model_error = msg
    with state_lock:
      _model_loading = False
    print("[VoxCPM2] " + msg)
    raise RuntimeError(msg)

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

    device = (
      _device_pref if _device_pref else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    print(f"[VoxCPM2] 正在加载模型: {model_path} (device={device})")
    # 清掉上一次加载遗留的 LoRA 状态，避免陈旧信息误导状态面板
    global _lora_load_info
    _lora_load_info = None
    lora_kwargs, _lora_info = _build_lora_kwargs()
    model = VoxCPM.from_pretrained(
      model_path,
      load_denoiser=use_denoiser,
      zipenhancer_model_id=cast(
        "str", zpath if use_denoiser else None
      ),  # 运行时接受 None（关闭降噪）
      optimize=False,
      device=device,
      **lora_kwargs,
    )
    # 捕获 LoRA 实际加载结果，供状态面板如实显示（不再谎称「已挂载」）
    if getattr(model, "_lora_attempted", False):
      lk = getattr(model, "_lora_loaded_keys", None)
      sk = getattr(model, "_lora_skipped_keys", None)
      _lora_load_info = (
        len(lk) if lk is not None else None,
        len(sk) if sk is not None else None,
      )
    else:
      _lora_load_info = None
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
    _gc_ok = False  # 回收失败不影响卸载主流程
  try:
    import torch

    if torch.cuda.is_available():
      torch.cuda.empty_cache()
  except Exception:
    _cuda_cleanup_ok = False  # 显存清理失败不影响卸载主流程
  print("[VoxCPM2] 模型已卸载")


def _load_model_background(force: bool = False):
  """在后台线程中执行模型加载，供手动触发使用。"""
  try:
    load_model(force_reload=force)
  except Exception as e:
    print(f"[VoxCPM2] 手动加载模型失败: {e}")


# 合法 VoxCPM 音素块（模块级，供 split_text / _is_phoneme_text 共用）：
# {hang2}（拼音声调）或 {HH AH0 L OW1}（CMU 英文音素）。
# 只匹配英文字母/ü/数字/空格/'-/. 组成的花括号块，普通中文花括号（如 {重要}）不匹配。
_PHONEME_BLOCK_RE = re.compile(r"\{[a-zA-ZüÜ0-9\s\-'\.]+\}")


def split_text(text: str, chunk_size: int = MAX_CHUNK_SIZE) -> list:
  if len(text) <= chunk_size:
    return [text]
  # 音素/混合模式：按 } 边界切分，绝不切断 {ni3} / {hang2} 音素块
  if _PHONEME_BLOCK_RE.search(text):
    blocks = re.findall(r"\{[^{}]*\}\s*", text) or [text]
    rest = re.sub(r"\{[^{}]*\}\s*", "", text)
    if rest:
      blocks.append(rest)
    chunks, current = [], ""
    for b in blocks:
      if len(current) + len(b) <= chunk_size:
        current += b
      else:
        if current:
          chunks.append(current)
        current = b
    if current:
      chunks.append(current)
    return chunks
  chunks = []
  sentences = re.split(r"([。！？；\.\!\?\;，,])", text)
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


# [v5.4] 分段幻觉守卫（voicebox 移植）；缺失时降级为不启用（不影响主链路）
try:
  import audio_guard
except Exception:  # audio_guard 不存在（旧部署热更新等极端场景）
  audio_guard = None


def resample_audio(audio: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
  """音频重采样（优先 librosa，回退 scipy/numpy 线性插值）。"""
  try:
    import librosa

    return librosa.resample(audio.astype(np.float32), orig_sr=sr_in, target_sr=sr_out)
  except Exception:
    _librosa_resample_ok = False  # librosa 不可用则继续尝试下一策略
  try:
    from scipy.signal import resample as sp_resample

    n = _safe_int(round(len(audio) * sr_out / sr_in))
    return cast(
      "np.ndarray", sp_resample(audio, n)
    )  # scipy 存根重载返回 tuple，实际为 ndarray
  except Exception:
    _scipy_resample_ok = False  # scipy 不可用则继续尝试下一策略
  # 简单线性插值回退
  n = _safe_int(round(len(audio) * sr_out / sr_in))
  if n <= 1:
    return audio
  xp = np.linspace(0, len(audio) - 1, len(audio))
  x = np.linspace(0, len(audio) - 1, n)
  return np.interp(x, xp, audio).astype(audio.dtype)


def crossfade_concat(
  audio_list: list, sample_rate: int, fade_ms: int = 80
) -> np.ndarray:
  """多段音频等功率交叉淡入淡出拼接（消除段间断裂/爆音）。

  规则：首段不淡入（result 初始即第一段、头部不动），末段不淡出（仅头部与前段尾交叉
  淡化，尾部完整保留）。重叠段使用等功率曲线 cos/sin，感知响度恒定，避免线性淡变中段的下凹。
  """
  if not audio_list:
    return np.array([], dtype=np.float32)
  if len(audio_list) == 1:
    return np.asarray(audio_list[0], dtype=np.float32)
  fade_n = max(1, _safe_int(sample_rate * fade_ms / 1000))
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
  return _safe_float(np.sqrt(np.mean(arr * arr)))


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
  target_rms = (
    rms_values[0] if target_mode == "first" else _safe_float(np.mean(valid_rms))
  )
  if target_rms < 1e-9:
    return audio_segments

  normalized = []
  for seg, rms in zip(audio_segments, rms_values, strict=True):
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
  max_amp = _safe_float(np.max(np.abs(arr)))
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
  # [修复] 显式控制指令标记: 仅当用户手动填写控制指令框时才在 fixed_clone 模式附加风格前缀
  # (预设回退描述不作前缀, 保持参考克隆默认链路行为不变; 自播种/逐段设计仍用回退描述)
  control_explicit = bool(control_text and str(control_text).strip())
  mode = args.get("mode", "voice_design")  # voice_design | fixed_clone | self_seeding
  reference_wav = args.get("reference_wav")
  prompt_text = args.get("prompt_text")
  cfg = _safe_float(args.get("cfg", 2.5), 2.5)
  steps = _safe_int(args.get("steps", 15), 15)
  # 可复现种子（官方 README 特性 generate(seed=...)）：引擎层 generate() 不接受 seed kwarg，
  # 这里解析目标种子；真正播种在模型加载完成后、生成循环前（加载过程会消耗 RNG，
  # 先播种会被抵消导致冷启动首个任务与后续不同）
  _seed_raw = str(args.get("seed", "") or "").strip()
  _seed_val = None
  if _seed_raw:
    try:
      _seed_val = int(float(_seed_raw))
    except (ValueError, TypeError):
      print(f"[VoxCPM2] seed={_seed_raw!r} 非有效整数，忽略（随机合成）")
  # normalize：用户显式开关（默认开）。若检测到音素串 {ni3}，自动强制切音素模式
  # （官方要求音素输入必须 normalize=False，且不能把 {} 块交给归一化/模型二次归一化）。
  requested_normalize = str(args.get("normalize", "true")).lower() in (
    "true",
    "1",
    "yes",
    True,
  )
  requested_phoneme_mode = str(args.get("phoneme_mode", "false")).lower() in (
    "true",
    "1",
    "yes",
    True,
  )

  # 合法 VoxCPM 音素块判定见模块级 _PHONEME_BLOCK_RE（split_text 共用）。
  def _is_phoneme_text(s: str) -> bool:
    """检测文本是否含 VoxCPM 音素块：任意合法 {} 块即进入音素/混合模式。

    支持纯音素串（{ni3}{hao3}）、单块（{hang2}）以及「普通文本 + 局部音素标注」
    混合输入（如"今天一行{hang2}代码写完了"）。合法块由 _PHONEME_BLOCK_RE
    限定（仅字母/ü/数字/空格等），避免把普通中文花括号误判为音素。
    """
    return bool(_PHONEME_BLOCK_RE.search(s or ""))

  # 音素模式 = 前端显式开关 OR 文本自动检测兜底（官方要求音素输入必须 normalize=False）
  phoneme_mode = requested_phoneme_mode or _is_phoneme_text(text)
  # 默认链路（无音素标注）自动纠错：98 条多音字语料命中词自动注入 {音素} 标注，
  # 使"一行"等默认链路也读对（hang2）。注入后强制进入混合模式（normalize 自动关闭）。
  if not phoneme_mode:
    try:
      from g2p_phoneme import apply_overlay_auto

      auto_text, applied = apply_overlay_auto(text)
      if applied:
        text = auto_text
        phoneme_mode = True
    except Exception:
      _phoneme_auto_skipped = (
        True  # 自动纠错失败不阻塞，退回默认链路（多音字可能读错，可手动标注兜底）
      )
  normalize = requested_normalize and not phoneme_mode
  denoise = str(args.get("denoise", "false")).lower() in ("true", "1", "yes", True)
  prompt_text = args.get("prompt_text") or None  # 终极克隆：参考音频的转录文本
  crossfade = _safe_int(args.get("crossfade", 80), 80)
  chunk_size = _safe_int(args.get("chunk_size", 180), 180)
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
  # 播种时机：模型加载完成后、首次生成前（加载/预热消耗了 RNG，此处重播才是确定态）
  if _seed_val is not None:
    import random as _random_mod
    import torch as _torch_mod

    _torch_mod.manual_seed(_seed_val)
    if _torch_mod.cuda.is_available():
      _torch_mod.cuda.manual_seed_all(_seed_val)
    np.random.seed(_seed_val)
    _random_mod.seed(_seed_val)
    print(f"[VoxCPM2] 可复现种子已启用: {_seed_val}（生成循环开始前播种）")

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

  # 混合模式去重读：{音素块} 紧跟标注的是其前面的那个汉字，读音由音素块接管，
  # 送入模型前舍去该字（"今天一行{hang2}代码" -> "今天一{hang2}代码"），
  # UI 输入框仍保留原字供对照。纯音素串（块前无汉字）不受影响。
  if phoneme_mode:
    try:
      from g2p_phoneme import strip_annotated_hanzi

      text = strip_annotated_hanzi(text)
    except Exception:
      _phoneme_strip_skipped = (
        True  # 剥离失败不阻塞合成，退回原文本（可能重复读但保证能出结果）
      )

  chunks = split_text(text, chunk_size=chunk_size)
  total_chunks = len(chunks)

  # 多段 voice_design 自动走 self-seeding：用第一段固定音色作为后续段的参考，
  # 避免长文本每段都重新按 control 描述采样导致音色不一致。
  use_self_seeding = (mode == "self_seeding") or (
    mode == "voice_design" and total_chunks > 1
  )

  # 统一走分段循环（单段也走这里，确保 fixed_clone / self_seeding 对短文本同样生效）
  audio_segments = []
  current_ref = (
    reference_wav if (reference_wav and os.path.exists(reference_wav)) else None
  )
  seed_prompt_text = None
  seed_ref_path = None
  synthesis_elapsed_total = 0.0
  synthesis_chars_total = 0

  for i, chunk in enumerate(chunks, 1):
    # 进度按「已完成段数」计算：第一段开始前应为 5%，避免一起步就 50%+
    progress = _safe_int(5 + 80 * (i - 1) / total_chunks)
    with task_lock:
      now = time.time()
      r = task_results[job_id]
      start_ts = r.get("start_time", now)
      elapsed = now - start_ts
      estimated_total = r.get("estimated_total_seconds", max(5.0, len(text) * 0.12))
      # 根据实际耗时动态修正剩余时间
      if i > 1 and elapsed > 0:
        estimated_total = max(estimated_total, elapsed * total_chunks / (i - 1))
      r.update(
        {
          "status": "synthesizing",
          "progress": progress,
          "display_progress": progress,
          "message": (
            f"正在合成第 {i}/{total_chunks} 段..."
            if total_chunks > 1
            else "正在合成..."
          ),
          "start_time": start_ts,
          "estimated_total_seconds": estimated_total,
          "elapsed_seconds": elapsed,
          "remaining_seconds": max(0, estimated_total - elapsed),
        }
      )
      task_results[job_id] = r

    # 音素/混合模式处理：
    # - 纯音素串（{ni3}{hao3}）与「普通文本 + 局部 {音素} 标注」（如"今天一行{hang2}代码"）
    #   统一走混合处理：normalize_text 已对 {..} 音素块做占位保护（问题1修复），
    #   普通段的数字/符号转中文读法后保留中文原文交给模型自读（默认正常链路），
    #   {hang2} 等标注块保留并强制按音素读音；模型侧 normalize 强制 False，
    #   防止模型自带归一化二次破坏 {} 块（问题2双保险）。
    # - 非音素模式：维持原有 normalize 行为。
    if phoneme_mode:
      processed_chunk = normalize_text(chunk) if normalize_text else chunk
      normalize = False
    else:
      processed_chunk = normalize_text(chunk) if normalize_text and normalize else chunk
    chunk_text = f"({control}){processed_chunk}" if control else processed_chunk
    chunk_start = time.time()
    _retry_text = chunk_text  # 默认重试文本；各分支可覆盖（fixed_clone/ref_continuation 用 processed_chunk）
    _regen = None  # 默认不可重试；各分支按副作用情况覆盖
    try:
      if mode == "fixed_clone" and current_ref:
        # 固定参考克隆 / 终极克隆（参考音频 + 转录文本）
        # [修复] 仅当用户显式填写控制指令时按段附加 (指令) 前缀, 在参考音色上叠加语气/情绪
        kwargs = {
          "text": chunk_text if control_explicit else processed_chunk,
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
        _regen = (
          (
            lambda t, _kw={k: v for k, v in kwargs.items() if k != "text"}: (
              model.generate(text=t, **_kw)
            )
          )
          if audio_guard is not None
          else None
        )
        _retry_text = chunk_text if control_explicit else processed_chunk  # 本分支实际生成文本（显式指令时含 control 前缀）
      elif use_self_seeding and i == 1:
        # 第一段 Voice Design
        wav = model.generate(
          text=chunk_text,
          cfg_value=cfg,
          inference_timesteps=steps,
          normalize=normalize,
          denoise=denoise,
        )
        seed_prompt_text = chunk_text
        _regen = None  # 种子段有副作用（写 seed_ref 供后续段克隆），不对半重试，仅裁剪
        seed_ref_path = os.path.join(TEMP_DIR, f"seed_{job_id}.wav")
        # Windows 可能在运行期间清理 AppData\Local\Temp，导致 TEMP_DIR 消失；
        # 写种子前确保目录存在，避免 sf.write 报 "Error opening ...: System error"
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
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
        _regen = (
          (
            lambda t, _ref=current_ref, _sr_path=seed_ref_path, _st=seed_prompt_text, _cfg=cfg, _stp=steps, _nm=normalize, _dn=denoise: (
              model.generate(
                text=t,
                cfg_value=_cfg,
                inference_timesteps=_stp,
                reference_wav_path=_ref,
                prompt_wav_path=_sr_path,
                prompt_text=_st,
                normalize=_nm,
                denoise=_dn,
              )
            )
          )
          if audio_guard is not None
          else None
        )
        _retry_text = processed_chunk
      else:
        wav = model.generate(
          text=chunk_text,
          cfg_value=cfg,
          inference_timesteps=steps,
          normalize=normalize,
          denoise=denoise,
        )
        _regen = (
          (
            lambda t, _cfg=cfg, _stp=steps, _nm=normalize, _dn=denoise: model.generate(
              text=t,
              cfg_value=_cfg,
              inference_timesteps=_stp,
              normalize=_nm,
              denoise=_dn,
            )
          )
          if audio_guard is not None
          else None
        )
        _retry_text = chunk_text
      chunk_elapsed = time.time() - chunk_start
      chunk_chars = len(processed_chunk)
      synthesis_elapsed_total += chunk_elapsed
      synthesis_chars_total += chunk_chars
      with _avg_lock:
        if synthesis_chars_total > 0:
          avg = synthesis_elapsed_total / synthesis_chars_total
          if _global_avg_seconds_per_char > 0:
            _global_avg_seconds_per_char = (
              _global_avg_seconds_per_char * 0.7 + avg * 0.3
            )
          else:
            _global_avg_seconds_per_char = avg

      sr = model.tts_model.sample_rate
      if audio_guard is not None:
        # [v5.4] 幻觉守卫：裁段内长静音；跑飞形态时自动对半拆段重生成（种子段仅裁剪）
        wav = audio_guard.guard_chunk(wav, sr, _regen, _retry_text)
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
      r.update(
        {
          "status": "synthesizing",
          "progress": 90,
          "display_progress": 90,
          "message": "正在拼接音频...",
          "elapsed_seconds": now - r.get("start_time", now),
          "remaining_seconds": 0,
        }
      )
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
  safe_name = (
    "voxcpm_"
    + datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    + "_"
    + uuid.uuid4().hex[:6]
  )
  out_dir = _output_dir / "VoxCPM_Outputs"
  out_dir.mkdir(parents=True, exist_ok=True)
  out_wav = out_dir / f"{safe_name}.wav"
  try:
    sf.write(str(out_wav), merged, out_sr)
  except Exception:
    # soundfile 不可用时的降级
    try:
      import wave

      with wave.open(str(out_wav), "wb") as wf:
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
    r.update(
      {
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
      }
    )
    task_results[job_id] = r

  return task_results[job_id]


def submit_task(args: dict) -> str:
  job_id = str(uuid.uuid4())[:8]
  args["job_id"] = job_id
  text = args.get("text", "")
  # 用历史每字符耗时预估，无历史则用保守默认值；未加载模型时预留加载时间
  with _avg_lock:
    per_char = (
      _global_avg_seconds_per_char if _global_avg_seconds_per_char > 0 else 0.35
    )
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


def get_app_version(fallback="5.3"):
  """读取 app/version.txt 作为统一版本号数据源（单一可信来源）；
  缺失或损坏时回退 fallback，保证程序仍可启动。"""
  try:
    p = Path(__file__).resolve().parent.parent / "version.txt"
    if p.exists():
      v = p.read_text(encoding="utf-8-sig").strip()
      if v:
        return v
  except Exception as _ver_exc:  # 版本号读取失败时仅提示，不阻断启动
    import sys

    print(f"[警告] 读取 app/version.txt 失败: {_ver_exc}", file=sys.stderr)
  return fallback


VERSION = get_app_version()

HTML_CONTENT = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>VoxCPM2 语音合成</title>
<style>
  :root {
    /* Apple-style 系统字体栈：优先 San Francisco / PingFang */
    --font-sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    --font-mono: "SF Mono", "SFMono-Regular", "Menlo", "Consolas", monospace;
    --bg: #0f1117;
    --surface: rgba(22, 27, 39, 0.82);
    --surface2: rgba(30, 37, 53, 0.86);
    --surface-solid: #161b27;
    --surface2-solid: #1e2535;
    --border: rgba(42, 51, 71, 0.65);
    --accent: #6c8eff;
    --accent2: #4ecdc4;
    --text: #f5f7ff;
    --text2: #a9b0c6;
    --input-bg: rgba(21, 23, 31, 0.92);
    --input-bg-focus: rgba(14, 15, 20, 0.96);
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #f87171;
    --shadow-sm: 0 2px 8px rgba(0,0,0,0.18);
    --shadow-md: 0 8px 24px rgba(0,0,0,0.22);
    --shadow-lg: 0 18px 48px rgba(0,0,0,0.32);
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --radius-xl: 20px;
  }
  /* 浅色主题 */
  [data-theme="light"] {
    --bg: #f4f6fb;
    --surface: rgba(255, 255, 255, 0.86);
    --surface2: rgba(238, 241, 247, 0.90);
    --surface-solid: #ffffff;
    --surface2-solid: #eef1f7;
    --border: rgba(216, 222, 234, 0.72);
    --accent: #3b65ff;
    --accent2: #0fa3a3;
    --text: #1a1f2b;
    --text2: #5b6678;
    --input-bg: rgba(255, 255, 255, 0.92);
    --input-bg-focus: rgba(255, 255, 255, 0.96);
    --green: #16a34a;
    --yellow: #d97706;
    --red: #dc2626;
    --shadow-sm: 0 2px 8px rgba(15, 23, 42, 0.06);
    --shadow-md: 0 8px 24px rgba(15, 23, 42, 0.10);
    --shadow-lg: 0 18px 48px rgba(15, 23, 42, 0.14);
  }
  /* 主题切换平滑过渡（仅颜色类属性，保证 60fps） */
  body, header, .sidebar, .content, .param-card, .text-card, .ref-card,
  .envbar, .env-item, .voice-btn, .control-card, .history-card, .progress-card,
  .modal, .toast, .status-badge, .app-icon, .theme-switch, .custom-voice-box {
    transition: background 0.3s ease, color 0.3s ease, border-color 0.3s ease;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font-sans);
    background: var(--bg) fixed;
    background-size: cover;
    color: var(--text);
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }

  /* ── Apple Design 风格：自定义背景时以背景色为氛围，所有承载文字区域
        使用独立输入/表面色，保证任何背景色下文本都清晰可读 ── */
  body.custom-bg-active .content,
  body.custom-bg-active .sidebar,
  body.custom-bg-active header {
    background: var(--surface);
    backdrop-filter: blur(20px) saturate(180%);
  }
  body.custom-bg-active .param-card,
  body.custom-bg-active .text-card,
  body.custom-bg-active .control-card,
  body.custom-bg-active .ref-card,
  body.custom-bg-active .history-card,
  body.custom-bg-active .progress-card,
  body.custom-bg-active .custom-voice-box,
  body.custom-bg-active .ref-preview,
  body.custom-bg-active .modal {
    background: var(--surface2);
    border-color: var(--border);
  }
  body.custom-bg-active .voice-btn:hover,
  body.custom-bg-active .mode-btn:hover,
  body.custom-bg-active .example-chip:hover,
  body.custom-bg-active .history-action-btn:hover,
  body.custom-bg-active .history-item:hover,
  body.custom-bg-active .upload-area:hover {
    background: rgba(108,142,255,0.12);
  }
  body.custom-bg-active .voice-btn.active,
  body.custom-bg-active .mode-btn.active {
    background: rgba(108,142,255,0.22);
  }
  /* 输入框：统一使用独立输入背景色，不随自定义背景色变化，确保可读性 */
  body.custom-bg-active textarea,
  body.custom-bg-active .path-input,
  body.custom-bg-active .prompt-text-input,
  body.custom-bg-active .env-select,
  body.custom-bg-active .cv-name {
    background: var(--input-bg);
    color: var(--text);
  }
  body.custom-bg-active textarea:focus,
  body.custom-bg-active .path-input:focus,
  body.custom-bg-active .prompt-text-input:focus,
  body.custom-bg-active .cv-name:focus {
    background: var(--input-bg-focus);
  }

  /* ── 顶栏 ── */
  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 8px 20px;
    min-height: 60px;
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: nowrap;
    flex-shrink: 0;
    overflow: hidden;
  }
  .logo {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
    letter-spacing: 1px;
  }
  .logo span { color: var(--text); font-weight: 400; }
  .header-left { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; flex-shrink: 0; }
  .header-sample-rate {
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; color: var(--text2);
    padding: 4px 8px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--surface2);
    flex: 0 0 160px;
    justify-content: center;
    overflow: hidden;
    box-sizing: border-box; height: 30px;
  }
  .header-sample-rate .env-select { max-width: 110px; }
  .header-sample-rate .k { color: var(--text2); }
  .header-env {
    display: flex; flex-wrap: wrap; gap: 6px;
    align-items: center;
    margin-left: auto;
    margin-right: 4px;
  }
  .brand-stack {
    display: flex;
    flex-direction: row;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
    margin-right: 2px;
  }
  .app-icon {
    width: 26px; height: 26px;
    border-radius: 7px;
    object-fit: contain;
    flex-shrink: 0;
  }
  .console-toggle {
    width: 24px; height: 24px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: var(--surface2);
    color: var(--text);
    font-size: 12px;
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
  .theme-switch input[type="color"].active { outline: 2px solid var(--accent); outline-offset: 1px; }
  .header-right {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: 12px;
    flex-shrink: 0;
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
    backdrop-filter: blur(20px) saturate(180%);
  }
  .sidebar h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1px;
    color: var(--text2);
    margin-bottom: 12px;
  }
  .voice-grid {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .voice-item {
    display: flex;
    align-items: stretch;
    gap: 2px;
    border-radius: 10px;
    cursor: move;
  }
  .voice-btn {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 11px 14px;
    border-radius: var(--radius-md);
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
    box-shadow: 0 2px 8px rgba(108,142,255,0.4);
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
    border-radius: var(--radius-lg);
    padding: 16px 18px;
    display: flex;
    flex-direction: column;
    gap: 12px;
    min-width: 200px;
    flex: 1;
    box-shadow: var(--shadow-sm);
    transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s;
  }
  .param-card:hover { border-color: rgba(108,142,255,0.4); box-shadow: var(--shadow-md); transform: translateY(-2px); }
  /* 统一悬停上浮效果：主内容卡片与 param-card 一致（边框提亮 + 阴影 + 上浮 2px） */
  .text-card, .control-card, .ref-card { transition: transform 0.15s, border-color 0.15s, box-shadow 0.15s; }
  .text-card:hover, .control-card:hover, .ref-card:hover { border-color: rgba(108,142,255,0.4); box-shadow: var(--shadow-md); transform: translateY(-2px); }
  .param-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; min-height: 36px; }
  .param-title { display: flex; align-items: baseline; gap: 10px; flex: 1; min-width: 0; }
  .param-title .param-unit { align-self: baseline; padding-bottom: 0; margin-left: 0; font-size: 13px; color: var(--text2); }
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
  .param-desc { font-size: 12px; color: var(--text2); white-space: normal; text-align: left; line-height: 1.5; overflow-wrap: break-word; word-break: break-word; }
  .param-desc-wrap { display: flex; flex-direction: column; align-items: flex-end; text-align: right; gap: 1px; }
  .param-desc-line { font-size: 12px; color: var(--text2); line-height: 1.35; }
  .param-unit {
    font-size: 12px; color: var(--text2); line-height: 1;
    align-self: flex-end; padding-bottom: 3px; margin-left: 4px;
    white-space: nowrap;
  }
  .param-slider-row { display: flex; align-items: center; gap: 10px; }
  .param-slider-row input[type="range"] { flex: 1; }
  .param-slider-row .param-unit { align-self: center; padding-bottom: 0; margin-left: 0; }
  .param-desc-right { display: flex; flex-direction: column; align-items: flex-end; flex-shrink: 0; justify-content: flex-end; gap: 6px; margin-top: -4px; }

  /* ── 高级参数卡片右上角开关（数字归一化 / 音素输入，垂直两行，紧凑） ── */
  .param-toggles {
    display: flex; flex-direction: column; align-items: flex-end;
    gap: 4px; flex-shrink: 0; padding: 0;
  }
  .param-toggle {
    display: flex; align-items: center; gap: 6px;
    padding: 4px 8px; border-radius: 20px;
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
    background: transparent; outline: none;
  }
  input[type="range"]::-webkit-slider-runnable-track {
    width: 100%; height: 6px; border-radius: 3px;
    background: linear-gradient(to right, var(--accent) var(--value-percent, 0%), var(--surface2) var(--value-percent, 0%));
    border: 1px solid var(--text2);
  }
  input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
    transition: transform 0.1s, box-shadow 0.1s;
    margin-top: -5px;
  }
  input[type="range"]::-webkit-slider-thumb:hover { transform: scale(1.1); box-shadow: 0 3px 10px rgba(108,142,255,0.5); }
  input[type="range"]::-moz-range-thumb {
    width: 16px; height: 16px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
  }
  input[type="range"]::-moz-range-progress { background: var(--accent); height: 6px; border-radius: 3px; }
  input[type="range"]::-moz-range-track {
    background: var(--surface2); height: 6px; border-radius: 3px;
    border: 1px solid var(--text2);
  }
  /* 自定义背景：滑块配色与浅色/深色主题保持一致，统一视觉语言 */
  body.custom-bg-active input[type="range"]::-webkit-slider-runnable-track {
    background: linear-gradient(to right, var(--accent) var(--value-percent, 0%), var(--surface2) var(--value-percent, 0%));
    border: 1px solid var(--text2);
  }
  body.custom-bg-active input[type="range"]::-moz-range-track {
    background: var(--surface2);
    border: 1px solid var(--text2);
  }
  body.custom-bg-active input[type="range"]::-moz-range-progress { background: var(--accent); }
  body.custom-bg-active input[type="range"]::-webkit-slider-thumb {
    border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
  }
  body.custom-bg-active input[type="range"]::-moz-range-thumb {
    border: 2px solid var(--surface);
    box-shadow: 0 2px 6px rgba(108,142,255,0.4);
  }

  /* ── 文本输入 ── */
  .text-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    flex: 0 0 auto;
    min-height: auto;
    box-shadow: var(--shadow-sm);
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
    flex: 0 0 auto;
    display: flex;
    height: 94px;   /* 默认三行：line-height1.5×15px×3 + padding24 + border2 ≈ 94px；自绘调高手柄可覆盖此值 */
  }
  #textInput { line-height: 1.5; }
  .text-area-hint {
    position: absolute;
    right: 14px;
    bottom: 12px;
    font-size: 12px;
    color: var(--text2);
    pointer-events: none;
    opacity: 0.7;
  }
  .text-area-resizer,
  .prompt-resizer {
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: -7px;
    height: 12px;
    cursor: ns-resize;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 3;
  }
  .text-area-resizer::before,
  .prompt-resizer::before {
    content: '';
    width: 44px;
    height: 4px;
    border-radius: 3px;
    background: var(--border);
    opacity: 0.55;
    transition: width .15s ease, background .15s ease, opacity .15s ease;
  }
  .text-area-resizer:hover::before,
  .text-area-resizer.dragging::before,
  .prompt-resizer:hover::before,
  .prompt-resizer.dragging::before {
    width: 64px;
    background: var(--accent);
    opacity: 1;
  }
  textarea {
    flex: 1;
    width: 100%;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    outline: none;
    color: var(--text);
    font-size: 15px;
    line-height: 1.7;
    resize: none;
    font-family: var(--font-sans);
    padding: 12px 14px;
    transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }
  textarea:focus {
    background: var(--input-bg-focus);
    border-color: var(--accent);
    box-shadow: 0 0 0 3px rgba(108,142,255,0.15);
  }
  textarea::placeholder { color: var(--text2); opacity: 0.7; }
  textarea:not(:placeholder-shown) ~ .text-area-hint { display: none; }

  /* ── 参考音频上传 ── */
  .ref-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
  }
  .ref-card h3 { font-size: 12px; color: var(--text2); margin-bottom: 10px; }
  .ref-modes {
    display: flex;
    gap: 8px;
    margin-bottom: 12px;
  }
  .mode-btn {
    padding: 8px 12px;
    border-radius: var(--radius-md);
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text2);
    cursor: pointer;
    font-size: 13px;
    text-align: center;
    transition: all 0.15s;
    line-height: 1.35;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .mode-btn:hover { border-color: var(--accent); color: var(--accent); }
  .mode-btn.active { border-color: var(--accent); background: rgba(108,142,255,0.15); color: var(--accent); }
  .ref-mode-btn {
    flex: 1;
    flex-direction: column;
    gap: 4px;
    min-height: 60px;
    padding: 10px 8px;
  }
  .ref-mode-btn .mode-title { font-size: 14px; font-weight: 600; color: inherit; }
  .ref-mode-btn .mode-sub { font-size: 11px; color: var(--text2); line-height: 1.3; }
  .ref-mode-btn.active .mode-sub { color: rgba(108,142,255,0.75); }
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
    border-radius: var(--radius-md);
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
    transition: opacity 0.15s, transform 0.1s, box-shadow 0.15s;
  }
  .btn-primary:hover { opacity: 0.9; }
  .btn-primary:active { transform: scale(0.98); }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
  .btn-secondary {
    height: 48px;
    padding: 0 20px;
    border-radius: var(--radius-md);
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
    border-radius: var(--radius-lg);
    padding: 16px;
    display: none;
    box-shadow: var(--shadow-sm);
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

  /* ── 模型下载卡片 ── */
  .dl-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: var(--radius-lg);
    padding: 14px 18px;
    margin-bottom: 16px;
    box-shadow: var(--shadow-sm);
  }
  .dl-card.done { border-left-color: var(--green); }
  .dl-card.error { border-left-color: var(--red); }
  .dl-card-head { display: flex; align-items: center; gap: 12px; }
  .dl-icon { font-size: 22px; line-height: 1; }
  .dl-head-text { flex: 0 1 auto; min-width: 0; }
  .dl-title { font-size: 14px; font-weight: 600; }
  .dl-sub { font-size: 12px; color: var(--text2); margin-top: 2px; line-height: 1.4; }
  .dl-spacer { flex: 1 1 auto; }
  .dl-progress { margin-top: 12px; }
  .dl-progress-meta { display: flex; justify-content: space-between; align-items: center; margin-top: 6px; gap: 10px; }
  .dl-fallback { font-size: 11px; color: var(--text2); margin-top: 10px; opacity: 0.85; line-height: 1.4; }
  .dl-card .btn-primary:disabled { opacity: 0.6; cursor: default; }

  /* ── 历史记录 ── */
  .history-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px;
    box-shadow: var(--shadow-sm);
  }
  .history-scroll { max-height: 216px; overflow-y: auto; }
  .head-wave { flex: 1 1 auto; min-width: 60px; height: 28px; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; box-sizing: border-box; display: block; }
  .synth-speed { flex: 0 0 auto; height: 28px; padding: 0 6px; font-size: 12px; color: var(--text); background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; cursor: pointer; }
  .synth-speed:hover { border-color: var(--accent); }
  .history-card h3 { font-size: 12px; color: var(--text2); margin: 0; }
  .history-head { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .history-actions { display: flex; gap: 6px; }
  .history-action-btn {
    padding: 3px 8px;
    border-radius: 4px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text2);
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .history-action-btn:hover { border-color: var(--accent); color: var(--accent); }
  .history-action-btn.danger:hover { border-color: var(--red); color: var(--red); }
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
    border-radius: var(--radius-lg);
    padding: 12px 18px;
    font-size: 13px;
    color: var(--text);
    z-index: 1000;
    opacity: 0;
    transform: translateY(10px);
    transition: all 0.3s;
    pointer-events: none;
    box-shadow: var(--shadow-md);
  }
  .toast.visible { opacity: 1; transform: none; }
  .toast.error { border-color: var(--red); color: var(--red); }
  .toast.success { border-color: var(--green); color: var(--green); }

  /* ── 模型校验结果（设置弹窗内常驻显示）── */
  .dl-verify { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 10px; }
  .dl-verify h4 { margin: 0 0 8px; font-size: 13px; font-weight: 600; }
  .dl-verify .vf { display: flex; justify-content: space-between; align-items: center;
                   gap: 10px; font-size: 12px; padding: 5px 0; border-bottom: 1px dashed var(--border); }
  .dl-verify .vf .nm { font-family: var(--mono, monospace); flex: 1 1 auto; word-break: break-all; }
  .dl-verify .vf .sz { opacity: .65; flex: 0 0 auto; margin: 0 8px; }
  .dl-verify .vf .st { flex: 0 0 auto; font-weight: 600; }
  .dl-verify .vf.ok .st { color: var(--green); }
  .dl-verify .vf.bad .st { color: var(--red); }
  .dl-verify .vf.bad .nm { color: var(--red); }
  /* ── 参考音频试听 / 终极克隆 / 高级 ── */
  .ref-preview { margin-top: 10px; background: var(--surface2); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 10px; }
  .ref-preview-head { display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: var(--text2); margin-bottom: 6px; }
  .ref-dur { color: var(--accent2); }
  .ref-warn { margin-top: 6px; font-size: 11px; color: var(--yellow); display: none; }
  .prompt-text-wrap { margin-top: 10px; }
  .prompt-resize-wrap { position: relative; }
  .prompt-text-input {
    width: 100%;
    min-height: 56px;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: var(--text);
    padding: 10px 12px;
    font-size: 13px;
    font-family: var(--font-sans);
    line-height: 1.55;
    resize: none;
    outline: none;
    transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .prompt-text-input:focus { background: var(--input-bg-focus); border-color: var(--accent); box-shadow: 0 0 0 3px rgba(108,142,255,0.15); }
  .toggle-row { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 13px; color: var(--text); cursor: pointer; }
  .toggle-row input { width: 16px; height: 16px; accent-color: var(--accent); }

  /* ── 音色描述 + 示例芯片 ── */
  .control-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 14px 16px;
    box-shadow: var(--shadow-sm);
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
    max-height: 90vh;
    overflow-y: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-xl);
    padding: 20px;
    box-shadow: var(--shadow-lg);
    position: relative;
    z-index: 1;
  }
  .modal-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; }
  .modal-head h3 { font-size: 15px; }
  .path-input {
    flex: 1;
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: var(--text);
    padding: 10px 12px;
    font-size: 13px;
    font-family: var(--font-sans);
    outline: none;
    transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .path-input:focus { background: var(--input-bg-focus); border-color: var(--accent); box-shadow: 0 0 0 3px rgba(108,142,255,0.15); }
  .path-row { display: flex; gap: 8px; align-items: center; margin-top: 6px; }
  .path-row .btn-secondary { flex-shrink: 0; height: 38px; padding: 0 14px; font-size: 13px; }
  .modal-actions { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }

  /* ── 顶部环境栏（横向排列，现已并入 header）── */
  .envbar {
    display: flex; flex-wrap: nowrap; gap: 6px;
    align-items: center;
    flex: 1;
    min-width: 0;
    overflow: hidden;
  }
  .env-item {
    display: flex; align-items: center; gap: 6px;
    font-size: 11px; padding: 4px 8px;
    border: 1px solid var(--border); border-radius: 6px;
    background: var(--surface2);
    white-space: nowrap;
    flex-shrink: 0;
  }
  .env-item.fixed {
    flex: 0 0 160px;
    justify-content: center;
    overflow: hidden;
    box-sizing: border-box; height: 30px;
  }
  .env-item.fixed .env-select,
  .env-item.fixed .v {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .env-item .k { color: var(--text2); }
  .env-item .v { color: var(--text); font-weight: 600; }
  .env-item .v.good { color: var(--green); }
  .env-item .v.bad { color: var(--red); }
  .env-path {
    flex: 1 1 0;
    min-width: 0;
    max-width: 160px;
  }
  .env-path .v {
    flex: 1 1 auto;
    min-width: 0;
    max-width: none;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .env-dev-btns { display: flex; gap: 3px; }
  .env-dev-btns button {
    padding: 0 8px; height: 22px; box-sizing: border-box; border-radius: 4px;
    border: 1px solid var(--border); background: transparent;
    color: var(--text); cursor: pointer; font-size: 11px; font-weight: 600; transition: all 0.15s;
  }
  .env-dev-btns button.active { border-color: var(--accent); background: rgba(108,142,255,0.15); color: var(--accent); }
  .env-select {
    background: var(--input-bg);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0 5px; height: 22px; box-sizing: border-box;
    font-size: 11px; font-weight: 600;
    font-family: var(--font-sans);
    outline: none;
    cursor: pointer;
    transition: background 0.2s ease, border-color 0.2s ease;
  }
  .env-select:focus { background: var(--input-bg-focus); border-color: var(--accent); }
  .env-select.good { color: var(--green); border-color: var(--green); }
  .env-select.bad { color: var(--red); border-color: var(--red); }

  /* ── 自定义音色保存框 ── */
  .custom-voice-box {
    margin-top: 12px;
    border: 1px dashed var(--border);
    border-radius: var(--radius-lg);
    padding: 10px 12px;
    display: flex; flex-direction: column; gap: 8px;
    background: var(--surface);
    box-shadow: var(--shadow-sm);
  }
  .custom-voice-box .cv-title { font-size: 11px; color: var(--text2); text-transform: uppercase; letter-spacing: 1px; }
  .custom-voice-box input.cv-name {
    width: 100%; background: var(--input-bg); border: 1px solid var(--border);
    border-radius: var(--radius-md); color: var(--text); padding: 8px 10px; font-size: 13px;
    font-family: var(--font-sans); outline: none;
    transition: background 0.2s ease, border-color 0.2s ease, box-shadow 0.2s ease;
  }
  .custom-voice-box input.cv-name:focus { background: var(--input-bg-focus); border-color: var(--accent); box-shadow: 0 0 0 3px rgba(108,142,255,0.15); }
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
  <div class="brand-stack">
    <img src="/VoxCPM_App.ico" class="app-icon" alt="VoxCPM2">
    <button class="console-toggle" id="consoleToggle" onclick="toggleConsole()" title="显示/隐藏命令行窗口">🖥️</button>
  </div>
  <div class="header-left">
    <div class="logo">Vox<span>CPM2</span></div>
    <div style="font-size:13px;color:var(--text2);">语音合成工具 {VERSION}</div>
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
    <div class="env-item fixed"><span class="k">Python</span><span class="v" id="envPy">-</span></div>
    <div class="env-item fixed"><span class="k">运行设备</span><span class="env-dev-btns" id="envDev"><button data-dev="cuda" onclick="setDevice('cuda')">GPU</button><button data-dev="cpu" onclick="setDevice('cpu')">CPU</button></span></div>
    <div class="env-item fixed">
      <span class="k">模型状态</span>
      <select class="env-select" id="envModel" onchange="handleModelAction(this.value)">
        <option value="status">未加载</option>
        <option value="load">加载模型</option>
      </select>
    </div>
    <div class="env-item env-path"><span class="k">模型目录</span><span class="v" id="envModelDir">-</span></div>
    <div class="env-item env-path"><span class="k">多音字 LoRA</span><span class="v" id="envLora">未挂载</span></div>
    <div class="env-item env-path"><span class="k">输出目录</span><span class="v" id="envOutDir">-</span></div>
    <div class="env-item env-path"><span class="k">音频保存于</span><span class="v" id="envOutSub">-</span></div>
  </div>
  <div class="header-right">
    <div class="theme-switch" id="themeSwitch">
      <button id="themeToggle" onclick="toggleTheme()" title="切换深浅色">🌙</button>
      <input type="color" id="customBg" onchange="setCustomBg(this.value)" title="自定义背景色">
    </div>
    <button class="status-badge" style="cursor:pointer" onclick="openSettings()" title="路径设置">⚙ 设置</button>
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
      <button class="voice-btn custom-save" onclick="deleteCustomPreset()" style="margin-top:4px;opacity:0.8;font-size:11px;">🗑 删除当前预设</button>
      <div class="cv-saved" id="cvSaved">✅ 已保存</div>
    </div>
  </aside>

  <!-- 主内容 -->
  <div class="content">

    <!-- 模型下载卡片（无模型版首次使用 / 通用更新校验） -->
    <div class="dl-card" id="dlCard" style="display:none">
      <div class="dl-card-head">
        <div class="dl-icon">📦</div>
        <div class="dl-head-text">
          <div class="dl-title" id="dlTitle">未检测到模型</div>
          <div class="dl-sub" id="dlSub">需要下载 VoxCPM2 主模型（约 5GB，支持断点续传）后才能合成。</div>
        </div>
        <div class="dl-spacer"></div>
        <button class="btn-primary" id="dlBtn" onclick="startModelDownload()">下载模型</button>
        <button class="btn-secondary" id="dlCancelBtn" style="display:none" onclick="cancelModelDownload()">取消</button>
      </div>
      <div class="dl-progress" id="dlProgress" style="display:none">
        <div class="progress-bar-wrap"><div class="progress-bar-fill" id="dlBar"></div></div>
        <div class="dl-progress-meta">
          <span class="progress-msg" id="dlMsg">准备中…</span>
          <span class="progress-pct" id="dlPct"></span>
        </div>
      </div>
      <div class="dl-fallback" id="dlFallback">
        也可双击安装目录下的「下载模型.bat」手动下载，或用网盘模型包解压到模型目录。
      </div>
    </div>

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
          <div class="param-desc-wrap"><div class="param-desc-line">↑ 质量更好但更慢</div></div>
        </div>
        <input type="range" id="stepsSlider" min="5" max="30" step="1" value="15">
      </div>
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">淡入淡出</div>
            <div class="param-value" id="crossfadeVal">80ms</div>
          </div>
          <div class="param-desc-wrap"><div class="param-desc-line">段间平滑过渡</div></div>
        </div>
        <input type="range" id="crossfadeSlider" min="0" max="200" step="10" value="80">
      </div>
      <div class="param-card">
        <div class="param-header">
          <div class="param-title">
            <div class="param-label">高级参数</div>
            <div class="param-value" id="chunkVal">180</div>
            <span class="param-unit">分段长度（字）</span>
          </div>
          <div class="param-toggles">
            <label class="param-toggle" title="数字归一化">
              <input type="checkbox" id="normalizeToggle" checked>
              <span>数字归一化</span>
            </label>
            <label class="param-toggle" title="音素输入模式：文本中的 {ni3}{hao3} 音素串将原样传给模型（自动关闭数字归一化）">
              <input type="checkbox" id="phonemeToggle">
              <span>音素输入</span>
            </label>
          </div>
        </div>
        <div class="param-slider-row">
          <input type="range" id="chunkSlider" min="60" max="400" step="20" value="180">
          <label style="font-size:11px;color:var(--text2, #8b93a7);white-space:nowrap;flex-shrink:0;" title="可复现种子（官方特性）：填同一整数 → 同文本/同设置结果近似一致，便于对比与复现；留空=随机">种子</label>
          <input type="text" id="seedInput" placeholder="留空=随机，如 42" style="width:96px;flex-shrink:0;font-size:11px;background:var(--surface, #1c2030);border:1px solid var(--border, #2a3045);border-radius:4px;color:var(--text, #e6e9f2);padding:3px 6px;outline:none;">
        </div>
      </div>
    </div>

    <!-- 文本输入 -->
    <div class="text-card">
      <div class="text-card-header">
        <h3>待合成文本</h3>
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
          <span class="char-count" id="charCount">0 字符</span>
          <button class="mode-btn" id="g2pBtn" onclick="g2pConvertText()" style="padding:4px 10px;font-size:11px;cursor:pointer;" title="将输入文本转换为 {ni3}{hao3} 音素串（需已下载 G2PW 离线模型）">🔤 转音素</button>
          <button class="mode-btn" id="txtUploadBtn" onclick="document.getElementById('txtFileInput').click()" style="padding:4px 10px;font-size:11px;cursor:pointer;">📄 上传TXT</button>
          <button class="mode-btn" id="normRulesBtn" onclick="openNormRulesEditor()" style="padding:4px 10px;font-size:11px;cursor:pointer;" title="查看 / 编辑数字归一化规则 num_norm_extra.txt（字面/正则两种，补充或覆盖内置读法），保存即生效（热加载，无需重启）">🔢 归一化规则</button>
          <button class="mode-btn" id="corpusEditBtn" onclick="openCorpusEditor()" style="padding:4px 10px;font-size:11px;cursor:pointer;" title="查看 / 编辑用户多音字语料 overlay_user_override.txt，保存即生效（热加载，无需重启）">✏️ 编辑语料</button>
          <input type="file" id="txtFileInput" accept=".txt,text/plain" style="display:none">
        </div>
      </div>
      <div class="text-area-wrap">
        <textarea id="textInput" rows="3" placeholder="在此输入要合成语音的文本..."></textarea>
        <div class="text-area-hint">或使用上方「上传TXT」按钮加载文本文件</div>
        <div class="text-area-resizer" id="textInputResizer" title="按住上下拖动，调整文本框高度"></div>
      </div>
    </div>

    <!-- 音色描述 + 示例 + 音色试听（试听横排在描述区下方，不拉宽外框） -->
    <div class="control-card">
      <h3>音色描述（可选，留空使用左侧预设；也可写方言/角色）</h3>
      <div class="prompt-resize-wrap">
        <textarea id="controlText" class="prompt-text-input" placeholder="例如：25岁温柔甜美女声，带一点播音腔。或『深宫太后，威严庄重』『河南方言大叔』"></textarea>
        <div class="prompt-resizer" id="controlTextResizer" title="按住上下拖动，调整高度"></div>
      </div>
      <div class="example-chips" id="exampleChips">
        <div class="preview-inline" style="display:flex;align-items:center;gap:6px;margin-left:auto;margin-right:36px;">
          <button id="voicePreviewBtn" class="mode-btn" onclick="runVoicePreview()" title="试听（有参考音频时直接播放，否则生成短句）" style="padding:4px 10px;font-size:12px;">▶ 试听</button>
          <canvas id="voicePreviewWave" width="180" height="28" style="width:180px;height:28px;background:var(--surface);border:1px solid var(--border);border-radius:4px;box-sizing:border-box;"></canvas>
          <audio id="voicePreviewAudio" preload="none" style="display:none;"></audio>
          <button id="previewPlayBtn" onclick="previewPlayPause()" title="播放/暂停" style="width:28px;height:28px;border-radius:50%;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:pointer;font-size:13px;line-height:1;">▶</button>
          <button id="previewDlBtn" onclick="previewDownload()" title="下载试听音频" style="padding:2px 6px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--text2);cursor:pointer;font-size:11px;">⬇</button>
          <select id="previewSpeed" onchange="previewSetSpeed(this.value)" title="语速" style="width:44px;height:24px;border-radius:4px;border:1px solid var(--border);background:var(--surface2);color:var(--text);font-size:11px;padding:0 2px;">
            <option value="0.5">0.5x</option><option value="0.75">0.75x</option><option value="1" selected>1x</option><option value="1.25">1.25x</option><option value="1.5">1.5x</option><option value="2">2x</option>
          </select>
          <input type="range" id="previewVolume" min="0" max="1" step="0.05" value="1" oninput="previewSetVolume(this.value)" title="音量" style="width:50px;height:4px;accent-color:var(--accent);cursor:pointer;">
          <div id="voicePreviewStatus" class="param-desc" style="margin:0;font-size:11px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">点击试听（有参考音频直接播放）</div>
        </div>
      </div>
    </div>

    <!-- 参考音频 -->
    <div class="ref-card">
      <h3 style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">音色统一模式（可选）
        <button class="mode-btn" id="profileBtn" onclick="openProfileManager()" style="padding:4px 10px;font-size:11px;cursor:pointer;" title="保存 / 应用 / 管理音色档案（voice + 音色描述 + 模式 + 参考音频）">🎚️ 音色档案</button>
        <button class="mode-btn" id="saveProfileBtn" onclick="saveCurrentAsProfile()" style="padding:4px 10px;font-size:11px;cursor:pointer;" title="将当前音色设置保存为固定参考克隆档案（需先试听生成参考音频）">💾 存档案</button>
        <span id="profileChips" style="display:inline-flex;gap:4px;flex-wrap:wrap;"></span>
      </h3>
      <div class="ref-modes">
        <button class="mode-btn ref-mode-btn active" data-mode="voice_design" onclick="setMode('voice_design', this)">
          <span class="mode-title">音色设计</span>
          <span class="mode-sub">文字描述音色</span>
        </button>
        <button class="mode-btn ref-mode-btn" data-mode="fixed_clone" onclick="setMode('fixed_clone', this)">
          <span class="mode-title">固定参考克隆</span>
          <span class="mode-sub">上传/录制参考音频</span>
        </button>
        <button class="mode-btn ref-mode-btn" data-mode="self_seeding" onclick="setMode('self_seeding', this)">
          <span class="mode-title">自播种</span>
          <span class="mode-sub">首段设计后续克隆</span>
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
        <div class="prompt-resize-wrap">
          <textarea id="promptText" class="prompt-text-input" placeholder="填入参考音频的原文转录，可显著提升音色相似度与稳定性（留空则为普通固定克隆）" oninput="onPromptInput()"></textarea>
          <div class="prompt-resizer" id="promptTextResizer" title="按住上下拖动，调整高度"></div>
        </div>
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
      <div class="history-head">
        <h3>最近合成记录</h3>
        <!-- 共享波形 + 调速：与标题同一行，显示当前播放/最近合成音频（与 #audioPlayer 同步） -->
        <canvas id="synthWave" class="head-wave" width="640" height="28"></canvas>
        <select id="synthSpeed" class="synth-speed" onchange="synthSpeedChange()" title="合成音频播放速度">
          <option value="0.5">0.5x</option>
          <option value="0.75">0.75x</option>
          <option value="1" selected>1x</option>
          <option value="1.25">1.25x</option>
          <option value="1.5">1.5x</option>
          <option value="2">2x</option>
        </select>
        <div class="history-actions">
          <button class="history-action-btn" onclick="restoreHistory()" title="恢复上次清除的记录">↩ 恢复</button>
          <button class="history-action-btn danger" onclick="clearHistory()" title="清空当前列表">✕ 清除</button>
        </div>
      </div>
      <div class="history-scroll">
        <div id="historyList">
          <div class="history-empty">暂无记录</div>
        </div>
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
    <div class="path-row" style="margin-top:10px">
      <button class="btn-secondary" onclick="startModelDownload()">下载 / 校验模型</button>
      <span class="param-desc" style="margin:0">主模型缺失或需更新时可用；约 5GB，支持断点续传。</span>
    </div>
    <div id="verifyResult"></div>
    <div class="param-label" style="margin-top:14px">Qwen3 时间戳对齐模型（可选）</div>
    <div class="path-row">
      <input id="qwenPathInput" class="path-input" placeholder="（自动管理；也可浏览指向已有 Qwen3 目录）">
      <button class="btn-secondary" onclick="selectFolder('qwenPathInput','选择 Qwen3 时间戳模型目录')">浏览...</button>
    </div>
    <div class="param-desc" id="qwenSub">模型读取/下载位置；修改后点「保存设置」生效。</div>
    <div class="path-row" style="margin-top:10px">
      <span id="qwenTitle" class="param-desc" style="margin:0;white-space:nowrap">状态检测中…</span>
      <button class="btn-secondary" id="qwenBtn" onclick="qwenDownload()" style="display:none">下载</button>
      <button class="btn-secondary" id="qwenDelBtn" onclick="qwenDelete()" style="display:none">删除</button>
      <span class="param-desc" style="margin:0">下载约 1.75GB；已安装可删除。</span>
    </div>
    <div class="dl-progress" id="qwenProgress" style="display:none;margin-top:8px">
      <div class="progress-bar-wrap"><div class="progress-bar-fill" id="qwenBar"></div></div>
      <div class="dl-progress-meta">
        <span class="progress-msg" id="qwenMsg">准备中…</span>
        <span class="progress-pct" id="qwenPct"></span>
      </div>
    </div>
    <div class="param-label" style="margin-top:14px">多音字修正 LoRA 权重（可选）</div>
    <div class="path-row">
      <input id="loraInput" class="path-input" placeholder="如 C:\...\lora_output\step_0005000">
      <button class="btn-secondary" onclick="selectFolder('loraInput','选择 LoRA 权重目录')">浏览...</button>
    </div>
    <div class="param-desc">训练产出的 step_XXXXXXX 目录（须含 lora_weights.safetensors 与 lora_config.json）。留空=使用原版模型；设置后下次合成将重载并挂载 LoRA 修正多音字读音。</div>
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

<!-- 多音字语料编辑弹窗 -->
<div class="modal-mask" id="corpusModal">
  <div class="modal" style="width:720px;max-width:92vw">
    <div class="modal-head">
      <h3>编辑多音字语料</h3>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="exportCorpusFile()">导出语料</button>
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="document.getElementById('corpusImportFile').click()">导入语料</button>
        <input type="file" id="corpusImportFile" accept=".txt,text/plain" style="display:none" onchange="importCorpusFile(this)">
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="closeCorpusEditor()">✕</button>
      </div>
    </div>
    <div class="param-desc" id="corpusPathHint" style="margin-bottom:8px">加载中...</div>
    <textarea id="corpusContent" spellcheck="false" style="width:100%;height:340px;font-family:var(--font-mono);font-size:12px;line-height:1.6;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px;box-sizing:border-box;resize:vertical;white-space:pre" placeholder="（文件为空或不存在，保存时将新建）"></textarea>
    <div class="param-desc" style="margin-top:8px">每行一条规则，格式：<code>上下文词 · 目标字: pypinyin默认=X → 强制=Y</code>（TONE3 数字声调，ü 写作 v，5 为轻声；以 # 开头的行为注释）。修改后点击「保存语料」即可，保存即生效（g2p 检测到文件变化自动热加载，无需重启）。</div>
    <div class="modal-actions">
      <button class="btn-secondary" onclick="closeCorpusEditor()">取消</button>
      <button class="btn-primary" style="flex:0 0 auto; padding:0 22px; height:40px" onclick="saveCorpus()">保存语料</button>
    </div>
  </div>
</div>

<!-- 数字归一化规则编辑弹窗（num_norm_extra.txt，与 text_norm_cn 同目录；保存即热加载） -->
<div class="modal-mask" id="normRulesModal">
  <div class="modal" style="width:720px;max-width:92vw">
    <div class="modal-head">
      <h3>编辑归一化规则</h3>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="closeNormRulesEditor()">✕</button>
      </div>
    </div>
    <div class="param-desc" id="normRulesPathHint" style="margin-bottom:8px">加载中...</div>
    <!-- 内置规则只读速览（来自 text_norm_cn.builtin_rule_summary；让用户知道已覆盖哪些场景，便于在下方补充） -->
    <details id="normRulesBuiltinDetails" style="margin-bottom:10px;border:1px solid var(--border);border-radius:8px;background:var(--surface2);">
      <summary style="padding:8px 12px;font-size:12px;font-weight:600;cursor:pointer;user-select:none;">📖 内置规则速览（只读，先看已覆盖哪些场景，再在下方用户规则里补空白）</summary>
      <pre id="normRulesBuiltin" style="margin:0;padding:10px 12px;font-size:11px;line-height:1.8;white-space:pre-wrap;word-break:break-all;font-family:var(--font-mono);max-height:240px;overflow:auto;color:var(--text);opacity:.85;">加载中...</pre>
    </details>
    <div class="param-desc" style="margin-bottom:6px;font-weight:600">用户规则（num_norm_extra.txt，可编辑；先于内置数字规则执行）</div>
    <textarea id="normRulesContent" spellcheck="false" style="width:100%;height:260px;font-family:var(--font-mono);font-size:12px;line-height:1.6;background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:10px;padding:10px;box-sizing:border-box;resize:vertical;white-space:pre" placeholder="（文件为空或不存在，保存时将新建）"></textarea>
    <div class="param-desc" style="margin-top:8px">每行一条规则，# 开头为注释。三种类型：<code>原文 =&gt; 读法</code>（如 <code>3.14 =&gt; 三点一四</code>，区分大小写）；<code>~原文 =&gt; 读法</code>（如 <code>~qwen-3.8 =&gt; 千问三点八</code>，不区分大小写，普通文本写法）；<code>?正则 =&gt; 替换</code>（如 <code>?0+(\d) =&gt; \1</code>，支持 \1 反向引用）。建议把读法直接写成中文，内置数字规则就不会再处理它；坏行会被自动跳过并警告，不会崩合成。保存即生效（自动热加载，无需重启）。</div>
    <div class="modal-actions">
      <button class="btn-secondary" onclick="closeNormRulesEditor()">取消</button>
      <button class="btn-primary" style="flex:0 0 auto; padding:0 22px; height:40px" onclick="saveNormRules()">保存规则</button>
    </div>
  </div>
</div>

<!-- 音色档案管理弹窗（REST 标准化：/api/profiles） -->
<div class="modal-mask" id="profileModal">
  <div class="modal" style="width:720px;max-width:92vw">
    <div class="modal-head">
      <h3>音色档案管理</h3>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="exportProfilesFile()">导出档案</button>
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="document.getElementById('profileImportFile').click()">导入档案</button>
        <input type="file" id="profileImportFile" accept=".json,application/json,.txt" style="display:none" onchange="importProfilesFile(this)">
        <button class="btn-secondary" style="height:32px;padding:0 12px" onclick="closeProfileManager()">✕</button>
      </div>
    </div>
    <div class="param-desc" id="profilePathHint" style="margin-bottom:8px">加载中...</div>
    <div class="param-desc" style="margin-bottom:8px">音色档案 = 当前界面音色设置的快照（预设 voice + 音色描述 + 模式 + 参考音频路径 + 提示文本）。保存后可随时一键应用。</div>
    <div style="display:flex;gap:8px;margin-bottom:10px;">
      <input type="text" id="profileNameInput" placeholder="新档案名称，如：深宫太后 / 新闻男主播" style="flex:1;height:36px;border:1px solid var(--border);border-radius:8px;background:var(--surface2);color:var(--text);padding:0 12px;box-sizing:border-box;">
      <button class="btn-primary" style="flex:0 0 auto;padding:0 18px;height:36px" onclick="saveProfileFromCurrent()">保存当前音色</button>
    </div>
    <div id="profileList" style="max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:6px;"></div>
    <div class="modal-actions">
      <button class="btn-secondary" onclick="closeProfileManager()">关闭</button>
      <button class="btn-secondary" onclick="refreshProfileList()">刷新列表</button>
    </div>
  </div>
</div>

<script>
// ── 全局状态 ─────────────────────────────────────
let selectedVoice = 'default';
let currentMode = 'voice_design';
let refFile = null;
let currentRefPath = '';   // 音色档案中的参考音频路径（fixed_clone 未重新上传时传给 /api/tts reference_path）
let lastPreviewUrl = '';   // 最后一次试听生成的 wav_url（播放用）
let lastPreviewPath = '';  // 最后一次试听生成的磁盘路径（存档案 reference_wav_path 用）
let recState = null;
let pollingInterval = null;
let currentJobId = null;
let history = [];
let currentPlayingWav = null;    // 当前播放器加载的音频文件名
let currentPlayBtnId = null;     // 当前显示为暂停的按钮 id

// ── 模型下载卡片状态 ─────────────────────────────
let _dlState = { status: 'idle' };
let _dlModelPresent = true;
let _dlAvailable = true;
let _dlPolling = false;

function renderDl() {
  const card = document.getElementById('dlCard');
  if (!card) return;
  if (!_dlAvailable) { card.style.display = 'none'; return; }
  const st = _dlState.status || 'idle';
  const active = (st === 'scanning' || st === 'downloading');
  // 展示条件：模型缺失时始终显示；模型已存在时仅下载进行中显示
  const show = (_dlModelPresent === false) || active;
  card.style.display = show ? 'block' : 'none';
  if (!show) return;

  const title = document.getElementById('dlTitle');
  const sub = document.getElementById('dlSub');
  const btn = document.getElementById('dlBtn');
  const cancelBtn = document.getElementById('dlCancelBtn');
  const progress = document.getElementById('dlProgress');
  const bar = document.getElementById('dlBar');
  const msg = document.getElementById('dlMsg');
  const pct = document.getElementById('dlPct');
  const fallback = document.getElementById('dlFallback');

  card.className = 'dl-card' + (st === 'done' ? ' done' : st === 'error' ? ' error' : '');

  if (st === 'idle') {
    title.textContent = '未检测到模型';
    sub.textContent = '需要下载 VoxCPM2 主模型（约 5GB，支持断点续传）后才能合成。';
    btn.style.display = 'inline-block'; btn.disabled = false; btn.textContent = '下载模型';
    cancelBtn.style.display = 'none';
    progress.style.display = 'none';
    fallback.style.display = 'block';
  } else if (active) {
    title.textContent = st === 'scanning' ? '正在检测模型文件…' : '正在下载模型…';
    sub.textContent = '下载在后台进行，可随时关闭本页；完成后回到「模型状态」加载即可。';
    btn.style.display = 'none';
    cancelBtn.style.display = 'inline-block';
    progress.style.display = 'block';
    fallback.style.display = 'none';
    const p = (_dlState.overall_percent != null) ? _dlState.overall_percent
            : (_dlState.percent != null ? _dlState.percent : 0);
    bar.style.width = p + '%';
    msg.textContent = _dlState.message || (_dlState.file ? ('当前: ' + _dlState.file) : '');
    pct.textContent = (_dlState.percent != null ? _dlState.percent + '%' : '');
    if (!_dlPolling) startDlPolling();
  } else if (st === 'done') {
    title.textContent = '模型下载完成';
    sub.textContent = '可前往右上角「模型状态 → 加载模型」开始使用。';
    btn.style.display = 'inline-block'; btn.disabled = true; btn.textContent = '已完成';
    cancelBtn.style.display = 'none';
    progress.style.display = 'block';
    bar.style.width = '100%';
    msg.textContent = _dlState.message || '';
    pct.textContent = '100%';
    fallback.style.display = 'none';
  } else { // error / cancelled
    title.textContent = st === 'error' ? '下载失败' : '已取消下载';
    sub.textContent = st === 'error'
      ? '请检查网络后重试，或双击「下载模型.bat」手动下载。'
      : '可重新点击下载，已下载部分将自动续传。';
    btn.style.display = 'inline-block'; btn.disabled = false; btn.textContent = '重新下载';
    cancelBtn.style.display = 'none';
    progress.style.display = 'none';
    fallback.style.display = 'block';
  }
}

function startDlPolling() {
  if (_dlPolling) return;
  _dlPolling = true;
  const iv = setInterval(async () => {
    try {
      const r = await fetch('/api/download-model/status');
      const d = await r.json();
      _dlState = d;
      renderDl();
      if (d.status !== 'scanning' && d.status !== 'downloading') {
        clearInterval(iv);
        _dlPolling = false;
        pollStatus();  // 刷新模型状态（下载完成后模型已就位）
      }
    } catch (e) {
      clearInterval(iv);
      _dlPolling = false;
    }
  }, 1000);
}

async function startModelDownload() {
  try {
    const r = await fetch('/api/download-model', { method: 'POST' });
    const d = await r.json();
    if (d.verified) {            // 模型已存在：展示真实校验结果
      renderVerify(d);
      showToast(d.message, d.all_ok ? 'success' : 'error');
      return;
    }
    if (!d.ok) { showToast(d.message || '无法启动下载', 'error'); return; }
    _dlState = { status: 'scanning', message: '正在检测模型文件…' };
    renderDl();
    startDlPolling();
  } catch (e) {
    showToast('启动失败: ' + e.message, 'error');
  }
}

function renderVerify(d) {
  const box = document.getElementById('verifyResult');
  if (!box) return;
  const files = d.files || [];
  let html = '<div class="dl-verify"><h4>模型文件校验结果</h4>';
  for (const f of files) {
    const cls = f.ok ? 'ok' : 'bad';
    const st = f.ok ? '✓ 正常' : ('✗ ' + (f.issue || '异常'));
    const sz = f.size ? (f.size / 1048576).toFixed(1) + ' MB' : '-';
    html += '<div class="vf ' + cls + '"><span class="nm">' + f.name +
            '</span><span class="sz">' + sz + '</span><span class="st">' + st + '</span></div>';
  }
  html += '</div>';
  box.innerHTML = html;
}

async function cancelModelDownload() {
  try {
    await fetch('/api/download-model/cancel', { method: 'POST' });
  } catch (e) {}
}

// ── qwen3 时间戳模型（可选下载/删除）───────────────
let _qwenPolling = false;
async function refreshQwenCard() {
  try {
    const r = await fetch('/api/qwen-model/status');
    const d = await r.json();
    const pathInput = document.getElementById('qwenPathInput');
    if (pathInput && d.model_dir) pathInput.value = d.model_dir;
    const title = document.getElementById('qwenTitle');
    const hint = document.getElementById('qwenStateHint');
    if (hint) hint.textContent = '';
    const btn = document.getElementById('qwenBtn');
    const delBtn = document.getElementById('qwenDelBtn');
    const prog = document.getElementById('qwenProgress');
    const bar = document.getElementById('qwenBar');
    const msg = document.getElementById('qwenMsg');
    const pct = document.getElementById('qwenPct');
    if (d.exists) {
      title.textContent = `已安装（${Math.round(d.size_mb/1024*100)/100}GB）`;
      btn.style.display = 'none';
      delBtn.style.display = 'inline-block';
      prog.style.display = 'none';
      if (!_qwenPolling && d.dl && d.dl.status === 'done') { msg.textContent = d.dl.message || ''; }
    } else {
      title.textContent = '未安装';
      btn.style.display = 'inline-block';
      delBtn.style.display = 'none';
      prog.style.display = 'none';
    }
    const st = d.dl ? d.dl.status : 'idle';
    if (st === 'downloading' || st === 'scanning') {
      prog.style.display = 'block';
      btn.style.display = 'none';
      delBtn.style.display = 'none';
      const p = d.dl.percent != null ? d.dl.percent : 0;
      bar.style.width = p + '%';
      msg.textContent = d.dl.message || '正在下载…';
      pct.textContent = p + '%';
      if (!_qwenPolling) { _qwenPolling = true; setTimeout(function tick() { refreshQwenCard().then(() => { if (_qwenPolling) setTimeout(tick, 2000); }); }, 2000); }
    } else if (st === 'error') {
      prog.style.display = 'block';
      bar.style.width = '100%';
      msg.textContent = d.dl.message || '下载失败';
      pct.textContent = '';
    }
  } catch (e) {}
}
async function qwenDownload() {
  const r = await fetch('/api/qwen-model/download', { method: 'POST' });
  const d = await r.json();
  alert(d.message || (d.ok ? '已开始下载。' : '无法开始下载。'));
  refreshQwenCard();
}
async function qwenDelete() {
  if (!confirm('确定删除本地 qwen3 时间戳模型（释放约 1.75GB）？删除后需重新下载才能用高精度模式。')) return;
  const r = await fetch('/api/qwen-model/delete', { method: 'POST' });
  const d = await r.json();
  alert(d.message || '');
  _qwenPolling = false;
  refreshQwenCard();
}

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
  // 逐步骤隔离执行：任一步失败不阻断后续绑定，避免「部分按钮无反应」
  const steps = [
    ['initTheme', initTheme],
    ['initConsole', initConsole],
    ['loadCustomVoices', loadCustomVoices],
    ['renderVoices', renderVoices],
    ['renderExamples', renderExamples],
    ['drawPreviewWavePlaceholder', () => { const c = document.getElementById('voicePreviewWave'); if (!c) return; const ctx = c.getContext('2d'); ctx.clearRect(0, 0, c.width, c.height); ctx.fillStyle = (getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#6c8eff') + '55'; const mid = c.height / 2; for (let i = 0; i < c.width; i += 4) { ctx.fillRect(i, mid - 1, 2, 2); } }],
    ['drawSynthWavePlaceholder', () => drawSynthWave(null, 0)],
    ['loadHistory', loadHistory],
    ['bindSliders', bindSliders],
    ['bindTextArea', bindTextArea],
    ['bindRefUpload', bindRefUpload],
    ['bindModelStatus', bindModelStatus],
    ['bindAudioPlayer', bindAudioPlayer],
    ['loadPaths', loadPaths],
    ['renderProfileChips', renderProfileChips],
    ['refreshQwenCard', refreshQwenCard],
  ];
  for (const [name, fn] of steps) {
    try {
      await fn();
    } catch (e) {
      console.warn('[init] 步骤失败(已隔离): ' + name, e);
    }
  }
  // 默认预设填入音色描述
  try {
    const defBtn = document.querySelector('.voice-btn[data-id="default"]');
    if (defBtn) selectVoice('default', defBtn);
  } catch (e) {
    console.warn('[init] 默认音色选择失败: ', e);
  }
  // 预热模型
  fetch('/api/ping').catch(() => {});
}

function renderVoices() {
  const grid = document.getElementById('voiceGrid');
  grid.innerHTML = '';
  const lockedReplacements = CUSTOM_VOICES.filter(v => v.replacesPreset).map(v => v.replacesPreset);
  const allIds = Object.keys(VOICE_LIST).filter(k => !lockedReplacements.includes(k))
    .concat(CUSTOM_VOICES.map(v => v.id));
  let savedOrder = [];
  try { savedOrder = JSON.parse(localStorage.getItem('voxcpm_voice_order') || '[]'); } catch {}
  if (savedOrder.length) {
    allIds.sort((a, b) => {
      const ia = savedOrder.indexOf(a), ib = savedOrder.indexOf(b);
      return ((ia === -1 ? 999 : ia)) - ((ib === -1 ? 999 : ib));
    });
  }
  for (const id of allIds) {
    const v = VOICE_LIST[id] || CUSTOM_VOICES.find(c => c.id === id);
    if (!v) continue;
    const isCustom = id.startsWith('custom_');
    const isLocked = id.startsWith('locked_');
    const isActive = id === selectedVoice;

    // 外层 .voice-item：包含拖拽手柄 + 按钮
    const wrapper = document.createElement('div');
    wrapper.className = 'voice-item';
    wrapper.dataset.id = id;

    // 整个卡片 mousedown 拖拽排序（移动>5px 才启动，不影响按钮点击）
    wrapper.addEventListener('mousedown', e => {
      if (e.button !== 0) return;
      const startX = e.clientX, startY = e.clientY;
      let dragging = false;
      const items = Array.from(grid.querySelectorAll('.voice-item'));
      const dragIdx = items.indexOf(wrapper);
      const onMove = ev => {
        const dx = ev.clientX - startX, dy = ev.clientY - startY;
        if (!dragging && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
          dragging = true;
          ev.preventDefault();
          wrapper.style.zIndex = '10';
          wrapper.style.position = 'relative';
          wrapper.style.transition = 'none';
          wrapper.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
        }
        if (dragging) {
          wrapper.style.transform = 'translateY(' + dy + 'px)';
          // 计算目标位置
          const gridTop = grid.getBoundingClientRect().top;
          const my = ev.clientY - gridTop;
          let targetIdx = 0;
          for (let i = 0; i < items.length; i++) {
            if (i === dragIdx) continue;
            const r = items[i].getBoundingClientRect();
            const mid = r.top + r.height / 2 - gridTop;
            if (my > mid) targetIdx = i + 1;
          }
          items.forEach((it, i) => {
            it.style.outline = '';
            if (i === targetIdx && targetIdx !== dragIdx) {
              it.style.outline = '2px solid var(--accent)';
              it.style.outlineOffset = '2px';
            }
          });
          wrapper._targetIdx = targetIdx;
        }
      };
      const onUp = () => {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (dragging) {
          const targetIdx = wrapper._targetIdx !== undefined ? wrapper._targetIdx : -1;
          wrapper.style.zIndex = '';
          wrapper.style.position = '';
          wrapper.style.transform = '';
          wrapper.style.transition = '';
          wrapper.style.boxShadow = '';
          const all = Array.from(grid.querySelectorAll('.voice-item'));
          all.forEach(it => { it.style.outline = ''; it.style.outlineOffset = ''; });
          if (targetIdx >= 0 && targetIdx !== dragIdx) {
            const dragEl = all[dragIdx];
            const refEl = all[targetIdx];
            if (targetIdx > dragIdx) { refEl.after(dragEl); } else { refEl.before(dragEl); }
            const newOrder = Array.from(grid.querySelectorAll('.voice-item')).map(it => it.dataset.id);
            try { localStorage.setItem('voxcpm_voice_order', JSON.stringify(newOrder)); } catch {}
          }
        }
      };
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });

    // 按钮主体
    const btn = document.createElement('button');
    btn.className = 'voice-btn' + (isActive ? ' active' : '') + (isCustom ? ' custom-preset' : '') + (isLocked ? ' locked-preset' : '');
    btn.dataset.id = id;
    btn.style.width = '100%';
    btn.style.textAlign = 'left';
    btn.onclick = () => selectVoice(id, btn);
    if (isLocked) {
      const displayName = (v.name || '').replace(/\s*🔒\s*$/g, '');
      btn.innerHTML = `<div class="voice-icon">🔒</div>
      <div style="flex:1;min-width:0">
        <div class="voice-name">${esc(displayName)}</div>
        <div class="voice-desc">${esc(v.desc || '固定克隆')}</div>
      </div>`;
      btn.style.background = 'var(--accent, #6c8eff)';
      btn.style.color = '#fff';
      btn.style.borderColor = 'var(--accent, #6c8eff)';
      const descEl = btn.querySelector('.voice-desc');
      if (descEl) descEl.style.color = 'rgba(255,255,255,0.8)';
      const nameEl = btn.querySelector('.voice-name');
      if (nameEl) nameEl.style.color = '#fff';
    } else {
      btn.innerHTML = `
      <div class="voice-icon">${v.icon}</div>
      <div>
        <div class="voice-name">${v.name}${isCustom ? ' <span style="font-size:10px;opacity:0.7">★</span>' : ''}</div>
        <div class="voice-desc">${v.desc}</div>
      </div>`;
    }

    // 档案 chip HTML5 drop 目标（保留）
    wrapper.addEventListener('dragover', e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      wrapper.style.outline = '2px dashed var(--accent)';
    });
    wrapper.addEventListener('dragleave', () => { wrapper.style.outline = ''; });
    wrapper.addEventListener('drop', e => {
      e.preventDefault();
      wrapper.style.outline = '';
      const data = e.dataTransfer.getData('text/plain');
      if (!data || data.startsWith('__voice__:')) return;
      const profileName = data;
      const lockedId = 'locked_' + Date.now();
      const lockedVoice = {
        id: lockedId,
        name: profileName,
        desc: '固定克隆',
        icon: '🔒',
        profileName: profileName,
        replacesPreset: VOICE_LIST[id] ? id : null,
      };
      const idx = CUSTOM_VOICES.findIndex(cv => cv.id === id);
      if (idx >= 0) {
        CUSTOM_VOICES[idx] = { ...lockedVoice, replacesPreset: CUSTOM_VOICES[idx].replacesPreset };
      } else {
        CUSTOM_VOICES.push(lockedVoice);
      }
      try { localStorage.setItem('voxcpm_custom_voices', JSON.stringify(CUSTOM_VOICES)); } catch {}
      renderVoices();
      const newWrapper = document.querySelector('.voice-item[data-id="' + lockedId + '"]');
      if (newWrapper) {
        const newBtn = newWrapper.querySelector('.voice-btn');
        if (newBtn) selectVoice(lockedId, newBtn);
      }
      applyProfile(profileName);
      showToast('已将档案「' + profileName + '」锁定到预设位', 'success');
    });

    wrapper.appendChild(btn);
    grid.appendChild(wrapper);
  }
}

function reorderVoices(dragId, targetId) {
  // 统一排序：所有可见预设共用一个顺序数组
  const lockedReplacements = CUSTOM_VOICES.filter(v => v.replacesPreset).map(v => v.replacesPreset);
  const allIds = Object.keys(VOICE_LIST).filter(k => !lockedReplacements.includes(k))
    .concat(CUSTOM_VOICES.map(v => v.id));
  // 移除被拖动的，插入到目标前
  const fromIdx = allIds.indexOf(dragId);
  if (fromIdx < 0) return;
  allIds.splice(fromIdx, 1);
  const toIdx = allIds.indexOf(targetId);
  if (toIdx < 0) allIds.splice(allIds.length, 0, dragId);
  else allIds.splice(toIdx, 0, dragId);
  try { localStorage.setItem('voxcpm_voice_order', JSON.stringify(allIds)); } catch {}
  renderVoices();
}

function renderExamples() {
  const box = document.getElementById('exampleChips');
  // 只清除旧 chips，保留预览区（.preview-inline）
  box.querySelectorAll('.example-chip').forEach(c => c.remove());
  const previewAnchor = box.querySelector('.preview-inline');
  for (const [label, desc] of EXAMPLES) {
    const chip = document.createElement('button');
    chip.className = 'example-chip';
    chip.textContent = label;
    chip.title = desc;
    chip.onclick = () => {
      selectVoice('default', document.querySelector('.voice-btn[data-id="default"]'));
      setMode('voice_design', document.querySelector('.mode-btn[data-mode="voice_design"]'));
      document.getElementById('controlText').value = desc;
    };
    if (previewAnchor) box.insertBefore(chip, previewAnchor);
    else box.appendChild(chip);
  }
}

function selectVoice(id, btn) {
  selectedVoice = id;
  document.querySelectorAll('.voice-btn').forEach(b => {
    b.classList.remove('active');
    b.style.boxShadow = '';
    b.style.borderColor = '';
  });
  btn.classList.add('active');
  // 将预设描述填入「音色描述」框，便于查看/微调
  const v = VOICE_LIST[id] || CUSTOM_VOICES.find(c => c.id === id);
  if (v) document.getElementById('controlText').value = v.desc || '';
  // 锁定预设自动应用档案设置
  if (v && v.profileName) {
    currentMode = 'fixed_clone';
    const cloneBtn = document.querySelector('.ref-mode-btn[data-mode="fixed_clone"]');
    if (cloneBtn) setMode('fixed_clone', cloneBtn);
    applyProfile(v.profileName, true);
  }
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

// 解码 ArrayBuffer → N-bin 归一化 peaks（与后端 /api/voice-preview 同口径）。
// 返回 { peaks, dur }：peaks 为归一化数组，dur 为解码时长(秒)——进度分母用它可避免
// audio.duration 起播前未就绪(Infinity) 导致的早期波形掉队/偏差。
async function peaksFromBuffer(ab, bins = 64) {
  try {
    const AC = window.AudioContext || window.webkitAudioContext;
    const ctx = new AC();
    const dec = await ctx.decodeAudioData(ab);
    const data = dec.getChannelData(0);
    const n = data.length;
    const BINS = bins;
    const peaks = new Array(BINS);
    let m = 0;
    for (let b = 0; b < BINS; b++) {
      const s = (b * n) / BINS | 0;
      const e = ((b + 1) * n) / BINS | 0;
      let mx = 0;
      for (let i = s; i < e; i++) { const v = data[i] < 0 ? -data[i] : data[i]; if (v > mx) mx = v; }
      peaks[b] = mx; if (mx > m) m = mx;
    }
    m = m || 1;
    const dur = dec.duration || (n / (dec.sampleRate || 24000));
    try { ctx.close(); } catch (e) {}
    return { peaks: peaks.map(p => p / m), dur };
  } catch (e) {
    return { peaks: null, dur: 0 };
  }
}

// ── 试听波形进度：共享 peaks + 可重启 rAF 循环（第1/2/N次播放都能跟）──
let _previewPeaks = null;    // 当前试听 64-bin peaks
let _previewDur = 0;         // 试听源时长(秒，与 peaks 同源；解码/后端口径，避免 audio.duration 未就绪早期偏差)
let _previewAnimId = 0;      // rAF 句柄（0=未运行）

function _previewAnimLoop() {
  const audio = document.getElementById('voicePreviewAudio');
  const _anim = () => {
    if (_previewAnimId === 0) return;          // 已停止
    if (audio.ended) {
      if (_previewPeaks) drawPreviewWave(_previewPeaks);   // 复位整条
      _previewAnimId = 0;                     // 停循环，等下次播放重启
      return;
    }
    if (!audio.paused && _previewPeaks) {
      // 进度分母优先用与 peaks 同源的 _previewDur；仅在不可用时才回退 audio.duration
      const denom = _previewDur > 0 ? _previewDur : (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0);
      if (denom > 0) drawPreviewWave(_previewPeaks, Math.min(1, audio.currentTime / denom));
    }
    _previewAnimId = requestAnimationFrame(_anim);
  };
  _previewAnimId = requestAnimationFrame(_anim);
}

function ensurePreviewAnim() {
  if (_previewAnimId) return;      // 已在跑
  if (!_previewPeaks) return;      // 还没 peaks
  _previewAnimLoop();
}

// ── 音色试听：以当前音色设置生成一段短句 + 波形显示（/api/voice-preview）──
async function runVoicePreview() {
  const btn = document.getElementById('voicePreviewBtn');
  const status = document.getElementById('voicePreviewStatus');
  const audio = document.getElementById('voicePreviewAudio');

  // ── 直接试听：已有加载的参考音频（音色档案 currentRefPath 或上传的 refFile）时，
  //    直接播放该参考（免 GPU 推理、秒出），不重新生成短句；无参考才生成 ──
  if (currentMode === 'fixed_clone' && (refFile || currentRefPath)) {
    // 一次取参考音频，既用于播放又用于算波形（避免双 fetch + 时延）
    let directUrl = '';
    let ab = null;
    if (refFile) {
      directUrl = URL.createObjectURL(refFile);      // 原 File 播放（duration 元数据可靠）
      ab = await refFile.arrayBuffer();
    } else {
      const fname = currentRefPath.split(/[\\/]/).pop();
      const blob = await (await fetch('/api/audio/' + encodeURIComponent(fname))).blob();
      directUrl = URL.createObjectURL(blob);
      ab = await blob.arrayBuffer();
    }
    _previewDur = 0;
    if (ab) { const r = await peaksFromBuffer(ab); _previewPeaks = r.peaks; _previewDur = r.dur || 0; } else { _previewPeaks = null; }   // 共享波形进度 + 时长(与 peaks 同源)
    audio.src = directUrl;
    lastPreviewUrl = directUrl;
    if (currentRefPath && !refFile) lastPreviewPath = currentRefPath;   // 档案参考→可再存档案
    status.style.color = '';
    status.textContent = '▶ 直接试听参考音频（未重新合成）';
    if (_previewPeaks) drawPreviewWave(_previewPeaks);
    const pb = document.getElementById('previewPlayBtn');
    try {
      await audio.play();
      pb.textContent = '⏸'; pb.title = '暂停';
    } catch (e) {
      status.textContent = '（浏览器限制自动播放，点 ▶）';
    }
    audio.onended = () => { pb.textContent = '▶'; pb.title = '播放'; };
    ensurePreviewAnim();
    return;
  }

  // ── 否则：无参考音频（纯预设/音色设计），生成一段短句试听 ──
  btn.disabled = true;
  status.textContent = '正在生成试听样本...';
  const fd = new FormData();
  fd.append('voice', selectedVoice);
  fd.append('control_text', document.getElementById('controlText').value.trim());
  fd.append('mode', currentMode);
  fd.append('cfg', document.getElementById('cfgSlider').value);
  fd.append('steps', document.getElementById('stepsSlider').value);
  fd.append('denoise', document.getElementById('denoiseToggle').checked ? 'true' : 'false');
  if (currentMode === 'fixed_clone') {
    if (refFile) fd.append('reference_wav', refFile);
    else if (currentRefPath) fd.append('reference_path', currentRefPath);
  }
  try {
    const r = await fetch('/api/voice-preview', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) { status.textContent = '⚠ ' + (d.error || '试听失败'); status.style.color = 'var(--err,#e55)'; return; }
    status.style.color = '';
    _previewPeaks = d.peaks || [];          // 共享波形进度
    _previewDur = d.duration || 0;         // 试听时长(与 peaks 同源，避免 audio.duration 未就绪早期偏差)
    drawPreviewWave(_previewPeaks);
    audio.src = d.wav_url;
    lastPreviewUrl = d.wav_url;
    lastPreviewPath = d.file_path || '';
    ensurePreviewAnim();                    // 起/续波形进度动画
    try { await audio.play(); document.getElementById('previewPlayBtn').textContent = '⏸'; } catch (e) { status.textContent = '（浏览器限制自动播放，点 ▶）'; }
    audio.onended = () => { const b = document.getElementById('previewPlayBtn'); b.textContent = '▶'; b.title = '播放'; };
    status.textContent = '已生成（约 ' + d.duration + 's）';
  } catch (e) {
    status.textContent = '⚠ 试听失败: ' + (e.message || e);
    status.style.color = 'var(--err,#e55)';
  } finally {
    btn.disabled = false;
  }
}

function previewPlayPause() {
  const audio = document.getElementById('voicePreviewAudio');
  const playBtn = document.getElementById('previewPlayBtn');
  if (!audio.src) return;
  if (audio.paused) {
    if (audio.ended) audio.currentTime = 0;    // 结束后重播→从头
    audio.play().catch(() => {});
    playBtn.textContent = '⏸'; playBtn.title = '暂停';
    ensurePreviewAnim();                       // 每次播放都重启波形进度（第2次也跟随）
  } else {
    audio.pause();
    playBtn.textContent = '▶'; playBtn.title = '播放';
  }
}

function previewDownload() {
  const audio = document.getElementById('voicePreviewAudio');
  if (!audio.src) return;
  const a = document.createElement('a');
  a.href = audio.src;
  a.download = 'voice_preview.wav';
  a.click();
}

function previewSetSpeed(v) {
  const audio = document.getElementById('voicePreviewAudio');
  audio.playbackRate = parseFloat(v) || 1;
}

function previewSetVolume(v) {
  const audio = document.getElementById('voicePreviewAudio');
  audio.volume = parseFloat(v);
}

function drawPreviewWave(peaks, progress = 0) {
  const c = document.getElementById('voicePreviewWave');
  if (!c) return;
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, c.width, c.height);
  const n = peaks.length;
  if (!n) return;
  const cw = c.width / n;
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#6c8eff';
  const dim = accent + '44';
  const progressIdx = Math.floor(progress * n);
  for (let i = 0; i < n; i++) {
    const p = peaks[i] || 0;
    const h = Math.max(2, p * (c.height - 4));
    ctx.fillStyle = i < progressIdx ? accent : dim;
    ctx.fillRect(i * cw + 1, (c.height - h) / 2, Math.max(1, cw - 2), h);
  }
  if (progress > 0 && progress < 1) {
    ctx.fillStyle = accent;
    ctx.fillRect(progress * c.width, 0, 1.5, c.height);
  }
}

// ── 共享波形（底部）+ 调速 ───────────────────────
// 显示“最近/正在播放”的完整合成音频波形，与 #audioPlayer 进度同步；
// 进度分母用解码时长 _synthDur（与 peaks 同源），避免 audio.duration 未就绪早期掉队。
let _synthPeaks = null, _synthDur = 0, _synthWav = '', _synthAnimId = 0;
let _synthPeaksCache = {};   // wavName -> {peaks, dur}

function drawSynthWave(peaks, progress = 0) {
  const c = document.getElementById('synthWave');
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  let W = c.width, H = c.height;
  const cwCss = c.clientWidth, chCss = c.clientHeight;
  if (cwCss > 0 && chCss > 0) {
    const tW = Math.round(cwCss * dpr), tH = Math.round(chCss * dpr);
    if (tW !== c.width || tH !== c.height) { c.width = tW; c.height = tH; }
    W = c.width; H = c.height;
  }
  const ctx = c.getContext('2d');
  ctx.clearRect(0, 0, W, H);
  const accent = getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#6c8eff';
  const n = peaks ? peaks.length : 0;
  if (!n) {
    ctx.fillStyle = accent + '55';
    const mid = H / 2;
    for (let x = 0; x < W; x += 4 * dpr) ctx.fillRect(x, mid - dpr, 2 * dpr, 2 * dpr);
    return;
  }
  const BINS = n, cw = W / BINS, dim = accent + '44';
  const progressIdx = Math.floor(Math.max(0, Math.min(1, progress)) * BINS);
  for (let i = 0; i < BINS; i++) {
    const p = peaks[i] || 0;
    const h = Math.max(2 * dpr, p * (H - 4 * dpr));
    ctx.fillStyle = i < progressIdx ? accent : dim;
    ctx.fillRect(i * cw + 1, (H - h) / 2, Math.max(1, cw - 2), h);
  }
  if (progress > 0 && progress < 1) { ctx.fillStyle = accent; ctx.fillRect(progress * W, 0, 1.5 * dpr, H); }
}

function _synthAnimLoop() {
  const audio = document.getElementById('audioPlayer');
  const _anim = () => {
    if (_synthAnimId === 0) return;
    if (audio.ended) {
      if (_synthPeaks) drawSynthWave(_synthPeaks);
      _synthAnimId = 0;
      return;
    }
    if (!audio.paused && _synthPeaks && currentPlayingWav === _synthWav) {
      const denom = _synthDur > 0 ? _synthDur : (isFinite(audio.duration) && audio.duration > 0 ? audio.duration : 0);
      if (denom > 0) drawSynthWave(_synthPeaks, Math.min(1, audio.currentTime / denom));
    }
    _synthAnimId = requestAnimationFrame(_anim);
  };
  _synthAnimId = requestAnimationFrame(_anim);
}
function ensureSynthAnim() { if (!_synthAnimId && _synthPeaks) _synthAnimLoop(); }

async function showSynthWave(wavName) {
  const token = wavName;
  _synthWav = wavName;
  if (_synthPeaksCache[wavName]) {
    _synthPeaks = _synthPeaksCache[wavName].peaks;
    _synthDur = _synthPeaksCache[wavName].dur || 0;
  } else {
    try {
      const blob = await (await fetch('/api/audio/' + encodeURIComponent(wavName))).blob();
      const ab = await blob.arrayBuffer();
      const r = await peaksFromBuffer(ab, 160);
      if (token !== _synthWav) return;   // 已有更新的请求，丢弃此陈旧结果
      _synthPeaks = r.peaks; _synthDur = r.dur || 0;
      if (_synthPeaks) _synthPeaksCache[wavName] = { peaks: _synthPeaks, dur: _synthDur };
    } catch (e) {
      if (token !== _synthWav) return;
      _synthPeaks = null; _synthDur = 0;
    }
  }
  if (token !== _synthWav) return;
  drawSynthWave(_synthPeaks, 0);
  applySynthSpeed();
  ensureSynthAnim();
}

// 调速：控制 #audioPlayer.playbackRate（底部合成音频）
function applySynthSpeed() {
  const sel = document.getElementById('synthSpeed');
  const p = document.getElementById('audioPlayer');
  if (sel && p) p.playbackRate = parseFloat(sel.value) || 1;
}
function synthSpeedChange() { applySynthSpeed(); }

function bindSliders() {
  const cfg = document.getElementById('cfgSlider');
  const steps = document.getElementById('stepsSlider');
  const xf = document.getElementById('crossfadeSlider');
  const chunk = document.getElementById('chunkSlider');
  cfg.oninput = () => document.getElementById('cfgVal').textContent = cfg.value;
  steps.oninput = () => document.getElementById('stepsVal').textContent = steps.value;
  xf.oninput = () => document.getElementById('crossfadeVal').textContent = xf.value + 'ms';
  chunk.oninput = () => document.getElementById('chunkVal').textContent = chunk.value;

  function updateRangeProgress(range) {
    const min = range.min ? Number(range.min) : 0;
    const max = range.max ? Number(range.max) : 100;
    const val = Number(range.value);
    const pct = max === min ? 0 : ((val - min) / (max - min) * 100);
    range.style.setProperty('--value-percent', pct + '%');
  }
  document.querySelectorAll('input[type="range"]').forEach(r => {
    updateRangeProgress(r);
    r.addEventListener('input', () => updateRangeProgress(r));
  });

  // 音素输入开关联动：开启音素模式时自动关闭数字归一化（官方要求 normalize=False）
  const phonemeToggle = document.getElementById('phonemeToggle');
  const normalizeToggle = document.getElementById('normalizeToggle');
  if (phonemeToggle && normalizeToggle) {
    phonemeToggle.addEventListener('change', () => {
      if (phonemeToggle.checked) {
        normalizeToggle.checked = false;
        showToast && showToast('已开启音素输入：文本中的 {ni3}{hao3} 将原样传给模型（数字归一化已自动关闭）', 'info');
      }
    });
  }
}

// G2P：将输入文本转为 {ni3}{hao3} 音素串并回填，同时自动开启音素模式
async function g2pConvertText() {
  const ta = document.getElementById('textInput');
  const btn = document.getElementById('g2pBtn');
  const text = ta.value.trim();
  if (!text) { showToast && showToast('请先输入要转换的文本', 'error'); return; }
  if (btn) { btn.disabled = true; btn.textContent = '⏳ 转换中...'; }
  try {
    const r = await fetch('/api/g2p', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: text}) });
    const d = await r.json();
    if (d.error) { showToast && showToast(d.error, 'error'); return; }
    ta.value = d.phonemes;
    document.getElementById('charCount').textContent = ta.value.length + ' 字符';
    const pt = document.getElementById('phonemeToggle');
    if (pt && !pt.checked) pt.click();
    showToast && showToast('已转换为音素串，音素输入模式已开启', 'info');
  } catch (e) {
    showToast && showToast('G2P 转换失败: ' + (e.message || e), 'error');
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '🔤 转音素'; }
  }
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

  // 文本框高度拖拽（自绘手柄：按住底部横条上下拖动，脱离 flex 拉伸后手动控高）
  const wrap = document.querySelector('.text-area-wrap');
  const rz = document.getElementById('textInputResizer');
  if (wrap && rz) {
    rz.addEventListener('mousedown', (e) => {
      e.preventDefault();
      rz.classList.add('dragging');
      const startY = e.clientY;
      const startH = wrap.getBoundingClientRect().height;
      wrap.style.flex = '0 0 auto';            // 脱离卡片 stretch，进入手动控高
      wrap.style.height = startH + 'px';
      const card = wrap.closest('.text-card');
      if (card) { card.style.flex = '0 0 auto'; card.style.minHeight = 'auto'; }  // 外边框跟随收缩/扩张
      document.body.style.userSelect = 'none';
      document.body.style.cursor = 'ns-resize';
      const move = (ev) => {
        let h = startH + (ev.clientY - startY);
        h = Math.max(94, Math.min(900, Math.round(h)));   // 下限=三行默认高94px，可拖回3行
        wrap.style.height = h + 'px';
      };
      const up = () => {
        document.removeEventListener('mousemove', move);
        document.removeEventListener('mouseup', up);
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        rz.classList.remove('dragging');
      };
      document.addEventListener('mousemove', move);
      document.addEventListener('mouseup', up);
    });
  }

  // 音色描述 / 参考转录 的自绘调高手柄（同 #textInput）
  bindPromptResizer('controlText');
  bindPromptResizer('promptText');
}

// 自绘调高手柄：按住底部横条上下拖动，调整 textarea 高度（.prompt-text-input 已设 resize:none）
function bindPromptResizer(taId) {
  const ta = document.getElementById(taId);
  const rz = document.getElementById(taId + 'Resizer');
  if (!ta || !rz) return;
  rz.addEventListener('mousedown', (e) => {
    e.preventDefault();
    rz.classList.add('dragging');
    const startY = e.clientY;
    const startH = ta.getBoundingClientRect().height;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'ns-resize';
    const move = (ev) => {
      let h = startH + (ev.clientY - startY);
      h = Math.max(56, Math.min(600, Math.round(h)));
      ta.style.height = h + 'px';
    };
    const up = () => {
      document.removeEventListener('mousemove', move);
      document.removeEventListener('mouseup', up);
      document.body.style.userSelect = '';
      document.body.style.cursor = '';
      rz.classList.remove('dragging');
    };
    document.addEventListener('mousemove', move);
    document.addEventListener('mouseup', up);
  });
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
    // 同步下载卡片状态
    _dlAvailable = !!d.download_available;
    _dlModelPresent = d.model_present;
    if (d.download) _dlState = d.download;
    renderDl();
    if (d.model_present === false) {
      dot.className = 'status-dot error';
      txt.textContent = '模型缺失（可点页面内下载）';
      return;
    }
    dot.className = 'status-dot ' + state;
    const labels = { ready: '模型就绪', loading: '加载中...', error: '加载失败', idle: '未加载' };
    txt.textContent = labels[state] || state;

    // 同步顶部环境栏里的「模型状态」下拉
    renderModelState(state, state === 'ready');
    // 同步多音字 LoRA 挂载状态（每 3s 自动刷新，避免模型加载完后仍显示「待加载」）
    renderLoraStatus(d.lora);
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
    document.getElementById('loraInput').value = d.lora_weights_path || '';
  } catch {}
  refreshQwenCard();
  document.getElementById('settingsModal').style.display = 'flex';
}
function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }
async function savePaths() {
  const model_dir = document.getElementById('modelDirInput').value.trim();
  const output_dir = document.getElementById('outputDirInput').value.trim();
  const lora_weights_path = document.getElementById('loraInput').value.trim();
  const ts_model_dir = document.getElementById('qwenPathInput').value.trim();
  try {
    const r = await fetch('/api/set_config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model_dir, output_dir, lora_weights_path, ts_model_dir })
    });
    const d = await r.json();
    if (d.ok) {
      let msg = '路径已保存';
      if (model_dir) msg += '，下次合成将重载模型';
      if (lora_weights_path) msg += '，多音字 LoRA 已配置（下次合成或点「加载模型」时挂载）';
      showToast(msg, 'success');
      closeSettings();
      loadPaths();
      refreshStatus();
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

function deleteVoiceById(id) {
  const idx = CUSTOM_VOICES.findIndex(v => v.id === id);
  if (idx < 0) { showToast('预设不存在', 'error'); return; }
  const removed = CUSTOM_VOICES[idx];
  if (!confirm('确认删除预设「' + removed.name + '」？')) return;
  CUSTOM_VOICES.splice(idx, 1);
  try { localStorage.setItem('voxcpm_custom_voices', JSON.stringify(CUSTOM_VOICES)); } catch {}
  if (selectedVoice === id) {
    selectedVoice = 'default';
  }
  renderVoices();
  const btn = document.querySelector('.voice-btn[data-id="' + selectedVoice + '"]');
  if (btn) btn.classList.add('active');
  showToast('已删除预设「' + removed.name + '」', 'success');
}

function deleteCustomPreset() {
  // 删除当前选中的自定义/锁定预设；若未选中则删除最后一个自定义预设
  let idx = CUSTOM_VOICES.findIndex(v => v.id === selectedVoice);
  if (idx < 0 && CUSTOM_VOICES.length > 0) {
    idx = CUSTOM_VOICES.length - 1;  // 兖底：删最后一个
  }
  if (idx < 0) { showToast('没有可删除的自定义预设', 'info'); return; }
  deleteVoiceById(CUSTOM_VOICES[idx].id);
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

function renderLoraStatus(lora) {
  const loraEl = document.getElementById('envLora');
  lora = lora || {};
  let loraText = '未挂载', loraCls = '';
  if (lora.status === 'pending') {
    loraText = '已配置，待加载';
  } else if (lora.status === 'ok') {
    loraText = '已挂载 ' + lora.loaded + ' 参数' + (lora.skipped ? ('（跳过 ' + lora.skipped + '）') : '');
    loraCls = ' good';
  } else if (lora.status === 'failed') {
    loraText = '挂载失败：' + (lora.resolve_error || '未知原因');
    loraCls = ' bad';
  }
  loraEl.textContent = loraText;
  loraEl.title = lora.path || loraText;
  loraEl.className = 'v' + loraCls;
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
  // 多音字 LoRA 真实挂载状态（不再谎称「已挂载」）
  renderLoraStatus(d.lora);
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
      showToast('命令行窗口切换失败（控制台创建/操作未成功），可再试一次', 'info');
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
    // 仅非 Windows 平台隐藏；Windows 下控制台切换始终可用（无控制台时后端会按需创建一个）
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
    // A 方案：文字颜色自动反色
    root.style.setProperty('--text', dark ? '#ffffff' : '#1a1f2b');
    root.style.setProperty('--text2', dark ? '#c0c6d0' : '#5b6678');
    // B 方案：半透明叠加层，与背景拉开层次（保持背景色可见但文字清晰）
    root.style.setProperty('--surface', dark ? 'rgba(0,0,0,0.36)' : 'rgba(255,255,255,0.78)');
    root.style.setProperty('--surface2', dark ? 'rgba(0,0,0,0.48)' : 'rgba(255,255,255,0.86)');
    root.style.setProperty('--border', dark ? 'rgba(255,255,255,0.18)' : 'rgba(0,0,0,0.12)');
    // 输入框使用独立背景色，确保任何背景色下文本清晰可读
    root.style.setProperty('--input-bg', dark ? 'rgba(22, 23, 30, 0.92)' : 'rgba(255, 255, 255, 0.92)');
    root.style.setProperty('--input-bg-focus', dark ? 'rgba(14, 15, 19, 0.96)' : 'rgba(255, 255, 255, 0.96)');
    document.body.classList.add('custom-bg-active');
  } else {
    root.setAttribute('data-theme', theme);
    ['--bg','--surface','--surface2','--border','--text','--text2','--input-bg','--input-bg-focus'].forEach(v => root.style.removeProperty(v));
    document.body.classList.remove('custom-bg-active');
  }
  const tgl = document.getElementById('themeToggle');
  if (tgl) {
    let base = theme;
    if (theme === 'custom' && customBg) {
      const c = hexToRgb(customBg);
      const lum = (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255;
      base = lum < 0.5 ? 'dark' : 'light';
    }
    // 图标跟随「当前」主题：浅色=☀️(太阳)、深色=🌙(月亮)；点击切到相反主题
    tgl.textContent = (base === 'light') ? '☀️' : '🌙';
    tgl.title = '切换到' + (base === 'light' ? '深色' : '浅色');
    tgl.classList.toggle('active', theme !== 'custom');
  }
  const colorInput = document.getElementById('customBg');
  if (colorInput) colorInput.classList.toggle('active', theme === 'custom');
  localStorage.setItem('voxcpm_theme', theme);
  if (theme === 'custom') localStorage.setItem('voxcpm_custom_bg', customBg);
}
function setTheme(t) { applyTheme(t); }
function toggleTheme() {
  const root = document.documentElement;
  let cur = root.getAttribute('data-theme') || 'dark';
  if (cur === 'custom') {
    const bg = localStorage.getItem('voxcpm_custom_bg') || '#000';
    const c = hexToRgb(bg);
    const lum = (0.2126 * c.r + 0.7152 * c.g + 0.0722 * c.b) / 255;
    cur = lum < 0.5 ? 'dark' : 'light';
  }
  applyTheme(cur === 'light' ? 'dark' : 'light');
}
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
  formData.append('phoneme_mode', document.getElementById('phonemeToggle').checked ? 'true' : 'false');
  formData.append('denoise', document.getElementById('denoiseToggle').checked ? 'true' : 'false');
  formData.append('target_sr', localStorage.getItem('voxcpm_sr') || 'native');
  formData.append('seed', (document.getElementById('seedInput') || { value: '' }).value.trim());
  const pt = document.getElementById('promptText').value.trim();
  if (pt) formData.append('prompt_text', pt);
  if (refFile && currentMode === 'fixed_clone') {
    formData.append('reference_wav', refFile);
  } else if (currentRefPath && currentMode === 'fixed_clone') {
    formData.append('reference_path', currentRefPath);
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
  addHistory({ text: document.getElementById('textInput').value.slice(0, 50), wav: d.output_wav, filename: d.output_wav, duration: d.duration });
  togglePlayAudio(d.output_wav, null);
  document.getElementById('progressCard').classList.remove('visible');
  resetBtn();
}

function resetBtn() {
  document.getElementById('synthBtn').disabled = false;
  document.getElementById('synthBtnIcon').textContent = '🔊';
  document.getElementById('synthBtnText').textContent = '开始合成';
}

// ── 音频播放 ─────────────────────────────────────
function bindAudioPlayer() {
  const player = document.getElementById('audioPlayer');
  player.onended = () => setPlayBtnState(null, false);
  player.onpause  = () => { if (player.ended) return; setPlayBtnState(currentPlayBtnId, false); };
  player.onplay   = () => setPlayBtnState(currentPlayBtnId, true);
}

function setPlayBtnState(btnId, isPlaying) {
  if (!btnId) {
    // 没有任何按钮应该显示暂停：全部重置为 ▶
    document.querySelectorAll('.history-play').forEach(b => b.textContent = '▶');
    currentPlayBtnId = null;
    return;
  }
  const btn = document.getElementById(btnId);
  if (btn) btn.textContent = isPlaying ? '⏸' : '▶';
  if (isPlaying) {
    currentPlayBtnId = btnId;
    // 其他按钮全部重置为 ▶
    document.querySelectorAll('.history-play').forEach(b => { if (b.id !== btnId) b.textContent = '▶'; });
  }
}

async function togglePlayAudio(wavName, btnId) {
  const player = document.getElementById('audioPlayer');
  if (currentPlayingWav === wavName && !player.paused) {
    player.pause();
    setPlayBtnState(btnId, false);
    return;
  }
  if (currentPlayingWav === wavName && player.paused) {
    await player.play();
    setPlayBtnState(btnId, true);
    ensureSynthAnim();
    return;
  }
  // 切换到新音频
  currentPlayingWav = wavName;
  player.src = '/api/audio/' + wavName;
  setPlayBtnState(currentPlayBtnId, false);   // 先把旧按钮重置
  applySynthSpeed();                          // 新资源会重置 playbackRate，重新应用所选速度
  try {
    await player.play();
    setPlayBtnState(btnId, true);
    showSynthWave(wavName);                  // 同步底部共享波形
  } catch {
    setPlayBtnState(btnId, false);
  }
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

function clearHistory() {
  if (!history.length) return;
  // 备份到 localStorage，便于「恢复」撤销清除
  localStorage.setItem('voxcpm_history_backup', JSON.stringify(history));
  history = [];
  localStorage.setItem('voxcpm_history', '[]');
  renderHistory();
  // 当前播放的音频按钮也要重置
  setPlayBtnState(null, false);
  showToast('已清空合成记录（可点击「恢复」撤销）', 'success');
}

function restoreHistory() {
  try {
    const backup = localStorage.getItem('voxcpm_history_backup');
    if (!backup) { showToast('没有可恢复的记录', 'error'); return; }
    const restored = JSON.parse(backup);
    if (!Array.isArray(restored) || !restored.length) { showToast('没有可恢复的记录', 'error'); return; }
    history = restored.slice(0, 20);
    localStorage.setItem('voxcpm_history', JSON.stringify(history));
    renderHistory();
    showToast('已恢复上次清除的记录', 'success');
  } catch (e) {
    showToast('恢复失败: ' + e.message, 'error');
  }
}

function renderHistory() {
  const list = document.getElementById('historyList');
  if (!history.length) { list.innerHTML = '<div class="history-empty">暂无记录</div>'; return; }
  list.innerHTML = history.map((h, i) => `
    <div class="history-item">
      <button class="history-play" id="historyPlay-${i}" onclick="togglePlayAudio('${h.wav}', 'historyPlay-${i}')">▶</button>
      <div class="history-info">
        <div class="history-text">${escHtml(h.text)}</div>
        <div class="history-meta">${h.time} · ${h.duration ? h.duration.toFixed(1) + 's' : ''} · ${escHtml(h.filename || h.wav)}</div>
      </div>
      <a class="history-download" href="/api/audio/${h.wav}" download="${escHtml(h.filename || h.wav)}">下载</a>
    </div>`).join('');
  // 重绘后如果当前播放音频仍在列表中，恢复对应按钮状态
  if (currentPlayingWav && currentPlayBtnId) {
    const btn = document.getElementById(currentPlayBtnId);
    const player = document.getElementById('audioPlayer');
    if (btn && player && !player.paused) btn.textContent = '⏸';
  }
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

// ── 多音字语料编辑（overlay_user_override.txt）──
async function openCorpusEditor() {
  const mask = document.getElementById('corpusModal');
  const hint = document.getElementById('corpusPathHint');
  const box = document.getElementById('corpusContent');
  mask.style.display = 'flex';
  hint.textContent = '加载中...';
  box.value = '';
  try {
    const r = await fetch('/api/corpus');
    const d = await r.json();
    hint.textContent = d.exists
      ? '文件：' + d.path + '（保存即生效，无需重启）'
      : '用户语料文件不存在，保存时将新建：' + (d.path || '');
    box.value = d.content || '';
  } catch (e) {
    hint.textContent = '读取失败: ' + (e.message || e);
  }
}
function closeCorpusEditor() {
  document.getElementById('corpusModal').style.display = 'none';
}
async function saveCorpus() {
  const btn = document.querySelector('#corpusModal .btn-primary');
  const content = document.getElementById('corpusContent').value;
  btn.disabled = true;
  btn.textContent = '保存中...';
  try {
    const r = await fetch('/api/corpus', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content })
    });
    const d = await r.json();
    if (d.ok) {
      showToast(d.message || '已保存（保存即生效，无需重启）', 'success');
      closeCorpusEditor();
    } else {
      showToast(d.error || '保存失败', 'error');
    }
  } catch (e) {
    showToast('保存失败: ' + (e.message || e), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '保存语料';
  }
}

// ── 归一化规则编辑（num_norm_extra.txt）──
async function openNormRulesEditor() {
  const mask = document.getElementById('normRulesModal');
  const hint = document.getElementById('normRulesPathHint');
  const box = document.getElementById('normRulesContent');
  const builtin = document.getElementById('normRulesBuiltin');
  mask.style.display = 'flex';
  hint.textContent = '加载中...';
  box.value = '';
  if (builtin) builtin.textContent = '加载中...';
  try {
    const r = await fetch('/api/norm-rules');
    const d = await r.json();
    hint.textContent = d.exists
      ? '文件：' + d.path + '（' + (d.rule_count || 0) + ' 条生效，保存即热加载，无需重启）'
      : '规则文件不存在，保存时将新建：' + (d.path || '');
    box.value = d.content || '';
    if (builtin) {
      builtin.textContent = d.builtin || '（内置规则速览暂不可用）';
      const det = document.getElementById('normRulesBuiltinDetails');
      if (det) det.open = false; // 每次打开弹窗默认收起内置速览，避免遮住用户规则编辑区
    }
  } catch (e) {
    hint.textContent = '读取失败: ' + (e.message || e);
  }
}
function closeNormRulesEditor() {
  document.getElementById('normRulesModal').style.display = 'none';
}
async function saveNormRules() {
  const btn = document.querySelector('#normRulesModal .btn-primary');
  const content = document.getElementById('normRulesContent').value;
  btn.disabled = true;
  btn.textContent = '保存中...';
  try {
    const r = await fetch('/api/norm-rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content })
    });
    const d = await r.json();
    if (d.ok) {
      showToast(d.message || '已保存（保存即生效，无需重启）', 'success');
      closeNormRulesEditor();
    } else {
      showToast(d.error || '保存失败', 'error');
    }
  } catch (e) {
    showToast('保存失败: ' + (e.message || e), 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '保存规则';
  }
}

// ── 音色档案管理（REST 标准化：/api/profiles）──
async function openProfileManager() {
  document.getElementById('profileModal').style.display = 'flex';
  await refreshProfileList();
}
function closeProfileManager() {
  document.getElementById('profileModal').style.display = 'none';
}
async function refreshProfileList() {
  const list = document.getElementById('profileList');
  const hint = document.getElementById('profilePathHint');
  list.innerHTML = '<div style="color:var(--muted)">加载中...</div>';
  try {
    const r = await fetch('/api/profiles');
    const d = await r.json();
    hint.textContent = '档案文件：' + (d.file || '');
    const ps = d.profiles || [];
    if (!ps.length) {
      list.innerHTML = '<div style="color:var(--muted);padding:8px 0">暂无音色档案。先在下方命名并点击「保存当前音色」。</div>';
      return;
    }
    list.innerHTML = '';
    ps.forEach(p => {
      const modeLabel = { voice_design: '音色设计', fixed_clone: '固定参考克隆', self_seeding: '自播种' }[p.mode] || p.mode || '音色设计';
      const desc = (p.control_text || '').slice(0, 40) || (p.voice || '');
      const row = document.createElement('div');
      row.style.cssText = 'display:flex;align-items:center;gap:8px;padding:8px 10px;background:var(--surface2);border:1px solid var(--border);border-radius:8px;';
      const info = document.createElement('div');
      info.style.cssText = 'flex:1;min-width:0;';
      info.innerHTML = '<div style="font-weight:600;font-size:13px;">' + esc(p.name) + '</div>' +
        '<div style="font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">[' + modeLabel + '] ' + esc(desc) + '</div>';
      const btnApply = document.createElement('button');
      btnApply.className = 'mode-btn';
      btnApply.textContent = '应用';
      btnApply.onclick = () => applyProfile(p.name);
      const btnDel = document.createElement('button');
      btnDel.className = 'mode-btn';
      btnDel.textContent = '删除';
      btnDel.onclick = () => deleteProfile(p.name);
      row.appendChild(info);
      row.appendChild(btnApply);
      row.appendChild(btnDel);
      list.appendChild(row);
    });
  } catch (e) {
    list.innerHTML = '<div style="color:var(--danger)">读取失败: ' + esc(e.message || e) + '</div>';
  }
}
function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
async function saveProfileFromCurrent() {
  const name = document.getElementById('profileNameInput').value.trim();
  if (!name) { showToast('请输入档案名称', 'error'); return; }
  const payload = {
    name: name,
    voice: selectedVoice || 'default',
    control_text: document.getElementById('controlText').value.trim(),
    mode: currentMode,
    prompt_text: document.getElementById('promptText').value.trim(),
  };
  try {
    const r = await fetch('/api/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (d.ok) {
      showToast(d.message || '已保存', 'success');
      document.getElementById('profileNameInput').value = '';
      await refreshProfileList();
    } else {
      showToast(d.error || '保存失败', 'error');
    }
  } catch (e) {
    showToast('保存失败: ' + (e.message || e), 'error');
  }
}
async function applyProfile(name, skipVoice) {
  try {
    const r = await fetch('/api/profiles');
    const d = await r.json();
    const p = (d.profiles || []).find(x => x.name === name);
    if (!p) { showToast('档案不存在', 'error'); return; }
    // 应用：模式 / 预设 / 音色描述 / 提示文本
    const modeBtn = document.querySelector('.ref-mode-btn[data-mode="' + (p.mode || 'voice_design') + '"]');
    if (modeBtn) setMode(p.mode || 'voice_design', modeBtn);
    if (!skipVoice && p.voice && VOICE_LIST[p.voice]) {
      const voiceBtn = document.querySelector('.voice-btn[data-id="' + p.voice + '"]');
      if (voiceBtn) selectVoice(p.voice, voiceBtn);
      else selectedVoice = p.voice;
    }
    if (p.control_text) document.getElementById('controlText').value = p.control_text;
    document.getElementById('promptText').value = p.prompt_text || '';
    if (p.reference_wav_path) {
      currentRefPath = p.reference_wav_path;
      showToast('已应用档案。参考音频：' + p.reference_wav_path + '（合成时自动使用，或重新上传覆盖）', 'success');
    } else {
      currentRefPath = '';
      showToast('已应用音色档案「' + name + '」', 'success');
    }
    closeProfileManager();
  } catch (e) {
    showToast('应用失败: ' + (e.message || e), 'error');
  }
}

async function saveCurrentAsProfile() {
  if (!lastPreviewPath) { showToast('请先点击「▶ 试听」生成参考音频，再保存档案', 'error'); return; }
  const name = prompt('档案名称（如：温柔女声-固定克隆）:', (document.getElementById('controlText').value.trim() || '未命名') + ' (克隆)');
  if (!name) return;
  const payload = {
    name: name,
    voice: selectedVoice || 'default',
    control_text: document.getElementById('controlText').value.trim(),
    mode: 'fixed_clone',
    reference_wav_path: lastPreviewPath,
    prompt_text: document.getElementById('promptText').value.trim(),
  };
  try {
    const r = await fetch('/api/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const d = await r.json();
    if (d.ok) { showToast('已保存为固定克隆档案：' + name, 'success'); }
    else { showToast(d.error || '保存失败', 'error'); }
  } catch (e) { showToast('保存失败: ' + (e.message || e), 'error'); }
  await renderProfileChips();
}

async function renderProfileChips() {
  const box = document.getElementById('profileChips');
  if (!box) return;
  box.innerHTML = '';
  try {
    const r = await fetch('/api/profiles');
    const d = await r.json();
    const ps = d.profiles || [];
    for (const p of ps) {
      const chip = document.createElement('button');
      chip.dataset.profileName = p.name || '';
      chip.style.cssText = 'padding:2px 8px;font-size:11px;border-radius:10px;border:1px solid var(--border);background:var(--surface2);color:var(--text);cursor:grab;max-width:100px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;user-select:none;';
      chip.textContent = p.name || '未命名';
      chip.title = (p.mode === 'fixed_clone' ? '🔒 ' : '') + (p.control_text || '') + ' (' + (p.mode || 'voice_design') + ') — 拖到左侧预设可锁定音色；拖到其它标签前/后可排序';
      chip.onclick = () => applyProfile(p.name);
      // mousedown 拖拽排序（chip 水平排列）
      chip.addEventListener('mousedown', e => {
        if (e.button !== 0) return;
        const startX = e.clientX, startY = e.clientY;
        let dragging = false;
        const onMove = ev => {
          const dx = ev.clientX - startX, dy = ev.clientY - startY;
          if (!dragging && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
            dragging = true;
            chip.style.zIndex = '10';
            chip.style.boxShadow = '0 2px 8px rgba(0,0,0,0.15)';
          }
          if (dragging) {
            chip.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
            const siblings = Array.from(box.querySelectorAll('button[data-profilename]'));
            const myX = ev.clientX;
            let targetIdx = 0;
            for (let i = 0; i < siblings.length; i++) {
              const r = siblings[i].getBoundingClientRect();
              if (myX > r.left + r.width / 2) targetIdx = i + 1;
            }
            siblings.forEach((s, i) => {
              s.style.outline = '';
              if (i === targetIdx && targetIdx !== siblings.indexOf(chip)) {
                s.style.outline = '2px solid var(--accent)';
              }
            });
            chip._targetIdx = targetIdx;
          }
        };
        const onUp = () => {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          if (dragging) {
            const siblings = Array.from(box.querySelectorAll('button[data-profilename]'));
            const dragIdx = siblings.indexOf(chip);
            const targetIdx = chip._targetIdx !== undefined ? chip._targetIdx : -1;
            chip.style.zIndex = '';
            chip.style.transform = '';
            chip.style.boxShadow = '';
            siblings.forEach(s => { s.style.outline = ''; });
            if (targetIdx >= 0 && targetIdx !== dragIdx) {
              const refEl = siblings[targetIdx];
              if (targetIdx > dragIdx) box.insertBefore(chip, refEl.nextSibling);
              else box.insertBefore(chip, refEl);
            }
            showToast('档案顺序已更新', 'info');
          }
        };
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
      });
      box.appendChild(chip);
    }
  } catch (e) { /* silent */ }
}
async function deleteProfile(name) {
  if (!confirm('确认删除音色档案「' + name + '」？')) return;
  try {
    const r = await fetch('/api/profiles/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name })
    });
    const d = await r.json();
    if (d.ok) { showToast(d.message || '已删除', 'success'); await refreshProfileList(); await renderProfileChips(); }
    else showToast(d.error || '删除失败', 'error');
  } catch (e) {
    showToast('删除失败: ' + (e.message || e), 'error');
  }
}

// ── 语料 / 音色档案：导入导出（REST 标准化）──
function downloadBlob(filename, content, mime) {
  const blob = new Blob([content], { type: mime || 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 800);
}
async function exportCorpusFile() {
  try {
    const r = await fetch('/api/corpus/export', { method: 'POST' });
    const d = await r.json();
    if (!d.ok) { showToast(d.error || '导出失败', 'error'); return; }
    downloadBlob(d.filename || 'corpus_user_override.txt', d.content || '');
    showToast('已导出语料' + (d.file_path ? '（同时保存至 ' + d.file_path + '）' : ''), 'success');
  } catch (e) {
    showToast('导出失败: ' + (e.message || e), 'error');
  }
}
async function importCorpusFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    const content = await file.text();
    const r = await fetch('/api/corpus/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content })
    });
    const d = await r.json();
    if (d.ok) {
      showToast(d.message || '导入完成', 'success');
      if (d.skipped_detail && d.skipped_detail.length) {
        const bad = d.skipped_detail.slice(0, 5).map(x => '  第' + x.line + '行: ' + x.text.slice(0, 60)).join('\n');
        showToast('已跳过 ' + d.skipped_detail.length + ' 条坏行（详见提示）', 'error');
        console.warn('[corpus import] 坏行:\n' + bad);
      }
      // 刷新编辑器内容
      try {
        const g = await fetch('/api/corpus');
        const gd = await g.json();
        document.getElementById('corpusContent').value = gd.content || '';
        document.getElementById('corpusPathHint').textContent = '语料文件：' + (gd.path || '');
      } catch (_) {}
    } else {
      showToast(d.error || '导入失败', 'error');
    }
  } catch (e) {
    showToast('导入失败: ' + (e.message || e), 'error');
  } finally {
    input.value = '';
  }
}
async function exportProfilesFile() {
  try {
    const r = await fetch('/api/profiles/export', { method: 'POST' });
    const d = await r.json();
    if (!d.ok) { showToast(d.error || '导出失败', 'error'); return; }
    downloadBlob(d.filename || 'voxcpm_profiles.json', d.content || '[]', 'application/json;charset=utf-8');
    showToast('已导出音色档案' + (d.file_path ? '（同时保存至 ' + d.file_path + '）' : ''), 'success');
  } catch (e) {
    showToast('导出失败: ' + (e.message || e), 'error');
  }
}
async function importProfilesFile(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  try {
    const content = await file.text();
    const r = await fetch('/api/profiles/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: content })
    });
    const d = await r.json();
    if (d.ok) {
      showToast(d.message || '导入完成', 'success');
      await refreshProfileList();
    } else {
      showToast(d.error || '导入失败', 'error');
    }
  } catch (e) {
    showToast('导入失败: ' + (e.message || e), 'error');
  } finally {
    input.value = '';
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
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
    return HTMLResponse(
      content=HTML_CONTENT.replace("{VERSION}", VERSION),
      media_type="text/html",
      headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )

  @app.get("/VoxCPM_App.ico")
  async def serve_icon():
    base = Path(__file__).resolve().parent
    for cand in [
      base.parent / "VoxCPM_App.ico",
      base.parent.parent / "installer" / "assets" / "VoxCPM_App.ico",
    ]:
      if cand.exists():
        return FileResponse(str(cand))
    raise HTTPException(404, "icon not found")

  @app.get("/api/ping")
  async def ping():
    return {"ok": True}

  @app.get("/api/voices")
  async def list_voices():
    return JSONResponse({"voices": VOICE_PRESETS})

  def _build_lora_status() -> dict:
    """汇总 LoRA 的真实挂载状态，供前端状态面板如实展示（不再谎称「已挂载」）。

    状态机：
      not_configured  未填写 LoRA 路径
      pending         已配置，但模型尚未（重新）加载 → 下次合成/点「加载模型」时挂载
      ok              已加载且 Loaded N>0 个参数 → 真正生效
      failed          resolve 失败（路径错/配置坏）或 Loaded 0（rank/形状不匹配）
    """
    global _lora_load_info, _lora_resolve_error
    if not _lora_weights_path:
      return {
        "configured": False,
        "status": "not_configured",
        "path": "",
        "loaded": None,
        "skipped": None,
        "resolve_error": "",
      }
    if _lora_resolve_error:
      return {
        "configured": True,
        "status": "failed",
        "path": _lora_weights_path,
        "loaded": None,
        "skipped": None,
        "resolve_error": _lora_resolve_error,
      }
    if _lora_load_info is None:
      return {
        "configured": True,
        "status": "pending",
        "path": _lora_weights_path,
        "loaded": None,
        "skipped": None,
        "resolve_error": "",
      }
    loaded, skipped = _lora_load_info
    if loaded and loaded > 0:
      return {
        "configured": True,
        "status": "ok",
        "path": _lora_weights_path,
        "loaded": loaded,
        "skipped": skipped,
        "resolve_error": "",
      }
    return {
      "configured": True,
      "status": "failed",
      "path": _lora_weights_path,
      "loaded": loaded,
      "skipped": skipped,
      "resolve_error": "LoRA 权重已解析但加载了 0 个参数（rank/形状不匹配？）",
    }

  @app.get("/api/paths")
  async def get_paths():
    import torch

    with state_lock:
      state = "loading" if _model_loading else ("ready" if _model_loaded else "idle")
      sr = (
        _cached_model.tts_model.sample_rate
        if (_model_loaded and _cached_model)
        else None
      )
      denoiser = _denoiser_available
    model_dir = resolve_model_dir()
    out_sub = _output_dir / "VoxCPM_Outputs"
    return JSONResponse(
      {
        "app_dir": str(Path(__file__).resolve().parent),
        "model_dir": model_dir,
        "output_dir": str(_output_dir),
        "output_subdir": str(out_sub),
        "lora_weights_path": _lora_weights_path,
        "lora": _build_lora_status(),
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "cuda_available": torch.cuda.is_available(),
        "device": _device_pref
        if _device_pref
        else ("cuda" if torch.cuda.is_available() else "cpu"),
        "sample_rate": sr,
        "model_loaded": _model_loaded,
        "model_state": state,
        "denoiser_available": denoiser,
      }
    )

  @app.get("/api/status")
  async def status():
    with state_lock:
      state = "loading" if _model_loading else ("ready" if _model_loaded else "idle")
      err = _model_error
    with _dl_lock:
      dl = dict(_dl_state)
    return JSONResponse(
      {
        "state": state,
        "error": err,
        "model_present": model_present(),
        "download_available": HAS_DL,
        "download": dl,
        "lora": _build_lora_status(),
        "model_dir": resolve_model_dir(),
        "models_dir_env": os.environ.get("VOXCPM_MODELS_DIR", ""),
      }
    )

  @app.post("/api/download-model")
  async def api_download_model_start():
    global _dl_thread
    with _dl_lock:
      st = _dl_state.get("status")
      if st in ("scanning", "downloading"):
        return JSONResponse({"ok": False, "message": "正在下载中，请稍候。"})
      if model_present():
        # 模型已存在：执行真实校验，返回每个文件的状态，而不是一句空话
        if HAS_DL and _dlmod is not None:
          files, missing, all_ok = verify_model_files()
          problems = len(missing) + sum(1 for x in files if not x["ok"])
          msg = (
            f"模型文件完整 ✓（共 {len(files)} 个，校验通过）"
            if all_ok
            else f"校验发现 {problems} 处异常，建议重新下载模型"
          )
          return JSONResponse(
            {
              "ok": True,
              "verified": True,
              "all_ok": all_ok,
              "message": msg,
              "files": files,
              "missing": missing,
            }
          )
        return JSONResponse({"ok": False, "message": "模型已存在，无需下载。"})
      _dl_state.update(
        {
          "status": "scanning",
          "phase": "scan",
          "file": None,
          "file_index": 0,
          "file_count": 0,
          "downloaded": 0,
          "total": None,
          "percent": None,
          "overall_percent": 0,
          "message": "正在检测模型文件…",
          "started_at": time.time(),
          "finished_at": None,
        }
      )
    t = threading.Thread(target=_dl_run, daemon=True)
    t.start()
    with _dl_lock:
      _dl_thread[0] = t
    return JSONResponse({"ok": True, "message": "已开始下载。"})

  @app.get("/api/download-model/status")
  async def api_download_model_status():
    with _dl_lock:
      return JSONResponse(dict(_dl_state))

  @app.post("/api/download-model/cancel")
  async def api_download_model_cancel():
    with _dl_lock:
      st = _dl_state.get("status")
      if st not in ("scanning", "downloading"):
        return JSONResponse({"ok": False, "message": "当前没有进行中的下载。"})
      _dl_state["status"] = "cancelled"
      _dl_state["message"] = "已取消下载。可重新点击下载，已下载部分将自动续传。"
      _dl_state["finished_at"] = time.time()
    return JSONResponse(
      {"ok": True, "message": "已请求取消；下载线程会在当前文件后停止。"}
    )

  # ── qwen3 时间戳对齐模型（可选，单独下载/删除）─────────
  def _qwen_local_ok() -> bool:
    """本地 6 文件齐且大小匹配 = True（对照官方 sha 表里的字节数）。"""
    m = os.path.join(_QWEN_MODEL_DIR, "model.safetensors")
    if not os.path.exists(m):
      return False
    if _tsq is not None:
      try:
        sizes = _tsq._QWEN_FILE_SIZES
        for f, sz in list(sizes.items())[:-1]:  # 前 5 个小文件只需存在
          if not os.path.exists(os.path.join(_QWEN_MODEL_DIR, f)):
            return False
        return os.path.getsize(m) == sizes["model.safetensors"]
      except Exception:
        pass
    return True

  def _qwen_size_mb() -> float:
    total = 0
    if os.path.isdir(_QWEN_MODEL_DIR):
      for f in os.listdir(_QWEN_MODEL_DIR):
        p = os.path.join(_QWEN_MODEL_DIR, f)
        if os.path.isfile(p):
          total += os.path.getsize(p)
    return round(total / 1048576, 1)

  @app.get("/api/qwen-model/status")
  async def api_qwen_model_status():
    with _qwen_lock:
      snap = dict(_qwen_state)
    exists = _qwen_local_ok()
    # 清理 hf cache（仅当本地目录已删）
    cache_dir = os.path.join(_APP_ROOT, "models", "qwen3_aligner_hf_cache")
    return JSONResponse(
      {
        "exists": exists,
        "size_mb": 0 if not exists else _qwen_size_mb(),
        "model_dir": _QWEN_MODEL_DIR,
        "supported": HAS_TS,
        "dl": snap,
      }
    )

  @app.post("/api/qwen-model/download")
  async def api_qwen_model_download():
    global _qwen_thread
    if not HAS_TS:
      return JSONResponse({"ok": False, "message": "qwen3 时间戳模块不可用（导入失败），无法下载。"})
    with _qwen_lock:
      if _qwen_state.get("status") in ("downloading", "scanning"):
        return JSONResponse({"ok": False, "message": "正在下载中，请稍候。"})
      if _qwen_local_ok():
        return JSONResponse({"ok": True, "message": f"模型已存在（{_qwen_size_mb()/1024:.2f}GB），无需下载。"})
      _qwen_state.update(
        {
          "status": "downloading",
          "file": None,
          "percent": None,
          "message": "准备下载…",
          "started_at": time.time(),
          "finished_at": None,
        }
      )

    def _qwen_progress(p):
      with _qwen_lock:
        _qwen_state.update({k: v for k, v in p.items() if k in ("percent", "message", "file", "phase")})

    def _qwen_run():
      try:
        ok = _tsq.ensure_model(_QWEN_MODEL_DIR, allow_download=True, progress_cb=_qwen_progress)
        with _qwen_lock:
          if ok:
            _qwen_state.update({"status": "done", "percent": 100, "message": "下载完成，--timestamps 高精度模式可用。", "finished_at": time.time()})
          else:
            _qwen_state.update({"status": "error", "message": "下载失败（无网络或源不可达），可稍后重试。", "finished_at": time.time()})
      except Exception as e:
        with _qwen_lock:
          _qwen_state.update({"status": "error", "message": f"下载异常: {e}", "finished_at": time.time()})

    t = threading.Thread(target=_qwen_run, daemon=True)
    t.start()
    with _qwen_lock:
      _qwen_thread[0] = t
    return JSONResponse({"ok": True, "message": "已开始下载（8 路并行，约 1.75GB）。"})

  @app.post("/api/qwen-model/delete")
  async def api_qwen_model_delete():
    with _qwen_lock:
      if _qwen_state.get("status") in ("downloading", "scanning"):
        return JSONResponse({"ok": False, "message": "正在下载中，无法删除。"})
    freed_mb = _qwen_size_mb()
    removed = []
    for d in (_QWEN_MODEL_DIR, os.path.join(_APP_ROOT, "models", "qwen3_aligner_hf_cache")):
      if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
        removed.append(d)
    if removed:
      return JSONResponse({"ok": True, "message": f"已删除 qwen3 时间戳模型（释放 {freed_mb/1024:.2f}GB）。"})
    return JSONResponse({"ok": True, "message": "本地无 qwen3 模型，无需删除。"})

  @app.post("/api/load_model")
  async def load_model_endpoint():
    with state_lock:
      if _model_loading:
        return JSONResponse({"ok": False, "error": "模型正在加载中，请稍候"})
      if _model_loaded and _cached_model is not None:
        return JSONResponse({"ok": True, "message": "模型已加载"})
    threading.Thread(
      target=lambda: _load_model_background(force=True), daemon=True
    ).start()
    return JSONResponse({"ok": True, "message": "模型加载任务已启动"})

  @app.post("/api/unload_model")
  async def unload_model_endpoint():
    unload_model()
    return JSONResponse({"ok": True, "message": "模型已卸载"})

  @app.get("/api/console_status")
  async def console_status():
    return JSONResponse(
      {
        "visible": _console_effectively_visible(),
        "supported": sys.platform == "win32",
        "has_console": bool(_get_console_hwnd()),  # 是否有原生控制台（无则用 tail 窗口）
      }
    )

  @app.post("/api/toggle_console")
  async def toggle_console():
    ok = _toggle_console()
    visible = _console_effectively_visible()
    return JSONResponse(
      {
        "ok": ok,
        "visible": visible,
        "message": "命令行窗口已显示" if visible else "命令行窗口已收起",
      }
    )

  @app.get("/api/status/{job_id}")
  async def job_status(job_id: str):
    with task_lock:
      result = task_results.get(
        job_id, {"status": "not_found", "message": "任务不存在"}
      )
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
      display = min(89, max(_safe_int(display), last_display + 1))
      result["display_progress"] = display
      result["elapsed_seconds"] = elapsed
      result["remaining_seconds"] = max(0, estimated - elapsed)
    return JSONResponse(result)

  @app.post("/api/set_config")
  async def set_config(req: Request):
    global _output_dir, _lora_weights_path, _QWEN_MODEL_DIR
    try:
      data = await req.json()
    except Exception:
      data = {}
    model_dir = (data.get("model_dir") or "").strip()
    output_dir = (data.get("output_dir") or "").strip()
    lora_weights_path = (data.get("lora_weights_path") or "").strip()
    ts_model_dir = (data.get("ts_model_dir") or "").strip()
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
      os.environ["VOXCPM_MODELS_DIR"] = resolved
      # 触发下次合成重载模型
      with state_lock:
        _model_loaded = False
        _cached_model = None
        _model_loading = False
    if lora_weights_path != _lora_weights_path:
      _lora_weights_path = lora_weights_path
      # LoRA 权重变化必然需要重载模型（挂载/卸载 LoRA 都改模型结构）
      with state_lock:
        _model_loaded = False
        _cached_model = None
        _model_loading = False
    if ts_model_dir != _QWEN_MODEL_DIR:
      # qwen3 时间戳模型目录变更（设置页「浏览」或留空回退默认）
      if ts_model_dir:
        _QWEN_MODEL_DIR = ts_model_dir
        os.environ["VOXCPM_TS_MODEL_DIR"] = ts_model_dir
      else:
        _QWEN_MODEL_DIR = _QWEN_MODEL_DIR_DEFAULT
        os.environ.pop("VOXCPM_TS_MODEL_DIR", None)
      with _qwen_lock:
        _qwen_state.update({"status": "idle", "file": None, "percent": None, "message": "", "started_at": None, "finished_at": None})
    _save_config()
    return JSONResponse(
      {
        "ok": True,
        "model_dir": resolve_model_dir(),
        "output_dir": str(_output_dir),
        "lora_weights_path": _lora_weights_path,
        "ts_model_dir": _QWEN_MODEL_DIR,
      }
    )

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

  def _win_select_folder(title: str = "选择文件夹") -> str | None:
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
    User32 = ctypes.windll.user32
    Ole32.CoInitialize(None)
    try:
      bi = BROWSEINFO()
      display_name = ctypes.create_unicode_buffer(260)
      # 以当前前台窗口作为父窗口，避免文件夹选择对话框被全屏浏览器压在底部
      owner = User32.GetForegroundWindow()
      bi.hwndOwner = owner
      bi.pidlRoot = None
      bi.pszDisplayName = ctypes.cast(ctypes.addressof(display_name), wintypes.LPWSTR)
      bi.lpszTitle = title
      bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE
      bi.lpfn = None
      bi.lParam = 0
      bi.iImage = 0
      # 弹出前强制父窗口置前，进一步保证选择框位于最顶层
      if owner:
        User32.SetForegroundWindow(owner)
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
    return JSONResponse(
      {"ok": bool(path), "path": path, "error": None if path else "未选择目录"}
    )

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
    phoneme_mode: str = Form("false"),
    denoise: str = Form("false"),
    target_sr: str = Form("native"),
    prompt_text: str = Form(""),
    reference_path: str = Form(""),
    reference_wav: UploadFile = File(None),
    seed: str = Form(""),
  ):
    if not text.strip():
      raise HTTPException(400, "文本不能为空")

    # 保存上传的参考音频；若提供了服务器端已有音频路径（reference_path，
    # 与 CLI --reference 对齐）则优先使用，避免重复上传
    ref_wav_path = None
    if mode == "fixed_clone":
      if reference_path and reference_path.strip():
        _rp = reference_path.strip()
        # 相对路径（纯文件名）解析到 TEMP_DIR
        if not os.path.isabs(_rp):
          _rp = str(TEMP_DIR / _rp)
        ref_wav_path = _rp
        if not os.path.isfile(ref_wav_path):
          raise HTTPException(400, f"参考音频路径不存在: {ref_wav_path}")
      elif reference_wav:
        suffix = Path(reference_wav.filename or "").suffix or ".wav"
        ref_wav_path = str(TEMP_DIR / f"ref_{uuid.uuid4().hex[:8]}{suffix}")
        # 同上：写上传参考音频前确保 TEMP_DIR 仍存在
        TEMP_DIR.mkdir(parents=True, exist_ok=True)
        try:
          with open(ref_wav_path, "wb") as f:
            shutil.copyfileobj(reference_wav.file, f)
        except OSError as _e_save:
          raise HTTPException(500, f"参考音频保存失败: {_e_save}") from _e_save

    job_id = submit_task(
      {
        "text": text,
        "voice": voice,
        "control_text": control_text,
        "mode": mode,
        "cfg": cfg,
        "steps": steps,
        "crossfade": crossfade,
        "chunk_size": chunk_size,
        "normalize": normalize,
        "phoneme_mode": phoneme_mode,
        "denoise": denoise,
        "target_sr": target_sr,
        "prompt_text": prompt_text,
        "reference_wav": ref_wav_path,
        "seed": seed,
      }
    )
    return JSONResponse({"job_id": job_id, "status": "queued"})

  # ── 音色试听：以当前音色设置生成短样本（音频 + 波形峰值），与主任务同一音色口径 ──
  _VOICE_PREVIEW_TEXT = "你好，这是一段当前音色的试听，希望你喜欢。"

  @app.post("/api/voice-preview")
  async def voice_preview(
    voice: str = Form("default"),
    control_text: str = Form(""),
    mode: str = Form("voice_design"),
    cfg: float = Form(2.5),
    steps: int = Form(15),
    denoise: str = Form("false"),
    reference_path: str = Form(""),
    reference_wav: UploadFile = File(None),
  ):
    """音色试听：用与主任务完全一致的音色解析（预设/描述/参考音频）合成一段固定短文本，
    落盘 TEMP_DIR（经 /api/audio/<文件名> 取回），返回 64-bin 波形峰值供前端画简波形。
    参考音频口径与 /api/tts 相同：优先服务器端路径，其次上传文件。
    模型未加载时返回 ok=False + 明确错误（nomodel 版需先下载模型）。"""
    use_denoise = denoise in ("true", "1", "yes")

    def _gen():
      model = load_model()
      if model is None:
        raise RuntimeError(
          f"模型未加载：{_model_error or '请先点击右上角「加载模型」'}"
        )
      if not HAS_SF:
        raise RuntimeError("缺少 soundfile 依赖，无法写出音频")
      control = (control_text or "").strip() or VOICE_PRESETS.get(
        voice, VOICE_PRESETS["default"]
      )
      ref_path = None
      if mode == "fixed_clone":
        if reference_path and reference_path.strip():
          _rp = reference_path.strip()
          if not os.path.isabs(_rp):
            _rp = str(TEMP_DIR / _rp)
          ref_path = _rp
          if not os.path.isfile(ref_path):
            raise RuntimeError(f"参考音频路径不存在: {ref_path}")
        elif reference_wav is not None:
          suffix = Path(reference_wav.filename or "").suffix or ".wav"
          ref_path = str(TEMP_DIR / f"ref_{uuid.uuid4().hex[:8]}{suffix}")
          TEMP_DIR.mkdir(parents=True, exist_ok=True)
          try:
            with open(ref_path, "wb") as f:
              shutil.copyfileobj(reference_wav.file, f)
          except OSError as _e_save:
            raise RuntimeError(f"参考音频保存失败: {_e_save}") from _e_save
      if ref_path:
        wav = model.generate(
          text=_VOICE_PREVIEW_TEXT,
          cfg_value=cfg,
          inference_timesteps=steps,
          reference_wav_path=ref_path,
          normalize=True,
          denoise=use_denoise,
        )
      else:
        chunk_text = (
          f"({control}){_VOICE_PREVIEW_TEXT}" if control else _VOICE_PREVIEW_TEXT
        )
        wav = model.generate(
          text=chunk_text,
          cfg_value=cfg,
          inference_timesteps=steps,
          normalize=True,
          denoise=use_denoise,
        )
      sr = model.tts_model.sample_rate
      arr = np.asarray(wav, dtype=np.float32).ravel()
      TEMP_DIR.mkdir(parents=True, exist_ok=True)
      fname = f"voice_preview_{_safe_int(time.time())}.wav"
      sf.write(str(TEMP_DIR / fname), arr, sr)
      # 64-bin 波形峰值（逐 bin 取绝对值最大，再按全局最大归一化）
      n = len(arr)
      peaks = []
      for b in range(64):
        seg = arr[(b * n) // 64 : ((b + 1) * n) // 64] if n else np.zeros(0)
        peaks.append(_safe_float(np.max(np.abs(seg))) if len(seg) else 0.0)
      m = max(peaks) or 1.0
      return (
        fname,
        [round(p / m, 4) for p in peaks],
        round(n / _safe_float(sr, 24000.0), 2),
      )

    loop = asyncio.get_running_loop()
    try:
      fname, peaks, dur = await loop.run_in_executor(executor, _gen)
    except Exception as e:
      return JSONResponse({"ok": False, "error": str(e)})
    return JSONResponse(
      {
        "ok": True,
        "wav_url": f"/api/audio/{fname}",
        "file_path": str(TEMP_DIR / fname),
        "filename": fname,
        "duration": dur,
        "peaks": peaks,
        "message": f"试听已生成（约 {dur} 秒）",
      }
    )

  @app.post("/api/g2p")
  async def g2p_convert(payload: dict):
    """G2P 转换：汉字文本 -> VoxCPM2 音素串（{ni3}{hao3}）。需已下载 G2PW 离线模型。"""
    text = (payload or {}).get("text", "")
    if not text or not text.strip():
      raise HTTPException(400, "文本不能为空")
    try:
      import g2p_phoneme

      phonemes = g2p_phoneme.text_to_phonemes(text.strip())
      return JSONResponse({"phonemes": phonemes})
    except FileNotFoundError as e:
      raise HTTPException(503, str(e)) from e
    except Exception as e:
      raise HTTPException(500, f"G2P 转换失败: {e}") from e

  @app.get("/api/corpus")
  async def get_corpus():
    """读取用户多音字语料文件内容（只读，不修改）；统一走 voxcpm_api 公共后端。"""
    return JSONResponse(voxcpm_api.read_corpus())

  @app.post("/api/corpus")
  async def save_corpus(payload: dict):
    """写回用户多音字语料文件（UTF-8）。保存即生效：g2p_phoneme 检测到 mtime 变化自动热加载，无需重启。"""
    content = (payload or {}).get("content", "")
    return JSONResponse(voxcpm_api.write_corpus(content))

  # ── 数字归一化规则（num_norm_extra.txt）：保存即热加载，无需重启 ──
  @app.get("/api/norm-rules")
  async def get_norm_rules():
    """读取用户归一化规则文件内容（只读，不修改）；统一走 voxcpm_api 公共后端。"""
    return JSONResponse(voxcpm_api.read_norm_rules())

  @app.post("/api/norm-rules")
  async def save_norm_rules(payload: dict):
    """写回用户归一化规则文件（UTF-8）。保存即生效：text_norm_cn 按 mtime 自动热加载，无需重启；
    坏行由解析器单条跳过 + 警告，不会崩合成任务。"""
    content = (payload or {}).get("content", "")
    return JSONResponse(voxcpm_api.write_norm_rules(content))

  # ── 音色档案（profile）：列表 / 新增 / 删除（REST 标准化）──
  @app.get("/api/profiles")
  async def list_profiles():
    """音色档案列表（用户保存的音色配置：voice/control_text/mode/参考音频等）。"""
    return JSONResponse(
      {"profiles": voxcpm_api.list_profiles(), "file": str(voxcpm_api.PROFILE_FILE)}
    )

  @app.post("/api/profiles")
  async def add_profile(payload: dict):
    """新增/覆盖音色档案。入参：{name, voice?, control_text?, mode?, reference_wav_path?, prompt_text?}"""
    payload = payload or {}
    name = payload.get("name", "")
    if not HAS_API or not name.strip():
      raise HTTPException(400, "档案名称不能为空")
    # 绝对路径转相对（只存文件名，避免跨机器路径失效）
    rwp = payload.get("reference_wav_path", "")
    if rwp and os.path.isabs(rwp):
      _td = str(TEMP_DIR) + os.sep
      if rwp.startswith(_td):
        payload["reference_wav_path"] = os.path.basename(rwp)
    return JSONResponse(voxcpm_api.save_profile(name, payload))

  @app.post("/api/profiles/delete")
  async def delete_profile(payload: dict):
    """删除音色档案。入参：{name}"""
    payload = payload or {}
    name = payload.get("name", "")
    if not name.strip():
      raise HTTPException(400, "档案名称不能为空")
    return JSONResponse(voxcpm_api.delete_profile(name))

  # ── 语料 / 音色档案：导入导出（REST 标准化）──
  @app.post("/api/corpus/import")
  async def import_corpus(payload: dict):
    """导入语料文本：逐行校验（与 g2p_phoneme 同口径），坏行跳过并统计；
    有效行合并写入现有语料（保留原内容，last-wins 语义一致）。入参：{content}"""
    content = (payload or {}).get("content", "")
    return JSONResponse(voxcpm_api.import_corpus_text(content))

  @app.post("/api/corpus/export")
  async def export_corpus():
    """导出当前语料内容（UTF-8 文本）；同时落盘 exports/ 便于 CLI/分享。"""
    r = voxcpm_api.export_corpus_text()
    f = voxcpm_api.export_corpus_to_file()
    r["file_path"] = f.get("path")
    return JSONResponse(r)

  @app.post("/api/profiles/import")
  async def import_profiles(payload: dict):
    """导入音色档案（JSON 数组文本）：坏项跳过，合法项合并写入（同名覆盖）。入参：{content}"""
    content = (payload or {}).get("content", "")
    return JSONResponse(voxcpm_api.import_profiles_text(content))

  @app.post("/api/profiles/export")
  async def export_profiles():
    """导出全部音色档案为 JSON 文本；同时落盘 exports/ 便于 CLI/分享。"""
    r = voxcpm_api.export_profiles_text()
    f = voxcpm_api.export_profiles_to_file()
    r["file_path"] = f.get("path")
    return JSONResponse(r)

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
      headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"},
    )


def run_server(port: int = 18978, host: str = "127.0.0.1", hide_console: bool = True):
  if not HAS_WEB:
    print("[错误] 缺少依赖: uvicorn, fastapi, starlette")
    print("请运行: pip install uvicorn fastapi")
    return

  # 无模型版安装包：模型需用户自行下载。给出明确指引并保持命令行窗口可见，避免静默失败。
  if not model_present():
    print("\n" + "=" * 50)
    print("[重要] " + _model_missing_detail())
    print("=" * 50 + "\n")
    hide_console = False

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
  print(f"\n{'=' * 50}")
  print("  VoxCPM2 Web UI 已启动")
  print(f"  访问地址: {url}")
  print(f"  模型目录: {resolve_model_dir()}")
  print(
    f"    (VOXCPM_MODELS_DIR={os.environ.get('VOXCPM_MODELS_DIR', '') or '(未设置)'}, "
    f"VOXCPM_MODEL_DIR={os.environ.get('VOXCPM_MODEL_DIR', '') or '(未设置)'})"
  )
  print("  按 Ctrl+C 停止服务器")
  print(f"{'=' * 50}\n")

  # 自动打开浏览器
  def open_browser():
    time.sleep(1.5)
    try:
      webbrowser.open(url)
    except Exception as e:
      print(f"[提示] 自动打开浏览器失败（{e}），请手动访问: {url}", file=sys.stderr)

  threading.Thread(target=open_browser, daemon=True).start()

  # 启动后自动隐藏命令行窗口（Windows），网页按钮可随时重新显示
  # 仅当服务器确实启动成功后才隐藏，避免失败时被静默吞掉
  started = {"ok": False}
  try:
    _add_event_handler = getattr(app, "add_event_handler", None)
    if _add_event_handler is None:
      raise AttributeError("add_event_handler 不可用")
    _add_event_handler("startup", lambda: started.__setitem__("ok", True))
  except Exception as e:
    # 注册失败 → started.ok 保持 False → 不隐藏控制台（安全默认），仅记一条日志
    print(f"[提示] 注册 startup 事件失败，窗口将不自动隐藏（{e}）", file=sys.stderr)
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
    print("  python vox_web_ui.py --port 8010")
    input("按回车键退出...")


# ══════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
  parser = argparse.ArgumentParser(description="VoxCPM2 Web UI — 本地语音合成")
  parser.add_argument("--port", type=int, default=18978, help="HTTP 端口 (默认 18978)")
  parser.add_argument(
    "--host", type=str, default="127.0.0.1", help="监听地址 (默认 127.0.0.1)"
  )
  parser.add_argument("--model-dir", type=str, default="", help="本地模型目录")
  parser.add_argument("--output-dir", type=str, default="", help="输出目录")
  parser.add_argument(
    "--show-console", action="store_true", help="保留命令行窗口显示（调试用）"
  )
  args = parser.parse_args()

  if args.model_dir:
    os.environ["VOXCPM_MODEL_DIR"] = args.model_dir
    os.environ["VOXCPM_MODELS_DIR"] = args.model_dir
  if args.output_dir:
    os.environ["VOXCPM_OUTPUT_DIR"] = args.output_dir

  run_server(port=args.port, host=args.host, hide_console=not args.show_console)
