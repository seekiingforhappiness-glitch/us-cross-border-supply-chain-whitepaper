"""V4 准入简报引擎：按 v0.1 手册 §7 输出 schema 确定性生成，不依赖 LLM。

红线（v0.1 §7 禁止行为）在此由架构兑现：
- hts_candidates 只来自 findings，没有就是空列表——不编造 HTS（AD3）
- 成本字段对 AI 角色脱敏时，定价风险如实声明"不可见"，不把 null 当无风险（AD4）
- needs_human_approval 恒为 true——AI 永远不是审批入口
"""

SEV_RANK = {"": -1, "low": 0, "medium": 1, "high": 2, "critical": 3}


def build_admission_briefing(session, admission_case_id):
    ctx = session.get_admission_context(admission_case_id)
    if "error" in ctx:
        return ctx
    case, cust = ctx["case"], ctx["customer"]
    finds, plans, scens = ctx["findings"], ctx["plans"], ctx["cost_scenarios"]
    citations = {admission_case_id, case["sku_id"], cust["customer_id"]} \
        | {f["compliance_finding_id"] for f in finds} \
        | {p["logistics_plan_id"] for p in plans}

    risk_level = case["risk_level"] or ("unknown" if not finds else max(
        (f["severity"] for f in finds), key=lambda s: SEV_RANK[s]))
    missing_documents = sorted({f["required_document"] or f["finding_type"]
                                for f in finds if f["evidence_status"] in ("missing", "rejected")})
    hts_candidates = sorted({f["hts_candidate"] for f in finds if f["hts_candidate"]})

    # 推荐路线：已有方案取 SLA 风险最低者；无方案按月单量启发式（注明是启发式）
    if plans:
        best = min(plans, key=lambda p: {"low": 0, "medium": 1, "high": 2}[p["sla_risk"]])
        recommended_route = (f"{best['route_type']}/{best['incoterm']}"
                             f"（既有方案 {best['logistics_plan_id']}，SLA 风险 {best['sla_risk']}）")
    else:
        route = "ocean_fcl" if case["monthly_order_estimate"] >= 1000 else "ocean_lcl"
        recommended_route = f"{route}（启发式：月单量 {case['monthly_order_estimate']}，待运营确认）"

    pricing_risks = []
    if any(f["severity"] == "critical" and f["evidence_status"] != "verified" for f in finds):
        pricing_risks.append("存在未核实的 critical 合规发现，审批门禁 G1 将拒绝（先补证据）")
    if case["incoterm_candidate"] == "DDP" and cust["ior_capability"] != "has_ior":
        pricing_risks.append(f"意向 DDP 但客户 IOR 能力={cust['ior_capability']}，"
                             "门禁 G2 将拦截——建议改 DAP 或客户补 IOR 资质")
    if not hts_candidates:
        pricing_risks.append("预审未提供 HTS 候选，关税成本无法测算（不做臆测）")
    if scens:
        pricing_risks.append(f"成本与毛利字段对当前角色（{type(session).__name__}/ops）不可见，"
                             "定价风险需财务/经理评估——不可见≠无风险")
    conf = 0.85 if hts_candidates and any(f["evidence_status"] == "verified" for f in finds) \
        else (0.6 if finds else 0.3)

    return {"admission_case_id": admission_case_id,
            "risk_level": risk_level,
            "missing_documents": missing_documents,
            "hts_candidates": hts_candidates,
            "recommended_route": recommended_route,
            "pricing_risks": pricing_risks,
            "needs_human_approval": True,
            "confidence": conf,
            "citations": sorted(citations)}


def render_admission_briefing(b):
    if "error" in b:
        return b["error"] + "，不存在"
    lines = [f"[{b['admission_case_id']}] 准入简报（AI 建议，最终决定须人工审批）",
             f"风险等级: {b['risk_level']} | 置信度: {b['confidence']}",
             "HTS 候选: " + (", ".join(b["hts_candidates"]) if b["hts_candidates"]
                             else "预审未提供 HTS 候选，不做臆测"),
             "缺失文件: " + (", ".join(b["missing_documents"]) or "无"),
             f"推荐路线: {b['recommended_route']}"]
    if b["pricing_risks"]:
        lines.append("定价/审批风险:")
        lines += [f"  - {r}" for r in b["pricing_risks"]]
    lines.append("needs_human_approval: true（AI 不是审批入口）")
    lines.append("数据出处对象: " + ", ".join(b["citations"]))
    return "\n".join(lines)
