# -*- mode: python ; coding: utf-8 -*-
# =============================================================================
# VoxCPM2 TTS —— PyInstaller 打包模板（从零起步 / 备用）
# =============================================================================
# 用途：
#   把 VoxCPM2 的入口（Web UI 或 CLI）冻结成可分发 exe，
#   让最终用户无需自带 python_cuda 也能双击运行。
#
# ⚠️ 重要前提与权衡（务必先读）：
#   1. 本模板必须在【完整 Python + 全部依赖】的环境中运行
#      （即 pip install -r requirements.txt 的普通 venv），
#      不能用随包 python_cuda 嵌入版（缺编译头，PyInstaller 无法分析）。
#   2. torch + CUDA 体积巨大（数 GB）。强烈建议用 --onedir（默认）：
#      生成 dist/VoxCPM2_TTS/ 目录，含 exe + 依赖。
#      --onefile 会把所有内容塞进单 exe，运行时解包到 %TEMP%，
#      启动慢、占临时盘、杀软易误报，一般不推荐。
#   3. 当前发行版默认采用「.py + python_cuda 直启」方案（见 README），
#      此 .spec 仅作为「想要单目录 / 单文件 exe」时的备选路线。
#   4. 离线降噪模型（app/models/zipenhancer）必须作为 datas 打入，
#      否则降噪降级为空操作（保持离线安全，但不报错）。
#   5. 模型权重（app/model，数 GB）默认【不】打入 exe；
#      用户首次运行仍需联网或自行放置权重。若要完全离线便携，
#      取消下方 MODEL_DIR 那段 datas 注释（体积会非常大）。
#
# 使用：
#   pyinstaller VoxCPM_TTS.spec                 # -> dist/VoxCPM2_TTS/
#   pyinstaller VoxCPM_TTS.spec --onefile       # 单文件（不推荐）
#   pyinstaller VoxCPM_TTS.spec --clean         # 清缓存重建
# =============================================================================

import os

# SPECPATH 由 PyInstaller 注入，为本 .spec 所在目录（build/）。
# 据此定位 app/ 与模型资源。
SPECPATH = os.path.dirname(os.path.abspath(SPEC)) if 'SPEC' in globals() else os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.abspath(os.path.join(SPECPATH, "..", "app"))
SCRIPTS_DIR = os.path.join(APP_DIR, "Scripts")
MODEL_DIR = os.path.join(APP_DIR, "model")
ZIPENHANCER_DIR = os.path.join(APP_DIR, "models", "zipenhancer")
ICON_SRC = os.path.join(APP_DIR, "VoxCPM_App.ico")

# ── 选择入口 ─────────────────────────────────────────────
# 默认冻结 Web UI（最像“App”，双击即起本地服务并开浏览器）。
ENTRY = os.path.join(SCRIPTS_DIR, "vox_web_ui.py")
# 若想冻结命令行长文本合成入口，改为下面这行：
# ENTRY = os.path.join(SCRIPTS_DIR, "voxcpm_tts_v5_longtext.py")

block_cipher = None

# ── 数据文件（必须打入，否则离线功能缺失）────────────────
added_datas = []
if os.path.isdir(ZIPENHANCER_DIR):
    # 保持相对路径 models/zipenhancer，便于 _resolve_zipenhancer_dir() 找到
    added_datas.append((ZIPENHANCER_DIR, "models/zipenhancer"))
# 如需把模型权重也打进 exe（体积巨大，谨慎），取消下一行：
# if os.path.isdir(MODEL_DIR):
#     added_datas.append((MODEL_DIR, "model"))

# ── 隐藏导入（torch / modelscope 大量动态导入）───────────
hiddenimports = [
    "voxcpm",
    "voxcpm.zipenhancer",
    "modelscope",
    "modelscope.pipelines",
    "modelscope.models",
    "modelscope.trainers",
    "addict",
    "soundfile",
    "librosa",
    "fastapi",
    "uvicorn",
    "uvicorn.logging",
    "starlette",
    "numpy",
    "scipy",
    "PIL",
    "simplejson",
    "sortedcontainers",
    "torch",
    "torchaudio",
]

a = Analysis(
    [ENTRY],
    pathex=[SCRIPTS_DIR, APP_DIR],
    binaries=[],
    datas=added_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "test",
        "tests",
        "torch.testing",
        "torch.utils.cpp_extension",
        "matplotlib",   # 纯推理不需要绘图，可排除
        "IPython",
        "pydoc",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VoxCPM2_TTS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,   # Web UI 建议 True 以查看启动/模型加载日志；纯后台可改 False
    icon=(ICON_SRC if os.path.exists(ICON_SRC) else None),
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="VoxCPM2_TTS",
)
