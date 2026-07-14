import type { Zone, ZoneId } from "../api";
import { formatInt, formatPct, formatUsd, isMasked, MASK_TEXT } from "../api";
import Icon, { type IconName } from "./Icons";

// 公司体征带：七掌控区各一枚体征块横排。点任一枚 → 中央区切换为该区展开视图。
// V9-D：每枚块三层信息层次（图标+标签 / 大数字 / 徽标+趋势）、hover 微抬升、选中态左缘光条、
// 无数据格降饱和（依然如实显示 reason tooltip）。全 emoji 清零，图标改 Icons.tsx 几何线条。
interface Props {
  zones: Zone[];
  activeZone: ZoneId | null;
  onSelect: (z: ZoneId) => void;
}

const ICON: Record<ZoneId, IconName> = {
  money: "money",
  fulfillment: "box",
  customers: "person",
  suppliers: "factory",
  inventory: "rack",
  ai: "chip",
  decisions: "stamp",
};

// 短标签（体征带空间紧，用短名；详情视图里用 API 的完整 headline_label）
const SHORT: Record<ZoneId, string> = {
  money: "费用敞口",
  fulfillment: "准交率",
  customers: "客户敞口",
  suppliers: "供应商",
  inventory: "库存击穿",
  ai: "AI 今日",
  decisions: "待拍板",
};

// headline 是比率的区（0-1 → 百分比）；其余按计数/金额/字符串各自处理
const RATE_ZONES: ReadonlySet<ZoneId> = new Set<ZoneId>(["fulfillment", "suppliers"]);

function renderValue(z: Zone): { text: string; state: "real" | "missing" | "masked" } {
  const v = z.headline_value;
  if (isMasked(v)) return { text: MASK_TEXT, state: "masked" };
  if (v === null || v === undefined) return { text: "无数据", state: "missing" };
  if (z.headline_unit === "usd") return { text: formatUsd(v), state: "real" };
  if (typeof v === "string") return { text: v, state: "real" }; // AI 区"N 检 / M 提案"
  if (RATE_ZONES.has(z.zone)) return { text: formatPct(v), state: "real" };
  return { text: formatInt(v), state: "real" };
}

export default function VitalsBand({ zones, activeZone, onSelect }: Props) {
  return (
    <nav className="cp-vitals" aria-label="公司体征带">
      {zones.map((z) => {
        const val = renderValue(z);
        const t = z.trend;
        // 趋势箭头：仅准交率 up=好（绿）/down=差（红）语义明确；其余（如 AI 检测数）方向中性不评判
        const goodWhenUp = z.zone === "fulfillment";
        let trendCls = "cp-trend--none";
        let arrow = "—";
        if (t) {
          if (t.delta > 0) {
            arrow = "▲";
            trendCls = goodWhenUp ? "cp-trend--up" : "cp-trend--flat";
          } else if (t.delta < 0) {
            arrow = "▼";
            trendCls = goodWhenUp ? "cp-trend--down" : "cp-trend--flat";
          } else {
            arrow = "—";
            trendCls = "cp-trend--flat";
          }
        }
        const nodata = val.state !== "real";
        return (
          <button
            key={z.zone}
            className={`cp-vital cp-vital--${z.zone} ${activeZone === z.zone ? "is-active" : ""} ${nodata ? "is-nodata" : ""}`}
            onClick={() => onSelect(z.zone)}
            aria-pressed={activeZone === z.zone}
            title={z.headline_reason || z.headline_label}
          >
            {z.alert_count > 0 && (
              <span className={`cp-vital__alert ${z.alert_count > 20 ? "" : "is-amber"}`}>
                {z.alert_count > 99 ? "99+" : z.alert_count}
              </span>
            )}
            <span className="cp-vital__top">
              <span className="cp-vital__icon" aria-hidden>
                <Icon name={ICON[z.zone]} size={15} />
              </span>
              <span className="cp-vital__label">{SHORT[z.zone]}</span>
            </span>
            <div className={`cp-vital__value num ${val.state === "real" ? "" : val.state === "masked" ? "is-masked" : "is-missing"}`}>
              {val.state === "masked" && <Icon name="lock" size={12} />}
              {val.text}
            </div>
            <div className="cp-vital__sub">
              {t ? (
                <span className={`cp-trend ${trendCls}`}>
                  {arrow} {Math.abs(t.delta) < 1 ? formatPct(Math.abs(t.delta)) : Math.abs(t.delta)}
                  <span className="cp-vital__win">· {t.window_days}d</span>
                </span>
              ) : (
                <span className="cp-trend cp-trend--none">静态快照</span>
              )}
            </div>
          </button>
        );
      })}
    </nav>
  );
}
