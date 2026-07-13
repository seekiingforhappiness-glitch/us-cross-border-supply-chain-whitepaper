"""S2 检测子集：在 simworld 上跑规则检测（as-of=当日的活世界快照）。

取法说明（报告同款）：**等价实现**而非直接 import engine 规则——engine 检测器读的是
data/ontology.sqlite 里 simworld 没有的本体专属表（expected_costs/po_lines/cycle_counts/
inventory_reservations），且 import 链 pipeline→agent→engine 会把物理隔离的 sim 运行期耦合进
本体栈。故本模块读 simworld 的内存世界（= store.py 逐列序列化的对象层），**逐条镜像 engine 规则
的判据/阈值/root_cause 措辞**并标注源规则：R1↔engine/rules.py、R2/R3↔engine/rules.py、
R4/R5/R6↔engine/cost_rules.py、R16↔engine/warehouse_rules.py。阈值单一来源在 config anomalies.detectors
（复述 config/datagen.yaml 引擎口径）。绝不读注入真值——一切从"世界事实"（计划/账单/库存）重算。

覆盖子集 = 注入异常的经济主脊：延误(R1)/单证(R2)/费用(R4/R5/R6)/断货(R16)。
R3（静默停滞）不入子集：sim 里程碑节奏稀疏（仅 departed/arrived，中段远洋天然静默 >5 天），
5 天判据会在正常远洋段刷屏——R3 是承运人 feed 高密度世界的规则，不适配 sim 密度（报告已述）。
盘点差异/供应商交期/资质过期作为世界纹理注入（sim_event_log、密度、叙事），其处置不进 AI 回路。
"""
from datetime import date, timedelta

SEV_ORDER = {"medium": 0, "high": 1, "critical": 2}
BUMP = {"medium": "high", "high": "critical", "critical": "critical"}
SIM_UNPLANNED_CODES = ("DET", "DEM", "CHS", "ACC", "PSS", "INSP")  # 无费率卡基准 → R6


def _det(world):
    return world["_cfg"]["anomalies"]["detectors"]


def _line_sev(breach_days, tier):
    """镜像 engine/rules._line_sev：breach≤3→medium 否则 high；大 B（tier A）再升一级。"""
    base = "medium" if breach_days <= 3 else "high"
    return BUMP[base] if tier == "A" else base


def _worse(a, b):
    return a if SEV_ORDER[a] >= SEV_ORDER[b] else b


def _expected_baseline(world, ship, code, issue_date):
    """镜像 generators._issue_invoice 的基准：rc.base × route_mult × (quote_level × hike)。
    与开票口径逐字一致 → 正常行 amount==baseline（不触发 R4），仅被泡重/超收抬过的行 > baseline×(1+tol)。"""
    cfg = world["_cfg"]
    rc = cfg["rate_card"].get(code)
    if rc is None:
        return None                                   # 计划外费种（DET/PSS/INSP...）无基准 → R6
    fwd = world["forwarders"][ship["forwarder_id"]]
    ev = cfg["events"]["forwarder_rate_hike"]
    hike = 1.0 + ev["hike_pct"] if (ship["forwarder_id"] == ev["forwarder_id"]
                                    and issue_date >= date.fromisoformat(ev["effective"])) else 1.0
    mult = fwd["quote_level"] * hike
    rmult = rc.get("route_mult", {}).get(ship["route_id"], 1.0)
    return round(rc["base"] * rmult * mult, 2)


def detect(world, day):
    """跑全检测子集，返回候选列表（每条含锚点/定级/金额/root_cause，不写库）。as-of=day。"""
    det = _det(world)
    buf = det["buffers"]["customs_days"] + det["buffers"]["lastmile_days"]
    cands = []

    # ===== R1 延误传导 / R2 文件缺失（镜像 engine/rules.py；R3 不入子集，见模块 docstring）=====
    for sid in sorted(world["shipments"]):
        ship = world["shipments"][sid]
        st = ship["status"]
        eta = ship["eta_current"]
        active = [lid for lid in ship["line_ids"]
                  if world["lines"][lid]["line_status"] != "fulfilled"]
        # R1
        if st != "delivered":
            hits, worst, max_breach, value = [], "medium", 0, 0.0
            for lid in active:
                ln = world["lines"][lid]
                breach = (eta - ln["promised_delivery_date"]).days + buf
                if breach <= 0:
                    continue
                tier = world["customers"][world["sos"][ln["so_id"]]["customer_id"]]["tier"]
                sev = _line_sev(breach, tier)
                hits.append(lid)
                value += ln["qty"] * ln["unit_price_usd"]
                max_breach = max(max_breach, breach)
                worst = _worse(worst, sev)
            if hits:
                cands.append(_c("R1", "delay_breach", sid, "Shipment", sid, worst, max_breach,
                                value, f"eta_current+{buf}d 缓冲击穿承诺 {max_breach} 天", day,
                                affected=hits))
        # R2
        if (ship["missing_docs"] and st != "delivered" and ship["customs_status"] != "released"
                and (eta - day).days < det["docs_window_days"]):
            val = sum(world["lines"][l]["qty"] * world["lines"][l]["unit_price_usd"] for l in active)
            cands.append(_c("R2", "docs_missing", sid, "Shipment", sid, "high", 0, val,
                            f"缺件 {','.join(ship['missing_docs'])} 且 ETA 距今 < "
                            f"{det['docs_window_days']} 天", day, affected=active))

    # ===== R4 费率超收 / R5 重复计费 / R6 计划外费用（镜像 engine/cost_rules.py）=====
    lines_by_inv = {}
    for l in world["invoice_lines"]:
        lines_by_inv.setdefault(l["invoice_id"], []).append(l)
    for inv in world["invoices"]:
        if inv["issue_date"] > day.isoformat():
            continue                                   # as_of 纪律：未来发票不参与（XB6）
        sid = inv["shipment_id"]
        ship = world["shipments"].get(sid)
        if ship is None:
            continue
        issue = date.fromisoformat(inv["issue_date"])
        ils = lines_by_inv.get(inv["invoice_id"], [])
        tol = det["rate_tolerance"]
        # R4：单行 amount > baseline×(1+tol)，按票聚合超收
        over_lines, over_sum, max_ratio = [], 0.0, 0.0
        for ln in ils:
            base = _expected_baseline(world, ship, ln["charge_code"], issue)
            if base is None or base <= 0:
                continue
            if ln["amount_usd"] > base * (1 + tol) + 1e-9:
                over = ln["amount_usd"] - base
                over_lines.append(ln["invoice_line_id"])
                over_sum += over
                max_ratio = max(max_ratio, over / base)
        if over_lines:
            sev = "high" if max_ratio > 0.20 else "medium"
            cands.append(_c("R4", "rate_overbilling", sid, "Invoice", inv["invoice_id"], sev, 0,
                            over_sum, f"费率超收：{len(over_lines)} 行超基准（最大 "
                            f"{round(max_ratio * 100, 1)}%，超收合计 ${round(over_sum, 2)}）", day,
                            inv_lines=over_lines))
        # R5：同 (charge_code, container_no) > 1 行，首行外均为重复
        groups = {}
        for ln in sorted(ils, key=lambda x: x["invoice_line_id"]):
            groups.setdefault((ln["charge_code"], ln["container_no"]), []).append(ln)
        dup_lines, dup_sum = [], 0.0
        for g in groups.values():
            for ln in g[1:]:
                dup_lines.append(ln["invoice_line_id"])
                dup_sum += ln["amount_usd"]
        if dup_lines:
            cands.append(_c("R5", "duplicate_charge", sid, "Invoice", inv["invoice_id"], "high", 0,
                            dup_sum, f"重复计费：{len(dup_lines)} 行同费种同柜重复"
                            f"（${round(dup_sum, 2)}）", day, inv_lines=dup_lines))
        # R6：计划外费种（无基准）
        unp = [ln for ln in ils if ln["charge_code"] in SIM_UNPLANNED_CODES]
        if unp:
            unp_sum = sum(ln["amount_usd"] for ln in unp)
            has_det = any(ln["charge_code"] in ("DET", "DEM") for ln in unp)
            delay = ship["eta_current"] - ship["eta_initial"]
            sev = "high" if unp_sum > det["unplanned_high_usd"] else "medium"
            if has_det and delay.days > 0:
                reason = f"计划外费用 ${round(unp_sum, 2)}；含滞箱费，源于 ETA 延误 {delay.days} 天"
            else:
                reason = f"计划外费用 ${round(unp_sum, 2)}（无费率卡基准）"
            cands.append(_c("R6", "unplanned_charge", sid, "Invoice", inv["invoice_id"], sev, 0,
                            unp_sum, reason, day, inv_lines=[l["invoice_line_id"] for l in unp]))

    # ===== R16 断货（镜像 engine/warehouse_rules.py）=====
    for key in sorted(world["inventory"]):
        pos = world["inventory"][key]
        avail, safety = pos["available_qty"], pos["safety_stock"]
        if avail <= safety:
            sev = "high" if avail <= safety * det["stockout_high_frac"] else "medium"
            price = world["skus"][key[0]]["unit_price_usd"]
            cands.append(_c("R16", "stockout", None, "InventoryPosition",
                            pos["inventory_position_id"], sev, 0, (safety - avail) * price,
                            f"断货：可用 {avail} ≤ 安全库存 {safety}（缺口 {safety - avail} 件）",
                            day, warehouse_id=pos["warehouse_id"], sku_id=key[0]))
    return cands


def _c(rule, rtype, shipment_id, anchor_kind, anchor_id, severity, breach, value, reason, day,
       affected=None, inv_lines=None, warehouse_id=None, sku_id=None):
    return {
        "rule_id": rule, "type": rtype, "shipment_id": shipment_id,
        "anchor_kind": anchor_kind, "anchor_id": anchor_id, "severity": severity,
        "breach_days": breach, "affected_value_usd": round(value, 2), "root_cause": reason,
        "detected_at": day.isoformat(),
        "affected_so_line_ids": sorted(affected or []),
        "affected_invoice_line_ids": sorted(inv_lines or []),
        "warehouse_id": warehouse_id, "sku_id": sku_id,
        # 去重键：同锚点同规则只建一个 open 风险
        "dedup_key": f"{rule}|{anchor_kind}|{anchor_id}",
    }
