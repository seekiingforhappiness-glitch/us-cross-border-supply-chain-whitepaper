from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class CanonicalEvent:
    source_system: str
    source_record_id: str
    message_id: str
    event_type: str
    event_classifier: str
    event_time: str
    booking_no: str | None
    container_no: str | None
    transform_version: str
    payload_json: str

    @property
    def idempotency_key(self) -> str:
        return f"{self.source_system}:{self.message_id}"


def _required_string(raw: dict[str, object], field: str) -> str:
    value = str(raw.get(field) or "")
    if not value:
        raise ValueError(f"missing_{field}")
    return value


def normalize_event(raw: dict[str, object], transform_version: str) -> CanonicalEvent:
    message_id = _required_string(raw, "message_id")
    source_system = _required_string(raw, "source_system")
    source_record_id = _required_string(raw, "source_record_id")
    event_type = _required_string(raw, "event_type")
    event_time = _required_string(raw, "event_time")
    return CanonicalEvent(
        source_system=source_system,
        source_record_id=source_record_id,
        message_id=message_id,
        event_type=event_type,
        event_classifier=str(raw.get("event_classifier") or "ACT"),
        event_time=event_time,
        booking_no=raw.get("booking_no") if isinstance(raw.get("booking_no"), str) else None,
        container_no=raw.get("container_no") if isinstance(raw.get("container_no"), str) else None,
        transform_version=transform_version,
        payload_json=json.dumps(raw, ensure_ascii=False, sort_keys=True),
    )
