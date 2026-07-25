# VoxCPM2 多音字 LoRA 训练指南（D:\AI\Build\多音字\）

> 目标：用 520 句「强制正确读音」的合成语音（音频即正确读音），训练一个 VoxCPM2 的 LoRA，
> 专门纠正多音字 (polyphone) 的读音。

---

## 0. 关键结论（先读）

- **VoxCPM2 已在本地找到**，无需安装：
  - pip 包 `voxcpm` **2.0.3**，位于系统 Python 3.12：
    `C:\Users\000\AppData\Local\Programs\Python\Python312\Lib\site-packages\voxcpm`
  - 模型 checkpoint：`D:\AI\Build\VoxCPM2Dist\app\model\openbmb\VoxCPM2`
    （含 `config.json` / `model.safetensors` ~4.5GB / `audiovae.pth` / `tokenizer.json`；
    `architecture: voxcpm2`，`sample_rate: 16000`，`out_sample_rate: 48000`）
  - 已验证的训练入口：`D:\AI\Build\VoxCPM2Dist\app\Scripts\training\train_voxcpm_finetune.py`
- **本项目的启动器**是 `D:\AI\Build\多音字\train_lora.py`（本项目级 glue：造清单 + 出配置 + 拉起训练）。
- **唯一指定的 Python**：`C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe`
  （Python 3.12.10，torch 2.12.1+cu126）。**不要**用 WorkBuddy 自带的 3.13，也**不要**动这个环境（不要 pip install）。

---

## 1. VoxCPM2 训练接口（实测结论）

VoxCPM2 是**音频条件 (audio-conditional)** 模型，训练配对就是 `(audio, text)`：
- `audio` = 我们的合成语音（已含正确读音）
- `text`  = 原始中文句（模型要学会「这个句子→这样读」）

### 1.1 数据格式：JSONL 清单（每行一条）

```json
{"text": "伐木丁丁，鸟鸣嘤嘤，山间自有清音。", "audio": "D:\\AI\\Build\\多音字\\wavs\\0001.wav"}
```

- 由 `voxcpm.training.load_audio_text_datasets(train_manifest=...)` 读取
  （内部用 HuggingFace `load_dataset("json", ...)` + `Audio(16000)` 加载音频）。
- 可选列：`ref_audio`（参考音路径）、`dataset_id`（多数据集标识，缺省补 0）。
- 音频会被重采样到 `sample_rate=16000`（V2 编码器输入率），非 16k 自动重采样。

### 1.2 LoRA 配置（`voxcpm.model.voxcpm2.LoRAConfig`）

字段（`train_voxcpm_finetune.py` 里以 dict 形式传给 `train()`）：

| 字段 | 含义 | 本项目取值 |
|------|------|-----------|
| `enable_lm` | 对 base_lm + residual_lm 加 LoRA（文本→语义/读音映射，**多音字修复就在这里**） | `true` |
| `enable_dit` | 对 feat_decoder（声学/音色）加 LoRA | `false` |
| `enable_proj` | 对投影层加 LoRA | `false` |
| `r` | LoRA rank | `16`（仓库现成 yaml 用 32，见 §4） |
| `alpha` | LoRA 缩放 | `32` |
| `dropout` | LoRA dropout | `0.0` |
| `target_modules_lm` / `target_modules_dit` / `target_proj_modules` | 目标层名（有默认值，一般无需改） | 默认 |

> **为什么 `enable_dit=false` / `enable_proj=false`**：VoxCPM2 是音频条件模型，音色在**推理时**由
> `reference_wav_path` 的参考音克隆，与 LoRA 通道隔离。LoRA 只改「读音映射」（LM），不动「声学渲染器」
> （DiT），才能做到「纠正多音字、保留音色让模型自己解决」。打开 dit 会扰动音色，违背初衷。

### 1.3 训练入口（`train_voxcpm_finetune.py`）关键超参

| 参数 | 说明 | 本项目默认 |
|------|------|-----------|
| `pretrained_path` | 模型目录（见 §0） | VoxCPM2Dist `.../model/openbmb/VoxCPM2` |
| `train_manifest` / `val_manifest` | JSONL 清单 | `lora_train.jsonl` / `lora_val.jsonl` |
| `sample_rate` | **必须等于 16000**，否则 assert 失败 | `16000` |
| `out_sample_rate` | 仅 TensorBoard 回放用 | `48000` |
| `batch_size` | 物理批次（受显存限制） | `2` |
| `grad_accum_steps` | 梯度累积；有效批次 = `batch_size × grad_accum_steps` | `8`（有效 16） |
| `num_workers` | Windows(spawn) 下每个 worker 是完整进程；RAM 紧可降到 1 | `2` |
| `num_iters` / `max_steps` | 训练步数（**step-based，不是 epoch-based**） | 由 `--epochs` 换算 |
| `learning_rate` | | `2e-4`（仓库现成 yaml 用 `1e-4`） |
| `weight_decay` | | `0.01` |
| `warmup_steps` | 余弦+warmup 调度 | `100` |
| `max_batch_tokens` | 按估算 token 数过滤超长样本；`0` 关闭 | `8192` |
| `max_grad_norm` | 梯度裁剪；`0` 关闭 | `1.0` |
| `save_path` | 权重输出目录 | `lora_output/` |
| `tensorboard` | 日志目录（可选，缺 `tensorboardX` 时自动降级为无图模式） | `lora_output/logs` |
| `lambdas` | 各子损失权重 | `loss/diff:1.0, loss/stop:1.0` |

> 入口自带：① LoRA 接入；② **datasets 5.x 的 soundfile 离线补丁**（monkeypatch `Audio.decode_example`，
> 绕开缺失的 ffmpeg / torchcodec，见入口文件第 56–78 行）；③ 余弦+warmup 调度；④ 梯度累积；
> ⑤ 断点续训（`latest/`）；⑥ `SIGTERM`/`SIGINT` 安全存盘。

---

## 2. 所需数据布局

```
D:\AI\Build\多音字\
├── wavs\                      # 合成音频（由合成 agent 产出）
│   ├── 0001.wav               #   命名：{index:04d}.wav
│   ├── 0002.wav
│   └── ...                    #   共 520 条（与 v19_teacher_text.jsonl 行的 idx 对应）
├── wavs_manifest.jsonl        # 合成 agent 写的清单（推荐，优先被吃）
├── data\
│   └── v20_teacher_text.jsonl # 520 行，字段 sentence/cosy3_text/it2_text/idx...
├── train_lora.py              # 本项目启动器（本文件配套脚本）
├── lora_train.jsonl           # 运行后生成：{text, audio}
├── lora_config.yaml          # 运行后生成：LoRA 训练配置
└── lora_output\               # 运行后生成：LoRA 权重 + lora_config.json
```

### `wavs_manifest.jsonl`（合成 agent 产出，推荐格式）

由 `synth-batch-ref` 在批量合成时产出，**真实 schema**（每行一个 JSON 对象）：

```json
{"index":1,"wav":"wavs/0001.wav","sentence":"伐木丁丁，鸟鸣嘤嘤，山间自有清音。",
 "char":"丁","pinyin_mark":"zhēng","pinyin_t3":"zheng1","model":"cosy3",
 "sample_rate":24000,"cosy3_text":"[f][á][m][ù][zh][ēng][zh][ēng]，...","it2_text":"fa2 mu4 zheng1 zheng1 ， ..."}
```

字段说明：
- `index`：int，= `v19_teacher_text.jsonl` 的 `idx`（1-based）；**wav 文件名用它编码**（`{idx:04d}.wav`）。
- `wav`：str，**相对项目根 `D:\AI\Build\多音字`** 的路径，如 `wavs/0001.wav`。
- `sentence`：str，**原始中文句 = VoxCPM2 训练目标文本**。
- `char` / `pinyin_mark` / `pinyin_t3`：被强制读音的多音字元数据。
- `model`：`"cosy3"` | `"it2"`；`sample_rate`：cosy3 输出 24000。
- `cosy3_text` / `it2_text`：**喂给 TTS 强制读音的控制串**（方括号音素 / 空格拼音），**不是训练文本**。

`train_lora.py` 的映射：`text = sentence`；`audio = PROJECT_DIR / wav`（相对路径相对项目根解析，
**不会**解析成 `wavs/wavs/...`）。脚本**优先吃这个清单**；若它缺失，则按 `wavs/{idx:04d}.wav`
与 `v19_teacher_text.jsonl` 的 `idx` 自动配对。

> **`--text-field` 安全护栏**：若误传 `cosy3_text` / `it2_text`，脚本会打印醒目警告——那是 TTS 控制码，
> 当训练 text 会让模型学去输出音素/拼音标注，整个训练就错了。默认 `sentence` 即正确。

> **引擎与坏数据**（data-audit / env-fix-ab A/B 结论）：
> - **选定主引擎 = IndexTTS2**（在 idx1/2/3 上把每个目标多音字都强制读对了：丁→zhēng、万俟卨→mò qí xiè、丧→sàng）。
>   CosyVoice3 只强制中 2/3，且对 idx1 叠字「丁丁」不稳定（出现 dīng dīng），**降级为 fallback**。
> - IndexTTS2 吃 `it2_text` 控制串；但 `it2_text` 有 30 行损坏（`cosy3_text` 均正常）。
>   **走 it2 路径必须用清理版 `data/v19_teacher_text.clean.jsonl`（489 行）**，或先修这 30 行再全量。
>   synth-batch-ref 将用胜出引擎（IndexTTS2）从 clean 源产出全量 wav + manifest。
> - 训练文本恒为 `sentence`，与引擎无关。

---

## 3. 如何启动训练

### 方式 A（推荐）：用本项目启动器 `train_lora.py`

```bat
:: 1) 仅生成 manifest + yaml，并打印启动命令（默认，安全，不真正开训）
C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe D:\AI\Build\多音字\train_lora.py

:: 2) 真正开始训练（长任务，Ctrl+C 会安全存盘到 latest/）
C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe D:\AI\Build\多音字\train_lora.py --run
```

常用参数：

| 参数 | 说明 |
|------|------|
| `--text-field` | 与 wav 配对的文本字段，默认 `sentence`（也可 `cosy3_text` / `it2_text`） |
| `--wav-index-mode` | 无 manifest 时 wav 编号方案：`idx`（用数据 idx 字段）或 `line`（0 基行号） |
| `--model-dir` | 覆盖模型目录 |
| `--epochs` | 训练轮数（换算成 `num_iters`）；默认 10 |
| `--build-only` | 只造 manifest，不写 yaml、不训练 |
| `--run` | 真正拉起训练入口 |

启动器会先造 `lora_train.jsonl` + `lora_config.yaml`，再打印并执行：

```
C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe ^
  D:\AI\Build\VoxCPM2Dist\app\Scripts\training\train_voxcpm_finetune.py ^
  --config_path D:\AI\Build\多音字\lora_config.yaml
```

### 方式 B：直接调用仓库入口（等价于上面的命令）

把 §1.3 的配置写成一个 yaml（参考 `lora_config.yaml` 或仓库现成
`VoxCPM2Dist/app/Scripts/training/voxcpm_finetune_lora.yaml`），然后：

```bat
C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe ^
  D:\AI\Build\VoxCPM2Dist\app\Scripts\training\train_voxcpm_finetune.py ^
  --config_path 你的配置.yaml
```

---

## 4. 推荐超参与理由

| 超参 | 推荐值 | 理由 |
|------|--------|------|
| `lora.enable_lm` | `true` | 多音字修复 = 改「文本→读音」映射，落在 LM |
| `lora.enable_dit` / `enable_proj` | `false` | 不动音色/声学，音色由参考音克隆，与 LoRA 隔离 |
| `lora.r` / `alpha` | `16` / `32`（轻量）或 `32` / `32`（仓库现成值） | 数据仅 520 句、~几十条读音；rank 过大易过拟合。`alpha` 通常 = 2×r 或 = r |
| `learning_rate` | `2e-4`（或仓库用的 `1e-4`） | LoRA 常用 1e-4~3e-4；小数据集偏保守 |
| `batch_size × grad_accum` | `2 × 8 = 16`（有效） | V2 模型 ~4.5GB + 激活，单卡 16GB 显存下稳妥；有效批次 16 对 520 句足够 |
| `epochs` | `25`（≈800 步） | 520 句 / 有效批次 16 ≈ 32.5 步/轮 → 25 轮 ≈ 813 步。保存点每 200 步一份（200/400/600/800） |
| `warmup_steps` | `100` | 余弦调度预热；步数 >> warmup 即可 |
| `max_grad_norm` | `1.0` | 稳定训练，防梯度爆炸 |
| `max_batch_tokens` | `8192` | 过滤异常超长样本，防 OOM；正常短句不受影响 |
| `num_workers` | `2`（RAM 紧改 `1`） | Windows spawn 下每个 worker 是完整 torch 进程 |

> 保存的 LoRA 仅含 `lora_*` 权重（`lora_weights.safetensors`）+ `lora_config.json`。
> **推理时必须用保存的 `lora_config.json` 重建 LoRA**，保证 r/alpha 一致。

---

## 5. 「音色锁定」澄清（重要，用户已确认）

VoxCPM2 是**音频条件**模型。训练时我们用固定参考音（合成音）做 (audio, text) 配对，
**但这不等于把模型永久锁死在该音色**：

- 训练时参考音只是训练信号，LoRA 只改「读音映射」（LM），不改声学渲染器（DiT 关闭）。
- 推理时仍可传入**任意** `reference_wav_path`，参考音决定最终音色 —— LoRA 只是把「读音偏好」
  带过去，音色由推理参考音重新偏置。
- 这是**预期的 voice-cloning / self-distillation** 行为，用户已批准，不是副作用。

---

## 6. 安装 / 环境（基本无需操作）

- 系统 Python 3.12 已含 `voxcpm 2.0.3` + `torch 2.12.1+cu126`，**直接可用**。
- 实测该环境下 `voxcpm`/`voxcpm.training`/`argbind`/`datasets`/`transformers`/`safetensors`/
  `soundfile`/`torchaudio` 均导入正常。
- `accelerate` 未安装，但 `voxcpm.training.Accelerator` 是仓库自带精简版（不依赖 `accelerate` 包），
  训练入口可正常运行。
- `torchcodec` 因缺 ffmpeg 无法加载，但训练入口已用 soundfile 离线补丁绕开，**不影响训练**。
- **不要** `pip install` 任何东西；若训练入口报缺包，先对照上面的导入清单定位，再单独询问。

仅当 VoxCPM2 **完全不存在**时才考虑安装（本次不需要）：
```bat
:: 仅在确证 pip show voxcpm 无结果时
C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe -m pip install voxcpm
:: 并从 https://github.com/OpenBMB/VoxCPM 下载模型权重到本地目录
```
（执行大安装前请先向 team-lead 说明。）

---

## 7. 需验证 / 待确认项（真实开训前必看）

1. **`wavs_manifest.jsonl` 的真实 schema —— ✅ 已与 synth-batch-ref 对齐**
   - 字段：`index`(=v20 idx, 1-based) / `wav`(相对项目根) / `sentence`(训练文本) /
     `model` / `sample_rate` / `cosy3_text` / `it2_text` 等。
   - 文件名 `{idx:04d}.wav`（1-based，每行 idx 唯一）。`train_lora.py` 已按此映射并冒烟通过。
2. **训练文本字段 —— ✅ 已确认 = `sentence`**
   - synth-batch-ref / env-fix-ab 均确认：cosy3→吃 `cosy3_text`、it2→吃 `it2_text`，但这二者只是
     「强制读音控制串」，**不是训练文本**；合成音频仍对应自然中文 `sentence`。脚本默认 `sentence` 正确，
     并已加护栏警告误用控制串字段。
3. **合成引擎选择 —— ✅ 已定 = IndexTTS2（primary），CosyVoice3 = fallback**
   - env-fix-ab 的 A/B（idx1/2/3，拼音核验）结论：IndexTTS2 把每个目标多音字都强制读对
     （丁→zhēng、万俟卨→mò qí xiè、丧→sàng）；CosyVoice3 只中 2/3，且对叠字「丁丁」不稳定，
     降级为 fallback。已通知 synth-batch-ref 用 IndexTTS2 产出全量 wav + manifest。
   - **数据已清洗（v20）**：520 行全量可用，it2 零损坏，cosy3 零不平衡。直接使用 `data/v20_teacher_text.jsonl`。
     （**489 行**）产出，或先修这 30 行再全量。我的脚本消费的 `sentence`/wav 不受影响，
     只是可用样本数变成 489（或修好后 520）。
   - ⚠️ **软门槛（建议，非硬阻塞）**：env-fix-ab 建议正式全量前再做一轮更广 A/B（20-50 idx，
     含那 30 个 flagged it2 行）+ whisper-large / 人工听检（whisper-base 对声调弱）。是否执行由
     team-lead 决定；脚本与数据就绪后，收到引擎产物即可 `--run`。
4. **自举合成的正确性风险**：若 wav 由 VoxCPM2 基础模型自举合成，基础模型若在某句也读错多音字，
   该训练目标即错。建议先小批量试听（参考仓库 `bootstrap_lora_audio.py --preview 20`），
   训练后用 `verify_lora.py` 复测，对仍读错的句单独排除/补录。
5. **显存**：V2 模型 ~4.5GB + 激活；`batch_size=2` 在 16GB 显存应可行。若 OOM，先降 `batch_size=1`、
   再降 `max_batch_tokens`。
6. **epochs 取舍**：v20 用 25 轮（≈800 步），每 200 步存检查点。之前 1180 句用 1000 步，现 520 句按比例调到 800 步。
   `loss/total` 是否收敛、以及 `verify_lora.py` 读音正确率为准回调。
7. **续训**：中断后重跑同一命令会从 `lora_output/latest/` 续训（入口内置信号安全存盘）。

---

## 8. 一句话流程

```
合成 wav（合成 agent）→ wavs/ + wavs_manifest.jsonl
        ↓
train_lora.py  （造 lora_train.jsonl + lora_config.yaml）
        ↓
train_lora.py --run  →  train_voxcpm_finetune.py --config_path lora_config.yaml
        ↓
lora_output/lora_weights.safetensors + lora_config.json
        ↓
推理时加载 LoRA + 任意参考音，输出正确读音、目标音色
```
