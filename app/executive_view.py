"""全局 Executive 一页视图数据层：跨 5 场景的高管总览（纯只读聚合）。

设计动机（≤5 行「为什么这样建」）：
- manager 原 KPI 落地页只有单场景快照；本模块把 5 个业务场景（延误/费用/采购/仓储/准入）+ 跨场景横条
  聚合成一个数据字典，让经理一屏看全域健康。
- **纯 §4 呈现层**：只对现有表 SELECT 计数，零写入、零动作、零新对象/规则/schema；不改任何权限。
- 纯函数 + 零 Streamlit 依赖：可被 streamlit_app 渲染，也可被 test_executive_view 直接 import 断言，
  且渲染显示的数字与测试断言同源（build 一次、render 与 test 都读它）。
- row_factory 无关：一律用 r[0] 取标量，Row 与 tuple 两种连接都能跑。
"""
from __future__ import annotations

# 未结风险谓词：与 streamlit_app / render_kpi_tab 全一致（resolved/escalated 视为已了结）。
OPEN_RISK = "status NOT IN ('resolved','escalated')"

# 场景 → 风险规则区间（与 ontology R1-R18 对应；rule_id 为真源，避免依赖 type 拼写）。
DELAY_RULES = ("R1", "R2", "R3")            # 延误运营：delay_breach / docs_missing / stalled
COST_RULES = ("R4", "R5", "R6")             # 费用稽核：rate_overbilling / duplicate / unplanned
PROCUREMENT_RULES = ("R7", "R8", "R9", "R10", "R11",  # 采购：三方对账 + 预付款 + 资质 + 采购治理
                     "R12", "R13", "R14", "R15")
WAREHOUSE_RULES = ("R16", "R17", "R18")     # 仓储库存：stockout / unfulfillable / shrinkage


def _scalar(conn, sql, params=()):
    """取查询首行首列（计数场景），无行则 0。"""
    row = conn.execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return 0
    return row[0]


def _open_risk_in(conn, rule_ids):
    """指定 rule_id 集合内的未结风险数（open）。"""
    ph = ",".join("?" * len(rule_ids))
    return _scalar(conn, f"SELECT count(*) FROM risk_events "
                         f"WHERE {OPEN_RISK} AND rule_id IN ({ph})", rule_ids)


def _open_risk_by_severity(conn, rule_ids):
    """指定 rule 集合内未结风险按 severity 分布（含 critical/high/medium 归零补齐）。"""
    ph = ",".join("?" * len(rule_ids))
    got = {r[0]: r[1] for r in conn.execute(
        f"SELECT severity, count(*) FROM risk_events "
        f"WHERE {OPEN_RISK} AND rule_id IN ({ph}) GROUP BY severity", rule_ids)}
    return {sev: got.get(sev, 0) for sev in ("critical", "high", "medium")}


def build_executive_summary(conn):
    """跨 5 场景 + 跨场景横条的高管总览（全部只读 SELECT 聚合，无任何写入/动作）。

    返回 dict：{delay, cost, procurement, warehouse, admission, cross}，各含纯计数。
    数字口径与 streamlit render_kpi_tab 同源（render 与 test 都读本函数），不臆造。
    """
    # ---- 延误运营（R1-R3）----
    delay = {
        "open_risk": _open_risk_in(conn, DELAY_RULES),
        "by_severity": _open_risk_by_severity(conn, DELAY_RULES),
        "at_risk_so_lines": _scalar(
            conn, "SELECT count(*) FROM sales_order_lines WHERE line_status='at_risk'"),
    }
    # ---- 费用稽核（R4-R6）----
    cost = {
        "open_risk": _open_risk_in(conn, COST_RULES),
        "by_severity": _open_risk_by_severity(conn, COST_RULES),
        "under_review_invoices": _scalar(
            conn, "SELECT count(*) FROM invoices WHERE status='under_review'"),
        "disputed_invoices": _scalar(
            conn, "SELECT count(*) FROM invoices WHERE status='disputed'"),
    }
    # ---- 采购（R7-R15）----
    procurement = {
        "open_risk": _open_risk_in(conn, PROCUREMENT_RULES),
        "three_way_recon": _open_risk_in(conn, ("R10", "R11")),   # 三方对账异常
        "prepayment_exposure": _open_risk_in(conn, ("R12",)),     # 预付款敞口
        "qualification_expired": _open_risk_in(conn, ("R13",)),   # 资质过期
        "single_source": _open_risk_in(conn, ("R14",)),           # 单一来源
        "maverick_spend": _open_risk_in(conn, ("R15",)),          # maverick 采购
    }
    # ---- 仓储库存（R16-R18）----
    warehouse = {
        "open_risk": _open_risk_in(conn, WAREHOUSE_RULES),
        "stockout": _open_risk_in(conn, ("R16",)),          # 断货
        "unfulfillable": _open_risk_in(conn, ("R17",)),     # 不可履约
        "shrinkage": _open_risk_in(conn, ("R18",)),         # 盘点差异
    }
    # ---- 准入合规 ----
    admission = {
        "by_status": {r[0]: r[1] for r in conn.execute(
            "SELECT status, count(*) FROM admission_cases GROUP BY status ORDER BY status")},
        # 门禁触发：G1 硬门禁条件——severity=critical 且 evidence_status≠verified 的合规发现（禁批）。
        "gate_triggered": _scalar(
            conn, "SELECT count(*) FROM compliance_findings "
                  "WHERE severity='critical' AND evidence_status!='verified'"),
    }
    # ---- 跨场景横条 ----
    cross = {
        "total_open_risk": _scalar(conn, f"SELECT count(*) FROM risk_events WHERE {OPEN_RISK}"),
        "tasks_by_sla": {s: _scalar(
            conn, "SELECT count(*) FROM tasks WHERE sla_state=?", (s,))
            for s in ("open", "due_today", "overdue")},
        "escalation_candidates": _scalar(
            conn, "SELECT count(*) FROM tasks WHERE sla_state='overdue' OR escalation_level>0"),
        "dq_open": _scalar(conn, "SELECT count(*) FROM dq_issues WHERE status!='closed'"),
    }
    return {
        "delay": delay,
        "cost": cost,
        "procurement": procurement,
        "warehouse": warehouse,
        "admission": admission,
        "cross": cross,
    }
