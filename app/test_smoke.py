"""冒烟测试（健壮性 pass）：python3 -m app.test_smoke

锁定两类保证：
① **7 角色 UI 干净加载**：ops/cs/finance/manager/sales/compliance/procurement 主界面各自 AppTest 加载，
   在 **真实库** 与 **空库**（0 行，schema 保留）上都断言 **0 未捕获异常**——空/边界输入优雅降级，不抛 traceback。
② **空/边界读取路径优雅**：不存在的对象 id / 未知类型 / 缺失 focus / None/空串/超长参数 → 结构化 error 或
   友好提示，而非崩溃；并回归锁定本次修的两个真实崩溃点：
   - 采购/仓储风险（RiskEvent.shipment_id 可空）经 Task 富工作台看简报（原 build_risk_briefing 直接
     ctx["shipment"] → KeyError）；
   - 空/无演示锚点时 render_adm_tab 的 .index("AC-2026-0031") → ValueError。

**红线**：优雅降级不得放宽授权——空库上 agent 仍不能审批（FORBIDDEN 护栏）、越权/越域仍被拒。
只读断言 + 临时副本/备份还原（绝不污染 data/ontology.sqlite；结束校验其 md5 不变）。
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

from . import object_workbench as owb
from . import standard_object_view as sov
from .executive_view import build_executive_summary
from agent.tools import AgentSession, FORBIDDEN_TOOLS

DB = "data/ontology.sqlite"
ROLES = ("ops", "cs", "finance", "manager", "sales", "compliance", "procurement")
FAILS = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _empty_copy_of(src):
    """把 src 复制到临时文件并清空所有表（schema 保留），返回临时路径。"""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    shutil.copy(src, path)
    c = sqlite3.connect(path)
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        try:
            c.execute(f"DELETE FROM {t}")
        except sqlite3.Error:
            pass
    c.commit()
    c.close()
    return path


def _apptest_roles(label):
    """对 6 角色分别 AppTest 加载当前 DB，断言各 0 未捕获异常。"""
    from streamlit.testing.v1 import AppTest
    for role in ROLES:
        at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
        at.session_state["role"] = role
        at.run()
        excs = list(at.exception)
        check(f"[{label}] role={role} 主界面 0 未捕获异常", not excs, str(excs[:1]))


def main():
    md5_before = hashlib.md5(open(DB, "rb").read()).hexdigest()

    # ===== ① 真实库：6 角色干净加载 =====
    print("== ① 真实库 · 6 角色 AppTest（0 未捕获异常）==")
    _apptest_roles("real")

    # ===== ② 空库：6 角色干净加载（swap + finally 还原）=====
    print("\n== ② 空库（0 行）· 6 角色 AppTest（空/边界 UI 优雅降级）==")
    bak = tempfile.mkstemp(suffix=".sqlite")[1]
    shutil.copy(DB, bak)
    try:
        empty = _empty_copy_of(DB)
        shutil.copy(empty, DB)      # 让 app 硬编码路径读到空库
        _apptest_roles("empty")
        os.remove(empty)
    finally:
        shutil.copy(bak, DB)         # 无论如何还原真实库
        os.remove(bak)
    md5_after = hashlib.md5(open(DB, "rb").read()).hexdigest()
    check("② 真实 DB 已按字节还原（swap 未污染）", md5_before == md5_after,
          f"{md5_before} != {md5_after}")

    # ===== ③ 不存在的对象 id / 未知类型 → 结构化 error（不抛）=====
    print("\n== ③ 富工作台 / 标准视图：不存在 id + 未知类型 → 友好 error ==")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    builders = [
        ("build_workbench", lambda: owb.build_workbench(con, "RSK-NOPE", "ops")),
        ("build_admission_workbench", lambda: owb.build_admission_workbench(con, "AC-NOPE", "compliance")),
        ("build_task_workbench", lambda: owb.build_task_workbench(con, "TASK-NOPE", "ops")),
        ("build_invoice_workbench", lambda: owb.build_invoice_workbench(con, "INV-NOPE", "finance")),
        ("build_po_workbench", lambda: owb.build_po_workbench(con, "PO-NOPE", "ops")),
        ("build_warehouse_workbench", lambda: owb.build_warehouse_workbench(con, "WH-NOPE", "ops")),
    ]
    for name, fn in builders:
        try:
            r = fn()
            check(f"③ {name}(缺失 id) → error dict 不抛", isinstance(r, dict) and "error" in r, str(r)[:60])
        except Exception as e:  # noqa: BLE001
            check(f"③ {name}(缺失 id) → error dict 不抛", False, f"raised {type(e).__name__}: {e}")
    for name, ot, oid in [("不存在 id", "Shipment", "NOPE"), ("未知类型", "NotAType", "x"),
                          ("None id", "Shipment", None), ("空串 id", "Shipment", "")]:
        try:
            r = sov.build_standard_view(con, ot, oid, "ops")
            check(f"③ standard_view({name}) → error dict 不抛", "error" in r, str(r)[:60])
        except Exception as e:  # noqa: BLE001
            check(f"③ standard_view({name}) → error dict 不抛", False, f"raised {type(e).__name__}: {e}")

    # ===== ④ 对象级 agent focus 到缺失 id → 友好简报（不抛）=====
    print("\n== ④ 对象级 agent focus 缺失 id → 友好简报（不抛）==")
    focus_probes = [
        ("risk", lambda: owb.focus_briefing_text(owb.make_agent_session("ops", "RSK-NOPE"))),
        ("admission", lambda: owb.focus_admission_briefing_text(owb.make_admission_agent_session("compliance", "AC-NOPE"))),
        ("task", lambda: owb.focus_task_briefing_text(owb.make_task_agent_session("ops", "TASK-NOPE"))),
        ("invoice", lambda: owb.focus_invoice_briefing_text(owb.make_invoice_agent_session("finance", "INV-NOPE"))),
        ("po", lambda: owb.focus_po_briefing_text(owb.make_po_agent_session("ops", "PO-NOPE"))),
        ("warehouse", lambda: owb.focus_warehouse_briefing_text(owb.make_warehouse_agent_session("ops", "WH-NOPE"))),
    ]
    for name, fn in focus_probes:
        try:
            r = fn()
            check(f"④ focus_{name}(缺失) → 友好字符串不抛", isinstance(r, str) and "不存在" in r, str(r)[:60])
        except Exception as e:  # noqa: BLE001
            check(f"④ focus_{name}(缺失) → 友好字符串不抛", False, f"raised {type(e).__name__}: {e}")

    # ===== ⑤ 读工具 None / 空串 / 超长参数 → 结构化 error（不抛）=====
    print("\n== ⑤ 读工具 None/空串/超长 → 结构化 error（不抛）==")
    s = AgentSession(role="ops")
    LONG = "X" * 5000
    read_probes = [("get_risk", None), ("get_risk", ""), ("get_risk", LONG),
                   ("get_shipment_context", None), ("get_impact_chain", None),
                   ("get_admission_context", None), ("get_invoice_context", None)]
    for tool, arg in read_probes:
        try:
            r = getattr(s, tool)(arg)
            check(f"⑤ {tool}({str(arg)[:6]}…) → error dict 不抛", isinstance(r, dict) and "error" in r, str(r)[:50])
        except Exception as e:  # noqa: BLE001
            check(f"⑤ {tool} → error dict 不抛", False, f"raised {type(e).__name__}: {e}")

    # ===== ⑥ 回归锁定：采购/仓储风险（shipment 可空）经 Task 简报优雅（原 KeyError）=====
    print("\n== ⑥ 回归锁：非货运锚定风险经 Task 富工作台简报优雅降级（不 KeyError）==")
    tmp = _empty_copy_of(DB)  # 复用副本机制：从真实库复制再灌最小数据
    shutil.copy(DB, tmp)      # 用真实库副本（含采购/仓储风险），在其上插临时任务
    tc = sqlite3.connect(tmp)
    tc.row_factory = sqlite3.Row
    got_null_ship = False
    for rule in ("R7", "R16", "R12"):  # 采购延误 / 仓储断货 / 预付款——均 shipment_id 可空
        row = tc.execute("SELECT risk_event_id FROM risk_events WHERE rule_id=? AND shipment_id IS NULL LIMIT 1",
                         (rule,)).fetchone()
        if not row:
            continue
        got_null_ship = True
        tid = f"TASK-SMOKE-{rule}"
        tc.execute("INSERT INTO tasks(task_id,risk_event_id,title,status,assignee_role,priority,due_at) "
                   "VALUES(?,?,'smoke','assigned','ops','P2','2026-08-10')", (tid, row["risk_event_id"]))
        tc.commit()
        sess = owb.make_task_agent_session("ops", tid, db_path=tmp)
        try:
            r = owb.focus_task_briefing_text(sess)
            check(f"⑥ Task 简报(父风险 {rule} 无货运) 优雅不抛", isinstance(r, str) and "未锚定有效货运" in r, str(r)[-70:])
        except Exception as e:  # noqa: BLE001
            check(f"⑥ Task 简报(父风险 {rule} 无货运) 优雅不抛", False, f"raised {type(e).__name__}: {e}")
    check("⑥ 数据含非货运锚定风险（回归有意义）", got_null_ship)
    tc.close()
    os.remove(tmp)

    # ===== ⑦ Executive 聚合在空库上不除零 / 不崩，计数归 0 =====
    print("\n== ⑦ Executive KPI 聚合：空库（0 风险/0 任务）不崩、计数为 0 ==")
    empty = _empty_copy_of(DB)
    ec = sqlite3.connect(empty)
    ec.row_factory = sqlite3.Row
    try:
        summ = build_executive_summary(ec)
        ok = (summ["cross"]["total_open_risk"] == 0
              and summ["cross"]["tasks_by_sla"]["overdue"] == 0
              and summ["delay"]["open_risk"] == 0)
        check("⑦ build_executive_summary(空库) 返回 0 计数不崩", ok, str(summ["cross"])[:60])
    except Exception as e:  # noqa: BLE001
        check("⑦ build_executive_summary(空库) 不崩", False, f"raised {type(e).__name__}: {e}")
    ec.close()

    # ===== ⑧ 红线：空数据分支不放宽授权（agent 仍不能审批 / 越权仍被拒）=====
    print("\n== ⑧ 红线：空库上授权护栏仍生效（降级不越权）==")
    check("⑧ FORBIDDEN_TOOLS 仍含审批/关闭（常量未削弱）",
          {"approve_mitigation", "close_risk_event"} <= FORBIDDEN_TOOLS)
    es = AgentSession(db_path=empty, role="manager")  # 空库 + 经理
    r1 = es.dispatch("approve_mitigation", {"task_id": "T", "decision": "approved"})
    check("⑧ 空库 agent(manager) approve_mitigation → 拒绝（FORBIDDEN 护栏）", r1.get("refused") is True, str(r1)[:60])
    r2 = es.dispatch("close_risk_event", {"risk_event_id": "R"})
    check("⑧ 空库 agent close_risk_event → 拒绝", r2.get("refused") is True, str(r2)[:60])
    eo = AgentSession(db_path=empty, role="ops")
    # ops 无 B1 建案权限 → 越权写被拒（不因空库放宽 ROLE_PERMS/ADM_PERMS gate）
    r3 = eo.dispatch("create_admission_case", {"customer_id": "C", "sku_id": "K",
                     "request_type": "new_sku", "incoterm_candidate": "FOB",
                     "target_launch_date": "2026-09-01", "estimated_monthly_qty": 100})
    check("⑧ 空库 agent(ops) 越权写 create_admission_case → 拒绝", r3.get("refused") is True, str(r3)[:60])
    os.remove(empty)
    con.close()

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（真实库经 swap 后按字节还原；空/边界分支在临时副本上验证，未污染 data/）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
