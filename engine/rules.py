"""W4 风险规则 R1-R3（manual §7 + W2 澄清）。

铁律：只读 data/ontology.sqlite（重建层），禁读 data/truth/；as_of_date 必须显式传入（D8）。
与 datagen/oracle.py 相互独立实现——oracle 读干净世界，本引擎读含噪重建结果，
评估测的是两者是否收敛（D6）。
"""
import json
from collections import defaultdict
from datetime import date

from pipeline.build_ontology import derive_shipment_state

SEV_ORDER = {"medium": 0, "high": 1, "critical": 2}
BUMP = {"medium": "high", "high": "critical", "critical": "critical"}


def _line_sev(breach_days, tier):
    base = "medium" if breach_days <= 3 else "high"
    return BUMP[base] if tier == "A" else base


def detect_risks(con, as_of, cfg):
    """返回风险候选列表（不写库——写入与合并语义在 detect.apply_candidates）。"""
    buf = cfg["buffers"]["customs_days"] + cfg["buffers"]["lastmile_days"]
    stall_days = cfg["risk"]["stall_days"]
    docs_window = cfg["risk"]["docs_window_days"]
    D = date.fromisoformat

    ships = list(con.execute("SELECT * FROM shipments ORDER BY shipment_id"))
    # D8 时间旅行安全：不信管道在快照日算好的状态，按 as_of 从事件流现场推导。
    # as_of 之后发生的事件必须不可见（demo-assertions A1 的依据）。
    ms_rows = con.execute("""SELECT * FROM shipment_milestones
                             WHERE is_duplicate = 0 AND substr(event_time, 1, 10) <= ?
                             ORDER BY event_time""", (as_of.isoformat(),))
    ms_by_ship = defaultdict(list)
    for m in ms_rows:
        ms_by_ship[m["shipment_id"]].append(dict(m))
    state = {sp["shipment_id"]: derive_shipment_state(sp["shipment_id"],
                                                      ms_by_ship.get(sp["shipment_id"], []),
                                                      sp["eta_initial"])
             for sp in ships}
    alloc_rows = con.execute("""
        SELECT a.shipment_id, a.so_line_id, a.allocated_qty,
               l.promised_delivery_date, l.unit_price_usd, l.line_status, c.tier
        FROM shipment_allocations a
        JOIN sales_order_lines l ON l.so_line_id = a.so_line_id
        JOIN sales_orders so     ON so.so_id = l.so_id
        JOIN customers c         ON c.customer_id = so.customer_id""")
    by_ship = defaultdict(list)
    for r in alloc_rows:
        by_ship[r["shipment_id"]].append(dict(r))

    cands = []

    def emit(rule, rtype, sp, affected, severity, breach, value, reason):
        cands.append({"rule_id": rule, "type": rtype, "shipment_id": sp["shipment_id"],
                      "affected_so_line_ids": json.dumps(sorted(affected)),
                      "severity": severity, "breach_days": breach,
                      "affected_value_usd": round(value, 2), "root_cause": reason,
                      "detected_at": as_of.isoformat()})

    # 行的 as_of 履约状态：其全部承运船 as_of 时已 delivered → fulfilled（不参与风险）
    ships_of_line = defaultdict(list)
    for rows in by_ship.values():
        for r in rows:
            ships_of_line[r["so_line_id"]].append(r["shipment_id"])

    def line_active(lid):
        return not all(state[s]["status"] == "delivered" for s in ships_of_line[lid])

    for sp in ships:
        st = state[sp["shipment_id"]]
        active = [r for r in by_ship.get(sp["shipment_id"], []) if line_active(r["so_line_id"])]
        # --- R1 延误传导 ---
        if st["status"] != "delivered":
            hits, worst, max_breach, value = [], "medium", 0, 0.0
            eta = D(st["eta_current"])
            for r in active:
                breach = (eta - D(r["promised_delivery_date"])).days + buf
                if breach <= 0:
                    continue
                sev = _line_sev(breach, r["tier"])
                hits.append(r["so_line_id"])
                value += r["allocated_qty"] * r["unit_price_usd"]
                max_breach = max(max_breach, breach)
                if SEV_ORDER[sev] > SEV_ORDER[worst]:
                    worst = sev
            if hits:
                emit("R1", "delay_breach", sp, hits, worst, max_breach, value,
                     f"eta_current+{buf}d buffers breaches promise by {max_breach}d")
        # --- R2 文件缺失 ---
        if (sp["missing_docs"] and st["status"] != "delivered"
                and st["customs_status"] != "released"
                and (D(st["eta_current"]) - as_of).days < docs_window):
            emit("R2", "docs_missing", sp, [r["so_line_id"] for r in active], "high", 0,
                 sum(r["allocated_qty"] * r["unit_price_usd"] for r in active),
                 f"missing {sp['missing_docs'].replace('|', ',')} with eta within {docs_window}d")
        # --- R3 静默停滞 ---
        if st["status"] == "in_transit" and st["last_event_time"]:
            gap = (as_of - D(st["last_event_time"][:10])).days
            if gap >= stall_days:
                emit("R3", "stalled", sp, [r["so_line_id"] for r in active], "medium", 0,
                     sum(r["allocated_qty"] * r["unit_price_usd"] for r in active),
                     f"in_transit with no milestone for {gap}d")
    return cands
