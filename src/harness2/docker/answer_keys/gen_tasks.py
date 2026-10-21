"""Generate local_eval/{t1_easy,t2_medium,t3_hard} JobBench-format tasks with computed rubrics.

usage: python -I gen_tasks.py <tasks_root> <answer_key_dir>
Answer keys go outside tasks/ so they never reach the agent or the dataset copy.
"""

import csv
import json
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal as D
from pathlib import Path

ROOT = Path(sys.argv[1]) / "local_eval"
KEYS = Path(sys.argv[2])
C2 = D("0.01")


def r2(x: D) -> D:
    return x.quantize(C2, rounding=ROUND_HALF_UP)


def f2(x: D) -> str:
    return f"{r2(x):.2f}"


def write_csv(path: Path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_task(name, instructions, rubrics, card):
    d = ROOT / name
    (d / "task_folder").mkdir(parents=True, exist_ok=True)
    (d / "task_folder" / "TASK_INSTRUCTIONS.txt").write_text(instructions.strip() + "\n", encoding="utf-8")
    (d / "RUBRICS.json").write_text(json.dumps({"rubrics": rubrics}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if card:
        KEYS.mkdir(parents=True, exist_ok=True)
        (KEYS / f"{name}.md").write_text(card.strip() + "\n", encoding="utf-8")
    return d / "task_folder"


# ---------------------------------------------------------------- easy
def easy():
    rng = random.Random(11)
    names = ["Bolt M6", "Bolt M8", "Nut M6", "Nut M8", "Washer 6mm", "Washer 8mm", "Bracket L",
             "Bracket T", "Hinge 40", "Hinge 60", "Spring S", "Spring L", "Gasket A", "Gasket B",
             "Clamp 20", "Clamp 32", "Rivet 4", "Rivet 5", "Pin 3x20", "Pin 4x30"]
    rows = []
    for i, n in enumerate(names):
        wh = ["North", "South", "East"][i % 3]
        qty = rng.randint(0, 400)
        cost = D(rng.randint(5, 2500)) / 100
        rop = rng.choice([25, 50, 75, 100, 150])
        rows.append((f"SKU-{101 + i}", n, wh, qty, cost, rop))
    # guarantee an equality edge case (qty == reorder point must NOT count as below)
    s = rows[4]
    rows[4] = (s[0], s[1], s[2], 50, s[4], 50)
    tf = write_task("t1_easy", "", [], "")
    write_csv(tf / "inventory.csv", ["sku", "name", "warehouse", "qty", "unit_cost", "reorder_point"],
              [(a, b, c, q, f"{u:.2f}", r) for a, b, c, q, u, r in rows])

    by_wh = defaultdict(D)
    for _, _, wh, q, u, _ in rows:
        by_wh[wh] += q * u
    total = sum(by_wh.values(), D(0))
    below = sorted(sku for sku, _, _, q, _, r in rows if q < r)
    top = max(rows, key=lambda x: x[3] * x[4])
    eq = rows[4][0]

    instructions = f"""
You are the inventory controller preparing the month-end stock report.

inventory.csv in this folder lists every stock line: sku, name, warehouse, qty (units on hand),
unit_cost (USD per unit) and reorder_point (units).

Write output/report.md containing:
1. The inventory value (qty x unit_cost) for each warehouse and the overall total, in USD to two decimals.
2. The SKUs whose qty is strictly below their reorder_point, sorted by SKU.
3. The single stock line with the highest inventory value: its SKU, name and value.
"""
    rubrics = [
        {"rubric": f"Does the report give the correct overall inventory value of {f2(total)} USD?", "weight": 10,
         "criterion": [f"Overall total is {f2(total)}", "Value is computed as qty x unit_cost summed over all lines"]},
        {"rubric": "Does the report give the correct inventory value per warehouse?", "weight": 8,
         "criterion": [f"{wh} {f2(v)}" for wh, v in sorted(by_wh.items())]},
        {"rubric": "Does the report list exactly the SKUs strictly below their reorder point?", "weight": 8,
         "criterion": [f"Lists exactly: {', '.join(below)} (in this order)",
                       f"Does NOT list {eq}, whose qty equals its reorder point"]},
        {"rubric": "Does the report identify the most valuable stock line?", "weight": 4,
         "criterion": [f"{top[0]} {top[1]} with value {f2(top[3] * top[4])}"]},
    ]
    card = f"""# t1_easy answer key
- Overall: {f2(total)}
- Per warehouse: {', '.join(f'{k} {f2(v)}' for k, v in sorted(by_wh.items()))}
- Below reorder point: {', '.join(below)} (trap: {eq} qty == reorder point)
- Top line: {top[0]} {top[1]} {f2(top[3] * top[4])}
"""
    write_task("t1_easy", instructions, rubrics, card)


# ---------------------------------------------------------------- medium
def medium():
    rng = random.Random(22)
    regions = {"C01": "North", "C02": "North", "C03": "South", "C04": "South", "C05": "East",
               "C06": "East", "C07": "West", "C08": "West"}
    messy = {"North": [" north", "NORTH", "North "], "South": ["south", "South"], "East": ["East", " east "],
             "West": ["WEST", "west"]}
    cust_rows = [(cid, rng.choice(messy[r])) for cid, r in regions.items()]
    fx = {("2026-07", "USD"): D("1"), ("2026-07", "EUR"): D("1.0820"), ("2026-07", "JPY"): D("0.006950"),
          ("2026-08", "USD"): D("1"), ("2026-08", "EUR"): D("1.0910"), ("2026-08", "JPY"): D("0.006820"),
          ("2026-09", "USD"): D("1"), ("2026-09", "EUR"): D("1.0760"), ("2026-09", "JPY"): D("0.007010")}
    prices = {"P-A": {"USD": D("120.00"), "EUR": D("110.00"), "JPY": D("17500")},
              "P-B": {"USD": D("45.50"), "EUR": D("42.00"), "JPY": D("6600")},
              "P-C": {"USD": D("899.00"), "EUR": D("829.00"), "JPY": D("129000")}}
    orders = []
    for i in range(42):
        m = rng.choice([7, 8, 9])
        day = rng.randint(1, 28)
        dt = date(2026, m, day)
        cur = rng.choice(["USD", "USD", "EUR", "JPY"])
        sku = rng.choice(list(prices))
        qty = rng.randint(1, 6)
        status = rng.choices(["completed", "cancelled", "refunded"], [7, 2, 1])[0]
        cid = rng.choice(list(regions))
        orders.append(dict(id=f"ORD-{5001 + i}", dt=dt, cid=cid, sku=sku, qty=qty,
                           price=prices[sku][cur], cur=cur, status=status))
    # a completed order whose customer is not in customers.csv
    orders[17].update(cid="C99", status="completed")

    def fmt_date(o, k):
        # mix ISO and DD/MM/YYYY; use days <= 12 for some to make MM/DD misreads visible
        return o["dt"].strftime("%d/%m/%Y") if k % 3 == 0 else o["dt"].isoformat()

    def fmt_status(s, k):
        return [s, s.upper(), s.capitalize() + " ", " " + s][k % 4]

    out_rows = []
    for k, o in enumerate(orders):
        out_rows.append([o["id"], fmt_date(o, k), o["cid"], o["sku"], o["qty"], f"{o['price']:.2f}" if o["cur"] != "JPY" else f"{o['price']:.0f}", o["cur"], fmt_status(o["status"], k)])
    # exact duplicate export rows
    dup_ids = [out_rows[5][0], out_rows[23][0]]
    out_rows.insert(9, list(out_rows[5]))
    out_rows.append(list(out_rows[24]))  # row 23 shifted by one after insert

    tf = write_task("t2_medium", "", [], "")
    write_csv(tf / "orders.csv", ["order_id", "order_date", "customer_id", "sku", "quantity", "unit_price", "currency", "status"], out_rows)
    write_csv(tf / "customers.csv", ["customer_id", "region"], cust_rows)
    write_csv(tf / "fx_rates.csv", ["month", "currency", "usd_per_unit"],
              [(m, c, f"{r}") for (m, c), r in sorted(fx.items())])

    rev = defaultdict(D)
    excluded_missing = []
    n_completed = 0
    for o in orders:
        if o["status"] != "completed":
            continue
        if o["cid"] not in regions:
            excluded_missing.append(o["id"])
            continue
        n_completed += 1
        month = o["dt"].strftime("%Y-%m")
        rev[(regions[o["cid"]], month)] += o["qty"] * o["price"] * fx[(month, o["cur"])]
    total = sum(rev.values(), D(0))
    by_region = defaultdict(D)
    for (rg, _), v in rev.items():
        by_region[rg] += v
    top_region = max(by_region, key=by_region.get)
    cells = sorted(rev.items())

    instructions = """
You are the revenue analyst closing Q3 2026 (July-September).

Files in this folder:
- orders.csv: one row per order. order_id is unique by design; the export sometimes repeats a row.
  order_date is either ISO (YYYY-MM-DD) or day-first (DD/MM/YYYY). unit_price is in the order's currency.
- customers.csv: the sales region of each customer.
- fx_rates.csv: USD value of one unit of each currency, per calendar month.

Rules:
- Only orders whose status is "completed" count as revenue (status text may vary in case and spacing).
- Order revenue = quantity x unit_price, converted to USD with the rate of the order's month.
- Region names must be normalised (they vary in case and spacing in customers.csv).
- Exclude completed orders whose customer is not in customers.csv, and report them.
- Keep full precision while computing; round only the reported numbers to two decimals.

Produce:
1. output/revenue.csv with columns region,month,revenue_usd (month as YYYY-MM), sorted by region then month.
2. output/summary.md stating: total Q3 revenue in USD, the region with the highest Q3 revenue,
   how many duplicate rows were removed, and which completed orders were excluded and why.
"""
    rubrics = [
        {"rubric": f"Is the total Q3 revenue reported as {f2(total)} USD?", "weight": 10,
         "criterion": [f"Total is {f2(total)}",
                       "Only completed orders are counted, after removing duplicate rows and converting each order with its own month's rate"]},
        {"rubric": "Does output/revenue.csv contain the correct region/month figures?", "weight": 10,
         "criterion": ["Columns are region,month,revenue_usd, sorted by region then month",
                       "Region names are normalised (North, South, East, West)"]
         + [f"{rg} {m}: {f2(v)}" for (rg, m), v in cells]},
        {"rubric": f"Does the summary name {top_region} as the top region?", "weight": 4,
         "criterion": [f"{top_region} with {f2(by_region[top_region])} USD"]},
        {"rubric": "Does the summary report the data-quality handling correctly?", "weight": 6,
         "criterion": [f"2 duplicate rows removed ({', '.join(dup_ids)})",
                       f"Excludes {', '.join(excluded_missing)} because customer C99 is not in customers.csv"]},
    ]
    card = f"""# t2_medium answer key
- Total: {f2(total)}; completed orders counted: {n_completed}
- Per region: {', '.join(f'{k} {f2(v)}' for k, v in sorted(by_region.items()))}; top {top_region}
- Cells: {'; '.join(f'{rg} {m} {f2(v)}' for (rg, m), v in cells)}
- Duplicates: {', '.join(dup_ids)}; excluded (missing customer): {', '.join(excluded_missing)}
- Traps: DD/MM vs ISO dates, status case/space, region case/space, per-month FX, duplicates, unknown customer
"""
    write_task("t2_medium", instructions, rubrics, card)


# ---------------------------------------------------------------- hard
def hard():
    rng = random.Random(33)
    reps = ["R1", "R2", "R3", "R4"]
    rep_names = {"R1": "Avery Chen", "R2": "Blake Lin", "R3": "Casey Wu", "R4": "Devon Huang"}
    role_changes = [("R1", "2026-01-01", "Junior"), ("R2", "2026-01-01", "Senior"),
                    ("R3", "2026-01-01", "Junior"), ("R4", "2026-01-01", "Senior"),
                    ("R3", "2026-08-11", "Senior")]  # mid-month promotion

    def role_on(rep, d):
        cur = None
        for r, eff, role in sorted(role_changes, key=lambda x: x[1]):
            if r == rep and date.fromisoformat(eff) <= d:
                cur = role
        return cur

    fx = {("2026-07", "USD"): D("1"), ("2026-07", "EUR"): D("1.0820"),
          ("2026-08", "USD"): D("1"), ("2026-08", "EUR"): D("1.0910"),
          ("2026-09", "USD"): D("1"), ("2026-09", "EUR"): D("1.0760")}
    deals = []
    for i in range(30):
        d = date(2026, rng.choice([7, 8, 9]), rng.randint(1, 28))
        owner = rng.choice(reps)
        cur = rng.choice(["USD", "USD", "EUR"])
        amt = D(rng.randrange(2000, 16000, 50))
        split_rep, split_pct = "", 0
        if i % 7 == 3:
            split_rep = rng.choice([r for r in reps if r != owner])
            split_pct = rng.choice([30, 40, 50])
        deals.append(dict(id=f"D-{301 + i}", rep=owner, dt=d, amt=amt, cur=cur, split_rep=split_rep, split_pct=split_pct))
    # make sure a deal lands right on the amendment date and R3 has deals both sides of promotion
    # R1 (Junior) crosses the 20,000 threshold after the memo date so the 8% tier matters
    deals[2].update(dt=date(2026, 8, 15), rep="R1", amt=D("15000.00"), cur="USD", split_rep="", split_pct=0)
    deals[4].update(dt=date(2026, 8, 22), rep="R1", amt=D("12000.00"), cur="USD", split_rep="", split_pct=0)
    deals[6].update(dt=date(2026, 8, 5), rep="R3")
    deals[8].update(dt=date(2026, 8, 20), rep="R3")
    deals.sort(key=lambda x: (x["dt"], x["id"]))

    returns_ = []
    for k, idx in enumerate([1, 9, 14, 21, 26]):
        dl = deals[idx]
        days = [12, 75, 40, 59, 61][k]  # 75 and 61 are outside the 60-day window
        rd = dl["dt"] + timedelta(days=days)
        part = [D("1"), D("0.5"), D("0.25"), D("1"), D("0.4")][k]
        returns_.append(dict(deal=dl["id"], dt=rd, amt=r2(dl["amt"] * part)))

    PLAN = {"Junior": (D("20000"), D("0.05"), D("0.07")), "Senior": (D("30000"), D("0.06"), D("0.09"))}
    AMEND = date(2026, 8, 15)

    # credits: (rep, deal, close date, usd share)
    credits = []
    for dl in deals:
        usd = dl["amt"] * fx[(dl["dt"].strftime("%Y-%m"), dl["cur"])]
        sp = D(dl["split_pct"]) / 100
        if dl["split_rep"]:
            credits.append((dl["split_rep"], dl, usd * sp))
        credits.append((dl["rep"], dl, usd * (1 - sp)))

    booked = defaultdict(D)  # (rep, month) cumulative USD
    gross = defaultdict(D)
    rate_of = {}  # (rep, deal) -> (commission, usd share)
    for rep, dl, usd in sorted(credits, key=lambda c: (c[1]["dt"], c[1]["id"], c[0])):
        month = dl["dt"].strftime("%Y-%m")
        role = role_on(rep, dl["dt"])
        cap, lo, hi = PLAN[role]
        if role == "Junior" and dl["dt"] >= AMEND:
            hi = D("0.08")
        before = booked[(rep, month)]
        low_part = max(D(0), min(usd, cap - before))
        comm = low_part * lo + (usd - low_part) * hi
        booked[(rep, month)] = before + usd
        gross[(rep, month)] += comm
        rate_of[(rep, dl["id"])] = (comm, usd)

    by_id = {d["id"]: d for d in deals}
    claw = defaultdict(D)
    claw_detail = []
    for rt in returns_:
        dl = by_id[rt["deal"]]
        if (rt["dt"] - dl["dt"]).days > 60:
            claw_detail.append((rt["deal"], "outside 60 days", D(0)))
            continue
        refund_usd = rt["amt"] * fx[(dl["dt"].strftime("%Y-%m"), dl["cur"])]
        frac = refund_usd / (dl["amt"] * fx[(dl["dt"].strftime("%Y-%m"), dl["cur"])])
        rmonth = rt["dt"].strftime("%Y-%m")
        tot = D(0)
        for rep in [dl["rep"]] + ([dl["split_rep"]] if dl["split_rep"] else []):
            comm, _ = rate_of[(rep, dl["id"])]
            c = comm * frac
            claw[(rep, rmonth)] += c
            tot += c
        claw_detail.append((rt["deal"], rmonth, tot))

    months = ["2026-07", "2026-08", "2026-09", "2026-10", "2026-11"]
    keys = [(r, m) for r in reps for m in months if gross.get((r, m)) or claw.get((r, m))]
    net = {k: gross.get(k, D(0)) - claw.get(k, D(0)) for k in keys}

    # payments: correct except deliberate errors
    pay = {k: r2(v) for k, v in net.items()}
    errs = []
    k1 = ("R1", "2026-08")
    if k1 in pay:
        pay[k1] -= D("37.50"); errs.append(k1)
    k2 = next(k for k in keys if k[0] == "R3" and k[1] == "2026-08")
    pay[k2] += D("120.00"); errs.append(k2)
    k3 = next(k for k in keys if k[0] == "R4" and k[1] == "2026-09")
    del pay[k3]; errs.append(k3)  # never paid

    tf = write_task("t3_hard", "", [], "")
    write_csv(tf / "reps.csv", ["rep_id", "name"], [(r, rep_names[r]) for r in reps])
    write_csv(tf / "role_changes.csv", ["rep_id", "effective_date", "role"], role_changes)
    write_csv(tf / "deals.csv", ["deal_id", "owner_rep_id", "close_date", "amount", "currency", "split_rep_id", "split_pct"],
              [(d["id"], d["rep"], d["dt"].isoformat(), f"{d['amt']:.2f}", d["cur"], d["split_rep"], d["split_pct"] or "") for d in sorted(deals, key=lambda x: x["id"])])
    write_csv(tf / "returns.csv", ["deal_id", "return_date", "refund_amount"],
              [(r["deal"], r["dt"].isoformat(), f"{r['amt']:.2f}") for r in returns_])
    write_csv(tf / "fx_rates.csv", ["month", "currency", "usd_per_unit"], [(m, c, f"{v}") for (m, c), v in sorted(fx.items())])
    write_csv(tf / "payments_made.csv", ["rep_id", "month", "amount_paid"], [(r, m, f"{v:.2f}") for (r, m), v in sorted(pay.items())])
    (tf / "commission_policy.md").write_text("""# Sales Commission Policy (effective 2026-01-01)

1. Commission is earned per rep per calendar month on USD-credited bookings.
2. Each deal is converted to USD at the rate for the month of its close_date.
3. Split deals: the split rep is credited split_pct % of the deal; the owner is credited the rest.
   Each credited share counts toward that rep's own monthly volume.
4. Plans are marginal tiers on the rep's cumulative USD credited in the month, applied to deals in
   order of close_date (ties: deal_id ascending):
   - Junior: 5% on the first 20,000 USD of the month, 7% above that.
   - Senior: 6% on the first 30,000 USD of the month, 9% above that.
   A deal that crosses the threshold is split between the two rates.
5. The plan used for a deal is the rep's role in effect on the deal's close_date.
6. Clawback: if a deal is refunded within 60 days of close_date (inclusive), each credited rep
   repays the same fraction of their commission on that deal as the fraction of the deal refunded.
   The clawback is booked in the month of the return_date. Refunds after 60 days have no clawback.
""", encoding="utf-8")
    (tf / "memo_2026-08-12.txt").write_text("""From: VP Sales
Date: 2026-08-12
Subject: Junior plan change

Effective for deals closed on or after 15 August 2026, the Junior upper-tier rate increases
from 7% to 8%. The threshold and the lower-tier rate are unchanged. Senior plans are unaffected.
""", encoding="utf-8")

    instructions = """
You are the sales-operations analyst auditing Q3 2026 commission payouts before they are finalised.

Files in this folder: reps.csv, role_changes.csv, deals.csv, returns.csv, fx_rates.csv,
payments_made.csv, commission_policy.md and memo_2026-08-12.txt. Read every document: later
memos may change the policy.

Compute, for every rep and every month that has commission or clawback activity:
gross commission, clawback and net commission (gross - clawback). Keep full precision while
computing; round only reported numbers to two decimals.

Produce:
1. output/commissions.csv with columns rep_id,month,gross_commission,clawback,net_commission,
   sorted by rep_id then month.
2. output/discrepancies.csv with columns rep_id,month,computed_net,amount_paid,difference
   (difference = amount_paid - computed_net; amount_paid is 0.00 when no payment exists),
   listing only rows where the absolute difference exceeds 0.01.
3. output/audit_memo.md: one short section per discrepancy with a likely cause, plus a list of
   any refunds that did NOT trigger a clawback and why.
"""
    lines = [f"{r} {m}: gross {f2(gross.get((r, m), D(0)))}, clawback {f2(claw.get((r, m), D(0)))}, net {f2(net[(r, m)])}" for r, m in keys]
    disc = []
    for k in sorted(errs):
        paid = pay.get(k, D(0))
        disc.append(f"{k[0]} {k[1]}: computed {f2(net[k])}, paid {paid:.2f}, difference {f2(paid - net[k])}")
    no_claw = [d for d, why, _ in claw_detail if why == "outside 60 days"]
    total_net = sum(net.values(), D(0))
    rubrics = [
        {"rubric": f"Is the total Q3 net commission across all reps {f2(total_net)}?", "weight": 8,
         "criterion": [f"Sum of net_commission over all rows is {f2(total_net)} (within 0.05 for rounding)"]},
        {"rubric": "Does output/commissions.csv contain the correct gross, clawback and net per rep and month?", "weight": 14,
         "criterion": ["Columns rep_id,month,gross_commission,clawback,net_commission sorted by rep then month"] + lines},
        {"rubric": "Are the policy edge cases applied?", "weight": 8,
         "criterion": ["Marginal tiers: a deal crossing the monthly threshold is split between the two rates",
                       "R3 is paid on the Junior plan before 2026-08-11 and on the Senior plan from 2026-08-11",
                       "Junior upper tier is 8% for deals closed on or after 2026-08-15 (memo), 7% before",
                       "Split deals credit split_pct to the split rep and the remainder to the owner"]},
        {"rubric": "Does output/discrepancies.csv list exactly the payout discrepancies?", "weight": 10,
         "criterion": disc + ["No other rows are listed"]},
        {"rubric": "Does the audit memo identify refunds without clawback?", "weight": 4,
         "criterion": [f"Names {', '.join(no_claw)} as refunded more than 60 days after close, so no clawback"]},
    ]
    card = "# t3_hard answer key\n" + "\n".join(f"- {x}" for x in lines) + \
        f"\n- Total net: {f2(total_net)}\n- Discrepancies:\n" + "\n".join(f"  - {x}" for x in disc) + \
        "\n- Clawbacks: " + "; ".join(f"{d} {w} {f2(v)}" for d, w, v in claw_detail) + \
        "\n- Traps: marginal tiers, mid-month promotion (R3 2026-08-11), memo 8% from 2026-08-15, splits, 60-day window, clawback month = return month, unpaid month\n"
    write_task("t3_hard", instructions, rubrics, card)


easy()
medium()
hard()
print("written to", ROOT)
for p in sorted(KEYS.glob("*.md")):
    print("=" * 10, p.stem)
    print(p.read_text(encoding="utf-8"))
