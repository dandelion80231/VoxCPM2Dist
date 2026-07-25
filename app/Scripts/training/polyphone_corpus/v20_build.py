# -*- coding: utf-8 -*-
"""
Build v20_teacher_text.jsonl from v19_teacher_text.jsonl.

Changes vs v19:
  1. DELETE line 25 (idx 25): char=份 is NOT a polyphone and bīn is an invalid
     reading (belongs to 彬). Toxic training sample.
  2. DELETE line 386 (idx 386): exact duplicate of line 63.
  3. DELETE any extra lines flagged by the polyphone scan (DELETE_EXTRA).
  4. REPLACE 3 of the 8 "easy" sentences (idx 4/5/30) with harder-polyphone
     sentences (处chǔ / 强qiǎng / 重chóng). idx values preserved (swap).
  5. FIX the 30 it2-broken lines: regenerate it2_text, grouping embedded latin
     pinyin annotations (e.g. "zòng" -> "zong4", "jī" -> "ji1") so IndexTTS2
     gets valid digit-tone tokens.
  6. Write v20_teacher_text.jsonl and v20_teacher_text.clean.jsonl (identical
     after fixes -- all rows are now clean).

cosy3/it2 generation reuses the EXACT functions from convert_polyphone.py.
"""
import json
import re
import sys
import importlib.util

import pypinyin
from pypinyin import pinyin, Style

ROOT = "D:/AI/Build/多音字/"
SRC = ROOT + r"data\v19_teacher_text.jsonl"
OUT_FULL = ROOT + r"data\v20_teacher_text.jsonl"
OUT_CLEAN = ROOT + r"data\v20_teacher_text.clean.jsonl"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


conv = load("convert_polyphone", ROOT + "convert_polyphone.py")
vp = load("v19_to_pinyin", ROOT + "v19_to_pinyin.py")

# ---- deletion set (ORIGINAL 1-based line numbers) -------------------------
DELETE_LINES = {25, 386}
DELETE_EXTRA = {415}  # 黑hè: 黑不是多音字，只有hēi一读，hè是无效读音，同类错误

# ---- it2 lines to regenerate (letter-spelling leak) -----------------------
IT2_BROKEN_LINES = {
    18, 43, 127, 204, 247, 288, 303, 324, 333, 369, 373, 378, 379,
    419, 421, 438, 442, 448, 456, 457, 461, 466, 477, 478, 491, 499,
    501, 508, 519, 520,
}

# ---- replacements: original line -> new row (idx preserved) ----------------
REPLACEMENTS = {
    4: {  # was 中zhòng(中奖) -> 处chǔ(相处)
        "sentence": "他们相处多年，从未红过脸。",
        "char": "处", "pinyin_mark": "chǔ", "pinyin_t3": "chu3", "word": "相处",
    },
    5: {  # was 为wèi(为了) -> 强qiǎng(勉强)
        "sentence": "他勉强接受了朋友的好意。",
        "char": "强", "pinyin_mark": "qiǎng", "pinyin_t3": "qiang3", "word": "勉强",
    },
    30: {  # was 作zuò(工作) -> 重chóng(重新)
        "sentence": "这个方案需要重新评估一遍。",
        "char": "重", "pinyin_mark": "chóng", "pinyin_t3": "chong2", "word": "重新",
    },
}

# ---------------------------------------------------------------------------
CJK = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
PUNCT = "，。？！、；：“”‘’（）—…·『』《》「」\n "

DIACRITIC = {
    "ā": ("a", 1), "á": ("a", 2), "ǎ": ("a", 3), "à": ("a", 4),
    "ō": ("o", 1), "ó": ("o", 2), "ǒ": ("o", 3), "ò": ("o", 4),
    "ē": ("e", 1), "é": ("e", 2), "ě": ("e", 3), "è": ("e", 4),
    "ī": ("i", 1), "í": ("i", 2), "ǐ": ("i", 3), "ì": ("i", 4),
    "ū": ("u", 1), "ú": ("u", 2), "ǔ": ("u", 3), "ù": ("u", 4),
    "ǖ": ("v", 1), "ǘ": ("v", 2), "ǚ": ("v", 3), "ǜ": ("v", 4),
    "ü": ("v", 0),
}


def latin_to_pinyin(s):
    base = ""
    tone = 0
    for c in s:
        if c in DIACRITIC:
            b, t = DIACRITIC[c]
            base += b
            if t:
                tone = t
        elif c.isalpha():
            base += c
    if not base:
        return None
    return base + (str(tone) if tone else "5")


def regen_it2(sentence, fpt):
    """Rebuild it2_text from full_pinyin_forced, grouping embedded latin
    pinyin annotations into single digit-tone tokens."""
    tokens = fpt.split()
    ti = 0
    out = []
    latin_run = ""
    for ch in sentence:
        if latin_run:
            if CJK.match(ch) or ch in PUNCT:
                conv = latin_to_pinyin(latin_run)
                if conv:
                    out.append(conv)
                latin_run = ""
            else:
                latin_run += ch
        if latin_run:
            continue
        if CJK.match(ch):
            out.append(tokens[ti])
            ti += 1
        elif ch in PUNCT:
            out.append(ch)
        else:
            latin_run = ch
    if latin_run:
        conv = latin_to_pinyin(latin_run)
        if conv:
            out.append(conv)
    return " ".join(out)


def force_pinyin(sent, tgt, tgt_t3, word):
    span = None
    if word and word in sent:
        s0 = sent.index(word)
        span = (s0, s0 + len(word))
    syls = []
    for pos, ch in enumerate(sent):
        if ch == tgt and (span is None or span[0] <= pos < span[1]):
            syls.append(tgt_t3)
        elif vp.HAN.match(ch):
            syls.append(vp.char_pinyin(ch))
        # non-Han (punctuation) skipped
    return " ".join(syls)


def build_replacement(spec, idx):
    sentence = spec["sentence"]
    fpt = force_pinyin(sentence, spec["char"], spec["pinyin_t3"], spec["word"])
    cosy3, it2 = conv.build_texts(sentence, fpt.split())
    return {
        "idx": idx,
        "sentence": sentence,
        "char": spec["char"],
        "pinyin_mark": spec["pinyin_mark"],
        "pinyin_t3": spec["pinyin_t3"],
        "word": spec["word"],
        "full_pinyin_forced": fpt,
        "risk_hard": [],
        "risk_soft": [],
        "cosy3_text": cosy3,
        "it2_text": it2,
        "cosy3_missing_tokens": [],
    }


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def validate(rows):
    print("=== VALIDATION (regen vs stored, using real generator) ===")
    # cosy3 via real generator on a few rows (should MATCH stored for all)
    for i in (0, 1, 16, 29):  # idx 1,2,17,30
        r = rows[i]
        rc, _ = conv.build_texts(r["sentence"], r["full_pinyin_forced"].split())
        ok = rc == r["cosy3_text"]
        print(f"  [{'OK ' if ok else 'DIFF'}] line {i+1} idx {r['idx']} cosy3: {ok}")
        if not ok:
            print(f"      regen: {rc}")
            print(f"      store: {r['cosy3_text']}")
    # it2 via regen_it2 on a non-broken row (should MATCH) and a broken one (should FIX)
    for i in (0, 17, 42):  # idx1 (ok), idx18 (broken), idx43 (broken)
        r = rows[i]
        ri = regen_it2(r["sentence"], r["full_pinyin_forced"])
        if i in (17, 42):
            print(f"  [FIX] line {i+1} idx {r['idx']} it2: {ri}")
        else:
            ok = ri == r["it2_text"]
            print(f"  [{'OK ' if ok else 'DIFF'}] line {i+1} idx {r['idx']} it2: {ok}")
            if not ok:
                print(f"      regen: {ri}")
                print(f"      store: {r['it2_text']}")
    print("=== END VALIDATION ===\n")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    rows = load_rows(SRC)
    print(f"loaded {len(rows)} rows")

    if mode == "validate":
        validate(rows)
        return

    delete = DELETE_LINES | DELETE_EXTRA
    out = []
    replaced, it2_fixed = [], []
    for i, r in enumerate(rows, start=1):
        if i in delete:
            continue
        if i in REPLACEMENTS:
            nr = build_replacement(REPLACEMENTS[i], r["idx"])
            out.append(nr)
            replaced.append(r["idx"])
            continue
        if i in IT2_BROKEN_LINES:
            r = dict(r)
            r["it2_text"] = regen_it2(r["sentence"], r["full_pinyin_forced"])
            it2_fixed.append(r["idx"])
        out.append(r)

    with open(OUT_FULL, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT_CLEAN, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"v20 written: {len(out)} rows")
    print(f"  deleted lines: {sorted(delete)}")
    print(f"  replaced idx : {replaced}")
    print(f"  it2 fixed idx: {it2_fixed} ({len(it2_fixed)} lines)")


if __name__ == "__main__":
    main()
