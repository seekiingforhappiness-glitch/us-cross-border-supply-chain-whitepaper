import { useEffect, useMemo, useState } from "react";
import { fetchPanorama, type Panorama as PanoData, type Role } from "../api";
import Icon from "../components/Icons";
import { buildCorridor, laneWidth, portName, type CorridorModel, type Lane } from "./corridorModel";

// 航线走廊图（V10 方案 C · 履约卡「航线视图」切入，SAP IBP tile 内切换范式）——SVG 手绘零依赖。
// 左列=起运港（一般中国 CNNGB/CNYTN/CNSHK…），右列=目的港/仓（USLAX/USLGB/USNYC/…，数据里
// 还有 DEHAM/NLRTM 等就如实一并画，不丢真数据）。中间弧线束（二次贝塞尔）每弧=一条航线：
//   粗细=在途票数 count、颜色=异常状态（含 critical=红/有 alert=琥珀/无=蓝），弧上标在途票数。
// 悬停显示 lane 明细；点弧线 → 该航线异常队列（下钻路径与区队列一致）。「返回指挥墙」显眼。
// 只画港口列+弧线束（不画世界地图轮廓）——避免大片空白海洋与地图质感风险。

const W = 1000;
const PAD_TOP = 78;
const PAD_BOTTOM = 52;
const ROW_STEP = 74;
const LEFT_X = 174;
const RIGHT_X = 826;
const PORT_W = 108;
const PORT_H = 34;
const CENTER_X = W / 2;

function evenY(n: number, i: number, height: number): number {
  if (n <= 1) return height / 2;
  const span = height - PAD_TOP - PAD_BOTTOM;
  return PAD_TOP + (span * i) / (n - 1);
}

interface Props {
  role: Role;
  onLane: (lane: Lane) => void;
  onBack: () => void;
}

export default function RouteCorridor({ role, onLane, onBack }: Props) {
  const [data, setData] = useState<PanoData | null>(null);
  const [err, setErr] = useState(false);
  const [hover, setHover] = useState<string | null>(null);

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

  const model: CorridorModel | null = useMemo(() => (data ? buildCorridor(data) : null), [data]);

  const head = (
    <div className="cp-panel-head">
      <button className="cp-return" onClick={onBack}>
        <Icon name="arrow-right" size={14} className="cp-return__ic" /> 返回指挥墙
      </button>
      <span className="cp-panel-head__title">航线走廊图 · 履约</span>
      {model && <span className="cp-panel-head__meta">{model.lanes.length} 航线 · {model.totalShipments} 票在途 · {model.totalAlerts} 告警</span>}
    </div>
  );

  if (err) return <div className="cp-corridor-wrap">{head}<div className="cp-fill-msg">航线数据加载失败——确认 API 已启动</div></div>;
  if (!data || !model) return <div className="cp-corridor-wrap">{head}<div className="cp-fill-msg">航线走廊加载中…</div></div>;
  if (model.lanes.length === 0) return <div className="cp-corridor-wrap">{head}<div className="cp-fill-msg">当前无在途航线（未 delivered 票为 0）</div></div>;

  const rows = Math.max(model.origins.length, model.dests.length);
  const H = Math.max(PAD_TOP + PAD_BOTTOM + (rows - 1) * ROW_STEP, 420);
  const originY = new Map(model.origins.map((o, i) => [o, evenY(model.origins.length, i, H)]));
  const destY = new Map(model.dests.map((d, i) => [d, evenY(model.dests.length, i, H)]));

  // 港口吞吐（票数）用于港节点副标注
  const originTput = new Map<string, number>();
  const destTput = new Map<string, number>();
  for (const l of model.lanes) {
    originTput.set(l.origin, (originTput.get(l.origin) ?? 0) + l.count);
    destTput.set(l.dest, (destTput.get(l.dest) ?? 0) + l.count);
  }

  const hoveredLane = hover ? model.lanes.find((l) => l.key === hover) ?? null : null;

  return (
    <div className="cp-corridor-wrap">
      {head}
      <div className="cp-corridor">
        <svg className="cp-corridor__svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
          {/* 列标题 */}
          <text className="cp-corridor__col" x={LEFT_X} y={40} textAnchor="middle">起运港 · 中国</text>
          <text className="cp-corridor__col" x={RIGHT_X} y={40} textAnchor="middle">目的港 / 仓</text>

          {/* 弧线束（已按 告警后画=在上层 排序） */}
          {model.lanes.map((l) => {
            const y1 = originY.get(l.origin) ?? H / 2;
            const y2 = destY.get(l.dest) ?? H / 2;
            const x1 = LEFT_X + PORT_W / 2;
            const x2 = RIGHT_X - PORT_W / 2;
            const cx = CENTER_X;
            const cy = (y1 + y2) / 2;
            const path = `M ${x1} ${y1} Q ${cx} ${cy} ${x2} ${y2}`;
            const w = laneWidth(l.count, model.maxCount);
            const isHover = hover === l.key;
            const mx = 0.25 * x1 + 0.5 * cx + 0.25 * x2; // 贝塞尔 t=0.5 点
            const my = 0.25 * y1 + 0.5 * cy + 0.25 * y2;
            return (
              <g key={l.key} className={`cp-arc cp-arc--${l.tone} ${isHover ? "is-hover" : ""}`}>
                {/* 命中区（透明粗线，好点选/悬停） */}
                <path d={path} className="cp-arc__hit" strokeWidth={Math.max(w + 12, 16)} onMouseEnter={() => setHover(l.key)} onMouseLeave={() => setHover(null)} onClick={() => onLane(l)}>
                  <title>{`${l.origin}→${l.dest} · ${l.count} 票 · ${l.containers} 柜${l.delayed ? ` · ${l.delayed} 延误` : ""}${l.alertCount ? ` · ${l.alertCount} 告警` : ""}\n点击看该航线异常队列`}</title>
                </path>
                <path d={path} className="cp-arc__line" strokeWidth={w} />
                {/* 票数标注 */}
                <g transform={`translate(${mx},${my})`} pointerEvents="none">
                  <circle className="cp-arc__dot" r={9} />
                  <text className="cp-arc__num" textAnchor="middle" dy={3}>
                    {l.count}
                  </text>
                </g>
              </g>
            );
          })}

          {/* 起运港节点（左列） */}
          {model.origins.map((o) => {
            const y = originY.get(o) ?? H / 2;
            const cn = portName(o);
            return (
              <g key={`o:${o}`} className="cp-port cp-port--origin">
                <rect x={LEFT_X - PORT_W / 2} y={y - PORT_H / 2} width={PORT_W} height={PORT_H} rx={7} />
                <text className="cp-port__code num" x={LEFT_X - PORT_W / 2 + 12} y={y - 1}>{o}</text>
                <text className="cp-port__name" x={LEFT_X - PORT_W / 2 + 12} y={y + 11}>{cn || "—"}</text>
                <text className="cp-port__tput num" x={LEFT_X + PORT_W / 2 - 10} y={y + 4} textAnchor="end">{originTput.get(o) ?? 0}票</text>
              </g>
            );
          })}

          {/* 目的港/仓节点（右列） */}
          {model.dests.map((d) => {
            const y = destY.get(d) ?? H / 2;
            const cn = portName(d);
            return (
              <g key={`d:${d}`} className="cp-port cp-port--dest">
                <rect x={RIGHT_X - PORT_W / 2} y={y - PORT_H / 2} width={PORT_W} height={PORT_H} rx={7} />
                <text className="cp-port__code num" x={RIGHT_X - PORT_W / 2 + 12} y={y - 1}>{d}</text>
                <text className="cp-port__name" x={RIGHT_X - PORT_W / 2 + 12} y={y + 11}>{cn || "—"}</text>
                <text className="cp-port__tput num" x={RIGHT_X + PORT_W / 2 - 10} y={y + 4} textAnchor="end">{destTput.get(d) ?? 0}票</text>
              </g>
            );
          })}
        </svg>

        {/* 悬停明细 tooltip（右上角固定，避免遮弧线） */}
        {hoveredLane && (
          <div className="cp-corridor__tip">
            <div className="cp-corridor__tip-t num">
              {hoveredLane.origin}
              <Icon name="arrow-right" size={12} />
              {hoveredLane.dest}
            </div>
            <div className="cp-corridor__tip-row">
              <span>{hoveredLane.count} 票</span>
              <span>{hoveredLane.containers} 柜</span>
              {hoveredLane.delayed > 0 && <span className="neg">{hoveredLane.delayed} 延误</span>}
              {hoveredLane.alertCount > 0 && <span className="neg">{hoveredLane.alertCount} 告警</span>}
            </div>
            {Object.keys(hoveredLane.statuses).length > 0 && (
              <div className="cp-corridor__tip-stat">
                {Object.entries(hoveredLane.statuses).map(([s, n]) => (
                  <span key={s} className="cp-chip">
                    {s} {n}
                  </span>
                ))}
              </div>
            )}
            <div className="cp-corridor__tip-hint">点击弧线看该航线异常队列 →</div>
          </div>
        )}

        <div className="cp-corridor__legend">
          <span><i className="cp-arc-lg cp-arc-lg--blue" />正常</span>
          <span><i className="cp-arc-lg cp-arc-lg--amber" />有异常</span>
          <span><i className="cp-arc-lg cp-arc-lg--red" />含紧急</span>
          <span className="cp-corridor__legend-sep">粗细 = 在途票数</span>
        </div>
      </div>
    </div>
  );
}
