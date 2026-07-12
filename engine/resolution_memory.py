"""C1 处置记忆（resolution memory）：每次处置沉淀为下次提案可引用的证据。

为什么这样建（≤5 行）：
- 写入只发生在动作层 approve/close 成功路径（AI 无写工具）；检索/渲染纯只读——C1 四红线。
- 相似 = rule_id 精确匹配 + 同航线 lane（Daniel 裁决）；lane 取 Shipment 的
  origin_port_locode→destination_port_locode（LOCODE 比自由文本港名/承运人稳定）。
- memory_id 确定性生成（sha256 熵，禁 count+1，仿 pipeline.dq_issues；碰撞用后缀，仿 _next_task_id）。
- 先例区块所有数字调用时从表现算（禁缓存/编造）；status=voided 的记忆对检索不可见（G1 负向测试）。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date

DECISIONS = ("adopted", "modified", "rejected")          # 人的决定：采纳/修改后采纳/拒绝
QUALITY_LABELS = ("effective", "partial", "ineffective")  # 关闭时专员三选一（人打标，AI 不自评）

# 列顺序即 INSERT/SELECT 顺序（本模块内用位置访问，不依赖连接的 row_factory）
COLUMNS = [
    "memory_id", "risk_event_id", "rule_id", "lane", "severity", "impact_usd", "as_of",
    "proposal_summary", "proposal_version", "cited_precedent_ids", "decision", "decision_note",
    "offsite_basis", "decided_by", "decided_at", "outcome_resolved", "outcome_days",
    "quality_label", "closed_by", "closed_at", "status",
]

RESOLUTION_MEMORY_DDL = """CREATE TABLE IF NOT EXISTS resolution_memory (
    memory_id TEXT PRIMARY KEY,
    risk_event_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    lane TEXT NOT NULL,
    severity TEXT,
    impact_usd REAL,
    as_of TEXT NOT NULL,
    proposal_summary TEXT NOT NULL,
    proposal_version TEXT NOT NULL,
    cited_precedent_ids TEXT NOT NULL,
    decision TEXT NOT NULL,
    decision_note TEXT,
    offsite_basis TEXT,
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    outcome_resolved TEXT,
    outcome_days INTEGER,
    quality_label TEXT,
    closed_by TEXT,
    closed_at TEXT,
    status TEXT NOT NULL DEFAULT 'active'
)"""

# 提案动作 → 汇总行中文标签（延误域为 C1 主场景；未列出的动作用原名如实展示，不猜译）
ACTION_LABELS = {"reschedule": "改期", "expedite": "加急", "accept_delay": "接受延误"}
DECISION_LABELS = {"adopted": "采纳", "modified": "修改后采纳", "rejected": "拒绝"}
QUALITY_LABELS_ZH = {"effective": "有效", "partial": "部分有效", "ineffective": "无效"}


def ensure_resolution_memory_table(conn: sqlite3.Connection) -> None:
    """建表兜底（幂等）：pipeline.build_ontology 建正表；旧库副本运行期补建（仿 M2 兼容模式）。"""
    conn.execute(RESOLUTION_MEMORY_DDL)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone()[0] > 0


def _stable_memory_id(entropy: str) -> str:
    digest = hashlib.sha256(entropy.encode("utf-8")).hexdigest()[:10].upper()
    return f"MEM-{digest}"


def _next_memory_id(conn: sqlite3.Connection, entropy: str) -> str:
    """确定性 id + 碰撞后缀（同 app.actions._next_task_id 模式，禁 count+1）。"""
    base = _stable_memory_id(entropy)
    candidate, suffix = base, 2
    while conn.execute("SELECT 1 FROM resolution_memory WHERE memory_id=?",
                       (candidate,)).fetchone():
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def lane_for_shipment(conn: sqlite3.Connection, shipment_id: str | None) -> str:
    """lane = origin_port_locode→destination_port_locode；无 shipment（采购/仓储风险）或
    无 shipments 表（最小 schema 测试库）返回 ''（空 lane 只与空 lane 相似，rule_id 仍精确匹配）。"""
    if not shipment_id:
        return ""
    try:
        row = conn.execute("""SELECT origin_port_locode, destination_port_locode
                              FROM shipments WHERE shipment_id=?""", (shipment_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return ""
        raise
    if not row or not row[0] or not row[1]:
        return ""
    return f"{row[0]}→{row[1]}"


def write_decision_memory(conn, risk_event_id, task_id, proposed_action, proposal_params,
                          decision, decision_note, decided_by, as_of,
                          offsite_basis=None, proposal_version=None):
    """审批成功后由动作层调用（AI 无此工具）。记录 DecisionRecord 四件套血缘：
    情境快照（rule/lane/severity/金额/as_of）+ 提案（版本戳+正文）+ 引用先例 + 决策人，
    另存场外依据（可空）。返回 memory_id。"""
    if decision not in DECISIONS:
        raise ValueError(f"decision 必须是 {sorted(DECISIONS)} 之一，收到 {decision!r}")
    ensure_resolution_memory_table(conn)
    risk = conn.execute("""SELECT rule_id, severity, affected_value_usd, shipment_id
                           FROM risk_events WHERE risk_event_id=?""", (risk_event_id,)).fetchone()
    if not risk:
        raise ValueError(f"风险事件 {risk_event_id} 不存在，无法归档处置记忆")
    rule_id, severity, impact_usd, shipment_id = risk[0], risk[1], risk[2], risk[3]
    lane = lane_for_shipment(conn, shipment_id)
    # 引用先例：决策时刻现查（同规则+同航线，排除本案），引用最相似 1 条（Daniel 裁决 Q2）
    similar = find_similar(conn, rule_id, lane, exclude_risk_id=risk_event_id)
    cited = [similar["most_similar"]["memory_id"]] if similar["most_similar"] else []
    summary = json.dumps({"action": proposed_action, "params": proposal_params},
                         ensure_ascii=False, sort_keys=True)
    if proposal_version is None:
        # 版本戳 = 提案内容寻址（同内容同戳、改提案即换戳），可回放"当时批的是哪一版"
        proposal_version = "prop-" + hashlib.sha256(summary.encode("utf-8")).hexdigest()[:10]
    memory_id = _next_memory_id(conn, f"{risk_event_id}|{task_id}|{decision}|{as_of}")
    conn.execute(
        f"INSERT INTO resolution_memory ({','.join(COLUMNS)}) "
        f"VALUES ({','.join('?' * len(COLUMNS))})",
        (memory_id, risk_event_id, rule_id, lane, severity, impact_usd, as_of,
         summary, proposal_version, json.dumps(cited), decision, decision_note,
         offsite_basis, decided_by, as_of, None, None, None, None, None, "active"))
    return memory_id


def backfill_outcome(conn, risk_event_id, quality_label, closed_by, as_of, outcome=None):
    """关闭风险时由动作层调用：回填该风险全部 active 记忆行的实际结果
    （outcome/耗时天数/质量标签/关闭人/关闭日）。quality_label 可空；非法值抛 ValueError。
    返回回填行数（无记忆行返回 0，不报错——兼容无提案直接关闭的路径）。"""
    if quality_label is not None and quality_label not in QUALITY_LABELS:
        raise ValueError(f"quality_label 必须是 {sorted(QUALITY_LABELS)} 之一或不填，"
                         f"收到 {quality_label!r}")
    ensure_resolution_memory_table(conn)
    rows = conn.execute("""SELECT memory_id, decided_at FROM resolution_memory
                           WHERE risk_event_id=? AND status='active'
                           ORDER BY memory_id""", (risk_event_id,)).fetchall()
    for memory_id, decided_at in rows:
        days = (date.fromisoformat(as_of[:10]) - date.fromisoformat(decided_at[:10])).days
        conn.execute("""UPDATE resolution_memory SET outcome_resolved=?, outcome_days=?,
                        quality_label=?, closed_by=?, closed_at=? WHERE memory_id=?""",
                     (outcome, days, quality_label, closed_by, as_of, memory_id))
    return len(rows)


def find_similar(conn, rule_id, lane, exclude_risk_id=None):
    """只读检索同类先例：rule_id 精确匹配 + 同 lane（Daniel 裁决 Q1），排除本案自身；
    status=voided 的记忆绝不返回（屏蔽即不可引用，G1）。返回
    {total, by_decision, by_action, most_similar}——全部运行时现算。
    most_similar 排序（确定性）：已回填结果者优先（能讲"后来怎么样"）> 采纳过的方案优先
    （裁决文案"当时选择…实际结果…"）> 决策日新 > memory_id。"""
    empty = {"rule_id": rule_id, "lane": lane, "total": 0,
             "by_decision": {}, "by_action": {}, "most_similar": None}
    if not _table_exists(conn, "resolution_memory"):
        return empty  # 只读工具不建表（旧库副本上保持零写入）
    rows = [dict(zip(COLUMNS, r)) for r in conn.execute(
        f"""SELECT {','.join(COLUMNS)} FROM resolution_memory
            WHERE status='active' AND rule_id=? AND lane=? AND risk_event_id != ?
            ORDER BY (closed_at IS NULL) ASC,
                     CASE decision WHEN 'adopted' THEN 0 WHEN 'modified' THEN 1 ELSE 2 END ASC,
                     decided_at DESC, memory_id DESC""",
        (rule_id, lane, exclude_risk_id or ""))]
    if not rows:
        return empty
    by_decision, by_action = {}, {}
    for r in rows:
        by_decision[r["decision"]] = by_decision.get(r["decision"], 0) + 1
        try:
            action = json.loads(r["proposal_summary"]).get("action", "?")
        except (ValueError, AttributeError):
            action = "?"
        r["proposed_action"] = action
        by_action[action] = by_action.get(action, 0) + 1
    return {"rule_id": rule_id, "lane": lane, "total": len(rows),
            "by_decision": by_decision, "by_action": by_action, "most_similar": rows[0]}


def render_precedent_block(similar):
    """渲染提案/审批区先例区块（Daniel 裁决 Q2：最相似 1 条讲透 + 一行汇总统计）。
    所有数字来自 find_similar 的现查结果，可对表回查核对；无先例如实说"首例"。"""
    if similar["total"] == 0:
        return (f"无先例，首例（rule={similar['rule_id']}、lane={similar['lane'] or '-'} "
                f"下无历史处置记忆）。")
    actions = "/".join(f"{ACTION_LABELS.get(a, a)} {n}"
                       for a, n in sorted(similar["by_action"].items()))
    decisions = "·".join(f"{DECISION_LABELS.get(d, d)} {n}"
                         for d, n in sorted(similar["by_decision"].items()))
    m = similar["most_similar"]
    act_label = ACTION_LABELS.get(m["proposed_action"], m["proposed_action"])
    line = (f"同类 {similar['total']} 次：{actions}（{decisions}）；"
            f"最相似 {m['risk_event_id']}：当时提案「{act_label}」被{DECISION_LABELS[m['decision']]}"
            f"（{m['decided_by']} @{m['decided_at']}")
    if m["decision_note"]:
        line += f"，批注：{m['decision_note']}"
    if m["offsite_basis"]:
        line += f"，场外依据：{m['offsite_basis']}"
    line += "），结果"
    if m["closed_at"]:
        line += f" {m['outcome_resolved'] or '-'}、{m['outcome_days']} 天关闭"
        if m["quality_label"]:
            line += f"、专员标记「{QUALITY_LABELS_ZH.get(m['quality_label'], m['quality_label'])}」"
        line += "。"
    else:
        line += "未回填（该案尚未关闭）。"
    return line
