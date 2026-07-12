# VoxCPM2Dist 离线发行版 · 开发经验与避雷手册

> 版本：v5.0.2（离线 TTS 分发版）
> 适用范围：基于 OpenBMB VoxCPM2 的中文 TTS 离线发行包（Windows x64 + NVIDIA CUDA，可 CPU 回退）
> 整理日期：2026-07-12
> 目的：把本次开发踩过的坑、验证过的治本方案、以及用户明确约定的工作流固化下来，便于日后复用。

---

## 0. 项目架构速览（一图看懂）

```
VoxCPM2Dist/
├── app/                         # 发行载荷（9.4GB，不进 git，走网盘）
│   ├── python_cuda/             # 内置 Python 3.12.10 (embed) + 冻结依赖
│   ├── model/openbmb/VoxCPM2/   # 模型权重（本地，离线）
│   ├── Scripts/                 # vox_web_ui.py / voxcpm_tts_v5_longtext.py / VoxCPM_TTS_v5_CN.ps1
│   └── Launch_TTS_Menu.bat      # 入口，拉起 ps1 菜单
├── installer/VoxCPM2_TTS.iss    # InnoSetup 安装包定义
├── build_installer.ps1          # 打包脚本：7z 压 app → payload/app.7z → ISCC 编译
├── payload/                     # 构建工作目录（app.7z + 7za.exe），保留以便原地重打
├── output/                      # 构建产物：Setup.exe + 3×.bin 分卷（每次重建覆盖）
├── README.md / requirements.txt / .gitignore
└── VoxCPM2_TTS_v5.0.2_Setup.zip # 分发包（网盘用，被 .gitignore 排除）
```

**三条入口，归一化必须一致**：
- 网页端 `vox_web_ui.py`（FastAPI + 内嵌 HTML）
- 长文本 CLI `voxcpm_tts_v5_longtext.py`
- PowerShell 菜单 `VoxCPM_TTS_v5_CN.ps1`（唯一入口，拉起其余两条）
- 数字归一化统一在 `text_norm_cn.py`（`app/` 与 `app/Scripts/` 各一份镜像，纯 re、无外部依赖）；两条路径都 `from text_norm_cn import normalize_text`，改动需同步两边。

---

## 1. 音频 / TTS 核心经验（必看）

### 1.1 长文本必须做段间音量归一化
- **症状**：长文本合成后音色/响度前后不一致、忽大忽小。
- **根因**：长文本切多段，每段由扩散模型独立生成；交叉淡入淡出前**没有段间 RMS 归一化**，模型输出的 RMS 天然不同，直接拼接必然跳。
- **治本**：`normalize_segments()`（各段 RMS 拉到非静音段平均值）+ `peak_normalize()`（单段峰值 ≤0.99）；crossfade 后再整体 0.95 峰值限制。**RMS/峰值归一化必须在 crossfade 之前做**。
- 音色一致性会因响度统一而显著改善；剩余音色差异属模型/扩散采样固有限制。

### 1.2 自播种（self-seeding）才是"真·第1段当种子"
- 真正自播种 = CLI 无 `--reference` 时默认模式 B（第1段 Voice Design 当种子），与"固定参考克隆"一致性**等价**（都走 ref_continuation + 段间归一化，篇内不漂移）。
- ps1 菜单「方式1 自播种」原名不副实——它其实也预生成参考音频再克隆（=方式2），并非真正自播种。
- **修正**：`>180` 自动分支与菜单项10 均改为真正 `--self-seeding --crossfade 80`，省一次参考生成（提速约 20%~30%），且与网页端行为统一。
- `load_denoiser=False` 必须保留（离线不联网下载降噪模型；denoise 在未装 denoiser 时为空操作，优雅降级）。

---

## 2. Web UI 双击打不开（两个独立根因）

**根因 A（真·主因）—— embed Python 的 sys.path 机制**
- 内置 `python_cuda` 是 embeddable Python，其 `_pth` 机制**不会把脚本所在目录（Scripts/）加入 sys.path**（sys.path[0] 是 python312.zip）。
- 故 `from text_norm_cn import normalize_text` 必 `ModuleNotFoundError`。
- **治本**：`vox_web_ui.py` 与 `voxcpm_tts_v5_longtext.py` 顶部加：
  ```python
  import os, sys
  sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
  ```
- ⚠️ `longtext.py` 此前该 import 在 try/except 内静默回退 wetext → 归一化与网页端不一致，现已真正统一。**复用经验：内嵌 Python 跑本地脚本务必手动把 __file__ 目录加入 sys.path。**

**根因 B（次因）—— 端口占用被静默吞**
- `.bat` 曾强制 `--port 8000`，被占用时 uvicorn 抛 OSError；`hide_console=True` 启动 2s 后自动隐藏窗口 → 失败被静默吞，表现"双击无反应"。
- **治本**：端口占用时自动顺延 8000~8049 探测可用端口；仅 FastAPI startup 确认成功后才隐藏窗口；失败打印明确中文错误并等待回车。

**端口一致性**：代码 argparse `--port` default **本就是 18978**（run_server default=18978）。勿误改成 8000；改动只能精准替换 docstring，不可全局 replace_all（文件内有 `0x8000` 常量）。

---

## 3. 安装包打包（本次最大的坑，重点避雷）

### 3.1 7-Zip 24.08 LZMA2 压 torch CUDA 大 DLL → native 崩溃 💥
- **现象**：`build_installer.ps1` 的 `[3/4] 正在压缩 app` 每次死在 ~2 分钟、输出 ~1GB 处（24/8/单线程、限固实块 `-ms=2g` 全都崩）。
- **定位**：`7z a -bb3` 详细日志最后一行停在 `python_cuda\Lib\site-packages\torch\lib\cusparse64_12.dll`（CUDA 大 DLL，数百 MB），其后**无任何 Error、无退出码** → 进程直接 abort（native crash）。
- **根因**：7z **24.08 的 LZMA2 算法**在压缩 torch CUDA 超大 DLL 时存在 native bug。**与线程数无关、与 safetensors 无关、与磁盘满/OOM/Defender 无关**。
- ⚠️ **重大误判记录**：曾以为"单线程稳"——但单线程构建同样崩（之前从未让它真正跑完验证）。**勿再迷信"单线程就稳"。**
- ⚠️ 7z 24.08 **没有编译进 ZSTD 编解码器**（只有 .zst 容器格式），故 `-m0=zstd` 报"参数错误"，zstd 不能在 .7z 内当算法用。

### 3.2 治本方案（已验证）
- **首选：升级 7-Zip 到 26.02**（用户已下载安装）。26.02 的 LZMA2 多线程压完整 app（9.32GB 原始 → 4.88GB）全程稳过。
  - 压缩参数：`@('-t7z','-mmt=on','-mx=7')`（多线程、最佳压缩率+速度）。
  - `build_installer.ps1` 自动下载 URL 同步升到 `7z2602-x64.exe`。
- **备选（若仍崩）**：换非 LZMA2 编解码器，如 PPMd（`-m0=PPMd -mmt=off -mx=7`）。PPMd 能越过崩溃点，对 exe/DLL 压缩率通常优于 LZMA2。
- ❌ **PPMd 不可作主方案**：单线程压 9.4GB 实测 8 小时只压 1.58GB 卡死，不可行。

### 3.3 沙箱 / 工具调用坑
- **PowerShell `$PID` 是只读自动变量**（=当前进程 ID），等待脚本里 `$pid = 6920` 赋值直接报错 → 监控逻辑白跑整轮。**改用其他变量名**（如 `$zipPid`）。
- **本环境 Bash 工具间歇故障**（报 `command expected string but received undefined`）；PowerShell 工具正常。长耗时 7z 压缩用 **Bash 直接调 `7z.exe` 本体**后台跑；ISCC 编译用 **PowerShell `Start-Process` 调原生 exe**（非解释器，不被沙箱禁）。
- 监控后台任务：要么靠系统完成通知，要么轮询产物文件大小增长，别依赖"等待某 PID 结束"的脚本（绕开 `$PID` 坑）。

### 3.4 ISCC 弃用警告
- `installer/VoxCPM2_TTS.iss` 第17行 `ArchitecturesInstallIn64BitMode=x64` 触发弃用 warning。
- 改为 `x64compatible`（ISCC 推荐大多数情况用此值），重跑确认 warning 消失。AppId 保持 `5.0` 以便原地升级；MyAppVersion / OutputBaseFilename 随版本走。

---

## 4. 依赖与环境

### 4.1 requirements.txt 必须与实际环境一致
- 头里版本注释曾过期（`v4.0` → 已改 `v5.0.2`）。
- 逐条比对 `requirements.txt` 的 33 项直接依赖 与 `app/python_cuda/Lib/site-packages` 实际安装：OK=33 / DIFF=0 / MISS=0，**完全一致**。
- ⚠️ 依赖清单口径坑：README 曾写"共 82 个包"但实列 91、且漏列 10 个（Web UI 栈 fastapi/starlette/uvicorn/jinja2/python-multipart/tzdata + 离线降噪 addict/Pillow/simplejson/sortedcontainers）。**真实第三方依赖数 = 101**（= site-packages 的 102 个 dist-info − pip 安装器本身）。已按实测重生成完整 101 个写进 README。

### 4.2 embed Python 与普通版
- `python_cuda` = Python 3.12.10 **embed** 经 `get-pip.py` 加装 pip：含 `python312._pth`（已启用 `import site`）、`python312.zip`、`get-pip.py`；**无 `pyvenv.cfg`**（不是 venv）。
- **运行效果与普通完整 Python 完全一致**——只是发行形态差异：embed 不写注册表/PATH、整个目录可携、靠 `_pth` 锁定 site-packages。
- **普通版可用**：用户自建 Python 3.12.10 + venv + `pip install -r requirements.txt`，把启动脚本的 python 路径指过去，跑出来一样。**但作为离线分发物，embed 更干净、更适合随包。**

### 4.3 Python 版本要求（已写入 README + requirements.txt）
- 随包固定 **3.12.10**；从源码构建请用 **3.12.x**；**Python 3.13 未验证**（torch CUDA 轮子/依赖可能不兼容）。
- 版本是**有意 pin 死**的兼容性锁，不要随意升（曾踩坑：pip 自动拉 `torch 2.13.0+cpu` 导致 CUDA 失效）。

---

## 5. Git 工作流约定（用户明确要求，务必遵守）

- **必须保留提交历史**：每次修改用普通 `git commit` + `git push`，**禁止** `git push -f` / `git commit --amend` 改写历史。
- **版本用 Git Tag 标记**：重要节点打 annotated tag（`git tag -a vX.Y.Z -m "..."` 并 `git push origin vX.Y.Z`），让 GitHub Releases 有可追溯版本。
- **连续小调整可继续在同一版本内**（如本次 v5.0.2 一系列修正都没新建 tag、没改版本号）。
- ⚠️ **提交用显式 add 具体文件，绝不 `git add -A`**：曾误把 `build_7z.log` 带进库，靠 `git rm --cached` + `.gitignore` 修复。构建日志 `*.log`、分发 zip `VoxCPM2_TTS_v*.zip` 应提前写进 `.gitignore`。

### 国内直连 GitHub 推送（必读）
- 需先开 **Watt Toolkit 代理（端口 26561）**：`git config --global http.proxy http://127.0.0.1:26561`。
- 写操作（push）报 **401** 时：`gh auth token` 取 token →
  `git config --global http.extraheader "Authorization: Basic <base64(x-access-token:TOKEN)>"` 注入 → 再 push。
  （读操作 `ls-remote` 可过，写不行；gh credential helper 对 push 不提供凭据。）
- Bash/Linux 子系统的 git 连不到 127.0.0.1:26561（网络命名空间隔离）→ **推送须用 PowerShell 工具**在 Windows 侧执行。

### README 图片（面向国内用户）
- 公开仓库图片用 **jsDelivr CDN**：`https://cdn.jsdelivr.net/gh/<user>/<repo>@<branch>/<path>`，避免 `raw.githubusercontent.com` 在国内被墙只显示 alt 文字。

---

## 6. 分发与文件系统

- **9.4GB 二进制载荷（环境+权重）不进 git**，走网盘（阿里云盘分发链接已在 README）；`.gitignore` 排除 `VoxCPM2_TTS_v*.zip`。
- **根目录 `VoxCPM2_TTS_v5.0.2_Setup.zip` 是自包含完整归档**（内含 output/ 的 exe + 3×.bin），找回旧版只需下载解压该 zip；`output/` 松散分卷是同字节冗余，可删（每次重建会重新生成）。
- **桌面快捷方式/卸载图标**经 .iss 的 `IconFilename`/`UninstallDisplayIcon` 指向 `installer/assets/VoxCPM_App.ico`。
- **清理原则**：诊断日志（7z_*.log / build_7z.log）、`__pycache__/` 是垃圾可删；`build/VoxCPM_TTS.spec`（PyInstaller 备用打包模板，README/vox_web_ui.py 引用）**有意保留**，非垃圾。
- 构建前体检：全文搜 `C:\`/`D:\`/本机用户名/项目绝对路径；配置 JSON 不带机器专属值；默认路径用 `Path.home()` 或相对 `__file__` 派生的安装目录。

---

## 7. 关键命令速查

```powershell
# 重打包安装包（基于当前 app/）
& "D:\AI\Build\VoxCPM2Dist\build_installer.ps1"
# 等效拆两步（沙箱受限时）：
# 1) 压缩（Bash 直接调 7z 后台）
& "C:\Program Files\7-Zip\7z.exe" a -t7z -mmt=on -mx=7 ../payload/app.7z *
# 2) 编译（PowerShell Start-Process 调 ISCC）
Start-Process "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" -ArgumentList "D:\AI\Build\VoxCPM2Dist\installer\VoxCPM2_TTS.iss"

# 重打根目录分发 zip（纯打包，最快）
& "C:\Program Files\7-Zip\7z.exe" a -tzip -mx=1 VoxCPM2_TTS_v5.0.2_Setup.zip output\VoxCPM2_TTS_v5.0.2_Setup.exe output\*.bin

# 验证归档完整
& "C:\Program Files\7-Zip\7z.exe" t payload\app.7z

# 推送（Watt Toolkit 代理开 + http.extraheader 注入后）
git push -u origin main --follow-tags
```

---

## 8. 已知事项 / 待办
- 安装包物理产物（zip/output/payload）按约定不进版本库，仍在本地，需用户上传网盘覆盖旧分发（链接不变）。
- Python 3.13 兼容性未验证；如需追新版本，须重建 `python_cuda` + 重新打包 9.4GB，收益低。
- ima 知识库 MCP 在本会话仅暴露读/搜索工具，无写入接口；本手册为 Markdown，可手动导入 ima 新建分组。

---

*整理自 v5.0.2 全周期开发记录（自播种统一、Web UI 修复、7z 打包崩溃治本、依赖核对、Python 版本说明等）。*
