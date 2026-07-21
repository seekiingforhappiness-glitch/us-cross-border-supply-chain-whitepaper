"""Transactional write boundary for the audit engine.

- `transaction`  : sqlite transaction context manager with SAVEPOINT nesting.
- `Actor`        : named actor shape (RBAC + maker-checker).
- `ApprovalPolicy`: maker-checker + role gate for dispute authorization.
- `next_stable_id`: deterministic id helper (no clock, no rng).

Adapted from the control-tower spine (app/action_context.py) for the
reconciliation domain. Writes are always transactional; nested calls use
savepoints so a partial failure rolls back only its own scope.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

POLICY_VERSION = "freight-audit-maker-checker-v1"


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str  # reviewer | finance | manager
    display_name: str = ""


@dataclass(frozen=True)
class ApprovalPolicy:
    """Dispute authorization gate (domain-spec §8).

    - Only finance/manager may authorize a dispute (draft -> authorized).
    - maker-checker: authorizer.actor_id must differ from proposer.actor_id.
    """

    policy_version: str = POLICY_VERSION

    def can_authorize(self, proposer: Actor, approver: Actor) -> tuple[bool, str]:
        if approver.role not in ("finance", "manager"):
            return False, "authorizer_role_required"
        if proposer.actor_id and proposer.actor_id == approver.actor_id:
            return False, "maker_checker_violation"
        return True, "authorized"


def next_stable_id(prefix: str, entropy: str) -> str:
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-{digest}"


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    """Transactional scope. Nested scope -> SAVEPOINT (partial rollback)."""
    if conn.in_transaction:
        savepoint = "fa_txn"
        conn.execute(f"SAVEPOINT {savepoint}")
        try:
            yield
        except Exception:
            conn.execute(f"ROLLBACK TO {savepoint}")
            conn.execute(f"RELEASE {savepoint}")
            raise
        else:
            conn.execute(f"RELEASE {savepoint}")
        return

    conn.execute("BEGIN")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
