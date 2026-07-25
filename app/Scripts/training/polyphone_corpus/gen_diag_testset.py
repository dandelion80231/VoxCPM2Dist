#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立测试集生成器（多音字 LoRA 诊断基准）

产出一份「与训练集零重叠」的固定基准句集，用于：
  - 当前 1200 步 LoRA 跑一遍  -> BEFORE 快照（本脚本 + run_diag_infer.py）
  - 将来用 v23(1501句) 重训后跑同一份 -> AFTER 快照
  两份快照对比即可用数据钉死「重训到底有没有用」。

设计：
  - 5 个失败字(行/还/量/假/兴): 各 1 句干净的 minority 读法(非绕口令)
  - 7 个已训成功控制字(好/发/得/应/背/转/舍): 证明架构能学时声调, 且 LoRA 不应回退
  - 3 个额外独立字(处/亲/从): 补充覆盖
  - 每句标注 target_char + expected_pinyin(t3) + category, 供报告与听测对照

用法:
  python gen_diag_testset.py            # 生成 data/diag_benchmark.json + 自检重叠
  python gen_diag_testset.py --check    # 仅自检与训练集重叠, 不写文件
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
V19 = ROOT / "data" / "v19_teacher_text.jsonl"
V23 = ROOT / "data" / "v23_teacher_text.jsonl"
OUT = ROOT / "data" / "diag_benchmark.json"

# (key, sentence, target_char, expected_pinyin_t3, category, note)
BENCHMARK = [
    # ---- 5 个失败字: 干净 minority 读法 ----
    ("hang_hang", "他在银行工作，负责行业分析。", "行", "hang2", "failure",
     "银行/行业 均读 háng(去声); base 先验 xíng(错误)"),
    ("huan_huan", "借出去的钱终于还钱了，物归原主。", "还", "huan2", "failure",
     "还钱 读 huán(阳平); base 先验 hái(错误)"),
    ("liang_liang", "护士量一量体温，又量了身高。", "量", "liang2", "failure",
     "量一量 读 liáng(阳平); base 先验 liàng(错误)"),
    ("jia_jia", "暑假学校放假，我们去假山玩。", "假", "jia4", "failure",
     "放假/假山 读 jià(去声); base 先验 jiǎ(错误)"),
    ("xing_xing", "听到好消息，孩子们兴奋得跳起来。", "兴", "xing1", "failure",
     "兴奋 读 xīng(阴平); base 先验 xìng(错误)"),
    # ---- 7 个已训成功控制字: 证明架构能学时声调, LoRA 不应回退 ----
    ("hao_hao", "他爱好读书，是个好学的人。", "好", "hao4", "control",
     "爱好/好学 读 hào; 好/发等是之前已训成功的声调纠错样例"),
    ("fa_fa", "他的头发乌黑发亮，显得年轻。", "发", "fa4", "control",
     "头发 读 fà; 声调级纠错成功案例"),
    ("de_dei", "这件事得慢慢来，急不得。", "得", "dei3", "control",
     "得(必须) 读 děi; 成功案例"),
    ("ying_ying", "他应该应允这个合理的请求。", "应", "ying1", "control",
     "应该/应允 读 yīng; 成功案例"),
    ("bei_bei", "他背着书包，背对着墙站着。", "背", "bei1", "control",
     "背着/背对 读 bēi; 成功案例"),
    ("zhuan_zhuan", "他转身，转告了这个重要消息。", "转", "zhuan3", "control",
     "转身/转告 读 zhuǎn; 成功案例"),
    ("she_she", "他舍近求远，舍弃了眼前的便利。", "舍", "she3", "control",
     "舍近求远/舍弃 读 shě; 成功案例"),
    # ---- 3 个额外独立字 ----
    ("chu_chu", "他们相处多年，从未红过脸。", "处", "chu3", "extra",
     "相处 读 chǔ; 独立集补充"),
    ("qin_qing", "两家结为亲家，逢年过节都走动。", "亲", "qing4", "extra",
     "亲家 读 qìng; 独立集补充"),
    ("cong_zong", "苏秦主张合从抗秦，这里的从通纵。", "从", "zong4", "extra",
     "合从(纵) 读 zòng; 独立集补充"),
]


def load_corpus(path: Path) -> set:
    if not path.exists():
        return set()
    s = set()
    for line in path.open(encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            s.add(json.loads(line)["sentence"])
        except Exception:
            pass
    return s


def main():
    ap = argparse.ArgumentParser(description="生成独立诊断基准集")
    ap.add_argument("--check", action="store_true", help="仅自检重叠, 不写文件")
    args = ap.parse_args()

    v19 = load_corpus(V19)
    v23 = load_corpus(V23)
    corpus = v19 | v23

    print(f"训练集规模: v19={len(v19)}  v23={len(v23)}  合并去重={len(corpus)}")
    print(f"基准句数: {len(BENCHMARK)}\n")

    overlap = []
    for key, sent, ch, py, cat, note in BENCHMARK:
        if sent in corpus:
            overlap.append((key, sent))
            print(f"  [⚠ 重叠] {key}: {sent}")
    if not overlap:
        print("  ✅ 全部基准句与训练集零重叠 (真正的独立集)")
    else:
        print(f"\n  ❌ 发现 {len(overlap)} 句与训练集重叠, 请修改后再用!")

    # 摘要表
    print("\n=== 基准集一览 ===")
    cats = {}
    for key, sent, ch, py, cat, note in BENCHMARK:
        cats.setdefault(cat, []).append((key, ch, py, sent))
    for cat in ("failure", "control", "extra"):
        print(f"\n[{cat}] {len(cats.get(cat, []))} 句")
        for key, ch, py, sent in cats.get(cat, []):
            print(f"  {key:<16} {ch}->{py:<6} {sent}")

    if args.check:
        return 0 if not overlap else 1

    if overlap:
        print("\n存在重叠, 已中止写文件。")
        return 1

    out = [{"key": k, "sentence": s, "char": c, "expected": p,
            "category": cat, "note": note}
           for (k, s, c, p, cat, note) in BENCHMARK]
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 写出 {OUT} ({len(out)} 句)")
    print("下一步: 用 run_diag_infer.py 分别跑 base 与 --lora-dir lora_output\\step_0001200")
    return 0


if __name__ == "__main__":
    sys.exit(main())
