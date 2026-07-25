#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每 10 分钟由 Scheduled Task (TrainV23Monitor) 调用。
从 train_v23.log 取最新 step/loss/lr，并结合上一条 progress 记录【自测步速】，
追加一行到 progress_10min.log。仅读日志、写一行摘要，不做任何改动/训练操作。

为何自测步速而非解析日志里的 'log interval'：
train_lora.py 把 interval 数值打在单独一行（与 'log interval:' 不 contiguous），
逐行正则抽不到；且 stdout 经 Tee-Object 块缓冲，行时机不稳。
最稳的办法是用本监控自身两次运行之间的 (步数差 / 真实墙钟)，自校正 ETA。
"""
import re
import pathlib
import datetime

BASE = pathlib.Path(r"D:\AI\Build\多音字")


def read_text_robust(path):
    """Tee-Object 把训练 stdout/stderr 写成 UTF-16 LE(BOM=ff fe)，
    Get-Content 能自动识别，但 Python 默认 utf-8 会静默丢字节。
    这里按 BOM 探测：UTF-16 / UTF-8-BOM / 退化为 utf-8。"""
    data = path.read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="ignore")
    if data[:3] == b"\xef\xbb\xbf":
        return data.decode("utf-8-sig", errors="ignore")
    return data.decode("utf-8", errors="ignore")
LOG = BASE / "train_v23.log"
OUT = BASE / "progress_10min.log"
TOTAL = 3047
FALLBACK_PER_STEP = 39  # 兜底：实测全程 ~38-39s/step（首次运行、无历史时用）


def parse_progress_history():
    """从已有 progress 记录抽出 [(ts_datetime, step), ...]。"""
    hist = []
    if OUT.exists():
        for ln in OUT.read_text(encoding="utf-8", errors="ignore").splitlines():
            # 注意：历史行可能带/不带年份两种格式，统一按“带年份”解析；
            # 旧格式（无年份）会被 strptime 默认成 1900 年，导致 dt 差 126 年，
            # 因此下方 main() 里加了 dt 合理性闸门（见 per_step 计算处）。
            m = re.search(r"^(\d{4}-)?(\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| step (\d+)/", ln)
            if m and m.group(1):
                # 只接受带年份的行（无年份会被 strptime 默认 1900 年，导致 dt 差 ~126 年、
                # 闸门误判、永远回退兜底步速）
                try:
                    ts = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d %H:%M:%S")
                    hist.append((ts, int(m.group(3))))
                except ValueError:
                    pass
    return hist


def main():
    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    if not LOG.exists():
        line = f"{ts} | LOG_MISSING"
    else:
        text = read_text_robust(LOG)
        lines = text.splitlines()
        train = [l for l in lines if l.startswith("[train] step")]
        err_kw = ["Error", "Traceback", "Exception",
                  "CUDA out of memory", "RuntimeError", "raise "]
        errs = [l for l in lines[-60:] if any(k in l for k in err_kw)]
        if train:
            m = re.search(
                r"step (\d+).*?loss/diff:\s*([\d.]+).*?lr:\s*([\d.eE+-]+)",
                train[-1],
            )
            if m:
                n = int(m.group(1))
                loss = m.group(2)
                lr = m.group(3)
                pct = n / TOTAL * 100
                # 自测步速：用上一条 progress 记录。
                # 闸门：两次运行本应间隔 ~10min，若 dt 落在合理区间(0,2h]才采用，
                # 否则（旧格式年份错乱 / 跨多日 / 解析异常）回退兜底步速，避免 ETA 爆掉。
                hist = parse_progress_history()
                per_step = None
                if hist:
                    # 用最早一条有效记录算「全程累计平均步速」——比相邻两次差最稳，
                    # 不会因恰好跨 10/20 步的相位抖动在 30~60s 间乱跳。
                    pts, pstep = hist[0]
                    dt = (now - pts).total_seconds()
                    if 0 < dt <= 3 * 86400 and n > pstep:
                        per_step = dt / (n - pstep)
                if per_step is None or not (5 < per_step < 600):
                    per_step = FALLBACK_PER_STEP
                eta_h = (TOTAL - n) * per_step / 3600
                line = (f"{ts} | step {n}/{TOTAL} ({pct:.1f}%) "
                        f"loss/diff={loss} lr={lr} ETA~{eta_h:.1f}h "
                        f"(~{per_step:.0f}s/step)")
                if hist and n <= hist[-1][1]:
                    line += " | STALLED? no step progress since last interval"
            else:
                line = f"{ts} | step-line unparsed: {train[-1][-120:]}"
        else:
            line = f"{ts} | RUNNING (no [train] step line yet; stdout buffering?)"
        if errs:
            line += f" | ERR: {errs[-1][-120:]}"
    with OUT.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


if __name__ == "__main__":
    main()
