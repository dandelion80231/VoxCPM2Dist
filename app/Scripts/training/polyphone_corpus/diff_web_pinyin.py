#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把网页拼音工具(teacher-toolset)导出的 字(pinyin) 标注,
与我们的参考读法 (pypinyin phrases_dict 默认 + 主动 overlay) 逐字比对,
挑出所有【多音字分歧句】供人工重点复核。
参考读法 = pypinyin 默认(已含 phrases_dict) + 针对 minority 读法的主动 overlay。
overlay 覆盖: 盛(chéng)/血(xuè)/好(hào)/量(liáng)/还(huán)/假(jià)/行(háng)/兴(xīng)
以及同句双读中少数读法(盛一碗/好钻研 等)。
"""
import re, json, sys
from pypinyin import lazy_pinyin, pinyin, Style
DESKTOP = r"D:/电脑桌面/新建 文本文档.txt"
OUT_TXT  = "data/web_diff_review.txt"
OUT_JSON = "data/web_annotated_parsed.jsonl"
TOKEN = re.compile(r'([\u4e00-\u9fff]+)\(([^)]+)\)')
SENT_RE = re.compile(r'^\s*(\d+)[、.．]\s*(.*)$')
# 合法音节集合(从 pypinyin 词典全量抽取), 用于贪心切分合并拼音串
from pypinyin.pinyin_dict import pinyin_dict as _PD
_TONELESS = set()
for _v in _PD.values():
    for _item in ("," .join(_v) if isinstance(_v, (list, tuple)) else str(_v)).split(","):
        for _s in _item.split():
            _TONELESS.add(re.sub(r"[1-5]$", "", _s))
def _split_syllables(s):
    """把可能缺空格的拼音串(如 'dabai zi3')贪心切分为合法音节列表。"""
    out = []
    i = 0
    n = len(s)
    while i < n:
        hit = None
        for L in range(min(6, n - i), 0, -1):
            c = s[i:i+L]
            if c in _TONELESS or (c[-1].isdigit() and c[:-1] in _TONELESS):
                hit = c
                break
        if hit is None:
            return None  # 无法切分
        out.append(hit)
        i += len(hit)
    return out
# (短语, 目标多音字, 参考读法 tone3) —— 主动 overlay 候选清单
OVERLAY = [
    # 盛 → chéng (盛饭/盛汤/盛一碗)
    ("盛一碗","盛","cheng2"),("盛汤","盛","cheng2"),
    ("盛满","盛","cheng2"),
    # 血 → xuè (书面/书面语语素)
    ("血淋淋","血","xue4"),
    # 好 → hào (喜好义)
    ("好钻研","好","hao4"),
    ("好强","好","hao4"),
    ("爱好","好","hao4"),
    ("好学","好","hao4"),
    ("好奇","好","hao4"),
    ("好客","好","hao4"),
    ("好为人师","好","hao4"),
    ("好高骛远","好","hao4"),
    # 量 → liáng (计量/估量义, 2声)
    ("量一量","量","liang2"),
    ("量身高","量","liang2"),
    # 量 → 轻声 (打量/思量/商量 的 量 读轻声 liang5; v20 it2 用 '5' 表轻声)
    ("打量","量","liang5"),("思量","量","liang5"),("商量","量","liang5"),
    # 还 → huán (归还义)
    ("还款","还","huan2"),
    ("还家","还","huan2"),
    ("还了钱","还","huan2"),
    # 假 → jià (假期义)
    # 行 → háng (行业/行列义)
    ("分行","行","hang2"),
    ("一行","行","hang2"),
    ("太行","行","hang2"),
    # 会 → kuài (仅 会计/会稽 等少数专有/职事义; 会议/体会 等=huì 走默认)
    ("会计","会","kuai4"),("会稽","会","kuai4"),
    # 薄 → bò (薄荷专读 bò, 其中 荷 读轻声 he5; v20 it2 用 '5' 表轻声)
    ("薄荷","荷","he5"),
    # 踏 → tā (仅 踏实/踏踏实实 读 tā; 脚踏实地 的 踏 是"踩"义读 tà, 见下压回)
    ("踏实","踏","ta1"),("踏踏实实","踏","ta1"),
    ("脚踏实地","踏","ta4"),
    # 削 → xiāo (口语: 削皮/削苹果/削铅笔) / xuē (书面: 削减/剥削 走 pypinyin 默认)
    ("削苹果","削","xiao1"),("削果皮","削","xiao1"),
    # ===== DeepSeek 补充(一般多音字 + 历史人名异读, 2026-07-20 裁定) =====
    # 沓(纷至沓来) tà  — 一沓(dá,量词) 走 pypinyin 默认
    ("沓来","沓","ta4"),("沓至","沓","ta4"),
    # 和: 和泥/和面 huó; 和诗/唱和 hè; 和牌(麻将) hú  — 和平 hé 走默认
    ("和水泥","和","huo2"),("和砂浆","和","huo2"),
    ("和诗","和","he4"),
    ("和牌","和","hu2"),
    # 哄(起哄/一哄而散) hòng  — 哄骗 hǒng / 哄堂 hōng 走默认
    ("哄散","哄","hong4"),
    # 弹(弹奏/弹拨) tán  — 子弹/弹弓 dàn 已覆盖
    ("弹奏","弹","tan2"),("弹拨","弹","tan2"),("弹去","弹","tan2"),
    # 铺(铺开) pū  — 当铺 pù 已覆盖
    ("铺开","铺","pu1"),
    # 累: 累累 léi; 累年/累计 lěi  — 劳累 lèi 已覆盖
    ("累累","累","lei2"),("累年","累","lei3"),
    # 载: 载入/载录/记载 zǎi  — 载满/载客 zài 已覆盖
    ("载入","载","zai3"),("载录","载","zai3"),
    # 似(相似/似锦) sì  — 似的 shì 走默认
    ("似锦","似","si4"),
    # 空(空虚/空房间) kōng  — 空出/空闲 kòng 走默认
    ("空房间","空","kong1"),
    # 倒(难不倒/倒下) dǎo  — 倒水/倒车 dào 已覆盖
    ("难不倒","倒","dao3"),
    # 帖: 字帖/古帖 tiè  — 请帖 tiě 已覆盖
    ("古帖","帖","tie4"),
    # 划(划伤/划船) huá  — 划分/计划 huà 已覆盖
    ("划伤","划","hua2"),
    # 舍: 圈舍/舍人/宿舍 shè  — 舍弃 shě 已覆盖
    ("圈舍","舍","she4"),("舍人","舍","she4"),
    # 曲(歌曲/一曲) qǔ  — 弯曲 qū 走默认
    ("一曲","曲","qu3"),
    # 冠(冠绝/冠军) guàn  — 皇冠 guān 走默认
    ("冠绝","冠","guan4"),
    # 处(地处/处所) chù  — 处理/处暑 chǔ 已覆盖
    ("地处","处","chu3"),
    # 脏(肮脏) zāng  — 心脏 zàng 走默认
    ("脏旧","脏","zang1"),
    # 朴(质朴/古朴) pǔ  — 朴刀 pō 走默认
    ("质朴","朴","pu3"),("古朴","朴","pu3"),
    # 长(长板凳/公冶长/牟长 读 cháng)  — 增长/长辈 zhǎng 走默认
    ("长板凳","长","chang2"),("公冶长","长","chang2"),("牟长","长","chang2"),
    # 历史人名/古籍专有名词异读(精确短语, 不误伤)
    ("樊於期","於","yu1"),
    ("缪贤","缪","miao4"),("缪公","缪","mu4"),
    ("翟方进","翟","zhai2"),("翟义","翟","zhai2"),
    ("牟长","牟","mu4"),("牟子","牟","mou2"),
    ("丌氏","丌","qi2"),
    ("荥经","荥","ying2"),
    ("阚泽","阚","kan4"),("阚邑","阚","kan4"),
    ("卜商","卜","bu3"),
    ("佛道","佛","fo2"),
    ("蠡湖","蠡","li3"),
    # ===== 同规律补全 · 第二批(常用多音字 minority 读法, 2026-07-20) =====
    # —— 第一批 46 条的常用短语补全(同字同读, 只补高频短语) ——
    ("弹唱","弹","tan2"),
    ("累月","累","lei3"),
    ("似是","似","si4"),
    ("划破","划","hua2"),
    ("冠冕","冠","guan4"),
    # —— 高频常用多音字(minority 读法; 均逐短语精确匹配, 不影响其他语境) ——
    # 重chóng / 强qiǎng / 传zhuàn / 种zhòng
    ("重来","重","chong2"),
    # 为wèi / 分fèn / 间jiàn / 应yìng / 只zhǐ
    # 模mú / 称chèn / 差cī / 屏bǐng / 卷juǎn
    ("卷起","卷","juan3"),
    # 转zhuàn / 供gòng / 给jǐ / 吓hè / 觉jiào
    ("转动","转","zhuan4"),
    # 角jué / 系jì / 乐yuè / 率lǜ / 壳qiào
    ("系扣","系","ji4"),
    # 畜xù / 贾gǔ / 几jī / 场cháng / 露lòu
    ("场院","场","chang2"),
    # 落lào/là / 埋mán / 没mò / 散sǎn / 塞sè/sài
    # 挑tiǎo / 吐tù / 相xiàng / 咽yàn / 与yù / 中zhòng
    ("中肯","中","zhong4"),
    # 琢zuó / 钻zuàn / 作zuō / 调tiáo / 担dàn / 当dàng / 得děi
    ("琢磨","琢","zuo2"),
    ("调解","调","tiao2"),
    ("重担","担","dan4"),
    # 创chuāng / 冲chòng / 揣chuāi / 喝hè / 荷hè / 晃huàng / 混hún / 济jǐ
    ("混水","混","hun2"),("混蛋","混","hun2"),
    ("济济","济","ji3"),
    # 夹jiá / 监jiàn / 将jiàng / 降xiáng / 嚼jué / 解jiè/xiè / 结jiē / 禁jìn
    # 尽jìn / 劲jìng / 卡qiǎ / 看kān / 勒lēi / 擂lèi / 笼lǒng
    # 溜liù / 搂lǒu / 绿lù / 脉mò / 蔓wàn / 猫máo / 蒙mēng/měng
    ("一溜","溜","liu4"),
    ("绿营","绿","lu4"),
    ("猫腰","猫","mao2"),
    ("蒙古","蒙","meng3"),
    # 靡mǐ / 泥nì / 宁nìng / 拧nǐng / 弄lòng / 疟yào / 炮páo / 喷pèn
    ("靡靡","靡","mi3"),
    ("拧断","拧","ning3"),
    ("炮制","炮","pao2"),
    # 劈pǐ / 漂piào / 撇piě / 奇jī / 铅yán / 悄qiǎo / 翘qiáo / 切qiè
    # 茄jiā / 亲qìng / 区ōu / 圈juàn / 嚷rāng / 任rén
    ("姓区","区","ou1"),
    ("圈养","圈","juan4"),
    # 撒sǎ / 丧sāng / 扫sào / 色shǎi / 杉shā / 厦xià / 扇shān / 稍shào
    ("掉色","色","shai3"),("退色","色","shai3"),
    # 识zhì / 食sì / 属zhǔ / 术zhú / 数shǔ/shuò / 说shuì / 宿xiù
    ("标识","识","zhi4"),
    ("食余","食","si4"),("食邑","食","si4"),
    ("数一数","数","shu3"),
    # 苔tāi / 通tòng / 瓦wà / 巷hàng / 唯wěi / 尾yǐ / 尉yù / 旋xuàn
    ("唯唯诺诺","唯","wei3"),
    ("旋风","旋","xuan4"),
    # 要yāo / 饮yìn / 晕yūn / 扎zā/zhá / 炸zhá / 占zhān / 朝zhāo
    ("饮马","饮","yin4"),
    ("扎辫","扎","za1"),
    # 折shé/zhē / 挣zhēng / 艾yì / 便pián / 泊pō / 簸bò / 颤zhàn / 度duó
    # 恶wù/ě / 坊fáng / 否pǐ / 服fù / 更gèng / 勾gòu / 纶guān / 哈hǎ / 吭háng / 坷kě
       # 磨坊=mò fáng(坊阳平); 作坊=zuō fang(坊轻声)走默认 fang5
    ("服药","服","fu4"),
    ("引吭","吭","hang2"),
    # —— 2026-07-21 复核修正: 错规则改值 + 漏规则补充 ——
    ("舍弃","舍","she3"),   # 舍弃=shě(放弃); 原因"宿舍"跨标点粘连被误判 shè, 此规则后置以覆盖
       # 和药=huò(调药和匀); 原漏规则, 静默留 hé
]
# 高优先(我们训练最在意、网页最易错的 minority 读法)
HIGH = {"cheng2","hang2","hao4","liang2","xue4","huan2","jia4","xing1"}
# 仅变调变体、无义项区别的字, 从比对中排除(避免变调噪声)
SANDHI = {"一", "不"}
# 语法虚词/功能字: 技术上是多音字但几乎永远读固定音, 且非本 LoRA 要纠的内容多音字,
# 其"分歧"纯属干扰(的/了/着/子/们/地/得/过等), 排除以聚焦内容多音字。
# 注意: 还/将/当/没 等含内容义项的字【不】排除。
STOP = {"的", "了", "着", "子", "们", "地", "得", "过", "把", "被", "给",
        "都", "哪", "吗", "呢", "吧", "啊", "呀", "哇", "啦", "么", "嘛", "哩", "咯", "喽", "呀"}
poly_cache = {}
def is_poly(ch):
    if ch in poly_cache:
        return poly_cache[ch]
    try:
        vs = set()
        for v in pinyin(ch, style=Style.TONE3, heteronym=True)[0]:
            vs.add(v)
        res = len(vs) > 1
    except Exception:
        res = False
    poly_cache[ch] = res
    return res
def ref_pinyin(clean):
    """clean 仅含汉字序列, 返回逐字参考读法(tone3)。"""
    ref = lazy_pinyin(clean, style=Style.TONE3, neutral_tone_with_five=True)
    # 修正长度(极端情况)
    if len(ref) != len(clean):
        ref = (ref + ["?"]*len(clean))[:len(clean)]
    for phrase, ch, rd in OVERLAY:
        start = 0
        while True:
            pos = clean.find(phrase, start)
            if pos < 0:
                break
            # 短语内所有目标字位置都强制(支持 踏踏实实 双'踏' 等)
            for off, cc in enumerate(phrase):
                if cc == ch:
                    ref[pos+off] = rd
            start = pos + 1
    return ref
def parse_line(text):
    """返回 (clean, pys, err)。clean=汉字序列, pys=逐字拼音。"""
    chars, pys = [], []
    for m in TOKEN.finditer(text):
        cs = m.group(1)
        syls = m.group(2).split()
        if len(cs) != len(syls):
            # 合并拼音串兜底: 去空格后贪心切分
            joined = m.group(2).replace(" ", "")
            sp = _split_syllables(joined)
            if sp is None or len(sp) != len(cs):
                return None, None, f"token {cs!r}->{m.group(2)!r} 字数≠音节数"
            syls = sp
        for c, s in zip(cs, syls):
            chars.append(c); pys.append(s.lower())
    return "".join(chars), pys, None
def main():
    raw = open(DESKTOP, encoding="utf-8").read().split("\n")
    # 抓段落/轮次标题
    section = ""
    parsed = []          # (srcline, num, section, clean, pys, rawtext)
    parse_errs = []
    for i, line in enumerate(raw, 1):
        s = line.strip()
        if not s:
            continue
        if s.startswith("#") or re.match(r"^第.+轮", s) or s.startswith("轮次"):
            section = s
            continue
        m = SENT_RE.match(s)
        if not m:
            continue
        num = m.group(1)
        body = m.group(2)
        clean, pys, err = parse_line(body)
        if err:
            parse_errs.append((i, num, section, body[:30], err))
            continue
        if not clean:
            continue
        parsed.append((i, num, section, clean, pys, body))
    # 比对
    divergent = []   # (srcline, num, section, rawtext, [(char, web, ref, hi)], clean)
    total_poly_flags = 0
    for srcline, num, section, clean, pys, rawtext in parsed:
        ref = ref_pinyin(clean)
        if len(ref) != len(pys):
            parse_errs.append((srcline, num, section, rawtext[:30],
                               f"参考长度{len(ref)}≠网页长度{len(pys)}"))
            continue
        flags = []
        for j, (c, w, r) in enumerate(zip(clean, pys, ref)):
            if not is_poly(c) or c in SANDHI or c in STOP:
                continue
            # 归一化: 网页用 ü, pypinyin 用 v(如 lü4 vs lv4), 否则会误判为分歧
            w = w.replace("\u00fc", "v")
            r = r.replace("\u00fc", "v")
            w_letters = re.sub(r"[1-5]$", "", w)
            r_letters = re.sub(r"[1-5]$", "", r)
            web_has_tone = bool(re.search(r"[1-5]$", w))
            if web_has_tone:
                # 网页标了调: 字母或声调不同都算分歧
                if w != r:
                    hi = (r in HIGH) or (w in HIGH)
                    flags.append((c, w, r, hi))
            else:
                # 网页未标调: 仅当字母(元音/辅音)不同才算错, 声调不可靠不报
                if w_letters != r_letters:
                    hi = (r in HIGH) or (w in HIGH)
                    flags.append((c, w, r, hi))
        if flags:
            total_poly_flags += len(flags)
            divergent.append((srcline, num, section, rawtext, flags, clean))
    # 排序: 高优先在前
    divergent.sort(key=lambda d: (not any(f[3] for f in d[4]), d[0]))
    # 写出
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("# 网页拼音标注 vs 参考读法 分歧句复核清单（中立比对，供人工复核）\n")
        f.write(f"# 源文件: {DESKTOP}\n")
        f.write(f"# 句子总数: {len(parsed)}  分歧句: {len(divergent)}  多音字分歧点: {total_poly_flags}\n")
        f.write("# 标注: [字: 网=网页读法 / 参=参考读法]  —— 中立比对, 非判定网页必错\n")
        f.write("# 注意: 参考读法(phrases_dict+overlay)也可能在上下文消歧上出错(如'削减' xuē 网页对、参考错),\n")
        f.write("#       本清单仅列出【双方不一致处】供人工逐条复核, 请以语言学为准定夺。\n")
        f.write("# 🔴=高优先(涉及 minority 读法, 训练最敏感)  🟡=一般多音字\n")
        f.write("# 定位: 源行号@段落(原编号)  —— 回桌面文件按行号查原文\n\n")
        for srcline, num, section, rawtext, flags, clean in divergent:
            tag = "🔴" if any(x[3] for x in flags) else "🟡"
            diffs = "  ".join(f"{c}: 网{w}/参{r}" for c, w, r, _ in flags)
            f.write(f"【{tag}】行{srcline} @{section} (原{num})\n")
            f.write(f"  原文: {rawtext}\n")
            f.write(f"  分歧: {diffs}\n\n")
    # 解析结果落盘(供后续汇入 v23)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        for srcline, num, section, clean, pys, rawtext in parsed:
            rec = {"srcline": srcline, "num": num, "section": section,
                   "clean": clean, "web_pinyin": pys, "raw": rawtext}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"解析句子: {len(parsed)}  解析失败: {len(parse_errs)}")
    print(f"分歧句: {len(divergent)}  多音字分歧点: {total_poly_flags}")
    hi = sum(1 for d in divergent if any(x[3] for x in d[4]))
    print(f"  其中高优先(minority读法): {hi} 句")
    if parse_errs[:5]:
        print("解析异常样例:")
        for e in parse_errs[:5]:
            print("  ", e)
    print("输出:", OUT_TXT, " / ", OUT_JSON)
if __name__ == "__main__":
    main()