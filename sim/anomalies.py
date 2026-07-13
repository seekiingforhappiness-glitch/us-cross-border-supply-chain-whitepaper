"""S2 异常谱系 + 连锁引擎：挂进 generators 的 5 处 ANOMALY-HOOK，由货代/供应商性格
参数 + 全局密度档驱动，异常与连锁链全部落 sim_event_log（caused_by 可追溯）。

为什么这样建（≤5 行）：① 一切随机只取 streams["anomalies"]（独立子流）——正常世界的
orders/booking/transit/invoice/inventory 抽签序列不受扰动，anomalies.enabled=false 即逐字节退回 S1。
② 异常"改世界事实"（顺延计划/加费种行/压库存），检测由 detectors 从事实重算（绝不读注入真值）。
③ 连锁引擎把"延误→滞箱计时→库存倒计时→大促断货"串成 caused_by 链，每环一条 sim_event。
④ 尾部脚本事件（货代资金链/海外仓事故）只留手动触发接口，回填不自动发生。
"""
from datetime import timedelta

from . import generators as G

# 计划外费种（无费率卡基准 → detectors R6）：对齐 engine UNPLANNED_CODES{DET,DEM,CHS,ACC}
# + 本 sim 扩展 PSS（旺季附加费）/INSP（查验费）。基准缺失即"计划外"，与引擎口径一致。
SIM_UNPLANNED_CODES = ("DET", "DEM", "CHS", "ACC", "PSS", "INSP")


def _a(world):
    return world["_cfg"]["anomalies"]


def enabled(world):
    return bool(_a(world).get("enabled"))


def _density_scale(world):
    """密度档缩放：real=1.0（校准基线，月 15-25），mild≈0.5（月 8-12），stress≈2.5（月 40+）。
    按所选档中值 / real 中值缩放全族注入概率——config 可切温和/压力（裁决点 Q2）。"""
    a = _a(world)
    prof = a["profiles"][a["density_profile"]]
    real = a["profiles"]["real"]
    return ((prof["monthly_min"] + prof["monthly_max"]) / 2) / \
           ((real["monthly_min"] + real["monthly_max"]) / 2)


def _weighted(rng, weights):
    """确定性加权选择：weights 为 {key: w}，按 key 排序后轮盘。"""
    items = sorted(weights.items())
    total = sum(w for _, w in items)
    x = rng.random() * total
    acc = 0.0
    for k, w in items:
        acc += w
        if x <= acc:
            return k
    return items[-1][0]


def _sev_by_days(days):
    return "critical" if days >= 13 else ("high" if days >= 8 else "medium")


def _in_window(day, win):
    return G.WD.D(win["from"]) <= day <= G.WD.D(win["to"])


def _month_key(day):
    return f"{day.year:04d}-{day.month:02d}"


# =========================== Hook 1：供应商交期延误（PO 齐货挂点）===========================
def supplier_delay(world, po_id, supplier, base_ready, day, streams):
    """惯性延期（chronic_delay）供应商把齐货日往后推，向下游自然传导（订舱→ETA→可能击穿承诺）。
    仅 chronic 供应商触发（其 PO 常年偏晚，2/25 家）。返回（可能被顺延的）齐货日。
    记为 texture:（背景纹理，不计入运营异常密度——密度以船/账单/库存级 5 族为准，避免 PO 海量刷高）。"""
    if not enabled(world) or not supplier["chronic_delay"]:
        return base_ready
    rng = streams["anomalies"]
    if rng.random() >= 0.45:
        return base_ready
    extra = rng.randint(5, 15)
    new_ready = base_ready + timedelta(days=extra)
    G.log_event(world, day, "texture:supplier_delay", "PurchaseOrder", po_id,
                f"supplier={supplier['supplier_id']};惯性延期;extra={extra}d;"
                f"ready {base_ready}->{new_ready}", "anomalies",
                severity=_sev_by_days(extra), family="delay")
    return new_ready


# =========================== Hook 2：延误族 / 查验 hold / 单证缺失（里程碑计划挂点）=========
_DELAY_LABEL = {"congestion": "港口拥堵", "port_omission": "跳港", "roll": "甩柜",
                "transship_miss": "中转误船"}


def reshape_plan(world, ship, streams):
    """在正常计划已建好后注入：延误族（顺延 arrived 及其后）、查验 hold（插 customs_hold +
    顺延放行/派送 + 记查验费）、单证缺失。设置连锁状态（滞箱计时/库存倒计时）与口径矛盾标记。"""
    if not enabled(world):
        return
    a = _a(world)
    rng = streams["anomalies"]
    scale = _density_scale(world)                          # 密度档缩放（温和/真实/压力）
    sid = ship["shipment_id"]
    bd = ship["_book_day"]                                  # 注入日 = 订舱当日（≤ as_of，防未来泄漏）

    # ---- 延误族 ----
    d = a["delay"]
    peak = _in_window(ship["etd"], world["_cfg"]["seasonal"]["congestion"]["peak_2025"])
    p_delay = min(0.95, d["per_shipment_rate"] * (d["peak_amplify"] if peak else 1.0) * scale)
    if rng.random() < p_delay:
        subtype = _weighted(rng, d["type_weights"])
        lo, hi = d["extra_days"][subtype]
        extra = rng.randint(lo, hi)
        _apply_delay(world, ship, extra)
        aid = G.log_event(world, bd, f"anomaly:delay:{subtype}", "Shipment", sid,
                          f"{_DELAY_LABEL[subtype]};extra={extra}d;peak={int(peak)};"
                          f"eta {ship['eta_initial']}->{ship['eta_current']}", "anomalies",
                          severity=_sev_by_days(extra), family="delay")
        ship["_delay_anomaly"] = aid
        ship["_delay_days"] = extra
        if subtype == "port_omission":                     # 跳港：卸在计划外邻港
            _reroute_port(world, ship, rng)
        _arm_inventory_countdown(world, ship, extra, aid)  # 连锁：库存倒计时（大促窗内升级为断货）

    # ---- 查验族（抽签 hold）----
    insp = a["inspection"]
    if rng.random() < min(0.95, insp["per_shipment_rate"] * scale):
        hold = rng.randint(*insp["hold_days"])
        fee = rng.randint(*insp["inspection_fee_usd"])
        _apply_customs_hold(world, ship, hold)
        ship["_inspection_fee"] = fee                       # 查验费（invoice 时落 INSP 计划外行 → R6）
        aid = G.log_event(world, bd, "anomaly:inspection:customs_hold",
                          "Shipment", sid, f"hold={hold}d;fee=${fee}", "anomalies",
                          severity="high" if hold >= 5 else "medium", family="inspection")
        # 口径矛盾：credibility 低的货代，其通知 ETA/到港口岸与"船司口径"不一致（成对里程碑）
        fwd = world["forwarders"][ship["forwarder_id"]]
        if fwd["credibility"] < 0.92 and rng.random() < insp["contradiction_rate"]:
            ship["_conflict"] = {"caused_by": aid}

    # ---- 单证族（缺件 / 资质将过期供应商发难）----
    doc = a["document"]
    if rng.random() < min(0.95, doc["per_shipment_rate"] * scale):
        n = 1 if rng.random() < 0.7 else 2
        miss = sorted(rng.sample(doc["missing_docs_pool"], n))
        ship["missing_docs"] = miss
        G.log_event(world, bd, "anomaly:document:missing_docs", "Shipment", sid,
                    f"missing={'|'.join(miss)}", "anomalies", severity="high", family="document")
    _qual_expiring_flare(world, ship, bd, rng)


def _apply_delay(world, ship, extra):
    """顺延 arrived 及其后所有里程碑 `extra` 天；同步 eta_current / _delivered_date。"""
    new_plan = []
    for ev_date, etype, ex in ship["plan"]:
        if etype in ("arrived", "customs_filed", "customs_released", "delivered", "transshipment"):
            ev_date = ev_date + timedelta(days=extra)
        new_plan.append((ev_date, etype, ex))
    ship["plan"] = sorted(new_plan, key=lambda x: x[0])
    ship["eta_current"] = ship["eta_initial"] + timedelta(days=extra)
    ship["_delivered_date"] = ship["_delivered_date"] + timedelta(days=extra)


def _reroute_port(world, ship, rng):
    """跳港：目的港改为同区域邻港（locode 同步；不改船司/航线）。"""
    alts = {"los_angeles": "long_beach", "long_beach": "los_angeles",
            "new_york": "savannah", "savannah": "new_york",
            "rotterdam": "hamburg", "hamburg": "rotterdam"}
    new_port = alts.get(ship["destination_port"])
    if new_port and new_port in G.WD.LOCODE:
        ship["destination_port"] = new_port
        ship["destination_port_locode"] = G.WD.LOCODE[new_port]


def _apply_customs_hold(world, ship, hold_days):
    """在 customs_filed 后插 customs_hold，并把放行/派送顺延 hold_days（查验滞留）。"""
    plan = []
    filed_date = None
    for ev_date, etype, ex in ship["plan"]:
        if etype == "customs_filed":
            filed_date = ev_date
        if etype in ("customs_released", "delivered"):
            ev_date = ev_date + timedelta(days=hold_days)
        plan.append((ev_date, etype, ex))
    if filed_date is not None:
        plan.append((filed_date, "customs_hold", ""))
    ship["plan"] = sorted(plan, key=lambda x: x[0])
    if ship["_delivered_date"] is not None:
        ship["_delivered_date"] = ship["_delivered_date"] + timedelta(days=hold_days)
    # 注意：查验 hold 顺延放行/派送/开票，但不改 eta_current（到港已发生；否则与 R1 缓冲重复计）。
    # 查验的可检出信号是 customs_hold 里程碑 + INSP 计划外费（R6），不强行制造 R1。


def _qual_expiring_flare(world, ship, bd, rng):
    """资质将过期供应商（S1 qual_expiring flag）在其承运货上发难：记一条单证族异常。"""
    a = _a(world)
    rate = a["document"]["qual_expiring_flare_rate"]
    for lid in ship["line_ids"]:
        sup = world["suppliers"][world["skus"][world["lines"][lid]["sku_id"]]["supplier_id"]]
        if sup.get("qual_expiring") and rng.random() < rate:
            G.log_event(world, bd, "anomaly:document:qual_expiring", "Supplier",
                        sup["supplier_id"], f"cert 将过期；影响 shipment={ship['shipment_id']}",
                        "anomalies", severity="high", family="document")
            return  # 一票记一次，避免刷量


def maybe_contradiction(world, ship, arrived_day, streams):
    """口径矛盾：到港时，货代口径（forwarder_portal）与船司口径（carrier_edi）对到港口岸/时间
    给出不一致的成对里程碑——两条同 event_type='arrived' 的记录，locode/source/time 冲突，
    共享 conflict_group 便于追溯。仅当 reshape_plan 标记了 _conflict 时触发。"""
    if not enabled(world) or not ship.get("_conflict"):
        return
    rng = streams["anomalies"]
    conf = ship.pop("_conflict")
    # 船司口径（真实到港，已由正常 arrived 落库）；此处补一条货代口径的"矛盾到港"
    alt_ports = [p for p in ("los_angeles", "long_beach", "new_york", "savannah")
                 if G.WD.LOCODE[p] != ship["destination_port_locode"]]
    alt_locode = G.WD.LOCODE[rng.choice(alt_ports)] if alt_ports else ship["destination_port_locode"]
    # 货代口径"抢跑"：早 1-2 天报了到港（且报错口岸）——落在真实到港之前，保持里程碑链时序不逆
    off = rng.choice([-2, -1])
    alt_day = max(ship["etd"] + timedelta(days=1), arrived_day + timedelta(days=off))
    group = f"CONFLICT-{ship['shipment_id']}"
    hour = 23
    world["milestones"].append({
        "milestone_id": G._nid(world, "ms", "MS-SIM", 6), "shipment_id": ship["shipment_id"],
        "event_type": "arrived", "event_classifier": "ACT",
        "event_time": G.iso_dt(alt_day, hour), "event_locode": alt_locode, "new_eta": "",
        "source_system": "forwarder_portal", "ingested_at": G.iso_dt(alt_day, hour),
        "is_duplicate": 0, "conflict_group": group,
    })
    G.log_event(world, arrived_day, "anomaly:inspection:eta_contradiction", "Shipment",
                ship["shipment_id"],
                f"货代口径 {alt_locode}@{alt_day} vs 船司口径 {ship['destination_port_locode']}"
                f"@{arrived_day}；group={group}", "anomalies",
                caused_by=conf.get("caused_by"), severity="medium", family="inspection")


# =========================== 连锁引擎：库存倒计时 → 大促断货升级 ===========================
def _arm_inventory_countdown(world, ship, extra, delay_aid):
    """延误 → 该船目的仓相关 position 进入"补货推迟"倒计时；若到货落在大促窗，
    倒计时升级为断货冲突（stockout）。记 chain:inventory_countdown（caused_by=延误）。"""
    wh = ship["destination_warehouse"]
    if wh not in world["warehouses"]:
        return  # 欧线直派无美仓
    promo = _month_key(ship["eta_current"]) in _a(world)["chain"]["stockout_promo_months"]
    if not promo:
        return  # 非大促窗：延误仅生滞箱，不升级断货（避免密度虚高）
    # 取本票最大数量行的 SKU 作为断货靶（一票一靶，避免刷量）
    top_lid = max(ship["line_ids"], key=lambda l: world["lines"][l]["qty"])
    kid = world["lines"][top_lid]["sku_id"]
    key = (kid, wh)
    if key not in world["inventory"]:
        return
    cid = G.log_event(world, ship["_book_day"], "chain:inventory_countdown", "InventoryPosition",
                      world["inventory"][key]["inventory_position_id"],
                      f"延误 {extra}d 推迟补货；SKU={kid}@{wh}；大促窗={_month_key(ship['eta_current'])}",
                      "anomalies", caused_by=delay_aid, severity="high", family="chain")
    # 断货窗 = [原定补货日 eta_initial, 延后到货 eta_current+缓冲]；窗内该 position 豁免例行补货 → 只由连锁断货
    world.setdefault("_chain_watch", []).append({
        "key": key, "caused_by": cid, "delay_root": delay_aid, "shipment_id": ship["shipment_id"],
        "from": ship["eta_initial"], "to": ship["eta_current"] + timedelta(days=10), "fired": False,
    })


def disrupted_keys(world, day):
    """当前处于连锁断货窗内的 position 键集合（供 generators 例行补货豁免）。"""
    return {w["key"] for w in world.get("_chain_watch", []) if w["from"] <= day <= w["to"]}


def inject_inventory(world, day, streams):
    """库存挂点：① 连锁断货——大促窗内，被延误标记的 position 加速消耗至安全库存下（断货冲突升级）；
    ② 盘点差异（shrinkage，仓储族纹理）。均落 sim_event_log。"""
    if not enabled(world):
        return
    rng = streams["anomalies"]
    # ① 连锁断货：窗内择一 tick 把靶 position 压到安全库存×frac（明确断货，R16 high；一票一次）
    for w in world.get("_chain_watch", []):
        if w["fired"] or not (w["from"] <= day <= w["to"]):
            continue
        pos = world["inventory"].get(w["key"])
        if pos is None:
            continue
        safety = pos["safety_stock"]
        frac = rng.uniform(*_a(world)["chain"]["stockout_target_frac"])
        target = int(safety * frac)
        pos["available_qty"] = min(pos["available_qty"], target)  # 压到 ≤ target（已更低则维持）
        w["fired"] = True
        G.log_event(world, day, "chain:stockout", "InventoryPosition",
                    pos["inventory_position_id"],
                    f"大促窗断货：可用压至 {pos['available_qty']} ≤ 安全 {safety}；"
                    f"源延误 {w['delay_root']} via shipment {w['shipment_id']}",
                    "anomalies", caused_by=w["caused_by"], severity="critical", family="chain")
    # ② 盘点差异（仓储族）：每 tick 抽查少量 position
    wc = _a(world)["warehouse"]
    shrink_rate = min(0.95, wc["shrink_rate"] * _density_scale(world))
    keys = list(world["inventory"])
    for _ in range(wc["shrink_per_tick_positions"]):
        if not keys or rng.random() >= shrink_rate:
            continue
        key = keys[rng.randint(0, len(keys) - 1)]
        pos = world["inventory"][key]
        system_qty = pos["available_qty"]
        if system_qty < 30:
            continue
        ratio = rng.uniform(*wc["shrink_ratio"])
        var = -int(round(system_qty * ratio))              # 差异多为负（丢货/损耗）
        world.setdefault("_cycle_counts", []).append({
            "cycle_count_id": G._nid(world, "cc", "CC-SIM", 6),
            "inventory_position_id": pos["inventory_position_id"],
            "warehouse_id": pos["warehouse_id"], "sku_id": key[0],
            "system_qty": system_qty, "counted_qty": system_qty + var,
            "as_of_date": day.isoformat(),
        })
        G.log_event(world, day, "anomaly:warehouse:shrinkage", "InventoryPosition",
                    pos["inventory_position_id"],
                    f"盘点差异：账面 {system_qty} 实盘 {system_qty + var}（差 {var}，"
                    f"{round(ratio * 100, 1)}%）", "anomalies",
                    severity="high" if ratio > wc["shrink_ratio"][1] * 0.7 else "medium",
                    family="warehouse")


# =========================== Hook 4/5：费用族 + 滞箱连锁（账单挂点）===========================
def inject_billing(world, ship, lines, add_line, primary, day, streams, base_mult):
    """账单挂点：① 费用族（泡重/费率超收/重复计费/旺季计划外附加费，按货代性格概率）；
    ② 滞箱连锁——延误致箱使费超免用箱期，落 DET 计划外行（chain:detention，caused_by=延误）。"""
    if not enabled(world):
        return
    a = _a(world)
    fee = a["fee"]
    rng = streams["anomalies"]
    fwd = world["forwarders"][ship["forwarder_id"]]
    p_fee = min(0.9, fwd["billing_error_rate"] * fee["billing_error_multiplier"] * _density_scale(world))

    if rng.random() < p_fee:
        # 子型权重：泡重随 volumetric_tendency 抬升；重复计费 duplicate_share；其余超收/旺季附加
        peak = _in_window(day, {"from": "2025-08-01", "to": "2025-10-31"})
        w = {"overbill": 0.9, "volumetric": 0.5 + fwd["volumetric_tendency"],
             "duplicate": fee["duplicate_share"], "peak_surcharge": 0.6 if peak else 0.15}
        sub = _weighted(rng, w)
        if sub == "overbill":                              # 费率超收 → R4（改现有 OFT 行金额）
            tgt = next((l for l in lines if l["charge_code"] == "OFT"), None)
            if tgt:
                pct = rng.uniform(*fee["overbill_pct"])
                new_amt = round(tgt["amount_usd"] * (1 + pct), 2)
                _bump_line(tgt, new_amt)
                G.log_event(world, day, "anomaly:fee:overbill", "Invoice", tgt["invoice_id"],
                            f"费率超收 {round(pct * 100, 1)}%；OFT {tgt['amount_usd']}", "anomalies",
                            severity="high" if pct > 0.20 else "medium", family="fee")
        elif sub == "volumetric":                          # 泡重虚加 → R4（改 THC 行金额）
            tgt = next((l for l in lines if l["charge_code"] == "THC"), None)
            if tgt:
                pct = rng.uniform(*fee["volumetric_extra_pct"])
                _bump_line(tgt, round(tgt["amount_usd"] * (1 + pct), 2))
                G.log_event(world, day, "anomaly:fee:volumetric", "Invoice", tgt["invoice_id"],
                            f"泡重虚加 {round(pct * 100, 1)}%（THC）", "anomalies",
                            severity="medium", family="fee")
        elif sub == "duplicate":                           # 重复计费 → R5（复制一条同费种同柜行）
            src = next((l for l in lines if l["charge_code"] in ("THC", "FSC", "DOC")), None)
            if src:
                add_line(src["charge_code"], src["container_no"], src["qty"],
                         src["amount_usd"] / max(src["qty"], 1))
                G.log_event(world, day, "anomaly:fee:duplicate", "Invoice", src["invoice_id"],
                            f"重复计费：{src['charge_code']}@{src['container_no']} 二次开列",
                            "anomalies", severity="high", family="fee")
        else:                                              # 旺季计划外附加费 → R6（PSS 计划外行）
            amt = rng.randint(*fee["peak_surcharge_usd"])
            add_line("PSS", primary, 1, amt)
            G.log_event(world, day, "anomaly:fee:peak_surcharge", "Invoice",
                        lines[0]["invoice_id"] if lines else ship["shipment_id"],
                        f"旺季计划外附加费 PSS ${amt}", "anomalies",
                        severity="high" if amt > a["detectors"]["unplanned_high_usd"] else "medium",
                        family="fee")

    # 查验费（reshape_plan 记的 INSP）落计划外行 → R6
    if ship.get("_inspection_fee"):
        add_line("INSP", primary, 1, ship["_inspection_fee"])

    # 滞箱连锁：延误致箱使费超免用箱期 → DET 计划外行（chain:detention，caused_by=延误）
    if ship.get("_delay_days"):
        free = a["chain"]["detention_free_days"]
        over = ship["_delay_days"] - free
        if over > 0:
            daily = a["chain"]["detention_daily_usd"]
            n_cont = len(ship["containers"])
            det = daily * over * n_cont
            add_line("DET", primary, over * n_cont, daily)
            G.log_event(world, day, "chain:detention", "Invoice",
                        lines[0]["invoice_id"] if lines else ship["shipment_id"],
                        f"滞箱费 ${det}（超免用箱期 {over}d × {n_cont} 柜 × ${daily}/d）",
                        "anomalies", caused_by=ship.get("_delay_anomaly"),
                        severity="high" if det > a["detectors"]["unplanned_high_usd"] else "medium",
                        family="chain")


def _bump_line(line, new_amount):
    """把某行金额抬到 new_amount（同步 unit_price，保持 amount=unit×qty 不变式）。"""
    line["amount_usd"] = new_amount
    line["unit_price_usd"] = round(new_amount / max(line["qty"], 1), 2)


# =========================== 尾部脚本事件（只留手动触发接口，回填不自动调用）================
def trigger_tail_event(world, name, day, streams):
    """演示用手动触发：货代资金链传闻 / 海外仓事故。回填全程不调用（auto=false）。"""
    rng = streams["anomalies"]
    cfg = _a(world)["tail_events"]
    if name == "forwarder_liquidity_rumor":
        fid = cfg["forwarder_liquidity_rumor"]["forwarder_id"]
        return G.log_event(world, day, "tail:forwarder_liquidity_rumor", "Forwarder", fid,
                           f"资金链传闻（手动注入）；rng={rng.random():.3f}", "anomalies",
                           severity="critical", family="tail")
    if name == "overseas_wh_incident":
        wid = cfg["overseas_wh_incident"]["warehouse_id"]
        return G.log_event(world, day, "tail:overseas_wh_incident", "Warehouse", wid,
                           "海外仓事故（手动注入）", "anomalies", severity="critical", family="tail")
    raise ValueError(f"未知尾部事件 {name!r}")
