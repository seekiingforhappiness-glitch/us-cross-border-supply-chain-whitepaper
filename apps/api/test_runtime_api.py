#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""apps/api/test_runtime_api.py —— ① runtime 治理 API 验收用例（V19，spec §①）。

隔离纪律（同 apps/api/test_command_bus.py）：每个用例一份 data/ontology.sqlite 的 **function 级临时副本**，
经 app.dependency_overrides 把 get_db_path 换成临时路径——**绝不写真库**（业务双库 md5 不因测试改变）。
POST /runtime/runs 会真的驱动一趟确定性剧本（think→get_risk→assign_task→propose→wait），在临时副本上
建任务/落黑匣子两表（telemetry，digest 豁免）；think 步一律 llm=off（零出境，遵守限流纪律）。

覆盖（任务书 §① + 生产级硬门）：
  · 诚实空态：本世界还没跑过 run → GET 列表返回空（非 404/500）。
  · happy：start→waiting_approval + run_id；detail 全量白话时间线 + 预算余量；list 含该 run + 游标翻页。
  · 冻结区不可达（红线）：驱动出的 steps 里无一 approve/close/quote 工具——API 不新开通往冻结区的路。
  · 权限双层 + gate：kill manager 专属（后端独立鉴权 403）+ X-Actor 必填（422）+ 幂等 + 不存在 404 + 审计留痕。
  · 等审批三分支透传：resume pending（仍在等）/ approved（写后复读→done）。
  · 错误信封一致：4xx 携既有 detail + 新增 error 对象（同 §二 契约硬化）。
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.actions as A
from apps.api.main import AS_OF, REPO_ROOT, app, get_db_path

REPO_DB = REPO_ROOT / "data" / "ontology.sqlite"
MANAGER_ROLE_ACTOR = "u-manager-us"     # 有审批权、且 ≠ AI actor（满足 maker-checker，供 resume-approved 用）


# ═══════════════════════════════════════════════════════════════════════════
# fixtures：function 级临时库 + TestClient（每个用例独立、互不相交）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def rt_env(tmp_path):
    assert REPO_DB.exists(), f"{REPO_DB} 不存在——需先跑 datagen/build_ontology/seed_demo_ops"
    dest = tmp_path / "ontology.sqlite"
    shutil.copy(REPO_DB, dest)          # 纯字节读源库 + 写临时副本，绝不触碰真库
    db = str(dest)
    app.dependency_overrides[get_db_path] = lambda: db
    with TestClient(app) as c:
        yield c, db
    app.dependency_overrides.pop(get_db_path, None)


def _assignable_risks(db_path, n=1):
    """取 n 个 open 且无非终态任务的风险事件号（可干净派单，同 test_command_bus._one_assignable_risk）。"""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        """SELECT risk_event_id FROM risk_events WHERE status='open' AND risk_event_id NOT IN (
             SELECT risk_event_id FROM tasks WHERE status NOT IN ('done','cancelled'))
           ORDER BY risk_event_id LIMIT ?""", (n,)).fetchall()
    con.close()
    assert len(rows) >= n, f"测试库需要至少 {n} 个可派单风险事件"
    return [r[0] for r in rows]


def _start(client, risk_id, role="ops", actor="u-ops-us"):
    return client.post("/runtime/runs", json={"goal_risk_id": risk_id},
                       headers={"X-Role": role, "X-Actor": actor})


# ═══════════════════════════════════════════════════════════════════════════
# 诚实空态
# ═══════════════════════════════════════════════════════════════════════════
def test_list_empty_honest_state(rt_env):
    client, _ = rt_env
    r = client.get("/runtime/runs")
    assert r.status_code == 200
    b = r.json()
    assert b["count"] == 0 and b["items"] == [] and "note" in b, "还没跑过 run 应诚实空态（非 404/500）"
    assert b["world"] == "verification"


# ═══════════════════════════════════════════════════════════════════════════
# 启动 gate：X-Actor 必填 / goal_risk_id 必填
# ═══════════════════════════════════════════════════════════════════════════
def test_start_missing_x_actor_422(rt_env):
    client, db = rt_env
    rid = _assignable_risks(db)[0]
    r = client.post("/runtime/runs", json={"goal_risk_id": rid}, headers={"X-Role": "ops"})
    assert r.status_code == 422
    assert "X-Actor" in r.json()["detail"]
    assert r.json()["error"]["code"] == "unprocessable_entity"


def test_start_missing_goal_risk_id_422(rt_env):
    client, _ = rt_env
    r = client.post("/runtime/runs", json={}, headers={"X-Role": "ops", "X-Actor": "u-ops-us"})
    assert r.status_code == 422
    assert "goal_risk_id" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# happy：start → waiting_approval；detail 白话时间线 + 预算余量；list 含该 run；控制面审计留痕
# ═══════════════════════════════════════════════════════════════════════════
def test_start_detail_list_happy(rt_env):
    client, db = rt_env
    rid = _assignable_risks(db)[0]
    r = _start(client, rid)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "waiting_approval", out
    assert out["status_label"] and out["llm_mode"] == "off" and out["world"] == "verification"
    run_id, task_id = out["run_id"], out.get("task_id")
    assert run_id and task_id

    # 详情：白话时间线全量 + 预算余量
    d = client.get(f"/runtime/runs/{run_id}")
    assert d.status_code == 200
    dj = d.json()
    assert dj["status"] == "waiting_approval"
    kinds = [s["kind"] for s in dj["steps"]]
    assert kinds == ["think", "tool", "command", "command", "wait"], kinds
    assert all(s["kind_label"] for s in dj["steps"]), "每步须带白话 kind 标签"
    bud = dj["budget"]
    assert bud["steps_used"] == 5 and bud["steps_remaining"] == bud["max_steps"] - 5
    assert bud["tool_calls_used"] == 3 and bud["seconds_remaining"] <= bud["max_seconds"]

    # 列表：含该 run
    lst = client.get("/runtime/runs").json()
    assert lst["count"] >= 1 and any(it["run_id"] == run_id for it in lst["items"])
    item = next(it for it in lst["items"] if it["run_id"] == run_id)
    assert item["status"] == "waiting_approval" and item["budget"]["max_steps"] == bud["max_steps"]

    # 控制面审计留痕（谁启动了哪个 run），trace_id 为 NULL（人类控制面）
    con = sqlite3.connect(db)
    row = con.execute("SELECT actor, role, trace_id FROM action_log WHERE action='StartAgentRun' "
                      "AND target_object_id=?", (run_id,)).fetchone()
    con.close()
    assert row == ("u-ops-us", "ops", None), "start 必须落控制面审计（trace_id NULL）"


# ═══════════════════════════════════════════════════════════════════════════
# 冻结区不可达（红线）：驱动出的 steps 里无一 approve/close/quote 工具
# ═══════════════════════════════════════════════════════════════════════════
def test_started_run_never_touches_frozen_actions(rt_env):
    client, db = rt_env
    rid = _assignable_risks(db)[0]
    run_id = _start(client, rid).json()["run_id"]
    steps = client.get(f"/runtime/runs/{run_id}").json()["steps"]
    tools = [s["payload"].get("tool", "") for s in steps if s["kind"] in ("tool", "command")]
    assert tools, "应有工具/命令步"
    for t in tools:
        assert not any(w in (t or "") for w in ("approve", "close", "quote")), \
            f"runtime API 驱动出的工具 {t} 触及冻结区语义——红线：新端点绝不可达审批执行"


# ═══════════════════════════════════════════════════════════════════════════
# 游标翻页（同 /objects 惯例）：逐页并集 = 全量、无重漏
# ═══════════════════════════════════════════════════════════════════════════
def test_list_cursor_pagination_union_complete(rt_env):
    client, db = rt_env
    risks = _assignable_risks(db, 3)
    started = {_start(client, rid).json()["run_id"] for rid in risks}
    assert len(started) == 3
    collected, cursor, pages = [], "", 0
    while True:
        pages += 1
        assert pages < 50, "翻页未收敛（疑似游标死循环）"
        j = client.get("/runtime/runs", params={"cursor": cursor, "limit": 2}).json()
        assert "next_cursor" in j, "游标模式响应应带 next_cursor"
        collected += [it["run_id"] for it in j["items"]]
        if j["next_cursor"] is None:
            break
        cursor = j["next_cursor"]
    assert len(collected) == len(set(collected)), "游标翻页不应重复"
    assert started <= set(collected), "游标翻页并集应含全部已启动 run，无遗漏"


def test_list_no_cursor_shape_has_no_next_cursor(rt_env):
    client, db = rt_env
    _start(client, _assignable_risks(db)[0])
    j = client.get("/runtime/runs", params={"limit": 5}).json()
    assert set(j.keys()) == {"world", "count", "limit", "items"}, \
        "无游标响应不应出现 next_cursor（与 /objects 无游标分支同构）"


# ═══════════════════════════════════════════════════════════════════════════
# 等审批三分支透传：pending（仍在等）/ approved（写后复读→done）
# ═══════════════════════════════════════════════════════════════════════════
def test_resume_pending_passthrough(rt_env):
    client, db = rt_env
    run_id = _start(client, _assignable_risks(db)[0]).json()["run_id"]
    r = client.post(f"/runtime/runs/{run_id}/resume", headers={"X-Role": "ops", "X-Actor": "u-ops-us"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "waiting_approval" and b.get("approval_status") == "pending", \
        "提案仍待批：resume 应如实返回 pending、不推进"


def test_resume_after_approval_verifies_done(rt_env):
    client, db = rt_env
    out = _start(client, _assignable_risks(db)[0]).json()
    run_id, task_id = out["run_id"], out["task_id"]
    # 扮演人类经理，在临时副本上直接审批 AI 的提案（提案人=AI actor ≠ 经理，满足 maker-checker）
    con = A.connect(db)
    ap = A.approve_mitigation(con, task_id, "approved", "测试人工审批", actor=MANAGER_ROLE_ACTOR,
                              role="manager", as_of=AS_OF)
    con.close()
    assert ap["ok"] is True, ap
    # 批完 resume → verify 写后复读 → done
    r = client.post(f"/runtime/runs/{run_id}/resume", headers={"X-Role": "ops", "X-Actor": "u-ops-us"})
    assert r.status_code == 200
    b = r.json()
    assert b["status"] == "done", b
    # 详情里出现 verify 步（写后复读核实）
    steps = client.get(f"/runtime/runs/{run_id}").json()["steps"]
    assert any(s["kind"] == "verify" for s in steps), "批准后应有写后复读 verify 步"


def test_resume_nonexistent_404(rt_env):
    client, _ = rt_env
    r = client.post("/runtime/runs/RUN-NOPE/resume", headers={"X-Role": "ops", "X-Actor": "u-ops-us"})
    assert r.status_code == 404


def test_resume_missing_x_actor_422(rt_env):
    client, db = rt_env
    run_id = _start(client, _assignable_risks(db)[0]).json()["run_id"]
    r = client.post(f"/runtime/runs/{run_id}/resume", headers={"X-Role": "ops"})
    assert r.status_code == 422 and "X-Actor" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════
# kill：manager 专属（后端独立鉴权）+ X-Actor 必填 + 幂等 + 不存在 404 + 审计留痕
# ═══════════════════════════════════════════════════════════════════════════
def test_kill_by_manager_idempotent_and_audited(rt_env):
    client, db = rt_env
    run_id = _start(client, _assignable_risks(db)[0]).json()["run_id"]
    h = {"X-Role": "manager", "X-Actor": MANAGER_ROLE_ACTOR}
    r1 = client.post(f"/runtime/runs/{run_id}/kill", headers=h)
    assert r1.status_code == 200 and r1.json()["status"] == "killed" and r1.json()["killed"] == 1
    # 幂等：重复 kill 返回同结果
    r2 = client.post(f"/runtime/runs/{run_id}/kill", headers=h)
    assert r2.status_code == 200 and r2.json()["status"] == "killed" and r2.json()["killed"] == 1
    # 被杀后 detail 反映 killed
    assert client.get(f"/runtime/runs/{run_id}").json()["status"] == "killed"
    # 审计留痕：至少一条 KillAgentRun（谁急停了哪个 run）
    con = sqlite3.connect(db)
    n = con.execute("SELECT count(*) FROM action_log WHERE action='KillAgentRun' "
                    "AND target_object_id=? AND actor=?", (run_id, MANAGER_ROLE_ACTOR)).fetchone()[0]
    con.close()
    assert n >= 1, "kill 必须落控制面审计留痕"


def test_kill_non_manager_403(rt_env):
    client, db = rt_env
    run_id = _start(client, _assignable_risks(db)[0]).json()["run_id"]
    r = client.post(f"/runtime/runs/{run_id}/kill", headers={"X-Role": "ops", "X-Actor": "u-ops-us"})
    assert r.status_code == 403, "kill 是 manager 专属（后端独立鉴权，不靠前端灰态）"
    assert r.json()["error"]["code"] == "forbidden"
    # 未被杀：run 仍在等审批（403 不应有副作用）
    assert client.get(f"/runtime/runs/{run_id}").json()["status"] == "waiting_approval"


def test_kill_missing_x_actor_422(rt_env):
    client, db = rt_env
    run_id = _start(client, _assignable_risks(db)[0]).json()["run_id"]
    r = client.post(f"/runtime/runs/{run_id}/kill", headers={"X-Role": "manager"})
    assert r.status_code == 422 and "X-Actor" in r.json()["detail"]


def test_kill_nonexistent_404(rt_env):
    client, _ = rt_env
    r = client.post("/runtime/runs/RUN-NOPE/kill",
                    headers={"X-Role": "manager", "X-Actor": MANAGER_ROLE_ACTOR})
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# detail 不存在 → 404；错误信封一致（既有 detail + 新增 error 对象）
# ═══════════════════════════════════════════════════════════════════════════
def test_detail_nonexistent_404_with_error_envelope(rt_env):
    client, _ = rt_env
    r = client.get("/runtime/runs/RUN-NOPE")
    assert r.status_code == 404
    b = r.json()
    assert isinstance(b.get("detail"), str) and b["error"]["code"] == "not_found"
    assert b["error"]["message"] == b["detail"], "error.message 即原白话 detail（契约硬化一致）"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
