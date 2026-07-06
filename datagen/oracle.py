"""Ground truth oracle：在"干净世界"上按 R1-R3 计算应检风险（expected_risk_events）。

与 W4 引擎的关键区别：oracle 读干净世界状态；引擎必须从含噪源表重建后再判。
两者独立实现，评估以 oracle 为准（D6）。
规则澄清（W2 落定，manual §7 同步）：
- R1/R2 跳过 status=delivered 的 shipment；R2 另跳过 customs_status=released；
- R1 跳过 fulfilled/cancelled 行；事件 severity = 受影响行 per-line severity 取最大。
"""
import json
from datetime import date

SEV_ORDER = {"medium": 0, "high": 1, "critical": 2}
BUMP = {"medium": "high", "high": "critical", "critical": "critical"}


def _line_sev(breach_days, tier):
    base = "medium" if breach_days <= 3 else "high"
    return BUMP[base] if tier == "A" else base


def sweep(world, cfg):
    as_of = world["as_of"]
    buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
    stall_days = cfg["risk"]["stall_days"]
    docs_window = cfg["risk"]["docs_window_days"]

    lines, sos, customers, skus = world["lines"], world["sos"], world["customers"], world["skus"]
    alloc_by_ship = {}
    for a in world["allocations"]:
        alloc_by_ship.setdefault(a["shipment_id"], []).append(a)
    last_event = {}
    for m in world["milestones"]:
        d = date.fromisoformat(m["event_time"][:10])
        sid = m["shipment_id"]
        if sid not in last_event or d > last_event[sid]:
            last_event[sid] = d

    expected, seq = [], 0

    def emit(rule, rtype, sp, affected, severity, breach, value, reason):
        nonlocal seq
        seq += 1
        expected.append({
            "expected_risk_id": f"EXP-{seq:05d}", "rule_id": rule, "type": rtype,
            "shipment_id": sp["shipment_id"],
            "affected_so_line_ids": json.dumps(sorted(affected)),
            "severity": severity, "breach_days": breach,
            "affected_value_usd": round(value, 2), "reason": reason,
            "case_id": world.get("design_ship_case", {}).get(sp["shipment_id"], ""),
        })

    for sp in sorted(world["shipments"].values(), key=lambda s: s["shipment_id"]):
        active_allocs = [a for a in alloc_by_ship.get(sp["shipment_id"], [])
                         if lines[a["so_line_id"]]["line_status"] in ("allocated", "open")]
        # --- R1 延误传导 ---
        if sp["status"] != "delivered":
            hits, worst, max_breach, value = [], "medium", 0, 0.0
            ready = sp["eta_current"]
            for a in active_allocs:
                ln = lines[a["so_line_id"]]
                breach = (ready - ln["promised_delivery_date"]).days + buf
                if breach <= 0:
                    continue
                tier = customers[sos[ln["so_id"]]["customer_id"]]["tier"]
                sev = _line_sev(breach, tier)
                hits.append(a["so_line_id"])
                value += a["allocated_qty"] * skus[ln["sku_id"]]["unit_price_usd"]
                max_breach = max(max_breach, breach)
                if SEV_ORDER[sev] > SEV_ORDER[worst]:
                    worst = sev
            if hits:
                emit("R1", "delay_breach", sp, hits, worst, max_breach, value,
                     f"eta_current+{buf}d buffers breaches promise by {max_breach}d")
        # --- R2 文件缺失 ---
        if (sp["missing_docs"] and sp["status"] != "delivered" and sp["customs_status"] != "released"
                and (sp["eta_current"] - as_of).days < docs_window):
            emit("R2", "docs_missing", sp, [a["so_line_id"] for a in active_allocs], "high", 0,
                 sum(a["allocated_qty"] * skus[lines[a["so_line_id"]]["sku_id"]]["unit_price_usd"]
                     for a in active_allocs),
                 f"missing {','.join(sp['missing_docs'])} with eta within {docs_window}d")
        # --- R3 静默停滞 ---
        gap = (as_of - last_event.get(sp["shipment_id"], as_of)).days
        if sp["status"] == "in_transit" and gap >= stall_days:
            emit("R3", "stalled", sp, [a["so_line_id"] for a in active_allocs], "medium", 0,
                 sum(a["allocated_qty"] * skus[lines[a["so_line_id"]]["sku_id"]]["unit_price_usd"]
                     for a in active_allocs),
                 f"in_transit with no milestone for {gap}d")
    return expected
