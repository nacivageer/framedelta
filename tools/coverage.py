#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""结构指标：代表帧对真值变化点的覆盖，以及「切点前后对照对」占了几个格子。

不叫读图方、不看打分——只回答两个几何问题：
    1. 每个真值变化点，最近的代表帧差多远（±1.0s / ±0.5s 覆盖率）
    2. 有多少张代表帧是「和相邻那张只差 ≤3 帧」的对照对（一对 = 2 格）

用法
    python tools/coverage.py --arm 4=runs/base/k4 --arm 6=runs/base/k6
    python tools/coverage.py --truth eval/truth.json --arm all=runs/eval

arm 的目录下面按 case 名分子目录（runs/base/k6/L1_trailer/）。
"""
import argparse
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_case(root, case):
    p = os.path.join(root, case, "digest.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def pair_cells(kf, gap_ms=200.0):
    """返回参与「相邻对照对」的格子下标集合（每对两个）。

    判据用时间而不是帧号：不同素材的 fps 不一样（30fps 的 5 帧 = 165ms，
    10fps 的 1 帧就 100ms），按 200ms 算才是「几乎同一时刻」的意思。
    """
    out = set()
    for i in range(len(kf) - 1):
        if abs(kf[i + 1]["t_ms"] - kf[i]["t_ms"]) <= gap_ms:
            out.update((i, i + 1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default="eval/truth_long.json")
    ap.add_argument("--arm", action="append", required=True, help="标签=目录，可多次")
    a = ap.parse_args()

    truth = json.load(open(a.truth, encoding="utf-8"))["cases"]
    arms = []
    for spec in a.arm:
        label, root = spec.split("=", 1)
        arms.append((label, root))

    print("%-12s %-4s %-4s %-5s %-7s %-7s %-6s %s" % (
        "case", "臂", "格数", "对照对", "±1.0s", "±0.5s", "最大距", "各点距离（* = 该点只被对照对覆盖）"))
    for case, t in truth.items():
        for label, root in arms:
            d = load_case(root, case)
            if d is None:
                print("%-12s %-4s %s" % (case, label, "缺产物"))
                continue
            kf = d.get("key_frames") or []
            ts = [x["t_ms"] / 1000.0 for x in kf]
            pr = pair_cells(kf)
            gaps, marks = [], []
            for ch in t.get("truth_changes") or []:
                ds = [abs(x - ch["t_s"]) for x in ts]
                if not ds:
                    gaps.append(99.0)
                    marks.append("99")
                    continue
                m = min(ds)
                gaps.append(m)
                best = ds.index(m)
                marks.append(("%.2f" % m) + ("*" if best in pr else ""))
            n1 = sum(1 for g in gaps if g <= 1.0)
            n05 = sum(1 for g in gaps if g <= 0.5)
            npair = len(pr) // 2
            print("%-12s %-4s %-4d %-5d %-7s %-7s %-6.2f %s" % (
                case, label, len(kf), npair, "%d/%d" % (n1, len(gaps)),
                "%d/%d" % (n05, len(gaps)), max(gaps), " ".join(marks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
