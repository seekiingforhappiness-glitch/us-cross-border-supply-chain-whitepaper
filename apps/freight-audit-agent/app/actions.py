"""Reconciliation action layer — RBAC + maker-checker enforced HERE.

This module is the authority. The Streamlit UI may hide buttons for tidiness,
but every state change goes through these functions, each of which:
  1. gates the action against the RBAC matrix (app.rbac.can_do); a denied call
     returns a structured refusal AND writes an action_log row (denials leave a
     trail too, per AGENTS §7);
  2. writes inside a governance transaction with an explicit `as_of` (no system
     clock), mirrored to action_log;
  3. keeps draft-not-send: approving only drafts a dispute in dispute_outbox
     (status=pending); authorizing only flips it to 'authorized'. Nothing is
     ever dispatched to a carrier or a payment system.

maker-checker: authorize_dispute requires the authorizer to differ from the
proposer who created the draft (enforced via app.rbac.maker_checker ->
governance.ApprovalPolicy). See MAKER_CHECKER_ENFORCED_AT below.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.rbac import can_do, maker_checker
from governance.audit import record_action
from governance.outbox import (
    ensure_dispute_outbox,
    enqueue_dispute,
    idempotency_key,
    transition_dispute,
)
from governance.transaction import Actor, transaction

DISPUTE_TARGET_SYSTEM = "carrier_dispute"
DISPUTE_ACTION = "dispute_claim"

# Documented enforcement point for the maker-checker hard rule (domain-spec §8).
# The role gate happens at the `can_do(...)` check in each function; the
# proposer != approver check happens in `authorize_dispute` at the
# `maker_checker(proposer, actor)` call (see that function).
MAKER_CHECKER_ENFORCED_AT = "app/actions.py::authorize_dispute -> maker_checker()"


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    action: str
    # done | permission_denied | maker_checker_violation | not_found |
    # invalid_state | outbox_error
    status: str
    detail: str = ""
    review_id: str | None = None
    outbox_key: str | None = None


def _deny(
    conn: sqlite3.Connection,
    actor: Actor,
    action: str,
    reason: str,
    *,
    as_of: str,
    review_id: str,
    detail: str,
) -> ActionResult:
    """Record a denied attempt (audit trail) and return a structured refusal."""
    with transaction(conn):
        record_action(
            conn, actor, f"{action}.denied", as_of=as_of,
            target_table="review_queue", target_id=review_id,
            payload={"reason": reason, "role": actor.role, "detail": detail},
        )
    return ActionResult(False, action, reason, detail, review_id)


def _load_review(conn: sqlite3.Connection, review_id: str) -> dict | None:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM review_queue WHERE review_id=?", (review_id,)
    ).fetchone()
    return dict(row) if row else None


def approve_discrepancy(
    conn: sqlite3.Connection, review_id: str, actor: Actor, *, as_of: str
) -> ActionResult:
    """Approve a discrepancy -> draft a dispute (dispute_outbox, status=pending).

    reviewer/finance may; manager may NOT (domain-spec §8). draft-not-send.
    """
    action = "approve_discrepancy"
    if not can_do(actor.role, action):
        return _deny(conn, actor, action, "permission_denied", as_of=as_of,
                     review_id=review_id, detail=f"role={actor.role}")
    review = _load_review(conn, review_id)
    if review is None:
        return ActionResult(False, action, "not_found", "no such review", review_id)
    if review["status"] != "pending":
        return ActionResult(False, action, "invalid_state",
                            f"status={review['status']}", review_id)

    key = idempotency_key(DISPUTE_TARGET_SYSTEM, DISPUTE_ACTION, review_id)
    payload = {
        "review_id": review_id,
        "invoice_id": review["invoice_id"],
        "discrepancy_type": review["discrepancy_type"],
        "detected_amount_usd": review["detected_amount_usd"],
    }
    try:
        with transaction(conn):
            conn.execute(
                "UPDATE review_queue SET status='approved', resolution='dispute_drafted', "
                "assigned_to=? WHERE review_id=?",
                (actor.actor_id, review_id),
            )
            record_action(
                conn, actor, action, as_of=as_of,
                target_table="review_queue", target_id=review_id,
                payload={"outbox_key": key, "detected_amount_usd": review["detected_amount_usd"]},
            )
            res = enqueue_dispute(  # participates in this transaction
                conn, DISPUTE_TARGET_SYSTEM, DISPUTE_ACTION, review_id, payload,
                as_of=as_of, proposer_id=actor.actor_id,
            )
            if not res.ok:
                raise RuntimeError(f"outbox {res.status}: {res.detail}")
    except Exception as exc:
        return ActionResult(False, action, "outbox_error", str(exc), review_id, key)
    return ActionResult(True, action, "done", "dispute drafted (pending)", review_id, key)


def reject_discrepancy(
    conn: sqlite3.Connection, review_id: str, actor: Actor, *,
    as_of: str, resolution: str = "false_positive",
) -> ActionResult:
    """Mark a discrepancy as a false positive / not pursued. All three roles."""
    action = "reject_discrepancy"
    if not can_do(actor.role, action):
        return _deny(conn, actor, action, "permission_denied", as_of=as_of,
                     review_id=review_id, detail=f"role={actor.role}")
    review = _load_review(conn, review_id)
    if review is None:
        return ActionResult(False, action, "not_found", "no such review", review_id)
    with transaction(conn):
        conn.execute(
            "UPDATE review_queue SET status='rejected', resolution=?, assigned_to=? "
            "WHERE review_id=?",
            (resolution, actor.actor_id, review_id),
        )
        record_action(
            conn, actor, action, as_of=as_of,
            target_table="review_queue", target_id=review_id,
            payload={"resolution": resolution, "prior_status": review["status"]},
        )
    return ActionResult(True, action, "done", resolution, review_id)


def rollback(
    conn: sqlite3.Connection, review_id: str, actor: Actor, *, as_of: str
) -> ActionResult:
    """Revert a review decision back to 'pending'. All three roles.

    draft-not-send means any dispute draft was never dispatched, so reopening
    the review is safe; the prior status is captured in the audit payload.
    """
    action = "rollback"
    if not can_do(actor.role, action):
        return _deny(conn, actor, action, "permission_denied", as_of=as_of,
                     review_id=review_id, detail=f"role={actor.role}")
    review = _load_review(conn, review_id)
    if review is None:
        return ActionResult(False, action, "not_found", "no such review", review_id)
    with transaction(conn):
        conn.execute(
            "UPDATE review_queue SET status='pending', resolution=NULL, assigned_to=NULL "
            "WHERE review_id=?",
            (review_id,),
        )
        record_action(
            conn, actor, action, as_of=as_of,
            target_table="review_queue", target_id=review_id,
            payload={"reverted_from": review["status"], "prior_resolution": review["resolution"]},
        )
    return ActionResult(True, action, "done", f"reverted from {review['status']}", review_id)


def authorize_dispute(
    conn: sqlite3.Connection, review_id: str, actor: Actor, *, as_of: str
) -> ActionResult:
    """Authorize a drafted dispute: dispute_outbox pending -> 'authorized'.

    Hard rules (domain-spec §8):
      - authorizer role must be finance/manager (RBAC gate), AND
      - authorizer actor_id must differ from the proposer who drafted it
        (maker-checker).
    A violation is a structured refusal + an action_log row. Still
    draft-not-send: 'authorized' is only a status, nothing is sent.
    """
    action = "authorize_dispute"
    # RBAC gate: reviewer is refused here.
    if not can_do(actor.role, action):
        return _deny(conn, actor, action, "permission_denied", as_of=as_of,
                     review_id=review_id, detail=f"role={actor.role}")

    ensure_dispute_outbox(conn)
    key = idempotency_key(DISPUTE_TARGET_SYSTEM, DISPUTE_ACTION, review_id)
    conn.row_factory = sqlite3.Row
    draft = conn.execute(
        "SELECT proposer_id, status FROM dispute_outbox WHERE idempotency_key=?", (key,)
    ).fetchone()
    if draft is None:
        return ActionResult(False, action, "not_found", "no dispute draft", review_id, key)
    if draft["status"] != "pending":
        return ActionResult(False, action, "invalid_state",
                            f"draft status={draft['status']}", review_id, key)

    # --- maker-checker enforcement (proposer != approver) --------------------
    proposer = Actor(actor_id=draft["proposer_id"] or "", role="reviewer")
    ok, reason = maker_checker(proposer, actor)
    if not ok:
        return _deny(conn, actor, action, reason, as_of=as_of,
                     review_id=review_id, detail=f"proposer={proposer.actor_id}")
    # -------------------------------------------------------------------------

    try:
        with transaction(conn):
            res = transition_dispute(  # participates in this transaction
                conn, key, "authorized", as_of=as_of, actor_id=actor.actor_id,
            )
            if not res.ok:
                raise RuntimeError(f"outbox {res.status}: {res.detail}")
            conn.execute(
                "UPDATE review_queue SET status='authorized' WHERE review_id=?",
                (review_id,),
            )
            record_action(
                conn, actor, action, as_of=as_of,
                target_table="dispute_outbox", target_id=key,
                payload={"proposer_id": proposer.actor_id, "authorizer_id": actor.actor_id},
            )
    except Exception as exc:
        return ActionResult(False, action, "outbox_error", str(exc), review_id, key)
    return ActionResult(True, action, "done", "draft->authorized (not sent)", review_id, key)
