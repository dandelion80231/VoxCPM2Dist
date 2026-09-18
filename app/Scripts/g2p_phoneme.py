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
    out_parts = []
    for item in parts:
        # item 形如 ['ni3']（汉字音节）或 [' world 123，']（非汉字片段）
        syl = item[0] if item else ""
        if _SYLLABLE_RE.match(syl):
            out_parts.append("{%s}" % syl)
        else:
            out_parts.append(syl)
    return "".join(out_parts)


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
