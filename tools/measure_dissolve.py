#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""溶解漏点的可分性测量（D 节 · 只测量，不改判据）。

为什么单写：`docs/方案_渐变判据.md` 第 2 节已经把整幅强度统计判死（hsv / energy /
调色板），剩下的问题是「区域级」与「两端点混合」这两族**能不能分开**，再谈实现。
所以这里不产出 digest、不 import 任何判据阈值，只把两族统计打到同一张表上，用
预登记规则出结论：两处正样本都要排在所有负样本之上、且余量 >= 20% 才算可分。

两族：

R 区域 —— 每格在 ±6px 内找最佳平移后算 NCC（「运动解释率」）。高 = 内容只是被
移动（运镜 / 物体移动），低 = 内容被替换或消失。看低分格的面积、连通性，以及
0.4s 后是否仍然低（文字淡出不会回来，路过的东西会）。
B 混合 —— 端点 P/Q 之间的线性混合拟合：叠化的中间帧落在 P–Q 连线上（残差低、
α 单调下降），纯运镜的中间帧不在线上。

用法
    python tools/measure_dissolve.py                      # 3 段长素材全部窗口
    python tools/measure_dissolve.py --case L1_trailer    # 只测一段
    python tools/measure_dissolve.py --dump               # 正样本额外打格子图
"""
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import gif_digest as g  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ---- 固定网格：工作分辨率 256x144，8x15 个 16x16 格，居中偏移 8px 给平移留余量 ----
W, H = 256, 144
CELL = 16
MARGIN = 8
NY = (H - 2 * MARGIN) // CELL          # 8
NX = (W - 2 * MARGIN) // CELL          # 15
ROWS = np.arange(0, NY * CELL, CELL)
COLS = np.arange(0, NX * CELL, CELL)
SHIFTS = [(dy, dx) for dy in range(-6, 7) for dx in range(-6, 7)]
CELL_N = float(CELL * CELL)

# 预登记判据（写死在代码里，测完不许改）
LOW_NCC = 0.55          # 「运动解释率」低：连最佳平移都匹配不上
STD_DROP = 0.60         # 格内标准差降到原来的 6 成以下：文字/细节消失
BLEND_RESID = 0.35      # 中间帧落在 P–Q 连线上的残差上限（按端点距离归一）
BLEND_COVER = 0.60      # 窗口内至少这么多帧落在连线上
ALPHA_MONO = 0.85       # α 单调步数占比下限
MARGIN_OK = 0.20        # 「可分」要求正样本比最强负样本高 20%

# H / D 两族是第二版（第一版 R/B 用失败原因反推出来的），阈值是探索性的
H_SEP = 0.10            # 两端调色板至少要差这么多（运镜两端通常 <0.10）
H_RESID = 0.30          # 中间帧偏离 P–Q 连线（按端点距离归一）
H_COVER = 0.75          # 多少中间帧落在连线上
H_MONO = 0.85
D_DIP = 0.85            # 细节能量降到两端均值的 85% 以下
D_AT = (0.25, 0.75)     # 最低点要落在窗口中部
T_DROP = 0.30           # 高频能量至少掉 30%（文字/图层消失）
T_MONO = 0.65           # 而且大部分步子是往下的（单调淡出，不是来回）
T_PERSIST = 0.30        # 0.4s 后仍然是低位（闪白会回来）
I_LOW = 0.80            # 至少 8 成中间帧与两端都不像
I_MID = 0.55            # 中点帧与最近一端的最佳平移 NCC 上限

# 正样本窗口（来自 eval/truth_long.json 的写法：溶解记在新画面首次稳定的中点附近）
POS = {
    "L1_trailer": [
        (10.70, 11.45, "11.1 文字淡出"),
        (13.88, 14.50, "14.2 叠化"),
    ],
}

CLIPS = {name: ROOT / "runs" / "eval" / "_clips" / ("%s.mp4" % name)
         for name in ("L1_trailer", "L2_aimanhua", "L3_screen")}

NEG_LENS = (0.60, 0.70)
STRIDE = 0.10
GUARD = 0.35          # 负样本窗口与真值变化保持的间隔


def _cell_sums(X):
    """128x240 → 8x15 的块和。X 的宽高必须是 CELL 的整数倍。"""
    t = np.add.reduceat(X, ROWS, axis=0)
    return np.add.reduceat(t, COLS, axis=1)


def _ncc_from_sums(sa, sa2, sb, sb2, cov):
    num = CELL_N * cov - sa * sb
    den = np.sqrt(np.maximum((CELL_N * sa2 - sa * sa) * (CELL_N * sb2 - sb * sb), 1e-6))
    return np.clip(num / den, -1.0, 1.0)


class Case:
    def __init__(self, name, path):
        info = g.probe_video(path)
        frames, durs, _ = g.load_frames(path, None, info=info)
        self.name = name
        self.path = path
        self.n = len(frames)
        self.times = np.cumsum([0.0] + [float(d) for d in durs[:-1]]) / 1000.0
        self.total = float(sum(durs)) / 1000.0
        grays = []
        hists = []
        for f in frames:
            im = g.Image.fromarray(f)
            grays.append(np.asarray(im.convert("L").resize((W, H), g.Image.BILINEAR),
                                    dtype=np.float32))
            small = np.asarray(im.convert("RGB").resize((64, 36), g.Image.BILINEAR),
                               dtype=np.uint8)
            q = (small >> 5).astype(np.int32)
            idx = (q[..., 0] << 6) | (q[..., 1] << 3) | q[..., 2]
            h = np.bincount(idx.ravel(), minlength=512).astype(np.float32)
            hists.append(h / max(1.0, h.sum()))
        self.grays = np.stack(grays)
        self.hists = np.stack(hists)
        self.hf = np.asarray([hf_energy(x) for x in grays], dtype=np.float32)
        del frames
        self.info = info

    def at(self, t):
        return int(np.argmin(np.abs(self.times - float(t))))

    def best_shift_ncc(self, ia, ib):
        """逐格：在 ±6px 内取 NCC 最大值（最佳平移后的匹配度）。"""
        a, b = self.grays[ia], self.grays[ib]
        y0, x0, h, w = MARGIN, MARGIN, NY * CELL, NX * CELL
        A = a[y0:y0 + h, x0:x0 + w]
        sa, sa2 = _cell_sums(A), _cell_sums(A * A)
        best = np.full((NY, NX), -1.0, dtype=np.float32)
        for dy, dx in SHIFTS:
            B = b[y0 + dy:y0 + dy + h, x0 + dx:x0 + dx + w]
            if B.shape != A.shape:
                continue
            ncc = _ncc_from_sums(sa, sa2, _cell_sums(B), _cell_sums(B * B), _cell_sums(A * B))
            best = np.maximum(best, ncc)
        return best

    def cell_std(self, ia):
        a = self.grays[ia][MARGIN:MARGIN + NY * CELL, MARGIN:MARGIN + NX * CELL]
        m = _cell_sums(a) / CELL_N
        v = _cell_sums(a * a) / CELL_N - m * m
        return np.sqrt(np.maximum(v, 0.0))


def region_stats(case, t0, t1, persist_s=0.40):
    i0, i1 = case.at(t0), case.at(t1)
    if i1 <= i0:
        return None
    ncc = case.best_shift_ncc(i0, i1)
    low = ncc < LOW_NCC
    n_low = int(low.sum())
    # 连通性：低分格是否连成一片（文字/图层区域是连贯的，噪声不是）
    seen = np.zeros_like(low, dtype=bool)
    biggest = 0
    for y in range(NY):
        for x in range(NX):
            if not low[y, x] or seen[y, x]:
                continue
            stack, size = [(y, x)], 0
            seen[y, x] = True
            while stack:
                cy, cx = stack.pop()
                size += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < NY and 0 <= nx < NX and low[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            biggest = max(biggest, size)
    # 0.4s 后：文字/图层消失不会回来；路过的东西会回来
    j = case.at(t1 + persist_s)
    stable = 0.0
    if j > i1 and n_low:
        later = case.best_shift_ncc(i0, j) < LOW_NCC
        stable = float((low & later).sum()) / n_low
    # 格内标准差塌陷：文字/细节消失时该区域变平滑
    sa, sb = case.cell_std(i0), case.cell_std(i1)
    ratio = sb / np.maximum(sa, 1e-3)
    std_drop = float((ratio < STD_DROP).mean())
    return {
        "i0": i0, "i1": i1, "mid_s": (t0 + t1) / 2.0,
        "low_frac": n_low / float(NY * NX),
        "low_cluster": (biggest / n_low) if n_low else 0.0,
        "low_stable": stable,
        "bg_ncc": float(np.median(ncc[~low])) if (~low).any() else 0.0,
        "ncc_low_med": float(np.median(ncc[low])) if n_low else 1.0,
        "std_drop": std_drop,
        "ncc_map": ncc,
    }


def blend_stats(case, t0, t1):
    i0, i1 = case.at(t0), case.at(t1)
    if i1 - i0 < 3:
        return None
    P, Q = case.grays[i0], case.grays[i1]
    D = (P - Q).ravel()
    dd = float(D @ D) + 1e-6
    d_rms = math.sqrt(dd / D.size)
    resid, alphas = [], []
    for k in range(i0 + 1, i1):
        F = case.grays[k].ravel()
        a = float((F - Q.ravel()) @ D) / dd
        a = min(1.0, max(0.0, a))
        R = F - (a * P.ravel() + (1.0 - a) * Q.ravel())
        resid.append(math.sqrt(float(R @ R) / R.size) / (d_rms + 1e-6))
        alphas.append(a)
    resid = np.asarray(resid)
    alphas = np.asarray(alphas)
    steps = np.diff(np.concatenate([[1.0], alphas, [0.0]]))
    down = float((steps <= 1e-6).mean())
    return {
        "i0": i0, "i1": i1, "mid_s": (t0 + t1) / 2.0,
        "blend_resid": float(np.median(resid)),
        "blend_cover": float((resid < BLEND_RESID).mean()),
        "alpha_mono": down,
        "alpha_span": float(alphas[0] - alphas[-1]) if len(alphas) else 0.0,
        "n_frames": len(resid),
    }


def hist_stats(case, t0, t1):
    """H 族：调色板轨迹的线性混合（8x8x8 RGB 直方图，对运动不敏感）。

    叠化在直方图空间里是一条从 h(P) 到 h(Q) 的直线；运镜基本不挪直方图（两端太近，
    由 hist_sep 挡掉）；硬切的两端也远，但中间帧不在直线上（α 跳变、残差大）。
    """
    i0, i1 = case.at(t0), case.at(t1)
    if i1 - i0 < 3:
        return None
    P, Q = case.hists[i0], case.hists[i1]
    D = P - Q
    dd = float(D @ D)
    sep = math.sqrt(dd)
    if dd < 1e-9:
        return {"hist_sep": 0.0, "hist_resid": 1.0, "hist_cover": 0.0,
                "hist_mono": 0.0, "hist_alpha": 0.0}
    resid, alphas = [], []
    for k in range(i0 + 1, i1):
        H = case.hists[k]
        a = float((H - Q) @ D) / dd
        a = min(1.0, max(0.0, a))
        R = H - (a * P + (1.0 - a) * Q)
        resid.append(math.sqrt(float(R @ R)) / sep)
        alphas.append(a)
    steps = np.diff(np.concatenate([[1.0], np.asarray(alphas), [0.0]]))
    return {
        "hist_sep": sep,
        "hist_resid": float(np.median(resid)),
        "hist_cover": float((np.asarray(resid) < 0.30).mean()),
        "hist_mono": float((steps <= 1e-6).mean()),
        "hist_alpha": float(alphas[0] - alphas[-1]) if alphas else 0.0,
    }


def detail_stats(case, t0, t1):
    """D 族：细节能量在过渡中塌陷成 V 形。

    两张不相关的画面按比例混合，高频细节互相抵消 → 格内标准差在中点降到两端之下。
    运镜不会（同一画面的细节跟着走）；硬切也不会（没有中间态）。
    """
    i0, i1 = case.at(t0), case.at(t1)
    if i1 - i0 < 3:
        return None
    det = []
    for k in range(i0, i1 + 1):
        a = case.grays[k]
        m = _cell_sums(a) / CELL_N
        v = _cell_sums(a * a) / CELL_N - m * m
        det.append(float(np.sqrt(np.maximum(v, 0.0)).mean()))
    det = np.asarray(det)
    edge = (det[0] + det[-1]) / 2.0
    inner = det[1:-1]
    dip = float(inner.min() / edge) if edge > 1e-6 else 1.0
    at = float((int(inner.argmin()) + 1) / (len(det) - 1))
    return {"detail_dip": dip, "detail_at": at,
            "detail_ends": float(det[0] - det[-1])}


def hf_energy(gray):
    """梯度能量：亮细笔画（文字/logo）贡献大，模糊 / 空场贡献小。"""
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    return float(gx + gy)


def text_stats(case, t0, t1):
    """T 族（区域/图层，针对 11.1 这类「文字在动底片上淡出」）。

    文字消失 = 高频能量单调掉下去并保持在低位；运镜不掉（细节跟着走）；爆炸 / 闪白
    也会掉，但 0.4s 内会回来（persist 挡掉）。
    """
    i0, i1 = case.at(t0), case.at(t1)
    if i1 - i0 < 3:
        return None
    hf = np.asarray([hf_energy(case.grays[k]) for k in range(i0, i1 + 1)])
    base = float(hf[0]) + 1e-6
    drop = 1.0 - float(hf[-1]) / base
    steps = np.diff(hf) / base
    down = float((steps <= 1e-4).mean())
    j = min(len(case.grays) - 1, case.at(t1 + 0.40))
    persist = 1.0 - float(case.hf[j]) / base
    return {"hf_drop": drop, "hf_mono": down, "hf_persist": persist}


def identity_stats(case, t0, t1):
    """I 族（场景身份，针对 14.2 这类两个都在动的镜头叠化）。

    叠化中点「两边都不像」：中间帧与首帧、尾帧的最佳平移 NCC 都低；运镜的中间帧
    跟首帧（补偿平移后）仍然像；硬切的中间帧就是某一端（像）。
    要求低身份覆盖窗口里的大部分帧（闪白只低两三帧）。
    """
    i0, i1 = case.at(t0), case.at(t1)
    if i1 - i0 < 4:
        return None
    ks = np.linspace(i0 + 1, i1 - 1, min(5, i1 - i0 - 1)).round().astype(int)
    scores = []
    for k in ks:
        a = float(case.best_shift_ncc(int(k), i0).mean())
        b = float(case.best_shift_ncc(int(k), i1).mean())
        scores.append(max(a, b))
    scores = np.asarray(scores)
    mid = float(scores[len(scores) // 2])
    return {"id_mid": mid, "id_low_frac": float((scores < 0.60).mean()),
            "id_min": float(scores.min())}


def pair_stats(case, t0, t1):
    """现有逐对统计的窗口汇总，用来对照「整幅统计分不开」。"""
    i0, i1 = case.at(t0), case.at(t1)
    hsv, energy, cf = [], [], []
    for k in range(i0 + 1, i1 + 1):
        a, b = case.grays[k - 1], case.grays[k]
        ra = np.repeat(a[..., None], 3, axis=2).astype(np.uint8)
        rb = np.repeat(b[..., None], 3, axis=2).astype(np.uint8)
        hsv.append(float(g.hsv_score(g.rgb_to_hsv(g.grid_rgb(ra)),
                                     g.rgb_to_hsv(g.grid_rgb(rb)))))
        energy.append(float(np.abs(b - a).mean()))
        cf.append(float((np.abs(b - a) > 12).mean()))
    return {"hsv_med": float(np.median(hsv)) if hsv else 0.0,
            "energy_med": float(np.median(energy)) if energy else 0.0,
            "cf_max": float(max(cf)) if cf else 0.0}


def truth_changes(name):
    path = ROOT / "eval" / "truth_long.json"
    truth = json.load(open(path, encoding="utf-8"))["cases"]
    return [float(c["t_s"]) for c in truth.get(name, {}).get("truth_changes", [])]


def windows(case, changes):
    out = []
    for t0, t1, label in POS.get(case.name, []):
        out.append({"t0": t0, "t1": t1, "kind": "pos", "label": label})
    for ln in NEG_LENS:
        t = 0.10
        while t + ln <= case.total - 0.10:
            a, b = t, t + ln
            blocked = any(a - GUARD <= c <= b + GUARD for c in changes)
            is_pos = any(abs(a - p["t0"]) < 0.05 and abs(b - p["t1"]) < 0.05 for p in out)
            if not blocked and not is_pos:
                out.append({"t0": a, "t1": b, "kind": "neg", "label": "len%.2f" % ln})
            t += STRIDE
    for c in changes:
        out.append({"t0": max(0.0, c - 0.30), "t1": min(case.total, c + 0.30),
                    "kind": "cut", "label": "真值 %.2fs" % c})
    return out


def dump_map(ncc, label):
    print("    格内最佳平移 NCC（行=上→下，列=左→右；.. <0.55 低分）  %s" % label)
    for y in range(NY):
        print("    " + " ".join("%4.2f" % v if v >= LOW_NCC else " .. " for v in ncc[y]))


def fmt_row(r):
    return ("%-8s %5.2fs %5.2fs %-4s %-14s low %5.3f clus %4.2f stab %4.2f "
            "bg %5.2f drop %5.3f | blend %5.2f cover %4.2f mono %4.2f") % (
        r["case"], r["t0"], r["t1"], r["kind"], r["label"][:14],
        r["low_frac"], r["low_cluster"], r["low_stable"], r["bg_ncc"], r["std_drop"],
        r["blend_resid"], r["blend_cover"], r["alpha_mono"])


def verdict(rows, key, higher_is_better=True, field=None):
    pos = [r[field or key] for r in rows if r["kind"] == "pos"]
    neg = [r[field or key] for r in rows if r["kind"] == "neg"]
    if not pos or not neg:
        return None
    pm, nm = min(pos), max(neg) if higher_is_better else min(neg)
    if higher_is_better:
        gap = (pm - nm) / max(abs(nm), 1e-6)
        ok = pm > nm and gap >= MARGIN_OK
    else:
        gap = (nm - pm) / max(abs(nm), 1e-6)
        ok = pm < nm and gap >= MARGIN_OK
    return {"pos_min": round(pm, 4), "neg_extreme": round(nm, 4),
            "gap": round(gap, 3), "ok": bool(ok),
            "pos": [round(x, 4) for x in pos]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="溶解漏点可分性测量（只测量，不改判据）")
    ap.add_argument("--case", action="append", help="只测指定 case（可重复）")
    ap.add_argument("--dump", action="store_true", help="正样本额外打格子图")
    ap.add_argument("--out", default=str(ROOT / "runs" / "measure_dissolve"))
    a = ap.parse_args(argv)

    names = a.case or list(CLIPS)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    rows, notes = [], {}
    for name in names:
        path = CLIPS[name]
        if not path.exists():
            print("跳过：素材不在 %s" % path)
            continue
        t0 = time.time()
        case = Case(name, path)
        changes = truth_changes(name)
        print("== %s  %d 帧 / %.2fs / %s  (读入 %.1fs)" % (
            name, case.n, case.total, "%dx%d" % (case.info["width"], case.info["height"]),
            time.time() - t0))
        for w in windows(case, changes):
            r = region_stats(case, w["t0"], w["t1"])
            b = blend_stats(case, w["t0"], w["t1"])
            h = hist_stats(case, w["t0"], w["t1"])
            d = detail_stats(case, w["t0"], w["t1"])
            tx = text_stats(case, w["t0"], w["t1"])
            ident = identity_stats(case, w["t0"], w["t1"])
            if not r or not b or not h or not d or not tx or not ident:
                continue
            row = {"case": name, "t0": w["t0"], "t1": w["t1"],
                   "kind": w["kind"], "label": w["label"]}
            row.update({k: v for k, v in r.items() if k != "ncc_map"})
            row.update({k: v for k, v in b.items() if k != "n_frames"})
            row.update(h)
            row.update(d)
            row.update(tx)
            row.update(ident)
            row.update(pair_stats(case, w["t0"], w["t1"]))
            row["n_frames"] = b["n_frames"]
            rows.append(row)
            if a.dump and w["kind"] == "pos":
                dump_map(r["ncc_map"], "%s %.2f-%.2f" % (name, w["t0"], w["t1"]))
        print("   窗口 %d 个，用时 %.1fs" % (
            sum(1 for r in rows if r["case"] == name), time.time() - t0))

    print("\n%-8s %-10s %-10s %-4s %-14s %s" % (
        "case", "t0", "t1", "kind", "label", "统计"))
    for r in rows:
        print(fmt_row(r))

    print("\n== 预登记判据（正样本要压过所有负样本，余量 >= %.0f%%）" % (MARGIN_OK * 100))
    checks = {
        "R 区域面积 low_frac": ("low_frac", True),
        "R 连通性 low_cluster": ("low_cluster", True),
        "R 持久 low_stable": ("low_stable", True),
        "R 标准差塌陷 std_drop": ("std_drop", True),
        "R 背景仍匹配 bg_ncc": ("bg_ncc", True),
        "B 混合残差 blend_resid": ("blend_resid", False),
        "B 混合覆盖 blend_cover": ("blend_cover", True),
        "B α 单调 alpha_mono": ("alpha_mono", True),
    }
    summary = {}
    for label, (field, better) in checks.items():
        v = verdict(rows, field, better)
        if v is None:
            continue
        summary[field] = v
        print("  %-22s 正样本 %s | 最强负样本 %.3f | 余量 %+.0f%% | %s" % (
            label, v["pos"], v["neg_extreme"], v["gap"] * 100,
            "可分" if v["ok"] else "不可分"))

    # 组合规则：区域族（面积+连通+持久）与混合族（残差+覆盖+单调）各要全过
    r_ok = all(summary.get(f, {}).get("ok") for f in ("low_frac", "low_cluster", "low_stable", "std_drop"))
    b_ok = all(summary.get(f, {}).get("ok") for f in ("blend_resid", "blend_cover", "alpha_mono"))
    print("\n结论：区域族 %s；混合族 %s" % ("可分" if r_ok else "不可分", "可分" if b_ok else "不可分"))

    # H / D 两族：单字段条件不要求过预登记阈值，直接看「组合规则命中数」
    print("\n== H / D 两族（探索性阈值，只统计命中）")

    def h_fire(r):
        return (r["hist_sep"] >= H_SEP and r["hist_resid"] <= H_RESID
                and r["hist_cover"] >= H_COVER and r["hist_mono"] >= H_MONO)

    def d_fire(r):
        return (r["detail_dip"] <= D_DIP
                and D_AT[0] <= r["detail_at"] <= D_AT[1])

    def t_fire(r):
        return (r["hf_drop"] >= T_DROP and r["hf_mono"] >= T_MONO
                and r["hf_persist"] >= T_PERSIST)

    def i_fire(r):
        return r["id_low_frac"] >= I_LOW and r["id_mid"] <= I_MID

    hits = {}
    for tag, fn in (("H 直方图轨迹", h_fire), ("D 细节塌陷", d_fire),
                    ("T 高频文字层", t_fire), ("I 场景身份", i_fire)):
        pos = [r for r in rows if r["kind"] == "pos"]
        neg = [r for r in rows if r["kind"] == "neg"]
        cut = [r for r in rows if r["kind"] == "cut"]
        hits[tag] = {"pos": sum(1 for r in pos if fn(r)), "pos_n": len(pos),
                     "neg": sum(1 for r in neg if fn(r)), "neg_n": len(neg),
                     "cut": sum(1 for r in cut if fn(r)), "cut_n": len(cut)}
        print("  %-12s 正样本 %d/%d | 负样本误报 %d/%d | 真值窗口 %d/%d" % (
            tag, hits[tag]["pos"], hits[tag]["pos_n"], hits[tag]["neg"],
            hits[tag]["neg_n"], hits[tag]["cut"], hits[tag]["cut_n"]))
    for r in rows:
        if r["kind"] == "pos":
            print("    pos %5.2f-%5.2f H sep %.3f resid %.2f cov %.2f mono %.2f | "
                  "D dip %.2f at %.2f | T drop %.2f mono %.2f persist %.2f | "
                  "I mid %.2f low %.2f" % (
                      r["t0"], r["t1"], r["hist_sep"], r["hist_resid"],
                      r["hist_cover"], r["hist_mono"], r["detail_dip"], r["detail_at"],
                      r["hf_drop"], r["hf_mono"], r["hf_persist"],
                      r["id_mid"], r["id_low_frac"]))
    h_ok = hits["H 直方图轨迹"]["pos"] == hits["H 直方图轨迹"]["pos_n"] and hits["H 直方图轨迹"]["neg"] == 0
    d_ok = hits["D 细节塌陷"]["pos"] == hits["D 细节塌陷"]["pos_n"] and hits["D 细节塌陷"]["neg"] == 0
    t_ok = hits["T 高频文字层"]["pos"] == hits["T 高频文字层"]["pos_n"] and hits["T 高频文字层"]["neg"] == 0
    i_ok = hits["I 场景身份"]["pos"] == hits["I 场景身份"]["pos_n"] and hits["I 场景身份"]["neg"] == 0
    print("  H %s；D %s；T %s；I %s" % (
        "可分" if h_ok else "不可分", "可分" if d_ok else "不可分",
        "可分" if t_ok else "不可分", "可分" if i_ok else "不可分"))

    (outdir / "measure.json").write_text(json.dumps(
        {"rows": rows, "summary": summary, "r_ok": r_ok, "b_ok": b_ok,
         "hits": hits, "h_ok": h_ok, "d_ok": d_ok, "t_ok": t_ok, "i_ok": i_ok},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "summary.txt").write_text(
        "\n".join(fmt_row(r) for r in rows), encoding="utf-8")
    print("产物：%s" % outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
