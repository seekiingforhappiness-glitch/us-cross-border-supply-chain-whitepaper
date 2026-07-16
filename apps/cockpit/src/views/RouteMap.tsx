import { useEffect, useMemo, useState } from "react";
import { fetchPanorama, type Panorama as PanoData, type Role } from "../api";
import Icon from "../components/Icons";
import StateHint from "../components/StateHint";
import { COUNTRY_CENTROID, LANDMASSES, PORT_COORD, type LonLat } from "./coastline";
import { buildCorridor, laneWidth, portName, type CorridorModel, type Lane } from "./corridorModel";

// 履约夜景真地理地图（V10 补记：走廊图升真地图，Daniel「用地图更合适」）——SVG 手绘零依赖。
// 等距圆柱投影（经纬度线性映射），太平洋居中：以 90°E 为左锚点向东展开，中国左 / 北美中右 /
// 欧洲西岸落右上（跨大西洋方向）。海岸线内嵌简化静态数据（coastline.ts，非依赖）。
//   · 港口 = 真实经纬度定位的发光点 + LOCODE + 中文名 + 在途票数徽标；表外港口按国家前缀兜底近似
//   · 航线弧 = 二次贝塞尔向高纬弯曲（跨太平洋弧向北弯，大圆航线真实观感）；粗细=在途票数、
//     颜色=异常状态（含 critical=红 / 有 alert=琥珀 / 无=蓝）、弧上票数徽标、悬停 tooltip
//   · 点弧线 → 该航线异常队列（LaneQueue，下钻路径不变，复用 B4 资产）
// 数据零硬编码：航线/票数/告警全部 panorama 现取；海岸线与港口坐标是地理常量（已注明来源）。

// ── 投影：等距圆柱 · 太平洋居中（90°E 左锚，向东展开）──────────────────────────
const LON_ANCHOR = 90; // 左参考经线 90°E
const LON_U_MIN = 8; // 视窗左边界（unwrap 后，约 98°E，东亚海岸入画）
const LON_U_MAX = 296; // 视窗右边界（约含欧洲西岸；跨太平洋为主视野，欧洲落右上）
const LAT_MIN = 0; // 北纬 0°（下）
const LAT_MAX = 70; // 北纬 70°（上）
const VB_W = 1240; // viewBox 宽（用户单位）
const VB_H = 300; // viewBox 高（≈4.1:1 宽幅世界带，海天填充上下）

// 经度向东展开：返回相对 90°E 的东向度数 [0,360)——太平洋（中国→美西→美东→欧洲）连续不跳变
const unwrapLon = (lon: number): number => (((lon - LON_ANCHOR) % 360) + 360) % 360;

function projX(lon: number): number {
  return ((unwrapLon(lon) - LON_U_MIN) / (LON_U_MAX - LON_U_MIN)) * VB_W;
}
function projY(lat: number): number {
  return ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN)) * VB_H;
}
const project = ([lon, lat]: LonLat): [number, number] => [projX(lon), projY(lat)];

// 折线环 → SVG points 串
const ringPoints = (ring: LonLat[]): string => ring.map((p) => project(p).join(",")).join(" ");

// 经纬网格（极淡）：经线每 30°（原始经度），纬线每 10°
const GRID_LONS = [90, 120, 150, 180, -150, -120, -90, -60]; // 东经 90 → 西经 60
const GRID_LATS = [10, 20, 30, 40, 50, 60];

// 港口定位：优先真实坐标，缺失则按 LOCODE 前两位国家近似（兜底，配合歧义列示，不冒充精确）
function locatePort(locode: string): { coord: LonLat; approx: boolean } | null {
  const exact = PORT_COORD[locode];
  if (exact) return { coord: exact, approx: false };
  const centroid = COUNTRY_CENTROID[locode.slice(0, 2).toUpperCase()];
  if (centroid) return { coord: centroid, approx: true };
  return null;
}

interface Props {
  role: Role;
  asOf?: string | null; // 世界时钟回放（A-2）：带上则 panorama 异常锚定按时点重建，地图与指挥墙一致
  onLane: (lane: Lane) => void;
  onBack: () => void;
}

interface PortNode {
  code: string;
  coord: LonLat;
  x: number;
  y: number;
  approx: boolean;
  count: number;
  side: "origin" | "dest";
  labelDy: number; // 共位港标签下移错位（洛杉矶/长滩、蛇口/盐田经纬近乎重合，避免叠字）
}

export default function RouteMap({ role, asOf, onLane, onBack }: Props) {
  const [data, setData] = useState<PanoData | null>(null);
  const [err, setErr] = useState(false);
  const [reload, setReload] = useState(0); // 错误态重试计数（StateHint 重试按钮驱动，U4）
  const [hover, setHover] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setErr(false);
    fetchPanorama(role, asOf)
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setErr(true));
    return () => {
      cancelled = true;
    };
  }, [role, asOf, reload]);

  const model: CorridorModel | null = useMemo(() => (data ? buildCorridor(data) : null), [data]);

  // 港口聚合（起运/目的两侧各自吞吐票数）+ 表外港口收集（歧义列示）
  const { ports, offTable } = useMemo(() => {
    const acc = new Map<string, PortNode>();
    const off = new Set<string>();
    if (!model) return { ports: [] as PortNode[], offTable: [] as string[] };
    const bump = (code: string, side: "origin" | "dest", count: number) => {
      const loc = locatePort(code);
      if (!loc) {
        off.add(code);
        return;
      }
      if (loc.approx) off.add(code);
      const [x, y] = project(loc.coord);
      const cur = acc.get(code);
      if (cur) cur.count += count;
      else acc.set(code, { code, coord: loc.coord, x, y, approx: loc.approx, count, side, labelDy: 0 });
    };
    for (const l of model.lanes) {
      bump(l.origin, "origin", l.count);
      bump(l.dest, "dest", l.count);
    }
    // 标签避让：近乎共位的港（同侧、投影 ≤7px 内）标签依次下移一整块，防叠字
    const list = [...acc.values()];
    const anchored: PortNode[] = [];
    for (const p of list.sort((a, b) => a.x - b.x || a.y - b.y)) {
      const clash = anchored.filter((q) => q.side === p.side && Math.abs(q.x - p.x) <= 7 && Math.abs(q.y - p.y + q.labelDy) <= 22).length;
      p.labelDy = clash * 22;
      anchored.push(p);
    }
    return { ports: list, offTable: [...off] };
  }, [model]);

  const head = (
    <div className="cp-panel-head">
      <button className="cp-return" onClick={onBack}>
        <Icon name="arrow-right" size={14} className="cp-return__ic" /> 返回指挥墙
      </button>
      <span className="cp-panel-head__title">履约航线地图</span>
      {model && (
        <span className="cp-panel-head__meta">
          {model.lanes.length} 航线 · {model.totalShipments} 票在途 · {model.totalAlerts} 告警
        </span>
      )}
    </div>
  );

  // U4 三态：加载/错误/空统一走 StateHint（对齐全舱四态；替代原 cp-fill-msg 行内文案）。
  if (err)
    return (
      <div className="cp-map-wrap">
        {head}
        <StateHint
          kind="error"
          title="航线地图不可用"
          message="没能取到履约航线数据。确认 apps/api 服务已在 8100 端口启动，再重试。"
          onRetry={() => setReload((n) => n + 1)}
        />
      </div>
    );
  if (!data || !model)
    return (
      <div className="cp-map-wrap">
        {head}
        <StateHint kind="loading" title="履约地图加载中…" skeletonRows={4} />
      </div>
    );
  if (model.lanes.length === 0)
    return (
      <div className="cp-map-wrap">
        {head}
        <StateHint
          kind="empty"
          title="当前无在途航线"
          reason="所有货件均已妥投（未 delivered 票为 0），没有可绘制的航线弧。"
          suggestion="切到模拟世界看连续在途的履约航线，或用顶栏时间轴回放到有在途货件的时点。"
        />
      </div>
    );

  const hoveredLane = hover ? model.lanes.find((l) => l.key === hover) ?? null : null;

  return (
    <div className="cp-map-wrap">
      {head}
      <div className="cp-map">
        <svg className="cp-map__svg" viewBox={`0 0 ${VB_W} ${VB_H}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="履约航线夜景地图">
          {/* 经纬网格（极淡） */}
          <g className="cp-map__grid">
            {GRID_LONS.map((lon) => {
              const x = projX(lon);
              if (x < 0 || x > VB_W) return null;
              return <line key={`gx${lon}`} x1={x} y1={0} x2={x} y2={VB_H} />;
            })}
            {GRID_LATS.map((lat) => {
              const y = projY(lat);
              return <line key={`gy${lat}`} x1={0} y1={y} x2={VB_W} y2={y} />;
            })}
          </g>

          {/* 陆地：极淡填充 + 海岸线微光描边 */}
          <g className="cp-map__land">
            {LANDMASSES.map((lm) => (
              <polygon key={lm.name} points={ringPoints(lm.ring)} />
            ))}
          </g>

          {/* 航线弧束（buildCorridor 已排序：告警航线后画=在上层） */}
          <g className="cp-map__arcs">
            {model.lanes.map((l) => {
              const o = locatePort(l.origin);
              const d = locatePort(l.dest);
              if (!o || !d) return null; // 无法定位的航线不画弧（端口已进歧义列示）
              const [x1, y1] = project(o.coord);
              const [x2, y2] = project(d.coord);
              // 二次贝塞尔向高纬（上/北）弯曲：控制点抬到两端更高纬之上，幅度∝水平跨度（大圆观感）
              const span = Math.abs(x2 - x1);
              const cx = (x1 + x2) / 2;
              const cy = Math.max(6, Math.min(y1, y2) - span * 0.16);
              const path = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;
              const w = laneWidth(l.count, model.maxCount);
              const isHover = hover === l.key;
              // 弧顶（t=0.5）放票数徽标
              const mx = 0.25 * x1 + 0.5 * cx + 0.25 * x2;
              const my = 0.25 * y1 + 0.5 * cy + 0.25 * y2;
              return (
                <g key={l.key} className={`cp-lane cp-lane--${l.tone} ${isHover ? "is-hover" : ""}`}>
                  <path
                    d={path}
                    className="cp-lane__hit"
                    strokeWidth={Math.max(w + 14, 18)}
                    onMouseEnter={() => setHover(l.key)}
                    onMouseLeave={() => setHover(null)}
                    onClick={() => onLane(l)}
                  >
                    <title>{`${l.origin}→${l.dest} · ${l.count} 票 · ${l.containers} 柜${l.delayed ? ` · ${l.delayed} 延误` : ""}${l.alertCount ? ` · ${l.alertCount} 告警` : ""}\n点击看该航线异常队列`}</title>
                  </path>
                  <path d={path} className="cp-lane__line" strokeWidth={w} />
                  <g transform={`translate(${mx},${my})`} pointerEvents="none">
                    <circle className="cp-lane__dot" r={9} />
                    <text className="cp-lane__num" textAnchor="middle" dy={3}>
                      {l.count}
                    </text>
                  </g>
                </g>
              );
            })}
          </g>

          {/* 港口发光点 + 标签 */}
          <g className="cp-map__ports">
            {ports.map((p) => {
              const cn = portName(p.code);
              const anchorRight = p.side === "origin"; // 中国港标签朝左，美/欧港朝右，减少压弧
              const tx = anchorRight ? p.x - 11 : p.x + 11;
              return (
                <g key={p.code} className={`cp-portnode cp-portnode--${p.side}`}>
                  <circle className="cp-portnode__halo" cx={p.x} cy={p.y} r={9} />
                  <circle className="cp-portnode__dot" cx={p.x} cy={p.y} r={3.4} />
                  {p.labelDy > 0 && <line className="cp-portnode__leader" x1={p.x} y1={p.y} x2={tx} y2={p.y - 1 + p.labelDy} />}
                  <text className="cp-portnode__code num" x={tx} y={p.y - 1 + p.labelDy} textAnchor={anchorRight ? "end" : "start"}>
                    {p.code}
                    {p.approx ? "＊" : ""}
                  </text>
                  <text className="cp-portnode__name" x={tx} y={p.y + 10 + p.labelDy} textAnchor={anchorRight ? "end" : "start"}>
                    {cn || "—"} · {p.count}票
                  </text>
                </g>
              );
            })}
          </g>
        </svg>

        {/* 悬停明细 tooltip（右上角固定） */}
        {hoveredLane && (
          <div className="cp-map__tip">
            <div className="cp-map__tip-t num">
              {hoveredLane.origin}
              <Icon name="arrow-right" size={12} />
              {hoveredLane.dest}
            </div>
            <div className="cp-map__tip-sub">
              {portName(hoveredLane.origin) || hoveredLane.origin} → {portName(hoveredLane.dest) || hoveredLane.dest}
            </div>
            <div className="cp-map__tip-row">
              <span>{hoveredLane.count} 票</span>
              <span>{hoveredLane.containers} 柜</span>
              {hoveredLane.delayed > 0 && <span className="neg">{hoveredLane.delayed} 延误</span>}
              {hoveredLane.alertCount > 0 && <span className="neg">{hoveredLane.alertCount} 告警</span>}
            </div>
            {Object.keys(hoveredLane.statuses).length > 0 && (
              <div className="cp-map__tip-stat">
                {Object.entries(hoveredLane.statuses).map(([s, n]) => (
                  <span key={s} className="cp-chip">
                    {s} {n}
                  </span>
                ))}
              </div>
            )}
            <div className="cp-map__tip-hint">点击弧线看该航线异常队列 →</div>
          </div>
        )}

        <div className="cp-map__legend">
          <span><i className="cp-lane-lg cp-lane-lg--blue" />正常</span>
          <span><i className="cp-lane-lg cp-lane-lg--amber" />有异常</span>
          <span><i className="cp-lane-lg cp-lane-lg--red" />含紧急</span>
          <span className="cp-map__legend-sep">粗细 = 在途票数 · 弧向高纬弯（大圆航线）</span>
        </div>

        {offTable.length > 0 && (
          <div className="cp-map__ambig" title="坐标表外港口：按国家前缀近似定位，标＊，不冒充精确">
            ＊近似定位：{offTable.join(" / ")}
          </div>
        )}
      </div>
    </div>
  );
}
