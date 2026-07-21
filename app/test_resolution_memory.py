"""C1 处置记忆无头测试：python3 -m app.test_resolution_memory

在 ontology.sqlite 的临时副本上验证 C1 最小纵向切片（G1 阶段门）：
① 决定写入（approve 成功路径，adopted/rejected 都归档）+ 关闭回填（outcome/耗时/质量标签）全链
② 数字可回查一致：render 的"同类 N 次"与表 count 现算一致（禁缓存/编造）
③ 屏蔽（status=voided）后 find_similar 不返回、不暗中引用（可解释检索负向测试）
④ 无先例如实返回"首例"
⑤ quality_label 三值校验（非法值拒绝且不关闭风险）
⑥ 回归：approve 权限仍仅 manager；AI 无审批/关闭工具、无处置记忆写工具、只多一个只读检索
"""
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

import yaml

from .actions import (ROLE_PERMS, approve_mitigation, assign_task, close_risk_event,
                      propose_mitigation)
from agent.tools import FORBIDDEN_TOOLS, TOOL_DEFS, AgentSession, allowed_tools_for_role
from engine.resolution_memory import find_similar, lane_for_shipment, render_precedent_block

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    cfg = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
    as_of = cfg["window"]["as_of"]
    close_as_of = (date.fromisoformat(as_of) + timedelta(days=3)).isoformat()
    tmp = Path(tempfile.mkdtemp()) / "memory.sqlite"
    shutil.copy("data/ontology.sqlite", tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    def q1(sql, *a):
        return con.execute(sql, a).fetchone()

    def mem_rows(risk_id):
        return con.execute("""SELECT * FROM resolution_memory WHERE risk_event_id=?
                              ORDER BY memory_id""", (risk_id,)).fetchall()

    # ---- 前置锚点（动态发现，避免硬编码 id 漂移）----
    risk_b = q1("SELECT * FROM risk_events WHERE shipment_id='SHP-2026-0099' AND rule_id='R1'")
    if risk_b is None or risk_b["status"] != "open":
        print("前置不满足：DEMO-01 风险不存在或已被处理过。\n"
              "请先重建：python3 -m pipeline.build_ontology && python3 -m engine.detect"
              " && python3 -m datagen.seed_demo_ops")
        sys.exit(2)
    rid_b = risk_b["risk_event_id"]
    lane_b = lane_for_shipment(con, risk_b["shipment_id"])
    # 同 rule 同 lane 的另一 open 风险（无非终态任务）——扮演"旧案"A
    peers = con.execute(
        """SELECT r.risk_event_id, r.shipment_id FROM risk_events r
           JOIN shipments s ON s.shipment_id=r.shipment_id
           WHERE r.rule_id='R1' AND r.status='open' AND r.risk_event_id != ?
             AND s.origin_port_locode || '→' || s.destination_port_locode = ?
             AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.risk_event_id=r.risk_event_id
                             AND t.status NOT IN ('done','cancelled'))
           ORDER BY r.risk_event_id""", (rid_b, lane_b)).fetchall()
    if not peers:
        print(f"前置不满足：找不到与 {rid_b} 同航线（{lane_b}）的开放 R1 旧案锚点。请重建数据链。")
        sys.exit(2)
    rid_a = peers[0]["risk_event_id"]
    print(f"锚点：旧案 A={rid_a}，新案 B={rid_b}，lane={lane_b}，as_of={as_of}")

    print("== ④ 无先例：如实返回首例 ==")
    sim0 = find_similar(con, "R1", lane_b, exclude_risk_id=rid_b)
    block0 = render_precedent_block(sim0)
    check("④ 空表时 total=0、most_similar=None", sim0["total"] == 0 and sim0["most_similar"] is None)
    check("④ 渲染如实说“无先例，首例”", "无先例" in block0 and "首例" in block0, block0)

    print("== ① 决定写入：旧案 A 走 assign→propose→reject→再 propose→approve ==")
    r = assign_task(con, rid_a, "ops", "P2", as_of, actor="daniel", role="ops", as_of=as_of)
    tid_a = r["object_id"]
    r = propose_mitigation(con, tid_a, "accept_delay", {"reason": "客户可接受晚到"},
                           actor="daniel", role="ops", as_of=as_of)
    check("① A 提案成功（前置）", r["ok"], str(r["error"]))
    r = approve_mitigation(con, tid_a, "rejected", "理由不充分，先与客户再确认",
                           actor="manager-li", role="manager", as_of=as_of)
    check("① 驳回也是“人的决定”→ 归档 decision=rejected",
          r["ok"] and len(mem_rows(rid_a)) == 1 and mem_rows(rid_a)[0]["decision"] == "rejected")
    r = propose_mitigation(con, tid_a, "accept_delay", {"reason": "客户邮件确认可接受晚到一周"},
                           actor="daniel", role="ops", as_of=as_of)
    r = approve_mitigation(con, tid_a, "approved", "同意接受延误", actor="manager-li",
                           role="manager", as_of=as_of, offsite_basis="客户电话口头确认（场外）")
    rows_a = mem_rows(rid_a)
    adopted = [m for m in rows_a if m["decision"] == "adopted"]
    check("① 批准归档 decision=adopted（A 共 2 条记忆，id 确定性且互异）",
          r["ok"] and len(rows_a) == 2 and len(adopted) == 1
          and rows_a[0]["memory_id"] != rows_a[1]["memory_id"])
    m = adopted[0] if adopted else None
    risk_a = q1("SELECT severity, affected_value_usd FROM risk_events WHERE risk_event_id=?",
                rid_a)
    check("① 情境快照完整：rule/lane/severity/金额/as_of 落库（与 A 案一致）",
          m is not None and m["rule_id"] == "R1" and m["lane"] == lane_b
          and m["severity"] == risk_a["severity"] and m["as_of"] == as_of
          and m["impact_usd"] == risk_a["affected_value_usd"])
    check("① 提案版本戳 + 场外依据 + 决策人归档",
          m is not None and str(m["proposal_version"]).startswith("prop-")
          and m["offsite_basis"] == "客户电话口头确认（场外）" and m["decided_by"] == "manager-li")
    check("① A 首例决策 cited_precedent_ids=[]（当时确无先例，不编造引用）",
          m is not None and json.loads(m["cited_precedent_ids"]) == [])

    print("== ① 关闭回填：A 关闭（+3 天）打质量标签 effective ==")
    r = close_risk_event(con, rid_a, "accepted_delay", "客户接受延误，风险关闭",
                         actor="daniel", role="ops", as_of=close_as_of,
                         quality_label="effective")
    rows_a = mem_rows(rid_a)
    check("① 关闭成功且回填全部 2 条记忆（outcome/耗时/标签/关闭人/关闭日）",
          r["ok"] and all(x["outcome_resolved"] == "accepted_delay" and x["outcome_days"] == 3
                          and x["quality_label"] == "effective" and x["closed_by"] == "daniel"
                          and x["closed_at"] == close_as_of for x in rows_a),
          str([dict(x) for x in rows_a]))

    print("== ② 数字可回查一致：B 视角检索（同 rule+lane，排除自身）==")
    sim = find_similar(con, "R1", lane_b, exclude_risk_id=rid_b)
    n_direct = q1("""SELECT count(*) c FROM resolution_memory
                     WHERE status='active' AND rule_id='R1' AND lane=? AND risk_event_id != ?""",
                  lane_b, rid_b)["c"]
    block = render_precedent_block(sim)
    check("② find_similar.total == 表 count 现算", sim["total"] == n_direct == 2,
          f"total={sim['total']} direct={n_direct}")
    check("② 渲染“同类 N 次”的 N 与表 count 一致", f"同类 {n_direct} 次" in block, block)
    check("② by_decision 统计与逐条决定一致", sim["by_decision"] == {"adopted": 1, "rejected": 1},
          str(sim["by_decision"]))
    check("② 最相似一案指向 A 的已采纳决定且带已回填结果（当时…结果…可讲透）",
          sim["most_similar"]["risk_event_id"] == rid_a
          and sim["most_similar"]["decision"] == "adopted"
          and sim["most_similar"]["outcome_resolved"] == "accepted_delay"
          and "接受延误" in block and "3 天关闭" in block and "有效" in block, block)
    # AI 只读工具与引擎同源同数
    sess = AgentSession(db_path=str(tmp), role="ops")
    out = sess.dispatch("get_similar_resolutions", {"risk_event_id": rid_b})
    check("② AI 只读工具与引擎检索同数、含渲染区块",
          out.get("total") == n_direct and out.get("precedent_block") == block, str(out))

    print("== ①/G1 引用可溯源：B 批准时引用最相似先例 ==")
    r = assign_task(con, rid_b, "ops", "P1", as_of, actor="daniel", role="ops", as_of=as_of)
    tid_b = r["object_id"]
    propose_mitigation(con, tid_b, "reschedule",
                       {"new_promise_date": "2026-08-27", "notify_customer": True},
                       actor="daniel", role="ops", as_of=as_of)
    r = approve_mitigation(con, tid_b, "approved", "参考先例改期", actor="manager-li",
                           role="manager", as_of=as_of)
    mem_b = mem_rows(rid_b)
    cited = json.loads(mem_b[0]["cited_precedent_ids"]) if mem_b else []
    check("① B 批准成功且归档 1 条记忆", r["ok"] and len(mem_b) == 1)
    check("①/G1 cited_precedent_ids 恰引用最相似 1 条（Daniel 裁决 Q2），且 id 可回查到 A",
          cited == [sim["most_similar"]["memory_id"]]
          and q1("SELECT risk_event_id FROM resolution_memory WHERE memory_id=?",
                 cited[0])["risk_event_id"] == rid_a if cited else False, str(cited))

    print("== ③ 屏蔽先例：voided 后不返回、不暗中引用 ==")
    con.execute("UPDATE resolution_memory SET status='voided' WHERE risk_event_id=?", (rid_a,))
    con.commit()
    sim_v = find_similar(con, "R1", lane_b, exclude_risk_id=rid_b)
    block_v = render_precedent_block(sim_v)
    check("③ voided 记忆不再返回（total=0、most_similar=None）",
          sim_v["total"] == 0 and sim_v["most_similar"] is None, str(sim_v))
    check("③ 渲染退回“首例”，绝不暗引已屏蔽先例", "首例" in block_v and rid_a not in block_v, block_v)
    sim_all = find_similar(con, "R1", lane_b, exclude_risk_id=None)
    check("③ 不排除自身时也只见 active（仅 B 自己的 1 条，无 voided 混入）",
          sim_all["total"] == 1 and sim_all["most_similar"]["risk_event_id"] == rid_b,
          str(sim_all))

    print("== ⑤ quality_label 三值校验 ==")
    r = close_risk_event(con, rid_b, "mitigated", "试图用非法标签关闭",
                         actor="daniel", role="ops", as_of=close_as_of,
                         quality_label="somewhat")
    check("⑤ 非法标签被拒且风险未被关闭",
          not r["ok"] and "quality_label" in (r["error"] or "")
          and q1("SELECT status FROM risk_events WHERE risk_event_id=?", rid_b)["status"]
          == "mitigating")
    r = close_risk_event(con, rid_b, "mitigated", "改期完成，先例已引用",
                         actor="daniel", role="ops", as_of=close_as_of,
                         quality_label="partial")
    check("⑤ 合法标签 partial 关闭成功并回填",
          r["ok"] and mem_rows(rid_b)[0]["quality_label"] == "partial")

    print("== ⑥ 回归：审批权限仍仅 manager；AI 无写工具、只多一个只读检索 ==")
    risk_c = q1("""SELECT r.risk_event_id FROM risk_events r
                   WHERE r.rule_id='R1' AND r.status='open'
                     AND r.risk_event_id NOT IN (?, ?)
                     AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.risk_event_id=r.risk_event_id
                                     AND t.status NOT IN ('done','cancelled'))
                   ORDER BY r.risk_event_id""", rid_a, rid_b)
    if risk_c is None:
        print("前置不满足：找不到第三个可用 R1 风险锚点。请重建数据链。")
        sys.exit(2)
    r = assign_task(con, risk_c["risk_event_id"], "ops", "P2", as_of,
                    actor="daniel", role="ops", as_of=as_of)
    tid_c = r["object_id"]
    propose_mitigation(con, tid_c, "accept_delay", {"reason": "回归用例"},
                       actor="daniel", role="ops", as_of=as_of)
    denied_before = q1("""SELECT count(*) c FROM action_log WHERE action='ApproveMitigation'
                          AND result LIKE 'denied%'""")["c"]
    r = approve_mitigation(con, tid_c, "approved", "ops 越权自批", actor="daniel",
                           role="ops", as_of=as_of)
    denied_after = q1("""SELECT count(*) c FROM action_log WHERE action='ApproveMitigation'
                         AND result LIKE 'denied%'""")["c"]
    check("⑥ ops 审批被拒且留 denied 审计、不产生记忆行",
          not r["ok"] and denied_after == denied_before + 1
          and len(mem_rows(risk_c["risk_event_id"])) == 0)
    check("⑥ ROLE_PERMS 审批仍仅 manager、关闭仍仅 ops（人类闸未松动）",
          ROLE_PERMS["ApproveMitigation"] == {"manager"} and ROLE_PERMS["CloseRiskEvent"] == {"ops"})
    r = approve_mitigation(con, tid_c, "approved", "经理正常批", actor="manager-li",
                           role="manager", as_of=as_of)
    check("⑥ manager 审批仍正常（含记忆归档）",
          r["ok"] and len(mem_rows(risk_c["risk_event_id"])) == 1)
    r = close_risk_event(con, risk_c["risk_event_id"], "mitigated", "不打标关闭（兼容既有调用）",
                         actor="daniel", role="ops", as_of=close_as_of)
    check("⑤/⑥ 不传 quality_label 关闭仍成功，记忆回填但标签留空（兼容回归）",
          r["ok"] and mem_rows(risk_c["risk_event_id"])[0]["quality_label"] is None
          and mem_rows(risk_c["risk_event_id"])[0]["outcome_resolved"] == "mitigated")
    check("⑥ FORBIDDEN_TOOLS 一字不改（恰四个审批/关闭类）",
          FORBIDDEN_TOOLS == {"approve_mitigation", "close_risk_event",
                              "approve_quote_decision", "reject_or_request_more_info"},
          str(FORBIDDEN_TOOLS))
    check("⑥ get_similar_resolutions 对全角色开放且为只读（在风险域读工具集）",
          all("get_similar_resolutions" in allowed_tools_for_role(role)
              for role in ("ops", "cs", "finance", "manager", "sales", "compliance",
                           "procurement")))
    check("⑥ TOOL_DEFS 无任何处置记忆写工具（write/backfill 不存在于注册表）",
          not any(("write" in d["name"] or "backfill" in d["name"]) for d in TOOL_DEFS))
    out = sess.dispatch("approve_mitigation", {"task_id": tid_c, "decision": "approved",
                                               "comment": "诱导越权"})
    check("⑥ AI dispatch 审批仍被拒（proposal-only 护栏未被 C1 撬动）",
          out.get("refused") is True, str(out))

    print(f"\n{'=' * 44}\n结果: {'全部通过 ✔' if not FAILS else f'{len(FAILS)} 项失败: {FAILS}'}")
    print("（临时副本测试，data/ontology.sqlite 未被污染）")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
