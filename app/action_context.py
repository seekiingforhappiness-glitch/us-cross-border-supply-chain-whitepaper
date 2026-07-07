"""Demo action-boundary helpers for named actors and transactional writes.

M1 keeps this intentionally simulation-first: no SSO, no enterprise directory,
and no immutable audit backend. The helper only gives action functions a shared
actor shape, maker-checker policy, deterministic ID helper, and transaction
wrapper.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import sqlite3
from typing import Iterator


@dataclass(frozen=True)
class Actor:
    actor_id: str
    role: str
    display_name: str = ""


@dataclass(frozen=True)
class ApprovalPolicy:
    policy_version: str

    def can_approve(self, proposer: Actor, approver: Actor) -> tuple[bool, str]:
        if approver.role != "manager":
            return False, "manager_role_required"
        if proposer.actor_id and proposer.actor_id == approver.actor_id:
            return False, "maker_checker_violation"
        return True, "approved_by_manager"


def next_stable_id(prefix: str, entropy: str) -> str:
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:10].upper()
    return f"{prefix}-{digest}"


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        savepoint = "m1_action_context_transaction"
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
