#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""给评测打分：读图方的 JSON 对上 truth.json。

用法
    python tools/score.py runs/eval/truth.json runs/eval/answers_agent.json [--tol 1.0]

读图方 JSON 形如 {case: {"count": n, "changes": [{"t_s": 2.5, "what": "..."}]}}
打分：次数是否一致、每条真值变化能否在 ±tol 秒内找到对上的（贪心一对一）。
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def match(truth, ans, tol):
    """贪心一对一匹配，返回 (命中数, 漏掉的真值, 多报的答案)。"""
    rest = list(range(len(ans)))
    hit, miss = [], []
    for t in truth:
        best, best_d = None, None
        for i in rest:
            d = abs((ans[i].get("t_s") or 0) - (t.get("t_s") or 0))
            if d <= tol and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is None:
            miss.append(t)
        else:
            hit.append((t, ans[best]))
            rest.remove(best)
    return hit, miss, [ans[i] for i in rest]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("truth")
    ap.add_argument("answers")
    ap.add_argument("--tol", type=float, default=1.0)
    ap.add_argument("--label", default="读图方")
    a = ap.parse_args()

    truth = json.load(open(a.truth, encoding="utf-8"))["cases"]
    ans = json.load(open(a.answers, encoding="utf-8"))

    rows, matched_total, truth_total = [], 0, 0
    for case, t in truth.items():
        if case not in ans:
            rows.append((case, t["truth_count"], "—", "缺答"))
            truth_total += t["truth_count"]
            continue
        a_case = ans[case]
        got = a_case.get("changes") or []
        hit, miss, extra = match(t.get("truth_changes") or [], got, a.tol)
        matched_total += len(hit)
        truth_total += t["truth_count"]
        count_ok = "✓" if len(got) == t["truth_count"] else "✗"
        detail = "命中%d/%d" % (len(hit), t["truth_count"])
        if miss:
            detail += "；漏：" + "、".join("%.1fs %s" % (m.get("t_s") or 0, m.get("kind")) for m in miss)
        if extra:
            detail += "；多：" + "、".join("%.1fs" % (e.get("t_s") or 0) for e in extra)
        rows.append((case, t["truth_count"], "%d %s" % (len(got), count_ok), detail))

    print("\n%-11s %-6s %-8s %s" % ("case", "真值", "读图方", "对照"))
    for r in rows:
        print("%-11s %-6s %-8s %s" % r)

    print("\n%s：变化命中 %d/%d（±%.1fs）；次数完全一致 %d/%d" % (
        a.label, matched_total, truth_total, a.tol,
        sum(1 for r in rows if "✗" not in str(r[2])), len(truth)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
