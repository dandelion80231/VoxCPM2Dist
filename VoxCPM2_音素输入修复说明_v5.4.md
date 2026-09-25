---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5931fa5612ae011c3dd49956bd422af2_bc95316db35911f1b3c552540024e231
    ReservedCode1: HGHvBrGRuNe1y8iNndgQyEoCbct3MUrsE+AB3p+qLYYRbLg5KqyO0RqiK9zPdgDOi4bWLuK2hwDbzHDsHu3cxnk8L9UbkkM33vPluFpeI+n+guWJ29leWXJkocf7dVELNIWmsKZzpHSut1gZ5kVL8vHeiG3xNaeHWInobNiH7ZYeOPe/aBXqeEAowt0=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5931fa5612ae011c3dd49956bd422af2_bc95316db35911f1b3c552540024e231
    ReservedCode2: HGHvBrGRuNe1y8iNndgQyEoCbct3MUrsE+AB3p+qLYYRbLg5KqyO0RqiK9zPdgDOi4bWLuK2hwDbzHDsHu3cxnk8L9UbkkM33vPluFpeI+n+guWJ29leWXJkocf7dVELNIWmsKZzpHSut1gZ5kVL8vHeiG3xNaeHWInobNiH7ZYeOPe/aBXqeEAowt0=
---

# VoxCPM2 音素输入修复说明（v5.4）

> 修复对象：VoxCPM2 分发包在**音素输入（Phoneme Input）**上存在的 5 个问题。
> 修复载体：独立副本 `D:\AI\Build\VoxCPM2Dist_fixed`（原项目 `D:\AI\Build\VoxCPM2Dist` 一律未动）。
> 修复日期：2026-09-18
> 提交方式：本地 git，仅限本地，**未推送 / 未上传 GitHub**。

---

## 0. 背景与结论

VoxCPM2 引擎**原生支持**音素输入（官方 Usage Guide 明确支持，非 CosyVoice 专有）：中文格式 `{ni3}{hao3}{shi4}{jie4}`（带声调数字拼音、每音节一对大括号），英文为 CMUDict 风格 `{HH AH0 L OW1}`；音素模式**必须 `normalize=False`**，官方不内置 G2P（汉字→音素）转换，需调用方自行完成。

分发包 5 个问题的根因是「默认配置 + 文档误导 + 缺少工具链」三重叠加，逐一修复如下。

**Commit 总览（副本仓库 main 分支）：**

| Commit | 问题 | 说明 |
|---|---|---|
| `c7d3a5f` | 问题 1 | normalize_text 豁免音素块，`{ni3}` 声调数字不再被转成中文 |
| `24bf148` | 问题 2 | 音素输入自动检测并强制 normalize=False，禁止透传二次归一化；长文本按 `}` 边界切分 |
| `5de8e5b` | 问题 3 | 修正 README 与经验文档错误宣称"不支持音素注入"的表述 |
| `7cb0d2e` | 问题 4 | Web UI 新增音素输入开关（自动联动关闭归一化）+ 后端显式 phoneme_mode 参数 |
| `a992af6` | 问题 5 | 新增 G2P 工具链 g2p_phoneme.py（pypinyin-g2pW 离线模型） |
| `8b6b8be` | 问题 5（增强） | Web UI 集成 G2P：🔤 转音素按钮 + `/api/g2p` 端点 |
| `32b2cd8` | 辅助 | 根目录 `models/`（G2PW 离线模型约 1.2GB）加入 .gitignore 不入库 |
| `6d19b2c` | 基线 | 创建副本、初始化本地 git、移除 origin 远程、剔除 build 产物目录跟踪 |

---

## 1. 问题 1：`normalize_text` 破坏音素串声调数字（高）

- **修改文件**：`app\Scripts\text_norm_cn.py`
- **位置**：`normalize_text` 函数，约 **213-229 行**（音素块保护）与 **414-418 行**（音素块还原）
- **改动前逻辑**：`normalize_text` 开头即做全角转半角，随后用 `re.sub(r'\d+', ...)` 等规则全局转换阿拉伯数字。`{ni3}` 中的声调数字 `3` 被转成中文读法"三"，音素串 `{ni3}{hao3}{shi4}{jie4}` 被破坏为 `{ni三}{hao三}...`。
- **改动后逻辑**：函数开头先扫描 `\{[^{}]*\}` 形式的音素块，整体替换为**字母序号占位符**（`§PA§`、`§PB§`…，避免 `\d+` 规则把序号转数字），全部数字/符号规则处理完毕后，在函数返回前将占位符原样还原为 `{ni3}` 音素块。同时英文缩写拆分正则增加 `\u00a7`（占位符标记）的边界豁免。
- **验证方式**：
  ```python
  from text_norm_cn import normalize_text
  assert normalize_text("{ni3}{hao3}{shi4}{jie4}") == "{ni3}{hao3}{shi4}{jie4}"  # 音素串原样保留
  assert normalize_text("你好世界") == "你好世界"
  assert normalize_text("共3个") == "共三个"  # 普通数字仍正常归一化
  ```

---

## 2. 问题 2：`normalize=True` 透传模型侧二次归一化（高）

- **修改文件**：`app\Scripts\vox_web_ui.py`
- **位置**：`split_text`（**631-650 行附近**）、`synthesize`（**792-802 行**、**886-891 行**）
- **改动前逻辑**：
  1. `split_text` 只按标点/长度切分，长文本可能把 `{ni3}` 切成半个音素块；
  2. `synthesize` 中 `normalize = args.get("normalize","true")` 直接透传，音素文本也会被 `normalize_text` 处理；
  3. 归一化后的文本还会透传给模型侧做二次归一化，而官方要求音素模式必须 `normalize=False`。
- **改动后逻辑**：
  1. 新增 `_is_phoneme_text(s)`：检测连续 2+ 个 `{xxx}` 块或单块音素文本；
  2. `split_text` 检测到音素串时按 `}` 边界切块、绝不切断音素块（验证 roundtrip 完整）；
  3. `synthesize` 中 `phoneme_mode = _is_phoneme_text(text)`，`normalize = requested_normalize and not phoneme_mode`（音素模式强制关归一化）；
  4. 归一化兜底：即使 `normalize` 因故为 True 且 `phoneme_mode` 为 True，也跳过 `normalize_text`，与问题 1 形成双保险。
- **验证方式**：Web UI 输入 `{ni3}{hao3}{shi4}{jie4}` 合成；日志确认请求中 `normalize=False` 生效、音素串未经过 `normalize_text`。长文本音素输入验证切分无半个音素块。

---

## 3. 问题 3：README 错误宣称"不支持音素注入"（高）

- **修改文件**：`README.md`（**47 行**、**532 行**）、`app\Scripts\training\polyphone_corpus\README_v23_完整经验.md`（**21 行附近**）
- **改动前**：
  - README.md:47 及 532：宣称 "VoxCPM2 **不支持** `{pinyin}`/`{ni3}` 音素注入（那是 CosyVoice 的特性），机械式拼音标注对其无效"。
  - README_v23_完整经验.md：宣称 "VoxCPM2 不做显式多音字处理（无 G2P/拼音输入通道）"。
- **改动后**：
  - 改为官方口径："VoxCPM2 **原生支持** `{ni3}{hao3}` 这类音素输入（中文带声调数字拼音，每音节一对大括号；英文为 CMUDict 风格），并非 CosyVoice 专有特性。音素模式**必须关闭文本归一化**（`normalize=False`，本分发 Web UI 已自动检测 `{}` 音素串并强制切换），且官方不内置 G2P 转换，需调用方自行完成汉字→音素（本分发已提供 G2P 模块 `app/Scripts/g2p_phoneme.py`）。"
  - README_v23 同步补充原生支持音素输入 + 本分发提供离线 G2P 模块的说明，并保留"LoRA 是另一种多音字纠正路径"的表述。
- **验证方式**：通读 README 音素相关段落，确认无"不支持音素注入"残留表述；`Select-String -Pattern "不支持.*音素|音素注入.*无效" README.md README_v23_完整经验.md` 0 命中。

---

## 4. 问题 4：前端无音素开关、后端无音素串兜底（中）

- **修改文件**：`app\Scripts\vox_web_ui.py`
- **位置**：HTML **2130-2131 行**（音素输入复选框）、JS **2567-2578 行**（开关联动）、**2581-2601 行**（`g2pConvertText`）、**3066 行**（FormData 提交）、FastAPI 参数 **3588 行**（`phoneme_mode: str = Form("false")`）、**3598 行附近**（透传 args）、**795-800 行**（后端显式参数 + 自动检测兜底）
- **改动前**：前端无任何音素模式入口；后端仅依赖文本自动判断（改动前其实无判断，直接透传 normalize）。
- **改动后**：
  1. 前端高级参数卡片新增「音素输入」复选框 `phonemeToggle`；
  2. JS 联动：开启音素开关自动取消勾选「数字归一化」并 Toast 提示（官方要求 normalize=False）；
  3. FormData 提交 `phoneme_mode` 字段；
  4. FastAPI 表单新增 `phoneme_mode: str = Form("false")` 并透传至 `synthesize`；
  5. 后端 `phoneme_mode = requested_phoneme_mode or _is_phoneme_text(text)`——显式开关优先，文本自动检测兜底（用户手输音素串不开开关也能正确合成）。
- **验证方式**：Web UI 勾选「音素输入」→ 数字归一化自动取消；提交请求 FormData 含 `phoneme_mode=true`；不勾选开关直接输入 `{ni3}{hao3}` 文本，后端自动进入音素模式。

---

## 5. 问题 5：缺少 G2P 转换工具链（低）

- **新增文件**：`app\Scripts\g2p_phoneme.py`（122 行）
- **Web UI 集成**：`app\Scripts\vox_web_ui.py` — **2146 行**（🔤 转音素按钮）、**2581-2601 行**（`g2pConvertText` JS）、**3625-3640 行**（`POST /api/g2p` 端点）
- **改动前**：分发包没有任何汉字→音素转换能力，用户只能手写 `{ni3}` 音素串。
- **改动后**：
  - `g2p_phoneme.py` 基于 `pypinyin_g2pw.G2PWPinyin`（g2pW + BERT 上下文多音字消歧），核心函数 `text_to_phonemes(text, v_to_u=True)`：汉字 → `{pin1}` 音素块，英文/数字/标点等非汉字原样保留；提供 CLI（`python g2p_phoneme.py "重庆银行行长"`）与模块两种用法；模型目录支持环境变量 `VOXCPM_G2PW_MODEL_DIR` 覆盖，默认 `<项目根>/models/G2PWModel/G2PWModel`；模型缺失时抛出带指引的 `FileNotFoundError`。
  - Web UI「待合成文本」卡片新增「🔤 转音素」按钮，调用 `POST /api/g2p`（body `{"text": "..."}`）→ 返回 `{"phonemes": "{ni3}{hao3}..."}` → 回填文本框并自动开启音素模式。
- **验证方式**：
  ```bash
  python g2p_phoneme.py "你好世界"   # => {ni3}{hao3}{shi4}{jie4}
  python g2p_phoneme.py "重庆银行行长到北京"  # 多音字消歧：重=chong2，行=hang2
  curl -X POST http://127.0.0.1:8000/api/g2p -H "Content-Type: application/json" -d "{\"text\":\"你好\"}"
  ```

---

## 6. 依赖与模型安装说明

### 6.1 Python 依赖（副本环境）

```bash
pip install pypinyin-g2pw==0.4.0
# 自动拉取 g2pw、pypinyin、onnxruntime、transformers、jieba 等
```

### 6.2 G2PW 离线模型（体积较大，不入 git）

```bash
# 模型包：G2PWModel-v2-onnx.zip（含 g2pw.onnx、bert 相关文件）
# 解压到（默认路径）：
D:\AI\Build\VoxCPM2Dist_fixed\models\G2PWModel\G2PWModel\
# tokenizer（bert-base-chinese，用于 BERT 上下文）：
D:\AI\Build\VoxCPM2Dist_fixed\models\bert-base-chinese\

# 也可用环境变量指定模型目录（优先级最高）：
set VOXCPM_G2PW_MODEL_DIR=D:\path\to\G2PWModel
```

已实测离线加载成功：`from pypinyin_g2pw import G2PWPinyin`，多音字消歧正确（"重庆银行"→`{chong2}{qing4}{yin2}{hang2}`）。`models/` 已加入 `.gitignore`，不入库、不随 git 分发。

---

## 7. 副本与 git 记录

- **副本目录**：`D:\AI\Build\VoxCPM2Dist_fixed`（robocopy 复制，剔除 build 产物目录跟踪；`git remote -v` 为空，无任何远程）
- **原项目**：`D:\AI\Build\VoxCPM2Dist`（未修改任何文件，本说明文档除外——见 §8）
- **关键文件**（副本内）：
  - `app\Scripts\text_norm_cn.py`（问题 1）
  - `app\Scripts\vox_web_ui.py`（问题 2/4/5 集成）
  - `app\Scripts\g2p_phoneme.py`（问题 5，新增）
  - `README.md`、`app\Scripts\training\polyphone_corpus\README_v23_完整经验.md`（问题 3）
  - `models\G2PWModel\G2PWModel\`、`models\bert-base-chinese\`（离线模型，gitignore）

---

## 8. 本说明文档的 git 记录

本说明文档存放于原项目目录 `D:\AI\Build\VoxCPM2Dist\VoxCPM2_音素输入修复说明_v5.4.md`，作为一次独立本地 git 记录提交到原项目仓库（仅新增本文件，不触碰任何代码/配置文件）。

---

## 附：修复后音素输入推荐使用流程

1. 文本归一化（数字/符号 → 中文词）——调用 `text_norm_cn.normalize_text`；
2. G2P：汉字 → 音素串——调用 `g2p_phoneme.text_to_phonemes` 或 Web UI「🔤 转音素」；
3. 模型侧：`normalize=False`——Web UI 勾选「音素输入」（自动关闭归一化）或后端自动检测 `{}` 音素串兜底；
4. 长文本自动按 `}` 边界切分，避免切断音素块。

> 注意：control 语音控制指令不能音素化；英文音素支持需按 CMUDict 风格实测。
*（内容由AI生成，仅供参考）*
