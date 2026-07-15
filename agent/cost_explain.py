"""X4 费用简报引擎（确定性）：从 ontology 生成费用异常的有据可查解释与处置建议。

设计立场与 explain.py 一致：事实部分全部确定性计算（每个数字有对象 ID 出处），
LLM（若接入）只负责转述。费用场景的三条红线由架构兑现：
- F4 归因叙事（XD2）：DET/DEM + 延误 → 人话点明"这笔滞箱费源于 <eta_change 日> 的 ETA 延误（延误 N 天）"
- rebill 建议先对照 app.actions.REBILL_MATRIX（直接复用 G4 门禁矩阵），DDP 明确"不可转嫁"
- needs_human_approval 恒 true——AI 永远不是审批入口（proposal-only）

为什么单独一支：费用类风险（R4/R5/R6）的事实结构（账单行/费种/柜/发票/vendor）
与控制塔风险（订单行/客户）不同，build_risk_briefing 的 impact_chain 对费用为空，
故按类型分流；非费用类风险显式拒绝并指回 build_risk_briefing。
"""
import json

from app.actions import REBILL_MATRIX

COST_TYPES = {"rate_overbilling", "duplicate_charge", "unplanned_charge"}
# R6 计划外费种（与 engine.cost_rules.UNPLANNED_CODES 同步）；滞箱/滞港属延误可归因费种
DET_CODES = {"DET", "DEM"}


def _latest_eta_change_date(session, shipment_id):
    """取该船最近一次 eta_change 事件的日期部分（F4 归因用）；无则 None。"""
    rows = session._rows(
        """SELECT event_time FROM shipment_milestones
           WHERE shipment_id=? AND event_type='eta_change'
           ORDER BY event_time DESC LIMIT 1""", shipment_id)
    return rows[0]["event_time"][:10] if rows else None


def build_cost_briefing(session, risk_event_id):
    """返回 {summary, attribution, facts, recommendations, needs_human_approval, citations}。
    非费用类风险返回 {error: ...} 提示改用 build_risk_briefing。"""
    risk = session.get_risk(risk_event_id)
    if "error" in risk:
        return risk
    if risk["type"] not in COST_TYPES:
        return {"error": f"{risk_event_id} 是 {risk['type']} 类风险，非费用异常，"
                         "请改用 build_risk_briefing（控制塔风险简报）"}

    shipment_id = risk["shipment_id"]
    inv_lids = json.loads(risk["affected_invoice_line_ids"] or "[]")

    # 受影响账单行明细（费种/柜/金额/所属发票/vendor + 基准/差异）
    lines = []
    if inv_lids:
        ph = ",".join("?" * len(inv_lids))
        lines = session._rows(
            f"""SELECT il.invoice_line_id, il.charge_code, il.container_no, il.amount_usd,
                       il.invoice_id, iv.vendor_name, iv.vendor_type,
                       ec.baseline_usd
                FROM invoice_lines il
                JOIN invoices iv ON iv.invoice_id=il.invoice_id
                LEFT JOIN expected_costs ec
                  ON ec.shipment_id=iv.shipment_id AND ec.charge_code=il.charge_code
                  AND ec.container_no=COALESCE(il.container_no,'')
                WHERE il.invoice_line_id IN ({ph}) ORDER BY il.invoice_line_id""",
            *inv_lids)
        for ln in lines:
            ln["diff_usd"] = (round(ln["amount_usd"] - ln["baseline_usd"], 2)
                              if ln["baseline_usd"] is not None else None)

    sp = session._rows("""SELECT shipment_id, incoterm, delay_days, status
                          FROM shipments WHERE shipment_id=?""", shipment_id)
    ship = sp[0] if sp else {"shipment_id": shipment_id, "incoterm": "", "delay_days": 0}
    incoterm = ship.get("incoterm") or ""
    delay_days = ship.get("delay_days") or 0

    citations = {risk_event_id, shipment_id}
    citations |= {ln["invoice_line_id"] for ln in lines}
    citations |= {ln["invoice_id"] for ln in lines}

    facts = {
        "risk": {"id": risk_event_id, "rule": risk["rule_id"], "type": risk["type"],
                 "severity": risk["severity"], "status": risk["status"],
                 "root_cause": risk["root_cause"],
                 "affected_value_usd": risk["affected_value_usd"]},
        "shipment": {"id": shipment_id, "incoterm": incoterm,
                     "delay_days": delay_days, "status": ship.get("status")},
        "affected_lines": lines,
    }

    # ---------- F4 归因叙事（XD2 核心）----------
    # 仅当 R6 且账单行含滞箱/滞港费种且延误>0 时生成人话归因，锚定最近一次 eta_change 日期。
    attribution = None
    has_det = any(ln["charge_code"] in DET_CODES for ln in lines)
    if risk["rule_id"] == "R6" and has_det and delay_days > 0:
        eta_date = _latest_eta_change_date(session, shipment_id)
        if eta_date:
            attribution = (f"这笔滞箱费源于 {eta_date} 的 ETA 延误（延误 {delay_days} 天）："
                           f"箱子超出免箱期滞留，承运商据此计收 DET/DEM。")
        else:
            attribution = (f"这笔滞箱费源于本票的 ETA 延误（延误 {delay_days} 天）："
                           f"箱子超出免箱期滞留，承运商据此计收 DET/DEM。")

    # ---------- 处置建议（确定性规则，proposal-only）----------
    recs = []
    val = risk["affected_value_usd"]
    if risk["rule_id"] in ("R4", "R5"):
        # 超收/重复 → 争议追回。理由带基准对比（超收行的 baseline vs amount）。
        detail = _dispute_rationale(risk["rule_id"], lines)
        recs.append({"action": "dispute",
                     "rationale": f"{detail} 建议对异常金额 ${val} 发起 dispute 争议追回，"
                                  "批准后发票转 disputed 进入争议流程；须经理审批。",
                     "params_hint": {"disputed_amount_usd": val,
                                     "reason": detail}})
    elif risk["rule_id"] == "R6":
        # R6 计划外费用多为合法费用（如延误产生的滞箱费）：先判可否转嫁客户。
        affected_codes = {ln["charge_code"] for ln in lines}
        allowed = REBILL_MATRIX.get(incoterm, set())
        overflow = affected_codes - allowed
        if not affected_codes:
            overflow = {"?"}
        if overflow:
            # 不可转嫁：DDP 门到门全我方，或费种超出该 incoterm 可转嫁集
            if incoterm == "DDP":
                recs.append({"action": "accept_charge",
                             "rationale": f"按 DDP 条款须我方承担，不可转嫁客户"
                                          f"（G4 矩阵 DDP→∅）；费种 {sorted(affected_codes)} "
                                          f"为合法计划外费用 ${val}，建议 accept_charge 照付并归档。",
                             "params_hint": {"reason": f"DDP 门到门责任，{sorted(affected_codes)} "
                                             f"由我方承担；金额 ${val}"}})
            else:
                recs.append({"action": "accept_charge",
                             "rationale": f"费种 {sorted(overflow)} 不在 {incoterm} 可转嫁集合内"
                                          f"（G4 矩阵），不可转嫁客户；建议 accept_charge 照付。",
                             "params_hint": {"reason": f"{incoterm} 下 {sorted(overflow)} 不可转嫁"}})
        else:
            recs.append({"action": "rebill_customer",
                         "rationale": f"依据 G4 矩阵，{incoterm} 下费种 {sorted(affected_codes)} "
                                      f"属买方责任，可转嫁客户；建议 rebill 金额 ${val}，"
                                      "批准后发票转 approved（我方先付、向客户开票追收）；须经理审批。",
                         "params_hint": {"rebill_amount_usd": val,
                                         "incoterm_basis": incoterm}})

    n_lines = len(lines)
    summary = (f"[{risk_event_id}] {risk['severity']} 级 {risk['type']}（规则 {risk['rule_id']}）："
               f"发票对账在货运 {shipment_id}（incoterm {incoterm}，延误 {delay_days} 天）上"
               f"发现 {n_lines} 条异常账单行，异常金额 ${val}。"
               f"根因：{risk['root_cause']}。以下建议均需人工审批执行（proposal-only）。")

    return {"summary": summary, "attribution": attribution, "facts": facts,
            "recommendations": recs, "needs_human_approval": True,
            "citations": sorted(citations)}


def _dispute_rationale(rule_id, lines):
    """构造带基准对比的争议理由。"""
    if rule_id == "R4":
        over = [ln for ln in lines if ln.get("diff_usd") and ln["diff_usd"] > 0]
        if over:
            ex = over[0]
            return (f"费率超收：如账单行 {ex['invoice_line_id']}（{ex['charge_code']}）"
                    f"实收 ${ex['amount_usd']} vs 基准 ${ex['baseline_usd']}，"
                    f"超收 ${ex['diff_usd']}。")
        return "费率超收：账单金额高于费率卡基准。"
    return "重复计费：同费种同柜在账单中出现多次，首行外均属重复扣款。"


def render_cost_briefing(b):
    """把费用简报渲染为纯文本（scripted 评估与 UI 展示共用），格式仿 render_admission_briefing。"""
    if "error" in b:
        return b["error"]
    lines = [b["summary"], ""]
    if b["attribution"]:
        lines += ["归因（跨场景）:", f"  {b['attribution']}", ""]
    lines.append("受影响账单行:")
    for x in b["facts"]["affected_lines"]:
        base = (f"基准 ${x['baseline_usd']}" if x["baseline_usd"] is not None else "无基准")
        diff = (f"，差异 ${x['diff_usd']}" if x.get("diff_usd") is not None else "")
        lines.append(f"  - {x['invoice_line_id']}（{x['charge_code']}"
                     f"{('/' + x['container_no']) if x['container_no'] else ''}）："
                     f"${x['amount_usd']}（{base}{diff}），"
                     f"发票 {x['invoice_id']} / {x['vendor_name']}　⚠️异常")
    lines.append("")
    lines.append("处置建议（需人工审批）:")
    for i, r in enumerate(b["recommendations"], 1):
        lines.append(f"{i}. {r['action']} — {r['rationale']}")
    lines.append("")
    lines.append("needs_human_approval: true（AI 不是审批入口）")
    lines.append("数据出处对象: " + ", ".join(b["citations"]))
    return "\n".join(lines)
