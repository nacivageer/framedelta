#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 runs/eval/_clips 下所有片段重跑一遍产物（评测集一条命令）。

用法
    python tools/eval_run.py            # 全跑
    python tools/eval_run.py --only e8_fufu e9_aimanhua

不负责打分：分数要用 tools/score.py，且读图方必须换 agent（见 eval/README.md）。
"""
import argparse
import glob
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = os.path.join(ROOT, "runs", "eval", "_clips")
OUT = os.path.join(ROOT, "runs", "eval")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", default=None, help="只跑这些 case 名")
    a = ap.parse_args()

    clips = sorted(glob.glob(os.path.join(CLIPS, "*.mp4")))
    if a.only:
        want = set(a.only)
        clips = [c for c in clips if os.path.splitext(os.path.basename(c))[0] in want]
    if not clips:
        print("没有片段：%s" % CLIPS, file=sys.stderr)
        return 2

    for c in clips:
        name = os.path.splitext(os.path.basename(c))[0]
        out = os.path.join(OUT, name)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "gif_digest.py"), c,
                            "--out", out, "--quiet"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("%-12s FAILED: %s" % (name, (r.stderr or "")[-200:]))
            continue
        print("%-12s ok" % name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
