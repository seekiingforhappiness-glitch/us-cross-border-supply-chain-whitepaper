"""W3 主入口：python3 -m pipeline.build_ontology

从含噪源表（data/raw/）重建对象层 → data/ontology.sqlite + data/dq_report.json。
铁律（AGENTS §5）：本模块禁止读取 data/truth/。状态一律以事件流为准，不信 tms 状态字段。
"""
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import yaml

from .event_envelope import normalize_event
from .er import resolve, resolve_milestones
from .mdm import CrosswalkEntry, resolve_crosswalk
from .dq_issues import create_unresolved_milestone_issues
from .outbox import ensure_integration_outbox
from engine.graph import upsert_relationship

RAW = Path("data/raw")
DB = Path("data/ontology.sqlite")
DQ = Path("data/dq_report.json")
CFG = Path("config/datagen.yaml")

PHASE = {"delivered": "delivered", "customs_released": "customs", "customs_hold": "customs",
         "customs_filed": "customs", "arrived": "arrived", "transshipment": "in_transit",
         "eta_change": None, "departed": "in_transit", "booking_confirmed": None}
PHASE_RANK = {"planned": 0, "in_transit": 1, "arrived": 2, "customs": 3, "delivered": 4}


def load(name):
    with open(RAW / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_config():
    with open(CFG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _alias(prefix, value):
    slug = "".join(ch if ch.isalnum() else "-" for ch in value.upper())
    slug = "-".join(part for part in slug.split("-") if part)
    return f"{prefix}-{slug}"


def build_mdm_crosswalk_rows(customers, skus, supplier_mapping, mdm_cfg):
    """Build a simulation-only external-id crosswalk around existing deterministic IDs."""
    updated_at = mdm_cfg["updated_at"]
    entries = []

    for r in sorted(customers, key=lambda x: x["customer_id"]):
        entries.append(CrosswalkEntry("oms", "customer", r["customer_id"], r["customer_id"], 1.0))
        entries.append(CrosswalkEntry(
            "carrier_portal", "customer", _alias("CUST", r["customer_name"]),
            r["customer_id"], 0.92))

    for r in sorted(skus, key=lambda x: x["sku_id"]):
        entries.append(CrosswalkEntry("catalog", "sku", r["sku_id"], r["sku_id"], 1.0))
        entries.append(CrosswalkEntry("erp", "sku", f"ERP-{r['sku_id']}", r["sku_id"], 0.98))

    for raw_name, supplier_id in sorted(supplier_mapping.items()):
        if supplier_id:
            entries.append(CrosswalkEntry("tms", "vendor", raw_name, supplier_id, 0.9))

    first_two_skus = [r["sku_id"] for r in sorted(skus, key=lambda x: x["sku_id"])[:2]]
    ambiguous_external_id = mdm_cfg["ambiguous_sku_external_id"]
    for sku_id in first_two_skus:
        entries.append(CrosswalkEntry("erp", "sku", ambiguous_external_id, sku_id, 0.7))

    rows = []
    keys = sorted({(e.source_system, e.object_type, e.external_id) for e in entries})
    for source_system, object_type, external_id in keys:
        result = resolve_crosswalk(entries, source_system, object_type, external_id)
        group = [
            e for e in entries
            if (e.source_system, e.object_type, e.external_id)
            == (source_system, object_type, external_id)
        ]
        for entry in sorted(group, key=lambda e: e.internal_id):
            rows.append({
                "source_system": source_system,
                "object_type": object_type,
                "external_id": external_id,
                "internal_id": entry.internal_id,
                "confidence": entry.confidence,
                "status": result.status,
                "updated_at": updated_at,
            })

    unresolved_result = resolve_crosswalk(
        entries, "erp", "vendor", mdm_cfg["unresolved_vendor_external_id"])
    rows.append({
        "source_system": "erp",
        "object_type": "vendor",
        "external_id": mdm_cfg["unresolved_vendor_external_id"],
        "internal_id": "",
        "confidence": unresolved_result.confidence,
        "status": unresolved_result.status,
        "updated_at": updated_at,
    })
    return rows


def summarize_mdm_crosswalk(rows):
    """Summarize resolver outcomes by external-id lookup key, not candidate rows."""
    status_by_key = {}
    for row in rows:
        key = (row["source_system"], row["object_type"], row["external_id"])
        previous = status_by_key.setdefault(key, row["status"])
        if previous != row["status"]:
            raise ValueError(f"conflicting MDM statuses for {key}: {previous} vs {row['status']}")
    status_counts = Counter(status_by_key.values())
    return {
        "total": len(status_by_key),
        "resolved": status_counts.get("resolved", 0),
        "ambiguous": status_counts.get("ambiguous", 0),
        "unresolved": status_counts.get("unresolved", 0),
        "candidate_rows": len(rows),
    }


def _relationship_id(relationship_type, *parts):
    return "REL-" + relationship_type + "-" + "-".join(str(p) for p in parts)


def build_object_relationship_rows(t, so_rows, line_rows, ship_rows, ms_rows):
    so_customer = {r["so_id"]: r["customer_id"] for r in so_rows}
    rows = []

    def add(source_type, source_id, target_type, target_id, relationship_type, source, rel_key=None):
        key = rel_key or (source_id, target_id)
        rows.append({
            "relationship_id": _relationship_id(relationship_type, *key),
            "source_type": source_type,
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            "relationship_type": relationship_type,
            "confidence": 1.0,
            "source": source,
        })

    for so in sorted(so_rows, key=lambda r: r["so_id"]):
        add("Customer", so["customer_id"], "SalesOrder", so["so_id"], "customer_places",
            "pipeline.build_ontology:sales_orders")

    for line in sorted(line_rows, key=lambda r: r["so_line_id"]):
        add("SalesOrder", line["so_id"], "SalesOrderLine", line["so_line_id"], "so_has_line",
            "pipeline.build_ontology:sales_order_lines")
        add("SalesOrderLine", line["so_line_id"], "Sku", line["sku_id"], "line_for_sku",
            "pipeline.build_ontology:sales_order_lines")
        customer_id = so_customer[line["so_id"]]
        add("SalesOrderLine", line["so_line_id"], "Customer", customer_id,
            "derived_line_belongs_to_customer",
            "pipeline.build_ontology:sales_order_lines")

    for po in sorted(t["srm_purchase_orders"], key=lambda r: r["po_id"]):
        add("Supplier", po["supplier_id"], "PurchaseOrder", po["po_id"], "derived_supplier_has_po",
            "pipeline.build_ontology:purchase_orders")

    for ship in sorted(ship_rows, key=lambda r: r["shipment_id"]):
        for po_id in sorted(pid for pid in ship["po_ids"].split("|") if pid):
            add("PurchaseOrder", po_id, "Shipment", ship["shipment_id"], "po_shipped_by",
                "pipeline.build_ontology:shipments")

    for milestone in sorted(ms_rows, key=lambda r: r["milestone_id"]):
        add("Shipment", milestone["shipment_id"], "ShipmentMilestone", milestone["milestone_id"],
            "shipment_has_milestone", "pipeline.build_ontology:shipment_milestones",
            rel_key=(milestone["milestone_id"],))

    for allocation in sorted(t["tms_allocations"], key=lambda r: r["allocation_id"]):
        add("Shipment", allocation["shipment_id"], "SalesOrderLine", allocation["so_line_id"],
            "derived_shipment_allocates_line", "pipeline.build_ontology:shipment_allocations",
            rel_key=(allocation["allocation_id"],))

    for container in sorted(t["tms_containers"], key=lambda r: r["container_no"]):
        add("Shipment", container["shipment_id"], "Container", container["container_no"],
            "shipment_has_container", "pipeline.build_ontology:containers")

    for invoice in sorted(t["ap_invoices"], key=lambda r: r["invoice_id"]):
        add("Shipment", invoice["shipment_id"], "Invoice", invoice["invoice_id"],
            "derived_shipment_has_invoice", "pipeline.build_ontology:invoices")

    for invoice_line in sorted(t["ap_invoice_lines"], key=lambda r: r["invoice_line_id"]):
        add("Invoice", invoice_line["invoice_id"], "InvoiceLine", invoice_line["invoice_line_id"],
            "invoice_has_line", "pipeline.build_ontology:invoice_lines")

    for expected_cost in sorted(t["ap_expected_costs"], key=lambda r: r["expected_cost_id"]):
        add("Shipment", expected_cost["shipment_id"], "ExpectedCost",
            expected_cost["expected_cost_id"], "derived_shipment_has_expected_cost",
            "pipeline.build_ontology:expected_costs")

    return sorted(rows, key=lambda r: r["relationship_id"])


def derive_shipment_state(sid, events, eta_initial):
    """从（判重后的）事件流推导 shipment 真相。乱序安全：全部按 event_time 排序后处理。"""
    ev = sorted(events, key=lambda e: (e["event_time"], e["milestone_id"]))
    state = {"status": "planned", "eta_current": eta_initial, "ata": None,
             "customs_status": "not_filed", "last_event_time": None}
    for e in ev:
        et = e["event_type"]
        if et == "eta_change" and e["new_eta"]:
            state["eta_current"] = e["new_eta"]
        phase = PHASE.get(et)
        if phase and PHASE_RANK[phase] > PHASE_RANK[state["status"]]:
            state["status"] = phase
        if et == "arrived":
            state["ata"] = e["event_time"][:10]
        if et == "customs_filed" and state["customs_status"] == "not_filed":
            state["customs_status"] = "filed"
        elif et == "customs_hold":
            state["customs_status"] = "hold"
        elif et == "customs_released":
            state["customs_status"] = "released"
        state["last_event_time"] = e["event_time"]
    return state


def source_event_rows(raw_milestones):
    rows = []
    for raw in sorted(raw_milestones, key=lambda r: (r["ingested_at"], r["milestone_id"])):
        event = normalize_event(raw, transform_version="M3")
        rows.append({
            "idempotency_key": event.idempotency_key,
            "source_system": event.source_system,
            "source_record_id": event.source_record_id,
            "message_id": event.message_id,
            "event_type": event.event_type,
            "event_classifier": event.event_classifier,
            "event_time": event.event_time,
            "booking_no": event.booking_no or "",
            "container_no": event.container_no or "",
            "transform_version": event.transform_version,
            "payload_json": event.payload_json,
            "ingested_at": str(raw.get("ingested_at") or ""),
        })
    return rows


def _ensure_unique_source_events(rows):
    seen = set()
    duplicates = []
    for row in rows:
        key = row["idempotency_key"]
        if key in seen:
            duplicates.append(key)
        seen.add(key)
    if duplicates:
        sample = ",".join(sorted(set(duplicates))[:5])
        raise ValueError(f"duplicate_idempotency_key:{sample}")


def main():
    cfg = load_config()
    t = {n: load(n) for n in ["srm_suppliers", "catalog_skus", "oms_customers", "oms_sales_orders",
                              "oms_so_lines", "srm_purchase_orders", "tms_shipments",
                              "tms_milestones", "tms_allocations",
                              "qms_admission_cases", "qms_compliance_findings",
                              "qms_logistics_plans", "qms_cost_scenarios",
                              "tms_containers", "rate_card", "ap_invoices",
                              "ap_invoice_lines", "ap_expected_costs",
                              "srm_po_lines", "srm_goods_receipts",
                              "srm_goods_receipt_lines", "ap_supplier_invoices",
                              "ap_supplier_invoice_lines", "ap_purchase_payments",
                              "srm_supplier_qualifications",
                              "wms_warehouses", "wms_inventory_positions",
                              "wms_inventory_reservations", "wms_cycle_counts"]}
    dq = {"input_rows": {k: len(v) for k, v in t.items()}}
    source_events = source_event_rows(t["tms_milestones"])
    _ensure_unique_source_events(source_events)

    # 0) 单证号级 ER（v0.6-H3）：源表 milestone 无内部 shipment_id，先按 booking_no/
    #    container_no 反查 shipment，补回 shipment_id；解析失败进 unresolved 停车表。
    #    booking_no 在 tms_shipments 唯一；container 经 tms_containers 反查。
    ship_by_booking = {r["booking_no"]: r["shipment_id"]
                       for r in t["tms_shipments"] if r["booking_no"]}
    ship_by_container = {r["container_no"]: r["shipment_id"]
                         for r in t["tms_containers"] if r["container_no"]}
    resolved_ms, unresolved_ms = resolve_milestones(
        t["tms_milestones"], ship_by_booking, ship_by_container)
    _reason_dist = dict(Counter(r["reason"] for r in unresolved_ms))
    _n_in = len(t["tms_milestones"])
    dq["milestone_resolution"] = {
        "total": _n_in,
        "resolved": len(resolved_ms),
        "unresolved": len(unresolved_ms),
        "resolution_rate": round(len(resolved_ms) / _n_in, 4) if _n_in else 1.0,
        "resolved_by_booking": sum(1 for r in resolved_ms if r["resolved_by"] == "booking_no"),
        "resolved_by_container": sum(1 for r in resolved_ms if r["resolved_by"] == "container_no"),
        "unresolved_by_reason": _reason_dist,
    }

    # 1) milestone 判重（四元组保首条，重复标记保留——事件不可变，只标不删）
    seen, dup_count = {}, 0
    ms_rows = []
    for r in sorted(resolved_ms, key=lambda x: (x["ingested_at"], x["milestone_id"])):
        key = (r["shipment_id"], r["event_type"], r["event_time"], r["source_system"])
        r = dict(r)
        r["is_duplicate"] = key in seen
        if r["is_duplicate"]:
            dup_count += 1
        else:
            seen[key] = r["milestone_id"]
        ms_rows.append(r)
    dq["milestone_duplicates_flagged"] = dup_count

    # 乱序度量：同一 shipment 内 ingested_at 顺序与 event_time 顺序的逆序对
    ooo = 0
    by_ship = defaultdict(list)
    for r in ms_rows:
        if not r["is_duplicate"]:
            by_ship[r["shipment_id"]].append(r)
    for sid, evs in by_ship.items():
        ing = sorted(evs, key=lambda e: e["ingested_at"])
        for i in range(len(ing)):
            for j in range(i + 1, len(ing)):
                if ing[i]["event_time"] > ing[j]["event_time"]:
                    ooo += 1
    dq["out_of_order_pairs"] = ooo

    # 2) shipment 状态重建（不信 tms 状态字段）
    ship_rows, conflicts, null_vessel = [], 0, 0
    for r in sorted(t["tms_shipments"], key=lambda x: x["shipment_id"]):
        st = derive_shipment_state(r["shipment_id"], by_ship.get(r["shipment_id"], []),
                                   r["eta_initial"])
        if st["status"] != r["status"]:
            conflicts += 1
        if not r["vessel_voyage"] or not r["carrier_name"]:
            null_vessel += 1
        eta_i, eta_c = r["eta_initial"], st["eta_current"]
        delay = (__import__("datetime").date.fromisoformat(eta_c)
                 - __import__("datetime").date.fromisoformat(eta_i)).days
        ship_rows.append({**r, "status_source": r["status"], "status": st["status"],
                          "eta_current": eta_c, "ata": st["ata"],
                          "customs_status": st["customs_status"], "delay_days": delay,
                          "last_event_time": st["last_event_time"], "expedite_flag": 0})
    dq["status_conflicts_corrected"] = conflicts
    dq["null_vessel_or_carrier"] = null_vessel

    # 3) 供应商 ER：TMS 变体名 → SRM supplier_id
    raw_names = set()
    for r in t["tms_shipments"]:
        raw_names.update(n for n in r["supplier_names"].split("|") if n)
    canonical = {r["supplier_id"]: r["supplier_name"] for r in t["srm_suppliers"]}
    mapping, ambiguous = resolve(raw_names, canonical)
    dq["er_names_total"] = len(mapping)
    dq["er_mapped"] = sum(1 for v in mapping.values() if v)
    dq["er_unmapped"] = sorted(k for k, v in mapping.items() if not v)
    dq["er_ambiguous_canonical"] = sorted(ambiguous)
    mdm_rows = build_mdm_crosswalk_rows(
        t["oms_customers"], t["catalog_skus"], mapping, cfg["mdm"])
    dq["mdm_crosswalk"] = summarize_mdm_crosswalk(mdm_rows)

    # 4) 行/订单状态推导
    ship_status = {r["shipment_id"]: r["status"] for r in ship_rows}
    allocs_of_line = defaultdict(list)
    orphan_allocs = 0
    line_ids = {r["so_line_id"] for r in t["oms_so_lines"]}
    for a in t["tms_allocations"]:
        if a["shipment_id"] not in ship_status or a["so_line_id"] not in line_ids:
            orphan_allocs += 1
            continue
        allocs_of_line[a["so_line_id"]].append(a["shipment_id"])
    dq["orphan_allocations"] = orphan_allocs
    line_rows = []
    for r in sorted(t["oms_so_lines"], key=lambda x: x["so_line_id"]):
        sids = allocs_of_line.get(r["so_line_id"], [])
        if not sids:
            status = "open"
        elif all(ship_status[s] == "delivered" for s in sids):
            status = "fulfilled"
        else:
            status = "allocated"
        line_rows.append({**r, "original_promised_date": r["promised_delivery_date"],
                          "reschedule_count": 0, "line_status": status})
    so_status = {}
    lines_of_so = defaultdict(list)
    for r in line_rows:
        lines_of_so[r["so_id"]].append(r["line_status"])
    for so, sts in lines_of_so.items():
        so_status[so] = ("fulfilled" if all(s == "fulfilled" for s in sts)
                         else "in_fulfillment" if any(s != "open" for s in sts) else "open")
    so_rows = [{**r, "status": so_status.get(r["so_id"], "open")}
               for r in sorted(t["oms_sales_orders"], key=lambda x: x["so_id"])]

    # 空值率统计
    dq["null_rates"] = {
        "tms.vessel_voyage": round(sum(1 for r in t["tms_shipments"] if not r["vessel_voyage"])
                                   / len(t["tms_shipments"]), 3),
        "tms.carrier_name": round(sum(1 for r in t["tms_shipments"] if not r["carrier_name"])
                                  / len(t["tms_shipments"]), 3),
    }

    # 5) 写 ontology.sqlite
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    cur = con.cursor()

    def table(name, rows, cols, pk):
        cur.execute(f"CREATE TABLE {name} ({', '.join(cols)}, PRIMARY KEY ({pk}))")
        cur.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * len(cols))})",
                        [[r.get(c.split()[0], "") for c in cols] for r in rows])

    cur.execute("""create table if not exists source_events (
        idempotency_key text primary key,
        source_system text not null,
        source_record_id text not null,
        message_id text not null,
        event_type text not null,
        event_classifier text not null,
        event_time text not null,
        booking_no text,
        container_no text,
        transform_version text not null,
        payload_json text not null,
        ingested_at text not null
    )""")
    cur.executemany("""insert into source_events (
        idempotency_key, source_system, source_record_id, message_id, event_type,
        event_classifier, event_time, booking_no, container_no, transform_version,
        payload_json, ingested_at
    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [
        [r["idempotency_key"], r["source_system"], r["source_record_id"], r["message_id"],
         r["event_type"], r["event_classifier"], r["event_time"], r["booking_no"],
         r["container_no"], r["transform_version"], r["payload_json"], r["ingested_at"]]
        for r in source_events
    ])
    dq["source_events"] = {
        "raw_milestones": len(t["tms_milestones"]),
        "inserted": cur.execute("select count(*) from source_events").fetchone()[0],
        "duplicate_idempotency_keys": len(source_events) - len({r["idempotency_key"] for r in source_events}),
    }

    table("suppliers", t["srm_suppliers"],
          ["supplier_id TEXT", "supplier_name TEXT", "city TEXT", "lead_time_days INTEGER",
           "factory_audit_status TEXT", "compliance_docs_status TEXT",
           "uflpa_risk_flag TEXT", "origin_evidence_status TEXT"],
          "supplier_id")
    table("skus", t["catalog_skus"],
          ["sku_id TEXT", "sku_name TEXT", "category TEXT", "unit_price_usd REAL", "supplier_id TEXT",
           "sku_status TEXT", "declared_value_usd TEXT", "package_weight_kg TEXT",
           "package_l_cm TEXT", "package_w_cm TEXT", "package_h_cm TEXT", "battery_flag TEXT",
           "food_contact_flag TEXT", "children_product_flag TEXT", "material TEXT",
           "use_case TEXT", "origin_country TEXT", "platform TEXT"],
          "sku_id")
    table("customers", t["oms_customers"],
          ["customer_id TEXT", "customer_name TEXT", "tier TEXT", "us_state TEXT",
           "business_model TEXT", "sales_channel TEXT", "ior_capability TEXT",
           "broker_status TEXT", "credit_terms TEXT", "risk_tier TEXT"], "customer_id")
    table("sales_orders", so_rows,
          ["so_id TEXT", "customer_id TEXT", "order_date TEXT", "status TEXT"], "so_id")
    table("sales_order_lines", line_rows,
          ["so_line_id TEXT", "so_id TEXT", "sku_id TEXT", "qty INTEGER", "unit_price_usd REAL",
           "promised_delivery_date TEXT", "original_promised_date TEXT",
           "reschedule_count INTEGER", "line_status TEXT"], "so_line_id")
    table("purchase_orders", sorted(t["srm_purchase_orders"], key=lambda x: x["po_id"]),
          ["po_id TEXT", "supplier_id TEXT", "sku_id TEXT", "qty INTEGER", "po_date TEXT",
           "expected_ready_date TEXT", "status TEXT"], "po_id")
    table("shipments", ship_rows,
          ["shipment_id TEXT", "booking_no TEXT", "mbl_no TEXT", "mode TEXT", "container_no TEXT",
           "container_type TEXT", "vessel_voyage TEXT", "carrier_name TEXT", "carrier_scac TEXT",
           "incoterm TEXT", "gross_weight_kg REAL", "volume_cbm REAL", "origin_port TEXT",
           "origin_port_locode TEXT", "destination_port TEXT", "destination_port_locode TEXT",
           "destination_warehouse TEXT", "etd TEXT", "eta_initial TEXT", "eta_current TEXT",
           "ata TEXT", "customs_status TEXT", "missing_docs TEXT", "delay_days INTEGER",
           "status TEXT", "status_source TEXT", "last_event_time TEXT", "expedite_flag INTEGER",
           "po_ids TEXT"], "shipment_id")
    table("shipment_milestones", ms_rows,
          ["milestone_id TEXT", "shipment_id TEXT", "event_type TEXT", "event_classifier TEXT",
           "event_time TEXT", "event_locode TEXT", "new_eta TEXT", "source_system TEXT",
           "ingested_at TEXT", "is_duplicate INTEGER"], "milestone_id")
    # v0.6-H3 unresolved 停车表：单证号无法解析的 milestone 原行全列保留 + reason
    # （不丢弃、不猜；下游控制塔不消费，仅供 DQ / evaluate 审计）
    table("unresolved_milestones", unresolved_ms,
          ["milestone_id TEXT", "booking_no TEXT", "container_no TEXT", "event_type TEXT",
           "event_classifier TEXT", "event_time TEXT", "event_locode TEXT", "new_eta TEXT",
           "source_system TEXT", "ingested_at TEXT", "reason TEXT"], "milestone_id")
    table("shipment_allocations", sorted(t["tms_allocations"], key=lambda x: x["allocation_id"]),
          ["allocation_id TEXT", "shipment_id TEXT", "so_line_id TEXT", "allocated_qty INTEGER"],
          "allocation_id")
    # v0.3 准入四表（工作流数据，qms 为记录系统，直通加载 + 引用完整性入 DQ）
    table("admission_cases", sorted(t["qms_admission_cases"], key=lambda x: x["admission_case_id"]),
          ["admission_case_id TEXT", "case_title TEXT", "customer_id TEXT", "sku_id TEXT",
           "request_type TEXT", "incoterm_candidate TEXT", "target_launch_date TEXT",
           "monthly_order_estimate INTEGER", "risk_level TEXT", "status TEXT",
           "decision TEXT", "decision_reason TEXT", "conditions TEXT"], "admission_case_id")
    table("compliance_findings", sorted(t["qms_compliance_findings"],
                                        key=lambda x: x["compliance_finding_id"]),
          ["compliance_finding_id TEXT", "admission_case_id TEXT", "finding_title TEXT",
           "finding_type TEXT", "severity TEXT", "hts_candidate TEXT", "pga_agency TEXT",
           "required_document TEXT", "evidence_status TEXT", "recommendation TEXT"],
          "compliance_finding_id")
    table("logistics_plans", sorted(t["qms_logistics_plans"], key=lambda x: x["logistics_plan_id"]),
          ["logistics_plan_id TEXT", "admission_case_id TEXT", "plan_name TEXT", "route_type TEXT",
           "incoterm TEXT", "origin_port_locode TEXT", "destination_port_locode TEXT",
           "us_warehouse_region TEXT", "last_mile_method TEXT", "estimated_transit_days INTEGER",
           "sla_risk TEXT", "operational_notes TEXT"], "logistics_plan_id")
    table("cost_scenarios", sorted(t["qms_cost_scenarios"], key=lambda x: x["cost_scenario_id"]),
          ["cost_scenario_id TEXT", "logistics_plan_id TEXT", "scenario_type TEXT",
           "quote_price_usd REAL", "product_cost_usd REAL", "first_mile_cost_usd REAL",
           "international_freight_usd REAL", "duty_tax_usd REAL", "customs_brokerage_usd REAL",
           "warehouse_cost_usd REAL", "last_mile_cost_usd REAL", "returns_allowance_usd REAL",
           "risk_buffer_usd REAL", "gross_margin_usd REAL", "gross_margin_rate REAL"],
          "cost_scenario_id")
    case_ids = {r["admission_case_id"] for r in t["qms_admission_cases"]}
    dq["admission_orphan_findings"] = sum(
        1 for r in t["qms_compliance_findings"] if r["admission_case_id"] not in case_ids)
    dq["admission_orphan_plans"] = sum(
        1 for r in t["qms_logistics_plans"] if r["admission_case_id"] not in case_ids)

    # v0.4 费用对账五表（ap/tms/rate_card 为记录系统，直通加载 + 引用完整性入 DQ）
    table("containers", sorted(t["tms_containers"], key=lambda x: x["container_no"]),
          ["container_no TEXT", "shipment_id TEXT", "container_type TEXT", "is_primary TEXT",
           "free_days INTEGER", "gross_weight_kg REAL", "volume_cbm REAL"], "container_no")
    rate_card_rows = [{**r, "rate_card_id": f"RC-{i:04d}"}
                      for i, r in enumerate(t["rate_card"], 1)]
    table("rate_card", rate_card_rows,
          ["rate_card_id TEXT", "charge_code TEXT", "scope TEXT", "key TEXT", "rate_usd REAL"],
          "rate_card_id")
    table("invoices", sorted(t["ap_invoices"], key=lambda x: x["invoice_id"]),
          ["invoice_id TEXT", "vendor_type TEXT", "vendor_name TEXT", "vendor_invoice_no TEXT",
           "shipment_id TEXT", "issue_date TEXT", "currency TEXT", "total_usd REAL",
           "status TEXT"], "invoice_id")
    table("invoice_lines", sorted(t["ap_invoice_lines"], key=lambda x: x["invoice_line_id"]),
          ["invoice_line_id TEXT", "invoice_id TEXT", "charge_code TEXT", "container_no TEXT",
           "qty INTEGER", "unit_price_usd REAL", "amount_usd REAL"], "invoice_line_id")
    table("expected_costs", sorted(t["ap_expected_costs"], key=lambda x: x["expected_cost_id"]),
          ["expected_cost_id TEXT", "shipment_id TEXT", "charge_code TEXT", "container_no TEXT",
           "baseline_usd REAL", "source TEXT"], "expected_cost_id")

    # P1 采购三方对账五表（srm/ap 为记录系统，直通加载 + 引用完整性入 DQ）
    # PoLine 覆盖 D2 单 SKU 约束：采购单行为一等对象，引用既有 purchase_orders.po_id
    table("po_lines", sorted(t["srm_po_lines"], key=lambda x: x["po_line_id"]),
          ["po_line_id TEXT", "po_id TEXT", "sku_id TEXT", "qty INTEGER", "unit_price_usd REAL",
           "currency TEXT", "expected_ready_date TEXT", "line_status TEXT", "as_of_date TEXT",
           "created_at TEXT"], "po_line_id")
    table("goods_receipts", sorted(t["srm_goods_receipts"], key=lambda x: x["grn_id"]),
          ["grn_id TEXT", "po_id TEXT", "received_date TEXT", "status TEXT", "as_of_date TEXT",
           "created_at TEXT"], "grn_id")
    table("goods_receipt_lines", sorted(t["srm_goods_receipt_lines"], key=lambda x: x["grn_line_id"]),
          ["grn_line_id TEXT", "grn_id TEXT", "po_line_id TEXT", "received_qty INTEGER",
           "accepted_qty INTEGER", "rejected_qty INTEGER", "qc_status TEXT", "defect_ppm INTEGER",
           "received_date TEXT", "as_of_date TEXT", "created_at TEXT"], "grn_line_id")
    table("supplier_invoices", sorted(t["ap_supplier_invoices"], key=lambda x: x["supplier_invoice_id"]),
          ["supplier_invoice_id TEXT", "supplier_id TEXT", "po_id TEXT", "vendor_invoice_no TEXT",
           "issue_date TEXT", "currency TEXT", "total_usd REAL", "status TEXT", "as_of_date TEXT",
           "created_at TEXT"], "supplier_invoice_id")
    table("supplier_invoice_lines",
          sorted(t["ap_supplier_invoice_lines"], key=lambda x: x["supplier_invoice_line_id"]),
          ["supplier_invoice_line_id TEXT", "supplier_invoice_id TEXT", "po_line_id TEXT",
           "qty INTEGER", "unit_price_usd REAL", "amount_usd REAL", "as_of_date TEXT",
           "created_at TEXT"], "supplier_invoice_line_id")
    # P2 采购富化两表：预付款（R12）+ 供应商资质（R13）；直通加载 + 引用完整性入 DQ
    table("purchase_payments", sorted(t["ap_purchase_payments"], key=lambda x: x["payment_id"]),
          ["payment_id TEXT", "po_id TEXT", "payment_type TEXT", "amount_usd REAL",
           "paid_date TEXT", "exposure_status TEXT", "as_of_date TEXT", "created_at TEXT"],
          "payment_id")
    table("supplier_qualifications",
          sorted(t["srm_supplier_qualifications"], key=lambda x: x["qualification_id"]),
          ["qualification_id TEXT", "supplier_id TEXT", "cert_type TEXT", "evidence_status TEXT",
           "valid_from TEXT", "valid_to TEXT", "status TEXT", "as_of_date TEXT", "created_at TEXT"],
          "qualification_id")

    # 采购侧 DQ（引用完整性——应全为 0；total 不平也应为 0）
    po_id_set = {r["po_id"] for r in t["srm_purchase_orders"]}
    pol_id_set = {r["po_line_id"] for r in t["srm_po_lines"]}
    grn_id_set = {r["grn_id"] for r in t["srm_goods_receipts"]}
    sinv_id_set = {r["supplier_invoice_id"] for r in t["ap_supplier_invoices"]}
    sup_id_set = {r["supplier_id"] for r in t["srm_suppliers"]}
    sil_sum = defaultdict(float)
    for r in t["ap_supplier_invoice_lines"]:
        sil_sum[r["supplier_invoice_id"]] += float(r["amount_usd"])
    dq["procurement"] = {
        "po_lines": len(t["srm_po_lines"]),
        "goods_receipts": len(t["srm_goods_receipts"]),
        "goods_receipt_lines": len(t["srm_goods_receipt_lines"]),
        "supplier_invoices": len(t["ap_supplier_invoices"]),
        "supplier_invoice_lines": len(t["ap_supplier_invoice_lines"]),
        "po_line_orphans": sum(1 for r in t["srm_po_lines"] if r["po_id"] not in po_id_set),
        "grn_orphans": sum(1 for r in t["srm_goods_receipts"] if r["po_id"] not in po_id_set),
        "grn_line_orphans": sum(1 for r in t["srm_goods_receipt_lines"]
                                if r["grn_id"] not in grn_id_set or r["po_line_id"] not in pol_id_set),
        "supplier_invoice_orphans": sum(1 for r in t["ap_supplier_invoices"]
                                        if r["po_id"] not in po_id_set
                                        or r["supplier_id"] not in sup_id_set),
        "supplier_invoice_line_orphans": sum(
            1 for r in t["ap_supplier_invoice_lines"]
            if r["supplier_invoice_id"] not in sinv_id_set or r["po_line_id"] not in pol_id_set),
        "supplier_invoice_total_imbalance": sum(
            1 for r in t["ap_supplier_invoices"]
            if abs(float(r["total_usd"]) - round(sil_sum[r["supplier_invoice_id"]], 2)) > 0.02),
        # P2 富化：预付款/资质规模 + 引用完整性（应全为 0）
        "purchase_payments": len(t["ap_purchase_payments"]),
        "supplier_qualifications": len(t["srm_supplier_qualifications"]),
        "payment_orphans": sum(1 for r in t["ap_purchase_payments"] if r["po_id"] not in po_id_set),
        "qualification_orphans": sum(1 for r in t["srm_supplier_qualifications"]
                                     if r["supplier_id"] not in sup_id_set),
    }

    # W1 仓储库存主线四表（wms 为记录系统，直通加载 + 引用完整性入 DQ）
    # 库存粒度 SKU×仓库（决策 W1）；position/reservation/cycle_count 挂 Warehouse/SalesOrderLine
    table("warehouses", sorted(t["wms_warehouses"], key=lambda x: x["warehouse_id"]),
          ["warehouse_id TEXT", "type TEXT", "operator TEXT", "region TEXT",
           "capacity_units INTEGER", "as_of_date TEXT"], "warehouse_id")
    table("inventory_positions", sorted(t["wms_inventory_positions"],
                                        key=lambda x: x["inventory_position_id"]),
          ["inventory_position_id TEXT", "sku_id TEXT", "warehouse_id TEXT",
           "available_qty INTEGER", "reserved_qty INTEGER", "in_transit_qty INTEGER",
           "quarantine_qty INTEGER", "safety_stock INTEGER", "as_of_date TEXT"],
          "inventory_position_id")
    table("inventory_reservations", sorted(t["wms_inventory_reservations"],
                                           key=lambda x: x["reservation_id"]),
          ["reservation_id TEXT", "so_line_id TEXT", "inventory_position_id TEXT",
           "qty INTEGER", "status TEXT", "as_of_date TEXT"], "reservation_id")
    table("cycle_counts", sorted(t["wms_cycle_counts"], key=lambda x: x["cycle_count_id"]),
          ["cycle_count_id TEXT", "inventory_position_id TEXT", "warehouse_id TEXT",
           "system_qty INTEGER", "counted_qty INTEGER", "variance INTEGER", "status TEXT",
           "as_of_date TEXT"], "cycle_count_id")

    # 仓储侧 DQ（引用完整性——应全为 0）
    wh_id_set = {r["warehouse_id"] for r in t["wms_warehouses"]}
    invpos_id_set = {r["inventory_position_id"] for r in t["wms_inventory_positions"]}
    line_id_set = {r["so_line_id"] for r in t["oms_so_lines"]}
    sku_id_set = {r["sku_id"] for r in t["catalog_skus"]}
    dq["warehouse"] = {
        "warehouses": len(t["wms_warehouses"]),
        "inventory_positions": len(t["wms_inventory_positions"]),
        "inventory_reservations": len(t["wms_inventory_reservations"]),
        "cycle_counts": len(t["wms_cycle_counts"]),
        "position_wh_orphans": sum(1 for r in t["wms_inventory_positions"]
                                   if r["warehouse_id"] not in wh_id_set),
        "position_sku_orphans": sum(1 for r in t["wms_inventory_positions"]
                                    if r["sku_id"] not in sku_id_set),
        "reservation_orphans": sum(1 for r in t["wms_inventory_reservations"]
                                   if r["inventory_position_id"] not in invpos_id_set
                                   or r["so_line_id"] not in line_id_set),
        "cycle_count_orphans": sum(1 for r in t["wms_cycle_counts"]
                                   if r["inventory_position_id"] not in invpos_id_set
                                   or r["warehouse_id"] not in wh_id_set),
    }

    relationship_rows = build_object_relationship_rows(t, so_rows, line_rows, ship_rows, ms_rows)
    for r in relationship_rows:
        upsert_relationship(con, r["relationship_id"], r["source_type"], r["source_id"],
                            r["target_type"], r["target_id"], r["relationship_type"],
                            r["confidence"], r["source"])
    relationship_type_counts = Counter(r["relationship_type"] for r in relationship_rows)
    dq["object_relationships"] = {
        "total": len(relationship_rows),
        "by_type": dict(sorted(relationship_type_counts.items())),
    }

    # 费用侧 DQ（P1 附近）：孤儿行、total 不平、柜孤儿——应全为 0
    ship_id_set = {r["shipment_id"] for r in t["tms_shipments"]}
    inv_id_set = {r["invoice_id"] for r in t["ap_invoices"]}
    cont_id_set = {r["container_no"] for r in t["tms_containers"]}
    dq["invoice_line_orphans"] = sum(1 for r in t["ap_invoice_lines"]
                                     if r["invoice_id"] not in inv_id_set)
    lines_sum = defaultdict(float)
    for r in t["ap_invoice_lines"]:
        lines_sum[r["invoice_id"]] += float(r["amount_usd"])
    dq["invoice_total_imbalance"] = sum(
        1 for r in t["ap_invoices"]
        if abs(float(r["total_usd"]) - round(lines_sum[r["invoice_id"]], 2)) > 0.02)
    dq["container_orphans"] = sum(1 for r in t["tms_containers"]
                                  if r["shipment_id"] not in ship_id_set)
    dq["invoice_shipment_orphans"] = sum(1 for r in t["ap_invoices"]
                                         if r["shipment_id"] not in ship_id_set)
    dq["invoice_line_container_orphans"] = sum(
        1 for r in t["ap_invoice_lines"]
        if r["container_no"] and r["container_no"] not in cont_id_set)

    cur.execute("CREATE TABLE supplier_name_map (raw_name TEXT PRIMARY KEY, supplier_id TEXT)")
    cur.executemany("INSERT INTO supplier_name_map VALUES (?,?)",
                    [(k, v or "") for k, v in sorted(mapping.items())])
    cur.execute("""create table if not exists mdm_crosswalk (
        source_system text not null,
        object_type text not null,
        external_id text not null,
        internal_id text not null,
        confidence real not null,
        status text not null,
        updated_at text not null,
        primary key (source_system, object_type, external_id, internal_id)
    )""")
    cur.executemany("""insert into mdm_crosswalk (
        source_system, object_type, external_id, internal_id,
        confidence, status, updated_at
    ) values (?, ?, ?, ?, ?, ?, ?)""", [
        [r["source_system"], r["object_type"], r["external_id"], r["internal_id"],
         r["confidence"], r["status"], r["updated_at"]]
        for r in mdm_rows
    ])
    # W4/W5 空表（schema 与 ontology JSON 一致）
    # P1：risk_events 增可空字段 affected_invoice_line_ids（费用场景专用，控制塔留空）
    # P1（采购）：RiskEvent 锚点泛化——增可空 po_id/supplier_id/affected_po_line_ids
    #   （唯一触碰核心对象处，与"加字段不联表"哲学一致）；shipment_id 随之变可空。
    #   既有列顺序不动，新列追加在尾部；R1-R6 写入走显式列名，不受影响。
    # W1（仓储）：RiskEvent 锚点再泛化——增可空 warehouse_id（仿 po_id 锚点，既有列不动，追加尾部）。
    #   R16-R18 用 warehouse_id + affected_so_line_ids（承载受影响业务对象 id：R16=InventoryPosition、
    #   R17=SalesOrderLine、R18=CycleCount，与采购 affected_po_line_ids 同构，决策 W1 仅批 warehouse_id）。
    cur.execute("""CREATE TABLE risk_events (risk_event_id TEXT PRIMARY KEY, type TEXT,
        rule_id TEXT, severity TEXT, shipment_id TEXT, affected_so_line_ids TEXT,
        affected_value_usd REAL, detected_at TEXT, root_cause TEXT, status TEXT,
        resolved_at TEXT, outcome TEXT, resolution_summary TEXT,
        affected_invoice_line_ids TEXT,
        po_id TEXT, supplier_id TEXT, affected_po_line_ids TEXT, warehouse_id TEXT)""")
    cur.execute("""CREATE TABLE tasks (task_id TEXT PRIMARY KEY, risk_event_id TEXT, title TEXT,
        assignee_role TEXT, priority TEXT, due_at TEXT, proposed_action TEXT,
        proposal_params TEXT, approval_status TEXT, approved_by_role TEXT,
        action_taken TEXT, status TEXT, assigned_by_actor_id TEXT,
        proposal_actor_id TEXT, proposal_actor_role TEXT, assignee_user_id TEXT,
        assignee_team_id TEXT, sla_state TEXT, escalation_level INTEGER DEFAULT 0,
        policy_version TEXT)""")
    cur.execute("""CREATE TABLE action_log (log_id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT,
        role TEXT, action TEXT, target_object_id TEXT, params_json TEXT, as_of_date TEXT,
        timestamp TEXT, result TEXT)""")
    ensure_integration_outbox(con)
    cur.execute("""CREATE TABLE dq_issues (
        dq_issue_id TEXT PRIMARY KEY,
        source_table TEXT NOT NULL,
        source_record_id TEXT NOT NULL,
        issue_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        status TEXT NOT NULL,
        assignee_user_id TEXT,
        resolution TEXT,
        detail_json TEXT,
        created_at TEXT,
        closed_at TEXT,
        policy_version TEXT
    )""")
    create_unresolved_milestone_issues(con, unresolved_ms)
    dq_issue_rows = con.execute(
        "SELECT source_table, issue_type, status FROM dq_issues").fetchall()
    dq["dq_issues"] = {
        "total": len(dq_issue_rows),
        "open": sum(1 for _, _, status in dq_issue_rows if status == "open"),
        "by_issue_type": dict(sorted(Counter(issue_type for _, issue_type, _ in dq_issue_rows).items())),
        "by_source_table": dict(sorted(Counter(source_table for source_table, _, _ in dq_issue_rows).items())),
    }
    con.commit()
    con.close()

    dq["output_rows"] = {"shipments": len(ship_rows), "milestones": len(ms_rows),
                         "so_lines": len(line_rows), "sales_orders": len(so_rows)}
    DQ.write_text(json.dumps(dq, ensure_ascii=False, indent=1))
    print(json.dumps(dq, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
