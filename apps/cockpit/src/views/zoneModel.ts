// 指挥墙 / 区队列的纯数据层（无 React、无副作用）——V10 方案 C。
// 两职责，全部从 vitals 载荷现取，零硬编码零编造，掩码/缺数如实透传：
//  1) summaryLines(zone)：该区卡片"最要紧的 2-3 行摘要"（老板扫一眼最想知道的），
//     每行的选型见下方各 builder 注释；masked→无权查看、missing→无数据，绝不填 0 冒充。
//  2) zoneQueue(zone)：下钻四段式第二段"工作队列"——把该区 detail 里的天然条目列表重组为
//     排序队列（排序沿用 API 现成序：金额降序/达成率升序/缺口降序/等待时长）；无天然条目列表的
//     区（钱/履约/AI 是存量聚合指标非逐条）返回 null，由聚合上下文承接，不硬造队列。
import {
  formatInt,
  formatPct,
  formatUsd,
  isMasked,
  isMissing,
  MASK_TEXT,
  type Missing,
  type ObjectRef,
  type Zone,
  type ZoneId,
} from "../api";
import type { IconName } from "../components/Icons";

// ── 区元数据（图标 + 短名；headline 是比率的区加 % 语义）──────────────────────
export const ZONE_ICON: Record<ZoneId, IconName> = {
  money: "money",
  fulfillment: "box",
  customers: "person",
  suppliers: "factory",
  inventory: "rack",
  ai: "chip",
  decisions: "stamp",
};
export const ZONE_SHORT: Record<ZoneId, string> = {
  money: "钱 · 费用",
  fulfillment: "履约 · 准交",
  customers: "客户 · 敞口",
  suppliers: "供应商",
  inventory: "库存",
  ai: "AI 运营账",
  decisions: "待我拍板",
};
// headline 为比率的区（0-1 → 百分比）
export const RATE_ZONES: ReadonlySet<ZoneId> = new Set<ZoneId>(["fulfillment", "suppliers"]);

// ── 值渲染态（掩码/缺数/真实）────────────────────────────────────────────────
export type ValState = "real" | "missing" | "masked";
export interface SummaryLine {
  label: string;
  value: string;
  state: ValState;
  tone?: "pos" | "neg" | "gold";
}

const trunc = (s: string, n = 15) => (s.length > n ? s.slice(0, n - 1) + "…" : s);

/** 一个可能被掩码/缺失的标量 → 展示串 + 态。fmt 只作用于真实值。 */
function cell(v: unknown, fmt: (x: number | string) => string): { text: string; state: ValState } {
  if (isMasked(v)) return { text: MASK_TEXT, state: "masked" };
  if (v === null || v === undefined) return { text: "无数据", state: "missing" };
  return { text: fmt(v as number | string), state: "real" };
}

type D = Record<string, unknown>;

// ── headline 渲染（指挥墙大数字）：与体征带同规，masked/missing 有态 ──────────
export function headlineOf(z: Zone): { text: string; state: ValState } {
  const v = z.headline_value;
  if (isMasked(v)) return { text: MASK_TEXT, state: "masked" };
  if (v === null || v === undefined) return { text: "无数据", state: "missing" };
  if (z.headline_unit === "usd") return { text: formatUsd(v), state: "real" };
  if (typeof v === "string") return { text: v, state: "real" }; // AI 区"N 检 / M 提案"
  if (RATE_ZONES.has(z.zone)) return { text: formatPct(v), state: "real" };
  return { text: formatInt(v), state: "real" };
}

// ═══════════════════════════ 摘要行（每区 2-3 行）═══════════════════════════
// 选型原则：headline 已答"这个区的总分"，摘要行补"老板接着最想追问的两件事"，且都是可回查
// 的一手字段（不与 headline 简单重复）。掩码/缺数如实。
function moneySummary(d: D): SummaryLine[] {
  // 在途压了多少钱 + 系统帮我拦回多少钱（headline 已给"未闭环敞口"）
  const itv = d.in_transit_value;
  const lines: SummaryLine[] = [];
  if (isMissing(itv)) {
    lines.push({ label: "在途货值", value: "无数据", state: "missing" });
  } else {
    const v = itv as { value_usd: number | string; in_transit_shipments?: number };
    const c = cell(v.value_usd, formatUsd);
    lines.push({
      label: "在途货值",
      value: c.state === "real" && v.in_transit_shipments != null ? `${c.text} · ${v.in_transit_shipments} 票` : c.text,
      state: c.state,
    });
  }
  const blk = d.intercepted_overbilling as { value_usd: number | string; resolved_r4_risks: number } | undefined;
  if (blk) {
    const c = cell(blk.value_usd, formatUsd);
    lines.push({
      label: "被拦回超收",
      value: c.state === "real" ? `${c.text} · ${blk.resolved_r4_risks} 起R4闭环` : c.text,
      state: c.state,
      tone: c.state === "real" ? "gold" : undefined,
    });
  }
  // 应收/应付水位（G2/V8-② payments 表）：在外未收的钱、其中已逾期的 + 要付的钱——一行两问
  lines.push(moneyFlowLine(d));
  return lines;
}

function moneyFlowLine(d: D): SummaryLine {
  const recv = d.receivables;
  const pay = d.payables;
  if (isMissing(recv) || isMissing(pay)) {
    return { label: "应收", value: "无数据", state: "missing" };
  }
  const r = recv as { amount_usd: number | string; overdue: { amount_usd: number | string } | Missing };
  const p = pay as { amount_usd: number | string };
  const rc = cell(r.amount_usd, formatUsd);
  const pc = cell(p.amount_usd, formatUsd);
  if (rc.state !== "real" || pc.state !== "real") {
    // 掩码/缺数如实：金额键与其余钱区指标同规，两值任一非 real 就不拼数字避免半掩半露
    return { label: "应收", value: rc.state === "masked" ? MASK_TEXT : rc.text, state: rc.state === "masked" || pc.state === "masked" ? "masked" : "missing" };
  }
  const overdueMissing = isMissing(r.overdue);
  const overdueText = overdueMissing ? "逾期无数据" : `逾期 ${formatUsd((r.overdue as { amount_usd: number | string }).amount_usd)}`;
  const overdueAmt = overdueMissing ? null : (r.overdue as { amount_usd: number | string }).amount_usd;
  return {
    label: "应收",
    value: `${rc.text}（${overdueText}）｜应付 ${pc.text}`,
    state: "real",
    tone: typeof overdueAmt === "number" && overdueAmt > 0 ? "neg" : undefined,
  };
}

function fulfillmentSummary(d: D, alertCount: number): SummaryLine[] {
  const customs = d.customs_blocked as { value: number } | undefined;
  const hist = d.delay_histogram as { delayed_shipments: number } | undefined;
  return [
    { label: "在途延误", value: `${formatInt(alertCount)} 票`, state: "real", tone: alertCount > 0 ? "neg" : undefined },
    customs
      ? { label: "清关卡点", value: `${formatInt(customs.value)} 票`, state: "real", tone: customs.value > 0 ? "neg" : undefined }
      : { label: "清关卡点", value: "无数据", state: "missing" },
    hist ? { label: "历史延误票", value: `${formatInt(hist.delayed_shipments)} 票`, state: "real" } : { label: "历史延误票", value: "无数据", state: "missing" },
  ];
}

function customersSummary(d: D): SummaryLine[] {
  const top = (d.top_exposure as { customer_name: string; exposure_usd: number | string }[]) ?? [];
  const linesTotal = d.affected_line_ids_total as number | undefined;
  const out: SummaryLine[] = [];
  if (top.length === 0) {
    out.push({ label: "最大敞口", value: "当前无客户被波及", state: "real" });
  } else {
    const c = cell(top[0].exposure_usd, formatUsd);
    out.push({ label: "最大敞口", value: `${trunc(top[0].customer_name, 12)} ${c.text}`, state: c.state, tone: c.state === "real" ? "neg" : undefined });
  }
  out.push({ label: "波及订单行", value: linesTotal != null ? `${formatInt(linesTotal)} 条` : "无数据", state: linesTotal != null ? "real" : "missing" });
  return out;
}

function suppliersSummary(d: D): SummaryLine[] {
  const delivery = d.delivery_hit_rate;
  const defect = d.defect_top;
  const recon = d.recon_diff_r7_r13 as { open_risks: number; amount_usd: number | string } | undefined;
  const r14 = d.single_source_r14 as { value: number } | undefined;
  const out: SummaryLine[] = [];
  // 最差交期供应商（缺收货域则退为对账差异）
  if (!isMissing(delivery) && delivery) {
    const dv = delivery as { worst_suppliers: { supplier_name: string; rate: number | null }[] };
    const w = dv.worst_suppliers?.[0];
    if (w) out.push({ label: "最差交期", value: `${trunc(w.supplier_name, 12)} ${formatPct(w.rate)}`, state: "real", tone: "neg" });
  }
  if (out.length === 0 && recon) {
    out.push({ label: "对账差异", value: `${recon.open_risks} 起 ${formatUsd(recon.amount_usd)}`, state: isMasked(recon.amount_usd) ? "masked" : "real", tone: recon.open_risks > 0 ? "neg" : undefined });
  }
  // 缺陷率 Top（缺收货域则退为单一依赖）
  if (!isMissing(defect) && defect) {
    const df = defect as { top: { supplier_name: string; avg_defect_ppm: number }[] };
    const t = df.top?.[0];
    if (t) out.push({ label: "缺陷率Top", value: `${trunc(t.supplier_name, 12)} ${formatInt(t.avg_defect_ppm)}ppm`, state: "real", tone: "neg" });
  }
  if (out.length < 2 && r14) {
    out.push({ label: "单一依赖", value: `${formatInt(r14.value)} SKU（R14）`, state: "real", tone: r14.value > 0 ? "neg" : undefined });
  }
  return out.slice(0, 3);
}

function inventorySummary(d: D): SummaryLine[] {
  const variance = d.count_variance;
  const resc = d.rescuable as { risks_checked: number; lines_checked: number; lines_fully_savable: number } | undefined;
  const out: SummaryLine[] = [];
  if (isMissing(variance)) {
    out.push({ label: "盘点差异", value: "无数据", state: "missing" });
  } else if (variance) {
    const v = variance as { count: number; abs_variance_units: number };
    out.push({ label: "盘点差异", value: `${formatInt(v.count)} 批 · ${formatInt(v.abs_variance_units)} 件`, state: "real", tone: v.count > 0 ? "neg" : undefined });
  }
  if (resc) {
    out.push(
      resc.lines_checked > 0
        ? { label: "现货救延误", value: `${resc.lines_fully_savable}/${resc.lines_checked} 行可全救`, state: "real", tone: "pos" }
        : { label: "现货救延误", value: `${formatInt(resc.risks_checked)} 起延误已复算`, state: "real" },
    );
  }
  return out;
}

function aiSummary(d: D): SummaryLine[] {
  const all = d.all_time as { proposals: number; approval_rate: number | null } | undefined;
  const mem = d.resolution_memory;
  const out: SummaryLine[] = [];
  if (all) out.push({ label: "累计通过率", value: `${formatPct(all.approval_rate)} · ${formatInt(all.proposals)} 提案`, state: "real", tone: "pos" });
  if (!isMissing(mem) && mem) {
    const m = mem as { total: number; with_cited_precedents: number; hit_rate: number | null };
    out.push({ label: "处置记忆命中", value: `${formatPct(m.hit_rate)} · ${m.with_cited_precedents}/${m.total}`, state: "real" });
  } else if (isMissing(mem)) {
    out.push({ label: "处置记忆", value: "无数据", state: "missing" });
  }
  return out;
}

function decisionsSummary(d: D): SummaryLine[] {
  const pend = (d.pending_proposals as { title: string; amount_usd: number | string | null }[]) ?? [];
  const overdue = d.overdue_tasks;
  const escalated = d.escalated_tasks;
  const out: SummaryLine[] = [];
  if (pend.length === 0) {
    out.push({ label: "最高金额提案", value: "当前无待批", state: "real" });
  } else {
    const c = cell(pend[0].amount_usd, formatUsd);
    out.push({ label: "最高金额提案", value: c.state === "real" ? `${c.text} · ${trunc(pend[0].title, 10)}` : c.text, state: c.state, tone: c.state === "real" ? "gold" : undefined });
  }
  if (isMissing(overdue) || isMissing(escalated)) {
    out.push({ label: "超期 / 升级", value: "SLA 无数据（模拟世界）", state: "missing" });
  } else {
    const o = (overdue as { value: number }).value;
    const e = (escalated as { value: number }).value;
    out.push({ label: "超期 / 升级", value: `${formatInt(o)} / ${formatInt(e)}`, state: "real", tone: o + e > 0 ? "neg" : undefined });
  }
  return out;
}

export function summaryLines(z: Zone): SummaryLine[] {
  const d = z.detail as D;
  switch (z.zone) {
    case "money":
      return moneySummary(d);
    case "fulfillment":
      return fulfillmentSummary(d, z.alert_count);
    case "customers":
      return customersSummary(d);
    case "suppliers":
      return suppliersSummary(d);
    case "inventory":
      return inventorySummary(d);
    case "ai":
      return aiSummary(d);
    case "decisions":
      return decisionsSummary(d);
  }
}

// ═══════════════════════════ 下钻队列（第二段）═══════════════════════════
export type DrillTarget =
  | { kind: "risk"; riskId: string; title: string; subtitle?: string; actionHint?: string }
  | { kind: "object"; ref: ObjectRef; title: string };

export interface QueueCell {
  text: string;
  num?: boolean;
  tone?: "pos" | "neg" | "gold";
  masked?: boolean;
}
export interface QueueRow {
  key: string;
  badge?: { text: string; tone: "red" | "amber" | "neutral" };
  cells: QueueCell[];
  drill: DrillTarget | null;
}
export interface QueueSpec {
  columns: { label: string; num?: boolean }[];
  rows: QueueRow[];
  /** 排序/口径说明（画面底注），全部为 API 现成序，不在前端二次排序造数。 */
  basis: string;
}

const money = (v: number | string | null | undefined): QueueCell => ({ text: formatUsd(v), num: true, tone: isMasked(v) ? undefined : "gold", masked: isMasked(v) });

function decisionsQueue(d: D): QueueSpec {
  const pend = (d.pending_proposals as {
    task_id: string;
    risk_event_id: string | null;
    title: string;
    priority: string | null;
    proposed_action: string | null;
    assignee_role: string | null;
    amount_usd: number | string | null;
  }[]) ?? [];
  return {
    columns: [{ label: "提案" }, { label: "动作" }, { label: "指派" }, { label: "金额", num: true }],
    basis: "按金额降序、等待时长（API 口径）——最贵/等最久的在前",
    rows: pend.map((p) => ({
      key: p.task_id,
      badge: p.priority ? { text: p.priority, tone: p.priority === "P1" ? "red" : "amber" } : undefined,
      cells: [{ text: p.title }, { text: p.proposed_action ?? "—" }, { text: p.assignee_role ?? "—" }, money(p.amount_usd)],
      drill: p.risk_event_id
        ? { kind: "risk", riskId: p.risk_event_id, title: p.title, subtitle: "待拍板 · 提案", actionHint: p.proposed_action ?? undefined }
        : { kind: "object", ref: { type: "Task", id: p.task_id }, title: p.title },
    })),
  };
}

function customersQueue(d: D): QueueSpec {
  const top = (d.top_exposure as { customer_id: string; customer_name: string; exposure_usd: number | string; open_risks: number; affected_lines: number }[]) ?? [];
  return {
    columns: [{ label: "客户" }, { label: "敞口", num: true }, { label: "风险", num: true }, { label: "波及行", num: true }],
    basis: "按敞口（去重订单行 Σ qty×单价）降序 Top（API 口径）",
    rows: top.map((c) => ({
      key: c.customer_id,
      badge: { text: `${c.open_risks} 险`, tone: "red" },
      cells: [{ text: c.customer_name }, money(c.exposure_usd), { text: formatInt(c.open_risks), num: true }, { text: formatInt(c.affected_lines), num: true }],
      drill: { kind: "object", ref: { type: "Customer", id: c.customer_id }, title: c.customer_name },
    })),
  };
}

function suppliersQueue(d: D): QueueSpec | null {
  const delivery = d.delivery_hit_rate;
  if (isMissing(delivery) || !delivery) return null; // 缺收货域→无队列，聚合上下文承接
  const dv = delivery as { worst_suppliers: { supplier_id: string; supplier_name: string; pos_measured: number; rate: number | null }[] };
  const worst = dv.worst_suppliers ?? [];
  return {
    columns: [{ label: "供应商" }, { label: "达成率", num: true }, { label: "PO 数", num: true }],
    basis: "按交期达成率升序（最差在前，API 口径）",
    rows: worst.slice(0, 20).map((s) => ({
      key: s.supplier_id,
      badge: s.rate == null ? undefined : s.rate < 0.7 ? { text: "低", tone: "red" } : s.rate < 0.85 ? { text: "关注", tone: "amber" } : undefined,
      cells: [{ text: s.supplier_name }, { text: formatPct(s.rate), num: true, tone: s.rate != null && s.rate < 0.85 ? "neg" : undefined }, { text: formatInt(s.pos_measured), num: true }],
      drill: { kind: "object", ref: { type: "Supplier", id: s.supplier_id }, title: s.supplier_name },
    })),
  };
}

function inventoryQueue(d: D): QueueSpec | null {
  const br = d.safety_breaches as { positions: { inventory_position_id: string; sku_id: string; warehouse_id: string; available_qty: number; safety_stock: number; gap: number }[] } | undefined;
  const pos = br?.positions ?? [];
  if (pos.length === 0) return null; // 无击穿明细→无队列（盘点差异只有计数无逐条，见聚合上下文）
  return {
    columns: [{ label: "SKU" }, { label: "仓" }, { label: "现货", num: true }, { label: "安全线", num: true }, { label: "缺口", num: true }],
    basis: "按缺口（安全线−现货）降序（API 口径）",
    rows: pos.map((p) => ({
      key: p.inventory_position_id,
      badge: { text: `-${p.gap}`, tone: "red" },
      cells: [{ text: p.sku_id }, { text: p.warehouse_id }, { text: formatInt(p.available_qty), num: true }, { text: formatInt(p.safety_stock), num: true }, { text: `-${formatInt(p.gap)}`, num: true, tone: "neg" }],
      drill: { kind: "object", ref: { type: "Sku", id: p.sku_id }, title: p.sku_id },
    })),
  };
}

/** 该区的工作队列；null = 该区无天然逐条列表（存量聚合指标），由聚合上下文承接，不硬造。 */
export function zoneQueue(z: Zone): QueueSpec | null {
  switch (z.zone) {
    case "decisions":
      return decisionsQueue(z.detail as D);
    case "customers":
      return customersQueue(z.detail as D);
    case "suppliers":
      return suppliersQueue(z.detail as D);
    case "inventory":
      return inventoryQueue(z.detail as D);
    default:
      return null; // money / fulfillment / ai
  }
}
