"""G-Shadow 影子测量台（治理证据包，agent 域）：python3 -m agent.shadow_bench [--llm]

一句话：让 AI 把历史风险案例都跑一遍、给出它**会提**的处置建议，**只记录不生效**，和
"人的真实决定 / 确定性金标"算一致率——这是"以后要不要给 AI 更多权限"的唯一证据来源。

比较基准双档（规格钉死，逐字执行，不自选口径）：
- 档1｜对 resolution_memory 历史决定（真金标，data/simworld.sqlite）：对**已关闭且有
  decision+quality_label** 的历史风险，让 AI 基于**当时脱敏简报**（检测时点、剔除决定/结果，
  见 _build_grounding）产"它会建议的处置动作"，与人当时决定比。一致 = AI 的 proposed_action
  与"人最终采纳的动作"同类。额外维度：在 quality_label=effective 子集里 AI 的一致率——这才是
  "AI 跟对了好决定"的真信号。分规则 / 分航线(lane) / 分严重度切片报告（不能只给总数）。
- 档2｜对确定性金标（data/ontology.sqlite）：复用 agent.evaluate 的评估集（不重造）——
  scripted 模式给确定性基准通过率（无 LLM 也出，"确定性档必出"）；--llm 且 LLM 可用时另跑
  真 AI 过题通过率。

—— 硬红线（违反即返工）——
* 纯只读、零线上行为改变：AI 在此只"生成建议供比对"，绝不经写路径、不产 Task/提案、不碰
  dispatch 写工具、不碰审批。业务库（ontology/simworld）一律**只读连接**打开。
* 唯一写入点 = 旁路留痕库 shadow_run。**落在独立库 data/shadow.sqlite**，不碰核心对象、
  不进本体/ontology_lint/真值 md5。选独立库而非 ontology.sqlite 的三条理由：
    ① 纯只读红线最强保证——业务库连一张旁路表都不加，truth md5 / lint / 对象枚举 / api 测试
       可证零影响；
    ② 并发安全——G-Ledger 正在改 build_ontology 并会重建 ontology.sqlite，旁路表落那里会被
       重建清空、留痕丢失；独立库不受重建影响；
    ③ 规格验收明确允许（"表未建（若用独立db则说明）"）——本说明即为其说明。
* 绝不编造一致率：LLM 不可用 / 样本不足时**如实报**"LLM 不可用 / 样本 N 太小"，不给假数字
  （规则5：自信的具体≠真实——这数要拿去做放权决策，必须一手实测）。
* --llm 缺省走确定性档（无 key / claude CLI 不通也出确定性基准部分）。

为什么这样建（≤5 行设计说明）：
- 影子 AI 用 agent.llm_agent._invoke_cli 单发合成（本账号 claude 订阅，无需 key），出境前过
  egress 白名单摘除闸门（sanitize_for_egress）——保留出境治理红线，且**不写 llm_calls**
  （避免污染生产遥测，影子调用留痕落 shadow_run.ai_raw，自成审计）。
- 影子 AI 的可选动作 = resolution_memory 金标里出现的同一动作空间（sim ai_loop 产出的 6 类），
  让 AI 与人"从同一菜单里选"，一致率才可比（不塞入金标里从未出现的动作制造虚假不一致）。
- "人最终采纳的动作"取值：decision=adopted→提案动作（人采纳了它）；decision=rejected→
  accept_delay（sim 世界里驳回=不施加处置、结局 outcome=accepted_delay，忠于世界真实结局）。
  sim 只有 adopted/rejected 两态（无 modified），映射确定。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

REAL_DB = "data/ontology.sqlite"    # 档2 金标（eval_cases 锚定的真实世界对象）——只读
SIM_DB = "data/simworld.sqlite"     # 档1 金标（resolution_memory）——只读
SHADOW_DB = "data/shadow.sqlite"    # 旁路留痕库（独立库，见模块 docstring）——唯一写入点

# 影子 AI 可选动作 = resolution_memory 金标动作空间（sim ai_loop 6 类）。含义供模型判读，键即分类值。
ACTION_TAXONOMY = {
    "expedite": "加急 / 空运补货，提前到港",
    "accept_delay": "接受延误 / 维持现状",
    "dispute": "争议账单，追回超额费用",
    "chase_docs": "补件催办，补齐缺失单证",
    "escalate_replenishment": "紧急补货，抬高库存",
    "escalate": "升级人工处理",
}
MIN_SLICE_N = 5  # 切片样本下限：低于此只报计数并标注"样本太小不下结论"，绝不用小样本冒充一致率

SHADOW_RUN_DDL = """CREATE TABLE IF NOT EXISTS shadow_run (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tier TEXT NOT NULL,            -- bench1_resolution | bench2_goldset
    mode TEXT NOT NULL,            -- scripted | llm
    case_ref TEXT NOT NULL,        -- risk_event_id(档1) | eval case id(档2)
    rule_id TEXT,
    lane TEXT,
    severity TEXT,
    decision TEXT,                 -- 档1：人的决定
    quality_label TEXT,            -- 档1：事后质量标签
    human_action TEXT,             -- 档1：人最终采纳的动作
    ai_action TEXT,                -- 档1：AI 影子提案动作；档2：''（金标题无动作维度）
    consistent INTEGER,            -- 1/0；档1: ai==human；档2: 是否过金标题；NULL=未判定/不可解析
    ai_raw TEXT,                   -- 审计：模型原始回答 / scripted 答案摘要
    note TEXT
)"""


# ─────────────────────────── 连接与工具 ───────────────────────────
def _ro(db_path: str) -> sqlite3.Connection:
    """只读连接（mode=ro，URI）：业务库一律经此打开，物理上写不进去。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rate(n_ok: int, n: int) -> str:
    return f"{n_ok}/{n} = {n_ok / n * 100:.1f}%" if n else "N/A（无样本）"


# ─────────────────────────── LLM 单发合成（只读、不写 llm_calls）───────────────────────────
def _llm_model() -> str:
    import os
    return os.environ.get("AGENT_MODEL", "claude-opus-4-8")


def probe_llm(model: str, timeout: int = 60):
    """真实探测 LLM 可用性：发一发轻量单发调用（无 PI），成功返回 (True, '')，
    失败返回 (False, 原因)。**一次真调用**而非只查 PATH——本账号订阅装了但调不通（如未登录 /
    环境不通）时 which 找得到却退出码非零，只有真调一次才知道（规则2：证据缺失≠不存在，也≠可用）。"""
    from .llm_agent import _invoke_cli
    try:
        _invoke_cli("回复一个字：好", model, timeout)
        return True, ""
    except Exception as exc:  # noqa: BLE001 —— 探测失败一律视为不可用，如实带回原因
        return False, f"{type(exc).__name__}: {str(exc)[:160]}"


def _parse_action(text: str):
    """从模型回答里解析出一个分类动作：取**最先出现**的 taxonomy 关键词（大小写不敏感）；
    没有任何关键词命中 → 返回 None（不可解析，如实计入分母外，绝不猜一个凑数）。"""
    low = (text or "").lower()
    hits = [(low.index(k), k) for k in ACTION_TAXONOMY if k in low]
    return min(hits)[1] if hits else None


def ask_shadow_action(grounding: str, model: str, timeout: int = 90):
    """让影子 AI 基于脱敏简报产"它会建议的处置动作"（单发、单动作、无解释）。
    出境前过 egress 白名单摘除闸门（保留出境治理红线）。返回 (ai_action|None, raw_answer, redactions)。"""
    from .egress_gate import sanitize_for_egress
    from .llm_agent import _invoke_cli
    menu = "\n".join(f"- {k}：{v}" for k, v in ACTION_TAXONOMY.items())
    prompt = (
        "你是跨境供应链控制塔的处置建议助手。下面是一条历史风险在【检测时点】的脱敏简报"
        "（不含任何后续人工决定与结果）。假如由你来提处置建议，你会建议下列哪一个处置动作？\n"
        "只输出一个动作关键词（下列之一），不要解释、不要标点、不要多余文字。\n\n"
        f"可选动作：\n{menu}\n\n"
        f"【脱敏简报】\n{grounding}\n\n"
        "只输出一个动作关键词："
    )
    clean, report = sanitize_for_egress(prompt)
    raw = _invoke_cli(clean, model, timeout)  # 失败抛 RuntimeError，由调用方计入 LLM 不可用
    return _parse_action(raw), (raw or "").strip(), report


# ─────────────────────────── 档1：对 resolution_memory 历史决定 ───────────────────────────
def _human_action(decision: str, proposal_summary: str) -> str:
    """人最终采纳的动作（见模块 docstring 映射）：adopted→提案动作；rejected→accept_delay。"""
    try:
        proposed = json.loads(proposal_summary).get("action", "?")
    except (ValueError, TypeError, AttributeError):
        proposed = "?"
    if decision == "rejected":
        return "accept_delay"
    return proposed  # adopted / modified（sim 无 modified，兜底同采纳）


def _closed_memory_rows(sim_ro: sqlite3.Connection):
    """已关闭且有 decision+quality_label 的历史风险（档1 population）。只读 SELECT。"""
    return sim_ro.execute(
        """SELECT rm.memory_id, rm.risk_event_id, rm.rule_id, rm.lane, rm.severity,
                  rm.proposal_summary, rm.decision, rm.quality_label, rm.outcome_resolved
           FROM resolution_memory rm
           WHERE rm.status='active' AND rm.decision IS NOT NULL AND rm.decision!=''
             AND rm.quality_label IS NOT NULL AND rm.quality_label!=''
           ORDER BY rm.memory_id""").fetchall()


def _build_grounding(sim_ro: sqlite3.Connection, mem_row: sqlite3.Row) -> str:
    """构造**检测时点、脱敏、无泄漏**的简报作 grounding（同 UI 对象工作台 grounding 精神）。

    红线（valid measurement 的命根子）：只放"检测时点已知"的字段——rule/type/severity/涉险金额/
    航线/incoterm/root_cause/受影响订单行；**剔除一切后验字段**——status/outcome/resolved_at/
    resolution_summary（会直接泄漏人的决定与结果）、shipment 的可变字段（eta_current/expedite_flag/
    missing_docs/delay_days，会因处置生效而变、泄漏做没做处置）。客户等级按 ops 角色脱敏（掩码）。"""
    rid = mem_row["risk_event_id"]
    re = sim_ro.execute(
        """SELECT rule_id, type, severity, affected_value_usd, shipment_id,
                  affected_so_line_ids, root_cause FROM risk_events
           WHERE risk_event_id=?""", (rid,)).fetchone()
    lines = [f"【风险快照（检测时点）】风险 {rid}"]
    if re:
        lines.append(f"规则 {re['rule_id']} / 类型 {re['type']} / 严重度 {re['severity']}")
        lines.append(f"涉险金额 ${re['affected_value_usd']}")
        lines.append(f"根因：{re['root_cause']}")
    else:  # 极端：记忆行有但风险行缺——退回记忆行的稳定字段，不编造
        lines.append(f"规则 {mem_row['rule_id']} / 严重度 {mem_row['severity']}")
    lane = mem_row["lane"] or "-"
    incoterm = ""
    ship_id = re["shipment_id"] if re else ""
    if ship_id:
        sp = sim_ro.execute("SELECT incoterm FROM shipments WHERE shipment_id=?",
                            (ship_id,)).fetchone()
        incoterm = sp["incoterm"] if sp else ""
    lines.append(f"航线 {lane}" + (f" / incoterm {incoterm}" if incoterm else ""))
    # 受影响订单行（脱敏：ops 角色看不到客户等级 → 掩码），join 失败则跳过该段（简报仍成立）
    try:
        lids = json.loads(re["affected_so_line_ids"]) if re and re["affected_so_line_ids"] else []
    except (ValueError, TypeError):
        lids = []
    if lids:
        ph = ",".join("?" * len(lids))
        try:
            aff = sim_ro.execute(
                f"""SELECT l.so_line_id, l.qty, l.promised_delivery_date, l.line_status,
                           c.customer_id FROM sales_order_lines l
                    JOIN sales_orders so ON so.so_id=l.so_id
                    JOIN customers c ON c.customer_id=so.customer_id
                    WHERE l.so_line_id IN ({ph})""", lids).fetchall()
        except sqlite3.Error:
            aff = []
        if aff:
            lines.append(f"【受影响订单行】{len(aff)} 行：")
            for a in aff:
                lines.append(f"- {a['so_line_id']} 客户 {a['customer_id']}（等级🔒无权查看）"
                             f" {a['qty']} 件，承诺日 {a['promised_delivery_date']}，"
                             f"行状态 {a['line_status']}")
    return "\n".join(lines)


def run_bench1(sim_ro, run_id, created_at, llm_available, model, limit, timeout, verbose,
               no_llm_reason="LLM 不可用"):
    """档1 主流程。返回 (rows, summary)：
    rows = 每案 shadow_run 行（仅 LLM 跑通时才有 AI 提案行）；summary = 供报告的结构化统计。
    无 LLM：不产 AI 行（不编造），summary 只含"可比案例清单"（分域计数，说明有 LLM 时将据此测量）。"""
    pop = _closed_memory_rows(sim_ro)
    # 可比案例清单（分规则/航线/严重度）——无论有无 LLM 都算，作为"能测多少"的诚实底数
    inv = {"total": len(pop), "by_rule": defaultdict(int), "by_lane": defaultdict(int),
           "by_severity": defaultdict(int), "effective_total": 0}
    for r in pop:
        inv["by_rule"][r["rule_id"]] += 1
        inv["by_lane"][r["lane"] or "-"] += 1
        inv["by_severity"][r["severity"]] += 1
        if r["quality_label"] == "effective":
            inv["effective_total"] += 1

    rows, records = [], []
    if not llm_available:
        return rows, {"ran": False, "reason": no_llm_reason, "inventory": inv,
                      "attempted": 0, "parsed": 0}

    subset = pop if not limit else pop[:limit]
    n_fail = 0
    for i, r in enumerate(subset, 1):
        grounding = _build_grounding(sim_ro, r)
        human = _human_action(r["decision"], r["proposal_summary"])
        try:
            ai_action, raw, _rep = ask_shadow_action(grounding, model, timeout)
        except Exception as exc:  # noqa: BLE001 —— 单案失败不编造，如实记 error 行
            n_fail += 1
            if verbose:
                print(f"  [档1 {i}/{len(subset)}] {r['risk_event_id']} LLM 调用失败："
                      f"{type(exc).__name__} {str(exc)[:80]}", file=sys.stderr)
            rows.append(dict(run_id=run_id, created_at=created_at, tier="bench1_resolution",
                             mode="llm", case_ref=r["risk_event_id"], rule_id=r["rule_id"],
                             lane=r["lane"], severity=r["severity"], decision=r["decision"],
                             quality_label=r["quality_label"], human_action=human,
                             ai_action=None, consistent=None,
                             ai_raw=f"[LLM 调用失败] {str(exc)[:120]}", note="llm_error"))
            if n_fail >= 3 and n_fail == i:  # 连开局连续失败 → fail-fast，避免几十次 60s 空转
                if verbose:
                    print("  [档1] 连续失败，判定 LLM 通道不可用，中止 LLM 档（如实标注）",
                          file=sys.stderr)
                return rows, {"ran": False, "reason": "LLM 调用连续失败，通道不可用",
                              "inventory": inv, "attempted": i, "parsed": 0}
            continue
        consistent = None if ai_action is None else int(ai_action == human)
        if ai_action is not None:
            records.append({"rule_id": r["rule_id"], "lane": r["lane"] or "-",
                            "severity": r["severity"], "quality_label": r["quality_label"],
                            "consistent": consistent})
        if verbose:
            tag = "?" if ai_action is None else ("✓" if consistent else "✗")
            print(f"  [档1 {i}/{len(subset)}] {r['risk_event_id']} {r['rule_id']} "
                  f"人={human} AI={ai_action or '不可解析'} {tag}", file=sys.stderr)
        rows.append(dict(run_id=run_id, created_at=created_at, tier="bench1_resolution",
                         mode="llm", case_ref=r["risk_event_id"], rule_id=r["rule_id"],
                         lane=r["lane"], severity=r["severity"], decision=r["decision"],
                         quality_label=r["quality_label"], human_action=human,
                         ai_action=ai_action, consistent=consistent,
                         ai_raw=raw[:500], note="" if ai_action else "unparseable"))

    parsed = records
    summary = {"ran": True, "inventory": inv, "attempted": len(subset),
               "parsed": len(parsed), "unparseable": len(subset) - len(parsed) - n_fail,
               "llm_errors": n_fail,
               "overall": _consistency(parsed),
               "effective": _consistency([r for r in parsed if r["quality_label"] == "effective"]),
               "by_rule": _sliced(parsed, "rule_id"),
               "by_lane": _sliced(parsed, "lane"),
               "by_severity": _sliced(parsed, "severity")}
    return rows, summary


def _consistency(records):
    n = len(records)
    ok = sum(r["consistent"] for r in records)
    return {"n": n, "consistent": ok, "rate": (ok / n if n else None)}


def _sliced(records, key):
    """分域一致率（规格红线：必须分域，不能只给总数）。每片给 n + 一致数 + 率；n<MIN 标低置信。"""
    buckets = defaultdict(list)
    for r in records:
        buckets[r[key]].append(r)
    out = {}
    for k, rs in sorted(buckets.items()):
        c = _consistency(rs)
        c["low_confidence"] = c["n"] < MIN_SLICE_N
        out[k] = c
    return out


# ─────────────────────────── 档2：对确定性金标（复用 agent.evaluate 评估集）───────────────────────────
def _passed(case, answer: str) -> bool:
    """金标判分（与 agent.evaluate.grade 同口径，不重造）：应含全含、禁含全无。"""
    missing = [k for k in case["expected_contains"] if k not in answer]
    leaked = [k for k in case.get("forbidden", []) if k in answer]
    return not missing and not leaked


def run_bench2(run_id, created_at, mode, real_db, cases, limit, verbose):
    """档2：在 real_db 的临时副本上跑评估集，逐题判分。
    mode='scripted'：确定性作答（无 LLM，必出）；mode='llm'：真 AI 过题（run_agent）。
    返回 (rows, summary)。临时副本 → 绝不污染工作库。"""
    from agent.evaluate import scripted_answer
    from agent.tools import AgentSession
    tmp = Path(tempfile.mkdtemp()) / "shadow_eval.sqlite"
    try:
        shutil.copy(real_db, tmp)
    except OSError as exc:
        return [], {"ran": False, "reason": f"复制金标库失败：{exc}", "mode": mode}
    sessions = {}

    def session_for(role):
        if role not in sessions:
            sessions[role] = AgentSession(db_path=str(tmp), role=role)
        return sessions[role]

    subset = cases if not limit else cases[:limit]
    rows, records = [], []
    for i, case in enumerate(subset, 1):
        role = case.get("role", "ops")
        try:
            if mode == "llm":
                from agent.llm_agent import run_agent
                answer = run_agent(case["question"], session=session_for(role), verbose=False)
            else:
                answer = scripted_answer(session_for(role), case)
        except Exception as exc:  # noqa: BLE001 —— LLM 档单题失败不编造，如实记 error
            rows.append(dict(run_id=run_id, created_at=created_at, tier="bench2_goldset",
                             mode=mode, case_ref=case["id"], rule_id=case["type"], lane=None,
                             severity=None, decision=None, quality_label=None, human_action=None,
                             ai_action="", consistent=None,
                             ai_raw=f"[调用失败] {str(exc)[:120]}", note="llm_error"))
            if mode == "llm" and i <= 3 and len(rows) == i:  # LLM 开局连续失败 → 中止（不空转）
                return rows, {"ran": False, "reason": "LLM 调用连续失败，通道不可用", "mode": mode}
            continue
        ok = _passed(case, answer)
        records.append({"type": case["type"], "ok": ok})
        rows.append(dict(run_id=run_id, created_at=created_at, tier="bench2_goldset", mode=mode,
                         case_ref=case["id"], rule_id=case["type"], lane=None, severity=None,
                         decision=None, quality_label=None, human_action=None, ai_action="",
                         consistent=int(ok), ai_raw=(answer or "")[:500], note=""))
        if verbose and mode == "llm":
            print(f"  [档2-llm {i}/{len(subset)}] {case['id']} {'✓' if ok else '✗'}",
                  file=sys.stderr)
    n = len(records)
    n_ok = sum(r["ok"] for r in records)
    by_type = defaultdict(lambda: [0, 0])
    for r in records:
        by_type[r["type"]][0] += int(r["ok"])
        by_type[r["type"]][1] += 1
    summary = {"ran": True, "mode": mode, "n": n, "passed": n_ok,
               "rate": (n_ok / n if n else None),
               "by_type": {k: {"passed": v[0], "n": v[1]} for k, v in sorted(by_type.items())}}
    return rows, summary


# ─────────────────────────── 留痕 & 报告 ───────────────────────────
def persist(shadow_db: str, rows: list) -> int:
    """把本轮所有 shadow_run 行落独立旁路库（唯一写入点）。返回落库行数。"""
    Path(shadow_db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(shadow_db)
    try:
        con.execute(SHADOW_RUN_DDL)
        cols = ["run_id", "created_at", "tier", "mode", "case_ref", "rule_id", "lane",
                "severity", "decision", "quality_label", "human_action", "ai_action",
                "consistent", "ai_raw", "note"]
        con.executemany(
            f"INSERT INTO shadow_run ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
            [tuple(r.get(c) for c in cols) for r in rows])
        con.commit()
        return con.execute("SELECT count(*) FROM shadow_run WHERE run_id=?",
                           (rows[0]["run_id"],)).fetchone()[0] if rows else 0
    finally:
        con.close()


def _print_bench1(s):
    print("\n" + "=" * 68)
    print("档1｜对 resolution_memory 历史决定（真金标，simworld）")
    print("=" * 68)
    inv = s["inventory"]
    print(f"可比案例（已关闭且有 decision+quality_label）：{inv['total']} 例"
          f"（其中人决定事后 effective：{inv['effective_total']} 例）")
    print("  分规则：" + "  ".join(f"{k}={v}" for k, v in sorted(inv["by_rule"].items())))
    print("  分严重度：" + "  ".join(f"{k}={v}" for k, v in sorted(inv["by_severity"].items())))
    print("  分航线：" + "  ".join(f"{k}={v}" for k, v in sorted(inv["by_lane"].items())))
    if not s["ran"]:
        print(f"\n⚠️ 一致率未测：{s['reason']}。")
        print("  （诚实优先：无一手实测不给一致率数字——这数要拿去做放权决策。上为可比案例底数，"
              "LLM 通道恢复后据此逐案实测。）")
        return
    print(f"\n实测：尝试 {s['attempted']} 例，可解析 {s['parsed']} 例"
          f"（不可解析 {s['unparseable']}，LLM 失败 {s['llm_errors']}）")
    ov, ef = s["overall"], s["effective"]
    print(f"  ▸ 总体一致率：{_rate(ov['consistent'], ov['n'])}"
          + ("（样本太小，不下结论）" if ov["n"] < MIN_SLICE_N else ""))
    print(f"  ▸ 【人决定 effective 子集】一致率（AI 跟对好决定的真信号）："
          f"{_rate(ef['consistent'], ef['n'])}"
          + ("（样本太小，不下结论）" if ef["n"] < MIN_SLICE_N else ""))
    for label, key in (("分规则", "by_rule"), ("分航线", "by_lane"), ("分严重度", "by_severity")):
        print(f"  {label}一致率：")
        for k, c in s[key].items():
            flag = "  ⚠样本太小" if c["low_confidence"] else ""
            print(f"    - {k}: {_rate(c['consistent'], c['n'])}{flag}")


def _print_bench2(s_scripted, s_llm):
    print("\n" + "=" * 68)
    print("档2｜对确定性金标（agent.evaluate 评估集，ontology）")
    print("=" * 68)
    if s_scripted.get("ran"):
        print(f"确定性基准（scripted，无 LLM 必出）：通过率 {_rate(s_scripted['passed'], s_scripted['n'])}")
        for k, v in s_scripted["by_type"].items():
            print(f"    - {k}: {_rate(v['passed'], v['n'])}")
    else:
        print(f"确定性基准未出：{s_scripted.get('reason')}")
    if s_llm is None:
        print("\n真 AI 过题率（LLM 档）：未请求（无 --llm）。")
    elif s_llm.get("ran"):
        print(f"\n真 AI 过题率（llm，真 AI 过金标题）：通过率 {_rate(s_llm['passed'], s_llm['n'])}")
        for k, v in s_llm["by_type"].items():
            print(f"    - {k}: {_rate(v['passed'], v['n'])}")
    else:
        print(f"\n真 AI 过题率（LLM 档）未跑：{s_llm.get('reason')}（如实标注，不编造）。")


def main():
    ap = argparse.ArgumentParser(description="G-Shadow 影子测量台（纯只读，不改任何线上 AI 行为）")
    ap.add_argument("--llm", action="store_true",
                    help="请求 LLM 档（档1 影子提案 + 档2 真 AI 过题）；LLM 不可用则优雅降级如实标注")
    ap.add_argument("--limit", type=int, default=None,
                    help="LLM 档每档最多跑 N 案（控制耗时；默认全跑）。不影响确定性基准与可比案例清单")
    ap.add_argument("--timeout", type=int, default=90, help="单次 LLM 调用超时秒")
    ap.add_argument("--real-db", default=REAL_DB)
    ap.add_argument("--sim-db", default=SIM_DB)
    ap.add_argument("--shadow-db", default=SHADOW_DB)
    ap.add_argument("--quiet", action="store_true", help="不打印逐案进度")
    args = ap.parse_args()
    verbose = not args.quiet

    run_id = "SHADOW-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    created_at = _now()
    model = _llm_model()

    print("=" * 68)
    print("G-Shadow 影子测量台")
    print("这是**影子测量**：AI 只生成建议供比对，不改变任何线上 AI 行为、不产生 Task/提案/审批，")
    print("落痕只进独立旁路库 shadow_run（data/shadow.sqlite），业务库全程只读。")
    print("=" * 68)
    print(f"run_id={run_id}  model={model}  --llm={'on' if args.llm else 'off'}"
          + (f"  --limit={args.limit}" if args.limit else ""))

    # LLM 可用性：仅当 --llm 时真探测一次（缺省确定性档不触发任何出境）
    llm_available, llm_reason = (False, "未请求 --llm")
    if args.llm:
        print("\n探测 LLM 通道（本账号 claude 订阅，单发合成）…")
        llm_available, llm_reason = probe_llm(model, timeout=min(args.timeout, 60))
        print(f"  LLM 可用：{llm_available}" + (f"（{llm_reason}）" if not llm_available else ""))

    all_rows = []

    # 档2 确定性基准（永远跑，"确定性档必出"）
    cases = yaml.safe_load(open("agent/eval_cases.yaml", encoding="utf-8"))
    b2_rows, b2_scripted = run_bench2(run_id, created_at, "scripted", args.real_db, cases,
                                      None, verbose)
    all_rows += b2_rows
    # 档2 LLM（仅 --llm 且可用）
    b2_llm = None
    if args.llm and llm_available:
        print("\n档2 LLM：真 AI 过金标题…")
        b2l_rows, b2_llm = run_bench2(run_id, created_at, "llm", args.real_db, cases,
                                      args.limit, verbose)
        all_rows += b2l_rows
    elif args.llm:
        b2_llm = {"ran": False, "reason": f"LLM 不可用：{llm_reason}", "mode": "llm"}

    # 档1 对 resolution_memory
    no_llm_reason = (f"LLM 不可用：{llm_reason}" if args.llm else "未请求 --llm（缺省只出确定性档）")
    sim_ro = _ro(args.sim_db)
    try:
        if args.llm and llm_available:
            print("\n档1：影子 AI 逐案产建议 vs 人历史决定…")
        b1_rows, b1_sum = run_bench1(sim_ro, run_id, created_at, args.llm and llm_available,
                                     model, args.limit, args.timeout, verbose,
                                     no_llm_reason=no_llm_reason)
        all_rows += b1_rows
    finally:
        sim_ro.close()

    # 落痕（唯一写入点）
    n_persisted = persist(args.shadow_db, all_rows)

    # ── 报告 ──
    _print_bench2(b2_scripted, b2_llm)
    _print_bench1(b1_sum)
    print("\n" + "=" * 68)
    print(f"留痕：shadow_run 落 {args.shadow_db}，本轮 run_id={run_id} 写入 {n_persisted} 行"
          f"（独立旁路库，未碰业务库 / 真值 md5 / ontology_lint）。")
    print("提醒：本测量不改任何线上 AI 行为，是影子测量；一致率数字全部一手实测，"
          "LLM 不可用/样本不足处已如实标注，未编造任何数字。")
    print("=" * 68)


if __name__ == "__main__":
    main()
