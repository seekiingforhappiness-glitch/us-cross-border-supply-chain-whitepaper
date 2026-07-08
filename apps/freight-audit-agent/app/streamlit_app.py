import sys
from pathlib import Path

_R = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _R) if _R not in sys.path else None

import json
import sqlite3

import pandas as pd
import streamlit as st

from agent.llm import LLMClient
from agent.tools import draft_dispute_letter
from app import actions
from app.rbac import ROLES, can_do, make_actor
from config.loader import db_path, load_config
from governance.audit import ensure_action_log
from governance.outbox import ensure_dispute_outbox

st.set_page_config(page_title="Freight Audit — Review Console", layout="wide")

_CFG = load_config()
AS_OF = _CFG["recon_as_of"]
_DEFAULT_ACTOR = {"reviewer": "rev-01", "finance": "fin-01", "manager": "mgr-01"}


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    ensure_action_log(conn)
    ensure_dispute_outbox(conn)
    return conn


def _df(rows) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in rows])


# --- reviewer: review queue --------------------------------------------------
def render_reviewer(conn, actor) -> None:
    st.header("复核队列 · Review Queue")
    st.caption("差异 + 证据包 → 核准(起草追款) / 驳回(误报) / 回滚。draft-not-send。")
    rows = conn.execute(
        "SELECT * FROM review_queue WHERE status='pending' "
        "ORDER BY detected_amount_usd DESC"
    ).fetchall()
    st.metric("待复核差异", len(rows))
    if not rows:
        st.info("队列为空：没有待复核差异。")
        return
    summary = _df(rows)[
        ["review_id", "invoice_id", "discrepancy_type", "severity", "detected_amount_usd"]
    ]
    st.dataframe(summary, width="stretch", hide_index=True)

    for r in rows[:25]:
        row = dict(r)
        title = (f"{row['discrepancy_type']} · {row['invoice_id']} · "
                 f"${row['detected_amount_usd']:,.2f} · {row['severity']}")
        with st.expander(title):
            st.json(json.loads(row["evidence_json"]))
            c1, c2, c3 = st.columns(3)
            rid = row["review_id"]
            if c1.button("核准→起草追款", key=f"ap-{rid}", disabled=not can_do(actor.role, "approve_discrepancy")):
                _run(actions.approve_discrepancy(conn, rid, actor, as_of=AS_OF))
            if c2.button("驳回(误报)", key=f"rj-{rid}"):
                _run(actions.reject_discrepancy(conn, rid, actor, as_of=AS_OF))
            if c3.button("回滚", key=f"rb-{rid}"):
                _run(actions.rollback(conn, rid, actor, as_of=AS_OF))


# --- finance: authorization desk ---------------------------------------------
def render_finance(conn, actor) -> None:
    st.header("授权台 · Authorization Desk")
    st.caption("待授权的追款草稿。maker-checker：发起人 ≠ 授权人。授权只改状态，不真发。")
    rows = conn.execute(
        """SELECT d.idempotency_key AS okey, d.proposer_id AS proposer,
                  r.review_id AS review_id, r.discrepancy_type AS dtype,
                  r.detected_amount_usd AS amount, r.evidence_json AS evidence_json,
                  i.invoice_no AS invoice_no, i.carrier_name AS carrier_name
           FROM dispute_outbox d
           JOIN review_queue r ON r.review_id = d.target_object
           JOIN invoices i ON i.invoice_id = r.invoice_id
           WHERE d.status='pending'
           ORDER BY r.detected_amount_usd DESC"""
    ).fetchall()
    st.metric("待授权追款", len(rows))
    if not rows:
        st.info("没有待授权的追款草稿。")
        return
    llm = LLMClient()
    for r in rows:
        row = dict(r)
        with st.expander(f"{row['dtype']} · {row['invoice_no']} · ${row['amount']:,.2f} · 发起人 {row['proposer']}"):
            letter = draft_dispute_letter(
                {
                    "carrier_name": row["carrier_name"],
                    "invoice_no": row["invoice_no"],
                    "discrepancy_type": row["dtype"],
                    "detected_amount_usd": row["amount"],
                    "evidence": json.loads(row["evidence_json"]),
                },
                llm=llm,
            )
            st.text_area("争议信草稿", letter["letter"], height=220, key=f"lt-{row['review_id']}")
            if row["proposer"] == actor.actor_id:
                st.warning("你是发起人，maker-checker 禁止你授权自己发起的追款。")
            if st.button("授权追款 (draft→authorized)", key=f"au-{row['review_id']}",
                         disabled=not can_do(actor.role, "authorize_dispute")):
                _run(actions.authorize_dispute(conn, row["review_id"], actor, as_of=AS_OF))


# --- manager: KPI board ------------------------------------------------------
def render_manager(conn) -> None:
    st.header("KPI 看板 · Manager Board")
    st.caption("待复核数 / 追回额 / 误报率 / 高额争议。仅 manager 可见。")
    counts = dict(conn.execute(
        "SELECT status, COUNT(*) FROM review_queue GROUP BY status"
    ).fetchall())
    pending = counts.get("pending", 0)
    recovered = conn.execute(
        "SELECT COALESCE(SUM(detected_amount_usd),0) FROM review_queue "
        "WHERE status IN ('approved','authorized')"
    ).fetchone()[0]
    approved = counts.get("approved", 0) + counts.get("authorized", 0)
    rejected = counts.get("rejected", 0)
    worked = approved + rejected
    fp_rate = (rejected / worked) if worked else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("待复核差异", pending)
    c2.metric("已核准追回额", f"${recovered:,.2f}")
    c3.metric("人工驳回率", f"{fp_rate:.1%}")
    c4.metric("已处置", worked)

    st.subheader("高额争议 Top 10")
    high = conn.execute(
        "SELECT review_id, invoice_id, discrepancy_type, severity, detected_amount_usd, status "
        "FROM review_queue ORDER BY detected_amount_usd DESC LIMIT 10"
    ).fetchall()
    if high:
        st.dataframe(_df(high), width="stretch", hide_index=True)
    else:
        st.info("暂无争议数据。")


def _run(result) -> None:
    if result.ok:
        st.success(f"{result.action}: {result.detail}")
    elif result.status == "maker_checker_violation":
        st.error("maker-checker 违规：发起人不能授权自己发起的追款。已写入审计。")
    elif result.status == "permission_denied":
        st.error(f"权限不足（{result.action}）。已写入审计。")
    else:
        st.error(f"{result.action} 失败：{result.status} — {result.detail}")


def main() -> None:
    conn = get_conn()

    role = st.session_state.get("role")
    if role not in ROLES:
        role = st.sidebar.selectbox("选择角色 · Role", ROLES, index=0)
    actor_id = st.sidebar.text_input("Actor ID", value=_DEFAULT_ACTOR.get(role, "user-01"))
    actor = make_actor(actor_id, role, actor_id)

    st.sidebar.markdown(f"**角色**: `{role}`  \n**执行者**: `{actor_id}`")
    st.sidebar.caption(f"LLM provider: {LLMClient().provider} · as_of {AS_OF}")
    st.title("Freight Audit Agent — 运费对账复核")

    if role == "reviewer":
        render_reviewer(conn, actor)
    elif role == "finance":
        render_finance(conn, actor)
    elif role == "manager":
        render_manager(conn)
    else:  # defensive; ROLES is closed
        st.error(f"未知角色: {role}")


main()
