#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
voxcpm_timestamps_qwen.py — VoxCPM2 高精度词/字级时间戳（Qwen3-ForcedAligner-0.6B，按需加载）

用法（对齐任意 wav + 已知文本，输出词级 JSON + 可选 SRT 字幕）:
  python Scripts\voxcpm_timestamps_qwen.py --audio out.wav --text "小兔子问：妈妈去哪儿了？"
  python Scripts\voxcpm_timestamps_qwen.py --audio out.wav --text-file text.txt --srt
  python Scripts\voxcpm_timestamps_qwen.py --audio out.wav --text "..." --chars   # 逐字（中文蹦字字幕）

特性:
  - 引擎：Qwen3-ForcedAligner-0.6B-hf（NAR 非自回归，transformers 5.13 原生内置，Apache-2.0）
    精度：词级 80ms 分辨率；官方报告累积平均偏移较 WhisperX 等降低 67%~77%
  - 按需加载：显存 ~1.8GB（bf16），对齐完自动 del + 清缓存释放
  - 支持单文件；>300 秒请分块（引擎上限 5 分钟）
  - 模型：本地 <dist>/models/qwen3_aligner/ 缺失时自动下载到该目录（HF 直连不通自动切 hf-mirror 国内镜像；小文件直下 + 大文件 8 路并行 Range 分片下载，~1.75GB 实测 ~2-3 分钟；仅首次下载，后续秒用）；无网可用 --no-download 跳过（配合 CLI 自动降级 whisper 兜底）
退出码: 0=成功；1=模型缺失；2=参数错
"""
import argparse
import json
import os
import sys
import threading
import time
import warnings

warnings.filterwarnings("ignore")


def _http_endpoint():
    """探测可用端点：HF 直连通则用它，否则国内镜像 hf-mirror（用 urllib，走系统 CA 库——dist 内置 certifi 太旧会 SSL 验证失败）。"""
    import urllib.request
    import ssl as _ssl
    try:
        _urlopen_guarded("https://huggingface.co")
        return "https://huggingface.co"
    except Exception:
        return "https://hf-mirror.com"


def _urlopen_guarded(url, timeout=10):
    """urllib GET：Windows 用系统证书库。"""
    import urllib.request
    return urllib.request.urlopen(url, timeout=timeout)


def _parallel_get(url, dest, workers=8, chunk_mb=64, progress_cb=None):
    """大文件 8 路并行 Range 分片下载（镜像对单连接限速，并行可提速 ~3-4x）。用 urllib（系统 CA）。progress_cb(dict{percent,message,phase}) 可选。"""
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor

    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    r = _urlopen_guarded(req, timeout=30)
    total = int(r.headers["Content-Range"].split("/")[1])
    chunk = max(chunk_mb * 1024 * 1024, total // (workers * 2))
    parts = []
    for i in range(0, total, chunk):
        parts.append((i, min(i + chunk - 1, total - 1)))

    lock = threading.Lock()
    done_bytes = {"v": 0}

    def fetch(part):
        s, e = part
        f = f"{dest}.p{s}"
        want = e - s + 1
        # 断点：分片已完整（大小匹配）则跳过
        if os.path.exists(f) and os.path.getsize(f) == want:
            with lock:
                done_bytes["v"] += want
                if progress_cb:
                    progress_cb({
                        "percent": min(99, int(done_bytes["v"] * 100 // total)),
                        "message": f"model.safetensors {done_bytes['v'] // (1024*1024)}/{total // (1024*1024)} MB", "file": os.path.basename(dest), "phase": "download",
                    })
            return f
        for attempt in range(4):  # 分片级重试（镜像瞬时 502/5xx 容错）
            try:
                rq = urllib.request.Request(url, headers={"Range": f"bytes={s}-{e}"})
                with _urlopen_guarded(rq, timeout=300) as rr:
                    with open(f, "wb") as fp:
                        while True:
                            blk = rr.read(1024 * 1024)
                            if not blk:
                                break
                            fp.write(blk)
                if os.path.getsize(f) != want:
                    raise RuntimeError(f"分片大小不符 {os.path.getsize(f)} != {want}")
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2 * (attempt + 1))
        with lock:
            done_bytes["v"] += want
            if progress_cb:
                progress_cb({
                    "percent": min(99, int(done_bytes["v"] * 100 // total)),
                    "message": f"model.safetensors {done_bytes['v'] // (1024*1024)}/{total // (1024*1024)} MB", "file": os.path.basename(dest), "phase": "download",
                })
        return f

    t0 = time.time()
    if progress_cb:
        progress_cb({"percent": 0, "message": f"开始 8 路并行下载 model.safetensors（{total // (1024*1024)} MB，分片可断点续传）", "file": "model.safetensors", "phase": "download"})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        files = list(pool.map(fetch, parts))
    # 清理旧分片后合并
    with open(dest, "wb") as out:
        for f in files:
            with open(f, "rb") as pf:
                out.write(pf.read())
            os.remove(f)
    # 清理残留分片（上次失败遗留的）
    import glob as _glob
    for old in _glob.glob(dest + ".p*"):
        try:
            os.remove(old)
        except Exception:
            pass
    if os.path.getsize(dest) != total:
        raise RuntimeError(f"下载体积不符（{os.path.getsize(dest)} != {total}）")
    print(f"[下载] model.safetensors {total // 1024 // 1024}MB（{workers} 路并行）{time.time() - t0:.0f}s")


def ensure_model(model_dir, allow_download=True, progress_cb=None):
    """本地模型在则直接返回；缺失且允许下载时下到本地 model_dir（8 路并行，自动切镜像）。成功返回目录，失败返回 None。"""
    if os.path.exists(os.path.join(model_dir, "model.safetensors")):
        return model_dir
    if not allow_download:
        return None
    repo = "Qwen/Qwen3-ForcedAligner-0.6B-hf"
    endpoint = _http_endpoint()
    base = f"{endpoint}/{repo}/resolve/main"
    files = ["config.json", "processor_config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "model.safetensors"]
    os.makedirs(model_dir, exist_ok=True)
    print(f"[下载] 首次使用，自动下载 Qwen3-ForcedAligner-0.6B（~1.75GB）→ {model_dir}（源: {endpoint}，8 路并行）")
    import urllib.request
    try:
        for f in files:
            dest = os.path.join(model_dir, f)
            if os.path.exists(dest):
                continue
            if f == "model.safetensors":
                _parallel_get(f"{base}/{f}", dest, progress_cb=progress_cb)
            else:
                if progress_cb:
                    progress_cb({"percent": 2, "message": f"下载小文件 {f}", "file": f, "phase": "download"})
                with _urlopen_guarded(f"{base}/{f}", timeout=120) as rr:
                    data = rr.read()
                with open(dest, "wb") as fp:
                    fp.write(data)
        if not os.path.exists(os.path.join(model_dir, "model.safetensors")):
            return None
        print(f"[下载] 完成 → {model_dir}")
        return model_dir
    except Exception as e:
        print(f"[下载] 失败（{type(e).__name__}: {e}）—— 重试或手动下载 {repo} 放到 {model_dir}", file=sys.stderr)
    return None


def resolve_model_dir():
    env = os.environ.get("VOXCPM_QWEN3_ALIGNER_DIR", "").strip()
    if env:
        return env
    # Scripts/ -> dist root
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "qwen3_aligner")
    return os.path.abspath(p)


def srt_escape(t):
    return t.replace("\n", " ").strip()


def to_srt(items, split_secs=2.5):
    """word/char items -> SRT blocks; greedy split so each cue <= split_secs."""
    cues = []
    cur = []
    cur_start = None
    for it in items:
        s, e = it["start_time"], it["end_time"]
        if cur_start is None:
            cur_start = s
        if cur and e - cur_start > split_secs:
            cues.append((cur_start, s, " ".join(cur)))
            cur, cur_start = [], s
        cur.append(srt_escape(it["text"]))
    if cur:
        cues.append((cur_start, items[-1]["end_time"], " ".join(cur)))
    lines = []
    for i, (s, e, txt) in enumerate(cues, 1):
        def fmt(t):
            h, r = divmod(int(t), 3600)
            m, r = divmod(r, 60)
            return f"{h:02d}:{m:02d}:{r:02d},{int((t - int(t)) * 1000):03d}"
        lines.append(f"{i}\n{fmt(s)} --> {fmt(e)}\n{txt}")
    return "\n\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--text-file", default="")
    ap.add_argument("--language", default="Chinese")
    ap.add_argument("--out", default="")
    ap.add_argument("--srt", action="store_true", help="同时输出 SRT（与 JSON 同名）")
    ap.add_argument("--chars", action="store_true", help="中文逐字：词 token 内字符线性均分（蹦字字幕）")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--no-download", action="store_true", help="模型缺失时不自动下载（直接报缺失，供 CLI 降级 whisper 兜底）")
    ap.add_argument("--keep-vram", action="store_true", help="结束后不释放显存（连续批量对齐用）")
    a = ap.parse_args()

    text = a.text
    if a.text_file:
        with open(a.text_file, "r", encoding="utf-8-sig") as f:
            text = f.read().strip()
    if not text:
        print("错误：需要 --text 或 --text-file", file=sys.stderr)
        sys.exit(2)
    if not os.path.exists(a.audio):
        print(f"错误：音频不存在 {a.audio}", file=sys.stderr)
        sys.exit(2)

    model_dir = resolve_model_dir()
    if not os.path.exists(os.path.join(model_dir, "model.safetensors")):
        model_dir = ensure_model(model_dir, allow_download=not a.no_download)
        if not model_dir:
            print("[QWEN3 对齐器] 模型缺失且自动下载失败", file=sys.stderr)
            print("（可用 --no-download 跳过，CLI 会自动降级 whisper 兜底；也可手动下载 Qwen/Qwen3-ForcedAligner-0.6B-hf 到 models\\qwen3_aligner\\）", file=sys.stderr)
            sys.exit(1)

    import torch
    from transformers import AutoProcessor, AutoModelForTokenClassification

    print(f"[加载] Qwen3-ForcedAligner-0.6B（bf16, {a.device}）: {model_dir}")
    t0 = time.time()
    proc = AutoProcessor.from_pretrained(model_dir)
    model = AutoModelForTokenClassification.from_pretrained(
        model_dir, dtype=torch.bfloat16
    )
    model.to(a.device)
    model.eval()
    ts_token = model.config.timestamp_token_id
    print(f"[加载] 完成（{time.time() - t0:.1f}s）")

    inp, word_lists = proc.prepare_forced_aligner_inputs(
        audio=a.audio, transcript=text, language=a.language
    )
    inp = {
        k: (v.to(a.device, torch.bfloat16) if "features" in k and torch.is_tensor(v) else v.to(a.device) if torch.is_tensor(v) else v)
        for k, v in inp.items()
    }
    t1 = time.time()
    with torch.inference_mode():
        out = model(**inp)
    items = proc.decode_forced_alignment(
        logits=out.logits,
        input_ids=inp["input_ids"],
        word_lists=word_lists,
        timestamp_token_id=ts_token,
    )[0]
    print(f"[对齐] {len(items)} 个 token，{time.time() - t1:.2f}s（NAR 单次前向）")

    if a.chars and len(text.strip()) > 0:
        # 词 token → 逐字（token 内字符按时间跨度均分）
        chars = []
        for it in items:
            seg = it["text"].replace(" ", "")
            n = max(len(seg), 1)
            span = max(it["end_time"] - it["start_time"], 0.0)
            step = span / n if n else 0.0
            for ci, ch in enumerate(seg):
                cs = it["start_time"] + ci * step
                ce = it["start_time"] + (ci + 1) * step
                chars.append({"char": ch, "start": round(cs, 3), "end": round(ce, 3),
                              "start_time": round(cs, 3), "end_time": round(ce, 3), "text": ch})
        items = chars

    out_path = a.out or os.path.splitext(a.audio)[0] + ".timestamps.json"
    payload = {
        "engine": "qwen3-forcedaligner-0.6b",
        "audio": os.path.basename(a.audio),
        "language": a.language,
        "items": items,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print(f"[保存] {out_path}")
    for it in items[:10]:
        key = "char" in it
        v = it.get("char", it.get("text", ""))
        print(f"   {v:<4} {it['start']:>8.3f}s → {it['end']:>8.3f}s")
    if len(items) > 10:
        print(f"   …（共 {len(items)} 项）")

    if a.srt:
        srt_path = os.path.splitext(out_path)[0] + ".srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            f.write(to_srt(items))
        print(f"[保存] SRT: {srt_path}")

    if not a.keep_vram:
        del model, proc, out, inp
        torch.cuda.empty_cache()
        try:
            import psutil
            proc_p = psutil.Process()
        except Exception:
            proc_p = None
        print("[释放] 对齐器已卸载，显存已清理")


if __name__ == "__main__":
    main()
