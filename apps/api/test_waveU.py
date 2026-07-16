#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_waveU.py —— 波U 后端契约层验收（U1 X-World / U2 provenance / U3 gating / U6 threads）。

隔离纪律（同 apps/api/test_api.py、test_cockpit.py）：绝不碰真库。
  · U1/U2/U6 数据面：把 data/ontology.sqlite、data/simworld.sqlite 各拷一份**保留原文件名**的临时
    副本（原名保证 _infer_world 推断出 verification/simulation），再 monkeypatch
    apps.api.main.DEFAULT_DB_PATH / SIMWORLD_DB_PATH 指向副本——这样 X-World 请求头的真实解析链路
    （get_db_path 读头 → 选库 → get_ro_connection 只读连）被完整覆盖，而非绕开它换路径。
    期间摘掉 get_db_path 的 dependency_overrides 并清掉 ONTOLOGY_DB 环境变量，让「缺省=默认库」这条
    向后兼容路径也走真链路。
  · U3 治理面：gating_report.json 无库依赖，monkeypatch apps.api.governance.GATING_REPORT_PATH
    指向临时文件（存在/缺失/损坏三态），不碰真报告。

"现查现算对照，勿硬编码具体值"：每条断言的期望值当场对临时库跑 SQL（如协作线程数、pending 提案数、
provenance 样例 id），不誊抄 28/2 等字面量；锚点（带 est_cost_usd 的线程等）运行时从库现查发现。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import apps.api.governance as gov
import apps.api.main as apimain
from agent.mcp_server import MASK
from apps.api.main import REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"
REAL_GATING = REPO_ROOT / "data" / "gating_report.json"
ZONE_ORDER = ["money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions"]

MANAGER = {"X-Role": "manager"}


# ═══════════════════════════════════════════════════════════════════════════
# fixture：双世界临时副本 + monkeypatch 两库常量（走 X-World 真解析链路）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """模块级：两库各一临时副本（保留原名），monkeypatch main.DEFAULT_DB_PATH/SIMWORLD_DB_PATH
    指向副本；摘掉 dependency_overrides + 清 ONTOLOGY_DB 环境变量，使 X-World 请求头与「缺省=默认库」
    都走真实生产链路。yield (client, verify_con, sim_con)——后两者供期望值现查对照。"""
    assert REPO_DB.exists() and SIM_DB.exists(), "需先跑 datagen/build_ontology/seed + sim.backfill"
    d = tmp_path_factory.mktemp("waveU")
    vpath = d / "ontology.sqlite"      # 原名 → _infer_world → verification
    spath = d / "simworld.sqlite"      # 原名 → _infer_world → simulation
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


def _zone(body: dict, name: str) -> dict:
    return {z["zone"]: z for z in body["zones"]}[name]


# ═══════════════════════════════════════════════════════════════════════════
# U1 —— X-World 请求头双世界解析
# ═══════════════════════════════════════════════════════════════════════════
def test_xworld_sim_header_routes_to_simworld_real_data(api):
    """X-World: sim → 全读端点解析到 simworld 副本；world 标识与聚合数据都来自模拟世界，
    数值当场对 sim 副本现查对照（非硬编码 28/…）。"""
    client, _vcon, scon = api

    # 1) ontology 自描述端点的 world 信封
    assert client.get("/ontology", headers={"X-World": "sim"}).json()["world"] == "simulation"

    # 2) objects 列表端点也经同一依赖获得双世界能力
    assert client.get("/objects/RiskEvent", headers={"X-World": "sim"}).json()["world"] == "simulation"

    # 3) 协作流：条数 = sim 副本真实计数（U6 端点同时验证 X-World 生效）
    exp_threads = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]
    tb = client.get("/collaboration/threads", headers={**MANAGER, "X-World": "sim"}).json()
    assert tb["world"] == "simulation" and tb["available"] is True
    assert tb["count"] == exp_threads and len(tb["threads"]) == exp_threads

    # 4) 驾驶舱聚合：待拍板 headline = sim pending 提案数（真数据流经聚合层）
    vb = client.get("/cockpit/vitals", headers={**MANAGER, "X-World": "sim"}).json()
    assert vb["world"] == "simulation"
    exp_pending = scon.execute("SELECT count(*) FROM tasks WHERE approval_status='pending'").fetchone()[0]
    assert _zone(vb, "decisions")["headline_value"] == exp_pending


def test_xworld_verify_header_routes_to_ontology(api):
    """X-World: verify → 解析到验证库副本；条数对验证副本现查对照。"""
    client, vcon, _scon = api
    assert client.get("/ontology", headers={"X-World": "verify"}).json()["world"] == "verification"
    exp = vcon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]
    tb = client.get("/collaboration/threads", headers={**MANAGER, "X-World": "verify"}).json()
    assert tb["world"] == "verification"
    assert tb["count"] == exp


def test_xworld_absent_is_backward_compatible_default(api, tmp_path):
    """缺省（无 X-World）= 现行 ONTOLOGY_DB 环境变量/默认库行为（byte-identical 向后兼容）：
    · 无头 + 无环境变量 → 默认库（verification）；
    · 无头 + ONTOLOGY_DB=sim 副本 → simulation（环境变量仍被尊重，与升级前一致）。
    证明 U1 只在**显式带头**时改变行为，不带头则逐字沿用旧路径。"""
    client, _vcon, _scon = api
    # 无头 → 默认库（api fixture 已清 ONTOLOGY_DB、DEFAULT_DB_PATH=验证副本）
    assert client.get("/ontology").json()["world"] == "verification"

    # 无头 + 环境变量指向 sim 副本：旧行为（环境变量驱动）逐字保留
    sim_copy = tmp_path / "simworld.sqlite"
    shutil.copy(SIM_DB, sim_copy)
    mp = pytest.MonkeyPatch()
    mp.setenv("ONTOLOGY_DB", str(sim_copy))
    try:
        with TestClient(app) as fresh:
            assert fresh.get("/ontology").json()["world"] == "simulation"
    finally:
        mp.undo()


def test_xworld_unknown_value_422_across_read_endpoints(api):
    """未知 X-World 值 → 422 fail-fast（不静默回退默认，避免"以为切了世界其实没切"），
    且该守卫对所有走 get_db_path 的读端点一致生效。"""
    client, _vcon, _scon = api
    for ep in ("/ontology", "/objects/RiskEvent", "/cockpit/vitals",
               "/cockpit/panorama", "/cockpit/ai-flow", "/collaboration/threads"):
        r = client.get(ep, headers={**MANAGER, "X-World": "banana"})
        assert r.status_code == 422, (ep, r.status_code)


def test_xworld_normalization_and_aliases_unit(api):
    """get_db_path 归一化契约（裸函数直调）：大小写不敏感 + 去空白 + 多别名；None（无头无环境变量）
    回退默认库；未知值抛 422。api fixture 已把两库常量指到副本，故可对副本路径精确断言。"""
    m = apimain
    for alias in ("sim", "SIM", "  SiMuLaTiOn  ", "simworld"):
        assert get_db_path(alias) == str(m.SIMWORLD_DB_PATH), alias
    for alias in ("verify", "VERIFY", " verification "):
        assert get_db_path(alias) == str(m.DEFAULT_DB_PATH), alias
    assert get_db_path(None) == str(m.DEFAULT_DB_PATH)      # 无头无环境变量 → 默认库
    with pytest.raises(HTTPException) as ei:
        get_db_path("prod")
    assert ei.value.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# U2 —— provenance 信封（?provenance=1 开关；缺省关=byte-identical）
# ═══════════════════════════════════════════════════════════════════════════
def test_provenance_vitals_toggle_and_byte_identical(api):
    """?provenance=1 为七区卡附 {caliber,sources,sample_ids}；缺省无此键，且加键前后既有字段
    逐字不变（byte-identical，仅多出 provenance 顶层键）。sample_ids 现查对照=真 id 非编造。"""
    client, vcon, _scon = api
    hv = {**MANAGER, "X-World": "verify"}
    base = client.get("/cockpit/vitals", headers=hv).json()
    assert "provenance" not in base                          # 缺省关

    withp = client.get("/cockpit/vitals", params={"provenance": 1}, headers=hv).json()
    assert "provenance" in withp
    # 除新增 provenance 键外，其余载荷逐字等价
    assert {k: v for k, v in withp.items() if k != "provenance"} == base

    prov = withp["provenance"]
    assert set(prov) == set(ZONE_ORDER)                      # 七区各一条溯源
    for zone, pv in prov.items():
        assert set(pv) == {"caliber", "sources", "sample_ids"}, zone
        assert isinstance(pv["caliber"], str) and pv["caliber"].strip(), zone
        assert isinstance(pv["sources"], list) and pv["sources"], zone
        assert isinstance(pv["sample_ids"], list) and len(pv["sample_ids"]) <= 3, zone

    # sample_ids 是真 id：money=open R4/R5/R6 风险、decisions=pending 提案，现查对照
    exp_money = [r[0] for r in vcon.execute(
        "SELECT risk_event_id FROM risk_events WHERE rule_id IN ('R4','R5','R6') "
        "AND status='open' ORDER BY risk_event_id LIMIT 3")]
    assert prov["money"]["sample_ids"] == exp_money
    exp_dec = [r[0] for r in vcon.execute(
        "SELECT task_id FROM tasks WHERE approval_status='pending' ORDER BY task_id LIMIT 3")]
    assert prov["decisions"]["sample_ids"] == exp_dec
    assert any(prov[z]["sample_ids"] for z in ZONE_ORDER), "至少一区应有真实样例（证明取样机制真跑）"


def test_provenance_panorama_and_ai_flow_toggle(api):
    """panorama/ai-flow 同款开关：缺省无 provenance 键；provenance=1 附对应子结构
    （panorama=nodes/edges/alerts，ai-flow=timeline）。"""
    client, _vcon, _scon = api
    hv = {**MANAGER, "X-World": "verify"}

    assert "provenance" not in client.get("/cockpit/panorama", headers=hv).json()
    assert "provenance" not in client.get("/cockpit/ai-flow", headers=hv).json()

    pano = client.get("/cockpit/panorama", params={"provenance": 1}, headers=hv).json()
    assert set(pano["provenance"]) == {"nodes", "edges", "alerts"}
    for pv in pano["provenance"].values():
        assert set(pv) == {"caliber", "sources", "sample_ids"}

    flow = client.get("/cockpit/ai-flow", params={"provenance": 1}, headers=hv).json()
    assert set(flow["provenance"]) == {"timeline"}
    assert set(flow["provenance"]["timeline"]) == {"caliber", "sources", "sample_ids"}


def test_provenance_survives_role_masking(api):
    """provenance 无敏感字段键，过 X-Role 脱敏应原样穿透：即使无权角色（cs），provenance 仍在、
    caliber 白话完整（脱敏只掩敏感值，不吞溯源提示）。"""
    client, _vcon, _scon = api
    body = client.get("/cockpit/vitals", params={"provenance": 1},
                      headers={"X-Role": "cs", "X-World": "verify"}).json()
    assert "provenance" in body
    assert all(body["provenance"][z]["caliber"].strip() for z in ZONE_ORDER)


# ═══════════════════════════════════════════════════════════════════════════
# U3 —— GET /governance/gating（有文件 / 缺文件 / 损坏 三态；只读 JSON 不 import gating）
# ═══════════════════════════════════════════════════════════════════════════
def test_gating_present_synthetic(monkeypatch, tmp_path):
    """有报告文件：字段挑选/透传契约——display_only 原样、tier_distribution=summary.by_tier、
    每域 {name(=plain),tier,rate,ci,n,gaps(白话)}、config 版本。用合成报告精确控制期望值。"""
    report = {
        "generated_at": "2026-07-16T00:00:00Z",
        "display_only": True,
        "config": {"version": "draft-1", "status": "draft",
                   "ladder": [{"tier": "shadow", "_plain": "只算不放"}],
                   "promotions": {"auto": {"_plain": "n≥1000 且纠正率<5%"}}},
        "sources": {"shadow_db": "shadow.sqlite", "main_db": "ontology.sqlite"},
        "summary": {"domain_count": 2, "by_tier": {"shadow": 2}, "reached_auto": [],
                    "note": "各域一览"},
        "telemetry": {"llm_calls_total": 0},
        "honest_note": "样本不足是设计内天花板",
        "domains": [
            {"domain": "R1", "plain": "延误击穿承诺", "group": "resolution", "n": 0, "hits": 0,
             "rate": None, "ci": None, "low_sample": False, "escalation": {"ref_n": 0},
             "tier": "shadow", "next_tier": "suggest", "gaps": ["缺样本：n=0 < 30"]},
            {"domain": "R2", "plain": "对账差异", "group": "reconciliation", "n": 5, "hits": 1,
             "rate": 0.2, "ci": [0.0, 0.5], "low_sample": True, "escalation": {},
             "tier": "shadow", "next_tier": "suggest", "gaps": []},
        ],
    }
    dest = tmp_path / "gating_report.json"
    dest.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(gov, "GATING_REPORT_PATH", dest)

    with TestClient(app) as c:
        body = c.get("/governance/gating").json()

    assert body["available"] is True
    assert body["display_only"] is True                      # 原样透传，绝不改
    assert body["config_version"] == "draft-1"
    assert body["config_status"] == "draft"
    assert body["tier_distribution"] == {"shadow": 2}
    assert body["domain_count"] == 2
    assert body["ladder"] and body["promotions"]             # 阶梯/升档白话原样带上
    assert len(body["domains"]) == 2
    d0 = body["domains"][0]
    assert d0["name"] == "延误击穿承诺"                        # plain → name（老板语言域名）
    assert d0["domain"] == "R1" and d0["tier"] == "shadow"
    assert d0["rate"] is None and d0["ci"] is None and d0["n"] == 0
    assert d0["gaps"] == ["缺样本：n=0 < 30"]                  # 白话差距清单原样
    d1 = body["domains"][1]
    assert d1["name"] == "对账差异" and d1["rate"] == 0.2 and d1["ci"] == [0.0, 0.5]


def test_gating_real_report_smoke(monkeypatch, tmp_path):
    """真 data/gating_report.json（存在时）：schema 与 summarizer 对齐守卫——available、
    每域带 name/tier/gaps、tier_distribution 与真文件 summary.by_tier 一致。文件缺失则跳过。"""
    if not REAL_GATING.exists():
        pytest.skip("data/gating_report.json 不存在，跳过真报告 smoke")
    dest = tmp_path / "gating_report.json"
    shutil.copy(REAL_GATING, dest)                            # 拷贝到临时，不碰真文件
    raw = json.loads(dest.read_text(encoding="utf-8"))
    monkeypatch.setattr(gov, "GATING_REPORT_PATH", dest)

    with TestClient(app) as c:
        body = c.get("/governance/gating").json()

    assert body["available"] is True
    assert body["display_only"] == raw.get("display_only")
    assert body["tier_distribution"] == raw["summary"]["by_tier"]
    assert body["domain_count"] == raw["summary"]["domain_count"]
    assert len(body["domains"]) == len(raw["domains"])
    for d in body["domains"]:
        assert {"domain", "name", "tier", "rate", "ci", "n", "gaps"} <= set(d)
    # 对抗复核发现1的回归堵门：sources 里任何字符串值不得携带路径分隔符——
    # 真报告的库路径是主机绝对路径（含 OS 用户名），HTTP 响应只许留 basename。
    def _no_paths(node):
        if isinstance(node, dict):
            for v in node.values():
                _no_paths(v)
        elif isinstance(node, str):
            assert "/" not in node, f"sources 泄露路径片段：{node}"
    _no_paths(body["sources"])


def test_gating_absent_honest_empty(monkeypatch, tmp_path):
    """文件不存在 → HTTP 200 + {available:false, reason}（诚实空态，绝不 404/500）。"""
    monkeypatch.setattr(gov, "GATING_REPORT_PATH", tmp_path / "nope.json")
    with TestClient(app) as c:
        r = c.get("/governance/gating")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False and body["reason"]


def test_gating_corrupt_degrades(monkeypatch, tmp_path):
    """文件存在但 JSON 损坏 → 降级为 available:false（不放大为故障，不拿旧值/猜测冒充档位）。"""
    dest = tmp_path / "gating_report.json"
    dest.write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(gov, "GATING_REPORT_PATH", dest)
    with TestClient(app) as c:
        r = c.get("/governance/gating")
    assert r.status_code == 200 and r.json()["available"] is False


# ═══════════════════════════════════════════════════════════════════════════
# U6 —— GET /collaboration/threads（真数据聚合 / 风险过滤 / X-Role 脱敏 / 缺表空态）
# ═══════════════════════════════════════════════════════════════════════════
def test_threads_sim_aggregation_and_enrichment(api):
    """sim 协作流：条数/聚合与副本现查一致；关联风险/任务富化正确；by_risk 与 summary 计数自洽。"""
    client, _vcon, scon = api
    body = client.get("/collaboration/threads", headers={**MANAGER, "X-World": "sim"}).json()
    exp = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]
    assert body["available"] is True and body["count"] == exp

    # by_risk 分组的线程数合计 = 总条数；summary.by_state 合计 = 总条数（聚合自洽，无丢/无重复计）
    assert sum(g["thread_count"] for g in body["by_risk"]) == exp
    assert sum(body["summary"]["by_state"].values()) == exp
    assert set(body["summary"]) == {"by_state", "by_counterparty_type", "escalated"}

    # 关联富化：挑一条 risk_event_id 真实存在于 risk_events 的线程，其 risk 摘要字段对副本现查对照
    anchor = scon.execute(
        "SELECT ct.coordination_id, ct.risk_event_id, r.rule_id, r.severity, r.status "
        "FROM coordination_threads ct JOIN risk_events r ON r.risk_event_id=ct.risk_event_id "
        "ORDER BY ct.coordination_id LIMIT 1").fetchone()
    assert anchor, "sim 应有 risk_event_id 命中 risk_events 的协作线程"
    t = next(t for t in body["threads"] if t["coordination_id"] == anchor["coordination_id"])
    assert t["risk"] is not None
    assert t["risk"]["rule_id"] == anchor["rule_id"]
    assert t["risk"]["severity"] == anchor["severity"]
    assert t["risk"]["status"] == anchor["status"]


def test_threads_risk_filter(api):
    """risk_event_id 过滤：只回该风险的线程，条数对副本现查对照，且每条 risk_event_id 相符。"""
    client, _vcon, scon = api
    rid = scon.execute("SELECT risk_event_id FROM coordination_threads "
                       "WHERE risk_event_id IS NOT NULL AND risk_event_id!='' "
                       "ORDER BY coordination_id LIMIT 1").fetchone()[0]
    exp = scon.execute("SELECT count(*) FROM coordination_threads WHERE risk_event_id=?",
                       (rid,)).fetchone()[0]
    body = client.get("/collaboration/threads", params={"risk_event_id": rid},
                      headers={**MANAGER, "X-World": "sim"}).json()
    assert body["count"] == exp and exp >= 1
    assert all(t["risk_event_id"] == rid for t in body["threads"])


def test_threads_role_masking_est_cost(api):
    """X-Role 脱敏语义同 /objects：富化进来的 task.proposal_params.est_cost_usd（本体嵌套敏感规则，
    visibleTo ops/manager）对无权角色（cs）掩码、对有权角色（ops/manager）见真值。锚点运行时现查。"""
    client, _vcon, scon = api
    row = scon.execute(
        "SELECT ct.coordination_id, t.proposal_params FROM coordination_threads ct "
        "JOIN tasks t ON t.task_id=ct.task_id WHERE t.proposal_params LIKE '%est_cost_usd%' "
        "ORDER BY ct.coordination_id LIMIT 1").fetchone()
    assert row, "sim 需要至少一条 task 带 est_cost_usd 的协作线程"
    coord_id = row[0]
    true_cost = json.loads(row[1])["est_cost_usd"]

    def cost_for(role):
        body = client.get("/collaboration/threads",
                          headers={"X-Role": role, "X-World": "sim"}).json()
        t = next(t for t in body["threads"] if t["coordination_id"] == coord_id)
        return t["task"]["proposal_params"]["est_cost_usd"]

    assert cost_for("ops") == true_cost            # visibleTo ops → 真值
    assert cost_for("manager") == true_cost        # visibleTo manager → 真值
    assert cost_for("cs") == MASK                  # cs 无权 → 掩码（🔒无权查看）


def test_threads_missing_table_honest_empty(tmp_path):
    """缺 coordination_threads 表的世界 → HTTP 200 + available:false + 空数组（诚实空态，绝不 500）。
    用一个不含该表的临时 sqlite，显式 dependency_overrides 注入，自成一体不依赖 api fixture。"""
    empty = tmp_path / "empty.sqlite"
    con = sqlite3.connect(empty)
    con.execute("CREATE TABLE dummy(x)")
    con.commit(); con.close()

    app.dependency_overrides[get_db_path] = lambda: str(empty)
    try:
        with TestClient(app) as c:
            r = c.get("/collaboration/threads", headers={"X-Role": "ops"})
    finally:
        app.dependency_overrides.pop(get_db_path, None)

    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["count"] == 0 and body["threads"] == [] and body["by_risk"] == []
    assert "coordination_threads" in body["reason"]
