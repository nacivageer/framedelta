#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""渐变事件（fade_events）的契约单测：合成帧，不需要素材，秒级。

用法
    python tools/test_fades.py

为什么单测：这条判据全部建立在「段的两侧都有静段」这类时间结构上，用 3 段真素材只能
看到它「碰巧对不对」；边界（最短静段、段长上下限、jump 抑制、闪白不算、>5s 开关）必须
在合成数据上钉死，否则下次调阈值会悄悄改掉语义。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import fade_events  # noqa: E402

FAILED = []
DT = 100.0


def check_true(name, cond, detail=""):
    if not cond:
        FAILED.append("%s%s" % (name, ("：" + detail) if detail else ""))


def ramp(v0, v1, steps):
    return [v0 + (v1 - v0) * i / max(1, steps - 1) for i in range(steps)]


def build(kinds, values):
    n = len(values)
    if len(kinds) != n - 1:
        raise SystemExit("合成数据不一致：%d 帧需要 %d 对，给了 %d" % (n, n - 1, len(kinds)))
    frames = np.zeros((n, 8, 8, 3), dtype=np.uint8)
    for i, v in enumerate(values):
        frames[i] = int(round(v))
    pairs = [{"frame": i + 1, "kind": kinds[i],
              "changed_frac": abs(values[i + 1] - values[i]) / 255.0,
              "motion": {"dx": 0.0, "dy": 0.0, "scale": 1.0}} for i in range(n - 1)]
    times = np.array([i * DT for i in range(n)], dtype=np.float64)
    return pairs, frames, list(range(n)), times, n * DT


def case_gradient(still_before=60, still_after=40):
    """长静段 + 5 对渐变 + 长静段（总长 >5s，判据才启用）。"""
    values = [10] * still_before + ramp(10, 200, 6) + [200] * still_after
    kinds = ["still"] * still_before + ["subtle"] * 5 + ["still"] * still_after
    return build(kinds, values)


def test_gradual_found():
    """静 → 渐变 → 静：应报一次 gradual，时间落在渐变段中点。"""
    pairs, frames, idx, times, total = case_gradient()
    ev = fade_events(pairs, frames, idx, times, total)
    check_true("渐变没被检出：%r" % ev, len(ev) == 1, "%r" % ev)
    if len(ev) == 1:
        check_true("mode 不是 gradual：%r" % ev[0]["mode"], ev[0]["mode"] == "gradual")
        mid = (60 * DT + 65 * DT) / 2.0
        check_true("时间偏离渐变段中点：%r（期望 ≈%.0f）" % (ev[0]["mid_ms"], mid),
                   abs(ev[0]["mid_ms"] - mid) <= 300, "%r" % ev[0]["mid_ms"])


def test_quiet_too_short():
    """一侧静段不足 250ms：不报（「运动里的溶解」默认就是这一类）。"""
    values = [10] * 2 + ramp(10, 200, 6) + [200] * 98
    kinds = ["still"] * 2 + ["subtle"] * 5 + ["still"] * 98
    pairs, frames, idx, times, total = build(kinds, values)
    ev = fade_events(pairs, frames, idx, times, total)
    check_true("静段不足却报了：%r" % ev, not ev, "%r" % ev)


def test_jump_inside_suppressed():
    """段里有硬切：不报（jumps/transitions 已经说过一次）。"""
    values = [10] * 60 + ramp(10, 200, 6) + [200] * 40
    kinds = ["still"] * 60 + ["subtle", "jump", "subtle", "subtle", "subtle"] + ["still"] * 40
    pairs, frames, idx, times, total = build(kinds, values)
    ev = fade_events(pairs, frames, idx, times, total)
    check_true("段内有 jump 却报了：%r" % ev, not ev, "%r" % ev)


def test_too_long_segment():
    """段长超过 1200ms：不报（那是持续运动，不是一次过渡）。"""
    values = [10] * 40 + ramp(10, 200, 30) + [200] * 30
    kinds = ["still"] * 40 + ["subtle"] * 29 + ["still"] * 30
    pairs, frames, idx, times, total = build(kinds, values)
    ev = fade_events(pairs, frames, idx, times, total)
    check_true("超长段却报了：%r" % ev, not ev, "%r" % ev)


def test_blank_tail():
    """片尾塌陷到白：应报一次 blank。"""
    values = [60] * 98 + ramp(60, 255, 4) + [255] * 50
    kinds = ["still"] * 98 + ["motion"] * 3 + ["still"] * 50
    pairs, frames, idx, times, total = build(kinds, values)
    ev = fade_events(pairs, frames, idx, times, total)
    check_true("白场塌陷没报：%r" % ev, any(e["mode"] == "blank" for e in ev), "%r" % ev)


def test_flash_not_blank():
    """闪白（亮两帧就回来）不算状态切换。"""
    values = [60] * 100 + [255] * 2 + [60] * 30
    kinds = ["still"] * 100 + ["jump", "jump"] + ["still"] * 29
    pairs, frames, idx, times, total = build(kinds, values)
    ev = [e for e in fade_events(pairs, frames, idx, times, total) if e["mode"] == "blank"]
    check_true("闪白被当成塌陷：%r" % ev, not ev, "%r" % ev)


def test_short_input_off():
    """≤5s 的输入一律不启用（短素材的 9/9 基线靠这条不变）。"""
    pairs, frames, idx, times, _ = case_gradient()
    ev = fade_events(pairs, frames, idx, times, 3000.0)
    check_true("≤5s 还报事件：%r" % ev, not ev, "%r" % ev)


def test_skip_spans():
    """和 drift 重叠的不重复报。"""
    pairs, frames, idx, times, total = case_gradient()
    ev = fade_events(pairs, frames, idx, times, total, skip_spans=[(58, 66)])
    check_true("与 drift 重叠却重复报：%r" % ev, not ev, "%r" % ev)


def main():
    for fn in (test_gradual_found, test_quiet_too_short, test_jump_inside_suppressed,
               test_too_long_segment, test_blank_tail, test_flash_not_blank,
               test_short_input_off, test_skip_spans):
        fn()
    if FAILED:
        print("FAIL %d 条：" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        return 1
    print("PASS 8 组检查（渐变检出 / 短静段不报 / jump 抑制 / 超长段不报 / 白场塌陷"
          " / 闪白不算 / ≤5s 关闭 / 与 drift 去重）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
