#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_cockpit.py —— 驾驶舱聚合层（B1）验收用例。

隔离纪律（同 apps/api/test_api.py）：验证世界用 data/ontology.sqlite 的临时拷贝 +
app.dependency_overrides 换 get_db_path；模拟世界按任务书用 monkeypatch ONTOLOGY_DB
指到 data/simworld.sqlite 的临时副本（保留原文件名以验证 world 标识），并在期间摘掉
dependency_overrides——走「环境变量→get_db_path→路由」的真实生产链路。

"现查现算对照，勿硬编码具体值"：每区抽 ≥1 个指标，期望值当场对临时库跑**独立写法**的
SQL（如客户区用 json_each 展开 affected_so_line_ids，与实现的 python 解析是两条路径），
不誊抄任何具体数字。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from agent.tools import MASK
from apps.api.main import REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"
ZONE_ORDER = ["money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions"]
ZONE_KEYS = {"zone", "headline_label", "headline_value", "trend", "alert_count", "detail"}
# G2 应收/应付/净流出：阈值/窗口现查 config（非誊抄字面量）——finance-manual v0.11 明示
# cash_watch_threshold_usd 是"初值代定候调"，硬编码字面量会在 Daniel 调阈值后无声失配。
_FINANCE_CFG = yaml.safe_load(open(REPO_ROOT / "config" / "datagen.yaml", encoding="utf-8"))["finance"]


# ═══════════════════════════════════════════════════════════════════════════
# fixtures：验证世界（module 级临时拷贝 + overrides）/ 模拟世界（function 级 env 链路）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tmp_db_path(tmp_path_factory):
    assert REPO_DB.exists(), f"{REPO_DB} 不存在——需先跑 datagen/build_ontology/seed_demo_ops"
    dest = tmp_path_factory.mktemp("cockpit_test_db") / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)
    return str(dest)


@pytest.fixture(scope="module")
def client(tmp_db_path):
    app.dependency_overrides[get_db_path] = lambda: tmp_db_path
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db_path, None)


@pytest.fixture(scope="module")
def con(tmp_db_path):
    c = sqlite3.connect(tmp_db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


@pytest.fixture()
def sim_world(tmp_path):
    """模拟世界副本 + ONTOLOGY_DB 环境变量真链路（任务书指定 monkeypatch 方式）。
    function 级：期间暂摘验证世界的 dependency_overrides，结束原样恢复，不影响其他用例。"""
    assert SIM_DB.exists(), f"{SIM_DB} 不存在——先 `python3 -m sim.backfill` 生成模拟世界"
    dest = tmp_path / "simworld.sqlite"          # 保留原文件名 → world 应推断为 simulation
    shutil.copy(SIM_DB, dest)
    mp = pytest.MonkeyPatch()
    mp.setenv("ONTOLOGY_DB", str(dest))
    saved = app.dependency_overrides.pop(get_db_path, None)
    scon = sqlite3.connect(dest)
    scon.row_factory = sqlite3.Row
    try:
        with TestClient(app) as c:
            yield c, scon
    finally:
        scon.close()
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        mp.undo()


def _zones(resp) -> dict[str, dict]:
    return {z["zone"]: z for z in resp.json()["zones"]}


# ═══════════════════════════════════════════════════════════════════════════
# vitals：七区结构
# ═══════════════════════════════════════════════════════════════════════════
def test_vitals_seven_zone_structure(client, con):
    resp = client.get("/cockpit/vitals", headers={"X-Role": "manager"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["world"] == "verification"
    # clock=库内最大事件日：现查检测/审计两账本对照（cockpit._world_clock 口径）
    expected_clock = max(
        con.execute("SELECT max(date(detected_at)) FROM risk_events").fetchone()[0],
        con.execute("SELECT max(date(timestamp)) FROM action_log").fetchone()[0])
    assert data["clock"] == expected_clock
    assert [z["zone"] for z in data["zones"]] == ZONE_ORDER
    for z in data["zones"]:
        assert ZONE_KEYS <= set(z), f"{z['zone']} 缺字段：{ZONE_KEYS - set(z)}"
        assert isinstance(z["alert_count"], int)
        assert isinstance(z["detail"], dict) and z["detail"]


def test_vitals_trend_all_null_in_verification(client):
    """验证世界=静态快照，七区 trend 必须全 null（红线：绝不编造趋势）。"""
    resp = client.get("/cockpit/vitals", headers={"X-Role": "manager"})
    assert [z["trend"] for z in resp.json()["zones"]] == [None] * 7


# ═══════════════════════════════════════════════════════════════════════════
# vitals：每区 ≥1 指标与手工 SQL 现查对照
# ═══════════════════════════════════════════════════════════════════════════
def test_vitals_money_matches_sql(client, con):
    money = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["money"]
    row = con.execute("SELECT count(*) c, round(sum(affected_value_usd),2) v FROM risk_events "
                      "WHERE rule_id IN ('R4','R5','R6') AND status='open'").fetchone()
    assert money["headline_value"] == row["v"]
    assert money["alert_count"] == row["c"]
    assert money["detail"]["fee_exposure"] == {
        "value_usd": row["v"], "open_risks": row["c"], "rules": ["R4", "R5", "R6"]}
    # 毛利率分布桶总数 = cost_scenarios 非空 margin 行数（现查）
    margin = money["detail"]["margin_distribution"]
    total = con.execute("SELECT count(*) FROM cost_scenarios "
                        "WHERE gross_margin_rate IS NOT NULL").fetchone()[0]
    assert margin["scenarios_total"] == total == sum(margin["buckets"].values())
    # 被拦截超收：验证世界风险全 open → 真实 0（非缺数标注）
    resolved = con.execute("SELECT count(*) FROM risk_events "
                           "WHERE rule_id='R4' AND status='resolved'").fetchone()[0]
    assert money["detail"]["intercepted_overbilling"]["resolved_r4_risks"] == resolved
    # 在途货值：现查有申报价值的在途分配行数——有则值=SQL 合计，全无则必须 null+如实 reason
    itv = con.execute("""
        SELECT count(*) total,
               sum(CASE WHEN k.declared_value_usd IS NULL OR k.declared_value_usd=''
                        THEN 0 ELSE 1 END) valued,
               round(sum(sa.allocated_qty * CASE WHEN k.declared_value_usd IS NULL
                         OR k.declared_value_usd='' THEN NULL
                         ELSE CAST(k.declared_value_usd AS REAL) END), 2) v
        FROM shipments s
        JOIN shipment_allocations sa ON sa.shipment_id = s.shipment_id
        JOIN sales_order_lines sol ON sol.so_line_id = sa.so_line_id
        JOIN skus k ON k.sku_id = sol.sku_id WHERE s.status='in_transit'""").fetchone()
    in_transit = money["detail"]["in_transit_value"]
    if itv["total"] and itv["valued"] == 0:
        assert in_transit["value"] is None and "申报价值" in in_transit["reason"]
    else:
        assert in_transit["value_usd"] == (itv["v"] or 0.0)


def test_vitals_money_finance_flow_matches_sql(client, con):
    """G2：应收/应付/净流出预警三指标与手工 SQL 现查对照（验证世界）。"""
    money = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["money"]
    clock = con.execute("SELECT max(date(detected_at)) FROM risk_events").fetchone()[0]

    for direction, key in (("in", "receivables"), ("out", "payables")):
        base = con.execute(
            "SELECT count(*) c, round(sum(amount_usd),2) v FROM payments "
            "WHERE direction=? AND status='scheduled'", (direction,)).fetchone()
        metric = money["detail"][key]
        assert metric["count"] == base["c"]
        assert metric["amount_usd"] == (base["v"] or 0.0)
        overdue = con.execute(
            "SELECT count(*) c, round(sum(amount_usd),2) v FROM payments "
            "WHERE direction=? AND status='scheduled' AND due_date < ? "
            "AND (paid_date IS NULL OR paid_date='')", (direction, clock)).fetchone()
        assert metric["overdue"]["count"] == overdue["c"]
        assert metric["overdue"]["amount_usd"] == (overdue["v"] or 0.0)
        assert metric["overdue"]["count"] <= metric["count"], "逾期笔数不能超过在外总笔数"

    window_days = _FINANCE_CFG["cash_watch_window_days"]
    threshold = _FINANCE_CFG["cash_watch_threshold_usd"]
    win_hi = con.execute("SELECT date(?, ?)", (clock, f"+{window_days} days")).fetchone()[0]
    out_win = con.execute(
        "SELECT round(sum(amount_usd),2) v FROM payments WHERE direction='out' "
        "AND status='scheduled' AND due_date > ? AND due_date <= ?", (clock, win_hi)).fetchone()[0]
    in_win = con.execute(
        "SELECT round(sum(amount_usd),2) v FROM payments WHERE direction='in' "
        "AND status='scheduled' AND due_date > ? AND due_date <= ?", (clock, win_hi)).fetchone()[0]
    net = round((out_win or 0.0) - (in_win or 0.0), 2)
    nc = money["detail"]["net_cash_14d"]
    assert nc["out_scheduled_usd"] == (out_win or 0.0)
    assert nc["in_scheduled_usd"] == (in_win or 0.0)
    assert nc["value_usd"] == net
    assert nc["window_days"] == window_days
    assert nc["threshold_usd"] == threshold
    assert nc["breach"] == (net > threshold)
    # headline/alert_count 语义不因新指标改变（任务书明示）
    row = con.execute("SELECT count(*) c, round(sum(affected_value_usd),2) v FROM risk_events "
                      "WHERE rule_id IN ('R4','R5','R6') AND status='open'").fetchone()
    assert money["headline_value"] == row["v"] and money["alert_count"] == row["c"]


def test_vitals_money_missing_payments_table(tmp_path):
    """缺 payments 表的世界：G2 三指标如实 null+reason，不 500，不连累钱区其余既有指标。"""
    dest = tmp_path / "no_payments.sqlite"
    shutil.copy(REPO_DB, dest)
    con = sqlite3.connect(dest)
    con.execute("DROP TABLE payments")
    con.commit()
    con.close()
    app.dependency_overrides[get_db_path] = lambda: str(dest)
    try:
        with TestClient(app) as c:
            resp = c.get("/cockpit/vitals", headers={"X-Role": "manager"})
            assert resp.status_code == 200
            money = {z["zone"]: z for z in resp.json()["zones"]}["money"]
            for key in ("receivables", "payables", "net_cash_14d"):
                metric = money["detail"][key]
                assert metric["value"] is None and "该世界无此域数据" in metric["reason"], (key, metric)
            # 不连累回归：费用敞口（既有指标，非本次改动范围）仍正常给数，不因钱区新指标缺表而挂
            assert isinstance(money["headline_value"], (int, float))
            assert isinstance(money["detail"]["fee_exposure"]["value_usd"], (int, float))
    finally:
        app.dependency_overrides.pop(get_db_path, None)


def test_vitals_fulfillment_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["fulfillment"]
    customs = con.execute("SELECT count(*) FROM shipments WHERE customs_status='not_filed' "
                          "AND status='in_transit'").fetchone()[0]
    assert z["detail"]["customs_blocked"]["value"] == customs
    delayed = con.execute("SELECT count(*) FROM shipments WHERE delay_days > 0").fetchone()[0]
    assert z["detail"]["delay_histogram"]["delayed_shipments"] == delayed
    assert sum(z["detail"]["delay_histogram"]["buckets"].values()) == delayed
    otd = z["detail"]["otd"]
    assert otd["measured"] + otd["excluded_no_arrival"] == otd["fulfilled_lines"]
    assert z["headline_value"] == otd["rate"] == round(otd["on_time"] / otd["measured"], 4)
    assert 0.0 <= otd["rate"] <= 1.0


def test_vitals_customers_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["customers"]
    # headline=有敞口客户数：用 json_each 展开（与实现的 python 解析是独立路径）现查对照
    expected = con.execute("""
        SELECT count(DISTINCT so.customer_id)
        FROM risk_events r, json_each(r.affected_so_line_ids) j
        JOIN sales_order_lines sol ON sol.so_line_id = j.value
        JOIN sales_orders so ON so.so_id = sol.so_id
        WHERE r.status='open' AND r.affected_so_line_ids LIKE '[%'""").fetchone()[0]
    assert z["headline_value"] == expected == z["alert_count"]
    funnel = z["detail"]["admission_funnel"]
    raw = dict(con.execute("SELECT status, count(*) FROM admission_cases GROUP BY status"))
    assert funnel["by_status"] == {k: raw[k] for k in funnel["by_status"]} and \
        sum(funnel["by_status"].values()) == funnel["cases_total"] == sum(raw.values())
    top = z["detail"]["top_exposure"]
    assert len(top) <= 5
    assert all(t["exposure_usd"] >= 0 for t in top)
    assert [t["exposure_usd"] for t in top] == sorted(
        (t["exposure_usd"] for t in top), reverse=True)


def test_vitals_suppliers_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["suppliers"]
    r14 = con.execute("SELECT count(*) FROM risk_events "
                      "WHERE rule_id='R14' AND status='open'").fetchone()[0]
    assert z["detail"]["single_source_r14"]["value"] == r14
    recon = con.execute("""
        SELECT count(*) c, round(sum(affected_value_usd),2) v FROM risk_events
        WHERE rule_id IN ('R7','R8','R9','R10','R11','R12','R13') AND status='open'""").fetchone()
    assert z["detail"]["recon_diff_r7_r13"] == {"open_risks": recon["c"], "amount_usd": recon["v"]}
    assert z["alert_count"] == r14 + recon["c"]
    # 交期达成率整体值：独立单条 SQL 复算（PO 首张 GRN ≤ expected_ready_date）
    row = con.execute("""
        SELECT count(*) m, sum(CASE WHEN g.first_recv <= po.expected_ready_date
                                    THEN 1 ELSE 0 END) ot
        FROM purchase_orders po
        JOIN (SELECT po_id, min(received_date) first_recv FROM goods_receipts
              GROUP BY po_id) g ON g.po_id = po.po_id""").fetchone()
    assert z["headline_value"] == round(row["ot"] / row["m"], 4)
    assert z["detail"]["delivery_hit_rate"]["pos_measured"] == row["m"]


def test_vitals_inventory_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["inventory"]
    breach = con.execute("SELECT count(*) FROM inventory_positions "
                         "WHERE available_qty < safety_stock").fetchone()[0]
    assert z["headline_value"] == breach == z["detail"]["safety_breaches"]["count"]
    assert len(z["detail"]["safety_breaches"]["positions"]) == min(breach, 20)
    variance = con.execute("SELECT count(*) FROM cycle_counts WHERE variance != 0").fetchone()[0]
    assert z["detail"]["count_variance"]["count"] == variance
    assert z["alert_count"] == breach + variance
    r = z["detail"]["rescuable"]
    assert r["lines_checked"] == (r["lines_fully_savable"] + r["lines_partially_savable"]
                                  + r["lines_no_stock"])


def test_vitals_ai_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["ai"]
    clock = con.execute("SELECT max(date(detected_at)) FROM risk_events").fetchone()[0]
    detections = con.execute("SELECT count(*) FROM risk_events WHERE date(detected_at)=?",
                             (clock,)).fetchone()[0]
    assert z["detail"]["today"]["detections"] == detections
    llm_total = con.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
    assert z["detail"]["llm_calls"]["total"] == llm_total
    mem = con.execute("SELECT count(*) FROM resolution_memory").fetchone()[0]
    assert z["detail"]["resolution_memory"]["total"] == mem
    proposals = con.execute("SELECT count(*) FROM tasks WHERE approval_status IN "
                            "('pending','approved','rejected')").fetchone()[0]
    assert z["detail"]["all_time"]["proposals"] == proposals


def test_vitals_decisions_matches_sql(client, con):
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "manager"}))["decisions"]
    pending = con.execute("SELECT count(*) FROM tasks "
                          "WHERE approval_status='pending'").fetchone()[0]
    assert z["headline_value"] == pending == len(z["detail"]["pending_proposals"])
    clock = con.execute("SELECT max(date(timestamp)) FROM action_log").fetchone()[0]
    overdue = con.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','cancelled') "
                          "AND due_at IS NOT NULL AND date(due_at) < ?", (clock,)).fetchone()[0]
    assert z["detail"]["overdue_tasks"]["value"] == overdue
    # 金额降序 + 金额来源如实标注（est_cost_usd 或回退父风险金额）
    amounts = [p["amount_usd"] for p in z["detail"]["pending_proposals"]]
    assert amounts == sorted((a for a in amounts if a is not None), reverse=True) + \
        [a for a in amounts if a is None]
    for p in z["detail"]["pending_proposals"]:
        if p["amount_usd"] is not None:
            assert p["amount_source"] in ("proposal_params.est_cost_usd",
                                          "risk_events.affected_value_usd")


# ═══════════════════════════════════════════════════════════════════════════
# 角色脱敏：钱区对 ops 掩码（与 UI COST_FIELDS 同规），tier 走本体 sensitiveFieldRules
# ═══════════════════════════════════════════════════════════════════════════
def test_vitals_money_masked_for_ops(client):
    ops = _zones(client.get("/cockpit/vitals", headers={"X-Role": "ops"}))
    assert ops["money"]["headline_value"] == MASK, "钱区 headline 对 ops 应掩码"
    assert ops["money"]["detail"]["fee_exposure"]["value_usd"] == MASK
    assert ops["money"]["detail"]["margin_distribution"] == MASK
    assert isinstance(ops["money"]["alert_count"], int), "计数不掩码"
    assert ops["money"]["detail"]["fee_exposure"]["open_risks"] > 0, "计数保持可见"
    # 其他区金额键同规掩码（客户敞口/供应商对账差异），比率与计数不掩
    assert all(t["exposure_usd"] == MASK for t in ops["customers"]["detail"]["top_exposure"])
    assert ops["suppliers"]["detail"]["recon_diff_r7_r13"]["amount_usd"] == MASK
    assert isinstance(ops["fulfillment"]["headline_value"], float), "OTD 比率对 ops 可见"
    # tier 本体规则：ops 掩码 / cs 明文（同一套 sensitiveFieldRules，不是第二套）
    assert all(h["tier"] == MASK for h in ops["customers"]["detail"]["health_cross"])
    cs = _zones(client.get("/cockpit/vitals", headers={"X-Role": "cs"}))
    assert any(h["tier"] != MASK for h in cs["customers"]["detail"]["health_cross"])

    # G2：应收/应付/净流出金额同规掩码；计数、breach、window_days 是状态/计数，不掩
    recv, pay, nc = (ops["money"]["detail"]["receivables"], ops["money"]["detail"]["payables"],
                     ops["money"]["detail"]["net_cash_14d"])
    assert recv["amount_usd"] == MASK and recv["overdue"]["amount_usd"] == MASK
    assert isinstance(recv["count"], int) and isinstance(recv["overdue"]["count"], int), "计数不掩码"
    assert pay["amount_usd"] == MASK and pay["overdue"]["amount_usd"] == MASK
    assert nc["value_usd"] == MASK and nc["out_scheduled_usd"] == MASK
    assert nc["in_scheduled_usd"] == MASK and nc["threshold_usd"] == MASK
    assert isinstance(nc["breach"], bool), "breach 是告警状态，不掩码"
    assert isinstance(nc["window_days"], int), "窗口天数不掩码"

    fin = _zones(client.get("/cockpit/vitals", headers={"X-Role": "finance"}))
    assert isinstance(fin["money"]["headline_value"], (int, float)), "finance 见真金额"
    assert isinstance(fin["money"]["detail"]["margin_distribution"], dict)
    assert isinstance(fin["money"]["detail"]["receivables"]["amount_usd"], (int, float))
    assert isinstance(fin["money"]["detail"]["net_cash_14d"]["value_usd"], (int, float))


# ═══════════════════════════════════════════════════════════════════════════
# panorama：结构 / 聚合规则 / 引用完整性 / 异常锚定守恒 / 金额掩码
# ═══════════════════════════════════════════════════════════════════════════
def test_panorama_structure_and_integrity(client, con):
    resp = client.get("/cockpit/panorama", headers={"X-Role": "manager"})
    assert resp.status_code == 200
    d = resp.json()
    assert set(d["layers"]) == {"customers", "orders", "shipments", "suppliers", "warehouses"}
    # 聚合规则现查：活跃票数>40 ⇒ shipments 层聚合为 lane 分组且节点数 ≤ 40
    active = con.execute("SELECT count(*) FROM shipments WHERE status!='delivered'").fetchone()[0]
    ship_layer = d["layers"]["shipments"]
    if active > 40:
        assert ship_layer["granularity"] == "group" and len(ship_layer["nodes"]) <= 40
    else:
        assert ship_layer["granularity"] == "entity" and len(ship_layer["nodes"]) == active
    # 边引用完整性：每条边两端都是已呈现节点
    node_ids = {n["id"] for layer in d["layers"].values() for n in layer["nodes"]}
    for e in d["edges"]:
        assert e["source"] in node_ids and e["target"] in node_ids, e
        assert e["via"] and e["count"] >= 1
    # 异常锚定守恒：Σ节点 alert_count + unanchored 总数 == open 风险总数（现查）
    open_total = con.execute("SELECT count(*) FROM risk_events WHERE status='open'").fetchone()[0]
    anchored = sum(n["alert_count"] for layer in d["layers"].values() for n in layer["nodes"])
    assert anchored + d["meta"]["alerts_unanchored_total"] == open_total == \
        d["meta"]["open_risks_total"]
    # 仓库迷你指标（击穿计数）现查对照
    breach = dict(con.execute("SELECT warehouse_id, count(*) FROM inventory_positions "
                              "WHERE available_qty < safety_stock GROUP BY warehouse_id"))
    for n in d["layers"]["warehouses"]["nodes"]:
        assert n["safety_breach_count"] == breach.get(n["label"], 0)


def test_panorama_money_masked_for_ops(client):
    """金额键掩码的跨角色一致性（数据鲁棒写法：当前两世界在途行均无申报价值 → 节点值多为
    null；凡 finance 见到数值的节点位，ops 必须是 MASK，ops 绝不出现数值明文）。"""
    d_ops = client.get("/cockpit/panorama", headers={"X-Role": "ops"}).json()
    d_fin = client.get("/cockpit/panorama", headers={"X-Role": "finance"}).json()
    fin_by_id = {n["id"]: n for layer in d_fin["layers"].values() for n in layer["nodes"]}
    for layer in d_ops["layers"].values():
        for n in layer["nodes"]:
            if "in_transit_value_usd" not in n:
                continue
            v_ops, v_fin = n["in_transit_value_usd"], fin_by_id[n["id"]]["in_transit_value_usd"]
            assert v_ops in (None, MASK), f"ops 不应见在途货值明文：{n['id']}={v_ops}"
            if isinstance(v_fin, (int, float)):
                assert v_ops == MASK, f"finance 有值的节点 {n['id']} 对 ops 必须掩码"
            else:
                assert v_ops == v_fin is None, "无值节点两角色都应为 null（不掩 null）"


# ═══════════════════════════════════════════════════════════════════════════
# ai-flow：来源合并 / 倒序 / limit
# ═══════════════════════════════════════════════════════════════════════════
def test_ai_flow_verification(client, con):
    resp = client.get("/cockpit/ai-flow", headers={"X-Role": "manager"})
    assert resp.status_code == 200
    d = resp.json()
    expected = (
        con.execute("SELECT count(*) FROM llm_calls").fetchone()[0]
        + con.execute("SELECT count(*) FROM action_log WHERE actor='ai-agent'").fetchone()[0]
        + con.execute("""SELECT count(*) FROM action_log
                         WHERE action IN ('AssignTask','ProposeMitigation',
                                          'ApproveMitigation','RejectMitigation')
                         AND result='ok' AND actor != 'ai-agent'""").fetchone()[0])
    assert d["count"] == len(d["items"]) == min(50, expected)
    ts_list = [i["ts"] for i in d["items"]]
    assert ts_list == sorted(ts_list, reverse=True), "必须时间倒序"
    for i in d["items"]:
        assert {"ts", "kind", "summary", "ref_object", "detail", "sim"} <= set(i)
        assert i["sim"] is False, "验证世界不应有 sim 徽标条目"
        assert i["kind"] in ("llm_call", "ai_action", "task_flow")
    assert any(i["kind"] == "task_flow" for i in d["items"])


def test_ai_flow_limit_param(client):
    resp = client.get("/cockpit/ai-flow", params={"limit": 5}, headers={"X-Role": "manager"})
    assert resp.status_code == 200 and len(resp.json()["items"]) <= 5
    assert resp.json()["limit"] == 5
    assert client.get("/cockpit/ai-flow", params={"limit": 0}).status_code == 422
    assert client.get("/cockpit/ai-flow", params={"limit": 9999}).status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# 双世界：ONTOLOGY_DB → simworld 副本（真实环境变量链路）
# ═══════════════════════════════════════════════════════════════════════════
def test_simworld_vitals_not_500_with_honest_reasons(sim_world):
    c, scon = sim_world
    resp = c.get("/cockpit/vitals", headers={"X-Role": "manager"})
    assert resp.status_code == 200, resp.text[:500]
    data = resp.json()
    assert data["world"] == "simulation"
    assert [z["zone"] for z in data["zones"]] == ZONE_ORDER
    zones = {z["zone"]: z for z in data["zones"]}
    # 缺域指标必须 value=null + reason 如实标注（仍合法缺的域：llm_calls）
    for zone, path in [("ai", "llm_calls")]:
        metric = zones[zone]["detail"][path]
        assert metric["value"] is None and "该世界无此域数据" in metric["reason"], (zone, path, metric)
    # overdue_tasks：动态跟随（G1 给 sim tasks 补了 due_at 列后指标应点亮——不再硬编码"必缺"）
    has_due = any(r[1] == "due_at" for r in scon.execute("PRAGMA table_info(tasks)"))
    ot = zones["decisions"]["detail"]["overdue_tasks"]
    if has_due:
        expected_ot = scon.execute(
            "SELECT count(*) FROM tasks WHERE status NOT IN ('done','cancelled') "
            "AND due_at IS NOT NULL AND due_at != '' AND date(due_at) < ?",
            (data["clock"],)).fetchone()[0]
        assert ot.get("value") == expected_ot, (ot, expected_ot)
    else:
        assert ot["value"] is None and "该世界无此域数据" in ot["reason"], ot
    # F2 补灌域：数据在库 ⇒ 指标必须点亮（非 null 包装、直给数据体）且与现查一致
    # （动态跟随库内现实，不硬编码补灌进度；点亮态形状=数据体，缺数态形状={value:null,reason}）
    def lit(zone, path):
        m = zones[zone]["detail"][path]
        assert m.get("value", "LIT") is not None, (zone, path, "应点亮却是 null 包装", m)
        return m
    if scon.execute("SELECT count(*) FROM cost_scenarios").fetchone()[0] > 0:
        md = lit("money", "margin_distribution")
        assert md["scenarios_total"] == scon.execute(
            "SELECT count(*) FROM cost_scenarios").fetchone()[0]
    if scon.execute("SELECT count(*) FROM goods_receipts").fetchone()[0] > 0:
        assert 0 < lit("suppliers", "delivery_hit_rate")["rate"] <= 1
        assert len(lit("suppliers", "defect_top")["top"]) > 0
    cc = scon.execute("SELECT count(*) FROM cycle_counts WHERE variance != 0").fetchone()[0]
    if cc:
        assert lit("inventory", "count_variance")["count"] == cc
    # 在途货值：申报价值有值 ⇒ 点亮为正数；全空 ⇒ null + 如实 reason（双态都守）
    unvalued = scon.execute("SELECT count(*) FROM skus WHERE declared_value_usd IS NULL "
                            "OR declared_value_usd=''").fetchone()[0]
    total_skus = scon.execute("SELECT count(*) FROM skus").fetchone()[0]
    itv = zones["money"]["detail"]["in_transit_value"]
    if unvalued == total_skus:
        assert itv["value"] is None and "申报价值" in itv["reason"]
    else:
        assert itv["value_usd"] > 0 and itv["rows_without_declared_value"] == 0
    # 有数指标照常计算并与 sim 副本现查一致
    row = scon.execute("SELECT count(*) c, round(sum(affected_value_usd),2) v FROM risk_events "
                       "WHERE rule_id IN ('R4','R5','R6') AND status='open'").fetchone()
    assert zones["money"]["headline_value"] == row["v"]
    assert zones["money"]["alert_count"] == row["c"]
    mem = scon.execute("SELECT count(*) FROM resolution_memory").fetchone()[0]
    assert zones["ai"]["detail"]["resolution_memory"]["total"] == mem > 0
    blocked = scon.execute("SELECT round(sum(affected_value_usd),2) FROM risk_events "
                           "WHERE rule_id='R4' AND status='resolved'").fetchone()[0]
    assert zones["money"]["detail"]["intercepted_overbilling"]["value_usd"] == blocked
    # G2：应收/应付/净流出预警——simworld（第二世界）同样与手工 SQL 现查对照，不誊抄数字
    clock = data["clock"]
    for direction, key in (("in", "receivables"), ("out", "payables")):
        base = scon.execute(
            "SELECT count(*) c, round(sum(amount_usd),2) v FROM payments "
            "WHERE direction=? AND status='scheduled'", (direction,)).fetchone()
        metric = zones["money"]["detail"][key]
        assert metric["count"] == base["c"]
        assert metric["amount_usd"] == (base["v"] or 0.0)
        overdue = scon.execute(
            "SELECT count(*) c, round(sum(amount_usd),2) v FROM payments "
            "WHERE direction=? AND status='scheduled' AND due_date < ? "
            "AND (paid_date IS NULL OR paid_date='')", (direction, clock)).fetchone()
        assert metric["overdue"]["count"] == overdue["c"]
        assert metric["overdue"]["amount_usd"] == (overdue["v"] or 0.0)
    window_days = _FINANCE_CFG["cash_watch_window_days"]
    threshold = _FINANCE_CFG["cash_watch_threshold_usd"]
    win_hi = scon.execute("SELECT date(?, ?)", (clock, f"+{window_days} days")).fetchone()[0]
    out_win = scon.execute(
        "SELECT round(sum(amount_usd),2) v FROM payments WHERE direction='out' "
        "AND status='scheduled' AND due_date > ? AND due_date <= ?", (clock, win_hi)).fetchone()[0]
    in_win = scon.execute(
        "SELECT round(sum(amount_usd),2) v FROM payments WHERE direction='in' "
        "AND status='scheduled' AND due_date > ? AND due_date <= ?", (clock, win_hi)).fetchone()[0]
    net = round((out_win or 0.0) - (in_win or 0.0), 2)
    nc = zones["money"]["detail"]["net_cash_14d"]
    assert nc["out_scheduled_usd"] == (out_win or 0.0) and nc["in_scheduled_usd"] == (in_win or 0.0)
    assert nc["value_usd"] == net and nc["breach"] == (net > threshold)
    assert nc["threshold_usd"] == threshold and nc["window_days"] == window_days
    # trend：AI 区有 detected_at 流水支撑 → 7 天对比为测量值；无快照支撑区仍 null
    ai_trend = zones["ai"]["trend"]
    assert ai_trend is not None and ai_trend["window_days"] == 7
    cur = scon.execute("SELECT count(*) FROM risk_events WHERE date(detected_at) "
                       "BETWEEN date(?,'-6 days') AND ?", (clock, clock)).fetchone()[0]
    assert ai_trend["current"] == cur
    assert zones["money"]["trend"] is None and zones["customers"]["trend"] is None


def test_simworld_panorama_and_ai_flow(sim_world):
    c, scon = sim_world
    resp = c.get("/cockpit/panorama", headers={"X-Role": "manager"})
    assert resp.status_code == 200
    d = resp.json()
    assert d["world"] == "simulation"
    assert all(len(layer["nodes"]) > 0 for layer in d["layers"].values())
    open_total = scon.execute("SELECT count(*) FROM risk_events "
                              "WHERE status='open'").fetchone()[0]
    anchored = sum(n["alert_count"] for layer in d["layers"].values() for n in layer["nodes"])
    assert anchored + d["meta"]["alerts_unanchored_total"] == open_total

    resp = c.get("/cockpit/ai-flow", params={"limit": 30}, headers={"X-Role": "manager"})
    assert resp.status_code == 200
    d = resp.json()
    # 2026-07-17 世界观更新：X-World 写路径 + Agent runtime 落地后，模拟世界不再只有仿真历史——
    # 真实运营活动（action_log 的 ai_action/流转等）会如实合流进 ai-flow。断言改为分族校验：
    # sim 徽标条目守仿真闭环词表；非 sim 条目是真实运营痕（ai_action 等），有自己的合法形状。
    total_available = scon.execute("SELECT count(*) FROM sim_ai_activity").fetchone()[0]
    assert d["count"] == min(30, total_available) if total_available < 30 else d["count"] == 30
    assert d["items"], "模拟世界应有 AI 闭环留痕条目"
    assert any(i["sim"] for i in d["items"]), "仿真历史条目不应消失"
    for i in d["items"]:
        if i["sim"]:
            assert i["kind"] in ("detect", "propose", "approve", "reject", "close")
        else:
            assert i["kind"] in ("ai_action", "flow", "llm"), f"真实运营条目 kind 异常：{i['kind']}"
    ts_list = [i["ts"] for i in d["items"]]
    assert ts_list == sorted(ts_list, reverse=True)


# ═══════════════════════════════════════════════════════════════════════════
# 时间轴回放（as_of，A-2/V13②）：byte-identical 缺省 / 窗口 / 回放重算 / 存量诚实标注 /
# 事件流过滤 / 脱敏在回放路径同样生效。现查现算对照，不誊抄具体数字。
# ═══════════════════════════════════════════════════════════════════════════
# 在途票时点复算的独立 SQL（与实现 _in_transit_as_of 同口径、另写一遍作对照）
_IN_TRANSIT_AS_OF_SQL = """
    SELECT count(*) FROM (
      SELECT shipment_id,
             max(CASE WHEN event_type='departed' AND date(event_time)<=? THEN 1 ELSE 0 END) dep,
             max(CASE WHEN event_type IN ('arrived','delivered')
                      AND date(event_time)<=? THEN 1 ELSE 0 END) arr
      FROM shipment_milestones GROUP BY shipment_id) WHERE dep=1 AND arr=0"""


def _mid_date(start: str, clock: str) -> str:
    """窗口内严格居中的一天（start<mid<clock），不硬编码具体日期。"""
    import datetime as _dt
    s, c = _dt.date.fromisoformat(start), _dt.date.fromisoformat(clock)
    return (s + (c - s) / 2).isoformat()


def test_window_present_on_all_three(client):
    """三端点均回传 window={start,end}，start≤end（滑条定义域）；带 clock 的端点 end=世界时钟。"""
    for path in ("/cockpit/vitals", "/cockpit/panorama", "/cockpit/ai-flow"):
        d = client.get(path, headers={"X-Role": "manager"}).json()
        assert "window" in d, path
        assert d["window"]["start"] <= d["window"]["end"], path
        if "clock" in d:  # ai-flow 载荷向来无 clock 字段；vitals/panorama 有，end 应=世界时钟
            assert d["window"]["end"] == d["clock"], path


def test_as_of_default_byte_identical(client):
    """缺省/≥世界时钟/非法 as_of → 不回放：无 as_of 信封、zones 与现状逐字一致（byte-identical）。"""
    base = client.get("/cockpit/vitals", headers={"X-Role": "manager"}).json()
    assert "as_of" not in base
    for q in (base["clock"], "2099-12-31", "not-a-date"):
        r = client.get("/cockpit/vitals", params={"as_of": q}, headers={"X-Role": "manager"}).json()
        assert "as_of" not in r, q
        assert r["zones"] == base["zones"], q
        assert r["clock"] == base["clock"], q
    # panorama / ai-flow 同样：缺省与 ≥clock 无信封、主体不变
    for path, key in (("/cockpit/panorama", "meta"), ("/cockpit/ai-flow", "items")):
        b = client.get(path, headers={"X-Role": "manager"}).json()
        f = client.get(path, params={"as_of": "2099-01-01"}, headers={"X-Role": "manager"}).json()
        assert "as_of" not in b and "as_of" not in f
        assert b[key] == f[key], path


def test_as_of_clamp_and_replay_gate(client):
    """as_of<窗口起点 → 夹到 window_start；window_start≤as_of<clock → 回放态且 effective=as_of。"""
    base = client.get("/cockpit/vitals", headers={"X-Role": "manager"}).json()
    start, clock = base["window"]["start"], base["clock"]
    clamped = client.get("/cockpit/vitals", params={"as_of": "2000-01-01"},
                         headers={"X-Role": "manager"}).json()
    assert clamped["as_of"]["is_replay"] and clamped["as_of"]["effective"] == start
    inside = _mid_date(start, clock)
    rep = client.get("/cockpit/vitals", params={"as_of": inside},
                     headers={"X-Role": "manager"}).json()
    assert rep["as_of"]["is_replay"] and rep["as_of"]["effective"] == inside
    assert rep["as_of"]["world_clock"] == clock and rep["clock"] == clock  # clock 不随拖动改


def test_verification_replay_snapshot_honesty(client, con):
    """验证世界=静态快照：回放态风险类 headline 归 current 且值与现状一致（不重建、不造 0）；
    唯里程碑复算的在途票数（in_transit_as_of）真回放，独立 SQL 对照。"""
    base = client.get("/cockpit/vitals", headers={"X-Role": "manager"}).json()
    start, clock = base["window"]["start"], base["clock"]
    assert start < clock, "里程碑应给验证世界一个可拖窗口"
    as_of = _mid_date(start, clock)
    rep = client.get("/cockpit/vitals", params={"as_of": as_of},
                     headers={"X-Role": "manager"}).json()
    env = rep["as_of"]
    assert env["is_replay"] and env["world_is_sim"] is False
    bz = {z["zone"]: z for z in base["zones"]}
    rz = {z["zone"]: z for z in rep["zones"]}
    # 风险类/存量类 headline：current 标注 + 值不变（静态快照无逐日史，绝不造假历史）
    for zn in ("money", "customers", "suppliers", "inventory", "fulfillment"):
        assert rz[zn]["headline_as_of"] == "current", zn
        assert rz[zn]["headline_value"] == bz[zn]["headline_value"], zn
    # in_transit_as_of：里程碑事件流复算，独立 SQL 对照（验证世界也真回放这一项）
    expect_it = con.execute(_IN_TRANSIT_AS_OF_SQL, (as_of, as_of)).fetchone()[0]
    assert rz["money"]["detail"]["in_transit_as_of"]["count"] == expect_it
    # AI 今日=事件流逐日复算（恒 replayed）；过去日检测=0 是 detected_at 批量单日的如实读数
    assert rz["ai"]["headline_as_of"] == "replayed"
    # 信封诚实枚举：验证世界风险类进 current_state_only（不谎称可回放）
    assert "money.fee_exposure" in env["current_state_only"]
    assert "money.in_transit_as_of" in env["replayable"]


def test_verification_replay_masking_preserved(client):
    """X-Role 脱敏在 as_of 路径同样生效：ops 请求回放态，钱区金额仍 MASK。"""
    base = client.get("/cockpit/vitals", headers={"X-Role": "manager"}).json()
    as_of = _mid_date(base["window"]["start"], base["clock"])
    money = _zones(client.get("/cockpit/vitals", params={"as_of": as_of},
                              headers={"X-Role": "ops"}))["money"]
    assert money["headline_value"] == MASK
    assert money["detail"]["fee_exposure"]["value_usd"] == MASK
    assert isinstance(money["detail"]["fee_exposure"]["open_risks"], int)  # 计数不掩


def test_simworld_vitals_replay(sim_world):
    """模拟世界（真历史）：回放态风险类按 detected_at/resolved_at 时点重建、AI 今日逐日复算——
    每项独立 SQL 现查对照（另写一遍口径，不誊抄数字）；存量类如实标 current。"""
    c, scon = sim_world
    base = c.get("/cockpit/vitals", headers={"X-Role": "manager"}).json()
    clock = base["clock"]
    # 取严格居于检测窗口内的一天（min<as_of<max detected day），保证两侧都有数据
    as_of = scon.execute(
        "SELECT date(detected_at) FROM risk_events "
        "WHERE date(detected_at) > (SELECT min(date(detected_at)) FROM risk_events) "
        "AND date(detected_at) < (SELECT max(date(detected_at)) FROM risk_events) "
        "ORDER BY detected_at LIMIT 1 "
        "OFFSET (SELECT count(DISTINCT date(detected_at))/2 FROM risk_events)").fetchone()[0]
    assert base["window"]["start"] < as_of < clock
    rep = c.get("/cockpit/vitals", params={"as_of": as_of},
                headers={"X-Role": "manager"}).json()
    env = rep["as_of"]
    assert env["is_replay"] and env["world_is_sim"] and env["effective"] == as_of
    rz = {z["zone"]: z for z in rep["zones"]}
    # headline 归类：钱/客户/AI=replayed，供应商/履约/库存/待拍板=current
    assert rz["money"]["headline_as_of"] == rz["customers"]["headline_as_of"] == "replayed"
    assert rz["ai"]["headline_as_of"] == "replayed"
    for zn in ("suppliers", "fulfillment", "inventory", "decisions"):
        assert rz[zn]["headline_as_of"] == "current", zn
    # 费用敞口 R4-R6：截至 as_of 仍未闭环（独立重写 detected_at/resolved_at 口径）
    exp = scon.execute(
        "SELECT count(*) c, round(sum(affected_value_usd),2) v FROM risk_events "
        "WHERE rule_id IN ('R4','R5','R6') AND date(detected_at)<=? "
        "AND (resolved_at IS NULL OR resolved_at='' OR date(resolved_at)>?)",
        (as_of, as_of)).fetchone()
    fe = rz["money"]["detail"]["fee_exposure"]
    assert fe["open_risks"] == exp["c"]
    assert rz["money"]["headline_value"] == exp["v"]
    # 供应商单一依赖 R14 + 对账 R7-R13：同口径时点重建
    r14 = scon.execute(
        "SELECT count(*) FROM risk_events WHERE rule_id='R14' AND date(detected_at)<=? "
        "AND (resolved_at IS NULL OR resolved_at='' OR date(resolved_at)>?)",
        (as_of, as_of)).fetchone()[0]
    assert rz["suppliers"]["detail"]["single_source_r14"]["value"] == r14
    # 在途票时点复算
    expect_it = scon.execute(_IN_TRANSIT_AS_OF_SQL, (as_of, as_of)).fetchone()[0]
    assert rz["money"]["detail"]["in_transit_as_of"]["count"] == expect_it
    # 待拍板超期任务：due_at<as_of 时点重算
    od = scon.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('done','cancelled') "
                      "AND due_at IS NOT NULL AND due_at!='' AND date(due_at)<?",
                      (as_of,)).fetchone()[0]
    assert rz["decisions"]["detail"]["overdue_tasks"]["value"] == od
    # AI 今日检测数：date(detected_at)=as_of（逐日复算）
    det = scon.execute("SELECT count(*) FROM risk_events WHERE date(detected_at)=?",
                       (as_of,)).fetchone()[0]
    assert rz["ai"]["detail"]["today"]["detections"] == det


def test_simworld_ai_flow_replay(sim_world):
    """模拟世界 ai-flow 事件流：as_of 过滤后所有条目 ts≤as_of，条数=独立现查 min(limit, 截至当日)。"""
    c, scon = sim_world
    as_of = scon.execute(
        "SELECT sim_date FROM sim_ai_activity "
        "WHERE sim_date > (SELECT min(sim_date) FROM sim_ai_activity) "
        "AND sim_date < (SELECT max(sim_date) FROM sim_ai_activity) "
        "ORDER BY sim_date LIMIT 1 "
        "OFFSET (SELECT count(DISTINCT sim_date)/2 FROM sim_ai_activity)").fetchone()[0]
    rep = c.get("/cockpit/ai-flow", params={"limit": 500, "as_of": as_of},
                headers={"X-Role": "manager"}).json()
    assert rep["as_of"]["is_replay"]
    assert all(i["ts"] <= as_of for i in rep["items"]), "回放态不得出现晚于 as_of 的事件"
    expected = scon.execute("SELECT count(*) FROM sim_ai_activity WHERE sim_date<=?",
                            (as_of,)).fetchone()[0]
    assert rep["count"] == min(500, expected)


def test_simworld_panorama_replay_anchoring(sim_world):
    """模拟世界 panorama：回放态锚定的风险集按 _risk_active 时点重建，守恒仍成立、总数=独立现查。"""
    c, scon = sim_world
    as_of = scon.execute(
        "SELECT date(detected_at) FROM risk_events "
        "WHERE date(detected_at) > (SELECT min(date(detected_at)) FROM risk_events) "
        "AND date(detected_at) < (SELECT max(date(detected_at)) FROM risk_events) "
        "ORDER BY detected_at LIMIT 1 "
        "OFFSET (SELECT count(DISTINCT date(detected_at))/2 FROM risk_events)").fetchone()[0]
    d = c.get("/cockpit/panorama", params={"as_of": as_of},
              headers={"X-Role": "manager"}).json()
    assert d["as_of"]["is_replay"]
    active = scon.execute(
        "SELECT count(*) FROM risk_events WHERE date(detected_at)<=? "
        "AND (resolved_at IS NULL OR resolved_at='' OR date(resolved_at)>?)",
        (as_of, as_of)).fetchone()[0]
    assert d["meta"]["open_risks_total"] == active
    anchored = sum(n["alert_count"] for layer in d["layers"].values() for n in layer["nodes"])
    assert anchored + d["meta"]["alerts_unanchored_total"] == active  # 守恒


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
