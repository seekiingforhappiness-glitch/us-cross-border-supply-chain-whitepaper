import { formatPct, type Zone, type ZoneId } from "../api";
import Icon from "../components/Icons";
import { headlineOf, summaryLines, ZONE_ICON, ZONE_SHORT, type SummaryLine } from "./zoneModel";

// 七区指挥墙（V10 方案 C 默认首屏中央）——体征带的"放大态"（顶部体征带已移除，避免同信息两处）。
// 每卡：区图标+区名 + headline 大数字 + 趋势（有数据才显示）+ 告警计数徽标 + 该区最要紧 2-3 行摘要。
// 排序：有告警卡按 alert_count 降序在前，无告警卡按固定七区序在后。有告警卡辉光呼吸。
// 无数据/掩码 headline 如实降饱和。履约卡右上角显眼「航线视图」钮 → 切航线走廊图（stopPropagation）。
// 点卡体 → 该区工作队列（下钻第二段）。

const FIXED_ORDER: ZoneId[] = ["money", "fulfillment", "customers", "suppliers", "inventory", "ai", "decisions"];

// 告警卡（alert_count>0）按 alert_count 降序在前，无告警卡按固定七区序在后。
function sortZones(zones: Zone[]): Zone[] {
  const rank = new Map(FIXED_ORDER.map((z, i) => [z, i]));
  return [...zones].sort((a, b) => {
    const aa = a.alert_count > 0;
    const ba = b.alert_count > 0;
    if (aa !== ba) return aa ? -1 : 1;
    if (aa && ba) return b.alert_count - a.alert_count || (rank.get(a.zone) ?? 9) - (rank.get(b.zone) ?? 9);
    return (rank.get(a.zone) ?? 9) - (rank.get(b.zone) ?? 9);
  });
}

// 趋势箭头：仅准交率 up=好（绿）/down=差（红）语义明确；其余方向中性（flat）不评判。
function trendView(z: Zone): { arrow: string; cls: string; text: string } | null {
  const t = z.trend;
  if (!t) return null;
  const goodWhenUp = z.zone === "fulfillment";
  let cls = "cp-trend--flat";
  let arrow = "—";
  if (t.delta > 0) {
    arrow = "▲";
    cls = goodWhenUp ? "cp-trend--up" : "cp-trend--flat";
  } else if (t.delta < 0) {
    arrow = "▼";
    cls = goodWhenUp ? "cp-trend--down" : "cp-trend--flat";
  }
  const mag = Math.abs(t.delta) < 1 ? formatPct(Math.abs(t.delta)) : String(Math.abs(t.delta));
  return { arrow, cls, text: `${mag} · ${t.window_days}d` };
}

function SummaryRow({ line }: { line: SummaryLine }) {
  const cls = line.state === "real" ? (line.tone ?? "") : line.state === "masked" ? "is-masked" : "is-missing";
  return (
    <div className="cp-wall__sumrow">
      <span className="cp-wall__sumk">{line.label}</span>
      <span className={`cp-wall__sumv num ${cls}`}>
        {line.state === "masked" && <Icon name="lock" size={11} />}
        {line.value}
      </span>
    </div>
  );
}

interface Props {
  zones: Zone[];
  onZone: (z: ZoneId) => void;
  onMap: () => void;
}

export default function CommandWall({ zones, onZone, onMap }: Props) {
  const ordered = sortZones(zones);
  return (
    <div className="cp-wall-wrap">
      <div className="cp-panel-head">
        <span className="cp-panel-head__title">七区指挥墙</span>
        <span className="cp-panel-head__meta">告警区自动排前 · 点卡下钻工作队列</span>
      </div>
      <div className="cp-wall" role="list">
        {ordered.map((z) => {
          const hl = headlineOf(z);
          const nodata = hl.state === "missing"; // 只对真·无数据降饱和；掩码是权限态，显锁不降卡
          const trend = trendView(z);
          const alerted = z.alert_count > 0;
          const isFulfillment = z.zone === "fulfillment";
          return (
            <button
              key={z.zone}
              role="listitem"
              className={`cp-wall-card cp-wall-card--${z.zone} ${alerted ? "is-alerted" : ""} ${nodata ? "is-nodata" : ""}`}
              onClick={() => onZone(z.zone)}
              title={z.headline_reason || `${z.headline_label} · 点击下钻`}
            >
              {alerted && <span className={`cp-wall-card__alert ${z.alert_count > 20 ? "" : "is-amber"}`}>{z.alert_count > 99 ? "99+" : z.alert_count}</span>}

              <div className="cp-wall-card__top">
                <span className="cp-wall-card__icon" aria-hidden>
                  <Icon name={ZONE_ICON[z.zone]} size={17} />
                </span>
                <span className="cp-wall-card__name">{ZONE_SHORT[z.zone]}</span>
              </div>

              <div className="cp-wall-card__headline">
                <span className={`cp-wall-card__big num ${hl.state === "real" ? "" : hl.state === "masked" ? "is-masked" : "is-missing"}`}>
                  {hl.state === "masked" && <Icon name="lock" size={14} />}
                  {hl.text}
                </span>
                {trend ? (
                  <span className={`cp-trend ${trend.cls}`}>
                    {trend.arrow} {trend.text}
                  </span>
                ) : (
                  <span className="cp-trend cp-trend--none">静态快照</span>
                )}
              </div>
              <div className="cp-wall-card__hllabel">
                {z.headline_label}
                {/* 回放态诚实标注：headline_as_of 仅回放时下发。current=该指标无时点历史→显当前值（不造假），
                    replayed=真按时点重算。存量类的小灰标是本任务"诚实边界"的画面兑现。 */}
                {z.headline_as_of === "current" && (
                  <span className="cp-asof-tag is-current" title="该指标无时点历史·回放时显示当前值">显示当前值</span>
                )}
                {z.headline_as_of === "replayed" && (
                  <span className="cp-asof-tag is-replayed" title="按所选时点重算">回放</span>
                )}
              </div>

              <div className="cp-wall-card__sum">
                {summaryLines(z).map((line, i) => (
                  <SummaryRow key={i} line={line} />
                ))}
              </div>

              {isFulfillment && (
                <div
                  className="cp-wall-card__switch"
                  role="button"
                  tabIndex={0}
                  aria-label="切换到航线地图"
                  onClick={(e) => {
                    e.stopPropagation();
                    onMap();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      e.stopPropagation();
                      onMap();
                    }
                  }}
                >
                  <Icon name="ship" size={15} />
                  <span>航线视图</span>
                  <Icon name="arrow-right" size={13} />
                </div>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
