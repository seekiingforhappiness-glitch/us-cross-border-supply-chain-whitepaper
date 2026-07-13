"""业务事件生成器 + run_tick（单一逻辑，两种驱动）。

为什么这样建（≤5 行）：① run_tick 是唯一的一天推进逻辑——backfill 循环调它、未来 live 每天调它一次
（一套逻辑两种驱动，防口径漂移）。② 订舱把 ready 行挂到"已存在的真实班次"上，装船里程碑按班次计划
逐日 emit（时钟到点才发，天然 ≤ as_of、时序单调）。③ 本单只产"正常世界"：每个生成器都标了 ANOMALY-HOOK
挂点（延误/查验/账单错/泡重/断货等），触发实现留 S2。④ 一切随机来自 streams[名]，确定性可复现。
"""
from datetime import date, timedelta

from .clock import iso_dt
from . import world_def as WD


# ---------- 季节曲线辅助 ----------
def _month_key(day):
    return f"{day.year:04d}-{day.month:02d}"


def demand_mult(cfg, day):
    return cfg["seasonal"]["demand_multiplier"].get(_month_key(day), 1.0)


def in_window(day, win):
    return WD.D(win["from"]) <= day <= WD.D(win["to"])


def cny_shutdown(cfg, day):
    return in_window(day, cfg["seasonal"]["cny_2026"]["shutdown"])


def push_past_cny(cfg, ready_day):
    """春节停摆：落在停摆窗口的齐货日推到停摆结束（工厂/口岸关门，货出不来）。"""
    sd = cfg["seasonal"]["cny_2026"]["shutdown"]
    if in_window(ready_day, sd):
        return WD.D(sd["to"]) + timedelta(days=1)
    return ready_day


def route_transit_mid(cfg, route_id):
    lo, hi = cfg["routes"][route_id]["transit_days"]
    return (lo + hi) // 2


# ---------- 动态状态初始化 ----------
def init_dynamic(world):
    world.update(
        sos={}, lines={}, pos={}, shipments={}, milestones=[], allocations=[],
        containers=[], invoices=[], invoice_lines=[], event_log=[],
        ready_pool={r: [] for r in world["routes"]},   # 每航线待订舱的 ready 行
        _book_seen={},                                  # 已发 booking_confirmed 的船
        counters={k: 0 for k in ("so", "line", "po", "ship", "ms", "alloc",
                                 "cont", "inv", "evt")},
    )
    # SKU 按类目索引（订单选品用）
    world["_skus_by_cat"] = {}
    for kid in sorted(world["skus"]):
        world["_skus_by_cat"].setdefault(world["skus"][kid]["category"], []).append(kid)
    # 客户下单起点（错峰）
    start = world["_start"]
    for cid in sorted(world["customers"]):
        c = world["customers"][cid]
        c["_next_order"] = start + timedelta(days=c["next_order_offset"])


def _nid(world, key, prefix, width=5):
    world["counters"][key] += 1
    return f"{prefix}-{world['counters'][key]:0{width}d}"


def _log(world, day, kind, otype, oid, params, stream):
    """sim_event_log：世界知道自己做了什么（类型/参数/种子路径）。"""
    world["event_log"].append({
        "sim_event_id": _nid(world, "evt", "SEV", 7), "sim_date": day.isoformat(),
        "event_kind": kind, "object_type": otype, "object_id": oid,
        "params_json": params, "rng_stream": stream,
    })


# ---------- 1. 新订单（客户节奏 × 季节曲线 × SKU 日销）----------
def _category_weights(world, day):
    """季节品在旺季/节前放量——影响 SKU 选择权重。"""
    peak = demand_mult(world["_cfg"], day) >= 1.25
    w = {}
    for cat, spec in world["_cfg"]["sku_categories"].items():
        base = spec["share"]
        w[cat] = base * (1.8 if (spec["seasonal"] and peak) else 1.0)
    return w


def _pick_route(world, cust, rng):
    if rng.random() < world["_cfg"]["booking"]["eu_share"]:
        return "EU"
    return "USEC" if cust["region"] == "US-East" else "USWC"


def emit_orders(world, day, streams):
    cfg = world["_cfg"]
    rng = streams["orders"]
    m = demand_mult(cfg, day)
    skus_by_cat = world["_skus_by_cat"]
    for cid in sorted(world["customers"]):
        c = world["customers"][cid]
        if day < c["_next_order"]:
            continue
        # 到期下单：行数与数量随季节乘子放大；季节品旺季更常被选中
        so_id = _nid(world, "so", "SO-SIM", 5)
        route = _pick_route(world, c, rng)
        transit_mid = route_transit_mid(cfg, route)
        lo, hi = c["lines_per_order_range"]
        n_lines = max(1, round(rng.randint(lo, hi) * (0.7 + 0.5 * m)))
        cw = _category_weights(world, day)
        cats = sorted(cw)
        weights = [cw[k] for k in cats]
        world["sos"][so_id] = {"so_id": so_id, "customer_id": cid, "order_date": day,
                               "route": route, "status": "open"}
        line_ids = []
        for _j in range(n_lines):
            cat = rng.choices(cats, weights=weights, k=1)[0]
            kid = rng.choice(skus_by_cat[cat])
            qmin, qmax = cfg["booking"]["qty_per_line"]
            qty = max(1, round(rng.randint(qmin, qmax) * (0.6 + 0.6 * m)))
            lid = _nid(world, "line", "SOL-SIM", 6)
            # 承诺交期 = 下单 + 全链提前期 + 松弛（正常世界可达 → S1 无 R1 击穿；延误留 S2）
            sup_lead = world["suppliers"][world["skus"][kid]["supplier_id"]]["lead_time_days"]
            lead = sup_lead + cfg["booking"]["booking_lead_days"] + transit_mid + 4 + 4
            promise = day + timedelta(days=lead + rng.randint(6, 18))
            world["lines"][lid] = {
                "so_line_id": lid, "so_id": so_id, "sku_id": kid, "qty": qty,
                "unit_price_usd": round(world["skus"][kid]["unit_price_usd"]
                                        * rng.uniform(0.9, 1.15), 2),
                "promised_delivery_date": promise, "route": route,
                "line_status": "open", "_po_id": None, "_ready": None, "_shipped": False,
            }
            line_ids.append(lid)
        _emit_pos_for_so(world, day, so_id, line_ids, streams)
        _log(world, day, "order_created", "SalesOrder", so_id,
             f"route={route};lines={n_lines};m={m:.2f}", "orders")
        # 下次下单：旺季更密（interval / m）
        interval = max(1, round(c["order_interval_days"] / max(0.5, m)))
        c["_next_order"] = day + timedelta(days=interval)


# ---------- 2. 采购（每 SO×供应商 一张 PO；齐货日 = 下单 + 供应商交期，避春节）----------
def _emit_pos_for_so(world, day, so_id, line_ids, streams):
    rng = streams["orders"]
    cfg = world["_cfg"]
    by_sup = {}
    for lid in line_ids:
        sup = world["skus"][world["lines"][lid]["sku_id"]]["supplier_id"]
        by_sup.setdefault(sup, []).append(lid)
    for sup in sorted(by_sup):
        po_id = _nid(world, "po", "PO-SIM", 5)
        s = world["suppliers"][sup]
        ready = push_past_cny(cfg, day + timedelta(days=s["lead_time_days"]))
        # ANOMALY-HOOK(S2)：chronic_delay 供应商在此把 ready 往后推（R7 供应商交期延误）。S1 不推。
        total_qty = sum(world["lines"][lid]["qty"] for lid in by_sup[sup])
        world["pos"][po_id] = {"po_id": po_id, "supplier_id": sup, "so_id": so_id,
                               "line_ids": by_sup[sup], "qty": total_qty, "po_date": day,
                               "expected_ready_date": ready, "status": "open"}
        for lid in by_sup[sup]:
            world["lines"][lid]["_po_id"] = po_id
            world["lines"][lid]["_ready"] = ready


# ---------- 3. 订舱（ready 行 → 挂船期表班次 → 生成柜 + 计划里程碑）----------
def emit_bookings(world, day, streams):
    cfg = world["_cfg"]
    # 3a. 新齐货的行进入对应航线 ready 池（确定性顺序）
    for lid in sorted(world["lines"]):
        ln = world["lines"][lid]
        if ln["_shipped"] or ln["_ready"] is None or ln["line_status"] != "open":
            continue
        if ln["_ready"] <= day and lid not in world["ready_pool"][ln["route"]]:
            world["ready_pool"][ln["route"]].append(lid)
    # 3b. 对每条今天需触发订舱的班次（etd == day + lead），把该航线 ready 池装船
    lead = cfg["booking"]["booking_lead_days"]
    cap = cfg["booking"]["container_capacity_units"]
    maxc = cfg["booking"]["max_containers_per_shipment"]
    trigger_etd = day + timedelta(days=lead)
    for sail in world["schedule"]:
        if sail["etd"] != trigger_etd or sail["booked"]:
            continue
        if cny_shutdown(cfg, sail["etd"]):
            continue  # 春节停摆：口岸关门，无离港（"春节周柜量显著低"）
        pool = world["ready_pool"][sail["route_id"]]
        if not pool:
            continue
        # 拼柜门槛：ready 量不足且最老行未超最大等待 → 跳过本班次，等下一班拼满（不发半空柜）。
        # 真实货主行为，也让薄货量航线（如欧线）自然拼成更满的少数船，而非每班一个单柜。
        pool_units = sum(world["lines"][lid]["qty"] for lid in pool)
        waited = (day - min(world["lines"][lid]["_ready"] for lid in pool)).days
        if pool_units < cfg["booking"]["min_fill_units"] and waited < cfg["booking"]["max_wait_days"]:
            continue
        # 取 ready 池前若干行，装满至多 maxc 个柜
        take, units = [], 0
        for lid in list(pool):
            q = world["lines"][lid]["qty"]
            if take and units + q > cap * maxc:
                break
            take.append(lid)
            units += q
        if not take:
            continue
        for lid in take:
            pool.remove(lid)
        sail["booked"] = 1
        _create_shipment(world, day, sail, take, units, streams)


def _create_shipment(world, book_day, sail, line_ids, units, streams):
    cfg = world["_cfg"]
    rng = streams["booking"]
    sid = _nid(world, "ship", "SHP-SIM", 5)
    scac = sail["scac"]
    n_cont = max(1, min(cfg["booking"]["max_containers_per_shipment"],
                        -(-units // cfg["booking"]["container_capacity_units"])))  # ceil
    # 旺季拥堵：运输时长上浮直接烘焙进 eta_initial（正常纹理，不是 eta_change → 时序仍单调）
    extra = 0
    cong = cfg["seasonal"]["congestion"]["peak_2025"]
    if in_window(sail["etd"], cong):
        extra = rng.randint(*cong["transit_extra_days"])
    eta = sail["eta"] + timedelta(days=extra)
    # 货代分配（本票 = 该航线固定轮转，简单确定）
    fids = sorted(world["forwarders"])
    fwd = world["forwarders"][fids[world["counters"]["ship"] % len(fids)]]
    # 目的仓：美西→自营/FBA，美东→3PL，欧线→EU 直派（无美仓）
    dest_wh = _dest_warehouse(world, sail["route_id"], rng)
    ctype = rng.choice(WD.CONTAINER_TYPES)
    primary_no = WD.make_container_no(sail["carrier"], rng)
    incoterm = rng.choice(WD.INCOTERMS)
    ship = {
        "shipment_id": sid, "sailing_id": sail["sailing_id"],
        "booking_no": f"{scac}{rng.randint(10**8, 10**9 - 1)}",
        "mbl_no": f"{scac}{rng.randint(10**8, 10**9 - 1)}",
        "mode": "ocean_fcl" if n_cont >= 1 else "ocean_lcl",
        "carrier_name": sail["carrier"], "carrier_scac": scac,
        "vessel_voyage": f"{sail['vessel']} {sail['voyage']}",
        "forwarder_id": fwd["id"], "route_id": sail["route_id"],
        "origin_port": sail["origin_port"], "destination_port": sail["dest_port"],
        "origin_port_locode": WD.LOCODE[sail["origin_port"]],
        "destination_port_locode": WD.LOCODE[sail["dest_port"]],
        "destination_warehouse": dest_wh, "incoterm": incoterm,
        "container_no": primary_no, "container_type": ctype,
        "etd": sail["etd"], "eta_initial": eta, "eta_current": eta, "ata": None,
        "customs_status": "not_filed", "status": "planned", "missing_docs": [],
        "expedite_flag": False, "po_ids": [], "line_ids": line_ids,
        "gross_weight_kg": rng.randint(9000, 26000) * n_cont,
        "volume_cbm": round(rng.uniform(45, 67) * n_cont, 1),
        "plan": [], "_delivered_date": None, "_invoiced": False,
    }
    # 柜（首柜 primary，其余按船司再生成合法 ISO6346 柜号）
    ship["containers"] = []
    for k in range(n_cont):
        cno = primary_no if k == 0 else WD.make_container_no(sail["carrier"], rng)
        ship["containers"].append(cno)
        world["containers"].append({
            "container_no": cno, "shipment_id": sid, "container_type": ctype,
            "is_primary": (k == 0), "free_days": rng.choice([5, 7, 10]),
            "gross_weight_kg": rng.randint(9000, 26000),
            "volume_cbm": round(rng.uniform(45, 67), 1),
        })
        world["counters"]["cont"] += 1
    # PO 关联（去重）
    ship["po_ids"] = sorted({world["lines"][lid]["_po_id"] for lid in line_ids
                             if world["lines"][lid]["_po_id"]})
    # 分配（每 shipment 必有 allocation——按行装船）
    for lid in line_ids:
        aid = _nid(world, "alloc", "ALC-SIM", 6)
        world["allocations"].append({"allocation_id": aid, "shipment_id": sid,
                                     "so_line_id": lid, "allocated_qty": world["lines"][lid]["qty"]})
        world["lines"][lid]["line_status"] = "allocated"
        world["lines"][lid]["_shipped"] = True
    # 计划里程碑（时钟到点逐日 emit；ANOMALY-HOOK(S2)：延误/甩柜/查验在此改计划）
    _plan_milestones(world, ship, streams)
    world["shipments"][sid] = ship
    _log(world, book_day, "shipment_booked", "Shipment", sid,
         f"sailing={sail['sailing_id']};carrier={sail['carrier']};fwd={fwd['id']};"
         f"cont={n_cont};lines={len(line_ids)};etd={sail['etd']};eta={eta}", "booking")


def _dest_warehouse(world, route_id, rng):
    if route_id == "USEC":
        return "USEC-3PL"
    if route_id == "EU":
        return "EU-DC"          # 欧线直派，无美仓（库存不 putaway）
    return rng.choice(["USWC-DC1", "USWC-DC1", "FBA-US"])  # 美西：自营为主 + 部分 FBA


def _plan_milestones(world, ship, streams):
    rng = streams["booking"]
    cfg = world["_cfg"]
    plan = [(ship["etd"], "departed", "")]
    if rng.random() < cfg["booking"]["transshipment_rate"]:
        mid = ship["etd"] + timedelta(days=max(1, (ship["eta_initial"] - ship["etd"]).days // 2))
        plan.append((mid, "transshipment", ""))
    arr = ship["eta_initial"]
    plan.append((arr, "arrived", ""))
    filed = arr + timedelta(days=rng.randint(0, 1))
    plan.append((filed, "customs_filed", ""))
    lo, hi = cfg["lead_times"]["customs_clear_days"]
    released = filed + timedelta(days=rng.randint(lo, hi))
    plan.append((released, "customs_released", ""))
    lo, hi = cfg["lead_times"]["lastmile_days"]
    delivered = released + timedelta(days=rng.randint(lo, hi))
    plan.append((delivered, "delivered", ""))
    ship["plan"] = sorted(plan, key=lambda x: x[0])
    ship["_delivered_date"] = delivered


# ---------- 4. 里程碑 emit（时钟到点逐日发；到港 putaway；妥投标记开票）----------
def emit_milestones(world, day, streams):
    rng = streams["transit"]
    for sid in sorted(world["shipments"]):
        ship = world["shipments"][sid]
        # 订舱当日发 booking_confirmed（我方动作，即时可知，delay=0）
        if ship["status"] == "planned" and world["_book_seen"].get(sid) is not True:
            _add_ms(world, ship, "booking_confirmed", day, rng)
            world["_book_seen"][sid] = True
        for ev_date, etype, _extra in ship["plan"]:
            if ev_date != day or ship.get("_report_stalled"):
                continue   # 已达上报前沿：更晚的里程碑不可能先于更早的被知悉，一并冻结
            if _add_ms(world, ship, etype, ev_date, rng):  # 仅 as_of 前"已上报"的才落库+推进
                _apply_status(world, ship, etype, ev_date)
                if etype == "arrived":
                    _putaway(world, ship, ev_date)
            else:
                ship["_report_stalled"] = True  # 该事件未上报 → 该船后续里程碑全部冻结（链保持合法前缀）


def _add_ms(world, ship, etype, day, rng):
    """落一条里程碑；货代通知延迟 → ingested_at 滞后。若 as_of 时尚未上报则不落库（无未来泄漏）。
    返回是否已落库（决定是否推进 shipment 状态——系统只认已上报的事实）。"""
    as_of = world["_as_of"]
    fwd = world["forwarders"][ship["forwarder_id"]]
    hour = rng.randint(1, 22)
    # ANOMALY-HOOK(S2)：查验抽签在 customs_filed 后按概率插 customs_hold + 时长/费用/口径矛盾事件。
    delay = 0 if etype == "booking_confirmed" else rng.randint(*fwd["notify_delay_days"])
    ingested_day = day + timedelta(days=delay)
    # 上报单调：晚发生的里程碑不会比早发生的更早被系统知悉（修同日到港+报关的独立延迟倒挂）
    prev_ing = ship.get("_last_ingested")
    if prev_ing is not None and ingested_day < prev_ing:
        ingested_day = prev_ing
    if ingested_day > as_of:
        return False   # 货代性格：通知延迟使该事件在 as_of 时仍未上报——系统尚不知情，不落库
    ship["_last_ingested"] = ingested_day
    # 同日多里程碑（如到港 + 当日报关）：小时数严格递增 → event_time 与里程碑序单调不逆
    if ship.get("_last_day") == day and hour <= ship.get("_last_hour", 0):
        hour = min(23, ship["_last_hour"] + 1)
    ship["_last_day"], ship["_last_hour"] = day, hour
    if etype in ("booking_confirmed", "departed"):
        locode = ship["origin_port_locode"]
    elif etype == "transshipment":
        locode = rng.choice(WD.TRANSSHIP_HUBS)
    else:
        locode = ship["destination_port_locode"]
    world["milestones"].append({
        "milestone_id": _nid(world, "ms", "MS-SIM", 6), "shipment_id": ship["shipment_id"],
        "event_type": etype, "event_classifier": "ACT",
        "event_time": iso_dt(day, hour), "event_locode": locode, "new_eta": "",
        "source_system": "forwarder_portal" if fwd["credibility"] < 0.92 else "carrier_edi",
        "ingested_at": iso_dt(ingested_day, min(23, hour + 1)), "is_duplicate": 0,
    })
    return True


def _apply_status(world, ship, etype, day):
    if etype == "departed":
        ship["status"] = "in_transit"
    elif etype == "arrived":
        ship["status"], ship["ata"] = "arrived", day
    elif etype == "customs_filed":
        ship["status"], ship["customs_status"] = "customs", "filed"
    elif etype == "customs_released":
        ship["customs_status"] = "released"
    elif etype == "delivered":
        ship["status"] = "delivered"
        for lid in ship["line_ids"]:
            world["lines"][lid]["line_status"] = "fulfilled"


def _putaway(world, ship, day):
    """到港上架：把本票各 SKU 数量补进目的仓库存（库存随补货流动）。"""
    wh = ship["destination_warehouse"]
    if (wh not in world["warehouses"]):
        return  # 欧线直派无美仓
    by_sku = {}
    for lid in ship["line_ids"]:
        kid = world["lines"][lid]["sku_id"]
        by_sku[kid] = by_sku.get(kid, 0) + world["lines"][lid]["qty"]
    for kid, q in sorted(by_sku.items()):
        pos = world["inventory"].get((kid, wh))
        if pos:
            pos["available_qty"] += q
    _log(world, day, "inventory_putaway", "Shipment", ship["shipment_id"],
         f"wh={wh};skus={len(by_sku)};units={sum(by_sku.values())}", "transit")


# ---------- 5. 库存日消耗（销售拉动；恒钳 ≥ 0）----------
def emit_inventory(world, day, streams):
    cfg = world["_cfg"]
    f = cfg["inventory"]["consume_factor"]
    region_share = cfg["inventory"]["region_share"]
    # ANOMALY-HOOK(S2)：断货(R16)/不可履约(R17)/盘点差异(R18) 在此按库存派生。S1 只做正常流动。
    for key in world["inventory"]:
        kid, wid = key
        pos = world["inventory"][key]
        region = world["warehouses"][wid]["region"]
        vel = world["skus"][kid]["daily_velocity"]
        consume = int(round(vel * region_share.get(region, 0.2) * f))
        pos["available_qty"] = max(0, pos["available_qty"] - consume)


# ---------- 6. 开票与对账周期（妥投后货代出账；每行挂柜）----------
def emit_invoicing(world, day, streams):
    cfg = world["_cfg"]
    rng = streams["invoice"]
    as_of = world["_as_of"]
    lo, hi = cfg["lead_times"]["invoice_after_delivery_days"]
    for sid in sorted(world["shipments"]):
        ship = world["shipments"][sid]
        if ship["_invoiced"] or ship["status"] != "delivered" or ship["_delivered_date"] is None:
            continue
        issue = ship["_delivered_date"] + timedelta(days=rng.randint(lo, hi))
        if issue != day or issue > as_of:
            continue
        _issue_invoice(world, ship, issue, rng)
        ship["_invoiced"] = True


def _issue_invoice(world, ship, issue_day, rng):
    cfg = world["_cfg"]
    fwd = world["forwarders"][ship["forwarder_id"]]
    rc = cfg["rate_card"]
    # 货代涨价事件（正常版本）：涉事货代自 effective 起报价上浮
    hike = 1.0
    ev = cfg["events"]["forwarder_rate_hike"]
    if ship["forwarder_id"] == ev["forwarder_id"] and issue_day >= WD.D(ev["effective"]):
        hike = 1.0 + ev["hike_pct"]
    mult = fwd["quote_level"] * hike
    route = ship["route_id"]
    inv_id = _nid(world, "inv", "INV-SIM", 5)
    primary = next(c for c in ship["containers"])
    lines = []

    def add_line(code, cno, qty, unit):
        amt = round(unit * qty, 2)
        lines.append({"invoice_line_id": f"{inv_id}-L{len(lines) + 1:02d}",
                      "invoice_id": inv_id, "charge_code": code, "container_no": cno,
                      "qty": qty, "unit_price_usd": round(unit, 2), "amount_usd": amt})
    # 柜级费种（OFT/THC/FSC）每柜一行；ANOMALY-HOOK(S2)：泡重(volumetric_tendency)/账单错(billing_error_rate)在此注入
    for cno in ship["containers"]:
        for code in ("OFT", "THC", "FSC"):
            spec = rc[code]
            rmult = spec.get("route_mult", {}).get(route, 1.0)
            add_line(code, cno, 1, spec["base"] * rmult * mult)
    # 票级费种（DOC/ISF/CUS）挂 primary 柜（保证每行有对应柜）
    for code in ("DOC", "ISF", "CUS"):
        add_line(code, primary, 1, rc[code]["base"] * mult)
    total = round(sum(l["amount_usd"] for l in lines), 2)
    world["invoices"].append({
        "invoice_id": inv_id, "vendor_type": "forwarder", "vendor_name": fwd["name"],
        "vendor_invoice_no": f"{ship['carrier_scac']}-{world['counters']['inv']:06d}",
        "shipment_id": ship["shipment_id"], "issue_date": issue_day.isoformat(),
        "currency": "USD", "total_usd": total, "status": "received",
    })
    world["invoice_lines"].extend(lines)
    _log(world, issue_day, "invoice_issued", "Invoice", inv_id,
         f"fwd={fwd['id']};hike={hike:.2f};lines={len(lines)};total={total}", "invoice")


# ---------- run_tick：单一逻辑，两种驱动（backfill 循环 / live 单调）----------
def run_tick(world, day, streams):
    """推进世界一天：产出当日全部业务事件。回填与 live 共用此函数。"""
    emit_orders(world, day, streams)
    emit_bookings(world, day, streams)
    emit_milestones(world, day, streams)
    emit_inventory(world, day, streams)
    emit_invoicing(world, day, streams)
