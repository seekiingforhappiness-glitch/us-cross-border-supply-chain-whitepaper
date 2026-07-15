"""W6 确定性风险简报引擎：不依赖 LLM，从 ontology 生成有据可查的解释与建议。

设计立场：解释与推荐的**事实部分**必须是确定性计算（每个数字有出处），
LLM（若接入）只负责把简报转述成自然对话——这保证"AI 回答全部可溯源到对象 ID"
不靠模型自觉，而靠架构。
"""
import json
from datetime import date, timedelta


def build_risk_briefing(session, risk_event_id):
    """返回 {summary, facts, recommendations, citations}；risk 不存在则返回 error。

    本简报是**货运中心**视图。非货运锚定的风险（采购/仓储/资金类，RiskEvent.shipment_id 可空）
    没有货运上下文，此时优雅降级为提示（原直接 `ctx["shipment"]` 会 KeyError 崩溃）——这条路径
    经由 Task 富工作台可达：任务台列全部任务，选中一个父风险为采购/仓储的任务再看简报即触发。
    """
    risk = session.get_risk(risk_event_id)
    if "error" in risk:
        return risk
    impact = session.get_impact_chain(risk_event_id)
    ctx = session.get_shipment_context(risk["shipment_id"])
    if "error" in ctx:  # shipment_id 为空 / 悬空 FK：无货运上下文，友好降级不崩溃
        return {"error": f"风险 {risk_event_id}（规则 {risk['rule_id']}、类型 {risk['type']}）"
                         "未锚定有效货运，无法生成货运中心简报；"
                         "请到对应对象工作台（采购单 / 仓库 / 发票）查看其处置。"}
    sp = ctx["shipment"]
    citations = {risk_event_id, risk["shipment_id"]} \
        | {a["so_line_id"] for a in impact.get("affected", [])} \
        | {a["so_id"] for a in impact.get("affected", [])} \
        | {a["customer_id"] for a in impact.get("affected", [])}

    facts = {
        "risk": {"id": risk_event_id, "rule": risk["rule_id"], "type": risk["type"],
                 "severity": risk["severity"], "status": risk["status"],
                 "root_cause": risk["root_cause"],
                 "affected_value_usd": risk["affected_value_usd"]},
        "shipment": {"id": sp["shipment_id"], "status": sp["status"],
                     "eta_initial": sp["eta_initial"], "eta_current": sp["eta_current"],
                     "delay_days": sp["delay_days"], "route":
                     f"{sp['origin_port_locode']}→{sp['destination_port_locode']}",
                     "missing_docs": sp["missing_docs"], "incoterm": sp["incoterm"]},
        "affected_lines": impact.get("affected", []),
    }

    # ---------- 建议（确定性规则，proposal-only） ----------
    recs = []
    as_of = date.fromisoformat(session.as_of)
    if risk["rule_id"] == "R1":
        eta_ready = date.fromisoformat(sp["eta_current"]) + timedelta(days=session.buf)
        safe_promise = (eta_ready + timedelta(days=1)).isoformat()
        if risk["severity"] in ("critical", "high"):
            recs.append({"action": "expedite",
                         "rationale": f"级别 {risk['severity']}、影响金额 ${risk['affected_value_usd']}，"
                                      "空运补货可保住原承诺日；代价是运费成本，需经理权衡",
                         "params_hint": {"new_mode": "air", "expected_new_eta": None,
                                         "est_cost_usd": None}})
        recs.append({"action": "reschedule",
                     "rationale": f"当前 ETA {sp['eta_current']} + {session.buf} 天缓冲 = "
                                  f"{eta_ready.isoformat()} 可达仓，改期到 {safe_promise} 及以后即安全；"
                                  "需客户成功与客户确认",
                     "params_hint": {"new_promise_date": safe_promise, "notify_customer": True}})
        if risk["severity"] == "medium":
            recs.append({"action": "accept_delay",
                         "rationale": "击穿幅度小且级别 medium，若客户可容忍可接受延误并记录",
                         "params_hint": {"reason": "minor breach accepted after customer check"}})
    elif risk["rule_id"] == "R2":
        recs.append({"action": "assign_task",
                     "rationale": f"缺失文件 [{sp['missing_docs']}]，ETA {sp['eta_current']} 临近，"
                                  "应立即派单跟进补件（v0.2 无补件专用动作，用任务承载）",
                     "params_hint": {"assignee_role": "ops", "priority": "P1"}})
    elif risk["rule_id"] == "R3":
        recs.append({"action": "assign_task",
                     "rationale": "船舶在途静默，应派单向承运商/货代核实位置与状态，"
                                  "排除数据断流 vs 真实异常",
                     "params_hint": {"assignee_role": "ops", "priority": "P2"}})

    n_cust = len({a["customer_id"] for a in impact.get("affected", [])})
    summary = (f"[{risk_event_id}] {risk['severity']} 级 {risk['type']}（规则 {risk['rule_id']}）："
               f"货运 {sp['shipment_id']}（{facts['shipment']['route']}）"
               f"ETA 由 {sp['eta_initial']} 变为 {sp['eta_current']}（延误 {sp['delay_days']} 天），"
               f"根因：{risk['root_cause']}。影响 {len(impact.get('affected', []))} 个订单行、"
               f"{n_cust} 个客户，金额 ${risk['affected_value_usd']}。当前状态 {risk['status']}。"
               f"以下建议均需人工确认执行（proposal-only）。")
    return {"summary": summary, "facts": facts, "recommendations": recs,
            "citations": sorted(citations)}


def render_briefing_text(briefing):
    """把简报渲染为纯文本（scripted 评估与 UI 展示共用）。"""
    if "error" in briefing:
        return briefing["error"]
    out = [briefing["summary"], "", "受影响明细："]
    for a in briefing["facts"]["affected_lines"]:
        out.append(f"- {a['so_line_id']}（订单 {a['so_id']}，客户 {a['customer_id']} "
                   f"{a['customer_name']}）：{a['qty']} 件，承诺日 {a['promised_delivery_date']}，"
                   f"行状态 {a['line_status']}")
    out.append("")
    out.append("处置建议（需人工审批）：")
    for i, r in enumerate(briefing["recommendations"], 1):
        out.append(f"{i}. {r['action']} — {r['rationale']}")
    out.append("")
    out.append("数据出处对象: " + ", ".join(briefing["citations"]))
    return "\n".join(out)


def build_invoice_briefing(session, invoice_id):
    """确定性发票对账简报（Invoice 对象工作台的无 key fallback）：逐行 amount vs baseline 差异、
    异常标记、关联费用风险，并给出 dispute 提案草案（proposal-only）。

    红线：① 对账金额随 role 脱敏（与 get_invoice_context / UI mask_cost 同口径，收敛后一致）——
    成本不可见角色（如 ops）拿到掩码值时不做数值汇总，如实以掩码呈现，不臆测金额；
    ② rebill_customer 是否放行由 G4 incoterm 责任门禁在**审批时**判定，AI 只提示不决策不审批；
    ③ needs_human_approval 恒 true——AI 永远不是审批入口（原则2）。"""
    from app.actions import REBILL_MATRIX  # 局部 import 避免 agent↔app 环形依赖
    ctx = session.get_invoice_context(invoice_id)
    if "error" in ctx:
        return ctx
    inv, lines, ship = ctx["invoice"], ctx["lines"], ctx.get("shipment")
    anomaly_lines = [ln for ln in lines if ln.get("is_anomaly")]
    # 成本掩码角色下 diff_usd 是掩码串——只对真数值做汇总（掩码值不参与、不臆测金额）
    over_lines = [ln for ln in lines
                  if isinstance(ln.get("diff_usd"), (int, float)) and ln["diff_usd"] > 0]
    total_over = round(sum(ln["diff_usd"] for ln in over_lines), 2)
    disputed_amount = round(sum(ln["diff_usd"] for ln in anomaly_lines
                                if isinstance(ln.get("diff_usd"), (int, float))), 2)
    citations = {invoice_id, inv["shipment_id"]} | {ln["invoice_line_id"] for ln in lines}
    # 关联风险：本票 shipment 上 flag 了本发票账单行的费用类风险（R4/R5/R6）
    line_ids = {ln["invoice_line_id"] for ln in lines}
    related = []
    for rr in session._rows("""SELECT risk_event_id, rule_id, type, severity, status,
                               affected_invoice_line_ids FROM risk_events WHERE shipment_id=?
                               ORDER BY risk_event_id""", inv["shipment_id"]):
        ils = json.loads(rr["affected_invoice_line_ids"]) if rr["affected_invoice_line_ids"] else []
        if line_ids & set(ils):
            related.append(rr)
            citations.add(rr["risk_event_id"])
    incoterm = ship["incoterm"] if ship else None
    rebillable = REBILL_MATRIX.get(incoterm, set()) if incoterm else set()

    recs = []
    if anomaly_lines:
        recs.append({"action": "dispute",
                     "rationale": f"账单行 {[ln['invoice_line_id'] for ln in anomaly_lines]} 被关联风险标记异常，"
                                  f"超基准合计 ${total_over}；建议对 vendor {inv['vendor_name']} 发起 dispute。"
                                  "须运营/财务经 propose_mitigation 提案，最终人工审批（AI 不审批）",
                     "params_hint": {"reason": "line(s) flagged anomalous vs expected baseline",
                                     "disputed_amount_usd": disputed_amount or total_over}})
    if incoterm and rebillable:
        recs.append({"action": "rebill_customer",
                     "rationale": f"该票 incoterm={incoterm} 下可转嫁费种={sorted(rebillable)}；"
                                  "是否放行由 G4 责任门禁在审批时判定，AI 不做该决策也不审批",
                     "params_hint": {"incoterm_basis": incoterm}})
    elif incoterm:
        recs.append({"action": "note",
                     "rationale": f"该票 incoterm={incoterm}，G4 责任矩阵下无可转嫁费种，"
                                  "rebill_customer 审批时会被拒；宜 dispute 或 accept_charge",
                     "params_hint": {}})

    summary = (f"[{invoice_id}] 发票对账简报：vendor {inv['vendor_name']}（{inv['vendor_type']}），"
               f"货运 {inv['shipment_id']}，状态 {inv['status']}，金额 ${inv['total_usd']} {inv['currency']}。"
               f"{len(lines)} 个账单行，其中 {len(anomaly_lines)} 行被关联风险标记异常、"
               f"{len(over_lines)} 行超基准（超支合计 ${total_over}）。"
               "以下均为提案/提示，须人工审批执行（proposal-only）。")
    return {"summary": summary,
            "invoice": {"id": invoice_id, "vendor": inv["vendor_name"], "status": inv["status"],
                        "total_usd": inv["total_usd"], "currency": inv["currency"],
                        "shipment_id": inv["shipment_id"], "incoterm": incoterm},
            "lines": lines, "anomaly_line_ids": [ln["invoice_line_id"] for ln in anomaly_lines],
            "related_risk_ids": [r["risk_event_id"] for r in related],
            "recommendations": recs, "needs_human_approval": True,
            "citations": sorted(citations)}


def render_invoice_briefing_text(b):
    """把发票对账简报渲染为纯文本（scripted 评估与 UI 展示共用）。"""
    if "error" in b:
        return b["error"]
    out = [b["summary"], "", "账单行差异（amount vs baseline）："]
    for ln in b["lines"]:
        base = ln["baseline_usd"] if ln.get("baseline_usd") is not None else "无基准"
        flag = " ⚠异常" if ln.get("is_anomaly") else ""
        out.append(f"- {ln['invoice_line_id']} {ln['charge_code']} 柜{ln.get('container_no') or '-'}："
                   f"${ln['amount_usd']} vs 基准 {base}，差异 {ln.get('diff_usd')}{flag}")
    if b["related_risk_ids"]:
        out.append("")
        out.append("关联费用风险：" + ", ".join(b["related_risk_ids"]))
    out.append("")
    out.append("处置建议 / 提示（须人工审批，AI 只提案不审批）：")
    for i, r in enumerate(b["recommendations"], 1):
        out.append(f"{i}. {r['action']} — {r['rationale']}")
    out.append("")
    out.append("needs_human_approval: true（AI 不是审批入口）")
    out.append("数据出处对象: " + ", ".join(b["citations"]))
    return "\n".join(out)
