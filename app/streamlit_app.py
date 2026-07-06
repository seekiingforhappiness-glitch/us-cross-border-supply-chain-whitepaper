"""控制塔 UI（W5）：streamlit run app/streamlit_app.py

四视图：风险队列 / 任务处理台 / 对象详情 / 审计日志 + 角色切换器。
动作全部经 app/actions.py（权限、前置校验、审计在动作层，UI 只是壳）。
字段级权限（manual §6）：ops 不可见 Customer.tier；cs 不可见 est_cost_usd。
"""
import json
import sqlite3
from datetime import date, timedelta

import yaml
import streamlit as st

try:
    from app.actions import (assign_task, propose_mitigation, approve_mitigation,
                             close_risk_event)
    from app.admission_actions import (create_admission_case, run_compliance_precheck,
                                       build_logistics_plan, calculate_cost_scenario,
                                       approve_quote_decision, reject_or_request_more_info)
except ImportError:  # streamlit run app/streamlit_app.py 时脚本目录在 sys.path
    from actions import (assign_task, propose_mitigation, approve_mitigation,
                         close_risk_event)
    from admission_actions import (create_admission_case, run_compliance_precheck,
                                   build_logistics_plan, calculate_cost_scenario,
                                   approve_quote_decision, reject_or_request_more_info)

st.set_page_config(page_title="跨境供应链控制塔", layout="wide")
CFG = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
AS_OF = CFG["window"]["as_of"]
SEV_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡"}


def db():
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    return con


def rows(sql, *a):
    with db() as con:
        return [dict(r) for r in con.execute(sql, a)]


def mask_tier(tier, role):
    return tier if role in ("cs", "manager") else "🔒无权查看"


def mask_cost(v, role):
    return v if role in ("finance", "manager") else "🔒无权查看"


def show_result(r):
    if r["ok"]:
        # 成功消息存入会话状态，rerun 后仍可见（否则被刷新冲掉，用户会误以为没成功而重复点击）
        st.session_state["flash"] = "✅ " + ("；".join(r["side_effects"]) or "完成")
        st.rerun()
    else:
        st.error(r["error"])


# ---------- 侧边栏 ----------
with st.sidebar:
    st.title("🚢 控制塔")
    role = st.selectbox("当前角色", ["ops", "cs", "manager", "sales", "compliance", "finance"],
                        format_func=lambda r: {"ops": "物流运营 ops", "cs": "客户成功 cs",
                                               "manager": "经理 manager", "sales": "销售 sales",
                                               "compliance": "合规 compliance",
                                               "finance": "财务 finance"}[r])
    actor = st.text_input("操作人", "daniel")
    st.caption(f"仿真时钟 as_of = **{AS_OF}**（D8）")
    n_open = rows("SELECT count(*) c FROM risk_events WHERE status NOT IN ('resolved','escalated')")[0]["c"]
    st.metric("未结风险", n_open)

if "flash" in st.session_state:  # 上一动作的成功回执
    st.success(st.session_state.pop("flash"))

tab_risk, tab_task, tab_obj, tab_adm, tab_log = st.tabs(
    ["🚨 风险队列", "🛠 任务处理台", "🔍 对象详情", "📋 准入工作台", "📜 审计日志"])

# ---------- 风险队列 ----------
with tab_risk:
    show_closed = st.checkbox("显示已关闭", value=False)
    where = "" if show_closed else "WHERE r.status NOT IN ('resolved','escalated')"
    risks = rows(f"""SELECT r.*, s.eta_initial, s.eta_current, s.delay_days, s.destination_port
                     FROM risk_events r JOIN shipments s ON s.shipment_id=r.shipment_id {where}
                     ORDER BY CASE r.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                              r.affected_value_usd DESC""")
    st.dataframe([{"风险": r["risk_event_id"], "级别": f"{SEV_ICON[r['severity']]}{r['severity']}",
                   "类型": r["type"], "规则": r["rule_id"], "货运": r["shipment_id"],
                   "延误(天)": r["delay_days"], "影响金额($)": r["affected_value_usd"],
                   "状态": r["status"]} for r in risks],
                 width="stretch", height=260)
    if risks:
        sel = st.selectbox("查看风险", [r["risk_event_id"] for r in risks])
        r = next(x for x in risks if x["risk_event_id"] == sel)
        st.markdown(f"**根因**：{r['root_cause']}　|　ETA {r['eta_initial']} → **{r['eta_current']}**")
        lids = json.loads(r["affected_so_line_ids"])
        if lids:
            ph = ",".join("?" * len(lids))
            lines = rows(f"""SELECT l.so_line_id, l.qty, l.promised_delivery_date, l.line_status,
                             so.so_id, c.customer_name, c.tier, k.sku_name
                             FROM sales_order_lines l JOIN sales_orders so ON so.so_id=l.so_id
                             JOIN customers c ON c.customer_id=so.customer_id
                             JOIN skus k ON k.sku_id=l.sku_id WHERE l.so_line_id IN ({ph})""", *lids)
            st.markdown("**受影响订单行**")
            st.dataframe([{"行": x["so_line_id"], "商品": x["sku_name"], "数量": x["qty"],
                           "承诺日": x["promised_delivery_date"], "行状态": x["line_status"],
                           "客户": x["customer_name"], "客户等级": mask_tier(x["tier"], role)}
                          for x in lines], width="stretch")
        c1, c2 = st.columns(2)
        with c1, st.form(f"assign_{sel}"):
            st.markdown("**派发任务（A3，运营）**")
            a_role = st.selectbox("处理角色", ["ops", "cs"])
            prio = st.selectbox("优先级", ["P1", "P2", "P3"])
            due = st.date_input("任务处理截止日", date.fromisoformat(AS_OF) + timedelta(days=2),
                                help="要求处理人完成处置的期限（默认 48 小时），"
                                     "不是货物交付日期，也不是客户承诺日")
            if st.form_submit_button("派单"):
                show_result(assign_task(db(), sel, a_role, prio, due.isoformat(),
                                        actor=actor, role=role, as_of=AS_OF))
        with c2, st.form(f"close_{sel}"):
            st.markdown("**关闭风险（A6，运营）**")
            outcome = st.selectbox("结论", ["mitigated", "accepted_delay", "false_alarm", "escalated"])
            summary = st.text_input("处理小结")
            if st.form_submit_button("关闭"):
                show_result(close_risk_event(db(), sel, outcome, summary,
                                             actor=actor, role=role, as_of=AS_OF))

# ---------- 任务处理台 ----------
with tab_task:
    tasks = rows("""SELECT t.*, r.severity, r.shipment_id, r.status risk_status
                    FROM tasks t JOIN risk_events r ON r.risk_event_id=t.risk_event_id
                    ORDER BY t.task_id DESC""")
    st.dataframe([{"任务": t["task_id"], "风险": t["risk_event_id"],
                   "级别": f"{SEV_ICON[t['severity']]}{t['severity']}", "货运": t["shipment_id"],
                   "负责角色": t["assignee_role"], "优先级": t["priority"], "处理截止": t["due_at"],
                   "提案": t["proposed_action"] or "-", "审批": t["approval_status"] or "-",
                   "状态": t["status"]} for t in tasks],
                 width="stretch", height=230)
    if tasks:
        tsel = st.selectbox("处理任务", [t["task_id"] for t in tasks])
        t = next(x for x in tasks if x["task_id"] == tsel)
        st.markdown(f"**{t['title']}**　|　任务状态 `{t['status']}`　风险状态 `{t['risk_status']}`")
        if t["proposal_params"]:
            p = json.loads(t["proposal_params"])
            if role == "cs" and "est_cost_usd" in p:
                p["est_cost_usd"] = "🔒无权查看"
            st.markdown(f"当前提案：`{t['proposed_action']}` {p}")
        if t["status"] == "assigned":
            with st.form(f"prop_{tsel}"):
                st.markdown("**提交处置方案（A4，运营/客户成功）**")
                opts = ["reschedule", "accept_delay"] + ([] if role == "cs" else ["expedite"])
                act = st.selectbox("方案", opts,
                                   help="cs 角色不可发起加急（成本字段无权限）" if role == "cs" else None)
                new_date = st.date_input("新承诺日（reschedule）",
                                         date.fromisoformat(AS_OF) + timedelta(days=19))
                cost = st.number_input("预估加急成本 $（expedite）", 0.0, step=100.0)
                new_eta = st.date_input("加急后预计到达（expedite）",
                                        date.fromisoformat(AS_OF) + timedelta(days=5))
                reason = st.text_input("理由（accept_delay）")
                if st.form_submit_button("提交提案"):
                    params = {"reschedule": {"new_promise_date": new_date.isoformat(),
                                             "notify_customer": True},
                              "expedite": {"new_mode": "air", "est_cost_usd": cost,
                                           "expected_new_eta": new_eta.isoformat()},
                              "accept_delay": {"reason": reason}}[act]
                    show_result(propose_mitigation(db(), tsel, act, params,
                                                   actor=actor, role=role, as_of=AS_OF))
        if t["approval_status"] == "pending":
            with st.form(f"appr_{tsel}"):
                st.markdown("**审批（A5，仅经理）**")
                decision = st.radio("决定", ["approved", "rejected"], horizontal=True)
                comment = st.text_input("批注")
                if st.form_submit_button("提交审批"):
                    show_result(approve_mitigation(db(), tsel, decision, comment,
                                                   actor=actor, role=role, as_of=AS_OF))

# ---------- 对象详情 ----------
with tab_obj:
    ships = rows("SELECT shipment_id FROM shipments ORDER BY shipment_id")
    ssel = st.selectbox("Shipment", [s["shipment_id"] for s in ships],
                        index=[s["shipment_id"] for s in ships].index("SHP-2026-0099"))
    sp = rows("SELECT * FROM shipments WHERE shipment_id=?", ssel)[0]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("状态", sp["status"], f"来源字段: {sp['status_source']}" if sp["status_source"] != sp["status"] else None)
    c2.metric("ETA", sp["eta_current"], f"{sp['delay_days']}天延误" if sp["delay_days"] else None,
              delta_color="inverse")
    c3.metric("清关", sp["customs_status"], f"缺: {sp['missing_docs']}" if sp["missing_docs"] else None,
              delta_color="inverse")
    c4.metric("贸易术语", sp["incoterm"], "已加急" if sp["expedite_flag"] else None)
    st.caption(f"{sp['carrier_name'] or sp['carrier_scac']} | {sp['vessel_voyage'] or '船名缺失'} | "
               f"箱 {sp['container_no']} {sp['container_type']} | "
               f"{sp['origin_port_locode']} → {sp['destination_port_locode']} | "
               f"订舱 {sp['booking_no']} | 提单 {sp['mbl_no']}")
    st.markdown("**事件流（判重后）**")
    ms = rows("""SELECT event_time, event_type, event_classifier, event_locode, new_eta,
                 source_system, is_duplicate FROM shipment_milestones
                 WHERE shipment_id=? ORDER BY event_time""", ssel)
    st.dataframe([{"时间": m["event_time"], "事件": m["event_type"],
                   "ACT/EST": m["event_classifier"], "地点": m["event_locode"],
                   "新ETA": m["new_eta"] or "-", "来源": m["source_system"],
                   "重复?": "⚠️" if m["is_duplicate"] else ""} for m in ms],
                 width="stretch")
    st.markdown("**影响链：船上货 → 订单行 → 客户**")
    chain = rows("""SELECT a.so_line_id, a.allocated_qty, l.promised_delivery_date, l.line_status,
                    l.reschedule_count, so.so_id, c.customer_name, c.tier, k.sku_name
                    FROM shipment_allocations a
                    JOIN sales_order_lines l ON l.so_line_id=a.so_line_id
                    JOIN sales_orders so ON so.so_id=l.so_id
                    JOIN customers c ON c.customer_id=so.customer_id
                    JOIN skus k ON k.sku_id=l.sku_id WHERE a.shipment_id=?""", ssel)
    st.dataframe([{"订单行": x["so_line_id"], "商品": x["sku_name"], "分配量": x["allocated_qty"],
                   "承诺日": x["promised_delivery_date"], "改期次数": x["reschedule_count"],
                   "行状态": x["line_status"], "订单": x["so_id"], "客户": x["customer_name"],
                   "客户等级": mask_tier(x["tier"], role)} for x in chain],
                 width="stretch")

# ---------- 准入工作台（v0.3）----------
with tab_adm:
    if role == "sales":
        with st.expander("➕ 新建准入案件（B1，销售）"):
            with st.form("b1"):
                custs = rows("SELECT customer_id, customer_name FROM customers ORDER BY customer_id")
                cands = rows("SELECT sku_id, sku_name FROM skus WHERE sku_status='candidate'")
                b1_c = st.selectbox("客户", [c["customer_id"] for c in custs],
                                    format_func=lambda i: f"{i} {next(x['customer_name'] for x in custs if x['customer_id']==i)}")
                b1_k = st.selectbox("候选 SKU", [k["sku_id"] for k in cands],
                                    format_func=lambda i: f"{i} {next(x['sku_name'] for x in cands if x['sku_id']==i)}") if cands else None
                b1_rt = st.selectbox("请求类型", ["new_sku", "ddp_quote", "dap_quote", "plan_review"])
                b1_it = st.selectbox("意向贸易术语", ["FOB", "DAP", "DDP", "tbd"])
                b1_dt = st.date_input("目标上线日", date.fromisoformat(AS_OF) + timedelta(days=60))
                b1_qty = st.number_input("预估月单量", 100, 100000, 1000)
                if st.form_submit_button("建案") and b1_k:
                    show_result(create_admission_case(db(), b1_c, b1_k, b1_rt, b1_it,
                                                      b1_dt.isoformat(), b1_qty,
                                                      actor=actor, role=role, as_of=AS_OF))
    acs = rows("""SELECT a.*, c.customer_name, k.sku_name FROM admission_cases a
                  JOIN customers c ON c.customer_id=a.customer_id
                  JOIN skus k ON k.sku_id=a.sku_id ORDER BY a.admission_case_id""")
    st.dataframe([{"案件": a["admission_case_id"], "标题": a["case_title"],
                   "术语": a["incoterm_candidate"], "风险": a["risk_level"] or "-",
                   "状态": a["status"], "决定": a["decision"] or "-"} for a in acs],
                 width="stretch", height=240)
    asel = st.selectbox("查看案件", [a["admission_case_id"] for a in acs],
                        index=[a["admission_case_id"] for a in acs].index("AC-2026-0031"))
    ac = next(x for x in acs if x["admission_case_id"] == asel)
    st.markdown(f"**{ac['case_title']}**　状态 `{ac['status']}`　风险 `{ac['risk_level'] or '-'}`"
                + (f"　决定 `{ac['decision']}`：{ac['decision_reason']}" if ac["decision"] else ""))
    finds = rows("SELECT * FROM compliance_findings WHERE admission_case_id=?", asel)
    if finds:
        st.markdown("**合规发现**")
        st.dataframe([{"发现": f["compliance_finding_id"], "类型": f["finding_type"],
                       "级别": f["severity"], "HTS": f["hts_candidate"] or "-",
                       "机构": f["pga_agency"], "证据": f["evidence_status"],
                       "建议": f["recommendation"]} for f in finds], width="stretch")
    aplans = rows("SELECT * FROM logistics_plans WHERE admission_case_id=?", asel)
    if aplans:
        st.markdown("**物流方案**")
        st.dataframe([{"方案": p["logistics_plan_id"], "路线": p["route_type"],
                       "术语": p["incoterm"],
                       "港口": f"{p['origin_port_locode']}→{p['destination_port_locode']}",
                       "时效(天)": p["estimated_transit_days"], "SLA风险": p["sla_risk"]}
                      for p in aplans], width="stretch")
        pids = [p["logistics_plan_id"] for p in aplans]
        scens = rows(f"""SELECT * FROM cost_scenarios
                         WHERE logistics_plan_id IN ({','.join('?' * len(pids))})""", *pids)
        if scens:
            st.markdown("**成本情景（成本与毛利仅财务/经理可见）**")
            st.dataframe([{"情景": s["cost_scenario_id"], "方案": s["logistics_plan_id"],
                           "类型": s["scenario_type"],
                           "报价$": mask_cost(s["quote_price_usd"], role),
                           "毛利$": mask_cost(s["gross_margin_usd"], role),
                           "毛利率": mask_cost(s["gross_margin_rate"], role)}
                          for s in scens], width="stretch")
    c1, c2 = st.columns(2)
    with c1:
        if role == "compliance":
            with st.form(f"b2_{asel}"):
                st.markdown("**合规预审（B2，合规）**")
                ft = st.selectbox("类型", ["hts", "pga", "certification", "labeling", "origin",
                                          "uflpa", "ad_cvd", "section_301", "platform_rule"])
                sev = st.selectbox("严重度", ["low", "medium", "high", "critical"])
                hts = st.text_input("HTS（类型为 hts 必填）", "8504.40.95")
                pga = st.selectbox("PGA 机构", ["none", "CBP", "FDA", "FCC", "CPSC", "EPA", "USDA"])
                ev = st.selectbox("证据状态", ["verified", "provided", "missing", "rejected"])
                rec = st.selectbox("建议", ["accept", "more_docs", "dap_only", "reject", "escalate"])
                if st.form_submit_button("提交预审"):
                    show_result(run_compliance_precheck(db(), asel, [{
                        "finding_title": f"{ft} review", "finding_type": ft, "severity": sev,
                        "hts_candidate": hts if ft == "hts" else "", "pga_agency": pga,
                        "evidence_status": ev, "recommendation": rec}],
                        actor=actor, role=role, as_of=AS_OF))
        if role == "ops":
            with st.form(f"b3_{asel}"):
                st.markdown("**物流方案（B3，运营）**")
                rt = st.selectbox("路线", ["ocean_fcl", "ocean_lcl", "air_freight", "express",
                                          "warehouse_fulfillment"])
                it = st.selectbox("贸易术语", ["FOB", "DAP", "DDP"])
                op = st.selectbox("起运港", ["CNYTN", "CNSHK", "CNNGB"])
                dp = st.selectbox("目的港", ["USLAX", "USLGB"])
                wr = st.selectbox("美仓区域", ["west", "central", "east", "platform"])
                lm = st.selectbox("尾程", ["UPS", "FedEx", "USPS", "OnTrac", "LTL", "platform"])
                td = st.number_input("预计总时效(天)", 3, 60, 32)
                sr = st.selectbox("SLA 风险", ["low", "medium", "high"])
                if st.form_submit_button("提交方案"):
                    show_result(build_logistics_plan(db(), asel, {
                        "plan_name": f"{rt}/{it}", "route_type": rt, "incoterm": it,
                        "origin_port_locode": op, "destination_port_locode": dp,
                        "us_warehouse_region": wr, "last_mile_method": lm,
                        "estimated_transit_days": td, "sla_risk": sr},
                        actor=actor, role=role, as_of=AS_OF))
        if role == "finance" and aplans:
            with st.form(f"b4_{asel}"):
                st.markdown("**成本情景（B4，财务）**")
                b4_p = st.selectbox("方案", [p["logistics_plan_id"] for p in aplans])
                b4_t = st.selectbox("情景", ["conservative", "base", "optimistic"])
                quote = st.number_input("报价 $", 0.0, step=100.0, value=10000.0)
                costs = {k: st.number_input(k, 0.0, step=50.0, value=v) for k, v in
                         (("product_cost_usd", 5000.0), ("first_mile_cost_usd", 300.0),
                          ("international_freight_usd", 1800.0), ("duty_tax_usd", 650.0),
                          ("customs_brokerage_usd", 180.0), ("warehouse_cost_usd", 400.0),
                          ("last_mile_cost_usd", 900.0), ("returns_allowance_usd", 150.0),
                          ("risk_buffer_usd", 250.0))}
                if st.form_submit_button("提交情景"):
                    show_result(calculate_cost_scenario(db(), b4_p, {"scenario_type": b4_t,
                                "quote_price_usd": quote, **costs},
                                actor=actor, role=role, as_of=AS_OF))
    with c2:
        if role == "manager" and aplans:
            with st.form(f"b5_{asel}"):
                st.markdown("**审批（B5，仅经理）——门禁 G1/G2/G3 自动校验**")
                b5_p = st.selectbox("批准方案", [p["logistics_plan_id"] for p in aplans])
                allsc = rows("SELECT cost_scenario_id FROM cost_scenarios WHERE logistics_plan_id=?", b5_p)
                b5_s = st.selectbox("批准情景", [s["cost_scenario_id"] for s in allsc]) if allsc else None
                b5_d = st.radio("决定", ["approve", "quote_with_conditions"], horizontal=True)
                b5_r = st.text_input("审批理由")
                b5_c = st.text_input("附加条件（quote_with_conditions）")
                if st.form_submit_button("提交审批") and b5_s:
                    show_result(approve_quote_decision(db(), asel, b5_p, b5_s, b5_d, b5_r, b5_c,
                                                       actor=actor, role=role, as_of=AS_OF))
        if role in ("compliance", "manager"):
            with st.form(f"b6_{asel}"):
                st.markdown("**拒接 / 补资料（B6）**")
                b6_d = st.radio("决定", ["more_info", "reject"], horizontal=True,
                                help="reject 仅经理可执行")
                b6_m = st.text_input("缺失文件（more_info，分号分隔）")
                b6_r = st.text_input("拒接原因（reject）")
                if st.form_submit_button("提交"):
                    show_result(reject_or_request_more_info(
                        db(), asel, b6_d, [x.strip() for x in b6_m.split(";") if x.strip()],
                        b6_r, actor=actor, role=role, as_of=AS_OF))

# ---------- 审计日志 ----------
with tab_log:
    only_bad = st.checkbox("只看被拒/越权", value=False)
    logs = rows(f"""SELECT * FROM action_log {"WHERE result != 'ok' AND result NOT IN ('created','merged')" if only_bad else ""}
                    ORDER BY log_id DESC LIMIT 200""")
    st.dataframe([{"#": x["log_id"], "操作人": x["actor"], "角色": x["role"], "动作": x["action"],
                   "对象": x["target_object_id"], "参数": x["params_json"],
                   "as_of": x["as_of_date"], "结果": x["result"]} for x in logs],
                 width="stretch", height=420)
