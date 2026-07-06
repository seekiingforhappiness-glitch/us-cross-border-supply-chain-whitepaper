"""W3 主入口：python3 -m pipeline.build_ontology

从含噪源表（data/raw/）重建对象层 → data/ontology.sqlite + data/dq_report.json。
铁律（AGENTS §5）：本模块禁止读取 data/truth/。状态一律以事件流为准，不信 tms 状态字段。
"""
import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from .er import resolve

RAW = Path("data/raw")
DB = Path("data/ontology.sqlite")
DQ = Path("data/dq_report.json")

PHASE = {"delivered": "delivered", "customs_released": "customs", "customs_hold": "customs",
         "customs_filed": "customs", "arrived": "arrived", "transshipment": "in_transit",
         "eta_change": None, "departed": "in_transit", "booking_confirmed": None}
PHASE_RANK = {"planned": 0, "in_transit": 1, "arrived": 2, "customs": 3, "delivered": 4}


def load(name):
    with open(RAW / f"{name}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def main():
    t = {n: load(n) for n in ["srm_suppliers", "catalog_skus", "oms_customers", "oms_sales_orders",
                              "oms_so_lines", "srm_purchase_orders", "tms_shipments",
                              "tms_milestones", "tms_allocations"]}
    dq = {"input_rows": {k: len(v) for k, v in t.items()}}

    # 1) milestone 判重（四元组保首条，重复标记保留——事件不可变，只标不删）
    seen, dup_count = {}, 0
    ms_rows = []
    for r in sorted(t["tms_milestones"], key=lambda x: (x["ingested_at"], x["milestone_id"])):
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

    table("suppliers", t["srm_suppliers"],
          ["supplier_id TEXT", "supplier_name TEXT", "city TEXT", "lead_time_days INTEGER"],
          "supplier_id")
    table("skus", t["catalog_skus"],
          ["sku_id TEXT", "sku_name TEXT", "category TEXT", "unit_price_usd REAL", "supplier_id TEXT"],
          "sku_id")
    table("customers", t["oms_customers"],
          ["customer_id TEXT", "customer_name TEXT", "tier TEXT", "us_state TEXT"], "customer_id")
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
    table("shipment_allocations", sorted(t["tms_allocations"], key=lambda x: x["allocation_id"]),
          ["allocation_id TEXT", "shipment_id TEXT", "so_line_id TEXT", "allocated_qty INTEGER"],
          "allocation_id")
    cur.execute("CREATE TABLE supplier_name_map (raw_name TEXT PRIMARY KEY, supplier_id TEXT)")
    cur.executemany("INSERT INTO supplier_name_map VALUES (?,?)",
                    [(k, v or "") for k, v in sorted(mapping.items())])
    # W4/W5 空表（schema 与 ontology JSON 一致）
    cur.execute("""CREATE TABLE risk_events (risk_event_id TEXT PRIMARY KEY, type TEXT,
        rule_id TEXT, severity TEXT, shipment_id TEXT, affected_so_line_ids TEXT,
        affected_value_usd REAL, detected_at TEXT, root_cause TEXT, status TEXT,
        resolved_at TEXT, outcome TEXT, resolution_summary TEXT)""")
    cur.execute("""CREATE TABLE tasks (task_id TEXT PRIMARY KEY, risk_event_id TEXT, title TEXT,
        assignee_role TEXT, priority TEXT, due_at TEXT, proposed_action TEXT,
        proposal_params TEXT, approval_status TEXT, approved_by_role TEXT,
        action_taken TEXT, status TEXT)""")
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
