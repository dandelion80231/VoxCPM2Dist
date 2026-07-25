#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v19 -> 拼音输入转换器（音频合成前置，与引擎无关）。

核心产出：每句的「目标字强制正确拼音」——合成时把【原句中文】喂给 TTS 引擎，
只对【目标多音字】做拼音覆盖，其余多音字交给引擎自身的上下文 g2p（比 pypinyin 强）。
这样彻底绕开「435 句含其他多音字」的难题：我们不替引擎决定其余字的读音。

同时附带：
  - full_pinyin_forced：pypinyin 打底 + 目标字强制（全拼音兜底方案用，一般不用）
  - risk 分析：其余多音字里 pypinyin 默认读音是否「非法(不在字典)」(hard) 或「合法但可能语境错」(soft)
"""
from __future__ import annotations
import json, re, sys
from pypinyin import pinyin, Style
from pypinyin.contrib.tone_convert import to_tone3

ROOT = "data"
V19 = f"{ROOT}/step3_train_final_v19.jsonl"
FULL = f"{ROOT}/polyphone_full.json"
OUT = f"{ROOT}/v19_pinyin_input.jsonl"

HAN = re.compile(r"[一-鿿]")

def norm_t3(s: str) -> str:
    """带调字母/数字调 -> 基元音(ü->v)+末尾声调数字(无调补5)。"""
    s = to_tone3(s)
    if s and not s[-1].isdigit():
        s = s + "5"
    return s.replace("ü", "v").replace("u:", "v")

def char_pinyin(ch: str) -> str:
    if not HAN.match(ch):
        return ch  # 非汉字原样返回（标点/数字/英文）
    return norm_t3(pinyin(ch, style=Style.TONE3, heteronym=False)[0][0])

def main():
    v19 = [json.loads(l) for l in open(V19, encoding="utf-8") if l.strip()]
    full = json.load(open(FULL, encoding="utf-8"))
    # 字典：char -> 合法读音集合(TONE3)
    valid = {}
    for r in full:
        ch = r["char"]
        ps = {norm_t3(p) for p in r.get("pinyin", [])}
        if ps:
            valid[ch] = ps

    out = []
    stat = {"hard": 0, "soft": 0, "clean": 0}
    for i, r in enumerate(v19, 1):
        sent = r["sentence"]
        tgt = r["char"]
        tgt_t3 = norm_t3(r["pinyin"])           # 目标字强制正确音(TONE3)
        word = r.get("word", "")

        # 定位 word 区间，仅在该区间内强制目标字（其余出现交给引擎）
        span = None
        if word and word in sent:
            s0 = sent.index(word)
            span = (s0, s0 + len(word))
        force_pos = set()
        for pos, ch in enumerate(sent):
            if ch == tgt and (span is None or span[0] <= pos < span[1]):
                force_pos.add(pos)

        # full_pinyin_forced（兜底方案）：逐字拼音，目标字强制
        syls = []
        for pos, ch in enumerate(sent):
            if pos in force_pos:
                syls.append(tgt_t3)
            elif HAN.match(ch):
                syls.append(char_pinyin(ch))
            # 非汉字(标点等) 跳过，不进拼音串
        full_py = " ".join(syls)

        # 其余多音字风险分析
        hard, soft = [], []
        for pos, ch in enumerate(sent):
            if ch == tgt or not HAN.match(ch):
                continue
            if ch in valid and len(valid[ch]) > 1:
                d = char_pinyin(ch)            # pypinyin 默认
                if d not in valid[ch]:
                    hard.append((ch, d))       # 非法读音 -> 硬错
                else:
                    soft.append((ch, d))       # 合法但可能语境错
        if hard:
            stat["hard"] += 1
        elif soft:
            stat["soft"] += 1
        else:
            stat["clean"] += 1

        out.append({
            "idx": i,
            "sentence": sent,
            "char": tgt,
            "pinyin_mark": r["pinyin"],
            "pinyin_t3": tgt_t3,           # ★ 合成驱动核心字段
            "word": word,
            "full_pinyin_forced": full_py,
            "risk_hard": hard,
            "risk_soft": soft,
        })

    with open(OUT, "w", encoding="utf-8") as f:
        for o in out:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")

    print(f"[完成] {len(out)} 句 -> {OUT}")
    print(f"[统计] 其余多音字风险: 含硬错(hard)={stat['hard']}  仅软错(soft)={stat['soft']}  干净(clean)={stat['clean']}")
    print("\n=== 样例(前6句, 看转换对不对) ===")
    for o in out[:6]:
        print(f"\n#{o['idx']} 目标 {o['char']}{o['pinyin_mark']}→{o['pinyin_t3']} [{o['word']}]")
        print(f"  原句: {o['sentence']}")
        print(f"  全拼音(兜底): {o['full_pinyin_forced']}")
        if o['risk_hard']:
            print(f"  ⚠ hard: {o['risk_hard']}")
        if o['risk_soft']:
            print(f"  · soft: {o['risk_soft'][:5]}{'...' if len(o['risk_soft'])>5 else ''}")

if __name__ == "__main__":
    main()
