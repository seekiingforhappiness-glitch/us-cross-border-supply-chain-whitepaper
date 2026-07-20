#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_evidence_export.py —— V23④ 证据包导出验收用例（决策日志 V23④）。

隔离纪律（同 apps/api/test_evidence.py）：两库各一 **模块级临时副本**（保留原名 → _infer_world 推断
verification/simulation），monkeypatch main.DEFAULT_DB_PATH/SIMWORLD_DB_PATH 指向副本，走 X-World 真解析
链路——**绝不写真库**（导出审计行落的是临时副本的 action_log；业务双库 md5/digest 不因测试改变）。

覆盖（任务书五点 × 真数据/边界）：
  ① 包含七块结构断言（risk_snapshot/impact/tasks/timeline/coordination/precedents/export_meta）。
  ② cs 角色导出包内金额=掩码值（不旁路实证：快照 affected_value_usd / impact.amount_usd /
     时间线嵌套提案金额 collect_amount_usd 全 MASK；manager 同字段=真数值对照）。
  ③ 导出后 action_log 新增审计行（action=ExportEvidencePackage，actor/role/params 如实；json/html 各一行）。
  ④ sim 世界包含模拟标注（json: export_meta.simulation_notice + world_is_simulation；html: 水印+横幅）。
  ⑤ html 版自包含（无外链 src/href/url() 指向外部域；无 <script src>/<link href>）。
  边界：404（风险查无）/ 422（未知 format）/ 审批理由与审批人如实呈现（RSK-SIM-00250 真实审批链）。
"""
from __future__ import annotations

import re
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import apps.api.main as apimain
from agent.tools import MASK
from apps.api.main import REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"
SIM = {"X-World": "sim"}
VERIFY = {"X-World": "verify"}
MANAGER = "manager"
CS = "cs"


# ═══════════════════════════════════════════════════════════════════════════
# fixture：双世界临时副本 + monkeypatch 两库常量（走 X-World 真解析链路，绝不写真库——
# 导出审计写连接经 get_db_path 解析到副本路径，审计行只落副本）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    assert REPO_DB.exists() and SIM_DB.exists(), "需先跑 datagen/build_ontology/seed + sim.backfill"
    d = tmp_path_factory.mktemp("evidence_export")
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


# ─── 动态发现 risk id（查条件而非硬编码脆 id，对 sim 重生成有韧性） ───
def _pick_risk(con: sqlite3.Connection, where: str = "1=1") -> str:
    row = con.execute(
        f"SELECT re.risk_event_id FROM risk_events re WHERE {where} "
        f"ORDER BY re.risk_event_id LIMIT 1").fetchone()
    assert row is not None, f"世界库中查无满足 [{where}] 的风险——检查数据前提"
    return row["risk_event_id"]


def _pick_risk_with_task(con: sqlite3.Connection) -> str:
    row = con.execute(
        "SELECT re.risk_event_id FROM risk_events re "
        "JOIN tasks t ON t.risk_event_id = re.risk_event_id "
        "ORDER BY re.risk_event_id LIMIT 1").fetchone()
    assert row is not None, "世界库中查无带任务的风险——检查数据前提"
    return row["risk_event_id"]


def _pick_risk_with_approval(con: sqlite3.Connection) -> str | None:
    """有真实 ApproveMitigation 审计行的风险（审批理由/审批人如实呈现用例）；无则 None（跳过）。"""
    row = con.execute(
        "SELECT t.risk_event_id FROM action_log al "
        "JOIN tasks t ON t.task_id = al.target_object_id "
        "WHERE al.action='ApproveMitigation' LIMIT 1").fetchone()
    return row["risk_event_id"] if row else None


def _ep(client, risk_id, role=MANAGER, world=SIM, fmt="json", actor="tester-01"):
    return client.get(f"/risk-events/{risk_id}/evidence-package?format={fmt}",
                      headers={"X-Role": role, "X-Actor": actor, **world})


# ═══════════════════════════════════════════════════════════════════════════
# ① 七块结构断言
# ═══════════════════════════════════════════════════════════════════════════
def test_package_has_seven_blocks(api):
    client, _, scon = api
    rid = _pick_risk_with_task(scon)
    r = _ep(client, rid)
    assert r.status_code == 200
    j = r.json()
    # 七块齐备（任务书①-⑦）
    for block in ("risk_snapshot", "impact", "tasks", "timeline", "coordination",
                  "precedents", "export_meta"):
        assert block in j, f"证据包缺第 {block} 块"
    assert j["kind"] == "risk_evidence_package"
    assert j["risk_event_id"] == rid
    assert j["world"] == "simulation"
    # ① 快照：脱敏后字段 + 白话中文名（字段名→中文映射覆盖在场字段）
    snap = j["risk_snapshot"]
    assert snap["object_type"] == "RiskEvent" and snap["id"] == rid
    assert snap["fields"]["risk_event_id"] == rid
    label_map = {p["field"]: p["label"] for p in snap["field_labels_cn"]}
    assert label_map["risk_event_id"] == "风险编号"
    assert set(label_map) == set(snap["fields"])
    # ③ 任务：该风险名下任务数与库中一致
    n_tasks = scon.execute("SELECT count(*) FROM tasks WHERE risk_event_id=?", (rid,)).fetchone()[0]
    assert j["tasks"]["count"] == n_tasks and len(j["tasks"]["items"]) == n_tasks
    # ⑦ 元信息：导出人/角色/时间/世界如实
    meta = j["export_meta"]
    assert meta["exported_by"] == "tester-01" and meta["role"] == MANAGER
    assert meta["as_of"] and meta["world"] == "simulation"


def test_blocks_honest_empty_not_500(api):
    """逐块可缺省诚实标注：无任务/无协调/无先例的风险照样 200，块内 count=0/空态说明（不 500 不编造）。"""
    client, vcon, _ = api
    # 验证世界随便挑一条风险：各块自理空态，包必须完整返回
    rid = _pick_risk(vcon)
    r = _ep(client, rid, world=VERIFY)
    assert r.status_code == 200
    j = r.json()
    assert j["world"] == "verification"
    for block in ("tasks", "timeline", "coordination"):
        b = j[block]
        assert "count" in b and b["count"] == len(b.get("items", [])), f"{block} 计数与明细不一致"
    # precedents：available/empty/n 语义由波E precedents_block 保证，这里只断言键在场不炸
    assert "precedents" in j and isinstance(j["precedents"], dict)


# ═══════════════════════════════════════════════════════════════════════════
# ② 脱敏口径 = 与对象读端点按声明一致（V25 裁决3，2026-07-20 Daniel"对齐：证据包按声明放开"）
# ═══════════════════════════════════════════════════════════════════════════
def test_amounts_follow_declaration_same_as_object_endpoint(api):
    """V25 裁决3 核心不变量：证据包快照的每个字段值 == 同角色 /objects 读端点的同字段值。
    affected_value_usd 未在 sensitiveFieldRules 声明 → cs 与 manager 同见真值（原全量成本门已撤，
    与对象卡口径打架的轮4 实证就此闭环）；已声明字段若为该对象所有则仍掩（masker 层兜住）。"""
    client, _, scon = api
    rid = _pick_risk(scon, "re.affected_value_usd IS NOT NULL AND re.affected_value_usd > 0")
    for role in (MANAGER, CS):
        pkg = _ep(client, rid, role=role).json()["risk_snapshot"]["fields"]
        resp = client.get(f"/objects/RiskEvent/{rid}", headers={"X-Role": role}).json()
        obj = resp.get("object", resp)  # 端点信封形状兼容
        for k, v in pkg.items():
            if k in obj:
                assert v == obj[k], f"{role} 证据包 {k}={v} != 对象读端点 {obj[k]}（两出口口径漂移）"
    # 回归钉（浏览器实测抓到的 bug）：白话中文标签不得被金额掩码误伤（label 恒为中文名，非锁串）
    jc = _ep(client, rid, role=CS).json()
    cs_labels = {p["field"]: p["label"] for p in jc["risk_snapshot"]["field_labels_cn"]}
    assert cs_labels["affected_value_usd"] == "影响金额（美元）"


def test_declared_fields_still_masked_in_package(api):
    """V25 裁决3 的另一半：按声明放开 ≠ 全放开——若证据包任何块内出现**已声明**敏感字段
    （以 Invoice 组式金额为例经由对象读端点抽验），非授权角色仍须掩码。此处以端点级对照钉住：
    cs 读 Invoice.total_usd 经对象读端点=MASK（masker 组式规则，V23③），证据包不得出现该对象
    明文（当前包不含发票快照块，断言为守恒式：包内任何 total_usd 键不得为数值——有则必掩）。"""
    client, _, scon = api
    rid = _pick_risk_with_task(scon)
    jc = _ep(client, rid, role=CS).json()
    leaks: list[str] = []

    def _scan(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                pth = f"{path}.{k}"
                if k == "total_usd" and isinstance(v, (int, float)) and v != 0:
                    leaks.append(f"{pth}={v}")
                _scan(v, pth)
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _scan(item, f"{pth}[{i}]" if False else f"{path}[{i}]")

    _scan(jc)
    assert not leaks, f"cs 证据包泄漏已声明敏感字段：{leaks}"


def test_timeline_nested_proposal_amount_follows_declaration(api):
    """V25 裁决3 后：时间线 params.proposal 嵌套金额（提案参数，未在声明清单）对 cs 与 manager
    同值（与任务对象卡上 proposal_params 的可见性一致，不再单独全掩）。有真实审批链的世界才跑。"""
    client, _, scon = api
    rid = _pick_risk_with_approval(scon)
    if rid is None:
        pytest.skip("本世界无 ApproveMitigation 审计行——无嵌套提案金额可实证")
    jm = _ep(client, rid, role=MANAGER).json()
    jc = _ep(client, rid, role=CS).json()

    def _usd_pairs(j):
        out = {}
        for t in j["timeline"]["items"]:
            prop = (t.get("params") or {}).get("proposal") or {}
            for k, v in prop.items():
                if k.endswith("_usd"):
                    out[f"{t.get('action')}:{k}"] = v
        return out

    pm, pc = _usd_pairs(jm), _usd_pairs(jc)
    assert pm, "有审批行的风险，时间线应含至少一个提案金额键"
    assert pm == pc, f"未声明的提案金额两角色应同值（V25 与对象卡口径一致）：manager={pm} cs={pc}"


def test_approval_reason_and_approver_surfaced(api):
    """③块"审批状态/理由/审批人"如实：有真实审批的任务，approval.reason/approver_actor/decision 齐备。"""
    client, _, scon = api
    rid = _pick_risk_with_approval(scon)
    if rid is None:
        pytest.skip("本世界无 ApproveMitigation 审计行")
    j = _ep(client, rid, role=MANAGER).json()
    approved = [t for t in j["tasks"]["items"] if t.get("approval")]
    assert approved, "有审批审计行的风险，任务块应至少一条带 approval 轨迹"
    ap = approved[0]["approval"]
    assert ap["approver_actor"] and ap["approver_role"]
    assert ap["decision"] in ("approved", "rejected")
    assert isinstance(ap["reason"], str) and ap["reason"].strip(), "审批理由必填（V22③）应如实呈现"


# ═══════════════════════════════════════════════════════════════════════════
# ③ 导出后 action_log 新增审计行
# ═══════════════════════════════════════════════════════════════════════════
def test_export_writes_audit_row(api):
    client, _, scon = api
    rid = _pick_risk(scon)
    before = scon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    r = _ep(client, rid, role=MANAGER, fmt="json", actor="auditor-07")
    assert r.status_code == 200
    rows = scon.execute(
        "SELECT actor, role, target_object_id, params_json, result FROM action_log "
        "WHERE action='ExportEvidencePackage' ORDER BY log_id DESC").fetchall()
    assert len(rows) == before + 1, "导出后 action_log 应新增恰一行 ExportEvidencePackage"
    row = rows[0]
    assert row["actor"] == "auditor-07" and row["role"] == MANAGER
    assert row["target_object_id"] == rid and row["result"] == "ok"
    assert '"format": "json"' in row["params_json"] and rid in row["params_json"]
    # html 导出同样落审计（format 如实）
    r2 = _ep(client, rid, role=CS, fmt="html", actor="auditor-08")
    assert r2.status_code == 200
    row2 = scon.execute(
        "SELECT actor, role, params_json FROM action_log "
        "WHERE action='ExportEvidencePackage' ORDER BY log_id DESC LIMIT 1").fetchone()
    assert row2["actor"] == "auditor-08" and row2["role"] == CS
    assert '"format": "html"' in row2["params_json"]


def test_404_no_audit_row(api):
    """风险查无 → 404，且不落审计行（没导出成功就没有"导出了"的痕，审计不撒谎）。"""
    client, _, scon = api
    before = scon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    r = _ep(client, "RSK-NOPE-99999")
    assert r.status_code == 404
    assert "不存在" in r.json()["detail"]
    after = scon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    assert after == before


def test_unknown_format_422(api):
    client, _, scon = api
    rid = _pick_risk(scon)
    r = _ep(client, rid, fmt="pdf")
    assert r.status_code == 422
    assert "format" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# ④ sim 世界包含模拟标注（不变量11）
# ═══════════════════════════════════════════════════════════════════════════
def test_sim_world_simulation_notice(api):
    client, vcon, scon = api
    rid = _pick_risk(scon)
    j = _ep(client, rid, world=SIM).json()
    meta = j["export_meta"]
    assert meta["world_is_simulation"] is True
    assert meta["simulation_notice"] and "模拟世界" in meta["simulation_notice"]
    # html：水印 + 横幅双显著标注
    h = _ep(client, rid, world=SIM, fmt="html").text
    assert "模拟数据" in h and "SIMULATION" in h
    # 断真实元素在场（CSS 规则定义恒在模板里，不能作为"渲染了"的证据）
    assert "<div class='watermark'>" in h and "<div class='sim-banner'>" in h
    # 验证世界对照：无模拟标注（不给真数据扣模拟帽子——反向也是诚实）
    vrid = _pick_risk(vcon)
    jv = _ep(client, vrid, world=VERIFY).json()
    assert jv["export_meta"]["world_is_simulation"] is False
    assert jv["export_meta"]["simulation_notice"] is None
    hv = _ep(client, vrid, world=VERIFY, fmt="html").text
    # 断元素在场性而非 CSS 类名子串（样式表恒含 .sim-banner/.watermark 规则定义，属静态模板）
    assert "<div class='sim-banner'>" not in hv and "<div class='watermark'>" not in hv


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ html 自包含（无外链 src/href 到外部域）
# ═══════════════════════════════════════════════════════════════════════════
def test_html_self_contained_no_external_links(api):
    client, _, scon = api
    rid = _pick_risk_with_task(scon)
    r = _ep(client, rid, fmt="html")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    h = r.text
    # 无任何 src=/href= 指向外部（http/https/协议相对 //）；内联 style 无 url(http…) 远程资源
    assert not re.search(r'(?:src|href)\s*=\s*["\'](?:https?:)?//', h, re.I), "html 含外链 src/href"
    assert not re.search(r'url\(\s*["\']?(?:https?:)?//', h, re.I), "html CSS 含远程 url()"
    assert "<script" not in h.lower(), "html 不应含脚本（纯静态可打印文档）"
    assert "<link" not in h.lower(), "html 不应含外部样式表"
    # 自包含要素在场：内联 <style> + 打印媒体查询（可浏览器打印为 PDF）
    assert "<style>" in h and "@media print" in h
    # 七块章节标题齐备
    for t in ("风险事件快照", "影响链", "关联任务与提案", "时间线", "协调线程", "同类先例", "导出元信息"):
        assert t in h, f"html 缺章节 {t}"


def test_html_renders_same_values_as_json(api):
    """V25 裁决3 后：html 只是渲染层非第二数据路——同角色 html 内含 json 快照的同值
    （原"cs html 应含掩码串"断言随全量成本门撤除而更新：未声明字段两格式同为明文）。"""
    client, _, scon = api
    rid = _pick_risk(scon, "re.affected_value_usd IS NOT NULL AND re.affected_value_usd > 0")
    for role in (MANAGER, CS):
        j = _ep(client, rid, role=role).json()
        h = _ep(client, rid, role=role, fmt="html").text
        v = j["risk_snapshot"]["fields"].get("affected_value_usd")
        if isinstance(v, (int, float)):
            assert str(v) in h or f"{v:,.2f}" in h or f"{v:.2f}" in h, f"{role} html 缺 json 同值 {v}"
        else:
            assert v in h, f"{role} html 缺掩码串"


# ═══════════════════════════════════════════════════════════════════════════
# 世界隔离：verify 导出的审计行落 verify 副本、不落 sim 副本（X-World 贯穿审计写连接）
# ═══════════════════════════════════════════════════════════════════════════
def test_audit_lands_in_requested_world_only(api):
    client, vcon, scon = api
    rid = _pick_risk(vcon)
    v_before = vcon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    s_before = scon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    assert _ep(client, rid, world=VERIFY).status_code == 200
    v_after = vcon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    s_after = scon.execute(
        "SELECT count(*) FROM action_log WHERE action='ExportEvidencePackage'").fetchone()[0]
    assert v_after == v_before + 1, "verify 世界导出应在 verify 库落审计"
    assert s_after == s_before, "verify 世界导出不得写 sim 库"
