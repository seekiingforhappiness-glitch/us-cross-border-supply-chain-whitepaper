"""噪声注入：只污染 emit 层源表，不改干净世界。每次注入写 injected_noise_log（D6）。"""
from datetime import datetime, timedelta


def _iso_add_hours(iso, hours):
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(hours=hours)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_variant(name):
    v = (name.replace("Shenzhen", "SZ").replace("Ningbo", "NB").replace("Dongguan", "DG")
             .replace("Electronics Co., Ltd.", "Elec. Co").replace("Co., Ltd.", "Co"))
    return v if v != name else name + " (HK)"


def apply_noise(world, design_noise, cfg, rng):
    nr = cfg["noise_rates"]
    design_ships = set(world.get("design_ship_case", {}))
    log = []
    nid = 0

    def add_log(ntype, table, target, desc, case=""):
        nonlocal nid
        nid += 1
        log.append({"noise_id": f"NOI-{nid:05d}", "noise_type": ntype, "target_table": table,
                    "target_id": target, "description": desc, "case_id": case})

    # 1) 供应商名称变体（仅出现在 TMS 侧，SRM 保持规范名 → W3 entity resolution 练习）
    variant_map = {}
    forced = {d["supplier_id"] for d in design_noise if d["type"] == "supplier_name_variant"}
    pool = sorted(s for s in world["suppliers"] if s not in forced)
    n_var = max(0, round(nr["supplier_name_variant"] * len(world["suppliers"])) - len(forced))
    chosen = sorted(set(rng.sample(pool, min(n_var, len(pool)))) | forced)
    for sid in chosen:
        variant_map[sid] = make_variant(world["suppliers"][sid]["supplier_name"])
        case = next((d["case_id"] for d in design_noise
                     if d["type"] == "supplier_name_variant" and d["supplier_id"] == sid), "")
        add_log("supplier_name_variant", "tms_shipments", sid,
                f"'{world['suppliers'][sid]['supplier_name']}' appears as '{variant_map[sid]}'", case)

    # 2) milestone 行：编号 + ingested_at（设计案例可显式指定 ingested_at）
    ms_rows = []
    seq = 0
    for m in sorted(world["milestones"],
                    key=lambda x: (x["shipment_id"], x["event_time"], x["event_type"])):
        seq += 1
        row = dict(m)
        row["milestone_id"] = f"MS-{seq:06d}"
        if "ingested_at" not in row:
            row["ingested_at"] = _iso_add_hours(row["event_time"], rng.randint(2, 48))
        ms_rows.append(row)

    # 设计噪声：重复上报 / 乱序记账
    for d in design_noise:
        if d["type"] == "milestone_duplicate":
            src = next(r for r in ms_rows if r["shipment_id"] == d["match"]["shipment_id"]
                       and r["event_type"] == d["match"]["event_type"])
            seq += 1
            dup = dict(src)
            dup["milestone_id"] = f"MS-{seq:06d}"
            dup["ingested_at"] = _iso_add_hours(src["ingested_at"], 3)
            ms_rows.append(dup)
            add_log("milestone_duplicate", "tms_milestones", src["milestone_id"],
                    "exact duplicate report", d["case_id"])
        elif d["type"] == "milestone_out_of_order":
            src = next(r for r in ms_rows if r["shipment_id"] == d["shipment_id"]
                       and r["event_time"] == d["match"]["event_time"])
            add_log("milestone_out_of_order", "tms_milestones", src["milestone_id"],
                    "stale eta_change ingested after newer event", d["case_id"])

    # 随机重复
    cand = [r for r in ms_rows if r["shipment_id"] not in design_ships]
    for r in rng.sample(cand, round(nr["milestone_duplicate"] * len(cand))):
        seq += 1
        dup = dict(r)
        dup["milestone_id"] = f"MS-{seq:06d}"
        dup["ingested_at"] = _iso_add_hours(r["ingested_at"], rng.randint(1, 6))
        ms_rows.append(dup)
        add_log("milestone_duplicate", "tms_milestones", r["milestone_id"], "duplicate report")
    # 随机乱序
    cand = [r for r in ms_rows if r["shipment_id"] not in design_ships]
    for r in rng.sample(cand, round(nr["milestone_out_of_order"] * len(cand))):
        r["ingested_at"] = _iso_add_hours(r["ingested_at"], rng.randint(24, 72))
        add_log("milestone_out_of_order", "tms_milestones", r["milestone_id"], "late ingestion")

    # 3) shipment 层：状态冲突 / 空值
    emit_status, null_ships = {}, set()
    all_status = ["planned", "in_transit", "arrived", "customs", "delivered"]
    pool = [s for s in sorted(world["shipments"]) if s not in design_ships]
    for sid in rng.sample(pool, round(nr["status_conflict"] * len(pool))):
        truth = world["shipments"][sid]["status"]
        emit_status[sid] = rng.choice([s for s in all_status if s != truth])
        add_log("status_conflict", "tms_shipments", sid, f"tms says {emit_status[sid]}, truth {truth}")
    for sid in rng.sample(pool, round(nr["null_vessel_carrier"] * len(pool))):
        null_ships.add(sid)
        add_log("null_vessel_carrier", "tms_shipments", sid, "vessel_voyage/carrier_name empty")
    for d in design_noise:
        if d["type"] == "status_conflict":
            truth = world["shipments"][d["shipment_id"]]["status"]
            emit_status[d["shipment_id"]] = d["emit_status"]
            add_log("status_conflict", "tms_shipments", d["shipment_id"],
                    f"tms says {d['emit_status']}, truth {truth}", d["case_id"])
        elif d["type"] == "null_vessel_carrier":
            null_ships.add(d["shipment_id"])
            add_log("null_vessel_carrier", "tms_shipments", d["shipment_id"],
                    "vessel_voyage/carrier_name empty", d["case_id"])

    return {"variant_map": variant_map, "ms_rows": ms_rows,
            "emit_status": emit_status, "null_ships": null_ships, "noise_log": log}
