---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5931fa5612ae011c3dd49956bd422af2_2e9b2b96b40011f1ba1b525400638852
    ReservedCode1: m2bxlPN02rsqLPZb3muYujp3zjWw3df+XQ2KY77PUvW0YIiKR68xXphqiKTUe/Dlsey6kZDcngNkHS24/GxjCqqVUdbjE1atsQD4ClRVL6xgDTNQ5Dxq1yKFZ8hK9G9pqLmSIW2ppQsjYDaAkbg3Ydwow0yfjczCGVl8GbX3PrfyMI+3sHToxNBm59A=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5931fa5612ae011c3dd49956bd422af2_2e9b2b96b40011f1ba1b525400638852
    ReservedCode2: m2bxlPN02rsqLPZb3muYujp3zjWw3df+XQ2KY77PUvW0YIiKR68xXphqiKTUe/Dlsey6kZDcngNkHS24/GxjCqqVUdbjE1atsQD4ClRVL6xgDTNQ5Dxq1yKFZ8hK9G9pqLmSIW2ppQsjYDaAkbg3Ydwow0yfjczCGVl8GbX3PrfyMI+3sHToxNBm59A=
---

# 修复：混合标注模式下语料 overlay 纠错失效

- 日期：2026-09-19
- 修复副本：`D:\AI\Build\VoxCPM2Dist_fixed`（仅本地 git，未推送）
- 原项目文档：`D:\AI\Build\VoxCPM2Dist\docs\g2p_overlay_mixed_mode_fix_20260919.md`
- 对应 commit：`75cd1ad` `fix(g2p): 混合标注模式 overlay 纠错失效 - forced 改用字符偏移匹配`

## 一、问题现象

文本含 `{音素}` 标注块时（如「今天一行{hang2}代码写完了，结实得很」），`text_to_phonemes` 的语料 overlay 纠错失效：

- 混合文本下「结实」返回 `jie1`（用户语料期望 `jie2`）—— **本次修复目标**
- 纯文本路径（无 `{音素}` 块）返回 `jie2`，正常

## 二、根因定位

`g2p_phoneme.py::text_to_phonemes` 中：

1. `_tokenize(text)` 将文本切成 tokens：汉字单字（带 `start` 字符偏移）、非汉字连续段；`{hang2}` 块整体是一个非汉字多字符段。
2. `engine.pinyin(text, style=Style.TONE3)` 返回 parts，pypinyin 对汉字/非汉字按字符分组。
3. 对齐检查 `len(tokens) != len(parts)` 通过（混合文本下两者长度一致，均为 15）。
4. **根因**：overlay 规则在 `_find_all(text, word)` 找到词的**字符偏移** `pos = start + off`，存入 `forced` 字典；但应用时却用**列表索引** `idx`（`forced[idx]`）查找。纯文本下汉字单字 token 的索引与字符偏移恰好一一对应，因此正常；混合文本下 `{hang2}` 多字符块占据多个字符位置，导致后续 token 的**索引**与**字符偏移**错位，`idx in forced` 不命中，overlay 被跳过、回退为 pypinyin 默认读音。

## 三、修改内容

文件：`app/Scripts/g2p_phoneme.py`（副本，改后 394 行，+3/-3）

### 改动 1（行号 L100，改前 L100）

**改前：**

```python
forced = {}  # token 索引 -> 强制读音（TONE3，v 风格）
```

**改后：**

```python
forced = {}  # 字符偏移 -> 强制读音（TONE3，v 风格）
```

### 改动 2（行号 L116-117，改前 L113-114）

**改前：**

```python
        if tok["is_han"] and idx in forced:
            fs = forced[idx]
```

**改后：**

```python
        if tok["is_han"] and tok["start"] in forced:
            fs = forced[tok["start"]]
```

改动说明：`forced` 的键本身是 `pos = start + off`（字符偏移），应用侧改为按 token 的字符偏移 `tok["start"]` 匹配，使两处口径一致。`_tokenize` 返回的 token 含 `start` 字段（字符起始偏移），无需额外计算。未改动对齐回退逻辑（`len(tokens) != len(parts)` 仍回退）与 `_SYLLABLE_RE` 等其余逻辑。

## 四、验证结果

### 4.1 直接调用断言（g2p_phoneme.text_to_phonemes）

| 用例 | 输入 | 输出 | 结果 |
|---|---|---|---|
| 混合·结实 | 今天一行{hang2}代码写完了，结实得很 | `{jin1}{tian1}{yi1}{hang2}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` | PASS（jie2 ✓，块保留 ✓） |
| 纯文本·结实 | 今天一行代码写完了，结实得很 | `{jin1}{tian1}{yi1}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` | PASS（回归不变） |
| 混合·处 | 地处{hang2}山区 | `{di4}{chu4}{hang2}{shan1}{qu1}` | PASS（chu4 ✓） |
| 纯文本·处 | 地处山区 | `{di4}{chu4}{shan1}{qu1}` | PASS（chu4 ✓） |
| 混合·胜（块在前） | {hang2}高处不胜寒 | `{hang2}{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS（sheng1 ✓） |
| 混合·胜（块在后） | 高处不胜寒{hang2} | `{gao1}{chu3}{bu4}{sheng1}{han2}{hang2}` | PASS（sheng1 ✓） |
| 纯文本·胜 | 高处不胜寒 | `{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS（sheng1 ✓） |
| 多块 | {hang2}今天一行{hang2}代码写完了，结实得很 | `{hang2}{jin1}{tian1}{yi1}{hang2}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` | PASS（2 个用户块 + 1 个「行」字 overlay 均保留，jie2 ✓） |
| 块开头 | {hang2}一行代码 | `{hang2}{yi1}{hang2}{dai4}{ma3}` | PASS（块 + 「行」overlay 均生效） |

说明：用户语料 3 条规则（结实→jie2、地处·处→chu4、高处不胜寒·胜→sheng1）在词未被块打断时均生效；若 `{音素}` 块插在上下文词**内部**（如「高处不胜{hang2}寒」），`_find_all` 无法匹配完整词，该词纠错不生效——属字符串匹配的固有局限（词不完整），非本次对齐缺陷，纯文本不受影响。

### 4.2 Web 接口 /api/g2p（重启服务后验证）

| 请求文本 | phonemes 返回 | 结果 |
|---|---|---|
| 今天一行{hang2}代码写完了，结实得很 | `{jin1}{tian1}{yi1}{hang2}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` | PASS（jie2，块保留） |
| 今天一行代码写完了，结实得很 | `{jin1}{tian1}{yi1}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` | PASS |
| 地处{hang2}山区 | `{di4}{chu4}{hang2}{shan1}{qu1}` | PASS |
| {hang2}高处不胜寒 | `{hang2}{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS |
| 高处不胜寒 | `{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS |
| {hang2}一行代码 | `{hang2}{yi1}{hang2}{dai4}{ma3}` | PASS |

### 4.3 CLI 合成

命令（CPU 模式）：

```
python voxcpm_tts_v5_longtext.py -t "今天一行{hang2}代码写完了，结实得很" --dir <temp> --steps 8 --cfg 2.5 --no-baseline
```

结果：合成成功，`[合成] 完成: 2.7s 音频, 9.3s 渲染, RTF 3.42`，输出 `C:\Users\000\Desktop\今天一行{hang2}代码写完了，结实得_20260919_155535.wav`（261164 字节），无 Python 报错。

## 五、commit

- `75cd1ad` `fix(g2p): 混合标注模式 overlay 纠错失效 - forced 改用字符偏移匹配`
- 变更：`app/Scripts/g2p_phoneme.py`，1 file changed, 3 insertions(+), 3 deletions(-)
- 仅本地 commit，未推送（副本无 origin）。

## 六、影响范围

- 只读影响：`text_to_phonemes` / `apply_overlay_auto` 的 overlay 命中口径从「列表索引」修正为「字符偏移」。
- 纯文本路径输出不变（索引 == 偏移，逐一验证回归通过）。
- `strip_annotated_hanzi`、`len(tokens) != len(parts)` 回退逻辑、`{音素}` 块保留逻辑均未改动。
*（内容由AI生成，仅供参考）*
