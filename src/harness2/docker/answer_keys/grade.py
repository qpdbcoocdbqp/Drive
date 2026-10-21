"""Grade local_eval outputs against the answer keys.

usage: python -I answer_keys/grade.py runs/local_eval
Each check is one rubric criterion; numbers match within 0.01 (0.05 for totals).
"""

import csv
import re
import sys
from pathlib import Path

RUNS = Path(sys.argv[1])
KEYS = Path(__file__).resolve().parent / "local_eval"


def key(name):
    return (KEYS / f"{name}.md").read_text(encoding="utf-8")


def text(p):
    try:
        return p.read_text(encoding="utf-8", errors="replace").replace(",", "")
    except OSError:
        return ""


def nums(s):
    return [float(x) for x in re.findall(r"-?\d+\.\d+", s)]


def has(s, v, tol=0.011):
    return any(abs(x - v) <= tol for x in nums(s))


def rows(p):
    try:
        with p.open(encoding="utf-8", errors="replace") as f:
            return [{k.strip().lower(): (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(f)]
    except (OSError, csv.Error):
        return []


def num(v):
    try:
        return float(str(v).replace(",", "").replace("$", ""))
    except ValueError:
        return None


results = {}


def check(task, weight, label, ok):
    results.setdefault(task, []).append((weight, label, bool(ok)))


# ---------------------------------------------------------------- t1
k = key("t1_easy")
out = RUNS / "t1_easy" / "output"
rep = text(out / "report.md")
total = float(re.search(r"Overall: ([\d.]+)", k).group(1))
check("t1_easy", 10, f"overall {total}", has(rep, total))
for wh, v in re.findall(r"(East|North|South) ([\d.]+)", k.split("Per warehouse:")[1].split("\n")[0]):
    check("t1_easy", 8 / 3, f"{wh} {v}", re.search(wh + r"\D{0,40}" + re.escape(v), rep))
below = re.search(r"Below reorder point: ([^(]+)", k).group(1).strip().split(", ")
trap = re.search(r"trap: (SKU-\d+)", k).group(1)
found = re.findall(r"SKU-\d+", rep)
check("t1_easy", 6, f"below list {below}", all(s in rep for s in below))
sec = rep.lower().split("reorder", 1)[-1][:1500]
check("t1_easy", 2, f"excludes {trap} (qty == reorder point)", rep and trap not in re.findall(r"SKU-\d+", sec.upper().split("HIGHEST")[0]))
top = re.search(r"Top line: (SKU-\d+) .* ([\d.]+)$", k, re.M)
check("t1_easy", 4, f"top {top.group(1)} {top.group(2)}", top.group(1) in rep and has(rep, float(top.group(2))))

# ---------------------------------------------------------------- t2
k = key("t2_medium")
out = RUNS / "t2_medium" / "output"
summ = text(out / "summary.md")
total = float(re.search(r"Total: ([\d.]+)", k).group(1))
check("t2_medium", 10, f"total {total}", has(summ, total, 0.05))
got = {}
for r in rows(out / "revenue.csv"):
    rg = (r.get("region") or "").strip().title()
    got[(rg, (r.get("month") or "")[:7])] = num(r.get("revenue_usd", ""))
cells = re.findall(r"(East|North|South|West) (2026-\d\d) ([\d.]+)", k.split("Cells:")[1].split("\n")[0])
for rg, m, v in cells:
    g = got.get((rg, m))
    check("t2_medium", 10 / len(cells), f"{rg} {m} {v} (got {g})", g is not None and abs(g - float(v)) <= 0.011)
check("t2_medium", 0, f"no extra region/month rows (got {len(got)}, want {len(cells)})", len(got) == len(cells))
top = re.search(r"top (\w+)", k).group(1)
check("t2_medium", 4, f"top region {top}", re.search(r"(highest|top)[^\n]{0,80}" + top, summ, re.I) or re.search(top + r"[^\n]{0,80}(highest|top)", summ, re.I))
check("t2_medium", 3, "2 duplicate rows removed", re.search(r"\b2\b[^\n]{0,40}duplicate|duplicate[^\n]{0,60}\b2\b", summ, re.I))
miss = re.search(r"missing customer\): (ORD-\d+)", k).group(1)
check("t2_medium", 3, f"excluded {miss} (unknown customer)", miss in summ)

# ---------------------------------------------------------------- t3
k = key("t3_hard")
out = RUNS / "t3_hard" / "output"
want = {(r, m): tuple(map(float, (g, c, n))) for r, m, g, c, n in
        re.findall(r"- (R\d) (2026-\d\d): gross ([-\d.]+), clawback ([-\d.]+), net ([-\d.]+)", k)}
got = {}
for r in rows(out / "commissions.csv"):
    got[(r.get("rep_id", ""), r.get("month", "")[:7])] = tuple(
        num(r.get(c, "")) for c in ("gross_commission", "clawback", "net_commission"))
total = float(re.search(r"Total net: ([-\d.]+)", k).group(1))
gt = sum((v[2] or 0) for v in got.values() if v and v[2] is not None)
check("t3_hard", 8, f"total net {total} (got {gt:.2f})", abs(gt - total) <= 0.05)
for kk, (g, c, n) in sorted(want.items()):
    v = got.get(kk)
    ok = v is not None and all(x is not None and abs(abs(x) - abs(y)) <= 0.011 for x, y in zip(v, (g, c, n)))
    check("t3_hard", 14 / len(want), f"{kk[0]} {kk[1]} gross {g} claw {c} net {n} (got {v})", ok)
dis = {(r.get("rep_id", ""), r.get("month", "")[:7]): num(r.get("difference", "")) for r in rows(out / "discrepancies.csv")}
wd = {(r, m): float(d) for r, m, d in re.findall(r"- (R\d) (2026-\d\d): computed .*difference ([-\d.]+)", k)}
for kk, d in wd.items():
    g = dis.get(kk)
    check("t3_hard", 10 / (len(wd) + 1), f"discrepancy {kk} {d} (got {g})", g is not None and abs(g - d) <= 0.011)
check("t3_hard", 10 / (len(wd) + 1), f"no extra discrepancy rows (got {sorted(dis)})", set(dis) == set(wd))
memo = text(out / "audit_memo.md")
nc = re.findall(r"(D-\d+) outside 60 days", k)
check("t3_hard", 4, f"memo names no-clawback refunds {nc}", all(d in memo for d in nc))

# ---------------------------------------------------------------- report
for task, cs in results.items():
    got_w = sum(w for w, _, ok in cs if ok)
    all_w = sum(w for w, _, _ in cs)
    print(f"\n## {task}: {got_w:.1f}/{all_w:.1f} ({100 * got_w / all_w:.0f}%)")
    for w, label, ok in cs:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
