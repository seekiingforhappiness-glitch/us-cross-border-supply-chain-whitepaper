// AI 工作流「用户故事卡」的纯数据层（无 React、无副作用）——V10 补记：Daniel「右边的工作流很难懂，
// 没有从用户的角度进行优化」。把同一 risk_event/task 链的 检测→提案→审批/结案 事件折叠成一张
// 故事卡：主句人话叙事（醒目金额），点展开=完整时间线（原始事件序列保留）。Streamlit ux_copy
// 清洗层精神在驾驶舱的补课。
//
// 语义红线：人话映射只翻译不加工——中文名逐字取自权威一手源（app/ux_copy.py 的 RULE_CN /
// QUALITY_LABEL_CN、app/streamlit_app.py 的审批模板、ontology 规则 type 字段）；/ontology 端点
// 未暴露规则/动作中文名（只给 PascalCase action name），故前端建映射表兜底并在报告列歧义。
// 金额/规则/严重度全部现取自载荷，不编造。
import { refToObjectRef, type AiFlowItem, type ObjectRef } from "../api";

// ── 规则码 → 中文（R1-R18 逐字镜像 app/ux_copy.py::RULE_CN；R19-R21 补自 ontology 规则 type
//    与 B5 任务书/V8 决策，均为一手源，非臆造）───────────────────────────────────
export const RULE_CN: Record<string, string> = {
  R1: "延误传导", R2: "单证缺失", R3: "静默停滞",
  R4: "费率超收", R5: "重复计费", R6: "计划外费用",
  R7: "供应商交期延误", R8: "短装", R9: "质量不合格",
  R10: "价量不符", R11: "开票超实收", R12: "预付款敞口",
  R13: "供应商资质过期", R14: "单一来源断供", R15: "绕流程采购",
  R16: "断货", R17: "不可履约", R18: "盘点差异",
  R19: "逾期应收", R20: "现金水位", R21: "重复或不符付款",
};
// 规则 type（英文枚举）兜底表（detect note 同时给 code 与 type，code 未命中时退 type）
export const RULE_TYPE_CN: Record<string, string> = {
  delay_breach: "延误传导", docs_missing: "单证缺失", stalled: "静默停滞",
  rate_overbilling: "费率超收", duplicate_charge: "重复计费", unplanned_charge: "计划外费用",
  // cash_watch：B6 核对 engine/finance_rules.py 的 emit("R20", "cash_watch", ...) 发现此处原键
  // 误写作 "cash_runway"（全仓库 grep 唯一出现处），从未真实匹配过——顺手订正，不改变任何行为
  // （RULE_CN 按 rule_id 已优先命中，这条 fallback 平时不会被触达；订正只会让它开始正确匹配）。
  overdue_receivable: "逾期应收", cash_watch: "现金水位", payment_anomaly: "重复或不符付款",
};

// ── 处置动作码 → 中文动词（英文码取自 app/streamlit_app.py::_APPROVE_HUMAN_TEMPLATES；
//    sim 提案 note 多数已是中文（加急/升级/接受延误/紧急补货/补件催办/争议账单），原样透传）──
export const ACTION_CN: Record<string, string> = {
  reconcile_payment: "对账追回", propose_collection: "催收",
  expedite: "加急", reschedule: "改期", accept_delay: "接受延误",
  dispute: "争议账单", accept_charge: "接受费用", rebill_customer: "转嫁客户",
  expedite_po: "催单加急", accept_receipt_variance: "接受收货差异",
  raise_supplier_claim: "供应商索赔", dispute_supplier_invoice: "争议供应商发票",
  escalate_prepayment: "升级预付款", hold_balance_payment: "暂缓尾款",
  request_supplier_docs: "催补资质", suspend_supplier: "冻结供应商",
  suggest_substitution: "现货替代", adjust_inventory: "调整库存",
  escalate_replenishment: "升级补货", initiate_second_source: "启动第二来源",
  block_non_po_payment: "拦截无PO付款", backfill_po: "补建采购单",
  // B6（对象卡用户语言化）补齐：Task.proposed_action 本体枚举里存在、但此前未入表的 3 个真实值
  // （v0.11.2/G4 词表对齐后的真实词表，见决策日志 V11 勘误）——collect 与 propose_collection 同义
  // （ontology RiskEvent 字段注释明确标注"duplicates 'collect' in intent"，非猜测合并）；
  // chase_docs 是 R2 单证缺失的催办动作（当前无独立处置函数，暂用通用改期/加急/接受延误三选一
  // 处置，故译作"催办单证"而非借用其它域的"催"字动词避免误导）；escalate 是通用升级动词。
  collect: "催收", chase_docs: "催办单证", escalate: "升级",
};

// ── kind → 人话徽标 ─────────────────────────────────────────────────────────
export const KIND_CN: Record<string, string> = {
  detect: "检测", propose: "提案", approve: "批准", reject: "驳回", close: "结案",
  llm_call: "AI 调用", ai_action: "AI 执行", task_flow: "流转",
};

// 生命周期次序（同日时间线 tiebreaker；非 sim 事件给中性档）
const KIND_SEQ: Record<string, number> = {
  detect: 0, llm_call: 0, propose: 1, ai_action: 1, task_flow: 1, approve: 2, reject: 2, close: 3,
};

// severity 译名导出供 ImpactPanel/LaneQueue 复用（三处曾各自本地重复定义同一张表，B5 归一）。
export const SEV_CN: Record<string, string> = { critical: "紧急", high: "高", medium: "中", low: "低" };
const OUTCOME_CN: Record<string, string> = { mitigated: "已缓解", accepted: "已接受", escalated: "已升级" };

// 动作层 PascalCase 名（非 sim 世界 task_flow/ai_action 用）→ 中文
const ACTION_NAME_CN: Record<string, string> = {
  IngestMilestone: "摄入里程碑", CreateRiskEvent: "创建风险", AssignTask: "派单",
  ProposeMitigation: "提交处置提案", ApproveMitigation: "审批处置", RejectMitigation: "驳回处置",
  CloseRiskEvent: "关闭风险",
};

const ruleName = (code: string, type?: string): string =>
  RULE_CN[code] || (type ? RULE_TYPE_CN[type] || type : "") || code || "风险";
const actionLabel = (raw: string): string => ACTION_CN[raw] || raw; // 中文码原样透传

/** 金额 → 醒目全额美元串（$7,157，整数千分位）；非数字（掩码/缺）→ null。 */
export function fmtAmount(v: number | null): string | null {
  if (v === null || !Number.isFinite(v)) return null;
  return `$${Math.round(v).toLocaleString("en-US")}`;
}

function parseAmount(note: string): number | null {
  const m = note.match(/\$\s*([\d,]+(?:\.\d+)?)/);
  if (!m) return null;
  const n = Number(m[1].replace(/,/g, ""));
  return Number.isFinite(n) ? n : null;
}

// detect note: "R21 payment_anomaly sev=high $7157.16"
function parseDetect(note: string): { code: string; type: string; sev: string; amount: number | null } {
  const m = note.match(/^(R\d+)\s+([a-z_]+)\s+sev=(\w+)/i);
  return {
    code: m?.[1] ?? "",
    type: m?.[2] ?? "",
    sev: m?.[3] ?? "",
    amount: parseAmount(note),
  };
}

// propose note: "提案「reconcile_payment」；经济账 追回重复付款 $7157.16"
function parsePropose(note: string): { action: string; amount: number | null } {
  const m = note.match(/提案「([^」]+)」/);
  return { action: m?.[1] ?? "", amount: parseAmount(note) };
}

// close note: "关闭 outcome=mitigated 用时 10d 质量=部分有效"
function parseClose(note: string): { outcome: string; days: string; quality: string } {
  return {
    outcome: note.match(/outcome=(\w+)/)?.[1] ?? "",
    days: note.match(/用时\s*(\d+)\s*d/)?.[1] ?? "",
    quality: note.match(/质量=(\S+)/)?.[1] ?? "",
  };
}

/** 时间线单条事件的原始 note 人话化（保留序列/细节，只去技术码）。 */
export function humanizeNote(kind: string, note: string): string {
  if (!note) return KIND_CN[kind] ?? kind;
  let t = note;
  // 规则 token：R21 payment_anomaly → R21 重复或不符付款
  t = t.replace(/\b(R\d+)\s+([a-z_]+)\b/gi, (_all, code: string, type: string) => `${code} ${ruleName(code, type)}`);
  t = t.replace(/sev=(\w+)/g, (_a, s: string) => `严重度 ${SEV_CN[s] ?? s}`);
  t = t.replace(/outcome=(\w+)/g, (_a, o: string) => `结果 ${OUTCOME_CN[o] ?? o}`);
  t = t.replace(/用时\s*(\d+)\s*d\b/g, "用时 $1 天");
  // 提案「英文码」→ 提案「中文」
  t = t.replace(/提案「([^」]+)」/g, (_a, act: string) => `提案「${actionLabel(act)}」`);
  return t;
}

// 非 sim 世界（llm_call/ai_action/task_flow）单条 summary 人话化（PascalCase 动作名 → 中文）
function humanizeSummary(summary: string): string {
  let t = summary;
  for (const [en, cn] of Object.entries(ACTION_NAME_CN)) t = t.replace(new RegExp(`\\b${en}\\b`, "g"), cn);
  return t;
}

// ── 故事卡类型 ──────────────────────────────────────────────────────────────
export type StoryState = "detected" | "proposed" | "approved" | "rejected" | "closed" | "info";

export interface StoryEvent {
  ts: string;
  kind: string;
  kindLabel: string;
  text: string; // 人话化后的时间线描述
  ref: ObjectRef | null; // 该事件自身对象链接（点击行为不变）
}
export interface StoryLink {
  label: string;
  ref: ObjectRef;
}
export interface Story {
  key: string;
  sim: boolean;
  state: StoryState;
  lead: string; // 主句前半（金额前）
  amount: string | null; // 醒目金额串（可选）
  trail: string; // 主句后半（金额后）
  ts: string; // 最近事件 ts（排序 + 展示）
  events: StoryEvent[]; // 时间线（时间升序）
  links: StoryLink[]; // 「查看详情」去重链接（风险 / 任务）
}

// 链 key：sim 事件按 risk_event_id 归并；task_flow/ai_action 按 ref 对象归并；其余（llm_call）独立成卡
function chainKey(it: AiFlowItem, idx: number): string {
  const rid = (it.detail as { risk_event_id?: string })?.risk_event_id;
  if (rid) return `risk:${rid}`;
  if ((it.kind === "task_flow" || it.kind === "ai_action") && it.ref_object) {
    const r = refToObjectRef(it.ref_object);
    if (r && (r.type === "Task" || r.type === "RiskEvent")) return `ref:${it.ref_object}`;
  }
  return `solo:${it.ts}:${it.kind}:${idx}`;
}

function composeSim(kindsByType: Record<string, AiFlowItem>): Pick<Story, "state" | "lead" | "amount" | "trail"> {
  const det = kindsByType.detect ? parseDetect(String((kindsByType.detect.detail as { note?: string }).note ?? "")) : null;
  const prop = kindsByType.propose ? parsePropose(String((kindsByType.propose.detail as { note?: string }).note ?? "")) : null;
  const clo = kindsByType.close ? parseClose(String((kindsByType.close.detail as { note?: string }).note ?? "")) : null;
  const hasApprove = !!kindsByType.approve;
  const hasReject = !!kindsByType.reject;

  const rn = det ? ruleName(det.code, det.type) : "风险";
  const amt = det?.amount ?? prop?.amount ?? null;
  const act = prop ? actionLabel(prop.action) : "";
  const sevLabel = det?.sev ? SEV_CN[det.sev] ?? det.sev : "";

  if (clo) {
    const quality = clo.quality || "—";
    const days = clo.days ? `，用时 ${clo.days} 天` : "";
    const gotApproved = hasApprove ? "获批" : "";
    const actPart = act ? `${act}${gotApproved}` : OUTCOME_CN[clo.outcome] ?? clo.outcome ?? "处置";
    return { state: "closed", lead: `${rn}处置完成：${actPart}${days}，效果 ${quality}`, amount: null, trail: "" };
  }
  if (hasApprove) return { state: "approved", lead: `${rn}：${act || "处置"}已获批，处置中`, amount: null, trail: "" };
  if (hasReject) return { state: "rejected", lead: `${rn}：AI 提案被驳回（维持现状 / 另议）`, amount: null, trail: "" };
  if (prop) return { state: "proposed", lead: `发现${rn} `, amount: fmtAmount(amt), trail: ` → AI 已提案${act || "处置"}，候审批` };
  // detect only
  const sevPart = sevLabel ? `（${sevLabel}）` : "";
  return { state: "detected", lead: `发现${rn} `, amount: fmtAmount(amt), trail: `${sevPart}——AI 检测中` };
}

/** ai-flow 事件流 → 故事卡（按链归并、人话主句、时间线保序）。 */
export function buildStories(items: AiFlowItem[]): Story[] {
  const groups = new Map<string, AiFlowItem[]>();
  const order: string[] = [];
  items.forEach((it, i) => {
    const k = chainKey(it, i);
    if (!groups.has(k)) {
      groups.set(k, []);
      order.push(k);
    }
    groups.get(k)!.push(it);
  });

  const stories: Story[] = [];
  for (const k of order) {
    const evs = groups.get(k)!;
    const sim = evs.some((e) => e.sim);
    // 时间线：时间升序（原始序列）；sim 事件时间为日期粒度（无时分），同日按生命周期次序
    // detect→propose→approve/reject→close 作 tiebreaker，避免同日事件倒挂（提案显示在检测前）。
    const sorted = [...evs].sort((a, b) => {
      if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1;
      return (KIND_SEQ[a.kind] ?? 9) - (KIND_SEQ[b.kind] ?? 9);
    });
    const timeline: StoryEvent[] = sorted.map((e) => ({
      ts: e.ts,
      kind: e.kind,
      kindLabel: KIND_CN[e.kind] ?? e.kind,
      text: sim ? humanizeNote(e.kind, String((e.detail as { note?: string })?.note ?? "")) : humanizeSummary(e.summary),
      ref: refToObjectRef(e.ref_object),
    }));
    const latestTs = sorted[sorted.length - 1].ts;

    // 查看详情链接：风险 / 任务（去重、人话前缀）
    const links: StoryLink[] = [];
    const seen = new Set<string>();
    const addLink = (id: string | null | undefined, prefix: string) => {
      if (!id || seen.has(id)) return;
      const r = refToObjectRef(id);
      if (r) {
        links.push({ label: `${prefix} ${id}`, ref: r });
        seen.add(id);
      }
    };
    const detailOf = (kd: string) => evs.find((e) => e.kind === kd)?.detail as { risk_event_id?: string; task_id?: string } | undefined;

    let composed: Pick<Story, "state" | "lead" | "amount" | "trail">;
    if (sim) {
      const byType: Record<string, AiFlowItem> = {};
      for (const e of evs) if (!byType[e.kind]) byType[e.kind] = e; // 每型取一（同型多条罕见，取首条）
      composed = composeSim(byType);
      const anyDetail = (detailOf("detect") || detailOf("propose") || detailOf("close") || detailOf("approve") || {}) as {
        risk_event_id?: string;
        task_id?: string;
      };
      addLink(anyDetail.risk_event_id, "风险");
      addLink(anyDetail.task_id, "任务");
    } else {
      // 非 sim：单事件信息卡
      const e = sorted[sorted.length - 1];
      composed = { state: "info", lead: humanizeSummary(e.summary), amount: null, trail: "" };
      addLink(e.ref_object, "对象");
    }

    stories.push({
      key: k,
      sim,
      ...composed,
      ts: latestTs,
      events: timeline,
      links,
    });
  }

  // 最近活动的故事排前（与既有「新条目上浮」一致）
  stories.sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
  return stories;
}
