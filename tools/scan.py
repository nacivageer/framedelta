#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫一段长视频，找「变化量最大的几个窗口」——给评测集选素材用。

用法
    python tools/scan.py <视频> [--fps 2] [--window 3] [--topk 5]

做法：ffmpeg 按 fps 抽小图（160×90）流式解码，算相邻帧平均绝对差，
再按 window 秒滑窗求和，报 top-k 窗口的起止秒与窗口内变化量。
只用来挑素材，不产出评价。
"""
import argparse
import subprocess
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def stream_small(path, fps, w=160, h=90):
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", "fps=%s,scale=%d:%d" % (fps, w, h),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fb = w * h * 3
    prev = None
    idx = 0
    while True:
        buf = proc.stdout.read(fb)
        if not buf or len(buf) < fb:
            break
        cur = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3).astype(np.int16)
        if prev is not None:
            yield idx, float(np.abs(cur - prev).mean())
        prev = cur
        idx += 1
    proc.stdout.close()
    proc.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--window", type=float, default=3.0, help="窗口长度（秒）")
    ap.add_argument("--topk", type=int, default=5)
    ap.add_argument("--gap", type=float, default=5.0, help="选中窗口之间至少隔多少秒")
    a = ap.parse_args()

    diffs = [(i / a.fps, e) for i, e in stream_small(a.input, a.fps)]
    if not diffs:
        print("没解出帧", file=sys.stderr)
        return 2

    win = max(int(round(a.window * a.fps)), 1)
    scores = []
    for s in range(0, max(len(diffs) - win + 1, 1)):
        chunk = diffs[s:s + win]
        scores.append((sum(e for _, e in chunk), diffs[s][0], diffs[s][0] + a.window))
    scores.sort(reverse=True)

    picked = []
    for score, t0, t1 in scores:
        if all(abs(t0 - p[1]) >= a.gap for p in picked):
            picked.append((score, t0, t1))
        if len(picked) >= a.topk:
            break

    print("%s  时长约 %.1fs，共 %d 个 2fps 采样点" % (a.input, diffs[-1][0], len(diffs)))
    print("%-6s %-12s %-10s %s" % ("排名", "起点(s)", "终点(s)", "变化量"))
    for k, (score, t0, t1) in enumerate(picked, 1):
        print("%-6d %-12.1f %-10.1f %.2f" % (k, t0, t1, score))
    return 0


if __name__ == "__main__":
    sys.exit(main())
