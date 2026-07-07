from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CrosswalkEntry:
    source_system: str
    object_type: str
    external_id: str
    internal_id: str
    confidence: float


@dataclass(frozen=True)
class CrosswalkResult:
    internal_id: str | None
    status: str
    confidence: float


def resolve_crosswalk(
    entries: list[CrosswalkEntry],
    source_system: str,
    object_type: str,
    external_id: str,
) -> CrosswalkResult:
    matches = [
        entry
        for entry in entries
        if entry.source_system == source_system
        and entry.object_type == object_type
        and entry.external_id == external_id
    ]
    if not matches:
        return CrosswalkResult(None, "unresolved", 0.0)
    internal_ids = {entry.internal_id for entry in matches}
    if len(internal_ids) > 1:
        return CrosswalkResult(None, "ambiguous", max(entry.confidence for entry in matches))
    best = max(matches, key=lambda entry: entry.confidence)
    return CrosswalkResult(best.internal_id, "resolved", best.confidence)
