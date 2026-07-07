from __future__ import annotations

from app.work_queue import Owner, assign_owner, sla_state


def test_assign_owner_prefers_matching_role_and_region() -> None:
    roster = [
        Owner(actor_id="u-ops-cn", role="ops", region="CN", active=True),
        Owner(actor_id="u-ops-us", role="ops", region="US", active=True),
        Owner(actor_id="u-fin-us", role="finance", region="US", active=True),
    ]
    owner = assign_owner(required_role="ops", region="US", roster=roster)
    assert owner.actor_id == "u-ops-us"


def test_sla_state_open_before_due_date() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-09") == "open"


def test_sla_state_due_today() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-10") == "due_today"


def test_sla_state_overdue_after_due_date() -> None:
    assert sla_state(due_at="2026-07-10T00:00:00Z", as_of_date="2026-07-11") == "overdue"


if __name__ == "__main__":
    test_assign_owner_prefers_matching_role_and_region()
    test_sla_state_open_before_due_date()
    test_sla_state_due_today()
    test_sla_state_overdue_after_due_date()
    print("ok")
