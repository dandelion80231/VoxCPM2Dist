#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""
synth_batch.py — batch forced-pronunciation TTS for the 多音字 (polyphone) LoRA corpus.

Reads D:\AI\Build\多音字\data\v19_teacher_text.jsonl (520 rows; fields: idx,
sentence, char, pinyin_mark, pinyin_t3, cosy3_text, it2_text, ...). For each row
it synthesizes one audio clip cloning the fixed reference voice (voice cloning /
self-distillation — intentional, user-approved), forcing the polyphone reading via
inline phoneme/pinyin markup, and writes it to wavs/{idx:04d}.wav.

Engines:
  --model cosy3  (DEFAULT, PRIMARY)  CosyVoice3 zero-shot, uses `cosy3_text`
                                    (inline bracket phonemes, e.g. [f][á][m][ù]).
  --model it2                    IndexTTS2, uses `it2_text`
                                    (inline pinyin, e.g. fa2 mu4 zheng1 zheng1).

CosyVoice3 forced pronunciation:
  * prompt_text MUST contain the <|endofprompt|> separator token (151646). The
    official convention is to prefix it with the assistant prompt:
        'You are a helpful assistant.<|endofprompt|>' + <prompt transcription>
  * The bracket phonemes survive text_normalize (it skips normalization when it
    sees '<|'), so the whole cosy3_text is fed verbatim via inference_zero_shot.
  * The frontend already uses allowed_special='all' (see cosyvoice/cli/frontend.py
    and the model's cosyvoice3.yaml), so no extra flag is needed.

torchaudio shim:
  torchaudio 2.11 in this env routes I/O through torchcodec (ffmpeg DLL missing)
  and dropped the backend= API the TTS code uses. We monkeypatch torchaudio.load/
  save/info onto soundfile BEFORE importing cosyvoice / indextts.

Resumable: any wav that already exists is skipped. Use --limit N for a smoke test
on the first N rows. Use --dry-run to parse the jsonl without loading any model.
"""
import sys
import os
import json
import argparse
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
# Package roots (mirror the test_*.py sys.path inserts so `import cosyvoice`
# / `import indextts` resolve in this standalone script).
sys.path.insert(0, os.path.join(HERE, "CosyVoice"))
sys.path.insert(0, os.path.join(HERE, "IndexTTS2_code"))

# ---------------------------------------------------------------------------
# torchaudio shim MUST be applied before importing cosyvoice / indextts.
# ---------------------------------------------------------------------------
import torch
import torchaudio
import torchaudio_shim
torchaudio.load = torchaudio_shim.load
torchaudio.save = torchaudio_shim.save
torchaudio.info = torchaudio_shim.info

# SentencePiece 0.2.1 cannot open file paths containing non-ASCII characters
# (the 多音字 parent folder) on Windows -- its C++ layer uses a narrow fopen.
# Load the .model via bytes instead. Required by IndexTTS2 (it2 engine);
# harmless for cosy3. Mirrors the torchaudio_shim pattern above.
try:
    import sentencepiece as _spm
    def _sp_load_from_file(self, path):
        with open(path, "rb") as _fh:
            return self.LoadFromSerializedProto(_fh.read())
    _spm.SentencePieceProcessor.LoadFromFile = _sp_load_from_file
except Exception:
    pass

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
DATA_JSONL = os.path.join(HERE, "data", "v19_teacher_text.jsonl")
OUT_DIR = os.path.join(HERE, "wavs")
MANIFEST = os.path.join(HERE, "wavs_manifest.jsonl")

COSY_MODEL_DIR = os.path.join(HERE, "models", "Fun-CosyVoice3-0.5B")
COSY_PROMPT_WAV = os.path.join(HERE, "assets", "ref_prompt_cosy.wav")

IT2_CFG = os.path.join(HERE, "IndexTTS2", "checkpoints", "config.yaml")
IT2_MODEL_DIR = os.path.join(HERE, "IndexTTS2", "checkpoints")
IT2_PROMPT_WAV = os.path.join(HERE, "assets", "ref_prompt_it2.wav")

# Prefix required by CosyVoice3 so the <|endofprompt|> token survives.
CV3_PREFIX = "You are a helpful assistant.<|endofprompt|>"
# Transcription of the fixed reference voice (D:\电脑桌面\tmpzr1urmt7.mp3),
# obtained via whisper ASR. Override with --prompt-text if needed.
PROMPT_TEXT_DEFAULT = "你好，请注意，这是一个参考音频测试。"


def log(*a):
    print("[synth_batch]", *a, flush=True)


def _make_rec(idx, row, out_rel, sr, model):
    """Build one manifest record (wav -> original sentence + index + metadata).

    Field set reconciled with both lora-prep and env-fix-ab:
      * lora-prep consumes: text=sentence, audio=PROJECT_DIR/wav (relative `wav`).
      * env-fix-ab expects: {"idx", "text", "audio"} with text=sentence.
    We emit BOTH naming conventions so every consumer is satisfied. `audio`
    is the absolute path; `wav` is the relative path (portable).
    """
    return {
        "index": idx,
        "idx": idx,                                  # env-fix-ab alias
        "wav": out_rel,                             # relative path (lora-prep)
        "audio": os.path.join(HERE, out_rel),       # absolute path (env-fix-ab)
        "text": row.get("sentence"),                # training-target Chinese text
        "sentence": row.get("sentence"),
        "char": row.get("char"),
        "pinyin_mark": row.get("pinyin_mark"),
        "pinyin_t3": row.get("pinyin_t3"),
        "model": model,
        "sample_rate": int(sr),
        "cosy3_text": row.get("cosy3_text"),
        "it2_text": row.get("it2_text"),
    }


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_rows(jsonl_path):
    rows = []
    with open(jsonl_path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                log(f"WARN: skipping malformed line {ln}: {e}")
    return rows


# ---------------------------------------------------------------------------
# CosyVoice3 engine
# ---------------------------------------------------------------------------
def build_cosy3(prompt_text_content):
    from cosyvoice.cli.cosyvoice import AutoModel
    log("loading CosyVoice3 from", COSY_MODEL_DIR)
    cosy = AutoModel(model_dir=COSY_MODEL_DIR)
    log("CosyVoice3 sample_rate =", cosy.sample_rate)
    return cosy, COSY_PROMPT_WAV, prompt_text_content


def synth_cosy3(cosy, prompt_wav, prompt_text_content, row, out_path):
    prompt_text = CV3_PREFIX + prompt_text_content
    gen = cosy.inference_zero_shot(
        row["cosy3_text"], prompt_text, prompt_wav, stream=False, speed=1.0
    )
    saved = False
    for j in gen:
        torchaudio.save(out_path, j["tts_speech"], cosy.sample_rate)
        saved = True
        break  # single short sentence -> one segment
    if not saved:
        raise RuntimeError("CosyVoice3 produced no audio for idx=%s" % row.get("idx"))
    return cosy.sample_rate


# ---------------------------------------------------------------------------
# IndexTTS2 engine
# ---------------------------------------------------------------------------
def build_it2(prompt_text_content):
    from indextts.infer_v2 import IndexTTS2
    log("loading IndexTTS2 from", IT2_MODEL_DIR)
    tts = IndexTTS2(
        cfg_path=IT2_CFG,
        model_dir=IT2_MODEL_DIR,
        use_fp16=True,
        use_cuda_kernel=False,
        use_deepspeed=False,
    )
    return tts, IT2_PROMPT_WAV


def synth_it2(tts, prompt_wav, prompt_text_content, row, out_path):
    # IndexTTS2 writes the file itself; text uses inline pinyin (it2_text).
    tts.infer(
        spk_audio_prompt=prompt_wav,
        text=row["it2_text"],
        output_path=out_path,
        verbose=False,
    )
    import soundfile as sf
    return sf.info(out_path).samplerate


# ---------------------------------------------------------------------------
# Optional: auto-detect prompt_text via whisper ASR on the prompt wav
# ---------------------------------------------------------------------------
def detect_prompt_text(prompt_wav):
    log("no --prompt-text given; running whisper ASR on", prompt_wav)
    import whisper
    import soundfile as sf
    import numpy as np
    w, sr = sf.read(prompt_wav, dtype="float32")
    w = np.asarray(w, dtype="float32")
    model = whisper.load_model("base")
    res = model.transcribe(w, language="zh", fp16=False)
    txt = res["text"].strip()
    log("detected prompt_text:", repr(txt))
    return txt


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Batch polyphone forced-pronunciation TTS")
    ap.add_argument("--model", choices=["cosy3", "it2"], default="cosy3",
                    help="TTS engine (default cosy3)")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process only the first N rows (0 = all)")
    ap.add_argument("--start", type=int, default=0,
                    help="Skip the first N rows (0-indexed)")
    ap.add_argument("--jsonl", default=DATA_JSONL, help="Input jsonl path")
    ap.add_argument("--out-dir", default=OUT_DIR, help="Output wav directory")
    ap.add_argument("--manifest", default=MANIFEST, help="Output manifest path")
    ap.add_argument("--prompt-wav", default=None, help="Override reference prompt wav")
    ap.add_argument("--prompt-text", default=None,
                    help="Transcription of the reference prompt (for cosy3). "
                         "If omitted, auto-detected via whisper ASR.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse jsonl and report, do NOT load models or synthesize")
    ap.add_argument("--logfile", default=None,
                    help="Optional log file (append mode). stdout/stderr redirected there.")
    args = ap.parse_args()

    if args.logfile:
        # 日志由脚本自身用 open() 写入, 不依赖 shell 的 '>' 重定向
        # (计划任务 cmd 上下文里 '>' 会被错误解析, 导致整批起不来)。
        _lf = open(args.logfile, "a", encoding="utf-8")
        sys.stdout = _lf
        sys.stderr = _lf

    rows = load_rows(args.jsonl)
    log(f"loaded {len(rows)} rows from {args.jsonl}")

    if args.dry_run:
        n = args.limit or len(rows)
        log(f"[dry-run] would synthesize up to {n} rows with engine={args.model}")
        # sanity-check required fields
        missing = 0
        for r in rows[:n]:
            need = ["idx", "sentence", "cosy3_text", "it2_text"]
            if any(k not in r for k in need):
                missing += 1
        log(f"[dry-run] rows missing required fields (idx/sentence/cosy3_text/it2_text): {missing}")
        idxs = [r.get("idx") for r in rows[:n]]
        log(f"[dry-run] first idx values: {idxs[:5]} ... last: {idxs[-1] if idxs else None}")
        return

    os.makedirs(args.out_dir, exist_ok=True)

    # Resolve engine + prompt
    if args.model == "cosy3":
        prompt_text_content = args.prompt_text or PROMPT_TEXT_DEFAULT
        if args.prompt_text is None:
            log("using default prompt_text:", repr(prompt_text_content))
        prompt_wav = args.prompt_wav or COSY_PROMPT_WAV
        cosy, prompt_wav, prompt_text_content = build_cosy3(prompt_text_content)
        engine_synth = lambda row, out: synth_cosy3(
            cosy, prompt_wav, prompt_text_content, row, out)
    else:
        prompt_wav = args.prompt_wav or IT2_PROMPT_WAV
        tts, prompt_wav = build_it2(prompt_text_content=None)
        engine_synth = lambda row, out: synth_it2(
            tts, prompt_wav, None, row, out)

    # Build manifest incrementally (append mode so resume keeps prior entries).
    # Track already-recorded idxs so a resumed run does not duplicate rows in
    # the manifest (it should only ADD new rows, never rewrite existing ones).
    manifest_f = open(args.manifest, "a", encoding="utf-8")
    seen_idx = set()
    if os.path.exists(args.manifest):
        with open(args.manifest, encoding="utf-8") as _mf:
            for _l in _mf:
                _l = _l.strip()
                if not _l:
                    continue
                try:
                    seen_idx.add(json.loads(_l).get("index"))
                except Exception:
                    pass

    done = 0
    skipped = 0
    generated = 0
    failed = 0
    consec_fail = 0
    t0 = time.time()
    per_sentence_times = []

    for i, row in enumerate(rows):
        if args.start and i < args.start:
            continue
        if args.limit and generated >= args.limit:
            break

        idx = row.get("idx", i)
        # 支持字符串编号(如 v23 的 'W1'/'123')与自定义输出目录
        out_name = f"{idx}.wav"
        out_abs = os.path.join(args.out_dir, out_name)
        out_rel = os.path.relpath(out_abs, HERE)

        if os.path.exists(out_abs) and os.path.getsize(out_abs) > 0:
            import soundfile as sf
            sr = sf.info(out_abs).samplerate
            if idx not in seen_idx:
                rec = _make_rec(idx, row, out_rel, sr, args.model)
                manifest_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                manifest_f.flush()
                seen_idx.add(idx)
            skipped += 1
            done += 1
            log(f"[{done}/{len(rows)}] idx={idx} -> {out_rel} (EXISTS, {sr}Hz, skipped)")
            continue

        t_start = time.time()
        try:
            sr = engine_synth(row, out_abs)
        except Exception as e:
            # 单句失败不再中断整批(长任务无人值守必须容错);
            # 但连续 5 句失败判定为系统性故障(如模型未加载), 仍中止以免静默跳过全部。
            log(f"ERROR synthesizing idx={idx}: {e!r}  (skip, will retry on next run)")
            failed += 1
            consec_fail += 1
            if consec_fail >= 5:
                log(f"ABORT: {consec_fail} consecutive synth failures -- likely systemic, stopping.")
                raise
            continue
        consec_fail = 0
        dt = time.time() - t_start
        per_sentence_times.append(dt)

        rec = _make_rec(idx, row, out_rel, sr, args.model)
        manifest_f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        manifest_f.flush()

        generated += 1
        done += 1
        log(f"[{done}/{len(rows)}] idx={idx} -> {out_rel} ({sr}Hz, {dt:.1f}s)")

    manifest_f.close()
    total = time.time() - t0
    log("=" * 50)
    log(f"engine={args.model}  generated={generated}  skipped(resume)={skipped}  failed={failed}")
    log(f"manifest -> {args.manifest}")
    if per_sentence_times:
        avg = sum(per_sentence_times) / len(per_sentence_times)
        log(f"avg per new sentence: {avg:.1f}s  (this run wall: {total:.1f}s)")
        if generated and args.limit and args.limit <= 5:
            est = avg * len(rows)
            log(f"ESTIMATE for full {len(rows)}: ~{est/60:.1f} min "
                f"(at {avg:.1f}s/sentence, single GPU, no resume)")


if __name__ == "__main__":
    main()
