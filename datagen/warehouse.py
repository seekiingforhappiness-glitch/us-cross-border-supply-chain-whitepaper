"""W1 仓储库存准确主线数据生成（Build 1/3：仅模型 + 数据 + ground truth，不做检测/动作/UI）。

独立随机流 random.Random(seed + warehouse.seed_offset=5000)，在 procurement(seed+4000) 之后、
不消耗既有随机流——控制塔/准入/费用/采购数据逐字节零扰动（§7 / X2 / V2 / P1 先例）。

建模选择（每条 ≤5 行"为什么这样建"）：
- 库存粒度 SKU×仓库（决策 W1）：InventoryPosition 主键 = 序号，一 (sku,warehouse) 对至多一条，不到
  lot（R21 批次过期因此推迟）；桶字段 available/reserved/in_transit/quarantine + safety_stock。
- ATP 由 position 桶字段直算（available+in_transit−reserved），**不从 reservation 反推**——避免检测
  循环依赖；reservation 仅承载"待履约需求"（status=open）供 R17 与 ATP 对比。
- 每个 position 只属一个注入桶（clean/r16/r16_gray/r17/r17_gray/r18/r18_gray）→ 三规则正交、真值 1:1。
- R17 host position 的 sku = 所选 open SOL 的 sku（reservation 把该 SOL 绑到此 position）：available>safety
  规避 R16、reserved 抬高使 ATP<需求触发 R17（与 R16 正交）；连接点为"延误查现货"（业务落点，Build 2/3）。
- 灰区 = 恰在阈值内（available=safety+δ / ATP≥需求 / |var|/system≤tol）→ 不进真值，测 Build 2 误报。

产出（写入 world["warehouse"]）：
  warehouses / positions / reservations / cycle_counts
  anomalies  ground truth（R16 锚 inventory_position_id / R17 锚 so_line_id / R18 锚 cycle_count_id）
"""

# 5 仓（覆盖 type 枚举 domestic/3PL/bonded/FBA/overseas）；前 4 为 fulfillment 仓，承载 position/预留/盘点。
# warehouse_id 复用既有 shipment.destination_warehouse 三个 US DC（LAX/ONT/RIV）→ 连接点"到货落仓"为真
# （Shipment.destination_warehouse → Warehouse）；FBA/overseas 补齐类型枚举。
WAREHOUSES = [
    ("LAX-DC1", "domestic", "self", "west", 500000),
    ("ONT-DC2", "3PL", "flexe", "west", 300000),
    ("RIV-DC3", "bonded", "self", "west", 200000),
    ("FBA-LAX9", "FBA", "amazon", "west", 400000),
    ("SZX-OVS1", "overseas", "self", "south_china", 350000),
]
FULFIL_WHS = ["LAX-DC1", "ONT-DC2", "RIV-DC3", "FBA-LAX9"]


def build_warehouse_world(world, cfg, rng):
    """生成仓储库存全套数据。rng 必须是 random.Random(seed + warehouse.seed_offset)。"""
    wc = cfg["warehouse"]
    as_of = world["as_of"]
    inj = wc["inject"]
    tol = wc["cycle_count_tol"]
    ira_target = wc["ira_target"]
    ss_lo, ss_hi = wc["safety_stock_range"]
    stockout_high_frac = wc["stockout_high_frac"]
    shrink_high_ratio = wc["shrink_high_ratio"]

    skus = world["skus"]
    lines = world["lines"]
    sku_ids = sorted(skus)

    def unit_price(sku_id):
        return round(skus[sku_id]["unit_price_usd"], 2)

    warehouses = [{"warehouse_id": wid, "type": wtype, "operator": op, "region": region,
                   "capacity_units": cap, "as_of_date": as_of.isoformat()}
                  for wid, wtype, op, region, cap in WAREHOUSES]

    positions = []
    reservations = []
    cycle_counts = []
    anomalies = []
    seqs = {"pos": 0, "rsv": 0, "cc": 0, "ewr": 0}

    def nid(kind, fmt):
        seqs[kind] += 1
        return fmt.format(seqs[kind])

    # (sku,warehouse) 唯一分配：一对至多一条 position（DQ 约束）。仓库大序（warehouse-major）消费——
    # 非 R17 桶优先铺满 LAX/ONT，给 R17（sku 由 open SOL 定，需在任意仓找空位）留 RIV/FBA 头寸，避免早
    # sku 被 4 仓占满导致 take_pair_for_sku 失败。used 去重。
    all_pairs = [(s, wh) for wh in FULFIL_WHS for s in sku_ids]
    used = set()
    ptr = [0]

    def take_pair():
        while all_pairs[ptr[0]] in used:
            ptr[0] += 1
        p = all_pairs[ptr[0]]
        used.add(p)
        ptr[0] += 1
        return p

    def take_pair_for_sku(sku):
        for wh in FULFIL_WHS:
            if (sku, wh) not in used:
                used.add((sku, wh))
                return sku, wh
        raise RuntimeError(f"no free warehouse for {sku}")

    clean_positions = []

    def add_position(sku_id, wh, available, reserved, in_transit, quarantine, safety, clean=False):
        pid = nid("pos", "INVP-{:05d}")
        p = {"inventory_position_id": pid, "sku_id": sku_id, "warehouse_id": wh,
             "available_qty": available, "reserved_qty": reserved,
             "in_transit_qty": in_transit, "quarantine_qty": quarantine,
             "safety_stock": safety, "as_of_date": as_of.isoformat()}
        positions.append(p)
        if clean:
            clean_positions.append(p)
        return pid

    def add_anomaly(rule_id, atype, warehouse_id, sku_id, position_id, so_line_id,
                    cycle_count_id, severity, value_usd, note):
        anomalies.append({
            "expected_warehouse_risk_id": nid("ewr", "EWR-{:05d}"),
            "rule_id": rule_id, "type": atype, "warehouse_id": warehouse_id,
            "sku_id": sku_id, "inventory_position_id": position_id,
            "so_line_id": so_line_id, "cycle_count_id": cycle_count_id,
            "severity": severity, "anomaly_value_usd": round(value_usd, 2), "note": note})

    # 目的仓轮转（anomaly host 用）
    wh_cycle = [0]

    def next_wh():
        wh = FULFIL_WHS[wh_cycle[0] % len(FULFIL_WHS)]
        wh_cycle[0] += 1
        return wh

    # === clean positions（健康：available≫safety、ATP≫需求、无 open 预留）===
    for _ in range(inj["clean"]):
        sku_id, wh = take_pair()
        safety = rng.randint(ss_lo, ss_hi)
        available = safety * rng.randint(3, 8)
        reserved = rng.randint(0, safety)
        in_transit = rng.randint(0, safety * 2)
        add_position(sku_id, wh, available, reserved, in_transit, 0, safety, clean=True)

    # === R16 断货：available ≤ safety_stock（high: available ≤ safety×frac）===
    for k in range(inj["r16"]):
        sku_id, wh = take_pair()
        safety = rng.randint(ss_lo, ss_hi)
        if k < inj["r16"] * stockout_high_frac:              # high：见底/接近清空
            available = rng.randint(0, int(safety * stockout_high_frac))
        else:                                                # medium：低于安全库存但未见底
            available = rng.randint(int(safety * stockout_high_frac) + 1, safety)
        pid = add_position(sku_id, wh, available, 0, 0, 0, safety)
        sev = "high" if available <= safety * stockout_high_frac else "medium"
        add_anomaly("R16", "stockout", wh, sku_id, pid, "", "", sev,
                    (safety - available) * unit_price(sku_id),
                    f"断货：可用 {available} ≤ 安全库存 {safety}（缺口 {safety - available} 件）")

    # === R16 灰区：available = safety + δ（略高于安全库存）→ 不触发 ===
    for _ in range(inj["r16_gray"]):
        sku_id, wh = take_pair()
        safety = rng.randint(ss_lo, ss_hi)
        available = safety + rng.randint(1, max(1, safety // 20))
        add_position(sku_id, wh, available, 0, 0, 0, safety)

    # === R17 不可履约：open SOL 的 sku 在目的仓 ATP < 需求量（ATP=available+in_transit−reserved）===
    # 选 open SOL（line_status='open'——ontology 亦为 open，无分配；与 build_ontology 派生一致）。
    open_sols = [l for l in sorted(lines.values(), key=lambda x: x["so_line_id"])
                 if l["line_status"] == "open"]
    n_r17 = inj["r17"] + inj["r17_gray"]
    r17_sols = open_sols[:n_r17]
    if len(r17_sols) < n_r17:
        raise RuntimeError(f"open SOL 不足：需 {n_r17} 得 {len(r17_sols)}")
    for k in range(inj["r17"]):
        sol = r17_sols[k]
        sku_id = sol["sku_id"]
        demand = sol["qty"]
        _, wh = take_pair_for_sku(sku_id)
        safety = rng.randint(ss_lo, ss_hi)
        available = safety + rng.randint(20, 80)             # >safety → 不触 R16
        if k < inj["r17"] * 0.5:                             # high：ATP ≤ 0
            reserved = available + rng.randint(10, 100)
            in_transit = 0
        else:                                                # medium：0 < ATP < demand
            atp = rng.randint(5, min(max(demand - 1, 6), 40))
            reserved = available - atp
            in_transit = 0
        pid = add_position(sku_id, wh, available, reserved, in_transit, 0, safety)
        atp = available + in_transit - reserved
        rsv_id = nid("rsv", "RSV-{:06d}")
        reservations.append({
            "reservation_id": rsv_id, "so_line_id": sol["so_line_id"],
            "inventory_position_id": pid, "qty": demand, "status": "open",
            "as_of_date": as_of.isoformat()})
        sev = "high" if atp <= 0 else "medium"
        add_anomaly("R17", "unfulfillable", wh, sku_id, pid, sol["so_line_id"], "", sev,
                    (demand - max(atp, 0)) * unit_price(sku_id),
                    f"不可履约：{sol['so_line_id']} 需 {demand} 件，目的仓 {wh} ATP={atp}"
                    f"（可用 {available}+在途 {in_transit}−预留 {reserved}）< 需求")

    # === R17 灰区：ATP ≥ 需求（恰好够）→ 不触发 ===
    for k in range(inj["r17_gray"]):
        sol = r17_sols[inj["r17"] + k]
        sku_id = sol["sku_id"]
        demand = sol["qty"]
        _, wh = take_pair_for_sku(sku_id)
        safety = rng.randint(ss_lo, ss_hi)
        available = demand + safety + rng.randint(50, 150)
        reserved = available - (demand + rng.randint(5, 20))  # ATP = demand + (5..20) ≥ demand
        pid = add_position(sku_id, wh, available, reserved, 0, 0, safety)
        rsv_id = nid("rsv", "RSV-{:06d}")
        reservations.append({
            "reservation_id": rsv_id, "so_line_id": sol["so_line_id"],
            "inventory_position_id": pid, "qty": demand, "status": "open",
            "as_of_date": as_of.isoformat()})

    # === R18 盘点差异：|counted−system|/system > tol（system=账面 available）===
    for k in range(inj["r18"]):
        sku_id, wh = take_pair()
        safety = rng.randint(ss_lo, ss_hi)
        system_qty = safety * rng.randint(3, 8)              # 健康账面（不触 R16）
        add_position(sku_id, wh, system_qty, rng.randint(0, safety), 0, 0, safety)
        if k < inj["r18"] * 0.5:                             # high：差异 > shrink_high_ratio
            ratio = rng.uniform(shrink_high_ratio + 0.005, shrink_high_ratio + 0.05)
        else:                                                # medium：tol < 差异 ≤ high 门槛
            ratio = rng.uniform(tol + 0.002, shrink_high_ratio)
        counted = int(round(system_qty * (1 - ratio)))       # 盘亏（counted < system）
        # 真差异比按整数重算，保证 gen/detect severity 与 truth 一致
        actual_ratio = abs(counted - system_qty) / system_qty
        pos_id = positions[-1]["inventory_position_id"]
        cc_id = nid("cc", "CCNT-{:05d}")
        cycle_counts.append({
            "cycle_count_id": cc_id, "inventory_position_id": pos_id, "warehouse_id": wh,
            "system_qty": system_qty, "counted_qty": counted,
            "variance": counted - system_qty, "status": "variance",
            "as_of_date": as_of.isoformat()})
        sev = "high" if actual_ratio > shrink_high_ratio else "medium"
        ira = round(1 - actual_ratio, 4)
        add_anomaly("R18", "shrinkage", wh, sku_id, pos_id, "", cc_id, sev,
                    abs(counted - system_qty) * unit_price(sku_id),
                    f"盘点差异：账面 {system_qty} 实盘 {counted}（差 {counted - system_qty}，"
                    f"|差异|/账面={round(actual_ratio * 100, 1)}% > 容差 {round(tol * 100, 1)}%，IRA={ira}<{ira_target}）")

    # === R18 灰区：|counted−system|/system ≤ tol → 不触发 ===
    for _ in range(inj["r18_gray"]):
        sku_id, wh = take_pair()
        safety = rng.randint(ss_lo, ss_hi)
        system_qty = safety * rng.randint(3, 8)
        pid = add_position(sku_id, wh, system_qty, rng.randint(0, safety), 0, 0, safety)
        ratio = tol * 0.5
        counted = int(round(system_qty * (1 - ratio)))
        cc_id = nid("cc", "CCNT-{:05d}")
        cycle_counts.append({
            "cycle_count_id": cc_id, "inventory_position_id": pid, "warehouse_id": wh,
            "system_qty": system_qty, "counted_qty": counted,
            "variance": counted - system_qty, "status": "counted",
            "as_of_date": as_of.isoformat()})

    # === 干净预留（status=allocated/fulfilled，非 open → 不进 R17 域）+ 干净盘点（counted=system）===
    clean_pos = clean_positions                              # 健康 position 承载干净预留/盘点
    extra_sols = open_sols[n_r17:n_r17 + wc["clean_reservations"]]
    for k, sol in enumerate(extra_sols):
        p = clean_pos[k % len(clean_pos)]
        rsv_id = nid("rsv", "RSV-{:06d}")
        reservations.append({
            "reservation_id": rsv_id, "so_line_id": sol["so_line_id"],
            "inventory_position_id": p["inventory_position_id"],
            "qty": min(sol["qty"], p["available_qty"]),
            "status": "allocated" if k % 2 == 0 else "fulfilled",
            "as_of_date": as_of.isoformat()})
    for k in range(wc["clean_cycle_counts"]):
        p = clean_pos[k % len(clean_pos)]
        cc_id = nid("cc", "CCNT-{:05d}")
        cycle_counts.append({
            "cycle_count_id": cc_id, "inventory_position_id": p["inventory_position_id"],
            "warehouse_id": p["warehouse_id"], "system_qty": p["available_qty"],
            "counted_qty": p["available_qty"], "variance": 0, "status": "reconciled",
            "as_of_date": as_of.isoformat()})

    # 真值输出（§5 铁律）：按 (rule_id, 锚点) 稳定排序
    anomalies.sort(key=lambda a: (a["rule_id"], a["inventory_position_id"],
                                  a["so_line_id"], a["cycle_count_id"]))

    world["warehouse"] = {
        "warehouses": warehouses,
        "positions": positions,
        "reservations": reservations,
        "cycle_counts": cycle_counts,
        "anomalies": anomalies,
        "r17_sols": [s["so_line_id"] for s in r17_sols[:inj["r17"]]],
    }
    return world["warehouse"]
