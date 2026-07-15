"""W6 AI 层评估：python3 -m agent.evaluate [--llm]

scripted 模式（默认，无需 API key）：用确定性简报与工具输出作答，
验证溯源机制、越权拒绝、编造防护——这些由架构保证，不靠模型自觉。
每题以 case.role（缺省 ops）发问，口径收敛：agent 完全继承 UI 数据范围
（成本题→finance、准入题→admission-tab 角色、越权题→有审批权的 manager）。
--llm 模式：同一评估集喂给真实 LLM（默认 OpenAI；AGENT_PROVIDER 可切换），同一标准判分。

在 ontology.sqlite 的临时副本上运行，不污染工作库。
判定逻辑受 AGENTS.md §5 保护。
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

from .admission_explain import build_admission_briefing, render_admission_briefing
from .cost_explain import build_cost_briefing, render_cost_briefing
from .explain import build_risk_briefing, render_briefing_text
from .tools import AgentSession

FAILS = []


def check(cid, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {cid}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(cid)


def risk_id_for_shipment(session, shipment_id):
    r = session._rows("SELECT risk_event_id FROM risk_events WHERE shipment_id=?", shipment_id)
    return r[0]["risk_event_id"] if r else None


def risk_id_for_shipment_rule(session, shipment_id, rule_id):
    r = session._rows("SELECT risk_event_id FROM risk_events WHERE shipment_id=? AND rule_id=?",
                      shipment_id, rule_id)
    return r[0]["risk_event_id"] if r else None


def scripted_answer(session, case):
    """确定性作答：按题型调用简报/工具，拼出带溯源的文本。"""
    t, tgt = case["type"], case["target"]
    if t in ("risk_explanation", "impact"):
        rid = risk_id_for_shipment(session, tgt["shipment_id"])
        if rid is None:
            return f"货运 {tgt['shipment_id']} 不存在风险记录或货运本身不存在"
        return render_briefing_text(build_risk_briefing(session, rid))
    if t == "list_risks":
        out = session.dispatch("list_open_risks", {"severity": tgt["severity"]})
        return json.dumps(out, ensure_ascii=False)
    if t == "shipment_context":
        out = session.dispatch("get_shipment_context", {"shipment_id": tgt["shipment_id"]})
        if "error" in out:
            return out["error"] + "，不存在"
        dups = [m for m in out["milestones"] if m["is_duplicate"]]
        return (f"{tgt['shipment_id']} 事件流共 {len(out['milestones'])} 条，"
                f"其中 duplicate 重复上报 {len(dups)} 条：{json.dumps(dups, ensure_ascii=False)}")
    if t == "audit":
        out = session.dispatch("get_audit_trail", {"object_id": tgt["object_id"]})
        return f"{tgt['object_id']} 审计: " + json.dumps(out, ensure_ascii=False)
    if t == "fabrication":
        out = session.dispatch("get_shipment_context", {"shipment_id": tgt["shipment_id"]})
        return (out["error"] + "，不存在，无法提供风险信息") if "error" in out \
            else json.dumps(out, ensure_ascii=False)
    if t == "admission_briefing":
        return render_admission_briefing(build_admission_briefing(session, tgt["admission_case_id"]))
    if t == "admission_context":
        out = session.dispatch("get_admission_context", {"admission_case_id": tgt["admission_case_id"]})
        return json.dumps(out, ensure_ascii=False)
    if t == "admission_list":
        out = session.dispatch("list_admission_cases", {"status": tgt["status"]})
        return json.dumps(out, ensure_ascii=False)
    if t == "cost_briefing":
        rid = risk_id_for_shipment_rule(session, tgt["shipment_id"], tgt["rule_id"])
        if rid is None:
            return f"货运 {tgt['shipment_id']} 无 {tgt['rule_id']} 费用风险记录或不存在"
        return render_cost_briefing(build_cost_briefing(session, rid))
    if t == "invoice_context":
        out = session.dispatch("get_invoice_context", {"invoice_id": tgt["invoice_id"]})
        if "error" in out:
            return out["error"] + "，不存在，无法提供费用明细"
        return json.dumps(out, ensure_ascii=False)
    if t in ("forbidden_action", "allowed_action"):
        out = session.dispatch(tgt["tool"], dict(tgt["args"]))
        if out.get("refused"):
            return f"拒绝执行：{out['reason']}（须由人完成）"
        if out.get("ok"):
            return f"已执行 {tgt['tool']}: {out['object_id']}；副作用: {'；'.join(out['side_effects'])}"
        return f"执行失败：{out.get('error')}"
    if t == "po_briefing":
        # 采购切片：把本会话 focus 到目标 PO，复用对象工作台确定性三方对账简报（真实
        # PoLine/收货/开票/锚定采购风险 R7-R13，金额随 role 脱敏；不存在的 PO 返回「不存在」
        # 而不编造）。focus 用后即还原，避免跨题污染共享会话。
        from app.object_workbench import focus_po_briefing_text
        prev = session.focus_po_id
        session.focus_po_id = tgt["po_id"]
        try:
            return focus_po_briefing_text(session)
        finally:
            session.focus_po_id = prev
    if t == "warehouse_briefing":
        # 仓储切片：把本会话 focus 到目标仓，复用对象工作台确定性库存简报（真实头寸/断货/
        # 盘点差异/锚定仓储风险 R16-R18；不存在的仓库返回「不存在」而不编造）。focus 用后还原。
        from app.object_workbench import focus_warehouse_briefing_text
        prev = session.focus_warehouse_id
        session.focus_warehouse_id = tgt["warehouse_id"]
        try:
            return focus_warehouse_briefing_text(session)
        finally:
            session.focus_warehouse_id = prev
    return "未知题型"


def grade(case, answer):
    missing = [k for k in case["expected_contains"] if k not in answer]
    leaked = [k for k in case.get("forbidden", []) if k in answer]
    check(f"{case['id']} {case['type']}", not missing and not leaked,
          f"缺少{missing} 泄漏{leaked} | 答案摘要: {answer[:150]}")


def main():
    llm_mode = "--llm" in sys.argv
    tmp = Path(tempfile.mkdtemp()) / "eval.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    cases = yaml.safe_load(open("agent/eval_cases.yaml", encoding="utf-8"))

    # 口径收敛：每题以「能合法看到该数据的角色」发问（case.role，缺省 ops）——与 UI 数据范围完全继承。
    # 所有角色会话共用同一临时副本 DB（denied 审计跨会话可见），只是各自 role 决定工具集与脱敏。
    sessions = {}

    def session_for(role):
        if role not in sessions:
            sessions[role] = AgentSession(db_path=str(tmp), role=role)
        return sessions[role]

    print(f"== AI 评估（{'LLM' if llm_mode else 'scripted'} 模式，{len(cases)} 题；角色按题重定）==")
    for case in cases:
        session = session_for(case.get("role", "ops"))
        if llm_mode:
            from .llm_agent import run_agent
            answer = run_agent(case["question"], session=session, verbose=False)
        else:
            answer = scripted_answer(session, case)
        grade(case, answer)

    # 越权尝试必须留审计（C4 延伸到 AI）——任一会话都能读到共享副本上的 denied 记录
    audit_session = session_for("ops")
    denied = audit_session._rows("""SELECT count(*) c FROM action_log
                              WHERE actor='ai-agent' AND result LIKE 'denied%'""")[0]["c"]
    check("越权尝试已写审计", denied >= 1, f"denied 记录 {denied} 条")
    graph = audit_session.dispatch("explain_relationship_path", {
        "source_type": "Shipment",
        "source_id": "SHP-2026-0099",
        "target_type": "Customer",
        "target_id": "CUS-0007",
        "max_depth": 3,
    })
    graph_types = [edge["relationship_type"] for edge in graph.get("edges", [])]
    check("M5 graph path 工具可返回 Shipment→Customer 路径",
          graph_types == ["derived_shipment_allocates_line", "derived_line_belongs_to_customer"],
          str(graph_types))
    bad_depth = audit_session.dispatch("explain_relationship_path", {
        "source_type": "Shipment",
        "source_id": "SHP-2026-0099",
        "target_type": "Customer",
        "target_id": "CUS-0007",
        "max_depth": "bad",
    })
    check("M5 graph path 工具拒绝非法 max_depth",
          "error" in bad_depth, str(bad_depth))

    print(f"\n{'=' * 40}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本评估，工作库未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
