from __future__ import annotations

from pipeline.build_ontology import _ensure_unique_source_events
from pipeline.event_envelope import normalize_event


def test_normalize_milestone_event_keeps_source_identity() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "message_id": "MSG-1",
        "booking_no": "BKG-2026-0001",
        "container_no": "MSCU1234567",
        "event_type": "eta_change",
        "event_classifier": "ACT",
        "event_time": "2026-07-07T12:00:00Z",
    }
    event = normalize_event(raw, transform_version="M3")
    assert event.message_id == "MSG-1"
    assert event.source_system == "carrier_portal"
    assert event.event_type == "eta_change"
    assert event.transform_version == "M3"
    assert event.idempotency_key == "carrier_portal:MSG-1"


def test_normalize_event_rejects_missing_message_id() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "event_type": "eta_change",
        "event_time": "2026-07-07T12:00:00Z",
    }
    try:
        normalize_event(raw, transform_version="M3")
    except ValueError as exc:
        assert str(exc) == "missing_message_id"
    else:
        raise AssertionError("expected missing_message_id")


def test_normalize_event_rejects_missing_source_record_id() -> None:
    raw = {
        "source_system": "carrier_portal",
        "message_id": "MSG-1",
        "event_type": "eta_change",
        "event_time": "2026-07-07T12:00:00Z",
    }
    try:
        normalize_event(raw, transform_version="M3")
    except ValueError as exc:
        assert str(exc) == "missing_source_record_id"
    else:
        raise AssertionError("expected missing_source_record_id")


def test_normalize_event_rejects_missing_event_type() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "message_id": "MSG-1",
        "event_time": "2026-07-07T12:00:00Z",
    }
    try:
        normalize_event(raw, transform_version="M3")
    except ValueError as exc:
        assert str(exc) == "missing_event_type"
    else:
        raise AssertionError("expected missing_event_type")


def test_normalize_event_rejects_missing_event_time() -> None:
    raw = {
        "source_system": "carrier_portal",
        "source_record_id": "SRC-1",
        "message_id": "MSG-1",
        "event_type": "eta_change",
    }
    try:
        normalize_event(raw, transform_version="M3")
    except ValueError as exc:
        assert str(exc) == "missing_event_time"
    else:
        raise AssertionError("expected missing_event_time")


def test_duplicate_idempotency_keys_fail_before_insert() -> None:
    rows = [{"idempotency_key": "carrier:MSG-1"}, {"idempotency_key": "carrier:MSG-1"}]
    try:
        _ensure_unique_source_events(rows)
    except ValueError as exc:
        assert str(exc) == "duplicate_idempotency_key:carrier:MSG-1"
    else:
        raise AssertionError("expected duplicate_idempotency_key")


if __name__ == "__main__":
    test_normalize_milestone_event_keeps_source_identity()
    test_normalize_event_rejects_missing_message_id()
    test_normalize_event_rejects_missing_source_record_id()
    test_normalize_event_rejects_missing_event_type()
    test_normalize_event_rejects_missing_event_time()
    test_duplicate_idempotency_keys_fail_before_insert()
    print("ok")
