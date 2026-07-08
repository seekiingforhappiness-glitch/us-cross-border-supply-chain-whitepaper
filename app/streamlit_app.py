"""控制塔 UI（W5 + RBAC 深化）：streamlit run app/streamlit_app.py

真·角色导航：不同角色登录后只渲染自己的工作台 tab（见 app/rbac_nav.ROLE_WORKSPACE），
不是全渲染再脱敏。经理额外拥有 KPI 总览落地页。
动作全部经 app/actions.py（权限、前置校验、审计在动作层，UI 只是壳）——导航层不放宽任何动作权限。
字段级权限（manual §6）：ops 不可见 Customer.tier；cs 不可见 est_cost_usd（保留）。
"""
import sys
from pathlib import Path

# streamlit run 只把脚本目录 app/ 放进 sys.path；显式补入项目根，
# 否则 app/actions.py 顶层 `from pipeline.outbox import ...` 会 ModuleNotFoundError。
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import html
import json
import sqlite3
from datetime import date, timedelta

import yaml
import streamlit as st

try:
    from app.actions import (assign_task, propose_mitigation, approve_mitigation,
                             close_risk_event, ensure_task_work_queue_columns)
    from app.dq_actions import assign_dq_issue, close_dq_issue
    from app.admission_actions import (create_admission_case, run_compliance_precheck,
                                       build_logistics_plan, calculate_cost_scenario,
                                       approve_quote_decision, reject_or_request_more_info)
except ImportError:  # streamlit run app/streamlit_app.py 时脚本目录在 sys.path
    from actions import (assign_task, propose_mitigation, approve_mitigation,
                         close_risk_event, ensure_task_work_queue_columns)
    from dq_actions import assign_dq_issue, close_dq_issue
    from admission_actions import (create_admission_case, run_compliance_precheck,
                                   build_logistics_plan, calculate_cost_scenario,
                                   approve_quote_decision, reject_or_request_more_info)

try:
    from app.rbac_nav import ROLE_WORKSPACE_META, TAB_LABELS, visible_tabs
    from app.data_scope import (MODES, MODE_LABELS, default_mode, resolve_actor,
                                risk_in_region_scope, scope_for_role,
                                audit_region_index, audit_in_scope)
    from app import object_workbench
    from app import standard_object_view as sov
    from app.executive_view import build_executive_summary
except ImportError:  # streamlit run app/streamlit_app.py 时脚本目录在 sys.path
    from rbac_nav import ROLE_WORKSPACE_META, TAB_LABELS, visible_tabs
    from data_scope import (MODES, MODE_LABELS, default_mode, resolve_actor,
                            risk_in_region_scope, scope_for_role,
                            audit_region_index, audit_in_scope)
    import object_workbench
    import standard_object_view as sov
    from executive_view import build_executive_summary

st.set_page_config(page_title="跨境供应链控制塔", layout="wide")
CFG = yaml.safe_load(open("config/datagen.yaml", encoding="utf-8"))
AS_OF = CFG["window"]["as_of"]
SEV_ICON = {"critical": "CRIT ", "high": "HIGH ", "medium": "MED "}


def inject_design_system():
    st.markdown("""
    <style>
    :root {
        --bg: #061012;
        --bg-2: #0a1518;
        --panel: rgba(16, 28, 32, 0.88);
        --panel-2: rgba(20, 35, 39, 0.92);
        --line: rgba(117, 226, 235, 0.16);
        --line-strong: rgba(117, 226, 235, 0.34);
        --text: #e8f4f3;
        --muted: #91a8a9;
        --cyan: #22d7e6;
        --amber: #ffb454;
        --red: #ff5d66;
        --green: #78d47d;
    }

    html, body, [data-testid="stAppViewContainer"] {
        background:
            linear-gradient(135deg, rgba(34, 215, 230, 0.07), transparent 34%),
            linear-gradient(180deg, #061012 0%, #081417 52%, #05090b 100%);
        color: var(--text);
        letter-spacing: 0;
    }

    [data-testid="stAppViewContainer"]::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        background-image:
            linear-gradient(rgba(117, 226, 235, 0.035) 1px, transparent 1px),
            linear-gradient(90deg, rgba(117, 226, 235, 0.028) 1px, transparent 1px);
        background-size: 42px 42px;
        mask-image: linear-gradient(180deg, rgba(0,0,0,0.7), transparent 78%);
    }

    .block-container {
        max-width: 1500px;
        padding-top: 1.05rem;
        padding-bottom: 2.4rem;
    }

    header[data-testid="stHeader"],
    div[data-testid="stToolbar"],
    div[data-testid="stDecoration"],
    #MainMenu,
    footer {
        display: none !important;
        visibility: hidden !important;
    }

    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(8, 17, 20, 0.98), rgba(5, 10, 12, 0.98));
        border-right: 1px solid var(--line);
    }

    section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
        gap: 0.85rem;
    }

    h1, h2, h3, p, label, span, div {
        font-family: Inter, "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
    }

    h1, h2, h3 {
        color: var(--text);
        letter-spacing: 0;
    }

    .side-brand {
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.15rem 0 0.55rem;
        border-bottom: 1px solid var(--line);
    }

    .side-mark {
        display: grid;
        place-items: center;
        width: 2.15rem;
        height: 2.15rem;
        border: 1px solid var(--line-strong);
        color: var(--cyan);
        background: rgba(34, 215, 230, 0.08);
        font-size: 0.82rem;
        font-weight: 760;
    }

    .side-title {
        font-size: 1.05rem;
        font-weight: 760;
        line-height: 1.2;
    }

    .side-subtitle {
        color: var(--muted);
        font-size: 0.72rem;
        margin-top: 0.14rem;
    }

    .command-header {
        display: flex;
        align-items: stretch;
        justify-content: space-between;
        gap: 1rem;
        padding: 1.05rem 1.15rem;
        margin-bottom: 0.85rem;
        border: 1px solid var(--line);
        background:
            linear-gradient(135deg, rgba(34, 215, 230, 0.14), transparent 38%),
            linear-gradient(180deg, rgba(18, 31, 36, 0.92), rgba(10, 18, 21, 0.92));
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.28);
    }

    .command-title h1 {
        margin: 0;
        font-size: clamp(1.55rem, 2.5vw, 2.2rem);
        line-height: 1.08;
        font-weight: 780;
    }

    .command-title p {
        margin: 0.42rem 0 0;
        color: var(--muted);
        font-size: 0.86rem;
    }

    .command-clock {
        min-width: 13rem;
        padding: 0.75rem 0.9rem;
        border-left: 2px solid var(--cyan);
        background: rgba(5, 12, 15, 0.6);
        color: var(--muted);
        font-size: 0.72rem;
        text-transform: uppercase;
    }

    .command-clock strong {
        display: block;
        color: var(--text);
        font-size: 1.1rem;
        margin-top: 0.25rem;
    }

    .signal-strip {
        display: grid;
        grid-template-columns: repeat(5, minmax(0, 1fr));
        gap: 0.75rem;
        margin-bottom: 1rem;
    }

    .signal-card {
        border: 1px solid var(--line);
        background: rgba(12, 24, 28, 0.82);
        padding: 0.78rem 0.85rem;
        min-height: 5.25rem;
    }

    .signal-card span {
        display: block;
        color: var(--muted);
        font-size: 0.72rem;
        margin-bottom: 0.3rem;
    }

    .signal-card strong {
        display: block;
        color: var(--text);
        font-size: 1.7rem;
        line-height: 1;
        font-weight: 780;
    }

    .signal-card em {
        display: block;
        margin-top: 0.38rem;
        color: var(--cyan);
        font-size: 0.72rem;
        font-style: normal;
    }

    div[data-testid="stMetric"] {
        border: 1px solid var(--line);
        background: rgba(13, 24, 27, 0.78);
        padding: 0.78rem 0.85rem;
    }

    div[data-testid="stMetric"] label,
    div[data-testid="stMetric"] [data-testid="stMetricDelta"] {
        color: var(--muted) !important;
    }

    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: var(--text);
        font-weight: 760;
    }

    div[data-testid="stTabs"] [data-baseweb="tab-list"] {
        gap: 0;
        padding: 0.25rem;
        border: 1px solid var(--line);
        background: rgba(6, 14, 17, 0.78);
    }

    div[data-testid="stTabs"] [data-baseweb="tab"] {
        min-height: 2.55rem;
        padding: 0.25rem 1rem;
        color: var(--muted);
        border-bottom: 2px solid transparent;
        font-weight: 650;
    }

    div[data-testid="stTabs"] [aria-selected="true"] {
        color: var(--cyan);
        border-bottom-color: var(--cyan);
        background: rgba(34, 215, 230, 0.08);
    }

    div[data-testid="stDataFrame"],
    div[data-testid="stForm"],
    div[data-testid="stExpander"] {
        border: 1px solid var(--line);
        background: var(--panel);
        box-shadow: 0 16px 38px rgba(0, 0, 0, 0.22);
    }

    .dark-table-wrap {
        overflow: auto;
        border: 1px solid var(--line-strong);
        background: rgba(10, 22, 25, 0.92);
        box-shadow: 0 16px 38px rgba(0, 0, 0, 0.22);
    }

    .dark-table {
        width: 100%;
        border-collapse: collapse;
        min-width: 720px;
        font-size: 0.82rem;
    }

    .dark-table th {
        position: sticky;
        top: 0;
        z-index: 1;
        background: #101f23;
        color: rgba(232, 244, 243, 0.72);
        text-align: left;
        font-weight: 660;
        padding: 0.64rem 0.7rem;
        border-bottom: 1px solid var(--line-strong);
        white-space: nowrap;
    }

    .dark-table td {
        color: rgba(232, 244, 243, 0.9);
        padding: 0.58rem 0.7rem;
        border-bottom: 1px solid rgba(117, 226, 235, 0.11);
        border-right: 1px solid rgba(117, 226, 235, 0.08);
        white-space: nowrap;
    }

    .dark-table tr:nth-child(even) td {
        background: rgba(255, 255, 255, 0.018);
    }

    .dark-table tr:hover td {
        background: rgba(34, 215, 230, 0.08);
    }

    .dark-table-empty {
        border: 1px solid var(--line);
        background: rgba(10, 22, 25, 0.72);
        padding: 0.8rem;
        color: var(--muted);
        font-size: 0.82rem;
    }

    div[data-testid="stDataFrameResizable"] {
        border: 1px solid var(--line-strong) !important;
        border-radius: 4px !important;
        background: rgba(10, 22, 25, 0.96) !important;
    }

    .stDataFrameGlideDataEditor {
        --gdg-accent-color: #22d7e6 !important;
        --gdg-accent-fg: #041012 !important;
        --gdg-accent-light: rgba(34, 215, 230, 0.16) !important;
        --gdg-text-dark: #e8f4f3 !important;
        --gdg-text-medium: rgba(232, 244, 243, 0.82) !important;
        --gdg-text-light: rgba(232, 244, 243, 0.48) !important;
        --gdg-text-header: rgba(232, 244, 243, 0.72) !important;
        --gdg-bg-cell: #0a1518 !important;
        --gdg-bg-cell-medium: #0d1b1f !important;
        --gdg-bg-header: #101f23 !important;
        --gdg-bg-header-hovered: rgba(34, 215, 230, 0.1) !important;
        --gdg-bg-header-has-focus: rgba(34, 215, 230, 0.14) !important;
        --gdg-bg-group-header: #101f23 !important;
        --gdg-bg-group-header-hovered: rgba(34, 215, 230, 0.1) !important;
        --gdg-bg-bubble: rgba(34, 215, 230, 0.1) !important;
        --gdg-bg-bubble-selected: rgba(34, 215, 230, 0.18) !important;
        --gdg-border-color: rgba(117, 226, 235, 0.16) !important;
        --gdg-horizontal-border-color: rgba(117, 226, 235, 0.12) !important;
        --gdg-link-color: #22d7e6 !important;
        --gdg-resize-indicator-color: #22d7e6 !important;
        --gdg-header-font-style: 600 13px !important;
        --gdg-base-font-style: 400 13px !important;
        --gdg-font-family: Inter, "SF Pro Display", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif !important;
    }

    div[data-testid="stForm"] {
        padding: 0.75rem 0.85rem;
    }

    .stMarkdown strong {
        color: var(--text);
    }

    .stMarkdown p,
    [data-testid="stCaptionContainer"] {
        color: var(--muted);
    }

    div[data-baseweb="select"] > div,
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stDateInput"] input,
    textarea {
        background: rgba(8, 18, 21, 0.98) !important;
        border-color: var(--line-strong) !important;
        color: var(--text) !important;
        border-radius: 4px !important;
    }

    .stButton > button,
    button[kind="primaryFormSubmit"],
    button[kind="secondaryFormSubmit"],
    button[data-testid="baseButton-secondary"] {
        border-radius: 4px !important;
        border: 1px solid rgba(34, 215, 230, 0.55) !important;
        background: linear-gradient(180deg, rgba(34, 215, 230, 0.22), rgba(34, 215, 230, 0.08)) !important;
        color: var(--text) !important;
        font-weight: 720 !important;
        min-height: 2.35rem;
    }

    .stButton > button:hover,
    button[kind="primaryFormSubmit"]:hover,
    button[kind="secondaryFormSubmit"]:hover {
        border-color: var(--cyan) !important;
        box-shadow: 0 0 0 2px rgba(34, 215, 230, 0.12) !important;
    }

    hr {
        border-color: var(--line);
    }

    @media (max-width: 900px) {
        .command-header {
            flex-direction: column;
        }
        .command-clock {
            min-width: 0;
            border-left: 0;
            border-top: 2px solid var(--cyan);
        }
        .signal-strip {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
    </style>
    """, unsafe_allow_html=True)


def db():
    con = sqlite3.connect("data/ontology.sqlite")
    con.row_factory = sqlite3.Row
    return con


def rows(sql, *a):
    with db() as con:
        ensure_task_work_queue_columns(con, AS_OF)
        return [dict(r) for r in con.execute(sql, a)]


def render_table(records, height=None):
    """Render Streamlit-friendly dark HTML tables so the app theme stays coherent."""
    if not records:
        st.markdown('<div class="dark-table-empty">无数据</div>', unsafe_allow_html=True)
        return
    columns = list(records[0].keys())
    style = f' style="max-height:{height}px"' if height else ""
    header = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    body = []
    for rec in records:
        cells = "".join("<td>{}</td>".format(html.escape("" if rec.get(c) is None else str(rec.get(c))))
                        for c in columns)
        body.append(f"<tr>{cells}</tr>")
    st.markdown(f"""
    <div class="dark-table-wrap"{style}>
        <table class="dark-table">
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(body)}</tbody>
        </table>
    </div>
    """, unsafe_allow_html=True)


def mask_tier(tier, role):
    return tier if role in ("cs", "manager") else "🔒无权查看"


def mask_cost(v, role):
    return v if role in ("finance", "manager") else "🔒无权查看"


def detail_rows(raw_detail):
    if not raw_detail:
        return []
    try:
        detail = json.loads(raw_detail)
    except (TypeError, json.JSONDecodeError):
        return [{"字段": "detail_json", "值": raw_detail}]
    if isinstance(detail, dict):
        return [{"字段": k, "值": v} for k, v in detail.items()]
    return [{"字段": "detail_json", "值": json.dumps(detail, ensure_ascii=False)}]


def show_result(r):
    if r["ok"]:
        # 成功消息存入会话状态，rerun 后仍可见（否则被刷新冲掉，用户会误以为没成功而重复点击）
        st.session_state["flash"] = "✅ " + ("；".join(r["side_effects"]) or "完成")
        st.rerun()
    else:
        st.error(r["error"])


def render_command_header(role, n_open):
    critical = rows("""SELECT count(*) c FROM risk_events
                       WHERE severity='critical' AND status NOT IN ('resolved','escalated')""")[0]["c"]
    pending_tasks = rows("""SELECT count(*) c FROM tasks
                            WHERE status IN ('assigned','in_progress')
                               OR approval_status='pending'""")[0]["c"]
    review_invoices = rows("SELECT count(*) c FROM invoices WHERE status='under_review'")[0]["c"]
    priced_cases = rows("SELECT count(*) c FROM admission_cases WHERE status='priced'")[0]["c"]
    role_label = {"ops": "物流运营", "cs": "客户成功", "manager": "经理", "sales": "销售",
                  "compliance": "合规", "finance": "财务"}[role]
    meta = ROLE_WORKSPACE_META.get(role, ROLE_WORKSPACE_META["manager"])
    # 数据域改为真实 active scope（行级范围）：读任务台当前 mode（有 task tab 才信 session），
    # 无则用角色默认。manager 恒 Global，ops/cs 如 "US · 我的任务"。不再硬编码/占位。
    _task_mode = st.session_state.get("task_scope_mode") if "task" in visible_tabs(role) else None
    scope_label = scope_for_role(role, _task_mode if _task_mode in MODES else None).label
    st.markdown(f"""
    <div class="command-header">
        <div class="command-title">
            <h1>{html.escape(meta['name'])}</h1>
            <p>跨境供应链控制塔 · 角色 {role_label} · 工作台 {html.escape(meta['domain'])}
               · 数据域 {html.escape(scope_label)}</p>
        </div>
        <div class="command-clock">
            simulation clock
            <strong>{AS_OF}</strong>
            <span>UTC+8 operational snapshot</span>
        </div>
    </div>
    <div class="signal-strip">
        <div class="signal-card"><span>未结风险</span><strong>{n_open}</strong><em>open risk events</em></div>
        <div class="signal-card"><span>Critical 队列</span><strong>{critical}</strong><em>priority lane</em></div>
        <div class="signal-card"><span>待处理任务</span><strong>{pending_tasks}</strong><em>human actions</em></div>
        <div class="signal-card"><span>对账复核</span><strong>{review_invoices}</strong><em>invoices under review</em></div>
        <div class="signal-card"><span>准入待批</span><strong>{priced_cases}</strong><em>priced cases</em></div>
    </div>
    """, unsafe_allow_html=True)


inject_design_system()


# ---------- 侧边栏 ----------
with st.sidebar:
    st.markdown("""
    <div class="side-brand">
        <div class="side-mark">CT</div>
        <div>
            <div class="side-title">控制塔</div>
            <div class="side-subtitle">Ontology OS</div>
        </div>
    </div>
    """, unsafe_allow_html=True)
    role = st.selectbox("当前角色", ["ops", "cs", "manager", "sales", "compliance", "finance"],
                        key="role",
                        format_func=lambda r: {"ops": "物流运营 ops", "cs": "客户成功 cs",
                                               "manager": "经理 manager", "sales": "销售 sales",
                                               "compliance": "合规 compliance",
                                               "finance": "财务 finance"}[r])
    actor = st.text_input("操作人", "daniel")
    st.caption(f"仿真时钟 as_of = **{AS_OF}**（D8）")
    n_open = rows("SELECT count(*) c FROM risk_events WHERE status NOT IN ('resolved','escalated')")[0]["c"]
    st.metric("未结风险", n_open)

if "flash" in st.session_state:  # 上一动作的成功回执
    st.success(st.session_state.pop("flash"))

COST_TYPES = {"rate_overbilling", "duplicate_charge", "unplanned_charge"}
# 采购三方对账风险类型（R7-R10）——任务台按此切换到采购处置提案；PO 锚点无 shipment，不进风险队列。
PROCUREMENT_TYPES = {"supplier_delay", "short_receipt", "qc_failure", "price_qty_mismatch"}
INV_STATUS_ICON = {"received": "IN ", "under_review": "REV ", "approved": "OK ", "disputed": "DSP "}

# 数据范围（呈现层）：按角色相关性把「本台焦点」风险类型置顶并标注，不删任何数据。
# 仅影响风险队列呈现次序/标注；动作层权限不受此影响。
ROLE_RISK_FOCUS = {
    "ops": {"delay_breach", "stalled", "docs_missing"},   # 运营处置全部物流类
    "cs": {"delay_breach", "stalled"},                    # 客户成功关注交付延误
    "compliance": {"docs_missing"},                       # 合规关注单证缺失
}

render_command_header(role, n_open)

# ---------- 全局 Executive 一页视图（manager 专属落地页）----------
def _signal_strip(cards):
    """复用既有 signal-card 样式渲染一行信号卡；cards=[(label, value, sub), ...]。"""
    html_cards = "".join(
        f'<div class="signal-card"><span>{html.escape(str(lbl))}</span>'
        f'<strong>{html.escape(str(val))}</strong>'
        f'<em>{html.escape(str(sub))}</em></div>'
        for lbl, val, sub in cards)
    st.markdown(f'<div class="signal-strip">{html_cards}</div>', unsafe_allow_html=True)


def render_kpi_tab():
    st.caption("经理总览 · 全局 Executive 一页视图：跨 5 场景的全域运营健康快照，"
               "来自现有对象表只读聚合（不新增对象/表/规则/动作）。")
    # 单次聚合：render 显示的数字与 test_executive_view 断言同源（build 一次，render 读它）。
    summary = build_executive_summary(db())
    cross, delay, cost = summary["cross"], summary["delay"], summary["cost"]
    proc, wh, adm = summary["procurement"], summary["warehouse"], summary["admission"]

    # ---- 顶部：全局健康（跨场景横条，复用 signal-card 样式）----
    st.markdown("#### 全局健康")
    sla = cross["tasks_by_sla"]
    _signal_strip([
        ("总未结风险", cross["total_open_risk"], "open risk events"),
        ("任务逾期", sla["overdue"], "SLA overdue"),
        ("今日到期", sla["due_today"], "due today"),
        ("升级候选", cross["escalation_candidates"], "overdue / escalated"),
        ("DQ 待处置", cross["dq_open"], "data quality open"),
    ])

    # ---- 场景 1 · 延误运营（R1-R3）----
    st.markdown("#### 延误运营　·　R1-R3")
    d = delay["by_severity"]
    dc = st.columns(5)
    dc[0].metric("开放风险", delay["open_risk"])
    dc[1].metric("critical", d["critical"])
    dc[2].metric("high", d["high"])
    dc[3].metric("medium", d["medium"])
    dc[4].metric("at_risk 订单行", delay["at_risk_so_lines"], help="line_status='at_risk' 的销售订单行")

    # ---- 场景 2 · 费用稽核（R4-R6）----
    st.markdown("#### 费用稽核　·　R4-R6")
    c = cost["by_severity"]
    cc = st.columns(5)
    cc[0].metric("开放风险", cost["open_risk"])
    cc[1].metric("critical", c["critical"])
    cc[2].metric("high", c["high"])
    cc[3].metric("under_review 发票", cost["under_review_invoices"])
    cc[4].metric("disputed 发票", cost["disputed_invoices"])

    # ---- 场景 3 · 采购（R7-R15）----
    st.markdown("#### 采购　·　R7-R15")
    pc = st.columns(6)
    pc[0].metric("开放风险", proc["open_risk"])
    pc[1].metric("三方对账异常", proc["three_way_recon"], help="R10 价量不符 + R11 票超收")
    pc[2].metric("预付款敞口", proc["prepayment_exposure"], help="R12")
    pc[3].metric("资质过期", proc["qualification_expired"], help="R13")
    pc[4].metric("单一来源", proc["single_source"], help="R14")
    pc[5].metric("maverick 采购", proc["maverick_spend"], help="R15")

    # ---- 场景 4 · 仓储库存（R16-R18）----
    st.markdown("#### 仓储库存　·　R16-R18")
    wc = st.columns(4)
    wc[0].metric("开放风险", wh["open_risk"])
    wc[1].metric("断货", wh["stockout"], help="R16")
    wc[2].metric("不可履约", wh["unfulfillable"], help="R17")
    wc[3].metric("盘点差异", wh["shrinkage"], help="R18")

    # ---- 场景 5 · 准入合规 ----
    st.markdown("#### 准入合规")
    ac_left, ac_right = st.columns([2, 1])
    with ac_left:
        st.markdown("**准入案件按状态**")
        render_table([{"准入状态": s, "数量": n} for s, n in adm["by_status"].items()]
                     or [{"准入状态": "(无)", "数量": 0}])
    with ac_right:
        st.metric("门禁触发", adm["gate_triggered"],
                  help="G1 硬门禁：severity=critical 且 evidence_status≠verified 的合规发现（禁批）")


# ---------- 风险队列 ----------
def render_risk_tab():
    c_top1, c_top2 = st.columns([1, 2])
    with c_top1:
        show_closed = st.checkbox("显示已关闭", value=False)
    # 行级数据范围：按 shipment 目的地 region 过滤（本区域 / 全部）。
    # manager 默认全部（不受限）；其余默认本区域。all-US demo 数据下「本区域」==全集，
    # 故 SHP-2026-0099 走查永不被挡，随时可切「全部」恢复全集（只过滤视图，不删数据）。
    _region = resolve_actor(role)[1]
    with c_top2:
        risk_choice = st.radio("数据范围（风险按目的地 region）", ["region", "all"],
                               index=1 if role == "manager" else 0, horizontal=True,
                               format_func=lambda m: {"region": f"本区域 {_region}",
                                                      "all": "全部"}[m],
                               key="risk_scope_mode")
    where = "" if show_closed else "WHERE r.status NOT IN ('resolved','escalated')"
    risks = rows(f"""SELECT r.*, s.eta_initial, s.eta_current, s.delay_days, s.destination_port,
                     s.destination_port_locode
                     FROM risk_events r JOIN shipments s ON s.shipment_id=r.shipment_id {where}
                     ORDER BY CASE r.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END,
                              r.affected_value_usd DESC""")
    if risk_choice != "all":
        risk_scope = scope_for_role(role, "team")  # team=同 region；manager 恒 all 不受限
        risks = [r for r in risks if risk_in_region_scope(risk_scope, r["destination_port_locode"])]
        st.caption(f"数据范围：本区域 {_region}（{len(risks)} 条风险；切「全部」即恢复全集，不删数据）")
    # 数据范围：把「本台焦点」风险类型稳定置顶并标注（不删数据，只调呈现）。
    focus = ROLE_RISK_FOCUS.get(role)
    if focus:
        st.caption(f"本台焦点：{'、'.join(sorted(focus))}（已置顶标注 ◆，其余风险仍完整可见）")
        risks = sorted(risks, key=lambda r: 0 if r["type"] in focus else 1)

    def risk_row(r):
        row = {"风险": r["risk_event_id"], "级别": f"{SEV_ICON[r['severity']]}{r['severity']}",
               "类型": r["type"], "规则": r["rule_id"], "货运": r["shipment_id"],
               "延误(天)": r["delay_days"], "影响金额($)": r["affected_value_usd"],
               "状态": r["status"]}
        if focus:
            row["焦点"] = "◆ 本台" if r["type"] in focus else ""
        return row
    render_table([risk_row(r) for r in risks], height=260)
    if risks:
        demo_idx = next((i for i, r in enumerate(risks) if r["shipment_id"] == "SHP-2026-0099"), 0)
        sel = st.selectbox("查看风险", [r["risk_event_id"] for r in risks], index=demo_idx)
        r = next(x for x in risks if x["risk_event_id"] == sel)
        st.markdown(f"**根因**：{r['root_cause']}　|　ETA {r['eta_initial']} → **{r['eta_current']}**")
        lids = json.loads(r["affected_so_line_ids"])
        if lids:
            ph = ",".join("?" * len(lids))
            lines = rows(f"""SELECT l.so_line_id, l.qty, l.promised_delivery_date, l.line_status,
                             so.so_id, c.customer_name, c.tier, k.sku_name
                             FROM sales_order_lines l JOIN sales_orders so ON so.so_id=l.so_id
                             JOIN customers c ON c.customer_id=so.customer_id
                             JOIN skus k ON k.sku_id=l.sku_id WHERE l.so_line_id IN ({ph})""", *lids)
            st.markdown("**受影响订单行**")
            render_table([{"行": x["so_line_id"], "商品": x["sku_name"], "数量": x["qty"],
                           "承诺日": x["promised_delivery_date"], "行状态": x["line_status"],
                           "客户": x["customer_name"], "客户等级": mask_tier(x["tier"], role)}
                          for x in lines])
        ilids = json.loads(r["affected_invoice_line_ids"]) if r["affected_invoice_line_ids"] else []
        if ilids:  # 费用异常：受影响账单行（行/费种/柜/金额/所属发票/vendor）
            ph = ",".join("?" * len(ilids))
            ilines = rows(f"""SELECT il.invoice_line_id, il.charge_code, il.container_no,
                              il.amount_usd, iv.invoice_id, iv.vendor_name, iv.status
                              FROM invoice_lines il JOIN invoices iv ON iv.invoice_id=il.invoice_id
                              WHERE il.invoice_line_id IN ({ph}) ORDER BY il.invoice_line_id""", *ilids)
            st.markdown("**受影响账单行**")
            render_table([{"账单行": x["invoice_line_id"], "费种": x["charge_code"],
                           "柜": x["container_no"] or "-", "金额$": x["amount_usd"],
                           "所属发票": x["invoice_id"], "vendor": x["vendor_name"],
                           "发票状态": f"{INV_STATUS_ICON.get(x['status'], '')}{x['status']}"}
                          for x in ilines])
        c1, c2 = st.columns(2)
        with c1, st.form(f"assign_{sel}"):
            st.markdown("**派发任务（A3，运营）**")
            a_role = st.selectbox("处理角色", ["ops", "cs"])
            prio = st.selectbox("优先级", ["P1", "P2", "P3"])
            due = st.date_input("任务处理截止日", date.fromisoformat(AS_OF) + timedelta(days=2),
                                help="要求处理人完成处置的期限（默认 48 小时），"
                                     "不是货物交付日期，也不是客户承诺日")
            if st.form_submit_button("派单"):
                show_result(assign_task(db(), sel, a_role, prio, due.isoformat(),
                                        actor=actor, role=role, as_of=AS_OF))
        with c2, st.form(f"close_{sel}"):
            st.markdown("**关闭风险（A6，运营）**")
            outcome = st.selectbox("结论", ["mitigated", "accepted_delay", "false_alarm", "escalated"])
            summary = st.text_input("处理小结")
            if st.form_submit_button("关闭"):
                show_result(close_risk_event(db(), sel, outcome, summary,
                                             actor=actor, role=role, as_of=AS_OF))
        # 对象工作台入口：点选的 risk → 进入 RiskEvent 富工作台（session_state 存 focus）
        st.divider()
        st.session_state["focus_risk_event_id"] = sel
        object_workbench.render_object_workbench(sel, role, actor, AS_OF, db, render_table)

# ---------- 任务处理台 ----------
def render_task_tab():
    tasks = rows("""SELECT t.*, r.severity, r.shipment_id, r.status risk_status, r.type risk_type,
                    r.affected_invoice_line_ids
                    FROM tasks t JOIN risk_events r ON r.risk_event_id=t.risk_event_id
                    ORDER BY t.task_id DESC""")
    # 行级数据范围：按 assignee 过滤（我的任务 / 本组 / 全部）。
    # manager 默认全部（监督全局不受限，即便选窄也恒全集）；ops/cs 默认「我的任务」。
    # 切「全部」即恢复全集——只过滤视图，不删数据（key=task_scope_mode 供命令栏数据域联动）。
    mode = st.radio("数据范围", list(MODES),
                    index=list(MODES).index(default_mode(role)), horizontal=True,
                    format_func=lambda m: MODE_LABELS[m], key="task_scope_mode")
    scope = scope_for_role(role, mode)
    _total = len(tasks)
    tasks = [t for t in tasks if scope.matches_task(t)]
    st.caption(f"数据域 **{scope.label}**：{len(tasks)}/{_total} 个任务"
               + ("（只看与自己相关的行；切「全部」恢复全集）" if scope.mode != "all" else ""))
    render_table([{"任务": t["task_id"], "风险": t["risk_event_id"],
                   "级别": f"{SEV_ICON[t['severity']]}{t['severity']}", "货运": t["shipment_id"],
                   "负责人": t["assignee_user_id"] or "-", "团队": t["assignee_team_id"] or "-",
                   "负责角色": t["assignee_role"], "优先级": t["priority"], "处理截止": t["due_at"],
                   "SLA": t["sla_state"] or "-", "升级": t["escalation_level"],
                   "提案": t["proposed_action"] or "-", "审批": t["approval_status"] or "-",
                   "状态": t["status"]} for t in tasks],
                 height=230)
    if tasks:
        tsel = st.selectbox("处理任务", [t["task_id"] for t in tasks])
        t = next(x for x in tasks if x["task_id"] == tsel)
        st.markdown(f"**{t['title']}**　|　负责人 `{t['assignee_user_id'] or '-'}`　"
                    f"团队 `{t['assignee_team_id'] or '-'}`　SLA `{t['sla_state'] or '-'}`　"
                    f"升级 `{t['escalation_level']}`　任务状态 `{t['status']}`　"
                    f"风险状态 `{t['risk_status']}`")
        if t["proposal_params"]:
            p = json.loads(t["proposal_params"])
            if role == "cs" and "est_cost_usd" in p:
                p["est_cost_usd"] = "🔒无权查看"
            st.markdown(f"当前提案：`{t['proposed_action']}` {p}")
        if t["status"] == "assigned" and t["risk_type"] in COST_TYPES:
            # 费用异常处置：方案改为 dispute/accept_charge/rebill_customer（finance 可提，P3）
            rinfo = rows("""SELECT s.incoterm, r.affected_value_usd FROM risk_events r
                            JOIN shipments s ON s.shipment_id=r.shipment_id
                            WHERE r.risk_event_id=?""", t["risk_event_id"])
            incoterm = rinfo[0]["incoterm"] if rinfo else "-"
            anom_val = float(rinfo[0]["affected_value_usd"]) if rinfo else 0.0
            with st.form(f"cprop_{tsel}"):
                st.markdown("**提交费用处置方案（A4，运营/财务）**")
                st.caption(f"该票 incoterm = **{incoterm}**（rebill 是否放行由 G4 责任矩阵判定）"
                           f"　异常金额 ${anom_val}")
                cact = st.selectbox("方案", ["dispute", "accept_charge", "rebill_customer"])
                creason = st.text_input("理由（dispute / accept_charge）")
                disputed = st.number_input("争议金额 $（dispute）", 0.0, step=50.0, value=anom_val)
                rebill_amt = st.number_input("转嫁金额 $（rebill_customer）", 0.0, step=50.0,
                                             value=anom_val)
                if st.form_submit_button("提交提案"):
                    cparams = {"dispute": {"reason": creason, "disputed_amount_usd": disputed},
                               "accept_charge": {"reason": creason},
                               "rebill_customer": {"rebill_amount_usd": rebill_amt,
                                                   "incoterm_basis": incoterm}}[cact]
                    show_result(propose_mitigation(db(), tsel, cact, cparams,
                                                   actor=actor, role=role, as_of=AS_OF))
        elif t["status"] == "assigned" and t["risk_type"] in PROCUREMENT_TYPES:
            # 采购三方对账处置（R7-R10）：方案改为 expedite_po/accept_receipt_variance/
            # raise_supplier_claim/dispute_supplier_invoice（ProposeMitigation 权限，走既有闭环）。
            with st.form(f"pprop_{tsel}"):
                st.markdown("**提交采购处置方案（A4，运营/财务）**")
                st.caption("延误→expedite_po / 短装→accept_receipt_variance / "
                           "质量→raise_supplier_claim / 价量→dispute_supplier_invoice")
                pact = st.selectbox("方案", ["expedite_po", "accept_receipt_variance",
                                             "raise_supplier_claim", "dispute_supplier_invoice"])
                preason = st.text_input("理由")
                pamt = st.number_input("金额 $（索赔/争议金额）", 0.0, step=100.0)
                if st.form_submit_button("提交提案"):
                    pparams = {"expedite_po": {"reason": preason},
                               "accept_receipt_variance": {"reason": preason},
                               "raise_supplier_claim": {"claim_amount_usd": pamt, "reason": preason},
                               "dispute_supplier_invoice": {"reason": preason,
                                                            "disputed_amount_usd": pamt}}[pact]
                    show_result(propose_mitigation(db(), tsel, pact, pparams,
                                                   actor=actor, role=role, as_of=AS_OF))
        elif t["status"] == "assigned":
            with st.form(f"prop_{tsel}"):
                st.markdown("**提交处置方案（A4，运营/客户成功）**")
                opts = ["reschedule", "accept_delay"] + ([] if role == "cs" else ["expedite"])
                act = st.selectbox("方案", opts,
                                   help="cs 角色不可发起加急（成本字段无权限）" if role == "cs" else None)
                new_date = st.date_input("新承诺日（reschedule）",
                                         date.fromisoformat(AS_OF) + timedelta(days=19))
                cost = st.number_input("预估加急成本 $（expedite）", 0.0, step=100.0)
                new_eta = st.date_input("加急后预计到达（expedite）",
                                        date.fromisoformat(AS_OF) + timedelta(days=5))
                reason = st.text_input("理由（accept_delay）")
                if st.form_submit_button("提交提案"):
                    params = {"reschedule": {"new_promise_date": new_date.isoformat(),
                                             "notify_customer": True},
                              "expedite": {"new_mode": "air", "est_cost_usd": cost,
                                           "expected_new_eta": new_eta.isoformat()},
                              "accept_delay": {"reason": reason}}[act]
                    show_result(propose_mitigation(db(), tsel, act, params,
                                                   actor=actor, role=role, as_of=AS_OF))
        if t["approval_status"] == "pending":
            with st.form(f"appr_{tsel}"):
                st.markdown("**审批（A5，仅经理）**")
                decision = st.radio("决定", ["approved", "rejected"], horizontal=True)
                comment = st.text_input("批注")
                if st.form_submit_button("提交审批"):
                    show_result(approve_mitigation(db(), tsel, decision, comment,
                                                   actor=actor, role=role, as_of=AS_OF))
        # 对象工作台入口：点选的 task → 进入 Task 富工作台（同 RiskEvent 的 focus 机制）
        st.divider()
        st.session_state["focus_task_id"] = tsel
        object_workbench.render_task_object_workbench(tsel, role, actor, AS_OF, db, render_table)

# ---------- 费用工作台（v0.4）----------
def render_cost_tab():
    st.caption("发票对账工作台：状态由 MatchInvoice（系统）与 A5 审批门径驱动，UI 只读呈现。")
    stat_filter = st.selectbox("发票状态筛选", ["全部", "received", "under_review", "approved", "disputed"])
    where_inv = "" if stat_filter == "全部" else f"WHERE iv.status='{stat_filter}'"
    invs = rows(f"""SELECT iv.invoice_id, iv.vendor_name, iv.vendor_type, iv.shipment_id,
                    iv.total_usd, iv.status, iv.issue_date
                    FROM invoices iv {where_inv} ORDER BY iv.invoice_id""")
    render_table([{"发票": x["invoice_id"], "vendor": x["vendor_name"], "类型": x["vendor_type"],
                   "货运": x["shipment_id"], "金额$": x["total_usd"], "开票日": x["issue_date"],
                   "状态": f"{INV_STATUS_ICON.get(x['status'], '')}{x['status']}"} for x in invs],
                 height=300)
    if invs:
        isel = st.selectbox("查看发票明细", [x["invoice_id"] for x in invs])
        iv = next(x for x in invs if x["invoice_id"] == isel)
        st.markdown(f"**{isel}**　{iv['vendor_name']}　货运 `{iv['shipment_id']}`　"
                    f"金额 ${iv['total_usd']}　状态 `{iv['status']}`")
        # 行明细 join expected_costs（基准列 + 差异列 + 行级异常标记）
        ilines = rows("""SELECT il.invoice_line_id, il.charge_code, il.container_no, il.amount_usd,
                         ec.baseline_usd
                         FROM invoice_lines il
                         LEFT JOIN expected_costs ec
                           ON ec.shipment_id=? AND ec.charge_code=il.charge_code
                           AND ec.container_no=COALESCE(il.container_no,'')
                         WHERE il.invoice_id=? ORDER BY il.invoice_line_id""",
                      iv["shipment_id"], isel)
        # 行级异常：来自本票所属风险的 affected_invoice_line_ids（并集）
        anom_ils = set()
        for rr in rows("SELECT affected_invoice_line_ids FROM risk_events WHERE shipment_id=?",
                       iv["shipment_id"]):
            if rr["affected_invoice_line_ids"]:
                anom_ils |= set(json.loads(rr["affected_invoice_line_ids"]))
        def diff(a, b):
            return round(a - b, 2) if b is not None else None
        render_table([{"账单行": x["invoice_line_id"], "费种": x["charge_code"],
                       "柜": x["container_no"] or "-", "金额$": x["amount_usd"],
                       "基准$": x["baseline_usd"] if x["baseline_usd"] is not None else "无基准",
                       "差异$": diff(x["amount_usd"], x["baseline_usd"]),
                       "异常": "!" if x["invoice_line_id"] in anom_ils else ""}
                      for x in ilines])
        # 对象工作台入口：点选的 invoice → 进入 Invoice 富工作台（同 RiskEvent 的 focus 机制）
        st.divider()
        st.session_state["focus_invoice_id"] = isel
        object_workbench.render_invoice_object_workbench(isel, role, actor, AS_OF, db, render_table)

# ---------- 采购工作台（P1 三方对账 + PurchaseOrder 富工作台）----------
def render_po_tab():
    """采购单 → PoLine × 收货 × 供票逐行对账 + 锚定采购风险(R7-R10) + 派发入口 + PO 对象工作台。

    采购风险用 po_id 锚点、无 shipment，故不在风险队列出现——本台是采购风险的派发/处置入口。
    propose/approve 在任务台完成（走既有 assign→propose→approve 闭环）。"""
    st.caption("采购三方对账工作台（P1）：采购单 → PoLine × 收货 × 供票逐行对账 + 锚定采购风险(R7-R10)。"
               "采购风险无 shipment 锚点（用 po_id），不在风险队列出现——在此派发，任务台提案/审批。")
    po_ids = [r["po_id"] for r in rows("SELECT DISTINCT po_id FROM po_lines ORDER BY po_id")]
    if not po_ids:
        st.warning("暂无采购单数据（先跑 datagen/pipeline/engine）")
        return
    # 有锚定采购风险的 PO 置顶（demo 一打开即见三方差异）
    risky = {r["po_id"] for r in rows("SELECT DISTINCT po_id FROM risk_events WHERE po_id IS NOT NULL")}
    po_ids = sorted(po_ids, key=lambda p: (p not in risky, p))
    psel = st.selectbox("查看采购单", po_ids)
    # 派发采购风险任务（A3，运营）——采购风险不在风险队列，故在此提供派发入口
    open_risks = rows("""SELECT risk_event_id, rule_id, type, severity FROM risk_events
                         WHERE po_id=? AND status='open' ORDER BY risk_event_id""", psel)
    if open_risks:
        with st.form(f"po_assign_{psel}"):
            st.markdown("**派发采购风险任务（A3，运营）**")
            rsel = st.selectbox("待派发风险",
                                [f"{r['risk_event_id']} · {r['rule_id']} {r['type']} ({r['severity']})"
                                 for r in open_risks])
            a_role = st.selectbox("处理角色", ["ops", "finance"])
            prio = st.selectbox("优先级", ["P1", "P2", "P3"])
            due = st.date_input("任务处理截止日", date.fromisoformat(AS_OF) + timedelta(days=2))
            if st.form_submit_button("派单"):
                rid = rsel.split(" ")[0]
                show_result(assign_task(db(), rid, a_role, prio, due.isoformat(),
                                        actor=actor, role=role, as_of=AS_OF))
    st.divider()
    st.session_state["focus_po_id"] = psel
    object_workbench.render_po_object_workbench(psel, role, actor, AS_OF, db, render_table)

# ---------- 对象详情：通用对象浏览器（标准对象视图兜底层）----------
def render_obj_tab():
    """通用对象浏览器：选对象类型 + 选 id → 富工作台类型给入口提示、其余渲染标准只读视图。

    标准视图（app/standard_object_view.py）是「Foundry 标准对象视图」兜底：非核心对象自动生成的
    只读视图（属性 + 关联对象，可点击导航）。四个富工作台对象（RiskEvent/AdmissionCase/Task/Invoice）
    的动作/对象级 AI 仍在各自标签，此处只做只读浏览与对象图导航（不重复渲染其 keyed 富工作台部件）。
    """
    types = sov.navigable_types()
    # 关联对象导航：读挂起的跳转目标（点关联对象按钮时写入），设定类型 + id 的 widget 状态
    pending = st.session_state.pop("obj_nav", None)
    if pending:
        st.session_state["obj_type_sel"] = pending[0]
        st.session_state[f"obj_id_sel_{pending[0]}"] = pending[1]
    st.session_state.setdefault("obj_type_sel", "Shipment")
    st.session_state.setdefault("obj_id_sel_Shipment", "SHP-2026-0099")  # 保留演示锚点
    if st.session_state["obj_type_sel"] not in types:
        st.session_state["obj_type_sel"] = "Shipment"

    otype = st.selectbox("对象类型", types, key="obj_type_sel")
    route = sov.route_object(otype)
    # Warehouse 是富对象但无独立工作台标签，故其对象中心入口就在本对象浏览器内直接渲染富工作台
    # （focus 机制 + 对象级 agent，同 PO/RiskEvent 等切片）；其余富对象的动作/AI 在各自标签。
    wh_inline = otype == "Warehouse"
    st.caption(f"对象类型 `{otype}` · 路由 `{route}` · "
               + ("仓储富工作台（库存/预留/盘点 + 锚定 R16-R18 + 对象级 AI）在此直接渲染"
                  if wh_inline else
                  "有富工作台（动作/对象级 AI 在对应标签）；此处只读浏览与对象图导航"
                  if route == "rich" else "标准只读视图（自动生成，属性 + 关联对象）"))

    table = sov.TYPE_META[otype]["table"]
    pk = sov.TYPE_META[otype]["pk"]
    ids = [r[pk] for r in rows(f"SELECT {pk} FROM {table} ORDER BY {pk} LIMIT 500")]
    cur_id = st.session_state.get(f"obj_id_sel_{otype}")
    if cur_id and cur_id not in ids and rows(f"SELECT 1 FROM {table} WHERE {pk}=?", cur_id):
        ids = [cur_id] + ids  # 导航目标不在前 500 时补进选项首位，避免 selectbox 报错
    if not ids:
        st.warning(f"{otype} 暂无数据")
        return
    if len(ids) >= 500:
        st.caption("（该类型对象较多，选择框仅列前 500；可经关联对象按钮导航到其余对象）")
    oid = st.selectbox("对象 ID", ids, key=f"obj_id_sel_{otype}")

    def _navigate(target_type, target_id):
        st.session_state["obj_nav"] = (target_type, target_id)
        st.rerun()

    if wh_inline:
        # 仓储对象工作台（同 focus 机制：点一个 Warehouse → 富工作台 + 预 scope 对象级 agent）
        st.session_state["focus_warehouse_id"] = oid
        object_workbench.render_warehouse_object_workbench(oid, role, actor, AS_OF, db, render_table)
    else:
        with db() as con:
            sov.render_standard_view(con, otype, oid, role, render_table, on_navigate=_navigate)

# ---------- DQ 处置（M6）----------
def render_dq_tab():
    show_closed_dq = st.checkbox("显示已关闭 DQ issue", value=False)
    where_dq = "" if show_closed_dq else "WHERE d.status!='closed'"
    dq_items = rows(f"""SELECT d.*, u.booking_no, u.container_no, u.event_type, u.source_system, u.reason
                        FROM dq_issues d
                        LEFT JOIN unresolved_milestones u
                          ON d.source_table='unresolved_milestones'
                         AND d.source_record_id=u.milestone_id
                        {where_dq}
                        ORDER BY CASE d.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                                      WHEN 'medium' THEN 2 ELSE 3 END,
                                 d.created_at DESC, d.dq_issue_id""")
    render_table([{"DQ issue": d["dq_issue_id"], "类型": d["issue_type"], "严重度": d["severity"],
                   "状态": d["status"], "负责人": d["assignee_user_id"] or "-",
                   "源表": d["source_table"], "源记录": d["source_record_id"],
                   "booking": d["booking_no"] or "-", "container": d["container_no"] or "-",
                   "事件": d["event_type"] or "-", "来源": d["source_system"] or "-",
                   "原因": d["reason"] or "-"} for d in dq_items],
                 height=280)
    if dq_items:
        dqsel = st.selectbox("处理 DQ issue", [d["dq_issue_id"] for d in dq_items])
        issue = next(x for x in dq_items if x["dq_issue_id"] == dqsel)
        st.markdown(f"**{dqsel}**　`{issue['issue_type']}`　状态 `{issue['status']}`　"
                    f"源 `{issue['source_table']}.{issue['source_record_id']}`")
        render_table(detail_rows(issue["detail_json"]))
        c1, c2 = st.columns(2)
        with c1, st.form(f"dq_assign_{dqsel}"):
            st.markdown("**分派 DQ issue（运营/经理/系统）**")
            assignee = st.text_input("负责人 user_id", issue["assignee_user_id"] or "u-ops-us")
            if st.form_submit_button("分派"):
                show_result(assign_dq_issue(db(), dqsel, assignee,
                                            actor=actor, role=role, as_of=AS_OF))
        with c2, st.form(f"dq_close_{dqsel}"):
            st.markdown("**关闭 DQ issue（只记录处置，不修复源系统）**")
            resolution = st.text_input("处置说明")
            if st.form_submit_button("关闭 DQ"):
                show_result(close_dq_issue(db(), dqsel, resolution,
                                           actor=actor, role=role, as_of=AS_OF))

# ---------- 准入工作台（v0.3）----------
def render_adm_tab():
    if role == "sales":
        with st.expander("➕ 新建准入案件（B1，销售）"):
            with st.form("b1"):
                custs = rows("SELECT customer_id, customer_name FROM customers ORDER BY customer_id")
                cands = rows("SELECT sku_id, sku_name FROM skus WHERE sku_status='candidate'")
                b1_c = st.selectbox("客户", [c["customer_id"] for c in custs],
                                    format_func=lambda i: f"{i} {next(x['customer_name'] for x in custs if x['customer_id']==i)}")
                b1_k = st.selectbox("候选 SKU", [k["sku_id"] for k in cands],
                                    format_func=lambda i: f"{i} {next(x['sku_name'] for x in cands if x['sku_id']==i)}") if cands else None
                b1_rt = st.selectbox("请求类型", ["new_sku", "ddp_quote", "dap_quote", "plan_review"])
                b1_it = st.selectbox("意向贸易术语", ["FOB", "DAP", "DDP", "tbd"])
                b1_dt = st.date_input("目标上线日", date.fromisoformat(AS_OF) + timedelta(days=60))
                b1_qty = st.number_input("预估月单量", 100, 100000, 1000)
                if st.form_submit_button("建案") and b1_k:
                    show_result(create_admission_case(db(), b1_c, b1_k, b1_rt, b1_it,
                                                      b1_dt.isoformat(), b1_qty,
                                                      actor=actor, role=role, as_of=AS_OF))
    acs = rows("""SELECT a.*, c.customer_name, k.sku_name FROM admission_cases a
                  JOIN customers c ON c.customer_id=a.customer_id
                  JOIN skus k ON k.sku_id=a.sku_id ORDER BY a.admission_case_id""")
    render_table([{"案件": a["admission_case_id"], "标题": a["case_title"],
                   "术语": a["incoterm_candidate"], "风险": a["risk_level"] or "-",
                   "状态": a["status"], "决定": a["decision"] or "-"} for a in acs],
                 height=240)
    if not acs:
        st.info("当前无准入案件（销售建案后可在此查看与处置）。")
        return
    # 演示锚点 AC-2026-0031 默认置顶；若该案不存在（空库/新数据）退回首项，不再 .index() 抛
    # ValueError（与风险台 SHP-2026-0099 的 next((...),0) 安全默认同规）。happy-path 索引不变。
    _acs_ids = [a["admission_case_id"] for a in acs]
    _adm_demo_idx = _acs_ids.index("AC-2026-0031") if "AC-2026-0031" in _acs_ids else 0
    asel = st.selectbox("查看案件", _acs_ids, index=_adm_demo_idx)
    ac = next(x for x in acs if x["admission_case_id"] == asel)
    st.markdown(f"**{ac['case_title']}**　状态 `{ac['status']}`　风险 `{ac['risk_level'] or '-'}`"
                + (f"　决定 `{ac['decision']}`：{ac['decision_reason']}" if ac["decision"] else ""))
    finds = rows("SELECT * FROM compliance_findings WHERE admission_case_id=?", asel)
    if finds:
        st.markdown("**合规发现**")
        render_table([{"发现": f["compliance_finding_id"], "类型": f["finding_type"],
                       "级别": f["severity"], "HTS": f["hts_candidate"] or "-",
                       "机构": f["pga_agency"], "证据": f["evidence_status"],
                       "建议": f["recommendation"]} for f in finds])
    aplans = rows("SELECT * FROM logistics_plans WHERE admission_case_id=?", asel)
    if aplans:
        st.markdown("**物流方案**")
        render_table([{"方案": p["logistics_plan_id"], "路线": p["route_type"],
                       "术语": p["incoterm"],
                       "港口": f"{p['origin_port_locode']}→{p['destination_port_locode']}",
                       "时效(天)": p["estimated_transit_days"], "SLA风险": p["sla_risk"]}
                      for p in aplans])
        pids = [p["logistics_plan_id"] for p in aplans]
        scens = rows(f"""SELECT * FROM cost_scenarios
                         WHERE logistics_plan_id IN ({','.join('?' * len(pids))})""", *pids)
        if scens:
            st.markdown("**成本情景（成本与毛利仅财务/经理可见）**")
            render_table([{"情景": s["cost_scenario_id"], "方案": s["logistics_plan_id"],
                           "类型": s["scenario_type"],
                           "报价$": mask_cost(s["quote_price_usd"], role),
                           "毛利$": mask_cost(s["gross_margin_usd"], role),
                           "毛利率": mask_cost(s["gross_margin_rate"], role)}
                          for s in scens])
    c1, c2 = st.columns(2)
    with c1:
        if role == "compliance":
            with st.form(f"b2_{asel}"):
                st.markdown("**合规预审（B2，合规）**")
                ft = st.selectbox("类型", ["hts", "pga", "certification", "labeling", "origin",
                                          "uflpa", "ad_cvd", "section_301", "platform_rule"])
                sev = st.selectbox("严重度", ["low", "medium", "high", "critical"])
                hts = st.text_input("HTS（类型为 hts 必填）", "8504.40.95")
                pga = st.selectbox("PGA 机构", ["none", "CBP", "FDA", "FCC", "CPSC", "EPA", "USDA"])
                ev = st.selectbox("证据状态", ["verified", "provided", "missing", "rejected"])
                rec = st.selectbox("建议", ["accept", "more_docs", "dap_only", "reject", "escalate"])
                if st.form_submit_button("提交预审"):
                    show_result(run_compliance_precheck(db(), asel, [{
                        "finding_title": f"{ft} review", "finding_type": ft, "severity": sev,
                        "hts_candidate": hts if ft == "hts" else "", "pga_agency": pga,
                        "evidence_status": ev, "recommendation": rec}],
                        actor=actor, role=role, as_of=AS_OF))
        if role == "ops":
            with st.form(f"b3_{asel}"):
                st.markdown("**物流方案（B3，运营）**")
                rt = st.selectbox("路线", ["ocean_fcl", "ocean_lcl", "air_freight", "express",
                                          "warehouse_fulfillment"])
                it = st.selectbox("贸易术语", ["FOB", "DAP", "DDP"])
                op = st.selectbox("起运港", ["CNYTN", "CNSHK", "CNNGB"])
                dp = st.selectbox("目的港", ["USLAX", "USLGB"])
                wr = st.selectbox("美仓区域", ["west", "central", "east", "platform"])
                lm = st.selectbox("尾程", ["UPS", "FedEx", "USPS", "OnTrac", "LTL", "platform"])
                td = st.number_input("预计总时效(天)", 3, 60, 32)
                sr = st.selectbox("SLA 风险", ["low", "medium", "high"])
                if st.form_submit_button("提交方案"):
                    show_result(build_logistics_plan(db(), asel, {
                        "plan_name": f"{rt}/{it}", "route_type": rt, "incoterm": it,
                        "origin_port_locode": op, "destination_port_locode": dp,
                        "us_warehouse_region": wr, "last_mile_method": lm,
                        "estimated_transit_days": td, "sla_risk": sr},
                        actor=actor, role=role, as_of=AS_OF))
        if role == "finance" and aplans:
            with st.form(f"b4_{asel}"):
                st.markdown("**成本情景（B4，财务）**")
                b4_p = st.selectbox("方案", [p["logistics_plan_id"] for p in aplans])
                b4_t = st.selectbox("情景", ["conservative", "base", "optimistic"])
                quote = st.number_input("报价 $", 0.0, step=100.0, value=10000.0)
                costs = {k: st.number_input(k, 0.0, step=50.0, value=v) for k, v in
                         (("product_cost_usd", 5000.0), ("first_mile_cost_usd", 300.0),
                          ("international_freight_usd", 1800.0), ("duty_tax_usd", 650.0),
                          ("customs_brokerage_usd", 180.0), ("warehouse_cost_usd", 400.0),
                          ("last_mile_cost_usd", 900.0), ("returns_allowance_usd", 150.0),
                          ("risk_buffer_usd", 250.0))}
                if st.form_submit_button("提交情景"):
                    show_result(calculate_cost_scenario(db(), b4_p, {"scenario_type": b4_t,
                                "quote_price_usd": quote, **costs},
                                actor=actor, role=role, as_of=AS_OF))
    with c2:
        if role == "manager" and aplans:
            with st.form(f"b5_{asel}"):
                st.markdown("**审批（B5，仅经理）——门禁 G1/G2/G3 自动校验**")
                b5_p = st.selectbox("批准方案", [p["logistics_plan_id"] for p in aplans])
                allsc = rows("SELECT cost_scenario_id FROM cost_scenarios WHERE logistics_plan_id=?", b5_p)
                b5_s = st.selectbox("批准情景", [s["cost_scenario_id"] for s in allsc]) if allsc else None
                b5_d = st.radio("决定", ["approve", "quote_with_conditions"], horizontal=True)
                b5_r = st.text_input("审批理由")
                b5_c = st.text_input("附加条件（quote_with_conditions）")
                if st.form_submit_button("提交审批") and b5_s:
                    show_result(approve_quote_decision(db(), asel, b5_p, b5_s, b5_d, b5_r, b5_c,
                                                       actor=actor, role=role, as_of=AS_OF))
        if role in ("compliance", "manager"):
            with st.form(f"b6_{asel}"):
                st.markdown("**拒接 / 补资料（B6）**")
                b6_d = st.radio("决定", ["more_info", "reject"], horizontal=True,
                                help="reject 仅经理可执行")
                b6_m = st.text_input("缺失文件（more_info，分号分隔）")
                b6_r = st.text_input("拒接原因（reject）")
                if st.form_submit_button("提交"):
                    show_result(reject_or_request_more_info(
                        db(), asel, b6_d, [x.strip() for x in b6_m.split(";") if x.strip()],
                        b6_r, actor=actor, role=role, as_of=AS_OF))
    # 对象工作台入口：点选的准入案 → 进入 AdmissionCase 富工作台（同 RiskEvent 的 focus 机制）
    st.divider()
    st.session_state["focus_admission_case_id"] = asel
    object_workbench.render_admission_object_workbench(asel, role, actor, AS_OF, db, render_table)

# ---------- 审计日志（ops/compliance/manager 可见；按 data_scope 过滤，manager 全量）----------
def render_log_tab():
    only_bad = st.checkbox("只看被拒/越权", value=False)
    logs = rows(f"""SELECT * FROM action_log {"WHERE result != 'ok' AND result NOT IN ('created','merged')" if only_bad else ""}
                    ORDER BY log_id DESC LIMIT 200""")
    # 行级数据范围：按目标对象 region 过滤（team-region）；manager 恒 all 不受限看全量（口径收敛）。
    scope = scope_for_role(role, "team")
    if scope.mode != "all":
        with db() as con:
            ridx = audit_region_index(con)
        logs = [x for x in logs if audit_in_scope(scope, x["target_object_id"], ridx)]
        st.caption(f"数据范围：{scope.label}（仅本区域数据范围内对象的审计；manager 视图为全量，"
                   f"切换角色即见差异）。当前 {len(logs)} 条。")
    render_table([{"#": x["log_id"], "操作人": x["actor"], "角色": x["role"], "动作": x["action"],
                   "对象": x["target_object_id"], "参数": x["params_json"],
                   "as_of": x["as_of_date"], "结果": x["result"]} for x in logs],
                 height=420)


# ---------- 真·角色导航：按 ROLE_WORKSPACE 只渲染该角色可见的工作台 tab ----------
TAB_RENDERERS = {
    "kpi": render_kpi_tab, "risk": render_risk_tab, "task": render_task_tab,
    "cost": render_cost_tab, "po": render_po_tab, "obj": render_obj_tab, "dq": render_dq_tab,
    "adm": render_adm_tab, "log": render_log_tab,
}
_keys = visible_tabs(role)
for _key, _tab in zip(_keys, st.tabs([TAB_LABELS[k] for k in _keys])):
    with _tab:
        TAB_RENDERERS[_key]()
