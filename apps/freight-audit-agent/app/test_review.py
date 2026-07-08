"""End-to-end review test (domain-spec §7.1) on a minimal throwaway DB.

Inject a RATE_MISMATCH invoice (OFR billed $2000 vs contract $1500) ->
  - the engine flags exactly one RATE_MISMATCH in review_queue,
    detected_amount_usd == (2000-1500) x qty, evidence carries the invoice
    line + contract rate + calculation, status == 'pending';
  - approve -> a dispute draft appears in dispute_outbox (status=pending),
    NOT auto-sent, and the action is logged;
  - rollback and reject each leave an audit trail.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_R = str(Path(__file__).resolve().parent.parent)
if _R not in sys.path:
    sys.path.insert(0, _R)

from app import actions
from app.rbac import make_actor
from datagen.schema import reset_schema
from governance.transaction import next_stable_id
from recon.engine import audit_all

AS_OF = "2026-07-08"
CREATED = f"{AS_OF}T00:00:00Z"


def _insert(conn: sqlite3.Connection, table: str, **cols) -> None:
    keys = ", ".join(cols)
    marks = ", ".join(["?"] * len(cols))
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({marks})", tuple(cols.values()))


def _build_scenario(conn: sqlite3.Connection) -> None:
    reset_schema(conn)
    _insert(conn, "rate_cons", rate_con_id="RC-1", carrier_id="MAEU",
            origin_port="CNSHA", destination_port="USLAX",
            effective_date="2026-01-01", expiry_date="2026-12-31",
            contract_party="ACME", currency="USD", free_time_days=4,
            as_of_date=AS_OF, created_at=CREATED)
    _insert(conn, "rate_card_lines", rate_card_line_id="RCL-1", rate_con_id="RC-1",
            charge_code="OFR", container_type="40HC", contracted_rate_usd=1500.0,
            unit="per_container", free_time_days=4, as_of_date=AS_OF, created_at=CREATED)
    _insert(conn, "bols", bol_id="BOL-1", bl_no="BLTEST1", booking_no="BKG1",
            carrier_id="MAEU", origin_port="CNSHA", destination_port="USLAX",
            container_no="CONT1", container_type="40HC", container_count=1,
            as_of_date=AS_OF, created_at=CREATED)
    _insert(conn, "invoices", invoice_id="INV-T1", invoice_no="INVNO-T1",
            carrier_id="MAEU", carrier_name="MAERSK", bill_to_party="ACME",
            booking_no="BKG1", bl_no="BLTEST1", invoice_date="2026-06-01",
            currency="USD", fx_rate=1.0, total_amount_usd=2000.0,
            as_of_date=AS_OF, created_at=CREATED)
    _insert(conn, "invoice_lines", invoice_line_id="LN-T1-01", invoice_id="INV-T1",
            line_no=1, charge_code="OFR", charge_description="Ocean Freight",
            container_no="CONT1", container_type="40HC", quantity=1.0,
            unit="per_container", unit_rate_usd=2000.0, amount_usd=2000.0,
            currency="USD", as_of_date=AS_OF, created_at=CREATED)
    conn.commit()


def _write_review_queue(conn: sqlite3.Connection, detections: list[dict]) -> None:
    for d in detections:
        review_id = next_stable_id(
            "REV", f"{d['invoice_id']}|{d['invoice_line_id']}|{d['discrepancy_type']}")
        _insert(conn, "review_queue", review_id=review_id, invoice_id=d["invoice_id"],
                invoice_line_id=d["invoice_line_id"], discrepancy_type=d["discrepancy_type"],
                severity=d["severity"], detected_amount_usd=d["detected_amount_usd"],
                evidence_json=json.dumps(d["evidence"], ensure_ascii=False, sort_keys=True),
                status="pending", resolution=None, assigned_to=None,
                as_of_date=AS_OF, created_at=CREATED)
    conn.commit()


def main() -> None:
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    conn = sqlite3.connect(tmp)
    try:
        _build_scenario(conn)
        detections = audit_all(conn)
        _write_review_queue(conn, detections)

        # --- §7.1 detection assertions ---
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM review_queue").fetchall()
        assert len(rows) == 1, f"expected 1 discrepancy, got {len(rows)}"
        row = dict(rows[0])
        assert row["discrepancy_type"] == "RATE_MISMATCH", row["discrepancy_type"]
        expected_recovery = (2000.0 - 1500.0) * 1.0
        assert row["detected_amount_usd"] == expected_recovery, row["detected_amount_usd"]
        assert row["status"] == "pending", row["status"]
        ev = json.loads(row["evidence_json"])
        assert ev["invoice_line"]["unit_rate_usd"] == 2000.0, ev
        assert ev["contract"]["contracted_rate_usd"] == 1500.0, ev
        assert "calc" in ev and "1500" in ev["calc"], ev
        rid = row["review_id"]
        print(f"[detect] 1x RATE_MISMATCH detected={row['detected_amount_usd']} "
              f"status={row['status']} evidence(line/contract/calc) PASS")

        reviewer = make_actor("rev-carol", "reviewer", "Carol")

        # --- approve -> draft, not sent, logged ---
        r_app = actions.approve_discrepancy(conn, rid, reviewer, as_of=AS_OF)
        assert r_app.ok and r_app.status == "done", r_app
        ob = conn.execute(
            "SELECT status, attempt_count FROM dispute_outbox WHERE idempotency_key=?",
            (r_app.outbox_key,),
        ).fetchone()
        assert ob["status"] == "pending", "draft must be pending (not sent)"
        assert ob["attempt_count"] == 0, "no send attempt on a draft"
        logged = conn.execute(
            "SELECT COUNT(*) FROM action_log WHERE action_name='approve_discrepancy' AND target_id=?",
            (rid,),
        ).fetchone()[0]
        assert logged == 1, "approve must be audited"
        print(f"[approve] draft={r_app.outbox_key} status={ob['status']} attempts={ob['attempt_count']} audited=1 PASS")

        # --- rollback leaves a trace ---
        r_rb = actions.rollback(conn, rid, reviewer, as_of=AS_OF)
        assert r_rb.ok and r_rb.status == "done", r_rb
        back = conn.execute("SELECT status FROM review_queue WHERE review_id=?", (rid,)).fetchone()[0]
        assert back == "pending", back
        rb_logged = conn.execute(
            "SELECT COUNT(*) FROM action_log WHERE action_name='rollback' AND target_id=?", (rid,)
        ).fetchone()[0]
        assert rb_logged == 1, "rollback must be audited"
        print(f"[rollback] review status={back} audited={rb_logged} PASS")

        # --- reject leaves a trace ---
        r_rej = actions.reject_discrepancy(conn, rid, reviewer, as_of=AS_OF, resolution="false_positive")
        assert r_rej.ok and r_rej.status == "done", r_rej
        st = conn.execute("SELECT status, resolution FROM review_queue WHERE review_id=?", (rid,)).fetchone()
        assert st[0] == "rejected" and st[1] == "false_positive", dict(zip(("status", "resolution"), st))
        rej_logged = conn.execute(
            "SELECT COUNT(*) FROM action_log WHERE action_name='reject_discrepancy' AND target_id=?", (rid,)
        ).fetchone()[0]
        assert rej_logged == 1, "reject must be audited"
        print(f"[reject] review status={st[0]} resolution={st[1]} audited={rej_logged} PASS")

        print("\nEnd-to-end review (§7.1): ALL PASS")
    finally:
        conn.close()
        os.remove(tmp)


if __name__ == "__main__":
    main()
