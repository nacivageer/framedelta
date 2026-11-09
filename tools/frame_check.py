#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""结构体检：产物文本（事件）与代表帧（画面）是否互相承认。

只读，不改任何产物。回答四个问题，对应 docs/tasks/select-frames.md 的 A / B 口径：

    1. events_reported：jumps + swaps + drifts + fades 去重后的条数
    2. frames_states：代表帧按 NCC >= 0.90 判定「相邻两格是不是同一画面」，
       frames_states = 1 + 相邻不同的对数（下限估计，不猜长空档里有几次）
    3. flag：frames_states - 1 > events_reported + 1（判据少报超过一格余量）
    4. 每个真值变化的覆盖：事件 ±0.5s 报出 / 前后 0.5s 内有帧对 / 所在状态区间有稳定帧

用法
    python tools/frame_check.py --truth eval/truth_big.json --arm big=runs/eval_big
    python tools/frame_check.py --truth eval/truth.json eval/truth_long.json --arm all=runs/eval --json runs/frame_check.json

口径里的两个阈值（NCC 0.90 / flag 余量 1）与 `tools/simmat.py` 同一来源；改它们要
先重跑本工具并把前后两版数字一起写进任务卡，不许只报新版。
"""
import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import grid_ncc, grid_rgb, load_frames  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SAME_PICTURE_NCC = 0.90   # 与 simmat.py 同源
FLAG_MARGIN = 1           # frames_states-1 > events + FLAG_MARGIN


def events_of(d):
    ev = []
    for j in d.get("jumps") or []:
        ev.append(j["t_ms"] / 1000.0)
    for s in d.get("swaps") or []:
        ev.append(s["t_ms"] / 1000.0)
    for r in d.get("drifts") or []:
        ev.append(((r.get("from_ms") or 0) + (r.get("to_ms") or 0)) / 2000.0)
    for f in d.get("fades") or []:
        ev.append((f.get("mid_ms") or 0) / 1000.0)
    return sorted(ev)


def frame_nccs(digest):
    """相邻代表帧的 NCC；源文件不在时返回 None（该段只报事件侧）。"""
    kf = digest.get("key_frames") or []
    src = digest.get("source") or (digest.get("meta") or {}).get("source")
    if not src or not os.path.exists(src):
        return None
    frames = load_frames(Path(src), None)[0]
    grids = [grid_rgb(frames[k["frame"]]) for k in kf]
    return [float(grid_ncc(grids[i], grids[i + 1])) for i in range(len(grids) - 1)]


def check_case(case, t, digest):
    kf = digest.get("key_frames") or []
    ts = [k["t_ms"] / 1000.0 for k in kf]
    ev = events_of(digest)
    nccs = frame_nccs(digest)
    if nccs is None:
        states = None
        flag = None
        diff_pairs = None
    else:
        diff_pairs = sum(1 for v in nccs if v < SAME_PICTURE_NCC)
        states = 1 + diff_pairs
        flag = (states - 1) > (len(ev) + FLAG_MARGIN)

    ch = [c["t_s"] for c in t.get("truth_changes") or []]
    dur = (digest.get("meta") or {}).get("duration_ms", 0) / 1000.0 or 15.0
    reported = [c for c in ch if any(abs(e - c) <= 0.5 for e in ev)]
    pair_ok = []
    for c in ch:
        before = any(c - 0.5 <= x <= c - 0.05 for x in ts)
        after = any(c + 0.05 <= x <= c + 0.5 for x in ts)
        pair_ok.append(before and after)
    bounds = [0.0] + ch + [dur]
    state_ok = 0
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        lo, hi = a + 0.25, b - 0.25
        if hi <= lo:
            lo, hi = a + 0.05, b - 0.05
        if any(lo <= x <= hi for x in ts):
            state_ok += 1
    return {
        "case": case,
        "truth": len(ch),
        "events": len(ev),
        "event_times": [round(e, 2) for e in ev],
        "frames": len(ts),
        "frame_times": [round(x, 2) for x in ts],
        "nccs": None if nccs is None else [round(v, 2) for v in nccs],
        "diff_pairs": diff_pairs,
        "frames_states": states,
        "flag": flag,
        "reported": len(reported),
        "pairs": sum(pair_ok),
        "states_covered": state_ok,
        "states_total": max(1, len(bounds) - 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", nargs="+", required=True)
    ap.add_argument("--arm", action="append", required=True, help="标签=目录，可多次")
    ap.add_argument("--json", help="把逐段结果写到这个文件")
    a = ap.parse_args()

    arms = [tuple(s.split("=", 1)) for s in a.arm]
    rows = []
    for tp in a.truth:
        truth = json.load(open(tp, encoding="utf-8"))["cases"]
        for case, t in truth.items():
            for label, root in arms:
                p = os.path.join(root, case, "digest.json")
                if not os.path.exists(p):
                    continue
                d = json.load(open(p, encoding="utf-8"))
                rows.append(check_case(case, t, d))

    print("%-12s %-5s %-6s %-6s %-5s %-6s %-6s %-6s %s" % (
        "case", "真值", "事件", "帧状态", "flag", "事件报", "帧对", "状态", "相邻NCC"))
    for r in rows:
        print("%-12s %-5d %-6d %-6s %-5s %-6d %-6d %-6s %s" % (
            r["case"], r["truth"], r["events"],
            "—" if r["frames_states"] is None else r["frames_states"],
            "—" if r["flag"] is None else ("YES" if r["flag"] else "no"),
            r["reported"], r["pairs"],
            "%d/%d" % (r["states_covered"], r["states_total"]),
            "—" if r["nccs"] is None else " ".join("%.2f" % v for v in r["nccs"])))

    tot = lambda k: sum(r[k] for r in rows)
    tch = tot("truth")
    print("\n合计：事件报出 %d/%d · 帧对 %d/%d · 状态覆盖 %d/%d · flag %d/%d 段" % (
        tot("reported"), tch, tot("pairs"), tch, tot("states_covered"), tot("states_total"),
        sum(1 for r in rows if r["flag"]), sum(1 for r in rows if r["flag"] is not None)))

    must = {"r4_skit", "a1_anime", "L3_screen", "e6_d"}
    got = {r["case"] for r in rows if r["flag"]}
    print("A1（四段必须 flag）：%s" % ("；".join(
        "%s %s" % (c, "✓" if c in got else "✗") for c in sorted(must))))
    quiet = [r["case"] for r in rows if r["case"] not in must and r["flag"]]
    print("A2（其余段误标 %d 段，允许 ≤2）：%s" % (len(quiet), "、".join(quiet) or "无"))

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("明细 -> %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
