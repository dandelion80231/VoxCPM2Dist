# -*- coding: utf-8 -*-
"""voxcpm_openai.py — VoxCPM2 OpenAI 兼容 API 适配层(端口 8020, 随 v5.3.7 安装包交付)

把本地 v5.3.6 dist 的自定义 REST 服务(vox_web_ui.py, 端口 18978)映射为
文档《VoxCPM2 API调试AI视频工作台完整执行方案》要求 OpenAI 兼容端点,
供灵剪/Lingji Cut、OpenReel Studio 等工作台直接接入:

  GET  /health                      健康检查(含后端 ping)
  GET  /v1/models                   模型列表(openbmb/VoxCPM2 + 别名 voxcpm2)
  POST /v1/audio/speech             语音合成(JSON → WAV)
       字段: model/input/voice/response_format(仅 wav)/speed(接收忽略)/style(或 instructions/control)/seed
       VoxCPM 扩展: voxcpm.{cfg_value,inference_timesteps,normalize,denoise,retry_badcase}
                 seed 可放顶层或 voxcpm.seed(可复现种子: 同种子+同文本 结果近似一致)
                 mode(reference=hifi 需 prompt_text 的克隆 | hifi), ref_audio, prompt_text, control
  GET  /v1/audio/voices (+ /v1/voices)          内置 11 预设 + 已上传音色
  POST /v1/audio/voices (multipart: audio_sample/file + voice_id/name/purpose/prompt_text/mode)
  DELETE /v1/audio/voices/{id} (+ /v1/voices/{id})
  POST /v1/files (+ GET)            旧版上传/发现回退

映射规则:
  - voice 是内置预设名 → 后端 mode=voice_design(预设 + control 指令)
  - voice 是已上传 voice_id → 后端 mode=fixed_clone + reference_path(voices/<id>/ref.wav)
  - ref_audio 直传路径 → 后端 mode=fixed_clone + reference_path
  - hifi: 上传音色 meta 或请求 prompt_text → fixed_clone + prompt_text(Ultimate Clone)
  - 输出: 200 + application/octet-stream(WAV); 后端错误: 502 JSON

用 dist 的 python_cuda 跑(fastapi/uvicorn 自带, 不加载模型, 不占额外显存)。
"""
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response

BACKEND = os.environ.get("VOXCPM_BACKEND", "http://127.0.0.1:18978")
VOICES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "voices")
os.makedirs(VOICES_DIR, exist_ok=True)
VALID_MODELS = {"openbmb/voxcpm2", "voxcpm2"}
app = FastAPI(title="VoxCPM2 OpenAI-Compatible Adapter")

def _safe_json(b: bytes):
    """UTF-8 优先; GBK 回退(Windows 终端 curl 可能发 GBK 字节); 都失败 → 400。"""
    try:
        return json.loads(b.decode("utf-8"))
    except UnicodeDecodeError:
        try:
            return json.loads(b.decode("gbk"))
        except Exception:
            raise HTTPException(400, "请求体需为 JSON 编码 UTF-8(Windows 终端 GBK 已自动回退, 仍失败请检查内容)")

LOG = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter.log"), "a", encoding="utf-8", errors="replace")
def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    LOG.write(line + "\n"); LOG.flush(); print(line, flush=True)

def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")

def _post_form(url, fields, timeout=30):
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

# ── health / models ────────────────────────────────────
@app.get("/health")
def health():
    try:
        ping = _get(BACKEND + "/api/ping", 5)
        return {"status": "ok", "backend": BACKEND, "backend_ping": ping.strip()[:80]}
    except Exception as e:
        return JSONResponse({"status": "degraded", "backend": BACKEND, "error": str(e)}, status_code=503)

@app.get("/v1/models")
def models():
    return {"object": "list", "data": [
        {"id": "openbmb/VoxCPM2", "object": "model", "owned_by": "voxcpm-local"},
        {"id": "voxcpm2", "object": "model", "owned_by": "voxcpm-local", "root": "openbmb/VoxCPM2"},
    ]}

# ── voices ─────────────────────────────────────────────
def uploaded_voices():
    out = []
    for d in sorted(os.listdir(VOICES_DIR)):
        meta = os.path.join(VOICES_DIR, d, "meta.json")
        ref = os.path.join(VOICES_DIR, d, "ref.wav")
        if os.path.exists(meta):
            m = json.load(open(meta, encoding="utf-8"))
            out.append({**m, "voice_id": d, "ref_exists": os.path.exists(ref)})
    return out

_builtin_cache = [None]
def builtin_voices():
    if _builtin_cache[0] is None:
        try:
            _builtin_cache[0] = set(json.loads(_get(BACKEND + "/api/voices", 10)).get("voices", {}).keys())
        except Exception:
            _builtin_cache[0] = set()
    return _builtin_cache[0]

@app.get("/v1/audio/voices")
@app.get("/v1/voices")
def list_voices():
    try:
        builtin = json.loads(_get(BACKEND + "/api/voices")).get("voices", {})
    except Exception:
        builtin = {}
    return {"builtin": builtin, "uploaded": uploaded_voices()}

@app.post("/v1/audio/voices")
@app.post("/v1/voices")
async def upload_voice(request: Request):
    if request.headers.get("content-type", "").startswith("multipart"):
        form = await request.form()
        fid = str(form.get("voice_id") or "").strip()
        audio = form.get("audio_sample") or form.get("file")
        if not fid or not audio or not hasattr(audio, "read"):
            raise HTTPException(400, "需要 voice_id + audio_sample(或 file) 音频字段")
        d = os.path.join(VOICES_DIR, fid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "ref.wav"), "wb") as f:
            f.write(audio.read())
        meta = {
            "voice_id": fid,
            "name": str(form.get("name") or fid),
            "purpose": str(form.get("purpose") or ""),
            "prompt_text": str(form.get("prompt_text") or ""),
            "mode": str(form.get("mode") or "reference"),
        }
        json.dump(meta, open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"voice upload: {fid} ({meta['mode']})")
        return {"ok": True, "voice_id": fid, "path": d}
    # JSON 回退: 指定本地文件路径
    b = _safe_json(await request.body())
    fid = b.get("voice_id"); src = b.get("audio_sample") or b.get("file")
    if not fid or not src or not os.path.isfile(src):
        raise HTTPException(400, "JSON 回退需要 voice_id + 本地存在的 audio_sample 路径")
    d = os.path.join(VOICES_DIR, fid); os.makedirs(d, exist_ok=True)
    shutil.copyfile(src, os.path.join(d, "ref.wav"))
    json.dump({"voice_id": fid, "name": b.get("name", fid), "purpose": b.get("purpose", ""),
               "prompt_text": b.get("prompt_text", ""), "mode": b.get("mode", "reference")},
              open(os.path.join(d, "meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return {"ok": True, "voice_id": fid}

@app.delete("/v1/audio/voices/{voice_id}")
@app.delete("/v1/voices/{voice_id}")
def delete_voice(voice_id: str):
    d = os.path.join(VOICES_DIR, voice_id)
    if not os.path.isdir(d):
        raise HTTPException(404, f"音色 {voice_id} 不存在")
    shutil.rmtree(d)
    log(f"voice delete: {voice_id}")
    return {"ok": True, "voice_id": voice_id}

# ── files 回退 ─────────────────────────────────────────
@app.post("/v1/files")
def upload_file(file: UploadFile = File(...), purpose: str = Form("voices")):
    fn = re.sub(r"[^\w.\-]", "_", file.filename or "upload.bin")
    p = os.path.join(VOICES_DIR, fn)
    with open(p, "wb") as f:
        f.write(file.file.read())
    return {"id": fn, "filename": fn, "bytes": os.path.getsize(p)}

@app.get("/v1/files")
def list_files():
    return {"data": [{"id": f, "filename": f} for f in os.listdir(VOICES_DIR) if os.path.isfile(os.path.join(VOICES_DIR, f))] +
              [{"id": f, "filename": f} for f in uploaded_voices()]}

# ── speech ─────────────────────────────────────────────
@app.post("/v1/audio/speech")
async def speech(request: Request):
    raw = await request.body()
    b = _safe_json(raw)
    model = str(b.get("model", "voxcpm2")).lower()
    if model.lower() not in VALID_MODELS and model not in VALID_MODELS:
        raise HTTPException(400, f"model 需为 {sorted(VALID_MODELS)}")
    text = str(b.get("input") or b.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "input 不能为空")
    rf = str(b.get("response_format", "wav")).lower()
    if rf != "wav":
        raise HTTPException(400, "response_format 目前仅支持 wav")
    if b.get("speed") not in (None, 1, 1.0):
        log(f"speed={b.get('speed')} 接收但忽略(VoxCPM2 无语速旋钮, 用 control 指令控制)")

    vcfg = b.get("voxcpm") or {}
    fields = {
        "text": text,
        "voice": "default",
        "control_text": str(b.get("style") or b.get("instructions") or b.get("control") or ""),
        "mode": "voice_design",
        "cfg": str(vcfg.get("cfg_value", 2.5)),
        "steps": str(vcfg.get("inference_timesteps", 15)),
        "denoise": "true" if vcfg.get("denoise") else "false",
        "normalize": "true" if vcfg.get("normalize", True) else "false",
        "prompt_text": "",
        "reference_path": "",
    }
    if vcfg.get("retry_badcase"):
        log("retry_badcase 接收但忽略(后端无此开关)")
    seed_val = b.get("seed")
    if seed_val is None:
        seed_val = vcfg.get("seed")
    if seed_val not in (None, ""):
        try:
            fields["seed"] = str(int(float(seed_val)))
        except (ValueError, TypeError):
            log(f"seed={seed_val!r} 非有效整数，已忽略（随机合成）")

    voice = str(b.get("voice") or "default")
    warnings = []
    if fields["control_text"]:
        warnings.append("control-channel in use (instructions): possible readout on v5.3.6; prefer preset_* fixed_clone voices; MUST listen before delivery")
    meta = None
    for u in uploaded_voices():
        if u["voice_id"] == voice:
            meta = u
            break
    ref_audio = b.get("ref_audio") or b.get("reference_audio") or ""
    prompt_text = str(b.get("prompt_text") or "")
    strict = bool(b.get("strict", False))

    if ref_audio:
        p = str(ref_audio)
        # 允许传入已上传 voice 的文件名
        if not os.path.isabs(p):
            cand = os.path.join(VOICES_DIR, p)
            p = cand if os.path.isfile(cand) else cand
        if not os.path.isfile(p):
            raise HTTPException(400, f"ref_audio 不存在: {ref_audio}")
        fields["mode"] = "fixed_clone"
        fields["reference_path"] = p
        fields["prompt_text"] = prompt_text if str(b.get("mode")) == "hifi" else ""
        log(f"speech: ref_audio={p} hifi={fields['prompt_text']!r} text={text[:30]!r}...")
    elif meta is not None:
        fields["mode"] = "fixed_clone"
        fields["voice"] = meta.get("voice", "default")
        fields["reference_path"] = os.path.join(VOICES_DIR, meta["voice_id"], "ref.wav")
        mode = meta.get("mode") or ("hifi" if meta.get("prompt_text") else "reference")
        fields["prompt_text"] = meta.get("prompt_text", "") if mode == "hifi" else ""
        if not fields["control_text"]:
            fields["control_text"] = ""
        # 护栏 B-①: 可控克隆 + prompt_text 隐式切 hifi(文档 1.7) → 显式警告/拒绝
        if mode == "hifi" and meta.get("prompt_text") and meta.get("mode") != "hifi":
            if strict:
                raise HTTPException(400, f"音色 {voice} 带 prompt_text, 隐式切 hifi(文档1.7); 要么重传不带 prompt_text 的参考, 要么显式 mode=hifi; strict=1 已拒绝隐式切换")
            warnings.append(f"voice {voice} has prompt_text -> auto-hifi (doc1.7); use strict=1 to reject implicit switch")
        log(f"speech: uploaded voice={voice} mode={mode}")
    elif voice != "default":
        # 内置预设白名单校验(防拼错静默落 default)
        known = builtin_voices() | {u["voice_id"] for u in uploaded_voices()}
        if known and voice not in known:
            raise HTTPException(400, f"voice={voice!r} 不存在。内置: {sorted(known & builtin_voices())}; 已上传: {sorted(known - builtin_voices())}")
        # 内置预设名直接透传 voice_design
        fields["voice"] = voice
        log(f"speech: builtin voice={voice}")
    if str(b.get("mode")) == "hifi" and not fields["prompt_text"] and not ref_audio:
        raise HTTPException(400, "hifi 模式需要 ref_audio 或已上传 hifi 音色, 且需 prompt_text")

    # 提交后端
    try:
        job = _post_form(BACKEND + "/api/tts", fields, timeout=60)
    except Exception as e:
        raise HTTPException(502, f"后端不可用: {e}")
    job_id = job.get("job_id")
    log(f"speech job={job_id}")
    deadline = time.time() + 1800
    while time.time() < deadline:
        st = json.loads(_get(f"{BACKEND}/api/status/{job_id}", 15))
        s = st.get("status")
        if s == "done":
            out = st.get("output_wav")
            # 护栏 B-③: 时长异常检测(中文 ~4.5 字/秒; 超 1.6 倍疑似控制词被念出)
            dur = st.get("duration")
            if dur:
                expected = 0.22 * len(text) + 1.0
                if dur > expected * 1.6 + 1.0:
                    warnings.append(f"DURATION ANOMALY: {dur}s vs expected ~{expected:.1f}s ({len(text)} chars): possible control-word readout; MUST listen")
            with urllib.request.urlopen(f"{BACKEND}/api/audio/{out}", timeout=60) as r:
                data = r.read()
            log(f"speech ok job={job_id} out={out} {len(data)//1024}KB" + (f" WARN:{warnings}" if warnings else ""))
            # 响应头是 latin-1: 非 ASCII 必须转义, 否则 UnicodeEncodeError→500(音频已合成但丢失)
            hdrs = {"X-Output-Format": "wav", "X-VoxCPM-Meta": json.dumps(
                {"job_id": job_id, "duration": st.get("duration"),
                 "num_chunks": st.get("num_chunks"), "voice": fields["voice"],
                  "mode": fields["mode"], "style": fields["control_text"]}, ensure_ascii=True)}
            if warnings:
                hdrs["X-VoxCPM-Warning"] = " | ".join(warnings)
            return Response(content=data, media_type="audio/wav", headers=hdrs)
        if s == "error":
            raise HTTPException(502, json.dumps({"error": st.get("message")}, ensure_ascii=False))
        time.sleep(2)
    raise HTTPException(504, "合成超时(30min)")

if __name__ == "__main__":
    import uvicorn
    log("adapter starting on :8020 -> backend " + BACKEND)
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("VOXCPM_API_PORT", "8020")), log_level="warning")
