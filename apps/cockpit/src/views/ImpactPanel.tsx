import { useEffect, useState } from "react";
import {
  fetchObject,
  formatInt,
  formatUsd,
  isMasked,
  MASK,
  postDecision,
  traverse,
  type ObjectFields,
  type ObjectRef,
  type PanoAlert,
  type Role,
} from "../api";
import { actorForRole } from "../roleActors";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import { AiDispatchButton } from "./AiRuns";
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

// A-1（V13①）待拍板动作区：把批准/驳回真的搬回驾驶舱。经理角色可点，其余角色看到灰态 + 提示。
// 批准=approve_mitigation(decision='approved') 按方案回写并结单；驳回=decision='rejected' 退回专员改方案
// （approve_mitigation 的 decision 枚举仅 approved/rejected）。点击走 POST /decisions/ApproveMitigation，
// 成功→onActed（刷新队列与体征、收起详情）；失败→原样展示后端白话中文错误，不吞不美化。
function DecisionButtons({ decision, role, onActed }: { decision: PendingDecision; role: Role; onActed?: () => void }) {
  const [busy, setBusy] = useState<null | "approved" | "rejected">(null);
  const [err, setErr] = useState<string | null>(null);
  // ApproveMitigation 本体 executors=[manager]——仅经理可批/驳；ops 等角色置灰并提示。前端只做体验预判，
  // 真正闸门在后端（无权也会 403 + 审计留痕），前端置灰不等于放松后端校验。
  const canDecide = role === "manager";

  const act = async (d: "approved" | "rejected") => {
    if (!canDecide || busy) return;
    setBusy(d);
    setErr(null);
    try {
      await postDecision(
        "ApproveMitigation",
        { task_id: decision.taskId, decision: d, comment: d === "approved" ? "驾驶舱批准" : "驾驶舱驳回" },
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
          roleHint="顶栏切到「老板 manager」才能批 / 驳；当前是运营 ops，只能看不能批。"
        />
      </div>
    );
  }

  return (
    <div className="cp-decide">
      <div className="cp-decide__row">
        <button className="cp-decide-btn cp-decide-btn--approve" disabled={busy !== null} onClick={() => act("approved")}>
          {busy === "approved" ? "批准中…" : "批准"}
        </button>
        <button className="cp-decide-btn cp-decide-btn--reject" disabled={busy !== null} onClick={() => act("rejected")}>
          {busy === "rejected" ? "驳回中…" : "驳回"}
        </button>
      </div>
      {err && (
        <div className="cp-decide__err">
          <b>没提交成功</b> · {err}
        </div>
      )}
      <div className="cp-decide__basis">批准=按方案回写并结单；驳回=退回专员改方案。经手身份 {actorForRole(role)}（原型级，真实系统换 SSO）</div>
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
}: {
  riskId: string;
  riskStatus: string | null;
  role: Role;
  onActed?: () => void;
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
          roleHint="顶栏切到「运营 ops」才能关闭风险；当前是老板 manager，只能看不能关。"
        />
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

export default function ImpactPanel({ focus, role, onOpenObject, onClose, onActed }: { focus: ImpactFocus; role: Role; onOpenObject: (r: ObjectRef) => void; onClose: () => void; onActed?: () => void }) {
  const alerts = [...focus.alerts].sort((a, b) => (SEV_RANK[b.severity] ?? 1) - (SEV_RANK[a.severity] ?? 1));
  const members = focus.members ?? [];
  const [riskIdx, setRiskIdx] = useState(0);
  const focusAlert = alerts[riskIdx] ?? null;

  const [risk, setRisk] = useState<ObjectFields | null>(null);
  const [lines, setLines] = useState<ObjectFields[] | null>(null);
  const [custRows, setCustRows] = useState<CustRow[] | null>(null);
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
      setTaskIds([]);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setRisk(null);
    setLines(null);
    setCustRows(null);
    setTaskIds([]);
    const rid = focusAlert.risk_event_id;
    fetchObject("RiskEvent", rid, role)
      .then(async (r) => {
        if (cancelled) return;
        setRisk(r);
        const ids = parseIds(r.affected_so_line_ids).slice(0, LINE_FETCH_CAP);
        const [lineRes, taskRes] = await Promise.all([
          Promise.all(ids.map((id) => fetchObject("SalesOrderLine", id, role).catch(() => null))),
          traverse("RiskEvent", rid, "task_handles_risk", role).catch(() => null),
        ]);
        if (cancelled) return;
        const okLines = lineRes.filter((x): x is ObjectFields => x !== null);
        setLines(okLines);
        setTaskIds(taskRes?.neighbor_ids ?? []);

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

            {/* 受影响订单行 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                受影响订单行
                {risk && (
                  <span className="cp-impact__agg">
                    {affectedLineCount} 条 · 合计 <b className={isMasked(totalUsd) ? "" : "gold"}>{formatUsd(totalUsd as number | string)}</b>
                  </span>
                )}
              </div>
              {!lines ? (
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

            {/* 波及客户 */}
            <div className="cp-impact__sect">
              <div className="cp-impact__sect-t">
                波及客户
                {custRows && custRows.length > 0 && <span className="cp-impact__agg">{custRows.length} 家</span>}
              </div>
              {!custRows ? (
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

            {/* 处置任务入口（打开对象卡看任务） */}
            {taskIds.length > 0 && (
              <div className="cp-impact__sect">
                <div className="cp-impact__sect-t">处置任务</div>
                <div className="cp-links">
                  {taskIds.map((tid) => (
                    <button key={tid} className="cp-link-btn" onClick={() => onOpenObject({ type: "Task", id: tid })}>
                      <Icon name="propose" size={14} />
                      <span className="num">{tid}</span>
                      <span className="cp-link-btn__dir">打开处置任务 →</span>
                    </button>
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
          {focus.decision ? (
            <DecisionButtons decision={focus.decision} role={role} onActed={onActed} />
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
