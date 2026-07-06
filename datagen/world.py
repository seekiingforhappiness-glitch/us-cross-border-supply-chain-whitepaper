"""随机世界生成：先生成"干净的真实世界"（clean world），噪声只在 emit 阶段污染源表。

设计依据：plan v0.2 §9、D6/D8。所有随机性来自传入的 rng，禁止隐式系统时间。
"""
from datetime import date, timedelta

PORTS_CN = ["yantian", "shekou", "ningbo"]
PORTS_US = ["los_angeles", "long_beach"]
# Tier 1 真实字段（docs/field-gap-analysis.md，D11）
SCAC = {"COSCO": "COSU", "OOCL": "OOLU", "Matson": "MATS", "ZIM": "ZIMU", "Evergreen": "EGLV"}
LOCODE = {"yantian": "CNYTN", "shekou": "CNSHK", "ningbo": "CNNGB",
          "los_angeles": "USLAX", "long_beach": "USLGB"}
TRANSSHIP_HUBS = ["SGSIN", "KRPUS", "TWKHH"]
CONTAINER_TYPES = ["40HC", "40GP", "20GP"]
INCOTERMS = ["FOB"] * 6 + ["CIF"] * 3 + ["DDP"]  # 权重 60/30/10
# 真实感：箱主代码按船司（ISO 6346），船名池按船司（船属于船公司，不得混配）
BOX_OWNER = {"COSU": ["CSNU", "CCLU", "CBHU"], "OOLU": ["OOLU", "OOCU"], "MATS": ["MATU"],
             "ZIMU": ["ZIMU", "ZCSU"], "EGLV": ["EGHU", "EGSU", "EITU"]}
VESSELS = {"COSU": ["COSCO SHIPPING PISCES", "COSCO SHIPPING ROSE", "XIN LOS ANGELES"],
           "OOLU": ["OOCL TOKYO", "OOCL LONG BEACH", "OOCL BREMERHAVEN"],
           "MATS": ["MANOA", "DANIEL K. INOUYE", "MATSONIA"],
           "ZIMU": ["ZIM SAN DIEGO", "ZIM MOUNT EVEREST"],
           "EGLV": ["EVER FORTUNE", "EVER LAMBENT", "EVER LIBRA"]}
CUSTOMER_NAMES = ["Pacific Rim Distribution LLC", "Bluewave Electronics Inc.", "SummitTech Wholesale",
                  "Redwood Retail Group", "Lakeshore Trading Co.", "Ironpeak Supply LLC",
                  "Coastal Gadget Outlet", "Metro Accessories Depot", "Northgate Commerce Inc.",
                  "Silverline Imports", "Frontier Retail Partners", "Harborview Merchants LLC",
                  "Canyon Electronics Supply", "Beacon Hill Trading", "Aurora Goods Inc."]
SKU_NAMES = {"charger": ["20W USB-C Fast Charger", "65W GaN Wall Charger", "Dual-Port Car Charger",
                         "30W PD Travel Charger", "10W Wireless Charging Pad"],
             "cable": ["USB-C to USB-C Cable 1m", "USB-C Braided Cable 2m", "Lightning to USB-C Cable 1m",
                       "Micro-USB Cable 0.5m", "HDMI 2.1 Cable 1.5m"],
             "earbuds": ["TWS Earbuds A20", "ANC Wireless Earbuds Pro", "Sport Neckband Earphones",
                         "Gaming Earbuds Low-Latency"],
             "phone_case": ["TPU Clear Case", "Shockproof Rugged Case", "Leather Folio Case",
                            "MagSafe Silicone Case"]}


def iso6346_check_digit(code10):
    """ISO 6346 校验位：4 位箱主代码 + 6 位序号 → 第 11 位。"""
    vals = {c: v for c, v in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26,
             27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38])}
    total = sum((vals[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(code10))
    return str(total % 11 % 10)


def make_container_no(scac, rng):
    owner = rng.choice(BOX_OWNER[scac])
    serial = f"{rng.randint(0, 999999):06d}"
    return f"{owner}{serial}{iso6346_check_digit(owner + serial)}"
WAREHOUSES = ["LAX-DC1", "ONT-DC2", "RIV-DC3"]
CARRIERS = ["COSCO", "OOCL", "Matson", "ZIM", "Evergreen"]
CATEGORIES = ["charger", "cable", "earbuds", "phone_case"]
US_STATES = ["CA", "TX", "NJ", "IL", "GA", "WA", "FL", "PA"]
SUPPLIER_CITIES = ["Shenzhen", "Dongguan", "Ningbo", "Suzhou", "Xiamen"]

# 设计案例保留槽位（design_cases.py 全权填充，随机生成必须跳过）
DESIGN_SHIP_NUMS = {42, 55, 60, 70, 75, 80, 85, 90, 95, 99, 100, 105, 110, 115, 118}
DESIGN_PO_NUMS = {101, 102, 201, 203, 204, 205, 206, 207, 208, 209, 210, 211, 212, 213, 214, 230, 231}
DESIGN_SO_NUMS = {188, 201} | set(range(210, 227))
# 保留客户槽位：属性被 design_cases 强制，但可参与随机订单
FORCED_CUSTOMERS = {
    7: ("BB Reseller", "A"), 1: ("Pacific Retail Group", "A"), 2: ("Western Distributors", "A"),
    5: ("Midwest Supply Co", "B"), 9: ("Lonestar Trading", "B"),
    11: ("Sunbelt Imports", "C"), 13: ("Great Lakes Wholesale", "C"),
}
FORCED_SUPPLIERS = {
    3: ("Shenzhen Hongyu Electronics Co., Ltd.", "Shenzhen"),
    6: ("Ningbo Sunrise Electronics Co., Ltd.", "Ningbo"),
}
FORCED_SKUS = {
    1: ("USB-C 20W Charger", "charger", 6.50),
    2: ("USB-C Cable 1m", "cable", 2.80),
}


def D(s):
    return date.fromisoformat(s)


def iso_dt(day, hour):
    return f"{day.isoformat()}T{hour:02d}:00:00Z"


def dt_date(iso):
    return date.fromisoformat(iso[:10])


def weighted_choice(rng, weights: dict):
    r = rng.random()
    acc = 0.0
    for k, w in weights.items():
        acc += w
        if r <= acc:
            return int(k)
    return int(list(weights)[-1])


def build_world(cfg, rng):
    w = {}
    c = cfg["counts"]
    wc = cfg["world"]
    start, end, as_of = D(cfg["window"]["start"]), D(cfg["window"]["end"]), D(cfg["window"]["as_of"])

    # --- suppliers ---
    suppliers = {}
    name_pool = ["Huaxin", "Botai", "Kaiyuan", "Ruide", "Jinsheng", "Weilong", "Antai", "Chuangke"]
    for i in range(1, c["suppliers"] + 1):
        sid = f"SUP-{i:04d}"
        if i in FORCED_SUPPLIERS:
            name, city = FORCED_SUPPLIERS[i]
        else:
            name = f"{rng.choice(SUPPLIER_CITIES)} {name_pool[(i - 1) % len(name_pool)]} Electronics Co., Ltd."
            city = rng.choice(SUPPLIER_CITIES)
        suppliers[sid] = {"supplier_id": sid, "supplier_name": name, "city": city,
                          "lead_time_days": rng.randint(15, 35)}

    # --- skus ---
    skus = {}
    for i in range(1, c["skus"] + 1):
        kid = f"SKU-{i:04d}"
        if i in FORCED_SKUS:
            name, cat, price = FORCED_SKUS[i]
        else:
            cat = rng.choice(CATEGORIES)
            name = f"{rng.choice(SKU_NAMES[cat])} ({i:03d})"
            price = round(rng.uniform(2.0, 25.0), 2)
        sup = f"SUP-{rng.randint(1, c['suppliers']):04d}" if i not in FORCED_SKUS else "SUP-0003"
        skus[kid] = {"sku_id": kid, "sku_name": name, "category": cat,
                     "unit_price_usd": price, "supplier_id": sup}

    # --- customers ---
    customers = {}
    tier_pool = ["A"] * 3 + ["B"] * 5 + ["C"] * 7
    rng.shuffle(tier_pool)
    for i in range(1, c["customers"] + 1):
        cid = f"CUS-{i:04d}"
        if i in FORCED_CUSTOMERS:
            name, tier = FORCED_CUSTOMERS[i]
        else:
            name, tier = CUSTOMER_NAMES[i - 1], tier_pool[i - 1]
        customers[cid] = {"customer_id": cid, "customer_name": name, "tier": tier,
                          "us_state": rng.choice(US_STATES)}

    # --- sales orders + lines（跳过设计槽位）---
    sos, lines = {}, {}
    order_span = (as_of - start).days
    for i in range(1, c["sales_orders"] + 1):
        soid = f"SO-2026-{i:04d}"
        if i in DESIGN_SO_NUMS:
            continue
        od = start + timedelta(days=rng.randint(0, order_span))
        cid = f"CUS-{rng.randint(1, c['customers']):04d}"
        sos[soid] = {"so_id": soid, "customer_id": cid, "order_date": od}
        n_lines = weighted_choice(rng, wc["lines_per_so_weights"])
        for j in range(1, n_lines + 1):
            lid = f"SOL-{i:04d}-{j}"
            promise = od + timedelta(days=rng.randint(*wc["promise_offset_days"]))
            kid = f"SKU-{rng.randint(1, c['skus']):04d}"
            lines[lid] = {"so_line_id": lid, "so_id": soid, "sku_id": kid,
                          "qty": rng.randint(*wc["qty_range"]),
                          # 成交价 = 目录价 ±15%（真实 OMS 行价 ≠ 目录价）
                          "unit_price_usd": round(skus[kid]["unit_price_usd"] * rng.uniform(0.85, 1.15), 2),
                          "promised_delivery_date": promise, "line_status": "open"}

    # --- purchase orders（跳过设计槽位）---
    pos = {}
    for i in range(1, c["purchase_orders"] + 1):
        pid = f"PO-2026-{i:04d}"
        if i in DESIGN_PO_NUMS:
            continue
        kid = f"SKU-{rng.randint(1, c['skus']):04d}"
        sup = skus[kid]["supplier_id"]
        pd = start + timedelta(days=rng.randint(0, max(1, (as_of - start).days - 25)))
        pos[pid] = {"po_id": pid, "supplier_id": sup, "sku_id": kid,
                    "qty": rng.randint(500, 5000), "po_date": pd,
                    "expected_ready_date": pd + timedelta(days=suppliers[sup]["lead_time_days"]),
                    "status": "shipped"}

    # --- shipments：按齐货日分桶挂 PO ---
    ship_nums = [i for i in range(1, c["shipments"] + 1) if i not in DESIGN_SHIP_NUMS]
    po_sorted = sorted(pos.values(), key=lambda p: (p["expected_ready_date"], p["po_id"]))
    # 每船 PO 数：先随机，再调平使总和恰好等于可用 PO 数（保证 po_shipped_by N:1 不被破坏）
    lo, hi = wc["pos_per_shipment"]
    sizes = [rng.randint(lo, hi) for _ in ship_nums]
    diff, i = sum(sizes) - len(po_sorted), 0
    while diff != 0:
        j = i % len(sizes)
        if diff > 0 and sizes[j] > lo:
            sizes[j] -= 1
            diff -= 1
        elif diff < 0 and sizes[j] < hi:
            sizes[j] += 1
            diff += 1
        i += 1
    shipments = {}
    idx = 0
    for n, take in zip(ship_nums, sizes):
        sid = f"SHP-2026-{n:04d}"
        chunk = po_sorted[idx: idx + take]
        idx += len(chunk)
        etd = max(p["expected_ready_date"] for p in chunk) + timedelta(days=rng.randint(3, 7))
        etd = min(etd, end - timedelta(days=14))
        eta_initial = etd + timedelta(days=rng.randint(*wc["transit_days"]))
        shipments[sid] = {
            "shipment_id": sid, "mode": rng.choice(["ocean_fcl", "ocean_fcl", "ocean_lcl"]),
            "container_no": None,   # enrich_shipments 按船司生成 ISO 6346 柜号
            "vessel_voyage": None,  # enrich_shipments 按船司船名池生成
            "carrier_name": rng.choice(CARRIERS),
            "origin_port": rng.choice(PORTS_CN), "destination_port": rng.choice(PORTS_US),
            "destination_warehouse": rng.choice(WAREHOUSES),
            "etd": etd, "eta_initial": eta_initial, "eta_current": eta_initial,
            "ata": None, "customs_status": "not_filed",
            "missing_docs": [], "expedite_flag": False,
            "po_ids": [p["po_id"] for p in chunk], "status": "planned",
        }
        if rng.random() < wc["docs_missing_rate"]:
            shipments[sid]["missing_docs"] = rng.sample(
                ["commercial_invoice", "packing_list", "bill_of_lading", "isf"], rng.randint(1, 2))

    # --- 随机时间线（含延误注入）---
    milestones = []
    nr = cfg["noise_rates"]
    for sp in shipments.values():
        milestones.extend(build_timeline(sp, rng, wc, nr, as_of))

    # --- 分配（行 → 可行 shipment）---
    allocations = []
    ship_by_sku = {}
    for sp in shipments.values():
        for pid in sp["po_ids"]:
            ship_by_sku.setdefault(pos[pid]["sku_id"], []).append(sp)
    aid = 0
    buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
    for ln in lines.values():
        feas = [sp for sp in ship_by_sku.get(ln["sku_id"], [])
                if sp["eta_initial"] + timedelta(days=buf) <= ln["promised_delivery_date"]]
        if not feas:
            continue
        # JIT 排船：优先选 ETA 最贴近承诺日的船（余量小 → 延误才会真实击穿承诺）
        feas.sort(key=lambda sp: (ln["promised_delivery_date"] - sp["eta_initial"]).days)
        feas = feas[:3]
        n_split = 2 if rng.random() < 0.12 and len(feas) >= 2 else 1
        picks = rng.sample(feas, n_split)
        qty_left = ln["qty"]
        for k, sp in enumerate(picks):
            aid += 1
            q = qty_left if k == n_split - 1 else qty_left // 2
            qty_left -= q
            allocations.append({"allocation_id": f"ALC-{aid:06d}", "shipment_id": sp["shipment_id"],
                                "so_line_id": ln["so_line_id"], "allocated_qty": q})
        ln["line_status"] = "allocated"

    w.update(suppliers=suppliers, skus=skus, customers=customers, sos=sos, lines=lines,
             pos=pos, shipments=shipments, milestones=milestones, allocations=allocations,
             as_of=as_of)
    return w


def build_timeline(sp, rng, wc, nr, as_of):
    """单个随机 shipment 的干净事件流（≤ as_of），并顺带推进 eta_current / status。"""
    ev = []

    def add(etype, day, hour=None, new_eta=None):
        ev.append({"shipment_id": sp["shipment_id"], "event_type": etype,
                   "event_time": iso_dt(day, hour if hour is not None else rng.randint(1, 22)),
                   "new_eta": new_eta.isoformat() if new_eta else "",
                   "source_system": rng.choice(["carrier_edi", "carrier_edi", "forwarder_portal"])})

    add("booking_confirmed", sp["etd"] - timedelta(days=rng.randint(4, 9)))
    if sp["etd"] > as_of:
        return ev  # planned
    add("departed", sp["etd"])
    sp["status"] = "in_transit"

    # 延误注入：eta_multi_change 比例的 shipment 有 1-2 次，eta_change_3plus 比例有 3-4 次
    r = rng.random()
    n_changes = rng.randint(3, 4) if r < nr["eta_change_3plus"] else (
        rng.randint(1, 2) if r < nr["eta_multi_change"] else 0)
    eta = sp["eta_initial"]
    span = (eta - sp["etd"]).days
    times = sorted(rng.uniform(0.15, 0.9) for _ in range(n_changes))
    for f in times:
        t = sp["etd"] + timedelta(days=int(span * f))
        if t <= sp["etd"] or t > as_of:
            continue
        eta = eta + timedelta(days=rng.randint(*wc["delay_days_per_change"]))
        add("eta_change", t, new_eta=eta)
        sp["eta_current"] = eta
    if rng.random() < wc["transshipment_rate"]:
        t = sp["etd"] + timedelta(days=max(1, span // 2))
        if t <= as_of:
            add("transshipment", t)

    if sp["eta_current"] <= as_of:  # 已到港
        ata = sp["eta_current"]
        sp["ata"] = ata
        add("arrived", ata)
        sp["status"] = "arrived"
        filed = ata + timedelta(days=rng.randint(0, 1))
        if filed <= as_of:
            add("customs_filed", filed)
            sp["status"], sp["customs_status"] = "customs", "filed"
            if sp["missing_docs"]:
                hold = filed + timedelta(days=rng.randint(1, 2))
                if hold <= as_of:
                    add("customs_hold", hold)  # 文件缺失 → 查验扣留，停在海关
                    sp["customs_status"] = "hold"
            else:
                released = filed + timedelta(days=rng.randint(1, 3))
                if released <= as_of:
                    add("customs_released", released)
                    sp["customs_status"] = "released"
                    dlv = released + timedelta(days=rng.randint(2, 5))
                    if dlv <= as_of:
                        add("delivered", dlv)
                        sp["status"] = "delivered"
    return ev


def enrich_shipments(world, rng):
    """Tier 1 字段（D11）：单证号、SCAC、柜型、重量体积、贸易术语、UN/LOCODE。"""
    for sid in sorted(world["shipments"]):
        sp = world["shipments"][sid]
        scac = SCAC.get(sp.get("carrier_name") or "") or rng.choice(sorted(SCAC.values()))
        sp["carrier_scac"] = scac
        sp["booking_no"] = f"{scac}{rng.randint(10**8, 10**9 - 1)}"
        sp["mbl_no"] = f"{scac}{rng.randint(10**8, 10**9 - 1)}"
        sp["container_no"] = make_container_no(scac, rng)                       # ISO 6346
        sp["vessel_voyage"] = f"{rng.choice(VESSELS[scac])} {rng.randint(20, 89):03d}E"  # 东行航次
        ct = rng.choice(CONTAINER_TYPES)
        sp["container_type"] = ct
        if ct == "20GP":
            sp["gross_weight_kg"], sp["volume_cbm"] = rng.randint(8000, 24000), round(rng.uniform(18, 30), 1)
        else:
            sp["gross_weight_kg"], sp["volume_cbm"] = rng.randint(10000, 26000), round(rng.uniform(45, 67), 1)
        sp["incoterm"] = rng.choice(INCOTERMS)
        sp["origin_port_locode"] = LOCODE[sp["origin_port"]]
        sp["destination_port_locode"] = LOCODE[sp["destination_port"]]


def sync_po_status(world):
    """PO 状态与所在 shipment 状态联动（真实感：未开船的 PO 不该是 shipped）。"""
    m = {"planned": "ready", "in_transit": "shipped", "arrived": "shipped",
         "customs": "shipped", "delivered": "closed"}
    for sp in world["shipments"].values():
        for pid in sp["po_ids"]:
            world["pos"][pid]["status"] = m[sp["status"]]


def enrich_milestones(world, rng):
    """Tier 1 字段（D11）：事件地点（UN/LOCODE）与 ACT/EST 分类（DCSA event_classifier）。"""
    for m in world["milestones"]:
        sp = world["shipments"][m["shipment_id"]]
        et = m["event_type"]
        m.setdefault("event_classifier", "EST" if et == "eta_change" else "ACT")
        if "event_locode" not in m:
            if et in ("booking_confirmed", "departed"):
                m["event_locode"] = LOCODE[sp["origin_port"]]
            elif et == "transshipment":
                m["event_locode"] = rng.choice(TRANSSHIP_HUBS)
            else:  # eta_change 是对 POD 到达时间的预估；到港/清关/妥投发生在 POD
                m["event_locode"] = LOCODE[sp["destination_port"]]


def ensure_multi_customer_breach(world, cfg, rng):
    """确定性补齐：保证"一票延误击穿多客户"案例数 ≥ 配置值（plan §9 人为设计要求）。

    做法：找"已延误且恰好击穿单一客户"的随机船，把另一客户的可重排行（SKU 匹配、
    排船时按 eta_initial 可行、延误后被击穿）重新指派到该船。语义合法：计划员当初
    完全可能这样排船。设计案例船不动。
    """
    buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
    target = cfg["world"].get("multi_customer_breach_min", 0)
    lines, sos, customers = world["lines"], world["sos"], world["customers"]
    design = set(world.get("design_ship_case", {}))

    def cust(lid):
        return sos[lines[lid]["so_id"]]["customer_id"]

    alloc_by_ship, alloc_by_line = {}, {}
    for a in world["allocations"]:
        alloc_by_ship.setdefault(a["shipment_id"], []).append(a)
        alloc_by_line.setdefault(a["so_line_id"], []).append(a)

    def breach_custs(sp):
        out = set()
        for a in alloc_by_ship.get(sp["shipment_id"], []):
            ln = lines[a["so_line_id"]]
            if (sp["eta_current"] - ln["promised_delivery_date"]).days + buf > 0:
                out.add(cust(a["so_line_id"]))
        return out

    delayed = [sp for sp in sorted(world["shipments"].values(), key=lambda s: s["shipment_id"])
               if sp["status"] not in ("delivered",) and sp["eta_current"] > sp["eta_initial"]]
    n_multi = sum(1 for sp in delayed if len(breach_custs(sp)) >= 2)
    singles = [sp for sp in delayed if len(breach_custs(sp)) == 1 and sp["shipment_id"] not in design]
    ship_skus = {sp["shipment_id"]: {world["pos"][p]["sku_id"] for p in sp["po_ids"]}
                 for sp in world["shipments"].values()}

    def line_breaches_elsewhere(ln):
        """该行当前是否已在其他延误船上构成击穿（偷走会拆东墙补西墙）。"""
        for a in alloc_by_line.get(ln["so_line_id"], []):
            osp = world["shipments"][a["shipment_id"]]
            if osp["status"] != "delivered" and \
                    (osp["eta_current"] - ln["promised_delivery_date"]).days + buf > 0:
                return True
        return False

    def find_donors(sp, blocked):
        return sorted((ln for ln in lines.values()
                       if ln["line_status"] in ("open", "allocated")
                       and ln["sku_id"] in ship_skus[sp["shipment_id"]]
                       and cust(ln["so_line_id"]) not in blocked
                       and not any(a["shipment_id"] in design
                                   for a in alloc_by_line.get(ln["so_line_id"], []))
                       and not line_breaches_elsewhere(ln)
                       and (sp["eta_initial"] - ln["promised_delivery_date"]).days + buf <= 0  # 排船时可行
                       and (sp["eta_current"] - ln["promised_delivery_date"]).days + buf > 0),  # 延误后击穿
                      key=lambda x: x["so_line_id"])

    def repoint(sp, ln):
        old = alloc_by_line.get(ln["so_line_id"], [])
        world["allocations"] = [a for a in world["allocations"] if a["so_line_id"] != ln["so_line_id"]]
        for a in old:
            alloc_by_ship[a["shipment_id"]].remove(a)
        na = {"allocation_id": f"ALC-8{len(world['allocations']):05d}",
              "shipment_id": sp["shipment_id"], "so_line_id": ln["so_line_id"],
              "allocated_qty": ln["qty"]}
        world["allocations"].append(na)
        alloc_by_ship.setdefault(sp["shipment_id"], []).append(na)
        alloc_by_line[ln["so_line_id"]] = [na]
        ln["line_status"] = "allocated"

    for sp in singles:
        if n_multi >= target:
            break
        donors = find_donors(sp, breach_custs(sp))
        if donors:
            repoint(sp, donors[0])
            if len(breach_custs(sp)) >= 2:  # 以重算结果为准，不凭假设计数
                n_multi += 1
    # 后备 1：donor 不足时，用零击穿的延误船注入两个不同客户的行
    if n_multi < target:
        zeros = [sp for sp in delayed if not breach_custs(sp) and sp["shipment_id"] not in design]
        for sp in zeros:
            if n_multi >= target:
                break
            for _ in range(2):
                donors = find_donors(sp, breach_custs(sp))
                if not donors:
                    break
                repoint(sp, donors[0])
            if len(breach_custs(sp)) >= 2:
                n_multi += 1
    # 后备 2：收紧同船其他客户行的承诺日到击穿窗口 [eta_initial+buf, eta_current+buf)。
    # 排船时仍可行（≥ eta_initial+buf），延误后被击穿——世界语义合法，且每条延误船几乎必然可行。
    if n_multi < target:
        for sp in delayed:
            if n_multi >= target:
                break
            if sp["shipment_id"] in design or len(breach_custs(sp)) >= 2:
                continue
            for _ in range(2):  # 最多收紧两条（零击穿船需要两个客户）
                blocked = breach_custs(sp)
                if len(blocked) >= 2:
                    break
                cands = sorted((lines[a["so_line_id"]] for a in alloc_by_ship.get(sp["shipment_id"], [])
                                if lines[a["so_line_id"]]["line_status"] in ("allocated", "open")
                                and cust(a["so_line_id"]) not in blocked),
                               key=lambda x: x["so_line_id"])
                tightened = False
                for ln in cands:
                    new_promise = sp["eta_initial"] + timedelta(days=buf)  # 击穿幅度最大的合法值
                    if new_promise <= sos[ln["so_id"]]["order_date"]:
                        continue
                    ln["promised_delivery_date"] = new_promise
                    tightened = True
                    break
                if not tightened:
                    break
            if len(breach_custs(sp)) >= 2:
                n_multi += 1
    # 后备 3：多客户在船但未延误（或已到港）的船 → 注入延误 + 收紧承诺。
    # 这是 plan §9 "人为设计 ≥15 个多客户击穿案例"的批量实现形态，保证确定性达标。
    as_of = world["as_of"]
    if n_multi < target:
        for sp in sorted(world["shipments"].values(), key=lambda s: s["shipment_id"]):
            if n_multi >= target:
                break
            sid = sp["shipment_id"]
            if sid in design or sp["etd"] >= as_of:
                continue
            aboard = {cust(a["so_line_id"]) for a in alloc_by_ship.get(sid, [])
                      if lines[a["so_line_id"]]["line_status"] in ("allocated", "open")}
            if len(aboard) < 2 or len(breach_custs(sp)) >= 2:
                continue
            # 注入延误：剥离到港后事件，临近 as_of 追加 eta_change（"刚收到延误通知"）
            strip = {"arrived", "customs_filed", "customs_hold", "customs_released", "delivered"}
            world["milestones"] = [m for m in world["milestones"]
                                   if not (m["shipment_id"] == sid and m["event_type"] in strip)]
            new_eta = max(sp["eta_current"], as_of) + timedelta(days=rng.randint(4, 9))
            t = max(sp["etd"] + timedelta(days=1), as_of - timedelta(days=1))
            world["milestones"].append({"shipment_id": sid, "event_type": "eta_change",
                                        "event_time": f"{t.isoformat()}T11:00:00Z",
                                        "new_eta": new_eta.isoformat(),
                                        "source_system": "carrier_edi"})
            sp.update(eta_current=new_eta, status="in_transit", ata=None, customs_status="not_filed")
            for _ in range(2):  # 收紧两个客户的行到击穿窗口
                blocked = breach_custs(sp)
                if len(blocked) >= 2:
                    break
                cands = sorted((lines[a["so_line_id"]] for a in alloc_by_ship.get(sid, [])
                                if lines[a["so_line_id"]]["line_status"] in ("allocated", "open")
                                and cust(a["so_line_id"]) not in blocked),
                               key=lambda x: x["so_line_id"])
                tightened = False
                for ln in cands:
                    new_promise = sp["eta_initial"] + timedelta(days=buf)
                    if new_promise > sos[ln["so_id"]]["order_date"]:
                        ln["promised_delivery_date"] = new_promise
                        tightened = True
                        break
                if not tightened:
                    break
            if len(breach_custs(sp)) >= 2:
                n_multi += 1


def finalize_line_status(world):
    """行履约状态：全部承运 shipment 已妥投 → fulfilled。"""
    ships_of_line = {}
    for a in world["allocations"]:
        ships_of_line.setdefault(a["so_line_id"], []).append(a["shipment_id"])
    for lid, sids in ships_of_line.items():
        if all(world["shipments"][s]["status"] == "delivered" for s in sids):
            world["lines"][lid]["line_status"] = "fulfilled"
