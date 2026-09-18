<!-- 入库注记：本文件原位于 D:\AI\Build\多音字\data\，随 VoxCPM2Dist v5.3.5 入库到 app/Scripts/training/polyphone_corpus/。文中示例的绝对路径（D:\AI\Build\多音字\...、C:\Users\000\...）请按你实际位置替换；teacher 引擎 IndexTTS2 需另行获取，不在本仓库。 -->

# 多音字 LoRA 训练语料 —— 完整经验手册（v1→v23 全覆盖 · v23 最终验证）

> 项目：VoxCPM2 TTS 多音字 LoRA 训练
> 日期：2026-07-18（语料 v1→v19）→ 2026-07-19（v20 定稿 + 音频合成 + 训练就绪）→ 2026-07-25（v23 最终验证：LoRA 仍无效，项目结论定稿）
> 版本：v23 最终验证版（合并此前所有经验，**本文是唯一保留的经验文件**）
> 自包含：从字典 → 语料筛选 → 音频合成 → 训练管线，**全流程可独立复用**。
> 旧文件（已过时）：`backups/2026-07-18_corpus885/README_筛选过程经验.md`

---

# 上部：文本侧（v1 → v19，7月18日）

---

## 一、目标与定位

为 VoxCPM2 TTS 构建「多音字正确读音」LoRA 的**训练文本**语料：每行一个含目标多音字的短句，语境锁死该字的正确读音。后续再配「读音正确」的音频。

VoxCPM2 不做显式多音字处理（官方无内置 G2P 转换通道，但**原生支持音素输入**——`{ni3}{hao3}` 带声调拼音串，须 `normalize=False`；本分发 v5.4 起提供 `app/Scripts/g2p_phoneme.py` 基于 pypinyin-g2pW 的离线 G2P 模块，可精确指定读音）。消歧默认靠 TSLM 隐式从 `(文本, 语音)` 配对中学。所以"教它对"的唯一手段 = 用**正确读音的 `(音频, 文本)`** 训 LoRA（`enable_lm=True`）。

**最关键的一条定位**：这不是"字典完整度"任务，而是 **base 模型会读错的多音字纠错集**。base 本就读对的现代常读音收进去是噪声、还会把语料冲大。因此筛选的唯一准绳是——**base 会不会读错**，而不是"这个字有几种读音"。

---

## 二、整体流水线（从字典到 885 原始语料）

| 步骤 | 做了什么 | 产出 | 关键文件 |
|---|---|---|---|
| 1 取源 | 从 chinese-dictionary 的 `polyphone.json`（2495 条）筛 `frequency≤1` | **456 个高频多音字** | `polyphone_freq_le1.json` |
| 2 抓例句 | 抓 `char_detail.json` 的 example 锁「正确上下文读音」 | **705 个「字×读音」候选对** | `step3_candidates.jsonl` |
| 3 LLM 扩句 | 逐对由 LLM 扩写成自然短句 | 738 条 → **738 条覆盖 456 字 100%** | `batch_01..14.jsonl` |
| 4 诗词补强 | 补叶音/平水韵读法（斜xiá/骑jì/胜shēng…） | **87 条** | `batch_15.jsonl` |
| 5 专名/通假补强 | 补字典内专名/通假 + 非字典专名/通假 | **+60 条** | `batch_16/17.jsonl` |
| 6 审计 | 自建 114 条「古诗文常见异读」清单查覆盖 | **100%(108/108)** | `step3_overview.md` |
| 7 裁剪 | 分桶审计，保留全部 885 | **885 行 / 493 字 / 818 对** | `pruning_analysis.md` |

---

## 二之一、参考数据源与链接

### A. 源字典：`chinese-dictionary`
- 仓库：<https://github.com/mapull/chinese-dictionary>（推荐 fork）
- 数据：`character/polyphone.json`（2495 条）
- 字段：`{"index":int,"char":str,"pinyin":[str...],"frequency":int,"strokes":int}`
- ⚠️ `frequency` 只有 0~3 四档且不可靠，仅适合粗筛，不能当排序依据

### B. 排序依据：《通用规范汉字表》（2013，教育部/国家语委，8105 字）
- 仓库：<https://github.com/shengdoushi/common-standard-chinese-characters-table>
- 一级 3500 → 二级 3000 → 三级 1605 → 未收录（极生僻）
- 下载（走代理须用 PowerShell `irm`，不是 `curl.exe`）：
  ```powershell
  $base='https://raw.githubusercontent.com/shengdoushi/common-standard-chinese-characters-table/master'
  irm -Uri "$base/level-1.txt" -OutFile "gsc_level1.txt"
  ```

---

## 三、筛选依据（核心原则）

### 3.1 唯一准绳：base 会不会读错
- **训什么**：base **会读错**的音 —— 叶音、专名/地名古音、诗词破读、通假借音、易混异读
- **不训什么**：base **本就读对**的现代常读音——收进去稀释难样本
- 判定方法：**推理实测**。用官方 demo 让 base 念「石径斜」（误读 xié，应为 xiá）、「华山」（误读 huá，应为 huà）→ 直接证明 base 误读叶音+专名

### 3.2 9 轮听测校准
- 累计听测 **459 句**，误读率约 6.7%
- 被砍的 248 句"可疑常见音"全部经耳朵验证

### 3.3 通假 ≠ "读错的音"
通假是古人**借字形**，不是该字有另一种读音。纯通假句录成正常本音即无害陪衬，保留。

---

## 四、9 轮听测裁剪过程

| 版本 | 行数 | 关键动作 |
|---|---|---|
| 885 原始 | 885 | 全量 |
| 799 | 799 | 删 17 纯通假 + 69 冗余常音 |
| v2 | 768 | 第1轮听测，砍 31 句读对 |
| v3 | 721 | 第2-3轮 |
| v4 | 674 | 第4轮 |
| v5-v10 | 375 | 第5-8轮 + 砍未测常见音 |
| **v11** | **380** | 第9轮定稿（听测口径） |
| **v12** | **379** | 审计修正 17 处标音/用字错 |
| **v13** | **399** | +20 条姓/地名 |
| **v14** | **432** | +33 条姓/地名/复姓；修正过 guō 假覆盖 |
| **v15** | **483** | +51 条长尾生僻；修正哈 hà→hǎ |
| **v16** | **498** | +15 条古诗词古文；pruning 误删追回 |
| **v17** | **499** | +1 解 jiè（解元） |
| **v18** | **520** | 修砌 qiè→qì；+21 条真缺口（豆包审查） |
| **v19** | **520** | 同 v18，按字频重排 |

---

## 五、审计发现盲区 → v12 修正 17 处

9 轮听测只验证了"base 会不会读错"，**不是验证我们自己标的拼音对不对**。LLM 扩写的文言/诗词句易混入同音错字+错拼音。

**17 处确凿错误：**
- 7 处句中用字错（发→废、共→拱、宁→贮、卷→蜷…）
- 10 处拼音标错（风fèng→fěng、处chú→chǔ、叶shè→yè…）
- 2 处 word 字段抽错 + 1 处完全重复

> ⚠️ 真标音 bug 累计 22 处。验证口径要诚实：听测验证 base，字典交叉比对验证标注。

---

## 六~九、v13~v18 补全（姓/地名/诗词/豆包审查）

- **v13**：+20 条姓/地名（员yùn/句gōu/缪miào/翟dí…）
- **v14**：+33 条（那nā/能nài/郇huán…）+ 4 复姓；修正过 guō 假覆盖
- **v15**：+51 条长尾生僻姓/地名
- **v16**：+15 条古诗词古文破读（荷hè/饮yìn/说yuè/丁zhēng…）
- **v17**：补解 jiè（解元）
- **v18**：豆包审查 → 修砌 qiè→qì + 21 条真缺口

---

## 十、按字频重排序（v19）

用《通用规范汉字表》三级分级排序：tier1(最常用)→tier2→tier3→tier4(极生僻)。

---

## 十一、关键经验（v1→v19 文本侧）

1. **JSONL 必须逐行写**，不能 `json.dump(list)`
2. **源字典 pinyin 数组不完整**（漏口语音），补语料要结合常识
3. **叶音/专名是 TTS 最易读错、也是语料价值核心**
4. **base 会不会读不能靠猜，要实测**
5. **拼音归一化绝不能抹声调**（华huā vs 华huà）
6. **「假覆盖」bug**：(字,拼音)在库 ≠ 句子语境对。必须逐句确认语义匹配
7. **硬错要敢推翻 LLM 手写**（砌 qiè）
8. **验证口径要诚实**：听测验证 base，字典交叉比对验证标注
9. **长任务保留完整版本链**：v1→v19 全程可追溯

---

## 十五、彻底重审（拉上游完整 polyphone.json）

- 旧审计用 456 字残破子集 → **伪校验**（子集漏字+自带错误）
- 拉上游 2495 字完整字典 `data/polyphone_full.json` 重审
- 揪出 3 个真错误：思 sì→sī、解 hài→xiè、行 hèng→xíng
- 人工复审再补 2 处假覆盖：旁→傍（傍晚）、莫→暮（暮色）
- 蠡 luó→lǐ；补蚌bèng + 馏liù → 回到 520
- v19 文本侧最终定稿：**520 行，零无效拼音**

---

# 下部：v20 定稿 + 音频合成 + 训练就绪（7月19日）

---

## 十六、v20 全表数据清理

v19 文本侧"定稿"只保证读音标注正确，但**未逐句检查句子本身是否可被 TTS 正常合成**。v20 补上了这一步。

### 16.1 发现的错误类型与范例

| 类型 | 数量 | 示例 | 根因 |
|------|------|------|------|
| **无效音替代** | 3 行 | 份bīn（份无此音）、黑hè（姓氏标准 hēi）、重复行 | 张冠李戴 |
| **括号/latin 嵌入** | 30 行 | 「读zòng」「读作jī」| 句内嵌拼音注释，IndexTTS2 BPE 不认 |
| **『』括号** | 8 行 | 『合从』『夫战』| IndexTTS2 BPE 无法编码，输出异常 |
| **通假同句异读** | 10 行 | 从通纵→两个从读不同音 | 通假规则：A通B 则全句 A 读 B 音 |
| **多音字读音错配** | 若干 | 数shǔ数shù、落là/落luò | 同一字在句中不同位置读音须精确区分 |
| **动词重叠轻声** | 3 行 | 看看→kàn kan（后字轻声）| 语流音变未标 |
| **亲属称谓轻声** | 8 行 | 爷爷→yé ye（后字轻声）| 同上 |
| **A一A 模式轻声** | 1 行 | 凉一凉→liàng yi liang | 重叠中间一字+后字轻读 |
| **读音知识性错误** | 4 行 | 矿藏→cáng（非zàng）| 字典查证纠错 |

### 16.2 v20 操作清单

**删除（3 行）：**
- idx 25：份bīn（无效音，份只有 fèn 一读）
- idx 386：完全重复行
- idx 415：黑hè（姓氏标准读 hēi，hè 只在极边缘用法）

**替换（3 行，原易句换难多音字）：**
- idx 4：中zhòng → 处chǔ「他们相处多年，从未红过脸。」
- idx 5：为wèi → 强qiǎng「他勉强接受了朋友的好意。」
- idx 30：作zuò → 重chóng「这个方案需要重新评估一遍。」

**追加（3 行，补回空位）：**
- idx 25：亲qìng「两家结为亲家后，逢年过节都互相走动。」
- idx 386：据jū「他最近手头拮据，日子过得精打细算。」
- idx 415：提dī「夜里独自走夜路要多加提防，安全第一。」

**括号/latin 清理（30 行）：**
- 全部去掉句内『』「」（）括号和 latin 拼音注释
- 句子改写为纯中文（如「读zòng」→ 句子改为自然收尾，不再嵌拼音）

**通假同读（10 行）：**
- 从zòng/夫fú/邪yé/藉jiè/蠡lǐ 等全句统一读音

**轻声修正（13 行）：**
- 亲属称谓后字 tone5：爷爷/奶奶/姥姥/爸爸/妈妈（8句）
- 动词重叠后字 tone5：看看/画画/等等（3句）
- A一A 模式：凉一凉→liàng yi liang（1句）
- 等等→deng3 deng5（1句）

**读音纠错（4 行）：**
- idx 314：落→là（忘记带走），非 luò
- idx 325：矿藏→cáng（蕴藏义），非 zàng
- idx 448：澹台→tán tái（复姓），非 dàn
- idx 501：陂陀→pō tuó，非 bēi

**读音精确匹配：**
- idx 87：各→自各儿(ge3) 各(ge4)干各(ge4)
- idx 205：数→学数(shu3)数(shu4)，从一数(shu3)到十

### 16.3 v20 最终验证

- 520 行，idx 1~520 连续
- 全表扫描：零括号、零 latin 嵌入、it2 全合法、音节数对齐
- 两个 JSONL 文件（`v20_teacher_text.jsonl` / `.clean.jsonl`）内容一致

---

## 十七、TTS 引擎选型（A/B 实测）

### 17.1 候选引擎

| 引擎 | 强制方式 | 优势 | 劣势 |
|------|---------|------|------|
| CosyVoice3 | 内联音素 `[zh][ēng]` | 官方支持 | 环境脆弱(torch2.3锁死)、重复字强制不全 |
| IndexTTS2 | 内联拼音 `zheng1` | 强制干净、环境兼容 | BPE 不认『』等特殊符号 |

### 17.2 实测结果（3 句对比）

| 句子 | IT2 | CV3 |
|------|-----|-----|
| 伐木丁丁（丁→zhēng） | 双丁均 zhēng ✅ | 一个 zhēng、一个 dīng ⚠️ |
| 万俟卨（万→mò） | 全对 ✅ | 全对 ✅ |
| 丧→sàng | 对 ✅ | 对 ✅ |

### 17.3 结论

**选 IndexTTS2 为 teacher。** CV3 对重复多音字强制不完全（标注正确但模型内部覆盖），且 torch 2.3 锁死与本环境 2.12 不兼容。

### 17.4 IndexTTS2 部署实录（安装 / 模型 / 启动 / 编程调用 / Windows 坑）

> 本项目的 IndexTTS2 是「代码与模型分离」布局：代码在 `IndexTTS2_code/`、权重在 `IndexTTS2/checkpoints/`、参考音色在 `assets/ref_prompt_it2.wav`。下面是从零部署到能合成的真实步骤（已对照 `IndexTTS2_code/pyproject.toml`、`README.md`、`webui.py`、`indextts/infer.py` 核对）。

**目录布局**
```
多音字/
├─ IndexTTS2_code/        # 代码仓库：webui.py / indextts/ 包 / pyproject.toml
├─ IndexTTS2/checkpoints/ # 模型权重：gpt.pth s2mel.pth config.yaml bpe.model
│                         #           feat1.pt feat2.pt wav2vec2bert_stats.pt
│                         #           qwen0.6bemo4-merge/ hf_cache/
└─ assets/ref_prompt_it2.wav   # 固定参考音色（克隆用）
```

**① 安装依赖（官方唯一支持 `uv`，conda/pip 不保证版本）**
- 要求 **Python `>=3.10,<3.12`**（即 3.10 / 3.11）；torch **`2.8.*`**。
- 用 `uv sync` 会在 `IndexTTS2_code/` 内自动建 `.venv` 并锁定 Python + 全部依赖：

```bash
pip install -U uv
cd IndexTTS2_code
uv sync --all-extras                       # 含 webui(gradio 5.45)、fp16、torch_compile
# 国内镜像（二选一）：
uv sync --all-extras --default-index "https://mirrors.aliyun.com/pypi/simple"
uv sync --all-extras --default-index "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple"
```
- `--all-extras` 含 WebUI；Windows 上 DeepSpeed 难装，可去掉 `--all-extras` 只加需要的 extra（`--extra webui`）。

**② 下载模型权重**（本项目已就位，放 `IndexTTS2/checkpoints/`，与代码分离）
```bash
# HuggingFace
uv tool install "huggingface-hub[cli,hf_xet]"
hf download IndexTeam/IndexTTS-2 --local-dir=IndexTTS2/checkpoints
# 或 ModelScope
modelscope download --model IndexTeam/IndexTTS-2 --local_dir IndexTTS2/checkpoints
```
- 首次运行会自动补下小模型到 `checkpoints/hf_cache/`；HF 慢先 `export HF_ENDPOINT="https://hf-mirror.com"`。
- 必需文件（缺失时 WebUI 启动会提示并自动补）：`config.yaml` `gpt.pth` `s2mel.pth` `bpe.model` `feat1.pt` `feat2.pt` `wav2vec2bert_stats.pt` `qwen0.6bemo4-merge/`。

**③ 启动 WebUI**
```bash
cd IndexTTS2_code
uv run webui.py                                    # 默认 http://127.0.0.1:7860
uv run webui.py --model_dir "D:\AI\Build\多音字\IndexTTS2\checkpoints"   # 用分离的权重目录
uv run webui.py --fp16 --accel --torch_compile     # 加速（启动即定，WebUI 内不可切）
```
- 关键参数：`--port`(默认 7860) `--host`(默认 0.0.0.0) `--model_dir`(默认 `./checkpoints`) `--fp16` `--accel` `--torch_compile` `--deepspeed`。
- WebUI 里上传参考音频（音色）+ 文本（支持 `zheng1 zheng1` 内联拼音强制读音）即可合成。

**④ 编程式调用（本项目 `synth_batch.py` 就是这么驱动的）**
```python
import sys
sys.path.insert(0, r"D:\AI\Build\多音字\IndexTTS2_code")   # 让 import indextts 解析
from indextts.infer import IndexTTS

tts = IndexTTS(
    cfg_path=r"D:\AI\Build\多音字\IndexTTS2\checkpoints\config.yaml",
    model_dir=r"D:\AI\Build\多音字\IndexTTS2\checkpoints",
    use_fp16=True, device="cuda",
)
tts.infer(
    audio_prompt=r"D:\AI\Build\多音字\assets\ref_prompt_it2.wav",  # 固定参考音色
    text="yin2 hang2 de ren hen duo",   # it2_text：空格分词 + 数字调拼音
    output_path=r"D:\AI\Build\多音字\wavs\0001.wav",
)
```
- API 签名：`IndexTTS(cfg_path, model_dir, use_fp16, device)` → `infer(audio_prompt, text, output_path, max_text_tokens_per_segment=120, ...)`。
- 新一代接口在 `indextts.infer_v2.IndexTTS2`（构造签名相同，`infer(spk_audio_prompt, text, output_path, ...)`）。

**⑤ ⚠️ Windows 三个必踩的坑（本项目环境实测）**
1. **torchaudio 路由坑**：本机 torch 2.12 的 torchaudio 走 torchcodec（缺 ffmpeg DLL）且丢了 `backend=` API。必须在 `import indextts` **之前** 把 `torchaudio.load/save/info` monkeypatch 到 soundfile（见 `synth_batch.py` 的 `torchaudio_shim`）。
2. **SentencePiece 非 ASCII 路径**：0.2.1 的 C++ 层用窄 `fopen`，打不开含中文的 `多音字` 父目录里的 `.model`。改成读字节：`SentencePieceProcessor.LoadFromFile` → 用 `LoadFromSerializedProto(open(path,'rb').read())`。
3. **Python 版本**：官方要求 `<3.12`；本机实际用 Python 3.12 + 上述 shim 跑通是特例。**复现请优先用 Python 3.11 + 官方 `uv sync`**，最稳，能避开上面两个 shim。

---

## 十八、520 句音频合成

### 18.1 合成配置

- 引擎：IndexTTS2
- 脚本：`synth_batch.py --model it2`
- 输入：`data/v20_teacher_text.jsonl`（使用 it2_text 字段）
- 输出：`wavs/0001.wav` ~ `wavs/0520.wav`
- 格式：22050Hz 单声道
- 参考音频：`ref_prompt_it2.wav`

### 18.2 分批策略

每批 20 句，断点续跑（已存在的 wav 自动跳过）。环境按 §17.4 用 `uv sync` 装好（`.venv` 在 `IndexTTS2_code/`）后，从项目根目录调用：

```powershell
cd D:\AI\Build\多音字
uv run --project IndexTTS2_code python synth_batch.py `
  --model it2 `
  --jsonl data/v20_teacher_text.jsonl `
  --start 0 --limit 20
```

> 也可直接用 venv 解释器：`& "D:\AI\Build\多音字\IndexTTS2_code\.venv\Scripts\python.exe" synth_batch.py ...`。
> 若沿用旧的 Python 3.12 + torch 2.12 自建环境（带 §17.4⑤ 的 torchaudio/sentencepiece 补丁），原 `Python312\python.exe synth_batch.py` 命令仍可用。

`--start` 是 0 基行号（不是 idx），每批 +20。共 26 批，约 2~3 小时。

### 18.3 合成质量

- 520/520 完整
- 时长：1.6~6.4s，平均 3.2s
- 采样率：全部 22050Hz
- 静音/截断/损坏：0
- ASR 抽查 20 句：关键多音字全部读出正确读音

### 18.4 踩坑记录

1. **PowerShell 需要 `&`**：路径含中文空格时必须 `& "path" args`
2. **`--start` 是行号不是 idx**：v20 idx=1 对应行号 0
3. **文件被占用删不掉**：等合成进程退出再用 .NET Delete 或 rm
4. **IndexTTS2 BPE 不认『』「」（）**：必须改句子清掉所有特殊括号
5. **latin 嵌入导致 it2 字母泄漏**：「读zòng」→ it2 变成「z ò n g」— v20_build.py 的 regen_it2 修复

---

## 十九、训练管线

### 19.1 文件关系

```
wavs/0001~0520.wav  ─┐
v20_teacher_text.jsonl─┤ → train_lora.py → lora_train.jsonl
                        │                 → lora_config.yaml
                        │                 → 调用 VoxCPM2Dist 训练入口
```

### 19.2 命令

```powershell
# 步骤1：生成训练清单和配置（不训练）
& "C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe" `
  "D:\AI\Build\多音字\train_lora.py"

# 步骤2：开始训练
& "C:\Users\000\AppData\Local\Programs\Python\Python312\python.exe" `
  "D:\AI\Build\多音字\train_lora.py" --run
```

### 19.3 训练超参（v19 初训实际值；v23 合并重训见 §23）

| 参数 | 值 | 说明 |
|------|-----|------|
| 总步数 | 1200（18 轮） | 18 轮 × 520 句 / 有效批次 8（实测跑值，远超甜区） |
| 检查点 | 每 200 步 | 200/400/600/800/1000/1200 各一份 |
| LoRA r | 16 | rank |
| alpha | 32 | 缩放，alpha/r = 2 |
| enable_lm | true | 只改语音模型（读音映射） |
| enable_dit | false | ⚠️ 开了会改音色，见 §23.4 |
| enable_proj | false | 不动投影层 |
| lr | 2e-4 | 学习率（warmup 100 步后余弦衰减） |
| batch × accum | 1 × 8 | 有效批次 = 8 |
| dropout | 0.0 | 520 句数据少，不丢弃 |

> ⚠️ 上表是 v19（520 句）初训的实际值。后续 v23 合并重训用 **2031 句 / 12 轮 / 3047 步**，超参与实战结论见 **§23**。

### 19.4 测试用文件

`data/v20_sentences.txt`：520 行纯句子（`0001、句子` 格式），训练完后用来测试模型读音纠正效果。

---

## 二十、合并经验教训（全流程）

### 20.1 数据层面（v1→v20 全程）

1. **多音字字典 ≠ 全部汉字**：2495 条只覆盖真正多音字。罕见姓/地名/通假字不在其中属正常。
2. **通假规则：A通B → 全句 A 读 B 的音**。同一语义下所有 A 统一读音。
3. **括号和 latin 注释是 TTS 训练毒药**：BPE 不认、latin 逐字母拆分。句子必须纯中文。
4. **轻声不可忽略**：亲属称谓后字、动词重叠后字、A一A 模式——需正确标 tone5。
5. **读音需要字典验证**：矿藏 cáng、澹台 tán、陂陀 pō——不能凭感觉。
6. **「假覆盖」模式**：(字,拼音)在库 ≠ 句子语境对。必须逐句确认语义匹配。
7. **用子集字典做交叉校验是伪校验**：子集自有错误会掩盖语料错。必须用完整权威字典。
8. **文本侧定稿 ≠ 数据定稿**：还需要过一遍"能否被 TTS 正常合成"的坎。

### 20.2 工程层面

9. **A/B 实测比纸上推演可靠**：CV3 理论支持强制读音，实际对重复字不完整。
10. **断点续跑设计必须**：520 句合成不可能不中断，`--start/--limit` + skip-existing 是关键。
11. **PowerShell 语法**：路径含中文空格加 `&`；`--start` 是 0 基行号。
12. **修改→删旧 wav→重新合成** 是标准修复三步，每次必走。
13. **验证口径要诚实**：听测验证 base 会不会读错，字典交叉比对验证标注正确性——两者是不同的事。
14. **长任务保留完整版本链 + 完整经验文档**。
15. **落选的引擎及时清理**：CosyVoice3 环境脆弱、模型占 13GB，A/B 确认不采用后应删除源码和模型、仅保留经验在文档中。

---

## 二十一、项目清理——退役 CosyVoice（释放 ~13GB）

### 21.1 为什么删

CosyVoice3 A/B 实测已确认不如 IndexTTS2（重复字强制不完整），且环境依赖脆弱（torch 2.3 与本机 2.12 不兼容）。**保留它的代码和模型没有实际用途，反而占用大量磁盘。**

### 21.2 应删除的目录/文件

```
D:\AI\Build\多音字\CosyVoice\                          ← 源码 (9MB)
D:\AI\Build\多音字\models\Fun-CosyVoice3-0.5B\          ← CV3 模型 (9.1GB)
D:\AI\Build\多音字\downloads\CosyVoice2-0.5B\            ← CV2 模型 (3.8GB)
D:\AI\Build\多音字\downloads\_llm_test.bin               ← 临时测试
D:\AI\Build\多音字\downloads\torch-2.6.0+cu126-*.whl     ← 旧 torch
D:\AI\Build\多音字\models\*.log                          ← 旧日志
D:\AI\Build\多音字\wavs_manifest.jsonl                   ← 临时清单
D:\AI\Build\多音字\nul                                    ← Windows 保留名空文件
```

### 21.3 保留的经验

CosyVoice3 的安装、配置、强制读音方式、A/B 实测结论均已记录在本文 §17。未来如需重新评估其他引擎，可参考此经验直接跳至 A/B 实测阶段，无需保留环境和模型。

### 21.4 复现指引（如果未来要用 CosyVoice3）

1. 模型：`Fun-CosyVoice3-0.5B`（从 ModelScope/HuggingFace 下载）
2. 安装：`pip install cosyvoice` + 配置 torch 2.3.1 / numpy 1.26.4
3. 强制读音：文本内联 `[zh][ēng]`，`prompt_text` 前缀 `<|endofprompt|>`
4. 已知缺陷：重复多音字强制不完整
5. 参考脚本结构：本文 §17

---

## 二十二、v20 最终文件清单

### 数据
| 文件 | 说明 |
|------|------|
| `data/v20_teacher_text.jsonl` | v20 完整数据（520 行，全部字段） |
| `data/v20_teacher_text.clean.jsonl` | 同上（v20 已全清洗，两者一致） |
| `data/v20_sentences.txt` | 纯句子版（`0001、句子`），训练后测试用 |
| `data/polyphone_full.json` | 权威多音字字典（2495 条） |
| `wavs/0001~0520.wav` | 520 句 IndexTTS2 合成音频 |

### 工具脚本
| 文件 | 说明 |
|------|------|
| `convert_polyphone.py` | 音素/拼音转换：`build_texts(sentence, pinyin_list)` → it2_text / cosy3_text |
| `v19_to_pinyin.py` | v19 拼音字段生成 |
| `v20_build.py` | v19→v20 构建脚本（可复现） |
| `synth_batch.py` | IndexTTS2 批量合成（断点续跑） |
| `train_lora.py` | LoRA 训练启动器（glue → VoxCPM2 入口） |
| `torchaudio_shim.py` | 环境修复 shim |
| `audit_dataset.py` | 数据集审计 |
| `verify_asr.py` / `asr_eval.py` | ASR 验证 |
| **`diff_web_pinyin.py`** | **★ 词典标注核心：phrases_dict 默认注音 + 主动 OVERLAY + 网页标注解析比对** |
| **`build_v23.py`** | **★ v23 构建：sentence→clean→ref_pinyin(OVERLAY 强制)→it2_text，含断言闸门** |
| **`lint_overlay.py`** | **★ OVERLAY 自检：死规则/非法读音/冗余/覆盖风险/重复，CI 可接** |
| **`verify_dict_sources.py`** | 验证 phrases_dict 默认是否够用、社区词典是否冗余 |
| **`test_pypinyin_dict.py`** | 对比默认 vs 加载社区词典的注音差异（minority 读法验证） |
| **`gen_diag_testset.py`** | 生成独立诊断基准集 `data/diag_benchmark.json`（与训练集零重叠） |
| **`run_diag_infer.py`** | 跑 base / 指定 LoRA 检查点的推理快照 |
| **`diag_listen.py` / `build_listen_html.py`** | 生成可听网页，人工判音 |
| **`merge_v19_v23.py`** | 合并 520 v19 + 1511 v23 → 2031 句 merged_manifest |
| **`append_hao_sentences.py`** | 补 好=hào 的 10 句 minority 样本 |
| `monitor_progress.py` | 训练进度监控（每 10 分钟写 progress_10min.log） |
| `LORA_TRAINING.md` | 训练文档 |

### 引擎
| 目录 | 说明 |
|------|------|
| `IndexTTS2/` + `IndexTTS2_code/` | **主力 TTS 引擎**（已确认 teacher） |
| `CosyVoice/` | ❌ 已删除（落选，经验见 §17） |

### 经验文档
| 文件 | 说明 |
|------|------|
| `data/README_v23_完整经验.md` | **本文**——唯一保留的完整经验文件 |
| `backups/` | 历史版本备份（已过时，仅存档） |
| `.workbuddy/memory/` | 项目工作记忆 |

---

## 二十三、训练实战经验（v19 初训 → v23 合并重训，7月20–21日）

> 本节沉淀 v19 初训的听测结论、失败根因、以及 v23 补数据合并重训的整套经验。
> 这是从"语料正确"到"模型真的读对"的关键一跃——此前 §19 只有管线，没有实战结论。

### 23.1 v19 初训设置与结果

| 项 | 值 |
|---|---|
| 数据 | 520 句（v19 文本侧定稿） |
| 轮数 / 步数 | 18 轮 / 1200 步（远超甜区） |
| 有效批次 | 8（BATCH_SIZE=1 × GRAD_ACCUM_STEPS=8） |
| rank / alpha | 16 / 32 |
| enable_lm / enable_dit / enable_proj | True / False / False |
| lr | 2e-4（warmup 100 步后余弦衰减） |
| loss/diff 终值 | 平台 ~0.60 |

### 23.2 听测定稿（哪些字对、哪些错）

- ✅ 全对：声韵母类（乐/重/血/调/弹/长）+ 声调类（好/发/得/应/背/转/舍）
- ❌ 失败（有无 LoRA 都错）：**行 / 还 / 量 / 假 / 兴** 5 字（同声韵、异调）
- ⚠️ 后修正：好字其实 BEFORE 独立集也错（base 读 hǎo 非 hào），v23 已补 10 句 hào 样本

### 23.3 失败根因 = minority 读法数据缺失（非容量 / 非需开 dit）

汉字级核查坐实，训练集里这些字的 minority 读法样本缺失或标错：
- **还 huán**（还钱/归还）：训练集 **0 句**真实 huán 语境；"还乡"本应 huán 却错标 hái
- **量 liáng**（量一量/测量）：完全 **0 句**
- **假 jià**（放假/假期）：仅"暑假"1 句 jià，缺"放假/请假"
- **行 háng**（银行/行业）：仅少数，且 xíng 远多于 háng（base 先验偏 xíng）
- **兴 xīng**（兴奋）：0 句"兴奋"，仅高兴/兴趣/兴焉/兴起
- 对照：好/发/得/应/背/转/舍 两读在训练集均有充分覆盖 → 所以这些对了
- 结论：**rank16 容量足够学声调**（好/发等证明），失败纯粹是"模型从未见过这些字的 minority 读法信号"
  - ⚠️ **此结论已被 §23.13 的 v23 终验推翻**：补数据 + rank16 仍无效，根因非单纯数据缺失。

### 23.4 VoxCPM2 架构要点（必知）

- 架构：LM(base_lm+residual_lm) → 投影 mu → DiT 据 mu+cond 扩散渲染 feat → AudioVAE 解码
- **音色无独立旁路**：音色经「ref prefix → LM → lm_to_dit_proj → mu」融进 DiT 条件，DiT 无 speaker embedding 输入
- **⚠️ enable_dit=True 会改音色**：LoRA 作用在 DiT 的 q/k/v/o_proj attention，而这些处理含音色的 mu → 改 DiT 必动音色，无隔离。`train_lora.py` 注释明写"开了会改音色"，**不要直接开**

### 23.5 声调纠正正确路线（保音色优先）

1. **首选（⚠️ 经 §23.13 v23 终验未达预期）**：补全 minority 读法训练数据（生成含缺失读法句子 + it2 强制拼音 + 重合成 + 重训，rank 保持 16 即可，容量已证够用）——v23 已走此路仍失败，此路线单独不够。
2. 次选：enable_proj=True 仅 lm_to_dit_proj + res_to_dit_proj（绕开可能含音色编码的 enc_to_lm_proj）
3. 最后才考虑 enable_dit=True，且训完必做同参考音音色对照
- **改 rank/dit/proj 均须从 0 重训**（形状变，不能续）；改 acc/lr 可从检查点续

### 23.6 v23 补数据 + 合并重训（本轮，运行中）

- v23 = 1511 句 minority 补强数据（含 还 huán / 量 liáng / 假 jià 放假 / 更多 háng / 兴 xīng 兴奋 / 好 hào 等）
- 合并 520 v19 + 1511 v23 = **2031 句**（`data/merged_manifest.jsonl`，缺音频 0 / 跨集重复 2）
- 本轮设置：EPOCHS=12 → num_iters=3047（每轮 ≈254 步）；rank16/alpha32/enable_lm=True/enable_dit=False；lr 2e-4 + warmup 100；SAVE_INTERVAL=200（15 个检查点 200..3000）
- 起训 2026-07-21 21:50，PID 28984，ETA ~27h（33s/step）

### 23.7 💡 经验：比"轮数"不比"步数"

数据集 520→2031（×3.9），每轮步数随之变长。**本次 1200 步 = 4.7 轮，绝非 v19 那次 1200 步 = 18 轮**。跨次比较 LoRA 进度必须用 **epoch** 作单位，裸步数在新旧数据集间不可比。

### 23.8 💡 经验：12 轮够不够 → 够且偏稳

总样本曝光 = 2031×12 = **24372 次**，是 v19（520×18=9360）的 **2.6 倍**。失败根因是 minority 数据缺失，非轮数/容量不足 → 瓶颈在数据覆盖；加轮数到 18+ 只增过拟合风险、不救那几个字。改 EPOCHS 须从头重训。

### 23.9 💡 经验：甜区与抽点时间线（~39s/step，起点 21:50/7-21）

> ⚠️ **实测步速是 ~38–39s/step，不是 33s/step**。33s 是早期监控误报（时间戳缺年份→dt 差 126 年→永远回退兜底值），已于 7-22 早修正 `monitor_progress.py`。以下时间按 ~39s/step 重算（与实测 step 600@04:14、step 950@08:04 吻合）。

| 步数 | 轮数 | 预计时间 | 预期 |
|---|---|---|---|
| 200 | 0.79 | ~00:07 | ❌ 太早（连一遍都没学完） |
| 600 | 2.36 | ~7-22 04:20 | 🟢 首个值得听 |
| 1000 | 3.94 | ~08:38 | 🟢 甜区 |
| 1200 | 4.7 | ~10:50 | 🟢 **甜区，押最佳** |
| 1400 | 5.5 | ~13:02 | 🟢 甜区 |
| 1600 | 6.3 | ~7-22 15:08 | 🟡 上沿，再往后可能过拟合 |
| 3047 | 12 | ~7-23 06:50 | 跑满 |

结论：**好效果约第 1000–1400 步（7-22 上午 08:38 – 下午 13:00）出现**，押 1200/1400。

### 23.10 💡 经验：听测是唯一裁判

loss/diff 平台对"声调纠正对没对"几乎不敏感（声调是超音段/F0，loss 主要吃声学重建）。必须拿目标字（行/还/量/假/兴/好）做人工 A/B 听测挑最佳 checkpoint，**不能靠 loss 数字判**。

### 23.11 ⚠️ 若某字仍错（哪怕 1600 步）

= 该字 minority 样本在数据里仍缺/错（已知坑），属**数据缺口非训练时长** → 正确修复是再补 v23 类样本重训，不是加轮数/开 dit/改 rank。

### 23.12 监控保障（可选）

`TrainV23Monitor` 计划任务每 10 分钟写 `progress_10min.log`（含步数/loss/lr/自测 ETA）。
- 坑1：训练日志 `train_v23.log` 是 **UTF-16 LE**（Tee-Object 默认），Python 读须 BOM 探测，否则静默丢字节。
- 坑2：监控进度行时间戳**须带年份**，否则 `strptime` 默认 1900 年与真实 `now` 差 126 年 → ETA 炸成几十亿小时。
- 查进度：`Get-Content 'D:\AI\Build\多音字\progress_10min.log' -Tail 10`

### 23.13 ⚠️ v23 合并重训终验（2026-07-23 跑满，实测失败）

- **跑满**：3047 步 / 12 轮于 2026-07-23 06:50 完成，15 个检查点（200..3000）。
- **终验方式**：用户亲耳 A/B 听测训练原句 + 12 句固定诊断集（base vs LoRA 检查点）。
- **结果**：目标字 **行 / 还 / 量 / 假 / 兴 / 好 全部仍读错**，与 base 听感**零差异**；12 句多音字无任何实际变化。
- **此结果推翻 §23.3 的「数据缺失论」**：
  - 训练集音频经用户逐句确认读音正确（非数据标错）；
  - v23 已补全 minority 样本（2031 句 × 12 轮 = 24372 次曝光）；
  - LoRA 加载链路经独立排查确认**完全正确**：权重非零（lora_B std≈0.005–0.009）、forward 生效、每层扰动 2.4%–7.4%。
  - 即：**数据对、加载对、权重有扰动，但模型仍不纠错** → 「补数据 + rank16」路线被证伪。
- **根因待查（截至 2026-07-25 未定位）**，候选方向：
  1. base 模型对这些字的读音先验极强，LoRA（rank16 + ~5% 扰动）在 12 轮内压不过；
  2. 声调（超音段/F0）映射可能不在 enable_lm 可撬动的载荷里，需更大容量 / enable_proj / enable_dit 才动得了；
  3. 扩散模型非确定性 + 听测主观，本机 whisper/funasr 均不可用，只能人工听，结论存在主观偏差风险。
- ⚠️ **结论**：本项目截至 2026-07-25 **尚未产出可用的多音字纠错 LoRA**。原 §27「复现成功判据」目前是**未达成**状态。

---

## 二十四、词典与拼音标注流水线（可复现核心 ★）

> 本节是"别人能复现全部成果"的最关键一环。前面 §16–§18 只写了"句子要标正确拼音"，
> 但**怎么标的、靠什么保证不出错、工具链在哪**，此前从未落文档。
> 全项目靠一套三层的拼音标注机制：
>
> 1. **phrases_dict 默认注音**（pypinyin 内置词组库，47111 条，默认激活）
> 2. **主动 OVERLAY**（我们手写的 minority 读法强制覆盖，107 条）
> 3. **断言闸门**（build_v23.py 自动校验，挡住"野读法"）
>
> 三者叠加 = 每条训练句都拿到一个"参考读法"，再转成 IndexTTS2 的 `it2_text` 强制读音。

### 24.1 依赖与安装

```powershell
pip install pypinyin pypinyin-dict
```
- `pypinyin` 自带 `phrases_dict`（词组→拼音，47111 条），**无需额外加载即生效**。
- `pypinyin-dict` 是社区增强词典（CC-CEDICT 单字 + large_pinyin 词组）。经 `verify_dict_sources.py` 验证：**它只是 phrases_dict 的子集，加载后无新增价值，可省**。

### 24.2 第一层：phrases_dict 默认注音

- **是什么**：`from pypinyin import pinyin; pinyin("薄荷")` → `["bò","he"]` 这种词组级默认读音。
- **为什么省事**：多字词（薄荷/银行/大夫/血淋淋）靠词组库直接读对，不需要逐字标。
- **离线备份**：`data/pypinyin_polyphone_current.json` 是 phrases_dict 的短语→拼音数组导出，可作对照/离线兜底（跨环境重建时免重新拉包）。
- **验证脚本**：`verify_dict_sources.py` 打印默认读法（盛饭/量杯/还钱/好学/薄荷/银行…），确认 majority 读法默认即对；并说明 pinyin-data 的 `polyphonic.csv` 在仓库根目录 404、词组数据已被 phrases_dict 包含。

### 24.3 第二层：主动 OVERLAY（minority 读法强制）★

phrases_dict 对 **minority 读法**（叶音、专名古音、同句双读）经常默认错。于是手写一份覆盖清单：

- **位置**：`diff_web_pinyin.py` 顶部的 `OVERLAY` 列表（当前 **107 条**）
- **格式**：`("短语", "目标字", "参考读法tone3")`
- **匹配逻辑**：`ref_pinyin(clean)` 先在 phrases_dict 结果上，按短语**精确匹配**后强制覆盖目标字位置的读法；支持"踏踏实实"双"踏"这类同字重复短语。
- **覆盖的高优先 minority 读法（训练最敏感）**：
  - 盛→chéng（盛饭/盛一碗/盛汤/盛满）
  - 血→xuè（血淋淋）
  - 好→hào（好钻研/好强/爱好/好学/好奇/好客/好为人师/好高骛远）
  - 量→liáng（量一量/量身高）/ liang5（打量/思量/商量 轻声）
  - 还→huán（还款/还家/还了钱）
  - 假→jià（放假类，靠短语触发）
  - 行→háng（分行/一行/太行）
  - 兴→xīng（兴奋类，靠短语触发）
- **外加常用多音字 minority 读法**：重chóng/强qiǎng/卷juǎn/中zhòng/转zhuàn/系jì/场cháng/溜liù/绿lù/猫māo/蒙měng/舍shè/圈juàn/色shǎi/识zhì/食sì/饮yìn/旋xuàn/混hún/济jǐ…（均逐短语精确匹配，不影响其他语境）
- **历史人名/古籍专有异读**：樊於期(於yū)/缪贤(缪miào)/缪公(缪mù)/翟方进(翟zhái)/牟长(牟mù)/荥经(荥yíng)/阚泽(阚kàn)/卜商(卜bǔ)/佛道(佛fó)/蠡湖(蠡lǐ)…

⚠️ **OVERLAY 的本质风险**：每条都是"我们强行断言"。只要它与 pypinyin 默认读法不同，就是风险面，**必须人工逐条确认正确**（此前所有真实 bug——发行/地处/空地/盛情/樊於期/作坊——全属这一类）。低风险做法：与默认一致的规则可留作文档，与默认不一致的规则必须复核。

### 24.4 第三层：断言闸门（build_v23.py 防投毒）★

标注完不能无条件信任，用两道自动化检查兜底：

1. **逐字对齐**：`len(ref) == len(clean)`，不允许出现 `?`（长度错位会被记为失败）。
2. **合法读音校验**：每个被强制的读法（去调后）必须落在该字的 pypinyin 异读表合法读音集合内。
   - 专有名词/译名罕见读法用 `WHITELIST`（如 缪→{mù,miù,móu,miào}）放行，避免误删正确句。
   - 不在合法集合 = 疑似投毒式错音 → 记为失败。

**失败句不写入最终 jsonl**，而是落到 `data/v23_assert_failures.txt` 等人工复核。
本轮结果：**assert_failures = 0**（1511 句全部通过，无野读法进入训练数据）。

### 24.5 lint_overlay.py（OVERLAY 自检，改完必跑）

每次改完 `diff_web_pinyin.py` 的 OVERLAY 后跑一遍，防止手滑写错：

```powershell
python lint_overlay.py            # 打印汇总 + 写出 data/overlay_override_risk.txt
python lint_overlay.py --strict   # 发现死规则/非法读音时非0退出（可接 CI）
```

检查项与产出：
- 🔴 死规则（短语不含目标字，永不触发）→ 应删
- 🔴 非法读音（字根本没这个音）→ 应删
- 🟡 重复规则 → 保留一条
- ✓ 与 pypinyin 默认一致（无害，留作文档）
- 🟡 **覆盖类（风险面）** → 写 `data/overlay_override_risk.txt`，必须人工逐条确认

当前规模：`overlay_override_risk.txt` 102 行（覆盖风险）、`overlay_review.txt` 251 行、`overlay_deletable.txt` 309 行、`overlay_agree_review.txt` 46 行。

### 24.6 网页拼音工具交叉验证（teacher-toolset.online）

仅靠内部 phrases_dict+OVERLAY 仍可能漏错，故引入外部交叉验证：

**流程**：上传文本到 `https://www.teacher-toolset.online/zh/onlinetools/pinyin-annotator` → 导出自带 `字(拼音)` 标注 → 存 `D:/电脑桌面/新建 文本文档.txt` → `python diff_web_pinyin.py` 解析、与我们的参考读法逐字比对 → 输出分歧清单。

**⚠️ 格式陷阱（解析必看）**：网页是 `字(拼音)` **逐字插入**，多字词被拼音隔开（如 `银(yin2)行(xing2)`），原文里**不存在"银行"子串** → 任何子串搜索/正则都必须先剥离 `(拼音)` 再匹配；多字词 token 的"字数≠音节数"会造成错位。`diff_web_pinyin.py` 用 `字(拼音)` token 正则 + 贪心音节切分兜底，但**自动扫描不可信，最终靠肉眼读原始行核对**。

**⚠️ 准确性不可作唯一权威**：实测前 9 行即 3 处真实错音——银行/发行的行标 xing2（应 háng）、盛一碗的盛标 sheng4（应 chéng）、好钻研的好标 hao3（应 hào）；且时错时对（薄荷=bò/重新=chóng/重要=zhòng 又标对）→ 上下文消歧不稳定。**正确用法**：仅作快速交叉参考 / 用 diff 找疑点，**绝不能直接灌训练**。

**产出**：`data/web_diff_review.txt`（分歧句复核清单，973 行）、`data/web_annotated_parsed.jsonl`（解析结果，1138 句）。

### 24.7 复现拼音标注的最小步骤

```powershell
pip install pypinyin pypinyin-dict
# 1) 验证默认注音库够用（可选，确认无需社区词典）
python verify_dict_sources.py
# 2) 若需补 minority 读法：编辑 diff_web_pinyin.py 的 OVERLAY，然后自检
python lint_overlay.py --strict
# 3) 构建某批语料的 it2_text（以 v23 为例，见 §25）
python build_v23.py
# 4) 如需外部交叉验证：网页导出 → 桌面文件 → 解析比对
python diff_web_pinyin.py
```

---

## 二十五、v23 数据补全流程（1511 句 minority 补强）

> 为什么有 v23：v19 初训后听测发现 **行/还/量/假/兴**（及修正后的 好）这几个 minority 读法在训练集里极稀疏或缺失（详见 §23.3），模型从未见过信号 → 必须补数据，而非加轮数/开 dit。

### 25.1 数据来源

- **主体 1501 句**：在 v19(520) 之外，另筛一批 minority 补强语料 → `data/corpus_sel_poly_proper.txt`（1501 行，含姓/地名/专名/通假/历史人名异读）。
- **好 hào 补 10 句**：原 v19 好字覆盖不足（v19 idx374 好学误标 hao3），用 `append_hao_sentences.py` 追补 爱好/好学/好奇/好客/好为人师/好高骛远 等 10 句（idx 1502–1511）。
- 合计 **1511 句** → `data/v23_teacher_text.jsonl`。

### 25.2 标注流水线（每句怎么拿到 it2_text）

```
sentence → han_only(仅汉字) → ref_pinyin(clean) [OVERLAY 强制]
        → convert_polyphone.build_texts(sentence, ref) → it2_text / cosy3_text
```

- `ref_pinyin()` 来自 `diff_web_pinyin.py`（§24.3 的 phrases_dict + OVERLAY）。
- `build_texts()` 来自 `convert_polyphone.py`：逐汉字消费一个音节，生成 IndexTTS2 的 `zheng1 zheng1 ,` 强制拼音格式 + CosyVoice3 的中括号音素格式（后者已落选，仅留接口）。

### 25.3 断言闸门结果

`build_v23.py` 跑完：`data/v23_assert_failures.txt` = **0 行** → 1511 句全部通过合法性校验，无野读法进入训练数据。

### 25.4 合并重训

`merge_v19_v23.py` 合并 520(v19) + 1511(v23) = **2031 句** → `data/merged_manifest.jsonl`（audio 绝对路径 + text + sentence + idx + src）：
- 跨集重复句：2（保留）
- 缺失音频：0
- 本轮训练设置见 §23.6（EPOCHS=12 → 3047 步 / rank16 / enable_lm=True / enable_dit=False）。

---

## 二十六、听测与评估方法论（复现"成果"的关键）★

> 训练跑完 ≠ 成功。**声调纠正对没对，loss 数字看不出来**（loss 主要吃声学重建，对超音段/F0 不敏感）。唯一裁判是**人工听测**。本节讲怎么系统化做，避免"凭感觉听两句就下结论"。

### 26.1 独立诊断基准集（与训练集零重叠）

`gen_diag_testset.py` 生成 `data/diag_benchmark.json`（**15 句**），设计原则：

- **5 个失败字**（行/还/量/假/兴）：各 1 句干净的 minority 读法，非绕口令，专测"最难的几个"。
- **7 个已训成功控制字**（好/发/得/应/背/转/舍）：证明架构能学时声调，且 LoRA **不应回退**。
- **3 个额外独立字**（处/亲/从）：补充覆盖。
- 每句标注 `target_char` + `expected_pinyin`(tone3) + `category`，供报告与听测对照。
- `--check` 模式自检与训练集(v19∪v23)零重叠（真正独立，结论可信）。

### 26.2 推理快照（BEFORE / AFTER）

`run_diag_infer.py` 用同一份基准集分别跑：
- **BEFORE**：base 模型（无 LoRA）→ 存 `data/diag_before_judgments.json` / `diag_metrics.json`
- **AFTER**：指定检查点（如 `lora_output/step_0001200`）→ 存 `diag_after_*.json`

两份快照对比即可用数据钉死"重训到底有没有用"，排除主观偏差。

### 26.3 听测与判定

- `diag_listen.py` / `build_listen_html.py` 把基准集 + 推理音频生成**可听网页**（`data/diag_listen.html`），每句标注目标字与期望音，人工逐句判"读对/读错"。
- **判定口径（三态）**：
  - base 错 + LoRA 对 → ✅ **成功**（纠错了）
  - base 对 + LoRA 错 → ⚠️ **回退**（危险，说明 LoRA 把原本对的搞错了，优先排查）
  - 两者都错 → ❌ **数据缺口**（模型从未见过该读法信号，正确修复是补数据重训，不是加轮数/开 dit/改 rank）

### 26.4 已知结论（v19 初训，见 §23.2）

- ✅ 全对：声韵母类（乐/重/血/调/弹/长）+ 声调类（好/发/得/应/背/转/舍）
- ❌ 失败：行/还/量/假/兴（同声韵异调，训练集 minority 样本缺失）
- v23 合并重训的目标：让这 5 字（及 好）在 AFTER 快照里翻盘。

### 26.5 甜区抽点（听测时机，详见 §23.9）

不要等跑满。检查点每 200 步一份，到 **600/1000/1200/1400/1600** 各抽一个出来听，挑耳朵最舒服的留。loss 不敏感的，全靠这套听测挑最佳点。

---

## 二十七、端到端复现速查（给别人照做）

```powershell
# —— 0. 环境 ——
pip install pypinyin pypinyin-dict
# Python 3.12 + torch 2.12（本机）；IndexTTS2 已部署；VoxCPM2 训练入口就位

# —— 1. 文本侧（已有，复现见 §二~§十六）——
#    v19 文本定稿：data/v20_teacher_text.jsonl（520 行，读音标注正确）

# —— 2. 拼音标注（词典流水线，§24）——
python verify_dict_sources.py     # 确认 phrases_dict 默认够用
python lint_overlay.py --strict   # OVERLAY 自检（改过才需）
python build_v23.py               # 生成 v23_teacher_text.jsonl（1511 句，断言0失败）
python diff_web_pinyin.py         # 可选：网页标注交叉验证

# —— 3. 音频合成（§18，IndexTTS2，断点续跑）——
#    synth_batch.py 把 v19/v23 的 it2_text 合成 wavs/，约 22050Hz 单声道
#    （v19 已合成 520 句；v23 的 1511 句如重做需同样流程）

# —— 4. 合并 + 训练（§23.6 / §25.4）——
python merge_v19_v23.py           # 520+1511=2031 → merged_manifest.jsonl
python train_lora.py              # 先生成清单+配置（num_iters=3047）
python train_lora.py --run        # 开训（建议走计划任务 PT0S 免被杀）

# —— 5. 进度与听测（§23.9 / §26）——
#    每 10 分钟看 progress_10min.log；到 600/1200/1400 步抽检查点听测
python gen_diag_testset.py        # 生成独立基准集（零重叠）
python run_diag_infer.py          # base / 指定 LoRA 检查点 推理快照
python build_listen_html.py       # 生成可听网页，人工判定
```

**复现成功的判据**：用 §26 的 15 句基准集，AFTER 快照里 行/还/量/假/兴/好 全部从"base 错"翻成"LoRA 对"，且 7 个控制字不回退。达到即证明"多音字纠错 LoRA"成果复现。

> ⚠️ **项目最终状态（2026-07-25）**：v23 合并重训跑满后**实测仍未达此判据**（详见 §23.13）。截至今日本项目**尚未产出可用的多音字纠错 LoRA**，根因待进一步定位。本文档保留完整排查链路供后续接手。

> 📌 经验收尾：本项目最大的坑不是训练超参，而是**数据覆盖**（minority 读法缺样本）。
> 任何"某字训不对"的第一反应都应是"训练集里这个读法够不够"，而不是"加轮数/开 dit/改 rank"。
