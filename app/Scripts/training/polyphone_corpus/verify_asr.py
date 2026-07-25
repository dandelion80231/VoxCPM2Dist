#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
A/B ASR verification for idx1 (丁 -> zhēng).

Loads the two A/B wavs with soundfile (via torchaudio_shim, NO ffmpeg),
resamples to 16k, runs openai-whisper (base), then converts the transcript
to pinyin with pypinyin to best-effort check the forced polyphone reading.

Usage:
  python verify_asr.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import torch, torchaudio, numpy as np
import torchaudio_shim
torchaudio.load = torchaudio_shim.load
torchaudio.save = torchaudio_shim.save
torchaudio.info = torchaudio_shim.info

import whisper
from pypinyin import pinyin, Style

WAVS = {
    "CosyVoice3 (A)": r'D:\AI\Build\多音字\polyphone_test\cosy3_idx1.wav',
    "IndexTTS2  (B)": r'D:\AI\Build\多音字\polyphone_test\it2_idx1.wav',
}

def load_16k(wav):
    w, sr = torchaudio.load(wav)
    if sr != 16000:
        w = torchaudio.transforms.Resample(sr, 16000)(w)
    return w.mean(0).numpy().astype(np.float32)

def to_pinyin(text):
    # heteronym=False: deterministic; we just want tone-bearing pinyin for the 丁 position
    return ' '.join(t[0] for t in pinyin(text, style=Style.TONE3, heteronym=False))

print('[loading whisper base ...]')
model = whisper.load_model('base')

for name, wav in WAVS.items():
    if not os.path.exists(wav):
        print(f'[{name}] MISSING: {wav}')
        continue
    audio = load_16k(wav)
    res = model.transcribe(audio, language='zh', fp16=False)['text']
    py = to_pinyin(res)
    print('=' * 60)
    print(f'[{name}] file : {wav}')
    print(f'[{name}] ASR  : {res}')
    print(f'[{name}] pinyin: {py}')
    # best-effort polyphone check: 丁 should be read zhēng (zheng1)
    # look for zheng/zhēng/征/睁/蒸 near where 丁 should be
    zh_re = ('zheng' in py.lower()) or ('征' in res) or ('睁' in res) or ('蒸' in res)
    print(f'[{name}] 丁->zhēng evidence: {"YES (zheng/征/睁/蒸 present)" if zh_re else "NO (read as dīng?)"}')
