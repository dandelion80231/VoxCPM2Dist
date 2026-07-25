#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并 v19(520) + v23(1511) = 2021 句, 生成训练用合并清单。
- merged_manifest.jsonl: 每行含 audio(绝对路径) + sentence, 供 train_lora.py 模式A直接消费
- merged_teacher_text.jsonl: 兜底文本(句子级)
逐一校验音频文件存在, 缺失则报告不写入。
"""
import json
from pathlib import Path

BASE = Path(r"D:\AI\Build\多音字")
V19_TEXT = BASE / "data" / "v19_teacher_text.jsonl"
V23_MANIFEST = BASE / "data" / "v23_manifest.jsonl"
OUT_MANIFEST = BASE / "data" / "merged_manifest.jsonl"
OUT_TEACHER = BASE / "data" / "merged_teacher_text.jsonl"

v19_recs, v23_recs, missing = [], [], []

# --- v19: 从 teacher text + wavs/{idx:04d}.wav ---
with open(V19_TEXT, encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        o = json.loads(ln)
        idx = o.get("idx")
        sent = (o.get("sentence") or o.get("text") or "").strip()
        if idx is None or not sent:
            continue
        wav = BASE / "wavs" / f"{int(idx):04d}.wav"
        if not wav.exists():
            missing.append(str(wav))
            continue
        v19_recs.append({
            "idx": int(idx), "src": "v19",
            "wav": f"wavs/{int(idx):04d}.wav", "audio": str(wav),
            "text": sent, "sentence": sent,
        })

# --- v23: 直接吃现有 manifest 的 audio(绝对路径) ---
with open(V23_MANIFEST, encoding="utf-8") as f:
    for ln in f:
        ln = ln.strip()
        if not ln:
            continue
        o = json.loads(ln)
        sent = (o.get("sentence") or o.get("text") or "").strip()
        audio = (o.get("audio") or o.get("wav") or "").strip()
        if not sent or not audio:
            continue
        ap = Path(audio)
        if not ap.is_absolute():
            ap = (BASE / ap).resolve()
        if not ap.exists():
            missing.append(str(ap))
            continue
        v23_recs.append({
            "idx": o.get("idx"), "src": "v23",
            "wav": o.get("wav") or f"wavs_v23/{o.get('idx')}.wav",
            "audio": str(ap), "text": sent, "sentence": sent,
        })

# --- 合并, 全局唯一 index 1..N ---
merged = v19_recs + v23_recs
for i, r in enumerate(merged, 1):
    r["index"] = i

with open(OUT_MANIFEST, "w", encoding="utf-8") as f:
    for r in merged:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

with open(OUT_TEACHER, "w", encoding="utf-8") as f:
    for r in merged:
        f.write(json.dumps({"idx": r["index"], "sentence": r["sentence"],
                            "src": r["src"]}, ensure_ascii=False) + "\n")

# --- 重复句检测(仅报告, 不剔除) ---
seen, dups = set(), 0
for r in merged:
    if r["sentence"] in seen:
        dups += 1
    seen.add(r["sentence"])

print(f"[merge] v19 = {len(v19_recs)}")
print(f"[merge] v23 = {len(v23_recs)}")
print(f"[merge] TOTAL = {len(merged)}")
print(f"[merge] 重复句(跨集) = {dups}")
print(f"[merge] 缺失音频 = {len(missing)}")
for m in missing[:20]:
    print(f"  MISSING: {m}")
print(f"[merge] -> {OUT_MANIFEST}")
print(f"[merge] -> {OUT_TEACHER}")
