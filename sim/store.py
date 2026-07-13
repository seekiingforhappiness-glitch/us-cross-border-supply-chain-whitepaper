"""落库 data/simworld.sqlite——对象层严格对齐本体 schema + sim 专属表。

为什么这样建（≤5 行）：① 对象层表（shipments/skus/...）列集与 pipeline/build_ontology 的本体 DDL
逐列对齐（等价 DDL，不 import 以免拉入 engine/agent 依赖、保物理隔离）——驾驶舱/透视镜读 simworld.sqlite
即等同读 ontology.sqlite。② 与 data/ontology.sqlite 物理隔离（独立文件）。③ 一切 sim 专属知识（货代/供应商/
SKU/客户性格参数、货代-船绑定、公司画像、船期、sim_event_log 自审计）落 sim_ 前缀表，不污染对象层。
④ 写入顺序确定 → 内容可复现（verify 已核验逐字节一致）。
"""
import sqlite3
from datetime import date
from pathlib import Path


# 对象层 DDL：列集严格对齐 pipeline/build_ontology 的本体表（便于驾驶舱直接读）
OBJECT_DDL = {
    "suppliers": """(supplier_id TEXT PRIMARY KEY, supplier_name TEXT, city TEXT,
        lead_time_days INTEGER, factory_audit_status TEXT, compliance_docs_status TEXT,
        uflpa_risk_flag TEXT, origin_evidence_status TEXT)""",
    "skus": """(sku_id TEXT PRIMARY KEY, sku_name TEXT, category TEXT, unit_price_usd REAL,
        supplier_id TEXT, sku_status TEXT, declared_value_usd TEXT, package_weight_kg TEXT,
        package_l_cm TEXT, package_w_cm TEXT, package_h_cm TEXT, battery_flag TEXT,
        food_contact_flag TEXT, children_product_flag TEXT, material TEXT, use_case TEXT,
        origin_country TEXT, platform TEXT)""",
    "customers": """(customer_id TEXT PRIMARY KEY, customer_name TEXT, tier TEXT, us_state TEXT,
        business_model TEXT, sales_channel TEXT, ior_capability TEXT, broker_status TEXT,
        credit_terms TEXT, risk_tier TEXT)""",
    "sales_orders": """(so_id TEXT PRIMARY KEY, customer_id TEXT, order_date TEXT, status TEXT)""",
    "sales_order_lines": """(so_line_id TEXT PRIMARY KEY, so_id TEXT, sku_id TEXT, qty INTEGER,
        unit_price_usd REAL, promised_delivery_date TEXT, original_promised_date TEXT,
        reschedule_count INTEGER, line_status TEXT)""",
    "purchase_orders": """(po_id TEXT PRIMARY KEY, supplier_id TEXT, sku_id TEXT, qty INTEGER,
        po_date TEXT, expected_ready_date TEXT, status TEXT)""",
    "shipments": """(shipment_id TEXT PRIMARY KEY, booking_no TEXT, mbl_no TEXT, mode TEXT,
        container_no TEXT, container_type TEXT, vessel_voyage TEXT, carrier_name TEXT,
        carrier_scac TEXT, incoterm TEXT, gross_weight_kg REAL, volume_cbm REAL, origin_port TEXT,
        origin_port_locode TEXT, destination_port TEXT, destination_port_locode TEXT,
        destination_warehouse TEXT, etd TEXT, eta_initial TEXT, eta_current TEXT, ata TEXT,
        customs_status TEXT, missing_docs TEXT, delay_days INTEGER, status TEXT,
        status_source TEXT, last_event_time TEXT, expedite_flag INTEGER, po_ids TEXT)""",
    "shipment_milestones": """(milestone_id TEXT PRIMARY KEY, shipment_id TEXT, event_type TEXT,
        event_classifier TEXT, event_time TEXT, event_locode TEXT, new_eta TEXT,
        source_system TEXT, ingested_at TEXT, is_duplicate INTEGER)""",
    "shipment_allocations": """(allocation_id TEXT PRIMARY KEY, shipment_id TEXT,
        so_line_id TEXT, allocated_qty INTEGER)""",
    "containers": """(container_no TEXT PRIMARY KEY, shipment_id TEXT, container_type TEXT,
        is_primary TEXT, free_days INTEGER, gross_weight_kg REAL, volume_cbm REAL)""",
    "invoices": """(invoice_id TEXT PRIMARY KEY, vendor_type TEXT, vendor_name TEXT,
        vendor_invoice_no TEXT, shipment_id TEXT, issue_date TEXT, currency TEXT,
        total_usd REAL, status TEXT)""",
    "invoice_lines": """(invoice_line_id TEXT PRIMARY KEY, invoice_id TEXT, charge_code TEXT,
        container_no TEXT, qty INTEGER, unit_price_usd REAL, amount_usd REAL)""",
    "warehouses": """(warehouse_id TEXT PRIMARY KEY, type TEXT, operator TEXT, region TEXT,
        capacity_units INTEGER, as_of_date TEXT)""",
    "inventory_positions": """(inventory_position_id TEXT PRIMARY KEY, sku_id TEXT,
        warehouse_id TEXT, available_qty INTEGER, reserved_qty INTEGER, in_transit_qty INTEGER,
        quarantine_qty INTEGER, safety_stock INTEGER, as_of_date TEXT)""",
}

# sim 专属表：世界谱系参数 / 性格模型 / 货代-船绑定 / 自审计——不污染对象层
SIM_DDL = {
    "sim_company_profile": """(name TEXT, annual_container_target INTEGER, team_size INTEGER,
        origin_hubs TEXT, eu_share REAL, window_start TEXT, window_end TEXT, as_of TEXT, seed INTEGER)""",
    "sim_forwarders": """(forwarder_id TEXT PRIMARY KEY, name TEXT, quote_level REAL,
        volumetric_tendency REAL, notify_delay_min INTEGER, notify_delay_max INTEGER,
        billing_error_rate REAL, credibility REAL)""",
    "sim_supplier_traits": """(supplier_id TEXT PRIMARY KEY, reliability REAL, quality REAL,
        price_increase_tendency REAL, chronic_delay INTEGER, qual_expiring INTEGER)""",
    "sim_sku_traits": """(sku_id TEXT PRIMARY KEY, category TEXT, seasonal INTEGER,
        daily_velocity INTEGER)""",
    "sim_customer_traits": """(customer_id TEXT PRIMARY KEY, tier TEXT, region TEXT,
        order_interval_days INTEGER, tolerance_days INTEGER)""",
    "sim_routes": """(route_id TEXT PRIMARY KEY, origin_ports TEXT, dest_ports TEXT,
        transit_min INTEGER, transit_max INTEGER, weight REAL)""",
    "sim_schedule": """(sailing_id TEXT PRIMARY KEY, route_id TEXT, carrier TEXT, scac TEXT,
        vessel TEXT, voyage TEXT, origin_port TEXT, dest_port TEXT, transit_days INTEGER,
        etd TEXT, eta TEXT, booked INTEGER)""",
    "sim_shipment_meta": """(shipment_id TEXT PRIMARY KEY, forwarder_id TEXT, route_id TEXT,
        sailing_id TEXT)""",
    "sim_event_log": """(sim_event_id TEXT PRIMARY KEY, sim_date TEXT, event_kind TEXT,
        object_type TEXT, object_id TEXT, params_json TEXT, rng_stream TEXT)""",
}


def _last_event_times(world):
    out = {}
    for m in world["milestones"]:
        sid = m["shipment_id"]
        if sid not in out or m["event_time"] > out[sid]:
            out[sid] = m["event_time"]
    return out


def _inventory_snapshot(world):
    """快照时补齐 in_transit / reserved（派生，恒 ≥ 0，reserved ≤ available）。"""
    cfg = world["_cfg"]
    region_share = cfg["inventory"]["region_share"]
    in_transit = {}
    active = ("planned", "in_transit", "arrived", "customs")
    for sid in sorted(world["shipments"]):
        ship = world["shipments"][sid]
        wh = ship["destination_warehouse"]
        if ship["status"] not in active or wh not in world["warehouses"]:
            continue
        for lid in ship["line_ids"]:
            kid = world["lines"][lid]["sku_id"]
            in_transit[(kid, wh)] = in_transit.get((kid, wh), 0) + world["lines"][lid]["qty"]
    rows = []
    as_of = world["_as_of"].isoformat()
    for key in sorted(world["inventory"]):
        kid, wid = key
        pos = world["inventory"][key]
        region = world["warehouses"][wid]["region"]
        vel = world["skus"][kid]["daily_velocity"]
        reserved = min(pos["available_qty"],
                       int(round(vel * region_share.get(region, 0.2) * 3)))  # 约 3 天需求预留
        rows.append({
            "inventory_position_id": pos["inventory_position_id"], "sku_id": kid,
            "warehouse_id": wid, "available_qty": pos["available_qty"],
            "reserved_qty": reserved, "in_transit_qty": in_transit.get(key, 0),
            "quarantine_qty": 0, "safety_stock": pos["safety_stock"], "as_of_date": as_of,
        })
    return rows


def _rows(world):
    """世界状态 → 对象层各表行（确定性排序；列集对齐本体，sim 专属字段不入此层）。"""
    as_of = world["_as_of"].isoformat()
    let = _last_event_times(world)
    t = {}
    t["suppliers"] = [{k: world["suppliers"][sid][k] for k in
                       ("supplier_id", "supplier_name", "city", "lead_time_days",
                        "factory_audit_status", "compliance_docs_status", "uflpa_risk_flag",
                        "origin_evidence_status")} for sid in sorted(world["suppliers"])]
    t["skus"] = [{"sku_id": s["sku_id"], "sku_name": s["sku_name"], "category": s["category"],
                  "unit_price_usd": s["unit_price_usd"], "supplier_id": s["supplier_id"],
                  "sku_status": s["sku_status"]}
                 for s in (world["skus"][k] for k in sorted(world["skus"]))]
    t["customers"] = [{k: world["customers"][cid][k] for k in
                       ("customer_id", "customer_name", "tier", "us_state", "business_model",
                        "sales_channel", "ior_capability", "broker_status", "credit_terms",
                        "risk_tier")} for cid in sorted(world["customers"])]
    t["sales_orders"] = [{"so_id": s["so_id"], "customer_id": s["customer_id"],
                          "order_date": s["order_date"].isoformat(), "status": s["status"]}
                         for s in (world["sos"][k] for k in sorted(world["sos"]))]
    t["sales_order_lines"] = [{
        "so_line_id": l["so_line_id"], "so_id": l["so_id"], "sku_id": l["sku_id"],
        "qty": l["qty"], "unit_price_usd": l["unit_price_usd"],
        "promised_delivery_date": l["promised_delivery_date"].isoformat(),
        "original_promised_date": l["promised_delivery_date"].isoformat(),
        "reschedule_count": 0, "line_status": l["line_status"],
    } for l in (world["lines"][k] for k in sorted(world["lines"]))]
    t["purchase_orders"] = [{
        "po_id": p["po_id"], "supplier_id": p["supplier_id"],
        "sku_id": world["lines"][p["line_ids"][0]]["sku_id"], "qty": p["qty"],
        "po_date": p["po_date"].isoformat(),
        "expected_ready_date": p["expected_ready_date"].isoformat(), "status": p["status"],
    } for p in (world["pos"][k] for k in sorted(world["pos"]))]
    t["shipments"] = [{
        "shipment_id": s["shipment_id"], "booking_no": s["booking_no"], "mbl_no": s["mbl_no"],
        "mode": s["mode"], "container_no": s["container_no"], "container_type": s["container_type"],
        "vessel_voyage": s["vessel_voyage"], "carrier_name": s["carrier_name"],
        "carrier_scac": s["carrier_scac"], "incoterm": s["incoterm"],
        "gross_weight_kg": s["gross_weight_kg"], "volume_cbm": s["volume_cbm"],
        "origin_port": s["origin_port"], "origin_port_locode": s["origin_port_locode"],
        "destination_port": s["destination_port"],
        "destination_port_locode": s["destination_port_locode"],
        "destination_warehouse": s["destination_warehouse"], "etd": s["etd"].isoformat(),
        "eta_initial": s["eta_initial"].isoformat(), "eta_current": s["eta_current"].isoformat(),
        "ata": s["ata"].isoformat() if s["ata"] else "", "customs_status": s["customs_status"],
        "missing_docs": "|".join(s["missing_docs"]),
        "delay_days": (s["eta_current"] - s["eta_initial"]).days, "status": s["status"],
        "status_source": s["status"], "last_event_time": let.get(s["shipment_id"], ""),
        "expedite_flag": 1 if s["expedite_flag"] else 0, "po_ids": "|".join(s["po_ids"]),
    } for s in (world["shipments"][k] for k in sorted(world["shipments"]))]
    t["shipment_milestones"] = sorted(world["milestones"], key=lambda m: m["milestone_id"])
    t["shipment_allocations"] = sorted(world["allocations"], key=lambda a: a["allocation_id"])
    t["containers"] = [{**c, "is_primary": str(bool(c["is_primary"]))}
                       for c in sorted(world["containers"], key=lambda c: c["container_no"])]
    t["invoices"] = sorted(world["invoices"], key=lambda i: i["invoice_id"])
    t["invoice_lines"] = sorted(world["invoice_lines"], key=lambda l: l["invoice_line_id"])
    t["warehouses"] = [{"warehouse_id": world["warehouses"][k]["id"],
                        "type": world["warehouses"][k]["type"],
                        "operator": world["warehouses"][k]["operator"],
                        "region": world["warehouses"][k]["region"],
                        "capacity_units": world["warehouses"][k]["capacity_units"],
                        "as_of_date": as_of} for k in sorted(world["warehouses"])]
    t["inventory_positions"] = _inventory_snapshot(world)
    return t


def _sim_rows(world, cfg):
    prof = world["profile"]
    win = cfg["window"]
    return {
        "sim_company_profile": [{
            "name": prof["name"], "annual_container_target": prof["annual_container_target"],
            "team_size": prof["team_size"], "origin_hubs": "|".join(prof["origin_hubs"]),
            "eu_share": prof["eu_share"], "window_start": win["start"], "window_end": win["end"],
            "as_of": win["as_of"], "seed": cfg["seed"],
        }],
        "sim_forwarders": [{
            "forwarder_id": f["id"], "name": f["name"], "quote_level": f["quote_level"],
            "volumetric_tendency": f["volumetric_tendency"],
            "notify_delay_min": f["notify_delay_days"][0], "notify_delay_max": f["notify_delay_days"][1],
            "billing_error_rate": f["billing_error_rate"], "credibility": f["credibility"],
        } for f in (world["forwarders"][k] for k in sorted(world["forwarders"]))],
        "sim_supplier_traits": [{
            "supplier_id": s["supplier_id"], "reliability": s["reliability"], "quality": s["quality"],
            "price_increase_tendency": s["price_increase_tendency"],
            "chronic_delay": 1 if s["chronic_delay"] else 0,
            "qual_expiring": 1 if s["qual_expiring"] else 0,
        } for s in (world["suppliers"][k] for k in sorted(world["suppliers"]))],
        "sim_sku_traits": [{
            "sku_id": s["sku_id"], "category": s["category"],
            "seasonal": 1 if s["seasonal"] else 0, "daily_velocity": s["daily_velocity"],
        } for s in (world["skus"][k] for k in sorted(world["skus"]))],
        "sim_customer_traits": [{
            "customer_id": c["customer_id"], "tier": c["tier"], "region": c["region"],
            "order_interval_days": c["order_interval_days"], "tolerance_days": c["tolerance_days"],
        } for c in (world["customers"][k] for k in sorted(world["customers"]))],
        "sim_routes": [{
            "route_id": r, "origin_ports": "|".join(world["routes"][r]["origin"]),
            "dest_ports": "|".join(world["routes"][r]["dest"]),
            "transit_min": world["routes"][r]["transit_days"][0],
            "transit_max": world["routes"][r]["transit_days"][1],
            "weight": world["routes"][r]["weight"],
        } for r in sorted(world["routes"])],
        "sim_schedule": [{
            "sailing_id": s["sailing_id"], "route_id": s["route_id"], "carrier": s["carrier"],
            "scac": s["scac"], "vessel": s["vessel"], "voyage": s["voyage"],
            "origin_port": s["origin_port"], "dest_port": s["dest_port"],
            "transit_days": s["transit_days"], "etd": s["etd"].isoformat(),
            "eta": s["eta"].isoformat(), "booked": s["booked"],
        } for s in sorted(world["schedule"], key=lambda s: s["sailing_id"])],
        "sim_shipment_meta": [{
            "shipment_id": s["shipment_id"], "forwarder_id": s["forwarder_id"],
            "route_id": s["route_id"], "sailing_id": s["sailing_id"],
        } for s in (world["shipments"][k] for k in sorted(world["shipments"]))],
        "sim_event_log": sorted(world["event_log"], key=lambda e: e["sim_event_id"]),
    }


def _colnames(ddl):
    inner = ddl.strip()[1:-1]
    cols, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(cur.strip())
            cur = ""
        else:
            cur += ch
    cols.append(cur.strip())
    names = []
    for c in cols:
        c = c.strip()
        if c.upper().startswith("PRIMARY KEY"):
            continue
        names.append(c.split()[0])
    return names


def _cell(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, date):
        return v.isoformat()
    return v


def write_simworld(world, cfg, path=None):
    path = path or cfg["output"]["sqlite_path"]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    obj, sim = _rows(world), _sim_rows(world, cfg)
    counts = {}
    for name, ddl in list(OBJECT_DDL.items()) + list(SIM_DDL.items()):
        con.execute(f"CREATE TABLE {name} {ddl}")
        cols = _colnames(ddl)
        rows = obj.get(name, sim.get(name, []))
        con.executemany(
            f"INSERT INTO {name} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [[_cell(r.get(c, "")) for c in cols] for r in rows])
        counts[name] = len(rows)
    con.commit()
    con.close()
    return counts
