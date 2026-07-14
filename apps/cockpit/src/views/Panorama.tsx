import { useEffect, useMemo, useState } from "react";
import { fetchPanorama, type ObjectRef, type Panorama as PanoData, type Role } from "../api";
import {
  buildDisplay,
  corridorFrom,
  ISO_HH,
  ISO_HW,
  LAYER_CN,
  LAYER_ORDER,
  LIT_TRUNK_CAP,
  planeCorners,
  planeTopY,
  projectFloor,
  SLAB,
  VB_H,
  VB_W,
  type DisplayBlock,
  type DisplayModel,
  type PanoSelection,
} from "./panoramaModel";

// 小全景（V9 视觉升级）：等距 2.5D 分层浮板 + 节点聚合组块 + 聚合级影响走廊。
// V9-A：五层半透明等距菱形浮板沿纵深堆叠（客户最上→仓库最下），实体点阵聚合为组块
//   （客户按州 / 订单按州簇 / 在途按航线 / 供应商按城市 / 仓库实体），层间只画聚合主干线。
// V9 追加修正：点击组块只做聚合级高亮（受影响组块整块点亮 + 计数徽标 + ≤5 粗光路，其余降暗），
//   绝不画实体级线网；结构化传播链走右侧「影响分析」面板（App 编排）。
// 智能感之二/之三：异常辉光脉动 + 影响走廊，集中于此；其余零动效数字说话。

const LERP = (a: number, b: number, t: number) => a + (b - a) * t;
const FH_RATIO = ISO_HH / ISO_HW; // 组块底面菱形高宽比（与浮板同等距角）

interface Placed {
  block: DisplayBlock;
  sx: number; // 底面中心
  sy: number;
  fw: number; // 底面半宽
  fh: number; // 底面半高
  h: number; // 立高
  topX: number; // 顶面中心（连线锚点）
  topY: number;
  depth: number; // u+v，绘制前后序
}

function layoutLayer(blocks: DisplayBlock[], i: number): Placed[] {
  const n = blocks.length;
  if (n === 0) return [];
  const cols = Math.min(6, Math.max(1, Math.ceil(Math.sqrt(n * 1.6))));
  const rows = Math.ceil(n / cols);
  const maxMetric = Math.max(...blocks.map((b) => b.metric), 1);
  const topY = planeTopY(i);
  const ordered = [...blocks].sort((a, b) => a.id.localeCompare(b.id));
  return ordered.map((block, k) => {
    const col = k % cols;
    const row = Math.floor(k / cols);
    const u = cols === 1 ? 0.5 : LERP(0.16, 0.84, col / (cols - 1));
    const v = rows === 1 ? 0.5 : LERP(0.3, 0.7, row / (rows - 1));
    const [sx, sy] = projectFloor(topY, u, v);
    const norm = Math.sqrt(block.metric / maxMetric); // 0..1
    const fw = 13 + 15 * norm;
    const h = 12 + 22 * (block.metric / maxMetric);
    return { block, sx, sy, fw, fh: fw * FH_RATIO, h, topX: sx, topY: sy - h, depth: u + v };
  });
}

// 等距组块（三面棱柱：顶亮 / 右中 / 左暗，微渐变靠 CSS 类）。
function IsoBlock({
  p,
  state,
  litWeight,
  onClick,
  onDouble,
  onHover,
  hovered,
}: {
  p: Placed;
  state: "focus" | "lit" | "dim" | "normal";
  litWeight: number;
  onClick: () => void;
  onDouble: () => void;
  onHover: (id: string | null) => void;
  hovered: boolean;
}) {
  const { sx, sy, fw, fh, h, block } = p;
  const lift = hovered ? 4 : 0; // hover 微抬升（affordance，reduced-motion 关）
  const y0 = sy - lift; // 底面
  const yt = sy - h - lift; // 顶面
  const fRight = `${sx + fw},${y0}`;
  const fBot = `${sx},${y0 + fh}`;
  const fLeft = `${sx - fw},${y0}`;
  const tTop = `${sx},${yt - fh}`;
  const tRight = `${sx + fw},${yt}`;
  const tBot = `${sx},${yt + fh}`;
  const tLeft = `${sx - fw},${yt}`;
  const glow = block.alertCount > 0 ? (block.sev >= 2 ? "url(#glow-red)" : "url(#glow-amber)") : undefined;
  const cls = `iso-blk iso-blk--${block.tone} is-${state}`;
  return (
    <g
      className={cls}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      onDoubleClick={(e) => {
        e.stopPropagation();
        onDouble();
      }}
      onMouseEnter={() => onHover(block.id)}
      onMouseLeave={() => onHover(null)}
      style={{ cursor: "pointer" }}
    >
      {/* 立面 */}
      <polygon className="iso-blk__left" points={`${fLeft} ${fBot} ${tBot} ${tLeft}`} />
      <polygon className="iso-blk__right" points={`${fBot} ${fRight} ${tRight} ${tBot}`} />
      {/* 顶面（辉光挂此） */}
      <polygon
        className="iso-blk__top"
        points={`${tTop} ${tRight} ${tBot} ${tLeft}`}
        filter={glow}
      />
      {block.alertCount > 0 && (
        <circle
          className={`iso-halo ${block.sev >= 2 ? "" : "iso-halo--amber"}`}
          cx={sx}
          cy={yt}
          r={fw + 3}
        />
      )}
      {state === "lit" && litWeight > 0 && (
        <g className="iso-affbadge" transform={`translate(${sx + fw - 2},${yt - fh - 6})`}>
          <rect x={-2} y={-9} width={String(litWeight).length * 6.5 + 14} height={15} rx={7.5} />
          <text x={5} y={2}>{`×${litWeight}`}</text>
        </g>
      )}
      <title>
        {`${LAYER_CN[block.layer]} · ${block.label}\n${block.sub}` +
          (block.alertCount > 0 ? `\n${block.alertCount} 未闭环风险` : "") +
          (block.members.length ? `\n${block.members.length} 实体成员（双击展开明细）` : block.ref ? "\n双击开对象卡" : "")}
      </title>
    </g>
  );
}

interface Props {
  role: Role;
  selectedId: string | null;
  onSelect: (sel: PanoSelection | null) => void;
  onOpenObject: (r: ObjectRef) => void;
}

export default function Panorama({ role, selectedId, onSelect, onOpenObject }: Props) {
  const [data, setData] = useState<PanoData | null>(null);
  const [err, setErr] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    fetchPanorama(role)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role]);

  const model: DisplayModel | null = useMemo(() => (data ? buildDisplay(data) : null), [data]);

  const layout = useMemo(() => {
    if (!model) return null;
    const placed = new Map<string, Placed>();
    LAYER_ORDER.forEach((layer, i) => {
      for (const p of layoutLayer(model.blocksByLayer[layer], i)) placed.set(p.block.id, p);
    });
    return placed;
  }, [model]);

  const corridor = useMemo(() => {
    if (!model || !selectedId) return null;
    return corridorFrom(model, selectedId);
  }, [model, selectedId]);

  if (err)
    return (
      <div className="cp-fill-msg">
        全景数据加载失败——确认 API 已启动（uvicorn 命令见 apps/cockpit/README.md）
      </div>
    );
  if (!data || !model || !layout) return <div className="cp-fill-msg">全景加载中…</div>;

  const litHops = corridor?.litHops ?? null;

  function selectBlock(b: DisplayBlock) {
    if (!model) return;
    if (selectedId === b.id) {
      onSelect(null);
      return;
    }
    onSelect({ block: b, affected: corridorFrom(model, b.id).affected });
  }

  function doubleBlock(b: DisplayBlock) {
    // 单实体块（仓库/单成员组）→ 直开对象卡；多成员聚合块 → 选中，明细走影响面板成员表。
    if (b.ref) onOpenObject(b.ref);
    else selectBlock(b);
  }

  // —— 边：底层 faint（cap 后）+ 走廊粗光路（≤5，按关系数量取强）——
  const baseEdgeEls = model.baseEdges.map((e, i) => {
    const s = layout.get(e.source);
    const t = layout.get(e.target);
    if (!s || !t) return null;
    return (
      <line
        key={`b${i}`}
        className="pano-trunk"
        x1={s.topX}
        y1={s.topY}
        x2={t.topX}
        y2={t.topY}
        strokeWidth={0.6 + Math.min(2.4, Math.log2(e.count + 1) * 0.5)}
      />
    );
  });

  let litEdgeEls: React.ReactNode[] = [];
  if (litHops) {
    const inCorridor = model.edges.filter((e) => litHops.has(e.source) && litHops.has(e.target));
    inCorridor.sort((a, b) => b.count - a.count);
    litEdgeEls = inCorridor.slice(0, LIT_TRUNK_CAP).map((e, i) => {
      const s = layout.get(e.source)!;
      const t = layout.get(e.target)!;
      const hop = Math.max(litHops.get(e.source)!, litHops.get(e.target)!);
      return (
        <line
          key={`l${i}`}
          className="pano-trunk is-lit"
          x1={s.topX}
          y1={s.topY}
          x2={t.topX}
          y2={t.topY}
          strokeWidth={1.6 + Math.min(4, Math.log2(e.count + 1) * 0.7)}
          style={{ animationDelay: `${(hop - 1) * 0.18}s` }}
        />
      );
    });
  }

  // 全部 placed，按层序 + depth 排序（后画者在前/上）
  const allPlaced = [...layout.values()].sort((a, b) => {
    const li = LAYER_ORDER.indexOf(a.block.layer) - LAYER_ORDER.indexOf(b.block.layer);
    return li !== 0 ? li : a.depth - b.depth;
  });

  const openRisks = data.meta.open_risks_total;
  const unanchored = data.meta.alerts_unanchored_total;

  return (
    <>
      <div className="cp-panel-head">
        <span className="cp-panel-head__title">小全景 · 等距分层图</span>
        <span className="cp-panel-head__meta">
          {model.blocks.length} 组块 · {model.edges.length} 聚合关系 · {openRisks} open 风险
        </span>
        <span className="cp-panel-head__spacer" />
        {selectedId && (
          <button className="cp-zone__back" onClick={() => onSelect(null)}>
            清除高亮
          </button>
        )}
      </div>
      <div className="cp-pano">
        <svg
          className="cp-pano__svg"
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          preserveAspectRatio="xMidYMid meet"
          onClick={() => selectedId && onSelect(null)}
        >
          <defs>
            <filter id="glow-red" x="-60%" y="-60%" width="220%" height="220%">
              <feGaussianBlur stdDeviation="4.5" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
            <filter id="glow-amber" x="-60%" y="-60%" width="220%" height="220%">
              <feGaussianBlur stdDeviation="3.5" result="b" />
              <feMerge>
                <feMergeNode in="b" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {/* 五层等距浮板（背景层，客户上→仓库下） */}
          {LAYER_ORDER.map((layer, i) => {
            const topY = planeTopY(i);
            const c = planeCorners(topY);
            const P = (pt: [number, number]) => `${pt[0]},${pt[1]}`;
            const topFace = `${P(c.top)} ${P(c.right)} ${P(c.bottom)} ${P(c.left)}`;
            const slab = `${P(c.left)} ${P(c.bottom)} ${P(c.right)} ${P([c.right[0], c.right[1] + SLAB])} ${P([c.bottom[0], c.bottom[1] + SLAB])} ${P([c.left[0], c.left[1] + SLAB])}`;
            const cnt = data.layers[layer].nodes.length;
            const gran = data.layers[layer].granularity;
            return (
              <g key={layer} className={`pano-plane pano-plane--${layer}`}>
                <polygon className="pano-plane__slab" points={slab} />
                <polygon className="pano-plane__top" points={topFace} />
                <line
                  className="pano-plane__edge"
                  x1={c.left[0]}
                  y1={c.left[1]}
                  x2={c.top[0]}
                  y2={c.top[1]}
                />
                <line
                  className="pano-plane__edge"
                  x1={c.left[0]}
                  y1={c.left[1]}
                  x2={c.bottom[0]}
                  y2={c.bottom[1]}
                />
                <text className="pano-plane__label" x={c.left[0] + 12} y={c.left[1] - 4}>
                  {LAYER_CN[layer]}
                </text>
                <text className="pano-plane__count" x={c.left[0] + 12} y={c.left[1] + 12}>
                  {cnt}
                  {gran === "group" ? " 组" : ""}
                </text>
              </g>
            );
          })}

          {/* 聚合主干线（faint） */}
          <g className="pano-trunks">{baseEdgeEls}</g>
          {/* 影响走廊粗光路（键随 focus 变以重放） */}
          <g key={selectedId ?? "none"}>{litEdgeEls}</g>

          {/* 组块 */}
          {allPlaced.map((p) => {
            const id = p.block.id;
            let state: "focus" | "lit" | "dim" | "normal" = "normal";
            let weight = 0;
            if (litHops) {
              if (id === selectedId) state = "focus";
              else if (litHops.has(id)) {
                state = "lit";
                // 徽标 = 入走廊关系数（聚合投影）
                for (const e of model.edges) {
                  const other = e.source === id ? e.target : e.target === id ? e.source : null;
                  if (other && litHops.has(other)) weight += e.count;
                }
              } else state = "dim";
            }
            return (
              <IsoBlock
                key={id}
                p={p}
                state={state}
                litWeight={weight}
                hovered={hovered === id}
                onHover={setHovered}
                onClick={() => selectBlock(p.block)}
                onDouble={() => doubleBlock(p.block)}
              />
            );
          })}

          {/* 组块标签（组名 + 计数；异常/焦点/走廊/悬停附 sub） */}
          {allPlaced.map((p) => {
            const id = p.block.id;
            const showSub =
              hovered === id ||
              id === selectedId ||
              (litHops?.has(id) ?? false) ||
              p.block.alertCount > 0;
            const label = p.block.label.length > 15 ? p.block.label.slice(0, 14) + "…" : p.block.label;
            const dim = litHops && !litHops.has(id) && id !== selectedId;
            return (
              <g key={`t${id}`} className={`pano-blk-label ${dim ? "is-dim" : ""}`} pointerEvents="none">
                <text className="pano-blk-label__name" x={p.topX} y={p.topY - p.fh - (p.block.alertCount > 0 ? 12 : 7)} textAnchor="middle">
                  {label}
                </text>
                {showSub && (
                  <text className="pano-blk-label__sub" x={p.topX} y={p.topY - p.fh + (p.block.alertCount > 0 ? 1 : 5)} textAnchor="middle">
                    {p.block.sub}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        <div className="cp-pano__legend">
          <span><i className="lg lg--red" />高危</span>
          <span><i className="lg lg--amber" />关注/延误</span>
          <span><i className="lg lg--busy" />在途</span>
          <span><i className="lg lg--ok" />正常</span>
        </div>

        <div className="cp-pano__hint">
          {selectedId ? (
            <>
              已选 <b>{layout.get(selectedId)?.block.label ?? selectedId}</b>
              {corridor && corridor.affected.length > 0 && <> · 影响走廊点亮 {corridor.affected.length} 组</>}
              <span className="cp-pano__clear" onClick={() => onSelect(null)}>清除</span>
            </>
          ) : (
            <>
              点<b>组块</b>看影响走廊（聚合级），异常组块<b>辉光脉动</b>。
              {unanchored > 0 && <> · <b>{unanchored}</b> 起未锚定风险见右栏</>}
            </>
          )}
        </div>
      </div>
    </>
  );
}
