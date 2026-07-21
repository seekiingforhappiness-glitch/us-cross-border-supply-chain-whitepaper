"""Shared detention / demurrage calculator.

datagen (to price clean D&D lines and to inject over-billing) and recon
(to recompute expected days) MUST both call these — never re-derive the
formula in two places.

domain-spec §4:
    demurrage_days = max(0, (pickup_date - discharge_date).days - free_time_days)
    detention_days = max(0, (return_date - gate_out_date).days - free_time_days)
    charge         = days * per_diem_rate
"""
from __future__ import annotations

from datetime import date


def _to_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _elapsed_days(start: str | date | None, end: str | date | None) -> int | None:
    if start is None or end is None:
        return None
    return (_to_date(end) - _to_date(start)).days


def demurrage_days(discharge_date, pickup_date, free_time_days: int) -> int:
    """Chargeable demurrage days (container sitting at port before pickup)."""
    elapsed = _elapsed_days(discharge_date, pickup_date)
    if elapsed is None:
        return 0
    return max(0, elapsed - int(free_time_days))


def detention_days(gate_out_date, return_date, free_time_days: int) -> int:
    """Chargeable detention days (container held out before empty return)."""
    elapsed = _elapsed_days(gate_out_date, return_date)
    if elapsed is None:
        return 0
    return max(0, elapsed - int(free_time_days))


def dd_charge(days: int, per_diem_rate: float) -> float:
    return round(max(0, int(days)) * float(per_diem_rate), 2)
