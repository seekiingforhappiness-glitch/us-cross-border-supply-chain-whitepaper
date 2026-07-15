"""P0 阻断修复验证（陌生人测试四件）：python3 -m app.test_ux_p0_fixes

对应 docs/research/2026-07-13-ux-stranger-test.md 的 P0 修复：
 P0-1 财务（有提案权角色）任务处理台入口 + 发票/PO 无动作时人话引导（消断头路）
 P0-2 AI 故障人话降级（异常→人话文案 + 自动展开简报 + 自检 reason 人话；界面无「退出码/CLI」黑话）
 P0-3 表单事前检查（有非终态任务→派单/关闭改提示卡 + 仅 false_alarm；非 manager 不渲染审批块；费用台醒目只读条）
 P0-4 风险队列默认选中 = 当前排序列表首行（不再钉演示锚点 SHP-2026-0099）

红线：只读断言（AppTest 读真库 + 纯函数/注入 fake st）；结束校验 data/ontology.sqlite md5 未变；
ROLE_PERMS 未改（导航/UI 对齐权限真源，不放宽任何写权限）。
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

from .actions import ROLE_PERMS
from .rbac_nav import ROLE_WORKSPACE
from . import object_workbench as owb
from agent import llm_agent
from agent.egress_gate import log_llm_call

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


# ---- 最小 fake streamlit：录制所有渲染文本，验证人话/无黑话，无需真实 runtime / CLI ----
class _FakeCtx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSt:
    def __init__(self):
        self.texts = []

    def _rec(self, *a, **k):
        for x in a:
            if isinstance(x, str):
                self.texts.append(x)

    caption = markdown = warning = info = text = error = success = write = _rec

    def spinner(self, *a, **k):
        return _FakeCtx()

    def expander(self, *a, **k):
        return _FakeCtx()

    @property
    def blob(self):
        return " ||| ".join(self.texts)


def _at_text(at):
    """收集 AppTest 一次 run 的全部可见文本（markdown/caption/info/warning/success/error）。"""
    out = []
    for kind in ("markdown", "caption", "info", "warning", "success", "error"):
        try:
            out += [(e.value or "") for e in getattr(at, kind)]
        except Exception:  # noqa: BLE001 —— 某类元素不存在
            pass
    return " ||| ".join(out)


def main():
    md5_before = hashlib.md5(open(DB, "rb").read()).hexdigest()
    con = _conn()

    # ===== P0-1 导航对齐 + 发票/PO 无动作人话引导 =====
    print("== P0-1 有提案权角色含任务处理台 + 发票/PO 无动作人话引导 ==")
    for r in sorted(ROLE_PERMS["ProposeMitigation"]):
        if r in ROLE_WORKSPACE:
            check(f"P0-1 {r} 工作台含 task（对齐 ROLE_PERMS.ProposeMitigation，消断头路）",
                  "task" in ROLE_WORKSPACE[r], str(ROLE_WORKSPACE[r]))
    h_fin = owb._actions_or_hint([], "finance", "DEFAULT")
    h_sales = owb._actions_or_hint([], "sales", "DEFAULT")
    h_has = owb._actions_or_hint(["ProposeMitigation"], "finance", "DEFAULT")
    check("P0-1 finance 无动作→人话引导含「任务处理台」「提交处置方案」",
          "任务处理台" in h_fin and "提交处置方案" in h_fin, h_fin)
    check("P0-1 finance 引导不再是干巴巴的「（无）」", "（无" not in h_fin, h_fin)
    check("P0-1 无提案权(sales) 无动作→仍回退领域默认说明（不误导可提案）",
          h_sales == "DEFAULT", h_sales)
    check("P0-1 有动作→原样顿号连接（不吞真实可用动作）", h_has == "ProposeMitigation", h_has)

    # ===== P0-2 AI 自检 reason 人话 + 异常路径人话降级（无退出码/CLI）=====
    print("== P0-2 AI 自检 reason 人话 + 异常路径人话文案（界面无退出码/CLI）==")
    tmp = tempfile.mkstemp(suffix=".sqlite")[1]
    shutil.copy(DB, tmp)
    log_llm_call(tmp, call_type="briefing", provider="claude_cli", status="error",
                 error="claude CLI 退出码 1：Not logged in")
    avail, reason = llm_agent.probe_cli_availability(tmp)
    os.remove(tmp)
    check("P0-2 上次调用失败→自检判定不可用（据 llm_calls，不真调 CLI）", avail is False, str(avail))
    check("P0-2 自检 reason 为面向用户中文（无「退出码」「CLI」「stderr」）",
          reason and all(w not in reason for w in ("退出码", "CLI", "stderr")), reason)

    def _raise(*a, **k):
        raise RuntimeError("claude CLI 退出码 1：Not logged in")

    fake = FakeSt()
    real_streamlit = sys.modules.get("streamlit")
    real_answer = llm_agent.answer_over_context
    sys.modules["streamlit"] = fake
    llm_agent.answer_over_context = _raise
    try:
        owb._render_object_llm_answer("这票为什么延误？", "确定性简报：RSK-0044 ETA 超期 …",
                                      "ops", db_path=DB)
    finally:
        llm_agent.answer_over_context = real_answer
        if real_streamlit is not None:
            sys.modules["streamlit"] = real_streamlit
        else:
            sys.modules.pop("streamlit", None)
    blob = fake.blob
    check("P0-2 异常→人话文案「AI 助手暂时不可用，已切换为数据简报」",
          "AI 助手暂时不可用" in blob and "数据简报" in blob, blob[:100])
    check("P0-2 异常界面绝无「退出码 / CLI / Not logged in / RuntimeError」黑话或异常泄漏",
          all(w not in blob for w in ("退出码", "CLI", "Not logged in", "RuntimeError")), blob[:160])
    check("P0-2 降级自动展开确定性简报（简报文本随人话一并呈现）", "RSK-0044" in blob, blob[:160])
    # 成功路径：正常渲染回答，不误报降级
    fake2 = FakeSt()
    real_streamlit = sys.modules.get("streamlit")
    sys.modules["streamlit"] = fake2
    llm_agent.answer_over_context = lambda *a, **k: "延误 7 天，建议改期（RSK-0044）。"
    try:
        owb._render_object_llm_answer("怎么办？", "确定性简报：RSK-0044 …", "ops", db_path=DB)
    finally:
        llm_agent.answer_over_context = real_answer
        if real_streamlit is not None:
            sys.modules["streamlit"] = real_streamlit
        else:
            sys.modules.pop("streamlit", None)
    check("P0-2 成功路径正常出答案、不误报「暂时不可用」",
          "建议改期" in fake2.blob and "AI 助手暂时不可用" not in fake2.blob, fake2.blob[:100])

    # ===== 动态锚点（AppTest 读真库）=====
    first_risk = con.execute(
        """SELECT r.risk_event_id FROM risk_events r JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE r.status NOT IN ('resolved','escalated')
           ORDER BY CASE r.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                    r.affected_value_usd DESC LIMIT 1""").fetchone()["risk_event_id"]
    busy = con.execute(
        """SELECT r.risk_event_id FROM risk_events r JOIN tasks t ON t.risk_event_id=r.risk_event_id
           JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE t.status NOT IN ('done','cancelled') AND r.status NOT IN ('resolved','escalated')
           ORDER BY r.risk_event_id LIMIT 1""").fetchone()
    free = con.execute(
        """SELECT r.risk_event_id FROM risk_events r JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE r.status='open' AND NOT EXISTS
             (SELECT 1 FROM tasks t WHERE t.risk_event_id=r.risk_event_id
              AND t.status NOT IN ('done','cancelled'))
           ORDER BY r.risk_event_id LIMIT 1""").fetchone()
    pend = con.execute(
        "SELECT task_id FROM tasks WHERE approval_status='pending' ORDER BY task_id LIMIT 1").fetchone()

    from streamlit.testing.v1 import AppTest

    # ===== P0-4 默认选中 = 列表首行 =====
    print("== P0-4 风险队列默认选中 = 当前排序列表首行（AppTest·manager）==")
    at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at.session_state["role"] = "manager"
    at.run()
    check(f"P0-4 manager 默认无异常", not list(at.exception), str(list(at.exception)[:1]))
    sel_val = next((s.value for s in at.selectbox if s.label == "查看风险"), None)
    check(f"P0-4 默认选中 = 排序后首行 {first_risk}（非固定演示锚点）",
          sel_val == first_risk, f"{sel_val} != {first_risk}")

    # ===== P0-3① 有非终态任务→派单/关闭改提示卡（+仅 false_alarm）；无任务→正常两表单 =====
    print("== P0-3① 活跃任务→提示卡 + 仅 false_alarm；无任务→正常派单/关闭（AppTest）==")
    if busy:
        busy_id = busy["risk_event_id"]
        busy_task = con.execute("""SELECT task_id FROM tasks WHERE risk_event_id=?
                                   AND status NOT IN ('done','cancelled') LIMIT 1""",
                                (busy_id,)).fetchone()["task_id"]
        at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
        at.session_state["role"] = "manager"
        at.session_state["risk_sel"] = busy_id
        at.run()
        txt = _at_text(at)
        check(f"P0-3① {busy_id} 有活跃任务→提示卡含任务号 {busy_task} 与「已有任务…在处理」",
              "已有任务" in txt and busy_task in txt and "在处理" in txt, txt[:160])
        check("P0-3① 活跃任务下保留 false_alarm 强制关闭路径（「误报强制关闭」表单在）",
              "误报强制关闭" in txt, "missing false_alarm form")
        check("P0-3① 活跃任务下常规「派发任务（A3，运营）」表单不渲染（禁掉必被拒的入口）",
              "派发任务（A3，运营）" not in txt, "assign form should be hidden")
    else:
        check("P0-3① 数据含带活跃任务的风险（用例有意义）", False, "no busy risk found")
    if free:
        free_id = free["risk_event_id"]
        at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
        at.session_state["role"] = "manager"
        at.session_state["risk_sel"] = free_id
        at.run()
        txt = _at_text(at)
        check(f"P0-3① {free_id} 无活跃任务→正常「派发任务（A3，运营）」与「关闭风险（A6，运营）」表单在",
              "派发任务（A3，运营）" in txt and "关闭风险（A6，运营）" in txt, txt[:160])
        check("P0-3① 无活跃任务→不出「已有任务…在处理」提示卡",
              "已有任务" not in txt, "unexpected active-task card")
    else:
        check("P0-3① 数据含无任务的开放风险（用例有意义）", False, "no free risk found")

    # ===== P0-3② 审批块仅 manager 渲染（非 manager 整块不出）=====
    print("== P0-3② 审批块仅 manager 渲染（非 manager 不渲染，AppTest·同一待批任务）==")
    if pend:
        ptid = pend["task_id"]
        for role, should in (("manager", True), ("ops", False)):
            at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
            at.session_state["role"] = role
            at.session_state["task_scope_mode"] = "all"  # 保证该待批任务在范围内可选
            at.run()
            tsb = [s for s in at.selectbox if s.label == "处理任务"]
            if tsb:
                tsb[0].set_value(ptid).run()
            at2 = at
            txt = _at_text(at2)
            has_appr = "审批（A5，仅经理）" in txt
            check(f"P0-3② role={role} 待批任务 {ptid} 审批块{'渲染' if should else '不渲染'}",
                  has_appr == should, f"has_appr={has_appr} txt={txt[:80]}")
    else:
        check("P0-3② 数据含 pending 待批任务（用例有意义）", False, "no pending task found")

    # ===== P0-3③ 费用工作台醒目只读条 =====
    print("== P0-3③ 费用工作台顶部醒目只读条（AppTest·finance）==")
    at = AppTest.from_file("app/streamlit_app.py", default_timeout=120)
    at.session_state["role"] = "finance"
    at.run()
    txt = _at_text(at)
    check("P0-3③ 费用台含「只读视图」醒目提示且指向「任务处理台」",
          "只读视图" in txt and "任务处理台" in txt, txt[:160])

    con.close()

    # ===== 红线：ROLE_PERMS 未改 + 真库 md5 未变 =====
    print("== 红线：ROLE_PERMS 未改（对齐真源不放宽）+ 真库 md5 未变 ==")
    check("ROLE_PERMS 四键与基线一致（导航/UI 对齐真源，硬 gate 未削弱）",
          ROLE_PERMS == {"AssignTask": {"ops", "system"},
                         "ProposeMitigation": {"ops", "cs", "finance", "procurement"},
                         "ApproveMitigation": {"manager"},
                         "CloseRiskEvent": {"ops"}}, str(ROLE_PERMS))
    md5_after = hashlib.md5(open(DB, "rb").read()).hexdigest()
    check("真实 DB md5 逐字节一致（AppTest 纯读；临时副本已删）", md5_before == md5_after,
          f"{md5_before} != {md5_after}")

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
