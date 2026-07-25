#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_v23.py — 把筛选后的 1501 句, 用锁定的参考读法(OVERLAY + phrases_dict)
强制成 it2 拼音, 产出 v23_teacher_text.jsonl, 供 IndexTTS2 合成。

流水线:
  sentence -> clean(仅汉字) -> ref_pinyin(clean) [OVERLAY 强制] -> pinyin_list
          -> convert_polyphone.build_texts(sentence, pinyin_list) -> it2_text / cosy3_text

断言闸门(防投毒):
  1) len(ref) == len(clean)  (逐字对齐, 不允许 '?')
  2) 每个多音字被强制的读法(去调后)必须落在该字合法读音集合内
不符合则记录到 assert_failures, 不写入最终 jsonl(人工复核后再补)。
"""
import re
import json
import os
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from pypinyin import pinyin, Style
from convert_polyphone import build_texts
from diff_web_pinyin import ref_pinyin

SRC = os.path.join(HERE, "data", "corpus_sel_poly_proper.txt")
OUT = os.path.join(HERE, "data", "v23_teacher_text.jsonl")
ASSERT_BAD = os.path.join(HERE, "data", "v23_assert_failures.txt")

LINE_RE = re.compile(r"^\s*([Ww]?\d+)\s*[、.．]\s*(.+?)\s*$")

def han_only(s):
    return "".join(c for c in s if "\u4e00" <= c <= "\u9fff")

def toneless(syl):
    """去声调(数字调或声调符号)与 ü->v 归一, 仅留声母韵母。"""
    s = re.sub(r"[1-5]$", "", syl)           # 去数字调
    out = []
    for ch in s:
        for d in unicodedata.normalize("NFD", ch):
            if "\u0300" <= d <= "\u036f":      # 丢弃组合声调符号
                continue
            out.append(d)
    return "".join(out).lower().replace("ü", "v").replace("\u0261", "g")

# 合法读音集合: 用 pypinyin 自身异读表(与 ref_pinyin 同源, 自动含轻声/ü/标准 g),
# 绕过 polyphone_full.json 的编码瑕疵(花体 g / ü 丢信息 / 漏轻声)。
# 专有名词/译名白名单: pypinyin 异读表未收录, 但确为正确读法(如 缪公=秦穆公 读 mù),
# 闸门只防"投毒式"错音, 这些合法读音应被放行, 否则会误删正确句子。
WHITELIST = {
    "缪": {"mu", "miu", "mou", "miao"},
}

_valid_cache = {}
def valid_readings(ch):
    """该字所有合法去调音节集合(含白名单); 无数据返回空(跳过校验)。"""
    if ch in _valid_cache:
        return _valid_cache[ch]
    vs = set()
    try:
        for v in pinyin(ch, style=Style.TONE3, heteronym=True)[0]:
            vs.add(toneless(v))
    except Exception:
        pass
    vs |= WHITELIST.get(ch, set())
    _valid_cache[ch] = vs
    return vs

# ---------------------------------------------------------------------------
def main():
    records, failures = [], []
    gidx = 0
    with open(SRC, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.rstrip("\n")
            if not line.strip():
                continue
            m = LINE_RE.match(line)
            if not m:
                print(f"[warn] 无法解析行: {line[:40]!r}")
                continue
            num, sentence = m.group(1), m.group(2)
            clean = han_only(sentence)
            ref = ref_pinyin(clean)

            # ---- 断言闸门(防投毒) ----
            # 全局唯一 idx = 文件顺序 1..N(避免段号碰撞); src/tag 保留溯源信息。
            # 该字若有 pypinyin 异读表, 则强制读法(去调后)必须落在其合法读音集合内。
            bad = []
            if len(ref) != len(clean):
                bad.append(f"长度不对 ref{len(ref)}≠clean{len(clean)}")
            else:
                for i, (c, syl) in enumerate(zip(clean, ref)):
                    if syl == "?":
                        bad.append(f"位置{i}字{c}读法'?'(长度错位)")
                        continue
                    vr = valid_readings(c)
                    if vr and toneless(syl) not in vr:
                        bad.append(f"位置{i}字{c}强制'{syl}'不在合法读音{vr}")
            if bad:
                failures.append((num, sentence, bad))
                continue

            gidx += 1
            cosy_text, it2_text = build_texts(sentence, ref)
            records.append({
                "idx": gidx,
                "src": num,
                "tag": "W" if num[:1] in ("W", "w") else "num",
                "sentence": sentence,
                "clean": clean,
                "ref_pinyin": ref,
                "it2_text": it2_text,
                "cosy3_text": cosy_text,
            })

    # -----------------------------------------------------------------------
    with open(OUT, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(ASSERT_BAD, "w", encoding="utf-8") as f:
        for num, sentence, bad in failures:
            f.write(f"[{num}] {sentence}\n  -> {'; '.join(bad)}\n")

    print("=" * 56)
    print(f"输入筛选句: {len(records)+len(failures)}")
    print(f"  通过断言, 写入 v23: {len(records)}")
    print(f"  断言失败(未写入, 待人工): {len(failures)}")
    print(f"写出 -> {OUT}")
    if failures:
        print(f"失败明细 -> {ASSERT_BAD}")
        for num, sentence, bad in failures[:10]:
            print(f"  [{num}] {sentence}  | {bad}")
    # 抽样展示前 5 句 it2_text 供人工看一眼
    print("-" * 56)
    print("样本(it2_text):")
    for r in records[:5]:
        print(f"  [{r['idx']}] {r['sentence']}")
        print(f"       {r['it2_text']}")
    print("=" * 56)


if __name__ == "__main__":
    main()
