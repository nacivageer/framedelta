#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""代表帧预算的单元测试：不依赖任何素材，几毫秒跑完。

用法
    python tools/test_budget.py

为什么单独测：预算的边界（8000ms、24000ms、封顶、--key 写死）是读图方和打分脚本
共同依赖的契约；只靠 tools/regress.py 端到端测，改公式时要等 45 秒才知道坏没坏，
而且端到端只覆盖手边这几段素材的时长。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gif_digest import KEY_BUDGET, key_budget, pick_key_frames  # noqa: E402

FAILED = []


def check(name, got, want):
    if got != want:
        FAILED.append("%s：期望 %r，实得 %r" % (name, want, got))


def check_true(name, cond, detail=""):
    if not cond:
        FAILED.append("%s%s" % (name, ("：" + detail) if detail else ""))


def test_boundaries():
    """≤8s 4 张；超过 8s 后每 4 秒 +1（向上取整）；上限 8。"""
    cases = [
        (0, 4), (1, 4), (480, 4), (3000, 4), (8000, 4),
        (8001, 5),            # 刚过 8 秒就给第 5 张
        (11999, 5), (12000, 5),  # 正好 4 秒整，不额外 +1
        (12001, 6),
        (15000, 6),           # 15 秒是这轮实测的长度
        (16000, 6), (16001, 7),
        (20000, 7), (24000, 8),
        (24001, 8), (60000, 8), (3600000, 8),   # 封顶
    ]
    for ms, want in cases:
        b = key_budget(ms)
        check("时长 %dms 的 k" % ms, b["k"], want)
        check("时长 %dms 的 mode" % ms, b["mode"], "auto")
        check("时长 %dms 的 images 未填" % ms, b.get("images"), None)


def test_monotonic():
    """时长增加，预算只能变大或不变。"""
    prev = 0
    for ms in range(0, 60000, 137):
        k = key_budget(ms)["k"]
        check_true("时长 %dms 的 k 不是单调不减" % ms, k >= prev, "%d < %d" % (k, prev))
        check_true("时长 %dms 的 k 超上限" % ms, k <= KEY_BUDGET["cap"], "k=%d" % k)
        prev = k


def test_derivation_text():
    """产物里那句话要能自己解释 k 怎么来的（读图方与人工排查都靠它）。"""
    short = key_budget(3000)
    check_true("短片段 derivation 没提到 ≤", "≤" in short["derivation"], short["derivation"])
    mid = key_budget(15000)
    check_true("15000ms derivation 缺 4 + ceil(7000/4000)",
               "4 + ceil(7000/4000) = 6" in mid["derivation"], mid["derivation"])
    cap = key_budget(60000)
    check_true("封顶时 derivation 没标注", "封顶" in cap["derivation"], cap["derivation"])


def test_fixed():
    """--key 写死时不走自适应，且要标明。"""
    b = key_budget(15000, 4)
    check("固定 k", b["k"], 4)
    check("固定 mode", b["mode"], "fixed")
    check_true("固定模式没说清来源", "--key" in b["rule"], b["rule"])
    check("固定 2 张", key_budget(300, 2)["k"], 2)
    # 0 / None 都当「没指定」，走自适应
    check("key=0 走自适应", key_budget(15000, 0)["mode"], "auto")
    check("key=None 走自适应", key_budget(15000, None)["k"], 6)


def test_pick_respects_k():
    """选帧不能超过预算（合成数据，不需要素材）。"""
    pairs = [{"frame": i, "kind": "motion" if i % 3 else "still", "energy": float(i % 7),
              "t_ms": float(i * 100), "t_start_ms": float(i * 100 - 100)} for i in range(1, 41)]
    for k in (1, 2, 4, 6, 8):
        picked = pick_key_frames(pairs, len(pairs) + 1, k)
        check_true("k=%d 时选出了 %d 帧" % (k, len(picked)),
                   len(picked) <= k, "超出预算")
        check_true("k=%d 时一帧都没选" % k, len(picked) >= 1)


def test_short_clip_unchanged():
    """短片段必须仍是 4 张——这是「≤8s 产物一字不变」的承诺。"""
    for ms in (480, 2460, 2970, 8000):
        check("短片段 %dms 的 k" % ms, key_budget(ms)["k"], 4)


def main():
    for fn in (test_boundaries, test_monotonic, test_derivation_text, test_fixed,
               test_pick_respects_k, test_short_clip_unchanged):
        fn()
    if FAILED:
        print("FAIL %d 条：" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        return 1
    print("PASS 6 组检查（边界 / 单调 / 推法文本 / --key 写死 / 选帧不超预算 / 短片段不变）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
