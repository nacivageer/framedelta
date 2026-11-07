#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按「时间均匀」抽 N 帧拼一张带时间标签的图——复刻 dsh-read-animation 的交付形态。

用途：做对照实验。「均匀 8 帧」对上「代表帧」，同一段素材、同一套问题，
看读图方答出来的东西差多少。

用法
    python tools/uniform.py <源> --n 8 --out runs/eval/_uniform/e6.png
    帧标签形如 `#frame016@1960ms`（帧号 + 该帧起始时间），与插件交付约定一致。
"""
import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import load_frames, _font, _fit  # noqa: E402


def frame_times(durs):
    times, t = [], 0.0
    for d in durs:
        times.append(t)
        t += d
    return times


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--n", type=int, default=8, help="抽几帧（插件默认 8）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--cell-w", type=int, default=320)
    a = ap.parse_args()

    frames, durs, _ = load_frames(Path(a.input), None)
    times = frame_times(durs)
    total = times[-1] if times else 0.0
    n = min(a.n, len(frames))

    # 插件口径：按时间均匀取点，点落到哪一帧就用哪一帧
    picks = []
    for k in range(n):
        t = total * k / n if total else 0.0
        idx = max(i for i, ft in enumerate(times) if ft <= t + 1e-9)
        picks.append((idx, times[idx]))

    h = int(a.cell_w * 9 / 16)
    rows = (len(picks) + a.cols - 1) // a.cols
    canvas = Image.new("RGB", (a.cell_w * a.cols, (h + 18) * rows), (18, 18, 18))
    dr = ImageDraw.Draw(canvas)
    for k, (idx, t_ms) in enumerate(picks):
        cell = _fit(Image.fromarray(frames[idx]), a.cell_w, h)
        x, y = (k % a.cols) * a.cell_w, (k // a.cols) * (h + 18)
        canvas.paste(cell, (x, y))
        label = "#frame%03d@%dms" % (idx, round(t_ms))
        dr.rectangle([x, y + h, x + dr.textlength(label, font=_font(13)) + 6, y + h + 17], fill=(0, 0, 0))
        dr.text((x + 3, y + h + 2), label, font=_font(13), fill=(255, 220, 90))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print("%s  %dx%d  %d 帧（总长 %.0fms）" % (out, canvas.width, canvas.height, len(picks), total))
    for k, (idx, t_ms) in enumerate(picks):
        print("  %d  frame %d  @%dms" % (k + 1, idx, round(t_ms)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
