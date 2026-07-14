// 航线走廊图的纯数据层（无 React、无副作用）——V10 方案 C 的履约卡切换视图。
// 把 panorama 的 shipments 层归一为"航线（lane）"：无论 API 给的是实体粒度（≤40 票，逐票带
// .lane 字段）还是分组粒度（>40 票，已按 lane 聚合），都产出统一的 Lane[]。
//  · 粗细 = 该航线在途票数 count（两粒度都稳有；柜数 container_count 附注，勿编货值——V10 红线）
//  · 颜色 = 异常状态：含 critical=红 / 有 alert=琥珀 / 无=蓝（beacon）
//  · 点弧线 → 该航线异常队列（alerts / 逐票），下钻路径与区队列一致
// origin/dest 由 lane 串 "CNNGB→USLAX" 拆得（真数据）；港口中文名仅为已知 UN/LOCODE 的展示译名
// （稳定事实，非业务数据），未知码原样显示 LOCODE，不编造。
import { nodeToObjectRef, type ObjectRef, type Panorama as PanoData, type PanoAlert, type PanoNode } from "../api";

export interface LaneAlert extends PanoAlert {
  shipment_id?: string;
}
export interface LaneShipment {
  id: string;
  ref: ObjectRef | null;
  status?: string | null;
  delay?: number | null;
  containers?: number;
  alertCount: number;
  sev: number; // -1 无告警
}
export type LaneTone = "red" | "amber" | "blue";
export interface Lane {
  key: string;
  origin: string;
  dest: string;
  count: number; // 在途票数（粗细驱动）
  containers: number; // 柜数（附注）
  delayed: number;
  alertCount: number;
  hasCritical: boolean;
  tone: LaneTone;
  alerts: LaneAlert[];
  shipments: LaneShipment[]; // 实体粒度世界才有；分组粒度为空（用 alerts）
  statuses: Record<string, number>;
}
export interface CorridorModel {
  lanes: Lane[];
  origins: string[]; // 左列（起运港，一般中国）
  dests: string[]; // 右列（目的港/仓）
  maxCount: number;
  entityWorld: boolean;
  totalShipments: number;
  totalAlerts: number;
}

const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };
export const laneSevRank = (s: string): number => SEV_RANK[s] ?? 1;

function toneOf(hasCritical: boolean, alertCount: number): LaneTone {
  if (hasCritical) return "red";
  if (alertCount > 0) return "amber";
  return "blue";
}

function splitLane(label: string): { origin: string; dest: string } {
  const i = label.indexOf("→");
  if (i < 0) return { origin: label || "?", dest: "?" };
  return { origin: label.slice(0, i) || "?", dest: label.slice(i + 1) || "?" };
}

export function buildCorridor(data: PanoData): CorridorModel {
  const layer = data.layers.shipments;
  const entityWorld = layer.granularity === "entity";
  const laneMap = new Map<string, Lane>();

  const ensure = (label: string): Lane => {
    let L = laneMap.get(label);
    if (!L) {
      const { origin, dest } = splitLane(label);
      L = { key: label, origin, dest, count: 0, containers: 0, delayed: 0, alertCount: 0, hasCritical: false, tone: "blue", alerts: [], shipments: [], statuses: {} };
      laneMap.set(label, L);
    }
    return L;
  };

  if (entityWorld) {
    for (const n of layer.nodes as PanoNode[]) {
      const L = ensure(n.lane || "?→?");
      L.count += 1;
      L.containers += n.container_count ?? 0;
      if ((n.delay_days ?? 0) > 0) L.delayed += 1;
      L.alertCount += n.alert_count ?? 0;
      if (n.status) L.statuses[n.status] = (L.statuses[n.status] ?? 0) + 1;
      const shpId = n.id.replace(/^shipment:/, "");
      let maxSev = -1;
      for (const a of n.alerts ?? []) {
        L.alerts.push({ ...a, shipment_id: shpId });
        maxSev = Math.max(maxSev, laneSevRank(a.severity));
        if (a.severity === "critical") L.hasCritical = true;
      }
      L.shipments.push({ id: n.id, ref: nodeToObjectRef(n.id), status: n.status, delay: n.delay_days, containers: n.container_count, alertCount: n.alert_count ?? 0, sev: maxSev });
    }
  } else {
    for (const n of layer.nodes as PanoNode[]) {
      const L = ensure(n.label);
      L.count += n.count ?? 0;
      L.containers += n.container_count ?? 0;
      L.delayed += n.delayed_count ?? 0;
      L.alertCount += n.alert_count ?? 0;
      for (const s in n.statuses ?? {}) L.statuses[s] = (L.statuses[s] ?? 0) + (n.statuses?.[s] ?? 0);
      for (const a of n.alerts ?? []) {
        L.alerts.push({ ...a });
        if (a.severity === "critical") L.hasCritical = true;
      }
    }
  }

  const lanes = [...laneMap.values()];
  for (const L of lanes) {
    L.tone = toneOf(L.hasCritical, L.alertCount);
    // 异常票/风险排前（严重度降序），供弧线队列与悬停摘要
    L.alerts.sort((a, b) => laneSevRank(b.severity) - laneSevRank(a.severity));
    L.shipments.sort((a, b) => b.sev - a.sev || (b.delay ?? 0) - (a.delay ?? 0));
  }
  // 弧线绘制序：告警航线后画（在上层），票数大的后画
  lanes.sort((a, b) => Number(a.alertCount > 0) - Number(b.alertCount > 0) || a.count - b.count);

  const origins = [...new Set(lanes.map((l) => l.origin))].sort();
  const dests = [...new Set(lanes.map((l) => l.dest))].sort();
  return {
    lanes,
    origins,
    dests,
    maxCount: Math.max(1, ...lanes.map((l) => l.count)),
    entityWorld,
    totalShipments: lanes.reduce((s, l) => s + l.count, 0),
    totalAlerts: lanes.reduce((s, l) => s + l.alertCount, 0),
  };
}

// ── 港口展示译名（已知 UN/LOCODE 的稳定标准名；未知原样显示 LOCODE，不编造）──────
export const PORT_CN: Record<string, string> = {
  CNNGB: "宁波",
  CNYTN: "盐田",
  CNSHK: "蛇口",
  CNSHA: "上海",
  CNTAO: "青岛",
  CNXMN: "厦门",
  USLAX: "洛杉矶",
  USLGB: "长滩",
  USNYC: "纽约",
  USSAV: "萨凡纳",
  USSEA: "西雅图",
  USOAK: "奥克兰",
  DEHAM: "汉堡",
  NLRTM: "鹿特丹",
};
export const portName = (locode: string): string => PORT_CN[locode] ?? "";

// ── SVG 二次贝塞尔弧路径（起点右缘 → 终点左缘，控制点按纵向错位分束）──────────────
export function arcPath(x1: number, y1: number, x2: number, y2: number, bend: number): string {
  const mx = (x1 + x2) / 2;
  const my = (y1 + y2) / 2 + bend;
  return `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;
}

/** 票数 → 弧线宽度（对数压缩，2..maxW，避免单条独大压死细线）。 */
export function laneWidth(count: number, maxCount: number, maxW = 11): number {
  const t = Math.log2(count + 1) / Math.log2(maxCount + 1);
  return 2 + t * (maxW - 2);
}
