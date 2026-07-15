import { useState, type CSSProperties } from "react";
import type { StructureData } from "../types";
import { Icon, type IconName } from "./icons";
import { useTip } from "./Tooltip";

/* ---------------- 等距投影（分离式 exploded axonometric） ---------------- */
const VB_W = 960;
const VB_H = 748;
const CX = 480;
const BASE = 486;
const UX = 48; // 半格宽
const UY = 17; // 半格高
const P = 6; // 平面边长（格）
const Z_GOV = 0;
const Z_BIZ = 220;
const Z_INTEL = 440;

function project(x: number, y: number, z: number) {
  return { sx: CX + (x - y) * UX, sy: BASE + (x + y) * UY - z };
}
const pct = (sx: number, sy: number) => ({
  left: `${(sx / VB_W) * 100}%`,
  top: `${(sy / VB_H) * 100}%`,
});

/* 平面四角（菱形）路径 */
function planePath(z: number) {
  const a = project(0, 0, z);
  const b = project(P, 0, z);
  const c = project(P, P, z);
  const d = project(0, P, z);
  return `M${a.sx},${a.sy} L${b.sx},${b.sy} L${c.sx},${c.sy} L${d.sx},${d.sy} Z`;
}
/* 平面网格线（蓝图感） */
function gridLines(z: number) {
  const lines: string[] = [];
  for (let i = 1; i < P; i++) {
    const a = project(i, 0, z);
    const b = project(i, P, z);
    lines.push(`M${a.sx},${a.sy} L${b.sx},${b.sy}`);
    const c = project(0, i, z);
    const d = project(P, i, z);
    lines.push(`M${c.sx},${c.sy} L${d.sx},${d.sy}`);
  }
  return lines;
}

/* 元素在各自平面上的坐标（格）——沿反对角线成行，避免屏幕空间重叠 */
const POS: Record<string, { x: number; y: number; z: number }> = {
  // 智能接入层（一行：x+y=5）
  "ai-colleague": { x: 1, y: 4, z: Z_INTEL },
  automation: { x: 2.5, y: 2.5, z: Z_INTEL },
  evaluator: { x: 4, y: 1, z: Z_INTEL },
  // 业务世界（菱形三列布局：共享核居中，六场景环绕）
  coordination: { x: 0.6, y: 0.6, z: Z_BIZ },
  delay: { x: 3, y: 0.6, z: Z_BIZ },
  admission: { x: 0.6, y: 3, z: Z_BIZ },
  core: { x: 3, y: 3, z: Z_BIZ },
  cost: { x: 5.4, y: 3, z: Z_BIZ },
  procurement: { x: 3, y: 5.4, z: Z_BIZ },
  warehouse: { x: 5.4, y: 5.4, z: Z_BIZ },
  // 治理层（一行：x+y=5）
  rbac: { x: 1, y: 4, z: Z_GOV },
  constitution: { x: 2.5, y: 2.5, z: Z_GOV },
  audit: { x: 4, y: 1, z: Z_GOV },
};

interface Node {
  id: string;
  name: string;
  plain: string;
  icon: IconName;
  layer: "intel" | "biz" | "gov";
  links: string[]; // 连到哪些业务域
  meta?: string[];
  emphasis?: boolean;
}

const LAYER_COLOR: Record<Node["layer"], string> = {
  intel: "var(--layer-intel)",
  biz: "var(--layer-biz)",
  gov: "var(--layer-gov)",
};
const LAYER_TONE: Record<Node["layer"], "violet" | "amber" | "cyan"> = {
  intel: "violet",
  biz: "amber",
  gov: "cyan",
};
const SCENE_ICON: Record<string, IconName> = {
  core: "core",
  delay: "ship",
  cost: "invoice",
  admission: "gate",
  procurement: "dock",
  warehouse: "warehouse",
  coordination: "thread",
};

export default function IsometricScene({ data }: { data: StructureData }) {
  const { show, move, hide } = useTip();
  const [hover, setHover] = useState<string | null>(null);

  // 组装节点
  const nodes: Node[] = [];
  data.layers.intelligence.items.forEach((it) =>
    nodes.push({
      id: it.id,
      name: it.name,
      plain: it.plain,
      icon: it.icon as IconName,
      layer: "intel",
      links: it.connectsTo,
    })
  );
  data.layers.business.items.forEach((it) =>
    nodes.push({
      id: it.id,
      name: it.name,
      plain: it.plain,
      icon: SCENE_ICON[it.id] ?? "core",
      layer: "biz",
      links: [],
      emphasis: it.id === "delay",
      meta: [`${it.objectCount} 对象`, it.recordCount > 0 ? `${it.recordCount.toLocaleString()} 条` : "—"],
    })
  );
  data.layers.governance.items.forEach((it) =>
    nodes.push({
      id: it.id,
      name: it.name,
      plain: it.plain,
      icon: it.icon as IconName,
      layer: "gov",
      links: it.guards,
    })
  );

  // 连线：intel→biz（connectsTo），gov→biz（guards）
  const edges: { from: string; to: string; layer: Node["layer"] }[] = [];
  nodes.forEach((n) => {
    if (n.layer === "intel" || n.layer === "gov") {
      n.links.forEach((biz) => {
        if (POS[biz]) edges.push({ from: n.id, to: biz, layer: n.layer });
      });
    }
  });

  const isActive = (id: string) => {
    if (!hover) return false;
    if (hover === id) return true;
    const hn = nodes.find((n) => n.id === hover);
    if (hn && (hn.layer === "intel" || hn.layer === "gov")) return hn.links.includes(id);
    // hover 一个业务域 → 高亮连它的 intel/gov
    const target = nodes.find((n) => n.id === hover);
    if (target && target.layer === "biz") {
      const src = nodes.find((n) => n.id === id);
      return !!src && (src.layer !== "biz") && src.links.includes(hover);
    }
    return false;
  };

  const edgeActive = (e: { from: string; to: string }) =>
    hover === e.from || hover === e.to;

  return (
    <div className="iso-wrap">
      <div className="iso-stage">
        <svg
          className="iso-svg"
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          preserveAspectRatio="xMidYMid meet"
        >
          <defs>
            <linearGradient id="pl-gov" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="rgba(87,186,217,0.10)" />
              <stop offset="1" stopColor="rgba(87,186,217,0.02)" />
            </linearGradient>
            <linearGradient id="pl-biz" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="rgba(233,172,82,0.10)" />
              <stop offset="1" stopColor="rgba(233,172,82,0.02)" />
            </linearGradient>
            <linearGradient id="pl-intel" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0" stopColor="rgba(146,148,222,0.12)" />
              <stop offset="1" stopColor="rgba(146,148,222,0.03)" />
            </linearGradient>
          </defs>

          {/* 三平面：从下往上画 */}
          {(
            [
              { z: Z_GOV, fill: "url(#pl-gov)", stroke: "var(--layer-gov)", name: "治理层", key: "gov" },
              { z: Z_BIZ, fill: "url(#pl-biz)", stroke: "var(--layer-biz)", name: "业务世界", key: "biz" },
              { z: Z_INTEL, fill: "url(#pl-intel)", stroke: "var(--layer-intel)", name: "智能接入层", key: "intel" },
            ] as const
          ).map((pl) => {
            const label = project(0, P, pl.z); // 左角放层名
            return (
              <g key={pl.key} opacity={hover ? 0.72 : 1} style={{ transition: "opacity .2s" }}>
                <path d={planePath(pl.z)} fill={pl.fill} stroke={pl.stroke} strokeWidth={1.3} strokeLinejoin="round" opacity={0.9} />
                {gridLines(pl.z).map((d, i) => (
                  <path key={i} d={d} stroke={pl.stroke} strokeWidth={0.5} opacity={0.16} />
                ))}
                <text
                  x={label.sx - 8}
                  y={label.sy + 4}
                  textAnchor="end"
                  className="iso-layer-label"
                  fill={pl.stroke}
                >
                  {pl.name}
                </text>
              </g>
            );
          })}

          {/* 连线 */}
          {edges.map((e, i) => {
            const a = POS[e.from];
            const b = POS[e.to];
            if (!a || !b) return null;
            const pa = project(a.x, a.y, a.z);
            const pb = project(b.x, b.y, b.z);
            const act = edgeActive(e);
            return (
              <line
                key={i}
                x1={pa.sx}
                y1={pa.sy}
                x2={pb.sx}
                y2={pb.sy}
                stroke={e.layer === "intel" ? "var(--layer-intel)" : "var(--layer-gov)"}
                strokeWidth={act ? 1.6 : 0.7}
                opacity={hover ? (act ? 0.85 : 0.06) : 0.18}
                strokeDasharray={e.layer === "gov" ? "3 3" : undefined}
                style={{ transition: "opacity .18s, stroke-width .18s" }}
              />
            );
          })}

          {/* 元素底座（小菱形节点） */}
          {nodes.map((n) => {
            const p = POS[n.id];
            if (!p) return null;
            const c = project(p.x, p.y, p.z);
            const s = n.emphasis ? 15 : 12;
            const active = isActive(n.id) || hover === n.id;
            return (
              <g key={n.id} style={{ transition: "opacity .18s" }} opacity={hover && !active ? 0.4 : 1}>
                {/* 竖直落柱到下一层参考（仅业务/智能） */}
                <path
                  d={`M${c.sx},${c.sy} L${c.sx},${c.sy + 14}`}
                  stroke={LAYER_COLOR[n.layer]}
                  strokeWidth={1}
                  opacity={0.35}
                />
                <path
                  d={`M${c.sx},${c.sy - s * 0.5} L${c.sx + s},${c.sy} L${c.sx},${c.sy + s * 0.5} L${c.sx - s},${c.sy} Z`}
                  fill={active ? LAYER_COLOR[n.layer] : "var(--surface-2)"}
                  stroke={LAYER_COLOR[n.layer]}
                  strokeWidth={1.3}
                  opacity={active ? 0.9 : 0.8}
                />
              </g>
            );
          })}
        </svg>

        {/* HTML 卡片层（清晰中文） */}
        {nodes.map((n) => {
          const p = POS[n.id];
          if (!p) return null;
          const c = project(p.x, p.y, p.z);
          const active = hover === n.id;
          const dim = hover && !active && !isActive(n.id);
          const style: CSSProperties = {
            ...pct(c.sx, c.sy),
            ["--nc" as string]: LAYER_COLOR[n.layer],
          };
          return (
            <button
              key={n.id}
              className={`iso-card ${n.layer}${n.emphasis ? " emph" : ""}${active ? " active" : ""}${dim ? " dim" : ""}`}
              style={style}
              onMouseEnter={(e) => {
                setHover(n.id);
                show({ title: n.name, plain: n.plain, meta: n.meta, tone: LAYER_TONE[n.layer] }, e);
              }}
              onMouseMove={(e) => move(e)}
              onMouseLeave={() => {
                setHover(null);
                hide();
              }}
              onFocus={() => setHover(n.id)}
              onBlur={() => setHover(null)}
            >
              <span className="iso-card-ico" style={{ color: LAYER_COLOR[n.layer] }}>
                <Icon name={n.icon} size={n.emphasis ? 22 : 19} />
              </span>
              <span className="iso-card-name">{n.name}</span>
              {n.meta && <span className="iso-card-meta num">{n.meta.join(" · ")}</span>}
            </button>
          );
        })}
      </div>

      <div className="iso-legend">
        <span className="iso-leg"><i style={{ background: "var(--layer-intel)" }} />智能接入层 · AI 从上方接入</span>
        <span className="iso-leg"><i style={{ background: "var(--layer-biz)" }} />业务世界 · 五场景 + 共享核</span>
        <span className="iso-leg"><i style={{ background: "var(--layer-gov)" }} />治理层 · 在下方兜底</span>
        <span className="iso-leg-hint">悬停任意元素：看白话说明，并高亮它的接入 / 兜底连线</span>
      </div>
    </div>
  );
}
