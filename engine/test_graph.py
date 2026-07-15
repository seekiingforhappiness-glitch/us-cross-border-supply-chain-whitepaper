"""M5 graph helper tests: python3 -m engine.test_graph."""
import json
import sqlite3
from pathlib import Path

from .graph import explain_path, upsert_relationship


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(name)


def test_explain_path_finds_stable_bfs_path():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    upsert_relationship(con, "REL-2", "SalesOrderLine", "SOL-1", "Customer", "CUS-1",
                        "belongs_to_customer", 1.0, "test")
    upsert_relationship(con, "REL-1", "Shipment", "SHP-1", "SalesOrderLine", "SOL-1",
                        "allocates", 1.0, "test")

    path = explain_path(con, "Shipment", "SHP-1", "Customer", "CUS-1", max_depth=3)

    check("Shipment -> SalesOrderLine -> Customer path found",
          [edge.relationship_id for edge in path] == ["REL-1", "REL-2"],
          str([edge.relationship_id for edge in path]))


def test_explain_path_returns_empty_when_depth_too_small():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    upsert_relationship(con, "REL-1", "Shipment", "SHP-1", "SalesOrderLine", "SOL-1",
                        "allocates", 1.0, "test")
    upsert_relationship(con, "REL-2", "SalesOrderLine", "SOL-1", "Customer", "CUS-1",
                        "belongs_to_customer", 1.0, "test")

    path = explain_path(con, "Shipment", "SHP-1", "Customer", "CUS-1", max_depth=1)

    check("max_depth prevents longer path", path == [], str(path))


def test_detected_risk_relationships_if_present():
    db_path = Path("data/ontology.sqlite")
    if not db_path.exists():
        print("  [SKIP] detected risk graph edges: data/ontology.sqlite not present")
        return
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    has_tables = con.execute("""
        SELECT count(*) FROM sqlite_master
        WHERE type='table' AND name IN ('risk_events', 'object_relationships')
    """).fetchone()[0]
    if has_tables < 2:
        print("  [SKIP] detected risk graph edges: risk_events/object_relationships absent")
        return
    risks = list(con.execute("""
        SELECT risk_event_id, shipment_id, affected_so_line_ids, affected_invoice_line_ids
        FROM risk_events ORDER BY risk_event_id LIMIT 10
    """))
    if not risks:
        print("  [SKIP] detected risk graph edges: no risk_events yet")
        return

    missing = []
    for risk in risks:
        expected_edges = [("risk_on_shipment", risk["shipment_id"])]
        expected_edges.extend(
            ("risk_affects_line", so_line_id)
            for so_line_id in json.loads(risk["affected_so_line_ids"])
        )
        if risk["affected_invoice_line_ids"]:
            expected_edges.extend(
                ("risk_affects_invoice_line", invoice_line_id)
                for invoice_line_id in json.loads(risk["affected_invoice_line_ids"])
            )
        for relationship_type, target_id in expected_edges:
            found = con.execute("""
                SELECT count(*) FROM object_relationships
                WHERE source_type='RiskEvent' AND source_id=?
                  AND relationship_type=? AND target_id=?
            """, (risk["risk_event_id"], relationship_type, target_id)).fetchone()[0]
            if not found:
                missing.append((risk["risk_event_id"], relationship_type, target_id))
    check("detected RiskEvent graph edges exist when risk_events are present",
          not missing, str(missing[:10]))


def main():
    print("== M5 graph helper tests ==")
    test_explain_path_finds_stable_bfs_path()
    test_explain_path_returns_empty_when_depth_too_small()
    test_detected_risk_relationships_if_present()
    print("\n结果: 全部通过")


if __name__ == "__main__":
    main()
