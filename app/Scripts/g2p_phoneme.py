# -*- coding: utf-8 -*-
"""
g2p_phoneme.py — VoxCPM2 音素输入 G2P 转换模块（问题5修复）

功能：将中文文本转换为 VoxCPM2 官方支持的音素输入格式，例如：
    "你好世界"  ->  "{ni3}{hao3}{shi4}{jie4}"
英文/数字/标点等非汉字片段原样保留（不包裹大括号），供模型按字符级输入。

依赖：
    pip install pypinyin-g2pw==0.4.0
    （自动拉取 g2pw、pypinyin、onnxruntime、transformers 等）

模型（离线，不入 git）：
    G2PWModel-v2-onnx.zip 解压到 <项目根>/models/G2PWModel/G2PWModel/
    bert-base-chinese tokenizer 到 <项目根>/models/bert-base-chinese/
    模型目录优先级：环境变量 VOXCPM_G2PW_MODEL_DIR > 脚本相对路径推导

用法：
    CLI:  python g2p_phoneme.py "重庆银行行长到北京"
    模块: from g2p_phoneme import text_to_phonemes
          text_to_phonemes("重庆银行", v_to_u=False)

说明：
    - 本模块只做 G2P（汉字 -> 拼音/音素），不负责文本归一化；
      调用方应先做数字/符号归一化（text_norm_cn.normalize_text），
      再调用本模块生成音素串，最后以 normalize=False 传给模型。
    - g2pW 多音字消歧基于 BERT 上下文，准确率高；个别字（如“测”在
      “测试”中偶读 ce4）属模型已知误差，可用 polyphone_corpus 修正规则兜底。
"""

import os
import re
import sys

# 模型目录推导：<项目根>/models/G2PWModel/G2PWModel
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, "..", ".."))
_DEFAULT_MODEL_DIR = os.path.join(_PROJECT_ROOT, "models", "G2PWModel", "G2PWModel")

_MODEL_DIR = os.environ.get("VOXCPM_G2PW_MODEL_DIR", _DEFAULT_MODEL_DIR)

# 汉字字符（含扩展）
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
# 可接受的拼音音节（声调数字结尾）
_SYLLABLE_RE = re.compile(r"^[a-zü]+[1-5]$", re.IGNORECASE)
# 合法 VoxCPM 音素块：{hang2}（拼音声调）或 {HH AH0 L OW1}（CMU 英文音素）。
# 只匹配英文字母/ü/数字/空格/'-/. 组成的花括号块，普通中文花括号（如 {重要}）不匹配。
_PHONEME_BLOCK_RE = re.compile(r"\{[a-zA-ZüÜ0-9\s\-'\.]+\}")

_engine = None
_engine_v_to_u = None


def _get_engine(v_to_u: bool = True):
    """懒加载 G2PWPinyin 引擎（离线加载，约 1~4 秒）。"""
    global _engine, _engine_v_to_u
    if _engine is not None and _engine_v_to_u == v_to_u:
        return _engine
    if not os.path.isdir(_MODEL_DIR):
        raise FileNotFoundError(
            f"G2PW 模型目录不存在: {_MODEL_DIR}\n"
            "请下载 G2PWModel-v2-onnx.zip 并解压到该目录，"
            "或用环境变量 VOXCPM_G2PW_MODEL_DIR 指定模型目录。"
        )
    from pypinyin import Style
    from pypinyin_g2pw import G2PWPinyin

    _engine = G2PWPinyin(
        model_dir=_MODEL_DIR,
        model_source=_MODEL_DIR,
        turnoff_tqdm=True,
        v_to_u=v_to_u,
    )
    _engine_v_to_u = v_to_u
    _engine._style = Style.TONE3  # noqa: 供内部读取
    return _engine


def text_to_phonemes(text: str, v_to_u: bool = True) -> str:
    """
    将文本转为 VoxCPM2 音素串：汉字 -> {pin1}，非汉字原样保留。

    v_to_u=True 时 ü 以 ü 形式输出（如 lü3），False 时输出 v（如 lv3）。
    """
    if not text:
        return text
    from pypinyin import Style

    engine = _get_engine(v_to_u=v_to_u)
    parts = engine.pinyin(text, style=Style.TONE3)
    # 字符级对齐：连续汉字逐字成 item、连续非汉字合并为一段（pypinyin 分组行为）
    tokens = _tokenize(text)
    if len(tokens) != len(parts):
        # 对齐失败（异常输入），回退到无纠错的原有逻辑
        out_parts = []
        for item in parts:
            syl = item[0] if item else ""
            out_parts.append("{%s}" % syl if _SYLLABLE_RE.match(syl) else syl)
        return "".join(out_parts)

    overlays = _load_overlay_rules()
    forced = {}  # token 索引 -> 强制读音（TONE3，v 风格）
    if overlays:
        for word, char, forced_t3 in overlays:
            # 在文本中定位 word 出现的每个区间，仅对区间内的目标字生效（与 v19 force_pos 语义一致）
            for start in _find_all(text, word):
                for off, ch in enumerate(word):
                    if ch == char:
                        pos = start + off
                        forced[pos] = forced_t3
    out_parts = []
    for idx, item in enumerate(parts):
        syl = item[0] if item else ""
        tok = tokens[idx]
        if tok["is_han"] and idx in forced:
            fs = forced[idx]
            if v_to_u:
                fs = fs.replace("v", "ü")
            out_parts.append("{%s}" % fs)
        elif _SYLLABLE_RE.match(syl):
            out_parts.append("{%s}" % syl)
        else:
            out_parts.append(syl)
    return "".join(out_parts)


def _tokenize(text: str):
    """将文本切分为 tokens：汉字单字（记录字符与起始偏移），非汉字连续段。"""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if _CJK_RE.match(ch):
            tokens.append({"char": ch, "start": i, "is_han": True})
            i += 1
        else:
            j = i
            while j < n and not _CJK_RE.match(text[j]):
                j += 1
            tokens.append({"char": text[i:j], "start": i, "is_han": False})
            i = j
    return tokens


def _find_all(text: str, word: str):
    """返回 word 在 text 中所有出现位置的起始索引列表。"""
    res = []
    start = 0
    while True:
        idx = text.find(word, start)
        if idx == -1:
            break
        res.append(idx)
        start = idx + 1
    return res


# ---- 多音字修正语料后置纠错（overlay_override_risk.txt，98 条） ----
_OVERLAY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "training", "polyphone_corpus", "data", "overlay_override_risk.txt",
)
_OVERLAY_RE = re.compile(
    r"^(?P<word>.+?)\s*·\s*(?P<char>.)\s*:\s*pypinyin默认=(?P<default>[a-züv]+[1-5])\s*→\s*强制=(?P<forced>[a-züv]+[1-5])"
)
_overlay_cache = None


def _load_overlay_rules():
    """解析语料为 [(上下文词, 目标字, 强制读音TONE3(v风格))]，失败/缺失时返回 []。"""
    global _overlay_cache
    if _overlay_cache is not None:
        return _overlay_cache
    rules = []
    if not os.path.isfile(_OVERLAY_PATH):
        print(f"[g2p_phoneme] 警告: 多音字修正语料不存在，跳过后置纠错: {_OVERLAY_PATH}", file=sys.stderr)
        _overlay_cache = []
        return _overlay_cache
    try:
        with open(_OVERLAY_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = _OVERLAY_RE.match(line)
                if m:
                    word = m.group("word").strip()
                    char = m.group("char").strip()
                    forced = m.group("forced").strip().replace("ü", "v").replace("u:", "v")
                    if word and char and forced and char in word:
                        rules.append((word, char, forced))
    except OSError as e:
        print(f"[g2p_phoneme] 警告: 读取语料失败: {e}", file=sys.stderr)
        _overlay_cache = []
        return _overlay_cache
    _overlay_cache = rules
    print(f"[g2p_phoneme] 已加载多音字修正语料 {len(rules)} 条: {_OVERLAY_PATH}", file=sys.stderr)
    return _overlay_cache


def overlay_stats():
    """返回已加载语料条数（供诊断/测试）。"""
    return len(_load_overlay_rules())


def has_phoneme_block(text: str) -> bool:
    """检测文本是否含任意合法 VoxCPM 音素块（{hang2} / {HH AH0 L OW1}）。

    与 is_phoneme_text 的区别：is_phoneme_text 用于判断「整段已是音素串」，
    这里只要出现一个合法音素块即视为混合模式（普通文本 + 局部音素标注），
    供 Web UI / CLI 合成链路决定是否走 mixed_to_phonemes。
    """
    return bool(_PHONEME_BLOCK_RE.search(text or ""))


def strip_annotated_hanzi(text: str) -> str:
    """混合模式去重读：{音素块} 紧跟标注的是其前面的那个汉字，读音已由音素块
    接管，送入模型前舍去该汉字本身，避免“汉字读一遍、{音素}又读一遍”。

    - 仅处理「汉字紧贴 {音素块}」形态（如"今天一行{hang2}代码" -> "今天一{hang2}代码"），
      汉字与块之间有无空格/换行等非汉字字符时不处理；
    - 仅移除单个汉字（标注对象是音素块紧邻的前一个汉字），连续块前无汉字不误删；
    - 纯音素串（块前是空格或行首）不受影响；
    - UI / 输入文本仍保留原字供对照，此处仅作用于“送入模型的文本”。

    例: "今天一行{hang2}代码写完了" -> "今天一{hang2}代码写完了"
    """
    if not text:
        return text
    return re.sub(r"([\u4e00-\u9fff])(\{[a-zA-ZüÜ0-9\s\-'\.]+\})", r"\2", text)


def apply_overlay_auto(text: str) -> tuple:
    """默认链路（无音素标注）自动纠错：98 条多音字语料命中的上下文词，
    在目标字后自动注入 {音素} 标注（如"一行" -> "一行{hang2}"），
    返回 (new_text, applied)。applied=True 时调用方应进入混合模式：
    strip_annotated_hanzi 会把紧贴块前的汉字舍去，模型只按 {hang2} 读音。

    - 同一位置多规则命中时取语料顺序靠前的一条；
    - 无命中 / 无语料时原样返回 (text, False)，不影响默认链路。
    """
    if not text:
        return text, False
    overlays = _load_overlay_rules()
    if not overlays:
        return text, False
    marks = []  # (pos, forced)
    for word, char, forced_t3 in overlays:
        forced = forced_t3.replace("v", "ü")
        for start in _find_all(text, word):
            for off, ch in enumerate(word):
                if ch == char:
                    marks.append((start + off, forced))
    if not marks:
        return text, False
    seen = {}
    for pos, forced in marks:
        if pos not in seen:
            seen[pos] = forced
    out = list(text)
    # 倒序插入，避免索引偏移
    for pos in sorted(seen, reverse=True):
        out.insert(pos + 1, "{%s}" % seen[pos])
    new_text = "".join(out)
    return new_text, (new_text != text)


def mixed_to_phonemes(text: str, normalize_segments: bool = True) -> str:
    """混合模式：普通文本 + 局部 {音素} 标注 -> 全音素串。

    - 普通部分（中文/数字/标点等）：先归一化（数字转中文读法，音素块被
      text_norm_cn 占位保护不会破坏），再经 G2P 转为 {pin1} 音素，保证读对；
    - 用户标注的 {hang2} 等合法音素块：作为非汉字片段原样保留，强制按
      标注读音；
    - 返回串为纯音素串，调用方必须以 normalize=False 传给模型（音素串
      不可再做二次归一化）。

    例: "今天一行{hang2}代码写完了" ->
        "{jin1}{tian1}{yi1}{hang2}{dai4}{ma3}{xie1}{wan2}{le0}{hang2}"
    （若 98 条多音字语料命中“一行”，普通部分的“行”也会被纠为 hang2，
     与用户标注读音一致；未命中的多音字则以标注为准。）
    """
    if not text:
        return text
    if normalize_segments:
        try:
            from text_norm_cn import normalize_text
            text = normalize_text(text)
        except Exception:
            pass  # 归一化失败不阻塞，直接按原文转音素
    return text_to_phonemes(text, v_to_u=True)


def is_phoneme_text(text: str) -> bool:
    """检测文本是否为音素串（含连续两个 {xxx} 音素块）。与 vox_web_ui 兜底一致。"""
    return bool(re.search(r"\{[^{}]*\}\s*\{[^{}]*\}", text or ""))


def main():
    if len(sys.argv) < 2:
        print("用法: python g2p_phoneme.py \"<中文文本>\" [--v-as-u|--v-as-v]", file=sys.stderr)
        return 1
    args = sys.argv[1:]
    v_to_u = True
    if "--v-as-v" in args:
        v_to_u = False
        args = [a for a in args if a != "--v-as-v"]
    if "--v-as-u" in args:
        args = [a for a in args if a != "--v-as-u"]
    text = " ".join(args)
    print(text_to_phonemes(text, v_to_u=v_to_u))
    return 0


if __name__ == "__main__":
    sys.exit(main())
