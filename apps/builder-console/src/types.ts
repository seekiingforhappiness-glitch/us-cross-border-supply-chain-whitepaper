// 数据契约：与 export_data.py 的输出一一对应。

export interface Meta {
  app: string;
  tagline: string;
  exportedAt: string;
  ontologyVersion: string;
  dbPath: string;
  tableCounts: Record<string, number>;
  readOnly: boolean;
  note: string;
}

export interface Field {
  name: string;
  type: string;
  desc?: string;
  values?: string[] | null;
}

// ---- 视图 1：结构 ----
export interface IntelItem {
  id: string;
  name: string;
  icon: string;
  plain: string;
  connectsTo: string[];
}
export interface DomainCard {
  id: string;
  name: string;
  plain: string;
  scene: string;
  objectCount: number;
  recordCount: number;
  objects: string[];
  shared: boolean;
}
export interface GovItem {
  id: string;
  name: string;
  icon: string;
  plain: string;
  guards: string[];
}
export interface StructureData {
  title: string;
  question: string;
  subtitle: string;
  layers: {
    intelligence: { name: string; plain: string; items: IntelItem[] };
    business: { name: string; plain: string; items: DomainCard[] };
    governance: { name: string; plain: string; items: GovItem[] };
  };
  totals: {
    objects: number;
    links: number;
    actions: number;
    riskRules: number;
    roles: number;
    records: number;
  };
}

// ---- 视图 2：旅程 ----
export interface Station {
  id: string;
  step: string;
  title: string;
  object: string;
  plain: string;
  fields: Field[];
  canDo: { action: string; by: string[]; note: string };
  count: number;
  countLabel: string;
  example?: string;
  frozen?: boolean;
}
export interface JourneyData {
  title: string;
  question: string;
  subtitle: string;
  caseHeadline: { shipment: string; risk: string; line: string };
  stations: Station[];
}

// ---- 视图 3：宪法 ----
export interface Article {
  no: string;
  text: string;
  plain: string;
  prevents: string;
}
export interface ConstitutionData {
  title: string;
  question: string;
  subtitle: string;
  groups: { group: string; plain: string; articles: Article[] }[];
  freezeZone: { name: string; plain: string; items: { name: string; why: string }[] };
  decisionTiers: { tier: string; plain: string }[];
  source: string;
}

// ---- 视图 4：飞轮 ----
export interface Asset {
  id: string;
  order: number;
  name: string;
  plain: string;
  metricLabel: string;
  metric: number;
  metricUnit?: string;
  sub: string;
  table: string | null;
}
export interface FlywheelData {
  title: string;
  question: string;
  subtitle: string;
  assets: Asset[];
  funnel: { stage: string; count: number; plain: string }[];
  honestState: string;
}

// ---- 视图 5：运行账本 ----
export interface AIActivityData {
  title: string;
  question: string;
  subtitle: string;
  llm: {
    total: number;
    byType: { type: string; n: number }[];
    byStatus: { status: string; n: number }[];
    estTokens: number;
    degraded: number;
  };
  llmNote: string;
  automation: {
    total: number;
    byAction: { action: string; n: number; plain: string }[];
    byRole: { role: string; n: number }[];
    span: { from: string; to: string };
    plain: string;
  };
  funnel: { stage: string; count: number; note: string }[];
  funnelPlain: string;
}

// ---- 视图 6：决策血缘 ----
export interface QuartetPhase {
  phase: string;
  actor: string;
  tier: string;
  plain: string;
  evidence?: {
    shipment: Record<string, unknown>;
    milestones: { event_type: string; event_classifier: string; event_time: string; event_locode: string }[];
    affectedLines: string[];
    exposureUsd: number;
    severity: string;
    rootCause: string;
  };
  proposal?: { action: string; params: Record<string, unknown>; estCostUsd: number; expectedNewEta: string };
  decision?: { approvalStatus: string; approvedByRole: string; actionTaken: string };
  outcome?: { taskStatus: string; precedentRecorded: boolean };
}
export interface DecisionLineageData {
  title: string;
  question: string;
  subtitle: string;
  caseId: string;
  riskId: string;
  shipmentId: string;
  quartet: QuartetPhase[];
  sourceNote: string;
}
