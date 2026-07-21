"""RBAC + maker-checker enforcement tests (run on a throwaway DB copy).

Covers domain-spec §8:
  ① reviewer calling authorize_dispute is refused (permission_denied)
  ② the proposer authorizing their own draft = maker_checker_violation
  ③ a different finance actor CAN authorize
  ④ manager sees the KPI board, reviewer does not
  ⑤ every denied attempt left an action_log row (denials leave a trail)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

_R = str(Path(__file__).resolve().parent.parent)
if _R not in sys.path:
    sys.path.insert(0, _R)

from app import actions
from app.rbac import can_do, make_actor
from config.loader import db_path

AS_OF = "2026-07-08"


def _fresh_conn() -> tuple[sqlite3.Connection, str]:
    fd, tmp = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copy(db_path(), tmp)
    return sqlite3.connect(tmp), tmp


def _a_pending_review(conn: sqlite3.Connection) -> str:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT review_id FROM review_queue WHERE status='pending' LIMIT 1"
    ).fetchone()[0]


def main() -> None:
    conn, tmp = _fresh_conn()
    try:
        rid = _a_pending_review(conn)
        finance_a = make_actor("fin-alice", "finance", "Alice")
        finance_b = make_actor("fin-bob", "finance", "Bob")
        reviewer = make_actor("rev-carol", "reviewer", "Carol")

        # finance_a drafts a dispute (proposer = fin-alice)
        r_app = actions.approve_discrepancy(conn, rid, finance_a, as_of=AS_OF)
        assert r_app.ok and r_app.status == "done", r_app
        print(f"[setup] finance_a approved {rid} -> draft {r_app.outbox_key} (pending)")

        # ① reviewer cannot authorize
        r1 = actions.authorize_dispute(conn, rid, reviewer, as_of=AS_OF)
        assert not r1.ok and r1.status == "permission_denied", r1
        print(f"[①] reviewer authorize_dispute -> {r1.status} PASS")

        # ② proposer authorizing own draft -> maker-checker violation
        r2 = actions.authorize_dispute(conn, rid, finance_a, as_of=AS_OF)
        assert not r2.ok and r2.status == "maker_checker_violation", r2
        print(f"[②] proposer self-authorize -> {r2.status} PASS")

        # ③ a different finance actor CAN authorize
        r3 = actions.authorize_dispute(conn, rid, finance_b, as_of=AS_OF)
        assert r3.ok and r3.status == "done", r3
        print(f"[③] finance_b authorize -> {r3.status} PASS")

        # draft-not-send: outbox is 'authorized' (a status), nothing dispatched
        conn.row_factory = sqlite3.Row
        ob = conn.execute(
            "SELECT status, authorizer_id, attempt_count FROM dispute_outbox WHERE idempotency_key=?",
            (r3.outbox_key,),
        ).fetchone()
        assert ob["status"] == "authorized" and ob["authorizer_id"] == "fin-bob", dict(ob)
        assert ob["attempt_count"] == 0, "authorized must not increment send attempts"
        print(f"[draft-not-send] outbox status={ob['status']} authorizer={ob['authorizer_id']} attempts={ob['attempt_count']} PASS")

        # ④ KPI visibility by role
        assert can_do("manager", "view_kpi") and not can_do("reviewer", "view_kpi")
        assert not can_do("finance", "view_kpi")
        print("[④] view_kpi: manager=True reviewer=False finance=False PASS")

        # ⑤ denied attempts were logged
        denied = conn.execute(
            "SELECT action_name, actor_id FROM action_log WHERE action_name LIKE '%.denied'"
        ).fetchall()
        names = {(d["action_name"], d["actor_id"]) for d in denied}
        assert ("authorize_dispute.denied", "rev-carol") in names, names
        assert ("authorize_dispute.denied", "fin-alice") in names, names
        print(f"[⑤] action_log denied rows: {sorted(names)} PASS")

        print("\nRBAC + maker-checker: ALL PASS")
    finally:
        conn.close()
        os.remove(tmp)


if __name__ == "__main__":
    main()
