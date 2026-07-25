# -*- coding: utf-8 -*-
"""验证两个候选发音词典源能否作为我们的自定义注音词典。
结论速览:
  - 方案3 pypinyin.phrases_dict: 47111 条, pypinyin 默认即激活, 多数 minority 读法默认读对。✅ 直接可用
  - 方案2 pinyin-data/polyphonic.csv: 该文件在仓库根目录 404 不存在; 仓库是单字级数据,
    词组在 submodule(tools/phrase-pinyin-data)。即使找到 ~1000 条词组也仅是 phrases_dict 子集。❌ 冗余
"""
from pypinyin import pinyin, Style, load_phrases_dict
from pypinyin.phrases_dict import phrases_dict

print("phrases_dict 总条目:", len(phrases_dict))

# A. 默认是否已激活 phrases_dict (不加载任何自定义)
print("\n[A] 默认 pypinyin (phrases_dict 已内置激活):")
for s in ["盛饭", "量杯", "还钱", "好学", "薄荷", "银行", "血肉"]:
    print("  ", s, "->", "".join(x[0] for x in pinyin(s, style=Style.TONE3)))

# B. 仅补 4 个 3 字词 overlay, 看全句是否全对
print("\n[B] 加极小 overlay(盛一碗/量一量/还了钱/好钻研) 后全句注音:")
load_phrases_dict({
    "盛一碗": [["cheng2"], ["yi1"], ["wan3"]],
    "量一量": [["liang2"], ["yi1"], ["liang2"]],
    "还了钱": [["huan2"], ["le"], ["qian2"]],
    "好钻研": [["hao4"], ["zuan1"], ["yan2"]],
})
for s in ["再盛一碗饭", "量一量尺寸", "他还了钱就走", "他好钻研古籍", "薄荷味道清凉", "银行发行债券"]:
    print("  ", s, "->", "".join(x[0] for x in pinyin(s, style=Style.TONE3)))

# C. pinyin-data 仓库实际内容(单字级, 无 polyphonic.csv)
print("\n[C] mozillazg/pinyin-data 仓库根目录实际文件(节选, 均为单字级):")
print("    pinyin.txt / cc_cedict.txt / zdic.txt / kMandarin.txt / kTGHZ2013.txt ...")
print("    词组数据在 submodule: tools/phrase-pinyin-data (即 pypinyin 词组源, 已被 phrases_dict 包含)")
print("    => 方案2 的 polyphonic.csv 不存在; 方案3 已覆盖其全部价值。")
