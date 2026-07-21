// 等距分层「本体地图」模型（v3 板块⑤ · 自驾驶舱 archive 迁入并适配）。
// 原素材（apps/cockpit/src/archive/panoramaModel.ts）分层的是"业务实例"（客户/订单/在途…）；
// 本模块按 V10 决议适配为"对象类型分层"——35 个本体对象类型按 7 业务域分层浮板，56 条关系为聚合边。
// 等距 2.5D 投影数学（projectFloor / planeCorners / planeTopY）沿用原素材；数据源改为 weave 快照。
// 纯函数、无副作用、无外部依赖，便于复用与推理。
import type { WeaveData } from "../types";

export type Tone = "cyan" | "amber" | "violet" | "green" | "red";

// 7 域配色（沿设计系统令牌，按域区分层）
export const DOMAIN_TONE: Record<string, Tone> = {
  core: "cyan", delay: "amber", cost: "green", admission: "violet",
  procurement: "amber", warehouse: "cyan", coordination: "violet",
};

export interface MapBlock {
  type: string;
  plainName: string;
  domain: string;
  domainName: string;
  covered: boolean;
  count: number;       // 活世界实例数（-1=未覆盖）
  fieldCount: number;
  degree: number;      // 关系度数（驱动组块大小）
  tone: Tone;
}
export interface MapLayer { id: string; name: string; blocks: MapBlock[]; }
export interface MapEdge { source: string; target: string; }
export interface MapModel {
  layers: MapLayer[];
  edges: MapEdge[];
  adj: Map<string, string[]>;
  blockByType: Map<string, MapBlock>;
}

/** 从 weave 快照构建"对象类型分层"显示模型：域=层，类型=组块，关系=聚合边。 */
export function buildMapModel(weave: WeaveData): MapModel {
  const blockByType = new Map<string, MapBlock>();
  const layers: MapLayer[] = weave.domains.map((d) => {
    const blocks: MapBlock[] = d.types.map((ty) => {
      const wt = weave.types[ty.type];
      const degree = wt ? wt.relationships.length : 0;
      const b: MapBlock = {
        type: ty.type,
        plainName: ty.plainName,
        domain: d.id,
        domainName: d.name,
        covered: ty.covered,
        count: ty.count,
        fieldCount: wt ? wt.fieldCount : 0,
        degree,
        tone: DOMAIN_TONE[d.id] ?? "cyan",
      };
      blockByType.set(ty.type, b);
      return b;
    });
    return { id: d.id, name: d.name, blocks };
  });

  // 边：从各类型的 out 关系聚合成类型级无向边（去重、丢自环）
  const seen = new Set<string>();
  const edges: MapEdge[] = [];
  for (const t of Object.values(weave.types)) {
    for (const r of t.relationships) {
      if (r.dir !== "out") continue;
      const s = t.type, tg = r.other;
      if (s === tg) continue;
      if (!blockByType.has(tg)) continue;
      const key = s < tg ? `${s}|${tg}` : `${tg}|${s}`;
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ source: s, target: tg });
    }
  }

  const adj = new Map<string, string[]>();
  for (const e of edges) {
    (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
    (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
  }
  return { layers, edges, adj, blockByType };
}

// ═══════════════ 等距投影（2.5D 分层，沿用 archive 素材）═══════════════
export const VB_W = 1180;
export const ISO_HW = 468;   // 面板半宽
export const ISO_HH = 92;    // 面板半高（浅等距角）
export const SLAB = 8;       // 浮板侧厚
export const TOP_MARGIN = 56;
export const LAYER_STEP = 116; // 7 层的层间步距
export const CX = VB_W / 2;

export function planeTopY(i: number): number {
  return TOP_MARGIN + i * LAYER_STEP;
}
export function vbHeight(nLayers: number): number {
  return planeTopY(nLayers - 1) + 2 * ISO_HH + 90;
}
/** 面板内世界坐标 (u,v)∈[0,1]² → 屏幕坐标。 */
export function projectFloor(topY: number, u: number, v: number): [number, number] {
  return [CX + (u - v) * ISO_HW, topY + (u + v) * ISO_HH];
}
export function planeCorners(topY: number): {
  top: [number, number]; right: [number, number]; bottom: [number, number]; left: [number, number];
} {
  return {
    top: [CX, topY],
    right: [CX + ISO_HW, topY + ISO_HH],
    bottom: [CX, topY + 2 * ISO_HH],
    left: [CX - ISO_HW, topY + ISO_HH],
  };
}

// ═══════════════ 关系走廊（聚合级 BFS，沿用 archive 素材）═══════════════
export function corridorFrom(
  model: MapModel, focusType: string, depth = 2,
): Map<string, number> {
  const litHops = new Map<string, number>([[focusType, 0]]);
  const queue: string[] = [focusType];
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
  return litHops;
}
