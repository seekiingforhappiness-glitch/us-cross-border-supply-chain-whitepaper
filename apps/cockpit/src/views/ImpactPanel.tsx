import { useEffect, useState } from "react";
import {
  COUNTERPARTY_TYPES,
  COUNTERPARTY_TYPE_CN,
  fetchObject,
  fetchRiskImpact,
  formatInt,
  formatUsd,
  isMasked,
  MASK,
  openCoordination,
  postDecision,
  traverse,
  type CounterpartyType,
  type ObjectFields,
  type ObjectRef,
  type PanoAlert,
  type PaymentImpactRow,
  type Role,
} from "../api";
import { actorForRole, roleShort } from "../roleActors";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import { AiDispatchButton } from "./AiRuns";
import EvidenceCard from "./EvidenceCard";
import { RULE_CN, RULE_TYPE_CN, SEV_CN } from "./aiFlowModel";
import { SEV_RANK } from "./severityRank";
import type { PendingDecision } from "./zoneModel";

// 影响分析面板（下钻第三段"详情"，右栏滑出）——V10 方案 C 直接复用 B3 资产、解耦全景依赖。
// 风险类队列条目（区队列/航线队列的风险条）点开 → 这里：风险摘要 → 受影响订单行（N 条/金额合计/
// 前 8 明细）→ 波及客户（line→order→customer 归并按敞口排序）→ 处置任务入口 → **动作区占位**
// （V10-B：真实批/驳留 Streamlit 操作台，此处只给指引 + AI 建议摘要，防审批语义分叉/单量爆炸）。
// 数据源：GET /objects + traverse（apps/api 只读消费，不改一行）；掩码/缺数如实。

export interface ImpactFocus {
  eyebrow?: string; // 默认"影响分析"
  title: string; // 主名（风险 id / 客户 / 航线）
  subtitle?: string; // 上下文行（区/层/路径）
  alerts: PanoAlert[]; // 待呈现风险（≥1）；单条时最小 alert 仅需 risk_event_id，详情由 fetch 补全
  members?: { id: string; label: string; ref: ObjectRef | null; note?: string }[];
  actionHint?: string; // AI 建议摘要（如提案 proposed_action），进动作区占位
  decision?: PendingDecision; // A-1：待拍板提案 → 动作区渲染真的批准/驳回按钮（走人类决策通道）
}

interface CustRow {
  customerId: string;
  lines: number;
  exposure: number;
  masked: boolean;
}

// 规则/severity 译名改从 aiFlowModel 权威映射表取（原地重复定义且部分错译/错键——如
// R1 曾显示"延误击穿承诺"而非权威源 ux_copy.py 的"延误传导"，"missing_docs"键名也与
// ontology 实际枚举值"docs_missing"不符，从未真正命中过——B5 归一为一处映射全端共享）。
// SEV_RANK（严重度排序权重表）改从 severityRank 模块取——原地重复定义，B 批归一为一处
// 全端共享（同 corridorModel.ts / LaneQueue.tsx）。

function sevChipClass(sev: string): string {
  if (SEV_RANK[sev] >= 2) return "cp-chip red";
  if (SEV_RANK[sev] >= 1) return "cp-chip amber";
  return "cp-chip";
}

function parseIds(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw || raw === "[]") return [];
  try {
    const v = JSON.parse(raw);
    return Array.isArray(v) ? v.map(String) : [];
  } catch {
    return [];
  }
}

const LINE_FETCH_CAP = 8; // 明细行按需拉取上限（用户点击触发，非循环，有界）

// ── 付款锚风险（R19/R21，轮3-D）归并呈现 ────────────────────────────────────
// 摘要文本有"客户 CUS-0002 订单 SO-SIM-01008"而结构化区 0 条——因为这族风险锚在付款不在订单行。
// 后端 /cockpit/risk-impact 沿实际 schema 归并（payment→单据→对手方），这里照实呈现；归并不到
// （rows 空）时保留原有诚实空态文案不变。译名/可点链接与全舱同规（对象类型映射见 api.ts）。
const PAY_ANCHOR_RULES = new Set(["R19", "R21"]);
const REF_TYPE_CN: Record<string, string> = { sales_order: "订单", supplier_invoice: "供应商发票" };
const REF_TYPE_OBJ: Record<string, string> = { sales_order: "SalesOrder", supplier_invoice: "SupplierInvoice" };
const CPTY_TYPE_OBJ: Record<string, string> = { customer: "Customer", supplier: "Supplier" };

function payRowStatus(r: PaymentImpactRow): { text: string; neg: boolean } {
  if (r.overdue_days != null) return { text: `逾期 ${r.overdue_days} 天`, neg: true };
  if (r.status === "paid") return { text: r.is_anchor === false ? "已付（重复笔）" : "已付", neg: false };
  return { text: "待回款", neg: false };
}

// A-1（V13①）待拍板动作区：把批准/驳回真的搬回驾驶舱。经理角色可点，其余角色看到灰态 + 提示。
// 批准=approve_mitigation(decision='approved') 按方案回写并结单；驳回=decision='rejected' 退回专员改方案
// （approve_mitigation 的 decision 枚举仅 approved/rejected）。点击走 POST /decisions/ApproveMitigation，
// 成功→onActed（刷新队列与体征、收起详情）；失败→原样展示后端白话中文错误，不吞不美化。
// export（P0·审批闭环）：ObjectCard 打开 Task 对象卡时复用同一套批准/驳回按钮——同一条 postDecision
// 人类决策通道、同一套 manager 门控与幂等键，零新写路（任务对象卡不再是审批死胡同）。
// onSwitchRole（欠账修复）：灰态提示区里直接给「切到老板角色」内联钮，点了就地切换、不必回顶栏。
export function DecisionButtons({ decision, role, onActed, onSwitchRole }: { decision: PendingDecision; role: Role; onActed?: () => void; onSwitchRole?: (r: Role) => void }) {
  const [busy, setBusy] = useState<null | "approved" | "rejected">(null);
  const [err, setErr] = useState<string | null>(null);
  // V22③ 审批理由必填：批准/驳回都必须先写一句为什么（王总"万把刀的处置点一下就落地，连为什么都不用写"）。
  // 单个理由框同时给两个按钮用——空值时两按钮都置灰。理由随请求走 comment，后端落 action_log 审计 +
  // 处置记忆批注（decision_note）；前端置灰只是体验预判，真正必填闸门在后端 /decisions（空→422 白话）。
  const [reason, setReason] = useState("");
  // ApproveMitigation 本体 executors=[manager]——仅经理可批/驳；ops 等角色置灰并提示。前端只做体验预判，
  // 真正闸门在后端（无权也会 403 + 审计留痕），前端置灰不等于放松后端校验。
  const canDecide = role === "manager";
  const reasonOk = reason.trim().length > 0;

  const act = async (d: "approved" | "rejected") => {
    if (!canDecide || busy || !reasonOk) return;
    setBusy(d);
    setErr(null);
    try {
      await postDecision(
        "ApproveMitigation",
        { task_id: decision.taskId, decision: d, comment: reason.trim() },
        role,
        actorForRole(role),
      );
      onActed?.(); // 成功：刷新待批队列与体征、收起详情（该提案已拍板，自动移出待批）
    } catch (e) {
      setErr((e as Error).message); // 失败：后端白话中文原文（缺身份 / 无权 / 提案人不能自批 …）
      setBusy(null);
    }
  };

  if (!canDecide) {
    return (
      <div className="cp-decide">
        <div className="cp-decide__row">
          <button className="cp-decide-btn cp-decide-btn--approve" disabled>
            批准
          </button>
          <button className="cp-decide-btn cp-decide-btn--reject" disabled>
            驳回
          </button>
        </div>
        <StateHint
          kind="no-permission"
          compact
          title="需经理角色才能拍板"
          // 「当前是X」用真实当前角色（V22① finance 接入前写死"运营 ops"，切财务会显错角色）——
          // 目标角色"老板 manager"是权限事实（ApproveMitigation executors=[manager]）不随当前角色变。
          roleHint={`切到「老板 manager」才能批 / 驳；当前是${roleShort(role)}，只能看不能批。`}
        />
        {onSwitchRole && (
          <button className="cp-role-switch" onClick={() => onSwitchRole("manager")}>
            切到老板角色 →
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="cp-decide cp-decide--form">
      {/* V22③ 审批理由（必填）：批准/驳回共用，空值时下方两按钮置灰。复用既有表单类，不新增样式。 */}
      <label className="cp-form-row">
        <span className="cp-form-row__k">审批理由*</span>
        <textarea
          className="cp-form-textarea"
          value={reason}
          disabled={busy !== null}
          placeholder="必填：为什么批准或驳回——会记入审计与处置记忆"
          onChange={(e) => setReason(e.target.value)}
        />
      </label>
      <div className="cp-decide__row">
        <button className="cp-decide-btn cp-decide-btn--approve" disabled={busy !== null || !reasonOk} onClick={() => act("approved")}>
          {busy === "approved" ? "批准中…" : "批准"}
        </button>
        <button className="cp-decide-btn cp-decide-btn--reject" disabled={busy !== null || !reasonOk} onClick={() => act("rejected")}>
          {busy === "rejected" ? "驳回中…" : "驳回"}
        </button>
      </div>
      {err && (
        <div className="cp-decide__err">
          <b>没提交成功</b> · {err}
        </div>
      )}
      <div className="cp-decide__basis">批准=按方案回写并结单；驳回=退回专员改方案。理由随决定记入审计与处置记忆。经手身份 {actorForRole(role)}（原型级，真实系统换 SSO）</div>
    </div>
  );
}

// A6（B-1/V18）关闭风险：处置完成后在此确认闭环。CloseRiskEvent 本体 executors=[ops]——与
// ApproveMitigation 的 manager 正相反（专员处置执行、经理审批把关，两条通道两种身份），故这里
// canDecide 判 role==="ops"，manager 置灰（前端只做体验预判，真正闸门仍在后端 app 层原函数）。
// 表单字段收窄到 close_risk_event 的必填契约（app/actions.py 同名函数签名：risk_event_id/
// outcome/resolution_summary 必填，quality_label 可空三选一，C1 专员关闭时顺手打）。
// "二次点击确认"防误触：先点"关闭风险"只展开表单，真正提交要再点一次"确认关闭"——不用浏览器
// confirm()（与项目其余交互一致，一律走内联态而非原生弹窗）。
const RISK_TERMINAL = new Set(["resolved", "escalated"]); // 镜像 app/actions.py::RISK_TERMINAL（前端预判用，非权威源）
const CLOSE_OUTCOMES: { value: string; label: string }[] = [
  { value: "mitigated", label: "已处置（方案已批准并执行）" },
  { value: "accepted_delay", label: "接受延误（不再处置）" },
  { value: "false_alarm", label: "误报（强制关闭，联动取消未结任务）" },
  { value: "escalated", label: "升级（转上级/外部处理，非已解决）" },
];
const CLOSE_QUALITY: { value: string; label: string }[] = [
  { value: "", label: "不填" },
  { value: "effective", label: "有效" },
  { value: "partial", label: "部分有效" },
  { value: "ineffective", label: "无效" },
];

function CloseRiskButton({
  riskId,
  riskStatus,
  role,
  onActed,
  onSwitchRole,
}: {
  riskId: string;
  riskStatus: string | null;
  role: Role;
  onActed?: () => void;
  onSwitchRole?: (r: Role) => void; // 灰态内联「切到运营角色」（欠账修复：CloseRiskEvent 是 ops 动作）
}) {
  const [open, setOpen] = useState(false); // 展开态＝二次确认防误触
  const [outcome, setOutcome] = useState("mitigated");
  const [summary, setSummary] = useState("");
  const [quality, setQuality] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const canDecide = role === "ops";

  if (riskStatus && RISK_TERMINAL.has(riskStatus)) return null; // 已终态（resolved/escalated）：无按钮

  if (!canDecide) {
    return (
      <div className="cp-decide">
        <button className="cp-decide-btn" disabled>
          关闭风险
        </button>
        <StateHint
          kind="no-permission"
          compact
          title="需运营角色才能关闭"
          // 「当前是X」用真实当前角色（原写死"老板 manager"，财务/其余角色进来会显错）——目标角色
          // "运营 ops"是权限事实（CloseRiskEvent executors=[ops]）不随当前角色变。
          roleHint={`切到「运营 ops」才能关闭风险；当前是${roleShort(role)}，只能看不能关。`}
        />
        {onSwitchRole && (
          <button className="cp-role-switch" onClick={() => onSwitchRole("ops")}>
            切到运营角色 →
          </button>
        )}
      </div>
    );
  }

  if (!open) {
    return (
      <div className="cp-decide">
        <button className="cp-decide-btn" onClick={() => setOpen(true)}>
          关闭风险
        </button>
        <div className="cp-decide__basis">处置完成后在此确认闭环——点开先展开结果与小结，不会一点就关。</div>
      </div>
    );
  }

  const submit = async () => {
    if (busy || !summary.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await postDecision(
        "CloseRiskEvent",
        { risk_event_id: riskId, outcome, resolution_summary: summary.trim(), quality_label: quality || null },
        role,
        actorForRole(role),
      );
      onActed?.(); // 成功：刷新体征、收起详情（同 DecisionButtons）
    } catch (e) {
      setErr((e as Error).message); // 失败：后端白话中文原文，原样展示不吞不美化
      setBusy(false);
    }
  };

  return (
    <div className="cp-decide cp-decide--form">
      <label className="cp-form-row">
        <span className="cp-form-row__k">结果</span>
        <select className="cp-form-select" value={outcome} disabled={busy} onChange={(e) => setOutcome(e.target.value)}>
          {CLOSE_OUTCOMES.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="cp-form-row">
        <span className="cp-form-row__k">处理小结*</span>
        <textarea
          className="cp-form-textarea"
          value={summary}
          disabled={busy}
          placeholder="必填：简述怎么处置的 / 为什么可以关闭"
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <label className="cp-form-row">
        <span className="cp-form-row__k">质量评估</span>
        <select className="cp-form-select" value={quality} disabled={busy} onChange={(e) => setQuality(e.target.value)}>
          {CLOSE_QUALITY.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <div className="cp-decide__row">
        <button className="cp-decide-btn cp-decide-btn--approve" disabled={busy || !summary.trim()} onClick={submit}>
          {busy ? "关闭中…" : "确认关闭"}
        </button>
        <button
          className="cp-decide-btn"
          disabled={busy}
          onClick={() => {
            setOpen(false);
            setErr(null);
          }}
        >
          取消
        </button>
      </div>
      {err && (
        <div className="cp-decide__err">
          <b>没提交成功</b> · {err}
        </div>
      )}
      <div className="cp-decide__basis">
        非"误报"结果需该风险名下任务全部终态；"已处置"还需已有批准并执行的方案。经手身份 {actorForRole(role)}（原型级，真实系统换 SSO）
      </div>
    </div>
  );
}

// 发起新协调线程（V22② 余量清偿，缘起李珊任务3 断头路的最后一环——催办已进驾驶舱，发起也补上）。
// 协调权限组 COORD_PERMS.ManageCoordination={ops,cs,procurement,finance}（本体声明）的前端镜像——与
// AiWorkflow.tsx::COORD_UI_ROLES 严格同集；老板/合规/销售不在组内 → 不渲染（诚实，不给必 403 的假按钮）。
// 后端仍是权威（绕过 UI 直接 POST 会 403 + denied 审计）。锚在**具体 Task**（有任务上下文才能锚定协调，
// 与 CL1 语义一致：协调是某处置任务派生的对外往返）。owner 不在表单——后端缺省用发起人身份。
const COORD_UI_ROLES: Role[] = ["ops", "cs", "procurement", "finance"];

function OpenCoordForm({ taskId, role, onOpened }: { taskId: string; role: Role; onOpened?: () => void }) {
  const [open, setOpen] = useState(false);
  const [ct, setCt] = useState<CounterpartyType>("supplier");
  const [ref, setRef] = useState("");
  const [ask, setAsk] = useState("");
  const [due, setDue] = useState("");
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  if (!COORD_UI_ROLES.includes(role)) return null; // 角色门控：非协调角色不渲染发起入口

  const canSubmit = !busy && ref.trim() !== "" && ask.trim() !== "" && due !== "";
  const submit = async () => {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await openCoordination(
        { task_id: taskId, counterparty_type: ct, counterparty_ref: ref.trim(), ask: ask.trim(), next_action_due: due },
        role,
        actorForRole(role),
      );
      setReceipt(`已发起协调线程 ${res.object_id ?? ""}——去右栏「协作流」tab 跟进 / 催办。`);
      setOpen(false);
      setRef("");
      setAsk("");
      setDue("");
      onOpened?.(); // 可选通知父层；发起协调**不关面板**（不改本风险的处置态，成功回执需留在原地可见）
    } catch (e) {
      setErr((e as Error).message); // 后端白话错误原文（无权 403 / task 不存在 / 枚举非法…），不吞不美化
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="cp-coord-open">
      <button className="cp-coord-open__toggle" onClick={() => { setOpen((v) => !v); setErr(null); }} disabled={busy}>
        <Icon name="users" size={12} /> {open ? "收起" : "发起协调"}
      </button>
      {open && (
        <div className="cp-coord-open__form">
          <div className="cp-coord-open__hint">对外协调（追工厂改期 / 追货代改配 / 追客户拆单…），锚定本处置任务 <span className="num">{taskId}</span></div>
          <label className="cp-coord-open__row">
            对手方类型
            <select value={ct} onChange={(e) => setCt(e.target.value as CounterpartyType)} className="cp-coord-open__ctrl">
              {COUNTERPARTY_TYPES.map((c) => (
                <option key={c} value={c}>{COUNTERPARTY_TYPE_CN[c]}</option>
              ))}
            </select>
          </label>
          <input className="cp-coord-open__ctrl" type="text" placeholder="对手方标识（如：SUP·工厂A / 货代名）" value={ref} onChange={(e) => setRef(e.target.value)} />
          <input className="cp-coord-open__ctrl" type="text" placeholder="诉求（如：确认改期到 8/30 出货）" value={ask} onChange={(e) => setAsk(e.target.value)} />
          <label className="cp-coord-open__row">
            首个待办截止
            <input className="cp-coord-open__ctrl" type="date" value={due} onChange={(e) => setDue(e.target.value)} required />
          </label>
          <div className="cp-coord-open__acts">
            <button className="cp-coord-open__submit" onClick={submit} disabled={!canSubmit}>
              {busy ? "发起中…" : "确认发起"}
            </button>
            <button className="cp-coord-open__cancel" onClick={() => setOpen(false)} disabled={busy}>取消</button>
          </div>
        </div>
      )}
      {receipt && <div className="cp-coord-open__ok">{receipt}</div>}
      {err && <div className="cp-coord-open__err">{err}</div>}
    </div>
  );
}

export default function ImpactPanel({ focus, role, onOpenObject, onClose, onActed, onSwitchRole }: { focus: ImpactFocus; role: Role; onOpenObject: (r: ObjectRef) => void; onClose: () => void; onActed?: () => void; onSwitchRole?: (r: Role) => void }) {
  const alerts = [...focus.alerts].sort((a, b) => (SEV_RANK[b.severity] ?? 1) - (SEV_RANK[a.severity] ?? 1));
  const members = focus.members ?? [];
  const [riskIdx, setRiskIdx] = useState(0);
  const focusAlert = alerts[riskIdx] ?? null;

  const [risk, setRisk] = useState<ObjectFields | null>(null);
  const [lines, setLines] = useState<ObjectFields[] | null>(null);
  const [custRows, setCustRows] = useState<CustRow[] | null>(null);
  const [payRows, setPayRows] = useState<PaymentImpactRow[] | null>(null); // 付款锚归并行（轮3-D）
  const [taskIds, setTaskIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0); // StateHint 错误态重试驱动（重跑风险 fetch）

  useEffect(() => {
    setRiskIdx(0);
  }, [focus.title]);

  useEffect(() => {
    if (!focusAlert) {
      setRisk(null);
      setLines(null);
      setCustRows(null);
      setPayRows(null);
      setTaskIds([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setRisk(null);
    setLines(null);
    setCustRows(null);
    setPayRows(null);
    setTaskIds([]);
    const rid = focusAlert.risk_event_id;
    fetchObject("RiskEvent", rid, role)
      .then(async (r) => {
        if (cancelled) return;
        setRisk(r);
        const allIds = parseIds(r.affected_so_line_ids);
        // 付款锚风险族（R19/R21 或 affected 里是 PAY-* id）：走 /cockpit/risk-impact 归并链；
        // PAY id 不当订单行去 fetch（那条路必 404，之前正是它把结构化区打成"0 条无可归并"）。
        const payAnchored = PAY_ANCHOR_RULES.has(String(r.rule_id)) || allIds.some((id) => id.startsWith("PAY"));
        const ids = allIds.filter((id) => !id.startsWith("PAY")).slice(0, LINE_FETCH_CAP);
        const [lineRes, taskRes, payRes] = await Promise.all([
          Promise.all(ids.map((id) => fetchObject("SalesOrderLine", id, role).catch(() => null))),
          traverse("RiskEvent", rid, "task_handles_risk", role).catch(() => null),
          payAnchored ? fetchRiskImpact(rid, role).catch(() => null) : Promise.resolve(null),
        ]);
        if (cancelled) return;
        const okLines = lineRes.filter((x): x is ObjectFields => x !== null);
        setLines(okLines);
        setTaskIds(taskRes?.neighbor_ids ?? []);
        // 归并到才展示（rows 空/接口失败 → 保留原有诚实空态文案，不新造空态）
        setPayRows(payRes && payRes.anchor === "payment" && payRes.rows.length > 0 ? payRes.rows : null);

        // 波及客户：line→order→customer_id 归并 + 敞口(qty×price)排序，有界。
        const soIds = [...new Set(okLines.map((l) => String(l.so_id)).filter(Boolean))];
        const orders = await Promise.all(soIds.map((id) => fetchObject("SalesOrder", id, role).catch(() => null)));
        if (cancelled) return;
        const soToCust = new Map<string, string>();
        orders.forEach((o, i) => {
          if (o && o.customer_id) soToCust.set(soIds[i], String(o.customer_id));
        });
        const agg = new Map<string, CustRow>();
        for (const l of okLines) {
          const cust = soToCust.get(String(l.so_id));
          if (!cust) continue;
          const row = agg.get(cust) ?? { customerId: cust, lines: 0, exposure: 0, masked: false };
          row.lines += 1;
          const qty = l.qty;
          const price = l.unit_price_usd;
          if (price === MASK) row.masked = true;
          else if (typeof qty === "number" && typeof price === "number") row.exposure += qty * price;
          agg.set(cust, row);
        }
        setCustRows([...agg.values()].sort((a, b) => b.exposure - a.exposure));
      })
      .catch((e: Error) => !cancelled && setErr(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [focusAlert, role, retryTick]);

  const affectedLineCount = risk ? parseIds(risk.affected_so_line_ids).length : 0;
  const totalUsd = risk?.affected_value_usd;

  return (
    <div className="cp-impact">
      <div className="cp-impact__head">
        <div className="cp-impact__eyebrow">
          <Icon name="spark" size={13} /> {focus.eyebrow ?? "影响分析"}
        </div>
        <div className="cp-impact__title">
          {focus.subtitle && <span className="cp-impact__layer">{focus.subtitle}</span>}
          <span className="cp-impact__name">{focus.title}</span>
          {/* 提案头部补显风险编号（P2 防混淆）：待拍板提案标题同船多险时一样（"处置 delay_breach @
              SHP-x"），靠这行风险号区分是哪一条；点它可开风险对象卡。 */}
          {focus.decision && focusAlert && (
            <span
              className="cp-impact__riskno num"
              onClick={() => onOpenObject({ type: "RiskEvent", id: focusAlert.risk_event_id })}
              title={`打开风险 ${focusAlert.risk_event_id}`}
            >
              {focusAlert.risk_event_id}
            </span>
          )}
        </div>
        <button className="cp-drawer__close" onClick={onClose} aria-label="关闭影响分析">
          <Icon name="x" size={15} />
        </button>
      </div>

      <div className="cp-impact__body">
        {alerts.length === 0 ? (
          <div className="cp-impact__sect">
            <div className="cp-impact__ok">
              <Icon name="approve" size={15} /> 当前无未闭环风险
            </div>
            {focus.subtitle && <div className="cp-basis">{focus.subtitle}</div>}
          </div>
        ) : (
          <>
            {/* 风险切换（多风险时） */}
            {alerts.length > 1 && (
              <div className="cp-impact__risks">
                {alerts.map((a, i) => (
                  <button key={`${a.risk_event_id}-${i}`} className={`cp-riskchip ${i === riskIdx ? "is-active" : ""}`} onClick={() => setRiskIdx(i)}>
                    {a.rule_id && (
                      <span className={sevChipClass(a.severity)} style={{ marginRight: 4 }}>
                        {a.rule_id}
                      </span>
                    )}
                    {a.risk_event_id.replace(/^RSK-?/, "")}
                  </button>
                ))}
              </div>
            )}

            {/* 风险摘要 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">风险摘要</div>
              {err ? (
                <StateHint kind="error" compact title="无法加载风险" message={err} onRetry={() => setRetryTick((n) => n + 1)} />
              ) : !risk ? (
                loading ? (
                  <StateHint kind="loading" compact title="加载中…" />
                ) : (
                  <div className="cp-inline-load">—</div>
                )
              ) : (
                <div className="cp-impact__risk">
                  <div className="cp-impact__risk-top">
                    <span className={sevChipClass(String(risk.severity))}>{SEV_CN[String(risk.severity)] ?? String(risk.severity)}</span>
                    <span className="cp-impact__rule num">{String(risk.rule_id)}</span>
                    <span className="cp-impact__rule-cn">
                      {RULE_CN[String(risk.rule_id)] || RULE_TYPE_CN[String(risk.type)] || String(risk.type)}
                    </span>
                    <span className="cp-impact__risk-id num" onClick={() => onOpenObject({ type: "RiskEvent", id: String(risk.risk_event_id) })}>
                      {String(risk.risk_event_id)}
                    </span>
                  </div>
                  <div className="cp-impact__cause">{String(risk.root_cause ?? "—")}</div>
                  <div className="cp-impact__meta num">
                    检出 {String(risk.detected_at ?? "—")} · 状态 {String(risk.status ?? "—")}
                    {risk.shipment_id ? <> · 锚 {String(risk.shipment_id)}</> : null}
                  </div>
                </div>
              )}
            </div>

            {/* 受影响订单行 / 受影响单据（付款锚风险 R19/R21 显付款归并，轮3-D） */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                {payRows ? "受影响单据 · 付款锚" : "受影响订单行"}
                {risk && (
                  <span
                    className="cp-impact__agg"
                    title={payRows
                      ? "影响金额＝该笔风险锚定付款的金额（R19 逾期应收 / R21 重复付款）；不是处置这笔风险要花多少钱（那是「处置成本」）"
                      : "影响货值＝受影响订单行 Σqty×单价，是这笔风险牵连了多少货；不是处置这笔风险要花多少钱（那是「处置成本」，两个口径不同、并存不冲突）"}
                  >
                    {payRows ? `${payRows.length} 笔付款` : `${affectedLineCount} 条`} · {payRows ? "影响金额" : "影响货值"}{" "}
                    <b className={isMasked(totalUsd) ? "" : "gold"}>{formatUsd(totalUsd as number | string)}</b>
                  </span>
                )}
              </div>
              {payRows ? (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>付款</th>
                      <th>单据</th>
                      <th className="num">金额</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payRows.map((p) => {
                      const st = payRowStatus(p);
                      const refObj = REF_TYPE_OBJ[p.ref_type];
                      return (
                        <tr
                          key={p.payment_id}
                          className={refObj ? "is-click" : ""}
                          onClick={() => refObj && onOpenObject({ type: refObj, id: p.ref_id })}
                        >
                          <td className="name num">{p.payment_id}</td>
                          <td className="num">
                            {REF_TYPE_CN[p.ref_type] ?? p.ref_type} {p.ref_id}
                          </td>
                          <td className="num">{formatUsd(p.amount_usd)}</td>
                          <td className={st.neg ? "neg" : ""}>{st.text}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : !lines ? (
                loading ? (
                  <StateHint kind="loading" compact title="加载中…" />
                ) : (
                  <div className="cp-inline-load">—</div>
                )
              ) : lines.length === 0 ? (
                <div className="cp-masked">无可展开的订单行明细</div>
              ) : (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>订单行</th>
                      <th>SKU</th>
                      <th className="num">数量</th>
                      <th className="num">金额</th>
                      <th>状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lines.map((l) => {
                      const qty = l.qty as number;
                      const price = l.unit_price_usd;
                      const amount = typeof price === "number" && typeof qty === "number" ? qty * price : price;
                      return (
                        <tr key={String(l.so_line_id)} className="is-click" onClick={() => onOpenObject({ type: "SalesOrderLine", id: String(l.so_line_id) })}>
                          <td className="name num">{String(l.so_line_id).replace(/^SOL-?/, "")}</td>
                          <td className="num">{String(l.sku_id)}</td>
                          <td className="num">{formatInt(qty)}</td>
                          <td className="num">{formatUsd(amount as number | string)}</td>
                          <td>{String(l.line_status)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
              {risk && affectedLineCount > (lines?.length ?? 0) && <div className="cp-basis">显示前 {lines?.length} 行（共 {affectedLineCount} 行受影响）</div>}
            </div>

            {/* 波及客户（付款锚风险：payment→单据→对手方归并行——"客户 X · 订单 Y · 金额 Z"，轮3-D） */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                {payRows && payRows.some((p) => p.counterparty_type !== "customer") ? "波及对手方" : "波及客户"}
                {payRows
                  ? <span className="cp-impact__agg">{new Set(payRows.map((p) => p.counterparty_id)).size} 家</span>
                  : custRows && custRows.length > 0 && <span className="cp-impact__agg">{custRows.length} 家</span>}
              </div>
              {payRows ? (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>{payRows.some((p) => p.counterparty_type !== "customer") ? "对手方" : "客户"}</th>
                      <th>{payRows.some((p) => p.ref_type === "supplier_invoice") ? "单据" : "订单"}</th>
                      <th className="num">金额</th>
                    </tr>
                  </thead>
                  <tbody>
                    {payRows.filter((p) => p.is_anchor !== false).map((p) => {
                      const cptyObj = CPTY_TYPE_OBJ[p.counterparty_type];
                      const st = payRowStatus(p);
                      return (
                        <tr
                          key={`${p.counterparty_id}-${p.ref_id}`}
                          className={cptyObj ? "is-click" : ""}
                          onClick={() => cptyObj && onOpenObject({ type: cptyObj, id: p.counterparty_id })}
                        >
                          <td className="name num">
                            {COUNTERPARTY_TYPE_CN[p.counterparty_type as CounterpartyType] ?? p.counterparty_type} {p.counterparty_id}
                          </td>
                          <td className="num">{p.ref_id}</td>
                          <td className="num">
                            {formatUsd(p.amount_usd)}
                            {st.neg && <span className="neg">（{st.text}）</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : !custRows ? (
                loading ? (
                  <StateHint kind="loading" compact title="归并中…" />
                ) : (
                  <div className="cp-inline-load">—</div>
                )
              ) : custRows.length === 0 ? (
                <div className="cp-masked">无可归并的客户（受影响行未关联到订单）</div>
              ) : (
                <table className="cp-table cp-table--tight">
                  <thead>
                    <tr>
                      <th>客户</th>
                      <th className="num">波及行</th>
                      <th className="num">敞口</th>
                    </tr>
                  </thead>
                  <tbody>
                    {custRows.map((c) => (
                      <tr key={c.customerId}>
                        <td className="name num">{c.customerId}</td>
                        <td className="num">{c.lines}</td>
                        <td className="num">{c.masked ? formatUsd(MASK) : formatUsd(c.exposure)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            {/* 处置任务入口（打开对象卡看任务）+ 发起对外协调（V22② 余量：协调锚在具体处置任务） */}
            {taskIds.length > 0 && (
              <div className="cp-impact__sect">
                <div className="cp-impact__sect-t">处置任务</div>
                <div className="cp-links">
                  {taskIds.map((tid) => (
                    <div key={tid} className="cp-task-row">
                      <button className="cp-link-btn" onClick={() => onOpenObject({ type: "Task", id: tid })}>
                        <Icon name="propose" size={14} />
                        <span className="num">{tid}</span>
                        <span className="cp-link-btn__dir">打开处置任务 →</span>
                      </button>
                      <OpenCoordForm taskId={tid} role={role} />
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {/* 组内成员（可开对象卡） */}
        {members.length > 0 && (
          <div className="cp-impact__sect">
            <div className="cp-impact__sect-t">
              相关成员 <span className="cp-impact__agg">{members.length}</span>
            </div>
            <div className="cp-impact__members">
              {members.slice(0, 24).map((m) => (
                <button key={m.id} className={`cp-member ${m.ref ? "" : "is-plain"}`} onClick={() => m.ref && onOpenObject(m.ref)} title={m.ref ? `打开 ${m.ref.type} ${m.ref.id}` : m.label}>
                  <span className="num">{m.label.length > 18 ? m.label.slice(0, 17) + "…" : m.label}</span>
                  {m.note && <span className="cp-member__note">{m.note}</span>}
                </button>
              ))}
              {members.length > 24 && <span className="cp-member is-plain">+{members.length - 24} 更多</span>}
            </div>
          </div>
        )}

        {/* 动作区（A-1/V13①批准驳回 + B-1/V18 关闭风险）：待拍板提案在此直接拍板；无待拍板提案时
            （已批/从未有提案）改渲染关闭风险（走同一条人类决策通道，留痕到审计、maker-checker 不变）。
            Streamlit 操作台作为兜底入口保留（改期/加急等复杂处置提案仍需去那边发起）。 */}
        <div className="cp-action">
          <div className="cp-action__t">
            <Icon name="stamp" size={13} /> 动作区
          </div>
          {focus.actionHint && (
            <div className="cp-action__hint">
              <span className="cp-action__hint-k">AI 建议</span>
              <span className="cp-action__hint-v">{focus.actionHint}</span>
            </div>
          )}
          {/* 口径修复（P1，王总"差8倍会拍错优先级"）：这里的金额是执行该方案的处置成本，与上方"受影响
              订单行"的影响货值是两个不同口径的数字——原先详情面板只显影响货值、处置成本只在队列列表里，
              两处分开看像是同一个数字对不上；并排显示 + 各自标口径，让差异不再像自相矛盾。 */}
          {focus.decision && (
            <div className="cp-action__hint">
              <span className="cp-action__hint-k">处置成本</span>
              <span
                className={`cp-action__hint-v num ${isMasked(focus.decision.amountUsd) ? "" : "gold"}`}
                title="处置成本＝执行该方案预计花费，非受影响订单行的影响货值"
              >
                {formatUsd(focus.decision.amountUsd as number | string)}
              </span>
            </div>
          )}
          {focus.decision ? (
            <>
              {/* 波E 证据链（V20）：批之前先看证据——影响量化/同类先例与结局/该域信任档/备选代价。
                  卡片自管加载与诚实空态；放在批准键之前=证据先于拍板的画面语序。 */}
              <EvidenceCard taskId={focus.decision.taskId} role={role} />
              <DecisionButtons decision={focus.decision} role={role} onActed={onActed} onSwitchRole={onSwitchRole} />
            </>
          ) : (
            risk && (
              <>
                {/* ④ 让 AI 处置：ops 可发起一趟 AI 处置差事（派单→提交处置提案→停在待拍板），
                    manager 灰态"运营发起"。既有 run 则显状态徽章（幂等，不重复启动）。 */}
                <AiDispatchButton
                  riskId={String(risk.risk_event_id)}
                  riskStatus={risk.status != null ? String(risk.status) : null}
                  role={role}
                />
                <CloseRiskButton
                  riskId={String(risk.risk_event_id)}
                  riskStatus={risk.status != null ? String(risk.status) : null}
                  role={role}
                  onActed={onActed}
                  onSwitchRole={onSwitchRole}
                />
              </>
            )
          )}
          <div className="cp-action__note">
            {focus.decision
              ? "批准 / 驳回在此直接拍板（人类决策通道，实时回写并留痕）。处置完成后可在风险详情里关闭风险；改期/加急等复杂处置提案仍需去 Streamlit 操作台发起。"
              : "「让 AI 处置」发起一趟 AI 差事（提案-only，停在待拍板）；「关闭风险」在处置完成后确认闭环——都实时回写并留痕。改期/加急等复杂处置提案仍需去 Streamlit 操作台发起，驾驶舱专注「看清 + 拍板定位」。"}
          </div>
        </div>
      </div>
    </div>
  );
}
