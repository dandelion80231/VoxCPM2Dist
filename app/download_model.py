# -*- coding: utf-8 -*-
"""下载 VoxCPM2 主模型权重到本安装目录的 model/openbmb/VoxCPM2。

仅依赖 Python 标准库（urllib / ssl），无需 pip 安装，可直接用随包 python_cuda 运行。
支持断点续传（.part 临时文件）。HuggingFace 为主源，失败自动回退 ModelScope。
"""
import os
import sys
import ssl
import urllib.request
import urllib.error

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


def download_one(base_url, fname, dest):
    """下载单个文件（支持断点续传）。返回 True 成功 / False 失败。"""
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
    sys.stdout.write("\n")
    if total and got < total:
        print("    [警告] %s 下载大小不足（%s / %s），可能中断" % (fname, _human(got), _human(total)))
        return False
    os.replace(part, dest)
    return True


def main():
    print("=" * 56)
    print("VoxCPM2 主模型一键下载")
    print("目标目录: " + TARGET)
    print("主源: ModelScope   OpenBMB/VoxCPM2")
    print("回退: HuggingFace  openbmb/VoxCPM2")
    print("提示: 大文件可断点续传；如需停止按 Ctrl+C，重跑本脚本会续传。")
    print("=" * 56)
    print("")

    ok_all = True
    for fname in FILES:
        dest = os.path.join(TARGET, fname)
        if os.path.exists(dest) and not os.path.exists(dest + ".part"):
            print("[跳过] 已存在: " + fname)
            continue
        print("[下载] " + fname)
        done = download_one(MS_BASE, fname, dest)
        if not done:
            print("  ModelScope 失败，尝试 HuggingFace 回退...")
            done = download_one(HF_BASE, fname, dest)
        if not done:
            print("[失败] " + fname + " 下载未完成。")
            print("  可改用 README 中的网盘/夸克链接手动放置，或检查网络后重跑本脚本。")
            ok_all = False
        else:
            print("[完成] " + fname)

    print("")
    if ok_all:
        print("全部模型文件就绪。请回到程序主界面重新加载模型（或重启本程序）。")
    else:
        print("部分文件未下载成功，请按上述提示处理后重跑「下载模型.bat」。")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断。重新运行「下载模型.bat」可断点续传。")
        sys.exit(130)
