# VoxCPM2 TTS 中文版 使用手册(v5.3.7，2026-09-25 修订)

离线可用的中文 TTS(文本转语音)工具包:自带 CUDA Python 运行时 + 引擎 + Web 界面 +
**OpenAI 兼容 API(v5.3.7 新增)**。所有合成在本机完成,不联网、不上传。

---

## 一、系统要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10/11 x64 |
| 显卡 | NVIDIA CUDA GPU(显存 ≥8GB 推荐);无独显可 CPU 合成(非常慢,仅试音) |
| 磁盘 | 安装包 ~1.7GB(无模型版) + 模型 ~4.7GB + 安装后运行目录 ~5GB |
| 依赖 | 无(自带 Python/CUDA/库,完全离线) |

## 二、安装(重要:安装路径必须纯 ASCII)

1. 双击 `VoxCPM2_TTS_v5.3.7_nomodel_Setup.exe`,选安装目录
   (默认 `D:\VoxCPM2 TTS`, 可自行更改; 必须纯英文)。
2. **⚠ 安装路径不能含中文/特殊字符**:如 `D:\电脑桌面\VoxCPM2`。
   原因:引擎内预编译的 kaldi-fst C++ 组件无法打开非 ASCII 路径下的 `.fst`
   音系文件,合成会报 502 "Error opening input stream"(文件其实存在)。
   纯 ASCII 路径(含空格)无影响。
3. 安装完成后(无模型版需补模型,二选一):
   - 双击安装目录 **`下载模型.bat`**(联网,ModelScope 主源 + HuggingFace 备用,
     支持断点续传,模型放 `model\openbmb\VoxCPM2`);
   - 或从网盘下「模型专用 7z」,解压到安装目录 `model\openbmb\VoxCPM2`(离线)。

## 三、三种使用方式

### ① Web UI(图形界面)
双击安装目录 `start_web_ui.bat` → 浏览器打开
`http://127.0.0.1:19001`(端口可在 bat 里改 `--port`)。
首次启动加载模型约 1-3 分钟,之后合成秒级。
界面内可直接:输入文本 → 选音色/预设 → 试听下载;管理参考音色/语料;
LoRA 多音字训练(Scripts\training)。
另有一批界面/操作改进(详见 §12):「待合成文本」框默认三行且底部可调高手柄、
「最近合成记录」标题行共享波形+调速、深浅色主题切换、音色/参考音色直接试听、控制台开关常可用。

### ② 命令行菜单
双击 `Scripts\Launch_TTS_Menu.bat` 或
`Scripts\VoxCPM_TTS_v5_CN.ps1`(PowerShell),按菜单选功能。

### ③ OpenAI 兼容 API(v5.3.7 新增,给工具/工作台对接用)
双击 `Scripts\start_openai_api.bat`:
- 自动检查并拉起后端(19001,未起则启动并等待);
- 启动 OpenAI 兼容适配层 **`http://127.0.0.1:8020`**。

对接参数(灵剪、Lingji Cut、各类工作流/剪辑台均可用):

| 项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8020/v1` |
| API Key | 任意非空字符串(本地校验,可填 `local`) |
| Model | `voxcpm2`(也接受 `openbmb/VoxCPM2` 等前缀) |

端点:

| 端点 | 说明 |
|---|---|
| `GET /health` | 适配层+后端存活、可用音色 |
| `GET /v1/models` | OpenAI 兼容模型列表 |
| `POST /v1/audio/speech` | 文生语音(返回 WAV 二进制流) |
| `GET/POST/DELETE /v1/audio/voices` | 上传/列出/删除参考音色(克隆) |
| `POST/GET /v1/files` | 音频文件管理(代理后端) |

合成示例:

```
curl -X POST http://127.0.0.1:8020/v1/audio/speech ^
  -H "Content-Type: application/json" ^
  -d ^{"model":"voxcpm2","input":"今天天气怎么样?","voice":"default"}^ -o out.wav
```

## 四、音色(voice 参数)

**A. 11 个内置预设**(voice_design 通道,零配置):

| voice | 风格 |
|---|---|
| default | 温婉女声(旁白默认,推荐) |
| sweet_girl | 甜美女声 |
| cheerful_girl | 活泼少女(适合角色/旁白二) |
| energetic_broadcaster | 元气男声播报(适合角色) |
| storyteller | 说书腔 |
| calm_male | 沉稳男声 |
| cool_guy | 冷淡男声 |
| gentleman | 绅士男声 |
| elder_woman | 老年女声 |
| teacher | 教师女声 |
| warm_woman | 温柔中年女声 |

**B. 上传参考音色(克隆,推荐,音色最稳)**:

```
curl -X POST http://127.0.0.1:8020/v1/audio/voices ^
  -H "Content-Type: application/json" ^
  -d ^{"voice_id":"my_voice","audio_sample":"/abs/path/ref.wav","mode":"reference"}^
```
- 参考音频 5-30s 清晰单人声,16kHz+ WAV/MP3;
- 之后合成用 `"voice":"my_voice"`(fixed_clone 通道);
- `GET /v1/audio/voices` 列表,`DELETE /v1/audio/voices/{id}` 删除。
- 未知 voice 名 → 直接 400(返回可用列表),绝不静默回退。

## 五、高级参数(POST /v1/audio/speech)

| 参数 | 作用 | 注意 |
|---|---|---|
| `style`（推荐） | 风格控制词（语气/语速/情绪） | 参考克隆(fixed_clone)模式自 2026-09-24 修订起**真正生效**：指令自动加前缀、不被念出；预设 voice_design 通道仍有念出风险 |
| `instructions` / `control` | `style` 的等价别名 | voice_design 预设通道：响应头会挂 `X-VoxCPM-Warning`，**交付前必须人工试听**；稳妥做法=上传参考音色 + `style` |
| `prompt_text` | 场景描述文本 | 会**隐式切 hifi 模式**(文档 1.7):要么不带,要么显式 `mode="hifi"`;带 `strict:true` 时适配层直接 400 拒绝隐式切换 |
| `mode` | `reference`(克隆)/ `hifi` | 上传音色默认 reference |
| `ref_audio` | 参考音频路径(配合克隆) |
| `seed`（或 `voxcpm.seed`） | **可复现种子**(官方特性, 本次补齐) | 同种子+同文本+同设置 → 结果可复现(已验证字节级一致)；不传=随机。同一文本建议换 2~3 个 seed 生成后取优(官方推荐用法) | |
| `voxcpm.cfg_value` | 引擎 cfg(默认 2.5) | 越大越贴参考,越大越慢 |
| `voxcpm.inference_timesteps` | 推理步数(默认 15) | |
| `strict` | 护栏严格模式 | 隐式 hifi 切换直接 400 |
| `sample_rate` | 输出采样率(默认 48000) | 工作流常用 16000 |

## 六、响应头护栏(自动风险标记,不用逐条人肉盯)

| 响应头 | 含义 |
|---|---|
| `X-VoxCPM-Meta` | 本次合成元数据(job_id/时长/音色/模式),供 QC 追溯 |
| `X-VoxCPM-Warning` | 触发了已知风险(用了 instructions 控制通道 / 隐式切 hifi),**必须试听** |
| `X-VoxCPM-Duration-Anomaly` | 合成时长异常偏长(疑似控制词被念出),**必须试听** |
逐条请求同时落盘安装目录 `Scripts\adapter.log` 可审计。

## 七、常见问题(FAQ)

**Q: 首次合成很慢/等很久?**
后端首次加载模型 1-3 分钟属正常;加载后秒级。`GET /health` 看后端状态。

**Q: 端口被占用?**
后端 19001(`start_web_ui.bat` 内 `--port`)、API 8020(`start_openai_api.bat` 内
前端 19001 在 `start_web_ui.bat` 里改 `--port`;API 8020 在 `start_openai_api.bat` 的 adapter 启动行前加 `set VOXCPM_API_PORT=8021`。

**Q: 报 502 / "Error opening input stream" 但文件存在?**
安装路径含中文 → 见第二节,重装到 ASCII 路径。

**Q: 显存不够 OOM?**
同一台机器只跑一个后端实例;别同时开 18978/19001 两个后端。

**Q: 合成有 LoRA 音色漂移/多音字问题?**
v5.3.7 默认 `VOXCPM_LORA=""`(不用 LoRA);多音字纠读可用
`Scripts\prepare_polyphone_lora_data.py` + `Scripts\training\` 训练多音字 LoRA(文档见 Web UI 内说明)。

**Q: 如何卸载/更新?**
安装目录有 `unins000.exe`;更新=装新版即可(模型目录 `model\` 独立保留)。

## 八、v5.3.7 更新说明

- **新增** OpenAI 兼容 API 适配层(`Scripts\voxcpm_openai.py` + `start_openai_api.bat`,端口 8020):
  `/v1/audio/speech`、音色上传/管理、代理文件管理;
- **新增** 三条自动护栏:instructions 控制通道警示、隐式 hifi 切换警示(strict 可 400)、时长异常检测,均经响应头标记;
- **新增** 本使用手册与 `OpenAI_API_QuickRef.md`（API 速查卡）；
- 引擎/模型与 v5.3.6 一致（OpenBMB VoxCPM2，无 LoRA 默认）。

## 九、v5.3.7 修订（2026-09-24，本包已含）

- **新增** 字符/词级时间戳双引擎（v5.3.7 本构建核心新特性，见 §十一）：高精度引擎 **Qwen3-ForcedAligner-0.6B**（官方 80ms 网格、非自回归单次前向、11 语言；**不随安装包附带，首次使用自动下载 ~1.75GB**，HF 不可达自动切 hf-mirror 国内镜像）+ 兜底引擎 **whisper-base**（stable-ts，CPU，内置 139MB）；CLI `--timestamps` 一键产出逐字 JSON + SRT；独立脚本 `Scripts\voxcpm_timestamps_qwen.py`；
- **修复** 参考克隆（fixed_clone）模式下控制风格指令此前被静默丢弃：现在显式填写的指令会自动附加 `(指令)` 前缀并真正生效，指令本身不会被念出；
- **新增** OpenAI 适配层 `style` 字段（与 `instructions`/`control` 等价），程序化语气控制；
- **补齐** 可复现种子 `seed`（官方 README 特性，本包三层统一支持）：Web UI 高级参数「种子」输入框 / OpenAI API `seed` 字段 / 命令行菜单 `--seed`；同种子+同文本 结果可复现（已验证字节级一致，适合 A/B 对比与交付复现）；
- **补齐** 流式生成演示：`Scripts\voxcpm_streaming_demo.py`（引擎级 API 原生支持，零额外依赖；实测 35 块流式输出、首块 1.2s）；
- **修复** 适配层响应头 `X-VoxCPM-Meta` 含中文时崩溃（500）的问题（改为 ASCII 转义）；
- Web UI 控制指令框在参考克隆模式为**选填生效**（opt-in）：仅手动填写时附加前缀，预设回退描述不自动附加，默认链路行为不变。

---
## 十、官方特性对照（2026-09-24 全量复核）

对照 OpenBMB/VoxCPM 官方文档逐项核对，结果：

| 官方特性 | 本包状态 |
|---|---|
| 预设音色 / 可控制克隆(参考+指令) / 音色设计(零样本) / HiFi 克隆 | ✅ 已有 |
| 自然语言指令控制语气/语速/情绪 | ✅ 已修复生效（`style`/`--control`，指令不被念出） |
| **可复现 seed** | ✅ **本次补齐**（此前引擎无 seed 参数，已在三层实现：API `seed`、CLI `--seed`、Web UI 高级参数；引擎层通过全局随机源播种实现，同种子结果可复现） |
| 官方命令行工具 `voxcpm.exe`（`design`/`clone`/`batch`/`validate` 子命令） | ✅ 已随包（`python_cuda\Scripts\voxcpm.exe`），离线用法：`voxcpm.exe clone --model-path "<安装目录>\model\openbmb\VoxCPM2" --text "..." --reference <参考wav> --output out.wav`（设计音色加 `--cfg 2.0`；批处理用 `batch` 子命令 + jsonl） |
| 降噪预处理（zipenhancer） | ✅ 已有（Web UI 自动探测，缺失时自动降级） |
| LoRA 热切换 | ✅ 引擎支持，本包默认不用（`VOXCPM_LORA=` 留空） |
| 流式生成 `generate_streaming`（RTF ~0.3） | ✅ 引擎级已内置（`model.generate_streaming()` 逐块返回音频）；包内提供演示脚本 `Scripts\voxcpm_streaming_demo.py`（双击/命令行运行，打印每块到达时刻 + 写 wav）。注：官方 RTF 0.13 需 vLLM/Nano-vLLM 后端（大型独立依赖，本包未含） |
| `--timestamps` 时间戳（字符/词级对齐） | ✅ **内置双引擎**（Qwen3-ForcedAligner-0.6B 高精度 + whisper-base 兜底，无需额外安装，见 §十一） |

> 说明：官方 README 的 `generate(seed=42)` 是引擎层参数（官方 v5.3.7+ 支持）；本包引擎版本暂不接受 seed kwarg，因此改为在脚本层播种全局随机源（torch/numpy/random），效果等价——同种子+同文本结果可复现（已实测：同 seed 两次合成 MD5 一致）。冷启动（模型刚加载完的首个任务）可能因 CUDA 懒初始化与后续任务略有差异，属正常现象；正式对比请在同一会话内进行。

---

## 十一、字符/词级时间戳（v5.3.7 本构建新增，双引擎）

对任意 TTS 产出 wav 做**逐字/逐词时间戳对齐**（字幕、配音口型、精确剪辑用）。

### 引擎选型（自动）

| 引擎 | 模型 | 精度 | 速度 | 位置 |
|---|---|---|---|---|
| **Qwen3-ForcedAligner-0.6B**（默认） | 1.75GB，**自动下载**（安装包不含，本地 `models\qwen3_aligner\`） | 官方 80ms 网格，非自回归 | ~0.5s/句（GPU） | 本地 `models\qwen3_aligner\` |
| whisper-base（自动兜底） | 139MB，安装自带 | 秒级粗对齐 | ~3s/句（CPU） | `models\whisper\` |

优先 qwen3（本地 `models\qwen3_aligner\` 有则直接用；无则**首次使用自动下载 ~1.75GB**，HF 直连不可达自动切 hf-mirror 国内镜像，8 路并行 + 分片断点续传）；下载失败/无网时自动回退 whisper-base，不会报错。

**WebUI 单独管理（可选）**：Web UI 设置页有「Qwen3 时间戳对齐模型」卡片，可单独查看状态 / 下载（8 路并行，进度条）/ 删除释放空间。不随安装包附带，不下载也能用基础模式（whisper 兜底，精度略低）。

### 用法一：CLI 一键（合成 + 时间戳同出）

```bat
python Scripts\voxcpm_tts_v5_longtext.py --text "你的文本" --voice default --timestamps --timestamps-srt
```

输出（与 wav 同名 sidecar，写在输出目录）：
- `*.timestamps.json`：逐字/逐词 `{start, end}` 数组 + 引擎标注
- `*.timestamps.srt`：SRT 字幕（--timestamps-srt 时）

可选参数：`--timestamps-language zh|en`（默认 zh）。

### 用法二：独立对齐任意 wav（不重新合成）

```bat
python Scripts\voxcpm_timestamps_qwen.py <wav文件> <文本> --chars --srt --device cuda
```

- `--chars` 逐字模式（默认逐词）；`--srt` 同时写 SRT；`--keep-vram` 对齐后不释放显存（连续多文件时省加载时间）
- 按需加载模型，**用完自动释放显存**，与主模型不同时占用 8GB 显存峰值（顺序执行）；对齐 10s 语音约 1–2 秒

### 说明
- 时间戳基于**强制对齐**（文本已知，非 ASR 识别），误差远小于重新跑一遍语音识别；
- Qwen3 引擎支持 11 种语言（zh/en/yue/ja/ko/fr/de/it/ru/es/pt），单段音频上限 5 分钟；
- 若不需要高精度时间戳，CLI 加 `--no-download` 或断网运行，自动降级 whisper 兜底（句级精度）。
- 内网离线：手动把 6 个文件放到安装目录 `models\qwen3_aligner\`（HF 仓库 `Qwen/Qwen3-ForcedAligner-0.6B-hf`）即优先本地加载。

---

## 十二、Web UI 界面/可用性更新（2026-09-25）

以下均为**界面与操作改进**，不影响引擎/模型（引擎仍为 VoxCPM2，版本号维持 v5.3.7）：

| 功能 | 说明 |
|---|---|
| 「待合成文本」框默认三行 | 不再把卡片撑满，默认紧凑三行；框底部有自绘调高手柄，上下拖动变高/变矮（下限=三行高度），刷新页面即可回到默认三行 |
| 「最近合成记录」共享波形+调速 | 记录卡标题行带最新一条合成音频的波形 + 调速下拉（0.5x–2x）；合成完成直接看波形，无需打开播放器；再次点击播放时波形同步跟随 |
| 深浅色主题切换 | 单个 🌙/☀️ 按钮在深/浅色主题间切换（图标跟随当前主题），保留原「自定义强调色」选择器 |
| 音色/参考音色直接试听 | 应用音色档案(参考音色)后，试听按钮直接播放已加载的参考音频，而非每次重生成短句(省 GPU) |
| 控制台窗口开关常可用 | 「显示/隐藏控制台」按钮恒可用；在无原生控制台的宿主(git-bash/ConEmu/Terminal)改为开/关日志尾随窗口，不再出现「切换失败」 |
| 文本输入框可拖调高 | 「音色描述」「参考转录」等输入框底部同样有自绘调高手柄，外边框随收缩/扩张跟随 |
| 合成记录可达性校验 | 「最近合成记录」只保留音频文件当前可访问的记录；输出目录改动/文件被清后，不可达记录自动清出列表并持久化（含「恢复」备份），清掉时 toast 提示 |
| 音色档案可达性校验 | 档案的参考音频若已不存在，打开「音色档案」弹窗时自动清出列表（后端逐档案标注 ref_ok，与 /api/audio 同款查找路径）；未存参考文件的档案保留 |

> 以上均为前端展示与交互改动；合成 API、参数、音色、时间戳等能力不变。排障见 §七 FAQ。

---
排障: API 请求日志在 `Scripts\adapter.log`;后端日志在 Web UI 控制台窗口。
