"""RBAC matrix + maker-checker for the reconciliation action layer.

Permissions are declared here (domain-spec §8) and ENFORCED in app/actions.py
at the action layer — the UI hiding a button is only cosmetic and never the
authority. maker-checker reuses governance.ApprovalPolicy so there is one
canonical rule for "proposer != approver".

    domain-spec §8 matrix
    action                          reviewer  finance  manager
    view_queue (queue + evidence)      ✓         ✓        ✓
    reject_discrepancy (flag/reject)   ✓         ✓        ✓
    approve_discrepancy (-> draft)     ✓         ✓        ✗
    authorize_dispute (draft->auth)    ✗         ✓        ✓
    view_kpi (KPI board)               ✗         ✗        ✓
    rollback                           ✓         ✓        ✓
"""
from __future__ import annotations

from governance.transaction import Actor, ApprovalPolicy

# Re-export Actor so callers can `from app.rbac import Actor`.
__all__ = ["Actor", "ROLE_PERMS", "ROLES", "ACTIONS", "can_do", "maker_checker", "make_actor"]

ROLES = ("reviewer", "finance", "manager")

ROLE_PERMS: dict[str, frozenset[str]] = {
    "reviewer": frozenset({"view_queue", "reject_discrepancy", "approve_discrepancy", "rollback"}),
    "finance": frozenset({"view_queue", "reject_discrepancy", "approve_discrepancy", "authorize_dispute", "rollback"}),
    "manager": frozenset({"view_queue", "reject_discrepancy", "authorize_dispute", "view_kpi", "rollback"}),
}

ACTIONS = frozenset().union(*ROLE_PERMS.values())

_POLICY = ApprovalPolicy()


def can_do(role: str, action: str) -> bool:
    """True iff `role` is permitted to perform `action` (domain-spec §8)."""
    return action in ROLE_PERMS.get(role, frozenset())


def maker_checker(proposer: Actor, approver: Actor) -> tuple[bool, str]:
    """Delegate to the canonical governance ApprovalPolicy.

    Returns (ok, reason). reason is 'authorized' | 'authorizer_role_required'
    | 'maker_checker_violation'.
    """
    return _POLICY.can_authorize(proposer, approver)


def make_actor(actor_id: str, role: str, display_name: str = "") -> Actor:
    return Actor(actor_id=actor_id, role=role, display_name=display_name or actor_id)
