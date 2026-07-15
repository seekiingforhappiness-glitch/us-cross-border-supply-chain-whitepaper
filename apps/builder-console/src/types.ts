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

// ---- 关联织网 ----
export interface WeaveRel {
  dir: "out" | "in"; linkType: string; other: string; otherPlain: string; cardinality: string; plain: string;
}
export interface WeaveAction {
  id: string; name: string; tier: string; plain: string; executors: string[]; frozen: boolean;
  primary: boolean; target: string; targetPlain: string; signature: string;
  preconditions: string[]; successEffects: string[]; failureHandling: string[]; audit: string[];
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
  paramsSchema?: unknown; domain: string;
}
export interface ActionsData {
  title: string; question: string; subtitle: string;
  roles: { id: string; plain: string }[];
  actions: ActionRow[];
  tiers: { key: string; mark: string; name: string; tone: string }[];
  frozenNote: string;
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
