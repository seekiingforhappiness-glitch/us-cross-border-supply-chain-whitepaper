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
  type Role,
  type SupplierComplianceDimension,
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

// 七区固定序（单一权威源）：manager 现七区墙 = 此序；CommandWall 的告警优先重排以此为同分位次序。
// 迁自 CommandWall.tsx 的局部 FIXED_ORDER（V24 归一到数据层，供 ROLE_WALL.manager / VALID_BLOCK_IDS /
// 墙渲染共用一份，避免"两处七区序"漂移）。
export const FIXED_ORDER_IDS: ZoneId[] = ["money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions"];

/** F·P1（轮3 老周/林律"无拍板权角色也被说等你拍板"）：区短名的角色适配——非 manager 没有审批权
 *  （后端 403），"待我拍板"这个第一人称对他们不属实，改中性"待批提案"；manager/未知角色维持原名。
 *  只改措辞不改数据：审批门控仍全在后端，前端不复制权限规则。 */
export function zoneShort(zone: ZoneId, role?: Role): string {
  if (zone === "decisions" && role && role !== "manager") return "待批提案";
  return ZONE_SHORT[zone];
}

// ═══════════════════════════ ROLE_WALL 角色化首屏配置（V24）═══════════════════════════
// 缘起：Daniel 亲验"各角色展示页面完全一样，本质是对每个角色的理解不够深——负责什么、看什么、
// 决定什么都没仔细思考"。规格 docs/superpowers/specs/2026-07-20-role-based-cockpit.md 定七角色
// 职责模型 → 首屏区块序。本表是**纯声明式**数据（无 React、无副作用），CommandWall 按它渲染
// （墙循环从写死七区改读本配置）。这是 V14 接缝③ ViewConfig 的第一次真实消费（角色缺省集，
// 用户自定义留二期）。
//
// 三条铁律（照规格 §设计原则，不发明语义）：
//  1) **首屏收窄≠权限收窄**：不渲染某区≠看不到——后端 vitals 一次下发全区数据（脱敏由 API 按
//     X-Role 同源执行，本表不含任何权限规则），本表只挑"这个角色首屏先摆哪几张、什么序、什么标题"；
//     未摆的区其对象仍经对象卡/搜索全量可达。
//  2) **同构骨架异构内容 / 零新端点**：区块类型以复用现有 zone 区卡为主（kind:"zone"）；spec 点名的
//     角色专属组合块（sales/compliance 的"我的准入案"）用**已在 vitals 载荷里**的 customers 区
//     admission_funnel 组合渲染（kind:"admission"），不新增后端端点。
//  3) **口径诚实**：角色化标题只换"这个角色怎么称呼这张卡"，绝不改这张卡背后的数字口径
//     （如非 manager 的 decisions 卡数字仍是全系统待批数，标题不谎称"只有我组的"——"我组"过滤在
//     下钻队列的 chip 里，见 ZoneQueue"我组的"）。
export type WallBlockId = ZoneId | "admission";
export interface WallBlock {
  /** 区块 id：zone id（复用区卡）或 "admission"（准入组合块）。合法集见 VALID_BLOCK_IDS。 */
  id: WallBlockId;
  kind: "zone" | "admission";
  /** kind==="zone" 时 = 渲染哪张 vitals 区卡；admission 块无 zone。 */
  zone?: ZoneId;
  /** 角色化标题（口径诚实，见铁律 3）。 */
  title: string;
  /** 默认下钻目标区（点卡进入的区队列）。zone 块 = 自身；admission 块 = customers（准入漏斗数据家）。 */
  drill: ZoneId;
}

const zb = (zone: ZoneId, title: string): WallBlock => ({ id: zone, kind: "zone", zone, title, drill: zone });
const adm = (title: string): WallBlock => ({ id: "admission", kind: "admission", title, drill: "customers" });

// 七角色首屏区块序（规格 §七角色职责模型 逐条落实）。区块序 = 该角色 10 秒扫屏的从上到下顺序。
// manager 保持现七区序（FIXED_ORDER）——它本就是监督全域视角；渲染时仍由 sortZones 告警优先重排
// （见 CommandWall），故 manager 画面与改前 byte-identical，本表 manager 项仅供完整性断言 + 文档。
export const ROLE_WALL: Record<Role, WallBlock[]> = {
  // 经理：监督 + 全域审批。现七区墙不动（任务硬约束 + 规格明示），标题用 ZONE_SHORT 原名。
  manager: [
    zb("money", ZONE_SHORT.money),
    zb("fulfillment", ZONE_SHORT.fulfillment),
    zb("customers", ZONE_SHORT.customers),
    zb("suppliers", ZONE_SHORT.suppliers),
    zb("inventory", ZONE_SHORT.inventory),
    zb("ai", ZONE_SHORT.ai),
    zb("decisions", ZONE_SHORT.decisions),
  ],
  // 运营：物流执行者（唯一 CloseRiskEvent 持有者）。①我组处置+超时 ②在途异常（延误+清关直达）
  // ③库存救援 ④供应商交期风向（只读）。不渲染钱区聚合、客户敞口榜（非其决策域，明细仍经对象卡达）。
  // 规格 ops③"逾期协调线程"无独立区卡 → 由右栏协作流 tab 承载（见歧义清单）。
  ops: [
    zb("decisions", "我组处置 · 超时告警"),
    zb("fulfillment", "在途异常 · 延误 / 清关卡点"),
    zb("inventory", "库存救援 · 现货救延误"),
    zb("suppliers", "供应商交期风向（只读）"),
  ],
  // 客服：客户面（R19 催收提案主力）。①我组处置（催收）②受影响客户（敞口榜+出险订单）
  // ③履约准交（对客户承诺）。不渲染供应商/库存/钱区聚合。规格 cs③"客户协调线程"→右栏协作流。
  cs: [
    zb("decisions", "我组处置 · 催收"),
    zb("customers", "受影响客户 · 敞口 / 出险订单"),
    zb("fulfillment", "履约准交 · 对客户承诺"),
  ],
  // 采购：供应商面（R7-R15 域提案）。①供应商绩效红榜（合规排序/R22/R23 都在此区摘要）②我组处置。
  // 规格 procurement 的绩效红榜/采购异常队列/资质临期三块同源于 suppliers 区卡摘要，故合为一张富卡。
  // 不渲染客户敞口/履约/钱区聚合。规格 procurement④"供应商协调线程"→右栏协作流。
  procurement: [
    zb("suppliers", "供应商绩效 · 对账 / 资质"),
    zb("decisions", "我组处置任务"),
  ],
  // 财务：钱面（R4-R6/R21 处置主力）。①现金水位+应收应付+费用异常（钱区升为首位，一张卡覆盖）
  // ②我组处置（R21 对账直达）。规格 finance 的费用异常队列/发票漏斗/逾期应收榜同源于 money 区卡。
  // 不渲染库存/供应商交期榜。
  finance: [
    zb("money", "现金水位 · 应收应付 / 费用异常"),
    zb("decisions", "我组处置 · 对账"),
  ],
  // 合规：合规面（RejectOrRequestMoreInfo 持有者、V23② 协调权）。①准入案队列（卡点步骤显性）
  // ②供应商合规红榜（UFLPA/资质，队列里"按合规风险"排序默认可用）③清关卡点。不渲染钱区（本就掩码）
  // /库存/客户敞口。规格 compliance④"合规协调线程"→右栏协作流、⑤"证据导出"→对象卡/证据卡入口。
  compliance: [
    adm("准入案 · 卡点步骤"),
    zb("suppliers", "供应商合规 · UFLPA / 资质"),
    zb("fulfillment", "清关卡点"),
  ],
  // 销售：准入发起 + 客户成单面（数据面最窄，页面最简洁是正确形态——规格明示）。①我的准入案进度
  // （卡在哪步/缺什么）②客户履约状态。规格 sales③"被驳回/需补件警示"= 准入块内的 needs_more_info/
  // rejected 计数（同块承载，不另立卡）。不渲染其余全部运营区。
  sales: [
    adm("我的准入案 · 卡在哪步 / 缺什么"),
    zb("fulfillment", "客户履约状态"),
  ],
};

/** 合法区块 id 全集（完整性断言用）：七区 + 准入组合块。 */
export const VALID_BLOCK_IDS: ReadonlySet<WallBlockId> = new Set<WallBlockId>([...FIXED_ORDER_IDS, "admission"]);

/** 某角色首屏摆出的 zone 区 id 集合（focus 的告警池按此收窄，只在该角色关心的区里挑最高告警）。 */
export function roleWallZoneIds(role: Role): Set<ZoneId> {
  return new Set(ROLE_WALL[role].filter((b) => b.kind === "zone" && b.zone).map((b) => b.zone as ZoneId));
}

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
  // 现金水位预警上浮（P2，陈会计"最要命的信号藏最深"）：原先 net_cash_14d 只在钱区下钻底部
  // （ZoneContext.tsx NetCash14dCard）才看得到，卡片摘要看不出来——补一行同源数据，击穿阈值时告警色。
  const cashLine = cashWatchLine(d);
  if (cashLine) lines.push(cashLine);
  return lines;
}

/** 14 天净流出预警摘要行：字段缺失（非本次改动范围的世界/版本）→ 如实跳过，不硬造。breach 是
 *  状态位，镜像 ZoneContext.tsx::NetCash14dCard 的口径——即便金额被掩码，告警灯本身仍如实显示。 */
function cashWatchLine(d: D): SummaryLine | null {
  const c = d.net_cash_14d;
  if (c === undefined) return null; // 字段本身不存在——不臆造，直接不显示这行
  if (isMissing(c)) return { label: "现金水位预警", value: "无数据", state: "missing" };
  const v = c as { value_usd: number | string; window_days: number; threshold_usd: number | string; breach: boolean };
  const vc = cell(v.value_usd, formatUsd);
  const breachTag = v.breach ? " · 击穿阈值" : "";
  return {
    label: "现金水位预警",
    value: vc.state === "real" ? `未来${v.window_days}天净流出 ${vc.text}${breachTag}` : `${vc.text}${breachTag}`,
    state: vc.state,
    tone: v.breach ? "neg" : undefined,
  };
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
  const r22 = d.perf_degradation_r22 as { open_risks: number; amount_usd: number | string } | undefined;
  const r23 = d.qual_expiry_r23 as { open_risks: number } | undefined;
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
  // V23① R22/R23：有告警才占摘要位（AI 检出的供应商风险优先于缺陷率 Top 上卡面）
  if (r22 && r22.open_risks > 0) {
    out.push({ label: "绩效劣化", value: `${formatInt(r22.open_risks)} 家 ${formatUsd(r22.amount_usd)}（R22）`, state: isMasked(r22.amount_usd) ? "masked" : "real", tone: "neg" });
  }
  if (r23 && r23.open_risks > 0) {
    out.push({ label: "资质预警", value: `${formatInt(r23.open_risks)} 证（R23）`, state: "real", tone: "neg" });
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
    out.push({ label: "最高处置成本", value: "当前无待批", state: "real" });
  } else {
    const c = cell(pend[0].amount_usd, formatUsd);
    // 口径修复（P1，王总"差8倍会拍错优先级"）：这里的金额是提案的处置成本（执行该方案的花费），
    // 不是受影响订单行货值——两者曾无区分地都叫"金额"，与影响面板的货值合计相差可达数倍。
    out.push({ label: "最高处置成本", value: c.state === "real" ? `${c.text} · ${trunc(pend[0].title, 10)}` : c.text, state: c.state, tone: c.state === "real" ? "gold" : undefined });
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

// ═══════════════════════════ 准入组合块（V24，sales/compliance 首屏）═══════════════════════════
// 规格点名 sales"我的准入案"/compliance"准入案队列（卡点步骤显性）"为角色专属组合块。准入案没有独立
// 的 vitals 区（七区无准入区），但**准入漏斗数据早已在载荷里**——customers 区 detail.admission_funnel
// （见 apps/api/cockpit.py::_zone_customers）。故本块零新端点，直接从已下发的 customers 区读取漏斗，
// 按状态机呈现"卡在哪步/缺什么"。模拟世界无 admission_cases 表 → 漏斗为 Missing → 卡面诚实空态
// （不填 0 冒充）。金额无关（漏斗只有计数），无脱敏问题。
export interface AdmissionFunnel {
  by_status: Record<string, number>;
  cases_total: number;
}

// 准入状态机白话名（Daniel 规则：术语必配白话）。口径源 = _zone_customers 的 status order。
export const ADMISSION_STATUS_CN: Record<string, string> = {
  draft: "草稿",
  in_precheck: "预审中",
  plan_ready: "方案就绪",
  priced: "已报价",
  quote_with_conditions: "有条件报价",
  approved: "已批准",
  needs_more_info: "需补件",
  rejected: "已驳回",
};
// 流转中（尚未到终态/卡点）的状态集——用于"审批中"汇总（不含 approved 终态、needs_more_info/rejected 卡点）。
const _ADMISSION_IN_REVIEW = ["draft", "in_precheck", "plan_ready", "priced", "quote_with_conditions"];

/** 从整套 zones 里读 customers 区的准入漏斗。缺 customers 区 / 缺漏斗字段 → undefined；该世界无准入域
 *  （Missing 形状）→ 原样返回 Missing（调用方画诚实空态）。 */
export function readAdmissionFunnel(zones: Zone[]): AdmissionFunnel | Missing | undefined {
  const c = zones.find((z) => z.zone === "customers");
  if (!c) return undefined;
  const f = (c.detail as D).admission_funnel;
  if (isMissing(f)) return f;
  if (f && typeof f === "object" && "by_status" in (f as object)) return f as AdmissionFunnel;
  return undefined;
}

/** 准入块需要处理的件数（需补件 + 已驳回）——卡面告警数 / focus"准入卡点"计数同源。 */
export function admissionActionable(f: AdmissionFunnel): number {
  return (f.by_status.needs_more_info ?? 0) + (f.by_status.rejected ?? 0);
}

/** 准入块摘要三行（审批中 / 需补件 / 已驳回），全部现取漏斗计数、缺则 0（真·计数为 0，非缺数）。 */
export function admissionSummary(f: AdmissionFunnel): SummaryLine[] {
  const bs = f.by_status;
  const inReview = _ADMISSION_IN_REVIEW.reduce((s, k) => s + (bs[k] ?? 0), 0);
  const needsMore = bs.needs_more_info ?? 0;
  const rejected = bs.rejected ?? 0;
  return [
    { label: "审批流转中", value: `${formatInt(inReview)} 件`, state: "real" },
    { label: `需补件（${ADMISSION_STATUS_CN.needs_more_info}）`, value: `${formatInt(needsMore)} 件`, state: "real", tone: needsMore > 0 ? "neg" : undefined },
    { label: `已驳回（${ADMISSION_STATUS_CN.rejected}）`, value: `${formatInt(rejected)} 件`, state: "real", tone: rejected > 0 ? "neg" : undefined },
  ];
}

// ═══════════════════════════ 下钻队列（第二段）═══════════════════════════
// A-1（V13①）：待拍板提案下钻时带上的"决策上下文"——影响面板动作区据此渲染【批准】【驳回】。
// taskId 是审批目标（approve_mitigation 按 task_id 拍板）；仅待拍板区 decisionsQueue 会设置它。
export interface PendingDecision {
  taskId: string;
  proposedAction: string | null;
  amountUsd: number | string | null;
}
export type DrillTarget =
  | { kind: "risk"; riskId: string; title: string; subtitle?: string; actionHint?: string; decision?: PendingDecision }
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
  /** 头部"共 N 条"标注（防静默截断）：全量数与展示行数一致时=「共 N 条」，被截时=「共 N 条·显示前 M
   *  条」如实标注。缺省不显（多数区展示即全量、无截断风险，不占头部）。 */
  countLabel?: string;
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
  // 全量待批数（=卡片大数字）：后端 pending_total 现取；缺则退为展示行数。展示行数 < 全量 = 被截断，
  // 头部如实标"显示前 M 条"（P1/P2 防静默截断：不让"卡片 22 vs 列表 20 行"再对不上）。
  const total = typeof d.pending_total === "number" ? (d.pending_total as number) : pend.length;
  const countLabel =
    pend.length < total ? `共 ${total} 条 · 显示前 ${pend.length} 条 · 按处置成本降序` : `共 ${total} 条 · 按处置成本降序`;
  return {
    // P2 防混淆：补"风险"列显 risk_event_id——同船多险时标题（"处置 delay_breach @ SHP-x"）一样、
    // 靠风险编号区分是哪一条（数据非错，是原来没把已在载荷里的 risk_event_id 显出来）。
    // P1 口径修复（王总"差8倍会拍错优先级"）：这一列原叫"金额"，实为提案的处置成本（执行该方案的
    // 花费）而非受影响订单行货值——影响面板另有一个货值合计，两口径并存无标注会被读成自相矛盾/数据错。
    columns: [{ label: "提案" }, { label: "风险" }, { label: "动作" }, { label: "指派" }, { label: "处置成本", num: true }],
    countLabel,
    // L·P3（轮3 老周"没有图例不知道 P2 算不算严重"）：basis 行补一句优先级图例（徽章渲染在通用
    // WorkQueue，列头/徽章不支持 title，图例落在紧贴表格下方的本行）。
    basis:
      "按处置成本（执行该方案的花费，非受影响订单行货值）降序、等待时长（API 口径）——最贵/等最久的在前。行首优先级徽章：P0 最急 → P3 最缓，数字越小越要先处理",
    rows: pend.map((p) => ({
      key: p.task_id,
      badge: p.priority ? { text: p.priority, tone: p.priority === "P1" ? "red" : "amber" } : undefined,
      cells: [
        { text: p.title },
        { text: p.risk_event_id ?? "—", num: true },
        { text: p.proposed_action ?? "—" },
        { text: p.assignee_role ?? "—" },
        money(p.amount_usd),
      ],
      // 待拍板提案下钻带 decision 上下文（taskId/动作/金额）→ 影响面板动作区渲染批准/驳回按钮（A-1）。
      drill: p.risk_event_id
        ? {
            kind: "risk",
            riskId: p.risk_event_id,
            title: p.title,
            subtitle: "待拍板 · 提案",
            actionHint: p.proposed_action ?? undefined,
            decision: { taskId: p.task_id, proposedAction: p.proposed_action, amountUsd: p.amount_usd },
          }
        : { kind: "object", ref: { type: "Task", id: p.task_id }, title: p.title },
    })),
  };
}

function customersQueue(z: Zone): QueueSpec {
  const d = z.detail as D;
  const top = (d.top_exposure as { customer_id: string; customer_name: string; exposure_usd: number | string; open_risks: number; affected_lines: number }[]) ?? [];
  // H·P2（轮3 苏苏"标 13 告警只数出 5 行，剩下 8 个藏哪儿"）：后端本区载荷只带敞口 Top5
  // （apps/api/cockpit.py::_zone_customers [:5]），全量逐家列表不在载荷里——不硬造"展开全部"假按钮，
  // 头部如实标"Top N · 共 M 家"（M=headline_value=有风险敞口客户数，与卡片告警数同源同值）。
  const total = typeof z.headline_value === "number" ? z.headline_value : null;
  const countLabel =
    total != null && total > top.length
      ? `Top ${top.length} · 共 ${total} 家风险敞口客户`
      : total != null
        ? `共 ${total} 家风险敞口客户`
        : undefined;
  return {
    columns: [{ label: "客户" }, { label: "敞口", num: true }, { label: "风险", num: true }, { label: "波及行", num: true }],
    countLabel,
    basis: "按敞口（去重订单行 Σ qty×单价）降序取前 5（API 口径）——未上榜客户敞口更小，暂无逐家全量列表",
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
  const dv = delivery as {
    worst_suppliers: {
      supplier_id: string;
      supplier_name: string;
      pos_measured: number;
      rate: number | null;
      // K·P1 合规三键：仅 compliance/manager 载荷带（本体 visibleTo；其他角色根本不带，非掩码）。
      // uflpa_risk_flag/qual_abnormal 是后端规整/派生 bool——判定规则单一来源在后端，前端只读。
      uflpa_risk_flag?: boolean;
      qual_abnormal?: boolean;
      factory_audit_status?: string | null;
      compliance_docs_status?: string | null;
    }[];
  };
  const worst = dv.worst_suppliers ?? [];
  // K·P1（轮3 林律"让我一家家点开核对 UFLPA 是体力活"）：合规维度摘要（仅授权角色载荷有此键）。
  const comp = d.compliance_dimension as SupplierComplianceDimension | undefined;
  const compSorted = comp?.available && comp.sort === "compliance";
  // J·P2（轮3 老周/林律"列表 PO 数 153 vs 对象卡关系采购单 167 对不上"）：两数是两种口径并存，
  // 非数据错——此列 pos_measured 只计"有收货记录、计入达成率分母"的采购单（后端 JOIN goods_receipts，
  // 见 _zone_suppliers），对象卡关系区"采购单"是该供应商全部采购单（含未收货，SUP-0010 实证
  // 167 全量 / 153 有收货）。列头点明 + basis 行白话讲清，不掩盖也不改后端。
  const baseBasis =
    "「PO 数（有收货）」只计有收货记录、计入达成率分母的采购单；对象卡关系区的「采购单」是全部采购单（含未收货），数字更大是口径差非数据错";
  const orderBasis = compSorted
    ? "按合规风险排序（UFLPA 命中优先、次按资质显式异常、再按交期达成率升序，API 口径）。"
    : "按交期达成率升序（最差在前，API 口径）。";
  // 零阳性诚实空态（sim 世界全库 UFLPA=0 时排序仍要能用）：后端 note 白话原文直出，不前端另造。
  const compNote = comp?.available ? (comp.note ?? "") : (comp?.reason ?? "");
  return {
    columns: [{ label: "供应商" }, { label: "达成率", num: true }, { label: "PO 数（有收货）", num: true }],
    basis: `${orderBasis}${baseBasis}${compNote ? `。${compNote}` : ""}`,
    rows: worst.slice(0, 20).map((s) => ({
      key: s.supplier_id,
      // 合规红/琥珀标优先于交期标（合规视角下 UFLPA 是首要风险信号）；载荷没带合规键（未授权角色/
      // 缺列世界）→ 原交期徽标原样（数据没有就不渲染，不编造）。
      badge:
        s.uflpa_risk_flag === true
          ? { text: "UFLPA", tone: "red" as const }
          : s.qual_abnormal === true
            ? { text: "资质异常", tone: "amber" as const }
            : s.rate == null
              ? undefined
              : s.rate < 0.7
                ? { text: "低", tone: "red" as const }
                : s.rate < 0.85
                  ? { text: "关注", tone: "amber" as const }
                  : undefined,
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
      return customersQueue(z);
    case "suppliers":
      return suppliersQueue(z.detail as D);
    case "inventory":
      return inventoryQueue(z.detail as D);
    default:
      return null; // money / fulfillment / ai
  }
}

// ═══════════════════════════ 今日焦点条（V22⑤，指挥墙顶部横条）═══════════════════════════
// 回答"30 秒说出今天最要紧的三件事"（陌生人测试李珊"七张卡全喊急" / 王总"默认世界差点全用空样本
// 做判断"，docs/research/2026-07-19-ux-stranger-round2.md §三-14 与"候 Daniel"段）。三条固定优先级
// 现算，规则写死可解释（不做 AI 排序），全部取自本次 vitals 载荷已解析的 zones/detail——零新端点
// 零新请求：
//   优先级 1：待拍板区 pending>0 →「N 条提案等你拍板，最高处置成本 $X」
//   优先级 2：钱区 net_cash_14d.breach=true →「未来 N 天净流出 $X 击穿阈值」
//   优先级 3：告警数最高的其余区（不含钱/待拍板——两区各自已有专属优先级，无论是否触发都不重复计入
//             "其余区"池，避免同一区被数两次）→「〈区名〉N 条告警：〈该区 headline 一句话〉」
// 某优先级无数据即跳过顺延（数组 filter 天然靠前补位，不留空位）；三条全部无数据 → 返回空数组，
// 调用方须整条不渲染（不摆空架子）。金额一律走 cell()（与卡片摘要行同一套掩码/缺数处理，不开新洞）。
export interface FocusItem {
  key: string;
  zone: ZoneId; // 点击跳转目标区（复用 onZone 下钻）
  text: string;
  source: string; // title 属性：数据出处白话，供追溯口径
}

function decisionsFocus(zones: Zone[], role?: Role): FocusItem | null {
  const z = zones.find((zz) => zz.zone === "decisions");
  if (!z) return null;
  const d = z.detail as D;
  const pend = (d.pending_proposals as { amount_usd: number | string | null; assignee_role: string | null }[]) ?? [];
  const total = typeof d.pending_total === "number" ? (d.pending_total as number) : pend.length;
  if (total <= 0 || pend.length === 0) return null; // 无待批提案，本优先级无数据
  // pending_proposals 已按处置成本降序（API 口径，同 decisionsQueue basis），首条即最高处置成本。
  const c = cell(pend[0].amount_usd, formatUsd);
  // 自队待批联动（V22 任务1；缘起：财务陌生人"自己的活要靠运气才能翻到"）：非 manager 角色（老板是
  // 收件人全集，不点）在首屏焦点条直接点出"你组 M 条"，M 由 pending_proposals 现算（每行带 assignee_role）。
  // 诚实门：仅当列表未被后端 cap(50) 截断（pend.length===total）时才可信全量，截断则不显自队数（不拿被截
  // 样本反推）；M=0 也不显（无自队待批，不添噪音）。点击仍跳待拍板区——去那里可用"我组的" chip 精确过滤。
  let mineNote = "";
  if (role && role !== "manager" && pend.length === total) {
    const mine = pend.filter((p) => p.assignee_role === role).length;
    if (mine > 0) mineNote = `（你组 ${formatInt(mine)} 条）`;
  }
  // F·P1（轮3 老周/林律）："等你拍板"只对 manager 属实（审批权在老板，其余角色批会被后端 403）——
  // 非 manager 改"候老板审批"；数据（total/mineNote/金额）一字不动，纯措辞角色适配。
  const verb = role && role !== "manager" ? "候老板审批" : "等你拍板";
  return {
    key: "focus-decisions",
    zone: "decisions",
    text: `${formatInt(total)} 条提案${verb}${mineNote}，最高处置成本 ${c.text}`,
    source: `来自：${zoneShort("decisions", role)}区当前值`,
  };
}

function cashFocus(zones: Zone[]): FocusItem | null {
  const z = zones.find((zz) => zz.zone === "money");
  if (!z) return null;
  const c = (z.detail as D).net_cash_14d;
  if (c === undefined || isMissing(c)) return null; // 字段不存在/该世界无此域，本优先级无数据
  const v = c as { value_usd: number | string; window_days: number; breach: boolean };
  if (!v.breach) return null; // 未击穿阈值，本优先级无数据
  const vc = cell(v.value_usd, formatUsd);
  return {
    key: "focus-cash",
    zone: "money",
    text: `未来${v.window_days}天净流出 ${vc.text} 击穿阈值`,
    source: "来自：钱区现金水位预警当前值",
  };
}

// V24：告警池按角色首屏区收窄——只在"该角色摆在首屏的 zone 区"里挑最高告警区（钱/待拍板两区
// 各有专属优先级，不进"其余区"池，避免重复计入）。manager 首屏 = 全七区，收窄后池 = 全区 −
// {money,decisions}，与改前 byte-identical（现状不动的保证）。
function alertFocus(zones: Zone[], role: Role): FocusItem | null {
  const wallZones = roleWallZoneIds(role);
  const pool = zones.filter((z) => wallZones.has(z.zone) && z.zone !== "money" && z.zone !== "decisions" && z.alert_count > 0);
  if (pool.length === 0) return null;
  const top = [...pool].sort((a, b) => b.alert_count - a.alert_count)[0];
  // 一句话取该区摘要首行（各区已按"最要紧"排首位，如供应商=最差交期、库存=盘点差异）——headline
  // 大数字与告警计数字段可能不同源（库存 headline=0 但告警 34），拼一起像自相矛盾，摘要首行没有此坑。
  const s0 = summaryLines(top)[0];
  const hl = headlineOf(top);
  const oneLiner = s0 ? `${s0.label} ${s0.value}` : `${top.headline_label} ${hl.text}`;
  return {
    key: `focus-alert-${top.zone}`,
    zone: top.zone,
    text: `${ZONE_SHORT[top.zone]} ${formatInt(top.alert_count)} 条告警：${oneLiner}`,
    source: `来自：${ZONE_SHORT[top.zone]}当前告警计数与摘要首行`,
  };
}

/** 准入卡点焦点（compliance/sales P1）：准入漏斗里 needs_more_info+rejected 件数 >0 → 一条焦点。
 *  缺准入域（Missing/undefined）或零卡点 → null（本优先级无数据，跳过顺延）。 */
function admissionFocus(zones: Zone[]): FocusItem | null {
  const f = readAdmissionFunnel(zones);
  if (!f || isMissing(f)) return null;
  const n = admissionActionable(f);
  if (n <= 0) return null;
  return {
    key: "focus-admission",
    zone: "customers", // 下钻落 customers 区（准入漏斗数据家），其 ZoneContext 呈现完整漏斗
    text: `${formatInt(n)} 件准入待补件 / 被驳回`,
    source: "来自：客户区准入漏斗当前值（needs_more_info + rejected）",
  };
}

// V24 角色化焦点源顺序（规格 §实现架构"今日焦点条按角色配置生成，各角色 P1/P2/P3 焦点源不同"）。
// 每角色列出该角色**关心且首屏摆得出**的焦点源，按 P1→P3 排；todaysFocus 依次现算取满 3 条。
// manager 保持 [decisions,cash,alert]（改前顺序，byte-identical）。焦点源只取该角色语境成立的：
//   · decisions（待批/我组待批）：仅摆了 decisions 区的角色（能审批/有处置任务）。
//   · cash（现金击穿）：仅 finance/manager（摆了钱区、且 _can_see_cost 见金额；其余角色钱区掩码不摆）。
//   · alert（最高告警区）：告警池已按 roleWallZoneIds 收窄到该角色首屏区。
//   · admission（准入卡点）：compliance/sales（准入是其主责，P1）。
export type FocusSourceId = "decisions" | "cash" | "alert" | "admission";
export const ROLE_FOCUS: Record<Role, FocusSourceId[]> = {
  manager: ["decisions", "cash", "alert"],
  ops: ["decisions", "alert"],
  cs: ["decisions", "alert"],
  procurement: ["decisions", "alert"],
  finance: ["cash", "decisions", "alert"],
  compliance: ["admission", "alert"],
  sales: ["admission", "alert"],
};

function focusSource(id: FocusSourceId, zones: Zone[], role: Role): FocusItem | null {
  switch (id) {
    case "decisions":
      return decisionsFocus(zones, role);
    case "cash":
      return cashFocus(zones);
    case "alert":
      return alertFocus(zones, role);
    case "admission":
      return admissionFocus(zones);
  }
}

/** 今日焦点条数据：按角色配置的焦点源顺序（ROLE_FOCUS）依次现算，取满 3 条为止；某源无数据跳过
 *  顺延；全部无数据 → 空数组（调用方须整条不渲染）。role 缺省（理论不发生，Role 全覆盖）退 manager 序。 */
export function todaysFocus(zones: Zone[], role: Role = "manager"): FocusItem[] {
  return (ROLE_FOCUS[role] ?? ROLE_FOCUS.manager)
    .map((id) => focusSource(id, zones, role))
    .filter((x): x is FocusItem => x !== null)
    .slice(0, 3);
}
