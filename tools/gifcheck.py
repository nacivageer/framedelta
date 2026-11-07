#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""12 个 GIF 例图的结构回归：假 jump、代表帧重复、时间覆盖与 beat 覆盖。

真值在 eval/gifs/truth.json（全帧结论来自识图库第 3 条 + 抽帧复核）。
不约读图方、不花 token；只量「产物自己有没有硬伤」：

  - jumps 数与 expected_jumps 的差（快动作被当硬切）
  - 代表帧两两 NCC ≥ same_picture_ncc 的重复对
  - 首/末覆盖与最大空档（末帧同图时不算漏）
  - beats 里每个时刻 ±tol 内有没有代表帧
  - budget.images 与 budget.k

用法
    python tools/gifcheck.py                    # 有产物就复用，没有就跑
    python tools/gifcheck.py --refresh          # 12 个全部重跑（约 45s）
    python tools/gifcheck.py --strict           # 有硬伤时退出码 1（给以后接 CI）
"""
import argparse
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import gif_digest as g  # noqa: E402

DEFAULT_TRUTH = os.path.join(ROOT, "eval", "gifs", "truth.json")
DEFAULT_RUNS = os.path.join(ROOT, "runs", "gifs")


def load_truth(path):
    t = json.load(open(path, encoding="utf-8"))
    base = t.get("gif_dir") or ""
    return t, base


def run_one(src, out):
    out_abs = os.path.join(ROOT, out) if not os.path.isabs(out) else out
    cmd = [sys.executable, os.path.join(ROOT, "gif_digest.py"), src,
           "--out", out_abs, "--quiet"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise RuntimeError("gif_digest 失败 rc=%d：%s" % (p.returncode, (p.stderr or "")[-300:]))
    return os.path.join(out_abs, "digest.json")


def ncc_pairs(frames, kfs):
    keys = [int(k["frame"]) for k in kfs]
    grids = [g.grid_rgb(frames[i]) for i in keys]
    pairs = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            pairs.append((float(g.grid_ncc(grids[i], grids[j])), keys[i], keys[j]))
    pairs.sort(reverse=True)
    return pairs


def ncc_to(frames, kfs, frame):
    a = g.grid_rgb(frames[frame])
    vals = [float(g.grid_ncc(a, g.grid_rgb(frames[int(k["frame"])]))) for k in kfs]
    return max(vals) if vals else 0.0


def check_case(case, gif_dir, runs_dir, same_ncc, default_tail_tol, refresh):
    stem = case["case"]
    src = os.path.join(gif_dir, case["file"]) if gif_dir else case["file"]
    out = os.path.join(runs_dir, stem)
    dpath = os.path.join(out, "digest.json")
    if refresh or not os.path.exists(dpath):
        dpath = run_one(src, out)
    dg = json.load(open(dpath, encoding="utf-8"))
    kfs = dg.get("key_frames") or []
    ktimes = [float(k["t_ms"]) for k in kfs]
    dur = float(dg.get("meta", {}).get("duration_ms") or case.get("duration_ms") or 0)
    budget = dg.get("budget") or {}
    jumps = dg.get("jumps") or []

    exp_j = int(case.get("expected_jumps", 0))
    false_j = max(0, len(jumps) - exp_j)
    missed_j = max(0, exp_j - len(jumps))

    frames, _, _ = g.load_frames(g.Path(src), None)
    pairs = ncc_pairs(frames, kfs)
    dups = [p for p in pairs if p[0] >= same_ncc]
    max_ncc = pairs[0][0] if pairs else 0.0

    # 末帧：同图不算漏；给出「选中的末帧」与终点差
    tail_gap = dur - ktimes[-1] if ktimes else dur
    last_same = ncc_to(frames, kfs, len(frames) - 1) if (kfs and frames) else 1.0
    tail_tol = float(case.get("tail_tol_ms", default_tail_tol))
    tail_ok = tail_gap <= tail_tol or last_same >= same_ncc

    gaps = [ktimes[i + 1] - ktimes[i] for i in range(len(ktimes) - 1)]
    max_gap = max(gaps) if gaps else dur
    first_gap = ktimes[0] if ktimes else dur

    beats = case.get("beats") or []
    covered, beat_rows = 0, []
    for b in beats:
        t = float(b["t_ms"])
        tol = float(b.get("tol_ms", 700))
        if ktimes:
            nearest = min(ktimes, key=lambda x: abs(x - t))
            d = abs(nearest - t)
            ok = d <= tol
        else:
            nearest, d, ok = None, float("inf"), False
        covered += 1 if ok else 0
        beat_rows.append((b.get("name", ""), t, nearest, d, ok))

    hard = missed_j > 0 or bool(dups) or not tail_ok
    return {
        "case": stem, "file": case["file"], "duration_ms": dur,
        "jumps": len(jumps), "expected_jumps": exp_j,
        "false_jumps": false_j, "missed_jumps": missed_j,
        "jump_ok": false_j == 0 and missed_j == 0,
        "images": len(kfs), "k": budget.get("k"), "budget_images": budget.get("images"),
        "max_ncc": round(max_ncc, 3), "dup_pairs": [(round(p[0], 3), p[1], p[2]) for p in dups],
        "first_ms": round(ktimes[0], 1) if ktimes else None,
        "last_ms": round(ktimes[-1], 1) if ktimes else None,
        "tail_gap_ms": round(tail_gap, 1), "tail_same": round(last_same, 3),
        "tail_ok": bool(tail_ok), "tail_tol_ms": tail_tol,
        "max_gap_ms": round(max_gap, 1), "head_gap_ms": round(first_gap, 1),
        "beats": [{"name": n, "t_ms": t, "nearest_ms": (round(nn, 1) if nn is not None else None),
                   "delta_ms": (round(d, 1) if d != float("inf") else None), "ok": ok}
                  for n, t, nn, d, ok in beat_rows],
        "beat_covered": covered, "beat_total": len(beats),
        "hard": bool(hard),
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default=DEFAULT_TRUTH)
    ap.add_argument("--runs", default=DEFAULT_RUNS)
    ap.add_argument("--gif-dir", default=None)
    ap.add_argument("--refresh", action="store_true", help="12 个全部重跑（约 45s）")
    ap.add_argument("--strict", action="store_true",
                    help="结构有硬伤（重复格/尾空/漏 jump）时返回 1")
    ap.add_argument("--strict-jumps", action="store_true",
                    help="把假 jump 也当失败（当前 5 段已知，先不接 CI）")
    ap.add_argument("--json", default=os.path.join(ROOT, "runs", "_gifcheck", "report.json"))
    a = ap.parse_args(argv)

    truth, base = load_truth(a.truth)
    gif_dir = a.gif_dir or base
    same_ncc = float(truth.get("same_picture_ncc", 0.90))
    tail_tol = float(truth.get("default_tail_tol_ms", 700))
    if not os.path.isdir(gif_dir):
        print("GIF 目录不存在：%s" % gif_dir, file=sys.stderr)
        return 2

    rows = []
    for case in truth["cases"]:
        try:
            rows.append(check_case(case, gif_dir, a.runs, same_ncc, tail_tol, a.refresh))
        except Exception as e:
            print("FAIL %s：%s" % (case.get("case"), e), file=sys.stderr)
            rows.append({"case": case.get("case"), "hard": True, "error": str(e),
                         "false_jumps": 0, "dup_pairs": [], "beat_covered": 0,
                         "beat_total": len(case.get("beats") or []), "images": 0, "k": 0})

    print("%-34s %4s %4s %4s %4s %5s %6s %6s %6s  %s"
          % ("case", "jump", "exp", "dup", "img", "beat", "first", "last", "tail", "verdict"))
    bad = 0
    for r in rows:
        if r.get("error"):
            print("%-34s  ERROR %s" % (r["case"], r["error"]))
            bad += 1
            continue
        flags = []
        if r["false_jumps"]:
            flags.append("假jump%d" % r["false_jumps"])
        if r["missed_jumps"]:
            flags.append("漏jump%d" % r["missed_jumps"])
        if r["dup_pairs"]:
            flags.append("重复%d" % len(r["dup_pairs"]))
        if not r["tail_ok"]:
            flags.append("尾空%.0fms" % r["tail_gap_ms"])
        if r["beat_covered"] < r["beat_total"]:
            flags.append("beat%d/%d" % (r["beat_covered"], r["beat_total"]))
        verdict = "ok" if not flags else " ".join(flags)
        if r["hard"]:
            bad += 1
        print("%-34s %4d %4d %4d %4d %2d/%-2d %6.0f %6.0f %6.0f  %s"
              % (r["case"][:34], r["jumps"], r["expected_jumps"], len(r["dup_pairs"]),
                 r["images"], r["beat_covered"], r["beat_total"],
                 r["first_ms"] or 0, r["last_ms"] or 0, r["tail_gap_ms"], verdict))

    total_j = sum(r.get("jumps", 0) for r in rows)
    total_false = sum(r.get("false_jumps", 0) for r in rows)
    total_dups = sum(len(r.get("dup_pairs") or []) for r in rows)
    total_beats = sum(r.get("beat_total", 0) for r in rows)
    total_beat_ok = sum(r.get("beat_covered", 0) for r in rows)
    tail_bad = sum(1 for r in rows if not r.get("tail_ok", True) and not r.get("error"))
    jump_bad = sum(1 for r in rows if not r.get("jump_ok", True))
    print("\n汇总：jumps %d（假 %d，%d 段）/ 重复对 %d / beat %d/%d / 尾空 %d 段 / 硬伤 %d 段"
          % (total_j, total_false, jump_bad, total_dups, total_beat_ok, total_beats,
             tail_bad, bad))

    os.makedirs(os.path.dirname(a.json), exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as f:
        json.dump({"truth": a.truth, "gif_dir": gif_dir, "same_picture_ncc": same_ncc,
                   "rows": rows}, f, ensure_ascii=False, indent=2)
    print("报告：%s" % a.json)
    return 1 if ((a.strict and bad) or (a.strict_jumps and jump_bad)) else 0


if __name__ == "__main__":
    sys.exit(main())
