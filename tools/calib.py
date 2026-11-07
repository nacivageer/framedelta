#!/usr/bin/env python3
"""framedelta 切点标定：用「自己拼出来的切点」当真值，扫阈值。

为什么这样拼：GIF 基本不会有硬切（切了破坏循环），现有四段 GIF 里没有可用的
真值。于是从视频里取互不相关的片段拼接，切点位置完全已知；再补上难例——
同场景远距离拼接（难正样本）、连续快动作（难负样本）、闪白、交叉溶解。

用法：
    python tools/calib.py               # 取帧 → 拼序列 → 测量 → 扫参 → 报告
    python tools/calib.py --keep        # 保留临时帧（默认跑完删掉）

依赖 ffmpeg / ffprobe。报告写到 runs/calib/report.json。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gif_digest as g  # noqa: E402

FPS = 10
WIDTH = 400

# 素材池：取之前都用 12 帧缩略图肉眼核过，确保窗口内没有剪辑点
# （BV1QoxX6NEGk 那段是桌面→logo→启动器的剪辑，窗口里夹着真切，已弃用）
POOLS = [
    ("bili2", r"C:\Users\Administrator\Documents\deepseek-harness\bilibili\bili\tmp\BV18wxd6zERw_360p.mp4", 30, 14),
    ("tutorial", r"E:\下载\EN3.5.6\1-安装文件\直装版安装教程.mp4", 44, 10),
    ("draw_a", r"D:\Desktop\参考\画师分类\望月けい\C2356望月けい\绘画视频\绘画过程望月けい---绘画过程1.mp4", 600, 8),
    ("draw_b", r"D:\Desktop\参考\画师分类\望月けい\C2356望月けい\绘画视频\绘画过程望月けい---绘画过程1.mp4", 3000, 8),
]


def probe_size(path: str):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def grab(path: str, ss: float, seconds: float, fps: float = FPS, width: int = WIDTH):
    """用 ffmpeg 管道取一段固定 fps、固定宽度的 RGB 帧。"""
    w0, h0 = probe_size(path)
    h = int(round(h0 * width / w0 / 2.0)) * 2
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{ss}", "-t", f"{seconds}", "-i", path,
           "-vf", f"fps={fps},scale={width}:{h}", "-f", "rawvideo",
           "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    fb = width * h * 3
    n = len(raw) // fb
    if n == 0:
        raise SystemExit(f"取帧失败：{path} @ {ss}s")
    return np.frombuffer(raw, np.uint8, count=n * fb).reshape(n, h, width, 3).copy()


def build_pools(tmp: Path, fps: float = FPS):
    pools = {}
    size = (WIDTH, int(round(WIDTH * 9 / 16 / 2)) * 2)   # 统一到 400×224，方便拼接
    for name, path, ss, sec in POOLS:
        if not Path(path).exists():
            raise SystemExit(f"素材不存在：{path}")
        frames = grab(path, ss, sec, fps=fps)
        from PIL import Image
        frames = np.stack([np.asarray(Image.fromarray(f).resize(size, Image.BILINEAR))
                           for f in frames])
        pools[name] = frames
        print(f"pool {name:9s} {frames.shape[0]:3d} frames {frames.shape[2]}x{frames.shape[1]}", flush=True)
    return pools


def seg(pool, start, n):
    return [pool[i % len(pool)].copy() for i in range(start, start + n)]


def compose(pools, rng):
    """返回 [(name, category, frames, gt_cut_frames)]。"""
    seqs = []

    # 1) 不同来源硬切：2 个切点
    names = ["bili2", "tutorial", "draw_a"]
    for i in range(6):
        order = list(rng.permutation(names))
        frames, cuts = [], []
        for nm in order:
            n = int(rng.integers(10, 14))
            st = int(rng.integers(0, max(1, len(pools[nm]) - n)))
            cuts.append(len(frames))
            frames += seg(pools[nm], st, n)
        seqs.append((f"cut_{i}", "hard_cut", frames, cuts[1:]))

    # 2) 同场景远距离拼接（同一人、同一机位）——难正样本
    for i in range(3):
        a = seg(pools["draw_a"], int(rng.integers(0, 40)), 12)
        b = seg(pools["draw_b"], int(rng.integers(0, 40)), 12)
        seqs.append((f"same_{i}", "same_scene_cut", a + b, [12]))

    # 3) 连续快动作（无切点）——难负样本
    for i, nm in enumerate(["bili2", "tutorial", "draw_a", "draw_b"]):
        n = 30
        st = int(rng.integers(0, max(1, len(pools[nm]) - n)))
        seqs.append((f"cont_{i}", "continuous", seg(pools[nm], st, n), []))

    # 4) 交叉溶解：不算硬切
    for i in range(2):
        a = seg(pools["draw_b"], int(rng.integers(0, 60)), 12)
        b = seg(pools["tutorial"], int(rng.integers(0, 60)), 12)
        blend = []
        for k in range(4):
            al = (k + 1) / 5.0
            blend.append((a[-1].astype(np.float32) * (1 - al)
                          + b[0].astype(np.float32) * al).astype(np.uint8))
        seqs.append((f"dissolve_{i}", "dissolve", a[:-1] + blend + b[1:], []))

    # 5) 闪白：1–2 帧
    for i, nwhite in enumerate([1, 2]):
        base = seg(pools["bili2"], int(rng.integers(0, 60)), 30)
        flash = [np.full_like(base[0], 255) for _ in range(nwhite)]
        seqs.append((f"flash_{i}", "flash", base[:15] + flash + base[15:], []))
    return seqs


def predict(pairs):
    return [p["frame"] for p in pairs if p["kind"] == "jump"]


def score(pred, gt, tol=1):
    """±tol 帧容差下的 TP/FP/FN。"""
    used = [False] * len(gt)
    tp = 0
    fp = 0
    for pf in pred:
        hit = False
        for i, gf in enumerate(gt):
            if not used[i] and abs(pf - gf) <= tol:
                used[i] = True
                hit = True
                tp += 1
                break
        if not hit:
            fp += 1
    fn = sum(1 for u in used if not u)
    return tp, fp, fn


def evaluate(metrics, params):
    """metrics: [(name, category, pairs, gt)]；返回整体与分类指标。"""
    th = dict(g.TH)
    th.update(params)
    per_cat = {}
    totals = [0, 0, 0]
    for name, cat, pairs, gt in metrics:
        g.classify_pairs(pairs, th)
        pred = predict(pairs)
        if cat in ("hard_cut", "same_scene_cut", "continuous"):
            tp, fp, fn = score(pred, gt)
            c = per_cat.setdefault(cat, [0, 0, 0])
            c[0] += tp
            c[1] += fp
            c[2] += fn
            totals[0] += tp
            totals[1] += fp
            totals[2] += fn
        else:
            c = per_cat.setdefault(cat, [0, 0])
            c[0] += 1 if pred else 0
            c[1] += 1
    tp, fp, fn = totals
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn, "per_category": per_cat}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--fps", type=float, default=FPS)
    a = ap.parse_args(argv)

    pools = build_pools(None, a.fps)

    rng = np.random.default_rng(20261107)
    seqs = compose(pools, rng)
    print(f"sequences {len(seqs)}", flush=True)

    metrics = []
    thumbs = {}
    for name, cat, frames, gt in seqs:
        res = g.analyse(frames, [100] * len(frames), list(range(len(frames))), classify=False)
        metrics.append((name, cat, res["pairs"], gt))
        from PIL import Image
        thumbs[name] = [np.asarray(Image.fromarray(f).resize((160, 90), Image.BILINEAR))
                        for f in frames]
        print(f"  measured {name:11s} {cat:15s} pairs={len(res['pairs']):3d} gt={gt}", flush=True)

    default = {k: g.TH[k] for k in ("jump_hsv_score", "jump_ncc", "jump_ncc_hard",
                                    "spike_factor", "spike_offset")}
    base = evaluate(metrics, default)
    print("\n== defaults", default)
    print(json.dumps(base, ensure_ascii=False))

    grid = []
    for hsv in (10, 12, 14, 16, 18, 20, 22, 25, 28):
        for ncc in (0.45, 0.55, 0.65):
            for nhard in (0.25, 0.35):
                for factor in (1.0, 1.25, 1.5, 1.75, 2.0):
                    for offset in (0.0, 3.0, 6.0):
                        p = {"jump_hsv_score": float(hsv), "jump_ncc": ncc,
                             "jump_ncc_hard": nhard, "spike_factor": factor,
                             "spike_offset": offset}
                        r = evaluate(metrics, p)
                        grid.append((r["f1"], r["precision"], r["recall"], p))
    grid.sort(key=lambda z: (-z[0], -z[1]))

    print("\n== top5")
    for f1, prec, rec, p in grid[:5]:
        print(f"F1 {f1:.3f}  P {prec:.3f}  R {rec:.3f}  {p}")

    best = grid[0][3]
    best_report = evaluate(metrics, best)

    # 逐条看默认阈值下错在哪
    errs = []
    strip_dir = ROOT / "runs" / "calib" / "strips"
    strip_dir.mkdir(parents=True, exist_ok=True)
    for name, cat, pairs, gt in metrics:
        g.classify_pairs(pairs, default)
        pred = predict(pairs)
        tp, fp, fn = score(pred, gt)
        if tp + fp + fn or pred:
            errs.append({"seq": name, "category": cat, "gt": gt, "pred": pred,
                         "tp": tp, "fp": fp, "fn": fn})
            print(f"  {name:11s} {cat:15s} gt={gt} pred={pred} tp{tp} fp{fp} fn{fn}")
            # 把每个对不上的事件附近几帧存成一条，人工核验是真切点还是标签错
            bad = [f for f in pred if min((abs(f - x) for x in gt), default=99) > 1]
            bad += [f for f in gt if min((abs(f - x) for x in pred), default=99) > 1]
            for ev in bad:
                lo, hi = max(0, ev - 4), min(len(thumbs[name]) - 1, ev + 4)
                strip = thumbs[name][lo:hi + 1]
                from PIL import Image, ImageDraw
                canvas = Image.new("RGB", (len(strip) * 164 + 4, 118), (18, 18, 18))
                dr = ImageDraw.Draw(canvas)
                for i, fr in enumerate(strip):
                    canvas.paste(Image.fromarray(fr).resize((160, 90)), (4 + i * 164, 22))
                    dr.text((6 + i * 164, 4), str(lo + i), fill=(230, 230, 230))
                canvas.save(strip_dir / f"{name}_fps{int(a.fps)}_ev{ev}.png")

    out = {"params_default": default, "default": base,
           "params_best": best, "best": best_report,
           "grid_top": [{"f1": f1, "precision": p, "recall": r, "params": pp}
                        for f1, p, r, pp in grid[:10]],
           "errors_default": errs,
           "sequences": [{"seq": n, "category": c, "gt": gt,
                          "pairs": len(pr)} for n, c, pr, gt in metrics],
           "fps": a.fps, "width": WIDTH}
    outdir = ROOT / "runs" / "calib"
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "report.json").write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"\nreport -> {outdir / 'report.json'}")


if __name__ == "__main__":
    sys.exit(main())
