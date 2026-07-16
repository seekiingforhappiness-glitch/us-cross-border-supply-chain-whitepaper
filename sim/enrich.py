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
        # G6 枚举清零（本体 AdmissionCase.decision 治本，V12 决策日志延伸）：sim 原产
        # approved/rejected/conditional，本体枚举为 approve/quote_with_conditions/reject/more_info——
        # 逐值改到本体标准词（approved→approve、rejected→reject、conditional→quote_with_conditions，
        # 仅时态/构词差异，语义等价，非语义变化）。纯字面量改不消费 rng → S1/S2 随机序列逐字节不变。
        decision = {"approved": "approve", "rejected": "reject",
                    "quote_with_conditions": "quote_with_conditions"}.get(status, "")
        reason = {"approved": "毛利达标、合规齐备", "rejected": "毛利为负或合规缺口",
                  "quote_with_conditions": "附条件报价（补件后放行）"}.get(status, "")
        cases.append({
            "admission_case_id": case_id,
            "case_title": f"{world['skus'][kid]['sku_name']} 上架申请",
            "customer_id": cid, "sku_id": kid, "request_type": "new_sku",
            # G6 枚举清零（本体 AdmissionCase.incoterm_candidate 治本）：sim 原误用船运 incoterm 三元组
            # ["FOB","CIF","DDP"]，但准入域本体枚举为 [FOB,DAP,DDP,tbd]——真实世界 datagen 恰用 DAP、
            # 从不用 CIF（G6 实测：DAP×11/DDP×12/FOB×12/tbd×5，零 CIF）。CIF 是船运域端口交货术语，
            # 准入域刻意选 DAP（门到门，对应 dap_quote 请求类型），故 sim 的 CIF 判为拷贝误写 → 映射到
            # 位置同序的 DAP：rng.choice 抽签次数与被选下标均不变，仅该下标字面量 CIF→DAP，零 rng 扰动。
            "incoterm_candidate": rng.choice(["FOB", "DAP", "DDP"]),
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
                # G6 枚举清零（本体 CostScenario.scenario_type 治本）：sim 原产 "baseline"，本体枚举为
                # [conservative,base,optimistic]——baseline 即 base（基准场景），改到本体标准词（同义，非语义变化）。
                "logistics_plan_id": f"LPLAN-SIM-{case_id[-5:]}", "scenario_type": "base",
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
        # C 任务对齐（改 sim 对齐真实世界）：sim 原写 "propose_collection"，但真实世界
        # app/actions.py:380 的 propose_collection() 实际写入的 proposed_action 值是 "collect"
        # （被 app/test_finance_loop.py:122 断言锁定为权威值）。这是 sim 与真实世界的语义标签不一致，
        # 本轮按主会话指派统一到真实世界权威值 "collect"（只动 sim 侧、不碰 app/actions.py）。纯字面量
        # 改、不消费 rng → S1/S2 随机序列逐字节不变；仅 tasks.proposed_action（+ 派生的 sim_ai_activity
        # detail 文案）该列这批 R19 催收任务的值 propose_collection→collect。本体枚举两值皆收编、均合法。
        _emit_finance_risk(world, "R19", "overdue_receivable", cross, p["amount_usd"],
                           f"逾期应收：客户 {p['counterparty_id']} 订单 {p['ref_id']} "
                           f"${round(p['amount_usd'], 2)} 逾期 {(as_of - _d(p['due_date'])).days} 天",
                           "collect", "cs",
                           {"collect_amount_usd": round(p["amount_usd"], 2),
                            "customer_id": p["counterparty_id"]},
                           f"催收可回收 ${round(p['amount_usd'], 2)}", rng)

    for src, dup in world.get("_r21_dups", []):              # 重复付款 → R21，as_of 复核出
        # G4 核实：R21 目前在 app/actions.py 无任何已实现的处置动作（有检测 engine/finance_rules.py，
        # 无处置）——"reconcile_payment" 不与任何真实值冲突，是这条处置路径的首次实现，本体如实收编。
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


# ══════════════════════ C 补全域：12 类零实例对象（派生自上面已建世界）══════════════════════
# 全部在既有 F2 补灌之后运行——依赖 world 里已就位的 PO/供票/准入案/船/供应商/风险任务；
# id 带 -SIM- 后缀、source（若适用）='sim'、时间戳 ≤ as_of、独立命名子流不消费既有 rng。

def _po_lines(world):
    """采购单行 = PO 的构成 SO 行分解（sim 中 PO.line_ids 直接指向 so_lines，一单多 SKU 的行即其构成
    SO 行）。po_line_id **复用 so_line_id**：既有 F2 goods_receipt_lines.po_line_id 已写 so_line_id，
    此举让收货行/供票行的 po_line_id 全部指向存在的 po_line（零改动既有表即建立父子引用，不扰动 F2）。
    unit_price=目录价（与 _procurement 供票总额同源）、qty=SO 行量 → 行量之和 = PO 头 qty（generators
    中 PO.qty 本就 =Σ 行 qty，天然一致）。line_status 由该 PO 是否已收货派生。"""
    as_of = world["_as_of"].isoformat()
    received_pos = {gr["po_id"] for gr in world.get("goods_receipts", [])}
    rows = []
    for po_id in sorted(world["pos"]):
        po = world["pos"][po_id]
        status = "received" if po_id in received_pos else "open"
        for lid in po["line_ids"]:
            ln = world["lines"][lid]
            price = round(world["skus"][ln["sku_id"]]["unit_price_usd"], 2)
            rows.append({
                "po_line_id": lid, "po_id": po_id, "sku_id": ln["sku_id"], "qty": ln["qty"],
                "unit_price_usd": price, "currency": "USD",
                "expected_ready_date": po["expected_ready_date"].isoformat(),
                "line_status": status, "as_of_date": as_of,
                "created_at": po["po_date"].isoformat()})
    world["po_lines"] = rows


def _supplier_invoice_lines(world):
    """供应商发票行 = 逐 PO 行对应（发票行金额对得上收货行与 PO 行）。同用目录价×行量，价 2 位小数×
    整数量 → 每行金额精确 2 位、Σ 行金额 = 供票 total_usd（_procurement 中 total 亦以目录价×行量算，逐分对账）。
    po_line_id 指向 _po_lines（=so_line_id）→ 与 goods_receipt_lines 同锚，发票↔收货天然可勾稽。"""
    as_of = world["_as_of"].isoformat()
    rows = []
    for sinv in sorted(world.get("supplier_invoices", []), key=lambda x: x["supplier_invoice_id"]):
        po = world["pos"].get(sinv["po_id"])
        if not po:
            continue
        for lid in po["line_ids"]:
            ln = world["lines"][lid]
            price = round(world["skus"][ln["sku_id"]]["unit_price_usd"], 2)
            rows.append({
                "supplier_invoice_line_id": G._nid(world, "sil", "SIL-SIM", 6),
                "supplier_invoice_id": sinv["supplier_invoice_id"], "po_line_id": lid,
                "qty": ln["qty"], "unit_price_usd": price, "amount_usd": round(ln["qty"] * price, 2),
                "as_of_date": as_of, "created_at": sinv["created_at"]})
    world["supplier_invoice_lines"] = rows


def _po_total(world, po):
    return round(sum(world["lines"][lid]["qty"]
                     * world["skus"][world["lines"][lid]["sku_id"]]["unit_price_usd"]
                     for lid in po["line_ids"]), 2)


def _purchase_payments(world, cfg, rng):
    """采购预付款（PO 定金/尾款）——A9 RecordPurchasePayment / R12 预付款敞口域。**与通用 payments 表
    互补不重叠**：本表锚 PO 里程碑付款（定金先付、尾款收货后付、exposure 敞口），payments 表锚发票结算
    （AP：out/supplier/supplier_invoice）——两者 grain 不同（PO 里程碑 vs 发票结算），本体 0.11.3 仍将
    PurchasePayment 声明为独立对象故按声明补数据（歧义候人确认，见报告，不擅自并表/删表）。exposure_status
    由"是否已收货 + 敞口是否超宽限"派生（不信状态字段，与真实世界 R12 判据同构）。"""
    pp = cfg["enrichment"]["purchase_pay"]
    ratio, grace = pp["deposit_ratio"], pp["overdue_grace_days"]
    as_of = world["_as_of"]
    recv_first = {}                                    # po_id -> 首收货日
    for gr in world.get("goods_receipts", []):
        d = gr["received_date"]
        recv_first[gr["po_id"]] = min(recv_first.get(gr["po_id"], d), d)
    rows = []
    for po_id in sorted(world["pos"]):
        po = world["pos"][po_id]
        if po["po_date"] > as_of:
            continue
        total = _po_total(world, po)
        deposit = round(ratio * total, 2)
        paid = min(po["po_date"] + timedelta(days=rng.randint(0, 3)), as_of)
        rd = recv_first.get(po_id)
        # 敞口判据（因果自洽，与 R12"钱已出、货未到"精神一致）：已收货→released；未收货且齐货日已过
        # grace 天（货逾期未到、定金悬空）→ at_risk（这些 PO 系供应商延误把实收推到 as_of 之后故无收货
        # 单，货真实逾期）；未收货但仍在齐货日前（在途正常）→ covered。不用"定金账龄"作判据（在途 PO 定金
        # 本就新，账龄判据会把逾期货误判为 covered）。
        if rd:
            exposure = "released"
        elif (as_of - po["expected_ready_date"]).days > grace:
            exposure = "at_risk"
        else:
            exposure = "covered"
        rows.append({
            "payment_id": G._nid(world, "ppay", "PPAY-SIM", 6), "po_id": po_id,
            "payment_type": "deposit", "amount_usd": deposit, "paid_date": paid.isoformat(),
            "exposure_status": exposure, "as_of_date": as_of.isoformat(),
            "created_at": paid.isoformat()})
        if rd:                                         # 已收货 PO：尾款于收货后支付（敞口 released）
            bpaid = min(_d(rd) + timedelta(days=rng.randint(1, 7)), as_of)
            rows.append({
                "payment_id": G._nid(world, "ppay", "PPAY-SIM", 6), "po_id": po_id,
                "payment_type": "balance", "amount_usd": round(total - deposit, 2),
                "paid_date": bpaid.isoformat(), "exposure_status": "released",
                "as_of_date": as_of.isoformat(), "created_at": bpaid.isoformat()})
    world["purchase_payments"] = rows


def _inventory_reservations(world, cfg):
    """库存预留：so_line → inventory_position（"待履约需求"记录，语义区别于 shipment_allocation=在途船舶
    对 so_line 的装载分配）。为 allocated 行建 allocated 预留（主场景），另抽样 open/fulfilled 行覆盖 enum
    多样性。position 取该 sku 可用量最大的头寸（确定性，无 rng）。qty=行需求量（不反推 ATP，与真实世界
    "reservation 仅承载待履约需求、ATP 由 position 桶字段直算"同构）。"""
    rc = cfg["enrichment"]["reservation"]
    as_of = world["_as_of"].isoformat()
    best = {}                                          # sku -> (available, position_id)
    for key in sorted(world["inventory"]):
        sku, _wh = key
        p = world["inventory"][key]
        cur = best.get(sku)
        if cur is None or p["available_qty"] > cur[0]:
            best[sku] = (p["available_qty"], p["inventory_position_id"])
    by_status = {"allocated": [], "open": [], "fulfilled": []}
    for lid in sorted(world["lines"]):
        st = world["lines"][lid]["line_status"]
        if st in by_status:
            by_status[st].append(lid)
    rows = []

    def emit(lid, status):
        ln = world["lines"][lid]
        b = best.get(ln["sku_id"])
        if not b:
            return
        rows.append({"reservation_id": G._nid(world, "rsv", "RSV-SIM", 6), "so_line_id": lid,
                     "inventory_position_id": b[1], "qty": ln["qty"], "status": status,
                     "as_of_date": as_of})
    for lid in by_status["allocated"]:
        emit(lid, "allocated")
    for lid in by_status["open"][:rc["open_sample"]]:
        emit(lid, "open")
    for lid in by_status["fulfilled"][:rc["fulfilled_sample"]]:
        emit(lid, "fulfilled")
    world["inventory_reservations"] = rows


def _sourcing(world, cfg, rng):
    """询价-报价链（RFQ→RFQLine→Quote）：抽部分 active SKU 生成比价场景（awarded 历史 + 在建）。
    awarded RFQ：目录 incumbent 落选 / 备源中标 / 可能第三方落选；在建 RFQ：目标仍单源，报价在途。"""
    sc = cfg["enrichment"]["sourcing"]
    as_of = world["_as_of"]
    active = [k for k in sorted(world["skus"]) if world["skus"][k].get("sku_status") == "active"]
    if not active:
        active = sorted(world["skus"])
    n = min(sc["rfq_count"], len(active))
    step = max(1, len(active) // n)
    picks = [active[i * step] for i in range(n)]
    all_sup = sorted(world["suppliers"])
    inflight_rfq = ["sent", "quoting", "evaluating", "draft"]
    inflight_quote = ["invited", "submitted", "shortlisted"]
    rfqs, rfq_lines, quotes = [], [], []
    for i, sku in enumerate(picks):
        incumbent = world["skus"][sku]["supplier_id"]
        base = world["skus"][sku]["unit_price_usd"]
        inflight = rng.random() < sc["inflight_ratio"]
        rid = G._nid(world, "rfq", "RFQ-SIM", 4)
        rfqs.append({"rfq_id": rid, "sku_id": sku,
                     "status": inflight_rfq[i % len(inflight_rfq)] if inflight else "awarded",
                     "created_date": (as_of - timedelta(days=rng.randint(5, 70))).isoformat(),
                     "as_of_date": as_of.isoformat()})
        rfq_lines.append({"rfq_line_id": G._nid(world, "rfql", "RFQL-SIM", 5), "rfq_id": rid,
                          "sku_id": sku, "qty": rng.randint(500, 5000)})
        alt = next((s for s in all_sup if s != incumbent), incumbent)

        def emit_quote(sup, st):
            quotes.append({"quote_id": G._nid(world, "quo", "QUO-SIM", 5), "rfq_id": rid,
                           "supplier_id": sup,
                           "unit_price_usd": round(base * rng.uniform(0.92, 1.08), 2),
                           "currency": "USD", "status": st, "as_of_date": as_of.isoformat()})
        if inflight:
            emit_quote(incumbent, inflight_quote[i % len(inflight_quote)])
            emit_quote(alt, "submitted")
        else:
            emit_quote(incumbent, "rejected")
            emit_quote(alt, "awarded")
            third = next((s for s in all_sup if s not in (incumbent, alt)), None)
            if third and rng.random() < 0.5:
                emit_quote(third, "rejected")
    world["rfqs"], world["rfq_lines"], world["quotes"] = rfqs, rfq_lines, quotes


_HTS_POOL = {"charger": "8504.40.95", "cable": "8544.42.90", "earbuds": "8518.30.20",
             "phone_case": "4202.99.90", "seasonal_gift": "9505.90.60"}


def _compliance_findings(world, cfg, rng):
    """准入合规预审发现（B2 动作产物）：为非 draft 案件生成 finding——HTS 归类（每案必有）+ 品类触发的
    PGA/认证（电池品类→锂电池认证、季节礼品→CPSC 儿童）+ 待补/驳回案件的缺证 finding（风险等级重算依据）。"""
    rows = []

    def add(aid, title, ftype, sev, hts, pga, doc, ev, rec):
        rows.append({"compliance_finding_id": G._nid(world, "cf", "CF-SIM", 5),
                     "admission_case_id": aid, "finding_title": title, "finding_type": ftype,
                     "severity": sev, "hts_candidate": hts, "pga_agency": pga,
                     "required_document": doc, "evidence_status": ev, "recommendation": rec})
    for a in sorted(world.get("admission_cases", []), key=lambda x: x["admission_case_id"]):
        if a["status"] == "draft":
            continue
        aid = a["admission_case_id"]
        cat = world["skus"][a["sku_id"]]["category"]
        add(aid, "HTS 归类确认", "hts", rng.choice(["low", "low", "medium"]),
            _HTS_POOL.get(cat, "9999.00.00"), "none", "", rng.choice(["verified", "provided"]), "accept")
        if cat in ("charger", "earbuds"):
            add(aid, "锂电池运输认证", "certification", rng.choice(["medium", "high"]), "", "none",
                "UN38.3 / MSDS", rng.choice(["provided", "missing"]), "more_docs")
        elif cat == "seasonal_gift":
            add(aid, "CPSC 儿童产品", "pga", rng.choice(["medium", "high"]), "", "CPSC",
                "CPC + ASTM F963", rng.choice(["provided", "missing"]), "more_docs")
        if a["status"] in ("needs_more_info", "rejected"):
            add(aid, "原产地追溯", "origin", "high", "", "CBP", "供应链追溯文件", "missing",
                "reject" if a["status"] == "rejected" else "escalate")
    world["compliance_findings"] = rows


def _logistics_plans(world, cfg, rng):
    """物流方案（B3 动作产物）：为达 plan_ready 及以上（priced_statuses）案件生成方案。logistics_plan_id
    = LPLAN-SIM-{案件后 5 位} —— 与 _admission 已产 cost_scenarios 的外键完全对齐（既有 cost_scenarios 的
    logistics_plan_id 从此指向存在的方案，零改动既有表即补齐父子引用）。incoterm 沿用案件候选（DDP 且客户
    无 IOR 则降 DAP，世界一致性）。"""
    priced = set(cfg["enrichment"]["admission"]["priced_statuses"])
    routes = ["ocean_fcl", "ocean_fcl", "ocean_lcl", "air_freight", "express"]
    transit = {"express": (5, 8), "air_freight": (8, 12), "ocean_fcl": (28, 38),
               "ocean_lcl": (32, 45), "warehouse_fulfillment": (30, 40)}
    rows = []
    for a in sorted(world.get("admission_cases", []), key=lambda x: x["admission_case_id"]):
        if a["status"] not in priced:
            continue
        itc = a["incoterm_candidate"]
        incoterm = "FOB" if itc == "tbd" else itc
        if incoterm == "DDP" and world["customers"][a["customer_id"]].get("ior_capability") != "has_ior":
            incoterm = "DAP"
        route = rng.choice(routes)
        rows.append({
            "logistics_plan_id": f"LPLAN-SIM-{a['admission_case_id'][-5:]}",
            "admission_case_id": a["admission_case_id"], "plan_name": f"{route}/{incoterm} 方案",
            "route_type": route, "incoterm": incoterm,
            "origin_port_locode": rng.choice(["CNYTN", "CNSHK", "CNNGB"]),
            "destination_port_locode": rng.choice(["USLAX", "USLGB"]),
            "us_warehouse_region": rng.choice(["west", "west", "central", "platform"]),
            "last_mile_method": rng.choice(["UPS", "FedEx", "USPS", "LTL", "platform"]),
            "estimated_transit_days": rng.randint(*transit[route]),
            "sla_risk": rng.choice(["low", "low", "medium", "high"]), "operational_notes": ""})
    world["logistics_plans"] = rows


def _expected_costs(world, cfg):
    """基准成本（R4-R6 费用规则的比对基准）——**按票展开、非静态表**（与真实世界 datagen 同构：逐 shipment
    从费率卡展开 OFT/THC/FSC 每柜一行 + DOC/CUS 每票一行）。baseline=纯费率卡（base×route_mult，不含货代
    quote_level/涨价/异常注入）→ 干净发票行对得上、被注入泡重/超收的异常发票行偏离基准 → R4-R6 可比对。
    source='rate_card'；ISF 不在 ExpectedCost 枚举故不展开（票级只展 DOC/CUS）。"""
    rc = cfg["rate_card"]
    rows = []
    for sid in sorted(world["shipments"]):
        ship = world["shipments"][sid]
        route = ship["route_id"]
        oft = rc["OFT"]["base"] * rc["OFT"].get("route_mult", {}).get(route, 1.0)
        for cno in ship["containers"]:
            for code, val in (("OFT", oft), ("THC", rc["THC"]["base"]), ("FSC", rc["FSC"]["base"])):
                rows.append({"expected_cost_id": G._nid(world, "ec", "EC-SIM", 6), "shipment_id": sid,
                             "charge_code": code, "container_no": cno,
                             "baseline_usd": round(val, 2), "source": "rate_card"})
        for code in ("DOC", "CUS"):
            rows.append({"expected_cost_id": G._nid(world, "ec", "EC-SIM", 6), "shipment_id": sid,
                         "charge_code": code, "container_no": "",
                         "baseline_usd": round(rc[code]["base"], 2), "source": "rate_card"})
    world["expected_costs"] = rows


def _supplier_qualifications(world, cfg, rng):
    """供应商资质（R13 资质过期规则的载体）：每供应商 iso9001（有效基础证）+ factory_audit。qual_expiring
    特质供应商产临期/已过期样本（支撑 R13 未来扩展——数据真实存在即可，不强制现在触发检测），其余供应商
    产有效证。检测应由 valid_to vs as_of 重算（as-of 安全），此处只保证样本存在。"""
    as_of = world["_as_of"]
    rows = []

    def emit(sid, cert, vfrom, vto, status, ev="verified"):
        rows.append({"qualification_id": G._nid(world, "qual", "QUAL-SIM", 5), "supplier_id": sid,
                     "cert_type": cert, "evidence_status": ev, "valid_from": vfrom.isoformat(),
                     "valid_to": vto.isoformat(), "status": status, "as_of_date": as_of.isoformat(),
                     "created_at": vfrom.isoformat()})
    for sid in sorted(world["suppliers"]):
        emit(sid, "iso9001", as_of - timedelta(days=200), as_of + timedelta(days=300), "valid")
        if world["suppliers"][sid].get("qual_expiring"):
            # 资质将过期供应商：同时给一张已过期(factory_audit) + 一张临期(product_safety_cert)——保证
            # expired/expiring 两类样本都存在（不依赖 qual_expiring 供应商数量，当前配置仅 1 家亦覆盖两态）。
            emit(sid, "factory_audit", as_of - timedelta(days=400),
                 as_of - timedelta(days=rng.randint(5, 40)), "expired", "provided")
            emit(sid, "product_safety_cert", as_of - timedelta(days=200),
                 as_of + timedelta(days=rng.randint(10, 40)), "expiring")
        else:
            emit(sid, "factory_audit", as_of - timedelta(days=100),
                 as_of + timedelta(days=rng.randint(200, 340)), "valid")
    world["supplier_qualifications"] = rows


_COORD_MAP = {
    "delay_breach": ("forwarder", "改配船期舱位确认"), "supplier_delay": ("supplier", "工厂确认改期后交期"),
    "short_receipt": ("supplier", "短装补发确认"), "qc_failure": ("supplier", "质量整改与返工确认"),
    "docs_missing": ("customs_broker", "补齐清关单证"), "stalled": ("forwarder", "在途停滞原因核查"),
    "rate_overbilling": ("forwarder", "费率超收争议核对"), "duplicate_charge": ("forwarder", "重复计费冲销确认"),
    "unplanned_charge": ("forwarder", "计划外附加费依据核对"), "overdue_receivable": ("customer", "逾期应收回款催办"),
    "payment_anomaly": ("bank", "重复付款追回核对"), "prepayment_exposure": ("bank", "预付款敞口对账"),
    "stockout": ("supplier", "紧急补货交期确认"),
}
_COORD_OWNER = {"forwarder": "u-ops-sim", "supplier": "u-ops-sim", "customs_broker": "u-ops-sim",
                "customer": "u-cs-sim", "bank": "u-finance-sim"}
_COORD_REF = {"supplier": "SUP", "forwarder": "FWD·货代", "customs_broker": "BRK·报关行",
              "customer": "CUS·客户采购", "bank": "BANK·银行"}


def _coordination_threads(world, cfg, rng):
    """CL1 协调回路线程（跨角色协作快照）——已交付但 sim 世界零实例，导致驾驶舱/透视镜演示不出该能力。
    锚到部分 sim task+其 risk，按风险类型映射对手方与诉求；state 混合 awaiting/responded/escalated/resolved，
    next_action_due 相对 as_of（部分逾期展示升级态）。policy_version 标 sim-coordination-v1、id 带 -SIM-。"""
    cc = cfg["enrichment"]["coordination"]
    as_of = world["_as_of"]
    tasks = [world["tasks"][k] for k in sorted(world.get("tasks", {}))]
    risks = world.get("risk_events", {})
    cand = [t for t in tasks if risks.get(t["risk_event_id"], {}).get("type") in _COORD_MAP]
    n = min(cc["thread_count"], len(cand))
    step = max(1, len(cand) // n) if n else 1
    picks = [cand[i * step] for i in range(n)]
    states = ["awaiting", "awaiting", "responded", "escalated", "resolved"]
    rows = []
    for i, t in enumerate(picks):
        re = risks[t["risk_event_id"]]
        cp_type, ask = _COORD_MAP[re["type"]]
        ref = (f"SUP·{re.get('supplier_id') or '工厂'}" if cp_type == "supplier"
               else _COORD_REF[cp_type])
        state = states[i % len(states)]
        opened = as_of - timedelta(days=rng.randint(1, 20))
        rows.append({
            "coordination_id": G._nid(world, "coord", "COORD-SIM", 5), "task_id": t["task_id"],
            "risk_event_id": t["risk_event_id"], "counterparty_type": cp_type,
            "counterparty_ref": ref, "ask": ask, "state": state,
            "followup_count": rng.randint(0, 3), "escalation_level": 1 if state == "escalated" else 0,
            "owner": _COORD_OWNER[cp_type],
            "next_action_due": (as_of + timedelta(days=rng.choice([-3, -2, -1, 2, 3, 5]))).isoformat(),
            "last_response": "对方已回复，处理中" if state in ("responded", "resolved") else "",
            "outcome": "已达成一致、闭环" if state == "resolved" else "",
            "opened_at": opened.isoformat(), "last_update": as_of.isoformat(),
            "policy_version": "sim-coordination-v1"})
    world["coordination_threads"] = rows


# ═══════════════════════════════════════ 主入口 ═══════════════════════════════════════
def enrich(world, cfg, streams):
    """回填后补灌全域（F2：采购→准入→盘点→资金流→资金流 AI 今日流；C：12 类零实例对象补全）。各域独立子流。"""
    _procurement(world, cfg, streams["procurement"])
    _admission(world, cfg, streams["admission"])
    _cycle_counts(world)
    _finance(world, cfg, streams["finance"])
    _finance_ai(world, cfg, streams["finance"])
    # C 补全域（12 类零实例对象；派生自上面已建世界，独立命名子流，不消费/扰动既有 rng 流）
    _po_lines(world)                                    # PO 行（复用 so_line_id → 供票行/收货行同锚）
    _supplier_invoice_lines(world)                      # 供票行（金额勾稽收货行）
    _purchase_payments(world, cfg, streams["purchase_pay"])   # 采购预付款（R12 敞口，独立于 payments）
    _inventory_reservations(world, cfg)                # 库存预留（区别于 shipment_allocation）
    _sourcing(world, cfg, streams["sourcing"])         # RFQ→Quote 询价比价链
    _compliance_findings(world, cfg, streams["compliance"])   # 合规预审发现（B2 产物）
    _logistics_plans(world, cfg, streams["logistics"])       # 物流方案（B3 产物，对齐 cost_scenarios 外键）
    _expected_costs(world, cfg)                        # 基准成本（R4-R6 比对基准，逐票展开）
    _supplier_qualifications(world, cfg, streams["qualification"])  # 供应商资质（R13 载体）
    _coordination_threads(world, cfg, streams["coordination"])     # CL1 协调回路线程
