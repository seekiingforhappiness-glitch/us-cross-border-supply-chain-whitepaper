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
import re
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


def _ensure_pending_anchor(con, assignee_role="ops", est_cost=1234.5, tid=None):
    """V21 测试自愈种子（同 test_decisions 惯例，2026-07-19 第四例世界观更新：验证世界待批余量
    是易变状态——人在驾驶舱批完即清零，测试自造锚点于临时副本，绝不依赖演示库余量）。"""
    tid = tid or f"TSK-TEST-V21-{assignee_role.upper()}"
    if con.execute("SELECT 1 FROM tasks WHERE task_id=?", (tid,)).fetchone():
        return tid
    cols = [r[1] for r in con.execute("PRAGMA table_info(tasks)")]
    donor = con.execute("SELECT * FROM tasks WHERE proposal_actor_id IS NOT NULL "
                        "AND proposed_action IS NOT NULL LIMIT 1").fetchone()
    assert donor, "库未跑过 seed_demo_ops（回归链缺步，勘误#2）"
    row = dict(zip(cols, donor))
    row.update(task_id=tid, approval_status="pending", status="in_progress",
               assignee_role=assignee_role, approved_by_role=None, action_taken=None,
               proposal_params=json.dumps({"est_cost_usd": est_cost, "reason": "v21-anchor"}))
    con.execute(f"INSERT INTO tasks ({','.join(cols)}) VALUES ({','.join('?'*len(cols))})",
                [row[c] for c in cols])
    con.commit()
    return tid


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
    # 隔离修复（2026-07-19，预存地雷）：原先 finally 里直接 pop 会把**模块级 override 一并拆除**，
    # 其后同模块所有用例静默漏到真库（真库待批被人批空后 V21 用例莫名空队列由此而来）。
    # 改保存/恢复式（同 other_team 用例的正确写法）。
    saved = app.dependency_overrides.get(get_db_path)
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
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        else:
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
# V21① 自队金额可见（决策日志 V21①，Daniel 2026-07-19"1.可以"）：待批提案金额对指派团队角色可见，
# 驾驶舱聚合脱敏 / 对象读端点(/objects) / AI 工具面(SensitiveFieldMasker) 三面同源同判
# ═══════════════════════════════════════════════════════════════════════════
def test_v21_amount_visibility_pure_functions():
    """三面唯一权威源纯函数（agent.tools）：自队例外 + 提案金额统一可见性。"""
    from agent.tools import own_team_amount_visible, proposal_amount_visible
    assert own_team_amount_visible("ops", "ops") is True
    assert own_team_amount_visible("ops", "finance") is False
    assert own_team_amount_visible("ops", None) is False        # 兜底保守：缺失→False
    assert own_team_amount_visible("ops", "") is False          # 空→False
    assert own_team_amount_visible("", "ops") is False
    assert proposal_amount_visible("ops", "ops") is True        # 自队
    assert proposal_amount_visible("ops", "finance") is False   # 他队且无成本权限→掩
    assert proposal_amount_visible("cs", "cs") is True          # cs 自队（颗粒度放宽）
    assert proposal_amount_visible("cs", "ops") is False        # cs 他队
    assert proposal_amount_visible("finance", "ops") is True    # 成本角色见全部
    assert proposal_amount_visible("manager", "cs") is True     # manager 不变


def test_vitals_decisions_own_team_amount_v21(client, con):
    """V21① 驾驶舱面：待批提案金额对指派团队可见。验证世界待批均 ops 指派 →
    ops(自队)见真值、cs(他队且无成本权限)掩码、manager 照旧全见。金额现查库真值不硬编码。"""
    _ensure_pending_anchor(con, "ops")            # 自愈锚点（不依赖演示库待批余量）
    def by_tid(role):
        z = _zones(client.get("/cockpit/vitals", headers={"X-Role": role}))["decisions"]
        return {p["task_id"]: p["amount_usd"] for p in z["detail"]["pending_proposals"]}
    ops, cs, mgr = by_tid("ops"), by_tid("cs"), by_tid("manager")
    assert ops, "自愈种子后必有待批锚点"
    for tid, mgr_amt in mgr.items():
        assignee = con.execute("SELECT assignee_role FROM tasks WHERE task_id=?",
                               (tid,)).fetchone()[0]
        if mgr_amt is None:                       # 无金额（无 est_cost 且无风险回退）→ 各角色皆 None，不掩
            assert ops[tid] is None and cs[tid] is None
            continue
        if assignee == "ops":                     # 自队：ops 见真值(=manager 真值)、cs 掩码
            assert ops[tid] == mgr_amt, f"{tid} ops 自队应见真值"
            assert cs[tid] == MASK, f"{tid} cs 他队应掩码"
        else:                                     # 他队（若存在）：ops 掩码
            assert ops[tid] == MASK, f"{tid} ops 他队应掩码"


def test_vitals_decisions_other_team_masked_v21(tmp_path):
    """V21① 反面（他队掩码）：把一条 est_cost_usd 待批提案改指派 finance 后——ops 掩码、finance(自队)
    见真值、manager 照旧真值、cs 掩码。自成一体临时库 + 保存/恢复 module override，不污染其他用例。"""
    dest = tmp_path / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)
    c = sqlite3.connect(dest)
    row = c.execute("SELECT task_id, proposal_params FROM tasks WHERE approval_status='pending' "
                    "AND proposal_params LIKE '%est_cost_usd%' ORDER BY task_id LIMIT 1").fetchone()
    if not row:                                   # 自愈锚点（第四例：不依赖演示库待批余量）
        tid0 = _ensure_pending_anchor(c, "ops", est_cost=987.6)
        row = c.execute("SELECT task_id, proposal_params FROM tasks WHERE task_id=?",
                        (tid0,)).fetchone()
    tid, true_amt = row[0], round(json.loads(row[1])["est_cost_usd"], 2)
    c.execute("UPDATE tasks SET assignee_role='finance' WHERE task_id=?", (tid,))
    c.commit(); c.close()

    saved = app.dependency_overrides.get(get_db_path)
    app.dependency_overrides[get_db_path] = lambda: str(dest)
    try:
        with TestClient(app) as cl:
            def amt(role):
                z = _zones(cl.get("/cockpit/vitals", headers={"X-Role": role}))["decisions"]
                return next(p["amount_usd"] for p in z["detail"]["pending_proposals"]
                            if p["task_id"] == tid)
            assert amt("ops") == MASK, "ops 对他队(finance)提案金额应掩码"
            assert amt("finance") == true_amt, "finance 对自队提案金额应见真值"
            assert amt("manager") == true_amt, "manager 照旧见真值"
            assert amt("cs") == MASK, "cs 他队应掩码"
    finally:
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        else:
            app.dependency_overrides.pop(get_db_path, None)


def test_objects_task_amount_own_team_v21(client, con):
    """V21① 对象读端点(/objects/Task)：proposal_params.est_cost_usd 自队例外——ops 见 ops 指派任务、
    掩 finance 指派任务；对应团队自见；manager 照旧全见。锚点现查（不硬编码 id）。"""
    def anchor(assignee):
        r = con.execute("SELECT task_id, proposal_params FROM tasks WHERE assignee_role=? "
                        "AND proposal_params LIKE '%est_cost_usd%' ORDER BY task_id LIMIT 1",
                        (assignee,)).fetchone()
        return (r[0], r[1]) if r else (None, None)

    def obj_cost(tid, role):
        pp = client.get(f"/objects/Task/{tid}", headers={"X-Role": role}).json()["proposal_params"]
        pp = json.loads(pp) if isinstance(pp, str) else pp
        return pp.get("est_cost_usd")

    ops_tid, ops_pp = anchor("ops")
    assert ops_tid, "需一条 ops 指派且带 est_cost_usd 的任务"
    ops_true = json.loads(ops_pp)["est_cost_usd"]
    assert obj_cost(ops_tid, "ops") == ops_true          # 自队 → 真值
    assert obj_cost(ops_tid, "manager") == ops_true      # manager 不变
    assert obj_cost(ops_tid, "cs") == MASK               # cs 他队 → 掩码
    assert obj_cost(ops_tid, "finance") == ops_true      # finance 成本角色 → 见

    fin_tid, fin_pp = anchor("finance")
    if fin_tid:                                          # 验证世界有 finance 指派带成本任务
        fin_true = json.loads(fin_pp)["est_cost_usd"]
        assert obj_cost(fin_tid, "ops") == MASK          # ops 他队 → 掩码（关键：非自队脱敏）
        assert obj_cost(fin_tid, "finance") == fin_true  # finance 自队 → 真值
        assert obj_cost(fin_tid, "manager") == fin_true  # manager 不变


def test_v21_three_faces_same_judgment(client, con):
    """V21① 三面一致性：同一 ops 指派待批提案，经①驾驶舱待批队列 ②/objects Task 读 ③AI 工具面
    SensitiveFieldMasker，ops 视角三者同判（同为真值）。
    第三面说明：现无返回 Task.proposal_params 的 MCP 读工具（aiQueryTools 皆 risk/admission/cost 域，
    均不含 Task est_cost），故直接对 AI 面共用的 SensitiveFieldMasker 单测——/objects 与 MCP call_tool
    共用同一 masker 类逻辑（mcp_server.OntologyMCPServer.masker），此单测即第三面的代码路径。"""
    from agent.mcp_server import SensitiveFieldMasker
    from pipeline.ontology_runtime import load_ontology
    _ensure_pending_anchor(con, "ops")            # 自愈锚点（第四例：不依赖演示库待批余量）
    row = con.execute("SELECT task_id, assignee_role, proposal_params FROM tasks "
                      "WHERE approval_status='pending' AND assignee_role='ops' "
                      "AND proposal_params LIKE '%est_cost_usd%' ORDER BY task_id LIMIT 1").fetchone()
    assert row, "自愈种子后必有 ops 待批带成本锚点"
    tid, assignee, pp = row[0], row[1], json.loads(row[2])

    # 面①驾驶舱待批队列
    z = _zones(client.get("/cockpit/vitals", headers={"X-Role": "ops"}))["decisions"]
    face1 = next(p["amount_usd"] for p in z["detail"]["pending_proposals"] if p["task_id"] == tid)
    # 面②/objects Task 读
    o = client.get(f"/objects/Task/{tid}", headers={"X-Role": "ops"}).json()["proposal_params"]
    face2 = (json.loads(o) if isinstance(o, str) else o)["est_cost_usd"]
    # 面③AI 工具面共用 masker（对象形载荷：带 assignee_role 兄弟 + proposal_params）
    payload = {"task_id": tid, "assignee_role": assignee,
               "proposal_params": json.dumps({"est_cost_usd": pp["est_cost_usd"]})}
    SensitiveFieldMasker(load_ontology(), "ops").mask_value(payload)
    face3 = json.loads(payload["proposal_params"])["est_cost_usd"]

    assert MASK not in (face1, face2, face3), \
        f"ops 自队三面皆应真值不掩：cockpit={face1} objects={face2} masker={face3}"
    assert face2 == face3 == pp["est_cost_usd"], "对象读端点与 AI 面 masker 取原始真值一致"
    assert face1 == round(pp["est_cost_usd"], 2), "驾驶舱聚合口径 round(2) 与真值一致"


# ═══════════════════════════════════════════════════════════════════════════
# V22 任务1 —— 待批提案队列"我组的"筛选（可选 assignee_role 参数）。缘起：财务陌生人实测
# "待批队列里自己的活要靠运气翻到"（docs/research/2026-07-19-ux-stranger-round2.md）。红线：只加
# 可选筛选参数，缺省=全部 pending（老板收件箱）逐字节不变；金额掩码逻辑零改。
# ═══════════════════════════════════════════════════════════════════════════
def _dec(client, assignee_role=None, role="manager"):
    q = f"?assignee_role={assignee_role}" if assignee_role else ""
    return _zones(client.get(f"/cockpit/vitals{q}", headers={"X-Role": role}))["decisions"]


def test_vitals_decisions_assignee_filter_partitions(client, con):
    """assignee_role 只筛待批提案列表：每角色过滤=该角色指派的 pending（现查 SQL 对照，卡片数=列表
    件数=pending_total）；各角色互不重叠、并集=缺省全量（老板收件箱的严格划分）。金额/超期口径不受影响。"""
    _ensure_pending_anchor(con, "ops")
    _ensure_pending_anchor(con, "finance", tid="TSK-TEST-V22-FIN", est_cost=555.0)
    full = _dec(client)
    full_total = full["detail"]["pending_total"]
    roles = [r[0] for r in con.execute(
        "SELECT DISTINCT assignee_role FROM tasks WHERE approval_status='pending' "
        "AND assignee_role IS NOT NULL")]
    assert len(roles) >= 2, "本用例需 ≥2 个指派角色才能验划分（锚点已保证 ops+finance）"
    role_sum = 0
    for r in roles:
        z = _dec(client, r)
        sql_n = con.execute("SELECT count(*) FROM tasks WHERE approval_status='pending' "
                            "AND assignee_role=?", (r,)).fetchone()[0]
        assert z["headline_value"] == z["detail"]["pending_total"] == sql_n, \
            f"{r} 过滤：卡片数=pending_total=该角色 pending 件数({sql_n})"
        assert all(p["assignee_role"] == r for p in z["detail"]["pending_proposals"]), \
            f"{r} 过滤后列表应只含该角色指派行"
        # 超期/升级件是独立生命周期信号，不随"我组的"变（与缺省一致）
        assert z["detail"]["overdue_tasks"] == full["detail"]["overdue_tasks"]
        assert z["detail"]["escalated_tasks"] == full["detail"]["escalated_tasks"]
        role_sum += sql_n
    assert role_sum == full_total, "各角色 pending 件数之和 = 缺省全量（严格划分，无重叠无遗漏）"


def test_vitals_decisions_default_unchanged_by_filter(client, con):
    """钉死缺省不变（V22 任务1 硬红线）：不传 assignee_role → 幂等 + 老板收件箱=全部 pending +
    列表含全部指派角色（未被"我组的"偷偷缩小）。缺省路径逐字节不因新参数漂移。"""
    _ensure_pending_anchor(con, "ops")
    d1 = _dec(client)
    d2 = _dec(client)
    assert d1 == d2, "缺省路径幂等（同请求两次深比较一致）"
    full = con.execute("SELECT count(*) FROM tasks WHERE approval_status='pending'").fetchone()[0]
    assert d1["headline_value"] == full == d1["detail"]["pending_total"], "缺省=全部 pending（收件箱全集）"
    if full <= 50:  # 未触发列表 cap 时，缺省列表应覆盖库中全部指派角色（不被任何筛选预缩）
        roles_in_list = {p["assignee_role"] for p in d1["detail"]["pending_proposals"]}
        roles_in_db = {r[0] for r in con.execute(
            "SELECT DISTINCT assignee_role FROM tasks WHERE approval_status='pending' "
            "AND assignee_role IS NOT NULL")}
        assert roles_in_list == roles_in_db, "缺省列表含全部指派角色，非某一组"


def test_vitals_decisions_assignee_legal_but_empty(client):
    """合法角色但当前世界无其指派待批 → 200 + 空列表（诚实空态，非 422）：这是前端"我组的"空态
    「当前没有指派给〈角色〉的待批提案」依赖的后端行为。验证世界 sales 从无指派 → 空。"""
    z = _dec(client, "sales")
    assert z["detail"]["pending_proposals"] == []
    assert z["headline_value"] == z["detail"]["pending_total"] == 0


def test_vitals_decisions_assignee_role_illegal_422(client):
    """非法 assignee_role → 422 白话（回显非法值 + 合法角色清单 + 指路缺省语义），不静默忽略。"""
    resp = client.get("/cockpit/vitals?assignee_role=wizard", headers={"X-Role": "manager"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "wizard" in detail and "缺省" in detail, "白话错误应回显非法值并指路缺省"
    # 合法值集应与本体派生的业务角色一致（不硬编码字面量：现查 _ROSTER_ROLES）
    from apps.api.cockpit import _ROSTER_ROLES
    assert all(r in detail for r in ("ops", "finance"))
    assert "system" not in _ROSTER_ROLES, "非用户角色 system 不作为合法筛选值"


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
# V22① finance 角色纵向切片：钱区可见 / 合规敏感字段掩码 / 审批 403 / API↔agent 口径同源
# 一切数据范围由 apps/api 按 X-Role 同源执行（宪法不变量 5，前端不复制规则）——本组从 API 侧固化
# finance 的数据范围（钱可见、合规域掩码、审批越权挡回、与 AI 工具面共用同一 _can_see_cost 判定）。
# ═══════════════════════════════════════════════════════════════════════════
def test_vitals_money_visible_for_finance(client):
    """钱区金额对 finance 可见（陈会计核心诉求：财务连自己的钱都看不见）。费用异常敞口 headline、
    应收/应付、14 天净流出三处金额都应是真数字（非 MASK），与 manager 同门（_can_see_cost=finance）。"""
    fin = _zones(client.get("/cockpit/vitals", headers={"X-Role": "finance"}))
    money = fin["money"]
    # ① 费用异常敞口 headline（R4-R6 口径）对 finance 可见
    assert money["headline_value"] != MASK and isinstance(money["headline_value"], (int, float)), \
        "钱区 headline（费用异常敞口）对 finance 应是真金额"
    assert money["detail"]["fee_exposure"]["value_usd"] != MASK
    # ② 应收水位 + 逾期应收（R19）金额对 finance 可见——陈会计"逾期应收可从队列进入"的钱侧锚
    recv = money["detail"]["receivables"]
    assert recv["amount_usd"] != MASK and isinstance(recv["amount_usd"], (int, float))
    assert recv["overdue"]["amount_usd"] != MASK, "逾期应收金额对 finance 应可见"
    # ③ 14 天净流出预警（现金水位）金额对 finance 可见
    assert money["detail"]["net_cash_14d"]["value_usd"] != MASK


def test_objects_compliance_field_masked_for_finance(client, con):
    """对象卡敏感字段脱敏：Supplier.uflpa_risk_flag 是**合规专属**字段（本体 sensitiveFieldRules
    visibleTo=[compliance,manager]）——finance 虽是成本角色，仍**看不到**合规域敏感字段（掩码为
    MASK，绝不返回 null 冒充"无风险"）。证明脱敏是逐字段声明驱动、不是"财务=看全部钱和别人的活"。"""
    row = con.execute("SELECT supplier_id FROM suppliers WHERE uflpa_risk_flag IS NOT NULL "
                      "AND uflpa_risk_flag != '' LIMIT 1").fetchone()
    assert row, "库内应有带 uflpa_risk_flag 的供应商（datagen 产出）"
    sid = row[0]
    def flag(role):
        return client.get(f"/objects/Supplier/{sid}", headers={"X-Role": role}).json()["uflpa_risk_flag"]
    assert flag("finance") == MASK, "finance 对合规专属字段 uflpa_risk_flag 应掩码（不是财务的域）"
    assert flag("compliance") != MASK, "compliance 见真值（visibleTo 含 compliance）"
    assert flag("manager") != MASK, "manager 全域可见"


def test_decisions_finance_forbidden_403(client, con):
    """审批越权挡回：finance 不在任何冻结动作 executors（ApproveMitigation=[manager]）——点批准
    应得 403，与后端 build_role_perms 同源。陈会计四任务对照(c)：财务点审批看到的是"需经理"而非放行。"""
    tid = _ensure_pending_anchor(con, "finance")   # 自愈锚点：一条指派 finance 的待批提案
    resp = client.post(
        "/decisions/ApproveMitigation",
        json={"task_id": tid, "decision": "approved", "comment": "finance 越权批（应 403）"},
        headers={"X-Role": "finance", "X-Actor": "u-fin-us"},
    )
    assert resp.status_code == 403, resp.text
    assert "finance" in (resp.json().get("detail") or ""), "403 白话应点名越权角色"


def test_scope_parity_api_side_cost_oracle(client):
    """API 面 ↔ AI 工具面口径同源（scope parity 的 API 侧断言，对应 app/test_scope_parity.py 的
    Streamlit 侧）：驾驶舱钱区脱敏与 agent.tools._can_see_cost 是**同一把尺子**——凡 _can_see_cost 为真
    的角色 API 就给真金额、为假就 MASK，两个方向都验，证明 UI 聚合与 AI 工具消费同一权限判定。"""
    from agent.tools import _can_see_cost
    # 批D：扩至七角色全量（cs/procurement/compliance/sales 新接入 UI，与 finance/manager/ops 同一循环
    # 同一断言逻辑验证——不是另起一套判断，_can_see_cost 仍是唯一权威源）。
    for role in ("finance", "manager", "ops", "cs", "procurement", "compliance", "sales"):
        headline = _zones(client.get("/cockpit/vitals", headers={"X-Role": role}))["money"]["headline_value"]
        if _can_see_cost(role):
            assert headline != MASK and isinstance(headline, (int, float)), \
                f"{role}: _can_see_cost 为真，API 钱区 headline 应给真金额"
        else:
            assert headline == MASK, f"{role}: _can_see_cost 为假，API 钱区 headline 应掩码"


# ═══════════════════════════════════════════════════════════════════════════
# 批D：cs/procurement/compliance/sales 驾驶舱角色纵向切片（决策日志 V22①"finance 优先——其后
# cs/procurement/compliance/sales"）。同 finance 纪律：一切数据范围由 apps/api 按 X-Role 同源执行
# （前端 roleActors.ts 不复制规则），本组从 API 侧固化四新角色的可见/掩码，依据本体 sensitiveFieldRules
# 声明逐条核对写就（8 条规则 2026-07-19 逐条侦察：procurement/sales 均无 visibleTo 命中，如实记录）。
# ═══════════════════════════════════════════════════════════════════════════
def test_objects_customer_tier_visible_for_cs_money_masked(client, con):
    """Customer.tier（sensitiveFieldRules visibleTo=[cs,manager]）对 cs 可见真值；钱区 headline 对
    cs 掩码（cs 不在 agent.tools._COST_VISIBLE=finance/manager）——客服看得到客户分层但看不到钱。"""
    row = con.execute("SELECT customer_id, tier FROM customers WHERE tier IS NOT NULL LIMIT 1").fetchone()
    assert row, "库内应有带 tier 的客户（datagen 产出）"
    cid, true_tier = row[0], row[1]
    got = client.get(f"/objects/Customer/{cid}", headers={"X-Role": "cs"}).json()["tier"]
    assert got == true_tier, "cs 对 Customer.tier 应见真值（对象读端点，非仅聚合层）"
    money = _zones(client.get("/cockpit/vitals", headers={"X-Role": "cs"}))["money"]
    assert money["headline_value"] == MASK, "cs 不是成本角色，钱区 headline 应掩码"


def test_objects_uflpa_visible_for_compliance_money_masked(client, con):
    """Supplier.uflpa_risk_flag（sensitiveFieldRules visibleTo=[compliance,manager]）对 compliance
    可见真值；钱区 headline 对 compliance 掩码（不在 _COST_VISIBLE）——合规看得到供应商合规旗标
    （UFLPA 强迫劳动风险）但看不到钱，与陈会计财务场景互补、非"合规=看全部"。"""
    row = con.execute("SELECT supplier_id FROM suppliers WHERE uflpa_risk_flag IS NOT NULL "
                      "AND uflpa_risk_flag != '' LIMIT 1").fetchone()
    assert row, "库内应有带 uflpa_risk_flag 的供应商（datagen 产出）"
    sid = row[0]
    flag = client.get(f"/objects/Supplier/{sid}", headers={"X-Role": "compliance"}).json()["uflpa_risk_flag"]
    assert flag != MASK, "compliance 对合规专属字段 uflpa_risk_flag 应见真值"
    money = _zones(client.get("/cockpit/vitals", headers={"X-Role": "compliance"}))["money"]
    assert money["headline_value"] == MASK, "compliance 不是成本角色，钱区 headline 应掩码"


def test_vitals_money_masked_for_sales_no_declared_visible_field(client, con):
    """sales：本体 sensitiveFieldRules 全部 8 条规则逐条核对（Customer.tier/credit_terms/risk_tier、
    Supplier.uflpa_risk_flag、CostScenario 组、Invoice 组、AdmissionCase.conditions、Payment.amount_usd）
    **无一条 visibleTo 含 sales**——如实钉死：钱区 headline 与合规专属字段(uflpa_risk_flag)对 sales
    均掩码，当前无任何专属可见敏感字段（非缺陷，2026-07-19 侦察结论，非猜测）。对照 finance 同请求应
    真值，排除"API 整体故障"误判。"""
    money = _zones(client.get("/cockpit/vitals", headers={"X-Role": "sales"}))["money"]
    assert money["headline_value"] == MASK, "sales 不是成本角色，钱区 headline 应掩码"
    row = con.execute("SELECT supplier_id FROM suppliers WHERE uflpa_risk_flag IS NOT NULL "
                      "AND uflpa_risk_flag != '' LIMIT 1").fetchone()
    assert row, "库内应有带 uflpa_risk_flag 的供应商"
    flag = client.get(f"/objects/Supplier/{row[0]}", headers={"X-Role": "sales"}).json()["uflpa_risk_flag"]
    assert flag == MASK, "sales 不在 uflpa_risk_flag 的 visibleTo（仅 compliance/manager），应掩码"
    fin_headline = _zones(client.get("/cockpit/vitals", headers={"X-Role": "finance"}))["money"]["headline_value"]
    assert fin_headline != MASK, "对照组：finance 应见真值（排除误判 API 整体故障）"


def test_objects_task_amount_own_team_visible_for_procurement(client, con):
    """procurement 自队金额例外（决策日志 V21①、agent.tools.own_team_amount_visible，三面同源单一
    权威源）：两世界现有 tasks.assignee_role 目前只出现 cs/finance/ops（2026-07-19 实测两库 DISTINCT
    assignee_role，procurement 从未被真实指派过待批提案）——本体亦无 procurement 专属可见字段，故用
    既有 _ensure_pending_anchor 自愈锚点模式（同 finance/ops 既有测试手法，写在 module 级临时库副本，
    不碰 data/ 源文件真值）验证代码路径确实认 procurement 为『自队』：procurement 见真值，cs（他队
    且无成本权限）掩码。"""
    tid = _ensure_pending_anchor(con, "procurement", est_cost=555.0, tid="TSK-TEST-V22-PROCUREMENT")

    def obj_cost(role):
        pp = client.get(f"/objects/Task/{tid}", headers={"X-Role": role}).json()["proposal_params"]
        pp = json.loads(pp) if isinstance(pp, str) else pp
        return pp.get("est_cost_usd")

    assert obj_cost("procurement") == 555.0, "procurement 对自队(assignee_role=procurement)提案金额应见真值"
    assert obj_cost("cs") == MASK, "cs 对他队(procurement)提案金额应掩码"


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


def test_ai_flow_mode_badge(tmp_path):
    """AI 工作流条目「执行方式」徽标（L-UX 轮2 诚实跳过项清偿，不变量11）：
    · llm_call 条目天然 mode='llm'；
    · runtime 派生的 ai_action 经 commands 幂等键(run:{run_id}:{step_no})桥到 run 的 think 步 → mode 现查；
    · task_flow / 无 run 桥接的 ai_action → **不带 mode 字段**（判不了不编造）。"""
    import sqlite3 as _sq
    from agent.runtime import ensure_runtime_tables
    from app.command_bus import ensure_commands_table
    dest = tmp_path / "mode_flow.sqlite"
    shutil.copy(REPO_DB, dest)
    con = _sq.connect(dest)
    ensure_runtime_tables(con)
    ensure_commands_table(con)
    # 两个 run：一个 think 步 mode=deterministic（剧本），一个 mode=llm（真模型）
    for rid, mode in (("RUN-DET", "deterministic"), ("RUN-LLM", "llm")):
        con.execute("INSERT INTO agent_runs (run_id, goal, status, agent_role, budget_json, "
                    "created_at, updated_at, killed, summary) VALUES (?,?,?,?,?,?,?,0,NULL)",
                    (rid, "处置 RSK-X", "running", "ops", "{}", "2026-07-10T00:00:00Z",
                     "2026-07-10T00:00:00Z"))
        con.execute("INSERT INTO agent_run_steps (run_id, step_no, kind, payload_json, "
                    "result_json, created_at) VALUES (?,?,?,?,?,?)",
                    (rid, 1, "think", "{}", json.dumps({"mode": mode}), "2026-07-10T00:00:01Z"))
    # 两条 run-keyed 命令（object_id 即 task_id，与 action_log.target_object_id 同值 → 桥接键）
    con.execute("INSERT INTO commands (command_id, idempotency_key, action, actor, role, "
                "params_fingerprint, result_status, object_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("c1", "run:RUN-DET:3", "AssignTask", "ai-agent", "ops", "fp", "ok",
                 "TSK-MODE-DET", "2026-07-10T00:00:02Z"))
    con.execute("INSERT INTO commands (command_id, idempotency_key, action, actor, role, "
                "params_fingerprint, result_status, object_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                ("c2", "run:RUN-LLM:4", "ProposeMitigation", "ai-agent", "ops", "fp", "ok",
                 "TSK-MODE-LLM", "2026-07-10T00:00:03Z"))
    # 三条 ai-agent 审计行：两条有 run 桥接（应得 mode），一条无桥接（不应带 mode）
    for action, tid, ts in (("AssignTask", "TSK-MODE-DET", "2026-07-10T09:00:00Z"),
                            ("ProposeMitigation", "TSK-MODE-LLM", "2026-07-10T09:01:00Z"),
                            ("ProposeMitigation", "TSK-NO-BRIDGE", "2026-07-10T09:02:00Z")):
        con.execute("INSERT INTO action_log (actor, role, action, target_object_id, params_json, "
                    "as_of_date, timestamp, result, trace_id) VALUES (?,?,?,?,?,?,?,?,NULL)",
                    ("ai-agent", "ops", action, tid, "{}", "2026-07-10", ts, "ok"))
    con.commit()
    con.close()

    saved = app.dependency_overrides.get(get_db_path)
    app.dependency_overrides[get_db_path] = lambda: str(dest)
    try:
        with TestClient(app) as c:
            d = c.get("/cockpit/ai-flow", params={"limit": 500},
                      headers={"X-Role": "manager"}).json()
        by_ref = {i["ref_object"]: i for i in d["items"]}
        # ai_action：run 桥接 → mode 现查（剧本 / 真模型）
        assert by_ref["TSK-MODE-DET"]["kind"] == "ai_action"
        assert by_ref["TSK-MODE-DET"]["mode"] == "deterministic"
        assert by_ref["TSK-MODE-LLM"]["mode"] == "llm"
        # 无 run 桥接的 ai_action → 判不了 → 不带 mode 字段（不编造）
        assert "mode" not in by_ref["TSK-NO-BRIDGE"], by_ref["TSK-NO-BRIDGE"]
        # llm_call 天然 'llm'；task_flow 判不了不带 mode
        for i in d["items"]:
            if i["kind"] == "llm_call":
                assert i["mode"] == "llm"
            if i["kind"] == "task_flow":
                assert "mode" not in i, i
    finally:
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        else:
            app.dependency_overrides.pop(get_db_path, None)


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
            assert i["kind"] in ("ai_action", "task_flow", "llm_call"), f"真实运营条目 kind 异常：{i['kind']}"
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


# ═══════════════════════════════════════════════════════════════════════════
# 轮3-A（P1）：ai-flow 自由文本金额脱敏——_can_see_cost 声明的执行缺口收紧。
# 泄漏面是 sim_ai_activity.detail 灌出的 summary/note 里的 "$7157.16"（_mask_money 只掩
# 结构化 _usd 键管不到文本）。非成本角色文本 "$金额"→"$•••"；finance/manager 不受影响。
# ═══════════════════════════════════════════════════════════════════════════
_RAW_DOLLAR = re.compile(r"\$\s*\d")     # 裸美元金额（$ 后跟数字；$••• 不命中）


def test_ai_flow_text_amounts_masked_for_non_cost_roles(sim_world):
    """合规/运营（非成本角色）：ai-flow 全载荷无裸美元金额，掩码 $••• 保留金额位语义信号。"""
    c, scon = sim_world
    n = scon.execute("SELECT count(*) FROM sim_ai_activity WHERE detail LIKE '%$%'").fetchone()[0]
    assert n > 0, "前置：sim 库应有带 $ 金额的 AI 留痕（否则断言空转）"
    for role in ("compliance", "ops", "cs", "procurement", "sales"):
        txt = json.dumps(c.get("/cockpit/ai-flow", params={"limit": 500},
                               headers={"X-Role": role}).json(), ensure_ascii=False)
        assert not _RAW_DOLLAR.search(txt), f"{role}: ai-flow 载荷仍有裸美元金额（执行缺口未堵）"
        assert "$•••" in txt, f"{role}: 应保留 $••• 掩码信号（不是把金额整句删掉）"


def test_ai_flow_text_amounts_kept_for_cost_roles(sim_world):
    """finance/manager（_can_see_cost）：ai-flow 文本金额保留真值，不被误伤。"""
    c, _ = sim_world
    for role in ("finance", "manager"):
        txt = json.dumps(c.get("/cockpit/ai-flow", params={"limit": 500},
                               headers={"X-Role": role}).json(), ensure_ascii=False)
        assert _RAW_DOLLAR.search(txt), f"{role}: 成本角色应见文本真值金额"
        assert "$•••" not in txt, f"{role}: 成本角色不应出现文本掩码"


def test_ai_flow_default_role_text_masked(sim_world):
    """X-Role 缺省（=ops）行为对齐既有掩码语义：同样无裸美元金额。"""
    c, _ = sim_world
    txt = json.dumps(c.get("/cockpit/ai-flow", params={"limit": 500}).json(),
                     ensure_ascii=False)
    assert not _RAW_DOLLAR.search(txt)


# ═══════════════════════════════════════════════════════════════════════════
# 轮3-D（P1）：付款锚风险（R19/R21）影响归并 /cockpit/risk-impact/{id}。
# 归并链全走 schema（payment→单据→对手方，零文本解析）；断言值对临时库跑独立 SQL 现查，
# 并与风险摘要文本中的客户/订单号交叉一致（苏苏实证的"摘要有、结构化区 0 条"缺口）。
# ═══════════════════════════════════════════════════════════════════════════
def test_risk_impact_r19_sim_matches_summary_text(sim_world):
    """sim R19：归并行的客户号/订单号与 root_cause 摘要文本一致；金额=风险行独立现查。"""
    c, scon = sim_world
    row = scon.execute("SELECT risk_event_id, root_cause, affected_value_usd FROM risk_events "
                       "WHERE rule_id='R19' ORDER BY risk_event_id LIMIT 1").fetchone()
    assert row, "前置：sim 库应有 R19 风险"
    m = re.search(r"客户 (CUS-\d+) 订单 (SO-SIM-\d+)", row["root_cause"])
    assert m, f"前置：R19 摘要应含客户/订单号（{row['root_cause']}）"
    d = c.get(f"/cockpit/risk-impact/{row['risk_event_id']}",
              headers={"X-Role": "manager"}).json()
    assert d["anchor"] == "payment" and d["rows"], d
    r0 = d["rows"][0]
    assert r0["counterparty_type"] == "customer"
    assert r0["counterparty_id"] == m.group(1), "归并客户号应与摘要文本一致"
    assert r0["ref_type"] == "sales_order" and r0["ref_id"] == m.group(2), "归并订单号应与摘要文本一致"
    assert abs(r0["amount_usd"] - row["affected_value_usd"]) < 0.01
    assert r0["overdue_days"] is not None and r0["overdue_days"] > 0, "未回款应收应有逾期天数"


def test_risk_impact_r21_sim_anchor_and_duplicate_sibling(sim_world):
    """sim R21（重复付款）：任务 proposal_params.payment_id 锚 + 同据同额姊妹笔一并列出。"""
    c, scon = sim_world
    t = scon.execute("SELECT t.risk_event_id, t.proposal_params FROM tasks t "
                     "JOIN risk_events r ON r.risk_event_id=t.risk_event_id "
                     "WHERE r.rule_id='R21' ORDER BY t.task_id LIMIT 1").fetchone()
    assert t, "前置：sim 库应有 R21 任务"
    anchor_pid = json.loads(t["proposal_params"])["payment_id"]
    d = c.get(f"/cockpit/risk-impact/{t['risk_event_id']}",
              headers={"X-Role": "manager"}).json()
    assert d["anchor"] == "payment"
    by_id = {r["payment_id"]: r for r in d["rows"]}
    assert anchor_pid in by_id and by_id[anchor_pid]["is_anchor"] is True
    # 姊妹重复笔：同 ref 同额的另一笔（独立 SQL 现查），应在 rows 里且 is_anchor=False
    src = scon.execute("SELECT ref_type, ref_id, amount_usd FROM payments WHERE payment_id=?",
                       (anchor_pid,)).fetchone()
    sibs = [r[0] for r in scon.execute(
        "SELECT payment_id FROM payments WHERE ref_type=? AND ref_id=? "
        "AND round(amount_usd,2)=round(?,2) AND payment_id != ?",
        (src["ref_type"], src["ref_id"], src["amount_usd"], anchor_pid))]
    assert sibs, "前置：R21 应存在同据同额的姊妹笔（重复付款异常本体）"
    for s in sibs:
        assert s in by_id and by_id[s]["is_anchor"] is False


def test_risk_impact_verify_world_r19_and_masking(client, con):
    """验证世界 R19（affected 里直接是 PAY id）：payment→订单/客户归并 = 独立 SQL 现查；
    金额掩码跟随现行角色规则（ops 掩、finance 真值——_can_see_cost 同一把尺）。"""
    row = con.execute("SELECT risk_event_id, affected_so_line_ids FROM risk_events "
                      "WHERE rule_id='R19' ORDER BY risk_event_id LIMIT 1").fetchone()
    assert row, "前置：验证世界应有 R19 风险"
    pay_id = json.loads(row["affected_so_line_ids"])[0]
    pay = con.execute("SELECT counterparty_id, ref_id, amount_usd FROM payments "
                      "WHERE payment_id=?", (pay_id,)).fetchone()
    d = client.get(f"/cockpit/risk-impact/{row['risk_event_id']}",
                   headers={"X-Role": "finance"}).json()
    assert d["anchor"] == "payment" and d["rows"]
    r0 = d["rows"][0]
    assert (r0["payment_id"], r0["counterparty_id"], r0["ref_id"]) == \
        (pay_id, pay["counterparty_id"], pay["ref_id"])
    assert abs(r0["amount_usd"] - pay["amount_usd"]) < 0.01, "finance 应见真值"
    masked = client.get(f"/cockpit/risk-impact/{row['risk_event_id']}",
                        headers={"X-Role": "ops"}).json()
    assert masked["rows"][0]["amount_usd"] == MASK, "ops 金额应掩码（现行角色规则）"


def test_risk_impact_non_payment_rule_honest_empty(client, con):
    """非付款锚风险（如 R1）：anchor='so_line' + rows=[]（订单行归并走既有链路，不假装归并）。"""
    row = con.execute("SELECT risk_event_id FROM risk_events WHERE rule_id='R1' "
                      "ORDER BY risk_event_id LIMIT 1").fetchone()
    assert row, "前置：验证世界应有 R1 风险"
    d = client.get(f"/cockpit/risk-impact/{row['risk_event_id']}",
                   headers={"X-Role": "manager"}).json()
    assert d["anchor"] == "so_line" and d["rows"] == [] and d["note"]


def test_risk_impact_unknown_risk_404(client):
    resp = client.get("/cockpit/risk-impact/RSK-NO-SUCH", headers={"X-Role": "manager"})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# 轮3-G（P0）：清关卡点逐票队列 /cockpit/customs-queue——与七区卡 customs_blocked 同一
# WHERE 口径（not_filed 且在途），total/排序/po_count 对临时库独立 SQL 现查，不誊抄数字。
# ═══════════════════════════════════════════════════════════════════════════
def _assert_customs_queue_consistent(payload: dict, dbcon) -> None:
    expected = dbcon.execute("SELECT count(*) FROM shipments "
                             "WHERE customs_status='not_filed' AND status='in_transit'"
                             ).fetchone()[0]
    assert payload["total"] == expected, "total 应=七区卡同口径现查"
    assert payload["count"] == min(50, expected) == len(payload["items"])
    days = [i["stuck_days"] for i in payload["items"] if i["stuck_days"] is not None]
    assert days == sorted(days, reverse=True), "应按卡点天数降序"
    if payload["items"]:
        it = payload["items"][0]
        po_raw = dbcon.execute("SELECT po_ids FROM shipments WHERE shipment_id=?",
                               (it["shipment_id"],)).fetchone()[0]
        assert it["po_count"] == len([p for p in str(po_raw or "").split("|") if p]), \
            "po_count 应=管道分隔现查（同 panorama 解析口径）"
        assert "卡点天数" in payload["basis"], "口径白话应入 basis"


def test_customs_queue_verify_world(client, con):
    d = client.get("/cockpit/customs-queue", headers={"X-Role": "manager"}).json()
    assert d["world"] == "verification"
    _assert_customs_queue_consistent(d, con)
    assert d["total"] > 0, "前置：验证世界应有清关卡点票（当前库 40）"


def test_customs_queue_sim_world(sim_world):
    """sim 世界（林律实证卡片 21 票只有数字）：逐票队列条数=独立现查，不硬编码 21。"""
    c, scon = sim_world
    d = c.get("/cockpit/customs-queue", headers={"X-Role": "compliance"}).json()
    assert d["world"] == "simulation"
    _assert_customs_queue_consistent(d, scon)
    assert d["total"] > 0, "前置：sim 库应有清关卡点票"


# ═══════════════════════════════════════════════════════════════════════════
# K·P1 供应商队列合规维度（轮3 林律"让我一家家点开核对 UFLPA 是体力活"）
# 可见性红线：本体 Supplier.uflpa_risk_flag visibleTo=[compliance,manager]——其他角色载荷
# **根本不带**合规键（非掩码，不开新洞）；缺省排序钉死；sort=compliance 角色门 422 白话。
# ═══════════════════════════════════════════════════════════════════════════
_COMP_KEYS = ("uflpa_risk_flag", "factory_audit_status", "compliance_docs_status", "qual_abnormal")


def _sup_zone(c, role, sort=None):
    url = "/cockpit/vitals" + (f"?sort={sort}" if sort else "")
    r = c.get(url, headers={"X-Role": role})
    assert r.status_code == 200, r.text
    return next(z for z in r.json()["zones"] if z["zone"] == "suppliers")


def test_suppliers_compliance_fields_for_compliance_role(client, con):
    """compliance 角色：worst_suppliers 行附合规四键（值对库现查，独立 SQL 真值判定路径）+
    detail.compliance_dimension 全库计数与独立 SQL 一致（含无收货、不在队列里的供应商）。"""
    z = _sup_zone(client, "compliance")
    rows = z["detail"]["delivery_hit_rate"]["worst_suppliers"]
    assert rows, "前置：验证世界应有收货域供应商队列"
    for r in rows:
        assert set(_COMP_KEYS) <= set(r), f"行缺合规键：{sorted(r)}"
        db = con.execute("SELECT uflpa_risk_flag u, factory_audit_status a, "
                         "compliance_docs_status d FROM suppliers WHERE supplier_id=?",
                         (r["supplier_id"],)).fetchone()
        # 独立真值判定路径（SQL 端 lower/trim 文本枚举，与实现的 python _flag_true 是两条路）
        exp_uflpa = (str(db["u"]).strip().lower() in ("1", "true", "t", "yes", "y")
                     if db["u"] is not None else False)
        assert r["uflpa_risk_flag"] == exp_uflpa
        assert r["factory_audit_status"] == db["a"]
        assert r["compliance_docs_status"] == db["d"]
        exp_abn = (str(db["a"] or "").strip().lower() in ("not_started", "pending", "failed")
                   or str(db["d"] or "").strip().lower() in ("missing", "partial", "rejected"))
        assert r["qual_abnormal"] == exp_abn
    comp = z["detail"]["compliance_dimension"]
    exp_total = con.execute(
        "SELECT count(*) FROM suppliers "
        "WHERE lower(trim(cast(uflpa_risk_flag AS TEXT))) IN ('1','true','t','yes','y')"
    ).fetchone()[0]
    assert comp["available"] is True and comp["sort"] == "default"
    assert comp["uflpa_flagged_total"] == exp_total
    assert comp["suppliers_total"] == con.execute("SELECT count(*) FROM suppliers").fetchone()[0]
    # 零阳性世界必须带诚实 note（sim 全 0 场景），有阳性则不带——两态都钉死
    assert ("note" in comp) == (exp_total == 0)


def test_suppliers_no_compliance_fields_for_other_roles(client):
    """可见性红线：非 compliance/manager 角色载荷**不含**任何合规键（不是掩码是不带）——
    行级四键 + 区级 compliance_dimension 都不得出现；ops/finance/procurement 三角色扫全。"""
    for role in ("ops", "finance", "procurement"):
        z = _sup_zone(client, role)
        assert "compliance_dimension" not in z["detail"], f"{role} 载荷泄漏 compliance_dimension"
        for r in z["detail"]["delivery_hit_rate"]["worst_suppliers"]:
            leaked = set(_COMP_KEYS) & set(r)
            assert not leaked, f"{role} 行载荷泄漏合规键 {leaked}"


def test_suppliers_default_order_pinned_with_fields(client, con):
    """缺省排序钉死：compliance（带字段）与 ops（不带）的行序完全一致；且逐行达成率单调不降
    （升序=最差在前，附字段不动序）；manager 同 compliance 见字段。"""
    comp_rows = _sup_zone(client, "compliance")["detail"]["delivery_hit_rate"]["worst_suppliers"]
    mgr_rows = _sup_zone(client, "manager")["detail"]["delivery_hit_rate"]["worst_suppliers"]
    ops_rows = _sup_zone(client, "ops")["detail"]["delivery_hit_rate"]["worst_suppliers"]
    assert [r["supplier_id"] for r in comp_rows] == [r["supplier_id"] for r in ops_rows]
    assert [r["supplier_id"] for r in mgr_rows] == [r["supplier_id"] for r in ops_rows]
    assert all(set(_COMP_KEYS) <= set(r) for r in mgr_rows), "manager 也应见合规字段"
    rates = [r["rate"] for r in ops_rows if r["rate"] is not None]
    assert rates == sorted(rates), "缺省序=达成率升序（最差在前）不得被改动"


def test_suppliers_sort_compliance_role_gate_422(client):
    """sort=compliance 对无权角色 → 422 白话（合规维度需合规或经理角色）；未知 sort 值 → 422。"""
    r = client.get("/cockpit/vitals?sort=compliance", headers={"X-Role": "ops"})
    assert r.status_code == 422
    msg = r.json()["error"]["message"]
    assert "合规" in msg and ("manager" in msg or "经理" in msg)
    r2 = client.get("/cockpit/vitals?sort=delivery", headers={"X-Role": "compliance"})
    assert r2.status_code == 422 and "sort" in r2.json()["error"]["message"]


def test_suppliers_sort_compliance_orders_flagged_first(client):
    """sort=compliance（compliance 角色）：UFLPA 命中行全部排最前、资质异常次之、组内按交期升序；
    与库内独立现查的分组期望逐位对照（验证世界有 3 家 UFLPA=True 真阳性，非合成）。"""
    z = _sup_zone(client, "compliance", sort="compliance")
    comp = z["detail"]["compliance_dimension"]
    assert comp["sort"] == "compliance"
    rows = z["detail"]["delivery_hit_rate"]["worst_suppliers"]
    # 字典序钉死（与声明口径逐字对应）：UFLPA 命中优先 → 次按资质异常 → 再按交期升序。
    # 即 (¬uflpa, ¬qual_abnormal, rate is None, rate) 元组序列必须已然有序——UFLPA 组内
    # "又异常又命中"排在"仅命中"之前（合规风险更重者更靠前），组内再按交期最差在前。
    keys = [(not r["uflpa_risk_flag"], not r["qual_abnormal"], r["rate"] is None, r["rate"])
            for r in rows]
    assert keys == sorted(keys), f"合规字典序被打破：{keys}"
    # 阳性数前置校验：本用例的看守力依赖验证世界真有阳性（若重灌后归零，此断言诚实报前置失效）
    assert comp["uflpa_flagged_total"] > 0, "前置：验证世界应有 UFLPA 阳性样本（当前库 3 家）"
    assert any(r["uflpa_risk_flag"] for r in rows), "阳性应浮上 Top10 首屏"


def test_suppliers_sort_compliance_zero_positive_world(sim_world):
    """sim 世界全库 UFLPA=0（轮3 §三备忘）：排序功能仍正确工作 + compliance_dimension 带
    诚实空态 note（"当前无 UFLPA 标记供应商"），不编造阳性。"""
    c, scon = sim_world
    z = _sup_zone(c, "compliance", sort="compliance")
    comp = z["detail"]["compliance_dimension"]
    exp = scon.execute(
        "SELECT count(*) FROM suppliers "
        "WHERE lower(trim(cast(uflpa_risk_flag AS TEXT))) IN ('1','true','t','yes','y')"
    ).fetchone()[0]
    assert comp["uflpa_flagged_total"] == exp
    if exp == 0:
        assert "当前无 UFLPA 标记供应商" in comp["note"]
        assert not any(r["uflpa_risk_flag"] for r in z["detail"]["delivery_hit_rate"]["worst_suppliers"])


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
