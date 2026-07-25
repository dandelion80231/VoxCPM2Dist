# -*- coding: utf-8 -*-
"""诊断听测页: (1) 这20句在520训练集里的it2强制拼音是否为正确的minority读法;
(2) base/0400/0800 波形时长是否真的不同(LoRA是否生效)."""
import json, wave, os

ROOT = r"D:/AI/Build/多音字"
CORPUS = os.path.join(ROOT, "data/v20_teacher_text.clean.jsonl")
SENT = os.path.join(ROOT, "listen_sentences.json")

# key -> (考察字, 期望minority拼音(数字调), 是否同声韵异调)
ANN = {
 "d01_ding":  ("丁", "zheng1", False),
 "d02_mo":    ("万", "mo4",    False),
 "d03_sang":  ("丧", "sang4",  True),
 "d04_chu":   ("处", "chu3",   True),
 "d05_li":    ("丽", "li2",    True),
 "d06_yue":   ("乐", "yue4",   False),
 "d07_sheng": ("乘", "sheng4", False),
 "d08_yu":    ("予", "yu2",    True),
 "d09_qing":  ("亲", "qing4",  False),
 "d10_qiu":   ("仇", "qiu2",   False),
 "d11_zong":  ("从", "zong4",  False),
 "d12_ling":  ("令", "ling2",  True),
 "d13_ang":   ("仰", "ang2",   False),
 "d14_jie":   ("价", "jie4",   False),
 "d15_ren":   ("任", "ren2",   True),
 "d16_kuai":  ("会", "kuai4",  False),
 "d17_zhuan": ("传", "zhuan4", False),
 "d18_ba":    ("伯", "ba4",    True),
 "d19_he":    ("何", "he4",    True),
 "d20_jia":   ("假", "jia4",   True),
}

# 读语料
corpus = {}
with open(CORPUS, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        corpus[d.get("sentence", "")] = d.get("it2_text", "")

sents = json.load(open(SENT, encoding="utf-8"))

def dur(path):
    try:
        w = wave.open(path, "rb")
        n = w.getnframes(); sr = w.getframerate()
        w.close()
        return round(n / sr, 2)
    except Exception as e:
        return f"ERR:{e}"

print(f"{'key':10} {'字':3} {'期望':7} {'异调':4} {'it2含正确读法':6}  base    0400   0800")
print("-" * 78)
for key, sent in sents:
    char, exp, toneonly = ANN[key]
    it2 = corpus.get(sent, "")
    ok = exp in it2.replace(" ", " ").split() or (exp in it2)
    # 在 it2 里找该字位置对应的拼音较麻烦, 这里简化为: expected pinyin 是否出现在 it2 中
    has = exp in it2
    b = dur(os.path.join(ROOT, "base", f"{key}.wav"))
    f4 = dur(os.path.join(ROOT, "0400", f"{key}.wav"))
    f8 = dur(os.path.join(ROOT, "0800", f"{key}.wav"))
    print(f"{key:10} {char:3} {exp:7} {'是' if toneonly else '否':4} {str(has):6}      {str(b):6} {str(f4):6} {str(f8):6}")

print()
print("注: 时长若 base/0400/0800 全部相等 -> LoRA 未生效; 不同 -> 生效(哪怕听感难辨).")
print("it2含正确读法=False -> 训练目标本身就是 majority(数据坑), LoRA 学错.")
