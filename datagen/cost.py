"""v0.4 费用/账单对账数据生成（X2）。

关键设计（与 v0.3 admission 同构）：独立随机流 random.Random(seed+2000)，不消耗既有
随机流——控制塔与准入数据逐字节零扰动，可复现性从源头保证。

产出对象（写入 world["cost"]）：
  containers   每票 ≥1 柜（primary 迁移自 shipment 现有字段），≥15 票升 2-3 柜
  rate_card    费率卡行表（charge_code, scope, key, rate_usd）
  expected_costs  按票展开的基准（DET/DEM/CHS/ACC 无基准——R6 判据）
  invoices / invoice_lines  按物流阶段开票（carrier/forwarder/warehouse/last_mile）
  anomalies    ground truth（按引擎聚合键 (shipment_id, rule) 每键一行）

设计案例 CD-A..CD-F 确定性选船、互不重叠、被选船排除随机噪声（不误报反例的基础）。
"""
import hashlib
import json
from datetime import timedelta

from .world import DESIGN_SHIP_NUMS, make_container_no, iso6346_check_digit

# --- 费种常量（manual §1.3 charge_code 枚举）---
# 票级费种（每票一行，无柜号）
DOC_USD = 75.0
CUS_USD = 150.0
DTY_USD = 600.0
WHS_USD = 250.0
STO_USD = 180.0
# THC 按目的港每柜
THC_BY_PORT = {"USLAX": 385.0, "USLGB": 365.0}
# LMD 按目的仓
LMD_BY_WAREHOUSE = {"LAX-DC1": 950.0, "ONT-DC2": 1050.0, "RIV-DC3": 1100.0}
FSC_RATE = 0.12  # FSC = OFT × 12% 每柜
# 计划外费种（无 ExpectedCost 基准——R6 判据）
UNPLANNED_CODES = {"DET", "DEM", "CHS", "ACC"}
CHS_USD = 120.0
ACC_USD = 85.0

CONTAINER_TYPES_EXTRA = ["40HC", "40GP", "20GP"]


def _num(shipment_id):
    return int(shipment_id.split("-")[-1])


def _route_offset(route):
    """航线基线偏移（确定性：用稳定哈希，不依赖 PYTHONHASHSEED）。"""
    key = f"{route[0]}-{route[1]}".encode()
    return int(hashlib.sha256(key).hexdigest(), 16) % 300


def _oft_rate(route, ctype, rng):
    """OFT 按 航线×柜型定值（同航线同柜型稳定）。40HC 2300-2600，40GP 略低，20GP 更低。"""
    base = 2300 + _route_offset(route)  # 2300-2599，40HC 基准区间（航线内稳定）
    adj = {"40HC": 0, "40GP": -180, "20GP": -520}[ctype]
    return float(base + adj)


def _iso_container(scac, ctype, rng):
    """复用 world.make_container_no（scac 决定箱主代码 + ISO 6346 校验位）。"""
    return make_container_no(scac, rng)


def build_cost_world(world, cfg, rng):
    """生成费用对账全套数据。rng 必须是 random.Random(seed+2000)。"""
    ccfg = cfg["cost"]
    tol = ccfg["rate_tolerance"]
    det_daily = ccfg["detention_daily_usd"]
    unplanned_high = ccfg["unplanned_high_threshold_usd"]
    high_thr = 1 + 0.20  # R4 超收 >20% → high（manual §3）
    window_end = cfg["window"]["end"]

    ships = world["shipments"]
    ship_ids = sorted(ships)

    # ------------------------------------------------------------------
    # 1. 确定性选设计船（互不重叠）
    # ------------------------------------------------------------------
    design_nums = set(DESIGN_SHIP_NUMS)

    def is_design(sid):
        return _num(sid) in design_nums

    # CD-C / CD-D：延误(>0)且已妥投的非设计船（在途船不会收到滞箱账单）
    delayed_delivered = [
        sid for sid in ship_ids
        if not is_design(sid)
        and ships[sid]["status"] == "delivered"
        and (ships[sid]["eta_current"] - ships[sid]["eta_initial"]).days > 0
    ]
    # 干净已妥投无延误船（CD-E 反例）
    clean_delivered = [
        sid for sid in ship_ids
        if not is_design(sid)
        and ships[sid]["status"] == "delivered"
        and (ships[sid]["eta_current"] - ships[sid]["eta_initial"]).days == 0
    ]
    # 已妥投船（CD-A 超收 / CD-F 仓库重复：需 warehouse 发票存在，即 delivered）
    delivered_all = [
        sid for sid in ship_ids
        if not is_design(sid) and ships[sid]["status"] == "delivered"
    ]

    cd = {}  # case_id -> shipment_id
    used = set()

    def take(pool):
        for sid in pool:
            if sid not in used:
                used.add(sid)
                return sid
        return None

    # CD-C：延误已妥投，强制 FOB，注入 DET → R6 high + FOB 可 rebill
    cd["CD-C"] = take(delayed_delivered)
    # CD-D：另一延误已妥投，强制 DDP，注入 DET → R6（X3 测 rebill 被 G4 拒）
    cd["CD-D"] = take(delayed_delivered)
    # CD-A：某已妥投船 OFT 超收 30% → R4 high
    cd["CD-A"] = take(delivered_all)
    # CD-B：某船 primary 柜 THC 在 carrier+forwarder 各计一次 → R5（跨票重复）
    cd["CD-B"] = take(delivered_all)
    # CD-F：warehouse 发票 WHS 重复两行 → R5（同票重复）
    cd["CD-F"] = take(delivered_all)
    # CD-E：无延误已妥投，全部账单恰在基准 → 零异常（不误报反例）
    cd["CD-E"] = take(clean_delivered)

    # 覆写设计船 incoterm（emit 前生效）
    if cd["CD-C"]:
        ships[cd["CD-C"]]["incoterm"] = "FOB"
    if cd["CD-D"]:
        ships[cd["CD-D"]]["incoterm"] = "DDP"

    ship_case = {v: k for k, v in cd.items() if v}   # shipment_id -> CD-x
    design_or_case = {sid for sid in ship_ids if is_design(sid)} | set(ship_case)

    # ------------------------------------------------------------------
    # 2. Container 迁移（每票 primary + 多柜升级）
    # ------------------------------------------------------------------
    containers = []              # dict 行
    ship_containers = {}         # shipment_id -> [container_no...]（primary 首位）

    for sid in ship_ids:
        sp = ships[sid]
        primary = {
            "container_no": sp["container_no"],
            "shipment_id": sid,
            "container_type": sp["container_type"],
            "is_primary": True,
            "free_days": rng.randint(5, 7),
            "gross_weight_kg": sp["gross_weight_kg"],
            "volume_cbm": sp["volume_cbm"],
        }
        containers.append(primary)
        ship_containers[sid] = [primary["container_no"]]

    # 多柜升级：确定性选 ≥15 票非设计 ocean_fcl，各加 1-2 柜
    fcl_candidates = [sid for sid in ship_ids
                      if ships[sid]["mode"] == "ocean_fcl" and not is_design(sid)]
    multi_targets = fcl_candidates[:18]  # ≥15，留裕度
    for sid in multi_targets:
        sp = ships[sid]
        scac = sp["carrier_scac"]
        extra = rng.randint(1, 2)
        for _ in range(extra):
            ctype = rng.choice(CONTAINER_TYPES_EXTRA)
            if ctype == "20GP":
                gw, vol = rng.randint(8000, 24000), round(rng.uniform(18, 30), 1)
            else:
                gw, vol = rng.randint(10000, 26000), round(rng.uniform(45, 67), 1)
            cno = _iso_container(scac, ctype, rng)
            containers.append({
                "container_no": cno, "shipment_id": sid, "container_type": ctype,
                "is_primary": False, "free_days": rng.randint(5, 7),
                "gross_weight_kg": gw, "volume_cbm": vol,
            })
            ship_containers[sid].append(cno)

    ctype_of = {c["container_no"]: c["container_type"] for c in containers}
    free_days_of = {c["container_no"]: c["free_days"] for c in containers}

    # ------------------------------------------------------------------
    # 3. 费率卡（rate_card 行表）
    # ------------------------------------------------------------------
    rate_card = []           # {charge_code, scope, key, rate_usd}
    oft_rate = {}            # (route, ctype) -> rate
    routes = sorted({(ships[s]["origin_port_locode"], ships[s]["destination_port_locode"])
                     for s in ship_ids})
    for route in routes:
        for ctype in CONTAINER_TYPES_EXTRA:
            r = _oft_rate(route, ctype, rng)
            oft_rate[(route, ctype)] = r
            rate_card.append({"charge_code": "OFT", "scope": "route_ctype",
                              "key": f"{route[0]}-{route[1]}|{ctype}", "rate_usd": round(r, 2)})
    for port, r in THC_BY_PORT.items():
        rate_card.append({"charge_code": "THC", "scope": "dest_port", "key": port, "rate_usd": r})
    rate_card.append({"charge_code": "FSC", "scope": "pct_of_oft", "key": "12pct",
                      "rate_usd": round(FSC_RATE, 4)})
    for code, r in [("DOC", DOC_USD), ("CUS", CUS_USD), ("DTY", DTY_USD),
                    ("WHS", WHS_USD), ("STO", STO_USD)]:
        rate_card.append({"charge_code": code, "scope": "per_shipment", "key": "flat", "rate_usd": r})
    for wh, r in LMD_BY_WAREHOUSE.items():
        rate_card.append({"charge_code": "LMD", "scope": "dest_warehouse", "key": wh, "rate_usd": r})

    def oft_for(sp, ctype):
        route = (sp["origin_port_locode"], sp["destination_port_locode"])
        return oft_rate[(route, ctype)]

    # ------------------------------------------------------------------
    # 4. ExpectedCost（按票展开基准；柜级费种按柜一行）
    # ------------------------------------------------------------------
    expected_costs = []
    ec_seq = [0]

    def add_ec(sid, code, baseline, container_no=None):
        ec_seq[0] += 1
        expected_costs.append({
            "expected_cost_id": f"EC-{ec_seq[0]:06d}", "shipment_id": sid,
            "charge_code": code, "container_no": container_no or "",
            "baseline_usd": round(baseline, 2), "source": "rate_card"})

    for sid in ship_ids:
        sp = ships[sid]
        cnos = ship_containers[sid]
        dport = sp["destination_port_locode"]
        wh = sp["destination_warehouse"]
        # 柜级：OFT / FSC / THC 每柜一行
        for cno in cnos:
            oft = oft_for(sp, ctype_of[cno])
            add_ec(sid, "OFT", oft, cno)
            add_ec(sid, "FSC", oft * FSC_RATE, cno)
            add_ec(sid, "THC", THC_BY_PORT[dport], cno)
        # 票级
        add_ec(sid, "DOC", DOC_USD)
        add_ec(sid, "CUS", CUS_USD)
        add_ec(sid, "DTY", DTY_USD)
        add_ec(sid, "WHS", WHS_USD)
        add_ec(sid, "STO", STO_USD)
        add_ec(sid, "LMD", LMD_BY_WAREHOUSE[wh])
        # DET/DEM/CHS/ACC 无基准（不写 ExpectedCost）——R6 判据

    # ------------------------------------------------------------------
    # 5. 发票生成（按物流阶段开票）
    # ------------------------------------------------------------------
    invoices = []
    invoice_lines = []
    inv_seq = [0]
    il_seq = [0]
    # 每票每（charge_code, container）的基准金额，供随机/设计注入引用
    # 异常收集：per (shipment, rule) 聚合
    #   R4: {shipment: {"lines":[il...], "over":sum}}
    #   R5: {shipment: {"lines":[il_of_dup...], "amount":sum}}
    #   R6: {shipment: {"lines":[il...], "amount":sum, "det_days":n}}
    r4 = {}   # rate_overbilling
    r5 = {}   # duplicate_charge
    r6 = {}   # unplanned_charge
    case_of_anom = {}  # (shipment, rule) -> case_id

    def acc_r4(sid, il_id, over, ratio):
        d = r4.setdefault(sid, {"lines": [], "over": 0.0, "max_ratio": 0.0})
        d["lines"].append(il_id)
        d["over"] += over
        d["max_ratio"] = max(d["max_ratio"], ratio)

    def acc_r5(sid, il_id, amount):
        d = r5.setdefault(sid, {"lines": [], "amount": 0.0})
        d["lines"].append(il_id)
        d["amount"] += amount

    def acc_r6(sid, il_id, amount, det_days=0, has_det=False):
        d = r6.setdefault(sid, {"lines": [], "amount": 0.0, "det_days": 0, "has_det": False})
        d["lines"].append(il_id)
        d["amount"] += amount
        if has_det:
            d["has_det"] = True
            d["det_days"] = max(d["det_days"], det_days)

    def new_line(inv_lines, code, container_no, qty, unit, amount=None):
        il_seq[0] += 1
        il_id = f"IL-{il_seq[0]:06d}"
        amt = round(qty * unit, 2) if amount is None else round(amount, 2)
        row = {"invoice_line_id": il_id, "charge_code": code,
               "container_no": container_no or "", "qty": qty,
               "unit_price_usd": round(unit, 2), "amount_usd": amt}
        inv_lines.append(row)
        return il_id, amt

    VENDOR_NAME = {"carrier": "carrier", "forwarder": "forwarder",
                   "warehouse": "warehouse", "last_mile": "last_mile"}

    def emit_invoice(sid, vendor_type, issue_date, lines):
        """lines: [(charge_code, container_no, qty, unit, amount_override)]。返回 (inv_id, [(il,amt,code,cno)])。"""
        inv_seq[0] += 1
        inv_id = f"INV-2026-{inv_seq[0]:05d}"
        built = []
        total = 0.0
        for (code, cno, qty, unit, amt_override) in lines:
            il_id, amt = new_line(invoice_lines, code, cno, qty, unit, amt_override)
            invoice_lines[-1]["invoice_id"] = inv_id
            built.append((il_id, amt, code, cno))
            total += amt
        sp = ships[sid]
        scac = sp["carrier_scac"]
        vino = {
            "carrier": f"{scac}-INV-{rng.randint(10000, 99999)}",
            "forwarder": f"FWD-{rng.randint(100000, 999999)}",
            "warehouse": f"WH{rng.randint(10000, 99999)}",
            "last_mile": f"LM-{rng.randint(100000, 999999)}",
        }[vendor_type]
        vendor_disp = {
            "carrier": {"COSU": "COSCO SHIPPING Lines", "OOLU": "OOCL",
                        "MATS": "Matson Navigation", "ZIMU": "ZIM", "EGLV": "Evergreen Line"}.get(scac, scac),
            "forwarder": "Transpacific Forwarding Co.",
            "warehouse": f"{sp['destination_warehouse']} Warehouse Ops",
            "last_mile": "LastMile Delivery Partners",
        }[vendor_type]
        invoices.append({
            "invoice_id": inv_id, "vendor_type": vendor_type, "vendor_name": vendor_disp,
            "vendor_invoice_no": vino, "shipment_id": sid,
            "issue_date": issue_date.isoformat(), "currency": "USD",
            "total_usd": round(total, 2), "status": "received"})
        return inv_id, built

    # 随机异常选取：先确定哪些"干净行"要超收/复制（排除全部设计/案例船）
    # 逐票处理，保证独立随机流可复现
    for sid in ship_ids:
        sp = ships[sid]
        if sp["status"] == "planned":
            continue  # 发票只开给非 planned 船
        cnos = ship_containers[sid]
        dport = sp["destination_port_locode"]
        wh = sp["destination_warehouse"]
        etd = sp["etd"]
        ata = sp["ata"]
        delay = (sp["eta_current"] - sp["eta_initial"]).days
        case = ship_case.get(sid)
        clean_case = design_or_case  # 集合：这些船排除随机噪声
        is_noise_excluded = sid in clean_case

        # 该票是否被随机选中做超收/重复（仅非设计/案例船）
        do_over = (not is_noise_excluded) and rng.random() < ccfg["overbilling_rate"]
        do_dup = (not is_noise_excluded) and rng.random() < ccfg["duplicate_rate"]
        over_factor = rng.uniform(1.06, 1.35) if do_over else 1.0
        # ~5 票加 CHS、~4 票加 ACC（用确定性稀疏概率，排除案例船）
        add_chs = (not is_noise_excluded) and rng.random() < (5.0 / max(1, len(ship_ids)))
        add_acc = (not is_noise_excluded) and rng.random() < (4.0 / max(1, len(ship_ids)))

        # === carrier 发票：OFT+FSC 每柜 + DOC（issue=etd+2，非 planned 才开）===
        carrier_lines = []
        # 记录 carrier 里 primary 柜的 THC？——THC 属 forwarder；CD-B 例外（carrier 也计一次）
        for cno in cnos:
            oft_base = oft_for(sp, ctype_of[cno])
            fsc_base = oft_base * FSC_RATE
            # CD-A：OFT 按基准×1.30（仅 primary 柜或全柜？——超收整票 OFT）
            oft_amt = oft_base
            if case == "CD-A":
                oft_amt = oft_base * 1.30
            elif do_over:
                oft_amt = oft_base * over_factor
            carrier_lines.append(("OFT", cno, 1, oft_amt, oft_amt))
            carrier_lines.append(("FSC", cno, 1, fsc_base, fsc_base))
        carrier_lines.append(("DOC", "", 1, DOC_USD, DOC_USD))
        # CD-B：primary 柜 THC 在 carrier 也计一次（跨票重复的第一处）
        cdb_thc_primary = None
        if case == "CD-B":
            pcno = cnos[0]
            cdb_thc_primary = ("THC", pcno, 1, THC_BY_PORT[dport], THC_BY_PORT[dport])
            carrier_lines.append(cdb_thc_primary)
        issue_c = etd + timedelta(days=2)
        inv_c, built_c = emit_invoice(sid, "carrier", issue_c, carrier_lines)

        # 归因：CD-A / 随机超收 → R4
        for (il_id, amt, code, cno) in built_c:
            if code == "OFT":
                oft_base = oft_for(sp, ctype_of[cno])
                ratio = (amt - oft_base) / oft_base if oft_base else 0.0
                if case == "CD-A":
                    acc_r4(sid, il_id, amt - oft_base, ratio)
                    case_of_anom[(sid, "R4")] = "CD-A"
                elif do_over and amt > oft_base * (1 + tol) + 1e-9:
                    acc_r4(sid, il_id, amt - oft_base, ratio)

        # === forwarder 发票：THC 每柜 + CUS + DTY（issue=ata+3，已到港才开）===
        if ata is not None and sp["status"] in ("arrived", "customs", "delivered"):
            fwd_lines = []
            thc_line_ids_by_cno = {}
            for cno in cnos:
                fwd_lines.append(("THC", cno, 1, THC_BY_PORT[dport], THC_BY_PORT[dport]))
            fwd_lines.append(("CUS", "", 1, CUS_USD, CUS_USD))
            fwd_lines.append(("DTY", "", 1, DTY_USD, DTY_USD))
            # CD-C / CD-D：注入 DET（min(delay,6) 天 × 150/天，挂 primary 柜，进 forwarder 发票）
            det_days = 0
            if case in ("CD-C", "CD-D"):
                det_days = min(delay, 6)
                det_amt = det_days * det_daily
                fwd_lines.append(("DET", cnos[0], det_days, det_daily, det_amt))
            issue_f = ata + timedelta(days=3)
            inv_f, built_f = emit_invoice(sid, "forwarder", issue_f, fwd_lines)
            for (il_id, amt, code, cno) in built_f:
                if code == "THC":
                    thc_line_ids_by_cno[cno] = il_id
                if code == "DET":
                    acc_r6(sid, il_id, amt, det_days=det_days, has_det=True)
                    case_of_anom[(sid, "R6")] = case
            # CD-B：primary 柜 THC 在 forwarder 也存在 → 跨票重复，重复金额=第二笔（forwarder 的）
            if case == "CD-B" and cdb_thc_primary is not None:
                pcno = cnos[0]
                dup_il = thc_line_ids_by_cno.get(pcno)
                if dup_il:
                    acc_r5(sid, dup_il, THC_BY_PORT[dport])
                    case_of_anom[(sid, "R5")] = "CD-B"

        # === warehouse 发票：WHS + STO（issue=ata+7，已妥投才开）===
        if sp["status"] == "delivered" and ata is not None:
            wh_lines = [("WHS", "", 1, WHS_USD, WHS_USD), ("STO", "", 1, STO_USD, STO_USD)]
            # CD-F：WHS 重复两行（同票重复）
            if case == "CD-F":
                wh_lines.append(("WHS", "", 1, WHS_USD, WHS_USD))
            issue_w = ata + timedelta(days=7)
            inv_w, built_w = emit_invoice(sid, "warehouse", issue_w, wh_lines)
            if case == "CD-F":
                whs_ils = [b[0] for b in built_w if b[2] == "WHS"]
                # 首行不计，第二行为重复
                for il_id in whs_ils[1:]:
                    acc_r5(sid, il_id, WHS_USD)
                    case_of_anom[(sid, "R5")] = "CD-F"

            # === last_mile 发票：LMD（issue=ata+9，已妥投才开）===
            lm_lines = [("LMD", "", 1, LMD_BY_WAREHOUSE[wh], LMD_BY_WAREHOUSE[wh])]
            issue_l = ata + timedelta(days=9)
            inv_l, built_l = emit_invoice(sid, "last_mile", issue_l, lm_lines)

        # === 随机异常：复制某干净行（→R5）、CHS/ACC（→R6）、随机 DET（→R6）===
        # 随机复制：从该票已生成的行里挑一行复制到同一发票（同费种同柜 → R5）
        if do_dup:
            # 选 carrier 发票里的 FSC 或 OFT（有柜、有基准）复制
            dup_src = [b for b in built_c if b[2] in ("OFT", "FSC", "THC", "DOC")]
            if dup_src:
                (_, amt0, code0, cno0) = rng.choice(dup_src)
                il_dup, amt_dup = new_line(invoice_lines, code0, cno0, 1,
                                           amt0, amt0)
                invoice_lines[-1]["invoice_id"] = inv_c
                # 更新 carrier 发票 total
                for inv in invoices:
                    if inv["invoice_id"] == inv_c:
                        inv["total_usd"] = round(inv["total_usd"] + amt_dup, 2)
                        break
                acc_r5(sid, il_dup, amt_dup)

        # 随机 DET：延误已妥投的非设计船 70% 概率注入（→R6）
        if (not is_noise_excluded) and sp["status"] == "delivered" and ata is not None \
                and delay > 0 and rng.random() < 0.70:
            det_days = min(delay, 6)
            det_amt = det_days * det_daily
            # 挂到 forwarder 发票（该船已到港，forwarder 必已开）——找该船 forwarder 发票
            fwd_id = next((inv["invoice_id"] for inv in invoices
                           if inv["shipment_id"] == sid and inv["vendor_type"] == "forwarder"), None)
            if fwd_id:
                il_det, amt_det = new_line(invoice_lines, "DET", cnos[0], det_days,
                                           det_daily, det_amt)
                invoice_lines[-1]["invoice_id"] = fwd_id
                for inv in invoices:
                    if inv["invoice_id"] == fwd_id:
                        inv["total_usd"] = round(inv["total_usd"] + amt_det, 2)
                        break
                acc_r6(sid, il_det, amt_det, det_days=det_days, has_det=True)

        # 随机 CHS / ACC（→R6）——挂 carrier 发票（一定存在）
        if add_chs:
            il_chs, amt_chs = new_line(invoice_lines, "CHS", cnos[0], 1, CHS_USD, CHS_USD)
            invoice_lines[-1]["invoice_id"] = inv_c
            for inv in invoices:
                if inv["invoice_id"] == inv_c:
                    inv["total_usd"] = round(inv["total_usd"] + amt_chs, 2)
                    break
            acc_r6(sid, il_chs, amt_chs)
        if add_acc:
            il_acc, amt_acc = new_line(invoice_lines, "ACC", "", 1, ACC_USD, ACC_USD)
            invoice_lines[-1]["invoice_id"] = inv_c
            for inv in invoices:
                if inv["invoice_id"] == inv_c:
                    inv["total_usd"] = round(inv["total_usd"] + amt_acc, 2)
                    break
            acc_r6(sid, il_acc, amt_acc)

    # ------------------------------------------------------------------
    # 6. ground truth（按 (shipment_id, rule) 每键一行）
    # ------------------------------------------------------------------
    anomalies = []

    def sev_r4(over_ratio):
        return "high" if over_ratio > 0.20 else "medium"

    def sev_r6(amount):
        return "high" if amount > unplanned_high else "medium"

    # R4 rate_overbilling —— severity 取最重（聚合内单行超收比 >20% 即 high）
    for sid, d in sorted(r4.items()):
        over_sum = round(d["over"], 2)
        max_ratio = d["max_ratio"]            # 聚合内最大单行超收比（精确）
        sev = sev_r4(max_ratio)
        cid = case_of_anom.get((sid, "R4"), "")
        anomalies.append({
            "rule_id": "R4", "type": "rate_overbilling", "shipment_id": sid, "severity": sev,
            "anomaly_value_usd": over_sum,
            "affected_invoice_line_ids": json.dumps(sorted(d["lines"])),
            "case_id": cid,
            "attribution": f"OFT 超基准 {round(max_ratio * 100, 1)}%（超收 ${over_sum}）"})

    # R5 duplicate_charge —— severity high
    for sid, d in sorted(r5.items()):
        cid = case_of_anom.get((sid, "R5"), "")
        anomalies.append({
            "rule_id": "R5", "type": "duplicate_charge", "shipment_id": sid, "severity": "high",
            "anomaly_value_usd": round(d["amount"], 2),
            "affected_invoice_line_ids": json.dumps(sorted(d["lines"])),
            "case_id": cid,
            "attribution": f"同费种重复计费 {len(d['lines'])} 行（重复金额 ${round(d['amount'], 2)}）"})

    # R6 unplanned_charge —— severity 金额>500 high 否则 medium
    for sid, d in sorted(r6.items()):
        amt = round(d["amount"], 2)
        cid = case_of_anom.get((sid, "R6"), "")
        sp = ships[sid]
        delay = (sp["eta_current"] - sp["eta_initial"]).days
        if d["has_det"] and delay > 0:
            attribution = (f"计划外费用 ${amt}；含滞箱费，源于 ETA 延误 {delay} 天"
                           f"（DET {d['det_days']} 天×${det_daily}/天）")
        else:
            attribution = f"计划外费用 ${amt}（无费率卡基准）"
        anomalies.append({
            "rule_id": "R6", "type": "unplanned_charge", "shipment_id": sid,
            "severity": sev_r6(amt), "anomaly_value_usd": amt,
            "affected_invoice_line_ids": json.dumps(sorted(d["lines"])),
            "case_id": cid, "attribution": attribution})

    world["cost"] = {
        "containers": containers,
        "rate_card": rate_card,
        "expected_costs": expected_costs,
        "invoices": invoices,
        "invoice_lines": invoice_lines,
        "anomalies": sorted(anomalies, key=lambda a: (a["shipment_id"], a["rule_id"])),
        "design_cases": cd,
    }
    return world["cost"]
