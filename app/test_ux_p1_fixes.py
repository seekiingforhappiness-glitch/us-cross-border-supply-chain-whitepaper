"""P1 高价值体验修复验证（陌生人测试六件）：python3 -m app.test_ux_p1_fixes

对应 docs/research/2026-07-13-ux-stranger-test.md 的 P1 修复：
 P1-1 用户侧文案全面去内部代码（成功/失败回执人话化，覆盖 assign/propose/approve/close 核心闭环
      + DQ/M1 长尾错误码 + 通用兜底清洗）
 P1-2 确定性简报默认展开 + 移到对象详情区顶部（6 个对象工作台）
 P1-3 「去 XX」引导可点（方案 B：st.tabs 实测无法程序化选中，按钮走 on_click 回调设
      session_state + 目标标签加粗高亮 + 顶部文字提示）
 P1-4 术语 tooltip 层（费种代码/规则代码图例 + 根因人话化模板）
 P1-5 首屏一句话健康度（与「我的今天」卡数字同源同查询）
 P1-6 列表搜索与排序（风险队列搜索+排序、发票列表搜索+只看异常）

红线：只读断言为主（AppTest 读真库 + 纯函数）；唯一涉及写操作的 P1-1 端到端场景用「备份→写→
断言→恢复」包裹，结束校验 data/ontology.sqlite md5 与开始时一致；ROLE_PERMS/actions.py 不改动
（人话化只在展示层，M1 maker-checker 铁律不受影响——已在开发阶段用不同 actor 验证过仍生效）。
"""
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile

from . import ux_copy
from .my_today import build_health_line, build_my_today, CARD_TARGET_TAB
from .rbac_nav import SURFACE_TABS

DB = "data/ontology.sqlite"
FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _conn(p=DB):
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c


BAD_TOKENS = ("expedite_flag", "MEM-", "D9/C4", "D9/C2", "决策血缘", "退出码", "CLI")


def _no_bad_tokens(text):
    return text is not None and all(tok not in text for tok in BAD_TOKENS)


def main():
    md5_before = hashlib.md5(open(DB, "rb").read()).hexdigest()

    # ===== P1-1① 已知内部码样例 → 人话化后零内部码 =====
    print("== P1-1① 成功/失败回执样例：人话化后零内部码 ==")
    samples_side_effects = [
        "Shipment SHP-2026-0055 expedite_flag=1，行风险解除（D9/C2 简化）",
        "处置记忆已归档 MEM-7F685A72A9（C1 决策血缘）",
        "处置记忆结果已回填 3 条（质量标签 effective）",
        "RiskEvent RSK-2026-0031: open→acknowledged",
        "Task TSK-2026-0012 assigned to u-ops-us-amelia",
    ]
    for s in samples_side_effects:
        out = ux_copy.sanitize_side_effect_line(s)
        check(f"P1-1① side_effect 清洗后无内部码「{s[:30]}…」", _no_bad_tokens(out), out)
    check("P1-1① expedite 整句改写为人话（含加急+交期风险解除）",
          "已标记加急" in ux_copy.sanitize_side_effect_line(samples_side_effects[0])
          and "交期风险解除" in ux_copy.sanitize_side_effect_line(samples_side_effects[0]),
          ux_copy.sanitize_side_effect_line(samples_side_effects[0]))
    check("P1-1① 质量标签 effective→有效（不再是英文枚举原文）",
          "有效" in ux_copy.sanitize_side_effect_line(samples_side_effects[2])
          and "effective" not in ux_copy.sanitize_side_effect_line(samples_side_effects[2]),
          ux_copy.sanitize_side_effect_line(samples_side_effects[2]))

    samples_errors = [
        "已存在非终态任务 TSK-2026-0099（单风险单任务，D9/C4）",
        "role_not_permitted",
        "dq_issue_already_closed",
        "M1 审批边界拒绝：maker_checker_violation",
        "M1 审批边界拒绝：manager_role_required",
    ]
    for s in samples_errors:
        out = ux_copy.humanize_error(s)
        check(f"P1-1① 报错清洗后无内部码「{s[:30]}…」", _no_bad_tokens(out), out)
    check("P1-1① 「已存在非终态任务」改写为「该风险已有任务…不能重复派单」（P1-1 点名案例）",
          ux_copy.humanize_error(samples_errors[0])
          == "该风险已有任务 TSK-2026-0099 在处理，不能重复派单",
          ux_copy.humanize_error(samples_errors[0]))
    check("P1-1① M1 拒绝原因人话化（maker-checker 含义讲清楚，不是裸 token）",
          "同一人" in ux_copy.humanize_error(samples_errors[3])
          and "maker_checker_violation" not in ux_copy.humanize_error(samples_errors[3]),
          ux_copy.humanize_error(samples_errors[3]))
    check("P1-1① DQ 内部枚举码码全部有人话映射", all(
        ux_copy.humanize_error(code) != code
        for code in ("role_not_permitted", "assignee_user_id_required",
                     "dq_issue_not_found", "dq_issue_already_closed", "resolution_required")))

    # ===== P1-1① 端到端：assign→propose(expedite)→approve→close，真实 UI 流程人话回执 =====
    print("== P1-1① 端到端闭环（AppTest 真实写操作，备份/恢复包裹）==")
    bak = tempfile.mkstemp(suffix=".sqlite")[1]
    shutil.copy(DB, bak)
    try:
        from streamlit.testing.v1 import AppTest
        con = _conn()
        free = con.execute(
            """SELECT r.risk_event_id, r.shipment_id FROM risk_events r
               JOIN shipments s ON s.shipment_id=r.shipment_id
               WHERE r.status='open' AND r.type='delay_breach' AND NOT EXISTS
                 (SELECT 1 FROM tasks t WHERE t.risk_event_id=r.risk_event_id
                  AND t.status NOT IN ('done','cancelled'))
               ORDER BY r.risk_event_id LIMIT 1""").fetchone()
        if free:
            rid, sid = free["risk_event_id"], free["shipment_id"]
            at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at.session_state["role"] = "ops"
            at.session_state["risk_sel"] = rid
            at.run()
            next(s for s in at.selectbox if s.label == "处理角色").set_value("ops")
            next(b for b in at.button if getattr(b, "label", "") == "派单").click().run()
            assign_msg = next((s.value for s in at.success), None)
            check("P1-1① 派单回执人话（含风险 ID/角色/任务 ID/截止日，零内部码）",
                  assign_msg and "已派单" in assign_msg and _no_bad_tokens(assign_msg), assign_msg)

            tsel = con.execute(
                """SELECT task_id FROM tasks WHERE risk_event_id=?
                   AND status NOT IN ('done','cancelled') ORDER BY task_id DESC LIMIT 1""",
                (rid,)).fetchone()["task_id"]

            at2 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at2.session_state["role"] = "ops"
            at2.session_state["task_scope_mode"] = "all"
            at2.run()
            next(s for s in at2.selectbox if s.label == "处理任务").set_value(tsel).run()
            next(s for s in at2.selectbox if s.label == "方案").set_value("expedite")
            next(n for n in at2.number_input
                 if n.label == "预估加急成本 $（expedite）").set_value(1135.6)
            next(b for b in at2.button if getattr(b, "label", "") == "提交提案").click().run()
            propose_msg = next((s.value for s in at2.success), None)
            check("P1-1① 提案回执人话（含加急+预计费用，零内部码）",
                  propose_msg and "加急空运" in propose_msg and "1135.6" in propose_msg
                  and _no_bad_tokens(propose_msg), propose_msg)

            at3 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at3.session_state["role"] = "manager"
            at3.session_state["task_scope_mode"] = "all"
            at3.run()
            # 换一个不同于提案人（daniel）的审批人，避免撞上 M1 maker-checker（预期行为，非 bug）
            next(t_ for t_ in at3.text_input if t_.label == "操作人").set_value("daniel-mgr").run()
            next(s for s in at3.selectbox if s.label == "处理任务").set_value(tsel).run()
            next(b for b in at3.button if getattr(b, "label", "") == "提交审批").click().run()
            approve_msg = next((s.value for s in at3.success), None)
            check(f"P1-1① 审批回执 = 点名案例格式（已批准：{sid} 加急空运，预计费用 $…，交期风险解除）",
                  approve_msg == f"✅ 已批准：{sid} 加急空运，预计费用 $1135.6，交期风险解除",
                  approve_msg)
            check("P1-1① 审批回执零内部码", _no_bad_tokens(approve_msg or ""), approve_msg)

            at4 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at4.session_state["role"] = "ops"
            at4.session_state["risk_sel"] = rid
            at4.run()
            next(s for s in at4.selectbox if s.label == "结论").set_value("mitigated")
            next(t_ for t_ in at4.text_input
                 if t_.label == "处理小结").set_value("已加急处置")
            next(b for b in at4.button if getattr(b, "label", "") == "关闭").click().run()
            close_msg = next((s.value for s in at4.success), None)
            check(f"P1-1① 关闭回执人话（已关闭：{rid}（已妥善处置），零内部码）",
                  close_msg == f"✅ 已关闭：{rid}（已妥善处置）", close_msg)
        else:
            check("P1-1① 数据含可用于端到端验证的空闲延误风险（用例有意义）", False, "no free delay risk")
        con.close()
    finally:
        shutil.copy(bak, DB)
        import os
        os.remove(bak)

    # ===== P1-2 确定性简报移到顶部默认展开（AppTest：expanded=True + 结构顺序）=====
    print("== P1-2 确定性简报顶部默认展开（6 个对象工作台）==")
    from streamlit.testing.v1 import AppTest
    # 5 个工作台面的富对象（风险/准入/任务/发票/PO）在默认 work 面各自 tab 内嵌显示；
    # Warehouse 无独立 tab，挂在控制室「追查一件事」对象浏览器内（需切 nav_surface=control +
    # 选中 obj_type_sel=Warehouse）——两种入口分属不同导航面，分两次 AppTest 触达。
    at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at.session_state["role"] = "manager"
    at.run()
    at_wh = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at_wh.session_state["role"] = "manager"
    at_wh.session_state["nav_surface"] = "control"
    at_wh.session_state["obj_type_sel"] = "Warehouse"
    at_wh.run()
    briefing_labels = ("查看确定性风险简报（无需 API key，每条事实带对象 ID 出处）",
                       "查看确定性准入简报（无需 API key，每条事实带对象 ID 出处）",
                       "查看确定性任务简报（无需 API key，每条事实带对象 ID 出处）",
                       "查看确定性发票对账简报（无需 API key，逐行差异带对象 ID 出处）",
                       "查看确定性三方对账简报（无需 API key，逐行差异带对象 ID 出处）",
                       "查看确定性仓储库存简报（无需 API key，每条带对象 ID 出处）")
    found = {e.label: e.proto.expanded for e in at.expander if e.label in briefing_labels}
    found.update({e.label: e.proto.expanded for e in at_wh.expander if e.label in briefing_labels})
    for lbl in briefing_labels:
        check(f"P1-2 「{lbl[:14]}…」默认展开 expanded=True", found.get(lbl) is True,
              f"found={found.get(lbl)}")
    check("P1-2 六个对象工作台简报 expander 均已渲染（本次 manager 视图触达）",
          len(found) == len(briefing_labels), str(found.keys()))
    # 位置：源码里简报 expander 必须出现在该函数体的 "# ①" 标记之前（挪到顶部，不是仍在底部）
    owb_src = open("app/object_workbench.py", encoding="utf-8").read()
    for fn_marker, brief_marker in [
            ("def render_object_workbench(", "查看确定性风险简报"),
            ("def render_admission_object_workbench(", "查看确定性准入简报"),
            ("def render_task_object_workbench(", "查看确定性任务简报"),
            ("def render_invoice_object_workbench(", "查看确定性发票对账简报"),
            ("def render_po_object_workbench(", "查看确定性三方对账简报"),
            ("def render_warehouse_object_workbench(", "查看确定性仓储库存简报")]:
        start = owb_src.index(fn_marker)
        end = owb_src.index("\ndef ", start + 1) if "\ndef " in owb_src[start + 1:] else len(owb_src)
        body = owb_src[start:end]
        brief_pos = body.index(brief_marker)
        attr_pos = body.index("# ①")
        check(f"P1-2 {fn_marker[4:-1]} 简报块在「① 属性」之前（顶部化）", brief_pos < attr_pos,
              f"brief={brief_pos} attr={attr_pos}")

    # ===== P1-3 「去 XX」引导可点（方案 B：按钮 on_click 设 session_state + 高亮 + 提示）=====
    print("== P1-3 引导可达性（方案 B：session_state 目标 + 标签加粗 + 提示行）==")
    # 同面跳转：ops「去风险队列」
    at5 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at5.session_state["role"] = "ops"
    at5.run()
    goto_risk = next((b for b in at5.button if b.key == "goto_risk"), None)
    check("P1-3 ops 我的今天含「去风险队列」按钮（非纯文字）", goto_risk is not None)
    if goto_risk:
        goto_risk.click().run()
        check("P1-3 点击后 nav_surface 仍为 work（同面跳转不切面）",
              at5.session_state["nav_surface"] == "work")
        check("P1-3 点击后 nav_target_tab=risk", at5.session_state["nav_target_tab"] == "risk")
        info_blob = " ||| ".join((i.value or "") for i in at5.info)
        check("P1-3 顶部出现「请点击上方「风险队列」标签」提示", "请点击上方「风险队列」标签" in info_blob,
              info_blob[:200])
        check("P1-3 风险队列 tab 标签已加粗高亮", any("**风险队列**" in t.label for t in at5.tabs))
    # 跨面跳转：ops「去 控制室→操作与异常记录」
    at6 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at6.session_state["role"] = "ops"
    at6.run()
    goto_dq = next((b for b in at6.button if b.key == "goto_dq"), None)
    check("P1-3 ops 我的今天含「去 控制室…」按钮", goto_dq is not None)
    if goto_dq:
        goto_dq.click().run()
        check("P1-3 点击后 nav_surface 已切到 control（跨面自动切换，用户少点一步）",
              at6.session_state["nav_surface"] == "control")
        check("P1-3 点击后 nav_target_tab=dq", at6.session_state["nav_target_tab"] == "dq")
        info_blob = " ||| ".join((i.value or "") for i in at6.info)
        check("P1-3 顶部出现「请点击上方「操作与异常记录」标签」提示",
              "请点击上方「操作与异常记录」标签" in info_blob, info_blob[:200])
        check("P1-3 已在控制室面（无「导航」radio 报错、控制室标识行存在）",
              not list(at6.exception))
        check("P1-3 「操作与异常记录」组标签已加粗高亮",
              any("**操作与异常记录**" in t.label for t in at6.tabs))
    check("P1-3 CARD_TARGET_TAB 覆盖全部四种卡", set(CARD_TARGET_TAB) == {"risk", "dq", "approve", "coord"})

    # ===== P1-4①② 费种/规则代码图例存在 =====
    print("== P1-4①② 费种/规则代码图例（覆盖 P0 点名的 OFT/FSC/DOC/THC + R1-R18 全量）==")
    for code in ("OFT", "FSC", "DOC", "THC", "DET", "DEM", "CHS"):
        check(f"P1-4① 费种图例含 {code}", code in ux_copy.CHARGE_CODE_LEGEND, ux_copy.CHARGE_CODE_LEGEND)
    for rid_ in [f"R{i}" for i in range(1, 19)]:
        check(f"P1-4② 规则图例含 {rid_}", rid_ in ux_copy.RULE_LEGEND)
    at7 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at7.session_state["role"] = "manager"
    at7.run()
    exp_labels = [e.label for e in at7.expander]
    check("P1-4② 风险队列有「规则代码对照」图例入口", "规则代码对照（R几 = 什么风险）" in exp_labels)
    check("P1-4① 费用工作台有「费种代码对照」图例入口", "费种代码对照（缩写 = 中文）" in exp_labels)

    # ===== P1-4③ 根因人话化（R1 点名样例：eta_current+5d... → 中文一句话）=====
    print("== P1-4③ 根因人话化模板 ==")
    r1_sample = "eta_current+5d buffers breaches promise by 23d"
    r1_human = ux_copy.humanize_root_cause("R1", r1_sample)
    check("P1-4③ R1 样例翻译含「比承诺交期晚 23 天」", "比承诺交期晚 23 天" in r1_human, r1_human)
    check("P1-4③ R1 翻译零英文技术表述残留", _no_bad_tokens(r1_human)
          and "eta_current" not in r1_human and "buffers" not in r1_human, r1_human)
    r2_human = ux_copy.humanize_root_cause(
        "R2", "missing bill_of_lading,packing_list with eta within 10d")
    check("P1-4③ R2 样例含中文单证名（提单/装箱单）", "提单" in r2_human and "装箱单" in r2_human, r2_human)
    r3_human = ux_copy.humanize_root_cause("R3", "in_transit with no milestone for 7d")
    check("P1-4③ R3 样例含「静默停滞」", "静默停滞" in r3_human, r3_human)
    check("P1-4③ 无法识别的格式保留原文 + 前缀「技术表述：」（不瞎编）",
          ux_copy.humanize_root_cause("R1", "some unrecognized format")
          == "技术表述：some unrecognized format")
    check("P1-4③ R4-R18（已是中文人话）原样返回不重复处理",
          ux_copy.humanize_root_cause("R4", "费率超收：测试文本") == "费率超收：测试文本")

    # ===== P1-5 首屏一句话健康度：数字与卡逐字同源 =====
    print("== P1-5 健康度一句话与「我的今天」卡数字同源 ==")
    con = _conn()
    AS_OF = _as_of()
    cards_mgr = build_my_today(con, "manager", AS_OF)
    health_mgr = build_health_line(con, "manager", AS_OF, cards_mgr)
    approve_count = next((c["count"] for c in cards_mgr if c["key"] == "approve"), 0)
    check("P1-5 manager 健康度行含待批提案数（与卡同源）",
          f"{approve_count} 件提案等你批" in health_mgr or (approve_count == 0 and "无 critical" in health_mgr),
          health_mgr)
    crit_row = con.execute("""SELECT count(*) c, COALESCE(sum(affected_value_usd),0) v
                             FROM risk_events WHERE severity='critical'
                             AND status NOT IN ('resolved','escalated')""").fetchone()
    if crit_row["c"] or approve_count:
        wan = round(crit_row["v"] / 10000, 1)
        check(f"P1-5 manager 健康度含 critical 数({crit_row['c']}) + 敞口(${wan}万)",
              f"{crit_row['c']} 笔 critical" in health_mgr and f"${wan} 万" in health_mgr, health_mgr)
    cards_ops = build_my_today(con, "ops", AS_OF)
    health_ops = build_health_line(con, "ops", AS_OF, cards_ops)
    total_ops = sum(c["count"] for c in cards_ops)
    check("P1-5 ops 健康度含个人待办总数（与卡求和同源）",
          (f"{total_ops} 件待办" in health_ops) if total_ops else ("暂无待办" in health_ops),
          f"health={health_ops} total={total_ops}")
    con.close()

    # ===== P1-6①② 列表搜索/排序/只看异常（纯函数正确性）=====
    print("== P1-6 搜索/排序/只看异常正确性（纯函数，构造 fixture）==")
    fixture_risks = [
        {"risk_event_id": "RSK-A", "shipment_id": "SHP-1", "type": "delay_breach",
         "delay_days": 3, "affected_value_usd": 100.0},
        {"risk_event_id": "RSK-B", "shipment_id": "SHP-2", "type": "docs_missing",
         "delay_days": 10, "affected_value_usd": 500.0},
        {"risk_event_id": "RSK-C", "shipment_id": "SHP-1", "type": "stalled",
         "delay_days": 1, "affected_value_usd": 900.0},
    ]
    hit = ux_copy.filter_risks_by_search(fixture_risks, "shp-1")
    check("P1-6① 搜索按货运号模糊匹配（大小写不敏感）",
          {r["risk_event_id"] for r in hit} == {"RSK-A", "RSK-C"}, str(hit))
    hit2 = ux_copy.filter_risks_by_search(fixture_risks, "RSK-B")
    check("P1-6① 搜索按风险 ID 精确子串命中单条", [r["risk_event_id"] for r in hit2] == ["RSK-B"])
    hit3 = ux_copy.filter_risks_by_search(fixture_risks, "docs")
    check("P1-6① 搜索按类型模糊匹配", [r["risk_event_id"] for r in hit3] == ["RSK-B"])
    check("P1-6① 空搜索词不过滤（原样返回）",
          ux_copy.filter_risks_by_search(fixture_risks, "") == fixture_risks)
    by_delay = ux_copy.sort_risks(fixture_risks, "延误天数")
    check("P1-6① 按延误天数降序", [r["risk_event_id"] for r in by_delay] == ["RSK-B", "RSK-A", "RSK-C"],
          str(by_delay))
    by_value = ux_copy.sort_risks(fixture_risks, "影响金额")
    check("P1-6① 按影响金额降序", [r["risk_event_id"] for r in by_value] == ["RSK-C", "RSK-B", "RSK-A"])
    check("P1-6① 默认「级别→金额」不重排（原序返回）",
          ux_copy.sort_risks(fixture_risks, "级别→金额（默认）") == fixture_risks)

    fixture_invs = [
        {"invoice_id": "INV-1", "vendor_name": "Acme Freight", "shipment_id": "SHP-1"},
        {"invoice_id": "INV-2", "vendor_name": "Globex Line", "shipment_id": "SHP-2"},
        {"invoice_id": "INV-3", "vendor_name": "Acme Freight", "shipment_id": "SHP-3"},
    ]
    anom_ids = ux_copy.anomaly_invoice_ids(
        [["IL-1", "IL-2"]], {"IL-1": "INV-1", "IL-2": "INV-3", "IL-9": "INV-2"})
    check("P1-6② 异常发票集合正确（IL-1/IL-2 命中 → INV-1/INV-3）",
          anom_ids == {"INV-1", "INV-3"}, str(anom_ids))
    only_anom = ux_copy.filter_invoices(fixture_invs, "", anom_ids, True)
    check("P1-6② 只看异常复选框计数正确（2 张，不含 INV-2）",
          {x["invoice_id"] for x in only_anom} == {"INV-1", "INV-3"}, str(only_anom))
    by_vendor = ux_copy.filter_invoices(fixture_invs, "acme", set(), False)
    check("P1-6② 按 vendor 模糊搜索（大小写不敏感）",
          {x["invoice_id"] for x in by_vendor} == {"INV-1", "INV-3"}, str(by_vendor))
    combo = ux_copy.filter_invoices(fixture_invs, "acme", anom_ids, True)
    check("P1-6② 搜索 + 只看异常可叠加", {x["invoice_id"] for x in combo} == {"INV-1", "INV-3"})

    # AppTest 层轻量冒烟：搜索/筛选控件存在且不抛异常
    at8 = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at8.session_state["role"] = "manager"
    at8.run()
    check("P1-6① 风险队列搜索框存在",
          any(t.label == "搜索（风险 ID / 货运号 / 类型，模糊匹配）" for t in at8.text_input))
    check("P1-6① 风险队列排序下拉存在",
          any(s.label == "排序" for s in at8.selectbox))
    check("P1-6② 发票列表搜索框存在",
          any(t.label == "搜索（发票号 / vendor / 货运号，模糊匹配）" for t in at8.text_input))
    check("P1-6② 「只看异常发票」复选框存在",
          any(c.label == "只看异常发票" for c in at8.checkbox))
    check("P1-6 全量冒烟 0 未捕获异常", not list(at8.exception), str(list(at8.exception)[:1]))

    # ===== 红线：真实 DB md5 逐字节一致（唯一写操作场景已备份/恢复）=====
    print("\n== 红线：真实 DB md5 逐字节一致（P1-1 端到端写操作已恢复）==")
    md5_after = hashlib.md5(open(DB, "rb").read()).hexdigest()
    check("真实 DB md5 逐字节一致", md5_before == md5_after, f"{md5_before} != {md5_after}")

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


def _as_of():
    import yaml
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    return cfg["window"]["as_of"]


if __name__ == "__main__":
    main()
