# -*- coding: utf-8 -*-
"""
Audit the polyphone forced-pronunciation corpus dataset.
READ-ONLY analysis: does NOT modify the source dataset.
Writes:
  - D:/AI/Build/多音字/data_audit.md      (detailed report)
  - D:/AI/Build/多音字/data/v19_teacher_text.clean.jsonl (rows passing all checks)
"""
import json
import re
import os
from collections import defaultdict

SRC = r"D:\AI\Build\多音字\data\v19_teacher_text.jsonl"
REPORT = r"D:\AI\Build\多音字\data_audit.md"
CLEAN = r"D:\AI\Build\多音字\data\v19_teacher_text.clean.jsonl"

# ---- CJK character detection (common + ext A + compat) ----
CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
def cjk_count(s):
    return len(CJK_RE.findall(s))

# ---- accented vowel (tone-mark) detection for cosy3 final detection ----
VOWEL_CHARS = set("aeiouüāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜêếềểễệ")
VOWEL_RE = re.compile(r"[aeiouüāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜêếềểễệ]")

# ---- bracket token extraction for cosy3 ----
BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")

# ---- it2 pinyin token validation ----
# Expected it2 format: space-separated pinyin tokens, each ending in a tone
# digit 1-5, e.g. "fa2 mu4 zheng1". Letters only (incl. ü), tone digit required.
PINYIN_RE = re.compile(r"^[a-zü]+[1-5]$")
# Punctuation (incl. CJK quotes/brackets) that may appear as standalone tokens.
PUNCT_RE = re.compile(
    r"^[，。？！、；：．…—·「」『』【】《》〈〉（）“”‘’"
    r"\"'.,!?;:()\[\]{}<>/\\|`~@#$%^&*\-_=+ ]+$"
)
# Single-letter / accented-vowel tokens indicate cosy3/GLM *letter-spelling*
# (e.g. "z ò n g") leaked into it2 instead of digit-tone pinyin.
LETTER_SPELL_RE = re.compile(r"^[a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜêếềểễệ]$")

def count_cosy3_syllables(text):
    """Count syllables in cosy3_text. Each syllable ends at a vowel-bearing
    bracket (the 'final'); consonant-only brackets are initials of the same syllable."""
    toks = BRACKET_RE.findall(text)
    syll = 0
    for t in toks:
        if VOWEL_RE.search(t):
            syll += 1
    return syll, toks

def cosy3_bracket_balance(text):
    return text.count("["), text.count("]")

def main():
    with open(SRC, "r", encoding="utf-8") as f:
        raw_lines = f.readlines()

    total = len(raw_lines)
    rows = []            # parsed dicts (or None)
    parse_errors = []    # (1-based index, error message)

    for i, line in enumerate(raw_lines, start=1):
        line = line.rstrip("\n")
        if line.strip() == "":
            # treat empty line as parse error
            parse_errors.append((i, "empty line"))
            rows.append(None)
            continue
        try:
            obj = json.loads(line)
            rows.append(obj)
        except Exception as e:
            parse_errors.append((i, str(e)))
            rows.append(None)

    # ---- per-row issue tracking ----
    # issue_types is a dict index->list of reason strings
    issues = defaultdict(list)
    valid_rows = []  # list of (index, obj) that pass all checks

    missing_field_idx = defaultdict(list)   # field -> [idx]
    empty_value_idx = defaultdict(list)     # field -> [idx]

    cosy3_imbalance = []
    cosy3_malformed = []
    cosy3_syll_mismatch = []
    it2_invalid_token = []
    it2_syll_mismatch = []
    sentence_issue = []
    cross_mismatch = []

    # cosy3 syllable counts and it2 syllable counts per index (for duplicates/cross not needed but useful)
    cosy3_syll_of = {}
    it2_syll_of = {}

    for i, obj in enumerate(rows, start=1):
        if obj is None:
            continue  # already counted in parse_errors

        # --- required fields presence ---
        for field in ("sentence", "cosy3_text", "it2_text"):
            if field not in obj:
                missing_field_idx[field].append(i)
                issues[i].append(f"missing field '{field}'")
            elif isinstance(obj[field], str) and obj[field].strip() == "":
                empty_value_idx[field].append(i)
                issues[i].append(f"empty value for '{field}'")

        sentence = obj.get("sentence", "")
        cosy3 = obj.get("cosy3_text", "")
        it2 = obj.get("it2_text", "")

        # --- sentence checks ---
        if sentence.strip() != "":
            if cjk_count(sentence) == 0:
                sentence_issue.append(i)
                issues[i].append("sentence has no Chinese characters")

        # --- cosy3 bracket balance & malformed tokens ---
        if cosy3.strip() != "":
            ob, cb = cosy3_bracket_balance(cosy3)
            if ob != cb:
                cosy3_imbalance.append((i, ob, cb))
                issues[i].append(f"cosy3 unbalanced brackets ([= {ob}, ]= {cb})")
            toks = BRACKET_RE.findall(cosy3)
            malformed = [t for t in toks if t == "" or re.search(r"[^a-züāáǎàēéěèīíǐìōóǒòūúǔùǖǘǚǜêếềểễệ]", t)]
            if malformed:
                cosy3_malformed.append((i, malformed[:5]))
                issues[i].append(f"cosy3 malformed bracket tokens: {malformed[:5]}")
            cs, _ = count_cosy3_syllables(cosy3)
            cosy3_syll_of[i] = cs
        else:
            cosy3_syll_of[i] = 0

        # --- it2 token validation ---
        it2_syll = 0
        row_invalid = []   # list of (token, kind)
        if it2.strip() != "":
            toks = it2.split()
            for t in toks:
                if PINYIN_RE.match(t):
                    it2_syll += 1
                elif PUNCT_RE.match(t):
                    # acceptable standalone punctuation token
                    continue
                elif LETTER_SPELL_RE.match(t):
                    row_invalid.append((t, "letter-spelling"))
                else:
                    row_invalid.append((t, "other"))
            if row_invalid:
                it2_invalid_token.append((i, row_invalid))
                sample = ", ".join(f"'{tk}'({kd})" for tk, kd in row_invalid[:6])
                issues[i].append(f"it2_text not digit-tone pinyin; bad tokens: {sample}")
            it2_syll_of[i] = it2_syll
        else:
            it2_syll_of[i] = 0

    # ---- it2_invalid_token is already per-row (i, [(token,kind),...]) ----
    it2_invalid_summary = {i: row for (i, row) in it2_invalid_token}

    # ---- cross-field syllable vs chinese char count ----
    for i, obj in enumerate(rows, start=1):
        if obj is None:
            continue
        sentence = obj.get("sentence", "")
        cosy3 = obj.get("cosy3_text", "")
        it2 = obj.get("it2_text", "")
        # skip if any field empty/missing (already flagged)
        if sentence.strip() == "" or cosy3.strip() == "" or it2.strip() == "":
            continue
        cc = cjk_count(sentence)
        cs = cosy3_syll_of.get(i, 0)
        isy = it2_syll_of.get(i, 0)

        cosy_mis = abs(cs - cc) > 2
        it2_mis = abs(isy - cc) > 2
        if cosy_mis:
            cosy3_syll_mismatch.append((i, cs, cc))
            issues[i].append(f"cosy3 syllable count ({cs}) vs Chinese chars ({cc}) mismatch >2")
        if it2_mis:
            it2_syll_mismatch.append((i, isy, cc))
            issues[i].append(f"it2 syllable count ({isy}) vs Chinese chars ({cc}) mismatch >2")
        if cosy_mis or it2_mis:
            cross_mismatch.append(i)

    # ---- duplicates ----
    seen_sentence = {}
    seen_cosy3 = {}
    seen_it2 = {}
    dup_sentence = []
    dup_cosy3 = []
    dup_it2 = []
    for i, obj in enumerate(rows, start=1):
        if obj is None:
            continue
        s = obj.get("sentence")
        c = obj.get("cosy3_text")
        t = obj.get("it2_text")
        if s is not None and s.strip() != "":
            if s in seen_sentence:
                dup_sentence.append((i, seen_sentence[s]))
                issues[i].append(f"duplicate sentence (first at row {seen_sentence[s]})")
            else:
                seen_sentence[s] = i
        if c is not None and c.strip() != "":
            if c in seen_cosy3:
                dup_cosy3.append((i, seen_cosy3[c]))
                issues[i].append(f"duplicate cosy3_text (first at row {seen_cosy3[c]})")
            else:
                seen_cosy3[c] = i
        if t is not None and t.strip() != "":
            if t in seen_it2:
                dup_it2.append((i, seen_it2[t]))
                issues[i].append(f"duplicate it2_text (first at row {seen_it2[t]})")
            else:
                seen_it2[t] = i

    # ---- determine valid rows (no issues at all) ----
    problematic = set(issues.keys())
    for i, obj in enumerate(rows, start=1):
        if obj is None:
            continue
        if i not in problematic:
            valid_rows.append((i, obj))

    # ---- write clean copy ----
    os.makedirs(os.path.dirname(CLEAN), exist_ok=True)
    with open(CLEAN, "w", encoding="utf-8") as f:
        for i, obj in valid_rows:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")

    # ---- build report ----
    valid_count = len(valid_rows)
    n_parse = len(parse_errors)
    n_missing = sum(len(v) for v in missing_field_idx.values())
    n_empty = sum(len(v) for v in empty_value_idx.values())

    lines = []
    lines.append("# 多音字数据集审计报告 (data audit)\n")
    lines.append(f"- 数据集: `{os.path.basename(SRC)}`")
    lines.append(f"- 总行数: **{total}**")
    lines.append(f"- JSON 解析失败行数: **{n_parse}**")
    lines.append(f"- 缺失字段总计 (行·次): **{n_missing}**")
    lines.append(f"- 空值字段总计 (行·次): **{n_empty}**")
    lines.append(f"- 通过全部检查的有效行数: **{valid_count}**")
    lines.append(f"- 存在问题行数 (去重后): **{len(problematic)}**\n")

    lines.append("## 各类问题统计\n")
    lines.append(f"- cosy3 括号不平衡: {len(cosy3_imbalance)} 行")
    lines.append(f"- cosy3 畸形 token (空/非法字符): {len(cosy3_malformed)} 行")
    lines.append(f"- cosy3 音节数与汉字数不符 (>2): {len(cosy3_syll_mismatch)} 行")
    lines.append(f"- it2 非法 token (非拼音/缺声调): {len(it2_invalid_summary)} 行")
    lines.append(f"- it2 音节数与汉字数不符 (>2): {len(it2_syll_mismatch)} 行")
    lines.append(f"- sentence 无汉字: {len(sentence_issue)} 行")
    lines.append(f"- 跨字段音节不匹配 (cosy3 或 it2 任一不符): {len(cross_mismatch)} 行")
    lines.append(f"- 重复 sentence: {len(dup_sentence)} 行 (后续重复)")
    lines.append(f"- 重复 cosy3_text: {len(dup_cosy3)} 行 (后续重复)")
    lines.append(f"- 重复 it2_text: {len(dup_it2)} 行 (后续重复)\n")

    if missing_field_idx:
        lines.append("### 缺失字段明细\n")
        for fld, idxs in missing_field_idx.items():
            lines.append(f"- `{fld}`: 行 {idxs}")
        lines.append("")

    if empty_value_idx:
        lines.append("### 空值字段明细\n")
        for fld, idxs in empty_value_idx.items():
            lines.append(f"- `{fld}`: 行 {idxs}")
        lines.append("")

    if parse_errors:
        lines.append("### JSON 解析失败行\n")
        for idx, msg in parse_errors:
            lines.append(f"- 行 {idx}: {msg}")
        lines.append("")

    if cosy3_imbalance:
        lines.append("### cosy3 括号不平衡\n")
        for idx, ob, cb in cosy3_imbalance:
            lines.append(f"- 行 {idx}: `[`={ob}, `]`={cb}")
        lines.append("")

    if cosy3_malformed:
        lines.append("### cosy3 畸形 token\n")
        for idx, bad in cosy3_malformed:
            lines.append(f"- 行 {idx}: {bad}")
        lines.append("")

    if cosy3_syll_mismatch:
        lines.append("### cosy3 音节数 vs 汉字数 不符\n")
        for idx, cs, cc in cosy3_syll_mismatch:
            lines.append(f"- 行 {idx}: cosy3 音节={cs}, 汉字数={cc}")
        lines.append("")

    if it2_invalid_summary:
        lines.append("### it2 非数字调拼音 token (cosy3/GLM 字母拼写法泄漏)\n")
        for idx, row in sorted(it2_invalid_summary.items()):
            toks = ", ".join(f"'{t}'({k})" for t, k in row)
            lines.append(f"- 行 {idx}: {toks}")
        lines.append("")

    if it2_syll_mismatch:
        lines.append("### it2 音节数 vs 汉字数 不符\n")
        for idx, isy, cc in it2_syll_mismatch:
            lines.append(f"- 行 {idx}: it2 拼音数={isy}, 汉字数={cc}")
        lines.append("")

    if sentence_issue:
        lines.append("### sentence 无汉字\n")
        lines.append(f"- 行 {sentence_issue}")
        lines.append("")

    if cross_mismatch:
        lines.append("### 跨字段不匹配 (汇总)\n")
        lines.append(f"- 行 {sorted(set(cross_mismatch))}")
        lines.append("")

    if dup_sentence:
        lines.append("### 重复 sentence (后续行)\n")
        for idx, first in dup_sentence:
            lines.append(f"- 行 {idx} 重复自 行 {first}")
        lines.append("")

    if dup_cosy3:
        lines.append("### 重复 cosy3_text (后续行)\n")
        for idx, first in dup_cosy3:
            lines.append(f"- 行 {idx} 重复自 行 {first}")
        lines.append("")

    if dup_it2:
        lines.append("### 重复 it2_text (后续行)\n")
        for idx, first in dup_it2:
            lines.append(f"- 行 {idx} 重复自 行 {first}")
        lines.append("")

    # ---- full problematic-row index list with reasons ----
    lines.append("## 问题行详细列表 (1-based 行号)\n")
    if issues:
        for idx in sorted(issues.keys()):
            lines.append(f"- **行 {idx}**: {'; '.join(issues[idx])}")
    else:
        lines.append("- 无")
    lines.append("")

    # ---- valid row index list (optional compact) ----
    lines.append(f"## 有效行数: {valid_count} / {total}\n")
    lines.append(f"有效行号: {sorted(i for i, _ in valid_rows)}\n")

    lines.append("---\n")
    lines.append("说明: 校验逻辑\n")
    lines.append("- 汉字计数: Unicode \\u3400-\\u4DBF, \\u4E00-\\u9FFF, \\uF900-\\uFAFF。\n")
    lines.append("- cosy3 音节计数: 每个以元音(含声调符号)结尾的 `[...]` 括号组记为 1 个音节；仅辅音的括号视为该音节的声母。\n")
    lines.append("- it2 音节计数: 与正则 `^[a-zü]+[1-5]$` 匹配的 token 数 (标点 token 不计)。\n")
    lines.append("- 跨字段判定: |音节数 - 汉字数| > 2 即标记不匹配。\n")
    lines.append("- 重复判定: 仅标记后续出现的重复行 (首次出现不标记)。\n")
    lines.append(f"- 干净副本已写入: `{CLEAN}` (仅包含无问题的行，保留全部字段)。\n")

    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # ---- console summary ----
    print("==== DATA AUDIT SUMMARY ====")
    print(f"total lines        : {total}")
    print(f"JSON parse errors  : {n_parse}")
    print(f"missing fields     : {n_missing}  {dict(missing_field_idx)}")
    print(f"empty values       : {n_empty}  {dict(empty_value_idx)}")
    print(f"cosy3 imbalance    : {len(cosy3_imbalance)}")
    print(f"cosy3 malformed    : {len(cosy3_malformed)}")
    print(f"cosy3 syll mismatch: {len(cosy3_syll_mismatch)}")
    print(f"it2 invalid token  : {len(it2_invalid_summary)}")
    print(f"it2 syll mismatch  : {len(it2_syll_mismatch)}")
    print(f"sentence no-cjk    : {len(sentence_issue)}")
    print(f"cross mismatch     : {len(cross_mismatch)}")
    print(f"dup sentence       : {len(dup_sentence)}")
    print(f"dup cosy3          : {len(dup_cosy3)}")
    print(f"dup it2            : {len(dup_it2)}")
    print(f"VALID rows         : {valid_count}")
    print(f"PROBLEM rows       : {len(problematic)}")
    print(f"report -> {REPORT}")
    print(f"clean  -> {CLEAN}")

if __name__ == "__main__":
    main()
