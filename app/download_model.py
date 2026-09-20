"""下载 VoxCPM2 主模型与可选离线降噪模型到本安装目录。

仅依赖 Python 标准库（urllib / ssl / concurrent.futures），无需 pip 安装，可直接用随包 python_cuda 运行。
流程：先扫描全部必需文件 -> 列出缺失/损坏项 -> 再针对性下载（支持断点续传）。
主模型 ModelScope 为主源、失败回退 HuggingFace；降噪模型仅 ModelScope 源（可选项）。

下载加速：大文件自动改走多线程 Range 切片下载（默认 8 线程，env VOXCPM_DL_THREADS 可调 1-32）：
每个切片独立落 <目标文件>.part.<k>，按切片自身大小续传；全部完成后拼接为 .part 再原子改名正式文件，
崩溃/取消后重跑自动续传。若源不支持 Range 或总大小未知，自动退回旧的单连接顺序下载，行为不变。

本模块同时支持两种调用方式：
  1) CLI：直接运行（python_cuda\\python.exe download_model.py），由 main() 打印进度；
  2) 可编程：download_models(progress_cb=..., should_stop=...) 由网页后台线程调用，
     通过 progress_cb 回报进度、should_stop 支持取消，无额外打印。
"""

import concurrent.futures
import os
import shutil
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request


class _DownloadCancelled(Exception):
    """下载被 should_stop 中止时抛出（调用方据此标记 cancelled）。"""


FILES = [
    "model.safetensors",
    "audiovae.pth",
    "config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "tokenization_voxcpm2.py",
]

HF_BASE = "https://huggingface.co/openbmb/VoxCPM2/resolve/main/"
MS_BASE = "https://modelscope.cn/models/OpenBMB/VoxCPM2/resolve/master/"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(APP_DIR, "model", "openbmb", "VoxCPM2")
try:
    os.makedirs(TARGET, exist_ok=True)
except Exception as _e:  # 目录不可建也不致脚本崩溃：后续下载/扫描会报具体错
    print("[警告] 无法创建模型目录 %s: %s" % (TARGET, _e))

# 离线降噪模型（ZipEnhancer，约 18MB），独立于主模型，位于另一个 ModelScope repo
ZIP_FILES = [
    "configuration.json",
    "onnx_model.onnx",
    "pytorch_model.bin",
]
MS_ZIP_BASE = "https://modelscope.cn/models/iic/speech_zipenhancer_ans_multiloss_16k_base/resolve/master/"
ZIP_TARGET = os.path.join(APP_DIR, "models", "zipenhancer")

# 每个文件记录 (文件名, 主源 base, 回退源 base 或 None)
MAIN_ITEMS = [(f, MS_BASE, HF_BASE) for f in FILES]
ZIP_ITEMS = [(f, MS_ZIP_BASE, None) for f in ZIP_FILES]

# ── 多线程下载调参 ──
MT_THREADS_DEFAULT = 8  # 默认并发段数（env VOXCPM_DL_THREADS 可覆盖，1-32）
CHUNK = 1024 * 1024  # 1MB 读块
SLICE_ALIGN = 32 * 1024 * 1024  # 切片边界 32MB 对齐
SLICE_MIN = 64 * 1024 * 1024  # 单段最小字节数（文件小于 段数*该值 时自动降档线程数）
RETRY_BACKOFFS = (2, 5, 15)  # 单段失败后的重试退避秒数（重试后仍失败才判整体失败）


def make_ctx():
    """尝试默认 CA 信任链；嵌入版 Python 常缺 CA 包，退化到不校验（仅下载公开模型，可接受）。"""
    try:
        return ssl.create_default_context()
    except Exception:
        pass
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    except Exception:
        return None


CTX = make_ctx()


def _human(n):
    return "%.1f MB" % (n / 1048576.0)


def remote_size(base_url, fname):
    """尝试获取远端文件总大小（用于完整性校验）。失败/超时返回 None（不判定）。"""
    url = _safe_url(base_url, fname)
    if url is None:
        return None
    req = urllib.request.Request(url)
    req.add_header("Range", "bytes=0-0")
    try:
        resp = urllib.request.urlopen(req, context=CTX, timeout=15)
        cr = resp.headers.get("Content-Range")
        if cr and "/" in cr:
            try:
                return int(cr.split("/")[-1])
            except Exception:
                return None
        cl = resp.headers.get("Content-Length")
        if cl:
            try:
                return int(cl)
            except Exception:
                return None
    except Exception:
        return None
    return None


def _mt_threads():
    """多线程下载段数（env VOXCPM_DL_THREADS 可调，1-32，默认 8；设 1 即单段）。"""
    try:
        v = int(os.environ.get("VOXCPM_DL_THREADS", "8"))
    except Exception:
        v = MT_THREADS_DEFAULT
    return max(1, min(32, v))


def _safe_url(base_url, fname):
    """拼出下载 URL，仅放行 http/https（本模块只用于拉取公开模型权重，防异常 scheme）。失败返回 None。"""
    try:
        u = base_url + fname
        return u if u.startswith(("http://", "https://")) else None
    except Exception:
        return None


def probe_range_support(base_url, fname):
    """探测远端是否支持 HTTP Range 及文件总大小。

    返回 (total_bytes_or_None, supports_range:bool)。
    206 -> 支持；200（服务端忽略 Range）-> 不支持，只能读 Content-Length 总大小；
    网络失败 -> (None, False)。
    """
    url = _safe_url(base_url, fname)
    if url is None:
        return None, False
    req = urllib.request.Request(url)
    req.add_header("Range", "bytes=0-0")
    try:
        resp = urllib.request.urlopen(req, context=CTX, timeout=15)
    except Exception:
        return None, False
    try:
        status = getattr(resp, "status", None) or getattr(resp, "code", 200)
        if status == 206:
            cr = resp.headers.get("Content-Range")
            if cr and "/" in cr:
                try:
                    return int(cr.split("/")[-1]), True
                except Exception:
                    return None, True
            return None, True
        cl = resp.headers.get("Content-Length")
        try:
            total = int(cl) if cl else None
        except Exception:
            total = None
        return total, False
    except Exception:
        return None, False
    finally:
        try:
            resp.close()
        except Exception:
            pass


def plan_slices(total, n):
    """把 total 字节切为 n 段连续切片（边界 32MB 对齐，小文件自动降档线程数）。

    返回 [(k, start, end)]，start/end 为闭区间字节偏移（全文件坐标）。
    入参异常时降级为单段全量切片（不抛异常）。
    """
    try:
        total = int(total)
        n = int(n)
    except Exception:
        return [(0, 0, -1)]
    if total <= 0:
        return [(0, 0, -1)]
    try:
        n = max(1, min(n, total // SLICE_MIN))
        base = int((total + n - 1) // n)
        base = int((base + SLICE_ALIGN - 1) // SLICE_ALIGN * SLICE_ALIGN)
    except Exception:
        return [(0, 0, -1)]
    slices, start, k = [], 0, 0
    while start < total:
        end = min(start + base - 1, total - 1)
        slices.append((k, start, end))
        start, k = end + 1, k + 1
    return slices


class _MTState:
    """多线程下载的工作线程共享状态。"""

    __slots__ = ("sizes", "lock", "stop", "cancelled", "fail")

    def __init__(self, n):
        self.sizes = [0] * n  # 每段已下载字节（切片文件自身大小）
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.cancelled = False
        self.fail = None  # 首个致命段错误文本


def _want_stop(state, should_stop):
    if state.stop.is_set():
        return True
    if should_stop and should_stop():
        return True
    return False


def _download_slice(base_url, fname, dest, k, start, end, state, should_stop):
    """工作线程：把 [start, end] 段下载到 <dest>.part.<k>，从切片文件自身大小续传。

    返回 'ok' / 'cancelled' / 'failed'。单段网络错误先按 RETRY_BACKOFFS 重试，
    重试耗尽才写 state.fail 并置 stop 事件（其他段看到后尽快收尾）。
    """
    url = _safe_url(base_url, fname)
    if url is None:
        state.fail = "slice %d: bad url" % k
        state.stop.set()
        return "failed"
    slf = "%s.part.%d" % (dest, k)
    slen = end - start + 1
    try:
        have = os.path.getsize(slf) if os.path.exists(slf) else 0
    except Exception:
        have = 0
    with state.lock:
        state.sizes[k] = have  # 协调线程据此统计已传量（含续传/已完成的段）
    if have == slen:
        return "ok"  # 本段已完整
    if have > slen:
        have = 0  # 异常超大切片文件：重拉本段
    err = ""
    attempts = 1 + len(RETRY_BACKOFFS)
    for attempt in range(attempts):
        if _want_stop(state, should_stop):
            state.cancelled = True
            state.stop.set()
            return "cancelled"
        try:
            req = urllib.request.Request(url)
            req.add_header("Range", "bytes=%d-%d" % (start + have, end))
            resp = urllib.request.urlopen(req, context=CTX, timeout=120)
            with open(slf, "ab" if have > 0 else "wb") as f:
                while True:
                    if _want_stop(state, should_stop):
                        state.cancelled = True
                        state.stop.set()
                        return "cancelled"
                    buf = resp.read(CHUNK)
                    if not buf:
                        break
                    f.write(buf)
                    have += len(buf)
                    with state.lock:
                        state.sizes[k] = have
                    if have >= slen:
                        return "ok"
            err = "truncated: %d/%d bytes" % (have, slen)
        except urllib.error.HTTPError as e:
            if e.code == 416:  # 范围不满足 -> 该段实际已完整
                return "ok"
            err = "HTTP %s" % e.code
        except Exception as e:
            err = "%s" % e
        if attempt + 1 < attempts:
            time.sleep(RETRY_BACKOFFS[attempt])
            if _want_stop(state, should_stop):
                state.cancelled = True
                state.stop.set()
                return "cancelled"
    state.fail = "slice %d: %s" % (k, err)
    state.stop.set()
    return "failed"


def scan_group(items, target):
    """扫描一组文件，按状态分类。

    返回 dict：
      missing    目标不存在且无 .part -> 需下载
      incomplete 存在 .part（上次中断）-> 需续传
      suspicious 存在但大小为 0 或联网比对不符 -> 需重新下载
      present    存在且校验通过 -> 跳过
      todo       missing + incomplete + suspicious（真正要处理的）
    """
    missing, incomplete, suspicious, present = [], [], [], []
    for fname, ms_base, _hf in items:
        dest = os.path.join(target, fname)
        part = dest + ".part"
        if os.path.exists(dest) and not os.path.exists(part):
            local = os.path.getsize(dest)
            if local == 0:
                suspicious.append(fname)
            else:
                expect = remote_size(ms_base, fname)
                if expect is not None and local != expect:
                    suspicious.append(fname)
                else:
                    present.append(fname)
        elif os.path.exists(part):
            incomplete.append(fname)
        else:
            missing.append(fname)
    todo = missing + incomplete + suspicious
    return {
        "missing": missing,
        "incomplete": incomplete,
        "suspicious": suspicious,
        "present": present,
        "todo": todo,
    }


def print_report(title, rep):
    print("  [检测] %s" % title)
    if rep["present"]:
        print("    完整(跳过): %d 个" % len(rep["present"]))
    if rep["missing"]:
        print("    缺失: %s" % ", ".join(rep["missing"]))
    if rep["incomplete"]:
        print("    未完成(续传): %s" % ", ".join(rep["incomplete"]))
    if rep["suspicious"]:
        print("    可疑/损坏(将重下): %s" % ", ".join(rep["suspicious"]))
    if not rep["todo"]:
        print("    全部就绪，无需下载。")


def download_one(
    base_url,
    fname,
    dest,
    progress_cb=None,
    file_index=0,
    file_count=0,
    should_stop=None,
):
    """下载单个文件（支持断点续传）。返回 True 成功 / False 失败。

    progress_cb(dict): 每个数据块回报当前文件进度；should_stop(): 返回 True 时中止。
    """
    part = dest + ".part"
    try:
        start = os.path.getsize(part) if os.path.exists(part) else 0
    except Exception:
        start = 0
    url = _safe_url(base_url, fname)
    if url is None:
        print("    [错误] 非法下载 URL: %s" % (base_url + fname)[:120])
        return False
    req = urllib.request.Request(url)
    if start > 0:
        req.add_header("Range", "bytes=%d-" % start)
    try:
        resp = urllib.request.urlopen(req, context=CTX, timeout=120)
    except urllib.error.HTTPError as e:
        if e.code == 416:  # 范围不满足 -> 已完整
            return True
        print("    [错误] HTTP %s 获取 %s" % (e.code, fname))
        return False
    except Exception as e:
        print("    [错误] %s (%s)" % (fname, e))
        return False

    remaining = resp.headers.get("Content-Length")
    try:
        remaining = int(remaining) if remaining else None
    except Exception:
        remaining = None
    total = (start + remaining) if remaining else None
    got = start
    pct = 0
    mode = "ab" if start > 0 else "wb"
    try:
        f = open(part, mode)
    except Exception as e:
        print("    [错误] 无法写入 %s (%s)" % (fname, e))
        return False
    with f:
        while True:
            if should_stop and should_stop():
                raise _DownloadCancelled()
            buf = resp.read(1024 * 1024)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if total:
                pct = got * 100 // total
                sys.stdout.write(
                    "\r    %-22s %3d%%  %s / %s"
                    % (fname, pct, _human(got), _human(total))
                )
            else:
                sys.stdout.write("\r    %-22s %s" % (fname, _human(got)))
            sys.stdout.flush()
            if progress_cb:
                frac = file_index - 1
                if total:
                    frac += pct / 100.0
                else:
                    frac += 0.5
                try:
                    op = (
                        int(frac / file_count * 100)
                        if file_count
                        else (pct if total else 0)
                    )
                except Exception:
                    op = pct if total else 0
                progress_cb(
                    {
                        "phase": "download",
                        "file": fname,
                        "downloaded": got,
                        "total": total,
                        "percent": pct if total else None,
                        "file_index": file_index,
                        "file_count": file_count,
                        "overall_percent": op,
                        "status": "downloading",
                    }
                )
    sys.stdout.write("\n")
    if total and got < total:
        print(
            "    [警告] %s 下载大小不足（%s / %s），可能中断"
            % (fname, _human(got), _human(total))
        )
        return False
    try:
        os.replace(part, dest)
    except Exception as e:
        print("    [错误] 重命名 %s (%s)" % (fname, e))
        return False
    return True


def download_one_mt(
    base_url,
    fname,
    dest,
    progress_cb=None,
    file_index=0,
    file_count=0,
    should_stop=None,
):
    """下载单个文件（多线程 Range 切片，逐段续传；源不支持 Range 时退回单连接顺序下载）。

    与 download_one 契约一致：返回 True 成功 / False 失败；should_stop() 为真时抛 _DownloadCancelled。
    progress_cb 额外提供两个字段：threads（实际并发段数）、speed_bps（近 0.5s 实测速率）。
    progress_cb 为 None 时由协调线程单线程打印进度（工作线程不打印，避免交错）。
    """
    total, supports = probe_range_support(base_url, fname)
    if not supports or not total:
        return download_one(
            base_url,
            fname,
            dest,
            progress_cb=progress_cb,
            file_index=file_index,
            file_count=file_count,
            should_stop=should_stop,
        )
    slices = plan_slices(total, _mt_threads())
    state = _MTState(len(slices))
    s0f = "%s.part.0" % dest
    s0len = slices[0][2] - slices[0][0] + 1
    # 兼容旧版单线程 .part 残留：前缀字节即切片 0 的有效数据
    try:
        old_part = dest + ".part"
        if os.path.exists(old_part):
            osz = os.path.getsize(old_part)
            if osz > s0len or os.path.exists(s0f) and os.path.getsize(s0f) >= osz:
                os.remove(old_part)
            else:
                os.replace(old_part, s0f)
                with state.lock:
                    state.sizes[0] = max(state.sizes[0], osz)
    except Exception:
        pass

    last_t, last_dl = time.time(), 0
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(slices), thread_name_prefix="dl"
    ) as ex:
        futs = [
            ex.submit(
                _download_slice, base_url, fname, dest, k, s, e, state, should_stop
            )
            for (k, s, e) in slices
        ]
        while True:
            fin = sum(1 for f in futs if f.done())
            if fin == len(futs):
                break
            now = time.time()
            dl = sum(state.sizes)
            if now - last_t >= 0.5:
                pct = min(99, dl * 100 // total)
                try:
                    speed = int((dl - last_dl) / max(now - last_t, 0.1))
                except Exception:
                    speed = 0
                frac = (file_index - 1) + pct / 100.0
                try:
                    op = int(frac / file_count * 100) if file_count else pct
                except Exception:
                    op = pct
                if progress_cb:
                    progress_cb(
                        {
                            "phase": "download",
                            "file": fname,
                            "downloaded": dl,
                            "total": total,
                            "percent": pct,
                            "file_index": file_index,
                            "file_count": file_count,
                            "overall_percent": op,
                            "status": "downloading",
                            "threads": len(slices),
                            "speed_bps": speed,
                        }
                    )
                else:
                    sys.stdout.write(
                        "\r    %-22s %3d%%  %s / %s  %dT  %d MB/s"
                        % (
                            fname,
                            pct,
                            _human(dl),
                            _human(total),
                            len(slices),
                            speed // (1024 * 1024),
                        )
                    )
                    sys.stdout.flush()
                last_t, last_dl = now, dl
            if should_stop and should_stop():
                state.cancelled = True
                state.stop.set()
            time.sleep(0.2)
        outcomes = [f.result() for f in futs]

    if any(o == "failed" for o in outcomes):
        print("    [错误] %s 分段下载失败: %s" % (fname, state.fail or "未知"))
        return False
    if any(o == "cancelled" for o in outcomes) or (should_stop and should_stop()):
        if not progress_cb:
            sys.stdout.write("\n")
        raise _DownloadCancelled()
    if not progress_cb:
        sys.stdout.write("\n")

    # ── 拼接：切片0→dest（原子改名），再逐段追加（追加后删该切片）；崩溃残留的前缀必在切片边界上，重跑可继续拼接 ──
    bounds = [0]
    for _k, _s, e in slices:
        bounds.append(e + 1)
    try:
        dsize = os.path.getsize(dest) if os.path.exists(dest) else 0
    except Exception:
        dsize = 0
    if dsize not in bounds:
        try:
            os.remove(dest)
        except Exception:
            pass
        dsize = 0
    if dsize == 0:
        try:
            os.replace(s0f, dest)
        except Exception as e:
            print("    [错误] 重命名切片0 %s (%s)" % (fname, e))
            return False
        startj = 1
    else:
        startj = bounds.index(dsize)
    try:
        with open(dest, "ab") as out:
            for j in range(startj, len(slices)):
                sjf = "%s.part.%d" % (dest, j)
                with open(sjf, "rb") as sl:
                    shutil.copyfileobj(sl, out, CHUNK)
                os.remove(sjf)
    except Exception as e:
        print("    [错误] 拼接 %s (%s)" % (fname, e))
        return False
    try:
        final_size = os.path.getsize(dest)
    except Exception:
        final_size = -1
    if final_size != total:
        print(
            "    [警告] %s 拼接结果 %s / %s，保留现场待重跑"
            % (fname, _human(final_size), _human(total))
        )
        return False
    for j in range(len(slices)):
        p = "%s.part.%d" % (dest, j)
        try:
            if os.path.exists(p):
                os.remove(p)
        except Exception:
            pass
    return True


def _do_download(
    items, target, hf_fallback, progress_cb=None, should_stop=None, label=""
):
    """按扫描结果下载指定文件。hf_fallback=True 时主源失败回退 HuggingFace。"""
    ok = True
    n = len(items)
    for i, (fname, ms_base, hf_base) in enumerate(items, start=1):
        if should_stop and should_stop():
            raise _DownloadCancelled()
        dest = os.path.join(target, fname)
        part = dest + ".part"
        # 可疑项：先删本地坏文件，确保触发重新下载（否则 download_one 会当成已存在跳过）
        if os.path.exists(dest) and not os.path.exists(part):
            try:
                os.remove(dest)
            except Exception:
                pass
        if progress_cb:
            progress_cb(
                {
                    "phase": "download",
                    "file": fname,
                    "file_index": i,
                    "file_count": n,
                    "status": "downloading",
                    "message": "正在下载 %s（%s）" % (fname, label),
                }
            )
        print("[下载] " + fname)
    done = download_one_mt(
        ms_base,
        fname,
        dest,
        progress_cb=progress_cb,
        file_index=i,
        file_count=n,
        should_stop=should_stop,
    )
    if not done and hf_fallback and hf_base:
        print("  ModelScope 失败，尝试 HuggingFace 回退...")
        done = download_one_mt(
            hf_base,
            fname,
            dest,
            progress_cb=progress_cb,
            file_index=i,
            file_count=n,
            should_stop=should_stop,
        )
        if progress_cb:
            try:
                op = int(i / n * 100) if n else 100
            except Exception:
                op = 100
            progress_cb(
                {
                    "phase": "download",
                    "file": fname,
                    "file_index": i,
                    "file_count": n,
                    "status": "done",
                    "percent": 100,
                    "overall_percent": op,
                    "message": "%s 下载完成" % fname,
                }
            )
        if not done:
            print("[失败] " + fname)
            ok = False
        else:
            print("[完成] " + fname)
    return ok


def download_models(progress_cb=None, should_stop=None):
    """可编程下载入口（供网页后台线程调用）。

    progress_cb(dict): 阶段/进度回调，字段含
        phase(scan|download|done) / status(scanning|downloading|done|error)
        / file / file_index / file_count / downloaded / total / percent
        / overall_percent / message
    should_stop(): 返回 True 时中止当前下载（已下载部分保留为 .part，可续传）。
    返回 (ok_main, ok_zip)。
    """
    if progress_cb:
        progress_cb(
            {"phase": "scan", "status": "scanning", "message": "正在检测模型文件…"}
        )

    rep_main = scan_group(MAIN_ITEMS, TARGET)
    try:
        os.makedirs(ZIP_TARGET, exist_ok=True)
    except Exception as _e:
        print("[警告] 无法创建降噪模型目录 %s: %s（跳过可选项）" % (ZIP_TARGET, _e))
        ZIP_TARGET = None
    rep_zip = (
        scan_group(ZIP_ITEMS, ZIP_TARGET)
        if ZIP_TARGET
        else {
            "missing": [],
            "incomplete": [],
            "suspicious": [],
            "present": [],
            "todo": [],
        }
    )

    if not rep_main["todo"] and not rep_zip["todo"]:
        if progress_cb:
            progress_cb(
                {
                    "phase": "done",
                    "status": "done",
                    "message": "模型文件均已就绪，无需下载。",
                }
            )
        return True, True

    main_todo = [(f, MS_BASE, HF_BASE) for f in rep_main["todo"]]
    zip_todo = [(f, MS_ZIP_BASE, None) for f in rep_zip["todo"]]

    ok_main = _do_download(
        main_todo,
        TARGET,
        hf_fallback=True,
        progress_cb=progress_cb,
        should_stop=should_stop,
        label="主模型 VoxCPM2",
    )
    ok_zip = _do_download(
        zip_todo,
        ZIP_TARGET,
        hf_fallback=False,
        progress_cb=progress_cb,
        should_stop=should_stop,
        label="离线降噪 ZipEnhancer",
    )

    if progress_cb:
        if ok_main:
            progress_cb(
                {
                    "phase": "done",
                    "status": "done",
                    "message": "模型下载完成。请返回主界面加载模型（或重启程序）。",
                }
            )
        else:
            progress_cb(
                {
                    "phase": "done",
                    "status": "error",
                    "message": "部分主模型文件未下载成功，请检查网络后重试。",
                }
            )
    return ok_main, ok_zip


def main():
    print("=" * 56)
    print("VoxCPM2 模型下载（先检测缺漏，再针对性下载）")
    print("主模型目标: " + TARGET)
    print("降噪目标:   " + ZIP_TARGET)
    print("提示: 已完整下载的文件会自动跳过；中断可续传；重跑即补缺。")
    print("=" * 56)
    print("")

    print("== 第一阶段：检测缺失/损坏的模型文件 ==")
    rep_main = scan_group(MAIN_ITEMS, TARGET)
    print_report("主模型 VoxCPM2", rep_main)
    try:
        os.makedirs(ZIP_TARGET, exist_ok=True)
    except Exception as _e:
        print("[警告] 无法创建降噪模型目录 %s: %s（跳过可选项）" % (ZIP_TARGET, _e))
        ZIP_TARGET = None
    rep_zip = (
        scan_group(ZIP_ITEMS, ZIP_TARGET)
        if ZIP_TARGET
        else {
            "missing": [],
            "incomplete": [],
            "suspicious": [],
            "present": [],
            "todo": [],
        }
    )
    print_report("离线降噪 ZipEnhancer（可选项）", rep_zip)
    print("")

    if not rep_main["todo"] and not rep_zip["todo"]:
        print("✅ 所有模型文件均已就绪，无需下载。直接启动程序即可。")
        return

    print("== 第二阶段：下载缺失/损坏的文件 ==")
    print("")

    # 复用 download_models 下载逻辑（不传 progress_cb，内部 print 输出进度；多线程路径由协调线程打印）
    ok_main, ok_zip = download_models()

    if ok_main:
        print("✅ 主模型就绪。请回到程序主界面重新加载模型（或重启本程序）。")
    else:
        print("⚠️ 部分主模型文件未下载成功，请检查网络后重跑「下载模型.bat」。")
        sys.exit(1)

    if ok_zip:
        print("✅ 降噪模型就绪。重启程序即可在「降噪」选项中启用离线降噪。")
    else:
        print("⚠️ 降噪模型部分文件未下载（可选项）。如需离线降噪，可手动从")
        print(
            "https://modelscope.cn/models/iic/speech_zipenhancer_ans_multiloss_16k_base 下载后放入"
        )
        print(
            "models\\zipenhancer\\，或复制完整版安装目录下的 models\\zipenhancer\\ 文件夹。"
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。重新运行「下载模型.bat」会先检测缺漏并断点续传。")
        sys.exit(130)
    except _DownloadCancelled:
        print("\n已取消下载。重新运行「下载模型.bat」会先检测缺漏并断点续传。")
        sys.exit(130)
