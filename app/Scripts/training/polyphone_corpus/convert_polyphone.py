#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Convert full_pinyin_forced (space-separated syllables w/ tone digits)
into two teacher-ready text forms:

  1) CosyVoice3  -> fully bracketed phoneme text  [zh][ēng][zh][ēng]，...
     every Han char becomes [initial][final_with_tone_mark]; punctuation kept raw.
  2) IndexTTS2   -> phoneme_input text            zheng1 zheng1 , niao3 ...
     already in IndexTTS2's `phoneme_input=True` format (space-sep, tone digits).

Alignment: walk the original sentence char-by-char; for each Han char consume
one pinyin syllable, for punctuation/other emit raw. Assumes one syllable per
Han char (holds for this corpus).

Validates every CosyVoice3 token against the REAL token set parsed from
CosyVoice3's tokenizer.py (authoritative, no model download needed).
"""
import re
import json
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
TOKENIZER_PY = os.path.join(ROOT, "CosyVoice", "cosyvoice", "tokenizer", "tokenizer.py")
SRC_JSONL = os.path.join(ROOT, "data", "v19_pinyin_input.jsonl")
OUT_JSONL = os.path.join(ROOT, "data", "v19_teacher_text.jsonl")

# ---------------------------------------------------------------------------
# CosyVoice3 initial set (from tokenizer.py). NOTE: y/w are NOT initials;
# they are encoded inside finals ([ià], [uāng], [ǚ] ...).
# ---------------------------------------------------------------------------
INITIALS = ['zh', 'ch', 'sh', 'b', 'p', 'm', 'f', 'd', 't', 'n', 'l',
            'g', 'k', 'h', 'j', 'q', 'x', 'r', 'z', 'c', 's']

TONE_VOWEL = {
    'a': ['ā', 'á', 'ǎ', 'à'], 'o': ['ō', 'ó', 'ǒ', 'ò'], 'e': ['ē', 'é', 'ě', 'è'],
    'i': ['ī', 'í', 'ǐ', 'ì'], 'u': ['ū', 'ú', 'ǔ', 'ù'], 'ü': ['ǖ', 'ǘ', 'ǚ', 'ǜ'],
    'v': ['ǖ', 'ǘ', 'ǚ', 'ǜ'],
}


def load_cv3_tokens(path):
    """Parse the CosyVoice3 phoneme token set straight from tokenizer.py source."""
    text = open(path, encoding='utf-8').read()
    start = text.index('class CosyVoice3Tokenizer')
    block = text[start:start + 5000]
    toks = re.findall(r"\[[^\]]*\]", block)
    phon = set()
    pat = re.compile(r"^[A-Za-zàáǎàâäãāéěèēíǐìīóǒòōúǔùūüǖǘǚǜ0-5]+$")
    for t in toks:
        if pat.fullmatch(t[1:-1]):
            phon.add(t)
    return phon


# CosyVoice3 has NO 'ü' token: ü-compounds are written with plain 'u'
# (yuè->[uè], yuán->[uán], yún->[ún], yú->[ú]). So map the yu- series to u-.
Y_MAP = {
    'i': 'i', 'a': 'ia', 'e': 'ie', 'o': 'io', 'ao': 'iao', 'ou': 'iu',
    'an': 'ian', 'ang': 'iang', 'ong': 'iong', 'in': 'in', 'ing': 'ing',
    'u': 'u', 'ue': 'ue', 'un': 'un', 'uan': 'uan',   # yu, yue, yun, yuan
}
W_MAP = {
    'u': 'u', 'a': 'ua', 'o': 'uo', 'ai': 'uai', 'ei': 'ui', 'an': 'uan',
    'en': 'un', 'ang': 'uang', 'eng': 'ueng',
}


def convert_glide(body):
    """Convert y/w-initial syllables to i/u-based finals (CosyVoice3 style)."""
    if body.startswith('y'):
        return Y_MAP.get(body[1:], 'i' + body[1:])
    if body.startswith('w'):
        return W_MAP.get(body[1:], 'u' + body[1:])
    return body


def apply_tone(final, tone):
    """Place the tone mark on the main vowel of a final. tone in 1..5 (5=neutral)."""
    if tone == 5:
        # CosyVoice3 has no neutral '[ia]' token (only toned [ià]); the only
        # real-world case is the sentence-final particle 呀(ya5). Fall back to
        # tone-4, which is audibly indistinguishable for a particle.
        if final == 'ia':
            return 'ià'
        return final
    if 'a' in final:
        pos = final.index('a')
    elif 'o' in final:
        pos = final.index('o')
    elif 'e' in final:
        pos = final.index('e')
    elif 'ü' in final or 'v' in final:
        pos = final.index('ü') if 'ü' in final else final.index('v')
    elif 'iu' in final or 'ui' in final:
        pos = (final.index('iu') + 1) if 'iu' in final else (final.index('ui') + 1)
    elif 'i' in final:
        pos = final.index('i')
    elif 'u' in final:
        pos = final.index('u')
    else:
        pos = 0
    ch = final[pos]
    marked = TONE_VOWEL[ch][tone - 1]
    return final[:pos] + marked + final[pos + 1:]


def syl_to_cv3(syl):
    """zheng1 -> [zh][ēng] ; you3 -> [iǔ] ; yu4 -> [ú] ; le5 -> [e]"""
    m = re.fullmatch(r"([a-züv]+)(\d)", syl)
    if not m:
        return None, f"bad syllable '{syl}'"
    body, tone = m.group(1), int(m.group(2))
    body = body.replace('v', 'ü')          # 'v' is ü in digital pinyin
    body = convert_glide(body)
    ini = ''
    for cand in sorted(INITIALS, key=len, reverse=True):
        if body.startswith(cand):
            ini = cand
            body = body[len(cand):]
            break
    final_toned = apply_tone(body, tone)
    return (f"[{ini}][{final_toned}]" if ini else f"[{final_toned}]"), None


def is_han(ch):
    return '\u4e00' <= ch <= '\u9fff'


def build_texts(sentence, pinyin_list):
    si = 0
    cosy_parts, it2_parts = [], []
    for ch in sentence:
        if is_han(ch):
            if si >= len(pinyin_list):
                cosy_parts.append(ch)
                it2_parts.append(ch)
                continue
            syl = pinyin_list[si]
            si += 1
            c3, err = syl_to_cv3(syl)
            cosy_parts.append(c3 if c3 else syl)
            it2_parts.append(syl)            # already tone-digit form for IndexTTS2
        else:
            cosy_parts.append(ch)
            it2_parts.append(ch)
    while si < len(pinyin_list):             # leftover (shouldn't happen)
        syl = pinyin_list[si]
        si += 1
        c3, _ = syl_to_cv3(syl)
        cosy_parts.append(c3 if c3 else syl)
        it2_parts.append(syl)
    cosy_text = ''.join(cosy_parts)
    it2_text = ' '.join(it2_parts)
    return cosy_text, it2_text


def main():
    tokens = load_cv3_tokens(TOKENIZER_PY)
    print(f"[info] loaded {len(tokens)} CosyVoice3 phoneme tokens from tokenizer.py")

    missing = {}          # token -> count
    bad_syl = {}          # syllable -> count
    n = 0
    out_records = []
    with open(SRC_JSONL, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            n += 1
            pinyin_list = d.get('full_pinyin_forced', '').split()
            cosy_text, it2_text = build_texts(d.get('sentence', ''), pinyin_list)

            # validate CV3 tokens
            this_missing = set()
            for tok in re.findall(r"\[[^\]]*\]", cosy_text):
                if tok not in tokens:
                    missing[tok] = missing.get(tok, 0) + 1
                    this_missing.add(tok)
            for syl in pinyin_list:
                if re.fullmatch(r"([a-züv]+)(\d)", syl) is None:
                    bad_syl[syl] = bad_syl.get(syl, 0) + 1

            rec = dict(d)
            rec['cosy3_text'] = cosy_text
            rec['it2_text'] = it2_text
            rec['cosy3_missing_tokens'] = sorted(this_missing)
            out_records.append(rec)

    with open(OUT_JSONL, 'w', encoding='utf-8') as f:
        for rec in out_records:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    print(f"[info] processed {n} records -> {OUT_JSONL}")
    print(f"[warn] bad syllables (no tone digit): {bad_syl if bad_syl else 'none'}")
    print(f"[CRIT] CosyVoice3 tokens MISSING from tokenizer set: "
          f"{len(missing)} distinct")
    for tok, c in sorted(missing.items(), key=lambda x: -x[1]):
        print(f"    {tok}  x{c}")
    if not missing:
        print("  -> full coverage OK for all 520 sentences ✅")

    # show idx1 as a sanity sample
    idx1 = next((r for r in out_records if r.get('idx') == 1), None)
    if idx1:
        print("\n[sample idx1] cosy3_text:\n ", idx1['cosy3_text'])
        print("          it2_text:\n  ", idx1['it2_text'])


if __name__ == '__main__':
    main()
