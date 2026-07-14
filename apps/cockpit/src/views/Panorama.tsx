import { useEffect, useMemo, useState } from "react";
import {
  fetchPanorama,
  nodeToObjectRef,
  type ObjectRef,
  type Panorama as PanoData,
  type PanoLayerName,
  type PanoNode,
  type Role,
} from "../api";

// 小全景：客户—订单—在途—供应商—仓库五层手绘 SVG 活图（零图形库依赖，纯 SVG/CSS）。
// 节点大小按聚合量、颜色按状态；异常上浮 = open 风险节点红/黄脉动（智能感之二）；
// 点节点 = 沿 edges 的受影响路径逐段点亮（影响走廊/连锁涟漪，智能感之三）+ 可打开对象卡。
// 全部数据来自 GET /cockpit/panorama，零硬编码。

const LAYER_ORDER: PanoLayerName[] = ["customers", "orders", "shipments", "suppliers", "warehouses"];
const LAYER_CN: Record<PanoLayerName, string> = {
  customers: "客户",
  orders: "订单",
  shipments: "在途",
  suppliers: "供应商",
  warehouses: "仓库",
};
const VB_W = 1120;
const VB_H = 640;
const X0 = 112;
const X1 = 1096;
const BAND_Y: Record<PanoLayerName, number> = {
  customers: 72,
  orders: 196,
  shipments: 322,
  suppliers: 462,
  warehouses: 584,
};
const SEV_RANK: Record<string, number> = { critical: 3, high: 2, medium: 1, low: 0 };

interface Placed {
  node: PanoNode;
  x: number;
  y: number;
  r: number;
  fill: string; // pano-node modifier
  sev: number; // -1 无告警，否则最高严重度
}

function nodeRadius(n: PanoNode): number {
  switch (n.layer) {
    case "orders":
      return 4 + Math.min(6, Math.sqrt(n.line_count ?? 0) / 3);
    case "shipments":
      return 4.5 + Math.min(4.5, n.container_count ?? 0);
    case "warehouses":
      return 9;
    default:
      return 5;
  }
}

function nodeFill(n: PanoNode, sev: number): string {
  if (sev >= 2) return "pano-node--red";
  if (sev >= 0) return "pano-node--amber";
  // 无 open 风险时的状态色（真实字段驱动，非装饰）
  if (n.layer === "shipments") {
    if ((n.delayed_count ?? (n.delay_days ?? 0) > 0 ? 1 : 0) > 0) return "pano-node--amber";
    return "pano-node--busy";
  }
  if (n.layer === "warehouses" && (n.safety_breach_count ?? 0) > 0) return "pano-node--amber";
  if (n.layer === "orders" && (n.at_risk_lines ?? 0) > 0) return "pano-node--amber";
  return "pano-node--ok";
}

function maxSeverity(n: PanoNode): number {
  if (!n.alerts || n.alerts.length === 0) return -1;
  return Math.max(...n.alerts.map((a) => SEV_RANK[a.severity] ?? 1));
}

export default function Panorama({ role, onOpenObject }: { role: Role; onOpenObject: (r: ObjectRef) => void }) {
  const [data, setData] = useState<PanoData | null>(null);
  const [err, setErr] = useState(false);
  const [focus, setFocus] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    setFocus(null);
    fetchPanorama(role)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  // —— 布局：节点定位 + 颜色 + 边端点坐标（useMemo，仅数据变时重算）——
  const layout = useMemo(() => {
    if (!data) return null;
    const placed = new Map<string, Placed>();
    for (const layer of LAYER_ORDER) {
      const nodes = data.layers[layer]?.nodes ?? [];
      const n = nodes.length;
      const span = X1 - X0;
      nodes.forEach((node, i) => {
        const x = n === 1 ? (X0 + X1) / 2 : X0 + (span * (i + 0.5)) / n;
        const sev = maxSeverity(node);
        placed.set(node.id, { node, x, y: BAND_Y[layer], r: nodeRadius(node), fill: nodeFill(node, sev), sev });
      });
    }
    // 无向邻接表（供影响走廊 BFS）
    const adj = new Map<string, string[]>();
    for (const e of data.edges) {
      if (!placed.has(e.source) || !placed.has(e.target)) continue;
      (adj.get(e.source) ?? adj.set(e.source, []).get(e.source)!).push(e.target);
      (adj.get(e.target) ?? adj.set(e.target, []).get(e.target)!).push(e.source);
    }
    return { placed, adj };
  }, [data]);

  // —— 影响走廊：从 focus 节点 BFS 深度 2（一个延误沿关系图漾开到订单行/客户/兄弟货件）——
  const lit = useMemo(() => {
    if (!layout || !focus) return null;
    const depth = new Map<string, number>([[focus, 0]]);
    const queue: string[] = [focus];
    while (queue.length) {
      const u = queue.shift()!;
      const du = depth.get(u)!;
      if (du >= 2) continue;
      for (const v of layout.adj.get(u) ?? []) {
        if (!depth.has(v)) {
          depth.set(v, du + 1);
          queue.push(v);
        }
      }
    }
    return depth; // nodeId -> hop
  }, [layout, focus]);

  if (err)
    return (
      <div className="cp-fill-msg">
        全景数据加载失败——确认 API 已启动（uvicorn 命令见 apps/cockpit/README.md）
      </div>
    );
  if (!data || !layout) return <div className="cp-fill-msg">全景加载中…</div>;

  const focusRef = focus ? nodeToObjectRef(focus) : null;
  const focusPlaced = focus ? layout.placed.get(focus) ?? null : null;

  // 边：基础层（全部，faint）+ 点亮层（focus 时，键随 focus 变 → 动画重放）
  const baseEdges = data.edges.map((e, i) => {
    const s = layout.placed.get(e.source);
    const t = layout.placed.get(e.target);
    if (!s || !t) return null;
    return <line key={i} className="pano-edge" x1={s.x} y1={s.y} x2={t.x} y2={t.y} />;
  });

  const litEdges: React.ReactNode[] = [];
  if (lit) {
    data.edges.forEach((e, i) => {
      if (!lit.has(e.source) || !lit.has(e.target)) return;
      const s = layout.placed.get(e.source)!;
      const t = layout.placed.get(e.target)!;
      const len = Math.hypot(t.x - s.x, t.y - s.y);
      const hop = Math.max(lit.get(e.source)!, lit.get(e.target)!);
      const st = { ["--len"]: len, animationDelay: `${(hop - 1) * 0.22}s` } as React.CSSProperties;
      litEdges.push(
        <line key={`l${i}`} className="pano-edge is-lit" x1={s.x} y1={s.y} x2={t.x} y2={t.y} style={st} />,
      );
    });
  }

  return (
    <>
      <div className="cp-panel-head">
        <span className="cp-panel-head__title">小全景 · 五层活图</span>
        <span className="cp-panel-head__meta">
          {data.meta.open_risks_total} open 风险 · {data.meta.edge_count} 关系
          {data.meta.aggregated_layers.length > 0 && ` · 聚合层：${data.meta.aggregated_layers.join("/")}`}
        </span>
      </div>
      <div className="cp-pano">
        <svg className="cp-pano__svg" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet">
          {/* 层带 + 层标签 */}
          {LAYER_ORDER.map((layer) => (
            <g key={layer}>
              <rect className="pano-layer-band" x={0} y={BAND_Y[layer] - 30} width={VB_W} height={60} rx={0} />
              <text className="pano-layer-label" x={16} y={BAND_Y[layer] - 4}>
                {LAYER_CN[layer]}
              </text>
              <text className="pano-layer-count" x={16} y={BAND_Y[layer] + 12}>
                {data.layers[layer].nodes.length}
                {data.layers[layer].granularity === "group" ? " 组" : ""}
              </text>
            </g>
          ))}

          {/* 基础边（faint 布线纹理） */}
          <g>{baseEdges}</g>
          {/* 点亮走廊（逐段绘制，键随 focus 变以重放动画） */}
          <g key={focus ?? "none"}>{litEdges}</g>

          {/* 节点 + 告警脉动光晕 */}
          {[...layout.placed.values()].map((p) => {
            const dim = lit ? !lit.has(p.node.id) : false;
            const isFocus = p.node.id === focus;
            const ref = nodeToObjectRef(p.node.id);
            const alertN = p.node.alert_count;
            const title =
              `${p.node.label}` +
              (p.node.lane ? ` · ${p.node.lane}` : "") +
              (p.node.status ? ` · ${p.node.status}` : "") +
              (typeof p.node.delay_days === "number" && p.node.delay_days > 0 ? ` · 延误${p.node.delay_days}天` : "") +
              (p.node.so_count ? ` · 订单${p.node.so_count}` : "") +
              (p.node.safety_breach_count ? ` · 击穿${p.node.safety_breach_count}` : "") +
              (alertN > 0 ? ` · ${alertN} 告警(${p.node.alerts.map((a) => a.rule_id).join(",")})` : "") +
              (ref ? "  ▸ 点击看走廊 / 双击开对象卡" : "  （聚合节点，无单一对象）");
            return (
              <g key={p.node.id}>
                {alertN > 0 && (
                  <circle
                    className={`pano-halo ${p.sev >= 2 ? "" : "pano-halo--amber"}`}
                    cx={p.x}
                    cy={p.y}
                    r={p.r}
                  />
                )}
                <circle
                  className={`pano-node ${p.fill} ${dim ? "is-dim" : ""} ${isFocus ? "is-focus" : ""}`}
                  cx={p.x}
                  cy={p.y}
                  r={p.r + (alertN > 0 ? 1.5 : 0)}
                  onClick={() => setFocus(p.node.id)}
                  onDoubleClick={() => ref && onOpenObject(ref)}
                >
                  <title>{title}</title>
                </circle>
              </g>
            );
          })}

          {/* 仓库 + 告警节点标签（其余不标，防拥挤） */}
          {[...layout.placed.values()]
            .filter((p) => p.node.layer === "warehouses" || p.node.alert_count > 0 || p.node.id === focus)
            .map((p) => (
              <text
                key={`t${p.node.id}`}
                className="pano-node-label"
                x={p.x}
                y={p.y - p.r - 4}
                textAnchor="middle"
              >
                {p.node.label.length > 16 ? p.node.label.slice(0, 15) + "…" : p.node.label}
              </text>
            ))}

          {/* focus 涟漪圈 */}
          {focusPlaced && (
            <circle
              key={`rip${focus}`}
              className="pano-ripple"
              cx={focusPlaced.x}
              cy={focusPlaced.y}
              r={focusPlaced.r + 6}
              strokeDasharray={260}
            />
          )}
        </svg>

        <div className="cp-pano__legend">
          <span>
            <i style={{ background: "var(--sev-red)" }} />高危
          </span>
          <span>
            <i style={{ background: "var(--sev-amber)" }} />关注/延误
          </span>
          <span>
            <i style={{ background: "var(--beacon)" }} />在途
          </span>
          <span>
            <i style={{ background: "#3a5573" }} />正常
          </span>
        </div>

        {focus ? (
          <div className="cp-pano__hint">
            已选 <b>{focusPlaced?.node.label ?? focus}</b>
            {lit && <> · 影响走廊 {lit.size} 节点点亮</>}
            {focusRef ? (
              <span className="cp-pano__clear" onClick={() => onOpenObject(focusRef)}>
                打开对象卡 →
              </span>
            ) : (
              <span style={{ color: "var(--ink-3)" }}> · 聚合节点无对象卡</span>
            )}
            <span className="cp-pano__clear" onClick={() => setFocus(null)}>
              清除
            </span>
          </div>
        ) : (
          <div className="cp-pano__hint">
            点节点看<b>影响走廊</b>，双击开<b>对象卡</b>。挂红黄标的异常永远比安静的亮、大、脉动。
          </div>
        )}
      </div>
    </>
  );
}
