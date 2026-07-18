import { useMemo, useState } from "react";
import { weave } from "../data";
import { useNav } from "../components/Nav";
import ViewHead from "../components/ViewHead";
import {
  buildMapModel, corridorFrom, planeCorners, planeTopY, projectFloor, vbHeight,
  ISO_HH, ISO_HW, SLAB, VB_W, type MapBlock, type MapModel,
} from "../components/ontologyMapModel";

const LERP = (a: number, b: number, t: number) => a + (b - a) * t;
const FH_RATIO = ISO_HH / ISO_HW;

interface Placed {
  block: MapBlock; sx: number; sy: number; fw: number; fh: number; h: number;
  topX: number; topY: number; depth: number;
}

function layoutLayer(blocks: MapBlock[], i: number): Placed[] {
  const n = blocks.length;
  if (n === 0) return [];
  const cols = Math.min(6, Math.max(1, Math.ceil(Math.sqrt(n * 1.7))));
  const rows = Math.ceil(n / cols);
  const maxDeg = Math.max(...blocks.map((b) => b.degree), 1);
  const topY = planeTopY(i);
  const ordered = [...blocks].sort((a, b) => a.type.localeCompare(b.type));
  return ordered.map((block, k) => {
    const col = k % cols;
    const row = Math.floor(k / cols);
    const u = cols === 1 ? 0.5 : LERP(0.14, 0.86, col / (cols - 1));
    const v = rows === 1 ? 0.5 : LERP(0.28, 0.72, row / (rows - 1));
    const [sx, sy] = projectFloor(topY, u, v);
    const norm = Math.sqrt(block.degree / maxDeg);
    const fw = 12 + 15 * norm;
    const h = 11 + 20 * (block.degree / maxDeg);
    return { block, sx, sy, fw, fh: fw * FH_RATIO, h, topX: sx, topY: sy - h, depth: u + v };
  });
}

function IsoBlock({
  p, state, onClick, onHover, hovered,
}: {
  p: Placed; state: "focus" | "lit" | "dim" | "normal";
  onClick: () => void; onHover: (t: string | null) => void; hovered: boolean;
}) {
  const { sx, sy, fw, fh, h, block } = p;
  const lift = hovered ? 4 : 0;
  const y0 = sy - lift, yt = sy - h - lift;
  const fRight = `${sx + fw},${y0}`, fBot = `${sx},${y0 + fh}`, fLeft = `${sx - fw},${y0}`;
  const tTop = `${sx},${yt - fh}`, tRight = `${sx + fw},${yt}`, tBot = `${sx},${yt + fh}`, tLeft = `${sx - fw},${yt}`;
  const cls = `om-blk om-blk--${block.tone} is-${state}${block.covered ? "" : " is-uncovered"}`;
  const selected = state === "focus";
  // P1 修复：节点点亮逻辑（corridorFrom BFS + onClick→setSelected）本就工作，代码复核确认（未做浏览器验证，
  // 按执行者红线不可做）；缺的是可达性——<g onClick> 无 role/tabIndex/aria-label，无障碍树不可见，键盘无法触达。
  // L-UX 轮2 勘误：SVG 图形上的 tabIndex+onKeyDown 是死路——Chromium 不向聚焦的 SVG 元素派发
  // 键盘事件（主会话实测：activeElement 在 <g> 上、窗口级 capture 监听收到 0 个 keydown）。
  // 键盘等价物改走 HTML 侧「键盘选择列表」（见组件底部，视觉隐藏、聚焦即现）；本 <g> 保留
  // role/aria-pressed 供指针语义与读屏描述，不再承诺 Enter。
  return (
    <g className={cls}
      role="button"
      aria-label={`${block.plainName}（${block.domainName}）${selected ? "，已选中，沿关系走廊点亮邻域" : "，点击点亮邻域"}`}
      aria-pressed={selected}
      onClick={(e) => { e.stopPropagation(); onClick(); }}
      onMouseEnter={() => onHover(block.type)} onMouseLeave={() => onHover(null)}
      style={{ cursor: "pointer" }}>
      <polygon className="om-blk__left" points={`${fLeft} ${fBot} ${tBot} ${tLeft}`} />
      <polygon className="om-blk__right" points={`${fBot} ${fRight} ${tRight} ${tBot}`} />
      <polygon className="om-blk__top" points={`${tTop} ${tRight} ${tBot} ${tLeft}`} />
      <title>{`${block.plainName} · ${block.type}\n${block.domainName}｜${block.fieldCount} 字段 · ${block.degree} 关系` +
        (block.covered ? `\n活世界 ${block.count.toLocaleString()} 实例` : "\n活世界暂未覆盖此域")}</title>
    </g>
  );
}

export default function ViewOntologyMap() {
  const { navigate } = useNav();
  const model: MapModel = useMemo(() => buildMapModel(weave), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);

  const layout = useMemo(() => {
    const placed = new Map<string, Placed>();
    model.layers.forEach((layer, i) => {
      for (const p of layoutLayer(layer.blocks, i)) placed.set(p.block.type, p);
    });
    return placed;
  }, [model]);

  const litHops = useMemo(
    () => (selected ? corridorFrom(model, selected) : null),
    [model, selected]
  );

  const VB_H = vbHeight(model.layers.length);
  const sel = selected ? model.blockByType.get(selected) : null;
  const selWeave = selected ? weave.types[selected] : null;

  // 边：底层 faint + 走廊点亮
  const baseEdgeEls = model.edges.map((e, i) => {
    const s = layout.get(e.source), t = layout.get(e.target);
    if (!s || !t) return null;
    const dim = litHops && !(litHops.has(e.source) && litHops.has(e.target));
    return (
      <line key={`b${i}`} className={`om-edge${dim ? " is-faint" : ""}`}
        x1={s.topX} y1={s.topY} x2={t.topX} y2={t.topY} />
    );
  });
  let litEdgeEls: React.ReactNode[] = [];
  if (litHops) {
    litEdgeEls = model.edges
      .filter((e) => litHops.has(e.source) && litHops.has(e.target))
      .map((e, i) => {
        const s = layout.get(e.source)!, t = layout.get(e.target)!;
        const hop = Math.max(litHops.get(e.source)!, litHops.get(e.target)!);
        return (
          <line key={`l${i}`} className="om-edge is-lit"
            x1={s.topX} y1={s.topY} x2={t.topX} y2={t.topY}
            style={{ animationDelay: `${(hop - 1) * 0.16}s` }} />
        );
      });
  }

  const allPlaced = [...layout.values()].sort((a, b) => {
    const li = model.layers.findIndex((l) => l.id === a.block.domain) -
      model.layers.findIndex((l) => l.id === b.block.domain);
    return li !== 0 ? li : a.depth - b.depth;
  });

  return (
    <div>
      <ViewHead idx="14" question="这套本体的对象类型是怎么分层的？"
        subtitle="等距分层「本体地图」——35 个对象类型按 7 业务域堆成半透明浮板（共享核在顶、协调在底），56 条关系为聚合边。点任意类型：沿关系走廊点亮它的邻域；再点「关联织网」进它的关联卡。视觉锚点＝Palantir Ontology 等距分层图。" />
      <div className="wv-hint mono">自驾驶舱归位的等距素材，适配为对象类型分层（非业务实例）· 结构可视化属透视镜语言</div>

      <div className="om-wrap">
        <div className="om-stage">
          <svg className="om-svg" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet"
            onClick={() => setSelected(null)}>
            {/* 7 层等距浮板（共享核最上→协调最下） */}
            {model.layers.map((layer, i) => {
              const topY = planeTopY(i);
              const c = planeCorners(topY);
              const P = (pt: [number, number]) => `${pt[0]},${pt[1]}`;
              const topFace = `${P(c.top)} ${P(c.right)} ${P(c.bottom)} ${P(c.left)}`;
              const slab = `${P(c.left)} ${P(c.bottom)} ${P(c.right)} ${P([c.right[0], c.right[1] + SLAB])} ${P([c.bottom[0], c.bottom[1] + SLAB])} ${P([c.left[0], c.left[1] + SLAB])}`;
              return (
                <g key={layer.id} className={`om-plane om-plane--${layer.blocks[0]?.tone ?? "cyan"}`}>
                  <polygon className="om-plane__slab" points={slab} />
                  <polygon className="om-plane__top" points={topFace} />
                  <line className="om-plane__edge" x1={c.left[0]} y1={c.left[1]} x2={c.top[0]} y2={c.top[1]} />
                  <line className="om-plane__edge" x1={c.left[0]} y1={c.left[1]} x2={c.bottom[0]} y2={c.bottom[1]} />
                  <text className="om-plane__label" x={c.left[0] + 14} y={c.left[1] - 3}>{layer.name}</text>
                  <text className="om-plane__count" x={c.left[0] + 14} y={c.left[1] + 12}>{layer.blocks.length} 类型</text>
                </g>
              );
            })}

            <g className="om-edges">{baseEdgeEls}</g>
            <g key={selected ?? "none"}>{litEdgeEls}</g>

            {allPlaced.map((p) => {
              const id = p.block.type;
              let state: "focus" | "lit" | "dim" | "normal" = "normal";
              if (litHops) state = id === selected ? "focus" : litHops.has(id) ? "lit" : "dim";
              return (
                <IsoBlock key={id} p={p} state={state} hovered={hovered === id}
                  onHover={setHovered} onClick={() => setSelected(selected === id ? null : id)} />
              );
            })}

            {/* 标签 */}
            {allPlaced.map((p) => {
              const id = p.block.type;
              const dim = litHops && !litHops.has(id) && id !== selected;
              const show = hovered === id || id === selected || (litHops?.has(id) ?? false) || !litHops;
              if (!show) return null;
              const label = p.block.plainName.length > 8 ? p.block.plainName.slice(0, 7) + "…" : p.block.plainName;
              return (
                <text key={`t${id}`} className={`om-blk-label${dim ? " is-dim" : ""}`}
                  x={p.topX} y={p.topY - p.fh - 5} textAnchor="middle" pointerEvents="none">{label}</text>
              );
            })}
          </svg>

        {/* 键盘等价选择列表（L-UX 轮2 P1 正解）：HTML <button> 原生吃 Enter/Space，绕开
            Chromium 的 SVG 焦点键盘事件缺失。视觉隐藏、键盘聚焦进入时整组显形（CSS
            .om-kbd:focus-within），与地图点击共用同一 setSelected——单一事实源。 */}
        <nav className="om-kbd" aria-label="键盘选择对象类型（与地图点击等价）">
          <span className="om-kbd__hint">键盘选择对象类型（Enter 点亮邻域）：</span>
          {[...allPlaced].sort((a, b) => a.block.type.localeCompare(b.block.type)).map(({ block }) => (
            <button key={block.type} className={`om-kbd__btn ${selected === block.type ? "is-on" : ""}`}
              aria-pressed={selected === block.type}
              onClick={() => setSelected(selected === block.type ? null : block.type)}>
              {block.plainName}
            </button>
          ))}
        </nav>
        </div>

        {/* 右侧信息栏 */}
        <aside className="om-side">
          {sel && selWeave ? (
            <>
              <div className="om-side-head">
                <span className={`tag ${sel.tone}`} style={{ fontSize: 10 }}>{sel.domainName}</span>
                <h3 className="om-side-title">{sel.plainName}</h3>
                <span className="om-side-type mono">{sel.type}</span>
              </div>
              <p className="om-side-why">{selWeave.why || "—"}</p>
              <div className="om-side-metrics">
                <div className="om-sm"><span className="om-sm-n num">{sel.fieldCount}</span><span className="om-sm-l">字段</span></div>
                <div className="om-sm"><span className="om-sm-n num">{sel.degree}</span><span className="om-sm-l">关系</span></div>
                <div className="om-sm"><span className="om-sm-n num">{sel.covered ? sel.count.toLocaleString() : "—"}</span><span className="om-sm-l">实例</span></div>
                <div className="om-sm"><span className="om-sm-n num">{litHops ? litHops.size - 1 : 0}</span><span className="om-sm-l">邻域点亮</span></div>
              </div>
              <div className="om-side-btns">
                <button className="wv-goto" onClick={() => navigate("weave", sel.type)}>关联织网 →</button>
                <button className="wv-goto" onClick={() => navigate("impact", sel.type)}>影响分析 →</button>
                {sel.covered && <button className="wv-goto" onClick={() => navigate("entity", sel.type)}>查看实例 →</button>}
              </div>
              <div className="om-side-neighbors">
                <div className="om-side-cap mono">关系走廊点亮的邻域（≤2 跳）</div>
                {[...(litHops?.entries() ?? [])].filter(([t]) => t !== selected)
                  .sort((a, b) => a[1] - b[1]).slice(0, 12).map(([t, hop]) => {
                    const b = model.blockByType.get(t)!;
                    return (
                      <button className="om-nb" key={t} onClick={() => setSelected(t)}>
                        <span className={`om-nb-dot tone-${b.tone}`} />
                        <span className="om-nb-name">{b.plainName}</span>
                        <span className="om-nb-hop mono">{hop} 跳</span>
                      </button>
                    );
                  })}
              </div>
            </>
          ) : (
            <div className="om-side-empty">
              <div className="om-legend">
                {model.layers.map((l) => (
                  <span className="om-leg" key={l.id}>
                    <i className={`om-leg-dot tone-${l.blocks[0]?.tone ?? "cyan"}`} />{l.name}
                  </span>
                ))}
              </div>
              <p className="om-side-hint">
                点任意<b>对象类型组块</b>：沿本体关系走廊点亮它的邻域（≤2 跳），右侧看它的字段/关系/实例数并可穿梭进关联织网。
                浮板越靠上越接近共享核，越靠下越专用。未覆盖类型半透明。
              </p>
              <div className="om-stat mono">
                {model.layers.length} 层 · {model.blockByType.size} 类型 · {model.edges.length} 关系边
              </div>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
