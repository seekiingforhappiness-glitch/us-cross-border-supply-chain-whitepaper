#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""波2-2c Agent runtime 堵门测试（全部临时副本库，data/ 真库零写入）：python3 -m agent.test_runtime

覆盖（任务书回归清单逐条）：状态迁移合法性 / 预算停（步数·时间·工具次数）/ loop guard /
崩溃恢复幂等（同 key 重放拿首次结果）/ kill / 冻结区静态断言 / 等审批三分支（pending·approved·rejected）
/ TELEMETRY 登记守护 / 表 CHECK 约束。think 步一律 llm='off'（确定性降级模式，绝不出境）。
注：本测试文件 import app.actions.approve_mitigation 是**扮演人类经理**做审批（maker-checker 的人侧）；
冻结区静态断言的对象是 agent/runtime.py 源码，不是测试文件。
"""
import ast
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from agent.runtime import (ALLOWED_TRANSITIONS, IllegalTransition, Runtime, TERMINAL_STATUSES,
                           ensure_runtime_tables, kill_run, list_runs)
from agent.tools import ALL_WRITE_PERM, FORBIDDEN_TOOLS
from app.actions import approve_mitigation  # 测试扮演人类经理（审批永远在人，runtime 不可达）

REPO = Path(__file__).resolve().parent.parent
RUNTIME_SRC = REPO / "agent" / "runtime.py"
FAILS = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def fresh_db() -> str:
    tmp = Path(tempfile.mkdtemp()) / "runtime.sqlite"
    shutil.copy(REPO / "data" / "ontology.sqlite", tmp)
    return str(tmp)


def new_rt(db, **kw) -> Runtime:
    kw.setdefault("llm", "off")   # 测试绝不出境（任务书：不要跑真 LLM）
    return Runtime(db_path=db, **kw)


def human_approve(rt, task_id, decision):
    return approve_mitigation(rt.con, task_id, decision, "测试人工审批", actor="mgr-demo",
                              role="manager", as_of=rt.session.as_of)


def nonterminal_tasks(con, risk_id):
    return con.execute("SELECT count(*) FROM tasks WHERE risk_event_id=? "
                       "AND status NOT IN ('done','cancelled')", (risk_id,)).fetchone()[0]


# ─── T1 状态迁移合法性 ───
def t1_transitions():
    print("① 状态迁移合法性")
    db = fresh_db()
    rt = new_rt(db)

    seq = iter(range(100))

    def raw_run(status="created"):
        rid = f"RUN-T1-{status}-{next(seq)}"
        rt.con.execute("INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,0,NULL)",
                       (rid, "处置 RSK-0001", status, "ops", "{}", "t", "t"))
        rt.con.commit()
        return rid

    rid = raw_run("created")
    legal_ok = True
    for a, b in [("created", "running"), ("running", "waiting_approval"),
                 ("waiting_approval", "running"), ("running", "done")]:
        try:
            rt._transition(rid, b)
        except IllegalTransition as exc:
            legal_ok = False
            check(f"合法链 {a}→{b}", False, str(exc))
    check("合法链 created→running→waiting_approval→running→done 全通", legal_ok)
    illegal_raised = 0
    for rid2, target in [(rid, "running"),                     # done→running（终态无出边）
                         (raw_run("created"), "waiting_approval"),  # created 不可直接等审批
                         (raw_run("waiting_approval"), "done")]:    # 等审批不可直接 done（须经 running）
        try:
            rt._transition(rid2, target)
        except IllegalTransition:
            illegal_raised += 1
    check("非法迁移（done→running / created→waiting_approval / waiting_approval→done）全部抛错",
          illegal_raised == 3, f"抛错 {illegal_raised}/3")
    for st in TERMINAL_STATUSES:
        check(f"终态 {st} 出边为空", ALLOWED_TRANSITIONS[st] == set())


# ─── T2 硬预算停 ───
def t2_budget():
    print("② 硬预算（步数 / 时间 / 工具次数）")
    db = fresh_db()
    rt = new_rt(db, budget={"max_steps": 1})
    out = rt.start("处置 RSK-0005")
    run = rt._load(out["run_id"])
    check("max_steps=1 → budget_exhausted", out["status"] == "budget_exhausted", str(out))
    check("步数预算白话 summary 落库", "步数预算耗尽" in (run["summary"] or ""), run["summary"])
    check("预算停时只执行了 1 步", len(rt._history(out["run_id"])) == 1)
    check("预算停前未产生任何写（无任务）", nonterminal_tasks(rt.con, "RSK-0005") == 0)

    rt2 = new_rt(db, budget={"max_seconds": 0})
    out2 = rt2.start("处置 RSK-0005")
    run2 = rt2._load(out2["run_id"])
    check("max_seconds=0 → timeout（时间预算独立成态）", out2["status"] == "timeout", str(out2))
    check("时间预算白话 summary 落库", "时间预算耗尽" in (run2["summary"] or ""), run2["summary"])
    check("timeout 时零步执行", len(rt2._history(out2["run_id"])) == 0)

    rt3 = new_rt(db, budget={"max_tool_calls": 1})
    out3 = rt3.start("处置 RSK-0005")
    hist3 = rt3._history(out3["run_id"])
    check("max_tool_calls=1 → budget_exhausted（写命令未执行）",
          out3["status"] == "budget_exhausted", str(out3))
    check("工具预算停在 think+get_risk 之后（2 步，无 command 步）",
          [s["kind"] for s in hist3] == ["think", "tool"], str([s["kind"] for s in hist3]))
    check("工具预算停前未派单", nonterminal_tasks(rt3.con, "RSK-0005") == 0)


# ─── T3 loop guard 防打转 ───
def t3_loop_guard():
    print("③ loop guard（连续 3 次同工具同参数自动停）")
    db = fresh_db()
    loop_plan = lambda run, history: {"kind": "tool", "tool": "get_risk",  # noqa: E731
                                      "args": {"risk_event_id": "RSK-0005"}}
    rt = new_rt(db, _plan_override=loop_plan)
    out = rt.start("处置 RSK-0005")
    hist = rt._history(out["run_id"])
    run = rt._load(out["run_id"])
    check("打转 run 以 failed 收场", out["status"] == "failed", str(out))
    check("恰好执行 3 次后停（N=3）", len(hist) == 3 and all(s["kind"] == "tool" for s in hist),
          str([(s["kind"], s["payload"].get("tool")) for s in hist]))
    check("防打转白话 summary 落库", "防打转护栏触发" in (run["summary"] or ""), run["summary"])


# ─── T4 崩溃恢复幂等（同 key 重放拿首次结果，恰一次） ───
class SimulatedCrash(RuntimeError):
    pass


def t4_crash_resume():
    print("④ 崩溃恢复幂等（总线已写、黑匣子未记 的窗口）")
    db = fresh_db()
    risk = "RSK-0006"   # 种子库中 open 且无既有任务的干净风险（RSK-0003 带种子任务，不适用）

    def crash_on_assign(kind, payload, step_no):
        if kind == "command" and payload.get("tool") == "assign_task":
            raise SimulatedCrash("模拟崩溃：assign 已过总线、步骤行未落账")

    rt = new_rt(db, _crash_hook=crash_on_assign)
    crashed = False
    run_id = None
    try:
        rt.start(f"处置 {risk}")
    except SimulatedCrash:
        crashed = True
        run_id = rt.con.execute("SELECT run_id FROM agent_runs ORDER BY created_at DESC LIMIT 1"
                                ).fetchone()[0]
    check("崩溃如实炸出（不吞错）", crashed)
    task_before = rt.con.execute("SELECT task_id FROM tasks WHERE risk_event_id=? "
                                 "AND status NOT IN ('done','cancelled')", (risk,)).fetchone()[0]
    steps_before = len(rt._history(run_id))
    first_cmd = rt.con.execute(
        "SELECT result_status, object_id FROM commands WHERE idempotency_key=?",
        (f"run:{run_id}:3",)).fetchone()
    check("崩溃现场：总线台账已 ok（任务已建）但 assign 步骤行缺失",
          task_before is not None and steps_before == 2
          and first_cmd is not None and first_cmd["result_status"] == "ok"
          and first_cmd["object_id"] == task_before,
          f"task={task_before}, steps={steps_before}, cmd={dict(first_cmd) if first_cmd else None}")

    rt2 = new_rt(db)   # 新进程视角，无 crash hook
    out = rt2.resume(run_id)
    hist = rt2._history(run_id)
    assign = next(s for s in hist if s["kind"] == "command"
                  and s["payload"]["tool"] == "assign_task")
    key = f"run:{run_id}:3"
    cmd_rows = rt2.con.execute(
        "SELECT count(*), max(result_status) FROM commands WHERE idempotency_key=?",
        (key,)).fetchone()
    check("resume 后走到等审批", out["status"] == "waiting_approval", str(out))
    check("重放步骤号与幂等键复现（step 3, run:{run_id}:3）",
          assign["step_no"] == 3 and assign["payload"]["idempotency_key"] == key)
    check("同 key 只有一条命令台账（无第二次执行）", cmd_rows[0] == 1 and cmd_rows[1] == "ok",
          str(cmd_rows))
    check("重放拿到首次结果（object_id=崩溃前已建任务，且 ok=True 而非「已存在任务」拒绝）",
          assign["result"].get("ok") is True and assign["result"].get("object_id") == task_before,
          str(assign["result"]))
    check("恰一次：该风险仍只有 1 个非终态任务", nonterminal_tasks(rt2.con, risk) == 1)


# ─── T5 kill switch ───
def t5_kill():
    print("⑤ kill switch（急停留痕，resume/step 前检查）")
    db = fresh_db()
    rt = new_rt(db)
    out = rt.start("处置 RSK-0002")
    run_id = out["run_id"]
    check("前置：run 停在等审批", out["status"] == "waiting_approval", str(out))
    k = kill_run(db, run_id)
    check("kill 成功且状态→killed", k["ok"] and k["status"] == "killed", str(k))
    out2 = rt.resume(run_id)
    check("kill 后 resume 不再推进（留痕可查）", out2["status"] == "killed", str(out2))
    steps = len(rt._history(run_id))
    k2 = kill_run(db, run_id)
    check("重复 kill 幂等（仍 killed，不炸）", k2["ok"] and k2["status"] == "killed", str(k2))
    check("kill 后步骤数不再增长", len(rt._history(run_id)) == steps)
    run = rt._load(run_id)
    check("killed=1 + 白话 summary 留痕", run["killed"] == 1 and "kill" in (run["summary"] or ""),
          str(run["summary"]))


# ─── T6 冻结区静态断言 ───
def t6_frozen_static():
    print("⑥ 冻结区静态断言（runtime.py 源码级不可达）")
    src = RUNTIME_SRC.read_text(encoding="utf-8")
    for frozen in sorted(FORBIDDEN_TOOLS):
        check(f"源码不含冻结工具名 {frozen}", frozen not in src)
    tree = ast.parse(src)
    planned_tools, all_strings = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            all_strings.append(node.value)
        if isinstance(node, ast.Dict):
            # 剧本计划字面量 {"kind": ..., "tool": "<工具名>", ...}——runtime 全部工具调用的静态源头
            # （dispatch 的实参是从这些 plan dict 流出的变量，故断言锚在计划字面量上）。
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "tool"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    planned_tools.append(v.value)
    check("AST：全部字符串常量无一命中冻结工具名（含子串级检查）",
          not any(f in s for s in all_strings for f in FORBIDDEN_TOOLS),
          str([s for s in all_strings if any(f in s for f in FORBIDDEN_TOOLS)]))
    bad = [t for t in planned_tools
           if any(w in t for w in ("approve", "close", "quote")) or t in FORBIDDEN_TOOLS]
    check(f"AST：剧本计划的工具名字面量 {sorted(set(planned_tools))} 无 approve/close/quote 字样",
          planned_tools and not bad, str(bad))
    writes = [t for t in planned_tools if t in ALL_WRITE_PERM]
    reads_or_writes = set(ALL_WRITE_PERM) | {"get_risk"}
    check("AST：写调用 ⊆ Toolbox 既有 7 写工具、全部调用 ⊆ 已知工具面",
          set(writes) <= set(ALL_WRITE_PERM) and set(planned_tools) <= reads_or_writes,
          str(sorted(set(planned_tools))))
    check("runtime 不直接 import 动作函数（写只经 Toolbox dispatch 单门）",
          "from app.actions" not in src and "app.admission_actions" not in src)


# ─── T7 等审批三分支 ───
def t7_approval_branches():
    print("⑦ 等审批三分支（pending / approved / rejected）")
    db = fresh_db()
    # pending → approved（同一 run 先后覆盖两分支）
    rt = new_rt(db)
    out = rt.start("处置 RSK-0001")
    run_id, task_id = out["run_id"], out["task_id"]
    check("提案提交后停在 waiting_approval + 记录等待的 task_id",
          out["status"] == "waiting_approval" and task_id, str(out))
    hist = rt._history(run_id)
    check("剧本步序 think→tool→command→command→wait",
          [s["kind"] for s in hist] == ["think", "tool", "command", "command", "wait"],
          str([s["kind"] for s in hist]))
    check("think 步如实标注确定性模式（llm=off 降级）",
          hist[0]["result"].get("mode") == "deterministic", str(hist[0]["result"]))
    keys = [s["payload"].get("idempotency_key") for s in hist if s["kind"] == "command"]
    check("写步骤幂等键=run:{run_id}:{step_no}",
          keys == [f"run:{run_id}:3", f"run:{run_id}:4"], str(keys))
    r1 = rt.resume(run_id)
    check("分支 pending：保持等待如实返回、不推进",
          r1["status"] == "waiting_approval" and r1.get("approval_status") == "pending", str(r1))
    check("分支 pending：不烧预算（步数不变）", len(rt._history(run_id)) == len(hist))
    ap = human_approve(rt, task_id, "approved")
    check("人工批准成功（测试扮演经理）", ap.get("ok") is True, str(ap.get("error")))
    r2 = rt.resume(run_id)
    run = rt._load(run_id)
    verify = [s for s in rt._history(run_id) if s["kind"] == "verify"]
    check("分支 approved：resume 后 run=done", r2["status"] == "done", str(r2))
    check("后置校验步（写后复读）已执行且通过",
          len(verify) == 1 and verify[0]["result"].get("ok") is True,
          str(verify[-1]["result"] if verify else None))
    obs = verify[0]["result"]["observed"] if verify else {}
    check("复读观测：approval_status=approved / status=done / action_taken 非空",
          obs.get("approval_status") == "approved" and obs.get("status") == "done"
          and obs.get("action_taken"), str(obs))
    check("done summary 白话落库", "写后复读" in (run["summary"] or ""), run["summary"])

    # rejected
    rt3 = new_rt(db)
    out3 = rt3.start("处置 RSK-0004")
    check("前置：第二个 run 停在等审批", out3["status"] == "waiting_approval", str(out3))
    rj = human_approve(rt3, out3["task_id"], "rejected")
    check("人工驳回成功", rj.get("ok") is True, str(rj.get("error")))
    r3 = rt3.resume(out3["run_id"])
    run3 = rt3._load(out3["run_id"])
    check("分支 rejected：run=failed", r3["status"] == "failed", str(r3))
    check("failed 带白话原因（驳回）", "驳回" in (run3["summary"] or ""), run3["summary"])
    # 状态相对（2026-07-19）：副本库可能已含真人真机遗留的 run 痕，断言"本测试新增的 2 趟可见"
    check("--list 能看到全部 run（含本测试新增2趟）",
          len(list_runs(db)) >= 2 and out3["run_id"] in {r["run_id"] for r in list_runs(db)})


# ─── T8 遥测登记守护 + T9 表约束 ───
def t8_t9_telemetry_and_checks():
    print("⑧ TELEMETRY_TABLES 登记守护 / ⑨ 表 CHECK 约束")
    from pipeline.db_digest import TELEMETRY_TABLES
    check("agent_runs / agent_run_steps 已登记 db_digest 豁免（业务确定性门不受扰）",
          {"agent_runs", "agent_run_steps"} <= TELEMETRY_TABLES, str(sorted(TELEMETRY_TABLES)))
    con = sqlite3.connect(":memory:")
    ensure_runtime_tables(con)
    bad_status = bad_kind = False
    try:
        con.execute("INSERT INTO agent_runs VALUES ('R1','g','bogus','ops','{}','t','t',0,NULL)")
    except sqlite3.IntegrityError:
        bad_status = True
    try:
        con.execute("INSERT INTO agent_run_steps VALUES ('R1',1,'bogus','{}',NULL,'t')")
    except sqlite3.IntegrityError:
        bad_kind = True
    check("非法 status 被表 CHECK 拒绝", bad_status)
    check("非法 step kind 被表 CHECK 拒绝", bad_kind)


def main() -> int:
    print("=== 波2-2c Agent runtime 测试（全部临时副本库） ===")
    t1_transitions()
    t2_budget()
    t3_loop_guard()
    t4_crash_resume()
    t5_kill()
    t6_frozen_static()
    t7_approval_branches()
    t8_t9_telemetry_and_checks()
    print("=" * 52)
    print("结果:", "全部通过 ✔" if not FAILS else f"{len(FAILS)} 项失败: {FAILS}")
    print("（临时副本测试，data/ 真库零写入；think 步全程 llm=off 未出境）")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
