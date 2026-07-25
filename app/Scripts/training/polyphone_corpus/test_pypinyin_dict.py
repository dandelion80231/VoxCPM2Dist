# -*- coding: utf-8 -*-
"""对比 pypinyin 默认注音 vs 加载 pypinyin-dict 社区增强词典后的注音效果。
聚焦之前实测标错的 minority 读法。"""
from pypinyin import pinyin, Style, load_phrases_dict

# 待测样本: (句子, 期望的 minority 读法 字典 {字: 期望拼音}, 说明)
CASES = [
    ("他好钻研古籍",     {"好": "hao4"}, "好=喜爱义应 hao4(非 hao3)"),
    ("薄荷味道清凉",     {"薄": "bo2"},  "薄荷应 bo2(非 bao2)"),
    ("再盛一碗饭",       {"盛": "cheng2"}, "盛=动词装应 cheng2(非 sheng4)"),
    ("血淋淋的现场",     {"血": "xue4"},  "血淋淋应 xue4(非 xie3)"),
    ("银行发行债券",     {"行": "hang2"}, "银行应 hang2(非 xing2)"),
    ("他还了钱就走",     {"还": "huan2"}, "还=归还应 huan2(非 hai2)"),
    ("量一量尺寸",       {"量": "liang2"}, "量=测量应 liang2(非 liang4)"),
    ("放假通知",         {"假": "jia4"},  "放假应 jia4(非 jia3)"),
    ("兴奋地跳起来",     {"兴": "xing1"}, "兴奋应 xing1(非 xing4)"),
    ("大夫嘱咐吃药",     {"大": "dai4"},  "大夫应 dai4(非 da4)"),
    ("他 ROC 了",         {},             "控制组(无多音字冲突)"),
]

def annotate(sent, style=Style.TONE3):
    return pinyin(sent, style=style, heteronym=False)

def run(label):
    print("=" * 60)
    print(label)
    print("=" * 60)
    all_ok = True
    for sent, expect, note in CASES:
        syl = annotate(sent)
        flat = [s[0] for s in syl]
        ok_parts = []
        for ch, want in expect.items():
            # 找该字在注音里第一次出现的位置
            idx = sent.find(ch)
            got = flat[idx] if idx >= 0 else "?"
            ok = (got == want)
            ok_parts.append(f"{ch}:{got}{'' if ok else '✗应为'+want}")
            if not ok:
                all_ok = False
        status = "OK " if expect and all(got == want for ch, want in expect.items() for got in [flat[sent.find(ch)]]) else "   "
        print(f"{status} {sent:14s} -> {''.join(flat):28s} | {', '.join(ok_parts) if ok_parts else note}")
    print()
    return all_ok

# A: 默认
run("A. pypinyin 默认 (无 pypinyin-dict)")
# B: 加载 pypinyin-dict 社区增强词典 (CC-CEDICT 单字 + large_pinyin 词组)
from pypinyin_dict.pinyin_data import cc_cedict
from pypinyin_dict.phrase_pinyin_data import large_pinyin
cc_cedict.load()
large_pinyin.load()
run("B. 加载 pypinyin-dict 社区词典后")
print("结论: 见上方逐项对比。✗ 表示仍标错。")
