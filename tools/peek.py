#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按时间点抽帧拼一条对比带：判断一个突变是不是「整幅换画」。

用法
    python tools/peek.py <源> 13000 15040 --before 60 --out runs/_probe/x.png

每个时间点抽两帧（t-before 与 t），左右成对、上下一组，标时间。
用途：回归报出来的 jump 是不是真的换画，一次图就能判。
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


def index_before(times, t_ms):
    """t 时刻对应的帧（最后一个 start<=t），取不到返回 None。"""
    hit = None
    for i, t in enumerate(times):
        if t <= t_ms + 1e-6:
            hit = i
        else:
            break
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("times", nargs="+", type=float, help="毫秒时间点")
    ap.add_argument("--before", type=float, default=60.0, help="对照帧提前多少毫秒")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cell-h", type=int, default=240)
    a = ap.parse_args()

    frames, durs, _ = load_frames(Path(a.input), None)
    times = frame_times(durs)
    pairs = []
    for t_ms in a.times:
        i2 = index_before(times, t_ms)
        i1 = index_before(times, t_ms - a.before)
        if i2 is None:
            print("时间点 %.0fms 超出范围（总长 %.0fms）" % (t_ms, times[-1] if times else 0), file=sys.stderr)
            return 2
        pairs.append((i1 if i1 is not None else 0, i2, t_ms))

    cells = []
    for i1, i2, t_ms in pairs:
        for idx, tag in ((i1, "-%.0fms" % a.before), (i2, "+0ms")):
            im = _fit(Image.fromarray(frames[idx]), a.cell_h * 16 // 9, a.cell_h)
            dr = ImageDraw.Draw(im)
            label = "%dms %s" % (round(t_ms), tag)
            dr.rectangle([0, 0, dr.textlength(label, font=_font(13)) + 6, 17], fill=(0, 0, 0))
            dr.text((3, 2), label, font=_font(13), fill=(255, 255, 0))
            cells.append(im)

    rows = len(pairs)
    w, h = cells[0].size
    canvas = Image.new("RGB", (w * 2, h * rows), (20, 20, 20))
    for k, im in enumerate(cells):
        canvas.paste(im, ((k % 2) * w, (k // 2) * h))

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print("%s  (%dx%d, %d 组)" % (out, canvas.width, canvas.height, rows))
    for i1, i2, t_ms in pairs:
        print("  %.0fms: frame %d → %d" % (t_ms, i1, i2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
