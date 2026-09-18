# 用户多音字语料编辑说明（README）

## 一、语料文件

| 文件 | 路径 | 说明 |
| --- | --- | --- |
| 默认语料 | `app\Scripts\training\polyphone_corpus\data\overlay_override_risk.txt` | 随项目分发的已知误读清单（98 条），一般不建议改动 |
| **用户语料** | `app\Scripts\training\polyphone_corpus\data\overlay_user_override.txt` | **用户自行增补/修改，本文件是你的专属语料文件** |

如需把用户语料放到其他位置，可设置环境变量 `VOXCPM_OVERLAY_USER_PATH` 指向你的文件，例如：
```
set VOXCPM_OVERLAY_USER_PATH=D:\my_polyphone\my_overlay.txt
```

## 二、格式（与默认语料一致）

每行一条规则，编码 UTF-8：

```
上下文词 · 目标字: pypinyin默认=X → 强制=Y
```

| 字段 | 要求 | 示例 |
| --- | --- | --- |
| 上下文词 | 目标字所在的词/短语，文本中出现该词时才触发 | `结实` |
| 目标字 | 上下文词中要修正读音的那个汉字，**必须出现在上下文词中** | `结` |
| pypinyin默认=X | 仅作说明参考，程序不校验 | `jie1` |
| 强制=Y | 最终强制的读音，**TONE3 数字声调**（1~5，5 为轻声）；韵母 ü 写作 `v`（如 `lv3`），程序自动还原 | `jie2` |

完整示例行：
```
结实 · 结: pypinyin默认=jie1 → 强制=jie2
地处 · 处: pypinyin默认=chu3 → 强制=chu4
```

规则说明：
- 以 `#` 开头的行、空行会被忽略（可写注释说明）；
- 同一位置命中多条规则时，**用户语料的规则优先生效**（可覆盖默认语料中同位置的规则）；
- 格式不兼容的行会被**静默跳过**，并在运行日志中提示（不影响其他规则）；
- 声调数字含义：1=阴平，2=阳平，3=上声，4=去声，5=轻声。

## 三、生效方式

1. 用文本编辑器（推荐 VS Code / Notepad++，编码选 UTF-8）编辑 `overlay_user_override.txt`，保存；
2. **无需重启进程**：g2p_phoneme.py 检测到语料文件变化会自动重载；
3. 在 Web UI 输入包含上下文词的句子，转音素后即可看到强制读音生效；
4. 如使用 CLI 单独调用，新进程自动读取最新内容。

## 四、验证方法

命令行快速验证（示例）：
```
python g2p_phoneme.py "这捆木材很结实"
```
若用户语料中含 `结实 · 结: pypinyin默认=jie1 → 强制=jie2`，输出中「结」应为 `{jie2}` 而非默认的 `{jie1}`。

查看加载统计（默认/用户/跳过行数）：
```
python -c "import g2p_phoneme; print(g2p_phoneme.overlay_info())"
```

## 五、语料来源参考

- 现成参考语料：`D:\AI\Build\多音字\data\xunfei_batch.txt`（520 句逐字注音，含较多**标注错误**，仅可作候选素材，逐条人工确认后再抄录到用户语料）；
- 全量多音字表：同目录 `polyphone_full.json`（2495 字，含生僻异读，仅查询用）。
