"""v0.3 准入世界生成（V2）。

关键设计：使用独立随机流（seed+1000），控制塔既有数据不受任何扰动——零回归从源头保证。
生成态案件视为"迁移导入的历史"（不带 action_log），新动作从 V3 起全部留审计。
"""
import random
from datetime import date, timedelta

HTS_POOL = {"charger": "8504.40.95", "cable": "8544.42.90",
            "earbuds": "8518.30.20", "phone_case": "3926.90.99"}
CANDIDATE_NAMES = ["65W GaN Charger Pro", "100W Desktop Charger", "Slim MagSafe Power Bank",
                   "USB4 Cable 1m", "Braided Lightning Cable 3m", "Solar Camping Charger",
                   "Kids Tablet Case", "ANC Earbuds Gen2", "Bone-Conduction Headset",
                   "Smart Plug US", "Silicone Baby Spoon Set", "Pet GPS Tracker",
                   "RGB Gaming Mousepad", "Foldable Phone Stand", "Car Vent Mount"]
# 前 6 个槽位属性钉死（设计案例依赖），其余随机
FIXED_CANDIDATES = {
    9001: {"sku_name": "65W GaN Charger Pro", "category": "charger", "battery": False,
           "food": False, "children": False, "platform": "amazon", "price": 11.5},
    9002: {"sku_name": "Solar Camping Charger", "category": "charger", "battery": True,
           "food": False, "children": False, "platform": "amazon", "price": 18.0},
    9003: {"sku_name": "Kids Tablet Case", "category": "phone_case", "battery": False,
           "food": False, "children": True, "platform": "walmart", "price": 6.2},
    9004: {"sku_name": "Silicone Baby Spoon Set", "category": "phone_case", "battery": False,
           "food": True, "children": True, "platform": "amazon", "price": 4.8},
    9005: {"sku_name": "ANC Earbuds Gen2", "category": "earbuds", "battery": True,
           "food": False, "children": False, "platform": "tiktok_shop", "price": 22.0},
}
STATUS_DIST = (["draft"] * 5 + ["in_precheck"] * 8 + ["plan_ready"] * 6 + ["priced"] * 6
               + ["approved"] * 4 + ["quote_with_conditions"] * 2 + ["rejected"] * 2
               + ["needs_more_info"] * 2)
STATUS_RANK = {"draft": 0, "in_precheck": 1, "plan_ready": 2, "priced": 3,
               "approved": 4, "quote_with_conditions": 4, "rejected": 4, "needs_more_info": 1}
SEV_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
COST_KEYS = ["product_cost_usd", "first_mile_cost_usd", "international_freight_usd",
             "duty_tax_usd", "customs_brokerage_usd", "warehouse_cost_usd",
             "last_mile_cost_usd", "returns_allowance_usd", "risk_buffer_usd"]


def extend_shared_objects(world, rng):
    """E1/E2：给共享对象补准入字段。设计案例依赖的客户属性钉死，其余随机。"""
    for cid, c in world["customers"].items():
        c["business_model"] = rng.choice(["platform_seller", "brand_dtc", "trader",
                                          "service_provider", "other"])
        c["sales_channel"] = rng.choice(["amazon_us", "walmart_dsv", "shopify_dtc", "mixed"])
        c["ior_capability"] = rng.choice(["has_ior", "has_ior", "needs_partner", "unknown"])
        c["broker_status"] = rng.choice(["has_broker", "needs_broker", "unknown"])
        c["credit_terms"] = rng.choice(["NET30", "NET45", "NET60", "prepaid"])
        c["risk_tier"] = rng.choice(["low", "low", "medium", "high"])
    world["customers"]["CUS-0007"]["ior_capability"] = "has_ior"       # AC-DEMO-A（G2 通过）
    world["customers"]["CUS-0011"]["ior_capability"] = "needs_partner"  # AC-DEMO-C（G2 阻断）
    for s in world["suppliers"].values():
        s["factory_audit_status"] = rng.choice(["passed", "passed", "pending", "not_started"])
        s["compliance_docs_status"] = rng.choice(["verified", "provided", "partial", "missing"])
        s["uflpa_risk_flag"] = rng.random() < 0.1
        s["origin_evidence_status"] = rng.choice(["verified", "provided", "missing"])
    # 现有目录 SKU = active，准入字段留空（遗留数据语义）
    for k in world["skus"].values():
        k.setdefault("sku_status", "active")
    # 候选 SKU：SKU-9001..9015
    for i in range(9001, 9016):
        fix = FIXED_CANDIDATES.get(i)
        cat = fix["category"] if fix else rng.choice(list(HTS_POOL))
        name = fix["sku_name"] if fix else CANDIDATE_NAMES[(i - 9001) % len(CANDIDATE_NAMES)]
        world["skus"][f"SKU-{i}"] = {
            "sku_id": f"SKU-{i}", "sku_name": name, "category": cat,
            "unit_price_usd": fix["price"] if fix else round(rng.uniform(3, 30), 2),
            "supplier_id": f"SUP-{rng.randint(1, 10):04d}", "sku_status": "candidate",
            "declared_value_usd": round(rng.uniform(2, 20), 2),
            "package_weight_kg": round(rng.uniform(0.05, 1.2), 2),
            "package_l_cm": rng.randint(5, 30), "package_w_cm": rng.randint(5, 20),
            "package_h_cm": rng.randint(2, 12),
            "battery_flag": fix["battery"] if fix else rng.random() < 0.3,
            "food_contact_flag": fix["food"] if fix else rng.random() < 0.1,
            "children_product_flag": fix["children"] if fix else rng.random() < 0.15,
            "material": rng.choice(["ABS+PC", "silicone", "TPU", "aluminum", "copper+nylon"]),
            "use_case": rng.choice(["home charging", "travel", "outdoor", "kids", "audio"]),
            "origin_country": "CN",
            "platform": fix["platform"] if fix else rng.choice(
                ["amazon", "walmart", "tiktok_shop", "shopify", "other"]),
        }


def _findings_for(rng, aid, sku, seq, force=None):
    """按监管触发生成 finding；force 用于设计案例。"""
    out = []

    def add(ftype, sev, hts="", pga="none", doc="", ev="provided", rec="accept", title=None):
        out.append({"compliance_finding_id": f"CF-{next(seq):05d}", "admission_case_id": aid,
                    "finding_title": title or f"{ftype} review", "finding_type": ftype,
                    "severity": sev, "hts_candidate": hts, "pga_agency": pga,
                    "required_document": doc, "evidence_status": ev, "recommendation": rec})

    if force == "demo_a":
        add("hts", "low", hts=HTS_POOL[sku["category"]], ev="verified", title="HTS 归类确认")
        add("pga", "low", pga="FCC", doc="FCC SDoC", ev="verified", title="FCC 符合性")
        return out
    if force == "demo_b":
        add("hts", "low", hts=HTS_POOL[sku["category"]], ev="verified")
        add("uflpa", "critical", doc="supply chain tracing docs", ev="missing",
            rec="escalate", title="UFLPA 供应链追溯缺失")
        return out
    add("hts", rng.choice(["low", "low", "medium"]), hts=HTS_POOL[sku["category"]],
        ev=rng.choice(["verified", "provided"]))
    if sku.get("battery_flag"):
        add("certification", rng.choice(["medium", "high"]), doc="UN38.3 / MSDS",
            ev=rng.choice(["provided", "missing"]), rec="more_docs", title="锂电池运输认证")
    if sku.get("food_contact_flag"):
        add("pga", "medium", pga="FDA", doc="FDA food-contact test report",
            ev=rng.choice(["provided", "missing"]), title="FDA 食品接触")
    if sku.get("children_product_flag"):
        add("pga", rng.choice(["medium", "high"]), pga="CPSC", doc="CPC + ASTM F963",
            ev=rng.choice(["provided", "missing"]), title="CPSC 儿童产品")
    return out


def _scenarios_for(rng, plan_id, seq, negative_conservative=False):
    base_cost = {k: round(rng.uniform(300, 4000), 2) for k in COST_KEYS}
    total = sum(base_cost.values())
    quote = round(total * rng.uniform(1.08, 1.25), 2)
    out = []
    for stype, factor in (("conservative", 1.15), ("base", 1.0), ("optimistic", 0.9)):
        costs = {k: round(v * factor, 2) for k, v in base_cost.items()}
        if negative_conservative and stype == "conservative":
            costs = {k: round(v * 1.45, 2) for k, v in base_cost.items()}  # 强制毛利为负
        s_total = sum(costs.values())
        margin = round(quote - s_total, 2)
        out.append({"cost_scenario_id": f"CS-{next(seq):05d}", "logistics_plan_id": plan_id,
                    "scenario_type": stype, "quote_price_usd": quote, **costs,
                    "gross_margin_usd": margin,
                    "gross_margin_rate": round(margin / quote, 4)})
    return out


def build_admission_world(world, cfg, rng):
    """生成 40 个案件（含 5 个设计案例，槽位 0031-0035）+ 门禁真值表。"""
    cases, findings, plans, scenarios, gates = [], [], [], [], []
    fseq = iter(range(1, 10000))
    pseq = iter(range(1, 10000))
    sseq = iter(range(1, 10000))
    start = date.fromisoformat(cfg["window"]["as_of"])
    DESIGN = {31: "A", 32: "B", 33: "C", 34: "D", 35: "E"}

    for i in range(1, 41):
        aid = f"AC-2026-{i:04d}"
        label = DESIGN.get(i)
        if label == "A":
            cid, kid, status, itc = "CUS-0007", "SKU-9001", "priced", "DDP"
        elif label == "B":
            cid, kid, status, itc = "CUS-0005", "SKU-9002", "priced", "DAP"
        elif label == "C":
            cid, kid, status, itc = "CUS-0011", "SKU-9003", "in_precheck", "DDP"
        elif label == "D":
            cid, kid, status, itc = "CUS-0009", "SKU-9004", "needs_more_info", "DAP"
        elif label == "E":
            cid, kid, status, itc = "CUS-0002", "SKU-9005", "priced", "FOB"
        else:
            cid = f"CUS-{rng.randint(1, 15):04d}"
            kid = f"SKU-{rng.randint(9001, 9015)}"
            status = STATUS_DIST[(i - 1) % len(STATUS_DIST)]
            itc = rng.choice(["FOB", "FOB", "DAP", "DDP", "tbd"])
        sku = world["skus"][kid]
        case = {"admission_case_id": aid,
                "case_title": f"{sku['sku_name']} 准入报价（{world['customers'][cid]['customer_name']}）",
                "customer_id": cid, "sku_id": kid,
                "request_type": "ddp_quote" if itc == "DDP" else rng.choice(
                    ["new_sku", "new_sku", "dap_quote", "plan_review"]),
                "incoterm_candidate": itc,
                "target_launch_date": (start + timedelta(days=rng.randint(30, 120))).isoformat(),
                "monthly_order_estimate": rng.randint(200, 5000),
                "risk_level": "", "status": status, "decision": "", "decision_reason": "",
                "conditions": ""}

        # findings（状态达 in_precheck 及以上，或 needs_more_info）
        if STATUS_RANK[status] >= 1:
            force = {"A": "demo_a", "B": "demo_b"}.get(label)
            fs = _findings_for(rng, aid, sku, fseq, force=force)
            findings.extend(fs)
            case["risk_level"] = max((f["severity"] for f in fs), key=lambda s: SEV_RANK[s])
        # plans（plan_ready 及以上）
        if STATUS_RANK[status] >= 2 and status != "needs_more_info":
            incoterm = itc if itc != "tbd" else "FOB"
            # G2 语义：客户无 IOR 时生成世界里不可能存在 DDP 方案
            if incoterm == "DDP" and world["customers"][cid]["ior_capability"] != "has_ior":
                incoterm = "DAP"
            plan_id = f"LP-{next(pseq):05d}"
            route = rng.choice(["ocean_fcl", "ocean_fcl", "ocean_lcl", "air_freight", "express"])
            plans.append({"logistics_plan_id": plan_id, "admission_case_id": aid,
                          "plan_name": f"{route}/{incoterm} 方案", "route_type": route,
                          "incoterm": incoterm,
                          "origin_port_locode": rng.choice(["CNYTN", "CNSHK", "CNNGB"]),
                          "destination_port_locode": rng.choice(["USLAX", "USLGB"]),
                          "us_warehouse_region": rng.choice(["west", "west", "central", "platform"]),
                          "last_mile_method": rng.choice(["UPS", "FedEx", "USPS", "LTL", "platform"]),
                          "estimated_transit_days": {"express": rng.randint(5, 8),
                                                     "air_freight": rng.randint(8, 12),
                                                     "ocean_fcl": rng.randint(28, 38),
                                                     "ocean_lcl": rng.randint(32, 45),
                                                     "warehouse_fulfillment": rng.randint(30, 40)}[route],
                          "sla_risk": rng.choice(["low", "low", "medium", "high"]),
                          "operational_notes": ""})
            # scenarios（priced 及以上）
            if STATUS_RANK[status] >= 3:
                ss = _scenarios_for(rng, plan_id, sseq, negative_conservative=(label == "E"))
                if incoterm == "DDP":  # DDP 成本门禁的世界一致性
                    for s in ss:
                        s["duty_tax_usd"] = max(s["duty_tax_usd"], 200.0)
                        s["customs_brokerage_usd"] = max(s["customs_brokerage_usd"], 150.0)
                        s["risk_buffer_usd"] = max(s["risk_buffer_usd"], 100.0)
                        t = sum(s[k] for k in COST_KEYS)
                        s["gross_margin_usd"] = round(s["quote_price_usd"] - t, 2)
                        s["gross_margin_rate"] = round(s["gross_margin_usd"] / s["quote_price_usd"], 4)
                if label == "A":  # 设计要求：DDP 成本抬升后三情景毛利仍为正
                    q = round(max(sum(s[k] for k in COST_KEYS) for s in ss) * 1.12, 2)
                    for s in ss:
                        s["quote_price_usd"] = q
                        t = sum(s[k] for k in COST_KEYS)
                        s["gross_margin_usd"] = round(q - t, 2)
                        s["gross_margin_rate"] = round(s["gross_margin_usd"] / q, 4)
                scenarios.extend(ss)
        # 终态历史案件补 decision；approved 的 SKU 转 active（E1/N5）
        if status in ("approved", "quote_with_conditions"):
            case["decision"] = "approve" if status == "approved" else "quote_with_conditions"
            case["decision_reason"] = "历史批准（迁移导入）"
            world["skus"][kid]["sku_status"] = "active"
        elif status == "rejected":
            case["decision"] = "reject"
            case["decision_reason"] = rng.choice(["critical 合规风险", "毛利不达标", "客户资质不足"])
        cases.append(case)

    gates = [
        {"case_label": "AC-DEMO-A", "admission_case_id": "AC-2026-0031", "gate": "happy_path",
         "attempt": "B5 approve", "expected": "allowed", "reason": "无 critical、DDP 有 IOR、已 priced"},
        {"case_label": "AC-DEMO-B", "admission_case_id": "AC-2026-0032", "gate": "G1",
         "attempt": "B5 approve", "expected": "blocked", "reason": "critical UFLPA finding 未 verified"},
        {"case_label": "AC-DEMO-C", "admission_case_id": "AC-2026-0033", "gate": "G2",
         "attempt": "B3 build DDP plan", "expected": "blocked", "reason": "customer ior=needs_partner"},
        {"case_label": "AC-DEMO-C", "admission_case_id": "AC-2026-0033", "gate": "G2-alt",
         "attempt": "B3 build DAP plan", "expected": "allowed", "reason": "DAP 不需要 IOR"},
        {"case_label": "AC-DEMO-D", "admission_case_id": "AC-2026-0034", "gate": "G3",
         "attempt": "B5 approve", "expected": "blocked", "reason": "案件未 priced（needs_more_info）"},
        {"case_label": "AC-DEMO-A", "admission_case_id": "AC-2026-0031", "gate": "B4-DDP-cost",
         "attempt": "B4 scenario with duty_tax=0", "expected": "blocked", "reason": "DDP 情景税费必须>0"},
        {"case_label": "AC-DEMO-E", "admission_case_id": "AC-2026-0035", "gate": "margin_transparency",
         "attempt": "B5 quote_with_conditions", "expected": "allowed",
         "reason": "毛利为负不禁止但必须如实呈现，经理附条件"},
    ]
    world["admission"] = {"cases": cases, "findings": findings, "plans": plans,
                          "scenarios": scenarios, "gates": gates}
