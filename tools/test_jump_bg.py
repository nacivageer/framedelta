#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""背景区判据的契约单测：合成帧，不需要素材，秒级。

用法
    python tools/test_jump_bg.py

判据：jump 前后各 500ms 的中位画面如果高度相关（bg_ncc ≥ 0.75），说明背景没换，
是同一画面上的快速运动 / 遮挡，不是换画，降级不报 jump。

要钉死的四件事：
1. 真换画（背景整块换掉）必须照报 jump，不能被背景判据误压；
2. 同一背景上的遮挡 / 掠过必须被压掉（39EC、clip74 那类误报）；
3. 窗口帧数不够时返回 None、不做判断（短素材、抽帧太稀不该被瞎判）；
4. 走 calib 的「analyse(classify=False) + classify_pairs」路径时判据同样生效。
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gif_digest as g  # noqa: E402

FAILED = []
DT = 100.0


def check(name, cond, detail=""):
    if not cond:
        FAILED.append("%s%s" % (name, ("：" + detail) if detail else ""))


def background(seed, h=120, w=160):
    rng = np.random.default_rng(seed)
    return rng.integers(40, 200, size=(h, w, 3), dtype=np.uint8)


def checker(h, w, cell=8):
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // cell + xx // cell) % 2) * 255).astype(np.uint8)
    return np.stack([c, c, c], axis=-1)


def cut_frames():
    """前 20 帧背景 A，后 20 帧背景 B：第 20 对是真正的换画。"""
    a, b = background(1), background(2)
    return [a.copy() for _ in range(20)] + [b.copy() for _ in range(20)]


def occlusion_frames():
    """同一背景，第 18–19 帧被一块棋盘格盖住大半：掠过镜头，不是换画。"""
    frames = [background(3).copy() for _ in range(40)]
    for i in (18, 19):
        frames[i][10:110, 10:150] = checker(100, 140)
    return frames


def run(frames, classify=True):
    durs = [DT] * len(frames)
    return g.analyse(frames, durs, list(range(len(frames))), classify=classify)


def test_background_ncc_basic():
    """窗口取中位：前景动、背景不动 → 两张中位画面几乎一样。"""
    bg = background(4)
    frames = [bg.copy() for _ in range(30)]
    for i, (y, x) in enumerate([(0, 0), (10, 20), (20, 40), (30, 60), (40, 80)]):
        frames[i + 10][y:y + 40, x:x + 40] = 255
    res = run(frames)
    v = g.background_ncc(res["grays"], res["times"], 12)
    check("前景移动被背景判据误伤：bg_ncc=%r" % v, v is not None and v > 0.95, "%r" % v)


def test_background_ncc_too_few_frames():
    """窗口帧数不足 → None，不拿一两帧瞎判。"""
    frames = [background(5).copy() for _ in range(3)]
    res = run(frames)
    v = g.background_ncc(res["grays"], res["times"], 2, th={"jump_bg_min_frames": 3})
    check("帧数不足却给了值：%r" % v, v is None, "%r" % v)


def test_cut_kept():
    """背景整块换掉：jump 必须留着，背景值要低。"""
    res = run(cut_frames())
    jumps = [p["frame"] for p in res["pairs"] if p["kind"] == "jump"]
    check("真切点被背景判据压掉了：jumps=%r" % jumps, 20 in jumps, "%r" % jumps)
    p = res["pairs"][19]
    check("真切点没算背景值：bg=%r" % p.get("bg_ncc"), p.get("bg_ncc") is not None,
          "%r" % p.get("bg_ncc"))
    check("真切点的背景相似度太高（中位窗口选错了？）：%r" % p.get("bg_ncc"),
          p.get("bg_ncc") is not None and p["bg_ncc"] < 0.75, "%r" % p.get("bg_ncc"))


def test_occlusion_suppressed():
    """同一背景上的遮挡：压成 motion/subtle，不再报 jump。"""
    res = run(occlusion_frames())
    jumps = [p["frame"] for p in res["pairs"] if p["kind"] == "jump"]
    check("遮挡仍被报成 jump：%r" % jumps, not jumps, "%r" % jumps)
    hit = [p for p in res["pairs"] if p.get("bg_suppressed")]
    check("没有任何对被背景判据压掉（判据没生效？）", bool(hit),
          "%r" % [(p["frame"], p.get("bg_ncc")) for p in res["pairs"] if 15 <= p["frame"] <= 23])
    for p in hit:
        check("被压掉的对没写背景值 #%d" % p["frame"], p.get("bg_ncc") is not None)


def test_calib_path():
    """calib 的路径（classify=False 后手动 classify_pairs）也必须压掉遮挡。"""
    res = run(occlusion_frames(), classify=False)
    g.classify_pairs(res["pairs"])
    jumps = [p["frame"] for p in res["pairs"] if p["kind"] == "jump"]
    check("classify_pairs 路径没压掉遮挡：%r" % jumps, not jumps, "%r" % jumps)


def test_threshold_override():
    """阈值可被扫参覆盖：压到 1.01 就什么都不压。"""
    res = run(occlusion_frames(), classify=False)
    g.classify_pairs(res["pairs"], {"jump_bg_ncc": 1.01})
    jumps = [p["frame"] for p in res["pairs"] if p["kind"] == "jump"]
    check("阈值覆盖失效：%r" % jumps, bool(jumps), "%r" % jumps)


def main():
    for fn in (test_background_ncc_basic, test_background_ncc_too_few_frames, test_cut_kept,
               test_occlusion_suppressed, test_calib_path, test_threshold_override):
        fn()
    if FAILED:
        print("FAIL %d 条：" % len(FAILED))
        for f in FAILED:
            print("  - " + f)
        return 1
    print("PASS 6 组检查（背景中位 / 帧数不足不判 / 真切点保留 / 遮挡压掉"
          " / classify_pairs 路径 / 阈值可覆盖）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
