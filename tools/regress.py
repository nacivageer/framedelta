#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""framedelta 回归：三个已知答案的素材 + 规模素材，断言产物与口径。

用法
    python tools/regress.py             # 全跑（含两段长素材，约 30s）
    python tools/regress.py --quick     # 只跑两个「已知答案」短素材
    python tools/regress.py --repeat    # 额外验证同一输入两次跑结果一致

判定
    FAIL  → 已知答案被改坏（决策记录 92–94 行那组数字）
    DRIFT → 规模素材的计数偏离基线 ±15%（可能是改进，也可能是退化，需要人看）
    退出码：0 全过；1 有 FAIL 或 DRIFT
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "gif_digest.py")
OUT_ROOT = os.path.join(ROOT, "runs", "_regress")
# 素材在 D 盘桌面（本机桌面被重定向）；找不到再退回用户目录
DESKTOP = next((p for p in ("D:\\Desktop", os.path.join(os.path.expanduser("~"), "Desktop"))
                if os.path.isdir(p)), "D:\\Desktop")

# 已知答案：数字来自决策记录「怎么查」一节（92–93 行）
KNOWN = [
    {
        "name": "eye6",
        "src": os.path.join(DESKTOP, "9150A82B79FAF0B206927E5712D5EEF3.gif"),
        "args": [],
        "strict": {
            "meta.frames": 6,
            "meta.duration_ms": 480,
            "meta.repeat_frames": 0,
            "counts.jumps": 0,
            "counts.subtle": 5,
            "counts.swaps": 0,
            "counts.drifts": 0,
            "len.transitions": 0,
            "len.shots": 1,
        },
        "max": {"local_mag_max": 0.1},  # 瞳孔是重画不是平移
        "why": "6 帧瞳孔：0 重复帧、0 突变、5 对 subtle、局部位移 < 0.1px",
    },
    {
        "name": "clip74",
        "src": os.path.join(DESKTOP, "88A91A881CFD41EC41B0C97A349ECC76.gif"),
        "args": [],
        "strict": {
            "meta.frames": 74,
            "meta.duration_ms": 2460,
            "meta.repeat_frames": 25,
            "counts.jumps": 16,
            "counts.still": 25,
            "counts.swaps": 0,
            "counts.drifts": 0,
            "len.transitions": 3,
            "len.shots": 4,
        },
        "why": "74 帧火焰：25 重复帧、16 jump、3 次转场、4 个镜头",
    },
]

# 规模素材：只锁基线 ±15%，防「改一处炸一片」；数字本身不是真理
# 基线冻结：2026-11-07（含尖峰规则版本）。旧 runs/ 里那两份是尖峰规则之前跑的，不能当基线。
SCALE = [
    {
        "name": "qiushui",
        "src": os.path.join(DESKTOP, "秋水传送bug.gif"),
        "args": [],
        "base": {"meta.frames": 192, "counts.jumps": 6, "len.transitions": 3, "len.shots": 3,
                 "counts.swaps": 0, "counts.drifts": 0},
    },
    {
        "name": "qingxiao",
        "src": os.path.join(DESKTOP, "清宵bug.gif"),
        "args": [],
        "base": {"meta.frames": 312, "counts.jumps": 32, "len.transitions": 15, "len.shots": 16,
                 "counts.swaps": 0, "counts.drifts": 1},
    },
]

VALID_KINDS = {"still", "subtle", "motion", "jump"}
JUMP_FIELDS = ("frame", "t_ms", "hsv_score", "ncc", "energy", "changed_frac", "coherence", "resid_px")


def get(obj, dotted):
    """'counts.jumps' / 'len.shots' 取值。"""
    if dotted.startswith("len."):
        return len(obj.get(dotted[4:]) or [])
    cur = obj
    for part in dotted.split("."):
        cur = (cur or {}).get(part)
    return cur


def run_case(case, extra_digest=False):
    out = os.path.join(OUT_ROOT, case["name"])
    if os.path.isdir(out):
        shutil.rmtree(out)
    cmd = [sys.executable, TOOL, case["src"], "--out", out, "--quiet"] + case["args"]
    t0 = time.time()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    secs = time.time() - t0
    if proc.returncode != 0:
        return None, "gif_digest 失败(rc=%d)：%s" % (proc.returncode, (proc.stderr or "")[-400:]), secs
    digest_path = os.path.join(out, "digest.json")
    if not os.path.exists(digest_path):
        return None, "没有 digest.json", secs
    d = json.load(open(digest_path, encoding="utf-8"))
    d["_out"] = out
    return d, None, secs


def structural(d):
    """产物是否还是「可读的变化描述」而不是一堆表格。返回问题列表。"""
    bad = []
    if d.get("schema") != "framedelta/digest@1":
        bad.append("schema 变了：%r" % d.get("schema"))
    out = d["_out"]
    for art in ("meta.json", "sheet.png", "flow.csv", "kymograph.png", "annotated.png", "digest.json"):
        p = os.path.join(out, art)
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            bad.append("产物缺失或为空：%s" % art)
    meta = d.get("meta") or {}
    # 口径字段在 meta.json（不是 digest.json 的 meta 摘要）
    mfile = {}
    if os.path.exists(os.path.join(out, "meta.json")):
        try:
            mfile = json.load(open(os.path.join(out, "meta.json"), encoding="utf-8"))
        except Exception as e:
            bad.append("meta.json 解析失败：%s" % e)
    if not mfile.get("repeat_definition"):
        bad.append("meta.json 缺 repeat_definition（重复帧口径不可复现）")
    if not mfile.get("duration_definition") and mfile.get("zero_duration_frames"):
        bad.append("meta.json 缺 duration_definition（零时长帧口径不可复现）")
    if (meta.get("fps") or 0) <= 0:
        bad.append("meta.fps 非法：%r" % meta.get("fps"))
    pairs = (d.get("counts") or {}).get("pairs")
    if meta.get("frames") and pairs is not None and pairs != max(meta["frames"] - 1, 0):
        bad.append("counts.pairs=%r 与 frames=%r 不一致" % (pairs, meta["frames"]))
    for j in d.get("jumps") or []:
        miss = [f for f in JUMP_FIELDS if j.get(f) is None]
        if miss:
            bad.append("jump@%s 缺证据字段 %s" % (j.get("t_ms"), ",".join(miss)))
            break
    for iv in d.get("intervals") or []:
        if iv.get("kind") not in VALID_KINDS:
            bad.append("interval kind 非法：%r" % iv.get("kind"))
            break
        if (iv.get("end_ms") or 0) <= (iv.get("start_ms") or 0):
            bad.append("interval 时长非正：%s–%s" % (iv.get("start_ms"), iv.get("end_ms")))
            break
    kf = d.get("key_frames") or []
    if not (1 <= len(kf) <= 4):
        bad.append("代表帧数量 %d 不在 1–4" % len(kf))
    for k in kf:
        if k.get("t_ms") is None or not k.get("reason"):
            bad.append("代表帧缺 t_ms/reason：%r" % k)
            break
    shots = d.get("shots") or []
    frames = meta.get("frames")
    if not shots:
        bad.append("shots 为空")
    else:
        # 镜头之间允许有缝：缝里是突变成簇的转场区，工具不把它算进任何镜头
        if shots[0].get("start_frame") != 0:
            bad.append("shots 未从第 0 帧开始")
        prev_end = None
        for s in shots:
            if s.get("start_frame") is None or s.get("end_frame") is None:
                bad.append("shot 缺 start/end：%r" % s)
                break
            if s["end_frame"] < s["start_frame"]:
                bad.append("shot 起止颠倒：%r" % s)
                break
            if frames is not None and s["end_frame"] > frames - 1:
                bad.append("shot 超出帧数：end=%r frames=%r" % (s["end_frame"], frames))
                break
            if prev_end is not None and s["start_frame"] <= prev_end:
                bad.append("shots 重叠：%r→%r" % (prev_end, s["start_frame"]))
                break
            prev_end = s["end_frame"]
    annot = os.path.join(out, "annotated.png")
    if os.path.exists(annot) and os.path.getsize(annot) < 5000:
        bad.append("annotated.png 只有 %d 字节，图可能是空的" % os.path.getsize(annot))
    return bad


def check_exact(d, expect):
    bad = []
    for k, want in expect.items():
        got = get(d, k)
        if got != want:
            bad.append("%s 期望 %r，实得 %r" % (k, want, got))
    return bad


def check_tolerance(d, base, tol=0.15):
    drift = []
    for k, want in base.items():
        got = get(d, k)
        if got is None:
            drift.append("%s 取不到值" % k)
            continue
        if want == 0:
            if got != 0:
                drift.append("%s 基线 0，实得 %r" % (k, got))
            continue
        if abs(got - want) / want > tol:
            drift.append("%s 基线 %r，实得 %r（%+.0f%%）" % (k, want, got, 100 * (got - want) / want))
    return drift


def local_mag_max(d):
    """所有区间里最大的局部位移幅度（px）。"""
    vals = [iv.get("local_mag_mean") or 0 for iv in d.get("intervals") or []]
    return max(vals) if vals else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑已知答案短素材")
    ap.add_argument("--repeat", action="store_true", help="同一输入跑两次，比 digest 是否一致")
    a = ap.parse_args()

    cases = KNOWN + ([] if a.quick else SCALE)
    for c in cases:
        if not os.path.exists(c["src"]):
            print("素材不存在：%s（%s）" % (c["src"], c["name"]), file=sys.stderr)
            return 2

    os.makedirs(OUT_ROOT, exist_ok=True)
    rows, failed, drifted = [], 0, 0

    for c in cases:
        d, err, secs = run_case(c)
        if err:
            rows.append((c["name"], "FAIL", err, secs))
            failed += 1
            continue
        problems = structural(d)
        if "strict" in c:
            problems += check_exact(d, c["strict"])
            cap = c.get("max") or {}
            if "local_mag_max" in cap:
                got = local_mag_max(d)
                if got >= cap["local_mag_max"]:
                    problems.append("local_mag_max 期望 < %.2f，实得 %.3f" % (cap["local_mag_max"], got))
        if "base" in c:
            tol = check_tolerance(d, c["base"])
            if tol:
                drifted += 1
                problems += ["DRIFT " + t for t in tol]
        if a.repeat:
            d2, err2, _ = run_case(c, extra_digest=True)
            if err2:
                problems.append("第二次跑失败：%s" % err2)
            else:
                for key in ("meta", "counts", "jumps", "transitions", "shots", "key_frames"):
                    if d.get(key) != d2.get(key):
                        problems.append("两次结果不一致：%s" % key)
                        break
        hard = [p for p in problems if not p.startswith("DRIFT ")]
        soft = [p for p in problems if p.startswith("DRIFT ")]
        if hard:
            failed += 1
        status = "FAIL" if hard else ("DRIFT" if soft else "PASS")
        detail = "; ".join(hard + soft)
        rows.append((c["name"], status, detail, secs))

    print("\n%-10s %-6s %-6s %s" % ("case", "结果", "用时", "说明"))
    for name, status, detail, secs in rows:
        print("%-10s %-6s %5.1fs %s" % (name, status, secs, detail))

    summary = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "cases": len(rows),
        "failed": failed,
        "drifted": drifted,
        "rows": [{"case": n, "status": s, "detail": d, "secs": round(x, 2)} for n, s, d, x in rows],
    }
    with open(os.path.join(OUT_ROOT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    print("\n%d/%d 通过；drift %d。报告：%s" % (
        len(rows) - failed - drifted, len(rows), drifted, os.path.join(OUT_ROOT, "report.json")))
    return 0 if (failed == 0 and drifted == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
