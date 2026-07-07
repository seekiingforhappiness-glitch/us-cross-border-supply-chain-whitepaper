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
                              "ap_invoice_lines", "ap_expected_costs"]}
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
    cur.execute("""CREATE TABLE risk_events (risk_event_id TEXT PRIMARY KEY, type TEXT,
        rule_id TEXT, severity TEXT, shipment_id TEXT, affected_so_line_ids TEXT,
        affected_value_usd REAL, detected_at TEXT, root_cause TEXT, status TEXT,
        resolved_at TEXT, outcome TEXT, resolution_summary TEXT,
        affected_invoice_line_ids TEXT)""")
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
    con.commit()
    con.close()

    dq["output_rows"] = {"shipments": len(ship_rows), "milestones": len(ms_rows),
                         "so_lines": len(line_rows), "sales_orders": len(so_rows)}
    DQ.write_text(json.dumps(dq, ensure_ascii=False, indent=1))
    print(json.dumps(dq, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
