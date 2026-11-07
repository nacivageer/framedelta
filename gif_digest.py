#!/usr/bin/env python3
"""framedelta v1 —— 把一段连续帧压成「可读的变化描述 + 几张能读懂的图」。

只吃路径、只吐文件。语义不在本工具的产出里（见 docs/决策记录.md 的三层分工）。

产物（--out 目录）：
    meta.json     帧数、时长、循环、重复帧、fps、分辨率
    sheet.png     总览拼图
    flow.csv      逐帧位移 / 缩放估计
    kymograph.png 时间切片
    annotated.png 3–4 代表帧 + 运动箭头
    digest.json   区间化摘要、切点、选帧理由

用法：
    python gif_digest.py D:\\Desktop\\xxx.gif --out runs\\xxx
    python gif_digest.py <帧序列目录> --fps 10 --out runs\\dir
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCHEMA = "framedelta/digest@1"
SCAN_W, SCAN_H = 64, 36          # 与 see-now cuts.js 同口径的变化分网格
ANALYSIS_SIDE = 256              # 运动估计的工作分辨率（长边）
BG = (13, 16, 19)
FG = (232, 236, 239)
DIM = (128, 138, 146)
ACCENT = (255, 90, 60)

# 阈值集中在一处，写进 digest.json，产物才可复现
_COARSEN_MIN_TOTAL_MS = 5000   # 只对长片段粗化区间，短片段保持逐段原样

TH = {
    "repeat_mean_abs_rgb": 1.0,   # 重复帧：整帧 RGB 均值绝对差 < 1.0
    "jump_hsv_score": 18.0,        # 突变候选：HSV 三通道加权差（0–255）
    "jump_ncc": 0.55,              # 突变：与 HSV 分联合的结构相关性阈值
    "jump_ncc_hard": 0.35,         # 结构相关性低于此值，无需分数也判突变
    "still_energy": 0.8,          # 静止：灰度帧差均值 < 0.8
    "still_changed_frac": 0.02,   # 静止：变化像素占比 < 2%
    "still_dx_dy": 0.5,           # 静止：位移小于半像素
    "subtle_energy": 12.0,        # 微动：能量小 + 局部位移小 + 几乎不缩放
    "subtle_local_mag": 1.5,
    "subtle_scale": 0.02,
    "spike_factor": 1.5,          # 突变必须是邻域尖峰：hsv ≥ factor×邻域中位数 + offset
    "spike_offset": 3.0,
    "spike_baseline_q": 0.5,      # 邻域基线取哪个分位；密接切点（动作片）需要更低的基线
    "transition_gap_ms": 250,     # 相邻突变间隔小于此值 → 同一次转场
    # 内容替换（布局不变、大块内容换）：尖峰规则看不见这类
    "swap_changed_frac": 0.30,
    "swap_ncc": 0.80,
    "swap_energy": 8.0,
    "swap_disp_px": 1.5,
    "swap_scale": 0.02,
    "swap_local_frac": 0.10,      # 有局部运动的块占比上限：内容替换时屏是静的
    "swap_nb_hsv": 10.0,          # 前后邻居必须安静（屏幕本来是静的）
    "swap_nb_energy": 6.0,
    # 累积变化（连续中等变化合成一次可见变化）
    "drift_min_pairs": 5,
    "drift_hsv_lo": 4.0,
    "drift_hsv_hi": 18.0,
    "drift_total_frac": 0.20,     # 首尾两帧的变化面积下限
    "drift_settle_energy": 3.0,   # 变化前后都要静下来
    "drift_settle_pairs": 4,
}


# --------------------------------------------------------------------------
# 载入
# --------------------------------------------------------------------------

def _natural_key(name: str):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", name)]


def _frames_from_pil(im: Image.Image):
    """PIL 多帧图 → RGB ndarray 列表 + 每帧时长（ms）。"""
    frames, durs = [], []
    n = getattr(im, "n_frames", 1)
    for i in range(n):
        im.seek(i)
        rgb = im.convert("RGB")
        frames.append(np.asarray(rgb, dtype=np.uint8).copy())
        d = im.info.get("duration", 0) or 0
        durs.append(int(d))
    loop = im.info.get("loop", None)
    return frames, durs, loop


def load_gif(path: Path):
    with Image.open(path) as im:
        frames, durs, loop = _frames_from_pil(im)
    return frames, durs, loop


def load_dir(path: Path, fps: float | None):
    exts = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
    files = sorted((p for p in path.iterdir() if p.suffix.lower() in exts and p.is_file()),
                   key=lambda p: _natural_key(p.name))
    if not files:
        raise SystemExit(f"目录里没有图片：{path}")
    frames = []
    size = None
    for p in files:
        with Image.open(p) as im:
            rgb = im.convert("RGB")
            if size is None:
                size = rgb.size
            elif rgb.size != size:
                rgb = rgb.resize(size, Image.BILINEAR)
            frames.append(np.asarray(rgb, dtype=np.uint8).copy())
    d = int(round(1000 / (fps if fps else 10)))
    return frames, [d] * len(frames), None


def _ffprobe_size(path: Path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    w, h = out.stdout.strip().split(",")[:2]
    return int(w), int(h)


def load_video(path: Path):
    if shutil.which("ffmpeg") is None:
        raise SystemExit("找不到 ffmpeg，无法读 mp4（GIF / 帧目录不受影响）")
    w, h = _ffprobe_size(path)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    frame_bytes = w * h * 3
    n = len(raw) // frame_bytes
    if n == 0:
        raise SystemExit(f"视频里没解出帧：{path}")
    arr = np.frombuffer(raw, dtype=np.uint8, count=n * frame_bytes).reshape(n, h, w, 3)
    fps_out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True).stdout.strip()
    fps = 0.0
    if "/" in fps_out:
        a, b = fps_out.split("/")
        fps = float(a) / float(b) if float(b) else 0.0
    d = int(round(1000 / fps)) if fps > 0 else 100
    return [f.copy() for f in arr], [d] * n, None


def load_frames(path: Path, fps: float | None):
    if path.is_dir():
        return load_dir(path, fps)
    ext = path.suffix.lower()
    if ext == ".gif":
        return load_gif(path)
    if ext in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}:
        return load_video(path)
    if ext in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
        with Image.open(path) as im:
            frames, durs, loop = _frames_from_pil(im)
        return frames, durs, loop
    raise SystemExit(f"不认识的输入：{path}")


# --------------------------------------------------------------------------
# 变化分（照 see-now cuts.js 的判据：HSV 三通道加权绝对差，64×36 网格）
# --------------------------------------------------------------------------

def grid_rgb(rgb: np.ndarray) -> np.ndarray:
    """64×36 网格，每格 4 个采样点求平均（同 cuts.js gridRgb）。"""
    h, w = rgb.shape[:2]
    ys0 = ((np.arange(SCAN_H) + 0.25) * h / SCAN_H).astype(int)
    ys1 = ((np.arange(SCAN_H) + 0.75) * h / SCAN_H).astype(int)
    xs0 = ((np.arange(SCAN_W) + 0.25) * w / SCAN_W).astype(int)
    xs1 = ((np.arange(SCAN_W) + 0.75) * w / SCAN_W).astype(int)
    g = (rgb[ys0][:, xs0].astype(np.float32) + rgb[ys0][:, xs1]
         + rgb[ys1][:, xs0] + rgb[ys1][:, xs1]) * 0.25
    return g


def rgb_to_hsv(g: np.ndarray) -> np.ndarray:
    img = Image.fromarray(np.clip(g, 0, 255).astype(np.uint8), "RGB").convert("HSV")
    return np.asarray(img, dtype=np.float32)


def hsv_score(a: np.ndarray, b: np.ndarray) -> float:
    dh = np.abs(a[..., 0] - b[..., 0])
    hue = np.minimum(dh, 255.0 - dh)
    wgt = np.minimum(a[..., 1], b[..., 1]) / 255.0
    d = (hue * wgt + np.abs(a[..., 1] - b[..., 1]) + np.abs(a[..., 2] - b[..., 2])) / 3.0
    d = np.where(np.maximum(a[..., 2], b[..., 2]) < 8, 0.0, d)  # 双黑格不算变化
    return float(d.mean())


def grid_ncc(a: np.ndarray, b: np.ndarray) -> float:
    """64×36 灰度网格的零均值归一化相关；切点会让整幅结构换掉，值会掉下去。"""
    a = a.astype(np.float32).ravel()
    b = b.astype(np.float32).ravel()
    a = a - a.mean()
    b = b - b.mean()
    den = math.sqrt(float((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-9 else 1.0


# --------------------------------------------------------------------------
# 运动估计：相位相关（全局 + 分块），拟合相似变换
# --------------------------------------------------------------------------

_WIN_CACHE: dict = {}


def _hanning(h: int, w: int) -> np.ndarray:
    key = (h, w)
    if key not in _WIN_CACHE:
        _WIN_CACHE[key] = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    return _WIN_CACHE[key]


def _parabolic(vm, v0, vp) -> float:
    den = vm - 2.0 * v0 + vp
    if abs(den) < 1e-12:
        return 0.0
    return float(np.clip(0.5 * (vm - vp) / den, -0.5, 0.5))


def phase_shift(a: np.ndarray, b: np.ndarray):
    """b 相对 a 的位移（亚像素）与相关峰高度。a、b 同尺寸灰度 float32。"""
    h, w = a.shape
    win = _hanning(h, w)
    fa = np.fft.rfft2((a - a.mean()) * win)
    fb = np.fft.rfft2((b - b.mean()) * win)
    r = np.conj(fa) * fb
    r /= np.maximum(np.abs(r), 1e-8)
    c = np.fft.irfft2(r, s=(h, w))
    iy, ix = np.unravel_index(int(np.argmax(c)), c.shape)
    peak = float(c[iy, ix])
    # 峰顶抛物线插值 → 亚像素；相位相关常被整数格点量化，这一步对局部小位移很关键
    dy = _parabolic(c[(iy - 1) % h, ix], c[iy, ix], c[(iy + 1) % h, ix])
    dx = _parabolic(c[iy, (ix - 1) % w], c[iy, ix], c[iy, (ix + 1) % w])
    sy = float(iy if iy <= h // 2 else iy - h) + dy
    sx = float(ix if ix <= w // 2 else ix - w) + dx
    return sx, sy, peak


def similarity_from_field(pts: np.ndarray, disp: np.ndarray, wts: np.ndarray,
                          center: tuple, drop: float = 2.0):
    """拟合 disp = t + k·(p − center)，返回 (tx, ty, scale, resid, 有效点数)。

    分块相位相关给出的位移场是实测的；缩放在这里从场的发散量反解，
    比逐尺度试错便宜，也顺手给出「哪块在动」（场本身留着画箭头）。
    """
    n = len(pts)
    if n < 3:
        return None
    keep = wts > 0
    tx = ty = 0.0
    k = 0.0
    for _ in range(2):
        if keep.sum() < 3:
            break
        u = pts[keep, 0] - center[0]
        v = pts[keep, 1] - center[1]
        d = disp[keep]
        wt = wts[keep]
        # 行：tx, ty, k
        A = np.zeros((2 * len(u), 3), dtype=np.float64)
        A[0::2, 0] = 1.0
        A[0::2, 2] = u
        A[1::2, 1] = 1.0
        A[1::2, 2] = v
        rhs = np.empty(2 * len(u), dtype=np.float64)
        rhs[0::2] = d[:, 0]
        rhs[1::2] = d[:, 1]
        sw = np.repeat(np.sqrt(wt), 2)
        sol, *_ = np.linalg.lstsq(A * sw[:, None], rhs * sw, rcond=None)
        tx, ty, k = float(sol[0]), float(sol[1]), float(sol[2])
        pred = np.stack([tx + k * (pts[:, 0] - center[0]),
                         ty + k * (pts[:, 1] - center[1])], axis=1)
        res = np.linalg.norm(disp - pred, axis=1)
        new_keep = keep & (res <= max(drop, 0.03 * math.hypot(*center)))
        if (new_keep == keep).all():
            break
        keep = new_keep
    valid = int(keep.sum())
    if valid == 0:
        return None
    res = np.linalg.norm(disp[keep] - np.stack(
        [tx + k * (pts[keep, 0] - center[0]), ty + k * (pts[keep, 1] - center[1])], axis=1), axis=1)
    return tx, ty, 1.0 + k, float(np.median(res)), valid


def block_field(a: np.ndarray, b: np.ndarray, target=10):
    """分块相位相关：自适应块尺寸（约 target×target 块），返回块中心、位移、可信度。"""
    h, w = a.shape
    dim = min(h, w)
    # 块要小到能装进局部结构（瞳孔、手指），又要大到有足够的纹理
    bs = int(max(16, min(dim // 10, 64)))
    bs = min(bs, int(dim * 0.35))
    span = max(1, target - 1)
    step_x = max(4, int(math.ceil((w - bs) / span)))
    step_y = max(4, int(math.ceil((h - bs) / span)))
    xs = list(range(0, w - bs + 1, step_x)) or [0]
    ys = list(range(0, h - bs + 1, step_y)) or [0]
    pts, disp, wts = [], [], []
    for y0 in ys:
        for x0 in xs:
            pa = a[y0:y0 + bs, x0:x0 + bs]
            pb = b[y0:y0 + bs, x0:x0 + bs]
            center = (x0 + bs / 2.0, y0 + bs / 2.0)
            if pa.std() < 3.0:
                pts.append(center)
                disp.append((0.0, 0.0))
                wts.append(0.0)
                continue
            dx, dy, peak = phase_shift(pa, pb)
            ok = peak > 0.07 and abs(dx) < bs * 0.35 and abs(dy) < bs * 0.35
            if ok:
                ix, iy = int(round(dx)), int(round(dy))
                if abs(ix) >= bs or abs(iy) >= bs:
                    ok = False
                elif ix != 0 or iy != 0:
                    # 用整数位移复核：真移过去了就得「更像」，否则是相位相关的假峰
                    base = float(np.abs(pa - pb).mean())
                    moved = float(np.abs(pa - np.roll(np.roll(pb, -iy, axis=0), -ix, axis=1)).mean())
                    if base > 0.5 and moved > base * 0.98:
                        ok = False
            pts.append(center)
            disp.append((dx, dy))
            wts.append(float(peak * pa.std()) if ok else 0.0)
    return np.array(pts, np.float32), np.array(disp, np.float32), np.array(wts, np.float32)


def estimate_motion(prev_gray: np.ndarray, cur_gray: np.ndarray):
    """返回 dict：全局相似变换（dx, dy, scale>1=推近）+ 局部运动统计 + 实测场。"""
    h, w = prev_gray.shape
    center = (w / 2.0, h / 2.0)
    pts, disp, wts = block_field(prev_gray, cur_gray)
    good = wts > 0
    valid = int(good.sum())
    mags = np.hypot(disp[:, 0], disp[:, 1]) if len(disp) else np.zeros(0)
    local_mag_mean = float(np.mean(mags[good])) if good.any() else 0.0
    # 块间位移的分布很偏（大片静止 + 少量局部运动），中位数比均值稳
    local_mag = float(np.median(mags[good])) if good.any() else 0.0
    local_frac = float((mags[good] > 0.6).mean()) if good.any() else 0.0
    # 运动一致性：有多少块站在中位位移 2px 之内（切镜头时这一项会塌下来）
    if valid >= 3:
        med = np.median(disp[good], axis=0)
        coh = float((np.linalg.norm(disp[good] - med, axis=1) < 2.0).mean())
    else:
        coh = 0.0
    fit = similarity_from_field(pts, disp, wts, center)
    if fit is None or fit[4] < 3:
        dx, dy, peak = phase_shift(prev_gray, cur_gray)
        return {"dx": dx, "dy": dy, "scale": 1.0, "resid": float("nan"),
                "valid": valid, "inliers": 0, "coherence": coh,
                "local_mag": local_mag, "local_mag_mean": local_mag_mean, "local_frac": local_frac,
                "pts": pts, "field": disp, "field_w": wts}
    tx, ty, s, resid, inliers = fit
    return {"dx": tx, "dy": ty, "scale": s, "resid": resid,
            "valid": valid, "inliers": inliers, "coherence": coh,
            "local_mag": local_mag, "local_mag_mean": local_mag_mean, "local_frac": local_frac,
            "pts": pts, "field": disp, "field_w": wts}


# --------------------------------------------------------------------------
# 分析
# --------------------------------------------------------------------------

def resample_indices(frames, durs, fps: float | None, max_frames: int | None):
    """按时间重采样到目标 fps，再按 max_frames 均匀截断。全部落在原帧下标上。"""
    n = len(frames)
    idx = list(range(n))
    if fps:
        t = np.cumsum([0] + durs[:-1]) / 1000.0
        total = sum(durs) / 1000.0
        if total > 0:
            grid = np.arange(0, total, 1.0 / fps)
            idx = sorted({int(np.argmin(np.abs(t - g))) for g in grid})
    if max_frames and len(idx) > max_frames:
        pick = np.linspace(0, len(idx) - 1, max_frames).round().astype(int)
        idx = [idx[i] for i in pick]
    return idx


def analyse(frames, durs, idx, work_side=ANALYSIS_SIDE, classify=True):
    """逐对测量变化分、能量、运动；默认顺带分类（classify=False 供标定扫参用）。"""
    sel = [frames[i] for i in idx]
    times = np.cumsum([0] + [durs[i] for i in idx[:-1]])  # ms，起点
    h0, w0 = sel[0].shape[:2]
    # 小图放大到工作分辨率，让亚像素位移有可分辨的尺度；大图缩到 256
    sc = min(2.0, work_side / max(h0, w0))
    wh = (max(16, int(round(w0 * sc))), max(16, int(round(h0 * sc))))
    grays = [np.asarray(Image.fromarray(f).convert("L").resize(wh, Image.BILINEAR),
                        dtype=np.float32) for f in sel]
    # 能量与变化占比在原分辨率上算，阈值才不会随缩放漂移
    origs = [np.asarray(Image.fromarray(f).convert("L"), dtype=np.float32) for f in sel]

    pairs = []
    for k in range(1, len(sel)):
        a, b = sel[k - 1], sel[k]
        ga, gb = grays[k - 1], grays[k]
        sc_hsv = hsv_score(rgb_to_hsv(grid_rgb(a)), rgb_to_hsv(grid_rgb(b)))
        ncc = grid_ncc(grid_rgb(a).mean(axis=2), grid_rgb(b).mean(axis=2))
        diff = np.abs(origs[k] - origs[k - 1])
        energy = float(diff.mean())
        changed = float((diff > 12).mean())
        mot = estimate_motion(ga, gb)
        if mot and sc != 1.0:
            # 工作分辨率 → 源像素
            mot["dx"] /= sc
            mot["dy"] /= sc
            mot["local_mag"] /= sc
            mot["local_mag_mean"] /= sc
            if mot["resid"] == mot["resid"]:
                mot["resid"] /= sc
        pairs.append({
            "frame": k, "t_ms": float(times[k]), "t_start_ms": float(times[k - 1]),
            "hsv": sc_hsv, "ncc": ncc,
            "energy": energy, "changed_frac": changed,
            # 强差面积：|差|>40 的像素占比。整块内容被换掉时它高；整体平移/调色时它低得多
            "hard_frac": float((diff > 40).mean()),
            "kind": "motion",
            "motion": mot,
        })
    if classify:
        classify_pairs(pairs, TH)
    return {
        "idx": idx, "times": times, "wh": wh, "grays": grays, "pairs": pairs,
    }


def content_events(pairs, frames, idx, times, th=None):
    """两类尖峰规则看不见的变化。

    - **swap**：布局不变、内容大块替换（文字块换、图片换）。判据是大面积变化的**孤立性**：
      前后邻居都安静（屏幕本来是静的），全局几乎没有位移，结构相关性仍高。
      相机运动会让邻居一起动，游戏片段因此不会命中（实测 3 段游戏/火焰素材 0 次）。
    - **drift**：连续多帧中等幅度变化累积成的一次可见变化（层滑入、淡入、运镜后停住）。
      判据是「变化前静、变化后也静」——尖峰规则要求相对邻域是尖峰，这类永远判不出来。
    """
    th = {**TH, **(th or {})}
    hs = [p["hsv"] for p in pairs]
    ens = [p["energy"] for p in pairs]
    swaps = []
    for i, p in enumerate(pairs):
        if p["kind"] == "jump":
            continue
        m = p["motion"] or {}
        disp = math.hypot(m.get("dx", 0.0), m.get("dy", 0.0))
        if p["changed_frac"] < th["swap_changed_frac"] or p["ncc"] < th["swap_ncc"]:
            continue
        if p["energy"] < th["swap_energy"] or disp > th["swap_disp_px"]:
            continue
        if abs(m.get("scale", 1.0) - 1.0) > th["swap_scale"]:
            continue
        # 局部不能有运动：内容替换是「屏不动、内容换」；游戏里那一帧会有 29% 的块在动（秋水 10160ms）
        if m.get("local_frac", 0.0) > th["swap_local_frac"]:
            continue
        nb = [j for j in range(max(0, i - 3), min(len(pairs), i + 4)) if j != i]
        if not nb:
            continue
        nb_hsv = float(np.median([hs[j] for j in nb]))
        nb_energy = float(np.median([ens[j] for j in nb]))
        if nb_hsv >= th["swap_nb_hsv"] or nb_energy >= th["swap_nb_energy"]:
            continue
        swaps.append({
            "frame": p["frame"], "t_ms": round(p["t_ms"], 1),
            "changed_frac": round(p["changed_frac"], 3),
            "ncc": round(p["ncc"], 3), "energy": round(p["energy"], 2),
            "neighbour_hsv": round(nb_hsv, 2), "neighbour_energy": round(nb_energy, 2),
            "dx_px": round(m.get("dx", 0.0), 2), "dy_px": round(m.get("dy", 0.0), 2),
        })

    drifts = []
    run = []

    def flush(cur):
        if len(cur) < th["drift_min_pairs"]:
            return
        k0, k1 = cur[0][0], cur[-1][0]
        n_settle = th["drift_settle_pairs"]
        before = ens[max(0, k0 - n_settle):k0]
        after = ens[k1 + 1:k1 + 1 + n_settle]
        if before and float(np.median(before)) >= th["drift_settle_energy"]:
            return
        if after and float(np.median(after)) >= th["drift_settle_energy"]:
            return
        d = _drift_from_run([p for _, p in cur], frames, idx, times, th)
        if d and d["changed_frac"] >= th["drift_total_frac"]:
            drifts.append(d)

    for i, p in enumerate(pairs):
        if p["kind"] == "jump":
            flush(run)
            run = []
            continue
        if th["drift_hsv_lo"] <= p["hsv"] < th["drift_hsv_hi"]:
            run.append((i, p))
        else:
            flush(run)
            run = []
    flush(run)

    # 已经被判成 swap 的单帧不再重复算进 drift
    swap_frames = {s["frame"] for s in swaps}
    for d in drifts:
        d["swap_inside"] = sum(1 for f in range(d["start_frame"], d["end_frame"] + 1)
                               if f in swap_frames)
    return swaps, drifts


def _drift_from_run(run, frames, idx, times, th):
    """一段连续中等变化：用首尾两帧的差分来确认「累积成了可见变化」。"""
    f0, f1 = run[0]["frame"], run[-1]["frame"]
    if f0 - 1 < 0 or f1 >= len(idx):
        return None
    a, b = frames[idx[f0 - 1]], frames[idx[f1]]
    d = np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
    ga, gb = rgb_to_hsv(grid_rgb(a)), rgb_to_hsv(grid_rgb(b))
    dh = np.abs(ga[..., 0] - gb[..., 0])
    hue = np.minimum(dh, 255.0 - dh)
    wgt = np.minimum(ga[..., 1], gb[..., 1]) / 255.0
    gd = (hue * wgt + np.abs(ga[..., 1] - gb[..., 1]) + np.abs(ga[..., 2] - gb[..., 2])) / 3.0
    p = run[-1]["motion"] or {}
    return {
        "start_frame": f0, "end_frame": f1,
        "start_ms": round(float(times[f0 - 1]), 1), "end_ms": round(float(times[f1]), 1),
        "duration_ms": round(float(times[f1] - times[f0 - 1]), 1),
        "pairs": len(run),
        "hsv_mean": round(float(np.mean([q["hsv"] for q in run])), 2),
        "hsv_max": round(float(max(q["hsv"] for q in run)), 2),
        "energy_mean": round(float(np.mean([q["energy"] for q in run])), 2),
        "changed_frac": round(float((np.abs(a.astype(np.int16) - b.astype(np.int16)) > 12).mean()), 3),
        "delta_energy": round(float(d), 2),
        "hard_cells": round(float((gd > 25).mean()), 3),
        "quiet_cells": round(float((gd < 3).mean()), 3),
        "disp_px": round(math.hypot(p.get("dx", 0.0), p.get("dy", 0.0)), 2),
    }


def classify_pairs(pairs, th=None):
    """把测量结果分成 still / subtle / motion / jump（可传入自定义阈值供扫参）。"""
    th = {**TH, **(th or {})}
    for p in pairs:
        kind = "motion"
        if p["ncc"] < th["jump_ncc_hard"] or (p["hsv"] >= th["jump_hsv_score"]
                                              and p["ncc"] < th["jump_ncc"]):
            kind = "jump"
        elif p["energy"] < th["still_energy"] and p["changed_frac"] < th["still_changed_frac"]:
            kind = "still"
        p["kind"] = kind
    # 突变要相对邻近帧是「尖峰」：连续快动作（游戏画面、快速运镜）不该串成一堆切镜头
    hs = np.array([p["hsv"] for p in pairs])
    for i, p in enumerate(pairs):
        lo, hi = max(0, i - 8), min(len(pairs), i + 9)
        win = [hs[j] for j in range(lo, hi) if j != i]
        base = float(np.quantile(win, th["spike_baseline_q"])) if win else 0.0
        p["hsv_base"] = base
        if p["kind"] == "jump" and p["hsv"] < th["spike_factor"] * base + th["spike_offset"]:
            p["kind"] = "subtle" if p["energy"] < th["subtle_energy"] else "motion"
    for p in pairs:
        mot = p["motion"]
        if p["kind"] == "motion" and mot and p["energy"] < th["subtle_energy"] \
                and mot["local_mag"] < th["subtle_local_mag"] \
                and abs(mot["scale"] - 1) < th["subtle_scale"]:
            p["kind"] = "subtle"
        if p["kind"] == "still" and mot:
            p["motion"] = {"dx": 0.0, "dy": 0.0, "scale": 1.0, "resid": 0.0,
                           "valid": mot["valid"], "inliers": mot["inliers"],
                           "coherence": mot["coherence"], "pts": mot["pts"],
                           "field": mot["field"], "field_w": mot["field_w"],
                           "local_mag": 0.0, "local_mag_mean": 0.0, "local_frac": 0.0}


def coarsen_segments(segs, min_ms=200.0, keep=("jump",)):
    """把过短的区间并进邻居：长片段上「每 2-3 对一个区间」读不动。

    规则：反复处理时长 < min_ms 的区间（jump 不并），把它合进能量更高的那一侧邻居，
    区间种类跟着邻居走；再合并相邻同种区间。只在 total 超过 _COARSEN_MIN_TOTAL_MS 时启用。
    """
    if len(segs) < 3:
        return segs
    work = [dict(s) for s in segs]
    changed = True
    while changed and len(work) > 1:
        changed = False
        for i, s in enumerate(work):
            dur = s.get("end_t_ms", 0) - s.get("start_t_ms", 0)
            if dur >= min_ms or s["kind"] in keep:
                continue
            left = work[i - 1] if i > 0 else None
            right = work[i + 1] if i + 1 < len(work) else None
            if left is None and right is None:
                continue
            if left is None:
                tgt = right
                merge_left = False
            elif right is None:
                tgt = left
                merge_left = True
            else:
                merge_left = left.get("energy_max", 0) >= right.get("energy_max", 0)
                tgt = left if merge_left else right
            if merge_left or tgt is left:
                tgt["end_frame"] = s["end_frame"]
                tgt["end_t_ms"] = s["end_t_ms"]
            else:
                tgt["start_frame"] = s["start_frame"]
                tgt["start_t_ms"] = s["start_t_ms"]
            tgt["pairs"] = tgt.get("pairs", 0) + s.get("pairs", 0)
            tgt["energy_max"] = max(tgt.get("energy_max", 0), s.get("energy_max", 0))
            work.pop(i)
            changed = True
            break
    # 相邻同种再并一遍
    out = []
    for s in work:
        if out and out[-1]["kind"] == s["kind"] and s["kind"] not in keep:
            out[-1]["end_frame"] = s["end_frame"]
            out[-1]["end_t_ms"] = s["end_t_ms"]
            out[-1]["pairs"] += s["pairs"]
            out[-1]["energy_max"] = max(out[-1]["energy_max"], s["energy_max"])
        else:
            out.append(s)
    return out


def segments_of(pairs):
    """把逐对判定并成区间。"""
    out = []
    for p in pairs:
        k = p["kind"]
        if out and out[-1]["kind"] == k:
            out[-1]["end_frame"] = p["frame"]
            out[-1]["end_t_ms"] = p["t_ms"]
            out[-1]["_items"].append(p)
        else:
            out.append({"kind": k, "start_frame": p["frame"] - 1, "end_frame": p["frame"],
                        "start_t_ms": p["t_start_ms"], "end_t_ms": p["t_ms"],
                        "_items": [p]})
    for s in out:
        items = s.pop("_items")
        s["pairs"] = len(items)
        ms = [it["motion"] for it in items if it["motion"] and it["kind"] != "jump"]
        s["energy_max"] = max(it["energy"] for it in items)
        s["hsv_max"] = max(it["hsv"] for it in items)
        if ms:
            s["dx_mean"] = float(np.mean([m["dx"] for m in ms]))
            s["dy_mean"] = float(np.mean([m["dy"] for m in ms]))
            s["scale_net"] = float(np.prod([m["scale"] for m in ms]))
            s["resid_med"] = float(np.median([m["resid"] for m in ms if m["resid"] == m["resid"]] or [0]))
            s["local_mag_mean"] = float(np.mean([m.get("local_mag", 0.0) for m in ms]))
            s["local_frac_mean"] = float(np.mean([m.get("local_frac", 0.0) for m in ms]))
        else:
            s["dx_mean"] = s["dy_mean"] = 0.0
            s["scale_net"] = 1.0
            s["resid_med"] = 0.0
            s["local_mag_mean"] = 0.0
            s["local_frac_mean"] = 0.0
        s["duration_ms"] = s["end_t_ms"] - s["start_t_ms"]
    return out


def shot_structure(pairs, idx, times, total_ms):
    """把突变按时间聚成「转场」，转场之间是「镜头」。

    连续几帧都判突变，通常是快速运镜/闪烁，不是一个接一个的切镜头；
    250ms 内相邻的突变算同一次转场。
    """
    gaps = TH["transition_gap_ms"]
    jump_list = [(p["frame"], p["t_ms"]) for p in pairs if p["kind"] == "jump"]
    clusters = []
    for fi, t in jump_list:
        if clusters and t - clusters[-1]["end_ms"] <= gaps:
            clusters[-1]["end_ms"] = t
            clusters[-1]["end_frame"] = fi
            clusters[-1]["jump_frames"].append(idx[fi])
        else:
            clusters.append({"start_frame": fi, "end_frame": fi, "start_ms": t,
                             "end_ms": t, "jump_frames": [idx[fi]]})
    transitions = [{"index": i,
                    "first_frame": idx[c["start_frame"] - 1],
                    "last_frame": idx[c["end_frame"]],
                    "start_ms": round(c["start_ms"], 1), "end_ms": round(c["end_ms"], 1),
                    "duration_ms": round(c["end_ms"] - c["start_ms"], 1),
                    "jumps": len(c["jump_frames"]),
                    "_grid": (c["start_frame"] - 1, c["end_frame"])}
                   for i, c in enumerate(clusters)]

    ranges, prev = [], 0
    for c in clusters:
        if prev <= c["start_frame"] - 1:
            ranges.append((prev, c["start_frame"] - 1))
        prev = c["end_frame"] + 1
    if prev <= len(idx) - 1:
        ranges.append((prev, len(idx) - 1))

    shots = []
    for i, (a, b) in enumerate(ranges):
        inner = [p for p in pairs if a < p["frame"] <= b]
        ms = [p["motion"] for p in inner if p["motion"] and p["kind"] != "jump"]
        end_ms = float(times[b + 1]) if b + 1 < len(times) else float(total_ms)
        shots.append({
            "index": i,
            "start_frame": idx[a], "end_frame": idx[b],
            "start_ms": round(float(times[a]), 1), "end_ms": round(end_ms, 1),
            "duration_ms": round(end_ms - float(times[a]), 1),
            "frames": b - a + 1,
            "holds": sum(1 for p in inner if p["kind"] == "still"),
            "motion_pairs": sum(1 for p in inner if p["kind"] == "motion"),
            "energy_mean": round(float(np.mean([p["energy"] for p in inner])), 2) if inner else 0.0,
            "energy_max": round(max((p["energy"] for p in inner), default=0.0), 2),
            "dx_mean": round(float(np.mean([m["dx"] for m in ms])), 2) if ms else 0.0,
            "dy_mean": round(float(np.mean([m["dy"] for m in ms])), 2) if ms else 0.0,
            "scale_net": round(float(np.prod([m["scale"] for m in ms])) if ms else 1.0, 3),
            "local_mag_max": round(max((m["local_mag"] for m in ms), default=0.0), 2),
            "_grid": (a, b),
        })
    return {"transitions": transitions, "shots": shots}


def pick_key_frames(pairs, n_frames, k=4, shots=None, swaps=None, drifts=None,
                    multi_cut=False):
    """L2 闸门：把时间轴切成 k 段，每段挑一个最有信息量的帧。"""
    n = len(pairs) + 1
    if n <= 2 * k:
        # 短片段：均匀撒点比按能量挑更有用（首尾都要看到）
        pos = sorted({int(round(i * (n - 1) / max(1, k - 1))) for i in range(k)})
        return [(p, "spread") for p in pos]
    prio = {0: (2.4, "first"), n - 1: (2.2, "last")}
    for p in pairs:
        if p["kind"] == "jump":
            # 突变帧优先于镜头能量峰值：漏掉它，annotated 上就只剩 Δarea 0% 的帧，
            # 读图方会以为这段没变化（评测 e4 就是这样：跳变在 0.56s，四张图全是 0%）。
            # 密集切点时不优先「切点前一帧」：否则 4 个格子会被几组相邻帧吃掉，
            # 反而没有一张图代表「这一段长什么样」（评测 e8 就是这样）
            before_p = 2.0 if multi_cut else 2.7
            for fi, why, base in ((p["frame"] - 1, "before_jump", before_p),
                                  (p["frame"], "after_jump", 3.0)):
                if 0 <= fi < n and prio.get(fi, (0, ""))[0] < base:
                    prio[fi] = (base, why)
    # 内容替换与累积变化同样是「变化的那一帧」，漏掉它们代表帧就只剩静止画面
    for s in swaps or []:
        fi = s["frame"]
        if 0 <= fi < n and prio.get(fi, (0, ""))[0] < 2.9:
            prio[fi] = (2.9, "swap")
    for d in drifts or []:
        fi = d["end_frame"]
        if 0 <= fi < n and prio.get(fi, (0, ""))[0] < 2.35:
            prio[fi] = (2.35, "drift_end")
    ranked = sorted((p for p in pairs if p["kind"] in ("motion", "subtle")),
                    key=lambda p: -p["energy"])
    for rank, p in enumerate(ranked[:8]):
        base = 2.0 - 0.1 * rank
        if base > prio.get(p["frame"], (0, ""))[0]:
            prio[p["frame"]] = (base, "motion_peak" if p["kind"] == "motion" else "subtle_peak")

    # 每个镜头的能量峰值：优先让人看到「这段里画面最满」的那一帧
    if shots:
        for s in shots:
            a, b = s["_grid"]
            inner = [p for p in pairs if a < p["frame"] <= b and p["kind"] != "jump"]
            if not inner:
                continue
            best = max(inner, key=lambda p: p["energy"])
            if best["energy"] > TH["still_energy"] * 2 and \
                    2.6 > prio.get(best["frame"], (0, ""))[0]:
                prio[best["frame"]] = (2.6, "shot_peak")

    # 有多个镜头时按镜头分槽：否则「切点前」和「切点后」会挤在同一段时间切片里，
    # 只能二选一——评测 e4 就是这样把切点前的画面丢掉，四张图全是 Δarea 0%。
    if shots and 2 <= len(shots) <= k:
        # 镜头结构把突变那一帧排除在两侧镜头之外（shot1 结束于 jump-1，shot2 始于 jump+1），
        # 分配前把它并入后一个镜头，否则「切点后的第一帧」永远选不到。
        ranges = [[s["start_frame"], s["end_frame"]] for s in shots]
        jumps_at = {p["frame"] for p in pairs if p["kind"] == "jump"}
        for i in range(1, len(ranges)):
            if ranges[i][0] - 1 in jumps_at:
                ranges[i][0] -= 1
        lengths = [b - a + 1 for a, b in ranges]
        alloc = [1] * len(ranges)
        for _ in range(k - len(ranges)):
            i = max(range(len(ranges)), key=lambda j: lengths[j] / alloc[j])
            alloc[i] += 1
        picked = []
        for (lo, hi), cnt in zip(ranges, alloc):
            picked += _pick_in_range(prio, lo, hi, cnt, n)
        seen, out = set(), []
        for fi, why in picked:
            if fi not in seen:
                seen.add(fi)
                out.append((fi, why))
        if len(out) >= 2:
            return sorted(out)

    chosen = []
    for b in range(k):
        lo = int(round(b * n / k))
        hi = int(round((b + 1) * n / k))
        cands = [(pr, fi, why) for fi, (pr, why) in prio.items() if lo <= fi < hi]
        if cands:
            cands.sort(key=lambda c: (-c[0], c[1]))
            chosen.append((cands[0][1], cands[0][2]))
        else:
            chosen.append((min(max(lo, 0), n - 1), "segment_start"))
    seen, out = set(), []
    for fi, why in chosen:
        if fi not in seen:
            seen.add(fi)
            out.append((fi, why))
    return sorted(out)


def _pick_in_range(prio, lo, hi, cnt, n):
    """在 [lo, hi] 里按优先级挑 cnt 个，彼此至少隔开一段（避免两张图几乎一样）。"""
    cands = sorted(((pr, fi, why) for fi, (pr, why) in prio.items() if lo <= fi <= hi and 0 <= fi < n),
                   key=lambda c: (-c[0], c[1]))
    span = hi - lo + 1
    min_gap = max(1, span // (cnt * 2))
    out = []
    for pr, fi, why in cands:
        if len(out) >= cnt:
            break
        if all(abs(fi - x) >= min_gap for x, _ in out):
            out.append((fi, why))
    if not out:
        out.append((lo, "segment_start"))
    return out


def hotspot(pairs, wh=None, pairs_skip_jump=True):
    """变化集中在哪块：在 64×36 网格上累计变化量，给出归一化 bbox 与热度。"""
    acc = np.zeros((SCAN_H, SCAN_W), np.float32)
    cnt = 0
    for p in pairs:
        if pairs_skip_jump and p["kind"] == "jump":
            continue
        acc += p.get("_grid", 0) if isinstance(p.get("_grid"), np.ndarray) else 0
        cnt += 1
    if cnt == 0 or acc.max() <= 0:
        return None
    acc /= cnt
    pos = acc[acc > 0]
    thr = max(1.5, float(np.percentile(pos, 90))) if pos.size else 1.5
    ys, xs = np.where(acc >= thr)
    if len(xs) == 0:
        return None
    return {
        "bbox_norm": [float(xs.min() / SCAN_W), float(ys.min() / SCAN_H),
                      float((xs.max() + 1) / SCAN_W), float((ys.max() + 1) / SCAN_H)],
        "cells_hot": int(len(xs)),
        "hot_fraction": round(float(len(xs) / (SCAN_W * SCAN_H)), 3),
        "peak_norm": [float((np.argmax(acc) % SCAN_W + 0.5) / SCAN_W),
                      float((np.argmax(acc) // SCAN_W + 0.5) / SCAN_H)],
        "mean_abs_diff": round(float(acc.mean()), 2),
        "threshold": round(thr, 2),
    }


# --------------------------------------------------------------------------
# 出图
# --------------------------------------------------------------------------

def _font(size=13):
    for name in ("consola.ttf", "segoeui.ttf", "msyh.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(im: Image.Image, box_w: int, box_h: int):
    im = im.copy()
    im.thumbnail((box_w, box_h), Image.LANCZOS)
    return im


def render_sheet(frames, durs, idx, times, key_grid, out: Path,
                 max_cells=36, cell_w=180, delta_by_grid=None):
    delta_by_grid = delta_by_grid or {}
    n = len(idx)
    grid_all = list(range(n))
    if n > max_cells:
        grid_all = [int(i) for i in np.linspace(0, n - 1, max_cells).round().astype(int)]
    chosen = sorted(dict.fromkeys(grid_all + sorted(key_grid)))[:max_cells + 8]
    h0, w0 = frames[idx[chosen[0]]].shape[:2]
    ar = w0 / h0 if h0 else 1
    cw, ch = cell_w, int(round(cell_w / ar))
    pad, label_h = 8, 22
    cols = min(6, max(1, math.ceil(math.sqrt(len(chosen)))))
    rows = math.ceil(len(chosen) / cols)
    W = cols * (cw + pad) + pad
    Hh = rows * (ch + label_h + pad) + pad + 26
    img = Image.new("RGB", (W, Hh), BG)
    dr = ImageDraw.Draw(img)
    f = _font(12)
    for i, g in enumerate(chosen):
        c, r = i % cols, i // cols
        x, y = pad + c * (cw + pad), 26 + r * (ch + label_h + pad)
        dr.rectangle([x - 1, y - 1, x + cw, y + ch], outline=(40, 46, 52))
        thumb = _fit(Image.fromarray(frames[idx[g]]), cw, ch)
        img.paste(thumb, (x + max(0, (cw - thumb.width) // 2), y))
        t = float(times[g]) / 1000
        tag = f"{idx[g]:d}  {t:5.2f}s"
        is_key = g in key_grid
        dlt = delta_by_grid.get(g)
        if dlt is not None:
            tag += f"  Δ{dlt * 100:.0f}%"
        dr.text((x, y + ch + 3), tag, font=f, fill=FG if is_key else DIM)
        if is_key:
            dr.rectangle([x - 1, y - 1, x + cw, y + ch], outline=ACCENT, width=2)
    dr.text((pad, 6), f"sheet · {len(frames)} frames · {sum(durs)/1000:.2f}s · "
                      f"{len(chosen)} cells", font=_font(13), fill=FG)
    img.save(out)


def render_kymograph(frames, idx, grays, pairs, out: Path):
    """在某条最活跃的竖带上做时间切片：y = 空间，x = 时间。"""
    st = np.stack(grays)                       # n, h, w
    var = st.var(axis=0)                       # 时间方差
    col = var.mean(axis=0)                     # 每列的活跃度
    if col.max() <= 0.01:
        w = st.shape[2]
        x0, x1 = w // 2 - 1, w // 2 + 2
    else:
        best = int(np.argmax(col))
        half = max(1, st.shape[2] // 40)
        x0, x1 = max(0, best - half), min(st.shape[2], best + half + 1)
    band = st[:, :, x0:x1].mean(axis=2)        # n, h
    h, n = band.shape[1], band.shape[0]
    # 每帧至少几个像素宽，否则 6 帧就是 6px 宽的细条，没法看
    px = int(np.clip(round(520 / max(1, n)), 6, 96))
    arr = np.repeat(band.T, px, axis=1)        # h, n*px
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "L").convert("RGB")
    tw = img.width
    canvas = Image.new("RGB", (tw + 74, h + 46), BG)
    canvas.paste(img, (70, 22))
    dr = ImageDraw.Draw(canvas)
    f = _font(11)
    dr.text((6, 4), f"kymograph · band x={x0}..{x1}", font=f, fill=DIM)
    dr.line([70, h + 26, 70 + tw, h + 26], fill=DIM)
    for p in (0.0, 0.25, 0.5, 0.75, 1.0):
        i = int(round(p * (n - 1)))
        x = 70 + (i + 0.5) * px
        dr.line([x, h + 22, x, h + 27], fill=DIM)
        dr.text((min(max(2, x - 10), tw + 46), h + 30), f"{idx[i]:d}", font=f, fill=DIM)
    for p in pairs:
        if p["kind"] == "jump":
            x = 70 + (p["frame"] - 0.5) * px
            dr.line([x, 18, x, h + 26], fill=ACCENT, width=2)
    dr.text((6, h // 2), "y", font=f, fill=DIM)
    canvas.save(out)


def _arrow(dr: ImageDraw.ImageDraw, x0, y0, x1, y1, color, width=2, head=6):
    dr.line([(x0, y0), (x1, y1)], fill=color, width=width)
    ang = math.atan2(y1 - y0, x1 - x0)
    for s in (+1, -1):
        a = ang + s * math.radians(150)
        dr.line([(x1, y1), (x1 + head * math.cos(a), y1 + head * math.sin(a))],
                fill=color, width=width)


def render_annotated(frames, idx, times, key_grid, analyses, out: Path):
    """3–4 代表帧，各配「相对上一帧变了多少」+ 实测位移场箭头（grid 下标 → 原帧）。

    每格头一行固定给 Δarea（变化面积占比）与全局位移：读图方据此分辨
    「整幅换画」与「一小块 UI 更新」，不必自己比像素。
    """
    by_frame = {p["frame"]: p for p in analyses["pairs"]}
    cell_w = 430
    h0, w0 = frames[idx[key_grid[0]]].shape[:2]
    ar = w0 / h0
    cw = cell_w
    ch = int(round(cell_w / ar))
    cols = min(2, len(key_grid))
    rows = math.ceil(len(key_grid) / cols)
    pad, top = 10, 40
    W = cols * (cw + pad) + pad
    Hh = top + rows * (ch + 52) + pad
    canvas = Image.new("RGB", (W, Hh), BG)
    dr = ImageDraw.Draw(canvas)
    f, fs = _font(12), _font(11)
    dr.text((pad, 8), "annotated · Δarea = pixels changed vs previous frame · "
                      "arrows = block displacement > 0.5px (x3)",
            font=_font(13), fill=FG)
    for i, g in enumerate(key_grid):
        fi = idx[g]
        c, r = i % cols, i // cols
        x, y = pad + c * (cw + pad), top + r * (ch + 48)
        im = Image.fromarray(frames[fi]).resize((cw, ch), Image.LANCZOS)
        canvas.paste(im, (x, y))
        dr.rectangle([x - 1, y - 1, x + cw, y + ch], outline=(50, 56, 62))
        p = by_frame.get(g)
        t = times[g] / 1000
        if not p:
            dr.text((x, y + ch + 4), f"#{fi}  {t:.2f}s  Δarea —", font=f, fill=FG)
            dr.text((x, y + ch + 21), "first frame (no previous)", font=fs, fill=DIM)
            continue

        m = p["motion"] or {}
        frac = p["changed_frac"] * 100
        head = f"#{fi}  {t:.2f}s  Δarea {frac:.0f}%"
        if m:
            head += f"  dx{m.get('dx', 0):+.2f} dy{m.get('dy', 0):+.2f} x{m.get('scale', 1):.3f}"
        dr.text((x, y + ch + 4), head, font=f, fill=FG)

        if p["kind"] == "jump":
            line2 = f"jump · hsv {p['hsv']:.1f} · ncc {p['ncc']:.2f} · energy {p['energy']:.1f}"
            if m.get("resid") == m.get("resid"):
                line2 += f" · resid {m['resid']:.1f}px"
            dr.text((x, y + ch + 21), line2, font=fs, fill=DIM)
            continue

        show_arrows = bool(m) and m["local_frac"] >= 0.05 and m["local_mag"] >= 0.05
        gh, gw = analyses["grays"][g].shape if g < len(analyses["grays"]) else (0, 0)
        if show_arrows and gw and gh:
            sx, sy = cw / gw, ch / gh
            pts, fld, wts = m["pts"], m["field"], m["field_w"]
            for (px, py), (dx, dy), w in zip(pts, fld, wts):
                if w <= 0:
                    continue
                mag = math.hypot(dx, dy)
                if mag < 0.5:
                    continue
                x0, y0 = x + px * sx, y + py * sy
                x1, y1 = x0 + dx * sx * 3.0, y0 + dy * sy * 3.0
                col = ACCENT if mag > 2 else (255, 170, 90)
                _arrow(dr, x0, y0, x1, y1, col, width=2)
        if m:
            line2 = (f"{p['kind']} · local {m['local_mag']:.2f}px · "
                     f"{m['local_frac']*100:.0f}% blocks · resid {m['resid']:.2f}px")
            if frac < 2:
                line2 += " · tiny area"
            elif not show_arrows:
                line2 += " · no coherent motion"
        else:
            line2 = f"{p['kind']} · energy {p['energy']:.1f}"
        dr.text((x, y + ch + 21), line2, font=fs, fill=DIM)
    canvas.save(out)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def build(input_path: Path, out_dir: Path, fps=None, max_frames=None, key_n=4,
          cell_w=180):
    frames, durs, loop = load_frames(input_path, fps)
    ext = input_path.suffix.lower()
    source_type = ('dir' if input_path.is_dir()
                   else 'gif' if ext == '.gif'
                   else 'video' if ext in {'.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v'}
                   else 'image')
    n = len(frames)
    raw_durs = list(durs)
    pos = [d for d in raw_durs if d > 0]
    fallback = int(np.median(pos)) if pos else 100
    durations = [d if d > 0 else fallback for d in raw_durs]
    zero_durs = sum(1 for d in raw_durs if d <= 0)

    # 重复帧统计（定义写进产物，否则数字没有意义）
    repeats = 0
    prev = frames[0]
    for i in range(1, n):
        if float(np.abs(frames[i].astype(np.int16) - prev.astype(np.int16)).mean()) \
                < TH["repeat_mean_abs_rgb"]:
            repeats += 1
        prev = frames[i]

    idx = resample_indices(frames, durations, fps, max_frames)
    analyses = analyse(frames, durations, idx)
    pairs = analyses["pairs"]
    total_ms = sum(durations)
    segs = segments_of(pairs)
    if total_ms > _COARSEN_MIN_TOTAL_MS:
        segs = coarsen_segments(segs)
    times = analyses["times"]
    struct = shot_structure(pairs, idx, times, total_ms)
    # shot_structure 的 start/end 是原始帧号；选帧用 idx 下标。重采样时两个空间不同，
    # 不映射会选出界（--fps 10 时 idx 只有 31 个，镜头却报到 91）。
    orig2grid = {int(f): g for g, f in enumerate(idx)}
    shots_grid = []
    for s in struct["shots"]:
        t = dict(s)
        t["start_frame"] = orig2grid.get(int(s["start_frame"]), 0)
        t["end_frame"] = orig2grid.get(int(s["end_frame"]), len(idx) - 1)
        shots_grid.append(t)
    # 先判 swap/drift，再选代表帧——「变化的那一帧」要参与选帧
    swaps, drifts = content_events(pairs, frames, idx, times)
    picked = pick_key_frames(pairs, n, key_n, shots=shots_grid,
                             swaps=swaps, drifts=drifts,
                             multi_cut=len(struct["transitions"]) > 1)
    keys = [fi for fi, _ in picked]
    reasons = {fi: why for fi, why in picked}
    by_frame = {p["frame"]: p for p in pairs}

    # 每个 pair 的逐格变化（给 hotspot 用，与 hsv_score 同口径）
    for p in pairs:
        a, b = frames[idx[p["frame"] - 1]], frames[idx[p["frame"]]]
        ha, hb = rgb_to_hsv(grid_rgb(a)), rgb_to_hsv(grid_rgb(b))
        dh = np.abs(ha[..., 0] - hb[..., 0])
        hue = np.minimum(dh, 255.0 - dh)
        wgt = np.minimum(ha[..., 1], hb[..., 1]) / 255.0
        gd = (hue * wgt + np.abs(ha[..., 1] - hb[..., 1])
              + np.abs(ha[..., 2] - hb[..., 2])) / 3.0
        gd = np.where(np.maximum(ha[..., 2], hb[..., 2]) < 8, 0.0, gd)
        p["_grid"] = gd
        p["cells_quiet"] = float((gd < 3).mean())
        p["cells_hard"] = float((gd > 25).mean())

    out_dir.mkdir(parents=True, exist_ok=True)
    real_keys = [idx[k] for k in keys]

    # meta.json
    meta = {
        "schema": "framedelta/meta@1",
        "source": str(input_path),
        "source_type": source_type,
        "width": int(frames[0].shape[1]), "height": int(frames[0].shape[0]),
        "frames": n, "duration_ms": int(total_ms),
        "duration_ms_raw": int(sum(raw_durs)),
        "zero_duration_frames": zero_durs,
        "duration_definition": f"零时长帧按非零中位数 {fallback}ms 计",
        "fps": round(n * 1000.0 / total_ms, 3) if total_ms else None,
        "loop": loop, "repeat_frames": repeats,
        "repeat_definition": f"mean |ΔRGB| < {TH['repeat_mean_abs_rgb']}",
        "frame_durations_ms": durations if len(set(durations)) > 1 else [durations[0]],
        "analysed_frames": len(idx),
        "pair_interval_ms_mean": round((times[-1] - times[0]) / max(1, len(idx) - 1), 1)
        if len(idx) > 1 else None,
        "analysis_scale": list(analyses["wh"]),
        "thresholds": TH,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")

    # flow.csv
    with (out_dir / "flow.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["pair", "from_frame", "to_frame", "t_ms", "kind", "hsv_score", "ncc",
                    "coherence", "energy", "changed_frac", "hard_frac", "dx_px", "dy_px", "scale",
                    "resid_px", "valid_blocks", "local_mag_px", "local_mag_mean_px", "local_frac"])
        for p in pairs:
            m = p["motion"]
            w.writerow([p["frame"], idx[p["frame"] - 1], idx[p["frame"]], f"{p['t_ms']:.0f}",
                        p["kind"], f"{p['hsv']:.2f}", f"{p['ncc']:.3f}",
                        f"{m['coherence']:.3f}" if m else "",
                        f"{p['energy']:.2f}",
                        f"{p['changed_frac']:.3f}",
                        f"{p.get('hard_frac', 0.0):.3f}",
                        f"{m['dx']:.2f}" if m else "", f"{m['dy']:.2f}" if m else "",
                        f"{m['scale']:.4f}" if m else "",
                        f"{m['resid']:.2f}" if m else "", m["valid"] if m else "",
                        f"{m.get('local_mag', 0):.2f}" if m else "",
                        f"{m.get('local_mag_mean', 0):.2f}" if m else "",
                        f"{m.get('local_frac', 0):.3f}" if m else ""])

    render_sheet(frames, durations, idx, times, set(keys), out_dir / "sheet.png",
                 cell_w=cell_w,
                 delta_by_grid={p["frame"]: p["changed_frac"] for p in pairs})
    render_kymograph(frames, idx, analyses["grays"], pairs, out_dir / "kymograph.png")
    render_annotated(frames, idx, times, keys, analyses, out_dir / "annotated.png")

    # digest.json
    jumps = [{"frame": p["frame"], "t_ms": p["t_ms"], "hsv_score": round(p["hsv"], 2),
              "ncc": round(p["ncc"], 3), "energy": round(p["energy"], 2),
              "changed_frac": round(p["changed_frac"], 3),
              "coherence": round(p["motion"]["coherence"], 3) if p["motion"] else None,
              "resid_px": round(p["motion"]["resid"], 2)
              if p["motion"] and p["motion"]["resid"] == p["motion"]["resid"] else None}
             for p in pairs if p["kind"] == "jump"]
    intervals = [{"kind": s["kind"], "start_frame": s["start_frame"], "end_frame": s["end_frame"],
                  "start_ms": round(s["start_t_ms"], 1), "end_ms": round(s["end_t_ms"], 1),
                  "duration_ms": round(s["duration_ms"], 1),
                  "energy_max": round(s["energy_max"], 2),
                  "dx_mean": round(s["dx_mean"], 2), "dy_mean": round(s["dy_mean"], 2),
                  "scale_net": round(s["scale_net"], 3), "resid_med": round(s["resid_med"], 2),
                  "local_mag_mean": round(s["local_mag_mean"], 2),
                  "local_frac_mean": round(s["local_frac_mean"], 3),
                  "pairs": s["pairs"]} for s in segs]
    hot = hotspot(pairs)
    transitions = [{k: v for k, v in t.items() if k != "_grid"} for t in struct["transitions"]]
    shots = [{k: v for k, v in s.items() if k != "_grid"} for s in struct["shots"]]
    digest = {
        "schema": SCHEMA,
        "source": str(input_path),
        "meta": {"frames": n, "duration_ms": int(total_ms),
                 "fps": meta["fps"], "repeat_frames": repeats,
                 "analysed": len(idx)},
        "thresholds": TH,
        "counts": {
            "pairs": len(pairs),
            "still": sum(1 for p in pairs if p["kind"] == "still"),
            "subtle": sum(1 for p in pairs if p["kind"] == "subtle"),
            "motion": sum(1 for p in pairs if p["kind"] == "motion"),
            "jumps": len(jumps),
            "swaps": len(swaps),
            "drifts": len(drifts),
        },
        "jumps": jumps,
        "swaps": swaps,
        "drifts": drifts,
        "transitions": transitions,
        "shots": shots,
        "intervals": intervals,
        "hotspot": hot,
        "key_frames": [
            # 每张代表帧都带「相对上一帧」的量化：读图方据此判「整幅换画」还是「小块更新」，
            # 这是评测里 e5 误报（把 <1% 的面板更新读成弹出卡片）的直接对策。
            dict([("frame", idx[g]), ("t_ms", round(float(times[g]), 1)),
                  ("reason", reasons.get(g, "selected"))] +
                 ([("delta", {"kind": by_frame[g]["kind"],
                              "changed_frac": round(by_frame[g]["changed_frac"], 3),
                              "dx_px": round((by_frame[g]["motion"] or {}).get("dx", 0.0), 2),
                              "dy_px": round((by_frame[g]["motion"] or {}).get("dy", 0.0), 2),
                              "scale": round((by_frame[g]["motion"] or {}).get("scale", 1.0), 3),
                              "local_mag_px": round(
                                  (by_frame[g]["motion"] or {}).get("local_mag", 0.0), 2)})]
                  if g in by_frame else []))
            for g in keys],
        "artifacts": ["meta.json", "sheet.png", "flow.csv", "kymograph.png",
                      "annotated.png", "digest.json"],
    }
    (out_dir / "digest.json").write_text(json.dumps(digest, ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    return meta, digest


def main(argv=None):
    ap = argparse.ArgumentParser(description="framedelta：连续帧 → 变化描述 + 可读的图")
    ap.add_argument("input", help="GIF / mp4 / 帧序列目录")
    ap.add_argument("--fps", type=float, default=None, help="按此 fps 重采样")
    ap.add_argument("--max-frames", type=int, default=None)
    ap.add_argument("--out", default=None, help="输出目录（默认 runs/<名字>）")
    ap.add_argument("--key", type=int, default=4, help="代表帧数量（默认 4）")
    ap.add_argument("--cell-w", type=int, default=180, help="总览单格宽度")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    src = Path(a.input)
    if not src.exists():
        raise SystemExit(f"输入不存在：{src}")
    out = Path(a.out) if a.out else Path("runs") / src.stem
    meta, digest = build(src, out, fps=a.fps, max_frames=a.max_frames,
                         key_n=a.key, cell_w=a.cell_w)
    if not a.quiet:
        print(json.dumps({
            "out": str(out), "frames": meta["frames"], "fps": meta["fps"],
            "duration_ms": meta["duration_ms"], "repeat_frames": meta["repeat_frames"],
            "counts": digest["counts"],
            "key_frames": [k["frame"] for k in digest["key_frames"]],
            "hotspot": digest["hotspot"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
