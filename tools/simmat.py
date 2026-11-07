#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代表帧之间的相似度矩阵：哪些格子其实是同一张画面。

静止素材（屏幕录制、幻灯片、暗场）里，`fill` / `last` / `shot_peak` 很容易在同一张
不动的画面上连取两三格：预算花了，信息是零。判据用 256x144 网格上的 NCC，
**≥0.90 基本可以当同一张画面**（同一页幻灯片 0.95–0.98，同一场景换机位 0.3–0.5）。

用法
    python tools/simmat.py runs/eval/L1_trailer
    python tools/simmat.py runs/base/k8/L3_screen --pair 0.90
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import grid_ncc, grid_rgb, load_frames  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description="代表帧相似度矩阵（同一张画面 = 白花一格）")
    ap.add_argument("run", help="产物目录（含 digest.json）或直接给 digest.json")
    ap.add_argument("--pair", type=float, default=0.90, help="判同一张画面的 NCC 阈值")
    a = ap.parse_args()

    dpath = a.run if a.run.endswith(".json") else os.path.join(a.run, "digest.json")
    d = json.load(open(dpath, encoding="utf-8"))
    kf = d.get("key_frames") or []
    if not kf:
        print("没有 key_frames：%s" % dpath, file=sys.stderr)
        return 2
    src = d.get("source") or (d.get("meta") or {}).get("source")
    if not src or not os.path.exists(src):
        print("源文件不在，无法取帧：%r（从 digest.source 读）" % src, file=sys.stderr)
        return 2

    frames = load_frames(__import__("pathlib").Path(src), None)[0]
    grids = [grid_rgb(frames[x["frame"]]) for x in kf]
    n = len(kf)
    sim = np.eye(n)
    for i in range(n):
        for j in range(i + 1, n):
            sim[i, j] = sim[j, i] = grid_ncc(grids[i], grids[j])

    print("        " + " ".join("%6s" % ("#%d" % kf[j]["frame"]) for j in range(n)))
    for i in range(n):
        print("#%-5d %.2fs " % (kf[i]["frame"], kf[i]["t_ms"] / 1000) +
              " ".join("%6.2f" % sim[i, j] for j in range(n)))

    dup = []
    for i in range(n):
        for j in range(i):
            if sim[i, j] >= a.pair:
                dup.append("#%d 同 #%d（NCC %.2f）" % (kf[i]["frame"], kf[j]["frame"], sim[i, j]))
                break
    print("\n%d 格里 %d 格是已出现过的画面：%s" % (n, len(dup), "、".join(dup) or "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
