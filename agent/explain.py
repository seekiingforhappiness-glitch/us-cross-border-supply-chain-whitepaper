"""W6 确定性风险简报引擎：不依赖 LLM，从 ontology 生成有据可查的解释与建议。

设计立场：解释与推荐的**事实部分**必须是确定性计算（每个数字有出处），
LLM（若接入）只负责把简报转述成自然对话——这保证"AI 回答全部可溯源到对象 ID"
不靠模型自觉，而靠架构。
"""
from datetime import date, timedelta


def build_risk_briefing(session, risk_event_id):
    """返回 {summary, facts, recommendations, citations}；risk 不存在则返回 error。"""
    risk = session.get_risk(risk_event_id)
    if "error" in risk:
        return risk
    impact = session.get_impact_chain(risk_event_id)
    ctx = session.get_shipment_context(risk["shipment_id"])
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
