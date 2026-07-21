#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_collaboration_actions.py —— 协作流人类协调通道验收（V22②）。

POST /collaboration/threads/{coordination_id}/actions/{action_name}：CL1 五个对既有线程的写动作
（催办 record_outreach / 记回应 record_response / 升级 escalate_coordination / 达成 resolve_coordination /
谈崩 mark_dead_ended）的人类 HTTP 通道。

隔离纪律（同 test_waveU.py）：绝不碰真库——data/ontology.sqlite、data/simworld.sqlite 各拷一份
**保留原文件名**的临时副本（原名保证 _infer_world 推断正确），monkeypatch main.DEFAULT_DB_PATH /
SIMWORLD_DB_PATH 指向副本、摘掉 dependency_overrides、清 ONTOLOGY_DB 环境变量——X-World 请求头的
真实解析链路（读头 → 选库 → 写库）被完整覆盖，而非绕开它换路径。

"现查现算对照，勿硬编码具体值"：锚点线程（awaiting / resolved / escalated）运行时从临时库现查发现，
断言期望值当场对库跑 SQL（followup_count 前后差、审计行数差），不誊抄字面量。

覆盖（任务书 §测试）：
  ① sim 世界成功路径：催办 awaiting 线程 → 200，next_action_due 更新 + followup_count++ + audit 落账（actor 透传）。
  ② verify 世界成功路径：同款催办 → 200 + 落库 + 审计（双世界写通道都真实可用）。
  ③ 越权（manager ∉ COORD_PERMS.ManageCoordination）→ 403 + denied 审计（coordination_actions._denied 语义）。
  ④ 非法状态转换：对 resolved（终态）线程催办 → 422 白话（"非法转移"）+ rejected 审计 + 线程未被改写。
  ⑤ 幂等重放：同 Idempotency-Key 双发 → 第二次原样返回首次结果、不重复执行（followup_count 只 +1，
     commands 台账该键恰 1 行）。
  ⑥ 缺 X-Actor → 422（人类通道必须带真实操作人 id，系统不代填）。
  ⑦ 安全红线不变量：OpenCoordination（开新线程，本批不做）→ 404；未知动作 → 404；五动作从不进 AI 工具面
     build_tool_defs；五动作在 /actions（AI 面）维持 404；Pascal / snake 两种拼法都可路由（RecordResponse
     Pascal 成功路径顺带覆盖第二动作）。
  ⑧ compliance 角色成功路径（V23② Daniel 批：合规获协调发起/跟进权，本体 executors 加 compliance）：
     催办（既有线程转移动作）与发起新协调线程（OpenCoordination）均验证 200 + 落库 + 审计 actor=compliance，
     与 ops 路径同构；manager 仍 403（③/⑨ 不变，越权集未被放宽）。
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

import apps.api.main as apimain
from app.coordination_actions import COORD_PERMS
from apps.api.main import REPO_ROOT, app, get_db_path
from pipeline.ontology_runtime import build_tool_defs, load_ontology, snake_case

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
SIM_DB = REPO_ROOT / "data" / "simworld.sqlite"

OPS_ACTOR = "u-ops-us"          # demo 运营演员（ops ∈ ManageCoordination，协调通道 happy path）
MANAGER_ACTOR = "u-manager-us"  # demo 经理演员（manager ∉ ManageCoordination——用于越权 403 路径）
COMPLIANCE_ACTOR = "u-compliance-us"  # demo 合规演员（V23② Daniel 批：compliance ∈ ManageCoordination
# ——本体 executors 加合规后新增的正向路径；DEMO_ROSTER 无此 owner 条目，同 cockpit roleActors.ts
# 占位符命名惯例，actor 只落 action_log 审计 / execute_command 幂等键，不查 DEMO_ROSTER，不影响写入）。
SIM = {"X-World": "sim"}
VERIFY = {"X-World": "verify"}
COORD_ACTIONS = ("RecordOutreach", "RecordResponse", "EscalateCoordination",
                 "ResolveCoordination", "MarkDeadEnded")


# ═══════════════════════════════════════════════════════════════════════════
# fixture：双世界临时副本 + monkeypatch 两库常量（走 X-World 真解析链路，同 test_waveU.py）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def api(tmp_path_factory):
    assert REPO_DB.exists() and SIM_DB.exists(), "需先跑 datagen/build_ontology/seed + sim.backfill"
    d = tmp_path_factory.mktemp("coordactions")
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


def _thread_in_state(con: sqlite3.Connection, state: str, exclude: set[str] | None = None) -> dict:
    """现查一条指定状态的线程做锚点（勿硬编码 id）。exclude 排除已被前面用例改过状态的线程。"""
    ph = ""
    params: list = [state]
    if exclude:
        ph = f" AND coordination_id NOT IN ({','.join('?' * len(exclude))})"
        params += sorted(exclude)
    row = con.execute(
        f"SELECT * FROM coordination_threads WHERE state=?{ph} ORDER BY coordination_id LIMIT 1",
        params).fetchone()
    assert row is not None, f"临时库应有 state={state} 的协调线程（演示数据前置）"
    return dict(row)


def _audit_count(con: sqlite3.Connection, action: str, target: str, result_like: str) -> int:
    return con.execute(
        "SELECT count(*) FROM action_log WHERE action=? AND target_object_id=? AND result LIKE ?",
        (action, target, result_like)).fetchone()[0]


_used_sim_ids: set[str] = set()   # 已被写用例占用的 sim 线程（用例间锚点互不相交）


# ═══════════════════════════════════════════════════════════════════════════
# ① sim 成功路径：催办 awaiting 线程 → 200 + next_action_due 更新 + followup_count++ + 审计落账
# ═══════════════════════════════════════════════════════════════════════════
def test_sim_record_outreach_success_updates_due_and_audits(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting")
    _used_sim_ids.add(t["coordination_id"])
    new_due = "2026-08-20"
    assert t["next_action_due"] != new_due, "锚点线程现有截止日不应恰等于测试新截止日（否则断言失真）"
    before_ok = _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": new_due, "note": "驾驶舱催办（测试）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["object_id"] == t["coordination_id"]

    after = scon.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                         (t["coordination_id"],)).fetchone()
    assert after["next_action_due"] == new_due, "催办必须重设 next_action_due"
    assert after["followup_count"] == t["followup_count"] + 1, "催办必须 followup_count++"
    assert after["state"] == "awaiting", "record_outreach 状态保持 awaiting"

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok") == before_ok + 1, \
        "成功催办必须走 coordination_actions 既有 _log 审计路径落账"
    log = scon.execute(
        "SELECT actor, role FROM action_log WHERE action='RecordOutreach' AND target_object_id=? "
        "AND result='ok' ORDER BY log_id DESC LIMIT 1", (t["coordination_id"],)).fetchone()
    assert log["actor"] == OPS_ACTOR, "审计 actor 必须是真实操作人 id（X-Actor 透传，非常量）"
    assert log["role"] == "ops"


# ═══════════════════════════════════════════════════════════════════════════
# ①b V23② compliance 角色成功路径：合规现为 COORD_PERMS.ManageCoordination 新成员，对既有线程的
# 催办（转移动作）必须像 ops/cs/procurement/finance 一样走通 → 200 + 落库 + 审计（actor=compliance）。
# ═══════════════════════════════════════════════════════════════════════════
def test_sim_record_outreach_compliance_role_success(api):
    client, _vcon, scon = api
    assert "compliance" in COORD_PERMS["ManageCoordination"], \
        "V23② 本体 executors 应已含 compliance（本用例的前提）"
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    _used_sim_ids.add(t["coordination_id"])
    new_due = "2026-08-24"
    assert t["next_action_due"] != new_due
    before_ok = _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": new_due, "note": "合规催办（V23② 测试）"},
        headers={**SIM, "X-Role": "compliance", "X-Actor": COMPLIANCE_ACTOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["object_id"] == t["coordination_id"]

    after = scon.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                         (t["coordination_id"],)).fetchone()
    assert after["next_action_due"] == new_due
    assert after["followup_count"] == t["followup_count"] + 1

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok") == before_ok + 1
    log = scon.execute(
        "SELECT actor, role FROM action_log WHERE action='RecordOutreach' AND target_object_id=? "
        "AND result='ok' ORDER BY log_id DESC LIMIT 1", (t["coordination_id"],)).fetchone()
    assert log["actor"] == COMPLIANCE_ACTOR and log["role"] == "compliance"


# ═══════════════════════════════════════════════════════════════════════════
# ② verify 成功路径：同款催办 → 200 + 落库 + 审计（双世界写通道都真实可用）
# ═══════════════════════════════════════════════════════════════════════════
def test_verify_record_outreach_success(api):
    client, vcon, _scon = api
    t = _thread_in_state(vcon, "awaiting")
    new_due = "2026-08-22"
    assert t["next_action_due"] != new_due

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": new_due, "note": "验证世界催办（测试）"},
        headers={**VERIFY, "X-Role": "cs", "X-Actor": "u-cs-us"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    after = vcon.execute("SELECT next_action_due, followup_count FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["next_action_due"] == new_due
    assert after["followup_count"] == t["followup_count"] + 1
    assert _audit_count(vcon, "RecordOutreach", t["coordination_id"], "ok") >= 1


# ═══════════════════════════════════════════════════════════════════════════
# ③ 越权：manager ∉ COORD_PERMS.ManageCoordination → 403 + denied 审计（app 层 _denied 语义）
# ═══════════════════════════════════════════════════════════════════════════
def test_unauthorized_role_403_with_denied_audit(api):
    client, _vcon, scon = api
    # 前置自检：manager 确实不在协调权限组（若本体变更此集合，测试应当显式失败暴露）
    assert "manager" not in COORD_PERMS["ManageCoordination"], \
        f"前置失效：manager 竟在 ManageCoordination {sorted(COORD_PERMS['ManageCoordination'])} 中"
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    _used_sim_ids.add(t["coordination_id"])
    before_denied = _audit_count(scon, "RecordOutreach", t["coordination_id"], "denied%")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-21", "note": "经理越权催办（应 403）"},
        headers={**SIM, "X-Role": "manager", "X-Actor": MANAGER_ACTOR})
    assert r.status_code == 403, r.text
    assert "权限" in r.json()["detail"] or "无权" in r.json()["detail"]

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "denied%") == before_denied + 1, \
        "越权必须走 coordination_actions 既有 _denied() 审计路径留痕"
    after = scon.execute("SELECT followup_count, next_action_due FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["followup_count"] == t["followup_count"], "越权请求不得改写线程"
    assert after["next_action_due"] == t["next_action_due"]


# ═══════════════════════════════════════════════════════════════════════════
# ④ 非法状态转换：对 resolved（终态）线程催办 → 422 白话 + rejected 审计 + 线程未改写
# ═══════════════════════════════════════════════════════════════════════════
def test_illegal_transition_on_resolved_thread_422(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "resolved")
    before_rej = _audit_count(scon, "RecordOutreach", t["coordination_id"], "rejected%")

    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-25", "note": "对终态线程催办（应被拒）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 422, r.text
    assert "非法转移" in r.json()["detail"], f"应携 app 层状态机白话错误原文：{r.text}"

    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "rejected%") == before_rej + 1, \
        "非法转移必须走 coordination_actions 既有 _fail() 审计路径留痕"
    after = scon.execute("SELECT state, followup_count FROM coordination_threads "
                         "WHERE coordination_id=?", (t["coordination_id"],)).fetchone()
    assert after["state"] == "resolved" and after["followup_count"] == t["followup_count"], \
        "被拒请求不得改写终态线程"


# ═══════════════════════════════════════════════════════════════════════════
# ⑤ 幂等重放：同 Idempotency-Key 双发 → 第二次原样返回首次结果、不重复执行
# ═══════════════════════════════════════════════════════════════════════════
def test_idempotency_key_replay_executes_once(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    _used_sim_ids.add(t["coordination_id"])
    key = "idem-coord-test-0001"
    payload = {"next_action_due": "2026-08-28", "note": "幂等双发（测试）"}
    hdrs = {**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR, "Idempotency-Key": key}
    url = f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach"
    # 现查审计基线（同文件其余用例的一贯手法）：活演示库（V22② 起真实浏览器操作会真写入
    # data/simworld.sqlite）可能已给锚点线程留过历史 RecordOutreach 审计，断言绝对值 1 不安全
    # ——只有断「本次双发只净增 1 条」才与锚点的历史无关（幂等语义本身就是关于净增量，非绝对值）。
    before_ok = _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok")

    r1 = client.post(url, json=payload, headers=hdrs)
    assert r1.status_code == 200, r1.text
    r2 = client.post(url, json=payload, headers=hdrs)
    assert r2.status_code == 200, r2.text
    assert r2.json() == r1.json(), "同幂等键重放必须原样返回首次结果"

    after = scon.execute("SELECT followup_count FROM coordination_threads WHERE coordination_id=?",
                         (t["coordination_id"],)).fetchone()
    assert after["followup_count"] == t["followup_count"] + 1, \
        "幂等键命中重放不得重复执行（followup_count 只允许 +1）"
    assert scon.execute("SELECT count(*) FROM commands WHERE idempotency_key=?",
                        (key,)).fetchone()[0] == 1, "commands 台账同键恰一行（claim-first 占坑）"
    assert _audit_count(scon, "RecordOutreach", t["coordination_id"], "ok") == before_ok + 1, \
        "重放净增恰一条成功审计——重放未二次走动作函数"


# ═══════════════════════════════════════════════════════════════════════════
# ⑥ 缺 X-Actor → 422（人类通道必须带真实操作人 id）
# ═══════════════════════════════════════════════════════════════════════════
def test_missing_x_actor_422(api):
    client, _vcon, scon = api
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)   # 只读锚点，不改写不占用
    r = client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/record_outreach",
        json={"next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "ops"})
    assert r.status_code == 422, r.text
    assert "X-Actor" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# ⑦ 安全红线不变量：白名单边界（OpenCoordination/未知动作 404）+ AI 面零暴露 + 双拼法路由
# ═══════════════════════════════════════════════════════════════════════════
def test_channel_whitelist_and_ai_face_invariants(api):
    client, _vcon, scon = api
    onto = load_ontology()
    coord_snake = {snake_case(n) for n in COORD_ACTIONS}

    # (a) OpenCoordination 走独立 POST /threads 路由，不在本转移通道 → 此路由 404，白话说明去处；未知动作同样 404
    t = _thread_in_state(scon, "awaiting", exclude=_used_sim_ids)
    for bad in ("OpenCoordination", "open_coordination", "no_such_action"):
        r = client.post(f"/collaboration/threads/{t['coordination_id']}/actions/{bad}",
                        json={}, headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
        assert r.status_code == 404, f"{bad} 应 404（白名单外，路由生成层就不认），当前 {r.status_code}"
    assert "OpenCoordination" in client.post(
        f"/collaboration/threads/{t['coordination_id']}/actions/open_coordination",
        json={}, headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR}).json()["detail"], \
        "404 文案应如实说明 OpenCoordination 未接、去处在哪"

    # (b) AI 面零暴露：五动作 ∩ build_tool_defs 工具名 == ∅（协调写动作从不进 MCP/TOOL_DEFS）
    tool_names = {td["name"] for td in build_tool_defs(onto)}
    leak = tool_names & coord_snake
    assert leak == set(), f"协调写动作泄漏进 AI 工具面 build_tool_defs：{sorted(leak)}"

    # (c) 五动作在 /actions（AI 面平行验证通道）维持 404——Pascal 与 snake 两种拼法都 404
    for name in COORD_ACTIONS:
        assert client.post(f"/actions/{name}", json={}).status_code == 404, f"/actions/{name} 应 404"
        assert client.post(f"/actions/{snake_case(name)}", json={}).status_code == 404

    # (d) 本通道 Pascal 拼法同样可路由：RecordResponse（Pascal）记回应 escalated 线程 → responded
    #     （顺带覆盖第二动作的成功路径与状态机 escalated→responded 转移）
    te = _thread_in_state(scon, "escalated", exclude=_used_sim_ids)
    _used_sim_ids.add(te["coordination_id"])
    r = client.post(
        f"/collaboration/threads/{te['coordination_id']}/actions/RecordResponse",
        json={"last_response": "工厂确认可改期到 8/30 出货（测试）"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 200, r.text
    after = scon.execute("SELECT state, last_response FROM coordination_threads "
                         "WHERE coordination_id=?", (te["coordination_id"],)).fetchone()
    assert after["state"] == "responded"
    assert "8/30" in (after["last_response"] or "")


# ═══════════════════════════════════════════════════════════════════════════
# ⑧ 发起新协调线程 POST /collaboration/threads（V22② 余量清偿，独立于 {id}/actions 路由）
#   成功创建 + audit(OpenCoordination ok) + commands 台账 + 幂等重放不重复建 / 越权 403 / task 不存在
#   422 白话 / counterparty 枚举 422 白话 / 缺 X-Actor 422。写入原样走 open_coordination（语义一行不改）。
# ═══════════════════════════════════════════════════════════════════════════
def _a_task(con: sqlite3.Connection) -> dict:
    """现查一条 Task 做协调锚点（勿硬编码 id）。"""
    row = con.execute("SELECT task_id, risk_event_id FROM tasks ORDER BY task_id LIMIT 1").fetchone()
    assert row is not None, "临时库应有 Task（演示数据前置）"
    return dict(row)


def test_open_coordination_success_creates_thread_and_audits(api):
    client, _vcon, scon = api
    task = _a_task(scon)
    n_before = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]
    cmd_before = scon.execute(
        "SELECT count(*) FROM commands WHERE action='OpenCoordination'").fetchone()[0]

    r = client.post(
        "/collaboration/threads",
        json={"task_id": task["task_id"], "counterparty_type": "supplier",
              "counterparty_ref": "SUP·工厂A", "ask": "工厂确认改期后交期（测试）",
              "next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["object_id"], body
    cid = body["object_id"]

    # 线程真落库（+1）、状态 awaiting、锚到该 Task、owner=发起人（缺省兜底）
    row = scon.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                       (cid,)).fetchone()
    assert row is not None
    assert row["task_id"] == task["task_id"] and row["state"] == "awaiting"
    assert row["counterparty_type"] == "supplier" and row["owner"] == OPS_ACTOR
    assert scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0] == n_before + 1

    # audit（OpenCoordination ok，actor=真实操作人）+ commands 台账（经写总线）
    assert _audit_count(scon, "OpenCoordination", cid, "ok") == 1
    log = scon.execute("SELECT actor FROM action_log WHERE action='OpenCoordination' "
                       "AND target_object_id=? AND result='ok'", (cid,)).fetchone()
    assert log["actor"] == OPS_ACTOR, "审计 actor 必须是 X-Actor 透传的真实操作人"
    assert scon.execute("SELECT count(*) FROM commands WHERE action='OpenCoordination'"
                        ).fetchone()[0] == cmd_before + 1, "发起协调必须经 execute_command 落 commands 台账"


def test_open_coordination_compliance_role_success(api):
    """V23② compliance 角色成功用例：合规现为 COORD_PERMS.ManageCoordination 新成员，发起协调必须
    像 ops 一样走通 → 200 + 落库 + owner 缺省=发起人 + 审计（actor=compliance），与 ops 路径同构。"""
    client, _vcon, scon = api
    task = _a_task(scon)
    n_before = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]

    r = client.post(
        "/collaboration/threads",
        json={"task_id": task["task_id"], "counterparty_type": "customs_broker",
              "counterparty_ref": "CB·报关行A", "ask": "补交清关合规文件（V23② 测试）",
              "next_action_due": "2026-08-31"},
        headers={**SIM, "X-Role": "compliance", "X-Actor": COMPLIANCE_ACTOR})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["object_id"], body
    cid = body["object_id"]

    row = scon.execute("SELECT * FROM coordination_threads WHERE coordination_id=?",
                       (cid,)).fetchone()
    assert row is not None and row["state"] == "awaiting" and row["owner"] == COMPLIANCE_ACTOR
    assert scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0] == n_before + 1

    assert _audit_count(scon, "OpenCoordination", cid, "ok") == 1
    log = scon.execute("SELECT actor, role FROM action_log WHERE action='OpenCoordination' "
                       "AND target_object_id=? AND result='ok'", (cid,)).fetchone()
    assert log["actor"] == COMPLIANCE_ACTOR and log["role"] == "compliance"


def test_open_coordination_idempotent_replay_creates_once(api):
    client, _vcon, scon = api
    task = _a_task(scon)
    key = "open-coord-idem-test-1"
    payload = {"task_id": task["task_id"], "counterparty_type": "forwarder",
               "counterparty_ref": "FWD·货代B", "ask": "货代确认改配船期（幂等测试）",
               "next_action_due": "2026-09-01"}
    hdrs = {**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR, "Idempotency-Key": key}
    n_before = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]

    r1 = client.post("/collaboration/threads", json=payload, headers=hdrs)
    r2 = client.post("/collaboration/threads", json=payload, headers=hdrs)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["object_id"] == r2.json()["object_id"], "同幂等键重放必须返回同一线程号"
    assert scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0] == n_before + 1, \
        "同幂等键重放不得重复建线程（恰建一次）"


def test_open_coordination_unauthorized_manager_403_with_denied_audit(api):
    client, _vcon, scon = api
    task = _a_task(scon)
    assert "manager" not in COORD_PERMS["ManageCoordination"]
    n_before = scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0]

    r = client.post(
        "/collaboration/threads",
        json={"task_id": task["task_id"], "counterparty_type": "supplier",
              "counterparty_ref": "x", "ask": "经理越权发起（应 403）",
              "next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "manager", "X-Actor": MANAGER_ACTOR})
    assert r.status_code == 403, r.text
    # 越权不得建线程；denied 审计留痕（app 层 _denied，target=task_id）
    assert scon.execute("SELECT count(*) FROM coordination_threads").fetchone()[0] == n_before, \
        "越权发起不得建线程"
    assert _audit_count(scon, "OpenCoordination", task["task_id"], "denied%") >= 1, \
        "越权必须走 _denied 审计留痕"


def test_open_coordination_missing_task_422_plainhint(api):
    client, _vcon, _scon = api
    r = client.post(
        "/collaboration/threads",
        json={"task_id": "TSK-DOES-NOT-EXIST", "counterparty_type": "supplier",
              "counterparty_ref": "x", "ask": "锚到不存在的任务（应拒）",
              "next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 422, r.text
    assert "TSK-DOES-NOT-EXIST" in r.json()["detail"] and "不存在" in r.json()["detail"], \
        "task 不存在应白话报错并点名缺失的锚点"


def test_open_coordination_bad_counterparty_type_422_plainhint(api):
    client, _vcon, _scon = api
    task = _a_task(_vcon)  # verify 世界任取一条 Task
    r = client.post(
        "/collaboration/threads",
        json={"task_id": task["task_id"], "counterparty_type": "not-a-type",
              "counterparty_ref": "x", "ask": "非法对手方类型（应拒）",
              "next_action_due": "2026-08-30"},
        headers={**VERIFY, "X-Role": "ops", "X-Actor": OPS_ACTOR})
    assert r.status_code == 422, r.text
    assert "counterparty_type" in r.json()["detail"], "枚举校验应白话点名字段"


def test_open_coordination_missing_x_actor_422(api):
    client, _vcon, scon = api
    task = _a_task(scon)
    r = client.post(
        "/collaboration/threads",
        json={"task_id": task["task_id"], "counterparty_type": "supplier",
              "counterparty_ref": "x", "ask": "缺 X-Actor（应拒）",
              "next_action_due": "2026-08-30"},
        headers={**SIM, "X-Role": "ops"})
    assert r.status_code == 422, r.text
    assert "X-Actor" in r.json()["detail"]


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
