#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
OVERLAY 词典自检工具 —— 每次改完 diff_web_pinyin.py 的 OVERLAY 后跑一遍。

检查项:
  1. 死规则: 短语里不含目标字(永不触发, 无意义)
  2. 非法读音: 强制读法不是该字的合法读音(会被 build_v23 闸门拦, 但提前发现)
  3. 冗余规则: 与 pypinyin 默认读法一致(无害但可删, 留着当文档也行)
  4. 覆盖规则: 与 pypinyin 默认不一致(=我们强行断言, 真实风险面, 需人工确认)
  5. 重复规则: 同一 (短语,字) 出现多次

用法:
  python lint_overlay.py            # 打印汇总 + 写出 data/overlay_override_risk.txt
  python lint_overlay.py --strict   # 发现死规则/非法读音时以非0退出(可接 CI)

依赖: pypinyin, diff_web_pinyin.py(同目录)
"""
import re
import sys
from pypinyin import lazy_pinyin, pinyin, Style

try:
    import diff_web_pinyin as dw
except Exception as e:
    print("无法 import diff_web_pinyin:", e)
    sys.exit(2)

HAN = lambda s: "".join(c for c in s if "\u4e00" <= c <= "\u9fff")
TONEMARK = lambda rd: re.sub(r"[1-5]$", "", rd)


def valid_readings(ch):
    """该字所有合法去调音节集合(同源 pypinyin 异读表)。"""
    try:
        vs = set()
        for v in pinyin(ch, style=Style.TONE3, heteronym=True)[0]:
            vs.add(TONEMARK(v))
        return vs
    except Exception:
        return set()


def load_overlay():
    src = open("diff_web_pinyin.py", encoding="utf-8").read()
    m = re.search(r"OVERLAY\s*=\s*\[(.*?)\n\]", src, re.S)
    body = m.group(1)
    # 丢掉整行注释(避免被注释掉的旧规则被正则误抓), 保留行内注释
    body = "\n".join(
        ln for ln in body.splitlines() if not ln.strip().startswith("#")
    )
    return re.findall(r'\("([^"]*)","([^"]*)","([^"]*)"\)', body)


# 专有名词罕见读法白名单: pypinyin 异读表没收, 但语言学上正确。
# 这些读法虽不在 pypinyin 默认异读集里, 也不算"非法读音"。
PROPER_NOUN_WHITELIST = {
    "缪": {"mu", "miao", "miu", "mou"},   # 缪公(秦穆公)读 mù; 缪贤读 miào
    # 如后续发现其他专有名词罕见读法被误报, 在此追加: "字": {"合法去调音节", ...}
}



def main():
    strict = "--strict" in sys.argv
    rules = load_overlay()

    broken, invalid, agree, override, dup = [], [], [], [], []
    seen = {}
    for ph, ch, rd in rules:
        key = (ph, ch)
        if key in seen:
            dup.append((ph, ch, rd, seen[key]))
        seen[key] = rd

        if ch not in ph:
            broken.append((ph, ch, rd))
            continue
        vr = valid_readings(ch)
        if vr and TONEMARK(rd) not in vr and TONEMARK(rd) not in PROPER_NOUN_WHITELIST.get(ch, set()):
            invalid.append((ph, ch, rd, sorted(vr)))
            continue
        try:
            dflt_all = lazy_pinyin(HAN(ph), style=Style.TONE3, neutral_tone_with_five=True)
        except Exception:
            dflt_all = []
        pos = ph.index(ch)
        dflt = dflt_all[pos] if pos < len(dflt_all) else "?"
        if dflt == rd:
            agree.append((ph, ch, rd))
        else:
            override.append((ph, ch, rd, dflt))

    print(f"OVERLAY 总条数: {len(rules)}")
    print(f"  🔴 死规则(短语不含目标字): {len(broken)}")
    print(f"  🔴 非法读音(字无此音):     {len(invalid)}")
    print(f"  🟡 重复规则:               {len(dup)}")
    print(f"  ✓  与 pypinyin 默认一致:   {len(agree)}")
    print(f"  🟡 覆盖 pypinyin 默认:     {len(override)}  (风险面)")

    if broken:
        print("\n=== 🔴 死规则(应删除) ===")
        for t in broken:
            print("  ", t)
    if invalid:
        print("\n=== 🔴 非法读音(字根本没这个音) ===")
        for t in invalid:
            print("  ", t)
    if dup:
        print("\n=== 🟡 重复规则(保留一条即可) ===")
        for t in dup:
            print("  ", t)

    # 写出覆盖清单供人工复核
    with open("data/overlay_override_risk.txt", "w", encoding="utf-8") as f:
        f.write(f"# OVERLAY 覆盖 pypinyin 默认的风险清单 (共 {len(override)} 条)\n")
        f.write("# 凡是与 pypinyin 默认读法不同的规则, 都是我们'强行断言', 需人工确认正确。\n")
        f.write("# 之前所有 bug(发行/地处/空地/盛情/樊於期/作坊)都属于这一类。\n\n")
        for ph, ch, rd, dflt in override:
            f.write(f"{ph} · {ch}: pypinyin默认={dflt} → 强制={rd}\n")
    print("\n写出覆盖清单 -> data/overlay_override_risk.txt")

    if strict and (broken or invalid):
        sys.exit(1)


if __name__ == "__main__":
    main()
