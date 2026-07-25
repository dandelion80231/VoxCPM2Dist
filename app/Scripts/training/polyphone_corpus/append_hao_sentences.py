#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
append_hao_sentences.py — 在不重建全量 v23 的前提下，追加 10 句 好=hào 正确标注句。
步骤：
  1) 一致性校验：加 OVERLAY 后，对现有 teacher_text 的 1-250 句用新 ref_pinyin 重算
     it2_text，与磁盘上的 it2_text 比对。若有差异 → 这些句理想注音变了，已合成 wav
     可能读错，必须退出并人工决定重合成哪些。预期零差异。
  2) 对 10 句候选用 build_v23 同款流水线(ref_pinyin + 断言闸门 + build_texts)生成完整
     记录，idx 从 max+1 起，追加到 v23_teacher_text.jsonl。
  3) 重建干净 manifest：去重(idx=1 留1 / idx=133 留1)，删除 251/252 孤儿行(有记录无 wav)。
"""
import sys, os, json, re
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from diff_web_pinyin import ref_pinyin
from convert_polyphone import build_texts
from build_v23 import han_only, valid_readings, toneless

TEACHER = os.path.join(HERE, "data", "v23_teacher_text.jsonl")
MANIFEST = os.path.join(HERE, "data", "v23_manifest.jsonl")
WAV_DIR = os.path.join(HERE, "wavs_v23")

# ---------------------------------------------------------------------------
# 1) 一致性校验 1-250
# ---------------------------------------------------------------------------
print("=" * 60)
print("[1/3] 校验 1-250 注音一致性 (加 OVERLAY 后)")
rows = [json.loads(l) for l in open(TEACHER, encoding="utf-8") if l.strip()]
existing = {r["idx"]: r for r in rows}
diffs = []
for idx in range(1, 251):
    r = existing.get(idx)
    if not r:
        continue
    clean = han_only(r["sentence"])
    ref = ref_pinyin(clean)
    _, it2 = build_texts(r["sentence"], ref)
    if it2 != r.get("it2_text"):
        diffs.append((idx, r["sentence"], r.get("it2_text"), it2))
if diffs:
    print("⚠️ 1-250 出现注音差异，这些句需重合成（先停在这里，人工决定）：")
    for d in diffs:
        print(f"   idx={d[0]} {d[1]}")
        print(f"       旧: {d[2]}")
        print(f"       新: {d[3]}")
    sys.exit(1)
print(f"   ✅ 1-250 注音一致性通过（0 差异），已合成 wav 全部有效")

# ---------------------------------------------------------------------------
# 2) 追加 10 句 好=hào
# ---------------------------------------------------------------------------
print("=" * 60)
print("[2/3] 生成并追加 10 句 好=hào 句")
SENTENCES = [
    "他的爱好是书法和篆刻。",
    "读书是她毕生的爱好。",
    "培养广泛的爱好能丰富生活。",
    "这孩子勤奋好学，成绩优异。",
    "好学不倦是成功的秘诀。",
    "孩子对世界充满好奇。",
    "好奇心驱使他不断探索。",
    "西北人热情好客，令人难忘。",
    "他总好为人师，爱指点别人。",
    "年轻人要脚踏实地，不好高骛远。",
]
max_idx = max(r["idx"] for r in rows)
new_records = []
for i, sent in enumerate(SENTENCES):
    clean = han_only(sent)
    ref = ref_pinyin(clean)
    bad = []
    if len(ref) != len(clean):
        bad.append(f"长度不对 ref{len(ref)}≠clean{len(clean)}")
    else:
        for ci, (c, syl) in enumerate(zip(clean, ref)):
            if syl == "?":
                bad.append(f"位置{ci}字{c}读法'?'")
                continue
            vr = valid_readings(c)
            if vr and toneless(syl) not in vr:
                bad.append(f"位置{ci}字{c}强制'{syl}'不在合法读音{vr}")
    if bad:
        print(f"❌ 句{i+1}断言失败: {sent} -> {bad}")
        sys.exit(1)
    cosy, it2 = build_texts(sent, ref)
    new_idx = max_idx + 1 + i
    new_records.append({
        "idx": new_idx,
        "src": f"APPEND{i+1}",
        "tag": "hao",
        "sentence": sent,
        "clean": clean,
        "ref_pinyin": ref,
        "it2_text": it2,
        "cosy3_text": cosy,
    })
    print(f"  [{new_idx}] {sent}")
    print(f"       {it2}")

with open(TEACHER, "a", encoding="utf-8") as f:
    for rec in new_records:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
print(f"   ✅ 追加 {len(new_records)} 句 -> {TEACHER}")

# ---------------------------------------------------------------------------
# 3) 重建干净 manifest
# ---------------------------------------------------------------------------
print("=" * 60)
print("[3/3] 重建干净 manifest")
mrows = [json.loads(l) for l in open(MANIFEST, encoding="utf-8") if l.strip()]
seen = {}
orphan = []
for r in mrows:
    idx = r.get("idx") or r.get("index")
    wav_path = r.get("audio") or os.path.join(HERE, r.get("wav", ""))
    if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
        if idx not in seen:          # 去重：每个 idx 只留第一条
            seen[idx] = r
    else:
        orphan.append(idx)
print(f"   旧 manifest 行数: {len(mrows)}")
print(f"   唯一有 wav 的 idx: {len(seen)}")
print(f"   孤儿(有记录无 wav, 将被删, 续跑重合成): {sorted(set(orphan))}")
with open(MANIFEST, "w", encoding="utf-8") as f:
    for idx in sorted(seen):
        f.write(json.dumps(seen[idx], ensure_ascii=False) + "\n")
print(f"   ✅ 干净 manifest 写出 -> {len(seen)} 行")

print("=" * 60)
print("全部完成。下一步：双击 synth_v23_full.bat 续跑（自动跳过 1-250，补 251-1501 + 新增 1502-1511）")
