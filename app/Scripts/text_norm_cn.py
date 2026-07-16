# text_norm_cn.py — 中文数字/日期/电话/单位归一化（网页端与命令行后端共用）
#
# 将阿拉伯数字、版本号、百分号、日期、计量单位等转换为自然的中文读法；
# 处理热线号码（110->妖妖灵）、电话号码（1->幺）、年份区间（->到）等特例；
# 并参考 WeTextProcessing 补充：二/两归一化、负号/正负号、km/kg/℃ 等常见单位、
# 全角→半角归一化、编号关键词守卫（whitelist 思路）、英文缩写逐字母读。
# 纯标准库实现（仅依赖 re），可被 vox_web_ui.py 与 voxcpm_tts_v5_longtext.py 直接 import。
#
# 两个「白名单」扩展点（按需自行维护）：
#   - 编号关键词守卫 _ID_KEYWORDS：数字紧跟这些词时强制逐位读（订单号/编号/账号…），
#     新增关键词直接在正则交替里加（长词放前面）。
#   - 英文缩写「按单词读」白名单 _ACRONYM_AS_WORD：命中则不打散字母、交 G2P 当单词读
#     （NASA/Intel/Google…）；按单词读的 acronym 加全大写词即可，逐字母读的
#     initialism（FBI/IBM/UN…）切勿加入。

import re

_CN_DIGITS = "零一二三四五六七八九"


# 全角→半角时保留的中文标点（不转半角，避免破坏 TTS 的中文停顿）：
#   U+FF0C 「，」全角逗号（中文逗号）、U+FF0E 「．」全角句号（中文句号的另一种写法）。
# 注：中文句号「。」是 U+3002（不在 0xFF01-0xFF5E 区间，本就保留）；
#     中文顿号「、」U+3001、全角分号「；」U+FF1B 等如需保留可补进此集合。
_FULLWIDTH_PUNCT_KEEP = {0xFF0C, 0xFF0E}


def _fullwidth_to_halfwidth(s: str) -> str:
    """全角字符转半角（参考 WeTextProcessing fullwidth_to_halfwidth.tsv）：

    ０-９／Ａ-Ｚ／ａ-ｚ 数字字母、全角拉丁标点（！（）：；等）转半角，
    避免后续数字/字母规则漏判。仅转换 U+FF01–U+FF5E 区间与 Ideographic Space
    （U+3000）。_FULLWIDTH_PUNCT_KEEP 中的中文标点（，．）刻意保留，
    让下游中文 TTS 停顿更自然。"""
    out = []
    for ch in s:
        code = ord(ch)
        if code in _FULLWIDTH_PUNCT_KEEP:
            out.append(ch)
        elif code == 0x3000:
            out.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        else:
            out.append(ch)
    return "".join(out)


# 按「单词」读的拉丁专有名词（机构/组织/品牌名等）：命中则不打散字母，
# 交给下游 G2P 当英文单词读（如 NASA -> ['næsə]，而非逐字母 N-A-S-A）。
# 与下面的缩写拆字母(Z)互补：CPU/USB/GPT/QQ 等首字母缩写不在表中，仍逐字母读。
#
# 收录原则（参考 WeTextProcessing 的 char 思路 + 英文读音惯例）：
#   - 只收「按单词读」的 acronym：NATO/NASA/UNESCO/UNICEF/OPEC/ASEAN/APEC/WHO/
#     CERN/FIFA/INTERPOL/NAFTA、NOAA/ISO、COVID/SARS/AIDS，以及品牌名（Intel/Nvidia/
#     Google/Apple/Tesla/Sony/Samsung/Microsoft…）。品牌名以全大写出现时(如 GOOGLE/INTEL)
#     也须收录，否则 Z 规则会把整串拆成单字母。
#   - 刻意排除「逐字母读」的 initialism：FBI/CIA/BBC/IBM/UN/UK/EU/WTO/IMF/GPS/CEO/
#     USB/CPU/AMD/HP/BMW/LG/ASUS…（放进去会读成单词，错误）。
#   - 扩展：直接往对应分类里加全大写词即可。
_ACRONYM_AS_WORD = {
    # 国际/政府间组织（按单词读）
    "NASA", "NATO", "UNESCO", "UNICEF", "OPEC", "ASEAN", "APEC",
    "WHO", "CERN", "FIFA", "INTERPOL", "NAFTA",
    # 机构/标准组织（按单词读）
    "NOAA", "ISO",
    # 医学 acronym（按单词读）
    "COVID", "SARS", "AIDS",
    # 科技/互联网品牌（按单词读；全大写出现时也须保留）
    "INTEL", "NVIDIA", "SONY", "SAMSUNG", "QUALCOMM", "HUAWEI", "XIAOMI",
    "TENCENT", "ALIBABA", "BYTEDANCE", "GOOGLE", "MICROSOFT", "APPLE",
    "AMAZON", "FACEBOOK", "TESLA", "ORACLE", "SAP", "DELL", "LENOVO",
    "CISCO", "ADOBE", "PAYPAL", "VISA", "MASTERCARD", "NETFLIX", "UBER",
    "EBAY", "YAHOO", "LINKEDIN", "SPOTIFY", "TIKTOK", "TWITTER",
    "INSTAGRAM", "WHATSAPP", "YOUTUBE", "DISCORD", "REDDIT", "SNAPCHAT",
    "TELEGRAM", "WECHAT", "STRIPE", "AIRBNB", "NASDAQ",
    # 汽车/工业品牌（按单词读）
    "BOEING", "AIRBUS", "TOYOTA", "HONDA", "FERRARI", "AUDI", "VOLVO",
    "PEUGEOT", "HYUNDAI", "KIA", "SUBARU", "MAZDA", "NISSAN", "LEXUS",
    "PORSCHE", "LAMBORGHINI", "ROLLSROYCE", "MERCEDES", "VOLKSWAGEN",
    "FORD", "CHRYSLER", "NIO", "XPENG",
    # 其他消费/工业品牌（按单词读）
    "BAYER", "BOSCH", "SIEMENS", "PHILIPS", "NOKIA", "ERICSSON",
    "MOTOROLA", "MEDIATEK", "OPPO", "VIVO", "REALME",
}


# 计量单位映射（参考 WeTextProcessing tn/chinese/data/measure/units_*.tsv）
# 按长度降序排列，确保 km/min/mol 等更长单位优先于单字 m，避免误吞
_MEASURE_UNITS = [
    (r"km/h", "公里每小时"), (r"m/s", "米每秒"), (r"mph", "英里每小时"),
    (r"kWh", "千瓦时"), (r"kwh", "千瓦时"),
    (r"km²", "平方千米"), (r"km2", "平方千米"),
    (r"km³", "立方千米"), (r"km3", "立方千米"),
    (r"m²", "平方米"), (r"m2", "平方米"),
    (r"m³", "立方米"), (r"m3", "立方米"),
    (r"cm²", "平方厘米"), (r"cm2", "平方厘米"),
    (r"cm³", "立方厘米"), (r"cm3", "立方厘米"),
    (r"mm²", "平方毫米"), (r"mm2", "平方毫米"),
    (r"dm³", "立方分米"), (r"dm3", "立方分米"),
    (r"km", "公里"), (r"cm", "厘米"), (r"mm", "毫米"), (r"dm", "分米"), (r"m", "米"),
    (r"kg", "千克"), (r"mg", "毫克"), (r"ng", "纳克"), (r"μg", "微克"),
    (r"g", "克"), (r"t", "吨"),
    (r"ml", "毫升"), (r"L", "升"), (r"l", "升"),
    (r"min", "分钟"), (r"ms", "毫秒"), (r"ns", "纳秒"), (r"μs", "微秒"), (r"ps", "皮秒"),
    (r"h", "小时"), (r"s", "秒"),
    (r"°C", "摄氏度"), (r"℃", "摄氏度"), (r"°c", "摄氏度"), (r"ºC", "摄氏度"),
    (r"°F", "华氏度"), (r"℉", "华氏度"), (r"°f", "华氏度"), (r"ºF", "华氏度"),
    (r"°", "度"), (r"º", "度"),
    (r"kW", "千瓦"), (r"kw", "千瓦"), (r"MW", "兆瓦"), (r"mw", "毫瓦"), (r"w", "瓦"),
    (r"mV", "毫伏"), (r"mv", "毫伏"), (r"v", "伏特"),
    (r"GHz", "吉赫兹"), (r"ghz", "吉赫兹"),
    (r"MHz", "兆赫兹"), (r"mhz", "兆赫兹"),
    (r"kHz", "千赫兹"), (r"khz", "千赫兹"),
    (r"Hz", "赫兹"), (r"hz", "赫兹"),
    (r"GB", "吉字节"), (r"gb", "吉字节"),
    (r"TB", "太字节"), (r"tb", "太字节"),
    (r"MB", "兆字节"), (r"mb", "兆字节"),
    (r"GPa", "吉帕斯卡"), (r"gpa", "吉帕斯卡"),
    (r"MPa", "兆帕"), (r"mpa", "兆帕"),
    (r"kPa", "千帕"), (r"kpa", "千帕"),
    (r"Pa", "帕斯卡"), (r"pa", "帕斯卡"),
    (r"Mbps", "兆比特每秒"), (r"kbps", "千比特每秒"),
    (r"rpm", "转每分"), (r"dB", "分贝"), (r"db", "分贝"), (r"kcal", "千卡"),
    (r"mol", "摩尔"), (r"rad", "弧度"), (r"ha", "公顷"), (r"mi", "英里"),
    (r"ft", "英尺"), (r"yd", "码"), (r"lb", "磅"), (r"lbs", "磅"), (r"oz", "盎司"), (r"nm", "纳米"),
]
_MEASURE_ALT = "|".join(re.escape(u) for u, _ in sorted(_MEASURE_UNITS, key=lambda x: -len(x[0])))
_MEASURE_MAP = dict(_MEASURE_UNITS)


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


def _num_to_chinese_value(n: int) -> str:
    """把任意非负整数按中文位值读法转换（支持万/亿级），正确处理节间「零」。
    例如 13750->一万三千七百五十，27500->二万七千五百，10086->一万零八十六，67->六十七。"""
    if n < 10000:
        return _int_to_chinese_small(n)
    units = ["", "万", "亿", "兆"]
    # 从低到高拆成每 4 位一段，再从高到低拼接，节间补「零」
    sections = []
    while n > 0:
        sections.append(n % 10000)
        n //= 10000
    res = ""
    for i in range(len(sections) - 1, -1, -1):
        seg = sections[i]
        if seg == 0:
            if res and res[-1] != "零":
                res += "零"
            continue
        if res and seg < 1000 and res[-1] != "零":
            res += "零"
        res += _int_to_chinese_small(seg) + units[i]
    if res.endswith("零"):
        res = res[:-1]
    return res or "零"


def _decimal_to_ch(s: str) -> str:
    """小数转中文：10.5 -> 十点五，5.6 -> 五点六（整数部分位值读，小数部分逐位）。
    兼容全角句号 ．（如 1．2），先归一化为 ASCII 点再拆分。"""
    s = s.replace("．", ".")
    int_part, dec_part = s.split(".")
    int_val = _num_to_chinese_value(int(int_part)) if int_part != "0" else "零"
    dec = "".join(_CN_DIGITS[int(c)] for c in dec_part)
    return f"{int_val}点{dec}"


def _num_or_decimal(s: str) -> str:
    """整数位值读、小数走 _decimal_to_ch。"""
    return _decimal_to_ch(s) if "." in s else _num_to_chinese_value(int(s))


def normalize_text(text: str) -> str:
    """数字/编号归一化：将阿拉伯数字、版本号、百分号、日期等转换为中文读法，
    同时去掉字母与数字之间的连字符/下划线，避免 GPT-5.6 被读成「杠」。

    流程：先全角→半角（Y），再做各类 NSW 规则，最后英文缩写拆字母（Z）。"""

    # Y) 全角→半角（必须最先做，否则全角数字/字母会漏过后续所有规则）
    text = _fullwidth_to_halfwidth(text)

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

    # 1.2) 横线/斜杠/点分日期（4 位年）：2024-01-15 / 2024/1/15 / 2024.1.15
    def dash_date_repl(m):
        out = _num_to_ch(m.group(1)) + "年"
        out += _int_to_chinese_small(int(m.group(2))) + "月"
        out += _int_to_chinese_small(int(m.group(3))) + "日"
        return out

    text = re.sub(r'(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})', dash_date_repl, text)
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

    # 1.7) 温度：-10°C / 25℃ / 30° -> 零下十摄氏度 / 二十五摄氏度 / 三十度
    def temp_repl(m):
        neg, num, sym = m.group(1), m.group(2), m.group(3)
        val = _num_or_decimal(num)
        prefix = "零下" if neg else ""
        return f"{prefix}{val}摄氏度" if sym != "°" else f"{prefix}{val}度"

    text = re.sub(r'(?<!\d)(-?)(\d+(?:\.\d+)?)\s*(°C|℃|°)', temp_repl, text)

    # 1.8) 时间：14:30 -> 十四点三十分；12:05 -> 十二点零五分；14:30:25 -> 十四点三十分二十五秒
    def time_repl(m):
        h, mi, s = int(m.group(1)), int(m.group(2)), m.group(3)
        out = _int_to_chinese_small(h) + "点"
        out += (_num_to_ch(f"0{mi}") if mi < 10 else _int_to_chinese_small(mi)) + "分"
        if s is not None:
            ss = int(s)
            out += (_num_to_ch(f"0{ss}") if ss < 10 else _int_to_chinese_small(ss)) + "秒"
        return out

    text = re.sub(r'([0-1]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?', time_repl, text)

    # 1.9) 比值/比分：3:5 -> 三比五（时间规则已消费 HH:MM，这里处理剩余短数字比）
    text = re.sub(r'(\d{1,3})\s*[:：]\s*(\d{1,3})',
                  lambda m: f"{_num_to_chinese_value(int(m.group(1)))}比{_num_to_chinese_value(int(m.group(2)))}", text)

    # 1.10) 分数：3/4 -> 四分之三（横线/斜杠日期已先消费 4 位年格式，不冲突）
    text = re.sub(r'(-?)(\d+)\s*/\s*(\d+)',
                  lambda m: (("负" if m.group(1) else "")
                             + f"{_num_to_chinese_value(int(m.group(3)))}分之{_num_to_chinese_value(int(m.group(2)))}"), text)

    # 1.11) 范围：10~20 -> 十到二十；3-5 -> 三到五（排除已处理的年份区间/横线日期）
    text = re.sub(r'(?<!\d年)(\d{1,3})\s*[-~～]\s*(\d{1,3})(?!年)',
                  lambda m: f"{_num_to_chinese_value(int(m.group(1)))}到{_num_to_chinese_value(int(m.group(2)))}", text)

    # 1.12) 金额符号：¥100 -> 一百元；$10.5 -> 十点五美元
    def money_repl(m):
        sym, sign, num = m.group(1), m.group(2), m.group(3)
        val = _num_or_decimal(num)
        return f"{'负' if sign else ''}{val}{'美元' if sym == '$' else '元'}"

    text = re.sub(r'([¥￥$])\s*(-?)(\d+(?:\.\d+)?)', money_repl, text)

    # 1.13) 数学符号：数字间的 + × ÷ = -> 加/乘/除/等于（减号已在范围/字母连接中处理，不全局替换避免破坏版本号）
    text = re.sub(r'(\d)\s*=\s*(\d)', r'\1等于\2', text)
    text = text.replace("+", "加").replace("×", "乘").replace("÷", "除")

    # 2) 百分数：整数按位值读（67% -> 百分之六十七），小数逐位读（5.6% -> 百分之五点六）
    #    符号组同时认 ASCII -、U+2212 −、U+00B1 ±（正负）；必须放在负号规则之前，
    #    否则 ±5% 会被百分号规则先吃掉数字、负号规则再也接不到数字。
    def pct_repl(m):
        sign, s = m.group(1), m.group(2)
        core = f"百分之{_decimal_to_ch(s)}" if "." in s else f"百分之{_num_to_chinese_value(int(s))}"
        prefix = "正负" if sign == "±" else "负" if sign in ("-", "−") else ""
        return prefix + core

    text = re.sub(r'([-−±]?)\s*(\d+(?:\.\d+)?)%', pct_repl, text)

    # 1.14) 计量单位：数字 + 单位 -> 数值读法 + 中文单位
    #       参考 WeTextProcessing tn/chinese/data/measure/units_*.tsv
    def measure_repl(m):
        sign, num, unit = m.group(1), m.group(2), m.group(3)
        return f"{'负' if sign else ''}{_num_or_decimal(num)}{_MEASURE_MAP[unit]}"

    text = re.sub(
        r'(?<!\d)(-?)(\d+(?:\.\d+)?)\s*(' + _MEASURE_ALT + r')(?![A-Za-z0-9])',
        measure_repl, text,
    )

    # 1.15) 负号 / 正负号（裸数字；容器类规则已在各自内部处理符号）
    def neg_repl(m):
        sym, num = m.group(1), m.group(2)
        return ("正负" if sym == "±" else "负") + _num_or_decimal(num)

    text = re.sub(r'(?<![A-Za-z0-9])([-−±])\s*(\d+(?:\.\d+)?)', neg_repl, text)

    # 3) 字母与数字之间用 - 或 _ 连接，例如 GPT-5.6 -> GPT五点六
    text = re.sub(r'([A-Za-z]+)[-_](\d+(?:\.\d+)?)', lambda m: f"{m.group(1)}{_num_or_decimal(m.group(2))}", text)

    # 4) 版本号（两段以上小数点，含全角句号 ．），例如 3.12.10 -> 三点一二点一零
    text = re.sub(r'\d+(?:[.．]\d+){2,}', lambda m: _num_to_ch(m.group(0)), text)

    # 5) 普通小数（含全角句号 ．），例如 5.6 -> 五点六，10.5 -> 十点五，1．2 -> 一点二
    #    （句末全角句号由 Y 的 _FULLWIDTH_PUNCT_KEEP 保留为中文标点，不在此转换）
    text = re.sub(r'\d+[.．]\d+', lambda m: _decimal_to_ch(m.group(0)), text)

    # X) 编号关键词守卫（复刻 WeTextProcessing whitelist 思路）：
    #    数字紧跟 订单号/编号/账号/卡号/密码/手机/QQ/微信号/快递单号 等时强制逐位读，
    #    避免 9-12 位编号被当成数量级读成超长数值（与下面 cap=12 互补）。
    #    注意必须放在电话规则(1.6)之后、普通整数规则之前：手机号 11 位已由 1.6 用「幺」
    #    读，这里仅兜底非标准手机号写法（如「手机号 12345」）。
    #    长词前置（手机号/手机号码/快递单号 在 手机/单号 之前），避免被短词截断后漏接数字。
    _ID_KEYWORDS = r'(?:订单号|快递单号|手机号码|手机号|编号|账号|卡号|密码|手机|微信号|QQ号?|QQ|单号|工号|学号|证号|批号|货号)'
    text = re.sub(
        r'(' + _ID_KEYWORDS + r')\s*(\d+)',
        lambda m: m.group(1) + _num_to_ch(m.group(2)),
        text,
    )

    # 6) 普通整数：≤12 位按位值读（支持到亿/兆：2亿=200000000->两亿元），
    #    13 位及以上视为编号逐位读（身份证 18 位、超长单号等）。
    #    9-12 位「真实数量级」（如 2亿）受益；9-12 位「编号」已被上面的守卫拦截逐位读。
    def int_repl(m):
        s = m.group(0)
        if len(s) <= 12:
            return _num_to_chinese_value(int(s))
        return _num_to_ch(s)

    text = re.sub(r'\d+', int_repl, text)

    # 7) 二/两 归一化（参考 WeTextProcessing cardinal/measure）：
    #    二百->两百、二千->两千、二万->两万、二亿->两亿、二兆->两兆（仅最高位为 2 时）
    text = (text.replace("二百", "两百").replace("二千", "两千")
                .replace("二万", "两万").replace("二亿", "两亿").replace("二兆", "两兆"))

    # Z) 英文缩写拆字母（让下游 G2P 逐字母读）：连续 2+ 个大写字母间插空格，
    #    如 CPU->C P U、USB->U S B、GPT->G P T。
    #    - 机构/组织名等「按单词读」的专有名词（_ACRONYM_AS_WORD，如 NASA）不打散，
    #      交给 G2P 当英文词读（NASA -> ['næsə]），避免被读成 N-A-S-A。
    #    - 不破坏 GPT-5.6：规则 3 已把它变成「GPT五点六」，这里仅拆 GPT 三字为「G P T」，
    #      结果是「G P T五点六」，字母逐读 + 中文「五点六」，符合预期。
    #    - 不拆混合大小写单词（如 iPhone、macOS）：只匹配「前后非字母」的纯大写串。
    #    - 不拆单字母（如 A股 的 A）：要求 {2,}。
    text = re.sub(
        r'(?<![A-Za-z])([A-Z]{2,})(?![a-z])',
        lambda m: " ".join(m.group(1)) if m.group(1) not in _ACRONYM_AS_WORD else m.group(1),
        text,
    )

    return text


__all__ = ["normalize_text"]
