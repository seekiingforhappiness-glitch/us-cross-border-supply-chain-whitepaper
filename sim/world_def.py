"""世界谱系（静态骨架）：公司画像 / 25 供应商 / 300 SKU / 40 客户 / 6 货代性格 /
航线船期表 / 3 仓库 / 初始库存。

为什么这样建（≤5 行）：① 与 datagen 物理隔离，故 ISO6346 校验位、真实船名池/SCAC 手法
自带一份副本（继承而不耦合，datagen 一行不动）。② 船期表按"周班×真实船司"预生成，订舱只
挂已存在的班次——航次号/船名/相邻班期天然合规。③ 供应商"惯性延期"、货代"泡重/账单错率/口径"
等只落参数与 flag，S1 不触发（异常留 S2）。所有随机来自传入 rng，无隐式系统时间。
"""
from datetime import date, timedelta

# ---- 真实感手法副本（手法继承自 datagen/world.py；此处自带以保物理隔离）----
LOCODE = {"yantian": "CNYTN", "shekou": "CNSHK", "ningbo": "CNNGB",
          "los_angeles": "USLAX", "long_beach": "USLGB", "new_york": "USNYC",
          "savannah": "USSAV", "rotterdam": "NLRTM", "hamburg": "DEHAM"}
# 内陆发货地 → 出海口岸（义乌走宁波，深圳走盐田/蛇口）
HUB_PORTS = {"shenzhen": ["yantian", "shekou"], "yiwu": ["ningbo"]}
CONTAINER_TYPES = ["40HC", "40GP", "20GP"]
TRANSSHIP_HUBS = ["SGSIN", "KRPUS", "TWKHH"]
INCOTERMS = ["FOB"] * 6 + ["CIF"] * 3 + ["DDP"]

# 船司：SCAC + 箱主代码（ISO6346）+ 真实船名池（船属于船公司，不得混配）
CARRIERS = {
    "COSCO":       {"scac": "COSU", "box": ["CSNU", "CCLU", "CBHU"],
                    "vessels": ["COSCO SHIPPING PISCES", "COSCO SHIPPING ROSE",
                                "XIN LOS ANGELES", "COSCO SHIPPING ARIES", "XIN SHANGHAI"]},
    "OOCL":        {"scac": "OOLU", "box": ["OOLU", "OOCU"],
                    "vessels": ["OOCL TOKYO", "OOCL LONG BEACH", "OOCL GERMANY",
                                "OOCL BREMERHAVEN", "OOCL SPAIN"]},
    "Matson":      {"scac": "MATS", "box": ["MATU"],
                    "vessels": ["MANOA", "DANIEL K. INOUYE", "MATSONIA", "MAUNAWILI"]},
    "Evergreen":   {"scac": "EGLV", "box": ["EGHU", "EGSU", "EITU"],
                    "vessels": ["EVER FORTUNE", "EVER LAMBENT", "EVER LIBRA",
                                "EVER ACE", "EVER FRONT"]},
    "ONE":         {"scac": "ONEY", "box": ["ONEU", "MOTU"],
                    "vessels": ["ONE COLUMBA", "ONE STORK", "ONE HAMBURG", "ONE APUS"]},
    "ZIM":         {"scac": "ZIMU", "box": ["ZIMU", "ZCSU"],
                    "vessels": ["ZIM SAN DIEGO", "ZIM MOUNT EVEREST", "ZIM NORFOLK"]},
    "Maersk":      {"scac": "MAEU", "box": ["MRKU", "MSKU"],
                    "vessels": ["MAERSK EDINBURGH", "MADRID MAERSK", "GJERTRUD MAERSK",
                                "MAERSK EMDEN"]},
    "MSC":         {"scac": "MSCU", "box": ["MSCU", "MEDU"],
                    "vessels": ["MSC GULSUN", "MSC ISABELLA", "MSC MIA", "MSC OSCAR"]},
    "CMA CGM":     {"scac": "CMDU", "box": ["CMAU", "CGMU"],
                    "vessels": ["CMA CGM MARCO POLO", "CMA CGM JACQUES SAADE",
                                "CMA CGM PALAIS ROYAL"]},
    "Hapag-Lloyd": {"scac": "HLCU", "box": ["HLXU", "HLBU"],
                    "vessels": ["ROTTERDAM EXPRESS", "HAMBURG EXPRESS", "BERLIN EXPRESS"]},
}
ROUTE_CARRIERS = {"USWC": ["COSCO", "OOCL", "Matson", "Evergreen"],
                  "USEC": ["COSCO", "ONE", "ZIM"],
                  "EU": ["Maersk", "MSC", "CMA CGM", "Hapag-Lloyd"]}

SKU_NAME_POOL = {
    "charger": ["USB-C Fast Charger", "GaN Wall Charger", "Car Charger", "Travel Charger",
                "Wireless Charging Pad", "Multi-Port Desktop Charger"],
    "cable": ["USB-C to USB-C Cable", "Braided Charging Cable", "Lightning Cable",
              "HDMI 2.1 Cable", "Micro-USB Cable", "Thunderbolt Cable"],
    "earbuds": ["TWS Earbuds", "ANC Wireless Earbuds", "Sport Neckband", "Gaming Earbuds"],
    "phone_case": ["Clear TPU Case", "Rugged Shockproof Case", "Leather Folio Case",
                   "MagSafe Silicone Case"],
    "seasonal_gift": ["LED Holiday String Lights", "Gift Bundle Box", "Festive Ornament Set",
                      "Seasonal Desk Lamp", "Cozy Blanket Throw"],
}
CN_SUPPLIER_CITIES = ["Shenzhen", "Dongguan", "Ningbo", "Yiwu", "Xiamen", "Suzhou", "Guangzhou"]
CN_SUPPLIER_STEMS = ["Huaxin", "Botai", "Kaiyuan", "Ruide", "Jinsheng", "Weilong", "Antai",
                     "Chuangke", "Hongyu", "Sunrise", "Zhongwei", "Lianfa", "Yuanda"]
US_STATES = ["CA", "TX", "NJ", "IL", "GA", "WA", "FL", "PA", "OH", "NC"]


def iso6346_check_digit(code10):
    """ISO 6346 校验位：4 位箱主代码 + 6 位序号 → 第 11 位（手法继承自 datagen/world）。"""
    vals = {c: v for c, v in zip("ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            [10, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 23, 24, 25, 26,
             27, 28, 29, 30, 31, 32, 34, 35, 36, 37, 38])}
    total = sum((vals[ch] if ch.isalpha() else int(ch)) * (2 ** i) for i, ch in enumerate(code10))
    return str(total % 11 % 10)


def make_container_no(carrier_key, rng):
    box = rng.choice(CARRIERS[carrier_key]["box"])
    serial = f"{rng.randint(0, 999999):06d}"
    return f"{box}{serial}{iso6346_check_digit(box + serial)}"


def D(s):
    return date.fromisoformat(s)


def _weighted_categories(cfg, rng, n):
    """按 share 分配 n 个 SKU 到五类目（确定性：先算配额再补齐）。"""
    cats = sorted(cfg["sku_categories"].items())
    quota = {k: int(round(v["share"] * n)) for k, v in cats}
    # 补齐/削减到恰好 n（对最大类目调整，确定性）
    diff = n - sum(quota.values())
    order = sorted(cats, key=lambda kv: -kv[1]["share"])
    i = 0
    while diff != 0:
        k = order[i % len(order)][0]
        if diff > 0:
            quota[k] += 1
            diff -= 1
        elif quota[k] > 0:
            quota[k] -= 1
            diff += 1
        i += 1
    out = []
    for k, _v in cats:
        out.extend([k] * quota[k])
    return out


def build_static_world(cfg, rng):
    """构建静态世界骨架（不含随时间流动的业务事件——那是 generators 的事）。"""
    w = {"profile": dict(cfg["company"])}
    start, end = D(cfg["window"]["start"]), D(cfg["window"]["end"])

    # --- 供应商（25 家；含 2 惯性延期 + 1 资质将过期，只落 flag，S1 不触发）---
    suppliers = {}
    n_sup = cfg["counts"]["suppliers"]
    chronic = set(rng.sample(range(1, n_sup + 1), cfg["counts"]["chronic_delay_suppliers"]))
    qual_exp = set(rng.sample(sorted(set(range(1, n_sup + 1)) - chronic),
                              cfg["counts"]["qual_expiring_suppliers"]))
    # F2：account 账期档（本体 0.11.0 Supplier.payment_terms_days）按供应商序确定性分配——
    # 不消费 rng → S1/S2 世界逐字节不变（补灌铁律：新字段不得漂移既有随机序列）。
    terms = cfg.get("enrichment", {}).get("supplier_terms_days", [30, 45, 60])
    for i in range(1, n_sup + 1):
        sid = f"SUP-{i:04d}"
        city = rng.choice(CN_SUPPLIER_CITIES)
        suppliers[sid] = {
            "supplier_id": sid,
            "supplier_name": f"{city} {CN_SUPPLIER_STEMS[(i - 1) % len(CN_SUPPLIER_STEMS)]} "
                             f"Electronics Co., Ltd.",
            "city": city,
            "lead_time_days": rng.randint(15, 35),
            "reliability": round(rng.uniform(0.80, 0.99), 3),   # 交期靠谱度（S2 用）
            "quality": round(rng.uniform(0.85, 0.99), 3),        # 质量水平（S2 用）
            "price_increase_tendency": round(rng.uniform(0.0, 0.30), 3),  # 涨价倾向（S2 用）
            "chronic_delay": i in chronic,                       # 惯性延期 flag（S2 触发）
            "qual_expiring": i in qual_exp,                      # 资质将过期 flag（S2 触发）
            "payment_terms_days": terms[(i - 1) % len(terms)],   # F2 账期（out 向 due 自动推算）
            # ontology schema 兼容字段（准入域，S1 给合规默认值）
            "factory_audit_status": "passed", "compliance_docs_status": "complete",
            "uflpa_risk_flag": "low", "origin_evidence_status": "verified",
        }
    w["suppliers"] = suppliers

    # --- SKU（300 个 across 5 类目含季节品）---
    skus = {}
    cats = _weighted_categories(cfg, rng, cfg["counts"]["skus"])
    sup_ids = sorted(suppliers)
    # F2：报关申报价值（钱区在途货值口径）——= 批发单价 × 常数因子，确定性派生不消费 rng
    # （报关价 ≈ 批发价的固定比例，真实感够用且不扰动既有随机序列）。
    dv_factor = cfg.get("enrichment", {}).get("declared_value_factor", 0.55)
    for i, cat in enumerate(cats, 1):
        kid = f"SKU-{i:04d}"
        spec = cfg["sku_categories"][cat]
        names = SKU_NAME_POOL[cat]
        # rng 调用序严格保持 S1 原样（sku_name→unit_price→supplier→velocity）——补灌不得漂移世界流
        skus[kid] = {
            "sku_id": kid,
            "sku_name": f"{rng.choice(names)} ({i:03d})",
            "category": cat,
            "unit_price_usd": round(rng.uniform(*spec["price"]), 2),
            "supplier_id": rng.choice(sup_ids),
            "seasonal": spec["seasonal"],
            "daily_velocity": rng.randint(*spec["velocity"]),    # 日销（驱动补货与库存消耗）
            "sku_status": "active",
        }
        # F2 报关价值（钱区在途货值）：unit_price × 常数，确定性派生不消费 rng
        skus[kid]["declared_value_usd"] = round(skus[kid]["unit_price_usd"] * dv_factor, 2)
    w["skus"] = skus

    # --- 客户（40 家三层：3 大 B/12 中型/25 长尾）---
    customers = {}
    cid_i = 0
    dest_regions = ["US-West", "US-West", "US-East", "US-Nat"]  # 多数走美西
    for tier, tspec in sorted(cfg["customer_tiers"].items()):
        for _ in range(tspec["count"]):
            cid_i += 1
            cid = f"CUS-{cid_i:04d}"
            customers[cid] = {
                "customer_id": cid, "customer_name": _company_name(rng, cid_i),
                "tier": tier, "us_state": rng.choice(US_STATES),
                "region": rng.choice(dest_regions),
                "order_interval_days": rng.randint(*tspec["order_interval_days"]),
                "tolerance_days": rng.randint(*tspec["tolerance_days"]),
                "lines_per_order_range": tspec["lines_per_order"],
                "next_order_offset": rng.randint(0, tspec["order_interval_days"][1]),  # 错峰起点
                "business_model": "wholesale", "sales_channel": "b2b",
                "ior_capability": "has_ior", "broker_status": "active",
                "credit_terms": "net30", "risk_tier": "low",
            }
    w["customers"] = customers

    # --- 6 货代性格模型 ---
    w["forwarders"] = {f["id"]: dict(f) for f in cfg["forwarders"]}

    # --- 航线与船期表（周班 × 真实船司）---
    w["routes"] = {k: dict(v, route_id=k) for k, v in cfg["routes"].items()}
    w["schedule"] = _build_schedule(cfg, rng, start, end)

    # --- 3 仓库 + 初始库存（安全库存 + 覆盖天数×日销）---
    w["warehouses"] = {wh["id"]: dict(wh) for wh in cfg["warehouses"]}
    w["inventory"] = _init_inventory(cfg, rng, skus, w["warehouses"], start)
    return w


def _company_name(rng, i):
    a = ["Pacific", "Bluewave", "Summit", "Redwood", "Lakeshore", "Ironpeak", "Coastal",
         "Metro", "Northgate", "Silverline", "Frontier", "Harborview", "Canyon", "Beacon",
         "Aurora", "Cascade", "Granite", "Meridian", "Vantage", "Keystone"]
    b = ["Distribution", "Electronics", "Wholesale", "Retail Group", "Trading Co.",
         "Supply", "Imports", "Commerce", "Merchants", "Partners"]
    return f"{a[(i - 1) % len(a)]} {b[(i * 7) % len(b)]} {['LLC', 'Inc.', 'Co.'][i % 3]}"


def _build_schedule(cfg, rng, start, end):
    """每条航线的每家船司按周班（7±jitter 天）预生成班次，直到窗口终点。"""
    sailings = []
    jitter = cfg["schedule_jitter_days"]
    sid = 0
    for route_id in sorted(ROUTE_CARRIERS):
        rspec = cfg["routes"][route_id]
        for carrier in ROUTE_CARRIERS[route_id]:
            base_weekday = rng.randint(0, 6)               # 该船司在该航线的固定发船工作日
            d = start + timedelta(days=(base_weekday - start.weekday()) % 7)
            while d <= end:
                sid += 1
                origin = rng.choice(rspec["origin"])
                dest = rng.choice(rspec["dest"])
                transit = rng.randint(*rspec["transit_days"])
                sailings.append({
                    "sailing_id": f"SAIL-{sid:05d}", "route_id": route_id, "carrier": carrier,
                    "scac": CARRIERS[carrier]["scac"],
                    "vessel": rng.choice(CARRIERS[carrier]["vessels"]),
                    "voyage": f"{rng.randint(20, 89):03d}E",  # 东行航次
                    "origin_port": origin, "dest_port": dest,
                    "transit_days": transit,
                    "etd": d, "eta": d + timedelta(days=transit), "booked": 0,
                })
                d = d + timedelta(days=7 + rng.randint(-jitter, jitter))  # 周班 6-8 天
    sailings.sort(key=lambda s: (s["etd"], s["sailing_id"]))
    return sailings


def _init_inventory(cfg, rng, skus, warehouses, start):
    """初始库存快照：每 (SKU, 仓) 一条 position，available = 安全库存 + 覆盖天数×区域日销。"""
    inv = {}
    ic = cfg["inventory"]
    region_share = ic["region_share"]
    for kid in sorted(skus):
        vel = skus[kid]["daily_velocity"]
        for wid in sorted(warehouses):
            region = warehouses[wid]["region"]
            share = region_share.get(region, 0.2)
            safety = rng.randint(*ic["safety_stock_range"])
            cover = rng.randint(*ic["init_cover_days"])
            avail = safety + int(round(vel * share * cover))
            inv[(kid, wid)] = {
                "inventory_position_id": f"INV-{kid[-4:]}-{wid}",
                "sku_id": kid, "warehouse_id": wid,
                "available_qty": avail, "reserved_qty": 0, "in_transit_qty": 0,
                "quarantine_qty": 0, "safety_stock": safety,
            }
    return inv
