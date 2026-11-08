#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""重采样路径的契约单测：时间轴、帧号、预算。

为什么单独一个：`--fps` / `--max-frames` 曾把时间轴压成「选中帧数 × 单帧时长」
（15s 素材报成 4.9s），而 jump / drift / swap / interval 的 frame 又是分析网格下标、
key_frames 是源帧号——这两类问题在任何「不重采样」的回归里都不会出现。
这里用一段合成的 60 帧序列（第 30 帧整幅换图）把它们钉住。

用法
    python tools/test_resample.py          # 秒级，不依赖外部素材
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "gif_digest.py"

N, W, H, CUT, FRAME_MS = 60, 320, 180, 30, 100
# 目录输入没有帧时长，load_dir 默认按 10fps 记（见 gif_digest.load_dir）。
DURATION_MS = N * FRAME_MS          # 6000
EXPECT_INTERVAL = (DURATION_MS - FRAME_MS) / 19.0   # 重采样到 20 帧后的平均间隔


def make_frames(path: Path):
    """两段纹理，第 CUT 帧整幅换成另一段——一次整幅换图，没有运动。"""
    path.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    tex = [rng.integers(0, 256, (H, W, 3), dtype=np.uint8) for _ in range(2)]
    for i in range(N):
        Image.fromarray(tex[0 if i < CUT else 1]).save(path / f"{i:04d}.png")


def run(input_path: Path, out: Path, *extra):
    cmd = [sys.executable, str(TOOL), str(input_path), "--out", str(out), "--quiet", *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("gif_digest 失败 rc=%d：%s" % (p.returncode, (p.stderr or "")[-600:]))
    return (json.load(open(out / "digest.json", encoding="utf-8")),
            json.load(open(out / "meta.json", encoding="utf-8")))


def check(name, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name, ("  " + detail) if detail else ""))
    return bool(cond)


def main():
    fails = 0
    with tempfile.TemporaryDirectory(prefix="fd-resample-") as tmp:
        tmp = Path(tmp)
        src = tmp / "frames"
        make_frames(src)

        # ---- 1) --max-frames：时间轴按源帧起点，不是「选中帧数 × 单帧时长」
        d1, m1 = run(src, tmp / "mf20", "--max-frames", "20")
        meta1 = d1["meta"]
        fails += not check("frames / analysed", meta1["frames"] == N and m1["analysed_frames"] == 20,
                           "frames=%r analysed=%r" % (meta1["frames"], m1["analysed_frames"]))
        fails += not check("duration_ms 是源时长", meta1["duration_ms"] == DURATION_MS,
                           "duration=%r" % (meta1["duration_ms"],))
        interval = m1.get("pair_interval_ms_mean")
        fails += not check("pair_interval 按源时间轴", interval is not None and abs(interval - EXPECT_INTERVAL) <= 10,
                           "interval=%r（应≈%.1f）" % (interval, EXPECT_INTERVAL))
        fails += not check("counts.pairs = analysed-1", d1["counts"]["pairs"] == 19,
                           "pairs=%r" % (d1["counts"]["pairs"],))

        # ---- 2) 事件帧号必须是源帧号（不是分析网格下标）
        jumps = d1.get("jumps") or []
        fails += not check("重采样后仍检出切点", len(jumps) >= 1, "jumps=%r" % ([j["frame"] for j in jumps],))
        jf = jumps[0]["frame"] if jumps else -1
        fails += not check("jump.frame 是源帧号（≈%d，不是网格 %d）" % (CUT, CUT * 20 // N),
                           CUT - 2 <= jf <= CUT + 3, "frame=%r" % (jf,))
        jt = jumps[0]["t_ms"] if jumps else -1
        fails += not check("jump.t_ms 落在真实时刻", CUT * FRAME_MS - 50 <= jt <= CUT * FRAME_MS + 150,
                           "t=%r（应≈%d）" % (jt, CUT * FRAME_MS))

        # ---- 3) 所有对外帧号都在源帧空间里；时间不超过总时长
        bad_frames = []
        for j in d1.get("jumps") or []:
            bad_frames += [("jumps", j["frame"])]
        for s in d1.get("swaps") or []:
            bad_frames += [("swaps", s["frame"])]
        for dr in d1.get("drifts") or []:
            bad_frames += [("drifts.start", dr["start_frame"]), ("drifts.end", dr["end_frame"])]
        for f in d1.get("fades") or []:
            bad_frames += [("fades.start", f["start_frame"]), ("fades.end", f["end_frame"])]
        for iv in d1.get("intervals") or []:
            bad_frames += [("intervals.start", iv["start_frame"]), ("intervals.end", iv["end_frame"])]
        over = [x for x in bad_frames if not (0 <= x[1] < N)]
        fails += not check("所有事件帧号 < frames", not over, "越界=%r" % (over[:5],))

        key1 = d1.get("key_frames") or []
        over_t = [k for k in key1 if k["t_ms"] > meta1["duration_ms"] + 1e-6]
        fails += not check("代表帧时间 ≤ duration", not over_t, "越界=%r" % ([k["t_ms"] for k in over_t],))
        fails += not check("budget.images = 实出张数",
                           (d1.get("budget") or {}).get("images") == len(key1),
                           "images=%r cells=%d" % ((d1.get("budget") or {}).get("images"), len(key1)))

        # ---- 4) 重采样与全帧的切点时刻一致（±2 帧；采样点可能落在切点后一帧）
        d2, m2 = run(src, tmp / "full")
        j2 = (d2.get("jumps") or [{}])[0]
        drift_ms = abs(j2.get("t_ms", 0) - jt) if jumps else 999
        fails += not check("重采样 vs 全帧切点同刻（±2 帧）", drift_ms <= 2 * FRAME_MS,
                           "mf=%r full=%r Δ=%rms" % (jt, j2.get("t_ms"), drift_ms))
        fails += not check("全帧 jump.frame 也对", 0 <= j2.get("frame", -1) < N,
                           "frame=%r" % (j2.get("frame"),))

        # ---- 5) --fps 走同一条时间轴
        d3, m3 = run(src, tmp / "fps15", "--fps", "15")
        int3 = m3.get("pair_interval_ms_mean")
        fails += not check("--fps 的 pair_interval 也按源时间轴",
                           int3 is not None and 60 <= int3 <= 75,
                           "interval=%r（15fps 应≈66.7）" % (int3,))

    print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
