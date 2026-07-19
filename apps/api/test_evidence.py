#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_evidence.py —— 波E 证据包端点验收用例（spec 2026-07-17-waveE-evidence-chain §①/②）。

隔离纪律（同 apps/api/test_waveU.py）：两库各一 **模块级临时副本**（保留原名 → _infer_world 推断
verification/simulation），monkeypatch main.DEFAULT_DB_PATH/SIMWORLD_DB_PATH 指向副本，走 X-World 真解析
链路——**绝不写真库**（业务双库 md5/digest 不因测试改变；GET /proposals/*/evidence 本就纯读）。
trust 块 monkeypatch apps.api.governance.GATING_REPORT_PATH（无库依赖，同 U3 治理面测法）。

覆盖（任务书 §① 四块 × 真数据/空态/脱敏 + §② 摘要纯函数）：
  · impact      真数据（订单行数/金额/客户数对副本现查对照）+ 空态（无 SO 行的费用类风险=0+note）+ 脱敏（ops 金额掩码）。
  · precedents  真数据（例数分布+有效率+近例 3 条）+ 放宽（rule+lane<5→rule_only）+ 空态（首例）+ 样本不足标注（合成库）。
  · trust       真数据（gating 该 rule 域 tier/rate/CI/n+display_only）+ 缺文件（available:false）+ 域缺失（available:false）。
  · alternatives 真数据（expedite vs accept_delay 历史有效率对照）+ null+reason（无货件→延误天数 null）。
  · 脱敏总校验：ops 看全部 _usd 键掩码、manager 看真值；计数/比率/天数/档位不掩。
  · summary     precedent_summary_line 纯函数（真数据一句摘要 / 首例）——runtime propose 步引用的同一逻辑。
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import apps.api.governance as gov
import apps.api.main as apimain
from agent.tools import MASK
from apps.api.evidence import precedents_block, precedent_summary_line
from apps.api.main import REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"
SIM = {"X-World": "sim"}
VERIFY = {"X-World": "verify"}
MANAGER = "manager"
OPS = "ops"


# ═══════════════════════════════════════════════════════════════════════════
# fixture：双世界临时副本 + monkeypatch 两库常量（走 X-World 真解析链路，绝不写真库）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    assert REPO_DB.exists() and SIM_DB.exists(), "需先跑 datagen/build_ontology/seed + sim.backfill"
    d = tmp_path_factory.mktemp("evidence")
    vpath, spath = d / "ontology.sqlite", d / "simworld.sqlite"
    shutil.copy(REPO_DB, vpath)
    shutil.copy(SIM_DB, spath)
    mp = pytest.MonkeyPatch()
    mp.setattr(apimain, "DEFAULT_DB_PATH", vpath)
    mp.setattr(apimain, "SIMWORLD_DB_PATH", spath)
    mp.delenv("ONTOLOGY_DB", raising=False)
    mp.delenv("ONTOLOGY_DB_PATH", raising=False)
    saved = app.dependency_overrides.pop(get_db_path, None)
    vcon = sqlite3.connect(vpath); vcon.row_factory = sqlite3.Row
    scon = sqlite3.connect(spath); scon.row_factory = sqlite3.Row
    try:
        with TestClient(app) as c:
            yield c, vcon, scon
    finally:
        vcon.close(); scon.close()
        if saved is not None:
            app.dependency_overrides[get_db_path] = saved
        mp.undo()


# ─── 动态发现 task_id（对 sim 重生成有韧性：查条件而非硬编码脆 id） ───
def _pick(con: sqlite3.Connection, where: str) -> str | None:
    row = con.execute(
        f"SELECT t.task_id FROM tasks t JOIN risk_events re ON re.risk_event_id=t.risk_event_id "
        f"WHERE {where} ORDER BY t.task_id LIMIT 1").fetchone()
    return row["task_id"] if row else None


def _ev(client, task_id, role=MANAGER, world=SIM):
    return client.get(f"/proposals/{task_id}/evidence",
                      headers={"X-Role": role, **world})


# ═══════════════════════════════════════════════════════════════════════════
# ① impact —— 真数据 / 空态 / 脱敏
# ═══════════════════════════════════════════════════════════════════════════
def test_impact_real_data_matches_manual_sql(api):
    """R1 提案（有 SO 行）：impact 三数与副本现查对照（复用 cockpit 影响口径：去重行 Σqty×单价、去重客户）。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.affected_so_line_ids NOT IN ('','[]')")
    assert tid, "sim 应有带 SO 行的 R1 提案"
    imp = _ev(client, tid).json()["impact"]
    # 手工现查对照（不誊抄数字）
    rid = scon.execute("SELECT risk_event_id FROM tasks WHERE task_id=?", (tid,)).fetchone()[0]
    import json as _json
    line_ids = _json.loads(scon.execute(
        "SELECT affected_so_line_ids FROM risk_events WHERE risk_event_id=?", (rid,)).fetchone()[0])
    ph = ",".join("?" * len(line_ids))
    rows = scon.execute(
        f"SELECT sol.qty, sol.unit_price_usd, so.customer_id FROM sales_order_lines sol "
        f"JOIN sales_orders so ON so.so_id=sol.so_id WHERE sol.so_line_id IN ({ph})", line_ids).fetchall()
    exp_amount = round(sum((r["qty"] or 0) * (r["unit_price_usd"] or 0.0) for r in rows), 2)
    exp_cust = len({r["customer_id"] for r in rows if r["customer_id"]})
    assert imp["affected_order_lines"] == len(rows)
    assert imp["amount_usd"] == exp_amount
    assert imp["affected_customers"] == exp_cust


def test_impact_empty_for_fee_risk_no_so_lines(api):
    """费用/断供类风险（affected_so_line_ids='[]'）：impact 三数=0 + 白话 note（诚实非缺数，不硬算）。"""
    client, _v, scon = api
    tid = _pick(scon, "re.affected_so_line_ids IN ('','[]')")
    assert tid, "sim 应有无 SO 行的风险提案（费用/断供类）"
    imp = _ev(client, tid).json()["impact"]
    assert imp["affected_order_lines"] == 0 and imp["amount_usd"] == 0.0
    assert imp["affected_customers"] == 0 and "note" in imp


def test_impact_amount_masked_for_ops(api):
    """脱敏：ops（无成本可见）看 impact.amount_usd 掩码；manager 看真值。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.affected_so_line_ids NOT IN ('','[]')")
    assert _ev(client, tid, role=OPS).json()["impact"]["amount_usd"] == MASK
    assert isinstance(_ev(client, tid, role=MANAGER).json()["impact"]["amount_usd"], (int, float))


# ═══════════════════════════════════════════════════════════════════════════
# ② precedents —— 真数据 / 放宽 / 空态（首例） / 样本不足（合成库）
# ═══════════════════════════════════════════════════════════════════════════
def test_precedents_real_distribution_and_effectiveness(api):
    """R1 提案：例数分布 by_decision + 事后有效率（quality_label='effective'）+ 近例≤3 条带白话结局；
    数字对副本现查对照（先例统计绝不编数）。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.affected_so_line_ids NOT IN ('','[]')")
    prec = _ev(client, tid).json()["precedents"]
    assert prec["available"] and prec["n"] >= 1
    assert sum(prec["by_decision"].values()) == prec["n"], "各决定例数之和=总例数"
    eff = prec["effectiveness"]
    assert eff["labeled"] == eff["effective"] + eff["partial"] + eff["ineffective"]
    if eff["labeled"]:
        assert eff["effective_rate"] == round(eff["effective"] / eff["labeled"], 4)
    assert len(prec["recent_examples"]) <= 3
    assert all("plain" in e for e in prec["recent_examples"])


def test_precedents_widen_when_lane_thin(api):
    """R2 提案（同规则+同航线常 <5）：放宽到同规则（任意航线），match_scope=rule_only + widened + widen_reason。"""
    client, _v, scon = api
    # 挑一个 rule+lane 确实 <5、rule_only 更多的 R2 提案（动态判定，避免脆 id）
    from apps.api.evidence import _retrieve_precedents
    from engine.resolution_memory import lane_for_shipment
    picked = None
    for row in scon.execute(
            "SELECT t.task_id, re.risk_event_id, re.rule_id, re.shipment_id FROM tasks t "
            "JOIN risk_events re ON re.risk_event_id=t.risk_event_id WHERE re.rule_id='R2' "
            "ORDER BY t.task_id"):
        lane = lane_for_shipment(scon, row["shipment_id"])
        tight = _retrieve_precedents(scon, "R2", lane, True, exclude_risk_id=row["risk_event_id"])
        broad = _retrieve_precedents(scon, "R2", lane, False, exclude_risk_id=row["risk_event_id"])
        if len(tight) < 5 and len(broad) > len(tight):
            picked = row["task_id"]; break
    assert picked, "sim 应有航线样本不足需放宽的 R2 提案"
    prec = _ev(client, picked).json()["precedents"]
    assert prec["widened"] is True and prec["match_scope"] == "rule_only"
    assert "widen_reason" in prec


def test_precedents_verification_world_state_relative(api):
    """验证世界先例=对库现查（世界观更新 2026-07-18：A-1 起人批决策会归档进 resolution_memory，
    验证世界不再恒 0 先例——测试不得假设不可变演示态，同 c14560a 教训）。n 与库内同 rule 计数
    一致；0 例时守诚实空态（empty:true+'首例'），有例时分布求和=n。"""
    client, vcon, _s = api
    row = vcon.execute(
        "SELECT t.task_id, r.rule_id FROM tasks t JOIN risk_events r ON r.risk_event_id=t.risk_event_id "
        "WHERE t.approval_status IS NOT NULL ORDER BY t.task_id LIMIT 1").fetchone()
    tid, rule = row[0], row[1]
    expected_n = vcon.execute(
        "SELECT count(*) FROM resolution_memory WHERE rule_id=? AND status='active'", (rule,)).fetchone()[0]
    prec = _ev(client, tid, world=VERIFY).json()["precedents"]
    assert prec["available"]
    if expected_n == 0:
        assert prec.get("empty") is True and prec["n"] == 0 and "首例" in prec["note"]
    else:
        assert prec["n"] >= 1 and sum(prec["by_decision"].values()) == prec["n"]


def test_precedents_pure_synthetic_edges():
    """纯函数边界（合成内存库，确定性）：0 例=首例空态；<5 例=样本不足标注；均未回填=有效率 None（不 0/0 冒充）。"""
    con = sqlite3.connect(":memory:"); con.row_factory = sqlite3.Row
    from engine.resolution_memory import RESOLUTION_MEMORY_DDL
    con.execute(RESOLUTION_MEMORY_DDL)
    # 0 例
    p0 = precedents_block(con, "R1", "CN→US")
    assert p0.get("empty") is True and p0["n"] == 0

    def _ins(mid, decision, quality, closed):
        con.execute(
            "INSERT INTO resolution_memory (memory_id,risk_event_id,rule_id,lane,as_of,"
            "proposal_summary,proposal_version,cited_precedent_ids,decision,decided_by,decided_at,"
            "quality_label,closed_at,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'active')",
            (mid, "RSK-x" + mid, "R1", "CN→US", "2026-01-01",
             '{"action":"accept_delay"}', "v1", "[]", decision, "u1", "2026-01-01",
             quality, closed))
    # 3 例（<5）、均未回填质量标签 → sample_note + effective_rate None
    for i in range(3):
        _ins(f"M{i}", "adopted", None, None)
    p3 = precedents_block(con, "R1", "CN→US")
    assert p3["n"] == 3 and "sample_note" in p3
    assert p3["effectiveness"]["effective_rate"] is None
    assert p3["effectiveness"]["labeled"] == 0
    con.close()


# ═══════════════════════════════════════════════════════════════════════════
# ③ trust —— 真数据 / 缺文件 / 域缺失（只读 JSON，不 import gating）
# ═══════════════════════════════════════════════════════════════════════════
def test_trust_real_gating_domain(api):
    """R1 提案：trust 命中 gating resolution 域 R1，带 tier/rate/CI/n + display_only 原样透传。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1'")
    t = _ev(client, tid).json()["trust"]
    assert t["available"] and t["rule_id"] == "R1" and t["domain"] == "R1"
    assert t["tier"] and t["rate"] is not None and t["n"] is not None and t["ci"] is not None
    assert t["display_only"] is True, "display_only 必须原样透传（档位≠已授权）"


def test_trust_missing_file_honest_empty(api, monkeypatch, tmp_path):
    """治理报告文件缺失 → trust.available=false + 白话 reason（诚实空态，绝不 500/用旧值冒充）。"""
    client, _v, scon = api
    monkeypatch.setattr(gov, "GATING_REPORT_PATH", tmp_path / "nope.json")
    tid = _pick(scon, "re.rule_id='R1'")
    r = _ev(client, tid)
    assert r.status_code == 200
    t = r.json()["trust"]
    assert t["available"] is False and "尚未生成" in t["reason"]


def test_trust_domain_not_in_report(api):
    """规则不在 gating 放权域（如 R19，报告只含 R1/R2/R4/R5/R6/R16）→ trust.available=false + reason。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R19'")
    assert tid, "sim 应有 R19（不在 gating 域）的提案"
    t = _ev(client, tid).json()["trust"]
    assert t["available"] is False and "R19" in t["reason"]


# ═══════════════════════════════════════════════════════════════════════════
# ④ alternatives —— 真数据 / null+reason
# ═══════════════════════════════════════════════════════════════════════════
def test_alternatives_real_two_options(api):
    """R1 提案：expedite vs accept_delay 两选项各给历史有效率（同类先例现算）+ 延误天数（有货件）+ 受影响货值。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.shipment_id IS NOT NULL")
    alt = _ev(client, tid).json()["alternatives"]
    assert alt["available"] and set(alt["options"]) == {"expedite", "accept_delay"}
    assert isinstance(alt["delay_days"], int)
    for act in ("expedite", "accept_delay"):
        h = alt["options"][act]["historical"]
        # 能算则给 effective_rate（float 或 None），算不出则 reason——绝不缺字段
        assert "effective_rate" in h or "reason" in h


def test_alternatives_no_shipment_delay_null_reason(api):
    """无货件锚定的风险（如 R19/R16）：延误天数 null + delay_reason（算不出如实标，绝不编）。"""
    client, _v, scon = api
    tid = _pick(scon, "(re.shipment_id IS NULL OR re.shipment_id='')")
    assert tid, "sim 应有无货件锚定的风险提案"
    alt = _ev(client, tid).json()["alternatives"]
    assert alt["delay_days"] is None and "delay_reason" in alt


def test_alternatives_historical_state_relative(api):
    """选项历史有效率=对库现查（世界观更新同上）：某选项在同类先例中 0 案例 → null+reason
    诚实空态；有案例 → n>=1 且 effective_rate 为 None（未回填结局）或 [0,1] 实数——绝不 0/0 冒充。"""
    client, vcon, _s = api
    tid = vcon.execute("SELECT task_id FROM tasks WHERE risk_event_id IS NOT NULL "
                       "ORDER BY task_id LIMIT 1").fetchone()[0]
    alt = _ev(client, tid, world=VERIFY).json()["alternatives"]
    for act in ("expedite", "accept_delay"):
        h = alt["options"][act]["historical"]
        if h["n"] == 0:
            assert h["effective_rate"] is None and "reason" in h
        else:
            assert h["effective_rate"] is None or 0.0 <= h["effective_rate"] <= 1.0


# ═══════════════════════════════════════════════════════════════════════════
# 脱敏总校验 + 404 + X-World 双世界
# ═══════════════════════════════════════════════════════════════════════════
def test_all_usd_keys_masked_for_ops_not_manager(api):
    """脱敏总校验（spec §① X-Role 脱敏同 objects）：ops 看载荷内**全部** _usd 键掩码；manager 看真值；
    计数/比率/天数/档位两角色都不掩（非金额不误掩）。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.affected_so_line_ids NOT IN ('','[]')")

    def _usd_values(node, out):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.endswith("_usd") and v is not None:
                    out.append(v)
                else:
                    _usd_values(v, out)
        elif isinstance(node, list):
            for x in node:
                _usd_values(x, out)

    ops_usd, mgr_usd = [], []
    _usd_values(_ev(client, tid, role=OPS).json(), ops_usd)
    _usd_values(_ev(client, tid, role=MANAGER).json(), mgr_usd)
    assert ops_usd and all(v == MASK for v in ops_usd), "ops 下每个 _usd 键都应掩码"
    assert any(isinstance(v, (int, float)) for v in mgr_usd), "manager 下 _usd 键应是真值"
    # 非金额键不掩：延误天数、有效率、档位在 ops 下仍是原值
    ops_body = _ev(client, tid, role=OPS).json()
    assert isinstance(ops_body["alternatives"]["delay_days"], (int, type(None)))
    assert ops_body["trust"].get("tier") is not None or ops_body["trust"]["available"] is False


def test_404_unknown_task(api):
    """未知 task → 404 诚实空态 + 统一错误信封（error.code=not_found）。"""
    client, _v, _s = api
    r = client.get("/proposals/TSK-NOT-A-REAL-TASK/evidence", headers=SIM)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_xworld_dual_routing(api):
    """X-World 双世界：同端点在 sim/verify 各自路由到对应库，world 信封如实。"""
    client, vcon, scon = api
    stid = _pick(scon, "re.rule_id='R1'")
    vtid = vcon.execute("SELECT task_id FROM tasks WHERE approval_status IS NOT NULL "
                        "ORDER BY task_id LIMIT 1").fetchone()[0]
    assert _ev(client, stid, world=SIM).json()["world"] == "simulation"
    assert _ev(client, vtid, world=VERIFY).json()["world"] == "verification"


# ═══════════════════════════════════════════════════════════════════════════
# ①+ 付款锚归并增量（轮3-Opus 歧义3 清偿）：R19/R21 影响块吃 cockpit._payment_impact 单一来源
# ═══════════════════════════════════════════════════════════════════════════
def test_impact_payment_rows_for_r19(api):
    """R19 提案（sim，affected_so_line_ids='[]' 但任务 proposal_params 带 customer_id+金额）：
    impact.payment_rows 有归并行，行内容与 /cockpit/risk-impact 同一归并链对库现查一致
    （payment→sales_order→客户，唯一匹配）；note 改指认付款行（不再"无订单行级影响"到底）。"""
    client, _v, scon = api
    # 挑一个金额对 payments 唯一匹配的 R19 提案（动态判定——归并本就只对唯一命中生效，不赌样本）
    picked = None
    for row in scon.execute(
            "SELECT t.task_id, t.proposal_params, re.affected_value_usd FROM tasks t "
            "JOIN risk_events re ON re.risk_event_id=t.risk_event_id "
            "WHERE re.rule_id='R19' ORDER BY t.task_id"):
        import json as _json
        params = _json.loads(row["proposal_params"] or "{}")
        cid = params.get("customer_id")
        if not cid or row["affected_value_usd"] is None:
            continue
        hits = scon.execute(
            "SELECT payment_id, ref_id, counterparty_id, amount_usd FROM payments "
            "WHERE direction='in' AND status='scheduled' AND counterparty_id=? "
            "AND round(amount_usd,2)=round(?,2)", (cid, row["affected_value_usd"])).fetchall()
        if len(hits) == 1:
            picked = (row["task_id"], hits[0])
            break
    assert picked, "sim 应有金额唯一匹配的 R19 提案（轮3-D 已实证）"
    tid, exp = picked
    imp = _ev(client, tid).json()["impact"]
    assert imp["payment_rows"], "R19 影响块应带付款归并行"
    r0 = imp["payment_rows"][0]
    assert r0["payment_id"] == exp["payment_id"]
    assert r0["counterparty_id"] == exp["counterparty_id"]
    assert r0["ref_id"] == exp["ref_id"]
    assert r0["amount_usd"] == exp["amount_usd"]          # manager 见真值
    assert r0["is_anchor"] is True
    assert r0["overdue_days"] is None or isinstance(r0["overdue_days"], int)
    assert "付款" in imp["note"], "有归并行时 note 应指认付款行，不再是'无订单行级影响'话术"


def test_impact_payment_rows_masked_for_ops(api):
    """脱敏：ops 看 payment_rows 内 amount_usd 掩码（走既有装配末端 _mask_money，不开新洞）。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R19'")
    imp = _ev(client, tid, role=OPS).json()["impact"]
    assert "payment_rows" in imp, "R19 影响块应带 payment_rows 键（含空数组诚实空态）"
    for r in imp["payment_rows"]:
        assert r["amount_usd"] == MASK, "ops 下付款行金额必须掩码"


def test_impact_no_payment_keys_for_non_payment_anchor(api):
    """非付款锚提案（R1）：impact 不带 payment_rows/payment_note 键——载荷 byte-identical 不回归。"""
    client, _v, scon = api
    tid = _pick(scon, "re.rule_id='R1' AND re.affected_so_line_ids NOT IN ('','[]')")
    imp = _ev(client, tid).json()["impact"]
    assert "payment_rows" not in imp and "payment_note" not in imp


# ═══════════════════════════════════════════════════════════════════════════
# ② summary —— precedent_summary_line 纯函数（runtime propose 步引用的同一逻辑）
# ═══════════════════════════════════════════════════════════════════════════
def test_summary_line_real_and_first_case(api):
    """precedent_summary_line：真数据回一句含『同类 N 例…事后有效』的白话摘要；验证世界（0 先例）回『首例』。
    此纯函数即 runtime propose 步 note 引用的证据逻辑（spec §②），role 无关、不含金额。"""
    _c, vcon, scon = api
    # sim 真数据：任取一个有先例的 R1 风险
    rid = scon.execute("SELECT risk_event_id FROM risk_events WHERE rule_id='R1' "
                       "ORDER BY risk_event_id LIMIT 1").fetchone()[0]
    line = precedent_summary_line(scon, rid)
    assert "同类" in line and "例" in line
    # verify：首例
    vrid = vcon.execute("SELECT risk_event_id FROM risk_events ORDER BY risk_event_id LIMIT 1").fetchone()[0]
    assert "首例" in precedent_summary_line(vcon, vrid)
