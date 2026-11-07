#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按帧号抽帧拼一条带帧号与时间的对照图——写/核真值时用。

用法
    python tools/frames.py <源> --out runs/_probe/x.png 448 449 450 451
    python tools/frames.py <源> --out x.png --around 451 3      # 451 前后各 3 帧

为什么不用 tools/peek.py：peek 按毫秒取帧，取的是「t 时刻开始的那一帧」；
跳变正好落在帧边界上时会取到跳变**前**那一帧，1 帧长的硬切看起来就像没变化。
真值复核一律按帧号取（digest 的 jumps[].frame 直接就是那个帧号）。
"""
import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import _fit, _font, load_frames  # noqa: E402


def frame_times(durs):
    times, t = [], 0.0
    for d in durs:
        times.append(t)
        t += d
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("frames", nargs="*", type=int, help="帧号（0 起）")
    ap.add_argument("--around", nargs=2, type=int, metavar=("帧号", "半径"),
                    help="取某帧前后各 N 帧，替代显式帧号")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--cell-w", type=int, default=400)
    a = ap.parse_args()

    frames, durs, _ = load_frames(Path(a.input), None)
    times = frame_times(durs)
    n = len(frames)

    idxs = list(a.frames)
    if a.around:
        c, r = a.around
        idxs = list(range(max(0, c - r), min(n, c + r + 1))) + idxs
    idxs = sorted(dict.fromkeys(idxs))
    bad = [i for i in idxs if not 0 <= i < n]
    if bad:
        print("帧号越界：%s（该素材共 %d 帧，0..%d）" % (bad, n, n - 1), file=sys.stderr)
        return 2
    if not idxs:
        print("没有要看的帧号", file=sys.stderr)
        return 2

    w = a.cell_w
    h = int(w * 9 / 16)
    cols = min(a.cols, len(idxs))
    rows = (len(idxs) + cols - 1) // cols
    canvas = Image.new("RGB", (w * cols, (h + 18) * rows), (18, 18, 18))
    dr = ImageDraw.Draw(canvas)
    for k, i in enumerate(idxs):
        cell = _fit(Image.fromarray(frames[i]), w, h)
        x, y = (k % cols) * w, (k // cols) * (h + 18)
        canvas.paste(cell, (x, y))
        label = "#%d  %.2fs" % (i, times[i] / 1000)
        dr.rectangle([x, y + h, x + dr.textlength(label, font=_font(13)) + 6, y + h + 17],
                     fill=(0, 0, 0))
        dr.text((x + 3, y + h + 2), label, font=_font(13), fill=(255, 220, 90))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print("%s  (%dx%d)  %d 帧 / 共 %d 帧" % (out, canvas.width, canvas.height, len(idxs), n))
    for i in idxs:
        print("  #%d  %.3fs" % (i, times[i] / 1000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
