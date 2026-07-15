from __future__ import annotations

from pipeline.mdm import CrosswalkEntry, resolve_crosswalk


def test_resolve_exact_external_id() -> None:
    entries = [
        CrosswalkEntry("carrier_portal", "customer", "BESTBUY-US", "CUS-0001", 1.0),
        CrosswalkEntry("erp", "customer", "BBY", "CUS-0001", 0.95),
    ]
    result = resolve_crosswalk(entries, "carrier_portal", "customer", "BESTBUY-US")
    assert result.internal_id == "CUS-0001"
    assert result.status == "resolved"


def test_ambiguous_external_id_is_not_guessed() -> None:
    entries = [
        CrosswalkEntry("erp", "sku", "USB-C-20W", "SKU-0001", 0.7),
        CrosswalkEntry("erp", "sku", "USB-C-20W", "SKU-0002", 0.7),
    ]
    result = resolve_crosswalk(entries, "erp", "sku", "USB-C-20W")
    assert result.internal_id is None
    assert result.status == "ambiguous"


def test_missing_external_id_is_unresolved() -> None:
    result = resolve_crosswalk([], "erp", "vendor", "V-404")
    assert result.internal_id is None
    assert result.status == "unresolved"


if __name__ == "__main__":
    test_resolve_exact_external_id()
    test_ambiguous_external_id_is_not_guessed()
    test_missing_external_id_is_unresolved()
    print("ok")
