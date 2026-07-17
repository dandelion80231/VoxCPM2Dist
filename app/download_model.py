# -*- coding: utf-8 -*-
"""下载 VoxCPM2 主模型与可选离线降噪模型到本安装目录。

仅依赖 Python 标准库（urllib / ssl），无需 pip 安装，可直接用随包 python_cuda 运行。
流程：先扫描全部必需文件 -> 列出缺失/损坏项 -> 再针对性下载（支持断点续传）。
主模型 ModelScope 为主源、失败回退 HuggingFace；降噪模型仅 ModelScope 源（可选项）。

本模块同时支持两种调用方式：
  1) CLI：直接运行（python_cuda\\python.exe download_model.py），由 main() 打印进度；
  2) 可编程：download_models(progress_cb=..., should_stop=...) 由网页后台线程调用，
     通过 progress_cb 回报进度、should_stop 支持取消，无额外打印。
"""
import os
import sys
import ssl
import urllib.request
import urllib.error


class _DownloadCancelled(Exception):
    """下载被 should_stop 中止时抛出（调用方据此标记 cancelled）。"""
    pass


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
os.makedirs(TARGET, exist_ok=True)

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
    url = base_url + fname
    req = urllib.request.Request(url)
    req.add_header("Range", "bytes=0-0")
    try:
        resp = urllib.request.urlopen(req, context=CTX, timeout=15)
        cr = resp.headers.get("Content-Range")
        if cr and "/" in cr:
            return int(cr.split("/")[-1])
        cl = resp.headers.get("Content-Length")
        if cl:
            return int(cl)
    except Exception:
        return None
    return None


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


def download_one(base_url, fname, dest, progress_cb=None, file_index=0, file_count=0, should_stop=None):
    """下载单个文件（支持断点续传）。返回 True 成功 / False 失败。

    progress_cb(dict): 每个数据块回报当前文件进度；should_stop(): 返回 True 时中止。
    """
    part = dest + ".part"
    start = os.path.getsize(part) if os.path.exists(part) else 0
    url = base_url + fname
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
    remaining = int(remaining) if remaining else None
    total = (start + remaining) if remaining else None
    got = start
    mode = "ab" if start > 0 else "wb"
    with open(part, mode) as f:
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
                sys.stdout.write("\r    %-22s %3d%%  %s / %s" % (fname, pct, _human(got), _human(total)))
            else:
                sys.stdout.write("\r    %-22s %s" % (fname, _human(got)))
            sys.stdout.flush()
            if progress_cb:
                frac = (file_index - 1)
                if total:
                    frac += pct / 100.0
                else:
                    frac += 0.5
                op = int(frac / file_count * 100) if file_count else (pct if total else 0)
                progress_cb({
                    "phase": "download", "file": fname, "downloaded": got, "total": total,
                    "percent": pct if total else None, "file_index": file_index,
                    "file_count": file_count, "overall_percent": op, "status": "downloading",
                })
    sys.stdout.write("\n")
    if total and got < total:
        print("    [警告] %s 下载大小不足（%s / %s），可能中断" % (fname, _human(got), _human(total)))
        return False
    os.replace(part, dest)
    return True


def _do_download(items, target, hf_fallback, progress_cb=None, should_stop=None, label=""):
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
            progress_cb({
                "phase": "download", "file": fname, "file_index": i, "file_count": n,
                "status": "downloading", "message": "正在下载 %s（%s）" % (fname, label),
            })
        print("[下载] " + fname)
        done = download_one(ms_base, fname, dest, progress_cb=progress_cb,
                            file_index=i, file_count=n, should_stop=should_stop)
        if not done and hf_fallback and hf_base:
            print("  ModelScope 失败，尝试 HuggingFace 回退...")
            done = download_one(hf_base, fname, dest, progress_cb=progress_cb,
                                file_index=i, file_count=n, should_stop=should_stop)
        if progress_cb:
            progress_cb({
                "phase": "download", "file": fname, "file_index": i, "file_count": n,
                "status": "done", "percent": 100,
                "overall_percent": int(i / n * 100) if n else 100,
                "message": "%s 下载完成" % fname,
            })
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
        progress_cb({"phase": "scan", "status": "scanning", "message": "正在检测模型文件…"})

    rep_main = scan_group(MAIN_ITEMS, TARGET)
    os.makedirs(ZIP_TARGET, exist_ok=True)
    rep_zip = scan_group(ZIP_ITEMS, ZIP_TARGET)

    if not rep_main["todo"] and not rep_zip["todo"]:
        if progress_cb:
            progress_cb({"phase": "done", "status": "done", "message": "模型文件均已就绪，无需下载。"})
        return True, True

    main_todo = [(f, MS_BASE, HF_BASE) for f in rep_main["todo"]]
    zip_todo = [(f, MS_ZIP_BASE, None) for f in rep_zip["todo"]]

    ok_main = _do_download(main_todo, TARGET, hf_fallback=True,
                           progress_cb=progress_cb, should_stop=should_stop, label="主模型 VoxCPM2")
    ok_zip = _do_download(zip_todo, ZIP_TARGET, hf_fallback=False,
                          progress_cb=progress_cb, should_stop=should_stop, label="离线降噪 ZipEnhancer")

    if progress_cb:
        if ok_main:
            progress_cb({"phase": "done", "status": "done",
                         "message": "模型下载完成。请返回主界面加载模型（或重启程序）。"})
        else:
            progress_cb({"phase": "done", "status": "error",
                         "message": "部分主模型文件未下载成功，请检查网络后重试。"})
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
    os.makedirs(ZIP_TARGET, exist_ok=True)
    rep_zip = scan_group(ZIP_ITEMS, ZIP_TARGET)
    print_report("离线降噪 ZipEnhancer（可选项）", rep_zip)
    print("")

    if not rep_main["todo"] and not rep_zip["todo"]:
        print("✅ 所有模型文件均已就绪，无需下载。直接启动程序即可。")
        return

    print("== 第二阶段：下载缺失/损坏的文件 ==")
    print("")

    # 复用 download_models 下载逻辑（不传 progress_cb，仍由内部 print 输出进度）
    ok_main, ok_zip = download_models(progress_cb=lambda p: None)

    if ok_main:
        print("✅ 主模型就绪。请回到程序主界面重新加载模型（或重启本程序）。")
    else:
        print("⚠️ 部分主模型文件未下载成功，请检查网络后重跑「下载模型.bat」。")
        sys.exit(1)

    if ok_zip:
        print("✅ 降噪模型就绪。重启程序即可在「降噪」选项中启用离线降噪。")
    else:
        print("⚠️ 降噪模型部分文件未下载（可选项）。如需离线降噪，可手动从")
        print("https://modelscope.cn/models/iic/speech_zipenhancer_ans_multiloss_16k_base 下载后放入")
        print("models\\zipenhancer\\，或复制完整版安装目录下的 models\\zipenhancer\\ 文件夹。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。重新运行「下载模型.bat」会先检测缺漏并断点续传。")
        sys.exit(130)
    except _DownloadCancelled:
        print("\n已取消下载。重新运行「下载模型.bat」会先检测缺漏并断点续传。")
        sys.exit(130)
