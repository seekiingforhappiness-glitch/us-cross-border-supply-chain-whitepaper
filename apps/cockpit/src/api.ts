// 薄 API 客户端：fetch + 类型 + 角色头（X-Role）。不做缓存/重试——组件层用 useEffect 自管。
//
// 请求路径固定用相对前缀 /api，由 vite.config.ts 的 server.proxy / preview.proxy 转发到
// apps/api 的真实地址 http://localhost:8100（走同源代理，不触发 CORS，不碰 apps/api 一行）。
// 双世界切换（验证世界 ⇄ 模拟世界）由 apps/api 进程的 ONTOLOGY_DB 环境变量决定，前端不变。
export const API_BASE_URL = "/api";

// 角色缩放：X-Role 头全局生效（体征带脱敏 + 全景粒度）。二稿起步两档：老板/运营专员。
export type Role = "manager" | "ops";

// 无权查看的掩码值（与 agent/tools.py::MASK 逐字一致——服务端契约，不可改）。金额类 _usd 键与
// margin_distribution 对无成本权限角色（ops）会被 apps/api 替换成这个字符串——渲染层需识别它、
// 不当数字画。MASK_TEXT 是其去 emoji 的展示文案（V9-B emoji 清零：契约值不变，渲染剥 emoji，
// 锁图标由 Icons.tsx 在渲染处补）。
export const MASK = "🔒无权查看";
export const MASK_TEXT = "无权查看";

// 缺域指标的统一形状（apps/api::_missing）：{value:null, reason:"该世界无此域数据（…）"}。
// 模拟世界缺采购/准入/审计等域，对应指标如实返回此形状——渲染层照实画"无数据 + 原因"。
export interface Missing {
  value: null;
  reason: string;
}

export function isMissing(x: unknown): x is Missing {
  return (
    typeof x === "object" &&
    x !== null &&
    "reason" in x &&
    (x as { value?: unknown }).value === null
  );
}

export function isMasked(x: unknown): x is typeof MASK {
  return x === MASK;
}

// ── 通用 GET（带角色头）。非 2xx 一律抛错，调用方负责降级展示，这层不吞异常。 ──
async function apiGet<T>(path: string, role: Role): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: { "X-Role": role } });
  if (!resp.ok) throw new Error(`GET ${path} 失败：HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

// ═══════════════════════════════ /cockpit/vitals ═══════════════════════════════
export type ZoneId =
  | "money"
  | "fulfillment"
  | "customers"
  | "suppliers"
  | "inventory"
  | "ai"
  | "decisions";

export interface Trend {
  metric: string;
  window_days: number;
  current: number;
  prior: number;
  delta: number;
  basis: string;
}

// headline_value：number（比率/计数）| string（AI 区的"N 检 / M 提案"、或被掩码的金额）| null。
export interface Zone {
  zone: ZoneId;
  headline_label: string;
  headline_value: number | string | null;
  headline_unit?: string; // "usd" → 金额，前端加 $ 与千分位
  headline_reason?: string; // headline 为 null 时的人话原因（如交期达成率缺收货域）
  trend: Trend | null;
  alert_count: number;
  detail: Record<string, unknown>; // 各区形状不同，由 ZoneDetail 的分区渲染器按键取用
}

export interface Vitals {
  world: string;
  clock: string | null;
  role: string;
  zones: Zone[];
}

export const fetchVitals = (role: Role) => apiGet<Vitals>("/cockpit/vitals", role);

// ═══════════════════════════════ /cockpit/panorama ═══════════════════════════════
export interface PanoAlert {
  risk_event_id: string;
  rule_id: string;
  type: string;
  severity: string;
}

// 节点是并集类型：实体节点与分组节点字段不同，共有 id/layer/label/alert_count/alerts。
// 其余字段（us_state/lane/status/so_count/count/…）按层可选，渲染层按存在性取用。
export interface PanoNode {
  id: string;
  layer: PanoLayerName;
  label: string;
  alert_count: number;
  alerts: PanoAlert[];
  // 实体/分组各层的可选度量字段
  us_state?: string | null;
  group_key?: string;
  count?: number;
  so_count?: number;
  line_count?: number;
  at_risk_lines?: number;
  open_lines?: number;
  lane?: string;
  status?: string;
  eta_current?: string | null;
  delay_days?: number | null;
  customs_status?: string | null;
  container_count?: number;
  delayed_count?: number;
  in_transit_value_usd?: number | string | null;
  statuses?: Record<string, number>;
  city?: string | null;
  lead_time_days?: number | null;
  region?: string | null;
  type?: string | null;
  safety_breach_count?: number;
}

export type PanoLayerName = "customers" | "orders" | "shipments" | "suppliers" | "warehouses";

export interface PanoLayer {
  granularity: "entity" | "group";
  nodes: PanoNode[];
  note?: string;
}

export interface PanoEdge {
  source: string;
  target: string;
  via: string;
  count: number;
}

export interface Panorama {
  world: string;
  clock: string | null;
  role: string;
  layers: Record<PanoLayerName, PanoLayer>;
  edges: PanoEdge[];
  alerts_unanchored: (PanoAlert & { anchor: string })[];
  meta: {
    layer_cap: number;
    aggregated_layers: string[];
    layer_counts: Record<string, number>;
    edge_count: number;
    open_risks_total: number;
    alerts_unanchored_total: number;
  };
}

export const fetchPanorama = (role: Role) => apiGet<Panorama>("/cockpit/panorama", role);

// ═══════════════════════════════ /cockpit/ai-flow ═══════════════════════════════
export interface AiFlowItem {
  ts: string;
  kind: string; // llm_call | ai_action | task_flow | detect | propose | approve | reject | close
  summary: string;
  ref_object: string | null;
  sim: boolean;
  detail: Record<string, unknown>;
}

export interface AiFlow {
  world: string;
  role: string;
  limit: number;
  count: number;
  sources_present: string[];
  items: AiFlowItem[];
}

export const fetchAiFlow = (role: Role, limit = 60) =>
  apiGet<AiFlow>(`/cockpit/ai-flow?limit=${limit}`, role);

// ═══════════════════════════ 对象卡：/objects/{Type}/{id}（+ links traverse）═══════════════════════════
// 全景节点 id 用小写前缀（customer:/shipment:/supplier:/warehouse:/orders:/lane:…），
// 而对象端点用本体 PascalCase 类型名——此表是唯一映射源，分组节点（orders/lane/聚合层）无
// 单一对象，返回 null 表示"不可打开对象卡"。
const NODE_PREFIX_TO_TYPE: Record<string, string> = {
  customer: "Customer",
  shipment: "Shipment",
  supplier: "Supplier",
  warehouse: "Warehouse",
};

export interface ObjectRef {
  type: string;
  id: string;
}

// 对象 id 前缀（'-' 前首段）→ 本体类型：AI 工作流卡片 ref_object、对象卡邻居 id 的开卡映射。
// 命中才给可点链接，未命中（如 trace_id / 仓库码 FBA-US）不硬造类型、不给假链接。
const ID_PREFIX_TO_TYPE: Record<string, string> = {
  CUS: "Customer",
  SHP: "Shipment",
  SUP: "Supplier",
  PO: "PurchaseOrder",
  TSK: "Task",
  RSK: "RiskEvent",
  INV: "Invoice",
  SKU: "Sku",
  SO: "SalesOrder",
};

/** 对象 id → 对象引用（按前缀映射类型）；无法映射 → null（不给假链接）。 */
export function refToObjectRef(ref: string | null | undefined): ObjectRef | null {
  if (!ref) return null;
  const type = ID_PREFIX_TO_TYPE[ref.split("-")[0]];
  return type ? { type, id: ref } : null;
}

/** 全景实体节点 id → 对象引用；分组/聚合节点 → null（无单一对象，不开卡）。 */
export function nodeToObjectRef(nodeId: string): ObjectRef | null {
  const idx = nodeId.indexOf(":");
  if (idx < 0) return null;
  const prefix = nodeId.slice(0, idx);
  const rest = nodeId.slice(idx + 1);
  const type = NODE_PREFIX_TO_TYPE[prefix];
  if (!type) return null; // orders:/lane:/customers:/suppliers: 分组节点
  return { type, id: rest };
}

export type ObjectFields = Record<string, unknown>;

export const fetchObject = (type: string, id: string, role: Role) =>
  apiGet<ObjectFields>(`/objects/${encodeURIComponent(type)}/${encodeURIComponent(id)}`, role);

export interface TraverseResult {
  source_type: string;
  source_id: string;
  link_type: string;
  count: number;
  neighbor_ids: string[];
}

export const traverse = (type: string, id: string, link: string, role: Role) =>
  apiGet<TraverseResult>(
    `/objects/${encodeURIComponent(type)}/${encodeURIComponent(id)}/links/${encodeURIComponent(link)}`,
    role,
  );

// ═══════════════════════════ /ontology（对象卡的 links 清单来源）═══════════════════════════
export interface OntologyLink {
  linkType: string;
  source: string;
  target: string;
  cardinality?: string;
  status?: string;
}

export interface OntologySummary {
  version: string;
  world: string;
  roles: string[];
  links: OntologyLink[];
  summary: {
    object_types: number;
    links: number;
    actions: number;
    exposed_actions: number;
    frozen_actions: number;
  };
}

export async function fetchOntologySummary(role: Role): Promise<OntologySummary> {
  return apiGet<OntologySummary>("/ontology", role);
}

/** 某对象类型可 traverse 的关系（本体里以该类型为 source 或 target 端点的所有 link——
 *  traverse 双向可走，见 pipeline.ontology_runtime.traverse）。附邻居类型供"继续点"接力。 */
export interface UsableLink {
  linkType: string;
  neighborType: string;
  direction: "forward" | "reverse";
  cardinality?: string;
}

export function linksForType(links: OntologyLink[], type: string): UsableLink[] {
  const out: UsableLink[] = [];
  for (const l of links) {
    if (l.status === "declared_only") continue; // 无承载列，traverse 会 422，不列
    if (l.source === type) {
      out.push({ linkType: l.linkType, neighborType: l.target, direction: "forward", cardinality: l.cardinality });
    } else if (l.target === type) {
      out.push({ linkType: l.linkType, neighborType: l.source, direction: "reverse", cardinality: l.cardinality });
    }
  }
  return out;
}

// ═══════════════════════════ 展示格式化小工具（纯函数，无副作用）═══════════════════════════
/** 金额 → 紧凑美元串：$1.2M / $23.4K / $512。掩码显示去 emoji 文案、缺值 —，不冒充数字。 */
export function formatUsd(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === MASK ? MASK_TEXT : v; // MASK 掩码串（剥 emoji）
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}

/** 比率 → 百分比串（0.9685 → "96.9%"）。null → "—"。 */
export function formatPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

/** 千分位整数（掩码串去 emoji 展示）。 */
export function formatInt(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v === MASK ? MASK_TEXT : v;
  return v.toLocaleString("en-US");
}
