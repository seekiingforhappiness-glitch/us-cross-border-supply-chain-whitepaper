"""Run the reconciliation engine over all invoices and populate review_queue.

Each detected discrepancy becomes a review_queue row (status='pending') with
its evidence pack, written inside a governance transaction and mirrored to
action_log (explicit as_of, no system clock). draft-not-send: nothing is
sent; a human works the queue.
"""
from __future__ import annotations

import json
import sqlite3

from config.loader import db_path, load_config
from governance.audit import record_action
from governance.transaction import Actor, next_stable_id, transaction
from recon.engine import audit_all


def run(db: str | None = None) -> dict:
    cfg = load_config()
    as_of = cfg["recon_as_of"]
    created_at = f"{as_of}T00:00:00Z"
    dbp = db or db_path()
    conn = sqlite3.connect(dbp)
    actor = Actor("system-recon", "reviewer", "Recon Engine")

    detections = audit_all(conn)

    conn.execute("DELETE FROM review_queue")
    with transaction(conn):
        for d in detections:
            review_id = next_stable_id(
                "REV", f"{d['invoice_id']}|{d['invoice_line_id']}|{d['discrepancy_type']}")
            conn.execute(
                """INSERT OR REPLACE INTO review_queue (
                    review_id, invoice_id, invoice_line_id, discrepancy_type, severity,
                    detected_amount_usd, evidence_json, status, resolution, assigned_to,
                    as_of_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?)""",
                (review_id, d["invoice_id"], d["invoice_line_id"], d["discrepancy_type"],
                 d["severity"], d["detected_amount_usd"],
                 json.dumps(d["evidence"], ensure_ascii=False, sort_keys=True), as_of, created_at),
            )
            record_action(
                conn, actor, "recon.flag_discrepancy", as_of=as_of,
                target_table="review_queue", target_id=review_id,
                payload={"invoice_id": d["invoice_id"], "type": d["discrepancy_type"],
                         "detected_amount_usd": d["detected_amount_usd"], "severity": d["severity"]},
            )
    conn.commit()

    by_type: dict[str, int] = {}
    total_recovery = 0.0
    for d in detections:
        by_type[d["discrepancy_type"]] = by_type.get(d["discrepancy_type"], 0) + 1
        total_recovery += d["detected_amount_usd"]
    conn.close()
    return {"detections": len(detections), "total_recovery_usd": round(total_recovery, 2),
            "by_type": by_type}


if __name__ == "__main__":
    r = run()
    print(f"recon complete -> review_queue: {r['detections']} pending, "
          f"recovery ${r['total_recovery_usd']:,.2f}")
    for t in sorted(r["by_type"]):
        print(f"  {t:26s} {r['by_type'][t]}")
