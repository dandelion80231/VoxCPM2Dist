---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 5931fa5612ae011c3dd49956bd422af2_3ecc342eb40a11f1b3c552540024e231
    ReservedCode1: xPpEnhs7VnvQ7rYDl8ML4+3mElLFT/YJPq5jKZ4djxKxQJR0bFwg3z7Z9xw9nRF34WUZUSoKW7H+51FOx3mFOlGjpueD5atBtfb8+CrVgXTkRg0OPGk7GqwrsXnBeB0sHy4WKsV6KmztuV7IIf0rBwJugo5pYK8+RO31ovi4cX5ERaYTcM9Dakg2QB4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 5931fa5612ae011c3dd49956bd422af2_3ecc342eb40a11f1b3c552540024e231
    ReservedCode2: xPpEnhs7VnvQ7rYDl8ML4+3mElLFT/YJPq5jKZ4djxKxQJR0bFwg3z7Z9xw9nRF34WUZUSoKW7H+51FOx3mFOlGjpueD5atBtfb8+CrVgXTkRg0OPGk7GqwrsXnBeB0sHy4WKsV6KmztuV7IIf0rBwJugo5pYK8+RO31ovi4cX5ERaYTcM9Dakg2QB4=
---

# VoxCPM2 词内 {音素} 块 overlay 纠错失效修复文档

- 日期：2026-09-19
- 实施副本：`D:\AI\Build\VoxCPM2Dist_fixed`（仅本地 git，不推送）
- 提交：`10a71d6` — `fix(g2p): 词内{音素}块 overlay 纠错失效 - 去块化匹配+偏移映射+词内块字豁免`
- 上游文档：本文件追加于原项目 `D:\AI\Build\VoxCPM2Dist\docs\`

---

## 1. 背景与根因

前置修复（`75cd1ad`）已解决混合标注模式下 forced 字典"字符偏移 vs 列表索引"错位问题。
但存在一个已知局限：**{音素} 块插入上下文词内部时（如「高处不胜{hang2}寒」），语料 overlay 纠错失效**。

根因：`text_to_phonemes` 中语料匹配直接调用 `_find_all(text, word)`，在**含块的原文**上查找完整上下文词。
当块插入词内部时，词不再是连续子串（"高处不胜{hang2}寒"中找不到"高处不胜寒"），该词的语料规则整体失效。

## 2. 推荐方案（三要点）

1. **去块化匹配**：搜索上下文词前先把 `{音素}` 块剔除，生成净化文本 `clean`，并记录
   `clean[i] -> 原文本字符偏移` 的映射表 `offs` 与块区间表 `blocks`；
   在净化文本上找词，命中后经偏移映射回原文本字符位置。
2. **词内块字豁免**：词命中后逐字检查，目标字若落在 `{音素}` 块区间内、
   或是紧邻块前的汉字（用户显式注音的对象），跳过该字纠错（用户显式注音优先）；
   词内其他未标注字照常应用语料规则。
3. **不破坏既有行为**：块在前/在后、纯文本、`apply_overlay_auto` 自动注入（已标注字不重复注入）维持现状。

## 3. 代码改动

文件：`app/Scripts/g2p_phoneme.py`（副本）

### 3.1 新增辅助函数 `_strip_phoneme_blocks`（L173-202）

位于 `_find_all`（L160）之后，用于生成净化文本、偏移映射与块区间：

```python
def _strip_phoneme_blocks(text: str):
    """剔除文本中的 {音素} 块，生成用于上下文词匹配的净化文本并记录偏移映射。

    返回 (clean, offs, blocks)：
    - clean ：去除所有合法音素块后的文本（块字符不保留，长度 <= 原文本）；
    - offs  ：clean[i] 对应原文本的字符偏移（一一映射，可还原命中位置）；
    - blocks：原文本中每个音素块的 (start, end) 左闭右开区间列表。

    例: "高处不胜{hang2}寒" -> ("高处不胜寒", [0,1,2,3,11], [(4, 11)])
    """
    offs = []
    blocks = []
    out = []
    i = 0
    n = len(text)
    while i < n:
        m = _PHONEME_BLOCK_RE.match(text, i)
        if m:
            blocks.append((m.start(), m.end()))
            i = m.end()
        else:
            out.append(text[i])
            offs.append(i)
            i += 1
    return "".join(out), offs, blocks
```

### 3.2 修改 `text_to_phonemes` 语料匹配逻辑（L102-124）

**改动前**（L101-108）：

```python
    if overlays:
        for word, char, forced_t3 in overlays:
            # 在文本中定位 word 出现的每个区间，仅对区间内的目标字生效（与 v19 force_pos 语义一致）
            for start in _find_all(text, word):
                for off, ch in enumerate(word):
                    if ch == char:
                        pos = start + off
                        forced[pos] = forced_t3
```

**改动后**（L102-124）：

```python
    if overlays:
        # 去块化匹配：{音素} 块可能插入上下文词内部（如「高处不胜{hang2}寒」），
        # 直接在原文上 _find_all 会匹配不到完整词，导致该词语料纠错失效。
        # 先剔除块生成净化文本 clean，并记录 clean[i] -> 原文本偏移 offs 与
        # 块区间 blocks；在 clean 上找词命中后，经 offs 映射回原文本字符位置。
        clean, offs, blocks = _strip_phoneme_blocks(text)
        for word, char, forced_t3 in overlays:
            # 在净化文本中定位 word 出现的每个区间，仅对区间内的目标字生效（与 v19 force_pos 语义一致）
            for start_c in _find_all(clean, word):
                for off, ch in enumerate(word):
                    if ch == char:
                        pos = offs[start_c + off]
                        # 词内块字豁免：目标字是被 {音素} 块显式注音的对象
                        # （落在块区间内，或为紧邻块前的汉字）时跳过该字纠错，
                        # 用户显式注音优先；词内其他未标注字照常应用语料规则。
                        if any(
                            bs <= pos < be
                            or (pos == bs - 1 and _CJK_RE.match(text[pos]))
                            for bs, be in blocks
                        ):
                            continue
                        forced[pos] = forced_t3
```

### 3.3 未改动的部分

- `forced` 字典的应用侧（`tok["start"] in forced`，L125-139）不变：`forced` 的键始终是**原文本字符偏移**，与 `_tokenize` 返回的 `tok["start"]` 直接对齐。
- `_find_all`、`strip_annotated_hanzi`（L340）、`apply_overlay_auto`（L357）、`mixed_to_phonemes`（L391）均无改动。
- 纯文本（无块）时 `clean == text`、`offs` 为恒等映射、`blocks` 为空，行为与改动前完全一致。

## 4. 验证结果

语料：默认 98 条 + 用户 3 条（`结实·结→jie2` / `地处·处→chu4` / `高处不胜寒·胜→sheng1`），共 101 条加载成功。

### 4.1 单元断言（直接调用 `text_to_phonemes` / `apply_overlay_auto`，8/8 PASS）

| # | 用例 | 断言 | 输出 | 结果 |
|---|---|---|---|---|
| 1 | `高处不胜{hang2}寒`（词内块+目标字=块前字） | 不含 `{sheng1}` 且含 `{hang2}` | `{gao1}{chu3}{bu4}{sheng4}{hang2}{han2}` | PASS（豁免） |
| 2 | `{hang2}高处不胜寒`（块在词前） | 含 `{sheng1}` | `{hang2}{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS |
| 3 | `高处不胜寒{hang2}`（块在词后） | 含 `{sheng1}` 且含 `{hang2}` | `{gao1}{chu3}{bu4}{sheng1}{han2}{hang2}` | PASS |
| 4 | `高处不胜寒`（纯文本） | 含 `{sheng1}` | `{gao1}{chu3}{bu4}{sheng1}{han2}` | PASS（回归） |
| 5 | `地{shi2}处`（词内块+目标字非块前字，核心修复点） | 含 `{chu4}` | `de{shi2}{chu4}` | PASS（修复前无 `{chu4}`） |
| 6 | `地处{shi2}理`（词内块+目标字=块前字） | 不含 `{chu4}` | `{di4}{chu3}{shi2}{li3}` | PASS（豁免，修复前含 `{chu4}`） |
| 7 | `结实{shi2}地处{chu4}`（多词多块） | 含 `{jie2}` 且含 `{chu4}` | `{jie2}{shi2}{shi2}{di4}{chu3}{chu4}` | PASS |
| 8 | `apply_overlay_auto("一行代码写完了")` | applied 且含 `{hang2}` | `一行{hang2}代码写完了` | PASS（现状维持） |

核心修复点说明（用例 5）：修复前在含块原文上 `_find_all("地{shi2}处","地处")` 匹配不到完整词，
"处"走引擎默认 `chu3`；修复后净化文本命中词并映射回原位置，"处"被语料强制为 `chu4`。

### 4.2 /api/g2p 接口验证（9 用例，全部正常）

启动 `vox_web_ui.py --port 19001` 后 POST `/api/g2p`：

| 用例 | 返回 phonemes |
|---|---|
| `今天一行{hang2}代码写完了，结实得很` | `{jin1}{tian1}{yi1}{xing4}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` |
| `今天一行代码写完了，结实得很`（纯文本） | `{jin1}{tian1}{yi1}{hang2}{dai4}{ma3}{xie3}{wan2}{liao3}，{jie2}{shi2}{de2}{hen3}` |
| `地处{hang2}山区`（块在词后+块前字） | `{di4}{chu3}{hang2}{shan1}{qu1}` |
| `{hang2}高处不胜寒`（块在词前） | `{hang2}{gao1}{chu3}{bu4}{sheng1}{han2}` |
| `高处不胜寒`（纯文本） | `{gao1}{chu3}{bu4}{sheng1}{han2}` |
| `{hang2}一行代码`（块在词前） | `{hang2}{yi1}{hang2}{dai4}{ma3}` |
| `高处不胜{hang2}寒`（词内块+豁免） | `{gao1}{chu3}{bu4}{sheng4}{hang2}{han2}` |
| `地{shi2}处`（词内块+纠错） | `de{shi2}{chu4}` |
| `地处{shi2}理`（词内块+豁免） | `{di4}{chu3}{shi2}{li3}` |

### 4.3 CLI 合成验证（一次，无报错）

```
python voxcpm_tts_v5_longtext.py -t "高处不胜{hang2}寒，一行代码。" -o temp\cli_word_inner.wav --no-baseline
```

- 模型加载 24.0s，合成完成 3.2s 音频 / 8.5s 渲染，RTF 2.67，输出 `temp\cli_word_inner.wav`
- 混合链路正常：`strip_annotated_hanzi` 剥离块前字后送入模型，无报错

## 5. 行为变更说明

- 词内插块 + 目标字为块前字（用户显式注音）：语料不再覆盖，以用户注音为准（如 `地处{hang2}山区` 中"处"不被强制 `chu4`）。
- 词内插块 + 目标字非块前字：语料纠错恢复生效（如 `地{shi2}处` 中"处"被强制 `chu4`）。
- 块前/块后、纯文本、`apply_overlay_auto` 自动注入行为均与改动前一致。
*（内容由AI生成，仅供参考）*
