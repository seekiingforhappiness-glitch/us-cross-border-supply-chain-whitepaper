from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Owner:
    actor_id: str
    role: str
    region: str
    active: bool


def assign_owner(required_role: str, region: str, roster: list[Owner]) -> Owner:
    for owner in roster:
        if owner.active and owner.role == required_role and owner.region == region:
            return owner
    for owner in roster:
        if owner.active and owner.role == required_role:
            return owner
    raise ValueError(f"no_active_owner_for_role:{required_role}")


def _parse_utc_date(value: str) -> date:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def sla_state(due_at: str, as_of_date: str) -> str:
    due = _parse_utc_date(due_at)
    current = date.fromisoformat(as_of_date)
    if current < due:
        return "open"
    if current == due:
        return "due_today"
    return "overdue"
