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
def transaction(conn: sqlite3.Connection, immediate: bool = False) -> Iterator[None]:
    """写事务包装器。immediate=True 用 BEGIN IMMEDIATE 立即取写锁（洞1.2 TOCTOU 收窄）：
    让"事务内状态机 SELECT 复检 + 写"在写锁下原子化，堵住"两并发都读到同状态都写入"的窗口
    （单连接单线程下与 BEGIN 行为一致，故既有测试不受影响）。嵌套事务走 SAVEPOINT，immediate
    对嵌套无意义（外层已持锁）。"""
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

    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
