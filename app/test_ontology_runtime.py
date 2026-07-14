"""桥2 运行时侧迁移一致性测试（API 层 plan M2 的 TDD 主线）：python3 -m app.test_ontology_runtime

守护命题：`pipeline.ontology_runtime` 从本体**解释生成**的权限/工具，必须与迁移前手维护的
硬编码字面量**逐字节等价**——生成结果 == 迁移前基线，则「本体成为唯一权威源」不改变任何行为。

本文件落死**迁移前基线**（LEGACY_*，由一次性脚本从旧 agent/tools.py 的 TOOL_DEFS/FORBIDDEN_TOOLS
抓取，json 归一化）。TDD 顺序：先写本测试并对着**未迁移**的运行时跑绿（证明生成器忠实复刻现状），
再迁移 5 个权限字典 + tools.py（迁移后本测试仍绿 = 迁移零行为漂移）。权限侧另引 test_agent_security
的 EXPECTED_* 人批口径交叉验证（生成结果必须等于人批基线，不是等于我自己抄的一份）。

只读断言；不碰数据库、不碰任何业务代码。
"""
import json
import sqlite3
import sys
from pathlib import Path

from pipeline.generate_ddl import object_ddls
from pipeline.ontology_runtime import (build_forbidden_tools, build_role_perms,
                                        build_tool_defs, load_ontology, snake_case,
                                        traverse)
# 人批口径（防被悄悄削弱）：权限侧基线直接取自安全回归的 EXPECTED_*（同一份守护）。
from .test_agent_security import EXPECTED_ADM_PERMS, EXPECTED_ROLE_PERMS

REPO_ROOT = Path(__file__).resolve().parent.parent

FAILS = []

# ═══════════════════════════════════════════════════════════════════════════
# 迁移前基线（LEGACY_*）——一次性脚本从旧 TOOL_DEFS/FORBIDDEN_TOOLS/5 权限字典抓取后落死
# ═══════════════════════════════════════════════════════════════════════════

# 旧 5 个权限字典的人批口径（PROC/WH/COORD 在 test_agent_security 无 EXPECTED，故此处落死；
# ROLE/ADM 用 test_agent_security 的 EXPECTED_* 交叉验证，见下）。
LEGACY_PROC_PERMS = {
    "RecordGoodsReceipt": {"ops", "system"},
    "MatchSupplierInvoice": {"finance", "system"},
}
LEGACY_WH_PERMS = {
    "Putaway": {"ops", "system"},
    "ReserveInventory": {"ops", "system"},
    "ReleaseReservation": {"ops", "system"},
    "RecordCycleCount": {"ops", "system"},
}
LEGACY_COORD_PERMS = {"ManageCoordination": {"ops", "cs", "procurement", "finance"}}

# build_role_perms 的完整键集（enforcement=role_dict 动作按 permission_key 归组）= 5 字典并集。
LEGACY_ROLE_PERMS_KEYS = (
    set(EXPECTED_ROLE_PERMS) | set(EXPECTED_ADM_PERMS)
    | set(LEGACY_PROC_PERMS) | set(LEGACY_WH_PERMS) | set(LEGACY_COORD_PERMS))

# 迁移前冻结区拉黑集（旧 FORBIDDEN_TOOLS 字面量）。
LEGACY_FORBIDDEN_TOOLS = {"approve_mitigation", "close_risk_event",
                          "approve_quote_decision", "reject_or_request_more_info"}

# 迁移前 TOOL_DEFS（17 工具：11 读 + 6 写）——原样搬家的对照基线，含顺序。
LEGACY_TOOL_DEFS = [
    {"name": "list_open_risks",
     "description": "列出未关闭的风险事件，可按 severity 过滤（critical/high/medium）",
     "input_schema": {"type": "object", "properties": {
         "severity": {"type": "string", "enum": ["critical", "high", "medium"]}}}},
    {"name": "get_risk",
     "description": "查单个风险事件详情（类型/规则/级别/根因/受影响行/金额/状态）",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "get_shipment_context",
     "description": "查货运完整上下文：基础信息、判重后事件流、所载订单行与客户（按角色脱敏）",
     "input_schema": {"type": "object", "properties": {
         "shipment_id": {"type": "string"}}, "required": ["shipment_id"]}},
    {"name": "get_impact_chain",
     "description": "查风险的影响链：受影响订单行→销售订单→客户，含分配数量与金额",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "get_audit_trail",
     "description": "查对象（风险/任务）的审计历史，含被拒绝的调用",
     "input_schema": {"type": "object", "properties": {
         "object_id": {"type": "string"}}, "required": ["object_id"]}},
    {"name": "list_admission_cases",
     "description": "列出准入案件（v0.3），可按 status 过滤（draft/in_precheck/plan_ready/priced/approved/quote_with_conditions/rejected/needs_more_info）",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "get_admission_context",
     "description": "查准入案件完整上下文：案件、合规发现、物流方案、成本情景（成本字段按角色脱敏）、客户能力",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"}}, "required": ["admission_case_id"]}},
    {"name": "list_invoices",
     "description": "列出发票（v0.4），可按 status 过滤（received/under_review/approved/disputed）。"
                    "返回 invoice_id/vendor/type/shipment/total/status/issue_date",
     "input_schema": {"type": "object", "properties": {"status": {"type": "string"}}}},
    {"name": "get_invoice_context",
     "description": "查发票完整对账上下文：发票 + 行明细（join expected_costs 给出基准与差异列）"
                    "+ 所属 shipment 摘要（incoterm/delay_days/status）。发票金额字段按角色脱敏"
                    "（与 UI 费用工作台 mask_cost 同规：finance/manager 可见，其余掩码）",
     "input_schema": {"type": "object", "properties": {
         "invoice_id": {"type": "string"}}, "required": ["invoice_id"]}},
    {"name": "get_similar_resolutions",
     "description": "C1 只读检索处置记忆：按规则类型精确匹配+同航线（origin→destination LOCODE）"
                    "查同类风险的历史处置——同类 N 次、按方案/决定的统计、最相似 1 案详情"
                    "（当时提案/人的决定/实际结果/质量标签）。所有数字运行时从 resolution_memory "
                    "现算可回查；无先例如实返回首例；被屏蔽（voided）的记忆不返回",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"}}, "required": ["risk_event_id"]}},
    {"name": "explain_relationship_path",
     "description": "只读查询 object_relationships：解释两个对象之间的有向关系路径",
     "input_schema": {"type": "object", "properties": {
         "source_type": {"type": "string"}, "source_id": {"type": "string"},
         "target_type": {"type": "string"}, "target_id": {"type": "string"},
         "max_depth": {"type": "integer", "minimum": 1, "maximum": 6}},
         "required": ["source_type", "source_id", "target_type", "target_id", "max_depth"]}},
    {"name": "assign_task",
     "description": "为 open 状态的风险派发处置任务（A3）。这是允许 AI 执行的写动作之一",
     "input_schema": {"type": "object", "properties": {
         "risk_event_id": {"type": "string"},
         "assignee_role": {"type": "string", "enum": ["ops", "cs"]},
         "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
         "due_at": {"type": "string"}},
         "required": ["risk_event_id", "assignee_role", "priority", "due_at"]}},
    {"name": "propose_mitigation",
     "description": "对 assigned 状态的任务提交处置提案（A4），最终须人工审批。proposal-only 的体现",
     "input_schema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "proposed_action": {"type": "string", "enum": ["reschedule", "expedite", "accept_delay"]},
         "proposal_params": {"type": "object"}},
         "required": ["task_id", "proposed_action", "proposal_params"]}},
    {"name": "create_admission_case",
     "description": "B1 建案（仅销售）：为 candidate SKU 建准入案。AI 准备动作之一，不做审批",
     "input_schema": {"type": "object", "properties": {
         "customer_id": {"type": "string"}, "sku_id": {"type": "string"},
         "request_type": {"type": "string"}, "incoterm_candidate": {"type": "string"},
         "target_launch_date": {"type": "string"}, "monthly_order_estimate": {"type": "integer"}},
         "required": ["customer_id", "sku_id", "request_type", "incoterm_candidate",
                      "target_launch_date", "monthly_order_estimate"]}},
    {"name": "run_compliance_precheck",
     "description": "B2 合规预审（仅合规）：提交 findings 列表，风险等级重算。AI 准备动作，不做审批",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"},
         "findings": {"type": "array", "items": {"type": "object"}}},
         "required": ["admission_case_id", "findings"]}},
    {"name": "build_logistics_plan",
     "description": "B3 物流方案（仅运营）：建方案，DDP 门禁自动校验。AI 准备动作，不做审批",
     "input_schema": {"type": "object", "properties": {
         "admission_case_id": {"type": "string"}, "plan": {"type": "object"}},
         "required": ["admission_case_id", "plan"]}},
    {"name": "calculate_cost_scenario",
     "description": "B4 成本情景（仅财务）：算成本与毛利。AI 准备动作，不做审批/拒接决策",
     "input_schema": {"type": "object", "properties": {
         "logistics_plan_id": {"type": "string"}, "scenario": {"type": "object"}},
         "required": ["logistics_plan_id", "scenario"]}},
    # F1 资金流（V8-②）：ProposeCollection 催收提案 maker 工具（exposed=true/auto）——本体新增动作
    # 随 build_tool_defs 自动枚举为第 7 个写工具（第 18 个工具）。基线随之扩展（仅追加、既有不改）。
    {"name": "propose_collection",
     "description": "对逾期应收(overdue 派生的 in 向 Payment)提交催收任务提案(F1)，最终须人工审批。proposal-only 的体现(maker-checker)",
     "input_schema": {"type": "object", "properties": {
         "payment_id": {"type": "string"}, "note": {"type": "string"}},
         "required": ["payment_id"]}},
]
LEGACY_TOOL_ORDER = [t["name"] for t in LEGACY_TOOL_DEFS]

# 迁移前 tools.py 角色域 scoping 常量（RISK/COST/ADMISSION 读工具分组）——TDD 步骤5 落死基线：
# 由 aiQueryTools 的 domain 字段派生，须与迁移前逐字相等（否则 scoping 行为漂移）。
LEGACY_READ_TOOLS_BY_DOMAIN = {
    "risk": {"list_open_risks", "get_risk", "get_shipment_context", "get_impact_chain",
             "get_audit_trail", "explain_relationship_path", "get_similar_resolutions"},
    "admission": {"list_admission_cases", "get_admission_context"},
    "cost": {"list_invoices", "get_invoice_context"},
}


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _norm(obj):
    """json 归一化（sort_keys）——深度相等比较不受键序影响。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _seed_memory_db(onto):
    """内存合成库（用生成 DDL 建表 + 手植最小行），覆盖 traverse 四承载而不依赖检测/seed 状态。"""
    ddls = object_ddls(onto)
    con = sqlite3.connect(":memory:")
    for tname in ["skus", "sales_order_lines", "shipments", "purchase_orders",
                  "warehouses", "risk_events"]:
        con.execute(ddls[tname][0])
    con.executescript(
        "INSERT INTO skus (sku_id) VALUES ('SKU-1');"
        "INSERT INTO sales_order_lines (so_line_id, sku_id) VALUES ('L1','SKU-1');"
        "INSERT INTO sales_order_lines (so_line_id, sku_id) VALUES ('L2','SKU-1');"
        "INSERT INTO shipments (shipment_id, po_ids, destination_warehouse) VALUES ('SHP-1','PO-1|PO-2','WH-1');"
        "INSERT INTO shipments (shipment_id, po_ids, destination_warehouse) VALUES ('SHP-2','PO-3','WH-1');"
        "INSERT INTO purchase_orders (po_id) VALUES ('PO-1');"
        "INSERT INTO warehouses (warehouse_id) VALUES ('WH-1');"
        "INSERT INTO risk_events (risk_event_id, affected_so_line_ids, affected_sku_ids) "
        "VALUES ('RSK-1','[\"L1\", \"L2\"]','[\"SKU-1\"]');")
    con.commit()
    return con


def _traverse_tests(onto):
    """traverse 单测：plan M3 点名覆盖——正向FK / reverse_json / column软命名 / N:M affected /
    declared_only 拒绝 / 未知 link 拒绝（+ 双向 + 非端点拒绝 + 真库 smoke）。"""
    print("== ⑨ traverse：四承载 + 双向 + 拒绝（内存合成库，覆盖 plan 点名 6 类）==")
    con = _seed_memory_db(onto)
    try:
        # 正向外键（N:1 标准推导）+ 反向
        check("⑨ 正向FK line_for_sku(SalesOrderLine→Sku)",
              traverse(con, "SalesOrderLine", "L1", "line_for_sku") == ["SKU-1"],
              str(traverse(con, "SalesOrderLine", "L1", "line_for_sku")))
        check("⑨ 反向FK line_for_sku(Sku→lines)",
              sorted(traverse(con, "Sku", "SKU-1", "line_for_sku")) == ["L1", "L2"],
              str(traverse(con, "Sku", "SKU-1", "line_for_sku")))
        # reverse_json（po_shipped_by：shipments.po_ids 承 PO 列表）双向
        check("⑨ reverse_json po_shipped_by(Shipment→POs)",
              traverse(con, "Shipment", "SHP-1", "po_shipped_by") == ["PO-1", "PO-2"],
              str(traverse(con, "Shipment", "SHP-1", "po_shipped_by")))
        check("⑨ reverse_json po_shipped_by 反向(PO→Shipment)",
              traverse(con, "PurchaseOrder", "PO-1", "po_shipped_by") == ["SHP-1"],
              str(traverse(con, "PurchaseOrder", "PO-1", "po_shipped_by")))
        # column 软命名（shipment_to_warehouse：shipments.destination_warehouse）双向
        check("⑨ column shipment_to_warehouse(Shipment→WH)",
              traverse(con, "Shipment", "SHP-1", "shipment_to_warehouse") == ["WH-1"],
              str(traverse(con, "Shipment", "SHP-1", "shipment_to_warehouse")))
        check("⑨ column 反向(WH→Shipments)",
              sorted(traverse(con, "Warehouse", "WH-1", "shipment_to_warehouse")) == ["SHP-1", "SHP-2"],
              str(traverse(con, "Warehouse", "WH-1", "shipment_to_warehouse")))
        # N:M affected（risk_affects_line：risk_events.affected_so_line_ids）双向
        check("⑨ N:M risk_affects_line(RiskEvent→lines)",
              traverse(con, "RiskEvent", "RSK-1", "risk_affects_line") == ["L1", "L2"],
              str(traverse(con, "RiskEvent", "RSK-1", "risk_affects_line")))
        check("⑨ N:M 反向(line→risks)",
              traverse(con, "SalesOrderLine", "L1", "risk_affects_line") == ["RSK-1"],
              str(traverse(con, "SalesOrderLine", "L1", "risk_affects_line")))
        # V6-裁1：risk_affects_sku 补正式承载列 affected_sku_ids、declared_only 退场，现真实可走（双向）
        check("⑨ N:M risk_affects_sku(RiskEvent→Sku)（V6-裁1 正式承载）",
              traverse(con, "RiskEvent", "RSK-1", "risk_affects_sku") == ["SKU-1"],
              str(traverse(con, "RiskEvent", "RSK-1", "risk_affects_sku")))
        check("⑨ N:M risk_affects_sku 反向(Sku→risks)",
              traverse(con, "Sku", "SKU-1", "risk_affects_sku") == ["RSK-1"],
              str(traverse(con, "Sku", "SKU-1", "risk_affects_sku")))
        # 未知 link 拒绝
        try:
            traverse(con, "Shipment", "SHP-1", "not_a_real_link")
            check("⑨ 未知 link 拒绝(raise)", False, "未 raise")
        except ValueError as e:
            check("⑨ 未知 link 拒绝(raise)", "未知关系" in str(e), str(e)[:70])
        # 非端点 object_type 拒绝
        try:
            traverse(con, "Sku", "SKU-1", "po_shipped_by")
            check("⑨ 非端点 object_type 拒绝(raise)", False, "未 raise")
        except ValueError as e:
            check("⑨ 非端点 object_type 拒绝(raise)", "端点" in str(e), str(e)[:70])
    finally:
        con.close()

    # 真实库 smoke：正向 FK + reverse_json 在真数据上也通（库存在才跑，否则跳过不判失败）
    db = REPO_ROOT / "data" / "ontology.sqlite"
    if db.exists():
        rcon = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = rcon.execute("SELECT so_line_id, sku_id FROM sales_order_lines LIMIT 1").fetchone()
            if row:
                check("⑨ 真库 smoke 正向FK line_for_sku 命中真数据",
                      traverse(rcon, "SalesOrderLine", row[0], "line_for_sku") == [row[1]],
                      str(traverse(rcon, "SalesOrderLine", row[0], "line_for_sku")))
            shp = rcon.execute("SELECT shipment_id, po_ids FROM shipments WHERE po_ids!='' LIMIT 1").fetchone()
            if shp:
                expect = [p for p in shp[1].split("|") if p]
                check("⑨ 真库 smoke reverse_json po_shipped_by 命中真数据",
                      traverse(rcon, "Shipment", shp[0], "po_shipped_by") == expect,
                      str(traverse(rcon, "Shipment", shp[0], "po_shipped_by")))
        finally:
            rcon.close()


def main():
    onto = load_ontology()
    gen_perms = build_role_perms(onto)
    gen_forbidden = build_forbidden_tools(onto)
    gen_tools = build_tool_defs(onto)
    gen_names = [t["name"] for t in gen_tools]

    print("== ① build_role_perms：生成的权限映射 == 5 个权限字典人批基线（逐键相等）==")
    # plan M2 点名断言：ProposeMitigation 恰为 P4 后的四角色
    check("① build_role_perms['ProposeMitigation'] == {ops,cs,finance,procurement}（P4 口径）",
          gen_perms.get("ProposeMitigation") == {"ops", "cs", "finance", "procurement"},
          str(gen_perms.get("ProposeMitigation")))
    # ROLE_PERMS 4 键：逐键 == test_agent_security 的 EXPECTED_ROLE_PERMS（人批口径守护）
    for k, v in EXPECTED_ROLE_PERMS.items():
        check(f"① ROLE_PERMS['{k}'] 生成 == 人批基线 {sorted(v)}", gen_perms.get(k) == v,
              str(gen_perms.get(k)))
    # ADM_PERMS 6 键：逐键 == EXPECTED_ADM_PERMS
    for k, v in EXPECTED_ADM_PERMS.items():
        check(f"① ADM_PERMS['{k}'] 生成 == 人批基线 {sorted(v)}", gen_perms.get(k) == v,
              str(gen_perms.get(k)))
    # PROC / WH / COORD：逐键 == 落死基线
    for label, base in (("PROC", LEGACY_PROC_PERMS), ("WH", LEGACY_WH_PERMS),
                        ("COORD", LEGACY_COORD_PERMS)):
        for k, v in base.items():
            check(f"① {label}_PERMS['{k}'] 生成 == 基线 {sorted(v)}", gen_perms.get(k) == v,
                  str(gen_perms.get(k)))
    # 键集完全一致（不多不少）：生成键集 == 5 字典并集，杜绝 role_dict 动作漏收/多收
    # F1（V8-②）：ROLE_PERMS 追加 RecordPayment/ProposeCollection 两键 → 并集 17→19。
    check("① build_role_perms 键集 == 5 权限字典并集（19 键，不多不少）",
          set(gen_perms) == LEGACY_ROLE_PERMS_KEYS,
          f"多={sorted(set(gen_perms)-LEGACY_ROLE_PERMS_KEYS)} 少={sorted(LEGACY_ROLE_PERMS_KEYS-set(gen_perms))}")

    print("== ② build_forbidden_tools：冻结区拉黑集 == 迁移前（恰四个审批/关闭类）==")
    check("② build_forbidden_tools == 迁移前 FORBIDDEN_TOOLS（不多不少）",
          gen_forbidden == LEGACY_FORBIDDEN_TOOLS, str(sorted(gen_forbidden)))

    print("== ③ build_tool_defs：工具名集合 + 顺序 == 基线 18 工具（11 读 + 7 写，含 F1 propose_collection）==")
    check("③ 工具名集合 == 基线 18 名（set 相等）",
          set(gen_names) == set(LEGACY_TOOL_ORDER),
          f"多={sorted(set(gen_names)-set(LEGACY_TOOL_ORDER))} 少={sorted(set(LEGACY_TOOL_ORDER)-set(gen_names))}")
    check("③ 工具名顺序 == 基线顺序（11 读 + 7 写，逐一对应）",
          gen_names == LEGACY_TOOL_ORDER, f"{gen_names}")
    check("③ 工具数 == 18", len(gen_tools) == 18, str(len(gen_tools)))

    print("== ④ build_tool_defs：逐工具 description + input_schema 深度相等（json 归一化）==")
    gen_by = {t["name"]: t for t in gen_tools}
    leg_by = {t["name"]: t for t in LEGACY_TOOL_DEFS}
    for name in LEGACY_TOOL_ORDER:
        g = gen_by.get(name, {})
        leg = leg_by[name]
        check(f"④ [{name}] input_schema 深度相等", _norm(g.get("input_schema")) == _norm(leg["input_schema"]),
              _norm(g.get("input_schema")))
        check(f"④ [{name}] description 相等", g.get("description") == leg["description"],
              str(g.get("description")))
        check(f"④ [{name}] 恰含 name/description/input_schema 三键",
              set(g) == {"name", "description", "input_schema"}, str(sorted(g)))

    print("== ⑤ 冻结区红线：生成工具集 ∩ 冻结区 == ∅（frozen 永不出现在 TOOL_DEFS）==")
    check("⑤ build_tool_defs 名集 ∩ build_forbidden_tools == ∅（全局红线3）",
          not (set(gen_names) & gen_forbidden), str(set(gen_names) & gen_forbidden))
    # 冻结区四动作 snake 逐一不在工具集
    frozen_from_actions = {snake_case(a["name"]) for a in onto["actions"]
                           if a.get("ai_executable") == "frozen"}
    check("⑤ ai_executable=frozen 四动作的 snake 均不在生成工具集",
          not (frozen_from_actions & set(gen_names)), str(frozen_from_actions & set(gen_names)))

    print("== ⑥ 角色域 scoping 基线：aiQueryTools.domain 派生的读工具分组 == 迁移前常量（TDD步骤5）==")
    aq = onto.get("aiQueryTools", [])
    for domain, expected in LEGACY_READ_TOOLS_BY_DOMAIN.items():
        derived = {q["name"] for q in aq if q.get("domain") == domain}
        check(f"⑥ domain={domain} 读工具分组 == 迁移前 {sorted(expected)}",
              derived == expected, f"派生={sorted(derived)}")
    # aiQueryTools 覆盖全部 11 读工具、无写工具混入
    all_domain_tools = {q["name"] for q in aq}
    check("⑥ aiQueryTools 恰覆盖 11 个读工具（== 三域并集）",
          all_domain_tools == set().union(*LEGACY_READ_TOOLS_BY_DOMAIN.values()),
          str(sorted(all_domain_tools)))

    print("== ⑦ 本体侧结构：7 个 exposed 动作均带 tool_description + tool_input_schema（含 F1 ProposeCollection）==")
    exposed = [a for a in onto["actions"] if a.get("exposed_as_tool") is True]
    check("⑦ exposed_as_tool=true 动作恰 7 个", len(exposed) == 7, str(len(exposed)))
    miss = [a["name"] for a in exposed
            if "tool_description" not in a or "tool_input_schema" not in a]
    check("⑦ 7 个 exposed 动作都带 tool_description + tool_input_schema 字段", not miss, str(miss))

    print("== ⑧ 变异防线：冻结区动作声明腐化必须被生成层显式拒绝（主会话评审固化） ==")
    # 场景：把 frozen 动作误标 exposed 且补齐 tool_* 字段（绕过 KeyError 偶然防护的最强变异）——
    # 生成层必须 raise 可读 ValueError，而非把审批工具注册出去留给 dispatch 层兜底。
    import copy as _copy
    mutated = _copy.deepcopy(onto)
    for a in mutated["actions"]:
        if a["name"] == "ApproveMitigation":
            a["exposed_as_tool"] = True
            a["tool_description"] = "变异注入"
            a["tool_input_schema"] = {"type": "object", "properties": {}}
    for fn_name, fn in (("build_tool_defs", build_tool_defs),
                        ("build_forbidden_tools", build_forbidden_tools)):
        try:
            fn(mutated)
            check(f"⑧ {fn_name} 拒绝『frozen 被标 exposed』变异", False, "未 raise——防线失效")
        except ValueError as e:
            check(f"⑧ {fn_name} 拒绝『frozen 被标 exposed』变异", "冻结区" in str(e), str(e)[:80])
    mutated2 = _copy.deepcopy(onto)
    for a in mutated2["actions"]:
        if a["name"] == "AssignTask":
            a["ai_executable"] = "never"  # exposed 仍 true → exposed⇔auto 破缺
    try:
        build_tool_defs(mutated2)
        check("⑧ build_tool_defs 拒绝『exposed⇔auto 破缺』变异", False, "未 raise——防线失效")
    except ValueError as e:
        check("⑧ build_tool_defs 拒绝『exposed⇔auto 破缺』变异", "不变式" in str(e), str(e)[:80])

    _traverse_tests(onto)

    print(f"\n{'=' * 44}")
    print(f"迁移一致性：生成结果 vs 迁移前基线 —— "
          f"{'全部通过 ✔（本体解释生成 == 硬编码基线，迁移零行为漂移）' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
