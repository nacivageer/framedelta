#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""输入加固的契约单测：旋转元数据、帧率字符串、坏输入的错误出口。

为什么单独一个：mp4 的显示旋转在旧版里被完全忽略——带 90° 元数据的竖屏视频会按
编码宽高解出来（320×240 侧躺），分析照样跑、产物照样出，只是全是错的；remux 后的
`avg_frame_rate` 还会变成 `30/1,`（尾逗号）让帧率解析抛 ValueError。这里用四个
合成的 320×240 / 左上红块样本把「转到哪个角」钉死，方向不靠猜。

方向基准：2026-11-09 用 ffmpeg 6.0 的 autorotate 逐像素比对过——
rotation 90 → np.rot90(k=1)、180 → k=2、270 → k=-1，90/270 交换宽高。

用法
    python tools/test_input.py        # 秒级；mp4 用例需要 ffmpeg/ffprobe
    python tools/test_input.py --require-ffmpeg   # CI 用：缺 ffmpeg 直接失败，不静默跳过
"""
import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "gif_digest.py"
CASES = ROOT / "runs" / "_input_cases" / "_rot_cases"

sys.path.insert(0, str(ROOT))
import gif_digest as g  # noqa: E402

W, H, BLOCK, FRAMES = 320, 240, 40, 6
# 实测角（红块原本在编码帧左上）：与 ffmpeg autorotate 一致
EXPECT_CORNER = {0: "top-left", 90: "bottom-left", 180: "bottom-right", 270: "top-right"}
EXPECT_WH = {0: (W, H), 90: (H, W), 180: (W, H), 270: (H, W)}


def check(name, cond, detail=""):
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name, ("  " + detail) if detail else ""))
    return bool(cond)


def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("命令失败：%s\n%s" % (" ".join(map(str, cmd)), p.stderr[-800:]))
    return p


def make_src():
    """320×240 白底 + 左上 40×40 红块，编成 6 帧 10fps 的 mp4，再 remux 加旋转。"""
    if CASES.exists():
        shutil.rmtree(CASES)
    CASES.mkdir(parents=True)
    a = np.full((H, W, 3), 245, dtype=np.uint8)
    a[0:BLOCK, 0:BLOCK] = (220, 20, 20)
    Image.fromarray(a).save(CASES / "src.png")
    sh(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(CASES / "src.png"),
        "-t", str(FRAMES / 10), "-r", "10", "-pix_fmt", "yuv420p", "-c:v", "libx264",
        "-g", "1", str(CASES / "base.mp4")])
    for rot in EXPECT_CORNER:
        # -display_rotation 是输入选项；-c copy remux 才会把 display matrix 写进文件
        sh(["ffmpeg", "-y", "-v", "error", "-display_rotation", str(rot),
            "-i", str(CASES / "base.mp4"), "-c", "copy", str(CASES / f"rot{rot}.mp4")])


def red_corner(rgb: np.ndarray):
    """红块落在哪个角 + 面积；没有红块返回 (None, 0)。"""
    r, gg, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    m = (r > 150) & (gg < 90) & (b < 90)
    if not m.any():
        return None, 0
    ys, xs = np.nonzero(m)
    h, w = rgb.shape[:2]
    return ("%s-%s" % ("top" if ys.mean() < h / 2 else "bottom",
                       "left" if xs.mean() < w / 2 else "right")), int(m.sum())


def run_tool(inp: Path, out: Path):
    return subprocess.run([sys.executable, str(TOOL), str(inp), "--out", str(out), "--quiet"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_parse_rate(fails):
    cases = [
        ("30/1,", 30.0, "尾逗号"),
        ("30000/1001", 29.97, "NTSC 分数"),
        ("0/0", 25.0, "0/0 退回 25"),
        (" 25 ", 25.0, "strip 空白"),
        ("", 25.0, "空串"),
        ("abc", 25.0, "解析失败不崩"),
        (None, 25.0, "None"),
    ]
    for text, want, why in cases:
        got = g.parse_rate(text)
        fails += not check("parse_rate(%r) = %r（%s）" % (text, want, why), abs(got - want) < 0.01,
                           "got=%r" % (got,))
    fails += not check("parse_rate('0/0', 0.0) 尊重显式 fallback", g.parse_rate("0/0", 0.0) == 0.0,
                       "got=%r" % (g.parse_rate("0/0", 0.0),))

    fps, suspect = g._fps_of_stream({"avg_frame_rate": "30/1,", "r_frame_rate": "30/1"})
    fails += not check("avg 带尾逗号仍解析为 30、不算 VFR", fps == 30.0 and not suspect,
                       "fps=%r suspect=%r" % (fps, suspect))
    fps, suspect = g._fps_of_stream({"avg_frame_rate": "0/0", "r_frame_rate": "30000/1001"})
    fails += not check("avg 0/0 退回 r_frame_rate", abs(fps - 29.97) < 0.01 and not suspect,
                       "fps=%r suspect=%r" % (fps, suspect))
    fps, suspect = g._fps_of_stream({"avg_frame_rate": "25/1", "r_frame_rate": "30/1"})
    fails += not check("avg/r 差 >5% 标 vfr_suspect", fps == 25.0 and suspect,
                       "fps=%r suspect=%r" % (fps, suspect))
    fps, suspect = g._fps_of_stream({})
    fails += not check("两条帧率都没有 → 25", fps == 25.0 and not suspect,
                       "fps=%r suspect=%r" % (fps, suspect))

    rot = g._rotation_of({"tags": {"rotate": "90"}})
    fails += not check("旧版 tags.rotate 兜底", rot == 90, "rot=%r" % (rot,))
    rot = g._rotation_of({"side_data_list": [{"rotation": -90}], "tags": {"rotate": "90"}})
    fails += not check("side_data 优先于 tags（-90 → 270）", rot == 270, "rot=%r" % (rot,))
    rot = g._rotation_of({})
    fails += not check("没有旋转信息 → 0", rot == 0, "rot=%r" % (rot,))


def test_corrupt_input(fails):
    """坏 GIF / 不是视频的 mp4：SystemExit 给中文提示，不让 traceback 当主输出。"""
    bad_gif = CASES / "bad.gif"
    bad_gif.write_bytes(b"GIF89a" + b"\x00" * 64)
    p = run_tool(bad_gif, CASES / "out_bad_gif")
    fails += not check("坏 GIF：非零退出", p.returncode != 0, "rc=%r" % (p.returncode,))
    fails += not check("坏 GIF：中文提示且无 traceback",
                       "读不了这个图片" in p.stderr and "Traceback" not in p.stderr,
                       "stderr=%r" % (p.stderr[-200:],))

    bad_mp4 = CASES / "bad.mp4"
    bad_mp4.write_bytes(b"\x00" * 200)
    p = run_tool(bad_mp4, CASES / "out_bad_mp4")
    fails += not check("坏 mp4：非零退出", p.returncode != 0, "rc=%r" % (p.returncode,))
    fails += not check("坏 mp4：中文提示且无 traceback",
                       "ffprobe" in p.stderr and "Traceback" not in p.stderr,
                       "stderr=%r" % (p.stderr[-200:],))


def test_rotation(fails):
    n0 = None
    for rot, corner in EXPECT_CORNER.items():
        src = CASES / f"rot{rot}.mp4"
        info = g.probe_video(src)
        fails += not check("probe rot%d：rotation=%d" % (rot, rot), info["rotation"] == rot,
                           "got=%r" % (info["rotation"],))

        frames, durs, _ = g.load_frames(src, None)
        hh, ww = frames[0].shape[:2]
        got_corner, area = red_corner(frames[0])
        fails += not check("rot%d：解码尺寸 %dx%d（display）" % (rot, *EXPECT_WH[rot]),
                           (ww, hh) == EXPECT_WH[rot], "got=%dx%d" % (ww, hh))
        fails += not check("rot%d：红块在 %s" % (rot, corner), got_corner == corner,
                           "got=%r" % (got_corner,))
        fails += not check("rot%d：红块未被裁切 / 缩放" % rot, area == BLOCK * BLOCK,
                           "area=%r（应 %d）" % (area, BLOCK * BLOCK))
        if n0 is None:
            n0 = len(frames)
        fails += not check("rot%d：帧数不变（%d）" % (rot, FRAMES), len(frames) == FRAMES,
                           "got=%d，首例=%r" % (len(frames), n0))
        fails += not check("rot%d：每帧都转到同一朝向" % rot,
                           all(red_corner(f)[0] == corner for f in frames),
                           "首/末帧=%r/%r" % (red_corner(frames[0])[0], red_corner(frames[-1])[0]))

        # CLI 产物：meta.json 与 digest.meta 都带 rotation；宽高是旋转后的显示尺寸
        out = CASES / f"out{rot}"
        p = run_tool(src, out)
        fails += not check("rot%d：CLI 退出码 0" % rot, p.returncode == 0,
                           "rc=%r stderr=%r" % (p.returncode, p.stderr[-300:]))
        if p.returncode == 0:
            meta = json.load(open(out / "meta.json", encoding="utf-8"))
            digest = json.load(open(out / "digest.json", encoding="utf-8"))
            fails += not check("rot%d：meta.json 尺寸" % rot,
                               (meta["width"], meta["height"]) == EXPECT_WH[rot],
                               "got=%dx%d" % (meta["width"], meta["height"]))
            fails += not check("rot%d：meta.json.rotation" % rot, meta.get("rotation") == rot,
                               "got=%r" % (meta.get("rotation"),))
            fails += not check("rot%d：digest.meta.rotation" % rot,
                               (digest.get("meta") or {}).get("rotation") == rot,
                               "got=%r" % ((digest.get("meta") or {}).get("rotation"),))
            fails += not check("rot%d：vfr_suspect 为 false（CFR 样本）" % rot,
                               meta.get("vfr_suspect") is False,
                               "got=%r" % (meta.get("vfr_suspect"),))


def main(argv=None):
    ap = argparse.ArgumentParser(description="输入加固的契约单测")
    ap.add_argument("--require-ffmpeg", action="store_true",
                    help="没有 ffmpeg / ffprobe 时直接失败，而不是跳过 mp4 用例（CI 用）")
    args = ap.parse_args(argv)

    fails = 0
    CASES.mkdir(parents=True, exist_ok=True)
    test_parse_rate(fails)

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        # 2026-11-09 追出来的坑：ubuntu-latest 镜像没有 ffmpeg，本脚本原来打印 SKIP 后
        # 仍然 exit 0 + "ALL PASS"，CI 的 Linux 腿等于空跑（job 结论还是 success）。
        if args.require_ffmpeg:
            print("FAIL  没有 ffmpeg / ffprobe，mp4 用例无法运行（--require-ffmpeg）")
            return 1
        print("SKIP  mp4 用例（本机没有 ffmpeg / ffprobe）")
    else:
        make_src()
        test_rotation(fails)
        test_corrupt_input(fails)

    print("\n%s" % ("ALL PASS" if not fails else "%d FAIL" % fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
