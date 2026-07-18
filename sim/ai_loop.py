"""S2 AI 同事在世界中运转：检测→分诊定级→提案(经济账现算)→模拟审批人→处置生效(真改世界状态)
→ 先例沉淀。在回填时间线内按 cadence 交织运转（延误处置在里程碑 emit 前改计划 → 因果真实）。

铁律（宪法③精神：合成不冒充真实）：一切模拟产物**显式标 source='sim'**——risk_events/tasks/
resolution_memory/ai_activity 全带 source='sim'；模拟审批人显式命名 **sim-approver-01**、AI 同事
命名 **sim-ai**（非真人、非真实 LLM）；这些先例永不写入 data/ontology.sqlite 的真实先例库
（物理隔离：只落 data/simworld.sqlite）。

先例数据模型 = engine.resolution_memory 的四件套血缘（情境快照+提案版本戳+引用先例+决策人，
关闭时回填结果与质量标签），并加 source 列；find_similar/render 的判据与排序在本模块内存态镜像
（回填期不往返 sqlite），落库时序列化到 simworld.sqlite 的 resolution_memory 表。

未做真实 LLM 调用 → 不伪造 llm_calls 行（诚实优先：AI 同事是确定性经济账引擎，非 LLM；
其推理留痕在 sim_ai_activity，actor=sim-ai）。
"""
import hashlib
import json
from datetime import date, timedelta

from . import generators as G

AI_ACTOR = "sim-ai"
APPROVER = "sim-approver-01"
PRIORITY = {"critical": "P1", "high": "P2", "medium": "P3"}
RANK = {"adopted": 0, "modified": 1, "rejected": 2}
ACTION_LABELS = {"expedite": "加急", "accept_delay": "接受延误", "dispute": "争议账单",
                 "escalate_replenishment": "紧急补货", "chase_docs": "补件催办", "escalate": "升级"}
DECISION_LABELS = {"adopted": "采纳", "modified": "修改后采纳", "rejected": "拒绝"}
QUALITY_ZH = {"effective": "有效", "partial": "部分有效", "ineffective": "无效"}


# ============================ 初始化 ============================
def init_ai(world):
    world["risk_events"] = {}          # rid -> risk row（sim）
    world["tasks"] = {}                # tid -> task row（sim）
    world["memory"] = []               # resolution_memory 行（sim，四件套）
    world["ai_activity"] = []          # AI 活动流（检测/提案/审批/关闭；actor + source=sim）
    world["_ai"] = {"seen": {}, "pending": [], "meta": {}}


def _cfg(world):
    return world["_cfg"]["ai_loop"]


# ============================ cadence 主回路 ============================
def run_cadence(world, day, streams):
    """一个检测周期：新建风险+提案 → 处置到期的待批 → 关闭到期的已决风险。全程 as-of=day。"""
    if not _cfg(world).get("enabled"):
        return
    rng = streams["ai"]
    _detect_and_create(world, day, rng)
    _process_approvals(world, day, rng)
    _close_matured(world, day, rng)


def _log_ai(world, day, actor, activity, rid, tid, detail):
    world["ai_activity"].append({
        "ai_event_id": G._nid(world, "ai", "AIE", 7), "sim_date": day.isoformat(),
        "actor": actor, "activity": activity, "risk_event_id": rid or "", "task_id": tid or "",
        "detail": detail, "source": "sim"})


# ============================ 检测 → 建风险 → 提案 ============================
def _detect_and_create(world, day, rng):
    from . import detectors as DET
    for c in DET.detect(world, day):
        key = c["dedup_key"]
        if key in world["_ai"]["seen"]:
            continue                                   # 同锚点同规则只建一次（不重复刷）
        rid = G._nid(world, "rsk", "RSK-SIM", 5)
        world["risk_events"][rid] = {
            "risk_event_id": rid, "type": c["type"], "rule_id": c["rule_id"],
            "severity": c["severity"], "shipment_id": c["shipment_id"] or "",
            "affected_so_line_ids": json.dumps(c["affected_so_line_ids"]),
            "affected_value_usd": c["affected_value_usd"], "detected_at": c["detected_at"],
            "root_cause": c["root_cause"], "status": "open", "resolved_at": "",
            "outcome": "", "resolution_summary": "",
            "affected_invoice_line_ids": json.dumps(c["affected_invoice_line_ids"]),
            "po_id": "", "supplier_id": "", "affected_po_line_ids": "[]",
            "warehouse_id": c["warehouse_id"] or "", "source": "sim"}
        world["_ai"]["seen"][key] = rid
        world["_ai"]["meta"][rid] = {"cand": c, "key": key}
        _log_ai(world, day, AI_ACTOR, "detect", rid, None,
                f"{c['rule_id']} {c['type']} sev={c['severity']} ${c['affected_value_usd']}")
        if c["severity"] in _cfg(world)["propose_severities"]:
            _propose(world, rid, c, day, rng)


def _propose(world, rid, c, day, rng):
    """生成提案：经济账现算 → 选动作；引用先例（内存态 find_similar）；排审批延迟。"""
    action, params, econ = _economics(world, c, rng)
    lane = _lane(world, c)
    sim = _find_similar(world, c["rule_id"], lane, exclude_rid=rid)
    block = _render_precedent(sim)
    summary = json.dumps({"action": action, "params": params}, ensure_ascii=False, sort_keys=True)
    version = "prop-" + hashlib.sha256(summary.encode("utf-8")).hexdigest()[:10]
    cited = [sim["most_similar"]["memory_id"]] if sim["most_similar"] else []
    tid = G._nid(world, "task", "TSK-SIM", 5)
    lo, hi = _cfg(world)["approver"]["decide_delay_days"]
    decision_day = day + timedelta(days=rng.randint(lo, hi))
    world["tasks"][tid] = {
        "task_id": tid, "risk_event_id": rid, "title": f"{c['rule_id']} {c['type']} 处置提案",
        "assignee_role": "ops", "priority": PRIORITY[c["severity"]], "proposed_action": action,
        "proposal_params": json.dumps(params, ensure_ascii=False), "approval_status": "pending",
        "approved_by_role": "", "action_taken": "", "status": "open",
        "proposal_actor_id": AI_ACTOR, "decided_at": "", "decision_day": decision_day.isoformat(),
        "economics_json": json.dumps(econ, ensure_ascii=False), "precedent_block": block,
        "source": "sim"}
    world["_ai"]["pending"].append(tid)
    meta = world["_ai"]["meta"][rid]
    meta.update(task_id=tid, proposal_version=version, cited=cited, summary=summary,
                action=action, params=params, lane=lane, econ=econ)
    _log_ai(world, day, AI_ACTOR, "propose", rid, tid,
            f"提案「{ACTION_LABELS.get(action, action)}」；经济账 {econ['verdict']}；"
            f"引用先例 {cited or '首例'}")


def _economics(world, c, rng):
    """加急成本 vs 断货/滞箱损失现算 → 动作 + 参数 + 账本。"""
    ec = _cfg(world)["economics"]
    rule = c["rule_id"]
    if rule in ("R1", "R3"):                            # 延误/静默 → 加急 vs 接受
        ship = world["shipments"][c["shipment_id"]]
        n_cont = len(ship["containers"])
        breach = max(c["breach_days"], 1)
        expedite_cost = ec["expedite_cost_per_container_usd"] * n_cont
        detention_avoid = ec["detention_avoided_per_day_usd"] * breach
        margin_at_risk = round(c["affected_value_usd"] * ec["margin_at_risk_frac"], 2)  # 迟交/断货边际损失近似
        benefit = round(detention_avoid + margin_at_risk, 2)
        if benefit > expedite_cost:
            pull = rng.randint(*ec["expedite_pull_in_days"])
            return ("expedite", {"pull_in_days": pull, "est_cost_usd": expedite_cost},
                    {"verdict": f"加急划算：收益 ${benefit} > 成本 ${expedite_cost}",
                     "expedite_cost_usd": expedite_cost, "benefit_usd": benefit,
                     "margin_at_risk_usd": margin_at_risk, "detention_avoided_usd": detention_avoid})
        return ("accept_delay", {},
                {"verdict": f"接受延误：加急成本 ${expedite_cost} ≥ 收益 ${benefit}",
                 "expedite_cost_usd": expedite_cost, "benefit_usd": benefit,
                 "margin_at_risk_usd": margin_at_risk, "detention_avoided_usd": detention_avoid})
    if rule in ("R4", "R5", "R6"):                      # 费用异常 → 争议账单（可追回；G4 核实：
                                                          # 这三条锚 Invoice/物流费票，真实同域动作是
                                                          # cost-manual v0.4 的 "dispute"（写 invoices.status），
                                                          # 非 P1 采购域 "dispute_supplier_invoice"（写
                                                          # supplier_invoices，锚 PoLine/Supplier，域不同）——
                                                          # 沿用真实同域动作名，非 V11 原提案词）
        return ("dispute", {"disputed_amount_usd": c["affected_value_usd"],
                            "invoice_id": c["anchor_id"]},
                {"verdict": f"争议可追回 ${c['affected_value_usd']}",
                 "recoverable_usd": c["affected_value_usd"], "cost_usd": 0})
    if rule == "R16":                                   # 断货 → 紧急补货 vs 升级
        pos = _position(world, c["anchor_id"])
        gap = pos["safety_stock"] - pos["available_qty"] if pos else 0
        stockout_loss = round(max(gap, 0) * ec["stockout_penalty_per_unit_usd"], 2)
        expedite_cost = round(ec["expedite_cost_per_container_usd"] * 0.4, 2)  # 空派/调拨近似
        if stockout_loss > expedite_cost:
            return ("escalate_replenishment", {"units": max(gap, 0), "est_cost_usd": expedite_cost},
                    {"verdict": f"紧急补货划算：断货损失 ${stockout_loss} > 成本 ${expedite_cost}",
                     "stockout_loss_usd": stockout_loss, "expedite_cost_usd": expedite_cost})
        return ("escalate", {}, {"verdict": f"升级人工：损失 ${stockout_loss} ≤ 成本 ${expedite_cost}",
                                 "stockout_loss_usd": stockout_loss, "expedite_cost_usd": expedite_cost})
    # R2 单证 → 补件催办（G4 核实：清缺 shipment.missing_docs，锚 Shipment；真实 app.actions
    # 无该域专属处置动作——R2 目前复用 reschedule/expedite/accept_delay 通用三选一（同 R1/R3），
    # "request_supplier_docs" 是 P2 供应商资质域动作（写 supplier_qualifications，锚 Supplier，
    # 域不同，勿借用）。无对应真实词可对齐，保留 sim 自有词，本体如实收编）
    return ("chase_docs", {"docs": world["shipments"][c["shipment_id"]]["missing_docs"]},
            {"verdict": f"补件避免清关滞留，涉险货值 ${c['affected_value_usd']}",
             "value_at_risk_usd": c["affected_value_usd"], "cost_usd": 0})


# ============================ 模拟审批人 → 处置生效（真改世界）============================
def _process_approvals(world, day, rng):
    ac = _cfg(world)["approver"]
    still = []
    for tid in world["_ai"]["pending"]:
        task = world["tasks"][tid]
        if task["decision_day"] > day.isoformat():
            still.append(tid)
            continue
        rid = task["risk_event_id"]
        meta = world["_ai"]["meta"][rid]
        approve = rng.random() < ac["approve_rate"]
        if approve:
            effect = _apply_disposition(world, task, meta["cand"], day)
            task.update(approval_status="approved", status="done", approved_by_role="manager",
                        action_taken=f"{meta['action']} approved", decided_at=day.isoformat())
            # G6 枚举清零（本体 RiskEvent.status 治本）：批准即处置生效、但尚未到期关闭（_close_matured
            # 会在 10-28 天后转 resolved+outcome）——此中间态"处置已施加、结果待兑现"，本体枚举 mitigating
            # （处置进行中）最贴，非 resolved（那是关闭态）。改 mitigated→mitigating（无内部读者依赖此字面量）。
            world["risk_events"][rid]["status"] = "mitigating"
            decision = "adopted"
            _log_ai(world, day, APPROVER, "approve", rid, tid, f"批准；处置生效：{effect}")
        else:
            task.update(approval_status="rejected", status="done", decided_at=day.isoformat())
            decision = "rejected"
            _log_ai(world, day, APPROVER, "reject", rid, tid, "驳回提案（维持现状/另议）")
        mem_id = _write_memory(world, rid, meta, decision, day)
        meta.update(mem_id=mem_id, decided_day=day, decision=decision,
                    close_day=day + timedelta(days=rng.randint(10, 28)))
    world["_ai"]["pending"] = still


def _apply_disposition(world, task, c, day):
    """已批准提案真实改变世界状态：加急提前 ETA / 争议账单转 disputed / 紧急补货抬库存 / 补件清缺。"""
    action = task["proposed_action"]
    params = json.loads(task["proposal_params"])
    if action == "expedite":                            # R1/R3：提前到港及后续（改计划，emit 前生效）
        pull = params.get("pull_in_days", 5)
        return _pull_in_shipment(world, c["shipment_id"], pull, day)
    if action == "escalate_replenishment":               # R16：紧急补货，可用抬到安全库存之上
        pos = _position(world, c["anchor_id"])
        if pos:
            before = pos["available_qty"]
            pos["available_qty"] = pos["safety_stock"] + max(params.get("units", 0), 1)
            return f"库存 {pos['inventory_position_id']} 可用 {before}→{pos['available_qty']}（紧急补货）"
        return "position 不存在"
    if action == "dispute":                              # R4/R5/R6：账单转 disputed
        for inv in world["invoices"]:
            if inv["invoice_id"] == c["anchor_id"]:
                inv["status"] = "disputed"
                return f"发票 {inv['invoice_id']} → disputed（争议 ${params.get('disputed_amount_usd')}）"
        return "发票不存在"
    if action == "chase_docs":                          # R2：补齐单证
        ship = world["shipments"].get(c["shipment_id"])
        if ship:
            had = ship["missing_docs"]
            ship["missing_docs"] = []
            return f"shipment {ship['shipment_id']} 补齐单证 {had}→[]"
        return "shipment 不存在"
    return "无状态变更（accept/escalate）"                # accept_delay / escalate：不改世界（记录决定）


def _pull_in_shipment(world, sid, pull, day):
    """加急：把未 emit 的 arrived 及其后里程碑提前 pull 天（不早于 day+1），同步 eta_current。"""
    ship = world["shipments"].get(sid)
    if not ship:
        return "shipment 不存在"
    floor = day + timedelta(days=1)
    new_plan = []
    for ev_date, etype, ex in ship["plan"]:
        if etype in ("arrived", "customs_filed", "customs_hold", "customs_released", "delivered") \
                and ev_date > day:
            ev_date = max(floor, ev_date - timedelta(days=pull))
        new_plan.append((ev_date, etype, ex))
    ship["plan"] = sorted(new_plan, key=lambda x: x[0])
    before = ship["eta_current"]
    ship["eta_current"] = max(floor, ship["eta_current"] - timedelta(days=pull))
    if ship["_delivered_date"] and ship["_delivered_date"] > day:
        ship["_delivered_date"] = max(floor, ship["_delivered_date"] - timedelta(days=pull))
    ship["expedite_flag"] = True
    return f"shipment {sid} ETA {before}→{ship['eta_current']}（加急 −{pull}d）"


# ============================ 关闭 → 回填先例结果 ============================
def _close_matured(world, day, rng):
    for rid, meta in list(world["_ai"]["meta"].items()):
        if "close_day" not in meta or meta.get("closed") or "mem_id" not in meta:
            continue
        if meta["close_day"] > day:
            continue
        risk = world["risk_events"][rid]
        adopted = meta["decision"] == "adopted"
        # G6 枚举清零（本体 RiskEvent.outcome 治本）：sim 原产 accepted（提案未采纳→接受延误/现状），
        # 本体 outcome 枚举为 [mitigated,accepted_delay,false_alarm,escalated]——accepted→accepted_delay
        # （语义即"接受延误"，与真实 app close_risk_event 流一致）；mitigated 本就在枚举内，不动。
        outcome = "mitigated" if adopted else "accepted_delay"
        ql = _sample_quality(world, rng)
        risk.update(status="resolved", outcome=outcome, resolved_at=meta["close_day"].isoformat(),
                    resolution_summary=f"{meta['action']} {'已执行' if adopted else '未采纳'}；"
                                       f"专员质量标记「{QUALITY_ZH[ql]}」")
        days = (meta["close_day"] - meta["decided_day"]).days
        _backfill_memory(world, meta["mem_id"], outcome, days, ql, meta["close_day"])
        meta["closed"] = True
        _log_ai(world, meta["close_day"], APPROVER, "close", rid, meta.get("task_id"),
                f"关闭 outcome={outcome} 用时 {days}d 质量={QUALITY_ZH[ql]}")


def _sample_quality(world, rng):
    bias = _cfg(world)["quality_label_bias"]
    x = rng.random()
    acc = 0.0
    for label in ("effective", "partial", "ineffective"):
        acc += bias[label]
        if x <= acc:
            return label
    return "effective"


# ============================ 先例四件套（写入 + 回填），全部 source='sim' ============================
def _write_memory(world, rid, meta, decision, day):
    risk = world["risk_events"][rid]
    mem_id = G._nid(world, "mem", "MEM-SIM", 5)
    world["memory"].append({
        "memory_id": mem_id, "risk_event_id": rid, "rule_id": risk["rule_id"],
        "lane": meta.get("lane", ""), "severity": risk["severity"],
        "impact_usd": risk["affected_value_usd"], "as_of": day.isoformat(),
        "proposal_summary": meta["summary"], "proposal_version": meta["proposal_version"],
        "cited_precedent_ids": json.dumps(meta.get("cited", [])), "decision": decision,
        "decision_note": "", "offsite_basis": "", "decided_by": APPROVER,
        "decided_at": day.isoformat(), "outcome_resolved": "", "outcome_days": "",
        "quality_label": "", "closed_by": "", "closed_at": "", "status": "active", "source": "sim"})
    return mem_id


def _backfill_memory(world, mem_id, outcome, days, quality_label, close_day):
    for m in world["memory"]:
        if m["memory_id"] == mem_id:
            m.update(outcome_resolved=outcome, outcome_days=days, quality_label=quality_label,
                     closed_by=APPROVER, closed_at=close_day.isoformat())
            return


# ============================ 内存态相似先例检索 / 渲染（镜像 engine.resolution_memory）========
def _lane(world, c):
    sid = c["shipment_id"]
    ship = world["shipments"].get(sid) if sid else None
    if not ship or not ship["origin_port_locode"] or not ship["destination_port_locode"]:
        return ""
    return f"{ship['origin_port_locode']}→{ship['destination_port_locode']}"


def _position(world, pid):
    for pos in world["inventory"].values():
        if pos["inventory_position_id"] == pid:
            return pos
    return None


def _find_similar(world, rule_id, lane, exclude_rid):
    """rule_id 精确 + 同 lane，排除本案，status='active'（voided 不返回）；排序镜像 engine：
    已回填结果者优先 > 采纳过的方案优先 > 决策日新 > memory_id 大。全部现算。"""
    rows = [m for m in world["memory"] if m["status"] == "active" and m["rule_id"] == rule_id
            and m["lane"] == lane and m["risk_event_id"] != exclude_rid]
    if not rows:
        return {"rule_id": rule_id, "lane": lane, "total": 0, "by_decision": {},
                "by_action": {}, "most_similar": None}
    rows.sort(key=lambda r: r["memory_id"], reverse=True)
    rows.sort(key=lambda r: r["decided_at"], reverse=True)
    rows.sort(key=lambda r: RANK.get(r["decision"], 3))
    rows.sort(key=lambda r: not r["closed_at"])          # closed_at 非空者在前
    by_decision, by_action = {}, {}
    for r in rows:
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
        act = json.loads(r["proposal_summary"]).get("action", "?")
        r["proposed_action"] = act
        by_action[act] = by_action.get(act, 0) + 1
    return {"rule_id": rule_id, "lane": lane, "total": len(rows),
            "by_decision": by_decision, "by_action": by_action, "most_similar": rows[0]}


def _render_precedent(sim):
    if sim["total"] == 0:
        return f"无先例，首例（rule={sim['rule_id']}、lane={sim['lane'] or '-'}）。"
    actions = "/".join(f"{ACTION_LABELS.get(a, a)} {n}" for a, n in sorted(sim["by_action"].items()))
    decisions = "·".join(f"{DECISION_LABELS.get(d, d)} {n}"
                         for d, n in sorted(sim["by_decision"].items()))
    m = sim["most_similar"]
    act = ACTION_LABELS.get(m["proposed_action"], m["proposed_action"])
    line = (f"同类 {sim['total']} 次：{actions}（{decisions}）；最相似 {m['risk_event_id']}："
            f"当时「{act}」被{DECISION_LABELS[m['decision']]}（{m['decided_by']} @{m['decided_at']}），结果")
    if m["closed_at"]:
        line += f" {m['outcome_resolved'] or '-'}、{m['outcome_days']} 天关闭"
        if m["quality_label"]:
            line += f"、标记「{QUALITY_ZH.get(m['quality_label'], m['quality_label'])}」。"
    else:
        line += "未回填（尚未关闭）。"
    return line
