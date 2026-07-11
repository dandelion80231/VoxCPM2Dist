# VoxCPM2 TTS 中文版 v5.0 — 自包含分发包

> **一句话**：开箱即用的 VoxCPM2 语音合成工具，内置完整 Python 环境与模型权重，**已随包内置离线真实降噪（ZipEnhancer）**，有 NVIDIA 显卡自动走 CUDA，无显卡自动 CPU 回退，零预装。

## 特性一览

| 特性 | 说明 |
|------|------|
| 🎙️ 音色统一 | 长文本分段配音 + 交叉淡入淡出拼接，消除段间断裂感 |
| 🔁 三种音色模式 | 固定参考音频（最稳）/ 自播种（自动生成参考）/ 逐段 Voice Design |
| 🖥️ 零依赖安装 | 内置 Python 3.12 + PyTorch 2.12.1+cu126，无需预装任何环境 |
| 📡 离线可用 | 模型权重 + 降噪模型均打包在内，`VOXCPM_MODEL_DIR` 指向本地，完全离线 |
| 🌐 国内镜像 | 默认走 hf-mirror.com，无需科学上网 |
| 🎛️ 10 种预设音色 | 温柔女声、沉稳男声、播音腔、磁性男声等，一键调用 |
| 🗣️ 三种克隆 | Controllable Clone / Ultimate Clone / Self-Seeding |
| 📝 文本规范化 | 内置 wetext 中文文本预处理（数字/繁体→简体/读音规范化） |
| 💻 PowerShell GUI | 图形化菜单，无需记命令行参数 |
| 🔇 离线降噪 | 随包内置 ZipEnhancer 降噪模型，合成时可勾选「降噪」去除录音噪声，纯本地不联网 |

## 快速开始

### 安装

1. 下载 `VoxCPM2_TTS_v5.0_Setup.exe` 及全部 `.bin` 分卷文件（**必须放在一起**）
2. 双击运行安装程序，选择安装目录（默认 `C:\Program Files\VoxCPM2 TTS`）
3. 安装完成后桌面会出现两个快捷方式：**交互菜单**（CLI 图形菜单）与**网页界面**（浏览器 UI）

### 使用

双击桌面上的 **VoxCPM2 TTS 中文版 - 交互菜单** 图标，进入交互式菜单：

```
+==========================================+
|                                          |
|   VoxCPM2 语音合成工具 v5.0 音色统一版   |
|                                          |
|   长文本配音 / 音色一致 / 交叉淡入淡出   |
|                                          |
+==========================================+

[快速命令]
  1  - 温柔女声（默认）
  2  - 活泼女声
  3  - 沉稳男声
  4  - 磁性男声
  5  - 自定义音色
  6  - 交互模式
  7  - 列出音色预设
  8  - 查看配置

[长文本配音]
  9  - 长文本文件配音（方式2：固定参考音频，最稳定）
  10 - 长文本文件配音（方式1：自播种，自动生成参考）
  11 - 长文本文件配音（方式3：逐段音色设计）
  12 - 生成参考音频（用于长文本统一音色）

[其他]
  13 - 克隆已有音频（Controllable Clone）
  14 - 终极克隆（Ultimate Clone）
  0  - 退出

[直接输入] 输入任意文本直接合成（默认温柔女声）
[高级用法] 输入完整 Python 参数（如: -f 文件.txt --reference ref.wav）
```

双击桌面上的 **VoxCPM2 TTS 中文版 - 网页界面** 图标，浏览器会自动打开 http://127.0.0.1:18978 的图形化界面。

### 命令行用法

> 安装包不再提供冻结的 `.exe`，改为用随包 `python_cuda` 直接运行 `.py` 引擎。双击桌面「交互菜单」图标即等价于下面的命令；「网页界面」图标等价于启动 Web UI。

```bash
# 进入安装目录后，用随包 Python 运行引擎
set PY=python_cuda\python.exe
set ENG=voxcpm_tts_v5_longtext.py

# 直接合成（默认温柔女声）
%PY% %ENG% "你好，欢迎使用语音合成系统。"

# 指定音色
%PY% %ENG% -t "今天天气真好" --voice calm_male

# 长文本文件配音（固定参考音频模式）
%PY% %ENG% -f article.txt -c "温柔女声" --split auto --chunk-size 180

# 终极克隆
%PY% %ENG% -t "合成文本" --prompt-audio ref.wav --prompt-text "参考音频原文" --reference ref.wav

# 列出所有音色预设
%PY% %ENG% --list-voices

# 查看当前配置
%PY% %ENG% --show-config
```

### 网页界面（Web UI）

启动器为 `app\start_web_ui.bat`，内部调用 `python_cuda\python.exe Scripts\vox_web_ui.py --port 8000 --host 127.0.0.1`，
随后自动打开浏览器 http://127.0.0.1:18978 。Web UI 支持麦克风录制参考音频、自定义音色描述、可编辑模型/输出目录，并会在路径面板显示「降噪模型：已内置（离线可用）」。

## 环境规格

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12.10 (embed) | 自包含便携版 |
| PyTorch | 2.12.1+cu126 | CUDA 12.6，无显卡自动 CPU 回退 |
| torchaudio | 2.11.0 (CPU) | 与 PyTorch 配合 |
| voxcpm | 2.0.3 | 语音合成引擎 |
| wetext | 0.1.4 | 中文文本规范化 |
| transformers | 5.13.0 | HuggingFace 模型加载 |
| ZipEnhancer (modelscope) | iic/speech_zipenhancer_ans_multiloss_16k_base | 离线降噪模型（随包内置） |
| InnoSetup | 6.7.1 | 安装包编译器 |

### 硬件要求

| 项目 | 最低 | 推荐 |
|------|------|------|
| GPU | 无（CPU 可运行） | NVIDIA RTX 3060 及以上 |
| 显存 | — | ≥8 GB |
| 磁盘空间 | 10 GB | 15 GB |
| 内存 | 8 GB | 16 GB+ |

### 模型文件

主模型权重约 4.6 GB，存放于安装目录 `model\openbmb\VoxCPM2\`：

| 文件 | 大小 | 说明 |
|------|------|------|
| `model.safetensors` | 4.37 GB | 主模型权重 |
| `audiovae.pth` | 360 MB | 音频 VAE 编码器 |
| `tokenizer.json` | 3.5 MB | 分词器 |
| `config.json` | — | 模型配置 |
| `tokenizer_config.json` | — | 分词器配置 |
| `special_tokens_map.json` | — | 特殊 token 映射 |
| `tokenization_voxcpm2.py` | — | 分词器代码 |

**离线降噪模型（ZipEnhancer）** 约 18 MB，存放于 `models\zipenhancer\`（`pytorch_model.bin` + `onnx_model.onnx` + `configuration.json` 等）。
合成时勾选「降噪」即调用它，纯本地、不联网；其运行依赖 `addict` / `Pillow` / `simplejson` / `sortedcontainers`（已写入 `requirements.txt` 并随包安装）。

## 项目结构

```
VoxCPM2Dist/
├── app/                          # 安装内容（打包进 Setup.exe）
│   ├── python_cuda/              # 嵌入式 Python 3.12 + 全部依赖（含降噪依赖）
│   │   ├── python.exe
│   │   └── Lib/site-packages/    # pip 安装的依赖包
│   ├── model/openbmb/VoxCPM2/   # 主模型权重
│   ├── models/zipenhancer/      # 离线降噪模型（ZipEnhancer，随包内置）
│   ├── VoxCPM_App.ico            # 应用图标（网页 /GET/VoxCPM_App.ico 用之）
│   ├── start_web_ui.bat          # 拉起网页界面（双击入口，端口 8000）
│   └── Scripts/                 # 核心引擎 + 启动器
│       ├── voxcpm_tts_v5_longtext.py # Python 引擎（TTS 核心，CLI 入口）
│       ├── vox_web_ui.py         # 网页界面引擎（Web UI 入口）
│       ├── text_norm_cn.py       # 中文数字归一化（网页端与 CLI 共享）
│       ├── voxcpm_web_config.json # 网页 UI 持久化配置
│       ├── VoxCPM_TTS_v5_CN.ps1  # v5 交互菜单（PowerShell）
│       └── Launch_TTS_Menu.bat   # 双击拉起 v5 菜单（包装 .ps1）
├── installer/
│   ├── VoxCPM2_TTS.iss          # InnoSetup 安装脚本
│   └── ChineseSimplified.isl    # 中文语言文件
├── build_installer.ps1          # 构建：7z 预压缩 app/ 为 payload/app.7z 后调用 ISCC
└── output/                       # 构建产物（安装包，已生成可分发）
│   ├── VoxCPM2_TTS_v5.0_Setup.exe    # 安装包引导
│   ├── VoxCPM2_TTS_v5.0_Setup-1.bin  # 分卷 1
│   ├── VoxCPM2_TTS_v5.0_Setup-2.bin  # 分卷 2
│   ├── VoxCPM2_TTS_v5.0_Setup-3.bin  # 分卷 3
│   └── VoxCPM2_TTS_v5.0_Setup-4.bin  # 分卷 4
```

## 构建说明

### 前置工具

- **InnoSetup 6.7.1**：[innosetup.com](https://www.innosetup.com/)
- **Python 3.12.10 embed**：[python.org/downloads/windows/](https://www.python.org/downloads/windows/)（embed zip）
- **PowerShell 5.1+**：Windows 自带
- **7-Zip / NanaZip**：用于 `app.7z` 预压缩（`build_installer.ps1` 会自动探测或下载）

### 构建流程

```bash
# 1. 解压 Python embed
7z x <Python-3.12.10-embed-amd64.zip> -oapp\python_cuda   # 注：原 staging/ 已清理，请自行从 python.org 下载 embed zip
# 编辑 app\python_cuda\python312._pth，添加一行：import site

# 2. 安装 PyTorch CUDA 版
app\python_cuda\python.exe -m pip install \
  torch-2.12.1+cu126-cp312-cp312-win_amd64.whl --no-deps \
  --index-url https://download.pytorch.org/whl/cu126

# 3. 安装其余依赖（含离线降噪所需的 addict / Pillow / simplejson / sortedcontainers）
app\python_cuda\python.exe -m pip install \
  torchaudio==2.11.0 --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
  voxcpm==2.0.3 --no-deps \
  wetext==0.1.4 transformers==5.13.0 librosa einops \
  addict==2.4.0 Pillow==12.3.0 simplejson==4.1.1 sortedcontainers==2.4.0 \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 4.（已废弃 exe 冻结）启动器改为随包 python_cuda 直启，无需编译。
#    交互菜单：app\Scripts\VoxCPM_TTS_v5_CN.ps1（双击 app\Scripts\Launch_TTS_Menu.bat 拉起）
#    网页界面：app\start_web_ui.bat
#    若日后仍需冻结 exe，请自备 PyInstaller（针对 python_cuda 3.12）另写 .spec。

# 5. 下载模型权重
app\python_cuda\python.exe -c "
import os; os.environ['HF_ENDPOINT']='https://hf-mirror.com'
from voxcpm import VoxCPM; VoxCPM.from_pretrained('openbmb/VoxCPM2')
" 2>/dev/null
# 将下载的模型复制到 app\model\openbmb\VoxCPM2\

# 5b. 下载离线降噪模型（随包内置，构建期需联网一次）
app\python_cuda\python.exe -c "
from modelscope import snapshot_download
snapshot_download('iic/speech_zipenhancer_ans_multiloss_16k_base', local_dir='app/models/zipenhancer')
"

# 6. 编译安装包（脚本会先把 app/ 7z 预压缩为 payload/app.7z，再调用 ISCC）
powershell -ExecutionPolicy Bypass -File build_installer.ps1
#   或直接： "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\VoxCPM2_TTS.iss
#   （需先手动生成 payload\app.7z：7z a payload\app.7z -cd app，并把 7za.exe 放 payload\）
```

### ISS 脚本关键配置

```ini
[Setup]
Compression=lzma2/fast          ; 模型已是压缩数据，fast 足够
SolidCompression=no             ; 大文件体量下固体压缩反而极慢
DiskSpanning=yes                ; 突破 4.2GB 单文件限制
DiskSliceSize=2000000000        ; 2GB/片，兼容 FAT32 U 盘
PrivilegesRequired=lowest       ; 无需管理员权限
```

## 调试经验 & 踩坑记录

### 1. PyTorch 版本兼容性

**问题**：首次安装时 pip 自动拉取了 `torch 2.13.0+cpu`（CPU 版），导致 CUDA 不可用。

**解决**：卸载 CPU 版，改用官方 pytorch cu126 源重装 CUDA 版：
```bash
pip uninstall torch torchaudio -y
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu126
```

### 2. torchaudio CUDA 版缺失

**问题**：PyTorch 2.12.1+cu126 可用时，torchaudio 的 cu126 wheel 最高只到 2.11.0，与 torch 2.12.1 版/ABI 不匹配。

**解决**：改用 torchaudio 2.11.0 CPU 版，不影响功能（音频编解码纯 CPU 即可）。

### 3. 单环境 vs 双环境

**决策**：一个 cu126 环境同时满足有卡/无卡场景，优于分离 python_cpu/python_cuda。
- 理由：torch 在 RTX 3060 (CUDA 12.6) 上自动走 GPU，在无 N 卡机器上自动回退 CPU
- 节省约 2 GB 体积（不需要维护两套 Python 环境）

### 4. InnoSetup 4.2GB 限制

**问题**：安装包超过 4.2 GB 时 ISCC 报 `Error: Disk spanning must be enabled`。

**解决**：启用 `DiskSpanning=yes` + `DiskSliceSize=2000000000`（2 GB 切片）。
- 产出：`Setup.exe` + `Setup-1.bin` ~ `Setup-4.bin`
- ⚠️ 分发时必须将所有文件放在一起，用户安装时自动拼接

### 5. SolidCompression 性能陷阱

**问题**：`SolidCompression=yes` 在 9.5 GB 数据量下，`lzma2/normal` 压缩极慢（>10 分钟卡在一个文件不动）。

**解决**：改用 `SolidCompression=no` + `lzma2/fast`。
- 原因：模型权重的 safetensors 文件和 CUDA DLL 已经是高度压缩的二进制数据，固体压缩带来的额外收益微乎其微
- 效果：编译时间从 >15 分钟降到 ~10 分钟

### 6. ISS DestDir 路径嵌套

**问题**：`DestDir` 配置错误导致安装文件多套一层 `app\` 目录。

**解决**：确认 `[Files]` 段中 `DestDir: "{app}"` 而非 `DestDir: "{app}\app"`。

### 7. 中文语言文件缺失

**问题**：ISCC 编译时报找不到 `ChineseSimplified.isl`。

**解决**：从 InnoSetup GitHub 仓库下载该文件放到 `installer/` 目录。

### 8. 模型下载源

**问题**：huggingface.co 在国内经常超时。

**解决**：设置 `HF_ENDPOINT=https://hf-mirror.com` 环境变量，或通过 `VOXCPM_MODEL_DIR` 直接指向本地模型目录实现离线。

### 9. wetext 文本规范化顺序

**经验**：先对正文做 wetext 规范化，再拼接 `(控制指令)` 括号。如果反过来，wetext 会把括号内的中文描述也做规范化处理，可能破坏控制指令。

### 10. 交叉淡入淡出拼接

**实现**：`crossfade_concat()` 函数对相邻音频段做余弦窗口混合（50ms 默认），最后一段保留完整不做截断。这比简单拼接消除了大部分段间爆音和静音间隙。

### 11. 离线降噪（ZipEnhancer）的内置与依赖

**背景**：真实降噪依赖 `voxcpm/zipenhancer.py` → `modelscope.pipelines(Tasks.acoustic_noise_suppression, model=本地路径)`，且 `modelscope` 解析配置需要 `addict` / `Pillow` / `simplejson` / `sortedcontainers`。

**关键补丁**：`zipenhancer.py` 的 `_normalize_loudness` 原本用 `torchaudio.load/save`（硬依赖 torchcodec + FFmpeg），已改为 `soundfile` 读写，去掉联网/FFmpeg 依赖，离线可跑。

**启用策略（优雅降级，保持离线安全）**：仅当随包 `models/zipenhancer/configuration.json` 存在且 `ZipEnhancer(zpath)` 预加载成功时，`from_pretrained(load_denoiser=True, zipenoiser_model_id=本地路径)`，否则 `load_denoiser=False`（空操作）。模型缺失时不影响基础 TTS。

**为何不再冻结 exe**：原冻结 `.exe` 仅约 5MB，不可能包含 torch（数 GB），本质是把运行委托给随包 `python_cuda`。因此现改为直接用 `python_cuda` 跑 `.py` 入口，天然包含新增降噪依赖与模型，无需重冻结、更稳健。

## 体积优化记录

| 阶段 | app 目录 | 安装包 | 优化手段 |
|------|----------|--------|----------|
| 初始 | 9.47 GB | 6.04 GB | 基线 |
| 优化后 | 9.28 GB | 5.99 GB | 删除 `torch/include` + `Lib/test` + 各包 `tests/` |

> **注意**：模型权重（4.62 GB）和 CUDA 运行时 DLL（~2.5 GB）占总体积 95%+。删除 Python 测试文件节省有限。如需进一步减重，可考虑排除 modelscope 的 CV 模型、torch 实验性模块等。

## 许可证

- **VoxCPM2 模型**：遵循 [openbmb/VoxCPM2](https://github.com/OpenBMB/VoxCPM) 原始许可
- **本分发包**：自定义许可，仅供个人研究使用

## 依赖清单

完整 pip 依赖列表（共 82 个包，含离线降噪所需的 `addict` / `Pillow` / `simplejson` / `sortedcontainers`）：

```
aiohappyeyeballs==2.7.1
aiohttp==3.14.1
aiosignal==1.4.0
annotated-doc==0.0.4
annotated-types==0.7.0
anyascii==0.3.3
anyio==4.14.1
argbind==0.3.9
attrs==26.1.0
audioread==3.1.0
certifi==2026.6.17
cffi==2.1.0
charset-normalizer==3.4.9
click==8.4.2
colorama==0.4.6
contractions==0.1.73
datasets==5.0.0
decorator==5.3.1
dill==0.4.1
docstring-parser==0.18.0
einops==0.8.2
filelock==3.29.7
frozenlist==1.8.0
fsspec==2026.4.0
h11==0.16.0
hf-xet==1.5.1
httpcore==1.0.9
httpx==0.28.1
huggingface-hub==1.22.0
idna==3.18
inflect==7.5.0
joblib==1.5.3
kaldifst==1.8.0
lazy-loader==0.5
librosa==0.11.0
llvmlite==0.48.0
markdown-it-py==4.2.0
markupsafe==3.0.3
mdurl==0.1.2
modelscope==1.38.1
modelscope-hub==0.1.7
more-itertools==11.1.0
mpmath==1.3.0
msgpack==1.2.1
multidict==6.7.1
multiprocess==0.70.19
narwhals==2.23.0
networkx==3.6.1
numba==0.66.0
numpy==2.4.6
packaging==26.2
pandas==3.0.3
platformdirs==4.10.0
pooch==1.9.0
propcache==0.5.2
pyahocorasick==2.3.1
pyarrow==24.0.0
pycparser==3.0
pydantic==2.13.4
pydantic-core==2.46.4
pygments==2.20.0
python-dateutil==2.9.0.post0
pyyaml==6.0.3
regex==2026.6.28
requests==2.34.2
rich==15.0.0
safetensors==0.8.0
scikit-learn==1.9.0
scipy==1.18.0
setuptools==81.0.0
shellingham==1.5.4
six==1.17.0
soundfile==0.14.0
soxr==1.1.0
sympy==1.14.0
textsearch==0.0.24
threadpoolctl==3.6.0
tokenizers==0.22.2
torch==2.12.1+cu126
torchaudio==2.11.0
tqdm==4.68.4
transformers==5.13.0
typeguard==4.5.2
typer==0.26.8
typing-extensions==4.16.0
typing-inspection==0.4.2
urllib3==2.7.0
voxcpm==2.0.3
wetext==0.1.4
xxhash==3.8.1
yarl==1.24.2
```

> 离线降噪额外依赖（已在 `requirements.txt` 中）：`addict==2.4.0`、`Pillow==12.3.0`、`simplejson==4.1.1`、`sortedcontainers==2.4.0`。

## 原始资源

- **VoxCPM2 模型**：[OpenBMB/VoxCPM2](https://github.com/OpenBMB/VoxCPM)
- **PyTorch CUDA Wheel**：[pytorch.org](https://pytorch.org/get-started/locally/)
- **InnoSetup**：[innosetup.com](https://www.innosetup.com/)
- **HF Mirror**：[hf-mirror.com](https://hf-mirror.com)
- **ZipEnhancer 降噪模型**：[modelscope iic/speech_zipenhancer_ans_multiloss_16k_base](https://modelscope.cn/models/iic/speech_zipenhancer_ans_multiloss_16k_base)
