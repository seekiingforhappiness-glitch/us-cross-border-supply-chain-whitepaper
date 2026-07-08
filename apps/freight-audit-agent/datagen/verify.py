"""Self-consistency checks for the generated dataset.

Verifies: fixed counts; byte-identical re-generation (determinism);
every ground-truth row points at a real invoice/line; gray-zone invoices
carry NO ground truth; detention/demurrage stored dates agree with
datagen.dd_calc; invoice totals equal the sum of their lines.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

from datagen import dd_calc
from datagen.generate import FREE_TIME, generate
from datagen.schema import DATA_TABLES


def _dump(db: str) -> dict[str, list]:
    conn = sqlite3.connect(db)
    out = {}
    for tbl in DATA_TABLES:
        rows = conn.execute(f"SELECT * FROM {tbl}").fetchall()
        out[tbl] = sorted(map(repr, rows))
    conn.close()
    return out


def verify() -> bool:
    tmp = tempfile.mkdtemp(prefix="fa_verify_")
    db1, db2 = os.path.join(tmp, "a.sqlite"), os.path.join(tmp, "b.sqlite")
    c1 = generate(db1)
    c2 = generate(db2)
    checks: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # 1. fixed counts
    check("carriers == 5", c1["carriers"] == 5, str(c1["carriers"]))
    check("rate_cons == 15", c1["rate_cons"] == 15, str(c1["rate_cons"]))
    check("rate_card_lines == 180", c1["rate_card_lines"] == 180, str(c1["rate_card_lines"]))
    check("invoices == 150", c1["invoices"] == 150, str(c1["invoices"]))
    check("bols == 150", c1["bols"] == 150, str(c1["bols"]))
    check("invoice_lines ~1200 (1000-1450)", 1000 <= c1["invoice_lines"] <= 1450, str(c1["invoice_lines"]))
    check("expected_discrepancies > 0", c1["expected_discrepancies"] > 0, str(c1["expected_discrepancies"]))
    check("dirty invoices == 45", c1["dirty_invoices"] == 45, str(c1["dirty_invoices"]))
    check("gray invoices == 12", c1["gray_invoices"] == 12, str(c1["gray_invoices"]))

    # 2. determinism: byte-identical regeneration
    d1, d2 = _dump(db1), _dump(db2)
    same = all(d1[t] == d2[t] for t in DATA_TABLES)
    check("regeneration is byte-identical", same,
          "" if same else "differing: " + ",".join(t for t in DATA_TABLES if d1[t] != d2[t]))

    # 3-6: relational / dd / total checks on db1
    conn = sqlite3.connect(db1)
    conn.row_factory = sqlite3.Row
    line_ids = {r[0] for r in conn.execute("SELECT invoice_line_id FROM invoice_lines")}
    inv_ids = {r[0] for r in conn.execute("SELECT invoice_id FROM invoices")}

    orphan_line = orphan_inv = 0
    for r in conn.execute("SELECT * FROM expected_discrepancies"):
        if r["invoice_line_id"] and r["invoice_line_id"] not in line_ids:
            orphan_line += 1
        if r["invoice_id"] not in inv_ids:
            orphan_inv += 1
    check("every ground-truth line_id exists", orphan_line == 0, f"{orphan_line} orphans")
    check("every ground-truth invoice exists", orphan_inv == 0, f"{orphan_inv} orphans")

    gray = set(c1["gray_ids"])
    gt_invoices = {r[0] for r in conn.execute("SELECT invoice_id FROM expected_discrepancies")}
    check("gray-zone invoices carry NO ground truth", not (gray & gt_invoices),
          f"leaked: {sorted(gray & gt_invoices)}")

    dd_bad = 0
    for r in conn.execute("SELECT * FROM bols"):
        dem = dd_calc.demurrage_days(r["discharge_date"], r["pickup_date"], FREE_TIME)
        det = dd_calc.detention_days(r["gate_out_date"], r["return_date"], FREE_TIME)
        if dem != (r["free_days_used"] - FREE_TIME) or det < 0:
            dd_bad += 1
    check("dd_calc agrees with stored bol dates", dd_bad == 0, f"{dd_bad} mismatched bols")

    total_bad = 0
    sums = dict(conn.execute(
        "SELECT invoice_id, ROUND(SUM(amount_usd), 2) FROM invoice_lines GROUP BY invoice_id"))
    for r in conn.execute("SELECT invoice_id, total_amount_usd FROM invoices"):
        if abs((sums.get(r[0], 0.0) or 0.0) - r[1]) > 0.01:
            total_bad += 1
    check("invoice total == sum(lines)", total_bad == 0, f"{total_bad} mismatched invoices")
    conn.close()

    print("=" * 60)
    print("DATAGEN VERIFY")
    print("=" * 60)
    for name, ok, detail in checks:
        tag = "PASS" if ok else "FAIL"
        extra = f"  [{detail}]" if (detail and not ok) else ""
        print(f"  [{tag}] {name}{extra}")
    print("-" * 60)
    print("counts:", {k: v for k, v in c1.items() if not k.endswith("_ids")})
    all_ok = all(ok for _, ok, _ in checks)
    print("=" * 60)
    print("VERIFY:", "ALL GREEN" if all_ok else "FAILURES PRESENT")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if verify() else 1)
