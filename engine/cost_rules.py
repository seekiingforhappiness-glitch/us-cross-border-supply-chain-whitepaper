"""X3 费用异常规则 R4-R6（cost-manual-v0.4 §3）。

与 engine/rules.py 同构：只读 data/ontology.sqlite（重建层），as_of 必须显式传入（D8）。
候选结构对齐 rules.emit：type/rule_id/shipment_id/severity/detected_at/root_cause/
affected_value_usd + affected_so_line_ids（费用异常恒为 "[]"）+ affected_invoice_line_ids
（排序 JSON，P1 新列）。写库与 A2 合并语义仍走 detect.apply_candidates。

匹配键 (shipment_id, charge_code, container_no 或空)；基准来自 expected_costs 表。
as_of 纪律（XB6）：只处理 issue_date ≤ as_of 的发票行——未来发票不参与检测。

为什么按票聚合：manual §3 的 A2 聚合键是 (shipment_id, type)，一票同类异常合成一个
RiskEvent（复用控制塔队列），affected_value 是异常金额之和而非账单总额。
"""
import json

# 计划外费种（无 ExpectedCost 基准——R6 判据；与 datagen UNPLANNED_CODES 同步）
UNPLANNED_CODES = {"DET", "DEM", "CHS", "ACC"}


def detect_cost_anomalies(con, as_of, cfg):
    """返回费用异常候选列表（不写库）。cfg 需含 cost.rate_tolerance /
    cost.unplanned_high_threshold_usd。"""
    ccfg = cfg["cost"]
    tol = ccfg["rate_tolerance"]
    unplanned_high = ccfg["unplanned_high_threshold_usd"]
    as_of_str = as_of.isoformat()

    # as_of 纪律：只取 issue_date ≤ as_of 的发票行（XB6）。一次 join 拉全量含票据元数据。
    rows = [dict(r) for r in con.execute(
        """SELECT il.invoice_line_id, il.invoice_id, il.charge_code, il.container_no,
                  il.amount_usd, iv.shipment_id, iv.issue_date
           FROM invoice_lines il JOIN invoices iv ON iv.invoice_id = il.invoice_id
           WHERE iv.issue_date <= ?
           ORDER BY iv.shipment_id, il.invoice_line_id""", (as_of_str,))]

    # 基准表：键 (shipment_id, charge_code, container_no 或空) → baseline_usd
    baseline = {}
    for e in con.execute("SELECT shipment_id, charge_code, container_no, baseline_usd "
                         "FROM expected_costs"):
        baseline[(e["shipment_id"], e["charge_code"], e["container_no"] or "")] = e["baseline_usd"]

    # shipment 延误天数（F4 归因）
    delay_of = {r["shipment_id"]: r["delay_days"]
                for r in con.execute("SELECT shipment_id, delay_days FROM shipments")}

    # 按 shipment 分桶
    by_ship = {}
    for r in rows:
        by_ship.setdefault(r["shipment_id"], []).append(r)

    cands = []

    def emit(rule, rtype, sid, severity, value, reason, inv_lines):
        cands.append({
            "rule_id": rule, "type": rtype, "shipment_id": sid,
            "affected_so_line_ids": "[]",              # 费用异常不牵动订单行
            "severity": severity, "breach_days": 0,
            "affected_value_usd": round(value, 2), "root_cause": reason,
            "detected_at": as_of_str,
            "affected_invoice_line_ids": json.dumps(sorted(inv_lines))})

    for sid in sorted(by_ship):
        lines = by_ship[sid]

        # --- R4 rate_overbilling：line.amount > baseline×(1+tol) ---
        # 按票聚合 over_sum=Σ超收部分（amount-baseline），severity 由聚合内最大单行超收比定。
        over_lines, over_sum, max_ratio = [], 0.0, 0.0
        for ln in lines:
            base = baseline.get((sid, ln["charge_code"], ln["container_no"] or ""))
            if base is None or base <= 0:
                continue
            if ln["amount_usd"] > base * (1 + tol) + 1e-9:
                over = ln["amount_usd"] - base
                ratio = over / base
                over_lines.append(ln["invoice_line_id"])
                over_sum += over
                max_ratio = max(max_ratio, ratio)
        if over_lines:
            sev = "high" if max_ratio > 0.20 else "medium"
            emit("R4", "rate_overbilling", sid, sev, over_sum,
                 f"费率超收：{len(over_lines)} 行超基准（最大超收比 {round(max_ratio * 100, 1)}%，"
                 f"超收合计 ${round(over_sum, 2)}）", over_lines)

        # --- R5 duplicate_charge：同匹配键 >1 行，按 invoice_line_id 排序首行外均重复 ---
        groups = {}
        for ln in sorted(lines, key=lambda x: x["invoice_line_id"]):
            groups.setdefault((ln["charge_code"], ln["container_no"] or ""), []).append(ln)
        dup_lines, dup_sum = [], 0.0
        for key, g in groups.items():
            if len(g) > 1:
                for ln in g[1:]:                       # 首行不计，其余为重复
                    dup_lines.append(ln["invoice_line_id"])
                    dup_sum += ln["amount_usd"]
        if dup_lines:
            emit("R5", "duplicate_charge", sid, "high", dup_sum,
                 f"重复计费：{len(dup_lines)} 行同费种同柜重复（重复金额 ${round(dup_sum, 2)}）",
                 dup_lines)

        # --- R6 unplanned_charge：charge_code ∈ UNPLANNED_CODES（无基准费种）---
        unp_lines = [ln for ln in lines if ln["charge_code"] in UNPLANNED_CODES]
        if unp_lines:
            unp_sum = sum(ln["amount_usd"] for ln in unp_lines)
            unp_ids = [ln["invoice_line_id"] for ln in unp_lines]
            has_det = any(ln["charge_code"] in ("DET", "DEM") for ln in unp_lines)
            sev = "high" if unp_sum > unplanned_high else "medium"
            delay = delay_of.get(sid, 0) or 0
            if has_det and delay > 0:
                # F4 跨场景归因：DET/DEM 且延误 → root_cause 必含"源于 ETA 延误 N 天"
                reason = (f"计划外费用 ${round(unp_sum, 2)}；含滞箱费，源于 ETA 延误 {delay} 天")
            else:
                reason = f"计划外费用 ${round(unp_sum, 2)}（无费率卡基准）"
            emit("R6", "unplanned_charge", sid, sev, unp_sum, reason, unp_ids)

    return cands
