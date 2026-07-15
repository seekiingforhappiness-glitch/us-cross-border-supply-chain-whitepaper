"""Independent evaluator: review_queue (detected) vs expected_discrepancies (truth).

Alignment key:
    line-level    -> (invoice_line_id, discrepancy_type)
    invoice-level -> (invoice_id, discrepancy_type)   [invoice_line_id is NULL]

Gray-zone invoices carry no ground truth, so any detection on them lands in
FP by construction. Reports precision / recall / false-positive rate /
recovered USD, plus the miss (FN) and false-alarm (FP) lists. PASS iff
precision >= 0.90 and recall >= 0.85 (domain-spec §7 / AGENTS §4).
"""
from __future__ import annotations

import sqlite3

from config.loader import db_path

P_GATE, R_GATE = 0.90, 0.85


def _key(invoice_id: str, invoice_line_id: str | None, dtype: str) -> tuple:
    if invoice_line_id:
        return ("L", invoice_line_id, dtype)
    return ("I", invoice_id, dtype)


def evaluate(db: str | None = None) -> dict:
    conn = sqlite3.connect(db or db_path())
    conn.row_factory = sqlite3.Row

    expected = {}
    for r in conn.execute("SELECT * FROM expected_discrepancies"):
        expected[_key(r["invoice_id"], r["invoice_line_id"], r["discrepancy_type"])] = dict(r)
    detected = {}
    for r in conn.execute("SELECT * FROM review_queue"):
        detected[_key(r["invoice_id"], r["invoice_line_id"], r["discrepancy_type"])] = dict(r)
    conn.close()

    e_keys, d_keys = set(expected), set(detected)
    tp_keys = d_keys & e_keys
    fp_keys = d_keys - e_keys
    fn_keys = e_keys - d_keys

    tp, fp, fn = len(tp_keys), len(fp_keys), len(fn_keys)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fp_rate = fp / (tp + fp) if (tp + fp) else 0.0
    recovery = round(sum(detected[k]["detected_amount_usd"] or 0.0 for k in tp_keys), 2)

    fn_list = [{"key": k, "type": k[2], "expected_recovery_usd": expected[k]["expected_recovery_usd"],
                "note": expected[k]["injection_note"]} for k in sorted(fn_keys)]
    fp_list = [{"key": k, "invoice_id": detected[k]["invoice_id"], "type": k[2],
                "detected_amount_usd": detected[k]["detected_amount_usd"]} for k in sorted(fp_keys)]

    passed = precision >= P_GATE and recall >= R_GATE
    return {"detected": len(d_keys), "expected": len(e_keys), "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "fp_rate": fp_rate,
            "recovery_usd": recovery, "passed": passed, "fn_list": fn_list, "fp_list": fp_list}


def main() -> None:
    r = evaluate()
    print("=" * 60)
    print("FREIGHT AUDIT — EVALUATION")
    print("=" * 60)
    print(f"detected (review_queue) : {r['detected']}")
    print(f"expected (ground truth) : {r['expected']}")
    print(f"TP / FP / FN            : {r['tp']} / {r['fp']} / {r['fn']}")
    print(f"precision               : {r['precision']:.3f}  (gate >= {P_GATE})")
    print(f"recall                  : {r['recall']:.3f}  (gate >= {R_GATE})")
    print(f"false-positive rate     : {r['fp_rate']:.3f}")
    print(f"recovered (TP)          : ${r['recovery_usd']:,.2f}")
    print("-" * 60)
    print(f"false negatives (missed): {len(r['fn_list'])}")
    for f in r["fn_list"]:
        print(f"  FN {f['type']:24s} exp_rec=${f['expected_recovery_usd']:.2f}  {f['note']}")
    print(f"false positives (alarms): {len(r['fp_list'])}")
    for f in r["fp_list"]:
        print(f"  FP {f['type']:24s} {f['invoice_id']}  det=${f['detected_amount_usd']:.2f}")
    print("=" * 60)
    print("RESULT:", "PASS" if r["passed"] else "FAIL",
          f"(P={r['precision']:.3f} R={r['recall']:.3f})")
    print("=" * 60)


if __name__ == "__main__":
    main()
