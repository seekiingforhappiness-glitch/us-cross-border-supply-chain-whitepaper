#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_api.py —— M5 验收用例（fastapi.testclient，无需起真 uvicorn 服务）。

隔离纪律（同 app/test_agent_security.py）：先把 data/ontology.sqlite（跑过 datagen.seed_demo_ops
的工作库）拷贝到临时文件，测试全程只读写临时文件，绝不污染真库；apps.api.main.get_db_path 依赖
经 app.dependency_overrides 换成临时路径（fastapi 标准做法），不依赖环境变量/进程重启。

"现查现算对照，勿硬编码具体值"贯彻到两层：
  1. 断言的期望值——直接对临时库 SQL 查询或对 M3 Pydantic 模型重新校验，不誊抄具体字段值。
  2. 断言用的锚点 id 本身——运行时从临时库动态发现（哪个 supplier/customer/risk_event 满足
     条件），不硬编码具体 id 字面量；发现动作全部收在模块级 autouse fixture，跑在任何写用例
     之前一次性完成，避免后续写用例改写的数据被发现型查询误捕获（写/读用例锚点互不相交）。
"""
from __future__ import annotations

import json
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from agent.mcp_server import MASK
from apps.api.main import REPO_ROOT, app, get_db_path
from pipeline.ontology_models import MODEL_BY_TYPE
from pipeline.ontology_runtime import build_role_perms, load_ontology, snake_case

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"


# ═══════════════════════════════════════════════════════════════════════════
# fixtures：临时库 + TestClient + 一次性发现的锚点
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def tmp_db_path(tmp_path_factory):
    assert REPO_DB.exists(), f"{REPO_DB} 不存在——需先跑一遍 datagen/build_ontology/seed_demo_ops"
    dest = tmp_path_factory.mktemp("api_test_db") / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)
    return str(dest)


@pytest.fixture(scope="module")
def client(tmp_db_path):
    app.dependency_overrides[get_db_path] = lambda: tmp_db_path
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def con(tmp_db_path):
    c = sqlite3.connect(tmp_db_path)
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def _first_row(con, sql, params=()):
    row = con.execute(sql, params).fetchone()
    assert row is not None, f"测试库缺少满足条件的种子数据：{sql} {params}"
    return row


@pytest.fixture(scope="module", autouse=True)
def seed_anchors(con):
    """先于本模块任何用例执行（module-scoped + autouse，首次被任何测试请求时立即计算并缓存），
    一次性从**尚未被本文件任何写用例改写过**的库现查所需锚点。之后各写用例只消费自己专属的
    索引位（assignable_risks[0]=denied 路径复用、[1]=AssignTask 成功用例、[2]=备用），互不相交，
    与执行顺序无关（denied 路径不产生写副作用，可安全复用/重放）。"""
    anchors = {
        "supplier": dict(_first_row(con, "SELECT * FROM suppliers ORDER BY supplier_id LIMIT 1")),
        "customer": dict(_first_row(
            con, "SELECT * FROM customers WHERE tier IS NOT NULL AND tier != '' "
                 "ORDER BY customer_id LIMIT 1")),
        "risk_with_shipment": dict(_first_row(
            con, "SELECT risk_event_id, shipment_id FROM risk_events "
                 "WHERE shipment_id IS NOT NULL ORDER BY risk_event_id LIMIT 1")),
        "assigned_task": dict(_first_row(
            con, "SELECT * FROM tasks WHERE status='assigned' ORDER BY task_id LIMIT 1")),
    }
    risk_rows = con.execute("""
        SELECT risk_event_id FROM risk_events
        WHERE status='open' AND risk_event_id NOT IN (
            SELECT risk_event_id FROM tasks WHERE status NOT IN ('done','cancelled')
        ) ORDER BY risk_event_id LIMIT 3""").fetchall()
    assert len(risk_rows) >= 3, "测试库需要至少 3 个 open 且无非终态任务的风险事件"
    anchors["assignable_risks"] = [r["risk_event_id"] for r in risk_rows]
    return anchors


# ═══════════════════════════════════════════════════════════════════════════
# GET /ontology —— 结构断言
# ═══════════════════════════════════════════════════════════════════════════
def test_ontology_structure(client):
    resp = client.get("/ontology")
    assert resp.status_code == 200
    data = resp.json()
    onto = load_ontology()  # 现查本体文件本身作对照，不誊抄 34/31 等数字
    assert data["version"] == onto["version"]
    assert len(data["objects"]) == len(onto["objects"])
    assert len(data["links"]) == len(onto["links"])
    assert len(data["actions"]) == len(onto["actions"])

    exposed_expected = {a["name"] for a in onto["actions"] if a.get("exposed_as_tool") is True}
    exposed_got = {a["name"] for a in data["actions"] if a["exposed_as_tool"]}
    assert exposed_got == exposed_expected
    assert data["summary"]["exposed_actions"] == len(exposed_expected)

    frozen_expected = {a["name"] for a in onto["actions"] if a.get("ai_executable") == "frozen"}
    assert data["summary"]["frozen_actions"] == len(frozen_expected)

    assert all(o.get("table") for o in data["objects"]), "每个对象摘要都应带派生表名"
    assert all(o.get("primaryKey") for o in data["objects"])


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type} —— 未知类型 422 / 未知过滤列 422 / 列表+过滤与库真值一致
# ═══════════════════════════════════════════════════════════════════════════
def test_unknown_object_type_422(client):
    assert client.get("/objects/NotARealOntologyType").status_code == 422
    assert client.get("/objects/NotARealOntologyType/whatever-id").status_code == 422


def test_unknown_filter_column_422(client):
    resp = client.get("/objects/RiskEvent", params={"totally_bogus_column_xyz": "x"})
    assert resp.status_code == 422


def test_list_objects_filter_matches_library_truth(client, con, seed_anchors):
    rid = seed_anchors["risk_with_shipment"]["risk_event_id"]
    truth_row = _first_row(con, "SELECT * FROM risk_events WHERE risk_event_id=?", (rid,))
    expected = MODEL_BY_TYPE["RiskEvent"].model_validate(dict(truth_row)).model_dump(mode="json")

    resp = client.get("/objects/RiskEvent", params={"risk_event_id": rid}, headers={"X-Role": "manager"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "RiskEvent"
    assert body["count"] == 1
    assert body["items"] == [expected]


def test_list_objects_default_limit_is_100(client):
    resp = client.get("/objects/RiskEvent")
    assert resp.status_code == 200
    assert resp.json()["limit"] == 100


def test_list_objects_limit_param_caps_result(client):
    resp = client.get("/objects/RiskEvent", params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert body["limit"] == 3
    assert len(body["items"]) <= 3


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type}/{id} —— ① 对象读与库真值一致 + 404
# ═══════════════════════════════════════════════════════════════════════════
def test_get_object_matches_library_truth(client, seed_anchors):
    row = seed_anchors["supplier"]
    sid = row["supplier_id"]
    expected = MODEL_BY_TYPE["Supplier"].model_validate(row).model_dump(mode="json")

    # manager 对 Supplier 全字段可见（uflpa_risk_flag 的 sensitiveFieldRules.visibleTo 含 manager）
    resp = client.get(f"/objects/Supplier/{sid}", headers={"X-Role": "manager"})
    assert resp.status_code == 200
    assert resp.json() == expected


def test_get_object_404_for_missing_id(client):
    resp = client.get("/objects/Supplier/NOT-A-REAL-SUPPLIER-ID")
    assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# ④ 敏感字段对无权角色掩码，对有权角色明文
# ═══════════════════════════════════════════════════════════════════════════
def test_sensitive_field_masked_by_role(client, seed_anchors):
    row = seed_anchors["customer"]
    cid, true_tier = row["customer_id"], row["tier"]

    resp_ops = client.get(f"/objects/Customer/{cid}", headers={"X-Role": "ops"})
    assert resp_ops.status_code == 200
    assert resp_ops.json()["tier"] == MASK, "Customer.tier 对 ops 应掩码（visibleTo=[cs,manager]）"

    resp_cs = client.get(f"/objects/Customer/{cid}", headers={"X-Role": "cs"})
    assert resp_cs.status_code == 200
    assert resp_cs.json()["tier"] == true_tier, "Customer.tier 对 cs 应明文"


# ═══════════════════════════════════════════════════════════════════════════
# GET /objects/{type}/{id}/links/{link} —— traverse 正例 / declared_only 422 / 未知关系 422
# ═══════════════════════════════════════════════════════════════════════════
def test_traverse_link_positive(client, seed_anchors):
    rid = seed_anchors["risk_with_shipment"]["risk_event_id"]
    sid = seed_anchors["risk_with_shipment"]["shipment_id"]
    resp = client.get(f"/objects/RiskEvent/{rid}/links/risk_on_shipment")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["neighbor_ids"] == [sid]


def test_risk_affects_sku_traversable(client, con):
    """V6-裁1（补）：risk_affects_sku 补正式承载列 affected_sku_ids、declared_only 豁免退场——
    本体不再有 declared_only 关系，R14 单一来源事件的 risk_affects_sku 现真实可走（200 正向遍历，
    语义跟随裁决，非削弱）。锚点 id 现查现算不硬编码。"""
    onto = load_ontology()
    assert not [l["linkType"] for l in onto["links"] if l.get("status") == "declared_only"], \
        "V6-裁1 后本体不应再有 declared_only 关系（risk_affects_sku 已补正式承载列 affected_sku_ids）"
    row = _first_row(
        con, "SELECT risk_event_id, affected_sku_ids FROM risk_events "
             "WHERE rule_id='R14' AND affected_sku_ids IS NOT NULL AND affected_sku_ids != '' "
             "AND affected_sku_ids != '[]' ORDER BY risk_event_id LIMIT 1")
    rid, expected_skus = row["risk_event_id"], json.loads(row["affected_sku_ids"])
    resp = client.get(f"/objects/RiskEvent/{rid}/links/risk_affects_sku")
    assert resp.status_code == 200
    body = resp.json()
    assert body["neighbor_ids"] == expected_skus, (body, expected_skus)
    assert body["count"] == len(expected_skus)


def test_unknown_link_type_422(client, seed_anchors):
    rid = seed_anchors["risk_with_shipment"]["risk_event_id"]
    resp = client.get(f"/objects/RiskEvent/{rid}/links/totally_bogus_link_xyz")
    assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════
# POST /actions/{name} —— 冻结区/未知动作 404
# ═══════════════════════════════════════════════════════════════════════════
def test_frozen_and_unknown_actions_404(client):
    onto = load_ontology()
    frozen = [a["name"] for a in onto["actions"] if a.get("ai_executable") == "frozen"]
    assert frozen, "本体应有冻结区动作"
    for name in frozen:
        assert client.post(f"/actions/{name}", json={}).status_code == 404, f"{name} 应 404"
    # PascalCase 与 snake_case 别名两种拼法都应 404（路由生成层压根没注册这些名字）
    for name in frozen:
        assert client.post(f"/actions/{snake_case(name)}", json={}).status_code == 404
    assert client.post("/actions/TotallyUnknownAction", json={}).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# ③ 无权角色 POST 动作 → 403 且 audit_log 出现拒绝记录
# ═══════════════════════════════════════════════════════════════════════════
def test_post_action_unauthorized_role_403_with_audit(client, con, seed_anchors):
    onto = load_ontology()
    allowed = build_role_perms(onto)["AssignTask"]
    denied_role = next(r for r in onto["roles"] if r not in allowed)

    rid = seed_anchors["assignable_risks"][0]
    before = con.execute(
        "SELECT count(*) FROM action_log WHERE action='AssignTask' AND result LIKE 'denied%'"
    ).fetchone()[0]
    body = {"risk_event_id": rid, "assignee_role": "ops", "priority": "P2",
            "due_at": "2026-08-10T00:00:00Z"}

    resp = client.post("/actions/AssignTask", json=body, headers={"X-Role": denied_role})
    assert resp.status_code == 403

    after = con.execute(
        "SELECT count(*) FROM action_log WHERE action='AssignTask' AND result LIKE 'denied%'"
    ).fetchone()[0]
    assert after == before + 1, "被拒绝的调用必须走 app.actions 既有的 _denied() 审计路径留痕"

    # snake_case 别名同样路由到同一动作、同样受权限闸门约束、同样留审计（幂等重放，denied 路径无写副作用）
    resp2 = client.post("/actions/assign_task", json=body, headers={"X-Role": denied_role})
    assert resp2.status_code == 403
    after2 = con.execute(
        "SELECT count(*) FROM action_log WHERE action='AssignTask' AND result LIKE 'denied%'"
    ).fetchone()[0]
    assert after2 == after + 1

    # 目标风险全程未被派单改写（拒绝路径不触发状态迁移）
    risk_status = con.execute(
        "SELECT status FROM risk_events WHERE risk_event_id=?", (rid,)).fetchone()["status"]
    assert risk_status == "open"


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ POST 合法提案 → 走 app.actions 原函数，提案/任务表新增行 + 审计留痕
# ═══════════════════════════════════════════════════════════════════════════
def test_post_action_success_assign_task_creates_row_and_audit(client, con, seed_anchors):
    rid = seed_anchors["assignable_risks"][1]
    before = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
    body = {"risk_event_id": rid, "assignee_role": "ops", "priority": "P2",
            "due_at": "2026-08-10T00:00:00Z"}

    resp = client.post("/actions/AssignTask", json=body, headers={"X-Role": "ops"})
    assert resp.status_code == 200
    result = resp.json()
    assert result["ok"] is True
    tid = result["object_id"]

    after = con.execute("SELECT count(*) FROM tasks").fetchone()[0]
    assert after == before + 1, "AssignTask 成功应在 tasks 表新增一行"

    task_row = con.execute("SELECT * FROM tasks WHERE task_id=?", (tid,)).fetchone()
    assert task_row is not None
    assert task_row["risk_event_id"] == rid
    assert task_row["status"] == "assigned"

    risk_row = con.execute(
        "SELECT status FROM risk_events WHERE risk_event_id=?", (rid,)).fetchone()
    assert risk_row["status"] == "acknowledged"

    audit = con.execute(
        "SELECT * FROM action_log WHERE action='AssignTask' AND target_object_id=? AND result='ok'",
        (tid,)).fetchone()
    assert audit is not None, "成功调用必须走 app.actions 既有函数自身的审计路径留痕"
    assert audit["actor"] == "api-caller"
    assert audit["role"] == "ops"


def test_post_action_success_propose_mitigation_updates_task_and_audit(client, con, seed_anchors):
    tid = seed_anchors["assigned_task"]["task_id"]
    body = {"task_id": tid, "proposed_action": "accept_delay",
            "proposal_params": {"reason": "M5 API 测试提案"}}

    resp = client.post("/actions/ProposeMitigation", json=body, headers={"X-Role": "ops"})
    assert resp.status_code == 200
    result = resp.json()
    assert result["ok"] is True
    assert result["object_id"] == tid

    row = con.execute(
        "SELECT status, approval_status, proposed_action FROM tasks WHERE task_id=?", (tid,)
    ).fetchone()
    assert row["status"] == "in_progress"
    assert row["approval_status"] == "pending"
    assert row["proposed_action"] == "accept_delay"

    audit = con.execute(
        "SELECT * FROM action_log WHERE action='ProposeMitigation' AND target_object_id=? "
        "AND result='ok'", (tid,)).fetchone()
    assert audit is not None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
