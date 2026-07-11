# text_norm_cn.py — 中文数字/日期/电话归一化（网页端与命令行后端共用）
#
# 将阿拉伯数字、版本号、百分号、日期等转换为自然的中文读法，
# 同时处理热线号码（110->妖妖灵）、电话号码（1->幺）、年份区间（->到）等特例。
# 纯标准库实现（仅依赖 re），可被 vox_web_ui.py 与 voxcpm_tts_v5_longtext.py 直接 import。

import re

_CN_DIGITS = "零一二三四五六七八九"


def _num_to_ch(s: str) -> str:
    """将阿拉伯数字串（含小数点）按位转换为中文读法。"""
    return "".join(_CN_DIGITS[int(c)] if c.isdigit() else "点" for c in s)


def _hotline_to_ch(s: str) -> str:
    """热线号码逐位读法：1 读成「幺」；110 特例读「妖妖灵」（报警电话的趣味读法）。"""
    if s == "110":
        return "妖妖灵"
    return "".join("幺" if c == "1" else _CN_DIGITS[int(c)] for c in s)


def _int_to_chinese_small(n: int) -> str:
    """把 0-9999 的整数按中文位值读法转换（26->二十六，180->一百八十，2024->二千零二十四）。"""
    if n == 0:
        return "零"
    digits = "零一二三四五六七八九"
    s = str(n)
    length = len(s)
    res = ""
    zero = False
    for i, ch in enumerate(s):
        d = int(ch)
        pos = length - 1 - i
        if d == 0:
            if not zero and pos > 0 and any(int(c) != 0 for c in s[i + 1:]):
                zero = True
        else:
            if zero:
                res += "零"
                zero = False
            # 10-19 读作「十几」，省略开头的「一」
            if not (d == 1 and pos == 1 and length == 2):
                res += digits[d]
            if pos > 0:
                res += ["", "十", "百", "千"][pos]
    return res


def normalize_text(text: str) -> str:
    """数字/编号归一化：将阿拉伯数字、版本号、百分号、日期等转换为中文读法，
    同时去掉字母与数字之间的连字符/下划线，避免 GPT-5.6 被读成「杠」。"""

    # 1) 日期：YYYY年MM月DD日 / MM月DD日 / MM月DD号 / YYYY年MM月
    #    年份逐位读（二零二四），月份、日期按位值读（六月二十六日）
    def date_repl(m):
        year, month, day = m.group(1), m.group(2), m.group(3)
        out = ""
        if year:
            out += _num_to_ch(year) + "年"
        if month:
            out += _int_to_chinese_small(int(month)) + "月"
        if day:
            suffix = "日" if "日" in m.group(0) else "号"
            out += _int_to_chinese_small(int(day)) + suffix
        return out

    text = re.sub(r'(\d{4})?年?(\d{1,2})月(\d{1,2})(?:日|号)', date_repl, text)
    text = re.sub(r'(\d{4})年(\d{1,2})月', lambda m: f"{_num_to_ch(m.group(1))}年{_int_to_chinese_small(int(m.group(2)))}月", text)
    # 1.3) 年份区间：2024年-2025年 -> 二零二四年到二零二五年
    #      （必须放在裸年份规则之前，否则年份被单独转换后区间匹配会失败）
    text = re.sub(r'(\d+)年\s*[-—~～]\s*(\d+)年',
                  lambda m: f"{_num_to_ch(m.group(1))}年到{_num_to_ch(m.group(2))}年", text)
    # 1.4) 裸 4 位年份（后面不紧跟月）：逐位读，例如 2025年 -> 二零二五年
    #      （必须放在普通整数规则之前，否则会被当成位值数读成「二千零二十五年」）
    text = re.sub(r'(\d{4})年(?![\d月])', lambda m: f"{_num_to_ch(m.group(1))}年", text)
    # 1.5) 热线号码：数字后紧跟热线类词（报警/急救/热线/电话等）时逐位读，1 读成「幺」
    #      110 特例读「妖妖灵」；不在热线语境下的数字（如 110人）仍按位值读
    _HOTLINE_SUFFIX = r'(?:报警|急救|火警|热线|电话|咨询|服务|客服|专线|呼叫|报警电话)'
    text = re.sub(r'(\d+)(?=' + _HOTLINE_SUFFIX + r')',
                  lambda m: _hotline_to_ch(m.group(1)), text)
    # 1.6) 电话号码：逐位读，1 读成「幺」
    #   - 关键词在前：电话/手机/手机号/手机号码/联系电话/联系方式/座机/分机 等，后可跟「是/为/：」
    #   - 独立移动号码：1[3-9] 开头的 11 位（即使没有关键词也按位读，避免漏读）
    #   - 固话：3-4 位区号 + 连字符 + 7-8 位（仅在关键词后识别，避免误伤普通数字）
    _PHONE_KEYWORDS = r'(?:电话|手机|手机号|手机号码|联系电话|联系方式|座机|分机)'
    _PHONE_SEP = r'\s*(?:是|为|：|:)?\s*'
    _PHONE_BODY = r'(?:\+?\d{1,3}[- ]?)?(?:1[3-9](?:[- ]?\d){9}|\d{3,4}[- ]\d{7,8})'
    text = re.sub(
        r'(' + _PHONE_KEYWORDS + r')(' + _PHONE_SEP + r')(' + _PHONE_BODY + r')',
        lambda m: m.group(1) + m.group(2) + _hotline_to_ch(re.sub(r'[^\d]', '', m.group(3))),
        text,
    )
    # 独立移动号码（无关键词前缀也按位读，1->幺）；用前后非数字锚定，避免命中更长数字串
    text = re.sub(r'(?<!\d)(1[3-9](?:[- ]?\d){9})(?!\d)',
                  lambda m: _hotline_to_ch(re.sub(r'[^\d]', '', m.group(1))), text)

    # 2) 百分数，例如 5.6% -> 百分之五点六
    text = re.sub(r'(\d+(?:\.\d+)?)%', lambda m: f"百分之{_num_to_ch(m.group(1))}", text)

    # 3) 字母与数字之间用 - 或 _ 连接，例如 GPT-5.6 -> GPT五点六
    text = re.sub(r'([A-Za-z]+)[-_](\d+(?:\.\d+)?)', lambda m: f"{m.group(1)}{_num_to_ch(m.group(2))}", text)

    # 4) 版本号（两段以上小数点），例如 3.12.10 -> 三点一二点一零
    text = re.sub(r'\d+(?:\.\d+){2,}', lambda m: _num_to_ch(m.group(0)), text)

    # 5) 普通小数，例如 5.6 -> 五点六
    text = re.sub(r'\d+\.\d+', lambda m: _num_to_ch(m.group(0)), text)

    # 6) 普通整数：4 位及以下按位值读（二十五岁），5 位及以上按编号逐位读
    def int_repl(m):
        s = m.group(0)
        if len(s) <= 4:
            return _int_to_chinese_small(int(s))
        return _num_to_ch(s)

    text = re.sub(r'\d+', int_repl, text)
    return text


__all__ = ["normalize_text"]
