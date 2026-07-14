"""F2 活世界补灌：回填后派生 pass——为采购收货 / 准入 / 库存盘点 / 资金流域补灌活世界实例，
让驾驶舱体征带七区全部点亮。

为什么这样建（≤5 行）：① 全部用独立命名 rng 子流（procurement/admission/finance，附在
backfill.STREAMS 末尾）——既有 world..ai 八条子流一格不动 → S1/S2 世界逐字节不变（补灌铁律）。
② 补灌是"回填完成后的派生 pass"：收货对 PO（received≤as_of 才收）、供票对收货、付款对发票、
准入案含成本情景、盘点并轨自 sim_cycle_counts——因果链单向自洽、时间线不穿越 as_of。③ 资金流侧
无 P/R 评估（sim 世界），但 AI 逾期应收今日流用真实 AR 账龄复核逻辑（as_of 当日 sweep 标记
上次复核后新逾期的应收）——"AI 今日"非零是真实账龄事件，非冒充。④ 一切 id 带 -SIM- 后缀、
风险/任务/活动 source='sim'，与验证世界物理隔离。
"""
import json
from datetime import date, timedelta

from . import generators as G
from . import ai_loop as AI


def _d(s):
    return date.fromisoformat(s) if isinstance(s, str) else s


# ═══════════════════════════ 采购收货域（供应商区四指标）═══════════════════════════
def _procurement(world, cfg, rng):
    """为齐货日 ≤ as_of 的 PO 生成 GoodsReceipt（收货）+ 行（defect_ppm/QC）+ SupplierInvoice。
    交期达成率由 received_date vs expected_ready_date 决定，概率随 supplier reliability/chronic 调制
    （chronic/低可靠更常迟交 → 整体率 ~78-88%，非 100 非 0）；received > as_of 的 PO 视为在途未收，不建收货。"""
    pc = cfg["enrichment"]["procurement"]
    as_of = world["_as_of"]
    grns, grnls, sinvs = [], [], []
    for po_id in sorted(world["pos"]):
        po = world["pos"][po_id]
        ready = po["expected_ready_date"]
        if ready > as_of:
            continue                                   # 货未到齐货日：在途未收，无收货单
        sup = world["suppliers"][po["supplier_id"]]
        p_on = min(0.97, pc["on_time_base"] * (sup["reliability"] / 0.90))
        if sup["chronic_delay"]:
            p_on *= 0.6                                 # 惯性延期供应商达成率显著更低
        if rng.random() < p_on:
            off = -rng.randint(*pc["early_days"])       # 达成：提前/准时到
        else:
            off = rng.randint(*pc["late_days"])         # 未达成：迟到
        received = ready + timedelta(days=off)
        if received > as_of or received < po["po_date"]:
            continue                                   # 收货落在未来 / 早于下单：不合法，跳过
        grn_id = G._nid(world, "grn", "GRN-SIM", 5)
        grns.append({"grn_id": grn_id, "po_id": po_id, "received_date": received.isoformat(),
                     "status": "received", "as_of_date": as_of.isoformat(),
                     "created_at": received.isoformat()})
        for lid in po["line_ids"]:
            ln = world["lines"][lid]
            qty = ln["qty"]
            base_ppm = pc["defect_ppm_cap"] * (1.0 - sup["quality"])   # 高质供应商趋 0
            defect_ppm = max(30, int(base_ppm * rng.uniform(0.4, 1.6)))
            qc_fail = rng.random() < pc["qc_fail_rate"]
            rejected = int(qty * rng.uniform(0.02, 0.08)) if qc_fail else 0
            grnls.append({
                "grn_line_id": G._nid(world, "grnl", "GRL-SIM", 6), "grn_id": grn_id,
                "po_line_id": lid, "received_qty": qty, "accepted_qty": qty - rejected,
                "rejected_qty": rejected, "qc_status": "failed" if qc_fail else "passed",
                "defect_ppm": defect_ppm, "received_date": received.isoformat(),
                "as_of_date": as_of.isoformat(), "created_at": received.isoformat()})
        total = round(sum(world["lines"][lid]["qty"]
                          * world["skus"][world["lines"][lid]["sku_id"]]["unit_price_usd"]
                          for lid in po["line_ids"]), 2)
        issue = min(received + timedelta(days=rng.randint(*pc["invoice_lag_days"])), as_of)
        sinv_id = G._nid(world, "sinv", "SINV-SIM", 5)
        sinvs.append({
            "supplier_invoice_id": sinv_id, "supplier_id": po["supplier_id"], "po_id": po_id,
            "vendor_invoice_no": f"{po['supplier_id']}-{sinv_id[-5:]}", "issue_date": issue.isoformat(),
            "currency": "USD", "total_usd": total, "status": "received",
            "as_of_date": as_of.isoformat(), "created_at": issue.isoformat()})
    world["goods_receipts"], world["goods_receipt_lines"] = grns, grnls
    world["supplier_invoices"] = sinvs


# ═══════════════════════════ 准入域（客户区漏斗 + 钱区毛利分布）═══════════════════════
def _weighted_list(weights, n):
    """确定性把 {status: weight} 展开成长度 n 的状态序列（漏斗各态按权重占位，无 rng）。"""
    items = sorted(weights.items())
    total = sum(w for _, w in items)
    out = []
    for s, w in items:
        out += [s] * round(w / total * n)
    while len(out) < n:
        out.append(items[0][0])
    return out[:n]


def _admission(world, cfg, rng):
    """生成准入案件（漏斗各状态分布）+ 为定价态案件生成 CostScenario（毛利率四桶全覆盖）。"""
    adm = cfg["enrichment"]["admission"]
    as_of = world["_as_of"]
    statuses = _weighted_list(adm["status_weights"], adm["case_count"])
    cust_ids, sku_ids = sorted(world["customers"]), sorted(world["skus"])
    reps = adm["margin_reps"]
    cases, scenarios, priced_i = [], [], 0
    for status in statuses:
        cid = cust_ids[rng.randrange(len(cust_ids))]
        kid = sku_ids[rng.randrange(len(sku_ids))]
        case_id = G._nid(world, "adm", "ADM-SIM", 5)
        decision = {"approved": "approved", "rejected": "rejected",
                    "quote_with_conditions": "conditional"}.get(status, "")
        reason = {"approved": "毛利达标、合规齐备", "rejected": "毛利为负或合规缺口",
                  "quote_with_conditions": "附条件报价（补件后放行）"}.get(status, "")
        cases.append({
            "admission_case_id": case_id,
            "case_title": f"{world['skus'][kid]['sku_name']} 上架申请",
            "customer_id": cid, "sku_id": kid, "request_type": "new_sku",
            "incoterm_candidate": rng.choice(["FOB", "CIF", "DDP"]),
            "target_launch_date": (as_of + timedelta(days=rng.randint(20, 120))).isoformat(),
            "monthly_order_estimate": rng.randint(200, 5000),
            "risk_level": rng.choice(["low", "low", "medium", "high"]),
            "status": status, "decision": decision, "decision_reason": reason,
            "conditions": "补齐 origin evidence" if status == "quote_with_conditions" else ""})
        if status in adm["priced_statuses"]:
            margin = reps[priced_i % len(reps)]           # 轮转覆盖四桶（loss/0-10/10-20/≥20）
            priced_i += 1
            quote = round(rng.uniform(9.0, 60.0), 2)
            gm = round(quote * margin, 2)
            cost = round(quote - gm, 2)                    # 总成本 = 报价 − 毛利
            # 成本分摊（固定比例，和 = cost；驾驶舱仅用 gross_margin_rate，其余保内部一致）
            def part(f):
                return round(cost * f, 2)
            scenarios.append({
                "cost_scenario_id": G._nid(world, "cs", "COST-SIM", 5),
                "logistics_plan_id": f"LPLAN-SIM-{case_id[-5:]}", "scenario_type": "baseline",
                "quote_price_usd": quote, "product_cost_usd": part(0.55),
                "first_mile_cost_usd": part(0.05), "international_freight_usd": part(0.16),
                "duty_tax_usd": part(0.09), "customs_brokerage_usd": part(0.03),
                "warehouse_cost_usd": part(0.04), "last_mile_cost_usd": part(0.05),
                "returns_allowance_usd": part(0.02), "risk_buffer_usd": part(0.01),
                "gross_margin_usd": gm, "gross_margin_rate": margin})
    world["admission_cases"], world["cost_scenarios"] = cases, scenarios


# ═══════════════════════════ 库存盘点域（库存区盘点差异）═══════════════════════════
def _cycle_counts(world):
    """并轨 sim_cycle_counts → 本体 cycle_counts（补 variance=实盘−账面、status；复用既有因果盘点差异，
    不新造随机 → 无 rng 消耗）。差异全非零（S2 shrinkage 恒为负），点亮库存区盘点差异指标。"""
    as_of = world["_as_of"].isoformat()
    world["cycle_counts"] = [{
        "cycle_count_id": c["cycle_count_id"], "inventory_position_id": c["inventory_position_id"],
        "warehouse_id": c["warehouse_id"], "system_qty": c["system_qty"],
        "counted_qty": c["counted_qty"], "variance": c["counted_qty"] - c["system_qty"],
        "status": "counted", "as_of_date": c.get("as_of_date", as_of)}
        for c in world.get("_cycle_counts", [])]


# ═══════════════════════════ 资金流域（payments + AI 逾期应收今日流）═══════════════════
def _lines_by_so(world):
    m = {}
    for lid in sorted(world["lines"]):
        m.setdefault(world["lines"][lid]["so_id"], []).append(lid)
    return m


def _pay_row(world, direction, cp_type, cp_id, ref_type, ref_id, amount, due, status, paid_date):
    return {"payment_id": G._nid(world, "pay", "PAY-SIM", 6), "direction": direction,
            "counterparty_type": cp_type, "counterparty_id": cp_id, "ref_type": ref_type,
            "ref_id": ref_id, "amount_usd": round(amount, 2), "due_date": due.isoformat(),
            "paid_date": paid_date.isoformat() if paid_date else "", "status": status,
            "as_of_date": world["_as_of"].isoformat(),
            "created_at": (paid_date or due).isoformat()}


def _finance(world, cfg, rng):
    """payments 三源：in 挂 SalesOrder（回款）/ out 挂 SupplierInvoice（货款）与物流 Invoice（费用）。
    due 由账期推算；~80/85% 已 paid，其余 scheduled（在途应收/应付）；最后一个 cadence 缺口内
    crossing 的应收强制未付 → 保证 as_of AR 复核当日有新逾期（AI 今日流锚点，见 _finance_ai）。"""
    fin = cfg["enrichment"]["finance"]
    as_of = world["_as_of"]
    guar = fin["horizon_guarantee_days"]
    lines_by_so = _lines_by_so(world)
    pays = []

    # —— in 向：应收（每 SalesOrder 一笔）——
    for so_id in sorted(world["sos"]):
        so = world["sos"][so_id]
        amt = sum(world["lines"][lid]["qty"] * world["lines"][lid]["unit_price_usd"]
                  for lid in lines_by_so.get(so_id, []))
        if amt <= 0:
            continue
        due = so["order_date"] + timedelta(days=fin["receivable_terms_days"])
        crossing = due + timedelta(days=8)               # R19：as_of > due+7 起判逾期
        # 末端 guarantee 天（含 as_of）crossing 的应收强制未付 → 保证 07-13/07-14 各有新逾期（AI 今日流锚点）
        force_overdue = as_of - timedelta(days=guar) < crossing <= as_of
        if due > as_of:
            pays.append(_pay_row(world, "in", "customer", so["customer_id"], "sales_order",
                                 so_id, amt, due, "scheduled", None))          # 未到期（在途应收）
        elif force_overdue or rng.random() >= fin["receivable_paid_rate"]:
            pays.append(_pay_row(world, "in", "customer", so["customer_id"], "sales_order",
                                 so_id, amt, due, "scheduled", None))          # 未回款（逾期/待收）
        else:
            pd = max(min(due + timedelta(days=rng.randint(-3, 5)), as_of), so["order_date"])
            pays.append(_pay_row(world, "in", "customer", so["customer_id"], "sales_order",
                                 so_id, amt, due, "paid", pd))

    # —— out 向：应付供应商（每 SupplierInvoice 一笔，due = issue + 供应商账期）——
    for si in sorted(world.get("supplier_invoices", []), key=lambda x: x["supplier_invoice_id"]):
        terms = world["suppliers"][si["supplier_id"]]["payment_terms_days"]
        due = _d(si["issue_date"]) + timedelta(days=terms)
        _emit_payable(world, pays, fin, as_of, rng, "supplier", si["supplier_id"],
                      "supplier_invoice", si["supplier_invoice_id"], si["total_usd"], due)

    # —— out 向：应付物流商（每物流 Invoice 一笔，due = issue + 物流账期）——
    for inv in sorted(world.get("invoices", []), key=lambda x: x["invoice_id"]):
        due = _d(inv["issue_date"]) + timedelta(days=fin["vendor_terms_days"])
        _emit_payable(world, pays, fin, as_of, rng, "vendor", inv["vendor_name"],
                      "invoice", inv["invoice_id"], inv["total_usd"], due)

    world["payments"] = pays
    _inject_r21(world, cfg, rng)                          # 付款异常纹理（重复付款/金额不符 → R21）


def _emit_payable(world, pays, fin, as_of, rng, cp_type, cp_id, ref_type, ref_id, amount, due):
    if due > as_of:
        pays.append(_pay_row(world, "out", cp_type, cp_id, ref_type, ref_id, amount, due,
                             "scheduled", None))                                # 未到期（在途应付）
    elif rng.random() >= fin["payable_paid_rate"]:
        pays.append(_pay_row(world, "out", cp_type, cp_id, ref_type, ref_id, amount, due,
                             "scheduled", None))                                # 待付
    else:
        pd = min(due + timedelta(days=rng.randint(-2, 4)), as_of)
        pays.append(_pay_row(world, "out", cp_type, cp_id, ref_type, ref_id, amount, due,
                             "paid", pd))


def _inject_r21(world, cfg, rng):
    """付款异常纹理：抽已 paid 的 out 应付，复制一条重复付款（同 ref 二次结清）→ R21 靶。"""
    n = cfg["enrichment"]["finance"]["r21_anomalies"]
    paid_out = [p for p in world["payments"] if p["direction"] == "out" and p["status"] == "paid"]
    world["_r21_dups"] = []
    if not paid_out:
        return
    picks = sorted(paid_out, key=lambda p: p["payment_id"])
    step = max(1, len(picks) // (n + 1))
    for k in range(n):
        src = picks[min((k + 1) * step, len(picks) - 1)]
        dup = _pay_row(world, "out", src["counterparty_type"], src["counterparty_id"],
                       src["ref_type"], src["ref_id"], src["amount_usd"], _d(src["due_date"]),
                       "paid", _d(src["paid_date"]) + timedelta(days=rng.randint(1, 5)))
        world["payments"].append(dup)
        world["_r21_dups"].append((src, dup))


# ═══════════════════ 资金流 AI：逾期应收今日流（真实 AR 账龄复核，非冒充）═══════════════════
def _finance_ai(world, cfg, rng):
    """AI 财务同事逐日复核 AR 账龄：每笔近端逾期应收在其 crossing 当日（=due+8，转 7 天逾期那天）
    被标记 → 生成 R19 风险 + 催收提案（ProposeCollection/A26，pending 候人批）。逾期按 crossing 日
    自然铺在近端各天（含 07-13/07-14）；_finance 已保证末端 guarantee 天有强制未付应收 → as_of 当日
    必有新逾期 → "AI 今日"真实非零（真实账龄事件，非冒充）。只覆盖近端窗（overdue_recent_days）——
    远期陈欠视为线下已处理，不灌入活 AI 流（避免刷量）。另对重复付款（_inject_r21）在 as_of 复核出 R21。
    全部 source='sim'、-SIM- id、affected 行留空（财务风险非交付敞口，不污染客户区口径）。"""
    fin = cfg["enrichment"]["finance"]
    as_of = world["_as_of"]
    recent_lo = as_of - timedelta(days=fin["overdue_recent_days"])

    overdue = []
    for p in world["payments"]:
        if p["direction"] != "in" or p["status"] != "scheduled" or not p["due_date"]:
            continue
        cross = _d(p["due_date"]) + timedelta(days=8)
        if recent_lo <= cross <= as_of:
            overdue.append((cross, p))
    overdue.sort(key=lambda cp: (cp[0], cp[1]["payment_id"]))

    for cross, p in overdue:
        _emit_finance_risk(world, "R19", "overdue_receivable", cross, p["amount_usd"],
                           f"逾期应收：客户 {p['counterparty_id']} 订单 {p['ref_id']} "
                           f"${round(p['amount_usd'], 2)} 逾期 {(as_of - _d(p['due_date'])).days} 天",
                           "propose_collection", "cs",
                           {"collect_amount_usd": round(p["amount_usd"], 2),
                            "customer_id": p["counterparty_id"]},
                           f"催收可回收 ${round(p['amount_usd'], 2)}", rng)

    for src, dup in world.get("_r21_dups", []):              # 重复付款 → R21，as_of 复核出
        _emit_finance_risk(world, "R21", "payment_anomaly", as_of, dup["amount_usd"],
                           f"付款异常：{dup['ref_type']} {dup['ref_id']} 出现重复付款"
                           f"（${round(dup['amount_usd'], 2)} 二次结清）",
                           "reconcile_payment", "finance",
                           {"disputed_amount_usd": round(dup["amount_usd"], 2),
                            "payment_id": dup["payment_id"]},
                           f"追回重复付款 ${round(dup['amount_usd'], 2)}", rng)


def _emit_finance_risk(world, rule, rtype, day, amount, root_cause, action, role, params, verdict, rng):
    sev = "critical" if amount > 50000 else "high"
    rid = G._nid(world, "rsk", "RSK-SIM", 5)
    world["risk_events"][rid] = {
        "risk_event_id": rid, "type": rtype, "rule_id": rule, "severity": sev,
        "shipment_id": "", "affected_so_line_ids": "[]", "affected_value_usd": round(amount, 2),
        "detected_at": day.isoformat(), "root_cause": root_cause, "status": "open",
        "resolved_at": "", "outcome": "", "resolution_summary": "",
        "affected_invoice_line_ids": "[]", "po_id": "", "supplier_id": "",
        "affected_po_line_ids": "[]", "warehouse_id": "", "source": "sim"}
    AI._log_ai(world, day, AI.AI_ACTOR, "detect", rid, None,
               f"{rule} {rtype} sev={sev} ${round(amount, 2)}")
    tid = G._nid(world, "task", "TSK-SIM", 5)
    world["tasks"][tid] = {
        "task_id": tid, "risk_event_id": rid, "title": f"{rule} {rtype} 处置提案",
        "assignee_role": role, "priority": AI.PRIORITY[sev], "proposed_action": action,
        "proposal_params": json.dumps(params, ensure_ascii=False), "approval_status": "pending",
        "approved_by_role": "", "action_taken": "", "status": "open",
        "proposal_actor_id": AI.AI_ACTOR, "decided_at": "", "decision_day": "",
        "economics_json": json.dumps({"verdict": verdict, "recoverable_usd": round(amount, 2)},
                                     ensure_ascii=False),
        "precedent_block": f"无先例，首例（rule={rule}）。", "source": "sim"}
    AI._log_ai(world, day, AI.AI_ACTOR, "propose", rid, tid,
               f"提案「{action}」；经济账 {verdict}")


# ═══════════════════════════════════════ 主入口 ═══════════════════════════════════════
def enrich(world, cfg, streams):
    """回填后补灌全域（顺序：采购→准入→盘点→资金流→资金流 AI 今日流）。各域用独立子流。"""
    _procurement(world, cfg, streams["procurement"])
    _admission(world, cfg, streams["admission"])
    _cycle_counts(world)
    _finance(world, cfg, streams["finance"])
    _finance_ai(world, cfg, streams["finance"])
