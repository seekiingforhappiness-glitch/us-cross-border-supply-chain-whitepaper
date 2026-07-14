// ⚠️ 等距分层图——透视镜 v3 素材（V10 决议自驾驶舱移除归档）。
// 全景聚合 + 等距 2.5D 投影 + 影响走廊 BFS 的纯函数层，是"系统结构可视化"（对象分层）的算法，
// 属后台透视镜语言。V10 自驾驶舱移除、归档于此供透视镜 v3 迁移；src/archive 已在 tsconfig 排除编译。
// 全景聚合与等距布局的纯函数层（无 React、无副作用，便于单测）。
// V9-A 根治：点阵→结构。把 API 的实体/分组节点在前端聚合为「组块」，避免 512 条实体级
// 细线乱麻；V9 追加修正：全景只做聚合级呈现，实体明细走影响面板的紧凑表格，绝不在画布画
// 实体线网。聚合仅对 API 已给字段做前端分组，零编造。
import {
  nodeToObjectRef,
  type ObjectRef,
  type Panorama as PanoData,
  type PanoAlert,
  type PanoLayerName,
  type PanoNode,
} from "../api";

export const LAYER_ORDER: PanoLayerName[] = [
  "customers",
  "orders",
  "shipments",
  "suppliers",
  "warehouses",
];
export const LAYER_CN: Record<PanoLayerName, string> = {
  customers: "客户",
  orders: "订单",
  shipments: "在途",
  suppliers: "供应商",
  warehouses: "仓库",
};
// 层图标名（Icons.tsx）
export const LAYER_ICON: Record<PanoLayerName, string> = {
  customers: "person",
  orders: "box",
  shipments: "ship",
  suppliers: "factory",
  warehouses: "rack",
};

const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };
export type Tone = "red" | "amber" | "busy" | "ok";

const MAX_BLOCKS_PER_LAYER = 14; // 单层组块上限，超过则最小者并入「其他」（可读阈值守护）
const BASE_EDGE_CAP = 46; // 底层主干线上限（faint），按关系数量取最强
export const LIT_TRUNK_CAP = 5; // 影响走廊点亮的粗光路上限（≤5，见 V9 追加修正）
const LIT_BLOCK_CAP = 10; // 走廊点亮组块上限（取关系最强，其余降暗保聚焦，非相关不喧宾夺主）

export interface BlockMember {
  id: string;
  label: string;
  ref: ObjectRef | null;
  note?: string;
}

export interface DisplayBlock {
  id: string;
  layer: PanoLayerName;
  label: string;
  sub: string;
  metric: number; // 组块大小驱动（成员数 / 单量 / 票数）
  alertCount: number;
  alerts: PanoAlert[];
  sev: number; // -1 无告警
  tone: Tone;
  members: BlockMember[]; // 实体成员（仅前端聚合的世界有；API 已分组世界为空 = 终端块）
  ref: ObjectRef | null; // 单实体块才有对象引用（双击直开卡）
}

export interface DisplayEdge {
  source: string;
  target: string;
  count: number;
}

export interface DisplayModel {
  blocks: DisplayBlock[];
  blocksByLayer: Record<PanoLayerName, DisplayBlock[]>;
  edges: DisplayEdge[]; // 全量聚合边（供 BFS）
  baseEdges: DisplayEdge[]; // 底层渲染用（cap 后）
  adj: Map<string, string[]>;
}

function maxSev(alerts: PanoAlert[]): number {
  if (!alerts || alerts.length === 0) return -1;
  return Math.max(...alerts.map((a) => SEV_RANK[a.severity] ?? 1));
}

function toneOf(sev: number, warn: boolean, layer: PanoLayerName): Tone {
  if (sev >= 2) return "red";
  if (sev >= 0 || warn) return "amber";
  if (layer === "shipments") return "busy";
  return "ok";
}

// 组块工厂：把一批「同组」原始节点折叠为一个 DisplayBlock。
function fold(
  id: string,
  layer: PanoLayerName,
  label: string,
  members: PanoNode[],
  opts: { sub: string; metric: number; warn: boolean; keepMembers: boolean },
): DisplayBlock {
  const alerts: PanoAlert[] = [];
  let alertCount = 0;
  for (const n of members) {
    alertCount += n.alert_count || 0;
    for (const a of n.alerts || []) if (alerts.length < 8) alerts.push(a);
  }
  const sev = maxSev(alerts);
  const memberRefs: BlockMember[] = opts.keepMembers
    ? members.map((n) => ({
        id: n.id,
        label: n.label,
        ref: nodeToObjectRef(n.id),
        note: n.status ?? undefined,
      }))
    : [];
  const singleRef = memberRefs.length === 1 ? memberRefs[0].ref : null;
  return {
    id,
    layer,
    label,
    sub: opts.sub,
    metric: Math.max(1, opts.metric),
    alertCount,
    alerts,
    sev,
    tone: toneOf(sev, opts.warn, layer),
    members: memberRefs,
    ref: singleRef,
  };
}

function groupBy<T>(rows: T[], key: (r: T) => string): Map<string, T[]> {
  const m = new Map<string, T[]>();
  for (const r of rows) {
    const k = key(r);
    (m.get(k) ?? m.set(k, []).get(k)!).push(r);
  }
  return m;
}

// 单层超阈值时把最小的若干组并入「其他×N」，保画面可读（V9 追加修正的通用原则）。
function capLayer(blocks: DisplayBlock[], layer: PanoLayerName): DisplayBlock[] {
  if (blocks.length <= MAX_BLOCKS_PER_LAYER) return blocks;
  const sorted = [...blocks].sort((a, b) => b.metric - a.metric);
  const keep = sorted.slice(0, MAX_BLOCKS_PER_LAYER - 1);
  const rest = sorted.slice(MAX_BLOCKS_PER_LAYER - 1);
  const alerts: PanoAlert[] = [];
  let alertCount = 0;
  let metric = 0;
  const members: BlockMember[] = [];
  for (const b of rest) {
    alertCount += b.alertCount;
    metric += b.metric;
    for (const a of b.alerts) if (alerts.length < 8) alerts.push(a);
    members.push(...b.members);
  }
  const sev = maxSev(alerts);
  keep.push({
    id: `grp:${layer}:其他`,
    layer,
    label: "其他",
    sub: `${rest.length} 组归并`,
    metric: Math.max(1, metric),
    alertCount,
    alerts,
    sev,
    tone: toneOf(sev, false, layer),
    members,
    ref: null,
  });
  return keep;
}

/**
 * 从 panorama 载荷构建聚合显示模型：
 *  - customers：分组世界直取；实体世界按 us_state 聚合。
 *  - orders：API 恒按客户锚点聚合，此处按该客户所属 state 再归并成州级订单簇。
 *  - shipments：分组世界（lane）直取；实体世界按 lane 字段聚合。
 *  - suppliers：分组世界（city）直取；实体世界按 city 聚合。
 *  - warehouses：恒实体（3-5），一块一实体。
 * 边：把原始边的两端映射到各自组块 id，累加 count；自环丢弃。
 */
export function buildDisplay(data: PanoData): DisplayModel {
  const rawToBlock = new Map<string, string>(); // 原始节点 id → 组块 id（供边重映射）
  const custState = new Map<string, string>(); // CUS-x → state（供订单归并）

  // —— customers ——
  const custLayer = data.layers.customers;
  let custBlocks: DisplayBlock[];
  if (custLayer.granularity === "group") {
    custBlocks = custLayer.nodes.map((n) => {
      rawToBlock.set(n.id, n.id);
      return fold(n.id, "customers", n.label, [n], {
        sub: `${n.count ?? 0} 客户`,
        metric: n.count ?? 1,
        warn: false,
        keepMembers: false,
      });
    });
  } else {
    for (const n of custLayer.nodes) {
      const st = n.us_state || "未知";
      custState.set(n.id.replace(/^customer:/, ""), st);
    }
    const g = groupBy(custLayer.nodes, (n) => n.us_state || "未知");
    custBlocks = [...g.entries()].map(([st, nodes]) => {
      const id = `grp:customers:${st}`;
      for (const n of nodes) rawToBlock.set(n.id, id);
      return fold(id, "customers", st, nodes, {
        sub: `${nodes.length} 客户`,
        metric: nodes.length,
        warn: false,
        keepMembers: true,
      });
    });
  }
  custBlocks = capLayer(custBlocks, "customers");

  // —— orders（恒按客户锚点，再归并到 state）——
  const orderRaw = data.layers.orders.nodes;
  const orderState = groupBy(orderRaw, (n) => {
    const anchor = n.id.replace(/^orders:/, "");
    if (anchor.startsWith("customer:")) {
      return custState.get(anchor.replace(/^customer:/, "")) || "未知";
    }
    if (anchor.startsWith("customers:")) return anchor.replace(/^customers:/, "") || "未知";
    return "未知";
  });
  const orderBlocks = capLayer(
    [...orderState.entries()].map(([st, nodes]) => {
      const id = `grp:orders:${st}`;
      let so = 0;
      let line = 0;
      let atRisk = 0;
      for (const n of nodes) {
        rawToBlock.set(n.id, id);
        so += n.so_count ?? 0;
        line += n.line_count ?? 0;
        atRisk += n.at_risk_lines ?? 0;
      }
      const b = fold(id, "orders", st, nodes, {
        sub: `${so} 单 · ${line} 行${atRisk ? ` · ${atRisk} 风险行` : ""}`,
        metric: so,
        warn: atRisk > 0,
        keepMembers: false,
      });
      return b;
    }),
    "orders",
  );

  // —— shipments ——
  const shipLayer = data.layers.shipments;
  let shipBlocks: DisplayBlock[];
  if (shipLayer.granularity === "group") {
    shipBlocks = shipLayer.nodes.map((n) => {
      rawToBlock.set(n.id, n.id);
      const delayed = n.delayed_count ?? 0;
      return fold(n.id, "shipments", n.label, [n], {
        sub: `${n.count ?? 0} 票${delayed ? ` · ${delayed} 延误` : ""}`,
        metric: n.count ?? 1,
        warn: delayed > 0,
        keepMembers: false,
      });
    });
  } else {
    const g = groupBy(shipLayer.nodes, (n) => n.lane || "?→?");
    shipBlocks = [...g.entries()].map(([lane, nodes]) => {
      const id = `grp:shipments:${lane}`;
      for (const n of nodes) rawToBlock.set(n.id, id);
      const delayed = nodes.filter((n) => (n.delay_days ?? 0) > 0).length;
      return fold(id, "shipments", lane, nodes, {
        sub: `${nodes.length} 票${delayed ? ` · ${delayed} 延误` : ""}`,
        metric: nodes.length,
        warn: delayed > 0,
        keepMembers: true,
      });
    });
  }
  shipBlocks = capLayer(shipBlocks, "shipments");

  // —— suppliers ——
  const supLayer = data.layers.suppliers;
  let supBlocks: DisplayBlock[];
  if (supLayer.granularity === "group") {
    supBlocks = supLayer.nodes.map((n) => {
      rawToBlock.set(n.id, n.id);
      return fold(n.id, "suppliers", n.label, [n], {
        sub: `${n.count ?? 0} 供应商`,
        metric: n.count ?? 1,
        warn: false,
        keepMembers: false,
      });
    });
  } else {
    const g = groupBy(supLayer.nodes, (n) => n.city || "未知");
    supBlocks = [...g.entries()].map(([city, nodes]) => {
      const id = `grp:suppliers:${city}`;
      for (const n of nodes) rawToBlock.set(n.id, id);
      return fold(id, "suppliers", city, nodes, {
        sub: `${nodes.length} 供应商`,
        metric: nodes.length,
        warn: false,
        keepMembers: true,
      });
    });
  }
  supBlocks = capLayer(supBlocks, "suppliers");

  // —— warehouses（恒实体）——
  const whBlocks = data.layers.warehouses.nodes.map((n) => {
    rawToBlock.set(n.id, n.id);
    const breach = n.safety_breach_count ?? 0;
    return fold(n.id, "warehouses", n.label, [n], {
      sub: `${n.type ?? ""}${breach ? ` · 击穿 ${breach}` : ""}`.trim(),
      metric: 1 + breach,
      warn: breach > 0,
      keepMembers: true,
    });
  });

  const blocksByLayer: Record<PanoLayerName, DisplayBlock[]> = {
    customers: custBlocks,
    orders: orderBlocks,
    shipments: shipBlocks,
    suppliers: supBlocks,
    warehouses: whBlocks,
  };
  const blocks = LAYER_ORDER.flatMap((l) => blocksByLayer[l]);
  const valid = new Set(blocks.map((b) => b.id));

  // —— 边重映射（原始 512 边 → 聚合主干）——
  const acc = new Map<string, DisplayEdge>();
  for (const e of data.edges) {
    const s = rawToBlock.get(e.source);
    const t = rawToBlock.get(e.target);
    if (!s || !t || s === t || !valid.has(s) || !valid.has(t)) continue;
    const key = `${s} ${t}`;
    const cur = acc.get(key);
    if (cur) cur.count += e.count;
    else acc.set(key, { source: s, target: t, count: e.count });
  }
  const edges = [...acc.values()];
  const baseEdges = [...edges].sort((a, b) => b.count - a.count).slice(0, BASE_EDGE_CAP);

  const adj = new Map<string, string[]>();
  for (const e of edges) {
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
  }

  return { blocks, blocksByLayer, edges, baseEdges, adj };
}

// ═══════════════ 等距投影（2.5D 分层）═══════════════
// 每层是一块半透明等距菱形面板（薄板岩浮板），沿纵深堆叠。世界坐标 (u,v)∈[0,1]² 投到屏幕。
export const VB_W = 1180;
export const VB_H = 864;
export const CX = VB_W / 2;
export const ISO_HW = 452; // 面板半宽
export const ISO_HH = 88; // 面板半高（HW/HH≈5，浅等距角，堆叠清晰）
export const SLAB = 9; // 浮板侧厚
export const TOP_MARGIN = 42;
export const LAYER_STEP = 150; // 层间纵向步距（>面板厚薄叠，留纵深）

export function planeTopY(i: number): number {
  return TOP_MARGIN + i * LAYER_STEP;
}

/** 面板内世界坐标 (u,v) → 屏幕坐标。(0,0)=顶点、(1,1)=底点、(1,0)=右、(0,1)=左。 */
export function projectFloor(topY: number, u: number, v: number): [number, number] {
  return [CX + (u - v) * ISO_HW, topY + (u + v) * ISO_HH];
}

/** 面板四角（顶/右/底/左），供绘制菱形浮板。 */
export function planeCorners(topY: number): { top: [number, number]; right: [number, number]; bottom: [number, number]; left: [number, number] } {
  return {
    top: [CX, topY],
    right: [CX + ISO_HW, topY + ISO_HH],
    bottom: [CX, topY + 2 * ISO_HH],
    left: [CX - ISO_HW, topY + ISO_HH],
  };
}

// ═══════════════ 影响走廊（聚合级）═══════════════
// V9 追加修正：不再画实体级线网。点击组块 → BFS 深度 2 点亮受影响的下游组块（聚合级），
// 明细传播链改由影响面板的紧凑表格呈现。affected 供影响面板「受影响的下游组」段直接用。
export interface AffectedBlock {
  id: string;
  layer: PanoLayerName;
  label: string;
  weight: number; // 该组与走廊的关系数量（聚合投影，非编造）
  sev: number;
}

export interface PanoSelection {
  block: DisplayBlock;
  affected: AffectedBlock[];
}

export function corridorFrom(
  model: DisplayModel,
  focusId: string,
  depth = 2,
): { litHops: Map<string, number>; affected: AffectedBlock[] } {
  const litHops = new Map<string, number>([[focusId, 0]]);
  const queue: string[] = [focusId];
  while (queue.length) {
    const u = queue.shift()!;
    const du = litHops.get(u)!;
    if (du >= depth) continue;
    for (const v of model.adj.get(u) ?? []) {
      if (!litHops.has(v)) {
        litHops.set(v, du + 1);
        queue.push(v);
      }
    }
  }
  const byId = new Map(model.blocks.map((b) => [b.id, b]));
  const all: AffectedBlock[] = [];
  for (const [id, hop] of litHops) {
    if (id === focusId) continue;
    const b = byId.get(id);
    if (!b) continue;
    let weight = 0;
    for (const e of model.edges) {
      const other = e.source === id ? e.target : e.target === id ? e.source : null;
      if (other && litHops.has(other) && (litHops.get(other)! < hop || other === focusId)) {
        weight += e.count;
      }
    }
    all.push({ id, layer: b.layer, label: b.label, weight, sev: b.sev });
  }
  all.sort((a, b) => b.weight - a.weight);
  // 只保留关系最强的前 N 组点亮（聚焦，非相关降暗）；litHops 同步收窄，供全景 lit/dim 与光路。
  const affected = all.slice(0, LIT_BLOCK_CAP);
  const keep = new Set<string>([focusId, ...affected.map((a) => a.id)]);
  const cappedHops = new Map<string, number>();
  for (const [id, hop] of litHops) if (keep.has(id)) cappedHops.set(id, hop);
  return { litHops: cappedHops, affected };
}
