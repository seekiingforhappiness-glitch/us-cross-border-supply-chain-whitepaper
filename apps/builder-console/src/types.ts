// 数据契约：与 export_data.py 的输出一一对应。

export interface Meta {
  app: string;
  tagline: string;
  exportedAt: string;
  ontologyVersion: string;
  dbPath: string;
  verifyDbPath: string;
  worldNote: string;
  tableCounts: Record<string, number>;
  readOnly: boolean;
  note: string;
}

export interface Field {
  name: string;
  type: string;
  required?: boolean;
  desc?: string;
  values?: string[] | null;
  sensitive?: string[];
}

// ---- 结构（封面） ----
export interface IntelItem { id: string; name: string; icon: string; plain: string; connectsTo: string[]; }
export interface DomainCard {
  id: string; name: string; plain: string; scene: string;
  objectCount: number; coveredTypes?: number; recordCount: number; objects: string[]; shared: boolean;
}
export interface GovItem { id: string; name: string; icon: string; plain: string; guards: string[]; }
export interface StructureData {
  title: string; question: string; subtitle: string;
  layers: {
    intelligence: { name: string; plain: string; items: IntelItem[] };
    business: { name: string; plain: string; items: DomainCard[] };
    governance: { name: string; plain: string; items: GovItem[] };
  };
  totals: { objects: number; links: number; actions: number; riskRules: number; roles: number; records: number; };
}

// ---- 旅程 ----
export interface Station {
  id: string; step: string; title: string; object: string; plain: string;
  fields: Field[]; canDo: { action: string; by: string[]; note: string };
  count: number; countLabel: string; example?: string; frozen?: boolean;
}
export interface JourneyData {
  title: string; question: string; subtitle: string; worldSource?: string;
  caseHeadline: { shipment: string; risk: string; line: string };
  stations: Station[];
}

// ---- 宪法 ----
export interface Article { no: string; text: string; plain: string; prevents: string; }
export interface ConstitutionData {
  title: string; question: string; subtitle: string;
  groups: { group: string; plain: string; articles: Article[] }[];
  freezeZone: { name: string; plain: string; items: { name: string; why: string; action: string | null }[] };
  decisionTiers: { tier: string; plain: string }[];
  source: string;
}

// ---- 动作作为 AI 工具（v3 板块③ · 直读本体 M1/M2 桥2 字段） ----
export interface ToolInputSchema {
  type?: string;
  properties?: Record<string, { type?: string; enum?: string[] }>;
  required?: string[];
}
export interface ActionToolView {
  exposedAsTool: boolean;
  aiExecutable: string | null;      // auto | confirm | never | frozen
  aiExecutablePlain: string;
  enforcement: string | null;
  frozen: boolean;
  toolName: string | null;          // snake_case（本体 signature 函数名）
  toolDescription: string | null;
  toolInputSchema: ToolInputSchema | null;
}

// ---- 关联织网 ----
export interface WeaveRel {
  dir: "out" | "in"; linkType: string; other: string; otherPlain: string; cardinality: string; plain: string;
}
export interface WeaveAction {
  id: string; name: string; tier: string; plain: string; executors: string[]; frozen: boolean;
  primary: boolean; target: string; targetPlain: string; signature: string;
  preconditions: string[]; successEffects: string[]; failureHandling: string[]; audit: string[];
  tool?: ActionToolView;
}
export interface WeaveRule { id: string; watch: string; plain: string; }
export interface StateMachine {
  states: string[];
  transitions: { from: string; to: string; trigger: string }[];
  notes?: string;
}
export interface WeaveType {
  type: string; plainName: string; why: string; domain: string;
  ownerRole?: string; statusField?: string; covered: boolean; count: number;
  fieldCount: number; fields: Field[]; stateMachine: StateMachine | null;
  relationships: WeaveRel[]; actions: WeaveAction[]; rules: WeaveRule[];
}
export interface WeaveDomainRef {
  id: string; name: string; plain: string; scene: string;
  types: { type: string; plainName: string; count: number; covered: boolean }[];
}
export interface WeaveData {
  title: string; question: string; subtitle: string; hint: string; defaultType: string;
  domains: WeaveDomainRef[]; types: Record<string, WeaveType>;
}

// ---- 实体浏览（懒加载 public/data/entity.json） ----
export interface EntityFieldVal { name: string; value: string | number | null; sensitive?: string[]; }
export interface EntityRel { dir: "out" | "in"; targetType: string; targetId: string | null; plain: string; more?: boolean; }
export interface TimelineEvent {
  date: string; kind: string; plain: string; family: string | null;
  severity: string | null; causedBy: string | null; lane: string; actor?: string;
}
export interface EntityInstance { id: string; fields: EntityFieldVal[]; rels: EntityRel[]; timeline: TimelineEvent[]; }
export interface EntityType {
  type: string; plainName: string; why?: string; domain: string; covered: boolean; count: number;
  indexFields: string[]; index: Record<string, string | number | null>[];
  detailCount?: number; instances: Record<string, EntityInstance>;
}
export interface EntityData {
  title: string; question: string; subtitle: string; worldSource: string;
  hero: { type: string; id: string; note: string };
  domains: { id: string; name: string; types: { type: string; plainName: string; count: number; covered: boolean }[] }[];
  types: Record<string, EntityType>;
}

// ---- 规则档案 ----
export interface RuleCard {
  id: string; type: string; target: string; targetPlain: string; watch: string;
  plain: string; logic: string; severityRule: string;
  living: { total: number; bySeverity: Record<string, number> }; domain: string;
}
export interface RulesData {
  title: string; question: string; subtitle: string; cards: RuleCard[];
  twoWorlds: {
    living: { label: string; source: string; plain: string };
    verify: { label: string; source: string; plain: string };
  };
  verifyPrecision: string;
}

// ---- 动作与权限 ----
export interface ActionRow {
  id: string; name: string; signature: string; target: string; targetPlain: string;
  tier: string; tierMark: string; plain: string; executors: string[]; executorsPlain: string[];
  systemCan: boolean; frozen: boolean; perms: Record<string, boolean>;
  preconditions: string[]; successEffects: string[]; failureHandling: string[]; audit: string[];
  paramsSchema?: unknown; tool: ActionToolView; domain: string;
}
export interface ActionsData {
  title: string; question: string; subtitle: string;
  roles: { id: string; plain: string }[];
  actions: ActionRow[];
  tiers: { key: string; mark: string; name: string; tone: string }[];
  toolSummary: { exposed: number; frozen: number; total: number; note: string };
  frozenNote: string;
}

// ---- 影响分析专题（v3 板块② · impact.json） ----
export interface StorageDecl {
  kind: string; table: string; column: string;
  discriminator?: string; discriminator_value?: string;
}
export interface ImpactLink {
  linkType: string; dir: "out" | "in"; other: string; otherPlain: string;
  cardinality: string; plain: string; storage: StorageDecl | null;
}
export interface ImpactRule { id: string; watch: string; plain: string; domain: string; }
export interface ImpactAction {
  id: string; name: string; tier: string; plain: string; primary: boolean;
  frozen: boolean; exposedAsTool: boolean; toolName: string | null;
}
export interface ImpactQueryTool { name: string; domain: string; description: string; }
export interface ImpactWriteTool {
  actionId: string; name: string; toolName: string; description: string; primary: boolean;
}
export interface ImpactField {
  name: string; type: string; desc: string; sensitive: string[] | null;
  carriesLink: string | null; exposedByTools: string[];
}
export interface ImpactType {
  type: string; plainName: string; why: string; domain: string; covered: boolean; count: number;
  links: ImpactLink[]; rules: ImpactRule[]; actions: ImpactAction[];
  queryTools: ImpactQueryTool[]; writeTools: ImpactWriteTool[];
  sensitive: { field: string; visibleTo: string[] }[]; fields: ImpactField[];
  counts: { links: number; rules: number; actions: number; tools: number; sensitive: number; instances: number };
}
export interface ImpactDomainRef {
  id: string; name: string; plain: string; scene: string;
  types: { type: string; plainName: string; impact: number; covered: boolean }[];
}
export interface ImpactData {
  title: string; question: string; subtitle: string; hint: string; defaultType: string;
  domains: ImpactDomainRef[]; types: Record<string, ImpactType>;
}

// ---- 治理控制室（第15视图 · governance.json · G-Dashboard） ----
export interface GovSource {
  key: string; label: string; path: string; table: string; status: string;
}
export interface GovSevenQ {
  q: string; plain: string; answeredBy: string; status: string; detail: string;
}
export interface GovNameN { type?: string; provider?: string; status?: string; result?: string; n: number; }
export interface GovTelemetry {
  source: string; available: boolean; total: number;
  byType: { type: string; n: number }[];
  byProvider: { provider: string; n: number }[];
  byStatus: { status: string; n: number }[];
  degraded: number;
  latencyMs: { p50: number | null; p95: number | null } | null;
  tokens: { input: number; output: number };
  tokensP?: { p50: number | null; p95: number | null };
  note: string;
}
export interface GovSecurity {
  source: string; deniedLive: number; actionLogTotal: number;
  byResult: { result: string; n: number }[];
  injection: { result: string; ref: string; asOf: string; note: string };
  note: string;
}
export interface GovGoldsetRun { run_id: string; created_at: string; n: number; passed: number; }
export interface GovGoldset {
  source: string | null; available: boolean;
  runs: GovGoldsetRun[];
  latest: { run_id: string; n: number; passed: number; rate: number | null } | null;
  llm: { run_id: string; n: number; passed: number; rate: number | null } | null;
  note: string;
}
export interface GovSlice { key: string; n: number; consistent: number; rate: number | null; lowConfidence: boolean; }
export interface GovShadow {
  source: string; measured: boolean; minSliceN: number;
  attempted: number; parsed: number;
  overall: { n: number; consistent: number; rate: number | null } | null;
  byRule: GovSlice[]; byLane: GovSlice[]; bySeverity: GovSlice[];
  population: {
    total: number; source: string;
    byRule: { key: string; n: number }[];
    bySeverity: { key: string; n: number }[];
  } | null;
  escalation: { escalatedCases: number; recall: number | null; note: string } | null;
  note: string;
}
export interface GovLedgerRun { as_of: string; created_at: string; rules: number; total: number; errors: number; }
export interface GovLedgerRule { rule_id: string; rule_version: string | null; detected_count: number; fp: string | null; status: string; }
export interface GovLedger {
  source: string | null; available: boolean;
  runs: GovLedgerRun[];
  latestByRule: GovLedgerRule[];
  diff: {
    asOf: string; before: string; after: string;
    changed: { rule_id: string; before: number | null; after: number | null }[];
    idempotent: boolean;
  } | null;
  note: string;
}
export interface GovAssertItem { id: string; text: string; checked: boolean; test: string; }
export interface GovAssertGroup { section: string; name: string; items: GovAssertItem[]; }
export interface GovGateItem { text: string; checked: boolean; }
export interface GovGateGroup { gate: string; items: GovGateItem[]; }
export interface GovCoverage {
  source: string; asOf: string | null;
  assertions: { available: boolean; groups: GovAssertGroup[]; total: number; checked: number };
  gates: { available: boolean; gates: GovGateGroup[]; total: number; checked: number; lastVerified: string | null };
  reproduce: string; note: string;
}
export interface GovLineageInstance {
  trace_id: string; call_type: string; provider: string; model: string | null; status: string;
  log_id: number; actor: string; action: string; target_object_id: string; result: string;
}
export interface GovLineage {
  schemaReady: boolean; llmCallsHasTrace: boolean; actionLogHasTrace: boolean;
  instances: GovLineageInstance[]; actionLogTraceNonNull?: number; note: string;
}
export interface GovGuardrail {
  key: string; label: string; value: number; pass: boolean; unit: string; plain: string; source: string;
}
export interface GovCost {
  source: string; available: boolean;
  byTypeProvider: { call_type: string; provider: string; n: number; tokens: number; duration_ms: number }[];
  note: string;
}
export interface GovernanceData {
  title: string; question: string; subtitle: string;
  sources: GovSource[];
  sevenQuestions: GovSevenQ[];
  telemetry: GovTelemetry;
  security: GovSecurity;
  goldset: GovGoldset;
  shadow: GovShadow;
  ledger: GovLedger;
  coverage: GovCoverage;
  lineage: GovLineage;
  guardrails: GovGuardrail[];
  cost: GovCost;
}

// ---- 全局搜索索引（v3 板块① · public/data/search.json 懒加载） ----
export interface SearchItem { id: string; t: string; s: string; }
export interface SearchIndex {
  title: string;
  types: Record<string, { plainName: string; domain: string; count: number }>;
  items: SearchItem[];
  total: number;
  note: string;
}

// ---- 演进史 ----
export interface DecisionEntry {
  code: string; title: string; date: string; approver: string; oneLine: string;
  series: string; seriesName: string; tone: string; milestone: boolean; srcIndex: number;
}
export interface EvolutionData {
  title: string; question: string; subtitle: string; entries: DecisionEntry[];
  series: { key: string; name: string; tone: string }[]; source: string; count: number;
}

// ---- 世界设定集 ----
export interface Forwarder {
  forwarder_id: string; name: string; quote_level: number; volumetric_tendency: number;
  notify_delay_min: number; notify_delay_max: number; billing_error_rate: number;
  credibility: number; shipments: number;
}
export interface WorldData {
  title: string; question: string; subtitle: string; worldSource: string;
  company: Record<string, string | number>;
  forwarders: Forwarder[];
  customers: { total: number; byTier: { tier: string; region: string; n: number }[] };
  routes: { route_id: string; origin_ports: string; dest_ports: string; transit_min: number; transit_max: number; weight: number }[];
  season: { month: string; n: number }[];
  spectrum: { total: number; families: { family: string; n: number; plain: string }[] };
  forwarderParams: { key: string; label: string; plain: string; good: string }[];
}

// ---- 越用越强 ----
export interface Asset {
  id: string; order: number; name: string; plain: string;
  metricLabel: string; metric: number; metricUnit?: string; sub: string; source: string | null;
}
export interface FlywheelData {
  title: string; question: string; subtitle: string; worldSource?: string;
  assets: Asset[]; funnel: { stage: string; count: number; plain: string }[];
  honestState: string; quality: { ql: string; n: number }[];
}

// ---- AI 账本 ----
export interface AIActivityData {
  title: string; question: string; subtitle: string;
  sim: {
    label: string; source: string; total: number;
    byActivity: { activity: string; n: number; plain: string }[];
    byActor: { actor: string; n: number }[];
    span: { from: string | null; to: string | null }; plain: string;
  };
  llm: {
    label: string; source: string; total: number;
    byType: { type: string; n: number }[]; estTokens: number; plain: string;
  };
}

// ---- 决策回放 ----
export interface QuartetPhase {
  phase: string; actor: string; tier: string; plain: string;
  evidence?: {
    shipment: Record<string, string | number>;
    chain: { sim_date: string; event_kind: string; family: string; severity: string; caused_by: string | null }[];
    affectedLines: string[]; exposureUsd: number; severity: string; rootCause: string;
  };
  proposal?: { action: string; economics: Record<string, string | number>; verdict: string; precedentBlock: string; cited: string };
  decision?: { approvalStatus: string; approvedByRole: string; actionTaken: string; decision: string };
  outcome?: { taskStatus: string; memoryId: string; outcomeResolved: string; outcomeDays: number; qualityLabel: string; precedentRecorded: boolean };
}
export interface DecisionLineageData {
  title: string; question: string; subtitle: string; worldSource?: string;
  caseId: string; riskId: string; shipmentId: string;
  quartet: QuartetPhase[];
  aiTrace: { sim_date: string; actor: string; activity: string; detail: string }[];
  sourceNote: string;
}
