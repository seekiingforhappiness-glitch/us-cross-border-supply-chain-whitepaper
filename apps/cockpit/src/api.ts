// 薄 API 客户端：fetch + 类型 + 角色头（X-Role）+ 世界头（X-World）。不做缓存/重试——组件层用
// useEffect 自管。
//
// 请求路径固定用相对前缀 /api，由 vite.config.ts 的 server.proxy / preview.proxy 转发到
// apps/api 的真实地址 http://localhost:8100（走同源代理，不触发 CORS，不碰 apps/api 一行）。
// 双世界切换（验证世界 ⇄ 模拟世界）U1：由驾驶舱顶栏切换钮控制，经 X-World 头传导到 apps/api 的
// get_db_path 依赖（verify→data/ontology.sqlite、sim→data/simworld.sqlite），不重启进程即切库。
import { actorForRole } from "./roleActors";

export const API_BASE_URL = "/api";

// 角色缩放：X-Role 头全局生效（体征带脱敏 + 全景粒度）。七档：老板/运营/客服/采购/合规/销售/财务
// （V22① finance 首批接入，批D 补齐 cs/procurement/compliance/sales——本体 7 角色前端全数落地）。
// 为什么在此扩：Role 是全舱透传的头值类型，权限/脱敏由 apps/api 按 X-Role 同源执行（宪法不变量 5），
// 前端只多认一个合法角色字符串、不复制任何权限规则；后端 7 角色齐备。
export type Role = "manager" | "ops" | "finance" | "cs" | "procurement" | "compliance" | "sales";

// 世界切换（U1）：验证世界（datagen 种子库，R/P=1.000 对照源）⇄ 模拟世界（14 个月连续活世界，
// 时间回放完整威力）。X-World 头值 verify/sim 与 apps/api::_WORLD_DB_ALIASES 契约一致。
export type World = "verify" | "sim";

// 驾驶舱当前选定的世界——所有请求的 X-World 头来源（模块级单例，App 的 world 状态经 setApiWorld
// 同步到此）。为什么用模块级而非逐函数传参：全舱所有 fetch（含 RouteMap/ObjectCard 等未逐个改签名
// 的既有调用）都要跟随世界切换，模块级单例让它们零改动自动带上当前世界头，是"App 状态 world 串所有
// fetch"最小侵入的落法。null=未显式选择 → 不发 X-World 头（沿用 apps/api 启动环境变量，首屏
// byte-identical 向后兼容）。切世界后 App 会同时清下钻状态并回今天（见 App.changeWorld）。
let currentWorld: World | null = null;
export function setApiWorld(w: World | null): void {
  currentWorld = w;
}
export function getApiWorld(): World | null {
  return currentWorld;
}

// 请求头组装：X-Role 恒带；X-World 仅当已显式选世界时带（null 不带 → 服务端用启动环境变量）。
function reqHeaders(role: Role, extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { "X-Role": role, ...extra };
  if (currentWorld) h["X-World"] = currentWorld;
  return h;
}

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

// ── 通用 GET（带角色头 + 世界头）。非 2xx 一律抛错，调用方负责降级展示，这层不吞异常。 ──
async function apiGet<T>(path: string, role: Role): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, { headers: reqHeaders(role) });
  if (!resp.ok) throw new Error(`GET ${path} 失败：HTTP ${resp.status}`);
  return (await resp.json()) as T;
}

// ═══════════════════════════ POST /decisions/{name}（人类决策通道，A-1 / V13①）═══════════════════════════
// 冻结区四动作（审批 ApproveMitigation / 关闭 CloseRiskEvent / 准入批 ApproveQuoteDecision /
// 拒接 RejectOrRequestMoreInfo）的**人类专用**写通道——与 /actions（AI 面）物理隔离，AI 永远调不到。
// 必带两个头：X-Role（鉴权，同 GET）+ X-Actor（真实决策人 id，审计留痕与 maker-checker 靠它，见
// roleActors.ts）。返回体是 app 层动作函数结果原样（ok/object_id/side_effects/error）。
// 非 2xx 时后端 HTTPException 的 detail 是白话中文错误原文——原样抛出，调用方展示，这层不吞不美化。
export interface DecisionResult {
  ok: boolean;
  object_id: string | null;
  side_effects: string[];
  error: string | null;
}

// 幂等键（B-1/V18）：优先用浏览器原生 crypto.randomUUID（现代浏览器 + localhost/https 安全上下文
// 皆支持）；极端环境缺失时退化为时间戳+随机数拼接，不阻断提交——生成失败只是失去"同键重放只执行
// 一次"的保护，不影响功能。后端 /decisions 早已透传 Idempotency-Key 头给写总线（波2 §一.4，本次
// 未改一行后端），此前 postDecision 一直没发这个头，四个冻结按钮（含既有 ApproveMitigation）都
// 补上；不改函数签名，调用方零改动自动获得保护。
function genIdempotencyKey(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `idem-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

// 对抗复核 F3 修正：随机键每次调用重生 → 真正的网络重试各拿新键，"同键重放"形同虚设。
// 改为**按意图记键**：同一（动作+请求体+操作者）在网络失败/在飞 409 期间复用同一个键（重试
// 命中总线重放）；成功或业务性拒绝（权限/指纹/状态机）后意图即完成，删记录——下一次同体请求
// 是新意图拿新键，不会永远重放旧结果。键仍是随机 UUID，意图签名只在本模块内存活。
const pendingIntentKeys = new Map<string, string>();

function stableStringify(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(stableStringify).join(",")}]`;
  if (v && typeof v === "object") {
    const o = v as Record<string, unknown>;
    return `{${Object.keys(o).sort().map((k) => `${JSON.stringify(k)}:${stableStringify(o[k])}`).join(",")}}`;
  }
  return JSON.stringify(v) ?? "null";
}

function intentKeyFor(name: string, body: Record<string, unknown>, actor: string): { sig: string; key: string } {
  const sig = `${name}|${actor}|${stableStringify(body)}`;
  let key = pendingIntentKeys.get(sig);
  if (!key) {
    key = genIdempotencyKey();
    pendingIntentKeys.set(sig, key);
  }
  return { sig, key };
}

export async function postDecision(
  name: string,
  body: Record<string, unknown>,
  role: Role,
  actor: string,
): Promise<DecisionResult> {
  const { sig, key } = intentKeyFor(name, body, actor);
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE_URL}/decisions/${encodeURIComponent(name)}`, {
      method: "POST",
      // 写通道同样跟随当前世界（X-World）：在模拟世界里拍板即写模拟库、验证世界即写验证库——
      // apps/api::post_decision 经 get_db_path 解析世界，前后端一套世界语义。
      headers: reqHeaders(role, { "Content-Type": "application/json", "X-Actor": actor, "Idempotency-Key": key }),
      body: JSON.stringify(body),
    });
  } catch (e) {
    // 网络级失败（fetch 未拿到响应）：保留意图键——用户重试同一意图时复用同键，总线保证只执行一次。
    throw e;
  }
  // 409=在飞（另一并发请求已认领）：保留键，稍后同键重试可取回首次结果；其余任何已决响应
  // （成功/业务拒绝）都意味着这个意图已了结，删记录换新意图。
  if (resp.status !== 409) pendingIntentKeys.delete(sig);
  if (!resp.ok) {
    // 后端 HTTPException detail 为白话中文（如"缺少 X-Actor…""角色无权…""提案人不能审批自己…"）。
    let detail = `提交失败：HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: unknown };
      if (typeof j.detail === "string" && j.detail) detail = j.detail;
    } catch {
      /* 非 JSON 响应：保留 HTTP 码兜底文案 */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as DecisionResult;
}

// ── 协调对手方类型枚举（发起协调下拉源）──────────────────────────────────────
// 镜像 app/coordination_actions.py::COUNTERPARTY_TYPES（= 本体 CoordinationThread.counterparty_type
// values）；后端仍是权威（非法值 422 白话）。前端下拉用这份，避免用户手打错。
export const COUNTERPARTY_TYPES = ["supplier", "forwarder", "customs_broker", "bank", "customer"] as const;
export type CounterpartyType = (typeof COUNTERPARTY_TYPES)[number];
export const COUNTERPARTY_TYPE_CN: Record<CounterpartyType, string> = {
  supplier: "供应商",
  forwarder: "货代",
  customs_broker: "报关行",
  bank: "银行",
  customer: "客户",
};

// 发起新协调线程（V22② 余量清偿）：POST /collaboration/threads（独立于 {id}/actions 转移通道）。
// 与 postDecision 同构——X-Actor 必填 + 按意图记幂等键 + 白话错误原样透传（后端 detail 是中文原文）。
// owner 不由前端填：后端缺省用发起人身份（open_coordination 要求非空，发起协调者天然是负责人）。
export interface OpenCoordinationBody {
  task_id: string;
  counterparty_type: CounterpartyType;
  counterparty_ref: string;
  ask: string;
  next_action_due: string;
}
export async function openCoordination(
  body: OpenCoordinationBody,
  role: Role,
  actor: string,
): Promise<DecisionResult> {
  const { sig, key } = intentKeyFor("OpenCoordination", body as unknown as Record<string, unknown>, actor);
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE_URL}/collaboration/threads`, {
      method: "POST",
      // 写通道跟随当前世界（X-World，reqHeaders 带上）：模拟世界发起即写模拟库、验证世界写验证库。
      headers: reqHeaders(role, { "Content-Type": "application/json", "X-Actor": actor, "Idempotency-Key": key }),
      body: JSON.stringify(body),
    });
  } catch (e) {
    throw e; // 网络级失败：保留意图键，重试同意图复用同键（总线只执行一次）
  }
  if (resp.status !== 409) pendingIntentKeys.delete(sig);
  if (!resp.ok) {
    let detail = `提交失败：HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: unknown };
      if (typeof j.detail === "string" && j.detail) detail = j.detail;
    } catch {
      /* 非 JSON 响应：保留 HTTP 码兜底文案 */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as DecisionResult;
}

// ── 协调转移动作写通道（CL1 六动作的人类 HTTP 门；V22 余量：从 AiWorkflow.tsx 内联下沉，消除同构复制）──
// POST /collaboration/threads/{id}/actions/{action}（独立于发起协调的 POST /collaboration/threads）。
// 与 postDecision/openCoordination 同风格：reqHeaders 带 X-Role/X-World + X-Actor（审计留痕，经
// roleActors.actorForRole 由 role 现算）+ 每次点击一枚新 Idempotency-Key（busy 态已挡双击连发，用
// 一次性键即可，不接意图去重）。错误处理同上：后端 detail 是白话中文原文，原样抛出不吞不美化。
export type CoordActionId =
  | "record_outreach"
  | "record_response"
  | "escalate_coordination"
  | "resolve_coordination"
  | "mark_dead_ended";

export interface CoordResult {
  ok: boolean;
  object_id: string | null;
  side_effects: string[];
  error: string | null;
}

export async function postCoordinationAction(
  coordinationId: string,
  action: CoordActionId,
  body: Record<string, unknown>,
  role: Role,
): Promise<CoordResult> {
  const resp = await fetch(
    `${API_BASE_URL}/collaboration/threads/${encodeURIComponent(coordinationId)}/actions/${action}`,
    {
      method: "POST",
      headers: reqHeaders(role, {
        "Content-Type": "application/json",
        "X-Actor": actorForRole(role),
        "Idempotency-Key": genIdempotencyKey(),
      }),
      body: JSON.stringify(body),
    },
  );
  if (!resp.ok) {
    let detail = `提交失败：HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: unknown };
      if (typeof j.detail === "string" && j.detail) detail = j.detail;
    } catch {
      /* 非 JSON 响应：保留 HTTP 码兜底文案 */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as CoordResult;
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
  // 回放态（A-2/V13②）：该区 headline 是"按时点重算"还是"存量现值"。仅当请求带 as_of 且落在回放
  // 区间时后端下发；"current" → 卡面显"显示当前值"小灰标（诚实边界：无历史版本不造假数字）。
  headline_as_of?: "replayed" | "current";
}

// 数据窗口（回放滑条定义域）：start=最早真实事件日、end=世界时钟今天。三端点恒回传（纯附加键）。
export interface DataWindow {
  start: string | null;
  end: string | null;
}

// 回放信封（仅当 as_of 落在 [window.start, world_clock) 时下发）：机器可读的重算/存量分类 + 人话边界。
export interface AsOfEnvelope {
  requested: string | null;
  effective: string | null; // 实际生效时点（可能因夹取而 ≠ requested）
  world_clock: string | null;
  window: DataWindow;
  is_replay: boolean;
  world_is_sim: boolean; // 验证世界=静态快照（多数指标只能显当前值）；模拟世界=真历史可回放
  replayable: string[]; // 真按时点重算的指标键
  current_state_only: string[]; // 无历史版本、显示当前值的指标键
  note: string; // 人话边界说明
}

// 数字溯源信封（U2）：?provenance=1 时后端为每区 headline 附一枚 {口径白话 / 来源表 / 样例 id}。
// caliber=该指标口径的用户语言（"系统这一刻替你盯着、还没消掉的可疑费用"）；sources=来源表/对象；
// sample_ids=当前世界现查的 ≤3 样例 id（可跳透视镜看完整血缘；缺数世界自然空数组，绝不编造）。
export interface ZoneProvenance {
  caliber: string;
  sources: string[];
  sample_ids: (string | number)[];
}

export interface Vitals {
  world: string;
  clock: string | null; // 世界今天（右端锚，不随拖动改）
  role: string;
  window?: DataWindow;
  as_of?: AsOfEnvelope; // 仅回放态出现
  zones: Zone[];
  provenance?: Record<ZoneId, ZoneProvenance>; // 仅 provenance=1 时出现（U2）
}

// asOf 缺省（null/undefined）=世界时钟今天=现状不变（不带 as_of 查询参数）。
// provenance（U2）：驾驶舱恒开（七区卡溯源浮层要用），后端为纯附加键（不影响既有字段）。
// assigneeRole（V22 任务1"我组的"）：可选——仅筛 decisions 区待批提案列表为"指派给该角色"的行，
// 其余区不变；缺省不传该参数 → 全响应 byte-identical（老板收件箱=全部 pending）。非法值后端 422。
// sort（K·P1 供应商合规排序）：仅 "compliance"——供应商队列 UFLPA 命中优先→资质异常→交期升序；
// 仅 compliance/manager 角色可传（其他角色后端 422 白话），缺省不传=交期升序 byte-identical。
export const fetchVitals = (role: Role, asOf?: string | null, provenance = true, assigneeRole?: Role | null, sort?: "compliance" | null) => {
  const qs = new URLSearchParams();
  if (asOf) qs.set("as_of", asOf);
  if (provenance) qs.set("provenance", "1");
  if (assigneeRole) qs.set("assignee_role", assigneeRole);
  if (sort) qs.set("sort", sort);
  const q = qs.toString();
  return apiGet<Vitals>(`/cockpit/vitals${q ? `?${q}` : ""}`, role);
};

// ═══════════════ 供应商队列合规维度（K·P1，仅 compliance/manager 载荷含）═══════════════
// 本体 Supplier.uflpa_risk_flag visibleTo=[compliance,manager]——其他角色载荷里**根本不带**这些键
// （不是掩码）。行级 uflpa_risk_flag/qual_abnormal 均为后端规整/派生 bool（判定规则单一来源在后端，
// 前端只读不复刻）；compliance_dimension 为区级摘要（全库计数+口径 basis+零阳性诚实 note）。
export interface SupplierComplianceDimension {
  available: boolean;
  reason?: string; // available:false（该世界缺合规列）
  sort?: "compliance" | "default" | string;
  uflpa_flagged_total?: number; // 全库现查（含无收货记录、不在队列里的供应商）
  qual_abnormal_total?: number;
  suppliers_total?: number;
  basis?: string;
  note?: string; // 零阳性时的诚实空态白话（"当前无 UFLPA 标记供应商…"）
}

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
  window?: DataWindow;
  as_of?: AsOfEnvelope; // 仅回放态出现（异常锚定的风险集按时点重建）
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

export const fetchPanorama = (role: Role, asOf?: string | null) =>
  apiGet<Panorama>(`/cockpit/panorama${asOf ? `?as_of=${encodeURIComponent(asOf)}` : ""}`, role);

// ═══════════════════════════════ /cockpit/ai-flow ═══════════════════════════════
export interface AiFlowItem {
  ts: string;
  kind: string; // llm_call | ai_action | task_flow | detect | propose | approve | reject | close
  summary: string;
  ref_object: string | null;
  sim: boolean;
  // 执行方式徽标（不变量11）：后端仅在**可判定**的条目上补——llm_call 天然 'llm'；runtime 派生的
  // ai_action 从其 run 的 think 步现查。判不了的条目（task_flow/sim/无桥接 ai_action）无此字段（不编造）。
  mode?: "deterministic" | "llm";
  detail: Record<string, unknown>;
}

export interface AiFlow {
  world: string;
  role: string;
  limit: number;
  count: number;
  window?: DataWindow;
  as_of?: AsOfEnvelope; // 仅回放态出现（各来源按自身时间戳≤as_of 过滤）
  sources_present: string[];
  items: AiFlowItem[];
}

export const fetchAiFlow = (role: Role, limit = 60, asOf?: string | null) =>
  apiGet<AiFlow>(`/cockpit/ai-flow?limit=${limit}${asOf ? `&as_of=${encodeURIComponent(asOf)}` : ""}`, role);

// ═══════════════ /cockpit/risk-impact/{id}（付款锚风险归并，轮3-D）═══════════════
// R19/R21 锚在付款不在订单行——本端点把 payment→单据（销售订单/供应商发票）→对手方（客户/供应商）
// 归并成结构化行；非付款锚风险返回 anchor='so_line' + rows=[]（订单行归并走既有对象读链路）。
// 金额掩码由后端按 X-Role 同源执行（amount_usd 对非成本角色是 MASK 字符串）。
export interface PaymentImpactRow {
  payment_id: string;
  direction: string; // in=应收 | out=应付
  counterparty_type: string; // customer | supplier
  counterparty_id: string;
  ref_type: string; // sales_order | supplier_invoice
  ref_id: string;
  amount_usd: number | string | null; // 非成本角色为 MASK 字符串
  due_date: string | null;
  status: string;
  overdue_days: number | null; // 仅未回款应收有值（世界时钟−到期日）
  is_anchor: boolean; // R21 的姊妹重复笔为 false
}

export interface RiskImpact {
  world: string;
  role: string;
  risk_event_id: string;
  rule_id: string;
  anchor: "payment" | "so_line";
  rows: PaymentImpactRow[];
  note?: string; // rows 空时的白话原因（诚实空态，非 0 条冒充）
  basis: string;
}

export const fetchRiskImpact = (riskEventId: string, role: Role) =>
  apiGet<RiskImpact>(`/cockpit/risk-impact/${encodeURIComponent(riskEventId)}`, role);

// ═══════════════ /cockpit/customs-queue（清关卡点逐票队列，轮3-G）═══════════════
// 口径与履约区卡 customs_blocked 完全同源（not_filed 且在途）；卡点天数/严重度口径见 basis 白话。
export interface CustomsQueueItem {
  shipment_id: string;
  destination_port: string | null;
  stuck_days: number | null; // 世界时钟−最后里程碑日；数据全缺如实 null
  stuck_since: string | null;
  po_count: number;
  severity: string | null; // 该票 open 风险最高档；无风险为 null
  open_risks: number;
}

export interface CustomsQueue {
  world: string;
  role: string;
  total: number;
  count: number;
  items: CustomsQueueItem[];
  basis: string;
  note?: string;
}

export const fetchCustomsQueue = (role: Role) => apiGet<CustomsQueue>(`/cockpit/customs-queue`, role);

// ═══════════════════════════ /governance/gating（AI 可信度正脸，U3）═══════════════════════════
// AI 放权档位摘要（display-only）：把 data/gating_report.json 翻成老板语言的"当前档位 + 白话为什么"。
// display_only=true 意为"只算档不放权"——这条原样透传，绝不误读成"档位=已授权"。文件缺失/损坏时
// 后端返回 {available:false, reason}（HTTP 200，不 404/500），前端画诚实空态。世界/角色无关（离线产物）。
export interface GatingDomain {
  domain: string;
  name: string; // plain=老板语言域名（如"延误击穿承诺"）
  group: string | null;
  tier: string | null; // 当前档位
  next_tier: string | null;
  n: number | null; // 样本量
  hits: number | null;
  rate: number | null;
  ci: [number, number] | number[] | null;
  low_sample: boolean | null;
  escalation: unknown;
  gaps: string[]; // 白话差距清单（"缺样本：n=0 < 30"…），原样展示=白话为什么差一档
}

export interface GatingLadderStep {
  tier?: string;
  name?: string;
  plain?: string;
  [k: string]: unknown;
}

// 可用态与空态并集：available 判别。空态只有 available:false + reason。
export type GovernanceGating =
  | {
      available: true;
      display_only: boolean | null;
      generated_at: string | null;
      config_version: string | null;
      config_status: string | null;
      ladder: GatingLadderStep[];
      promotions: Record<string, unknown>;
      tier_distribution: Record<string, number>; // 整体档位分布（各档域数）
      domain_count: number | null;
      reached_auto: string[];
      summary_note: string | null;
      sources: Record<string, unknown>;
      telemetry: Record<string, unknown>;
      honest_note: string | null;
      domains: GatingDomain[];
    }
  | { available: false; reason: string };

export const fetchGovernanceGating = (role: Role) =>
  apiGet<GovernanceGating>("/governance/gating", role);

// ═══════════════════════════ /collaboration/threads（协作流真身，U6）═══════════════════════════
// 对外协调的跟进线程（改配船期 / 工厂确认交期 / 客户接受拆单…），按风险聚合。X-World 双世界：
// sim 有真数据（28 条）、verify 为 seed 演示条（可能 0 条=正常）。缺表世界 → available:false 空态。
// X-Role 脱敏同 /objects（富化进来的 task.proposal_params.est_cost_usd 对无成本权限角色自动掩码）。
export interface CollabRiskRef {
  rule_id?: string | null;
  type?: string | null;
  severity?: string | null;
  status?: string | null;
}

export interface CollabThread {
  coordination_id: string;
  risk_event_id?: string | null;
  task_id?: string | null;
  state?: string | null;
  counterparty_type?: string | null;
  counterparty?: string | null;
  owner?: string | null;
  escalation_level?: number | null;
  opened_at?: string | null;
  last_update?: string | null;
  next_action_due?: string | null;
  risk?: CollabRiskRef | null;
  task?: Record<string, unknown> | null;
  [k: string]: unknown; // 两世界 schema 有差异，未知列原样透传
}

export interface CollabByRisk {
  risk_event_id: string | null;
  rule_id: string | null;
  severity: string | null;
  thread_count: number;
  states: Record<string, number>;
  coordination_ids: (string | null)[];
}

export interface CollaborationThreads {
  world: string;
  role: string;
  available: boolean;
  reason?: string; // 仅 available:false 时
  count: number;
  threads: CollabThread[];
  by_risk: CollabByRisk[];
  summary: {
    by_state: Record<string, number>;
    by_counterparty_type: Record<string, number>;
    escalated: number;
  };
}

export const fetchCollaborationThreads = (role: Role, riskEventId?: string | null) =>
  apiGet<CollaborationThreads>(
    `/collaboration/threads${riskEventId ? `?risk_event_id=${encodeURIComponent(riskEventId)}` : ""}`,
    role,
  );

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

// GET /objects/{type}（列表+等值过滤）薄封装——客户卡"出险订单"接线（轮3 余量①）用它按 rule_id
// 取小规模系统级候选池（R19/R21 全量，现状 ≤21 条，不随客户订单量增长，见 ObjectCard.tsx 用处
// 注释）。不做游标翻页——调用处场景本就是小结果集，翻页复杂度无必要。
export interface ObjectListResult {
  type: string;
  world: string;
  count: number;
  limit: number;
  items: ObjectFields[];
}

export const fetchObjectsByFilter = (
  type: string,
  filters: Record<string, string>,
  role: Role,
  limit = 300,
): Promise<ObjectListResult> => {
  const params = new URLSearchParams({ ...filters, limit: String(limit) });
  return apiGet<ObjectListResult>(`/objects/${encodeURIComponent(type)}?${params.toString()}`, role);
};

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

// ═══════════════════════════ /runtime/runs（AI 处置任务 runtime，规格④）═══════════════════════════
// 持久 Agent runtime 的 HTTP 门面（apps/api/runtime.py）：看/管 AI「处置差事」(run)——列出/查看每趟跑
// 到哪、每步干了什么（白话时间线）、预算还剩多少；替 ops 启动、批完续跑、manager 急停（kill）。
// 冻结区仍不可达（本组仅 list/detail/start/resume/kill 五动作，无一能执行审批/关闭/报价裁决）。双世界
// 跟随 X-World（runtime 两表落在对应世界库）、角色头 X-Role 同门；写操作 X-Actor 必填（后端审计留痕）。

// 预算余量摘要：三条余量（步/工具次数/秒）都由后端现算。列表不逐 run 数工具步 → tool_calls_used /
// tool_calls_remaining 为 null（前端按"未统计"呈现），详情里才有完整工具次数。
export interface RuntimeBudget {
  max_steps: number | null;
  steps_used: number;
  steps_remaining: number | null;
  max_tool_calls: number | null;
  tool_calls_used: number | null;
  tool_calls_remaining: number | null;
  max_seconds: number | null;
  spent_seconds: number;
  seconds_remaining: number | null;
}

// 单步视图（详情时间线）：白话 kind 标签 + payload/result 原样。think 步的 result.mode = llm|deterministic
// （LLM 不可用时优雅降级、如实标注，见 apps/api/runtime.py）。
export interface RuntimeStep {
  step_no: number;
  kind: string; // think | tool | command | wait | verify
  kind_label: string; // 白话标签（如"写后复读核实（读回数据库确认副作用真的发生）"）
  payload: Record<string, unknown>;
  result: Record<string, unknown> | null;
  created_at: string | null;
}

// 列表项：status/goal/budget 摘要/updated_at（+状态白话标签/步数/summary/killed）。
export interface RuntimeRunListItem {
  run_id: string;
  status: string;
  status_label: string; // 状态白话（"等待人工审批（去待拍板处批它）"…）
  goal: string; // "处置 RSK-0007"
  agent_role: string;
  killed: number; // 0/1
  steps: number; // 已落账步数
  updated_at: string | null;
  summary: string | null; // 白话结论（终态/等审批时后端写入；未有则 null）
  budget: RuntimeBudget;
}

export interface RuntimeRunsList {
  world: string;
  count: number;
  limit: number;
  items: RuntimeRunListItem[];
  note?: string; // 诚实空态：两表未建（本世界还没跑过任何 run）时的白话原因
  next_cursor?: string | null; // 仅当请求带 cursor 时出现
}

// 详情：状态 + 预算余量 + steps 白话时间线全量。
export interface RuntimeRunDetail {
  world: string;
  run_id: string;
  status: string;
  status_label: string;
  goal: string;
  agent_role: string;
  killed: number;
  summary: string | null;
  created_at: string | null;
  updated_at: string | null;
  budget: RuntimeBudget;
  steps: RuntimeStep[];
}

// start/resume 驱动结果信封：透传 run 驱动结果 + world + llm_mode + 状态白话标签（门面不代判，
// 见 apps/api/runtime.py::_run_envelope）。note/summary/task_id/approval_status 按分支可选出现。
export interface RuntimeRunEnvelope {
  run_id: string;
  status: string;
  status_label: string;
  world: string;
  llm_mode: string; // off（确定性剧本）/ auto（探测可用才出境）
  note?: string;
  summary?: string | null;
  task_id?: string;
  approval_status?: string; // pending / approved / rejected（resume 分支透传）
  killed?: number;
}

// kill 结果：ok + killed + status（+world+status_label）。幂等：重复 kill 返回同结果。
export interface RuntimeKillResult {
  ok: boolean;
  run_id: string;
  status: string;
  killed: number;
  world: string;
  status_label: string;
}

// GET：只读列表/详情（跟随 X-World / X-Role；列表诚实空态=count 0 + note，非 404/500）。
export const fetchRuntimeRuns = (role: Role) => apiGet<RuntimeRunsList>("/runtime/runs", role);

export const fetchRuntimeRunDetail = (role: Role, runId: string) =>
  apiGet<RuntimeRunDetail>(`/runtime/runs/${encodeURIComponent(runId)}`, role);

// ── 写通道 POST（X-Actor 必填 + 白话错误原样透传，同 postDecision 的"不吞错"约定）──
// runtime 三个 POST（启动/续跑/急停）不走 /decisions 的幂等意图键机制：启动的双击由前端"先查既有 run"
// 挡（见 AiRuns 的 AiDispatchButton）+ 后端命令总线内部写恰一次；续跑/急停后端天然幂等（终态 no-op /
// 重复 kill 同结果）。故这里只需薄 POST + 白话错误。网络级失败（fetch 未拿到响应）直接向上抛，调用方降级。
async function runtimePost<T>(
  path: string,
  role: Role,
  actor: string,
  body?: Record<string, unknown>,
): Promise<T> {
  const resp = await fetch(`${API_BASE_URL}${path}`, {
    method: "POST",
    // 写通道同样跟随当前世界（X-World，由 reqHeaders 带上）：在模拟世界里启动即写模拟库的 runtime 两表。
    headers: reqHeaders(role, { "Content-Type": "application/json", "X-Actor": actor }),
    body: JSON.stringify(body ?? {}),
  });
  if (!resp.ok) {
    // 后端 HTTPException detail 为白话中文（"缺少 X-Actor…""急停是 manager 专属…""任务运行不存在…"）。
    let detail = `提交失败：HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: unknown };
      if (typeof j.detail === "string" && j.detail) detail = j.detail;
    } catch {
      /* 非 JSON 响应：保留 HTTP 码兜底文案 */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

/** 启动一趟 AI 处置差事（POST /runtime/runs {goal_risk_id, llm?}）：同步推进到首个稳态（等审批/终态）。
 *  llm（波E② 真模型开关，可选）：true→该趟 think 走真模型（覆盖 env 默认，慢约 2-3 分钟、走订阅通道）；
 *  false→确定性脚本（快速）；undefined→不带 llm 键，随后端 env 默认（既有调用方 byte-identical，默认 off）。
 *  仅"透传"用户选择，真正的 probe/降级/如实回传 llm_mode 都在后端 apps/api/runtime.py（本层不代判）。 */
export const startRuntimeRun = (role: Role, actor: string, goalRiskId: string, llm?: boolean) => {
  const body: Record<string, unknown> = { goal_risk_id: goalRiskId };
  if (llm !== undefined) body.llm = llm; // 不勾/未指定时不注入 llm 键→保持既有请求体形状
  return runtimePost<RuntimeRunEnvelope>("/runtime/runs", role, actor, body);
};

// AI 账卡聚合（规格③：CommandWall AI 运营账卡补「AI 任务：进行中 N · 等拍板 M」）。/runtime/runs 现查、
// 按 status 归类：进行中 = created + running（非终态、且未停在待拍板）；等拍板 = waiting_approval。
// 该列表端点服务端不接 X-Role 参数（apps/api/runtime.py::list_runs_endpoint 只声明 cursor/limit/db_path）
// → 计数与角色无关，此处传 "manager" 仅为满足 reqHeaders 的 X-Role 恒带约定，不影响结果；X-World 随模块
// 单例。失败向上抛，调用方（CommandWall）诚实降级、不占位（不画假 0/—）。
export interface RuntimeRunsAgg {
  inProgress: number;
  waiting: number;
  total: number;
}
export async function fetchRuntimeRunsAgg(): Promise<RuntimeRunsAgg> {
  const d = await fetchRuntimeRuns("manager");
  let inProgress = 0;
  let waiting = 0;
  for (const r of d.items) {
    if (r.status === "waiting_approval") waiting += 1;
    else if (r.status === "created" || r.status === "running") inProgress += 1;
  }
  return { inProgress, waiting, total: d.items.length };
}

/** 断点续跑（POST …/resume）：等审批三分支语义透传（pending 不推进 / approved 写后复读→done / rejected→failed）。 */
export const resumeRuntimeRun = (role: Role, actor: string, runId: string) =>
  runtimePost<RuntimeRunEnvelope>(`/runtime/runs/${encodeURIComponent(runId)}/resume`, role, actor);

/** 急停（POST …/kill）：manager 专属（后端 X-Role 独立鉴权 403）+ X-Actor 必填；幂等留痕。 */
export const killRuntimeRun = (role: Role, actor: string, runId: string) =>
  runtimePost<RuntimeKillResult>(`/runtime/runs/${encodeURIComponent(runId)}/kill`, role, actor);

// ═══════════════════════════ /proposals/{task_id}/evidence（波E 证据链，规格③）═══════════════════════════
// 提案证据包：impact / precedents / trust / alternatives 四块现算（apps/api/evidence.py 纯读聚合，本前端
// 不改一行后端）。设计要点（渲染层必须照实呈现，不得把"无"画成 0/编数）：
//   · 每块都可能诚实空态：available:false + reason（缺 rule_id / 风险查无 / 治理域缺失）、empty:true（先例
//     首例 n=0 但检索成功）、字段 null + reason（算不出的延误天数/有效率）。
//   · 金额键 _usd 对无成本可见角色（ops）掩码为 MASK 串（"🔒无权查看"）——用 formatUsd/isMasked 识别，
//     不当数字画（同 objects 脱敏）。计数/比率/天数/档位不掩。
//   · effective_rate=null 意为"无已回填质量标签的例"（诚实空态），绝非 0%。

export interface EvidenceImpact {
  available?: boolean; // false=风险查无 → reason
  reason?: string;
  affected_order_lines?: number;
  amount_usd?: number | string; // number | MASK（ops 掩码）
  affected_customers?: number;
  requested_line_ids?: number;
  resolved_line_ids?: number;
  note?: string; // 无订单行 / 悬空 id 的白话说明（诚实非缺数）
  // 付款锚增量（轮3-Opus 歧义3 清偿）：R19/R21 提案附付款归并行（与 /cockpit/risk-impact 同一
  // 归并链、同一行形状）；非付款锚提案**不带**此键（byte-identical）。归并不到 → 空数组 + payment_note。
  payment_rows?: PaymentImpactRow[];
  payment_note?: string; // 归并不到时的诚实空态白话（锚付款无法唯一定位等）
}

export interface EvidenceEffectiveness {
  labeled: number;
  effective: number;
  partial: number;
  ineffective: number;
  unlabeled: number;
  effective_rate: number | null; // null=无已回填标签例（诚实空态，非 0）
  note: string;
}

export interface EvidenceExample {
  memory_id: string | null;
  risk_event_id: string | null;
  decision: string | null; // adopted / modified / rejected
  proposed_action: string | null;
  quality_label: string | null;
  outcome_resolved: string | null;
  outcome_days: number | null;
  decided_at: string | null;
  impact_usd: number | string | null; // number | MASK | null
  plain: string; // 白话结局（后端拼好，直接展示，不再前端拼译）
}

export interface EvidencePrecedents {
  available: boolean;
  reason?: string; // available:false（缺 rule_id）
  empty?: boolean; // true=首例（检索成功但 n=0）
  n: number;
  rule_id?: string;
  lane?: string;
  match_scope?: "rule_and_lane" | "rule_only" | string;
  widened?: boolean;
  by_decision?: Record<string, number>; // {adopted:N, modified:N, rejected:N}（仅在场决定）
  effectiveness?: EvidenceEffectiveness;
  recent_examples?: EvidenceExample[];
  widen_reason?: string; // 放宽到全航线取样的白话原因
  sample_note?: string; // n<5 样本不足白话（仅供参考）
  note?: string; // 首例白话
}

export interface EvidenceTrust {
  available: boolean;
  reason?: string; // available:false（缺 rule_id / 报告未生成 / 该域缺失）
  display_only?: boolean | null; // true=只算档不放权（档位≠已授权）
  rule_id?: string;
  domain?: string;
  name?: string | null; // 老板语言域名（如"延误击穿承诺"）
  group?: string | null;
  tier?: string | null; // 当前档位（shadow / suggest / ...）
  next_tier?: string | null;
  n?: number | null;
  hits?: number | null;
  rate?: number | null; // 历史一致率
  ci?: number[] | null; // [下界, 上界]
  low_sample?: boolean | null;
  gaps?: string[]; // 白话差距清单（"缺一致率：38.2% < 85.0%"）
  generated_at?: string | null;
  config_version?: string | null;
  note?: string;
}

export interface EvidenceActionHistorical {
  n: number;
  effective_rate: number | null; // null=无例 或 有例未回填（见 reason/note）
  reason?: string; // n=0 无历史案例
  labeled?: number;
  effective?: number;
  note?: string;
}

export interface EvidenceAlternativeOption {
  label: string; // 中文动作名（后端已译："加急" / "接受延误"）
  historical: EvidenceActionHistorical;
}

export interface EvidenceAlternatives {
  available: boolean;
  reason?: string; // available:false（风险查无）
  affected_value_usd?: number | string | null; // number | MASK | null
  delay_days?: number | null; // null=无货件 / 无列 → delay_reason
  options?: Record<string, EvidenceAlternativeOption>; // expedite / accept_delay
  note?: string;
  delay_reason?: string;
}

export interface ProposalEvidence {
  world: string;
  task_id: string;
  risk_event_id: string | null;
  proposed_action: string | null;
  approval_status: string | null;
  role: string;
  impact: EvidenceImpact;
  precedents: EvidencePrecedents;
  trust: EvidenceTrust;
  alternatives: EvidenceAlternatives;
}

/** 提案证据包（纯读；非 2xx 抛错，调用方降级为"证据暂不可用"，绝不阻断审批按钮）。 */
export const fetchProposalEvidence = (taskId: string, role: Role) =>
  apiGet<ProposalEvidence>(`/proposals/${encodeURIComponent(taskId)}/evidence`, role);

// ═══════════ /risk-events/{id}/evidence-package（V23④ 证据包导出）═══════════
// 单风险证据包（七块：对象快照/影响链/关联任务/时间线/协调/先例/元信息），json|html 两格式。
// 为什么走 fetch→Blob 而非直接 window.open(url)：脱敏/世界/经手人全靠 X-Role/X-World/X-Actor 请求头
// （宪法不变量 5：权限由 API 同源执行），window.open 发不了自定义头——直开会丢角色掩码语义。
// 故先带头 fetch 拿响应体，再转 Blob URL 交给新窗口/下载锚点；导出行为已在后端落 action_log
// （ExportEvidencePackage，actor=X-Actor 如实），前端不再另记。非 2xx 抛白话中文原文（不吞不美化）。
export async function fetchEvidencePackageBlob(
  riskEventId: string,
  role: Role,
  fmt: "json" | "html",
): Promise<Blob> {
  const resp = await fetch(
    `${API_BASE_URL}/risk-events/${encodeURIComponent(riskEventId)}/evidence-package?format=${fmt}`,
    { headers: reqHeaders(role, { "X-Actor": actorForRole(role) }) },
  );
  if (!resp.ok) {
    let detail = `导出失败：HTTP ${resp.status}`;
    try {
      const j = (await resp.json()) as { detail?: unknown };
      if (typeof j.detail === "string" && j.detail) detail = j.detail;
    } catch {
      /* 非 JSON 响应：保留 HTTP 码兜底文案 */
    }
    throw new Error(detail);
  }
  return await resp.blob();
}
