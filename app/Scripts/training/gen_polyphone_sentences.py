#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
从 xiaohk/pinyin_data 的 polyphone.json 自动生成「多音字纠正 LoRA」训练句清单。

数据来源（已 vendoring 到本目录 polyphone_data.json）：
  https://github.com/xiaohk/pinyin_data
  结构： { "字": { "读音": [ [词头列表], [词中列表], [词尾列表] ] } }
  例：   { "会": { "huì":[["会合"],[],["都会"]], "kuài":[["会计"],[],["财会"]] } }

为什么用它（而不是直接用 chinese-dictionary 的 polyphone.json）：
  - chinese-dictionary 的 polyphone.json 只有 char -> [pinyin 候选]，没有任何例句，
    无法告诉我们「什么语境读哪个音」，不能用于生成训练句。
  - 本项目用 xiaohk 的版本：每个读音都附带示例词，且示例词里目标字必然读该音，
    因此只要把示例词放进自然句、由朗读者按正确读音读出，模型就能学到「字->正确读音」。

注意（关键约束，见 README 多音字章节）：
  - VoxCPM2 是 tokenizer-free，**不支持 `{pinyin}` 音素注入**，pinyin 字符串本身无法喂给模型。
  - 本脚本产出的是「纯中文字符句」，靠录音里的正确读音来监督模型；pinyin 仅用于挑选示例词。
  - 训练请用 LM-only LoRA（enable_lm=true, enable_dit=false）：
    读音映射在语言模型（LM）里，音色由推理时 reference_wav_path 克隆，二者通道隔离，
    故改 LM 不动 DiT 即可「只修多音字、不改音色」。

用法：
  python gen_polyphone_sentences.py                 # 写 ../polyphone_sentences.txt
  python gen_polyphone_sentences.py --out out.txt --max-words 2
  python gen_polyphone_sentences.py --limit 250     # 上限条数（随机均匀抽样以保持覆盖）
"""
import argparse
import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SRC = os.path.join(HERE, "polyphone_data.json")
DEFAULT_OUT = os.path.join(HERE, "..", "polyphone_sentences.txt")

# 自然句模板（{w}=示例词）。轮换使用以增加多样性，避免模型过拟合到模板句式。
TEMPLATES = [
    "请跟我读：「{w}」。",
    "「{w}」是一个常用词语。",
    "他正在练习说「{w}」。",
    "我们在课上学了「{w}」。",
    "你能用「{w}」造一个句子吗？",
    "这句话里出现了「{w}」。",
]

# 过滤：单字词无法提供消歧上下文（字本身读音仍模糊），跳过。
MIN_WORD_LEN = 2


def collect(src_path, max_words_per_reading):
    """返回 [(char, reading, word, sentence), ...]，已按模板生成句子。"""
    with open(src_path, encoding="utf-8") as f:
        data = json.load(f)

    out = []
    tpl_idx = 0
    for char in sorted(data.keys()):
        readings = data[char]
        for reading in sorted(readings.keys()):
            groups = readings[reading]
            # groups: [词头, 词中, 词尾]
            words = []
            for grp in groups:
                for w in grp:
                    if len(w) >= MIN_WORD_LEN and w not in words:
                        words.append(w)
            if not words:
                continue
            chosen = words[:max_words_per_reading]
            for w in chosen:
                tpl = TEMPLATES[tpl_idx % len(TEMPLATES)]
                tpl_idx += 1
                out.append((char, reading, w, tpl.format(w=w)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=DEFAULT_SRC, help="polyphone_data.json 路径")
    ap.add_argument("--out", default=DEFAULT_OUT, help="输出训练句清单")
    ap.add_argument("--max-words", type=int, default=2,
                    help="每个(字,读音)最多取几个示例词（默认2）")
    ap.add_argument("--limit", type=int, default=0,
                    help="输出条数上限（0=不限制；超出则均匀抽样保持覆盖）")
    ap.add_argument("--seed", type=int, default=20260717)
    args = ap.parse_args()

    if not os.path.exists(args.src):
        print(f"[ERR] 找不到数据源 {args.src}，请先下载 polyphone_data.json", file=sys.stderr)
        sys.exit(1)

    rows = collect(args.src, args.max_words)
    # 去重句子（保留首次出现）
    seen = set()
    uniq = []
    for r in rows:
        if r[3] not in seen:
            seen.add(r[3])
            uniq.append(r)
    rows = uniq

    if args.limit and len(rows) > args.limit:
        random.seed(args.seed)
        # 均匀抽样：跨所有 (char) 轮换抽取，避免只覆盖前面几个字
        by_char = {}
        for r in rows:
            by_char.setdefault(r[0], []).append(r)
        chars = sorted(by_char.keys())
        picked = []
        i = 0
        while len(picked) < args.limit:
            c = chars[i % len(chars)]
            bucket = by_char[c]
            if bucket:
                picked.append(bucket.pop(0))
            i += 1
            if all(not v for v in by_char.values()):
                break
        rows = picked

    out_path = os.path.abspath(args.out)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"# 自动生成 · 多音字纠正 LoRA 训练句 · 共 {len(rows)} 句\n")
        f.write("# 数据源：xiaohk/pinyin_data polyphone.json（读音->示例词）\n")
        f.write("# 录制/合成时请按正确读音朗读，句序与音频 001.wav.. 一一对应\n")
        for _, _, _, sent in rows:
            f.write(sent + "\n")

    nchar = len({r[0] for r in rows})
    print(f"[OK] 写出 {len(rows)} 句，覆盖 {nchar} 个多音字 -> {out_path}")


if __name__ == "__main__":
    main()
